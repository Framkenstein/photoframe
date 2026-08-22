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
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory

import icloud
import scrape

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "state.json"
# Written by start-frame.sh so Escape can stop the browser it launched.
KIOSK_PID_FILE = BASE / ".kiosk.pid"

# How often to re-read the albums: picks up newly added photos and refreshes
# the image URLs before Google rotates them.
REFRESH_EVERY_SECONDS = 6 * 60 * 60

# How often the photo changes on its own. "%Y-%m-%dT%H" = a new photo each
# hour; use "%Y-%m-%d" for one a day.
PERIOD_FORMAT = "%Y-%m-%dT%H"


def current_period():
    """The current slot. When this string changes, the photo advances."""
    return datetime.now().strftime(PERIOD_FORMAT)

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


def photo_list():
    """Where photos come from, most specific first.

    Offline albums win when enabled, so the frame keeps working with no
    internet at all.
    """
    if scrape.offline_enabled():
        cached = scrape.offline_photos()
        if cached:
            return cached
    if scrape.demo_enabled():
        return scrape.demo_photos()
    return scrape.load()["photos"]


def _pick(advance):
    """Return the photo to display. Advances if asked, or if the day rolled over.

    Keeping the current photo's URL (rather than an index) means a refresh that
    adds or removes photos never knocks the frame onto a random image.
    """
    photos = photo_list()
    if not photos:
        return None

    state = load_state()
    today = current_period()
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
    if not scrape.demo_enabled() and not scrape.read_album_urls():
        return send_from_directory(BASE / "static", "setup.html")
    return send_from_directory(BASE / "static", "index.html")


# Apple's signed asset URLs last about an hour, so resolve them on demand and
# keep each one only briefly. The frame changes photo hourly, so this is a
# handful of requests a day.
_icloud_cache = {}
_ICLOUD_TTL = 40 * 60


@app.route("/icloud/<token>/<checksum>")
def icloud_photo(token, checksum):
    now = time.time()
    hit = _icloud_cache.get((token, checksum))
    if hit and hit[1] > now:
        return redirect(hit[0], code=302)

    try:
        url = icloud.resolve_one(token, checksum)
    except Exception as exc:
        return jsonify({"error": f"iCloud lookup failed: {exc}"}), 502
    if not url:
        return jsonify({"error": "photo not found in that album"}), 404

    _icloud_cache[(token, checksum)] = (url, now + _ICLOUD_TTL)
    if len(_icloud_cache) > 5000:                     # keep it from growing forever
        for k, v in list(_icloud_cache.items()):
            if v[1] <= now:
                _icloud_cache.pop(k, None)
    return redirect(url, code=302)


@app.route("/offline/<key>/<name>")
def offline_file(key, name):
    return send_from_directory(scrape.OFFLINE_DIR / key, name)


@app.route("/demo/<path:name>")
def demo_file(name):
    return send_from_directory(BASE / "demo", name)


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
                    "error": "Those do not look like Google Photos or iCloud shared album links.",
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


# Album downloads run in the background; the setup page polls for progress.
_downloads = {}


def _album_report():
    # Titles for albums that are not cached yet come from the last scrape, so
    # the list reads as album names rather than a column of URLs.
    known = {}
    for a in scrape.load().get("albums", []):
        if a.get("title"):
            known[a["url"]] = a["title"]

    out = []
    for url in scrape.read_album_urls():
        count, size, title = scrape.album_cache_info(url)
        title = title or known.get(url)
        job = _downloads.get(url)
        out.append({
            "url": url,
            "title": title,
            "cached": count,
            "bytes": size,
            "job": job,
        })
    return out


@app.route("/api/offline")
def api_offline():
    return jsonify({"enabled": scrape.offline_enabled(), "albums": _album_report()})


@app.route("/api/offline/mode", methods=["POST"])
def api_offline_mode():
    want = bool((request.get_json(silent=True) or {}).get("enabled"))
    if want and not scrape.offline_photos():
        return jsonify({"error": "Download at least one album first."}), 400
    scrape.set_offline(want)
    STATE_FILE.unlink(missing_ok=True)     # the photo set changed
    return jsonify({"enabled": scrape.offline_enabled()})


def _run_download(url):
    def progress(done, total):
        _downloads[url] = {"state": "running", "done": done, "total": total}
    try:
        progress(0, 0)
        result = scrape.download_album(url, progress)
        _downloads[url] = {
            "state": "done",
            "done": result["downloaded"],
            "total": result["total"],
        }
    except Exception as exc:
        _downloads[url] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}


@app.route("/api/offline/download", methods=["POST"])
def api_offline_download():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if url not in scrape.read_album_urls():
        return jsonify({"error": "unknown album"}), 400
    job = _downloads.get(url)
    if job and job.get("state") == "running":
        return jsonify({"state": "already running"}), 202
    threading.Thread(target=_run_download, args=(url,), daemon=True).start()
    return jsonify({"state": "started"}), 202


@app.route("/api/offline/delete", methods=["POST"])
def api_offline_delete():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    scrape.delete_album_cache(url)
    _downloads.pop(url, None)
    if not scrape.offline_photos():
        scrape.set_offline(False)          # nothing left to show offline
    STATE_FILE.unlink(missing_ok=True)
    return jsonify({"ok": True, "enabled": scrape.offline_enabled()})


@app.route("/api/photo")
def api_photo():
    with _lock:
        photo = _pick(advance=False)
    return jsonify({"photo": photo, "day": current_period()})


@app.route("/api/next", methods=["POST", "GET"])
def api_next():
    with _lock:
        photo = _pick(advance=True)
    return jsonify({"photo": photo, "day": current_period()})


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
