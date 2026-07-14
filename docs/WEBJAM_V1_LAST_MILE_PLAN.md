# WebJam v1 last-mile readiness record

**Working branch:** `codex/vnext-last-mile-readiness`
**Version:** `0.11.0` source candidate; no new package has been made from this branch
**Last verified baseline:** `master` at `a3ba8498ed87529589cd4738903695d7cba18219`
**Baseline CI:** GitHub Actions run `29296365785` passed on 2026-07-14.

This is an implementation record, not a release claim. It separates source
behavior, package evidence, and physical musician evidence so a green unit
test can never be confused with a successful rehearsal.

## Product path

The ordinary supported path stays intentionally small:

1. Choose **Host a Jam** or **Join a Jam**.
2. Complete Band Check only when the saved setup is missing or changed.
3. Host: WebJam starts and supervises the local Jamulus server and client,
   checks the private-LAN pre-share facts, then enables **Copy Invite**.
4. Join: paste/open the complete invitation, pass Band Check, and WebJam starts
   the client.
5. Play. Recording, Studio, conversation, settings, and diagnostics stay under
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
  musicians play. A WebJam PortAudio meter or scratch recording does not prove
  Jamulus selected the same device; real two-way audibility remains a human
  gate.

## Current implementation milestones

| Milestone | Source status | Evidence / boundary |
| --- | --- | --- |
| Minimal Host/Join and progressive Band Check | Implemented | Qt coverage exercises the first screen, invitation ingress, and preflight gate. |
| Owned Jamulus host/client lifecycle | Implemented | Host server authentication, private secret permissions, duplicate prevention, idempotent stop, and reconnect coverage exist. |
| Authoritative session lifecycle | Implemented on this branch | `core/session_lifecycle.py` records preflight, host/join, private-LAN share readiness, roster-confirmed connection, degraded/reconnect, recording finalization, cleanup, and recoverable failure. It supplies the diagnostics timeline. |
| Honest pre-share readiness | Implemented on this branch | `core/host_share_readiness.py` fails closed when the server, UDP listener, or private Wi-Fi address is missing. It does not call that an Internet reachability test. |
| Band Check | Implemented | Ready / Warning / Action Needed results retain independent local, production, and musician-confirmed evidence. |
| Recording and Logic handoff | Implemented in source | Schema-v2 manifests, atomic output/recovery, alignment evidence, common-origin PCM24 stems, checksums, and import instructions exist. Physical Logic import is still NOT RUN. |
| Privacy-safe diagnostics | Implemented | Preview, clipboard, JSON, and ZIP derive from one allowlisted/redacted snapshot. The lifecycle timeline contains no invitation, address, device, or path data. |
| v0.11 macOS arm64 package | Existing private artifact | Exact artifact/build evidence is in `README.md`, `TEST_PROCEDURE.md`, and the test-night handoff. No new artifact is implied by this branch. |

## Highest-risk failure modes and response

| Condition | Product response | Evidence level |
| --- | --- | --- |
| Duplicate Host/Join click | One owned launch/reconnect path; repeated starts are ignored while a gate or process is active. | Automated |
| Host server cannot start or UDP listener is absent | Invite stays unavailable; the HUD provides one next action. | Automated local state |
| No private Wi-Fi address | Invite stays unavailable and asks the host to connect to the band Wi-Fi. | Automated local state |
| Process exit or silent RPC | Lifecycle becomes degraded/reconnecting; bounded retry keeps mix state and ends in one retry action if exhausted. | Automated |
| Stale terminal callback | Lifecycle rejects a transition that would resurrect a completed/failed-final session. | Automated |
| End Session during an active host take | Recording finalizes before owned processes are released; a failed finalization leaves the session protected. | Automated; physical media review pending |
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

The focused source checks added for this milestone cover lifecycle transitions,
support-bundle redaction, and pre-share refusal when a local fact is missing.
On this branch, the local Python suite completed with **1,624 passed, 18
skipped, 1 existing Starlette/httpx deprecation warning, and 6 subtests** in
54.86 seconds. The full CI matrix remains the authoritative cross-platform
build evidence.

## Manual certification gates — never inferred from source tests

- Two independent Apple-Silicon Macs, exact v0.11.0 artifact, same private LAN
- Both musicians hear each other through the actual Jamulus route
- Host/link/join and reconnect after outage/interface changes
- Host server take, guest original delivery, Studio playback, alignment/drift
- Logic Pro import/playback of the exported package
- Packaged VoiceOver/NVDA review
- Developer ID signing and notarization before broad distribution

Use [SUNDAY_TWO_MAC_PILOT.md](../SUNDAY_TWO_MAC_PILOT.md) for the operator
worksheet. Mark every unobserved physical result **NOT RUN**.

## Rollback

The v0.11.0 private artifact is ad-hoc signed and has no public release tag.
For a test-night rollback, quit WebJam, preserve diagnostics/takes, remove the
candidate app, and restore the preserved v0.10.0 artifact described in
`TEST_PROCEDURE.md`. Do not overwrite an installed app in place.
