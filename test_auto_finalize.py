"""The hook's "finalize without a human" flag must survive a restart.

It lived in an in-memory set, so the 13 September reboot left every import
verified but never swapped in -- no artwork, no notification.
"""
import os
import sqlite3
import tempfile

TMP = tempfile.mkdtemp()
os.environ.setdefault("TMDB_API_KEY", "")
os.environ["MEDIA_ROOT"] = TMP
os.environ["MM_DB"] = os.path.join(TMP, "m.db")

import app  # noqa: E402


def connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def main():
    db = os.path.join(TMP, "t.db")
    c = connect(db)
    c.executescript("""
      CREATE TABLE jobs (id INTEGER PRIMARY KEY, movie_id INT, episode_id INT, kind TEXT, status TEXT, exit_code INT);
      CREATE TABLE movies (id INTEGER PRIMARY KEY, folder TEXT, file TEXT, title TEXT, status TEXT,
        output_file TEXT, duration REAL);
      INSERT INTO movies VALUES (1, 'F', 'F.mkv', 'F', 'cleaning', '/x/.F.remux.mkv', 10);
      INSERT INTO jobs VALUES (7, 1, NULL, 'remux', 'done', 0), (8, 1, NULL, 'remux', 'done', 0);
    """)
    app._add_missing_columns(c)
    app._mark_auto_finalize(c, 7)
    c.close()

    # a restart: a new process, a new connection, nothing carried in memory
    c = connect(db)
    assert c.execute("SELECT auto_finalize FROM jobs WHERE id=7").fetchone()[0] == 1, \
        "the flag did not survive a new connection"

    swaps = []
    app._owner_info = lambda conn, kind, oid: dict(conn.execute(
        "SELECT *, 'F' AS out_base FROM movies WHERE id=?", (oid,)).fetchone())
    app._kept_tracks = lambda *a: []
    app._set_owner_status = lambda *a: None
    app._delete_original = lambda conn, kind, oid: swaps.append(oid)
    app._finish_staging = lambda *a: None
    app._notify = lambda *a, **k: None

    app.jobs.verify_output = lambda *a, **k: (False, "bad")
    app._verify_and_finalize(c, "movie", 1, {"id": 7, "kind": "remux"})
    assert swaps == [], "a failed verification must never finalize"
    assert c.execute("SELECT auto_finalize FROM jobs WHERE id=7").fetchone()[0] == 1, \
        "a failed verification consumed the flag"

    app.jobs.verify_output = lambda *a, **k: (True, "ok")
    app._verify_and_finalize(c, "movie", 1, {"id": 7, "kind": "remux"})
    assert swaps == [1], "a verified hook job did not finalize after the restart"
    assert c.execute("SELECT auto_finalize FROM jobs WHERE id=7").fetchone()[0] == 0, \
        "flag not cleared: a second poll would delete again"

    app._verify_and_finalize(c, "movie", 1, {"id": 8, "kind": "remux"})
    assert swaps == [1], "a manual job finalized without anyone asking"
    print("test_auto_finalize OK")


if __name__ == "__main__":
    main()
