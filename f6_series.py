"""F6 for series: bring every episode to the standard in ONE rewrite, or none.

    f6_series.py                                  # plan: what each episode would get (reads nothing but media.db)
    f6_series.py --run [--limit N] [--show "<folder>"] [--ignore-downloads]

Decisions (Antonio): D1 2026-10-04, an episode whose only difference is order or default flags is NOT
rewritten; mkvpropedit fixes the flags and the order stays. D2 2026-10-04, a sidecar that was embedded moves
to /srv/storage/.recycle (same relative path), so Jellyfin does not list it twice. 2026-10-03: F6 waits for
the downloads, so nothing starts while a .part in Downloads/incomplete grew in the last 10 minutes.

Per episode, the decision is the app's own (scan.suggest_tracks, multi_audio like the import), computed on an
in-memory copy of media.db with the filtered sidecars added as external tracks:
  remux     a track is dropped or a sidecar is embedded: commands.build_mkvmerge_remux to a hidden temp file
            in the same folder; verified (layout, duration, default flags, chapters, and the md5 of every
            kept stream against the original); fsync; os.replace under the SAME name (delete-original would
            rename the episode and lose its title); app._reinspect_in_place.
  propedit  only default flags differ: mkvpropedit flag-default, nothing else.
  blocked   the plan would pick one of several Spanish full text subtitles with no variant: that is a coin
            toss between Latino and castellano until subs_variant_pass has been reviewed and applied (P1).
Editing a file makes Jellyfin drop its media segments: after a show, wait for Jellyfin to re-read it and run
"Media Segment Scan" (PLAN-CONVENCION.md §9).
"""
import json, os, re, signal, sqlite3, subprocess, sys, time

import commands
import library_audit as la
import scan

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "f6_series.jsonl")
INCOMPLETE = "/srv/storage/Downloads/incomplete"
RECYCLE = "/srv/storage/.recycle"
QUIET = 600
NICE = ["ionice", "-c2", "-n7", "nice", "-n", "10"]


def downloads_busy(root=INCOMPLETE, quiet=QUIET, now=None):
    now = now or time.time()
    for dp, _, fs in os.walk(root):
        for f in fs:
            try:
                if now - os.path.getmtime(os.path.join(dp, f)) < quiet:
                    return True
            except OSError:
                pass
    return False


def filtered_sidecars(folder, stem, log_text):
    """Sidecars of this episode that went through the subtitle filter and were not rejected by it."""
    out = []
    for f in sorted(os.listdir(folder)):
        if not (f.startswith(stem + ".") and f.lower().endswith(".srt")):
            continue
        status = re.findall(r"^\[subs\] '?(?:[^\n]*/)?" + re.escape(f) + r"'?: (\w+)", log_text, re.M)
        if (status and status[-1] == "ok") or (not status and os.path.exists(os.path.join(folder, f + ".orig"))):
            out.append({"ext_path": os.path.join(folder, f), "lang": scan.guess_srt_lang(f),
                        "sdh": int(bool(re.search(r"\.(hi|sdh|cc)\.", f, re.I)))})
    return out


def plan(conn, eid, sidecars, kind="episode"):
    """-> (action, rows, why). Works on `conn`, which must be a throwaway copy. kind: episode | movie."""
    col, table = ("episode_id", "episodes") if kind == "episode" else ("movie_id", "movies")
    for s in sidecars:
        conn.execute(f"INSERT INTO tracks ({col}, mkv_id, type, codec, lang, name, ext_path, sdh_flag, keep) "
                     "VALUES (?, NULL, 'subtitle', 'SubRip/SRT', ?, ?, ?, ?, 1)",
                     (eid, s["lang"], os.path.basename(s["ext_path"]), s["ext_path"], s["sdh"]))
    full_spa = [r for r in map(dict, conn.execute(f"SELECT * FROM tracks WHERE {col}=? AND type='subtitle'", (eid,)))
                if r["lang"] == "spa" and not r["forced_flag"] and scan._sub_class(r) == "text"]
    scan.suggest_tracks(conn, eid, table=table, multi_audio=(kind == "episode"))
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM tracks WHERE {col}=?", (eid,))]
    if len(full_spa) > 1:
        return "blocked", rows, f"{len(full_spa)} Spanish full text subtitles without variant (P1 first)"
    dropped = [r for r in rows if r["mkv_id"] is not None and not r["keep"]]
    embedded = [r for r in rows if r["ext_path"] and r["keep"]]
    if dropped or embedded:
        return "remux", rows, f"drop {len(dropped)}, embed {len(embedded)}"
    if any(bool(r["out_default"]) != bool(r["default_flag"]) for r in rows if r["type"] != "video" and r["keep"]):
        return "propedit", rows, "default flags"
    return "skip", rows, "conform"


# mkvmerge repeats the HEVC/AVC parameter sets and the codec-private SEI inside every keyframe when the source
# kept them only in CodecPrivate (The Seven Deadly Sins S04E04: 343 of 34813 video packets grew, same count and
# timestamps). With those NAL types removed on BOTH sides the slice data must still match bit for bit.
HEADER_NALS = {"hevc": "32|33|34|39", "h264": "6|7|8"}


def secs(tag):
    """'00:24:39.895000000' -> seconds, None when the track carries no statistics tag."""
    if not tag:
        return None
    h, m, x = tag.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


# A text subtitle is compared as the subtitle FILE it makes, not packet by packet: mkvmerge rewrites every ASS
# block one byte shorter (Dragon Ball Super S01E28: 12816 packets, 170 -> 169 bytes) while the extracted .ass is
# identical byte for byte. Same single read of the file: the text goes to temp files beside the hash output.
TEXT_SUBS = {"ass": "ass", "ssa": "ass", "subrip": "srt"}


def stream_md5(path, ids, normalize=False):
    """One md5 per id, in order: packets for video/audio/bitmap subs, the rendered file for text subs."""
    import hashlib, shutil, tempfile
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_name", "-of", "csv=p=0",
                            path], capture_output=True, text=True).stdout.split()
    codec = dict(c.split(",")[:2] for c in probe if "," in c)
    packets = [i for i in ids if codec.get(str(i)) not in TEXT_SUBS]
    texts = [i for i in ids if codec.get(str(i)) in TEXT_SUBS]
    d = tempfile.mkdtemp(dir="/var/tmp")
    try:
        cmd = NICE + ["ffmpeg", "-v", "error", "-nostdin", "-i", path]
        if packets:
            for n, i in enumerate(packets):
                cmd += ["-map", f"0:{i}"]
                if normalize and codec.get(str(i)) in HEADER_NALS:
                    cmd += [f"-bsf:{n}", f"filter_units=remove_types={HEADER_NALS[codec[str(i)]]}"]
            cmd += ["-c", "copy", "-f", "streamhash", "-hash", "md5", os.path.join(d, "packets.txt")]
        for i in texts:
            cmd += ["-map", f"0:{i}", "-c", "copy", "-f", TEXT_SUBS[codec[str(i)]], os.path.join(d, f"{i}.sub")]
        subprocess.run(cmd, capture_output=True, text=True)
        res = {}
        if packets and os.path.exists(os.path.join(d, "packets.txt")):
            got = [ln.split("=", 1)[1] for ln in open(os.path.join(d, "packets.txt")).read().split() if "MD5=" in ln]
            res.update(zip(packets, got))
        for i in texts:
            f = os.path.join(d, f"{i}.sub")
            if os.path.exists(f):
                res[i] = hashlib.md5(open(f, "rb").read()).hexdigest()
        return [res[i] for i in ids] if len(res) == len(ids) else []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def chapters(path):
    x = subprocess.run(["mkvextract", path, "chapters", "-"], capture_output=True, text=True).stdout
    return re.findall(r"<ChapterTimeStart>([\d:.]+)", x), re.findall(r"<ChapterString>(.*?)</ChapterString>", x, re.S)


def remux_verified(path, rows):
    """Rewrite `path` in place per `rows` (suggest_tracks output). -> None on success, else why it was refused.
    The original is untouched unless every check passes."""
    info = la.probe(path)
    props = info["container"]["properties"]
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".f6.mkv")
    argv = commands.build_mkvmerge_remux(rows, props.get("title") or "", path, tmp)
    r = subprocess.run(NICE + argv, capture_output=True, text=True)
    if r.returncode not in (0, 1) or not os.path.exists(tmp):
        return f"mkvmerge rc={r.returncode} {r.stdout[-200:]}"
    try:
        kept = [t for t in rows if t["keep"]]
        order = (sorted([t for t in kept if t["type"] == "video"], key=lambda t: t["mkv_id"])
                 + sorted([t for t in kept if t["type"] == "audio"], key=lambda t: t["out_order"])
                 + sorted([t for t in kept if t["type"] == "subtitle"], key=lambda t: t["out_order"]))
        new = la.probe(tmp)
        nt = new["tracks"]
        if len(nt) != len(order):
            return f"tracks {len(nt)} != planned {len(order)}"
        kinds = {"video": "video", "audio": "audio", "subtitle": "subtitles"}
        if [t["type"] for t in nt] != [kinds[t["type"]] for t in order]:
            return "track layout differs from the plan"
        # per kept track, not the container: the container lasts as long as its LONGEST track, and dropping a
        # subtitle that ran past the video legitimately shortens it (DBZ S09E15: container 1481.8 s, video 1479.9 s)
        old_tr = {t["id"]: t for t in info["tracks"]}
        for t, n in zip(order, nt):
            if t["mkv_id"] is None or t["type"] == "subtitle":    # subtitle statistics are recomputed; the text is compared below
                continue
            a, b = secs(old_tr[t["mkv_id"]]["properties"].get("tag_duration")), secs(n["properties"].get("tag_duration"))
            if a is not None and b is not None and abs(a - b) > 0.1:
                return f"duration of track {t['mkv_id']} differs: {a:.3f} vs {b:.3f} s"
        if not any(not t["keep"] for t in rows) and abs(
                (new["container"]["properties"].get("duration") or 0) - (props.get("duration") or 0)) > 1e8:
            return "duration differs by more than 0.1 s"
        for t, n in zip(order, nt):
            if t["type"] != "video" and bool(n["properties"].get("default_track")) != bool(t["out_default"]):
                return f"default flag of output track {n['id']}"
            if t["type"] != "video" and n["properties"].get("language") != (t["out_lang"] or "und"):
                return f"language of output track {n['id']}: {n['properties'].get('language')}"
        if chapters(path) != chapters(tmp):
            return "chapters differ"
        pairs = [(t["mkv_id"], i) for i, t in enumerate(order) if t["mkv_id"] is not None]
        a, b = stream_md5(path, [p for p, _ in pairs]), stream_md5(tmp, [q for _, q in pairs])
        if a and a != b:
            a, b = stream_md5(path, [p for p, _ in pairs], True), stream_md5(tmp, [q for _, q in pairs], True)
        if not a or a != b:
            return "stream hashes differ"
        fd = os.open(tmp, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, path)
        tmp = None
        return None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def propedit_flags(path, rows):
    argv = ["mkvpropedit", path]
    for t in rows:
        if t["type"] != "video" and t["keep"] and t["mkv_id"] is not None and bool(t["out_default"]) != bool(t["default_flag"]):
            argv += ["--edit", f"track:{t['mkv_id'] + 1}", "--set", f"flag-default={1 if t['out_default'] else 0}"]
    r = subprocess.run(argv, capture_output=True, text=True)
    return None if r.returncode == 0 else f"mkvpropedit rc={r.returncode} {r.stdout[-200:]}"


def recycle(path):
    dst = os.path.join(RECYCLE, os.path.relpath(path, os.path.dirname(RECYCLE)))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    base, n = dst, 1
    while os.path.exists(dst):            # Bazarr re-downloads under the same name; never overwrite what is there
        dst, n = f"{base}.{n}", n + 1
    os.rename(path, dst)
    return dst


def main(run, limit, show, ignore_downloads=False):
    src = sqlite3.connect(os.path.join(HERE, "media.db"))
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    mem.row_factory = sqlite3.Row
    q = ("SELECT e.id, e.folder ef, e.file, s.folder sf FROM episodes e JOIN shows s ON s.id=e.show_id "
         "WHERE e.file IS NOT NULL" + (" AND s.folder=?" if show else "") + " ORDER BY e.size_bytes")
    eps = mem.execute(q, (show,) if show else ()).fetchall()
    busy = {r[0] for r in src.execute("SELECT episode_id FROM jobs WHERE status IN ('running','queued') AND episode_id IS NOT NULL")}
    log_text = open(os.path.join(HERE, "server.log"), encoding="utf-8", errors="replace").read()
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    counts, done = {}, 0
    out = open(LOG, "a") if run else None
    app = None
    for e in eps:
        folder = os.path.join(la.SHOWS, e["sf"], e["ef"])
        path = os.path.join(folder, e["file"])
        if not os.path.exists(path):
            continue
        sidecars = filtered_sidecars(folder, os.path.splitext(e["file"])[0], log_text)
        action, rows, why = plan(mem, e["id"], sidecars)
        counts[action] = counts.get(action, 0) + 1
        if not run or action in ("skip", "blocked"):
            continue
        if stop or (limit and done >= limit) or not os.path.ismount("/srv/storage"):
            break
        while not ignore_downloads and downloads_busy():
            if stop:
                break
            print("downloads active, waiting", flush=True)
            time.sleep(QUIET)
        if stop:
            break
        if e["id"] in busy:
            print("SKIP (job active)", e["file"], flush=True)
            continue
        t0 = time.time()
        err = remux_verified(path, rows) if action == "remux" else propedit_flags(path, rows)
        moved = []
        if not err and action == "remux":
            moved = [recycle(r["ext_path"]) for r in rows if r["ext_path"] and r["keep"] and os.path.exists(r["ext_path"])]
        if not err:
            if app is None:
                import app
            c = app.get_db()
            try:
                if action == "remux":
                    c.execute("UPDATE episodes SET output_file=NULL, status='clean', updated_at=? WHERE id=?", (app._now(), e["id"]))
                app._reinspect_in_place(c, "episode", e["id"], path)
                c.commit()
            finally:
                c.close()
        done += 1
        rec = {"id": e["id"], "file": e["file"], "action": action, "why": why, "status": "FAILED" if err else "ok",
               "error": err, "recycled": moved, "secs": round(time.time() - t0)}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        print(rec["status"], action, e["file"][:60], err or why, f"{rec['secs']}s", flush=True)
    print(counts if not run else f"{done} processed; plan counts so far {counts}", flush=True)


def run_movie(folder, run=False):
    """F6 for one movie folder (same checks as the episodes): `f6_series.py --movie "<folder>" [--run]`."""
    src = sqlite3.connect(os.path.join(HERE, "media.db"))
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    mem.row_factory = sqlite3.Row
    m = mem.execute("SELECT id, folder, file FROM movies WHERE folder=?", (folder,)).fetchone()
    path = os.path.join(la.MOVIES, m["folder"], m["file"])
    log_text = open(os.path.join(HERE, "server.log"), encoding="utf-8", errors="replace").read()
    sidecars = filtered_sidecars(os.path.dirname(path), os.path.splitext(m["file"])[0], log_text)
    action, rows, why = plan(mem, m["id"], sidecars, kind="movie")
    print(action, why, [(r["mkv_id"], r["type"], r["lang"], r["codec"][:10], r["keep"], r["out_order"], r["out_default"])
                        for r in rows], flush=True)
    if action not in ("remux", "propedit") or not run:
        return None
    t0 = time.time()
    err = remux_verified(path, rows) if action == "remux" else propedit_flags(path, rows)
    moved = [recycle(r["ext_path"]) for r in rows if not err and action == "remux" and r["ext_path"] and r["keep"]
             and os.path.exists(r["ext_path"])]
    if not err:
        import app
        c = app.get_db()
        try:
            c.execute("UPDATE movies SET status='clean', updated_at=? WHERE id=?", (app._now(), m["id"]))
            app._reinspect_in_place(c, "movie", m["id"], path)
            c.commit()
        finally:
            c.close()
    rec = {"movie": m["id"], "file": m["file"], "action": action, "why": why, "status": "FAILED" if err else "ok",
           "error": err, "recycled": moved, "secs": round(time.time() - t0)}
    open(os.path.join(HERE, "f6_movies.jsonl"), "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(rec, flush=True)
    return rec


def run_movies(run, ignore_downloads=False):
    """Every movie, smallest file first; one already logged ok is not planned again. Stops on SIGTERM or a lost mount."""
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    log = os.path.join(HERE, "f6_movies.jsonl")
    done = {json.loads(l)["file"] for l in open(log) if '"ok"' in l} if os.path.exists(log) else set()
    con = sqlite3.connect(os.path.join(HERE, "media.db"))
    todo = [(f, fi) for f, fi in con.execute("SELECT folder, file FROM movies WHERE file IS NOT NULL") if fi not in done]
    con.close()
    todo.sort(key=lambda m: os.path.getsize(p) if os.path.exists(p := os.path.join(la.MOVIES, *m)) else 0)
    for folder, _ in todo:
        if stop or not os.path.ismount("/srv/storage"):
            break
        while not ignore_downloads and downloads_busy() and not stop:
            print("downloads active, waiting", flush=True)
            time.sleep(QUIET)
        if stop:
            break
        try:
            run_movie(folder, run)
        except Exception as ex:                  # one odd folder must not end a 50-hour run
            print("ERROR", folder, repr(ex), flush=True)
    print("movies done", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--movie" in a:
        run_movie(a[a.index("--movie") + 1], "--run" in a)
        sys.exit()
    if "--movies" in a:
        run_movies("--run" in a, "--ignore-downloads" in a)
        sys.exit()
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0,
         a[a.index("--show") + 1] if "--show" in a else None,
         "--ignore-downloads" in a)                  # Antonio 2026-10-04: the stalled downloads do not compete
