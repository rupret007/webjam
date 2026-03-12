from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)

# Must match ALLOWED_ARTIFACT_TYPES in ui/views/session_canvas.py
VALID_ARTIFACT_TYPES = {"image", "link", "note", "doc", "board"}
TITLE_MAX_LEN = 256
REFERENCE_MAX_LEN = 1024


class WebJamRepository:
    _MAX_COHORT_EVENTS = 1000

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(Path.home() / ".webjam_app.db")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            # Keep default journal mode if WAL is unavailable.
            pass
        return conn

    @contextmanager
    def _managed_connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._managed_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_key TEXT UNIQUE NOT NULL,
                    mode_key TEXT NOT NULL,
                    template_name TEXT NOT NULL,
                    session_goal TEXT NOT NULL,
                    review_state TEXT NOT NULL DEFAULT 'draft',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_notes (
                    room_key TEXT PRIMARY KEY,
                    notes TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Backward-compatible migrations for existing databases.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "must_change_password" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
            if "failed_attempts" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0")
            if "locked_until" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN locked_until INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def ensure_default_admin(self) -> None:
        with self._managed_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
                if row and row[0] > 0:
                    conn.commit()
                    return
                username = "admin"
                password = secrets.token_urlsafe(12)
                salt = os.urandom(16)
                digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
                conn.execute(
                    "INSERT INTO users (username, salt, password_hash, role, must_change_password, failed_attempts, locked_until) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (username, salt, digest, "admin", 1, 0, 0),
                )
                # Stored as plaintext intentionally for first-run UX display.
                # Deleted when the admin changes the password via update_password().
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("admin_bootstrap_password", password),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
            except Exception:
                conn.rollback()
                raise

    def get_bootstrap_admin_password(self) -> Optional[str]:
        return self.get_setting("admin_bootstrap_password")

    def authenticate(self, username: str, password: str) -> Optional[str]:
        role, status = self.authenticate_with_status(username, password)
        if status == "ok":
            return role
        return None

    def authenticate_with_status(self, username: str, password: str) -> Tuple[Optional[str], str]:
        if not isinstance(username, str) or not isinstance(password, str):
            return None, "invalid_credentials"
        now = int(time.time())
        with self._managed_connection() as conn:
            # Serialize login mutation flow so failed_attempt counters remain consistent
            # under concurrent attempts.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT salt, password_hash, role, must_change_password, failed_attempts, locked_until FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row:
                return None, "invalid_credentials"

            salt, expected_digest, role, must_change_password, failed_attempts, locked_until = row
            if int(locked_until or 0) > now:
                return None, "locked"
            if int(locked_until or 0) > 0 and int(locked_until or 0) <= now:
                conn.execute(
                    "UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE username = ?",
                    (username,),
                )
                conn.commit()
                failed_attempts = 0
                locked_until = 0

            if isinstance(salt, memoryview):
                salt = salt.tobytes()
            if isinstance(expected_digest, memoryview):
                expected_digest = expected_digest.tobytes()
            if not isinstance(salt, (bytes, bytearray)) or not isinstance(expected_digest, (bytes, bytearray)):
                LOGGER.warning("Corrupt credential payload for user '%s'; rejecting authentication.", username)
                return None, "invalid_credentials"
            try:
                actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes(salt), 120_000)
            except Exception as exc:
                LOGGER.warning("Credential digest computation failed for user '%s': %s", username, exc)
                return None, "invalid_credentials"
            if hmac.compare_digest(actual_digest, bytes(expected_digest)):
                conn.execute(
                    "UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE username = ?",
                    (username,),
                )
                conn.commit()
                if int(must_change_password or 0) == 1:
                    return role, "password_change_required"
                return role, "ok"

            new_failed_attempts = int(failed_attempts or 0) + 1
            new_locked_until = 0
            status = "invalid_credentials"
            if new_failed_attempts >= 5:
                new_locked_until = now + 300
                status = "locked"
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE username = ?",
                (new_failed_attempts, new_locked_until, username),
            )
            conn.commit()
            return None, status

    def update_password(self, username: str, new_password: str) -> bool:
        if not new_password or len(new_password) < 8 or len(new_password) > 128:
            return False
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", new_password.encode("utf-8"), salt, 120_000)
        with self._managed_connection() as conn:
            updated = conn.execute(
                "UPDATE users SET salt = ?, password_hash = ?, must_change_password = 0, failed_attempts = 0, locked_until = 0 "
                "WHERE username = ?",
                (salt, digest, username),
            ).rowcount
            updated_count = int(updated or 0)
            if username == "admin" and updated_count > 0:
                conn.execute("DELETE FROM app_settings WHERE key = ?", ("admin_bootstrap_password",))
            conn.commit()
        return updated_count > 0

    def set_setting(self, key: str, value: str) -> None:
        with self._managed_connection() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def increment_setting(self, key: str, amount: int = 1) -> int:
        with self._managed_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, "0"),
            )
            conn.execute(
                "UPDATE app_settings SET value = CAST(value AS INTEGER) + ? WHERE key = ?",
                (amount, key),
            )
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            conn.commit()
        if not row:
            return amount
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return amount

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._managed_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return row[0]

    def list_settings(self) -> Dict[str, str]:
        with self._managed_connection() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
        return {k: v for k, v in rows}

    def delete_setting(self, key: str) -> None:
        with self._managed_connection() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            conn.commit()

    def add_audit(self, action: str, actor: str, details: str) -> None:
        with self._managed_connection() as conn:
            conn.execute(
                "INSERT INTO audit_log (action, actor, details) VALUES (?, ?, ?)",
                (action, actor, details),
            )
            conn.commit()

    def get_audit_log(self, limit: int = 50) -> List[Tuple[int, str, str, str, str]]:
        with self._managed_connection() as conn:
            rows = conn.execute(
                "SELECT id, action, actor, details, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return rows

    def upsert_room_context(self, room_key: str, mode_key: str, template_name: str, session_goal: str, review_state: str = "draft") -> None:
        with self._managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO collaboration_rooms (room_key, mode_key, template_name, session_goal, review_state)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_key) DO UPDATE SET
                    mode_key = excluded.mode_key,
                    template_name = excluded.template_name,
                    session_goal = excluded.session_goal,
                    review_state = excluded.review_state,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (room_key, mode_key, template_name, session_goal, review_state),
            )
            conn.commit()

    def get_room_context(self, room_key: str) -> Dict[str, str]:
        with self._managed_connection() as conn:
            row = conn.execute(
                "SELECT mode_key, template_name, session_goal, review_state FROM collaboration_rooms WHERE room_key = ?",
                (room_key,),
            ).fetchone()
        if not row:
            return {
                "mode_key": "music_jam",
                "template_name": "Band Rehearsal",
                "session_goal": "",
                "review_state": "draft",
            }
        return {
            "mode_key": row[0],
            "template_name": row[1],
            "session_goal": row[2],
            "review_state": row[3],
        }

    def add_session_artifact(self, room_key: str, title: str, artifact_type: str, reference: str) -> int:
        title = ("" if title is None else str(title))[:TITLE_MAX_LEN]
        reference = ("" if reference is None else str(reference))[:REFERENCE_MAX_LEN]
        artifact_type = ("" if artifact_type is None else str(artifact_type)).strip().lower()
        if artifact_type not in VALID_ARTIFACT_TYPES:
            artifact_type = "note"
        with self._managed_connection() as conn:
            cur = conn.execute(
                "INSERT INTO collaboration_artifacts (room_key, title, artifact_type, reference) VALUES (?, ?, ?, ?)",
                (room_key, title, artifact_type, reference),
            )
            conn.commit()
            return int(cur.lastrowid)

    def remove_session_artifact(self, artifact_id: int) -> None:
        with self._managed_connection() as conn:
            conn.execute("DELETE FROM collaboration_artifacts WHERE id = ?", (artifact_id,))
            conn.commit()

    def list_session_artifacts(self, room_key: str) -> List[Dict[str, str]]:
        with self._managed_connection() as conn:
            rows = conn.execute(
                "SELECT id, title, artifact_type, reference, created_at FROM collaboration_artifacts WHERE room_key = ? ORDER BY id DESC",
                (room_key,),
            ).fetchall()
        return [
            {
                "id": str(row[0]),
                "title": row[1],
                "artifact_type": row[2],
                "reference": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def save_session_notes(self, room_key: str, notes: str) -> None:
        with self._managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO collaboration_notes (room_key, notes)
                VALUES (?, ?)
                ON CONFLICT(room_key) DO UPDATE SET notes = excluded.notes, updated_at = CURRENT_TIMESTAMP
                """,
                (room_key, notes),
            )
            conn.commit()

    def get_session_notes(self, room_key: str) -> str:
        with self._managed_connection() as conn:
            row = conn.execute("SELECT notes FROM collaboration_notes WHERE room_key = ?", (room_key,)).fetchone()
        if not row:
            return ""
        return row[0]

    def append_cohort_event(self, cohort: str, event_type: str, payload: Any) -> None:
        key = f"cohort_events_{cohort}"
        with self._managed_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            current = row[0] if row else "[]"
            try:
                events = json.loads(current)
                if not isinstance(events, list):
                    events = []
            except json.JSONDecodeError:
                events = []
            except TypeError:
                events = []
            except Exception as exc:
                LOGGER.warning("Unexpected cohort event payload decode failure for '%s': %s", key, exc)
                events = []
            event_ts = int(time.time())
            event_type_text = str(event_type)
            event_payload = payload
            if not isinstance(event_payload, dict):
                event_payload = {"value": event_payload}

            events.append(
                {
                    "ts": event_ts,
                    "event_type": event_type_text,
                    "payload": event_payload,
                }
            )
            if len(events) > self._MAX_COHORT_EVENTS:
                events = events[-self._MAX_COHORT_EVENTS :]
            try:
                serialized_events = json.dumps(events)
            except (TypeError, ValueError):
                LOGGER.warning(
                    "Non-JSON cohort payload detected for '%s'; coercing payload values to strings.",
                    key,
                )
                if isinstance(payload, dict):
                    safe_payload = {str(k): str(v) for k, v in payload.items()}
                else:
                    safe_payload = {"value": str(payload)}
                events[-1] = {
                    "ts": event_ts,
                    "event_type": event_type_text,
                    "payload": safe_payload,
                }
                serialized_events = json.dumps(events)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, serialized_events),
            )
            conn.commit()

