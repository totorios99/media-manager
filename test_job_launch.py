"""A job must survive whoever launched it, and must not be failed while it is starting.

Nine encodes queued by the oneshot mm-compress.service died within seconds on
2026-11-04: with no tmux server running, the launch started one inside that unit's
cgroup, and systemd killed it when the unit exited. The Odyssey's remux (job 2093)
was marked failed at second 0 while mkvmerge ran on: a poll landed between the
'running' commit and `tmux new-session`, and nothing looked at the session again.
"""
import sqlite3
import subprocess
import tempfile

import jobs


def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, progress REAL, exit_code INT, "
                 "tmux_session TEXT, log_path TEXT, started_at TEXT, finished_at TEXT)")
    return conn


def launched_argv(no_systemd):
    calls = []
    real_run, real_flag = subprocess.run, jobs.NO_SYSTEMD
    subprocess.run = lambda argv, **kw: calls.append(argv)
    jobs.NO_SYSTEMD = no_systemd
    try:
        conn = db()
        conn.execute("INSERT INTO jobs (id, kind, status) VALUES (7, 'encode', 'queued')")
        jobs._launch(conn, 7, "true", tempfile.mkdtemp(), None)
    finally:
        subprocess.run, jobs.NO_SYSTEMD = real_run, real_flag
    return calls[-1]


def poll_with_sessions(answers, log_text=""):
    log = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
    log.write(log_text)
    log.close()
    conn = db()
    conn.execute("INSERT INTO jobs (id, kind, status, tmux_session, log_path) VALUES (1, 'remux', 'running', 'mm-1', ?)",
                 (log.name,))
    replies = iter(answers)
    real_alive, real_sleep = jobs.session_alive, jobs.time.sleep
    jobs.session_alive, jobs.time.sleep = (lambda s: next(replies)), (lambda s: None)
    try:
        return jobs.poll_job(conn, 1)["status"]
    finally:
        jobs.session_alive, jobs.time.sleep = real_alive, real_sleep


def main():
    argv = launched_argv(False)
    tmux = argv.index("tmux")
    assert argv[0] == "systemd-run" and "--scope" in argv[:tmux] and argv[tmux + 1] == "new-session", argv
    assert launched_argv(True)[0] == "tmux", "without systemd the launch stays bare"

    assert poll_with_sessions([False, True]) == "running", "a session that appeared during the grace wait is alive"
    assert poll_with_sessions([False, False]) == "failed", "a session still gone with no EXIT line is a crash"
    print("test_job_launch OK")


if __name__ == "__main__":
    main()
