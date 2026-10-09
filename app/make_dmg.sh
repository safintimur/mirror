#!/bin/zsh
# Builds build/Mirror-<version>.dmg: Mirror.app + a link to /Applications on a styled Finder window.
# Finder lays out the window (icon positions, background), so the image is mounted briefly.
set -euo pipefail
cd "${0:A:h}"
./build.sh >/dev/null
APP=build/Mirror.app
VERSION=$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")
VOL=Mirror
OUT=build/Mirror-$VERSION.dmg
STAGE=build/dmg-stage
RW=build/dmg-rw.dmg
[[ -d /Volumes/$VOL ]] && { echo "/Volumes/$VOL is already mounted: eject it first" >&2; exit 1; }

rm -rf "$STAGE" "$RW" "$OUT"
mkdir -p "$STAGE/.background"
ditto "$APP" "$STAGE/Mirror.app"
ln -s /Applications "$STAGE/Applications"
cp Resources/dmg/background.tiff "$STAGE/.background/background.tiff"

hdiutil create -volname "$VOL" -srcfolder "$STAGE" -fs HFS+ -format UDRW -size 40m -ov "$RW" >/dev/null
DEV=$(hdiutil attach "$RW" -readwrite -noverify -noautoopen | awk '/Apple_HFS/ {print $1}')
trap 'hdiutil detach "$DEV" -quiet 2>/dev/null || true' EXIT

osascript <<OSA
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 860, 592}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 128
    set text size of opts to 13
    set background picture of opts to file ".background:background.tiff"
    set position of item "Mirror.app" of container window to {180, 200}
    set position of item "Applications" of container window to {480, 200}
    update without registering applications
    delay 1
    close
  end tell
end tell
OSA

# volume icon last: hdiutil -srcfolder drops .VolumeIcon.icns and Finder's layout pass clears it again
cp "$APP/Contents/Resources/Mirror.icns" "/Volumes/$VOL/.VolumeIcon.icns"
SetFile -a C "/Volumes/$VOL"
sync
hdiutil detach "$DEV" -quiet
trap - EXIT
hdiutil convert "$RW" -format ULFO -o "$OUT" >/dev/null
rm -rf "$STAGE" "$RW"
echo "$PWD/$OUT"
