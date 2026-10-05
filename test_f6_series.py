"""f6_series: the in-place remux keeps every kept stream bit for bit, drops what the plan drops, keeps chapters;
nothing starts while a download is growing."""
import os, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f6_series as f6  # noqa: E402
import library_audit as la  # noqa: E402


def row(t, mkv_id, lang, keep=1, order=0, default=0, codec="", ext=None):
    return {"type": t, "mkv_id": mkv_id, "lang": lang, "codec": codec, "ext_path": ext, "keep": keep,
            "out_order": order, "out_lang": lang, "out_default": default, "out_forced": 0, "out_name": "",
            "default_flag": 0, "forced_flag": 0, "sdh_flag": 0, "commentary_flag": 0}


def make_episode(d):
    base = os.path.join(d, "b.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:d=2", "-shortest", "-c:a", "aac", "-c:v", "libx264", base], check=True)
    srt = lambda n, txt: open(os.path.join(d, n), "w").write(f"1\n00:00:00,100 --> 00:00:01,500\n{txt}\n")
    srt("es.srt", "Hola"); srt("fr.srt", "Bonjour"); srt("en.srt", "Hello")
    open(os.path.join(d, "ch.txt"), "w").write("CHAPTER01=00:00:00.000\nCHAPTER01NAME=OP\n"
                                                "CHAPTER02=00:00:01.000\nCHAPTER02NAME=Episodio\n")
    ep = os.path.join(d, "Show - S01E01 - Title.mkv")
    subprocess.run(["mkvmerge", "-q", "-o", ep, "--chapters", os.path.join(d, "ch.txt"),
                    "--language", "0:eng", "--language", "1:eng", base,
                    "--language", "1:spa", "-D", "-S", base,            # second audio track, Spanish
                    "--language", "0:spa", os.path.join(d, "es.srt"),
                    "--language", "0:fre", os.path.join(d, "fr.srt"),
                    "--language", "0:eng", os.path.join(d, "en.srt")], capture_output=True)   # rc 1: UID warning
    assert os.path.exists(ep)
    return ep


def test_remux_in_place_drops_french_and_keeps_the_rest():
    d = tempfile.mkdtemp()
    ep = make_episode(d)
    ids = {(t["type"], t["properties"]["language"]): t["id"] for t in la.probe(ep)["tracks"]}
    assert len(ids) == 6, ids
    rows = [row("video", 0, "eng", default=1),
            row("audio", 1, "eng", order=0, default=1), row("audio", 2, "spa", order=1),
            row("subtitle", 3, "spa", order=0, default=1, codec="SubRip/SRT"),
            row("subtitle", 4, "fre", keep=0, codec="SubRip/SRT"),
            row("subtitle", 5, "eng", order=1, codec="SubRip/SRT")]
    before = f6.chapters(ep)
    err = f6.remux_verified(ep, rows)
    assert err is None, err
    tr = la.probe(ep)["tracks"]
    assert [t["properties"]["language"] for t in tr] == ["eng", "eng", "spa", "spa", "eng"], tr
    assert f6.chapters(ep) == before and before[1] == ["OP", "Episodio"]
    assert not [f for f in os.listdir(d) if f.endswith(".f6.mkv")], "temp file left behind"
    assert os.path.basename(ep) in os.listdir(d)                 # same name: the episode title survives
    print("test_remux_in_place_drops_french_and_keeps_the_rest OK")


def test_refused_plan_leaves_the_original_alone():
    d = tempfile.mkdtemp()
    ep = make_episode(d)
    size, mtime = os.path.getsize(ep), os.path.getmtime(ep)
    rows = [row("video", 0, "eng", default=1), row("audio", 1, "eng", default=1), row("audio", 2, "spa", order=1),
            row("subtitle", 3, "spa", default=1, codec="SubRip/SRT"),
            row("subtitle", 4, "fre", keep=0), row("subtitle", 5, "eng", order=1)]
    real = f6.stream_md5
    calls = []
    f6.stream_md5 = lambda p, ids, normalize=False: calls.append(p) or (["a"] * len(ids) if len(calls) % 2 else ["b"] * len(ids))
    try:                                                         # a stream that changed in the copy must stop it
        err = f6.remux_verified(ep, rows)
    finally:
        f6.stream_md5 = real
    assert err == "stream hashes differ", err
    assert (os.path.getsize(ep), os.path.getmtime(ep)) == (size, mtime)
    assert not [f for f in os.listdir(d) if f.endswith(".f6.mkv")]
    print("test_refused_plan_leaves_the_original_alone OK")


def test_downloads_busy():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.mkv.part")
    open(p, "w").write("x")
    assert f6.downloads_busy(d)
    os.utime(p, (time.time() - 3600, time.time() - 3600))
    assert not f6.downloads_busy(d)
    print("test_downloads_busy OK")


if __name__ == "__main__":
    test_remux_in_place_drops_french_and_keeps_the_rest(); test_refused_plan_leaves_the_original_alone(); test_downloads_busy()
