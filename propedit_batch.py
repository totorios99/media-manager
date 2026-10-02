"""Queue the app's own propedit job for every file the audit flagged on audio defaults or metadata.

    propedit_batch.py                 # list what would be queued
    propedit_batch.py --run [--limit N] [--only movie|episode]
    propedit_batch.py --verify        # re-audit the queued files, report fixed / still failing

Needs library_audit.json (library_audit.py). Per file: POST .../suggest (convention:
original language first, TrueHD/Atmos never default, video language stamped), then POST
.../jobs {"kind":"propedit"}. The app refuses a propedit that would drop or add a track
(castellano removal, external subs): those answer 400 and stay for the remux pass.
Subtitle tagging (es-419) is NOT done here: the propedit chain writes language=spa only.
"""
import json, os, re, sqlite3, sys, urllib.request, urllib.error

import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("MM_API", "http://localhost:8500")
LOG = os.path.join(HERE, "propedit_batch.jsonl")
PICK = re.compile(r"^(audio: (default|2 defaults)|meta:)")


def post(path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def candidates(only=None):
    audit = json.load(open(os.path.join(HERE, "library_audit.json")))
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    out = []
    for label, why in audit.get("movie", {}).items():
        if only in (None, "movie") and any(PICK.match(r) for r in why):
            row = conn.execute("SELECT id FROM movies WHERE folder=?", (label,)).fetchone()
            if row:
                out.append(("movie", row[0], label, [r for r in why if PICK.match(r)]))
    for label, why in audit.get("episode", {}).items():
        if only in (None, "episode") and any(PICK.match(r) for r in why):
            show, se = label.split("|")
            s, e = int(se[1:3]), int(se[4:6])
            row = conn.execute("SELECT e.id FROM episodes e JOIN shows h ON h.id=e.show_id "
                               "WHERE h.folder=? AND e.season=? AND e.episode=?", (show, s, e)).fetchone()
            if row:
                out.append(("episode", row[0], label, [r for r in why if PICK.match(r)]))
    return out


def run(cands):
    log = open(LOG, "a")
    for kind, id_, label, why in cands:
        base = f"/api/{kind}s/{id_}"
        sc, _ = post(base + "/suggest")
        if sc != 200:
            res = {"step": "suggest", "status": sc}
        else:
            sc, body = post(base + "/jobs", {"kind": "propedit"})
            res = {"step": "job", "status": sc, "detail": body.get("detail") or body.get("job_id")}
        log.write(json.dumps({"kind": kind, "id": id_, "label": label, "why": why, **res}, ensure_ascii=False) + "\n")
        log.flush()
        print(f"{res['status']} {kind} {label[:60]} {res.get('detail') or ''}"[:140], flush=True)


def verify():
    rows = [json.loads(l) for l in open(LOG)]
    ids = {(r["kind"], r["id"]): r for r in rows if r["step"] == "job" and r["status"] == 200}
    conn = sqlite3.connect(os.path.join(HERE, "media.db"))
    fixed = still = 0
    for (kind, id_), r in ids.items():
        if kind == "movie":
            q = conn.execute("SELECT folder, file, original_language, animation FROM movies WHERE id=?", (id_,)).fetchone()
            path, orig, anim = os.path.join(la.MOVIES, q[0], q[1]), q[2], q[3]
        else:
            q = conn.execute("SELECT s.folder, e.folder, e.file, s.original_language, s.animation FROM episodes e "
                             "JOIN shows s ON s.id=e.show_id WHERE e.id=?", (id_,)).fetchone()
            path, orig, anim = os.path.join(la.SHOWS, q[0], q[1], q[2]), q[3], q[4]
        _, _, bad = la.audit_one((kind, r["label"], path, orig, anim))
        left = [b for b in bad if PICK.match(b)]
        fixed += not left
        still += bool(left)
        if left:
            print("STILL", r["label"][:50], left)
    print(f"{fixed} fixed, {still} still failing (of {len(ids)} jobs accepted)")


if __name__ == "__main__":
    a = sys.argv[1:]
    only = a[a.index("--only") + 1] if "--only" in a else None
    c = candidates(only)
    if "--limit" in a:
        c = c[: int(a[a.index("--limit") + 1])]
    if "--verify" in a:
        verify()
    elif "--run" in a:
        run(c)
    else:
        print(len(c), "files:", {k: sum(1 for x in c if x[0] == k) for k in ("movie", "episode")})
        for x in c[:12]:
            print(" ", x[0], x[2][:55], x[3][:2])
