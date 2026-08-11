"""Photo frame server.

Shows one photo per day from your Google Photos shared albums, full screen.
Space bar (or a tap/click) jumps to the next one immediately.

Runs on http://localhost:8081
"""

import json
import os
import signal
import threading
import time
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import scrape

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "state.json"
# Written by start-frame.sh so Escape can stop the browser it launched.
KIOSK_PID_FILE = BASE / ".kiosk.pid"

# How often to re-read the albums: picks up newly added photos and refreshes
# the image URLs before Google rotates them.
REFRESH_EVERY_SECONDS = 6 * 60 * 60

app = Flask(__name__, static_folder=None)

_lock = threading.Lock()


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"current_url": None, "day": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1))


def _index_of(photos, url):
    for i, photo in enumerate(photos):
        if photo["url"] == url:
            return i
    return None


def _pick(advance):
    """Return the photo to display. Advances if asked, or if the day rolled over.

    Keeping the current photo's URL (rather than an index) means a refresh that
    adds or removes photos never knocks the frame onto a random image.
    """
    photos = scrape.load()["photos"]
    if not photos:
        return None

    state = load_state()
    today = date.today().isoformat()
    index = _index_of(photos, state.get("current_url"))

    if index is None:
        # First run, or the current photo vanished from the albums. Land on the
        # first photo and treat today as already spent -- otherwise the day
        # rollover below would immediately skip past it.
        index = 0
        changed = True
    else:
        changed = False
        if advance or state.get("day") != today:
            index = (index + 1) % len(photos)
            changed = True

    if changed:
        save_state({"current_url": photos[index]["url"], "day": today})

    photo = dict(photos[index])
    photo["position"] = index + 1
    photo["total"] = len(photos)
    return photo


@app.after_request
def no_cache(response):
    """Never let the browser cache the frame itself.

    A kiosk that caches its own HTML keeps running whatever code it saw first,
    so a fix on disk never reaches the screen until the profile is wiped.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    # First run with nothing configured yet -> send people to setup instead of
    # a black screen.
    if not scrape.read_album_urls():
        return send_from_directory(BASE / "static", "setup.html")
    return send_from_directory(BASE / "static", "index.html")


@app.route("/frame")
def frame():
    return send_from_directory(BASE / "static", "index.html")


@app.route("/setup")
def setup():
    return send_from_directory(BASE / "static", "setup.html")


@app.route("/api/albums", methods=["GET"])
def api_albums_get():
    return jsonify({"albums": scrape.read_album_urls()})


@app.route("/api/albums", methods=["POST"])
def api_albums_post():
    """Save the links from the setup screen, then fetch them straight away."""
    payload = request.get_json(silent=True) or {}
    submitted = payload.get("albums", [])
    if not isinstance(submitted, list):
        return jsonify({"error": "albums must be a list"}), 400

    bad = [u for u in submitted if u.strip() and not scrape.looks_like_album_url(u)]
    if bad:
        return (
            jsonify(
                {
                    "error": "Those do not look like Google Photos album links.",
                    "invalid": bad,
                }
            ),
            400,
        )

    with _lock:
        saved = scrape.write_album_urls(submitted)
        if not saved:
            return jsonify({"error": "Add at least one album link."}), 400
        data = scrape.refresh()
        # A brand new album list means the remembered photo is meaningless.
        if STATE_FILE.exists():
            STATE_FILE.unlink()

    return jsonify(
        {"photo_count": len(data["photos"]), "albums": data.get("albums", [])}
    )


@app.route("/api/photo")
def api_photo():
    with _lock:
        photo = _pick(advance=False)
    return jsonify({"photo": photo, "day": date.today().isoformat()})


@app.route("/api/next", methods=["POST", "GET"])
def api_next():
    with _lock:
        photo = _pick(advance=True)
    return jsonify({"photo": photo, "day": date.today().isoformat()})


@app.route("/api/status")
def api_status():
    data = scrape.load()
    return jsonify(
        {
            "photo_count": len(data["photos"]),
            "albums": data.get("albums", []),
            "state": load_state(),
        }
    )


@app.route("/api/quit", methods=["POST"])
def api_quit():
    """Close the fullscreen frame.

    Chromium's kiosk mode ignores Escape, and a page cannot close a window it
    did not open, so the key press comes here instead and we stop the browser
    the launcher started. The launcher records its PID on the way up.
    """
    if not KIOSK_PID_FILE.exists():
        return jsonify({"stopped": False, "reason": "not running as a kiosk"}), 409

    try:
        pid = int(KIOSK_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return jsonify({"stopped": False, "reason": "bad pid file"}), 500

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        KIOSK_PID_FILE.unlink(missing_ok=True)
        return jsonify({"stopped": False, "reason": "already gone"}), 409
    except PermissionError:
        return jsonify({"stopped": False, "reason": "not permitted"}), 403

    return jsonify({"stopped": True})


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    with _lock:
        data = scrape.refresh()
    return jsonify({"photo_count": len(data["photos"]), "albums": data.get("albums", [])})


def refresh_loop():
    """Re-read the albums periodically, in the background."""
    while True:
        time.sleep(REFRESH_EVERY_SECONDS)
        try:
            with _lock:
                scrape.refresh()
        except Exception as exc:
            print(f"[refresh] failed: {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    # Fetch once at startup if we have never scraped, so the frame is not blank.
    if not scrape.load()["photos"] and scrape.read_album_urls():
        try:
            scrape.refresh()
        except Exception as exc:
            print(f"[startup] refresh failed: {type(exc).__name__}: {exc}", flush=True)

    threading.Thread(target=refresh_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=8081, threaded=True)
