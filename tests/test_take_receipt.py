"""Semantic tests for the offline Studio export recording receipt."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from core.file_io import atomic_write_text
from core.studio_export import export_studio_arrangement
from core.studio_project import default_studio_document
from core.studio_store import STUDIO_STATE_FILENAME
from core.take_project import (
    AlignmentState,
    ProjectStatus,
    RecoveryStatus,
    SessionEvidence,
    SourceQuality,
    SourceType,
    TakeProject,
    write_take_project,
)
from core.take_receipt import RecordingReceiptError, render_recording_receipt
from tests.test_studio_export import (
    RATE,
    _cross_take_fixture,
    _digest,
    _fixture,
    _id,
    _segment,
    _track,
)


def _export_receipt(tmp_path: Path) -> tuple[str, dict, TakeProject, dict[str, TakeProject]]:
    take_dir, project, document, _sources = _fixture(tmp_path)
    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "exports",
        block_frames=3,
        disk_reserve_bytes=0,
    )
    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    contributing = {project.take_id: project}
    return (
        (result.folder / "RECORDING_RECEIPT.md").read_text(encoding="utf-8"),
        provenance,
        project,
        contributing,
    )


def test_plan_unavailable_with_and_without_fingerprint(tmp_path: Path) -> None:
    text, provenance, project, _contributing = _export_receipt(tmp_path)
    assert (
        "Plan comparison unavailable. This saved take does not include the "
        "original per-source recording plan."
    ) in text
    assert "required" not in text.casefold().split("plan comparison unavailable")[1][:200]
    assert "optional" not in text.casefold().split("plan comparison unavailable")[1][:200]

    fingerprint = "ab" * 32
    with_fingerprint = replace(
        project,
        session_evidence=SessionEvidence(recording_plan_fingerprint=fingerprint),
    )
    provenance_with = dict(provenance)
    text_with = render_recording_receipt(
        with_fingerprint,
        {with_fingerprint.take_id: with_fingerprint},
        provenance_with,
    )
    assert "Plan comparison unavailable." in text_with
    assert fingerprint not in text_with


def test_mixed_source_routes_and_recorded_formats(tmp_path: Path) -> None:
    take_dir = tmp_path / "mixed-sources"
    take_dir.mkdir()
    jamulus_segment, _ = _segment(
        take_dir,
        20,
        np.linspace(0.1, 0.3, 8, dtype=np.float32),
    )
    stereo_samples = np.column_stack(
        (
            np.linspace(0.2, 0.5, 8, dtype=np.float32),
            np.linspace(0.3, 0.6, 8, dtype=np.float32),
        )
    )
    local_segment, _ = _segment(take_dir, 21, stereo_samples)
    shared_segment, _ = _segment(
        take_dir,
        22,
        np.column_stack(
            (
                np.full(8, 0.15, dtype=np.float32),
                np.full(8, 0.12, dtype=np.float32),
            )
        ),
    )
    jamulus = replace(
        _track(10, "Bass", jamulus_segment, order=0),
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
    )
    local = replace(
        _track(11, "Drums", local_segment, order=1),
        source_type=SourceType.LOCAL_ISOLATED,
        quality=SourceQuality.UNVERIFIED,
        alignment=AlignmentState(confidence=1.0, method="test-alignment"),
    )
    shared = replace(
        _track(12, "Backing", shared_segment, order=2),
        source_type=SourceType.LIVE_REFERENCE,
        quality=SourceQuality.REFERENCE,
    )
    local = replace(local, segments=(local_segment,))
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Mixed routes",
        take_name="Take A",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(jamulus, local, shared),
    )
    write_take_project(take_dir, project)
    document = default_studio_document(project)
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "mixed-exports",
        block_frames=2,
        disk_reserve_bytes=0,
    )
    receipt = (result.folder / "RECORDING_RECEIPT.md").read_text(encoding="utf-8")
    assert "3 saved source tracks represented in this package." in receipt
    assert "Jamulus server" in receipt
    assert "Local Original" in receipt
    assert "Shared Track" in receipt
    assert "recorded mono" in receipt
    assert "recorded stereo" in receipt
    assert "| Bass" in receipt
    assert any("Local Original" in line and "| 0 |" in line for line in receipt.splitlines())


def test_unknown_signal_gaps_and_needs_attention_status(tmp_path: Path) -> None:
    take_dir = tmp_path / "attention"
    take_dir.mkdir()
    segment, _ = _segment(take_dir, 20, np.full(8, 0.2, dtype=np.float32))
    segment = replace(segment, has_signal=None, gaps=())
    track = replace(
        _track(10, "Mystery", segment, order=0),
        source_type=SourceType.UNKNOWN,
        quality=SourceQuality.UNVERIFIED,
    )
    project = TakeProject(
        session_id=_id(5),
        take_id=_id(6),
        session_title="Attention",
        take_name="Take needs review",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(track,),
        warnings=("level jumped",),
        errors=("capture stalled",),
        session_evidence=SessionEvidence(
            recovery_status=RecoveryStatus.NEEDS_ATTENTION,
            recovery_notes=("partial recovery", "check alignment"),
        ),
    )
    write_take_project(take_dir, project)
    document = default_studio_document(project)
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "attention-exports",
        block_frames=2,
        disk_reserve_bytes=0,
    )
    receipt = (result.folder / "RECORDING_RECEIPT.md").read_text(encoding="utf-8")
    assert "Needs attention" in receipt
    assert "unknown" in receipt
    mystery_line = next(line for line in receipt.splitlines() if "Mystery" in line)
    assert "| 0 |" in mystery_line
    assert "| unknown |" in mystery_line
    assert "Live dropout" in receipt
    assert "Warnings: 1" in receipt
    assert "Errors: 1" in receipt
    assert "Recovery notes: 2" in receipt


def test_cross_take_rows_stay_distinct_and_reject_mismatched_inputs(
    tmp_path: Path,
) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        catalog,
        _paths,
    ) = _cross_take_fixture(tmp_path)
    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "cross-exports",
        source_catalog=catalog,
        block_frames=2,
        disk_reserve_bytes=0,
    )
    receipt = (result.folder / "RECORDING_RECEIPT.md").read_text(encoding="utf-8")
    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    assert primary.take_id in receipt
    assert alternate.take_id in receipt
    assert receipt.count("Vocal") >= 2
    contributing = {primary.take_id: primary, alternate.take_id: alternate}
    with pytest.raises(RecordingReceiptError, match="take_project_revision"):
        render_recording_receipt(
            replace(primary, revision=primary.revision + 1),
            contributing,
            provenance,
        )
    before_manifest = _digest(primary_root / "webjam-take.json")
    before_alternate = _digest(alternate_root / "webjam-take.json")
    assert before_manifest == _digest(primary_root / "webjam-take.json")
    assert before_alternate == _digest(alternate_root / "webjam-take.json")


def test_labels_are_escaped_without_extra_rows(tmp_path: Path) -> None:
    take_dir = tmp_path / "escaped"
    take_dir.mkdir()
    segment, _ = _segment(take_dir, 20, np.full(8, 0.2, dtype=np.float32))
    evil = '<script>alert("x")</script>\npath: /Users/secret\nwebjam://invite'
    track = _track(10, evil, segment, order=0)
    project = TakeProject(
        session_id=_id(7),
        take_id=_id(8),
        session_title=evil,
        take_name="Take **bold**",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(track,),
    )
    write_take_project(take_dir, project)
    document = default_studio_document(project)
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "escaped-exports",
        block_frames=2,
        disk_reserve_bytes=0,
    )
    receipt = (result.folder / "RECORDING_RECEIPT.md").read_text(encoding="utf-8")
    assert "<script>" not in receipt
    assert "/Users/secret" not in receipt
    assert "webjam://" not in receipt.lower()
    assert receipt.count("| Source |") == 1
    assert not re.search(r"<script", receipt, flags=re.IGNORECASE)
