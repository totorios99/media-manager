"""preflight must reject a config whose track ids no longer match the source.

This is the failure that burned jobs 743/744: Radarr replaced the file, the
stored config still addressed the old ids, and mkvmerge cheerfully muxed the
wrong tracks for 67 minutes before verify_output noticed.
"""
import os, subprocess, tempfile
import jobs
from scan import inspect_file


def _mkv(path, langs):
    """One tiny video track plus one audio track per language, in order."""
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1"]
    for _ in langs:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    cmd += ["-map", "0:v"]
    for i, _ in enumerate(langs, start=1):
        cmd += ["-map", f"{i}:a"]
    cmd += ["-c:v", "libx264", "-c:a", "ac3"]
    for i, l in enumerate(langs):
        cmd += [f"-metadata:s:a:{i}", f"language={l}"]
    subprocess.run(cmd + [path], check=True)
    return path


def _config(path):
    """The config as suggest_tracks would store it for this exact file."""
    return [{"mkv_id": t["mkv_id"], "type": t["type"], "codec": t["codec"],
             "lang": t["lang"], "ext_path": None}
            for t in inspect_file(path)["tracks"]]


def main():
    with tempfile.TemporaryDirectory() as d:
        original = _mkv(os.path.join(d, "orig.mkv"), ["eng", "spa"])
        cfg = _config(original)

        ok, msg = jobs.preflight(original, cfg)
        assert ok, f"config matching its own file must pass: {msg}"

        # the upgrade case: same title, more audio tracks, so every id shifts
        replaced = _mkv(os.path.join(d, "new.mkv"), ["eng", "fre", "spa"])
        ok, msg = jobs.preflight(replaced, cfg)
        assert not ok, "a config pointing at the old track layout must be refused"
        assert "escanea" in msg, f"message should say what to do, got: {msg}"

        # a track that simply is not there any more
        short = _mkv(os.path.join(d, "short.mkv"), ["eng"])
        ok, msg = jobs.preflight(short, cfg)
        assert not ok and "ya no existe" in msg, msg

        ok, msg = jobs.preflight(os.path.join(d, "gone.mkv"), cfg)
        assert not ok and "no existe" in msg, msg

        # an external .srt is added by path, not by source id: never a mismatch
        ok, _ = jobs.preflight(original, cfg + [
            {"mkv_id": 99, "type": "subtitle", "codec": "SubRip/SRT",
             "lang": "spa", "ext_path": "/tmp/x.srt"}])
        assert ok, "external subtitle must not be checked against source ids"

    print("test_preflight OK")


if __name__ == "__main__":
    main()
