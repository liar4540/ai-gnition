# Trustworthy Inbox Agent (team nyp)

An inbox assistant built on IBM watsonx Orchestrate for the AI-GNITION hackathon. It reads a Gmail inbox, flags scams with evidence computed by plain code, turns important emails into tasks and reply drafts, and asks the user on Telegram before it sends an email or writes to Google Calendar. The AI model proposes; code checks; a human approves; only one tool acts.

## For judges: try it

**Live demo:** <PASTE THE SUBMITTED LINK HERE>

Nothing to install and no login. Open the link in any browser, on desktop or phone. The agent is already running on IBM watsonx Orchestrate and reads a **demo Gmail inbox with sample emails**. No personal data, passwords or API keys are shown.

**Main journey (about 3 minutes)**

1. **Scan.** Press **Scan inbox** in the top bar. The agent reads the demo inbox and sorts every email as *important*, *routine* or *suspicious*. Use the **▾** arrow next to the button to scan older emails, for example the last 2 weeks. Progress shows on the button.
2. **Threats.** Open **Threats**. The scam emails are quarantined, each with the exact reasons found by code, for example a look-alike domain, a link pointing somewhere else, a Reply-To mismatch, changed bank details or hidden prompt-injection text. No reply or task is made for them.
3. **Tasks and the draft.** Open **Tasks** and click a task, such as the interview invite. The panel shows the task, its deadline and why the agent decided this, plus the **exact draft**: the reply (to, subject, text) and the calendar event with reminders, each waiting for the owner's decision: **Send email / Don't send / Edit** or **Add to calendar / Don't add**. *View the email and its safety evidence* shows the evidence and confidence.
4. **Your own email (optional).** Send an email from your own address to **inboxshield.demo@gmail.com**, then press **Scan inbox** again to see how it is handled.
5. **Assistant (optional).** Open the **Assistant** and ask, for example, *"What are my open tasks?"*, *"Why was the latest suspicious email flagged?"* or *"Remind me tomorrow at 3pm to submit the report"*.

**About approvals.** Sending a reply or adding a calendar event never happens automatically. The owner decides, either on the approval card in the team's Telegram or on the dashboard after **Owner sign in** (a PIN only the owner has). Either way the same checks apply: the approval works once, expires after 30 minutes and covers only the exact text shown. After a decision, the Telegram buttons disappear, the result is confirmed there, and the dashboard shows *Done* or *Rejected* within seconds. Visitors can see every draft but cannot send, so the approval step is shown in our demo video: **<DEMO VIDEO LINK>**. If we are online during judging, we approve your test emails live.

**If something looks slow.** A scan of older mail takes about 30 to 60 seconds per 8 emails, because each email is checked by code and then by the AI model. The dashboard refreshes on its own.

## Technology Stack

| Layer | Technologies |
|---|---|
| AI model | `gpt-oss-120b` (OpenAI open-weight, served by Groq; default model on our Orchestrate tenant), ReAct style. Classifies emails, writes task titles, deadlines, reply drafts and "what changed". It has no send rights. |
| Agent platform | IBM watsonx Orchestrate (SaaS, jp-tok): native agent `nyp_inbox_agent` with Python tools, built with the watsonx Orchestrate ADK (`ibm-watsonx-orchestrate` 2.19) |
| Frontend | **Inbox Shield** dashboard: React 19, Vite 8, React Router, Material UI 9, react-markdown. Design system from UI UX Pro Max (flat SaaS style, inbox blue + threat red, Plus Jakarta Sans). Dashboard, Inbox (search, category tabs, risk/status filters, sorting, detail drawer), Threats, Tasks with a draft panel (Send / Don't send / Edit for the signed-in owner), Activity, and an Assistant chat. Telegram bot for approvals (Approve, Reject, tone buttons) |
| Backend | Agent tools: Python 3.12, one `@tool` per file in `tools/`, standard library only. Dashboard API: Express 5 (Node 22.9+) that reads the store straight from Supabase, calls the deployed agent through the Orchestrate runtime API (`/v1/orchestrate/runs`) for scans and chat, and queues the owner's decisions; senders/subjects are fetched live from Gmail and kept in memory only |
| External APIs | Gmail API v1, Google Calendar API v3, Google OAuth 2.0 (refresh-token flow), Telegram Bot API, Supabase REST API |
| Database | **Supabase (Postgres, Singapore)** behind one module (`tools/nyp_store.py`, HTTPS REST, standard library only); tables in `supabase/schema.sql`: `sync_state`, `analyses`, `tasks`, `drafts`, `approvals`, `activity_log`, `web_decisions`. Row level security on with no policies, so only the secret key can use them. SQLite (`/tmp/nyp_inbox.db`) is the fallback and the test backend. IDs, labels, evidence codes and hashes only, never email text; draft text is deleted after the decision |
| Security | Only `nyp_execute` can send or write to Calendar, using its own Google connection. Approvals are single-use, expire after 30 minutes and are bound to the SHA-256 hash of the exact content. Only the owner can approve: on Telegram (one allowed chat) or on the dashboard after a PIN sign-in (signed HttpOnly, SameSite=Strict cookie; 5 wrong PINs lock sign-in for 15 minutes). The dashboard only queues a decision; the agent's `nyp_check_approvals` applies it with the same checks as a Telegram press, and editing a reply creates a new approval for the new text. Plain-code safety rules (look-alike domain, link and Reply-To mismatch, SPF/DKIM/DMARC, changed bank details, prompt injection). Secrets in Orchestrate key-value connections, Render environment variables and git-ignored `.env`; nothing secret reaches the browser |
| Testing | Python `unittest` (266 tests: every store and approval test runs on both SQLite and a fake Supabase; fake Gmail, Calendar, Telegram and Google). Dashboard server: `npm test` (node:test; owner sign-in, forged cookies, decision checks, database state). Manual end-to-end script `scripts/e2e_test.md`. In-app `diagnose` and `net check` |
| Deployment | watsonx Orchestrate ADK CLI via `scripts/deploy.ps1` (`-Live`) and `scripts/setup_connections.ps1`. Web dashboard on **Render** (Singapore, `render.yaml` blueprint); `npm start` also runs it locally on one port |
| Dev tools | IBM Bob workspace (ADK docs MCP server, `wxo-builder` skill), VS Code, Git/GitHub |

## Repository Structure

```
agents/nyp_inbox_agent.yaml   agent: model, instructions, tools, tool-call guard
tools/                        one Python tool per file (nyp_store, nyp_read_mail, nyp_safety_check,
                              nyp_calendar_check, nyp_save_analysis, nyp_request_approval,
                              nyp_executor, nyp_chat_views, nyp_diagnose) + nyp_google_auth helper
connections/                  connection definitions (no secrets), incl. nyp_db for Supabase
supabase/schema.sql           database tables, run once in the Supabase SQL editor
frontend/                     React dashboard + API server (frontend/server)
render.yaml                   Render blueprint for hosting the dashboard
scripts/                      deploy, connection setup, Google sign-in, web config, seed emails, e2e test
tests/                        unit tests with fakes
docs/IMPLEMENTATION_PLAN.md   build plan and decisions
```

## Run it yourself (developers)

Judges do not need this section; use the live demo link above. Running your own copy needs your own watsonx Orchestrate, Google Cloud and Telegram credentials.

Prerequisites: Python venv in `venv/` (ADK installed), Node.js 22.9+, and `.env` filled from `.env.example`.

**Database (Supabase, once).** Create a project at supabase.com in the **Singapore** region. In **SQL Editor**, run `supabase/schema.sql`. From **Project Settings → API**, copy the project URL and the **secret** (service_role) key into `.env` as `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`. Never use the secret key in a browser. Without these two values everything still works on SQLite, but the dashboard cannot send or reject.

```powershell
# one-time Google sign-in (test Gmail), then load credentials (incl. Supabase) and deploy
venv\Scripts\python scripts\get_google_refresh_token.py read
venv\Scripts\python scripts\get_google_refresh_token.py act
powershell -ExecutionPolicy Bypass -File scripts\setup_connections.ps1
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Live

# tests
venv\Scripts\python -m unittest discover -s tests -t .
cd frontend; npm test
```

If you change the safety rules in `tools/nyp_safety_check.py`, run `venv\Scripts\python scripts\export_rules.py` so the dashboard server's evidence labels (`frontend/server/rules.json`) match. A unit test fails until you do.

### Web dashboard

```powershell
powershell -ExecutionPolicy Bypass -File scripts\get_web_config.ps1   # writes frontend\server\.env (agent id)
cd frontend
npm install
npm run dev                                                           # http://localhost:5173 (development)
```

`npm run dev` starts the page (Vite) and the local API server, which reads the root `.env`. All data comes from Supabase and the deployed agent; nothing is mocked. Set `OWNER_PIN` (6+ digits) and `SESSION_SECRET` (any long random string) in `.env` to enable **Owner sign in**. While the server runs, it checks Telegram every 30 seconds, applies the owner's dashboard decisions straight away, and carries out only what was approved.

### Host the dashboard on Render

1. Push the repository to GitHub. `.env` and `frontend/server/.env` are git-ignored; check they are not in the push.
2. In Render, choose **New → Blueprint** and pick the repository. `render.yaml` creates the `inbox-shield` web service (Singapore, Starter plan, always on).
3. Fill in the secret values when Render asks: the same as in your `.env`, plus `WXO_AGENT_ID` from `frontend\server\.env`. `SESSION_SECRET` is generated for you.
4. Deploy. The dashboard is at `https://inbox-shield.onrender.com` (or the name Render shows). That link never changes.

### Share the dashboard from your own computer

```powershell
cd frontend
npm run build
npm start                                          # site + API on http://localhost:8787
cloudflared tunnel --url http://localhost:8787     # second terminal: prints the https://….trycloudflare.com link
```

Optional, in `frontend\server\.env`: `DEMO_GMAIL` (address shown in the "How to try this" box) and `DEMO_VIDEO_LINK`. Keep both terminals open and the computer awake while the link is in use. A restart gives a new link. The web chat refuses "reset" and "delete data" requests, so visitors cannot wipe the demo.
