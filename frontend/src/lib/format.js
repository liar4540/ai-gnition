const dateTime = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dateOnly = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function formatDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateTime.format(d);
}

// Deadlines are ISO dates ("2026-11-05") or datetimes.
export function formatDeadline(iso) {
  if (!iso) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return dateOnly.format(new Date(`${iso}T00:00:00`));
  return formatDateTime(iso);
}

export function timeAgo(date) {
  if (!date) return "";
  const secs = Math.round((new Date(date).getTime() - Date.now()) / 1000);
  const abs = Math.abs(secs);
  if (abs < 60) return relative.format(Math.round(secs), "second");
  if (abs < 3600) return relative.format(Math.round(secs / 60), "minute");
  if (abs < 86400) return relative.format(Math.round(secs / 3600), "hour");
  return relative.format(Math.round(secs / 86400), "day");
}
