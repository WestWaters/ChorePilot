"""A small QR code encoder (ISO/IEC 18004), stdlib only: byte mode, error correction L or M,
versions 1-10 (up to 213 bytes at M). Enough for an otpauth:// URI (TOTP enrolment in an
authenticator app) or a link. Renders to the terminal (half blocks), SVG and PNG.

    m = encode("otpauth://totp/ChorePilot:Alex?secret=...&issuer=ChorePilot")
    print(terminal(m))

The layout follows the standard's construction (finder / timing / alignment patterns,
format + version information, Reed-Solomon over GF(256) with 0x11D, block interleaving,
zig-zag placement, the eight masks scored with the four penalty rules).
"""
from __future__ import annotations

import struct
import zlib

# (ec codewords per block, [(number of blocks, data codewords per block), ...]) per version
EC_TABLE = {
    "L": {1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]),
          5: (26, [(1, 108)]), 6: (18, [(2, 68)]), 7: (20, [(2, 78)]), 8: (24, [(2, 97)]),
          9: (30, [(2, 116)]), 10: (18, [(2, 68), (2, 69)])},
    "M": {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
          5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]),
          8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])},
}
FORMAT_BITS = {"L": 1, "M": 0}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
         8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

# GF(256) tables, primitive polynomial x^8 + x^4 + x^3 + x^2 + 1
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n):
    g = [1]
    for i in range(n):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _gmul(c, _EXP[i])
        g = nxt
    return g


def rs_remainder(data, n):
    g = _rs_generator(n)
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _gmul(g[j + 1], f)
    return rem


def _capacity(version, ecl):
    ec, groups = EC_TABLE[ecl][version]
    return sum(n * d for n, d in groups)


def _data_codewords(payload, version, ecl):
    cap = _capacity(version, ecl)
    bits = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4)
    put(len(payload), 8 if version < 10 else 16)
    for b in payload:
        put(b, 8)
    if len(bits) > cap * 8:
        return None
    put(0, min(4, cap * 8 - len(bits)))
    put(0, (-len(bits)) % 8)
    out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(out) < cap:
        out.append(pad)
        pad ^= 0xEC ^ 0x11
    return out


def _interleave(data, version, ecl):
    ec, groups = EC_TABLE[ecl][version]
    blocks, pos = [], 0
    for n, d in groups:
        for _ in range(n):
            blk = data[pos:pos + d]
            pos += d
            blocks.append((blk, rs_remainder(blk, ec)))
    out = []
    for i in range(max(len(b[0]) for b in blocks)):
        out += [b[0][i] for b in blocks if i < len(b[0])]
    for i in range(ec):
        out += [b[1][i] for b in blocks]
    return out


class _Grid:
    def __init__(self, version):
        self.v = version
        self.n = 17 + 4 * version
        self.m = [[False] * self.n for _ in range(self.n)]
        self.fn = [[False] * self.n for _ in range(self.n)]

    def set(self, x, y, dark, function=True):
        self.m[y][x] = bool(dark)
        if function:
            self.fn[y][x] = True

    def functions(self):
        n = self.n
        for i in range(n):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = ALIGN[self.v]
        last = len(pos) - 1
        for i, ay in enumerate(pos):
            for j, ax in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits("M", 0)        # reserve the areas; redrawn for the chosen mask
        if self.v >= 7:
            rem = self.v
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.v << 12 | rem
            for i in range(18):
                b = (bits >> i) & 1
                a, c = n - 11 + i % 3, i // 3
                self.set(a, c, b)
                self.set(c, a, b)

    def format_bits(self, ecl, mask):
        n = self.n
        data = FORMAT_BITS[ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, n - 15 + i, bit(i))
        self.set(8, n - 8, 1)            # the dark module

    def codewords(self, cws):
        n, i, total = self.n, 0, len(cws) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    up = ((right + 1) & 2) == 0
                    y = n - 1 - vert if up else vert
                    if not self.fn[y][x]:
                        if i < total:
                            self.m[y][x] = bool((cws[i >> 3] >> (7 - (i & 7))) & 1)
                            i += 1
                        else:
                            self.m[y][x] = False
            right -= 2

    def apply_mask(self, k):
        f = MASKS[k]
        for y in range(self.n):
            for x in range(self.n):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]


MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]


def penalty(m):
    n, score = len(m), 0
    lines = [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    pat = [True, False, True, True, True, False, True]
    for line in lines:
        run, prev = 0, None
        for c in line:
            if c == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, c
        if run >= 5:
            score += run - 2
        padded = [False] * 4 + list(line) + [False] * 4
        for i in range(len(padded) - 10):
            w = padded[i:i + 11]
            if (w[:7] == pat and not any(w[7:])) or (w[4:] == pat and not any(w[:4])):
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in m)
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def encode(text, ecl="M", mask=None):
    """-> a square matrix of booleans (True = dark), without the quiet zone."""
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    for version in range(1, 11):
        data = _data_codewords(payload, version, ecl)
        if data is not None:
            break
    else:
        raise ValueError(f"too long for a version-10 QR code at level {ecl} "
                         f"({len(payload)} bytes)")
    g = _Grid(version)
    g.functions()
    g.codewords(_interleave(data, version, ecl))
    best = None
    for k in ([mask] if mask is not None else range(8)):
        g.apply_mask(k)
        g.format_bits(ecl, k)
        p = penalty(g.m)
        if best is None or p < best[0]:
            best = (p, k)
        g.apply_mask(k)                  # undo (XOR)
    k = best[1]
    g.apply_mask(k)
    g.format_bits(ecl, k)
    return g.m


def _bordered(m, border):
    n = len(m)
    blank = [False] * (n + 2 * border)
    return ([blank] * border + [[False] * border + list(r) + [False] * border for r in m]
            + [blank] * border)


def terminal(m, border=2, ansi=True):
    """Half-block text: two rows per line. With ansi=True it forces black-on-white, so it scans
    from dark terminals too."""
    b = _bordered(m, border)
    if len(b) % 2:
        b.append([False] * len(b[0]))
    lines = []
    for y in range(0, len(b), 2):
        s = "".join({(True, True): "█", (True, False): "▀", (False, True): "▄",
                     (False, False): " "}[(b[y][x], b[y + 1][x])] for x in range(len(b[0])))
        lines.append(f"\x1b[30;107m{s}\x1b[0m" if ansi else s)
    return "\n".join(lines)


def svg(m, scale=6, border=4):
    b = _bordered(m, border)
    n = len(b)
    path = "".join(f"M{x},{y}h1v1h-1z" for y, row in enumerate(b) for x, c in enumerate(row) if c)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" '
            f'height="{n * scale}" shape-rendering="crispEdges"><rect width="{n}" height="{n}" '
            f'fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def png(m, scale=8, border=4):
    """A greyscale PNG (bytes)."""
    b = _bordered(m, border)
    n = len(b) * scale
    raw = bytearray()
    for row in b:
        line = bytes([0]) + bytes(0 if c else 255 for c in row for _ in range(scale))
        raw += line * scale

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
