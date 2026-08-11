#!/bin/bash
# Open the photo frame fullscreen on this machine's display.
#
# Keeps the screen awake only while the frame is running, and puts the normal
# blanking behaviour back on exit.
#
# Exit the frame with Alt+F4.

set -u

FRAME_URL="http://localhost:8081"
WAYFIRE_INI="$HOME/.config/wayfire.ini"
MARKER="# --- photoframe keep-awake (auto-added, removed on exit) ---"
BLOCK_LINES=4          # lines we append after the marker line itself
PROFILE="$HOME/.config/photoframe-kiosk"
BASE="$(cd "$(dirname "$0")" && pwd)"
LOCKFILE="$BASE/.frame.lock"
KIOSK_PID_FILE="$BASE/.kiosk.pid"

# Launched from a Desktop button there is no terminal to print to.
if [ ! -t 1 ]; then
  exec >>"$BASE/frame.log" 2>&1
fi

log() { echo "[$(date '+%F %T')] $*"; }

# --- Only ever run one frame at a time -------------------------------------
# Clicking the Desktop button twice used to start a second copy. Chromium would
# hand the URL to the instance already holding the profile and exit straight
# away -- and that exit tore down the screen-awake setting out from under the
# frame that was still running. Take the lock before touching anything.
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  log "frame is already running -- ignoring this launch"
  exit 0
fi

restore_blanking() {
  if [ -f "$WAYFIRE_INI" ] && grep -qF "$MARKER" "$WAYFIRE_INI"; then
    # Delete exactly our own block -- the marker line plus the settings under
    # it. Deleting through to end-of-file would destroy anything the Pi's
    # display-settings GUI appended after us while the frame was running.
    escaped=$(printf '%s' "$MARKER" | sed 's/[][\.*^$/]/\\&/g')
    sed -i "/$escaped/,+${BLOCK_LINES}d" "$WAYFIRE_INI"
    log "screen blanking restored"
  fi
}
cleanup() {
  restore_blanking
  rm -f "$KIOSK_PID_FILE"
}
# Only armed once we hold the lock, so a duplicate launch can never trigger it.
trap cleanup EXIT INT TERM

# 0. Clear any orphaned browser still holding the kiosk profile.
#    We hold the lock, so no legitimate launcher is running -- anything still
#    on our profile is left over from a run that was killed. Without this,
#    Chromium hands the URL to that orphan and exits immediately, which looks
#    like the button doing nothing at all.
if pgrep -f -- "--user-data-dir=$PROFILE" >/dev/null 2>&1; then
  log "clearing orphaned kiosk browser"
  pkill -f -- "--user-data-dir=$PROFILE"
  for _ in $(seq 1 10); do
    pgrep -f -- "--user-data-dir=$PROFILE" >/dev/null 2>&1 || break
    sleep 0.5
  done
  pkill -9 -f -- "--user-data-dir=$PROFILE" 2>/dev/null
  sleep 1
fi

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
#    (restore first, in case a previous run was killed before it could clean up)
restore_blanking
{
  # No leading blank line: the block must be exactly BLOCK_LINES+1 lines so the
  # cleanup restores the file byte for byte. Otherwise every launch would leave
  # one more blank line behind.
  echo "$MARKER"
  echo "[idle]"
  echo "dpms_timeout = -1"
  echo "screensaver_timeout = -1"
  echo "disable_on_fullscreen = true"
} >> "$WAYFIRE_INI"
log "screen blanking disabled"

# 3. Fullscreen browser, in its own profile so normal browsing is untouched.
#    Chromium on the Pi is noisy about missing UPower/video devices; none of it
#    matters here, so keep it out of the log.
log "opening frame (press Esc to exit)"
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
  "$FRAME_URL/frame" \
  > >(grep -viE "UPower|v4l2_utils|gpu_init|close object|extension_registrar|object_proxy") 2>&1 &

KIOSK_PID=$!
# Escape in the browser posts to /api/quit, which stops this PID.
echo "$KIOSK_PID" > "$KIOSK_PID_FILE"

wait "$KIOSK_PID"
log "frame closed"
# trap restores blanking on the way out
