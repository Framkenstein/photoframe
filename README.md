# photoframe

A digital photo frame for a Raspberry Pi (or any Linux box) that shows photos
from your **Google Photos shared albums** — one new photo per day, full screen.

Nothing is downloaded or stored. The frame keeps a list of image URLs and
streams each photo from Google when it displays it, so a Pi with a small SD
card can show a library of any size.

- **Set up in the browser** — paste your album links, press Go, done
- **One photo per day**, changing at local midnight
- **Space bar** (or click/tap) jumps to the next photo immediately
- **Fills the screen edge to edge** — portrait photos are cropped to fill
  rather than letterboxed between black bars
- **Multiple albums**, pooled and shuffled into one stable sequence
- Runs 24/7 as a systemd service and survives reboots

## Why this exists

Google removed the `photoslibrary.readonly`, `photoslibrary.sharing`, and
`photoslibrary` API scopes on **31 March 2025**. Since then, no third-party
application can read your Google Photos library. Apps may only touch photos
they uploaded themselves, or ask you to hand-pick images through the
[Picker API](https://developers.google.com/photos/picker/guides/get-started-picker)
one session at a time. That rules out self-hosted photo servers reading your
library, and it rules out the obvious way to build a frame.

This project takes the remaining route: you make an album **link-shared**, and
the frame reads that public page the same way a browser would.

> [!IMPORTANT]
> This uses no official API. It reads the JSON blob Google embeds in the public
> share page. It works today and it is not guaranteed to work tomorrow — if
> Google changes that page, refreshes will start finding zero photos. See
> [Troubleshooting](#troubleshooting).

## Requirements

- Linux with Python 3.9+
- `chromium-browser` (for the fullscreen display)
- Python packages: `flask`, `requests`
- Optional: a Wayfire desktop session — the launcher disables screen blanking
  automatically there. On other desktops, disable blanking yourself.

## Install

```bash
git clone https://github.com/Framkenstein/photoframe.git ~/photoframe
cd ~/photoframe
./install.sh
```

`install.sh` installs the Python dependencies, registers a systemd **user**
service so the frame runs 24/7 and comes back after a reboot, and adds a
"Photo Frame" button to your Desktop.

## Adding your albums

Open <http://localhost:8081>. With nothing configured yet you get the setup
screen: paste a link, press **+ Add more** for another box, and press **Go**.
It fetches each album and tells you how many photos it found before you start
the frame.

To change your albums later, go to <http://localhost:8081/setup> — your current
links are already filled in.

For each album you want, the link comes from Google Photos:

1. Open the album
2. **Share** → **Create link**
3. Copy the link

Both link formats work:

```
https://photos.app.goo.gl/aBcDeFgHiJkLmNoP
https://photos.google.com/share/AF1QipMxxxx?key=yyyy
```

<details>
<summary>Prefer a text file?</summary>

Links are stored in `albums.txt`, one per line; lines starting with `#` are
ignored. After editing it by hand, run `./refresh.sh` to pick up the changes.
</details>

> [!WARNING]
> **A shared album link is a password.** Anyone who has it can view every photo
> in that album, without signing in. `albums.txt` is in `.gitignore` for exactly
> this reason — never commit it, never paste your links into a bug report.

## Using it

Click the **Photo Frame** button on your Desktop, or open
<http://localhost:8081> in any browser on the machine.

| Key | Action |
| --- | --- |
| <kbd>Space</kbd> / <kbd>→</kbd> / <kbd>Enter</kbd> | Next photo |
| Click or tap | Next photo |
| <kbd>Alt</kbd>+<kbd>F4</kbd> | Exit the frame |

Advancing manually does not disturb the schedule: the photo you land on stays
until the next midnight.

## How it works

```
setup screen ──> albums.txt ──> scrape.py ──> photos.json ──> server.py ──> browser
(paste links,                  (reads the                    (picks one     (kiosk;
 press Go)                      public share                  per day)       Space
                                pages)                                       = next)
```

- **`scrape.py`** fetches each shared-album page and pulls out image URLs with
  their dimensions. Photos smaller than 300px are skipped (avatars, icons).
  Results are cached in `photos.json`. A failing album is reported and skipped
  rather than sinking the whole refresh.
- **`server.py`** serves the display on port 8081 and decides which photo is
  today's. It re-scrapes every 6 hours to pick up newly added photos and to
  refresh URLs before Google rotates them.
- **`static/index.html`** is the display: two stacked `<img>` layers that
  crossfade, `object-fit: cover` to fill the screen, and a keyboard handler.

**Ordering** is by a hash of each photo's URL. That gives a shuffled-looking
but *stable* sequence: adding photos to an album later slots them in without
reshuffling everything, and the frame never jumps to a random image after a
refresh. The current photo is tracked by URL rather than by index for the same
reason.

## Configuration

| What | Where |
| --- | --- |
| Album links | `albums.txt` |
| How often to re-scrape | `REFRESH_EVERY_SECONDS` in `server.py` |
| Port | bottom of `server.py` |
| Image resolution requested | `IMAGE_SIZE` in `static/index.html` (`s2560` = longest edge 2560px) |
| Crossfade length | `.layer` transition in `static/index.html` |
| Fill vs. fit | `object-fit` in `static/index.html` — change `cover` to `contain` for letterboxing instead of cropping |

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Setup screen if no albums yet, otherwise the frame |
| `GET /frame` | The frame, always |
| `GET /setup` | The setup screen, always |
| `GET /api/photo` | Today's photo (advances if the day rolled over) |
| `POST /api/next` | Advance now |
| `GET /api/albums` | Current album links |
| `POST /api/albums` | Replace album links, then re-scrape |
| `GET /api/status` | Photo count, per-album results, current state |
| `POST /api/refresh` | Force a re-scrape |

## Troubleshooting

**"No photos yet" on screen**
No albums are configured. Open <http://localhost:8081/setup> and add one.

**An album reports 0 photos**
Almost always the album is not link-shared. Open it in Google Photos → Share →
Create link. If the link definitely works in a private browser window and the
count is still 0, Google has likely changed their page format — please
[open an issue](https://github.com/Framkenstein/photoframe/issues).

**Screen goes black after a while**
The launcher disables blanking on Wayfire only. On another desktop, disable
screen blanking and power saving in its settings.

**Check what the service is doing**

```bash
systemctl --user status photoframe.service
curl -s http://localhost:8081/api/status | python3 -m json.tool
tail -f ~/photoframe/server.log
```

## Limitations

- Depends on Google's public share page format, not a supported API
- Album must be link-shared; private albums are not reachable
- Videos are not shown, only stills
- No transitions beyond a crossfade, by design — it is a photo frame

## License

MIT — see [LICENSE](LICENSE).
