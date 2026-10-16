"""app._unmark_false_sdh: a sub Bazarr called SDH for one bracketed line becomes the plain one; a real SDH stays."""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402
import subs_clean as sc  # noqa: E402


def test_false_sdh():
    d = tempfile.mkdtemp()
    f = os.path.join(d, "Movie (2014).en.sdh.srt")
    open(f, "w", encoding="utf-8").write(sc.render([(i, i + 1, "[music]" if i == 3 else f"line {i}") for i in range(50)]))
    open(f + ".orig", "w").write("x")
    out = app._unmark_false_sdh(f)
    assert out.endswith("Movie (2014).en.srt") and os.path.exists(out) and os.path.exists(out + ".orig"), out
    g = os.path.join(d, "Other (2010).en.sdh.srt")
    open(g, "w", encoding="utf-8").write(sc.render([(i, i + 1, f"[door {i}]") for i in range(20)]))
    assert app._unmark_false_sdh(g) == g, "a real SDH keeps its name"
    print("test_false_sdh OK")


if __name__ == "__main__":
    test_false_sdh()
