"""The Sonarr hook, exercised against a real SQLite DB and a fake Sonarr payload.

Covers what actually broke in the Radarr one: an import landing in a root we do
not watch, and an event that must rescan before it looks anything up.
"""
import json
import os
import sqlite3
import tempfile

os.environ.setdefault("TMDB_API_KEY", "")
TMP = tempfile.mkdtemp()
os.environ["MM_SHOWS_ROOT"] = TMP
os.environ.setdefault("MEDIA_ROOT", TMP)
os.environ["MM_DB"] = os.path.join(TMP, "t.db")

import app  # noqa: E402


def test_hook_ignores_events_it_does_not_handle():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()
    body = src[src.index("async def sonarr_hook"):src.index("def _fetch_show_artwork")]
    assert '"Download", "Rename", "EpisodeFileDelete"' in body, \
        "the Sonarr hook no longer names the events it acts on"
    # a Grab fires long before any file exists; acting on it would enqueue a job
    # against a path Sonarr has not written yet
    assert '"Grab"' not in body, "the hook would act on Grab, before the file exists"
    assert "scan.upsert_show" in body, "the hook looks up rows without rescanning first"
    idx_scan = body.index("scan.upsert_show")
    idx_lookup = body.index("SELECT id FROM episodes")
    assert idx_scan < idx_lookup, "the hook queries episodes before the rescan"
    print("test_hook_ignores_events_it_does_not_handle OK")


def test_missing_show_names_the_path():
    """Underworld landed in /staging and the warning said only 'no encontré la
    carpeta', which reads as 'nothing happened'."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()
    body = src[src.index("async def sonarr_hook"):src.index("def _fetch_show_artwork")]
    assert "fuera de la biblioteca" in body and "series_path" in body, \
        "a missing show would be reported without saying where Sonarr put it"
    print("test_missing_show_names_the_path OK")


def test_propedit_is_tried_before_remux():
    """In-place header edits cost seconds; a remux copies the whole episode
    across a 40 MB/s bus. Only a config that drops tracks needs the copy."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()
    body = src[src.index("async def sonarr_hook"):src.index("def _fetch_show_artwork")]
    assert 'for kind in ("propedit", "remux")' in body, \
        "the hook does not prefer the in-place edit"
    assert 'exc.status_code == 400' in body, \
        "a propedit refusal is not distinguished from a real failure"
    print("test_propedit_is_tried_before_remux OK")


def test_outside_library_creates_nothing():
    """An import into a root we do not watch must warn, not mint a show row.
    Calling upsert_show on the basename first created a phantom "Fake" show for
    a payload pointing at /staging -- the Underworld failure, rebuilt."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()
    body = src[src.index("async def sonarr_hook"):src.index("def _fetch_show_artwork")]
    guard = body.index("os.path.isdir(os.path.join(SHOWS_ROOT, folder))")
    assert guard < body.index("scan.upsert_show"), \
        "the hook upserts the show before checking the folder is ours"
    print("test_outside_library_creates_nothing OK")


if __name__ == "__main__":
    test_hook_ignores_events_it_does_not_handle()
    test_missing_show_names_the_path()
    test_propedit_is_tried_before_remux()
    test_outside_library_creates_nothing()
