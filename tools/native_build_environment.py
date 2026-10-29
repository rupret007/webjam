"""Prepare one fresh native packaging environment; never repair global Python."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
import venv


TARGETS = {
    "windows-x64": ("win32", "3.11.9", {"amd64", "x86_64"}),
    "macos-arm64": ("darwin", "3.11.9", {"arm64"}),
    "macos-x64": ("darwin", "3.11.9", {"x86_64"}),
    "linux-x64": ("linux", "3.11.15", {"x86_64", "amd64"}),
}


def clean_environment(directory: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    scripts = directory / ("Scripts" if os.name == "nt" else "bin")
    env.update(VIRTUAL_ENV=str(directory), PYTHONNOUSERSITE="1")
    env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
    return env


def create_environment(runtime_temp: Path, expected_version: tuple[int, int, int]) -> dict:
    if tuple(sys.version_info[:3]) != expected_version:
        raise ValueError("selected native Python version differs from the matrix")
    if not runtime_temp.is_absolute() or runtime_temp.is_symlink() or not runtime_temp.is_dir():
        raise ValueError("an explicit existing RUNNER_TEMP directory is required")
    runtime_temp = runtime_temp.resolve(strict=True)
    directory = Path(tempfile.mkdtemp(prefix="webjam-native-build-", dir=runtime_temp))
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(directory)
    scripts = directory / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    if not python.is_file():
        raise ValueError("fresh environment has no interpreter")
    probe = """import json,site,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
assert Path(sys.prefix).resolve()==root and sys.prefix!=sys.base_prefix
assert site.ENABLE_USER_SITE is False
assert all(Path(p).resolve().is_relative_to(root) for p in sys.path
           if p and Path(p).name in ('site-packages','dist-packages'))
print(json.dumps({'version':list(sys.version_info[:3]),'base':sys.base_prefix}))
"""
    completed = subprocess.run(
        [str(python), "-c", probe, str(directory)], cwd=directory,
        env=clean_environment(directory), check=True, capture_output=True, text=True, timeout=60,
    )
    if len(completed.stdout) > 8192 or completed.stderr:
        raise ValueError("unexpected isolated interpreter evidence")
    proof = json.loads(completed.stdout)
    if proof["version"] != list(expected_version) or Path(proof["base"]).resolve() != Path(sys.base_prefix).resolve():
        raise ValueError("fresh environment changed the selected base interpreter")
    config = (directory / "pyvenv.cfg").read_text(encoding="utf-8")
    if "include-system-site-packages = false" not in config.lower():
        raise ValueError("global site packages are not excluded")
    return {"directory": directory, "scripts": scripts, "python": python, "version": proof["version"]}


def publish_environment(result: dict, runtime_temp: Path, github_path: Path, github_env: Path) -> None:
    root = runtime_temp.resolve(strict=True)
    outputs = []
    for path in (github_path, github_env):
        before = path.lstat()
        if not path.resolve(strict=True).is_relative_to(root) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("GitHub output must be an existing regular file under RUNNER_TEMP")
        outputs.append((path, before))
    values = {key: str(result[key]) for key in ("directory", "scripts", "python")}
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("invalid environment path")
    payloads = [values["scripts"] + "\n", "\n".join([
        "VIRTUAL_ENV=" + values["directory"], "WEBJAM_BUILD_PYTHON=" + values["python"],
        "PYTHONNOUSERSITE=1", "PYTHONPATH=", "PYTHONHOME=", "",
    ])]
    for (path, before), payload in zip(outputs, payloads):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("GitHub output identity changed")
            data = payload.encode("utf-8")
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("short GitHub environment write")
                data = data[written:]
            os.fsync(fd)
        finally:
            os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--python-version", required=True)
    args = parser.parse_args()
    system, version, machines = TARGETS[args.target]
    if args.python_version != version or sys.platform != system or platform.machine().lower() not in machines:
        raise ValueError("native target, platform or version does not match the reviewed matrix")
    runtime = Path(os.environ["RUNNER_TEMP"])
    result = create_environment(runtime, tuple(map(int, version.split("."))))
    publish_environment(result, runtime, Path(os.environ["GITHUB_PATH"]), Path(os.environ["GITHUB_ENV"]))
    print(json.dumps({"isolated": True, "python": str(result["python"]), "version": result["version"]}))


if __name__ == "__main__":
    main()
