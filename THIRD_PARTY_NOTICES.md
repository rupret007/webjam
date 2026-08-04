# Third-Party Notices

WebJam's own code is MIT-licensed (see `LICENSE`). This file covers
third-party software that WebJam **bundles** (ships inside its own
installers/artifacts) rather than merely depending on at build time.

## Inter typeface

WebJam bundles the [Inter](https://rsms.me/inter/) typeface — source at
[rsms/inter](https://github.com/rsms/inter) — as the app's UI font on
every platform.

- **Version bundled:** `4.1` ([release](https://github.com/rsms/inter/releases/tag/v4.1))
- **Files:** `Inter-Regular.ttf`, `Inter-Medium.ttf`, `Inter-SemiBold.ttf`,
  `Inter-Bold.ttf`, shipped unmodified at `webjam_qt/theme/fonts/` inside
  every WebJam build.
- **License:** SIL Open Font License 1.1, which permits bundling and
  redistribution with software. The full license text is included verbatim
  at [`licenses/INTER_OFL.txt`](licenses/INTER_OFL.txt). The font is not
  modified and keeps its Reserved Font Name "Inter".

## Jamulus

WebJam bundles an offline fallback from the official
[Jamulus](https://jamulus.io) client — source at
[jamulussoftware/jamulus](https://github.com/jamulussoftware/jamulus) — and
can install a newer, explicitly approved component in its per-user component
store after verifying a signed catalog and the exact downloaded bytes.

- **Immutable offline fallback:** `3.12.2` (tag
  [`r3_12_2`](https://github.com/jamulussoftware/jamulus/releases/tag/r3_12_2)).
  WebJam v0.22 retains this reviewed client, server, and HEADLESS Reference
  Track build inside its desktop packages. It is not modified by the updater.
- **Fallback license:** the exact GNU General Public License v2 text shipped by
  Jamulus 3.12.2 is included verbatim at
  [`licenses/JAMULUS_COPYING.txt`](licenses/JAMULUS_COPYING.txt) and is
  also placed alongside the bundled copy in every WebJam build (macOS:
  `WebJam.app/Contents/Resources/THIRD_PARTY_LICENSES/`; Windows:
  `_internal/Jamulus/` next to the bundled installer; Linux: the visible
  archive-root `Jamulus/` directory next to the bundled `.deb`).
- **Approved client/server update input:** official Jamulus `3.12.3` (tag
  [`r3_12_3`](https://github.com/jamulussoftware/jamulus/releases/tag/r3_12_3),
  source commit `74dc422116983a2173eb917cb4d6a403886b31e5`). Upstream now
  states that Jamulus is distributed under AGPL 3.0 or later, with code
  contributed before 3.12.1dev licensed under GPL 3.0 or later. The exact
  3.12.3 upstream COPYING text is preserved at
  [`licenses/JAMULUS_COPYING-r3_12_3.txt`](licenses/JAMULUS_COPYING-r3_12_3.txt).
  Official packages also carry their platform-specific third-party inventory.
  WebJam downloads only immutable assets whose size, SHA-256, target,
  architecture, publisher policy, runtime inventory, and legal inventory match
  both its compatibility registry and a valid signed component catalog.
- **Managed HEADLESS status:** **NOT APPROVED**. WebJam has a separate,
  evidence-only r3_12_3 HEADLESS patch/build profile with complete
  corresponding source and exact AGPL/GPL text. It cannot be added to an
  activating catalog until qualified review resolves the AGPL section 13
  network-source-offer question or approves a protocol-visible offer design.
  A CI-produced checksum is evidence, not approval. Reference Track therefore
  continues to use the embedded, reviewed 3.12.2 HEADLESS fallback.
- **How it's bundled, per platform:**
  - **macOS:** `Jamulus.app` from Jamulus's official release `.dmg` is
    nested inside `WebJam.app/Contents/Resources/Jamulus.app`. WebJam's build
    replaces the nested app's code signature with an ad-hoc signature that
    omits App Sandbox; this lets WebJam provision private, loopback-only
    JSON-RPC credentials in WebJam's own Application Support tree without
    asking musicians for Full Disk Access or Other Application Data. The
    upstream executable and framework contents are otherwise unchanged.
    WebJam launches it as a separate OS process and talks to it only over
    its public JSON-RPC/UDP interfaces — it is never linked against or
    merged into WebJam's own code.
    `JamulusServer.app` from the same release is prepared the same way and
    nested beside the client so a designated host needs no separate install.
    WebJam launches each as an independent process.
    Reference Track additionally uses `JamulusHeadlessClient.app`, a separate
    client-capable build from the same exact Jamulus commit
    (`ffca974ed4e47b8f4621f3b583c00db2f87974fa`). It is compiled with
    `CONFIG+=headless` and explicitly without `serveronly`, using Qt 6.10.2
    downloaded by the wheel-only, SHA-256-locked aqtinstall 3.3.0 build
    environment. It includes
    only QtCore, QtNetwork, QtXml, and QtConcurrent—not QtGui, QtWidgets, or
    QtMultimedia. The complete patched corresponding-source archive accompanies
    the binary inside the companion app; it includes the two-file compatibility
    patch, pinned dependency lock, build instructions, build/verifier scripts,
    and signing configuration. The four dynamically linked Qt frameworks are
    distributed under LGPLv3 with a dedicated notice and the exact unmodified
    Qt 6.10.2 qtbase source archive in the companion. Provenance, license text,
    both source archives, and the final executable checksum ship with the
    candidate and are verified before packaging.
    This companion does not replace the ordinary musician-facing `Jamulus.app`.
    Pinned inputs make the build auditable and repeatable, but WebJam does not
    claim bit-for-bit identity across different Apple clang or SDK builds.
    The resulting nested apps, and the private WebJam artifact that contains
    them, are ad-hoc signed and are **not notarized**; do not describe the
    prepared copies as upstream-notarized nested apps.
    The official DMG presents a Jamulus software-license agreement. An
    automatic component check may download the exact DMG, but the updater must
    not mount, extract, or stage it until the user explicitly accepts that
    agreement. Downloading is never treated as acceptance. Only after that
    explicit Agree action may the bounded installer pass the corresponding
    response to `hdiutil` for that one verified image. The signed upstream app
    bundles contain expected Qt framework symlinks. Those are accepted only as
    members of the exact, deeply verified upstream signature—not as a general
    permission to extract arbitrary symlinks.
  - **Windows:** Jamulus only publishes an NSIS *installer* executable
    (no portable binary), so Windows packaging carries the unmodified installer
    as a distribution dependency. On a clean Windows system, the Host/Join
    dialog offers an Install Jamulus action only after WebJam verifies the
    exact pinned filename and SHA-256; it verifies the file again immediately
    before launch. The upstream installer itself is unsigned, so its UAC
    publisher warning remains a separate release-certification disclosure.
  - **Linux:** the Ubuntu x86-64 build carries the official unmodified `.deb`
    as a distribution dependency plus a visible install helper. It is a
    join-only build certified only for Ubuntu 22.04 x64; other Ubuntu versions
    and Linux distributions are not certified. The helper installs Jamulus as
    `/usr/bin/jamulus`, which WebJam discovers without a custom path.
- **Separate-process boundary:** WebJam never statically or dynamically links
  Jamulus into WebJam's executable. It launches Jamulus as an independent
  process and communicates through its public JSON-RPC/UDP protocols, as it
  would with a separately installed copy. The applicable upstream license
  texts, exact upstream repository/tag/commit identities, and source-location
  directions are provided with their respective components. Complete patched
  corresponding source is bundled only where this notice says so explicitly.
  Ordinary upstream client/server packages do not claim that complete source
  archives are inside the desktop package. This notice does not make a legal
  determination beyond that audited product boundary.
- **Escape hatch:** source runs can use an installed/custom Jamulus path.
  Frozen builds resolve a fully verified managed component first, then the
  embedded 3.12.2 fallback, then a compatible explicit/system copy. They never
  follow an upstream "latest" pointer or silently activate an unapproved
  version.
- **Staying current safely:** automatic checks may download an approved
  component asynchronously, but activation waits for an idle lifecycle
  boundary. The managed macOS store retains a verified prior version for
  rollback. Windows and Linux installations remain operating-system-owned and
  use the embedded fallback rather than claiming app-managed rollback.
  Platform installers continue to require explicit user approval. The updater
  never mutates `WebJam.app`, invokes hidden elevation, or weakens
  operating-system trust policy.

## Pocket Stage Python runtime dependency inventory

The Pocket Stage source and frozen-application specification use the following
Python runtime packages. Versions are pinned in
`requirements-lock/release-constraints.txt` and the target-specific hash locks:

- [`cryptography` 50.0.0](https://github.com/pyca/cryptography) — ephemeral
  certificate/key creation and certificate serialization. License:
  [Apache License 2.0 OR BSD 3-Clause License](https://github.com/pyca/cryptography/blob/main/LICENSE);
  WebJam redistributes it under the verified BSD option copied from the pinned
  distribution at [`licenses/CRYPTOGRAPHY_LICENSE.txt`](licenses/CRYPTOGRAPHY_LICENSE.txt).
  Upstream no longer publishes or tests Intel macOS wheels. The Intel package
  therefore builds the same official 50.0.0 source archive in native x86_64 CI
  under the explicit, hash-pinned contract recorded in
  [`packaging/macos/CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt`](packaging/macos/CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt).
  That WebJam-verified exception statically links OpenSSL 3.5.7 LTS from its
  hash-verified source; OpenSSL is licensed under Apache License 2.0, with the
  redistributed text at [`licenses/OPENSSL_LICENSE.txt`](licenses/OPENSSL_LICENSE.txt).
  The reviewed Windows x64, Linux x64, and macOS arm64 upstream wheels instead
  statically embed OpenSSL 4.0.1; their exact wheel hashes and inspected native
  identities are recorded in
  [`packaging/CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt`](packaging/CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt).
- [`websockets` 16.1.1](https://github.com/python-websockets/websockets) —
  secure WebSocket runtime support used by the Pocket Stage gateway stack.
  License: [BSD 3-Clause License](https://github.com/python-websockets/websockets/blob/main/LICENSE),
  with the verified pinned-distribution text at
  [`licenses/WEBSOCKETS_LICENSE.txt`](licenses/WEBSOCKETS_LICENSE.txt).
- [`Segno` 1.6.6](https://github.com/heuer/segno) — QR-code rendering in the
  desktop pairing dialog. License:
  [BSD 3-Clause License](https://github.com/heuer/segno/blob/master/LICENSE),
  with the verified pinned-distribution text at
  [`licenses/SEGNO_LICENSE.txt`](licenses/SEGNO_LICENSE.txt).

This section is a dependency and license inventory, not an assertion that the
Pocket Stage developer preview is present in the already-published v0.18.1
packages. The links above identify the upstream projects and authoritative
license text. The three local copies were taken from the exact installed pinned
distributions and the frozen-application specification is configured to place
them in `THIRD_PARTY_LICENSES`; this does not claim that an already-published
artifact contains them. Any future promoted package must inventory its exact
dependency graph and verify the staged license files during package inspection.

## Complete frozen Python runtime and audio-codec inventory

The exact four-target Python release locks are classified by the reviewed
policy in
[`packaging/runtime-dependency-policy.json`](packaging/runtime-dependency-policy.json).
Its deterministic checker fails closed if a locked distribution is
unattributed, if a reviewed entry becomes stale, or if a selected frozen
runtime license is GPL or AGPL. LGPL remains permitted and attributed. This
Python-runtime rule does not reclassify the separately executed Jamulus
distribution above or PyInstaller's freeze-time bootloader exception.

The generated human-readable inventory is
[`THIRD_PARTY_NOTICES_RUNTIME.md`](THIRD_PARTY_NOTICES_RUNTIME.md), with a
matching CycloneDX 1.5 artifact at
[`packaging/WebJam-runtime-sbom.cdx.json`](packaging/WebJam-runtime-sbom.cdx.json). Both
files, the reviewed policy, SoundFile's exact BSD notice, SoundFile's wheel
codec notes, NumPy's wheel license inventory, and libsndfile's wheel-provided
LGPL text are verified in every native package.

Reference Studio does not add FFmpeg or a separate MP3 executable. MP3 import
remains a packaged-codec capability: WebJam must probe
`soundfile.check_format("MP3")` and hide or refuse MP3 operations when that
probe fails. Reference Studio MP3 bounce remains disabled by default and must
not be enabled by the import probe alone; no default encoder adapter is
bundled. SoundFile's libsndfile wheel payload and its libmpg123/libmp3lame
terms are recorded in the generated runtime notice.

## WebJam fabric transport

WebJam bundles `webjam-fabric`, its statically compiled native transport
sidecar, as a separate executable beside the desktop application. The binary
links pinned Pion ICE/STUN/TURN/transport modules, quic-go, and their permissive
transitive dependencies.

- **Exact versions and linked inventory:**
  [`transport/DEPENDENCIES.md`](transport/DEPENDENCIES.md)
- **Attribution and license mapping:**
  [`transport/NOTICE.md`](transport/NOTICE.md)
- **Full license texts:** [`transport/licenses/`](transport/licenses/)

The notice, inventory, and license texts are included in every desktop bundle's
`THIRD_PARTY_LICENSES` data. `go.sum`, `go mod verify`, `go mod tidy -diff`, the
race suite, the vulnerability audit, and a linked-binary dependency inventory
are release gates.

## VB-CABLE (Windows audio routing)

WebJam bundles the VB-CABLE installers (see `VB/readme.txt` for VB-Audio
Software's own license terms) under the terms of that EULA, which
explicitly permits redistribution alongside another application's
installer.

## Webex

WebJam opens Cisco Webex externally for native speech/video. Webex itself is
not bundled. WebJam also does not bundle the retired Webex web widget,
Qt WebEngine meeting runtime, or deprecated Guest Issuer token exchange.
