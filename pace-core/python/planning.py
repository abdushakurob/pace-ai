"""Small deterministic bridge from a requested task to a safe focus proposal."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re


def match_issue(text: str, issues: list[dict]) -> dict | None:
    words = {word for word in text.lower().split() if len(word) > 2}
    ranked = [(len(words & set(issue["title"].lower().split())), issue) for issue in issues]
    best = max(ranked, default=(0, None), key=lambda item: item[0])
    return best[1] if best[0] else None


def requested_minutes(text: str, default: int = 60) -> int:
    """Extract a bounded duration from a natural-language capture."""
    match = re.search(r"\b(\d{1,3})\s*(minutes?|mins?|hours?|hrs?|h)\b", text.lower())
    if not match:
        return default
    value = int(match.group(1))
    minutes = value * 60 if match.group(2).startswith(("hour", "hr", "h")) else value
    return max(15, min(minutes, 240))


def first_gap(events: list[dict], minutes: int = 60, target_date: str | None = None, preferred_period: str = "anytime") -> tuple[datetime, datetime]:
    """Find a policy-allowed gap. AI supplies intent; it never chooses the final time."""
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    try:
        requested_day = date.fromisoformat(target_date) if target_date else now.date()
    except ValueError:
        requested_day = now.date()
    if requested_day < now.date():
        requested_day = now.date()
    duration = timedelta(minutes=minutes)
    starts = {"morning": 8, "afternoon": 13, "evening": 18, "anytime": 8}
    ends = {"morning": 12, "afternoon": 18, "evening": 22, "anytime": 22}
    fixed = [(datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])) for event in events if event.get("kind") == "fixed"]
    for offset in range(7):
        day = requested_day + timedelta(days=offset)
        start = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=starts.get(preferred_period, 8))
        end_of_window = start.replace(hour=ends.get(preferred_period, 22))
        if day == now.date():
            start = max(start, now + timedelta(minutes=5))
        if start + duration > end_of_window:
            continue
        day_events = sorted(((event_start, event_end) for event_start, event_end in fixed if event_start.date() == day), key=lambda item: item[0])
        candidate = start
        for event_start, event_end in day_events:
            if event_end <= candidate:
                continue
            if candidate + duration <= event_start:
                return candidate, candidate + duration
            candidate = max(candidate, event_end)
            if candidate + duration > end_of_window:
                break
        if candidate + duration <= end_of_window:
            return candidate, candidate + duration
    raise ValueError("Pace could not find a suitable time in the next seven days.")
