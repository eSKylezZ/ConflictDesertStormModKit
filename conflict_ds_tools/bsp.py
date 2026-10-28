"""Collision trees (BSPT chunks) of Conflict: Desert Storm .EVO meshes: read, build from triangles, write.

An object's collision is a BSP tree stored after its geometry as {u32 size, u32 u, "BSPT", u32 size, tree}. The game
(FUN_004f7980) loads only the u = 0 tree; u = 1 trees (render-only polygons) are read and thrown away. The EOBJ header
names the source the original tool built it from ("#BSP_<name>" helper object, "THIS" = the object itself, "NOBSP")
- the game ignores that name.

Tree (FUN_0055c790; offsets relative to the tree start, 0 = none, the root node is at 0):
  node   32 B: plane (nx, ny, nz, d; distance = n.p + d), offset of the polygons facing n, offset of the polygons
         facing -n, child on the negative side, child on the positive side
  polys  back to back {u16 vertex count, u16 surface, vec3 centre (vertex average), f32 1.25 x the largest squared
         vertex distance from the centre, count x vec3}, ended by a u16 0; the block is padded to 16 bytes:
         size = (sum(20 + 12 n) + 0x13) & ~0xF
  layout pre-order: node, its n-facing block, its -n-facing block, the negative subtree, the positive subtree.
It is a polygon-soup tree - no solid / empty leaves. The queries (sphere FUN_0054dfe0, swept FUN_0054e420) test a
node's polygons when the shape touches its plane (|distance| <= r + 1e-4) and descend into the side(s) it reaches, so
every polygon below a node must lie on that node's side of the plane; polygons crossing a plane are split. Polygon
winding: (v1 - v0) x (v2 - v0) points along the plane normal of the n-facing list. Game polygons are convex.

Surface = row of the game's surface table Default.spl (identical in every archive), see SURFACES: footstep sounds and
bullet-hit effects per row. In game (Mission 1, a wall across the player's path): Blocking Concrete, Concrete, Render
Only and Player Barrier all stop the player's soldier, Enemy Barrier doesn't - walking collision ignores the surface
except the barriers. Projectiles (FUN_004F38E0 -> FUN_004DBE10 with flags 0x1C08 | 0x4000, + 0x80 for guns'
bullets) skip, via the face mask built in FUN_004DE6C0: all three barrier classes, faces with +0x14 bit 2 AND the
float == 0 (Invisible, fences, railings, hedge, forest floor), and with 0x80 every float == 0 face (water). Tested in
game: Blocking Invisible stops the soldier but bullets pass; Player Barrier: bullets pass; Concrete stops both.
"Blocking ..." rows (class 8 in the row's flags, plain = 1, barriers 16/32/64) are left out of
the AI's route map: the level-start quadtree (FUN_005062e0 -> FUN_00505430, column test FUN_00505200 for class 8)
splits mixed squares and makes walkable cells only where a column has no Blocking face and a ray down finds ground;
the cells are linked into the AI's navigation graph ([0x63C9C0]). Plain = ground / floors, Blocking = obstacles.
That build only runs when the level has no saved route map: level init FUN_0040C090 first tries Routing_Load
(FUN_005072A0: <level>.qtd quadtree + <level>.rmd route graph) - shipped levels have both, so collision changes in a
level reach soldiers' movement at once but the AI's routes only when those files are redone.

Pure Python, no dependencies.
"""
import math
import struct

# Default.spl rows, in order (the surface index is the row number)
SURFACES = [
    "Default", "Blocking Sand", "Blocking Baked Earth", "Blocking Metal", "Blocking Concrete", "Blocking Tarmac",
    "Blocking Rock", "Blocking Marble Tile", "Blocking Ladder", "Blocking Glass", "Blocking Water", "Blocking Cloth",
    "Blocking Wood", "Blocking Breakable", "Blocking Chain Link Fence", "Blocking Flesh", "Sand", "Baked Earth",
    "Metal", "Concrete", "Tarmac", "Rock", "Marble Tile", "Ladder", "Glass", "Water", "Cloth", "Wood", "Breakable",
    "Chain Link Fence", "Flesh", "Grass", "Render Only", "Blocking Grass", "Blocking Iron Railings",
    "Blocking Invisible", "Blocking Wood Fence", "Blocking Hedge", "Iron Railings", "Invisible", "Wood Fence", "Hedge",
    "River Bed", "Blocking River Bed", "Forest Floor", "Blocking Forest Floor", "Player Barrier", "Enemy Barrier",
    "Non Controlled Barrier", "Stairs", "Blocking Stairs",
]
# Desert Storm II's Default.spl: DS1's 51 rows unchanged, then these (same file in all 14 DS2 level archives)
SURFACES_DS2 = [
    "Snow", "Blocking Snow", "Soft Mud", "Blocking Soft Mud", "Tree Canopy", "Blocking Tree Canopy", "Gravel",
    "Plastic", "Rubber", "Blocking Gravel", "Blocking Plastic", "Blocking Rubber",
]
# Later games only append rows (every game's table is the one before it plus these): The Great Escape = DS2's 63
# (all 18 level archives), Vietnam 65 (14), Global Storm 76 (16)
SURFACES_CV = ["Ignored by Exporter", "Steaming Pipe"]
SURFACES_CG = [
    "Vehicle Barrier", "Blocking Air Duct", "Air Duct", "Blocking Brick", "Blocking Plaster", "Brick", "Plaster",
    "Blocking Electronics", "Electronics", "Blocking Foliage", "Foliage",
]
ALL_SURFACES = SURFACES + SURFACES_DS2 + SURFACES_CV + SURFACES_CG
SURFACE_COUNTS = {"ds1": len(SURFACES), "ds2": 63, "ge": 63, "cv": 65, "cg": 76}


def surfaces(game_id="ds1"):
    """Default.spl row names of a game (ds1: 51 rows, ds2 / ge: 63, cv: 65, cg: 76)."""
    return ALL_SURFACES[:SURFACE_COUNTS.get(game_id, len(SURFACES))]


# for models moved to a game whose Default.spl lacks a row: the nearest row it has (by footstep sound / feel and the
# same blocking behaviour); every game has DS1's 51 rows, so the chain always ends there
SURFACE_FALLBACK = {
    "Snow": "Sand", "Blocking Snow": "Blocking Sand", "Soft Mud": "River Bed", "Blocking Soft Mud": "Blocking River Bed",
    "Tree Canopy": "Hedge", "Blocking Tree Canopy": "Blocking Hedge", "Gravel": "Baked Earth",
    "Blocking Gravel": "Blocking Baked Earth", "Plastic": "Wood", "Blocking Plastic": "Blocking Wood",
    "Rubber": "Cloth", "Blocking Rubber": "Blocking Cloth",
    "Ignored by Exporter": "Render Only", "Steaming Pipe": "Metal",
    "Vehicle Barrier": "Blocking Invisible", "Blocking Air Duct": "Blocking Metal", "Air Duct": "Metal",
    "Blocking Brick": "Blocking Concrete", "Blocking Plaster": "Blocking Concrete", "Brick": "Concrete",
    "Plaster": "Concrete", "Blocking Electronics": "Blocking Metal", "Electronics": "Metal",
    "Blocking Foliage": "Blocking Hedge", "Foliage": "Hedge",
}


def convert_surface(surface, game_id):
    """(surface row for `game_id`, the row's name before or None if unchanged): a row the game's Default.spl
    doesn't have becomes its fallback (SURFACE_FALLBACK); the flag bits above 0x3FF are dropped then."""
    index = surface & 0x3FF
    count = SURFACE_COUNTS.get(game_id, len(SURFACES))
    if index < count:
        return surface, None
    name = surface_name(index)
    target = name
    while surface_index(target) >= count:
        target = SURFACE_FALLBACK.get(target)
        if target is None:
            return 0, name                     # unknown row: Default
    return surface_index(target), name


def first_game_with(index):
    """The first game (id) whose Default.spl has row `index`, or None."""
    for gid in ("ds1", "ds2", "cv", "cg"):
        if index < SURFACE_COUNTS[gid]:
            return gid
    return None


def surface_name(index):
    return ALL_SURFACES[index] if 0 <= index < len(ALL_SURFACES) else str(index)

EPS = 0.01          # cm: points this close to a plane count as on it
MAX_VERTS = 32      # largest merged polygon (the game's own go up to 37)


def surface_index(name):
    """Surface row for a name (case and spaces / underscores ignored; the later games' extra rows included - the
    writer refuses rows the template's game doesn't have); a number string is taken as it is."""
    if isinstance(name, int):
        return name
    s = str(name).strip()
    if s.isdigit():
        return int(s)
    key = s.lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    for i, n in enumerate(ALL_SURFACES):
        if n.lower() == key:
            return i
    raise ValueError("unknown surface %r" % name)


class Poly:
    __slots__ = ("verts", "surface")

    def __init__(self, verts, surface):
        self.verts = verts          # [(x, y, z)], convex, winding along the plane normal
        self.surface = surface


class Node:
    __slots__ = ("plane", "front", "back", "neg", "pos")

    def __init__(self, plane):
        self.plane = plane          # (nx, ny, nz, d)
        self.front = []             # polygons facing the plane normal
        self.back = []              # polygons facing away from it
        self.neg = None
        self.pos = None


# ---- vector helpers ----
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _newell(verts):
    """Polygon normal (not normalised) by Newell's method - robust for any planar polygon."""
    x = y = z = 0.0
    k = len(verts)
    for i in range(k):
        a, b = verts[i], verts[(i + 1) % k]
        x += (a[1] - b[1]) * (a[2] + b[2])
        y += (a[2] - b[2]) * (a[0] + b[0])
        z += (a[0] - b[0]) * (a[1] + b[1])
    return (x, y, z)


def plane_of(verts):
    """(nx, ny, nz, d) of a polygon, or None when it is degenerate."""
    n = _newell(verts)
    length = math.sqrt(_dot(n, n))
    if length < 1e-9:
        return None
    n = (n[0] / length, n[1] / length, n[2] / length)
    c = [sum(v[i] for v in verts) / len(verts) for i in range(3)]
    return (n[0], n[1], n[2], -_dot(n, c))


def _dist(plane, v):
    return plane[0] * v[0] + plane[1] * v[1] + plane[2] * v[2] + plane[3]


def area(verts):
    return 0.5 * math.sqrt(_dot(*(_newell(verts),) * 2))


# ---- reading ----
def read(data):
    """A tree from a BSPT chunk's data (the bytes after the size); None for an empty tree."""
    if len(data) < 32:
        return None

    def polys(off):
        out = []
        while off and off + 4 <= len(data):
            n, surface = struct.unpack_from("<2H", data, off)
            if n == 0:
                break
            verts = [struct.unpack_from("<3f", data, off + 20 + 12 * i) for i in range(n)]
            out.append(Poly(verts, surface))
            off += 20 + 12 * n
        return out

    def node(off):
        nd = Node(struct.unpack_from("<4f", data, off))
        a, b, neg, pos = struct.unpack_from("<4I", data, off + 16)
        nd.front = polys(a) if a else []
        nd.back = polys(b) if b else []
        nd.neg = node(neg) if neg else None
        nd.pos = node(pos) if pos else None
        return nd

    return node(0)


def nodes(root):
    """All nodes, pre-order."""
    out, stack = [], [root] if root else []
    while stack:
        nd = stack.pop()
        out.append(nd)
        if nd.pos:
            stack.append(nd.pos)
        if nd.neg:
            stack.append(nd.neg)
    return out


def polygons(root):
    return [p for nd in nodes(root) for p in nd.front + nd.back]


# ---- writing ----
def _block_size(polys):
    if not polys:
        return 0
    return (sum(20 + 12 * len(p.verts) for p in polys) + 0x13) & ~0xF


def _poly_bytes(p):
    k = len(p.verts)
    c = tuple(sum(v[i] for v in p.verts) / k for i in range(3))
    # 1.25 x the largest squared distance; the game's own values differ by float rounding either way, so err large
    # (it only culls: a sphere is tested against the polygon when |centre - c|^2 <= r^2 + this + 1e-4)
    r2 = max(_dot(_sub(v, c), _sub(v, c)) for v in p.verts) * 1.25 * 1.0001
    out = struct.pack("<2H3ff", k, p.surface, c[0], c[1], c[2], r2)
    return out + b"".join(struct.pack("<3f", *v) for v in p.verts)


def write(root):
    """Tree bytes (the BSPT chunk's data); b"" for an empty tree."""
    if root is None:
        return b""
    order = nodes(root)
    offset = {}
    o = 0
    for nd in order:
        offset[id(nd)] = o
        o += 32 + _block_size(nd.front) + _block_size(nd.back)
    out = bytearray(o)
    for nd in order:
        base = offset[id(nd)]
        fa = base + 32 if nd.front else 0
        ba = base + 32 + _block_size(nd.front) if nd.back else 0
        struct.pack_into("<4f4I", out, base, *nd.plane, fa, ba,
                         offset[id(nd.neg)] if nd.neg else 0, offset[id(nd.pos)] if nd.pos else 0)
        for start, polys in ((fa, nd.front), (ba, nd.back)):
            p_off = start
            for p in polys:
                b = _poly_bytes(p)
                out[p_off:p_off + len(b)] = b
                p_off += len(b)
    return bytes(out)


def chunk(tree_bytes, u=0):
    """The bytes an object stores for one tree: {u32 size, u32 u, "BSPT", u32 size, data}, or {0, u} when empty."""
    if not tree_bytes:
        return struct.pack("<2I", 0, u)
    return struct.pack("<2I4sI", len(tree_bytes), u, b"BSPT", len(tree_bytes)) + tree_bytes


# ---- building ----
def _convex(verts, normal):
    """Every turn goes the same way round the normal, none turns back on itself, and the turns add up to one full
    turn. The side test alone let through outlines that double back along a sliver (a spike: a turn of ~180 degrees
    with a tiny cross product) or wind round twice - seen when merging the game's own split polygons back up, where
    the merged polygon covered other ground than its triangles."""
    k = len(verts)
    total = 0.0
    for i in range(k):
        a, b, c = verts[i], verts[(i + 1) % k], verts[(i + 2) % k]
        u, w = _sub(b, a), _sub(c, b)
        lu, lw = math.sqrt(_dot(u, u)), math.sqrt(_dot(w, w))
        if lu < 1e-6 or lw < 1e-6:
            return False
        s = _dot(_cross(u, w), normal)
        if s < -1e-4 * lu * lw:
            return False
        turn = math.atan2(s, _dot(u, w))
        if not -1e-3 < turn < math.pi - 1e-3:
            return False
        total += turn
    return abs(total - 2 * math.pi) < 1e-3 * k


def _drop_collinear(idx, pos, normal):
    """Remove vertices on a straight line between their neighbours."""
    changed = True
    while changed and len(idx) > 3:
        changed = False
        for i in range(len(idx)):
            a, b, c = pos[idx[i - 1]], pos[idx[i]], pos[idx[(i + 1) % len(idx)]]
            u, w = _sub(b, a), _sub(c, b)
            lu, lw = math.sqrt(_dot(u, u)), math.sqrt(_dot(w, w))
            if lu * lw == 0 or abs(_dot(_cross(u, w), normal)) <= 1e-5 * lu * lw and _dot(u, w) > 0:
                del idx[i]
                changed = True
                break
    return idx


def merge_coplanar(triangles, max_verts=MAX_VERTS):
    """Convex polygons from triangles [((v0, v1, v2), surface)]: triangles welded by position, then neighbours with the
    same plane and surface merged while the result stays convex. Degenerate triangles are dropped."""
    pos, index = [], {}

    def vid(v):
        key = (round(v[0], 3), round(v[1], 3), round(v[2], 3))
        if key not in index:
            index[key] = len(pos)
            pos.append(tuple(float(x) for x in v))
        return index[key]

    groups = {}
    for verts, surface in triangles:
        t = [vid(v) for v in verts]
        if len(set(t)) < 3:
            continue
        pl = plane_of([pos[i] for i in t])
        if pl is None or area([pos[i] for i in t]) < 1e-4:
            continue
        key = (surface, round(pl[0], 3), round(pl[1], 3), round(pl[2], 3), round(pl[3], 1))
        groups.setdefault(key, []).append(t)

    out = []
    for key, tris in groups.items():
        normal = key[1:4]
        polys = [list(t) for t in tris]
        alive = [True] * len(polys)
        edge_owner = {}
        for pi, p in enumerate(polys):
            for i in range(len(p)):
                edge_owner[(p[i], p[(i + 1) % len(p)])] = pi
        changed = True
        while changed:
            changed = False
            for pi in range(len(polys)):
                if not alive[pi]:
                    continue
                p = polys[pi]
                for i in range(len(p)):
                    a, b = p[i], p[(i + 1) % len(p)]
                    qi = edge_owner.get((b, a))
                    if qi is None or qi == pi or not alive[qi]:
                        continue
                    q = polys[qi]
                    if b not in q or q[(q.index(b) + 1) % len(q)] != a:
                        continue            # stale entry: that edge went in an earlier merge
                    j = q.index(b)
                    # p ... a | q from a round to b | b ... p
                    q_path = [q[(j + 2 + k) % len(q)] for k in range(len(q) - 2)]   # after a, up to before b
                    merged = p[:i + 1] + q_path + p[i + 1:]
                    if len(set(merged)) != len(merged):
                        continue
                    merged = _drop_collinear(merged, pos, normal)
                    mv = [pos[k] for k in merged]
                    if len(merged) > max_verts or not _convex(mv, normal):
                        continue
                    mp = plane_of(mv)
                    if mp is None or max(abs(_dist(mp, v)) for v in mv) > EPS * 0.5:
                        continue
                    alive[qi] = False
                    polys[pi] = merged
                    for k in range(len(merged)):
                        edge_owner[(merged[k], merged[(k + 1) % len(merged)])] = pi
                    changed = True
                    break
                if changed:
                    break
        for pi, p in enumerate(polys):
            if alive[pi]:
                out.append(Poly([pos[k] for k in p], key[0]))
    return out


def _split(verts, plane):
    """(negative part, positive part) of a convex polygon cut by a plane; either may be None."""
    neg, pos = [], []
    k = len(verts)
    ds = [_dist(plane, v) for v in verts]
    for i in range(k):
        a, b = verts[i], verts[(i + 1) % k]
        da, db = ds[i], ds[(i + 1) % k]
        if da <= EPS:
            neg.append(a)
        if da >= -EPS:
            pos.append(a)
        if (da < -EPS and db > EPS) or (da > EPS and db < -EPS):
            t = da / (da - db)
            m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
            neg.append(m)
            pos.append(m)

    def clean(vs):
        if len(vs) < 3:
            return None
        dedup = [v for i, v in enumerate(vs) if _dot(_sub(v, vs[i - 1]), _sub(v, vs[i - 1])) > 1e-8]
        if len(dedup) < 3 or area(dedup) < 1e-4:
            return None
        return dedup

    return clean(neg), clean(pos)


class _Item:
    __slots__ = ("poly", "plane", "area")

    def __init__(self, poly, plane):
        self.poly, self.plane, self.area = poly, plane, area(poly.verts)


def _classify(item, plane):
    """0 on the plane, -1 negative side, 1 positive side, 2 crossing."""
    lo = hi = 0.0
    for v in item.poly.verts:
        d = _dist(plane, v)
        lo, hi = min(lo, d), max(hi, d)
    if hi <= EPS and lo >= -EPS:
        return 0
    if hi <= EPS:
        return -1
    if lo >= -EPS:
        return 1
    return 2


def _choose(items, candidates=24, split_cost=4):
    """The plane (of one of up to `candidates` evenly picked polygons, larger ones first) with the fewest splits and
    the most even sides."""
    pick = sorted(items, key=lambda it: -it.area)[:candidates // 2]
    step = max(1, len(items) // (candidates - len(pick)))
    pick += items[::step][:candidates - len(pick)]
    best, best_score = None, None
    for cand in pick:
        n = neg = pos = splits = 0
        for it in items:
            c = _classify(it, cand.plane)
            if c == -1:
                neg += 1
            elif c == 1:
                pos += 1
            elif c == 2:
                splits += 1
            else:
                n += 1
        score = splits * split_cost + abs(neg - pos) - n
        if best_score is None or score < best_score:
            best, best_score = cand, score
    return best


def build(polys, candidates=24, split_cost=4):
    """A tree over convex polygons (Poly list, e.g. from merge_coplanar); None when there are none."""
    items = []
    for p in polys:
        pl = plane_of(p.verts)
        if pl is not None and len(p.verts) >= 3:
            items.append(_Item(p, pl))
    if not items:
        return None
    root = None
    stack = [(items, None, None)]           # (items, parent node, which child)
    while stack:
        its, parent, side = stack.pop()
        chosen = _choose(its, candidates, split_cost)
        plane = chosen.plane
        nd = Node(plane)
        neg, pos = [], []
        for it in its:
            c = 0 if it is chosen else _classify(it, plane)
            if c == 0:
                (nd.front if _dot(it.plane, plane) > 0 else nd.back).append(it.poly)
            elif c == -1:
                neg.append(it)
            elif c == 1:
                pos.append(it)
            else:
                a, b = _split(it.poly.verts, plane)
                if a:
                    neg.append(_Item(Poly(a, it.poly.surface), it.plane))
                if b:
                    pos.append(_Item(Poly(b, it.poly.surface), it.plane))
        if parent is None:
            root = nd
        elif side < 0:
            parent.neg = nd
        else:
            parent.pos = nd
        if pos:
            stack.append((pos, nd, 1))
        if neg:
            stack.append((neg, nd, -1))
    return root


def from_triangles(triangles, max_verts=MAX_VERTS, candidates=24, split_cost=4):
    """Tree bytes from [((v0, v1, v2), surface)] in the object's own space (cm), winding as the game's meshes
    (front = (v1 - v0) x (v2 - v0))."""
    return write(build(merge_coplanar(triangles, max_verts), candidates, split_cost))


# ---- queries (the game's traversal, for checks) ----
def _point_poly_dist2(c, verts, plane):
    """Squared distance from a point to a convex planar polygon."""
    d = _dist(plane, c)
    proj = (c[0] - plane[0] * d, c[1] - plane[1] * d, c[2] - plane[2] * d)
    inside = True
    k = len(verts)
    for i in range(k):
        a, b = verts[i], verts[(i + 1) % k]
        if _dot(_cross(_sub(b, a), _sub(proj, a)), plane[:3]) < 0:
            inside = False
            break
    if inside:
        return d * d
    best = None
    for i in range(k):
        a, b = verts[i], verts[(i + 1) % k]
        ab = _sub(b, a)
        L = _dot(ab, ab)
        t = 0.0 if L == 0 else max(0.0, min(1.0, _dot(_sub(c, a), ab) / L))
        q = (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t)
        e = _dot(_sub(c, q), _sub(c, q))
        best = e if best is None or e < best else best
    return best


def sphere_hits(root, centre, radius):
    """[(squared distance, Poly)] the sphere touches, found the way the game walks the tree (FUN_0054dfe0)."""
    out = []
    stack = [root] if root else []
    while stack:
        nd = stack.pop()
        d = _dist(nd.plane, centre)
        if d > radius + 1e-4:
            if nd.pos:
                stack.append(nd.pos)
            continue
        if d < -(radius + 1e-4):
            if nd.neg:
                stack.append(nd.neg)
            continue
        for flip, polys in ((1, nd.front), (-1, nd.back)):
            plane = tuple(x * flip for x in nd.plane)
            for p in polys:
                e = _point_poly_dist2(centre, p.verts, plane)
                if e <= radius * radius:
                    out.append((e, p))
        if nd.neg:
            stack.append(nd.neg)
        if nd.pos:
            stack.append(nd.pos)
    return out
