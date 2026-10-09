#!/bin/zsh
# Builds build/Mirror.app (menu bar only, ad-hoc signed) with the engine bundled in Resources.
set -euo pipefail
cd "${0:A:h}"
swift build -c release
APP=build/Mirror.app
VERSION=0.2.0
BUILD=$(git rev-list --count HEAD 2>/dev/null || echo 1)
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/MirrorBar "$APP/Contents/MacOS/Mirror"
cp ../codex_mirror.py "$APP/Contents/Resources/"
cp ../LICENSE "$APP/Contents/Resources/"
# App icon: Icon Composer source -> Assets.car (macOS 26, light/dark) + Mirror.icns fallback
# (absolute paths: actool resolves relative ones against its background server's directory)
xcrun actool "$PWD/Resources/Mirror.icon" --compile "$PWD/$APP/Contents/Resources" --output-format human-readable-text \
  --errors --output-partial-info-plist "$PWD/.build/icon-partial.plist" --app-icon Mirror --include-all-app-icons \
  --platform macosx --minimum-deployment-target 26.0 --target-device mac >/dev/null
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.tim.codex-claude-mirror</string>
  <key>CFBundleName</key><string>Mirror</string>
  <key>CFBundleDisplayName</key><string>Mirror</string>
  <key>CFBundleExecutable</key><string>Mirror</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD</string>
  <key>CFBundleDevelopmentRegion</key><string>ru</string>
  <key>CFBundleLocalizations</key><array><string>ru</string></array>
  <key>NSHumanReadableCopyright</key><string>© 2026 Timur Safin</string>
  <key>CFBundleIconFile</key><string>Mirror</string>
  <key>CFBundleIconName</key><string>Mirror</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
codesign --force --deep --sign - "$APP" >/dev/null
echo "$PWD/$APP"
