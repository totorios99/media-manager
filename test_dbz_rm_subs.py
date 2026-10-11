"""dbz_rm_subs.classify on the shapes the survey found."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbz_rm_subs as d  # noqa: E402


def t(words, chala, cast, name=None, forced=False):
    return {"words": words, "chala": chala, "cast": cast, "lat": 0, "name": name, "forced": forced}


def test_classify():
    acts, _ = d.classify({"3": t(659, True, 3), "4": t(488, False, 0)})
    assert acts == {"3": "es-ES", "4": "es-419"}, "S01E01: castellano with lyrics, Latino without"
    acts, _ = d.classify({"3": t(162, True, 0), "4": t(1175, True, 19), "5": t(1019, False, 0)})
    assert acts == {"3": "es-ES", "4": "es-ES", "5": "es-419"}, "S02E08: songs, castellano, Latino"
    acts, _ = d.classify({"3": t(160, True, 0), "4": t(1305, True, 4)})
    assert acts == {"3": "es-ES", "4": "es-ES"}, "S01E14: no Latino track at all"
    s02e16 = {"3": t(163, True, 0), "4": t(1290, True, 22), "5": dict(t(1061, False, 1), lat=7)}
    assert d.classify(s02e16)[0] == {"3": "es-ES", "4": "es-ES", "5": "es-419"}, "Latino markers outweigh one stray"
    assert d.classify({"3": dict(t(1228, False, 1), lat=1), "4": t(2124, True, 0)})[0] is None, "S04E11 stays a tie"
    acts, why = d.classify({"3": t(1031, True, 1), "4": t(799, False, 0)})
    assert acts is None and "track 3" in why, "one castellano marker with lyrics is a tie"
    acts, _ = d.classify({"3": t(2901, False, 2, "GATON"), "5": t(41, False, 0, "FORZADO")})
    assert acts == {"3": "keep", "5": "forced"}, "R&M: noisy markers never drop the only full Spanish track"
    print("test_classify OK")


def test_tie_plan_keeps_only_the_dub():
    s04e12 = {"3": dict(t(818, False, 0), lat=4), "4": dict(t(1637, True, 0), lat=2)}
    assert d.tie_plan(s04e12) == ("3", [], ["4"]), "the lyrics-free dub stays, the fansub goes"
    s04e05 = {"3": t(278, True, 0), "4": t(833, True, 1), "5": t(2177, True, 1), "6": t(235, True, 0)}
    assert d.tie_plan(s04e05) == (None, ["3", "6"], ["4", "5"]), "no dub track: nothing Spanish stays"
    s06e16 = {"3": t(242, True, 0), "4": t(1411, True, 0), "5": dict(t(981, False, 0), lat=1)}
    assert d.tie_plan(s06e16) == ("5", ["3"], ["4"])
    assert d.tie_plan({"3": t(900, False, 3)})[0] is None, "a castellano dub subtitle is not kept"
    print("test_tie_plan_keeps_only_the_dub OK")


if __name__ == "__main__":
    test_classify()
    test_tie_plan_keeps_only_the_dub()
