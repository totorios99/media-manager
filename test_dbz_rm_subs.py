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
    acts, why = d.classify({"3": t(1031, True, 1), "4": t(799, False, 0)})
    assert acts is None and "track 3" in why, "one castellano marker with lyrics is a tie"
    acts, _ = d.classify({"3": t(2901, False, 2, "GATON"), "5": t(41, False, 0, "FORZADO")})
    assert acts == {"3": "keep", "5": "forced"}, "R&M: noisy markers never drop the only full Spanish track"
    print("test_classify OK")


if __name__ == "__main__":
    test_classify()
