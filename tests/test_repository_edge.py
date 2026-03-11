from __future__ import annotations

import os
import threading
import unittest

from tests.conftest import make_temp_db, cleanup_temp_file
from storage.repository import WebJamRepository, VALID_ARTIFACT_TYPES, TITLE_MAX_LEN, REFERENCE_MAX_LEN


class TestRepositoryPasswordEdge(unittest.TestCase):
    def setUp(self):
        self.db = make_temp_db()
        self.repo = WebJamRepository(db_path=self.db)
        self.repo.ensure_default_admin()

    def tearDown(self):
        cleanup_temp_file(self.db)

    def test_empty_password_rejected(self):
        self.assertFalse(self.repo.update_password("admin", ""))

    def test_short_password_rejected(self):
        self.assertFalse(self.repo.update_password("admin", "1234567"))

    def test_exact_min_password_accepted(self):
        self.assertTrue(self.repo.update_password("admin", "12345678"))

    def test_128_char_password_accepted(self):
        pw = "a" * 128
        self.assertTrue(self.repo.update_password("admin", pw))
        role, status = self.repo.authenticate_with_status("admin", pw)
        self.assertEqual(status, "ok")

    def test_129_char_password_rejected(self):
        pw = "a" * 129
        self.assertFalse(self.repo.update_password("admin", pw))

    def test_unicode_password(self):
        pw = "\u00e9\u00e8\u00ea\u00eb" * 3
        self.assertTrue(self.repo.update_password("admin", pw))
        role, status = self.repo.authenticate_with_status("admin", pw)
        self.assertEqual(status, "ok")

    def test_nonexistent_user_returns_invalid(self):
        role, status = self.repo.authenticate_with_status("ghost", "password")
        self.assertIsNone(role)
        self.assertEqual(status, "invalid_credentials")

    def test_non_string_password_returns_invalid(self):
        role, status = self.repo.authenticate_with_status("admin", None)  # type: ignore[arg-type]
        self.assertIsNone(role)
        self.assertEqual(status, "invalid_credentials")

    def test_non_string_username_returns_invalid(self):
        role, status = self.repo.authenticate_with_status(None, "password123")  # type: ignore[arg-type]
        self.assertIsNone(role)
        self.assertEqual(status, "invalid_credentials")

    def test_failed_admin_password_update_keeps_bootstrap_secret(self):
        bootstrap_pw = self.repo.get_bootstrap_admin_password()
        self.assertTrue(bootstrap_pw)
        with self.repo._managed_connection() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", ("admin",))
            conn.commit()
        self.assertFalse(self.repo.update_password("admin", "newpassword123"))
        self.assertEqual(self.repo.get_bootstrap_admin_password(), bootstrap_pw)


class TestRepositoryIncrementConcurrent(unittest.TestCase):
    def setUp(self):
        self.db = make_temp_db()
        self.repo = WebJamRepository(db_path=self.db)

    def tearDown(self):
        cleanup_temp_file(self.db)

    def test_concurrent_increments(self):
        threads = []
        for _ in range(10):
            t = threading.Thread(target=lambda: self.repo.increment_setting("counter", 1))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        val = int(self.repo.get_setting("counter", "0"))
        self.assertEqual(val, 10)


class TestRepositoryCohortEvents(unittest.TestCase):
    def setUp(self):
        self.db = make_temp_db()
        self.repo = WebJamRepository(db_path=self.db)

    def tearDown(self):
        cleanup_temp_file(self.db)

    def test_cohort_event_overflow_truncates(self):
        for i in range(WebJamRepository._MAX_COHORT_EVENTS + 50):
            self.repo.append_cohort_event("test", "click", {"n": str(i)})
        import json
        raw = self.repo.get_setting(f"cohort_events_test")
        events = json.loads(raw)
        self.assertEqual(len(events), WebJamRepository._MAX_COHORT_EVENTS)

    def test_cohort_event_with_corrupt_json(self):
        self.repo.set_setting("cohort_events_bad", "NOT_JSON")
        self.repo.append_cohort_event("bad", "click", {"k": "v"})
        import json
        raw = self.repo.get_setting("cohort_events_bad")
        events = json.loads(raw)
        self.assertEqual(len(events), 1)

    def test_cohort_event_with_non_serializable_payload_is_coerced(self):
        self.repo.append_cohort_event("bad_payload", "click", {"bad": {"set_value"}})
        import json
        raw = self.repo.get_setting("cohort_events_bad_payload")
        events = json.loads(raw)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0]["payload"]["bad"], str)

    def test_cohort_event_with_non_mapping_payload_is_wrapped(self):
        self.repo.append_cohort_event("wrapped", "click", ["a", "b"])  # type: ignore[arg-type]
        import json
        raw = self.repo.get_setting("cohort_events_wrapped")
        events = json.loads(raw)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["value"], ["a", "b"])


class TestRepositoryArtifacts(unittest.TestCase):
    def setUp(self):
        self.db = make_temp_db()
        self.repo = WebJamRepository(db_path=self.db)

    def tearDown(self):
        cleanup_temp_file(self.db)

    def test_invalid_artifact_type_falls_back_to_note(self):
        aid = self.repo.add_session_artifact("room1", "Test", "invalid_type", "ref")
        artifacts = self.repo.list_session_artifacts("room1")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["artifact_type"], "note")

    def test_title_truncation_at_limit(self):
        long_title = "x" * (TITLE_MAX_LEN + 100)
        self.repo.add_session_artifact("room1", long_title, "note", "ref")
        artifacts = self.repo.list_session_artifacts("room1")
        self.assertEqual(len(artifacts[0]["title"]), TITLE_MAX_LEN)

    def test_reference_truncation_at_limit(self):
        long_ref = "y" * (REFERENCE_MAX_LEN + 100)
        self.repo.add_session_artifact("room1", "title", "link", long_ref)
        artifacts = self.repo.list_session_artifacts("room1")
        self.assertEqual(len(artifacts[0]["reference"]), REFERENCE_MAX_LEN)

    def test_all_valid_artifact_types_accepted(self):
        for atype in VALID_ARTIFACT_TYPES:
            aid = self.repo.add_session_artifact("room1", f"t_{atype}", atype, "ref")
            self.assertGreater(aid, 0)

    def test_room_context_defaults_for_unknown_room(self):
        ctx = self.repo.get_room_context("nonexistent")
        self.assertEqual(ctx["mode_key"], "music_jam")
        self.assertEqual(ctx["review_state"], "draft")

    def test_notes_roundtrip(self):
        self.repo.save_session_notes("room1", "Hello world")
        self.assertEqual(self.repo.get_session_notes("room1"), "Hello world")

    def test_empty_notes_for_unknown_room(self):
        self.assertEqual(self.repo.get_session_notes("ghost_room"), "")


class TestRepositorySettings(unittest.TestCase):
    def setUp(self):
        self.db = make_temp_db()
        self.repo = WebJamRepository(db_path=self.db)

    def tearDown(self):
        cleanup_temp_file(self.db)

    def test_get_setting_default(self):
        self.assertIsNone(self.repo.get_setting("nonexistent"))
        self.assertEqual(self.repo.get_setting("nonexistent", "fallback"), "fallback")

    def test_delete_setting(self):
        self.repo.set_setting("key1", "value1")
        self.repo.delete_setting("key1")
        self.assertIsNone(self.repo.get_setting("key1"))

    def test_list_settings_sorted(self):
        self.repo.set_setting("z_key", "z")
        self.repo.set_setting("a_key", "a")
        keys = list(self.repo.list_settings().keys())
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
