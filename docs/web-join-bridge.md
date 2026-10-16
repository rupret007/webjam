# Host-local Web Join audio bridge

`services.web_join_bridge.WebJoinBridge` owns one Jamulus client and one codec
pair for each enrolled guest. It has no sound-device, directory-server, or public
UDP listener. The host supplies a numeric Jamulus port; both UDP endpoints use
`127.0.0.1`. Browser signaling cannot supply addresses or native-library paths.

The host creates and starts the bridge, then passes its `port` and random
32-byte `token` to Pion over the existing private host IPC. Pion only authenticates
the bridge after guest enrollment succeeds. UDP authentication is `WJWB1` followed
by the token; the reply is `WJWA1` followed by the token. The source socket is
then pinned. Audio uses RTP v2 carrying standard Opus. `WJWC1` followed by the
token closes the guest. Host kick, generation rotation, and session end must
also call `close()`; `closed` and `close_reason` expose asynchronous failures.
Never put these local credentials in a browser offer.

Native libopus decodes browser 48 kHz stereo, 10/20 ms Opus. PCM is repacketized
into Jamulus custom Opus frames of 128 samples and paced at 48 kHz. The guest's
Jamulus mix is decoded and encoded as standard stereo Opus at 128 kbit/s, 20 ms,
with DTX disabled and FEC enabled. Jamulus control framing is based on the
[pinned 3.12.2 protocol](https://github.com/jamulussoftware/jamulus/blob/r3_12_2/src/protocol.cpp).
The existing monitoring adapter uses a different wire format, so this audio
client only reuses its CRC calculation.

The worker bounds incoming PCM at 60 ms, drops duplicate/stale RTP sequence
numbers, rejects oversized and malformed RTP, and stops on queue overload.
Defaults are a 180-second dormant authentication deadline, a 5-second Jamulus
media deadline, a 15-second browser media deadline, and a one-hour lifetime.
The bridge independently caps combined RTP input/output at 256 MiB; the Pion
endpoint independently enforces the cap for the one web guest supported by
this implementation. Failure closes sockets,
releases native codec states, and sends Jamulus's disconnect message.

## Native dependency

Ordinary libopus packages can omit custom modes and cannot encode Jamulus's
128-sample frames. Missing custom APIs make `start()` fail before opening any
socket. There is no runtime download or package-manager fallback.

Build the optional codec explicitly with:

```sh
packaging/macos/build-web-join-opus.sh /absolute/path/to/webjam/native/web-join
```

The script downloads the pinned Opus 1.5.2 source, verifies SHA-256, enables
custom modes, runs the upstream codec tests, and writes the library plus its
BSD license, source archive, and build provenance. On macOS, `webjam.spec`
automatically includes `native/web-join/libwebjoin_opus.dylib` in the app's
`Contents/Frameworks` when present and requires `OPUS-COPYING.txt` and
`OPUS-PROVENANCE.txt` alongside it. Normal packaging signs the library and
includes those notices. Leaving the optional library absent keeps Web Join
unavailable. A source checkout uses the same `native/web-join/` path.
Linux builds emit `libwebjoin_opus.so`. The host may instead
supply an explicit trusted absolute library path. Installing or deploying the
artifact is a separate packaging operation.

## Verification

The regular suite uses deterministic codec doubles to exercise local UDP
isolation, deadlines, quota, bounded buffering, cleanup, and strict framing:

```sh
python -m pytest tests/test_web_join_bridge.py
```

The explicit native lab requires both real dependencies and fails if either is
missing. It never silently replaces a Jamulus server or codec with a fake:

```sh
WEBJAM_WEB_JOIN_OPUS=/absolute/libwebjoin_opus.dylib \
WEBJAM_WEB_JOIN_JAMULUS=/absolute/JamulusServer \
python -m pytest tests/labs/web_join_loopback_lab.py -s
```

The lab starts its own headless server bound to loopback with directory
registration disabled. It sends synthetic standard-Opus stereo sine through
the complete bridge, verifies the frequency and amplitude of the real Jamulus
return mix, and records `WEB_JOIN_LAB` JSON with added loopback latency. Arrival
measurement polls at 20 ms, so results include up to 20 ms measurement granularity.
This isolates bridge overhead; browser audio devices, ICE/TURN, and WAN buffering
need separate end-to-end measurement.

A local macOS run against the bundled Jamulus 3.12.2 server and the pinned
custom-mode Opus 1.5.2 library passed all 27 bridge contract tests and both native
lab tests on 2026-10-16. The complete browser-codec/Jamulus-codec/server-mix path
returned 124 stereo packets with a 440 Hz peak and measured 39.52 ms added
loopback delay. This measurement excludes WAN and browser audio-device delay.

The custom encoder leaves `OPUS_BITRATE_MAX` at its default and sets its CBR
size through the 106-byte output limit. Setting an explicit custom bitrate in
upstream Opus 1.5.2 dereferences its external entropy-coder pointer before local
initialization; the direct C custom-codec probe reproduced this independently
of Python. Standard browser Opus retains the explicit 128 kbit/s setting.
Ordinary scheduling jitter retains the sample clock; longer host scheduling
pauses discard stale audio and resume with silence without increasing buffer
limits or sending a burst of stale frames.
