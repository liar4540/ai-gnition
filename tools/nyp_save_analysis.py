"""
Step 5: the validator between the model and everything else.

The planner (the agent's model) calls nyp_save_analysis with the fixed planner fields.
Code validates them, reads the safety evidence that nyp_safety_check already stored for
the message (it never trusts evidence passed by the model), computes the final
confidence and band, and writes the analysis, task and drafts.

Reply drafts are stored as {"body", "thread_id"} only. nyp_request_approval completes the
recipient and subject from Gmail in code (never from the model) before hashing the exact
payload that will be sent.
"""
from datetime import date, datetime, timedelta
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

import nyp_store as store
from nyp_safety_check import RULES_BY_CODE, confidence_adjustment, label


class Category(str, Enum):
    ROUTINE = "routine"
    IMPORTANT = "important"
    SUSPICIOUS = "suspicious"


class PlannedAction(str, Enum):
    REPLY = "reply"
    CALENDAR = "calendar"
    REPLY_AND_CALENDAR = "reply_and_calendar"
    REVIEW = "review"
    NONE = "none"


REMINDERS = [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}]
DEFAULT_EVENT_MINUTES = 60
LOCAL_TZ = store.LOCAL_TZ


def band_for(confidence: int) -> str:
    if confidence >= 90:
        return "high"
    if confidence >= 70:
        return "medium"
    return "low"


def _parse_deadline(deadline: Optional[str]):
    """Return a date or datetime, or None. Raises ValueError on anything that is not ISO 8601."""
    if not deadline:
        return None
    if len(deadline) == 10:
        return date.fromisoformat(deadline)
    return datetime.fromisoformat(deadline)


def _local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=LOCAL_TZ) if dt.tzinfo is None else dt


def calendar_payload(title: str, deadline: str, end: Optional[str] = None) -> dict:
    """The exact event that will be created: all-day on a date (or date range), or a timed event
    (one hour by default). Times without an offset are taken as local (LOCAL_TZ)."""
    when = _parse_deadline(deadline)
    until = _parse_deadline(end) if end else None
    if isinstance(when, datetime):
        start = _local(when)
        finish = _local(until) if isinstance(until, datetime) else start + timedelta(minutes=DEFAULT_EVENT_MINUTES)
        if finish <= start:
            raise ValueError("the event must end after it starts")
        return {"title": title, "start": start.isoformat(), "end": finish.isoformat(), "all_day": False,
                "reminders": REMINDERS}
    last_day = until if isinstance(until, date) and not isinstance(until, datetime) else when
    if last_day < when:
        raise ValueError("the event must end after it starts")
    return {"title": title, "start": when.isoformat(), "end": (last_day + timedelta(days=1)).isoformat(),
            "all_day": True, "reminders": REMINDERS}


def validate(category: str, action: str, task_title: Optional[str], deadline: Optional[str],
             draft_reply: Optional[str], model_confidence: int, reason: str) -> list:
    """Return a list of problems; empty means the planner output is acceptable."""
    problems = []
    if category not in {c.value for c in Category}:
        problems.append(f"category must be one of {[c.value for c in Category]}")
    if action not in {a.value for a in PlannedAction}:
        problems.append(f"action must be one of {[a.value for a in PlannedAction]}")
    if task_title and len(task_title) > 80:
        problems.append("task_title longer than 80 characters")
    if draft_reply and category == Category.SUSPICIOUS.value:
        problems.append("draft_reply is not allowed for a suspicious email")
    if not isinstance(model_confidence, int) or isinstance(model_confidence, bool) or not 0 <= model_confidence <= 100:
        problems.append("model_confidence must be an integer 0-100")
    if not reason or not reason.strip():
        problems.append("reason is required")
    try:
        _parse_deadline(deadline)
    except ValueError:
        problems.append("deadline must be an ISO 8601 date or datetime")
    if action in ("reply", "reply_and_calendar") and not draft_reply:
        problems.append(f"action {action} needs a draft_reply")
    if action in ("calendar", "reply_and_calendar") and not deadline:
        problems.append(f"action {action} needs a deadline")
    if action != "none" and not task_title:
        problems.append(f"action {action} needs a task_title")
    return problems


def save(message_id: str, thread_id: str, category: str, task_title: Optional[str], deadline: Optional[str],
         action: str, draft_reply: Optional[str], delta: Optional[str], model_confidence: int, reason: str) -> dict:
    category = getattr(category, "value", category)
    action = getattr(action, "value", action)
    problems = validate(category, action, task_title, deadline, draft_reply, model_confidence, reason)
    if problems:
        store.log("nyp_save_analysis", f"rejected {message_id}", "; ".join(problems))
        return {"ok": False, "rejected": problems}

    safety = store.get_analysis(message_id)
    if safety is None or safety["risk"] is None:
        store.log("nyp_save_analysis", f"rejected {message_id}", "no safety check")
        return {"ok": False, "rejected": ["run nyp_safety_check for this message first"]}

    evidence = safety["evidence"]
    confidence = max(0, min(100, model_confidence + confidence_adjustment(evidence)))
    band = band_for(confidence)
    reasons = [label(c) for c in evidence]
    warnings = [c for c in evidence if RULES_BY_CODE.get(c, {}).get("adjust", 0) < 0]
    if (category == Category.SUSPICIOUS.value and not safety["quarantined"] and not warnings
            and "BULK_SENDER" in evidence):
        # A signed newsletter with no warning sign at all: the code evidence wins over the label.
        category = Category.ROUTINE.value
        reasons.append("Saved as routine: a verified newsletter with no warning signs (the model had labelled it suspicious).")
    quarantined = bool(safety["quarantined"]) or category == Category.SUSPICIOUS.value
    if quarantined and category != Category.SUSPICIOUS.value:
        reasons.append(f"Code checks overrode the model's '{category}' label.")
        category = Category.SUSPICIOUS.value

    store.save_analysis(message_id, thread_id, category, safety["risk"], confidence, band, evidence, quarantined)

    base = {"ok": True, "message_id": message_id, "category": category, "confidence": confidence, "band": band,
            "evidence": evidence, "reasons": reasons}
    if quarantined:
        store.log("nyp_save_analysis", f"quarantined {message_id}", ",".join(evidence) or "model said suspicious")
        return {**base, "task_id": None, "quarantined": True, "needs_approval": False, "needs_clarification": False}

    if action == PlannedAction.NONE.value or not task_title:
        store.log("nyp_save_analysis", f"analysed {message_id}", f"{category}, no task")
        return {**base, "task_id": None, "quarantined": False, "needs_approval": False, "needs_clarification": False}

    needs_clarification = band == "low"
    wants_approval = action in ("reply", "calendar", "reply_and_calendar")
    needs_approval = wants_approval and not needs_clarification
    status = "on_hold" if action == PlannedAction.REVIEW.value else ("pending_approval" if needs_approval else "open")

    existing = store.get_task_by_thread(thread_id)
    fields = {"title": task_title, "status": status, "confidence": confidence, "reason": reason}
    if deadline:
        fields["deadline"] = deadline
    if existing:
        ids = [i for i in (existing["source_message_ids"] or "").split(",") if i]
        if message_id not in ids:
            ids.append(message_id)
        fields["source_message_ids"] = ",".join(ids)
        if delta:
            fields["delta"] = delta
        task = store.upsert_task(existing["task_id"], **fields)
        store.log("nyp_save_analysis", "replanned", f"task {task['task_id']}: {delta or 'updated'}")
    else:
        task = store.upsert_task(thread_id=thread_id, source_message_ids=message_id, delta=delta, **fields)
        store.log("nyp_save_analysis", "task_created", f"task {task['task_id']}")

    approvals_needed = []
    if needs_approval:
        if action in ("reply", "reply_and_calendar"):
            store.create_draft(task["task_id"], "send_reply", {"body": draft_reply, "thread_id": thread_id})
            approvals_needed.append("send_reply")
        if action in ("calendar", "reply_and_calendar"):
            cal_action = "calendar_move" if task.get("calendar_event_id") else "calendar_create"
            store.create_draft(task["task_id"], cal_action, calendar_payload(task_title, deadline))
            approvals_needed.append(cal_action)

    return {**base, "task_id": task["task_id"], "quarantined": False, "needs_approval": needs_approval,
            "needs_clarification": needs_clarification, "approvals_needed": approvals_needed,
            "status": status}


def save_and_request(message_id: str, thread_id: str, category: str, task_title: Optional[str],
                     deadline: Optional[str], action: str, draft_reply: Optional[str], delta: Optional[str],
                     model_confidence: int, reason: str) -> dict:
    """save(), then send every needed approval to Telegram in code, so no approval depends on the
    model remembering a follow-up call. Sending a card never sends email or writes to Calendar."""
    result = save(message_id, thread_id, category, task_title, deadline, action, draft_reply, delta,
                  model_confidence, reason)
    if result.get("ok") and result.get("approvals_needed"):
        import nyp_request_approval as approvals   # imported here: that module imports this one
        result["approvals"] = [{"action": a, **approvals.request_approval(result["task_id"], a)}
                               for a in result["approvals_needed"]]
    return result


@tool(permission=ToolPermission.READ_WRITE,
      expected_credentials=[ExpectedCredentials(app_id="nyp_telegram", type=ConnectionType.KEY_VALUE),
                            ExpectedCredentials(app_id="nyp_google_read", type=ConnectionType.KEY_VALUE)]
      + store.DB_CREDENTIALS)
def nyp_save_analysis(message_id: str, thread_id: str, category: Category, task_title: Optional[str],
                      deadline: Optional[str], action: PlannedAction, draft_reply: Optional[str],
                      delta: Optional[str], model_confidence: int, reason: str) -> dict:
    """
    Save your analysis of one email after nyp_safety_check. Code validates every field, computes the final confidence, and sends any needed approval to Telegram itself.

    Args:
        message_id (str): Gmail message id.
        thread_id (str): Gmail thread id.
        category (Category): routine, important or suspicious.
        task_title (str): Short task in your own words, max 80 characters, or null.
        deadline (str): ISO 8601 date or datetime, or null.
        action (PlannedAction): reply, calendar, reply_and_calendar, review or none.
        draft_reply (str): Reply text in your own words, or null. Never for suspicious emails.
        delta (str): What changed versus the earlier email in this thread, or null.
        model_confidence (int): Your confidence 0-100.
        reason (str): One sentence, in your own words.
    Returns:
        dict: {ok, task_id, band, confidence, needs_approval, needs_clarification, approvals_needed, approvals: [sent cards], reasons} or {ok: false, rejected: [...]}.
    """
    return save_and_request(message_id, thread_id, category, task_title, deadline, action, draft_reply, delta,
                            model_confidence, reason)


def create_reminder(title: str, start: str, end: Optional[str] = None, note: Optional[str] = None) -> dict:
    """A calendar event the user asked for directly in chat (no email behind it). It becomes a task
    with a calendar_create draft; nothing is written to Calendar until approved on Telegram."""
    title = (title or "").strip()
    problems = []
    if not title:
        problems.append("title is required")
    elif len(title) > 80:
        problems.append("title longer than 80 characters")
    try:
        payload = calendar_payload(title, start, end)
    except (TypeError, ValueError) as e:
        problems.append(f"start/end must be ISO 8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM): {e}")
        payload = None
    if note and len(note) > 300:
        problems.append("note longer than 300 characters")
    if problems:
        store.log("nyp_create_reminder", "rejected", "; ".join(problems))
        return {"ok": False, "rejected": problems}

    task = store.upsert_task(thread_id=f"user-request-{store.now_iso()}", source_message_ids="", title=title,
                             deadline=payload["start"], status="pending_approval", confidence=100,
                             reason=note or "Requested by the user in chat.")
    store.create_draft(task["task_id"], "calendar_create", payload)
    store.log("nyp_create_reminder", "task_created", f"task {task['task_id']} calendar event")
    return {"ok": True, "task_id": task["task_id"], "approvals_needed": ["calendar_create"], "event": payload}



class PlannedEmail(BaseModel):
    message_id: str = Field(description="Gmail message id, copied from the read tool")
    thread_id: str = Field(description="Gmail thread id, copied from the read tool")
    category: Category = Field(description="routine, important or suspicious")
    task_title: Optional[str] = Field(default=None, description="Short task in your own words, max 80 chars, or null")
    deadline: Optional[str] = Field(default=None, description="ISO 8601 date or datetime, or null")
    action: PlannedAction = Field(description="reply, calendar, reply_and_calendar, review or none")
    draft_reply: Optional[str] = Field(default=None, description="Reply text in your own words, or null")
    delta: Optional[str] = Field(default=None, description="What changed versus the earlier email in this thread, or null")
    model_confidence: int = Field(description="Your confidence 0-100")
    reason: str = Field(description="One sentence, in your own words")


def _note_calendar_clash(item: dict) -> None:
    """Record CALENDAR_CLASH evidence in code when a timed deadline overlaps an existing event."""
    if item.get("action") not in ("calendar", "reply_and_calendar") or not item.get("deadline"):
        return
    try:
        payload = calendar_payload(item.get("task_title") or "event", item["deadline"])
        if payload["all_day"]:
            return
        import nyp_calendar_check as cal
        if cal.conflicts(payload["start"], payload["end"])["clash"]:
            store.add_evidence(item["message_id"], "CALENDAR_CLASH")
    except Exception:
        pass   # a calendar lookup problem must not block saving the analysis


def save_many(items: list) -> dict:
    results = []
    for raw in items:
        item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        item = {k: getattr(v, "value", v) for k, v in item.items()}
        _note_calendar_clash(item)
        try:
            r = save_and_request(item.get("message_id"), item.get("thread_id"), item.get("category"),
                                 item.get("task_title"), item.get("deadline"), item.get("action"),
                                 item.get("draft_reply"), item.get("delta"), item.get("model_confidence"),
                                 item.get("reason"))
        except Exception as e:   # one bad item must not lose the rest of the batch
            r = {"ok": False, "rejected": [f"could not save: {e}"]}
        results.append({"message_id": item.get("message_id"), **{k: r.get(k) for k in
                        ("ok", "category", "band", "task_id", "quarantined", "needs_clarification", "rejected")},
                        "approvals_sent": [a["action"] for a in r.get("approvals", []) if a.get("ok")]})
    saved = sum(1 for r in results if r["ok"])
    store.log("nyp_save_analysis", "batch saved", f"{saved} of {len(results)} saved")
    return {"saved": saved, "rejected": len(results) - saved, "results": results}


@tool(permission=ToolPermission.READ_WRITE,
      expected_credentials=[ExpectedCredentials(app_id="nyp_telegram", type=ConnectionType.KEY_VALUE),
                            ExpectedCredentials(app_id="nyp_google_read", type=ConnectionType.KEY_VALUE)]
      + store.DB_CREDENTIALS)
def nyp_save_analyses(items: List[PlannedEmail]) -> dict:
    """
    Save your analysis of SEVERAL emails in one call: one item per email returned by the read tool. Code validates each item, checks calendar clashes, computes confidence and sends approvals to Telegram.

    Args:
        items (List[PlannedEmail]): One analysis per email; include every email you were given.
    Returns:
        dict: {saved, rejected, results: [{message_id, ok, category, band, task_id, quarantined, needs_clarification, rejected, approvals_sent}]}.
    """
    return save_many(items)
