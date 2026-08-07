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


def _artwork_base(stem, ext):
    """None if not artwork. '' for generic artwork (folder.jpg, backdrop.jpg).
    Otherwise the file-stem prefix it belongs to ('Movie.hevc-poster' -> 'Movie.hevc')."""
    if ext.lower() not in ARTWORK_EXT:
        return None
    low = stem.lower()
    for suf in ARTWORK_SUFFIXES:
        if low == suf:
            return ""
        if low.endswith(("-" + suf, "." + suf, " " + suf, "_" + suf)):
            return stem[:-(len(suf) + 1)]
    return None


def _is_artwork(stem, ext):
    return _artwork_base(stem, ext) is not None

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
    and recognized Jellyfin artwork/.nfo — unless that artwork/.nfo belongs to a
    file that no longer exists (orphans left behind after delete-original)."""
    junk = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return junk
    # stems that per-file artwork/.nfo may legitimately reference
    live_stems = {os.path.splitext(f)[0] for f in entries if f.lower().endswith(VIDEO_EXT)}
    live_stems |= {os.path.splitext(f)[0] for f in keep_files}
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
        base = _artwork_base(stem, ext)
        if base is not None:
            if base and base not in live_stems:
                junk.append(f)  # orphaned Jellyfin artwork
            continue
        if ext.lower() == ".nfo":
            if stem.lower() != "movie" and stem not in live_stems:
                junk.append(f)  # orphaned nfo
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


def tmdb_search_candidates(query, year, api_key, limit=6, kind="movie"):
    """List of candidate matches for manual re-match UI (unlike tmdb_search, no
    auto-pick). kind='tv' searches shows (TMDB field names differ: name/first_air_date
    instead of title/release_date)."""
    if not api_key or not query:
        return []
    endpoint = "movie" if kind == "movie" else "tv"
    params = {"api_key": api_key, "query": query, "include_adult": "false"}
    if year and kind == "movie":
        params["year"] = year
    url = f"{TMDB_BASE}/search/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            results = json.load(r).get("results") or []
    except Exception:
        results = []
    if kind == "movie":
        return [
            {"tmdb_id": r["id"], "title": r.get("title"),
             "year": int(r["release_date"][:4]) if r.get("release_date") else None,
             "original_language": r.get("original_language"), "poster_path": r.get("poster_path")}
            for r in results[:limit]
        ]
    return [
        {"tmdb_id": r["id"], "title": r.get("name"),
         "year": int(r["first_air_date"][:4]) if r.get("first_air_date") else None,
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
# kept in the file but never auto-picked as default/first: some clients can't
# play these and Jellyfin transcodes — the compatible pick leads instead
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


def track_sig(t):
    """Stable identity for a track across sibling episodes: type + language +
    codec + the release's own track name. mkv_id/order is NOT part of it --
    sibling episodes routinely carry the same tracks in a different order."""
    return "|".join([t["type"], t["lang"] or "", t["codec"] or "", (t["name"] or "").strip()])


def suggest_tracks(conn, owner_id, table="movies", multi_audio=False):
    """One audio track per language (orig lang first + default), best codec that
    isn't TrueHD/Atmos; TrueHD/Atmos kept after the compatible picks, never default.
    Subs: forced first (orig lang default), then PGS, then SRT fallback, one per
    (class, lang). Sets status='ready'; overwrites any prior config.

    multi_audio=True (TV only): keeps EVERY audio track in a wanted language
    (multiple dub sources), instead of one best-codec pick per language."""
    col = "movie_id" if table == "movies" else "episode_id"
    movie = conn.execute(f"SELECT * FROM {table} WHERE id=?", (owner_id,)).fetchone()
    if not movie:
        return
    # episodes hold no original_language of their own -- it lives on the parent show
    if table == "movies":
        original_language = movie["original_language"]
    else:
        show = conn.execute("SELECT original_language FROM shows WHERE id=?", (movie["show_id"],)).fetchone()
        original_language = show["original_language"] if show else None
    orig3 = LANG_ISO1_TO_3.get(original_language or "")
    wanted = [l for l in ([orig3] if orig3 else []) + ["eng", "spa"] if l]
    wanted = list(dict.fromkeys(wanted))  # de-dup, preserve order (original lang first)

    tracks = [dict(r) for r in conn.execute(f"SELECT * FROM tracks WHERE {col}=?", (owner_id,))]
    audio = [t for t in tracks if t["type"] == "audio"]
    subs = [t for t in tracks if t["type"] == "subtitle"]

    # a bare "spa" is only in `wanted` as a stand-in for whichever Spanish
    # variant(s) actually exist in the file -- swap it for the real ones found
    # (Latino and Castellano both survive if both are present) instead of
    # letting the one-per-language rule collapse them into a single pick
    if "spa" in wanted:
        spa_variants = sorted({t["lang"] for t in audio + subs if t["lang"].startswith("spa")})
        i = wanted.index("spa")
        wanted[i:i + 1] = spa_variants or ["spa"]
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
        # out_lang is muxed as a real mkv language tag -- "spa-mx"/"spa-es" are
        # this module's internal grouping keys, not valid ISO codes, so they
        # collapse back to "spa" here; the Latino/Castellano distinction is
        # still visible from the source track's `lang` column in the UI
        plan[t["id"]] = {"out_order": order[t["type"]], "out_lang": _spanish_base(lang),
                          "out_default": 1 if default else 0, "out_forced": 1 if forced else 0}
        order[t["type"]] += 1

    # audio: one track per language, best codec that isn't TrueHD/Atmos;
    # avoided codecs only win when they're the sole option for that language.
    # multi_audio (TV): keep every dub source in a wanted language instead.
    first_audio = True
    for lang in wanted:
        # avoided codecs sort last within a language, so cands[0] is the compatible
        # best — the same pick the old next() made, one less pass
        cands = sorted([t for t in audio if t["lang"] == lang],
                       key=lambda t: (_audio_avoided(t["codec"]), _audio_rank(t["codec"])))
        if not cands:
            continue
        # ponytail: TV keeps every dub in a wanted language; movies keep one
        for t in (cands if multi_audio else cands[:1]):
            mark(t, lang, default=first_audio, forced=False)
            first_audio = False
    if not multi_audio:
        # TrueHD/Atmos stays in the file for the premium experience — kept after
        # the compatible picks, never default (one per language)
        for lang in wanted:
            t = next((t for t in audio if t["lang"] == lang and _audio_avoided(t["codec"])
                      and t["id"] not in plan), None)
            if t:
                mark(t, lang, default=False, forced=False)

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
    conn.execute(f"UPDATE {table} SET status='ready', updated_at=? WHERE id=?", (now, owner_id))
    conn.commit()


def guess_srt_lang(filename):
    m = LANG_SUFFIX_RE.search(filename)
    if not m:
        return "und"
    code = m.group(1).lower()
    return code if len(code) == 3 else LANG_2TO3.get(code, "und")


def find_external_subs(folder, stem=None):
    """stem: if given (episode path), only .srt files whose name starts with the
    video's own stem are matched -- otherwise a season folder makes every
    episode adopt every sibling's subtitle files (and delete_original would
    then delete them out from under the other episodes)."""
    subs = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return subs
    for f in entries:
        if not f.lower().endswith(".srt"):
            continue
        if stem is not None and not f.startswith(stem):
            continue
        subs.append({"ext_path": os.path.join(folder, f), "lang": guess_srt_lang(f), "name": f})
    return subs


# ---------- TV shows ----------

SEASON_DIR_RE = re.compile(r"^season\s*0*(\d+)$|^s0*(\d+)$", re.IGNORECASE)
# SxxExx / sxex, or a bare "1x03" form. Deliberately requires the S.../x
# separator so a lone year like "2019" in a filename never matches.
EPISODE_RE = re.compile(r"s0*(\d+)\s*e0*(\d+)|(?<!\d)(\d{1,2})x(\d{2})(?!\d)", re.IGNORECASE)


def parse_episode(name):
    """(season, episode) or None. Tries SxxExx first, then 1x03; a year
    (Show 2019 S02E10) never matches by itself since EPISODE_RE requires
    the S/E or x separator, not bare digits."""
    m = EPISODE_RE.search(name)
    if not m:
        return None
    if m.group(1) is not None:
        return int(m.group(1)), int(m.group(2))
    return int(m.group(3)), int(m.group(4))


def classify_folder(path):
    """'show' if the folder holds a Season NN / SNN subdir, or >=2 files that
    parse as episodes; 'movie' otherwise (the existing one-video-per-folder
    default). A single stray SxxExx-named file is not enough -- movies
    occasionally have a release tag that happens to look like one."""
    try:
        entries = list(os.scandir(path))
    except OSError:
        return "movie"
    for e in entries:
        if e.is_dir() and SEASON_DIR_RE.match(e.name):
            return "show"
    ep_files = [e.name for e in entries
                if e.is_file() and e.name.lower().endswith(VIDEO_EXT) and parse_episode(e.name)]
    return "show" if len(ep_files) >= 2 else "movie"


JOB_OUTPUT_RE = re.compile(r"\.(remux|hevc)\.mkv$|\.sample\.rf\d+\.mkv$", re.IGNORECASE)


def find_episode_files(folder):
    """[(rel_subpath, season, episode)] -- rel_subpath is relative to `folder`
    and may include a season subdir. Recurses exactly one extra level (season
    dirs only), matching scan_library's one-level movie walk.

    Movies never see two files claim to be the same movie because
    find_main_file picks one (the largest) per folder. Episodes have no such
    guarantee -- a remux/encode job leaves its OWN SxxExx-matching output
    sitting next to the source until delete-original runs, and both would
    otherwise register as separate episodes. So candidates are grouped by
    (season, episode) and, within a group, a job-output file only wins if it's
    the only candidate (i.e. delete-original already ran); otherwise the
    original source file wins, exactly mirroring find_main_file's role."""
    candidates = []  # (rel_subpath, season, episode, stat_size)
    try:
        top = list(os.scandir(folder))
    except OSError:
        return []
    for e in top:
        if e.is_file() and e.name.lower().endswith(VIDEO_EXT) and not e.name.startswith("._"):
            ep = parse_episode(e.name)
            if ep:
                candidates.append((e.name, ep[0], ep[1], e.stat().st_size))
        elif e.is_dir() and SEASON_DIR_RE.match(e.name):
            try:
                sub = list(os.scandir(e.path))
            except OSError:
                continue
            for f in sub:
                if f.is_file() and f.name.lower().endswith(VIDEO_EXT) and not f.name.startswith("._"):
                    ep = parse_episode(f.name)
                    if ep:
                        candidates.append((os.path.join(e.name, f.name), ep[0], ep[1], f.stat().st_size))

    groups = {}
    for rel_path, season, episode, size in candidates:
        groups.setdefault((season, episode), []).append((rel_path, size))
    out = []
    for (season, episode), files in groups.items():
        non_output = [f for f in files if not JOB_OUTPUT_RE.search(f[0])]
        pool = non_output or files
        best = max(pool, key=lambda f: f[1])  # largest, ties broken deterministically by size
        out.append((best[0], season, episode))
    return out


def tmdb_search_tv(name, api_key):
    if not api_key or not name:
        return None
    params = {"api_key": api_key, "query": name, "include_adult": "false"}
    url = f"{TMDB_BASE}/search/tv?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            results = json.load(r).get("results") or []
    except Exception:
        results = []
    if not results:
        return None
    top = results[0]
    return {
        "tmdb_id": top["id"],
        "title": top.get("name") or name,
        "year": int(top["first_air_date"][:4]) if top.get("first_air_date") else None,
        "original_language": top.get("original_language"),
        "poster_path": top.get("poster_path"),
    }


def tmdb_get_tv(tmdb_id, api_key):
    if not api_key:
        return None
    url = f"{TMDB_BASE}/tv/{tmdb_id}?{urllib.parse.urlencode({'api_key': api_key})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.load(r)
    except Exception:
        return None
    return {
        "tmdb_id": d["id"], "title": d.get("name"),
        "year": int(d["first_air_date"][:4]) if d.get("first_air_date") else None,
        "original_language": d.get("original_language"), "poster_path": d.get("poster_path"),
        "seasons": [
            {"season_number": sn["season_number"], "episode_count": sn["episode_count"]}
            for sn in d.get("seasons") or [] if sn["season_number"] != 0 or sn["episode_count"]
        ],
    }


def upsert_show(conn, media_root, folder_name, api_key):
    """Mirrors upsert_movie: upserts the show row, then each episode file found
    by find_episode_files, then prunes episode rows whose file vanished."""
    folder_path = os.path.join(media_root, folder_name)
    clean_title, guess_year = clean_title_year(folder_name)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    row = conn.execute("SELECT id FROM shows WHERE folder=?", (folder_name,)).fetchone()
    show_id = row["id"] if row else None
    tmdb = tmdb_search_tv(clean_title, api_key) if not show_id else None
    if show_id:
        existing = conn.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
        tmdb = {"tmdb_id": existing["tmdb_id"], "title": existing["title"], "year": existing["year"],
                "original_language": existing["original_language"], "poster_path": existing["poster_path"]}

    fields = dict(
        folder=folder_name, clean_title=clean_title, guess_year=guess_year,
        tmdb_id=(tmdb or {}).get("tmdb_id"), title=(tmdb or {}).get("title"), year=(tmdb or {}).get("year"),
        original_language=(tmdb or {}).get("original_language"), poster_path=(tmdb or {}).get("poster_path"),
        updated_at=now,
    )
    if show_id:
        set_clause = ", ".join(f"{k}=?" for k in fields if k != "folder")
        vals = [v for k, v in fields.items() if k != "folder"] + [show_id]
        conn.execute(f"UPDATE shows SET {set_clause} WHERE id=?", vals)
    else:
        cols = ", ".join(fields.keys())
        qs = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO shows ({cols}) VALUES ({qs})", list(fields.values()))
        show_id = cur.lastrowid

    found = find_episode_files(folder_path)
    seen_files = set()
    for rel_path, season, episode in found:
        seen_files.add(rel_path)
        ep_folder_name = os.path.dirname(rel_path)  # '' or a season subdir name
        ep_file = os.path.basename(rel_path)
        ep_row = conn.execute(
            "SELECT id, status, updated_at FROM episodes WHERE show_id=? AND folder=? AND file=?",
            (show_id, ep_folder_name, ep_file),
        ).fetchone()
        abs_path = os.path.join(folder_path, rel_path)
        # a show's own folder mtime says nothing about a file inside a season
        # subdir, so episodes are skipped per file instead of per show
        if path_unchanged(ep_row, abs_path):
            continue
        info = inspect_file(abs_path)
        stem = os.path.splitext(ep_file)[0]
        ext_subs = find_external_subs(os.path.join(folder_path, ep_folder_name), stem=stem)
        existing_status = ep_row["status"] if ep_row else None
        status = existing_status if existing_status in (
            "ready", "working", "clean", "cleaning", "encoding") else "unprocessed"
        ep_fields = dict(
            show_id=show_id, season=season, episode=episode, folder=ep_folder_name, file=ep_file,
            container_title=info["container_title"], video_codec=info["video_codec"],
            width=info["width"], height=info["height"], bitrate=info["bitrate"],
            duration=info["duration"], size_bytes=info["size_bytes"], hdr=info["hdr"],
            status=status, updated_at=now,
        )
        if ep_row:
            set_clause = ", ".join(f"{k}=?" for k in ep_fields if k not in ("folder", "file"))
            vals = [v for k, v in ep_fields.items() if k not in ("folder", "file")] + [ep_row["id"]]
            conn.execute(f"UPDATE episodes SET {set_clause} WHERE id=?", vals)
            episode_id = ep_row["id"]
        else:
            cols = ", ".join(ep_fields.keys())
            qs = ", ".join("?" for _ in ep_fields)
            cur = conn.execute(f"INSERT INTO episodes ({cols}) VALUES ({qs})", list(ep_fields.values()))
            episode_id = cur.lastrowid
        _upsert_tracks(conn, episode_id, info["tracks"], ext_subs, (tmdb or {}).get("original_language"),
                       owner_col="episode_id")

    for r in conn.execute("SELECT id, folder, file FROM episodes WHERE show_id=?", (show_id,)).fetchall():
        rel = os.path.join(r["folder"], r["file"]) if r["folder"] else r["file"]
        if rel not in seen_files:
            conn.execute("DELETE FROM episodes WHERE id=?", (r["id"],))
    conn.commit()
    return show_id


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


# mkv's legacy language field is ISO 639-2 only -- "spa" carries no region, so
# Latin American and European Spanish audio collide as one language and only
# one survives the one-per-language rule. Rippers that care tag it in the
# track NAME instead ("Latino" / "Castellano" / "Spain"), so that's the only
# real signal available. This is a guess, not a real language code: it only
# refines the internal `lang` used for matching/display, never what's written
# to out_lang (mkvmerge/HandBrake need a real ISO code -- see _spanish_base).
SPANISH_MX_RE = re.compile(r"\blatino\b|\blat(?:am)?\b|\bmex(?:ico)?\b|\b(?:es-)?419\b", re.IGNORECASE)
SPANISH_ES_RE = re.compile(r"\bcastellano\b|\bespa[ñn]a\b|\bspain\b|\b(?:es-)?es\b", re.IGNORECASE)


def _spanish_variant(lang, name):
    """'spa'|'spa-mx'|'spa-es' -- refines a bare 'spa' using the track name's
    Latino/Castellano wording. Non-Spanish or already-specific langs pass through."""
    if lang != "spa" or not name:
        return lang
    if SPANISH_MX_RE.search(name):
        return "spa-mx"
    if SPANISH_ES_RE.search(name):
        return "spa-es"
    return lang


def _spanish_base(lang):
    """Real ISO code to actually write out (mkvmerge/mkvpropedit don't know
    'spa-mx') -- strips the variant suffix this module adds internally."""
    return lang.split("-")[0]


def inspect_file(path):
    """Returns dict: container_title, duration, video_codec, width, height, bitrate,
    hdr, size_bytes, tracks (list of dicts w/ mkv_id/type/codec/lang/name/channels/default/forced)."""
    mkv = _run_json(["mkvmerge", "-J", path])
    props = (mkv.get("container") or {}).get("properties", {})
    tracks = []
    for t in mkv.get("tracks", []):
        ttype = "subtitle" if t["type"] == "subtitles" else t["type"]
        tp = t.get("properties", {})
        name = tp.get("track_name") or ""
        lang = tp.get("language") or tp.get("language_ietf") or "und"
        tracks.append({
            "mkv_id": t["id"], "type": ttype, "codec": t.get("codec"),
            "lang": _spanish_variant(lang, name),
            "name": name,
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

    # mkv rarely puts a duration on the stream itself; it lives in a DURATION tag
    video_duration = None
    for src in (vstream.get("duration"), (vstream.get("tags") or {}).get("DURATION")):
        if not src:
            continue
        try:
            if ":" in str(src):
                h, m, s = str(src).split(":")
                video_duration = int(h) * 3600 + int(m) * 60 + float(s)
            else:
                video_duration = float(src)
            break
        except (TypeError, ValueError):
            pass

    return {
        "container_title": props.get("title"),
        "duration": duration,
        "video_duration": video_duration,
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


def _upsert_tracks(conn, owner_id, source_tracks, ext_subs, original_language, owner_col="movie_id"):
    existing = {}
    for r in conn.execute(f"SELECT * FROM tracks WHERE {owner_col}=?", (owner_id,)):
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
        # lang may be a spa-mx/spa-es grouping key (see _spanish_variant) --
        # out_lang always needs a real mkv language tag, never that suffix
        out_lang = _spanish_base(lang) if lang == "eng" or lang.startswith("spa") or lang == orig_lang_3 else (
            orig_lang_3 if type_ == "audio" and orig_lang_3 else "")
        out_order = order_counters.get(type_, 0)
        if type_ in order_counters:
            order_counters[type_] += 1
        conn.execute(
            f"INSERT INTO tracks ({owner_col}, mkv_id, type, codec, lang, name, channels, default_flag, forced_flag, "
            "ext_path, keep, out_order, out_lang, out_default, out_forced, out_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
            (owner_id, mkv_id, type_, codec, lang, name, channels, default_flag, forced_flag,
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


def path_unchanged(row, path):
    """True when a movie folder / episode file needs no re-inspect: its row is
    healthy and the path has not been touched since the row was written. A file
    added or removed inside a folder bumps that directory's mtime, so new and
    deleted media is always picked up.

    ponytail: mtime-only. An in-place edit that keeps the entry list identical
    (mkvpropedit retagging a track) leaves mtime alone and is missed -- MM
    updates the DB itself after its own jobs, so this only bites external edits.
    Upgrade path: a force flag on /api/scan that bypasses this check."""
    if row is None or row["status"] in ("stub", "error"):
        return False
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return False
    return row["updated_at"] > time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime))


def scan_library(conn, media_root, api_key, progress_cb=None, include_shows=True):
    """Scans all top-level entries, classifying each as a movie or a show
    before pruning -- a folder reclassified between scans (or one that just
    vanished) is pruned from whichever table it used to live in.

    include_shows=False: skip show classification/pruning entirely -- used
    when TV shows live under a separate MM_SHOWS_ROOT, scanned instead by
    scan_shows_root() below. Without this, every top-level name here would be
    treated as this root's complete set of shows, and any show actually
    living under the other root would look "vanished" and get pruned."""
    entries = sorted(e.name for e in os.scandir(media_root) if e.is_dir() and not e.name.startswith("."))
    kinds = {name: (classify_folder(os.path.join(media_root, name)) if include_shows else "movie")
             for name in entries}
    movie_folders = {n for n, k in kinds.items() if k == "movie"}
    show_folders = {n for n, k in kinds.items() if k == "show"}

    for r in conn.execute("SELECT id, folder FROM movies").fetchall():
        if r["folder"] not in movie_folders:
            conn.execute("DELETE FROM movies WHERE id=?", (r["id"],))
    if include_shows:
        for r in conn.execute("SELECT id, folder FROM shows").fetchall():
            if r["folder"] not in show_folders:
                conn.execute("DELETE FROM shows WHERE id=?", (r["id"],))
    conn.commit()

    # shows are deliberately not skipped: an episode added under a season
    # subfolder leaves the show's own directory mtime untouched
    seen = {r["folder"]: r for r in
            conn.execute("SELECT folder, status, updated_at FROM movies").fetchall()}

    total = len(entries)
    for i, name in enumerate(entries, start=1):
        try:
            if kinds[name] == "show":
                upsert_show(conn, media_root, name, api_key)
            elif not path_unchanged(seen.get(name), os.path.join(media_root, name)):
                upsert_movie(conn, media_root, name, api_key)
        except Exception:
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            if kinds[name] == "show":
                # shows carry no status column (it's a computed aggregate over
                # episodes) -- just make sure a row exists so it's visible; the
                # next scan retries upsert_show naturally
                conn.execute(
                    "INSERT INTO shows (folder, updated_at) VALUES (?, ?) "
                    "ON CONFLICT(folder) DO UPDATE SET updated_at=excluded.updated_at",
                    (name, now),
                )
            else:
                conn.execute(
                    "INSERT INTO movies (folder, status, updated_at) VALUES (?, 'error', ?) "
                    "ON CONFLICT(folder) DO UPDATE SET status='error', updated_at=excluded.updated_at",
                    (name, now),
                )
            conn.commit()
        if progress_cb:
            progress_cb(i, total, name)
    return total


def scan_shows_root(conn, shows_root, api_key, progress_cb=None):
    """Scans a dedicated TV-shows root (MM_SHOWS_ROOT), separate from
    MEDIA_ROOT's movies. Every top-level folder here is expected to be a
    show; classify_folder is still consulted as a safety net so a stray
    non-show folder is skipped rather than miscreated as an empty show."""
    entries = sorted(e.name for e in os.scandir(shows_root) if e.is_dir() and not e.name.startswith("."))
    on_disk = set(entries)
    for r in conn.execute("SELECT id, folder FROM shows").fetchall():
        if r["folder"] not in on_disk:
            conn.execute("DELETE FROM shows WHERE id=?", (r["id"],))
    conn.commit()

    total = len(entries)
    for i, name in enumerate(entries, start=1):
        try:
            if classify_folder(os.path.join(shows_root, name)) == "show":
                upsert_show(conn, shows_root, name, api_key)
        except Exception:
            conn.execute(
                "INSERT INTO shows (folder, updated_at) VALUES (?, ?) "
                "ON CONFLICT(folder) DO UPDATE SET updated_at=excluded.updated_at",
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
            "Movie (2016)-poster.jpg", "Movie (2016).nfo",          # live artwork/nfo — keep
            "Movie (2016).remux-poster.jpg", "Old Cut.nfo",          # orphans — junk
            "www.YTS.MX.jpg", "YIFYStatus.com.txt", "RARBG.txt", "._Movie (2016)",
            "2 Fast 2 Furious (2003) 1080p BluRay REMUX [wWw.PelisMKVHD.Com]-logo.png",
            "2 Fast 2 Furious (2003) 1080p BluRay REMUX [wWw.PelisMKVHD.Com]-backdrop.jpg",
        ]
        for n in names:
            open(os.path.join(d, n), "w").close()
        junk = set(find_movie_junk(d, {"Movie (2016).mkv", "Movie.eng.srt"}))
        assert junk == {"www.YTS.MX.jpg", "YIFYStatus.com.txt", "RARBG.txt", "._Movie (2016)",
                        "Movie (2016).remux-poster.jpg", "Old Cut.nfo",
                        "2 Fast 2 Furious (2003) 1080p BluRay REMUX [wWw.PelisMKVHD.Com]-logo.png",
                        "2 Fast 2 Furious (2003) 1080p BluRay REMUX [wWw.PelisMKVHD.Com]-backdrop.jpg"}, junk

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
    assert got[2]["keep"] == 1 and got[2]["out_default"] == 0 and got[2]["out_order"] == 2, \
        "TrueHD kept, never default, after the compatible picks"
    assert got[3]["keep"] == 1 and got[3]["out_default"] == 1 and got[3]["out_order"] == 0
    assert got[4]["keep"] == 1 and got[4]["out_default"] == 0 and got[4]["out_order"] == 1
    assert got[5]["keep"] == 1 and got[5]["out_forced"] == 1 and got[5]["out_default"] == 1 and got[5]["out_order"] == 0
    assert got[6]["keep"] == 1 and got[6]["out_order"] == 1
    assert got[7]["keep"] == 0, "duplicate PGS must stay unchecked"
    assert got[8]["keep"] == 1 and got[8]["out_order"] == 2

    # multi_audio=True (TV): both eng audio tracks survive, TrueHD still never default
    c2 = _sq.connect(":memory:")
    c2.row_factory = _sq.Row
    c2.executescript("""
      CREATE TABLE shows (id INTEGER PRIMARY KEY, original_language TEXT);
      CREATE TABLE episodes (id INTEGER PRIMARY KEY, show_id INT, original_language TEXT,
        status TEXT, updated_at TEXT);
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, episode_id INT, mkv_id INT, type TEXT, codec TEXT,
        lang TEXT, name TEXT, channels INT, default_flag INT DEFAULT 0, forced_flag INT DEFAULT 0,
        ext_path TEXT, keep INT DEFAULT 1, out_order INT DEFAULT 0, out_lang TEXT DEFAULT '',
        out_default INT DEFAULT 0, out_forced INT DEFAULT 0, out_name TEXT DEFAULT '');
      INSERT INTO shows VALUES (1, 'en');
      INSERT INTO episodes (id, show_id, status, updated_at) VALUES (1, 1, 'unprocessed', NULL);
      INSERT INTO tracks (id, episode_id, mkv_id, type, codec, lang, forced_flag) VALUES
        (1,1,0,'video','HEVC','und',0),
        (2,1,1,'audio','TrueHD Atmos','eng',0),
        (3,1,2,'audio','DTS-HD Master Audio','eng',0),
        (4,1,3,'audio','AAC','eng',0);
    """)
    suggest_tracks(c2, 1, table="episodes", multi_audio=True)
    got2 = {r["id"]: dict(r) for r in c2.execute("SELECT * FROM tracks")}
    assert got2[3]["keep"] == 1 and got2[3]["out_default"] == 1, "best compatible eng audio, default"
    assert got2[4]["keep"] == 1 and got2[4]["out_default"] == 0, "second eng dub source also kept"
    assert got2[2]["keep"] == 1 and got2[2]["out_default"] == 0, "TrueHD kept too, never default"

    # classify_folder / parse_episode
    assert parse_episode("Show.S01E03.mkv") == (1, 3)
    assert parse_episode("show.s1e3.mkv") == (1, 3)
    assert parse_episode("show.1x03.mkv") == (1, 3)
    assert parse_episode("Show 2019 S02E10.mkv") == (2, 10)
    assert parse_episode("Show 2019.mkv") is None, "bare year must not parse as an episode"

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "show_seasondir", "Season 01"))
        open(os.path.join(d, "show_seasondir", "Season 01", "e1.mkv"), "w").close()
        assert classify_folder(os.path.join(d, "show_seasondir")) == "show"

        os.makedirs(os.path.join(d, "show_flat"))
        open(os.path.join(d, "show_flat", "Show.S01E01.mkv"), "w").close()
        open(os.path.join(d, "show_flat", "Show.S01E02.mkv"), "w").close()
        assert classify_folder(os.path.join(d, "show_flat")) == "show"

        os.makedirs(os.path.join(d, "movie_dir"))
        open(os.path.join(d, "movie_dir", "Movie (2016).mkv"), "w").close()
        assert classify_folder(os.path.join(d, "movie_dir")) == "movie"

        # a single stray SxxExx-looking file is not enough to call it a show
        os.makedirs(os.path.join(d, "movie_one_ep_like"))
        open(os.path.join(d, "movie_one_ep_like", "Movie.S01E01.mkv"), "w").close()
        assert classify_folder(os.path.join(d, "movie_one_ep_like")) == "movie"

    # Latino vs Castellano Spanish: differentiated by track name, one of each kept,
    # out_lang written back as plain "spa" (not a real ISO code with the suffix)
    assert _spanish_variant("spa", "Spanish (Latino)") == "spa-mx"
    assert _spanish_variant("spa", "Español Latino 5.1") == "spa-mx"
    assert _spanish_variant("spa", "Castellano") == "spa-es"
    assert _spanish_variant("spa", "Spanish (Spain)") == "spa-es"
    assert _spanish_variant("spa", "") == "spa", "no name -- can't tell, stays bare"
    assert _spanish_variant("eng", "Latino") == "eng", "non-spanish langs pass through untouched"
    assert _spanish_base("spa-mx") == "spa" and _spanish_base("spa") == "spa"

    c3 = _sq.connect(":memory:")
    c3.row_factory = _sq.Row
    c3.executescript("""
      CREATE TABLE movies (id INTEGER PRIMARY KEY, original_language TEXT, status TEXT, updated_at TEXT);
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, mkv_id INT, type TEXT, codec TEXT,
        lang TEXT, name TEXT, channels INT, default_flag INT DEFAULT 0, forced_flag INT DEFAULT 0,
        ext_path TEXT, keep INT DEFAULT 1, out_order INT DEFAULT 0, out_lang TEXT DEFAULT '',
        out_default INT DEFAULT 0, out_forced INT DEFAULT 0, out_name TEXT DEFAULT '');
      INSERT INTO movies VALUES (1, 'en', 'unprocessed', NULL);
      INSERT INTO tracks (id, movie_id, mkv_id, type, codec, lang, name, forced_flag) VALUES
        (1,1,0,'video','HEVC','und','',0),
        (2,1,1,'audio','AC-3','eng','',0),
        (3,1,2,'audio','AC-3','spa-mx','Latino',0),
        (4,1,3,'audio','AC-3','spa-es','Castellano',0);
    """)
    suggest_tracks(c3, 1)
    got3 = {r["id"]: dict(r) for r in c3.execute("SELECT * FROM tracks")}
    assert got3[3]["keep"] == 1 and got3[3]["out_lang"] == "spa", "Latino kept, out_lang normalized to real ISO code"
    assert got3[4]["keep"] == 1 and got3[4]["out_lang"] == "spa", "Castellano kept too -- not collapsed into one spa slot"
    assert got3[3]["out_order"] != got3[4]["out_order"], "distinct slots, not overwriting each other"

    print("scan.py self-check OK")
