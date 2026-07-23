#!/bin/zsh

set -euo pipefail

script_dir="${0:A:h}"
project="$script_dir/WebJamPocketStage.xcodeproj"
readme="$script_dir/READ ME FIRST.txt"
xcode_app="/Applications/Xcode.app"

pause_and_fail() {
  print -u2 -- ""
  print -u2 -- "$1"
  print -u2 -- ""
  read "?Press Return to close this window..." || true
  exit 1
}

if [[ "$(/usr/bin/uname -s)" != "Darwin" ]]; then
  pause_and_fail "Pocket Stage must be installed from a Mac running Xcode."
fi

if [[ ! -d "$project" || -L "$project" ]]; then
  pause_and_fail "The included Pocket Stage Xcode project is missing or unsafe. Download a fresh WebJam Mac candidate."
fi

if [[ ! -f "$project/project.pbxproj" || -L "$project/project.pbxproj" ]]; then
  pause_and_fail "The included Pocket Stage Xcode project is incomplete. Download a fresh WebJam Mac candidate."
fi

if [[ ! -d "$xcode_app" || -L "$xcode_app" ]]; then
  pause_and_fail "Install the full Xcode app from the Mac App Store, open it once, then run this helper again."
fi

if ! DEVELOPER_DIR="$xcode_app/Contents/Developer" \
  /usr/bin/xcodebuild -version >/dev/null 2>&1; then
  pause_and_fail "Open Xcode once and complete any requested component installation, then run this helper again."
fi

print -- "Opening the included Pocket Stage project in Xcode."
print -- ""
print -- "In Xcode:"
print -- "  1. Select the PocketStage target, then Signing & Capabilities."
print -- "  2. Choose your free Personal Team and enter a unique bundle identifier."
print -- "  3. Connect and trust your iPhone, choose it as the destination, and press Run."
print -- ""
print -- "A paid Apple Developer Program membership is not required for this owner-device test."

if [[ -f "$readme" && ! -L "$readme" ]]; then
  /usr/bin/open -a TextEdit "$readme" >/dev/null 2>&1 || true
fi
/usr/bin/open -a "$xcode_app" "$project"
