"""F6 (series first), PLAN ONLY: what the mass remux would do to every episode. Reads media.db, never the library.

    f6_plan.py            # summary per show, details in f6_plan.json

It runs the app's own rule (scan.suggest_tracks, multi_audio like the import does) on a COPY of the database
and compares the result with the tracks as they are now. Nothing is written to media.db or to any media file.
The live pass will use the same decision and commands.build_mkvmerge_remux; this tells what it would touch first.

Not measured here (needs the files): tag/name differences, and whether a Spanish text subtitle exists as a sidecar.
An episode is `work` when a track would be dropped, the order of a kind changes, or a default flag flips.
Gaps are listed apart: no kept Spanish text subtitle (PGS-only or none) or no kept English one is a Bazarr/OCR
matter that no remux fixes.
"""
import collections, json, os, sqlite3, sys

import scan

HERE = os.path.dirname(os.path.abspath(__file__))
COPY = "/var/tmp/f6_plan.db"
OUT = os.path.join(HERE, "f6_plan.json")
MBPS = 36e6          # measured ceiling of the USB 2.0 bridge; a rewrite reads and writes the file once each


def drop_kind(t):
    if t["type"] == "audio":
        if t.get("commentary_flag"):
            return "audio commentary"
        return "audio castellano" if t["lang"] == "spa-es" else f"audio {t['lang']}"
    cls = scan._sub_class(t)
    return f"sub {cls} {t['lang']}"


def plan_episode(conn, eid):
    before = [dict(r) for r in conn.execute("SELECT * FROM tracks WHERE episode_id=?", (eid,))]
    scan.suggest_tracks(conn, eid, table="episodes", multi_audio=True)
    after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tracks WHERE episode_id=?", (eid,))}
    drops = [drop_kind(t) for t in after.values() if not t["keep"]]
    reorder = flags = False
    for kind in ("audio", "subtitle"):
        kept = [t for t in after.values() if t["type"] == kind and t["keep"] and t["mkv_id"] is not None]
        reorder |= [t["id"] for t in sorted(kept, key=lambda t: t["out_order"])] != [t["id"] for t in sorted(kept, key=lambda t: t["mkv_id"])]
        flags |= any(bool(t["out_default"]) != bool(t["default_flag"]) for t in kept)
    kept_subs = [t for t in after.values() if t["type"] == "subtitle" and t["keep"]]
    spa_text = any(t["lang"].startswith("spa") and not t["forced_flag"] and scan._sub_class(t) == "text" for t in kept_subs)
    eng_text = any(t["lang"] == "eng" and not t["forced_flag"] and scan._sub_class(t) == "text" for t in kept_subs)
    return {"drops": drops, "reorder": reorder, "flags": flags, "spa_text": spa_text, "eng_text": eng_text,
            "tracks": len(before)}


def main():
    src = sqlite3.connect(os.path.join(HERE, "media.db"))
    if os.path.exists(COPY):
        os.remove(COPY)
    dst = sqlite3.connect(COPY)
    src.backup(dst)
    src.close()
    dst.row_factory = sqlite3.Row
    eps = dst.execute("SELECT e.id, e.season, e.episode, e.size_bytes, e.file, s.folder sf, s.original_language ol "
                      "FROM episodes e JOIN shows s ON s.id=e.show_id WHERE e.file IS NOT NULL ORDER BY s.folder, e.season, e.episode").fetchall()
    shows, detail = collections.OrderedDict(), []
    for e in eps:
        try:
            p = plan_episode(dst, e["id"])
        except Exception as ex:                                   # one odd row must not hide the other 1200
            p = {"error": repr(ex), "drops": [], "reorder": False, "flags": False, "spa_text": False, "eng_text": False}
        work = bool(p["drops"] or p["reorder"] or p["flags"])
        s = shows.setdefault(e["sf"], collections.Counter())
        s["episodes"] += 1
        s["work"] += work
        s["bytes_work"] += (e["size_bytes"] or 0) if work else 0
        s["no_spa_text"] += not p["spa_text"]
        s["no_eng_text"] += not p["eng_text"]
        s["no_orig_lang"] += not e["ol"]
        s["errors"] += "error" in p
        for k in p["drops"]:
            s["drop:" + k] += 1
        detail.append({"id": e["id"], "show": e["sf"], "s": e["season"], "e": e["episode"], "work": work, **p})
    json.dump(detail, open(OUT, "w"), ensure_ascii=False)
    tot = collections.Counter()
    print(f"{'show':46} {'eps':>4} {'work':>5} {'GB':>6}  no-spa-text no-eng-text")
    for name, c in shows.items():
        tot.update(c)
        print(f"{name[:46]:46} {c['episodes']:>4} {c['work']:>5} {c['bytes_work'] / 1e9:>6.0f}  {c['no_spa_text']:>11} {c['no_eng_text']:>11}"
              + (f"  ERRORS {c['errors']}" if c["errors"] else "") + (f"  no-orig-lang {c['no_orig_lang']}" if c["no_orig_lang"] else ""))
    print(f"\nTOTAL {tot['episodes']} episodes, {tot['work']} need work, {tot['bytes_work'] / 1e12:.2f} TB to rewrite "
          f"(~{2 * tot['bytes_work'] / MBPS / 3600:.0f} h at {MBPS / 1e6:.0f} MB/s, an estimate)")
    drops = {k[5:]: v for k, v in tot.items() if k.startswith("drop:")}
    print("tracks dropped:", dict(sorted(drops.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
