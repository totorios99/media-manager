"""Metadata-only repair with mkvpropedit: never adds, drops or moves a track.

    meta_fix.py                       # dry run: counts and a few examples per fix
    meta_fix.py --run [--limit N]     # edit in place, log old values, re-read to verify

Fixes (what the audit flags and the app's propedit job cannot do, because that job
refuses any title whose plan drops a track):
  video   language 'und' -> the title's original language
  ietf    Spanish track whose NAME says Latino/Castellano but whose BCP 47 tag is a generic 'es':
          write es-419 / es-ES (no content analysis: only evidence already in the file)
  title   empty container title -> the app's title_display ("Title (Year)" / "Show - S01E01")
  audio   exactly one default, chosen by audio_default.py (original language or Latino dub
          for animation; TrueHD/Atmos only when it is the sole track of that language;
          otherwise the most compatible codec).
Old values go to meta_fix.jsonl first, so every edit can be reverted by hand.
"""
import concurrent.futures as cf, json, os, re, sqlite3, subprocess, sys

import audio_default
import library_audit as la
import scan

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "meta_fix.jsonl")
CACHE = "/var/tmp/mm_probe_cache.json"          # keyed by path+mtime+size, safe to delete
SKIP_DEFAULT = {"Dragon Ball Z (1989) [tvdbid-81472]"}   # until its castellano track is removed: both Spanish tracks look alike
SKIP_ALL = {"Dragon Ball Z (1989) [tvdbid-81472]"}       # dbz_castellano.py is rewriting those files right now


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
        cur = [t for t in audio if t["properties"].get("default_track")]
        target = audio_default.pick(audio, orig, animation, cur[0] if len(cur) == 1 else None)
        if target is not None and cur != [target]:
            for i, t in enumerate(audio, 1):
                want = 1 if t is target else 0
                if bool(t["properties"].get("default_track")) != bool(want):
                    argv += ["--edit", f"track:a{i}", "--set", f"flag-default={want}"]
                    before[f"a{i}.default"] = bool(t["properties"].get("default_track"))
            fixes.append("audio-default")
    # Spanish variant already evidenced by the track name but stored as a generic 'es': write the BCP 47 tag
    for ttype, sel in (("audio", "a"), ("subtitles", "s")):
        for i, t in enumerate([x for x in info["tracks"] if x["type"] == ttype], 1):
            v = audio_default.variant(t) if ttype == "audio" else la.variant(t, "subtitle")
            tag = {"spa-mx": "es-419", "spa-es": "es-ES"}.get(v)
            cur = (t["properties"].get("language_ietf") or "").lower()
            if tag and cur in ("", "es", "spa"):
                argv += ["--edit", f"track:{sel}{i}", "--set", f"language-ietf={tag}"]
                before[f"{sel}{i}.ietf"] = cur
                fixes.append("ietf")
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

    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    def one(row):
        kind, id_, path, orig, anim, td, show = row
        if not os.path.exists(path) or show in SKIP_ALL:
            return None
        st = os.stat(path)
        key = f"{path}|{st.st_mtime_ns}|{st.st_size}"
        info = cache.get(key) or la.probe(path)
        if not info or "tracks" not in info:
            return None
        cache[key] = info
        argv, before, fixes = plan(info, orig, bool(anim), td, show in SKIP_DEFAULT)
        return (kind, id_, path, argv, before, fixes) if fixes else None

    with cf.ThreadPoolExecutor(4) as pool:           # mkvmerge -J is I/O-bound: sequential took >30 min
        results = [r for r in pool.map(one, list(rows(conn))) if r]
    json.dump(cache, open(CACHE, "w"))
    for r in results:
        kind, id_, path, argv, before, fixes = r
        todo.append(r)
        for f in set(fixes):
            counts[f] = counts.get(f, 0) + 1
            ex.setdefault(f, []).append(os.path.basename(path)[:60])
    print(len(todo), "files to edit;", counts)
    by = {}
    for kind, id_, path, argv, before, fixes in todo:
        k = (os.path.relpath(path, la.SHOWS).split("/")[0] if kind == "episode" else "(películas)")
        for f in set(fixes):
            by.setdefault(k, {}).setdefault(f, 0)
            by[k][f] += 1
    for k, v in sorted(by.items()):
        print("   ", k[:34].ljust(34), v)
    for f, names in ex.items():
        print(" ", f, names[:3])
    if not run:
        return
    log = open(LOG, "a")
    done = bad = 0
    busy = {(("movie_id" if k == "movie" else "episode_id"), i) for k, i in (
        ("movie", r[0]) for r in conn.execute("SELECT movie_id FROM jobs WHERE status IN ('running','queued') AND movie_id IS NOT NULL")
    )} | {("episode_id", r[0]) for r in conn.execute(
        "SELECT episode_id FROM jobs WHERE status IN ('running','queued') AND episode_id IS NOT NULL")}
    skipped = 0
    for kind, id_, path, argv, before, fixes in todo[: limit or None]:
        if (("movie_id" if kind == "movie" else "episode_id"), id_) in busy:
            skipped += 1                    # a remux/propedit owns this file right now: never edit under it
            print("SKIP (job active)", os.path.basename(path))
            continue
        log.write(json.dumps({"path": path, "before": before, "fixes": fixes}, ensure_ascii=False) + "\n")
        log.flush()
        r = subprocess.run(["mkvpropedit", path, *argv], capture_output=True, text=True)
        info = la.probe(path)
        ok = r.returncode in (0, 1) and info is not None and verify(path, argv)
        done += ok
        bad += not ok
        if not ok:
            print("FAILED", os.path.basename(path), r.stdout[-150:], r.stderr[-150:])
    print(f"{done} edited, {bad} failed, {skipped} skipped (job active). Rerun library_audit.py to confirm; the app's DB rows refresh on its next rescan.")


if __name__ == "__main__":
    a = sys.argv[1:]
    main("--run" in a, int(a[a.index("--limit") + 1]) if "--limit" in a else 0)
