"""Subtitle sync measured against where people actually speak (Silero VAD), not against audio energy.

    measure(wav, srt) -> (offset_s or None, score)      # drop-in measure_fn for subs_clean.window_offsets

The offset is what to ADD to the cues, the same convention as subs_clean.measure (ffsubsync). The VAD runs in the
whisper-ctranslate2 tool environment (it ships faster-whisper and the Silero model), as a subprocess, so
media-manager gets no new dependency.

Why: ffsubsync's window measure reported 20-60 s offsets on files that Whisper found in sync (10-25: 0 of 9
real), and its "after" check reused the same measure. Speech spans are language-blind: Spanish cues line up with
English speech just as well, since both follow the same lines on screen.

Score: the share of cue time that overlaps speech at the chosen offset. An offset is only reported when it beats
0 s clearly; a flat curve (music, silence, a dub that talks over everything) gives None, not a guess.
"""
import json, os, subprocess, sys

import subs_clean as sc

VAD_PY = os.path.expanduser("~/.local/share/uv/tools/whisper-ctranslate2/bin/python")
SEARCH_S, STEP_S = 15.0, 0.05
MIN_CUES, MIN_SCORE = 8, 0.35       # too few cues, or too little overlap anywhere: no verdict
MIN_PEAK = 0.03                     # peak height over the curve's floor; less is a flat curve: no verdict
NEAR_PEAK = 0.25                    # 0 s within this share of the peak height counts as the peak itself

_HELPER = """
import json, sys
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps, VadOptions
a = decode_audio(sys.argv[1], sampling_rate=16000)
ts = get_speech_timestamps(a, VadOptions(min_silence_duration_ms=300, speech_pad_ms=100))
print(json.dumps([[t["start"] / 16000, t["end"] / 16000] for t in ts]))
"""


def speech_spans(wav):
    out = subprocess.run(["nice", "-n", "10", VAD_PY, "-c", _HELPER, wav], capture_output=True, text=True,
                         timeout=1800, check=True)
    return json.loads(out.stdout)


def overlap(cues, speech, off):
    """Seconds of cue time inside speech when every cue is moved by `off`. Both lists sorted by start."""
    tot, j = 0.0, 0
    for a, b in cues:
        a, b = a + off, b + off
        while j < len(speech) and speech[j][1] <= a:
            j += 1
        k = j
        while k < len(speech) and speech[k][0] < b:
            tot += min(b, speech[k][1]) - max(a, speech[k][0])
            k += 1
    return tot


def best_offset(cues, speech, search=SEARCH_S, step=STEP_S):
    """-> (offset or None, score at the reported offset)."""
    total = sum(b - a for a, b in cues) or 1.0
    n = int(round(search / step))
    curve = [(i * step, overlap(cues, speech, i * step) / total) for i in range(-n, n + 1)]
    best = max(s for _, s in curve)
    top = [o for o, s in curve if s >= best - 0.005]
    off = top[len(top) // 2]                     # a cue shorter than its speech span fits a plateau: take its middle
    at0 = next(s for o, s in curve if abs(o) < step / 2)
    # Judged on the peak's height over the curve's floor, not as a ratio to 0 s: in a talky film speech covers
    # most of the window, so the curve is shallow (When Harry Met Sally: 0.749 at the peak, 0.673 at +-4 s) and a
    # +2 s shift beat 0 s by only 11 %; the old "beat 0 s by 15 %" rule called it in sync.
    peak = best - min(s for _, s in curve)
    if best < MIN_SCORE or peak < MIN_PEAK:
        return None, best
    if abs(off) <= 0.3 or best - at0 < NEAR_PEAK * peak:
        return 0.0, at0
    return round(off, 2), best


def measure(wav, srt):
    cues = [(a, b) for a, b, _ in sc.parse(sc.decode(open(srt, "rb").read()))]
    if len(cues) < MIN_CUES:
        return None, 0.0
    return best_offset(sorted(cues), speech_spans(wav))


if __name__ == "__main__":
    if len(sys.argv) == 3:                       # sync_vad.py <media> <srt>: the 3-window check on a real file
        print(sc.window_offsets(sys.argv[1], sys.argv[2], measure_fn=measure))
        sys.exit()
    starts, t = [], 0.0
    for i in range(80):                          # irregular gaps: a periodic pattern would match at several offsets
        starts.append(t)
        t += 2.0 + (i * 7 % 5) * 0.9
    speech = [[s, s + 2.0] for s in starts]
    cues = [(s + 3.0, s + 5.0) for s in starts]  # every cue 3 s late
    off, score = best_offset(cues, speech)
    assert abs(off + 3.0) < 0.06 and score > 0.9, (off, score)
    assert best_offset([(s, s + 1.6) for s in starts], speech)[0] == 0.0
    assert best_offset(cues, [])[0] is None
    print("sync_vad self-test OK")
