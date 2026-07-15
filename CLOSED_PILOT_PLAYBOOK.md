# Closed Pilot Playbook

Use this only for an intentional private Test Night. It is not part of the
ordinary musician workflow.

## Start Test Night

Launch WebJam with the bounded local flag:

```bash
WebJam --test-night
```

In a packaged macOS build, run the executable inside the app bundle with the
same flag. Then open **More → Test Night**. Normal launches do not show this
entry.

The operator dialog creates a local-only, opaque pilot run. It has no cloud
analytics and accepts no notes, audio, invite links, device names, paths, or
credentials.

## What WebJam can record automatically

WebJam may record bounded facts after the real owner reports them, including:

- app/package identity available to the running build;
- Band Check and conductor transitions;
- host readiness and invite availability;
- connection/reconnection, recording, take validation, Studio, export, and
  owned-process cleanup states.

It does not infer that another musician heard audio, that headphones were used
properly, that Studio sounded correct, or that another editor imported tracks.

## What the operator must record

At the correct point, explicitly select an outcome for:

- both musicians hearing each other;
- headphone/talkback correctness;
- whether the session was playable;
- Studio playback and track alignment; and
- whether a rehearsal moment was useful.

Use **BLOCKED** or **NOT RUN** when the second Mac, audio interface,
headphones, network, or external editor is unavailable. Do not turn an absent
prerequisite into a failure or a pass.

## Pause, restart, and failure discipline

- **Pause** before quitting; shutdown also pauses an in-progress run.
- After an app restart, an unfinished run restores as paused until the operator
  explicitly resumes it.
- **Abandon** preserves the current evidence and marks the run unfinished.
- **Restart** creates a new run; it never rewrites or deletes the earlier one.
- Preserve a failed take, export, and support bundle before changing a cable,
  device, network, or route.

The ledger is append-only, hash-linked, bounded, and stored privately beneath
WebJam application support. A later successful retry appends evidence; it
cannot erase an earlier failure.

## End of night

1. Stop recording and wait for take validation.
2. Review any transfer or recovery truth in Studio.
3. End/leave the session and wait for owned-process cleanup.
4. Export the sanitized report from Test Night only when the run is paused,
   abandoned, or complete.
5. Record physical results in [SUNDAY_TWO_MAC_PILOT.md](SUNDAY_TWO_MAC_PILOT.md).

The report contains only safe labels, opaque run ID, app/build/artifact
identity, states, outcome classes, evidence-reference classes, limitations,
and the event-chain result. It excludes audio, invites, credentials, addresses,
device identifiers, paths, names, and notes.
