# Desktop release runbook

> **Unreleased after v0.22.2:** current workflow facts in this maintained
> runbook may describe source changes not present in the immutable published
> v0.22.2 assets. The historical v0.22.2 release record below remains unchanged
> release evidence.

This is the release boundary for WebJam's native desktop packages. The GitHub
Actions `build-desktop` matrix is the authoritative source builder. Version
tags may promote its explicitly unsigned/ad-hoc outputs as a private test
candidate. The environment-bound `windows-release-trust` and
`macos-release-trust` jobs remain the only authoritative packagers for a future
signed platform release, once their GitHub Environments have real protection
rules and credentials. Do not reuse a package from a different source commit or
replace assets on a published tag.

## Supported targets

| Target | Runner | Preferred release asset | Portable fallback | Product scope |
| --- | --- | --- | --- | --- |
| Windows x64 | `windows-2025` | `WebJam-v<VERSION>-windows-x64-UNSIGNED-TEST-ONLY-setup.exe` | `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip` | Join and Reference Studio |
| Intel macOS | `macos-15-intel` | `WebJam-v<VERSION>-macos-x64-ADHOC-TEST-ONLY.dmg` | `WebJam-macos-x64-ADHOC-TEST-ONLY.zip` | Host, Join, and Reference Studio |
| Apple Silicon macOS | `macos-14` | `WebJam-v<VERSION>-macos-arm64-ADHOC-TEST-ONLY.dmg` | `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip` | Host, Join, and Reference Studio |
| Linux x64 | `ubuntu-22.04` | `WebJam-linux-x64.zip` | — | Join and Reference Studio on Ubuntu 22.04 x64 |

Windows and Linux deliberately leave **Host a Jam** disabled. A release must
not describe them as hosting replacements for the managed macOS Jamulus server.

## Private test-candidate lane

A `v*` tag builds all four targets from one commit and creates a reviewable
draft without requesting signing credentials. Windows filenames include
`UNSIGNED-TEST-ONLY`; macOS filenames include `ADHOC-TEST-ONLY`. Publishing the
draft as Latest does not change that trust status and must retain the warning in
the release title and opening notes. Physical audio, hardware, SmartScreen,
Gatekeeper, signing, and notarization results remain **NOT RUN** unless recorded
against the exact attached hashes.

Each candidate Mac DMG and ZIP contains `Install WebJam.command`,
`Install WebJam - Remove Quarantine.command`, `READ ME FIRST.txt`, and fixed
candidate metadata beside `WebJam.app`. Dragging `WebJam.app` onto the
Applications shortcut is the primary installation path because current macOS
versions can block a quarantined `.command` file without offering app-bundle
Open Anyway approval. The README documents explicit Terminal invocation for
the optional helpers. The guided helper validates and installs the app,
preserves quarantine, and attempts launch. The advanced helper performs the
same validation, asks for explicit confirmation, and removes quarantine from
the installed `WebJam.app` only. Neither helper uses `sudo`, disables
Gatekeeper, or changes another application. Both prefer `/Applications` and
fall back to `~/Applications` when the system folder is not writable.

## Automated build gates

Every target is built from the same clean commit after the Python suite, real
Jamulus integration, Go transport, and reference-service jobs pass. The desktop
job then proves, from a fresh install, mounted disk image, or archive extraction
as appropriate:

- packaged version and exact source build ID;
- expected native application and transport architecture;
- transport SHA-256, embedded build ID, protocol hello, and clean shutdown;
- required QSS, Jamulus 3.12.2 payload, and license files;
- the source, mounted-DMG copy, and portable-ZIP copy of the outer Mac bundle
  must each contain this exact `NSAppDataUsageDescription` value from
  `webjam.spec`: “WebJam accesses Jamulus app data only for dedicated WebJam
  profiles and private Reference Track audio-route and control files. It never
  reads or changes your regular Jamulus profile.” Missing, empty, generic, or
  alternate text fails packaging;
- absence of the retired Qt WebEngine/Webex-widget runtime;
- a real frozen Host/Join-dialog launch with an isolated home directory;
- no startup exception or owned-process residue.

Native builders use exact Python patch versions and install only wheels from
the target-specific files under `requirements-lock/`, with every distribution
hash required. `pip check`, the full frozen graph, and interpreter identity are
recorded in the build log. Dependency changes require regenerating all four
locks and rerunning the full native matrix before signing.

The same job runs `python tools/runtime_dependency_policy.py --check` before
freezing. This compares all four locks with the reviewed runtime/build/excluded
classification, rejects unattributed lock drift and selected GPL/AGPL Python
runtime licenses, and proves the checked-in human notice and CycloneDX SBOM are
deterministic. After PyInstaller runs, `--verify-bundle` requires the generated
notice/SBOM plus checksum-pinned NumPy, SoundFile, and libsndfile license
evidence inside the actual target bundle. MP3 import remains capability-gated
by `soundfile.check_format("MP3")`; no package may claim MP3 import merely
because SoundFile is installed. MP3 bounce is a separate, disabled-by-default
adapter capability, and no default encoder adapter ships in this release.

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
Both portable and Setup paths are exercised from directories containing spaces.
Ordinary Actions downloads are renamed `UNSIGNED-TEST-ONLY` before upload and
are retained for 90 days as `webjam-windows-x64`. That artifact contains exactly
the Setup, portable ZIP, and a verified two-entry
`WebJam-v<VERSION>-windows-x64-SHA256SUMS.txt` manifest.

A manual `workflow_dispatch` with `windows_signing_rehearsal=true` runs a
separate job bound to the environment-bound, intended-to-be-protected
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

A manual `workflow_dispatch` with `macos_signing_rehearsal=true` selects the
fail-closed release-trust path. The rehearsal signs and notarizes both Mac
architectures but cannot create
a GitHub Release; use it to prove credentials and Apple acceptance before a
future production-trusted release. It requires all five GitHub Actions secrets:

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
inside out with Hardened Runtime and a secure timestamp. The two Jamulus apps
receive only `packaging/macos/Jamulus.entitlements`; WebJam receives
`packaging/macos/WebJam.entitlements` and is sealed last. The component-policy
gate rejects any retired Qt WebEngine runtime before signing. Production
signing never uses `--deep`; deep verification is an additional final check.

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
implemented but have not yet completed a real credentialed rehearsal. These
manual rehearsals do not block the private candidate lane. If no
eligible Windows PFX already exists, remote/provider-backed signing integration
is still required. Missing credentials, an unexpected Windows publisher,
rejected signatures/notarization, failed stapling, or failed platform trust
assessment all stop the environment-bound trust jobs. For a future
production-trusted release, do not create its release tag until both signing
rehearsals succeed,
physical acceptance is recorded, and GitHub immutable releases are enabled.
Those gates do not block the explicitly unsigned/ad-hoc private-candidate lane.

The repository currently has three GitHub Environments, but as of 2026-07-29
all three have empty protection rules and no deployment-branch policy. Treat
their names as workflow routing only, not as an approval boundary, until a
repository administrator configures them:

- `windows-release`, containing only the two Windows secrets and pinned
  `WINDOWS_CODESIGN_SUBJECT` environment variable when using the eligible PFX
  path, or the least-privilege OIDC/provider configuration selected for remote
  signing;
- `macos-release`, containing only the five Apple secrets and pinned
  `APPLE_DEVELOPER_TEAM_ID` environment variable;
- `release-latest`, containing no signing secret and protecting the final
  revalidation-and-publication job.

Configure required reviewers, prevent self-review, disable administrator
bypass when the repository policy allows it, and restrict deployment branches
and tags to the approved rehearsal ref and version tags. The two trust workflow
jobs are already isolated and explicitly bound to their environments with
`deployment: false`; the publisher is bound separately to `release-latest`.
Environment secrets remain unavailable until applicable protection rules pass.
Do not copy trust credentials into repository-level secrets. The current trust
environments contain no release credentials, so protection and credential
provisioning remain production-trusted-release blockers but not
private-candidate blockers.

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
4. Gatekeeper/SmartScreen behavior appropriate to the actual signatures; an
   ad-hoc or unsigned result may be recorded only as private-candidate evidence,
   never as a production-trusted distribution pass.
5. Reference Studio on each target: create a project in a path containing
   spaces, import a supported backing file, save/reopen, verify waveform and
   local playback, edit and move a named section, exercise mixer/automation,
   review tempo analysis, perform a WAV and FLAC bounce, cancel one bounce, and
   verify exact checksums plus absence of partial outputs. On supported physical
   hardware, separately record input mapping, count-in, punch/cycle recording,
   latency compensation, dropout/recovery behavior, and audible playback.
6. On each Mac architecture, run the installed app's Other Application Data
   decision and recovery gate while hashing the user's regular `Jamulus.ini`
   before and after. **Allow** must continue normal Host/Join through the
   dedicated `WebJam-native-v0.16.ini`. **Don't Allow** must start no musician
   Jamulus client and must give explicit quit-WebJam-completely, reopen, and
   choose-Allow guidance, with no in-process retry. After that full relaunch,
   **Allow** must recover normal Host/Join. In the same allowed controlled-pilot
   launch, Reference Track must not cause a second dialog or a generic/default
   purpose prompt. The regular `Jamulus.ini` must remain unchanged throughout.
   Because macOS can ask again after WebJam quits, record and retest a repeated
   prompt rather than claiming durable install-level permission.

If any gate is not run, report it as **NOT RUN**. A process launch, synthetic
JACK graph, or connected roster is not evidence that a person heard audio.

## Version and publication rule

The public tags and attached assets are immutable historical evidence. The
failed `v0.18.0` credentialed release attempt remains fixed at its source
commit; the candidate-lane change is versioned as `v0.18.1`. Never move a tag
or silently replace archives on a published release.

Pocket Stage begins with the separately versioned v0.19.0 candidate. Both Mac
containers must carry **Pocket Stage iPhone Setup** with the generated Xcode
project, complete local Swift package, executable opener, and desktop
version/build metadata produced by the same CI run. Mount/extract both targets
and verify that inventory before publishing. The kit uses an Apple Personal
Team for temporary owner-device installation; it is not a pre-signed iOS
release asset and does not change the exact eight-file GitHub release inventory.

The v0.20.0 source candidate added external-only Webex handoff and the
capability-gated macOS Reference Track pilot. Its Mac package must retain the
Pocket Stage kit and include the Reference Track modules from the same exact
source/build identity. Automated package checks do not replace the explicitly
**NOT RUN** physical Reference Track audibility and isolation gates.

The v0.21.0 source candidate adds standalone Reference Studio projects. It
retains Host/Join, schema-2 session Studio, Pocket Stage, external-only Webex,
and the locked Reference Track pilot without reinterpreting their saved state.
Package checks must exercise a project path containing spaces, immutable
collected media, project plus schema-3 Studio save/reopen, atomic Save As,
recovery discovery, waveform preparation, one deterministic local render, and
WAV/FLAC bounce rollback and checksums. They must also prove the packaged
runtime contains the reviewed notices and SBOM required by the Reference Studio
decoder/mixer graph. MP3 bounce is unavailable unless a separately identified
adapter passes its runtime self-test and license policy; no default adapter
ships in this candidate.

The exact v0.21.0 draft inventory is:

- `WebJam-v0.21.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.21.0-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.21.0-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.21.0-SHA256SUMS.txt`

The checksum file must contain exactly seven entries, one for each package and
not for itself. Windows is unsigned. Both Mac architectures are ad-hoc signed
and unnotarized. Physical interface routing, Reference Studio audibility,
latency calibration, recording/recovery, long-session hardware use,
SmartScreen/Gatekeeper behavior, signed trust, and notarization remain
**NOT RUN** unless evidence names the exact asset and SHA-256.

Candidate tag CI attaches the explicitly labeled unsigned Windows Setup and
ZIP, both explicitly labeled ad-hoc Mac DMGs and ZIPs, and the Ubuntu ZIP to a
draft. It generates, verifies, and attaches
`WebJam-v<VERSION>-SHA256SUMS.txt` for that exact seven-package set. A
maintainer first reviews the draft warning and CI evidence, then manually runs
**Publish Verified WebJam Release** with that exact tag. The promotion workflow
downloads the draft, rejects any inventory other than the seven packages plus
the checksum manifest, verifies every checksum, publishes it as a
non-prerelease with GitHub's explicit `--latest` setting, and finally proves
that the `/releases/latest` endpoint reports the same tag and assets. Never
publish the draft directly from the web page, because that bypasses this final
inventory and Latest assertion. Any later production-trusted release still
requires the credentialed rehearsals and physical gates above.

For v0.21.0, completion means the verified promotion workflow has published the
draft as a non-prerelease with GitHub's explicit **Latest** setting and the
public `/releases/latest` response contains the exact inventory above. A green
four-target matrix, retained Windows Actions artifact, pushed tag, or complete
draft does not satisfy the release request on its own.

### v0.22.0 blocked signed-component candidate evidence

The immutable v0.22.0 source candidate added an independently updatable Jamulus boundary,
exact Jamulus-name validation/preview, native Webex detection with explicit
official Cisco installer handoff, and path-free updater/Webex support evidence.
It preserves the v0.21.0 Reference Studio, Pocket Stage kit, embedded Jamulus
3.12.2 fallback, and locked Reference Track behavior. Never move or replace the
v0.21.0 tag or assets.

The unpublished v0.22.0 draft inventory was:

- `WebJam-v0.22.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.0-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.0-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.0-SHA256SUMS.txt`

Its checksum file contained exactly seven entries and did not list itself. The
separate signed component catalog, Jamulus installers/DMG/packages, signing
key, public-key source, HEADLESS evidence, and source archives were excluded.
Tag CI created the draft, but the required exact-package check then proved that
the frozen updater could not establish catalog TLS trust. The promotion
workflow was therefore never authorized to publish it. The inventory and
promotion rules above are retained only as historical evidence of the blocked
gate, not as instructions to complete a v0.22.0 release. Keep the annotated tag
and tagged bytes permanently. Retain the unpublished draft untouched until
v0.22.1 is publicly verified, then delete only that obsolete draft by release
ID. Never publish, move, or rebuild v0.22.0.

The catalog is operated in a separate non-Latest release named
`jamulus-components-v1`. Generate its payload from the checked-in compatibility
registry, use the offline Ed25519 private key only through the release tool,
increase the prior accepted sequence, limit validity to at most 31 days, sign
canonical JSON, and verify it with the packaged public key before upload. The
catalog release must remain a prerelease so it cannot become the repository's
Latest desktop release. An expired catalog is a normal fail-closed condition:
clients keep the verified managed/current component or embedded 3.12.2
fallback and report that update checking is unavailable.

The blocked v0.22.0 gate would also have required proof that the updater
rejected invalid signatures, expiry, replay, rollback, same-sequence
equivocation, bad hosts/redirects, wrong target, architecture, publisher,
filename, size/hash, partial downloads, unexpected inventory,
traversal/symlinks, lock contention, corrupt pointers, and busy activation.
Those requirements carry forward to v0.22.1; they do not authorize publication
of v0.22.0. Platform approval evidence must likewise stay attached to the exact
candidate under test.

v0.22.0 is permanently blocked from publication. Its green build matrix does
not supersede the failed exact-package updater gate.
Automated source and package evidence does not convert physical audio, Webex
joining, hardware recovery, long-session, Windows publisher trust, WebJam
Developer ID/notarization, or managed-device policy into PASS; record those as
**NOT RUN** unless performed against the exact published assets.

### Historical v0.22.1 frozen-updater reliability candidate

v0.22.1 retains the reviewed v0.22.0 product behavior and fixes the frozen
runtime's TLS trust selection. The updater explicitly loads the packaged,
release-locked Certifi CA bytes, requires hostname verification and TLS 1.2 or
newer, and ignores CA environment overrides. Its fixed-URL frozen smoke must
verify the live signed catalog from an exact package before promotion.

The exact v0.22.1 draft inventory is:

- `WebJam-v0.22.1-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.1-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.1-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.1-SHA256SUMS.txt`

After tag CI creates the draft, independently download all eight assets, prove
the manifest lists exactly the other seven files, and verify every checksum.
Then renew `jamulus-components-v1` to signed sequence 2 targeting exact WebJam
0.22.1 without moving its stable tag. Run the fixed frozen probe from the exact
Mac package. Set the three digest variables from the independently downloaded
and verified public catalog exactly as described in
`docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md`; do not copy values from the
desktop package:

```bash
.venv/bin/python tests/support/run_frozen_component_catalog_smoke.py \
  --binary /path/to/WebJam.app/Contents/MacOS/WebJam \
  --expected-version 0.22.1 \
  --expected-sequence 2 \
  --expected-target macos-arm64 \
  --expected-jamulus-version 3.12.3 \
  --expected-catalog-envelope-sha256 "$catalog_envelope_sha256" \
  --expected-catalog-payload-sha256 "$catalog_payload_sha256" \
  --expected-signer-fingerprint-sha256 "$signer_fingerprint_sha256"
```

The probe reported packaged Certifi trust ready, environment CA overrides
ignored, the explicit redirect allowlist, catalog sequence 2, approved Jamulus
3.12.3, and exact agreement with independently recorded envelope, payload, and
signer digests. That publication procedure and its candidate-specific workflow
configuration are historical evidence only. Do not copy v0.22.1 run or
artifact identifiers into a new publisher. The current dynamic promotion
contract is defined below.

### Published v0.22.2 demo-navigation candidate — historical record

v0.22.2 is a new immutable patch after v0.22.1; never move, replace, or rebuild
the v0.22.1 tag or assets. It adds direct Webex/Track/Studio navigation,
side-effect-free Conversation access, source-first Reference Track loading,
redacted Track diagnostics, and the manual-launch/reconnect generation fix.
It retains the same unsigned Windows and Linux/private portable trust boundary,
ad-hoc-signed and unnotarized Mac boundary, Trinity identity, immutable
Jamulus 3.12.2 fallback, and draft-first publication controls.

The verified publisher completed this procedure. GitHub now reports
[`v0.22.2`](https://github.com/rupret007/webjam/releases/tag/v0.22.2) as a
published, non-prerelease **Latest** release with the exact inventory below.
The separate non-Latest `jamulus-components-v1` release contains signed
sequence 3 for exact WebJam 0.22.2. The remaining text in this subsection is
the immutable release procedure and evidence boundary, not a pending
publication instruction.

The exact v0.22.2 draft inventory is:

- `WebJam-v0.22.2-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.2-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.2-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.2-SHA256SUMS.txt`

After tag CI creates the draft, independently download all eight assets. Prove
the manifest lists exactly the other seven files and verify every checksum.
The signed Jamulus catalog is version-specific: publish and independently
verify a monotonic catalog sequence **3 or greater** targeting exact WebJam
0.22.2 before testing the frozen updater or promoting the desktop draft. For
the first clean attempt this is sequence 3; if that exact sequence has already
been published, redownload and verify it instead of regenerating or advancing
it casually. Never reuse the exact-v0.22.1 sequence-2 catalog as v0.22.2
authorization, never move the stable component-channel tag, and never add the
catalog to the desktop asset inventory.

From both exact Mac packages, verify that the frozen catalog probe reports the
independently recorded public envelope, payload, signer, sequence, expiry,
target, Jamulus version, and packaged Certifi trust state. In the real packaged
UI, verify:

- direct **Webex** and its More entry show Conversation without launching;
- **Show Webex App** activates verified running Webex or launches the verified
  app itself when stopped, with no URL/document, browser, or meeting handoff;
- **Join / Open Meeting** performs only one explicit URL handoff;
- **Mute in Webex** focuses Webex for its own control without claiming mute or
  changing Jamulus;
- direct **Studio** reaches the existing live/offline Studio route;
- host-only **Reference Track** can load and inspect a source while Play remains locked,
  and **Recheck Route** starts no playback;
- a deliberately slow hosted-server start does not let reconnect supervision
  cancel the manual Jamulus launch generation.

Physical two-endpoint music, Reference Track audibility/isolation, external
Webex join/mute state, hardware recovery, long-session behavior, Windows
publisher trust, WebJam Developer ID/notarization, and managed-device policy
remain **NOT RUN** unless the evidence names the exact v0.22.2 asset and
checksum.

The promotion workflow must dynamically discover the unique successful
`v0.22.2` tag-CI run for the exact tag commit and all four expected native
artifacts. It rejects missing or ambiguous runs/artifacts and verifies their
GitHub digests, package inventories, versions, build IDs, and inner checksums.
Do not enter or hardcode run IDs, workflow IDs, artifact IDs, sizes, or digests
in the workflow. Require the draft assets to match those independently tested
packages before checking the complete inventory. Only then run **Publish
Verified WebJam Release** for v0.22.2. Independently prove that GitHub
`/releases/latest` reports v0.22.2 as a published non-prerelease with exactly
the seven packages plus checksum manifest. Never publish the draft directly
from the web page.
