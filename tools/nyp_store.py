"""
The ONLY module that opens the database (hard rule 7).

SQLite at /tmp/nyp_inbox.db (override with NYP_DB_PATH for local tests). Other tool
files import the plain helper functions below; the @tool functions at the bottom are
the ones the agent can call. No email subject, body or sender is ever written here
(hard rule 3) -- draft content is the one exception, and it is deleted after the
decision, leaving only its hash.
"""
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

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
  expires_at TEXT, used INTEGER DEFAULT 0, decided_at TEXT, result_id TEXT);
CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, actor TEXT, event TEXT, result TEXT);
"""

TABLES = ("sync_state", "analyses", "tasks", "drafts", "approvals", "activity_log")
TASK_FIELDS = ("thread_id", "source_message_ids", "title", "deadline", "status", "confidence",
               "reason", "delta", "calendar_event_id")
APPROVAL_FIELDS = ("decision", "state", "used", "decided_at", "result_id", "expires_at")


def db_path() -> str:
    return os.environ.get("NYP_DB_PATH") or "/tmp/nyp_inbox.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: dict) -> str:
    """SHA-256 of the canonical JSON (sorted keys) of an action payload."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ---- sync state -------------------------------------------------------------------

def get_sync_state() -> dict:
    rows = _query("SELECT history_id, last_run FROM sync_state WHERE id=1")
    return rows[0] if rows else {"history_id": None, "last_run": None}


def set_sync_state(history_id: Optional[str]) -> dict:
    _execute("INSERT INTO sync_state (id, history_id, last_run) VALUES (1, ?, ?) "
             "ON CONFLICT(id) DO UPDATE SET history_id=excluded.history_id, last_run=excluded.last_run",
             (history_id, now_iso()))
    return get_sync_state()


# ---- analyses ---------------------------------------------------------------------

def save_analysis(message_id: str, thread_id: str, category: str, risk: str, confidence: int,
                  band: str, evidence: list, quarantined: bool) -> dict:
    _execute("INSERT OR REPLACE INTO analyses (message_id, thread_id, category, risk, confidence, band, "
             "evidence, quarantined, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (message_id, thread_id, category, risk, int(confidence), band, json.dumps(list(evidence)),
              1 if quarantined else 0, now_iso()))
    return get_analysis(message_id)


def record_safety(message_id: str, risk: str, evidence: list, quarantined: bool) -> dict:
    """Save safety-check evidence before the planner runs. Keeps CALENDAR_CLASH if already recorded."""
    existing = get_analysis(message_id)
    codes = list(evidence)
    if existing and "CALENDAR_CLASH" in existing["evidence"] and "CALENDAR_CLASH" not in codes:
        codes.append("CALENDAR_CLASH")
    if existing:
        _execute("UPDATE analyses SET risk=?, evidence=?, quarantined=? WHERE message_id=?",
                 (risk, json.dumps(codes), 1 if quarantined else 0, message_id))
    else:
        _execute("INSERT INTO analyses (message_id, risk, evidence, quarantined, created_at) VALUES (?, ?, ?, ?, ?)",
                 (message_id, risk, json.dumps(codes), 1 if quarantined else 0, now_iso()))
    return get_analysis(message_id)


def add_evidence(message_id: str, code: str) -> None:
    """Append one evidence code (e.g. CALENDAR_CLASH) to a message's analysis row."""
    existing = get_analysis(message_id)
    if existing is None:
        _execute("INSERT INTO analyses (message_id, risk, evidence, created_at) VALUES (?, 'low', ?, ?)",
                 (message_id, json.dumps([code]), now_iso()))
    elif code not in existing["evidence"]:
        _execute("UPDATE analyses SET evidence=? WHERE message_id=?",
                 (json.dumps(existing["evidence"] + [code]), message_id))


def get_analysis(message_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM analyses WHERE message_id=?", (message_id,))
    if not rows:
        return None
    row = rows[0]
    row["evidence"] = json.loads(row["evidence"] or "[]")
    return row


def list_analyses(category: Optional[str] = None, quarantined: Optional[bool] = None) -> list:
    sql, params = "SELECT * FROM analyses WHERE 1=1", []
    if category:
        sql += " AND category=?"
        params.append(category)
    if quarantined is not None:
        sql += " AND quarantined=?"
        params.append(1 if quarantined else 0)
    rows = _query(sql + " ORDER BY created_at", tuple(params))
    for row in rows:
        row["evidence"] = json.loads(row["evidence"] or "[]")
    return rows


# ---- tasks ------------------------------------------------------------------------

def get_task(task_id: int) -> Optional[dict]:
    rows = _query("SELECT * FROM tasks WHERE task_id=?", (int(task_id),))
    return rows[0] if rows else None


def get_task_by_thread(thread_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM tasks WHERE thread_id=? ORDER BY task_id DESC LIMIT 1", (thread_id,))
    return rows[0] if rows else None


def list_tasks(status: Optional[str] = None) -> list:
    if status:
        return _query("SELECT * FROM tasks WHERE status=? ORDER BY task_id", (status,))
    return _query("SELECT * FROM tasks ORDER BY task_id")


def upsert_task(task_id: Optional[int] = None, **fields) -> dict:
    """Insert a task, or update the given fields of an existing one."""
    unknown = set(fields) - set(TASK_FIELDS)
    if unknown:
        raise ValueError(f"unknown task fields: {sorted(unknown)}")
    ts = now_iso()
    if task_id is None:
        fields.setdefault("status", "open")
        cols = list(fields) + ["created_at", "updated_at"]
        vals = list(fields.values()) + [ts, ts]
        task_id = _execute(f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           tuple(vals))
    else:
        if get_task(task_id) is None:
            raise ValueError(f"task {task_id} not found")
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
            _execute(f"UPDATE tasks SET {sets} WHERE task_id=?", tuple(fields.values()) + (ts, int(task_id)))
    return get_task(task_id)


# ---- drafts -----------------------------------------------------------------------

def create_draft(task_id: int, action: str, payload: dict) -> dict:
    """Store the exact action payload and its SHA-256 hash."""
    h = content_hash(payload)
    draft_id = _execute("INSERT INTO drafts (task_id, action, content, content_hash) VALUES (?, ?, ?, ?)",
                        (int(task_id), action, canonical_json(payload), h))
    return get_draft(draft_id)


def get_draft(draft_id: int) -> Optional[dict]:
    rows = _query("SELECT * FROM drafts WHERE draft_id=?", (int(draft_id),))
    if not rows:
        return None
    row = rows[0]
    row["payload"] = json.loads(row["content"]) if row["content"] else None
    return row


def get_latest_draft(task_id: int, action: str) -> Optional[dict]:
    rows = _query("SELECT draft_id FROM drafts WHERE task_id=? AND action=? AND deleted=0 "
                  "ORDER BY draft_id DESC LIMIT 1", (int(task_id), action))
    return get_draft(rows[0]["draft_id"]) if rows else None


def get_draft_by_hash(task_id: int, content_hash_value: str) -> Optional[dict]:
    rows = _query("SELECT draft_id FROM drafts WHERE task_id=? AND content_hash=? "
                  "ORDER BY draft_id DESC LIMIT 1", (int(task_id), content_hash_value))
    return get_draft(rows[0]["draft_id"]) if rows else None


def delete_draft_text(draft_id: int) -> None:
    """Drop the draft text after the decision; the hash stays (rule 3)."""
    _execute("UPDATE drafts SET content=NULL, deleted=1 WHERE draft_id=?", (int(draft_id),))


# ---- approvals --------------------------------------------------------------------

def create_approval(task_id: int, action: str, content_hash_value: str, ttl_minutes: int = 30) -> dict:
    approval_id = uuid.uuid4().hex[:12]
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    _execute("INSERT INTO approvals (approval_id, task_id, action, content_hash, decision, state, expires_at, used) "
             "VALUES (?, ?, ?, ?, 'pending', 'prepared', ?, 0)",
             (approval_id, int(task_id), action, content_hash_value, expires))
    return get_approval(approval_id)


def get_approval(approval_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM approvals WHERE approval_id=?", (approval_id,))
    return rows[0] if rows else None


def list_approvals(decision: Optional[str] = None) -> list:
    if decision:
        return _query("SELECT * FROM approvals WHERE decision=? ORDER BY expires_at", (decision,))
    return _query("SELECT * FROM approvals ORDER BY expires_at")


def mark_approval(approval_id: str, **fields) -> dict:
    unknown = set(fields) - set(APPROVAL_FIELDS)
    if unknown:
        raise ValueError(f"unknown approval fields: {sorted(unknown)}")
    if get_approval(approval_id) is None:
        raise ValueError(f"approval {approval_id} not found")
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        _execute(f"UPDATE approvals SET {sets} WHERE approval_id=?", tuple(fields.values()) + (approval_id,))
    return get_approval(approval_id)


def claim_approval(approval_id: str) -> bool:
    """Atomically flip used 0 -> 1 and state -> attempted. True only for the first caller,
    so an approval can never be executed twice even if two calls race."""
    conn = _connect()
    try:
        cur = conn.execute("UPDATE approvals SET used=1, state='attempted' "
                           "WHERE approval_id=? AND used=0 AND decision='approved'", (approval_id,))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


# ---- activity log -----------------------------------------------------------------

def log(actor: str, event: str, result: str) -> None:
    _execute("INSERT INTO activity_log (ts, actor, event, result) VALUES (?, ?, ?, ?)",
             (now_iso(), actor, event, result))


def list_activity(limit: int = 20) -> list:
    return _query("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (int(limit),))


def count(table: str, where: str = "", params: tuple = ()) -> int:
    """Row count for the dashboard. `table` must be one of ours; `where` is code-written, never user text."""
    if table not in TABLES:
        raise ValueError(f"unknown table {table}")
    sql = f"SELECT COUNT(*) AS n FROM {table}" + (f" WHERE {where}" if where else "")
    return _query(sql, params)[0]["n"]


def count_distinct(table: str, column: str, where: str = "", params: tuple = ()) -> int:
    if table not in TABLES or not column.isidentifier():
        raise ValueError(f"bad table or column {table}.{column}")
    sql = f"SELECT COUNT(DISTINCT {column}) AS n FROM {table}" + (f" WHERE {where}" if where else "")
    return _query(sql, params)[0]["n"]


def list_drafts(task_id: int) -> list:
    """Live (not yet decided) drafts for a task, newest first per action."""
    rows = _query("SELECT draft_id FROM drafts WHERE task_id=? AND deleted=0 ORDER BY draft_id DESC", (int(task_id),))
    return [get_draft(r["draft_id"]) for r in rows]


# ---- tools ------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_WRITE)
def nyp_store_init() -> dict:
    """
    Create the inbox database tables if needed and report row counts per table.

    Returns:
        dict: {"db_path": str, "counts": {table: rows}}.
    """
    log("nyp_store_init", "init", "ok")
    return {"db_path": db_path(), "counts": {t: count(t) for t in TABLES}}


@tool(permission=ToolPermission.READ_ONLY)
def nyp_get_sync_state() -> dict:
    """
    Read the saved Gmail history id and the time of the last run.

    Returns:
        dict: {"history_id": str or null, "last_run": str or null}.
    """
    return get_sync_state()


@tool(permission=ToolPermission.READ_WRITE)
def nyp_set_sync_state(history_id: str) -> dict:
    """
    Save the Gmail history id bookmark and stamp the last run time.

    Args:
        history_id (str): Gmail historyId to resume from next time.
    Returns:
        dict: The saved sync state.
    """
    return set_sync_state(history_id)


@tool(permission=ToolPermission.READ_ONLY)
def nyp_list_tasks(status: Optional[str] = None) -> list:
    """
    List tasks, optionally filtered by status.

    Args:
        status (str): One of open, pending_approval, on_hold, done, cancelled; empty for all.
    Returns:
        list: Task rows.
    """
    return list_tasks(status or None)


@tool(permission=ToolPermission.READ_WRITE)
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


@tool(permission=ToolPermission.READ_WRITE)
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
