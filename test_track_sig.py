"""ponytail: minimal self-check, not a suite -- run with `python3 test_track_sig.py`."""
from scan import track_sig

BASELINE_AUDIO = dict(type="audio", lang="spa", codec="FLAC", name="[Español (Latam)] [Oficial OP] [WB]")
FLOW_VARIANT = dict(type="audio", lang="spa", codec="FLAC", name="[Español (Latam)] [Oficial OP] [FLOW]")
FORCED_SUB = dict(type="subtitle", lang="eng", codec="HDMV PGS", name="[English] [Forced] [Funimation BD]")
MISLABELED = dict(type="audio", lang="jpn", codec="FLAC", name="[Español (Latam)] [Oficial OP] [WB]")

assert track_sig(BASELINE_AUDIO) == track_sig(dict(BASELINE_AUDIO))  # identical track -> same sig
assert track_sig(BASELINE_AUDIO) != track_sig(FLOW_VARIANT)  # different source (name) -> different sig
assert track_sig(BASELINE_AUDIO) != track_sig(MISLABELED)  # mislabeled lang -> different sig (correctly flagged)
assert track_sig(FORCED_SUB) not in (track_sig(BASELINE_AUDIO), track_sig(FLOW_VARIANT))

# order-independence: episode 54's tracks arrive in a different mkv_id order,
# but track_sig ignores mkv_id/out_order entirely
a = dict(BASELINE_AUDIO, mkv_id=3, out_order=1)
b = dict(BASELINE_AUDIO, mkv_id=7, out_order=4)
assert track_sig(a) == track_sig(b)

print("ok")
