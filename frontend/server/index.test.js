// Tests for the dashboard server: owner sign-in, the decision endpoint's checks, and the state
// built from the database. Run: npm test   (node --test, no extra packages)
//
// A tiny fake Supabase runs on localhost; every other outside call (IBM IAM, Orchestrate) is
// answered locally with an error, so nothing leaves this machine.
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

const PIN = "246810";
const fakeDb = { approvals: [], web_decisions: [], posted: [] };
let dbServer;
let appServer;
let base;
let mod;

function startFakeSupabase() {
  return new Promise((resolve) => {
    dbServer = http.createServer((req, res) => {
      const url = new URL(req.url, "http://x");
      const table = url.pathname.replace("/rest/v1/", "");
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        res.setHeader("Content-Type", "application/json");
        if (req.method === "POST") {
          fakeDb.posted.push({ table, row: JSON.parse(body) });
          res.statusCode = 201;
          res.end();
          return;
        }
        const eq = [...url.searchParams].filter(([k, v]) => k !== "select" && k !== "order" && k !== "limit" && v.startsWith("eq."));
        const rows = (fakeDb[table] || []).filter((r) => eq.every(([k, v]) => String(r[k]) === v.slice(3)));
        res.end(JSON.stringify(rows));
      });
    });
    dbServer.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${dbServer.address().port}`));
  });
}

before(async () => {
  const dbUrl = await startFakeSupabase();
  Object.assign(process.env, {
    WXO_SERVER_URL: "https://orchestrate.invalid", WXO_API_KEY: "test-key", WXO_AGENT_ID: "agent-1",
    SUPABASE_URL: dbUrl, SUPABASE_SERVICE_KEY: "sb_secret_test", OWNER_PIN: PIN,
    SESSION_SECRET: "a-long-random-test-secret-value",
  });
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) =>
    String(url).startsWith("http://127.0.0.1") ? realFetch(url, opts)
      : Promise.resolve(new Response("{}", { status: 400 }));   // IAM / Orchestrate: offline
  mod = await import("./index.js");
  await new Promise((resolve) => {
    appServer = mod.app.listen(0, "127.0.0.1", resolve);
  });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

after(() => {
  appServer?.close();
  dbServer?.close();
});

const post = (path, body, cookie) =>
  fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json", ...(cookie ? { Cookie: cookie } : {}) },
                       body: JSON.stringify(body) });

async function signIn() {
  const res = await post("/api/owner/login", { pin: PIN });
  assert.equal(res.status, 200);
  const cookie = res.headers.get("set-cookie");
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
  return cookie.split(";")[0];
}

describe("owner sign-in", () => {
  test("wrong PIN is refused, right PIN gives a session", async () => {
    assert.equal((await post("/api/owner/login", { pin: "000000" })).status, 401);
    const cookie = await signIn();
    const me = await (await fetch(base + "/api/owner", { headers: { Cookie: cookie } })).json();
    assert.deepEqual(me, { enabled: true, owner: true, web_actions: true });
    const stranger = await (await fetch(base + "/api/owner")).json();
    assert.equal(stranger.owner, false);
  });

  test("a forged or tampered cookie is not the owner", async () => {
    const cookie = await signIn();
    const [name, value] = cookie.split("=");
    const [expires, sig] = value.split(".");
    for (const forged of [`${name}=${Number(expires) + 1000}.${sig}`, `${name}=${expires}.AAAA`, `${name}=9999999999999.x`]) {
      const me = await (await fetch(base + "/api/owner", { headers: { Cookie: forged } })).json();
      assert.equal(me.owner, false, forged);
    }
  });

  test("checkPin compares the whole PIN", () => {
    assert.equal(mod.checkPin(PIN, PIN), true);
    assert.equal(mod.checkPin(PIN.slice(0, 5), PIN), false);
    assert.equal(mod.checkPin(PIN + "0", PIN), false);
  });
});

describe("decision endpoint", () => {
  test("visitors cannot decide", async () => {
    fakeDb.approvals = [{ approval_id: "aaaaaaaaaaaa", action: "send_reply", decision: "pending" }];
    const res = await post("/api/approvals/aaaaaaaaaaaa/decision", { decision: "approve" });
    assert.equal(res.status, 401);
    assert.equal(fakeDb.posted.length, 0);
  });

  test("the owner's decision is only queued, with checks", async () => {
    const cookie = await signIn();
    fakeDb.posted = [];
    fakeDb.web_decisions = [];
    fakeDb.approvals = [
      { approval_id: "aaaaaaaaaaaa", action: "send_reply", decision: "pending" },
      { approval_id: "bbbbbbbbbbbb", action: "calendar_create", decision: "approved" },
      { approval_id: "cccccccccccc", action: "calendar_create", decision: "pending" },
    ];
    const cases = [
      ["/api/approvals/not-an-id/decision", { decision: "approve" }, 400],
      ["/api/approvals/aaaaaaaaaaaa/decision", { decision: "send_now" }, 400],
      ["/api/approvals/dddddddddddd/decision", { decision: "approve" }, 404],
      ["/api/approvals/bbbbbbbbbbbb/decision", { decision: "reject" }, 409],       // already approved
      ["/api/approvals/cccccccccccc/decision", { decision: "edit", body: "x" }, 400], // only replies
      ["/api/approvals/aaaaaaaaaaaa/decision", { decision: "edit", body: "   " }, 400],
    ];
    for (const [path, body, status] of cases) {
      assert.equal((await post(path, body, cookie)).status, status, `${path} ${JSON.stringify(body)}`);
    }
    assert.equal(fakeDb.posted.length, 0);

    const ok = await post("/api/approvals/aaaaaaaaaaaa/decision", { decision: "edit", body: " New text " }, cookie);
    assert.equal(ok.status, 202);
    assert.deepEqual(fakeDb.posted.map((p) => [p.table, p.row.approval_id, p.row.decision, p.row.body, p.row.status]),
                     [["web_decisions", "aaaaaaaaaaaa", "edit", "New text", "queued"]]);

    fakeDb.web_decisions = [{ id: 1, approval_id: "aaaaaaaaaaaa", status: "queued" }];
    assert.equal((await post("/api/approvals/aaaaaaaaaaaa/decision", { decision: "approve" }, cookie)).status, 409);
  });
});

describe("state from the database", () => {
  test("matches the agent's export: counts, evidence labels, live drafts only", () => {
    const s = mod.buildState({
      analyses: [
        { message_id: "m1", thread_id: "t1", category: "important", risk: "low", confidence: 95, band: "high",
          quarantined: 0, created_at: "1", evidence: '["SENDER_DOMAIN_KNOWN"]' },
        { message_id: "m2", thread_id: "t2", category: "suspicious", risk: "high", confidence: 30, band: "low",
          quarantined: 1, created_at: "2", evidence: '["LOOKALIKE_DOMAIN","MADE_UP_CODE"]' },
        { message_id: "m3", thread_id: null, category: null, risk: "low", quarantined: 0, created_at: "3", evidence: "[]" },
      ],
      tasks: [{ task_id: 1, status: "pending_approval", deadline: "2026-11-05", title: "Reply" },
              { task_id: 2, status: "done", deadline: null, title: "Pay" }],
      approvals: [{ approval_id: "a1", task_id: 1, action: "send_reply", decision: "pending", state: "prepared" },
                  { approval_id: "a2", task_id: 2, action: "calendar_create", decision: "approved", state: "confirmed" }],
      drafts: [{ draft_id: 1, task_id: 1, action: "send_reply", content: '{"body":"Hi"}', content_hash: "h1", deleted: 0 },
               { draft_id: 2, task_id: 2, action: "send_reply", content: null, content_hash: "h2", deleted: 1 }],
      activity: [], webDecisions: [{ id: 1, approval_id: "a1", decision: "edit", body: "secret edit", status: "queued" }],
    });
    assert.equal(s.counts.analysed, 2);                       // the uncategorised row is not counted
    assert.equal(s.counts.quarantined, 1);
    assert.equal(s.counts.tasks_pending, 1);
    assert.equal(s.counts.events_added, 1);
    assert.equal(s.counts.followups_drafted, 2);              // decided drafts still count
    assert.equal(s.counts.deadlines_detected, 1);
    const phish = s.analyses.find((a) => a.message_id === "m2");
    assert.equal(phish.quarantined, true);
    assert.deepEqual(phish.evidence[0], { code: "LOOKALIKE_DOMAIN", label: "Sender domain imitates a known domain", severity: "high" });
    assert.deepEqual(phish.evidence[1], { code: "MADE_UP_CODE", label: "MADE_UP_CODE", severity: "info" });
    assert.deepEqual(s.drafts.map((d) => [d.draft_id, d.payload]), [[1, { body: "Hi" }]]);
    assert.equal(JSON.stringify(s).includes("secret edit"), false);  // edited text never leaves the database
  });
});
