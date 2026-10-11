"""The 46 F6-blocked episodes (38 Dragon Ball Z, 8 Rick and Morty): two or more bare-`spa` text subtitles.

    dbz_rm_subs.py SURVEY.jsonl [--run]

Antonio, 2026-10-11: keep the Latino one by markers; a tie stays blocked for him.
DBZ S01E01 read by hand: one full track has the opening lyrics and castellano markers ("Mutenroshi", "Menuda
porquería", "ha volado"); the other has no lyrics and follows the Latino dub ("maestro Roshi"). Some episodes add a
~160-word lyrics-only track. So, per track (survey: words, CHA-LA present, castellano/latino marker counts):
  songs       < 300 words                                  -> es-ES (goes with the castellano release)
  castellano  full, >= 2 castellano markers                -> es-ES (F6 then drops it)
  latino      full, no lyrics, 0 castellano markers        -> es-419 "Español (Latino)"
anything else, or not exactly one Latino when castellano is absent -> blocked.
Rick and Morty: the second track is "FORZADO" (36-258 words) without the forced flag -> flag-forced=1.
mkvpropedit by UID; the DB row is re-read; F6 does the remux afterwards.
"""
import json, os, sqlite3, subprocess, sys, time

import commands
import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "dbz_rm_subs.jsonl")


def classify(tracks):
    """{track_id: survey row} -> ({track_id: action}, None) or (None, why it is a tie)."""
    acts = {}
    has_forzado = any("forzad" in (o.get("name") or "").lower() for o in tracks.values())
    for i, t in tracks.items():
        name = (t.get("name") or "").lower()
        if "forzad" in name and not t.get("forced"):
            acts[i] = "forced"
        elif has_forzado and t["words"] >= 300:
            acts[i] = "keep"                    # R&M: a flag fix, not a variant call; the full track stays as it is
        elif t["words"] < 300:
            acts[i] = "es-ES"
        elif t["cast"] >= 2:
            acts[i] = "es-ES"
        elif not t["chala"] and (t["cast"] == 0 or t.get("lat", 0) >= max(3, 3 * t["cast"])):
            acts[i] = "es-419"                  # S02E16: 1 castellano marker against 7 Latino ones
        else:
            return None, f"track {i}: {t['words']} words, lyrics {t['chala']}, castellano markers {t['cast']}"
    if list(acts.values()).count("es-419") > 1:
        return None, "more than one Latino candidate"
    full_left = [i for i, a in acts.items() if a in ("es-419", "keep")]
    if len(full_left) > 1:
        return None, "more than one full Spanish track would stay"
    return acts, None


def tie_plan(tracks):
    """Antonio 2026-10-11 for the ties: "conserva los del doblaje siempre y cuando no sean castellano".
    The dub's subtitle is the full track WITHOUT the opening lyrics (the Latino dub sings its own). -> (dub, castellano
    ids, other ids to drop). dub is None when there is none, or it reads castellano (>= 2 markers)."""
    dubs = [i for i, t in tracks.items() if t["words"] >= 300 and not t["chala"]]
    dub = dubs[0] if len(dubs) == 1 and tracks[dubs[0]]["cast"] <= 1 else None
    cast = [i for i, t in tracks.items() if i != dub and (t["words"] < 300 or t["cast"] >= 2)]
    other = [i for i in tracks if i != dub and i not in cast]
    return dub, cast, other


def apply_tie(r, conn_path):
    """Tag, then a verified remux that keeps only the dub's Spanish subtitle. -> (status, detail)."""
    import f6_series as f6
    dub, cast, other = tie_plan(r["tracks"])
    tracks = {str(t["id"]): t for t in la.probe(r["path"])["tracks"]}
    edits = []
    for i in cast:
        edits += ["--edit", f"track:={tracks[i]['properties']['uid']}", "--set", "language=spa",
                  "--set", "language-ietf=es-ES", "--set", "name=Español (España)"]
    if dub:
        p = tracks[dub]["properties"]
        nm = commands._canonical_name({"type": "subtitle", "out_lang": "spa", "lang": "spa-mx",
                                       "sdh_flag": p.get("flag_hearing_impaired"), "out_forced": p.get("forced_track")})
        edits += ["--edit", f"track:={p['uid']}", "--set", "language=spa", "--set", "language-ietf=es-419", "--set", f"name={nm}"]
    res = subprocess.run(["ionice", "-c3", "mkvpropedit", r["path"]] + edits, capture_output=True, text=True)
    if res.returncode:
        return "FAILED", f"mkvpropedit {res.stdout[-200:]}"
    import app
    c = app.get_db()
    try:
        app._reinspect_in_place(c, "episode", r["id"], r["path"])
        c.commit()
    finally:
        c.close()
    src = sqlite3.connect(conn_path)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    mem.row_factory = sqlite3.Row
    _, rows, _ = f6.plan(mem, r["id"], [])
    drop = {int(i) for i in cast + other}
    for row in rows:
        if row["mkv_id"] in drop:
            row["keep"], row["out_default"] = 0, 0
    if dub and not any(row["mkv_id"] == int(dub) and row["keep"] for row in rows):
        return "FAILED", "the plan does not keep the dub's subtitle"
    err = f6.remux_verified(r["path"], rows)
    if err:
        return "FAILED", err
    c = app.get_db()
    try:
        c.execute("UPDATE episodes SET output_file=NULL, status='clean', updated_at=? WHERE id=?", (app._now(), r["id"]))
        app._reinspect_in_place(c, "episode", r["id"], r["path"])
        c.commit()
    finally:
        c.close()
    return "ok", f"dub {dub}, dropped {sorted(drop)}" + ("" if dub else " (no Latino sub left: Bazarr)")


def main(survey, run):
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    rows = [json.loads(l) for l in open(survey)]
    # track ids are those of the survey: once a file was tagged or remuxed, never touch it again from this survey
    done = {json.loads(l)["path"] for l in open(LOG) if '"ok"' in l} if os.path.exists(LOG) else set()
    rows = [r for r in rows if r["path"] not in done]
    log = open(LOG, "a") if run else None
    counts = {}
    for r in rows:
        acts, why = classify(r["tracks"])
        name = os.path.basename(r["path"])
        if not acts:
            dub, cast, other = tie_plan(r["tracks"])
            counts["tie"] = counts.get("tie", 0) + 1
            print("TIE", name, why, "-> keep dub", dub, "castellano", cast, "drop", other, flush=True)
            if run:
                t0 = time.time()
                status, detail = apply_tie(r, os.path.join(HERE, "media.db"))
                log.write(json.dumps({"id": r["id"], "path": r["path"], "status": status, "tie": True, "detail": detail,
                                      "secs": round(time.time() - t0)}, ensure_ascii=False) + "\n")
                log.flush()
                print(status, name, detail, flush=True)
            continue
        tracks = {str(t["id"]): t for t in la.probe(r["path"])["tracks"]}
        edits = []
        for i, a in acts.items():
            p = tracks[i]["properties"]
            sel = ["--edit", f"track:={p['uid']}"]
            if a == "forced":
                edits += sel + ["--set", "flag-forced=1", "--set", "flag-default=0"]
            elif a == "es-ES":
                edits += sel + ["--set", "language=spa", "--set", "language-ietf=es-ES", "--set", "name=Español (España)"]
            elif a == "es-419":
                nm = commands._canonical_name({"type": "subtitle", "out_lang": "spa", "lang": "spa-mx",
                                               "sdh_flag": p.get("flag_hearing_impaired"), "out_forced": p.get("forced_track")})
                edits += sel + ["--set", "language=spa", "--set", "language-ietf=es-419", "--set", f"name={nm}"]
        counts["ok"] = counts.get("ok", 0) + 1
        print(name, acts, flush=True)
        if not run or not edits:
            continue
        res = subprocess.run(["ionice", "-c3", "mkvpropedit", r["path"]] + edits, capture_output=True, text=True)
        ok = res.returncode == 0
        if ok:
            import app
            c = app.get_db()
            try:
                app._reinspect_in_place(c, "episode", r["id"], r["path"])
                c.commit()
            finally:
                c.close()
        log.write(json.dumps({"id": r["id"], "path": r["path"], "status": "ok" if ok else "FAILED", "acts": acts,
                              "err": None if ok else res.stdout[-300:]}, ensure_ascii=False) + "\n")
        log.flush()
    print(counts, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], "--run" in sys.argv)
