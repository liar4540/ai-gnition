// API server for the web dashboard. Runs on localhost by default; HOST=0.0.0.0 to host it.
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
//   GET  /api/owner         is owner sign-in set up, and is this browser signed in
//   POST /api/owner/login   { pin } -> signed, HttpOnly cookie (8 hours)
//   POST /api/owner/logout
//   POST /api/approvals/:id/decision  { decision: approve | reject | edit, body? }  owner only
//
// With SUPABASE_URL + SUPABASE_SERVICE_KEY set, /api/state reads the database directly (fast,
// no agent run) and the owner can decide on the dashboard. The server only QUEUES a decision in
// web_decisions; the agent's nyp_check_approvals applies it with the same checks as a Telegram
// press, and only nyp_execute (with its own Google connection) sends or writes to Calendar.
import express from "express";
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { pathToFileURL, fileURLToPath } from "node:url";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const PORT = Number(process.env.PORT || process.env.API_PORT || 8787);
const SERVER_DIR = path.dirname(fileURLToPath(import.meta.url));
const DB_URL = (process.env.SUPABASE_URL || "").replace(/\/+$/, "");
const DB_KEY = process.env.SUPABASE_SERVICE_KEY || "";
const OWNER_PIN = process.env.OWNER_PIN || "";
const SESSION_SECRET = process.env.SESSION_SECRET || "";
// evidence code -> { label, severity }; written by scripts/export_rules.py from the safety rules
const RULES = JSON.parse(readFileSync(path.join(SERVER_DIR, "rules.json"), "utf-8"));
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
export const dbConfigured = (env = process.env) => filled(env.SUPABASE_URL) && filled(env.SUPABASE_SERVICE_KEY);
export const ownerEnabled = (env = process.env) =>
  filled(env.OWNER_PIN) && String(env.OWNER_PIN).length >= 6 && filled(env.SESSION_SECRET) && String(env.SESSION_SECRET).length >= 16;

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

// ---- Supabase (PostgREST over HTTPS), server side only ---------------------------------------
async function db(method, pathAndQuery, body, prefer) {
  const headers = { apikey: DB_KEY, "Content-Type": "application/json", Accept: "application/json" };
  if (DB_KEY.startsWith("eyJ")) headers.Authorization = `Bearer ${DB_KEY}`; // legacy service_role JWT
  if (prefer) headers.Prefer = prefer;
  const res = await fetch(`${DB_URL}/rest/v1/${pathAndQuery}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new HttpError(502, `Supabase answered ${res.status}: ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

const evidenceItems = (codes) =>
  codes.map((code) => ({ code, label: RULES[code]?.label || code, severity: RULES[code]?.severity || "info" }));

// The same JSON as the agent's nyp_export_state (tools/nyp_chat_views.py), read straight from
// the database. Keep the two in step.
export function buildState({ analyses, tasks, approvals, drafts, activity, webDecisions }, limit = 200) {
  const parse = (s, fallback) => {
    try {
      return s ? JSON.parse(s) : fallback;
    } catch {
      return fallback;
    }
  };
  const allAnalyses = analyses.map((a) => ({ ...a, evidence: parse(a.evidence, []) }));
  const analysed = allAnalyses.filter((a) => a.category);
  const n = (rows, test) => rows.filter(test).length;
  const outAnalyses = analysed.slice(-limit).map((a) => ({
    message_id: a.message_id, thread_id: a.thread_id, category: a.category, risk: a.risk,
    confidence: a.confidence, band: a.band, quarantined: Boolean(a.quarantined), created_at: a.created_at,
    evidence: evidenceItems(a.evidence),
  }));
  const counts = {
    analysed: outAnalyses.length,
    routine: n(analysed, (a) => a.category === "routine"),
    important: n(analysed, (a) => a.category === "important"),
    suspicious: n(analysed, (a) => a.category === "suspicious"),
    quarantined: n(allAnalyses, (a) => a.quarantined),
    tasks_pending: n(tasks, (t) => ["open", "pending_approval", "on_hold"].includes(t.status)),
    tasks_done: n(tasks, (t) => t.status === "done"),
    approvals_total: approvals.length,
    approvals_pending: n(approvals, (a) => a.decision === "pending"),
    approvals_approved: n(approvals, (a) => a.decision === "approved"),
    approvals_rejected: n(approvals, (a) => a.decision === "rejected"),
    approvals_cancelled: n(approvals, (a) => ["cancelled", "expired"].includes(a.decision)),
    actions_without_approval: n(approvals, (a) => a.state === "confirmed" && a.decision !== "approved"),
    deadlines_detected: n(tasks, (t) => t.deadline !== null && t.deadline !== undefined),
    events_added: n(approvals, (a) => ["calendar_create", "calendar_move"].includes(a.action) && a.state === "confirmed"),
    replies_sent: n(approvals, (a) => a.action === "send_reply" && a.state === "confirmed"),
    followups_drafted: new Set(drafts.filter((d) => d.action === "send_reply").map((d) => d.task_id)).size,
  };
  const taskFields = ["task_id", "thread_id", "source_message_ids", "title", "deadline", "status", "confidence",
    "reason", "delta", "calendar_event_id", "created_at", "updated_at"];
  const approvalFields = ["approval_id", "task_id", "action", "content_hash", "decision", "state", "expires_at",
    "used", "decided_at", "result_id"];
  const pick = (row, fields) => Object.fromEntries(fields.map((f) => [f, row[f] ?? null]));
  return {
    generated_at: new Date().toISOString(),
    backend: "supabase",
    counts,
    analyses: outAnalyses,
    tasks: tasks.slice(-limit).map((t) => pick(t, taskFields)),
    approvals: approvals.slice(-limit).map((a) => pick(a, approvalFields)),
    drafts: drafts
      .filter((d) => !d.deleted && d.content)
      .map((d) => ({ draft_id: d.draft_id, task_id: d.task_id, action: d.action, content_hash: d.content_hash,
                     payload: parse(d.content, null) }))
      .filter((d) => d.payload),
    activity: activity.slice(0, limit),
    // the owner's dashboard decisions, without any edited text
    web_decisions: (webDecisions || []).map((w) => pick(w, ["id", "approval_id", "decision", "status", "result",
      "created_at", "applied_at"])),
  };
}

async function loadStateFromDb() {
  const [analyses, tasks, approvals, drafts, activity, webDecisions] = await Promise.all([
    db("GET", "analyses?select=*&order=created_at.asc"),
    db("GET", "tasks?select=*&order=task_id.asc"),
    db("GET", "approvals?select=*&order=expires_at.asc"),
    db("GET", "drafts?select=draft_id,task_id,action,content,content_hash,deleted&order=draft_id.asc"),
    db("GET", "activity_log?select=*&order=id.desc&limit=200"),
    db("GET", "web_decisions?select=id,approval_id,decision,status,result,created_at,applied_at&order=id.desc&limit=50"),
  ]);
  return buildState({ analyses, tasks, approvals, drafts, activity, webDecisions });
}

async function getState(force) {
  const ttl = dbConfigured() ? 3_000 : STATE_TTL_MS; // the database is cheap to read; the agent is not
  if (!force && stateCache.data && Date.now() - stateCache.at < ttl) return stateCache.data;
  if (!stateInFlight) {
    stateInFlight = (dbConfigured() ? loadStateFromDb() : loadState())
      .then((data) => {
        stateCache = { at: Date.now(), data };
        approvalsPending = (data?.counts?.approvals_pending || 0) > 0 ||
          (data?.web_decisions || []).some((w) => w.status === "queued");
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
let syncAgain = false;

// Run "check approvals" now (an owner decision is waiting), or right after the one in flight.
function requestSync() {
  approvalsPending = true;
  if (syncInFlight) {
    syncAgain = true;
    return;
  }
  syncApprovals(true);
}

async function syncApprovals(force = false) {
  if (syncInFlight || configProblems().length) return;
  if (!force && (!approvalsPending || [...scans.values()].some((s) => s.status === "running"))) return;
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
    if (syncAgain) {
      syncAgain = false;
      setTimeout(() => syncApprovals(true), 0);
    }
  }
}

// ---- owner sign-in ---------------------------------------------------------------------------
// One owner, one PIN (OWNER_PIN, 6+ characters). The session is a signed expiry time in an
// HttpOnly, SameSite=Strict cookie, so other sites cannot use it and page scripts cannot read it.
const OWNER_COOKIE = "nyp_owner";
const SESSION_MS = 8 * 60 * 60_000;
const LOGIN_WINDOW_MS = 15 * 60_000;
const LOGIN_MAX_FAILS = 5;
const loginFails = new Map(); // ip -> [timestamps]

const sha256 = (s) => createHash("sha256").update(String(s)).digest();
const sign = (value) => createHmac("sha256", SESSION_SECRET).update(value).digest("base64url");

function safeEqual(a, b) {
  const x = Buffer.from(String(a));
  const y = Buffer.from(String(b));
  return x.length === y.length && timingSafeEqual(x, y);
}

export function checkPin(pin, expected = OWNER_PIN) {
  return timingSafeEqual(sha256(pin), sha256(expected)); // equal-length digests: no length leak
}

function readCookie(req, name) {
  for (const part of String(req.headers.cookie || "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return "";
}

function isOwner(req) {
  if (!ownerEnabled()) return false;
  const [expires, sig] = readCookie(req, OWNER_COOKIE).split(".");
  return Boolean(expires && sig) && safeEqual(sig, sign(expires)) && Number(expires) > Date.now();
}

function setOwnerCookie(req, res, value, maxAgeSeconds) {
  res.setHeader("Set-Cookie", `${OWNER_COOKIE}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAgeSeconds}` +
    (req.secure ? "; Secure" : ""));
}

function tooManyFails(ip, now = Date.now()) {
  const recent = (loginFails.get(ip) || []).filter((t) => now - t < LOGIN_WINDOW_MS);
  loginFails.set(ip, recent);
  return recent.length >= LOGIN_MAX_FAILS;
}

function requireOwner(req) {
  if (!ownerEnabled()) throw new HttpError(503, "Owner sign-in is not set up on this server (OWNER_PIN, SESSION_SECRET).");
  if (!isOwner(req)) throw new HttpError(401, "Sign in as the owner to send or reject from the dashboard.");
  if (!dbConfigured()) throw new HttpError(503, "Deciding on the dashboard needs the Supabase database (SUPABASE_URL, SUPABASE_SERVICE_KEY).");
}

const APPROVAL_ID_RE = /^[a-f0-9]{12}$/;
const DECISIONS = ["approve", "reject", "edit"];
const MAX_REPLY_CHARS = 5000;

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
app.set("trust proxy", 1); // hosts (Render) put one proxy in front: req.secure / req.ip are the visitor's
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

app.get("/api/config", (_req, res) => res.json({
  demo_email: process.env.DEMO_GMAIL || "",
  video_link: process.env.DEMO_VIDEO_LINK || "",
}));

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

app.get("/api/owner", (req, res) => res.json({ enabled: ownerEnabled(), owner: isOwner(req), web_actions: dbConfigured() }));

app.post("/api/owner/login", (req, res) => {
  if (!ownerEnabled()) {
    res.status(503).json({ error: "Owner sign-in is not set up on this server." });
    return;
  }
  if (tooManyFails(req.ip)) {
    res.status(429).json({ error: "Too many wrong PINs. Try again in 15 minutes." });
    return;
  }
  if (!checkPin(String(req.body?.pin ?? ""))) {
    loginFails.get(req.ip).push(Date.now());
    res.status(401).json({ error: "Wrong PIN." });
    return;
  }
  loginFails.delete(req.ip);
  const expires = String(Date.now() + SESSION_MS);
  setOwnerCookie(req, res, `${expires}.${sign(expires)}`, SESSION_MS / 1000);
  res.json({ owner: true });
});

app.post("/api/owner/logout", (req, res) => {
  setOwnerCookie(req, res, "", 0);
  res.json({ owner: false });
});

app.post("/api/approvals/:id/decision", handle(async (req, res) => {
  requireOwner(req);
  const id = String(req.params.id);
  const decision = String(req.body?.decision || "");
  const body = decision === "edit" ? String(req.body?.body ?? "").trim() : null;
  if (!APPROVAL_ID_RE.test(id)) throw new HttpError(400, "Invalid approval id.");
  if (!DECISIONS.includes(decision)) throw new HttpError(400, "Decision must be approve, reject or edit.");
  if (decision === "edit" && (!body || body.length > MAX_REPLY_CHARS)) {
    throw new HttpError(400, `The edited reply must be 1 to ${MAX_REPLY_CHARS} characters.`);
  }
  const [approval] = await db("GET", `approvals?approval_id=eq.${id}&select=approval_id,action,decision`);
  if (!approval) throw new HttpError(404, "Unknown approval.");
  if (!["pending", "expired"].includes(approval.decision)) {
    throw new HttpError(409, `This request is already ${approval.decision}.`);
  }
  if (decision === "edit" && approval.action !== "send_reply") throw new HttpError(400, "Only a reply can be edited.");
  const queued = await db("GET", `web_decisions?approval_id=eq.${id}&status=eq.queued&select=id`);
  if (queued.length) throw new HttpError(409, "This request is already being processed.");
  // Only queued here; nyp_check_approvals applies it with every check, nyp_execute acts.
  await db("POST", "web_decisions", { approval_id: id, decision, body, status: "queued",
                                      created_at: new Date().toISOString() }, "return=minimal");
  stateCache = { at: 0, data: null };
  requestSync();
  res.status(202).json({ queued: true, decision });
}));

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
  // The web chat may be shared with visitors: never let it wipe the store (use Orchestrate chat).
  if (/\breset\b|nyp_store_reset|\b(delete|wipe|erase|clear)\b.*\b(data|store|tasks|analys[ei]s)\b/i.test(message)) {
    throw new HttpError(403, "Resetting or deleting data is turned off in the web chat.");
  }
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

// Serve the built dashboard (npm run build -> ../dist) from the same port, for hosting.
const DIST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");
if (existsSync(DIST)) {
  app.use(express.static(DIST));
  app.use((req, res, next) => {
    if (req.method !== "GET" || req.path.startsWith("/api")) return next();
    res.sendFile(path.join(DIST, "index.html"));
  });
}

const HOST = process.env.HOST || "localhost";

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  setInterval(syncApprovals, SYNC_MS);
  app.listen(PORT, HOST, () => {
    const problems = configProblems();
    console.log(`API server on http://localhost:${PORT}` + (problems.length ? ` — missing: ${problems.join("; ")}` : " — ready"));
  });
}

export { app, toolOutputs, replyText, batchOutcome };
