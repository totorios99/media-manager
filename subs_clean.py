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
import json, os, re, statistics, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3}) --> (\d+):(\d\d):(\d\d)[,.](\d{3})")

# Provider names are safe anywhere: dialogue never says them. Mr. Robot's
# subtitulos.es card sits at 06:22, not at the edges, so position cannot be the test.
STRONG = re.compile(r"subtitulos\.es|difunde la palabra|opensubtitles|subdivx|argenteam|"
                    r"addic7ed|tusubtitulo|subscene|podnapisi|asia-team|subt[ií]tulos traducidos por|"
                    # English credit lines; never dialogue (Yellowstone S01E08: "- Synced & corrected by MementMori -")
                    r"synced (?:&|and) corrected by|sync(?:ed)? and corrections? by|subtitles? ripped by|"
                    r"subtitles? by\b.*(?:www|\.com|\.net|\.org|@)", re.I)
# A line that is little more than a web address or an e-mail is a credit (Prison Break E14 "OverCraft@gmail.com",
# E17 ".: Www.Asia-Team.Net :." at minute 7); "Mira www.google.com y dime." is dialogue and stays.
LINK = re.compile(r"\bwww\.[\w-]+\.\w+\S*|https?://\S+|\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I)


def _link_only(line):
    return bool(LINK.search(line)) and len(re.findall(r"\w+", LINK.sub(" ", line))) <= 2
# Credits lines that could be real dialogue: only at the very start or end of the file.
WEAK = re.compile(r"traducc?id[oa] por|sincronizad[oa] por|subtitulad[oa] por|subt[ií]tulos por|"   # Roman Holiday
                  r"edici[oó]n final por|corregid[oa] por|"                                         # Prison Break E14
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
            if STRONG.search(ln) or _link_only(ln) or (edge and WEAK.search(ln)):
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


WINDOW_S, WINDOW_OFF_OK, REFIT_MAX = 480, 0.3, 3.0   # REFIT_MAX: Prison Break E12/E21/E22 went to tens of seconds after refitting


def window_offsets(media, srt, measure_fn=None, n=3, starts=None, length=WINDOW_S):
    """Offset measured separately at the start, middle and end of the film (WINDOW_S each). A single global fit can
    average a wrong cold open into a right body: Prison Break E06 was 'factor 1.0000 / +0.00 s' overall and off before
    the intro. -> [(window_start_s, offset_s or None)]. Each window: its audio cut to a temp WAV, its cues re-based."""
    import tempfile
    measure_fn = measure_fn or measure
    cues = parse(decode(open(srt, "rb").read()))
    if not cues:
        return []
    end = cues[-1][1]
    starts = starts or [max(cues[0][0] - 30, 0), max(end / 2 - WINDOW_S / 2, 0), max(end - WINDOW_S, 0)][:n]
    out = []
    for a in starts:
        sub = [(x - a, y - a, t) for x, y, t in cues if a <= x < a + length]
        if len(sub) < 8:                      # too little dialogue to say anything
            out.append((a, None))
            continue
        fd, wav = tempfile.mkstemp(suffix=".wav", dir="/var/tmp")
        os.close(fd)
        fd2, s = tempfile.mkstemp(suffix=".srt", dir="/var/tmp")
        os.close(fd2)
        try:
            subprocess.run(["ionice", "-c3", "nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(a),
                            "-t", str(length), "-i", media, "-map", "0:a:0", "-ac", "1", "-ar", "16000", wav],
                           check=True, capture_output=True, timeout=3600)
            open(s, "w", encoding="utf-8").write(render(sub))
            off, _ = measure_fn(wav, s)
            out.append((a, off))
        except Exception:
            out.append((a, None))
        finally:
            for p in (wav, s):
                if os.path.exists(p):
                    os.unlink(p)
    return out


def refit_from_windows(srt, offsets):
    """Least-squares line through the per-window offsets (offset = b + s*t), applied as scale (1+s) then shift b.
    For a drift too small for the global measure to see: Prison Break E08 scaled 1.045 still went -0.57 s / +0.89 s /
    +1.43 s across the episode. Writes the file; returns (s, b) or None when fewer than two windows measured."""
    pts = [(a + WINDOW_S / 2, o) for a, o in offsets if o is not None]
    if len(pts) < 2 or any(abs(o) > REFIT_MAX for _, o in pts):
        return None                              # big window errors are another edit, not a drift: a line makes it worse
    n = len(pts)
    mx, my = sum(x for x, _ in pts) / n, sum(y for _, y in pts) / n
    s = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
    b = my - s * mx
    cues = parse(decode(open(srt, "rb").read()))
    open(srt, "w", encoding="utf-8").write(render(shift(scale(cues, 1 + s), b)))
    return s, b


def piecewise_from_windows(srt, offsets):
    """Shift every cue by the offset interpolated between the measured window centres (flat beyond the ends).
    For an edit that a single line cannot follow: Prison Break E06 sidecar +0.77 / -0.37 / -0.31 / -1.61 / -0.37 s.
    Writes the file; returns the (centre, offset) points used, or None when fewer than two windows measured."""
    pts = sorted((a + WINDOW_S / 2, o) for a, o in offsets if o is not None and abs(o) <= REFIT_MAX)
    if len(pts) < 2:
        return None

    def off(t):
        if t <= pts[0][0]:
            return pts[0][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if t <= x1:
                return y0 + (y1 - y0) * (t - x0) / (x1 - x0)
        return pts[-1][1]
    cues = parse(decode(open(srt, "rb").read()))
    open(srt, "w", encoding="utf-8").write(render([(a + off(a), b + off(a), t) for a, b, t in cues]))
    return pts


def segment_fit(srt, offsets, jump=1.0, max_off=15.0):
    """Two edits of different length (Netflix vs disc: The Seven Deadly Sins S01E18 was -4.9 s until the eyecatch at
    12.7 min and +1.0 s after it). When neighbouring windows disagree by more than `jump`, cut at the largest gap
    between cues inside that stretch and shift each side by its own windows' mean. One cut only; offsets beyond
    `max_off` are measurement noise (opening/ending songs). Writes the file; returns the cut (s) or None."""
    pts = sorted((a, o) for a, o in offsets if o is not None and abs(o) <= max_off)
    jumps = [(abs(o1 - o0), i) for i, ((a0, o0), (a1, o1)) in enumerate(zip(pts, pts[1:])) if abs(o1 - o0) > jump]
    if len(jumps) != 1 or len(pts) < 3:
        return None
    i = jumps[0][1]
    lo, hi = pts[i][0], pts[i + 1][0] + WINDOW_S
    cues = parse(decode(open(srt, "rb").read()))
    gaps = [(b2 - a1, a1 + (b2 - a1) / 2) for (_, a1, _), (b2, _, _) in zip(cues, cues[1:]) if lo <= a1 <= hi]
    if not gaps:
        return None
    cut = max(gaps)[1]
    before = sum(o for _, o in pts[:i + 1]) / (i + 1)
    after = sum(o for _, o in pts[i + 1:]) / (len(pts) - i - 1)
    open(srt, "w", encoding="utf-8").write(render([(a + (before if a < cut else after), b + (before if a < cut else after), t)
                                                   for a, b, t in cues]))
    return cut


NOISE_S = 30.0       # a window this far off is the opening/ending song or a dialogue-free stretch, not a sync error


REF_SPREAD = 0.3      # sub-to-sub window offsets this close together are one constant offset


def reference_offsets(srt, ref, starts, max_d=3.0):
    """Per window of `starts`: median of (nearest `ref` cue start - cue start) over the cues of `srt` in it, pairs
    further than max_d apart ignored. Two subs of the same cut in different languages share most cue starts."""
    import bisect
    cues = parse(decode(open(srt, "rb").read()))
    rs = sorted(c[0] for c in parse(decode(open(ref, "rb").read())))
    out = []
    for a in starts:
        d = []
        for s, _, _ in cues:
            if a <= s <= a + WINDOW_S and rs:
                i = bisect.bisect_left(rs, s)
                b = min((rs[j] - s for j in (i - 1, i) if 0 <= j < len(rs)), key=abs)
                if abs(b) <= max_d:
                    d.append(b)
        out.append((a, statistics.median(d) if len(d) >= 10 else None))
    return out


ALASS = os.path.expanduser("~/.local/bin/alass")


def dialogue_starts(srt, fractions=(0.1, 0.3, 0.5, 0.7, 0.9)):
    """Window starts spread over the dialogue span, not on the logos or the credits."""
    cues = parse(decode(open(srt, "rb").read()))
    if not cues:
        return []
    a, b = cues[0][0], cues[-1][1]
    return [max(a + f * (b - a) - WINDOW_S / 2, 0) for f in fractions]


def repair_by_reference(srt, ref, starts=None):
    """Verify `srt` against `ref`, a subtitle already timed to the same video (usually the embedded track of another
    language), and repair it in place: one coherent offset -> shift; anything else (another frame rate, a cut) ->
    alass to the reference, kept only if every window then agrees within WINDOW_OFF_OK. The file is left as it was
    on failure. -> (ok, how, windows)."""
    starts = starts or dialogue_starts(srt)
    original = open(srt, "rb").read()
    good = lambda w: len([o for _, o in w if o is not None]) >= 3 and all(
        abs(o) <= WINDOW_OFF_OK for _, o in w if o is not None)
    r = reference_offsets(srt, ref, starts)
    vals = [o for _, o in r if o is not None]
    if good(r):
        return True, "reference", r
    if len(vals) >= 3 and max(vals) - min(vals) <= REF_SPREAD:
        open(srt, "w", encoding="utf-8").write(render(shift(parse(decode(original)), statistics.median(vals))))
        return True, "reference shift", reference_offsets(srt, ref, starts)
    out = srt + ".alass.srt"
    try:
        p = subprocess.run([ALASS, ref, srt, out], capture_output=True, timeout=1800)
        if p.returncode == 0 and os.path.exists(out):
            w = reference_offsets(out, ref, starts)
            if good(w):
                open(srt, "wb").write(open(out, "rb").read())
                return True, "reference alass", w
    finally:
        if os.path.exists(out):
            os.remove(out)
    open(srt, "wb").write(original)
    return False, "reference off", r


def settle_windows(media, srt, measure_fn=None, starts=None, windows_fn=None, reference_fn=None):
    """Verify `srt` by windows and repair it in place when a global fit was not enough (The Good, the Bad and the
    Ugly: global +1.24 s, windows -1.4 s). Windows beyond NOISE_S are ignored. Tries a piecewise fit (up to three
    passes) and then a two-segment cut; when those fail, `reference_fn()` (lazy: it may extract a track) can give
    an SRT already timed to the video to measure against. -> (ok, how, windows); ok needs 3+ trusted windows, all
    within WINDOW_OFF_OK."""
    if starts is None:
        # five windows inside the dialogue span: the default start/middle/end put one on the studio logos and one on
        # the credits (Avengers Infinity War ES: +19.16 s and +1.39 s there, -0.27 s in the middle)
        cues = parse(decode(open(srt, "rb").read()))
        if cues:
            a, b = cues[0][0], cues[-1][1]
            starts = [max(a + f * (b - a) - WINDOW_S / 2, 0) for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    windows_fn = windows_fn or (lambda m, f: window_offsets(m, f, measure_fn, n=5, starts=starts))
    trusted = lambda w: [(a, o) for a, o in w if o is not None and abs(o) <= NOISE_S]
    good = lambda w: len(trusted(w)) >= 3 and all(abs(o) <= WINDOW_OFF_OK for _, o in trusted(w))
    w = windows_fn(media, srt)
    if good(w):
        return True, "windows", w
    if len(trusted(w)) < 3:
        return True, "unmeasured", w        # too little dialogue to judge: the global fit stands, as before
    original = open(srt, "rb").read()
    for _ in range(3):
        if piecewise_from_windows(srt, trusted(w)) is None:
            break
        w = windows_fn(media, srt)
        if good(w):
            return True, "piecewise", w
    open(srt, "wb").write(original)
    w0 = windows_fn(media, srt)
    if segment_fit(srt, trusted(w0)) is not None:
        w = windows_fn(media, srt)
        if good(w):
            return True, "segments", w
    open(srt, "wb").write(original)
    ref = reference_fn() if reference_fn else None
    if ref:
        # audio windows failed: measure against a subtitle already timed to this video instead (Ballerina ES: audio
        # +0.46/+57/-21/-19/+46 s, against the embedded English +0.49..+0.57 s everywhere -> one shift)
        ok, how, r = repair_by_reference(srt, ref, starts)
        if ok:
            return True, how, r
    offs = [o for _, o in trusted(w0)]
    if max(offs) - min(offs) > 10.0 and any(abs(o) <= WINDOW_OFF_OK for o in offs):
        # scattered both ways by tens of seconds is the measurement failing (Avengers Infinity War: two independent
        # subs gave the same -15/+30/-0.2/+24/+52 s), not the sub; a real edit offset is coherent (GBU: -1.4 everywhere)
        return True, "unreliable windows", w0
    return False, "windows off", w0


def windows_ok(offsets):
    return all(o is None or abs(o) <= WINDOW_OFF_OK for _, o in offsets) and any(o is not None for _, o in offsets)


def center_channel(media):
    """Path of a temporary 16 kHz mono WAV of the first audio track's centre channel, or None when there is no
    centre. On 5.1/7.1 mixes ffsubsync's speech detection over the whole mix can lock onto music and effects:
    Indiana Jones and the Last Crusade measured factor 1.078 on the mix (implausible: the last cue would land after
    the film ends) and 1.000 / +0.010 s on the centre channel, where the dialogue lives. The caller deletes it."""
    layout = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                             "stream=channel_layout", "-of", "csv=p=0", media], capture_output=True, text=True).stdout
    if "FC" not in layout.upper() and not re.match(r"\s*(5\.1|6\.1|7\.1|3\.0|4\.0|quad)", layout):
        return None
    import tempfile
    fd, wav = tempfile.mkstemp(suffix=".wav", dir="/var/tmp")
    os.close(fd)
    r = subprocess.run(["ionice", "-c3", "nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", media,
                        "-map", "0:a:0", "-af", "pan=mono|c0=FC", "-ar", "16000", wav], capture_output=True, timeout=4 * 3600)
    if r.returncode != 0:
        os.unlink(wav)
        return None
    return wav


# Framerate pairs that explain a pure speed mismatch (The Hunt: PAL 25/24). Anything
# else is another cut of the film, and rescaling it would only hide that.
STANDARD = [24 / 23.976, 25 / 24, 25 / 23.976]
STANDARD += [1 / f for f in STANDARD]
OFF_OK, FAC_OK = 0.1, 0.0005


def _settled(off, fac):
    return abs(off) <= OFF_OK and abs(fac - 1) <= FAC_OK


def fix(media, srt, measure_fn=None, center_fn=center_channel, linear=False):
    """fix_once on the full mix; when that finds a speed no framerate explains, once more against the centre
    channel (center_channel) before calling it another cut."""
    r = fix_once(media, srt, measure_fn, linear)
    if r["status"] == "rejected" and r["action"].startswith("non-standard") and center_fn:
        wav = center_fn(media)
        if wav:
            try:
                r2 = fix_once(wav, srt, measure_fn, linear)
            finally:
                if os.path.exists(wav):
                    os.unlink(wav)
            r2["action"] = (r2["action"] + " (centre channel)") if r2["status"] == "ok" else r2["action"]
            if r2["status"] == "ok" or r2["action"] != r["action"]:
                return r2
    return r


def fix_once(media, srt, measure_fn=None, linear=False):
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
    # linear=True is the manual override for a constant drift no framerate explains (Prison Break S01: 1.004-1.008
    # in 19 of 21 episodes, offsets small). Only Antonio's request opens it; the re-measure gate below still decides.
    elif linear or any(abs(fac - s) <= 0.0005 for s in STANDARD):
        action, fixed = f"scale {fac:.4f} + shift", scale(kept, fac)
        o2, f2 = trial(fixed)
        fixed = shift(fixed, o2)
    else:
        return res("rejected", kept, f"non-standard speed {fac:.4f}: another cut?", off, fac)
    o3, f3 = trial(fixed)
    if abs(f3 - 1) <= FAC_OK and not _settled(o3, f3):     # speed right, a residual offset left: one more shift
        fixed = shift(fixed, o3)                            # (Prison Break E15/E19: +0.22 s / +0.17 s after scaling)
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
