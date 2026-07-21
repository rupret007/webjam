# Desktop release runbook

This is the release boundary for WebJam's native desktop packages. The GitHub
Actions `build-desktop` matrix is the authoritative source builder. The
environment-gated `windows-release-trust` and `macos-release-trust` jobs are the
only authoritative packagers for signed platform releases. Do not reuse a
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

Native builders use exact Python patch versions and install only wheels from
the target-specific files under `requirements-lock/`, with every distribution
hash required. `pip check`, the full frozen graph, and interpreter identity are
recorded in the build log. Dependency changes require regenerating all four
locks and rerunning the full native matrix before signing.

Linux additionally launches the extracted app with the packaged Jamulus client
against a private dummy JACK graph. The gate requires authenticated loopback
JSON-RPC, the named JACK connection, secret-file mode `0600`, normal app exit,
and no remaining Jamulus client or occupied RPC port. The distributed
`install-jamulus.sh` verifies the pinned Jamulus package SHA-256 before it can
invoke `sudo apt`.

Windows compiles a per-user Setup executable, silently installs it to a fresh
location, verifies the installed version/build ID, x64 payload, transport
manifest, and exact upstream Jamulus installer SHA-256, launches the installed
app, checks both requested shortcuts, and uninstalls it. The gate proves owned
files and shortcuts are removed while an unowned sentinel is preserved.
Ordinary Actions downloads are renamed `UNSIGNED-TEST-ONLY` before upload.

A `v*` tag, or a manual `workflow_dispatch` with
`windows_signing_rehearsal=true`, runs a separate job bound to the protected
`windows-release` environment. It requires environment secrets
`WINDOWS_CODESIGN_PFX` and `WINDOWS_CODESIGN_PASSWORD` plus the non-secret
environment variable `WINDOWS_CODESIGN_SUBJECT`. The job requires exactly one
currently valid private-key certificate with the Code Signing EKU, rejects
SHA-1 certificate signatures, requires an RSA key from 2048 through 4096 bits
for Windows application-control compatibility, and pins the exact publisher
subject. It Authenticode-signs and timestamps `WebJam.exe`,
`webjam-fabric.exe`, Setup, and the embedded uninstaller. The certificate is
removed with its private key before the final Setup is installed or launched;
the ordinary Windows runner exercises this lifecycle with a disposable test
certificate and proves its backing key file is gone. The final gate repeats
install/launch/uninstall checks and retains a SHA-256 container manifest,
publisher metadata, and an inventory of every installed PE signature.

This PFX path is conditional: use it only with an existing eligible exportable
legacy certificate or an internal-enterprise certificate whose root the target
organization deploys. Newly issued publicly trusted code-signing private keys
are normally hardware/HSM or remote-service backed and cannot be exported into
`WINDOWS_CODESIGN_PFX`. Before a public release, choose and integrate an
approved remote signer (for example Azure Artifact Signing, SignPath, or a CA's
hosted signing service) or a Microsoft Store distribution path. Do not weaken
hardware-key policy to fit the PFX workflow.

An installer format and valid Authenticode signature do not guarantee a quiet
SmartScreen experience: publisher/file reputation and, on managed PCs,
organization approval or allowlisting are still required. The bundled upstream
Jamulus installer is separate, unsigned software and can show its own
publisher/UAC warning.

Each macOS job also creates and verifies a read-only drag-to-Applications disk
image. CI mounts it without browsing, checks its Applications link, copies the
app to a fresh directory, ejects the image, then repeats signature,
architecture, version, build-ID, transport, bundled-Jamulus, and launch checks
against that copy. Ordinary branch apps remain ad-hoc signed, are neither
Developer ID signed nor notarized, and are renamed `ADHOC-TEST-ONLY` before
upload.

A `v*` tag, or a manual `workflow_dispatch` with
`macos_signing_rehearsal=true`, selects the fail-closed release-trust path. The
manual rehearsal signs and notarizes both Mac architectures but cannot create
a GitHub Release; use it to prove credentials and Apple acceptance before
creating a version tag. Both modes require all five GitHub Actions secrets:

- `MACOS_DEVELOPER_ID_P12`: base64 PKCS#12 containing the Developer ID
  Application certificate and private key;
- `MACOS_DEVELOPER_ID_P12_PASSWORD`;
- `APPLE_NOTARY_KEY_P8`: base64 App Store Connect API private key;
- `APPLE_NOTARY_KEY_ID`;
- `APPLE_NOTARY_ISSUER_ID`.

Also set the non-secret `APPLE_DEVELOPER_TEAM_ID` GitHub variable to the
10-character Team ID expected in every resulting code signature. The keychain
preparation step rejects a certificate whose Team ID differs from that pin.

`packaging/macos/release-keychain.sh` validates those values, decodes the
certificate and API key beneath `RUNNER_TEMP`, imports exactly one Developer ID
Application identity into an ephemeral keychain, verifies its Team ID, and
preflights the notary API. `packaging/macos/release-trust.sh` then inventories
and signs every collected Mach-O file and recognized code bundle from the
inside out with Hardened Runtime and a secure timestamp. The Qt WebEngine
helper receives its shipped JIT/runtime entitlement plist; the two Jamulus
apps receive only `packaging/macos/Jamulus.entitlements`; WebJam receives
`packaging/macos/WebJam.entitlements` and is sealed last. Production signing
never uses `--deep`; deep verification is an additional final check.

Each distributed outer container receives its own accepted `xcrun notarytool
submit ... --wait --output-format json` result. CI retains and inspects both
notary logs, rejects any issue, and fails unless the status is exactly
`Accepted`. For the portable ZIP it staples and validates `WebJam.app`, then
recreates and freshly extracts the ZIP because a ZIP cannot itself be stapled.
It signs and submits the DMG separately, staples and validates it, and requires
Gatekeeper (`spctl`) acceptance for the app from fresh ZIP and DMG copies and
for the DMG itself. Notary JSON, logs, hashes, and signature inventories are
retained as a separate CI evidence artifact. An unconditional step deletes the
credential files and ephemeral keychain immediately after the final DMG notary
operation, before WebJam is launched from the mounted image or any artifact
upload action runs.

The macOS orchestration and conditional Windows PFX orchestration are
implemented but have not yet completed a real credentialed rehearsal. If no
eligible Windows PFX already exists, remote/provider-backed signing integration
is still required. Missing credentials, an unexpected Windows publisher,
rejected signatures/notarization, failed stapling, or failed platform trust
assessment all stop the protected jobs. Do not create the release tag until
both signing rehearsals succeed, physical acceptance is recorded, and GitHub
immutable releases are enabled for this repository.

Before provisioning any private key, a repository administrator must create
two protected GitHub Environments:

- `windows-release`, containing only the two Windows secrets and pinned
  `WINDOWS_CODESIGN_SUBJECT` environment variable when using the eligible PFX
  path, or the least-privilege OIDC/provider configuration selected for remote
  signing;
- `macos-release`, containing only the five Apple secrets and pinned
  `APPLE_DEVELOPER_TEAM_ID` environment variable.

Configure required reviewers, prevent self-review, disable administrator
bypass when the repository policy allows it, and restrict deployment branches
and tags to the approved rehearsal ref and version tags. Both workflow jobs are
already isolated and explicitly bound to those environments with
`deployment: false`; environment secrets remain unavailable until protection
rules pass. Do not copy these values into repository-level secrets. The current
repository reports zero configured Environments, so administrator configuration
and credential provisioning remain release blockers.

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
Once the credentialed rehearsal and manual gates above pass, tag CI attaches
the direct Setup executable, both DMGs, Linux ZIP, and portable ZIP fallbacks
to a draft GitHub release. It also generates, verifies, and attaches
`WebJam-v<VERSION>-SHA256SUMS.txt` for that exact seven-file asset set. A
maintainer publishes the draft only after the manual gates above refer to the
exact downloaded assets and manifest hashes.
