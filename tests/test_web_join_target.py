import json
import subprocess
import sys
import pytest

from core import web_join_target as target


BUILD = "a" * 40
PRIVATE_PAGE = "https://stalemate-hangout-beta.jeffstory007.workers.dev/join/"


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_private_package_selects_the_protected_community_page(tmp_path, monkeypatch):
    manifest = tmp_path / target.MANIFEST_NAME
    manifest.write_bytes(target.build_target_document("private-beta", BUILD))
    monkeypatch.setattr(target.sys, "frozen", True, raising=False)
    monkeypatch.setattr(target, "_manifest_path", lambda: manifest)
    monkeypatch.setattr(target, "build_id", lambda: BUILD)
    monkeypatch.setenv("WEBJAM_WEB_JOIN_TARGET", "https://untrusted.invalid/join/")

    assert target.web_join_page() == PRIVATE_PAGE


def test_source_default_does_not_read_a_packaged_or_environment_override(monkeypatch):
    monkeypatch.delattr(target.sys, "frozen", raising=False)
    monkeypatch.setenv("WEBJAM_WEB_JOIN_TARGET", "private-beta")

    def unexpected_read():
        pytest.fail("source default must not read a package target")

    monkeypatch.setattr(target, "_manifest_path", unexpected_read)
    assert target.web_join_page() == "https://stalemate.band/join/"


@pytest.mark.parametrize("identifier", ["public", "private-beta"])
def test_manifest_round_trip_is_bound_to_the_exact_build(identifier):
    document = target.build_target_document(identifier, BUILD)
    page = target.parse_target_document(document, BUILD)
    assert page.startswith("https://") and page.endswith("/join/")
    with pytest.raises(target.WebJoinTargetUnavailable):
        target.parse_target_document(document, "b" * 40)


@pytest.mark.parametrize("identifier", [
    "", "PUBLIC", "private-beta\n", "https://untrusted.invalid/join/#secret",
    "//stalemate.band/join/", "public\x00", None, True, {"target": "public"},
])
def test_build_rejects_unregistered_destinations_without_echoing_them(identifier):
    with pytest.raises(target.WebJoinTargetUnavailable) as failure:
        target.build_target_document(identifier, BUILD)
    assert str(failure.value) == target.UNAVAILABLE


@pytest.mark.parametrize("document", [
    b"", b"x" * 513, b"[]", b"null", b"\xff", b"{}",
    _canonical({"version": True, "build": BUILD, "target": "public"}),
    _canonical({"version": 1, "build": BUILD, "target": "private-beta", "url": "https://untrusted.invalid"}),
    _canonical({"version": 1, "build": BUILD, "target": "https://untrusted.invalid"}),
    ('{"version":1,"build":"' + BUILD + '","target":"public","target":"private-beta"}\n').encode(),
])
def test_invalid_package_metadata_fails_closed(document):
    with pytest.raises(target.WebJoinTargetUnavailable) as failure:
        target.parse_target_document(document, BUILD)
    assert str(failure.value) == target.UNAVAILABLE


def test_frozen_missing_or_changed_metadata_has_no_public_fallback(tmp_path, monkeypatch):
    manifest = tmp_path / target.MANIFEST_NAME
    monkeypatch.setattr(target.sys, "frozen", True, raising=False)
    monkeypatch.setattr(target, "_manifest_path", lambda: manifest)
    monkeypatch.setattr(target, "build_id", lambda: BUILD)
    with pytest.raises(target.WebJoinTargetUnavailable):
        target.web_join_page()
    manifest.write_bytes(target.build_target_document("public", "b" * 40))
    with pytest.raises(target.WebJoinTargetUnavailable):
        target.web_join_page()


def test_package_target_rejects_a_symlink_and_directory(tmp_path):
    original = tmp_path / "original.json"
    original.write_bytes(target.build_target_document("public", BUILD))
    symlink = tmp_path / target.MANIFEST_NAME
    symlink.symlink_to(original)
    for path in (symlink, tmp_path):
        with pytest.raises(target.WebJoinTargetUnavailable):
            target.read_target_document(path, BUILD)


def test_private_target_cannot_be_used_for_a_tagged_release():
    with pytest.raises(target.WebJoinTargetUnavailable):
        target.build_target_document("private-beta", BUILD, git_ref="refs/tags/v0.29.0")
    assert target.build_target_document("public", BUILD, git_ref="refs/tags/v0.29.0")
    assert target.build_target_document("private-beta", BUILD, git_ref="refs/heads/codex/private-test")


def test_package_gate_rejects_a_different_target_or_source(tmp_path):
    from tests.support.verify_packaged_web_join_target import verify

    manifest = tmp_path / target.MANIFEST_NAME
    manifest.write_bytes(target.build_target_document("private-beta", BUILD))
    verify(manifest, BUILD, "private-beta")
    for build, identifier in ((BUILD, "public"), ("b" * 40, "private-beta")):
        with pytest.raises(target.WebJoinTargetUnavailable):
            verify(manifest, build, identifier)


@pytest.mark.parametrize("identifier,corrupt,expected_exit", [
    ("private-beta", False, 0), ("public", False, 1), ("private-beta", True, 1),
])
def test_real_package_verifier_exit_and_private_diagnostics(tmp_path, identifier, corrupt, expected_exit):
    manifest = tmp_path / "private-destination-do-not-echo.json"
    manifest.write_bytes(
        b'{"untrusted":"owner-secret-do-not-echo"}' if corrupt
        else target.build_target_document("private-beta", BUILD)
    )
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.verify_packaged_web_join_target",
         "--manifest", str(manifest), "--build-id", BUILD, "--target", identifier],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == expected_exit
    assert "do-not-echo" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    if expected_exit == 0:
        assert result.stdout == "Packaged Web Join destination verified.\n"
        assert not result.stderr
    else:
        assert not result.stdout
        assert result.stderr == "Packaged Web Join destination verification failed.\n"
