"""subs_check: an ad inside an embedded SRT is removed in place; the other tracks, names and order survive."""
import os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library_audit as la  # noqa: E402
import ocr_pass  # noqa: E402
import subs_check as sk  # noqa: E402
from test_f6_series import make_episode  # noqa: E402


def test_embedded_ad_is_removed_in_place():
    d = tempfile.mkdtemp()
    ep = make_episode(d)            # for b.mkv and en.srt; the test file is rebuilt with an ad in the Spanish track
    os.remove(ep)
    open(os.path.join(d, "es.srt"), "w").write("1\n00:00:00,100 --> 00:00:00,900\nHola\n\n"
                                                "2\n00:00:01,000 --> 00:00:01,500\nwww.subdivx.com\n")
    subprocess.run(["mkvmerge", "-q", "-o", ep, "--language", "0:eng", "--language", "1:eng", os.path.join(d, "b.mkv"),
                    "--language", "0:spa", os.path.join(d, "es.srt"),
                    "--language", "0:eng", os.path.join(d, "en.srt")], capture_output=True)
    decisions, rep = sk.check_file(ep, measure_fn=lambda wav, s: (0.0, 1.0))
    assert [a for _, a, _, _ in decisions] == ["replace"] and rep["ads"], rep
    info = la.probe(ep)
    err = ocr_pass.remux(ep, info, decisions)
    assert err is None, err
    tr = la.probe(ep)["tracks"]
    assert [(t["type"], t["properties"]["language"]) for t in tr] == [
        ("video", "eng"), ("audio", "eng"), ("subtitles", "spa"), ("subtitles", "eng")], tr
    out = os.path.join(d, "out.srt")
    subprocess.run(["mkvextract", ep, "tracks", f"2:{out}"], capture_output=True, check=True)
    txt = open(out, encoding="utf-8-sig").read()
    assert "Hola" in txt and "subdivx" not in txt, txt
    print("test_embedded_ad_is_removed_in_place OK")


if __name__ == "__main__":
    test_embedded_ad_is_removed_in_place()
