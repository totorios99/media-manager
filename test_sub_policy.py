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
            "default_flag, forced_flag, keep, sdh_flag) VALUES (?,1,?,?,?,?,'',0,?,1,?)",
            (i + 1, i, t["type"], t["codec"], t["lang"], t.get("forced", 0), t.get("sdh", 0)))
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


def order(conn):
    """Kept subtitle tracks in output order: [(lang, forced, default)]."""
    rows = conn.execute("SELECT lang, out_forced, out_default, out_order FROM tracks "
                        "WHERE type='subtitle' AND keep=1 ORDER BY out_order").fetchall()
    return [(r["lang"], r["out_forced"], r["out_default"]) for r in rows]


def test_standard_order_latino_english_latino_forced():
    """SUBTITLES-PLAN: Latino full, English full, Latino forced -- whatever the source order."""
    conn = build([A("eng"), SRT("eng"), SRT("spa", forced=1), SRT("spa")])
    scan.suggest_tracks(conn, 1)
    assert order(conn) == [("spa", 0, 1), ("eng", 0, 0), ("spa", 1, 0)], order(conn)


def test_no_english_forced_and_no_other_languages():
    conn = build([A("fra"), SRT("eng", forced=1), SRT("eng"), SRT("fra"), SRT("spa")], original_language="fr")
    scan.suggest_tracks(conn, 1)
    assert [(l, f) for l, f, _ in order(conn)] == [("spa", 0), ("eng", 0)], order(conn)


def test_normal_english_beats_sdh():
    conn = build([A("eng"), SRT("eng", sdh=1), SRT("eng"), SRT("spa")])
    scan.suggest_tracks(conn, 1)
    kept_eng = [r for r in conn.execute("SELECT id FROM tracks WHERE lang='eng' AND type='subtitle' AND keep=1")]
    assert [r["id"] for r in kept_eng] == [3], "la pista de dialogo normal, no la SDH"


def test_sdh_is_used_when_it_is_the_only_english():
    conn = build([A("eng"), SRT("eng", sdh=1), SRT("spa")])
    scan.suggest_tracks(conn, 1)
    assert ("eng", 0, 0) in order(conn)


def test_latino_full_is_default_with_english_audio():
    conn = build([A("eng"), SRT("spa", forced=1), SRT("spa"), SRT("eng")])
    scan.suggest_tracks(conn, 1)
    assert order(conn)[0] == ("spa", 0, 1), "el espanol completo arranca aunque el audio sea ingles"
    assert all(d == 0 for _, _, d in order(conn)[1:])


def test_default_is_latino_full_with_foreign_audio():
    conn = build([A("jpn"), SRT("spa", forced=1), SRT("spa"), SRT("eng")], original_language="ja")
    scan.suggest_tracks(conn, 1)
    assert order(conn)[0] == ("spa", 0, 1)


def test_no_default_subtitle_when_spanish_audio_plays():
    conn = build([A("spa"), SRT("spa", forced=1), SRT("spa"), SRT("eng")], original_language="es")
    scan.suggest_tracks(conn, 1)
    assert all(d == 0 for _, _, d in order(conn)), order(conn)


def test_no_default_subtitle_when_animation_starts_on_the_spanish_dub():
    conn = build([A("jpn"), A("spa"), SRT("spa"), SRT("eng")], original_language="ja")
    conn.execute("UPDATE movies SET animation=1")
    conn.commit()
    scan.suggest_tracks(conn, 1)
    assert all(d == 0 for _, _, d in order(conn)), order(conn)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("test_sub_policy OK")
