"""Authenticate a successful same-source push artifact for one manual matrix."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import zipfile

from tests.support.diagnose_windows_reference_studio import digest, package_binding, source_binding

REPOSITORY = "rupret007/webjam"
PACKAGE_NAME = "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip"


def api(path: str):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/{path}"],
                                             text=True, timeout=60))


def require(value, reason):
    if not value:
        raise ValueError(reason)


def extract_package(package: Path, destination: Path) -> None:
    destination.mkdir()
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        names = [item.filename.rstrip("/") for item in members]
        require(len(names) <= 50_000 and len({name.casefold() for name in names}) == len(names),
                "Duplicate, aliased or oversized package inventory")
        require(sum(item.file_size for item in members) <= 8 * 1024**3, "Package is oversized")
        for item, name in zip(members, names):
            relative = PurePosixPath(name)
            require(relative.parts and relative.parts[0] == "WebJam" and relative.as_posix() == name
                    and ".." not in relative.parts and ":" not in name and "\\" not in name
                    and not stat.S_ISLNK(item.external_attr >> 16), "Unsafe package member")
            target = destination.joinpath(*relative.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                require(target.stat().st_size == item.file_size, "Extracted member size differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = source_binding(args.source_head)
    output = args.output.resolve()
    output.mkdir(parents=True)
    receipt = {"status": "STARTING", "source": source, "run_id": args.run_id}
    try:
        run = api(f"actions/runs/{args.run_id}")
        receipt["run"] = run
        require(run["head_sha"] == args.source_head and run["event"] == "push"
                and run["status"] == "completed" and run["conclusion"] == "success",
                "Expected a successful completed push of this exact source")
        require(run["path"] == ".github/workflows/ci.yml" and run["workflow_id"] == 235479110,
                "Producer is not the registered WebJam CI workflow")
        require(run["run_attempt"] == 1, "Matrix input must come from a first-attempt push")
        jobs = api(f"actions/runs/{args.run_id}/jobs?per_page=100")
        require(jobs["total_count"] <= 100, "Job inventory requires unsupported pagination")
        matches = [job for job in jobs["jobs"] if job["name"] == "Build Desktop (windows-x64)"]
        require(len(matches) == 1 and matches[0]["status"] == "completed"
                and matches[0]["conclusion"] == "success" and matches[0]["head_sha"] == args.source_head
                and matches[0]["run_id"] == args.run_id and matches[0]["run_attempt"] == run["run_attempt"],
                "Exact Windows desktop producer has not passed")
        receipt["package_job"] = matches[0]
        artifacts = api(f"actions/runs/{args.run_id}/artifacts?per_page=100")
        require(artifacts["total_count"] <= 100, "Artifact inventory requires unsupported pagination")
        candidates = [item for item in artifacts["artifacts"] if item["name"] == "webjam-windows-x64"]
        require(len(candidates) == 1, "Expected exactly one Windows artifact")
        artifact = candidates[0]
        receipt["artifact"] = artifact
        require(not artifact["expired"] and artifact["workflow_run"]["id"] == args.run_id
                and artifact["workflow_run"]["head_sha"] == args.source_head,
                "Windows artifact provenance differs")
        expected = artifact.get("digest", "").removeprefix("sha256:")
        require(re.fullmatch(r"[0-9a-f]{64}", expected), "Missing authenticated archive hash")
        require(0 < artifact["size_in_bytes"] <= 4 * 1024**3, "Actions archive size is invalid")
        require(shutil.disk_usage(output).free > artifact["size_in_bytes"] + 10 * 1024**3,
                "Insufficient space for owned diagnostic inputs")
        outer = output / "actions-artifact.zip"
        with outer.open("xb") as stream:
            subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],
                           stdout=stream, timeout=300, check=True)
        require(outer.stat().st_size == artifact["size_in_bytes"] and digest(outer) == expected,
                "Actions archive checksum or size mismatch")
        package = output / PACKAGE_NAME
        with zipfile.ZipFile(outer) as archive:
            members = [item for item in archive.infolist() if item.filename == PACKAGE_NAME]
            require(len(members) == 1 and not members[0].is_dir()
                    and not stat.S_ISLNK(members[0].external_attr >> 16)
                    and 0 < members[0].file_size <= 4 * 1024**3, "Invalid inner Windows package")
            with archive.open(members[0]) as source, package.open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        extract_package(package, output / "extracted")
        binary = output / "extracted/WebJam/WebJam.exe"
        receipt.update(status="AUTHENTICATED_DIAGNOSTIC_INPUT", outer_sha256=expected,
                       binary=str(binary), package=str(package),
                       binding=package_binding(binary, package, args.source_head))
    except Exception as exc:
        receipt.update(status="INPUT_FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        (output / "input.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
