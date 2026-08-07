"""path_unchanged: skip a movie folder / episode file only when it was not touched."""
import os
import tempfile
import time

import scan


def _row(status="ready", updated_at="2030-01-01T00:00:00"):
    return {"status": status, "updated_at": updated_at}


def test():
    with tempfile.TemporaryDirectory() as d:
        assert scan.path_unchanged(_row(), d), "untouched folder should be skipped"
        assert not scan.path_unchanged(None, d), "never-scanned folder must be scanned"
        assert not scan.path_unchanged(_row(status="stub"), d), "stub must be rescanned"
        assert not scan.path_unchanged(_row(status="error"), d), "error must be rescanned"
        assert not scan.path_unchanged(_row(updated_at="1999-01-01T00:00:00"), d), \
            "folder touched after the row was written must be rescanned"
        assert not scan.path_unchanged(_row(), os.path.join(d, "gone")), \
            "missing folder must not be skipped"

        # a newly dropped file bumps the directory mtime -> no longer skippable
        stale = time.strftime("%Y-%m-%dT%H:%M:%S")
        time.sleep(1.1)
        ep = os.path.join(d, "Show S01E01.mkv")
        open(ep, "w").close()
        assert not scan.path_unchanged(_row(updated_at=stale), d), \
            "added file must trigger a rescan"
        # episode files are checked the same way, by their own mtime
        assert not scan.path_unchanged(_row(updated_at=stale), ep), \
            "new episode file must be inspected"
        assert scan.path_unchanged(_row(), ep), "untouched episode should be skipped"
    print("ok")


if __name__ == "__main__":
    test()
