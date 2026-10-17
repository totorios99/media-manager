"""ocr_recover.pending: a .sup already tried (any verdict) is not OCR'd again."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_recover as orc  # noqa: E402


def test_pending_skips_every_logged_verdict():
    review = [{"path": "a.mkv", "track": 3, "lang": "spa", "sup": "/r/a.3.sup"},
              {"path": "b.mkv", "track": 2, "lang": "eng", "sup": "/r/b.2.sup"},
              {"path": "c.mkv", "track": 4, "lang": "eng", "sup": "/r/c.4.sup"},
              {"path": "d.mkv", "track": 5, "lang": "fre", "sup": "/r/d.5.sup"},
              {"path": "e.mkv", "track": 6, "lang": "eng", "sup": "/r/e.6.idx"}]
    logged = [{"path": "a.mkv", "track": 3, "status": "failed"}, {"path": "b.mkv", "track": 2, "status": "ok"}]
    assert [r["path"] for r in orc.pending(review, logged)] == ["c.mkv"]
    print("test_pending_skips_every_logged_verdict OK")


if __name__ == "__main__":
    test_pending_skips_every_logged_verdict()
