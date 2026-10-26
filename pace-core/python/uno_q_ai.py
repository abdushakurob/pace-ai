"""Structured task planning with the Uno Q local LLM brick."""
from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_MODEL = "llamacpp:gemma-3-1b-it-Q4_0"
SYSTEM = """Turn the user's task request into JSON only. Keys: title, duration_minutes, target_date, preferred_period, rationale.
duration_minutes is an integer from 15 to 240. target_date is YYYY-MM-DD or null. preferred_period is morning, afternoon, evening, or anytime.
Use the supplied current time and calendar. Do not schedule, modify, or claim to create an event."""


def plan(text: str, now: str, commitments: list[dict], issues: list[dict]) -> dict[str, Any]:
    from arduino.app_bricks.llm import LargeLanguageModel

    preferred = os.environ.get("PACE_LLM_MODEL", DEFAULT_MODEL)
    available = [str(item) for item in LargeLanguageModel().list_models() or []]
    # list_models() reports the short runner name (for example
    # ``gemma-3-1b-it-Q4_0``), while the brick constructor needs the configured
    # provider-qualified ID (``llamacpp:gemma-3-1b-it-Q4_0``).
    model_id = preferred
    if not available:
        raise RuntimeError("No Uno Q local model is available. Install the Pace model from app.yaml first.")
    model = LargeLanguageModel(model=model_id, system_prompt=SYSTEM, temperature=0.0, max_tokens=220)
    raw = model.chat(json.dumps({"request": text, "now": now, "commitments": commitments, "available_issues": issues}, ensure_ascii=False) + "\nJSON:")
    try:
        answer = json.loads(str(raw)[str(raw).find("{") : str(raw).rfind("}") + 1])
        duration = int(answer["duration_minutes"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("The Uno Q model did not return a valid task plan. Try wording the task more directly.") from exc
    period = str(answer.get("preferred_period") or "anytime").lower()
    if period not in {"morning", "afternoon", "evening", "anytime"}:
        period = "anytime"
    target_date = answer.get("target_date")
    return {"title": str(answer.get("title") or text).strip()[:160], "duration_minutes": max(15, min(duration, 240)), "target_date": str(target_date) if target_date else None, "preferred_period": period, "rationale": str(answer.get("rationale") or "Uno Q interpreted your request.")[:280]}
