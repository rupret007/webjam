#!/bin/bash

# Build a conventional drag-to-Applications disk image from a prepared,
# already-signed WebJam.app bundle. Signing/notarization belongs to the caller:
# this script deliberately does not mutate the application after copying it.

set -euo pipefail

usage() {
  printf 'Usage: %s <WebJam.app> <output.dmg> [volume-name] [candidate-extras]\n' "$0" >&2
  exit 64
}

[[ $# -ge 2 && $# -le 4 ]] || usage

source_app=$1
output_dmg=$2
volume_name=${3:-WebJam}
candidate_extras=${4:-}

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
if [[ -n "$candidate_extras" ]]; then
  [[ -d "$candidate_extras" && ! -L "$candidate_extras" ]] || {
    printf 'Candidate extras must be a real directory: %s\n' "$candidate_extras" >&2
    exit 66
  }
  for name in \
    "Install WebJam.command" \
    "Install WebJam - Remove Quarantine.command" \
    "READ ME FIRST.txt" \
    "WebJam Candidate Info.txt"; do
    [[ -f "$candidate_extras/$name" && ! -L "$candidate_extras/$name" ]] || {
      printf 'Candidate extra is missing or unsafe: %s\n' "$name" >&2
      exit 66
    }
  done
  [[ -d "$candidate_extras/Pocket Stage iPhone Setup" \
    && ! -L "$candidate_extras/Pocket Stage iPhone Setup" ]] || {
    printf 'Pocket Stage iPhone setup kit is missing or unsafe.\n' >&2
    exit 66
  }
  if find "$candidate_extras/Pocket Stage iPhone Setup" \
    -type l -print -quit | grep -q .; then
    printf 'Pocket Stage iPhone setup kit contains a symbolic link.\n' >&2
    exit 66
  fi
  [[ -x "$candidate_extras/Pocket Stage iPhone Setup/Open Pocket Stage in Xcode.command" ]] || {
    printf 'Pocket Stage Xcode helper is not executable.\n' >&2
    exit 66
  }
  [[ -f "$candidate_extras/Pocket Stage iPhone Setup/WebJamPocketStage.xcodeproj/project.pbxproj" \
    && ! -L "$candidate_extras/Pocket Stage iPhone Setup/WebJamPocketStage.xcodeproj/project.pbxproj" ]] || {
    printf 'Pocket Stage Xcode project is missing or unsafe.\n' >&2
    exit 66
  }
  [[ -x "$candidate_extras/Install WebJam.command" ]] || {
    printf 'Guided candidate helper is not executable.\n' >&2
    exit 66
  }
  [[ -x "$candidate_extras/Install WebJam - Remove Quarantine.command" ]] || {
    printf 'Advanced candidate helper is not executable.\n' >&2
    exit 66
  }
fi
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
# Keep the temporary path's .dmg suffix: hdiutil appends .dmg when it is
# omitted, which would otherwise leave verification looking at the wrong
# filename on a successful build.
temporary_dmg="${output_dmg}.tmp.$$.dmg"
cleanup() {
  rm -rf -- "$stage_root"
  rm -f -- "$temporary_dmg"
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
if [[ -n "$candidate_extras" ]]; then
  for name in \
    "Install WebJam.command" \
    "Install WebJam - Remove Quarantine.command" \
    "READ ME FIRST.txt" \
    "WebJam Candidate Info.txt" \
    "Pocket Stage iPhone Setup"; do
    ditto "$candidate_extras/$name" "$stage_root/$name"
  done
fi
ln -s /Applications "$stage_root/Applications"

# macOS runners can briefly report the output resource as busy while a prior
# filesystem operation has drained. Build into a unique sibling and retry only
# that transient container operation; the final path is published atomically
# after verification and is never overwritten.
for attempt in 1 2 3; do
  rm -f -- "$temporary_dmg"
  if hdiutil create \
    -volname "$volume_name" \
    -fs HFS+ \
    -format UDZO \
    -imagekey zlib-level=9 \
    -srcfolder "$stage_root" \
    "$temporary_dmg"; then
    break
  fi
  if [[ "$attempt" == 3 ]]; then
    printf 'hdiutil could not create the disk image after %s attempts.\n' \
      "$attempt" >&2
    exit 1
  fi
  printf 'hdiutil reported a busy resource; retrying (%s/3).\n' "$attempt" >&2
  sync
  sleep $((attempt * 2))
done

hdiutil verify "$temporary_dmg"
mv -- "$temporary_dmg" "$output_dmg"

hdiutil verify "$output_dmg"
image_complete=1
