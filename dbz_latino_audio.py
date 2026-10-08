"""Tag Dragon Ball Z's Spanish audio es-419 / "Español (Latino)" (Antonio, 2026-10-08: "etiqueta DBZ como es-419").

    dbz_latino_audio.py [--run]

The castellano dub was removed (dbz_castellano.jsonl, D5), so the Spanish audio left in each episode is the Latino
one. A file with more than one Spanish audio track is not touched and is listed instead. mkvpropedit by UID; the DB row
is re-read. Log: dbz_latino_audio.jsonl.
"""
import glob, json, os, subprocess, sys

import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
SHOW = "/srv/storage/Shows/Dragon Ball Z (1989) [tvdbid-81472]"


def main(run):
    tally = {"tag": 0, "already": 0, "no spa": 0, "ambiguous": 0, "failed": 0}
    log = open(os.path.join(HERE, "dbz_latino_audio.jsonl"), "a") if run else None
    app = None
    for path in sorted(glob.glob(os.path.join(glob.escape(SHOW), "*", "*.mkv"))):
        spa = [t for t in la.probe(path)["tracks"] if t["type"] == "audio" and t["properties"].get("language") == "spa"]
        if not spa:
            tally["no spa"] += 1
            continue
        if len(spa) > 1:
            tally["ambiguous"] += 1
            print("ambiguous:", os.path.basename(path), flush=True)
            continue
        p = spa[0]["properties"]
        if p.get("language_ietf") == "es-419" and "latino" in (p.get("track_name") or "").lower():
            tally["already"] += 1
            continue
        tally["tag"] += 1
        if not run:
            continue
        r = subprocess.run(["ionice", "-c3", "mkvpropedit", path, "--edit", f"track:={p['uid']}",
                            "--set", "language-ietf=es-419", "--set", "name=Español (Latino)"], capture_output=True, text=True)
        ok = r.returncode == 0
        if ok:
            if app is None:
                import app
            ep = os.path.basename(path)
            c = app.get_db()
            try:
                row = c.execute("SELECT id FROM episodes WHERE file=?", (ep,)).fetchone()
                if row:
                    app._reinspect_in_place(c, "episode", row["id"], path)
                    c.commit()
            finally:
                c.close()
        else:
            tally["failed"] += 1
        log.write(json.dumps({"path": path, "old": [p.get("language_ietf"), p.get("track_name")], "ok": ok,
                              "err": None if ok else r.stdout[-300:]}, ensure_ascii=False) + "\n")
        log.flush()
    print(tally, flush=True)


if __name__ == "__main__":
    main("--run" in sys.argv)
