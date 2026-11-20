"""Manual inbox: a folder dropped in Downloads/manual/movies or .../shows ends up in the library, normalised.

    inbox.py --run     # one pass over the inbox (mm-inbox.timer, every 5 minutes)

Antonio 2026-11-18 (plan whimsical-doodling-quilt, phase 5): put a film in by hand without Claude. Nothing here
remuxes or names anything itself. It hands the file to Radarr as a manual import (no search, importMode copy,
which hardlinks) and Radarr's webhook runs the same path as any download: _adopt_from_staging, remux,
_verify_and_finalize, _finish_staging, and the lost-audio guard on upgrades. This file only waits, identifies,
imports, watches, and deletes the package once Jellyfin serves the result.

Shows (2026-11-19) take the same road through Sonarr: one folder per show ("Serie (Año)", or {tvdb-N} in the
name), every video in it must map to an episode, and an episode the library already has stops the whole folder:
there is no lost-audio guard for episodes, so replacing one stays a decision for a person.

States: waiting -> needs_title | importing -> normalizing -> ready, or failed with a reason. A failed or
needs_title item is left on disk untouched; the panel sets its TMDB id or retries it.
"""
import json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request

import app, scan

INBOX = os.environ.get("MM_INBOX_MOVIES", "/srv/storage/Downloads/manual/movies")
PROFILE = int(os.environ.get("MM_INBOX_PROFILE", "7"))   # Radarr "4K Preferred": what The Odyssey and Toy Story 5 got
IMPORT_TIMEOUT = 30 * 60      # Radarr imported nothing that reached the hook by then: say so
TMDB_TAG = re.compile(r"\{tmdb-(\d+)\}")
INBOX_SHOWS = os.environ.get("MM_INBOX_SHOWS", "/srv/storage/Downloads/manual/shows")
SONARR_URL = os.environ.get("SONARR_URL", "http://localhost:8989")
SONARR_CONFIG = os.environ.get("SONARR_CONFIG", "/DATA/AppData/sonarr/config/config.xml")
SONARR_ROOT = "/data/Shows"              # Sonarr mounts /srv/storage as /data
TV_PROFILE = 1                            # "Any": a manual import is whatever Antonio brought
TVDB_TAG = re.compile(r"\{tvdb-(\d+)\}")


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


def _sonarr(method, path, body=None):
    # the key is read from Sonarr's own config on every call and never stored, printed or logged
    key = re.search(r"<ApiKey>(\w+)</ApiKey>", open(SONARR_CONFIG).read()).group(1)
    req = urllib.request.Request(f"{SONARR_URL}/api/v3/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Api-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read() or b"null")


def _container(path):
    return "/data" + path[len("/srv/storage"):] if path.startswith("/srv/storage/") else path


def identify_show(name, tvdb_id=None):
    """-> (Sonarr lookup record or None, guessed title, guessed year). Exact only, like films."""
    tag = TVDB_TAG.search(name)
    tvdb_id = tvdb_id or (int(tag.group(1)) if tag else None)
    title, year = scan.clean_title_year(TVDB_TAG.sub("", name).strip())
    if tvdb_id:
        hits = _sonarr("GET", f"series/lookup?term=tvdb:{tvdb_id}") or []
        return (hits[0] if hits else None), title, year
    if not year:
        return None, title, year
    hits = [h for h in _sonarr("GET", "series/lookup?" + urllib.parse.urlencode({"term": title})) or []
            if _norm(h.get("title")) == _norm(title) and h.get("year") == year]
    return (hits[0] if len(hits) == 1 else None), title, year


def _sonarr_series(look):
    """Sonarr's record for this show, added unmonitored with no search if it has none."""
    hit = _sonarr("GET", f"series?tvdbId={look['tvdbId']}")
    if hit:
        return hit[0]
    return _sonarr("POST", "series", {**look, "qualityProfileId": TV_PROFILE, "rootFolderPath": SONARR_ROOT,
                                      "monitored": False, "seasonFolder": True,
                                      "addOptions": {"monitor": "none", "searchForMissingEpisodes": False,
                                                     "searchForCutoffUnmetEpisodes": False}})


def _import_show(entry, series):
    """Sonarr ManualImport of every video in `entry`. -> (command id, [[s, e], ...], None) or (None, None, reason)."""
    q = urllib.parse.urlencode({"folder": _container(entry), "seriesId": series["id"], "filterExistingFiles": "false"})
    cands = {c.get("path"): c for c in _sonarr("GET", f"manualimport?{q}") or []}
    files, eps = [], []
    for v in sorted(f for f in _files(entry) if f.lower().endswith(scan.VIDEO_EXT) and "sample" not in f.lower()
                    and not os.path.basename(f).startswith(".")):
        c = cands.get(_container(v)) or {}
        if not c.get("episodes"):
            return None, None, f"Sonarr no reconoce el episodio de {os.path.basename(v)}"
        have = [e for e in c["episodes"] if e.get("hasFile")]
        if have:
            return None, None, (f"S{have[0]['seasonNumber']:02d}E{have[0]['episodeNumber']:02d} ya está en la "
                                "biblioteca: reemplazarlo lo decide una persona")
        files.append({"path": c["path"], "seriesId": series["id"], "episodeIds": [e["id"] for e in c["episodes"]],
                      "quality": c["quality"], "languages": c.get("languages") or [],
                      "releaseGroup": c.get("releaseGroup") or "", "downloadId": ""})
        first = min(c["episodes"], key=lambda e: (e["seasonNumber"], e["episodeNumber"]))   # a double episode is one row
        eps.append([first["seasonNumber"], first["episodeNumber"]])
    if not files:
        return None, None, "no hay video"
    cmd = _sonarr("POST", "command", {"name": "ManualImport", "importMode": "copy", "files": files})
    return ((cmd["id"], eps, None) if cmd and cmd.get("id") else (None, None, "Sonarr no aceptó la importación"))


def step_show(conn, item):
    """step() for a show folder. Ready = every imported episode verified clean; the package is a hardlinked copy,
    so deleting it frees nothing the library needs. ponytail: no Jellyfin check per episode, the Shows library
    is re-read by the hook's own rescan; add one if an episode ever shows stale tracks."""
    path, name = item["path"], os.path.basename(item["path"])
    if item["status"] == "waiting":
        if os.path.isfile(path):
            return _fail(conn, item, "un episodio suelto va dentro de una carpeta «Serie (Año)»")
        size = _size(path)
        if size != item["size"]:
            _set(conn, item, size=size, reason="copiando" if item["size"] is not None else None)
            return
        if not _video(path):
            reason = _extract(path)
            if reason:
                return _fail(conn, item, reason)
            _set(conn, item, size=_size(path), reason="extraído")
            return
        look, title, year = identify_show(name, item["tvdb_id"])
        if not look:
            _set(conn, item, status="needs_title", title=title, year=year,
                 reason="sin coincidencia exacta en Sonarr" if year else "falta el año en el nombre")
            app._notify("Entrada manual: necesita título", f"{name}: elige la serie en media-manager", tags="question")
            return
        _set(conn, item, tvdb_id=look["tvdbId"], title=look["title"], year=look.get("year"))
        series = _sonarr_series(look)
        if not series or not series.get("id"):
            return _fail(conn, item, "Sonarr no pudo añadir la serie")
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM jobs").fetchone()[0]
        cmd, eps, reason = _import_show(path, series)
        if not cmd:
            return _fail(conn, item, reason)
        _set(conn, item, status="importing", radarr_cmd=cmd, episodes=json.dumps(eps), last_job=last,
             imported_at=time.time(), reason=None)
        return

    series = (_sonarr("GET", f"series?tvdbId={item['tvdb_id']}") or [{}])[0]
    show = conn.execute("SELECT id FROM shows WHERE folder=?", (os.path.basename(series.get("path") or ""),)).fetchone()
    rows = []
    for s, e in json.loads(item["episodes"] or "[]"):
        ep = show and conn.execute("SELECT id, file, status FROM episodes WHERE show_id=? AND season=? AND episode=?",
                                   (show["id"], s, e)).fetchone()
        job = ep and conn.execute("SELECT id, status FROM jobs WHERE episode_id=? AND id>? ORDER BY id DESC LIMIT 1",
                                  (ep["id"], item["last_job"])).fetchone()
        rows.append((f"S{s:02d}E{e:02d}", ep, job))
    if item["status"] == "importing":
        cmd = _sonarr("GET", f"command/{item['radarr_cmd']}") or {}
        if cmd.get("status") == "failed":
            return _fail(conn, item, f"Sonarr: {cmd.get('message') or 'la importación falló'}")
        if rows and all(job for _, _, job in rows):
            _set(conn, item, status="normalizing", movie_id=show["id"])
        elif time.time() - item["imported_at"] > IMPORT_TIMEOUT:
            missing = [n for n, _, job in rows if not job]
            _fail(conn, item, f"sin normalizar tras 30 min: {', '.join(missing[:5])} (revisa Actividad en Sonarr)")
        return
    bad = [f"{n} (job {job['id']})" for n, _, job in rows if job["status"] == "failed"]
    if bad:
        return _fail(conn, item, f"el remux falló: {', '.join(bad[:5])}")
    if not all(job["status"] == "verified" and ep["status"] == "clean" and not (ep["file"] or ".").startswith(".")
               for _, ep, job in rows):
        return
    shutil.rmtree(path)
    _set(conn, item, status="ready", reason=None)
    app._notify(f"Entrada manual lista: {item['title']}", f"{len(rows)} episodios normalizados; borré el paquete de entrada",
                tags="white_check_mark")


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
    if item["path"].startswith(INBOX_SHOWS + "/"):
        return step_show(conn, item)
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
    os.makedirs(INBOX_SHOWS, exist_ok=True)
    for p in [os.path.join(d, f) for d in (INBOX, INBOX_SHOWS) for f in sorted(os.listdir(d))]:
        f = os.path.basename(p)
        if f.startswith((".", "_")):
            continue
        if os.path.isfile(p) and p.startswith(INBOX + "/"):
            if not f.lower().endswith(scan.VIDEO_EXT + (".rar",)):
                continue
            # Radarr's manual import scans a folder: give a loose file its own
            d = os.path.join(INBOX, os.path.splitext(f)[0])
            os.makedirs(d, exist_ok=True)
            os.rename(p, os.path.join(d, f))
            p = d
        conn.execute("INSERT OR IGNORE INTO inbox_items (path, updated_at) VALUES (?, ?)", (p, scan._now()))
        # the same folder name dropped again after an earlier one finished: a new item
        conn.execute("UPDATE inbox_items SET status='waiting', size=NULL, reason=NULL, tmdb_id=NULL, movie_id=NULL, "
                     "tvdb_id=NULL, episodes=NULL "
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
