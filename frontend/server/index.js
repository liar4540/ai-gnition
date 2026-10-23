// API server for the web dashboard. Runs on localhost only.
//
// It holds the Orchestrate API key (from the project's root .env) and talks to the deployed
// nyp_inbox_agent through the Orchestrate runtime API (/v1/orchestrate/runs). The browser
// never sees a key or token.
//
//   GET  /api/state         dashboard data: the raw output of the agent's nyp_export_state tool
//   POST /api/scan          start "scan my inbox" (reads mail, asks for approvals on Telegram)
//   GET  /api/runs/:id      status of a scan
//   POST /api/chat          free chat with the agent { message, thread_id? }
//   GET  /api/messages?ids  sender / subject / received date fetched live from Gmail (never stored)
import express from "express";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";

const PORT = Number(process.env.API_PORT || 8787);
const WXO_URL = (process.env.WXO_SERVER_URL || "").replace(/\/+$/, "");
const WXO_KEY = process.env.WXO_API_KEY || "";
const AGENT_ID = process.env.WXO_AGENT_ID || "";
const STATE_TTL_MS = 15_000;
const MESSAGE_TTL_MS = 5 * 60_000;

const filled = (v) => Boolean(v) && !String(v).startsWith("<");
export function configProblems(env = process.env) {
  const missing = [];
  if (!filled(env.WXO_SERVER_URL)) missing.push("WXO_SERVER_URL (root .env)");
  if (!filled(env.WXO_API_KEY)) missing.push("WXO_API_KEY (root .env)");
  if (!filled(env.WXO_AGENT_ID)) missing.push("WXO_AGENT_ID (frontend/server/.env, run scripts/get_web_config.ps1)");
  return missing;
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ---- IBM Cloud IAM token for Orchestrate ------------------------------------------------
let iam = { token: null, expires: 0 };
async function iamToken(force = false) {
  if (!force && iam.token && Date.now() < iam.expires - 60_000) return iam.token;
  const res = await fetch("https://iam.cloud.ibm.com/identity/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ibm:params:oauth:grant-type:apikey", apikey: WXO_KEY }),
  });
  if (!res.ok) throw new HttpError(502, `IBM Cloud rejected the Orchestrate API key (HTTP ${res.status}).`);
  const body = await res.json();
  iam = { token: body.access_token, expires: Date.now() + body.expires_in * 1000 };
  return iam.token;
}

async function orchestrate(method, path, body) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetch(`${WXO_URL}/v1/orchestrate${path}`, {
      method,
      headers: { Authorization: `Bearer ${await iamToken(attempt > 0)}`, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 && attempt === 0) continue; // token expired: refresh once
    const text = await res.text();
    if (!res.ok) {
      let msg = text.slice(0, 300);
      try {
        msg = JSON.parse(text).message || msg;
      } catch {
        /* not JSON */
      }
      throw new HttpError(res.status === 403 ? 403 : 502, `Orchestrate answered ${res.status}: ${msg}`);
    }
    return text ? JSON.parse(text) : {};
  }
  throw new HttpError(502, "Orchestrate kept refusing the token.");
}

// ---- agent runs ---------------------------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function toolOutputs(run) {
  const steps = run?.result?.data?.message?.step_history || [];
  const out = [];
  for (const step of steps) {
    for (const d of step.step_details || []) {
      if (d.type === "tool_response") out.push({ name: d.name, content: d.content });
    }
  }
  return out;
}

function replyText(run) {
  const content = run?.result?.data?.message?.content || [];
  return content.filter((c) => c.response_type === "text" || c.text).map((c) => c.text).join("\n").trim();
}

async function startRun(message, threadId) {
  return orchestrate("POST", "/runs", {
    message: { role: "user", content: message },
    agent_id: AGENT_ID,
    ...(threadId ? { thread_id: threadId } : {}),
  });
}

async function waitForRun(runId, timeoutMs) {
  const until = Date.now() + timeoutMs;
  let delay = 800;
  while (Date.now() < until) {
    const run = await orchestrate("GET", `/runs/${encodeURIComponent(runId)}`);
    if (["completed", "failed", "cancelled"].includes(run.status)) return run;
    await sleep(delay);
    delay = Math.min(delay * 1.5, 3000);
  }
  throw new HttpError(504, "The agent did not finish in time.");
}

function summarise(run) {
  return {
    run_id: run.id,
    thread_id: run.thread_id,
    status: run.status,
    text: replyText(run),
    error: run.status === "failed" ? run.last_error?.message || "The agent run failed." : null,
  };
}

// ---- /api/state: single flight + short cache ----------------------------------------------
let stateCache = { at: 0, data: null };
let stateInFlight = null;

async function loadState() {
  const started = await startRun("export state");
  const run = await waitForRun(started.run_id, 90_000);
  if (run.status !== "completed") throw new HttpError(502, run.last_error?.message || `Agent run ${run.status}.`);
  const out = toolOutputs(run).find((t) => t.name === "nyp_export_state");
  if (!out) throw new HttpError(502, "The agent did not call nyp_export_state. Is the latest agent deployed to live?");
  return typeof out.content === "string" ? JSON.parse(out.content) : out.content;
}

async function getState(force) {
  if (!force && stateCache.data && Date.now() - stateCache.at < STATE_TTL_MS) return stateCache.data;
  if (!stateInFlight) {
    stateInFlight = loadState()
      .then((data) => {
        stateCache = { at: Date.now(), data };
        approvalsPending = (data?.counts?.approvals_pending || 0) > 0;
        return data;
      })
      .finally(() => {
        stateInFlight = null;
      });
  }
  return stateInFlight;
}

// ---- scans --------------------------------------------------------------------------------
// New mail: one "scan my inbox" run. Older mail (a window): the server loops "scan batch:" runs,
// 10 emails each, until nyp_read_mail_range reports nothing left. Progress comes from the tool
// outputs, not from the model's wording.
const scans = new Map(); // scan id -> { status, text, error, progress, finished_at }
const MAX_BATCHES = 40;  // up to ~400 emails per scan
const PERIOD_RE = /^\s*(\d{1,3})\s*(d|days?|w|weeks?|m|months?)\s*$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function scanWindow(body = {}) {
  const period = body.period ? String(body.period).trim() : "";
  const start = body.start_date ? String(body.start_date).trim() : "";
  const end = body.end_date ? String(body.end_date).trim() : "";
  if (!period && !start) return null; // new mail only
  if (period && start) throw new HttpError(400, "Give either a period or a start date, not both.");
  if (period && !PERIOD_RE.test(period)) throw new HttpError(400, "Period must look like '2 weeks' or '3 months'.");
  if (start && !DATE_RE.test(start)) throw new HttpError(400, "Start date must be YYYY-MM-DD.");
  if (end && (!DATE_RE.test(end) || end < start)) throw new HttpError(400, "End date must be YYYY-MM-DD on or after the start date.");
  return period ? `period=${period}` : `start_date=${start}${end ? ` end_date=${end}` : ""}`;
}

function batchOutcome(run) {
  const outs = toolOutputs(run);
  const read = outs
    .filter((t) => t.name === "nyp_read_mail_range")
    .map((t) => {
      try {
        return typeof t.content === "string" ? JSON.parse(t.content) : t.content;
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .pop();
  const parse = (t) => {
    try {
      return typeof t.content === "string" ? JSON.parse(t.content) : t.content;
    } catch {
      return null;
    }
  };
  // Saved = items the batch tool really stored, plus any single saves.
  const saved =
    outs.filter((t) => t.name === "nyp_save_analyses").map(parse).reduce((n, r) => n + (r?.saved || 0), 0) +
    outs.filter((t) => t.name === "nyp_save_analysis").map(parse).filter((r) => r?.ok).length;
  return { read, saved };
}

async function runNewMailScan() {
  const started = await startRun("scan my inbox");
  const run = await waitForRun(started.run_id, 10 * 60_000);
  return summarise(run);
}

async function runBatchedScan(id, window) {
  const progress = { found: 0, analysed: 0, remaining: null, batches: 0 };
  const show = () => scans.set(id, { id, status: "running", progress: { ...progress }, text: "", error: null });
  show();
  let retried = false;
  let analysedHere = 0;
  for (let i = 0; i < MAX_BATCHES; i++) {
    const started = await startRun(`scan batch: ${window}`);
    const run = await waitForRun(started.run_id, 8 * 60_000);
    if (run.status !== "completed") throw new HttpError(502, run.last_error?.message || `Agent run ${run.status}.`);
    const { read, saved } = batchOutcome(run);
    if (!read) throw new HttpError(502, "The agent did not read the batch (nyp_read_mail_range was not called).");
    if (read.error) throw new HttpError(400, read.error);
    analysedHere += saved;
    progress.batches += 1;
    progress.found = read.found;
    progress.remaining = read.remaining_after_this_batch + (read.returned - saved);
    progress.analysed = read.found - progress.remaining;
    show();
    stateCache = { at: 0, data: null };
    if (read.returned === 0) break;                 // nothing left to analyse
    if (saved === 0) {
      if (retried) throw new HttpError(502, "The agent read a batch but did not analyse it, twice in a row.");
      retried = true;
    } else {
      retried = false;
    }
    if (read.remaining_after_this_batch === 0 && saved === read.returned) break;
  }
  const left = progress.remaining || 0;
  return {
    status: "completed",
    progress,
    text: left
      ? `Analysed ${analysedHere} emails; ${left} are left (limit of ${MAX_BATCHES} batches). Run the same scan again to continue.`
      : `Analysed ${analysedHere} emails. All ${progress.found} emails in the window are done.`,
  };
}

async function trackScan(id, window) {
  try {
    const result = window ? await runBatchedScan(id, window) : await runNewMailScan();
    scans.set(id, { ...result, id, error: result.error || null, finished_at: new Date().toISOString() });
  } catch (e) {
    const prev = scans.get(id) || {};
    scans.set(id, { id, status: "failed", text: "", progress: prev.progress, error: e.message, finished_at: new Date().toISOString() });
  } finally {
    stateCache = { at: 0, data: null }; // the next /api/state shows the new results
    approvalsPending = true;              // a scan can create approvals; let the sync look
  }
}

// ---- approval sync --------------------------------------------------------------------------
// Telegram presses are only read when nyp_check_approvals runs. While approvals are waiting,
// run the agent's "check approvals" every 30 s: it records Approve/Reject and executes ONLY what
// was approved on Telegram (nyp_execute keeps all its checks). Nothing is approved here.
const SYNC_MS = 30_000;
let approvalsPending = false;
let syncInFlight = false;
let lastSync = { at: null, text: "", error: null };

async function syncApprovals() {
  if (syncInFlight || !approvalsPending || configProblems().length) return;
  if ([...scans.values()].some((s) => s.status === "running")) return;
  syncInFlight = true;
  try {
    const started = await startRun("check approvals");
    const run = await waitForRun(started.run_id, 4 * 60_000);
    lastSync = { at: new Date().toISOString(), text: replyText(run).slice(0, 500), error: run.status === "completed" ? null : run.status };
    stateCache = { at: 0, data: null };
    await getState(true); // refreshes approvalsPending from the real data
  } catch (e) {
    lastSync = { at: new Date().toISOString(), text: "", error: e.message };
  } finally {
    syncInFlight = false;
  }
}

// ---- Gmail metadata, live and in memory only ----------------------------------------------
let gmail = { token: null, expires: 0 };
const messageCache = new Map(); // id -> { at, value }

async function gmailToken() {
  if (gmail.token && Date.now() < gmail.expires - 60_000) return gmail.token;
  const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_READ_REFRESH_TOKEN } = process.env;
  if (![GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_READ_REFRESH_TOKEN].every(filled)) {
    throw new HttpError(503, "Google read access is not set up in the root .env (GOOGLE_* values).");
  }
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      refresh_token: GOOGLE_READ_REFRESH_TOKEN,
      grant_type: "refresh_token",
    }),
  });
  if (!res.ok) throw new HttpError(502, `Google refused the read token (HTTP ${res.status}).`);
  const body = await res.json();
  gmail = { token: body.access_token, expires: Date.now() + body.expires_in * 1000 };
  return gmail.token;
}

function header(headers, name) {
  return headers.find((h) => h.name.toLowerCase() === name)?.value || "";
}

async function messageMeta(id) {
  const hit = messageCache.get(id);
  if (hit && Date.now() - hit.at < MESSAGE_TTL_MS) return hit.value;
  const url =
    `https://gmail.googleapis.com/gmail/v1/users/me/messages/${encodeURIComponent(id)}` +
    "?format=metadata&metadataHeaders=From&metadataHeaders=Subject";
  const res = await fetch(url, { headers: { Authorization: `Bearer ${await gmailToken()}` } });
  let value;
  if (res.status === 404) {
    value = { id, available: false };
  } else if (!res.ok) {
    throw new HttpError(502, `Gmail answered ${res.status}.`);
  } else {
    const m = await res.json();
    const h = m.payload?.headers || [];
    value = {
      id,
      available: true,
      from: header(h, "from"),
      subject: header(h, "subject"),
      received_at: m.internalDate ? new Date(Number(m.internalDate)).toISOString() : null,
    };
  }
  messageCache.set(id, { at: Date.now(), value });
  return value;
}

// ---- HTTP -----------------------------------------------------------------------------------
const app = express();
app.use(express.json({ limit: "16kb" }));

const handle = (fn) => async (req, res) => {
  const problems = configProblems();
  if (problems.length) {
    res.status(503).json({ error: `Missing configuration: ${problems.join("; ")}` });
    return;
  }
  try {
    await fn(req, res);
  } catch (e) {
    res.status(e.status || 500).json({ error: e.message || "Unexpected error" });
  }
};

app.get("/api/health", (_req, res) => res.json({ ok: true, missing: configProblems() }));

app.get("/api/state", handle(async (req, res) => {
  res.json(await getState(req.query.refresh === "1"));
}));

app.post("/api/scan", handle(async (req, res) => {
  const window = scanWindow(req.body || {});
  const running = [...scans.values()].find((s) => s.status === "running");
  if (running) {
    res.status(409).json({ error: "A scan is already running.", run_id: running.id });
    return;
  }
  const id = randomUUID();
  scans.set(id, { id, status: "running", text: "", error: null,
                  progress: window ? { found: 0, analysed: 0, remaining: null, batches: 0 } : null });
  trackScan(id, window);
  res.status(202).json({ run_id: id, status: "running" });
}));

app.get("/api/sync", (_req, res) => res.json({ ...lastSync, running: syncInFlight, watching: approvalsPending }));

app.get("/api/runs/:id", handle(async (req, res) => {
  const scan = scans.get(req.params.id);
  if (!scan) throw new HttpError(404, "Unknown scan.");
  res.json(scan);
}));

app.post("/api/chat", handle(async (req, res) => {
  const message = String(req.body?.message || "").trim();
  const threadId = req.body?.thread_id ? String(req.body.thread_id) : undefined;
  if (!message) throw new HttpError(400, "Message is empty.");
  if (message.length > 2000) throw new HttpError(400, "Message is too long (max 2000 characters).");
  const started = await startRun(message, threadId);
  const run = await waitForRun(started.run_id, 3 * 60_000);
  stateCache = { at: 0, data: null }; // chat can change data (e.g. "check approvals")
  approvalsPending = true;              // chat can create approvals (e.g. a reminder)
  res.json(summarise(run));
}));

app.get("/api/messages", handle(async (req, res) => {
  const ids = String(req.query.ids || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (ids.length > 100) throw new HttpError(400, "At most 100 ids per request.");
  if (!ids.every((id) => /^[A-Za-z0-9_-]{1,64}$/.test(id))) throw new HttpError(400, "Invalid message id.");
  const results = await Promise.all(ids.map((id) => messageMeta(id).catch((e) => ({ id, available: false, error: e.message }))));
  res.json({ messages: results });
}));

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  setInterval(syncApprovals, SYNC_MS);
  app.listen(PORT, "localhost", () => {
    const problems = configProblems();
    console.log(`API server on http://localhost:${PORT}` + (problems.length ? ` — missing: ${problems.join("; ")}` : " — ready"));
  });
}

export { app, toolOutputs, replyText, batchOutcome };
