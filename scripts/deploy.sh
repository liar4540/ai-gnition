#!/usr/bin/env bash
# Deploy the nyp inbox agent: activate the Orchestrate env, import tools, then the agent.
# Needs WXO_SERVER_URL and WXO_API_KEY in .env (copy .env.example). Run from anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and fill in WXO_SERVER_URL and WXO_API_KEY." >&2
  exit 1
fi
set -a; source .env; set +a

for var in WXO_SERVER_URL WXO_API_KEY; do
  val="${!var:-}"
  if [[ -z "$val" || "$val" == \<*\> ]]; then
    echo "Missing $var in .env (still empty or a <placeholder>). Not deploying." >&2
    exit 1
  fi
done

orchestrate env add -n nyp_env -u "$WXO_SERVER_URL" 2>/dev/null || true
orchestrate env activate nyp_env --api-key "$WXO_API_KEY"

# Each tool file is imported with the whole tools/ folder as package root so it can
# import nyp_store helpers. Only the connections listed for a file are attached to it:
# nyp_google_act (send + calendar write) goes to nyp_executor.py and nowhere else.
declare -A APP_IDS=(
  [nyp_read_mail.py]="nyp_google_read"
  [nyp_calendar_check.py]="nyp_google_read"
  [nyp_chat_views.py]="nyp_google_read"
  [nyp_request_approval.py]="nyp_telegram nyp_google_read"
  [nyp_executor.py]="nyp_google_act"
)

# Tools use only the standard library and the ADK, so no requirements file is needed.
# If you add one, every package in it must be exact-pinned (pkg==x.y.z).
REQS=()
[[ -f tools/requirements.txt ]] && REQS=(-r tools/requirements.txt)

for f in tools/nyp_*.py; do
  name="$(basename "$f")"
  args=(-k python -f "$f" -p tools "${REQS[@]}")
  for app in ${APP_IDS[$name]:-}; do
    args+=(-a "$app")
  done
  echo "Importing $name"
  orchestrate tools import "${args[@]}"
done

orchestrate agents import -f agents/nyp_inbox_agent.yaml
echo "Deployed. Open nyp_inbox_agent in Orchestrate Draft Preview and type: ping"
