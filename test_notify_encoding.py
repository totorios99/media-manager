"""ntfy gets an ASCII Title (RFC 2047) so "Subtítulo" does not arrive as mojibake; a burst collapses to one notice."""
import base64, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
os.environ.setdefault("TMDB_API_KEY", "x")
os.environ["MM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
import app  # noqa: E402


def test_non_ascii_title_is_encoded_word():
    seen = {}
    real = app.urllib.request.urlopen
    app.urllib.request.urlopen = lambda req, timeout=0: (seen.update(h=dict(req.header_items())) or type("R", (), {"read": lambda s: b""})())
    try:
        app._notify("Subtítulo sin procesar", "x")
    finally:
        app.urllib.request.urlopen = real
    t = seen["h"]["Title"]
    assert t.isascii() and t.startswith("=?UTF-8?B?"), t
    assert base64.b64decode(t[10:-2]).decode() == "Subtítulo sin procesar"
    print("test_non_ascii_title_is_encoded_word OK")


def test_burst_collapses_to_one_notice():
    sent = []
    real = app._notify
    app._notify = lambda title, body, **k: sent.append(body)
    try:
        app._SUBFIX_NOTES.update(last=0.0, held=0)
        for i in range(100):
            app._notify_subs_burst("Subtítulo sin procesar", f"f{i}")
    finally:
        app._notify = real
    assert len(sent) == 1 and sent[0] == "f0", sent
    assert app._SUBFIX_NOTES["held"] == 99
    print("test_burst_collapses_to_one_notice OK")


if __name__ == "__main__":
    test_non_ascii_title_is_encoded_word(); test_burst_collapses_to_one_notice()
