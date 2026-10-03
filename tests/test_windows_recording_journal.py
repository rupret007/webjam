"""Native Windows journal privacy and recovery; never substitute the recorder."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from core import recording_manifest_journal as journal_module
from core.recording_manifest_journal import RecordingManifestJournal, RecordingManifestJournalError
from core.take_project import RecoveryStatus, SessionEvidence

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows ACL and handle contract")


def assert_native_private_acl(path, *, directory):
    # Inspect independently of the ctypes implementation, including elevated
    # runners whose default owner would otherwise be Administrators.
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", r"""
        $ErrorActionPreference = 'Stop'
        $acl = Get-Acl -LiteralPath $env:WEBJAM_TEST_JOURNAL_PATH
        $sidType = [System.Security.Principal.SecurityIdentifier]
        $rules = @($acl.GetAccessRules($true, $true, $sidType) | ForEach-Object {
            @{sid=$_.IdentityReference.Value; mask=[int]$_.FileSystemRights;
              inherited=$_.IsInherited; inheritance=[int]$_.InheritanceFlags;
              propagation=[int]$_.PropagationFlags; type=[int]$_.AccessControlType}
        })
        @{owner=$acl.GetOwner($sidType).Value;
          user=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
          protected=$acl.AreAccessRulesProtected; rules=$rules} | ConvertTo-Json -Depth 4 -Compress
        """],
        env={**os.environ, "WEBJAM_TEST_JOURNAL_PATH": str(path)},
        capture_output=True, text=True, check=True, timeout=30,
    )
    facts = json.loads(result.stdout)
    assert facts["owner"] == facts["user"]
    assert facts["protected"] is True
    assert len(facts["rules"]) == 2
    assert {rule["sid"] for rule in facts["rules"]} == {facts["user"], "S-1-5-18"}
    for rule in facts["rules"]:
        assert rule["mask"] == 0x1f01ff
        assert rule["type"] == 0 and not rule["inherited"]
        assert rule["inheritance"] == (3 if directory else 0)
        assert rule["propagation"] == 0


def _child_load(journal, take_id):
    result = subprocess.run(
        [sys.executable, "-c", """
import json, os, sys
from core.recording_manifest_journal import RecordingManifestJournal
result = RecordingManifestJournal(sys.argv[1]).load(sys.argv[2])
assert result is not None and result.trusted
print(json.dumps({'pid': os.getpid(), 'evidence': result.evidence.to_dict(),
                  'plan': result.plan.to_private_dict() if result.plan else None}))
""", str(journal.takes_root), take_id],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=True, timeout=30,
    )
    facts = json.loads(result.stdout)
    assert facts.pop("pid") != os.getpid()
    return facts


def test_native_private_checkpoint_survives_a_fresh_process(tmp_path):
    from tests.test_recording_manifest_journal import _evidence_for_plan, _plan
    from dataclasses import replace

    journal = RecordingManifestJournal(tmp_path / "takes")
    take_id = str(uuid.uuid4())
    plan = _plan(take_id)
    evidence = _evidence_for_plan(plan)
    path = journal.create(take_id, evidence, plan=plan)
    assert_native_private_acl(journal.directory, directory=True)
    assert_native_private_acl(path, directory=False)
    assert journal.load(take_id).trusted
    updated = replace(evidence, ended_utc="2026-10-03T00:00:00Z")
    journal.update(take_id, updated, plan=plan)
    assert_native_private_acl(path, directory=False)
    assert _child_load(journal, take_id) == {"evidence": updated.to_dict(), "plan": plan.to_private_dict()}
    assert journal.remove(take_id)
    assert not journal.remove(take_id) and journal.load(take_id) is None


@pytest.mark.parametrize("directory", [False, True])
def test_expanded_acl_blocks_trust_and_mutation(tmp_path, directory):
    journal = RecordingManifestJournal(tmp_path)
    take_id = str(uuid.uuid4())
    path = journal.create(take_id, SessionEvidence())
    before = path.read_bytes()
    target = journal.directory if directory else path
    subprocess.run(["icacls", str(target), "/grant", "*S-1-1-0:(R)"],
                   capture_output=True, check=True, timeout=30)
    try:
        loaded = journal.load(take_id)
        assert loaded is not None and not loaded.trusted
        assert loaded.evidence.recovery_status is RecoveryStatus.NEEDS_ATTENTION
        with pytest.raises((OSError, RecordingManifestJournalError)):
            journal.update(take_id, SessionEvidence(ended_utc="2026-10-03T00:00:00Z"))
        if directory:
            with pytest.raises((OSError, RecordingManifestJournalError)):
                journal.create(str(uuid.uuid4()), SessionEvidence())
        assert path.read_bytes() == before
        assert not list(journal.directory.glob(".recording-evidence-*.tmp"))
    finally:
        subprocess.run(["icacls", str(target), "/remove:g", "*S-1-1-0"],
                       capture_output=True, check=True, timeout=30)
    assert journal.load(take_id).trusted


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("substitution", ["private_bytes", "external_hardlink"])
def test_publication_rechecks_private_exact_evidence(tmp_path, monkeypatch, operation, substitution):
    journal = RecordingManifestJournal(tmp_path)
    take_id = str(uuid.uuid4())
    if operation == "update":
        journal.create(take_id, SessionEvidence())
    outside = tmp_path / "unrelated.txt"
    outside.write_bytes(b"Preserve unrelated content")
    link, replace = os.link, os.replace
    publish = link if operation == "create" else replace

    def substitute(source, target):
        if substitution == "private_bytes":
            # Keep the valid private ACL, but corrupt the intended checkpoint.
            Path(source).write_bytes(b"{}")
        else:
            Path(source).unlink()
            link(outside, source)
        publish(source, target)

    monkeypatch.setattr(journal_module.os, "link" if operation == "create" else "replace", substitute)
    with pytest.raises(RecordingManifestJournalError, match="verify the published"):
        getattr(journal, operation)(take_id, SessionEvidence())
    assert outside.read_bytes() == b"Preserve unrelated content"
    assert journal.path_for(take_id).exists()  # Uncertain evidence is retained.
    assert not journal.load(take_id).trusted
    assert not list(journal.directory.glob(".recording-evidence-*.tmp"))


def test_directory_guard_blocks_rename_and_releases_handle(tmp_path):
    from core.windows_private_journal import private_directory

    directory, moved = tmp_path / "private", tmp_path / "moved"
    with private_directory(directory, create=True):
        with pytest.raises(OSError):
            directory.rename(moved)
        assert directory.is_dir() and not moved.exists()
    directory.rename(moved)
    with private_directory(moved):
        pass


def test_native_descriptor_is_not_inherited_and_duplicate_creation_is_safe(tmp_path):
    from core.windows_private_journal import private_directory, private_file_descriptor

    directory = tmp_path / "private"
    with private_directory(directory, create=True):
        path = directory / "checkpoint"
        descriptor = private_file_descriptor(path, create=True)
        try:
            assert not os.get_inheritable(descriptor)
            os.write(descriptor, b"Keep original bytes")
        finally:
            os.close(descriptor)
        with pytest.raises(FileExistsError):
            private_file_descriptor(path, create=True)
        assert path.read_bytes() == b"Keep original bytes"


def test_extra_hardlink_is_untrusted_and_preserved(tmp_path):
    journal = RecordingManifestJournal(tmp_path)
    take_id = str(uuid.uuid4())
    path = journal.create(take_id, SessionEvidence())
    outside = tmp_path / "alias.json"
    os.link(path, outside)
    before = outside.read_bytes()
    assert not journal.load(take_id).trusted
    with pytest.raises((OSError, RecordingManifestJournalError)):
        journal.remove(take_id)
    assert path.read_bytes() == outside.read_bytes() == before
    outside.unlink()
    assert journal.load(take_id).trusted


def test_junction_directory_never_mutates_its_target(tmp_path):
    original = RecordingManifestJournal(tmp_path / "original")
    take_id = str(uuid.uuid4())
    path = original.create(take_id, SessionEvidence())
    before = path.read_bytes()
    redirected = RecordingManifestJournal(tmp_path / "redirected")
    redirected.takes_root.mkdir()
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", """
        $ErrorActionPreference = 'Stop'
        New-Item -ItemType Junction -Path $env:WEBJAM_TEST_JOURNAL_LINK -Target $env:WEBJAM_TEST_JOURNAL_TARGET | Out-Null
        """],
        env={**os.environ, "WEBJAM_TEST_JOURNAL_LINK": str(redirected.directory),
             "WEBJAM_TEST_JOURNAL_TARGET": str(original.directory)},
        capture_output=True, check=True, timeout=30,
    )
    try:
        assert not redirected.load(take_id).trusted
        with pytest.raises((OSError, RecordingManifestJournalError)):
            redirected.create(str(uuid.uuid4()), SessionEvidence())
        with pytest.raises((OSError, RecordingManifestJournalError)):
            redirected.update(take_id, SessionEvidence())
        assert path.read_bytes() == before
        assert list(original.directory.iterdir()) == [path]
    finally:
        os.rmdir(redirected.directory)  # Remove the junction itself only.
    assert original.load(take_id).trusted
