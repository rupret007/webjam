#!/usr/bin/env bash
# Developer ID sign, notarize, staple, and independently verify WebJam release
# containers. This is intentionally separate from the ordinary ad-hoc branch
# build so an untrusted artifact can never drift into a tagged GitHub release.

set -euo pipefail

readonly COMMAND="${1:-}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly WEBJAM_ENTITLEMENTS="$SCRIPT_DIR/WebJam.entitlements"
readonly JAMULUS_ENTITLEMENTS="$SCRIPT_DIR/Jamulus.entitlements"

readonly PYTHON_BIN="${WEBJAM_PYTHON_BIN:-python3}"
readonly CODESIGN_BIN="${WEBJAM_CODESIGN_BIN:-/usr/bin/codesign}"
readonly XCRUN_BIN="${WEBJAM_XCRUN_BIN:-/usr/bin/xcrun}"
readonly SPCTL_BIN="${WEBJAM_SPCTL_BIN:-/usr/sbin/spctl}"
readonly SYSPOLICY_CHECK_BIN="${WEBJAM_SYSPOLICY_CHECK_BIN:-/usr/bin/syspolicy_check}"
readonly DITTO_BIN="${WEBJAM_DITTO_BIN:-/usr/bin/ditto}"
readonly HDIUTIL_BIN="${WEBJAM_HDIUTIL_BIN:-/usr/bin/hdiutil}"
readonly FILE_BIN="${WEBJAM_FILE_BIN:-/usr/bin/file}"
readonly SHASUM_BIN="${WEBJAM_SHASUM_BIN:-/usr/bin/shasum}"
readonly RUNNER_TEMP_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"

SIGNING_IDENTITY="${WEBJAM_MACOS_CODESIGN_IDENTITY:-}"
SIGNING_TEAM_ID="${WEBJAM_MACOS_CODESIGN_TEAM_ID:-}"
SIGNING_KEYCHAIN="${WEBJAM_MACOS_KEYCHAIN:-}"
NOTARY_KEY="${WEBJAM_NOTARY_KEY_P8:-}"
NOTARY_KEY_ID="${WEBJAM_NOTARY_KEY_ID:-}"
NOTARY_ISSUER_ID="${WEBJAM_NOTARY_ISSUER_ID:-}"

JAMULUS_APP=""
JAMULUS_SERVER_APP=""
QT_HELPER_APP=""
OUTER_EXECUTABLE=""
JAMULUS_EXECUTABLE=""
JAMULUS_SERVER_EXECUTABLE=""
QT_HELPER_EXECUTABLE=""
# Bash 3.2 (the system Bash on GitHub macOS runners) treats expansion of a
# declared-but-empty array as unbound under `set -u`; retain an inert sentinel.
temporary_paths=("")

cleanup_temporary_paths() {
  local path
  for path in "${temporary_paths[@]}"; do
    [[ -n "$path" ]] || continue
    rm -rf -- "$path"
  done
}

trap cleanup_temporary_paths EXIT

usage() {
  printf '%s\n' \
    "Usage:" \
    "  $0 app <WebJam.app> <final.zip> <evidence-dir>" \
    "  $0 dmg <WebJam.dmg> <evidence-dir>" \
    "  $0 verify-app <WebJam.app>" >&2
  exit 64
}

die() {
  printf 'macOS release trust failure: %s\n' "$*" >&2
  exit 1
}

require_env() {
  local name
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      die "required environment variable $name is empty"
    fi
  done
}

require_signing_environment() {
  require_env \
    WEBJAM_MACOS_CODESIGN_IDENTITY \
    WEBJAM_MACOS_CODESIGN_TEAM_ID \
    WEBJAM_MACOS_KEYCHAIN \
    WEBJAM_NOTARY_KEY_P8 \
    WEBJAM_NOTARY_KEY_ID \
    WEBJAM_NOTARY_ISSUER_ID
  [[ -f "$SIGNING_KEYCHAIN" ]] || die "ephemeral signing keychain is missing"
  [[ -f "$NOTARY_KEY" ]] || die "notary API private key is missing"
}

plist_executable() {
  "$PYTHON_BIN" - "$1" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as stream:
    value = plistlib.load(stream).get("CFBundleExecutable", "")
if not isinstance(value, str) or not value:
    raise SystemExit("CFBundleExecutable is missing")
print(value)
PY
}

validate_source_entitlements() {
  "$PYTHON_BIN" - \
    "$WEBJAM_ENTITLEMENTS" "$JAMULUS_ENTITLEMENTS" "$3" <<'PY'
import plistlib
import sys

expected = (
    {
        "com.apple.security.device.camera": True,
        "com.apple.security.device.audio-input": True,
        # Qt documents this entitlement for its WebEngine deployment helper.
        "com.apple.security.device.microphone": True,
    },
    {"com.apple.security.device.audio-input": True},
    {
        "com.apple.security.cs.allow-jit": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-executable-page-protection": True,
        "com.apple.security.cs.disable-library-validation": True,
    },
)
paths = sys.argv[1:]
if len(paths) != len(expected):
    raise SystemExit("release entitlement policy received the wrong file count")
for path, wanted in zip(paths, expected):
    with open(path, "rb") as stream:
        actual = plistlib.load(stream)
    if actual != wanted:
        raise SystemExit(f"unexpected release entitlements in {path}: {actual!r}")
PY
}

validate_component_policy() {
  local app="$1"
  [[ -d "$app" ]] || die "app bundle does not exist: $app"
  [[ -f "$app/Contents/Info.plist" ]] || die "outer Info.plist is missing"

  JAMULUS_APP="$app/Contents/Resources/Jamulus.app"
  JAMULUS_SERVER_APP="$app/Contents/Resources/JamulusServer.app"
  [[ -d "$JAMULUS_APP" ]] || die "bundled Jamulus.app is missing"
  [[ -d "$JAMULUS_SERVER_APP" ]] || die "bundled JamulusServer.app is missing"

  QT_HELPER_APP="$($PYTHON_BIN - "$app" <<'PY'
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
matches = []
for current, directories, _files in os.walk(root):
    directories[:] = [name for name in directories if not pathlib.Path(current, name).is_symlink()]
    path = pathlib.Path(current)
    if path.name == "QtWebEngineProcess.app":
        matches.append(path)
if len(matches) != 1:
    raise SystemExit(f"expected one QtWebEngineProcess.app; found {len(matches)}")
candidate = matches[0]
parts = candidate.relative_to(root).parts
if "QtWebEngineCore.framework" not in parts or "Helpers" not in parts:
    raise SystemExit(f"Qt WebEngine helper is in an unexpected location: {candidate}")
print(candidate)
PY
)" || die "Qt WebEngine helper policy failed"

  "$PYTHON_BIN" - \
    "$app" "$JAMULUS_APP" "$JAMULUS_SERVER_APP" "$QT_HELPER_APP" <<'PY'
import os
import pathlib
import plistlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
expected_apps = {pathlib.Path(value).resolve() for value in sys.argv[2:]}
actual_apps = set()
for current, directories, _files in os.walk(root):
    directories[:] = [name for name in directories if not pathlib.Path(current, name).is_symlink()]
    path = pathlib.Path(current).resolve()
    if path != root and path.suffix == ".app":
        actual_apps.add(path)
    for directory in directories:
        nested = pathlib.Path(current, directory)
        if nested.suffix in {".xpc", ".appex"}:
            raise SystemExit(f"unapproved independently executing bundle: {nested}")
if actual_apps != expected_apps:
    unexpected = sorted(str(path) for path in actual_apps - expected_apps)
    missing = sorted(str(path) for path in expected_apps - actual_apps)
    raise SystemExit(f"nested app policy mismatch; unexpected={unexpected}, missing={missing}")

for bundle, keys in (
    (root, ("NSMicrophoneUsageDescription", "NSCameraUsageDescription")),
    (pathlib.Path(sys.argv[2]), ("NSMicrophoneUsageDescription",)),
    (pathlib.Path(sys.argv[3]), ("NSMicrophoneUsageDescription",)),
):
    with open(bundle / "Contents" / "Info.plist", "rb") as stream:
        info = plistlib.load(stream)
    missing = [key for key in keys if not info.get(key)]
    if missing:
        raise SystemExit(f"{bundle} is missing privacy strings: {missing}")
PY

  OUTER_EXECUTABLE="$app/Contents/MacOS/$(plist_executable "$app/Contents/Info.plist")"
  JAMULUS_EXECUTABLE="$JAMULUS_APP/Contents/MacOS/$(plist_executable "$JAMULUS_APP/Contents/Info.plist")"
  JAMULUS_SERVER_EXECUTABLE="$JAMULUS_SERVER_APP/Contents/MacOS/$(plist_executable "$JAMULUS_SERVER_APP/Contents/Info.plist")"
  QT_HELPER_EXECUTABLE="$QT_HELPER_APP/Contents/MacOS/$(plist_executable "$QT_HELPER_APP/Contents/Info.plist")"
  local executable
  for executable in \
    "$OUTER_EXECUTABLE" \
    "$JAMULUS_EXECUTABLE" \
    "$JAMULUS_SERVER_EXECUTABLE" \
    "$QT_HELPER_EXECUTABLE"; do
    [[ -x "$executable" ]] || die "bundle executable is missing: $executable"
  done

  local qt_entitlements="$QT_HELPER_APP/Contents/Resources/QtWebEngineProcess.entitlements"
  [[ -f "$qt_entitlements" ]] || die "Qt helper entitlement source is missing"
  validate_source_entitlements "$WEBJAM_ENTITLEMENTS" "$JAMULUS_ENTITLEMENTS" \
    "$qt_entitlements"
}

is_macho() {
  local description
  if ! description="$($FILE_BIN -b "$1")"; then
    die "could not identify code file: $1"
  fi
  [[ "$description" == *Mach-O* ]]
}

sign_target() {
  local target="$1"
  local entitlements="${2:-}"
  local -a args=(
    --force
    --all-architectures
    --options runtime
    --timestamp
    --keychain "$SIGNING_KEYCHAIN"
    --sign "$SIGNING_IDENTITY"
  )
  if [[ -n "$entitlements" ]]; then
    args+=(--entitlements "$entitlements")
  fi
  "$CODESIGN_BIN" "${args[@]}" "$target"
}

bundle_inventory() {
  "$PYTHON_BIN" - "$1" <<'PY'
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
suffixes = {".framework", ".app", ".bundle", ".plugin", ".xpc", ".appex"}
bundles = []
for current, directories, _files in os.walk(root):
    directories[:] = [name for name in directories if not pathlib.Path(current, name).is_symlink()]
    path = pathlib.Path(current).resolve()
    if path != root and path.suffix in suffixes:
        bundles.append(path)
for path in sorted(bundles, key=lambda item: (-len(item.parts), str(item))):
    sys.stdout.buffer.write(os.fsencode(path) + b"\0")
PY
}

sign_app_inside_out() {
  local app="$1"
  local qt_entitlements="$QT_HELPER_APP/Contents/Resources/QtWebEngineProcess.entitlements"
  local target file_inventory bundle_list

  file_inventory="$(mktemp "$RUNNER_TEMP_DIR/webjam-file-inventory.XXXXXX")"
  temporary_paths+=("$file_inventory")
  bundle_list="$(mktemp "$RUNNER_TEMP_DIR/webjam-bundle-inventory.XXXXXX")"
  temporary_paths+=("$bundle_list")
  find "$app" -type f -print0 > "$file_inventory" \
    || die "could not inventory physical app files"
  bundle_inventory "$app" > "$bundle_list" \
    || die "could not inventory nested code bundles"

  # Sign every physical Mach-O leaf. Symlink aliases are deliberately skipped;
  # their canonical framework binaries are signed once.
  while IFS= read -r -d '' target; do
    if is_macho "$target"; then
      sign_target "$target"
    fi
  done < "$file_inventory"

  # Seal recognized code bundles deepest-first. App bundles receive only the
  # entitlements required by their independently executing main process.
  while IFS= read -r -d '' target; do
    case "$target" in
      "$QT_HELPER_APP")
        sign_target "$target" "$qt_entitlements"
        ;;
      "$JAMULUS_APP"|"$JAMULUS_SERVER_APP")
        sign_target "$target" "$JAMULUS_ENTITLEMENTS"
        ;;
      *.app|*.xpc|*.appex)
        die "refusing to sign an app/helper outside the explicit policy: $target"
        ;;
      *)
        sign_target "$target"
        ;;
    esac
  done < "$bundle_list"

  # The transport manifest must describe the final signed bytes. It is an
  # outer resource, so refresh it after leaf signing and before sealing WebJam.
  local fabric="$app/Contents/MacOS/webjam-fabric"
  local fabric_manifest="$app/Contents/Resources/webjam-fabric.sha256"
  [[ -x "$fabric" ]] || die "signed transport executable is missing"
  "$SHASUM_BIN" -a 256 "$fabric" | awk '{print $1}' > "$fabric_manifest"
  sign_target "$app" "$WEBJAM_ENTITLEMENTS"
}

signature_details() {
  "$CODESIGN_BIN" -d --verbose=4 "$1" 2>&1
}

verify_signature() {
  local target="$1"
  local details
  "$CODESIGN_BIN" --verify --all-architectures --strict --verbose=4 "$target"
  details="$(signature_details "$target")"
  grep -Fq 'Authority=Developer ID Application:' <<< "$details" \
    || die "Developer ID Application authority is missing: $target"
  grep -Fq "TeamIdentifier=$SIGNING_TEAM_ID" <<< "$details" \
    || die "Team ID mismatch: $target"
  grep -Eq 'flags=.*\(.*runtime.*\)' <<< "$details" \
    || die "hardened runtime is missing: $target"
  grep -Eq '^Timestamp=.+' <<< "$details" \
    || die "secure timestamp is missing: $target"
  if grep -Fq 'Signature=adhoc' <<< "$details"; then
    die "ad-hoc signature remains: $target"
  fi
}

entitlements_xml() {
  local target="$1"
  local output
  if ! output="$($CODESIGN_BIN -d --xml --entitlements - "$target")"; then
    die "could not read signed entitlements: $target"
  fi
  printf '%s' "$output"
}

verify_entitlements_exact() {
  local target="$1"
  local expected="$2"
  local actual
  actual="$(entitlements_xml "$target")"
  [[ -n "$actual" ]] || die "expected entitlements are missing: $target"
  printf '%s' "$actual" | "$PYTHON_BIN" -c '
import plistlib
import sys

expected_path = sys.argv[1]
actual = plistlib.loads(sys.stdin.buffer.read())
with open(expected_path, "rb") as stream:
    expected = plistlib.load(stream)
if actual != expected:
    raise SystemExit(
        f"entitlement mismatch for expected {expected_path}: {actual!r}"
    )
' "$expected"
}

verify_no_entitlements() {
  local target="$1"
  local actual
  actual="$(entitlements_xml "$target")"
  if [[ -n "$actual" ]]; then
    printf '%s' "$actual" | "$PYTHON_BIN" -c '
import plistlib
import sys

value = plistlib.loads(sys.stdin.buffer.read())
if value:
    raise SystemExit(f"unexpected entitlements: {value!r}")
' || die "unexpected entitlements remain: $target"
  fi
}

verify_transport_manifest() {
  local app="$1"
  local fabric="$app/Contents/MacOS/webjam-fabric"
  local manifest="$app/Contents/Resources/webjam-fabric.sha256"
  [[ -s "$manifest" ]] || die "transport hash manifest is missing"
  local actual expected
  actual="$($SHASUM_BIN -a 256 "$fabric" | awk '{print $1}')"
  expected="$(tr -d '\r\n' < "$manifest")"
  [[ "$actual" == "$expected" ]] || die "signed transport hash mismatch"
}

verify_app_core() {
  local app
  app="$(cd "$1" && pwd -P)"
  local inventory_path="${2:-}"
  local target entitlements details file_inventory bundle_list
  require_env WEBJAM_MACOS_CODESIGN_TEAM_ID
  validate_component_policy "$app"
  file_inventory="$(mktemp "$RUNNER_TEMP_DIR/webjam-verify-files.XXXXXX")"
  temporary_paths+=("$file_inventory")
  bundle_list="$(mktemp "$RUNNER_TEMP_DIR/webjam-verify-bundles.XXXXXX")"
  temporary_paths+=("$bundle_list")
  find "$app" -type f -print0 > "$file_inventory" \
    || die "could not inventory files for release verification"
  bundle_inventory "$app" > "$bundle_list" \
    || die "could not inventory bundles for release verification"

  "$CODESIGN_BIN" --verify --all-architectures --deep --strict --verbose=4 "$app"
  verify_signature "$app"
  verify_entitlements_exact "$app" "$WEBJAM_ENTITLEMENTS"
  verify_entitlements_exact "$JAMULUS_APP" "$JAMULUS_ENTITLEMENTS"
  verify_entitlements_exact "$JAMULUS_SERVER_APP" "$JAMULUS_ENTITLEMENTS"
  verify_entitlements_exact \
    "$QT_HELPER_APP" \
    "$QT_HELPER_APP/Contents/Resources/QtWebEngineProcess.entitlements"

  if [[ -n "$inventory_path" ]]; then
    : > "$inventory_path"
  fi
  while IFS= read -r -d '' target; do
    if ! is_macho "$target"; then
      continue
    fi
    verify_signature "$target"
    entitlements="$(entitlements_xml "$target")"
    if grep -Fq 'com.apple.security.get-task-allow' <<< "$entitlements"; then
      die "get-task-allow is forbidden in release code: $target"
    fi
    case "$target" in
      "$OUTER_EXECUTABLE"|"$JAMULUS_EXECUTABLE"|"$JAMULUS_SERVER_EXECUTABLE"|"$QT_HELPER_EXECUTABLE")
        ;;
      *)
        verify_no_entitlements "$target"
        ;;
    esac
    if [[ -n "$inventory_path" ]]; then
      details="$(signature_details "$target")"
      printf 'TARGET=%s\n%s\n\n' "${target#"$app"/}" "$details" \
        >> "$inventory_path"
    fi
  done < "$file_inventory"

  while IFS= read -r -d '' target; do
    verify_signature "$target"
    entitlements="$(entitlements_xml "$target")"
    if grep -Fq 'com.apple.security.get-task-allow' <<< "$entitlements"; then
      die "get-task-allow is forbidden in release bundle: $target"
    fi
    case "$target" in
      "$JAMULUS_APP"|"$JAMULUS_SERVER_APP"|"$QT_HELPER_APP")
        ;;
      *)
        verify_no_entitlements "$target"
        ;;
    esac
  done < "$bundle_list"
  verify_transport_manifest "$app"
}

verify_stapled_app() {
  local app="$1"
  local inventory_path="${2:-}"
  verify_app_core "$app" "$inventory_path"
  "$XCRUN_BIN" stapler validate "$app"
  "$SYSPOLICY_CHECK_BIN" distribution "$app"
  "$SPCTL_BIN" --assess --type exec --verbose=4 "$app"
}

notary_submit() {
  local submission="$1"
  local label="$2"
  local evidence_dir="$3"
  local result="$evidence_dir/${label}-notary-submit.json"
  local log="$evidence_dir/${label}-notary-log.json"
  local submit_rc log_rc submission_id

  set +e
  "$XCRUN_BIN" notarytool submit "$submission" \
    --key "$NOTARY_KEY" \
    --key-id "$NOTARY_KEY_ID" \
    --issuer "$NOTARY_ISSUER_ID" \
    --wait --timeout 45m --output-format json > "$result"
  submit_rc=$?
  set -e

  submission_id="$($PYTHON_BIN - "$result" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        value = json.load(stream).get("id", "")
except (OSError, json.JSONDecodeError):
    value = ""
print(value if isinstance(value, str) else "")
PY
)"

  log_rc=1
  if [[ -n "$submission_id" ]]; then
    set +e
    "$XCRUN_BIN" notarytool log \
      --key "$NOTARY_KEY" \
      --key-id "$NOTARY_KEY_ID" \
      --issuer "$NOTARY_ISSUER_ID" \
      "$submission_id" "$log"
    log_rc=$?
    set -e
  fi
  (( submit_rc == 0 )) || die "$label notarization command failed (submission ID: ${submission_id:-unavailable})"
  [[ -n "$submission_id" ]] || die "$label notarization returned no submission ID"
  (( log_rc == 0 )) || die "$label notarization log could not be retained ($submission_id)"

  "$PYTHON_BIN" - "$result" "$log" "$submission_id" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
if result.get("id") != sys.argv[3] or result.get("status") != "Accepted":
    raise SystemExit(f"notarization was not accepted: {result!r}")
with open(sys.argv[2], encoding="utf-8") as stream:
    log = json.load(stream)
if log.get("status") != "Accepted":
    raise SystemExit(f"notary log was not accepted: {log!r}")
if log.get("issues") not in (None, []):
    raise SystemExit(f"notary log contains issues: {log['issues']!r}")
PY
}

write_sha256() {
  local source="$1"
  local destination="$2"
  "$SHASUM_BIN" -a 256 "$source" > "$destination"
}

release_app() {
  [[ "$#" == 3 ]] || usage
  local app
  app="$(cd "$1" && pwd -P)"
  local final_zip="$2"
  local evidence_dir="$3"
  local temp_dir submission_zip fresh_dir
  require_signing_environment
  validate_component_policy "$app"
  mkdir -p "$evidence_dir" "$(dirname "$final_zip")"
  temp_dir="$(mktemp -d "$RUNNER_TEMP_DIR/webjam-release-trust.XXXXXX")"
  temporary_paths+=("$temp_dir")
  submission_zip="$temp_dir/WebJam-notary-submission.zip"
  fresh_dir="$temp_dir/fresh"

  sign_app_inside_out "$app"
  verify_app_core "$app" "$evidence_dir/app-signed-signature-inventory.txt"
  "$SYSPOLICY_CHECK_BIN" notary-submission "$app"
  "$DITTO_BIN" -c -k --sequesterRsrc --keepParent "$app" "$submission_zip"
  write_sha256 "$submission_zip" "$evidence_dir/app-submission-zip.sha256"
  notary_submit "$submission_zip" app "$evidence_dir"

  "$XCRUN_BIN" stapler staple "$app"
  "$XCRUN_BIN" stapler validate "$app"
  verify_stapled_app "$app" "$evidence_dir/app-stapled-signature-inventory.txt"

  rm -f -- "$final_zip"
  "$DITTO_BIN" -c -k --sequesterRsrc --keepParent "$app" "$final_zip"
  write_sha256 "$final_zip" "$evidence_dir/app-final-zip.sha256"
  mkdir -p "$fresh_dir"
  "$DITTO_BIN" -x -k "$final_zip" "$fresh_dir"
  [[ -d "$fresh_dir/WebJam.app" ]] || die "final ZIP did not contain WebJam.app"
  verify_stapled_app \
    "$fresh_dir/WebJam.app" \
    "$evidence_dir/app-fresh-zip-signature-inventory.txt"

  rm -rf -- "$temp_dir"
}

verify_dmg_signature() {
  local dmg="$1"
  local details
  "$CODESIGN_BIN" --verify --all-architectures --strict --verbose=4 "$dmg"
  details="$(signature_details "$dmg")"
  grep -Fq 'Authority=Developer ID Application:' <<< "$details" \
    || die "Developer ID Application authority is missing from DMG"
  grep -Fq "TeamIdentifier=$SIGNING_TEAM_ID" <<< "$details" \
    || die "DMG Team ID mismatch"
  grep -Eq '^Timestamp=.+' <<< "$details" \
    || die "DMG secure timestamp is missing"
  if grep -Fq 'Signature=adhoc' <<< "$details"; then
    die "DMG is ad-hoc signed"
  fi
}

release_dmg() {
  [[ "$#" == 2 ]] || usage
  local dmg="$1"
  local evidence_dir="$2"
  require_signing_environment
  [[ -f "$dmg" ]] || die "DMG does not exist: $dmg"
  mkdir -p "$evidence_dir"

  "$HDIUTIL_BIN" verify "$dmg"
  write_sha256 "$dmg" "$evidence_dir/dmg-unsigned.sha256"
  "$CODESIGN_BIN" \
    --force --all-architectures --timestamp \
    --keychain "$SIGNING_KEYCHAIN" --sign "$SIGNING_IDENTITY" "$dmg"
  verify_dmg_signature "$dmg"
  write_sha256 "$dmg" "$evidence_dir/dmg-signed.sha256"
  notary_submit "$dmg" dmg "$evidence_dir"
  "$XCRUN_BIN" stapler staple "$dmg"
  "$XCRUN_BIN" stapler validate "$dmg"
  "$HDIUTIL_BIN" verify "$dmg"
  verify_dmg_signature "$dmg"
  "$SPCTL_BIN" --assess --type open \
    --context context:primary-signature --verbose=4 "$dmg"
  write_sha256 "$dmg" "$evidence_dir/dmg-final-stapled.sha256"
  signature_details "$dmg" > "$evidence_dir/dmg-signature.txt"
}

case "$COMMAND" in
  app)
    shift
    release_app "$@"
    ;;
  dmg)
    shift
    release_dmg "$@"
    ;;
  verify-app)
    [[ "$#" == 2 ]] || usage
    verify_stapled_app "$2"
    ;;
  *)
    usage
    ;;
esac
