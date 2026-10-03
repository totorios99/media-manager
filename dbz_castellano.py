"""Dragon Ball Z: drop the castellano audio track, leave the Latino one as the default.

    dbz_castellano.py                  # plan only: what would be dropped, and on what evidence
    dbz_castellano.py --run [--limit N]

Why not the app's remux + delete-original: finalizing renames every episode from
"Dragon Ball Z (1989) - S01E01 - The New Threat.mkv" to "... - S01E01.mkv" and overwrites the
container title. Here mkvmerge copies everything except the dropped track (chapters, subtitles,
attachments, tags, name, title), and the original is replaced only after every kept stream's
packets hash identically in the new file (the app's verify checks layout and duration, not content).

Which Spanish track is castellano: all 288 episodes with two Spanish tracks come from the same
August 2026 rebuild, which put the Latino dub FIRST (180 of them say so in the track name:
'Español Latino' vs 'Castellano/Montaje Selecta'). The rest have indistinguishable names
('Español (AAC)' / 'Español (AC-3)'), so position is the evidence there; counted as basis=order.
Episode 199 (S07E05) has a single Spanish track and is left as it is.
Idempotent: an episode that no longer has two Spanish tracks is skipped.
"""
import json, os, re, sqlite3, subprocess, sys, time

import audio_default as ad
import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "dbz_castellano.jsonl")
SHOW = "Dragon Ball Z (1989) [tvdbid-81472]"
LAT = re.compile(r"latino|latinoam", re.I)
CAST = re.compile(r"castellano|selecta|españa|espana|\(es\)", re.I)


def spanish(audio):
    return [t for t in audio if ad.variant(t) in ("spa", "spa-mx", "spa-es")]


def decide(info):
    """-> (keep_ids, drop_ids, latino_id, basis) or None when there is nothing to do."""
    audio = [t for t in info["tracks"] if t["type"] == "audio"]
    spa = spanish(audio)
    if len(spa) < 2:
        return None
    name = lambda t: t["properties"].get("track_name") or ""
    named_lat = [t for t in spa if LAT.search(name(t))]
    named_cast = [t for t in spa if CAST.search(name(t))]
    if len(named_lat) == 1:
        latino, basis = named_lat[0], "name"
    elif len(named_cast) == len(spa) - 1 and named_cast:
        latino, basis = next(t for t in spa if t not in named_cast), "name"
    else:
        latino, basis = spa[0], "order"                  # the rebuild put the Latino dub first
    drop = [t for t in spa if t is not latino]
    keep_audio = [t["id"] for t in audio if t not in drop]
    return keep_audio, [t["id"] for t in drop], latino["id"], basis


def hashes(path, ids):
    cmd = ["ionice", "-c2", "-n7", "ffmpeg", "-v", "error", "-nostdin", "-i", path]
    for i in ids:
        cmd += ["-map", f"0:{i}"]
    out = subprocess.run(cmd + ["-c", "copy", "-f", "streamhash", "-hash", "md5", "-"],
                         capture_output=True, text=True)
    return [ln.split("=", 1)[1] for ln in out.stdout.split() if "MD5=" in ln]


def process(path, ep_id, conn):
    info = la.probe(path)
    d = decide(info)
    if not d:
        return {"status": "skip", "why": "no second Spanish track"}
    keep_audio, drop, latino, basis = d
    props = (info.get("container") or {}).get("properties", {})
    tracks = info["tracks"]
    kept_ids = [t["id"] for t in tracks if t["type"] != "audio" or t["id"] in keep_audio]
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".castfix.mkv")
    cmd = ["ionice", "-c2", "-n7", "nice", "-n", "10", "mkvmerge", "-o", tmp]
    if props.get("title"):
        cmd += ["--title", props["title"]]
    cmd += ["--audio-tracks", ",".join(map(str, keep_audio))]
    for t in tracks:
        if t["type"] == "audio" and t["id"] in keep_audio:
            cmd += ["--default-track-flag", f"{t['id']}:{'yes' if t['id'] == latino else 'no'}"]
    r = subprocess.run(cmd + [path], capture_output=True, text=True)
    if r.returncode not in (0, 1) or not os.path.exists(tmp):
        return {"status": "FAILED", "why": f"mkvmerge rc={r.returncode} {r.stdout[-200:]}"}
    new = la.probe(tmp)
    ok = len(new["tracks"]) == len(kept_ids) and abs(
        (new["container"]["properties"].get("duration") or 0) - (props.get("duration") or 0)) < 1e8  # 0.1 s in ns
    nd = [t for t in new["tracks"] if t["type"] == "audio" and t["properties"].get("default_track")]
    ok = ok and len(nd) == 1 and nd[0]["properties"].get("language") == "spa"
    if ok:
        ok = hashes(path, kept_ids) == hashes(tmp, list(range(len(kept_ids)))) and len(kept_ids) > 0
    if not ok:
        os.remove(tmp)
        return {"status": "FAILED", "why": "verification (layout, duration, default flag or stream hash)"}
    fd = os.open(tmp, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)
    before = os.path.getsize(path)
    os.replace(tmp, path)                               # same name, same folder: nothing else moves
    return {"status": "ok", "basis": basis, "dropped": drop, "latino": latino,
            "bytes_freed": before - os.path.getsize(path)}


def main(run, limit):
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    eps = conn.execute("SELECT e.id, e.season, e.episode, e.folder, e.file FROM episodes e JOIN shows s ON s.id=e.show_id "
                       "WHERE s.folder=? AND e.file IS NOT NULL ORDER BY e.season, e.episode", (SHOW,)).fetchall()
    busy = {r[0] for r in conn.execute("SELECT episode_id FROM jobs WHERE status IN ('running','queued') AND episode_id IS NOT NULL")}
    todo, counts = [], {"name": 0, "order": 0, "skip": 0}
    for e in eps:
        path = os.path.join(la.SHOWS, SHOW, e["folder"], e["file"])
        d = decide(la.probe(path)) if os.path.exists(path) else None
        if d:
            counts[d[3]] += 1
            todo.append((e, path))
        else:
            counts["skip"] += 1
    print(len(eps), "episodes;", {k: v for k, v in counts.items()}, "(skip = nothing to drop)")
    if not run:
        return
    import app                                           # for _reinspect_in_place; needs the service env
    log = open(LOG, "a")
    done = failed = 0
    for e, path in todo[: limit or None]:
        if e["id"] in busy:
            print("SKIP (job active)", e["file"]); continue
        t0 = time.time()
        res = process(path, e["id"], conn)
        if res["status"] == "ok":
            c = app.get_db()
            try:
                c.execute("UPDATE episodes SET output_file=NULL, status='clean', updated_at=? WHERE id=?", (app._now(), e["id"]))
                app._reinspect_in_place(c, "episode", e["id"], path)
                c.commit()
            finally:
                c.close()
        done += res["status"] == "ok"
        failed += res["status"] == "FAILED"
        log.write(json.dumps({"id": e["id"], "file": e["file"], **res, "secs": round(time.time() - t0)}, ensure_ascii=False) + "\n")
        log.flush()
        print(res["status"], e["file"][:60], res.get("why", ""), f"{time.time() - t0:.0f}s", flush=True)
    print(f"{done} done, {failed} failed")


if __name__ == "__main__":
    a = sys.argv[1:]
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0)
