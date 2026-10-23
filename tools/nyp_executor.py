"""
Step 7: the executor -- the ONLY tool that sends email or writes to Calendar (hard rule 1).

It uses its own connection, nyp_google_act (gmail.send + calendar.events only), and acts
only when the approval is approved, unused, unexpired, for the same action, and the
SHA-256 of the exact stored content still equals the approved hash (hard rule 2).
"sent" / "added" / "moved" is said only after Google returns an id.

Every Google HTTP call goes through _google_call() so tests can replace it.
"""
import base64
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

import nyp_store as store
from nyp_google_auth import access_token

APP_ID = "nyp_google_act"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _google_call(method: str, url: str, body: dict) -> dict:
    """Send one write request to Google with the action token and return the JSON reply."""
    token = access_token(APP_ID)
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google API {e.code}: {e.reason}") from None


def _event_body(payload: dict) -> dict:
    key = "date" if payload["all_day"] else "dateTime"
    return {"summary": payload["title"], "start": {key: payload["start"]}, "end": {key: payload["end"]},
            "reminders": {"useDefault": False,
                          "overrides": [{"method": r["method"], "minutes": r["minutes"]} for r in payload["reminders"]]}}


def _send_reply(payload: dict) -> str:
    msg = EmailMessage()
    msg["To"] = payload["to"]
    msg["Subject"] = payload["subject"]
    msg.set_content(payload["body"])
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _google_call("POST", GMAIL_SEND_URL, {"raw": raw, "threadId": payload["thread_id"]})["id"]


def _refuse(approval_id: str, reason: str) -> dict:
    store.log("nyp_execute", f"refused {approval_id}", reason)
    return {"ok": False, "status": "refused", "reason": reason}


def execute(approval_id: str, action: str) -> dict:
    a = store.get_approval(approval_id)
    if a is None:
        return _refuse(approval_id, "no such approval")
    if a["decision"] != "approved":
        return _refuse(approval_id, f"approval is {a['decision']}, not approved")
    if a["used"]:
        return _refuse(approval_id, "approval was already used")
    if datetime.fromisoformat(a["expires_at"]) <= datetime.now(timezone.utc):
        store.mark_approval(approval_id, decision="expired")
        return _refuse(approval_id, "approval has expired")
    if action != a["action"]:
        return _refuse(approval_id, f"approval is for {a['action']}, not {action}")
    draft = store.get_latest_draft(a["task_id"], a["action"])
    if draft is None or draft["payload"] is None:
        return _refuse(approval_id, "no stored content for this approval")
    if store.content_hash(draft["payload"]) != a["content_hash"]:
        return _refuse(approval_id, "content changed since it was approved (hash mismatch)")
    task = store.get_task(a["task_id"])
    if a["action"] == "calendar_move" and not (task or {}).get("calendar_event_id"):
        return _refuse(approval_id, "there is no existing event to move")

    # Mark used + attempted BEFORE calling Google, atomically, so it can never run twice.
    if not store.claim_approval(approval_id):
        return _refuse(approval_id, "approval was already used")

    payload = draft["payload"]
    try:
        if a["action"] == "send_reply":
            result_id = _send_reply(payload)
        elif a["action"] == "calendar_create":
            result_id = _google_call("POST", EVENTS_URL, _event_body(payload))["id"]
        else:
            result_id = _google_call("PATCH", f"{EVENTS_URL}/{task['calendar_event_id']}", _event_body(payload))["id"]
        if not result_id:
            raise RuntimeError("Google returned no id")
    except Exception as e:
        store.mark_approval(approval_id, state="failed")
        store.log("nyp_execute", f"failed {approval_id}", str(e))
        return {"ok": False, "status": "failed", "state": "attempted then failed",
                "reason": f"{e}. Nothing was confirmed; you can request a new approval to retry."}

    store.mark_approval(approval_id, state="confirmed", result_id=result_id)
    store.delete_draft_text(draft["draft_id"])
    task_fields = {}
    if a["action"] in ("calendar_create", "calendar_move"):
        task_fields["calendar_event_id"] = result_id
    still_waiting = [x for x in store.list_approvals()
                     if x["task_id"] == a["task_id"] and x["approval_id"] != approval_id
                     and (x["decision"] == "pending" or (x["decision"] == "approved" and not x["used"]))]
    task_fields["status"] = "pending_approval" if still_waiting else "done"
    store.upsert_task(a["task_id"], **task_fields)

    status = {"send_reply": "sent", "calendar_create": "added", "calendar_move": "moved"}[a["action"]]
    store.log("nyp_execute", f"{status} {approval_id}", f"task {a['task_id']} id {result_id}")
    return {"ok": True, "status": status, "id": result_id, "task_id": a["task_id"]}


@tool(permission=ToolPermission.READ_WRITE,
      expected_credentials=[ExpectedCredentials(app_id=APP_ID, type=ConnectionType.KEY_VALUE)])
def nyp_execute(approval_id: str, action: str) -> dict:
    """
    Carry out ONE action the user approved on Telegram. Only use an approval_id that nyp_check_approvals just reported as approved.

    Args:
        approval_id (str): Approval id from the approved list of nyp_check_approvals.
        action (str): The approved action: send_reply, calendar_create or calendar_move.
    Returns:
        dict: {ok: true, status: sent|added|moved, id} only after Google returns an id; otherwise {ok: false, status: refused|failed, reason}.
    """
    return execute(approval_id, action)
