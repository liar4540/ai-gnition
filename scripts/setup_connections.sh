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

# Google: key-value connections holding a refresh token each (get them with
# scripts/get_google_refresh_token.py read / act). The tools refresh access tokens themselves.
declare -A GOOGLE_TOKENS=([nyp_google_read]=GOOGLE_READ_REFRESH_TOKEN [nyp_google_act]=GOOGLE_ACT_REFRESH_TOKEN)
for app in nyp_google_read nyp_google_act; do
  token_var="${GOOGLE_TOKENS[$app]}"
  if ! filled GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET "$token_var"; then
    echo "Skipped $app: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / $token_var not filled."
    continue
  fi
  for e in draft live; do   # live = deployed agent and embedded web chat
    orchestrate connections configure -a "$app" --env "$e" --type team --kind key_value
    orchestrate connections set-credentials -a "$app" --env "$e" \
      -e "client_id=$GOOGLE_CLIENT_ID" -e "client_secret=$GOOGLE_CLIENT_SECRET" -e "refresh_token=${!token_var}"
  done
  echo "$app set (draft and live)."
done

if filled TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; then
  for e in draft live; do
    orchestrate connections configure -a nyp_telegram --env "$e" --type team --kind key_value
    orchestrate connections set-credentials -a nyp_telegram --env "$e" \
      -e "bot_token=$TELEGRAM_BOT_TOKEN" -e "allowed_chat_id=$TELEGRAM_CHAT_ID"
  done
  echo "Telegram connection set (draft and live)."
else
  echo "Skipped Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not filled. Approvals will say 'telegram not configured'."
fi
