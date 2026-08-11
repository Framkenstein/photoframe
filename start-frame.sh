#!/bin/bash
# Open the photo frame fullscreen on this machine's display.
#
# Keeps the screen awake only while the frame is running, and puts the normal
# blanking behaviour back on exit.

set -u

FRAME_URL="http://localhost:8081"
WAYFIRE_INI="$HOME/.config/wayfire.ini"
MARKER="# --- photoframe keep-awake (auto-added, removed on exit) ---"
PROFILE="$HOME/.config/photoframe-kiosk"
BASE="$(cd "$(dirname "$0")" && pwd)"

# Launched from a Desktop button there is no terminal to print to.
if [ ! -t 1 ]; then
  exec >>"$BASE/frame.log" 2>&1
fi

log() { echo "[$(date '+%F %T')] $*"; }

restore_blanking() {
  if [ -f "$WAYFIRE_INI" ] && grep -qF "$MARKER" "$WAYFIRE_INI"; then
    sed -i "/$(printf '%s' "$MARKER" | sed 's/[][\.*^$/]/\\&/g')/,\$d" "$WAYFIRE_INI"
    log "screen blanking restored"
  fi
}
trap restore_blanking EXIT INT TERM

# 1. Make sure the frame service is up.
if ! curl -fsS -o /dev/null --max-time 3 "$FRAME_URL/api/status" 2>/dev/null; then
  log "starting photoframe service..."
  systemctl --user start photoframe.service 2>/dev/null || {
    log "no systemd service found, starting server directly"
    (cd "$BASE" && setsid python3 server.py >>"$BASE/server.log" 2>&1 &)
  }
fi

log "waiting for $FRAME_URL ..."
for i in $(seq 1 45); do
  curl -fsS -o /dev/null --max-time 2 "$FRAME_URL/api/status" 2>/dev/null && break
  if [ "$i" -eq 45 ]; then
    log "ERROR: frame service never came up. Try: python3 $BASE/server.py"
    exit 1
  fi
  sleep 2
done
log "service is up"

# 2. Stop the screen blanking. Wayfire reloads this file live.
restore_blanking
{
  echo ""
  echo "$MARKER"
  echo "[idle]"
  echo "dpms_timeout = -1"
  echo "screensaver_timeout = -1"
  echo "disable_on_fullscreen = true"
} >> "$WAYFIRE_INI"
log "screen blanking disabled"

# 3. Fullscreen browser, in its own profile so normal browsing is untouched.
chromium-browser \
  --kiosk \
  --user-data-dir="$PROFILE" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=Translate,TranslateUI \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  --start-fullscreen \
  "$FRAME_URL"

# trap restores blanking on the way out
