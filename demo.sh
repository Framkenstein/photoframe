#!/bin/bash
# Offline demo mode: show bundled sample photos instead of your Google albums.
# Useful where there is no internet (meetups, conference stands).
#
#   ./demo.sh on        show the sample photos
#   ./demo.sh off       go back to your albums
#   ./demo.sh status    which mode is active
#   ./demo.sh download  fetch the sample photos (done automatically by "on")

set -eu
BASE="$(cd "$(dirname "$0")" && pwd)"
DEMO="$BASE/demo"
FLAG="$DEMO/enabled"
COUNT=24

restart() {
  rm -f "$BASE/state.json"          # the photo set changed
  systemctl --user restart photoframe.service 2>/dev/null || true
  sleep 2
}

download() {
  mkdir -p "$DEMO"
  echo "Downloading $COUNT sample photos..."
  local n=0
  for i in $(seq 1 16); do
    curl -fsSL --max-time 30 -o "$DEMO/land_$i.jpg" "https://picsum.photos/1920/1080?random=$i" && n=$((n+1)) || true
  done
  for i in $(seq 1 8); do
    curl -fsSL --max-time 30 -o "$DEMO/port_$i.jpg" "https://picsum.photos/1080/1920?random=$((100+i))" && n=$((n+1)) || true
  done
  echo "  got $n photos"
}

have_photos() { ls "$DEMO"/*.jpg >/dev/null 2>&1; }

case "${1:-status}" in
  on)
    have_photos || download
    have_photos || { echo "No demo photos and none could be downloaded. Run this with internet once." >&2; exit 1; }
    touch "$FLAG"; restart
    echo "Demo mode ON - $(ls "$DEMO"/*.jpg | wc -l) sample photos, no internet needed."
    ;;
  off)
    rm -f "$FLAG"; restart
    echo "Demo mode OFF - back to your Google Photos albums."
    ;;
  download) download ;;
  status)
    if [ -f "$FLAG" ]; then echo "Demo mode is ON ($(ls "$DEMO"/*.jpg 2>/dev/null | wc -l) sample photos)"
    else echo "Demo mode is OFF (using your Google albums)"; fi
    ;;
  *) echo "Usage: $0 {on|off|status|download}" >&2; exit 1 ;;
esac
