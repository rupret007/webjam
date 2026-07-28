#!/usr/bin/env bash
# Verify the separately built Jamulus client used only by Reference Track.

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly EXPECTED_VERSION="3.12.2"
readonly EXPECTED_COMMIT="ffca974ed4e47b8f4621f3b583c00db2f87974fa"
readonly EXPECTED_QT_VERSION="6.10.2"
readonly EXPECTED_QT_SOURCE_SHA256="aeb78d29291a2b5fd53cb55950f8f5065b4978c25fb1d77f627d695ab9adf21e"
readonly EXPECTED_AQT_VERSION="3.3.0"
readonly EXPECTED_DEPLOYMENT_TARGET="13.0"
readonly APP_NAME="JamulusHeadlessClient.app"
readonly EXECUTABLE_NAME="JamulusHeadlessClient"

die() {
  printf 'Jamulus HEADLESS companion verification failed: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'Usage: %s <JamulusHeadlessClient.app> <arm64|x86_64> <sha256-manifest>\n' \
    "$0" >&2
  exit 64
}

[[ $# -eq 3 ]] || usage
app=$1
expected_arch=$2
manifest=$3

[[ "$expected_arch" == "arm64" || "$expected_arch" == "x86_64" ]] || usage
[[ -d "$app" && ! -L "$app" ]] || die "app bundle is missing or unsafe"
[[ "$(basename "$app")" == "$APP_NAME" ]] || die "unexpected app bundle name"
[[ -f "$manifest" && ! -L "$manifest" ]] || die "checksum manifest is missing or unsafe"

for command in codesign file lipo nm otool shasum strings tar; do
  command -v "$command" >/dev/null 2>&1 || die "$command is required"
done
[[ -x /usr/libexec/PlistBuddy ]] || die "PlistBuddy is required"

info="$app/Contents/Info.plist"
binary="$app/Contents/MacOS/$EXECUTABLE_NAME"
resources="$app/Contents/Resources"
license_dir="$resources/THIRD_PARTY_LICENSES"
provenance="$license_dir/JamulusHeadlessClient-PROVENANCE.txt"
source_offer="$license_dir/JamulusHeadlessClient-SOURCE-OFFER.txt"
build_instructions="$license_dir/JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt"
corresponding_source="$license_dir/JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz"
qt_notice="$license_dir/JamulusHeadlessClient-QT-NOTICE.txt"
qt_source="$license_dir/qtbase-everywhere-src-$EXPECTED_QT_VERSION.tar.xz"
packaged_patch="$license_dir/jamulus-headless-r3_12_2.patch"
packaged_license="$license_dir/JAMULUS_COPYING.txt"

[[ -f "$info" && ! -L "$info" ]] || die "Info.plist is missing or unsafe"
[[ -x "$binary" && ! -L "$binary" ]] || die "main executable is missing or unsafe"
for required in \
  "$provenance" "$source_offer" "$build_instructions" \
  "$corresponding_source" "$qt_notice" "$qt_source" \
  "$packaged_patch" "$packaged_license"; do
  [[ -f "$required" && ! -L "$required" ]] || \
    die "required license/provenance material is missing: $(basename "$required")"
done

plist_value() {
  /usr/libexec/PlistBuddy -c "Print :$1" "$info"
}

[[ "$(plist_value CFBundleExecutable)" == "$EXECUTABLE_NAME" ]] || \
  die "CFBundleExecutable is not $EXECUTABLE_NAME"
[[ "$(plist_value CFBundleIdentifier)" == \
  "app.jamulussoftware.JamulusHeadlessClient" ]] || \
  die "unexpected bundle identifier"
[[ "$(plist_value CFBundleVersion)" == "$EXPECTED_VERSION" ]] || \
  die "unexpected bundle version"
[[ "$(plist_value CFBundleShortVersionString)" == "$EXPECTED_VERSION" ]] || \
  die "unexpected short bundle version"

[[ "$(lipo "$binary" -archs)" == "$expected_arch" ]] || \
  die "main executable architecture does not exactly match $expected_arch"
file "$binary" | grep -Fq "$expected_arch" || die "file did not report $expected_arch"
binary_minos="$(otool -l "$binary" \
  | awk '$1 == "minos" { print $2; exit }')"
[[ "$binary_minos" == "$EXPECTED_DEPLOYMENT_TARGET" ]] || \
  die "main executable minimum macOS version is not $EXPECTED_DEPLOYMENT_TARGET"
binary_sdk="$(otool -l "$binary" \
  | awk '$1 == "sdk" { print $2; exit }')"
[[ "$binary_sdk" =~ ^[0-9]+\.[0-9]+$ ]] || \
  die "main executable macOS SDK version is missing or malformed"

expected_frameworks=$'QtConcurrent.framework\nQtCore.framework\nQtNetwork.framework\nQtXml.framework'
actual_frameworks="$(
  find "$app/Contents/Frameworks" -mindepth 1 -maxdepth 1 \
    -type d -name '*.framework' -exec basename {} \; | LC_ALL=C sort
)"
[[ "$actual_frameworks" == "$expected_frameworks" ]] || \
  die "framework inventory is not the exact headless allow-list"
[[ ! -e "$app/Contents/PlugIns" ]] || die "HEADLESS companion must not ship Qt plugins"
for framework_name in QtConcurrent QtCore QtNetwork QtXml; do
  framework_info="$app/Contents/Frameworks/$framework_name.framework/Versions/A/Resources/Info.plist"
  [[ -f "$framework_info" && ! -L "$framework_info" ]] || \
    die "$framework_name metadata is missing or unsafe"
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
    "$framework_info")" == "$EXPECTED_QT_VERSION" ]] || \
    die "$framework_name is not Qt $EXPECTED_QT_VERSION"
done

while IFS= read -r -d '' target; do
  description="$(file -b "$target")" || die "could not inspect $target"
  [[ "$description" == *Mach-O* ]] || continue
  [[ "$(lipo "$target" -archs)" == "$expected_arch" ]] || \
    die "Mach-O architecture mismatch: $target"
  # The first otool line is the inspected file's own path, not a load command.
  links="$(otool -L "$target" | tail -n +2)" || \
    die "could not inspect linkage: $target"
  if grep -Eq 'Qt(Gui|Widgets|Multimedia)(Widgets)?\\.framework' <<< "$links"; then
    die "GUI or multimedia Qt linkage is forbidden: $target"
  fi
  if grep -Eq '/(Users|private|tmp|opt/homebrew)/' <<< "$links"; then
    die "build-machine path leaked into linkage: $target"
  fi
  rpaths="$(otool -l "$target" | awk '
    $1 == "cmd" && $2 == "LC_RPATH" { want = 1; next }
    want && $1 == "path" { print $2; want = 0 }
  ')"
  if grep -Eq '^/(Users|private|tmp|opt/homebrew)/' <<< "$rpaths"; then
    die "build-machine path leaked into LC_RPATH: $target"
  fi
done < <(find "$app" -type f -print0)

[[ -z "$(find "$app" \
  \( -iname '*QtGui*' -o -iname '*QtWidgets*' -o -iname '*QtMultimedia*' \) \
  -print -quit)" ]] || die "GUI or multimedia Qt payload is forbidden"

symbol_table="$(nm -a "$binary")" || die "could not inspect client symbols"
grep -Fq '__ZN10CClientRpcC1EP7CClient' <<< "$symbol_table" || \
  die "CClientRpc capability is missing"
grep -Fq '__ZN7CClient24OnControllerInFaderLevelEii' <<< "$symbol_table" || \
  die "HEADLESS fader-application capability is missing"
strings "$binary" | grep -Fxq 'jamulusclient/setFaderLevel' || \
  die "setFaderLevel RPC method is missing"

version_output="$("$binary" --version 2>&1)" || die "version probe failed"
grep -Fq "Version $EXPECTED_VERSION" <<< "$version_output" || \
  die "runtime version is not $EXPECTED_VERSION"

codesign --verify --deep --strict --verbose=2 "$app" || \
  die "deep code-signature validation failed"
entitlements="$(codesign -d --xml --entitlements - "$app" 2>/dev/null || true)"
grep -Fq 'com.apple.security.device.audio-input' <<< "$entitlements" || \
  die "audio-input entitlement is missing"
if grep -Fq 'com.apple.security.app-sandbox' <<< "$entitlements"; then
  die "App Sandbox is forbidden for the supervised companion"
fi

provenance_value() {
  local key=$1
  local count
  count="$(grep -c "^${key}=" "$provenance" || true)"
  [[ "$count" == 1 ]] || die "provenance key is missing or duplicated: $key"
  sed -n "s/^${key}=//p" "$provenance"
}

[[ "$(provenance_value format)" == 1 ]] || die "unsupported provenance format"
[[ "$(provenance_value component)" == JamulusHeadlessClient ]] || \
  die "unexpected provenance component"
[[ "$(provenance_value version)" == "$EXPECTED_VERSION" ]] || \
  die "provenance version mismatch"
[[ "$(provenance_value source_commit)" == "$EXPECTED_COMMIT" ]] || \
  die "provenance source commit mismatch"
[[ "$(provenance_value source_tree)" =~ ^[0-9a-f]{40}$ ]] || \
  die "provenance source tree is malformed"
[[ "$(provenance_value source_archive_commit)" =~ ^[0-9a-f]{40}$ ]] || \
  die "provenance source archive commit is malformed"
[[ "$(provenance_value qt_version)" == "$EXPECTED_QT_VERSION" ]] || \
  die "provenance Qt version mismatch"
[[ "$(provenance_value qt_source_archive_sha256)" == \
  "$EXPECTED_QT_SOURCE_SHA256" ]] || \
  die "provenance Qt source checksum mismatch"
[[ "$(provenance_value aqtinstall_version)" == "$EXPECTED_AQT_VERSION" ]] || \
  die "provenance aqtinstall version mismatch"
[[ "$(provenance_value architecture)" == "$expected_arch" ]] || \
  die "provenance architecture mismatch"
[[ "$(provenance_value deployment_target)" == \
  "$EXPECTED_DEPLOYMENT_TARGET" ]] || \
  die "provenance deployment target mismatch"
[[ "$(provenance_value apple_clang_version)" == Apple\ clang\ version\ * ]] || \
  die "provenance Apple clang version is malformed"
[[ "$(provenance_value macos_sdk_version)" =~ ^[0-9]+\.[0-9]+$ ]] || \
  die "provenance macOS SDK version is malformed"
[[ "$(provenance_value macos_sdk_version)" == "$binary_sdk" ]] || \
  die "provenance macOS SDK version does not match the executable"
[[ "$(provenance_value build_mode)" == headless-client ]] || \
  die "provenance build mode is not headless-client"
[[ "$(provenance_value server_only)" == false ]] || \
  die "provenance claims a server-only build"

actual_patch_sha="$(shasum -a 256 "$packaged_patch" | awk '{print $1}')"
expected_patch_sha="$(shasum -a 256 \
  "$SCRIPT_DIR/jamulus-headless-r3_12_2.patch" | awk '{print $1}')"
[[ "$actual_patch_sha" == "$expected_patch_sha" ]] || \
  die "packaged source patch differs from the reviewed patch"
[[ "$(provenance_value patch_sha256)" == "$actual_patch_sha" ]] || \
  die "provenance patch checksum mismatch"
cmp -s "$source_offer" "$SCRIPT_DIR/JamulusHeadlessClient-SOURCE-OFFER.txt" || \
  die "packaged source offer differs from the reviewed source offer"
cmp -s \
  "$build_instructions" \
  "$SCRIPT_DIR/JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt" || \
  die "packaged build instructions differ from the reviewed instructions"
cmp -s "$qt_notice" "$SCRIPT_DIR/JamulusHeadlessClient-QT-NOTICE.txt" || \
  die "packaged Qt notice differs from the reviewed notice"
cmp -s "$packaged_license" "$SCRIPT_DIR/../../licenses/JAMULUS_COPYING.txt" || \
  die "packaged GPL text differs from the reviewed license"

source_sha="$(shasum -a 256 "$corresponding_source" | awk '{print $1}')"
[[ "$(provenance_value corresponding_source_sha256)" == "$source_sha" ]] || \
  die "corresponding-source archive checksum mismatch"
qt_source_sha="$(shasum -a 256 "$qt_source" | awk '{print $1}')"
[[ "$qt_source_sha" == "$EXPECTED_QT_SOURCE_SHA256" ]] || \
  die "packaged Qt source archive checksum mismatch"
qt_source_listing="$(tar -tJf "$qt_source")" || \
  die "packaged Qt source archive is unreadable"
qt_source_root="qtbase-everywhere-src-$EXPECTED_QT_VERSION"
for required_qt_source in \
  CMakeLists.txt \
  LICENSES/LGPL-3.0-only.txt \
  src/corelib/CMakeLists.txt \
  src/network/CMakeLists.txt \
  src/xml/CMakeLists.txt; do
  grep -Fxq "$qt_source_root/$required_qt_source" \
    <<< "$qt_source_listing" || \
    die "packaged Qt source is missing $required_qt_source"
done
if grep -E '(^|/)\.\.?(/|$)' <<< "$qt_source_listing" >/dev/null; then
  die "packaged Qt source archive contains a traversal path"
fi
source_root="JamulusHeadlessClient-source"
source_listing="$(tar -tzf "$corresponding_source")" || \
  die "corresponding-source archive is unreadable"
source_count="$(printf '%s\n' "$source_listing" | wc -l | tr -d ' ')"
[[ "$source_count" -ge 1000 ]] || \
  die "corresponding-source archive is unexpectedly incomplete"
if printf '%s\n' "$source_listing" \
  | grep -Ev "^${source_root}/([^/].*)?$" >/dev/null; then
  die "corresponding-source archive contains an unsafe path"
fi
if printf '%s\n' "$source_listing" \
  | grep -E '(^|/)\.\.?(/|$)' >/dev/null; then
  die "corresponding-source archive contains a traversal path"
fi
[[ -z "$(printf '%s\n' "$source_listing" | grep -E '(^|/)\.git(/|$)' || true)" ]] || \
  die "corresponding-source archive contains repository metadata"
for required_source in \
  COPYING \
  Jamulus.pro \
  src/client.cpp \
  src/clientrpc.cpp \
  src/main.cpp \
  src/sound/coreaudio-mac/sound.h \
  webjam-packaging/JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt \
  webjam-packaging/JamulusHeadlessClient-QT-NOTICE.txt \
  webjam-packaging/JamulusHeadlessClient-SOURCE-OFFER.txt \
  webjam-packaging/Jamulus.entitlements \
  webjam-packaging/aqtinstall-3.3.0-lock.txt \
  webjam-packaging/build-jamulus-headless-client.sh \
  webjam-packaging/jamulus-headless-r3_12_2.patch \
  webjam-packaging/verify-jamulus-headless-client.sh; do
  grep -Fxq "$source_root/$required_source" <<< "$source_listing" || \
    die "corresponding source is missing $required_source"
done
while IFS='|' read -r archived reviewed; do
  tar -xOzf "$corresponding_source" \
    "$source_root/webjam-packaging/$archived" \
    | cmp -s - "$SCRIPT_DIR/$reviewed" || \
    die "corresponding source contains an unreviewed $archived"
done <<'REVIEWED_INPUTS'
JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt|JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt
JamulusHeadlessClient-QT-NOTICE.txt|JamulusHeadlessClient-QT-NOTICE.txt
JamulusHeadlessClient-SOURCE-OFFER.txt|JamulusHeadlessClient-SOURCE-OFFER.txt
Jamulus.entitlements|Jamulus.entitlements
aqtinstall-3.3.0-lock.txt|aqtinstall-3.3.0-lock.txt
build-jamulus-headless-client.sh|build-jamulus-headless-client.sh
jamulus-headless-r3_12_2.patch|jamulus-headless-r3_12_2.patch
verify-jamulus-headless-client.sh|verify-jamulus-headless-client.sh
REVIEWED_INPUTS
tar -xOzf "$corresponding_source" "$source_root/COPYING" \
  | cmp -s - "$packaged_license" || \
  die "corresponding source contains an unexpected GPL text"
tar -xOzf "$corresponding_source" \
  "$source_root/webjam-packaging/jamulus-headless-r3_12_2.patch" \
  | cmp -s - "$packaged_patch" || \
  die "corresponding source contains an unexpected WebJam patch"
main_source="$(tar -xOzf \
  "$corresponding_source" "$source_root/src/main.cpp")" || \
  die "corresponding source main.cpp is unreadable"
grep -Fq 'defined( Q_OS_MACOS ) && !defined( HEADLESS )' \
  <<< "$main_source" || \
  die "corresponding source is missing the HEADLESS mnemonic guard"
sound_header="$(tar -xOzf "$corresponding_source" \
  "$source_root/src/sound/coreaudio-mac/sound.h")" || \
  die "corresponding source CoreAudio header is unreadable"
grep -Fq '#ifndef HEADLESS' <<< "$sound_header" || \
  die "corresponding source is missing the HEADLESS CoreAudio guard"

manifest_line="$(cat "$manifest")"
manifest_name="$APP_NAME/Contents/MacOS/$EXECUTABLE_NAME"
[[ "$manifest_line" =~ ^([0-9a-f]{64})[[:space:]][[:space:]](.+)$ ]] || \
  die "checksum manifest is malformed"
[[ "${BASH_REMATCH[2]}" == "$manifest_name" ]] || \
  die "checksum manifest names an unexpected file"
actual_sha="$(shasum -a 256 "$binary" | awk '{print $1}')"
[[ "${BASH_REMATCH[1]}" == "$actual_sha" ]] || \
  die "main executable checksum mismatch"

printf '%s\n' \
  "Verified JamulusHeadlessClient $EXPECTED_VERSION" \
  "source_commit=$EXPECTED_COMMIT" \
  "architecture=$expected_arch" \
  "sha256=$actual_sha"
