"""The notification is read on a phone by someone deciding whether to sit down.
"3840x1608 · eng/spa/eng+atmos" was written for logs, not for that."""
import os, sqlite3, sys, tempfile

sys.path.insert(0, os.path.expanduser("~/media-manager"))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
import app


def main():
    assert app._lang_phrase([]) == "sin audio"
    assert app._lang_phrase(["eng"]) == "Inglés"
    assert app._lang_phrase(["spa", "eng"]) == "Español y inglés"
    assert app._lang_phrase(["spa", "eng", "jpn"]) == "Español, inglés y japonés"
    assert app._lang_phrase(["zzz"]) == "Zzz", "un idioma que no conozco pasa tal cual"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, type TEXT,
                           out_lang TEXT, codec TEXT, keep INT, out_order INT);
      INSERT INTO tracks (movie_id, type, out_lang, codec, keep, out_order) VALUES
        (1,'audio','eng','E-AC-3',1,0), (1,'audio','spa','E-AC-3',1,1),
        (1,'audio','eng','TrueHD Atmos',1,2),
        (2,'audio','eng','AAC',1,0);
    """)

    uhd_dv = {"width": 3840, "height": 1608, "bitrate": 20_000_000,
              "video_codec": "hevc", "hdr": "DV+HDR10"}
    got = app._friendly_detail(conn, "movie", 1, uhd_dv)
    assert got == "4K · Dolby Vision · Inglés y español · Atmos", got  # orden de pista: el original primero

    fhd_sdr = {"width": 1920, "height": 1080, "bitrate": 9_000_000,
               "video_codec": "h264", "hdr": "SDR"}
    got = app._friendly_detail(conn, "movie", 2, fhd_sdr)
    assert got == "Full HD · Inglés", got

    # sin datos de vídeo: no inventa una resolución
    got = app._friendly_detail(conn, "movie", 2, {"width": None, "hdr": None})
    assert got == "Inglés", got

    print("test_notify_text OK")


def test_jellyfin_lookup_uses_title():
    """Jellyfin matches an item's NAME. The file is "Superman (2025).mkv" and the
    item is called "Superman", so searching the filename stem found nothing and
    every title was announced as "falta indexar en Jellyfin"."""
    import inspect, app
    src = inspect.getsource(app._jellyfin_refresh)
    assert "title or os.path.splitext(file_name)[0]" in src, \
        "la búsqueda debe partir del título, con el nombre de fichero como respaldo"
    assert "title=None" in inspect.signature(app._jellyfin_refresh).__str__() or \
        "title" in inspect.signature(app._jellyfin_refresh).parameters
    print("test_jellyfin_lookup_uses_title OK")


if __name__ == "__main__":
    main()
    test_jellyfin_lookup_uses_title()
