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


def test_urls_emails_and_team_credits_anywhere():
    """Prison Break E14/E17: e-mail credits at the edges, an Asia-Team block at minute 7."""
    kept, removed = sc.strip_ads(sc.parse(
        "1\n00:00:05,000 --> 00:00:08,000\nTraduccido por Aleath\nSincronizado por OverCraft\n\n"
        "2\n00:00:09,000 --> 00:00:10,000\nOverCraft@gmail.com\n\n"
        "3\n00:06:54,000 --> 00:07:00,000\nSubtítulos Traducidos por:\n\n"
        "4\n00:07:06,000 --> 00:07:13,000\nEquipo Asia-Team\n.: Www.Asia-Team.Net :.\n\n"
        "5\n00:20:00,000 --> 00:20:02,000\nTenemos que salir de aquí.\n\n"
        "6\n00:42:42,000 --> 00:42:47,000\nEdicion final por OverCraft\n"))
    assert [t for _, _, t in kept] == ["Tenemos que salir de aquí."], kept
    print("test_urls_emails_and_team_credits_anywhere OK")


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


def test_refit_from_windows_removes_a_small_linear_drift():
    """E08: windows at 0/17/35 min said -0.57/+0.89/+1.43 s. The refit line must bring every window to ~0."""
    cues = [(60.0 * i, 60.0 * i + 2, f"l{i}") for i in range(1, 45)]
    p = _write("mm-test-refit.srt", cues)
    drift = lambda t: -0.8 + 0.001 * t                         # what each cue still lacks to be in time
    offs = [(a, drift(a + sc.WINDOW_S / 2)) for a in (0, 1000, 2100)]
    s, b = sc.refit_from_windows(p, offs)
    out = sc.parse(sc.decode(open(p, "rb").read()))
    for (t, _, _), (t2, _, _) in zip(cues, out):
        assert abs(t2 - (t + drift(t))) < 0.01, (t, t2)
    os.unlink(p)
    print("test_refit_from_windows_removes_a_small_linear_drift OK")


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


def test_piecewise_follows_a_mid_episode_jump():
    import tempfile
    f = tempfile.mktemp(suffix=".srt")
    cues = [(t, t + 1, f"l{t}") for t in range(0, 3000, 30)]
    open(f, "w", encoding="utf-8").write(sc.render(cues))
    W = sc.WINDOW_S
    pts = sc.piecewise_from_windows(f, [(0, 0.0), (1000, 0.0), (2000, -1.6), (2500, None)])
    assert len(pts) == 3
    got = {int(t[1:]): a for a, _, t in sc.parse(sc.decode(open(f, "rb").read()))}   # label "l<orig>" -> new start
    assert abs(got[0] - 0) < 0.01, "flat before the first centre"
    assert abs(got[2490] - (2490 - 1.6)) < 0.01, "flat after the last centre"
    mid = 1000 + W / 2 + (2000 - 1000) / 2
    near = min(got, key=lambda k: abs(k - mid))
    assert -1.0 < got[near] - near < -0.5, "interpolated between the two centres"
    assert sc.piecewise_from_windows(f, [(0, 0.1), (900, None)]) is None
    print("test_piecewise_follows_a_mid_episode_jump OK")


def test_english_credits():
    """Yellowstone S01E08 (Addic7ed): the credit line beside the web address goes too, at minute 5 and at the end."""
    kept, removed = sc.strip_ads(sc.parse(
        "1\n00:05:21,000 --> 00:05:26,000\n- Synced & corrected by <font color=\"#E83286\">MementMori</font> -\n"
        "-- <font color=\"#138CE9\">www.addic7ed.com</font> --\n\n"
        "2\n00:20:00,000 --> 00:20:02,000\nThe subtitles by the door are yours.\n\n"
        "3\n00:40:17,000 --> 00:40:22,000\nSubtitles ripped by someone\n"))
    assert [t for _, _, t in kept] == ["The subtitles by the door are yours."], kept
    print("test_english_credits OK")


def test_segment_fit_cuts_at_the_eyecatch():
    import tempfile
    f = tempfile.mktemp(suffix=".srt")
    cues = [(t, t + 2, f"l{t}") for t in list(range(0, 760, 20)) + list(range(770, 1400, 20))]   # 10 s gap at 760
    open(f, "w", encoding="utf-8").write(sc.render(cues))
    cut = sc.segment_fit(f, [(300, -4.9), (540, -4.9), (780, 1.0), (1020, 1.0), (1200, 60.0)])
    assert cut is not None and 740 < cut < 780, cut
    got = {int(t[1:]): a for a, _, t in sc.parse(sc.decode(open(f, "rb").read()))}
    assert abs(got[700] - (700 - 4.9)) < 0.01 and abs(got[790] - 791.0) < 0.01, (got[700], got[790])
    assert sc.segment_fit(f, [(300, 0.1), (540, 0.2), (780, 0.1)]) is None, "no jump, nothing to do"
    print("test_segment_fit_cuts_at_the_eyecatch OK")


def test_settle_windows_repairs_a_global_fit_or_refuses():
    import tempfile
    f = tempfile.mktemp(suffix=".srt")
    open(f, "w", encoding="utf-8").write(sc.render([(t, t + 2, f"l{t}") for t in range(0, 3000, 15)]))
    calls = iter([[(600, -1.4), (1500, -1.4), (2400, -1.4), (2900, 60.0)],     # global fit left it -1.4 s
                  [(600, 0.02), (1500, -0.01), (2400, 0.03), (2900, 60.0)]])   # after the piecewise pass
    ok, how, w = sc.settle_windows("m.mkv", f, windows_fn=lambda m, p: next(calls))
    assert ok and how == "piecewise", (ok, how)
    got = {int(t[1:]): a for a, _, t in sc.parse(sc.decode(open(f, "rb").read()))}
    assert abs(got[300] - (300 - 1.4)) < 0.01, got[300]                        # the fit was really applied
    bad = [(600, 2.0), (1500, -2.5), (2400, 2.2)]
    before = open(f, "rb").read()
    ok, how, _ = sc.settle_windows("m.mkv", f, windows_fn=lambda m, p: bad)
    assert not ok and how == "windows off" and open(f, "rb").read() == before, "a failed repair leaves the file as it was"
    noisy = [(600, -15.1), (1500, 24.2), (2400, -0.19), (2900, 28.0)]
    ok, how, _ = sc.settle_windows("m.mkv", f, windows_fn=lambda m, p: noisy)
    assert ok and how == "unreliable windows" and open(f, "rb").read() == before, "scattered windows keep the global fit"
    ok, how, _ = sc.settle_windows("m.mkv", f, windows_fn=lambda m, p: [(600, None), (1500, 59.0)])
    assert ok and how == "unmeasured", "unmeasurable windows keep the global fit"
    print("test_settle_windows_repairs_a_global_fit_or_refuses OK")


def test_settle_windows_falls_back_to_a_reference_sub():
    import tempfile
    f, ref = tempfile.mktemp(suffix=".srt"), tempfile.mktemp(suffix=".srt")
    times = [t * 7.3 for t in range(800)]                                       # ~97 min, irregular-ish
    open(ref, "w", encoding="utf-8").write(sc.render([(t, t + 2, "en") for t in times]))
    open(f, "w", encoding="utf-8").write(sc.render([(t - 0.52, t + 1.5, "es") for t in times]))
    noisy = lambda m, p: [(600, 0.46), (1900, 57.0), (3300, -21.5), (4700, -19.2), (5500, 46.2)]   # Ballerina, audio
    ok, how, w = sc.settle_windows("m.mkv", f, windows_fn=noisy, reference_fn=lambda: ref)
    assert ok and how == "reference shift", (ok, how)
    assert all(abs(o) < 0.01 for _, o in w), w
    ok, how, _ = sc.settle_windows("m.mkv", f, windows_fn=noisy, reference_fn=lambda: ref)
    assert ok and how == "reference", how                                       # already in sync: not rewritten
    ok, how, _ = sc.settle_windows("m.mkv", f, windows_fn=noisy, reference_fn=lambda: None)
    assert not ok and how == "windows off", how                                  # no reference: the hook's 10-17 verdict
    print("test_settle_windows_falls_back_to_a_reference_sub OK")


if __name__ == "__main__":
    test_settle_windows_falls_back_to_a_reference_sub()
    test_nonstandard_on_the_mix_is_remeasured_on_the_centre_channel()
    test_fix_constant_offset_is_corrected_and_remeasured()
    test_fix_rejects_nonstandard_speed()
    test_linear_override_scales_a_constant_drift_and_still_remeasures()
    test_windows_catch_a_cold_open_the_global_fit_hides()
    test_refit_from_windows_removes_a_small_linear_drift()
    test_mid_file_ad_removed_dialogue_kept()
    test_credit_block_loses_the_name_too()
    test_urls_emails_and_team_credits_anywhere()
    test_cue_with_dialogue_keeps_dialogue()
    test_render_roundtrip_and_shift()
    test_piecewise_follows_a_mid_episode_jump()
    test_english_credits()
    test_segment_fit_cuts_at_the_eyecatch()
    test_settle_windows_repairs_a_global_fit_or_refuses()
