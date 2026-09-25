#!/usr/bin/env python3
"""Daily batch: give Bazarr profile 1 (Espanol Latino) to the next 25 movies
without full Spanish (subs_inventory.json spa_full == "none").

Movies with neither full Spanish nor full English go first. Batch size keeps
provider quota in check (OpenSubtitles free ~20 downloads/day). A movie that
still shows no missing subtitles after the assignment gets a scan-disk,
otherwise Bazarr never searches it. Once every target has the profile the
run is a no-op. Driven by bazarr-profile-batch.timer (systemd --user).
"""
import json, os, re, time, urllib.parse, urllib.request

BATCH = 25
PROFILE = 1
BASE = "http://localhost:6767/api"
INV = os.path.expanduser("~/media-manager/subs_inventory.json")
KEY = re.search(r"^\s+apikey: ([0-9a-f]{32})", open(
    "/DATA/AppData/bazarr/config/config/config.yaml").read(), re.M).group(1)


def api(method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(BASE + path, body, {"X-API-KEY": KEY}, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def movies():
    return {os.path.basename(os.path.dirname(m["path"])): m
            for m in api("GET", "/movies?start=0&length=-1")["data"]}


def main():
    inv = [i for i in json.load(open(INV)) if i["spa_full"] == "none"]
    inv.sort(key=lambda i: i["eng_full"] != "none")  # no-eng first
    by_folder = movies()
    todo = [by_folder[i["folder"]]["radarrId"] for i in inv
            if i["folder"] in by_folder and by_folder[i["folder"]]["profileId"] != PROFILE]
    print(f"{len(todo)} targets without profile; assigning {min(len(todo), BATCH)}")
    batch = todo[:BATCH]
    for rid in batch:  # one per request: Bazarr splits list values badly
        api("POST", "/movies", {"radarrid": rid, "profileid": PROFILE})
    if not batch:
        return
    time.sleep(10)
    now = {m["radarrId"]: m for m in movies().values()}
    for rid in batch:
        if not now[rid].get("missing_subtitles"):
            api("PATCH", f"/movies?radarrid={rid}&action=scan-disk")
            print(f"scan-disk {rid} {now[rid]['title']}")
    print("assigned:", ", ".join(now[r]["title"] for r in batch))


if __name__ == "__main__":
    main()
