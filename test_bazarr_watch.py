"""bazarr_watch.wanted: which removals are watched."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bazarr_watch as bw  # noqa: E402


def test_wanted():
    d = tempfile.mkdtemp()
    w = lambda name, rows: open(os.path.join(d, name), "w").write("".join(json.dumps(r) + "\n" for r in rows))
    w("ocr_review.jsonl", [{"path": "/m/a.mkv", "lang": "eng", "why": "OCR failed"}, {"path": "/m/b.mkv", "lang": "fre"}])
    w("dbz_rm_subs.jsonl", [{"path": "/s/e1.mkv", "status": "ok", "tie": True, "detail": "dub None, dropped [3] (no Latino sub left: Bazarr)"},
                            {"path": "/s/e2.mkv", "status": "ok", "tie": True, "detail": "dub 3, dropped [4]"},
                            {"path": "/s/e3.mkv", "status": "ok", "acts": {"3": "es-ES", "4": "es-ES"}},
                            {"path": "/s/e4.mkv", "status": "ok", "acts": {"3": "es-ES", "4": "es-419"}}])
    real, bw.HERE = bw.HERE, d
    try:
        got = set(bw.wanted())
    finally:
        bw.HERE = real
    assert got == {("/m/a.mkv", "eng"), ("/s/e1.mkv", "spa"), ("/s/e3.mkv", "spa")}, got
    print("test_wanted OK")


if __name__ == "__main__":
    test_wanted()
