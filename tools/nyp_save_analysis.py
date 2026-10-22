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
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

import nyp_store as store
from nyp_safety_check import confidence_adjustment, label


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


def calendar_payload(title: str, deadline: str) -> dict:
    """The exact event that will be created: all-day on a date, or one hour from a datetime."""
    when = _parse_deadline(deadline)
    if isinstance(when, datetime):
        end = when + timedelta(minutes=DEFAULT_EVENT_MINUTES)
        return {"title": title, "start": when.isoformat(), "end": end.isoformat(), "all_day": False,
                "reminders": REMINDERS}
    return {"title": title, "start": when.isoformat(), "end": (when + timedelta(days=1)).isoformat(),
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


@tool(permission=ToolPermission.READ_WRITE)
def nyp_save_analysis(message_id: str, thread_id: str, category: Category, task_title: Optional[str],
                      deadline: Optional[str], action: PlannedAction, draft_reply: Optional[str],
                      delta: Optional[str], model_confidence: int, reason: str) -> dict:
    """
    Save your analysis of one email after nyp_safety_check. Code validates every field and computes the final confidence.

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
        dict: {ok, task_id, band, confidence, needs_approval, needs_clarification, approvals_needed, reasons} or {ok: false, rejected: [...]}.
    """
    return save(message_id, thread_id, category, task_title, deadline, action, draft_reply, delta,
                model_confidence, reason)
