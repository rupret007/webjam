# WebJam v0.16 two-Mac pilot worksheet

Fill this out against the final v0.16 package only. Do not copy a package SHA,
source SHA, or test count from v0.15.0.

## Package identity

| Field | Record |
| --- | --- |
| App version | `0.16.0` |
| Source SHA | `a36789978efbaac5e85fbc5c6ef55abae4ed42e3` |
| Archive filename | `WebJam-v0.16.0-TEST-NIGHT-macos-arm64.zip` |
| Archive SHA-256 | `3ad2da6eccd99eb3965cc0e637ff147198e19446b3d878e4631a689cd5c9bf7b` |
| Automated evidence | 1,783 passed; 18 environment-bound skips; 6 subtests; 0 failures/errors |
| Package evidence | Fresh extraction, signatures, transport, and frozen Host smoke passed |
| Rollback | v0.15.0 package/app preserved before install |

## Host Mac

- [ ] Choose **Host a Jam**.
- [ ] Private server starts before Jamulus appears.
- [ ] In Jamulus, choose interface/channels/headphones/buffer.
- [ ] Return to WebJam; confirm returned instrument sounds right.
- [ ] Skip or add Webex only after music is ready.
- [ ] Copy invitation and enter jam.

## Guest Mac

- [ ] Open or paste invitation once.
- [ ] Jamulus opens against the invited band.
- [ ] Complete Jamulus native sound setup if needed.
- [ ] Confirm the band is heard and enter jam.

## Live and record

- [ ] Both musicians hear each other through Jamulus.
- [ ] Webex, if used, remains optional and muted while playing.
- [ ] Host Record offers shared-only versus Local Originals on first use.
- [ ] Shared take stops/finalizes safely.
- [ ] Studio shows the take; choose playback output there if needed.
- [ ] Track Export imports cleanly into the chosen external editor.

## Disruption checks

- [ ] Disconnect/reconnect an interface and recover through Jamulus.
- [ ] Sleep/wake one Mac and record the truthful recovery result.
- [ ] End/Leave confirms owned client/server cleanup after recording finalizes.

Unchecked physical items are **NOT RUN**, not inferred from source tests.
