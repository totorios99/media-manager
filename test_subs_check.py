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


def test_ocr_remux_falls_back_to_ffmpeg_when_mkvmerge_retimes():
    d = tempfile.mkdtemp()
    ep = make_episode(d)
    os.remove(ep)
    open(os.path.join(d, "es.srt"), "w").write("1\n00:00:00,100 --> 00:00:00,900\nHola\n\n2\n00:00:01,000 --> 00:00:01,500\nwww.subdivx.com\n")
    subprocess.run(["mkvmerge", "-q", "-o", ep, "--title", "T", "--language", "0:eng", "--language", "1:eng", os.path.join(d, "b.mkv"),
                    "--language", "0:es-419", "--track-name", "0:Español (Latino)", os.path.join(d, "es.srt"),
                    "--language", "0:eng", os.path.join(d, "en.srt")], capture_output=True)
    decisions, rep = sk.check_file(ep, measure_fn=lambda wav, s: (0.0, 1.0))
    real, calls = ocr_pass.f6.durations_differ, []
    def first_fails(*a):
        calls.append(1)
        return "duration of track 1 differs: 1 vs 2 s" if len(calls) == 1 else real(*a)
    ocr_pass.f6.durations_differ = first_fails
    try:
        err = ocr_pass.remux(ep, la.probe(ep), decisions)
    finally:
        ocr_pass.f6.durations_differ = real
    assert err is None and len(calls) == 2, (err, calls)
    tr = la.probe(ep)["tracks"]
    assert [(t["properties"].get("language_ietf"), t["properties"].get("track_name")) for t in tr][2:] == [
        ("es-419", "Español (Latino)"), ("en", None)], tr
    assert la.probe(ep)["container"]["properties"].get("title") == "T"
    print("test_ocr_remux_falls_back_to_ffmpeg_when_mkvmerge_retimes OK")


def test_off_sync_is_reported_not_rewritten():
    d = tempfile.mkdtemp()
    ep = make_episode(d)
    os.remove(ep)
    # 30 cues 40 s apart: three distinct windows, each with enough cues to be measured
    open(os.path.join(d, "es.srt"), "w").write("".join(
        f"{i}\n00:{i * 40 // 60:02d}:{i * 40 % 60:02d},000 --> 00:{i * 40 // 60:02d}:{i * 40 % 60:02d},800\nL{i}\n\n"
        for i in range(1, 31)))
    subprocess.run(["mkvmerge", "-q", "-o", ep, os.path.join(d, "b.mkv"), "--language", "0:spa", os.path.join(d, "es.srt")],
                   capture_output=True)
    calls = []                      # first measure 1 s off, every later one 0: a fix would be "verified" and written
    real = sk.sc.window_offsets
    sk.sc.window_offsets = lambda *a, **k: (calls.append(1) or [(t, 1.0 if len(calls) == 1 else 0.0) for t in (10, 360, 720)])
    try:
        decisions, rep = sk.check_file(ep)
    finally:
        sk.sc.window_offsets = real
    assert not decisions and rep["sync"].get("unfixed") and "how" not in rep["sync"], rep
    print("test_off_sync_is_reported_not_rewritten OK")


if __name__ == "__main__":
    test_off_sync_is_reported_not_rewritten()
    test_embedded_ad_is_removed_in_place()
    test_ocr_remux_falls_back_to_ffmpeg_when_mkvmerge_retimes()
