# WebJam v0.24.0 recording-first physical test checklist

> **Current status:** every physical row is **NOT RUN**. Automated tests and
> successful package builds are prerequisites, not evidence that musicians
> heard, recorded, edited, or exported correctly on physical systems.

Use this ledger only with the exact v0.24.0 release assets and checksum
manifest. Windows packages are unsigned; macOS packages are ad-hoc signed and
unnotarized. Never carry a PASS from v0.23.0 or a branch artifact into this
ledger.

## Result rules

- **NOT RUN** — the exact action was not performed against the recorded asset.
- **PASS** — the expected result was directly observed and sanitized evidence
  is linked.
- **FAIL** — the observed result violated the expectation; preserve recoverable
  media and attach a sanitized issue.
- **BLOCKED** — the test was attempted but an external prerequisite was absent.

Do not publish invitations, meeting links, credentials, local paths, device
UIDs, raw exceptions, or private participant names as evidence.

## Exact candidate identity

Complete this table before changing any result:

| Evidence field | Value |
| --- | --- |
| Candidate version | `0.24.0` |
| Annotated tag and commit | **NOT RUN — verify exact `v0.24.0` tag on GitHub** |
| Tag CI and protected promotion run IDs | **NOT RUN — not recorded** |
| GitHub release ID | **NOT RUN — not recorded** |
| Host asset filename and SHA-256 | **NOT RUN — not recorded** |
| Guest asset filename(s) and SHA-256 | **NOT RUN — not recorded** |
| Jamulus client/server identity | **NOT RUN — not recorded** |
| Test machines, OS versions, interfaces, and headphones | **NOT RUN — not recorded** |
| Test date/time zone and musician aliases | **NOT RUN — not recorded** |
| Sanitized evidence location | **NOT RUN — not recorded** |

If any tag, commit, package, lock, component, or checksum changes, start a new
ledger.

## A. Package and clean-start boundary

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| A01 | Download through GitHub **Latest** and verify all selected files against `WebJam-v0.24.0-SHA256SUMS.txt`. | **NOT RUN** |
| A02 | Install/extract on clean Windows, Intel Mac, Apple-silicon Mac, and Ubuntu accounts as available; record SmartScreen, Gatekeeper, and quarantine behavior truthfully. | **NOT RUN** |
| A03 | Launch and confirm About/package metadata says v0.24.0 without claiming signing, notarization, audibility, or physical certification. | **NOT RUN** |
| A04 | Confirm Host/Join and Reference Studio remain understandable at 720×560, 760×600, 1024×768, and 1440×900. | **NOT RUN** |

## B. Multi-machine rehearsal and conversation handoff

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| B01 | Host on a Mac; join from a second physical machine; each musician hears the other through Jamulus with wired headphones. | **NOT RUN** |
| B02 | Exercise known Webex, Zoom, Microsoft Teams, Google Meet, and FaceTime links plus at least two unrelated meeting platforms using hardened public HTTPS DNS-host links. Known services receive friendly labels; generic providers stay neutral. Copy Link returns the normalized link, and Join/Open hands off exactly once without claiming join, mute, or native provider verification. | **NOT RUN** |
| B03 | Refuse HTTP, userinfo, custom-port, local/special-use-host, IP-literal, percent-encoded-host, and known-brand-lookalike cases. On non-Mac systems, FaceTime refuses with honest platform guidance. Native detection/install/mute/bring-forward remains Webex-only. | **NOT RUN** |
| B04 | Inspect logs, mappings, diagnostics, and a Support Bundle: no meeting URL, invitation, credential, device UID, or private path appears; an unknown provider's hostname is also absent, while any known-provider projection is origin-only. | **NOT RUN** |

## C. Shared Track and recording lifecycle

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| C01 | Load/drop a supported Shared Track. Name, duration, progressive waveform, route readiness, count-in, dropout, and cleanup state remain legible; loading never starts playback. | **NOT RUN** |
| C02 | Prove the Mac route, play through the separate `WebJam Track` participant, and verify every musician can mix/mute it independently without a local duplicate. | **NOT RUN** |
| C03 | Start Record Session with and without Shared Track. Observe Preparing → Count-in when applicable → Recording → Stopping → Finalizing → Ready/attention. | **NOT RUN** |
| C04 | During recording, host cards show conservative per-source Armed/Waiting/Recording/Saved/attention truth. Guests receive session-wide state but no invented per-musician proof or recorder authority. | **NOT RUN** |
| C05 | Duplicate Record/Stop, reconnect, End/Leave, and cleanup races create one generation and never claim Ready before recorder and Shared Track cleanup are proved. | **NOT RUN** |

## D. Configurable Local Originals

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| D01 | With Local Originals disabled, no input opens and no local stem appears. | **NOT RUN** |
| D02 | In Recording Setup, add, name, enable/disable, and remove mono/stereo tracks; invalid, duplicate, control-character, or over-limit names fail visibly. | **NOT RUN** |
| D03 | Record 1, 2, and several configured tracks. Enabled Local-Original tracks map sequentially to the intended device channels; stereo entries produce distinct L/R stems. | **NOT RUN** |
| D04 | With an empty configuration, verify the compatible two-input default. With capture disabled, verify no default stems are invented. | **NOT RUN** |
| D05 | Create more demanding mono/stereo configurations up to the 32 enabled-input-channel bound on suitable hardware; the editor refuses channel 33 without truncation, and storage estimates, required-stem count, diagnostics, and manifest agree with actual capture. | **NOT RUN** |
| D06 | Inject input dropout, disk-full, stop, and restart failures. Gaps/recovery remain explicit and no partial take is called complete. | **NOT RUN** |

## E. Take identity and Studio continuation

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| E01 | Final take contains one authoritative stem per proven musician, one distinct Shared Track when used, and only opted-in Local Originals. | **NOT RUN** |
| E02 | A durably finalized take auto-selects and opens in Studio; failure/attention does not falsely auto-open as ready. | **NOT RUN** |
| E03 | Record repeated takes with the same and a changed Shared Track. Same-source lanes match; changed or legacy-unproved sources refuse comping with the honest reason. | **NOT RUN** |
| E04 | Exercise Arrange, take lanes, comping, fades, cycle, markers/sections, mixer controls, autosave/recovery, and undo/redo without changing source media. | **NOT RUN** |
| E05 | Change several faders/mutes/pans, use **Reset Mix**, then Undo. Defaults restore in one action while export inclusion stays unchanged; Undo restores the prior mix. | **NOT RUN** |
| E06 | Cause a brief lane and master overload. Indicators stay latched for the playback epoch and clear only on the documented restart/seek boundary. | **NOT RUN** |

## F. Export, recovery, and endurance

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| F01 | On macOS/Linux export the edited package; verify equal-length edited stems, originals, rough mix, markers, arrangement, provenance, and checksums in an external editor. | **NOT RUN** |
| F02 | On Windows verify the explicitly limited aligned-originals/reference-mix export and its exclusions. | **NOT RUN** |
| F03 | Change a source/manifest/Studio document during export and inject write/fsync/rename failures. No partial folder is reported as success. | **NOT RUN** |
| F04 | Disconnect/reconnect interfaces, restart Jamulus/JACK, sleep/wake, and change networks during separate expendable sessions. Identity and recovery stay conservative. | **NOT RUN** |
| F05 | Complete five-minute and sixty-minute two-musician sessions with Shared Track, multiple takes, configured Local Originals, Studio review, export, and clean teardown. | **NOT RUN** |
| F06 | Use keyboard-only navigation and VoiceOver/NVDA/Orca as applicable across live recording, input-track editor, Shared Track, Studio, and export. | **NOT RUN** |

## Release decision summary

| Gate family | Status | Evidence |
| --- | --- | --- |
| Exact v0.24.0 package identity/checksums | **NOT RUN** | None |
| Clean install and platform trust | **NOT RUN** | None |
| Multi-machine Jamulus and Shared Track audibility | **NOT RUN** | None |
| Provider-neutral conversation handoff | **NOT RUN** | None |
| Recording lifecycle and per-source truth | **NOT RUN** | None |
| Configurable mono/stereo Local Originals | **NOT RUN** | None |
| Authoritative stems and cross-take identity | **NOT RUN** | None |
| Studio auto-open, Reset Mix, and overload latch | **NOT RUN** | None |
| Export and external-editor import | **NOT RUN** | None |
| Recovery, long-session, and accessibility | **NOT RUN** | None |
| Signing, notarization, SmartScreen, and Gatekeeper | **NOT RUN** | None |

Release recommendation: **NOT RUN — this ledger authorizes no physical or
production-trust claim.**
