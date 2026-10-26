"""Local plan state. Calendar events themselves live on the MCP server."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime
from typing import Optional


def db_path() -> str:
    override = os.environ.get("PACE_DB")
    candidates = [override] if override else []
    candidates += ["/app/data/pace.db", "/home/arduino/pace/pace.db", "/tmp/pace/pace.db"]
    last_error = "No writable database path"
    for path in candidates:
        if not path:
            continue
        try:
            folder = os.path.dirname(path)
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".pace-write")
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(probe)
            return path
        except OSError as exc:
            last_error = str(exc)
    raise RuntimeError(last_error)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  app TEXT NOT NULL,
  idle_seconds INTEGER NOT NULL,
  paused INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  issue_key TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commitments (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  start_iso TEXT NOT NULL,
  end_iso TEXT NOT NULL,
  kind TEXT NOT NULL,
  pace_owned INTEGER NOT NULL,
  related_app TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,
  due TEXT NOT NULL,
  note TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  observation TEXT NOT NULL,
  analysis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
  provider TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  permissions TEXT NOT NULL,
  token TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(activity)")}
    if "window_title" not in columns:
        conn.execute("ALTER TABLE activity ADD COLUMN window_title TEXT NOT NULL DEFAULT ''")
    if conn.execute("SELECT value FROM settings WHERE key = 'token'").fetchone() is None:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('token', ?)",
            (secrets.token_hex(8),),
        )
    if conn.execute("SELECT value FROM settings WHERE key = 'protected_start'").fetchone() is None:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('protected_start', '18:00')"
        )
    conn.commit()
    return conn


def init() -> None:
    with connect():
        return


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def record_activity(app: str, idle_seconds: int, paused: bool, window_title: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO activity(ts, app, idle_seconds, paused, window_title) VALUES(?, ?, ?, ?, ?)",
            (
                datetime.now().astimezone().isoformat(),
                app,
                int(idle_seconds),
                1 if paused else 0,
                window_title or "",
            ),
        )


def latest_activity() -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {
        "app": row["app"],
        "window": row["window_title"] if "window_title" in row.keys() else "",
        "idle_seconds": row["idle_seconds"],
        "paused": bool(row["paused"]),
        "at": row["ts"],
    }


def replace_commitments(events: list[dict]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM commitments")
        conn.executemany(
            """
            INSERT INTO commitments(id, title, start_iso, end_iso, kind, pace_owned, related_app)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    event["id"],
                    event["title"],
                    event["start"],
                    event["end"],
                    event["kind"],
                    1 if event.get("pace_owned") else 0,
                    event.get("related_app") or "",
                )
                for event in events
            ],
        )


def commitments() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM commitments ORDER BY start_iso").fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "start": row["start_iso"],
            "end": row["end_iso"],
            "kind": row["kind"],
            "pace_owned": bool(row["pace_owned"]),
            "related_app": row["related_app"],
        }
        for row in rows
    ]


def add_focus_block(title: str, start: str, end: str, related_app: str = "") -> dict:
    """Create a Pace-owned block locally until a calendar connector is enabled."""
    item = {
        "id": uuid.uuid4().hex,
        "title": title.strip(),
        "start": start,
        "end": end,
        "kind": "flexible",
        "pace_owned": True,
        "related_app": related_app.strip(),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO commitments(id, title, start_iso, end_iso, kind, pace_owned, related_app)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"], item["title"], item["start"], item["end"], item["kind"], 1,
                item["related_app"],
            ),
        )
    return item


def open_proposal() -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM proposals WHERE status = 'open' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return _proposal(row)


def proposal_by_id(proposal_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _proposal(row)


def proposal_for_issue(issue_key: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM proposals WHERE issue_key = ? ORDER BY created_at DESC LIMIT 1",
            (issue_key,),
        ).fetchone()
    return _proposal(row)


def suppressed_issues() -> set[str]:
    with connect() as conn:
        rows = conn.execute("SELECT issue_key FROM proposals WHERE status = 'kept'").fetchall()
    return {row["issue_key"] for row in rows}


def save_proposal(proposal_id: str, issue_key: str, summary: str, detail: str, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO proposals(id, issue_key, status, summary, detail, payload, created_at)
            VALUES(?, ?, 'open', ?, ?, ?, ?)
            """,
            (
                proposal_id,
                issue_key,
                summary,
                detail,
                json.dumps(payload),
                datetime.now().astimezone().isoformat(),
            ),
        )


def set_proposal_status(proposal_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))


def audit(action: str, detail: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit(ts, action, detail) VALUES(?, ?, ?)",
            (datetime.now().astimezone().isoformat(), action, detail),
        )


def add_message(role: str, body: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages(ts, role, body) VALUES(?, ?, ?)",
            (datetime.now().astimezone().isoformat(), role, body),
        )


def messages(limit: int = 40) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"role": row["role"], "body": row["body"], "ts": row["ts"]} for row in reversed(rows)]


def tools() -> list[dict]:
    raw = get_setting("tools", "[]") or "[]"
    try:
        found = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return found if isinstance(found, list) else []


def save_tools(items: list[dict]) -> None:
    set_setting("tools", json.dumps(items))


def add_reminder(reminder_id: str, due: str, note: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO reminders(id, due, note, status) VALUES(?, ?, ?, 'open')",
            (reminder_id, due, note),
        )


def open_reminders() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'open' ORDER BY due"
        ).fetchall()
    return [{"id": row["id"], "due": row["due"], "note": row["note"]} for row in rows]


def dismiss_reminder(reminder_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE reminders SET status = 'done' WHERE id = ?", (reminder_id,))


def record_context(observation: dict, analysis: dict) -> None:
    """Persist text context only; raw screenshots never belong in the Pace database."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO context_observations(ts, observation, analysis) VALUES(?, ?, ?)",
            (datetime.now().astimezone().isoformat(), json.dumps(observation), json.dumps(analysis)),
        )


def latest_context() -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM context_observations ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "at": row["ts"],
        "observation": json.loads(row["observation"]),
        "analysis": json.loads(row["analysis"]),
    }


def save_connection(provider: str, permissions: list[str], token: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO connections(provider, status, permissions, token, updated_at) VALUES(?, 'connected', ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET status='connected', permissions=excluded.permissions, token=excluded.token, updated_at=excluded.updated_at",
            (provider, json.dumps(permissions), token, datetime.now().astimezone().isoformat()),
        )


def connections() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT provider, status, permissions, updated_at FROM connections ORDER BY provider").fetchall()
    return [{"provider": row["provider"], "status": row["status"], "permissions": json.loads(row["permissions"]), "updated_at": row["updated_at"]} for row in rows]


def connection_token(provider: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT token FROM connections WHERE provider = ?", (provider,)).fetchone()
    return row["token"] if row else ""


def remove_connection(provider: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM connections WHERE provider = ?", (provider,))


def save_linear_issues(items: list[dict]) -> None:
    set_setting("linear_issues", json.dumps(items))


def linear_issues() -> list[dict]:
    try:
        return json.loads(get_setting("linear_issues", "[]") or "[]")
    except json.JSONDecodeError:
        return []


def recent_audit(limit: int = 8) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": row["ts"], "action": row["action"], "detail": row["detail"]} for row in rows]


def _proposal(row) -> Optional[dict]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "issue_key": row["issue_key"],
        "status": row["status"],
        "summary": row["summary"],
        "detail": row["detail"],
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
    }
