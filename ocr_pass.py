"""P3: no PGS left in the library (Antonio, 2026-10-09: "Espero en mi regreso ya no encontrar PGS en mi librería").

    ocr_pass.py                      # plan only: what each file would become
    ocr_pass.py --run [--limit N] [--match REGEX] [--ignore-window]

Per file with PGS tracks (read live with mkvmerge -J, not from the DB):
  * a PGS whose language already has a text track with the same forced flag  -> dropped, no OCR needed;
  * a PGS in a language outside the convention (not spa/eng)                 -> dropped;
  * otherwise: one mkvextract for all PGS tracks, pgs_bright (dim palettes OCR to nothing), `uvx pgsrip`,
    punctuation-only lines removed, then the gate: OOV <= 8 % against the library vocabulary and enough cues
    (>= 50 for a full track, >= 1 for forced). Spanish gets a variant verdict from its text: castellano is dropped
    (D5) unless Spanish is the film's original language; latino -> es-419; unknown -> bare spa.
  * a track that fails the gate is dropped too (Antonio, 2026-10-11): its disc .sup is copied to .recycle, the
    track goes to ocr_review.jsonl, and Bazarr's wanted search fills the language with a hook-verified SRT.
The remux keeps every other stream bit for bit (f6_series.stream_md5, chapters compared), puts each SRT where its PGS
was, writes to a hidden .ocr-tmp, fsyncs and replaces. Night window MM_NIGHT between files, like F6.
"""
import glob, json, os, re, shutil, signal, sqlite3, subprocess, sys, time

import commands
import f6_series as f6
import library_audit as la
import ocr_proto as op
import overnight
import pgs_bright
import subs_clean as sc
import subs_variant_pass as vp

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = "/var/tmp/ocr"
OOV_MAX, MIN_CUES = 8.0, 50
LANG2 = {"spa": "es", "eng": "en"}
LOG, REVIEW = os.path.join(HERE, "ocr_pass.jsonl"), os.path.join(HERE, "ocr_review.jsonl")


def targets(conn):
    """(kind, id, path, original_language) for every file the DB says has a kept PGS track."""
    q = ("SELECT DISTINCT 'movie', m.id, m.folder, m.file, m.original_language FROM tracks t JOIN movies m ON m.id=t.movie_id "
         "WHERE t.type='subtitle' AND (t.codec LIKE '%PGS%' OR t.codec LIKE '%VobSub%') AND t.keep=1 "
         "UNION SELECT DISTINCT 'episode', e.id, s.folder || '/' || e.folder, e.file, s.original_language FROM tracks t "
         "JOIN episodes e ON e.id=t.episode_id JOIN shows s ON s.id=e.show_id "
         "WHERE t.type='subtitle' AND (t.codec LIKE '%PGS%' OR t.codec LIKE '%VobSub%') AND t.keep=1")
    out = []
    for kind, oid, folder, file, orig in conn.execute(q):
        root = la.MOVIES if kind == "movie" else la.SHOWS
        out.append((kind, oid, os.path.join(root, folder, file), orig or ""))
    return sorted(out, key=lambda x: os.path.getsize(x[2]) if os.path.exists(x[2]) else 0)


def clean_srt(path):
    cues = sc.parse(sc.decode(open(path, "rb").read()))
    cues = [(a, b, "\n".join(l for l in t.split("\n") if re.search(r"\w", l))) for a, b, t in cues]
    kept, _ = sc.strip_ads([c for c in cues if c[2]])
    open(path, "w", encoding="utf-8").write(sc.render(kept))
    return len(kept)


def plan_file(path, orig_lang, vocs, run):
    """-> (decisions, srts) where decisions = [(track, action, why, extra)]; OCR only happens with run=True."""
    info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout)
    tracks = info["tracks"]
    # a castellano text track does not stand in for a Spanish PGS: that PGS may be the Latino one
    texts = {(t["properties"].get("language"), bool(t["properties"].get("forced_track")))
             for t in tracks if t["type"] == "subtitles" and "PGS" not in t["codec"] and "VobSub" not in t["codec"]
             and t["properties"].get("language_ietf") != "es-ES"}
    pgs = [t for t in tracks if t["type"] == "subtitles" and "PGS" in t["codec"]]
    vobs = [t for t in tracks if t["type"] == "subtitles" and "VobSub" in t["codec"]]
    decisions, need_ocr, need_save = [], [], []
    for t in vobs:      # no VobSub OCR here: same rule as a failed gate (Antonio 2026-10-11), original kept in .recycle
        lang, forced = t["properties"].get("language"), bool(t["properties"].get("forced_track"))
        if lang not in LANG2:
            decisions.append((t, "drop", f"{lang}: outside the convention", None))
        elif (lang, forced) in texts:
            decisions.append((t, "drop", f"{lang}{' forced' if forced else ''} already has a text track", None))
        elif t["properties"].get("language_ietf") == "es-ES" and orig_lang != "es":
            decisions.append((t, "drop", "castellano VobSub; D5", None))
        else:
            need_save.append(t)
    for t in pgs:
        lang, forced = t["properties"].get("language"), bool(t["properties"].get("forced_track"))
        if lang not in LANG2:
            decisions.append((t, "drop", f"{lang}: outside the convention", None))
        elif (lang, forced) in texts:
            decisions.append((t, "drop", f"{lang}{' forced' if forced else ''} already has a text track", None))
        else:
            need_ocr.append(t)
    if not run:
        decisions += [(t, "ocr", "planned", None) for t in need_ocr]
        decisions += [(t, "drop", "VobSub: no OCR; left to Bazarr", None) for t in need_save]
        return decisions
    if not need_ocr and not need_save:
        return decisions
    d = os.path.join(WORK, str(abs(hash(path))))
    os.makedirs(d, exist_ok=True)
    subprocess.run(["ionice", "-c3", "mkvextract", path, "tracks"] + [f"{t['id']}:{d}/{t['id']}.sup" for t in need_ocr]
                   + [f"{t['id']}:{d}/{t['id']}.sub" for t in need_save], capture_output=True, check=True)
    for t in need_save:
        decisions.append((t, "drop", "VobSub: no OCR; left to Bazarr",
                          {"sup": [p for p in (f"{d}/{t['id']}.sub", f"{d}/{t['id']}.idx") if os.path.exists(p)]}))
    for t in need_ocr:
        p, lang2 = t["properties"], LANG2[t["properties"]["language"]]
        forced = bool(p.get("forced_track"))
        sup = f"{d}/{t['id']}.{lang2}.sup"
        open(sup, "wb").write(pgs_bright.brighten(open(f"{d}/{t['id']}.sup", "rb").read()))
        subprocess.run(["uvx", "pgsrip", "rip", "-l", lang2, "-f", sup], capture_output=True, text=True)
        srt = sup[:-4] + ".srt"
        n = clean_srt(srt) if os.path.exists(srt) and os.path.getsize(srt) else 0
        rate = op.oov(srt, vocs[lang2])[0] * 100 if n else 100.0
        if n < (1 if forced else MIN_CUES) or rate > OOV_MAX:
            # Antonio 2026-10-11: no PGS left even then; the disc .sup goes to .recycle and Bazarr's wanted search
            # (language now missing) brings an SRT through the /api/subs/fix hook
            decisions.append((t, "drop", f"OCR failed the gate: {n} cues, OOV {rate:.1f} %; left to Bazarr",
                              {"sup": f"{d}/{t['id']}.sup"}))
            continue
        extra = {"srt": srt, "oov": round(rate, 1), "cues": n, "lang": p["language"], "ietf": None}
        if p["language"] == "spa":
            v = vp.verdict(vp.plain_text(srt))["verdict"]
            if v == "castellano" and orig_lang != "es":
                decisions.append((t, "drop", f"OCR says castellano (OOV {rate:.1f} %); D5", None))
                continue
            extra["ietf"] = {"latino": "es-419", "castellano": "es-ES"}.get(v)
            extra["variant"] = v
        decisions.append((t, "replace", f"OCR ok: {n} cues, OOV {rate:.1f} %", extra))
    return decisions


def remux(path, info, decisions):
    """Drop the PGS marked drop/replace, add the SRTs in their place, verify every kept stream, replace in place."""
    by_id = {t["id"]: (a, why, x) for t, a, why, x in decisions}
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".ocr-tmp")
    keep_ids = [t["id"] for t in info["tracks"] if by_id.get(t["id"], ("keep",))[0] == "keep"]
    subs_keep = [i for i in keep_ids if next(t for t in info["tracks"] if t["id"] == i)["type"] == "subtitles"]
    cmd = f6.NICE + ["mkvmerge", "-o", tmp, "--subtitle-tracks", ",".join(map(str, subs_keep)) if subs_keep else "",
                     path]
    if not subs_keep:
        cmd = f6.NICE + ["mkvmerge", "-o", tmp, "--no-subtitles", path]
    order, n_file = [f"0:{i}" for i in keep_ids], 1
    for t in info["tracks"]:
        a, why, x = by_id.get(t["id"], ("keep", "", None))
        if a != "replace":
            continue
        p = t["properties"]
        name = commands._canonical_name({"type": "subtitle", "out_lang": x["lang"],
                                         "lang": "spa-mx" if x.get("ietf") == "es-419" else x["lang"],
                                         "sdh_flag": p.get("flag_hearing_impaired"), "out_forced": p.get("forced_track")})
        cmd += ["--language", f"0:{x['ietf'] or x['lang']}", "--track-name", f"0:{name}",
                "--default-track-flag", f"0:{'yes' if p.get('default_track') else 'no'}",
                "--forced-display-flag", f"0:{'yes' if p.get('forced_track') else 'no'}",
                "--hearing-impaired-flag", f"0:{'yes' if p.get('flag_hearing_impaired') else 'no'}", x["srt"]]
        # the SRT takes the PGS's place: right after the track that preceded it among the kept ones
        prev = [i for i in keep_ids if i < t["id"]]
        pos = order.index(f"0:{prev[-1]}") + 1 if prev else 0
        order.insert(pos, f"{n_file}:0")
        n_file += 1
    cmd += ["--track-order", ",".join(order)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode not in (0, 1) or not os.path.exists(tmp):
        return f"mkvmerge rc={r.returncode} {r.stdout[-200:]}"
    try:
        new = json.loads(subprocess.run(["mkvmerge", "-J", tmp], capture_output=True, text=True).stdout)["tracks"]
        if len(new) != len(order):
            return f"tracks {len(new)} != planned {len(order)}"
        kept_pgs = sum(1 for t in info["tracks"] if ("PGS" in t["codec"] or "VobSub" in t["codec"])
                       and by_id.get(t["id"], ("keep",))[0] == "keep")
        images = sum("PGS" in t["codec"] or "VobSub" in t["codec"] for t in new)
        if images != kept_pgs:
            return f"image subtitle tracks in the output: {images}, expected {kept_pgs}"
        out_ids = [k for k, spec in enumerate(order) if spec.startswith("0:")]
        err = f6.durations_differ(path, info["tracks"], tmp, new, list(zip(keep_ids, out_ids)))
        if err:
            return err
        a, b = f6.stream_md5(path, keep_ids), f6.stream_md5(tmp, out_ids)
        if a != b:
            a, b = f6.stream_md5(path, keep_ids, True), f6.stream_md5(tmp, out_ids, True)
        if not a or a != b:
            return "stream hashes differ"
        if f6.chapters(path) != f6.chapters(tmp):
            return "chapters differ"
        fd = os.open(tmp, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, path)
        tmp = None
        return None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def main(run, limit, match, ignore_window):
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    done = {json.loads(l)["path"] for l in open(LOG) if '"ok"' in l} if os.path.exists(LOG) else set()
    todo = [x for x in targets(conn) if x[2] not in done and os.path.exists(x[2]) and (not match or re.search(match, x[2]))]
    conn.close()
    vocs = {"es": op.vocab("es"), "en": op.vocab("en")} if run else {}
    counts, n = {}, 0
    for kind, oid, path, orig in todo:
        if stop or (limit and n >= limit) or not os.path.ismount("/srv/storage"):
            break
        while run and not ignore_window and not stop and not overnight.in_window():
            time.sleep(300)
        if stop:
            break
        t0 = time.time()
        try:
            decisions = plan_file(path, orig, vocs, run)
        except Exception as e:
            print("ERROR", path, repr(e), flush=True)
            continue
        acts = [a for _, a, _, _ in decisions]
        for a in acts:
            counts[a] = counts.get(a, 0) + 1
        print(os.path.basename(path), "|", "; ".join(f"{t['id']} {t['properties'].get('language')} {a}: {w}"
                                                     for t, a, w, _ in decisions), flush=True)
        if not run:
            continue
        n += 1
        for t, a, why, _ in decisions:
            if a == "keep":
                open(REVIEW, "a").write(json.dumps({"path": path, "track": t["id"], "lang": t["properties"].get("language"),
                                                    "why": why}, ensure_ascii=False) + "\n")
        if not any(a in ("drop", "replace") for a in acts):
            continue
        info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout)
        err = remux(path, info, decisions)
        if not err:
            for t, a, why, x in decisions:
                if x and x.get("sup"):
                    srcs = x["sup"] if isinstance(x["sup"], list) else [x["sup"]]
                    base = os.path.join(f6.RECYCLE, os.path.relpath(path, os.path.dirname(f6.RECYCLE))
                                        + f".track{t['id']}.{t['properties'].get('language')}")
                    os.makedirs(os.path.dirname(base), exist_ok=True)
                    for src in srcs:
                        shutil.copyfile(src, dst := base + os.path.splitext(src)[1])
                    open(REVIEW, "a").write(json.dumps({"path": path, "track": t["id"], "lang": t["properties"].get("language"),
                                                        "why": why, "sup": dst}, ensure_ascii=False) + "\n")
            import app
            c = app.get_db()
            try:
                app._reinspect_in_place(c, kind, oid, path)
                c.commit()
            finally:
                c.close()
        rec = {"path": path, "status": "FAILED" if err else "ok", "error": err, "secs": round(time.time() - t0),
               "decisions": [(t["id"], t["properties"].get("language"), a, w) for t, a, w, _ in decisions]}
        open(LOG, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(rec["status"], os.path.basename(path), err or "", f"{rec['secs']}s", flush=True)
        shutil.rmtree(os.path.join(WORK, str(abs(hash(path)))), ignore_errors=True)
    print("counts", counts, flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0,
         a[a.index("--match") + 1] if "--match" in a else None, "--ignore-window" in a)
