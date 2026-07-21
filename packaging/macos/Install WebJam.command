#!/bin/bash

# Clickable installer for WebJam's ad-hoc-signed private test candidates.
# The default path preserves macOS quarantine and guides the user through
# Apple's Open Anyway flow.  The explicit --remove-quarantine mode is exposed
# by the separately named advanced wrapper shipped beside this file.

set -euo pipefail

mode="guided"
if [[ "${1:-}" == "--remove-quarantine" ]]; then
  mode="remove-quarantine"
elif [[ $# -ne 0 ]]; then
  printf 'Usage: %s [--remove-quarantine]\n' "$0" >&2
  exit 64
fi

readonly SCRIPT_DIR="$(cd -P -- "$(dirname -- "$0")" && pwd)"
readonly SOURCE_APP="$SCRIPT_DIR/WebJam.app"
readonly CANDIDATE_INFO="$SCRIPT_DIR/WebJam Candidate Info.txt"
readonly CODESIGN_BIN="${WEBJAM_CODESIGN_BIN:-/usr/bin/codesign}"
readonly DITTO_BIN="${WEBJAM_DITTO_BIN:-/usr/bin/ditto}"
readonly FILE_BIN="${WEBJAM_FILE_BIN:-/usr/bin/file}"
readonly OPEN_BIN="${WEBJAM_OPEN_BIN:-/usr/bin/open}"
readonly PLIST_BUDDY_BIN="${WEBJAM_PLIST_BUDDY_BIN:-/usr/libexec/PlistBuddy}"
readonly SHASUM_BIN="${WEBJAM_SHASUM_BIN:-/usr/bin/shasum}"
readonly XATTR_BIN="${WEBJAM_XATTR_BIN:-/usr/bin/xattr}"

die() {
  printf 'WebJam was not installed: %s\n' "$*" >&2
  exit 1
}

pause_if_interactive() {
  if [[ -t 0 && "${WEBJAM_INSTALL_NO_PAUSE:-0}" != 1 ]]; then
    printf '\nPress Return to close this window. '
    IFS= read -r _ || true
  fi
}

trap pause_if_interactive EXIT

[[ "$(uname -s)" == "Darwin" || "${WEBJAM_INSTALL_TEST_MODE:-0}" == 1 ]] || \
  die "this helper can run only on macOS."
[[ -d "$SOURCE_APP" && ! -L "$SOURCE_APP" ]] || \
  die "WebJam.app must be beside this helper and must not be a symbolic link."
[[ -f "$CANDIDATE_INFO" && ! -L "$CANDIDATE_INFO" ]] || \
  die "WebJam Candidate Info.txt is missing or unsafe."

format=""
version=""
build_id=""
target=""
architecture=""
trust=""
while IFS='=' read -r key value; do
  case "$key" in
    format) format="$value" ;;
    version) version="$value" ;;
    build_id) build_id="$value" ;;
    target) target="$value" ;;
    architecture) architecture="$value" ;;
    trust) trust="$value" ;;
    '') ;;
    *) die "candidate metadata contains an unknown field: $key" ;;
  esac
done < "$CANDIDATE_INFO"

[[ "$format" == 1 ]] || die "candidate metadata format is unsupported."
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  die "candidate version is invalid."
[[ "$build_id" =~ ^[0-9a-f]{40}$ ]] || die "candidate build ID is invalid."
[[ "$target" == "macos-arm64" || "$target" == "macos-x64" ]] || \
  die "candidate target is invalid."
[[ "$architecture" == "arm64" || "$architecture" == "x86_64" ]] || \
  die "candidate architecture is invalid."
[[ "$trust" == "ad-hoc-unnotarized" ]] || \
  die "candidate trust declaration is invalid."

if [[ "$target" == "macos-arm64" ]]; then
  [[ "$architecture" == "arm64" ]] || die "target and architecture disagree."
else
  [[ "$architecture" == "x86_64" ]] || die "target and architecture disagree."
fi

readonly INFO_PLIST="$SOURCE_APP/Contents/Info.plist"
readonly APP_EXECUTABLE="$SOURCE_APP/Contents/MacOS/WebJam"
readonly FABRIC_EXECUTABLE="$SOURCE_APP/Contents/MacOS/webjam-fabric"
readonly FABRIC_MANIFEST="$SOURCE_APP/Contents/Resources/webjam-fabric.sha256"
readonly BUILD_ID_FILE="$SOURCE_APP/Contents/Resources/webjam-build-id.txt"

for path in "$INFO_PLIST" "$APP_EXECUTABLE" "$FABRIC_EXECUTABLE" \
  "$FABRIC_MANIFEST" "$BUILD_ID_FILE"; do
  [[ -e "$path" && ! -L "$path" ]] || die "required package file is missing or unsafe: $(basename "$path")"
done
[[ -x "$APP_EXECUTABLE" && -x "$FABRIC_EXECUTABLE" ]] || \
  die "required WebJam executables are not executable."

"$CODESIGN_BIN" --verify --deep --strict "$SOURCE_APP" >/dev/null 2>&1 || \
  die "the WebJam bundle signature is invalid or the package was modified."

packaged_version="$("$PLIST_BUDDY_BIN" -c 'Print :CFBundleShortVersionString' "$INFO_PLIST")"
[[ "$packaged_version" == "$version" ]] || \
  die "the packaged version does not match the candidate metadata."
packaged_build_id="$(tr -d '\r\n' < "$BUILD_ID_FILE")"
[[ "$packaged_build_id" == "$build_id" ]] || \
  die "the packaged build ID does not match the candidate metadata."

"$FILE_BIN" "$APP_EXECUTABLE" | grep -Fq "$architecture" || \
  die "the WebJam executable does not match the declared architecture."
"$FILE_BIN" "$FABRIC_EXECUTABLE" | grep -Fq "$architecture" || \
  die "the WebJam transport does not match the declared architecture."

expected_fabric_hash="$(tr -d '[:space:]' < "$FABRIC_MANIFEST")"
[[ "$expected_fabric_hash" =~ ^[0-9a-f]{64}$ ]] || \
  die "the packaged transport checksum is invalid."
actual_fabric_hash="$("$SHASUM_BIN" -a 256 "$FABRIC_EXECUTABLE" | awk '{print $1}')"
[[ "$actual_fabric_hash" == "$expected_fabric_hash" ]] || \
  die "the packaged transport checksum does not match."

machine="${WEBJAM_INSTALL_MACHINE:-$(uname -m)}"
if [[ "$machine" == "arm64" ]]; then
  [[ "$architecture" == "arm64" ]] || \
    die "download the Apple-silicon (arm64) WebJam package for this Mac."
elif [[ "$machine" == "x86_64" ]]; then
  [[ "$architecture" == "x86_64" ]] || \
    die "download the Intel (x64) WebJam package for this Mac."
else
  die "this Mac architecture is unsupported: $machine"
fi

readonly SYSTEM_APPLICATIONS_DIR="${WEBJAM_SYSTEM_APPLICATIONS_DIR:-/Applications}"
if [[ -n "${WEBJAM_INSTALL_DESTINATION:-}" ]]; then
  destination="$WEBJAM_INSTALL_DESTINATION"
elif [[ -d "$SYSTEM_APPLICATIONS_DIR" && -w "$SYSTEM_APPLICATIONS_DIR" ]]; then
  destination="$SYSTEM_APPLICATIONS_DIR/WebJam.app"
else
  mkdir -p -- "$HOME/Applications"
  destination="$HOME/Applications/WebJam.app"
fi

parent="$(dirname -- "$destination")"
[[ "$(basename -- "$destination")" == "WebJam.app" ]] || \
  die "the installation destination must end in WebJam.app."
mkdir -p -- "$parent"
[[ -d "$parent" && -w "$parent" ]] || \
  die "the installation folder is not writable: $parent"
[[ ! -L "$destination" ]] || die "refusing to replace a symbolic-link destination."

if [[ "$mode" == "remove-quarantine" ]]; then
  printf '%s\n' \
    'ADVANCED OPTION: This removes Apple quarantine from WebJam only.' \
    'The app is ad-hoc signed and has not been reviewed or notarized by Apple.'
  if [[ "${WEBJAM_INSTALL_ASSUME_YES:-0}" != 1 ]]; then
    printf 'Type REMOVE to continue: '
    IFS= read -r confirmation
    [[ "$confirmation" == "REMOVE" ]] || die "advanced installation was cancelled."
  fi
fi

if [[ -e "$destination" && "${WEBJAM_INSTALL_ASSUME_YES:-0}" != 1 ]]; then
  printf 'Replace the existing WebJam installation at %s? [y/N] ' "$destination"
  IFS= read -r confirmation
  [[ "$confirmation" == y || "$confirmation" == Y ]] || \
    die "installation was cancelled."
fi

stage_root="$(mktemp -d "$parent/.WebJam.install.XXXXXX")"
stage_app="$stage_root/WebJam.app"
backup=""
committed=0
cleanup_install() {
  if [[ "$committed" != 1 && -n "$backup" && -e "$backup" && ! -e "$destination" ]]; then
    mv -- "$backup" "$destination" || true
  fi
  rm -rf -- "$stage_root"
}
trap 'cleanup_install; pause_if_interactive' EXIT

"$DITTO_BIN" "$SOURCE_APP" "$stage_app"
"$CODESIGN_BIN" --verify --deep --strict "$stage_app" >/dev/null 2>&1 || \
  die "the staged application failed signature verification."
[[ "$(tr -d '\r\n' < "$stage_app/Contents/Resources/webjam-build-id.txt")" == "$build_id" ]] || \
  die "the staged application build ID changed unexpectedly."

if [[ -e "$destination" ]]; then
  backup="$parent/.WebJam.backup.$$.app"
  [[ ! -e "$backup" ]] || die "a previous WebJam installation backup already exists."
  mv -- "$destination" "$backup"
fi
if [[ "${WEBJAM_INSTALL_FAIL_AFTER_BACKUP:-0}" == 1 ]]; then
  die "injected post-backup failure."
fi
mv -- "$stage_app" "$destination"
committed=1
if [[ -n "$backup" ]]; then
  rm -rf -- "$backup"
  backup=""
fi

if [[ "$mode" == "remove-quarantine" ]]; then
  "$XATTR_BIN" -dr com.apple.quarantine "$destination"
  if "$XATTR_BIN" -lr "$destination" 2>/dev/null | grep -Fq com.apple.quarantine; then
    die "quarantine remained on part of the installed WebJam bundle."
  fi
  printf 'Installed WebJam %s at %s and removed quarantine from that app only.\n' \
    "$version" "$destination"
  "$OPEN_BIN" "$destination" || die "WebJam was installed but could not be opened."
else
  printf 'Installed WebJam %s at %s. Apple quarantine was preserved.\n' \
    "$version" "$destination"
  "$OPEN_BIN" "$destination" >/dev/null 2>&1 || true
  if "$XATTR_BIN" -lr "$destination" 2>/dev/null | grep -Fq com.apple.quarantine; then
    printf '%s\n' \
      '' \
      'If macOS blocked WebJam:' \
      '1. Open System Settings > Privacy & Security.' \
      '2. Scroll to Security and click Open Anyway for WebJam.' \
      '3. Confirm Open when macOS asks again.'
    "$OPEN_BIN" -a "System Settings" >/dev/null 2>&1 || true
  fi
fi

printf 'Installation complete.\n'
