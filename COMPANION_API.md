# WebJam Companion API — v0.18

The Companion API is an optional, read-only localhost HTTP surface for a DAW,
editor, or script. It is off by default. WebJam runs normally when it is off or
when FastAPI/Uvicorn are unavailable.

## Boundary

- Bind: `127.0.0.1` only, default port `8765`.
- Requests require a loopback `Host` header (`localhost`, `127.0.0.1`, or
  `[::1]`, with an optional port). Other hosts receive `403`.
- The API has no command, recording, session-control, note, or media endpoint.
- Payloads exclude musician names, internal channel IDs, invitations, network
  addresses, device names, paths, tokens, credentials, Webex state, authored
  notes, creative summaries, and raw exceptions.
- Callback failures return fixed text. Internal exception messages are never
  copied into HTTP responses.

## Endpoints

### `GET /health`

```json
{"status": "ok"}
```

This proves only that the optional local HTTP process can answer. It does not
prove a Jamulus connection, participant, recording, take, or export.

### `GET /participants`

Returns anonymous, session-local mixer slots. A slot is useful only for the
current process and is not a durable musician or Jamulus identity.

```json
{
  "participants": [
    {
      "slot": 1,
      "fader_level": 100,
      "pan": 50,
      "muted": false,
      "solo": false,
      "is_local": true
    },
    {
      "slot": 2,
      "fader_level": 80,
      "pan": 25,
      "muted": false,
      "solo": false,
      "is_local": false
    }
  ]
}
```

Do not persist a slot or use it to identify a person. Consumers must tolerate
slot reassignment and missing optional fields.

### `GET /diagnostics`

Returns a finite, privacy-safe operational summary. Example fields may be
omitted when their owner has no evidence.

```json
{
  "diagnostics": {
    "participant_count": 2,
    "session_health": {
      "process_state": "Running",
      "rpc_available": true,
      "participant_count": 2
    },
    "session_lifecycle": {
      "phase": "connected",
      "recovery_attempt": 0
    },
    "musician_guidance": {
      "schema": 1,
      "generation": 1,
      "revision": 8,
      "role": "host",
      "phase": "live",
      "primary_action": "record",
      "primary_enabled": true,
      "evidence": "human_confirmation",
      "recovery": "none",
      "outputs": [
        {"key": "recording", "state": "not_started"},
        {"key": "take", "state": "not_started"},
        {"key": "guest_media", "state": "not_required"},
        {"key": "studio", "state": "not_started"},
        {"key": "export", "state": "not_started"}
      ],
      "transitions": []
    }
  }
}
```

Guidance is an allowlist of enums, booleans, non-negative attempt counters,
fixed output keys/states, and at most five reason-free UTC lifecycle
transitions. Local titles, explanations, recovery prose, and Creative Pulse
content are deliberately absent. A consumer should use `generation` and
`revision` to discard an older observation and should treat unknown enum values
as unsupported rather than guessing.

## Enable and query

Set one of the following before launch:

| Setting | Environment variable | Default |
| --- | --- | --- |
| `companion_api_enabled` | `WEBJAM_COMPANION_API` | `false` |
| `companion_api_port` | `WEBJAM_COMPANION_API_PORT` | `8765` |

Then query locally:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/participants
curl http://127.0.0.1:8765/diagnostics
```

FastAPI and Uvicorn are optional dependencies. Install them only when this
local integration is wanted:

```bash
pip install fastapi uvicorn
```

The API remains intentionally unversioned at the URL level. Parse defensively,
ignore unknown fields, and do not depend on musician identity or private
machine details being added later.
