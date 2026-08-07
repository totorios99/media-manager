"""_advice resolution class: scope crops must not read as a smaller format.

Regression: 3840x1608 (2.39:1 UHD) was classed FHD by a height-only test, so a
normal 23 Mbps 4K transfer was advised 'encode'.
"""
import os

os.environ.setdefault("MEDIA_ROOT", "/tmp")
os.environ.setdefault("TMDB_API_KEY", "x")

import app  # noqa: E402  -- must follow the env vars above


def test():
    scope_uhd = {"bitrate": 22_911_634, "width": 3840, "height": 1608}
    assert app._advice(scope_uhd) == "keep", "scope 4K judged against the UHD cap"

    flat_uhd = {"bitrate": 22_911_634, "width": 3840, "height": 2160}
    assert app._advice(flat_uhd) == "keep"
    assert app._advice({"bitrate": 60e6, "width": 3840, "height": 1608}) == "encode", \
        "a genuinely bloated 4K still gets flagged"

    scope_fhd = {"bitrate": 20e6, "width": 1920, "height": 800}
    assert app._advice(scope_fhd) == "encode", "scope 1080p judged against the FHD cap"
    assert app._advice({"bitrate": 10e6, "width": 1920, "height": 800}) == "keep"

    assert app._advice({"bitrate": 10e6, "width": 720, "height": 400}) == "encode", "SD cap"
    assert app._advice({"bitrate": None, "width": 3840, "height": 1608}) is None
    assert app._advice({"bitrate": 20e6}) == "encode", "no dimensions -> SD cap, unchanged"
    print("ok")


if __name__ == "__main__":
    test()
