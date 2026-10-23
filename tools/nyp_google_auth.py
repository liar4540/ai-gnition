"""
Google access tokens from a refresh token kept in a key_value connection (the plan's
fallback for when Orchestrate's own Google sign-in does not work).

Each connection holds: client_id, client_secret, refresh_token. nyp_google_read holds a
read-only refresh token; nyp_google_act (executor only) holds the send/calendar-write one.
Get the refresh tokens once with scripts/get_google_refresh_token.py.

Helper module only: no @tool here, nothing is stored, tokens are never returned to the agent.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from ibm_watsonx_orchestrate.run import connections

TOKEN_URL = "https://oauth2.googleapis.com/token"
_cache = {}   # app_id -> (access_token, expires_at); lives only as long as the tool process


class GoogleNotConfigured(Exception):
    pass


def access_token(app_id: str) -> str:
    cached = _cache.get(app_id)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    try:
        kv = connections.key_value(app_id) or {}
    except Exception:
        raise GoogleNotConfigured(f"connection {app_id} is not set up") from None
    fields = {k: str(kv.get(k, "")).strip() for k in ("client_id", "client_secret", "refresh_token")}
    missing = [k for k, v in fields.items() if not v or v.startswith("<")]
    if missing:
        raise GoogleNotConfigured(f"connection {app_id} is missing {', '.join(missing)}")

    body = urllib.parse.urlencode({**fields, "grant_type": "refresh_token"}).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read().decode("utf-8")).get("error", e.reason)
        except Exception:
            reason = e.reason
        hint = " (sign in again with scripts/get_google_refresh_token.py)" if reason == "invalid_grant" else ""
        raise RuntimeError(f"Google token refresh failed for {app_id}: {reason}{hint}") from None
    token = data["access_token"]
    _cache[app_id] = (token, time.time() + int(data.get("expires_in", 3600)))
    return token
