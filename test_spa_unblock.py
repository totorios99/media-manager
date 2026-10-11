"""spa_unblock.decide: tag only when it leaves one full Spanish track."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spa_unblock as su  # noqa: E402


def t(words, verdict="unknown", name=None, forced=False):
    return {"words": words, "verdict": verdict, "name": name, "forced": forced}


def test_decide():
    acts, _ = su.decide({"3": t(2901, name="GATON"), "5": t(41, name="FORZADO")})
    assert acts == {"5": "forced"}, "R&M: the misnamed forced track is flagged, the full one is left alone"
    acts, _ = su.decide({"3": t(1300, "castellano"), "4": t(800)})
    assert acts == {"3": "es-ES"}, "castellano out, the other one is the only full track left"
    acts, _ = su.decide({"3": t(4000, "latino"), "4": t(3500, "castellano")})
    assert acts == {"3": "es-419", "4": "es-ES"}
    acts, why = su.decide({"3": t(1600), "4": t(800)})
    assert acts is None and "2 full" in why, "two unknown full tracks stay a human call"
    print("test_decide OK")


if __name__ == "__main__":
    test_decide()
