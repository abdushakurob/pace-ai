"""Standalone Pace Hub.

Run with: python hub.py
Then open http://127.0.0.1:7462.  This has no Arduino runtime dependency.
"""

from __future__ import annotations

import json
import mimetypes
import os
import ssl
import socket
import uuid
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from http.cookies import SimpleCookie
import secrets
import urllib.request
import urllib.error
import qrcode
import qrcode.image.svg

try:
    import certifi
except ImportError:
    certifi = None

import ai_context
import google_calendar
import linear
import planning
import store


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
HOST = os.environ.get("PACE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PACE_PORT", "7462"))
OAUTH_CALLBACK_BASE = os.environ.get("PACE_OAUTH_CALLBACK_BASE", f"http://127.0.0.1:{PORT}").rstrip("/")


def _share_url() -> str:
    explicit = os.environ.get("PACE_SHARE_URL", "").rstrip("/")
    if explicit:
        base = explicit
    elif HOST in {"127.0.0.1", "localhost"}:
        base = f"http://127.0.0.1:{PORT}/app"
    else:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                base = f"http://{probe.getsockname()[0]}:{PORT}/app"
        except OSError:
            base = f"http://127.0.0.1:{PORT}/app"
    access = os.environ.get("PACE_ACCESS_TOKEN", "")
    return base + ("?" + urlencode({"access": access}) if access else "")


def _https_context() -> ssl.SSLContext:
    """Use certifi's current CA bundle when Pace runs under a Python that lacks OS roots."""
    return ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _current_block() -> dict | None:
    now = datetime.now().astimezone()
    for event in store.commitments():
        try:
            start = datetime.fromisoformat(event["start"])
            end = datetime.fromisoformat(event["end"])
        except ValueError:
            continue
        if start <= now < end:
            return event
    return None


def _analyse_activity_if_needed(activity: dict) -> None:
    """Keep analysis bounded: only while a focus block is active and context changed."""
    if activity["paused"] or not ai_context.configured()["ready"]:
        return
    block = _current_block()
    if block is None:
        return
    previous = store.latest_context()
    same_window = previous and previous["observation"].get("window") == activity["window"]
    if same_window:
        try:
            previous_at = datetime.fromisoformat(previous["at"])
            if datetime.now().astimezone() - previous_at < timedelta(minutes=2):
                return
        except (TypeError, ValueError):
            return
    observation = {
        "app": activity["app"],
        "window": activity["window"],
        "captured_at": _now(),
    }
    try:
        store.record_context(observation, ai_context.analyse(observation, block))
    except RuntimeError:
        # A transient model failure must not interrupt desktop observation.
        return


def state() -> dict:
    now = datetime.now().astimezone()
    activity = store.latest_activity()
    block = _current_block()
    return {
        "now": now.isoformat(),
        "greeting": "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening",
        "date": now.strftime("%A %d %B").replace(" 0", " "),
        "timeline": store.commitments(),
        "activity": activity,
        "current_block": block,
        "context": store.latest_context(),
        "ai": ai_context.configured(),
        "calendar": {"connected": False, "note": "Connect a calendar from Pace."},
        "messages": store.messages(),
        "reminders": store.open_reminders(),
        "suggestion": store.open_proposal(),
        # The standalone Hub opens directly into the day view.  Connections are
        # intentionally configured through the Hub APIs as they are added.
        "setup": {"started": True, "calendar": True, "computer": True, "tools": True},
        "tools": store.tools(),
        "connections": store.connections(),
        "mcp": {"calendar_url": os.environ.get("PACE_CALENDAR_MCP_URL", ""), "provider": os.environ.get("PACE_CALENDAR_MCP_PROVIDER", "")},
        "linear_issues": store.linear_issues(),
        "companion": "live" if activity else "Waiting for desktop observer",
        "share_url": _share_url(),
    }


class PaceHandler(SimpleHTTPRequestHandler):
    def has_access(self) -> bool:
        expected = os.environ.get("PACE_ACCESS_TOKEN", "")
        if not expected:
            return True
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        received = jar.get("pace_access")
        return bool(received) and secrets.compare_digest(received.value, expected)

    def authorize(self) -> bool:
        if self.has_access():
            return True
        parsed = urlparse(self.path)
        access = (parse_qs(parsed.query).get("access") or [""])[-1]
        expected = os.environ.get("PACE_ACCESS_TOKEN", "")
        if access and secrets.compare_digest(access, expected):
            remaining = parse_qs(parsed.query)
            remaining.pop("access", None)
            location = parsed.path + (("?" + urlencode(remaining, doseq=True)) if remaining else "")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Set-Cookie", f"pace_access={expected}; HttpOnly; SameSite=Lax; Path=/")
            self.send_header("Location", location or "/")
            self.end_headers()
            return False
        self.respond_html("<h1>Pace is private</h1><p>Open the personal Pace link that was shared with you.</p>", HTTPStatus.UNAUTHORIZED)
        return False
    def translate_path(self, path: str) -> str:
        requested = urlparse(path).path.lstrip("/")
        if not requested:
            requested = "landing.html"
        elif requested == "app" or requested.startswith("app/"):
            requested = "index.html"
        candidate = (ASSETS / requested).resolve()
        if ASSETS not in candidate.parents and candidate != ASSETS:
            return str(ASSETS / "index.html")
        return str(candidate)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            return self.respond_json({"ok": True})
        if not self.authorize():
            return
        if path == "/api/state":
            return self.respond_json(state())
        if path == "/api/pairing-qr":
            return self.respond_qr(_share_url())
        if path.startswith("/api/connect/"):
            return self.connect(path, parse_qs(urlparse(self.path).query))
        return super().do_GET()

    def connect(self, path: str, query: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            return self.respond_json({"ok": False, "error": "Unknown connector."}, HTTPStatus.NOT_FOUND)
        provider = parts[2]
        callback = f"{OAUTH_CALLBACK_BASE}/api/connect/{provider}/callback"
        if len(parts) == 4 and parts[3] == "callback":
            expected = store.get_setting(f"oauth_state_{provider}", "")
            received = (query.get("state") or [""])[-1]
            if not expected or not secrets.compare_digest(expected, received):
                return self.respond_html("Pace could not verify this connection. Return to Pace and try again.", HTTPStatus.BAD_REQUEST)
            code = (query.get("code") or [""])[-1]
            if not code:
                return self.respond_html("The provider did not return an authorization code.", HTTPStatus.BAD_REQUEST)
            token, exchange_error = self.exchange(provider, code, callback)
            if not token:
                detail = exchange_error or "The OAuth provider returned an empty error response. Check the Hub terminal."
                return self.respond_html(f"Pace could not finish the connection: {detail}", HTTPStatus.BAD_REQUEST)
            permissions = ["read", "write_pace_blocks"] if provider == "google" else ["read", "write"]
            store.save_connection(provider, permissions, token)
            return self.respond_html("Connected. You can close this tab and return to Pace.")
        client_id = os.environ.get(f"PACE_{provider.upper()}_CLIENT_ID", "")
        if not client_id:
            return self.respond_json({"ok": False, "error": f"Set PACE_{provider.upper()}_CLIENT_ID and PACE_{provider.upper()}_CLIENT_SECRET before connecting {provider}."}, HTTPStatus.BAD_REQUEST)
        nonce = secrets.token_urlsafe(32)
        store.set_setting(f"oauth_state_{provider}", nonce)
        if provider == "google":
            params = {"client_id": client_id, "redirect_uri": callback, "response_type": "code", "access_type": "offline", "prompt": "consent", "scope": "https://www.googleapis.com/auth/calendar.events"}
            url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        elif provider == "linear":
            params = {"client_id": client_id, "redirect_uri": callback, "response_type": "code", "scope": "read write", "actor": "user", "state": nonce}
            url = "https://linear.app/oauth/authorize?" + urlencode(params)
        else:
            return self.respond_json({"ok": False, "error": "Unsupported connector."}, HTTPStatus.NOT_FOUND)
        if provider == "google":
            url += "&state=" + nonce
        self.send_response(HTTPStatus.FOUND); self.send_header("Location", url); self.end_headers()

    def exchange(self, provider: str, code: str, callback: str) -> tuple[str, str]:
        client_id = os.environ.get(f"PACE_{provider.upper()}_CLIENT_ID", "")
        secret = os.environ.get(f"PACE_{provider.upper()}_CLIENT_SECRET", "")
        endpoint = "https://oauth2.googleapis.com/token" if provider == "google" else "https://api.linear.app/oauth/token"
        data = urlencode({"code": code, "client_id": client_id, "client_secret": secret, "redirect_uri": callback, "grant_type": "authorization_code"}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=20, context=_https_context()) as response:
                payload = json.loads(response.read())
                refresh_token = payload.get("refresh_token", "")
                if refresh_token:
                    return refresh_token, ""
                return "", f"{provider.title()} accepted the authorization but did not issue a refresh token. Remove Pace from your connected apps, then try again. Response fields: {', '.join(sorted(payload.keys())) or 'none'}."
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace").strip()
            return "", f"Token exchange returned HTTP {exc.code}. {body[:500] or 'No response body.'}"
        except Exception as exc:
            return "", str(exc)

    def do_POST(self) -> None:
        if not self.has_access():
            return self.respond_json({"ok": False, "error": "Open Pace with your private access link first."}, HTTPStatus.UNAUTHORIZED)
        path = urlparse(self.path).path
        fields = self.request_fields()
        if path == "/api/activity":
            activity = {
                "app": str(fields.get("app", "unknown")),
                "idle_seconds": int(fields.get("idle_seconds", 0)),
                "paused": str(fields.get("paused", "")).lower() == "true",
                "window": str(fields.get("window", "")),
            }
            store.record_activity(
                activity["app"], activity["idle_seconds"], activity["paused"], activity["window"],
            )
            _analyse_activity_if_needed(activity)
            return self.respond_json({"ok": True})
        if path == "/api/focus-blocks":
            title = str(fields.get("title", "")).strip()
            start = str(fields.get("start", ""))
            end = str(fields.get("end", ""))
            if not title or not start or not end:
                return self.respond_json({"ok": False, "error": "Title, start, and end are required."}, HTTPStatus.BAD_REQUEST)
            try:
                start_at = datetime.fromisoformat(start)
                end_at = datetime.fromisoformat(end)
            except ValueError:
                return self.respond_json({"ok": False, "error": "Use an ISO date and time."}, HTTPStatus.BAD_REQUEST)
            if end_at <= start_at:
                return self.respond_json({"ok": False, "error": "The block must end after it starts."}, HTTPStatus.BAD_REQUEST)
            item = store.add_focus_block(title, start_at.astimezone().isoformat(), end_at.astimezone().isoformat(), str(fields.get("related_app", "")))
            store.audit("create_focus_block", f"Added {item['title']}")
            return self.respond_json({"ok": True, "focus_block": item})
        if path == "/api/capture":
            text = str(fields.get("text", "")).strip()
            if not text:
                return self.respond_json({"ok": False, "error": "Tell Pace what you want to do."}, HTTPStatus.BAD_REQUEST)
            try:
                intent = ai_context.plan_task(text, _now(), store.commitments(), store.linear_issues())
                start, end = planning.first_gap(store.commitments(), intent["duration_minutes"], intent["target_date"], intent["preferred_period"])
            except Exception as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            minutes = intent["duration_minutes"]
            title = intent["title"]
            issue = planning.match_issue(text, store.linear_issues())
            if issue:
                title = f"{issue['identifier']} · {issue['title']}"
            proposal = {"title": title[:160], "start": start.isoformat(), "end": end.isoformat(), "related_issue": issue}
            proposal_id = uuid.uuid4().hex
            store.save_proposal(proposal_id, f"capture:{proposal_id}", f"Schedule {proposal['title']} at {start.strftime('%a %H:%M')}", f"{intent['rationale']} Pace found a {minutes}-minute {intent['preferred_period']} opening around your fixed commitments.", {"create": proposal, "intent": intent})
            return self.respond_json({"ok": True, "proposal": {"id": proposal_id, **proposal}})
        if path == "/api/ai/settings":
            mode = str(fields.get("mode", "off")).strip().lower()
            base_url = str(fields.get("base_url", "")).strip().rstrip("/")
            model = str(fields.get("model", "")).strip()
            api_key = str(fields.get("api_key", "")).strip()
            if mode not in {"off", "openai-compatible", "uno-q"}:
                return self.respond_json({"ok": False, "error": "Choose a supported AI mode."}, HTTPStatus.BAD_REQUEST)
            if mode == "openai-compatible" and (not base_url.startswith(("http://", "https://")) or not model):
                return self.respond_json({"ok": False, "error": "Enter an OpenAI-compatible base URL and model name."}, HTTPStatus.BAD_REQUEST)
            if mode == "uno-q" and (not base_url.startswith(("http://", "https://")) or not api_key):
                return self.respond_json({"ok": False, "error": "Enter the Uno Q Pace app URL and its Hub token."}, HTTPStatus.BAD_REQUEST)
            store.set_setting("ai_mode", mode)
            store.set_setting("ai_base_url", base_url)
            store.set_setting("ai_model", model)
            if api_key:
                store.set_setting("ai_api_key", api_key)
            if mode == "off":
                store.set_setting("ai_api_key", "")
            store.audit("ai_settings", f"AI mode set to {mode}")
            return self.respond_json({"ok": True, "ai": ai_context.configured()})
        if path == "/api/google/sync":
            token = store.connection_token("google")
            if not token:
                return self.respond_json({"ok": False, "error": "Connect Google Calendar first."}, HTTPStatus.BAD_REQUEST)
            try:
                store.replace_commitments(google_calendar.list_today(token))
                store.audit("google_sync", "Imported Google Calendar events")
                return self.respond_json({"ok": True, "timeline": store.commitments()})
            except Exception as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/google/focus-block":
            token = store.connection_token("google")
            title, start, end = str(fields.get("title", "")).strip(), str(fields.get("start", "")), str(fields.get("end", ""))
            if not token or not title or not start or not end:
                return self.respond_json({"ok": False, "error": "Connect Google Calendar and provide a complete focus block."}, HTTPStatus.BAD_REQUEST)
            try:
                event = google_calendar.create_pace_block(token, title, start, end)
                store.audit("google_create", f"Created Pace block: {title}")
                return self.respond_json({"ok": True, "event": event})
            except Exception as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/google/disconnect":
            token = store.connection_token("google")
            try:
                if token:
                    google_calendar.revoke(token)
            except Exception:
                pass
            store.remove_connection("google")
            store.audit("google_disconnect", "Disconnected Google Calendar")
            return self.respond_json({"ok": True})
        if path == "/api/google/focus-block/update":
            token = store.connection_token("google")
            event_id, start, end = str(fields.get("event_id", "")), str(fields.get("start", "")), str(fields.get("end", ""))
            if not token or not event_id or not start or not end:
                return self.respond_json({"ok": False, "error": "A connected Google Calendar event and new times are required."}, HTTPStatus.BAD_REQUEST)
            try:
                event = google_calendar.update_pace_block(token, event_id, start, end)
                store.audit("google_update", f"Updated Pace block {event.get('summary') or event_id}")
                return self.respond_json({"ok": True, "event": event})
            except Exception as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/proposals/apply":
            proposal = store.proposal_by_id(str(fields.get("id", "")))
            if not proposal or proposal["status"] != "open":
                return self.respond_json({"ok": False, "error": "That proposal is no longer available."}, HTTPStatus.BAD_REQUEST)
            create = proposal["payload"].get("create")
            if not create:
                return self.respond_json({"ok": False, "error": "This proposal cannot be applied."}, HTTPStatus.BAD_REQUEST)
            try:
                token = store.connection_token("google")
                if token:
                    google_calendar.create_pace_block(token, create["title"], create["start"], create["end"])
                store.add_focus_block(create["title"], create["start"], create["end"])
                store.set_proposal_status(proposal["id"], "applied")
                store.audit("apply_proposal", proposal["summary"])
                return self.respond_json({"ok": True})
            except Exception as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/proposals/keep":
            proposal = store.proposal_by_id(str(fields.get("id", "")))
            if not proposal or proposal["status"] != "open":
                return self.respond_json({"ok": False, "error": "That proposal is no longer available."}, HTTPStatus.BAD_REQUEST)
            store.set_proposal_status(proposal["id"], "kept")
            store.audit("keep_proposal", proposal["summary"])
            return self.respond_json({"ok": True})
        if path == "/api/linear/sync":
            token = store.connection_token("linear")
            if not token:
                return self.respond_json({"ok": False, "error": "Connect Linear first."}, HTTPStatus.BAD_REQUEST)
            try:
                issues = linear.assigned_issues(token)
                store.save_linear_issues(issues)
                store.audit("linear_sync", f"Imported {len(issues)} assigned Linear issues")
                return self.respond_json({"ok": True, "issues": issues})
            except Exception as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/context/analyse":
            observation = {
                "app": fields.get("app", ""),
                "window": fields.get("window", ""),
                "screen_summary": fields.get("screen_summary", ""),
                "captured_at": _now(),
            }
            try:
                result = ai_context.analyse(observation, _current_block())
                store.record_context(observation, result)
                return self.respond_json({"ok": True, "context": result})
            except RuntimeError as exc:
                return self.respond_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/messages":
            text = str(fields.get("text", "")).strip()
            if not text:
                return self.respond_json({"ok": False, "error": "A message is required."}, HTTPStatus.BAD_REQUEST)
            store.add_message("you", text)
            return self.respond_json({"ok": True})
        if path == "/api/instructions":
            text = str(fields.get("text", "")).strip()
            if not text:
                return self.respond_json({"ok": False, "error": "An instruction is required."}, HTTPStatus.BAD_REQUEST)
            store.add_message("you", text)
            store.add_message(
                "pace",
                "I saved that request. Connect an AI model to interpret natural-language planning requests.",
            )
            return self.respond_json({"ok": True})
        return self.respond_json({"ok": False, "error": "Unknown endpoint."}, HTTPStatus.NOT_FOUND)

    def request_fields(self) -> dict:
        query = {key: values[-1] for key, values in parse_qs(urlparse(self.path).query).items()}
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return query
        raw = self.rfile.read(length).decode("utf-8")
        if "application/json" in self.headers.get("Content-Type", ""):
            body = json.loads(raw or "{}")
        else:
            body = {key: values[-1] for key, values in parse_qs(raw).items()}
        return {**query, **body}

    def respond_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def respond_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = f"<!doctype html><title>Pace</title><main style='font:16px system-ui;max-width:38rem;margin:15vh auto'>{body}</main>".encode()
        self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)

    def respond_qr(self, value: str) -> None:
        image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage, border=1)
        content = image.to_string(encoding="unicode").encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:7462")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    store.init()
    server = ThreadingHTTPServer((HOST, PORT), PaceHandler)
    print(f"Pace Hub is running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
