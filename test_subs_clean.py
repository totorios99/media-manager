"""subs_clean: provider ads go, dialogue stays, constant shifts are exact."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subs_clean as sc  # noqa: E402

SRT = """1
00:00:01,000 --> 00:00:02,000
Hola, amigo.

2
00:06:22,293 --> 00:06:33,262
www.SUBTITULOS.es
-DIFUNDE LA PALABRA-

3
00:06:37,228 --> 00:06:39,200
Mira www.google.com y dime.

4
00:10:00,000 --> 00:10:02,000
- Traducido por mi madre.
- Qué bien.

5
00:00:30,000 --> 00:00:31,000
Traducido por Fulano

6
01:04:00,000 --> 01:04:02,000
Nos vemos.

7
01:04:19,667 --> 01:04:50,067
Subtítulos por OpenSubtitles
-DIFUNDE LA PALABRA-
"""


def test_mid_file_ad_removed_dialogue_kept():
    cues = sc.parse(SRT)
    kept, removed = sc.strip_ads(cues)
    text = " ".join(t for _, _, t in kept)
    assert "SUBTITULOS" not in text and "DIFUNDE" not in text and "OpenSubtitles" not in text
    assert "google.com" in text, "a URL in dialogue is not an ad"
    assert "Traducido por mi madre" in text, "weak credit mid-file is dialogue"
    assert "Fulano" not in text, "weak credit at the start is a credit"
    assert len(kept) == 4, len(kept)
    print("test_mid_file_ad_removed_dialogue_kept OK")


def test_credit_block_loses_the_name_too():
    kept, removed = sc.strip_ads(sc.parse(
        "1\n00:00:05,000 --> 00:00:06,000\nHola\n\n"
        "2\n01:30:00,000 --> 01:30:02,000\nAdiós\n\n"
        "3\n01:30:56,000 --> 01:30:58,000\nSubtitulada por\nBrielga / Alexcrist.\n"))
    assert [t for _, _, t in kept] == ["Hola", "Adiós"], kept
    kept, _ = sc.strip_ads(sc.parse(          # Roman Holiday: an hour past the film, F6 refused the remux
        "1\n00:00:05,000 --> 00:00:06,000\nHola\n\n"
        "2\n02:57:57,400 --> 02:58:09,841\nSubtítulos por Mötis Crüe\n"))
    assert [t for _, _, t in kept] == ["Hola"], kept
    print("test_credit_block_loses_the_name_too OK")


def test_cue_with_dialogue_keeps_dialogue():
    kept, removed = sc.strip_ads(sc.parse(
        "1\n00:00:05,000 --> 00:00:06,000\nHola\nwww.subtitulos.es\n"))
    assert [t for _, _, t in kept] == ["Hola"] and len(removed) == 1
    print("test_cue_with_dialogue_keeps_dialogue OK")


def test_render_roundtrip_and_shift():
    cues = sc.parse(SRT)
    again = sc.parse(sc.render(cues))
    assert [(round(a, 3), round(b, 3), t) for a, b, t in again] == \
           [(round(a, 3), round(b, 3), t) for a, b, t in cues]
    moved = sc.shift(cues, -1.93)
    assert abs(moved[0][0] - (1.0 - 1.93)) < 1e-9
    assert sc.render(sc.shift(sc.parse("1\n00:00:01,000 --> 00:00:02,000\nx\n"), -5)).startswith(
        "1\n00:00:00,000"), "negative times clamp to zero"
    print("test_render_roundtrip_and_shift OK")


def _fake_measure(truth_first, factor=1.0):
    """Stands in for ffsubsync: the 'media' says the first cue belongs at truth_first."""
    def m(media, path):
        first = sc.parse(sc.decode(open(path, "rb").read()))[0][0]
        return truth_first - first * factor, factor
    return m


def _write(name, cues):
    p = os.path.join("/var/tmp", name)
    open(p, "w", encoding="utf-8").write(sc.render(cues))
    return p


def test_fix_constant_offset_is_corrected_and_remeasured():
    p = _write("mm-test-off.srt", sc.shift(sc.parse(SRT), -1.93))
    r = sc.fix("media", p, _fake_measure(1.0))
    assert r["status"] == "ok" and r["action"].startswith("shift"), r
    assert abs(r["offset"]) < 0.01 and r["ads"] == 5, r
    assert "SUBTITULOS" not in r["text"]
    os.unlink(p)
    print("test_fix_constant_offset_is_corrected_and_remeasured OK")


def test_fix_rejects_nonstandard_speed():
    p = _write("mm-test-speed.srt", sc.parse(SRT))
    r = sc.fix("media", p, lambda m, s: (0.0, 1.2))
    assert r["status"] == "rejected" and r["text"] is None, r
    os.unlink(p)
    print("test_fix_rejects_nonstandard_speed OK")


def test_windows_catch_a_cold_open_the_global_fit_hides():
    """Prison Break E06: right after the intro, 3 s off before it. The window at the start must fail."""
    assert sc.windows_ok([(0, 0.05), (1200, -0.02), (2100, 0.1)])
    assert not sc.windows_ok([(0, 3.0), (1200, 0.0), (2100, 0.0)])
    assert not sc.windows_ok([(0, None), (1200, None)]), "no measurement at all is not a pass"
    print("test_windows_catch_a_cold_open_the_global_fit_hides OK")


def test_linear_override_scales_a_constant_drift_and_still_remeasures():
    """Prison Break S01: a constant 1.006 drift. Rejected by default; with linear=True it is scaled, and the result
    is kept only because the re-measure settles at 1.0000."""
    cues = [(10.0 + 100 * i, 12.0 + 100 * i, f"línea {i}") for i in range(25)]
    p = _write("mm-test-linear.srt", cues)
    tf, tl = 10.0 * 1.006, (10.0 + 100 * 24) * 1.006           # where the film really says them

    def m(media, path):                                          # linear fit of the file onto the truth
        c = sc.parse(sc.decode(open(path, "rb").read()))
        fac = (tl - tf) / (c[-1][0] - c[0][0])
        return tf - c[0][0] * fac, round(fac, 4)
    assert sc.fix("media", p, m, center_fn=None)["status"] == "rejected"
    r = sc.fix("media", p, m, center_fn=None, linear=True)
    assert r["status"] == "ok" and r["factor"] == 1.0 and abs(r["offset"]) <= sc.OFF_OK, r
    os.unlink(p)
    print("test_linear_override_scales_a_constant_drift_and_still_remeasures OK")



def test_nonstandard_on_the_mix_is_remeasured_on_the_centre_channel():
    """Indiana Jones: 1.078 on the 5.1 mix, 1.000 on the centre channel. The centre WAV is always deleted."""
    p = _write("mm-test-centre.srt", sc.parse(SRT))
    made = []

    def centre(media):
        w = _write("mm-test-centre.wav", [])
        made.append(w)
        return w
    r = sc.fix("movie.mkv", p, lambda m, s: (0.0, 1.0) if m.endswith(".wav") else (0.0, 1.078), centre)
    assert r["status"] == "ok" and "centre channel" in r["action"], r
    assert made and not os.path.exists(made[0]), "temporary WAV left behind"
    r = sc.fix("movie.mkv", p, lambda m, s: (0.0, 1.078), centre)     # still off on the centre: still rejected
    assert r["status"] == "rejected", r
    os.unlink(p)
    print("test_nonstandard_on_the_mix_is_remeasured_on_the_centre_channel OK")


if __name__ == "__main__":
    test_nonstandard_on_the_mix_is_remeasured_on_the_centre_channel()
    test_fix_constant_offset_is_corrected_and_remeasured()
    test_fix_rejects_nonstandard_speed()
    test_linear_override_scales_a_constant_drift_and_still_remeasures()
    test_windows_catch_a_cold_open_the_global_fit_hides()
    test_mid_file_ad_removed_dialogue_kept()
    test_credit_block_loses_the_name_too()
    test_cue_with_dialogue_keeps_dialogue()
    test_render_roundtrip_and_shift()
