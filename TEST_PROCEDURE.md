# WebJam standard test procedure

**Last updated:** 2026-07-13
**Current target:** private Apple Silicon two-Mac v0.9.0 test

The release path is tested as one product flow:

> Host → Copy Invite → open or paste → Play → Studio → End Session

The old setup wizard, Ready Check, raw server endpoint, visible music-client
window, and Stop-Audio-with-server-left-running workflow are not acceptance
paths for this build.

## 1. Source gate

Run Qt workflows serially; concurrent offscreen test processes can share Qt
state and create false failures.

```bash
.venv/bin/python -m ruff check --no-cache \
  webjam_qt/ core/ ui/ services/ api/ \
  jamulus_controller.py jamulus_state_manager.py
.venv/bin/python -m compileall -q \
  webjam_qt core ui services api \
  jamulus_controller.py jamulus_state_manager.py
.venv/bin/python -m pip check
git diff --check
QT_QPA_PLATFORM=offscreen .venv/bin/python ux_smoke_test.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
```

The full suite must include coverage for strict invitation parsing, one-click
host defaults, duplicate-submit prevention, cold- and warm-start links,
microphone permission states, connection timeout/retry, reconnect proof,
headless client launch, truthful per-participant meters, role-aware End/Leave,
Studio recording, owned-server shutdown, fatal-error handling, and the
two-choice returning-user launch.

When a compatible official binary is available, also run the real-service and
ownership regressions against it:

```bash
WEBJAM_JAMULUS_BINARY=/path/to/Jamulus \
  .venv/bin/python -m pytest tests/test_real_jamulus_integration.py -v
QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m pytest tests/test_hosted_server.py -v
```

## 2. Exact macOS bundle gate

Build from a clean PyInstaller work directory, add the pinned official client
and dedicated-server bundles, then sign the completed private artifact using
the documented ad-hoc test-build process.

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm webjam.spec
```

Inspect the exact `dist/WebJam.app` that will be zipped:

- `Info.plist` has the expected version and registers the `webjam` URL scheme.
- The archive contains `network_invite`, `launch_dialog`, `session_hud`,
  `recording_studio`, `take_export`, and `recording_setup`, plus the bundled
  Inter license and resources.
- Both pinned official music bundles are present with the expected
  architecture/version.
- `codesign --verify --strict` succeeds for the outer and nested bundles.

For an isolated frozen Host lifecycle smoke, point `HOME` at an empty temporary
directory and set `WEBJAM_SMOKE_AUTOSTART_AUDIO=1` plus
`WEBJAM_SMOKE_EXIT_MS=15000`. The latter is accepted only with the smoke hook
and only from 1–60 seconds. It represents an affirmative response to the live
Host close confirmation, then exits through Qt's normal close and
`aboutToQuit` paths so the test can require released ports and no
WebJam/Jamulus processes.
- The nested test-build signatures do not enable App Sandbox.
- Launching the frozen executable starts the headless client—no second GUI—and
  its owned client/server processes stop cleanly.
- The produced ZIP has a recorded SHA-256. Test a fresh extraction of that ZIP,
  not an earlier app left in Applications.

This artifact is ad-hoc signed and intentionally not notarized. Control-click
→ Open, or Privacy & Security → Open Anyway, is an acceptable first-launch
step. A missing/invalid sealed resource or “damaged app” result fails the
bundle gate.

## 3. Frozen-flow smoke

With a clean preferences profile, verify the exact packaged app:

1. Every launch begins with only **Host a Jam** and **Join a Jam**. Active UI
   uses near-black, white/neutral, and burnt orange (`#BF5700`) without purple,
   teal, red danger styling, neon glow, or busy gradients.
2. **Host a Jam** is one click and reaches **Ready to share** without a wizard.
   The single host tile is labeled **You**, not “Musician” or “Bandmate,” and
   remains connected beyond the 30-second join timeout.
3. **Copy Invite** is unavailable until the hosted service is alive and then
   copies one strict `webjam://join?...` link without secrets or paths.
4. A valid link works on cold start and while WebJam is already running. A
   malformed/ambiguous link fails safely.
5. A join that cannot connect stops after about 30 seconds and presents one
   **Try Again** action. An unavailable jam and an offline/same-Wi-Fi problem
   use distinct plain-language guidance.
6. Local signal can affect only the local card unless an exact remote channel
   level exists. No synthetic activity is shown when metering is unavailable.
7. The host can use **Record** in the bottom control bar; **More → Multitrack
   Studio** shows live lanes and completed takes. Recording Setup preserves a
   selected stereo output and an explicitly enabled two-input host capture.
   A completed take can export numbered, equal-length 24-bit stems plus a
   stereo rough mix without modifying its source WAVs.
8. A host sees **End Session**; a guest sees **Leave Jam**. Their confirmations
   and cleanup scope are distinct, and **Ending…** / **Leaving…** remains until
   the work actually completes.
9. **End Session** stops/saves recording first, then the client and owned
   server. Relaunching can host again without a port conflict.
10. At 760×600 the participant grid reflows and Copy Invite / Record / More /
    End-or-Leave remain visible, usable, and reachable in sensible tab order.
11. Permission required/denied, interrupted, unavailable, recoverable failure,
    and fatal startup states show one human-readable next step and no raw
    exception or implementation detail.

Automated runtime evidence proves service startup, authenticated control,
roster truth, and cleanup. It does not prove acoustic audibility between two
interfaces; that remains a physical gate.

## 4. Physical two-Mac gate

Run [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) with the exact ZIP and
record evidence for:

- same-network Host → Copy Invite → open/paste → Play;
- real two-way audibility and honest local/remote meters;
- one server WAV per musician, any enabled host input stems, Studio stereo
  playback/pan/mute/solo, an atomic Logic package, and aligned Logic import;
- reconnect without stale readiness, including the one-retry timeout path;
- End Session and quit with no owned music-client, server, or `caffeinate`
  process left behind.

For this private pilot, both Macs must be on the same local network (preferably
the same Wi-Fi for the first run). Internet, VPN, NAT traversal, Windows, and
Intel macOS are outside tonight's pass claim.

## 5. Pass rule

The build is ready for the private test only when the source gate, exact-bundle
gate, and frozen-flow smoke pass. It is ready to advance beyond the test only
when both musicians pass every physical two-Mac item and the saved take imports
correctly into Logic. Preserve logs and take folders for any failure before
changing configuration or rebuilding.

The retired 2024 harness remains in
[`legacy/TEST_PROCEDURE_2024.md`](legacy/TEST_PROCEDURE_2024.md) for history; it
is not a current release gate.
