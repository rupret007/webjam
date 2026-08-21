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


# ----------------------------------------------------------------------
# The clock is a cross-profile contract, not a Music internal
# ----------------------------------------------------------------------
def test_the_clock_does_not_depend_on_anything_music_specific():
    """A painter must be able to read bars without importing Song tools."""

    tree = ast.parse((REPO_ROOT / "core" / "song_clock.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert imported <= {
        "__future__",
        "threading",
        "time",
        "dataclasses",
        "typing",
        "core.song_form",
    }
    for forbidden in (
        "core.music_ai_client",
        "core.music_ai_catalog",
        "core.stem_bench",
        "core.song_workbench",
    ):
        assert forbidden not in imported


def test_no_art_or_drawing_surface_was_implemented():
    """The clock is published for another profile; the profile is not built."""

    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path, text in _shipping_sources()
        for marker in ("Drawpile", "drawpile", "CanvasStroke", "brush_stroke")
        if marker in text
    ]
    assert offenders == []


def test_the_published_clock_contract_is_stable_and_declared():
    from core.song_clock import describe_contract

    contract = describe_contract()
    # A rename or removal here breaks every subscribing profile, so the field
    # list is pinned rather than merely documented. Adding a field is allowed
    # and updates this pin; changing what an existing one means is not.
    assert set(contract["fields"]) == {
        "position_source",
        "generation",
        "state",
        "position_s",
        "section",
        "section_index",
        "section_role",
        "bar",
        "bar_in_section",
        "beat",
        "bars_total",
        "beats_per_bar",
        "key",
        "key_source",
        "bpm",
        "bpm_source",
        "chords_now",
        "sections",
        "following_audio",
        "section_lengths_assumed",
    }


def test_the_clock_never_reads_the_live_jam():
    """No beat tracking exists, so no code here may reach for audio.

    Checked on identifiers rather than raw text: the module docstring names
    Jamulus precisely in order to say it does not touch it, and that sentence
    should not have to be deleted to keep this guard honest.
    """

    tree = ast.parse((REPO_ROOT / "core" / "song_clock.py").read_text())
    identifiers = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    for forbidden in (
        "jamulus",
        "sounddevice",
        "numpy",
        "np",
        "soundfile",
        "sf",
        "audio_engine",
        "detect_tempo",
    ):
        assert not any(
            forbidden in name.lower() for name in identifiers
        ), forbidden


# ----------------------------------------------------------------------
# The song stack loads only when a session asks for it
# ----------------------------------------------------------------------
def test_the_song_stack_is_not_imported_at_controller_import_time():
    """Podcast and Review sessions must not pay for Music AI to start.

    This is also load-bearing for stability: pulling this module graph in at
    application_controller import time reproducibly crashed Qt teardown during
    pytest's forced end-of-session garbage collection, in a process where every
    test had passed. Keeping the import inside the property fixes that and
    keeps the stack off the startup path.
    """

    tree = ast.parse(
        (REPO_ROOT / "webjam_qt" / "controllers" / "application_controller.py")
        .read_text()
    )
    module_level = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "webjam_qt.controllers.song_tools_coordinator" not in module_level
    for module in module_level:
        assert not module.startswith("core.music_ai")
        assert module not in {"core.song_workbench", "core.stem_bench"}


def test_the_coordinator_is_built_on_first_use():
    from webjam_qt.controllers.application_controller import ApplicationController

    controller = ApplicationController.__new__(ApplicationController)
    controller._song_tools = None

    first = ApplicationController.song_tools.fget(controller)
    second = ApplicationController.song_tools.fget(controller)

    assert type(first).__name__ == "SongToolsCoordinator"
    assert first is second


# ----------------------------------------------------------------------
# ADR 0002: no model SDK on the realtime path
# ----------------------------------------------------------------------
def test_no_generative_model_sdk_is_bundled():
    """ADR 0002: no model SDK or generative runtime on the realtime path."""

    requirements = (REPO_ROOT / "requirements.txt").read_text().lower()
    for sdk in (
        "openai",
        "anthropic",
        "transformers",
        "torch",
        "onnxruntime",
        "llama",
        "langchain",
        "google-generativeai",
    ):
        assert sdk not in requirements, sdk

    for path, text in _shipping_sources():
        lowered = text.lower()
        for sdk in ("import openai", "import anthropic", "import torch"):
            assert sdk not in lowered, f"{path}: {sdk}"


def test_music_ai_never_runs_on_the_jamulus_realtime_path():
    """Cloud jobs are worker-thread only and touch no audio callback."""

    source = (
        REPO_ROOT / "webjam_qt" / "controllers" / "song_tools_coordinator.py"
    ).read_text()
    assert "threading.Thread" in source

    tree = ast.parse((REPO_ROOT / "core" / "music_ai_client.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    for audio in ("sounddevice", "jamulus_controller", "core.audio_engine", "numpy"):
        assert audio not in imported


def test_the_session_pulse_stays_local_and_untouched():
    """ADR 0002 keeps SessionPulse the creative authority; nothing rewires it."""

    import subprocess

    changed = subprocess.run(
        ["git", "diff", "--name-only", "origin/master...HEAD"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    ).stdout.split()
    for owned_elsewhere in (
        "core/session_intelligence.py",
        "core/session_conductor.py",
        "core/musician_guidance.py",
        "core/studio_project.py",
        "core/studio_sections.py",
    ):
        assert owned_elsewhere not in changed, owned_elsewhere


def test_song_help_is_labelled_a_suggestion_everywhere_it_is_offered():
    """ADR 0002: a future assistant must be visibly labeled as a suggestion."""

    from core.song_form import parse_song_form
    from core.song_help import suggest_chords, suggest_next_chords

    form = parse_song_form("Key: G major\n[Verse]\nG D Em C\n")
    assert "Suggestions for" in suggest_chords(form).headline()
    assert "try:" in suggest_next_chords(form, section_name="Verse").headline()

    overlay = (
        REPO_ROOT / "webjam_qt" / "widgets" / "song_overlay.py"
    ).read_text()
    assert "Suggest chords" in overlay
    assert "Nothing is uploaded" in overlay
