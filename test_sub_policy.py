"""Subtitle policy: text over bitmap, and the default follows the audio."""
import os, sqlite3, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp()
os.environ["MEDIA_ROOT"] = _TMP
os.environ.setdefault("TMDB_API_KEY", "x")
os.environ["MM_DB_PATH"] = os.path.join(_TMP, "t.db")
import app  # noqa: E402  -- owns the schema
import scan  # noqa: E402

app.init_db()


def build(tracks, original_language="en"):
    conn = sqlite3.connect(os.environ["MM_DB_PATH"])
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM tracks")
    conn.execute("DELETE FROM movies")
    conn.execute("INSERT INTO movies (id, folder, title, original_language, status) "
                 "VALUES (1, 'f', 't', ?, 'unprocessed')", (original_language,))
    for i, t in enumerate(tracks):
        conn.execute(
            "INSERT INTO tracks (id, movie_id, mkv_id, type, codec, lang, name, "
            "default_flag, forced_flag, keep) VALUES (?,1,?,?,?,?,'',0,?,1)",
            (i + 1, i, t["type"], t["codec"], t["lang"], t.get("forced", 0)))
    conn.commit()
    return conn


def kept(conn):
    return {r["id"]: r for r in conn.execute(
        "SELECT id, type, codec, lang, keep, out_default, out_forced FROM tracks")}


A = lambda lang: {"type": "audio", "codec": "AC-3", "lang": lang}
SRT = lambda lang, **k: dict({"type": "subtitle", "codec": "SubRip/SRT", "lang": lang}, **k)
PGS = lambda lang, **k: dict({"type": "subtitle", "codec": "HDMV PGS", "lang": lang}, **k)
VOB = lambda lang: {"type": "subtitle", "codec": "VobSub", "lang": lang}


def test_text_beats_bitmap():
    """Same language in both formats: the .srt is kept, the PGS dropped."""
    conn = build([A("eng"), SRT("spa"), PGS("spa")])
    scan.suggest_tracks(conn, 1)
    r = kept(conn)
    assert r[2]["keep"] == 1, "el SRT se queda"
    assert r[3]["keep"] == 0, "el PGS sobra cuando hay texto"


def test_lone_bitmap_survives():
    """The 70 films whose Spanish exists only as PGS keep it."""
    conn = build([A("eng"), SRT("eng"), PGS("spa")])
    scan.suggest_tracks(conn, 1)
    r = kept(conn)
    assert r[3]["keep"] == 1, "un PGS sin SRT equivalente es el unico espanol que hay"


def test_vobsub_loses_to_srt():
    conn = build([A("eng"), SRT("spa"), VOB("spa")])
    scan.suggest_tracks(conn, 1)
    assert kept(conn)[3]["keep"] == 0


def test_default_follows_audio_foreign():
    """Japanese audio, no dub: the full Spanish subtitle starts, not the forced."""
    conn = build([A("jpn"), SRT("spa", forced=1), SRT("spa"), SRT("eng")],
                 original_language="ja")
    scan.suggest_tracks(conn, 1)
    r = kept(conn)
    full = r[3]
    assert full["out_default"] == 1, "el espanol completo arranca con audio japones"
    assert r[2]["out_default"] == 0, "el forzado deja de ser el de por defecto"


def test_default_stays_forced_for_english():
    """English audio: forced Spanish is enough, he reads English."""
    conn = build([A("eng"), SRT("spa", forced=1), SRT("spa")])
    scan.suggest_tracks(conn, 1)
    r = kept(conn)
    assert r[2]["out_default"] == 1 and r[2]["out_forced"] == 1
    assert r[3]["out_default"] == 0


def test_default_stays_forced_for_spanish_audio():
    conn = build([A("spa"), SRT("spa", forced=1), SRT("spa")], original_language="es")
    scan.suggest_tracks(conn, 1)
    assert kept(conn)[2]["out_default"] == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("test_sub_policy OK")
