"""Recover audio an upgrade dropped, using the copy Radarr put in the recycle bin.

An upgrade replaces a file that may hold the only Spanish dub in the library.
Radarr moves the old file to the recycle bin instead of deleting it, which gives
a window -- but only a window: it expires on its own after seven days, and
nothing else would notice. Shutter Island lost its Spanish exactly this way.

Two rules this module will not bend:

  * A graft is never blind. The offset between the two copies is measured by
    cross-correlating a track both files share, and a graft whose offset cannot
    be measured does not happen. The Fast and the Furious sat a constant
    +995.4 ms out with *identical* duration -- runtime comparison cannot see it,
    and a viewer notices within a minute.
  * The recycled file is never deleted here. It expires on Radarr's schedule.
    If a graft is wrong, the source has to still be there.
"""
import json
import os
import subprocess

import numpy as np

RECYCLE = os.environ.get("RECYCLE_DIR", "/srv/storage/.recycle")
SR, WIN, MAX_LAG = 8000, 45, 15
# Beyond this the two files are not the same cut, whatever the titles say.
SANE_OFFSET_MS = 15_000
# A correlation this weak means the tracks do not share content; refuse rather
# than graft on noise.
MIN_PEAK = 0.5


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def tracks(path):
    out = _run(["mkvmerge", "-J", path]).stdout
    return json.loads(out or "{}").get("tracks", [])


def audio_langs(path):
    return {(t["properties"].get("language") or "und")
            for t in tracks(path) if t["type"] == "audio"}


def lost_langs(old_path, new_track_langs, spanish_film):
    """Audio languages the recycled copy has that the new file will NOT ship.

    `new_track_langs` are the scanned, variant-resolved langs of the new file's
    audio ('spa-mx', 'spa-es', ...). Comparing raw tags was blind to a Latino
    dub replaced by a Castilian one: both read 'spa', so no loss was reported,
    and then suggest_tracks dropped the Castilian (it only survives on
    Spanish-original films) and the film shipped with no Spanish at all.
    The recycled copy is our own remux, so its bare 'spa' is what the policy kept."""
    kept = {("spa" if l.startswith("spa") else l) for l in new_track_langs
            if spanish_film or l != "spa-es"}
    return sorted(audio_langs(old_path) - kept - {"und"})


def recycled_copy(folder):
    """The recycled file for a library folder, or None. Largest wins: Radarr
    keeps the folder name, and a title recycled twice leaves more than one."""
    d = os.path.join(RECYCLE, folder)
    if not os.path.isdir(d):
        return None
    vids = [os.path.join(d, f) for f in os.listdir(d)
            if f.lower().endswith((".mkv", ".mp4", ".m4v"))]
    return max(vids, key=os.path.getsize) if vids else None


def _stream_index(path, lang):
    d = json.loads(_run(["ffprobe", "-v", "error", "-select_streams", "a",
                         "-show_entries", "stream=index:stream_tags=language",
                         "-of", "json", path]).stdout or "{}")
    for s in d.get("streams", []):
        if (s.get("tags") or {}).get("language", "") == lang:
            return s["index"]
    return None


def _pcm(path, at, idx):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", path,
                        "-map", f"0:{idx}", "-t", str(WIN), "-ac", "1",
                        "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32)[:WIN * SR]


def measure_offset(a_path, b_path, at):
    """How far b lags a, in ms, over a track both carry. None when unmeasurable.

    Returns (offset_ms, peak) so a caller can refuse on a weak correlation
    rather than trusting a number produced from noise.
    """
    shared = audio_langs(a_path) & audio_langs(b_path) - {"und"}
    if not shared:
        return None, 0.0
    lang = "eng" if "eng" in shared else sorted(shared)[0]
    ia, ib = _stream_index(a_path, lang), _stream_index(b_path, lang)
    if ia is None or ib is None:
        return None, 0.0
    a, b = _pcm(a_path, at, ia), _pcm(b_path, at, ib)
    n = min(len(a), len(b))
    # The search window is trimmed from both ends, so the sample has to be
    # longer than twice the lag or there is nothing left to correlate. Report
    # unmeasurable rather than raising: a short or near-silent tail is a normal
    # thing to run into, and the caller's answer to it is "refuse to graft".
    lags = MAX_LAG * SR
    if n < 2 * lags + SR * 5:
        return None, 0.0
    a, b = a[:n], b[:n]
    a = (a - a.mean()) / (a.std() or 1)
    b = (b - b.mean()) / (b.std() or 1)
    core = a[lags:-lags]
    c = np.correlate(b, core, mode="valid")
    k = int(np.argmax(c))
    denom = np.linalg.norm(core) * np.linalg.norm(b[k:k + len(core)])
    peak = float(c[k] / denom) if denom else 0.0
    return (k - lags) / SR * 1000.0, peak


def plan_graft(new_path, old_path):
    """What the new file is missing that the old one has.

    Returns (missing_langs, offset_ms, peak, reason). reason is set when a graft
    must not proceed, and is None when it may.
    """
    new_l, old_l = audio_langs(new_path), audio_langs(old_path)
    missing = sorted(old_l - new_l - {"und"})
    if not missing:
        return [], 0.0, 1.0, "el reemplazo no perdió ningún idioma"
    dur = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", new_path]).stdout.strip()
    at = min(2400, int(float(dur or 3000) * 0.4))
    off, peak = measure_offset(new_path, old_path, at)
    if off is None:
        return missing, None, 0.0, "sin pista común medible: no se injerta a ciegas"
    if peak < MIN_PEAK:
        return missing, off, peak, f"correlación débil ({peak:.2f}): fuentes distintas"
    if abs(off) > SANE_OFFSET_MS:
        return missing, off, peak, f"desfase absurdo ({off/1000:.1f}s): no es el mismo corte"
    return missing, off, peak, None
