"""Night supervisor for the long, disk-heavy jobs. Detached; checks every 5 minutes and rewrites
/var/tmp/overnight_report.md.

    setsid nohup uv run python -u overnight.py > /var/tmp/overnight.log 2>&1 < /dev/null &

Why it exists in this shape: on 2026-10-03 the library disk (JMicron USB 2.0 bridge) reset under sustained mixed load
and ext4 shut down. Until there is SATA/UASP, long jobs run

  * only inside a night window (MM_NIGHT=0-7, local hours, start inclusive),
  * one at a time, in this order: the sidecar subtitle queue (subs_queue.py --paced), then the Latino/castellano
    variant pass (subs_variant_pass.py); both resumable, both at idle I/O priority for the heavy reads,
  * never while /srv/storage is not a mounted filesystem: everything is stopped and one alert is sent.

Outside the window a running job gets SIGTERM (the service may finish the one file it already has, up to ~25 min).
A job that dies inside the window with work left is relaunched at most twice per night. media-manager.service down ->
restarted (once per 15 min). It never touches Radarr, Sonarr, Jellyfin, Bazarr, Transmission or the disk's contents.
"""
import base64, json, os, re, shutil, signal, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = "/var/tmp"
STATE, REPORT = f"{OUT}/overnight_state.json", f"{OUT}/overnight_report.md"
MOUNT = "/srv/storage"
WINDOW = os.environ.get("MM_NIGHT", "0-7")
NTFY = os.environ.get("NTFY_URL", "http://172.17.0.1:8095/media")
EVERY = 300
JOBS = [("queue", ["subs_queue.py", "--paced"]), ("variant", ["subs_variant_pass.py"])]


def in_window(hour=None, window=WINDOW):
    h = time.localtime().tm_hour if hour is None else hour
    a, b = (int(x) for x in window.split("-"))
    return a <= h < b if a < b else (h >= a or h < b)


def mounted():
    return os.path.ismount(MOUNT) and os.path.isdir(f"{MOUNT}/Movies")


def env():
    e = dict(os.environ)
    for ln in open(os.path.join(HERE, ".env.systemd")):
        if "=" in ln and not ln.startswith("#"):
            k, _, v = ln.strip().partition("=")
            e[k] = v.strip("'\"")
    return e


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def pid_of(name):
    try:
        return int(open(f"{OUT}/night_{name}.pid").read().split()[-1])
    except (OSError, ValueError):
        return None


def start(name, args):
    f = open(f"{OUT}/night_{name}.out", "a")
    p = subprocess.Popen(["uv", "run", "python", "-u", *args], cwd=HERE, env=env(), stdout=f, stderr=f,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    open(f"{OUT}/night_{name}.pid", "w").write(f"pid {p.pid}\n")
    log(f"started {name} (pid {p.pid})")


def stop(name):
    pid = pid_of(name)
    if alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)       # uv + its python child
        except OSError:
            pass
        log(f"stopped {name} (pid {pid})")
        return True
    return False


def load():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {"alerts": {}, "svc_restart_at": 0, "restarts": {}, "done": {}}


def alert(st, key, title, body, prio=4):
    """ntfy, at most once per key per 2 hours. The title goes out as an RFC 2047 encoded-word: a raw "í" in an
    HTTP header reaches ntfy as mojibake."""
    if time.time() - st["alerts"].get(key, 0) < 7200:
        return
    st["alerts"][key] = time.time()
    log(f"ALERT {title}: {body}")
    if not title.isascii():
        title = "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?="
    try:
        urllib.request.urlopen(urllib.request.Request(NTFY, data=body.encode(), headers={
            "Title": title, "Priority": str(prio), "Tags": "warning"}), timeout=10).read()
    except Exception as e:
        log(f"ntfy failed: {e}")


def queue_pending():
    sys.path.insert(0, HERE)
    import subs_queue
    tried = json.load(open(subs_queue.TRIED)) if os.path.exists(subs_queue.TRIED) else {}
    return [p for _, p in subs_queue.unfiltered() if tried.get(p, 0) < 2]


def job_done(name, st):
    if name == "queue":
        return bool(st["done"].get("queue")) or not queue_pending()
    out = f"{OUT}/night_variant.out"
    return bool(st["done"].get("variant")) or (os.path.exists(out) and "== verdicts" in open(out).read()[-600:])


def cycle(st):
    now, notes = time.time(), []
    if not mounted():
        for n, _ in JOBS:
            stop(n)
        alert(st, "mount", "Disco de la biblioteca no montado",
              "/srv/storage no está montado: paré las colas nocturnas y no lanzo nada hasta que vuelva.", 5)
        return {"mounted": False, "notes": ["DISK NOT MOUNTED: all jobs stopped"]}
    st["alerts"].pop("mount", None)
    active = subprocess.run(["systemctl", "--user", "is-active", "media-manager"], capture_output=True, text=True,
                            timeout=30).stdout.strip()
    if active != "active" and now - st["svc_restart_at"] > 900:
        st["svc_restart_at"] = now
        subprocess.run(["systemctl", "--user", "restart", "media-manager"], timeout=60)
        alert(st, "svc", "media-manager caído", "El servicio no estaba activo; lo reinicié.")
        notes.append("service was down: restarted")
    window, today = in_window(), time.strftime("%F")
    want = next((n for n, _ in JOBS if not job_done(n, st)), None)
    for name, args in JOBS:
        running = alive(pid_of(name))
        if name == want and window:
            if not running:
                n = st["restarts"].get(f"{name}:{today}", 0)
                if n < 3:                                    # first start + at most two relaunches per night
                    st["restarts"][f"{name}:{today}"] = n + 1
                    start(name, args)
                    notes.append(f"{name} started" if n == 0 else f"{name} relaunched ({n}/2)")
                else:
                    alert(st, f"giveup:{name}", f"Cola nocturna {name} no avanza",
                          "Murió 3 veces esta noche; la dejo parada hasta mañana.")
        elif running:
            stop(name)
            notes.append(f"{name} stopped ({'window closed' if not window else 'another job has priority'})")
    if want is None:
        alert(st, "alldone", "Colas nocturnas terminadas", "Sidecars y pasada latino/castellano completos.", 3)
    free = shutil.disk_usage(MOUNT).free / 1e9
    if free < 100:
        alert(st, "disk", "Poco disco libre", f"Quedan {free:.0f} GB en {MOUNT}.", 5)
    return {"mounted": True, "service": active, "window": window, "want": want, "free": free, "notes": notes}


def report(st, r):
    pend = len(queue_pending()) if r.get("mounted") else "?"
    vp = ""
    if os.path.exists(f"{OUT}/night_variant.out"):
        vp = (open(f"{OUT}/night_variant.out").read().strip().splitlines() or [""])[-1][:110]
    open(REPORT, "w").write(
        f"# Informe nocturno ({time.strftime('%F %T')})\n\n"
        f"- disco montado: {r.get('mounted')} | servicio: {r.get('service')} | libre: {r.get('free', 0):.0f} GB\n"
        f"- ventana {WINDOW} abierta ahora: {r.get('window')} | trabajo en turno: {r.get('want')}\n"
        f"- sidecars pendientes: {pend}\n- última línea de la pasada de variante: {vp}\n"
        f"- avisos enviados: {sorted(k for k in st['alerts'])}\n- este ciclo: {r.get('notes') or 'sin novedades'}\n")


if __name__ == "__main__":
    st, once = load(), "--once" in sys.argv
    if not once:
        alert(st, "start", "Supervisor nocturno activo", f"Ventana {WINDOW}, un trabajo cada vez, comprueba el montaje.", 2)
    while True:
        try:
            r = cycle(st)
            report(st, r)
        except Exception as e:                              # a supervisor that dies is worse than a noisy one
            log(f"cycle error: {e!r}")
        json.dump(st, open(STATE, "w"))
        if once:
            break
        time.sleep(EVERY)
