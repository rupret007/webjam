# Desktop release runbook

This is the release boundary for WebJam's native desktop packages. The GitHub
Actions `build-desktop` matrix is the authoritative builder; do not reuse a
package from a different source commit or replace assets on a published tag.

## Supported targets

| Target | Runner | Preferred release asset | Portable fallback | Product scope |
| --- | --- | --- | --- | --- |
| Windows x64 | `windows-2025` | `WebJam-v<VERSION>-windows-x64-setup.exe` | `WebJam-windows-x64.zip` | Join/client |
| Intel macOS | `macos-15-intel` | `WebJam-v<VERSION>-macos-x64.dmg` | `WebJam-macos-x64.zip` | Host and Join |
| Apple Silicon macOS | `macos-14` | `WebJam-v<VERSION>-macos-arm64.dmg` | `WebJam-macos-arm64.zip` | Host and Join |
| Linux x64 | `ubuntu-22.04` | `WebJam-linux-x64.zip` | — | Join/client on Ubuntu 22.04 x64 |

Windows and Linux deliberately leave **Host a Jam** disabled. A release must
not describe them as hosting replacements for the managed macOS Jamulus server.

## Automated build gates

Every target is built from the same clean commit after the Python suite, real
Jamulus integration, Go transport, and reference-service jobs pass. The desktop
job then proves, from a fresh install, mounted disk image, or archive extraction
as appropriate:

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

Windows compiles a per-user Setup executable, silently installs it to a fresh
location, verifies the installed version/build ID, x64 payload, transport
manifest, and exact upstream Jamulus installer SHA-256, launches the installed
app, checks both requested shortcuts, and uninstalls it. The gate proves owned
files and shortcuts are removed while an unowned sentinel is preserved. A tag
build is rejected unless `WINDOWS_CODESIGN_PFX` and its password produce valid
Authenticode signatures for `WebJam.exe`, `webjam-fabric.exe`, the Setup
executable, and its embedded uninstaller. An installer format alone does not
remove SmartScreen: publisher signing and, on managed PCs, organization approval
or allowlisting are still required. The bundled upstream Jamulus installer is
separate, unsigned software and can show its own publisher/UAC warning.

Each macOS job also creates and verifies a read-only drag-to-Applications disk
image. CI mounts it without browsing, checks its Applications link, copies the
app to a fresh directory, ejects the image, then repeats strict/deep signature,
architecture, version, build-ID, transport, bundled-Jamulus, and launch checks
against that copy. The apps are currently ad-hoc signed, not Developer ID
signed or notarized. They are private test packages until a notarized
distribution path and credentials are added.

## Manual release gates

Automation proves packaging and process behavior, not audible hardware truth.
Before publishing, record the exact artifact SHA-256 and complete:

1. Intel Mac: mount the DMG on an Intel Mac, drag WebJam to Applications, eject
   it, launch the installed copy, then exercise Host and Join, real interface
   input/headphone output, invitation, authenticated roster, and Leave/End
   cleanup. Record Gatekeeper behavior for the exact signature/notarization.
2. Windows 11 x64: run Setup, launch WebJam from the Start menu, exercise the
   Install Jamulus/UAC flow, Join from a Mac host, real interface I/O,
   invitation v3 on a publisher-signed build, Leave cleanup, and uninstall from
   **Settings > Apps > Installed apps**. Record SmartScreen/publisher behavior
   and prove no WebJam/Jamulus/fabric process residue.
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
Tag CI attaches the direct Setup executable, both DMGs, Linux ZIP, and portable
ZIP fallbacks to a draft GitHub release. A maintainer publishes it only after
the manual gates above refer to the exact downloaded draft assets and hashes.
