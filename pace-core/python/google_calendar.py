"""Google Calendar adapter behind Pace's connector boundary."""
from __future__ import annotations

import json
import os
import ssl
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import certifi


def _open(request: urllib.request.Request, timeout: int = 20):
    try:
        return urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where()))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise RuntimeError(f"Google Calendar returned HTTP {exc.code}: {detail[:800] or 'no response body'}") from exc


def _token(refresh_token: str) -> str:
    data = urllib.parse.urlencode({"client_id": os.environ["PACE_GOOGLE_CLIENT_ID"], "client_secret": os.environ["PACE_GOOGLE_CLIENT_SECRET"], "refresh_token": refresh_token, "grant_type": "refresh_token"}).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with _open(request) as response:
        return json.loads(response.read())["access_token"]


def list_today(refresh_token: str) -> list[dict]:
    now = datetime.now().astimezone()
    params = urllib.parse.urlencode({"timeMin": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), "timeMax": (now + timedelta(days=2)).isoformat(), "singleEvents": "true", "orderBy": "startTime"})
    request = urllib.request.Request("https://www.googleapis.com/calendar/v3/calendars/primary/events?" + params, headers={"Authorization": "Bearer " + _token(refresh_token)})
    with _open(request) as response:
        items = json.loads(response.read()).get("items", [])
    return [{"id": item["id"], "title": item.get("summary") or "Untitled", "start": item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"), "end": item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"), "kind": "flexible" if item.get("extendedProperties", {}).get("private", {}).get("pace_owned") == "true" else "fixed", "pace_owned": item.get("extendedProperties", {}).get("private", {}).get("pace_owned") == "true", "related_app": ""} for item in items if item.get("start") and item.get("end")]


def create_pace_block(refresh_token: str, title: str, start: str, end: str) -> dict:
    body = json.dumps({"summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}, "extendedProperties": {"private": {"pace_owned": "true"}}}).encode()
    request = urllib.request.Request("https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates=none", data=body, headers={"Authorization": "Bearer " + _token(refresh_token), "Content-Type": "application/json"})
    with _open(request) as response:
        return json.loads(response.read())


def revoke(refresh_token: str) -> None:
    data = urllib.parse.urlencode({"token": refresh_token}).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/revoke", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with _open(request):
        return


def update_pace_block(refresh_token: str, event_id: str, start: str, end: str) -> dict:
    token = _token(refresh_token)
    base = "https://www.googleapis.com/calendar/v3/calendars/primary/events/" + urllib.parse.quote(event_id, safe="")
    with _open(urllib.request.Request(base, headers={"Authorization": "Bearer " + token})) as response:
        existing = json.loads(response.read())
    if existing.get("extendedProperties", {}).get("private", {}).get("pace_owned") != "true":
        raise ValueError("Pace only updates focus blocks that it created.")
    body = json.dumps({"start": {"dateTime": start}, "end": {"dateTime": end}}).encode()
    request = urllib.request.Request(base + "?sendUpdates=none", data=body, method="PATCH", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with _open(request) as response:
        event = json.loads(response.read())
    return event
