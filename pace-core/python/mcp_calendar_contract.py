"""Provider-neutral contract for hosted Calendar MCP tools.

Transport clients supply discovered tools; Pace maps their provider-specific names and
schemas to these stable operations before the planner sees them.
"""
from __future__ import annotations


OPERATIONS = {
    "list_events": ("list_events", "list calendar events", "google_calendar_list_events"),
    "create_pace_block": ("create_event", "create calendar event", "google_calendar_create_event"),
    "update_pace_block": ("update_event", "update calendar event", "google_calendar_update_event"),
    "find_free_time": ("free_busy", "get_free_busy", "retrieve_free_busy_calendar_details"),
}


def resolve(operation: str, tools: dict[str, object]) -> object | None:
    """Find a provider tool by normalized MCP name or descriptive suffix."""
    aliases = OPERATIONS.get(operation, ())
    for name, tool in tools.items():
        normalized = name.lower().replace("-", "_").replace(" ", "_")
        if any(alias.replace(" ", "_") in normalized for alias in aliases):
            return tool
    return None


def event_payload(title: str, start: str, end: str) -> dict:
    """Common fields accepted by most Calendar MCP create-event tools."""
    return {"summary": title, "title": title, "start": start, "end": end, "description": "Created by Pace. Pace-owned focus block."}
