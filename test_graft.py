"""The sync check must catch an offset that duration comparison cannot see.

Built on two synthetic files carrying the same audio, one shifted by a known
amount. The Fast and the Furious was +995.4 ms out with identical duration, so a
graft that trusts equal runtimes is a graft that ships a film out of sync.
"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("TMDB_API_KEY", "x")

import graft

# Real films give minutes of audio; these clips are seconds. Narrow the search
# window so the test exercises the measurement rather than the guard.
graft.WIN, graft.MAX_LAG = 20, 3


def build(path, offset_ms=0, langs=("eng", "spa")):
    """A short mkv with a tone-plus-noise soundtrack, optionally delayed.

    Noise matters: a pure tone correlates with itself at every period, so the
    peak would land anywhere and the test would pass on a broken measurement.
    """
    delay = f",adelay={offset_ms}|{offset_ms}" if offset_ms else ""
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "color=c=black:s=320x240:d=40:r=5",
           "-f", "lavfi", "-i",
           f"sine=frequency=440:duration=40,aeval=val(0)+0.4*random(0){delay}"]
    for _ in langs[1:]:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=880:duration=40"]
    cmd += ["-map", "0:v", "-c:v", "libx264", "-preset", "ultrafast", "-t", "38"]
    for i, _ in enumerate(langs):
        cmd += ["-map", f"{i+1}:a"]
    cmd += ["-c:a", "flac"]
    for i, l in enumerate(langs):
        cmd += [f"-metadata:s:a:{i}", f"language={l}"]
    cmd.append(path)
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


def main():
    d = tempfile.mkdtemp()
    new = os.path.join(d, "new.mkv")      # upgrade: English only
    old = os.path.join(d, "old.mkv")      # recycled: English + Spanish
    build(new, 0, ("eng",))
    build(old, 0, ("eng", "spa"))

    missing, off, peak, reason = graft.plan_graft(new, old)
    assert missing == ["spa"], f"no vio la pista perdida: {missing}"
    assert reason is None, f"rechazó un injerto válido: {reason}"
    assert abs(off) < 60, f"desfase espurio en archivos alineados: {off:.1f} ms"
    assert peak > graft.MIN_PEAK, f"pico bajo en el mismo audio: {peak:.2f}"

    # the case duration cannot see: same length, shifted content
    shifted = os.path.join(d, "shifted.mkv")
    build(shifted, 1000, ("eng", "spa"))
    missing, off, peak, reason = graft.plan_graft(new, shifted)
    assert missing == ["spa"]
    assert off is not None and reason is None, f"no pudo medir: {reason}"
    assert 900 < abs(off) < 1100, f"no midió el segundo de desfase: {off:.1f} ms"

    # nothing missing -> nothing to do
    both = os.path.join(d, "both.mkv")
    build(both, 0, ("eng", "spa"))
    missing, off, peak, reason = graft.plan_graft(both, old)
    assert missing == [] and reason, "debería no hacer nada cuando no falta idioma"

    # no shared track -> refuse rather than guess
    fr = os.path.join(d, "fr.mkv")
    build(fr, 0, ("fra",))
    missing, off, peak, reason = graft.plan_graft(fr, old)
    assert reason and "ciegas" in reason, f"debería negarse sin pista común: {reason}"
    print("ok")


if __name__ == "__main__":
    sys.exit(main())
