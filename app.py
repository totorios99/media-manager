import os
import re
import shlex
import sqlite3
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import commands
import jobs
import scan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media/hdd1/Movies")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
DB_PATH = os.environ.get("MM_DB_PATH", os.path.join(BASE_DIR, "media.db"))
LOG_DIR = os.environ.get("MM_LOG_DIR", os.path.join(BASE_DIR, "logs"))

# CPU schedule: throttled during work hours so the PC stays usable, full speed
# otherwise. Quota changes are applied live to running jobs — encodes never pause.
# ponytail: single daily hour range; per-weekday schedule if ever needed
WORK_HOURS = os.environ.get("MM_WORK_HOURS", "9-23")    # start-end, end exclusive; wraps midnight if start>end
WORK_QUOTA = os.environ.get("MM_WORK_QUOTA", "300%")
FREE_QUOTA = os.environ.get("MM_FREE_QUOTA", "600%")    # 6 threads on this box = unlimited


def _get_setting(conn, key, default):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _power_state(conn):
    return {
        "mode": _get_setting(conn, "power_mode", "auto"),  # auto | full | throttle
        "work_hours": _get_setting(conn, "work_hours", WORK_HOURS),
        "work_quota": WORK_QUOTA,
        "free_quota": FREE_QUOTA,
    }


def _current_quota(conn):
    p = _power_state(conn)
    if p["mode"] == "full":
        return FREE_QUOTA
    if p["mode"] == "throttle":
        return WORK_QUOTA
    a, b = (int(x) for x in p["work_hours"].split("-"))
    h = time.localtime().tm_hour
    in_work = (a <= h < b) if a <= b else (h >= a or h < b)
    return WORK_QUOTA if in_work else FREE_QUOTA

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT UNIQUE NOT NULL,
    file TEXT,
    clean_title TEXT, guess_year INTEGER,
    tmdb_id INTEGER, title TEXT, year INTEGER,
    original_language TEXT, poster_path TEXT,
    container_title TEXT, video_codec TEXT, width INTEGER, height INTEGER,
    bitrate INTEGER, duration REAL, size_bytes INTEGER,
    hdr TEXT,
    status TEXT DEFAULT 'unprocessed',
    output_file TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT UNIQUE NOT NULL,
    clean_title TEXT, guess_year INTEGER,
    tmdb_id INTEGER, title TEXT, year INTEGER,
    original_language TEXT, poster_path TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    season INTEGER, episode INTEGER,
    folder TEXT NOT NULL,
    file TEXT,
    container_title TEXT, video_codec TEXT, width INTEGER, height INTEGER,
    bitrate INTEGER, duration REAL, size_bytes INTEGER, hdr TEXT,
    status TEXT DEFAULT 'unprocessed',
    excluded INTEGER DEFAULT 0,
    output_file TEXT, updated_at TEXT,
    UNIQUE(folder, file)
);
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER REFERENCES movies(id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
    mkv_id INTEGER,
    type TEXT NOT NULL, codec TEXT, lang TEXT, name TEXT,
    channels INTEGER, default_flag INTEGER DEFAULT 0, forced_flag INTEGER DEFAULT 0,
    ext_path TEXT,
    keep INTEGER DEFAULT 1, out_order INTEGER DEFAULT 0,
    out_lang TEXT DEFAULT '', out_default INTEGER DEFAULT 0, out_forced INTEGER DEFAULT 0,
    out_name TEXT DEFAULT '',
    CHECK ((movie_id IS NULL) <> (episode_id IS NULL))
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER, episode_id INTEGER, kind TEXT,
    tmux_session TEXT, log_path TEXT, cmd TEXT,
    status TEXT, progress REAL DEFAULT 0, exit_code INTEGER,
    started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tracks_movie ON tracks(movie_id);
CREATE INDEX IF NOT EXISTS idx_tracks_episode ON tracks(episode_id);
CREATE INDEX IF NOT EXISTS idx_jobs_movie ON jobs(movie_id);
CREATE INDEX IF NOT EXISTS idx_jobs_episode ON jobs(episode_id);
CREATE INDEX IF NOT EXISTS idx_episodes_show ON episodes(show_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

# Columns of the pre-TV `tracks` table, in order -- used verbatim by the
# rebuild migration below (SQLite can't drop NOT NULL any other way).
_TRACKS_OLD_COLS = ("id", "movie_id", "mkv_id", "type", "codec", "lang", "name",
                     "channels", "default_flag", "forced_flag", "ext_path", "keep",
                     "out_order", "out_lang", "out_default", "out_forced", "out_name")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate(conn):
    """One-shot rebuild for pre-TV databases: tracks.movie_id was NOT NULL, which
    SQLite can't alter away, so the table must be recreated. Gated on
    PRAGMA user_version (not a column sniff) so a crash mid-migration can't be
    mistaken for "done" on the next startup."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
    jobs_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if jobs_exists:
        job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "episode_id" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN episode_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_episode ON jobs(episode_id)")
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tracks)")} if exists else set()
    if exists and "episode_id" not in cols:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        try:
            conn.execute("""
                CREATE TABLE tracks_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    movie_id INTEGER REFERENCES movies(id) ON DELETE CASCADE,
                    episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
                    mkv_id INTEGER,
                    type TEXT NOT NULL, codec TEXT, lang TEXT, name TEXT,
                    channels INTEGER, default_flag INTEGER DEFAULT 0, forced_flag INTEGER DEFAULT 0,
                    ext_path TEXT,
                    keep INTEGER DEFAULT 1, out_order INTEGER DEFAULT 0,
                    out_lang TEXT DEFAULT '', out_default INTEGER DEFAULT 0, out_forced INTEGER DEFAULT 0,
                    out_name TEXT DEFAULT '',
                    CHECK ((movie_id IS NULL) <> (episode_id IS NULL))
                )
            """)
            col_list = ", ".join(_TRACKS_OLD_COLS)
            conn.execute(f"INSERT INTO tracks_new ({col_list}) SELECT {col_list} FROM tracks")
            conn.execute("DROP TABLE tracks")
            conn.execute("ALTER TABLE tracks_new RENAME TO tracks")
            conn.execute("CREATE INDEX idx_tracks_movie ON tracks(movie_id)")
            conn.execute("CREATE INDEX idx_tracks_episode ON tracks(episode_id)")
            bad = conn.execute("PRAGMA foreign_key_check(tracks)").fetchall()
            if bad:
                raise RuntimeError(f"foreign_key_check failed post-migration: {bad}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA user_version=1")
    conn.commit()


def init_db():
    conn = get_db()
    # migrate BEFORE the schema script: on a pre-TV DB, `tracks` still has the
    # old NOT NULL shape, and the script's own `idx_tracks_episode` index would
    # fail against it since CREATE TABLE IF NOT EXISTS no-ops on the existing table
    _migrate(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


app = FastAPI()

scan_state = {"running": False, "done": 0, "total": 0, "current": ""}


# ---------- owner abstraction (movie or episode) ----------
# A "job owner" is either a movie or an episode. Both are one metadata row +
# one file; everything below (job building, verification, junk sweep) only
# needs a uniform view of that, so the rest of the file works on `kind`
# ('movie'|'episode') + a plain dict, never branching on the two schemas again.

def _owner_col(kind):
    return "movie_id" if kind == "movie" else "episode_id"


def _owner_table(kind):
    return "movies" if kind == "movie" else "episodes"


def _owner_info(conn, kind, owner_id):
    """Uniform dict for a movie or episode row: folder (already composed with
    any show/season prefix, relative to MEDIA_ROOT), file, title_display (for
    --title metadata), out_base (filename stem), duration, size_bytes, status,
    output_file, original_language. None if the row doesn't exist."""
    if kind == "movie":
        row = conn.execute("SELECT * FROM movies WHERE id=?", (owner_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["title_display"] = f"{d['title']} ({d['year']})" if d.get("title") else d.get("clean_title")
        d["out_base"] = _safe_name(d["title_display"] or d["folder"])
        return d
    row = conn.execute("SELECT * FROM episodes WHERE id=?", (owner_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    show = conn.execute("SELECT * FROM shows WHERE id=?", (d["show_id"],)).fetchone()
    show_title = (show["title"] or show["clean_title"]) if show else "Unknown"
    d["original_language"] = show["original_language"] if show else None
    d["_raw_folder"] = d["folder"]  # season subdir (or '') relative to the show folder
    d["folder"] = os.path.join(show["folder"], d["folder"]) if d["folder"] else show["folder"]
    d["title_display"] = f"{show_title} - S{(d['season'] or 0):02d}E{(d['episode'] or 0):02d}"
    d["out_base"] = _safe_name(d["title_display"])
    return d


def _set_owner_status(conn, kind, owner_id, status, output_file=None):
    table = _owner_table(kind)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if output_file is not None:
        conn.execute(f"UPDATE {table} SET status=?, output_file=?, updated_at=? WHERE id=?",
                     (status, output_file, now, owner_id))
    else:
        conn.execute(f"UPDATE {table} SET status=?, updated_at=? WHERE id=?", (status, now, owner_id))


def _job_owner_kind_id(job):
    return ("movie", job["movie_id"]) if job["movie_id"] else ("episode", job["episode_id"])


def _reap_stale_jobs():
    """Recover jobs left 'running' by a crash/power-loss. A job that finished
    while the server was down has an EXIT: marker in its log — _poll_and_finalize
    picks that up and verifies normally. One whose process is gone (systemd scope
    inactive) is marked failed and its partial output deleted, so it can simply
    be re-run. tmux liveness alone is not trusted: session-restore plugins
    recreate same-named sessions holding a plain shell."""
    conn = get_db()
    try:
        for r in conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
            job = _poll_and_finalize(conn, r["id"])
            if job and job["status"] == "running" and not jobs.scope_active(job["id"]):
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                kind, oid = _job_owner_kind_id(job)
                conn.execute("UPDATE jobs SET status='failed', finished_at=? WHERE id=?", (now, job["id"]))
                _set_owner_status(conn, kind, oid, "error")
                conn.commit()
                job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (r["id"],)).fetchone())
            if job and job["status"] == "failed" and job["kind"] != "sample":
                kind, oid = _job_owner_kind_id(job)
                owner = _owner_info(conn, kind, oid)
                if owner and owner["output_file"]:
                    try:
                        os.remove(owner["output_file"])
                    except OSError:
                        pass
    finally:
        conn.close()


@app.on_event("startup")
def _startup():
    os.makedirs(LOG_DIR, exist_ok=True)
    init_db()
    _reap_stale_jobs()
    threading.Thread(target=_job_ticker, daemon=True).start()


def _job_ticker():
    """Server-side job driver (UI polling only works while a browser is open):
    advances running jobs, starts the next queued one, re-applies the CPU
    quota schedule live to running scopes."""
    while True:
        time.sleep(30)
        try:
            conn = get_db()
            try:
                for r in conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
                    _poll_and_finalize(conn, r["id"])
                if not conn.execute("SELECT id FROM jobs WHERE status='running' LIMIT 1").fetchone():
                    nxt = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                    if nxt:
                        jobs.launch_queued(conn, dict(nxt), LOG_DIR, cpu_quota=_current_quota(conn))
                quota = _current_quota(conn)
                for r in conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
                    jobs.set_cpu_quota(r["id"], quota)
            finally:
                conn.close()
        except Exception:
            pass  # ticker must survive any transient error (db lock, tmux hiccup)


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ---------- scan ----------

def _run_scan():
    conn = get_db()
    def cb(done, total, name):
        scan_state.update(done=done, total=total, current=name)
    try:
        scan.scan_library(conn, MEDIA_ROOT, TMDB_API_KEY, progress_cb=cb)
    finally:
        conn.close()
        scan_state["running"] = False


@app.post("/api/scan")
def start_scan():
    if scan_state["running"]:
        raise HTTPException(409, "scan already running")
    scan_state.update(running=True, done=0, total=0, current="")
    threading.Thread(target=_run_scan, daemon=True).start()
    return {"started": True}


@app.get("/api/scan/status")
def scan_status():
    return scan_state


def _keep_files_for(conn, kind, owner):
    """Filenames (basename only) that must survive any junk sweep for this
    movie/episode: the current source video, any in-progress/finished job
    output, external subs. For an episode, also every sibling episode's files
    in the same folder -- a shared season directory is swept as one unit, and
    a sibling's live file must never be mistaken for junk."""
    keep = set()
    if owner["file"]:
        keep.add(owner["file"])
    if owner["output_file"]:
        keep.add(os.path.basename(owner["output_file"]))
    col = _owner_col(kind)
    for t in conn.execute(f"SELECT ext_path FROM tracks WHERE {col}=? AND ext_path IS NOT NULL", (owner["id"],)):
        keep.add(os.path.basename(t["ext_path"]))
    if kind == "episode":
        for r in conn.execute(
            "SELECT id, file, output_file FROM episodes WHERE show_id=? AND folder=? AND id!=?",
            (owner["show_id"], owner["_raw_folder"], owner["id"]),
        ).fetchall():
            if r["file"]:
                keep.add(r["file"])
            if r["output_file"]:
                keep.add(os.path.basename(r["output_file"]))
            for t in conn.execute("SELECT ext_path FROM tracks WHERE episode_id=? AND ext_path IS NOT NULL", (r["id"],)):
                keep.add(os.path.basename(t["ext_path"]))
    return keep


def _junk_scan(conn):
    """folder -> [junk filenames], plus a synthetic "__top_level__" entry for
    stray ._* AppleDouble files sitting directly under MEDIA_ROOT."""
    result = {}
    top_junk = [e.name for e in os.scandir(MEDIA_ROOT) if e.is_file() and e.name.startswith("._")]
    if top_junk:
        result["__top_level__"] = top_junk
    for row in conn.execute("SELECT id FROM movies WHERE file IS NOT NULL").fetchall():
        owner = _owner_info(conn, "movie", row["id"])
        folder = os.path.join(MEDIA_ROOT, owner["folder"])
        junk = scan.find_movie_junk(folder, _keep_files_for(conn, "movie", owner))
        if junk:
            result[owner["folder"]] = junk
    # episodes: sweep once per distinct (show, folder) pair, not once per
    # episode -- the union keep-set already covers every sibling
    seen = set()
    for row in conn.execute("SELECT id, show_id, folder FROM episodes WHERE file IS NOT NULL").fetchall():
        key = (row["show_id"], row["folder"])
        if key in seen:
            continue
        seen.add(key)
        owner = _owner_info(conn, "episode", row["id"])
        folder = os.path.join(MEDIA_ROOT, owner["folder"])
        junk = scan.find_movie_junk(folder, _keep_files_for(conn, "episode", owner))
        if junk:
            result[owner["folder"]] = junk
    return result


@app.get("/api/junk/preview")
def junk_preview():
    conn = get_db()
    try:
        result = _junk_scan(conn)
        return {"total": sum(len(v) for v in result.values()), "folders": result}
    finally:
        conn.close()


@app.post("/api/junk/apply")
def junk_apply():
    conn = get_db()
    try:
        result = _junk_scan(conn)
        removed, errors = 0, []
        for folder, files in result.items():
            base = MEDIA_ROOT if folder == "__top_level__" else os.path.join(MEDIA_ROOT, folder)
            for f in files:
                try:
                    os.remove(os.path.join(base, f))
                    removed += 1
                except OSError as e:
                    errors.append(f"{folder}/{f}: {e}")
        return {"removed": removed, "errors": errors}
    finally:
        conn.close()


# ---------- movies ----------

# bitrate above this (Mbps, by resolution class) → worth a heavy encode;
# at/below it the storage win doesn't justify a lossy re-encode
# ponytail: fixed thresholds; make settings if they ever need tuning
ADVICE_MBPS_UHD, ADVICE_MBPS_FHD, ADVICE_MBPS_SD = 25, 15, 8


def _advice(d):
    """'encode' | 'keep' | None (no data). Advisory only — never blocks a job."""
    br, h = d.get("bitrate"), d.get("height") or 0
    if not br:
        return None
    cap = ADVICE_MBPS_UHD if h >= 2000 else ADVICE_MBPS_FHD if h >= 1000 else ADVICE_MBPS_SD
    return "encode" if br > cap * 1e6 else "keep"


def _movie_summary(row, dup_ids):
    d = dict(row)
    d["dup"] = d["id"] in dup_ids
    d["advice"] = _advice(d)
    return d


def _dup_ids(conn):
    rows = conn.execute(
        "SELECT id FROM movies WHERE tmdb_id IN "
        "(SELECT tmdb_id FROM movies WHERE tmdb_id IS NOT NULL GROUP BY tmdb_id HAVING COUNT(*) > 1)"
    ).fetchall()
    return {r["id"] for r in rows}


@app.get("/api/movies")
def list_movies(status: str | None = None, dup: bool | None = None, q: str | None = None):
    conn = get_db()
    try:
        sql = "SELECT * FROM movies WHERE 1=1"
        args = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if q:
            sql += " AND (title LIKE ? OR clean_title LIKE ? OR folder LIKE ?)"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY COALESCE(title, clean_title, folder)"
        rows = conn.execute(sql, args).fetchall()
        dups = _dup_ids(conn)
        result = [_movie_summary(r, dups) for r in rows]
        if dup is not None:
            result = [m for m in result if m["dup"] == dup]
        return result
    finally:
        conn.close()


@app.get("/api/movies/{movie_id}")
def get_movie(movie_id: int):
    conn = get_db()
    try:
        movie = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not movie:
            raise HTTPException(404, "not found")
        tracks = conn.execute(
            # kept tracks in planned output order first — the UI table renders rows
            # in this order and writes row position back as out_order on save
            "SELECT * FROM tracks WHERE movie_id=? ORDER BY type, keep DESC, out_order, mkv_id", (movie_id,)
        ).fetchall()
        siblings = []
        if movie["tmdb_id"]:
            siblings = [dict(r) for r in conn.execute(
                "SELECT id, folder, title, year, status FROM movies WHERE tmdb_id=? AND id!=?",
                (movie["tmdb_id"], movie_id),
            ).fetchall()]
        m = dict(movie)
        m["advice"] = _advice(m)
        return {"movie": m, "tracks": [dict(t) for t in tracks], "duplicates": siblings}
    finally:
        conn.close()


@app.get("/api/tmdb/search")
def tmdb_search_ep(q: str, year: int | None = None, kind: str = "movie"):
    return scan.tmdb_search_candidates(q, year, TMDB_API_KEY, kind=kind)


@app.post("/api/movies/{movie_id}/tmdb")
def set_tmdb(movie_id: int, body: dict):
    tmdb_id = body.get("tmdb_id")
    if not tmdb_id:
        raise HTTPException(400, "tmdb_id required")
    info = scan.tmdb_get_movie(tmdb_id, TMDB_API_KEY)
    if not info:
        raise HTTPException(502, "TMDB lookup failed")
    conn = get_db()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "UPDATE movies SET tmdb_id=?, title=?, year=?, original_language=?, poster_path=?, updated_at=? WHERE id=?",
            (info["tmdb_id"], info["title"], info["year"], info["original_language"], info["poster_path"], now, movie_id),
        )
        conn.commit()
        return {"ok": True, "movie": info}
    finally:
        conn.close()


@app.post("/api/movies/{movie_id}/suggest")
def suggest(movie_id: int):
    conn = get_db()
    try:
        scan.suggest_tracks(conn, movie_id)
        tracks = conn.execute("SELECT * FROM tracks WHERE movie_id=? ORDER BY type, out_order", (movie_id,)).fetchall()
        return [dict(t) for t in tracks]
    finally:
        conn.close()


@app.post("/api/movies/{movie_id}/strip-names")
def strip_names(movie_id: int):
    conn = get_db()
    try:
        conn.execute("UPDATE tracks SET out_name='' WHERE movie_id=?", (movie_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.put("/api/movies/{movie_id}/config")
def save_config(movie_id: int, body: dict):
    """body: {tracks: [{id, keep, out_order, out_lang, out_default, out_forced, out_name}]}"""
    conn = get_db()
    try:
        for t in body.get("tracks", []):
            conn.execute(
                "UPDATE tracks SET keep=?, out_order=?, out_lang=?, out_default=?, out_forced=?, out_name=? "
                "WHERE id=? AND movie_id=?",
                (int(t["keep"]), int(t["out_order"]), t["out_lang"], int(t["out_default"]),
                 int(t["out_forced"]), t.get("out_name", ""), t["id"], movie_id),
            )
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("UPDATE movies SET status='ready', updated_at=? WHERE id=? AND status NOT IN ('working','clean')",
                     (now, movie_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _safe_name(s):
    return "".join(c for c in s if c not in '<>:"/\\|?*').strip()


@app.post("/api/movies/{movie_id}/rename")
def rename_movie(movie_id: int):
    conn = get_db()
    try:
        m = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not m:
            raise HTTPException(404, "not found")
        if not m["title"] or not m["year"]:
            raise HTTPException(400, "movie not TMDB-matched yet")
        running = conn.execute("SELECT id FROM jobs WHERE movie_id=? AND status='running'", (movie_id,)).fetchone()
        if running:
            raise HTTPException(409, "a job is running for this movie; rename after it finishes")

        target = _safe_name(f"{m['title']} ({m['year']})")
        old_folder = os.path.join(MEDIA_ROOT, m["folder"])
        new_folder = os.path.join(MEDIA_ROOT, target)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")

        # -- folder rename, committed on its own so a later file-rename failure
        #    can never leave the DB pointing at a folder that no longer exists
        if target != m["folder"]:
            if os.path.isdir(old_folder):
                if os.path.isdir(new_folder) and not scan.find_main_file(new_folder):
                    # target is a stub folder (no video) -- absorb it: move our
                    # files in (ours win on name collision), drop old folder and
                    # the stub's DB row so the movie appears once
                    # ponytail: os.replace won't merge colliding subdirs; none seen in this library
                    for f in os.listdir(old_folder):
                        os.replace(os.path.join(old_folder, f), os.path.join(new_folder, f))
                    os.rmdir(old_folder)
                    conn.execute("DELETE FROM movies WHERE folder=? AND id!=?", (target, movie_id))
                elif os.path.exists(new_folder):
                    raise HTTPException(409, f"target folder already exists: {target}")
                else:
                    os.rename(old_folder, new_folder)
            elif os.path.isdir(new_folder):
                pass  # already renamed on disk (e.g. earlier partial rename) -- adopt and resync DB
            else:
                raise HTTPException(404, f"folder missing on disk: {m['folder']}")
            conn.execute("UPDATE movies SET folder=?, updated_at=?, "
                         "output_file=REPLACE(COALESCE(output_file,''), ?, ?) WHERE id=?",
                         (target, now, old_folder, new_folder, movie_id))
            conn.execute("UPDATE movies SET output_file=NULL WHERE id=? AND output_file=''", (movie_id,))
            conn.execute("UPDATE tracks SET ext_path=REPLACE(ext_path, ?, ?) WHERE movie_id=? AND ext_path IS NOT NULL",
                         (old_folder, new_folder, movie_id))
            conn.commit()

        # -- file rename: normalize the main video to "Title (Year).ext".
        #    Once delete-original has removed the source, the main video is the
        #    job output, so this is also what strips ".remux"/".hevc" suffixes.
        cur_file = m["file"]
        if cur_file and not os.path.exists(os.path.join(new_folder, cur_file)):
            # DB points at a file that's gone (earlier partial rename / manual
            # change) -- recover by adopting the largest video in the folder
            cur_file = scan.find_main_file(new_folder)
        new_file = cur_file
        if cur_file:
            old_stem, ext = os.path.splitext(cur_file)
            new_file = target + ext
            if new_file != cur_file:
                dst = os.path.join(new_folder, new_file)
                if os.path.exists(dst):
                    raise HTTPException(409, f"file already exists: {new_file}")
                os.rename(os.path.join(new_folder, cur_file), dst)
            for f in os.listdir(new_folder):
                if f.startswith(old_stem) and f != new_file:
                    new_name = target + f[len(old_stem):]
                    if not os.path.exists(os.path.join(new_folder, new_name)):
                        os.rename(os.path.join(new_folder, f), os.path.join(new_folder, new_name))
            conn.execute("UPDATE tracks SET ext_path=REPLACE(ext_path, ?, ?) WHERE movie_id=? AND ext_path IS NOT NULL",
                         (os.path.join(new_folder, old_stem), os.path.join(new_folder, target), movie_id))
        conn.execute("UPDATE movies SET file=?, updated_at=? WHERE id=?", (new_file, now, movie_id))
        conn.commit()
        return {"ok": True, "folder": target, "file": new_file}
    finally:
        conn.close()


SAMPLE_RE = re.compile(r"\.sample\.rf(\d+)\.mkv$")


@app.get("/api/movies/{movie_id}/samples")
def list_samples(movie_id: int):
    """30s preview clips in the movie folder, with full-encode size estimates
    (sample bytes scaled to full duration -- video+audio scale linearly)."""
    conn = get_db()
    try:
        m = _movie_or_404(conn, movie_id)
    finally:
        conn.close()
    folder = os.path.join(MEDIA_ROOT, m["folder"])
    out = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        entries = []
    for f in entries:
        mm = SAMPLE_RE.search(f)
        if not mm:
            continue
        size = os.path.getsize(os.path.join(folder, f))
        est = int(size * m["duration"] / 30) if m["duration"] else None
        out.append({"file": f, "rf": int(mm.group(1)), "size_bytes": size, "est_full_bytes": est})
    return out


@app.get("/api/movies/{movie_id}/samples/file")
def get_sample(movie_id: int, file: str):
    conn = get_db()
    try:
        m = _movie_or_404(conn, movie_id)
    finally:
        conn.close()
    if os.path.basename(file) != file or not SAMPLE_RE.search(file):
        raise HTTPException(400, "not a sample file")
    path = os.path.join(MEDIA_ROOT, m["folder"], file)
    if not os.path.exists(path):
        raise HTTPException(404, "sample not found")
    return FileResponse(path, media_type="video/x-matroska", filename=file)


@app.delete("/api/movies/{movie_id}/samples")
def delete_sample(movie_id: int, file: str):
    conn = get_db()
    try:
        m = _movie_or_404(conn, movie_id)
    finally:
        conn.close()
    if os.path.basename(file) != file or not SAMPLE_RE.search(file):
        raise HTTPException(400, "not a sample file")
    path = os.path.join(MEDIA_ROOT, m["folder"], file)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return {"ok": True}


@app.post("/api/movies/{movie_id}/accept")
def accept_as_is(movie_id: int):
    """Mark the current file as final — quality already fine, no job needed."""
    conn = get_db()
    try:
        m = _movie_or_404(conn, movie_id)
        if not m["file"]:
            raise HTTPException(400, "no source file")
        busy = conn.execute("SELECT id FROM jobs WHERE movie_id=? AND status IN ('running','queued')",
                            (movie_id,)).fetchone()
        if busy:
            raise HTTPException(409, "a job is running or queued for this movie")
        conn.execute("UPDATE movies SET status='clean', updated_at=? WHERE id=?",
                     (time.strftime("%Y-%m-%dT%H:%M:%S"), movie_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- shows / episodes ----------
# Shows carry no status column -- it's an aggregate over their episodes,
# computed here rather than stored, so it can never drift out of sync with
# the episode rows that are the actual source of truth.

def _show_or_404(conn, show_id):
    s = conn.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
    if not s:
        raise HTTPException(404, "not found")
    return dict(s)


def _show_status(conn, show_id):
    rows = conn.execute(
        "SELECT status FROM episodes WHERE show_id=? AND excluded=0", (show_id,)
    ).fetchall()
    statuses = {r["status"] for r in rows}
    if not statuses:
        return "unprocessed"
    if statuses == {"clean"}:
        return "clean"
    if "encoding" in statuses or "cleaning" in statuses:
        return "encoding"
    if "error" in statuses:
        return "error"
    if statuses <= {"ready", "clean"}:
        return "ready" if "ready" in statuses else "clean"
    return "unprocessed"


def _show_summary(conn, s):
    d = dict(s)
    d["status"] = _show_status(conn, s["id"])
    return d


@app.get("/api/shows")
def list_shows(q: str | None = None):
    conn = get_db()
    try:
        sql = "SELECT * FROM shows WHERE 1=1"
        args = []
        if q:
            sql += " AND (title LIKE ? OR clean_title LIKE ? OR folder LIKE ?)"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY COALESCE(title, clean_title, folder)"
        rows = conn.execute(sql, args).fetchall()
        return [_show_summary(conn, r) for r in rows]
    finally:
        conn.close()


@app.get("/api/shows/{show_id}")
def get_show(show_id: int):
    conn = get_db()
    try:
        s = _show_or_404(conn, show_id)
        episodes = conn.execute(
            "SELECT * FROM episodes WHERE show_id=? ORDER BY season, episode", (show_id,)
        ).fetchall()
        d = _show_summary(conn, s)
        return {"show": d, "episodes": [dict(e) for e in episodes]}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/tmdb")
def set_show_tmdb(show_id: int, body: dict):
    tmdb_id = body.get("tmdb_id")
    if not tmdb_id:
        raise HTTPException(400, "tmdb_id required")
    info = scan.tmdb_get_tv(tmdb_id, TMDB_API_KEY)
    if not info:
        raise HTTPException(502, "TMDB lookup failed")
    conn = get_db()
    try:
        _show_or_404(conn, show_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "UPDATE shows SET tmdb_id=?, title=?, year=?, original_language=?, poster_path=?, updated_at=? WHERE id=?",
            (info["tmdb_id"], info["title"], info["year"], info["original_language"], info["poster_path"], now, show_id),
        )
        conn.commit()
        return {"ok": True, "show": info}
    finally:
        conn.close()


@app.put("/api/shows/{show_id}/episodes")
def set_excluded(show_id: int, body: dict):
    """body: {excluded: [episode_id, ...]} -- the full set of excluded episode
    ids for this show (not a diff); any episode not listed is included."""
    conn = get_db()
    try:
        _show_or_404(conn, show_id)
        excluded = set(int(i) for i in body.get("excluded", []))
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for r in conn.execute("SELECT id FROM episodes WHERE show_id=?", (show_id,)).fetchall():
            conn.execute("UPDATE episodes SET excluded=?, updated_at=? WHERE id=?",
                         (1 if r["id"] in excluded else 0, now, r["id"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/suggest")
def suggest_show(show_id: int):
    """Runs suggest_tracks per non-excluded episode -- never a copied plan,
    since sibling episodes routinely differ in track count/order (different
    rip sources per episode)."""
    conn = get_db()
    try:
        _show_or_404(conn, show_id)
        eps = conn.execute("SELECT id FROM episodes WHERE show_id=? AND excluded=0", (show_id,)).fetchall()
        for e in eps:
            scan.suggest_tracks(conn, e["id"], table="episodes", multi_audio=True)
        return {"ok": True, "count": len(eps)}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/jobs")
def launch_show_jobs(show_id: int, body: dict):
    """Enqueues one job per included, configured episode. Space is checked
    ONCE for the whole batch -- source+output coexist per episode until each
    finalizes, so a per-job check alone would under-count a 24-episode run."""
    job_kind = body.get("kind")
    if job_kind not in ("remux", "encode", "sample"):
        raise HTTPException(400, "kind must be remux|encode|sample")
    quality = int(body.get("quality") or 22)
    conn = get_db()
    try:
        _show_or_404(conn, show_id)
        eps = conn.execute(
            "SELECT id, size_bytes FROM episodes WHERE show_id=? AND excluded=0 AND status IN ('ready','clean')",
            (show_id,),
        ).fetchall()
        if not eps:
            raise HTTPException(400, "no configured, included episodes to run")
        already = conn.execute(
            "SELECT episode_id FROM jobs j JOIN episodes e ON e.id=j.episode_id "
            "WHERE e.show_id=? AND j.status IN ('running','queued')", (show_id,)
        ).fetchall()
        already_ids = {r["episode_id"] for r in already}
        todo = [e for e in eps if e["id"] not in already_ids]
        if not todo:
            raise HTTPException(409, "every included episode already has a job running or queued")
        need_bytes = sum((e["size_bytes"] or 0) for e in todo) if job_kind != "sample" else 2 * 10**9 * len(todo)
        if not jobs.has_space_for(MEDIA_ROOT, need_bytes):
            raise HTTPException(507, "not enough free disk space for this batch")
        job_ids = [_enqueue(conn, "episode", e["id"], job_kind, quality)["job_id"] for e in todo]
        return {"job_ids": job_ids, "count": len(job_ids)}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/rename")
def rename_show(show_id: int):
    """Folder -> "Title (Year)", then each episode's video (and sidecars) to
    its own S01E03-style out_base. Season-directory names are left as-is --
    Jellyfin reads season/episode numbers from the filename, so the directory
    name is cosmetic."""
    conn = get_db()
    try:
        s = _show_or_404(conn, show_id)
        if not s["title"] or not s["year"]:
            raise HTTPException(400, "show not TMDB-matched yet")
        running = conn.execute(
            "SELECT j.id FROM jobs j JOIN episodes e ON e.id=j.episode_id "
            "WHERE e.show_id=? AND j.status='running'", (show_id,)
        ).fetchone()
        if running:
            raise HTTPException(409, "a job is running for this show; rename after it finishes")

        target = _safe_name(f"{s['title']} ({s['year']})")
        old_folder = os.path.join(MEDIA_ROOT, s["folder"])
        new_folder = os.path.join(MEDIA_ROOT, target)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")

        if target != s["folder"]:
            if not os.path.isdir(old_folder):
                raise HTTPException(404, f"folder missing on disk: {s['folder']}")
            if os.path.exists(new_folder):
                raise HTTPException(409, f"target folder already exists: {target}")
            os.rename(old_folder, new_folder)
            conn.execute("UPDATE shows SET folder=?, updated_at=? WHERE id=?", (target, now, show_id))
            conn.commit()

        for e in conn.execute("SELECT * FROM episodes WHERE show_id=?", (show_id,)).fetchall():
            if not e["file"]:
                continue
            ep_folder = os.path.join(new_folder, e["folder"]) if e["folder"] else new_folder
            out_base = _safe_name(f"{s['title']} - S{(e['season'] or 0):02d}E{(e['episode'] or 0):02d}")
            old_stem, ext = os.path.splitext(e["file"])
            new_file = out_base + ext
            if new_file != e["file"] and os.path.exists(os.path.join(ep_folder, e["file"])):
                dst = os.path.join(ep_folder, new_file)
                if not os.path.exists(dst):
                    os.rename(os.path.join(ep_folder, e["file"]), dst)
                    for f in os.listdir(ep_folder):
                        if f.startswith(old_stem) and f != new_file:
                            new_name = out_base + f[len(old_stem):]
                            if not os.path.exists(os.path.join(ep_folder, new_name)):
                                os.rename(os.path.join(ep_folder, f), os.path.join(ep_folder, new_name))
                    conn.execute(
                        "UPDATE tracks SET ext_path=REPLACE(ext_path, ?, ?) WHERE episode_id=? AND ext_path IS NOT NULL",
                        (os.path.join(ep_folder, old_stem), os.path.join(ep_folder, out_base), e["id"]),
                    )
                    conn.execute("UPDATE episodes SET file=?, updated_at=? WHERE id=?", (new_file, now, e["id"]))
        conn.commit()
        return {"ok": True, "folder": target}
    finally:
        conn.close()


# ---------- episode wrappers (thin, over the shared owner-agnostic functions) ----------

@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: int):
    conn = get_db()
    try:
        e = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not e:
            raise HTTPException(404, "not found")
        tracks = conn.execute(
            "SELECT * FROM tracks WHERE episode_id=? ORDER BY type, keep DESC, out_order, mkv_id", (episode_id,)
        ).fetchall()
        show = conn.execute("SELECT * FROM shows WHERE id=?", (e["show_id"],)).fetchone()
        d = dict(e)
        d["show"] = dict(show) if show else None
        return {"episode": d, "tracks": [dict(t) for t in tracks]}
    finally:
        conn.close()


@app.post("/api/episodes/{episode_id}/suggest")
def suggest_episode(episode_id: int):
    conn = get_db()
    try:
        _owner_or_404(conn, "episode", episode_id)
        scan.suggest_tracks(conn, episode_id, table="episodes", multi_audio=True)
        tracks = conn.execute(
            "SELECT * FROM tracks WHERE episode_id=? ORDER BY type, out_order", (episode_id,)
        ).fetchall()
        return [dict(t) for t in tracks]
    finally:
        conn.close()


@app.post("/api/episodes/{episode_id}/strip-names")
def strip_names_episode(episode_id: int):
    conn = get_db()
    try:
        _owner_or_404(conn, "episode", episode_id)
        conn.execute("UPDATE tracks SET out_name='' WHERE episode_id=?", (episode_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.put("/api/episodes/{episode_id}/config")
def save_config_episode(episode_id: int, body: dict):
    conn = get_db()
    try:
        _owner_or_404(conn, "episode", episode_id)
        for t in body.get("tracks", []):
            conn.execute(
                "UPDATE tracks SET keep=?, out_order=?, out_lang=?, out_default=?, out_forced=?, out_name=? "
                "WHERE id=? AND episode_id=?",
                (int(t["keep"]), int(t["out_order"]), t["out_lang"], int(t["out_default"]),
                 int(t["out_forced"]), t.get("out_name", ""), t["id"], episode_id),
            )
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "UPDATE episodes SET status='ready', updated_at=? WHERE id=? AND status NOT IN ('working','clean')",
            (now, episode_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/episodes/{episode_id}/command")
def preview_command_episode(episode_id: int, kind: str, quality: int = 22):
    conn = get_db()
    try:
        return _preview_command(conn, "episode", episode_id, kind, quality)
    finally:
        conn.close()


@app.post("/api/episodes/{episode_id}/jobs")
def launch_job_episode(episode_id: int, body: dict):
    conn = get_db()
    try:
        return _enqueue(conn, "episode", episode_id, body.get("kind"), int(body.get("quality") or 22))
    finally:
        conn.close()


@app.post("/api/episodes/{episode_id}/delete-original")
def delete_original_episode(episode_id: int):
    conn = get_db()
    try:
        return _delete_original(conn, "episode", episode_id)
    finally:
        conn.close()


# ---------- power / throttle ----------

@app.get("/api/power")
def get_power():
    conn = get_db()
    try:
        p = _power_state(conn)
        p["effective_quota"] = _current_quota(conn)
        return p
    finally:
        conn.close()


@app.put("/api/power")
def set_power(body: dict):
    """body: {mode?: auto|full|throttle, work_hours?: "9-23"}. Applies the new
    quota to running jobs immediately — no waiting for the 30s ticker."""
    conn = get_db()
    try:
        mode = body.get("mode")
        if mode is not None:
            if mode not in ("auto", "full", "throttle"):
                raise HTTPException(400, "mode must be auto|full|throttle")
            _set_setting(conn, "power_mode", mode)
        hours = body.get("work_hours")
        if hours is not None:
            if not re.fullmatch(r"([01]?\d|2[0-3])-([01]?\d|2[0-3])", hours):
                raise HTTPException(400, 'work_hours must look like "9-23" (hours 0-23)')
            _set_setting(conn, "work_hours", hours)
        conn.commit()
        quota = _current_quota(conn)
        for r in conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
            jobs.set_cpu_quota(r["id"], quota)
        p = _power_state(conn)
        p["effective_quota"] = quota
        return p
    finally:
        conn.close()


# ---------- commands / jobs ----------

def _kept_tracks(conn, kind, owner_id):
    col = _owner_col(kind)
    rows = conn.execute(f"SELECT * FROM tracks WHERE {col}=? AND keep=1", (owner_id,)).fetchall()
    return [dict(r) for r in rows]


def _all_tracks(conn, kind, owner_id):
    col = _owner_col(kind)
    return [dict(r) for r in conn.execute(f"SELECT * FROM tracks WHERE {col}=?", (owner_id,)).fetchall()]


def _movie_or_404(conn, movie_id):
    m = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not m:
        raise HTTPException(404, "not found")
    return dict(m)


def _owner_or_404(conn, kind, owner_id):
    owner = _owner_info(conn, kind, owner_id)
    if not owner:
        raise HTTPException(404, "not found")
    return owner


def _build_job_cmd(conn, kind, owner, job_kind, quality):
    """Returns (cmd_str, out_path) for remux/encode/sample. `owner` is the
    uniform dict from _owner_info; `kind` is 'movie'|'episode'."""
    title = owner["title_display"]
    folder_path = os.path.join(MEDIA_ROOT, owner["folder"])
    in_path = os.path.join(folder_path, owner["file"])
    tracks = _all_tracks(conn, kind, owner["id"])
    if job_kind == "remux":
        out_path = os.path.join(folder_path, f"{owner['out_base']}.remux.mkv")
        return shlex.join(commands.build_mkvmerge_remux(tracks, title, in_path, out_path)), out_path
    if job_kind == "sample":
        out_path = os.path.join(folder_path, f"{owner['out_base']}.sample.rf{quality}.mkv")
        start = int((owner["duration"] or 1200) / 2)  # mid-movie: representative scene
        hb_argv, _ = commands.build_handbrake_encode(tracks, in_path, out_path,
                                                      quality=quality, sample_start=start)
        # throwaway preview: skip the mkvpropedit metadata pass
        return shlex.join(hb_argv), out_path
    out_path = os.path.join(folder_path, f"{owner['out_base']}.hevc.mkv")
    hb_argv, sub_order = commands.build_handbrake_encode(tracks, in_path, out_path, quality=quality)
    audio_order = sorted([t for t in tracks if t["type"] == "audio" and t["keep"]], key=lambda t: t["out_order"])
    vtrack = next((t for t in tracks if t["type"] == "video"), None)
    mkvpe = commands.build_mkvpropedit_chain(out_path, title, audio_order, sub_order,
                                             video_lang=vtrack["out_lang"] if vtrack else None)
    return shlex.join(hb_argv) + " && " + shlex.join(mkvpe), out_path


def _preview_command(conn, kind, owner_id, job_kind, quality):
    if job_kind not in ("remux", "encode", "sample"):
        raise HTTPException(400, "kind must be remux|encode|sample")
    owner = _owner_or_404(conn, kind, owner_id)
    cmd_str, _ = _build_job_cmd(conn, kind, owner, job_kind, quality)
    return {"cmd": cmd_str}


@app.get("/api/movies/{movie_id}/command")
def preview_command(movie_id: int, kind: str, quality: int = 22):
    conn = get_db()
    try:
        return _preview_command(conn, "movie", movie_id, kind, quality)
    finally:
        conn.close()


def _enqueue(conn, kind, owner_id, job_kind, quality):
    if job_kind not in ("remux", "encode", "sample"):
        raise HTTPException(400, "kind must be remux|encode|sample")
    owner = _owner_or_404(conn, kind, owner_id)
    if not owner["file"]:
        raise HTTPException(400, "no source file")
    col = _owner_col(kind)
    mine = conn.execute(
        f"SELECT id FROM jobs WHERE {col}=? AND status IN ('running','queued')", (owner_id,)
    ).fetchone()
    if mine:
        raise HTTPException(409, "a job is already running or queued for this item")

    need_bytes = 2 * 10**9 if job_kind == "sample" else owner["size_bytes"]
    if not jobs.has_space_for(MEDIA_ROOT, need_bytes):
        raise HTTPException(507, "not enough free disk space for this operation")

    # global single runner: one job hammers the CPU at a time, rest queue up
    busy = conn.execute("SELECT id FROM jobs WHERE status='running' LIMIT 1").fetchone()
    cmd_str, out_path = _build_job_cmd(conn, kind, owner, job_kind, quality)
    id_kwargs = {"movie_id": owner_id} if kind == "movie" else {"episode_id": owner_id}
    job_id = jobs.start_job(conn, job_kind, cmd_str, LOG_DIR,
                            cpu_quota=_current_quota(conn), queued=bool(busy), **id_kwargs)
    if job_kind != "sample":  # samples never touch owner state
        status = "cleaning" if job_kind == "remux" else "encoding"
        _set_owner_status(conn, kind, owner_id, status, output_file=out_path)
        conn.commit()
    return {"job_id": job_id, "queued": bool(busy)}


@app.post("/api/movies/{movie_id}/jobs")
def launch_job(movie_id: int, body: dict):
    conn = get_db()
    try:
        return _enqueue(conn, "movie", movie_id, body.get("kind"), int(body.get("quality") or 22))
    finally:
        conn.close()


def _poll_and_finalize(conn, job_id):
    """Advances a job's progress/status, and on first transition to done/failed
    runs verify + updates the parent movie's status. Idempotent — safe to call
    from any polling path (list, single, or the header job strip)."""
    job = jobs.poll_job(conn, job_id)
    if not job:
        return None
    job = dict(job)
    if job["kind"] == "sample":
        # samples have no owner state to advance; just close out the job row
        if job["status"] == "done":
            conn.execute("UPDATE jobs SET status='verified' WHERE id=?", (job_id,))
            conn.commit()
            job["status"] = "verified"
        return job
    kind, oid = _job_owner_kind_id(job)
    if job["status"] == "done":
        _verify_and_finalize(conn, kind, oid, job)
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    elif job["status"] == "failed":
        _set_owner_status(conn, kind, oid, "error")
        conn.commit()
    return job


def _job_eta(j):
    """Last ETA HandBrake printed, parsed from the log tail (running jobs only)."""
    if j.get("status") == "running" and j.get("log_path"):
        m = jobs.ETA_RE.findall(jobs.tail(j["log_path"]))
        return m[-1] if m else None
    return None


@app.get("/api/jobs")
def list_jobs():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
        out = []
        for r in rows:
            if r["status"] in ("running", "done"):
                out.append(_poll_and_finalize(conn, r["id"]))
            else:
                out.append(dict(r))
        movie_titles = {m["id"]: (f"{m['title']} ({m['year']})" if m["title"] else m["clean_title"] or m["folder"])
                        for m in conn.execute("SELECT id, title, year, clean_title, folder FROM movies")}
        ep_titles = {}
        for e in conn.execute(
            "SELECT e.id, e.season, e.episode, s.title, s.clean_title FROM episodes e "
            "JOIN shows s ON s.id = e.show_id"
        ):
            show_title = e["title"] or e["clean_title"]
            ep_titles[e["id"]] = f"{show_title} S{(e['season'] or 0):02d}E{(e['episode'] or 0):02d}"
        for j in out:
            if j["movie_id"]:
                j["movie_title"] = movie_titles.get(j["movie_id"], f"movie {j['movie_id']}")
            else:
                j["movie_title"] = ep_titles.get(j["episode_id"], f"episode {j['episode_id']}")
            j["eta"] = _job_eta(j)
        return out
    finally:
        conn.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    conn = get_db()
    try:
        job = _poll_and_finalize(conn, job_id)
        if not job:
            raise HTTPException(404, "not found")
        job["eta"] = _job_eta(job)
        return job
    finally:
        conn.close()


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    try:
        return {"reclaimed_bytes": int(_get_setting(conn, "reclaimed_bytes", "0"))}
    finally:
        conn.close()


def _verify_and_finalize(conn, kind, owner_id, job):
    owner = _owner_info(conn, kind, owner_id)
    kept = _kept_tracks(conn, kind, owner_id)
    ok, msg = jobs.verify_output(owner["output_file"], kept, owner["duration"])
    if ok:
        conn.execute("UPDATE jobs SET status='verified' WHERE id=?", (job["id"],))
        _set_owner_status(conn, kind, owner_id, "clean")
    else:
        conn.execute("UPDATE jobs SET status='failed', exit_code=-1 WHERE id=?", (job["id"],))
        _set_owner_status(conn, kind, owner_id, "error")
    conn.commit()
    return ok, msg


def _delete_original(conn, kind, owner_id):
    owner = _owner_or_404(conn, kind, owner_id)
    if owner["status"] != "clean":
        raise HTTPException(400, "not verified clean yet")
    folder = os.path.join(MEDIA_ROOT, owner["folder"])

    # Resolve the job output BEFORE deleting anything. If there is no
    # recorded output (already finalized) or it can't be found on disk,
    # abort -- otherwise a repeat click would delete the only copy left.
    out = owner["output_file"]
    if not out:
        raise HTTPException(400, "no job output recorded; original was already deleted")
    if not os.path.exists(out):
        # output_file can hold a pre-rename folder path; the file itself
        # moved with the folder, so look for its basename in the current one
        cand = os.path.join(folder, os.path.basename(out))
        if not os.path.exists(cand):
            raise HTTPException(404, "job output file not found on disk; refusing to delete original")
        out = cand

    old_path = os.path.join(folder, owner["file"]) if owner["file"] else None
    freed = 0
    if old_path and os.path.exists(old_path) and old_path != out:
        freed = os.path.getsize(old_path) - os.path.getsize(out)
        os.remove(old_path)
    col = _owner_col(kind)
    for t in conn.execute(f"SELECT ext_path FROM tracks WHERE {col}=? AND ext_path IS NOT NULL", (owner_id,)):
        if t["ext_path"] and os.path.exists(t["ext_path"]):
            os.remove(t["ext_path"])
    # old source/subs are gone now, so keep only the job output plus any
    # sibling episode's own files when sweeping junk (shared season folder)
    keep = {os.path.basename(out)}
    if kind == "episode":
        for r in conn.execute(
            "SELECT file, output_file FROM episodes WHERE show_id=? AND folder=? AND id!=?",
            (owner["show_id"], owner["_raw_folder"], owner_id),
        ).fetchall():
            if r["file"]:
                keep.add(r["file"])
            if r["output_file"]:
                keep.add(os.path.basename(r["output_file"]))
    for junk in scan.find_movie_junk(folder, keep):
        try:
            os.remove(os.path.join(folder, junk))
        except OSError:
            pass
    final_name = f"{owner['out_base']}.mkv"
    final_path = os.path.join(folder, final_name)
    if out != final_path:
        os.rename(out, final_path)
    table = _owner_table(kind)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(f"UPDATE {table} SET file=?, output_file=NULL, updated_at=? WHERE id=?",
                 (final_name, now, owner_id))
    if freed > 0:
        total = int(_get_setting(conn, "reclaimed_bytes", "0")) + freed
        _set_setting(conn, "reclaimed_bytes", str(total))
    conn.commit()
    return {"ok": True, "file": final_name}


@app.post("/api/movies/{movie_id}/delete-original")
def delete_original(movie_id: int):
    conn = get_db()
    try:
        return _delete_original(conn, "movie", movie_id)
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8500, reload=False)
