#!/bin/sh
# Installs a macOS launchd job that snapshots every tracked artist daily at 09:00.
# Growth/momentum scores depend on these daily snapshots. Remove with:
#   launchctl unload ~/Library/LaunchAgents/com.ar-scout.snapshot.plist && rm ~/Library/LaunchAgents/com.ar-scout.snapshot.plist
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.ar-scout.snapshot.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ar-scout.snapshot</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/ar-scout</string><string>snapshot</string><string>all</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DIR/data/snapshot.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/snapshot.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed: daily snapshot at 09:00 -> $DIR/data/snapshot.log"
