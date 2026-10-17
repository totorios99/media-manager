"""thin_forced.decide on the 10-17 library cases."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thin_forced as tf  # noqa: E402


def t(i, lang, n, forced=False):
    return {"id": i, "lang": lang, "n": n, "forced": forced}


def test_decide():
    # American Sniper: ES 7 vs EN 1897 -> ES forced, Spanish missing
    assert tf.decide([t(4, "spa", 7), t(5, "eng", 1897)], 133) == ({4: (1, 0)}, ["spa"])
    # Thunderbolts: both thin by cues per minute
    assert tf.decide([t(4, "spa", 50), t(5, "eng", 31)], 127) == ({4: (1, 0), 5: (1, 0)}, ["eng", "spa"])
    # Project Hail Mary: unflagged 83 + forced 3356 in Spanish -> swap, nothing missing
    assert tf.decide([t(4, "spa", 83), t(5, "eng", 1768), t(6, "spa", 3356, True)], 157) == \
        ({4: (1, 0), 6: (0, 1)}, [])
    # Eraserhead: one sparse but real track; Return of the Jedi: EN 27 vs ES 970
    assert tf.decide([t(2, "eng", 136)], 89) == ({}, [])
    assert tf.decide([t(3, "spa", 970), t(4, "eng", 27)], 135) == ({4: (1, 0)}, ["eng"])
    # an already forced track and short files are left alone
    assert tf.decide([t(3, "spa", 900), t(4, "spa", 20, True)], 100) == ({}, [])
    assert tf.decide([t(3, "spa", 5)], 20) == ({}, [])
    print("test_decide OK")


if __name__ == "__main__":
    test_decide()
