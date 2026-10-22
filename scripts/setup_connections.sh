#!/usr/bin/env bash
# Create the nyp_ connections in Orchestrate and load credentials from .env.
# Anything still empty or a <placeholder> in .env is skipped with a message, so you can
# run this again later once you have the Telegram bot or Google client.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a; source .env; set +a

filled() {  # true when every named variable is set and not a <placeholder>
  for var in "$@"; do
    local val="${!var:-}"
    [[ -z "$val" || "$val" == \<*\> ]] && return 1
  done
  return 0
}

if ! filled WXO_SERVER_URL WXO_API_KEY; then
  echo "WXO_SERVER_URL / WXO_API_KEY not filled in .env; cannot reach Orchestrate." >&2
  exit 1
fi
orchestrate env add -n nyp_env -u "$WXO_SERVER_URL" 2>/dev/null || true
orchestrate env activate nyp_env --api-key "$WXO_API_KEY"

for app in nyp_google_read nyp_google_act nyp_telegram; do
  orchestrate connections import -f "connections/$app.yaml"
done

if filled GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; then
  for app in nyp_google_read nyp_google_act; do
    orchestrate connections set-credentials -a "$app" --env draft \
      --client-id "$GOOGLE_CLIENT_ID" --client-secret "$GOOGLE_CLIENT_SECRET"
  done
  echo "Google connections set. Sign in once for each (read, then act) when Orchestrate asks."
else
  echo "Skipped Google: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not filled."
fi

if filled TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; then
  orchestrate connections set-credentials -a nyp_telegram --env draft \
    --entries "bot_token=$TELEGRAM_BOT_TOKEN,allowed_chat_id=$TELEGRAM_CHAT_ID"
  echo "Telegram connection set."
else
  echo "Skipped Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not filled. Approvals will say 'telegram not configured'."
fi
