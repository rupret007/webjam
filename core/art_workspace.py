"""Local Art project context; references are remembered, never opened on load."""
from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


def _text(value: object, maximum: int = 200_000) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ValueError("Art workspace text is invalid or too long.")
    return value


def normalize_art_workspace(payload: dict) -> dict:
    """Validate saved context without touching referenced files or services."""
    if not isinstance(payload, dict):
        raise ValueError("Art workspace must be an object.")
    if not payload:
        payload = {"version": 1}
    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise ValueError("Art workspace version is unsupported.")
    result = {"version": 1}
    for key in ("brief", "progress", "next_steps"):
        result[key] = _text(payload.get(key, ""))
    references = payload.get("references", [])
    bookmarks = payload.get("bookmarks", [])
    if not isinstance(references, list) or len(references) > 100:
        raise ValueError("Keep at most 100 project references.")
    if not isinstance(bookmarks, list) or len(bookmarks) > 500:
        raise ValueError("Keep at most 500 lesson bookmarks.")
    result["references"] = []
    ids = set()
    for entry in references:
        if not isinstance(entry, dict):
            raise ValueError("Invalid project reference.")
        key = _text(entry.get("id"), 64)
        kind = entry.get("kind")
        if not key or key in ids or kind not in {"file", "url"}:
            raise ValueError("Invalid project reference identity.")
        ids.add(key)
        locator = _text(entry.get("locator"), 8192)
        if not locator or (kind == "url" and not valid_reference_url(locator)):
            raise ValueError("Use a local file or an ordinary http/https reference.")
        result["references"].append({"id": key, "kind": kind,
            "title": _text(entry.get("title", "Reference"), 512), "locator": locator})
    result["bookmarks"] = []
    bookmark_ids = set()
    for entry in bookmarks:
        if not isinstance(entry, dict):
            raise ValueError("Invalid lesson bookmark.")
        key = _text(entry.get("id"), 64)
        if not key or key in bookmark_ids or entry.get("reference_id") not in ids:
            raise ValueError("Lesson bookmark reference is missing.")
        bookmark_ids.add(key)
        seconds = entry.get("seconds")
        if (type(seconds) not in {int, float} or not math.isfinite(seconds)
                or not 0 <= seconds <= 86400):
            raise ValueError("Lesson position must be between 0 and 86400 seconds.")
        result["bookmarks"].append({"id": key, "reference_id": entry["reference_id"],
            "seconds": float(seconds), "note": _text(entry.get("note", ""), 4000)})
    return result


def valid_reference_url(url: str) -> bool:
    if not isinstance(url, str) or any(character.isspace() for character in url):
        return False
    try:
        parsed = urlparse(url)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def make_reference(locator: str, *, kind: str, title: str = "") -> dict:
    entry = {"id": uuid4().hex, "kind": kind, "locator": locator,
             "title": title or (Path(locator).name if kind == "file" else locator)}
    return normalize_art_workspace({"version": 1, "references": [entry]})["references"][0]


def art_summary(title: str, payload: dict) -> str:
    """Portable progress handoff; private local reference paths are excluded."""
    value = normalize_art_workspace(payload)
    lines = [f"# {title or 'Art workspace'}", "", "Art project summary"]
    for key, heading in (("brief", "Project brief"), ("progress", "Progress"),
                         ("next_steps", "Next steps")):
        if value[key].strip():
            lines.extend(["", f"## {heading}", value[key]])
    if value["references"]:
        lines.extend(["", "## References"])
        for ref in value["references"]:
            lines.append(f"- {ref['title']}")
    if value["bookmarks"]:
        titles = {item["id"]: item["title"] for item in value["references"]}
        lines.extend(["", "## Lesson bookmarks"])
        for mark in value["bookmarks"]:
            seconds = int(mark["seconds"])
            lines.append(f"- {titles[mark['reference_id']]} — {seconds // 60}:{seconds % 60:02d}: {mark['note']}")
    return "\n".join(lines) + "\n"
