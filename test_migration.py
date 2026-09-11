"""Additive columns must reach a database that is already stamped.

They used to sit past _migrate's `user_version >= 1` early return, so every
column added after the stamp silently never appeared: `animation` was missing
and suggest_tracks died with "no such column: animation" on the live library.
"""
import os
import sqlite3
import tempfile

os.environ.setdefault("TMDB_API_KEY", "")
TMP = tempfile.mkdtemp()
os.environ.setdefault("MEDIA_ROOT", TMP)
os.environ["MM_DB"] = os.path.join(TMP, "m.db")

import app  # noqa: E402


def main():
    db = os.path.join(TMP, "stamped.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE shows (id INTEGER PRIMARY KEY, folder TEXT);
        CREATE TABLE movies (id INTEGER PRIMARY KEY, folder TEXT);
        CREATE TABLE episodes (id INTEGER PRIMARY KEY, folder TEXT);
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, episode_id INT);
        PRAGMA user_version=1;
    """)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    app._migrate(conn)          # returns early: the database is stamped
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(shows)")}
    assert "animation" not in cols, "test premise broken: _migrate no longer returns early"

    app._add_missing_columns(conn)
    for table, col in (("shows", "animation"), ("movies", "animation"),
                       ("movies", "atmos"), ("episodes", "atmos"),
                       ("tracks", "sdh_flag"), ("tracks", "commentary_flag")):
        got = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert col in got, f"{table}.{col} never reached a stamped database"

    # idempotent: a second pass must not raise "duplicate column name"
    app._add_missing_columns(conn)
    print("test_migration OK")


if __name__ == "__main__":
    main()
