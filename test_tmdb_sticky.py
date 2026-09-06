"""A manual TMDB correction must survive the next scan.

upsert_movie re-searched TMDB on every scan, so a corrected identity was
silently reverted: "Enemy (2013)" is TMDB 181886, released 2014-03-14, and a
year-strict search on the folder's 2013 returns "Class Enemy" instead. The
wrong title then rode into the file on the next propedit.
"""
import os
import sqlite3
import tempfile

os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("TMDB_API_KEY", "x")

import scan


def main():
    root = tempfile.mkdtemp()
    folder = "Enemy (2013)"
    os.makedirs(os.path.join(root, folder))
    open(os.path.join(root, folder, "Enemy (2013).mkv"), "wb").write(b"\x00" * 1024)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE movies (id INTEGER PRIMARY KEY, folder TEXT, file TEXT,
        clean_title TEXT, guess_year INT, tmdb_id INT, title TEXT, year INT,
        original_language TEXT, poster_path TEXT, container_title TEXT,
        video_codec TEXT, width INT, height INT, bitrate INT, duration REAL,
        size_bytes INT, hdr TEXT, atmos INT DEFAULT 0, status TEXT,
        output_file TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, episode_id INT,
        mkv_id INT, type TEXT, codec TEXT, lang TEXT, name TEXT, channels INT,
        default_flag INT, forced_flag INT, sdh_flag INT DEFAULT 0,
        commentary_flag INT DEFAULT 0, ext_path TEXT, keep INT, out_order INT,
        out_lang TEXT, out_default INT, out_forced INT, out_name TEXT)""")
    conn.execute("INSERT INTO movies (id, folder, file, tmdb_id, title, year) "
                 "VALUES (1, ?, ?, 181886, 'Enemy', 2013)", (folder, "Enemy (2013).mkv"))
    conn.commit()

    called = []
    scan.tmdb_search = lambda *a, **k: called.append(a) or {
        "tmdb_id": 223895, "title": "Class Enemy", "year": 2013,
        "original_language": "sl", "poster_path": "/wrong.jpg"}
    scan.inspect_file = lambda p: {
        "container_title": None, "video_codec": "hevc", "width": 1920, "height": 800,
        "bitrate": 2_000_000, "duration": 5460.0, "size_bytes": 1024, "hdr": "SDR",
        "atmos": 0, "tracks": []}
    scan.find_external_subs = lambda p: []

    scan.upsert_movie(conn, root, folder, "x")
    row = conn.execute("SELECT tmdb_id, title FROM movies WHERE id=1").fetchone()
    assert not called, "tmdb_search ran on a row that already had an id"
    assert row["tmdb_id"] == 181886, f"tmdb_id was overwritten: {row['tmdb_id']}"
    assert row["title"] == "Enemy", f"title was overwritten: {row['title']}"
    print("ok")


if __name__ == "__main__":
    main()
