#!/usr/bin/env bash
# Fetch pinned inputs, patch, build, and ad-hoc sign the Reference Track client.

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_REPOSITORY="https://github.com/jamulussoftware/jamulus.git"
readonly QT_VERSION="6.10.2"
readonly QT_SOURCE_ARCHIVE_NAME="qtbase-everywhere-src-6.10.2.tar.xz"
readonly QT_SOURCE_ARCHIVE_SHA256="aeb78d29291a2b5fd53cb55950f8f5065b4978c25fb1d77f627d695ab9adf21e"
readonly QT_SOURCE_ARCHIVE_URL="https://download.qt.io/official_releases/qt/6.10/6.10.2/submodules/$QT_SOURCE_ARCHIVE_NAME"
readonly AQTINSTALL_VERSION="3.3.0"
readonly DEPLOYMENT_TARGET="13.0"
readonly APP_NAME="JamulusHeadlessClient.app"
readonly EXECUTABLE_NAME="JamulusHeadlessClient"
readonly AQT_LOCK="$SCRIPT_DIR/aqtinstall-3.3.0-lock.txt"
readonly QT_NOTICE="$SCRIPT_DIR/JamulusHeadlessClient-QT-NOTICE.txt"
readonly ENTITLEMENTS="$SCRIPT_DIR/Jamulus.entitlements"
readonly VERIFY="$SCRIPT_DIR/verify-jamulus-headless-client.sh"

die() {
  printf 'Jamulus HEADLESS companion build failed: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    "Usage: $0 <arm64|x86_64> <output/JamulusHeadlessClient.app> [r3_12_2|r3_12_3]" \
    >&2
  exit 64
}

[[ $# -eq 2 || $# -eq 3 ]] || usage
architecture=$1
output_app=$2
profile=${3:-r3_12_2}

case "$profile" in
  r3_12_2)
    VERSION="3.12.2"
    SOURCE_TAG="r3_12_2"
    SOURCE_COMMIT="ffca974ed4e47b8f4621f3b583c00db2f87974fa"
    PATCH_NAME="jamulus-headless-r3_12_2.patch"
    SOURCE_OFFER_NAME="JamulusHeadlessClient-SOURCE-OFFER.txt"
    BUILD_INSTRUCTIONS_NAME="JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt"
    LICENSE_NAME="JAMULUS_COPYING.txt"
    ;;
  r3_12_3)
    VERSION="3.12.3"
    SOURCE_TAG="r3_12_3"
    SOURCE_COMMIT="74dc422116983a2173eb917cb4d6a403886b31e5"
    PATCH_NAME="jamulus-headless-r3_12_3.patch"
    SOURCE_OFFER_NAME="JamulusHeadlessClient-r3_12_3-SOURCE-OFFER.txt"
    BUILD_INSTRUCTIONS_NAME="JamulusHeadlessClient-r3_12_3-BUILD-INSTRUCTIONS.txt"
    LICENSE_NAME="JAMULUS_COPYING-r3_12_3.txt"
    ;;
  *)
    usage
    ;;
esac
readonly profile VERSION SOURCE_TAG SOURCE_COMMIT PATCH_NAME
readonly SOURCE_OFFER_NAME BUILD_INSTRUCTIONS_NAME LICENSE_NAME
readonly PATCH="$SCRIPT_DIR/$PATCH_NAME"
readonly SOURCE_OFFER="$SCRIPT_DIR/$SOURCE_OFFER_NAME"
readonly BUILD_INSTRUCTIONS="$SCRIPT_DIR/$BUILD_INSTRUCTIONS_NAME"
readonly LICENSE="$SCRIPT_DIR/../../licenses/$LICENSE_NAME"

[[ "$architecture" == "arm64" || "$architecture" == "x86_64" ]] || usage
[[ "$(uname -s)" == Darwin ]] || die "this build requires macOS"
[[ "$(basename "$output_app")" == "$APP_NAME" ]] || \
  die "output app must be named $APP_NAME"
[[ ! -e "$output_app" ]] || die "refusing to replace existing output: $output_app"
output_parent="$(cd "$(dirname "$output_app")" && pwd -P)" || \
  die "output parent does not exist"
manifest="$output_parent/JamulusHeadlessClient.sha256"
[[ ! -e "$manifest" ]] || die "refusing to replace existing manifest: $manifest"

for required in \
  "$PATCH" "$SOURCE_OFFER" "$BUILD_INSTRUCTIONS" "$AQT_LOCK" "$QT_NOTICE" \
  "$LICENSE" "$ENTITLEMENTS" "$VERIFY" "$0"; do
  [[ -f "$required" && ! -L "$required" ]] || \
    die "reviewed build input is missing or unsafe: $required"
done
for command in \
  codesign curl ditto file git gzip install_name_tool lipo make nm otool shasum \
  strings sysctl tar xcrun; do
  command -v "$command" >/dev/null 2>&1 || die "$command is required"
done
[[ -x /usr/libexec/PlistBuddy ]] || die "PlistBuddy is required"

python_bin="${WEBJAM_JAMULUS_BUILD_PYTHON:-python3}"
"$python_bin" -c 'import aqt' >/dev/null 2>&1 || \
  die "aqtinstall $AQTINSTALL_VERSION is required"
actual_aqt="$("$python_bin" -c \
  'from importlib.metadata import version; print(version("aqtinstall"))')"
[[ "$actual_aqt" == "$AQTINSTALL_VERSION" ]] || \
  die "aqtinstall must be exactly $AQTINSTALL_VERSION (found $actual_aqt)"

work_parent="${WEBJAM_JAMULUS_BUILD_ROOT:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}}"
[[ -d "$work_parent" && ! -L "$work_parent" ]] || \
  die "build root is missing or unsafe: $work_parent"
work_dir="$(mktemp -d "$work_parent/webjam-jamulus-headless.XXXXXX")"
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

source_dir="$work_dir/source"
qt_root="$work_dir/qt"
build_dir="$work_dir/build"
stage_app="$work_dir/stage/$APP_NAME"
mkdir -p "$source_dir" "$build_dir" "$(dirname "$stage_app")"

git -C "$source_dir" init -q
git -C "$source_dir" remote add origin "$SOURCE_REPOSITORY"
git -C "$source_dir" fetch -q --depth=1 origin "$SOURCE_COMMIT"
[[ "$(git -C "$source_dir" rev-parse FETCH_HEAD)" == "$SOURCE_COMMIT" ]] || \
  die "downloaded source does not match the pinned commit"
git -C "$source_dir" checkout -q --detach "$SOURCE_COMMIT"
[[ -z "$(git -C "$source_dir" status --short)" ]] || \
  die "pinned source checkout is unexpectedly dirty"
grep -Fxq "VERSION = $VERSION" "$source_dir/Jamulus.pro" || \
  die "pinned source does not declare Jamulus $VERSION"
cmp -s "$source_dir/COPYING" "$LICENSE" || \
  die "reviewed license text does not match pinned upstream source"

git -C "$source_dir" apply --check "$PATCH"
git -C "$source_dir" apply "$PATCH"
changed_files="$(git -C "$source_dir" diff --name-only | LC_ALL=C sort)"
expected_changed=$'src/main.cpp\nsrc/sound/coreaudio-mac/sound.h'
[[ "$changed_files" == "$expected_changed" ]] || \
  die "source patch changed files outside the two-file allow-list"
git -C "$source_dir" diff --check
SOURCE_DATE_EPOCH="$(git -C "$source_dir" show -s --format=%ct HEAD)"

"$python_bin" - "$source_dir" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
project = (root / "Jamulus.pro").read_text(encoding="utf-8")
client = (root / "src" / "client.cpp").read_text(encoding="utf-8")
rpc = (root / "src" / "clientrpc.cpp").read_text(encoding="utf-8")
main = (root / "src" / "main.cpp").read_text(encoding="utf-8")
sound = (root / "src" / "sound" / "coreaudio-mac" / "sound.h").read_text(
    encoding="utf-8"
)

required = {
    "HEADLESS project definition": "DEFINES += HEADLESS" in project,
    "HEADLESS removes Qt GUI": 'QT -= gui' in project,
    "client RPC is compiled when not server-only": "SOURCES += src/clientrpc.cpp" in project,
    "HEADLESS fader applies directly": (
        "#ifdef HEADLESS\n    // only apply new fader level" in client
        and "SetRemoteChanGain ( iChannelIdx" in client
    ),
    "setFaderLevel RPC is present": '"jamulusclient/setFaderLevel"' in rpc,
    'setFaderLevel exact "ok" response is present': (
        'response["result"] = "ok";' in rpc
    ),
    "mnemonic call is excluded from HEADLESS": (
        "defined( Q_OS_MACOS ) && !defined( HEADLESS )" in main
    ),
    "CoreAudio QMessageBox is excluded from HEADLESS": (
        "#ifndef HEADLESS\n#    include <QMessageBox>\n#endif" in sound
    ),
}
missing = [name for name, passed in required.items() if not passed]
if missing:
    raise SystemExit("pinned source contract failed: " + ", ".join(missing))
PY

# Corresponding source accompanies the binary rather than relying on a future
# external download. Archive the exact patched tree plus every reviewed
# script/configuration file used to control this specialized build.
source_packaging="$source_dir/webjam-packaging"
mkdir -p "$source_packaging"
install -m 644 "$PATCH" \
  "$source_packaging/$PATCH_NAME"
install -m 644 "$SOURCE_OFFER" \
  "$source_packaging/JamulusHeadlessClient-SOURCE-OFFER.txt"
install -m 644 "$BUILD_INSTRUCTIONS" \
  "$source_packaging/JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt"
install -m 644 "$AQT_LOCK" \
  "$source_packaging/aqtinstall-3.3.0-lock.txt"
install -m 644 "$QT_NOTICE" \
  "$source_packaging/JamulusHeadlessClient-QT-NOTICE.txt"
install -m 644 "$ENTITLEMENTS" \
  "$source_packaging/Jamulus.entitlements"
install -m 755 "$0" \
  "$source_packaging/build-jamulus-headless-client.sh"
install -m 755 "$VERIFY" \
  "$source_packaging/verify-jamulus-headless-client.sh"
git -C "$source_dir" add -- \
  src/main.cpp \
  src/sound/coreaudio-mac/sound.h \
  webjam-packaging
source_tree="$(git -C "$source_dir" write-tree)"
source_archive_commit="$(
  printf '%s\n' 'WebJam JamulusHeadlessClient corresponding source' \
    | env \
      GIT_AUTHOR_NAME=WebJam \
      GIT_AUTHOR_EMAIL=noreply@webjam.local \
      GIT_AUTHOR_DATE="$SOURCE_DATE_EPOCH +0000" \
      GIT_COMMITTER_NAME=WebJam \
      GIT_COMMITTER_EMAIL=noreply@webjam.local \
      GIT_COMMITTER_DATE="$SOURCE_DATE_EPOCH +0000" \
      git -C "$source_dir" commit-tree "$source_tree" -p "$SOURCE_COMMIT"
)"
corresponding_source="$work_dir/JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz"
git -C "$source_dir" archive \
  --format=tar \
  --prefix=JamulusHeadlessClient-source/ \
  "$source_archive_commit" \
  | gzip -n -9 > "$corresponding_source"
tar -tzf "$corresponding_source" >/dev/null
corresponding_source_sha="$(shasum -a 256 "$corresponding_source" \
  | awk '{print $1}')"

qt_source_archive="$work_dir/$QT_SOURCE_ARCHIVE_NAME"
if [[ -n "${WEBJAM_JAMULUS_QT_SOURCE_ARCHIVE:-}" ]]; then
  reviewed_qt_source="$WEBJAM_JAMULUS_QT_SOURCE_ARCHIVE"
  [[ -f "$reviewed_qt_source" && ! -L "$reviewed_qt_source" ]] || \
    die "pre-downloaded Qt source archive is missing or unsafe"
  install -m 644 "$reviewed_qt_source" "$qt_source_archive"
else
  curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 \
    --output "$qt_source_archive" \
    "$QT_SOURCE_ARCHIVE_URL"
fi
actual_qt_source_sha="$(shasum -a 256 "$qt_source_archive" \
  | awk '{print $1}')"
[[ "$actual_qt_source_sha" == "$QT_SOURCE_ARCHIVE_SHA256" ]] || \
  die "Qt source archive checksum does not match the reviewed input"
qt_source_listing="$(tar -tJf "$qt_source_archive")" || \
  die "Qt source archive is unreadable"
qt_source_root="qtbase-everywhere-src-$QT_VERSION"
for required_qt_source in \
  CMakeLists.txt \
  LICENSES/LGPL-3.0-only.txt \
  src/corelib/CMakeLists.txt \
  src/network/CMakeLists.txt \
  src/xml/CMakeLists.txt; do
  grep -Fxq "$qt_source_root/$required_qt_source" \
    <<< "$qt_source_listing" || \
    die "Qt source archive is missing $required_qt_source"
done
if grep -E '(^|/)\.\.?(/|$)' <<< "$qt_source_listing" >/dev/null; then
  die "Qt source archive contains a traversal path"
fi

if [[ -n "${WEBJAM_JAMULUS_QT_ROOT:-}" ]]; then
  qt_dir="$WEBJAM_JAMULUS_QT_ROOT"
  [[ -d "$qt_dir" && ! -L "$qt_dir" ]] || \
    die "preinstalled Qt root is missing or unsafe"
else
  "$python_bin" -m aqt install-qt mac desktop "$QT_VERSION" clang_64 \
    --outputdir "$qt_root" --archives qtbase qttools qttranslations
  qt_dir="$qt_root/$QT_VERSION/macos"
fi
qmake="$qt_dir/bin/qmake"
[[ -x "$qmake" ]] || die "aqtinstall did not produce the pinned qmake"
[[ "$("$qmake" -query QT_VERSION)" == "$QT_VERSION" ]] || \
  die "qmake version is not $QT_VERSION"

export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export TZ=UTC
export SOURCE_DATE_EPOCH
export ZERO_AR_DATE=1

"$qmake" "$source_dir/Jamulus.pro" -o "$build_dir/Makefile" \
  "CONFIG+=release" \
  "CONFIG-=debug_and_release" \
  "CONFIG+=headless" \
  "CONFIG-=serveronly" \
  "TARGET=$EXECUTABLE_NAME" \
  "QMAKE_APPLE_DEVICE_ARCHS=$architecture" \
  "QT_ARCH=$architecture" \
  "QMAKE_MACOSX_DEPLOYMENT_TARGET=$DEPLOYMENT_TARGET"
# Jamulus's generated resource rule depends on the translated QM files,
# but qmake does not make the default target depend on their aggregate target.
# Build that deterministic prerequisite explicitly before compiling.
make -C "$build_dir" -f Makefile.Release -j 1 compiler_lrelease_make_all
make -C "$build_dir" -j 1

raw_app="$build_dir/$APP_NAME"
raw_binary="$raw_app/Contents/MacOS/$EXECUTABLE_NAME"
[[ -x "$raw_binary" ]] || die "qmake did not produce the client app"
[[ "$(lipo "$raw_binary" -archs)" == "$architecture" ]] || \
  die "qmake output architecture mismatch"
ditto "$raw_app" "$stage_app"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleShortVersionString $VERSION" \
  "$stage_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleVersion $VERSION" \
  "$stage_app/Contents/Info.plist"

# qmake records its build-only Qt path as a fallback rpath. Remove it so the
# package can load only the four audited frameworks beside the executable.
if otool -l "$stage_app/Contents/MacOS/$EXECUTABLE_NAME" \
  | grep -Fq "$qt_dir/lib"; then
  install_name_tool -delete_rpath "$qt_dir/lib" \
    "$stage_app/Contents/MacOS/$EXECUTABLE_NAME"
fi

framework_root="$stage_app/Contents/Frameworks"
mkdir -p "$framework_root"
for framework_name in QtConcurrent QtCore QtNetwork QtXml; do
  source_framework="$qt_dir/lib/$framework_name.framework"
  destination_framework="$framework_root/$framework_name.framework"
  source_binary="$source_framework/Versions/A/$framework_name"
  [[ -f "$source_binary" ]] || die "Qt framework is missing: $framework_name"
  lipo "$source_binary" -verify_arch "$architecture" || \
    die "$framework_name does not support $architecture"
  mkdir -p "$destination_framework/Versions/A"
  lipo "$source_binary" -thin "$architecture" \
    -output "$destination_framework/Versions/A/$framework_name"
  chmod 755 "$destination_framework/Versions/A/$framework_name"
  if [[ -d "$source_framework/Versions/A/Resources" ]]; then
    ditto "$source_framework/Versions/A/Resources" \
      "$destination_framework/Versions/A/Resources"
  fi
  ln -s A "$destination_framework/Versions/Current"
  ln -s "Versions/Current/$framework_name" \
    "$destination_framework/$framework_name"
  ln -s Versions/Current/Resources "$destination_framework/Resources"
done

license_dir="$stage_app/Contents/Resources/THIRD_PARTY_LICENSES"
mkdir -p "$license_dir"
install -m 644 "$LICENSE" "$license_dir/JAMULUS_COPYING.txt"
install -m 644 "$PATCH" "$license_dir/$PATCH_NAME"
install -m 644 "$SOURCE_OFFER" \
  "$license_dir/JamulusHeadlessClient-SOURCE-OFFER.txt"
install -m 644 "$BUILD_INSTRUCTIONS" \
  "$license_dir/JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt"
install -m 644 "$corresponding_source" \
  "$license_dir/JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz"
install -m 644 "$QT_NOTICE" \
  "$license_dir/JamulusHeadlessClient-QT-NOTICE.txt"
install -m 644 "$qt_source_archive" \
  "$license_dir/$QT_SOURCE_ARCHIVE_NAME"
patch_sha="$(shasum -a 256 "$PATCH" | awk '{print $1}')"
license_sha="$(shasum -a 256 "$LICENSE" | awk '{print $1}')"
apple_clang_version="$(xcrun clang --version | sed -n '1p')"
macos_sdk_version="$(xcrun --sdk macosx --show-sdk-version)"
[[ -n "$apple_clang_version" && -n "$macos_sdk_version" ]] || \
  die "Apple clang or macOS SDK version could not be recorded"
{
  printf '%s\n' \
    'format=1' \
    'component=JamulusHeadlessClient' \
    "version=$VERSION" \
    "profile=$profile" \
    "source_repository=$SOURCE_REPOSITORY" \
    "source_commit=$SOURCE_COMMIT" \
    "source_tag=$SOURCE_TAG" \
    "source_tree=$source_tree" \
    "source_archive_commit=$source_archive_commit" \
    "corresponding_source_sha256=$corresponding_source_sha" \
    "patch_sha256=$patch_sha" \
    "license_sha256=$license_sha" \
    "qt_version=$QT_VERSION" \
    "qt_source_archive_sha256=$actual_qt_source_sha" \
    "aqtinstall_version=$AQTINSTALL_VERSION" \
    "architecture=$architecture" \
    "deployment_target=$DEPLOYMENT_TARGET" \
    "apple_clang_version=$apple_clang_version" \
    "macos_sdk_version=$macos_sdk_version" \
    'build_mode=headless-client' \
    'server_only=false'
} > "$license_dir/JamulusHeadlessClient-PROVENANCE.txt"

# Ad-hoc signing is deliberate for private candidates. The protected release
# rehearsal later replaces this signature inside-out with Developer ID.
codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$stage_app"
codesign --verify --deep --strict "$stage_app"
ditto "$stage_app" "$output_app"
binary="$output_app/Contents/MacOS/$EXECUTABLE_NAME"
sha="$(shasum -a 256 "$binary" | awk '{print $1}')"
printf '%s  %s\n' "$sha" \
  "$APP_NAME/Contents/MacOS/$EXECUTABLE_NAME" > "$manifest"

"$VERIFY" "$output_app" "$architecture" "$manifest" "$profile"
