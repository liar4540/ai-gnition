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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission
from ibm_watsonx_orchestrate.run import connections

import nyp_store as store
from nyp_read_mail import APP_ID as GOOGLE_READ_APP_ID, get_reply_headers
from nyp_safety_check import label
from nyp_save_analysis import create_reminder
LOCAL_TZ = store.LOCAL_TZ

TELEGRAM_APP_ID = "nyp_telegram"
ACTIONS = ("send_reply", "calendar_create", "calendar_move")
TONES = {"formal": "More formal", "firm": "Firmer", "short": "Shorten"}
NOT_CONFIGURED = "telegram not configured"
CREDENTIALS = [ExpectedCredentials(app_id=TELEGRAM_APP_ID, type=ConnectionType.KEY_VALUE),
               ExpectedCredentials(app_id=GOOGLE_READ_APP_ID, type=ConnectionType.KEY_VALUE)] + store.DB_CREDENTIALS


class TelegramNotConfigured(Exception):
    pass


def _telegram_config() -> dict:
    try:
        kv = connections.key_value(TELEGRAM_APP_ID) or {}
    except (Exception, SystemExit):   # the ADK calls sys.exit() when a connection is missing
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


def _local_time(iso: str) -> str:
    """'2026-10-25T09:00:00+08:00' -> 'Sun 25 Oct 2026, 9:00 AM' in the user's time zone."""
    dt = datetime.fromisoformat(iso)
    dt = dt.astimezone(LOCAL_TZ) if dt.tzinfo else dt
    return dt.strftime("%a %d %b %Y, %I:%M %p").replace(", 0", ", ")


def _when(payload: dict) -> str:
    if payload["all_day"]:
        first = date.fromisoformat(payload["start"])
        last = date.fromisoformat(payload["end"]) - timedelta(days=1)  # end is exclusive
        text = first.strftime("%a %d %b %Y")
        return (text if last == first else f"{text} to {last.strftime('%a %d %b %Y')}") + " (all day)"
    start, end = _local_time(payload["start"]), _local_time(payload["end"])
    same_day = start.split(",")[0] == end.split(",")[0]
    return f"{start} to {end.split(', ')[1] if same_day else end} ({LOCAL_TZ.tzname(None)})"


def _card(task: dict, action: str, payload: dict, approval: dict) -> str:
    """The Telegram message: exact content, risk, confidence, evidence, and what approve does."""
    from_user = not (task.get("source_message_ids") or "").strip()
    analysis = _latest_analysis(task)
    lines = [f"Approval needed: {task['title']}", ""]
    if action == "send_reply":
        lines += [f"Reply to: {payload['to']}", f"Subject: {payload['subject']}", "", payload["body"], ""]
        effect = "Gmail sends exactly this reply in the original thread."
    else:
        reminders = ", ".join(f"{r['minutes'] // 60}h before" for r in payload["reminders"])
        lines += [f"Event: {payload['title']}", f"When: {_when(payload)}", f"Reminders: {reminders}", ""]
        effect = ("Google Calendar moves the existing event to this time." if action == "calendar_move"
                  else "Google Calendar adds this event with these reminders.")
    if from_user:
        lines.append("Source: you asked for this in chat (no email involved).")
    else:
        lines.append(f"Risk: {analysis['risk']}   Confidence: {task.get('confidence')}")
        for code in analysis["evidence"]:
            lines.append(f"  - {label(code)}")
    if task.get("delta"):
        lines.append(f"What changed: {task['delta']}")
    lines += [f"If you approve: {effect}", f"Expires: {_local_time(approval['expires_at'])} ({LOCAL_TZ.tzname(None)})"]
    return "\n".join(lines)


def _buttons(action: str, approval_id: str) -> dict:
    rows = [[{"text": "Approve", "callback_data": f"a:{approval_id}"},
             {"text": "Reject", "callback_data": f"r:{approval_id}"}]]
    if action == "send_reply":
        rows.append([{"text": text, "callback_data": f"t:{tone}:{approval_id}"} for tone, text in TONES.items()])
    return {"inline_keyboard": rows}


def close_card(chat_id: str, message_id, note: str, card_text: Optional[str] = None,
               stamp: Optional[str] = None) -> None:
    """Remove the buttons from a Telegram card and post a short reply under it. With card_text and
    stamp, the card itself is rewritten to end with the decision (e.g. "REJECTED"), which also drops
    the buttons. Best effort: a Telegram hiccup must never undo or block a decision already recorded."""
    if not message_id:
        return
    try:
        if card_text and stamp:
            _telegram_call("editMessageText", {"chat_id": chat_id, "message_id": int(message_id),
                                               "text": f"{card_text}\n\n{stamp}"[:4096]})
        else:
            raise ValueError("no card text")
    except Exception:
        try:
            _telegram_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": int(message_id),
                                                      "reply_markup": {"inline_keyboard": []}})
        except Exception as e:
            store.log("nyp_request_approval", "could not remove buttons", str(e)[:200])
    notify(chat_id, message_id, note)


def notify(chat_id: str, message_id, text: str) -> None:
    """Send a short message, as a reply to the card when we know which one."""
    payload = {"chat_id": chat_id, "text": text}
    if message_id:
        payload["reply_parameters"] = {"message_id": int(message_id), "allow_sending_without_reply": True}
    try:
        _telegram_call("sendMessage", payload)
    except Exception as e:
        store.log("nyp_request_approval", "could not send notice", str(e)[:200])


def close_approval_card(approval: dict, note: str) -> None:
    try:
        chat_id = _telegram_config()["allowed_chat_id"]
    except TelegramNotConfigured:
        return
    close_card(chat_id, approval.get("telegram_message_id"), note)


def notify_result(approval_id: str, text: str) -> None:
    """Used by the executor after Google answers: reply under the original card."""
    approval = store.get_approval(approval_id) or {}
    try:
        chat_id = _telegram_config()["allowed_chat_id"]
    except TelegramNotConfigured:
        return
    notify(chat_id, approval.get("telegram_message_id"), text)


def describe_action(action: str, payload: dict) -> str:
    """One line for the done message: what exactly happened."""
    if action == "send_reply":
        return f"your reply to {payload.get('to', 'the sender')} was sent"
    verb = "moved in" if action == "calendar_move" else "added to"
    return f"'{payload.get('title', 'the event')}' was {verb} your Google Calendar ({_when(payload)})"


def _send_for_approval(task: dict, action: str, draft: dict) -> dict:
    """Create the approval row for a stored draft and send the Telegram card."""
    for old in store.list_approvals("pending"):
        if old["task_id"] == task["task_id"] and old["action"] == action:
            store.mark_approval(old["approval_id"], decision="cancelled", decided_at=store.now_iso())
            close_approval_card(old, "Replaced by a newer request below. This one can no longer be approved.")
    approval = store.create_approval(task["task_id"], action, draft["content_hash"])
    config = _telegram_config()
    try:
        sent = _telegram_call("sendMessage", {"chat_id": config["allowed_chat_id"],
                                              "text": _card(task, action, draft["payload"], approval),
                                              "reply_markup": _buttons(action, approval["approval_id"])})
        if isinstance(sent, dict) and sent.get("message_id") is not None:
            store.mark_approval(approval["approval_id"], telegram_message_id=str(sent["message_id"]))
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
            # The draft is kept: expiry is not the user's decision, and it can still be sent
            # from the dashboard (a new approval) or rejected. Its text goes once decided.
            store.mark_approval(a["approval_id"], decision="expired", decided_at=store.now_iso())
            close_card(config["allowed_chat_id"], a.get("telegram_message_id"),
                       "Expired after 30 minutes. Nothing was sent or added. Ask again if you still want it.")
            out["expired"].append(a["approval_id"])

    _apply_web_decisions(out, config["allowed_chat_id"])

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
        msg = cq.get("message") or {}
        answer = _handle_press(cq.get("data", ""), out, chat_id, msg.get("message_id"), msg.get("text"))
        try:
            _telegram_call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": answer})
        except Exception:
            pass
    if updates:
        # Confirm everything we have seen so Telegram never returns these presses again.
        _telegram_call("getUpdates", {"offset": max(u["update_id"] for u in updates) + 1, "timeout": 0})
    return out


def _handle_press(data: str, out: dict, chat_id: str = "", message_id=None, card_text=None, via: str = "") -> str:
    parts = data.split(":")
    kind, approval_id = parts[0], parts[-1]
    a = store.get_approval(approval_id)
    if a is None or a["decision"] != "pending" or (kind == "t" and (len(parts) != 3 or parts[1] not in TONES)):
        out["ignored"] += 1
        if a is not None and message_id:   # an old card that still shows buttons: tidy it up
            close_card(chat_id, message_id, "This request is already closed.")
        return "This request is no longer open."
    message_id = message_id or a.get("telegram_message_id")
    if _is_expired(a):
        store.mark_approval(approval_id, decision="expired", decided_at=store.now_iso())
        out["expired"].append(approval_id)
        close_card(chat_id, message_id, "Expired after 30 minutes. Nothing was sent or added. Ask again if you still want it.")
        return "This request expired."
    now = store.now_iso()
    where = " on the web dashboard" if via == "web" else ""
    if via == "web" and card_text is None and kind in ("a", "r"):
        # A web decision has no Telegram text to hand: rebuild the card so it can be stamped.
        draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
        task = store.get_task(a["task_id"])
        if draft and draft["payload"] and task:
            card_text = _card(task, a["action"], draft["payload"], a)
    if kind == "a":
        store.mark_approval(approval_id, decision="approved", state="approved", decided_at=now)
        store.log("nyp_check_approvals", f"approved {approval_id}", f"task {a['task_id']} {a['action']}")
        out["approved"].append({"approval_id": approval_id, "task_id": a["task_id"], "action": a["action"]})
        doing = "Sending the reply now" if a["action"] == "send_reply" else "Adding it to Google Calendar now"
        close_card(chat_id, message_id, f"Approved{where}. {doing}; you will get a message here when it is done.",
                   card_text, f"✅ APPROVED{where.upper()}")
        return "Approved."
    if kind == "r":
        store.mark_approval(approval_id, decision="rejected", decided_at=now)
        draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
        if draft:
            store.delete_draft_text(draft["draft_id"])
        task = store.get_task(a["task_id"])
        still_waiting = [x for x in store.list_approvals() if x["task_id"] == a["task_id"]
                         and x["approval_id"] != approval_id
                         and (x["decision"] == "pending" or (x["decision"] == "approved" and not x["used"]))]
        if task and task["status"] == "pending_approval" and not still_waiting:
            store.upsert_task(a["task_id"], status="cancelled")   # the web app shows it as Rejected
        store.log("nyp_check_approvals", f"rejected {approval_id}", f"task {a['task_id']} {a['action']}")
        out["rejected"].append({"approval_id": approval_id, "task_id": a["task_id"], "action": a["action"]})
        close_card(chat_id, message_id, f"Rejected{where}. Nothing was sent or added.", card_text,
                   f"❌ REJECTED{where.upper()}. Nothing was sent or added to your calendar.")
        return "Rejected. Nothing will be sent."
    if kind == "t":
        tone = parts[1]
        store.mark_approval(approval_id, decision="cancelled", decided_at=now, result_id=f"tone:{tone}")
        draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
        store.log("nyp_check_approvals", f"tone {tone} {approval_id}", f"task {a['task_id']}")
        close_card(chat_id, message_id, f"Rewriting the reply ({TONES[tone]}). A new card will follow.")
        out["tone_requests"].append({"approval_id": approval_id, "task_id": a["task_id"], "tone": TONES[tone],
                                     "current_body": draft["payload"]["body"] if draft and draft["payload"] else None})
        return f"OK, rewriting: {TONES[tone]}."
    out["ignored"] += 1
    return "Unknown button."


WEB_PRESS = {"approve": ("a", "approved"), "reject": ("r", "rejected")}


def _apply_web_decisions(out: dict, chat_id: str) -> None:
    """Apply the owner's Send / Don't send / Edit from the web dashboard. The dashboard server only
    queues them (after checking the owner's sign-in); they go through the same checks as a Telegram
    press, so a used or already-decided approval is refused here too."""
    for wd in store.list_queued_web_decisions():
        try:
            ok, text = _web_decision(out, chat_id, wd)
        except Exception as e:               # one bad row must not block the others
            ok, text = False, f"error: {e}"[:200]
        store.finish_web_decision(wd["id"], ok, text)
        store.log("nyp_check_approvals", f"web {wd['decision']} {wd['approval_id']}", "applied" if ok else text)


def _web_decision(out: dict, chat_id: str, wd: dict):
    a = store.get_approval(wd["approval_id"])
    if a is None:
        return False, "unknown approval"
    expired = a["decision"] == "expired" or (a["decision"] == "pending" and _is_expired(a))
    if a["decision"] != "pending" and not expired:
        return False, f"this request is already {a['decision']}"
    draft = store.get_draft_by_hash(a["task_id"], a["content_hash"])
    if draft is None or draft["payload"] is None:
        return False, "the draft is no longer available"
    if wd["decision"] == "edit":
        result = _web_edit(a, draft, wd["body"] or "")
        return result["ok"], (f"new approval {result['approval_id']}" if result["ok"] else result["error"])
    if expired:
        if a["decision"] == "pending":       # not yet swept: mark it and close its card first
            store.mark_approval(a["approval_id"], decision="expired", decided_at=store.now_iso())
            close_card(chat_id, a.get("telegram_message_id"), "Expired after 30 minutes. Nothing was sent or added.")
        if wd["decision"] == "reject":
            return _reject_expired(out, a, draft)
        # The owner approves the exact same content now: a fresh approval for the same hash.
        renewed = _send_for_approval(store.get_task(a["task_id"]), a["action"], draft)
        if not renewed["ok"]:
            return False, renewed["error"]
        a = store.get_approval(renewed["approval_id"])
    kind, wanted = WEB_PRESS[wd["decision"]]
    text = _handle_press(f"{kind}:{a['approval_id']}", out, chat_id, a.get("telegram_message_id"), via="web")
    return (store.get_approval(a["approval_id"]) or {}).get("decision") == wanted, text


def _reject_expired(out: dict, a: dict, draft: dict):
    """Don't send, for a draft whose approval already expired: drop the text and close the task."""
    store.delete_draft_text(draft["draft_id"])
    store.mark_approval(a["approval_id"], decision="rejected", decided_at=store.now_iso())
    task = store.get_task(a["task_id"])
    waiting = [x for x in store.list_approvals("pending") if x["task_id"] == a["task_id"]]
    if task and task["status"] in ("pending_approval", "open") and not waiting:
        store.upsert_task(a["task_id"], status="cancelled")
    store.log("nyp_check_approvals", f"rejected {a['approval_id']}", f"task {a['task_id']} {a['action']} (web)")
    out["rejected"].append({"approval_id": a["approval_id"], "task_id": a["task_id"], "action": a["action"]})
    return True, "Rejected. Nothing will be sent."


def _web_edit(a: dict, old: dict, new_body: str) -> dict:
    """Replace a reply with the owner's edited text: a new draft (new hash) and a new approval.
    A still-pending old approval is cancelled and its card closed by _send_for_approval."""
    if a["action"] != "send_reply":
        return {"ok": False, "error": "only a reply can be edited"}
    if not new_body.strip():
        return {"ok": False, "error": "the edited reply is empty"}
    draft = store.create_draft(a["task_id"], "send_reply", {**old["payload"], "body": new_body.strip()})
    store.delete_draft_text(old["draft_id"])
    return _send_for_approval(store.get_task(a["task_id"]), "send_reply", draft)


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
    Read the user's Telegram button presses and the owner's Send / Don't send / Edit from the web dashboard, and update approvals. Only the configured chat or the signed-in owner can approve.

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


def create_reminder_with_approval(title: str, start: str, end: Optional[str] = None,
                                  note: Optional[str] = None) -> dict:
    """Create the user's reminder task and immediately send its Telegram approval card."""
    made = create_reminder(title, start, end, note)
    if not made["ok"]:
        return made
    sent = request_approval(made["task_id"], "calendar_create")
    return {**made, "approval": sent}


@tool(permission=ToolPermission.READ_WRITE, expected_credentials=CREDENTIALS)
def nyp_create_reminder(title: str, start: str, end: Optional[str] = None, note: Optional[str] = None) -> dict:
    """
    Create a calendar event or reminder the USER asked for in chat (not from an email) and send its approval card to Telegram. Nothing is added until the user approves there.

    Args:
        title (str): Event title in your own words, max 80 characters.
        start (str): YYYY-MM-DD for an all-day event, or YYYY-MM-DDTHH:MM in Singapore time (no offset needed).
        end (str): Optional end, same format. Default: one hour after a timed start, or the same day for all-day.
        note (str): Optional short reason, max 300 characters.
    Returns:
        dict: {ok, task_id, event, approval: {ok, approval_id, expires_at} or {ok: false, error}} or {ok: false, rejected: [...]}.
    """
    return create_reminder_with_approval(title, start, end, note)
