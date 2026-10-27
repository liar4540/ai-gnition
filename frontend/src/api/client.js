// Thin wrappers around the local API server (frontend/server/index.js).
async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    throw new Error("Cannot reach the dashboard API. Is `npm run dev` running?");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

export const api = {
  state: (refresh = false) => request(`/api/state${refresh ? "?refresh=1" : ""}`),
  // window: null for new mail, or { period } / { start_date, end_date } for older mail
  startScan: (window) => request("/api/scan", { method: "POST", body: JSON.stringify(window || {}) }),
  sync: () => request("/api/sync"),
  scanStatus: (runId) => request(`/api/runs/${encodeURIComponent(runId)}`),
  chat: (message, threadId) =>
    request("/api/chat", { method: "POST", body: JSON.stringify({ message, thread_id: threadId }) }),
  messages: (ids) => request(`/api/messages?ids=${ids.map(encodeURIComponent).join(",")}`),
  owner: () => request("/api/owner"),
  login: (pin) => request("/api/owner/login", { method: "POST", body: JSON.stringify({ pin }) }),
  logout: () => request("/api/owner/logout", { method: "POST", body: "{}" }),
  // decision: "approve" | "reject" | "edit" (with body). Owner only; the agent applies it.
  decide: (approvalId, decision, body) =>
    request(`/api/approvals/${encodeURIComponent(approvalId)}/decision`,
            { method: "POST", body: JSON.stringify({ decision, ...(body !== undefined ? { body } : {}) }) }),
};
