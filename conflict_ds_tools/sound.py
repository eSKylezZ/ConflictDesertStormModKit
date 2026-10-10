"""Conflict: Desert Storm sounds: level sound caches (.sch), sound banks and samples (PSF ADPCM).

A level's sound cache (<level>.sch next to the game exe) is a chunk file: "SCH\\0", u32 size of the rest, then
chunks {tag[4], u32 size, data} read in order by the game (FUN_0055f540):
  BANK  a sound bank: u32 key = ELF hash of "<NAME>.sbk", then its sound slots, which refer to samples by id
  PFSM  a sample: i32 language (-1 = all), u32 sample id, u32 data size, u16 sample rate, u8 bits (4 = ADPCM,
        otherwise 8-bit), u8 pad, data  (PFST: the same, streamed)
  IMUS  music
Weapons name their shot bank in Weaps.txt column 64 (the game adds ".sbk"). Every gun's bank refers to four samples
in this order: trigger click, distant tail, shot variation 1, shot variation 2.

Samples (GAMELIB SoundDX WaveDataSourcePsf.cpp; decoder FUN_00561ab0 / FUN_00561d50): 16-byte blocks = header byte
(high nibble filter, low nibble shift) + 15 bytes = 30 samples, low nibble first. Each nibble is the top 4 bits of an
int16, scaled by 2^-shift, then v = raw - c0*s1 - c1*s2 with (c0, c1) from the game's table at 0x5FE64C - the
PlayStation VAG filters. 8-bit samples: header + 15 bytes, each the top byte of an int16.

Pure Python, no dependencies.
"""
import array
import struct
import sys
import wave

FILTERS = [(0.0, 0.0), (-0.9375, 0.0), (-1.796875, 0.8125), (-1.53125, 0.859375), (-1.90625, 0.9375)]


# ---- hashes ----
def elf_hash(name):
    """FUN_004b9bf0: the case-sensitive ELF / PJW hash the game keys sound banks by."""
    h = 0
    for c in name.encode("latin-1"):
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h = (h & 0x0FFFFFFF) ^ (g >> 24)
    return h


def bank_key(name):
    return elf_hash(name if name.lower().endswith(".sbk") else name + ".sbk")


# ---- sound cache chunks ----
def chunks(data):
    """[(tag, chunk bytes incl. its 8-byte header)] of a .sch file."""
    if data[:4] != b"SCH\0":
        raise ValueError("not a sound cache (.sch)")
    p, out = 8, []
    while p + 8 <= len(data):
        tag = data[p:p + 4]
        size = struct.unpack_from("<I", data, p + 4)[0]
        out.append((tag, data[p:p + 8 + size]))
        p += 8 + size
    return out


def build(chunk_list):
    body = b"".join(chunk_list)
    return b"SCH\0" + struct.pack("<I", len(body)) + body


def sample_id(chunk):
    return struct.unpack_from("<I", chunk, 12)[0]


def samples(chunk_list):
    """{sample id: [chunk, ...]} of the PFSM / PFST chunks."""
    out = {}
    for tag, c in chunk_list:
        if tag in (b"PFSM", b"PFST") and len(c) >= 16:
            out.setdefault(sample_id(c), []).append(c)
    return out


def bank_chunk(chunk_list, name):
    key = bank_key(name)
    for tag, c in chunk_list:
        if tag == b"BANK" and struct.unpack_from("<I", c, 8)[0] == key:
            return c
    return None


def bank_samples(bank, have):
    """Sample ids a bank refers to, in the bank's order (entries aren't 4-byte aligned: every offset is checked)."""
    order = []
    for o in range(12, len(bank) - 3):
        v = struct.unpack_from("<I", bank, o)[0]
        if v in have and v not in order:
            order.append(v)
    return order


def extract_bank(data, name, rename=None):
    """A small sound cache (a "bank pack") with bank `name` and every sample it refers to, optionally renamed."""
    cl = chunks(data)
    b = bank_chunk(cl, name)
    if b is None:
        return None
    b = bytearray(b)
    if rename:
        struct.pack_into("<I", b, 8, bank_key(rename))
    have = samples(cl)
    return build([bytes(b)] + [c for i in bank_samples(b, have) for c in have[i]])


# ---- samples ----
# The squad voice banks (Bradley, Foley, Ramirez = Connors, Jones) all have the same 46 events = lines 1-47 in bank
# order (event 0x000B has two variants, lines 41 and 42). "loud" lines are used in combat, "quiet" ones when the squad
# is calm (fire at will off, nobody shooting, no known enemies). Lines 37 and 47 are never played by the game.
VOICE_LINES = {
    1: "watch_your_fire", 2: "fire_at_will_loud", 3: "everybody_up_loud", 4: "advance_order_quiet",
    5: "grenade_throw_quiet", 6: "fire_at_will_quiet", 7: "explosive_placed_quiet", 8: "destroyed_vehicle",
    9: "advance_order_loud", 10: "death", 11: "spotted_vehicle_loud", 12: "hold_fire_quiet",
    13: "spotted_tank_quiet", 14: "spotted_gun_quiet", 15: "spotted_infantry_quiet", 16: "moving_loud",
    17: "spotted_helicopter_quiet", 18: "im_hit", 19: "cant_get_there_quiet", 20: "hold_fire_loud",
    21: "grenade_throw_loud", 22: "moving_quiet", 23: "explosive_placed_loud", 24: "enemy_killed",
    25: "man_down", 26: "everybody_down_quiet", 27: "everybody_up_quiet", 28: "destroyed_tank",
    29: "spotted_gun_loud", 30: "destroyed_other", 31: "spotted_tank_loud", 32: "spotted_vehicle_quiet",
    33: "everybody_down_loud", 34: "spotted_helicopter_loud", 35: "cant_get_there_loud", 36: "destroyed_helicopter",
    37: "unused", 38: "incoming_grenade", 39: "downed_calling_for_help", 40: "spotted_infantry_loud",
    41: "grunt_a", 42: "grunt_b", 43: "hold_position_quiet", 44: "hold_position_loud", 45: "follow_me_quiet",
    46: "follow_me_loud", 47: "unused",
}


def sample_language(chunk):
    """Language of a PFSM / PFST chunk: -1 all, 0 English (the SAS actors), 1 French, 2 German, 3 Spanish, 4 Italian,
    6 American (the Delta actors)."""
    return struct.unpack_from("<i", chunk, 8)[0]


def sample_info(chunk):
    """(id, rate, bits, data) of a PFSM / PFST chunk."""
    _lang, sid, size, rate, bits = struct.unpack_from("<iIIHB", chunk, 8)
    return sid, rate, bits, chunk[24:24 + size]


def decode(data, bits=4):
    """ADPCM (or 8-bit) sample data -> list of int16."""
    out, s1, s2 = [], 0.0, 0.0
    for b in range(0, len(data) - 15, 16):
        head = data[b]
        c0, c1 = FILTERS[head >> 4] if (head >> 4) < len(FILTERS) else (0.0, 0.0)
        scale = 1.0 / (1 << (head & 15))
        raws = []
        for x in data[b + 1:b + 16]:
            raws += [(x & 0x0F) << 12, (x & 0xF0) << 8] if bits == 4 else [x << 8]
        for r in raws:
            r = r - 65536 if r >= 32768 else r
            v = r * scale - c0 * s1 - c1 * s2
            s2, s1 = s1, v
            out.append(max(-32768, min(32767, int(round(v)))))
    return out


def encode(pcm):
    """int16 samples -> ADPCM data (best filter per block by trial encoding, the largest shift that fits)."""
    out, s1, s2 = bytearray(), 0.0, 0.0
    for i in range(0, len(pcm), 30):
        block = list(pcm[i:i + 30]) + [0] * max(0, 30 - len(pcm[i:i + 30]))
        best = None
        for f, (c0, c1) in enumerate(FILTERS):
            peak, p1, p2 = 0.0, s1, s2
            for x in block:
                peak = max(peak, abs(x + c0 * p1 + c1 * p2))
                p2, p1 = p1, x
            shift = 12
            while shift > 0 and peak * (1 << shift) / 4096.0 > 7.0:
                shift -= 1
            data, d1, d2, err = bytearray(16), s1, s2, 0.0
            data[0] = f << 4 | shift
            for k, x in enumerate(block):
                pred = -c0 * d1 - c1 * d2
                n = max(-8, min(7, int(round((x - pred) * (1 << shift) / 4096.0))))
                v = max(-32768.0, min(32767.0, n * 4096.0 / (1 << shift) + pred))
                err += (v - x) ** 2
                d2, d1 = d1, v
                data[1 + k // 2] |= (n & 15) << (4 if k & 1 else 0)
            if best is None or err < best[0]:
                best = (err, bytes(data), d1, d2)
        out += best[1]
        s1, s2 = best[2], best[3]
    return bytes(out)


def sample_chunk(pcm, rate, sid):
    """A PFSM chunk for int16 samples."""
    data = encode(pcm)
    return b"PFSM" + struct.pack("<IiIIHBB", 16 + len(data), -1, sid, len(data), rate, 4, 0xCC) + data


# ---- WAV ----
def read_wav(path):
    """(rate, mono int16 list) of a 8/16/24/32-bit PCM WAV (channels mixed down)."""
    w = wave.open(path, "rb")
    ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n)
    w.close()
    out = []
    for i in range(n):
        acc = 0
        for c in range(ch):
            o = (i * ch + c) * width
            if width == 1:
                v = (raw[o] - 128) << 8
            elif width == 2:
                v = struct.unpack_from("<h", raw, o)[0]
            elif width == 3:
                v = int.from_bytes(raw[o:o + 3], "little", signed=True) >> 8
            else:
                v = struct.unpack_from("<i", raw, o)[0] >> 16
            acc += v
        out.append(max(-32768, min(32767, acc // ch)))
    return rate, out


def pcm_bytes(pcm):
    """int16 samples -> little-endian bytes (one buffer copy, no per-sample packing)."""
    a = array.array("h", pcm)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def write_wav(path, rate, pcm):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(pcm_bytes(pcm))
    w.close()


# ---- streams (cutscene / objective speech: Data\Sound\<language>\*.psf) ----
STREAM_RATE = 44100


def decode_stream(data):
    """A .psf stream -> (rate, [left int16 ...], [right int16 ...]). 8-byte header "PSF\x80" + u32, then stereo
    ADPCM: 16-byte blocks interleaved left, right, left, right."""
    if data[:4] != b"PSF\x80":
        raise ValueError("not a speech / cutscene stream (music files use another layout)")
    body = data[8:]
    left = b"".join(body[o:o + 16] for o in range(0, len(body) - 31, 32))
    right = b"".join(body[o + 16:o + 32] for o in range(0, len(body) - 31, 32))
    return STREAM_RATE, decode(left), decode(right)


def encode_stream(left, right):
    """Two int16 channels at 44.1 kHz -> .psf stream bytes."""
    n = max(len(left), len(right))
    left = list(left) + [0] * (n - len(left))
    right = list(right) + [0] * (n - len(right))
    a, b = encode(left), encode(right)
    out = bytearray(b"PSF\x80" + struct.pack("<I", 0xEB300000))
    for o in range(0, len(a), 16):
        out += a[o:o + 16] + b[o:o + 16]
    return bytes(out)


def read_wav_stereo(path):
    """(rate, left, right) of a 8/16/24/32-bit PCM WAV; mono is used for both channels."""
    w = wave.open(path, "rb")
    ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n)
    w.close()

    def sample(o):
        if width == 1:
            return (raw[o] - 128) << 8
        if width == 2:
            return struct.unpack_from("<h", raw, o)[0]
        if width == 3:
            return int.from_bytes(raw[o:o + 3], "little", signed=True) >> 8
        return struct.unpack_from("<i", raw, o)[0] >> 16
    left = [sample(i * ch * width) for i in range(n)]
    right = [sample((i * ch + 1) * width) for i in range(n)] if ch > 1 else left
    return rate, left, right


def resample(pcm, rate, to):
    """Linear resampling (enough for speech; use an audio editor for best quality)."""
    if rate == to or not pcm:
        return list(pcm)
    n = int(len(pcm) * to / rate)
    out = []
    for i in range(n):
        x = i * rate / to
        k = int(x)
        f = x - k
        a = pcm[k]
        b = pcm[k + 1] if k + 1 < len(pcm) else a
        out.append(int(a + (b - a) * f))
    return out


def write_wav_stereo(path, rate, left, right):
    w = wave.open(path, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(rate)
    n = min(len(left), len(right))
    frames = array.array("h", bytes(4 * n))
    frames[0::2] = array.array("h", left[:n])
    frames[1::2] = array.array("h", right[:n])
    if sys.byteorder == "big":
        frames.byteswap()
    w.writeframes(frames.tobytes())
    w.close()
