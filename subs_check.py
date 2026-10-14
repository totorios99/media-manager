"""Embedded text subtitles: no ads, in sync (Antonio, 2026-10-11: "sin anuncios en los subs, todo bien sincronizado").

    subs_check.py                         # plan: which files would be read
    subs_check.py --run [--limit N] [--match REGEX] [--ignore-window]

The ad sweep of 2026-10-01 read sidecars only; the 2567 text tracks inside the MKVs were never checked.
Per file (one mkvextract for all its SubRip tracks in spa/eng):
  * ads: subs_clean.strip_ads on every track;
  * sync: the main track (first full Spanish, else English) measured in 3 windows (subs_clean.window_offsets).
    Off by more than 0.3 s -> alass to the file's English track, then a linear refit; kept only if the windows pass.
    Still off -> left as it is and listed in subs_check_review.jsonl (a slightly-off sub beats none).
A changed track replaces the old one in place through ocr_pass.remux (every other stream verified bit for bit).
ASS tracks (anime fansubs, timed to their release) are not touched: subs_clean parses SRT only.
Resumable: a file is skipped while its path and size match an ok line of subs_check.jsonl. Series first (small files).
"""
import json, os, re, signal, sqlite3, subprocess, sys, tempfile, time

import library_audit as la
import ocr_pass
import overnight
import subs_clean as sc

HERE = os.path.dirname(os.path.abspath(__file__))
LOG, REVIEW = os.path.join(HERE, "subs_check.jsonl"), os.path.join(HERE, "subs_check_review.jsonl")
ALASS = os.path.expanduser("~/.local/bin/alass")


def targets(conn):
    q = ("SELECT DISTINCT 'episode', e.id, s.folder || '/' || e.folder, e.file FROM tracks t JOIN episodes e ON e.id=t.episode_id "
         "JOIN shows s ON s.id=e.show_id WHERE t.type='subtitle' AND t.ext_path IS NULL AND t.codec LIKE '%SubRip%' "
         "UNION SELECT DISTINCT 'movie', m.id, m.folder, m.file FROM tracks t JOIN movies m ON m.id=t.movie_id "
         "WHERE t.type='subtitle' AND t.ext_path IS NULL AND t.codec LIKE '%SubRip%'")
    out = []
    for kind, oid, folder, file in conn.execute(q):
        p = os.path.join(la.SHOWS if kind == "episode" else la.MOVIES, folder, file)
        if os.path.exists(p):
            out.append((kind, oid, p))
    return sorted(out, key=lambda x: (x[0] != "episode", os.path.getsize(x[2])))


def worst(w):
    vals = [abs(o) for _, o in w if o is not None]
    return max(vals) if vals else None


def check_file(path, measure_fn=None):
    """-> (decisions for ocr_pass.remux, report dict). Writes cleaned SRTs to a temp dir kept in report['dir']."""
    info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout)
    srts = [t for t in info["tracks"] if t["type"] == "subtitles" and "SubRip" in t["codec"]
            and t["properties"].get("language") in ("spa", "eng")]
    rep = {"ads": {}, "sync": None}
    if not srts:
        return [], rep
    d = tempfile.mkdtemp(dir="/var/tmp", prefix="subs_check_")
    rep["dir"] = d
    subprocess.run(["ionice", "-c3", "mkvextract", path, "tracks"] + [f"{t['id']}:{d}/{t['id']}.srt" for t in srts],
                   capture_output=True, check=True)
    changed = {}
    for t in srts:
        f = f"{d}/{t['id']}.srt"
        cues = sc.parse(sc.decode(open(f, "rb").read()))
        kept, removed = sc.strip_ads(cues)
        if removed and kept:
            open(f, "w", encoding="utf-8").write(sc.render(kept))
            rep["ads"][t["id"]] = [f"{int(a)//60}:{int(a)%60:02d} {ln}" for a, ln in removed][:10]
            changed[t["id"]] = "ads"
    full = [t for t in srts if not t["properties"].get("forced_track")]
    main = next((t for t in full if t["properties"]["language"] == "spa"), None) or next(iter(full), None)
    eng = next((t for t in full if t["properties"]["language"] == "eng" and t is not main), None)
    if main:
        f = f"{d}/{main['id']}.srt"
        w = sc.window_offsets(path, f, measure_fn)
        rep["sync"] = {"before": w}
        if worst(w) is not None and worst(w) > sc.WINDOW_OFF_OK:
            backup = open(f, "rb").read()
            fixed = None
            if eng:
                out = f + ".alass.srt"
                r = subprocess.run([ALASS, f"{d}/{eng['id']}.srt", f, out], capture_output=True)
                if r.returncode == 0 and os.path.exists(out):
                    w2 = sc.window_offsets(path, out, measure_fn)
                    if sc.windows_ok(w2):
                        os.replace(out, f)
                        fixed = ("alass", w2)
            if not fixed and sc.refit_from_windows(f, w) is not None:
                w3 = sc.window_offsets(path, f, measure_fn)
                if sc.windows_ok(w3):
                    fixed = ("refit", w3)
            if not fixed:
                open(f, "wb").write(backup)
                w4 = w
                for _ in range(3):                  # Prison Break E06 needed three passes (10-13)
                    if sc.piecewise_from_windows(f, w4) is None:
                        break
                    w4 = sc.window_offsets(path, f, measure_fn)
                    if sc.windows_ok(w4):
                        fixed = ("piecewise", w4)
                        break
            if fixed:
                rep["sync"].update(how=fixed[0], after=fixed[1])
                changed[main["id"]] = (changed.get(main["id"], "") + " sync").strip()
            else:
                open(f, "wb").write(backup)
                rep["sync"]["unfixed"] = True
    decisions = []
    for t in srts:
        if t["id"] in changed:
            p = t["properties"]
            decisions.append((t, "replace", changed[t["id"]],
                              {"srt": f"{d}/{t['id']}.srt", "lang": p["language"], "ietf": p.get("language_ietf")
                               if p.get("language_ietf") not in (None, "", "es", "en", "und") else None}))
    return decisions, rep


def main(run, limit, match, ignore_window):
    import shutil
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    done = set()
    if os.path.exists(LOG):
        for l in open(LOG):
            r = json.loads(l)
            if r["status"] in ("ok", "clean"):
                done.add((r["path"], r["size"]))
    todo = [x for x in targets(conn) if (x[2], os.path.getsize(x[2])) not in done and (not match or re.search(match, x[2]))]
    conn.close()
    print(len(todo), "files to read", flush=True)
    if not run:
        return
    n = 0
    for kind, oid, path in todo:
        if stop or (limit and n >= limit) or not os.path.ismount("/srv/storage"):
            break
        while not ignore_window and not stop and not overnight.in_window():
            time.sleep(300)
        if stop:
            break
        n += 1
        t0 = time.time()
        rep, err = {}, None
        try:
            decisions, rep = check_file(path)
            if decisions:
                info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout)
                err = ocr_pass.remux(path, info, decisions)
                if not err:
                    import app
                    c = app.get_db()
                    try:
                        app._reinspect_in_place(c, kind, oid, path)
                        c.commit()
                    finally:
                        c.close()
            status = "FAILED" if err else ("ok" if decisions else "clean")
        except Exception as e:
            status, err = "ERROR", repr(e)[:300]
        finally:
            if rep.get("dir"):
                shutil.rmtree(rep["dir"], ignore_errors=True)
        sync = rep.get("sync") or {}
        rec = {"path": path, "size": os.path.getsize(path), "status": status, "error": err, "ads": rep.get("ads"),
               "sync": {k: v for k, v in sync.items()}, "secs": round(time.time() - t0)}
        open(LOG, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
        if sync.get("unfixed") or status in ("FAILED", "ERROR"):
            open(REVIEW, "a").write(json.dumps({"path": path, "status": status, "error": err, "sync": sync},
                                               ensure_ascii=False) + "\n")
        print(status, os.path.basename(path), err or "", "ads" if rep.get("ads") else "",
              sync.get("how") or ("UNFIXED sync" if sync.get("unfixed") else ""), f"{rec['secs']}s", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0,
         a[a.index("--match") + 1] if "--match" in a else None, "--ignore-window" in a)
