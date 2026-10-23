"""
Connectivity check for the deployed agent. Read only: it never sends, writes or shows a token.

Each stage has a short timeout and reports ok/error and how long it took, so a failure in
nyp_read_new_mail can be traced to: no internet from the tenant, the Google refresh token
in nyp_google_read, or Gmail itself.
"""
import json
import time
import urllib.error
import urllib.request

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

from nyp_google_auth import access_token

APP_ID = "nyp_google_read"
STAGE_TIMEOUT = 8


def _get_json(url: str, token: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=STAGE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _reach(url: str) -> str:
    """Any HTTP answer (JSON, HTML or an error code) proves the network path works."""
    try:
        with urllib.request.urlopen(url, timeout=STAGE_TIMEOUT) as resp:
            return f"reachable (HTTP {resp.status})"
    except urllib.error.HTTPError as e:
        return f"reachable (HTTP {e.code})"


def _stage(name: str, fn) -> dict:
    start = time.monotonic()
    try:
        detail = fn()
        return {"stage": name, "ok": True, "seconds": round(time.monotonic() - start, 2), "detail": detail}
    except urllib.error.HTTPError as e:
        return {"stage": name, "ok": False, "seconds": round(time.monotonic() - start, 2),
                "detail": f"HTTP {e.code} {e.reason}"}
    except Exception as e:
        return {"stage": name, "ok": False, "seconds": round(time.monotonic() - start, 2),
                "detail": f"{type(e).__name__}: {e}"[:300]}


@tool(permission=ToolPermission.READ_ONLY)
def nyp_net_check() -> dict:
    """
    Check that the Orchestrate tenant can reach Google and Telegram over the internet. Uses no connection or credentials.

    Returns:
        dict: {"stages": [{stage, ok, seconds, detail}]}.
    """
    return {"stages": [_stage("internet_to_google", lambda: _reach("https://www.googleapis.com/discovery/v1/apis?name=gmail")),
                       _stage("internet_to_telegram", lambda: _reach("https://api.telegram.org/"))]}


@tool(permission=ToolPermission.READ_ONLY,
      expected_credentials=[ExpectedCredentials(app_id=APP_ID, type=ConnectionType.KEY_VALUE)])
def nyp_diagnose() -> dict:
    """
    Check Gmail access step by step: internet to Google, the Google token from nyp_google_read, and one Gmail call.

    Returns:
        dict: {"stages": [{stage, ok, seconds, detail}], "verdict": str}.
    """
    stages = [_stage("internet_to_google", lambda: _reach("https://www.googleapis.com/discovery/v1/apis?name=gmail"))]
    holder = {}

    def get_token():
        holder["token"] = access_token(APP_ID)
        return "access token received"

    stages.append(_stage("google_token", get_token))
    if "token" in holder:
        stages.append(_stage("gmail_profile",
                             lambda: f"{_get_json('https://gmail.googleapis.com/gmail/v1/users/me/profile', holder['token']).get('messagesTotal')} messages in mailbox"))

    failed = next((s for s in stages if not s["ok"]), None)
    if failed is None:
        verdict = "All checks passed. Gmail is reachable; if scans still time out, the first scan is doing too much work in one call."
    elif failed["stage"] == "internet_to_google":
        verdict = "The Orchestrate tenant cannot reach Google. Ask the facilitator whether tools may call external APIs."
    elif failed["stage"] == "google_token":
        verdict = ("No Google token. Run scripts/get_google_refresh_token.py read, then scripts/setup_connections.ps1, "
                   "and check the detail above (invalid_grant = sign in again; invalid_client = wrong client id/secret).")
    else:
        verdict = "Token works, but the Gmail call failed (403 = Gmail API not enabled or wrong scope; 401 = token rejected)."
    return {"stages": stages, "verdict": verdict}
