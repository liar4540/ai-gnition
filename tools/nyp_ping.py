from datetime import datetime, timezone

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission


@tool(permission=ToolPermission.READ_ONLY)
def nyp_ping() -> dict:
    """
    Health check for the inbox agent. Returns ok and the current UTC time.

    Returns:
        dict: {"ok": true, "time": "<UTC ISO 8601 timestamp>"}.
    """
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}
