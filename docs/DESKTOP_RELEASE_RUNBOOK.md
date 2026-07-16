# Desktop release runbook

This is the release boundary for WebJam's native desktop packages. The GitHub
Actions `build-desktop` matrix is the authoritative builder; do not reuse a
package from a different source commit or replace assets on a published tag.

## Supported targets

| Target | Runner | Package | Product scope |
| --- | --- | --- | --- |
| Windows x64 | `windows-latest` | `WebJam-windows-x64.zip` | Join/client |
| Intel macOS | `macos-15-intel` | `WebJam-macos-x64.zip` | Host and Join |
| Apple Silicon macOS | `macos-14` | `WebJam-macos-arm64.zip` | Host and Join |
| Linux x64 | `ubuntu-22.04` | `WebJam-linux-x64.zip` | Join/client on Ubuntu 22.04+ |

Windows and Linux deliberately leave **Host a Jam** disabled. A release must
not describe them as hosting replacements for the managed macOS Jamulus server.

## Automated build gates

Every target is built from the same clean commit after the Python suite, real
Jamulus integration, Go transport, and reference-service jobs pass. The desktop
job then proves, from a fresh archive extraction:

- packaged version and exact source build ID;
- expected native application and transport architecture;
- transport SHA-256, embedded build ID, protocol hello, and clean shutdown;
- required QSS, Webex HTML, Jamulus 3.12.2 payload, and license files;
- a real frozen Host/Join-dialog launch with an isolated home directory;
- no startup exception or owned-process residue.

Linux additionally launches the extracted app with the packaged Jamulus client
against a private dummy JACK graph. The gate requires authenticated loopback
JSON-RPC, the named JACK connection, secret-file mode `0600`, normal app exit,
and no remaining Jamulus client or occupied RPC port.

Windows verifies the exact upstream Jamulus installer SHA-256 both in CI and at
runtime. A tag build is rejected unless `WINDOWS_CODESIGN_PFX` and its password
produce valid Authenticode signatures for `WebJam.exe` and
`webjam-fabric.exe`; unsigned branch artifacts support legacy v1/v2 testing but
secure packaged v3 correctly fails closed. The upstream Jamulus NSIS installer
is itself unsigned and still shows its own publisher/UAC warning.

macOS packages are currently ad-hoc signed, not Developer ID signed or
notarized. They are private test packages until a notarized distribution path
and credentials are added.

## Manual release gates

Automation proves packaging and process behavior, not audible hardware truth.
Before publishing, record the exact artifact SHA-256 and complete:

1. Intel Mac: fresh extraction on an Intel Mac, Host and Join, real interface
   input/headphone output, invitation, authenticated roster, Leave/End cleanup.
2. Windows 11 x64: fresh extraction, Install Jamulus/UAC flow, Join from a Mac
   host, real interface I/O, invitation v3 on a publisher-signed build, Leave
   cleanup, and no WebJam/Jamulus/fabric residue.
3. Ubuntu 22.04 x64: fresh extraction, `./install-jamulus.sh`, Join from a Mac
   host, real JACK/ALSA interface I/O, Leave cleanup, and no process residue.
4. Gatekeeper/SmartScreen behavior appropriate to the actual signatures; do
   not record an ad-hoc or unsigned package as a public distribution pass.

If any gate is not run, report it as **NOT RUN**. A process launch, synthetic
JACK graph, or connected roster is not evidence that a person heard audio.

## Version and publication rule

The public `v0.16.2` tag and its attached assets are immutable historical
evidence. Post-tag packaging fixes require a new version and tag (normally
`v0.16.3`); never move `v0.16.2` or silently replace its published archives.
Tag CI creates a draft release only. A maintainer publishes it after the manual
gates above refer to the exact downloaded draft assets and hashes.
