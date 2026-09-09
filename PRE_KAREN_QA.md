# PRE_KAREN: verified Internet service control

Declared dependent base: #112 `df18426dd195dcda8265d6979d869eaad8a463f8`.
Jeff authorized Internet implementation/isolated testing on 2026-09-09. This is
transport groundwork toward both complete creative sessions, not a claim of
separate-home joining or physical acceptance. OPEN DRAFT only; Karen pending.

## Acceptance and security review

- Immutable endpoint and TLS policy; only the exact shipped profile can start
  an operation. No address, trust, redirect or certificate-policy input in IPC.
- TLS 1.3 minimum with ordinary chain and DNS-name verification and platform
  trusted roots. No exported custom TLS configuration or WebJam trust override.
- Endpoint validation before I/O, including raw non-ASCII and ambiguous names;
  zero connector, unknown/modified profile, cancelled inputs fail closed.
- DNS/TCP/handshake share a bounded deadline; no role/enrollment request can be
  written until verification succeeds. Errors omit network/certificate details.
- Initial control and fresh authenticated close retain the same connector.
  Close shares the existing total shutdown budget and does not re-enroll/retry.
  Failed TLS close leaves remote deletion unconfirmed and existing authority live.
- Underlying connection ownership must close directly on all failure/cancel/Close
  paths, avoiding TLS close-notify waits beyond the caller's budget.
- Test-only temporary CA and DNS-route injection stay package-private. Real Python
  service tests bind only owned ephemeral loopback listeners and preserve bounded
  startup/probe/shutdown. No system trust, public endpoint or live media change.

## Verification

Focused race checks passed: native TLS plus both independent Python-service
cases (9.496s), profile policy (1.187s), IPC profile/override guards (1.261s),
and the final local-only DNS-name regression (1.246s). Integration was required,
not skipped. Full required local checks
and both exact-tip hosted workflows must pass; each desktop executes the TLS
control tests with Python required, alongside existing Art runtime and packaging.
The exact tip, counts, workflow links and retained failures belong in the PR body
and AFTER. Do not substitute parent proof, skipped tests or a compiled binary
for execution. Physical Art/Music rows remain NOT RUN.

Retain the early evidence: the first launch hit a sandbox Go-cache permission
denial. The first executable connector run then reproduced a real five-second
fresh-close failure caused by TLS close-notify. The corrected client owns and
closes the underlying connection directly on every teardown path; dedicated
held-peer tests exercise explicit close, acknowledged fresh close, malformed,
canceled and lost receipts. Preserve `connector-unit-attempt-1.log`,
`connector-unit-attempt-2.log` and subsequent distinct verification logs in
`out/internet-control-tls`; a later green result does not erase the red baseline.
The first corrected race attempt was denied owned loopback binding by the
sandbox; the same bounded command passed with authorized local-socket access.
These development attempts were not frozen-tip verification. The final full
run must establish its own exact-source result.

## Limits and remaining work

No Internet profile is exposed yet. Host admission, relay return-path validation,
resource limits, deployment ownership and exact endpoint remain follow-on work.
Native Art peer names and lesson requests need authenticated protocol support;
existing local-only guards are not removed here. TLS service identity alone does
not authorize host registration or certify the relay against abuse.

No merge/squash/tag/sign/release/Pages/Publish/deploy/spend/live Cisco, automatic
capture or unsolicited send. Unsigned 0.27.2 remains Jeff-only. No short codes,
public room discovery, second media engine, other repo or second goal. Parked
#37/#49 and all prior drafts untouched. Self-QA is not Karen PASS.
