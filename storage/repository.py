from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class WebJamRepository:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(Path.home() / ".webjam_app.db")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            if row and row[0] > 0:
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
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("admin_bootstrap_password", password),
            )
            conn.commit()

    def get_bootstrap_admin_password(self) -> Optional[str]:
        return self.get_setting("admin_bootstrap_password")

    def authenticate(self, username: str, password: str) -> Optional[str]:
        role, status = self.authenticate_with_status(username, password)
        if status == "ok":
            return role
        return None

    def authenticate_with_status(self, username: str, password: str) -> Tuple[Optional[str], str]:
        now = int(time.time())
        with self._connect() as conn:
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

            actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
            if actual_digest == expected_digest:
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
        if not new_password or len(new_password) < 8:
            return False
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", new_password.encode("utf-8"), salt, 120_000)
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE users SET salt = ?, password_hash = ?, must_change_password = 0, failed_attempts = 0, locked_until = 0 "
                "WHERE username = ?",
                (salt, digest, username),
            ).rowcount
            if username == "admin":
                conn.execute("DELETE FROM app_settings WHERE key = ?", ("admin_bootstrap_password",))
            conn.commit()
        return bool(updated)

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def increment_setting(self, key: str, amount: int = 1) -> int:
        current_raw = self.get_setting(key, "0") or "0"
        try:
            current_val = int(current_raw)
        except ValueError:
            current_val = 0
        new_val = current_val + amount
        self.set_setting(key, str(new_val))
        return new_val

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return row[0]

    def list_settings(self) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
        return {k: v for k, v in rows}

    def delete_setting(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            conn.commit()

    def add_audit(self, action: str, actor: str, details: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (action, actor, details) VALUES (?, ?, ?)",
                (action, actor, details),
            )
            conn.commit()

    def get_audit_log(self, limit: int = 50) -> List[Tuple[int, str, str, str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, action, actor, details, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return rows

