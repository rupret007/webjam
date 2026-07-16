"""One end-to-end, hardware-free proof of the dual-musician rehearsal lab."""

from __future__ import annotations

import json
import stat

from core import session_transfer_runtime
from tests.support.dual_musician_rehearsal_lab import (
    CLEANUP_SCHEMA,
    LAB_REPORT_SCHEMA,
    DualMusicianRehearsalLab,
)


def test_dual_musician_rehearsal_lab_is_repeatable_and_sanitized(
    tmp_path,
    monkeypatch,
) -> None:
    """Exercise real peer HTTP/transfer while isolating only RFC1918 admission.

    Production intentionally rejects loopback as a private-session endpoint.
    The scoped test values permit the in-process loopback service and suspend
    background maintenance writes while the lab performs exact reconciliation.
    Both are restored before the test returns; production policy is unchanged.
    """

    original_policy = session_transfer_runtime.is_private_lan_host
    original_poll_seconds = session_transfer_runtime._POLL_SECONDS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "core.session_transfer_runtime.is_private_lan_host",
            lambda host: host == "127.0.0.1",
        )
        scoped.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
        result = DualMusicianRehearsalLab(tmp_path).run()

    assert session_transfer_runtime.is_private_lan_host is original_policy
    assert session_transfer_runtime._POLL_SECONDS == original_poll_seconds
    assert result.report_path.is_file()
    assert result.cleanup_path.is_file()
    assert result.export_folder.is_dir()
    assert result.gapped_take_dir.is_dir()
    assert result.clean_take_dir.is_dir()
    assert stat.S_IMODE(result.report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.cleanup_path.stat().st_mode) == 0o600

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    cleanup = json.loads(result.cleanup_path.read_text(encoding="utf-8"))
    assert report == result.report
    assert cleanup == result.cleanup
    assert report["schema_version"] == LAB_REPORT_SCHEMA
    assert cleanup["schema_version"] == CLEANUP_SCHEMA
    assert report["overall_status"] == "passed"
    assert report["execution"]["jamulus"] == "not_exercised"
    assert all(step["status"] == "passed" for step in report["ux_step_measures"])
    assert all("elapsed_ms" in step and "target_ms" in step for step in report["ux_step_measures"])
    assert cleanup["primary_peer_port_released"] is True
    assert cleanup["relaunch_started_with_rotated_session"] is True
    assert cleanup["relaunch_peer_port_released"] is True
    assert cleanup["stale_previous_invite_rejected"] is True
    assert cleanup["new_webjam_runtime_threads"] == []
    assert cleanup["preserved_local_originals_remain"] is True

    serialized = result.report_path.read_text(encoding="utf-8")
    assert "invite_token" not in serialized
    assert "participant_token" not in serialized
    assert "peer_port" not in serialized
    assert "session_id" not in serialized
    cleanup_serialized = result.cleanup_path.read_text(encoding="utf-8")
    assert "invite_token" not in cleanup_serialized
    assert "participant_token" not in cleanup_serialized
    assert '"peer_port"' not in cleanup_serialized
    assert "session_id" not in cleanup_serialized

    ux = report["source_level_ux"]
    assert ux["first_host"] == {
        "step_count": 3,
        "repeated_setup_question_count": 0,
    }
    assert ux["first_join"] == {
        "step_count": 3,
        "repeated_setup_question_count": 0,
    }
    assert ux["returning_host"] == {
        "step_count": 1,
        "repeated_setup_question_count": 0,
    }
    assert ux["returning_join"] == {
        "step_count": 1,
        "repeated_setup_question_count": 0,
    }
    assert all(
        isinstance(ux[key], int) and ux[key] >= 0
        for key in (
            "invite_ready_ms",
            "guest_connect_ms",
            "returning_host_connect_ms",
            "returning_join_connect_ms",
        )
    )
