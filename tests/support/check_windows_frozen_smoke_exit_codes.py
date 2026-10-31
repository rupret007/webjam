"""Exercise the workflow's actual frozen-smoke commands on native Windows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "tests.support.run_frozen_pocket_stage_smoke",
    "tests.support.run_frozen_reference_studio_smoke",
)


def smoke_commands() -> str:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    marker = "      - name: Verify frozen Windows package\n"
    assert workflow.count(marker) == 1, "Frozen Windows gate must be unambiguous"
    step = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    start = script.index("python -m " + MODULES[0])
    end = script.index('$env:WEBJAM_SMOKE_LAUNCH_ONLY = "1"', start)
    commands = script[start:end]
    assert commands.count("python -m " + MODULES[0]) == 1
    assert commands.count("python -m " + MODULES[1]) == 1
    return commands


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def check(shell: str) -> bool:
    if os.name != "nt":
        raise RuntimeError("This regression requires native Windows; it must not be skipped")
    commands = smoke_commands()
    cases = []
    with tempfile.TemporaryDirectory(prefix="webjam-smoke-exit-") as temporary:
        root = Path(temporary)
        for first, second, expected in ((17, 0, 17), (0, 29, 29), (0, 0, 0)):
            trace = root / f"trace-{first}-{second}.txt"
            script = (
                "$ErrorActionPreference = 'Stop'\n"
                "$global:LASTEXITCODE = 0\n"
                f"$testPython = {quote(sys.executable)}\n"
                f"$trace = {quote(str(trace))}\n"
                "$app = 'unused-test-binary'; $appDir = 'unused-test-directory'\n"
                "function python {\n"
                "  $module = $args[1]\n"
                "  Add-Content -LiteralPath $trace -Value $module\n"
                f"  if ($module -eq {quote(MODULES[0])}) {{ $code = {first} }}\n"
                f"  elseif ($module -eq {quote(MODULES[1])}) {{ $code = {second} }}\n"
                "  else { throw 'Unexpected smoke helper' }\n"
                '  & $testPython -c "import sys; sys.exit($code)"\n'
                "}\n"
                + commands
                + "\nexit $LASTEXITCODE\n"
            )
            child = subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=20, check=False,
            )
            observed = trace.read_text(encoding="utf-8-sig").splitlines() if trace.exists() else []
            wanted = list(MODULES[:1] if first else MODULES)
            cases.append({
                "first_exit": first, "second_exit": second,
                "expected_exit": expected, "actual_exit": child.returncode,
                "helpers_called": observed,
                "passed": child.returncode == expected and observed == wanted,
            })
    passed = all(case["passed"] for case in cases)
    print(json.dumps({
        "native_windows": True,
        "commands_sha256": hashlib.sha256(commands.encode()).hexdigest(),
        "cases": cases, "passed": passed,
    }, sort_keys=True))
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", default="pwsh", help="Explicit native PowerShell executable")
    return 0 if check(parser.parse_args().shell) else 1


if __name__ == "__main__":
    raise SystemExit(main())
