"""The manual inbox: waits for a stable size, imports only an exact TMDB match, deletes the package only once
Jellyfin serves the normalised film. Radarr, TMDB, ntfy and Jellyfin are stubbed; the state machine is real."""
import os
import shutil
import sqlite3
import tempfile

import app
import inbox
import scan

calls = []


def setup():
    root = tempfile.mkdtemp()
    inbox.INBOX = os.path.join(root, "movies")
    inbox.INBOX_SHOWS = os.path.join(root, "shows")
    app.STAGING_ROOT = os.path.join(root, "Staging")
    os.makedirs(os.path.join(inbox.INBOX, "The Odyssey (2026)"))
    with open(os.path.join(inbox.INBOX, "The Odyssey (2026)", "odyssey.mkv"), "wb") as f:
        f.write(b"x" * 100)
    os.makedirs(os.path.join(inbox.INBOX, "Some.Film.REMUX"))
    with open(os.path.join(inbox.INBOX, "Some.Film.REMUX", "a.mkv"), "wb") as f:
        f.write(b"x" * 10)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(app.SCHEMA)
    return conn


def radarr(method, path, body=None, timeout=90):
    calls.append((method, path.split("?")[0], body))
    if path.startswith("movie?tmdbId="):
        return []
    if path.startswith("movie/lookup"):
        return {"title": "The Odyssey", "tmdbId": 1}
    if method == "POST" and path == "movie":
        return {**body, "id": 383}
    if path.startswith("manualimport"):
        return [{"path": os.path.join(inbox.INBOX, "The Odyssey (2026)", "odyssey.mkv"), "quality": {"q": 1}}]
    if method == "POST" and path == "command":
        return {"id": 99}
    if path.startswith("command/"):
        return {"status": "completed"}


def status(conn, name):
    return conn.execute("SELECT status, reason FROM inbox_items WHERE path LIKE ?", ("%" + name,)).fetchone()


def main():
    conn = setup()
    app._radarr, app._notify = radarr, lambda *a, **k: calls.append(("notify", a[0], None))
    scan.tmdb_search_candidates = lambda q, y, k, limit=6, kind="movie": (
        [{"tmdb_id": 1, "title": "The Odyssey", "year": 2026}, {"tmdb_id": 2, "title": "The Odyssey", "year": 1997}])
    seen = {"v": False}
    app._jellyfin_refresh = lambda folder, file, title=None: (seen["v"], "no indexada")

    inbox.run(conn)
    assert status(conn, "The Odyssey (2026)")["status"] == "waiting", "first sighting only records the size"
    assert not [c for c in calls if c[0] == "POST"], "nothing is imported before the size is seen twice"

    inbox.run(conn)
    assert status(conn, "The Odyssey (2026)")["status"] == "importing"
    add = next(c[2] for c in calls if c[:2] == ("POST", "movie"))
    assert add["monitored"] is False and add["addOptions"] == {"searchForMovie": False}
    assert add["rootFolderPath"] == app.STAGING_CONTAINER
    imp = next(c[2] for c in calls if c[:2] == ("POST", "command"))
    assert imp["importMode"] == "copy" and imp["files"][0]["movieId"] == 383
    assert status(conn, "Some.Film.REMUX")["status"] == "needs_title", "no year, no guess"

    # the hook took it: movie row + remux job
    conn.execute("INSERT INTO movies (id, folder, file, tmdb_id, status) VALUES (474, 'The Odyssey (2026)', "
                 "'.odyssey.import.mkv', 1, 'cleaning')")
    conn.execute("INSERT INTO jobs (id, movie_id, kind, status) VALUES (5, 474, 'remux', 'running')")
    inbox.run(conn)
    assert status(conn, "The Odyssey (2026)")["status"] == "normalizing"

    conn.execute("UPDATE jobs SET status='verified' WHERE id=5")
    conn.execute("UPDATE movies SET status='clean', file='The Odyssey (2026).mkv' WHERE id=474")
    inbox.run(conn)
    assert os.path.isdir(os.path.join(inbox.INBOX, "The Odyssey (2026)")), "Jellyfin has not shown it: keep the package"
    assert status(conn, "The Odyssey (2026)")["status"] == "normalizing"

    seen["v"] = True
    inbox.run(conn)
    assert status(conn, "The Odyssey (2026)")["status"] == "ready"
    assert not os.path.exists(os.path.join(inbox.INBOX, "The Odyssey (2026)")), "package deleted once Jellyfin serves it"

    # an ambiguous title (two exact title+year hits) is not guessed
    scan.tmdb_search_candidates = lambda q, y, k, limit=6, kind="movie": (
        [{"tmdb_id": 3, "title": "Heat", "year": 1995}, {"tmdb_id": 4, "title": "Heat", "year": 1995}])
    assert inbox.identify("Heat (1995)")[0] is None
    scan.tmdb_get_movie = lambda i, k: {"tmdb_id": i, "title": "Heat", "year": 1995}
    assert inbox.identify("Heat (1995) {tmdb-949}")[0]["tmdb_id"] == 949, "{tmdb-N} forces the film"
    print("test_inbox OK")


def sonarr(method, path, body=None):
    calls.append((method, path.split("?")[0], body))
    d = os.path.join(inbox.INBOX_SHOWS, "Andor (2022)")
    if path.startswith("series/lookup"):
        return [{"title": "Andor", "year": 2022, "tvdbId": 393189}]
    if path.startswith("series?tvdbId="):
        return [{"id": 7, "path": "/data/Shows/Andor (2022)"}] if any(c[:2] == ("POST", "series") for c in calls) else []
    if method == "POST" and path == "series":
        return {**body, "id": 7}
    if path.startswith("manualimport"):
        return [{"path": os.path.join(d, f"e{n}.mkv"), "quality": {"q": 1},
                 "episodes": [{"id": 100 + n, "seasonNumber": 1, "episodeNumber": n, "hasFile": SHOW["has"] == n}]}
                for n in (1, 2)]
    if method == "POST" and path == "command":
        return {"id": 55}
    if path.startswith("command/"):
        return {"status": "completed"}


SHOW = {"has": None}


def shows():
    conn = setup()
    calls.clear()
    app._notify = lambda *a, **k: calls.append(("notify", a[0], None))
    inbox._sonarr = sonarr
    d = os.path.join(inbox.INBOX_SHOWS, "Andor (2022)")
    os.makedirs(d)
    for n in (1, 2):
        open(os.path.join(d, f"e{n}.mkv"), "wb").write(b"x" * 10)
    open(os.path.join(inbox.INBOX_SHOWS, "loose S01E01.mkv"), "wb").write(b"x")
    shutil.rmtree(os.path.join(inbox.INBOX, "The Odyssey (2026)"))
    shutil.rmtree(os.path.join(inbox.INBOX, "Some.Film.REMUX"))

    SHOW["has"] = 2                    # the library already has E02: nothing is imported, a person decides
    inbox.run(conn); inbox.run(conn)
    r = status(conn, "Andor (2022)")
    assert r["status"] == "failed" and "S01E02 ya está" in r["reason"], dict(r)
    assert not [c for c in calls if c[:2] == ("POST", "command")]
    assert status(conn, "loose S01E01.mkv")["status"] == "failed", "a loose episode needs its show folder"

    SHOW["has"] = None
    conn.execute("UPDATE inbox_items SET status='waiting' WHERE path LIKE '%Andor (2022)'")
    inbox.run(conn)
    assert status(conn, "Andor (2022)")["status"] == "importing"
    add = next(c[2] for c in calls if c[:2] == ("POST", "series"))
    assert add["monitored"] is False and add["addOptions"]["searchForMissingEpisodes"] is False
    imp = next(c[2] for c in calls if c[:2] == ("POST", "command"))
    assert imp["importMode"] == "copy" and [f["episodeIds"] for f in imp["files"]] == [[101], [102]]

    # the Sonarr hook took both: rows + jobs
    conn.execute("INSERT INTO shows (id, folder) VALUES (3, 'Andor (2022)')")
    for n in (1, 2):
        conn.execute("INSERT INTO episodes (id, show_id, season, episode, folder, file, status) "
                     "VALUES (?, 3, 1, ?, 'Season 01', ?, 'cleaning')", (n, n, f"Andor - S01E0{n}.mkv"))
        conn.execute("INSERT INTO jobs (id, episode_id, kind, status) VALUES (?, ?, 'remux', 'running')", (10 + n, n))
    inbox.run(conn)
    assert status(conn, "Andor (2022)")["status"] == "normalizing"
    conn.execute("UPDATE jobs SET status='verified' WHERE id=11")
    conn.execute("UPDATE episodes SET status='clean' WHERE id=1")
    inbox.run(conn)
    assert status(conn, "Andor (2022)")["status"] == "normalizing", "E02 is still running"
    conn.execute("UPDATE jobs SET status='verified' WHERE id=12")
    conn.execute("UPDATE episodes SET status='clean' WHERE id=2")
    inbox.run(conn)
    assert status(conn, "Andor (2022)")["status"] == "ready" and not os.path.exists(d)
    assert inbox._container("/srv/storage/Downloads/manual/shows/X") == "/data/Downloads/manual/shows/X"
    print("test_inbox shows OK")


if __name__ == "__main__":
    main()
    shows()
