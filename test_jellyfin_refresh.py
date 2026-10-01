"""A new film is not in Jellyfin: ask for a library scan and wait for it, instead of
reporting "no indexado" forever. A film that never shows up says so."""
import json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
os.environ.setdefault("TMDB_API_KEY", "x")
os.environ["MM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
import app  # noqa: E402

PATH = "/hdd1/Movies/New Film (2026)/New Film (2026).mkv"


def run(appears_after):
    """appears_after: number of searches that come back empty before the item shows."""
    calls = {"search": 0, "scan": 0, "item_refresh": 0}

    def fake(cmd, **kw):
        url = cmd[-1]
        if "/Library/Refresh" in url:
            calls["scan"] += 1
            out = ""
        elif "/Items/abc/Refresh" in url:
            calls["item_refresh"] += 1
            out = ""
        else:
            calls["search"] += 1
            hit = appears_after is not None and calls["search"] > appears_after
            out = json.dumps({"Items": [{"Id": "abc", "Path": PATH, "UserData": {}}] if hit else []})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    app.subprocess.run, real_run = fake, app.subprocess.run
    app.time.sleep, real_sleep = (lambda s: None), app.time.sleep
    app.JELLYFIN_URL, app.JELLYFIN_MOVIES, app.JELLYFIN_USER = "http://jf", "/hdd1/Movies", ""
    app._jellyfin_token = lambda: "tok"
    try:
        res = app._jellyfin_refresh("New Film (2026)", "New Film (2026).mkv", "New Film")
    finally:
        app.subprocess.run, app.time.sleep = real_run, real_sleep
    return res, calls


def main():
    (ok, why), c = run(appears_after=2)          # shows up on the second poll
    assert ok and why == "refrescado" and c["scan"] == 1 and c["item_refresh"] == 1, (ok, why, c)

    (ok, why), c = run(appears_after=0)          # already indexed: no scan at all
    assert ok and c["scan"] == 0, (ok, why, c)

    (ok, why), c = run(appears_after=None)       # never appears
    assert not ok and "no lo indexó" in why and c["scan"] == 1 and c["item_refresh"] == 0, (ok, why, c)
    print("test_jellyfin_refresh OK")


if __name__ == "__main__":
    main()
