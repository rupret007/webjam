Make Windows Music/Art portability failures diagnosable, then repair demonstrated reliability defects through bounded self-iteration.

Use one WebJam coding lane. All PRs stay DRAFT. Jeff owns merges, tags, releases, Latest and feel. No Barker/Wildflower. Preserve unrelated edits, current environments, source, media and useful evidence. No feature expansion, engine rewrite, signing project or version changes.

Start from verified portability commit 2e13e4ae8beab398e76b66969778b0a3d1aa4075 and its retained evidence. PR172 depends on draft171 while unmerged; use one dependent draft for this next round. Both source runs, both real Jamulus versions and all desktop gates passed. The delivered Mac build is Desktop/WebJam-TEST-2e13e4a. Earlier Windows native worker-GC aborts remain unexplained. The SessionStrip ownership defect was proven repaired, but its causal relationship to those aborts is unproven. Failure-only native CDB capture has not executed.

1. Assess existing diagnostics and CI before editing. Reuse the current helper and exact source/package identity checks. Define a small explicit experiment matrix and retain the starting evidence. Keep Host/Join quick and planning optional.

2. Prove diagnostic capture on an actual Windows runner independently of a product failure. Use a private intentional-abort control with bounded process-tree cleanup. Confirm debugger identity and a readable dump containing thread/module/stack/context data; retain native logs and hashes. Clearly distinguish an expected diagnostic-control crash from an ordinary product gate. Do not fabricate a failing original gate or alter global debugger/trust settings.

3. Support useful retained evidence for both the native complete-workflow process and the frozen package. Preserve exact checkout/build/executable/package binding and inherited Qt fatal settings. Original gate outcomes, assertions and deadlines stay unchanged. A successful diagnostic retry must never replace or erase an earlier failure.

4. Run one bounded reproduction matrix: at most six native and six frozen complete-workflow attempts in fresh processes and owned private roots, including ordinary execution and clearly labeled controlled-GC experiments. Reuse restored Verify/A-B/export/teardown coverage. Retain every outcome and stop the matrix at the first unexpected failure to diagnose it. Do not spend unbounded CI time trying to force recurrence. If no crash recurs, report "not reproduced; cause unresolved."

5. Self-iterate on concrete findings: reproduce, inspect the evidence, identify the cause, make the smallest coherent repair, add a meaningful regression, review it independently and retest. Fix issues uncovered within this scope without waiting for routine approval. Preserve before/after evidence. No speculative rewrite or causal-fix claim based only on a later pass. Run the established exact-head required software gates for final changes.

6. Update actual-click docs and the draft PR with the diagnostic workflow, findings and limitations. Deliver a concise evidence-backed handoff with source/package identities and any verified replacement test build needed for changed application bytes. Remove only freshly verified obsolete builds and owned duplicate copies after replacement; retain useful failures and current environments.

Physical two-Mac audio/reconnect, real-editor import and feel remain NOT RUN until observed. Incorporate owner-provided observations against the identified build without inventing results or blocking independent software work.

Done when native Windows diagnostic capture is proven, the bounded matrix is accounted for, demonstrated in-scope defects are repaired and regression-tested, required final gates pass, and the draft handoff clearly separates proven fixes from unresolved causes and unrun physical checks.
