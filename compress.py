"""Bring films above the bitrate ceiling down to it, one x265 encode at a time, in media-manager's encode lane.

    compress.py            # plan: which films exceed the ceiling, by how much (reads headers only)
    compress.py --run      # queue the next one, unless an encode is already queued or running

Antonio 2026-10-31: re-encode what is over the ceiling (LIBRARY_STATE.md "Techo de bitrate": 4K 32 Mbps, 1080p 15,
SD 8, measured on the VIDEO stream), keep Dolby Vision, x265 on the CPU, and let it take months: it only works while
nobody is watching (playback_guard freezes HandBrakeCLI). The job is media-manager's own `encode` (HandBrake Flatpak,
which keeps the DV RPU); jobs.encode_checks refuses an output that lost DV or HDR or is not smaller, and the original
goes to .recycle, not away. A film whose encode failed once is not queued again: that needs a look, not a retry.
"""
import json, os, sqlite3, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "compress.jsonl")
MOVIES = os.environ.get("MEDIA_ROOT", "/srv/storage/Movies")
CEILING = [(1600, 32e6), (700, 15e6), (0, 8e6)]          # (min height, video bps)
QUALITY = 22                                             # HandBrake RF, the encode path's default
MARGIN = 1.10                                            # 15.1 on a 15 Mbps ceiling is not worth a lossy pass
# Never queued from here. The Odyssey (Antonio 2026-11-18): its own bitrate target, at an RF he picks from samples,
# not RF 22. Toy Story 5 (Antonio 2026-11-20): stays uncompressed for good, to keep its full Dolby Vision P7
# (BL+EL+RPU); a permanent decision, not a temporary hold.
HOLD = {"The Odyssey (2026)", "Toy Story 5 (2026)"}
# Failed encodes that say nothing about the film: Rocky IV (2082) died in the 2026-11-04 crash, the other nine had
# their tmux killed with mm-compress.service's cgroup at second 0 (fixed in c465569). These may be queued again.
INFRA_FAILED = {2082, 2084, 2085, 2086, 2087, 2088, 2089, 2090, 2091, 2092}
# Samples go before the next film (Antonio 2026-11-19): each (folder, scene start in s, RF) is one 30 s job in the
# encode lane, same preset as the full encode, one per run, in this order. One with a job of any status is never
# queued again. Scenes: the three 30 s windows with the most bytes between keyframes in the file's Cues index,
# 10 min apart, past the logos and before the credits. Toy Story 5 has none: it is never compressed (see HOLD).
SAMPLES = [("The Odyssey (2026)", s, q) for s in (2599, 3539, 7843) for q in (16, 18, 20)]


def ceiling(height):
    return next(c for h, c in CEILING if (height or 0) >= h)


def video_bps(path):
    """The video stream's own bitrate: its BPS statistics tag, else (file bits - audio bits) / duration."""
    info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout or "{}")
    tracks = info.get("tracks", [])
    v = next((t for t in tracks if t["type"] == "video"), None)
    if not v:
        return None
    bps = v["properties"].get("tag_bps")
    if bps:
        return float(bps)
    dur = (info.get("container", {}).get("properties", {}).get("duration") or 0) / 1e9
    if not dur:
        return None
    audio = sum(float(t["properties"].get("tag_bps") or 0) for t in tracks if t["type"] == "audio")
    return os.path.getsize(path) * 8 / dur - audio


def candidates(conn):
    failed = {r[0] for r in conn.execute("SELECT movie_id, id FROM jobs WHERE kind='encode' AND status='failed'")
              if r[1] not in INFRA_FAILED}
    active = {r[0] for r in conn.execute("SELECT movie_id FROM jobs WHERE status IN ('running','queued')")}
    out = []
    for mid, folder, file, height, total in conn.execute(
            "SELECT id, folder, file, height, bitrate FROM movies WHERE status='clean' AND file IS NOT NULL"):
        if mid in failed or mid in active or folder in HOLD or not file.lower().endswith(".mkv"):
            continue
        if total and total <= ceiling(height):     # the container total bounds the video: no header to read
            continue
        path = os.path.join(MOVIES, folder, file)
        if not os.path.exists(path):
            continue
        bps, cap = video_bps(path), ceiling(height)
        if bps and bps > cap * MARGIN:
            out.append({"id": mid, "folder": folder, "bps": bps, "cap": cap, "size": os.path.getsize(path)})
    return sorted(out, key=lambda c: c["bps"] / c["cap"], reverse=True)   # furthest over the ceiling first


def next_sample(conn):
    """-> (movie id, folder, start, rf) of the first entry in SAMPLES that has no job yet, or None."""
    for folder, s, q in SAMPLES:
        m = conn.execute("SELECT id FROM movies WHERE folder=?", (folder,)).fetchone()
        if m and not conn.execute("SELECT 1 FROM jobs WHERE movie_id=? AND kind='sample' AND cmd LIKE ?",
                                  (m[0], f"%.sample.rf{q}.t{s}.mkv%")).fetchone():
            return m[0], folder, s, q
    return None


def queue_next(conn):
    # Without the Flatpak build in HANDBRAKE_CLI the job runs Arch's HandBrakeCLI, which strips Dolby Vision: a
    # day and a half of CPU that encode_checks then throws away. mm-compress.service started without
    # .env.systemd on its first run (Rocky IV, job 2081, cancelled at 0.05 %).
    if "flatpak" not in os.environ.get("HANDBRAKE_CLI", ""):
        raise SystemExit("HANDBRAKE_CLI is not the Flatpak HandBrake (load .env.systemd); not queuing")
    if conn.execute("SELECT 1 FROM jobs WHERE kind IN ('encode','sample') AND status IN ('running','queued')").fetchone():
        return None
    import app
    smp = next_sample(conn)
    if smp:
        mid, folder, start, q = smp
        job = app._enqueue(conn, "movie", mid, "sample", q, start)
        return {"id": mid, "folder": folder, "sample": f"rf{q} t{start}", "job": job.get("job_id")}
    todo = candidates(conn)
    if not todo:
        return None
    c = todo[0]
    job = app._enqueue(conn, "movie", c["id"], "encode", QUALITY)
    app._mark_auto_finalize(conn, job.get("job_id"))
    return {**c, "job": job.get("job_id")}


if __name__ == "__main__":
    assert ceiling(2160) == 32e6 and ceiling(1080) == 15e6 and ceiling(1038) == 15e6 and ceiling(480) == 8e6
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    if "--run" not in sys.argv:
        todo = candidates(conn)
        for c in todo:
            print(f"{c['bps'] / 1e6:6.1f} / {c['cap'] / 1e6:.0f} Mbps  {c['size'] / 1e9:5.1f} GB  {c['folder']}")
        print(len(todo), "films over the ceiling,", round(sum(c["size"] for c in todo) / 1e12, 2), "TB")
        sys.exit()
    q = queue_next(conn)
    if q:
        rec = {"sample": q["sample"]} if "sample" in q else {"bps": round(q["bps"]), "cap": q["cap"]}
        open(LOG, "a").write(json.dumps({"time": time.strftime("%F %T"), "status": "ok", "path": q["folder"],
                                         **rec, "job": q["job"]}) + "\n")
    print("queued", q["folder"] if q else "nothing")
