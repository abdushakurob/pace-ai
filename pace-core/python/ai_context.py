"""Bounded AI context analysis for the standalone Pace Hub.

The model may describe the user's current work context. It never receives authority
to edit a calendar, task tracker, or Pace policy.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import certifi
import store


SYSTEM_PROMPT = """You classify whether observed computer activity relates to a planned work block.
Return JSON only with these exact keys:
activity, related, confidence, evidence.
activity is a short description. related is true or false. confidence is a number from 0 to 1.
evidence is an array of up to three short observations. Do not suggest or perform actions."""

PLANNING_PROMPT = """You turn a user's work request into a planning intent. Return JSON only with these exact keys:
title, duration_minutes, target_date, preferred_period, rationale.
target_date is an ISO YYYY-MM-DD date or null. preferred_period is one of morning, afternoon, evening, anytime.
Infer duration and timing from the request and calendar context. Do not claim to book, edit, or create anything."""


def configured() -> dict[str, str | bool]:
    mode = os.environ.get("PACE_AI_MODE") or store.get_setting("ai_mode", "off") or "off"
    base_url = os.environ.get("PACE_AI_BASE_URL") or store.get_setting("ai_base_url", "") or ""
    model = os.environ.get("PACE_AI_MODEL") or store.get_setting("ai_model", "") or ""
    api_key = os.environ.get("PACE_AI_API_KEY") or store.get_setting("ai_api_key", "") or ""
    mode = mode.strip().lower()
    return {
        "mode": mode,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "has_api_key": bool(api_key),
        "ready": (mode == "openai-compatible" and bool(base_url) and bool(model)) or (mode == "uno-q" and bool(base_url) and bool(api_key)),
    }


def _chat_json(system_prompt: str, input_data: dict[str, Any]) -> dict[str, Any]:
    config = configured()
    if not config["ready"]:
        raise RuntimeError("Connect an AI model in Settings before asking Pace to plan a task.")
    payload = {
        "model": config["model"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(input_data, ensure_ascii=False)},
        ],
    }
    api_key = os.environ.get("PACE_AI_API_KEY") or store.get_setting("ai_api_key", "") or ""
    request = urllib.request.Request(
        str(config["base_url"]) + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
        method="POST",
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            result = json.loads(response.read().decode("utf-8"))
        return json.loads(result["choices"][0]["message"]["content"])
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Pace could not reach the configured AI endpoint: {exc.reason}") from exc
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The AI endpoint did not return the expected JSON result.") from exc


def plan_task(text: str, now: str, commitments: list[dict], issues: list[dict]) -> dict[str, Any]:
    """Ask the selected model for intent. Policy chooses the final available slot."""
    config = configured()
    if config["mode"] == "uno-q":
        api_key = os.environ.get("PACE_AI_API_KEY") or store.get_setting("ai_api_key", "") or ""
        # Arduino WebUI maps exposed function arguments from the query string.
        # Keep this transport detail inside the adapter; callers use normal data.
        params = urllib.parse.urlencode({
            "text": text,
            "now": now,
            "commitments": json.dumps(commitments),
            "issues": json.dumps(issues),
            "token": api_key,
        })
        request = urllib.request.Request(str(config["base_url"]) + "/api/ai/plan?" + params, method="POST")
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=60, context=context) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "Uno Q could not plan this task."))
            answer = result["plan"]
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Pace could not reach the Uno Q at {config['base_url']}: {exc.reason}") from exc
    else:
        answer = _chat_json(PLANNING_PROMPT, {"request": text, "now": now, "commitments": commitments, "available_issues": issues})
    duration = answer.get("duration_minutes")
    if not isinstance(duration, (int, float)):
        raise RuntimeError("The AI plan did not include a duration.")
    period = str(answer.get("preferred_period") or "anytime").lower()
    if period not in {"morning", "afternoon", "evening", "anytime"}:
        period = "anytime"
    target_date = answer.get("target_date")
    if target_date is not None:
        target_date = str(target_date)
    return {
        "title": str(answer.get("title") or text).strip()[:160],
        "duration_minutes": max(15, min(int(duration), 240)),
        "target_date": target_date,
        "preferred_period": period,
        "rationale": str(answer.get("rationale") or "Pace interpreted your request.")[:280],
    }


def analyse(observation: dict[str, Any], planned_block: dict[str, Any] | None) -> dict[str, Any]:
    """Analyse one explicit observation through a compatible local or hosted endpoint."""
    answer = _chat_json(SYSTEM_PROMPT, {"planned_block": planned_block, "observation": observation})

    related = answer.get("related")
    confidence = answer.get("confidence")
    evidence = answer.get("evidence")
    if not isinstance(related, bool) or not isinstance(confidence, (int, float)) or not isinstance(evidence, list):
        raise RuntimeError("The AI context result failed validation.")
    return {
        "activity": str(answer.get("activity") or "Unknown activity")[:240],
        "related": related,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "evidence": [str(item)[:240] for item in evidence[:3]],
    }
