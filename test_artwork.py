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


if __name__ == "__main__":
    main()
