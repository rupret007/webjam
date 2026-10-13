#!/usr/bin/env bash
# Finish detaching the CI-owned mount before checking its copied app.
set -euo pipefail

if [[ $# -ne 1 || "$1" != /* ]]; then
  printf 'Usage: detach-dmg.sh /absolute/mountpoint\n' >&2
  exit 2
fi

mount_path="$1"
for attempt in 1 2 3; do
  if hdiutil detach "$mount_path"; then
    exit 0
  else
    status=$?
  fi

  # macOS hdiutil reports EBUSY as exit 16. A copied image can still be
  # busy briefly; other failures must stop the package verification now.
  if [[ "$status" -ne 16 || "$attempt" -eq 3 ]]; then
    exit "$status"
  fi
  printf 'Disk image is busy; retrying detach (%s/3).\n' "$attempt" >&2
  sleep $((attempt * 2))
done
