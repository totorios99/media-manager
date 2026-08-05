"""ponytail: self-check for rename_movie's sidecar sweep -- `python3 test_rename.py`.

Regression guard for the Avatar Aang bug: the encode was written as
"Title (Year).hevc.mkv" while the source stem was still "Title", so the sweep
appended the year a second time -> "Title (Year) (Year).hevc.mkv", and
movies.output_file was left pointing at a name that no longer existed, which
made delete-original 404 and strand the 19.9 GB original.

Runs against a temp MEDIA_ROOT and a temp DB; touches nothing real.
"""
import os
import sqlite3
import tempfile

tmp = tempfile.mkdtemp(prefix="mm-rename-test-")
os.environ["MM_DB_PATH"] = os.path.join(tmp, "test.db")
os.environ["MEDIA_ROOT"] = os.path.join(tmp, "Movies")
os.makedirs(os.environ["MEDIA_ROOT"])

import app  # noqa: E402  -- must follow the env vars above

assert app.MEDIA_ROOT == os.environ["MEDIA_ROOT"], "test would hit the real library"
app.init_db()


def setup(folder, files, output_file=None):
    os.makedirs(os.path.join(app.MEDIA_ROOT, folder), exist_ok=True)
    for f in files:
        open(os.path.join(app.MEDIA_ROOT, folder, f), "w").close()
    conn = app.get_db()
    cur = conn.execute(
        "INSERT INTO movies (folder, file, title, year, status, output_file) VALUES (?,?,?,?,?,?)",
        (folder, files[0], "Avatar Aang: The Last Airbender", 2026, "clean",
         os.path.join(app.MEDIA_ROOT, folder, output_file) if output_file else None),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def state(mid):
    conn = app.get_db()
    row = conn.execute("SELECT folder, file, output_file FROM movies WHERE id=?", (mid,)).fetchone()
    conn.close()
    return row


TARGET = "Avatar Aang The Last Airbender (2026)"

# -- the bug: encode already carries the year, source stem does not
mid = setup("Avatar Aang The Last Airbender",
            ["Avatar Aang The Last Airbender.mkv",
             "Avatar Aang The Last Airbender (2026).hevc.mkv"],
            output_file="Avatar Aang The Last Airbender (2026).hevc.mkv")
app.rename_movie(mid)

folder = os.path.join(app.MEDIA_ROOT, TARGET)
names = sorted(os.listdir(folder))
assert names == [f"{TARGET}.hevc.mkv", f"{TARGET}.mkv"], names
assert not any("(2026) (2026)" in n for n in names), f"year doubled: {names}"

row = state(mid)
assert row["folder"] == TARGET, row["folder"]
assert row["file"] == f"{TARGET}.mkv", row["file"]
# delete-original resolves the file to KEEP through output_file -- a stale value
# here is what stranded the original on disk
assert os.path.exists(row["output_file"]), f"output_file points at nothing: {row['output_file']}"

# -- the ordinary case must still rewrite the suffix and follow output_file
mid2 = setup("Some Movie",
             ["Some Movie.mkv", "Some Movie.hevc.mkv", "Some Movie.eng.srt"],
             output_file="Some Movie.hevc.mkv")
conn = app.get_db()
conn.execute("UPDATE movies SET title='Some Movie', year=2020 WHERE id=?", (mid2,))
conn.commit()
conn.close()
app.rename_movie(mid2)

names2 = sorted(os.listdir(os.path.join(app.MEDIA_ROOT, "Some Movie (2020)")))
assert names2 == ["Some Movie (2020).eng.srt",
                  "Some Movie (2020).hevc.mkv",
                  "Some Movie (2020).mkv"], names2
row2 = state(mid2)
assert row2["output_file"].endswith("Some Movie (2020).hevc.mkv"), row2["output_file"]
assert os.path.exists(row2["output_file"]), row2["output_file"]

print("ok")
