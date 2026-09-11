"""Staging keeps Radarr's raw import out of Jellyfin's view. Measured on the
real server: a folder whose only video is a dotfile is indexed 0 times; once the
final visible file lands it is indexed once. So the title appears for the first
time already normalised, and the window between import and remux closes."""
import os, sys, tempfile

sys.path.insert(0, os.path.expanduser("~/media-manager"))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
os.environ.setdefault("MM_STAGING_ROOT", tempfile.mkdtemp())


def main():
    import app
    app.MEDIA_ROOT = tempfile.mkdtemp()
    app.STAGING_ROOT = tempfile.mkdtemp()

    folder = "Some Movie (2010)"
    src = os.path.join(app.STAGING_ROOT, folder)
    os.makedirs(src)
    with open(os.path.join(src, "Some.Movie.2010.BluRay.mkv"), "w") as fh:
        fh.write("x" * 5000)
    # scene junk must not be mistaken for the film
    open(os.path.join(src, "RARBG.txt"), "w").write("junk")

    hidden = app._adopt_from_staging(folder)
    assert hidden == ".Some.Movie.2010.BluRay.import.mkv", hidden

    dest = os.path.join(app.MEDIA_ROOT, folder)
    placed = os.path.join(dest, hidden)
    assert os.path.exists(placed), "el import debe quedar en la carpeta de la película"
    assert os.path.basename(placed).startswith("."), "y tiene que estar oculto"

    # hardlink: same inode, so Radarr's copy is untouched and it costs no space
    assert os.stat(placed).st_ino == os.stat(os.path.join(src, "Some.Movie.2010.BluRay.mkv")).st_ino

    # the only visible entries must be none: that is what keeps Jellyfin away
    visible = [f for f in os.listdir(dest) if not f.startswith(".")]
    assert visible == [], f"nada visible hasta colocar el definitivo, hay {visible}"

    # adopting twice must not fail or duplicate
    assert app._adopt_from_staging(folder) == hidden

    # nothing to adopt: no staging folder, or no video in it
    assert app._adopt_from_staging("No Existe (1999)") is None
    empty = os.path.join(app.STAGING_ROOT, "Vacía (2001)")
    os.makedirs(empty)
    open(os.path.join(empty, "leeme.txt"), "w").write("x")
    assert app._adopt_from_staging("Vacía (2001)") is None

    print("test_staging OK")


if __name__ == "__main__":
    main()
