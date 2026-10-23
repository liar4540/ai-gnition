import { RISK_ORDER } from "./derive.js";

const STATUS_ORDER = ["quarantined", "awaiting_approval", "needs_input", "on_hold", "task_open", "done", "cancelled", "no_action"];
const CATEGORY_ORDER = ["suspicious", "important", "routine"];

const KEY = {
  sender: (e) => (e.from || "").toLowerCase(),
  subject: (e) => (e.subject || "").toLowerCase(),
  received: (e) => e.receivedAt || e.created_at || "",
  category: (e) => CATEGORY_ORDER.indexOf(e.category),
  risk: (e) => RISK_ORDER[e.risk] ?? -1,
  confidence: (e) => e.confidence ?? -1,
  status: (e) => STATUS_ORDER.indexOf(e.status),
};

export function sortEmails(emails, by, dir) {
  const key = KEY[by] || KEY.received;
  const sign = dir === "asc" ? 1 : -1;
  return [...emails].sort((a, b) => {
    const x = key(a);
    const y = key(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sign || a.message_id.localeCompare(b.message_id);
  });
}

export function matchesQuery(email, q) {
  if (!q) return true;
  const hay = [email.from, email.subject, email.task?.title, email.task?.delta, email.category,
    ...email.evidence.map((e) => e.label)].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).every((word) => hay.includes(word));
}
