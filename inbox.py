"""Manual inbox for films: a folder dropped in Downloads/manual/movies ends up in the library, normalised.

    inbox.py --run     # one pass over the inbox (mm-inbox.timer, every 5 minutes)

Antonio 2026-11-18 (plan whimsical-doodling-quilt, phase 5): put a film in by hand without Claude. Nothing here
remuxes or names anything itself. It hands the file to Radarr as a manual import (no search, importMode copy,
which hardlinks) and Radarr's webhook runs the same path as any download: _adopt_from_staging, remux,
_verify_and_finalize, _finish_staging, and the lost-audio guard on upgrades. This file only waits, identifies,
imports, watches, and deletes the package once Jellyfin serves the result.

States: waiting -> needs_title | importing -> normalizing -> ready, or failed with a reason. A failed or
needs_title item is left on disk untouched; the panel sets its TMDB id or retries it.
"""
import json, os, re, shutil, subprocess, sys, time, urllib.parse

import app, scan

INBOX = os.environ.get("MM_INBOX_MOVIES", "/srv/storage/Downloads/manual/movies")
PROFILE = int(os.environ.get("MM_INBOX_PROFILE", "7"))   # Radarr "4K Preferred": what The Odyssey and Toy Story 5 got
IMPORT_TIMEOUT = 30 * 60      # Radarr imported nothing that reached the hook by then: say so
TMDB_TAG = re.compile(r"\{tmdb-(\d+)\}")


def _files(path):
    if os.path.isfile(path):
        return [path]
    return [os.path.join(d, f) for d, _, fs in os.walk(path) for f in fs]


def _size(path):
    return sum(os.path.getsize(f) for f in _files(path) if os.path.exists(f))


def _video(path):
    vids = [f for f in _files(path) if f.lower().endswith(scan.VIDEO_EXT) and not os.path.basename(f).startswith(".")
            and "sample" not in os.path.basename(f).lower()]
    return max(vids, key=os.path.getsize) if vids else None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def identify(name, tmdb_id=None):
    """-> (match or None, guessed title, guessed year). Exact only: same title and same year, one hit."""
    tag = TMDB_TAG.search(name)
    tmdb_id = tmdb_id or (int(tag.group(1)) if tag else None)
    title, year = scan.clean_title_year(TMDB_TAG.sub("", name).strip())
    if tmdb_id:
        return scan.tmdb_get_movie(tmdb_id, app.TMDB_API_KEY), title, year
    if not year:
        return None, title, year
    hits = [c for c in scan.tmdb_search_candidates(title, year, app.TMDB_API_KEY, limit=20)
            if _norm(c["title"]) == _norm(title) and c["year"] == year]
    return (hits[0] if len(hits) == 1 else None), title, year


def _extract(entry):
    """RAR without a video next to it -> extract in place. -> reason on failure, else None."""
    rars = sorted(f for f in _files(entry) if f.lower().endswith(".rar"))
    if not rars:
        return "no hay video"
    first = next((f for f in rars if re.search(r"\.part0*1\.rar$", f, re.I)), rars[0])   # a set opens at volume 1
    r = subprocess.run(["unrar", "x", "-o+", "-p-", "-idq", first, os.path.dirname(first) + "/"],
                       capture_output=True, text=True)
    if r.returncode:
        out = (r.stdout + r.stderr).lower()
        return "el RAR pide contraseña" if "password" in out or "encrypted" in out else f"unrar falló: {out.strip()[-200:]}"
    return None


def _radarr_movie(tmdb_id):
    """Radarr's record for this film, added unmonitored into Staging if it has none (no search)."""
    hit = app._radarr("GET", f"movie?tmdbId={tmdb_id}")
    if hit:
        return hit[0]
    look = app._radarr("GET", f"movie/lookup/tmdb?tmdbId={tmdb_id}")
    if not look:
        return None
    # no `path`: Radarr builds the folder from its own naming format under rootFolderPath
    return app._radarr("POST", "movie", {**look, "qualityProfileId": PROFILE, "rootFolderPath": app.STAGING_CONTAINER,
                                         "monitored": False, "minimumAvailability": "released",
                                         "addOptions": {"searchForMovie": False}})


def _import(entry, video, movie):
    """Radarr ManualImport of one file into `movie`. -> (command id, None) or (None, reason)."""
    q = urllib.parse.urlencode({"folder": entry, "filterExistingFiles": "false"})
    cand = next((c for c in app._radarr("GET", f"manualimport?{q}") or [] if c.get("path") == video), None)
    if not cand:
        return None, "Radarr no ve el video en la carpeta"
    f = {"path": video, "movieId": movie["id"], "quality": cand["quality"], "languages": cand.get("languages") or [],
         "releaseGroup": cand.get("releaseGroup") or "", "indexerFlags": cand.get("indexerFlags") or 0, "downloadId": ""}
    cmd = app._radarr("POST", "command", {"name": "ManualImport", "importMode": "copy", "files": [f]})
    return ((cmd or {}).get("id"), None) if cmd and cmd.get("id") else (None, "Radarr no aceptó la importación")


def _set(conn, item, **kw):
    kw["updated_at"] = scan._now()
    conn.execute(f"UPDATE inbox_items SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), item["id"]))
    conn.commit()
    item.update(kw)


def _fail(conn, item, reason):
    _set(conn, item, status="failed", reason=reason)
    app._notify(f"Entrada manual falló: {item['title'] or os.path.basename(item['path'])}", reason,
                tags="warning", priority=4)


def step(conn, item):
    """Advance one item by at most one state. Each branch reads the world fresh; nothing is cached across runs."""
    path, name = item["path"], os.path.basename(item["path"])
    if item["status"] == "waiting":
        size = _size(path)
        if size != item["size"]:                 # still copying (or first sighting): wait one more pass
            _set(conn, item, size=size, reason="copiando" if item["size"] is not None else None)
            return
        video = _video(path)
        if not video:
            reason = _extract(path)
            if reason:
                return _fail(conn, item, reason)
            _set(conn, item, size=_size(path), reason="extraído")    # a fresh size: wait one pass more
            return
        match, title, year = identify(name, item["tmdb_id"])
        if not match:
            _set(conn, item, status="needs_title", title=title, year=year,
                 reason="sin coincidencia exacta en TMDB" if year else "falta el año en el nombre")
            app._notify(f"Entrada manual: necesita título", f"{name}: elige la película en media-manager", tags="question")
            return
        _set(conn, item, tmdb_id=match["tmdb_id"], title=match["title"], year=match["year"])
        movie = _radarr_movie(match["tmdb_id"])
        if not movie or not movie.get("id"):
            return _fail(conn, item, "Radarr no pudo añadir la película")
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM jobs").fetchone()[0]
        cmd, reason = _import(path, video, movie)
        if not cmd:
            return _fail(conn, item, reason)
        _set(conn, item, status="importing", radarr_cmd=cmd, last_job=last, imported_at=time.time(), reason=None)
        return

    if item["status"] in ("importing", "normalizing"):
        row = conn.execute("SELECT id, folder, file, status FROM movies WHERE tmdb_id=?", (item["tmdb_id"],)).fetchone()
        job = row and conn.execute("SELECT id, status FROM jobs WHERE movie_id=? AND id>? ORDER BY id DESC LIMIT 1",
                                   (row["id"], item["last_job"])).fetchone()
        if item["status"] == "importing":
            cmd = app._radarr("GET", f"command/{item['radarr_cmd']}") or {}
            if cmd.get("status") == "failed":
                return _fail(conn, item, f"Radarr: {cmd.get('message') or 'la importación falló'}")
            if row and row["status"] == "graft":
                return _fail(conn, item, "la copia nueva perdió audio que tenía la anterior: injerto pendiente")
            if job:
                _set(conn, item, status="normalizing", movie_id=row["id"])
            elif time.time() - item["imported_at"] > IMPORT_TIMEOUT:
                _fail(conn, item, "Radarr no importó en 30 min (revisa Actividad en Radarr)")
            return
        if job["status"] == "failed":
            return _fail(conn, item, f"el remux falló (job {job['id']})")
        if job["status"] != "verified" or row["status"] != "clean" or not row["file"] or row["file"].startswith("."):
            return
        if os.path.isdir(os.path.join(app.STAGING_ROOT, row["folder"])):    # _finish_staging not done yet
            return
        seen, why = app._jellyfin_refresh(row["folder"], row["file"], item["title"])
        if not seen:
            return _set(conn, item, reason=f"esperando a Jellyfin: {why}")
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        _set(conn, item, status="ready", reason=None)
        app._notify(f"Entrada manual lista: {item['title']} ({item['year']})", "en Jellyfin; borré el paquete de entrada",
                    tags="white_check_mark")


def run(conn):
    os.makedirs(INBOX, exist_ok=True)
    for f in sorted(os.listdir(INBOX)):
        p = os.path.join(INBOX, f)
        if f.startswith((".", "_")):
            continue
        if os.path.isfile(p):
            if not f.lower().endswith(scan.VIDEO_EXT + (".rar",)):
                continue
            # Radarr's manual import scans a folder: give a loose file its own
            d = os.path.join(INBOX, os.path.splitext(f)[0])
            os.makedirs(d, exist_ok=True)
            os.rename(p, os.path.join(d, f))
            p = d
        conn.execute("INSERT OR IGNORE INTO inbox_items (path, updated_at) VALUES (?, ?)", (p, scan._now()))
        # the same folder name dropped again after an earlier one finished: a new item
        conn.execute("UPDATE inbox_items SET status='waiting', size=NULL, reason=NULL, tmdb_id=NULL, movie_id=NULL "
                     "WHERE path=? AND status='ready'", (p,))
    conn.commit()
    for r in conn.execute("SELECT * FROM inbox_items WHERE status IN ('waiting','importing','normalizing')").fetchall():
        item = dict(r)
        if not os.path.exists(item["path"]):
            _set(conn, item, status="failed", reason="la carpeta desapareció")
            continue
        try:
            step(conn, item)
        except Exception as e:                    # one bad item must not stall the rest
            _fail(conn, item, f"error interno: {e}")


if __name__ == "__main__":
    app.init_db()
    conn = app.get_db()
    if "--run" in sys.argv:
        run(conn)
    for r in conn.execute("SELECT id, status, title, path, reason FROM inbox_items ORDER BY id DESC LIMIT 20"):
        print(dict(r))
