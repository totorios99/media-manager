"""An encode loses quality on purpose, so finalizing it moves the original to .recycle instead of deleting it."""
import os
import sqlite3
import tempfile

TMP = tempfile.mkdtemp()
os.environ.setdefault("TMDB_API_KEY", "")
os.environ["MEDIA_ROOT"] = os.path.join(TMP, "Movies")
os.environ["SHOWS_ROOT"] = os.path.join(TMP, "Shows")
os.environ["MM_DB_PATH"] = os.path.join(TMP, "m.db")
os.environ["RECYCLE_DIR"] = os.path.join(TMP, ".recycle")

import app  # noqa: E402


def main():
    folder = os.path.join(TMP, "Movies", "F (2000)")
    os.makedirs(folder)
    old, out = os.path.join(folder, "F (2000).mkv"), os.path.join(folder, ".F (2000).hevc.mkv")
    open(old, "wb").write(b"o" * 2000)
    open(out, "wb").write(b"n" * 1000)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE movies (id INTEGER PRIMARY KEY, file TEXT, output_file TEXT, updated_at TEXT);
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, episode_id INT, ext_path TEXT);
      CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
      INSERT INTO movies VALUES (1, 'F (2000).mkv', NULL, NULL);
    """)
    app._owner_info = lambda conn, kind, oid: {"id": 1, "status": "clean", "folder": "F (2000)",
                                               "file": "F (2000).mkv", "output_file": out, "out_base": "F (2000)"}
    app._reinspect_in_place = lambda *a, **k: None
    app._announce_ready = lambda *a, **k: None
    app._delete_original(c, "movie", 1)
    kept = os.path.join(TMP, ".recycle", "Movies", "F (2000)", "F (2000).mkv")
    assert open(kept, "rb").read() == b"o" * 2000, "original not in .recycle"
    assert open(os.path.join(folder, "F (2000).mkv"), "rb").read() == b"n" * 1000, "encode not in place"
    assert app._lane("encode") == "encode" and app._lane("remux") == app._lane("propedit") == "main"
    print("test_encode_recycle OK")


if __name__ == "__main__":
    main()
