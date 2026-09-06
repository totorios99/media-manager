"""Empty out_lang must not reach mkvmerge/mkvpropedit.

Both reject '' outright -- "not a valid IETF BCP 47 language tag" -- and the
whole job dies before reading a byte. 1498 kept tracks in this library have no
out_lang, so this is the common case, not the edge one.
"""
import os
os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("TMDB_API_KEY", "x")
import commands


def t(tid, type_, **kw):
    d = {"mkv_id": tid, "type": type_, "out_lang": None, "out_default": 0,
         "out_forced": 0, "out_name": "", "sdh_flag": 0, "commentary_flag": 0,
         "keep": 1, "out_order": tid, "ext_path": None}
    d.update(kw)
    return d


def main():
    tracks = [t(0, "video"), t(1, "audio"), t(2, "subtitle")]
    argv = commands.build_mkvmerge_remux(tracks, "T", "/in.mkv", "/out.mkv")
    for i, a in enumerate(argv):
        if a == "--language":
            assert not argv[i + 1].endswith(":"), f"empty language in {argv[i + 1]!r}"
            assert "und" in argv[i + 1] or argv[i + 1].split(":")[1]
    pe = commands.build_mkvpropedit_chain("/out.mkv", "T", [tracks[1]], [tracks[2]])
    for i, a in enumerate(pe):
        if a.startswith("language="):
            assert a != "language=", "empty language reached mkvpropedit"
    print("ok")


if __name__ == "__main__":
    main()
