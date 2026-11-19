"""The manual inbox: waits for a stable size, imports only an exact TMDB match, deletes the package only once
Jellyfin serves the normalised film. Radarr, TMDB, ntfy and Jellyfin are stubbed; the state machine is real."""
import os
import sqlite3
import tempfile

import app
import inbox
import scan

calls = []


def setup():
    root = tempfile.mkdtemp()
    inbox.INBOX = os.path.join(root, "movies")
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


if __name__ == "__main__":
    main()
