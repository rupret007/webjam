"""Synthetic RPC/audio boundary around the production recording coordinator.

No Jamulus process, socket, microphone or physical output is used. A one-host
roster fixture supplies the external identity observations; an in-memory RPC
server writes native-shaped WAV/LOF files. Readiness, exact plan binding,
recorder workers, manifest publication, empty guest-inventory reconciliation
and library completion notifications execute their production implementations.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core.jamulus_roster_identity import JamulusCommonProfile
from core.recording_manifest_journal import RecordingManifestJournal
from core.session_transfer_runtime import HostPeerSession
from core.take_library import load_take
from core.take_project import load_take_project
from services.session_workspace_packaged_smoke import _require, _wait
from tests.test_server_rpc_and_record_button import _hosted_readiness_fixture
from webjam_qt.controllers.recording_coordinator import RecorderPhase


def record_completed_takes(app, controller, root: Path) -> tuple[list[Path], dict]:
    fixture = _hosted_readiness_fixture((JamulusCommonProfile("Host", 3, "Chicago", 2),))
    takes_root = Path(controller.settings.takes_directory)
    takes_root.mkdir(exist_ok=True)
    secret_path = Path(controller.settings.server_rpc_secret_file)
    secret_path.write_text("controlled-recording-secret\n", encoding="utf-8")
    secret_path.chmod(0o600)
    events, errors, generated, accepted_plans = [], [], [], []
    state = {"enabled": False}

    # The host has no guests or isolated-input obligations. Use the real
    # reconciliation writer with an explicitly empty controlled inventory.
    reconciliation = HostPeerSession()
    reconciliation.transfers = SimpleNamespace(inventory=lambda _take_id: ())
    reconciliation.registry = SimpleNamespace(participants=lambda: ())
    peer = fixture.host_peer
    peer.begin_take = lambda take_id, **kw: events.append(("started", take_id))
    peer.begin_take_finalization = lambda take_id, **kw: (events.append(("finalizing", take_id)) or True)
    peer.finish_take = lambda take_id, **kw: events.append(("finished", take_id, kw["needs_attention"]))
    peer.register_take = reconciliation.register_take
    peer.wait_for_initial_take_inventory = lambda take_id, **kw: True
    peer.reconcile_take = reconciliation.reconcile_take

    class SyntheticRpc:
        def __init__(self, *, port, secret, **_kwargs):
            _require(port == controller.settings.server_rpc_port
                     and secret == "controlled-recording-secret", "recorder binding changed")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_clients(self):
            return deepcopy(fixture.payload)

        def start_recording(self):
            _require(not state["enabled"], "duplicate recorder start")
            number = len(generated) + 1
            path = takes_root / f"Recorded take {number}"
            path.mkdir()
            native_name = "Host-127_0_0_1_50000-0-1.wav"
            frames = np.arange(24_000, dtype=np.float32)
            sf.write(path / native_name, 0.25 * np.sin(frames * (2 * np.pi * (220 + number * 110) / 48_000)),
                     48_000, subtype="PCM_16")
            (path / "take.lof").write_text(f'file "{native_name}" offset 0.00000000000000\n', encoding="utf-8")
            generated.append(path)
            state["enabled"] = True
            return True

        def stop_recording(self):
            _require(state["enabled"], "recorder stop without a start")
            state["enabled"] = False
            return True

        def get_recorder_status(self):
            return {"enabled": state["enabled"]}

    def accept_readiness(presentation):
        _require(presentation.can_start, "controlled source plan was blocked")
        accepted_plans.append(controller.recording._recording_plan)
        return True

    with (
        patch.object(controller, "host_peer", peer),
        patch.object(controller, "participants", fixture.participants),
        patch.object(controller, "_primary_ordered_roster_proof", fixture.proof),
        patch.object(controller.audio, "connected", True),
        patch.object(controller.jamulus, "ordered_roster_proof_for", return_value=fixture.proof),
        patch.object(controller, "_confirm_recording_readiness", accept_readiness),
        patch.object(controller, "_show_actionable_error", lambda *a, **kw: errors.append((a, kw))),
        patch("core.jamulus_server_rpc.JamulusServerRpc", SyntheticRpc),
    ):
        controller.session_library.start_session()
        for number in range(1, 3):
            controller.recording.on_record_requested()
            _wait(app, lambda: controller._recorder_armed or bool(errors),
                  "controlled recorder did not confirm start")
            _require(not errors, f"controlled recording readiness failed: {errors}")
            take_id = controller.recording._take_id
            _require(take_id and len(generated) == number, "start did not allocate one distinct take")
            reservation = controller.session_library.current.take_links[-1]
            _require(reservation["take_id"] == take_id and reservation["status"] == "pending",
                     "recording start did not reserve its workspace take")
            _require(not (generated[-1] / "webjam-take.json").exists(),
                     "the synthetic recorder created a completed manifest")
            controller.recording.on_record_requested()
            _wait(app, lambda: not controller.recording._take_id or bool(errors),
                  "production finalization did not retire the take", timeout=20)
            _require(not errors and controller.recording.phase is RecorderPhase.COMPLETE,
                     "production finalization did not report a complete take")
            take = load_take(generated[-1])
            project = load_take_project(generated[-1])
            _require(take.take_id == take_id and not take.manifest_errors
                     and controller.recording.last_completed_take == take.path,
                     "publication lost the exact active take identity")
            _require(project.session_evidence.started_utc and project.session_evidence.ended_utc,
                     "published take lacks observed start/stop evidence")
            _require(RecordingManifestJournal(takes_root).load(take_id) is None,
                     "published take retained its unfinished evidence journal")
            link = controller.session_library.current.take_links[-1]
            _require(link["take_id"] == take_id and link["validated"] and link["status"] == "complete",
                     "production completion did not update the reserved library link")
    ids = [load_take(path).take_id for path in generated]
    _require(len(set(ids)) == 2 and len(accepted_plans) == 2, "recording plans or take identities collapsed")
    _require(events == [event for take_id in ids for event in
                       (("started", take_id), ("finalizing", take_id), ("finished", take_id, False))],
             "record/finalization notifications were missing, duplicated or out of order")
    return generated, {"coordinator_recordings": 2, "production_publications": 2,
                       "synthetic_rpc": True, "physical_audio": "not_run"}
