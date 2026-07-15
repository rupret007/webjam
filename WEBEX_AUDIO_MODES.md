# Webex audio modes

WebJam deliberately keeps rehearsal music and conversation on separate audio
lanes. **Jamulus is always the music path.** Native Webex is either a speech
conversation path, video only, or an advanced one-way feed for an audience. WebJam
opens the meeting but cannot inspect or change Webex's microphone, speaker,
mute state, Mic Mode, or Smart Audio setting.

This guidance applies to the v0.15.0 private candidate. Record its exact ZIP,
SHA-256, and source/build commit in
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) before a physical run.
Physical CoreAudio, two-Mac, recording/recovery, and optional external-editor
observations remain **NOT RUN** until musicians record them. Earlier ZIPs are
rollback artifacts only.

## Musician conversation — recommended

Use this on both musician Macs for the two-person pilot.

```text
instrument / vocal ──> audio interface ──> Jamulus ──> wired headphones
conversation microphone ─────────────────> Webex ─────> same headphones
```

1. In the v0.15.0 candidate, choose the musician's audio interface in
   **Settings → Band input** and **Band output & review** before starting the
   jam. For an earlier rollback ZIP or a manual Jamulus fallback, select that
   interface in Jamulus instead.
2. In Webex, select a dedicated webcam, headset, or USB microphone for speech
   when possible. The music-interface input is an acceptable fallback, but do
   not play while that Webex microphone is open.
3. Set the Webex speaker to the wired audio interface. Never use speakers in
   the room.
4. Join Webex muted. Use **Standard** macOS Mic Mode and **Optimize for My
   Voice** in Webex.
5. Keep Webex muted while playing.
6. Before speaking in Webex, mute the audio interface and confirm the Jamulus
   input is silent. If the interface has no safe mute, end the WebJam session
   first. WebJam cannot live-mute a Jamulus 3.12.2 network send.

Jamulus returns the local musician as part of that musician's personal server
mix. Hearing that return is intentional and is how the musician evaluates the
same network path as the rest of the band. Avoid adding direct software
monitoring on top of it; an interface's zero-latency hardware monitor may be
used sparingly when needed.

## Video only

Choose this when Webex is needed only for faces or screen sharing.

1. Join Webex with **Don't connect to audio**.
2. Use Jamulus and wired interface headphones for every sound.
3. No BlackHole, VB-CABLE, aggregate device, or Multi-Output Device is needed.

## Audience broadcast bridge — advanced

This mode sends the complete Jamulus program mix to observers in Webex. It is
not musician conversation. Musicians in the same Webex meeting must disconnect
Webex audio, otherwise they will hear a delayed duplicate of the music.

On macOS, create a Multi-Output Device containing the physical interface and
BlackHole. Use the interface as clock source and drift correction only on
BlackHole. In the v0.15.0 candidate, choose that Multi-Output Device in
**Settings → Band output & review** before starting the jam. For an earlier
rollback ZIP or a manual Jamulus fallback, set Jamulus output to the
Multi-Output Device instead. Set the Webex microphone to BlackHole and its
speaker to the physical interface. Exclude every real microphone from Webex,
enable **Music Mode**, and prove the feed from a second device using headphones
before a session. Windows uses the equivalent VB-CABLE routing.

An audience bridge cannot also provide normal musician conversation in the same
Webex client: one Webex microphone cannot safely be both the speech mic and the
Jamulus program feed. Use a dedicated third client or a separately engineered
mix-minus system if both are required in a future production.

## Local recording is separate

**Keep local interface originals on this Mac** is independent of all three
Webex modes. When enabled, **Meter and local recording input** chooses the
device WebJam opens for local meters and isolated local capture. It does not
configure Jamulus or Webex. Run **Band Check** after choosing Host/Join on a new
or changed setup, with `F2`, from Settings, or from the live **More** menu to
confirm 48-kHz support and recording-path access before relying on it. The host,
or a guest connected through an active v2 private invite, may opt in; the host
still controls the shared take. A v1 guest has no WebJam-orchestrated local
capture or delivery.

While capture is active, WebJam records periodic durable checkpoints. If the
app or Mac stops unexpectedly, surviving local media is recovered as **NEEDS
ATTENTION** and must be manually reviewed for checkpoint/gap truth before it is
called complete, aligned, exported, or delivered. Recovery does not configure
Webex and does not automatically transfer a recovered guest original.

## Safety rules

- Never send the Webex return into Jamulus.
- Never open both a Jamulus speech path and Webex speech path. Mute the audio
  interface first, or end the WebJam session before unmuting Webex.
- Never interpret **Opened externally** as confirmation that Webex joined.
- Never expose Jamulus client RPC 22222 or recorder RPC 22240 through a router.
- The lab-only v3 private path is not a Webex bridge, Internet-hosting option,
  or public remote-audio service. Do not expose it through a router.
- If routing becomes ambiguous, mute Webex first and keep Jamulus as the known
  music path.
