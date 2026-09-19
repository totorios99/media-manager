import asyncio
import json
import os
import urllib.parse
import subprocess
import shutil
import re
import shlex
import signal
import sqlite3
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import base64
import urllib.error
import urllib.request

import commands
import graft
import jobs
import scan
from scan import _now

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media/hdd1/Movies")
# TV shows live in their own root, separate from MEDIA_ROOT's movies. Falls
# back to MEDIA_ROOT so a single-root setup keeps working unchanged.
SHOWS_ROOT = os.environ.get("MM_SHOWS_ROOT", MEDIA_ROOT)
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
    hdr TEXT, atmos INTEGER DEFAULT 0,
    status TEXT DEFAULT 'unprocessed',
    output_file TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT UNIQUE NOT NULL,
    clean_title TEXT, guess_year INTEGER,
    tmdb_id INTEGER, title TEXT, year INTEGER,
    original_language TEXT, poster_path TEXT,
    track_config TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    season INTEGER, episode INTEGER,
    folder TEXT NOT NULL,
    file TEXT,
    container_title TEXT, video_codec TEXT, width INTEGER, height INTEGER,
    bitrate INTEGER, duration REAL, size_bytes INTEGER, hdr TEXT, atmos INTEGER DEFAULT 0,
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
    sdh_flag INTEGER DEFAULT 0, commentary_flag INTEGER DEFAULT 0,
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
    # 30s was not enough: verifying a 30 GB 4K remux reads the whole file back
    # over a 1.5 Gb/s USB link while transmission writes to the same SMR disk,
    # and every enqueue in that window died with "database is locked" -- 36 in
    # one run. Waiting is always better than failing here; nothing holds a write
    # lock for a long stretch, the contention is pure disk starvation.
    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _add_missing_columns(conn):
    """Additive columns, checked on EVERY startup.

    These used to live at the end of _migrate, past its `user_version >= 1`
    early return -- so once a database was stamped, no column added later ever
    reached it. `animation` silently never appeared and suggest_tracks died with
    "no such column". Column presence is the only guard these need: adding one
    is idempotent and cheap, unlike the table rebuild _migrate gates.

    Must still run AFTER that rebuild: it recreates `tracks` from
    _TRACKS_OLD_COLS and renames it over the top, which would drop them again.
    """
    for table, col, decl in (
        ("movies", "atmos", "INTEGER DEFAULT 0"),
        ("episodes", "atmos", "INTEGER DEFAULT 0"),
        ("shows", "animation", "INTEGER DEFAULT 0"),
        ("movies", "animation", "INTEGER DEFAULT 0"),
        ("jobs", "auto_finalize", "INTEGER DEFAULT 0"),
        ("tracks", "sdh_flag", "INTEGER DEFAULT 0"),
        ("tracks", "commentary_flag", "INTEGER DEFAULT 0"),
    ):
        if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            conn.commit()


def _migrate(conn):
    """One-shot rebuild for pre-TV databases: tracks.movie_id was NOT NULL, which
    SQLite can't alter away, so the table must be recreated. Gated on
    PRAGMA user_version (not a column sniff) so a crash mid-migration can't be
    mistaken for "done" on the next startup."""
    shows_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shows'").fetchone()
    if shows_exists:
        show_cols = {r["name"] for r in conn.execute("PRAGMA table_info(shows)")}
        if "track_config" not in show_cols:
            conn.execute("ALTER TABLE shows ADD COLUMN track_config TEXT")
            conn.commit()
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
    # AFTER the schema script, not before: on a fresh database the tables do not
    # exist yet when this runs, so every column it adds is silently lost. That
    # is why a new DB came up without `animation` while `atmos` survived -- the
    # latter is spelled out in SCHEMA, the former only here.
    _add_missing_columns(conn)
    conn.commit()
    conn.close()


app = FastAPI()

scan_state = {"running": False, "done": 0, "total": 0, "current": ""}

# How often the ticker advances jobs, and how often /api/events re-reads state.
# Encode progress moves slowly; 2s is well under the "did that button work?"
# threshold without making the log tail hot.
TICK_SECONDS = 2
QUOTA_EVERY_N_TICKS = 15  # -> 30s, the cadence quota re-application always had


# ---------- owner abstraction (movie or episode) ----------
# A "job owner" is either a movie or an episode. Both are one metadata row +
# one file; everything below (job building, verification, junk sweep) only
# needs a uniform view of that, so the rest of the file works on `kind`
# ('movie'|'episode') + a plain dict, never branching on the two schemas again.

def _owner_col(kind):
    return "movie_id" if kind == "movie" else "episode_id"


def _owner_table(kind):
    return "movies" if kind == "movie" else "episodes"


def _root_for(kind):
    """Movies live under MEDIA_ROOT, episodes under SHOWS_ROOT -- separate
    filesystem roots, falling back to the same one when unconfigured."""
    return MEDIA_ROOT if kind == "movie" else SHOWS_ROOT


def _owner_info(conn, kind, owner_id):
    """Uniform dict for a movie or episode row: folder (already composed with
    any show/season prefix, relative to MEDIA_ROOT for a movie or SHOWS_ROOT
    for an episode -- see _root_for), file, title_display (for --title
    metadata), out_base (filename stem), duration, size_bytes, status,
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
    # The filename stem must match what rename_show writes, year included --
    # finalizing an episode used to drop the year and leave it named
    # differently from every sibling that had only been renamed.
    show_year = show["year"] if show else None
    named = f"{show_title} ({show_year})" if show_year else show_title
    d["out_base"] = _safe_name(f"{named} - S{(d['season'] or 0):02d}E{(d['episode'] or 0):02d}")
    return d


def _set_owner_status(conn, kind, owner_id, status, output_file=None):
    table = _owner_table(kind)
    now = _now()
    if output_file is not None:
        conn.execute(f"UPDATE {table} SET status=?, output_file=?, updated_at=? WHERE id=?",
                     (status, output_file, now, owner_id))
    else:
        conn.execute(f"UPDATE {table} SET status=?, updated_at=? WHERE id=?", (status, now, owner_id))


def _job_owner_kind_id(job):
    return ("movie", job["movie_id"]) if job["movie_id"] else ("episode", job["episode_id"])


def _reconcile_staging():
    """Pick up staged imports the process lost track of.

    _STAGED lives in memory, so a restart mid-flight forgets that a title came
    from staging: the normalised file may already be in place with the staging
    copy still there, or the hidden import may be waiting with no job to remux
    it. Either way nobody would ever finish it, and staging is invisible to
    Jellyfin -- a silent failure, which is worse than the visible one it
    replaced. This is the price of staging and it has to be paid at startup."""
    if not os.path.isdir(STAGING_ROOT):
        return
    conn = get_db()
    try:
        for folder in sorted(os.listdir(STAGING_ROOT)):
            if folder.startswith("."):
                continue
            dest = os.path.join(MEDIA_ROOT, folder)
            row = conn.execute("SELECT id, file FROM movies WHERE folder=?", (folder,)).fetchone()
            final = row and row["file"] and not row["file"].startswith(".")                 and os.path.exists(os.path.join(dest, row["file"]))
            if final:
                _STAGED[row["id"]] = folder          # so _finish_staging acts
                _finish_staging(conn, row["id"])
                continue
            hidden = os.path.isdir(dest) and any(
                f.startswith(".") and f.lower().endswith(scan.VIDEO_EXT)
                for f in os.listdir(dest))
            if hidden and row:
                _STAGED[row["id"]] = folder
                print(f"[staging] {folder!r}: import adoptado sin terminar, re-encolando",
                      flush=True)
                try:
                    scan.suggest_tracks(conn, row["id"], "movies")
                    job = _enqueue(conn, "movie", row["id"], "remux", 22)
                    _mark_auto_finalize(conn, job.get("job_id"))
                except Exception as e:
                    _notify("Staging sin terminar", f"{folder}: no pude re-encolar: {e}",
                            tags="warning", priority=4)
    except Exception as e:
        print(f"[staging] reconciliación falló: {e}", flush=True)
    finally:
        conn.close()


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
                now = _now()
                kind, oid = _job_owner_kind_id(job)
                conn.execute("UPDATE jobs SET status='failed', finished_at=? WHERE id=?", (now, job["id"]))
                _set_owner_status(conn, kind, oid, "error")
                conn.commit()
                job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (r["id"],)).fetchone())
            # propedit's output_file is the source itself; there is no partial
            # output to clear and removing it would destroy the only copy
            if job and job["status"] == "failed" and job["kind"] not in ("sample", "propedit"):
                kind, oid = _job_owner_kind_id(job)
                owner = _owner_info(conn, kind, oid)
                if owner and owner["output_file"]:
                    try:
                        os.remove(owner["output_file"])
                    except OSError:
                        pass
    finally:
        conn.close()


# Set the moment a shutdown signal arrives, so open /api/events streams end
# themselves. It has to be the SIGNAL and not the "shutdown" lifespan event:
# uvicorn waits for in-flight requests FIRST and only runs lifespan shutdown
# afterwards, so a handler there never gets the chance to release the stream
# that is holding the shutdown up.
_shutting_down = asyncio.Event()


def _install_shutdown_signal():
    loop = asyncio.get_running_loop()
    previous = {}

    def handler(signum, frame):
        loop.call_soon_threadsafe(_shutting_down.set)
        prev = previous.get(signum)
        if callable(prev):  # uvicorn's own handler still has to run
            prev(signum, frame)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # not the main thread, or a platform without these signals


@app.on_event("startup")
def _startup():
    os.makedirs(LOG_DIR, exist_ok=True)
    init_db()
    _reap_stale_jobs()
    _reconcile_staging()
    _install_shutdown_signal()
    threading.Thread(target=_job_ticker, daemon=True).start()


def _job_ticker():
    """Server-side job driver (UI polling only works while a browser is open):
    advances running jobs, starts the next queued one, re-applies the CPU
    quota schedule live to running scopes.

    This is the ONLY place jobs are advanced on a timer. /api/events is a
    read-only mirror of what this thread has already written, so finalization
    can't race between the ticker and every open browser tab."""
    n = 0
    while True:
        time.sleep(TICK_SECONDS)
        n += 1
        try:
            conn = get_db()
            try:
                for r in conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
                    _poll_and_finalize(conn, r["id"])
                if not conn.execute("SELECT id FROM jobs WHERE status='running' LIMIT 1").fetchone():
                    nxt = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                    if nxt:
                        jobs.launch_queued(conn, dict(nxt), LOG_DIR, cpu_quota=_current_quota(conn))
                # Quota stays on the old 30s cadence: every application is a
                # `systemctl set-property` fork per running scope, and the tick
                # is now 15x faster. Live changes still apply instantly --
                # set_power() pushes them itself, and launch passes the quota in.
                if n % QUOTA_EVERY_N_TICKS == 0:
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
        separate_roots = SHOWS_ROOT != MEDIA_ROOT
        scan.scan_library(conn, MEDIA_ROOT, TMDB_API_KEY, progress_cb=cb, include_shows=not separate_roots)
        if separate_roots:
            scan.scan_shows_root(conn, SHOWS_ROOT, TMDB_API_KEY, progress_cb=cb)
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
    """abs folder path -> [junk filenames], plus synthetic "__top_level__" /
    "__top_level_shows__" entries for stray ._* AppleDouble files sitting
    directly under MEDIA_ROOT / SHOWS_ROOT. Keyed by absolute path (not a
    folder name relative to some assumed root) since movies and episodes can
    now live under two different roots."""
    result = {}
    top_junk = [e.name for e in os.scandir(MEDIA_ROOT) if e.is_file() and e.name.startswith("._")]
    if top_junk:
        result[("__top_level__", MEDIA_ROOT)] = top_junk
    if SHOWS_ROOT != MEDIA_ROOT:
        show_top_junk = [e.name for e in os.scandir(SHOWS_ROOT) if e.is_file() and e.name.startswith("._")]
        if show_top_junk:
            result[("__top_level_shows__", SHOWS_ROOT)] = show_top_junk
    for row in conn.execute("SELECT id FROM movies WHERE file IS NOT NULL").fetchall():
        owner = _owner_info(conn, "movie", row["id"])
        folder = os.path.join(MEDIA_ROOT, owner["folder"])
        junk = scan.find_movie_junk(folder, _keep_files_for(conn, "movie", owner))
        if junk:
            result[(owner["folder"], folder)] = junk
    # episodes: sweep once per distinct (show, folder) pair, not once per
    # episode -- the union keep-set already covers every sibling
    seen = set()
    for row in conn.execute("SELECT id, show_id, folder FROM episodes WHERE file IS NOT NULL").fetchall():
        key = (row["show_id"], row["folder"])
        if key in seen:
            continue
        seen.add(key)
        owner = _owner_info(conn, "episode", row["id"])
        folder = os.path.join(SHOWS_ROOT, owner["folder"])
        junk = scan.find_movie_junk(folder, _keep_files_for(conn, "episode", owner))
        if junk:
            result[(owner["folder"], folder)] = junk
    return result


@app.get("/api/junk/preview")
def junk_preview():
    conn = get_db()
    try:
        result = _junk_scan(conn)
        return {"total": sum(len(v) for v in result.values()),
                "folders": {label: files for (label, _abs), files in result.items()}}
    finally:
        conn.close()


@app.post("/api/junk/apply")
def junk_apply():
    conn = get_db()
    try:
        result = _junk_scan(conn)
        removed, errors = 0, []
        for (label, base), files in result.items():
            for f in files:
                try:
                    os.remove(os.path.join(base, f))
                    removed += 1
                except OSError as e:
                    errors.append(f"{label}/{f}: {e}")
        return {"removed": removed, "errors": errors}
    finally:
        conn.close()


# ---------- movies ----------

# bitrate ceiling per resolution class (Mbps): above it a heavy encode buys real
# space; at or under it the storage win doesn't justify a lossy re-encode.
# The floor is the other end -- under it the bitrate is thin for the resolution,
# so the file wants a better source, not a shrink.
# ponytail: fixed thresholds; make settings if they ever need tuning
# resolution class -> (cap, floor) in Mbps
# The UHD cap was 25, matching Radarr's preferred size of 187 MB/min. It moved to
# 32 because that is where the return collapses: the 19 titles sitting between
# 25 and 32 hold 0.56 TB and chasing all of them back down would return 59 GB --
# about 3 GB each, for a download, a verification and a remux apiece. Above 32
# the recovery per title is worth the work. FHD and SD are unchanged: at 1080p,
# 15 Mbps is already generous and raising it would only hide genuinely fat files.
_BANDS = {"uhd": (32, 15), "fhd": (15, 8), "sd": (8, 4)}
# Those floors are h264 numbers. HEVC/AV1/VP9 hold the same picture at roughly
# 60% of the bitrate, so a codec-blind floor called 116 of 178 4K HEVC files
# "thin" when they were fine -- 75% of the library came back `lean` and the
# signal was useless. The cap stays codec-blind on purpose: a 40 Mbps file is
# worth re-encoding whatever wrote it.
MODERN_CODEC_FLOOR = 0.6
_MODERN_CODECS = ("hevc", "h265", "av1", "vp9")


def _res_class(w, h):
    """'uhd' | 'fhd' | 'sd'. Width first: a 2.39:1 scope UHD is 3840x1608, and a
    height-only test rates it FHD -- then 23 Mbps looks bloated and every scope
    4K begs for an encode."""
    if w >= 3000 or h >= 2000:
        return "uhd"
    if w >= 1800 or h >= 1000:
        return "fhd"
    return "sd"


def _quality(d):
    """Where the bitrate sits for its resolution, or None with no data.

    {'tier': 'bloated'|'ideal'|'lean', 'mbps', 'cap', 'floor', 'res'}
    Advisory only — never blocks a job."""
    br, w, h = d.get("bitrate"), d.get("width") or 0, d.get("height") or 0
    if not br:
        return None
    res = _res_class(w, h)
    cap, floor = _BANDS[res]
    codec = (d.get("video_codec") or "").lower()
    if any(c in codec for c in _MODERN_CODECS):
        floor = round(floor * MODERN_CODEC_FLOOR, 1)
    mbps = br / 1e6
    tier = "bloated" if mbps > cap else ("lean" if mbps < floor else "ideal")
    return {"tier": tier, "mbps": round(mbps, 1), "cap": cap, "floor": floor, "res": res}


def _movie_summary(row, dup_ids):
    d = dict(row)
    d["dup"] = d["id"] in dup_ids
    d["quality"] = _quality(d)
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
        m["quality"] = _quality(m)
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
        now = _now()
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
        now = _now()
        conn.execute("UPDATE movies SET status='ready', updated_at=? WHERE id=? AND status NOT IN ('working','clean')",
                     (now, movie_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _safe_name(s):
    return "".join(c for c in s if c not in '<>:"/\\|?*').strip()


def _canonical_name(f, old_stem, new_base):
    """Re-stem a sibling file from `old_stem` to `new_base`, keeping its suffix.

    A file already carrying `new_base` comes back unchanged: a job output named
    "Title (Year).hevc.mkv" written next to a source stem of "Title" is a prefix
    match, and blindly re-stemming it appends the year a second time."""
    if f.startswith(new_base) or not f.startswith(old_stem):
        return f
    return new_base + f[len(old_stem):]


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
        now = _now()

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
                new_name = _canonical_name(f, old_stem, target)
                if new_name != f and not os.path.exists(os.path.join(new_folder, new_name)):
                    os.rename(os.path.join(new_folder, f), os.path.join(new_folder, new_name))
            conn.execute("UPDATE tracks SET ext_path=REPLACE(ext_path, ?, ?) WHERE movie_id=? AND ext_path IS NOT NULL",
                         (os.path.join(new_folder, old_stem), os.path.join(new_folder, target), movie_id))
            # the job output is one of those files, so output_file has to follow it
            # or delete-original can't find the copy it must keep and refuses to run
            if m["output_file"]:
                conn.execute("UPDATE movies SET output_file=? WHERE id=?",
                             (os.path.join(new_folder,
                                           _canonical_name(os.path.basename(m["output_file"]), old_stem, target)),
                              movie_id))
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
                     (_now(), movie_id))
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
        now = _now()
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
        now = _now()
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


def _episode_track_sig_map(conn, episode_id):
    """{sig: track_row} for one episode. Raises via caller if a sig repeats --
    ambiguous tracks can't be matched by identity."""
    # video is always kept (see tracksHtml in the UI) and its "name" is really
    # the episode's own title burned into the container -- unique per episode,
    # never a useful identity for a show-wide config. Audio/subtitles only.
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM tracks WHERE episode_id=? AND type != 'video'", (episode_id,)
    )]
    sigs = {}
    ambiguous = set()
    for r in rows:
        sig = scan.track_sig(r)
        if sig in sigs:
            ambiguous.add(sig)
        sigs[sig] = r
    return sigs, ambiguous


def _show_config_conflicts(conn, show_id, config):
    """Dry-run diff of the stored config against every non-excluded episode.
    Returns (clean: [{episode_id, track_updates}], conflicts: [{...}])."""
    clean, conflicts = [], []
    eps = conn.execute(
        "SELECT id, season, episode FROM episodes WHERE show_id=? AND excluded=0 ORDER BY season, episode",
        (show_id,),
    ).fetchall()
    for e in eps:
        sigs, ambiguous = _episode_track_sig_map(conn, e["id"])
        missing = [sig for sig, settings in config.items() if settings.get("keep") and sig not in sigs]
        extra = [sig for sig in sigs if sig not in config]
        if missing or extra or ambiguous:
            conflicts.append({
                "episode_id": e["id"], "season": e["season"], "episode": e["episode"],
                "missing": missing, "extra": extra, "ambiguous": sorted(ambiguous),
            })
        else:
            updates = [
                {"id": sigs[sig]["id"], "keep": settings["keep"], "out_order": settings["out_order"],
                 "out_lang": settings["out_lang"], "out_default": settings["out_default"],
                 "out_forced": settings["out_forced"], "out_name": settings.get("out_name", "")}
                for sig, settings in config.items() if sig in sigs
            ]
            clean.append({"episode_id": e["id"], "track_updates": updates})
    return clean, conflicts


@app.get("/api/shows/{show_id}/config")
def get_show_config(show_id: int):
    conn = get_db()
    try:
        s = _show_or_404(conn, show_id)
        config = json.loads(s["track_config"]) if s["track_config"] else {}
        clean, conflicts = _show_config_conflicts(conn, show_id, config)
        return {"config": config, "applicable": len(clean), "conflicts": conflicts}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/config/from-episode")
def set_show_config_from_episode(show_id: int, body: dict):
    """Selects one already-configured episode as the show's canonical
    configuration, keyed by track identity (scan.track_sig) not track id/order."""
    episode_id = body.get("episode_id")
    if not episode_id:
        raise HTTPException(400, "episode_id required")
    conn = get_db()
    try:
        _show_or_404(conn, show_id)
        ep = conn.execute("SELECT id FROM episodes WHERE id=? AND show_id=?", (episode_id, show_id)).fetchone()
        if not ep:
            raise HTTPException(404, "episode not found on this show")
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM tracks WHERE episode_id=? AND type != 'video'", (episode_id,)
        )]
        config = {
            scan.track_sig(r): {
                "keep": r["keep"], "out_order": r["out_order"], "out_lang": r["out_lang"],
                "out_default": r["out_default"], "out_forced": r["out_forced"], "out_name": r["out_name"],
            }
            for r in rows
        }
        now = _now()
        conn.execute("UPDATE shows SET track_config=?, updated_at=? WHERE id=?",
                     (json.dumps(config), now, show_id))
        conn.commit()
        return {"ok": True, "config": config}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/config/apply")
def apply_show_config(show_id: int):
    """Applies the stored show config to every non-excluded episode whose
    tracks match it exactly. Episodes with a missing/extra/ambiguous track are
    left untouched (not set to 'ready') and reported back for individual
    handling on their own episode page -- never a partial apply."""
    conn = get_db()
    try:
        s = _show_or_404(conn, show_id)
        config = json.loads(s["track_config"]) if s["track_config"] else {}
        if not config:
            raise HTTPException(400, "no show config set -- POST .../config/from-episode first")
        clean, conflicts = _show_config_conflicts(conn, show_id, config)
        now = _now()
        for item in clean:
            _apply_track_settings(conn, item["episode_id"], item["track_updates"])
            conn.execute(
                "UPDATE episodes SET status='ready', updated_at=? WHERE id=? AND status NOT IN ('working','clean')",
                (now, item["episode_id"]),
            )
        conn.commit()
        return {"applied": len(clean), "conflicts": conflicts}
    finally:
        conn.close()


@app.get("/api/shows/{show_id}/seasons")
def show_seasons(show_id: int):
    """TMDB's expected season/episode counts vs. what's actually on disk --
    missing episodes and any file numbered outside TMDB's known range."""
    conn = get_db()
    try:
        s = _show_or_404(conn, show_id)
        if not s["tmdb_id"]:
            raise HTTPException(400, "show not TMDB-matched yet")
        info = scan.tmdb_get_tv(s["tmdb_id"], TMDB_API_KEY)
        if not info:
            raise HTTPException(502, "TMDB lookup failed")
        tmdb_seasons = {sn["season_number"]: sn["episode_count"] for sn in info.get("seasons", [])}
        rows = conn.execute(
            "SELECT season, episode FROM episodes WHERE show_id=? AND file IS NOT NULL", (show_id,)
        ).fetchall()
        present = {}
        out_of_range = []
        for r in rows:
            present.setdefault(r["season"], set()).add(r["episode"])
            expected = tmdb_seasons.get(r["season"])
            if expected is None or not (1 <= r["episode"] <= expected):
                out_of_range.append({"season": r["season"], "episode": r["episode"]})
        seasons = []
        for season_number, episode_count in sorted(tmdb_seasons.items()):
            have = present.get(season_number, set())
            missing = [n for n in range(1, episode_count + 1) if n not in have]
            seasons.append({
                "season": season_number, "expected": episode_count,
                "present": len(have), "missing": missing,
            })
        return {"seasons": seasons, "out_of_range": out_of_range}
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
        # 'clean' is deliberately NOT included. A batch launch is "do the rest of
        # the show", and re-running it over finished episodes re-does verified
        # work -- worse, _enqueue overwrites their output_file, so a later cancel
        # strands the output they already produced. Redoing one is a per-episode
        # decision, made on that episode's own page.
        eps = conn.execute(
            "SELECT id, size_bytes FROM episodes WHERE show_id=? AND excluded=0 AND status='ready'",
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
        if not jobs.has_space_for(SHOWS_ROOT, need_bytes):
            raise HTTPException(507, "not enough free disk space for this batch")
        job_ids = [_enqueue(conn, "episode", e["id"], job_kind, quality)["job_id"] for e in todo]
        return {"job_ids": job_ids, "count": len(job_ids)}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/rename")
def rename_show(show_id: int):
    """Folder -> "Title (Year)", each episode moved into "Season NN/" (or
    "Specials" for season 0) and renamed to "Title (Year) - SxxEyy" --
    Jellyfin's recommended TV layout. Refuses episodes TMDB doesn't recognize
    (season/episode outside its known range) rather than bake a wrong SxxEyy
    into a filename; those show up in GET .../seasons as out_of_range."""
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

        info = scan.tmdb_get_tv(s["tmdb_id"], TMDB_API_KEY) if s["tmdb_id"] else None
        tmdb_seasons = {sn["season_number"]: sn["episode_count"] for sn in (info or {}).get("seasons", [])}
        episodes = conn.execute("SELECT * FROM episodes WHERE show_id=? AND file IS NOT NULL", (show_id,)).fetchall()
        out_of_range = [
            f"S{(e['season'] or 0):02d}E{(e['episode'] or 0):02d}" for e in episodes
            if tmdb_seasons.get(e["season"]) is None or not (1 <= e["episode"] <= tmdb_seasons[e["season"]])
        ]
        if out_of_range:
            raise HTTPException(409, f"episodes outside TMDB's known range, resolve first: {', '.join(out_of_range)}")

        target = _safe_name(f"{s['title']} ({s['year']})")
        old_folder = os.path.join(SHOWS_ROOT, s["folder"])
        new_folder = os.path.join(SHOWS_ROOT, target)
        now = _now()

        if target != s["folder"]:
            if not os.path.isdir(old_folder):
                raise HTTPException(404, f"folder missing on disk: {s['folder']}")
            if os.path.exists(new_folder):
                raise HTTPException(409, f"target folder already exists: {target}")
            os.rename(old_folder, new_folder)
            conn.execute("UPDATE shows SET folder=?, updated_at=? WHERE id=?", (target, now, show_id))
            conn.commit()

        old_season_dirs = set()
        for e in episodes:
            season = e["season"] or 0
            old_ep_folder = os.path.join(new_folder, e["folder"]) if e["folder"] else new_folder
            if e["folder"]:
                old_season_dirs.add(old_ep_folder)
            season_dir_name = "Specials" if season == 0 else f"Season {season:02d}"
            new_ep_folder = os.path.join(new_folder, season_dir_name)
            os.makedirs(new_ep_folder, exist_ok=True)

            out_base = _safe_name(f"{s['title']} ({s['year']}) - S{season:02d}E{(e['episode'] or 0):02d}")
            old_stem, ext = os.path.splitext(e["file"])
            new_file = out_base + ext
            src = os.path.join(old_ep_folder, e["file"])
            if (new_file == e["file"] and old_ep_folder == new_ep_folder) or not os.path.exists(src):
                continue
            dst = os.path.join(new_ep_folder, new_file)
            if os.path.exists(dst):
                continue
            os.rename(src, dst)
            for f in list(os.listdir(old_ep_folder)) if os.path.isdir(old_ep_folder) else []:
                new_name = _canonical_name(f, old_stem, out_base)
                if new_name != f:
                    sidecar_dst = os.path.join(new_ep_folder, new_name)
                    if not os.path.exists(sidecar_dst):
                        os.rename(os.path.join(old_ep_folder, f), sidecar_dst)
            conn.execute(
                "UPDATE tracks SET ext_path=REPLACE(ext_path, ?, ?) WHERE episode_id=? AND ext_path IS NOT NULL",
                (os.path.join(old_ep_folder, old_stem), os.path.join(new_ep_folder, out_base), e["id"]),
            )
            if e["output_file"]:
                conn.execute(
                    "UPDATE episodes SET output_file=? WHERE id=?",
                    (os.path.join(new_ep_folder,
                                  _canonical_name(os.path.basename(e["output_file"]), old_stem, out_base)),
                     e["id"]),
                )
            conn.execute(
                "UPDATE episodes SET folder=?, file=?, updated_at=? WHERE id=?",
                (season_dir_name, new_file, now, e["id"]),
            )
        conn.commit()

        for d in old_season_dirs:
            try:
                os.rmdir(d)
            except OSError:
                pass
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


def _apply_track_settings(conn, episode_id, tracks):
    """tracks: [{id, keep, out_order, out_lang, out_default, out_forced, out_name?}].
    Shared by the per-episode config save and the show-level config apply."""
    for t in tracks:
        conn.execute(
            "UPDATE tracks SET keep=?, out_order=?, out_lang=?, out_default=?, out_forced=?, out_name=? "
            "WHERE id=? AND episode_id=?",
            (int(t["keep"]), int(t["out_order"]), t["out_lang"], int(t["out_default"]),
             int(t["out_forced"]), t.get("out_name", ""), t["id"], episode_id),
        )


@app.put("/api/episodes/{episode_id}/config")
def save_config_episode(episode_id: int, body: dict):
    conn = get_db()
    try:
        _owner_or_404(conn, "episode", episode_id)
        _apply_track_settings(conn, episode_id, body.get("tracks", []))
        now = _now()
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


# Job outputs are written as dotfiles. Jellyfin scans the movie folder while the
# job runs -- Radarr tells it to on every import -- sees a second video beside the
# original and indexes the half-written remux as its own movie. Twelve of those
# phantom entries accumulated in one evening, each with artwork downloaded for it.
# A leading dot makes Jellyfin skip the file; _delete_original renames it to the
# visible final name once verification passes.
def _build_job_cmd(conn, kind, owner, job_kind, quality):
    """Returns (cmd_str, out_path) for remux/propedit/encode/sample. `owner` is
    the uniform dict from _owner_info; `kind` is 'movie'|'episode'.

    propedit edits the source in place, so its out_path IS the source path --
    no 2 TB of copying and no free-space requirement for the 104 titles that
    only have wrong labels, not extra tracks."""
    title = owner["title_display"]
    folder_path = os.path.join(_root_for(kind), owner["folder"])
    in_path = os.path.join(folder_path, owner["file"])
    tracks = _all_tracks(conn, kind, owner["id"])
    if job_kind == "remux":
        out_path = os.path.join(folder_path, f".{owner['out_base']}.remux.mkv")
        return shlex.join(commands.build_mkvmerge_remux(tracks, title, in_path, out_path)), out_path
    if job_kind == "propedit":
        # track:a{N}/track:s{N} address positions in the FILE, and mkvpropedit
        # moves nothing -- so the metadata written at each position must be the
        # metadata of the track that physically sits there. Sorting by out_order
        # would stamp each track's labels onto whichever track occupies its
        # intended slot. The config's out_order is left untouched: it still
        # records the order a later remux should actually produce.
        audio_order = sorted([t for t in tracks if t["type"] == "audio" and t["keep"]],
                             key=lambda t: t["mkv_id"])
        sub_order = sorted([t for t in tracks if t["type"] == "subtitle" and t["keep"]],
                           key=lambda t: t["mkv_id"])
        vtrack = next((t for t in tracks if t["type"] == "video"), None)
        argv = commands.build_mkvpropedit_chain(in_path, title, audio_order, sub_order,
                                                video_lang=vtrack["out_lang"] if vtrack else None,
                                                video_track=vtrack)
        return shlex.join(argv), in_path
    if job_kind == "sample":
        out_path = os.path.join(folder_path, f".{owner['out_base']}.sample.rf{quality}.mkv")
        start = int((owner["duration"] or 1200) / 2)  # mid-movie: representative scene
        hb_argv, _ = commands.build_handbrake_encode(tracks, in_path, out_path,
                                                      quality=quality, sample_start=start)
        # throwaway preview: skip the mkvpropedit metadata pass
        return shlex.join(hb_argv), out_path
    out_path = os.path.join(folder_path, f".{owner['out_base']}.hevc.mkv")
    hb_argv, sub_order = commands.build_handbrake_encode(tracks, in_path, out_path, quality=quality)
    audio_order = sorted([t for t in tracks if t["type"] == "audio" and t["keep"]], key=lambda t: t["out_order"])
    vtrack = next((t for t in tracks if t["type"] == "video"), None)
    mkvpe = commands.build_mkvpropedit_chain(out_path, title, audio_order, sub_order,
                                             video_lang=vtrack["out_lang"] if vtrack else None,
                                             video_track=vtrack)
    return shlex.join(hb_argv) + " && " + shlex.join(mkvpe), out_path


def _preview_command(conn, kind, owner_id, job_kind, quality):
    if job_kind not in ("remux", "propedit", "encode", "sample"):
        raise HTTPException(400, "kind must be remux|propedit|encode|sample")
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
    if job_kind not in ("remux", "propedit", "encode", "sample"):
        raise HTTPException(400, "kind must be remux|propedit|encode|sample")
    owner = _owner_or_404(conn, kind, owner_id)
    if not owner["file"]:
        raise HTTPException(400, "no source file")
    col = _owner_col(kind)
    mine = conn.execute(
        f"SELECT id FROM jobs WHERE {col}=? AND status IN ('running','queued')", (owner_id,)
    ).fetchone()
    if mine:
        raise HTTPException(409, "a job is already running or queued for this item")

    if job_kind == "propedit":
        # mkvpropedit only speaks Matroska; an mp4/avi has to go through
        # mkvmerge to become an mkv at all, so it is never a propedit candidate
        if not owner["file"].lower().endswith(".mkv"):
            raise HTTPException(400, "not a Matroska file; use kind=remux")
        # in-place editing cannot drop or add a track, so anything the config
        # wants removed (or pulled in from an external .srt) needs a real remux
        bad = conn.execute(
            f"SELECT 1 FROM tracks WHERE {col}=? AND (keep=0 OR ext_path IS NOT NULL) LIMIT 1",
            (owner_id,)).fetchone()
        if bad:
            raise HTTPException(400, "this title drops or adds tracks; use kind=remux")
        # A config whose out_order differs from the file order is still a valid
        # propedit: the labels get fixed in place and the tracks stay where they
        # are. Only a remux can move them, and the config still says where they
        # belong when one runs.

    # propedit rewrites headers in place: no second copy, so no space needed
    need_bytes = 0 if job_kind == "propedit" else (2 * 10**9 if job_kind == "sample" else owner["size_bytes"])
    if not jobs.has_space_for(_root_for(kind), need_bytes):
        raise HTTPException(507, "not enough free disk space for this operation")

    # The config addresses source tracks by mkv_id. When Radarr replaces a file
    # (upgrade) the ids remap and the stale config muxes the wrong tracks --
    # jobs 743/744 burned 67 min of CPU before verify_output caught it. Check the
    # real file now, at enqueue, while it costs seconds.
    if job_kind in ("remux", "propedit"):
        kept = [dict(r) for r in conn.execute(
            f"SELECT * FROM tracks WHERE {col}=? AND keep=1", (owner_id,))]
        ok, msg = jobs.preflight(
            os.path.join(_root_for(kind), owner["folder"], owner["file"]), kept)
        if not ok:
            raise HTTPException(409, msg)

    # global single runner: one job hammers the CPU at a time, rest queue up
    busy = conn.execute("SELECT id FROM jobs WHERE status='running' LIMIT 1").fetchone()
    cmd_str, out_path = _build_job_cmd(conn, kind, owner, job_kind, quality)
    id_kwargs = {"movie_id": owner_id} if kind == "movie" else {"episode_id": owner_id}
    job_id = jobs.start_job(conn, job_kind, cmd_str, LOG_DIR,
                            cpu_quota=_current_quota(conn), queued=bool(busy), **id_kwargs)
    if job_kind != "sample":  # samples never touch owner state
        status = "cleaning" if job_kind in ("remux", "propedit") else "encoding"
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


def _cancel_job(conn, job):
    """A queued job is simply forgotten; a running one is killed and its partial
    output deleted. The owner returns to 'ready', NOT 'error' -- cancelling is a
    decision, and an errored episode is excluded from the next batch launch."""
    job = dict(job)
    if job["status"] not in ("running", "queued"):
        return False
    now = _now()
    if job["status"] == "running":
        jobs.kill_job(job)
        conn.execute("UPDATE jobs SET status='cancelled', exit_code=-2, finished_at=? WHERE id=?",
                     (now, job["id"]))
    else:
        conn.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
    if job["kind"] != "sample":
        kind, oid = _job_owner_kind_id(job)
        owner = _owner_info(conn, kind, oid)
        # same reason as the reaper: for propedit output_file is the source
        if (owner and job["status"] == "running" and owner["output_file"]
                and job["kind"] != "propedit"):
            try:
                os.remove(owner["output_file"])
            except OSError:
                pass
        # never knock a finished episode off 'clean' because a later job was cancelled
        if owner and owner["status"] != "clean":
            _set_owner_status(conn, kind, oid, "ready", output_file="")
    conn.commit()
    return True


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: int):
    conn = get_db()
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "not found")
        if not _cancel_job(conn, job):
            raise HTTPException(409, f"job is {job['status']}, nothing to cancel")
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/shows/{show_id}/jobs/cancel")
def cancel_show_jobs(show_id: int):
    """Cancels this show's whole in-flight batch: the running job plus the queue
    behind it. Cancel the running one LAST -- the ticker starts the next queued
    job the moment the runner frees up, so draining the queue first stops it."""
    conn = get_db()
    try:
        _show_or_404(conn, show_id)
        rows = conn.execute(
            "SELECT j.* FROM jobs j JOIN episodes e ON e.id = j.episode_id "
            "WHERE e.show_id=? AND j.status IN ('running','queued') "
            "ORDER BY CASE j.status WHEN 'queued' THEN 0 ELSE 1 END, j.id", (show_id,)
        ).fetchall()
        n = sum(1 for r in rows if _cancel_job(conn, r))
        return {"cancelled": n}
    finally:
        conn.close()


def _job_eta(j):
    """Last ETA HandBrake printed, parsed from the log tail (running jobs only)."""
    if j.get("status") == "running" and j.get("log_path"):
        m = jobs.ETA_RE.findall(jobs.tail(j["log_path"]))
        return m[-1] if m else None
    return None


# Polled every few seconds by every open page. The UI reads only
# status/progress/eta/title, so the bulky operational columns never ship.
_JOB_LIST_OMIT = ("cmd", "log_path", "tmux_session")


def _recent_and_active_jobs(conn):
    """The 50 most recent jobs PLUS every active one. A plain
    `ORDER BY id DESC LIMIT 50` drops the running job as soon as the queue is
    longer than the window -- the runner works oldest-first, so the running job
    has the LOWEST id of anything in flight. That made the dashboard render
    "idle" mid-encode and hid every progress bar."""
    recent = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
    seen = {r["id"] for r in recent}
    active = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('running', 'queued') ORDER BY id DESC"
    ).fetchall()
    return sorted(list(recent) + [a for a in active if a["id"] not in seen],
                  key=lambda r: r["id"], reverse=True)


def _shape_jobs(conn, out):
    """Attaches owner title + ETA and strips the operational columns. Mutates
    and returns `out` (a list of plain job dicts)."""
    # look up only the owners actually referenced -- the old version scanned
    # the whole movies table and the whole episodes-join-shows every poll
    movie_ids = {j["movie_id"] for j in out if j["movie_id"]}
    ep_ids = {j["episode_id"] for j in out if j["episode_id"]}
    movie_titles, ep_titles = {}, {}
    if movie_ids:
        qs = ",".join("?" * len(movie_ids))
        for m in conn.execute(
            f"SELECT id, title, year, clean_title, folder FROM movies WHERE id IN ({qs})",
            list(movie_ids),
        ):
            movie_titles[m["id"]] = (f"{m['title']} ({m['year']})" if m["title"]
                                     else m["clean_title"] or m["folder"])
    if ep_ids:
        qs = ",".join("?" * len(ep_ids))
        for e in conn.execute(
            f"SELECT e.id, e.season, e.episode, s.title, s.clean_title FROM episodes e "
            f"JOIN shows s ON s.id = e.show_id WHERE e.id IN ({qs})",
            list(ep_ids),
        ):
            show_title = e["title"] or e["clean_title"]
            ep_titles[e["id"]] = f"{show_title} S{(e['season'] or 0):02d}E{(e['episode'] or 0):02d}"

    for j in out:
        if j["movie_id"]:
            j["movie_title"] = movie_titles.get(j["movie_id"], f"movie {j['movie_id']}")
        else:
            j["movie_title"] = ep_titles.get(j["episode_id"], f"episode {j['episode_id']}")
        j["eta"] = _job_eta(j)
        for k in _JOB_LIST_OMIT:
            j.pop(k, None)
    return out


@app.get("/api/jobs")
def list_jobs():
    """Advancing read of the job list. /api/events serves the same shape without
    advancing; this endpoint stays for one-shot fetches and as the fallback when
    a browser has no EventSource."""
    conn = get_db()
    try:
        out = [_poll_and_finalize(conn, r["id"]) if r["status"] in ("running", "done") else dict(r)
               for r in _recent_and_active_jobs(conn)]
        return _shape_jobs(conn, out)
    finally:
        conn.close()


def _events_payload(conn):
    """Read-only snapshot for the SSE stream. Deliberately does NOT advance
    jobs -- _job_ticker owns that."""
    return {"jobs": _shape_jobs(conn, [dict(r) for r in _recent_and_active_jobs(conn)]),
            "scan": dict(scan_state)}


def _events_json():
    conn = get_db()
    try:
        return json.dumps(_events_payload(conn), sort_keys=True)
    finally:
        conn.close()


@app.get("/api/events")
async def events(request: Request):
    """Server-sent events: job progress and scan progress, pushed on change.

    Replaces three separate client poll chains (job list, scan status, and a
    per-job completion watcher). Each connection re-reads state on the ticker's
    cadence and only sends when the payload actually differs, so an idle library
    costs one comment line every few seconds.

    ponytail: per-connection read rather than a shared pub/sub. One admin, maybe
    ten -- a broadcast bus only earns its keep when the read itself starts to
    hurt, and this one is a 1.3 MB local SQLite file.
    """
    async def gen():
        last = None
        while not _shutting_down.is_set() and not await request.is_disconnected():
            payload = await run_in_threadpool(_events_json)
            if payload != last:
                last = payload
                yield f"data: {payload}\n\n"
            else:
                # comment frame: keeps proxies from idling the connection out
                # and gives the client something to notice a dead link by
                yield ": keepalive\n\n"
            # Waiting on the shutdown event instead of a plain sleep is what
            # makes `./run.sh` restartable: uvicorn's graceful shutdown waits
            # for in-flight requests, and a stream that only ends when the
            # CLIENT leaves never ends -- one open browser tab hung the server
            # indefinitely. The client reconnects on its own afterwards.
            try:
                await asyncio.wait_for(_shutting_down.wait(), timeout=TICK_SECONDS)
                break
            except asyncio.TimeoutError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # nginx/CasaOS reverse proxy must not buffer this
    })


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



# --- ntfy -------------------------------------------------------------------
# The container address, not the tailnet hostname: a "your film is ready"
# message must not depend on the tailnet being up to arrive.
NTFY_URL = os.environ.get("NTFY_URL", "http://172.17.0.1:8095/media")
def _mark_auto_finalize(conn, job_id):
    """Flag a hook-created job to finalize without a human once it verifies.

    This used to be an in-memory set, on the reasoning that after a restart
    falling back to manual is the safe direction when deletion is involved.
    With the service now started by systemd, a restart is routine rather than
    rare, and that "safe" fallback became a silent one: the 13 September reboot
    left every import raw -- verified, never swapped in, no artwork, no
    notification. Persisting it removes no safety: finalize still runs only
    after verify_output has passed on the new file.
    """
    if job_id:
        conn.execute("UPDATE jobs SET auto_finalize=1 WHERE id=?", (job_id,))
        conn.commit()
_HOOK_SECRET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".radarr-hook-secret")


def _notify(title, body, tags="", priority=3):
    """Fire-and-forget ntfy publish. Never raises: a notification failing must
    not fail the job it is reporting on."""
    try:
        req = urllib.request.Request(
            NTFY_URL, data=body.encode("utf-8"),
            headers={"Title": title, "Tags": tags, "Priority": str(priority)})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[ntfy] no se pudo notificar: {e}", flush=True)


def _track_summary(conn, kind, owner_id):
    """The one line worth reading in a notification: what you will hear."""
    col = _owner_col(kind)
    rows = conn.execute(
        f"SELECT out_lang, out_name, codec FROM tracks WHERE {col}=? AND type='audio' "
        "AND keep=1 ORDER BY out_order", (owner_id,)).fetchall()
    out = []
    for r in rows:
        lang = (r["out_lang"] or "und")
        atmos = "atmos" in (r["codec"] or "").lower()
        out.append(f"{lang}{'+atmos' if atmos else ''}")
    return "/".join(out) or "sin audio"


def _hook_credentials():
    try:
        with open(_HOOK_SECRET) as fh:
            lines = [l.strip() for l in fh if l.strip()]
        return (lines[0], lines[1]) if len(lines) >= 2 else None
    except OSError:
        return None


def _check_hook_auth(request):
    """HTTP Basic. uvicorn listens on 0.0.0.0, so this endpoint is reachable
    from the whole LAN and it starts remuxes across 5 TB."""
    want = _hook_credentials()
    if not want:
        raise HTTPException(503, "hook secret not configured")
    hdr = request.headers.get("authorization", "")
    if not hdr.lower().startswith("basic "):
        raise HTTPException(401, "authentication required",
                            headers={"WWW-Authenticate": "Basic"})
    try:
        user, _, pw = base64.b64decode(hdr.split(None, 1)[1]).decode().partition(":")
    except Exception:
        raise HTTPException(401, "malformed credentials")
    import hmac
    if not (hmac.compare_digest(user, want[0]) and hmac.compare_digest(pw, want[1])):
        raise HTTPException(403, "bad credentials")


@app.get("/api/hooks/radarr")
def radarr_hook_probe():
    """Radarr validates a webhook URL before saving it and refuses a 404."""
    return {"ok": True, "hook": "radarr"}


def _lost_audio_vs_recycle(conn, movie_id):
    """Audio languages the recycled copy has and the current file does not.

    Header reads only (mkvmerge -J on both files) so it is cheap enough to run
    inside the webhook; measuring the offset for an actual graft decodes audio
    and belongs outside the request."""
    m = conn.execute("SELECT folder, file FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not m or not m["file"]:
        return []
    try:
        old = graft.recycled_copy(m["folder"])
        if not old:
            return []
        new_path = os.path.join(MEDIA_ROOT, m["folder"], m["file"])
        return sorted(graft.audio_langs(old) - graft.audio_langs(new_path) - {"und"})
    except Exception as e:            # a broken recycle copy must not block an import
        print(f"[hook] no pude comparar con .recycle: {e}", flush=True)
        return []


STAGING_ROOT = os.environ.get("MM_STAGING_ROOT", "/media/hdd1/Staging")
# Radarr's container path for that same directory, as it appears in folderPath
STAGING_CONTAINER = os.environ.get("MM_STAGING_CONTAINER", "/staging")
_STAGED = {}          # movie_id -> staging folder still to clean up


def _adopt_from_staging(folder):
    """Bring a staged import into the library WITHOUT making it visible yet.

    Radarr imports into /staging, which Jellyfin does not watch, so the raw file
    never reaches the library. We hardlink it into the movie's folder under a
    dotfile name: same inode, zero bytes, Radarr's copy untouched and its record
    still valid. Jellyfin skips a folder whose only video is hidden (measured:
    indexed 0 with the dotfile, 1 once the final file lands), so the title
    appears for the first time already normalised.

    Returns the hidden filename, or None when there is nothing to adopt."""
    src_dir = os.path.join(STAGING_ROOT, folder)
    if not os.path.isdir(src_dir):
        return None
    vids = [f for f in os.listdir(src_dir)
            if f.lower().endswith(scan.VIDEO_EXT) and not f.startswith(".")]
    if not vids:
        return None
    src = os.path.join(src_dir, max(vids, key=lambda f: os.path.getsize(os.path.join(src_dir, f))))
    dest_dir = os.path.join(MEDIA_ROOT, folder)
    os.makedirs(dest_dir, exist_ok=True)
    hidden = f".{os.path.splitext(os.path.basename(src))[0]}.import{os.path.splitext(src)[1]}"
    dest = os.path.join(dest_dir, hidden)
    if os.path.exists(dest):
        os.remove(dest)
    try:
        os.link(src, dest)                    # same filesystem on the host
    except OSError:
        shutil.copy2(src, dest)               # never fail an import over this
    return hidden


@app.get("/api/hooks/sonarr")
def sonarr_hook_probe():
    """Sonarr validates a webhook URL before saving it and refuses a 404."""
    return {"ok": True, "hook": "sonarr"}


@app.post("/api/hooks/sonarr")
async def sonarr_hook(request: Request):
    """Sonarr's counterpart to the Radarr hook.

    The payload shape is different enough that sharing one handler would be a
    tangle of `if "series" in body`: Sonarr sends a `series` plus a LIST of
    `episodes`, and one file can carry several of them (a double episode).
    """
    _check_hook_auth(request)
    body = await request.json()
    event = body.get("eventType", "")
    if event == "Test":
        return {"ok": True, "test": True}
    # "Upgrade" and "EpisodeFileDeleteForUpgrade" are accepted defensively: an
    # upgrade import and a delete-for-upgrade are reported under different
    # eventType strings across Sonarr versions, and the cost of listing one that
    # never fires is nothing, while missing one leaves our rows describing a file
    # that no longer exists. "Grab" stays out on purpose: it arrives before the
    # file does, and a job enqueued then points at a path Sonarr has not written.
    if event not in ("Download", "Upgrade", "Rename",
                     "EpisodeFileDelete", "EpisodeFileDeleteForUpgrade"):
        return {"ok": True, "ignored": event}

    series = body.get("series") or {}
    series_path = (series.get("path") or "").rstrip("/")
    folder = os.path.basename(series_path)
    title = series.get("title") or folder
    conn = get_db()
    try:
        if not folder:
            raise HTTPException(400, "payload sin series.path")
        # Check the folder is really ours BEFORE upserting anything. Sonarr's
        # path is a container path (/data/Shows/...) that need not match
        # SHOWS_ROOT textually, so the test that matters is whether the folder
        # exists under the root we watch. Upserting first would mint a show row
        # for any basename Sonarr sends -- an import into /staging created a
        # phantom "Fake" show in exactly this spot, which is how Underworld
        # disappeared quietly on the Radarr side.
        if not os.path.isdir(os.path.join(SHOWS_ROOT, folder)):
            msg = (f"{title}: Sonarr la dejó en {series_path or '?'}, "
                   f"fuera de la biblioteca")
            _notify("Importación sin normalizar", msg, tags="warning", priority=4)
            print(f"[hook] sonarr {event} {title}: fuera de la biblioteca, "
                  f"path={series_path!r}", flush=True)
            raise HTTPException(404, msg)
        # Always rescan: the event means Sonarr just changed what is on disk, so
        # every stored episode row for this show may describe the previous file.
        # Same reasoning as the Radarr hook, same bugs avoided.
        scan.upsert_show(conn, SHOWS_ROOT, folder, TMDB_API_KEY)
        show = conn.execute("SELECT * FROM shows WHERE folder=?", (folder,)).fetchone()
        if not show:
            msg = f"{title}: no encontré la carpeta {folder!r} tras el rescan"
            _notify("Importación sin normalizar", msg, tags="warning", priority=4)
            raise HTTPException(404, msg)
        if event.startswith("EpisodeFileDelete"):
            # the rescan above already pruned the row; nothing left to normalise
            return {"ok": True, "show_id": show["id"], "rescanned": True}

        # A new show arrives with bare shelves: Jellyfin renders its own images
        # but nothing writes them to disk, and no Sonarr metadata consumer is on.
        if TMDB_API_KEY and show["tmdb_id"]:
            try:
                _fetch_show_artwork(show)
            except Exception as e:      # artwork is never worth failing an import
                print(f"[hook] sonarr artwork {title}: {e}", flush=True)

        wanted = [(e.get("seasonNumber"), e.get("episodeNumber"))
                  for e in (body.get("episodes") or [])]
        queued, skipped = [], []
        for season, number in wanted:
            row = conn.execute(
                "SELECT id FROM episodes WHERE show_id=? AND season=? AND episode=?",
                (show["id"], season, number)).fetchone()
            if not row:
                skipped.append(f"S{season or 0:02d}E{number or 0:02d}: sin fila tras el rescan")
                continue
            eid = row["id"]
            scan.suggest_tracks(conn, eid, table="episodes", multi_audio=True)
            # propedit when nothing has to be dropped -- it rewrites headers in
            # place instead of copying the whole episode across a 40 MB/s bus.
            # _enqueue refuses it with 400 when the config drops or adds a track,
            # which is exactly when a real remux is the only option.
            for kind in ("propedit", "remux"):
                try:
                    job = _enqueue(conn, "episode", eid, kind, 22)
                except HTTPException as exc:
                    if kind == "propedit" and exc.status_code == 400:
                        continue
                    skipped.append(f"ep{eid}: {exc.detail}")
                    break
                _mark_auto_finalize(conn, job.get("job_id"))
                queued.append({"episode_id": eid, "kind": kind, **job})
                break
        print(f"[hook] sonarr {event} {title}: {len(queued)} en cola, "
              f"{len(skipped)} sin encolar", flush=True)
        if skipped and not queued:
            _notify(f"Requiere atención: {title}", "; ".join(skipped[:3]),
                    tags="warning", priority=4)
        return {"ok": True, "show_id": show["id"], "queued": queued, "skipped": skipped}
    finally:
        conn.close()


def _fetch_show_artwork(show):
    """poster/backdrop/logo for the show and a poster per season, from TMDB."""
    import artwork
    d = os.path.join(SHOWS_ROOT, show["folder"])
    seasons = []
    for name in sorted(os.listdir(d)):
        if name.lower().startswith("season ") and os.path.isdir(os.path.join(d, name)):
            try:
                seasons.append((int(name.split()[1]), os.path.join(d, name)))
            except ValueError:
                pass
    return artwork.fetch_show(show["tmdb_id"], d, TMDB_API_KEY, seasons)


@app.post("/api/hooks/radarr")
async def radarr_hook(request: Request):
    _check_hook_auth(request)
    body = await request.json()
    event = body.get("eventType", "")
    if event == "Test":
        return {"ok": True, "test": True}
    if event not in ("Download", "MovieFileImported", "Upgrade"):
        return {"ok": True, "ignored": event}

    movie = body.get("movie") or {}
    folder_path = (movie.get("folderPath") or "").rstrip("/")
    folder = os.path.basename(folder_path)
    staged = folder_path.startswith(STAGING_CONTAINER + "/")
    title = movie.get("title") or folder
    tmdb = movie.get("tmdbId")
    conn = get_db()
    try:
        row = None
        if tmdb:
            row = conn.execute("SELECT id FROM movies WHERE tmdb_id=?", (tmdb,)).fetchone()
        if not row and folder:
            row = conn.execute("SELECT id FROM movies WHERE folder=?", (folder,)).fetchone()
        # Always rescan, not just when the row is missing. Every event that gets
        # here means Radarr just put a different file on disk, so the stored row
        # describes the previous one: a stub still has file=NULL and _enqueue
        # rejects it with "no source file", and an upgrade still carries the old
        # file's track ids, which preflight then refuses. Both happened on the
        # same import batch -- Superman and Captain America hit the first, Tokyo
        # Drift the second.
        if staged:
            # hardlink the staged file in hidden, so the rescan below sees it
            # while Jellyfin still does not
            _adopt_from_staging(folder)
        if folder:
            scan.upsert_movie(conn, MEDIA_ROOT, folder, TMDB_API_KEY)
            row = conn.execute("SELECT id FROM movies WHERE folder=?", (folder,)).fetchone()
        if not row:
            # Radarr can import into a root folder media-manager does not watch.
            # Underworld landed in /staging and the warning just said "no
            # encontré la carpeta", which reads as "nothing happened". Say where
            # Radarr put it: that is the whole diagnosis.
            where = (movie.get("folderPath") or "").rstrip("/")
            outside = where and not where.startswith(("/movies", MEDIA_ROOT))
            msg = (f"{title}: Radarr la dejó en {where}, fuera de la biblioteca"
                   if outside else
                   f"{title}: no encontré la carpeta {folder!r} en la biblioteca")
            _notify("Importación sin normalizar", msg, tags="warning", priority=4)
            print(f"[hook] {event} {title}: sin fila, folderPath={where!r}", flush=True)
            raise HTTPException(404, msg)
        mid = row["id"]
        # An upgrade can arrive with fewer dubs than the copy it replaced, and
        # Radarr has no idea: it compares quality, not audio. Shutter Island and
        # Tokyo Drift both lost their Spanish that way. Remuxing now would ship
        # the loss and delete-original would make it permanent, so stop here and
        # say so -- the recycled copy is the only source for a graft, and it
        # expires in seven days.
        lost = _lost_audio_vs_recycle(conn, mid)
        if lost:
            _notify(f"Injerto pendiente: {title}",
                    f"el reemplazo perdió {'/'.join(lost)}; la copia en .recycle aún lo tiene",
                    tags="warning", priority=4)
            print(f"[hook] {event} {title} -> movie {mid}: perdió {lost}, sin remux", flush=True)
            return {"ok": True, "movie_id": mid, "needs_graft": lost}
        # the convention lives in suggest_tracks: original language first and
        # default, Spanish second, TrueHD/Atmos kept but never default
        scan.suggest_tracks(conn, mid, "movies")
        if staged:
            _STAGED[mid] = folder
        job = _enqueue(conn, "movie", mid, "remux", 22)
        _mark_auto_finalize(conn, job.get("job_id"))
        print(f"[hook] {event} {title} -> movie {mid}, job {job.get('job_id')}", flush=True)
        return {"ok": True, "movie_id": mid, **job}
    finally:
        conn.close()


def _has_artwork(kind, folder):
    """Whether the folder carries any image Jellyfin can use as a poster."""
    try:
        return any(f.lower().endswith((".jpg", ".jpeg", ".png"))
                   for f in os.listdir(os.path.join(_root_for(kind), folder)))
    except OSError:
        return False


_ARTWORK = ("poster.jpg", "folder.jpg", "backdrop.jpg", "landscape.jpg",
            "logo.png", "banner.jpg", "clearart.png", "disc.png", "thumb.jpg")


def _restore_artwork(kind, folder):
    """Put back the artwork Radarr took to the recycle bin with the old file.

    Replacing a movie file moves the whole set -- poster, backdrop, logo -- into
    .recycle, leaving the library folder with nothing but the video. Jellyfin was
    serving those images, so the title reappears with no poster. The recycled
    copy still has them and it is a few MB, so copy them back rather than making
    Jellyfin re-download what is already on the disk.

    Returns the filenames restored. Never raises: artwork must not fail a job."""
    restored = []
    try:
        dest = os.path.join(_root_for(kind), folder)
        if any(f.lower().endswith((".jpg", ".jpeg", ".png")) for f in os.listdir(dest)):
            return restored                      # already has its own, leave it
        src = os.path.join(graft.RECYCLE, folder)
        if not os.path.isdir(src):
            return restored
        for f in os.listdir(src):
            if f.lower() in _ARTWORK or f.lower().endswith((".jpg", ".jpeg", ".png")):
                shutil.copy2(os.path.join(src, f), os.path.join(dest, f))
                restored.append(f)
    except Exception as e:
        print(f"[artwork] no pude restaurar en {folder!r}: {e}", flush=True)
    return restored


def _readiness(conn, kind, owner_id, owner):
    """What still stands between this file and being genuinely watchable.

    Returns (pending, notes). `pending` is what we can still obtain and have not
    -- announcing "Lista" with any of it outstanding is a half-truth. `notes` is
    what the film simply does not have and no source can give it; that is worth
    stating but must not hold the announcement forever.

    The distinction is the whole point: Tokyo Drift's Spanish was sitting in the
    recycle bin (pending, recoverable), while Superman's does not exist in any
    copy we hold (a note, not a blocker)."""
    pending, notes = [], []
    folder = owner["folder"]
    if not _has_artwork(kind, folder):
        src = os.path.join(graft.RECYCLE, folder)
        have_src = os.path.isdir(src) and any(
            f.lower().endswith((".jpg", ".jpeg", ".png")) for f in os.listdir(src))
        # pending is a list of things to get; notes describes what the film
        # lacks. Same word cannot serve both: "carátula" alone said nothing.
        pending.append("carátula") if have_src else notes.append("sin carátula")
    lost = _lost_audio_vs_recycle(conn, owner_id) if kind == "movie" else []
    if lost:
        pending.append("audio " + "/".join(lost))
    else:
        langs = {(r["out_lang"] or "") for r in conn.execute(
            f"SELECT out_lang FROM tracks WHERE {_owner_col(kind)}=? AND type='audio' AND keep=1",
            (owner_id,))}
        if "spa" not in langs:
            notes.append("sin audio en español")
    return pending, notes


def _refresh_after_propedit(conn, kind, owner_id):
    """Write back what the in-place edit just put in the file.

    path_unchanged is mtime-based and says so: an mkvpropedit retag can leave the
    row describing the labels the file had BEFORE the edit. preflight then
    compares its stored lang against the file and refuses the next job on a
    title that is in fact correct -- it rejected an already-tagged Peaky Blinders
    episode with "la pista 0 es 'eng' en el origen y la config espera 'und'".

    Copying the config onto the observed columns is only honest because
    verify_output has just re-read the file and confirmed lang/default/forced
    match; the name is the same string that was handed to mkvpropedit.
    """
    col = "movie_id" if kind == "movie" else "episode_id"
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM tracks WHERE {col}=? AND keep=1", (owner_id,))]
    by_type = {}
    for t in rows:
        by_type.setdefault(t["type"], []).append(t)
    for t in rows:
        name = "" if t["type"] == "video" else commands._canonical_name(t, by_type[t["type"]])
        conn.execute(
            "UPDATE tracks SET lang=?, default_flag=?, forced_flag=?, name=? WHERE id=?",
            (t["out_lang"] or t["lang"], t["out_default"], t["out_forced"], name, t["id"]))
    conn.commit()


def _verify_and_finalize(conn, kind, owner_id, job):
    owner = _owner_info(conn, kind, owner_id)
    kept = _kept_tracks(conn, kind, owner_id)
    src = os.path.join(_root_for(kind), owner["folder"], owner["file"]) if owner["file"] else None
    # propedit edits in place: the file order is unchanged and IS the truth to
    # check against, so expectations line up by mkv_id, not by out_order.
    ok, msg = jobs.verify_output(owner["output_file"], kept, owner["duration"], source_path=src,
                                 order_key="mkv_id" if job["kind"] == "propedit" else "out_order")
    if ok:
        conn.execute("UPDATE jobs SET status='verified' WHERE id=?", (job["id"],))
        _set_owner_status(conn, kind, owner_id, "clean")
        if job["kind"] == "propedit":
            _refresh_after_propedit(conn, kind, owner_id)
    else:
        conn.execute("UPDATE jobs SET status='failed', exit_code=-1 WHERE id=?", (job["id"],))
        _set_owner_status(conn, kind, owner_id, "error")
    conn.commit()
    # The single point that knows a job both finished AND passed verification.
    # A notification here means normalized and checked -- never merely downloaded.
    auto = conn.execute("SELECT auto_finalize FROM jobs WHERE id=?", (job["id"],)).fetchone()
    if ok and auto and auto["auto_finalize"]:
        # cleared before acting, so a crash mid-finalize cannot repeat a delete
        conn.execute("UPDATE jobs SET auto_finalize=0 WHERE id=?", (job["id"],))
        conn.commit()
        try:
            # replaces the raw import with the normalised file, renames it to
            # "Title (Year).mkv" and sweeps the folder of scene junk
            _delete_original(conn, kind, owner_id)
            _finish_staging(conn, owner_id)
            owner = _owner_info(conn, kind, owner_id)
        except Exception as e:
            print(f"[hook] no se pudo finalizar {owner_id}: {e}", flush=True)
            _notify(f"Requiere atención: {owner['title'] or owner['folder']}",
                    f"el remux verificó pero no se pudo finalizar: {e}",
                    tags="warning", priority=4)
    if kind == "movie":
        name = owner["title"] or owner["folder"]
        if not ok:
            _notify(f"Falló: {name}", msg or "la verificación no pasó",
                    tags="rotating_light", priority=4)
        elif owner["output_file"]:
            # Verified but the normalised file is NOT in place yet: the job output
            # is still a dotfile beside the original, and Jellyfin is serving the
            # original. Announcing availability here told the user Superman was
            # ready while the 43-track import was still what played. The
            # announcement belongs to _announce_ready, after the swap.
            print(f"[job] {name} verificada, pendiente de reemplazar el original",
                  flush=True)
    return ok, msg


JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "")
_JELLYFIN_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    ".jellyfin-token")


def _jellyfin_token():
    """Its own credential, in its own 0600 file, never another service's.

    Empty when the file is absent, which disables the Jellyfin check rather than
    breaking anything."""
    tok = os.environ.get("JELLYFIN_TOKEN", "")
    if tok:
        return tok
    try:
        with open(_JELLYFIN_TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""
# Jellyfin's path for a library that media-manager knows by MEDIA_ROOT
JELLYFIN_MOVIES = os.environ.get("JELLYFIN_MOVIES", "/hdd1/Movies")
# whose watch history decides "Ya disponible" vs "Mejorada" -- the person the
# notifications go to. Empty means the distinction falls back to whether a
# previous copy was replaced.
JELLYFIN_USER = os.environ.get("JELLYFIN_USER", "")


def _jellyfin_refresh(folder, file_name, title=None, timeout=90):
    """Make Jellyfin re-read the file we just put in place, and say whether it
    now holds it. Returns (ok, detail).

    Swapping the file leaves Jellyfin serving cached media streams from the copy
    that is gone: the audio list it offers is the old one. Announcing a film as
    ready while that is true means sitting down and picking a track that is not
    there. Skipped silently when no token is configured -- it must never block a
    notification, only strengthen it."""
    token = _jellyfin_token()
    _jellyfin_refresh.played = False
    if not (JELLYFIN_URL and token):
        return True, "sin credenciales de Jellyfin: no verificado"
    path = f"{JELLYFIN_MOVIES}/{folder}/{file_name}"
    hdr = ["-H", f'Authorization: MediaBrowser Token="{token}"']
    # asking as a user also returns whether that user has already watched it
    base = f"{JELLYFIN_URL}/Users/{JELLYFIN_USER}/Items" if JELLYFIN_USER else f"{JELLYFIN_URL}/Items"
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), *hdr,
             # search by TITLE, not by filename: Jellyfin matches the item's name
             # and knows "Superman", never "Superman (2025)". Searching the file
             # stem found nothing and every title came out as "falta indexar".
             f"{base}?recursive=true&includeItemTypes=Movie&fields=Path,UserData"
             f"&searchTerm={urllib.parse.quote((title or os.path.splitext(file_name)[0])[:40])}"],
            capture_output=True, text=True).stdout
        items = (json.loads(out or "{}") or {}).get("Items", [])
        match = next((i for i in items if i.get("Path") == path), None)
        if not match:
            return False, "Jellyfin no tiene el fichero indexado"
        _jellyfin_refresh.played = bool((match.get("UserData") or {}).get("Played"))
        subprocess.run(["curl", "-s", "-o", "/dev/null", "--max-time", str(timeout),
                        "-X", "POST", *hdr,
                        f"{JELLYFIN_URL}/Items/{match['Id']}/Refresh"
                        "?metadataRefreshMode=Default&imageRefreshMode=FullRefresh"
                        "&replaceAllImages=false"], check=False)
        return True, "refrescado"
    except Exception as e:
        return True, f"no se pudo consultar Jellyfin: {e}"


_RES_WORD = {"uhd": "4K", "fhd": "Full HD", "sd": "SD"}
_LANG_WORD = {"eng": "inglés", "spa": "español", "jpn": "japonés", "fre": "francés",
              "ger": "alemán", "ita": "italiano", "por": "portugués", "kor": "coreano",
              "chi": "chino", "rus": "ruso", "swe": "sueco", "nor": "noruego",
              "dan": "danés", "dut": "neerlandés", "pol": "polaco", "ara": "árabe"}


def _lang_phrase(langs):
    """"español e inglés" -- a list a person reads, not ISO codes."""
    words = [_LANG_WORD.get(l, l) for l in langs]
    if not words:
        return "sin audio"
    if len(words) == 1:
        return words[0].capitalize()
    return (", ".join(w for w in words[:-1]) + " y " + words[-1]).capitalize()


def _friendly_detail(conn, kind, owner_id, owner):
    """What the film is, in the words someone reading a phone notification uses.

    "4K Dolby Vision · Español e inglés · Atmos", not "3840x1608 · eng/spa/eng+atmos".
    The technical line was written for me reading logs, not for the person
    deciding whether to sit down and watch something."""
    bits = []
    q = _quality(dict(owner)) if owner.get("width") else None
    if q:
        bits.append(_RES_WORD.get(q["res"], q["res"].upper()))
    hdr = (owner.get("hdr") or "").upper()
    if "DV" in hdr or "DOLBY VISION" in hdr:
        bits.append("Dolby Vision")
    elif "HDR10+" in hdr:
        bits.append("HDR10+")
    elif "HDR" in hdr:
        bits.append("HDR10")
    rows = conn.execute(
        f"SELECT out_lang, codec FROM tracks WHERE {_owner_col(kind)}=? AND type='audio' "
        "AND keep=1 ORDER BY out_order", (owner_id,)).fetchall()
    seen, atmos = [], False
    for r in rows:
        lg = r["out_lang"] or "und"
        if lg not in seen:
            seen.append(lg)
        if "atmos" in (r["codec"] or "").lower():
            atmos = True
    detail = " · ".join([b for b in bits if b] + [_lang_phrase(seen)])
    if atmos:
        detail += " · Atmos"
    return detail


RADARR_URL = os.environ.get("RADARR_URL", "http://localhost:7878")
_RADARR_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  ".radarr-token")


def _radarr_token():
    """Radarr has no per-application keys in this version: this is the global
    key, so a leak is "Radarr compromised", not "media-manager compromised".
    Same handling as the webhook secret -- own file, 0600, gitignored, never in
    a message or a log."""
    tok = os.environ.get("RADARR_TOKEN", "")
    if tok:
        return tok
    try:
        with open(_RADARR_TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _radarr(method, path, body=None, timeout=90):
    token = _radarr_token()
    if not token:
        return None
    cmd = ["curl", "-s", "--max-time", str(timeout), "-X", method,
           "-H", f"X-Api-Key: {token}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd + [f"{RADARR_URL}/api/v3/{path}"],
                         capture_output=True, text=True).stdout
    try:
        return json.loads(out or "null")
    except ValueError:
        return None


def _finish_staging(conn, movie_id):
    """Retire the staging copy once the normalised file is in place.

    Order matters and it is not the obvious one: repoint Radarr first, delete
    the staging folder second, rescan third. Deleting before repointing leaves
    Radarr looking at a path that no longer exists, and hasFile=false does not
    mean "upgradeable" to Radarr -- it means missing, and it re-grabs."""
    folder = _STAGED.pop(movie_id, None)
    if not folder:
        return
    owner = _owner_info(conn, "movie", movie_id)
    dest = os.path.join(MEDIA_ROOT, folder)
    if not (owner and owner["file"] and os.path.exists(os.path.join(dest, owner["file"]))):
        print(f"[staging] {folder!r}: sin fichero final, no retiro nada", flush=True)
        return
    tmdb = owner.get("tmdb_id")
    movies = _radarr("GET", "movie") or []
    m = next((x for x in movies if x.get("tmdbId") == tmdb), None)
    if not m:
        print(f"[staging] {folder!r}: Radarr no la conoce, dejo staging intacto", flush=True)
        return
    m["path"] = f"/movies/{folder}"
    m["rootFolderPath"] = "/movies"
    if _radarr("PUT", f"movie/{m['id']}?moveFiles=false", m) is None:
        print(f"[staging] {folder!r}: falló el repunte, dejo staging intacto", flush=True)
        return
    src = os.path.join(STAGING_ROOT, folder)
    try:
        if os.path.isdir(src):
            shutil.rmtree(src)
    except OSError as e:
        print(f"[staging] no pude borrar {src!r}: {e}", flush=True)
    _radarr("POST", "command", {"name": "RescanMovie", "movieIds": [m["id"]]})
    print(f"[staging] {folder!r} retirada de staging y repuntada a /movies", flush=True)


def _announce_ready(conn, kind, owner_id):
    """Say a film is available -- once it actually is, with its file in place.

    Called after the job output has replaced the original, never at verification:
    a verified output still sitting next to the source changes nothing for the
    viewer, and the dotfile naming means Jellyfin cannot even see it."""
    if kind != "movie":
        return
    owner = _owner_info(conn, kind, owner_id)
    name = owner["title"] or owner["folder"]
    _restore_artwork(kind, owner["folder"])
    pending, notes = _readiness(conn, kind, owner_id, owner)
    seen, why = _jellyfin_refresh(owner["folder"], owner["file"], owner.get("title"))
    if not seen:
        pending.append("indexar en Jellyfin")
    elif why and why != "refrescado":
        notes.append(why)
    detail = _friendly_detail(conn, kind, owner_id, owner)
    if pending:
        _notify(f"Casi lista: {name}", f"falta {', '.join(pending)} · {detail}",
                tags="warning", priority=4)
        return
    # A title that replaced a previous copy is an upgrade, not a premiere: the
    # recycled copy is the evidence, and saying "ya disponible" about a film the
    # viewer already owns reads as noise.
    # Two ways a title is an upgrade rather than a premiere: it replaced a copy
    # we held (the recycled file proves it), or the viewer has already watched it
    # -- Captain America was seen in June and its old file was deleted as corrupt,
    # so only the second signal catches that one.
    replaced = bool(graft.recycled_copy(owner["folder"])) if kind == "movie" else False
    already_seen = bool(getattr(_jellyfin_refresh, "played", False))
    head = "Mejorada" if (replaced or already_seen) else "Ya disponible"
    _notify(f"{head}: {name}", detail + (" · " + ", ".join(notes) if notes else ""),
            tags="sparkles" if replaced else "white_check_mark")


def _reinspect_in_place(conn, kind, owner_id, path):
    """Re-read the file that just replaced the original and rewrite its tracks.

    Nothing did this, so after every remux the tracks table still described the
    file that had been deleted: Inside Out 2 carried 41 rows for a file with 8.
    Worse, the UPDATE above stamps updated_at to now, so path_unchanged then
    decides the row is newer than the file and never looks again -- the lie is
    permanent and preflight starts refusing jobs on titles that are correct.

    Scanning through upsert_movie/upsert_show would hit that same skip, so the
    file is inspected directly. _upsert_tracks matches on track_sig, so the rows
    that survived the remux keep their config and the dropped ones are pruned.
    """
    try:
        info = scan.inspect_file(path)
    except Exception as e:
        print(f"[finalize] no pude releer {path}: {e}", flush=True)
        return
    col = "movie_id" if kind == "movie" else "episode_id"
    owner = _owner_info(conn, kind, owner_id)
    ext_subs = scan.find_external_subs(os.path.dirname(path),
                                       os.path.splitext(os.path.basename(path))[0])
    scan._upsert_tracks(conn, owner_id, info["tracks"], ext_subs,
                        owner.get("original_language"), owner_col=col)
    table = _owner_table(kind)
    conn.execute(
        f"UPDATE {table} SET video_codec=?, width=?, height=?, bitrate=?, "
        f"duration=?, size_bytes=?, hdr=? WHERE id=?",
        (info["video_codec"], info["width"], info["height"], info["bitrate"],
         info["duration"], info["size_bytes"], info["hdr"], owner_id))
    conn.commit()


def _fsync_file(path):
    """Block until `path` is durably on disk."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _delete_original(conn, kind, owner_id):
    owner = _owner_or_404(conn, kind, owner_id)
    if owner["status"] != "clean":
        raise HTTPException(400, "not verified clean yet")
    folder = os.path.join(_root_for(kind), owner["folder"])

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
        # The output has to be on the disk before the only other copy goes. The
        # verification that got us here read it through the page cache, which
        # proves nothing about the platter: on a disk that writes at 40 MB/s the
        # kernel can hold gigabytes of dirty pages, so a power cut shortly after
        # this point loses the output AND the original. On 2026-09-18 a cut left
        # a Radarr import 8 MB short; a remux finished minutes earlier survived
        # only because it was old enough to have been flushed.
        _fsync_file(out)
        os.remove(old_path)
    col = _owner_col(kind)
    # External .srt files are Bazarr's, not ours: it maintains and re-syncs
    # them, and deleting one only makes it download the file again. We stopped
    # adopting them (scan.ADOPT_EXTERNAL_SUBS), so nothing here should carry an
    # ext_path any more -- but a row written before that change still could,
    # and deleting a subtitle we did not mux in would be silent data loss.
    for t in conn.execute(f"SELECT ext_path FROM tracks WHERE {col}=? AND ext_path IS NOT NULL", (owner_id,)):
        if t["ext_path"] and os.path.exists(t["ext_path"]):
            print(f"[delete-original] conservo el subtítulo externo {t['ext_path']!r}", flush=True)
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
    now = _now()
    conn.execute(f"UPDATE {table} SET file=?, output_file=NULL, updated_at=? WHERE id=?",
                 (final_name, now, owner_id))
    _reinspect_in_place(conn, kind, owner_id, final_path)
    if freed > 0:
        total = int(_get_setting(conn, "reclaimed_bytes", "0")) + freed
        _set_setting(conn, "reclaimed_bytes", str(total))
    conn.commit()
    # the file is in place now -- this is the first moment the film is genuinely
    # watchable, so this is where availability gets announced
    _announce_ready(conn, kind, owner_id)
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
