"""ponytail: minimal self-check for the SSE payload -- run with `python3 test_events.py`.

The stream only sends when the payload string changes, so the contract that
actually matters is: identical state -> byte-identical JSON. If that ever drifts
(dict ordering, float repr), /api/events silently degrades into a 2s poll.
"""
import app

conn = app.get_db()
try:
    payload = app._events_payload(conn)
    assert set(payload) == {"jobs", "scan"}, payload.keys()
    assert isinstance(payload["jobs"], list)
    assert set(payload["scan"]) == {"running", "done", "total", "current"}

    # operational columns must never reach the browser: `cmd` carries absolute
    # library paths and log_path/tmux_session are server-side handles
    for j in payload["jobs"]:
        for omitted in app._JOB_LIST_OMIT:
            assert omitted not in j, f"{omitted} leaked into the event payload"
        assert "movie_title" in j and "eta" in j, j

    # unchanged state must serialize identically, or every tick sends a frame
    assert app._events_json() == app._events_json()

    # ...and a real change must produce a different frame
    before = app._events_json()
    app.scan_state.update(running=True, done=3, total=10, current="Some Folder")
    after = app._events_json()
    assert before != after, "scan progress did not change the payload"
    assert '"done": 3' in after and "Some Folder" in after
finally:
    app.scan_state.update(running=False, done=0, total=0, current="")
    conn.close()

print("ok")
