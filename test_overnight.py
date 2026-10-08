"""overnight.py: the night window and the disk-gone guard."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overnight as ov  # noqa: E402

IN_WINDOW = ov.in_window          # the tests below replace ov.in_window; keep the real one


def test_window_00_to_07():
    assert [IN_WINDOW(h, "0-7") for h in (0, 3, 6, 7, 12, 23)] == [True, True, True, False, False, False]


def test_window_wraps_midnight():
    assert [IN_WINDOW(h, "22-6") for h in (21, 22, 23, 0, 5, 6, 12)] == [False, True, True, True, True, False, False]


def test_missing_disk_stops_everything_and_alerts_once():
    stopped, sent = [], []
    ov.mounted = lambda: False
    ov.stop = lambda name: stopped.append(name)
    ov.alert = lambda st, key, *a, **k: sent.append(key)
    r = ov.cycle({"alerts": {}, "svc_restart_at": 0, "restarts": {}, "done": {}})
    assert r["mounted"] is False and stopped == ["queue", "variant"] and sent == ["mount"], (r, stopped, sent)


def test_outside_window_nothing_is_started():
    started = []
    ov.mounted = lambda: True
    ov.in_window = lambda *a, **k: False
    ov.start = lambda name, args: started.append(name)
    ov.stop = lambda name: None
    ov.alert = lambda *a, **k: None
    ov.job_done = lambda name, st: False
    ov.shutil.disk_usage = lambda p: type("U", (), {"free": 2e12})()
    ov.subprocess.run = lambda *a, **k: type("R", (), {"stdout": "active\n"})()
    r = ov.cycle({"alerts": {}, "svc_restart_at": 0, "restarts": {}, "done": {}})
    assert started == [] and r["window"] is False and r["want"] == "queue"


def test_inside_window_starts_the_first_pending_job_only():
    started = []
    ov.mounted = lambda: True
    ov.in_window = lambda *a, **k: True
    ov.start = lambda name, args: started.append(name)
    ov.stop = lambda name: None
    ov.alert = lambda *a, **k: None
    ov.alive = lambda pid: False
    ov.job_done = lambda name, st: name == "never"        # nothing done
    ov.shutil.disk_usage = lambda p: type("U", (), {"free": 2e12})()
    ov.subprocess.run = lambda *a, **k: type("R", (), {"stdout": "active\n"})()
    ov.queue_pending = lambda: ["a.srt"]
    ov.cycle({"alerts": {}, "svc_restart_at": 0, "restarts": {}, "done": {}})
    assert started == ["queue"], started


def test_a_queue_that_made_progress_is_not_counted_as_dead():
    """Prison Break arrived all day under the 0-24 window: three normal exits used up the night's three starts."""
    started, sent = [], []
    ov.start = lambda name, args: started.append(name)
    ov.alert = lambda st, key, *a, **k: sent.append(key)
    ov.mounted, ov.in_window, ov.alive = (lambda: True), (lambda *a, **k: True), (lambda pid: False)
    ov.stop = lambda name: None
    ov.job_done = lambda name, st: name != "queue"
    ov.shutil.disk_usage = lambda p: type("U", (), {"free": 2e12})()
    ov.subprocess.run = lambda *a, **k: type("R", (), {"stdout": "active\n"})()
    today = ov.time.strftime("%F")
    st = {"alerts": {}, "svc_restart_at": 0, "restarts": {f"queue:{today}": 3}, "done": {},
          "pending_at_start": {"queue": ["old1.srt", "old2.srt"]}}
    ov.queue_pending = lambda: ["old2.srt", "new1.srt", "new2.srt"]      # more pending, but old1 got done
    ov.cycle(st)
    assert started == ["queue"] and "giveup:queue" not in sent, (started, sent)
    started.clear()
    st["restarts"][f"queue:{today}"] = 3                                 # nothing it started with got done: dead
    ov.cycle(st)
    assert started == [] and "giveup:queue" in sent, (started, sent)


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(n, "OK")
