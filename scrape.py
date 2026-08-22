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
import time
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


ALBUMS_HEADER = """\
# Google Photos shared album links, one per line.
# Managed by the setup screen at http://localhost:8081/setup
# You can also edit this file by hand.
"""


def write_album_urls(urls):
    """Replace albums.txt with this list of links."""
    cleaned = []
    for url in urls:
        url = url.strip()
        if url and not url.startswith("#") and url not in cleaned:
            cleaned.append(url)
    ALBUMS_FILE.write_text(ALBUMS_HEADER + "\n".join(cleaned) + "\n")
    return cleaned


def looks_like_album_url(url):
    """Cheap sanity check so obvious typos get caught before we fetch."""
    url = url.strip().lower()
    return url.startswith("http") and (
        "photos.app.goo.gl" in url or "photos.google.com" in url
    )


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



# --- Offline demo mode -------------------------------------------------------
# Lets the frame run with no internet at all (conference stands, demos).
# Photos live in demo/ and are served by our own server rather than Google.

DEMO_DIR = BASE / "demo"
DEMO_FLAG = DEMO_DIR / "enabled"


def demo_enabled():
    return DEMO_FLAG.exists()


def demo_photos():
    """Local demo images, ordered the same stable way as real ones."""
    if not DEMO_DIR.is_dir():
        return []
    photos = []
    for f in sorted(DEMO_DIR.iterdir()):
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            photos.append({
                "url": "/demo/" + f.name,
                "w": 0, "h": 0,
                "album": "Demo album (offline)",
                "local": True,
            })
    return stable_order(photos)




# --- Offline albums ----------------------------------------------------------
# Cache chosen albums on disk so the frame keeps working with no internet.
# Each album lives in offline/<key>/ with a meta.json describing it.

OFFLINE_DIR = BASE / "offline"
OFFLINE_FLAG = OFFLINE_DIR / "enabled"
OFFLINE_IMAGE_SIZE = "s2560"   # matches what the display asks Google for


def offline_enabled():
    return OFFLINE_FLAG.exists()


def set_offline(enabled):
    OFFLINE_DIR.mkdir(exist_ok=True)
    if enabled:
        OFFLINE_FLAG.touch()
    else:
        OFFLINE_FLAG.unlink(missing_ok=True)
    return offline_enabled()


def album_key(url):
    """Stable directory name for an album link."""
    return hashlib.sha1(url.strip().encode()).hexdigest()[:12]


def album_dir(url):
    return OFFLINE_DIR / album_key(url)


def album_cache_info(url):
    """How much of this album is cached: (count, bytes, title)."""
    d = album_dir(url)
    if not d.is_dir():
        return 0, 0, None
    title = None
    meta = d / "meta.json"
    if meta.exists():
        try:
            title = json.loads(meta.read_text()).get("title")
        except (json.JSONDecodeError, OSError):
            pass
    files = [f for f in d.iterdir() if f.suffix.lower() == ".jpg"]
    return len(files), sum(f.stat().st_size for f in files), title


def delete_album_cache(url):
    d = album_dir(url)
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
        return True
    return False


def download_album(url, progress=None):
    """Fetch an album's photos to disk. progress(done, total) is called as it goes.

    Google rejects image requests carrying a Referer, so we send none -- the
    same reason the display sets referrerpolicy="no-referrer".
    """
    title, photos = scrape_album(url)
    photos = stable_order(photos)
    d = album_dir(url)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"url": url, "title": title}))

    total = len(photos)
    done = 0
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    for i, photo in enumerate(photos):
        target = d / f"{i:05d}.jpg"
        if target.exists() and target.stat().st_size > 0:
            done += 1
            if progress:
                progress(done, total)
            continue
        try:
            r = session.get(photo["url"] + "=" + OFFLINE_IMAGE_SIZE, timeout=60)
            if r.status_code == 429:      # backed off, try once more
                time.sleep(5)
                r = session.get(photo["url"] + "=" + OFFLINE_IMAGE_SIZE, timeout=60)
            r.raise_for_status()
            target.write_bytes(r.content)
            done += 1
        except Exception:
            target.unlink(missing_ok=True)   # leave a gap rather than a broken file
        if progress:
            progress(done, total)

    return {"title": title, "downloaded": done, "total": total}


def offline_photos():
    """Every cached photo, across all cached albums."""
    if not OFFLINE_DIR.is_dir():
        return []
    photos = []
    for d in sorted(OFFLINE_DIR.iterdir()):
        if not d.is_dir():
            continue
        title = "Offline album"
        meta = d / "meta.json"
        if meta.exists():
            try:
                title = json.loads(meta.read_text()).get("title") or title
            except (json.JSONDecodeError, OSError):
                pass
        for f in sorted(d.iterdir()):
            if f.suffix.lower() == ".jpg":
                photos.append({
                    "url": f"/offline/{d.name}/{f.name}",
                    "w": 0, "h": 0,
                    "album": title,
                    "local": True,
                })
    return stable_order(photos)



if __name__ == "__main__":
    result = refresh()
    sys.exit(0 if result["photos"] else 1)
