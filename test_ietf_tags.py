"""Spanish variants survive a rewrite: es-419 / es-ES are written as BCP 47, not flattened to 'spa'."""
import json, os, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commands  # noqa: E402


def row(t, mkv_id, lang, out_lang, name=""):
    return {"type": t, "mkv_id": mkv_id, "lang": lang, "ext_path": None, "keep": 1, "out_order": 0,
            "out_lang": out_lang, "out_default": 0, "out_forced": 0, "out_name": name}


def test_lang_tag_only_for_spanish_variants():
    assert commands.lang_tag(row("audio", 1, "spa-mx", "spa")) == "es-419"
    assert commands.lang_tag(row("audio", 1, "spa-es", "spa")) == "es-ES"
    assert commands.lang_tag(row("audio", 1, "spa", "spa")) == "spa"          # unknown variant: unchanged
    assert commands.lang_tag(row("audio", 1, "eng", "eng")) == "eng"
    assert commands.ietf_args(row("audio", 1, "eng", "eng")) == []
    print("test_lang_tag_only_for_spanish_variants OK")


def test_builders_carry_the_tag():
    lat, cas = row("audio", 1, "spa-mx", "spa", "Español Latino"), row("audio", 2, "spa-es", "spa", "Castellano")
    remux = commands.build_mkvmerge_remux([row("video", 0, "eng", "eng"), lat, cas], "T (2000)", "/in.mkv", "/out.mkv")
    assert "1:es-419" in remux and "2:es-ES" in remux, remux
    pe = commands.build_mkvpropedit_chain("/x.mkv", "T (2000)", [lat, cas], [])
    assert "language-ietf=es-419" in pe and "language-ietf=es-ES" in pe, pe
    print("test_builders_carry_the_tag OK")


def test_real_files_keep_es_419():
    """The measured bug: propedit language=spa alone turns es-419 into es."""
    d = tempfile.mkdtemp()
    base = os.path.join(d, "b.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:d=1", "-shortest", "-c:a", "aac", "-c:v", "libx264", base], check=True)
    f = os.path.join(d, "t.mkv")
    subprocess.run(["mkvmerge", "-q", "-o", f, "--language", "1:es-419", base], check=True, capture_output=True)
    lat = row("audio", 1, "spa-mx", "spa", "Latino")
    # the chain addresses audio by output position: a1 is the only audio track
    pe = commands.build_mkvpropedit_chain(f, "T", [lat], [])
    subprocess.run(pe, check=True, capture_output=True)
    tr = [t for t in json.loads(subprocess.run(["mkvmerge", "-J", f], capture_output=True, text=True).stdout)["tracks"]
          if t["type"] == "audio"][0]["properties"]
    assert tr["language"] == "spa" and tr["language_ietf"] == "es-419", tr
    shutil.rmtree(d)
    print("test_real_files_keep_es_419 OK")


def test_remux_with_es_419_still_verifies():
    """Regression: writing es-419 made inspect_file report 'spa-mx' and verify_output (expecting 'spa') failed
    the Hobbit's remux at 100%, leaving the film in 'error'."""
    import jobs
    d = tempfile.mkdtemp()
    base = os.path.join(d, "b.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:d=1", "-shortest", "-c:a", "aac", "-c:v", "libx264", base], check=True)
    rows = [row("video", 0, "eng", "eng"), row("audio", 1, "spa-mx", "spa", "Latino")]
    rows[0]["out_default"], rows[1]["out_default"] = 1, 1
    out = os.path.join(d, "o.mkv")
    subprocess.run(commands.build_mkvmerge_remux(rows, "T (2000)", base, out), check=True, capture_output=True)
    ok, msg = jobs.verify_output(out, rows, source_path=base)
    assert ok, msg
    shutil.rmtree(d)
    print("test_remux_with_es_419_still_verifies OK")


if __name__ == "__main__":
    test_lang_tag_only_for_spanish_variants(); test_builders_carry_the_tag(); test_real_files_keep_es_419(); test_remux_with_es_419_still_verifies()
