"""Instructions become structured intents. Invalid model output is rejected."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional


TYPES = {"move", "protect_evening", "what_next", "clarify"}
FIELDS = {"type", "target", "when", "question"}
# Gemma 3 1B Q4_0 is the Gemma quant published for Uno Q (722 MB).
# Gemma 4 E2B and E4B are listed for Ventuno Q only.
DEFAULT_MODEL = "llamacpp:gemma-3-1b-it-Q4_0"
_STATUS_CACHE: tuple[float, dict] | None = None

SYSTEM = (
    "You translate a scheduling instruction into JSON and nothing else. "
    "Allowed types: move, protect_evening, what_next, clarify. "
    "Keys only: type, target, when, question. "
    "For move, target is the task name and when is tomorrow or HH:MM. "
    "If the request is ambiguous, type is clarify and question is one specific question. "
    "Never invent a calendar change."
)

_MOVE_TOMORROW = re.compile(r"\bmove\s+(?:the\s+)?(.+?)\s+to\s+tomorrow\b", re.I)
_MOVE_TIME = re.compile(
    r"\bmove\s+(?:the\s+)?(.+?)\s+to\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)
_EVENING = re.compile(r"\b(keep|protect|leave)\b.*\bevening\b", re.I)
_NEXT = re.compile(r"\bwhat\b.*\bnext\b|\bwhat should i do\b|\bwhat do i (?:still )?have\b", re.I)
_REMIND = re.compile(r"\bremind me\b", re.I)
_REMIND_IN = re.compile(r"\bin\s+(\d+)\s+minutes?\b", re.I)
_REMIND_AT = re.compile(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", re.I)
_PREP = re.compile(r"\b(?:prep|prepare)\b", re.I)
_SUMMARY = re.compile(r"\bsummar", re.I)
_DRAFT = re.compile(r"\b(?:draft|reply|write)\b", re.I)


def validate(payload) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    if set(payload) - FIELDS:
        return None
    if payload.get("type") not in TYPES:
        return None
    cleaned = {
        "type": payload["type"],
        "target": _text(payload.get("target")),
        "when": _text(payload.get("when")),
        "question": _text(payload.get("question")),
    }
    if cleaned["type"] == "move" and not cleaned["target"]:
        return None
    if cleaned["type"] == "clarify" and not cleaned["question"]:
        return None
    return cleaned


def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_rules(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {"type": "clarify", "target": None, "when": None, "question": "What should I change?"}
    if _EVENING.search(raw):
        return {"type": "protect_evening", "target": None, "when": None, "question": None}
    if _NEXT.search(raw):
        return {"type": "what_next", "target": None, "when": None, "question": None}
    match = _MOVE_TOMORROW.search(raw)
    if match:
        return {"type": "move", "target": match.group(1).strip(), "when": "tomorrow", "question": None}
    match = _MOVE_TIME.search(raw)
    if match:
        return {
            "type": "move",
            "target": match.group(1).strip(),
            "when": match.group(2).strip(),
            "question": None,
        }
    return {
        "type": "clarify",
        "target": None,
        "when": None,
        "question": "Tell me what to change, what to remember, or what to do with the work you're in.",
    }


def work_kind(text: str) -> Optional[str]:
    raw = text or ""
    if _REMIND.search(raw):
        return "remind"
    if _PREP.search(raw):
        return "prep"
    if _SUMMARY.search(raw):
        return "summarize"
    if _DRAFT.search(raw):
        return "draft"
    return None


def reminder_request(text: str) -> dict:
    raw = (text or "").strip()
    note = _REMIND.sub("", raw, count=1)
    note = re.sub(r"^\s*to\s+", "", note, flags=re.I).strip(" .")
    minutes = _REMIND_IN.search(raw)
    if minutes:
        note = _REMIND_IN.sub("", note).strip(" .")
        return {"in_minutes": int(minutes.group(1)), "at": None, "note": note or "Reminder"}
    clock = _REMIND_AT.search(raw)
    if clock:
        note = _REMIND_AT.sub("", note).strip(" .")
        return {"in_minutes": None, "at": clock.group(1).strip(), "note": note or "Reminder"}
    return {"in_minutes": None, "at": None, "note": note or "Reminder"}


def interpret(text: str, plan_summary: str) -> tuple[dict, str]:
    """Return a valid intent and the source that produced it: model or parser."""
    model_intent = _from_model(text, plan_summary)
    if model_intent is not None:
        return model_intent, "model"
    return parse_rules(text), "parser"


def _from_model(text: str, plan_summary: str) -> Optional[dict]:
    if os.environ.get("PACE_LLM", "auto") == "off":
        return None
    try:
        from arduino.app_bricks.llm import LargeLanguageModel
    except Exception:
        return None
    preferred = os.environ.get("PACE_LLM_MODEL", DEFAULT_MODEL)
    try:
        probe = LargeLanguageModel(system_prompt=SYSTEM, temperature=0.0, max_tokens=180)
        available = list(probe.list_models() or [])
    except Exception:
        return None
    model_id = _pick(available, preferred)
    if not model_id:
        return None
    try:
        llm = LargeLanguageModel(
            model=model_id,
            system_prompt=SYSTEM,
            temperature=0.0,
            max_tokens=180,
        )
        raw = llm.chat(
            "Plan:\n"
            + (plan_summary or "No events.")
            + "\nInstruction:\n"
            + text
            + "\nJSON:"
        )
    except Exception:
        return None
    return validate(extract_json(raw if isinstance(raw, str) else str(raw)))


def _pick(available: list, preferred: str) -> Optional[str]:
    names = [str(item) for item in available]
    if preferred in names:
        return preferred
    preferred_tail = preferred.split(":")[-1].lower()
    for name in names:
        lowered = name.lower()
        if preferred_tail and preferred_tail in lowered:
            return name
        if "qwen" in lowered or "gemma" in lowered or lowered.startswith("llamacpp"):
            return name
    return None


def _text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def model_status() -> dict:
    global _STATUS_CACHE
    if _STATUS_CACHE and time.monotonic() - _STATUS_CACHE[0] < 30:
        return dict(_STATUS_CACHE[1])
    preferred = os.environ.get("PACE_LLM_MODEL", DEFAULT_MODEL)
    status = {
        "preferred": preferred,
        "quant": "Q4_0",
        "ready": False,
        "using": None,
        "note": "Gemma 3 1B Q4_0. The instruction box uses the strict parser until this model answers.",
    }
    if os.environ.get("PACE_LLM", "auto") == "off":
        status["note"] = "Local model is turned off. The strict parser is handling instructions."
        _STATUS_CACHE = (time.monotonic(), status)
        return status
    try:
        from arduino.app_bricks.llm import LargeLanguageModel

        available = list(LargeLanguageModel().list_models() or [])
    except Exception:
        _STATUS_CACHE = (time.monotonic(), status)
        return status
    chosen = _pick([str(item) for item in available], preferred)
    if chosen:
        status["ready"] = True
        status["using"] = chosen
        status["note"] = f"Local quant {chosen} turns instructions into JSON. Invalid JSON is discarded."
    _STATUS_CACHE = (time.monotonic(), status)
    return status
