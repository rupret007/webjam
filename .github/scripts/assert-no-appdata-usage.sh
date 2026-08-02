#!/usr/bin/env bash
# Assert the app and its bundled Jamulus components declare no
# NSAppDataUsageDescription.
#
# Extracted from ci.yml because it appeared four times, and the two copies
# inside "Build desktop artifact" pushed that step past GitHub's 21,000
# character limit for a single expression. GitHub then refused the whole
# workflow before creating any job, which surfaces only as a run that fails
# in 0s with no logs.
set -euo pipefail

app="${1:?usage: assert-no-appdata-usage.sh <path-to-.app>}"

for bundle in \
  "$app" \
  "$app/Contents/Resources/Jamulus.app" \
  "$app/Contents/Resources/JamulusServer.app" \
  "$app/Contents/Resources/JamulusHeadlessClient.app"; do
  ! /usr/libexec/PlistBuddy \
    -c 'Print :NSAppDataUsageDescription' \
    "$bundle/Contents/Info.plist" \
    >/dev/null 2>&1
done
