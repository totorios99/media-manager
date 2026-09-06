"""_quality tiers: bloated / ideal / lean, per resolution class.

The scope-crop case is the one that keeps regressing: a 2.39:1 UHD transfer is
3840x1608, so a height-only rule reads it as 1080p and judges 20 Mbps against
the FHD cap of 15 -- calling a perfectly sized 4K bloated.
"""
import os
import sys

os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("MM_DB_PATH", "/tmp/mm-test-quality.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app  # noqa: E402  -- must follow the env vars above


def q(bitrate, width=0, height=0, video_codec=""):
    return app._quality({"bitrate": bitrate, "width": width, "height": height,
                         "video_codec": video_codec})


def main():
    scope_uhd = dict(width=3840, height=1608)
    assert q(20e6, **scope_uhd)["tier"] == "ideal", "scope 4K judged against the UHD band"
    assert q(20e6, **scope_uhd)["res"] == "uhd"
    assert q(40e6, **scope_uhd)["tier"] == "bloated"
    assert q(9e6, **scope_uhd)["tier"] == "lean"

    # boundaries are inclusive on both ends of the ideal band
    assert q(25e6, **scope_uhd)["tier"] == "ideal", "at the cap is still fine"
    assert q(25.1e6, **scope_uhd)["tier"] == "bloated"
    assert q(15e6, **scope_uhd)["tier"] == "ideal", "at the floor is still fine"
    assert q(14.9e6, **scope_uhd)["tier"] == "lean"

    flat_fhd = dict(width=1920, height=1080)
    assert q(12e6, **flat_fhd)["tier"] == "ideal"
    assert q(20e6, **flat_fhd)["tier"] == "bloated"
    assert q(5e6, **flat_fhd)["tier"] == "lean"

    assert q(3e6, width=1280, height=720)["res"] == "sd", "720p rides the SD band"
    assert q(3e6, width=1280, height=720)["tier"] == "lean"

    assert q(None, **scope_uhd) is None, "no bitrate -> no verdict"
    assert q(20e6)["res"] == "sd", "no dimensions -> SD cap, unchanged"

    # only 'bloated' means encode; 'lean' is a thin file, not a fat one
    assert q(20e6, **scope_uhd)["tier"] == "ideal"
    assert q(40e6, **scope_uhd)["tier"] == "bloated"
    assert q(9e6, **scope_uhd)["tier"] == "lean", "thin is not something to encode"

    # codec-aware floor: a 12 Mbps 4K is thin for h264 but fine for HEVC
    h264_uhd = dict(width=3840, height=2160, video_codec="h264")
    hevc_uhd = dict(width=3840, height=2160, video_codec="hevc")
    assert q(12e6, **h264_uhd)["tier"] == "lean"
    assert q(12e6, **hevc_uhd)["tier"] == "ideal"
    assert q(8e6, **hevc_uhd)["tier"] == "lean", "9 Mbps floor still bites below it"
    assert q(40e6, **hevc_uhd)["tier"] == "bloated", "the cap ignores codec"
    print("ok")


if __name__ == "__main__":
    main()
