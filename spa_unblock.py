"""Unblock files F6 refuses for having 2+ bare-`spa` full text subtitles (PIPELINE_GAPS.md; DBZ/R&M 2026-10-11).

    spa_unblock.py [--run]

Per blocked file, one mkvextract of its bare-`spa` text tracks, then per track:
  name says forzado/forced, < 300 words, no forced flag   -> flag-forced=1   (R&M S07 "FORZADO")
  subs_variant_pass.verdict castellano                     -> es-ES "Español (España)" (F6 drops it)
  verdict latino                                           -> es-419 "Español (Latino)"
The tags are written only when at most ONE full non-castellano Spanish track is left, so F6 can plan the file;
otherwise nothing is touched and the file goes to spa_unblock_review.jsonl (a coin toss stays a human call).
"""
import json, os, re, shutil, sqlite3, subprocess, sys, tempfile

import commands
import f6_series as f6
import library_audit as la
import subs_variant_pass as vp

HERE = os.path.dirname(os.path.abspath(__file__))
LOG, REVIEW = os.path.join(HERE, "spa_unblock.jsonl"), os.path.join(HERE, "spa_unblock_review.jsonl")
TEXT = re.compile(r"SubRip|SubStation", re.I)
FORCED_NAME = re.compile(r"forzad|forced", re.I)


def decide(tracks):
    """{id: {"name", "forced", "words", "verdict"}} -> ({id: action}, None) or (None, why)."""
    acts = {}
    for i, t in tracks.items():
        if FORCED_NAME.search(t["name"] or "") and t["words"] < 300 and not t["forced"]:
            acts[i] = "forced"
        elif t["verdict"] == "castellano":
            acts[i] = "es-ES"
        elif t["verdict"] == "latino":
            acts[i] = "es-419"
    full = [i for i, t in tracks.items() if acts.get(i) not in ("forced", "es-ES") and not t["forced"]]
    if len(full) > 1:
        return None, f"{len(full)} full Spanish tracks with no castellano evidence: " + ", ".join(
            f"{i} {tracks[i]['words']}w {tracks[i]['verdict']}" for i in full)
    return acts, None


def blocked(conn):
    src = sqlite3.connect(os.path.join(HERE, "media.db"))
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    mem.row_factory = sqlite3.Row
    log_text = open(os.path.join(HERE, "server.log"), encoding="utf-8", errors="replace").read()
    q = [("episode", "SELECT e.id, s.folder || '/' || e.folder f, e.file FROM episodes e JOIN shows s ON s.id=e.show_id "
                     "WHERE e.file IS NOT NULL", la.SHOWS),
         ("movie", "SELECT id, folder f, file FROM movies WHERE file IS NOT NULL", la.MOVIES)]
    out = []
    for kind, sql, root in q:
        for r in mem.execute(sql).fetchall():
            path = os.path.join(root, r["f"], r["file"])
            if not os.path.exists(path):
                continue
            side = f6.filtered_sidecars(os.path.dirname(path), os.path.splitext(r["file"])[0], log_text)
            if f6.plan(mem, r["id"], side, kind=kind)[0] == "blocked":
                out.append((kind, r["id"], path))
            mem.rollback()                                # plan() inserts sidecar rows; keep the copy clean
    return out


def survey(path):
    info = la.probe(path)
    spa = [t for t in info["tracks"] if t["type"] == "subtitles" and TEXT.search(t["codec"])
           and t["properties"].get("language") == "spa" and t["properties"].get("language_ietf") in (None, "", "es")]
    d = tempfile.mkdtemp(dir="/var/tmp", prefix="spa_unblock_")
    try:
        ext = {t["id"]: f"{d}/{t['id']}.{'ass' if 'SubStation' in t['codec'] else 'srt'}" for t in spa}
        subprocess.run(["ionice", "-c3", "mkvextract", path, "tracks"] + [f"{i}:{p}" for i, p in ext.items()],
                       capture_output=True, check=True)
        res = {}
        for t in spa:
            v = vp.verdict(vp.plain_text(ext[t["id"]]))
            res[str(t["id"])] = {"name": t["properties"].get("track_name"), "forced": bool(t["properties"].get("forced_track")),
                                 "words": v["words"], "verdict": v["verdict"], "uid": t["properties"]["uid"]}
        return res
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(run):
    done = {json.loads(l)["path"] for l in open(LOG) if '"ok"' in l} if os.path.exists(LOG) else set()
    todo = [x for x in blocked(None) if x[2] not in done]
    print(len(todo), "blocked files", flush=True)
    for kind, oid, path in todo:
        if not os.path.ismount("/srv/storage"):
            break
        try:
            tracks = survey(path)
            acts, why = decide(tracks)
        except Exception as e:
            acts, why, tracks = None, f"ERROR {e!r}"[:300], {}
        print(os.path.basename(path), acts or why, flush=True)
        if not run:
            continue
        if not acts:
            status = "ERROR" if why.startswith("ERROR") else "review"
            open(REVIEW, "a").write(json.dumps({"path": path, "why": why}, ensure_ascii=False) + "\n")
            open(LOG, "a").write(json.dumps({"path": path, "status": status, "why": why}, ensure_ascii=False) + "\n")
            continue
        edits = []
        for i, a in acts.items():
            sel = ["--edit", f"track:={tracks[i]['uid']}"]
            if a == "forced":
                edits += sel + ["--set", "flag-forced=1", "--set", "flag-default=0"]
            elif a == "es-ES":
                edits += sel + ["--set", "language-ietf=es-ES", "--set", "name=Español (España)"]
            else:
                nm = commands._canonical_name({"type": "subtitle", "out_lang": "spa", "lang": "spa-mx", "sdh_flag": 0,
                                               "out_forced": 0})
                edits += sel + ["--set", "language-ietf=es-419", "--set", f"name={nm}"]
        r = subprocess.run(["ionice", "-c3", "mkvpropedit", path] + edits, capture_output=True, text=True) if edits else None
        ok = r is None or r.returncode == 0
        if ok and edits:
            import app
            c = app.get_db()
            try:
                app._reinspect_in_place(c, kind, oid, path)
                c.commit()
            finally:
                c.close()
        open(LOG, "a").write(json.dumps({"path": path, "status": "ok" if ok else "FAILED", "acts": acts,
                                         "error": None if ok else r.stdout[-200:]}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main("--run" in sys.argv)
