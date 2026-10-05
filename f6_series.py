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


def plan(conn, eid, sidecars):
    """-> (action, rows, why). Works on `conn`, which must be a throwaway copy."""
    for s in sidecars:
        conn.execute("INSERT INTO tracks (episode_id, mkv_id, type, codec, lang, name, ext_path, sdh_flag, keep) "
                     "VALUES (?, NULL, 'subtitle', 'SubRip/SRT', ?, ?, ?, ?, 1)",
                     (eid, s["lang"], os.path.basename(s["ext_path"]), s["ext_path"], s["sdh"]))
    full_spa = [r for r in map(dict, conn.execute("SELECT * FROM tracks WHERE episode_id=? AND type='subtitle'", (eid,)))
                if r["lang"] == "spa" and not r["forced_flag"] and scan._sub_class(r) == "text"]
    scan.suggest_tracks(conn, eid, table="episodes", multi_audio=True)
    rows = [dict(r) for r in conn.execute("SELECT * FROM tracks WHERE episode_id=?", (eid,))]
    if len(full_spa) > 1:
        return "blocked", rows, f"{len(full_spa)} Spanish full text subtitles without variant (P1 first)"
    dropped = [r for r in rows if r["mkv_id"] is not None and not r["keep"]]
    embedded = [r for r in rows if r["ext_path"] and r["keep"]]
    if dropped or embedded:
        return "remux", rows, f"drop {len(dropped)}, embed {len(embedded)}"
    if any(bool(r["out_default"]) != bool(r["default_flag"]) for r in rows if r["type"] != "video" and r["keep"]):
        return "propedit", rows, "default flags"
    return "skip", rows, "conform"


def stream_md5(path, ids):
    cmd = NICE + ["ffmpeg", "-v", "error", "-nostdin", "-i", path]
    for i in ids:
        cmd += ["-map", f"0:{i}"]
    out = subprocess.run(cmd + ["-c", "copy", "-f", "streamhash", "-hash", "md5", "-"], capture_output=True, text=True)
    return [ln.split("=", 1)[1] for ln in out.stdout.split() if "MD5=" in ln]


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
        if abs((new["container"]["properties"].get("duration") or 0) - (props.get("duration") or 0)) > 1e8:
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
    dst = os.path.join(RECYCLE, os.path.relpath(path, "/srv/storage"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(path, dst)
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


if __name__ == "__main__":
    a = sys.argv[1:]
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0,
         a[a.index("--show") + 1] if "--show" in a else None,
         "--ignore-downloads" in a)                  # Antonio 2026-10-04: the stalled downloads do not compete
