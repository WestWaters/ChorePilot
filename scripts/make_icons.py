#!/usr/bin/env python3
"""Draw ChorePilot's app icons with the standard library only (no Pillow, no Inkscape).

    python3 scripts/make_icons.py          # writes chorepilot/webui/icon*.png + icon.svg

The mark is a house with a check in it, near-black on the dashboard's one accent colour. Shapes are
rendered from signed distances with one-pixel anti-aliasing, so the PNGs are crisp at every
size. Run it again after changing the colour or the glyph; the outputs are committed.
"""
from __future__ import annotations

import math
import os
import struct
import zlib

ACCENT = (0xC8, 0xF0, 0x31)        # signal lime
GLYPH = (0x0A, 0x0A, 0x0A)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chorepilot", "webui")
# the glyph, in a 24 x 24 box (same as the "mark" icon in app.js)
HOUSE = [(4.5, 10.5), (12, 4.5), (19.5, 10.5), (19.5, 19.5), (4.5, 19.5), (4.5, 10.5)]
CHECK = [(8.8, 13.6), (11.1, 15.9), (15.4, 11.5)]
STROKE = 2.0


def _seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rrect(px, py, size, r):
    """Signed distance to a rounded square [0, size]^2 with corner radius r."""
    c = size / 2
    qx, qy = abs(px - c) - (c - r), abs(py - c) - (c - r)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def render(size, radius_frac=0.12, glyph_frac=0.64, full_bleed=False):
    scale = size * glyph_frac / 24.0
    off = (size - 24 * scale) / 2
    segs = [(a, b) for line in (HOUSE, CHECK) for a, b in zip(line, line[1:])]
    segs = [((ax * scale + off, ay * scale + off), (bx * scale + off, by * scale + off))
            for (ax, ay), (bx, by) in segs]
    half = STROKE * scale / 2
    r = size * radius_frac
    rows = []
    for y in range(size):
        row = bytearray([0])
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            bg = 1.0 if full_bleed else max(0.0, min(1.0, 0.5 - _rrect(px, py, size, r)))
            d = min(_seg(px, py, a[0], a[1], b[0], b[1]) for a, b in segs) - half
            fg = max(0.0, min(1.0, 0.5 - d))
            col = [round(ACCENT[i] * (1 - fg) + GLYPH[i] * fg) for i in range(3)]
            row += bytes(col + [round(255 * bg)])
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def svg():
    pts = lambda line: " ".join(f"{x:g},{y:g}" for x, y in line)  # noqa: E731
    hexcol = "#%02x%02x%02x" % ACCENT
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<rect width="24" height="24" rx="2.9" fill="{hexcol}"/>'
            f'<g transform="translate(4.32 4.32) scale(.64)" fill="none" stroke="#0a0a0a" '
            f'stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">'
            f'<polyline points="{pts(HOUSE)}"/><polyline points="{pts(CHECK)}"/></g></svg>\n')


def main():
    os.makedirs(OUT, exist_ok=True)
    files = {
        "icon-192.png": render(192),
        "icon-512.png": render(512),
        "icon-maskable-512.png": render(512, glyph_frac=0.5, full_bleed=True),
        "apple-touch-icon.png": render(180, full_bleed=True, glyph_frac=0.6),
    }
    for name, data in files.items():
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        print(f"wrote {name} ({len(data)} bytes)")
    with open(os.path.join(OUT, "icon.svg"), "w") as f:
        f.write(svg())
    print("wrote icon.svg")


if __name__ == "__main__":
    main()
