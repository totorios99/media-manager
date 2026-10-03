"""Run every sidecar .srt that never went through /api/subs/fix through it, smallest media first.

    subs_queue.py            # list and count
    subs_queue.py --run      # POST them all; the endpoint serialises them (one ffsubsync at a time)

"Never filtered" = no `[subs] <name>:` line in server.log and no `<name>.orig`. Those are the sidecars Bazarr
wrote before its post-processing hook existed. Each one means reading the whole video once (ffsubsync), so
~1.5 TB for the movies: start it when the disk is free (after the DBZ remux), not alongside it.
"""
import base64, json, os, re, sys, urllib.request

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
    if "--run" in sys.argv:
        import app
        u, p = app._hook_credentials()
        auth = "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()
        ok = sum(post(path, auth) == 202 for _, path in todo)
        print(ok, "queued; results arrive as [subs] lines in server.log")
