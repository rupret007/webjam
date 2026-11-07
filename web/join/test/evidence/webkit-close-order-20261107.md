# macOS WebKit audio close ordering

This candidate addresses the reproduced application cleanup case from
[#448](https://github.com/rupret007/webjam/issues/448). It is not a physical
Safari, ARM, or system-resource-release acceptance claim. Private website
delivery remains separate and unchanged during its real eight-hour observation.

On the Intel MacBook (macOS 15.7.9, pinned Playwright 1.64.1 / WebKit 27.2),
the unchanged cap-recovery tests both failed on parent `3ae314e`: their original
line 186 expected `['closed', 'closed']` but received `['running', 'closed']`.
The generated capture track had already ended. No microphone or live provider
was involved. Original raw failure reports/logs remain owner-only; the
[evidence summary](webkit-close-order-20261107.json) retains their hashes.

Native-only probes, without WebJam imports, media sources, capture, fake clocks
or an AudioContext subclass, narrowed the ordering behavior:

| Operation after resume | State after close and another 1,200ms |
| --- | --- |
| Immediate close | Returns to running; currentTime stays zero |
| Wait 200ms before close | Stays closed; rendering time had advanced |
| Await suspend acknowledgement, then close | Stays closed, including at zero rendering time |
| Request suspend and close without awaiting it | Returns to running; suspend rejects |

Both explicit 48kHz and default context options gave these observations. They
support an initialization-ordering investigation; they do not identify a
particular upstream callback or establish resource release. The
[Web Audio specification](https://webaudio.github.io/web-audio-api/#dom-audiocontext-close)
defines close separately from suspension; generic errors or stopped tracks are
not a replacement for the unchanged closed-state assertion.

The application now requests and awaits suspension before closing its own
context. A 250ms fallback still attempts close when suspension never acknowledges;
throws and rejections also proceed to close. The caller continues stopping its
capture tracks immediately and updating the UI without waiting for this helper.
There is no fixed initialization sleep, state-property override or user-agent
exception. The timer bounds the acknowledgement wait; it is not a wall-clock
service guarantee for a paused browser event loop.

Candidate source `3652ef6` passed all 40 Web Join unit checks, the same two strict
cap tests, and all 19 existing failure/recovery cases. No original assertion,
per-test timeout, retry setting or fixture source changed. Independent readback
matched reports, logs and source hashes and found the test processes/server gone;
the separate original expiry observer remained live.

The focused Mac diagnostic used the original static server and origin, omitted
the unused Go bridge server, and granted no physical microphone permission.
The original generated-media fixture supplied every stream. The first
incorrectly anchored selector reported No tests found and executed zero cases;
that stop was retained before the corrected selector listed and ran both cases.
This focused run is separate from normal application CI and the native-provider
CI mode. Run the existing suite normally from a checkout with its documented
Node, browser and Go prerequisites:

```sh
cd web/join
npm ci
npx playwright install webkit
npm run test:e2e -- --project=webkit test/e2e/failure-recovery.spec.js --grep 'a confirmed relay cap closes'
```

Keep physical Safari/system-resource behavior and an ARM run pending until
demonstrated. This source change does not qualify music hardware, Shared Track,
Webex, a second network, or the public community.
