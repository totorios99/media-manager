"""After a remux replaces the file, the rows must describe the NEW file.

Nothing re-read it, so the tracks table kept describing the deleted original --
Inside Out 2 carried 41 rows for a file with 8 -- and because finalizing stamps
updated_at to now, path_unchanged then skipped the file forever.
"""
import os
import sqlite3
import subprocess
import tempfile

TMP = tempfile.mkdtemp()
os.environ.setdefault("TMDB_API_KEY", "")
os.environ["MEDIA_ROOT"] = TMP
os.environ["MM_DB"] = os.path.join(TMP, "m.db")

import app  # noqa: E402
import scan  # noqa: E402


def make_mkv(path, langs):
    """A tiny real Matroska with one video track plus one audio per language."""
    args = ["ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1"]
    for _ in langs:
        args += ["-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1"]
    args += ["-map", "0:v"]
    for i, _ in enumerate(langs, start=1):
        args += ["-map", f"{i}:a"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]
    for i, lang in enumerate(langs):
        args += [f"-metadata:s:a:{i}", f"language={lang}"]
    args += [path]
    subprocess.run(args, check=True, capture_output=True)


def main():
    folder = os.path.join(TMP, "Film (2024)")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "Film (2024).mkv")

    conn = sqlite3.connect(os.path.join(TMP, "t.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE movies (id INTEGER PRIMARY KEY, folder TEXT, file TEXT, clean_title TEXT,
        title TEXT, year INT, original_language TEXT, video_codec TEXT, width INT, height INT,
        bitrate INT, duration REAL, size_bytes INT, hdr TEXT, status TEXT, output_file TEXT,
        updated_at TEXT, animation INT DEFAULT 0, atmos INT DEFAULT 0);
      CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id INT, episode_id INT,
        mkv_id INT, type TEXT, codec TEXT, lang TEXT, name TEXT, channels INT,
        default_flag INT DEFAULT 0, forced_flag INT DEFAULT 0, ext_path TEXT,
        keep INT DEFAULT 1, out_order INT DEFAULT 0, out_lang TEXT DEFAULT '',
        out_default INT DEFAULT 0, out_forced INT DEFAULT 0, out_name TEXT DEFAULT '',
        sdh_flag INT DEFAULT 0, commentary_flag INT DEFAULT 0);
      INSERT INTO movies (id, folder, file, clean_title, title, year, original_language, status)
        VALUES (1, 'Film (2024)', 'Film (2024).mkv', 'Film', 'Film', 2024, 'en', 'clean');
    """)
    # the pre-remux reality: four audio tracks, three of them unwanted
    for mkv_id, lang in ((0, "und"), (1, "eng"), (2, "spa"), (3, "fre"), (4, "ita")):
        ttype = "video" if mkv_id == 0 else "audio"
        conn.execute("INSERT INTO tracks (movie_id, mkv_id, type, codec, lang, name, keep) "
                     "VALUES (1,?,?,'x',?,'',1)", (mkv_id, ttype, lang))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM tracks").fetchone()[0] == 5

    # what the remux actually produced
    make_mkv(path, ["eng", "spa"])
    app._reinspect_in_place(conn, "movie", 1, path)

    rows = conn.execute("SELECT * FROM tracks ORDER BY mkv_id").fetchall()
    langs = [r["lang"] for r in rows if r["type"] == "audio"]
    assert len(rows) == 3, f"rows still describe the deleted original: {len(rows)} rows"
    assert langs == ["eng", "spa"], f"wrong audio rows kept: {langs}"
    m = conn.execute("SELECT * FROM movies WHERE id=1").fetchone()
    assert m["size_bytes"] == os.path.getsize(path), "size still from the old file"
    assert m["width"] == 64, f"dimensions not refreshed: {m['width']}"
    print("test_reinspect OK")


if __name__ == "__main__":
    main()
