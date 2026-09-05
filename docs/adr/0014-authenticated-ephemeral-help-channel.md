# ADR 0014: Authenticated ephemeral help channel

- Status: **Accepted**
- Date: 2026-09-04
- Scope: Troubleshooting text between a proved WebJam host and guest before
  Jamulus is ready
- Related: ADR 0001 (remote transport), remote-session threat model, short-code
  and pre-connection chat plan

## Context

A participant whose audio setup is failing needs a way to tell the other
person what is wrong without opening another meeting app. Jamulus chat starts
too late for that case. The reference transport already has a mutually proved,
generation-scoped QUIC connection before Jamulus can become useful, but no
desktop command or typed frame used that reliable plane for help.

This capability sits on a sensitive boundary. Text can leak into logs or crash
reports, an acknowledgement can be mislabeled as human attention, a stale
session can receive a late message, and a peer can turn an unbounded chat path
into memory pressure. A second socket or cloud chatbot would also create a new
authentication and privacy problem.

## Decision

WebJam carries help on the existing authorized reliable QUIC plane. The
channel is constructed only after the same certificate-pin and exporter-proof
gate that authorizes audio. It owns no listener, address, credential, database,
file, analytics event, or offline route.

Each frame has fixed magic and version plus a closed message/ack kind, sender
role, session generation, monotonic request ID, and exact payload length. The
receiver checks the outer stream generation and the inner frame generation,
requires the opposite proved role, and rejects duplicate or stale IDs.

Text has one canonical spelling: NFC plain UTF-8, nonblank, at most 500 bytes,
with HTML delimiters, newlines, controls, format characters, private-use code
points, and surrogates rejected. Sending and receiving each have a six-message
burst and a 30-per-minute token bucket. At most eight delivery receipts may be
pending, and the orchestrator-to-desktop event queue is fixed at sixteen.
Unknown frames, wrong roles/generations, replay, flooding, and backpressure
fail closed.

The receipt vocabulary is deliberately narrow:

- `help_accepted`: the local authenticated transport accepted the send;
- `help_received`: the peer transport accepted one valid message;
- `help_delivered`: the peer transport acknowledged it.

None means displayed, read, understood, or acted on. Closing, reset, expiry,
transport failure, or a new generation destroys the channel and every pending
receipt.

`send_help` is the sole IPC command that accepts bounded free-form text. The
three help-event types are never added to the desktop diagnostic timeline.
Go event formatting and Python event representations omit the content. The
desktop may give received text to an in-memory observer, which is the seam a
future reviewed UI can consume.

## Consequences

- The transport foundation for setup help is real and tested in both
  directions without weakening peer authentication or introducing a service.
- There is no user-facing help control yet. Source and releases must not claim
  pre-Jamulus chat until that UI, failure language, and real-path evidence
  exist.
- The message lives briefly in process memory and IPC pipes. Administrator
  memory dumps and a fully compromised endpoint remain out of scope.
- An invited peer can send objectionable plain text within the bound. The
  channel prevents persistence and resource growth; it does not moderate a
  trusted collaborator.

## Rejected alternatives

**Use the reference service as chat storage.** Rejected. It would expose
content and require retention, offline delivery, abuse, deletion, and
notification policies that setup help does not need.

**Acknowledge “read.”** Rejected. The transport cannot know what the UI showed
or a person noticed.

**Send before mutual proof.** Rejected. Possession of a partial invitation or
ability to reach the relay is not permission to deliver text.

**Reuse Jamulus chat.** Rejected for this narrow purpose because it is not
available when Jamulus itself is the setup problem.
