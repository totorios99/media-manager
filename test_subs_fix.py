"""Bazarr's post-processing hook: a sidecar is cleaned and re-timed in place, the
original survives once as .srt.orig, and nothing outside the library is touched."""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())

import subs_clean as sc  # noqa: E402

SRT = ("1\n00:00:05,000 --> 00:00:06,000\nHola\n\n"
       "2\n00:06:22,000 --> 00:06:30,000\nwww.SUBTITULOS.es\n-DIFUNDE LA PALABRA-\n\n"
       "3\n00:10:00,000 --> 00:10:02,000\nAdiós\n")


def fake(truth_first):
    def m(media, path):
        first = sc.parse(sc.decode(open(path, "rb").read()))[0][0]
        return truth_first - first, 1.0
    return m


def main():
    import app
    root = tempfile.mkdtemp()
    app.MEDIA_ROOT = app.SHOWS_ROOT = root
    season = os.path.join(root, "Show (2015)", "Season 1")
    os.makedirs(season)
    for ep in ("S01E01", "S01E02"):
        open(os.path.join(season, f"Show - {ep}.mkv"), "w").close()
    srt = os.path.join(season, "Show - S01E01.es-MX.hi.srt")
    open(srt, "w", encoding="utf-8").write(SRT)

    # the video is the episode's own, not its sibling's
    assert app._media_for_subtitle(srt).endswith("Show - S01E01.mkv")

    r = app._fix_subtitle(srt, fake(6.93))        # file is 1.93 s early
    assert r["status"] == "ok" and r["written"] and r["ads"] == 2, r
    out = open(srt, encoding="utf-8").read()
    assert "SUBTITULOS" not in out and "Hola" in out and "Adiós" in out
    assert sc.parse(out)[0][0] == 6.93, sc.parse(out)[0]
    assert open(srt + ".orig", encoding="utf-8").read() == SRT, "original kept"

    # second pass on a clean, in-time file changes nothing and keeps the first .orig
    r2 = app._fix_subtitle(srt, fake(6.93))
    assert r2["written"] is False and open(srt + ".orig", encoding="utf-8").read() == SRT

    # paths outside the library are refused
    outside = os.path.join(tempfile.mkdtemp(), "x.srt")
    open(outside, "w").write(SRT)
    try:
        app._fix_subtitle(outside, fake(0))
        raise AssertionError("outside path accepted")
    except ValueError:
        pass

    # a rejected result is never written
    bad = os.path.join(season, "Show - S01E02.es-MX.srt")
    open(bad, "w", encoding="utf-8").write(SRT)
    r3 = app._fix_subtitle(bad, lambda m, p: (0.0, 1.2))
    assert r3["status"] == "rejected" and not r3["written"]
    assert open(bad, encoding="utf-8").read() == SRT and not os.path.exists(bad + ".orig")
    print("test_subs_fix OK")


if __name__ == "__main__":
    main()
