#!/bin/bash
# Manual opt-in only. This script is not invoked by tests or installation hooks.
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release --arch arm64
binary_dir="$(swift build -c release --arch arm64 --show-bin-path)"
app_dir="$PWD/dist/ScreenPilot.app"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources"
cp "$binary_dir/ScreenPilot" "$app_dir/Contents/MacOS/ScreenPilot"
cp Resources/Info.plist "$app_dir/Contents/Info.plist"
codesign --force --sign "${SCREENPILOT_SIGN_IDENTITY:--}" "$app_dir"
printf '%s\n' "$app_dir"
