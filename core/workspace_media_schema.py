"""Dependency-free validation of portable content proofs (never filesystem I/O)."""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

MAX_PACKAGE_FILES = 4096
MAX_PACKAGE_BYTES = 64 * 1024**3
MAX_PACKAGE_METADATA_BYTES = 16 * 1024**2
ROLES = frozenset({"manifest", "audio", "review", "studio", "art"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z", re.I)


def digest(value: object) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("Media checksum must be a lowercase SHA256 digest.")
    return value


def relative_path(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024
            or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or unicodedata.normalize("NFC", value) != value):
        raise ValueError("Media member must use a bounded canonical relative path.")
    parts = value.split("/")
    if (len(parts) > 16 or PurePosixPath(value).is_absolute() or any(
            p in {"", ".", ".."} or any(c in p for c in ':<>"|?*') or p.endswith((".", " "))
            or _DEVICE.fullmatch(p) for p in parts)):
        raise ValueError("Media member path is unsafe on a supported platform.")
    return value


def file_inventory(files: object) -> None:
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_PACKAGE_FILES:
        raise ValueError("Media inventory is empty or exceeds its file limit.")
    seen, parents = set(), set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size_bytes", "sha256", "role"}:
            raise ValueError("Media inventory fields are unsupported.")
        name = relative_path(item["path"])
        folded = name.casefold()
        components = folded.split("/")
        ancestors = {"/".join(components[:i]) for i in range(1, len(components))}
        if folded in seen or folded in parents or ancestors & seen:
            raise ValueError("Media paths duplicate or collide.")
        seen.add(folded)
        parents.update(ancestors)
        size = item["size_bytes"]
        if type(size) is not int or not 0 <= size <= MAX_PACKAGE_BYTES:
            raise ValueError("Media size exceeds the package limit.")
        total += size
        digest(item["sha256"])
        if not isinstance(item["role"], str) or item["role"] not in ROLES:
            raise ValueError("Media role is unsupported.")
    if total > MAX_PACKAGE_BYTES:
        raise ValueError("Media inventory exceeds the expanded package limit.")


def media_provenance(record) -> None:
    proofs = record.media_provenance
    if not isinstance(proofs, (list, tuple)) or len(proofs) > MAX_PACKAGE_FILES:
        raise ValueError("Workspace media provenance is invalid.")
    seen = set()
    count = total = 0
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != {"kind", "reference_id", "files", "dependencies"}:
            raise ValueError("Workspace media proof fields are unsupported.")
        kind, key = proof["kind"], proof["reference_id"]
        if not isinstance(kind, str) or kind not in {"take", "art"} or not isinstance(key, str) or not key or len(key.encode("utf-8")) > 100 or "\0" in key:
            raise ValueError("Workspace media reference is invalid.")
        if (kind, key) in seen:
            raise ValueError("Workspace media reference is duplicated.")
        seen.add((kind, key))
        file_inventory(proof["files"])
        count += len(proof["files"])
        total += sum(f["size_bytes"] for f in proof["files"])
        dependencies = proof["dependencies"]
        if (not isinstance(dependencies, list) or len(dependencies) > 128
                or any(not isinstance(v, str) or not v or len(v.encode("utf-8")) > 100 or "\0" in v for v in dependencies)
                or len(set(dependencies)) != len(dependencies) or key in dependencies):
            raise ValueError("Workspace take dependencies are invalid.")
        if kind == "art":
            refs = record.art.get("references", [])
            if not isinstance(refs, list) or any(not isinstance(r, dict) for r in refs):
                raise ValueError("Art proof reference list is invalid.")
            matches = [r for r in refs if r.get("id") == key and r.get("kind") == "file"]
            if (len(matches) > 1 or any(r.get("id") == key and r.get("kind") != "file" for r in refs)
                    or len(proof["files"]) != 1 or proof["files"][0]["role"] != "art" or dependencies):
                raise ValueError("Art media proof does not match its file reference.")
        else:
            refs = [r for r in record.take_links if r.get("take_id") == key]
            manifests = [f for f in proof["files"] if f["role"] == "manifest"]
            if (len(manifests) != 1 or manifests[0]["path"] != "webjam-take.json"
                    or not any(f["role"] == "audio" for f in proof["files"])
                    or any(r.get("source_identity") != manifests[0]["sha256"] for r in refs)):
                raise ValueError("Take media proof does not match its manifest identity.")
            for item in proof["files"]:
                role, path = item["role"], item["path"]
                if (role == "art" or role == "review" and path != ".webjam-review.json"
                        or role == "studio" and path not in {".webjam-studio-state.json", ".webjam-studio-state.json.bak", ".webjam-studio-state.v1.json.bak"}
                        or role == "audio" and path.rsplit(".", 1)[-1].lower() not in {"wav", "flac", "aif", "aiff"}):
                    raise ValueError("Take sidecar or media path does not match its role.")
    if count > MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES:
        raise ValueError("Workspace media proofs exceed the inventory limit.")
