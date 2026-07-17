# Native release dependency locks

The desktop release matrix installs only from the target-specific, hashed lock
matching its native package. These locks preserve the Python dependency graph
that is reviewed and tested before signing; `requirements.txt` remains the
human-maintained application dependency declaration.

The v0.16.3 candidate uses CPython 3.11.9 on Windows and both macOS targets,
CPython 3.11.15 on Linux, pip 26.1.2, setuptools 81.0.0 on macOS, and
setuptools 83.0.0 elsewhere. PyInstaller 6.21's macOS `pkg_resources`
runtime hook requires `NullProvider`, which setuptools 82+ no longer ships.
Regenerate all locks with uv 0.11.29 after a deliberate dependency change,
then rerun the entire native matrix before creating a release tag:

The macOS-only pin is a narrow exception to GHSA-h35f-9h28-mq5c. That issue
affects Unicode exclusion rules while creating an sdist; this release path
installs only hash-pinned wheels and creates a PyInstaller application, never
an sdist. Remove the exception when PyInstaller supports setuptools 82+.

```text
uv pip compile requirements.txt \
  --constraints requirements-lock/release-constraints.txt \
  --python-version <target-python> \
  --python-platform <target-platform> \
  --generate-hashes --only-binary :all: \
  --output-file requirements-lock/<target>.txt
```

Target platforms are `x86_64-pc-windows-msvc`, `x86_64-apple-darwin`,
`aarch64-apple-darwin`, and `x86_64-manylinux_2_34`. Regenerate
`bootstrap.txt` from `bootstrap.in` with the same hash and binary-only flags.
