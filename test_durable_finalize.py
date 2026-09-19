"""The output must be fsynced before the original is deleted.

Verification reads the remux through the page cache, and on a disk that writes at
40 MB/s the kernel can hold gigabytes of dirty pages. A power cut right after
_delete_original removed the source would then lose the output too. Only the
ORDER matters here: fsync(out) strictly before remove(old).
"""
import os
import sqlite3
import tempfile

TMP = tempfile.mkdtemp()
os.environ.setdefault("TMDB_API_KEY", "")
os.environ["MEDIA_ROOT"] = TMP
os.environ["SHOWS_ROOT"] = TMP
os.environ["MM_DB_PATH"] = os.path.join(TMP, "m.db")

import app  # noqa: E402


def main():
    folder = os.path.join(TMP, "F")
    os.makedirs(folder)
    old = os.path.join(folder, "F.mkv")
    out = os.path.join(folder, ".F.remux.mkv")
    open(old, "wb").write(b"o" * 2000)
    open(out, "wb").write(b"n" * 1000)

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE movies (id INTEGER PRIMARY KEY, file TEXT, output_file TEXT, updated_at TEXT);
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, episode_id INT, ext_path TEXT);
      CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
      INSERT INTO movies VALUES (1, 'F.mkv', NULL, NULL);
    """)

    app._owner_info = lambda conn, kind, oid: {
        "id": 1, "status": "clean", "folder": "F", "file": "F.mkv",
        "output_file": out, "out_base": "F"}
    app._reinspect_in_place = lambda *a, **k: None
    app._announce_ready = lambda *a, **k: None

    events = []
    real_fsync, real_remove = os.fsync, os.remove

    def fsync(fd):
        events.append(("fsync", os.readlink(f"/proc/self/fd/{fd}")))
        return real_fsync(fd)

    def remove(p, *a, **k):
        events.append(("remove", p))
        return real_remove(p, *a, **k)

    app.os.fsync, app.os.remove = fsync, remove
    try:
        app._delete_original(c, "movie", 1)
    finally:
        app.os.fsync, app.os.remove = real_fsync, real_remove

    kinds = [(k, os.path.basename(p)) for k, p in events]
    assert ("fsync", ".F.remux.mkv") in kinds, f"the output was never fsynced: {kinds}"
    assert ("remove", "F.mkv") in kinds, f"the original was not removed: {kinds}"
    assert kinds.index(("fsync", ".F.remux.mkv")) < kinds.index(("remove", "F.mkv")), \
        f"the original was deleted BEFORE the output reached the disk: {kinds}"
    assert os.path.exists(os.path.join(folder, "F.mkv")), "the output must end up as the final file"
    print("test_durable_finalize OK")


if __name__ == "__main__":
    main()
