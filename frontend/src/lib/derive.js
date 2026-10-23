// Join the agent's export (analyses, tasks, approvals) into one row per email.
// Everything here is derived from fields the backend returned; nothing is invented.

export const CATEGORY = {
  important: { label: "Important", color: "primary" },
  routine: { label: "Routine", color: "default" },
  suspicious: { label: "Suspicious", color: "error" },
};

export const RISK = {
  low: { label: "Low risk", color: "success" },
  medium: { label: "Medium risk", color: "warning" },
  high: { label: "High risk", color: "error" },
};
export const RISK_ORDER = { low: 0, medium: 1, high: 2 };

export const STATUS = {
  quarantined: { label: "Quarantined", color: "error" },
  awaiting_approval: { label: "Awaiting approval", color: "warning" },
  needs_input: { label: "Needs your input", color: "warning" },
  on_hold: { label: "On hold", color: "warning" },
  task_open: { label: "Task open", color: "info" },
  done: { label: "Done", color: "success" },
  rejected: { label: "Rejected", color: "error" },
  cancelled: { label: "Cancelled", color: "default" },
  no_action: { label: "No action", color: "default" },
};

export const ACTION_LABEL = {
  send_reply: "Send reply",
  calendar_create: "Add calendar event",
  calendar_move: "Move calendar event",
};

export const DECISION = {
  pending: { label: "Waiting on Telegram", color: "warning" },
  approved: { label: "Approved", color: "success" },
  rejected: { label: "Rejected", color: "error" },
  cancelled: { label: "Cancelled", color: "default" },
  expired: { label: "Expired", color: "default" },
};

// A task whose approvals were all decided and the last decision was Reject: nothing was sent or added.
export function wasRejected(task, approvals = []) {
  if (!task || !["open", "cancelled"].includes(task.status)) return false;
  const decided = approvals.filter((a) => a.decision !== "pending");
  if (!decided.length || decided.length !== approvals.length) return false;
  const last = [...decided].sort((x, y) => (x.decided_at || "").localeCompare(y.decided_at || "")).pop();
  return last.decision === "rejected";
}

function statusFor(analysis, task, approvals) {
  if (analysis.quarantined) return "quarantined";
  if (!task) return "no_action";
  if (wasRejected(task, approvals)) return "rejected";
  if (task.status === "pending_approval") return "awaiting_approval";
  if (task.status === "done") return "done";
  if (task.status === "cancelled") return "cancelled";
  if (task.status === "on_hold") return "on_hold";
  if (analysis.band === "low") return "needs_input";
  return "task_open";
}

const NEXT = {
  quarantined: "Quarantined by the safety checks. No reply was drafted and no task was created.",
  awaiting_approval: "Approve or reject on Telegram (@nypfrontendbot). Nothing is sent until you approve.",
  needs_input: "Confidence is low, so the agent did not act. Answer its question in the assistant.",
  on_hold: "Held for human review.",
  task_open: "Task created. No action is waiting for approval.",
  done: "Completed.",
  rejected: "You rejected this on Telegram. Nothing was sent and nothing was added to your calendar.",
  cancelled: "Cancelled.",
  no_action: "No action needed.",
};

export function buildEmails(state, meta = {}) {
  const taskByMessage = new Map();
  for (const t of state.tasks) {
    for (const id of (t.source_message_ids || "").split(",").filter(Boolean)) taskByMessage.set(id, t);
  }
  const approvalsByTask = new Map();
  for (const a of state.approvals) {
    if (!approvalsByTask.has(a.task_id)) approvalsByTask.set(a.task_id, []);
    approvalsByTask.get(a.task_id).push(a);
  }
  return state.analyses.map((a) => {
    const task = taskByMessage.get(a.message_id) || null;
    const approvals = task ? approvalsByTask.get(task.task_id) || [] : [];
    const status = statusFor(a, task, approvals);
    const m = meta[a.message_id];
    return {
      ...a,
      task,
      approvals,
      status,
      next: NEXT[status],
      from: m?.from || "",
      subject: m?.subject || "",
      receivedAt: m?.received_at || null,
      metaState: !m ? "loading" : m.available ? "ok" : "unavailable",
    };
  });
}

// "Display Name <a@b.com>" -> { name, address }
export function parseSender(from) {
  const m = /^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/.exec(from || "");
  if (m) return { name: m[1].trim() || m[2], address: m[2] };
  return { name: from || "", address: from || "" };
}
