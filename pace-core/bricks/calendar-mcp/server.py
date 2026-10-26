"""Pace calendar tools over MCP. Events stay here, not in a provider SDK."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PATH = Path(os.environ.get("CALENDAR_PATH", "/data/calendar.json"))
mcp = FastMCP("Pace Calendar", host="0.0.0.0", port=8080)


def _load() -> list[dict]:
    if not PATH.exists():
        return []
    return json.loads(PATH.read_text(encoding="utf-8"))


def _save(events: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(events, indent=2), encoding="utf-8")


def _event(title, start, end, kind, pace_owned, related_app) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "kind": kind,
        "pace_owned": pace_owned,
        "related_app": related_app,
    }


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


@mcp.tool()
def list_events(time_min: str, time_max: str) -> str:
    """List events that overlap an ISO-8601 window. Returns a JSON array."""
    start = _parse(time_min)
    end = _parse(time_max)
    found = []
    for event in _load():
        event_start = _parse(event["start"])
        event_end = _parse(event["end"])
        if event_start < end and event_end > start:
            found.append(event)
    return json.dumps(found)


@mcp.tool()
def create_event(
    title: str,
    start: str,
    end: str,
    kind: str = "flexible",
    pace_owned: bool = True,
    related_app: str = "",
) -> str:
    """Create a Pace-owned flexible event. Returns the event as JSON."""
    events = _load()
    event = _event(title, _parse(start), _parse(end), "flexible" if pace_owned else kind, bool(pace_owned), related_app)
    events.append(event)
    _save(events)
    return json.dumps(event)


@mcp.tool()
def update_event(event_id: str, start: str, end: str) -> str:
    """Move a Pace-owned event. Refuses events that are not pace_owned. Returns the event as JSON."""
    events = _load()
    for event in events:
        if event["id"] != event_id:
            continue
        if not event.get("pace_owned"):
            raise ValueError(f"{event['title']} is fixed and cannot be moved through Pace.")
        event["start"] = _parse(start).isoformat()
        event["end"] = _parse(end).isoformat()
        _save(events)
        return json.dumps(event)
    raise ValueError(f"No event {event_id}")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
