# Trustworthy Inbox Agent (team nyp)

An inbox assistant built on IBM watsonx Orchestrate for the AI-GNITION hackathon. It reads a Gmail inbox, flags scams with evidence computed by plain code, turns important emails into tasks and reply drafts, and asks the user on Telegram before it sends an email or writes to Google Calendar. The AI model proposes; code checks; a human approves; only one tool acts.

## For judges: try it

**Live demo:** <PASTE THE SUBMITTED LINK HERE>

Nothing to install and no login. Open the link in any browser, on desktop or phone. The agent is already running on IBM watsonx Orchestrate and reads a **demo Gmail inbox with sample emails**. No personal data, passwords or API keys are shown.

**Main journey (about 3 minutes)**

1. **Scan.** Press **Scan inbox** in the top bar. The agent reads the demo inbox and sorts every email as *important*, *routine* or *suspicious*. Use the **▾** arrow next to the button to scan older emails, for example the last 2 weeks. Progress shows on the button.
2. **Threats.** Open **Threats**. The scam emails are quarantined, each with the exact reasons found by code, for example a look-alike domain, a link pointing somewhere else, a Reply-To mismatch, changed bank details or hidden prompt-injection text. No reply or task is made for them.
3. **Tasks and the "why" card.** Open **Tasks** (or **Inbox**) and click an important email, such as the interview invite. You see the task, its deadline, a reply draft and a calendar event with reminders, and why the agent decided this: evidence, confidence and what happens next.
4. **Your own email (optional).** Send an email from your own address to **inboxshield.demo@gmail.com**, then press **Scan inbox** again to see how it is handled.
5. **Assistant (optional).** Open the **Assistant** and ask, for example, *"What are my open tasks?"*, *"Why was the latest suspicious email flagged?"* or *"Remind me tomorrow at 3pm to submit the report"*.

**About approvals.** Sending a reply or adding a calendar event never happens automatically. Each action sends an approval card to the team's Telegram, and only the owner's chat can press **Approve** or **Reject**. After a press, the buttons disappear, the result is confirmed on Telegram, and the dashboard shows *Approved/Done* or *Rejected* within about 30 seconds. Judges can't press these buttons, so the step is shown in our demo video: **<DEMO VIDEO LINK>**. If we are online during judging, we approve your test emails live.

**If something looks slow.** A scan of older mail takes about 30 to 60 seconds per 8 emails, because each email is checked by code and then by the AI model. The dashboard refreshes on its own.

## Technology Stack

| Layer | Technologies |
|---|---|
| AI model | `gpt-oss-120b` (OpenAI open-weight, served by Groq; default model on our Orchestrate tenant), ReAct style. Classifies emails, writes task titles, deadlines, reply drafts and "what changed". It has no send rights. |
| Agent platform | IBM watsonx Orchestrate (SaaS, jp-tok): native agent `nyp_inbox_agent` with Python tools, built with the watsonx Orchestrate ADK (`ibm-watsonx-orchestrate` 2.19) |
| Frontend | **Inbox Shield** dashboard: React 19, Vite 8, React Router, Material UI 9, react-markdown. Design system from UI UX Pro Max (flat SaaS style, inbox blue + threat red, Plus Jakarta Sans). Dashboard, Inbox (search, category tabs, risk/status filters, sorting, detail drawer), Threats, Tasks, Activity, and an Assistant chat. Telegram bot for approvals (Approve, Reject, tone buttons) |
| Backend | Agent tools: Python 3.12, one `@tool` per file in `tools/`, standard library only. Dashboard API: Express 5 on localhost that calls the deployed agent through the Orchestrate runtime API (`/v1/orchestrate/runs`) and reads the raw output of the read-only `nyp_export_state` tool; senders/subjects are fetched live from Gmail and kept in memory only |
| External APIs | Gmail API v1, Google Calendar API v3, Google OAuth 2.0 (refresh-token flow), Telegram Bot API |
| Database | SQLite (`/tmp/nyp_inbox.db`) behind one module (`tools/nyp_store.py`): `sync_state`, `analyses`, `tasks`, `drafts`, `approvals`, `activity_log`. IDs, labels, evidence codes and hashes only, never email text |
| Security | Only `nyp_execute` can send or write to Calendar, using its own Google connection. Approvals are single-use, expire after 30 minutes and are bound to the SHA-256 hash of the exact content. Only one Telegram chat can approve. Plain-code safety rules (look-alike domain, link and Reply-To mismatch, SPF/DKIM/DMARC, changed bank details, prompt injection). Secrets in Orchestrate key-value connections and git-ignored `.env`; the dashboard API keeps the Orchestrate key server-side and only offers one action (Scan inbox) |
| Testing | Python `unittest` (121 tests, fake Gmail, Calendar, Telegram and Google servers). Manual end-to-end script `scripts/e2e_test.md`. In-app `diagnose` and `net check` |
| Deployment | watsonx Orchestrate ADK CLI via `scripts/deploy.ps1` (`-Live`) and `scripts/setup_connections.ps1`. Web dashboard: `npm start` serves the site and API on one port, shared through a Cloudflare tunnel |
| Dev tools | IBM Bob workspace (ADK docs MCP server, `wxo-builder` skill), VS Code, Git/GitHub |

## Repository Structure

```
agents/nyp_inbox_agent.yaml   agent: model, instructions, tools, tool-call guard
tools/                        one Python tool per file (nyp_store, nyp_read_mail, nyp_safety_check,
                              nyp_calendar_check, nyp_save_analysis, nyp_request_approval,
                              nyp_executor, nyp_chat_views, nyp_diagnose) + nyp_google_auth helper
connections/                  connection definitions (no secrets)
frontend/                     React dashboard + local API server (frontend/server)
scripts/                      deploy, connection setup, Google sign-in, web config, seed emails, e2e test
tests/                        unit tests with fakes
docs/IMPLEMENTATION_PLAN.md   build plan and decisions
```

## Run it yourself (developers)

Judges do not need this section; use the live demo link above. Running your own copy needs your own watsonx Orchestrate, Google Cloud and Telegram credentials.

Prerequisites: Python venv in `venv/` (ADK installed), Node.js 20+, and `.env` filled from `.env.example`.

```powershell
# one-time Google sign-in (test Gmail), then load credentials and deploy
venv\Scripts\python scripts\get_google_refresh_token.py read
venv\Scripts\python scripts\get_google_refresh_token.py act
powershell -ExecutionPolicy Bypass -File scripts\setup_connections.ps1
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Live

# tests
venv\Scripts\python -m unittest discover -s tests -t .
```

### Web dashboard

```powershell
powershell -ExecutionPolicy Bypass -File scripts\get_web_config.ps1   # writes frontend\server\.env (agent id)
cd frontend
npm install
npm run dev                                                           # http://localhost:5173 (development)
```

`npm run dev` starts the page (Vite) and the local API server, which reads the root `.env`. All data comes from the deployed agent; nothing is mocked. The only action button is **Scan inbox**. Approvals happen on Telegram. While the server runs, it checks Telegram every 30 seconds and carries out only what was approved.

### Share the dashboard (demo link)

```powershell
cd frontend
npm run build
npm start                                          # site + API on http://localhost:8787
cloudflared tunnel --url http://localhost:8787     # second terminal: prints the https://….trycloudflare.com link
```

Optional, in `frontend\server\.env`: `DEMO_GMAIL` (address shown in the "How to try this" box) and `DEMO_VIDEO_LINK`. Keep both terminals open and the computer awake while the link is in use. A restart gives a new link. The web chat refuses "reset" and "delete data" requests, so visitors cannot wipe the demo.
