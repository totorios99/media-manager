"""Re-OCR the disc subtitles ocr_pass dropped after a failed gate, from the .sup it kept in .recycle.

    ocr_recover.py [--run]

Reads ocr_review.jsonl (entries with a "sup" path). Same steps as ocr_pass.plan_file: pgs_bright, pgsrip, clean_srt,
the OOV gate (now ignoring repeated proper names), and for Spanish the text verdict (castellano stays dropped unless
the show's original language is Spanish). A track that passes becomes a sidecar next to the video with an "[subs] ok"
line in server.log, so the next F6 embeds it with its usual verification. Logs ocr_recover.jsonl; a path+track that
is already ok is skipped.
"""
import json, os, shutil, subprocess, sys

import ocr_pass as opass
import ocr_proto as op
import pgs_bright
import subs_variant_pass as vp

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "ocr_recover.jsonl")
WORK = "/var/tmp/ocr_recover"
SUFFIX = {"spa": ".es-MX.srt", "eng": ".en.srt"}


def pending(review, logged):
    """Review rows with a kept .sup that were never tried: the same .sup gives the same OCR, so a failed or castellano
    verdict is final too (Kill Bill Vol. 1 ES was re-OCR'd six times with the same 37 %)."""
    done = {(r["path"], r["track"]) for r in logged}
    return [r for r in review if r.get("sup", "").endswith(".sup") and r["lang"] in SUFFIX
            and (r["path"], r["track"]) not in done]


def main(run):
    logged = list(map(json.loads, open(LOG))) if os.path.exists(LOG) else []
    todo = [r for r in pending(map(json.loads, open(opass.REVIEW)), logged) if os.path.exists(r["sup"])]
    print(len(todo), "recyclable tracks", flush=True)
    vocs = {"es": op.vocab("es"), "en": op.vocab("en")} if run else {}
    os.makedirs(WORK, exist_ok=True)
    for r in todo:
        lang2 = opass.LANG2[r["lang"]]
        side = os.path.splitext(r["path"])[0] + SUFFIX[r["lang"]]
        if not run or os.path.exists(side):
            print("skip" if os.path.exists(side) else "plan", os.path.basename(r["path"]), r["lang"], flush=True)
            continue
        sup = os.path.join(WORK, f"{abs(hash(r['path']))}.{r['track']}.{lang2}.sup")
        open(sup, "wb").write(pgs_bright.brighten(open(r["sup"], "rb").read()))
        subprocess.run(["uvx", "pgsrip", "rip", "-l", lang2, "-f", sup], capture_output=True, text=True)
        srt = sup[:-4] + ".srt"
        n = opass.clean_srt(srt) if os.path.exists(srt) and os.path.getsize(srt) else 0
        rate = op.oov(srt, vocs[lang2])[0] * 100 if n else 100.0
        status, why = "ok", f"{n} cues, OOV {rate:.1f} %"
        if n < opass.MIN_CUES or rate > opass.OOV_MAX:
            status = "failed"
        elif r["lang"] == "spa" and vp.verdict(vp.plain_text(srt))["verdict"] == "castellano":
            status, why = "castellano", why + "; castellano stays dropped (D5)"
        if status == "ok":
            shutil.copyfile(srt, side)
            open(os.path.join(HERE, "server.log"), "a").write(
                f"[subs] {os.path.basename(side)}: ok OCR recovered from the disc .sup ({why})\n")
        open(LOG, "a").write(json.dumps({"path": r["path"], "track": r["track"], "lang": r["lang"], "status": status,
                                         "why": why}, ensure_ascii=False) + "\n")
        print(status, os.path.basename(r["path"]), r["lang"], why, flush=True)


if __name__ == "__main__":
    main("--run" in sys.argv)
