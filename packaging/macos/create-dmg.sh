#!/bin/bash

# Build a conventional drag-to-Applications disk image from a prepared,
# already-signed WebJam.app bundle. Signing/notarization belongs to the caller:
# this script deliberately does not mutate the application after copying it.

set -euo pipefail

usage() {
  printf 'Usage: %s <WebJam.app> <output.dmg> [volume-name]\n' "$0" >&2
  exit 64
}

[[ $# -ge 2 && $# -le 3 ]] || usage

source_app=$1
output_dmg=$2
volume_name=${3:-WebJam}

[[ -d "$source_app" ]] || {
  printf 'Source application does not exist: %s\n' "$source_app" >&2
  exit 66
}
[[ "$(basename "$source_app")" == "WebJam.app" ]] || {
  printf 'Source application must be named WebJam.app: %s\n' "$source_app" >&2
  exit 65
}
[[ "$output_dmg" == *.dmg ]] || {
  printf 'Output must use the .dmg extension: %s\n' "$output_dmg" >&2
  exit 65
}
[[ -n "$volume_name" ]] || {
  printf 'Volume name must not be empty.\n' >&2
  exit 65
}
[[ ! -e "$output_dmg" ]] || {
  printf 'Refusing to replace an existing disk image: %s\n' "$output_dmg" >&2
  exit 73
}
command -v hdiutil >/dev/null 2>&1 || {
  printf 'hdiutil is required to build a macOS disk image.\n' >&2
  exit 69
}
command -v ditto >/dev/null 2>&1 || {
  printf 'ditto is required to preserve the application bundle.\n' >&2
  exit 69
}

output_parent=$(dirname "$output_dmg")
[[ -d "$output_parent" ]] || {
  printf 'Output directory does not exist: %s\n' "$output_parent" >&2
  exit 73
}

stage_root=$(mktemp -d "${TMPDIR:-/tmp}/webjam-dmg.XXXXXX")
image_complete=0
cleanup() {
  rm -rf -- "$stage_root"
  if [[ "$image_complete" != 1 ]]; then
    rm -f -- "$output_dmg"
  fi
}
trap cleanup EXIT

# The volume root inherits the source directory's mode. mktemp intentionally
# creates a private 0700 directory, which would make an ownership-enabled mount
# inaccessible to other accounts if it were copied as-is.
chmod 755 "$stage_root"
ditto "$source_app" "$stage_root/WebJam.app"
ln -s /Applications "$stage_root/Applications"

hdiutil create \
  -volname "$volume_name" \
  -fs HFS+ \
  -format UDZO \
  -imagekey zlib-level=9 \
  -srcfolder "$stage_root" \
  "$output_dmg"

hdiutil verify "$output_dmg"
image_complete=1
