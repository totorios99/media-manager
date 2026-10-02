"""Read-only audit of the whole library against our standards.

    set -a; . ./.env.systemd; set +a; uv run python library_audit.py

1. Missing: Radarr movies without a file, show episodes TMDB has aired and we lack.
2. Compliance, file by file (mkvmerge -J, nothing is written):
   audio    one default, original language (animation: Latino) and never TrueHD/Atmos;
            Latino dub present; no castellano unless the original is Spanish
   subs     text only (no PGS/VobSub), embedded, order Latino > English > Latino forced,
            Spanish tagged es-419, forced/SDH flags coherent
   metadata container title without release junk, video language set, track names clean
Standards: LIBRARY_STATE.md "Convenciones" and SUBTITLES-PLAN.md "El estándar".
Writes library_audit.json next to this script (full per-file reasons).
"""
import collections, concurrent.futures as cf, datetime, json, os, re, sqlite3, subprocess, sys
import urllib.request

import audio_default
import scan

HERE = os.path.dirname(os.path.abspath(__file__))
MOVIES = os.environ.get("MEDIA_ROOT", "/srv/storage/Movies")
SHOWS = os.environ.get("MM_SHOWS_ROOT", "/srv/storage/Shows")
JUNK = re.compile(r"www\.|\.com\b|\.org\b|\bx26[45]\b|bluray|web-?dl|hdtv|\brip\b|\[.*\]|\w\.\w+\.\w", re.I)
FORCED = re.compile(r"forced|forzad", re.I)


def probe(path):
    out = subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout
    return json.loads(out) if out else None


def variant(t, ttype):
    p = t["properties"]
    return scan._spanish_variant(p.get("language"), p.get("track_name"), ttype, p.get("language_ietf"))


def check(info, orig, animation, sidecars):
    """-> list of reasons the file does not comply (empty = complies)."""
    bad = []
    tracks = info["tracks"]
    audio = [t for t in tracks if t["type"] == "audio"]
    subs = [t for t in tracks if t["type"] == "subtitles"]
    video = [t for t in tracks if t["type"] == "video"]
    # ---- audio
    defaults = [t for t in audio if t["properties"].get("default_track")]
    if len(defaults) != 1:
        bad.append(f"audio: {len(defaults)} defaults")
    else:
        d = defaults[0]
        exp = audio_default.pick(audio, orig, bool(animation), d)
        if exp is not None and exp is not d:
            if audio_default.variant(exp) != audio_default.variant(d):
                bad.append(f"audio: default is {audio_default.variant(d)}, expected {audio_default.variant(exp)}")
            else:
                bad.append(f"audio: default is {d['codec']}, expected {exp['codec']} (TrueHD/Atmos only if alone; else most compatible)")
    variants = [variant(t, "audio") for t in audio]
    if orig != "es" and "spa-mx" not in variants and "spa" not in variants:
        bad.append("audio: no Latino dub")
    if orig != "es" and "spa-es" in variants:
        bad.append("audio: castellano present")
    # ---- subtitles
    rank = []
    for t in subs:
        p = t["properties"]
        cls = scan._sub_class({"codec": t["codec"]})
        v = variant(t, "subtitle")
        forced = bool(p.get("forced_track")) or bool(FORCED.search(p.get("track_name") or ""))
        if cls != "text":
            bad.append(f"subs: {cls.upper()} track ({v})")
        if v in ("spa-mx", "spa") and not forced:
            rank.append(1)
        elif v == "eng" and not forced:
            rank.append(2)
        elif v in ("spa-mx", "spa") and forced:
            rank.append(3)
        else:
            rank.append(9)
        if v == "spa":
            bad.append("subs: Spanish without a variant tag (es-419)")
        if v == "spa-es" and orig != "es":
            bad.append("subs: castellano present")
        if forced and not p.get("forced_track"):
            bad.append("subs: forced only in the name, flag missing")
    if 9 in rank:
        bad.append("subs: extra tracks outside Latino/English/Latino-forced")
    if rank != sorted(rank):
        bad.append("subs: wrong order")
    if 1 not in rank:
        bad.append("subs: no embedded Latino text" + (" (sidecar es-MX exists)" if sidecars["spa"] else ""))
    if 2 not in rank:
        bad.append("subs: no embedded English text")
    # ---- metadata
    title = (info.get("container") or {}).get("properties", {}).get("title") or ""
    if not title:
        bad.append("meta: no container title")
    elif JUNK.search(title):
        bad.append(f"meta: junk in title ({title[:40]})")
    if any((v["properties"].get("language") or "und") == "und" for v in video):
        bad.append("meta: video language und")
    if any(JUNK.search(t["properties"].get("track_name") or "") for t in tracks):
        bad.append("meta: junk in a track name")
    return bad


def sidecar_langs(path):
    d, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    found = {"spa": False, "eng": False}
    for f in os.listdir(d):
        if f.startswith(stem + ".") and f.lower().endswith(".srt"):
            g = scan.guess_srt_lang(f)
            found["spa" if g.startswith("spa") else "eng" if g == "eng" else "x"] = True
    return found


def load_rows(conn):
    rows = []
    for r in conn.execute("SELECT id, folder, file, original_language ol, animation FROM movies WHERE file IS NOT NULL"):
        rows.append(("movie", f"{r['folder']}", os.path.join(MOVIES, r["folder"], r["file"]), r["ol"], r["animation"]))
    for r in conn.execute("SELECT e.id, s.folder sf, e.folder ef, e.file, e.season, e.episode, s.original_language ol, "
                          "s.animation FROM episodes e JOIN shows s ON s.id=e.show_id WHERE e.file IS NOT NULL AND e.excluded=0"):
        rows.append(("episode", f"{r['sf']}|S{r['season']:02d}E{r['episode']:02d}",
                     os.path.join(SHOWS, r["sf"], r["ef"], r["file"]), r["ol"], r["animation"]))
    return rows


def audit_one(row):
    kind, label, path, orig, anim = row
    if not os.path.exists(path):
        return kind, label, ["file missing on disk"]
    try:
        info = probe(path)
        if not info:
            return kind, label, ["unreadable"]
        return kind, label, check(info, orig, bool(anim), sidecar_langs(path))
    except Exception as e:                        # one broken file must not stop the audit
        return kind, label, [f"error: {e}"]


def missing_movies():
    tok = open(os.path.join(HERE, ".radarr-token")).read().strip()
    req = urllib.request.Request(os.environ.get("RADARR_URL", "http://localhost:7878") + "/api/v3/movie",
                                 headers={"X-Api-Key": tok})
    ms = json.load(urllib.request.urlopen(req, timeout=60))
    return sorted(f"{m['title']} ({m['year']})" + ("" if m["monitored"] else " [no monitorada]")
                  for m in ms if not m.get("hasFile"))


def tmdb(path):
    key = os.environ["TMDB_API_KEY"]
    return json.load(urllib.request.urlopen(f"{scan.TMDB_BASE}/{path}?api_key={key}&language=es-MX", timeout=30))


def missing_episodes(conn):
    today = datetime.date.today().isoformat()
    res = []
    for s in conn.execute("SELECT id, folder, tmdb_id FROM shows"):
        have = {(r[0], r[1]) for r in conn.execute(
            "SELECT season, episode FROM episodes WHERE show_id=? AND file IS NOT NULL", (s["id"],))}
        if not s["tmdb_id"]:
            res.append((s["folder"], len(have), None, ["sin tmdb_id"]))
            continue
        aired, miss = 0, []
        for se in tmdb(f"tv/{s['tmdb_id']}")["seasons"]:
            if se["season_number"] == 0:
                continue
            for ep in tmdb(f"tv/{s['tmdb_id']}/season/{se['season_number']}").get("episodes", []):
                if ep.get("air_date") and ep["air_date"] <= today:
                    aired += 1
                    if (se["season_number"], ep["episode_number"]) not in have:
                        miss.append(f"S{se['season_number']:02d}E{ep['episode_number']:02d}")
        res.append((s["folder"], len(have), aired, miss))
    return res


def compact(codes):
    """['S01E01','S01E02','S01E04'] -> 'S01E01-02, S01E04' (reads, does not dump 80 codes)."""
    out, run = [], []
    for c in codes:
        if run and c[:3] == run[-1][:3] and int(c[4:]) == int(run[-1][4:]) + 1:
            run.append(c)
        else:
            if run:
                out.append(run)
            run = [c]
    out.append(run) if run else None
    return ", ".join(r[0] if len(r) == 1 else f"{r[0]}-{r[-1][3:]}" for r in out)


def main():
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    print("== FALTANTES")
    mm = missing_movies()
    print(f"Películas sin fichero en Radarr: {len(mm)}")
    for m in mm:
        print("  -", m)
    print("Series (tengo / emitidos en TMDB):")
    for folder, have, aired, miss in missing_episodes(conn):
        if miss:
            print(f"  - {folder}: {have}/{aired} · faltan {compact(miss) if aired else miss[0]}")
    rows = load_rows(conn)
    print(f"\n== CUMPLIMIENTO ({len(rows)} ficheros)")
    res = collections.defaultdict(list)
    with cf.ThreadPoolExecutor(4) as ex:
        for kind, label, bad in ex.map(audit_one, rows):
            res[kind].append((label, bad))
    json.dump({k: dict(v) for k, v in res.items()}, open(os.path.join(HERE, "library_audit.json"), "w"),
              ensure_ascii=False, indent=1)
    for kind in ("movie", "episode"):
        items = res[kind]
        ok = sum(1 for _, b in items if not b)
        why = collections.Counter(re.sub(r"\(.*?\)", "", r).strip() for _, b in items for r in set(b))
        print(f"{'Películas' if kind == 'movie' else 'Episodios'}: {ok}/{len(items)} cumplen todo")
        for r, n in why.most_common(8):
            print(f"    {n:4d}  {r}")
    # per show roll-up
    per = collections.defaultdict(lambda: [0, 0])
    for label, bad in res["episode"]:
        s = label.split("|")[0]
        per[s][0] += 1
        per[s][1] += 0 if bad else 1
    print("Series (cumplen/total):", "; ".join(f"{s.split(' (')[0]} {o}/{t}" for s, (t, o) in sorted(per.items())))


if __name__ == "__main__":
    main()
