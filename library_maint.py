"""Nightly library maintenance: keep everything new in convention with the same verified tools, one heavy job at a time.

    library_maint.py            # run every step (systemd: library-maint.timer, 00:41)

Steps, each idempotent (a conform file is skipped by its own plan, so a quiet night costs a few DB reads):
  spa_unblock (files F6 blocks on 2+ bare-spa subs) -> f6_series (episodes) -> f6_series --movies -> ocr_pass (no PGS/VobSub) -> subs_check (ads + sync of embedded SRT)
then a Jellyfin library refresh and the Media Segment Scan when any file changed.
A flock on /var/tmp/library_maint.lock keeps two runs (or the trip chain, which takes the same lock) from overlapping.
Failure handling: a step that exits non-zero or logs new FAILED/ERROR lines is counted; the run goes on with the
next step; one ntfy message at the end lists them (files stay as they were: every tool replaces only after verifying).
The disk is checked before every step; unmounted -> stop and alert.
"""
import fcntl, json, os, subprocess, sys, time, urllib.request

import overnight

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = "/var/tmp/library_maint.lock"
OUT = "/var/tmp/library_maint.out"
UV = os.path.expanduser("~/.local/bin/uv")
SEGMENT_SCAN = "f861734dd71b37f9482b52a820e39013"
STEPS = [  # (name, argv, its jsonl log)
    ("spa unblock", ["spa_unblock.py", "--run"], "spa_unblock.jsonl"),
    ("f6 series", ["f6_series.py", "--run", "--ignore-downloads"], "f6_series.jsonl"),
    ("f6 movies", ["f6_series.py", "--movies", "--run", "--ignore-downloads"], "f6_movies.jsonl"),
    ("ocr", ["ocr_pass.py", "--run"], "ocr_pass.jsonl"),
    ("ocr recover", ["ocr_recover.py", "--run"], "ocr_recover.jsonl"),
    ("subs check", ["subs_check.py", "--run"], "subs_check.jsonl"),
]


def lines(name):
    p = os.path.join(HERE, name)
    return open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []


def new_records(name, start):
    out = []
    for l in lines(name)[start:]:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def jellyfin(path, method="POST"):
    import app
    tok = app._jellyfin_token()
    if not (app.JELLYFIN_URL and tok):
        return "no Jellyfin credentials"
    req = urllib.request.Request(app.JELLYFIN_URL + path, method=method, data=b"" if method == "POST" else None,
                                 headers={"Authorization": f'MediaBrowser Token="{tok}"'})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return None
    except Exception as e:
        return repr(e)[:200]


def run():
    log = open(OUT, "a", buffering=1)
    say = lambda *a: print(time.strftime("%F %T"), *a, file=log, flush=True)
    st = {"alerts": {}}
    problems, changed = [], 0
    for name, argv, jl in STEPS:
        if not os.path.ismount("/srv/storage"):
            overnight.alert(st, "maint-disk", "Mantenimiento detenido", "/srv/storage no está montado", prio=5)
            say("disk not mounted, stop")
            return 2
        start = len(lines(jl))
        say("start", name)
        r = subprocess.run([UV, "run", "python", "-u"] + argv, cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
        recs = new_records(jl, start)
        bad = [x for x in recs if x.get("status") in ("FAILED", "ERROR")]
        changed += sum(1 for x in recs if x.get("status") == "ok")
        say("end", name, f"rc={r.returncode}", f"records={len(recs)}", f"bad={len(bad)}")
        if r.returncode:
            problems.append(f"{name}: exit {r.returncode}")
        for x in bad[:5]:
            problems.append(f"{name}: {os.path.basename(x.get('path') or x.get('file') or '?')} — {(x.get('error') or '')[:80]}")
        if len(bad) > 5:
            problems.append(f"{name}: +{len(bad) - 5} more")
    if changed:
        for p in ("/Library/Refresh", f"/ScheduledTasks/Running/{SEGMENT_SCAN}"):
            err = jellyfin(p)
            say("jellyfin", p, err or "ok")
            if err:
                problems.append(f"jellyfin {p}: {err}")
    say("done", f"changed={changed}", f"problems={len(problems)}")
    if problems:
        overnight.alert(st, "maint", "Mantenimiento: revisar", "\n".join(problems)[:3500])
    return 1 if problems else 0


if __name__ == "__main__":
    if not overnight.in_window():                  # a Persistent= catch-up at 07:26 held the lock all day, waiting
        print("outside the MM_NIGHT window; nothing to do")
        sys.exit(0)
    if os.environ.get("MAINT_LOCK_HELD"):          # the trip chain took the lock for us
        sys.exit(run())
    fd = open(LOCK, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another maintenance run holds the lock; nothing to do")
        sys.exit(0)
    sys.exit(run())
