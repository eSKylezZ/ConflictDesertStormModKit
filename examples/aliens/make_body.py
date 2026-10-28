r"""make_body.py <out folder> [--kind drone|warrior|sniper] [--game DIR] - an alien body on the soldiers' skeleton.

Every soldier animation has one track per object of the standard 51-object skeleton, so an alien keeps it: the base
body's skinned mesh (IRAQSCIENTIST - civilian clothes, no helmet) is reshaped bone by bone in model space - each vertex
p' = sum_i w_i * B_i * S_i * B_i^-1 * p (bone world B, a per-bone scale S along the bone and across it) - so it bends
with the same weights; the skeleton itself is untouched. Bone length axis = the axis pointing at the bone's child.
The texture is baked into the body's own UV layout from per-vertex colours worked out in model space (skin, eyes, a
harness), and the model / texture get their own names so the scientist is unchanged:
  ALIEN_<KIND>.evo, ALIEN_<KIND>.dds (+ ALIEN_<KIND>_preview.png)."""
import argparse, math, os, struct, sys
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [KIT, os.path.join(HERE, "..", "hovercraft")]
from conflict_ds_tools import evo, evo_write, images  # noqa: E402
from gamefiles import read_game_file  # noqa: E402

def _default_game():
    """The installed Desert Storm (env CONFLICT_DS_GAME, else registry / Steam libraries / usual paths)."""
    kit = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if kit not in sys.path:
        sys.path.insert(0, kit)
    from conflict_ds_tools import games
    return os.environ.get("CONFLICT_DS_GAME") or games.find_game_dir(games.GAMES["ds1"])


ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--kind", default="drone", choices=("drone", "warrior", "sniper"))
ap.add_argument("--game", default=_default_game())
ap.add_argument("--base", default="IRAQSCIENTIST")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
NAME = "ALIEN_" + args.kind.upper()

# (length scale, girth scale) per bone (name contains)
PROFILES = {
    "drone":   {"HEAD": (1.75, 1.45), "NECK": (1.0, 0.62), "SPINE": (1.0, 0.76), "PELVIS": (1.0, 0.8),
                "CLAVICLE": (1.0, 0.7), "UPPERARM": (1.0, 0.58), "FOREARM": (1.0, 0.55), "HAND": (1.45, 0.8),
                "THIGH": (1.0, 0.6), "CALF": (1.0, 0.55), "FOOT": (1.15, 0.8), "TOE": (1.2, 0.8)},
    "warrior": {"HEAD": (1.45, 1.3), "NECK": (1.0, 0.85), "SPINE": (1.0, 1.1), "PELVIS": (1.0, 1.0),
                "CLAVICLE": (1.0, 1.15), "UPPERARM": (1.0, 0.9), "FOREARM": (1.0, 0.9), "HAND": (1.35, 1.0),
                "THIGH": (1.0, 0.85), "CALF": (1.0, 0.8), "FOOT": (1.15, 0.95), "TOE": (1.2, 0.9)},
    "sniper":  {"HEAD": (2.05, 1.25), "NECK": (1.0, 0.55), "SPINE": (1.0, 0.7), "PELVIS": (1.0, 0.75),
                "CLAVICLE": (1.0, 0.65), "UPPERARM": (1.0, 0.5), "FOREARM": (1.0, 0.5), "HAND": (1.6, 0.7),
                "THIGH": (1.0, 0.55), "CALF": (1.0, 0.5), "FOOT": (1.2, 0.75), "TOE": (1.3, 0.75)},
}[args.kind]
SKIN = {"drone": (138, 150, 140), "warrior": (112, 120, 96), "sniper": (150, 146, 160)}[args.kind]
HARNESS = {"drone": (40, 42, 48), "warrior": (70, 52, 38), "sniper": (30, 30, 36)}[args.kind]

data = read_game_file(args.game, args.base + ".EVO")
scene = evo.Scene(evo.read(data))
objs = scene.objects
skin = next(o for o in objs if o.node.kind == 2)
mesh = skin.node.meshes[0]
P = np.array(mesh.positions, float)
weights = scene.vertex_weights(skin, mesh)          # per vertex [(bone object index, weight)]
world = {o.index: np.array(o.world, float) for o in objs}
names = {o.index: o.node.name for o in objs}
children = {}
for o in objs:
    if o.parent is not None:
        children.setdefault(o.parent, []).append(o.index)


def length_axis(i):
    """Local axis (0/1/2, sign) towards the bone's first non-connector child; x for leaf bones (Biped)."""
    for c in children.get(i, []):
        if "CONNECTOR" in names[c]:
            continue
        local = np.linalg.inv(world[i]) @ world[c][:, 3]
        k = int(np.argmax(np.abs(local[:3])))
        if abs(local[k]) > 1.0:
            return k
    return 0


deform = {}
for i, nm in names.items():
    prof = next((v for key, v in PROFILES.items() if key in nm), None)
    if not prof or "CONNECTOR" in nm:
        continue
    k = length_axis(i)
    s = np.full(3, prof[1])
    s[k] = prof[0]
    S = np.diag(list(s) + [1.0])
    deform[i] = world[i] @ S @ np.linalg.inv(world[i])

Q = np.zeros_like(P)
for v, wl in enumerate(weights):
    ph = np.append(P[v], 1.0)
    acc = np.zeros(4)
    for b, w in wl:
        acc += w * (deform[b] @ ph if b in deform else ph)
    Q[v] = acc[:3]
tris = [tuple(t) for t in mesh.triangles]
# head sculpt: no ears (lateral bumps pulled in to the skull), a flat nose
_head = next(i for i, n in names.items() if n == "BIP01 HEAD")
_hk = length_axis(_head)
_up = world[_head][:3, _hk] / np.linalg.norm(world[_head][:3, _hk])
_fw = np.array((0.0, 0.0, -1.0)) - _up * (-_up[2])
_fw /= np.linalg.norm(_fw)
_sd = np.cross(_up, _fw)
_o = world[_head][:3, 3]
_hv = [v for v, wl in enumerate(weights) if wl and wl[0][0] == _head]
if _hv:
    rel = Q[_hv] - _o
    sds, fws = rel @ _sd, rel @ _fw
    half_w = np.percentile(np.abs(sds), 80)                # the skull's half width, ears excluded
    face = np.percentile(fws, 85)                          # the face plane, nose excluded
    for k, v in enumerate(_hv):
        r = rel[k].copy()
        if abs(sds[k]) > half_w:
            r -= _sd * (sds[k] - np.sign(sds[k]) * half_w) * 0.9
        if fws[k] > face and abs(sds[k]) < 0.25 * half_w:
            r -= _fw * (fws[k] - face) * 0.8
        Q[v] = _o + r
# vertex normals from the new shape (averaged over vertices at the same position, so seams stay smooth)
fn = np.zeros_like(Q)
for a, b, c in tris:
    n = np.cross(Q[b] - Q[a], Q[c] - Q[a])
    for x in (a, b, c):
        fn[x] += n
key = {}
for v in range(len(Q)):
    key.setdefault(tuple(np.round(Q[v], 2)), []).append(v)
N = np.zeros_like(Q)
for vs in key.values():
    s = fn[vs].sum(axis=0)
    N[vs] = s / (np.linalg.norm(s) + 1e-9)

# ---- texture: per-vertex colour from the reshaped body, baked into the UVs
head_i = next(i for i, n in names.items() if n == "BIP01 HEAD")
H = world[head_i]
hk = length_axis(head_i)
head_origin = H[:3, 3]
head_up = H[:3, hk] / np.linalg.norm(H[:3, hk])
head_verts = [v for v, wl in enumerate(weights) if wl and wl[0][0] == head_i]
hv = Q[head_verts]
centre = hv.mean(axis=0)
fwd = np.array((0.0, 0.0, -1.0))                      # models face -Z
fwd = fwd - head_up * (fwd @ head_up)
fwd /= np.linalg.norm(fwd)
side = np.cross(head_up, fwd)
h_low = ((hv - head_origin) @ head_up).min()
h_high = ((hv - head_origin) @ head_up).max()
rng = np.random.default_rng(11)


def colour(v):
    q = Q[v]
    base = np.array(SKIN, float) * (0.92 + 0.12 * math.sin(q[1] * 0.35 + q[0] * 0.2) + rng.normal(0, 0.03))
    wl = weights[v]
    bone = names[wl[0][0]] if wl else ""
    if "HEAD" in bone:
        d = q - head_origin
        u, f, sd = d @ head_up, d @ fwd, d @ side
        t = (u - h_low) / max(1e-6, h_high - h_low)       # 0 chin .. 1 crown
        if f > 0:
            for eye in (-1, 1):                            # big almond eyes, slanted
                ex, ey = (sd - eye * 0.23 * (h_high - h_low) * 0.5) / 5.8, (t - 0.42 - 0.04 * eye * sd / 6) / 0.135
                if ex * ex + ey * ey < 1.0:
                    return np.array((8, 9, 12)) + (40 if ex * ex + (ey + 0.5) ** 2 < 0.08 else 0)
            if abs(sd) < 2.2 and 0.16 < t < 0.2:          # mouth slit
                return base * 0.55
        return base * (0.85 + 0.25 * t)
    if "SPINE" in bone or "CLAVICLE" in bone:              # chest harness: two crossing straps + belt
        x, y = q[0], q[1]
        if abs(abs(x) - (y - 95) * 0.45) < 4.5 or abs(y - 98) < 4:
            return np.array(HARNESS, float)
    if "PELVIS" in bone:
        return np.array(HARNESS, float) * 1.1
    return base


cols = np.array([colour(v) for v in range(len(Q))])
TW = TH = 512
img = np.zeros((TH, TW, 3), float)
filled = np.zeros((TH, TW), bool)
uv = np.array(mesh.uvs, float)
for a, b, c in tris:
    pa, pb, pc = uv[a] * (TW, TH), uv[b] * (TW, TH), uv[c] * (TW, TH)
    x0, x1 = int(max(0, min(pa[0], pb[0], pc[0]) - 1)), int(min(TW - 1, max(pa[0], pb[0], pc[0]) + 1))
    y0, y1 = int(max(0, min(pa[1], pb[1], pc[1]) - 1)), int(min(TH - 1, max(pa[1], pb[1], pc[1]) + 1))
    if x1 < x0 or y1 < y0:
        continue
    xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
    d = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
    if abs(d) < 1e-9:
        continue
    l1 = ((pb[1] - pc[1]) * (xs - pc[0]) + (pc[0] - pb[0]) * (ys - pc[1])) / d
    l2 = ((pc[1] - pa[1]) * (xs - pc[0]) + (pa[0] - pc[0]) * (ys - pc[1])) / d
    l3 = 1 - l1 - l2
    m = (l1 >= -0.02) & (l2 >= -0.02) & (l3 >= -0.02)
    rgb = l1[..., None] * cols[a] + l2[..., None] * cols[b] + l3[..., None] * cols[c]
    img[y0:y1 + 1, x0:x1 + 1][m] = rgb[m]
    filled[y0:y1 + 1, x0:x1 + 1] |= m
for _ in range(4):                                         # bleed the islands' edges outwards (no seams at mips)
    grow = img.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        sh = np.roll(np.roll(img, dy, 0), dx, 1)
        shf = np.roll(np.roll(filled, dy, 0), dx, 1)
        take = shf & ~filled
        grow[take] = sh[take]
        filled = filled | take
    img = grow
img[~filled] = SKIN
rgba = np.dstack([np.clip(img, 0, 255), np.full((TH, TW), 255)]).astype(np.uint8)
open(os.path.join(args.out, NAME + ".dds"), "wb").write(images.encode_dds(TW, TH, rgba.tobytes()))
Image.fromarray(rgba[..., :3]).save(os.path.join(args.out, NAME + "_texture.png"))

# ---- the model: reshaped skin, own model + texture name
wnames = [[(names[b], w) for b, w in wl] for wl in weights]
part = evo_write.SkinPart([tuple(q) for q in Q], [tuple(n) for n in N], [tuple(t) for t in mesh.uvs], tris, wnames)
warnings = []
out = bytearray(evo_write.rebuild(data, {skin.node.name: part}, model_name=NAME, warnings=warnings))
old_tex = next(t for _h, t in skin.node.textures)
old_h, new_h = evo_write.texture_hash(old_tex), evo_write.texture_hash(NAME)
p = out.find(b"TEXL")
while p >= 0:                                              # texture slot names + the geometry's texture hash
    n = struct.unpack_from("<I", out, p + 4)[0]
    for k in range(n):
        o = p + 8 + k * 68
        nm = out[o + 4:o + 68].split(b"\0")[0].decode("latin-1")
        if nm.lower() == old_tex.lower():
            struct.pack_into("<I", out, o, new_h)
            out[o + 4:o + 68] = NAME.encode().ljust(64, b"\0")
    p = out.find(b"TEXL", p + 4)
out = bytes(out).replace(struct.pack("<I", old_h), struct.pack("<I", new_h))
open(os.path.join(args.out, NAME + ".evo"), "wb").write(out)
print(NAME, "from", args.base, "-", len(Q), "vertices,", len(deform), "bones reshaped, texture", NAME, "warnings:",
      warnings or "none")
