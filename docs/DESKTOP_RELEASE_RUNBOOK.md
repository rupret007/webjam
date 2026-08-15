# Desktop release runbook

> **v0.22.5 historical candidate:** the exact eight-asset release is immutable.
> It is never moved or replaced when a later candidate becomes GitHub Latest.

> **Historical boundary:** v0.22.5 and every earlier tag, asset, checksum, and
> release record remain immutable and must never be replaced by the current
> source line.

> **v0.23.0 published testing boundary:** the Shared Track, Record Session, and
> Studio candidate is an immutable historical release. Tag CI `31368570400`
> produced its exact eight assets; protected promotion `31371289158` verified
> and published release `367773776`. Every physical/credentialed gate remains
> **NOT RUN** until separately observed against exact checksums.

> **v0.24.0 published testing boundary:** this recording-first line is the
> immutable GitHub Latest private test release. Its exact annotated tag,
> successful four-target tag CI, eight-asset draft and checksum manifest,
> sealed-v3 rejection proof, and pinned protected promotion passed. Never
> rebuild or replace v0.24.0 or v0.23.0 assets with later source.

> **v0.25.0 source-candidate boundary:** the current tree reports v0.25.0 but
> no annotated tag, tag object/commit, successful tag CI run, draft release,
> body digest, inventory digest, asset IDs/sizes/digests, checksum manifest, or
> promotion result exists yet. GitHub Latest remains v0.24.0. The v0.25
> publisher is deliberately non-executable until those post-tag facts are
> recorded and independently reviewed.

This is the release boundary for WebJam's native desktop packages. The GitHub
Actions `build-desktop` matrix is the authoritative source builder. Version
tags may promote its explicitly unsigned/ad-hoc outputs as a private test
candidate. The environment-bound `windows-release-trust` and
`macos-release-trust` jobs remain the only authoritative packagers for a future
signed platform release, once their GitHub Environments have real protection
rules and credentials. Do not reuse a package from a different source commit or
replace assets on a published tag.

For v0.24.0, never reuse the v0.22.5 v3 catalog: it authorizes exact WebJam
0.22.5 only. The historical `publish-v023-testing-release.yml` lane proved v3
for v0.22.5 and rejected v0.23.0. The pinned v0.24 fallback-only lane likewise
proved that catalog is rejected for v0.24.0, then bound one successful tag CI
run, the exact draft inventory, all asset digests, the checksum manifest, and
the protected `release-latest` environment before publication.
That private test candidate uses embedded Jamulus 3.12.2 only when no compatible
managed catalog exists. A new immutable signed channel remains mandatory before
managed 3.12.3 downloads can be advertised. Run the
[v0.24 physical checklist](../V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md)
against exact assets; automation cannot convert its **NOT RUN** rows to PASS.

For v0.25.0, the sealed v3 catalog must likewise be proved valid for historical
v0.22.5 and rejected for exact v0.25.0. The source registry narrowly recognizes
the unchanged audited Jamulus 3.12.2/3.12.3 identities through 0.25.0 and
rejects 0.25.1; that baked policy is not a signed managed-update catalog. The
candidate therefore remains fallback-only unless a new immutable signed
version-specific component channel is separately completed.

## v0.25.0 pre-promotion procedure

The checked-in `.github/workflows/publish-v025-testing-release.yml` is a
read-only placeholder with its only job forced false. It cannot publish or make
a release Latest. Do not remove that guard before all of the following exist:

1. Merge the fully reviewed source, docs, tests, version, SBOM, package copy,
   and checklist to `master`; freeze release-related pushes.
2. Create and push one annotated `v0.25.0` tag at the exact reviewed master
   commit. Record the tag-object SHA and peeled tag-commit SHA.
3. Let the tag run `.github/workflows/ci.yml` once successfully across all four
   native targets and create the expected eight-asset draft. Record the unique
   successful tag CI run ID and exact draft release ID.
4. Query the draft through the GitHub API. Independently download every asset,
   verify each GitHub SHA-256 digest, require the seven package entries in
   `WebJam-v0.25.0-SHA256SUMS.txt`, then calculate the canonical release-body
   and sorted `{id,name,size,digest}` inventory SHA-256 values.
5. Replace every explicit `UNSET_POST_TAG_*` placeholder with those observed
   values and replace the forced-false guard only in a separately reviewed
   commit. The enabled workflow must preserve the v0.24 lane's exact tag,
   descendant-master, unique tag-CI, immutable draft, eight-asset, checksum,
   sealed-catalog-rejection, embedded-fallback, protected-environment, and
   post-publication redownload checks, changed only for v0.25 pins.
6. Rerun the workflow static tests from the release-control commit. Dispatch
   only from exact `master` through `release-latest`, then independently verify
   GitHub Latest and record the real evidence below the immutable v0.24 record.

No value in this pre-promotion section is release evidence. Run the
[v0.25 physical checklist](../V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
only against the exact published asset hashes; all rows begin **NOT RUN**.

## Supported targets

| Target | Runner | Preferred release asset | Portable fallback | Product scope |
| --- | --- | --- | --- | --- |
| Windows x64 | `windows-2025` | `WebJam-v<VERSION>-windows-x64-UNSIGNED-TEST-ONLY-setup.exe` | `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip` | Join and Reference Studio |
| Intel macOS | `macos-15-intel` | `WebJam-v<VERSION>-macos-x64-ADHOC-TEST-ONLY.dmg` | `WebJam-macos-x64-ADHOC-TEST-ONLY.zip` | Host, Join, and Reference Studio |
| Apple Silicon macOS | `macos-14` | `WebJam-v<VERSION>-macos-arm64-ADHOC-TEST-ONLY.dmg` | `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip` | Host, Join, and Reference Studio |
| Linux x64 | `ubuntu-22.04` | `WebJam-linux-x64.zip` | — | Join and Reference Studio on Ubuntu 22.04 x64 |

Windows and Linux deliberately leave the profile-specific **Host** action
disabled. A release must not describe them as hosting replacements for the
managed macOS Jamulus server.

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
- the source, mounted-DMG copy, and portable-ZIP copy of every outer and nested
  Mac bundle must omit `NSAppDataUsageDescription`; any declaration is a
  package failure because Host, Join, and Reference Track use only WebJam-owned
  Application Support storage and must not request Other Application Data;
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
by `soundfile.check_format("MP3")` and the descriptor-bound structural
frame/duration checks; no package may claim MP3 import merely because
SoundFile is installed. Each frozen target must also prove an exact final
partial block and normal end-of-song transition. MP3 bounce is a separate,
disabled-by-default adapter capability, and no default encoder adapter ships
in this release.

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

The repository currently has three GitHub Environments. As of 2026-08-11,
`release-latest` requires review by maintainer `rupret007`, permits that
single maintainer to review a private-candidate deployment, and restricts
deployments to the `master` branch. Administrator bypass remains available.
The two native trust environments still have empty protection rules and no
deployment-branch policy, so treat their names as workflow routing only:

- `windows-release`, containing only the two Windows secrets and pinned
  `WINDOWS_CODESIGN_SUBJECT` environment variable when using the eligible PFX
  path, or the least-privilege OIDC/provider configuration selected for remote
  signing;
- `macos-release`, containing only the five Apple secrets and pinned
  `APPLE_DEVELOPER_TEAM_ID` environment variable;
- `release-latest`, containing no signing secret and protecting the final
  revalidation-and-publication job.

For a production-trusted release, require an independent reviewer, prevent
self-review, disable administrator bypass when repository policy allows it,
and restrict the native trust deployments to approved rehearsal refs and
version tags. The two trust workflow jobs are already isolated and explicitly
bound to their environments with `deployment: false`; the publisher is bound
separately to `release-latest`. Environment secrets remain unavailable until
applicable protection rules pass. Do not copy trust credentials into
repository-level secrets. The current native trust environments contain no
release credentials, so protection and credential provisioning remain
production-trusted-release blockers but not private-candidate blockers.

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
6. On each Mac architecture, run the installed app's permissionless Jamulus
   profile gate with no Full Disk Access and, where possible, an existing
   denied Other Application Data state. Hash the user's regular `Jamulus.ini`
   before and after. Fresh Host/Join must open the dedicated
   `WebJam-native-v0.16.ini`, allow its one-time native sound setup, and reach
   authenticated connection evidence without an App Data prompt. A separate
   microphone prompt is recorded as audio permission, not App Data. Repeat
   after a complete quit to prove the returning path. In the same controlled
   pilot, Reference Track must keep its profile and control files below
   `~/Library/Application Support/WebJam/runtime/reference-track` and must not
   prompt for another application's data. The regular `Jamulus.ini` must
   remain unchanged throughout. Any App Data prompt is a package failure; do
   not grant it or add WebJam to Full Disk Access.
7. For Presence v2 recorder correlation, use the exact packaged hash with a
   hosted server and at least two independent participant clients. Record that
   each client sees its own client-local channel zero while the host correlates
   distinct server ordinals and recorder stems. Repeat with the same visible
   name but distinct complete profiles, then with identical complete profiles:
   distinct profiles must remain separate, while an indistinguishable remote
   row must fail closed with truthful readiness and no guessed stem. Let the
   unchanged roster pass through two lease rotations during a take. Exercise a
   capture opt-in during rollover followed by opt-out, then disconnect and
   reconnect one participant while producing identifiable audio before and after.
   Verify one durable participant owns separate immutable media segments, the
   opted-in Local Original remains expected, server stems are not crossed, and
   missing/ambiguous evidence stays visible. Inspect logs, participant-registry
   files, take evidence, diagnostics, and the support bundle for challenges,
   private roster fingerprints, raw profiles/local channel lists, process IDs,
   tokens, addresses, and paths. Do not describe the remote ordinal as
   cryptographic Jamulus identity; this gate uses private invites shared only
   with trusted collaborators.

If any gate is not run, report it as **NOT RUN**. A process launch, synthetic
JACK graph, or connected roster is not evidence that a person heard audio.

Presence v2 physical evidence currently remains:

| Gate | Status |
| --- | --- |
| Two independent participant machines with distinct recorder stems | **NOT RUN** |
| Same-name/distinct-profile and identical-full-profile behavior | **NOT RUN** |
| Two live lease rotations during one take | **NOT RUN** |
| Capture opt-in/opt-out and delivered Local Original | **NOT RUN** |
| Audible reconnect represented by separate `MediaSegment` records | **NOT RUN** |
| Exact-package disk/log/diagnostics/support privacy inspection | **NOT RUN** |

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

The verified publisher completed this procedure. GitHub reported
[`v0.22.2`](https://github.com/rupret007/webjam/releases/tag/v0.22.2) as a
published, non-prerelease **Latest** release at that time; immutable v0.22.3
now supersedes it as Latest. The separate component release carried signed
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
- **Open Webex to Mute** focuses Webex for its own control without claiming mute or
  changing Jamulus;
- direct **Studio** reaches the existing live/offline Studio route;
- host-only **Reference Track** can load and inspect a source; **Play** stays
  fail-closed unless the machine-derived isolated-route proof is current, and
  **Recheck Route** starts no playback;
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

### v0.22.3 security and reliability candidate — published record

v0.22.3 is a new patch identity after immutable v0.22.2. Never move, replace,
rebuild, delete, or retag either release. Its exact annotated tag, package,
catalog, checksum, provenance, and verified-promotion gates passed before it
became immutable GitHub Latest.

This candidate upgrades `cryptography` to 50.0.0, remediating
CVE-2026-69247, CVE-2026-69248, and CVE-2026-69249. Windows, Linux, and
Apple-silicon macOS use exact hash-locked upstream wheels. Upstream no longer
publishes an Intel macOS wheel, so Intel macOS has one reviewed native x86_64
source-build exception. It consumes only the official hash-locked
`cryptography` source and build inputs, builds a private static OpenSSL 3.5.7
LTS prefix from its verified source, and proves architecture, OpenSSL identity,
static linkage, installed runtime paths, license evidence, and final frozen
inventory. No other target or dependency may use that exception.

The exact v0.22.3 published inventory is:

- `WebJam-v0.22.3-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.3-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.3-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.3-SHA256SUMS.txt`

The checksum manifest must contain exactly seven entries for the other seven
files and must not list itself. The release title is exactly
`WebJam v0.22.3 — unsigned private test candidate.` The opening notes must
state that Windows is unsigned, macOS is ad-hoc signed and unnotarized, this is
not a production-trusted release, and remaining physical and credentialed
gates are **NOT RUN**. No Apple Developer account or notarization result is
claimed.

Before tag CI creates the draft, an admin-authenticated maintainer must enable
repository immutable releases and read the setting back successfully:

```bash
gh api --method PUT \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  repos/rupret007/webjam/immutable-releases
test "$(gh api \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  repos/rupret007/webjam/immutable-releases --jq .enabled)" = true
```

Do not move this administration check into tag CI: the repository-scoped
`GITHUB_TOKEN` has no Administration permission and cannot read this setting.
Draft status is the only period in which assets and notes may still be
assembled. Final promotion must prove the published release reports
`immutable=true`, `draft=false`, `prerelease=false`, exact tag `v0.22.3`, and
the exact title, required trust/security warning text, and eight-asset
inventory above through the draft, publish response, and `/releases/latest`.
Any title, body, or asset mutation after the initial draft verification must
fail closed before publication.

The Jamulus catalog is a separate, public non-Latest prerelease. Starting from
the independently verified public sequence 3 for exact WebJam 0.22.2, generate
and publish exactly one signature-valid sequence 4 catalog for exact WebJam
0.22.3 without moving the stable `jamulus-components-v1` tag. Independently
redownload those public bytes, bind their GitHub digest plus envelope, payload,
and signer SHA-256 values, and run the fixed-URL frozen probe against every
exact desktop target. Sequence 3 cannot authorize v0.22.3, and different
sequence-4 bytes are equivocation.

Only after the four native tag packages, exact architectures and metadata,
embedded Jamulus inventories/checksums, signed catalog, TLS behavior,
redaction, Linux plus native Windows/Apple-silicon/Intel package launches, and
checksum manifest pass may a maintainer run
**Publish Verified WebJam Release** for `v0.22.3`. The publisher must discover
the unique successful tag-CI run dynamically and must not accept manually
entered run, artifact, size, or digest identities.

Publication completed on 2026-08-04 UTC. Feature run `30879823262`, master run
`30881094293`, tag run `30882232394`, and protected promotion run
`30884167136` all passed for commit
`19ae56905d1a770ba310534126b7a568d313aec3`. Release ID `364655595` is
immutable and Latest with the exact inventory above. The separate component
release is an immutable non-Latest prerelease carrying signed sequence 4 for
exact WebJam 0.22.3.

The sealed package-internal Windows, macOS, and Linux read-me files retain
their pre-publication header. Their stated condition—use only after checksum
and verified promotion pass—is now satisfied, but immutable package bytes must
not be rewritten. Update that header before tagging the next version; never
rebuild or replace v0.22.3 to correct copy.

Publishing this explicitly untrusted private candidate does not convert
physical evidence to PASS. Two-Mac audibility, physical interface
disconnect/reconnect, sleep/wake, interruption and recording recovery,
long-session use, Presence-v2 recorder correlation, Reference Track
audibility/isolation, Reference Studio physical input/output and latency,
external-editor import, real Webex join/focus behavior, Pocket Stage physical
pairing, SmartScreen, Gatekeeper, publisher signing, Developer ID,
notarization, and managed-device policy remain **NOT RUN** until dated evidence
names an exact v0.22.3 asset, build ID, SHA-256, environment, and evidence
location.

### v0.22.4 DAW and reliability candidate — historical published record

v0.22.4 is a new immutable patch identity after v0.22.3. Never move, replace,
rebuild, delete, or retag either release. It carries the Reference Studio
multi-region editing and loop Overdub work, the stale playback-status fix, and
the bounded macOS disk-image retry.

The exact v0.22.4 published inventory is:

- `WebJam-v0.22.4-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.4-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.4-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.4-SHA256SUMS.txt`

The separate `jamulus-components-v2` prerelease carries signed sequence 5
for exact WebJam 0.22.4. The sealed v1 catalog and its v0.22.3 authorization
remain historical. The v0.22.4 package remains unsigned on Windows and
ad-hoc signed and unnotarized on macOS; physical audio, hardware, Webex,
Pocket Stage, Gatekeeper, SmartScreen, signing, and notarization gates remain
**NOT RUN** unless recorded against the exact published asset and checksum.

Publication completed on 2026-08-05 UTC. Tag CI run `30979207513`, source
master CI run `30978055097`, and protected promotion run `30980588968` passed
for commit `9baed5329984ee48591f75a86cb42cebc1e3a62f`. Release ID `365318104`
was GitHub **Latest** when published and remains immutable at
https://github.com/rupret007/webjam/releases/tag/v0.22.4. Its checksum
manifest is `WebJam-v0.22.4-SHA256SUMS.txt`; the separate component release is
ID `365297898` at the immutable `jamulus-components-v2` tag.

### v0.22.5 reference-demo reliability candidate — published record

v0.22.5 is an immutable published patch identity. It carries the real-world
MP3 and Reference Track acceptance work plus bounded first-demo
safety/presentation fixes. It did not move or replace v0.22.4 or either sealed
component channel.

The exact published release inventory is:

- `WebJam-v0.22.5-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.5-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.5-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.5-SHA256SUMS.txt`

The original promotion contract required completing the new fixed
`jamulus-components-v3` boundary in
[`JAMULUS_COMPONENT_RELEASE_RUNBOOK.md`](JAMULUS_COMPONENT_RELEASE_RUNBOOK.md).
Require signed sequence 6 for exact WebJam 0.22.5, its independently verified
one-asset non-Latest prerelease, and the final pinned channel anchor. The
completed gated sequence is retained below as historical evidence:

1. Record the independently verified v3 release IDs, catalog hashes, signer,
   expiry, and asset evidence in the required follow-up commit. After separate
   approval to push that evidence, require a clean post-evidence final `master`,
   exact v0.22.5 metadata, and green source CI including all four desktop
   targets.
2. After separate explicit verification-dispatch approval, run this read-only
   gate with the successful final-master CI run and its exact commit:

   ```bash
   gh workflow run verify-component-candidate.yml \
     --repo rupret007/webjam \
     --ref master \
     -f source_run_id=<successful-master-ci-run-id> \
     -f expected_sha=<exact-40-character-origin-master-sha>
   ```

   Require all four native jobs and the final read-only identity-revalidation
   job to pass. They bind each Actions artifact ID and wrapper digest, then hash
   and size every contained release file. The DMG and Windows Setup containers
   are inventory- and hash-bound; only the four portable ZIP packages are
   live-launched against immutable v3 sequence 6.
   If `master` changes, run CI and this proof again. Preserve the successful
   post-evidence proof and freeze `master` without another source commit through
   the desktop tag, draft, and promotion. A CI run from before this verification
   workflow landed is stale by construction; use a new green push run from the
   exact current-master commit.
3. After explicit approval, create and verify an annotated `v0.22.5` tag at
   exact `origin/master`; after separate approval, push only that tag.
4. Let tag CI create the draft. Never create, upload, replace, or publish the
   desktop release manually.
5. Verify the draft has the title/trust warning above, exactly the eight assets
   listed here, and seven checksum-manifest entries matching fresh downloads.
6. After explicit publication approval, dispatch **Publish Verified WebJam
   Release** for `v0.22.5`; require success before calling it public or Latest.
7. Re-read the public API and require immutable, non-draft, non-prerelease,
   Latest state plus the same asset IDs, sizes, digests, and checksums.

Windows remains unsigned. macOS remains ad-hoc signed and unnotarized. Physical
two-musician audio, real Reference Track audibility/isolation, Webex behavior,
Pocket Stage, Gatekeeper, SmartScreen, signing, and notarization remain
**NOT RUN** until dated evidence names an exact v0.22.5 asset and SHA-256.

Publication completed on 2026-08-07 UTC for commit
`d7d0039759e8334407fe2e6ed9e42edf0d7ef639`. Source CI run `31206070715`,
component-package verification run `31208008965`, tag CI run `31208271585`,
and protected promotion run `31210531934` all passed. Immutable release ID
`366957478` was GitHub **Latest** when published and remains immutable at
https://github.com/rupret007/webjam/releases/tag/v0.22.5. Its exact eight assets
include checksum manifest `WebJam-v0.22.5-SHA256SUMS.txt`; the separate
immutable v3 component release is ID `366930115` at tag
`jamulus-components-v3`.

### v0.24.0 recording-first candidate — published Latest record

v0.24.0 is an immutable private test release and a new package identity after
v0.23.0. It was published as GitHub **Latest** at
https://github.com/rupret007/webjam/releases/tag/v0.24.0. Never move its
annotated tag, rebuild or replace an asset, edit the immutable release, or
substitute a later `master` commit or branch artifact for its tagged bytes.

The complete publication chain is pinned below:

| Evidence | Exact value |
| --- | --- |
| Annotated tag | `v0.24.0` |
| Annotated tag object | `99cb3798a925a39b70159e3a1a56166e98b5c316` |
| Peeled tag/source commit | `9edada8613b5aca6fec6a4110e2322611ad6658e` |
| Source/master CI | `31540572960` |
| Successful tag CI | `31542495182`, attempt 2 |
| Tag draft-release job | `93953326611` |
| Release-control commit | `28c9d673985f81729b316f352f13704ffd0e845e` |
| Release-control CI | `31544471336` |
| Protected publisher | run `31546157181`; proof job `93959002476`; publish job `93959070227` |
| GitHub release | ID `368897541`; published `2026-08-11T23:23:12Z` |
| Exact release-body SHA-256 | `7eeee822a22929289d3d6aee792050e34633366b4f6708a5c9592f4a97315487` |
| Canonical asset-inventory SHA-256 | `83f9724cb83c79087c14e07beb873ef690ed43ac7a1d83218af1a0dc786a4184` |

The inventory digest above is over the compact JSON array of
`{id,name,size,digest}` objects sorted by asset name. The exact immutable asset
inventory is:

| Asset ID | Filename | Size (bytes) | GitHub digest |
| ---: | --- | ---: | --- |
| `510747174` | `WebJam-linux-x64.zip` | `168017509` | `sha256:a8d4dd3bc0d6d3b8244baa85bd26fc12cf7e81bcd4187267c41a16bf471591c9` |
| `510747172` | `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip` | `216031863` | `sha256:4f95e0e7de5ae59a9aec296869f1fd4d5f8c598e76a95a45981b7827f28cabc4` |
| `510747168` | `WebJam-macos-x64-ADHOC-TEST-ONLY.zip` | `222343926` | `sha256:91d2dd05024ea558bd81b2a596a09c545ad9f72ae690c2ef7bce1d6d33360da5` |
| `510747169` | `WebJam-v0.24.0-SHA256SUMS.txt` | `749` | `sha256:e24810b3d73c4032bc578f8eb236f64f450152c907843763830bbf8300b081d1` |
| `510747170` | `WebJam-v0.24.0-macos-arm64-ADHOC-TEST-ONLY.dmg` | `217132079` | `sha256:1d6c698aab8382a8098a96b6602345e4bcb98770aaab6e56397a33f02d1d951a` |
| `510747175` | `WebJam-v0.24.0-macos-x64-ADHOC-TEST-ONLY.dmg` | `223311523` | `sha256:1af795ab85ee246cf2c36785400e86a7f35b91883ed03a2097616e48039feac8` |
| `510747173` | `WebJam-v0.24.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe` | `144648416` | `sha256:b463ddefb753f3ee745dcf7a58e20d2b69274d3814c9c1daf54c7a46aaf5b4bc` |
| `510747171` | `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip` | `165359997` | `sha256:422b457f02291fbe5ecd55728b4d66ee4cde5112526d1461b8c1fa792639b79c` |

The checksum manifest's own GitHub digest is
`sha256:e24810b3d73c4032bc578f8eb236f64f450152c907843763830bbf8300b081d1`.
Its seven package entries are exactly:

```text
a8d4dd3bc0d6d3b8244baa85bd26fc12cf7e81bcd4187267c41a16bf471591c9  WebJam-linux-x64.zip
4f95e0e7de5ae59a9aec296869f1fd4d5f8c598e76a95a45981b7827f28cabc4  WebJam-macos-arm64-ADHOC-TEST-ONLY.zip
91d2dd05024ea558bd81b2a596a09c545ad9f72ae690c2ef7bce1d6d33360da5  WebJam-macos-x64-ADHOC-TEST-ONLY.zip
1d6c698aab8382a8098a96b6602345e4bcb98770aaab6e56397a33f02d1d951a  WebJam-v0.24.0-macos-arm64-ADHOC-TEST-ONLY.dmg
1af795ab85ee246cf2c36785400e86a7f35b91883ed03a2097616e48039feac8  WebJam-v0.24.0-macos-x64-ADHOC-TEST-ONLY.dmg
b463ddefb753f3ee745dcf7a58e20d2b69274d3814c9c1daf54c7a46aaf5b4bc  WebJam-v0.24.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe
422b457f02291fbe5ecd55728b4d66ee4cde5112526d1461b8c1fa792639b79c  WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip
```

Source CI, successful tag CI attempt 2, its draft-release job, release-control
CI, protected read-only proof, protected publish job, and the post-publication
Latest redownload all passed. The protected publisher proved that immutable v3
sequence 6 remains valid only for exact WebJam 0.22.5 and rejects v0.24.0; no
component tag, release, or asset moved. The v0.24.0 packages therefore retain
the reviewed embedded Jamulus 3.12.2 fallback. Managed 3.12.3 download remains
unavailable until a new immutable signed version-specific channel exists.

Windows remains unsigned. Both Mac architectures remain ad-hoc signed and
unnotarized. Publication and automation do not prove physical audibility,
recording, Studio, meeting-platform handoff, SmartScreen, Gatekeeper, signing,
or notarization. Every such v0.24.0 result remains **NOT RUN** in the
[recording-first physical checklist](../V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md)
until dated evidence names one exact asset and checksum.
