"""playback_guard: only a running (not paused) stream counts; whole job trees are selected, nothing else."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import playback_guard as g  # noqa: E402


def test_watching():
    assert not g.watching([{"Client": "JellyStat"}, {"NowPlayingItem": None}])
    assert not g.watching([{"NowPlayingItem": {"Id": "x"}, "PlayState": {"IsPaused": True}}])     # paused for days
    assert g.watching([{"NowPlayingItem": {"Id": "x"}, "PlayState": {"IsPaused": False}}])
    print("test_watching OK")


def test_heavy_trees_takes_descendants_only_of_jobs():
    table = {1: (0, "systemd"),
             10: (1, "uv run python -u f6_series.py --run"), 11: (10, "python3 -u f6_series.py --run"),
             12: (11, "mkvmerge -o /srv/x.mkv"),
             20: (1, "/usr/bin/jellyfin"), 21: (20, "ffmpeg -i /srv/x.mkv"),               # Jellyfin's own transcode
             30: (1, "uvx ffsubsync /srv/m.mkv -i a.srt"), 31: (30, "ffmpeg -loglevel fatal -i /srv/m.mkv"),
             40: (1, "python -u playback_guard.py"),
             50: (1, "/usr/bin/zsh -c grep f6_series.py log"), 51: (50, "grep f6_series.py log")}   # my own shell
    assert g.heavy_trees(table, me=40) == {10, 11, 12, 30, 31}, g.heavy_trees(table, me=40)
    print("test_heavy_trees_takes_descendants_only_of_jobs OK")


def test_proc_table_reads_this_process():
    t = g.proc_table()
    assert os.getpid() in t and "test_playback_guard" in t[os.getpid()][1]
    print("test_proc_table_reads_this_process OK")


def test_disk_rest_is_the_last_20_minutes_of_every_3_hours():
    import time
    at = lambda h, m: time.mktime((2026, 10, 31, h, m, 0, 0, 0, -1))
    assert g.resting(at(2, 40)) and g.resting(at(2, 59)) and g.resting(at(23, 45))
    assert not g.resting(at(2, 39)) and not g.resting(at(3, 0)) and not g.resting(at(12, 0))
    print("test_disk_rest_is_the_last_20_minutes_of_every_3_hours OK")


def test_import_remux_is_heavy_but_a_probe_is_not():
    assert g.HEAVY.search("mkvmerge -o /srv/storage/Movies/X/.X.remux.mkv --title X in.mkv")
    assert not g.HEAVY.search("mkvmerge -J /srv/storage/Movies/X/X.mkv")
    print("test_import_remux_is_heavy_but_a_probe_is_not OK")


if __name__ == "__main__":
    test_disk_rest_is_the_last_20_minutes_of_every_3_hours()
    test_import_remux_is_heavy_but_a_probe_is_not()
    test_watching(); test_heavy_trees_takes_descendants_only_of_jobs(); test_proc_table_reads_this_process()
