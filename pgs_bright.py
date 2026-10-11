"""Brighten a PGS (.sup) stream's palettes so dim text OCRs.

    pgs_bright.py in.sup out.sup

When Harry Met Sally's Spanish track draws its text at luma 122 of 255 with a black outline; pgsrip/tesseract read
nothing from it (0 cues, even at confidence 0), while the same film's English track came out at 3.2 % OOV.
A .sup is a series of segments: "PG", pts(4), dts(4), type(1), size(2), payload. Palette segments (type 0x14) hold
entries of id, Y, Cr, Cb, alpha. Stretching Y of the opaque entries to full range turns grey text white; the
glyph bitmaps (type 0x15) are untouched, so timing and shapes stay exactly as on the disc.
"""
import struct, sys


def brighten(data):
    out, i, n = bytearray(data), 0, len(data)
    while i + 13 <= n:
        if data[i:i + 2] != b"PG":
            raise ValueError(f"not a PGS segment at byte {i}")
        seg_type, size = data[i + 10], struct.unpack(">H", data[i + 11:i + 13])[0]
        payload = i + 13
        if seg_type == 0x14:                                   # palette: id, version, then 5-byte entries
            ys = [data[j + 1] for j in range(payload + 2, payload + size, 5) if data[j + 4] > 32]
            top = max(ys) if ys else 0
            if 0 < top < 200:
                for j in range(payload + 2, payload + size, 5):
                    if data[j + 4] > 32:                       # only visible entries; the outline stays dark
                        out[j + 1] = min(235, 16 + (data[j + 1] - 16) * (235 - 16) // max(top - 16, 1))
        i = payload + size
    return bytes(out)


if __name__ == "__main__":
    open(sys.argv[2], "wb").write(brighten(open(sys.argv[1], "rb").read()))
