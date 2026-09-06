"""propedit edits the source in place, so its two failure modes are unrecoverable.

1. build_mkvpropedit_chain addresses track:a{N} by position in the FILE, while
   the caller passes tracks sorted by out_order. mkvmerge reorders as it writes
   so the two agree for a remux; mkvpropedit moves nothing, so a reordering
   config stamps each track's metadata onto whichever track sits at that
   position -- and verify_output compares expected-by-out_order against
   got-by-mkv_id, so the swap verifies clean.
2. _build_job_cmd returns the source path as out_path, which _enqueue stores in
   output_file. Both _cancel_job and _reap_stale_jobs remove output_file to
   clear a partial output; for propedit that is the only copy of the film.
"""
import os
import re

os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("TMDB_API_KEY", "x")

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()


def main():
    # 1. the enqueue guard exists and fires before the command is built
    assert "this title reorders tracks; use kind=remux" in SRC, \
        "no guard against a propedit whose config reorders tracks"

    # 2. neither delete path may run for propedit
    reaper = re.search(r'if job and job\["status"\] == "failed" and job\["kind"\][^\n]*', SRC)
    assert reaper and "propedit" in reaper.group(0), \
        "the stale-job reaper would remove a propedit's source file"

    cancel = SRC[SRC.index("def _cancel_job"):]
    cancel = cancel[:cancel.index("os.remove(owner[\"output_file\"])")]
    assert 'job["kind"] != "propedit"' in cancel, \
        "cancelling a propedit would remove its source file"

    import commands
    # 3. a language with two kept audio tracks must not name both the same
    a = {"out_lang": "eng", "out_name": "", "codec": "AC-3", "type": "audio"}
    b = {"out_lang": "eng", "out_name": "", "codec": "TrueHD Atmos", "type": "audio"}
    n1, n2 = commands._canonical_name(a, [a, b]), commands._canonical_name(b, [a, b])
    assert n1 != n2, f"both English audio tracks named {n1!r}"
    # a lone track keeps the plain label
    assert commands._canonical_name(a, [a]) == "English"
    print("ok")


if __name__ == "__main__":
    main()
