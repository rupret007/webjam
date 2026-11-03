# Native Jamulus session test path (2026-11-03)

Read-only audit for branch `cursor/hangout-music-20261103` (PR416). **Jamulus remains the live audio path.** WebJam orchestrates launch, invitations, and recovery; it does not replace Jamulus, bypass it, or tune its latency/jitter buffers in code.

This document is a **physical acceptance worksheet** for Hang Out / community music readiness. Automated tests (for example `tests/test_jamulus_native_startup.py`, `DUAL_MUSICIAN_REHEARSAL_LAB.md`) prove control-plane behavior only—they are not substitutes for audibility on real hardware.

## Scope and machines

| Role | Machine | Native Jamulus client | Host owned `JamulusServer` |
| --- | --- | --- | --- |
| Primary host | Apple Silicon Mac Mini (ARM) | Yes (host musician) | **Yes — preferred server choice** |
| Native guest A | Intel MacBook Pro | Yes | No |
| Native guest B | Windows PC (x64) | Yes, when using a **signed** package that supports native remote Music join | No |

**Server choice:** Use WebJam **Host** on the ARM Mini so WebJam starts the reviewed `JamulusServer` on the default UDP port (`22124`, `core/settings.py:227`) with loopback JSON-RPC (`services/bridge_service.py:4183–4201`). Do not substitute a manual public Jamulus server for this gate unless you are running a separate, explicitly scoped test.

**Client build:** Use the organizer’s **qualified exact build** (filename + SHA-256 + About build ID). Follow the invitation’s network scope (`changelog.d/community-music-setup.md`). The checkout README notes that the current **unsigned Windows** candidate cannot join **native remote Music** until the transport has a valid Windows signature (`README.md:18–19`); plan the Windows leg accordingly or record **BLOCKED** with that reason.

**Out of scope for “native-native pass”:** Treat **browser Web Join** as a secondary path (section below). A Web Join guest is not a substitute for the two native desktop guests hearing each other through Jamulus.

## Headphones, consent, and feedback safety

Before Host/Join on each native machine:

1. Connect the intended audio interface and **wired headphones** (demo worksheet: `WEBJAM_V0225_DEMO_READINESS.md`).
2. Complete Jamulus’s one-time **native sound setup** in the dedicated profile (`--inifile WebJam-native-v0.16.ini`, `README.md:807–814`). Choose interface, input channels, headphones, and buffer in **Jamulus → Settings → Audio/Network Settings** (`README.md:314`). WebJam does not open the musician’s normal `Jamulus.ini` or write buffer/jitter values (`README.md:828–829`).
3. Each musician plays a short note and **verbally confirms** clean two-way hearing before recording evidence (`WEBJAM_V0225_DEMO_READINESS.md` steps 3–4).
4. If macOS built-in mic/speakers would cause feedback, verify the safe **Go Back** path stays default-safe (`WEBJAM_V0225_DEMO_READINESS.md` step 2).

For **Web Join** (secondary): the guest must check **Wear headphones** (`web/join/index.html:24`), accept the browser microphone prompt after **Join** (`web/join/app.js:30–31`), and understand echo cancellation is intentionally off for music (`web/join/index.html:24`, `web/join/audio.js:1–7`).

## Native-to-native test procedure

Run on a single continuous session unless a step requires a deliberate stop. Record timestamps, build IDs, and observations in a private worksheet—omit secrets, invite fragments, RPC secrets, and filesystem paths from shared evidence.

### 1. Identity and install

On all three machines:

1. Verify download SHA-256 and install without side-loading adjacent source builds.
2. Open **About**; record version/build ID per machine.
3. Note OS, architecture, interface names, and headphone confirmation.

### 2. Host on ARM Mini

1. Launch WebJam → **Music** → **Host**.
2. Confirm WebJam starts owned `JamulusServer` (HUD/server truth) and opens the visible Jamulus client for native sound setup (`services/bridge_service.py:2976–2991`).
3. Finish Jamulus sound setup; wait until WebJam confirms the authenticated music connection (native startup journey; `NATIVE_SOUND_SETUP_GRACE_SECONDS` = 600 s max wait, `services/bridge_service.py:432`).
4. **Copy invite**; keep the invitation scope (LAN vs cross-home) as issued.

### 3. Join from Intel MacBook Pro (native guest A)

1. **Join** with the full invitation (single field + **Join**, `README.md:307–311`).
2. Complete native Jamulus sound setup if prompted; confirm two-way audio with the host.
3. In Jamulus, record the **ping, jitter, and buffer** values shown in Jamulus’s own UI (WebJam does not author these numbers).

### 4. Join from Windows PC (native guest B)

1. Repeat step 3 on Windows when the package supports native remote Music join.
2. If blocked by unsigned transport policy, record **NOT RUN / BLOCKED** and continue the native-native gate with two Macs only; do not bypass Jamulus with Web Join for this primary gate.

### 5. Ensemble exercise (minimum)

1. All native participants play together **≥ 5 minutes** (`WEBJAM_V0225_DEMO_READINESS.md` step 6).
2. Capture each musician’s short judgment: timing feel, perceived latency, dropouts, drift—not “latency eliminated” claims.
3. Optional: host enables **Record Session** only after readiness sheet acceptance (`USER_GUIDE.md` recording section)—recording is evidence of session truth, not a substitute for audibility.

### 6. Stop, leave, and recovery

| Action | Host (ARM Mini) | Native guest |
| --- | --- | --- |
| Deliberate exit | **End Session** / **End Room** (`USER_GUIDE.md:61–62`) | **Leave Jam** / **Leave Room** |
| Connection trouble | Use on-screen **Try again** / invitation recovery (`USER_GUIDE.md:406`, `925–929`) | **Paste new invite** when rejected |
| Interface unplug | Follow documented recovery; Jamulus/WebJam should remain truthful | Same |
| Quit WebJam | Wait for “stopping” music engine to finish (`USER_GUIDE.md:485–486`) | Same |

After **End/Leave**, confirm WebJam stops **owned** processes only and that a relaunch can Host/Join again without manual `Jamulus.ini` repair (`TEST_PROCEDURE.md:469–500`).

**Native sound setup timeout:** If Jamulus sound setup exceeds **10 minutes** without verified music connection, WebJam fails closed with setup timeout guidance (`webjam_qt/controllers/application_controller.py:296`, `services/bridge_service.py:432`).

## Secondary path: browser Web Join (Opus custom bridge)

Use only after the native-native path is recorded, or as an explicitly labeled secondary row.

1. On the **native host**, enable **Web Join → Allow a web guest**, then **Copy web invite** (`docs/web-join.md:4–14`).
2. Guest opens the link in a supported browser; completes headphones + **Join with microphone** (`web/join/index.html`, `web/join/app.js`).
3. Expect UI copy **Web Join, higher delay (about 60–120 ms)** (`web/join/session.js:89`)—an expectation, not a WAN guarantee.
4. **Fails closed:** missing custom Opus / bridge codec → no usable invite (`docs/web-join.md:21–22`, `services/web_join_bridge.py:8–10`). Invalid TURN/mailbox/credential shape → relay unavailable (`web/join/policy.js:35–51`). Bridge listens on loopback only (`docs/web-join.md:111–112`).
5. Host **Kick web guest**, clearing permission, stopping the session, or quitting tears down the sidecar/bridge (`docs/web-join.md:11–14`).
6. Record: browser, selected ICE path (if visible), constraint warnings (`web/join/audio.js:9–13`), estimated listening delay line (`web/join/app.js:37`), and audible comparison vs native Jamulus on the same network.

Web Join converts **standard WebRTC Opus** to **Jamulus Opus custom 128-sample** frames at the host bridge (`services/web_join_bridge.py:8–9`, `33–34`)—it does not change Jamulus client/server jitter tuning.

## What to record (evidence checklist)

Per machine: date/time, tester, asset filename, SHA-256, About build ID, OS/arch/model, interface in/out, wired headphones (Y/N), **Jamulus ping/jitter/buffer as shown in Jamulus**, two-way hearing confirmation (Y/N), dropout notes, End/Leave clean teardown (Y/N).

Optional secondary row: browser name/version, Web Join latency estimate text, pass/fail vs native path on same host.

Do **not** commit invite URLs, TURN credentials, mailbox payloads, or RPC secrets.

## Latency / jitter / buffer settings (audit table)

“Configured” means a constant, default, validation bound, or enforced policy in this repository. **Jamulus live buffer/jitter/auto-jitter/frame-size/audio-quality knobs are musician-owned in Jamulus UI; WebJam does not write them** (`core/jamulus_profile.py:4–14`, `README.md:828–829`).

| Setting | Default / bound | Source (file:line) | Exposed to players in UI? |
| --- | --- | --- | --- |
| **Jamulus auto jitter buffer** | Not set by WebJam; musician value in dedicated `WebJam-native-v0.16.ini` after setup | `core/jamulus_profile.py:4–14`; launch without buffer flags `services/bridge_service.py:2976–2991` | **Jamulus only** — Settings → Audio/Network (`README.md:314`) |
| **Jamulus local/client buffer size (64 / 128 / 256 frames)** | Not set by WebJam | Same as above | **Jamulus only** (same) |
| **Jamulus server buffer / frame size** | Not set by WebJam; server CLI has no frame/buffer overrides | `services/bridge_service.py:4183–4201` (`--port`, recording, JSON-RPC only) | **Jamulus server defaults**; not surfaced in WebJam HUD as a tuning control |
| **Jamulus audio quality / Opus quality preset** | Not set by WebJam | `README.md:828–829`; `webjam_qt/windows/simple_settings.py:4` (dialog excludes jitter/buffer) | **Jamulus only** |
| **CoreAudio/ASIO route `requested_buffer_frames` (64 / 128 / 256)** | Default **128** in route profile schema; validated for non-JACK platforms | `core/audio_route_profile.py:75–76`, `154–161`, `344` | **WebJam Recording Setup / route repair** — not Jamulus network buffer; `USER_GUIDE.md:1404–1408` (**48 kHz and automatic buffer** is **local recording format**, not Jamulus live audio) |
| **Web Join browser `jitterBufferTarget`** | **20** (seconds → treated as ms target by browser) | `web/join/audio.js:78–79` | Browser guest: internal; optional stats via estimated listening delay (`web/join/app.js:37`, `web/join/audio.js:83–91`) |
| **Web Join WebRTC Opus SDP (`ptime`, fmtp)** | `ptime` **20** ms; `stereo=1`, `maxaveragebitrate=128000`, `usedtx=0`, `useinbandfec=1` | `web/join/audio.js:39–60`, `56` | Not separately editable; warnings if constraints drift (`web/join/audio.js:9–13`) |
| **Web Join Pion Opus codec registration** | Same fmtp as browser path | `transport/internal/webjoin/endpoint.go:167` | Host-side; no musician control |
| **Web Join → Jamulus custom Opus frame size** | **128** samples @ 48 kHz (`JAMULUS_FRAMES`); **106** byte payload | `services/web_join_bridge.py:31–34`, `155–161` | Not exposed; bridge fails closed if custom Opus missing (`services/web_join_bridge.py:8–10`) |
| **Web Join RTP decode frame (`WEB_FRAMES`)** | **960** samples | `services/web_join_bridge.py:35` | Internal bridge timing |
| **Web Join ICE gather / connect / enrollment timeouts** | **20 s** gather, **25 s** connect, **10 s** enrollment, **30 s** idle media | `transport/internal/webjoin/endpoint.go:27–30`, `502–507` | Guest sees status/errors; host sidecar closes on timeout |
| **Web Join credential HTTP deadline** | **12 s** | `transport/internal/webjoin/credentials.go:104`, `109` | Failure → “Web Join unavailable” (`web/join/policy.js:5`) |
| **Browser ICE gather wait** | **15 s** | `web/join/session.js:76` | Guest status: “Network discovery timed out…” |
| **Browser enrollment wait** | **30 s** | `web/join/session.js:83` | Guest status: “Host authentication timed out…” |
| **Browser disconnect guard** | **10 s** after `disconnected` | `web/join/session.js:44` | Guest error path |
| **TURN credential lifetime (`ttl`)** | **600 s** default minted TTL | `deploy/relay/cloudflare-worker/src/policy.js:24`; `deploy/relay/turn_credentials.py:49`; `deploy/relay/cloudflare-worker/wrangler.toml:11`; validated **1–3600** in clients `web/join/policy.js:36`, `transport/internal/webjoin/credentials.go:143` | Browser: session ends at `expiresAt` (`web/join/session.js:91–92`); desktop native transport uses same Worker policy (`transport/internal/directstun/relay.go:33` `credentialTTL = 10 * time.Minute`) |
| **Signaling mailbox TTL** | **600 s** | `core/signaling_mailbox.py:28`; `wrangler.toml:19` | Not shown as a countdown; paste fallback when expired |
| **Web Join sidecar offer horizon** | **600 s** (`expires_at_unix`) | `services/web_join_host.py:278–289` | Invite `#e=` fragment expiry; bounded by TURN TTL (`docs/web-join.md:37–40`) |
| **Native Jamulus sound-setup grace** | **600 s** (10 min) | `services/bridge_service.py:432`, `2063–2065` | Startup guidance / timeout message (`webjam_qt/controllers/application_controller.py:296`) |
| **Default Jamulus UDP port** | **22124** | `core/settings.py:227`; `core/jamulus_endpoint.py:9` | Shown indirectly via connection/server truth, not as a latency knob |
| **Hosted practice server port (dev/practice only)** | **22135** | `services/bridge_service.py:428` | Practice path only—not the primary Hang Out host gate |

### Jamulus protocol constants (not WebJam tuning)

Legacy adapter documents message type IDs including `JITT_BUF_SIZE = 1` in `jamulus_controller.py:1133` and `JITT_BUF_SIZE = 10` / `REQ_JITT_BUF_SIZE = 11` in `core/jamulus_protocol.py:59–60`. These name on-wire message types; **WebJam does not use them to push jitter buffer sizes to musicians.**

## Related documents

- `WEBJAM_V0225_DEMO_READINESS.md` — two-musician demo worksheet
- `TEST_PROCEDURE.md` — macOS Jamulus profile / privacy gate
- `docs/web-join.md` — Web Join spec and validation commands
- `docs/web-join-bridge.md` — Opus custom bridge setup
- `DUAL_MUSICIAN_REHEARSAL_LAB.md` — source-level rehearsal (not physical audio)

## Audit notes (2026-11-03)

- No Jamulus latency/jitter/buffer/quality values are written from WebJam application code; native launch supplies `--inifile` and connection args only (`services/bridge_service.py:2976–2991`, `4183–4201`).
- Web Join adds a **second** Opus path (standard → custom 128) and browser jitter target; it must remain secondary to native Jamulus for Hang Out music readiness.
- TURN/mailbox/**600 s** lifetime is consistent across Worker policy, Python coturn helper, Go validators, and Web Join host expiry.
