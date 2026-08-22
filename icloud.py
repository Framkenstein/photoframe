"""Read public iCloud shared albums.

Apple exposes shared albums through a small JSON API rather than embedding the
data in the page, so this is a real API client rather than a scrape:

  POST https://p<NN>-sharedstreams.icloud.com/<token>/sharedstreams/webstream
       -> album name and every photo's metadata
  POST .../webasseturls
       -> short-lived signed URLs for the photos you ask about

Two things shape the design:

* The partition host is not knowable up front. The first request may answer
  330 and name the correct host, which we then use.
* Asset URLs expire after about an hour, so they cannot be cached in
  photos.json like Google's. Photos are stored as a checksum and resolved to a
  fresh URL at display time (see the /icloud route in server.py).
"""

import re
import requests

FIRST_HOST = "p04-sharedstreams.icloud.com"
TIMEOUT = 30

UA = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# https://www.icloud.com/sharedalbum/#B0Abc123     (token after the fragment)
# https://www.icloud.com/sharedalbum/en-us/#B0Abc123
# https://share.icloud.com/photos/0Abc123
TOKEN_RE = re.compile(
    r"(?:icloud\.com/sharedalbum/(?:[a-z-]+/)?#|share\.icloud\.com/photos/)"
    r"([A-Za-z0-9_-]{8,})",
    re.I,
)


def looks_like_icloud_url(url):
    return bool(TOKEN_RE.search(url.strip()))


def extract_token(url):
    m = TOKEN_RE.search(url.strip())
    if not m:
        raise ValueError("Not an iCloud shared album link")
    return m.group(1)


def _post(host, token, endpoint, payload):
    return requests.post(
        f"https://{host}/{token}/sharedstreams/{endpoint}",
        json=payload,
        headers={"User-Agent": UA, "Content-Type": "text/plain"},
        timeout=TIMEOUT,
    )


def _post_following_redirect(token, endpoint, payload, host=FIRST_HOST):
    """POST, and if Apple points us at a different partition, follow it once."""
    resp = _post(host, token, endpoint, payload)
    if resp.status_code == 330:
        new_host = resp.headers.get("X-Apple-MMe-Host")
        if not new_host:
            try:
                new_host = resp.json().get("X-Apple-MMe-Host")
            except ValueError:
                new_host = None
        if new_host:
            resp = _post(new_host, token, endpoint, payload)
    resp.raise_for_status()
    return resp


def fetch_album(url):
    """Return (album_name, [photo dicts]) for a public iCloud shared album."""
    token = extract_token(url)
    data = _post_following_redirect(token, "webstream", {"streamCtag": None}).json()

    title = data.get("streamName") or "iCloud shared album"
    photos = []

    for item in data.get("photos", []):
        derivatives = item.get("derivatives") or {}
        best = None
        for d in derivatives.values():
            try:
                w, h = int(d.get("width") or 0), int(d.get("height") or 0)
            except (TypeError, ValueError):
                continue
            if not d.get("checksum") or w == 0 or h == 0:
                continue
            if best is None or w * h > best["w"] * best["h"]:
                best = {"checksum": d["checksum"], "w": w, "h": h}
        if best:
            best["guid"] = item.get("photoGuid")
            photos.append(best)

    return title, photos, token


def resolve_urls(token, checksums):
    """Map checksum -> a freshly signed URL. Apple caps each request, so batch."""
    out = {}
    checksums = [c for c in checksums if c]
    for i in range(0, len(checksums), 100):
        batch = checksums[i:i + 100]
        data = _post_following_redirect(
            token, "webasseturls", {"photoGuids": batch}
        ).json()
        for checksum, info in (data.get("items") or {}).items():
            location = info.get("url_location")
            path = info.get("url_path")
            if location and path:
                out[checksum] = f"https://{location}{path}"
    return out


def resolve_one(token, checksum):
    """A single fresh URL, or None if Apple no longer knows this photo."""
    return resolve_urls(token, [checksum]).get(checksum)
