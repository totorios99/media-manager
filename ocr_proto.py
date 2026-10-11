"""OCR prototype (P3, 2026-10-10): PGS -> SRT with pgsrip on a few movies, timed, with a quality gate.

    ocr_proto.py "<movie folder>" ...      # read-only on the library; work happens in /var/tmp/ocr

Per Spanish (or English) PGS track: mkvextract to .sup, `uvx pgsrip rip -l es|en`, then
  OOV rate  = share of word tokens not in a vocabulary built from the library's own Spanish/English SRTs
              (good human subtitles measured 3.9-7.3 % against it in the plan),
  variant   = subs_variant_pass.verdict on the text (latino / castellano / unknown) for bare 'spa' tracks.
PGS timing is the disc's own, so no sync step is needed. Results go to ocr_proto.jsonl.
"""
import collections, glob, json, os, re, subprocess, sys, time

import library_audit as la
import subs_clean as sc
import subs_variant_pass as vp

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = "/var/tmp/ocr"
WORD = re.compile(r"[a-záéíóúüñ]+", re.I)


def vocab(lang):
    """Words seen at least twice across the library's sidecar SRTs (current, .orig and recycled copies)."""
    pat = "*.es-MX*.srt*" if lang == "es" else "*.en*.srt*"
    files = glob.glob(f"/srv/storage/Movies/*/{pat}") + glob.glob(f"/srv/storage/.recycle/Movies/*/{pat}")
    c = collections.Counter()
    for f in files[:400]:
        try:
            c.update(w.lower() for w in WORD.findall(sc.decode(open(f, "rb").read())))
        except OSError:
            pass
    return {w for w, n in c.items() if n >= 2}


def oov(srt, voc):
    words = [w.lower() for _, _, t in sc.parse(sc.decode(open(srt, "rb").read())) for w in WORD.findall(t)]
    return (sum(w not in voc for w in words) / len(words) if words else 1.0), len(words)


def main(folders):
    os.makedirs(WORK, exist_ok=True)
    vocs = {"es": vocab("es"), "en": vocab("en")}
    out = open(os.path.join(HERE, "ocr_proto.jsonl"), "a")
    for folder in folders:
        mkv = glob.glob(os.path.join(la.MOVIES, glob.escape(folder), "*.mkv"))[0]
        info = json.loads(subprocess.run(["mkvmerge", "-J", mkv], capture_output=True, text=True).stdout)
        for t in info["tracks"]:
            p = t["properties"]
            if t["type"] != "subtitles" or "PGS" not in t["codec"] or p.get("language") not in ("spa", "eng"):
                continue
            lang = "es" if p["language"] == "spa" else "en"
            base = os.path.join(WORK, f"{folder}.{t['id']}.{lang}")
            t0 = time.time()
            subprocess.run(["ionice", "-c3", "mkvextract", mkv, "tracks", f"{t['id']}:{base}.sup"], capture_output=True, check=True)
            t1 = time.time()
            r = subprocess.run(["uvx", "pgsrip", "rip", "-l", lang, "-f", base + ".sup"], capture_output=True, text=True)
            t2 = time.time()
            srt = next(iter(sorted(glob.glob(glob.escape(base) + "*.srt"))), None)
            rec = {"movie": folder, "track": t["id"], "lang": p.get("language_ietf") or lang,
                   "name": p.get("track_name"), "forced": bool(p.get("forced_track")),
                   "extract_s": round(t1 - t0), "ocr_s": round(t2 - t1), "srt": srt,
                   "rc": r.returncode, "err": None if srt else (r.stderr or r.stdout)[-300:]}
            if srt:
                rate, n = oov(srt, vocs[lang])
                rec.update(oov=round(rate * 100, 1), words=n, cues=len(sc.parse(sc.decode(open(srt, "rb").read()))))
                if lang == "es":
                    rec["variant"] = vp.verdict(vp.plain_text(srt))
            print(json.dumps(rec, ensure_ascii=False), flush=True)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
