"""Radarr moves a movie's artwork into the recycle bin along with the file it
replaces, leaving the library folder with nothing but the video. Jellyfin was
serving those images, so the title comes back with no poster -- which is how
Tokyo Drift, Inside Out 2 and Shutter Island all lost theirs."""
import os, sys, tempfile

sys.path.insert(0, os.path.expanduser("~/media-manager"))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())


def main():
    import graft
    root = tempfile.mkdtemp()
    recycle = tempfile.mkdtemp()
    graft.RECYCLE = recycle
    import app
    app.MEDIA_ROOT = root

    folder = "Some Movie (2010)"
    os.makedirs(os.path.join(root, folder))
    os.makedirs(os.path.join(recycle, folder))
    open(os.path.join(root, folder, "Some Movie (2010).mkv"), "w").close()
    for f in ("folder.jpg", "backdrop.jpg", "logo.png"):
        open(os.path.join(recycle, folder, f), "w").write("img")

    assert not app._has_artwork("movie", folder), "empieza sin carátula"
    restored = app._restore_artwork("movie", folder)
    assert sorted(restored) == ["backdrop.jpg", "folder.jpg", "logo.png"], restored
    assert app._has_artwork("movie", folder)

    # already has its own artwork: leave it alone, never overwrite
    open(os.path.join(recycle, folder, "otra.jpg"), "w").write("img")
    assert app._restore_artwork("movie", folder) == [], "no debe tocar lo que ya está"

    # no recycled copy at all: no crash, nothing restored
    empty = "Otra (1999)"
    os.makedirs(os.path.join(root, empty))
    open(os.path.join(root, empty, "v.mkv"), "w").close()
    assert app._restore_artwork("movie", empty) == []
    assert not app._has_artwork("movie", empty)

    print("test_artwork OK")


def test_readiness_gate():
    """"Lista" must never go out while something obtainable is still missing.

    Tokyo Drift shipped that message with its Spanish sitting in the recycle bin.
    Superman has no Spanish in any copy we hold -- that is a note, not a blocker,
    or the film would never be announced at all."""
    import sqlite3, app, graft
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tracks (id INTEGER PRIMARY KEY, movie_id INT, type TEXT,
                           out_lang TEXT, keep INT);
      CREATE TABLE movies (id INTEGER PRIMARY KEY, folder TEXT, file TEXT, original_language TEXT);
      INSERT INTO tracks (movie_id, type, out_lang, keep) VALUES
        (1,'audio','eng',1), (1,'audio','spa',1),
        (2,'audio','eng',1);
      INSERT INTO movies (id, folder, file) VALUES (1,'Full (2020)',NULL),
                                                   (2,'Full (2020)',NULL);
    """)
    root = app.MEDIA_ROOT

    def owner(folder):
        return {"folder": folder}

    # con carátula y con español: lista
    full = "Full (2020)"
    os.makedirs(os.path.join(root, full), exist_ok=True)
    open(os.path.join(root, full, "folder.jpg"), "w").write("i")
    pending, notes = app._readiness(conn, "movie", 1, owner(full))
    assert pending == [] and notes == [], (pending, notes)

    # sin carátula, pero la papelera la tiene: pendiente, no lista
    bare = "Bare (2021)"
    os.makedirs(os.path.join(root, bare), exist_ok=True)
    os.makedirs(os.path.join(graft.RECYCLE, bare), exist_ok=True)
    open(os.path.join(graft.RECYCLE, bare, "folder.jpg"), "w").write("i")
    pending, notes = app._readiness(conn, "movie", 1, owner(bare))
    assert pending == ["carátula"], pending

    # sin carátula y sin copia de la que sacarla: se anuncia, con nota
    gone = "Gone (2022)"
    os.makedirs(os.path.join(root, gone), exist_ok=True)
    pending, notes = app._readiness(conn, "movie", 1, owner(gone))
    assert pending == [] and notes == ["sin carátula"], (pending, notes)

    # sin español en ninguna parte: nota, nunca un bloqueo
    pending, notes = app._readiness(conn, "movie", 2, owner(full))
    assert pending == [] and notes == ["sin audio en español"], (pending, notes)

    print("test_readiness_gate OK")


def test_artwork_language_preference():
    """A logo in the wrong language is worse than no logo: Spanish wins, then
    English, then the textless image. Vote average only breaks ties inside a
    language, never across them -- a highly-voted Japanese poster must not beat
    a mediocre Spanish one."""
    import artwork
    images = {"posters": [
        {"iso_639_1": "ja", "file_path": "/ja.jpg", "vote_average": 9.9, "width": 2000},
        {"iso_639_1": "en", "file_path": "/en.jpg", "vote_average": 5.0, "width": 1000},
        {"iso_639_1": "es", "file_path": "/es.jpg", "vote_average": 1.0, "width": 500},
        {"iso_639_1": None, "file_path": "/none.jpg", "vote_average": 7.0, "width": 900},
    ]}
    assert artwork._best(images, "posters") == "/es.jpg"
    images["posters"] = [i for i in images["posters"] if i["iso_639_1"] != "es"]
    assert artwork._best(images, "posters") == "/en.jpg"
    images["posters"] = [i for i in images["posters"] if i["iso_639_1"] != "en"]
    assert artwork._best(images, "posters") == "/none.jpg"
    # only a language we do not want left: take it rather than ship nothing
    images["posters"] = [{"iso_639_1": "ja", "file_path": "/ja.jpg", "vote_average": 9.9}]
    assert artwork._best(images, "posters") == "/ja.jpg"
    assert artwork._best({"logos": []}, "logos") is None
    print("test_artwork_language_preference OK")


if __name__ == "__main__":
    main()
    test_readiness_gate()
    test_artwork_language_preference()
