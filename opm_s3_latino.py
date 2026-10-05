"""One-Punch Man S03: add the Latino dub from Antonio's downloads to the library episodes (2026-10-05, his request).

    opm_s3_latino.py [01 02 ...]          # all 12 when no episode is named

Video: the library's (H.264 1080p ~8 Mbps; the downloads are the same resolution at 3-6 Mbps). The Latino AAC is
in sync with it (envelope cross-correlation: lag 0.00 s at minute 1 and minute 20 in all 12), so it is muxed as is.
Order and flags follow suggest_tracks for animation: audio jpn, eng (where the library has it), Latino last and
default; subtitles Spanish full, English full, no forced English, none default because Spanish plays.
Every kept stream is checked against its source (f6_series.stream_md5: packets for video/audio, the rendered text
for subtitles; HEVC/AVC header repetition tolerated) and chapters must match before the file is replaced in place.
"""
import os, subprocess, sys

import f6_series as f6
import library_audit as la

SEASON = "/srv/storage/Shows/One-Punch Man (2015) [tvdbid-293088]/Season 3"
DL = os.path.expanduser("~/Downloads")


def plan(info):
    tr = info["tracks"]
    p = lambda t: t["properties"]
    audio = [t for t in tr if t["type"] == "audio"]
    jpn = next(t for t in audio if p(t).get("language") == "jpn")
    eng = next((t for t in audio if p(t).get("language") == "eng"), None)
    subs = [t for t in tr if t["type"] == "subtitles" and not p(t).get("forced_track")]
    spa_s = next(t for t in subs if p(t).get("language") == "spa")
    eng_s = next(t for t in subs if p(t).get("language") == "eng")
    return jpn, eng, spa_s, eng_s


def build(ep):
    lib = os.path.join(SEASON, f"One-Punch Man (2015) - S03E{ep}.mkv")
    mp4 = os.path.join(DL, f"Opman_T03-{ep}_Latino.mp4")
    tmp = os.path.join(SEASON, f".One-Punch Man (2015) - S03E{ep}.latino.mkv")
    info = la.probe(lib)
    jpn, eng, spa_s, eng_s = plan(info)
    m_audio = next(t for t in la.probe(mp4)["tracks"] if t["type"] == "audio")
    a_ids = [jpn["id"]] + ([eng["id"]] if eng else [])
    s_ids = [spa_s["id"], eng_s["id"]]
    cmd = f6.NICE + ["mkvmerge", "-o", tmp, "--title", info["container"]["properties"].get("title") or "",
                     "--video-tracks", "0", "--audio-tracks", ",".join(map(str, a_ids)),
                     "--subtitle-tracks", ",".join(map(str, s_ids))]
    names = {jpn["id"]: ("jpn", "JPN"), spa_s["id"]: ("es", "Español"), eng_s["id"]: ("eng", "English")}
    if eng:
        names[eng["id"]] = ("eng", "English")
    for i in [0] + a_ids + s_ids:
        cmd += ["--default-track-flag", f"{i}:{'yes' if i == 0 else 'no'}"]
        if i:
            lang, name = names[i]
            cmd += ["--language", f"{i}:{lang}", "--track-name", f"{i}:{name}", "--forced-display-flag", f"{i}:no"]
    cmd += [lib, "--no-video", "--no-subtitles", "--audio-tracks", str(m_audio["id"]),
            "--language", f"{m_audio['id']}:es-419", "--track-name", f"{m_audio['id']}:Español (Latino)",
            "--default-track-flag", f"{m_audio['id']}:yes", mp4]
    order = ["0:0"] + [f"0:{i}" for i in a_ids] + [f"1:{m_audio['id']}"] + [f"0:{i}" for i in s_ids]
    cmd += ["--track-order", ",".join(order)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        return f"mkvmerge rc={r.returncode} {r.stdout[-300:]}"
    try:
        new = la.probe(tmp)["tracks"]
        want = ["video"] + ["audio"] * (len(a_ids) + 1) + ["subtitles"] * 2
        if [t["type"] for t in new] != want:
            return f"layout {[t['type'] for t in new]}"
        defaults = [bool(t["properties"].get("default_track")) for t in new]
        if defaults != [True] + [False] * len(a_ids) + [True, False, False]:
            return f"default flags {defaults}"
        lat = new[1 + len(a_ids)]
        if lat["properties"].get("language_ietf") != "es-419":
            return "Latino tag"
        src_ids, out_ids = [0] + a_ids + s_ids, [0] + list(range(1, 1 + len(a_ids))) + [2 + len(a_ids), 3 + len(a_ids)]
        a, b = f6.stream_md5(lib, src_ids), f6.stream_md5(tmp, out_ids)
        if a != b:
            a, b = f6.stream_md5(lib, src_ids, True), f6.stream_md5(tmp, out_ids, True)
        if not a or a != b:
            return "library streams differ"
        if f6.stream_md5(mp4, [m_audio["id"]]) != f6.stream_md5(tmp, [1 + len(a_ids)]):
            return "Latino stream differs"
        if f6.chapters(lib) != f6.chapters(tmp):
            return "chapters differ"
        fd = os.open(tmp, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, lib)
        tmp = None
        return None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


if __name__ == "__main__":
    eps = sys.argv[1:] or [f"{n:02d}" for n in range(1, 13)]
    for ep in eps:
        err = build(ep)
        print(f"S03E{ep}", "ok" if not err else f"FAILED: {err}", flush=True)
