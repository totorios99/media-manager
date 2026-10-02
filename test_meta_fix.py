"""meta_fix.pick_default: the right default, never a commentary, never a coin flip."""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
os.environ.setdefault("TMDB_API_KEY", "x")
import meta_fix as mf  # noqa: E402


def t(codec, lang, name="", default=False, **flags):
    return {"type": "audio", "codec": codec, "properties": {"language": lang, "track_name": name,
                                                            "default_track": default, **flags}}


def test_heavy_default_yields_to_lighter_same_language():
    heavy, light, spa = t("TrueHD Atmos", "eng", default=True), t("AC-3", "eng"), t("E-AC-3", "spa")
    assert mf.pick_default([heavy, spa, light], "en", False, heavy) is light
    print("test_heavy_default_yields_to_lighter_same_language OK")


def test_lone_heavy_english_stays():           # F1: its only English is TrueHD, the light track is Spanish
    heavy, spa = t("TrueHD Atmos", "eng", default=True), t("AC-3", "spa")
    assert mf.pick_default([heavy, spa], "en", False, heavy) is heavy
    print("test_lone_heavy_english_stays OK")


def test_commentary_is_never_picked():
    heavy = t("TrueHD Atmos", "eng", default=True)
    com = t("AC-3", "eng", "Commentary by the director")
    flagged = t("AC-3", "eng", "English", flag_commentary=True)
    main = t("AC-3", "eng", "AC-3 5.1")
    assert mf.pick_default([heavy, com, flagged, main], "en", False, heavy) is main
    assert mf.pick_default([heavy, com, flagged], "en", False, heavy) is heavy
    print("test_commentary_is_never_picked OK")


def test_valid_default_is_left_alone():        # DBS: two identical Spanish tracks, a2 is the default
    a1, a2 = t("FLAC", "spa", "Español (FLAC)"), t("FLAC", "spa", "Español (FLAC)", default=True)
    assert mf.pick_default([a1, a2, t("FLAC", "jpn", "JPN")], "ja", True, a2) is a2
    print("test_valid_default_is_left_alone OK")


def test_wrong_language_default_moves():
    spa, eng = t("AC-3", "spa", default=True), t("AC-3", "eng")
    assert mf.pick_default([spa, eng], "en", False, None) is eng      # two defaults/none -> policy
    assert mf.pick_default([spa, eng], "en", False, spa) is eng
    print("test_wrong_language_default_moves OK")


def test_animation_without_spanish_uses_original():
    jpn, eng = t("FLAC", "jpn"), t("FLAC", "eng", default=True)
    assert mf.pick_default([jpn, eng], "ja", True, eng) is jpn
    print("test_animation_without_spanish_uses_original OK")


if __name__ == "__main__":
    test_heavy_default_yields_to_lighter_same_language(); test_lone_heavy_english_stays()
    test_commentary_is_never_picked(); test_valid_default_is_left_alone()
    test_wrong_language_default_moves(); test_animation_without_spanish_uses_original()
