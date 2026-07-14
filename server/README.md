# WebJam band server

A private Jamulus server for your band with **multitrack recording**: every
musician gets their own track, and every take lands as a ready-to-open
**Reaper project** (`.rpp` + per-musician WAVs). This is the band-server
setup used by WebJam's pilot **● Record** button; recordings can also be
started from a connected Jamulus client GUI or via JSON-RPC.

## macOS host + musician workstation (v0.13.0 private test-night candidate)

The v0.13.0 Apple-Silicon ZIP is the active private test-night package:
`WebJam-v0.13.0-TEST-NIGHT-macos-arm64.zip`, built from `4d09810`. It is ad-hoc
signed and not notarized; its package integrity/launch checks pass, while its
physical-musician results remain **NOT RUN**. The v0.12.0 ZIP is preserved
rollback evidence.

**WebJam hosts the server automatically.** In the packaged macOS app, choose
**Host a Jam**, confirm the name and band sound, complete Band Check, and
choose **Start Session**.
WebJam then verifies the prepared JamulusServer 3.12.2 app, checks
the required listeners, creates the protected RPC secret and recordings folder
under `~/Library/Application Support/WebJam/`, starts the server with a
`caffeinate` sleep assertion, and connects the local client. The invitation is
not enabled until the hosted service is alive.

An active or validating host take blocks **End Session**. The host presses
**Stop Rec**, waits for **Take saved**, then uses End Session to disconnect the
client, stop the server WebJam owns, and release the sleep assertion. A guest's
**Leave Jam** finalizes any active opted-in v2 local original, persists its
resumable queue, attempts a final upload, and stops that guest client. The
manual Terminal procedure below is a developer fallback and protocol reference,
not a musician setup step.

If an app interruption leaves a local original unfinished, WebJam's current
source keeps the recoverable media visible rather than calling it saved. It
records durable-frame checkpoints while writing, and startup recovery creates a
**Needs Attention** project with any post-checkpoint frames disclosed as
unverified. A recovered guest original stays on that guest Mac for review; it
is not silently uploaded or presented as host-delivered media.

If the fallback script's server is already running, WebJam can adopt it only
after the configured secret authenticates and the recorder API responds.
WebJam never terminates or stops recording on an adopted process when it quits.

Downloadable WebJam builds include both official Jamulus 3.12.2 apps. Source
checkouts and the manual fallback below use `/Applications/Jamulus.app` as the
musician client and `/Applications/JamulusServer.app` as the dedicated server.
The Mac can run
the server and its own WebJam client simultaneously because the
WebJam-launched client keeps JSON-RPC `22222`, while recorder control uses
loopback-only `22240`.

The official apps used by the manual fallback are sandboxed. In particular,
the server cannot write to an arbitrary `~/Music` directory when launched
from Terminal. Keep the manual fallback's RPC secret and takes in the server
app's real `Data/Documents` container as shown below. Downloadable WebJam
builds prepare their nested copies for unattended orchestration and instead
use WebJam's own Application Support directory.

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

The TCP listener must report `127.0.0.1:22240`, never `*:22240`. For the
current same-LAN pilot, disable VPN software and allow Jamulus through the
macOS firewall if prompted. Do **not** configure router forwarding: public
Internet, NAT, and router-traversal behavior are outside this pilot. Never
forward TCP 22222 or 22240.

For source/developer runs, configure the host Mac's stored settings with:

- Enable **This Mac hosts the band server**. WebJam then enforces Jamulus
  server `127.0.0.1`, port `22124`; stale LAN/public host values are ignored.
- Local Jamulus control port: `22222`
- `server_rpc_port`: `22240`
- `server_rpc_secret_file`: packaged builds derive
  `~/Library/Application Support/WebJam/JamulusServer/webjam_server_rpc.secret`
- `takes_directory`: packaged builds derive
  `~/Library/Application Support/WebJam/JamulusServer/Recordings`

The manual `/Applications/JamulusServer.app` fallback continues to use the
sandbox-container paths shown in the script above.

One-click in-app hosting is macOS-only in the private pilot. The active v0.13.0
macOS artifact contains prepared nested client and server apps. The launch path
verifies the dedicated server before publishing the invite.

The remote musician does not receive the recorder secret. The private pilot uses
two Macs on the same local network; internet, VPN, NAT, and router traversal are
not part of the claim. Remote/private server work remains an advanced path to
validate separately after the same-LAN artifact passes. In the separate v3
loopback/CI laboratory profile, WebJam can retry an invitation only before the
sidecar begins enrollment; after enrollment begins it requires a fresh invite.
That is not an Internet-service capability.

## Docker on a Linux VPS (legacy-compatible option)

The checked-in Compose recipe remains pinned to a third-party Jamulus 3.9.0
image for reproducibility. It is compatible with the current RPC contract but
is **not** the v0.13.0 test-night validation path; that path uses the prepared
official 3.12.2 macOS binary above. Do not silently change the image digest.

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
address is the VPS IP (or a DNS name you point at it), port `22124`. This is an
advanced source/developer topology; the v0.13.0 Join screen intentionally
accepts only a complete invitation generated by the host.

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
  is taped until someone starts it. WebJam reports recording with text,
  elapsed time, and the active Record control rather than relying on color.
- Start/stop today: via JSON-RPC (`jamulusserver/startRecording` /
  `stopRecording`) or by any client GUI's Edit menu if enabled.
- Each take becomes `recordings/<timestamp>/` containing one WAV per
  musician + a `.rpp` Reaper project that opens with everything laid out.
- Logic users import the WAV stems directly; the `.rpp` file is a Reaper
  project and is not expected to open in Logic. In WebJam Studio's separate
  Logic-package flow, current source refuses an explicitly silent selected stem
  or an unaligned guest/local original until the musician deselects it, keeps an
  aligned server stem, or aligns and verifies it. This is a source safety rule,
  not a completed Logic Pro validation.
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
recorder for the whole band; recording state and elapsed time remain visible
while tape rolls. Every take lands in `recordings/` as one WAV per musician +
a Reaper `.rpp`.

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
