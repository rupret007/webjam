# WebJam v1 last-mile readiness record

**Working branch:** `master`
**Version:** `0.12.0` private Apple-Silicon test-night candidate, built from
`796e9a4ddebe79f430b0ded8cf8034bc27836dd0`; package verification is complete
and physical musician certification remains **NOT RUN**
**Exact artifact:** `WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
`01427316820b884d61546d40a9327a49cedf43d6a60a4d88b5b29ab4c693a24c`
**Baseline CI:** GitHub Actions run `29296365785` passed on 2026-07-14.

This is an implementation record, not a release claim. It separates source
behavior, package evidence, and physical musician evidence so a green unit
test can never be confused with a successful rehearsal.

## Product path

The ordinary supported path stays intentionally small:

1. Choose **Host a Jam** or **Join a Jam**.
2. Confirm the musician name and band sound in one concise screen.
3. Complete Band Check when the saved setup is missing or changed.
4. Host: WebJam starts and supervises the local Jamulus server and client,
   checks the private-LAN pre-share facts, then enables **Copy Invite**.
   If Wi-Fi, sleep/wake, or an interface change gives the host a new private
   address after a link was copied, WebJam stops calling the session simply
   ready and asks for one **Copy New Invite** action.
5. Join: paste/open the complete invitation, pass Band Check, and WebJam starts
   the client.
6. Play. Recording, Studio, conversation, settings, and diagnostics stay under
   **More**.

No ordinary musician is asked for an executable path, server address, UDP
port, router change, or Webex credential.

## Supported network and audio truth

- **Supported ordinary topology:** v1/v2 private RFC1918 IPv4 LAN. A host
  advertises only after the authenticated hosted server is alive, UDP 22124 is
  locally bound, and WebJam has a private LAN address. This is a pre-share
  local readiness check, not proof that an external home or NAT is reachable.
- **Not supported or claimed:** public Internet hosting, VPNs, IPv6,
  NAT traversal, router automation, public rendezvous, and a deployed relay.
  The v3 `reference-local` profile remains a loopback/CI lab boundary.
- **Live audio truth:** Jamulus is the performance-audio path. Webex is
  optional conversation/video and should keep its microphone muted while
  musicians play. The v0.12.0 candidate resolves a stable CoreAudio pair,
  rejects missing/ambiguous/non-48-kHz hardware, and stages a WebJam-owned
  filename-only Jamulus config before launch. A WebJam PortAudio meter or
  scratch recording still does not prove a bandmate can hear the route; real
  two-way audibility remains a human gate.

## Current implementation milestones

| Milestone | Source status | Evidence / boundary |
| --- | --- | --- |
| Minimal Host/Join, sound confirmation, and progressive Band Check | Included in v0.12.0 | Qt coverage exercises the first screen, confirmation, invitation ingress, and preflight gate; the exact package contains this flow. Physical two-Mac confirmation remains NOT RUN. |
| Owned Jamulus host/client lifecycle | Implemented | Host server authentication, private secret permissions, duplicate prevention, idempotent stop, and reconnect coverage exist. A long event-loop pause (including likely sleep/wake) clears the connected/roster claim until fresh live evidence arrives. |
| Authoritative session lifecycle | Implemented on this branch | `core/session_lifecycle.py` records preflight, host/join, private-LAN share readiness, roster-confirmed connection, degraded/reconnect, recording finalization, cleanup, and recoverable failure. An active cancellation closes through ending/completed/idle rather than leaving stale state. It supplies the diagnostics timeline. |
| Honest pre-share readiness | Implemented on this branch | `core/host_share_readiness.py` fails closed when the server, UDP listener, or private Wi-Fi address is missing. It does not call that an Internet reachability test. |
| LAN address-change truth | Implemented on this branch | A copied v1/v2 LAN invite is process-locally tied to its advertised address. A changed private address produces a plain **Copy New Invite** recovery state; no address is persisted or added to diagnostics. |
| Band Check | Implemented | Ready / Warning / Action Needed results retain independent local, production, and musician-confirmed evidence. |
| macOS Jamulus device route | Included in v0.12.0 | Settings persists CoreAudio UIDs; launch uses a protected owned config and filename-only `--inifile`, freezes that plan through reconnect, and fails closed if hardware changes. It is OS preflight, not graph/hearing proof. |
| Recording and Logic handoff | Included in v0.12.0 | The candidate checks a writable recording folder and a conservative PCM24 reserve before a take; Record recalculates for the actual roster and starts nothing if storage is unsafe. Its schema-v2 session-evidence portion writes only recorder-server-confirmed start/end timestamps, host/protocol, and a bounded redacted lifecycle/recovery timeline—never an invite, address, credential, or raw device identifier. A private crash-safe checkpoint below Takes retains that evidence across an in-progress interruption and is removed only after final manifest publication; Logic export carries nonempty evidence. Atomic output/recovery, alignment evidence, common-origin PCM24 stems, checksums, and import instructions exist. Physical recording recovery and Logic import are still NOT RUN. |
| Privacy-safe diagnostics | Implemented | Preview, clipboard, JSON, and ZIP derive from one allowlisted/redacted snapshot. The lifecycle timeline contains no invitation, address, device, or path data. |
| v0.12 macOS arm64 package | Exact private test-night artifact | Fresh extraction passes strict/deep signature, nested-app inspection, exact sidecar build/hash/IPC validation, and two isolated six-second offscreen launch/TERM cycles. The package is arm64, bundles Jamulus/JamulusServer 3.12.2, and remains ad-hoc signed. Physical musician results are NOT RUN. |

## Highest-risk failure modes and response

| Condition | Product response | Evidence level |
| --- | --- | --- |
| Duplicate Host/Join click | One owned launch/reconnect path; repeated starts are ignored while a gate or process is active. | Automated |
| Host server cannot start or UDP listener is absent | Invite stays unavailable; the HUD provides one next action. | Automated local state |
| No private Wi-Fi address | Invite stays unavailable and asks the host to connect to the band Wi-Fi. | Automated local state |
| Wi-Fi/interface change after sharing | HUD calls out the old LAN invite and requires one new copy action before calling the host ready again. | Automated local state |
| Sleep/wake or long app pause | Live roster/connected evidence is cleared and WebJam rechecks the music connection before returning to a connected state. | Automated source state; physical sleep/wake remains NOT RUN |
| Process exit or silent RPC | Lifecycle becomes degraded/reconnecting; bounded retry keeps mix state and ends in one retry action if exhausted. | Automated |
| Stale terminal callback | Lifecycle rejects a transition that would resurrect a completed/failed-final session. | Automated |
| End Session during an active host take | Recording finalizes before owned processes are released; a failed finalization leaves the session protected. | Automated; physical media review pending |
| Recording folder unavailable or storage dangerously low | v0.12.0 Band Check reports one corrective action; Record rechecks before any local capture or server recorder starts, then starts nothing if unsafe. Low storage warns to make room before a long rehearsal. | Source and package inspection pass; physical drive-full recovery remains NOT RUN |
| App interruption during an in-progress take | v0.12.0 atomically retains only bounded/redacted session evidence in a private checkpoint below Takes. A malformed or unfinished checkpoint is recovery-needed truth, not a completed take; final publication removes it. | Source and package inspection pass; physical interruption recovery remains NOT RUN |
| Webex duplicate music path | Webex stays optional and its role is explained as conversation/video, not performance audio. | UI/source evidence |
| Remote home/NAT reachability | Not claimed. The user gets no public-share promise from the private-LAN flow. | Physical/deployment gate NOT RUN |

## Acceptance evidence

Run from the repository root with the project virtual environment:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
git ls-files '*.py' -z | xargs -0 ./.venv/bin/python -m py_compile
make -C transport check
(cd transport && go test -race -count=1 ./... && go mod verify && go mod tidy -diff)
QT_QPA_PLATFORM=offscreen ./.venv/bin/python ux_smoke_test.py
```

The historical baseline source checks covered lifecycle transitions,
support-bundle redaction, pre-share refusal when a local fact is missing, and a
copied LAN invite after a private Wi-Fi address changes. At the earlier baseline
they completed with **1,628 passed, 18 skipped, 1 existing Starlette/httpx
deprecation warning, and 6 subtests** in 49.92 seconds. That result predates
the current storage hardening and is not presented as its evidence. The focused
storage group now passes focused source coverage. The current full source
gate completed with **1,687 passed, 18 skipped, one existing Starlette/httpx
deprecation warning, and 6 subtests**, with no failures or errors. The exact
v0.12.0 package was then built from that code commit and passed its documented
fresh-extraction/signature/nested-app/sidecar/launch gates; that remains
distinct from physical musician certification.
GitHub Actions run
`29311760834` passed its then-current reference service, Python/UX, transport,
real-Jamulus integration, and macOS arm64/x64 plus Windows x64 packaging jobs.
The downloaded CI artifacts identify build `e4172a84cdbddbfe34e9e9d89ba61c245d00551c`;
they are candidate evidence, not test-night certification. The full CI matrix
remains the authoritative cross-platform build evidence.

## Manual certification gates — never inferred from source tests

- Two independent Apple-Silicon Macs, exact v0.12.0 artifact, same private LAN
- Both musicians hear each other through the actual Jamulus route
- Host/link/join and reconnect after outage/interface changes
- Host server take, guest original delivery, Studio playback, alignment/drift
- Logic Pro import/playback of the exported package
- Packaged VoiceOver/NVDA review
- Developer ID signing and notarization before broad distribution

Use [SUNDAY_TWO_MAC_PILOT.md](../SUNDAY_TWO_MAC_PILOT.md) for the operator
worksheet. Mark every unobserved physical result **NOT RUN**.

## Rollback

The v0.12.0 private artifact is ad-hoc signed and has no public release tag.
For a test-night rollback, quit WebJam, preserve diagnostics/takes, remove the
candidate app, and restore the preserved v0.11.0 artifact described in
`TEST_PROCEDURE.md`. Do not overwrite an installed app in place.
