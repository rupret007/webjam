# WebJam band server

A private Jamulus server for your band with **multitrack recording**: every
musician gets their own track, and every take lands as a ready-to-open
**Reaper project** (`.rpp` + per-musician WAVs). This is the band-server
setup used by WebJam's pilot **● Record** button; recordings can also be
started from a connected Jamulus client GUI or via JSON-RPC.

## macOS host + musician workstation (v0.8.1 pilot)

**WebJam can now host the server for you.** Enable *"This Mac hosts the band
server"* in Setup/Settings and press **Start Audio**: WebJam verifies
JamulusServer.app 3.12.2, checks the ports, creates the protected RPC secret
and recordings folder in the server app's container, starts the server with
a `caffeinate` sleep assertion, and supervises it (a crashed server is
restarted automatically). The server keeps running through Stop Audio and
stops only when WebJam quits — quitting mid-recording first stops the
recording cleanly. The manual Terminal procedure below remains the fallback
and reference; if the script's server is already running, WebJam adopts it
only after the configured secret authenticates and the recorder API responds.
The status bar then reads `Server: External :22124`. WebJam never terminates
or stops recording on an adopted process when it quits. A server WebJam starts
itself is reported as `Server: Hosting :22124` and is stopped on WebJam quit.

Install both official Jamulus 3.12.2 apps from the macOS disk image:
`/Applications/Jamulus.app` is the musician client and
`/Applications/JamulusServer.app` is the dedicated server. The downloadable
WebJam app bundles the client, but not the dedicated server. The Mac can run
the server and its own WebJam client simultaneously because the
WebJam-launched client keeps JSON-RPC `22222`, while recorder control uses
loopback-only `22240`.

Both official apps are sandboxed. In particular, the server cannot write to
an arbitrary `~/Music` directory when launched from Terminal. Keep the RPC
secret and takes in the server app's real `Data/Documents` container as shown
below; `Data/Music` is only a symlink back to `~/Music` and is not suitable.

In a dedicated Terminal window, run:

```bash
JAMULUS="/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"
CONTAINER="$HOME/Library/Containers/app.jamulussoftware.JamulusServer/Data"
SECRET="$CONTAINER/Documents/webjam_server_rpc.secret"
RECORDINGS="$CONTAINER/Documents/WebJam Recordings"
LOG="$HOME/Library/Logs/WebJam/jamulus-server.log"

test -x "$JAMULUS" || { echo "Install JamulusServer.app 3.12.2 first" >&2; exit 1; }
mkdir -p "$RECORDINGS" "$(dirname "$LOG")"
if [ ! -s "$SECRET" ]; then
  (umask 077 && openssl rand -hex 32 > "$SECRET")
fi
chmod 600 "$SECRET"

caffeinate -dimsu "$JAMULUS" \
  --nogui --port 22124 \
  --recording "$RECORDINGS" --norecord \
  --jsonrpcbindip 127.0.0.1 \
  --jsonrpcport 22240 --jsonrpcsecretfile "$SECRET" \
  --welcomemessage "WebJam private band server" \
  2>&1 | tee -a "$LOG"
```

The checked-in `server/start_macos_pilot.sh` runs this same validated command,
checks the exact version and port collisions, and creates the protected secret
on first use. Leave its Terminal window open. `caffeinate` prevents sleep;
**Ctrl+C once** stops the server cleanly. From another Terminal, verify the
intended listeners and watch the log:

```bash
lsof -nP -iUDP:22124
lsof -nP -iTCP:22240 -sTCP:LISTEN
tail -f "$HOME/Library/Logs/WebJam/jamulus-server.log"
```

The TCP listener must report `127.0.0.1:22240`, never `*:22240`. Give the Mac
a DHCP reservation, disable its VPN during the pilot, allow Jamulus through
the macOS firewall, and forward **UDP 22124 only** from the router. Never
forward TCP 22222 or 22240.

Configure the host Mac's WebJam settings with:

- Enable **This Mac hosts the band server**. WebJam then enforces Jamulus
  server `127.0.0.1`, port `22124`; stale LAN/public host values are ignored.
- Local Jamulus control port: `22222`
- `server_rpc_port`: `22240`
- `server_rpc_secret_file`: the full path to
  `~/Library/Containers/app.jamulussoftware.JamulusServer/Data/Documents/webjam_server_rpc.secret`
- `takes_directory`: the full path to
  `~/Library/Containers/app.jamulussoftware.JamulusServer/Data/Documents/WebJam Recordings`

In-app hosting is macOS-only in v0.8.1. `JamulusServer.app` is a separate
official application and is not inside the WebJam artifact; the nested
`Jamulus.app` bundle is the musician client only. Ready Check verifies the
dedicated server's exact version before the session.

The remote musician does not receive the recorder secret. For the two-Mac
pilot, test both the Mac mini's stable Tailscale address and the home's public
address on UDP 22124. Require `tailscale ping` to report a direct path rather
than DERP. For the public path, prove access from a genuinely external network.
Use whichever route has lower stable delay and no recurring dropout. If direct
UDP fails because of CGNAT or router restrictions, retain a direct Tailscale
path or approve a private VPS; a public Jamulus server cannot pass the local
recording gate.

## Docker on a Linux VPS (legacy-compatible option)

The checked-in Compose recipe remains pinned to a third-party Jamulus 3.9.0
image for reproducibility. It is compatible with the current RPC contract but
is **not** the v0.8.1 weekend validation path; that path uses the official
3.12.2 macOS binary above. Do not silently change the image digest.

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

## Official package on Ubuntu/Debian

```bash
# Jamulus isn't in stock Ubuntu — add the official repo first:
curl https://raw.githubusercontent.com/jamulussoftware/jamulus/main/linux/setup_repo.sh | sudo bash
sudo apt install jamulus-headless

mkdir -p ~/webjam-recordings
jamulus-headless --server --nogui --port 22124 \
        --recording ~/webjam-recordings --norecord \
        --jsonrpcport 22222 --jsonrpcsecretfile ~/jsonrpc.secret
```

(Wrap it in a reviewed systemd unit for restarts.)

## Recording

- `--recording <dir> --norecord` = the recorder is *ready but idle*; nothing
  is taped until someone starts it. WebJam shows a red **● REC** chip to
  every member whenever the recorder is rolling.
- Start/stop today: via JSON-RPC (`jamulusserver/startRecording` /
  `stopRecording`) or by any client GUI's Edit menu if enabled.
- Each take becomes `recordings/<timestamp>/` containing one WAV per
  musician + a `.rpp` Reaper project that opens with everything laid out.
- Logic users import the WAV stems directly; the `.rpp` file is a Reaper
  project and is not expected to open in Logic.
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
3. For a remote Linux server, open the tunnel (the server's RPC stays loopback-only
   on purpose):
   `ssh -N -L 22240:127.0.0.1:22222 you@your-server`

For the same-Mac setup above, do not open an SSH tunnel: the server already
listens on loopback port 22240.

Now the **● Record** button in the Conductor arms/stops the multitrack
recorder for the whole band; everyone sees the red ● REC chip while tape
rolls. Every take lands in `recordings/` as one WAV per musician + a
Reaper `.rpp`.

## Security notes

- The JSON-RPC port defaults to loopback and the macOS command also sets
  `--jsonrpcbindip 127.0.0.1` explicitly — never expose it publicly.
  Reach it from your machine with an SSH tunnel:
  `ssh -N -L 22240:127.0.0.1:22222 you@your-server`
- Anyone who knows the address can join a public-internet Jamulus server.
  Don't register it in a public directory, use a non-default port if you
  like, and treat the welcome message as your "you're in the right place"
  signal.
- Recordings contain your band's audio — the `recordings/` folder is yours
  to protect like any other master tapes.
