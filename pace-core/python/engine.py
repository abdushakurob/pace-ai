"""Deterministic scheduling. The model never writes the calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


LEAD = timedelta(minutes=20)
ACTIVE_IDLE_SECONDS = 180
ACTIVITY_FRESH = timedelta(minutes=2)


@dataclass
class Event:
    id: str
    title: str
    start: datetime
    end: datetime
    kind: str
    pace_owned: bool
    related_app: str = ""


@dataclass
class Activity:
    app: str
    idle_seconds: int
    paused: bool
    at: datetime


def apps_match(related: str, current: str) -> bool:
    if not (related or "").strip():
        return True
    left = related.lower().removesuffix(".exe").strip()
    right = (current or "").lower().removesuffix(".exe").strip()
    if not right:
        return False
    return left == right or left in right or right in left


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.astimezone()
    return value


def protected_hit(start: datetime, end: datetime, protected_start: str) -> bool:
    hour, minute = (int(part) for part in protected_start.split(":", 1))
    gate = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if start >= gate:
        return True
    return start < gate < end


def overlaps_fixed(start: datetime, end: datetime, events: list[Event], skip_id: str = "") -> Optional[Event]:
    for event in events:
        if event.kind != "fixed" or event.id == skip_id:
            continue
        if start < event.end and end > event.start:
            return event
    return None


def consider(
    now: datetime,
    events: list[Event],
    activity: Optional[Activity],
    suppressed: set[str],
    protected_start: str = "18:00",
) -> Optional[dict]:
    """One proposal when a flexible block overruns into the next fixed commitment."""
    now = _aware(now)
    if activity is None or activity.paused:
        return None
    if _aware(activity.at) < now - ACTIVITY_FRESH:
        return None
    if activity.idle_seconds >= ACTIVE_IDLE_SECONDS:
        return None

    session = None
    for event in events:
        if event.kind != "flexible":
            continue
        if event.start <= now and event.end < now and apps_match(event.related_app, activity.app):
            if session is None or event.end > session.end:
                session = event
    if session is None:
        return None

    upcoming = [
        event
        for event in events
        if event.kind == "fixed" and event.start > now and event.start.date() == now.date()
    ]
    if not upcoming:
        return None
    nxt = min(upcoming, key=lambda event: event.start)
    issue_key = f"overrun:{session.id}:{nxt.id}:{now.date().isoformat()}"
    if issue_key in suppressed:
        return None

    continue_until = nxt.start - LEAD
    if continue_until < now:
        continue_until = now
    minutes = max(0, int((nxt.start - now).total_seconds() // 60))

    movable = [
        event
        for event in events
        if event.pace_owned and event.kind == "flexible" and event.id != session.id and event.start >= session.end
    ]
    move = None
    if movable:
        review = min(movable, key=lambda event: event.start)
        duration = review.end - review.start
        new_start = review.start + timedelta(days=1)
        new_end = new_start + duration
        conflict = overlaps_fixed(new_start, new_end, events, review.id)
        if conflict is None and not protected_hit(new_start, new_end, protected_start):
            move = {
                "id": review.id,
                "title": review.title,
                "new_start": new_start.isoformat(),
                "new_end": new_end.isoformat(),
            }

    stop_label = continue_until.strftime("%H:%M")
    meeting_label = nxt.start.strftime("%H:%M")
    if move:
        summary = (
            f"Continue {session.title} until {stop_label} and move {move['title']} to tomorrow."
        )
    else:
        summary = f"Continue {session.title} until {stop_label}. Leave fixed commitments where they are."
    detail = (
        f"Your next meeting, {nxt.title}, starts at {meeting_label}. "
        f"You have {minutes} minutes available. "
        f"{session.title} ran past its planned end."
    )
    return {
        "issue_key": issue_key,
        "summary": summary,
        "detail": detail,
        "continue_until": continue_until.isoformat(),
        "session_id": session.id,
        "session_title": session.title,
        "next_fixed_id": nxt.id,
        "next_fixed_title": nxt.title,
        "next_fixed_start": nxt.start.isoformat(),
        "move": move,
    }


def plan_intent(intent: dict, events: list[Event], protected_start: str, now: datetime) -> dict:
    """Turn a validated intent into a calendar patch, or a specific question."""
    now = _aware(now)
    kind = intent.get("type")
    if kind == "what_next":
        upcoming = [event for event in events if event.end > now]
        if not upcoming:
            return {"ok": True, "action": "say", "message": "Nothing else is scheduled today."}
        nxt = min(upcoming, key=lambda event: event.start)
        return {
            "ok": True,
            "action": "say",
            "message": f"Next is {nxt.title} at {nxt.start.strftime('%H:%M')}.",
        }
    if kind == "protect_evening":
        return {
            "ok": True,
            "action": "protect",
            "protected_start": "18:00",
            "message": "Evenings stay protected from 18:00. I will not place flexible work there.",
        }
    if kind == "clarify":
        return {
            "ok": True,
            "action": "say",
            "message": intent.get("question") or "Which flexible block should I move?",
        }
    if kind != "move":
        return {"ok": False, "action": "say", "message": "I can move a flexible block, protect the evening, or say what is next."}

    target = (intent.get("target") or "").strip().lower()
    if not target:
        return {"ok": True, "action": "say", "message": "Which block should I move?"}
    matches = [event for event in events if target in event.title.lower()]
    flexible = [event for event in matches if event.pace_owned and event.kind == "flexible"]
    fixed = [event for event in matches if event.kind == "fixed" or not event.pace_owned]
    if not flexible:
        if fixed:
            return {
                "ok": True,
                "action": "say",
                "message": f"{fixed[0].title} is fixed. I will not move it.",
            }
        return {"ok": True, "action": "say", "message": f"I don't see a flexible block called {intent.get('target')}."}
    if len(flexible) > 1:
        names = ", ".join(event.title for event in flexible)
        return {"ok": True, "action": "say", "message": f"Which one: {names}?"}

    event = flexible[0]
    when = (intent.get("when") or "").strip().lower()
    duration = event.end - event.start
    if when == "tomorrow":
        new_start = event.start + timedelta(days=1)
    else:
        hour, minute = _parse_clock(when)
        if hour is None:
            return {"ok": True, "action": "say", "message": "Tell me tomorrow, or a time like 19:00."}
        new_start = event.start.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if new_start <= now:
            new_start = new_start + timedelta(days=1)
    new_end = new_start + duration
    conflict = overlaps_fixed(new_start, new_end, events, event.id)
    if conflict is not None:
        return {
            "ok": True,
            "action": "say",
            "message": f"That overlaps {conflict.title}, which is fixed. I left the plan as it is.",
        }
    if protected_hit(new_start, new_end, protected_start):
        return {
            "ok": True,
            "action": "say",
            "message": f"That falls in protected time after {protected_start}. I left the plan as it is.",
        }
    return {
        "ok": True,
        "action": "move",
        "message": f"Moved {event.title} to {new_start.strftime('%a %H:%M')}.",
        "move": {
            "id": event.id,
            "title": event.title,
            "new_start": new_start.isoformat(),
            "new_end": new_end.isoformat(),
        },
    }


def _parse_clock(value: str) -> tuple[Optional[int], Optional[int]]:
    text = value.replace(".", "").strip().lower()
    if not text:
        return None, None
    suffix = ""
    if text.endswith("am") or text.endswith("pm"):
        suffix = text[-2:]
        text = text[:-2].strip()
    if ":" in text:
        hour_text, minute_text = text.split(":", 1)
    else:
        hour_text, minute_text = text, "0"
    if not hour_text.isdigit() or not minute_text.isdigit():
        return None, None
    hour = int(hour_text)
    minute = int(minute_text)
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None, None
    return hour, minute
