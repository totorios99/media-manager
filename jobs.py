"""tmux job launch, log polling, output verification, free-space guard."""
import os
import re
import shlex
import subprocess
import time

from scan import inspect_file

TMUX_PREFIX = "mm-"
HB_PROGRESS_RE = re.compile(r"Encoding:.*?(\d+\.\d+)\s*%")
MKV_PROGRESS_RE = re.compile(r"Progress:\s*(\d+)%")
EXIT_RE = re.compile(r"EXIT:(\d+)")
ETA_RE = re.compile(r"ETA\s+(\d+h\d+m\d+s)")

TYPE_RANK = {"video": 0, "audio": 1, "subtitle": 2}


# In Docker there is no user systemd; jobs run bare and CPU limits come from
# the container (compose `cpus:`). Live per-job quota control is host-only.
NO_SYSTEMD = bool(os.environ.get("MM_NO_SYSTEMD"))

# Encode scopes land as siblings of app-org.chromium.Chromium-*.scope under
# app.slice. CPUQuota caps total time but doesn't stop the encoder's many
# threads from winning CFS contests against a foreground app's few threads at
# equal weight — cgroup v2 splits time by group weight first, so a low weight
# here makes the browser/audio win every contested tick, independent of the
# quota schedule (applies in "full" mode too, not just "throttle").
CPU_WEIGHT = "10"


def _systemd_wrap(cmd_str, unit, cpu_quota):
    """Run cmd inside a named user scope so its CPU quota can be changed live:
    systemctl --user set-property --runtime <unit>.scope CPUQuota=N%.
    Env vars are set explicitly because the tmux server may predate the session bus vars."""
    if NO_SYSTEMD:
        return cmd_str
    uid = os.getuid()
    env = f"XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"
    quota = f"-p CPUQuota={cpu_quota} " if cpu_quota else ""
    return (f"{env} systemd-run --user --scope --unit={unit} "
            f"-p CPUWeight={CPU_WEIGHT} {quota}sh -c {shlex.quote(cmd_str)}")


def start_job(conn, movie_id, kind, cmd_str, log_dir, cpu_quota=None, queued=False):
    """cmd_str: full shell command (already built/joined, may contain && chains).
    queued=True only records the row; a ticker launches it when the runner is free."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        "INSERT INTO jobs (movie_id, kind, status, cmd, started_at) VALUES (?,?,?,?,?)",
        (movie_id, kind, "queued" if queued else "running", cmd_str, now),
    )
    job_id = cur.lastrowid
    conn.commit()
    if not queued:
        _launch(conn, job_id, cmd_str, log_dir, cpu_quota)
    return job_id


def _launch(conn, job_id, cmd_str, log_dir, cpu_quota):
    os.makedirs(log_dir, exist_ok=True)
    session = f"{TMUX_PREFIX}{job_id}"
    log_path = os.path.join(log_dir, f"job-{job_id}.log")
    wrapped = _systemd_wrap(cmd_str, f"mm-job-{job_id}", cpu_quota)
    full_cmd = f"{wrapped} >> {shlex.quote(log_path)} 2>&1; echo EXIT:$? >> {shlex.quote(log_path)}"
    conn.execute("UPDATE jobs SET status='running', tmux_session=?, log_path=?, started_at=? WHERE id=?",
                 (session, log_path, time.strftime("%Y-%m-%dT%H:%M:%S"), job_id))
    conn.commit()
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "sh", "-c", full_cmd], check=True)


def launch_queued(conn, job, log_dir, cpu_quota=None):
    _launch(conn, job["id"], job["cmd"], log_dir, cpu_quota)


def _handbrake_scopes():
    """flatpak launches HandBrakeCLI in its own transient scope
    (app-flatpak-fr.handbrake.ghb-*.scope), escaping mm-job-N.scope — so a quota
    set there only caps the idle launcher. Return the live flatpak app scopes so
    the real encoder can be throttled. Only one encode runs at a time, so setting
    all of them is safe."""
    r = subprocess.run(["systemctl", "--user", "list-units", "--plain", "--no-legend",
                        "app-flatpak-fr.handbrake.ghb-*.scope"], capture_output=True, text=True)
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.split()]


def set_cpu_quota(job_id, quota):
    """Non-flatpak jobs (mkvmerge/mkvpropedit) run inside mm-job-N.scope; encodes
    run in a flatpak app scope. Set both so throttle applies either way."""
    if NO_SYSTEMD:
        return
    for unit in [f"mm-job-{job_id}.scope", *_handbrake_scopes()]:
        subprocess.run(["systemctl", "--user", "set-property", "--runtime", unit,
                        f"CPUQuota={quota}", f"CPUWeight={CPU_WEIGHT}"], capture_output=True)


def tail(path, n=4096):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def scope_active(job_id):
    if NO_SYSTEMD:
        # container tmux has no session-restore plugin, so has-session is honest here
        return session_alive(f"{TMUX_PREFIX}{job_id}")
    r = subprocess.run(["systemctl", "--user", "is-active", f"mm-job-{job_id}.scope"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def session_alive(session):
    r = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True)
    return r.returncode == 0


def poll_job(conn, job_id):
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job or job["status"] != "running":
        return job
    text = tail(job["log_path"]) if job["log_path"] else ""
    exit_matches = EXIT_RE.findall(text)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if exit_matches:
        code = int(exit_matches[-1])
        status = "done" if code == 0 else "failed"
        conn.execute("UPDATE jobs SET status=?, exit_code=?, finished_at=?, progress=100 WHERE id=?",
                     (status, code, now, job_id))
        conn.commit()
    elif job["tmux_session"] and not session_alive(job["tmux_session"]):
        conn.execute("UPDATE jobs SET status='failed', finished_at=? WHERE id=?", (now, job_id))
        conn.commit()
    else:
        progress = job["progress"] or 0
        flat = text.replace("\r", "\n")
        hb, mkv = HB_PROGRESS_RE.findall(flat), MKV_PROGRESS_RE.findall(flat)
        if hb:
            progress = float(hb[-1])
        elif mkv:
            progress = float(mkv[-1])
        conn.execute("UPDATE jobs SET progress=? WHERE id=?", (progress, job_id))
        conn.commit()
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def verify_output(out_path, kept_tracks, source_duration=None):
    """kept_tracks: rows (dicts) from `tracks` table with keep=1, already the config
    we asked for. Compares output-order sequence of type/lang/default/forced."""
    if not os.path.exists(out_path):
        return False, "output file missing"
    info = inspect_file(out_path)
    got = info["tracks"]
    if len(got) != len(kept_tracks):
        return False, f"track count mismatch: expected {len(kept_tracks)} got {len(got)}"

    exp_sorted = sorted(kept_tracks, key=lambda t: (TYPE_RANK[t["type"]], t["out_order"]))
    got_sorted = sorted(got, key=lambda t: (TYPE_RANK[t["type"]], t["mkv_id"]))
    for e, g in zip(exp_sorted, got_sorted):
        if e["type"] != g["type"]:
            return False, f"type mismatch: expected {e['type']} got {g['type']}"
        if e["out_lang"] and g["lang"] != e["out_lang"]:
            return False, f"lang mismatch on {e['type']}: expected {e['out_lang']} got {g['lang']}"
        if bool(e["out_default"]) != bool(g["default_flag"]):
            return False, f"default flag mismatch on {e['type']}"
        if e["type"] == "subtitle" and bool(e["out_forced"]) != bool(g["forced_flag"]):
            return False, "forced flag mismatch"
    if source_duration and info["duration"]:
        if abs(info["duration"] - source_duration) > 2:
            return False, f"duration mismatch: {info['duration']:.1f}s vs {source_duration:.1f}s"
    return True, "ok"


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def has_space_for(path, source_size_bytes, factor=1.2):
    if not source_size_bytes:
        return True
    return free_bytes(path) >= source_size_bytes * factor


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "job-1.log")
        with open(log, "w") as f:
            f.write("Encoding: task 1 of 1, 12.34 %\rEncoding: task 1 of 1, 55.00 %\nEXIT:0\n")
        text = tail(log).replace("\r", "\n")
        m = HB_PROGRESS_RE.findall(text)
        assert m[-1] == "55.00", m
        assert EXIT_RE.findall(text)[-1] == "0"

        log2 = os.path.join(d, "job-2.log")
        with open(log2, "w") as f:
            f.write("Progress: 10%\nProgress: 42%\nEXIT:1\n")
        text2 = tail(log2)
        assert MKV_PROGRESS_RE.findall(text2)[-1] == "42"
        assert EXIT_RE.findall(text2)[-1] == "1"
    print("jobs.py self-check OK")
