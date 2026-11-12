"""A small QR code generator for the control page (network address for phones).
Byte mode, error correction level M, versions 1 to 10 (up to 213 bytes). Standard library only.
Follows ISO/IEC 18004; the structure follows Project Nayuki's reference implementation (MIT)."""

# Error correction level M, per version 1..10: (EC codewords per block, number of blocks).
_ECC_M = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2),
          6: (16, 4), 7: (18, 4), 8: (22, 4), 9: (22, 5), 10: (26, 5)}
_FORMAT_BITS_M = 0  # Level M is 00 in the format information.
MAX_VERSION = 10


def _bit(value, i):
    return (value >> i) & 1 != 0


def _gf_mul(x, y):
    """Multiply in GF(2^8) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _raw_modules(ver):
    """Modules available for data and error correction in this version."""
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _data_capacity(ver):
    ecc_len, blocks = _ECC_M[ver]
    return _raw_modules(ver) // 8 - ecc_len * blocks


def _alignment_positions(ver):
    if ver == 1:
        return []
    size = ver * 4 + 17
    numalign = ver // 7 + 2
    step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
    return list(reversed([size - 7 - i * step for i in range(numalign - 1)] + [6]))


class _Grid:
    def __init__(self, ver):
        self.ver = ver
        self.size = ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x, y, dark):
        self.dark[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self.set_function(6, i, i % 2 == 0)
            self.set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = _alignment_positions(self.ver)
        last = len(positions) - 1
        for i, ax in enumerate(positions):
            for j, ay in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # These overlap the finder patterns.
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format_bits(0)
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                a, b = size - 11 + i % 3, i // 3
                self.set_function(a, b, _bit(bits, i))
                self.set_function(b, a, _bit(bits, i))

    def draw_format_bits(self, mask):
        data = _FORMAT_BITS_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        size = self.size
        for i in range(0, 6):
            self.set_function(8, i, _bit(bits, i))
        self.set_function(8, 7, _bit(bits, 6))
        self.set_function(8, 8, _bit(bits, 7))
        self.set_function(7, 8, _bit(bits, 8))
        for i in range(9, 15):
            self.set_function(14 - i, 8, _bit(bits, i))
        for i in range(0, 8):
            self.set_function(size - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15):
            self.set_function(8, size - 15 + i, _bit(bits, i))
        self.set_function(8, size - 8, True)  # The dark module.

    def draw_codewords(self, codewords):
        i, size, total = 0, self.size, len(codewords) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5  # Skip the vertical timing pattern.
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = size - 1 - vert if upward else vert
                    if not self.function[y][x] and i < total:
                        self.dark[y][x] = _bit(codewords[i >> 3], 7 - (i & 7))
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        conditions = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        invert = conditions[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and invert(x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self):
        """A simplified penalty (runs, 2x2 blocks, balance), used only to choose a good mask."""
        size, dark, score = self.size, self.dark, 0
        for lines in (dark, list(zip(*dark))):
            for line in lines:
                run = 1
                for a, b in zip(line, line[1:]):
                    if a == b:
                        run += 1
                    else:
                        score += run - 2 if run >= 5 else 0
                        run = 1
                score += run - 2 if run >= 5 else 0
        for y in range(size - 1):
            for x in range(size - 1):
                if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                    score += 3
        total = sum(row.count(True) for row in dark)
        score += abs(total * 20 - size * size * 10) // (size * size) * 10
        return score


def _codewords(data, ver):
    count_bits = 8 if ver <= 9 else 16
    bits = [0, 1, 0, 0] + [_bit(len(data), i) for i in reversed(range(count_bits))]
    for byte in data:
        bits += [_bit(byte, i) for i in reversed(range(8))]
    capacity = _data_capacity(ver) * 8
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join("1" if b else "0" for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < _data_capacity(ver):
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    ecc_len, numblocks = _ECC_M[ver]
    raw = _raw_modules(ver) // 8
    numshort = numblocks - raw % numblocks
    shortlen = raw // numblocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(numblocks):
        dat = words[k:k + shortlen - ecc_len + (0 if i < numshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < numshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != shortlen - ecc_len or j >= numshort:
                result.append(block[i])
    return result


def qr_matrix(text):
    """The QR code for `text` as rows of booleans (True = dark), without the quiet zone."""
    data = text.encode("utf-8")
    for ver in range(1, MAX_VERSION + 1):
        header_bits = 4 + (8 if ver <= 9 else 16)
        if header_bits + len(data) * 8 <= _data_capacity(ver) * 8:
            break
    else:
        raise ValueError("The text is too long for a QR code.")
    codewords = _codewords(data, ver)
    best = None
    for mask in range(8):
        grid = _Grid(ver)
        grid.draw_function_patterns()
        grid.draw_codewords(codewords)
        grid.apply_mask(mask)
        grid.draw_format_bits(mask)
        score = grid.penalty()
        if best is None or score < best[0]:
            best = (score, grid)
    return best[1].dark


def qr_svg(text, quiet=4):
    """An SVG image of the QR code: black on white, with a quiet zone, scalable."""
    matrix = qr_matrix(text)
    size = len(matrix) + 2 * quiet
    path = "".join(f"M{x + quiet},{y + quiet}h1v1h-1z"
                   for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')
