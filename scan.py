"""Library scan: walk MEDIA_ROOT, clean names, TMDB match, ffprobe/mkvmerge inspect, upsert DB."""
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi")
TMDB_BASE = "https://api.themoviedb.org/3"

JUNK_TOKENS = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray|bdremux|remux|webrip|web-?dl|hdrip|dvdrip|brrip|hdtv|"
    r"x264|x265|h264|h265|hevc|avc|10bit|8bit|hdr10\+?|hdr|dts-?hd|dts|truehd|atmos|ac3|aac|5\.1|7\.1|2\.0|"
    r"yify|yts(\.\w+)?|pelismkvhd|pelismegahd|pelis\w*|extended|uncut|proper|repack|limited|"
    r"multi|dual|dubbed|subs?|espanol|latino|castellano)\b",
    re.IGNORECASE,
)
LANG_SUFFIX_RE = re.compile(r"\.([a-z]{2,3})(?:\.\w+)?\.srt$", re.IGNORECASE)

# Jellyfin/Kodi artwork we must never touch, regardless of source-junk sweeps.
# Matched by suffix, not exact stem: some scrapers name files
# "<release name>-backdrop.jpg" rather than a bare "backdrop.jpg", and that
# release-name prefix can itself contain scene-junk tokens (e.g. a tracker tag)
# that would otherwise trip the junk regex below.
ARTWORK_SUFFIXES = {"folder", "poster", "backdrop", "background", "landscape", "banner",
                     "logo", "fanart", "thumb", "clearart", "clearlogo", "disc", "art"}
ARTWORK_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _is_artwork(stem, ext):
    if ext.lower() not in ARTWORK_EXT:
        return False
    low = stem.lower()
    return any(low == suf or low.endswith(("-" + suf, "." + suf, " " + suf, "_" + suf))
               for suf in ARTWORK_SUFFIXES)

# Known scene/tracker junk patterns -- deny-list, not allow-list: anything that
# doesn't match a known-junk pattern is left alone rather than guessed at.
JUNK_FILE_RE = re.compile(
    r"^www\.[\w.\-]+\.(jpg|jpeg|png|txt|url)$"
    r"|yify|yts\.mx|yifystatus|rarbg|pelismegahd|pelismkvhd|eztv\b|1337x|torrent9"
    r"|^rarbg\.txt$|\.torrent$|\.url$",
    re.IGNORECASE,
)


def find_movie_junk(folder, keep_files):
    """Non-destructive: returns filenames (not paths) of scene/tracker junk in a
    movie folder, excluding keep_files (main video + external subs, by filename)
    and any recognized Jellyfin artwork or .nfo."""
    junk = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return junk
    for f in entries:
        if f in keep_files:
            continue
        path = os.path.join(folder, f)
        if not os.path.isfile(path):
            continue
        if f.startswith("._"):
            junk.append(f)
            continue
        stem, ext = os.path.splitext(f)
        if _is_artwork(stem, ext):
            continue
        if ext.lower() == ".nfo":
            continue
        if JUNK_FILE_RE.search(f):
            junk.append(f)
    return junk
LANG_2TO3 = {"en": "eng", "es": "spa", "fr": "fre", "de": "ger", "it": "ita",
             "pt": "por", "ja": "jpn", "zh": "chi", "ko": "kor", "ru": "rus", "ar": "ara"}
LANG_ISO1_TO_3 = LANG_2TO3  # reuse for original_language (TMDB gives ISO 639-1)


def clean_title_year(raw):
    name = re.sub(r"[._]+", " ", raw)
    m = re.search(r"(?:19|20)\d{2}", name)
    year = int(m.group()) if m else None
    if year:
        before, after = name[:m.start()], name[m.end():]
        before = re.sub(r"[\[\(]\s*$", "", before)
        after = re.sub(r"^\s*[\]\)]", "", after)
        def _score(s):
            s = re.sub(r"\[[^\]]*\]", " ", s)
            s = re.sub(r"\([^)]*\)", " ", s)
            s = JUNK_TOKENS.sub(" ", s)
            return sum(c.isalpha() for c in s)
        title_raw = before if _score(before) >= _score(after) else after
    else:
        title_raw = name
    title_raw = re.sub(r"\[[^\]]*\]", " ", title_raw)
    title_raw = re.sub(r"\([^)]*\)", " ", title_raw)
    title_raw = JUNK_TOKENS.sub(" ", title_raw)
    title_raw = re.sub(r"\s{2,}", " ", title_raw).strip(" -_.")
    return title_raw, year


def tmdb_search(title, year, api_key):
    if not api_key or not title:
        return None
    def _query(with_year):
        params = {"api_key": api_key, "query": title, "include_adult": "false"}
        if with_year and year:
            params["year"] = year
        url = f"{TMDB_BASE}/search/movie?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.load(r).get("results") or []
        except Exception:
            return []
    results = _query(True) or _query(False)
    if not results:
        return None
    # TMDB's `year` param soft-ranks rather than hard-filters, so sequels/parts
    # with near-identical titles can outrank the actual year match (e.g. "Part I"
    # vs "Part II" queries both surfacing "Part 1" first). Prefer exact-year hits.
    if year:
        exact = [r for r in results if (r.get("release_date") or "").startswith(str(year))]
        if exact:
            results = exact
    top = results[0]
    return {
        "tmdb_id": top["id"],
        "title": top.get("title") or title,
        "year": int(top["release_date"][:4]) if top.get("release_date") else year,
        "original_language": top.get("original_language"),
        "poster_path": top.get("poster_path"),
    }


def tmdb_search_candidates(query, year, api_key, limit=6):
    """List of candidate matches for manual re-match UI (unlike tmdb_search, no auto-pick)."""
    if not api_key or not query:
        return []
    params = {"api_key": api_key, "query": query, "include_adult": "false"}
    if year:
        params["year"] = year
    url = f"{TMDB_BASE}/search/movie?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            results = json.load(r).get("results") or []
    except Exception:
        results = []
    return [
        {"tmdb_id": r["id"], "title": r.get("title"),
         "year": int(r["release_date"][:4]) if r.get("release_date") else None,
         "original_language": r.get("original_language"), "poster_path": r.get("poster_path")}
        for r in results[:limit]
    ]


def tmdb_get_movie(tmdb_id, api_key):
    if not api_key:
        return None
    url = f"{TMDB_BASE}/movie/{tmdb_id}?{urllib.parse.urlencode({'api_key': api_key})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.load(r)
    except Exception:
        return None
    return {
        "tmdb_id": d["id"], "title": d.get("title"),
        "year": int(d["release_date"][:4]) if d.get("release_date") else None,
        "original_language": d.get("original_language"), "poster_path": d.get("poster_path"),
    }


PREMIUM_AUDIO_ORDER = ["dts-hd", "dts", "e-ac-3", "eac3", "ac-3", "ac3", "aac"]
# never auto-picked as the default: some clients can't play these and Jellyfin
# transcodes; user re-enables manually when the Atmos experience is wanted
AVOID_DEFAULT_AUDIO = ("truehd", "atmos")


def _audio_rank(codec):
    c = (codec or "").lower()
    for i, name in enumerate(PREMIUM_AUDIO_ORDER):
        if name in c:
            return i
    return len(PREMIUM_AUDIO_ORDER)


def _audio_avoided(codec):
    c = (codec or "").lower()
    return any(a in c for a in AVOID_DEFAULT_AUDIO)


def _sub_class(t):
    c = (t["codec"] or "").lower()
    if "pgs" in c:
        return "pgs"
    if "vobsub" in c:
        return "vob"
    if t.get("ext_path") or "subrip" in c or "srt" in c:
        return "srt"
    return "other"


def suggest_tracks(conn, movie_id):
    """One audio track per language (orig lang first + default), best codec that
    isn't TrueHD/Atmos (client compat — re-enable manually for the Atmos file).
    Subs: forced first (orig lang default), then PGS, then SRT fallback, one per
    (class, lang). Sets status='ready'; overwrites any prior config."""
    movie = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not movie:
        return
    orig3 = LANG_ISO1_TO_3.get(movie["original_language"] or "")
    wanted = [l for l in ([orig3] if orig3 else []) + ["eng", "spa"] if l]
    wanted = list(dict.fromkeys(wanted))  # de-dup, preserve order (original lang first)

    tracks = [dict(r) for r in conn.execute("SELECT * FROM tracks WHERE movie_id=?", (movie_id,))]
    audio = [t for t in tracks if t["type"] == "audio"]
    subs = [t for t in tracks if t["type"] == "subtitle"]
    if len(audio) == 1 and audio[0]["lang"] == "und":
        # only audio track available, no other choice -- always keep it even if
        # TMDB hasn't matched yet (orig3 unknown) or its language isn't eng/spa
        fallback_lang = orig3 or "eng"
        audio[0]["lang"] = fallback_lang  # local-only override for grouping, not written back verbatim
        if fallback_lang not in wanted:
            wanted.insert(0, fallback_lang)

    plan = {}
    order = {"audio": 0, "subtitle": 0}

    def mark(t, lang, default, forced):
        plan[t["id"]] = {"out_order": order[t["type"]], "out_lang": lang,
                          "out_default": 1 if default else 0, "out_forced": 1 if forced else 0}
        order[t["type"]] += 1

    # audio: one track per language, best codec that isn't TrueHD/Atmos;
    # avoided codecs only win when they're the sole option for that language
    first_audio = True
    for lang in wanted:
        cands = sorted([t for t in audio if t["lang"] == lang], key=lambda t: _audio_rank(t["codec"]))
        if not cands:
            continue
        pick = next((t for t in cands if not _audio_avoided(t["codec"])), cands[0])
        mark(pick, lang, default=first_audio, forced=False)
        first_audio = False

    # subs, output order: forced (movie language first, that one default),
    # then one image sub (PGS, VobSub fallback) per language, then one SRT
    # per language. One track per (class, lang) — extras stay unchecked.
    first_forced = True
    for lang in wanted:
        f = next((t for t in subs if t["forced_flag"]
                  and (t["lang"] == lang or (t["lang"] == "und" and lang == (orig3 or "eng")))), None)
        if f:
            mark(f, lang, default=first_forced, forced=True)
            first_forced = False
    for lang in wanted:
        cands = [t for t in subs if t["lang"] == lang and not t["forced_flag"] and t["id"] not in plan]
        img = next((t for t in cands if _sub_class(t) == "pgs"), None) \
            or next((t for t in cands if _sub_class(t) == "vob"), None)
        if img:
            mark(img, lang, default=False, forced=False)
    for lang in wanted:
        cands = [t for t in subs if t["lang"] == lang and not t["forced_flag"] and t["id"] not in plan]
        srt = next((t for t in cands if _sub_class(t) == "srt"), None)
        if srt:
            mark(srt, lang, default=False, forced=False)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for t in tracks:
        if t["type"] == "video":
            lang = t["lang"] if t["lang"] != "und" else (orig3 or "eng")
            conn.execute("UPDATE tracks SET keep=1, out_order=0, out_lang=?, out_default=1, out_forced=0 WHERE id=?",
                         (lang, t["id"]))
            continue
        cfg = plan.get(t["id"])
        if cfg:
            conn.execute(
                "UPDATE tracks SET keep=1, out_order=?, out_lang=?, out_default=?, out_forced=? WHERE id=?",
                (cfg["out_order"], cfg["out_lang"], cfg["out_default"], cfg["out_forced"], t["id"]),
            )
        else:
            conn.execute("UPDATE tracks SET keep=0 WHERE id=?", (t["id"],))
    conn.execute("UPDATE movies SET status='ready', updated_at=? WHERE id=?", (now, movie_id))
    conn.commit()


def guess_srt_lang(filename):
    m = LANG_SUFFIX_RE.search(filename)
    if not m:
        return "und"
    code = m.group(1).lower()
    return code if len(code) == 3 else LANG_2TO3.get(code, "und")


def find_external_subs(folder):
    subs = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return subs
    for f in entries:
        if f.lower().endswith(".srt"):
            subs.append({"ext_path": os.path.join(folder, f), "lang": guess_srt_lang(f), "name": f})
    return subs


def find_main_file(folder):
    best, best_size = None, -1
    try:
        entries = os.scandir(folder)
    except OSError:
        return None
    for e in entries:
        if e.is_file() and e.name.lower().endswith(VIDEO_EXT) and not e.name.startswith("._"):
            size = e.stat().st_size
            if size > best_size:
                best, best_size = e.name, size
    return best


def _run_json(cmd, timeout=60):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return json.loads(out.stdout) if out.stdout else {}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}


def _detect_hdr(streams):
    for s in streams:
        if s.get("codec_type") != "video":
            continue
        transfer = (s.get("color_transfer") or "").lower()
        side = s.get("side_data_list") or []
        has_dv = any("dovi" in str(sd).lower() or "dolby vision" in str(sd).lower() for sd in side)
        if has_dv and transfer == "smpte2084":
            return "DV+HDR10"
        if has_dv:
            return "DV"
        if transfer == "smpte2084":
            return "HDR10"
        if transfer == "arib-std-b67":
            return "HLG"
    return "SDR"


def inspect_file(path):
    """Returns dict: container_title, duration, video_codec, width, height, bitrate,
    hdr, size_bytes, tracks (list of dicts w/ mkv_id/type/codec/lang/name/channels/default/forced)."""
    mkv = _run_json(["mkvmerge", "-J", path])
    props = (mkv.get("container") or {}).get("properties", {})
    tracks = []
    for t in mkv.get("tracks", []):
        ttype = "subtitle" if t["type"] == "subtitles" else t["type"]
        tp = t.get("properties", {})
        tracks.append({
            "mkv_id": t["id"], "type": ttype, "codec": t.get("codec"),
            "lang": tp.get("language") or tp.get("language_ietf") or "und",
            "name": tp.get("track_name") or "",
            "channels": tp.get("audio_channels"),
            "default_flag": 1 if tp.get("default_track") else 0,
            "forced_flag": 1 if tp.get("forced_track") else 0,
            "ext_path": None,
        })

    ff = _run_json(["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_format", "-show_streams", path], timeout=30)
    streams = ff.get("streams", [])
    fmt = ff.get("format", {})
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})

    duration = None
    try:
        duration = float(fmt.get("duration") or 0) or None
    except (TypeError, ValueError):
        pass
    bitrate = None
    try:
        bitrate = int(fmt.get("bit_rate") or 0) or None
    except (TypeError, ValueError):
        pass

    return {
        "container_title": props.get("title"),
        "duration": duration,
        "video_codec": vstream.get("codec_name"),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "bitrate": bitrate,
        "hdr": _detect_hdr(streams),
        "size_bytes": os.path.getsize(path) if os.path.exists(path) else None,
        "tracks": tracks,
    }


def upsert_movie(conn, media_root, folder_name, api_key):
    """Scan one top-level folder, upsert movies + tracks rows. Preserves existing
    track config (keep/out_order/out_lang/out_default/out_forced/out_name) across
    rescans by matching on mkv_id (internal) or ext_path (external)."""
    folder_path = os.path.join(media_root, folder_name)
    if not os.path.isdir(folder_path) or folder_name.startswith("._"):
        return None

    main_file = find_main_file(folder_path)
    clean_title, guess_year = clean_title_year(folder_name)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    cur = conn.execute("SELECT id FROM movies WHERE folder = ?", (folder_name,))
    row = cur.fetchone()
    movie_id = row["id"] if row else None

    if not main_file:
        if movie_id:
            conn.execute(
                "UPDATE movies SET file=NULL, clean_title=?, guess_year=?, status='stub', updated_at=? WHERE id=?",
                (clean_title, guess_year, now, movie_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO movies (folder, file, clean_title, guess_year, status, updated_at) "
                "VALUES (?, NULL, ?, ?, 'stub', ?)",
                (folder_name, clean_title, guess_year, now),
            )
            movie_id = cur.lastrowid
        conn.commit()
        return movie_id

    file_path = os.path.join(folder_path, main_file)
    tmdb = tmdb_search(clean_title, guess_year, api_key)
    info = inspect_file(file_path)
    ext_subs = find_external_subs(folder_path)

    # keep status if already configured/working/clean; else unprocessed/unmatched
    existing_status = None
    if movie_id:
        existing_status = conn.execute("SELECT status FROM movies WHERE id=?", (movie_id,)).fetchone()["status"]
    status = existing_status if existing_status in ("ready", "working", "clean", "cleaning", "encoding") else "unprocessed"

    fields = dict(
        folder=folder_name, file=main_file, clean_title=clean_title, guess_year=guess_year,
        tmdb_id=(tmdb or {}).get("tmdb_id"), title=(tmdb or {}).get("title"), year=(tmdb or {}).get("year"),
        original_language=(tmdb or {}).get("original_language"), poster_path=(tmdb or {}).get("poster_path"),
        container_title=info["container_title"], video_codec=info["video_codec"],
        width=info["width"], height=info["height"], bitrate=info["bitrate"],
        duration=info["duration"], size_bytes=info["size_bytes"], hdr=info["hdr"],
        status=status, updated_at=now,
    )

    if movie_id:
        set_clause = ", ".join(f"{k}=?" for k in fields if k != "folder")
        vals = [v for k, v in fields.items() if k != "folder"] + [movie_id]
        conn.execute(f"UPDATE movies SET {set_clause} WHERE id=?", vals)
    else:
        cols = ", ".join(fields.keys())
        qs = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO movies ({cols}) VALUES ({qs})", list(fields.values()))
        movie_id = cur.lastrowid

    _upsert_tracks(conn, movie_id, info["tracks"], ext_subs, (tmdb or {}).get("original_language"))
    conn.commit()
    return movie_id


def _upsert_tracks(conn, movie_id, source_tracks, ext_subs, original_language):
    existing = {}
    for r in conn.execute("SELECT * FROM tracks WHERE movie_id=?", (movie_id,)):
        key = ("mkv", r["mkv_id"]) if r["mkv_id"] is not None else ("ext", r["ext_path"])
        existing[key] = r

    seen_keys = set()
    order_counters = {"audio": 0, "subtitle": 0}
    orig_lang_3 = LANG_ISO1_TO_3.get(original_language or "", None)

    def upsert_one(key, type_, codec, lang, name, channels, default_flag, forced_flag, ext_path, mkv_id):
        seen_keys.add(key)
        prior = existing.get(key)
        if prior:
            conn.execute(
                "UPDATE tracks SET codec=?, lang=?, name=?, channels=?, default_flag=?, forced_flag=? WHERE id=?",
                (codec, lang, name, channels, default_flag, forced_flag, prior["id"]),
            )
            return
        out_lang = lang if lang in ("eng", "spa") or (orig_lang_3 and lang == orig_lang_3) else (
            orig_lang_3 if type_ == "audio" and orig_lang_3 else "")
        out_order = order_counters.get(type_, 0)
        if type_ in order_counters:
            order_counters[type_] += 1
        conn.execute(
            "INSERT INTO tracks (movie_id, mkv_id, type, codec, lang, name, channels, default_flag, forced_flag, "
            "ext_path, keep, out_order, out_lang, out_default, out_forced, out_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
            (movie_id, mkv_id, type_, codec, lang, name, channels, default_flag, forced_flag,
             ext_path, out_order, out_lang, default_flag, forced_flag, ""),
        )

    for t in source_tracks:
        key = ("mkv", t["mkv_id"])
        upsert_one(key, t["type"], t["codec"], t["lang"], t["name"], t["channels"],
                   t["default_flag"], t["forced_flag"], None, t["mkv_id"])

    for s in ext_subs:
        key = ("ext", s["ext_path"])
        upsert_one(key, "subtitle", "SRT", s["lang"], s["name"], None, 0, 0, s["ext_path"], None)

    for key, prior in existing.items():
        if key not in seen_keys:
            conn.execute("DELETE FROM tracks WHERE id=?", (prior["id"],))


def scan_library(conn, media_root, api_key, progress_cb=None):
    """Scans all top-level entries. progress_cb(done, total, name) optional."""
    entries = sorted(e.name for e in os.scandir(media_root) if e.is_dir() and not e.name.startswith("._"))
    # prune rows whose folder vanished from disk (renamed away, merged, deleted)
    on_disk = set(entries)
    for r in conn.execute("SELECT id, folder FROM movies").fetchall():
        if r["folder"] not in on_disk:
            conn.execute("DELETE FROM movies WHERE id=?", (r["id"],))
    conn.commit()
    total = len(entries)
    for i, name in enumerate(entries, start=1):
        try:
            upsert_movie(conn, media_root, name, api_key)
        except Exception as e:
            conn.execute(
                "INSERT INTO movies (folder, status, updated_at) VALUES (?, 'error', ?) "
                "ON CONFLICT(folder) DO UPDATE SET status='error', updated_at=excluded.updated_at",
                (name, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            conn.commit()
        if progress_cb:
            progress_cb(i, total, name)
    return total


if __name__ == "__main__":
    assert clean_title_year("Arrival (2016) [2160p] [4K] [BluRay] [5.1] [YTS.MX]") == ("Arrival", 2016)
    t, y = clean_title_year("[2016] ¿Qué culpa tiene el niño")
    assert y == 2016 and "culpa" in t.lower(), (t, y)
    assert clean_title_year("12 Angry Men (1957)") == ("12 Angry Men", 1957)
    assert guess_srt_lang("movie.eng.sdh.srt") == "eng"
    assert guess_srt_lang("movie.spa.srt") == "spa"
    assert guess_srt_lang("movie.srt") == "und"

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        names = [
            "Movie (2016).mkv", "Movie.eng.srt", "folder.jpg", "backdrop.jpg",
            "landscape.jpg", "logo.png", "movie.nfo",
            "www.YTS.MX.jpg", "YIFYStatus.com.txt", "RARBG.txt", "._Movie (2016)",
            "2 Fast 2 Furious (2003) 1080p BluRay REMUX [wWw.PelisMKVHD.Com]-logo.png",
            "2 Fast 2 Furious (2003) 1080p BluRay REMUX [wWw.PelisMKVHD.Com]-backdrop.jpg",
        ]
        for n in names:
            open(os.path.join(d, n), "w").close()
        junk = set(find_movie_junk(d, {"Movie (2016).mkv", "Movie.eng.srt"}))
        assert junk == {"www.YTS.MX.jpg", "YIFYStatus.com.txt", "RARBG.txt", "._Movie (2016)"}, junk

    # suggest_tracks: TrueHD skipped for default, forced sub first+default, dup PGS unchecked
    import sqlite3 as _sq
    c = _sq.connect(":memory:")
    c.row_factory = _sq.Row
    c.executescript("""
      CREATE TABLE movies (id INTEGER PRIMARY KEY, original_language TEXT, status TEXT, updated_at TEXT);
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, mkv_id INT, type TEXT, codec TEXT,
        lang TEXT, name TEXT, channels INT, default_flag INT DEFAULT 0, forced_flag INT DEFAULT 0,
        ext_path TEXT, keep INT DEFAULT 1, out_order INT DEFAULT 0, out_lang TEXT DEFAULT '',
        out_default INT DEFAULT 0, out_forced INT DEFAULT 0, out_name TEXT DEFAULT '');
      INSERT INTO movies VALUES (1, 'en', 'unprocessed', NULL);
      INSERT INTO tracks (id, movie_id, mkv_id, type, codec, lang, forced_flag) VALUES
        (1,1,0,'video','HEVC','und',0),
        (2,1,1,'audio','TrueHD Atmos','eng',0),
        (3,1,2,'audio','DTS-HD Master Audio','eng',0),
        (4,1,3,'audio','AC-3','spa',0),
        (5,1,4,'subtitle','HDMV PGS','spa',1),
        (6,1,5,'subtitle','HDMV PGS','spa',0),
        (7,1,6,'subtitle','HDMV PGS','spa',0),
        (8,1,7,'subtitle','SubRip/SRT','eng',0);
    """)
    suggest_tracks(c, 1)
    got = {r["id"]: dict(r) for r in c.execute("SELECT * FROM tracks")}
    assert got[2]["keep"] == 0, "TrueHD must stay unchecked"
    assert got[3]["keep"] == 1 and got[3]["out_default"] == 1 and got[3]["out_order"] == 0
    assert got[4]["keep"] == 1 and got[4]["out_default"] == 0 and got[4]["out_order"] == 1
    assert got[5]["keep"] == 1 and got[5]["out_forced"] == 1 and got[5]["out_default"] == 1 and got[5]["out_order"] == 0
    assert got[6]["keep"] == 1 and got[6]["out_order"] == 1
    assert got[7]["keep"] == 0, "duplicate PGS must stay unchecked"
    assert got[8]["keep"] == 1 and got[8]["out_order"] == 2
    print("scan.py self-check OK")
