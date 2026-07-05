# WebJam band server

A private Jamulus server for your band with **multitrack recording**: every
musician gets their own track, and every take lands as a ready-to-open
**Reaper project** (`.rpp` + per-musician WAVs). This is the foundation for
WebJam's Record button (Phase B of the roadmap) — and it's useful today:
recordings can be started from any connected Jamulus client's GUI, or via
JSON-RPC.

## Quick start (Docker, any Linux VPS)

```bash
# 1. Get these files onto the server
git clone https://github.com/rupret007/webjam && cd webjam/server

# 2. Create the JSON-RPC secret (required once)
echo "$(openssl rand -base64 24)" > jsonrpc.secret && chmod 600 jsonrpc.secret

# 3. Go
docker compose up -d
```

Open **UDP 22124** in your VPS firewall/security group. Your band's server
address is the VPS IP (or a DNS name you point at it), port `22124` — put
that in WebJam's setup wizard.

## Without Docker (bare Ubuntu/Debian)

```bash
sudo apt install jamulus   # or download from jamulus.io
mkdir -p ~/webjam-recordings
jamulus --server --nogui --port 22124 \
        --recording ~/webjam-recordings --norecord \
        --jsonrpcport 22222 --jsonrpcsecretfile ~/jsonrpc.secret
```

(Wrap it in a systemd unit for restarts; ask WebJam's maintainer-bot for one.)

## Recording

- `--recording <dir> --norecord` = the recorder is *ready but idle*; nothing
  is taped until someone starts it. WebJam shows a red **● REC** chip to
  every member whenever the recorder is rolling.
- Start/stop today: via JSON-RPC (`jamulusserver/startRecording` /
  `stopRecording`) or by any client GUI's Edit menu if enabled.
- Each take becomes `recordings/<timestamp>/` containing one WAV per
  musician + a `.rpp` Reaper project that opens with everything laid out.
- Getting stems off the server: `scp -r you@server:recordings/<take> .`
  (a friendlier flow is planned for WebJam's session archive).

## Security notes

- The JSON-RPC port binds **127.0.0.1 only** — never expose it publicly.
  Reach it from your machine with an SSH tunnel:
  `ssh -L 22222:127.0.0.1:22222 you@your-server`
- Anyone who knows the address can join a public-internet Jamulus server.
  Don't register it in a public directory, use a non-default port if you
  like, and treat the welcome message as your "you're in the right place"
  signal.
- Recordings contain your band's audio — the `recordings/` folder is yours
  to protect like any other master tapes.
