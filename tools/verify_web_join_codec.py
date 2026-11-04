"""Exercise the actual bundled custom Opus codec without devices or networking."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.web_join_bridge import OpusCodec  # noqa: E402

OPUS_SOURCE_SHA256 = "65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1"


def verify(bundle: Path, target: str, *, codec_factory=OpusCodec, run=subprocess.run) -> dict:
    expected = {"macos-arm64": "arm64", "macos-x64": "x86_64"}.get(target)
    if expected is None:
        raise ValueError("This codec check requires a supported Mac host target")
    root = bundle.resolve(strict=True)
    library = root / "Contents/Frameworks/libwebjoin_opus.dylib"
    notices = root / "Contents/Resources/THIRD_PARTY_LICENSES/web-join"
    for path in [library, notices / "OPUS-COPYING.txt", notices / "OPUS-PROVENANCE.txt"]:
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("Mac host bundle is missing its owned codec, license or provenance")
    license_text = (notices / "OPUS-COPYING.txt").read_text()
    provenance = (notices / "OPUS-PROVENANCE.txt").read_text()
    if not license_text.strip() or not all(value in provenance for value in (
        "opus=1.5.2", f"source_sha256={OPUS_SOURCE_SHA256}", "--enable-custom-modes",
    )):
        raise ValueError("Bundled codec provenance does not match the pinned custom-mode build")
    result = run(["/usr/bin/lipo", "-archs", str(library)], check=True,
                 capture_output=True, text=True)
    if result.stdout.split() != [expected]:
        raise ValueError("Bundled browser-guest codec has the wrong architecture")
    codec = codec_factory(str(library))
    try:
        jam = codec.encode_jamulus(bytes(128 * 4))
        if len(jam) != 106 or len(codec.decode_jamulus(jam)) != 128 * 4:
            raise ValueError("Bundled codec cannot round-trip the actual Jamulus frame size")
        web = codec.encode_web(bytes(960 * 4))
        if not 1 <= len(web) <= 1275 or len(codec.decode_web(web)) != 960 * 4:
            raise ValueError("Bundled codec cannot round-trip a browser audio frame")
    finally:
        codec.close()
    return {"target": target, "codecSha256": hashlib.sha256(library.read_bytes()).hexdigest(),
            "opusSourceSha256": OPUS_SOURCE_SHA256, "jamulusFrames": 128,
            "browserFrames": 960, "sampleRate": 48000,
            "hardwareCaptureOpened": False, "networkOpened": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=["macos-arm64", "macos-x64"])
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.target)))
