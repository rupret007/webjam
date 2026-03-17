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
MIX_PROFILE_NAME_MAX_LEN = 128
SENSITIVE_SETTING_KEYS = {"admin_bootstrap_password"}
SENSITIVE_SETTING_KEY_FRAGMENTS = ("password", "secret", "token")
BOOTSTRAP_CREDENTIALS_FILENAME_SUFFIX = "_admin_bootstrap.txt"


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mix_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_name TEXT UNIQUE NOT NULL,
                    mode_key TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_mix_defaults (
                    username TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
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

    def _bootstrap_credentials_path(self) -> Path:
        db_path = Path(self.db_path).expanduser()
        return db_path.with_name(f"{db_path.stem}{BOOTSTRAP_CREDENTIALS_FILENAME_SUFFIX}")

    @staticmethod
    def _format_bootstrap_credentials_text(password: str) -> str:
        return (
            "WebJam Bootstrap Admin Credentials\n\n"
            "Username: admin\n"
            f"Password: {password}\n\n"
            "Use these credentials for the first admin sign-in only.\n"
            "WebJam will require an immediate password change and then remove this file.\n"
        )

    @staticmethod
    def _parse_bootstrap_credentials_text(content: object) -> Optional[str]:
        if not isinstance(content, str):
            return None
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if line.startswith("Password:"):
                password = line.partition(":")[2].strip()
                return password or None
        return None

    def _write_bootstrap_credentials_file(self, password: str) -> bool:
        if not isinstance(password, str) or not password:
            return False
        path = self._bootstrap_credentials_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._format_bootstrap_credentials_text(password), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return True
        except OSError as exc:
            LOGGER.warning("Failed to write bootstrap admin credentials file '%s': %s", path, exc)
            return False

    def _remove_bootstrap_credentials_file(self) -> None:
        path = self._bootstrap_credentials_path()
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            LOGGER.warning("Failed to remove bootstrap admin credentials file '%s': %s", path, exc)

    def ensure_default_admin(self) -> None:
        bootstrap_password: Optional[str] = None
        with self._managed_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
                if row and row[0] > 0:
                    conn.commit()
                else:
                    username = "admin"
                    password = secrets.token_urlsafe(12)
                    bootstrap_password = password
                    salt = os.urandom(16)
                    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
                    conn.execute(
                        "INSERT INTO users (username, salt, password_hash, role, must_change_password, failed_attempts, locked_until) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (username, salt, digest, "admin", 1, 0, 0),
                    )
                    conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
            except Exception:
                conn.rollback()
                raise

        if bootstrap_password:
            if not self._write_bootstrap_credentials_file(bootstrap_password):
                self.set_setting("admin_bootstrap_password", bootstrap_password)
            else:
                self.delete_setting("admin_bootstrap_password")
        else:
            self.get_bootstrap_admin_credentials_path()

    def get_bootstrap_admin_credentials_path(self) -> Optional[str]:
        path = self._bootstrap_credentials_path()
        if path.exists():
            return str(path)
        legacy_password = self.get_setting("admin_bootstrap_password")
        if isinstance(legacy_password, str) and legacy_password:
            if self._write_bootstrap_credentials_file(legacy_password):
                self.delete_setting("admin_bootstrap_password")
                return str(path)
        return None

    def get_bootstrap_admin_password(self) -> Optional[str]:
        path = self.get_bootstrap_admin_credentials_path()
        if path:
            try:
                content = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                LOGGER.warning("Failed to read bootstrap admin credentials file '%s': %s", path, exc)
            else:
                password = self._parse_bootstrap_credentials_text(content)
                if password:
                    return password
        legacy_password = self.get_setting("admin_bootstrap_password")
        if isinstance(legacy_password, str) and legacy_password:
            return legacy_password
        return None

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
        if username == "admin" and updated_count > 0:
            self._remove_bootstrap_credentials_file()
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

    @staticmethod
    def is_sensitive_setting_key(key: object) -> bool:
        lowered = str(key or "").strip().casefold()
        if lowered in SENSITIVE_SETTING_KEYS:
            return True
        return any(fragment in lowered for fragment in SENSITIVE_SETTING_KEY_FRAGMENTS)

    @staticmethod
    def _normalize_mix_profile_name(profile_name: object) -> str:
        normalized = str(profile_name or "").strip()
        if not normalized:
            return ""
        return normalized[:MIX_PROFILE_NAME_MAX_LEN]

    @staticmethod
    def _normalize_username(username: object) -> str:
        return str(username or "").strip()

    @staticmethod
    def _mix_profile_name_key(profile_name: object) -> str:
        return WebJamRepository._normalize_mix_profile_name(profile_name).casefold()

    @staticmethod
    def _serialize_json_payload(payload: Any) -> str:
        try:
            return json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON serializable") from exc

    def _matching_mix_profile_rows(
        self,
        conn: sqlite3.Connection,
        profile_name: str,
    ) -> List[Tuple[int, str, str, str, str]]:
        wanted_key = self._mix_profile_name_key(profile_name)
        if not wanted_key:
            return []
        rows = conn.execute(
            "SELECT id, profile_name, mode_key, payload, updated_at FROM mix_profiles ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [row for row in rows if self._mix_profile_name_key(row[1]) == wanted_key]

    def save_mix_profile(self, profile_name: str, mode_key: str, payload: Any) -> None:
        normalized_name = self._normalize_mix_profile_name(profile_name)
        if not normalized_name:
            raise ValueError("profile_name cannot be empty")
        normalized_mode = str(mode_key or "").strip()
        serialized_payload = self._serialize_json_payload(payload)
        with self._managed_connection() as conn:
            existing_rows = self._matching_mix_profile_rows(conn, normalized_name)
            if existing_rows:
                primary_id = int(existing_rows[0][0])
                conn.execute(
                    """
                    UPDATE mix_profiles
                    SET profile_name = ?, mode_key = ?, payload = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (normalized_name, normalized_mode, serialized_payload, primary_id),
                )
                duplicate_ids = [int(row[0]) for row in existing_rows[1:]]
                if duplicate_ids:
                    placeholders = ",".join("?" for _ in duplicate_ids)
                    conn.execute(f"DELETE FROM mix_profiles WHERE id IN ({placeholders})", duplicate_ids)
            else:
                conn.execute(
                    "INSERT INTO mix_profiles (profile_name, mode_key, payload) VALUES (?, ?, ?)",
                    (normalized_name, normalized_mode, serialized_payload),
                )
            conn.commit()

    def save_user_mix_default(self, username: str, payload: Any) -> None:
        normalized_username = self._normalize_username(username)
        if not normalized_username:
            raise ValueError("username cannot be empty")
        serialized_payload = self._serialize_json_payload(payload)
        with self._managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_mix_defaults (username, payload)
                VALUES (?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_username, serialized_payload),
            )
            conn.commit()

    def get_user_mix_default(self, username: str) -> Optional[Dict[str, Any]]:
        normalized_username = self._normalize_username(username)
        if not normalized_username:
            return None
        with self._managed_connection() as conn:
            row = conn.execute(
                "SELECT username, payload, updated_at FROM user_mix_defaults WHERE username = ?",
                (normalized_username,),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            LOGGER.warning("Corrupt user mix payload for '%s'; skipping.", normalized_username)
            return None
        return {
            "username": row[0],
            "payload": payload,
            "updated_at": row[2],
        }

    def get_mix_profile(self, profile_name: str) -> Optional[Dict[str, Any]]:
        normalized_name = self._normalize_mix_profile_name(profile_name)
        if not normalized_name:
            return None
        with self._managed_connection() as conn:
            rows = self._matching_mix_profile_rows(conn, normalized_name)
        for row in rows:
            try:
                payload = json.loads(row[3])
            except (TypeError, json.JSONDecodeError):
                LOGGER.warning("Corrupt mix profile payload for '%s'; skipping.", normalized_name)
                continue
            return {
                "profile_name": row[1],
                "mode_key": row[2],
                "payload": payload,
                "updated_at": row[4],
            }
        return None

    def list_mix_profiles(self, mode_key: Optional[str] = None) -> List[Dict[str, str]]:
        params: tuple[Any, ...] = ()
        query = "SELECT id, profile_name, mode_key, updated_at FROM mix_profiles"
        if mode_key is not None:
            query += " WHERE mode_key = ?"
            params = (str(mode_key or "").strip(),)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._managed_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        deduped: List[Dict[str, str]] = []
        seen_keys: set[str] = set()
        for row in rows:
            profile_name = row[1]
            key = self._mix_profile_name_key(profile_name)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(
                {
                    "profile_name": profile_name,
                    "mode_key": row[2],
                    "updated_at": row[3],
                }
            )
        deduped.sort(key=lambda item: item["profile_name"].casefold())
        return deduped

    def delete_mix_profile(self, profile_name: str) -> bool:
        normalized_name = self._normalize_mix_profile_name(profile_name)
        if not normalized_name:
            return False
        with self._managed_connection() as conn:
            matching_rows = self._matching_mix_profile_rows(conn, normalized_name)
            if not matching_rows:
                return False
            ids = [int(row[0]) for row in matching_rows]
            placeholders = ",".join("?" for _ in ids)
            deleted = conn.execute(f"DELETE FROM mix_profiles WHERE id IN ({placeholders})", ids).rowcount
            conn.commit()
        return int(deleted or 0) > 0

    def export_support_snapshot(self, room_key: Optional[str] = None) -> Dict[str, Any]:
        def _safe_json_payload(raw_payload: str) -> Tuple[Any, str]:
            try:
                return json.loads(raw_payload), "ok"
            except (TypeError, json.JSONDecodeError):
                return None, "invalid"

        with self._managed_connection() as conn:
            app_settings_rows = conn.execute(
                "SELECT key, value FROM app_settings ORDER BY key"
            ).fetchall()
            artifact_count = None
            note_length = None
            room_mode_key: Optional[str] = None
            if room_key is not None:
                room_rows = conn.execute(
                    """
                    SELECT room_key, mode_key, template_name, session_goal, review_state, updated_at
                    FROM collaboration_rooms
                    WHERE room_key = ?
                    ORDER BY room_key
                    """,
                    (room_key,),
                ).fetchall()
                if room_rows:
                    room_mode_key = str(room_rows[0][1] or "")
                if room_mode_key:
                    profile_rows = conn.execute(
                        """
                        SELECT profile_name, mode_key, payload, updated_at
                        FROM mix_profiles
                        WHERE mode_key = ?
                        ORDER BY updated_at DESC, id DESC
                        """,
                        (room_mode_key,),
                    ).fetchall()
                else:
                    profile_rows = []
                user_mix_rows: list[Any] = []
                artifact_count_row = conn.execute(
                    "SELECT COUNT(*) FROM collaboration_artifacts WHERE room_key = ?",
                    (room_key,),
                ).fetchone()
                note_row = conn.execute(
                    "SELECT LENGTH(notes), updated_at FROM collaboration_notes WHERE room_key = ?",
                    (room_key,),
                ).fetchone()
                artifact_count = int(artifact_count_row[0]) if artifact_count_row else 0
                note_length = {
                    "chars": int(note_row[0] or 0),
                    "updated_at": note_row[1],
                } if note_row else {"chars": 0, "updated_at": None}
            else:
                room_rows = conn.execute(
                    """
                    SELECT room_key, mode_key, template_name, session_goal, review_state, updated_at
                    FROM collaboration_rooms
                    ORDER BY room_key
                    """
                ).fetchall()
                profile_rows = conn.execute(
                    """
                    SELECT profile_name, mode_key, payload, updated_at
                    FROM mix_profiles
                    ORDER BY updated_at DESC, id DESC
                    """
                ).fetchall()
                user_mix_rows = conn.execute(
                    """
                    SELECT username, payload, updated_at
                    FROM user_mix_defaults
                    ORDER BY updated_at DESC, username ASC
                    """
                ).fetchall()

        app_settings = {
            key: value
            for key, value in app_settings_rows
            if not self.is_sensitive_setting_key(key)
        }
        mix_profiles: List[Dict[str, Any]] = []
        for profile_name, mode_key, raw_payload, updated_at in profile_rows:
            payload, payload_state = _safe_json_payload(raw_payload)
            mix_profiles.append(
                {
                    "profile_name": profile_name,
                    "mode_key": mode_key,
                    "payload_state": payload_state,
                    "payload": payload,
                    "updated_at": updated_at,
                }
            )
        user_mix_defaults: List[Dict[str, Any]] = []
        for username, raw_payload, updated_at in user_mix_rows:
            payload, payload_state = _safe_json_payload(raw_payload)
            user_mix_defaults.append(
                {
                    "username": username,
                    "payload_state": payload_state,
                    "payload": payload,
                    "updated_at": updated_at,
                }
            )

        snapshot: Dict[str, Any] = {
            "app_settings": app_settings,
            "room_contexts": [
                {
                    "room_key": row[0],
                    "mode_key": row[1],
                    "template_name": row[2],
                    "session_goal": row[3],
                    "review_state": row[4],
                    "updated_at": row[5],
                }
                for row in room_rows
            ],
            "mix_profiles": mix_profiles,
            "user_mix_defaults": user_mix_defaults,
        }
        if room_key is not None:
            snapshot["current_room_summary"] = {
                "room_key": room_key,
                "artifact_count": artifact_count,
                "notes": note_length,
            }
        return snapshot

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

