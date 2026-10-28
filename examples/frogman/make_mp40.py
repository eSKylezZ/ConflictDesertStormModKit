r"""make_mp40.py <out folder> [--game DIR] [--install] [--preview] [--sounds DIR] - an MP40 for the Danish frogman.

The MP5SD's behaviour (US_WPN_MP5SilencedSubMG: held, aimed and reloaded like the MP5) made loud, with the MP40's own
numbers: ~540 rounds a minute, 32-round magazines, a muzzle flash and the normal hearing range. Built by script:
  - model (MP40.evo) on the MP5SD's object (evo_write.rebuild): receiver tube, bakelite lower frame, ribbed grip and
    magazine housing, long straight magazine, barrel nut, barrel with resting bar and hooded front sight, cocking
    knob, trigger guard, folding stock (extended) - smooth tubes, ~10k triangles; muzzle / eject points moved;
  - texture (MP40.dds): parkerised steel with worn edges, reddish-brown bakelite, black ribbed grip;
  - HUD / inventory picture (MP40_Icon.png) rendered from the model's left side;
  - sounds: synthesised (open-bolt slam + 9 mm bark + a short room tail, and a distant tail). --sounds DIR uses your
    own mp40_shot.wav / mp40_shot2.wav / mp40_tail.wav from DIR instead (16-bit WAV).
Gun space (the game's): y along the barrel (muzzle +y), z up, x sideways (+x = the gun's left); cm; origin = the top
of the pistol grip, where the MP5's is."""
import argparse, math, os, shutil, sys, wave
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [HERE, KIT, os.path.join(KIT, "examples", "hovercraft")]
from conflict_ds_tools import evo, evo_write, images  # noqa: E402
from gamefiles import read_game_file  # noqa: E402
from make_frogman import render, fbm, noise2  # noqa: E402

ID = "DK_WPN_MP40"
TEX = "MP40"
BASE, BASE_MODEL = "US_WPN_MP5SilencedSubMG", "SMG01_MP5SD3"
TS = 512
REG = {"metal": (0, 0, 256, 256), "bakelite": (256, 0, 512, 128), "grip": (256, 128, 512, 256),
       "mag": (0, 256, 256, 512), "dark": (256, 256, 384, 384), "bright": (384, 256, 512, 384),
       "rod": (256, 384, 512, 512)}


# ---------------------------------------------------------------- mesh building (smooth tubes, flat boxes)
class Gun:
    def __init__(self):
        self.P, self.N, self.UV, self.T = [], [], [], []

    def _v(self, p, n, reg, s, t):
        x0, y0, x1, y1 = REG[reg]
        self.P.append(tuple(float(c) for c in p))
        nn = np.asarray(n, float)
        self.N.append(tuple(nn / (np.linalg.norm(nn) + 1e-12)))
        self.UV.append(((x0 + 2 + s * (x1 - x0 - 4)) / TS, (y0 + 2 + t * (y1 - y0 - 4)) / TS))
        return len(self.P) - 1

    def tri(self, a, b, c, out):
        pa, pb, pc = (np.array(self.P[i]) for i in (a, b, c))
        if np.cross(pb - pa, pc - pa) @ np.asarray(out, float) < 0:
            b, c = c, b
        self.T.append((a, b, c))

    def quad(self, a, b, c, d, out):
        self.tri(a, b, c, out)
        self.tri(a, c, d, out)

    def box(self, lo, hi, reg):
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        for axis in range(3):
            for s in (-1, 1):
                n = np.zeros(3)
                n[axis] = s
                u, v = [k for k in range(3) if k != axis]
                ids = []
                for su, sv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    p = lo.copy()
                    p[axis] = hi[axis] if s > 0 else lo[axis]
                    p[u] = hi[u] if su else lo[u]
                    p[v] = hi[v] if sv else lo[v]
                    ids.append(self._v(p, n, reg, su, 1 - sv))
                self.quad(*ids, out=n)

    def sweep(self, path, radius, reg, n=24, caps=True, rx=None):
        """A tube along a path of points; radius = number or per-point list; rx = per-point radius across x (oval)."""
        path = [np.asarray(p, float) for p in path]
        m = len(path)
        rad = radius if isinstance(radius, (list, tuple)) else [radius] * m
        rxs = rx if rx is not None else rad
        L = np.r_[0, np.cumsum([np.linalg.norm(path[i + 1] - path[i]) for i in range(m - 1)])]
        rings = []
        for i, p in enumerate(path):
            d = path[min(i + 1, m - 1)] - path[max(i - 1, 0)]
            d /= np.linalg.norm(d)
            ref = np.array((1.0, 0, 0)) if abs(d[0]) < 0.9 else np.array((0, 0, 1.0))
            a = ref - d * (ref @ d)
            a /= np.linalg.norm(a)
            b = np.cross(d, a)
            ring = []
            for k in range(n + 1):
                th = 2 * math.pi * k / n
                off = a * math.cos(th) * rxs[i] + b * math.sin(th) * rad[i]
                nrm = a * math.cos(th) / max(rxs[i], 1e-6) + b * math.sin(th) / max(rad[i], 1e-6)
                ring.append(self._v(p + off, nrm, reg, k / n, L[i] / max(L[-1], 1e-6)))
            rings.append(ring)
        for i in range(m - 1):
            for k in range(n):
                a, b, c, d = rings[i][k], rings[i][k + 1], rings[i + 1][k + 1], rings[i + 1][k]
                out = (np.array(self.P[a]) + np.array(self.P[c])) / 2 - (path[i] + path[i + 1]) / 2
                self.quad(a, b, c, d, out)
        if caps:
            for i, sgn in ((0, -1), (m - 1, 1)):
                d = path[min(i + 1, m - 1)] - path[max(i - 1, 0)]
                d = d / np.linalg.norm(d) * sgn
                cen = self._v(path[i], d, reg, 0.5, 0.5)
                ring = [self._v(self.P[j], d, reg, 0.5 + 0.5 * math.cos(2 * math.pi * k / n),
                                0.5 + 0.5 * math.sin(2 * math.pi * k / n)) for k, j in enumerate(rings[i])]
                for k in range(n):
                    self.tri(cen, ring[k], ring[k + 1], d)

    def tube_y(self, y0, y1, r, reg, z=4.6, x=0.0, n=24, caps=True, rx=None):
        self.sweep([(x, y0, z), (x, y1, z)], r, reg, n, caps, None if rx is None else [rx, rx])

    def extrude(self, poly, x0, x1, reg):
        """A convex-ish (y, z) outline extruded across x; sides flat, side faces mapped by (y, z)."""
        poly = [np.asarray(p, float) for p in poly]
        ys, zs = [p[0] for p in poly], [p[1] for p in poly]
        st = lambda p: ((p[0] - min(ys)) / (max(ys) - min(ys)), (max(zs) - p[1]) / (max(zs) - min(zs)))  # noqa: E731
        cy = np.mean(poly, axis=0)
        for x, s in ((x0, -1), (x1, 1)):
            n = (s, 0, 0)
            c = self._v((x, cy[0], cy[1]), n, reg, *st(cy))
            ids = [self._v((x, p[0], p[1]), n, reg, *st(p)) for p in poly]
            for k in range(len(poly)):
                self.tri(c, ids[k], ids[(k + 1) % len(poly)], n)
        for k in range(len(poly)):
            p, q = poly[k], poly[(k + 1) % len(poly)]
            e = q - p
            n = np.array((0.0, e[1], -e[0]))
            if n[1:] @ (p - cy) < 0:
                n = -n
            ids = [self._v((x, pt[0], pt[1]), n, reg, su, sv)
                   for (x, pt, su, sv) in ((x0, p, 0, 0), (x1, p, 1, 0), (x1, q, 1, 1), (x0, q, 0, 1))]
            self.quad(*ids, out=n)


AXIS_Z = 4.6          # bore line height above the grip top


def build():
    g = Gun()
    Z = AXIS_Z
    # receiver: bolt tube + knurled rear cap
    g.tube_y(-5.6, 21.0, 1.85, "metal")
    g.tube_y(-6.9, -5.5, 2.05, "metal")
    for k in range(6):
        g.tube_y(-6.8 + k * 0.22, -6.7 + k * 0.22, 2.15, "bright", n=24)
    g.box((-0.95, -3.6, Z + 1.55), (0.95, -1.6, Z + 2.3), "metal")            # rear sight base
    for x in (-0.65, 0.65):
        g.box((x - 0.18, -2.9, Z + 2.3), (x + 0.18, -2.3, Z + 3.0), "metal")  # sight ears
    # bakelite lower frame + steel bottom plate
    g.box((-1.55, -6.0, 0.15), (1.55, 12.2, 3.7), "bakelite")
    g.box((-1.35, -6.2, -0.35), (1.35, 12.4, 0.2), "metal")
    g.box((-1.7, 0.6, 1.0), (1.7, 10.2, 1.25), "dark")                        # the panel's raised rib line
    # magazine housing with ribs, and the magazine
    g.box((-1.75, 12.2, -4.8), (1.75, 17.6, 3.9), "metal")
    for z in (-3.8, -2.4, -1.0, 0.4):
        g.box((-1.95, 12.6, z), (1.95, 17.2, z + 0.55), "metal")
    g.box((-1.3, 13.3, -21.6), (1.3, 16.5, -4.8), "mag")
    g.box((-1.5, 13.0, -22.2), (1.5, 16.8, -21.6), "metal")                  # floor plate
    for y in (14.3, 15.5):
        g.box((-1.42, y - 0.18, -20.2), (1.42, y + 0.18, -6.0), "mag")        # stamped ribs
    # barrel nut, barrel jacket, barrel, muzzle, front sight, resting bar
    g.tube_y(21.0, 23.4, 2.05, "metal")
    for k in range(5):
        g.tube_y(21.2 + k * 0.45, 21.4 + k * 0.45, 2.15, "bright", n=24)
    g.tube_y(23.4, 31.0, 1.3, "metal")
    g.tube_y(31.0, 44.6, 0.8, "metal", n=20)
    g.tube_y(44.3, 45.3, 0.95, "bright", n=20)
    g.box((-0.75, 41.6, Z + 0.6), (0.75, 43.9, Z + 1.5), "metal")             # sight base
    g.box((-0.13, 42.55, Z + 1.5), (0.13, 43.0, Z + 2.7), "metal")            # post
    g.sweep([(0, 42.0, Z + 2.25), (0, 43.6, Z + 2.25)], 1.0, "metal", n=24, caps=False)   # hood (open ring)
    g.sweep([(0, 43.6, Z + 2.25), (0, 42.0, Z + 2.25)], 0.85, "dark", n=24, caps=False)   # hood inside
    g.box((-0.38, 30.6, Z - 2.6), (0.38, 34.2, Z - 0.9), "metal")             # resting bar
    g.box((-0.38, 33.4, Z - 3.5), (0.38, 34.2, Z - 2.6), "metal")
    # cocking knob on the left (+x) and its slot; ejection port on the right (-x)
    g.box((1.75, 2.0, Z + 0.35), (1.9, 14.5, Z + 1.0), "dark")
    g.sweep([(1.8, 11.0, Z + 0.7), (3.2, 11.0, Z + 0.7)], 0.45, "metal", n=16)
    g.sweep([(3.0, 11.0, Z + 0.7), (3.7, 11.0, Z + 0.7)], 0.72, "metal", n=20)
    g.box((-1.9, 9.5, Z + 0.2), (-1.75, 13.2, Z + 1.6), "dark")
    # trigger guard + trigger
    g.sweep([(0, 0.9, 0.0), (0, 1.0, -1.6), (0, 1.9, -3.0), (0, 4.0, -3.7), (0, 6.2, -3.2), (0, 7.3, -1.6),
             (0, 7.4, 0.0)], 0.3, "metal", n=10, rx=[0.42] * 7)
    g.extrude([(4.0, 0.0), (4.7, 0.0), (4.5, -1.2), (4.0, -2.3), (3.6, -2.4), (3.9, -1.2)], -0.25, 0.25, "metal")
    # pistol grip (black ribbed bakelite) on a steel core, raked back
    g.extrude([(1.0, 0.0), (-4.2, 0.0), (-5.4, -3.0), (-6.9, -10.6), (-6.4, -11.4), (-2.8, -11.6), (-2.2, -10.8),
               (-0.6, -4.2)], -1.5, 1.5, "grip")
    g.extrude([(0.8, 0.1), (-4.0, 0.1), (-6.6, -10.9), (-2.6, -11.3)], -1.2, 1.2, "metal")
    # folding stock, extended: hinge, two rods, shoulder piece
    g.box((-1.6, -8.0, -0.4), (1.6, -6.0, 3.6), "metal")
    for x in (-1.3, 1.3):
        g.sweep([(x, -7.4, 2.9), (x, -12.0, 2.5), (x, -20.0, 1.7), (x, -27.6, 1.0)], 0.42, "rod", n=16)
    g.extrude([(-27.0, 2.6), (-28.6, 2.9), (-29.7, -1.6), (-29.4, -9.0), (-28.1, -9.4), (-28.2, -1.6)],
              -1.7, 1.7, "metal")
    g.sweep([(-1.45, -27.4, 1.0), (1.45, -27.4, 1.0)], 0.6, "metal", n=16)                       # cross pin
    return g


# ---------------------------------------------------------------- texture
def texture():
    rng = np.random.default_rng(40)
    img = np.zeros((TS, TS, 3))

    def region(name):
        x0, y0, x1, y1 = REG[name]
        return slice(y0, y1), slice(x0, x1), x1 - x0, y1 - y0

    ys_, xs_, w, h = region("metal")
    n1, n2 = noise2(w, h, 0.03, 1, 4), noise2(w, h, 0.25, 2, 2)
    m = np.ones((h, w, 3)) * np.array((40.0, 40.0, 45.0)) * (0.82 + 0.3 * n1)[..., None]
    m[n2 > 0.8] += 10                                               # a little wear
    img[ys_, xs_] = m
    ys_, xs_, w, h = region("bakelite")
    yy, xx = np.mgrid[0:h, 0:w]
    grain = noise2(w, h, 0.015, 3, 3) * 0.6 + 0.4 * (np.sin(xx * 0.9 + 6 * noise2(w, h, 0.02, 4, 2)) * 0.5 + 0.5)
    b = np.array((74.0, 34.0, 32.0)) * (0.7 + 0.38 * grain)[..., None]
    chip = noise2(w, h, 0.2, 5, 2) > 0.86
    b[chip] = (128, 100, 86)
    img[ys_, xs_] = b
    ys_, xs_, w, h = region("grip")
    yy, xx = np.mgrid[0:h, 0:w]
    g = np.ones((h, w, 3)) * np.array((30.0, 30.0, 33.0)) * (0.85 + 0.3 * noise2(w, h, 0.05, 6, 3))[..., None]
    rib = (yy % 9)
    g[rib < 2] *= 0.45
    g[rib == 2] *= 1.6
    cy, cx = int(h * 0.45), int(w * 0.5)
    screw = (yy - cy) ** 2 + (xx - cx) ** 2 < 49
    g[screw] = (70, 70, 74)
    g[screw & (np.abs(yy - cy) < 1)] = (20, 20, 22)
    img[ys_, xs_] = g
    ys_, xs_, w, h = region("mag")
    mg = np.ones((h, w, 3)) * np.array((36.0, 36.0, 40.0)) * (0.85 + 0.3 * noise2(w, h, 0.04, 7, 3))[..., None]
    window = (np.abs(np.mgrid[0:h, 0:w][1] - w * 0.5) < 6) & (np.mgrid[0:h, 0:w][0] > h * 0.35) & \
        (np.mgrid[0:h, 0:w][0] < h * 0.42)
    mg[window] = (14, 14, 16)
    img[ys_, xs_] = mg
    ys_, xs_, w, h = region("dark")
    img[ys_, xs_] = np.array((16.0, 16.0, 18.0)) * (0.8 + 0.4 * noise2(w, h, 0.1, 8, 2))[..., None]
    ys_, xs_, w, h = region("bright")
    img[ys_, xs_] = np.array((96.0, 96.0, 102.0)) * (0.8 + 0.4 * noise2(w, h, 0.08, 9, 2))[..., None]
    ys_, xs_, w, h = region("rod")
    r = np.ones((h, w, 3)) * np.array((44.0, 44.0, 48.0)) * (0.85 + 0.3 * noise2(w, h, 0.03, 10, 3))[..., None]
    xx = np.mgrid[0:h, 0:w][1]
    r *= (0.75 + 0.45 * np.sin(xx / w * 2 * math.pi) ** 2)[..., None]       # a highlight line round each rod
    img[ys_, xs_] = r
    return np.clip(img, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- sound (22050 Hz, mono)
RATE = 22050


def _lowpass(x, fc):
    a = math.exp(-2 * math.pi * fc / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def mp40_shot(seed, length=0.42):
    rng = np.random.default_rng(seed)
    n = int(RATE * length)
    t = np.arange(n) / RATE
    noise = rng.uniform(-1, 1, n)
    crack = (noise - _lowpass(noise, 1800)) * np.exp(-t * 260)                      # the muzzle crack
    bark = _lowpass(noise, 900) * np.exp(-t * 32) * 3.2                             # 9 mm bark
    f = 105 * (1 - 0.45 * np.clip(t / 0.08, 0, 1))
    thump = np.sin(2 * math.pi * np.cumsum(f) / RATE) * np.exp(-t * 26) * 0.9        # chest thump
    clank = np.zeros(n)                                                             # open bolt slamming home
    for fr, dec, amp in ((1870, 70, 0.30), (3120, 95, 0.22), (4710, 130, 0.14), (2440, 85, 0.16)):
        fr *= 1 + rng.normal(0, 0.02)
        clank += amp * np.sin(2 * math.pi * fr * t + rng.uniform(0, 6)) * np.exp(-t * dec)
    back = np.zeros(n)                                                              # bolt back, ~45 ms later
    k = int(RATE * 0.045)
    back[k:] = clank[:n - k] * 0.45
    room = _lowpass(noise, 600) * np.exp(-t * 9) * 0.9 * np.clip(t / 0.01, 0, 1)    # short room tail
    x = crack * 1.1 + bark + thump + clank + back + room
    x *= np.clip(t / 0.0006, 0, 1)
    return x / (np.abs(x).max() + 1e-9) * 0.92


def mp40_tail(seed, length=1.5):
    rng = np.random.default_rng(seed)
    n = int(RATE * length)
    t = np.arange(n) / RATE
    noise = rng.uniform(-1, 1, n)
    rumble = _lowpass(_lowpass(noise, 220), 220) * np.exp(-t * 2.4) * np.clip(t / 0.03, 0, 1)
    pop = _lowpass(noise, 1200) * np.exp(-t * 40) * 0.25
    x = rumble + pop
    return x / (np.abs(x).max() + 1e-9) * 0.85


def write_wav(path, x):
    pcm = np.round(np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


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
    ap.add_argument("--install", action="store_true", help="copy into <game>\\Mods\\DanishFrogman")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--sounds", help="folder with your own mp40_shot.wav / mp40_shot2.wav / mp40_tail.wav")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    g = build()
    tex = texture()
    open(os.path.join(args.out, TEX + ".dds"), "wb").write(
        images.encode_dds(TS, TS, np.dstack([tex, np.full((TS, TS), 255, np.uint8)]).tobytes(), fmt="DXT1"))
    part = evo_write.Part(TEX, g.P, g.N, g.UV, g.T)
    data = read_game_file(args.game, BASE_MODEL + ".EVO")
    warnings = []
    muzzle, eject = (0.0, 45.4, AXIS_Z), (-2.0, 11.3, AXIS_Z + 0.9)
    out = evo_write.rebuild(data, {0: [part]}, model_name=TEX, warnings=warnings, points={0: {0: muzzle, 2: eject}})
    open(os.path.join(args.out, TEX + ".evo"), "wb").write(out)
    chk = evo.Scene(evo.read(out))
    print(TEX, "%d vertices, %d triangles" % (len(g.P), len(g.T)), "warnings:", warnings or "none",
          "| read back:", sum(len(m.positions) for m in chk.objects[0].node.meshes), "vertices, points",
          chk.objects[0].node.points)

    V, T, UV, N = np.array(g.P), np.array(g.T), np.array(g.UV), np.array(g.N)
    # HUD / inventory picture: the gun's left side (+x towards the viewer), muzzle to the right, light like the game's
    Vs = np.c_[V[:, 1], V[:, 2], -V[:, 0]] * 2.6 + (0, 60, 0)
    Ns = np.c_[N[:, 1], N[:, 2], -N[:, 0]]
    shot = render(Vs, T, UV, tex.astype(float), 0, (1200, 400), NV=Ns)
    mask = np.any(np.abs(shot.astype(int) - 235) > 2, axis=2)
    yy, xx = np.nonzero(mask)
    crop = shot[yy.min():yy.max() + 1, xx.min():xx.max() + 1].astype(float)
    cm = mask[yy.min():yy.max() + 1, xx.min():xx.max() + 1]
    lum = crop.mean(axis=2)
    grey = np.clip(120 + (lum - lum[cm].mean()) * 2.2, 70, 235)
    rgba = np.dstack([grey, grey, grey, cm * 255.0]).astype(np.uint8)
    pic = Image.fromarray(rgba, "RGBA")
    pic.thumbnail((124, 38), Image.LANCZOS)
    icon = Image.new("RGBA", (128, 40), (0, 0, 0, 0))
    icon.paste(pic, ((128 - pic.width) // 2, (40 - pic.height) // 2))
    icon.save(os.path.join(args.out, TEX + "_Icon.png"))

    # sounds
    names = ("mp40_shot.wav", "mp40_shot2.wav", "mp40_tail.wav")
    if args.sounds:
        for nm in names:
            shutil.copy2(os.path.join(args.sounds, nm), os.path.join(args.out, nm))
        print("sounds: your own from", args.sounds)
    else:
        write_wav(os.path.join(args.out, names[0]), mp40_shot(1))
        write_wav(os.path.join(args.out, names[1]), mp40_shot(2))
        write_wav(os.path.join(args.out, names[2]), mp40_tail(3))

    with open(os.path.join(args.out, ID + ".weapon"), "w") as f:
        f.write("# MP40 (examples/frogman/make_mp40.py): the MP5SD's handling with the MP40's model, rate of fire,\n"
                "# 32-round magazines, muzzle flash and its own sound. Swap the WAVs for your own recordings any time.\n\n"
                f"id                 = {ID}\n"
                f"based on           = {BASE}\n"
                "display name       = MP40\n"
                f"model              = {TEX}\n"
                f"hud icon           = {TEX}_Icon\n"
                "time between shots = 0.11        # ~540 rounds a minute (MP5 0.08)\n"
                "magazine size      = 32\n"
                "magazines          = 6\n"
                "damage             = 13\n"
                "range              = 650\n"
                "muzzle flash       = yes         # the MP5SD has none\n"
                "hearing range      = 80          # the MP5SD's is 8 (silenced)\n"
                "shot sound         = mp40_shot.wav\n"
                "shot sound 2       = mp40_shot2.wav\n"
                "tail sound         = mp40_tail.wav\n")

    if args.preview:
        pv = os.path.join(args.out, "preview")
        os.makedirs(pv, exist_ok=True)
        views = []
        for yaw, pitch in ((-35, 15), (0, 0), (200, 10)):
            c, s = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
            Rx = np.array(((1, 0, 0), (0, c, -s), (0, s, c)))
            Vg = (np.c_[V[:, 1] - 8, V[:, 2] + 4, -V[:, 0]] @ Rx.T) * 4.6 + (0, 95, 0)
            Ng = np.c_[N[:, 1], N[:, 2], -N[:, 0]] @ Rx.T
            views.append(render(Vg, T, UV, tex.astype(float), yaw, (1400, 700), NV=Ng)[120:620])
        Image.fromarray(np.vstack(views)).save(os.path.join(pv, "mp40_views.png"))
        Image.fromarray(tex).save(os.path.join(pv, "mp40_texture.png"))
        icon.resize((512, 160), Image.NEAREST).save(os.path.join(pv, "mp40_icon_x4.png"))

    if args.install:
        dest = os.path.join(args.game, "Mods", "DanishFrogman")
        os.makedirs(dest, exist_ok=True)
        for f in (TEX + ".evo", TEX + ".dds", TEX + "_Icon.png", ID + ".weapon") + names:
            shutil.copy2(os.path.join(args.out, f), dest)
        print("installed", dest)


if __name__ == "__main__":
    main()
