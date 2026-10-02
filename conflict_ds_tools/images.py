"""DDS / TGA decoding and PNG writing without dependencies (numpy is used when present - Blender ships it).

Formats in the game data: DXT1, DXT3, R5G6B5 and A8R8G8B8 (cube maps: first face). DXT5 and the other plain
RGB(A) layouts are decoded too. Images come back as (width, height, bytes RGBA, top row first).
"""
import struct
import zlib

try:
    import numpy as np
except ImportError:          # pure-Python fallback (slow for big batches, fine for a few textures)
    np = None


class ImageError(Exception):
    pass


# ------------------------------------------------------------------------------------------------------ DDS --

def _565(c):
    return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)


def _dxt_colour_block_py(b, o, four_colour):
    c0, c1, bits = struct.unpack_from("<HHI", b, o)
    p0, p1 = _565(c0), _565(c1)
    if c0 > c1 or four_colour:
        pal = [p0 + (255,), p1 + (255,), tuple((2 * a + c) // 3 for a, c in zip(p0, p1)) + (255,),
               tuple((a + 2 * c) // 3 for a, c in zip(p0, p1)) + (255,)]
    else:
        pal = [p0 + (255,), p1 + (255,), tuple((a + c) // 2 for a, c in zip(p0, p1)) + (255,), (0, 0, 0, 0)]
    return [pal[bits >> (2 * i) & 3] for i in range(16)]


def _dxt5_alpha_py(b, o):
    a0, a1 = b[o], b[o + 1]
    bits = int.from_bytes(b[o + 2:o + 8], "little")
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[bits >> (3 * i) & 7] for i in range(16)]


def _decode_dxt_py(data, w, h, kind):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    size = 8 if kind == "DXT1" else 16
    out = bytearray(bw * 4 * bh * 4 * 4)
    stride = bw * 4 * 4
    o = 0
    for by in range(bh):
        for bx in range(bw):
            if kind == "DXT1":
                px = _dxt_colour_block_py(data, o, False)
            else:
                px = _dxt_colour_block_py(data, o + 8, True)
                if kind == "DXT3":
                    ab = int.from_bytes(data[o:o + 8], "little")
                    alpha = [(ab >> (4 * i) & 15) * 17 for i in range(16)]
                else:
                    alpha = _dxt5_alpha_py(data, o)
                px = [p[:3] + (a,) for p, a in zip(px, alpha)]
            for i, p in enumerate(px):
                y, x = by * 4 + i // 4, bx * 4 + i % 4
                q = y * stride + x * 4
                out[q:q + 4] = bytes(p)
            o += size
    return _crop(out, bw * 4, bh * 4, w, h)


def _crop(buf, pw, ph, w, h):
    if pw == w and ph == h:
        return bytes(buf)
    return b"".join(bytes(buf[y * pw * 4:y * pw * 4 + w * 4]) for y in range(h))


def _decode_dxt_np(data, w, h, kind):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    n = bw * bh
    size = 8 if kind == "DXT1" else 16
    blocks = np.frombuffer(data, np.uint8, n * size).reshape(n, size)
    cb = blocks[:, 8:16] if kind != "DXT1" else blocks
    c0 = cb[:, 0].astype(np.uint32) | cb[:, 1].astype(np.uint32) << 8
    c1 = cb[:, 2].astype(np.uint32) | cb[:, 3].astype(np.uint32) << 8
    bits = (cb[:, 4].astype(np.uint32) | cb[:, 5].astype(np.uint32) << 8 | cb[:, 6].astype(np.uint32) << 16
            | cb[:, 7].astype(np.uint32) << 24)

    def rgb(c):
        return np.stack([(c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31], 1)

    p0, p1 = rgb(c0).astype(np.int32), rgb(c1).astype(np.int32)
    four = (c0 > c1) | (kind != "DXT1")
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.zeros((n, 4, 4), np.uint8)
    pal[:, 0, :3], pal[:, 1, :3], pal[:, 2, :3], pal[:, 3, :3] = p0, p1, p2, p3
    pal[:, :3, 3] = 255
    pal[:, 3, 3] = np.where(four, 255, 0)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    px = pal[np.arange(n)[:, None], idx]                      # (n, 16, 4)
    if kind == "DXT3":
        ab = blocks[:, :8]
        a = np.stack([ab & 15, ab >> 4], 2).reshape(n, 16).astype(np.uint16) * 17
        px[:, :, 3] = a
    elif kind == "DXT5":
        a0, a1 = blocks[:, 0].astype(np.int32), blocks[:, 1].astype(np.int32)
        ab = np.zeros(n, np.uint64)
        for i in range(6):
            ab |= blocks[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
        i7 = np.arange(1, 7)
        pal8 = np.where((a0 > a1)[:, None],
                        np.concatenate([a0[:, None], a1[:, None], ((7 - i7) * a0[:, None] + i7 * a1[:, None]) // 7], 1),
                        np.concatenate([a0[:, None], a1[:, None],
                                        ((5 - i7[:4]) * a0[:, None] + i7[:4] * a1[:, None]) // 5,
                                        np.zeros((n, 1), np.int32), np.full((n, 1), 255, np.int32)], 1))
        ai = ((ab[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
        px[:, :, 3] = pal8[np.arange(n)[:, None], ai]
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w].tobytes()


def _decode_masked(data, w, h, bits, masks):
    bpp = bits // 8
    n = w * h
    if np is not None:
        raw = np.frombuffer(data, np.uint8, n * bpp).reshape(n, bpp).astype(np.uint32)
        v = np.zeros(n, np.uint32)
        for i in range(bpp):
            v |= raw[:, i] << (8 * i)
        out = np.zeros((n, 4), np.uint8)
        for ch, m in enumerate(masks):
            if m:
                shift = (m & -m).bit_length() - 1
                mx = m >> shift
                out[:, ch] = ((v & m) >> shift) * 255 // mx
            elif ch == 3:
                out[:, 3] = 255
        return out.tobytes()
    out = bytearray(n * 4)
    for i in range(n):
        v = int.from_bytes(data[i * bpp:i * bpp + bpp], "little")
        for ch, m in enumerate(masks):
            if m:
                shift = (m & -m).bit_length() - 1
                out[i * 4 + ch] = ((v & m) >> shift) * 255 // (m >> shift)
            elif ch == 3:
                out[i * 4 + 3] = 255
    return bytes(out)


def decode_dds(data):
    if data[:4] != b"DDS ":
        raise ImageError("not a DDS file")
    h, w = struct.unpack_from("<II", data, 12)
    pflags = struct.unpack_from("<I", data, 80)[0]
    four = data[84:88]
    bits = struct.unpack_from("<I", data, 88)[0]
    masks = struct.unpack_from("<4I", data, 92)
    body = data[128:]
    if pflags & 4:
        kind = four.decode("latin-1")
        if kind not in ("DXT1", "DXT3", "DXT5", "DXT2", "DXT4"):
            raise ImageError("unsupported DDS format " + kind)
        kind = {"DXT2": "DXT3", "DXT4": "DXT5"}.get(kind, kind)
        rgba = (_decode_dxt_np if np is not None else _decode_dxt_py)(body, w, h, kind)
    else:
        if not pflags & 1:
            masks = masks[:3] + (0,)
        rgba = _decode_masked(body, w, h, bits, masks)
    return w, h, rgba


# ------------------------------------------------------------------------------------------------------ TGA --

def decode_tga(data):
    idlen, cmtype, itype = data[0], data[1], data[2]
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    if cmtype or itype not in (2, 3, 10, 11):
        raise ImageError("unsupported TGA type %d" % itype)
    px = bpp // 8
    o = 18 + idlen
    n = w * h
    if itype in (10, 11):
        raw = bytearray()
        while len(raw) < n * px:
            c = data[o]
            o += 1
            if c & 0x80:
                raw += data[o:o + px] * ((c & 0x7F) + 1)
                o += px
            else:
                raw += data[o:o + px * (c + 1)]
                o += px * (c + 1)
        raw = bytes(raw[:n * px])
    else:
        raw = data[o:o + n * px]
    out = bytearray(n * 4)
    if px == 1:
        out[0::4] = out[1::4] = out[2::4] = raw
        out[3::4] = b"\xff" * n
    elif px == 2:
        for i in range(n):
            v = raw[2 * i] | raw[2 * i + 1] << 8
            out[4 * i:4 * i + 4] = bytes(((v >> 10 & 31) * 255 // 31, (v >> 5 & 31) * 255 // 31,
                                          (v & 31) * 255 // 31, 255))
    else:
        out[0::4], out[1::4], out[2::4] = raw[2::px], raw[1::px], raw[0::px]
        out[3::4] = raw[3::px] if px == 4 else b"\xff" * n
    if not desc & 0x20:        # bottom-up
        row = w * 4
        out = b"".join(bytes(out[y * row:(y + 1) * row]) for y in range(h - 1, -1, -1))
    return w, h, bytes(out)


# ------------------------------------------------------------------------------------------------------ PNG --

def encode_png(w, h, rgba, alpha=True):
    ch = 4 if alpha else 3
    row = w * 4
    if alpha:
        body = b"".join(b"\0" + rgba[y * row:(y + 1) * row] for y in range(h))
    else:
        rgb = bytearray(w * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = rgba[0::4], rgba[1::4], rgba[2::4]
        body = b"".join(b"\0" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(body, 6)) + chunk(b"IEND", b""))


def _to565(c):
    return (min(31, (c[0] * 31 + 127) // 255) << 11) | (min(63, (c[1] * 63 + 127) // 255) << 5) | min(31, (c[2] * 31 + 127) // 255)


def _dxt1_block(px):
    """8-byte DXT1 colour block for 16 (r, g, b) pixels: endpoints = the extremes along the block's colour range."""
    lo = [min(p[i] for p in px) for i in range(3)]
    hi = [max(p[i] for p in px) for i in range(3)]
    axis = [hi[i] - lo[i] for i in range(3)]
    proj = [sum(p[i] * axis[i] for i in range(3)) for p in px]
    e0, e1 = px[proj.index(max(proj))], px[proj.index(min(proj))]
    c0, c1 = _to565(e0), _to565(e1)
    if c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)
    if c0 < c1:
        c0, c1 = c1, c0

    def rgb(c):
        return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)
    a, b = rgb(c0), rgb(c1)
    pal = [a, b, tuple((2 * a[i] + b[i]) // 3 for i in range(3)), tuple((a[i] + 2 * b[i]) // 3 for i in range(3))]
    bits = 0
    for k, p in enumerate(px):
        best = min(range(4), key=lambda j: sum((p[i] - pal[j][i]) ** 2 for i in range(3)))
        bits |= best << (2 * k)
    return struct.pack("<HHI", c0, c1, bits)


def _dxt5_alpha_block(alphas):
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        return bytes([a0, a1]) + b"\0" * 6
    pal = [a0, a1] + [((7 - j) * a0 + j * a1) // 7 for j in range(1, 7)]
    bits = 0
    for k, a in enumerate(alphas):
        bits |= min(range(8), key=lambda j: abs(a - pal[j])) << (3 * k)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def _dxt3_alpha_block(alphas):
    """8-byte explicit alpha (4 bits per pixel) of a DXT3 block."""
    v = 0
    for k, a in enumerate(alphas):
        v |= ((a * 15 + 127) // 255) << (4 * k)
    return v.to_bytes(8, "little")


def encode_dds(w, h, rgba, alpha=None, fmt=None):
    """A DXT1 (or DXT5 when the image has transparency) .dds without mip maps - the layout of the game's own
    textures. rgba = bytes, top row first; the game wants powers of two (256 x 256, 512 x 512 ...).
    fmt = "DXT1" / "DXT3" / "DXT5" forces a format (soldier-panel portraits are DXT3)."""
    if fmt is None:
        if alpha is None:
            alpha = has_alpha(rgba)
        fmt = "DXT5" if alpha else "DXT1"
    fmt = fmt.upper()
    alpha = fmt != "DXT1"
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            px, al = [], []
            for y in range(by, by + 4):
                for x in range(bx, bx + 4):
                    o = (min(y, h - 1) * w + min(x, w - 1)) * 4
                    px.append((rgba[o], rgba[o + 1], rgba[o + 2]))
                    al.append(rgba[o + 3])
            if fmt == "DXT5":
                out += _dxt5_alpha_block(al)
            elif fmt == "DXT3":
                out += _dxt3_alpha_block(al)
            out += _dxt1_block(px)
    fourcc = fmt.encode()
    header = b"DDS " + struct.pack("<7I", 124, 0x81007, h, w, len(out), 0, 0) + b"\0" * 44
    header += struct.pack("<2I", 32, 4) + fourcc + b"\0" * 20
    header += struct.pack("<4I", 0x1000, 0, 0, 0) + b"\0" * 4
    return header + bytes(out)


def has_alpha(rgba):
    return rgba[3::4].count(255) != len(rgba) // 4


def flip_green(rgba):
    b = bytearray(rgba)
    b[1::4] = bytes(255 - x for x in b[1::4]) if np is None else \
        (255 - np.frombuffer(bytes(b[1::4]), np.uint8)).tobytes()
    return bytes(b)


def decode_png(data):
    """8-bit, non-interlaced PNG (grey, RGB, palette, grey + alpha, RGBA) -> (w, h, RGBA bytes)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageError("not a PNG file")
    o, idat, pal, trns = 8, b"", None, None
    w = h = ctype = 0
    while o + 8 <= len(data):
        n, t = struct.unpack_from(">I4s", data, o)
        body = data[o + 8:o + 8 + n]
        if t == b"IHDR":
            w, h, bits, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if bits != 8 or interlace:
                raise ImageError("only 8-bit, non-interlaced PNGs are supported")
        elif t == b"PLTE":
            pal = body
        elif t == b"tRNS":
            trns = body
        elif t == b"IDAT":
            idat += body
        o += 12 + n
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if bpp is None:
        raise ImageError("unsupported PNG colour type %d" % ctype)
    raw, stride = zlib.decompress(idat), w * bpp
    rows, prev, p = [], bytearray(stride), 0
    for _ in range(h):
        f, line = raw[p], bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    out = bytearray(w * h * 4)
    for y, line in enumerate(rows):
        for x in range(w):
            q, s = (y * w + x) * 4, x * bpp
            if ctype == 6:
                out[q:q + 4] = line[s:s + 4]
            elif ctype == 2:
                out[q:q + 4] = line[s:s + 3] + b"\xff"
            elif ctype == 0:
                out[q:q + 4] = bytes((line[s],) * 3) + b"\xff"
            elif ctype == 4:
                out[q:q + 4] = bytes((line[s],) * 3) + bytes((line[s + 1],))
            else:
                i = line[s]
                out[q:q + 3] = pal[i * 3:i * 3 + 3]
                out[q + 3] = trns[i] if trns and i < len(trns) else 255
    return w, h, bytes(out)


def decode_file(path):
    with open(path, "rb") as f:
        data = f.read()
    low = path.lower()
    if low.endswith(".dds") or data[:4] == b"DDS ":
        return decode_dds(data)
    if low.endswith(".png") or data[:4] == b"\x89PNG":
        return decode_png(data)
    if low.endswith(".tga"):
        return decode_tga(data)
    raise ImageError("unsupported image " + path)


def to_png(path, flip_g=False):
    """PNG bytes for a DDS/TGA/PNG file (PNG files are returned as they are unless flip_g)."""
    if path.lower().endswith(".png") and not flip_g:
        with open(path, "rb") as f:
            return f.read()
    if path.lower().endswith(".png"):
        raise ImageError("PNG re-encoding needs the source DDS/TGA")
    w, h, rgba = decode_file(path)
    if flip_g:
        rgba = flip_green(rgba)
    return encode_png(w, h, rgba, has_alpha(rgba))
