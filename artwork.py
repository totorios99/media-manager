"""Download show artwork from TMDB into the layout Jellyfin reads off disk.

Movies get theirs from Radarr, which writes poster/backdrop/landscape/logo into
the movie folder. There is no Sonarr here, so nothing ever wrote a single image
for a series -- all 11 shelves were bare. This fills the same role for shows.

Language preference is Spanish, then English, then a textless image: a logo in
the wrong language is worse than no logo, but a plain one always works.
"""
import json
import os
import urllib.parse
import urllib.request

TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/original"
LANGS = ["es", "en", "null"]          # null = textless, TMDB's own spelling

# filename -> TMDB field. Jellyfin reads these names straight out of the folder.
SHOW_IMAGES = {"poster.jpg": "posters", "backdrop.jpg": "backdrops",
               "logo.png": "logos", "banner.jpg": None}


def _get(path, api_key, **params):
    params["api_key"] = api_key
    url = f"{TMDB}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def _best(images, key):
    """Highest-voted image, preferring Spanish, then English, then textless."""
    pool = images.get(key) or []
    for lang in LANGS:
        want = None if lang == "null" else lang
        hits = [i for i in pool if i.get("iso_639_1") == want]
        if hits:
            return max(hits, key=lambda i: (i.get("vote_average") or 0,
                                            i.get("width") or 0))["file_path"]
    return max(pool, key=lambda i: i.get("vote_average") or 0)["file_path"] if pool else None


def download(url_path, dest):
    """Writes through a temp name: a half-downloaded poster.jpg would be cached
    by Jellyfin as the real one and never retried."""
    tmp = dest + ".part"
    with urllib.request.urlopen(IMG + url_path, timeout=60) as r, open(tmp, "wb") as fh:
        fh.write(r.read())
    os.replace(tmp, dest)
    return os.path.getsize(dest)


def fetch_show(tmdb_id, folder, api_key, seasons=(), overwrite=False):
    """Returns a list of (filename, bytes) actually written."""
    images = _get(f"tv/{tmdb_id}/images", api_key,
                  include_image_language=",".join(LANGS))
    written = []
    for name, key in SHOW_IMAGES.items():
        dest = os.path.join(folder, name)
        if not key or (os.path.exists(dest) and not overwrite):
            continue
        path = _best(images, key)
        if path:
            written.append((name, download(path, dest)))
    for season, season_dir in seasons:
        dest = os.path.join(season_dir, "poster.jpg")
        if os.path.exists(dest) and not overwrite:
            continue
        try:
            data = _get(f"tv/{tmdb_id}/season/{season}", api_key)
        except Exception:
            continue
        if data.get("poster_path"):
            written.append((f"S{season:02d}/poster.jpg",
                            download(data["poster_path"], dest)))
    return written


if __name__ == "__main__":
    import sqlite3
    import sys

    key = os.environ.get("TMDB_API_KEY")
    if not key:
        sys.exit("TMDB_API_KEY no está en el entorno; usa run.sh")
    # MM_SHOWS_ROOT is the name run.sh and the service export (app.py reads it too).
    # This used to read SHOWS_ROOT, which nothing sets, so the hardcoded default
    # was what actually ran -- harmless until the mount point moves.
    root = os.environ.get("MM_SHOWS_ROOT") or os.environ.get("SHOWS_ROOT") or "/media/hdd1/Shows"
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    over = "--overwrite" in sys.argv
    for s in conn.execute("SELECT * FROM shows WHERE tmdb_id IS NOT NULL ORDER BY folder"):
        d = os.path.join(root, s["folder"])
        if not os.path.isdir(d):
            print(f"  {s['folder']}: carpeta ausente")
            continue
        seasons = []
        for name in sorted(os.listdir(d)):
            if name.lower().startswith("season ") and os.path.isdir(os.path.join(d, name)):
                try:
                    seasons.append((int(name.split()[1]), os.path.join(d, name)))
                except ValueError:
                    pass
        got = fetch_show(s["tmdb_id"], d, key, seasons, overwrite=over)
        print(f"{s['title'] or s['folder']}: " +
              (", ".join(f"{n} ({b//1024} KB)" for n, b in got) if got else "sin cambios"))
