#!/bin/bash

# Assemble the generated, self-contained owner-device Xcode project that is
# carried inside WebJam's Mac candidate containers. The caller generates the
# project from ios/project.yml and compiles it before invoking this script.

set -euo pipefail

usage() {
  printf 'Usage: %s <ios-source> <output-directory> <desktop-version> <build-id>\n' "$0" >&2
  exit 64
}

[[ $# -eq 4 ]] || usage

source_root=$1
output_root=$2
desktop_version=$3
build_id=$4

[[ -d "$source_root" && ! -L "$source_root" ]] || {
  printf 'Pocket Stage source directory is missing or unsafe: %s\n' "$source_root" >&2
  exit 66
}
[[ ! -e "$output_root" ]] || {
  printf 'Refusing to replace an existing Pocket Stage setup kit: %s\n' "$output_root" >&2
  exit 73
}
[[ "$desktop_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  printf 'Desktop version is invalid: %s\n' "$desktop_version" >&2
  exit 65
}
[[ "$build_id" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Desktop build ID must be a lowercase 40-character Git commit.\n' >&2
  exit 65
}
command -v ditto >/dev/null 2>&1 || {
  printf 'ditto is required to prepare the Pocket Stage setup kit.\n' >&2
  exit 69
}

required_files=(
  "Package.swift"
  "project.yml"
  "README.md"
  "PocketStage/Info.plist"
  "PocketStage/PairingQRScanner.swift"
  "PocketStage/PocketStageApp.swift"
  "PocketStage/PocketStageTabView.swift"
  "PocketStage/StageConnectionModel.swift"
  "PocketStage/StageSocket.swift"
  "Sources/PocketStageProtocol/PocketStageProtocol.swift"
  "WebJamPocketStage.xcodeproj/project.pbxproj"
)
for relative in "${required_files[@]}"; do
  [[ -f "$source_root/$relative" && ! -L "$source_root/$relative" ]] || {
    printf 'Generated Pocket Stage input is missing or unsafe: %s\n' "$relative" >&2
    exit 66
  }
done

for directory in PocketStage Sources Tests Fixtures WebJamPocketStage.xcodeproj; do
  [[ -d "$source_root/$directory" && ! -L "$source_root/$directory" ]] || {
    printf 'Generated Pocket Stage directory is missing or unsafe: %s\n' "$directory" >&2
    exit 66
  }
  if find "$source_root/$directory" -type l -print -quit | grep -q .; then
    printf 'Generated Pocket Stage directory contains a symbolic link: %s\n' "$directory" >&2
    exit 66
  fi
done

helper="$(cd "$(dirname "$0")" && pwd)/Open Pocket Stage in Xcode.command"
[[ -f "$helper" && ! -L "$helper" && -x "$helper" ]] || {
  printf 'Pocket Stage Xcode helper is missing, unsafe, or not executable.\n' >&2
  exit 66
}

mkdir -m 755 "$output_root"
install -m 644 "$source_root/Package.swift" "$output_root/Package.swift"
install -m 644 "$source_root/project.yml" "$output_root/project.yml"
install -m 644 "$source_root/README.md" "$output_root/READ ME FIRST.txt"
for directory in PocketStage Sources Tests Fixtures WebJamPocketStage.xcodeproj; do
  ditto "$source_root/$directory" "$output_root/$directory"
done
install -m 755 "$helper" "$output_root/Open Pocket Stage in Xcode.command"
printf '%s\n' \
  'format=1' \
  "desktop_version=$desktop_version" \
  "desktop_build_id=$build_id" \
  'distribution=apple-personal-team-owner-device' \
  > "$output_root/Pocket Stage Build Info.txt"

if find "$output_root" -type l -print -quit | grep -q .; then
  printf 'Prepared Pocket Stage setup kit unexpectedly contains a symbolic link.\n' >&2
  exit 66
fi
