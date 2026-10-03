"""Unattended supervisor for the long jobs. Detached; every 5 minutes it checks, repairs what is safe to repair,
alerts (ntfy) on what is not, and rewrites /var/tmp/overnight_report.md.

    setsid nohup uv run python -u overnight.py > /var/tmp/overnight.log 2>&1 < /dev/null &

Phase 1  dbz_castellano.py --run  (resumable). Dead and not finished -> relaunch (max 3). 3 new FAILED lines -> kill
         and alert: never keep rewriting files after verification starts failing. No progress for 90 min -> alert.
Phase 2  once DBZ has nothing left to drop: meta_fix --run (DBZ default + es-419), then subs_queue.py --run (sidecars
         never filtered) and subs_variant_pass.py (Latino or castellano by content), both resumable.
Always   media-manager.service down -> restart (max once per 15 min). A restart empties the in-process subtitle
         queue, so subs_queue.py --run is repeated afterwards (idempotent). Stale .castfix.mkv left by a dead run
         -> removed. Disk under 100 GB free -> alert.
It never touches Radarr, Sonarr, Jellyfin, Bazarr or any file outside what those scripts already own.
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = "/var/tmp"
STATE = f"{OUT}/overnight_state.json"
REPORT = f"{OUT}/overnight_report.md"
DBZ_DIR = "/srv/storage/Shows/Dragon Ball Z (1989) [tvdbid-81472]"
NTFY = os.environ.get("NTFY_URL", "http://172.17.0.1:8095/media")
EVERY = 300


def env():
    e = dict(os.environ)
    for ln in open(os.path.join(HERE, ".env.systemd")):
        if "=" in ln and not ln.startswith("#"):
            k, _, v = ln.strip().partition("=")
            e[k] = v.strip("'\"")
    return e


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def load():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {"alerts": {}, "dbz_restarts": 0, "phase": 1, "failed_seen": 0, "svc_restart_at": 0}


def alert(st, key, title, body, prio=4):
    """ntfy, at most once per key per 2 hours."""
    if time.time() - st["alerts"].get(key, 0) < 7200:
        return
    st["alerts"][key] = time.time()
    log(f"ALERT {title}: {body}")
    try:
        urllib.request.urlopen(urllib.request.Request(NTFY, data=body.encode(), headers={
            "Title": title, "Priority": str(prio), "Tags": "warning"}), timeout=10).read()
    except Exception as e:
        log(f"ntfy failed: {e}")


def spawn(args, out, pidfile=None):
    f = open(out, "a")
    p = subprocess.Popen(["setsid", "uv", "run", "python", "-u", *args], cwd=HERE, env=env(), stdout=f, stderr=f,
                         stdin=subprocess.DEVNULL)
    if pidfile:
        open(pidfile, "w").write(f"pid {p.pid}\n")
    return p.pid


def dbz_status():
    lines = [json.loads(l) for l in open(f"{HERE}/dbz_castellano.jsonl")] if os.path.exists(f"{HERE}/dbz_castellano.jsonl") else []
    ok = {l["id"] for l in lines if l["status"] == "ok"}
    failed = sum(l["status"] == "FAILED" for l in lines)
    last = os.path.getmtime(f"{HERE}/dbz_castellano.jsonl") if lines else 0
    return len(ok), failed, last


def dbz_left():
    """Episodes that still have a second Spanish track (a re-plan; ~5-10 min of probing, so only when idle)."""
    r = sh(["uv", "run", "python", "dbz_castellano.py"], cwd=HERE, env=env())
    m = re.search(r"\{'name': (\d+), 'order': (\d+)", r.stdout)
    return int(m.group(1)) + int(m.group(2)) if m else None


def pid_of(name):
    try:
        return open(f"{OUT}/{name}.pid").read().split()[-1]
    except OSError:
        return None


def cycle(st):
    now = time.time()
    notes = []
    # ---- service
    active = sh(["systemctl", "--user", "is-active", "media-manager"], timeout=30).stdout.strip()
    main = sh(["systemctl", "--user", "show", "media-manager", "-p", "ActiveEnterTimestampMonotonic", "--value"],
              timeout=30).stdout.strip()
    if active != "active":
        if now - st["svc_restart_at"] > 900:
            st["svc_restart_at"] = now
            sh(["systemctl", "--user", "restart", "media-manager"], timeout=60)
            alert(st, "svc", "media-manager caído", "El servicio no estaba activo; lo reinicié.")
            notes.append("service was down: restarted")
        else:
            alert(st, "svc2", "media-manager sigue caído", "Reinicié hace poco y no levanta; mira server.log.", 5)
    elif st.get("svc_start") not in (None, main) and st["phase"] >= 2:
        # restarted by someone: the in-process subtitle queue is gone, re-post what is left (idempotent)
        spawn(["subs_queue.py", "--run"], f"{OUT}/subs_queue.out")
        notes.append("service restarted: subs_queue re-posted")
    st["svc_start"] = main

    # ---- phase 1: DBZ
    ok, failed, last = dbz_status()
    pid = pid_of("dbz_run")
    if st["phase"] == 1:
        if failed - st["failed_seen"] >= 3:
            if alive(pid):
                sh(["pkill", "-TERM", "-P", str(pid)])
                os.kill(int(pid), 15)
            alert(st, "dbzfail", "DBZ detenido", f"{failed - st['failed_seen']} fallos de verificación seguidos: paré el remux. Nada se borró sin verificar.", 5)
            st["phase"] = 0
        elif alive(pid):
            if last and now - last > 5400:
                alert(st, "dbzstall", "DBZ sin avance", "No hay episodios nuevos desde hace más de 90 min.")
            notes.append(f"DBZ running: {ok} episodes done")
        else:
            left = dbz_left()
            if left == 0:
                st["phase"] = 2
                alert(st, "dbzdone", "DBZ terminado", f"{ok} episodios sin castellano. Sigo con metadata, sidecars y variante.", 3)
            elif st["dbz_restarts"] < 3:
                st["dbz_restarts"] += 1
                spawn(["dbz_castellano.py", "--run"], f"{OUT}/dbz_run.out", f"{OUT}/dbz_run.pid")
                alert(st, f"dbzrestart{st['dbz_restarts']}", "DBZ relanzado", f"El proceso había muerto con {left} pendientes; reintento {st['dbz_restarts']}/3.")
                notes.append("DBZ died: relaunched")
            else:
                alert(st, "dbzgiveup", "DBZ no avanza", f"Murió 3 veces con {left} pendientes. Lo dejo parado.", 5)
                st["phase"] = 0
        # stale temp files from a dead run
        if not alive(pid_of("dbz_run")):
            for d, _, fs in os.walk(DBZ_DIR):
                for f in fs:
                    p = os.path.join(d, f)
                    if f.endswith(".castfix.mkv") and now - os.path.getmtime(p) > 7200:
                        os.remove(p)
                        notes.append(f"removed stale {f[:40]}")

    # ---- phase 2
    if st["phase"] == 2:
        if not st.get("metafix_done"):
            r = sh(["uv", "run", "python", "-u", "meta_fix.py", "--run"], cwd=HERE, env=env())
            open(f"{OUT}/metafix_dbz.out", "w").write(r.stdout + r.stderr)
            st["metafix_done"] = True
            alert(st, "metafix", "Metadata de DBZ lista", (re.findall(r"\d+ edited.*", r.stdout) or ["sin resumen"])[-1], 3)
        if not st.get("queue_started"):
            spawn(["subs_queue.py", "--run"], f"{OUT}/subs_queue.out")
            spawn(["subs_variant_pass.py"], f"{OUT}/variant_pass.out", f"{OUT}/variant_pass.pid")
            st["queue_started"] = st["variant_started"] = True
            alert(st, "phase2", "Cola nocturna en marcha", "Filtro de sidecars y pasada latino/castellano arrancados.", 3)
        elif not alive(pid_of("variant_pass")) and not st.get("variant_done"):
            tail = open(f"{OUT}/variant_pass.out").read()[-400:] if os.path.exists(f"{OUT}/variant_pass.out") else ""
            if "== verdicts" in tail:
                st["variant_done"] = True
                alert(st, "variantdone", "Pasada latino/castellano terminada", tail.strip().splitlines()[-1][:180], 3)
            elif st.get("variant_restarts", 0) < 2:
                st["variant_restarts"] = st.get("variant_restarts", 0) + 1
                spawn(["subs_variant_pass.py"], f"{OUT}/variant_pass.out", f"{OUT}/variant_pass.pid")
                notes.append("variant pass died: relaunched")

    # ---- disk
    free = shutil.disk_usage("/srv/storage").free / 1e9
    if free < 100:
        alert(st, "disk", "Poco disco libre", f"Quedan {free:.0f} GB en /srv/storage.", 5)
    return ok, failed, active, free, notes


def report(st, ok, failed, active, free, notes):
    subs = sh(["sh", "-c", f"grep -c '^\\[subs\\]' {HERE}/server.log"]).stdout.strip()
    vp = ""
    if os.path.exists(f"{OUT}/variant_pass.out"):
        vp = open(f"{OUT}/variant_pass.out").read().strip().splitlines()[-1:] or [""]
        vp = vp[0][:120]
    open(REPORT, "w").write(
        f"# Informe nocturno ({time.strftime('%F %T')})\n\n"
        f"- fase: {st['phase']} (1 = DBZ, 2 = cola, 0 = parado por seguridad)\n"
        f"- DBZ: {ok} episodios hechos, {failed} fallos de verificación, {st['dbz_restarts']} relanzamientos\n"
        f"- servicio media-manager: {active}\n- disco libre: {free:.0f} GB\n"
        f"- líneas [subs] en server.log: {subs}\n- última línea de la pasada de variante: {vp}\n"
        f"- avisos enviados: {sorted(st['alerts'])}\n- este ciclo: {notes or 'sin novedades'}\n")


if __name__ == "__main__":
    st = load()
    st["failed_seen"] = st.get("failed_seen", dbz_status()[1]) if os.path.exists(STATE) else dbz_status()[1]
    once = "--once" in sys.argv
    if not once:
        alert(st, "start", "Supervisor nocturno activo", "Vigilo DBZ, el servicio y la cola. Informe en /var/tmp/overnight_report.md", 2)
    while True:
        try:
            res = cycle(st)
            report(st, *res)
        except Exception as e:                          # a supervisor that dies is worse than a noisy one
            log(f"cycle error: {e!r}")
        json.dump(st, open(STATE, "w"))
        if once:
            break
        time.sleep(EVERY)
