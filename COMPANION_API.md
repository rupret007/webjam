# WebJam Companion API

The WebJam Companion API is an optional localhost HTTP API that lets external tools (DAWs, editors, scripts) read live session state from a running WebJam instance. It is off by default and starts only when explicitly enabled and FastAPI/uvicorn are installed.

## Overview

- **Host:** `127.0.0.1`
- **Port:** `8765`
- **Protocol:** HTTP (REST)
- **Availability:** Best-effort. If `fastapi` and `uvicorn` are not installed, the API does not start; the app runs normally without it.

## Endpoints

### GET /health

Simple liveness check.

**Request:**
```
GET http://127.0.0.1:8765/health
```

**Response:** `200 OK`
```json
{
  "status": "ok"
}
```

---

### GET /participants

Returns the current mixer participants (channels) with their state.

**Request:**
```
GET http://127.0.0.1:8765/participants
```

**Response:** `200 OK`
```json
{
  "participants": [
    {
      "channel_id": 0,
      "name": "You",
      "fader_level": 100,
      "pan": 50,
      "muted": false,
      "solo": false,
      "is_local": true
    },
    {
      "channel_id": 1,
      "name": "Guitarist",
      "fader_level": 80,
      "pan": 25,
      "muted": false,
      "solo": false,
      "is_local": false
    }
  ]
}
```

---

### GET /diagnostics

Returns non-sensitive session state from the running Qt Conductor (no secrets).

**Request:**
```
GET http://127.0.0.1:8765/diagnostics
```

**Response:** `200 OK`
```json
{
  "diagnostics": {
    "jamulus_state": "Running",
    "webex_state": "Embedded",
    "jamulus_connected": "True",
    "participant_count": "3",
    "jamulus_server": "jam.example.com:22124",
    "session_health": {
      "process_state": "Running",
      "rpc_available": true,
      "participant_count": 3
    }
  }
}
```

---

## Usage

1. Launch WebJam.
2. Enable the API first (`companion_api_enabled: true` or `WEBJAM_COMPANION_API=1`); then it starts in the background when the app initializes, if FastAPI/Uvicorn are available.
3. Call the endpoints from your tool, script, or DAW integration.

**Example (curl):**
```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/participants
curl http://127.0.0.1:8765/diagnostics
```

**Example (Python):**
```python
import requests

r = requests.get("http://127.0.0.1:8765/participants")
data = r.json()
for p in data["participants"]:
    print(f"{p['name']}: fader={p['fader_level']}, muted={p['muted']}")
```

## Configuration

The API is opt-in. It starts on launch only when enabled and its dependencies are installed. Control it via settings in `~/.webjam_config.json` or environment variables:

| Setting | Env var | Default | Meaning |
|---------|---------|---------|---------|
| `companion_api_enabled` | `WEBJAM_COMPANION_API` | `false` | Set to `true`/`1` to enable the API. |
| `companion_api_port` | `WEBJAM_COMPANION_API_PORT` | `8765` | Localhost port to serve on. |

## Security

- Binds to `127.0.0.1` only.
- Every request must carry a loopback `Host` header (`localhost`, `127.0.0.1`, or `[::1]`, optional port); any other Host gets `403 Forbidden`. This blocks DNS-rebinding attacks where a malicious web page tries to read your session state through the browser. Normal localhost HTTP clients send a loopback Host automatically.
- All endpoints are read-only and the `/diagnostics` payload never includes secrets.

## Dependencies

To enable the Companion API, install:

```bash
pip install fastapi uvicorn
```

These are optional; WebJam runs without them. The API is not available when they are missing.

## Versioning

The API is currently unversioned. Endpoints and response shapes may evolve. For integrations, prefer defensive parsing (e.g. handle missing keys) and optional fields.
