"""build_mkvmerge_remux must emit the five subtitle flags and a real name.

Regression: it emitted none of them, so a source's hearing-impaired/original
flag survived a remux uncontrolled, and `--track-name <id>:` with an empty
out_name wiped useful labels ("English (SDH)") to blank.
"""
import commands


def _t(mkv_id, type_, lang, out_name="", forced=0, sdh=0, comm=0, ext=None):
    return dict(mkv_id=mkv_id, type=type_, lang=lang, codec="X", ext_path=ext, keep=1,
                out_order=mkv_id, out_lang=lang, out_default=0, out_forced=forced,
                out_name=out_name, sdh_flag=sdh, commentary_flag=comm)


def test():
    tracks = [
        _t(0, "video", "und"),
        _t(1, "audio", "eng"),
        _t(2, "subtitle", "eng", sdh=1),
        _t(3, "subtitle", "spa", forced=1),
        _t(4, "subtitle", "eng", out_name="Director's cut notes", comm=1),
    ]
    argv = commands.build_mkvmerge_remux(tracks, "T", "/in.mkv", "/out.mkv")
    s = " ".join(argv)

    for flag in ("--hearing-impaired-flag", "--commentary-flag", "--original-flag",
                 "--text-descriptions-flag", "--visual-impaired-flag"):
        assert f"{flag} 2:" in s, f"{flag} missing on the SDH sub"
    assert "--hearing-impaired-flag 2:yes" in s
    assert "--hearing-impaired-flag 3:no" in s
    assert "--commentary-flag 4:yes" in s
    assert "--original-flag 2:no" in s

    # video never gets subtitle-shaped flags
    assert "--hearing-impaired-flag 0:" not in s

    assert "--track-name 2:English (SDH)" in s, "blank out_name must derive a name"
    assert "--track-name 3:Español (forzados)" in s
    assert "--track-name 4:Director's cut notes" in s, "explicit out_name wins"
    print("ok")


if __name__ == "__main__":
    test()
