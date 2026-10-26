"""Calendar access goes through MCP tools. There is no direct provider client."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional


LIST_ALIASES = ("list_events", "listevents", "list_calendar_events")
CREATE_ALIASES = ("create_event", "createevent")
UPDATE_ALIASES = ("update_event", "updateevent")

ARG_ALIASES = {
    "time_min": ("time_min", "timeMin", "start", "start_time"),
    "time_max": ("time_max", "timeMax", "end", "end_time"),
    "event_id": ("event_id", "eventId", "id"),
    "title": ("title", "summary", "name"),
    "start": ("start", "start_time", "startTime"),
    "end": ("end", "end_time", "endTime"),
    "kind": ("kind", "event_type"),
    "pace_owned": ("pace_owned", "paceOwned"),
    "related_app": ("related_app", "relatedApp"),
}


class CalendarMCP:
    def __init__(self) -> None:
        self.url = os.environ.get("PACE_CALENDAR_MCP_URL", "http://calendar-mcp:8080/mcp")
        token = os.environ.get("PACE_CALENDAR_MCP_TOKEN") or ""
        self.token = token or None
        self.extra = _parse_extra(os.environ.get("PACE_MCP_ENDPOINTS", ""))
        self.catalog: dict[str, str] = {}
        self.error: Optional[str] = None
        self._tools = {}
        self._mcp = None

    def connect(self) -> bool:
        try:
            from arduino.app_bricks.mcp_client import HTTPEndpoint, MCPClient
        except Exception as exc:
            self.error = f"MCP client unavailable: {exc}"
            return False
        endpoints = [HTTPEndpoint(name="calendar", url=self.url, token=self.token)]
        for name, url, token in self.extra:
            endpoints.append(HTTPEndpoint(name=name, url=url, token=token or None))
        try:
            self._mcp = MCPClient(endpoints=endpoints)
            self.catalog = dict(self._mcp.list_tools() or {})
            self._tools = {tool.name: tool for tool in self._mcp.get_tools()}
            self.error = None
            return True
        except Exception as exc:
            self.error = str(exc)
            self._tools = {}
            return False

    def status(self) -> dict:
        return {
            "url": self.url,
            "connected": bool(self._tools) and self.error is None,
            "error": self.error,
            "tools": sorted(self.catalog),
            "extras": [name for name, _url, _token in self.extra],
        }

    def list_events(self, time_min: str, time_max: str) -> list[dict]:
        tool = self._require(LIST_ALIASES)
        raw = self._call(tool, {"time_min": time_min, "time_max": time_max})
        return [_normalize(item) for item in _as_list(raw)]

    def create_event(self, title: str, start: str, end: str, related_app: str = "") -> dict:
        tool = self._require(CREATE_ALIASES)
        raw = self._call(
            tool,
            {
                "title": title,
                "start": start,
                "end": end,
                "kind": "flexible",
                "pace_owned": True,
                "related_app": related_app,
            },
        )
        data = _as_object(raw)
        return _normalize(data if isinstance(data, dict) else {"title": title, "start": start, "end": end})

    def update_event(self, event_id: str, start: str, end: str) -> dict:
        tool = self._require(UPDATE_ALIASES)
        raw = self._call(tool, {"event_id": event_id, "start": start, "end": end})
        data = _as_object(raw)
        if isinstance(data, dict):
            data.setdefault("id", event_id)
            return _normalize(data)
        return {"id": event_id, "start": start, "end": end, "title": "", "kind": "flexible", "pace_owned": True, "related_app": ""}

    def _require(self, aliases: tuple[str, ...]):
        tool = self._find(aliases)
        if tool is None:
            raise RuntimeError(f"Calendar MCP has no tool matching {', '.join(aliases)}")
        return tool

    def _find(self, aliases: tuple[str, ...]):
        for name, tool in self._tools.items():
            normalized = name.lower().replace("-", "_")
            for alias in aliases:
                if normalized == alias or normalized.endswith("_" + alias):
                    return tool
        return None

    def _call(self, tool, preferred: dict):
        props = set()
        args = getattr(tool, "args", None)
        if isinstance(args, dict):
            props = set(args)
        payload = {}
        if props:
            for logical, value in preferred.items():
                for alias in ARG_ALIASES.get(logical, (logical,)):
                    if alias in props:
                        payload[alias] = value
                        break
        else:
            payload = preferred
        return _invoke(tool, payload)


def _invoke(tool, payload):
    async def call():
        return await tool.ainvoke(payload)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(call())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, call()).result()


def _parse_extra(raw: str) -> list[tuple[str, str, str]]:
    endpoints = []
    for part in (raw or "").split(";"):
        piece = part.strip()
        if not piece:
            continue
        bits = piece.split("|")
        if len(bits) < 2:
            continue
        name, url = bits[0].strip(), bits[1].strip()
        token = bits[2].strip() if len(bits) > 2 else ""
        if name and url:
            endpoints.append((name, url, token))
    return endpoints


def _as_list(raw) -> list:
    data = _unwrap(raw)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and not _is_content_block(item)]
    if isinstance(data, dict):
        for key in ("events", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        if _looks_like_event(data):
            return [data]
    return []


def _as_object(raw):
    data = _unwrap(raw)
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return data if isinstance(data, (dict, list)) else raw


def _unwrap(raw, depth: int = 0):
    if depth > 6 or raw is None:
        return raw
    content = getattr(raw, "content", None)
    if content is not None and not isinstance(raw, (dict, list, str, bytes)):
        return _unwrap(content, depth + 1)
    if isinstance(raw, str):
        text = raw.strip()
        if text[:1] in "[{":
            try:
                return _unwrap(json.loads(text), depth + 1)
            except json.JSONDecodeError:
                return raw
        return raw
    if isinstance(raw, list):
        if raw and all(_is_content_block(item) for item in raw):
            return _unwrap("".join(str(item.get("text") or "") for item in raw), depth + 1)
        return raw
    if isinstance(raw, dict):
        if _is_content_block(raw):
            return _unwrap(raw.get("text") or "", depth + 1)
        if not _looks_like_event(raw):
            for key in ("structuredContent", "structured_content", "content"):
                if key in raw:
                    return _unwrap(raw[key], depth + 1)
        return raw
    return raw


def _is_content_block(item) -> bool:
    return isinstance(item, dict) and "text" in item and not _looks_like_event(item)


def _looks_like_event(item) -> bool:
    return isinstance(item, dict) and any(key in item for key in ("title", "summary", "start", "end"))


def _moment(value) -> str:
    if isinstance(value, dict):
        return value.get("dateTime") or value.get("date") or ""
    return str(value or "")


def _normalize(event: dict) -> dict:
    pace_owned = bool(event.get("pace_owned") or event.get("paceOwned"))
    kind = event.get("kind") or ("flexible" if pace_owned else "fixed")
    if kind not in ("fixed", "flexible"):
        kind = "fixed"
    return {
        "id": str(event.get("id") or event.get("event_id") or ""),
        "title": event.get("title") or event.get("summary") or "Untitled",
        "start": _moment(event.get("start")),
        "end": _moment(event.get("end")),
        "kind": kind,
        "pace_owned": pace_owned,
        "related_app": event.get("related_app") or event.get("relatedApp") or "",
    }
