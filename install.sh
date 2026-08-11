#!/bin/bash
# Set up the photo frame on this machine:
#   - install Python dependencies
#   - register a systemd user service so it runs 24/7 and survives reboots
#   - add a Desktop button that opens the frame fullscreen
#
# Safe to re-run.

set -eu

BASE="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

echo "==> Installing Python dependencies"
if ! python3 -c "import flask, requests" 2>/dev/null; then
  pip3 install --user -r "$BASE/requirements.txt" 2>/dev/null \
    || sudo apt-get install -y python3-flask python3-requests
else
  echo "    already present"
fi

echo "==> Setting up albums.txt"
if [ ! -f "$BASE/albums.txt" ]; then
  cp "$BASE/albums.example.txt" "$BASE/albums.txt"
  echo "    created $BASE/albums.txt -- add your shared album links to it"
else
  echo "    already exists, left alone"
fi

echo "==> Installing systemd user service"
mkdir -p "$HOME/.config/systemd/user"
sed "s|%h|$HOME|g" "$BASE/photoframe.service" > "$HOME/.config/systemd/user/photoframe.service"
systemctl --user daemon-reload
systemctl --user enable photoframe.service
systemctl --user restart photoframe.service
echo "    service enabled and started"

# Keep the service running even when nobody is logged in.
if command -v loginctl >/dev/null && [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "==> Enabling lingering so the frame survives logout (needs sudo)"
  sudo loginctl enable-linger "$USER" || echo "    skipped -- frame will still run while logged in"
fi

echo "==> Adding Desktop button"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/Photo Frame.desktop" <<EOF
[Desktop Entry]
Name=Photo Frame
Comment=Show my Google Photos albums fullscreen (Alt+F4 to exit)
Exec=$BASE/start-frame.sh
Icon=image-x-generic
Type=Application
Terminal=false
StartupNotify=true
Categories=Graphics;Viewer;
EOF
chmod +x "$DESKTOP_DIR/Photo Frame.desktop"
gio set "$DESKTOP_DIR/Photo Frame.desktop" metadata::trusted true 2>/dev/null || true
echo "    added to $DESKTOP_DIR"

echo
echo "Done."
echo
echo "Next:"
echo "  1. Put your Google Photos shared album links in: $BASE/albums.txt"
echo "  2. Run: $BASE/refresh.sh"
echo "  3. Click the 'Photo Frame' button on your Desktop"
