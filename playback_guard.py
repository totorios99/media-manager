"""Freeze the heavy disk jobs while someone is watching; thaw them when playback pauses or ends.

    setsid nohup uv run python -u playback_guard.py > /var/tmp/playback_guard.log 2>&1 < /dev/null &

Why: on 2026-10-04 Neptune stalled mid-episode while F6, an ffsubsync measurement and a centre-channel extraction
shared the USB 2.0 bridge (I/O pressure 50 %; 4 % once they were stopped). Antonio watches an episode now and then
and leaves the rest for hours or days, so the jobs must step aside only while a stream is actually running.

Every CHECK seconds it asks Jellyfin for sessions. A session with a NowPlayingItem that is not paused counts as
watching. Then every process tree whose root matches HEAVY gets SIGSTOP, or SIGCONT when nobody is watching
(or Jellyfin does not answer: a guard that freezes work forever on an API hiccup is worse than a stutter).
Stopping is invisible to the jobs; ffsubsync's 4 h limit in the service is the one clock that keeps running.
On exit, and on start, it thaws everything it may have frozen.
"""
import json, os, re, signal, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = 10
ROOT_BINARIES = {"python", "python3", "uv", "uvx", "ffmpeg"}   # not shells or grep that merely mention a job
HEAVY = re.compile(r"f6_series\.py|ocr_pass\.py|subs_check\.py|dbz_rm_subs\.py|ffsubsync|pgsrip|subs_variant_pass\.py|pan=mono\|c0=FC|subs_queue\.py")


def env():
    e = dict(os.environ)
    try:
        for ln in open(os.path.join(HERE, ".env.systemd")):
            if "=" in ln and not ln.startswith("#"):
                k, _, v = ln.strip().partition("=")
                e.setdefault(k, v.strip("'\""))
    except OSError:
        pass
    return e


def watching(sessions):
    return any(s.get("NowPlayingItem") and not (s.get("PlayState") or {}).get("IsPaused") for s in sessions)


def sessions(url, token):
    req = urllib.request.Request(url.rstrip("/") + "/Sessions", headers={"Authorization": f'MediaBrowser Token="{token}"'})
    return json.load(urllib.request.urlopen(req, timeout=5))


def proc_table(root="/proc"):
    """{pid: (ppid, cmdline)} for every readable process."""
    out = {}
    for d in os.listdir(root):
        if not d.isdigit():
            continue
        try:
            stat = open(f"{root}/{d}/stat").read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            cmd = open(f"{root}/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
            out[int(d)] = (ppid, cmd)
        except (OSError, ValueError, IndexError):
            pass
    return out


def heavy_trees(table, me=None):
    """Every pid whose cmdline matches HEAVY, plus all its descendants (mkvmerge, ffmpeg spawned by them)."""
    me = me or os.getpid()
    roots = {p for p, (_, cmd) in table.items()
             if p != me and HEAVY.search(cmd) and os.path.basename(cmd.split(" ", 1)[0]) in ROOT_BINARIES}
    kids = {}
    for p, (pp, _) in table.items():
        kids.setdefault(pp, []).append(p)
    out, stack = set(), list(roots)
    while stack:
        p = stack.pop()
        if p not in out:
            out.add(p)
            stack += kids.get(p, [])
    return out


def send(pids, sig):
    for p in pids:
        try:
            os.kill(p, sig)
        except OSError:
            pass


def main():
    e = env()
    url, token = e.get("JELLYFIN_URL", "http://localhost:8097"), open(os.path.join(HERE, ".jellyfin-token")).read().strip()
    send(heavy_trees(proc_table()), signal.SIGCONT)               # a previous guard may have died with them stopped

    def bye(*_):
        send(heavy_trees(proc_table()), signal.SIGCONT)
        print(time.strftime("%F %T"), "exit: thawed", flush=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    frozen = False
    while True:
        try:
            busy = watching(sessions(url, token))
        except Exception:
            busy = False
        pids = heavy_trees(proc_table())
        send(pids, signal.SIGSTOP if busy else signal.SIGCONT)
        if busy != frozen:
            print(time.strftime("%F %T"), ("froze" if busy else "thawed"), len(pids), "processes", flush=True)
            frozen = busy
        time.sleep(CHECK)


if __name__ == "__main__":
    main()
