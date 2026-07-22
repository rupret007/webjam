#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"

pause_and_fail() {
  print -u2 -- ""
  print -u2 -- "$1"
  print -u2 -- ""
  read "?Press Return to close this window..." || true
  exit 1
}

if ! /usr/bin/xcodebuild -version >/dev/null 2>&1; then
  pause_and_fail "Install the full Xcode app from Apple, open it once, then run this helper again."
fi

if ! command -v xcodegen >/dev/null 2>&1; then
  pause_and_fail "XcodeGen 2.45.4 or newer is required. Install it with: brew install xcodegen"
fi

installed_version="$(xcodegen version 2>/dev/null | /usr/bin/awk '{print $NF}')"
if [[ -z "$installed_version" ]]; then
  pause_and_fail "WebJam could not determine the installed XcodeGen version."
fi

cd "$script_dir"
xcodegen generate --spec project.yml

project="$script_dir/WebJamPocketStage.xcodeproj"
if [[ ! -d "$project" ]]; then
  pause_and_fail "XcodeGen did not create the Pocket Stage project."
fi

print -- "Pocket Stage is ready to open in Xcode."
print -- "Select your Personal Team and a unique bundle identifier, connect your iPhone, then press Run."
/usr/bin/open "$project"
