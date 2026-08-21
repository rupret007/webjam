"""Where the Music AI key lives, and the boundaries that must stay closed.

The guards at the bottom are deliberately repo-wide. A Webex Embedded App
needs a licensed organization and a Control Hub administrator, so it cannot be
part of this product; these fail if companion, pairing, or panel code returns.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.redaction import redact_mapping
from core.settings import AppSettings, load_settings, save_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPING_DIRS = ("core", "webjam_qt", "services", "api", "ui")


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def test_the_key_defaults_to_empty_so_song_tools_fail_closed():
    assert AppSettings().music_ai_api_key == ""


def test_the_key_can_come_from_the_environment(tmp_path):
    config = str(tmp_path / "settings.json")
    with patch.dict(os.environ, {"WEBJAM_MUSIC_AI_API_KEY": "env-key-1"}):
        assert load_settings(config).music_ai_api_key == "env-key-1"


def test_the_environment_wins_over_a_saved_key(tmp_path):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"music_ai_api_key": "file-key"}))
    with patch.dict(os.environ, {"WEBJAM_MUSIC_AI_API_KEY": "env-key-2"}):
        assert load_settings(str(config)).music_ai_api_key == "env-key-2"


def test_a_saved_key_round_trips(tmp_path):
    config = tmp_path / "settings.json"
    save_settings(
        AppSettings(config_file=str(config), music_ai_api_key="saved-key")
    )
    with patch.dict(os.environ, {}, clear=True):
        assert load_settings(str(config)).music_ai_api_key == "saved-key"


def test_the_settings_file_stays_owner_only(tmp_path):
    """The key sits beside the Sentry DSN in a 0600 file."""

    config = tmp_path / "settings.json"
    save_settings(AppSettings(config_file=str(config), music_ai_api_key="k"))
    assert oct(config.stat().st_mode & 0o777) == "0o600"


def test_a_non_string_key_is_coerced_rather_than_crashing(tmp_path):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"music_ai_api_key": 12345}))
    with patch.dict(os.environ, {}, clear=True):
        assert load_settings(str(config)).music_ai_api_key == "12345"


def test_the_key_is_redacted_from_diagnostics():
    redacted = redact_mapping(
        {"music_ai_api_key": "live-secret-value", "jamulus_port": 22124}
    )
    assert redacted["music_ai_api_key"] == "[redacted]"
    assert redacted["jamulus_port"] == 22124


def test_no_api_key_is_committed_to_this_repository():
    """Every checked-in key value must be empty or an obvious test placeholder.

    A real Music AI key is a long opaque token. This walks the tree for any
    assignment to the setting and refuses anything that does not read as a
    fixture, so a key pasted in while debugging cannot be committed quietly.
    """

    import re

    assignment = re.compile(
        r"music_ai_api_key\s*(?:[:=]|=)\s*[\"']([^\"']*)[\"']"
    )
    allowed = {"", "k", "env-key-1", "env-key-2", "file-key", "saved-key"}
    offenders: list[str] = []

    for directory in (*SHIPPING_DIRS, "tests"):
        for path in (REPO_ROOT / directory).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for value in assignment.findall(text):
                if value not in allowed and len(value) > 12:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {len(value)} chars")

    assert offenders == []
    assert 'music_ai_api_key: str = ""' in (
        REPO_ROOT / "core" / "settings.py"
    ).read_text()


# ----------------------------------------------------------------------
# No embedded app, no companion, no panel API
# ----------------------------------------------------------------------
def _shipping_sources() -> list[tuple[Path, str]]:
    sources: list[tuple[Path, str]] = []
    for directory in SHIPPING_DIRS:
        for path in (REPO_ROOT / directory).rglob("*.py"):
            sources.append((path, path.read_text(encoding="utf-8", errors="ignore")))
    return sources


@pytest.mark.parametrize(
    "marker",
    [
        "webex.application",
        "app.application.states",
        "EmbeddedApp",
        "embedded_app",
        "QtWebEngine",
        "QWebEngineView",
        "getUser()",
    ],
)
def test_no_webex_embedded_app_code_ships(marker):
    """A custom add-on needs an org and a Control Hub admin. Free Webex has none."""

    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path, text in _shipping_sources()
        if marker in text
    ]
    assert offenders == []


def test_no_music_module_imports_a_meeting_dependency():
    """Song tools and songwriting must run with Webex absent entirely."""

    music_modules = [
        "core/song_form.py",
        "core/song_help.py",
        "core/song_workbench.py",
        "core/music_ai_client.py",
        "core/music_ai_catalog.py",
        "core/music_ai_results.py",
    ]
    for relative in music_modules:
        tree = ast.parse((REPO_ROOT / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "webex" not in (node.module or "").lower(), relative
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "webex" not in alias.name.lower(), relative


def test_song_tools_never_check_a_meeting_before_running():
    """No add-on gate: the upload decision must not consult Webex at all."""

    source = (REPO_ROOT / "core" / "song_workbench.py").read_text()
    assert "webex" not in source.lower()
    assert "meeting" not in source.lower()


def test_the_music_ai_client_talks_only_to_published_hosts():
    from core.music_ai_client import _ALLOWED_HOSTS

    assert _ALLOWED_HOSTS == frozenset(
        {"api.music.ai", "storage.googleapis.com", "cdn.music.ai"}
    )


def test_the_documented_base_url_and_console_are_used():
    from core.music_ai_client import API_BASE_URL, API_KEY_CONSOLE_URL

    assert API_BASE_URL == "https://api.music.ai/v1"
    assert API_KEY_CONSOLE_URL == "https://music.ai/dash"
