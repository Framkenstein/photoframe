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
  # Never leave browser processes behind: an orphan holding the kiosk profile
  # makes the next launch hand off its URL and exit, which looks like the
  # Desktop button doing nothing.
  local pids
  pids=$(kiosk_pids)
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    sleep 1
    pids=$(kiosk_pids)
    # shellcheck disable=SC2086
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  fi
}
# Only armed once we hold the lock, so a duplicate launch can never trigger it.
trap cleanup EXIT INT TERM

# 0. Clear any orphaned browser still holding the kiosk profile.
#    We hold the lock, so no legitimate launcher is running -- anything still
#    on our profile is left over from a run that was killed. Without this,
#    Chromium hands the URL to that orphan and exits immediately, which looks
#    like the button doing nothing at all.

# Only ever match real browser processes. A bare `pkill -f` on the profile path
# would also match any shell whose command line merely mentions it -- including
# the one running this script.
kiosk_pids() {
  local pid comm
  for pid in $(pgrep -f -- "--user-data-dir=$PROFILE" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    comm=$(cat "/proc/$pid/comm" 2>/dev/null) || continue
    case "$comm" in chromium*|chrome*) echo "$pid" ;; esac
  done
}

if [ -n "$(kiosk_pids)" ]; then
  log "clearing orphaned kiosk browser"
  # shellcheck disable=SC2046
  kill $(kiosk_pids) 2>/dev/null
  for _ in $(seq 1 12); do
    [ -z "$(kiosk_pids)" ] && break
    sleep 0.5
  done
  if [ -n "$(kiosk_pids)" ]; then
    kill -9 $(kiosk_pids) 2>/dev/null
    sleep 1
  fi
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
#
#    `9>&-` closes the lock file descriptor for the browser. Without it Chromium
#    inherits fd 9 and keeps holding the flock after this script exits, so every
#    later launch is refused with "already running" and the Desktop button stops
#    working entirely.
#
#    Chromium's own noise (missing UPower, no /dev/video10) goes to its own log
#    rather than frame.log, which stays readable.
# Wayfire does not reliably honour Chromium's kiosk fullscreen request -- it
# leaves the window at its saved size (maximized:false) so the frame ends up as
# an ordinary window nobody can see. Size it to the output explicitly as well.
SCREEN=$(wlr-randr 2>/dev/null | awk '/\(current\)/ {print $1; exit}')
SCREEN_W=${SCREEN%%x*}
SCREEN_H=${SCREEN##*x}
case "${SCREEN_W:-}${SCREEN_H:-}" in
  ''|*[!0-9]*) SCREEN_W=1920; SCREEN_H=1080 ;;
esac
log "sizing frame to ${SCREEN_W}x${SCREEN_H}"

log "opening frame (press Esc to exit)"
chromium-browser \
  --kiosk \
  --start-fullscreen \
  --window-position=0,0 \
  --window-size="${SCREEN_W},${SCREEN_H}" \
  --user-data-dir="$PROFILE" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=Translate,TranslateUI \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  "$FRAME_URL/frame" \
  > "$BASE/chromium.log" 2>&1 9>&- &

KIOSK_PID=$!
# Escape in the browser posts to /api/quit, which stops this PID.
echo "$KIOSK_PID" > "$KIOSK_PID_FILE"

# Chromium's --kiosk and --start-fullscreen are not honoured on this Wayfire
# setup -- the window opens at an ordinary size. F11 does work, so press it once
# the window has had time to map. This only lands if the frame has keyboard
# focus, which is true when it starts with the session (see [autostart] in
# wayfire.ini) but not when it is launched into a busy desktop.
if command -v wtype >/dev/null 2>&1; then
  ( sleep 8; wtype -k F11 2>/dev/null && log "sent F11 to fullscreen the frame" ) &
fi

wait "$KIOSK_PID"
log "frame closed"
# trap restores blanking on the way out
