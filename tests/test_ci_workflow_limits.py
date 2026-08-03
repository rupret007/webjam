"""Keep CI parseable by GitHub, not just by PyYAML.

GitHub rejects a workflow whose single expression exceeds 21,000
characters. It refuses the file before creating any job, so the only
symptom is a run that fails in 0 seconds with no logs, no annotations, and
no failing step -- while the file parses locally and looks identical to the
last one that worked.

That cost days of CI here: every push from 2026-07-30 died this way after a
step grew past the ceiling. A local check turns a silent, near-undiagnosable
outage into a test failure that names the step.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = ROOT / ".github" / "workflows"


def _workflow_paths(directory: Path = WORKFLOW_DIRECTORY) -> tuple[Path, ...]:
    """Discover both workflow suffixes accepted by GitHub Actions."""

    return tuple(
        sorted(
            (*directory.glob("*.yml"), *directory.glob("*.yaml")),
            key=lambda path: path.name,
        )
    )


WORKFLOWS = _workflow_paths()

# GitHub's hard limit for one expression.
GITHUB_MAX_EXPRESSION = 21_000
# Fail earlier than GitHub does, so a step that is merely close to the
# ceiling is caught while it can still be fixed deliberately.
SAFE_MAX = 20_500


class _SourceLine(NamedTuple):
    text: str
    has_break: bool


_RUN_KEY = re.compile(
    r"^(?P<indent> *)(?P<sequence>-\s+)?run\s*:\s*(?P<value>.*)$"
)
_STRUCTURAL_LINE = re.compile(r"^ *(?:-\s+)?(?P<body>.*)$")
_ORDINARY_MAPPING = re.compile(
    r"^[A-Za-z0-9_.-]+\s*:\s*(?P<value>.*)$"
)
_SAFE_FLOW_SEQUENCE = re.compile(
    r"^\[\s*(?:[A-Za-z0-9_.-]+\s*(?:,\s*[A-Za-z0-9_.-]+\s*)*)?\]"
    r"\s*(?:#.*)?$"
)
_MERGE_KEY = re.compile(r"^<<\s*:")
_SAFE_MAPPING_KEY = re.compile(
    r"^(?P<indent> *)(?P<sequence>-\s+)?"
    r"[A-Za-z0-9_.-]+\s*:\s*(?P<value>.*)$"
)
_BLOCK_HEADER = re.compile(
    r"^(?P<style>[|>])"
    r"(?:(?P<chomp_first>[+-])(?P<indent_second>[1-9])?"
    r"|(?P<indent_first>[1-9])(?P<chomp_second>[+-])?)?"
    r"\s*(?:#.*)?$"
)

_DOUBLE_QUOTED_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}


def _source_lines(text: str) -> list[_SourceLine]:
    """Split YAML source while retaining whether each line had a line break."""

    result: list[_SourceLine] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            result.append(_SourceLine(line[:-2], True))
        elif line.endswith(("\n", "\r")):
            result.append(_SourceLine(line[:-1], True))
        else:
            result.append(_SourceLine(line, False))
    return result


def _decode_double_quoted(value: str) -> str:
    """Decode a one-line YAML double-quoted scalar without a YAML dependency."""

    decoded: list[str] = []
    index = 1
    while index < len(value):
        char = value[index]
        if char == '"':
            remainder = value[index + 1 :].strip()
            if remainder and not remainder.startswith("#"):
                raise AssertionError(
                    f"unexpected content after double-quoted run scalar: {remainder!r}"
                )
            return "".join(decoded)
        if char != "\\":
            decoded.append(char)
            index += 1
            continue

        index += 1
        if index >= len(value):
            raise AssertionError("unterminated escape in double-quoted run scalar")
        escape = value[index]
        if escape in _DOUBLE_QUOTED_ESCAPES:
            decoded.append(_DOUBLE_QUOTED_ESCAPES[escape])
            index += 1
            continue
        widths = {"x": 2, "u": 4, "U": 8}
        width = widths.get(escape)
        if width is None:
            raise AssertionError(
                f"unsupported YAML escape in double-quoted run scalar: \\{escape}"
            )
        digits = value[index + 1 : index + 1 + width]
        if len(digits) != width or not all(
            char in "0123456789abcdefABCDEF" for char in digits
        ):
            raise AssertionError(
                f"invalid Unicode escape in double-quoted run scalar: \\{escape}{digits}"
            )
        try:
            code_point = int(digits, 16)
            if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
                raise ValueError
            decoded.append(chr(code_point))
        except ValueError as exc:
            raise AssertionError(
                f"invalid Unicode code point in run scalar: \\{escape}{digits}"
            ) from exc
        index += width + 1

    raise AssertionError("unterminated double-quoted run scalar")


def _decode_single_quoted(value: str) -> str:
    """Decode a one-line YAML single-quoted scalar."""

    decoded: list[str] = []
    index = 1
    while index < len(value):
        char = value[index]
        if char != "'":
            decoded.append(char)
            index += 1
            continue
        if index + 1 < len(value) and value[index + 1] == "'":
            decoded.append("'")
            index += 2
            continue
        remainder = value[index + 1 :].strip()
        if remainder and not remainder.startswith("#"):
            raise AssertionError(
                f"unexpected content after single-quoted run scalar: {remainder!r}"
            )
        return "".join(decoded)

    raise AssertionError("unterminated single-quoted run scalar")


def _decode_plain(value: str) -> str:
    """Decode the one-line plain-scalar subset used by workflow `run` keys."""

    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            value = value[:index]
            break
    return value.rstrip()


def _decode_inline_scalar(value: str) -> str:
    value = value.lstrip()
    if value.startswith('"'):
        return _decode_double_quoted(value)
    if value.startswith("'"):
        return _decode_single_quoted(value)
    return _decode_plain(value)


def _reject_unsupported_yaml_syntax(line: str, path: Path) -> None:
    """Enforce a lexical subset in which no computed key can become ``run``.

    Supporting YAML aliases, tags, complex/quoted keys, and flow mappings
    would require a complete YAML resolver. They are unnecessary in these
    workflows, so rejecting those structural forms is safer than guessing at
    a key and silently missing a GitHub-sized command. Block-scalar contents
    never reach this function because their decoder advances past the body.
    """

    match = _STRUCTURAL_LINE.match(line)
    body = match.group("body") if match is not None else ""
    if not body or body.startswith("#"):
        return
    if body.startswith(("'", '"', "[", "{", "?", "&", "*", "!")) or (
        _MERGE_KEY.match(body) is not None
    ):
        raise AssertionError(
            f"unsupported YAML property syntax in {path}; use ordinary block "
            "mapping keys without aliases, tags, anchors, quoted/complex keys, "
            "or flow mappings so the dependency-free run guard can prove them"
        )
    mapping = _ORDINARY_MAPPING.match(body)
    if mapping is None:
        return
    value = mapping.group("value").lstrip()
    if value.startswith("[") and _SAFE_FLOW_SEQUENCE.fullmatch(value):
        return
    if value.startswith(("&", "*", "!", "[", "{")):
        raise AssertionError(
            f"unsupported YAML property value in {path}; aliases, tags, anchors, "
            "and flow mappings are outside the dependency-free run guard"
        )


def _inline_scalar_has_continuation(
    lines: list[_SourceLine],
    header_index: int,
    parent_indent: int,
) -> bool:
    """Return whether an inline scalar continues on a more-indented line."""

    for candidate in lines[header_index + 1 :]:
        stripped = candidate.text.strip()
        if not stripped or candidate.text.lstrip().startswith("#"):
            continue
        indentation = len(candidate.text) - len(candidate.text.lstrip(" "))
        return indentation > parent_indent
    return False


def _folded_break(
    contents: list[str],
    more_indented: list[bool],
    index: int,
) -> str:
    """Return YAML's decoded separator after one folded content line."""

    current = contents[index]
    following = contents[index + 1]
    if current and following:
        return "\n" if more_indented[index] or more_indented[index + 1] else " "
    if following == "":
        return "\n"
    if more_indented[index + 1]:
        return "\n"

    # The last empty line between two ordinary text lines supplies no
    # additional break; the break before the empty run already represents it.
    previous_nonempty = index - 1
    while previous_nonempty >= 0 and contents[previous_nonempty] == "":
        previous_nonempty -= 1
    if previous_nonempty < 0 or more_indented[previous_nonempty]:
        return "\n"
    return ""


def _decode_block_scalar(
    lines: list[_SourceLine],
    header_index: int,
    parent_indent: int,
    header: re.Match[str],
) -> tuple[str, int]:
    """Decode one YAML literal/folded block and return its next line index."""

    body, index = _block_scalar_body(lines, header_index, parent_indent)

    explicit_indent = header.group("indent_first") or header.group("indent_second")
    if explicit_indent is not None:
        content_indent = parent_indent + int(explicit_indent)
    else:
        content_indent = next(
            (
                len(line.text) - len(line.text.lstrip(" "))
                for line in body
                if line.text.strip()
            ),
            parent_indent + 1,
        )

    chomp = header.group("chomp_first") or header.group("chomp_second") or ""
    whitespace_only = not any(line.text.strip() for line in body)
    explicit_space_content = bool(
        explicit_indent is not None
        and any(len(line.text) > content_indent for line in body)
    )
    if whitespace_only and not explicit_space_content:
        # With auto-detected indentation, YAML treats whitespace-only content
        # as empty unless keep chomping is explicit. An explicit indentation
        # indicator is different only when spaces extend beyond its declared
        # margin; those spaces flow through the ordinary decoder below.
        # Preserve only physical line breaks here for ``|+``/``>+``.
        if chomp == "+":
            return "".join("\n" for line in body if line.has_break), index
        return "", index

    contents: list[str] = []
    more_indented: list[bool] = []
    for line in body:
        indentation = len(line.text) - len(line.text.lstrip(" "))
        if line.text.strip() and indentation < content_indent:
            raise AssertionError(
                "block run scalar contains a non-empty line shallower than "
                "its YAML content indentation"
            )
        if indentation >= content_indent:
            content = line.text[content_indent:]
        else:
            # Whitespace-only lines may be less indented than the detected
            # content margin and still represent empty scalar lines.
            content = ""
        contents.append(content)
        more_indented.append(bool(content) and content[0].isspace())

    decoded: list[str] = []
    for offset, (line, content) in enumerate(zip(body, contents, strict=True)):
        decoded.append(content)
        if not line.has_break:
            continue
        if offset + 1 == len(contents):
            decoded.append("\n")
        elif header.group("style") == "|":
            decoded.append("\n")
        else:
            decoded.append(_folded_break(contents, more_indented, offset))

    value = "".join(decoded)
    if chomp == "-":
        value = value.rstrip("\n")
    elif chomp != "+" and value.endswith("\n"):
        value = value.rstrip("\n") + "\n"
    return value, index


def _block_scalar_body(
    lines: list[_SourceLine],
    header_index: int,
    parent_indent: int,
) -> tuple[list[_SourceLine], int]:
    """Return a block scalar's source body and first following YAML line."""

    body: list[_SourceLine] = []
    index = header_index + 1
    while index < len(lines):
        candidate = lines[index]
        indentation = len(candidate.text) - len(candidate.text.lstrip(" "))
        if candidate.text.strip() and indentation <= parent_indent:
            break
        body.append(candidate)
        index += 1
    return body, index


def _run_scalars(paths=WORKFLOWS):
    """Measure every YAML `run` scalar from raw workflow text.

    Deliberately not PyYAML: CI installs only requirements.txt plus pytest,
    ruff, and pip-audit, so importing yaml here fails collection on the
    runner. The repository's other workflow tests read ci.yml as text for
    the same reason. This decoder covers the repository's accepted one-line
    and block-scalar forms with decoded length semantics. Exotic YAML keys,
    aliases/tags, flow mappings, and multiline inline scalars fail closed;
    contributors can express the same command with ``run: |`` or ``run: >``.
    """

    for path in paths:
        lines = _source_lines(path.read_text(encoding="utf-8"))
        index = 0
        name = "unnamed step"
        while index < len(lines):
            line = lines[index].text
            _reject_unsupported_yaml_syntax(line, path)
            stripped = line.strip()
            if stripped.startswith("- name:"):
                name = _decode_inline_scalar(stripped[len("- name:") :])

            match = _RUN_KEY.match(line)
            if match is None:
                ordinary = _SAFE_MAPPING_KEY.match(line)
                if ordinary is not None:
                    other_value = ordinary.group("value")
                    other_header = _BLOCK_HEADER.fullmatch(other_value)
                    if other_header is not None:
                        _body, index = _block_scalar_body(
                            lines,
                            index,
                            len(ordinary.group("indent"))
                            + len(ordinary.group("sequence") or ""),
                        )
                        continue
                    if other_value.lstrip().startswith(("|", ">")):
                        raise AssertionError(
                            f"unsupported or invalid block scalar header in {path}: "
                            f"{other_value!r}"
                        )
                index += 1
                continue
            raw_value = match.group("value")
            header = _BLOCK_HEADER.fullmatch(raw_value)
            if header is not None:
                body, index = _decode_block_scalar(
                    lines,
                    index,
                    len(match.group("indent"))
                    + len(match.group("sequence") or ""),
                    header,
                )
                yield path.name, name, body
                continue

            if raw_value.lstrip().startswith(("|", ">")):
                raise AssertionError(
                    f"unsupported or invalid block scalar header in {path}: "
                    f"{raw_value!r}"
                )
            inline = raw_value.lstrip()
            if inline.startswith(("*", "&", "!", "[", "{")):
                raise AssertionError(
                    f"unsupported alias, tag, anchor, or flow run scalar in {path}; "
                    "use a direct string or block scalar"
                )
            parent_indent = len(match.group("indent")) + (
                len(match.group("sequence") or "")
            )
            if _inline_scalar_has_continuation(lines, index, parent_indent):
                raise AssertionError(
                    f"unsupported multiline inline run scalar in {path}; "
                    "use `run: |` or `run: >`"
                )
            yield path.name, name, _decode_inline_scalar(raw_value)
            index += 1


def test_workflows_exist_to_check() -> None:
    assert WORKFLOWS, "no workflow files found to check"


def test_workflow_discovery_includes_yml_and_yaml(tmp_path: Path) -> None:
    (tmp_path / "first.yml").write_text("name: first\n", encoding="utf-8")
    (tmp_path / "second.yaml").write_text("name: second\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("run: ignored\n", encoding="utf-8")

    assert [path.name for path in _workflow_paths(tmp_path)] == [
        "first.yml",
        "second.yaml",
    ]


def _github_expression_length(value: str) -> int:
    """Return the UTF-16 code-unit length enforced by GitHub's .NET runner."""

    return len(value.encode("utf-16-le")) // 2


def _oversized_run_scalars(paths=WORKFLOWS, *, limit: int) -> list[str]:
    """Describe decoded run scalars exceeding one GitHub runner limit."""

    offenders = []
    for fname, name, body in _run_scalars(paths):
        length = _github_expression_length(body)
        if length > limit:
            offenders.append(
                f"{fname}: {name} is {length} UTF-16 code units"
            )
    return offenders


@pytest.mark.parametrize("limit", [GITHUB_MAX_EXPRESSION, SAFE_MAX])
def test_no_run_scalar_approaches_github_expression_limit(limit: int) -> None:
    offenders = _oversized_run_scalars(limit=limit)

    assert not offenders, (
        "GitHub refuses a workflow whose expression exceeds "
        f"{GITHUB_MAX_EXPRESSION} UTF-16 code units, and reports it only as a run "
        "that fails in 0s with no logs. Move the body into a script under "
        ".github/scripts/ and call it instead: " + "; ".join(offenders)
    )


@pytest.mark.parametrize(
    ("header", "body", "expected"),
    [
        (">-", "          alpha\n          beta\n", "alpha beta"),
        ("|+", "          alpha\n\n\n", "alpha\n\n\n"),
        ("|2-", "          alpha\n          beta\n", "alpha\nbeta"),
        (">-2", "          alpha\n\n          beta\n", "alpha\nbeta"),
        (
            "> # folded with clip chomping",
            "          alpha\n            indented\n          beta\n",
            "alpha\n  indented\nbeta\n",
        ),
    ],
)
def test_run_scalar_block_styles_and_indicators(
    tmp_path: Path,
    header: str,
    body: str,
    expected: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n      - name: Decode me\n"
        f"        run: {header}\n{body}"
        "      - run: finished\n",
        encoding="utf-8",
    )

    scalars = list(_run_scalars([workflow]))

    assert scalars[0] == ("fixture.yml", "Decode me", expected)
    assert scalars[1][2] == "finished"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("echo plain # YAML comment", "echo plain"),
        ("'echo it''s # data' # YAML comment", "echo it's # data"),
        ('"printf \\u263a\\n" # YAML comment', "printf ☺\n"),
    ],
)
def test_run_scalar_inline_styles(
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    workflow.write_text(
        f"jobs:\n  test:\n    steps:\n      - name: Inline\n        run: {source}\n",
        encoding="utf-8",
    )

    assert list(_run_scalars([workflow])) == [
        ("fixture.yml", "Inline", expected)
    ]


def test_astral_text_is_measured_in_github_utf16_code_units(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = "😀" * ((GITHUB_MAX_EXPRESSION // 2) + 1)
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        f"      - name: Astral\n        run: '{payload}'\n",
        encoding="utf-8",
    )

    body = list(_run_scalars([workflow]))[0][2]

    assert len(body) < GITHUB_MAX_EXPRESSION
    assert _github_expression_length(body) > GITHUB_MAX_EXPRESSION
    assert _oversized_run_scalars(
        [workflow],
        limit=GITHUB_MAX_EXPRESSION,
    ) == [
        "fixture.yml: Astral is 21002 UTF-16 code units"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "      - 'run': echo quoted-key\n",
        '      - {name: Flow, run: "echo flow"}\n',
        "      - ? run\n        : echo explicit-key\n",
        "      - run: *shared\n",
        "      - run: !!str echo tagged\n",
    ],
)
def test_unsupported_run_yaml_fails_closed(
    tmp_path: Path,
    source: str,
) -> None:
    workflow = tmp_path / "fixture.yaml"
    workflow.write_text(
        "shared: &shared echo aliased\n"
        "jobs:\n  test:\n    steps:\n"
        f"{source}",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="unsupported"):
        list(_run_scalars([workflow]))


def test_multiline_plain_scalar_cannot_bypass_limit(tmp_path: Path) -> None:
    workflow = tmp_path / "fixture.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        "      - run: x\n"
        f"          {'a' * (GITHUB_MAX_EXPRESSION + 50)}\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="multiline inline run scalar"):
        list(_run_scalars([workflow]))


@pytest.mark.parametrize(
    ("preamble", "step"),
    [
        ("", '      - "\\u0072un": |\n          {payload}\n'),
        ("", "      - !!str run: |\n          {payload}\n"),
        ("", "      - &run_key run: |\n          {payload}\n"),
        (
            "run_key_name: &run_key run\n",
            "      - *run_key: |\n          {payload}\n",
        ),
        ("", '      - {{"\\u0072un": "{payload}"}}\n'),
    ],
)
def test_computed_run_keys_cannot_bypass_limit(
    tmp_path: Path,
    preamble: str,
    step: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = "a" * (GITHUB_MAX_EXPRESSION + 50)
    workflow.write_text(
        preamble
        + "jobs:\n  test:\n    steps:\n"
        + step.format(payload=payload),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="unsupported YAML property"):
        list(_run_scalars([workflow]))


@pytest.mark.parametrize(
    "steps_value",
    [
        '[{{run: "{payload}"}}]',
        '[{{"\\u0072un": "{payload}"}}]',
    ],
)
def test_flow_sequence_steps_cannot_bypass_limit(
    tmp_path: Path,
    steps_value: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = "a" * (GITHUB_MAX_EXPRESSION + 50)
    workflow.write_text(
        "jobs:\n  test:\n"
        f"    steps: {steps_value.format(payload=payload)}\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="unsupported YAML property"):
        list(_run_scalars([workflow]))


def test_extra_sequence_spacing_is_measured(tmp_path: Path) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = "a" * (GITHUB_MAX_EXPRESSION + 50)
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        f'      -    run: "{payload}"\n',
        encoding="utf-8",
    )

    scalars = list(_run_scalars([workflow]))

    assert len(scalars) == 1
    assert len(scalars[0][2]) == len(payload)


@pytest.mark.parametrize("spacing", ["", " ", "   "])
def test_merge_mapping_cannot_hide_run_scalar(
    tmp_path: Path,
    spacing: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = "a" * (GITHUB_MAX_EXPRESSION + 50)
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        f'      - <<{spacing}: {{run: "{payload}"}}\n',
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="unsupported YAML property"):
        list(_run_scalars([workflow]))


def test_non_run_block_scalar_body_is_not_scanned_as_workflow_yaml(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = "a" * (GITHUB_MAX_EXPRESSION + 50)
    workflow.write_text(
        "description: |\n"
        '  "quoted prose is allowed here"\n'
        f"  run: {payload}\n"
        "jobs:\n  test:\n    steps:\n"
        "      - run: echo measured\n",
        encoding="utf-8",
    )

    assert [body for _path, _name, body in _run_scalars([workflow])] == [
        "echo measured"
    ]


@pytest.mark.parametrize(
    ("header", "body", "expected"),
    [
        ("|", "          \n", ""),
        (">-", "           \n          \n", ""),
        ("|+", "          \n           \n", "\n\n"),
    ],
)
def test_empty_block_scalar_chomping_matches_yaml(
    tmp_path: Path,
    header: str,
    body: str,
    expected: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        f"      - run: {header}\n{body}"
        "      - run: finished\n",
        encoding="utf-8",
    )

    assert list(_run_scalars([workflow]))[0][2] == expected


@pytest.mark.parametrize(
    ("header", "body", "expected"),
    [
        ("|2", "           \n", " \n"),
        ("|2-", "           \n", " "),
        ("|2+", "           \n           \n", " \n \n"),
        (">2", "           \n", " \n"),
    ],
)
def test_whitespace_only_explicit_indentation_is_scalar_content(
    tmp_path: Path,
    header: str,
    body: str,
    expected: str,
) -> None:
    workflow = tmp_path / "fixture.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        f"      - run: {header}\n{body}"
        "      - run: finished\n",
        encoding="utf-8",
    )

    assert list(_run_scalars([workflow]))[0][2] == expected


def test_oversized_whitespace_only_explicit_block_is_detected(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "fixture.yml"
    payload = " " * (GITHUB_MAX_EXPRESSION + 50)
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        f"      - name: Whitespace\n        run: |1+\n         {payload}\n",
        encoding="utf-8",
    )

    body = list(_run_scalars([workflow]))[0][2]

    assert body == f"{payload}\n"
    assert _github_expression_length(body) > GITHUB_MAX_EXPRESSION
    assert _oversized_run_scalars(
        [workflow],
        limit=GITHUB_MAX_EXPRESSION,
    ) == [
        "fixture.yml: Whitespace is 21051 UTF-16 code units"
    ]
