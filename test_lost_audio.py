"""The webhook must notice when an upgrade arrives with fewer dubs than the copy
it replaced. Tokyo Drift reached the Jellyfin library without its Spanish because
nothing made that comparison."""
import os, sqlite3, subprocess, sys, tempfile

import graft


def _mkv(path, langs):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1"]
    for _ in langs:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    cmd += ["-map", "0:v"] + sum([["-map", f"{i}:a"] for i in range(1, len(langs) + 1)], [])
    cmd += ["-c:v", "libx264", "-c:a", "ac3"]
    for i, l in enumerate(langs):
        cmd += [f"-metadata:s:a:{i}", f"language={l}"]
    subprocess.run(cmd + [path], check=True)
    return path


def main():
    with tempfile.TemporaryDirectory() as d:
        movies, recycle = os.path.join(d, "Movies"), os.path.join(d, "recycle")
        folder = "Some Movie (2010)"
        os.makedirs(os.path.join(movies, folder))
        os.makedirs(os.path.join(recycle, folder))
        graft.RECYCLE = recycle

        new = _mkv(os.path.join(movies, folder, "Some Movie (2010).mkv"), ["eng"])
        old = _mkv(os.path.join(recycle, folder, "Some Movie (2010).mkv"), ["eng", "spa"])

        lost = sorted(graft.audio_langs(old) - graft.audio_langs(new) - {"und"})
        assert lost == ["spa"], f"the dropped dub must be seen, got {lost}"

        # same languages: nothing to report, the import proceeds
        both = _mkv(os.path.join(movies, folder, "both.mkv"), ["eng", "spa"])
        assert sorted(graft.audio_langs(old) - graft.audio_langs(both) - {"und"}) == []

        # a richer replacement is not a loss
        richer = _mkv(os.path.join(movies, folder, "richer.mkv"), ["eng", "spa", "fre"])
        assert sorted(graft.audio_langs(old) - graft.audio_langs(richer) - {"und"}) == []

        # no recycled copy at all: nothing to compare, never a false alarm
        graft.RECYCLE = os.path.join(d, "empty")
        assert graft.recycled_copy(folder) is None

    print("test_lost_audio OK")


if __name__ == "__main__":
    main()
