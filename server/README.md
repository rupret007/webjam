# WebJam band server

A private Jamulus server for your band with **multitrack recording**: every
musician gets their own track, and every take lands as a ready-to-open
**Reaper project** (`.rpp` + per-musician WAVs). This is the band-server
setup used by WebJam's pilot **● Record** button; recordings can also be
started from a connected Jamulus client GUI or via JSON-RPC.

## Quick start (Docker, any Linux VPS)

```bash
# 1. Get these files onto the server
git clone https://github.com/rupret007/webjam && cd webjam/server

# 2. Create the JSON-RPC secret (required once)
echo "$(openssl rand -base64 24)" > jsonrpc.secret && chmod 600 jsonrpc.secret

# 3. Go
docker compose up -d
```

The compose file pins the Jamulus container by digest so a future `latest`
image cannot change your band server unexpectedly. Review and intentionally
update `server/docker-compose.yml` when you want to upgrade Jamulus.

Open **UDP 22124** in your VPS firewall/security group. Your band's server
address is the VPS IP (or a DNS name you point at it), port `22124` — put
that in WebJam's setup wizard.

## Without Docker (bare Ubuntu/Debian)

```bash
# Jamulus isn't in stock Ubuntu — add the official repo first:
curl https://raw.githubusercontent.com/jamulussoftware/jamulus/main/linux/setup_repo.sh | sudo bash
sudo apt install jamulus-headless

mkdir -p ~/webjam-recordings
jamulus-headless --server --nogui --port 22124 \
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
- Retention/backup: recordings are your master tapes. Before a pilot, decide
  how long the VPS keeps takes, who can delete them, and where they are backed
  up. A simple starting policy is: after every session, copy the take folder to
  a second machine/cloud bucket, verify the Reaper project opens, then prune
  server-side takes older than 30 days.

## Hooking up WebJam's ● Record button (one-time, per conductor)

1. Copy the server's secret to your machine:
   `scp you@your-server:path/to/jsonrpc.secret ~/.webjam_server_rpc.secret && chmod 600 ~/.webjam_server_rpc.secret`
2. Tell WebJam where it is — either add
   `"server_rpc_secret_file": "~/.webjam_server_rpc.secret"` (use the full
   path) to `~/.webjam_config.json`, or set the
   `WEBJAM_SERVER_RPC_SECRET_FILE` environment variable.
3. Before the session, open the tunnel (the server's RPC stays loopback-only
   on purpose):
   `ssh -N -L 22240:127.0.0.1:22222 you@your-server`

Now the **● Record** button in the Conductor arms/stops the multitrack
recorder for the whole band; everyone sees the red ● REC chip while tape
rolls. Every take lands in `recordings/` as one WAV per musician + a
Reaper `.rpp`.

## Security notes

- The JSON-RPC port binds **127.0.0.1 only** — never expose it publicly.
  Reach it from your machine with an SSH tunnel:
  `ssh -N -L 22240:127.0.0.1:22222 you@your-server`
- Anyone who knows the address can join a public-internet Jamulus server.
  Don't register it in a public directory, use a non-default port if you
  like, and treat the welcome message as your "you're in the right place"
  signal.
- Recordings contain your band's audio — the `recordings/` folder is yours
  to protect like any other master tapes.
