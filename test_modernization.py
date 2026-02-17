import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from admin.policy import PolicyEngine, UserContext
from core.audio_engine import RealAudioEngine
from core.jamulus_protocol import JamulusProtocolAdapter
from core.settings import AppSettings, load_settings
from core.creative_modes import get_mode_by_key, get_mode_labels
from storage.repository import WebJamRepository
from ui.accessibility import clamp_scale, scaled_font_size, contrast_palette
from ui.auth_controller import AuthController
from ui.preferences import UiPreferencesService
from ui.services import MetricsService, RetryService
from ui.ux_status import classify_latency_ms, readiness_state, connection_summary
from ui.views.setup_wizard import SetupWizard


class TestModernizationCore(unittest.TestCase):
    def test_policy_engine_roles(self):
        policy = PolicyEngine()
        admin = UserContext(username="admin", role="admin")
        performer = UserContext(username="user", role="performer")
        self.assertTrue(policy.allows(admin, "bulk_reset"))
        self.assertFalse(policy.allows(performer, "bulk_reset"))

    def test_repository_auth(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.ensure_default_admin()
            bootstrap_pw = repo.get_bootstrap_admin_password()
            self.assertIsNotNone(bootstrap_pw)

            role, status = repo.authenticate_with_status("admin", bootstrap_pw or "")
            self.assertEqual(role, "admin")
            self.assertEqual(status, "password_change_required")

            self.assertTrue(repo.update_password("admin", "StrongPass123"))
            self.assertIsNone(repo.get_bootstrap_admin_password())
            role2, status2 = repo.authenticate_with_status("admin", "StrongPass123")
            self.assertEqual(role2, "admin")
            self.assertEqual(status2, "ok")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_repository_lockout_on_failed_attempts(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.ensure_default_admin()
            for _ in range(5):
                _role, status = repo.authenticate_with_status("admin", "wrong-password")
            self.assertEqual(status, "locked")
            _role, status2 = repo.authenticate_with_status("admin", "wrong-password")
            self.assertEqual(status2, "locked")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_repository_unlock_resets_failed_attempts_after_cooldown(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.ensure_default_admin()
            for _ in range(5):
                _role, status = repo.authenticate_with_status("admin", "wrong-password")
            self.assertEqual(status, "locked")

            future = int(time.time()) + 301
            with patch("storage.repository.time.time", return_value=future):
                _role, status_after = repo.authenticate_with_status("admin", "wrong-password")
            self.assertEqual(status_after, "invalid_credentials")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_repository_persists_ui_preferences(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.set_setting("ui_font_scale", "1.20")
            repo.set_setting("ui_high_contrast", "1")
            repo.set_setting("ui_auto_setup_on_start", "0")
            repo.set_setting("ui_window_geometry", "1600x900+40+40")
            self.assertEqual(repo.get_setting("ui_font_scale"), "1.20")
            self.assertEqual(repo.get_setting("ui_high_contrast"), "1")
            self.assertEqual(repo.get_setting("ui_auto_setup_on_start"), "0")
            self.assertEqual(repo.get_setting("ui_window_geometry"), "1600x900+40+40")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_repository_increment_setting(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            self.assertEqual(repo.increment_setting("metric_test_counter"), 1)
            self.assertEqual(repo.increment_setting("metric_test_counter"), 2)
            self.assertEqual(repo.get_setting("metric_test_counter"), "2")
            repo.set_setting("metric_test_counter", "bad")
            self.assertEqual(repo.increment_setting("metric_test_counter"), 1)
            repo.delete_setting("metric_test_counter")
            self.assertIsNone(repo.get_setting("metric_test_counter"))
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_repository_session_canvas_storage(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.upsert_room_context(
                room_key="default_room",
                mode_key="visual_studio",
                template_name="Critique Circle",
                session_goal="Review two sketches and choose revision direction.",
                review_state="review",
            )
            ctx = repo.get_room_context("default_room")
            self.assertEqual(ctx["mode_key"], "visual_studio")
            self.assertEqual(ctx["review_state"], "review")

            artifact_id = repo.add_session_artifact("default_room", "Inspiration Board", "link", "https://example.com/board")
            artifacts = repo.list_session_artifacts("default_room")
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0]["id"], str(artifact_id))

            repo.save_session_notes("default_room", "Keep color palette muted.")
            self.assertIn("muted", repo.get_session_notes("default_room"))

            repo.remove_session_artifact(artifact_id)
            self.assertEqual(len(repo.list_session_artifacts("default_room")), 0)
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_creative_modes_schema(self):
        labels = get_mode_labels()
        self.assertIn("Music Jam", labels)
        mode = get_mode_by_key("writers_room")
        self.assertIn("draft", mode.default_template.lower())

    def test_protocol_adapter_cache(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 22124)
        adapter.set_cached_participants({0: "Local", 1: "Peer"})
        names = adapter.get_cached_participants()
        self.assertEqual(names, ["Local", "Peer"])

    def test_audio_engine_fallback_level(self):
        settings = AppSettings()
        engine = RealAudioEngine(settings)
        engine.start()
        try:
            engine.set_level_override(5, 0.42)
            level = engine.get_level(5)
            self.assertGreaterEqual(level, 0.0)
            self.assertLessEqual(level, 1.0)
        finally:
            engine.stop()

    def test_setup_wizard_webex_url_validation(self):
        ok, detail = SetupWizard.check_webex_url("https://example.webex.com/meet/demo")
        self.assertTrue(ok)
        self.assertIn("valid", detail.lower())

        bad_ok, bad_detail = SetupWizard.check_webex_url("http:///bad-url")
        self.assertFalse(bad_ok)
        self.assertIn("invalid", bad_detail.lower())

    def test_setup_wizard_preflight_results_include_failures(self):
        settings = AppSettings(
            jamulus_server="127.0.0.1",
            jamulus_port=1,
            webex_url="https://webjam-sbx.webex.com/meet/webjam01",
        )
        results = SetupWizard.run_preflight_checks(
            settings=settings,
            find_jamulus=lambda: None,
            diagnostics_provider=lambda: {"active": "False", "backend": "none"},
        )
        checks = {name: ok for name, ok, _ in results}
        self.assertIn("Jamulus executable", checks)
        self.assertIn("Audio diagnostics", checks)
        self.assertFalse(checks["Jamulus executable"])
        self.assertFalse(checks["Audio diagnostics"])

    def test_settings_parses_jamulus_candidates_from_env(self):
        env_value = r"C:\Jamulus\Jamulus.exe;D:\Audio\Jamulus.exe"
        with patch.dict(os.environ, {"WEBJAM_JAMULUS_CANDIDATES": env_value}, clear=False):
            loaded = load_settings(settings_path="__nonexistent_settings_file__.json")
        self.assertEqual(
            loaded.jamulus_candidates,
            [r"C:\Jamulus\Jamulus.exe", r"D:\Audio\Jamulus.exe"],
        )

    def test_latency_classification(self):
        self.assertEqual(classify_latency_ms(None)[0], "Latency: n/a")
        self.assertIn("Good", classify_latency_ms(20.0)[0])
        self.assertIn("Fair", classify_latency_ms(45.0)[0])
        self.assertIn("Poor", classify_latency_ms(120.0)[0])

    def test_readiness_and_connection_summary(self):
        ready_text, _ = readiness_state(3)
        waiting_text, _ = readiness_state(0)
        self.assertEqual(ready_text, "Room: ready")
        self.assertEqual(waiting_text, "Room: waiting for participants")
        self.assertEqual(
            connection_summary("Connected", "Opened in browser"),
            "Jamulus: Connected | Webex: Opened in browser",
        )

    def test_accessibility_helpers(self):
        self.assertEqual(clamp_scale(0.2), 0.8)
        self.assertEqual(clamp_scale(2.0), 1.6)
        self.assertEqual(clamp_scale(1.1), 1.1)
        self.assertGreaterEqual(scaled_font_size(10, 0.8), 8)

        default_palette = contrast_palette(False)
        high_palette = contrast_palette(True)
        self.assertIn("bg", default_palette)
        self.assertIn("fg", high_palette)
        self.assertNotEqual(default_palette["bg"], high_palette["bg"])

    def test_retry_service_retries_then_succeeds(self):
        attempts = {"count": 0}

        def flaky():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient")
            return "ok"

        result = RetryService.retry_action(flaky, attempts=3, base_delay=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 3)

    def test_metrics_service_collect_reset_export(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            metrics = MetricsService(repo)
            metrics.increment("metric_setup_wizard_opened")
            collected = metrics.collect()
            self.assertEqual(collected["metric_setup_wizard_opened"], "1")

            tmp_dir = Path(tempfile.mkdtemp())
            out = metrics.export_snapshot(
                home_dir=tmp_dir,
                jamulus_state="Connected",
                webex_state="Opened in browser",
                latency_ms=23.5,
                server="127.0.0.1:22124",
                webex_url="https://example.webex.com/meet/demo",
                audio_diagnostics={"active": "True"},
            )
            self.assertTrue(out.exists())

            metrics.reset_with_prefix("metric_")
            collected_after = metrics.collect()
            self.assertEqual(collected_after["metric_setup_wizard_opened"], "0")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_metrics_service_collect_includes_dynamic_metric_keys(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            metrics = MetricsService(repo)
            repo.set_setting("metric_mode_selected_visual_studio", "3")
            collected = metrics.collect()
            self.assertEqual(collected.get("metric_mode_selected_visual_studio"), "3")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_repository_append_cohort_event(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.append_cohort_event(
                cohort="mixed_discipline",
                event_type="session_completed",
                payload={"mode_key": "music_jam"},
            )
            raw = repo.get_setting("cohort_events_mixed_discipline", "[]")
            self.assertIsNotNone(raw)
            self.assertIn("session_completed", raw or "")
            self.assertIn("music_jam", raw or "")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_ui_preferences_service_roundtrip(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            prefs_service = UiPreferencesService(repo)

            defaults = prefs_service.load()
            self.assertEqual(defaults.window_geometry, "1600x900")
            self.assertFalse(defaults.high_contrast_enabled)

            prefs_service.save_ui(
                font_scale=1.2,
                high_contrast_enabled=True,
                auto_setup_enabled=False,
            )
            prefs_service.save_window_geometry("1800x1000+20+20")

            loaded = prefs_service.load()
            self.assertAlmostEqual(loaded.font_scale, 1.2, places=2)
            self.assertTrue(loaded.high_contrast_enabled)
            self.assertFalse(loaded.auto_setup_enabled)
            self.assertEqual(prefs_service.get_window_geometry(), "1800x1000+20+20")

            self.assertEqual(prefs_service.reset_window_geometry(), "1600x900")
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_auth_controller_sign_in_with_password_rotation(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            repo = WebJamRepository(db_path=db)
            repo.ensure_default_admin()
            bootstrap_pw = repo.get_bootstrap_admin_password()
            self.assertIsNotNone(bootstrap_pw)

            controller = AuthController(repo, PolicyEngine())
            with patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", bootstrap_pw, "NewStrongPass1", "NewStrongPass1"]), patch("ui.auth_controller.messagebox.showinfo"), patch("ui.auth_controller.messagebox.showwarning"), patch("ui.auth_controller.messagebox.showerror"):
                user = controller.sign_in_interactive()
            self.assertIsNotNone(user)
            self.assertEqual(user.role, "admin")
            self.assertIsNone(repo.get_bootstrap_admin_password())
        finally:
            if os.path.exists(db):
                for _ in range(10):
                    try:
                        os.remove(db)
                        break
                    except PermissionError:
                        time.sleep(0.05)

    def test_auth_controller_authorize_rules(self):
        controller = AuthController(WebJamRepository(db_path=":memory:"), PolicyEngine())
        admin = UserContext(username="admin", role="admin")
        performer = UserContext(username="user", role="performer")
        with patch("ui.auth_controller.messagebox.showwarning"):
            self.assertTrue(controller.authorize(None, "save_mix", require_sign_in=False))
            self.assertFalse(controller.authorize(None, "save_mix", require_sign_in=True))
            self.assertTrue(controller.authorize(admin, "bulk_reset", require_sign_in=False))
            self.assertFalse(controller.authorize(performer, "bulk_reset", require_sign_in=False))


if __name__ == "__main__":
    unittest.main()

