#!/usr/bin/env bash
set -euo pipefail

# Start the notarized Jamulus 3.12.2 server for WebJam's same-Mac pilot.
# JamulusServer.app is sandboxed, so both the RPC secret and recordings must
# live in its real container storage. Data/Music is only a symlink to ~/Music
# and is not writable by the sandbox when launched from Terminal.

SERVER="/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"
CONTAINER="$HOME/Library/Containers/app.jamulussoftware.JamulusServer/Data"
SECRET="$CONTAINER/Documents/webjam_server_rpc.secret"
RECORDINGS="$CONTAINER/Documents/WebJam Recordings"
LOG_DIR="$HOME/Library/Logs/WebJam"
LOG="$LOG_DIR/jamulus-server.log"

if [[ ! -x "$SERVER" ]]; then
  echo "JamulusServer.app 3.12.2 is required in /Applications." >&2
  echo "Install it from the official Jamulus 3.12.2 macOS disk image." >&2
  exit 1
fi

if ! "$SERVER" --version 2>&1 | grep -q "Version 3.12.2"; then
  echo "This pilot requires JamulusServer.app 3.12.2 exactly." >&2
  exit 1
fi

if lsof -nP -iUDP:22124 -iTCP:22240 2>/dev/null | grep -q .; then
  echo "UDP 22124 or TCP 22240 is already in use:" >&2
  lsof -nP -iUDP:22124 -iTCP:22240 >&2 || true
  exit 1
fi

mkdir -p "$RECORDINGS" "$LOG_DIR"
if [[ ! -s "$SECRET" ]]; then
  umask 077
  openssl rand -hex 32 > "$SECRET"
fi
chmod 600 "$SECRET"

echo "Starting Jamulus 3.12.2 server. Keep this Terminal window open."
echo "Press Ctrl+C once for a clean shutdown."
echo "Recordings: $RECORDINGS"
echo "Log: $LOG"

caffeinate -dimsu "$SERVER" \
  --nogui --port 22124 \
  --recording "$RECORDINGS" --norecord \
  --jsonrpcbindip 127.0.0.1 \
  --jsonrpcport 22240 --jsonrpcsecretfile "$SECRET" \
  --welcomemessage "WebJam private band server" \
  2>&1 | tee -a "$LOG"
