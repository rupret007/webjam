"""Where the Music AI key lives, and the boundaries that must stay closed.

The guards at the bottom are deliberately repo-wide. A Webex Embedded App
needs a licensed organization and a Control Hub administrator, so it cannot be
part of this product; these fail if companion, pairing, or panel code returns.
"""

from __future__ import annotations

import ast
import json
import os
import re
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
    allowed = {
        "",
        "k",
        "env-key-1",
        "env-key-2",
        "file-key",
        "saved-key",
        "from-the-old-file",
        "from-the-keychain",
    }
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
        "core/song_model_help.py",
        "core/music_ai_client.py",
        "core/music_ai_catalog.py",
        "core/music_ai_results.py",
        "core/provider_credentials.py",
        "core/secret_store.py",
        "core/text_model_client.py",
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
    """No add-on gate: the upload decision must not consult a meeting at all.

    Checked on identifiers, because the confirmation copy legitimately says a
    meeting and its recording are never uploaded.
    """

    import ast

    tree = ast.parse((REPO_ROOT / "core" / "song_workbench.py").read_text())
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.module or "" for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    for forbidden in ("webex", "meeting", "webex_url", "meeting_link"):
        assert not any(forbidden in name.lower() for name in names), forbidden


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


def test_music_song_stack_does_not_implement_a_drawing_surface():
    """Art owns the canvas. Music's song stack must not grow one."""

    music_paths = [
        path
        for path in (REPO_ROOT / "core").glob("*.py")
        if path.name.startswith(("music_ai", "song_", "stem_bench"))
    ]
    music_paths.extend(
        [
            REPO_ROOT / "webjam_qt" / "controllers" / "song_tools_coordinator.py",
            REPO_ROOT / "webjam_qt" / "widgets" / "song_overlay.py",
        ]
    )
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in music_paths
        if path.is_file()
        for marker in ("Drawpile", "drawpile", "CanvasStroke", "brush_stroke")
        if marker in path.read_text(encoding="utf-8", errors="ignore")
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
        "form_shape",
        "count_in",
        "states_place",
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


# SessionPulse / conductor / studio arrangement stay the creative and
# operational authorities. Music AI and song-tools PRs must not rewire them.
# Operational copy in the conductor (one next step after Host/Join) is not a
# rewire; the pairing check below still fails if those files change together
# with the Music AI / song-tools surface. The import check always runs.
_PULSE_OWNED_FILES = (
    "core/session_intelligence.py",
    "core/session_conductor.py",
    "core/musician_guidance.py",
    "core/studio_project.py",
    "core/studio_sections.py",
)
_MUSIC_AI_IMPORTS = {
    "core.music_ai_client",
    "core.music_ai_catalog",
    "core.music_ai_results",
    "core.song_workbench",
    "core.stem_bench",
    "core.song_clock",
    "core.song_form",
    "core.song_help",
    "core.song_sections",
    "core.song_model_help",
    "core.text_model_client",
    "core.provider_credentials",
    "core.secret_store",
    "webjam_qt.controllers.song_tools_coordinator",
}


def _is_music_ai_surface(path: str) -> bool:
    return path in SONG_MODULES or path.startswith("core/music_ai")


def _changed_paths_against_master() -> list[str]:
    import subprocess

    for spec in ("origin/master...HEAD", "origin/main...HEAD", "master...HEAD"):
        result = subprocess.run(
            ["git", "diff", "--name-only", spec],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0:
            return [line for line in result.stdout.splitlines() if line]
    return []


def _music_ai_and_pulse_paths(changed: list[str]) -> tuple[list[str], list[str]]:
    music_ai = [path for path in changed if _is_music_ai_surface(path)]
    pulse = [path for path in changed if path in _PULSE_OWNED_FILES]
    return music_ai, pulse


def _imported_modules(relative: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / relative).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_the_session_pulse_stays_local_and_untouched():
    """ADR 0002: Music AI / song-tools work must not rewire SessionPulse.

    Pulse-owned files may change for operational copy. A PR that also
    touches the Music AI / song-tools surface may not change them. Those
    files also must never import Music AI or song-workbench.
    """

    music_ai, pulse = _music_ai_and_pulse_paths(_changed_paths_against_master())
    assert not (music_ai and pulse), (
        "Music AI / song-tools files must not rewire SessionPulse / "
        f"conductor: {music_ai} vs {pulse}"
    )
    for owned in _PULSE_OWNED_FILES:
        imported = _imported_modules(owned)
        for module in imported:
            assert not module.startswith("core.music_ai"), f"{owned} imports {module}"
            assert module not in _MUSIC_AI_IMPORTS, f"{owned} imports {module}"


def test_a_music_ai_change_that_also_edits_the_conductor_is_still_rejected():
    """The pairing gate is not dropped; only copy-only conductor PRs pass."""

    music_ai, pulse = _music_ai_and_pulse_paths(
        ["core/music_ai_client.py", "core/session_conductor.py"]
    )
    assert music_ai == ["core/music_ai_client.py"]
    assert pulse == ["core/session_conductor.py"]
    copy_only_music, copy_only_pulse = _music_ai_and_pulse_paths(
        ["core/session_conductor.py", "webjam_qt/widgets/session_strip.py"]
    )
    assert copy_only_music == []
    assert copy_only_pulse == ["core/session_conductor.py"]
    for path in _PULSE_OWNED_FILES:
        assert not _is_music_ai_surface(path), path
    for path in SONG_MODULES:
        assert _is_music_ai_surface(path), path
    assert _is_music_ai_surface("core/music_ai_client.py")
    assert _is_music_ai_surface("core/music_ai_catalog.py")


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
    assert "Suggestion" in overlay
    assert "Nothing is uploaded" in overlay


# ----------------------------------------------------------------------
# ADR 0002: the panel explains, the HUD acts
# ----------------------------------------------------------------------
HUD_PRIMARY_ACTIONS = {
    "Copy Invite",
    "Copy New Invite",
    "Reset Invite",
    "Enter Jam",
    "Bring Jamulus Forward",
    "Fix Audio in Jamulus",
    "Yes, It Sounds Right",
    "Add Conversation",
    "Save Meeting Link",
    "Try Again",
}


def test_the_song_panel_adds_no_button_for_a_hud_primary_action():
    """ADR 0002: Canvas and Studio explain the next action, never duplicate it."""

    import ast

    tree = ast.parse(
        (REPO_ROOT / "webjam_qt" / "widgets" / "song_overlay.py").read_text()
    )
    button_labels = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "QPushButton"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            button_labels.add(node.args[0].value)

    assert not (button_labels & HUD_PRIMARY_ACTIONS), sorted(
        button_labels & HUD_PRIMARY_ACTIONS
    )
    for label in button_labels:
        assert "invite" not in label.lower()


def test_the_song_panel_never_touches_conductor_phase_or_recording():
    """Creative guidance may not alter operational truth."""

    source = (
        REPO_ROOT / "webjam_qt" / "controllers" / "song_tools_coordinator.py"
    ).read_text()
    for forbidden in (
        "session_conductor",
        "_render_session_conductor",
        "_update_session_hud",
        "_publish_musician_guidance",
        "_refresh_session_pulse",
        "recording.",
        "_on_record_requested",
        "audio.on_launch_toggle",
    ):
        assert forbidden not in source, forbidden


def test_the_clock_tick_does_not_rebuild_guidance():
    """ADR 0002: guidance is not rebuilt from playhead or animation ticks."""

    import ast
    import inspect
    import textwrap

    from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator

    tick = ast.parse(
        textwrap.dedent(inspect.getsource(SongToolsCoordinator._on_tick))
    )
    called = {
        node.func.attr
        for node in ast.walk(tick)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in (
        "_update_session_hud",
        "_publish_musician_guidance",
        "_refresh_session_pulse",
        "_render_session_conductor",
        "set_musician_guidance",
        "set_session_pulse",
    ):
        assert forbidden not in called


def test_the_missing_key_line_is_one_sentence_and_names_the_env_var():
    from core.music_ai_client import API_KEY_ENV_VAR, missing_key_message

    message = missing_key_message()
    assert API_KEY_ENV_VAR in message
    assert "music.ai/dash" in message
    assert len(message.split(". ")) <= 3
    assert "sign up" not in message.lower()


def test_the_missing_key_line_never_reaches_the_hud():
    """An absent optional credential is not the session's next action."""

    controller = (
        REPO_ROOT / "webjam_qt" / "controllers" / "application_controller.py"
    ).read_text()
    assert "missing_key_message" not in controller
    assert "music_ai_api_key" not in controller


# ----------------------------------------------------------------------
# Webex coexistence (ADR 0004): a second window, never touched
# ----------------------------------------------------------------------
SONG_MODULES = (
    "core/song_form.py",
    "core/song_help.py",
    "core/song_sections.py",
    "core/song_clock.py",
    "core/song_workbench.py",
    "core/stem_bench.py",
    "core/music_ai_client.py",
    "core/music_ai_catalog.py",
    "core/music_ai_results.py",
    "core/provider_credentials.py",
    "core/secret_store.py",
    "core/text_model_client.py",
    "core/song_model_help.py",
    "webjam_qt/widgets/song_overlay.py",
    "webjam_qt/controllers/song_tools_coordinator.py",
)


def test_song_tools_never_reach_into_the_meeting_app():
    """No embed, no output tap, no mute/mic/speaker, no screen share."""

    # Deliberately unambiguous terms: the stem bench has its own mute and solo,
    # which are faders on a reference file and nothing to do with a meeting.
    for relative in SONG_MODULES:
        source = (REPO_ROOT / relative).read_text().lower()
        for forbidden in (
            "webengine",
            "qwebengineview",
            "screen_share",
            "screenshare",
            "start_share",
            "meeting_mute",
            "webex_mute",
            "set_microphone",
            "set_speaker",
            "meeting_capture",
            # meeting_recording_note() is honest copy saying a recording is
            # *not* a take, so ban the capture-shaped forms specifically.
            "meeting_recording_path",
            "capture_meeting",
            "meeting_output",
            "oauth",
        ):
            assert forbidden not in source, f"{relative}: {forbidden}"


def test_song_tools_do_not_drive_the_meeting_handoff():
    """Join / Open Meeting and Show Webex App stay owned by Conversation."""

    source = (
        REPO_ROOT / "webjam_qt" / "controllers" / "song_tools_coordinator.py"
    ).read_text()
    for owned_elsewhere in (
        "_on_join_video",
        "_show_webex_app",
        "_show_webex_conversation",
        "_focus_webex_mute",
        "_leave_video",
        "join_meeting_url",
        "webbrowser",
    ):
        assert owned_elsewhere not in source, owned_elsewhere


def test_no_song_result_is_ever_sent_to_the_meeting():
    """Stems, chords, and lyrics land locally; nothing is pushed into a call."""

    source = (
        REPO_ROOT / "core" / "music_ai_results.py"
    ).read_text().lower()
    for forbidden in ("webex", "meeting", "share_to", "broadcast"):
        assert forbidden not in source, forbidden


def test_the_upload_source_can_never_be_a_meeting_or_the_live_mix():
    from core.music_ai_catalog import resolve_song_tools
    from core.music_ai_client import MusicAIWorkflow
    from core.song_workbench import (
        LIVE_MIX_SOURCE,
        SOURCE_PICKED_FILE,
        SOURCE_SHARED_TRACK,
        evaluate_upload,
    )

    catalog = resolve_song_tools(
        [MusicAIWorkflow("1", "Stem Separation", "stems", "isolate")]
    )
    capability = catalog.capability("stems")

    # Only two sources exist, and neither can be a meeting capture: one is a
    # file the user picked, the other is the Shared Track they loaded.
    for source_kind in ("webex_recording", "meeting_capture", "system_audio", ""):
        assert evaluate_upload(
            capability=capability,
            source_kind=source_kind,
            path="/tmp/whatever.wav",
            is_host=True,
            has_api_key=True,
        ).blocked

    assert {SOURCE_PICKED_FILE, SOURCE_SHARED_TRACK, LIVE_MIX_SOURCE} == {
        "picked_file",
        "shared_track",
        "<live-jamulus-mix>",
    }


def test_an_in_flight_job_never_covers_or_disables_the_meeting_controls():
    """A running job is one word on the strip; Conversation stays reachable."""

    import ast
    import inspect

    from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator

    for method in (
        SongToolsCoordinator.run_song_tool,
        SongToolsCoordinator._finish_tool,
        SongToolsCoordinator._render_song_line,
    ):
        tree = ast.parse(inspect.getsource(method).lstrip())
        touched = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "webex_embed",
            "set_video_state",
            "_video_button",
            "setEnabled",
        ):
            assert forbidden not in touched, f"{method.__name__}: {forbidden}"


def test_no_new_oauth_or_web_runtime_is_introduced():
    for relative in SONG_MODULES:
        source = (REPO_ROOT / relative).read_text().lower()
        for forbidden in ("oauth", "pkce", "client_secret", "webengine", "webview"):
            assert forbidden not in source, f"{relative}: {forbidden}"


# ----------------------------------------------------------------------
# Left-out rules: things Song tools must never do
# ----------------------------------------------------------------------
def test_stem_chips_never_touch_a_jamulus_channel_or_a_meeting_mute():
    """Two mutes stay two. A stem chip is neither of them."""

    import ast

    tree = ast.parse((REPO_ROOT / "core" / "stem_bench.py").read_text())
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    # The bench owns set_muted/set_solo on its own stems; what it must never
    # reach is a Jamulus channel or a meeting.
    for forbidden in (
        "jamulus",
        "channel_id",
        "set_channel_gain",
        "set_self_muted",
        "webex",
        "meeting",
    ):
        assert not any(forbidden in name.lower() for name in names), forbidden

    panel = (REPO_ROOT / "webjam_qt" / "widgets" / "song_overlay.py").read_text()
    assert "Musicians and the meeting are unaffected." in panel
    assert "stem of the reference file" in panel


def test_song_tools_never_re_route_an_audio_device():
    """Dual audio: picking a file must not move Webex or Jamulus devices."""

    for relative in SONG_MODULES:
        source = (REPO_ROOT / relative).read_text().lower()
        for forbidden in (
            "sounddevice",
            "set_input_device",
            "set_output_device",
            "audio_route",
            "coreaudio",
            "blackhole",
            "default_device",
        ):
            assert forbidden not in source, f"{relative}: {forbidden}"


def test_song_tools_never_mirror_chat_into_a_meeting_or_rename_anyone():
    """The sheet goes to band chat. Nothing goes to a meeting; no name moves."""

    source = (
        REPO_ROOT / "webjam_qt" / "controllers" / "song_tools_coordinator.py"
    ).read_text()
    assert "send_chat" in source          # Jamulus band chat, the existing path
    for forbidden in ("set_name", "musician_name", "webex_chat", "post_message"):
        assert forbidden not in source, forbidden


def test_a_meeting_recording_is_never_treated_as_a_take():
    from core.meeting_companion import meeting_recording_note

    note = meeting_recording_note()
    assert "is not a WebJam take" in note
    assert "Neither becomes the other" in note

    # The upload confirmation says the same thing where it matters most.
    from core.music_ai_catalog import resolve_song_tools
    from core.music_ai_client import MusicAIWorkflow
    from core.song_workbench import SOURCE_SHARED_TRACK, evaluate_upload

    catalog = resolve_song_tools(
        [MusicAIWorkflow("1", "Stem Separation", "stems", "isolate")]
    )
    decision = evaluate_upload(
        capability=catalog.capability("stems"),
        source_kind=SOURCE_SHARED_TRACK,
        path=str(REPO_ROOT / "pytest.ini"),
        is_host=True,
        has_api_key=True,
    )
    assert "neither is a meeting or its recording" in decision.confirmation_body


def test_write_help_never_writes_the_arrangement():
    """Jeff keeps the set list and the feel. Keep writes notes, nothing else."""

    source = (REPO_ROOT / "core" / "song_workbench.py").read_text()
    assert "keep_progression" in source
    # No Studio document, marker, or arrangement type is reachable from here.
    for forbidden in ("StudioDocument", "StudioMarker", "reorder_section"):
        assert forbidden not in source, forbidden


def test_a_job_left_running_is_reported_not_restarted():
    """Sleeping a laptop mid-job must not spend the account's credits twice."""

    import ast
    import inspect

    from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator

    source = inspect.getsource(SongToolsCoordinator)
    assert "_unfinished_job" in source
    assert "still at Music AI" in source

    finish = ast.parse(inspect.getsource(SongToolsCoordinator._finish_tool).lstrip())
    called = {
        node.func.attr
        for node in ast.walk(finish)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    # Nothing in the failure path starts another job.
    assert "run_song_tool" not in called
    assert "run_file_workflow" not in called


# ----------------------------------------------------------------------
# Music vocabulary stays inside Music
# ----------------------------------------------------------------------
# Words that belong to a song. "stem" is deliberately absent: master already
# uses it for recording stems across every profile, so it is shared product
# vocabulary rather than something Music leaked.
MUSIC_ONLY_WORDS = (
    "chord",
    "bpm",
    "verse",
    "chorus",
    "bridge",
    "lyric",
    "song tools",
    "music ai",
    "moises",
)


def test_music_words_never_reach_another_creator_profile(qapp=None):
    """Art, Podcast, and Review share this window; they do not share the song."""

    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QLabel,
        QPushButton,
        QWidget,
    )

    from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key_or_default
    from webjam_qt.windows.conductor_window import ConductorWindow

    app = QApplication.instance() or QApplication([])
    del app

    for profile in CREATOR_PROFILES:
        if profile.key == "music":
            continue
        window = ConductorWindow(
            mode_entries=[("music", "Music")],
            initial_mode_key="music",
            initial_title="Session",
        )
        try:
            window.set_creator_profile(
                get_creator_profile_by_key_or_default(profile.key)
            )
            visible: list[str] = []
            for widget in window.findChildren(QWidget):
                # isVisibleTo walks the ancestor chain; isHidden would collect
                # the children of a panel that is itself hidden.
                if not widget.isVisibleTo(window):
                    continue
                if isinstance(widget, (QLabel, QPushButton)):
                    visible.append(widget.text())
                    visible.append(widget.toolTip())
                elif isinstance(widget, QComboBox):
                    visible.extend(
                        widget.itemText(index) for index in range(widget.count())
                    )
            for action in window.session_strip._tools_button.menu().actions():
                if action.isVisible():
                    visible.append(action.text())

            blob = " ".join(text for text in visible if text).lower()
            for word in MUSIC_ONLY_WORDS:
                # Whole words only: "system output" is not a stem, and
                # "bridge" the word appears in ordinary sentences.
                assert not re.search(rf"\b{re.escape(word)}\b", blob), (
                    f"{profile.key}: {word}"
                )
        finally:
            window.deleteLater()


def test_the_shared_clock_contract_names_no_music_only_concept():
    """A painter subscribing to bars must not be handed a vocabulary lesson."""

    from core.song_clock import describe_contract

    fields = set(describe_contract()["fields"])
    # Bars, sections, key, and BPM are timeline facts any profile can use.
    # Anything narrower than that would be Music leaking outward.
    for narrow in ("stems", "lyrics", "suggestion", "workflow", "job"):
        assert not any(narrow in field for field in fields), narrow
