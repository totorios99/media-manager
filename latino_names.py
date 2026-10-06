"""Name every embedded Latino subtitle "Español (Latino)…" (2026-10-06).

    latino_names.py [--run]

Bazarr counts an embedded spa track as Latino only when its name says so (custom_lang: "latin", "mx", "latam"…).
subs_variant_pass --apply tagged 761 files es-419 but left the names, and F6 named its embeds from the bare out_lang,
so Bazarr re-downloaded all ten Mr. Robot S01 sidecars. mkvpropedit by UID, name only; the DB row is re-read.
"""
import json, os, sqlite3, subprocess, sys

import commands
import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))


def targets(conn):
    q = ("SELECT t.movie_id, t.episode_id, t.type, t.lang, t.name, t.sdh_flag, t.forced_flag, "
         "m.folder mf, m.file mfile, e.folder ef, e.file efile, s.folder sf FROM tracks t "
         "LEFT JOIN movies m ON m.id=t.movie_id LEFT JOIN episodes e ON e.id=t.episode_id "
         "LEFT JOIN shows s ON s.id=e.show_id "
         "WHERE t.ext_path IS NULL AND t.type='subtitle' AND t.lang='spa-mx' "
         "AND lower(coalesce(t.name,'')) NOT LIKE '%latin%'")
    out = {}
    for r in conn.execute(q):
        if r["movie_id"]:
            key = ("movie", r["movie_id"], os.path.join(la.MOVIES, r["mf"], r["mfile"]))
        else:
            key = ("episode", r["episode_id"], os.path.join(la.SHOWS, r["sf"], r["ef"], r["efile"]))
        out.setdefault(key, []).append(r)
    return out


def name_for(p):
    return commands._canonical_name({"type": "subtitle", "out_lang": "spa", "lang": "spa-mx",
                                     "sdh_flag": p.get("flag_hearing_impaired"), "out_forced": p.get("forced_track")})


def main(run):
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    todo = targets(conn)
    conn.close()
    log = open(os.path.join(HERE, "latino_names.jsonl"), "a")
    done = failed = 0
    for (kind, oid, path), _ in sorted(todo.items(), key=lambda kv: kv[0][2]):
        if not os.path.exists(path):
            print("missing", path, flush=True)
            continue
        tracks = la.probe(path)["tracks"]
        edits, changes = [], {}
        for t in tracks:
            p = t["properties"]
            if t["type"] == "subtitles" and p.get("language_ietf") == "es-419" and "latin" not in (p.get("track_name") or "").lower():
                new = name_for(p)
                edits += ["--edit", f"track:={p['uid']}", "--set", f"name={new}"]
                changes[t["id"]] = [p.get("track_name"), new]
        if not run or not edits:
            print(os.path.basename(path), changes or "nothing (no es-419 tag in the file)", flush=True)
            continue
        r = subprocess.run(["ionice", "-c3", "mkvpropedit", path] + edits, capture_output=True, text=True)
        ok = r.returncode == 0
        if ok:
            import app
            c = app.get_db()
            try:
                app._reinspect_in_place(c, kind, oid, path)
                c.commit()
            finally:
                c.close()
        done, failed = done + ok, failed + (not ok)
        log.write(json.dumps({"path": path, "changes": changes, "ok": ok, "err": None if ok else r.stdout[-300:]},
                             ensure_ascii=False) + "\n")
        log.flush()
    print(f"{len(todo)} files; renamed {done}, failed {failed}", flush=True)


if __name__ == "__main__":
    main("--run" in sys.argv)
