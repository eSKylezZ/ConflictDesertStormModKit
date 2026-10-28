r"""make_frogman.py <out folder> [--game DIR] [--install] - a Danish frogman (Fromandskorpset) uniform for Bradley.

A body + uniform built from Bradley's own body (HERO01_ARMSTRONG, read from the player's game files), so it keeps the
standard 51-object skeleton and every soldier animation:
  - the skin is subdivided twice (1028 -> ~16k vertices) and sculpted: a hood over a helmet shape (no ears / nose),
    a black shirt with ragged, rolled-up sleeves, a coyote belt + buckle, rig straps, gloves, boots;
  - kit geometry in the same skin (one texture): a tiger-stripe chest rig (3 mag pouches, 2 radio pouches with green
    radios and antennas, an admin panel with a Danish flag), a scrim-net drape over head and shoulders, hip pouches
    and a thigh holster - every kit vertex takes the bone weights of the body surface under it;
  - Bradley's forearms (sleeves) and mitten hands are replaced by new lofted arms, rolled sleeve cuffs and gloved
    hands with five fingers in a grip pose;
  - one 2048 x 2048 texture: Bradley's UVs in the top-left 1280 x 1280 painted per texel from model-space positions,
    the new arms / hands / cuffs and the kit atlas around it: net hood, tattooed forearms (anchor + "FKP" banner, a rose, waves), tan gloves ...
  - a matching normal map and a 64 x 64 portrait.
Files: FROGMAN_BRADLEY.evo, FROGMAN_DK.dds, FROGMAN_DK_BUMP.tga, FROGMAN_PORTRAIT.dds, DanishFrogman.skin
(+ previews in <out>\preview when --preview)."""
import argparse, math, os, shutil, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [KIT, os.path.join(HERE, "..", "hovercraft")]
from conflict_ds_tools import evo, evo_write, images  # noqa: E402
from gamefiles import read_game_file  # noqa: E402

TEX = "FROGMAN_DK"
MODEL = "FROGMAN_BRADLEY"
BUMP = "FROGMAN_DK_BUMP"
PORTRAIT = "FROGMAN_PORTRAIT"
TS = 2048                 # texture size
BODY_S = 1280 / TS        # Bradley's own UVs (body, sleeves, legs, head) squeezed into the top-left 1280 x 1280
ARM_RECT = {-1: (1280, 0, 1664, 640), 1: (1664, 0, 2048, 640)}       # the new bare arms (u round, v along)
HAND_RECT = {-1: (1280, 640, 1664, 1280), 1: (1664, 640, 2048, 1280)}  # the new gloved hands
CUFF_RECT = {-1: (0, 1700, 512, 1860), 1: (512, 1700, 1024, 1860)}     # the rolled-up sleeve ends
K = 2                     # kit atlas: designed on a 1024 texture, drawn twice as big


def _kreg(r):
    x0, y0, x1, y1 = r
    return (x0 * K, (y0 - 820) * K + 1280, x1 * K, (y1 - 820) * K + 1280)


# kit atlas regions (pixels, x0 y0 x1 y1)
R_RIG = _kreg((0, 820, 300, 1024))
R_RADIO = _kreg((300, 820, 380, 920))
R_ADMIN = _kreg((300, 920, 380, 1024))
R_CAMO = _kreg((380, 820, 460, 920))
R_BLACK = _kreg((380, 920, 420, 1024))
R_COYOTE = _kreg((420, 920, 460, 1024))
R_NET = _kreg((460, 820, 1024, 1024))
RIG_X, RIG_Y = (-17.0, 17.0), (110.0, 134.0)      # chest rig extent (cm, model space)


# ---------------------------------------------------------------- noise
def _hash3(ix, iy, iz, seed):
    h = (ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791) ^ (seed * 2654435761)
    h &= 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFF) / 65535.0


def vnoise(p, scale, seed=0):
    q = np.asarray(p, float) * scale
    i = np.floor(q).astype(np.int64)
    f = q - i
    f = f * f * (3 - 2 * f)
    out = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1]) * (f[:, 2] if dz else 1 - f[:, 2])
                out = out + w * _hash3(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz, seed)
    return out


def fbm(p, scale, seed, octaves=4):
    s, a, tot = 0.0, 0.5, 0.0
    for k in range(octaves):
        s = s + a * vnoise(p, scale * 2 ** k, seed + 17 * k)
        tot += a
        a *= 0.5
    return s / tot


def noise2(w, h, scale, seed, octaves=4):
    """2D fbm image (h, w) in 0..1, scale = cells per pixel."""
    ys, xs = np.mgrid[0:h, 0:w]
    p = np.c_[xs.ravel(), ys.ravel(), np.zeros(w * h)]
    return fbm(p, scale, seed, octaves).reshape(h, w)


def sstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- camo patterns
TIGER = (np.array((104, 110, 78)), np.array((62, 70, 46)), np.array((24, 26, 20)))


def tiger(p):
    """Tiger stripe from points p (n, 3) in pattern space: horizontal brush strokes."""
    q = np.c_[p[:, 0] * 0.55 + p[:, 2] * 0.55, p[:, 1] * 2.6, p[:, 2] * 0.2]
    warp = fbm(p * 0.3, 0.5, 41, 3)
    q[:, 1] += warp * 6
    a = fbm(q, 0.18, 42, 4)
    b = fbm(q * 1.7, 0.18, 43, 3)
    out = np.tile(TIGER[0], (len(p), 1)).astype(float)
    out[a > 0.5] = TIGER[1]
    out[(a > 0.58) | (b > 0.64)] = TIGER[2]
    return out * (0.9 + 0.2 * fbm(p, 1.5, 44, 2))[:, None]


MULTICAM = [np.array(c) for c in ((150, 140, 104), (104, 110, 74), (122, 96, 66), (70, 64, 48), (186, 176, 138))]


def multicam(p):
    out = np.tile(MULTICAM[0], (len(p), 1)).astype(float)
    for k, (sc, th) in enumerate(((0.22, 0.56), (0.3, 0.6), (0.5, 0.66), (0.7, 0.7)), 1):
        n = fbm(p, sc, 60 + k, 3)
        out[n > th] = MULTICAM[k]
    return out * (0.92 + 0.16 * fbm(p, 2.0, 70, 2))[:, None]


# ---------------------------------------------------------------- tattoos (canvas: arc length x along the arm)
TAT_PX = 16                               # pixels per cm
TAT_U, TAT_V = (-17.6, 17.6), (34.0, 73.0)  # u = arc length from the top of the arm (cm), v = |x| (cm)


def _font(size):
    for f in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def tattoo_canvas(side):
    w = int((TAT_U[1] - TAT_U[0]) * TAT_PX)
    h = int((TAT_V[1] - TAT_V[0]) * TAT_PX)
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)

    def P(u, v):   # cm -> px
        return ((u - TAT_U[0]) * TAT_PX, (v - TAT_V[0]) * TAT_PX)

    lw = 7
    if side < 0:   # right arm: anchor wrapped in rope, "FKP" banner, a swallow by the wrist
        cu, top, bot = 0.0, 43.0, 61.0
        d.line([P(cu, top), P(cu, bot)], fill=255, width=lw + 2)
        d.line([P(cu - 3.4, top + 2.2), P(cu + 3.4, top + 2.2)], fill=255, width=lw + 1)
        d.ellipse([P(cu - 1.3, top - 2.6), P(cu + 1.3, top)], outline=255, width=lw)
        d.arc([P(cu - 6.2, bot - 8.0), P(cu + 6.2, bot + 2.0)], 20, 160, fill=255, width=lw + 2)
        for sgn in (-1, 1):
            tip = P(cu + sgn * 6.0, bot - 4.6)
            d.polygon([tip, P(cu + sgn * 4.4, bot - 5.6), P(cu + sgn * 7.2, bot - 6.4)], fill=255)
        for k in range(9):   # rope: an S curve of small loops round the shank
            v = top + 1.0 + k * 2.0
            u = 2.6 * math.sin(k * 1.3)
            d.arc([P(u - 1.2, v - 0.9), P(u + 1.2, v + 0.9)], 0, 300, fill=255, width=3)
        # banner across the shank
        bv = 52.0
        d.polygon([P(-7.5, bv - 1.6), P(7.5, bv - 1.6), P(8.8, bv + 1.6), P(-8.8, bv + 1.6)], outline=255, width=4)
        d.polygon([P(-8.8, bv - 0.8), P(-11.0, bv - 1.8), P(-10.2, bv + 0.2), P(-11.0, bv + 2.4), P(-8.6, bv + 1.6)],
                  outline=255, width=3)
        d.polygon([P(8.8, bv - 0.8), P(11.0, bv - 1.8), P(10.2, bv + 0.2), P(11.0, bv + 2.4), P(8.6, bv + 1.6)],
                  outline=255, width=3)
        f = _font(int(2.6 * TAT_PX))
        txt = Image.new("L", img.size, 0)
        ImageDraw.Draw(txt).text(P(0, bv), "FKP", fill=255, font=f, anchor="mm")
        img = Image.composite(Image.new("L", img.size, 0), img, txt.point(lambda a: 255 if a > 90 else 0).filter(
            ImageFilter.MaxFilter(5)))      # cut the banner's lines behind the letters
        img = Image.composite(Image.new("L", img.size, 255), img, txt)
        d = ImageDraw.Draw(img)
        # swallow near the wrist (on the front of the arm)
        su, sv = 9.5, 66.5
        d.polygon([P(su - 3.2, sv - 1.0), P(su, sv + 0.6), P(su + 3.4, sv - 1.6), P(su + 1.2, sv + 1.6),
                   P(su + 2.4, sv + 3.2), P(su, sv + 2.0), P(su - 1.6, sv + 3.0), P(su - 1.0, sv + 1.0)], fill=255)
        # waves round the upper arm under the sleeve's edge
        for row in range(2):
            v = 37.5 + row * 1.6
            pts = [P(u, v + 0.6 * math.sin(u * 1.4 + row)) for u in np.arange(TAT_U[0], TAT_U[1], 0.25)]
            d.line(pts, fill=255, width=4)
    else:          # left arm: a rose with leaves, a compass star, dots
        cu, cv = 0.0, 50.0
        for r, a0 in ((4.6, 0), (3.5, 40), (2.4, 80), (1.3, 120)):
            for k in range(5):
                a = math.radians(a0 + k * 72)
                du, dv = r * 0.55 * math.cos(a), r * 0.55 * math.sin(a)
                d.arc([P(cu + du - r * 0.6, cv + dv - r * 0.6), P(cu + du + r * 0.6, cv + dv + r * 0.6)],
                      a0 + k * 72 - 80, a0 + k * 72 + 100, fill=255, width=4)
        d.ellipse([P(cu - 5.2, cv - 5.2), P(cu + 5.2, cv + 5.2)], outline=255, width=4)
        for sgn in (-1, 1):   # leaves + stem
            lu, lv = cu + sgn * 6.4, cv + 5.0
            d.polygon([P(lu - sgn * 1.4, lv - 2.2), P(lu + sgn * 2.4, lv + 0.6), P(lu - sgn * 0.6, lv + 2.8)],
                      outline=255, width=4)
            d.line([P(lu - sgn * 1.4, lv - 2.2), P(lu - sgn * 0.2, lv + 0.8)], fill=255, width=3)
        d.line([P(cu, cv + 5.2), P(cu + 1.0, cv + 13.0)], fill=255, width=5)
        # compass star near the wrist
        su, sv = 0.0, 66.0
        for k in range(8):
            a = math.radians(k * 45)
            r = 3.0 if k % 2 == 0 else 1.6
            d.polygon([P(su, sv), P(su + r * math.cos(a) + 0.5 * math.cos(a + 1.57), sv + r * math.sin(a)
                                      + 0.5 * math.sin(a + 1.57)), P(su + r * math.cos(a), sv + r * math.sin(a))],
                      fill=255 if k % 2 == 0 else 170)
        d.ellipse([P(su - 1.0, sv - 1.0), P(su + 1.0, sv + 1.0)], outline=255, width=3)
        for k in range(14):   # dot band round the upper arm
            u = TAT_U[0] + k * 2.5
            d.ellipse([P(u - 0.35, 38.0), P(u + 0.35, 38.7)], fill=255)
    img = img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    return np.asarray(img, float) / 255.0


# ---------------------------------------------------------------- body painting
def classify(bone_names):
    cat = []
    for n in bone_names:
        if "HEAD" in n or "NECK" in n:
            cat.append(0)
        elif any(k in n for k in ("UPPERARM", "FOREARM", "HAND")):
            cat.append(2)
        elif any(k in n for k in ("THIGH", "CALF", "FOOT", "TOE")):
            cat.append(3)
        else:
            cat.append(1)
    return np.array(cat)


TATTOOS = {}


def paint_body(p, n, cat):
    """Colour (n, 3), texture height (cm, for the normal map) and geometry height (cm, displacement) per point."""
    N = len(p)
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    ax = np.abs(x)
    side = np.where(x < 0, -1.0, 1.0)
    fab = fbm(p, 0.35, 1, 3)
    fine = vnoise(p, 2.5, 2)
    rgb = np.array((33.0, 33.0, 35.0)) * (0.82 + 0.36 * fab)[:, None] + (fine[:, None] - 0.5) * 8
    dust = vnoise(p, 5.0, 3) > 0.8
    rgb[dust] += 14
    htex = (fine - 0.5) * 0.04
    hgeo = np.zeros(N)

    legs = (cat == 3) | ((cat == 1) & (y < 86.5))
    arm = cat == 2
    head = (cat == 0) & (y > 146)

    # ---- sleeves, skin, tattoos, gloves (arms lie along x in the bind pose; angle 0 = top = outside when hanging)
    ang = np.arctan2(-(p[:, 2] - 7.0), p[:, 1] - 143.0)
    rag_p = np.c_[np.cos(ang) * 4, np.sin(ang) * 4, side * 10]
    cut = 99.0 + 0 * rag_p[:, 0]          # Bradley's sleeves end under the new rolled cuff (build_arm)
    sleeve = arm & (ax <= cut)
    hgeo[sleeve] = 0.3
    # sleeve pocket on the upper arm's outside
    pocket = sleeve & (np.abs(ang) < 0.85) & (ax > 23.5) & (ax < 31.5)
    rgb[pocket] *= 1.12
    edge = pocket & ((np.abs(ang) > 0.74) | (ax < 24.1) | (ax > 30.9))
    rgb[edge] = (20, 20, 22)
    htex[pocket] += 0.06
    velcro = pocket & (np.abs(ang) < 0.55) & (ax > 25) & (ax < 29.5)
    rgb[velcro] = np.array((48.0, 48.0, 46.0)) + (vnoise(p[velcro], 8, 4)[:, None] - 0.5) * 18
    # rolled cuff
    roll = arm & (ax <= cut) & (ax > cut - 3.6)
    k = (cut - ax)[roll] / 3.6
    rgb[roll] = np.array((50.0, 50.0, 52.0)) * (0.75 + 0.4 * np.sin(k * math.pi))[:, None]
    fold = roll & ((((cut - ax) / 1.25) % 1.0) < 0.12)
    rgb[fold] *= 0.55
    hgeo[roll] = 0.3 + 0.75 * np.sin(k * math.pi)
    htex[roll] += 0.15 * np.sin(k * math.pi * 3)
    fray = arm & (ax > cut - 0.35) & (ax <= cut + 0.25) & (vnoise(p, 9, 5) > 0.45)
    rgb[fray] = (96, 94, 90)
    # skin
    skin = arm & (ax > cut)
    sk = np.array((200.0, 142.0, 112.0)) * (0.9 + 0.16 * fbm(p[skin], 0.6, 9, 3))[:, None]
    hair = (vnoise(p[skin], 6.0, 10) > 0.78) & (np.abs(ang[skin]) < 1.6)
    sk[hair] *= 0.8
    freck = vnoise(p[skin], 3.0, 11) > 0.82
    sk[freck] *= np.array((0.86, 0.78, 0.74))
    for s in (-1, 1):
        if s not in TATTOOS:
            TATTOOS[s] = tattoo_canvas(s)
        can = TATTOOS[s]
        m = side[skin] == s
        u = ang[skin][m] * 5.6
        v = ax[skin][m]
        cx = np.clip(((u - TAT_U[0]) * TAT_PX).astype(int), 0, can.shape[1] - 1)
        cy = np.clip(((v - TAT_V[0]) * TAT_PX).astype(int), 0, can.shape[0] - 1)
        a = np.clip(can[cy, cx] * 1.25, 0, 0.95)
        sub = sk[m]
        sub = sub * (1 - a[:, None]) + np.array((40.0, 46.0, 60.0)) * a[:, None]
        sk[m] = sub
    rgb[skin] = sk
    # gloves
    glove = arm & (ax > 72.3 + 0.4 * np.sin(ang * 2))
    g = np.array((152.0, 108.0, 72.0)) * (0.86 + 0.24 * fbm(p[glove], 0.9, 12, 3))[:, None]
    rgb[glove] = g
    hgeo[glove] = 0.22
    cuffs = glove & (ax < 75.2)
    rgb[cuffs] = np.array((50.0, 45.0, 40.0)) + (vnoise(p[cuffs], 6, 13)[:, None] - 0.5) * 14
    hgeo[cuffs] = 0.35
    back = glove & (n[:, 1] > 0.3)
    knuck = back & (ax > 78.5) & (ax < 85.8)
    rgb[knuck] = np.array((38.0, 36.0, 35.0)) * (0.8 + 0.4 * vnoise(p[knuck], 3, 14))[:, None]
    hgeo[knuck] = 0.5
    htex[knuck] += 0.2 * (np.sin(ax[knuck] * 3.5) > 0.6)
    palm = glove & (n[:, 1] < -0.3) & (ax > 75.2)
    rgb[palm] = np.array((172.0, 132.0, 96.0)) * (0.9 + 0.2 * vnoise(p[palm], 2, 15))[:, None]
    grip = palm & (ax > 76) & (ax < 84) & (vnoise(p, 5, 16) > 0.5)[palm.nonzero()[0]] if False else \
        palm & (ax > 76) & (ax < 84)
    rgb[grip] *= 0.5
    tips = glove & (ax > 88.5)
    rgb[tips] *= np.array((0.62, 0.6, 0.6))

    # ---- trousers + boots
    tr = np.array((30.0, 30.0, 32.0)) * (0.84 + 0.32 * fab[legs])[:, None] + np.sin((x + y)[legs] * 9)[:, None] * 1.6
    rgb[legs] = tr
    hgeo[legs] = 0.0
    knee = legs & (y > 47) & (y < 58) & (n[:, 2] < -0.3)
    rgb[knee] += 6 + vnoise(p[knee], 1.5, 17)[:, None] * 10
    htex[knee] += 0.1 * np.sin(y[knee] * 2.2)
    cpk = legs & (cat == 3) & (y > 58) & (y < 75) & (n[:, 0] * side > 0.45)
    rgb[cpk] *= 1.1
    flap = cpk & (y > 71.5)
    hgeo[flap] = 0.35
    rgb[flap & (y < 72.1)] = (16, 16, 18)
    hgeo[cpk] += 0.15
    stitch = cpk & ((y < 58.6) | (np.abs(n[:, 0] * side - 0.45) < 0.05))
    rgb[stitch] = (18, 18, 20)
    boot = legs & (y < 29.5)
    rgb[boot] = np.array((40.0, 33.0, 28.0)) * (0.82 + 0.36 * fbm(p[boot], 0.8, 18, 3))[:, None]
    hgeo[boot] = 0.35
    sole = legs & (y < 6.8)
    rgb[sole] = (19, 19, 20)
    hgeo[sole] = 0.5
    xc = side * (21.3 + np.clip((53.0 - y) / 36.0, 0, 1) * 11.5)
    lace = boot & (y > 11) & (y < 28.5) & (n[:, 2] < -0.45) & (np.abs(x - xc) < 2.2)
    cross = lace & ((((y + 2.0 * np.abs(x - xc)) / 2.2) % 1.0) < 0.28)
    rgb[cross] = (22, 22, 22)
    htex[cross] += 0.12

    # ---- belt + buckle
    belt = ((cat == 1) | (cat == 3)) & (y > 86.5) & (y < 93.6) & (ax < 25)
    bc = np.array((124.0, 100.0, 68.0)) * (0.9 + 0.2 * fab[belt])[:, None] + np.sin(y[belt] * 14)[:, None] * 3
    rgb[belt] = bc
    rgb[belt & ((np.abs(y - 87.3) < 0.18) | (np.abs(y - 92.8) < 0.18))] = (78, 62, 42)
    hgeo[belt] = 0.75
    buckle = belt & (ax < 4.2) & (n[:, 2] < -0.4)
    rgb[buckle] = (56, 58, 61)
    ring = buckle & ((ax > 3.3) | (y < 87.6) | (y > 92.5))
    rgb[ring] = (142, 142, 146)
    hgeo[buckle] = 1.15
    keeper = belt & (np.abs(ax - 9.5) < 1.0) & (n[:, 2] < -0.3)
    rgb[keeper] = (40, 34, 26)

    # ---- rig straps (tiger stripe webbing) on the shirt
    torso = ((cat == 1) & (y > 94)) | (arm & (ax < 23))
    front = z < 1.5
    fs = torso & front & (np.abs(ax - 11.0) < 2.0) & (y > 131) & (y < 156)
    bs = torso & ~front & (np.abs(ax - 11.0 * np.clip((y - 110.0) / 40.0, 0, 1)) < 2.0) & (y > 110) & (y < 156)
    hs = torso & (y > 111) & (y < 115.5) & (cat == 1)
    straps = fs | bs | hs
    rgb[straps] = tiger(p[straps] * 1.6)
    st_edge = (fs & (np.abs(np.abs(ax - 11.0) - 1.75) < 0.2)) | (hs & ((np.abs(y - 111.3) < 0.2) | (np.abs(y - 115.2) < 0.2)))
    rgb[st_edge] *= 0.5
    hgeo[straps] = 0.4
    htex[straps] += 0.08 * np.sin(y[straps] * 10)

    # ---- hood: dark fabric under a scrim net, burlap scraps, a black face behind the net
    if head.any():
        q = p[head]
        hb = np.array((36.0, 42.0, 31.0)) * (0.75 + 0.5 * fbm(q, 0.5, 20, 3))[:, None]
        scr = fbm(np.c_[q[:, 0], q[:, 1] * 0.5, q[:, 2]], 0.3, 21, 3) > 0.6
        hb[scr] = np.array((92.0, 92.0, 66.0)) * (0.8 + 0.3 * vnoise(q[scr], 3, 22))[:, None]
        face = (q[:, 2] < -3.0) & (q[:, 1] < 173) & (q[:, 1] > 157)
        hb[face] *= 0.35
        th = np.arctan2(q[:, 0], -(q[:, 2] - 2.0))
        u, v = th * 10.5, q[:, 1]
        d1, d2 = ((u + v) / 2.3) % 1.0, ((u - v) / 2.3) % 1.0
        dist = np.minimum(np.minimum(d1, 1 - d1), np.minimum(d2, 1 - d2))
        string = dist < 0.12
        hb[~string] *= 0.6
        hb[string] = np.array((74.0, 88.0, 58.0)) * (0.75 + 0.5 * vnoise(q[string], 1.5, 23))[:, None]
        rgb[head] = hb
        htex[head] += np.where(string, 0.12, 0.0)
    # baked light: a little darker underneath
    rgb *= (0.84 + 0.16 * np.clip(n[:, 1] * 0.5 + 0.5, 0, 1))[:, None]
    return np.clip(rgb, 0, 255), htex, hgeo


# ---------------------------------------------------------------- mesh helpers
def vertex_normals(V, T):
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, T[:, k], fn)
    key = {}
    for i, v in enumerate(np.round(V, 2)):
        key.setdefault(tuple(v), []).append(i)
    out = np.zeros_like(V)
    for vs in key.values():
        s = N[vs].sum(axis=0)
        out[vs] = s / (np.linalg.norm(s) + 1e-9)
    return out


def subdivide(V, UV, W, T):
    V, UV, W = list(V), list(UV), list(W)
    mid = {}

    def m(a, b):
        k = (a, b) if a < b else (b, a)
        if k not in mid:
            mid[k] = len(V)
            V.append((V[a] + V[b]) / 2)
            UV.append((UV[a] + UV[b]) / 2)
            d = {}
            for src in (W[a], W[b]):
                for bn, w in src.items():
                    d[bn] = d.get(bn, 0.0) + w / 2
            W.append(d)
        return mid[k]

    out = []
    for a, b, c in T:
        ab, bc, ca = m(a, b), m(b, c), m(c, a)
        out += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
    return np.array(V), np.array(UV), W, np.array(out)


def taubin(V, T, iters=10, lam=0.5, mu=-0.53, keep=None):
    """Taubin smoothing (no shrinking) over vertices welded by position, so UV seams stay closed. keep = per-vertex
    0..1 how much of the smoothing to take."""
    G, inv = np.unique(np.round(V, 3), axis=0, return_inverse=True)
    inv = inv.ravel()
    P = np.zeros((len(G), 3))
    np.add.at(P, inv, V)
    P /= np.bincount(inv, minlength=len(G))[:, None]
    e = inv[np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]]
    e = np.unique(np.sort(e[e[:, 0] != e[:, 1]], 1), axis=0)
    deg = np.bincount(e.ravel(), minlength=len(G)).astype(float)
    deg[deg == 0] = 1
    k = np.ones(len(G))
    if keep is not None:
        k = np.zeros(len(G))
        np.maximum.at(k, inv, keep)
    for _ in range(iters):
        for f in (lam, mu):
            s = np.zeros_like(P)
            np.add.at(s, e[:, 0], P[e[:, 1]])
            np.add.at(s, e[:, 1], P[e[:, 0]])
            P += (f * k)[:, None] * (s / deg[:, None] - P)
    return P[inv]


def top4(d):
    items = sorted(d.items(), key=lambda kv: -kv[1])[:4]
    s = sum(w for _b, w in items)
    return {b: w / s for b, w in items}


class Ray:
    """Moller-Trumbore against a fixed mesh."""

    def __init__(self, V, T):
        self.v0 = V[T[:, 0]]
        self.e1 = V[T[:, 1]] - self.v0
        self.e2 = V[T[:, 2]] - self.v0

    def hit(self, o, d):
        d = np.asarray(d, float)
        pv = np.cross(d, self.e2)
        det = (self.e1 * pv).sum(1)
        ok = np.abs(det) > 1e-9
        inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
        tv = np.asarray(o, float) - self.v0
        u = (tv * pv).sum(1) * inv
        qv = np.cross(tv, self.e1)
        v = (qv * d).sum(1) * inv
        t = (qv * self.e2).sum(1) * inv
        m = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
        return t[m].min() if m.any() else None


class Kit:
    """Extra skin geometry: vertices with explicit UVs; triangles oriented outward with the game's winding."""

    def __init__(self, sign):
        self.sign = sign
        self.P, self.UV, self.T = [], [], []
        self.rigid = []          # per vertex: bone name or None (None = the body surface's weights)

    def v(self, p, uv, rigid=None):
        self.P.append(np.asarray(p, float))
        self.UV.append(uv)
        self.rigid.append(rigid)
        return len(self.P) - 1

    def tri(self, a, b, c, out):
        n = np.cross(self.P[b] - self.P[a], self.P[c] - self.P[a])
        if np.linalg.norm(n) < 1e-9:
            return
        if (n @ np.asarray(out, float)) * self.sign < 0:
            b, c = c, b
        self.T.append((a, b, c))

    def quad(self, a, b, c, d, out):
        self.tri(a, b, c, out)
        self.tri(a, c, d, out)

    def piece(self, tris_out):
        """Triangles of one connected piece, oriented together: one vote over all of them (a triangle folded over in a
        tight bend must keep its neighbours' winding, or the shadow volume's outline breaks)."""
        vote = 0.0
        for (a, b, c), out in tris_out:
            n = np.cross(self.P[b] - self.P[a], self.P[c] - self.P[a])
            vote += np.sign(n @ np.asarray(out, float))
        flip = vote * self.sign < 0
        for (a, b, c), _out in tris_out:
            self.T.append((a, c, b) if flip else (a, b, c))

    def box(self, lo, hi, reg_front, reg_other=None, rigid=None):
        reg_other = reg_other or reg_front
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        c = (lo + hi) / 2
        for axis in range(3):
            for s in (-1, 1):
                nrm = np.zeros(3)
                nrm[axis] = s
                u_ax, v_ax = [k for k in range(3) if k != axis]
                if axis == 1:
                    u_ax, v_ax = 0, 2
                reg = reg_front if (axis == 2 and s < 0) else reg_other
                ids = []
                for su, sv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    p = c.copy()
                    p[axis] = hi[axis] if s > 0 else lo[axis]
                    p[u_ax] = hi[u_ax] if su else lo[u_ax]
                    p[v_ax] = hi[v_ax] if sv else lo[v_ax]
                    tv = 1 - sv if v_ax == 1 else sv           # image rows run down
                    ids.append(self.v(p, region_uv(reg, su, tv), rigid))
                self.quad(*ids, out=nrm)

    def tube(self, pts, r, reg, rigid, n=8):
        pts = [np.asarray(p, float) for p in pts]
        rings = []
        for i, p in enumerate(pts):
            d = pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]
            d /= np.linalg.norm(d)
            a = np.cross(d, (1.0, 0, 0) if abs(d[0]) < 0.9 else (0, 1.0, 0))
            a /= np.linalg.norm(a)
            b = np.cross(d, a)
            rr = r * (0.5 if i == len(pts) - 1 else 1.0)
            rings.append([self.v(p + rr * (math.cos(t) * a + math.sin(t) * b), region_uv(reg, k / n, i / (len(pts) - 1)),
                                 rigid) for k, t in enumerate(np.linspace(0, 2 * math.pi, n, endpoint=False))])
        for i in range(len(rings) - 1):
            for k in range(n):
                a, b = rings[i][k], rings[i][(k + 1) % n]
                c, d = rings[i + 1][(k + 1) % n], rings[i + 1][k]
                out = (self.P[a] + self.P[c]) / 2 - (pts[i] + pts[i + 1]) / 2
                self.quad(a, b, c, d, out)
        top = self.v(pts[-1] + (pts[-1] - pts[-2]) / np.linalg.norm(pts[-1] - pts[-2]) * r * 0.5,
                     region_uv(reg, 0.5, 1.0), rigid)
        for k in range(n):
            self.tri(rings[-1][k], rings[-1][(k + 1) % n], top, out=pts[-1] - pts[-2])


def region_uv(reg, s, t):
    x0, y0, x1, y1 = reg
    return ((x0 + 1.5 + s * (x1 - x0 - 3)) / TS, (y0 + 1.5 + t * (y1 - y0 - 3)) / TS)


def sub_region(reg, s0, t0, s1, t1):
    x0, y0, x1, y1 = reg
    return (x0 + s0 * (x1 - x0), y0 + t0 * (y1 - y0), x0 + s1 * (x1 - x0), y0 + t1 * (y1 - y0))


# ---------------------------------------------------------------- chest rig shape
MAGS = (-6.2, 0.0, 6.2)
RADIOS = (-13.4, 13.4)


def rig_depth(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    edge = np.minimum(np.minimum(x - RIG_X[0], RIG_X[1] - x), np.minimum(y - RIG_Y[0], RIG_Y[1] - y))
    d = 0.6 + 0.7 * sstep(0, 0.9, edge)

    def pouch(cx, hw, y0, y1, depth, r=0.9):
        ex = hw - np.abs(x - cx)
        ey = np.minimum(y - y0, y1 - y)
        return depth * sstep(0, r, ex) * sstep(0, r, ey)
    for cx in MAGS:
        d = np.maximum(d, pouch(cx, 2.9, 110.6, 130.0, 4.3))
    for cx in RADIOS:
        d = np.maximum(d, pouch(cx, 3.2, 111.0, 128.8, 4.8))
    return d


# ---------------------------------------------------------------- kit atlas painting
def paint_atlas(img, hmap):
    rng = np.random.default_rng(5)
    # rig front: x, y in model space
    x0, y0, x1, y1 = R_RIG
    w, h = x1 - x0, y1 - y0
    ys, xs = np.mgrid[0:h, 0:w]
    s = (xs + 0.5 - 1.5) / (w - 3)
    t = (ys + 0.5 - 1.5) / (h - 3)
    X = RIG_X[0] + s * (RIG_X[1] - RIG_X[0])
    Y = RIG_Y[1] - t * (RIG_Y[1] - RIG_Y[0])
    P = np.c_[X.ravel(), Y.ravel(), np.zeros(w * h)]
    col = tiger(P * 1.6).reshape(h, w, 3)
    D = rig_depth(X, Y)
    gy, gx = np.gradient(D)
    slope = np.hypot(gx, gy)
    col *= (0.72 + 0.28 * np.clip(D / 4.5, 0, 1))[..., None]          # grooves between pouches darker
    col[slope > 0.35 / K] *= 0.55                                          # seams round each pouch
    hh = D * 0.25
    for cx in MAGS:
        band = (np.abs(X - cx) < 2.7) & (Y > 117.5) & (Y < 119.8)
        col[band] = (34, 36, 30)
        hh[band] += 0.15
        flap = (np.abs(X - cx) < 2.8) & (Y > 125.6) & (Y < 130.0)
        col[flap] *= 1.08
        col[(np.abs(X - cx) < 2.8) & (np.abs(Y - 125.6) < 0.25)] = (20, 22, 18)
        col[(np.abs(X - cx) < 0.8) & (Y > 124.0) & (Y < 125.8)] = (30, 30, 30)   # flap tab
    for cx in RADIOS:
        bung = (np.abs(X - cx) < 3.0) & (np.abs(Y - 123.5) < 0.45)
        col[bung] = (18, 18, 18)
        col[(np.abs(X - cx) < 3.1) & (np.abs(Y - 128.6) < 0.25)] = (20, 22, 18)
    pull = (np.abs(X - 3.1) < 0.45) & (Y > 122.0) & (Y < 127.0)
    col[pull] = (186, 28, 30)
    binding = (np.minimum(np.minimum(X - RIG_X[0], RIG_X[1] - X), np.minimum(Y - RIG_Y[0], RIG_Y[1] - Y)) < 0.4)
    col[binding] = (28, 30, 24)
    img[y0:y1, x0:x1] = col
    hmap[y0:y1, x0:x1] = hh

    def fill(reg, fn):
        rx0, ry0, rx1, ry1 = reg
        rw, rh = rx1 - rx0, ry1 - ry0
        yy, xx = np.mgrid[0:rh, 0:rw]
        img[ry0:ry1, rx0:rx1] = fn(xx, yy, rw, rh)

    def radio(xx, yy, rw, rh):
        c = np.ones((rh, rw, 3)) * np.array((78.0, 94.0, 58.0))
        c *= (0.88 + 0.24 * noise2(rw, rh, 0.08 / K, 31, 3))[..., None]
        keys = (xx > rw * 0.22) & (xx < rw * 0.78) & (yy > rh * 0.5) & (yy < rh * 0.9)
        c[keys & (((xx // (6 * K)) + (yy // (6 * K))) % 2 == 0)] = (30, 32, 26)
        c[keys & ~(((xx // (6 * K)) + (yy // (6 * K))) % 2 == 0)] = (52, 60, 40)
        disp = (xx > rw * 0.25) & (xx < rw * 0.75) & (yy > rh * 0.2) & (yy < rh * 0.38)
        c[disp] = (60, 70, 62)
        c[(yy < 18 * K) & (xx < 18 * K)] = (78, 94, 58)                          # plain corner for the box sides
        return c
    fill(R_RADIO, radio)

    def admin(xx, yy, rw, rh):
        c = np.ones((rh, rw, 3)) * np.array((172.0, 154.0, 120.0))
        c *= (0.9 + 0.2 * noise2(rw, rh, 0.1 / K, 32, 3))[..., None]
        fx0, fx1, fy0, fy1 = rw * 0.18, rw * 0.82, rh * 0.18, rh * 0.62     # flag patch (3 : 1 : 4.5 / 3 : 1 : 3)
        flag = (xx >= fx0) & (xx < fx1) & (yy >= fy0) & (yy < fy1)
        c[flag] = (200, 16, 46)
        fu = (xx - fx0) / (fx1 - fx0) * 8.5
        fv = (yy - fy0) / (fy1 - fy0) * 7.0
        c[flag & (((fu >= 3) & (fu < 4)) | ((fv >= 3) & (fv < 4)))] = (238, 238, 232)
        border = (xx >= fx0 - 3 * K) & (xx < fx1 + 3 * K) & (yy >= fy0 - 3 * K) & (yy < fy1 + 3 * K) & ~flag
        c[border] = (64, 60, 50)
        c[(yy > rh * 0.78) & (yy < rh * 0.86)] = (24, 24, 24)            # bungee
        c[(yy < 14 * K) & (xx < 14 * K)] = (172, 154, 120)
        return c
    fill(R_ADMIN, admin)

    def camo(xx, yy, rw, rh):
        c = multicam(np.c_[xx.ravel() * 0.35 / K, yy.ravel() * 0.35 / K, np.zeros(xx.size)]).reshape(rh, rw, 3)
        c[(yy > rh * 0.3) & (yy < rh * 0.33)] *= 0.5                       # flap line
        return c
    fill(R_CAMO, camo)
    fill(R_BLACK, lambda xx, yy, rw, rh: np.ones((rh, rw, 3)) * 22 + noise2(rw, rh, 0.2 / K, 33, 2)[..., None] * 12)
    fill(R_COYOTE, lambda xx, yy, rw, rh: np.ones((rh, rw, 3)) * np.array((124.0, 100.0, 68.0))
         * (0.88 + 0.24 * noise2(rw, rh, 0.15 / K, 34, 3))[..., None])

    # net drape: diamond scrim over a dark gap, burlap strips
    rx0, ry0, rx1, ry1 = R_NET
    rw, rh = rx1 - rx0, ry1 - ry0
    yy, xx = np.mgrid[0:rh, 0:rw]
    c = np.ones((rh, rw, 3)) * np.array((16.0, 18.0, 13.0))
    c *= (0.7 + 0.6 * noise2(rw, rh, 0.05 / K, 35, 3))[..., None]
    cell = 15.0 * K
    d1, d2 = ((xx + yy) / cell) % 1.0, ((xx - yy) / cell) % 1.0
    dist = np.minimum(np.minimum(d1, 1 - d1), np.minimum(d2, 1 - d2))
    string = dist < 0.09
    sc = np.array((74.0, 88.0, 58.0)) * (0.7 + 0.55 * noise2(rw, rh, 0.1 / K, 36, 3))[..., None]
    c[string] = sc[string]
    hn = np.where(string, 0.12, 0.0)
    for _ in range(60):                                                    # burlap strips tied into the net
        sx, sy = rng.integers(0, rw), rng.integers(0, rh)
        ln, wd = rng.integers(14 * K, 40 * K), rng.integers(4 * K, 8 * K)
        tone = np.array(((96, 92, 64), (70, 76, 50), (110, 98, 70), (54, 58, 40))[rng.integers(0, 4)], float)
        m = (np.abs(xx - sx) < wd / 2) & (yy >= sy) & (yy < sy + ln)
        c[m] = tone * (0.8 + 0.4 * noise2(rw, rh, 0.3 / K, 37, 2))[m][:, None]
        hn[m] = 0.18
    c[rh - 4 * K:, :] = c[rh - 4 * K:, :] * 0.6
    img[ry0:ry1, rx0:rx1] = c
    hmap[ry0:ry1, rx0:rx1] = hn


# ---------------------------------------------------------------- rasterising (UV space)
def raster(tris_uv, attrs, size):
    """Per texel the barycentric mix of per-vertex attrs over the triangles (uv in pixels); returns (attr image,
    filled mask, corner index image = the vertex with the largest weight)."""
    H, W = size
    out = np.zeros((H, W, attrs.shape[1]))
    filled = np.zeros((H, W), bool)
    near = np.full((H, W), -1, np.int64)
    for tri, (pa, pb, pc) in tris_uv:
        x0, x1 = int(max(0, math.floor(min(pa[0], pb[0], pc[0]) - 1))), int(min(W - 1, math.ceil(max(pa[0], pb[0], pc[0]) + 1)))
        y0, y1 = int(max(0, math.floor(min(pa[1], pb[1], pc[1]) - 1))), int(min(H - 1, math.ceil(max(pa[1], pb[1], pc[1]) + 1)))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(d) < 1e-9:
            continue
        l1 = ((pb[1] - pc[1]) * (xs - pc[0]) + (pc[0] - pb[0]) * (ys - pc[1])) / d
        l2 = ((pc[1] - pa[1]) * (xs - pc[0]) + (pa[0] - pc[0]) * (ys - pc[1])) / d
        l3 = 1 - l1 - l2
        m = (l1 >= -0.03) & (l2 >= -0.03) & (l3 >= -0.03)
        L = np.clip(np.stack([l1, l2, l3], -1), 0, None)
        L /= L.sum(-1, keepdims=True) + 1e-12
        a = attrs[list(tri)]
        val = L @ a
        sub = out[y0:y1 + 1, x0:x1 + 1]
        sub[m] = val[m]
        filled[y0:y1 + 1, x0:x1 + 1] |= m
        idx = np.array(tri)[np.argmax(L, -1)]
        near[y0:y1 + 1, x0:x1 + 1][m] = idx[m]
    return out, filled, near


def bleed(img, filled, n=6):
    img = img.copy()
    for _ in range(n):
        grow = img.copy()
        f2 = filled.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            sh = np.roll(np.roll(img, dy, 0), dx, 1)
            shf = np.roll(np.roll(filled, dy, 0), dx, 1)
            take = shf & ~f2
            grow[take] = sh[take]
            f2 |= take
        img, filled = grow, f2
    return img, filled


# ---------------------------------------------------------------- preview renderer (orthographic, z-buffer)
def render(V, T, UV, tex, yaw, size=(600, 900), NV=None):
    W, H = size
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    R = np.array(((c, 0, s), (0, 1, 0), (-s, 0, c)))
    Q = V @ R.T
    scale = H / 195.0
    sx = W / 2 + Q[:, 0] * scale
    sy = H - 10 - Q[:, 1] * scale
    zb = np.full((H, W), np.inf)
    out = np.full((H, W, 3), 235.0)
    th, tw = tex.shape[:2]
    light = np.array((0.4, 0.7, -0.6))
    light /= np.linalg.norm(light)
    for a, b, cc in T:
        xs_, ys_ = sx[[a, b, cc]], sy[[a, b, cc]]
        x0, x1 = int(max(0, xs_.min())), int(min(W - 1, xs_.max() + 1))
        y0, y1 = int(max(0, ys_.min())), int(min(H - 1, ys_.max() + 1))
        if x1 < x0 or y1 < y0:
            continue
        n = np.cross(Q[b] - Q[a], Q[cc] - Q[a])
        nl = np.linalg.norm(n)
        if nl < 1e-9:
            continue
        n /= nl
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (ys_[1] - ys_[2]) * (xs_[0] - xs_[2]) + (xs_[2] - xs_[1]) * (ys_[0] - ys_[2])
        if abs(d) < 1e-9:
            continue
        l1 = ((ys_[1] - ys_[2]) * (px - xs_[2]) + (xs_[2] - xs_[1]) * (py - ys_[2])) / d
        l2 = ((ys_[2] - ys_[0]) * (px - xs_[2]) + (xs_[0] - xs_[2]) * (py - ys_[2])) / d
        l3 = 1 - l1 - l2
        m = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        if not m.any():
            continue
        z = l1 * Q[a, 2] + l2 * Q[b, 2] + l3 * Q[cc, 2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        m &= z < sub
        if not m.any():
            continue
        u = l1 * UV[a, 0] + l2 * UV[b, 0] + l3 * UV[cc, 0]
        v = l1 * UV[a, 1] + l2 * UV[b, 1] + l3 * UV[cc, 1]
        col = tex[np.clip((v * th).astype(int), 0, th - 1), np.clip((u * tw).astype(int), 0, tw - 1)]
        if NV is not None:
            nn = l1[..., None] * NV[a] + l2[..., None] * NV[b] + l3[..., None] * NV[cc]
            nn = nn @ R.T
            nn /= np.linalg.norm(nn, axis=-1, keepdims=True) + 1e-9
            shade = 0.45 + 0.65 * np.abs(nn @ light)
        else:
            shade = 0.45 + 0.65 * abs(n @ light)
        sub[m] = z[m]
        out[y0:y1 + 1, x0:x1 + 1][m] = col[m] * (shade[m][:, None] if np.ndim(shade) else shade)
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- new arms, rolled sleeves, gloved hands
# Bradley's forearms are sleeves and his hands mittens (3 fingers in one strip) - both are replaced: his mesh is cut
# off inside the sleeve and these pieces are built along the bones. Ring angle 0 = top (outside when the arm hangs),
# +90 degrees = front (-z), as the tattoo canvas expects.
ARM_AX = (33.5, 38.0, 42.0, 46.0, 49.0, 53.0, 58.0, 63.0, 68.0, 72.0, 75.0, 77.5)
ARM_RY = (5.3, 5.2, 4.8, 4.0, 3.9, 4.35, 4.2, 3.75, 3.15, 2.7, 2.55, 2.5)     # half height (cm)
ARM_RZ = (5.4, 5.3, 4.9, 4.2, 4.3, 4.95, 4.8, 4.4, 3.95, 3.55, 3.45, 3.4)    # half depth (cm)
ARM_START, ARM_END, SLEEVE_END = 33.5, 77.5, 38.6
BONES_X = (19.8, 49.3, 75.0)
BONES_Y = (143.6 - 1.0, 143.0 - 1.0, 142.4 - 0.8)
BONES_Z = (6.0, 7.8, 7.5)


def arm_centre(side, ax):
    return np.array((side * ax, np.interp(ax, BONES_X, BONES_Y), np.interp(ax, BONES_X, BONES_Z)))


def arm_radii(ax):
    return np.interp(ax, ARM_AX, ARM_RY), np.interp(ax, ARM_AX, ARM_RZ)


def ring_pts(c, a, b, ra, rb, n, p=2.0, r_of=None):
    th = 2 * math.pi * np.arange(n) / n
    if r_of is not None:
        r = r_of(th)
        return c + np.outer(r * np.cos(th), a) + np.outer(r * np.sin(th), b)
    co, si = np.cos(th), np.sin(th)
    k = (np.abs(co / ra) ** p + np.abs(si / rb) ** p) ** (-1.0 / p)    # superellipse radius
    return c + np.outer(k * co, a) + np.outer(k * si, b)


def arm_weights(side, ax):
    L = "L" if side > 0 else "R"
    fore = float(sstep(44.0, 53.0, ax))
    hand = float(sstep(70.5, 76.0, ax))
    w = {"BIP01 %s UPPERARM" % L: 1 - fore, "BIP01 %s FOREARM" % L: fore * (1 - hand), "BIP01 %s HAND" % L: fore * hand}
    return {k: v for k, v in w.items() if v > 1e-4}


def loft(kit, rings, centres, reg, wts, outs, cap_end=None, cap_start=False, close_end=False):
    """rings: [(n, 3) points] closed loops; wts: per ring weights dict; outs: per segment 'out' / 'in' / a vector."""
    n = len(rings[0])
    ids = []
    for i, r in enumerate(rings):
        ids.append([kit.v(r[k % n], region_uv(reg, k / n, i / (len(rings) - 1)), wts[i]) for k in range(n + 1)])
    tris = []                                   # one consistent winding for the whole piece (Kit.piece)
    for i in range(len(rings) - 1):
        for k in range(n):
            a, b, c, d = ids[i][k], ids[i][k + 1], ids[i + 1][k + 1], ids[i + 1][k]
            radial = (kit.P[a] + kit.P[c]) / 2 - (centres[i] + centres[i + 1]) / 2
            o = outs[i]
            if isinstance(o, str):
                out = radial if o == "out" else -radial
            else:
                out = np.asarray(o, float)
            tris += [((a, b, c), out), ((a, c, d), out)]
    if cap_end is not None:
        tip = kit.v(cap_end, region_uv(reg, 0.5, 1.0), wts[-1])
        t = cap_end - centres[-1]
        tris += [((ids[-1][k], ids[-1][k + 1], tip), t) for k in range(n)]
    # flat caps: every piece closed, so the game's stencil shadow volumes stay closed too. A cap's triangles run its
    # ring backwards relative to the side quads next to them, so their vote is taken on their own and checked against
    # the side: the ring edge must be used once in each direction.
    piece_tris = list(tris)
    kit.piece(piece_tris)
    side = {(t_[0], t_[1]) for t_ in kit.T[-len(piece_tris):]} | {(t_[1], t_[2]) for t_ in kit.T[-len(piece_tris):]} |         {(t_[2], t_[0]) for t_ in kit.T[-len(piece_tris):]}

    def cap(ring, centre, uv_t):
        c = kit.v(centre, region_uv(reg, 0.5, uv_t), wts[0] if uv_t == 0.0 else wts[-1])
        # the side uses edge (ring[k], ring[k+1]) one way; the cap must use it the other way
        fwd = (ring[0], ring[1]) in side
        for k in range(n):
            kit.T.append((ring[k + 1], ring[k], c) if fwd else (ring[k], ring[k + 1], c))
    if cap_start:
        cap(ids[0], centres[0], 0.0)
    if close_end:
        cap(ids[-1], centres[-1], 1.0)


def build_arm(kit, side, Vold, cat_old):
    up, fwd = np.array((0, 1.0, 0)), np.array((0, 0, -1.0))
    # the bare arm: inside the sleeve to inside the glove
    axs = np.arange(ARM_START, ARM_END + 1e-6, 0.5)
    rings, cs, ws = [], [], []
    for ax in axs:
        c = arm_centre(side, ax)
        ry, rz = arm_radii(ax)
        bulge = 1 + 0.05 * math.exp(-((ax - 54.0) / 5.0) ** 2)
        rings.append(ring_pts(c, up, fwd, ry * bulge, rz * bulge, 32, 2.2))
        cs.append(c)
        ws.append(arm_weights(side, ax))
    loft(kit, rings, cs, ARM_RECT[side], ws, ["out"] * len(rings), cap_start=True, close_end=True)
    # rolled-up sleeve: round the end of Bradley's sleeve (measured per angle), folded back over itself
    m = (cat_old == 2) & (np.sign(Vold[:, 0]) == side) & (np.abs(Vold[:, 0]) > 34.5) & (np.abs(Vold[:, 0]) < SLEEVE_END)
    c0 = arm_centre(side, 36.5)
    rel = Vold[m] - c0
    th_v = np.arctan2(-rel[:, 2], rel[:, 1]) % (2 * math.pi)
    rad_v = np.hypot(rel[:, 1], rel[:, 2])
    nb = 32
    rs = np.full(nb, np.nan)
    for k in range(nb):
        d = np.abs((th_v - 2 * math.pi * k / nb + math.pi) % (2 * math.pi) - math.pi)
        sel = d < 0.25
        if sel.any():
            rs[k] = rad_v[sel].max()
    ok = ~np.isnan(rs)
    rs = np.interp(np.arange(nb), np.arange(nb)[ok], rs[ok], period=nb)
    rs = (np.roll(rs, 1) + 2 * rs + np.roll(rs, -1)) / 4
    rs = np.maximum(rs, 5.9)

    def sleeve_r(off):
        return lambda th: np.interp(th, 2 * math.pi * np.arange(nb + 1) / nb, np.r_[rs, rs[0]]) + off

    def arm_r(ax, off):
        ry, rz = arm_radii(ax)
        return lambda th: ((np.cos(th) / (ry + off)) ** 2 + (np.sin(th) / (rz + off)) ** 2) ** -0.5
    prof = [(34.4, sleeve_r(0.1)), (35.0, sleeve_r(0.55)), (35.9, sleeve_r(0.95)), (37.0, sleeve_r(1.15)),
            (38.1, sleeve_r(1.05)), (38.9, sleeve_r(0.7)), (39.3, sleeve_r(0.25)), (39.35, arm_r(39.3, 1.1)),
            (39.0, arm_r(39.0, 0.35)), (38.3, arm_r(38.3, 0.05))]
    rings, cs, ws = [], [], []
    for ax, rf in prof:
        c = arm_centre(side, ax)
        rings.append(ring_pts(c, up, fwd, 1, 1, 48, r_of=rf))
        cs.append(c)
        ws.append(arm_weights(side, ax))
    outs = ["out"] * 6 + [np.array((side, 0.0, 0.0))] + ["in"] * 2
    loft(kit, rings, cs, CUFF_RECT[side], ws, outs, cap_start=True, close_end=True)
    build_hand(kit, side)


# finger: (base x, base z, base y, radius, lengths, curls right hand, curls left hand) in hand space (cm, degrees)
FINGERS = [(8.6, 2.85, -0.25, 1.13, (4.4, 2.6, 2.1), (35, 50, 25), (40, 55, 30)),
           (8.7, 0.95, -0.2, 1.16, (4.8, 3.0, 2.2), (65, 85, 45), (45, 60, 30)),
           (8.3, -0.92, -0.25, 1.09, (4.5, 2.8, 2.1), (68, 85, 45), (50, 62, 30)),
           (7.7, -2.7, -0.35, 0.99, (3.6, 2.1, 1.9), (72, 85, 40), (55, 62, 30))]
THUMB = ((1.2, -0.75, 3.1), 1.28, (3.6, 3.1, 2.5), (20, 35, 25), (10, 25, 15))
PALM_X = (-1.6, 9.4)


def curl_chain(base, d0, a0, lengths, curls, r0, step=0.3):
    """Points, frames and radii along a finger: bends (degrees) at 0.8 cm, +l1, +l1+l2, each spread over 0.7 cm."""
    joints = np.cumsum((0.8,) + tuple(lengths[:2]))
    total = 0.8 + sum(lengths)
    pts, frames, radii = [], [], []
    p = np.array(base, float)
    s = 0.0
    while True:
        phi = sum(math.radians(cu) * float(sstep(j - 0.55, j + 0.55, s)) for j, cu in zip(joints, curls))
        d = d0 * math.cos(phi) - a0 * math.sin(phi)
        a = a0 * math.cos(phi) + d0 * math.sin(phi)
        r = r0 * (1 - 0.18 * s / total)
        end = total - s
        if end < 0.75:
            r *= math.sqrt(max(0.0, 1 - ((0.75 - end) / 0.8) ** 2)) * 0.85 + 0.15 * (end / 0.75)
        pts.append(p.copy())
        frames.append((a, np.cross(d, a)))
        radii.append(max(r, 0.08))
        if s >= total - 1e-6:
            break
        ds = min(step, total - s)
        p = p + d * ds
        s += ds
    return pts, frames, radii, d


def build_hand(kit, side):
    L = "L" if side > 0 else "R"
    hand_w = {"BIP01 %s HAND" % L: 1.0}
    wrist = arm_centre(side, 75.0)
    Lx, Ly, Lz = np.array((side, 0.0, 0.0)), np.array((0, 1.0, 0)), np.array((0, 0, -1.0))   # along, back, thumb side

    def H(x, y, z):
        return wrist + Lx * x + Ly * y + Lz * z
    reg = HAND_RECT[side]
    # palm: a flattened box-ish loft from inside the glove cuff to the knuckles
    xs = np.arange(PALM_X[0], PALM_X[1] + 1e-6, 0.4)
    rings, cs, ws = [], [], []
    for x in xs:
        t = (x - PALM_X[0]) / (PALM_X[1] - PALM_X[0])
        ry = 1.45 + 0.5 * math.sin(min(1.0, t * 1.4) * math.pi / 2)
        rz = 3.1 + 1.45 * math.sin(min(1.0, t * 1.5) * math.pi / 2)
        end = PALM_X[1] - x
        if end < 1.0:
            f = math.sqrt(max(0.05, 1 - ((1.0 - end) / 1.05) ** 2))
            ry, rz = ry * f, rz * (0.6 + 0.4 * f)
        c = H(x, -0.25 * max(0.0, x) / 9.4, 0.15)
        rings.append(ring_pts(c, Ly, Lz, ry, rz, 28, 2.8))
        cs.append(c)
        ws.append(hand_w)
    loft(kit, rings, cs, sub_region(reg, 0, 0, 1, 0.42), ws, ["out"] * len(rings), cap_end=cs[-1] + Lx * 0.15,
         cap_start=True)
    # fingers + thumb
    for k, (bx, bz, by, r0, lens, cur_r, cur_l) in enumerate(FINGERS):
        pts, frames, radii, d = curl_chain(H(bx, by, bz), Lx, Ly, lens, cur_r if side < 0 else cur_l, r0)
        rings = [ring_pts(p, a, b, r, r * 1.08, 14, 2.2) for p, (a, b), r in zip(pts, frames, radii)]
        loft(kit, rings, pts, sub_region(reg, k / 5, 0.42, (k + 1) / 5, 0.84), [hand_w] * len(rings),
             ["out"] * len(rings), cap_end=pts[-1] + d * 0.12, cap_start=True)
    (tx, ty, tz), r0, lens, cur_r, cur_l = THUMB
    d0 = Lx * 0.62 + Lz * 0.66 - Ly * 0.42
    d0 /= np.linalg.norm(d0)
    a0 = Ly + Lz * 0.35
    a0 = a0 - d0 * (a0 @ d0)
    a0 /= np.linalg.norm(a0)
    pts, frames, radii, d = curl_chain(H(tx, ty, tz), d0, a0, lens, cur_r if side < 0 else cur_l, r0)
    rings = [ring_pts(p, a, b, r, r * 1.1, 14, 2.2) for p, (a, b), r in zip(pts, frames, radii)]
    loft(kit, rings, pts, sub_region(reg, 0.8, 0.42, 1.0, 0.84), [hand_w] * len(rings), ["out"] * len(rings),
         cap_end=pts[-1] + d * 0.12, cap_start=True)
    # gauntlet cuff over the wrist
    prof = [(71.6, 0.05), (72.1, 0.5), (73.5, 0.62), (75.5, 0.62), (77.1, 0.55), (77.6, 0.3), (77.7, -0.4)]
    rings, cs, ws = [], [], []
    for ax, off in prof:
        c = arm_centre(side, ax)
        ry, rz = arm_radii(ax)
        if ax > 75.0:                                   # wide enough for the palm under it
            x = ax - 75.0
            ry = max(ry, 1.65 + 0.1 * x)
            rz = max(rz, 3.4 + 0.25 * x)
        rings.append(ring_pts(c, Ly, Lz, ry + off, rz + off, 32, 2.4))
        cs.append(c)
        ws.append(arm_weights(side, ax))
    loft(kit, rings, cs, sub_region(reg, 0, 0.86, 1, 1.0), ws, ["out"] * 5 + [Lx], cap_start=True, close_end=True)


def paint_new_parts(img, hmap):
    """The new arms / cuffs / hands, painted in their own parameter space (u = round, v = along)."""
    for side in (-1, 1):
        # arm skin + tattoos
        x0, y0, x1, y1 = ARM_RECT[side]
        w, h = x1 - x0, y1 - y0
        ys, xs = np.mgrid[0:h, 0:w]
        su = np.clip((xs + 0.5 - 1.5) / (w - 3), 0, 1)
        tv = np.clip((ys + 0.5 - 1.5) / (h - 3), 0, 1)
        th = su * 2 * math.pi
        ax = ARM_START + tv * (ARM_END - ARM_START)
        ry, rz = arm_radii(ax)
        P = np.c_[(side * ax).ravel(), (142 + ry * np.cos(th)).ravel(), (7 - rz * np.sin(th)).ravel()]
        sk = np.array((200.0, 142.0, 112.0)) * (0.9 + 0.16 * fbm(P, 0.6, 9, 3))[:, None]
        ang = ((th + math.pi) % (2 * math.pi) - math.pi).ravel()
        hair = (vnoise(P, 6.0, 10) > 0.78) & (np.abs(ang) < 1.6)
        sk[hair] *= 0.8
        sk[vnoise(P, 3.0, 11) > 0.82] *= np.array((0.86, 0.78, 0.74))
        if side not in TATTOOS:
            TATTOOS[side] = tattoo_canvas(side)
        can = TATTOOS[side]
        cx = np.clip(((ang * 5.6 - TAT_U[0]) * TAT_PX).astype(int), 0, can.shape[1] - 1)
        cy = np.clip(((ax.ravel() - TAT_V[0]) * TAT_PX).astype(int), 0, can.shape[0] - 1)
        a = np.clip(can[cy, cx] * 1.25, 0, 0.95)
        sk = sk * (1 - a[:, None]) + np.array((40.0, 46.0, 60.0)) * a[:, None]
        sk *= (0.86 + 0.14 * (np.cos(th.ravel()) * 0.5 + 0.5))[:, None]
        img[y0:y1, x0:x1] = sk.reshape(h, w, 3)
        hmap[y0:y1, x0:x1] = 0.0
        # rolled sleeve: shirt cloth, folds round the roll, darker inside
        x0, y0, x1, y1 = CUFF_RECT[side]
        w, h = x1 - x0, y1 - y0
        ys, xs = np.mgrid[0:h, 0:w]
        v = ys / (h - 1)
        P = np.c_[xs.ravel() * 0.05, ys.ravel() * 0.05, np.full(w * h, side * 5.0)]
        c = (np.array((40.0, 40.0, 42.0)) * (0.82 + 0.36 * fbm(P, 2.0, 50, 3))[:, None]).reshape(h, w, 3)
        c *= (0.7 + 0.45 * np.sin(np.clip(v / 0.66, 0, 1) * math.pi))[..., None]
        crease = (np.abs(np.sin(xs * 0.11 + 3 * np.sin(xs * 0.023))) < 0.08) & (v < 0.66)
        c[crease] *= 0.6
        c[v > 0.66] *= 0.45
        fray = (v > 0.6) & (v < 0.7) & (noise2(w, h, 0.3, 51, 2) > 0.6)
        c[fray] = (92, 90, 86)
        img[y0:y1, x0:x1] = c
        hmap[y0:y1, x0:x1] = np.where(crease, -0.1, 0.0)
        # glove
        x0, y0, x1, y1 = HAND_RECT[side]
        w, h = x1 - x0, y1 - y0
        ys, xs = np.mgrid[0:h, 0:w]
        P = np.c_[xs.ravel() * 0.04, ys.ravel() * 0.04, np.full(w * h, side * 9.0)]
        tan = (np.array((152.0, 108.0, 72.0)) * (0.84 + 0.28 * fbm(P, 1.0, 52, 3))[:, None]).reshape(h, w, 3)
        hm = np.zeros((h, w))
        vt = ys / (h - 1)
        # palm piece (v 0..0.42): u round from the back of the hand, along = wrist -> knuckles
        pv = vt / 0.42
        pu = xs / (w - 1) * 2 * math.pi
        back = (np.cos(pu) > 0.35) & (pv < 1.0)
        plate = back & (pv > 0.42) & (pv < 0.97)
        tan[plate] = np.array((40.0, 38.0, 36.0)) * (0.8 + 0.3 * noise2(w, h, 0.05, 53, 2)[plate])[:, None]
        ridge = plate & (np.abs(((pv - 0.42) / 0.11) % 1.0 - 0.5) < 0.08)
        tan[ridge] *= 0.6
        hm[plate] = 0.25
        palm = (np.cos(pu) < -0.4) & (pv < 1.0)
        tan[palm] = np.array((172.0, 132.0, 96.0)) * (0.88 + 0.2 * noise2(w, h, 0.06, 54, 2)[palm])[:, None]
        grip = palm & (np.cos(pu) < -0.75) & (pv > 0.35) & (pv < 0.9) & (noise2(w, h, 0.25, 55, 1) > 0.45)
        tan[grip] *= 0.55
        # fingers (v 0.42..0.84, 5 strips): u round from the finger's back, v base -> tip
        fv = (vt - 0.42) / 0.42
        fu = ((xs / (w - 1)) * 5 % 1.0) * 2 * math.pi
        fing = (fv >= 0) & (fv <= 1)
        knuckle = fing & (np.cos(fu) > 0.45) & (fv > 0.05) & (fv < 0.42)
        tan[knuckle] = np.array((44.0, 42.0, 40.0)) * (0.8 + 0.3 * noise2(w, h, 0.08, 56, 2)[knuckle])[:, None]
        hm[knuckle] = 0.2
        tan[fing & (fv > 0.85)] *= 0.72
        tan[fing & (np.abs(np.sin(fu)) > 0.97)] *= 0.7
        tan[fing & (np.cos(fu) < -0.4)] *= np.array((1.08, 1.12, 1.15))
        # gauntlet cuff (v 0.86..1): dark velcro, tan binding at the edges
        gc = vt >= 0.86
        gv = (vt - 0.86) / 0.14
        tan[gc] = np.array((50.0, 46.0, 42.0)) * (0.8 + 0.4 * noise2(w, h, 0.2, 57, 2)[gc])[:, None]
        tan[gc & ((gv < 0.12) | (gv > 0.78))] = (120, 88, 60)
        hm[gc] = 0.1
        sh = np.where(vt < 0.42, 0.86 + 0.14 * (np.cos(pu) * 0.5 + 0.5),
                      np.where(vt < 0.84, 0.86 + 0.14 * (np.cos(fu) * 0.5 + 0.5), 1.0))
        img[y0:y1, x0:x1] = np.clip(tan * sh[..., None], 0, 255)
        hmap[y0:y1, x0:x1] = hm


# ---------------------------------------------------------------- main
def main():
    def _default_game():
        """The installed Desert Storm (env CONFLICT_DS_GAME, else registry / Steam libraries / usual paths)."""
        kit = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        if kit not in sys.path:
            sys.path.insert(0, kit)
        from conflict_ds_tools import games
        return os.environ.get("CONFLICT_DS_GAME") or games.find_game_dir(games.GAMES["ds1"])


    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--game", default=_default_game())
    ap.add_argument("--install", action="store_true", help="copy the mod to <game>\\Mods\\DanishFrogman")
    ap.add_argument("--preview", action="store_true", help="render front / back / side pictures to <out>\\preview")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    data = read_game_file(args.game, "HERO01_ARMSTRONG.EVO")
    scene = evo.Scene(evo.read(data))
    objs = scene.objects
    names = {o.index: o.node.name for o in objs}
    skin = next(o for o in objs if o.node.kind == 2)
    mesh = skin.node.meshes[0]
    V = np.array(mesh.positions, float)
    UV = np.array(mesh.uvs, float)
    T = np.array(mesh.triangles, int)
    W = [{names[b]: w for b, w in wl} for wl in scene.vertex_weights(skin, mesh)]
    world = {o.node.name: np.array(o.world, float) for o in objs}

    # the game's winding: cross(b - a, c - a) against "outward" (away from the vertex's main bone)
    dom = [max(w.items(), key=lambda kv: kv[1])[0] for w in W]
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    outw = V[T].mean(1) - np.array([world[dom[a]][:3, 3] for a, _b, _c in T])
    sign = 1.0 if (np.sign((fn * outw).sum(1))).mean() > 0 else -1.0
    print("winding: cross product points", "outward" if sign > 0 else "inward")

    # ---- head sculpt on the original vertices: a hood over a helmet (fuller, rounder, no ears, no nose)
    hw = np.array([w.get("BIP01 HEAD", 0.0) for w in W])
    hv = hw > 0.5
    c = (V[hv].min(0) + V[hv].max(0)) / 2
    rel = V - c
    half_w = np.percentile(np.abs(rel[hv, 0]), 78)
    face = np.percentile(-rel[hv, 2], 82)
    new = rel.copy()
    ear = np.abs(new[:, 0]) > half_w
    new[ear, 0] -= (new[ear, 0] - np.sign(new[ear, 0]) * half_w) * 0.9
    nose = (-new[:, 2] > face) & (np.abs(new[:, 0]) < 0.35 * half_w)
    new[nose, 2] += (-new[nose, 2] - face) * 0.85
    new *= np.array((1.1, 1.05, 1.1))
    new[:, 1] += np.clip(rel[:, 1], 0, None) * 0.05                        # helmet crown
    V = V + hw[:, None] * (c + new - V)
    nw = np.array([w.get("BIP01 NECK", 0.0) for w in W])
    V[:, [0, 2]] += nw[:, None] * (V[:, [0, 2]] - np.array((0.0, 4.0))) * 0.06

    # ---- body mesh: subdivide twice, smooth, taper the bare forearms, then displace by the painted geometry heights
    Vs, UVs, Ws, Ts = V, UV, W, T
    for _ in range(2):
        Vs, UVs, Ws, Ts = subdivide(Vs, UVs, Ws, Ts)
    Ws = [top4(w) for w in Ws]
    dom_s = [max(w.items(), key=lambda kv: kv[1])[0] for w in Ws]
    cat_s = classify(dom_s)
    Vs = taubin(Vs, Ts, iters=10)
    # Bradley's forearms (sleeves) and hands (mittens) go: new arms / cuffs / hands are built in build_arm
    for side in (-1, 1):
        m = (cat_s == 2) & (np.sign(Vs[:, 0]) == side)
        ax = np.abs(Vs[m, 0])
        cy = np.interp(ax, BONES_X, BONES_Y)
        cz = np.interp(ax, BONES_X, BONES_Z)
        dy, dz = Vs[m, 1] - cy, Vs[m, 2] - cz
        r = np.hypot(dy, dz) + 1e-9
        th = np.arctan2(-dz, dy)
        ry, rz = arm_radii(np.maximum(ax, ARM_START))
        ry, rz = ry + 1.7, rz + 1.7                     # a loose sleeve over the new arm
        target = ((np.cos(th) / ry) ** 2 + (np.sin(th) / rz) ** 2) ** -0.5
        w = sstep(26.0, 33.0, ax)
        f = 1 - w * (1 - np.minimum(1.0, target / r))
        Vs[m, 1] = cy + dy * f
        Vs[m, 2] = cz + dz * f
    Vs = taubin(Vs, Ts, iters=3)
    Vold, cat_old = Vs.copy(), cat_s.copy()
    cen = Vs[Ts].mean(1)
    tcat = cat_s[Ts].max(1)
    cut = (tcat == 2) & (np.abs(cen[:, 0]) > SLEEVE_END)
    Ts = Ts[~cut]
    used = np.unique(Ts)
    remap = np.full(len(Vs), -1)
    remap[used] = np.arange(len(used))
    Ts = remap[Ts]
    Vs, UVs, cat_s = Vs[used], UVs[used], cat_s[used]
    Ws = [Ws[i] for i in used]
    dom_s = [dom_s[i] for i in used]
    Ns = vertex_normals(Vs, Ts)
    _c, _ht, hg = paint_body(Vs, Ns, cat_s)
    Vbase = Vs
    Vs = Vs + Ns * hg[:, None]
    Vs = taubin(Vs, Ts, iters=2, lam=0.35, mu=-0.37)                       # soften the displacement steps
    print("body: %d -> %d vertices, %d triangles" % (len(V), len(Vs), len(Ts)))

    # ---- body texture, painted per texel on the final (smoothed, tapered) surface before displacement
    bh = bw = int(TS * BODY_S)
    tuv = [(tuple(t), (UVs[t[0]] * (bw, bh), UVs[t[1]] * (bw, bh), UVs[t[2]] * (bw, bh))) for t in Ts]
    A, filled, near = raster(tuv, np.c_[Vbase, Ns], (bh, bw))
    sel = filled.ravel()
    Pt, Nt = A.reshape(-1, 6)[sel, :3], A.reshape(-1, 6)[sel, 3:]
    Nt /= np.linalg.norm(Nt, axis=1, keepdims=True) + 1e-9
    cats = cat_s[near.ravel()[sel]]
    col, htex, _hg = paint_body(Pt, Nt, cats)
    body = np.zeros((bh * bw, 3))
    body[sel] = col
    body_h = np.zeros(bh * bw)
    body_h[sel] = htex
    body, f2 = bleed(body.reshape(bh, bw, 3), filled)
    body_h, _ = bleed(body_h.reshape(bh, bw, 1), filled)
    body[~f2] = (30, 30, 32)
    tex = np.zeros((TS, TS, 3))
    hmap = np.zeros((TS, TS))
    tex[:bh, :bw] = body
    hmap[:bh, :bw] = body_h[..., 0]
    paint_atlas(tex, hmap)
    paint_new_parts(tex, hmap)
    UVs = UVs * BODY_S

    # ---- kit
    kit = Kit(sign)
    ray = Ray(Vs, Ts)
    for side in (-1, 1):
        build_arm(kit, side, Vold, cat_old)

    def front_z(x, y):
        t = ray.hit((x, y, -80.0), (0, 0, 1.0))
        return -80.0 + t if t is not None else None

    # chest rig: a heightfield on the chest, pouches raised, walls back into the body
    gx = np.arange(RIG_X[0], RIG_X[1] + 1e-6, 0.5)
    gy = np.arange(RIG_Y[0], RIG_Y[1] + 1e-6, 0.5)
    zs = np.array([[front_z(x, y) for x in gx] for y in gy], dtype=object)
    zs = np.array([[z if z is not None else np.nan for z in row] for row in zs], float)
    if np.isnan(zs).any():                                   # fill holes from neighbours
        zs = np.where(np.isnan(zs), np.nanmin(zs), zs)
    D = rig_depth(*np.meshgrid(gx, gy))
    ids = np.zeros(zs.shape, int)
    for j, y in enumerate(gy):
        for i, x in enumerate(gx):
            s = (x - RIG_X[0]) / (RIG_X[1] - RIG_X[0])
            t = (RIG_Y[1] - y) / (RIG_Y[1] - RIG_Y[0])
            ids[j, i] = kit.v((x, y, zs[j, i] - 0.25 - D[j, i]), region_uv(R_RIG, s, t))
    for j in range(len(gy) - 1):
        for i in range(len(gx) - 1):
            kit.quad(ids[j, i], ids[j, i + 1], ids[j + 1, i + 1], ids[j + 1, i], out=(0, 0, -1.0))
    ring = [(j, 0) for j in range(len(gy))] + [(len(gy) - 1, i) for i in range(1, len(gx))] + \
        [(j, len(gx) - 1) for j in range(len(gy) - 2, -1, -1)] + [(0, i) for i in range(len(gx) - 2, 0, -1)]
    backs = []
    for j, i in ring:
        p = kit.P[ids[j, i]].copy()
        p[2] = zs[j, i] + 0.4
        backs.append(kit.v(p, kit.UV[ids[j, i]]))
    ctr = np.array((0.0, (RIG_Y[0] + RIG_Y[1]) / 2, 0.0))
    for k in range(len(ring)):
        a, b = ids[ring[k]], ids[ring[(k + 1) % len(ring)]]
        o = (kit.P[a] + kit.P[b]) / 2 - ctr
        o[2] = 0
        kit.quad(a, b, backs[(k + 1) % len(ring)], backs[k], out=o)

    rc = kit.v((0.0, (RIG_Y[0] + RIG_Y[1]) / 2, float(np.nanmean(zs)) + 0.4), kit.UV[backs[0]])
    for k in range(len(backs)):                                         # back of the rig (inside the chest)
        kit.tri(backs[k], backs[(k + 1) % len(backs)], rc, out=(0, 0, 1.0))

    def rig_front(x, y):
        return front_z(x, y) - 0.25 - float(rig_depth(x, y))

    # radios standing in their pouches + antennas, admin panel on top
    for cx in RADIOS:
        zf = rig_front(cx, 124.0)
        kit.box((cx - 2.5, 125.0, zf + 0.6), (cx + 2.5, 141.5, zf + 4.0), R_RADIO, sub_region(R_RADIO, 0, 0, 0.2, 0.15),
                rigid="BIP01 SPINE3")
        kit.tube([(cx + 1.2, 141.4, zf + 2.0), (cx + 1.2, 143.2, zf + 2.0)], 0.7, R_BLACK, "BIP01 SPINE3")   # knob
    zr = rig_front(RADIOS[0], 124.0) + 2.6
    kit.tube([(RADIOS[0] - 1.0, 141.0, zr), (RADIOS[0] - 1.6, 160.0, zr - 0.6), (RADIOS[0] - 2.4, 178.0, zr - 0.8),
              (RADIOS[0] - 3.0, 190.0, zr - 0.6)], 0.42, R_BLACK, "BIP01 SPINE3")
    zl = rig_front(RADIOS[1], 124.0) + 2.6
    kit.tube([(RADIOS[1] - 1.0, 141.0, zl), (RADIOS[1] - 1.5, 150.0, zl + 1.0), (RADIOS[1] - 0.5, 157.0, zl + 6.0),
              (RADIOS[1] + 2.0, 162.0, zl + 14.0), (RADIOS[1] + 5.0, 172.0, zl + 18.0)], 0.38, R_BLACK, "BIP01 SPINE3")
    za = front_z(0.0, 135.0)
    kit.box((-6.5, 131.0, za - 3.2), (6.5, 139.5, za + 0.5), R_ADMIN, sub_region(R_ADMIN, 0, 0, 0.17, 0.13),
            rigid="BIP01 SPINE3")

    # hip pouches on the belt + a thigh holster
    for cx, wd in ((14.0, 2.4), (10.8, 2.4)):                               # double pistol-mag pouch, wearer's left
        zf = front_z(cx, 88.0)
        kit.box((cx - wd / 2, 81.0, zf - 2.6), (cx + wd / 2, 93.2, zf + 0.6), R_CAMO)
    zf = front_z(-14.0, 89.0)
    kit.box((-17.5, 80.5, zf - 3.4), (-10.5, 92.8, zf + 0.6), R_CAMO)       # utility pouch, wearer's right
    t = ray.hit((-80.0, 72.0, 0.0), (1.0, 0, 0))
    xo = -80.0 + t if t is not None else -26.0
    kit.box((xo - 3.4, 60.0, -5.0), (xo + 0.6, 84.0, 4.0), R_CAMO)          # holster on the right thigh
    kit.box((xo - 2.8, 84.0, -4.4), (xo - 0.2, 89.5, -0.6), R_BLACK)        # pistol grip
    kit.box((xo - 1.8, 84.0, 0.0), (xo + 0.2, 94.0, 1.6), R_COYOTE)         # drop-leg strap up to the belt

    # scrim-net drape: a curtain round the head and neck that never goes inwards on the way down, over the shoulders
    axis = np.array((0.0, 0.0, 2.0))
    NA, top_y = 56, 167.0
    thetas = np.linspace(0, 2 * math.pi, NA + 1)
    rows = 22
    no_arm = (classify(dom_s) != 2) & (np.abs(Vs[:, 0]) < 19.0)      # T-pose arms would pull the curtain out
    rv = Vs[no_arm] - axis
    vy = Vs[no_arm, 1]
    rv_th = np.arctan2(rv[:, 0], -rv[:, 2])
    rv_r = np.hypot(rv[:, 0], rv[:, 2])
    grid = np.zeros((rows + 1, NA + 1), int)
    inner = np.zeros((rows + 1, NA + 1), int)
    rmax = np.zeros(NA + 1)
    rag = np.random.default_rng(3)
    bot_noise = rag.normal(0, 1.4, NA + 1)
    bot_noise[-1] = bot_noise[0]
    for j in range(rows + 1):
        for i, th in enumerate(thetas):
            ybot = 150.0 - 13.0 * math.cos(th) ** 2 + bot_noise[i]
            y = top_y + (ybot - top_y) * j / rows
            dth = np.abs((rv_th - th + math.pi) % (2 * math.pi) - math.pi)
            m = (np.abs(vy - y) < 1.4) & (dth < 0.14)
            rb = rv_r[m].max() if m.any() else 0.0
            if j == 0:
                rmax[i] = 0.0
            flare = max(0.0, 156.0 - y) * 0.12
            r = min(max(rb + 0.9, rmax[i] - 0.15) + flare, 21.0)
            rmax[i] = max(rmax[i], r - flare)
            p = axis + np.array((math.sin(th) * r, y, -math.cos(th) * r))
            p[1] = y
            uv = region_uv(R_NET, i / NA, j / rows)
            grid[j, i] = kit.v(p, uv, rigid=("drape", y, p[0]))
            pin = axis + np.array((math.sin(th) * (r - 0.18), y, -math.cos(th) * (r - 0.18)))
            pin[1] = y
            inner[j, i] = kit.v(pin, uv, rigid=("drape", y, p[0]))
    for j in range(rows):
        for i in range(NA):
            o = kit.P[grid[j, i]] - axis
            o[1] = 0
            kit.quad(grid[j, i], grid[j, i + 1], grid[j + 1, i + 1], grid[j + 1, i], out=o)
            kit.quad(inner[j, i], inner[j, i + 1], inner[j + 1, i + 1], inner[j + 1, i], out=-o)
    for j, up in ((0, (0, 1.0, 0)), (rows, (0, -1.0, 0))):              # top and bottom rims: the two sheets joined
        for i in range(NA):
            kit.quad(grid[j, i], grid[j, i + 1], inner[j, i + 1], inner[j, i], out=up)

    # kit weights: rigid bone, drape rule, else the nearest body vertex's
    KP = np.array(kit.P)
    kw = []
    need = [k for k, r in enumerate(kit.rigid) if r is None]
    nearest = {}
    for s0 in range(0, len(need), 256):
        chunk = need[s0:s0 + 256]
        d = ((KP[chunk][:, None, :] - Vs[None, :, :]) ** 2).sum(-1)
        for k, idx in zip(chunk, d.argmin(1)):
            nearest[k] = idx
    for k, r in enumerate(kit.rigid):
        if r is None:
            kw.append(Ws[nearest[k]])
        elif isinstance(r, dict):
            kw.append(r)
        elif isinstance(r, tuple):
            _tag, y, x = r
            a = float(np.clip((y - 149.0) / 9.0, 0, 1))
            w = {"BIP01 HEAD": a}
            rest = 1 - a
            if rest > 0:
                sh = float(np.clip((abs(x) - 12.0) / 6.0, 0, 1)) * 0.5
                w["BIP01 SPINE3"] = rest * (1 - sh)
                if sh > 0:
                    w["BIP01 %s CLAVICLE" % ("L" if x > 0 else "R")] = rest * sh
            kw.append({b: v for b, v in w.items() if v > 1e-4})
        else:
            kw.append({r: 1.0})
    KN = vertex_normals(KP, np.array(kit.T)) if kit.T else np.zeros_like(KP)
    print("kit: %d vertices, %d triangles" % (len(KP), len(kit.T)))

    # ---- write texture, normal map, model
    tex_u8 = np.clip(tex, 0, 255).astype(np.uint8)
    rgba = np.dstack([tex_u8, np.full((TS, TS), 255, np.uint8)])
    open(os.path.join(args.out, TEX + ".dds"), "wb").write(images.encode_dds(TS, TS, rgba.tobytes(), fmt="DXT1"))
    Image.fromarray(tex_u8).save(os.path.join(args.out, TEX + "_texture.png"))
    NMS = 1024
    hs = Image.fromarray(hmap.astype(np.float32), mode="F").resize((NMS, NMS), Image.BOX)
    hh = np.asarray(hs, float) / 0.06           # heights in texels (~0.06 cm a texel)
    gy_, gx_ = np.gradient(hh)
    k = 2.2
    nrm = np.dstack([-gx_ * k, -gy_ * k, np.ones_like(hh)])
    nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)
    nm = np.clip((nrm * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8)
    nm_rgba = np.dstack([nm, np.full((NMS, NMS), 255, np.uint8)])
    open(os.path.join(args.out, BUMP + ".tga"), "wb").write(images.encode_tga(NMS, NMS, nm_rgba.tobytes()))

    allV = np.vstack([Vs, KP])
    allN = np.vstack([vertex_normals(Vs, Ts), KN])
    allUV = np.vstack([UVs, np.array(kit.UV)])
    allT = np.vstack([Ts, np.array(kit.T) + len(Vs)])
    allW = Ws + kw
    _, weld = np.unique(np.round(allV, 3), axis=0, return_inverse=True)
    weld = weld.ravel()
    e = np.sort(weld[np.r_[allT[:, [0, 1]], allT[:, [1, 2]], allT[:, [2, 0]]]], 1)
    _, uses = np.unique(e, axis=0, return_counts=True)
    print("edges: %d open (one triangle), %d shared by more than two" % ((uses == 1).sum(), (uses > 2).sum()))
    # winding consistency: across a shared edge the two triangles must run it in opposite directions
    directed = {}
    for ti, (a_, b_, c_) in enumerate(allT):
        for p_, q_ in ((a_, b_), (b_, c_), (c_, a_)):
            directed.setdefault((weld[p_], weld[q_]), []).append(ti)
    bad = [k for k, v in directed.items() if len(v) > 1 or (k[1], k[0]) in directed and len(directed[(k[1], k[0])]) > 1]
    flipped = {t for k in bad for t in directed[k]}
    nb = len(Vs)
    print("winding: %d edges run the same way twice, %d triangles involved (%d body, %d kit)" % (
        len(bad), len(flipped), sum(1 for t in flipped if t < len(Ts)), sum(1 for t in flipped if t >= len(Ts))))
    if flipped:
        cen = allV[allT[sorted(flipped)]].mean(1)
        print("  where (cm):", np.round(cen[:12], 1).tolist())
        h, e_ = np.histogram(cen[:, 0], bins=24, range=(-96, 96))
        print("  x histogram:", [(int(e_[i]), int(c)) for i, c in enumerate(h) if c])
        print("  y range %.1f..%.1f, z range %.1f..%.1f" % (cen[:, 1].min(), cen[:, 1].max(), cen[:, 2].min(), cen[:, 2].max()))
    part = evo_write.SkinPart([tuple(p) for p in allV], [tuple(n) for n in allN], [tuple(u) for u in allUV],
                              [tuple(int(i) for i in t) for t in allT], [list(w.items()) for w in allW], texture=TEX)
    warnings = []
    out = evo_write.rebuild(data, {skin.node.name: part}, model_name=MODEL, warnings=warnings)
    open(os.path.join(args.out, MODEL + ".evo"), "wb").write(out)
    print(MODEL, "%d vertices, %d triangles," % (len(allV), len(allT)), "warnings:", warnings or "none")
    # read it back
    chk = evo.Scene(evo.read(out))
    sk2 = next(o for o in chk.objects if o.node.kind == 2)
    print("read back:", len(chk.objects), "objects, skin", len(sk2.node.meshes[0].positions), "vertices,",
          sk2.node.textures)

    # ---- portrait 64 x 64 (DXT3)
    pimg = Image.new("RGB", (256, 256), (70, 78, 60))
    d = ImageDraw.Draw(pimg)
    for yy in range(256):
        d.line([(0, yy), (255, yy)], fill=(int(96 - yy * 0.12), int(104 - yy * 0.12), int(80 - yy * 0.1)))
    d.polygon([(10, 256), (40, 200), (216, 200), (246, 256)], fill=(32, 32, 34))           # shoulders
    tp = tiger(np.c_[np.tile(np.arange(140), 50) * 0.3, np.repeat(np.arange(50), 140) * 0.3,
                     np.zeros(7000)]).reshape(50, 140, 3).astype(np.uint8)
    pimg.paste(Image.fromarray(tp), (58, 216))                                             # rig top
    d.polygon([(70, 214), (82, 120), (128, 22), (174, 120), (186, 214)], fill=(30, 36, 26))  # hood + drape
    d.ellipse([(72, 24), (184, 150)], fill=(34, 40, 30))
    d.ellipse([(96, 80), (160, 150)], fill=(16, 16, 14))                                   # face behind the net
    for k in range(-20, 30):
        d.line([(k * 9, 0), (k * 9 + 256, 256)], fill=(70, 84, 56), width=2)
        d.line([(k * 9, 0), (k * 9 - 256, 256)], fill=(70, 84, 56), width=2)
    mask = Image.new("L", (256, 256), 0)
    md = ImageDraw.Draw(mask)
    md.polygon([(70, 214), (82, 120), (128, 22), (174, 120), (186, 214)], fill=255)
    md.ellipse([(72, 24), (184, 150)], fill=255)
    base = Image.new("RGB", (256, 256))
    base.paste(pimg)
    clean = pimg.copy()
    cd = ImageDraw.Draw(clean)
    for yy in range(256):
        cd.line([(0, yy), (255, yy)], fill=(int(96 - yy * 0.12), int(104 - yy * 0.12), int(80 - yy * 0.1)))
    cd.polygon([(10, 256), (40, 200), (216, 200), (246, 256)], fill=(32, 32, 34))
    clean.paste(Image.fromarray(tp), (58, 216))
    final = Image.composite(pimg, clean, mask)
    fd = ImageDraw.Draw(final)
    fd.line([(78, 200), (70, 20)], fill=(18, 18, 18), width=4)                               # antenna
    port = final.resize((64, 64), Image.LANCZOS)
    prgba = np.dstack([np.asarray(port), np.full((64, 64), 255, np.uint8)])
    open(os.path.join(args.out, PORTRAIT + ".dds"), "wb").write(images.encode_dds(64, 64, prgba.tobytes(), fmt="DXT3"))
    port.resize((128, 128), Image.NEAREST).save(os.path.join(args.out, PORTRAIT + "_preview.png"))

    with open(os.path.join(args.out, "DanishFrogman.skin"), "w") as f:
        f.write("# A Danish frogman (Fromandskorpset) for Bradley: scrim-net hood, black shirt with rolled sleeves,\n"
                "# tattooed forearms, tiger-stripe chest rig with radios. Body and texture made by make_frogman.py\n"
                "# from Bradley's own body, so every animation fits. Set HEADGEAR / BACKPACK / POUCHES to NONE on the\n"
                "# loadout screen - the SAS cap and radio pack would sit on top of the hood and rig.\n\n"
                "uniform      = SAS\n"
                "uniform name = Danish Frogman\n"
                "soldier      = Bradley\n"
                f"texture      = {TEX}\n"
                f"body         = {MODEL}\n"
                f"normal map   = {BUMP}\n"
                f"portrait     = {PORTRAIT}\n")

    if args.preview:
        pv = os.path.join(args.out, "preview")
        os.makedirs(pv, exist_ok=True)
        shots = [render(allV, allT, allUV, tex_u8.astype(float), yaw, NV=allN) for yaw in (0, 35, 90, 180)]
        Image.fromarray(np.hstack(shots)).save(os.path.join(pv, "frogman_turnaround.png"))
        ch = [render(allV, allT, allUV, tex_u8.astype(float), yaw, (900, 900), NV=allN) for yaw in (0, 180)]
        for side in (-1, 1):                                                   # hand + forearm close-ups
            ctr = arm_centre(side, 72.0)
            VV = (allV - ctr) * 3.2 + (0, 97, 0)
            Mt = np.array(((1, 0, 0), (0, 0, -1.0), (0, -1.0, 0)))
            views = [render(VV, allT, allUV, tex_u8.astype(float), yaw, (520, 520), NV=allN) for yaw in (0, 90, 180)]
            views.append(render((VV - (0, 97, 0)) @ Mt.T + (0, 97, 0), allT, allUV, tex_u8.astype(float), 0, (520, 520),
                                NV=allN @ Mt.T))
            Image.fromarray(np.hstack(views)).save(os.path.join(pv, "frogman_hand_%s.png" % ("right" if side < 0 else "left")))
        top = render(allV @ np.array(((1, 0, 0), (0, 0, -1.0), (0, -1.0, 0))).T + (0, 40, 0), allT, allUV,
                     tex_u8.astype(float), 0, (2000, 1000), NV=allN @ np.array(((1, 0, 0), (0, 0, -1.0), (0, -1.0, 0))).T)[700:, :]
        Image.fromarray(top).save(os.path.join(pv, "frogman_arms_top.png"))
        for img, nm_ in zip(ch, ("front", "back")):
            Image.fromarray(img[0:500, 150:750]).resize((1200, 1000), Image.LANCZOS).save(
                os.path.join(pv, "frogman_%s_closeup.png" % nm_))
    if args.install:
        dest = os.path.join(args.game, "Mods", "DanishFrogman")
        os.makedirs(dest, exist_ok=True)
        for f in (MODEL + ".evo", TEX + ".dds", BUMP + ".tga", PORTRAIT + ".dds", "DanishFrogman.skin"):
            shutil.copy2(os.path.join(args.out, f), dest)
        print("installed", dest)


if __name__ == "__main__":
    main()
