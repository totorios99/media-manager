"""app._repair_or_recycle: a rejected sub is fixed against the embedded subtitle, or leaves the player's view."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402
import subs_clean as sc  # noqa: E402


def test_reject_recycle():
    root = tempfile.mkdtemp()
    movies = os.path.join(root, "Movies")
    d = os.path.join(movies, "Film (2014)")
    os.makedirs(d)
    app.MEDIA_ROOT, app.SHOWS_ROOT = movies, os.path.join(root, "Shows")
    app.REJECTED_LOG = os.path.join(root, "rejected_subs.jsonl")
    times = [60 + i * 6.7 for i in range(900)]
    ref = os.path.join(root, "ref.srt")
    open(ref, "w", encoding="utf-8").write(sc.render([(t, t + 2, "en") for t in times]))
    sub = os.path.join(d, "Film (2014).es-MX.srt")
    open(sub, "w", encoding="utf-8").write(sc.render([(t - 0.8, t + 1, "es") for t in times]))
    rej = {"status": "rejected", "ads": 0, "action": "windows off", "offset": 0.0, "factor": 1.0,
           "media": os.path.join(d, "Film (2014).mkv"), "written": False}

    app._embedded_reference_srt = lambda media, extracted: ref
    r = app._repair_or_recycle(sub, rej)
    assert r["status"] == "ok" and r["written"] and "reference shift" in r["action"], r
    assert abs(sc.parse(sc.decode(open(sub, "rb").read()))[0][0] - 60) < 0.01
    assert os.path.exists(sub + ".orig")

    app._REFS.clear()                                                    # per-job cache (the job drops it)
    app._embedded_reference_srt = lambda media, extracted: None        # nothing to measure against
    r = app._repair_or_recycle(sub, rej)
    assert r["status"] == "rejected" and not os.path.exists(sub), r
    assert os.path.exists(os.path.join(root, ".recycle", "Movies", "Film (2014)", "Film (2014).es-MX.srt"))
    logged = json.loads(open(app.REJECTED_LOG).read().splitlines()[-1])
    assert logged["lang"] == "spa" and logged["path"].endswith("Film (2014).mkv"), logged
    # no embedded text track, but a sidecar the hook already verified: it becomes the reference (Allegiant, 10-21)
    app._REFS.clear()
    en = os.path.join(d, "Film (2014).en.srt")
    open(en, "w", encoding="utf-8").write(sc.render([(t, t + 2, "en") for t in times]))
    app.SERVER_LOG = os.path.join(root, "server.log")
    open(app.SERVER_LOG, "w").write("[subs] Film (2014).en.srt: ok ads=0 none; windows\n")
    open(sub, "w", encoding="utf-8").write(sc.render([(t - 0.8, t + 1, "es") for t in times]))
    r = app._repair_or_recycle(sub, rej)
    assert r["status"] == "ok" and "reference" in r["action"], r
    print("test_reject_recycle OK")


if __name__ == "__main__":
    test_reject_recycle()
