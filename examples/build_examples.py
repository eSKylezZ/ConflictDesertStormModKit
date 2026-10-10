"""Build the example mods from your own copy of the game.

The .skin / .weapon / table files are in examples/mods; everything made from game data (textures, models,
animations, sound banks) is built here, so this repository ships no game files.

    python examples/build_examples.py                          # -> examples/build/Mods
    python examples/build_examples.py --blender "C:\\...\\blender.exe"   # also the models and animations
    python examples/build_examples.py --blender ... --install  # and copy them into <game>\\Mods

Needs Python 3.8+. The Blender part (models, animations) needs Blender 3.6 or newer; without --blender those
files are skipped and the mods that need them are left out.
"""
import argparse
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from conflict_ds_tools import archive, evo, images, sound  # noqa: E402

# mods whose files come (partly) from Blender
BLENDER_MODS = ("SilencedSAW", "SpeedReload", "LoudMP5", "BigHeadUniform", "_BigHead", "_HeadTilt", "_BouncyRun", "_HazardDrums")


class Game:
    """Single files straight out of the game's .dat archives, by name."""

    def __init__(self, folder):
        self.dir = folder
        self.index = {}
        for path in archive.list_archives(folder):
            with open(path, "rb") as f:
                head = f.read(0x8000)
            first = struct.unpack_from("<I", head, 4)[0]
            for p in range(0, min(first, len(head)), 12):
                h, o, s = struct.unpack_from("<III", head, p)
                if h == o == s == 0:
                    break
                self.index.setdefault(h, (path, o, s))  # the first archive that has it

    def get(self, name):
        hit = self.index.get(evo.archive_hash(name))
        if hit is None:
            raise SystemExit("%s is not in the game's archives (%s)" % (name, self.dir))
        path, o, s = hit
        with open(path, "rb") as f:
            f.seek(o)
            return f.read(s)

    def texture(self, name):
        w, h, rgba = images.decode_dds(self.get(name + ".DDS"))
        return w, h, bytearray(rgba)

    def save(self, name, folder):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "wb") as f:
            f.write(self.get(name))


# ---------------------------------------------------------------------------------------------- textures --
def lum(r, g, b):
    return 0.3 * r + 0.59 * g + 0.11 * b


def clamp(v):
    return max(0, min(255, int(v)))


def is_skin(r, g, b):
    return r > 150 and r > g > b and r - b > 45


def soldier_keep(chest=False):
    """Pixels of a soldier texture (512 x 512 hero layout) that are face, hair or bare skin - left as they are.
    chest: the bottom-left block is bare skin (Foley) rather than trousers (Bradley)."""
    def keep(x, y, w, h, r, g, b):
        u, v = x * 512 // w, y * 512 // h
        if u < 256 and 128 <= v < 320:        # head block: face, hair, ears
            return True
        if u >= 256 and v >= 384:             # hands / gloves
            return True
        return chest and v >= 384 and is_skin(r, g, b)
    return keep


def recolour(tex, fn, keep=None):
    w, h, px = tex
    out = bytearray(px)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            r, g, b = px[o], px[o + 1], px[o + 2]
            if keep and keep(x, y, w, h, r, g, b):
                continue
            out[o], out[o + 1], out[o + 2] = (clamp(c) for c in fn(r, g, b, x, y))
    return w, h, out


def pink(r, g, b, x, y):
    l = lum(r, g, b)
    return 70 + l * 0.8, 10 + l * 0.35, 45 + l * 0.55


def winter_levels(lums):
    """Winter camouflage from a texture's own brightness: (dark, light) = its 20th / 75th percentile. Above the light
    one is snow white, below the dark one the grey patches - a fixed scale left desert camo a pale blue-grey."""
    s = sorted(lums) or [0, 255]
    return s[len(s) // 5], max(s[len(s) * 3 // 4], s[len(s) // 5] + 1)


def winter_value(l, dark, light):
    t = min(1.0, max(0.0, (l - dark) / (light - dark)))
    v = 118 + 127 * t * t * (3 - 2 * t)        # grey 118 .. white 245, smooth in between
    return v - 3, v, v + 4                      # a slight cool tint, not blue


def winter_for(tex, keep=None):
    """The winter recolour function for this texture (levels measured on the texels that get recoloured)."""
    w, h, px = tex
    lums = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            o = (y * w + x) * 4
            r, g, b = px[o], px[o + 1], px[o + 2]
            if not (keep and keep(x, y, w, h, r, g, b)):
                lums.append(lum(r, g, b))
    dark, light = winter_levels(lums)
    return lambda r, g, b, x, y: winter_value(lum(r, g, b), dark, light)


def gold(r, g, b, x, y):
    l = lum(r, g, b)
    return 50 + l * 1.05, 32 + l * 0.8, l * 0.25


def tiger(r, g, b, x, y):
    l = lum(r, g, b) / 255.0
    wave_ = math.sin(x * 0.55 + 2.2 * math.sin(y * 0.35) + 0.6 * math.sin(x * 0.13 + y * 0.21))
    if wave_ > 0.55:                                  # black stripe
        return 20 * l + 5, 15 * l + 4, 10 * l + 3
    return 90 + 190 * l, 35 + 110 * l, 10 + 25 * l   # orange, shaded like the original


def tint(rgb):
    def fn(r, g, b, x, y):
        l = lum(r, g, b) / 255.0
        return (c * (0.25 + 1.1 * l) for c in rgb)
    return fn


def hazard(r, g, b, x, y):
    """Oil drum: yellow / black diagonal bands on the side (left half of its texture), lids unchanged."""
    l = lum(r, g, b) / 255.0
    if x >= 64:                                       # the lids
        return r, g, b
    if ((x + y) // 16) % 2:
        return 30 * l + 10, 30 * l + 10, 25 * l + 8
    return 120 + 150 * l, 95 + 130 * l, 10 + 20 * l


QUILT = 32     # diamond size in pixels of a 256 x 256 normal map (64 on the 512 x 512 texture)


def quilt(x, y, w):
    """Padded diamonds: height 0 on the stitched seams, 1 in the middle of a diamond, and its slope (d/dx, d/dy),
    for pixel x, y of a w-wide soldier texture (the pattern lines up on the texture and the normal map)."""
    p = QUILT * w / 256.0
    a, b = ((x + y) % p) / p, ((x - y) % p) / p
    sa, ca, sb, cb = math.sin(math.pi * a), math.cos(math.pi * a), math.sin(math.pi * b), math.cos(math.pi * b)
    k = math.pi / p
    return sa * sb, k * (ca * sb + sa * cb), k * (ca * sb - sa * cb)


def navy(r, g, b, x, y):
    """Plain navy cloth: the camo flattened to a little texture, so the quilting's light and shade stand out."""
    f = 0.8 + 0.45 * lum(r, g, b) / 255.0
    return 52 * f, 62 * f, 88 * f


def jacket_only(keep):
    """Quilting goes on the jacket and trousers: also leave out the boots (top-left block of the hero layout)."""
    return lambda x, y, w, h, r, g, b: (x * 512 // w < 256 and y * 512 // h < 128) or keep(x, y, w, h, r, g, b)


def quilted(tex, keep):
    """The uniform texture: the seams darkened, so the stitching shows on the colour too."""
    w, h, px = tex
    out = bytearray(px)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            if keep(x, y, w, h, px[o], px[o + 1], px[o + 2]):
                continue
            f = 0.72 + 0.28 * min(1.0, quilt(x, y, w)[0] * 5)
            out[o], out[o + 1], out[o + 2] = (clamp(c * f) for c in px[o:o + 3])
    return w, h, out


def quilted_normals(tex, keep, strength=1.1):
    """The soldier's own normal map with the padded diamonds added on the cloth (DirectX style: green = down)."""
    w, h, px = tex
    out = bytearray(px)
    for y in range(h):
        for x in range(w):
            if keep(x, y, w, h, 0, 0, 0):
                continue
            o = (y * w + x) * 4
            nx, ny, nz = (c / 127.5 - 1 for c in px[o:o + 3])
            _, dx, dy = quilt(x, y, w)
            s = strength * QUILT / math.pi       # slope 1 at the steepest point of a diamond -> strength
            nx, ny = nx - dx * s, ny - dy * s
            n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            out[o], out[o + 1], out[o + 2] = (clamp(127.5 + 127.5 * c / n) for c in (nx, ny, nz))
    return w, h, out


def dxt3(w, h, rgba):
    """DXT3 .dds (explicit 4-bit alpha) - the format of the game's 64 x 64 portraits."""
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            px, al = [], 0
            for i, (y, x) in enumerate((y, x) for y in range(by, by + 4) for x in range(bx, bx + 4)):
                o = (y * w + x) * 4
                px.append((rgba[o], rgba[o + 1], rgba[o + 2]))
                al |= (rgba[o + 3] >> 4) << (4 * i)
            out += al.to_bytes(8, "little") + images._dxt1_block(px)
    header = b"DDS " + struct.pack("<7I", 124, 0x81007, h, w, len(out), 0, 0) + b"\0" * 44
    header += struct.pack("<2I", 32, 4) + b"DXT3" + b"\0" * 20
    header += struct.pack("<4I", 0x1000, 0, 0, 0) + b"\0" * 4
    return header + bytes(out)


def write_dds(tex, path, fmt="dxt1"):
    w, h, px = tex
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dxt3(w, h, px) if fmt == "dxt3" else images.encode_dds(w, h, bytes(px), alpha=False)
    with open(path, "wb") as f:
        f.write(data)


def write_png(tex, path):
    w, h, px = tex
    with open(path, "wb") as f:
        f.write(images.encode_png(w, h, bytes(px)))


# ------------------------------------------------------------------------------------------------- icons --
# Weapon pictures (HUD weapon panel, inventory, pick-up icon) are images of GWInt.img: pages GWint0-4.png, 272-byte
# entries from offset 284 {u16 page, name[256] (IMAGE_n), pad, u16 x, y, w, h, i16 offset x, y}. A weapon's picture
# number is fixed in the exe (Weaps.txt column 5 "IMAGE_WEAPON_SAW" -> 111).
ICON_IDS = {"SAW": 111, "DESERTEAGLE": 99, "MP5": 107, "COMMANDO": 97}


def game_icon(game, which):
    img = game.get("GWINT.IMG")
    o = 284 + ICON_IDS[which] * 272
    page = struct.unpack_from("<H", img, o)[0]
    x, y, w, h = struct.unpack_from("<4H", img, o + 260)
    pw, ph, px = images.decode_png(game.get("GWINT%d.PNG" % page))
    out = bytearray()
    for row in range(y, y + h):
        out += px[(row * pw + x) * 4:(row * pw + x + w) * 4]
    return w, h, out


def icon_recolour(icon, fn, only_grey=False):
    """Recolours the icon's opaque pixels; only_grey leaves coloured parts (the SAW's brown ammo box) alone."""
    w, h, px = icon
    out = bytearray(px)
    for i in range(0, len(px), 4):
        r, g, b, a = px[i:i + 4]
        if a == 0 or (only_grey and max(r, g, b) - min(r, g, b) > 28):
            continue
        out[i:i + 3] = bytes(clamp(c) for c in fn(r, g, b, (i // 4) % w, (i // 4) // w))
    return w, h, out


def icon_crop(icon, left, right=0, pad_left=0):
    """Columns left..w-right, with pad_left transparent columns in front."""
    w, h, px = icon
    nw = w - left - right + pad_left
    out = bytearray(nw * h * 4)
    for y in range(h):
        src = px[(y * w + left) * 4:(y * w + w - right) * 4]
        out[(y * nw + pad_left) * 4:(y * nw + pad_left) * 4 + len(src)] = src
    return nw, h, out


def icon_tube(icon, x0, x1, y0, y1, dark=(38, 40, 44), light=(120, 124, 130)):
    """A shaded horizontal cylinder (suppressor / barrel) over columns x0..x1-1, rows y0..y1-1."""
    w, h, px = icon
    for y in range(y0, y1):
        t = (y - y0 + 0.5) / (y1 - y0)
        k = max(0.0, 1.0 - abs(t - 0.35) * 2.2)        # highlight a little above the middle
        c = [int(d + (l - d) * k) for d, l in zip(dark, light)]
        edge = y in (y0, y1 - 1)
        for x in range(x0, x1):
            o = (y * w + x) * 4
            px[o:o + 4] = bytes([c[0] // (2 if edge else 1), c[1] // (2 if edge else 1), c[2] // (2 if edge else 1), 255])
    for y in range(y0 + 1, y1 - 1):                     # darker front face
        o = (y * w + x0) * 4
        px[o:o + 4] = bytes([dark[0] // 2, dark[1] // 2, dark[2] // 2, 255])
    return icon


def write_icon(icon, path):
    w, h, px = icon
    with open(path, "wb") as f:
        f.write(images.encode_png(w, h, bytes(px), True))


# ------------------------------------------------------------------------------------------------ sounds --
RATE = 22050


def write_wav(path, samples):
    pcm = struct.pack("<%dh" % len(samples), *(max(-32767, min(32767, int(s * 32767))) for s in samples))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def radio(pcm, rate, seed):
    """A voice line over a field radio: thin band (high- and low-pass), driven into soft clipping, hiss, and a squelch
    click at both ends."""
    rnd = random.Random(seed)
    hp_a = math.exp(-2 * math.pi * 450 / rate)            # one-pole filters
    lp_a = math.exp(-2 * math.pi * 1900 / rate)
    out, hp, prev, lp = [], 0.0, 0.0, 0.0
    squelch = int(rate * 0.06)
    for i in range(squelch):                               # key-up burst
        out.append(0.5 * rnd.uniform(-1, 1) * (1 - i / squelch))
    for v in pcm:
        x = v / 32768.0
        hp = hp_a * (hp + x - prev)
        prev = x
        lp = lp_a * lp + (1 - lp_a) * hp
        y = math.tanh(lp * 4.0) * 0.8 + 0.05 * rnd.uniform(-1, 1)
        out.append(y)
    for i in range(squelch):                               # release burst
        out.append(0.35 * rnd.uniform(-1, 1) * (1 - i / squelch))
    return out


def speech_windows(a, b, rate, threshold=1500.0, gap=0.5, pad=0.12):
    """Where a cutscene has speech, found by comparing its SAS and Delta mixes (same music and effects, different
    actors): (start, end) sample ranges where the two differ, merged across short gaps and padded a little."""
    win = rate // 20
    loud = []
    for w in range(0, min(len(a), len(b)) - win, win):
        d = sum((a[i] - b[i]) ** 2 for i in range(w, w + win, 4)) / (win / 4)
        loud.append(math.sqrt(d) > threshold)
    spans = []
    for i, on in enumerate(loud):
        if not on:
            continue
        if spans and i - spans[-1][1] <= gap * 20:
            spans[-1][1] = i
        else:
            spans.append([i, i])
    n = min(len(a), len(b))
    return [(max(0, s * win - int(pad * rate)), min(n, (e + 1) * win + int(pad * rate))) for s, e in spans]


def radio_scene(left, right, windows, rate, seed):
    """The cutscene's mix with the speech stretches heard over a field radio: in each window the sound is summed to
    mono and run through the radio effect, faded in and out over 40 ms, with a squelch burst at both ends. The rest
    of the scene - and its length - stays as it was."""
    rnd = random.Random(seed)
    left, right = list(left), list(right)
    fade = int(rate * 0.04)
    for start, end in windows:
        seg = [(left[i] + right[i]) // 2 for i in range(start, end)]
        wet = radio(seg, rate, seed + start)
        squelch = int(rate * 0.06)
        wet = wet[squelch:squelch + len(seg)]               # same length as the window (bursts added below)
        for k, i in enumerate(range(start, end)):
            mix = min(1.0, k / fade, (end - 1 - i) / fade)  # crossfade between the original and the radio
            v = wet[k] * 32767 * 0.9
            left[i] = int(left[i] * (1 - mix) + v * mix)
            right[i] = int(right[i] * (1 - mix) + v * mix)
        for at in (start, max(start, end - squelch)):     # key-up / release clicks
            for k in range(squelch):
                if at + k < len(left):
                    burst = int(9000 * rnd.uniform(-1, 1) * (1 - k / squelch))
                    left[at + k] = max(-32768, min(32767, left[at + k] + burst))
                    right[at + k] = max(-32768, min(32767, right[at + k] + burst))
    clip = lambda c: [max(-32768, min(32767, v)) for v in c]
    return clip(left), clip(right)


def radio_cutscene(game_dir, scene, folder):
    """Cutscene `scene` (e.g. CS_M01_S01) with its speech over the radio, for both actors: Sound\\SAS (from the English
    file) and Sound\\Delta (from the American one) - the same steps a modder takes with ds_sound.py stream / psf and an
    audio editor."""
    base = os.path.join(game_dir, "Data", "Sound")
    rate, en_l, en_r = sound.decode_stream(open(os.path.join(base, "English", scene + ".psf"), "rb").read())
    _, us_l, us_r = sound.decode_stream(open(os.path.join(base, "American", scene + ".psf"), "rb").read())
    windows = speech_windows(en_l, us_l, rate)
    for actors, (l, r) in (("SAS", (en_l, en_r)), ("Delta", (us_l, us_r))):
        out_l, out_r = radio_scene(l, r, windows, rate, 7)
        os.makedirs(os.path.join(folder, actors), exist_ok=True)
        with open(os.path.join(folder, actors, scene + ".psf"), "wb") as f:
            f.write(sound.encode_stream(out_l, out_r))
    return windows, rate


def radio_voice(game_dir, bank, folder):
    """Every line of squad voice bank `bank` (Delta actor) with the radio effect, as <bank>_lineNN.wav."""
    data = open(os.path.join(game_dir, "mission3.sch"), "rb").read()
    cl = sound.chunks(data)
    b = sound.bank_chunk(cl, bank)
    have = sound.samples(cl)
    os.makedirs(folder, exist_ok=True)
    for k, i in enumerate(sound.bank_samples(b, have)):
        chunk = next((c for c in have[i] if sound.sample_language(c) == 6), None) or have[i][0]
        _sid, rate, bits, raw = sound.sample_info(chunk)
        samples = radio(sound.decode(raw, bits), rate, k)
        pcm = struct.pack("<%dh" % len(samples), *(max(-32767, min(32767, int(s * 32767))) for s in samples))
        name = "%s_line%02d_%s.wav" % (bank, k + 1, sound.VOICE_LINES[k + 1])   # as ds_sound.py wav names them
        with wave.open(os.path.join(folder, name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)


def gunshot(seed, length=0.45, thump=90.0, crack=0.9, decay=11.0):
    """A synthetic gunshot: a noise crack plus a falling low thump, fading out."""
    rnd = random.Random(seed)
    out, lp = [], 0.0
    for i in range(int(RATE * length)):
        t = i / RATE
        env = math.exp(-t * decay)
        lp += 0.35 * (rnd.uniform(-1, 1) - lp)                     # darker noise
        body = math.sin(2 * math.pi * thump * t * (1 - 0.5 * t)) * math.exp(-t * 18)
        out.append(0.85 * env * (crack * lp + 0.8 * body))
    return out


def tail(seed, length=1.4):
    """The distant echo: low rumbling noise with a slow fade."""
    rnd = random.Random(seed)
    out, lp, lp2 = [], 0.0, 0.0
    for i in range(int(RATE * length)):
        t = i / RATE
        lp += 0.05 * (rnd.uniform(-1, 1) - lp)
        lp2 += 0.05 * (lp - lp2)
        out.append(3.5 * lp2 * math.exp(-t * 2.6) * min(1.0, t * 40))
    return out


def bank_pack(game_dir, bank, rename):
    for name in sorted(os.listdir(game_dir)):
        if name.lower().endswith(".sch"):
            data = open(os.path.join(game_dir, name), "rb").read()
            out = sound.extract_bank(data, bank, rename)
            if out:
                return out
    raise SystemExit("sound bank %s not found in the game's .sch files" % bank)


# ------------------------------------------------------------------------------------------------- build --
def build(game, out, work, blender):
    src = os.path.join(HERE, "mods")
    # Start clean - but only the examples' own folders (and their switched-off "_" twins): -o may be a real Mods
    # folder with other mods in it, which must survive.
    for name in set(os.listdir(src)) | set(BLENDER_MODS):
        for variant in (name, name.lstrip("_"), "_" + name.lstrip("_")):
            shutil.rmtree(os.path.join(out, variant), ignore_errors=True)
    for mod in sorted(os.listdir(src)):                     # the hand-written files
        shutil.copytree(os.path.join(src, mod), os.path.join(out, mod), dirs_exist_ok=True)

    def mod(name, *path):
        d = os.path.join(out, name)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, *path)

    print("textures")
    write_dds(recolour(game.texture("HERO01_UK_01"), pink, soldier_keep()), mod("PinkSAS", "SASPink_01.dds"))
    write_dds(recolour(game.texture("BRADLEYSASPORTRAIT"), tint((255, 110, 190))), mod("PinkSAS", "PinkPortrait.dds"),
              "dxt3")
    # BigHeadUniform: an olive uniform of its own (a body skin needs its own texture name - the body goes with it)
    write_dds(recolour(game.texture("HERO01_UK_01"), tint((150, 165, 105)), soldier_keep()), mod("BigHeadUniform", "BigHead_UK.dds"))
    jacket = jacket_only(soldier_keep())
    write_dds(quilted(recolour(game.texture("HERO01_UK_01"), navy, jacket), jacket), mod("QuiltedSAS", "QuiltedSAS_01.dds"))
    w, h, px = images.decode_tga(game.get("HERO01BUMP_UK_01.TGA"))
    w, h, px = quilted_normals((w, h, bytearray(px)), jacket)
    with open(mod("QuiltedSAS", "QuiltedSAS_Bump.tga"), "wb") as f:
        f.write(images.encode_tga(w, h, bytes(px)))
    write_dds(recolour(game.texture("HERO01_UK_01"), tint((150, 128, 96)), soldier_keep()), mod("RadioSAS", "RadioSAS_01.dds"))
    radio_voice(game.dir, "Bradley", mod("RadioSAS", "radio"))
    print("cutscene sound")
    windows, rate = radio_cutscene(game.dir, "CS_M01_S01", mod("RadioCutscene", "Sound"))
    print("  CS_M01_S01 speech at", ", ".join("%.1f-%.1f s" % (a / rate, b / rate) for a, b in windows))
    delta = game.texture("HERO04_US_01")
    keep = soldier_keep(chest=True)
    write_dds(recolour(delta, winter_for(delta, keep), keep), mod("WinterDelta", "DeltaWinter_04.dds"))
    write_dds(recolour(game.texture("PISTOL01_DESERTEAGLE"), gold), mod("GoldenEagle", "GoldEagle.dds"))
    saw = game.texture("LMG01_M249SAW")
    write_dds(recolour(saw, tiger), mod("TigerSAW", "TigerSAW.dds"))
    write_dds(recolour(game.texture("LMG01_M249AMMOBOX"), tiger), mod("TigerSAW", "TigerAmmoBox.dds"))
    write_dds(recolour(saw, tint((255, 70, 60))), mod("SilencedSAW", "SAW_Red.dds"))

    print("weapon pictures")
    saw_icon = game_icon(game, "SAW")
    write_icon(icon_recolour(saw_icon, tiger), mod("TigerSAW", "TigerSAW_Icon.png"))
    write_icon(icon_recolour(game_icon(game, "DESERTEAGLE"), gold), mod("GoldenEagle", "GoldEagle_Icon.png"))
    # silenced SAW: blue like its texture (ammo box stays), a suppressor in front of the muzzle (the barrel is rows 7-11)
    silenced = icon_crop(icon_recolour(saw_icon, tint((70, 110, 255)), only_grey=True), 0, 0, 20)
    write_icon(icon_tube(silenced, 0, 22, 5, 14, (20, 26, 60), (90, 110, 190)), mod("SilencedSAW", "SAW_Silenced_Icon.png"))
    # MP5 Breacher: the MP5SD without its suppressor (columns 2-14, rows 6-11) - a short thin barrel instead
    mp5 = icon_crop(game_icon(game, "MP5"), 15)
    breacher = icon_crop(mp5, 0, 0, 8)
    write_icon(icon_tube(breacher, 0, 8, 8, 11), mod("LoudMP5", "Breacher_Icon.png"))

    print("sounds")
    write_wav(mod("TigerSAW", "tiger_shot1.wav"), gunshot(1, thump=85))
    write_wav(mod("TigerSAW", "tiger_shot2.wav"), gunshot(2, thump=100, decay=13))
    write_wav(mod("TigerSAW", "tiger_tail.wav"), tail(3))
    write_wav(mod("SilencedSAW", "shot.wav"), gunshot(4, length=0.3, thump=60, crack=0.5, decay=16))
    with open(mod("LoudMP5", "BREACHER.sch"), "wb") as f:
        f.write(bank_pack(game.dir, "REMMINGTON870", "BREACHER"))

    if not blender:
        for m in BLENDER_MODS:                              # incomplete without their models / animations
            shutil.rmtree(os.path.join(out, m), ignore_errors=True)
        print("no --blender: skipped", ", ".join(BLENDER_MODS))
        return

    print("game files for Blender")
    for name in ("HERO01_ARMSTRONG.EVO", "HERO01_UK_01.DDS", "LMG01_M249SAW.EVO", "LMG01_M249SAW.DDS",
                 "LMG01_M249AMMOBOX.DDS", "SMG01_MP5SD3.EVO", "SMG01_MP5SD3.DDS", "OILDRUM.EVO", "OILDRUM.DDS", "UPRIGHT_RELAXED_WITH_RIFLE.PRB",
                 "UPRIGHT_RELAXED_RUN_WITH_RIFLE.PRB", "UPRIGHT_RELAXED_RELOAD_RIFLE.PRB", "PRONE_RELOAD_LMG.PRB"):
        game.save(name, work)
    write_png(recolour(saw, tint((70, 110, 255))), os.path.join(work, "SAW_Blue.png"))
    write_png(recolour(game.texture("OILDRUM"), hazard), os.path.join(work, "HazardDrum.png"))

    print("Blender")
    script = os.path.join(HERE, "blender_examples.py")
    r = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", script, "--",
                        work, out], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith(("example:", "  ", "Error", "Traceback")) or "Error" in line:
            print(line)
    if r.returncode:
        print(r.stderr[-3000:])
        raise SystemExit("Blender failed")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-g", "--game", help="game folder (default: found from the registry)")
    p.add_argument("-o", "--out", default=os.path.join(HERE, "build", "Mods"), help="output Mods folder")
    p.add_argument("--blender", help="blender.exe, for the model and animation examples")
    p.add_argument("--install", action="store_true", help="copy the built mods into <game>\\Mods")
    a = p.parse_args()
    game_dir = a.game or archive.find_game_dir()
    if not game_dir:
        raise SystemExit("game not found - pass -g <game folder>")
    game = Game(game_dir)
    work = os.path.join(os.path.dirname(a.out), "work")
    build(game, a.out, work, a.blender)
    print("built:", a.out)
    examples = set(os.listdir(os.path.join(HERE, "mods"))) | set(BLENDER_MODS)
    built = [m for m in sorted(os.listdir(a.out)) if m in examples]   # only ours, if -o is a real Mods folder
    for m in built:
        print("  %-16s %s" % (m, ", ".join(sorted(os.listdir(os.path.join(a.out, m))))))
    if a.install:
        dest = os.path.join(game_dir, "Mods")
        if os.path.normcase(os.path.abspath(dest)) == os.path.normcase(os.path.abspath(a.out)):
            print("already built into", dest)
            return
        for m in built:
            shutil.copytree(os.path.join(a.out, m), os.path.join(dest, m), dirs_exist_ok=True)
        print("installed into", dest)


if __name__ == "__main__":
    main()
