"""A Mac host cannot pass packaging through its machine's unrelated Opus install."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.verify_web_join_codec import OPUS_SOURCE_SHA256, verify


def fixture_bundle(tmp_path):
    root = tmp_path / "WebJam.app"
    lib = root / "Contents/Frameworks/libwebjoin_opus.dylib"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"fixture; never loaded by a real codec")
    notices = root / "Contents/Resources/THIRD_PARTY_LICENSES/web-join"
    notices.mkdir(parents=True)
    (notices / "OPUS-COPYING.txt").write_text("Test license fixture")
    (notices / "OPUS-PROVENANCE.txt").write_text(
        f"opus=1.5.2\nsource_sha256={OPUS_SOURCE_SHA256}\nconfigure=--enable-custom-modes\n")
    return root, lib, notices


def forbidden(*args, **kwargs):
    pytest.fail("A missing or unowned dependency reached native execution")


def test_missing_bundle_codec_cannot_fall_back_to_system_opus(tmp_path):
    root, lib, _ = fixture_bundle(tmp_path)
    lib.unlink()
    with pytest.raises(ValueError, match="missing"):
        verify(root, "macos-x64", codec_factory=forbidden, run=forbidden)


def test_codec_symlink_cannot_escape_its_bundle(tmp_path):
    root, lib, _ = fixture_bundle(tmp_path)
    outside = tmp_path / "unrelated.dylib"
    outside.write_bytes(lib.read_bytes())
    lib.unlink()
    lib.symlink_to(outside)
    with pytest.raises(ValueError, match="owned"):
        verify(root, "macos-x64", codec_factory=forbidden, run=forbidden)


def test_wrong_native_architecture_is_refused_before_codec_allocation(tmp_path):
    root, _, _ = fixture_bundle(tmp_path)
    with pytest.raises(ValueError, match="architecture"):
        verify(root, "macos-x64", codec_factory=forbidden,
               run=lambda *args, **kwargs: SimpleNamespace(stdout="arm64\n"))


def test_ordinary_opus_provenance_is_not_a_custom_codec(tmp_path):
    root, _, notices = fixture_bundle(tmp_path)
    (notices / "OPUS-PROVENANCE.txt").write_text("opus=1.5.2\nconfigure=--disable-custom-modes\n")
    with pytest.raises(ValueError, match="provenance"):
        verify(root, "macos-x64", codec_factory=forbidden, run=forbidden)


def test_packaging_roundtrip_uses_actual_bundle_path_and_always_closes(tmp_path):
    root, lib, _ = fixture_bundle(tmp_path)
    calls = []
    class Codec:
        def __init__(self, path):
            assert Path(path) == lib
        def encode_jamulus(self, pcm):
            assert len(pcm) == 512
            return bytes(106)
        def decode_jamulus(self, encoded):
            return bytes(512)
        def encode_web(self, pcm):
            raise RuntimeError("native codec rejected a frame")
        def close(self):
            calls.append("closed")
    with pytest.raises(RuntimeError, match="rejected"):
        verify(root, "macos-x64", codec_factory=Codec,
               run=lambda *args, **kwargs: SimpleNamespace(stdout="x86_64\n"))
    assert calls == ["closed"]
