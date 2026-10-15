"""ocr_proto.oov: repeated capitalized names are not OCR noise; real garbage still counts."""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_proto as op  # noqa: E402
import subs_clean as sc  # noqa: E402


def test_names_are_not_oov():
    f = tempfile.mktemp(suffix=".srt")
    lines = ["Ryuk dice que no", "Ryuk tiene hambre", "Ryuk se ríe", "xqzv no es nada"]
    open(f, "w", encoding="utf-8").write(sc.render([(i, i + 1, t) for i, t in enumerate(lines)]))
    voc = {"dice", "que", "no", "tiene", "hambre", "se", "ríe", "es", "nada"}
    rate, n = op.oov(f, voc)
    assert n == 14 and abs(rate - 1 / 14) < 1e-9, (rate, n)          # only xqzv is unknown
    lines[2] = "el ryuk se ríe"                                       # seen lowercase once: not a name any more
    open(f, "w", encoding="utf-8").write(sc.render([(i, i + 1, t) for i, t in enumerate(lines)]))
    rate, n = op.oov(f, voc | {"el"})
    assert abs(rate - 4 / 15) < 1e-9, (rate, n)
    print("test_names_are_not_oov OK")


if __name__ == "__main__":
    test_names_are_not_oov()
