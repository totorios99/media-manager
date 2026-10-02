"""Metadata-only repair with mkvpropedit: never adds, drops or moves a track.

    meta_fix.py                       # dry run: counts and a few examples per fix
    meta_fix.py --run [--limit N]     # edit in place, log old values, re-read to verify

Fixes (what the audit flags and the app's propedit job cannot do, because that job
refuses any title whose plan drops a track):
  video   language 'und' -> the title's original language
  title   empty container title -> the app's title_display ("Title (Year)" / "Show - S01E01")
  audio   exactly one default: the original language, or the Latino dub for animation;
          the lighter track when the same language also has TrueHD/Atmos.
          Titles in SKIP_DEFAULT are left alone until Antonio decides (PLAN-CONVENCION.md §8).
Old values go to meta_fix.jsonl first, so every edit can be reverted by hand.
"""
import concurrent.futures as cf, json, os, re, sqlite3, subprocess, sys

import library_audit as la
import scan

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "meta_fix.jsonl")
SKIP_DEFAULT = {"The Office (US)"}          # 193 eps default to Latino; pending decision


def heavy(t):
    return bool(re.search(r"truehd|atmos", f"{t['codec']} {t['properties'].get('track_name', '')}", re.I))


def pick_default(audio, orig, animation):
    v = lambda t: la.variant(t, "audio")
    cands = [t for t in audio if v(t) in ("spa-mx", "spa")] if animation else []
    if not cands:
        want = scan.LANG_ISO1_TO_3.get(orig or "", "eng")
        cands = [t for t in audio if v(t) == want or (orig == "es" and v(t) in ("spa", "spa-mx", "spa-es"))]
    light = [t for t in cands if not heavy(t)]
    return (light or cands or [None])[0]


def plan(info, orig, animation, title_display, skip_default):
    """-> (argv_tail, before, fixes) for one file; empty argv_tail = nothing to do."""
    argv, before, fixes = [], {}, []
    props = (info.get("container") or {}).get("properties", {})
    if not props.get("title") and title_display:
        argv += ["--edit", "info", "--set", f"title={title_display}"]
        before["title"] = props.get("title"); fixes.append("title")
    iso = scan.LANG_ISO1_TO_3.get(orig or "")
    vids = [t for t in info["tracks"] if t["type"] == "video"]
    for j, t in enumerate(vids, 1):
        if iso and (t["properties"].get("language") or "und") == "und":
            argv += ["--edit", f"track:v{j}", "--set", f"language={iso}"]
            before[f"v{j}.language"] = t["properties"].get("language"); fixes.append("video-lang")
    audio = [t for t in info["tracks"] if t["type"] == "audio"]
    if audio and not skip_default:
        target = pick_default(audio, orig, animation)
        cur = [t for t in audio if t["properties"].get("default_track")]
        if target is not None and cur != [target]:
            for i, t in enumerate(audio, 1):
                want = 1 if t is target else 0
                if bool(t["properties"].get("default_track")) != bool(want):
                    argv += ["--edit", f"track:a{i}", "--set", f"flag-default={want}"]
                    before[f"a{i}.default"] = bool(t["properties"].get("default_track"))
            fixes.append("audio-default")
    return argv, before, fixes


def rows(conn):
    for r in conn.execute("SELECT id, folder, file, title, year, clean_title, original_language ol, animation FROM movies "
                          "WHERE file IS NOT NULL"):
        td = f"{r['title']} ({r['year']})" if r["title"] else r["clean_title"]
        yield "movie", r["id"], os.path.join(la.MOVIES, r["folder"], r["file"]), r["ol"], r["animation"], td, r["folder"]
    for r in conn.execute("SELECT e.id, s.folder sf, e.folder ef, e.file, e.season, e.episode, s.title st, s.clean_title sc, "
                          "s.original_language ol, s.animation FROM episodes e JOIN shows s ON s.id=e.show_id "
                          "WHERE e.file IS NOT NULL AND e.excluded=0"):
        td = f"{r['st'] or r['sc']} - S{r['season']:02d}E{r['episode']:02d}"
        yield "episode", r["id"], os.path.join(la.SHOWS, r["sf"], r["ef"], r["file"]), r["ol"], r["animation"], td, r["sf"]


def verify(path, argv):
    info = la.probe(path)
    props = (info.get("container") or {}).get("properties", {})
    for i, a in enumerate(argv):
        if a == "--set" and argv[i + 1].startswith("title="):
            if props.get("title") != argv[i + 1][6:]:
                return False
    return info is not None


def main(run, limit):
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    todo, counts, ex = [], {}, {}

    def one(row):
        kind, id_, path, orig, anim, td, show = row
        info = la.probe(path) if os.path.exists(path) else None
        if not info:
            return None
        argv, before, fixes = plan(info, orig, bool(anim), td, show in SKIP_DEFAULT)
        return (kind, id_, path, argv, before, fixes) if fixes else None

    with cf.ThreadPoolExecutor(4) as pool:           # mkvmerge -J is I/O-bound: sequential took >30 min
        results = [r for r in pool.map(one, list(rows(conn))) if r]
    for r in results:
        kind, id_, path, argv, before, fixes = r
        todo.append(r)
        for f in set(fixes):
            counts[f] = counts.get(f, 0) + 1
            ex.setdefault(f, []).append(os.path.basename(path)[:60])
    print(len(todo), "files to edit;", counts)
    for f, names in ex.items():
        print(" ", f, names[:3])
    if not run:
        return
    log = open(LOG, "a")
    done = bad = 0
    for kind, id_, path, argv, before, fixes in todo[: limit or None]:
        log.write(json.dumps({"path": path, "before": before, "fixes": fixes}, ensure_ascii=False) + "\n")
        log.flush()
        r = subprocess.run(["mkvpropedit", path, *argv], capture_output=True, text=True)
        info = la.probe(path)
        ok = r.returncode in (0, 1) and info is not None and verify(path, argv)
        done += ok
        bad += not ok
        if not ok:
            print("FAILED", os.path.basename(path), r.stdout[-150:], r.stderr[-150:])
    print(f"{done} edited, {bad} failed. Rerun library_audit.py to confirm; the app's DB rows refresh on its next rescan.")


if __name__ == "__main__":
    a = sys.argv[1:]
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0)
