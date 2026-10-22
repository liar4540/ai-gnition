"""
Step 6: ask the user on Telegram before any send or calendar write.

Connections: nyp_telegram (key_value: bot_token, allowed_chat_id) and nyp_google_read
(only to look up a reply's recipient and subject in code). This file never sends email
or writes to Calendar.

If the Telegram connection is missing or still holds a <placeholder>, every tool here
returns "telegram not configured" and creates no approval. There is no fallback to
approving in the Orchestrate chat (hard rule 5).

Every Telegram HTTP call goes through _telegram_call() so tests can replace it.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission
from ibm_watsonx_orchestrate.run import connections

import nyp_store as store
from nyp_read_mail import APP_ID as GOOGLE_READ_APP_ID, get_reply_headers
from nyp_safety_check import label

TELEGRAM_APP_ID = "nyp_telegram"
ACTIONS = ("send_reply", "calendar_create", "calendar_move")
TONES = {"formal": "More formal", "firm": "Firmer", "short": "Shorten"}
NOT_CONFIGURED = "telegram not configured"
CREDENTIALS = [ExpectedCredentials(app_id=TELEGRAM_APP_ID, type=ConnectionType.KEY_VALUE),
               ExpectedCredentials(app_id=GOOGLE_READ_APP_ID, type=ConnectionType.OAUTH2_AUTH_CODE)]


class TelegramNotConfigured(Exception):
    pass


def _telegram_config() -> dict:
    try:
        kv = connections.key_value(TELEGRAM_APP_ID) or {}
    except Exception:
        raise TelegramNotConfigured() from None
    token, chat_id = str(kv.get("bot_token", "")).strip(), str(kv.get("allowed_chat_id", "")).strip()
    if not token or not chat_id or token.startswith("<") or chat_id.startswith("<"):
        raise TelegramNotConfigured()
    return {"bot_token": token, "allowed_chat_id": chat_id}


def _telegram_call(method: str, payload: dict):
    """POST one Telegram Bot API method and return its 'result'."""
    token = _telegram_config()["bot_token"]
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram API {e.code}: {e.reason}") from None
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body.get('description')}")
    return body.get("result")


def _is_expired(approval: dict) -> bool:
    return datetime.fromisoformat(approval["expires_at"]) <= datetime.now(timezone.utc)


def _latest_analysis(task: dict) -> dict:
    ids = [i for i in (task.get("source_message_ids") or "").split(",") if i]
    return (store.get_analysis(ids[-1]) if ids else None) or {"risk": "unknown", "evidence": []}


def _card(task: dict, action: str, payload: dict, approval: dict) -> str:
    """The Telegram message: exact content, risk, confidence, evidence, and what approve does."""
    analysis = _latest_analysis(task)
    lines = [f"Approval needed: {task['title']}", ""]
    if action == "send_reply":
        lines += [f"Reply to: {payload['to']}", f"Subject: {payload['subject']}", "", payload["body"], ""]
        effect = "Gmail sends exactly this reply in the original thread."
    else:
        when = payload["start"] if payload["all_day"] else f"{payload['start']} to {payload['end']}"
        reminders = ", ".join(f"{r['minutes'] // 60}h before" for r in payload["reminders"])
        lines += [f"Event: {payload['title']}", f"When: {when}{' (all day)' if payload['all_day'] else ''}",
                  f"Reminders: {reminders}", ""]
        effect = ("Google Calendar moves the existing event to this time." if action == "calendar_move"
                  else "Google Calendar adds this event with these reminders.")
    lines.append(f"Risk: {analysis['risk']}   Confidence: {task.get('confidence')}")
    for code in analysis["evidence"]:
        lines.append(f"  - {label(code)}")
    if task.get("delta"):
        lines.append(f"What changed: {task['delta']}")
    lines += [f"If you approve: {effect}", f"Expires: {approval['expires_at'][:16].replace('T', ' ')} UTC"]
    return "\n".join(lines)


def _buttons(action: str, approval_id: str) -> dict:
    rows = [[{"text": "Approve", "callback_data": f"a:{approval_id}"},
             {"text": "Reject", "callback_data": f"r:{approval_id}"}]]
    if action == "send_reply":
        rows.append([{"text": text, "callback_data": f"t:{tone}:{approval_id}"} for tone, text in TONES.items()])
    return {"inline_keyboard": rows}


def _send_for_approval(task: dict, action: str, draft: dict) -> dict:
    """Create the approval row for a stored draft and send the Telegram card."""
    for old in store.list_approvals("pending"):
        if old["task_id"] == task["task_id"] and old["action"] == action:
            store.mark_approval(old["approval_id"], decision="cancelled", decided_at=store.now_iso())
    approval = store.create_approval(task["task_id"], action, draft["content_hash"])
    config = _telegram_config()
    try:
        _telegram_call("sendMessage", {"chat_id": config["allowed_chat_id"],
                                       "text": _card(task, action, draft["payload"], approval),
                                       "reply_markup": _buttons(action, approval["approval_id"])})
    except Exception as e:
        store.mark_approval(approval["approval_id"], decision="cancelled", decided_at=store.now_iso())
        store.log("nyp_request_approval", f"send failed {approval['approval_id']}", str(e))
        return {"ok": False, "error": f"could not send the Telegram message: {e}"}
    store.upsert_task(task["task_id"], status="pending_approval")
    store.log("nyp_request_approval", f"requested {approval['approval_id']}", f"task {task['task_id']} {action}")
    return {"ok": True, "approval_id": approval["approval_id"], "task_id": task["task_id"], "action": action,
            "state": "prepared", "expires_at": approval["expires_at"]}


def request_approval(task_id: int, action: str) -> dict:
    if action not in ACTIONS:
        return {"ok": False, "error": f"action must be one of {list(ACTIONS)}"}
    try:
        _telegram_config()
    except TelegramNotConfigured:
        return {"ok": False, "error": NOT_CONFIGURED}
    task = store.get_task(task_id)
    if task is None:
        return {"ok": False, "error": f"task {task_id} not found"}
    if task["status"] in ("done", "cancelled"):
        return {"ok": False, "error": f"task {task_id} is {task['status']}"}
    draft = store.get_latest_draft(task_id, action)
    if draft is None:
        return {"ok": False, "error": f"no {action} draft for task {task_id}"}

    if action == "send_reply" and "to" not in draft["payload"]:
        # Complete the reply in code: recipient and subject come from Gmail, never from the model.
        source_ids = [i for i in (task["source_message_ids"] or "").split(",") if i]
        headers = get_reply_headers(source_ids[-1])
        full = {"to": headers["to"], "subject": headers["subject"], "body": draft["payload"]["body"],
                "thread_id": draft["payload"]["thread_id"]}
        store.delete_draft_text(draft["draft_id"])
        draft = store.create_draft(task_id, action, full)
    return _send_for_approval(task, action, draft)


def check_approvals() -> dict:
    try:
        config = _telegram_config()
    except TelegramNotConfigured:
        return {"ok": False, "error": NOT_CONFIGURED}
    out = {"ok": True, "approved": [], "rejected": [], "tone_requests": [], "expired": [], "ignored": 0}

    for a in store.list_approvals("pending"):
        if _is_expired(a):
            store.mark_approval(a["approval_id"], decision="expired", decided_at=store.now_iso())
            draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
            if draft:
                store.delete_draft_text(draft["draft_id"])
            out["expired"].append(a["approval_id"])

    updates = _telegram_call("getUpdates", {"timeout": 0, "allowed_updates": ["callback_query"]}) or []
    for upd in updates:
        cq = upd.get("callback_query")
        if not cq:
            continue
        chat_id = str((cq.get("message") or {}).get("chat", {}).get("id", ""))
        if chat_id != config["allowed_chat_id"]:
            out["ignored"] += 1
            store.log("nyp_check_approvals", "ignored press", "chat id not allowed")
            continue
        answer = _handle_press(cq.get("data", ""), out)
        try:
            _telegram_call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": answer})
        except Exception:
            pass
    if updates:
        # Confirm everything we have seen so Telegram never returns these presses again.
        _telegram_call("getUpdates", {"offset": max(u["update_id"] for u in updates) + 1, "timeout": 0})
    return out


def _handle_press(data: str, out: dict) -> str:
    parts = data.split(":")
    kind, approval_id = parts[0], parts[-1]
    a = store.get_approval(approval_id)
    if a is None or a["decision"] != "pending" or (kind == "t" and (len(parts) != 3 or parts[1] not in TONES)):
        out["ignored"] += 1
        return "This request is no longer open."
    if _is_expired(a):
        store.mark_approval(approval_id, decision="expired", decided_at=store.now_iso())
        out["expired"].append(approval_id)
        return "This request expired."
    now = store.now_iso()
    if kind == "a":
        store.mark_approval(approval_id, decision="approved", state="approved", decided_at=now)
        store.log("nyp_check_approvals", f"approved {approval_id}", f"task {a['task_id']} {a['action']}")
        out["approved"].append({"approval_id": approval_id, "task_id": a["task_id"], "action": a["action"]})
        return "Approved."
    if kind == "r":
        store.mark_approval(approval_id, decision="rejected", decided_at=now)
        draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
        if draft:
            store.delete_draft_text(draft["draft_id"])
        task = store.get_task(a["task_id"])
        if task and task["status"] == "pending_approval":
            store.upsert_task(a["task_id"], status="open")
        store.log("nyp_check_approvals", f"rejected {approval_id}", f"task {a['task_id']} {a['action']}")
        out["rejected"].append({"approval_id": approval_id, "task_id": a["task_id"], "action": a["action"]})
        return "Rejected. Nothing will be sent."
    if kind == "t":
        tone = parts[1]
        store.mark_approval(approval_id, decision="cancelled", decided_at=now, result_id=f"tone:{tone}")
        draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
        store.log("nyp_check_approvals", f"tone {tone} {approval_id}", f"task {a['task_id']}")
        out["tone_requests"].append({"approval_id": approval_id, "task_id": a["task_id"], "tone": TONES[tone],
                                     "current_body": draft["payload"]["body"] if draft and draft["payload"] else None})
        return f"OK, rewriting: {TONES[tone]}."
    out["ignored"] += 1
    return "Unknown button."


def revise_draft(approval_id: str, new_body: str) -> dict:
    try:
        _telegram_config()
    except TelegramNotConfigured:
        return {"ok": False, "error": NOT_CONFIGURED}
    a = store.get_approval(approval_id)
    if a is None or a["action"] != "send_reply" or a["decision"] != "cancelled" \
            or not (a["result_id"] or "").startswith("tone:") or a["result_id"].endswith(":revised"):
        return {"ok": False, "error": "only a reply whose tone button was just pressed can be revised"}
    if not new_body or not new_body.strip():
        return {"ok": False, "error": "new_body is empty"}
    old = store.get_draft_by_hash(a["task_id"], a["content_hash"])
    if old is None or old["payload"] is None:
        return {"ok": False, "error": "the original draft is no longer available"}
    payload = {**old["payload"], "body": new_body.strip()}
    store.delete_draft_text(old["draft_id"])
    store.mark_approval(approval_id, result_id=a["result_id"] + ":revised")
    draft = store.create_draft(a["task_id"], "send_reply", payload)
    return _send_for_approval(store.get_task(a["task_id"]), "send_reply", draft)


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=CREDENTIALS)
def nyp_request_approval(task_id: int, action: str) -> dict:
    """
    Ask the user on Telegram to approve one action for a task. Nothing is sent or added until they approve.

    Args:
        task_id (int): Task id from nyp_save_analysis.
        action (str): send_reply, calendar_create or calendar_move.
    Returns:
        dict: {ok, approval_id, state: prepared, expires_at} or {ok: false, error}.
    """
    return request_approval(task_id, action)


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=CREDENTIALS)
def nyp_check_approvals() -> dict:
    """
    Read the user's Telegram button presses and update approvals. Only the configured chat can approve.

    Returns:
        dict: {ok, approved: [{approval_id, task_id, action}], rejected, tone_requests: [{approval_id, tone, current_body}], expired, ignored}.
    """
    return check_approvals()


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=CREDENTIALS)
def nyp_revise_draft(approval_id: str, new_body: str) -> dict:
    """
    After a tone button press, save your rewritten reply and send it for a fresh approval.

    Args:
        approval_id (str): The approval id from tone_requests in nyp_check_approvals.
        new_body (str): The rewritten reply text in the requested tone.
    Returns:
        dict: {ok, approval_id (new), state: prepared, expires_at} or {ok: false, error}.
    """
    return revise_draft(approval_id, new_body)
