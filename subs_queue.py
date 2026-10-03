"""Run every sidecar .srt that never went through /api/subs/fix through it, smallest media first.

    subs_queue.py            # list and count
    subs_queue.py --run      # POST them all at once (the endpoint serialises them, but nothing paces them)
    subs_queue.py --paced    # one at a time, waiting for each result; stops when the mount disappears or
                             # when asked to (SIGTERM): what the night supervisor runs

"Never filtered" = no `[subs] <name>:` line in server.log and no `<name>.orig`. Those are the sidecars Bazarr
wrote before its post-processing hook existed. Each one means reading the whole video once (ffsubsync), so
~1.5 TB for the movies: start it when the disk is free (after the DBZ remux), not alongside it.
"""
import base64, json, os, re, signal, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOTS = ["/srv/storage/Movies", "/srv/storage/Shows"]


def unfiltered():
    log = open(os.path.join(HERE, "server.log"), encoding="utf-8", errors="replace").read()
    done = set(re.findall(r"^\[subs\] (.+?\.srt):", log, re.M))
    out = []
    for root in ROOTS:
        for dp, _, fs in os.walk(root):
            for f in fs:
                p = os.path.join(dp, f)
                if f.lower().endswith(".srt") and f not in done and not os.path.exists(p + ".orig"):
                    stem = f
                    vids = [v for v in fs if v.lower().endswith((".mkv", ".mp4", ".m4v", ".avi"))
                            and stem.startswith(os.path.splitext(v)[0] + ".")]
                    size = max((os.path.getsize(os.path.join(dp, v)) for v in vids), default=0)
                    out.append((size, p))
    return sorted(out)


def post(path, auth):
    req = urllib.request.Request("http://localhost:8500/api/subs/fix", data=json.dumps({"subtitle": path}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    return urllib.request.urlopen(req, timeout=60).status


if __name__ == "__main__":
    todo = unfiltered()
    print(len(todo), "sidecars never filtered;", f"{sum(s for s, _ in todo) / 1e12:.2f} TB of video to read")
    if "--paced" in sys.argv:
        paced(lambda: os.path.ismount("/srv/storage"))
    elif "--run" in sys.argv:
        import app
        u, p = app._hook_credentials()
        auth = "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()
        ok = sum(post(path, auth) == 202 for _, path in todo)
        print(ok, "queued; results arrive as [subs] lines in server.log")


TRIED = "/var/tmp/subs_queue_tried.json"


def paced(is_ok=lambda: True):
    """One sidecar at a time: POST, then wait until server.log carries that file's [subs] line (any outcome).
    A file attempted twice is skipped (`ffsubsync gave no result` and timeouts would otherwise repeat every night). `is_ok()` is checked between files (mount present, inside the window)."""
    import app
    u, p = app._hook_credentials()
    auth = "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()
    tried = json.load(open(TRIED)) if os.path.exists(TRIED) else {}
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    logf = os.path.join(HERE, "server.log")
    for _, path in unfiltered():
        if stop or not is_ok():
            break
        if tried.get(path, 0) >= 2:
            continue
        tried[path] = tried.get(path, 0) + 1
        json.dump(tried, open(TRIED, "w"))
        size0 = os.path.getsize(logf)
        post(path, auth)
        name, t0 = os.path.basename(path), time.time()
        while not stop and time.time() - t0 < 3 * 3600:
            time.sleep(20)
            with open(logf, "rb") as fh:
                fh.seek(size0)
                new = fh.read().decode("utf-8", "replace")
            if re.search(r"^\[subs\] '?(?:[^\n]*/)?" + re.escape(name), new, re.M):
                break                                     # a result line (ok or error); unfiltered() drops the ok ones
    print("paced queue ended", "(stopped)" if stop else "", flush=True)
