"""Phase 0 of SUBTITLES-PLAN.md: what subtitles every movie actually carries.

Reads Matroska headers only (mkvmerge -J) plus the folder listing, so it is safe to
run any time. Writes subs_inventory.json next to this file and prints a summary.
Replaces the counts taken from media.db, which nothing had verified.
"""
import collections, json, os, sqlite3, subprocess, sys

ROOT = os.environ.get("MEDIA_ROOT", "/srv/storage/Movies")
TEXT = {"SubRip/SRT", "SubStationAlpha", "WebVTT", "Timed Text"}


def classify(tracks):
    spa = [t for t in tracks if t["lang"] == "spa"]
    full = [t for t in spa if not t["forced"]]
    forced = [t for t in spa if t["forced"]]
    eng = [t for t in tracks if t["lang"] == "eng" and not t["forced"]]
    kind = lambda ts: "none" if not ts else ("text" if any(t["codec"] in TEXT for t in ts) else "image")
    return {"spa_full": kind(full), "spa_forced": kind(forced), "eng_full": kind(eng)}


def main():
    conn = sqlite3.connect(os.path.expanduser("~/media-manager/media.db"))
    rows = conn.execute("select folder, file, original_language, animation from movies "
                        "where coalesce(file,'')!='' order by folder").fetchall()
    out = []
    for folder, file, orig, anim in rows:
        path = os.path.join(ROOT, folder, file)
        r = subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True)
        try:
            j = json.loads(r.stdout)
        except ValueError:
            out.append({"folder": folder, "error": r.stderr.strip()[:200] or "unreadable"})
            continue
        subs = []
        for t in j.get("tracks", []):
            if t["type"] != "subtitles":
                continue
            p = t["properties"]
            subs.append({"id": t["id"], "lang": p.get("language"), "ietf": p.get("language_ietf"),
                         "codec": t["codec"], "name": p.get("track_name"),
                         "forced": bool(p.get("forced_track")), "default": bool(p.get("default_track")),
                         "hi": bool(p.get("flag_hearing_impaired"))})
        side = sorted(f for f in os.listdir(os.path.join(ROOT, folder))
                      if f.lower().endswith((".srt", ".ass", ".sup")))
        rec = {"folder": folder, "original_language": orig, "animation": bool(anim),
               "subs": subs, "sidecars": side, **classify(subs)}
        out.append(rec)
        print(len(out), folder, rec["spa_full"], rec["eng_full"], rec["spa_forced"], flush=True)

    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subs_inventory.json")
    json.dump(out, open(dst, "w"), indent=1, ensure_ascii=False)

    ok = [o for o in out if "error" not in o]
    print("\n== resumen (%d películas, %d ilegibles)" % (len(ok), len(out) - len(ok)))
    for key in ("spa_full", "eng_full", "spa_forced"):
        print(" ", key, dict(collections.Counter(o[key] for o in ok)))
    print("  con .srt/.ass/.sup al lado:", sum(1 for o in ok if o["sidecars"]))
    print("  ya cumplen el estándar en forma (español y English completos en texto):",
          sum(1 for o in ok if o["spa_full"] == "text" and o["eng_full"] == "text"))


if __name__ == "__main__":
    sys.exit(main())
