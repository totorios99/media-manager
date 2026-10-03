"""Latino or castellano? Decided from the TEXT of every embedded Spanish subtitle with no evidence in its tags.

    subs_variant_pass.py                # measure (resumable); writes subs_variant_pass.json
    (applying the verdicts as language-ietf tags is a separate step, after the report has been read)

Scope: embedded Spanish text tracks (SRT/ASS) whose variant is a bare 'spa' (LIBRARY_STATE.md,
"Distinguir latino de castellano"). Image tracks (PGS) need the OCR step; audio would need speech
recognition: neither is judged here.

The rule is the one already written down: STRUCTURAL markers decide, because they are grammar and not word
choice -- vosotros forms (habéis, sois, mirad), the pronoun `os` (os he dicho), `coger`, leísmo (le vi). A
Spanish translator cannot avoid them even when writing neutral; a Latin American one never produces them.
Vocabulary only corroborates (a 'papa' also matches "el Papa"; `piso`/`pasta` exist on both sides).

  castellano  >= 3 STRONG markers, or 1-2 strong plus >= 3 castellano-only words or >= 3 weak markers
              (weak = coger, leísmo: they never decide alone)
  latino      0 structural markers over >= 3000 words (American Psycho: 0 of 4 in 1325 lines settled it)
  unknown     anything else (too little text, or a handful of markers that could be a quotation)

Episodes: a season's tracks that share (codec, name, position) almost always share one translation, so 3 episodes
per group are measured and the rest inherit; a disagreement measures the whole group. Movies are measured one by
one. mkvextract reads the whole file (Matroska has no per-track index), hence idle I/O priority, small files first.
"""
import collections, json, os, re, sqlite3, subprocess, sys, tempfile

import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "subs_variant_pass.json")
TEXT_CODEC = re.compile(r"srt|subrip|substation|ass|text", re.I)

CAST_STRUCT = re.compile(          # strong: grammar a Latin American translator does not produce
    r"\b(?:vosotros|vosotras|vuestr[oa]s?|sois)\b"
    r"|\b(?:ten|hab|quer|pod|sab|ver|ser|est|dec|hac|tra|sal|vol|dej|ven|segu)(?:éis|áis)\b"
    r"|\b(?:mirad|escuchad|venid|esperad|callaos|sentaos|dejad|volved|salid|tomad|dadme|decidme|pasad|entrad|seguid|vamonos)\b"
    r"|\bos (?:he|ha|han|hemos|voy|vais|lo|la|los|las|dije|digo|quiero|pido|ruego|aviso|prometo|juro)\b", re.I)
CAST_WEAK = re.compile(            # weak: `coger` is vulgar in Mexico and constant in crime/comedy dialogue (Club de
    r"\bcog(?:er|e|í|ió|eré|emos|en|ido|ía)\b"       # Cuervos, The Sopranos gave 15 and 8 false castellano tracks)
    r"|\ble (?:vi|vimos|conocí|conozco|veo|vio|llamé)\b", re.I)
CAST_LEX = re.compile(r"\b(?:ordenador(?:es)?|coches?|piso|alquil(?:ar|o|a)|joder|coño|gilipollas|hostia|chaval(?:es)?|"
                      r"currar|guay|tronco|tío|tía|mola|vale)\b", re.I)
LAT_LEX = re.compile(r"\b(?:ustedes|celular(?:es)?|computadoras?|departamento|rent(?:ar|a|ó)|carros?|platicar|plática|"
                     r"chavos?|güey|órale|ahorita|mande|ándale|híjole|chido|padrísimo)\b", re.I)


def plain_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            txt = raw.decode("utf-8", "replace")
    if path.endswith(".ass") or path.endswith(".ssa"):
        lines = [ln.split(",", 9)[9] for ln in txt.splitlines() if ln.startswith("Dialogue:") and ln.count(",") >= 9]
        txt = "\n".join(re.sub(r"\{[^}]*\}", "", ln).replace("\\N", " ") for ln in lines)
    else:
        txt = "\n".join(ln for ln in txt.splitlines() if ln.strip() and not ln.strip().isdigit() and "-->" not in ln)
    return re.sub(r"<[^>]+>", "", txt)


def verdict(txt):
    words = len(re.findall(r"\w+", txt))
    cs, cl, ll = len(CAST_STRUCT.findall(txt)), len(CAST_LEX.findall(txt)), len(LAT_LEX.findall(txt))
    cw = len(CAST_WEAK.findall(txt))
    if cs >= 3 or (cs >= 1 and (cl >= 3 or cw >= 3)):
        v = "castellano"
    elif cs == 0 and words >= 3000:
        v = "latino"
    else:
        v = "unknown"
    return {"verdict": v, "words": words, "struct": cs, "weak": cw, "cast_lex": cl, "lat_lex": ll,
            "examples": [m.group(0).lower() for m in CAST_STRUCT.finditer(txt)][:4]}


def candidates(conn):
    """[(kind, id, group, path, size, [(mkv_id, name, codec)])]: one entry per file with such tracks."""
    out = {}
    q = ("SELECT t.movie_id mid, t.episode_id eid, t.mkv_id, t.name, t.codec, t.forced_flag FROM tracks t "
         "WHERE t.type='subtitle' AND t.lang='spa' AND t.ext_path IS NULL")
    for r in conn.execute(q):
        if not TEXT_CODEC.search(r["codec"] or "") or "pgs" in (r["codec"] or "").lower():
            continue
        key = ("movie", r["mid"]) if r["mid"] else ("episode", r["eid"])
        out.setdefault(key, []).append((r["mkv_id"], r["name"] or "", r["codec"], r["forced_flag"]))
    rows = []
    for (kind, id_), tr in out.items():
        if kind == "movie":
            m = conn.execute("SELECT folder, file, size_bytes FROM movies WHERE id=?", (id_,)).fetchone()
            if not m or not m["file"]:                 # orphan track rows from a replaced or deleted title
                continue
            path, group, size = os.path.join(la.MOVIES, m["folder"], m["file"]), ("movie", id_), m["size_bytes"]
        else:
            e = conn.execute("SELECT s.id sid, s.folder sf, e.folder ef, e.file, e.season, e.size_bytes FROM episodes e "
                             "JOIN shows s ON s.id=e.show_id WHERE e.id=?", (id_,)).fetchone()
            if not e or not e["file"]:
                continue
            path = os.path.join(la.SHOWS, e["sf"], e["ef"], e["file"])
            sig = tuple((n, c) for _, n, c, _ in sorted(tr))
            group, size = ("ep", e["sid"], e["season"], sig), e["size_bytes"]
        rows.append((kind, id_, group, path, size or 0, sorted(tr)))
    return rows


def measure(path, tracks):
    """verdict per mkv track id; one mkvextract pass over the whole file."""
    with tempfile.TemporaryDirectory(dir="/var/tmp") as d:
        spec = [f"{mid}:{os.path.join(d, str(mid))}.{'ass' if 'ass' in c.lower() or 'substation' in c.lower() else 'srt'}"
                for mid, _, c, _ in tracks]
        subprocess.run(["ionice", "-c3", "nice", "-n", "15", "mkvextract", path, "tracks", *spec],
                       capture_output=True, text=True)
        res = {}
        for (mid, name, c, forced), sp in zip(tracks, spec):
            p = sp.split(":", 1)[1]
            res[mid] = {**verdict(plain_text(p)), "name": name, "forced_flag": forced} if os.path.exists(p) else {"error": "extract"}
        return res


def main():
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    conn.row_factory = sqlite3.Row
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    rows = candidates(conn)
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r[2]].append(r)
    todo = []                                              # (priority, size, row, inherits_from_group)
    for g, members in groups.items():
        members.sort(key=lambda r: r[0] + str(r[1]))
        if g[0] == "movie":
            todo += [(1, r[4], r) for r in members]
        else:
            n = len(members)
            pick = {0, n // 2, n - 1}                    # first, middle, last (all of them when n <= 3)
            todo += [(0, r[4], r) for i, r in enumerate(members) if i in pick]
    todo.sort(key=lambda x: (x[0], x[1]))
    print(len(rows), "files,", len(todo), "to measure first (episodes sampled 3 per group; movies one by one)", flush=True)
    for _, size, (kind, id_, group, path, sz, tracks) in todo:
        st = os.stat(path) if os.path.exists(path) else None
        key = f"{path}|{st.st_mtime_ns if st else 0}|{st.st_size if st else 0}"
        if key in done or not st:
            continue
        done[key] = {"kind": kind, "id": id_, "path": path, "tracks": measure(path, tracks)}
        json.dump(done, open(OUT, "w"), ensure_ascii=False)
        print(kind, os.path.basename(path)[:55], {m: t.get("verdict") for m, t in done[key]["tracks"].items()}, flush=True)
    # groups whose sampled episodes disagree: measure the rest
    for g, members in groups.items():
        if g[0] == "movie":
            continue
        seen = collections.defaultdict(set)
        for r in members:
            for k, v in done.items():
                if v["path"] == r[3]:
                    for mid, t in v["tracks"].items():
                        seen[mid].add(t.get("verdict"))
        if any(len(vs - {"unknown"}) > 1 for vs in seen.values()):
            print("group disagrees, measuring all:", g[1:3], flush=True)
            for kind, id_, _, path, sz, tracks in members:
                st = os.stat(path)
                key = f"{path}|{st.st_mtime_ns}|{st.st_size}"
                if key not in done:
                    done[key] = {"kind": kind, "id": id_, "path": path, "tracks": measure(path, tracks)}
                    json.dump(done, open(OUT, "w"), ensure_ascii=False)
    summary(done)


def effective(t):
    """The verdict to trust. Results measured before the strong/weak split carry no `weak` field: a castellano
    verdict whose sampled evidence is only the coger family cannot be told from a Mexican translation."""
    if t.get("verdict") == "castellano" and "weak" not in t:
        if t["examples"] and all(re.match(r"cog", e) for e in t["examples"]):
            return "review"
    return t.get("verdict", "error")


def summary(done):
    c = collections.Counter()
    for v in done.values():
        for t in v["tracks"].values():
            c[(v["kind"], effective(t))] += 1
    print("== verdicts:", dict(c))


if __name__ == "__main__":
    if "--apply" in sys.argv:
        sys.exit("apply is a separate, reviewed step: see the report first")
    main()
