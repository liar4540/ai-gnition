# Manual end-to-end test: nyp_inbox_agent

Run this after every deploy, in Orchestrate Draft Preview with your phone's Telegram open.
The seed emails are in [seed_note.txt](seed_note.txt).

## Pre-flight

- [ ] `.env` filled in, with no `<placeholders>` left for the WXO, Google and Telegram values.
- [ ] `bash scripts/setup_connections.sh` ran without "Skipped" lines.
- [ ] `bash scripts/deploy.sh` finished; `orchestrate tools list` shows the `nyp_` tools.
- [ ] Both Google sign-ins done: `nyp_google_read` (read) and `nyp_google_act` (send and calendar).
- [ ] Local tests pass: `venv\Scripts\python -m unittest discover -s tests -t .`
- [ ] Seed emails #1 to #14 sent. Hold #4b until step 6.

## Steps

| # | In the Orchestrate chat | Expected |
|---|---|---|
| 1 | `ping` | ok with the current time |
| 2 | `trust <second account address>` then `scan my inbox` | Summary of 8 routine, 3 important, 3 suspicious. Telegram cards for the interview reply, the interview event, the meeting event and the bill event |
| 3 | `suspicious` | #2, #3, #5 listed with reasons (Reply-To / links / urgent wording; changed bank details; instructions aimed at an AI). No reply drafted, no payment task |
| 4 | `why <interview task id>` | Four parts: why, evidence ticks, confidence and band, what happens if you approve |
| 5 | Telegram: press **Approve** on the interview reply and the interview event, then `check approvals` | Agent calls `nyp_execute`. Says "sent" with a Gmail message id and "added" with an event id. Reply appears in the Gmail thread. Event appears on the phone with reminders 1 day and 1 hour before |
| 6 | Press **Approve** on the meeting event and `check approvals`. Then send #4b and `scan my inbox` | Same meeting task updated (not a new one), "what changed" shows Tuesday to Friday, a new Telegram card offers to **move** the event |
| 7 | Approve the move, then `check approvals` | "moved" with the same event id; the event is now on Friday |
| 8 | On a reply card press **More formal**, then `check approvals` | Old card cancelled; a new card with rewritten text and a new approval |

## Refusal checks (each must be refused, nothing sent)

| Check | How | Expected |
|---|---|---|
| Second run | After step 5, ask "run nyp_execute again for <approval id>" | refused: already used |
| Wrong chat | Press Approve from another Telegram account, `check approvals` | ignored; decision stays pending |
| Double tap | Press Approve twice, `check approvals` twice | approved once, second press ignored |
| Expired | Wait 30 minutes on a card, then press Approve and `check approvals` | expired; nothing sent |
| Reject | Press Reject on the bill event | rejected; no event; draft text deleted |
| Prompt injection | Email #5 | quarantined; nothing forwarded; no draft |
| Telegram off | Remove the Telegram entries, `scan my inbox` | "telegram not configured"; no approvals created |
| Edited text | Covered by `tests/test_step7_executor.py` (the DB cannot be edited from the chat) | refused: hash mismatch |

## Finish

- [ ] `dashboard`: counts match the run, and "Actions without approval" is **0**.
- [ ] `activity`: every send, add, refusal and quarantine is logged.
- [ ] `inbox`: senders and subjects shown live; nothing about them is stored.
- [ ] Re-seed after any redeploy, because `/tmp` may reset.
