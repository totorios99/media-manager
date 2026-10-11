"""pgs_bright: dim palettes are stretched, bright ones and non-palette segments are left alone."""
import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pgs_bright as pb  # noqa: E402


def seg(kind, payload):
    return b"PG" + b"\0" * 8 + bytes([kind]) + struct.pack(">H", len(payload)) + payload


def test_dim_palette_is_stretched_and_the_rest_untouched():
    dim = bytes([0, 0]) + bytes([1, 122, 128, 128, 255]) + bytes([2, 16, 128, 128, 255]) + bytes([3, 200, 128, 128, 0])
    bitmap = bytes(range(20))
    out = pb.brighten(seg(0x14, dim) + seg(0x15, bitmap))
    pal = out[13:13 + len(dim)]
    assert pal[2 + 1] == 235, "the brightest visible entry goes to full white"
    assert pal[7 + 1] == 16, "the dark outline stays dark"
    assert pal[12 + 1] == 200, "an invisible entry is not touched"
    assert out[13 + len(dim) + 13:] == bitmap, "bitmaps are untouched"
    bright = bytes([0, 0]) + bytes([1, 235, 128, 128, 255])
    assert pb.brighten(seg(0x14, bright)) == seg(0x14, bright), "already-bright palettes are not changed"
    print("test_dim_palette_is_stretched_and_the_rest_untouched OK")


if __name__ == "__main__":
    test_dim_palette_is_stretched_and_the_rest_untouched()
