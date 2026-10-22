"""
Step 8: read-only chat views. Every tool returns short markdown for the Orchestrate chat.

No writes in this file. Counts come only from the store; nyp_inbox fetches sender and
subject live from Gmail (read-only connection) and joins them in memory without saving.
"""
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

import nyp_store as store
from nyp_read_mail import APP_ID as GOOGLE_READ_APP_ID, get_message
from nyp_safety_check import RULES_BY_CODE, label

INBOX_LIMIT = 25
EFFECTS = {"send_reply": "Gmail sends the reply shown in the Telegram message, in the same thread.",
           "calendar_create": "Google Calendar adds the event with reminders 1 day and 1 hour before.",
           "calendar_move": "Google Calendar moves the existing event to the new time."}


def _cell(value) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "/").replace("\n", " ")


def _table(headers: list, rows: list) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _tick(code: str) -> str:
    return ("✅ " if RULES_BY_CODE.get(code, {}).get("adjust", 0) > 0 else "⚠️ ") + label(code)


def dashboard() -> str:
    c = store.count
    analysed = c("analyses", "category IS NOT NULL")
    by_cat = {cat: c("analyses", "category=?", (cat,)) for cat in ("routine", "important", "suspicious")}
    pending = c("tasks", "status IN ('open','pending_approval','on_hold')")
    done = c("tasks", "status='done'")
    without_approval = c("approvals", "state='confirmed' AND decision!='approved'")
    return "\n".join([
        "### Inbox Summary",
        _table(["Emails analysed", "Routine", "Important", "Suspicious", "Pending tasks", "Completed tasks"],
               [[analysed, by_cat["routine"], by_cat["important"], by_cat["suspicious"], pending, done]]),
        "", "### Trust Metrics",
        _table(["Approvals needed", "Approved", "Rejected", "Cancelled", "Actions without approval"],
               [[c("approvals"), c("approvals", "decision='approved'"), c("approvals", "decision='rejected'"),
                 c("approvals", "decision IN ('cancelled','expired')"), without_approval]]),
        "", "### Productivity",
        _table(["Deadlines detected", "Events added", "Tasks completed", "Follow-ups generated"],
               [[c("tasks", "deadline IS NOT NULL"),
                 c("approvals", "action IN ('calendar_create','calendar_move') AND state='confirmed'"),
                 done, store.count_distinct("drafts", "task_id", "action='send_reply'")]]),
    ])


def explain(task_id: int) -> str:
    task = store.get_task(task_id)
    if task is None:
        return f"No task {task_id}."
    ids = [i for i in (task["source_message_ids"] or "").split(",") if i]
    analysis = (store.get_analysis(ids[-1]) if ids else None) or {"evidence": [], "band": None}
    why = task["reason"] or "No reason recorded."
    if task["delta"]:
        why += f" What changed in the latest email: {task['delta']}."
    evidence = [f"- {_tick(code)}" for code in analysis["evidence"]] or ["- No warning signs or trust signals found."]
    actions = []
    for d in store.list_drafts(task_id):
        if d["action"] not in actions:
            actions.append(d["action"])
    approve = [f"- {EFFECTS[a]}" for a in actions] or ["- Nothing is waiting for approval for this task."]
    approve.append("- Nothing happens until you press Approve on Telegram; each approval works once and expires after 30 minutes.")
    return "\n".join([
        f"### {task['title']}",
        "**Why did I recommend this?**", why, "",
        "**What evidence did I use?**", *evidence, "",
        "**How confident am I?**", f"{task['confidence']} / 100 ({analysis['band'] or 'unknown'} band)", "",
        "**What happens if you approve?**", *approve,
    ])


def suspicious_list() -> str:
    rows = store.list_analyses(quarantined=True)
    if not rows:
        return "No suspicious emails. 0 quarantined."
    out = [f"**{len(rows)} quarantined email(s).** No reply was drafted and no task was created for them.", ""]
    for r in rows:
        reasons = "; ".join(label(c) for c in r["evidence"] if RULES_BY_CODE.get(c, {}).get("adjust", 0) < 0)
        out.append(f"- `{r['message_id']}` risk **{r['risk']}**: {reasons or 'labelled suspicious by the analysis'}")
    return "\n".join(out)


def inbox() -> str:
    rows = [r for r in store.list_analyses() if r["category"]][-INBOX_LIMIT:]
    if not rows:
        return "No analysed emails yet. Say 'scan my inbox' first."
    table = []
    for r in reversed(rows):
        try:
            m = get_message(r["message_id"])
            sender, subject = m["sender_address"], m["subject"]
        except Exception:
            sender, subject = "(not available in Gmail)", "-"
        table.append([sender, subject, r["category"], r["risk"], r["confidence"]])
    return _table(["From", "Subject", "Label", "Risk", "Confidence"], table)


def tasks(status: Optional[str] = None) -> str:
    rows = store.list_tasks(status or None)
    if not rows:
        return "No tasks" + (f" with status {status}." if status else ".")
    return _table(["ID", "Task", "Deadline", "Status", "Confidence", "What changed"],
                  [[t["task_id"], t["title"], t["deadline"], t["status"], t["confidence"], t["delta"]] for t in rows])


def activity(limit: int = 20) -> str:
    rows = store.list_activity(limit)
    if not rows:
        return "No activity yet."
    return _table(["Time (UTC)", "Actor", "Event", "Result"],
                  [[r["ts"][:19].replace("T", " "), r["actor"], r["event"], r["result"]] for r in rows])


@tool(permission=ToolPermission.READ_ONLY)
def nyp_dashboard() -> str:
    """
    Show the trust dashboard: inbox summary, trust metrics and productivity, counted from the store.

    Returns:
        str: Markdown tables.
    """
    return dashboard()


@tool(permission=ToolPermission.READ_ONLY)
def nyp_explain(task_id: int) -> str:
    """
    Explain a task: why, what evidence, how confident, and what happens if the user approves.

    Args:
        task_id (int): Task id.
    Returns:
        str: Four-part markdown card.
    """
    return explain(task_id)


@tool(permission=ToolPermission.READ_ONLY)
def nyp_suspicious_list() -> str:
    """
    List quarantined emails with the reasons they were flagged.

    Returns:
        str: Markdown list.
    """
    return suspicious_list()


@tool(permission=ToolPermission.READ_ONLY,
      expected_credentials=[ExpectedCredentials(app_id=GOOGLE_READ_APP_ID, type=ConnectionType.OAUTH2_AUTH_CODE)])
def nyp_inbox() -> str:
    """
    Show analysed emails with sender and subject fetched live from Gmail (nothing is saved).

    Returns:
        str: Markdown table.
    """
    return inbox()


@tool(permission=ToolPermission.READ_ONLY)
def nyp_tasks(status: Optional[str] = None) -> str:
    """
    List tasks, optionally only one status.

    Args:
        status (str): open, pending_approval, on_hold, done or cancelled; empty for all.
    Returns:
        str: Markdown table.
    """
    return tasks(status)


@tool(permission=ToolPermission.READ_ONLY)
def nyp_activity(limit: int = 20) -> str:
    """
    Show the most recent activity log entries.

    Args:
        limit (int): How many entries, default 20.
    Returns:
        str: Markdown table.
    """
    return activity(limit)
