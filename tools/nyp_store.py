"""
The ONLY module that opens the database (hard rule 7).

Two interchangeable backends with the same functions:
- Supabase (Postgres), when the nyp_db connection holds a url and service_key (or the env vars
  SUPABASE_URL / SUPABASE_SERVICE_KEY are set). Spoken to over HTTPS with Supabase's REST API,
  standard library only. Tables come from supabase/schema.sql.
- SQLite at /tmp/nyp_inbox.db otherwise (NYP_DB_PATH overrides it and always wins, for tests).

Other tool files import the plain helper functions below; the @tool functions at the bottom are
the ones the agent can call. No email subject, body or sender is ever written here (hard rule 3)
-- draft content is the one exception, and it is deleted after the decision, leaving only its
hash. The same goes for the text of a web edit queued in web_decisions.
"""
import hashlib
import http.client
import json
import os
import sqlite3
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission
from ibm_watsonx_orchestrate.run import connections

DB_APP_ID = "nyp_db"
# Every tool that touches the store lists this, so Orchestrate hands it the Supabase credentials.
DB_CREDENTIALS = [ExpectedCredentials(app_id=DB_APP_ID, type=ConnectionType.KEY_VALUE)]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (id INTEGER PRIMARY KEY CHECK (id=1),
  history_id TEXT, last_run TEXT);
CREATE TABLE IF NOT EXISTS analyses (
  message_id TEXT PRIMARY KEY, thread_id TEXT, category TEXT CHECK (category IN ('routine','important','suspicious')),
  risk TEXT CHECK (risk IN ('low','medium','high')), confidence INTEGER, band TEXT,
  evidence TEXT,
  quarantined INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  task_id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, source_message_ids TEXT,
  title TEXT, deadline TEXT, status TEXT CHECK (status IN ('open','pending_approval','on_hold','done','cancelled')),
  confidence INTEGER, reason TEXT, delta TEXT, calendar_event_id TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS drafts (draft_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER,
  action TEXT, content TEXT, content_hash TEXT, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY, task_id INTEGER, action TEXT CHECK (action IN ('send_reply','calendar_create','calendar_move')),
  content_hash TEXT, decision TEXT CHECK (decision IN ('pending','approved','rejected','cancelled','expired')),
  state TEXT CHECK (state IN ('prepared','approved','attempted','confirmed','failed')),
  expires_at TEXT, used INTEGER DEFAULT 0, decided_at TEXT, result_id TEXT, telegram_message_id TEXT);
CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, actor TEXT, event TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS web_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT,
  decision TEXT CHECK (decision IN ('approve','reject','edit')), body TEXT,
  status TEXT DEFAULT 'queued' CHECK (status IN ('queued','applied','failed')),
  result TEXT, created_at TEXT, applied_at TEXT);
"""

TABLES = ("sync_state", "analyses", "tasks", "drafts", "approvals", "activity_log", "web_decisions")
PRIMARY_KEY = {"sync_state": "id", "analyses": "message_id", "tasks": "task_id", "drafts": "draft_id",
               "approvals": "approval_id", "activity_log": "id", "web_decisions": "id"}
TASK_FIELDS = ("thread_id", "source_message_ids", "title", "deadline", "status", "confidence",
               "reason", "delta", "calendar_event_id")
TASK_STATUSES = ("open", "pending_approval", "on_hold", "done", "cancelled")
APPROVAL_FIELDS = ("decision", "state", "used", "decided_at", "result_id", "expires_at", "telegram_message_id")
WEB_DECISIONS = ("approve", "reject", "edit")


def db_path() -> str:
    return os.environ.get("NYP_DB_PATH") or "/tmp/nyp_inbox.db"


# ---- SQLite backend -----------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migration for databases created before the column existed (keeps their data).
    if "telegram_message_id" not in {r[1] for r in conn.execute("PRAGMA table_info(approvals)")}:
        conn.execute("ALTER TABLE approvals ADD COLUMN telegram_message_id TEXT")
        conn.commit()
    return conn


def _query(sql: str, params: tuple = ()) -> list:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _execute(sql: str, params: tuple = ()) -> int:
    """Run one write statement, commit, and return lastrowid."""
    conn = _connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _check_names(table: str, cols) -> None:
    """Table and column names are always written by our code; refuse anything else."""
    if table not in TABLES or not all(str(c).isidentifier() for c in cols):
        raise ValueError(f"bad table or column {table}.{list(cols)}")


class _SQLite:
    name = "sqlite"

    def select(self, table, where=None, order=None, desc=False, limit=None):
        where = where or {}
        _check_names(table, list(where) + ([order] if order else []))
        sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(f"{k}=?" for k in where) if where else "")
        if order:
            sql += f" ORDER BY {order}{' DESC' if desc else ''}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return _query(sql, tuple(where.values()))

    def insert(self, table, row):
        _check_names(table, row)
        rid = _execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       tuple(row.values()))
        pk = PRIMARY_KEY[table]
        return self.select(table, {pk: row.get(pk, rid)})[0]

    def update(self, table, values, where):
        """Update matching rows; returns the number changed (one statement, so it is atomic)."""
        _check_names(table, list(values) + list(where))
        conn = _connect()
        try:
            cur = conn.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in values)} WHERE "
                               + " AND ".join(f"{k}=?" for k in where), tuple(values.values()) + tuple(where.values()))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def upsert(self, table, row):
        _check_names(table, row)
        pk = PRIMARY_KEY[table]
        sets = ", ".join(f"{k}=excluded.{k}" for k in row if k != pk)
        _execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))}) "
                 f"ON CONFLICT({pk}) DO UPDATE SET {sets}", tuple(row.values()))

    def count(self, table):
        _check_names(table, [])
        return _query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]

    def delete_all(self):
        conn = _connect()
        try:
            for table in TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence")
            conn.commit()
        finally:
            conn.close()


# ---- Supabase backend (PostgREST over HTTPS) ------------------------------------------

class DatabaseError(RuntimeError):
    pass


_connections = {}   # host -> open HTTPSConnection, reused: a new TLS handshake per call costs ~0.4 s
# The server closed an idle kept-alive connection before our request reached it: safe to resend.
_STALE_CONNECTION = (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


def _rest_call(method: str, url: str, headers: dict, body=None):
    """One HTTPS request to Supabase. Returns (status, parsed JSON or None, response headers).
    Tests replace this function with a fake."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    for attempt in range(2):
        conn = _connections.get(parts.netloc)
        if conn is None:
            conn = _connections[parts.netloc] = http.client.HTTPSConnection(parts.netloc, timeout=20)
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
        except _STALE_CONNECTION as e:
            conn.close()
            _connections.pop(parts.netloc, None)
            if attempt == 0:
                continue
            raise DatabaseError(f"Cannot reach Supabase: {e}") from None
        except (OSError, http.client.HTTPException) as e:   # timeouts etc.: never resend (could double-write)
            conn.close()
            _connections.pop(parts.netloc, None)
            raise DatabaseError(f"Cannot reach Supabase: {e}") from None
        if resp.status >= 400:
            raise DatabaseError(f"Supabase answered {resp.status}: {raw[:300]}")
        return resp.status, (json.loads(raw) if raw else None), dict(resp.getheaders())


class _Supabase:
    name = "supabase"

    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.key = key

    def _call(self, method, table, query=None, body=None, prefer=None):
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if self.key.startswith("eyJ"):          # legacy service_role JWT; new sb_secret_ keys go in apikey only
            headers["Authorization"] = f"Bearer {self.key}"
        if prefer:
            headers["Prefer"] = prefer
        url = self.base + table + ("?" + urllib.parse.urlencode(query) if query else "")
        return _rest_call(method, url, headers, body)

    @staticmethod
    def _filters(where):
        return [(k, f"eq.{v}") for k, v in (where or {}).items()]

    def select(self, table, where=None, order=None, desc=False, limit=None):
        _check_names(table, list(where or {}) + ([order] if order else []))
        query = [("select", "*")] + self._filters(where)
        if order:
            query.append(("order", f"{order}.{'desc' if desc else 'asc'}"))
        if limit:
            query.append(("limit", str(int(limit))))
        return self._call("GET", table, query)[1] or []

    def insert(self, table, row):
        _check_names(table, row)
        return self._call("POST", table, None, row, "return=representation")[1][0]

    def update(self, table, values, where):
        _check_names(table, list(values) + list(where))
        # One PATCH with all conditions is a single UPDATE in Postgres, so a claim is atomic.
        return len(self._call("PATCH", table, self._filters(where), values, "return=representation")[1] or [])

    def upsert(self, table, row):
        _check_names(table, row)
        self._call("POST", table, [("on_conflict", PRIMARY_KEY[table])], row,
                   "resolution=merge-duplicates,return=minimal")

    def count(self, table):
        _check_names(table, [])
        _, _, headers = self._call("GET", table, [("select", PRIMARY_KEY[table]), ("limit", "1")],
                                   prefer="count=exact")
        content_range = {k.lower(): v for k, v in headers.items()}.get("content-range", "*/0")
        return int(content_range.split("/")[-1])

    def delete_all(self):
        for table in TABLES:
            self._call("DELETE", table, [(PRIMARY_KEY[table], "not.is.null")])


_backend_override = None     # tests set this to a _Supabase wired to a fake
_supabase_config = None      # cached per tool process: {"url", "service_key"} or {} when not set up


def _load_supabase_config() -> dict:
    global _supabase_config
    if _supabase_config is None:
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not (url and key):
            try:
                kv = connections.key_value(DB_APP_ID) or {}
                url, key = str(kv.get("url", "")).strip(), str(kv.get("service_key", "")).strip()
            except (Exception, SystemExit):     # the ADK calls sys.exit() when a connection is missing
                url = key = ""
        filled = url and key and not url.startswith("<") and not key.startswith("<")
        _supabase_config = {"url": url, "service_key": key} if filled else {}
    return _supabase_config


def _db():
    if _backend_override is not None:
        return _backend_override
    if os.environ.get("NYP_DB_PATH"):
        return _SQLite()
    cfg = _load_supabase_config()
    return _Supabase(cfg["url"], cfg["service_key"]) if cfg else _SQLite()


def backend_name() -> str:
    return _db().name


# ---- common helpers -------------------------------------------------------------------

# The user's time zone. A time without an offset ("2026-10-25T09:00") means 09:00 here,
# not 09:00 UTC. Singapore has no daylight saving, so a fixed offset is exact.
LOCAL_TZ = timezone(timedelta(hours=8), "SGT")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: dict) -> str:
    """SHA-256 of the canonical JSON (sorted keys) of an action payload."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _first(rows: list) -> Optional[dict]:
    return rows[0] if rows else None


# ---- sync state -------------------------------------------------------------------

def get_sync_state() -> dict:
    row = _first(_db().select("sync_state", {"id": 1}))
    return {"history_id": row["history_id"], "last_run": row["last_run"]} if row else {"history_id": None, "last_run": None}


def set_sync_state(history_id: Optional[str]) -> dict:
    _db().upsert("sync_state", {"id": 1, "history_id": history_id, "last_run": now_iso()})
    return get_sync_state()


# ---- analyses ---------------------------------------------------------------------

def _analysis_row(row: Optional[dict]) -> Optional[dict]:
    if row is not None:
        row["evidence"] = json.loads(row["evidence"] or "[]")
    return row


def save_analysis(message_id: str, thread_id: str, category: str, risk: str, confidence: int,
                  band: str, evidence: list, quarantined: bool) -> dict:
    _db().upsert("analyses", {"message_id": message_id, "thread_id": thread_id, "category": category, "risk": risk,
                              "confidence": int(confidence), "band": band, "evidence": json.dumps(list(evidence)),
                              "quarantined": 1 if quarantined else 0, "created_at": now_iso()})
    return get_analysis(message_id)


def record_safety(message_id: str, risk: str, evidence: list, quarantined: bool) -> dict:
    """Save safety-check evidence before the planner runs. Keeps CALENDAR_CLASH if already recorded."""
    existing = get_analysis(message_id)
    codes = list(evidence)
    if existing and "CALENDAR_CLASH" in existing["evidence"] and "CALENDAR_CLASH" not in codes:
        codes.append("CALENDAR_CLASH")
    values = {"risk": risk, "evidence": json.dumps(codes), "quarantined": 1 if quarantined else 0}
    if existing:
        _db().update("analyses", values, {"message_id": message_id})
    else:
        _db().insert("analyses", {"message_id": message_id, **values, "created_at": now_iso()})
    return get_analysis(message_id)


def add_evidence(message_id: str, code: str) -> None:
    """Append one evidence code (e.g. CALENDAR_CLASH) to a message's analysis row."""
    existing = get_analysis(message_id)
    if existing is None:
        _db().insert("analyses", {"message_id": message_id, "risk": "low", "evidence": json.dumps([code]),
                                  "quarantined": 0, "created_at": now_iso()})
    elif code not in existing["evidence"]:
        _db().update("analyses", {"evidence": json.dumps(existing["evidence"] + [code])}, {"message_id": message_id})


def get_analysis(message_id: str) -> Optional[dict]:
    return _analysis_row(_first(_db().select("analyses", {"message_id": message_id})))


def list_analyses(category: Optional[str] = None, quarantined: Optional[bool] = None) -> list:
    where = {}
    if category:
        where["category"] = category
    if quarantined is not None:
        where["quarantined"] = 1 if quarantined else 0
    return [_analysis_row(r) for r in _db().select("analyses", where, order="created_at")]


# ---- tasks ------------------------------------------------------------------------

def get_task(task_id: int) -> Optional[dict]:
    return _first(_db().select("tasks", {"task_id": int(task_id)}))


def get_task_by_thread(thread_id: str) -> Optional[dict]:
    return _first(_db().select("tasks", {"thread_id": thread_id}, order="task_id", desc=True, limit=1))


def list_tasks(status: Optional[str] = None) -> list:
    return _db().select("tasks", {"status": status} if status else None, order="task_id")


def upsert_task(task_id: Optional[int] = None, **fields) -> dict:
    """Insert a task, or update the given fields of an existing one."""
    unknown = set(fields) - set(TASK_FIELDS)
    if unknown:
        raise ValueError(f"unknown task fields: {sorted(unknown)}")
    if "status" in fields and fields["status"] not in TASK_STATUSES:
        raise ValueError(f"status must be one of {list(TASK_STATUSES)}")
    ts = now_iso()
    if task_id is None:
        fields.setdefault("status", "open")
        task_id = _db().insert("tasks", {**fields, "created_at": ts, "updated_at": ts})["task_id"]
    else:
        if get_task(task_id) is None:
            raise ValueError(f"task {task_id} not found")
        if fields:
            _db().update("tasks", {**fields, "updated_at": ts}, {"task_id": int(task_id)})
    return get_task(task_id)


# ---- drafts -----------------------------------------------------------------------

def _draft_row(row: Optional[dict]) -> Optional[dict]:
    if row is not None:
        row["payload"] = json.loads(row["content"]) if row["content"] else None
    return row


def create_draft(task_id: int, action: str, payload: dict) -> dict:
    """Store the exact action payload and its SHA-256 hash."""
    return _draft_row(_db().insert("drafts", {"task_id": int(task_id), "action": action, "deleted": 0,
                                              "content": canonical_json(payload), "content_hash": content_hash(payload)}))


def get_draft(draft_id: int) -> Optional[dict]:
    return _draft_row(_first(_db().select("drafts", {"draft_id": int(draft_id)})))


def get_latest_draft(task_id: int, action: str) -> Optional[dict]:
    return _draft_row(_first(_db().select("drafts", {"task_id": int(task_id), "action": action, "deleted": 0},
                                          order="draft_id", desc=True, limit=1)))


def get_draft_by_hash(task_id: int, content_hash_value: str) -> Optional[dict]:
    return _draft_row(_first(_db().select("drafts", {"task_id": int(task_id), "content_hash": content_hash_value},
                                          order="draft_id", desc=True, limit=1)))


def delete_draft_text(draft_id: int) -> None:
    """Drop the draft text after the decision; the hash stays (rule 3)."""
    _db().update("drafts", {"content": None, "deleted": 1}, {"draft_id": int(draft_id)})


def list_drafts(task_id: int) -> list:
    """Live (not yet decided) drafts for a task, newest first per action."""
    return [_draft_row(r) for r in _db().select("drafts", {"task_id": int(task_id), "deleted": 0},
                                                order="draft_id", desc=True)]


def draft_task_ids(action: str) -> set:
    """Ids of tasks that ever had a draft for this action (decided or not)."""
    return {r["task_id"] for r in _db().select("drafts", {"action": action})}


def list_live_drafts() -> list:
    """Every draft whose text is still stored (not yet decided), oldest first."""
    return [_draft_row(r) for r in _db().select("drafts", {"deleted": 0}, order="draft_id")]


# ---- approvals --------------------------------------------------------------------

def create_approval(task_id: int, action: str, content_hash_value: str, ttl_minutes: int = 30) -> dict:
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    return _db().insert("approvals", {"approval_id": uuid.uuid4().hex[:12], "task_id": int(task_id), "action": action,
                                      "content_hash": content_hash_value, "decision": "pending", "state": "prepared",
                                      "expires_at": expires, "used": 0})


def get_approval(approval_id: str) -> Optional[dict]:
    return _first(_db().select("approvals", {"approval_id": approval_id}))


def list_approvals(decision: Optional[str] = None) -> list:
    return _db().select("approvals", {"decision": decision} if decision else None, order="expires_at")


def mark_approval(approval_id: str, **fields) -> dict:
    unknown = set(fields) - set(APPROVAL_FIELDS)
    if unknown:
        raise ValueError(f"unknown approval fields: {sorted(unknown)}")
    if get_approval(approval_id) is None:
        raise ValueError(f"approval {approval_id} not found")
    if fields:
        _db().update("approvals", fields, {"approval_id": approval_id})
    return get_approval(approval_id)


def claim_approval(approval_id: str) -> bool:
    """Atomically flip used 0 -> 1 and state -> attempted. True only for the first caller,
    so an approval can never be executed twice even if two calls race."""
    return _db().update("approvals", {"used": 1, "state": "attempted"},
                        {"approval_id": approval_id, "used": 0, "decision": "approved"}) == 1


# ---- web decisions (owner's Send / Don't send / Edit on the dashboard) -------------------
# The dashboard server writes these rows with its own database key after checking the owner's
# sign-in. nyp_check_approvals applies them with the same code as a Telegram press.

def queue_web_decision(approval_id: str, decision: str, body: Optional[str] = None) -> dict:
    if decision not in WEB_DECISIONS:
        raise ValueError(f"decision must be one of {list(WEB_DECISIONS)}")
    return _db().insert("web_decisions", {"approval_id": approval_id, "decision": decision, "body": body,
                                          "status": "queued", "created_at": now_iso()})


def list_queued_web_decisions() -> list:
    return _db().select("web_decisions", {"status": "queued"}, order="id")


def finish_web_decision(decision_id: int, ok: bool, result: str) -> None:
    """Record the outcome and drop any edited text (rule 3)."""
    _db().update("web_decisions", {"status": "applied" if ok else "failed", "result": result[:300], "body": None,
                                   "applied_at": now_iso()}, {"id": int(decision_id)})


# ---- activity log -----------------------------------------------------------------

def log(actor: str, event: str, result: str) -> None:
    _db().insert("activity_log", {"ts": now_iso(), "actor": actor, "event": event, "result": result})


def list_activity(limit: int = 20) -> list:
    return _db().select("activity_log", order="id", desc=True, limit=int(limit))


def count(table: str) -> int:
    """Row count of one of our tables."""
    return _db().count(table)


# ---- tools ------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_WRITE, expected_credentials=DB_CREDENTIALS)
def nyp_store_init() -> dict:
    """
    Create the inbox database tables if needed and report row counts per table.

    Returns:
        dict: {"backend": "supabase" or "sqlite", "db_path": str, "counts": {table: rows}}.
    """
    log("nyp_store_init", "init", "ok")
    db = _db()
    return {"backend": db.name, "db_path": "supabase" if db.name == "supabase" else db_path(),
            "counts": {t: count(t) for t in TABLES}}


@tool(permission=ToolPermission.ADMIN, expected_credentials=DB_CREDENTIALS)
def nyp_store_reset(confirm: str) -> dict:
    """
    Delete ALL stored data (sync bookmark, analyses, tasks, drafts, approvals, activity log). Use only when the user explicitly asks to reset, e.g. after switching the test Gmail account.

    Args:
        confirm (str): Must be exactly RESET, typed by the user.
    Returns:
        dict: {"reset": true, "counts": {...}} or {"reset": false, "error": str}.
    """
    if confirm != "RESET":
        return {"reset": False, "error": "Not reset. The user must confirm with the word RESET."}
    _db().delete_all()
    log("nyp_store_reset", "reset", "all tables cleared")
    return {"reset": True, "counts": {t: count(t) for t in TABLES}}


@tool(permission=ToolPermission.READ_ONLY, expected_credentials=DB_CREDENTIALS)
def nyp_get_sync_state() -> dict:
    """
    Read the saved Gmail history id and the time of the last run.

    Returns:
        dict: {"history_id": str or null, "last_run": str or null}.
    """
    return get_sync_state()


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=DB_CREDENTIALS)
def nyp_set_sync_state(history_id: str) -> dict:
    """
    Save the Gmail history id bookmark and stamp the last run time.

    Args:
        history_id (str): Gmail historyId to resume from next time.
    Returns:
        dict: The saved sync state.
    """
    return set_sync_state(history_id)


@tool(permission=ToolPermission.READ_ONLY, expected_credentials=DB_CREDENTIALS)
def nyp_list_tasks(status: Optional[str] = None) -> list:
    """
    List tasks, optionally filtered by status.

    Args:
        status (str): One of open, pending_approval, on_hold, done, cancelled; empty for all.
    Returns:
        list: Task rows.
    """
    return list_tasks(status or None)


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=DB_CREDENTIALS)
def nyp_upsert_task(thread_id: str, title: str, deadline: Optional[str] = None, status: str = "open",
                    task_id: Optional[int] = None) -> dict:
    """
    Create a task, or update an existing one by task_id. Title must be in your own words.

    Args:
        thread_id (str): Gmail thread id the task comes from.
        title (str): Short task title in your own words, max 80 characters.
        deadline (str): ISO 8601 date or datetime, or empty.
        status (str): open, pending_approval, on_hold, done or cancelled.
        task_id (int): Existing task id to update; empty to create.
    Returns:
        dict: The saved task row.
    """
    if len(title) > 80:
        raise ValueError("title longer than 80 characters")
    fields = {"thread_id": thread_id, "title": title, "status": status}
    if deadline:
        fields["deadline"] = deadline
    task = upsert_task(task_id, **fields)
    log("nyp_upsert_task", "task_saved", f"task {task['task_id']}")
    return task


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=DB_CREDENTIALS)
def nyp_log(actor: str, event: str, result: str) -> dict:
    """
    Append one line to the activity log. Never put email text in it.

    Args:
        actor (str): Who did it, e.g. the tool name.
        event (str): What happened.
        result (str): Outcome.
    Returns:
        dict: {"ok": true}.
    """
    log(actor, event, result)
    return {"ok": True}
