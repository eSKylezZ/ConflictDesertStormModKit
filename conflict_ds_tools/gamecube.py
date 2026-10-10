"""Conflict: Desert Storm - Nintendo GameCube disc reading (for converting its assets for the PC game).

Disc images: a plain GameCube disc image (.iso / .gcm, 1.4 GB) or a CISO (.ciso, "CISO" + block size + block map,
as made by NKit / Dolphin tools). The game is GCFP75 (Europe) / the US release; files live in /cache:
catalog.dat, chardata.dat, frontend.dat, <level>.dat, <level>.sch, STREAM.SHD / STREAM.SWD.

Sound caches (<level>.sch) are the PC format written big-endian with byte-swapped chunk tags and 32-byte aligned
chunks: "\\0HCS", u32 size, padding to 0x20, then chunks {tag reversed ("TSFP", "KNAB", "MSFP"), u32 BE size, data},
each chunk start rounded up to 32 bytes. A sample (PFSM) has a 0x60-byte header: +0x18 i32 language (-1 = all),
+0x38 u32 sample id (the same ids as the PC), +0x40 u16 pitch = rate * 4096 / 48000, +0x44 u32 data size; then
big-endian 16-bit PCM. The PC game has the same ids, but as 4-bit ADPCM, often at a lower rate (5-11 kHz where the
GameCube has 14-44.1 kHz).

Pure Python, no dependencies.
"""
import struct


class Disc:
    """Read files from a GameCube disc image (plain or CISO). Close it (or use ``with Disc(path) as disc:``)."""

    def __init__(self, path):
        self.f = open(path, "rb")
        try:
            self._open()
        except BaseException:
            self.f.close()
            raise

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _open(self):
        head = self.f.read(0x8000)
        self.ciso = head[:4] == b"CISO"
        if self.ciso:
            self.block = struct.unpack_from("<I", head, 4)[0]
            self.map, n = [], 0
            for b in head[8:]:
                self.map.append(n if b else -1)
                n += 1 if b else 0
        boot = self.read(0, 0x440)
        self.game_id = boot[:6].decode("latin-1")
        self.title = boot[0x20:0x60].split(b"\0")[0].decode("latin-1")
        if boot[0x1C:0x20] != b"\xc2\x33\x9f\x3d":
            raise ValueError("not a GameCube disc image")
        fst_off, fst_size = struct.unpack_from(">II", boot, 0x424)
        fst = self.read(fst_off, fst_size)
        count = struct.unpack_from(">I", fst, 8)[0]
        names = count * 12
        self.files = {}

        def name_at(o):
            end = fst.index(b"\0", names + o)
            return fst[names + o:end].decode("latin-1")

        def walk(i, end, path):
            while i < end:
                kind = fst[i * 12]
                name = path + "/" + name_at(int.from_bytes(fst[i * 12 + 1:i * 12 + 4], "big"))
                a, b = struct.unpack_from(">II", fst, i * 12 + 4)
                if kind:
                    walk(i + 1, b, name)
                    i = b
                else:
                    self.files[name.lower()] = (name, a, b)
                    i += 1
        walk(1, count, "")

    def read(self, offset, size):
        if not self.ciso:
            self.f.seek(offset)
            return self.f.read(size)
        out = bytearray()
        while size > 0:
            blk, o = divmod(offset, self.block)
            take = min(size, self.block - o)
            if blk >= len(self.map) or self.map[blk] < 0:
                out += bytes(take)
            else:
                self.f.seek(0x8000 + self.map[blk] * self.block + o)
                out += self.f.read(take)
            offset += take
            size -= take
        return bytes(out)

    def names(self):
        return sorted(v[0] for v in self.files.values())

    def file(self, name):
        name, offset, size = self.files[name.lower()]
        return self.read(offset, size)


def sound_cache_samples(data):
    """The samples of a GameCube <level>.sch: list of (language, id, rate, pcm int16 list)."""
    if data[:4] != b"\0HCS":
        raise ValueError("not a GameCube sound cache")
    out, o = [], 0x20
    while o + 8 <= len(data):
        tag = data[o:o + 4][::-1]
        size = struct.unpack_from(">I", data, o + 4)[0]
        if tag == b"PFSM" and size >= 0x60:
            h = o + 8
            lang = struct.unpack_from(">i", data, h + 0x18)[0]
            sid = struct.unpack_from(">I", data, h + 0x38)[0]
            pitch = struct.unpack_from(">H", data, h + 0x40)[0]
            n = struct.unpack_from(">I", data, h + 0x44)[0]
            n = min(n, size - 0x60) // 2
            pcm = list(struct.unpack_from(">%dh" % n, data, h + 0x60))
            out.append((lang, sid, round(pitch * 48000 / 4096), pcm))
        o = (o + 8 + size + 31) & ~31
    return out
