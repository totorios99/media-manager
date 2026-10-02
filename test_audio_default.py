"""audio_default.pick: the right default, never a commentary, TrueHD only when alone."""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
os.environ.setdefault("TMDB_API_KEY", "x")
import audio_default as ad  # noqa: E402


def t(codec, lang, name="", default=False, ch=6, **flags):
    return {"type": "audio", "codec": codec, "properties": {"language": lang, "track_name": name,
            "default_track": default, "audio_channels": ch, **flags}}


def test_truehd_alone_stays_default():                       # F1: English is TrueHD only
    heavy, spa = t("TrueHD Atmos", "eng", default=True), t("AC-3", "spa")
    assert ad.pick([heavy, spa], "en", False, heavy) is heavy


def test_truehd_yields_to_most_compatible_same_language():
    heavy, ac3, eac3 = t("TrueHD Atmos", "eng", default=True), t("AC-3", "eng"), t("E-AC-3", "eng")
    assert ad.pick([heavy, ac3, eac3], "en", False, heavy) is eac3


def test_compat_beats_lossless_dts():                        # Gladiator: DTS-HD MA and E-AC-3
    dts, eac3 = t("DTS-HD Master Audio", "eng", default=True, ch=8), t("E-AC-3", "eng", ch=6)
    assert ad.pick([dts, eac3], "en", False, dts) is eac3
    assert ad.pick([dts], "en", False, dts) is dts            # alone: whatever it is


def test_more_channels_break_a_codec_tie():
    s, m = t("AC-3", "eng", ch=2), t("AC-3", "eng", ch=6)
    assert ad.pick([s, m], "en", False, None) is m


def test_commentary_is_never_picked():
    heavy = t("TrueHD Atmos", "eng", default=True)
    com = t("AC-3", "eng", "Commentary by the director")
    flagged = t("AC-3", "eng", "English", flag_commentary=True)
    main = t("AC-3", "eng", "AC-3 5.1")
    assert ad.pick([heavy, com, flagged, main], "en", False, heavy) is main
    assert ad.pick([heavy, com, flagged], "en", False, heavy) is heavy


def test_tied_default_is_left_alone():                       # DBS: two identical Spanish FLAC tracks
    a1, a2 = t("FLAC", "spa", "Español (FLAC)"), t("FLAC", "spa", "Español (FLAC)", default=True)
    assert ad.pick([a1, a2, t("FLAC", "jpn", "JPN")], "ja", True, a2) is a2


def test_original_language_for_non_animation():              # The Office: English, not the Latino dub
    spa, eng = t("AC-3", "spa", "Español (Latinoamérica)", default=True), t("AC-3", "eng", "English")
    assert ad.pick([spa, eng], "en", False, spa) is eng


def test_animation_prefers_latino_when_it_exists_else_original():
    lat, jpn = t("AC-3", "spa", "Latino"), t("FLAC", "jpn", default=True)
    assert ad.pick([lat, jpn], "ja", True, jpn) is lat
    assert ad.pick([jpn, t("FLAC", "eng")], "ja", True, jpn) is jpn


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(n, "OK")
