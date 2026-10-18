"""Sidecars the hook rejected before 10-17 are still next to their videos, and Jellyfin offers them (Antonio watched
Need for Speed with one). Each goes through app._repair_or_recycle, the same step the hook now runs on a rejection:
fixed against the embedded subtitle, or moved to .recycle and listed for bazarr_watch.

    rejected_recheck.py [--run] [--weak]

--weak takes the sidecars accepted without a real check instead ("unreliable windows" / "unmeasured": the audio
could not confirm them; Underworld EN, Avengers Infinity War, Project Hail Mary ES, Return of the Jedi EN).

One file at a time, inside the MM_NIGHT window (the reference extraction reads the whole video). The verdict is
written to server.log as a normal [subs] line, so a re-run skips what is no longer rejected.
"""
import glob, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LINE = re.compile(r"\[subs\] (.+?\.srt): (\w+)(.*)")
WEAK = re.compile(r"unreliable windows|unmeasured")


def still_rejected(log_path, roots, weak=False):
    last = {}
    for l in open(log_path, encoding="utf-8", errors="replace"):
        m = LINE.match(l)
        if m and m.group(2) != "renamed":
            last[m.group(1)] = (m.group(2), m.group(3))
    names = {k for k, (v, rest) in last.items()
             if ((v == "ok" and WEAK.search(rest)) if weak else v == "rejected")}
    return [p for root in roots for p in glob.glob(glob.escape(root) + "/**/*.srt", recursive=True)
            if os.path.basename(p) in names]


def main(run, weak):
    import app, overnight
    todo = still_rejected(os.path.join(HERE, "server.log"), [app.MEDIA_ROOT, app.SHOWS_ROOT], weak)
    print(len(todo), "weakly checked" if weak else "rejected", "sidecars on disk", flush=True)
    for sub in todo:
        print(("check " if run else "plan ") + sub, flush=True)
        if not run:
            continue
        while not overnight.in_window():
            time.sleep(300)
        media = app._media_for_subtitle(sub)
        if not media:
            continue
        r = app._repair_or_recycle(sub, {"status": "rejected", "ads": 0, "action":
                                         "weak check before 10-17" if weak else "rejected before 10-17",
                                         "offset": 0.0, "factor": 1.0, "media": media, "written": False},
                                   keep_without_reference=weak)
        name = os.path.basename(sub)
        if r["status"] == "ok":
            name = os.path.basename(app._unmark_false_sdh(sub))
        line = (f"[subs] {name}: {r['status']} ads={r['ads']} {r['action']} "
                f"final={r['offset']:+.2f}s/{r['factor']:.4f} written={r['written']}")
        if r["status"] != "ok":
            line = line.replace(": rejected ", ": recycled ", 1)    # no longer "rejected": a re-run skips it
        open(os.path.join(HERE, "server.log"), "a", encoding="utf-8").write(line + "\n")
        print(line, flush=True)


if __name__ == "__main__":
    main("--run" in sys.argv, "--weak" in sys.argv)
