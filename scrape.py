"""Pull the photo list out of public Google Photos shared albums.

Google removed the Library API read scopes in March 2025, so there is no
official way to list a shared album. This reads the public share page and
picks the image URLs out of the JSON blob Google embeds in it. It works, but
it depends on Google's page layout -- if a refresh suddenly finds 0 photos in
an album that used to work, that is the likely cause.

Nothing is downloaded. We only keep the URLs; the display fetches each image
from Google when it shows it.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
ALBUMS_FILE = BASE / "albums.txt"
PHOTOS_FILE = BASE / "photos.json"

# Pretend to be a normal browser -- Google serves a stripped page otherwise.
UA = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Matches  "https://lh3.googleusercontent.com/pw/<id>",<width>,<height>
PHOTO_RE = re.compile(
    r'"(https://lh3\.googleusercontent\.com/pw/[A-Za-z0-9_\-]{20,})"\s*,\s*(\d+)\s*,\s*(\d+)'
)

# Album titles show up in a couple of places; this is best-effort only.
TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')

MIN_DIMENSION = 300  # skip avatars, icons and thumbnails


def read_album_urls():
    if not ALBUMS_FILE.exists():
        return []
    urls = []
    for line in ALBUMS_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def scrape_album(url):
    """Return (title, [photo dicts]) for one shared album URL."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    html = resp.text

    title_match = TITLE_RE.search(html)
    title = title_match.group(1) if title_match else "Shared album"

    photos = {}
    for base_url, width, height in PHOTO_RE.findall(html):
        width, height = int(width), int(height)
        if width < MIN_DIMENSION or height < MIN_DIMENSION:
            continue
        # Same photo can appear more than once in the blob; keep the largest.
        prev = photos.get(base_url)
        if prev is None or width * height > prev["w"] * prev["h"]:
            photos[base_url] = {
                "url": base_url,
                "w": width,
                "h": height,
                "album": title,
            }
    return title, list(photos.values())


def stable_order(photos):
    """Shuffle deterministically so albums interleave instead of clumping.

    Ordering by a hash of the URL means the sequence stays put across
    refreshes, and photos added later slot in without reshuffling the rest.
    """
    return sorted(photos, key=lambda p: hashlib.sha1(p["url"].encode()).hexdigest())


def refresh():
    album_urls = read_album_urls()
    if not album_urls:
        print("No album links in albums.txt yet -- nothing to fetch.")
        return {"photos": [], "albums": []}

    all_photos = {}
    album_report = []

    for url in album_urls:
        try:
            title, photos = scrape_album(url)
        except Exception as exc:  # one bad album must not sink the rest
            print(f"  FAILED  {url}\n          {type(exc).__name__}: {exc}")
            album_report.append({"url": url, "title": None, "count": 0, "ok": False})
            continue

        for photo in photos:
            all_photos[photo["url"]] = photo

        state = "ok" if photos else "no photos found -- is the link public?"
        print(f"  {len(photos):5d}  {title}  ({state})")
        album_report.append(
            {"url": url, "title": title, "count": len(photos), "ok": bool(photos)}
        )

    ordered = stable_order(list(all_photos.values()))
    data = {"photos": ordered, "albums": album_report}
    PHOTOS_FILE.write_text(json.dumps(data, indent=1))
    print(f"\nTotal: {len(ordered)} photos from {len(album_urls)} album link(s).")
    return data


def load():
    if PHOTOS_FILE.exists():
        try:
            return json.loads(PHOTOS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"photos": [], "albums": []}


if __name__ == "__main__":
    result = refresh()
    sys.exit(0 if result["photos"] else 1)
