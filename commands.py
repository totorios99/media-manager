"""Pure argv builders for mkvmerge / HandBrakeCLI / mkvpropedit.

Track dict shape (as stored in the `tracks` DB table / passed from app.py):
    type: 'video' | 'audio' | 'subtitle'
    mkv_id: int or None   -- source track id from `mkvmerge -J` (0-based); None = external file
    ext_path: str or None -- absolute path to an external .srt; None = internal track
    keep: 0/1
    out_order: int        -- rank within its type among kept tracks (0-based, per type)
    out_lang: str          -- 3-letter code, required for kept tracks
    out_default: 0/1
    out_forced: 0/1
    out_name: str

Ordering convention: within the muxed output, tracks are grouped video, then
audio (out_order asc), then subtitles (out_order asc, internal and external
tracks share one out_order sequence for mkvmerge -- see HandBrake caveat below).
"""
import os
import shlex

# Arch's handbrake-cli is built without libdovi and silently strips Dolby
# Vision RPU on every encode. Point HANDBRAKE_CLI at a libdovi-enabled build
# (e.g. "flatpak run --filesystem=/media/hdd1/Movies fr.handbrake.HandBrakeCLI")
# to keep DV. May be multiple argv words, hence shlex.split.
HANDBRAKE_CLI = shlex.split(os.environ.get("HANDBRAKE_CLI", "HandBrakeCLI"))


def _kept(tracks, ttype):
    return [t for t in tracks if t["type"] == ttype and t["keep"]]


def build_mkvmerge_remux(tracks, title, in_path, out_path):
    video = sorted(_kept(tracks, "video"), key=lambda t: t["mkv_id"])
    audio = sorted(_kept(tracks, "audio"), key=lambda t: t["out_order"])
    subs = sorted(_kept(tracks, "subtitle"), key=lambda t: t["out_order"])
    subs_internal = [t for t in subs if t["mkv_id"] is not None]
    subs_external = [t for t in subs if t["ext_path"]]

    argv = ["mkvmerge", "-o", out_path, "--title", title]

    if video:
        argv += ["--video-tracks", ",".join(str(t["mkv_id"]) for t in video)]
    else:
        argv += ["--no-video"]

    if audio:
        argv += ["--audio-tracks", ",".join(str(t["mkv_id"]) for t in audio)]
    else:
        argv += ["--no-audio"]

    if subs_internal:
        argv += ["--subtitle-tracks", ",".join(str(t["mkv_id"]) for t in subs_internal)]
    else:
        argv += ["--no-subtitles"]

    # per-track flags apply to the tracks of the NEXT file on the command line
    main_file_tracks = video + audio + subs_internal
    for t in main_file_tracks:
        tid = t["mkv_id"]
        argv += ["--language", f"{tid}:{t['out_lang']}"]
        argv += ["--default-track-flag", f"{tid}:{'yes' if t['out_default'] else 'no'}"]
        if t["type"] != "video":
            argv += ["--forced-display-flag", f"{tid}:{'yes' if t['out_forced'] else 'no'}"]
        argv += ["--track-name", f"{tid}:{t['out_name'] or ''}"]
    argv.append(in_path)

    # external subtitle files: each is its own input file with a single track (id 0)
    ext_file_index = {}
    for i, t in enumerate(subs_external, start=1):
        argv += ["--language", f"0:{t['out_lang']}"]
        argv += ["--default-track-flag", f"0:{'yes' if t['out_default'] else 'no'}"]
        argv += ["--forced-display-flag", f"0:{'yes' if t['out_forced'] else 'no'}"]
        argv += ["--track-name", f"0:{t['out_name'] or ''}"]
        argv.append(t["ext_path"])
        ext_file_index[id(t)] = i

    order_pairs = [f"0:{t['mkv_id']}" for t in main_file_tracks]
    for t in subs_external:
        order_pairs.append(f"{ext_file_index[id(t)]}:0")
    # rebuild order respecting video, then audio, then merged subs by out_order
    ordered = video + audio + subs
    order_pairs = []
    for t in ordered:
        if t["mkv_id"] is not None:
            order_pairs.append(f"0:{t['mkv_id']}")
        else:
            order_pairs.append(f"{ext_file_index[id(t)]}:0")
    argv += ["--track-order", ",".join(order_pairs)]

    return argv


def build_handbrake_encode(tracks, in_path, out_path,
                            video_encoder="x265_10bit", quality=22, preset="slow",
                            sample_start=None):
    """Returns (argv, sub_output_order) where sub_output_order is the list of
    subtitle track dicts in final output order (internal group first, then
    external group -- HandBrakeCLI cannot interleave --subtitle and --srt-file
    tracks, so external subs always land after internal ones regardless of
    out_order relative to internal tracks. Same-group relative order is
    preserved by out_order. This is a HandBrakeCLI limitation, not ours --
    the mkvpropedit pass afterward fixes lang/default/forced but not position.
    """
    all_audio_by_id = sorted([t for t in tracks if t["type"] == "audio"], key=lambda t: t["mkv_id"])
    all_subs_by_id = sorted(
        [t for t in tracks if t["type"] == "subtitle" and t["mkv_id"] is not None],
        key=lambda t: t["mkv_id"],
    )
    hb_audio_idx = {id(t): i + 1 for i, t in enumerate(all_audio_by_id)}
    hb_sub_idx = {id(t): i + 1 for i, t in enumerate(all_subs_by_id)}

    audio = sorted(_kept(tracks, "audio"), key=lambda t: t["out_order"])
    subs = sorted(_kept(tracks, "subtitle"), key=lambda t: t["out_order"])
    subs_internal = [t for t in subs if t["mkv_id"] is not None]
    subs_external = [t for t in subs if t["ext_path"]]

    argv = HANDBRAKE_CLI + [
        "-i", in_path, "-o", out_path, "-f", "mkv",
        "-e", video_encoder, "-q", str(quality),
        "--encoder-preset", preset, "--cfr", "--crop", "0:0:0:0",
        "--hdr-dynamic-metadata", "all",
    ]
    if sample_start is not None:
        # 30s preview clip at full-encode settings, for quality/size judgment
        argv += ["--start-at", f"seconds:{int(sample_start)}", "--stop-at", "seconds:30"]

    if audio:
        argv += ["-a", ",".join(str(hb_audio_idx[id(t)]) for t in audio)]
        argv += ["-E", ",".join("copy" for _ in audio)]
    else:
        argv += ["-a", "none"]

    if subs_internal:
        argv += ["-s", ",".join(str(hb_sub_idx[id(t)]) for t in subs_internal)]
        default_pos = [i + 1 for i, t in enumerate(subs_internal) if t["out_default"]]
        if default_pos:
            argv += [f"--subtitle-default={default_pos[0]}"]
        forced_pos = [i + 1 for i, t in enumerate(subs_internal) if t["out_forced"]]
        if forced_pos:
            argv += [f"--subtitle-forced={','.join(str(p) for p in forced_pos)}"]
    elif not subs_external:
        argv += ["-s", "none"]

    if subs_external:
        argv += ["--srt-file", ",".join(t["ext_path"] for t in subs_external)]
        argv += ["--srt-lang", ",".join(t["out_lang"] for t in subs_external)]
        default_pos = [i + 1 for i, t in enumerate(subs_external) if t["out_default"]]
        if default_pos:
            argv += [f"--srt-default={default_pos[0]}"]
        # no --srt-forced flag in HandBrakeCLI; fixed by mkvpropedit pass below

    sub_output_order = subs_internal + subs_external
    return argv, sub_output_order


def build_mkvpropedit_chain(out_path, title, audio_output_order, sub_output_order, video_lang=None):
    """audio_output_order / sub_output_order: kept tracks in final OUTPUT order
    (1-based position == track:a{N}/track:s{N} target)."""
    argv = ["mkvpropedit", out_path, "--edit", "info", "--set", f"title={title}"]
    if video_lang:
        # HandBrake writes the video track as 'und'; verify compares against the
        # configured lang, so it must be stamped here
        argv += ["--edit", "track:v1", "--set", f"language={video_lang}"]
    for i, t in enumerate(audio_output_order, start=1):
        argv += [
            "--edit", f"track:a{i}",
            "--set", f"language={t['out_lang']}",
            "--set", f"flag-default={1 if t['out_default'] else 0}",
            "--set", f"name={t['out_name'] or ''}",
        ]
    for i, t in enumerate(sub_output_order, start=1):
        argv += [
            "--edit", f"track:s{i}",
            "--set", f"language={t['out_lang']}",
            "--set", f"flag-default={1 if t['out_default'] else 0}",
            "--set", f"flag-forced={1 if t['out_forced'] else 0}",
            "--set", f"name={t['out_name'] or ''}",
        ]
    return argv


if __name__ == "__main__":
    # fixture: mimics a cleaned-down Arrival-like file
    T = [
        {"type": "video", "mkv_id": 0, "ext_path": None, "keep": 1, "out_order": 0,
         "out_lang": "eng", "out_default": 1, "out_forced": 0, "out_name": ""},
        {"type": "audio", "mkv_id": 1, "ext_path": None, "keep": 1, "out_order": 0,
         "out_lang": "eng", "out_default": 1, "out_forced": 0, "out_name": ""},
        {"type": "audio", "mkv_id": 2, "ext_path": None, "keep": 0, "out_order": 1,
         "out_lang": "eng", "out_default": 0, "out_forced": 0, "out_name": ""},
        {"type": "subtitle", "mkv_id": 3, "ext_path": None, "keep": 1, "out_order": 0,
         "out_lang": "eng", "out_default": 1, "out_forced": 1, "out_name": ""},  # forced eng
        {"type": "subtitle", "mkv_id": 5, "ext_path": None, "keep": 1, "out_order": 1,
         "out_lang": "eng", "out_default": 0, "out_forced": 0, "out_name": ""},  # full PGS
        {"type": "subtitle", "mkv_id": None, "ext_path": "/m/Arrival/eng.sdh.srt", "keep": 1,
         "out_order": 2, "out_lang": "eng", "out_default": 0, "out_forced": 0, "out_name": ""},
    ]

    remux = build_mkvmerge_remux(T, "Arrival (2016)", "/m/Arrival/in.mkv", "/m/Arrival/out.mkv")
    assert remux[:4] == ["mkvmerge", "-o", "/m/Arrival/out.mkv", "--title"]
    assert "--video-tracks" in remux and remux[remux.index("--video-tracks") + 1] == "0"
    assert "--audio-tracks" in remux and remux[remux.index("--audio-tracks") + 1] == "1"
    assert "--subtitle-tracks" in remux and remux[remux.index("--subtitle-tracks") + 1] == "3,5"
    assert "--track-order" in remux
    order = remux[remux.index("--track-order") + 1]
    assert order == "0:0,0:1,0:3,0:5,1:0", order

    hb_argv, sub_order = build_handbrake_encode(T, "/m/Arrival/in.mkv", "/m/Arrival/out.mkv")
    assert "-a" in hb_argv and hb_argv[hb_argv.index("-a") + 1] == "1"
    assert "-s" in hb_argv and hb_argv[hb_argv.index("-s") + 1] == "1,2"  # mkv_id 3,5 -> hb pos 1,2
    assert "--subtitle-forced=1" in hb_argv  # mkv_id 3 (forced eng) is subs_internal[0]
    assert "--srt-file" in hb_argv
    assert len(sub_order) == 3

    mkvpe = build_mkvpropedit_chain(
        "/m/Arrival/out.mkv", "Arrival (2016)",
        [t for t in T if t["type"] == "audio" and t["keep"]],
        sub_order,
    )
    assert "track:a1" in mkvpe
    assert "track:s3" in mkvpe  # 3 kept subs -> a1, s1, s2, s3
    assert mkvpe[mkvpe.index("track:s1") + 2] == "language=eng"
    print("commands.py self-check OK")
