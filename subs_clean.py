"""Subtitle intake filter, SUBTITLES-PLAN.md "Filtro de entrada": ads and sync.

    subs_clean.py sweep [ROOT ...]   # read-only: which .srt carry provider ads, and where

Library functions, used before an SRT enters a remux:
    parse / render      SRT text <-> [(start, end, text)]
    strip_ads           drop provider cues (subtitulos.es "DIFUNDE LA PALABRA", ...)
    shift / scale       fix a constant offset / a standard framerate mismatch
    measure             ffsubsync --gss against the media: (offset_s, factor)

Order matters: strip ads first. An ad cue is fake dialogue and skews the measurement.
Nothing here writes next to the original; callers decide where the result goes.
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3}) --> (\d+):(\d\d):(\d\d)[,.](\d{3})")

# Provider names are safe anywhere: dialogue never says them. Mr. Robot's
# subtitulos.es card sits at 06:22, not at the edges, so position cannot be the test.
STRONG = re.compile(r"subtitulos\.es|difunde la palabra|opensubtitles|subdivx|argenteam|"
                    r"addic7ed|tusubtitulo|subscene|podnapisi", re.I)
# Credits lines that could be real dialogue: only at the very start or end of the file.
WEAK = re.compile(r"traducid[oa] por|sincronizad[oa] por|subtitulad[oa] por|"
                  r"sync(?:hronized)? (?:and corrected )?by|\bresync\b", re.I)
EDGE_S = 90


def decode(raw):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def _sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse(text):
    """[(start, end, text)] in file order. Cue numbers are dropped: render renumbers."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for i, ln in enumerate(lines):
            m = TS.search(ln)
            if m:
                cues.append((_sec(*m.groups()[:4]), _sec(*m.groups()[4:]),
                             "\n".join(lines[i + 1:]).strip()))
                break
    return cues


def _fmt(t):
    t = max(t, 0.0)
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def render(cues):
    return "".join(f"{n}\n{_fmt(a)} --> {_fmt(b)}\n{t}\n\n" for n, (a, b, t) in enumerate(cues, 1))


def strip_ads(cues):
    """-> (kept, removed). A cue loses only the lines that match; if dialogue is left in
    the same cue it stays. removed = [(start, line)] so the caller can log what went."""
    end = cues[-1][1] if cues else 0
    kept, removed = [], []
    for a, b, text in cues:
        edge = a < EDGE_S or a > end - EDGE_S
        # a credit block spans lines ("Subtitulada por:" / "Gelula/SDI"): the name
        # would survive a line filter, so at the edges the whole cue goes
        if edge and WEAK.search(text):
            removed.extend((a, ln.strip()) for ln in text.split("\n"))
            continue
        keep = []
        for ln in text.split("\n"):
            if STRONG.search(ln) or (edge and WEAK.search(ln)):
                removed.append((a, ln.strip()))
            else:
                keep.append(ln)
        # the ad's companion line ("-DIFUNDE LA PALABRA-") matches too; bare dashes do not
        if any(l.strip(" -–—") for l in keep):
            kept.append((a, b, "\n".join(keep).strip()))
    return kept, removed


def shift(cues, offset):
    return [(a + offset, b + offset, t) for a, b, t in cues]


def scale(cues, factor):
    return [(a * factor, b * factor, t) for a, b, t in cues]


def measure(media, srt):
    """ffsubsync --gss -> (offset_s, factor). `uvx`, not a dependency: it is a measuring
    tool, and its output file is discarded. Detecting speech by audio energy by hand
    does not work (The Hunt: four windows at the search limit, flipping sign)."""
    out = subprocess.run(["nice", "-n", "10", "uvx", "ffsubsync", media, "-i", srt, "-o", os.devnull, "--gss"],
                         capture_output=True, text=True, timeout=4 * 3600)  # a 30 GB film is ~14 min of USB 2.0 per pass, and there are up to three passes
    log = out.stdout + out.stderr
    off = re.findall(r"offset seconds: (-?[\d.]+)", log)
    fac = re.findall(r"scale factor: (-?[\d.]+)", log)
    if not off or not fac:
        raise RuntimeError("ffsubsync gave no result: " + log[-300:])
    return float(off[-1]), float(fac[-1])


# Framerate pairs that explain a pure speed mismatch (The Hunt: PAL 25/24). Anything
# else is another cut of the film, and rescaling it would only hide that.
STANDARD = [24 / 23.976, 25 / 24, 25 / 23.976]
STANDARD += [1 / f for f in STANDARD]
OFF_OK, FAC_OK = 0.1, 0.0005


def _settled(off, fac):
    return abs(off) <= OFF_OK and abs(fac - 1) <= FAC_OK


def fix(media, srt, measure_fn=None):
    """Clean ads, then bring the timing right. Never writes.

    -> {"status": "ok"|"rejected", "text": str|None, "ads": n, "action": str,
        "offset": float, "factor": float}   (offset/factor = the FINAL re-measure)
    "ok" means the result was re-measured and came out at factor 1.000, offset ~0:
    the gate in SUBTITLES-PLAN.md, not just "we applied a correction"."""
    import tempfile
    measure_fn = measure_fn or measure
    kept, removed = strip_ads(parse(decode(open(srt, "rb").read())))

    def trial(cues):
        with tempfile.NamedTemporaryFile("w", suffix=".srt", dir="/var/tmp", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(render(cues))
        try:
            return measure_fn(media, fh.name)
        finally:
            os.unlink(fh.name)

    off, fac = trial(kept)
    res = lambda status, cues, action, o, f: {"status": status, "ads": len(removed), "action": action,
        "offset": o, "factor": f, "text": render(cues) if status == "ok" else None}
    if _settled(off, fac):
        return res("ok", kept, "none", off, fac)
    if abs(fac - 1) <= FAC_OK:                       # constant offset: the same in every window
        action, fixed = f"shift {off:+.3f}s", shift(kept, off)
    elif any(abs(fac - s) <= 0.0005 for s in STANDARD):
        action, fixed = f"scale {fac:.4f} + shift", scale(kept, fac)
        o2, f2 = trial(fixed)
        fixed = shift(fixed, o2)
    else:
        return res("rejected", kept, f"non-standard speed {fac:.4f}: another cut?", off, fac)
    o3, f3 = trial(fixed)
    return res("ok" if _settled(o3, f3) else "rejected", fixed, action, o3, f3)


def sweep(roots):
    """Read-only. One record per .srt that has provider cues; never prints dialogue."""
    hits = []
    for root in roots:
        for dp, _, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".srt"):
                    continue
                p = os.path.join(dp, f)
                try:
                    cues = parse(decode(open(p, "rb").read()))
                except OSError:
                    continue
                kept, removed = strip_ads(cues)
                if removed:
                    hits.append({"path": p, "cues": len(cues), "removed": len(removed),
                                 "at": [f"{_fmt(a)[:8]} {ln[:40]}" for a, ln in removed]})
    return hits


if __name__ == "__main__":
    if sys.argv[1:2] != ["sweep"]:
        sys.exit(__doc__)
    roots = sys.argv[2:] or [os.environ.get("MEDIA_ROOT", "/srv/storage/Movies"),
                             os.environ.get("MM_SHOWS_ROOT", "/srv/storage/Shows")]
    hits = sweep(roots)
    json.dump(hits, open(os.path.join(HERE, "subs_clean_sweep.json"), "w"), ensure_ascii=False, indent=1)
    for h in hits:
        print(f"{h['removed']:2d} cue(s)  {h['path']}")
        for a in h["at"]:
            print(f"      {a}")
    print(f"{len(hits)} .srt with provider ads")
