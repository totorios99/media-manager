"""Phase 1 of SUBTITLES-PLAN.md: is each Spanish text subtitle complete, and Latino?

    subs_variant.py sidecars   # the .es*.srt files next to each movie (minutes)
    subs_variant.py embedded   # Spanish text tracks inside the mkv (reads whole files)

Only numbers leave this script: cue counts, coverage, marker counts. No dialogue.
Needs subs_inventory.json from phase 0. Writes subs_variant_<mode>.json.
"""
import collections, json, os, re, sqlite3, subprocess, sys, tempfile

ROOT = os.environ.get("MEDIA_ROOT", "/srv/storage/Movies")
HERE = os.path.dirname(os.path.abspath(__file__))
TEXT = {"SubRip/SRT", "SubStationAlpha", "WebVTT"}

# Strong markers only. 'vale' and 'conducir' are also Mexican and inflated Joker.
CAST = [r"\bvosotr[oa]s\b", r"\bsois\b", r"\b\w+(?:áis|éis)\b", r"\bordenador(?:es)?\b",
        r"\benfadad[oa]s?\b", r"\bgilipollas\b"]
LAT = [r"\bustedes\b", r"\bcomputadoras?\b", r"\bcelular(?:es)?\b", r"\benojad[oa]s?\b",
       r"\bcarros?\b", r"\bplatic\w+\b", r"\bahorita\b"]
TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3}) --> (\d+):(\d\d):(\d\d)[,.](\d{3})")


def decode(raw):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def analyse(text, duration):
    sec = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    cues = [(sec(*t[:4]), sec(*t[4:])) for t in TS.findall(text)]
    low = text.lower()
    cast = sum(len(re.findall(p, low)) for p in CAST)
    lat = sum(len(re.findall(p, low)) for p in LAT)
    span = (cues[-1][1] - cues[0][0]) if cues else 0
    coverage = span / duration if duration else None
    per_min = len(cues) / (duration / 60) if duration else None
    looks_forced = bool(cues) and (len(cues) < 150 or (per_min is not None and per_min < 1.5))
    variant = ("castellano" if cast >= 3 and cast > 2 * lat else
               "latino" if lat >= 3 and lat > 2 * cast else
               "latino?" if cast == 0 and len(cues) > 400 else "indeterminado")
    return {"cues": len(cues), "coverage": round(coverage, 2) if coverage else None,
            "cues_per_min": round(per_min, 1) if per_min else None,
            "looks_forced": looks_forced, "cast_markers": cast, "lat_markers": lat, "variant": variant}


def main(mode):
    inv = {o["folder"]: o for o in json.load(open(os.path.join(HERE, "subs_inventory.json")))}
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    dur = dict(conn.execute("select folder, duration from movies"))
    file_of = dict(conn.execute("select folder, file from movies"))
    out = []
    for folder, o in sorted(inv.items()):
        if "error" in o:
            continue
        if mode == "sidecars":
            items = [(s, os.path.join(ROOT, folder, s)) for s in o["sidecars"]
                     if s.lower().endswith(".srt") and re.search(r"\.es(-\w+)?\.", s.lower())]
            for name, p in items:
                rec = {"folder": folder, "source": name, **analyse(decode(open(p, "rb").read()), dur.get(folder))}
                out.append(rec)
                print(folder, name, rec["variant"], rec["cues"], "forced?" if rec["looks_forced"] else "", flush=True)
        else:
            tracks = [t for t in o["subs"] if t["lang"] == "spa" and t["codec"] in TEXT]
            if not tracks:
                continue
            with tempfile.TemporaryDirectory(dir="/var/tmp") as d:
                spec = [f"{t['id']}:{os.path.join(d, str(t['id']))}.srt" for t in tracks]
                r = subprocess.run(["ionice", "-c3", "mkvextract", os.path.join(ROOT, folder, file_of[folder]),
                                    "tracks", *spec], capture_output=True, text=True)
                for t in tracks:
                    p = os.path.join(d, f"{t['id']}.srt")
                    if not os.path.exists(p):
                        out.append({"folder": folder, "track": t["id"], "error": r.stderr.strip()[:200]})
                        continue
                    rec = {"folder": folder, "track": t["id"], "name": t["name"], "flag_forced": t["forced"],
                           **analyse(decode(open(p, "rb").read()), dur.get(folder))}
                    out.append(rec)
                    print(folder, t["id"], rec["variant"], rec["cues"], "forced?" if rec["looks_forced"] else "", flush=True)
    json.dump(out, open(os.path.join(HERE, f"subs_variant_{mode}.json"), "w"), indent=1, ensure_ascii=False)
    ok = [x for x in out if "error" not in x]
    full = [x for x in ok if not x.get("flag_forced")]
    print(f"\n== {mode}: {len(ok)} pistas, {len(out) - len(ok)} errores")
    print("  variante (sin contar los forzados marcados):", dict(collections.Counter(x["variant"] for x in full)))
    print("  parecen forzados sin estar marcados:", sum(x["looks_forced"] for x in full))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sidecars")
