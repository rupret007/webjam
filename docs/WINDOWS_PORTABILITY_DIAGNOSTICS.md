# Windows Music/Art portability diagnostics

This draft round makes native source and frozen workflow failures diagnosable.
It does not claim to explain the earlier worker-GC aborts. The demonstrated
SessionStrip ownership defect was repaired in the preceding portability round;
its causal relationship to those crashes remains unproven. Run-specific evidence
belongs in the dependent draft PR and the identified CI artifacts.

One WebJam coding lane; all PRs remain draft. Jeff owns merges, tags, releases,
Latest and feel. No Barker/Wildflower, version changes or release publication.
The [goal prompt](WINDOWS_PORTABILITY_RELIABILITY_GOAL.md) preserves the full scope.

## Independent native control

Every WebJam CI run has a **Windows native diagnostic control** job, independent
of product tests and packaging. It first exercises actual Windows process/job
APIs, including a sleeping descendant and injected assignment/resume/query
failures. Two further controls let the root exit first: a child that finishes
naturally must pass without forced termination, while a child that outlives the
original deadline must time out and be reaped. The child starts suspended, joins an unnamed kill-on-close job, then
resumes. Receipts distinguish the original error from cleanup errors and verify
the owned process tree is empty. No global debugger or registry setting changes.

A debugger exit event can precede complete process shutdown. After the debugger
exits, the process owner waits for its job to empty using only the time remaining
on the original deadline. Receipts retain the initial members, natural drain
duration, members before cleanup and whether forced termination was needed.
An application exit event and success marker cannot hide an unfinished tree or
a missed deadline. See Microsoft's [debugging event lifecycle](https://learn.microsoft.com/en-us/windows/win32/debug/debugging-events).

The separate CDB control runs an intentional `os.abort()` in a private process,
requires a structurally valid dump, reopens it with CDB and requires a readable
`ucrtbase!abort` frame. A second control requires the actual debuggee exit code23;
CDB's own successful exit is insufficient. An expected five-second timeout of a
parent with a sleeping child proves descendant cleanup. These are labeled
controls, never fabricated product-gate failures. CDB path/version/hash, process
identities, native logs, dump bytes and evidence hashes are retained.

Open the run's **Windows native diagnostic control** job to inspect each step.
Download `webjam-windows-diagnostic-control-<run_id>-<run_attempt>` from the run's
artifacts. A missing debugger, unreadable stack, unexpected control outcome or
unverified cleanup fails the job. Tests and capture evidence upload even on
failure where files exist. Host loss can still prevent artifact upload.

## One explicit reproduction matrix

Normal push/PR runs never launch the matrix. First obtain a successful,
completed, first-attempt **push** run of WebJam CI for the exact diagnostic
branch commit. The workflow authenticates its Windows desktop job and artifact,
checks the Actions archive hash, safely extracts its inner test ZIP, and compares
every extracted file to that ZIP. Extra files and case aliases are rejected.
This binds the actual packaged Python/Qt/runtime payload, not only the executable.

Run the existing workflow once against that same branch:

```sh
gh workflow run ci.yml \
  --ref codex/windows-portability-diagnostics-20261003 \
  -f windows_diagnostic_source_run=SOURCE_PUSH_RUN_ID
```

Replace `SOURCE_PUSH_RUN_ID` with the proven producer run ID. Leave other optional
inputs false/empty. The registered workflow can run against this draft branch;
its new input may not appear in the default branch's web form until reviewed and
merged. This dispatch also runs ordinary CI jobs; the matrix lives in its own
independent control job and does not replace them. Do not dispatch a second copy
or rerun a failed matrix. Retain the first outcome and diagnose it.

The fixed order is interleaved source/frozen, with six attempts per kind:

| Attempt per kind | Launcher | GC experiment |
| --- | --- | --- |
| 1 | Plain | Ordinary automatic GC |
| 2 | Plain | Collect before each workspace worker operation |
| 3 | Plain | Collect after operation return, before terminal-result publication |
| 4 | CDB | Ordinary automatic GC |
| 5 | CDB | Collect before each workspace worker operation |
| 6 | CDB | Collect after operation return, before terminal-result publication |

Every attempt starts a fresh process and private temporary root and drives the
same Reference Studio → saved workspace → continuity → portability chain,
including restored Verify/A-B/export and cleanup. The compiled hook's fixtures
are synthetic; this is distinct from the separate-process production-recorder
proof. No physical audio device, peer or external editor is exercised.

Forced GC is an explicitly selected experiment inside the already-private smoke
hook. It temporarily disables automatic GC and collects in the actual workspace
worker; it logs PID/thread/mode and completed collections. It restores GC and the
worker method afterward. It adds no object enumeration, finalizers or timed
faulthandler watchdog. **After** does not mean after thread retirement or UI-owner
teardown; the operation and returned value remain referenced during collection.
Ordinary smoke and normal application behavior retain ordinary GC.

Plain attempts retain the existing60-second deadline. CDB attempts have a
separate120-second diagnostic deadline. Neither changes the normal gate's
assertions or deadlines. Success requires the actual application exit code0,
exact success marker, complete terminal phase, verified cleanup and, when
requested, actual worker-GC events. A malformed dump also fails the attempt.

The matrix stops at the first unexpected failure and records all remaining slots
as unexecuted. Any subsequent diagnostic replay must consume an unused slot in
the six-per-kind budget; record it against the original ledger rather than
starting a new matrix. Successful completion means **not reproduced; cause
unresolved**, never proof that the historical crash was fixed.

Download `webjam-windows-workflow-matrix-<run_id>-<run_attempt>` for `matrix.json`,
per-attempt receipts/logs/dumps, authenticated input metadata and the exact inner
package. These synthetic diagnostics expire after14days in Actions, so retain
useful evidence locally before expiry. Preserve originals and meaningful failures.

## Failure-only diagnostics during ordinary CI

A failed Windows complete native-workflow step now captures the direct source
workflow under CDB. A failed frozen Windows step captures the exact extracted
package. Both retain the original gate as **FAILED**; a successful diagnostic
replay cannot turn it green. Source replay deliberately runs the complete shared
workflow rather than the pytest module's intentional-abort unit tests.

The original native pytest and frozen runtime deadlines remain unchanged.
Failure-only diagnostic steps have an eight-minute outer bound for controls,
package checks, the120-second replay and verified process cleanup. Failure paths
copy phase/result/application logs before deleting any owned temporary root; when
cleanup cannot be proven, the root is retained and identified in the evidence.
Inherited `QT_FATAL_WARNINGS` and `QT_FATAL_CRITICALS` are recorded unchanged.

## Interpretation and owner pilot

Require actual native Windows receipts before claiming these controls passed.
Local mocks and structural dump fixtures establish boundaries, not Windows
execution. Keep original errors, debugger outcomes, process cleanup, package
identity and observed workflow outcomes separate in the draft handoff.

Physical two-Mac audio/reconnect, real-editor import and Jeff's feel remain
**NOT RUN** until observed against an identified build. The current owner route
is the [ten-minute Music/Art pilot](WORKSPACE_PORTABILITY_PILOT.md). Keep the last
verified build until any replacement is verified and delivered.

Implementation references: [CDB options](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/cdb-command-line-options),
[debugger event commands](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/sx--sxd--sxe--sxi--sxn--sxr--sx---set-exceptions-),
[loaded module paths](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/lm--list-loaded-modules-),
and [Windows job assignment](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject).
