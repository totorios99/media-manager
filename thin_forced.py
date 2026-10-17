"""Text subtitle tracks that carry only signs/foreign lines but are not flagged forced (Thunderbolts 2025, 10-17:
Spanish 50 cues, English 31, both unflagged and the Spanish one default; the movie had no full subtitle at all).

    thin_forced.py [--run]

Per file, mkvmerge -J only (num_index_entries = cue count for SubRip, no extraction). A non-forced text track is thin when
  * it has < 15 % of the cues of the fullest other text track in the file (American Sniper ES 7 vs EN 1897), or
  * it has < 0.5 cues per minute (Thunderbolts ES 50 in 127 min).
Eraserhead EN (136 cues, 89 min, its only track) is a real full track and stays as is.
A thin track gets flag-forced=1 and flag-default=0 (mkvpropedit, in place, no remux); when the same language has a forced
track holding the full dialogue (Project Hail Mary ES: 83 unflagged, 3356 forced) the flags are swapped instead.
Each flagged language without a full track is logged to thin_forced.jsonl; bazarr_watch reads it as wanted from Bazarr.
"""
import json, os, re, sqlite3, subprocess, sys

import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "thin_forced.jsonl")
TEXT = re.compile(r"SubRip", re.I)   # ponytail: ASS left out, karaoke lines inflate its cue counts
RATIO, PER_MIN, MIN_LEN = 0.15, 0.5, 30


def decide(tracks, minutes):
    """tracks: [{"id", "lang", "n", "forced"}] -> ({id: (forced, default)}, [langs left without a full track])."""
    if minutes < MIN_LEN:
        return {}, []
    acts = {}
    for t in tracks:
        if t["forced"] or not t["n"]:
            continue
        others = [o["n"] for o in tracks if o is not t]
        if (others and t["n"] < RATIO * max(others)) or t["n"] < PER_MIN * minutes:
            acts[t["id"]] = (1, 0)
            swap = [o for o in tracks if o["lang"] == t["lang"] and o["forced"] and o["n"] > t["n"] / RATIO]
            for o in swap:
                acts[o["id"]] = (0, 1)
    missing = sorted({t["lang"] for t in tracks if t["id"] in acts and acts[t["id"]] == (1, 0)}
                     - {t["lang"] for t in tracks if acts.get(t["id"], (t["forced"], 0))[0] == 0})
    return acts, missing


def targets():
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    q = [("movie", "SELECT id, folder, file FROM movies WHERE file IS NOT NULL", la.MOVIES),
         ("episode", "SELECT e.id, s.folder || '/' || e.folder, e.file FROM episodes e JOIN shows s ON s.id=e.show_id "
                     "WHERE e.file IS NOT NULL", la.SHOWS)]
    out = [(kind, oid, os.path.join(root, folder, file)) for kind, sql, root in q for oid, folder, file in conn.execute(sql)]
    conn.close()
    return [x for x in out if x[2].endswith(".mkv") and os.path.exists(x[2])]


def main(run):
    seen = {(r["path"], r["size"]) for r in map(json.loads, open(LOG))} if os.path.exists(LOG) else set()
    n = 0
    for kind, oid, path in targets():
        size = os.path.getsize(path)
        if (path, size) in seen or not os.path.ismount("/srv/storage"):
            continue
        info = la.probe(path)
        if not info:
            continue
        minutes = (info.get("container", {}).get("properties", {}).get("duration") or 0) / 6e10
        tracks = [{"id": t["id"], "uid": t["properties"]["uid"], "lang": t["properties"].get("language"),
                   "n": t["properties"].get("num_index_entries") or 0, "forced": bool(t["properties"].get("forced_track"))}
                  for t in info["tracks"] if t["type"] == "subtitles" and TEXT.search(t["codec"])]
        acts, missing = decide(tracks, minutes)
        if not acts:
            continue
        n += 1
        print(os.path.basename(path), acts, "missing:", missing, flush=True)
        if not run:
            continue
        uid = {t["id"]: t["uid"] for t in tracks}
        edits = []
        for i, (forced, default) in acts.items():
            edits += ["--edit", f"track:={uid[i]}", "--set", f"flag-forced={forced}", "--set", f"flag-default={default}"]
        r = subprocess.run(["ionice", "-c3", "mkvpropedit", path] + edits, capture_output=True, text=True)
        if r.returncode == 0:
            import app
            c = app.get_db()
            try:
                app._reinspect_in_place(c, kind, oid, path)
                c.commit()
            finally:
                c.close()
        open(LOG, "a").write(json.dumps({"path": path, "size": os.path.getsize(path), "status": "ok" if r.returncode == 0
                                         else "FAILED", "acts": {str(k): v for k, v in acts.items()}, "missing": missing,
                                         "error": None if r.returncode == 0 else r.stdout[-200:]}, ensure_ascii=False) + "\n")
    print(n, "files with thin unflagged tracks", flush=True)


if __name__ == "__main__":
    main("--run" in sys.argv)
