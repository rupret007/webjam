"""Per-change changelog fragments in ``changelog.d/``.

``check``   – CI: a pull request must add a non-empty ``changelog.d/*.md``
              fragment or a non-empty ``changelog.d/*.skip`` reason, and must
              not edit ``CHANGELOG.md`` directly.
``combine`` – release time: move every fragment into ``## [Unreleased]`` of
              ``CHANGELOG.md`` (file-name order) and delete the fragments.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAGMENT_DIR = "changelog.d"
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"
IGNORED = {"README.md"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _is_fragment(path: str) -> bool:
    p = Path(path)
    return (
        len(p.parts) == 2
        and p.parts[0] == FRAGMENT_DIR
        and p.name not in IGNORED
        and not p.name.startswith(".")
        and p.suffix in {".md", ".skip"}
    )


def check(base: str = "origin/master", root: Path = REPO_ROOT) -> list[str]:
    merge_base = _git(root, "merge-base", base, "HEAD").strip()
    rows = _git(root, "diff", "--name-status", "--no-renames", merge_base, "HEAD").splitlines()
    problems: list[str] = []
    added: list[str] = []
    for row in rows:
        status, path = row.split("\t", 1)
        if path == CHANGELOG:
            problems.append(
                f"{CHANGELOG} is edited directly; move the entry to {FRAGMENT_DIR}/<topic>.md"
            )
        if status.startswith("A") and _is_fragment(path):
            added.append(path)
    valid = []
    for path in added:
        text = (root / path).read_text(encoding="utf-8").strip()
        if not text:
            problems.append(f"{path} is empty")
        elif path.endswith(".md") and not text.lstrip().startswith("- "):
            problems.append(f"{path} must contain Markdown bullet(s) starting with '- '")
        else:
            valid.append(path)
    if not valid:
        problems.append(
            f"no changelog fragment: add {FRAGMENT_DIR}/<topic>.md, or "
            f"{FRAGMENT_DIR}/<topic>.skip with a one-line reason"
        )
    return problems


def combine(root: Path = REPO_ROOT) -> list[str]:
    directory = root / FRAGMENT_DIR
    fragments = sorted(
        p for p in directory.iterdir()
        if p.is_file() and _is_fragment(f"{FRAGMENT_DIR}/{p.name}")
    ) if directory.is_dir() else []
    entries = [
        p.read_text(encoding="utf-8").strip() for p in fragments if p.suffix == ".md"
    ]
    entries = [e for e in entries if e]
    changelog = root / CHANGELOG
    text = changelog.read_text(encoding="utf-8")
    if entries:
        marker = f"{UNRELEASED}\n"
        if marker not in text:
            raise SystemExit(f"{CHANGELOG} has no '{UNRELEASED}' heading")
        block = "\n".join(entries) + "\n"
        head, tail = text.split(marker, 1)
        tail = tail.lstrip("\n")
        text = f"{head}{marker}\n{block}{tail}"
        changelog.write_text(text, encoding="utf-8")
    for p in fragments:
        p.unlink()
    return [p.name for p in fragments]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("--base", default="origin/master")
    sub.add_parser("combine")
    args = parser.parse_args(argv)
    if args.command == "check":
        problems = check(args.base)
        if problems:
            print("::error::Changelog fragment check failed.")
            for p in problems:
                print(f"  {p}")
            return 1
        print("OK: changelog fragment present.")
        return 0
    moved = combine()
    print(f"Combined {len(moved)} fragment file(s) into {CHANGELOG}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
