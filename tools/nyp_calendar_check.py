"""
Step 4: calendar clash check through the read-only connection nyp_google_read.

READ ONLY: this file makes GET requests only -- no insert, update or delete.
Every Calendar HTTP call goes through _calendar_call() so tests can replace it.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.connections import ConnectionType, ExpectedCredentials
from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

import nyp_store as store
from nyp_google_auth import access_token

APP_ID = "nyp_google_read"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _calendar_call(params: dict) -> dict:
    """GET the primary calendar's events with the read-only token."""
    token = access_token(APP_ID)
    req = urllib.request.Request(EVENTS_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Calendar API {e.code}: {e.reason}") from None


def conflicts(start: str, end: str) -> dict:
    resp = _calendar_call({"timeMin": start, "timeMax": end, "singleEvents": "true", "orderBy": "startTime"})
    events = []
    for ev in resp.get("items", []):
        if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
            continue
        events.append({"title": ev.get("summary", "(no title)"),
                       "start": ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date"),
                       "end": ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date")})
    return {"clash": bool(events), "events": events}


@tool(permission=ToolPermission.READ_WRITE,   # writes only our own store (clash evidence), never Calendar
      expected_credentials=[ExpectedCredentials(app_id=APP_ID, type=ConnectionType.KEY_VALUE)])
def nyp_calendar_conflicts(start: str, end: str, message_id: Optional[str] = None) -> dict:
    """
    List calendar events overlapping a time window and say whether it clashes.

    Args:
        start (str): Window start, RFC 3339 datetime with timezone, e.g. 2026-10-23T14:00:00+08:00.
        end (str): Window end, RFC 3339 datetime with timezone.
        message_id (str): The email this time came from, so a clash is recorded as evidence; optional.
    Returns:
        dict: {"clash": bool, "events": [{"title", "start", "end"}]}.
    """
    result = conflicts(start, end)
    if result["clash"] and message_id:
        store.add_evidence(message_id, "CALENDAR_CLASH")
    return result
