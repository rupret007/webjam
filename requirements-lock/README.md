# Native release dependency locks

The desktop release matrix installs only from the target-specific, hashed lock
matching its native package. These locks preserve the Python dependency graph
that is reviewed and tested before signing; `requirements.txt` remains the
human-maintained application dependency declaration.

The v0.26.0 release uses the exact dependency locks reviewed by its successful
native tag CI and protected publisher. Those locks are package evidence only
when bound to an exact checksum-verified v0.26.0 release asset; a source
checkout remains metadata, not a published package claim.
The locks target CPython
3.11.9 on Windows and both macOS targets, CPython
3.11.15 on Linux, pip 26.1.2, setuptools 81.0.0 on macOS, and setuptools 83.0.0
elsewhere. PyInstaller 6.21's macOS `pkg_resources`
runtime hook requires `NullProvider`, which setuptools 82+ no longer ships.
Regenerate all locks with uv 0.11.29 after a deliberate dependency change,
then rerun the entire native matrix before creating a release tag:

These lock files alone do not claim signed packages, notarization, or
physical-audio evidence; they record the dependency graph the release workflow
must use.

The macOS-only pin is a narrow exception to GHSA-h35f-9h28-mq5c. That issue
affects Unicode exclusion rules while creating an sdist; this release path
never creates an sdist. The Intel cryptography exception consumes an upstream
sdist to create a wheel, which does not exercise the vulnerable setuptools
sdist-creation path. Remove the exception when PyInstaller supports
setuptools 82+.

`cryptography==50.0.0` is the first release fixing all three v0.22.2 audit
findings. Upstream removed x86_64 macOS wheels and support in 49.0.0, so the
Intel candidate has one explicit source-build path. CI installs the PEP 517
tools from `macos-x64-cryptography-build.txt`, fetches Cargo crates under the
sdist's checksum-bearing `Cargo.lock`, builds a hash-verified OpenSSL 3.5.7 LTS
source as a private static x86_64 prefix, and then builds and installs
cryptography offline. The helper verifies the installed extension's version,
architecture, OpenSSL identity, static linkage, and runtime paths. See
`packaging/macos/CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt`. Every other runtime
package and platform remains binary-only.

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
For the Intel target, add `--no-binary cryptography`; the reviewed installer
helper is the enforcement point that refuses every other source distribution.

Regenerate the Intel build-tool lock from
`macos-x64-cryptography-build.in` for CPython 3.11.9 and
`x86_64-apple-darwin`, with hashes and `--only-binary :all:`. Regenerate the
Pocket Stage Swift/WSS integration lock from
`pocket-stage-integration-macos-arm64.in`, the release constraints, CPython
3.12, and `aarch64-apple-darwin`, also with hashes and binary-only enforcement.
The minimal Linux catalog-verifier lock intentionally records only the exact
CPython 3.12 Linux wheel hash for each of its three packages; validate those
hashes against PyPI and the Linux target lock whenever it changes.
