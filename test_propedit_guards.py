"""propedit edits the source in place, so its two failure modes are unrecoverable.

1. build_mkvpropedit_chain addresses track:a{N} by position in the FILE, while
   the caller passes tracks sorted by out_order. mkvmerge reorders as it writes
   so the two agree for a remux; mkvpropedit moves nothing, so a reordering
   config stamps each track's metadata onto whichever track sits at that
   position -- and verify_output compares expected-by-out_order against
   got-by-mkv_id, so the swap verifies clean.
2. _build_job_cmd returns the source path as out_path, which _enqueue stores in
   output_file. Both _cancel_job and _reap_stale_jobs remove output_file to
   clear a partial output; for propedit that is the only copy of the film.
"""
import os
import re

os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("TMDB_API_KEY", "x")

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()


def main():
    # 1. propedit writes each position the metadata of the track that actually
    # sits there. Sorting by out_order would stamp a reordered config's labels
    # onto the wrong tracks, in place, over the only copy.
    branch = SRC[SRC.index('if job_kind == "propedit":'):]
    branch = branch[:branch.index('if job_kind == "sample":')]
    assert 'key=lambda t: t["mkv_id"]' in branch and 'key=lambda t: t["out_order"]' not in branch, \
        "propedit orders its mkvpropedit targets by out_order, not by file position"
    assert 'order_key="mkv_id" if job["kind"] == "propedit"' in SRC, \
        "propedit is verified against out_order, which it never applied"

    # 2. neither delete path may run for propedit
    reaper = re.search(r'if job and job\["status"\] == "failed" and job\["kind"\][^\n]*', SRC)
    assert reaper and "propedit" in reaper.group(0), \
        "the stale-job reaper would remove a propedit's source file"

    cancel = SRC[SRC.index("def _cancel_job"):]
    cancel = cancel[:cancel.index("os.remove(owner[\"output_file\"])")]
    assert 'job["kind"] != "propedit"' in cancel, \
        "cancelling a propedit would remove its source file"

    import commands
    # 3. a language with two kept audio tracks must not name both the same
    a = {"out_lang": "eng", "out_name": "", "codec": "AC-3", "type": "audio"}
    b = {"out_lang": "eng", "out_name": "", "codec": "TrueHD Atmos", "type": "audio"}
    n1, n2 = commands._canonical_name(a, [a, b]), commands._canonical_name(b, [a, b])
    assert n1 != n2, f"both English audio tracks named {n1!r}"
    # a lone track keeps the plain label
    assert commands._canonical_name(a, [a]) == "English"
    print("ok")


def test_sdh_naming_is_type_aware():
    """SDH is subtitle vocabulary. A Thunderbolts release tagged its only Spanish
    dub hearing-impaired and the remux shipped an audio track called
    "Español (SDH)"."""
    import commands
    assert commands._canonical_name(
        {"out_lang": "eng", "sdh_flag": 1, "type": "subtitle"}) == "English (SDH)"
    assert commands._canonical_name(
        {"out_lang": "spa", "sdh_flag": 1, "type": "audio"}) == "Español (HI)"
    assert commands._canonical_name(
        {"out_lang": "spa", "type": "audio"}) == "Español"
    print("test_sdh_naming_is_type_aware OK")


def test_verify_honours_order_key():
    """An in-place edit never moves a track, so its expectations line up by file
    position. Checking a reordered config by out_order would compare the English
    track against the Spanish one that still sits in front of it."""
    import os
    import jobs
    got = [
        {"type": "video", "mkv_id": 0, "lang": "eng", "default_flag": 1, "forced_flag": 0},
        {"type": "audio", "mkv_id": 1, "lang": "spa", "default_flag": 0, "forced_flag": 0},
        {"type": "audio", "mkv_id": 2, "lang": "eng", "default_flag": 1, "forced_flag": 0},
    ]
    kept = [
        {"type": "video", "mkv_id": 0, "out_order": 0, "out_lang": "eng", "out_default": 1, "out_forced": 0},
        {"type": "audio", "mkv_id": 1, "out_order": 1, "out_lang": "spa", "out_default": 0, "out_forced": 0},
        {"type": "audio", "mkv_id": 2, "out_order": 0, "out_lang": "eng", "out_default": 1, "out_forced": 0},
    ]
    real_inspect, real_exists = jobs.inspect_file, os.path.exists
    jobs.inspect_file = lambda p: {"tracks": got, "duration": None, "video_duration": None}
    os.path.exists = lambda p: True if p == "X" else real_exists(p)
    try:
        ok, msg = jobs.verify_output("X", kept, order_key="mkv_id")
        assert ok, f"file-order verification should pass: {msg}"
        ok, _ = jobs.verify_output("X", kept, order_key="out_order")
        assert not ok, "out_order verification should fail on a file that was never reordered"
    finally:
        jobs.inspect_file, os.path.exists = real_inspect, real_exists
    print("test_verify_honours_order_key OK")


def test_refresh_after_propedit_clears_the_stale_row():
    """path_unchanged is mtime-based, so a retag can leave the row describing the
    labels the file had before the edit -- and preflight then refuses the next
    job on a title that is already correct."""
    import sqlite3
    import app
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, episode_id INT, mkv_id INT,
        type TEXT, codec TEXT, lang TEXT, name TEXT, channels INT,
        default_flag INT, forced_flag INT, ext_path TEXT, keep INT,
        out_order INT, out_lang TEXT, out_default INT, out_forced INT, out_name TEXT,
        sdh_flag INT DEFAULT 0, commentary_flag INT DEFAULT 0);
      INSERT INTO tracks VALUES
        (1,NULL,7,0,'video','HEVC','und','',NULL,1,0,NULL,1,0,'eng',1,0,'',0,0),
        (2,NULL,7,1,'audio','E-AC-3','eng','RARBG',6,0,0,NULL,1,0,'eng',1,0,'',0,0);
    """)
    app._refresh_after_propedit(c, "episode", 7)
    rows = {r["id"]: r for r in c.execute("SELECT * FROM tracks")}
    assert rows[1]["lang"] == "eng", "video lang still stale; preflight will refuse the next job"
    assert rows[2]["default_flag"] == 1, "default flag not written back"
    assert rows[2]["name"] == "English", f"release junk survived: {rows[2]['name']!r}"
    assert rows[1]["name"] == "", "video track should carry no name"
    print("test_refresh_after_propedit_clears_the_stale_row OK")


if __name__ == "__main__":
    main()
    test_sdh_naming_is_type_aware()
    test_verify_honours_order_key()
    test_refresh_after_propedit_clears_the_stale_row()
