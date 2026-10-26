"""Pace Uno Q bridge: local Gemma inference for a Pace Hub on the same LAN."""
from __future__ import annotations

import json
import os
import secrets

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge

import uno_q_ai

ui = WebUI()
TOKEN = os.environ.get("PACE_UNO_TOKEN", "") or secrets.token_urlsafe(24)


def _signal(name: str) -> None:
    """Send a best-effort cue to the Uno Q LED matrix."""
    try:
        Bridge.notify("pace_signal", name)
    except Exception:
        pass


def _authorized(token: str) -> bool:
    return bool(token) and secrets.compare_digest(token, TOKEN)


def get_state():
    """Shown only to the person configuring their trusted local Pace Hub."""
    return {
        "ok": True,
        "service": "Pace Uno Q AI bridge",
        "model": os.environ.get("PACE_LLM_MODEL", "llamacpp:gemma-3-1b-it-Q4_0"),
        "token": TOKEN,
        "signal": "ready",
    }


def post_ai_plan(text: str = "", now: str = "", commitments: str = "[]", issues: str = "[]", token: str = ""):
    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    if not text.strip():
        return {"ok": False, "error": "A task request is required."}
    try:
        _signal("thinking")
        result = {
            "ok": True,
            "plan": uno_q_ai.plan(text, now, json.loads(commitments), json.loads(issues)),
        }
        _signal("ready")
        return result
    except Exception as exc:
        _signal("error")
        return {"ok": False, "error": str(exc)}


ui.expose_api("GET", "/api/state", get_state)
ui.expose_api("POST", "/api/ai/plan", post_ai_plan)
print("Pace Uno Q bridge ready. Open /api/state once to copy its token into Pace Hub Settings.")
App.run()
