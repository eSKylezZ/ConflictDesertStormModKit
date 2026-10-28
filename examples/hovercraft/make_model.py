r"""make_model.py <out folder> [--game DIR] - the hovercraft model, built by script on the Humvee's object tree.

HOVERCRAFT.evo = HUMVEE.evo rebuilt (evo_write.rebuild): the crew / exit connectors, hit regions, gun mount and the
collision stay the Humvee's, so seats, exits and damage work as on the Humvee; the body object, the gun-mount carriage
and the shadow get new geometry. HOVER_PAD.evo = a Humvee wheel with its geometry reduced to one tiny triangle (the
.DYN keeps physics wheels for the hover handling; they sit inside the skirt). HOVERCRAFT01.dds = a 256 x 256 atlas.

Vehicle space (the Humvee's): x sideways (driver at -75), y up from the ground, z forward; cm. Layout:
  skirt 0-70 cm (black rubber) around a rounded hull, hull walls up to 120 cm, an open cockpit (floor 40 cm, the seated
  crew shows from the waist up), a raised rear deck at 90 cm for the back passengers, a slanted windscreen, a pylon under
  the gun mount, two ducted fans with rudders at the stern."""
import argparse, math, os, sys
import numpy as np

KIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, KIT)
from conflict_ds_tools import evo, evo_write, images  # noqa: E402

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
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gamefiles import read_game_file  # noqa: E402

TEX = "HOVERCRAFT01"
# atlas regions (u0, v0, u1, v1) in a 256 x 256 texture
REG = {"hull": (0, 0, 128, 128), "stripe": (128, 0, 256, 32), "skirt": (128, 32, 256, 96), "glass": (128, 96, 192, 128),
       "metal": (192, 96, 256, 128), "deck": (0, 128, 128, 256), "dark": (128, 128, 256, 256)}


def make_texture():
    w = h = 256
    img = np.zeros((h, w, 4), np.float32)
    rng = np.random.default_rng(7)

    def fill(r, rgb, noise=6.0):
        u0, v0, u1, v1 = r
        img[v0:v1, u0:u1, :3] = np.array(rgb) + rng.normal(0, noise, (v1 - v0, u1 - u0, 1))
        img[v0:v1, u0:u1, 3] = 255
    fill(REG["hull"], (176, 160, 118))                     # desert sand
    for k in range(0, 128, 32):                            # panel lines + rivets
        img[k:k + 2, 0:128, :3] *= 0.7
        img[0:128, k:k + 2, :3] *= 0.7
    fill(REG["stripe"], (205, 112, 34), 4)                 # orange band
    img[12:20, 128:256, :3] = (230, 220, 200)
    fill(REG["skirt"], (34, 33, 31), 5)                    # rubber
    for k in range(32, 96, 8):
        img[k:k + 1, 128:256, :3] *= 0.6
    fill(REG["glass"], (40, 62, 78), 3)
    img[100:104, 132:188, :3] = (150, 170, 185)            # glint
    fill(REG["metal"], (110, 112, 108), 8)
    fill(REG["deck"], (120, 110, 84), 8)                   # anti-slip deck
    img[128:256:6, 0:128, :3] *= 0.85
    fill(REG["dark"], (55, 52, 46), 6)
    rgba = np.clip(img, 0, 255).astype(np.uint8)
    return images.encode_dds(w, h, rgba.tobytes())


class Mesh:
    """Flat-shaded triangles in vehicle space, each face with an atlas region; winding fixed per face."""

    def __init__(self):
        self.faces = []   # (p0, p1, p2, normal, region)

    def tri(self, a, b, c, region, out=None, both=False):
        n = np.cross(np.subtract(b, a), np.subtract(c, a))
        ln = np.linalg.norm(n)
        if ln < 1e-6:
            return
        n = n / ln
        if out is not None and np.dot(n, out) < 0:
            b, c, n = c, b, -n
        self.faces.append((a, b, c, tuple(n), region))
        if both:
            self.faces.append((a, c, b, tuple(-n), region))

    def quad(self, a, b, c, d, region, out=None, both=False):
        self.tri(a, b, c, region, out, both)
        self.tri(a, c, d, region, out, both)

    def part(self, to_local):
        rot = [row[:3] for row in to_local[:3]]
        pos, nrm, uv, tris = [], [], [], []
        for a, b, c, n, region in self.faces:
            u0, v0, u1, v1 = REG[region]
            # planar UV: the two axes the face is most flat in, tiled over the region
            ax = np.argsort(np.abs(n))[:2]
            for p, k in ((a, 0), (b, 1), (c, 2)):
                q = evo.mat_apply(to_local, p)
                pos.append(q)
                nn = np.dot(rot, n)
                nrm.append(tuple(nn / (np.linalg.norm(nn) + 1e-9)))
                s, t = (p[ax[0]] / 200.0) % 1.0, (p[ax[1]] / 200.0) % 1.0
                uv.append(((u0 + 2 + s * (u1 - u0 - 4)) / 256.0, (v0 + 2 + t * (v1 - v0 - 4)) / 256.0))
            i = len(pos) - 3
            tris.append((i, i + 1, i + 2))
        return evo_write.Part(TEX, pos, nrm, uv, tris)


def rounded_rect(hw, front, back, r, y, n=6):
    """Points of a rounded rectangle (x half width, z from back to front), counter-clockwise seen from above."""
    pts = []
    for cx, cz, a0 in ((hw - r, front - r, 0), (-hw + r, front - r, 90), (-hw + r, back + r, 180), (hw - r, back + r, 270)):
        for k in range(n + 1):
            a = math.radians(a0 + 90 * k / n)
            pts.append((cx + r * math.cos(a), y, cz + r * math.sin(a)))
    return pts


def band(m, lo, hi, region, centre):
    for i in range(len(lo)):
        j = (i + 1) % len(lo)
        mid = np.add(lo[i], hi[j]) / 2
        out = np.subtract(mid, (centre[0], mid[1], centre[2]))
        m.quad(lo[i], lo[j], hi[j], hi[i], region, out=out)


def cap(m, ring, region, up):
    c = tuple(np.mean(ring, axis=0))
    for i in range(len(ring)):
        m.tri(c, ring[i], ring[(i + 1) % len(ring)], region, out=up)


def box(m, x0, x1, y0, y1, z0, z1, region, both=False):
    c = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    P = lambda x, y, z: (x, y, z)   # noqa: E731
    faces = [((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)), ((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)),
             ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)), ((x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)),
             ((x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)), ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))]
    for f in faces:
        mid = np.mean(f, axis=0)
        m.quad(*[P(*p) for p in f], region, out=np.subtract(mid, c), both=both)


def ring_z(m, cx, cy, z0, z1, r_out, r_in, region, n=16):
    """A duct: a short tube along z (outer and inner walls + front / back lips)."""
    for k in range(n):
        a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
        o = lambda a, r, z: (cx + r * math.cos(a), cy + r * math.sin(a), z)   # noqa: E731
        m.quad(o(a0, r_out, z0), o(a1, r_out, z0), o(a1, r_out, z1), o(a0, r_out, z1), region,
               out=(math.cos(a0), math.sin(a0), 0))
        m.quad(o(a0, r_in, z0), o(a0, r_in, z1), o(a1, r_in, z1), o(a1, r_in, z0), region,
               out=(-math.cos(a0), -math.sin(a0), 0))
        for z, s in ((z0, -1), (z1, 1)):
            m.quad(o(a0, r_in, z), o(a1, r_in, z), o(a1, r_out, z), o(a0, r_out, z), region, out=(0, 0, s))


hull = Mesh()
CENTRE = (0.0, 0.0, -15.0)
# skirt: three rounded rings (bottom inset, bulge, top) -> two bands
sk_lo = rounded_rect(118, 270, -305, 60, 4)
sk_mid = rounded_rect(140, 292, -327, 70, 38)
sk_hi = rounded_rect(130, 282, -317, 66, 70)
band(hull, sk_lo, sk_mid, "skirt", CENTRE)
band(hull, sk_mid, sk_hi, "skirt", CENTRE)
cap(hull, sk_lo[::-1], "dark", (0, -1, 0))                # underside (cushion)
# hull walls above the skirt, stripe on top
w_lo = rounded_rect(124, 262, -300, 58, 70)
w_mid = rounded_rect(124, 262, -300, 58, 106)
w_hi = rounded_rect(120, 256, -296, 56, 120)
band(hull, sk_hi, w_lo, "dark", CENTRE)                    # skirt-to-hull lip
band(hull, w_lo, w_mid, "hull", CENTRE)
band(hull, w_mid, w_hi, "stripe", CENTRE)
# inner wall face of the cockpit rim (double-sided band facing in) + floors
in_hi = rounded_rect(108, 244, -284, 50, 120)
in_lo = rounded_rect(108, 244, -284, 50, 40)
for i in range(len(in_hi)):
    j = (i + 1) % len(in_hi)
    hull.quad(in_lo[i], in_lo[j], in_hi[j], in_hi[i], "hull", both=True)
    hull.quad(w_hi[i], w_hi[j], in_hi[j], in_hi[i], "stripe", out=(0, 1, 0))   # rim top
cap(hull, rounded_rect(108, 244, -284, 50, 40), "deck", (0, 1, 0))            # cockpit floor
box(hull, -108, 108, 40, 90, -284, -110, "hull")                               # rear deck block (passengers at y 97)
box(hull, -108, 108, 40, 112, 185, 244, "hull")                                # bow deck
# windscreen: slanted, double-sided, in front of the driver / front passenger
hull.quad((-100, 112, 185), (100, 112, 185), (92, 172, 150), (-92, 172, 150), "glass", both=True)
box(hull, -104, -96, 112, 174, 148, 188, "metal")                              # screen posts
box(hull, 96, 104, 112, 174, 148, 188, "metal")
# pylon under the gun mount (mount at y 204, z -13)
box(hull, -9, 9, 40, 200, -22, -4, "metal")
# stern: two ducted fans (centre y 170) on struts, hub + blades, rudders behind
for sx in (-68, 68):
    ring_z(hull, sx, 170, -312, -282, 58, 50, "metal")
    box(hull, sx - 6, sx + 6, 88, 116, -300, -290, "metal")                    # strut
    box(hull, sx - 10, sx + 10, 160, 180, -300, -294, "dark")                  # hub
    for a in (0, 60, 120):
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        p = lambda r, z: (sx + r * ca, 170 + r * sa, z)   # noqa: E731
        q = lambda r, z: (sx - 4 * sa + r * ca, 170 + 4 * ca + r * sa, z)   # noqa: E731
        hull.quad(p(-48, -297), p(48, -297), q(48, -295), q(-48, -295), "dark", both=True)
    box(hull, sx - 2, sx + 2, 118, 226, -338, -318, "stripe")                  # rudder

# the Humvee: object tree, world matrices (vehicle space -> object space for the parts)
data = read_game_file(args.game, "HUMVEE.EVO")
scene = evo.Scene(evo.read(data))
world = {o.node.name: o.world for o in scene.objects}
to_body = evo.mat_invert_affine(world["HUMVEE"])
parts = {"HUMVEE": [hull.part(to_body)]}

carriage = Mesh()                                   # gun-mount carriage: a small turret ring at the mount
ring_z(carriage, 0, 214, -30, 8, 18, 12, "metal", n=10)
parts["50 CAL CARIAGE"] = [carriage.part(evo.mat_invert_affine(world["50 CAL CARIAGE"]))]

shadow = Mesh()                                     # closed, simple: the hull block + the fans
box(shadow, -130, 130, 4, 120, -317, 282, "dark")
box(shadow, -126, 126, 120, 172, 150, 188, "dark")
for sx in (-68, 68):
    box(shadow, sx - 58, sx + 58, 112, 228, -312, -282, "dark")
parts["#SHA_HUMVEE"] = [shadow.part(evo.mat_invert_affine(world["#SHA_HUMVEE"]))]

warnings = []
out = evo_write.rebuild(data, parts, model_name="HOVERCRAFT", warnings=warnings)
open(os.path.join(args.out, "HOVERCRAFT.evo"), "wb").write(out)
print("HOVERCRAFT.evo", len(out), "bytes,", len(hull.faces), "hull triangles", "warnings:", warnings or "none")

# wheels: invisible (one tiny triangle under the hull)
wheel = read_game_file(args.game, "HUMVEERIGHTWHEEL.EVO")
wscene = evo.Scene(evo.read(wheel))
pad = Mesh()
pad.tri((0, 0, 0), (0.1, 0, 0), (0, 0, 0.1), "dark", out=(0, -1, 0))
wparts = {o.node.name: [pad.part(evo.mat_invert_affine(o.world))] for o in wscene.objects if o.node.meshes}
open(os.path.join(args.out, "HOVER_PAD.evo"), "wb").write(evo_write.rebuild(wheel, wparts, model_name="HOVER_PAD"))
open(os.path.join(args.out, TEX + ".dds"), "wb").write(make_texture())
print("HOVER_PAD.evo, " + TEX + ".dds written")
