"""_upsert_tracks must follow track identity, not mkv_id.

Regression: an external remux that inserts an audio track shifts every later
index. Keyed on mkv_id, each row kept its saved config while describing a
different physical track -- keep flags landed on the wrong tracks.
"""
import sqlite3

import scan

SCHEMA = """
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER, episode_id INTEGER, mkv_id INTEGER,
    type TEXT NOT NULL, codec TEXT, lang TEXT, name TEXT,
    channels INTEGER, default_flag INTEGER DEFAULT 0, forced_flag INTEGER DEFAULT 0,
    ext_path TEXT, keep INTEGER DEFAULT 1, out_order INTEGER DEFAULT 0,
    out_lang TEXT DEFAULT '', out_default INTEGER DEFAULT 0, out_forced INTEGER DEFAULT 0,
    out_name TEXT DEFAULT ''
)
"""


def _t(mkv_id, type_, lang, codec, name, channels=None, default_flag=0, forced_flag=0):
    return dict(mkv_id=mkv_id, type=type_, lang=lang, codec=codec, name=name,
                channels=channels, default_flag=default_flag, forced_flag=forced_flag)


def test():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    before = [
        _t(0, "video", "und", "V_MPEGH/ISO/HEVC", "Encoded by r00t", default_flag=1),
        _t(1, "audio", "eng", "A_TRUEHD", "Surround 7.1 Atmos", 8, default_flag=1),
        _t(2, "audio", "eng", "A_AC3", "Commentary", 2),
        _t(3, "subtitle", "eng", "S_TEXT/UTF8", "English"),
        _t(4, "subtitle", "spa", "S_HDMV/PGS", "Spanish (Latin American)"),
    ]
    scan._upsert_tracks(conn, 1, before, [], "en")

    # user config: drop the commentary, keep the Spanish sub and mark it default
    conn.execute("UPDATE tracks SET keep=0 WHERE name='Commentary'")
    conn.execute("UPDATE tracks SET out_default=1 WHERE name='Spanish (Latin American)'")
    conn.commit()
    spa_row_id = conn.execute("SELECT id FROM tracks WHERE name='Spanish (Latin American)'").fetchone()["id"]

    # external remux inserts a Spanish audio track at index 2 -- everything after shifts
    after = [
        _t(0, "video", "und", "V_MPEGH/ISO/HEVC", "Encoded by r00t", default_flag=1),
        _t(1, "audio", "eng", "A_TRUEHD", "Surround 7.1 Atmos", 8, default_flag=1),
        _t(2, "audio", "spa", "A_EAC3", "Surround 7.1", 8),
        _t(3, "audio", "eng", "A_AC3", "Commentary", 2),
        _t(4, "subtitle", "eng", "S_TEXT/UTF8", "English"),
        _t(5, "subtitle", "spa", "S_HDMV/PGS", "Spanish (Latin American)"),
    ]
    scan._upsert_tracks(conn, 1, after, [], "en")

    rows = {r["name"]: r for r in conn.execute("SELECT * FROM tracks WHERE movie_id=1")}
    assert len(rows) == 6, f"expected 6 tracks, got {len(rows)}"

    spa_sub = rows["Spanish (Latin American)"]
    assert spa_sub["id"] == spa_row_id, "config row must be reused, not replaced"
    assert spa_sub["mkv_id"] == 5, f"mkv_id must follow the shift, got {spa_sub['mkv_id']}"
    assert spa_sub["type"] == "subtitle" and spa_sub["out_default"] == 1, "saved config must stay put"

    assert rows["Commentary"]["keep"] == 0, "keep=0 must stay on the commentary"
    assert rows["Commentary"]["type"] == "audio" and rows["Commentary"]["mkv_id"] == 3

    new_audio = rows["Surround 7.1"]
    assert new_audio["type"] == "audio" and new_audio["lang"] == "spa", "new track misread"
    assert new_audio["keep"] == 1, "a newly seen track defaults to keep"

    # a dropped track's row goes away instead of sliding onto a neighbour
    scan._upsert_tracks(conn, 1, [t for t in after if t["name"] != "Commentary"], [], "en")
    left = {r["name"] for r in conn.execute("SELECT name FROM tracks WHERE movie_id=1")}
    assert "Commentary" not in left and len(left) == 5, f"stale row not pruned: {left}"
    print("ok")


if __name__ == "__main__":
    test()
