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


if __name__ == "__main__":
    test_mid_file_ad_removed_dialogue_kept()
    test_credit_block_loses_the_name_too()
    test_cue_with_dialogue_keeps_dialogue()
    test_render_roundtrip_and_shift()
