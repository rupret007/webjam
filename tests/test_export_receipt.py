from __future__ import annotations

import hashlib
import json

import pytest

from core.export_receipt import ExportReceiptError, verify_export_receipt


def package(root, edited):
    root.mkdir()
    name = "provenance.json" if edited else "webjam-track-export.json"
    (root / name).write_text(json.dumps({"take_id": "exact-take", "gain": 0.75}))
    (root / "audio.wav").write_bytes(b"actual rendered audio")
    if edited:
        (root / "studio-document.json").write_text(json.dumps({"revision": 17, "pan": -0.4}))
    checksum = root / ("SHA256SUMS.txt" if edited else "CHECKSUMS.sha256")
    checksum.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                                for p in sorted(root.iterdir())))
    return checksum


@pytest.mark.parametrize("edited", [False, True])
def test_receipt_proves_actual_files_and_displays_exact_recipe(tmp_path, edited):
    root = tmp_path / "export"
    package(root, edited)
    receipt = verify_export_receipt(root)
    assert receipt.folder == root
    assert receipt.file_count == (3 if edited else 2)
    assert '"gain": 0.75' in receipt.details
    assert "exact-take" in receipt.details
    assert str(root) in receipt.details
    assert "not an external-editor import" in receipt.details
    if edited:
        assert '"revision": 17' in receipt.details


@pytest.mark.parametrize("damage", ["changed", "missing", "symlink", "traversal", "empty", "duplicate", "metadata"])
def test_unverified_export_has_no_receipt(tmp_path, damage):
    root = tmp_path / "export"
    checksums = package(root, False)
    audio = root / "audio.wav"
    if damage == "changed":
        audio.write_bytes(b"changed")
    elif damage == "missing":
        audio.unlink()
    elif damage == "symlink":
        outside = tmp_path / "outside.wav"
        audio.rename(outside)
        audio.symlink_to(outside)
    elif damage == "traversal":
        checksums.write_text("0" * 64 + "  ../outside.wav\n")
    elif damage == "empty":
        checksums.write_text("")
    elif damage == "duplicate":
        checksums.write_text(checksums.read_text() * 2)
    else:
        checksums.write_text(checksums.read_text().splitlines()[0] + "\n")
    with pytest.raises(ExportReceiptError):
        verify_export_receipt(root)
