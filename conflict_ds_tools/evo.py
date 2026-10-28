"""Reader for Conflict: Desert Storm .EVO meshes (pure Python, no dependencies - shared by the Blender add-on
and the command-line converter evo_convert.py).

Layout follows the game's loader in DesertStorm.exe:
  file   = EOBJ objects back to back; each ends with NEXT (more follow) or DONE (last)
  EOBJ   FUN_004f7980: tag, u32 version 0x2f, model name[64], node name[64], u32 name hash, u32 node type,
         u32 flags, u32 geometry kind, 6 x u32, parent node name[64] ("ROOT" = none), u32,
         u32 n + n x {char[128] bone name, u32 node index} (skin bone list), u32 has-keys, char[128],
         char[128] (shadow: "NOSHADOW" / "(NULL)" / name), u32 BSPT count, u32 n + n x vec3 [+ 20 bytes],
         TEXL, MATL, BBOX, BSPH, BBOX, BSPH, geometry by kind, BSPT count x {u32 size, u32, [tag, u32 n, n B]},
         [keys: tag, u32, u32 n + n x {vec3 pos, vec3 unused, vec3 scale, quat xyzw}], NEXT/DONE
  TEXL   FUN_0054af90: u32 n + n x {u32 hash, char[64] name}   ("Default" = no texture / chosen by the game)
  MATL   FUN_0054b380: u32 n + n x {u32 hash, 4 f32, 4 f32, f32, 4 f32}
  BBOX   FUN_004f8410: tag, vec3 min, vec3 max, 6 x 16 B planes;  BSPH FUN_004f8460: tag, vec3, f32
  kind 0/3 static    FUN_00528330: VBOB, u32 n + n x VBUF
  VBUF   FUN_00525db0: tag, u32 version, [u32 external if version >= 2], u32 flags, u32 material hash,
         u32 texture hash, u32 is-strip, u32 vertex count, u32 strip count, u32 list count, u32,
         vertices (36 B: pos, normal, BGRA colour, uv), [strip u16s], list u16s, VSTP {u32 n, n B},
         [version 5: tag, u32 n, n x 2 B], [version > 4: FACE {u32 n, n x 12 B}],
         VBPL {u32 n, n x 4 B (version > 3) or n x 2 B}, [version >= 3: 2 x u32]
  kind 2 skinned     FUN_00528de0: tag, u32 has-strip, u32 vertex count, u32 strip count, u32 list count,
         u32 texture hash, u32 material hash, per vertex {u32 n, n x 44 B influence: pos, normal (both in
         the bone's space), BGRA colour, uv, u8 bone (-> bone list), 3 pad, f32 weight}, [strip u16s],
         list u16s, tag + u32, tag + u32 n + n x 29 B (triangle adjacency)
  kind 1 cloth       FUN_0052d600 / FUN_0052baa0: tag, u32 n, u32, n x {tag, u32 version, [u32 if version >= 2],
         u32 flags, u32 material, u32 texture, u32 is-strip, u32 vertex count, u32 strip count,
         u32 list count, u32 frames, vertices (36 B), (frames - 1) x vertex count x {pos, normal},
         [strip u16s], list u16s, [version > 2: tag, u32 n, n B]}

Header char[128] pair after has-keys: collision source ("#BSP_<name>" helper, "THIS" own mesh, "NOBSP"; the game
ignores it) and shadow ("NOSHADOW" / "(NULL)" / "#SHA_<name>").
  BSPT   {u32 size, u32 u, "BSPT", u32 size, tree}; the game loads only u = 0 (the collision tree), u = 1 is skipped.
         Tree (FUN_0055c790): node 32 B {plane n, d; offset of polys facing n; of polys facing -n; child on the
         negative side; child on the positive side} (offsets from the tree start, 0 = none, root at 0), laid out
         pre-order: node, its two poly blocks, negative subtree, positive subtree. Polys back to back {u16 n,
         u16 surface (row of Default.spl: 18 Metal, 32 Render Only ...), vec3 centre (vertex average), f32
         1.25 x max squared distance to the centre, n x vec3}, ended by u16 0, block padded to 16 bytes.
  FACE   (#SHA_ shadow meshes only) per list triangle: s8 x3 normal x 127, u8 0, u16 x3 neighbours across
         v0v1 / v1v2 / v2v0 (open edge 0xFFFF), 2 bytes unused. The game skips it and computes the table itself
         when it loads the shadow object (FUN_005227b0) - a writer can leave FACE empty.

Space: Direct3D, left-handed, Y up, centimetres. Node keys (only in the first object of a hierarchy, one per
object in file order) are local to the parent; the stored quaternion is the conjugate of the rotation
(verified: every skin influence of HERO01_ARMSTRONG lands on the same point to 1e-5).
"""
import math
import struct

from . import games

VERSION = 0x2F

# the points of an object (header: u32 n, n x vec3, then 5 x i32 = point index per role, -1 = none), in the object's
# space; found by role in every game model: guns' muzzle (just past the barrel) and shell ejection port, the M203's
# muzzle, launchers' back blast (role 2), ladders' bottom / top climb points
POINT_ROLES = ("MUZZLE", "LAUNCHER", "EJECT", "LADDER_BOTTOM", "LADDER_TOP")

# object flags
FLAG_HITBOX = 0x01      # "#HR_..." hit regions (no geometry)
FLAG_CONNECTOR = 0x02   # attachment points (weapon in hand, seats, exits ...)
FLAG_COLLISION = 0x80   # "#BSP_..." collision mesh
FLAG_SHADOW = 0x100     # "#SHA_..." shadow volume mesh


class EvoError(Exception):
    pass


class Reader:
    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def need(self, n):
        if n < 0 or self.p + n > len(self.d):
            raise EvoError("unexpected end of file at 0x%x (+%d)" % (self.p, n))

    def raw(self, n):
        self.need(n)
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self, n=1):
        self.need(4 * n)
        v = struct.unpack_from("<%df" % n, self.d, self.p)
        self.p += 4 * n
        return v if n > 1 else v[0]

    def u16s(self, n):
        self.need(2 * n)
        v = struct.unpack_from("<%dH" % n, self.d, self.p)
        self.p += 2 * n
        return list(v)

    def s(self, n):
        return self.raw(n).split(b"\0")[0].decode("latin-1")

    def tag(self, expect=None):
        t = self.raw(4)
        if expect and t != expect:
            raise EvoError("expected %s at 0x%x, found %r" % (expect.decode(), self.p - 4, t))
        return t


class Material:
    def __init__(self, hash_, a, b, power, c):
        self.hash = hash_
        self.colour_a = a
        self.colour_b = b
        self.power = power
        self.colour_c = c


class Mesh:
    """One draw batch: triangles over one vertex list with one texture."""

    def __init__(self):
        self.positions = []    # (x, y, z); skinned meshes: filled by Scene (bind pose, model space)
        self.normals = []
        self.colours = []      # (r, g, b, a) 0..1
        self.uvs = []          # (u, v), Direct3D convention (v = 0 at the top)
        self.triangles = []    # (a, b, c), clockwise front faces (Direct3D)
        self.texture = ""      # texture name, "" = none / "Default"
        self.texture_hash = 0
        self.material_hash = 0
        self.influences = None  # skinned: per vertex [(bone list index, weight, local pos, local normal)]
        self.frames = []       # cloth: extra frames [(positions, normals)]


class Node:
    def __init__(self):
        self.model = ""        # model name (same for every object of a file)
        self.name = ""         # node name ("" for single static objects)
        self.hash = 0
        self.type = 0          # 0 static, 4 hierarchy, 5 world piece (other values: unused on sub-objects)
        self.flags = 0
        self.kind = 0          # geometry: 0/3 static, 2 skinned, 1 cloth
        self.parent = ""       # parent node name, "" = none
        self.points = []
        self.textures = []     # [(hash, name)]
        self.materials = []
        self.bbox = None       # ((min), (max))
        self.meshes = []
        self.keys = []         # [(pos, scale, quat)] - one per object of the file
        self.bones = []        # skinned: [(bone node name, node index)]
        self.collision_source = ""   # "#BSP_<helper>", "THIS", "NOBSP" (the game ignores it)
        self.shadow_name = ""        # "#SHA_<object>", "NOSHADOW", "(NULL)"
        self.trees = []        # BSPT entries [(u, tree bytes)]; the game uses the u = 0 one (bsp.read)
        self.point_roles = (-1,) * 5   # points index per role (POINT_ROLES), -1 = none
        self.offset = 0

    @property
    def label(self):
        return self.name or self.model


def _triangles_from_strip(idx):
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, b, c) if i % 2 == 0 else (b, a, c))
    return tris


def _triangles(strip_idx, list_idx, nvert):
    if list_idx:
        tris = [tuple(list_idx[i:i + 3]) for i in range(0, len(list_idx) - 2, 3)]
    else:
        tris = _triangles_from_strip(strip_idx or [])
    return [t for t in tris if max(t) < nvert and len(set(t)) == 3]


def _colour(v):
    return ((v >> 16 & 0xFF) / 255.0, (v >> 8 & 0xFF) / 255.0, (v & 0xFF) / 255.0, (v >> 24) / 255.0)


def _vertices(r, m, n):
    r.need(36 * n)
    for _ in range(n):
        x, y, z, nx, ny, nz, c, u, v = struct.unpack_from("<6fI2f", r.d, r.p)
        r.p += 36
        m.positions.append((x, y, z))
        m.normals.append((nx, ny, nz))
        m.colours.append(_colour(c))
        m.uvs.append((u, v))


def _vertices_cv(r, m, n, colours, stride=32):
    """VBUF 7: n x {pos, normal, uv} (32 B; Global Storm level geometry 40 B: + lightmap uv), then n x BGRA colour
    when the buffer has colours (flag 0x80)."""
    r.need(stride * n)
    for _ in range(n):
        x, y, z, nx, ny, nz, u, v = struct.unpack_from("<8f", r.d, r.p)
        if stride == 40:
            m.uvs2.append(struct.unpack_from("<2f", r.d, r.p + 32))
        r.p += stride
        m.positions.append((x, y, z))
        m.normals.append((nx, ny, nz))
        m.uvs.append((u, v))
    if not colours:
        m.colours.extend([(1.0, 1.0, 1.0, 1.0)] * n)
        return
    r.need(4 * n)
    for c in struct.unpack_from("<%dI" % n, r.d, r.p):
        m.colours.append(_colour(c))
    r.p += 4 * n


def _read_vbuf(r, node):
    r.tag(b"VBUF")
    ver = r.u32()
    external = r.u32() if ver >= 2 else 0
    flags = r.u32()
    m = Mesh()
    m.material_hash = r.u32()
    m.texture_hash = r.u32()
    strip = r.u32()
    nvert, nstrip, nlist = r.u32(), r.u32(), r.u32()
    r.u32()
    split = ver >= 7 and getattr(node, "version", 0) & 0xFFFF >= 0x40   # DS2 has VBUF 7 too, with DS1's layout
    if split:
        # Vietnam / Global Storm: u32 0, u32 vertex count again, u32 stride (32; Global Storm level geometry 40), 3 x u32
        # (0 so far), split vertex / colour streams
        r.u32(); r.u32(); stride = r.u32(); r.raw(12)
        if stride not in (32, 40):
            raise EvoError("VBUF 7 with vertex stride %d" % stride)
    if external == 0 and split:
        m.uvs2 = []
        _vertices_cv(r, m, nvert, flags & 0x80, stride)
        strip_idx = r.u16s(nstrip) if strip else None
        m.triangles = _triangles(strip_idx, r.u16s(nlist), nvert)
    elif external == 0:
        _vertices(r, m, nvert)
        strip_idx = r.u16s(nstrip) if strip else None
        m.triangles = _triangles(strip_idx, r.u16s(nlist), nvert)
    r.tag()                           # VSTP
    r.raw(r.u32())
    if ver == 5:
        r.tag()
        r.raw(r.u32() * 2)
    if ver > 4:
        r.tag()                       # FACE
        r.raw(r.u32() * 12)
    r.tag()                           # VBPL: one value per strip triangle; Desert Storm skips it (0x525920)
    n = r.u32()
    m.vbpl = list(struct.unpack("<%di" % n, r.raw(4 * n))) if ver > 3 else list(r.u16s(n))
    if ver >= 3:
        m.vbuf_tail = struct.unpack("<2I", r.raw(8))
    m.vbuf_flags = flags
    if m.triangles:
        node.meshes.append(m)


def _read_water(r, node):
    """Vietnam geometry kind 4 (rivers, swamps; mapped from the data): "WCOB", u32 columns, u32 rows, u32 points
    (= columns x rows), u32 texture hash, u32 material hash, 52 bytes (wave settings?: u32, f32 x2, f32 1123.5, 75, 75,
    a plane), then points x {f32 x, f32 (0), f32 z, f32 u, f32 v, f32 y (water level), f32 (0)} row by row."""
    r.tag(b"WCOB")
    cols, rows, npts = r.u32(), r.u32(), r.u32()
    m = Mesh()
    m.texture_hash = r.u32()
    m.material_hash = r.u32()
    r.raw(52)
    r.need(28 * npts)
    for _ in range(npts):
        x, _a, z, u, v, y, _b = struct.unpack_from("<7f", r.d, r.p)
        r.p += 28
        m.positions.append((x, y, z))
        m.normals.append((0.0, 1.0, 0.0))
        m.uvs.append((u, v))
        m.colours.append((1.0, 1.0, 1.0, 1.0))
    if cols < 2 or cols * rows != npts:
        return
    P = m.positions

    def up(a, b, c):    # the stored order's cross product is the outward normal: keep it pointing up (+Y)
        (x0, _, z0), (x1, _, z1), (x2, _, z2) = P[a], P[b], P[c]
        return (a, b, c) if (z1 - z0) * (x2 - x0) - (x1 - x0) * (z2 - z0) > 0 else (a, c, b)
    for j in range(rows - 1):
        for i in range(cols - 1):
            a, b, c, d = j * cols + i, j * cols + i + 1, (j + 1) * cols + i, (j + 1) * cols + i + 1
            m.triangles += [up(a, b, c), up(b, d, c)]
    node.meshes.append(m)


def _read_skin(r, node):
    r.tag()
    has_strip = r.u32()
    nvert, nstrip, nlist = r.u32(), r.u32(), r.u32()
    m = Mesh()
    m.texture_hash = r.u32()
    m.material_hash = r.u32()
    m.influences = []
    for _ in range(nvert):
        n = r.u32()
        size = 44 if node.version & 0xFFFF < 0x35 else 42           # DS1 / Great Escape: 44
        r.need(size * n)
        infl = []
        for i in range(n):
            if size == 44:   # DS1: bone u8 + 3 bytes padding
                x, y, z, nx, ny, nz, c, u, v, bone, w = struct.unpack_from("<6fI2fB3xf", r.d, r.p)
            else:            # DS2+ (CSkinObject::Read 0x556B70): bone u16, no padding
                x, y, z, nx, ny, nz, c, u, v, bone, w = struct.unpack_from("<6fI2fHf", r.d, r.p)
            r.p += size
            infl.append((bone, w, (x, y, z), (nx, ny, nz)))
            if i == 0:
                m.colours.append(_colour(c))
                m.uvs.append((u, v))
        if not infl:
            m.colours.append((1.0, 1.0, 1.0, 1.0))
            m.uvs.append((0.0, 0.0))
        m.influences.append(infl)
    strip_idx = r.u16s(nstrip) if has_strip else None
    m.triangles = _triangles(strip_idx, r.u16s(nlist), nvert)
    r.tag()
    r.u32()
    r.tag()
    r.raw(r.u32() * 29)
    node.meshes.append(m)


def _read_cloth(r, node):
    r.tag()
    count = r.u32()
    r.u32()
    for _ in range(count):
        r.tag()
        ver = r.u32()
        if ver >= 2:
            r.u32()
        flags = r.u32()
        m = Mesh()
        m.material_hash = r.u32()
        m.texture_hash = r.u32()
        strip = r.u32()
        nvert, nstrip, nlist, nframes = r.u32(), r.u32(), r.u32(), r.u32()
        if node.version & 0xFFFF >= 0x40:
            _vertices_cv(r, m, nvert, False)   # Vietnam / Global Storm: pos, normal, uv (32 B); no colour array
        else:
            _vertices(r, m, nvert)
        for _f in range(1, nframes):
            r.need(24 * nvert)
            vals = struct.unpack_from("<%df" % (6 * nvert), r.d, r.p)
            r.p += 24 * nvert
            m.frames.append(([vals[i * 6:i * 6 + 3] for i in range(nvert)],
                             [vals[i * 6 + 3:i * 6 + 6] for i in range(nvert)]))
        strip_idx = r.u16s(nstrip) if strip else None
        m.triangles = _triangles(strip_idx, r.u16s(nlist), nvert)
        if ver > 2:
            r.tag()
            r.raw(r.u32())
        if m.triangles:
            node.meshes.append(m)


def read_node(r):
    n = Node()
    n.offset = r.p
    r.tag(b"EOBJ")
    # EOBJ version: low word 0x2F (DS1, FUN_004f7980), 0x35 (DS2, FUN_005087b0 "ReadInEnvObjectLevel"), 0x40 (Vietnam),
    # 0x49 (Global Storm); high word 0 or 3
    n.version = r.u32()
    if 0x54 <= n.version & 0xFFFF < 0x100 and n.version >> 16 in (0, 3):
        return _read_node_do(r, n)                     # Denied Ops (package streams, apkg.py)
    if n.version & 0xFFFF not in (VERSION, 0x34, 0x35, 0x40, 0x49) or n.version >> 16 not in (0, 2, 3):
        raise EvoError("unsupported EOBJ version 0x%x at 0x%x" % (n.version, n.offset))
    if n.version & 0xFFFF >= 0x40:
        # Vietnam / Global Storm (mapped from the data): u32 type, model[64], node[64], hash, flags, kind, 2 x u32
        # (counts?), u32, collision source[128], shadow[128], BSPT count, points - no parent / bone list / key index here
        n.type = r.u32()
        n.model = r.s(64)
        n.name = r.s(64)
        n.hash = r.u32()
        n.flags = r.u32()
        n.kind = r.u32()
        n.ds_counts = (r.u32(), r.u32())
        n.ds_u = r.u32()
        n.parent = ""
        n.key_index = -1
        has_keys = 0
        return _read_node_tail(r, n, has_keys)
    n.model = r.s(64)
    n.name = r.s(64)
    n.hash = r.u32()
    n.type = r.u32()
    n.flags = r.u32()
    n.kind = r.u32()
    r.raw(24 if n.version & 0xFFFF == VERSION else 20)          # DS2 / Great Escape (0x34): 20
    parent = r.s(64)
    n.parent = "" if parent == "ROOT" else parent
    if n.version & 0xFFFF == 0x34:
        # The Great Escape: the one u32 here is the object's key index (skin bones first) - DS2's scheme without
        # DS2's extra 65535 word; skin influences name key indices (see _cv_key_order)
        n.key_index = struct.unpack("<i", struct.pack("<I", r.u32()))[0]
    elif n.version & 0xFFFF == VERSION:
        # DS1: the object's slot (FUN_004f7980 -> object +0xF0): its key in the root's list, its animation track and
        # its instance matrix - the object's own index in every game file; an added skin object shares the main
        # skin's slot (an animation has one track per slot, read unchecked)
        n.key_index = struct.unpack("<i", struct.pack("<I", r.u32()))[0]
    else:
        r.u32()            # 65535 in DS2
    if n.version & 0xFFFF >= 0x35:
        # DS2: this object's entry in the root's key list, and the number a skin's bone list uses for it
        n.key_index = struct.unpack("<i", struct.pack("<I", r.u32()))[0]
    for _ in range(r.u32()):
        name = r.s(128)
        n.bones.append((name, r.u32()))
    has_keys = r.u32()
    return _read_node_tail(r, n, has_keys)


def _read_node_tail(r, n, has_keys):
    n.collision_source = r.s(128)
    n.shadow_name = r.s(128)
    nbspt = r.u32()
    if n.version & 0xFFFF >= 0x40:
        # Vietnam / Global Storm (SCON reader Vietnam 0x4FD34F, Global Storm 0x5FDBFE): u32 has-points, then when set
        # "SCON", u32 n, u32 a, u32 b; n > 0: n x vec3 + one i32 point index per role (Vietnam 7, Global Storm 12;
        # the first five kept as point_roles - DS1/2's roles, guns: role 0 muzzle); a > 0: 24 bytes; b > 0: 48 bytes
        n.ds_points_raw = None
        if r.u32():
            start = r.p
            r.tag(b"SCON")
            npts, a, b = r.u32(), r.u32(), r.u32()
            if npts:
                n.points = [r.f32(3) for _ in range(npts)]
                roles = struct.unpack("<%di" % (12 if n.version & 0xFFFF >= 0x49 else 7),
                                      r.raw(48 if n.version & 0xFFFF >= 0x49 else 28))
                n.point_roles, n.ds_point_roles = roles[:5], roles
            if a:
                n.ds_point_block_a = r.raw(24)
            if b:
                n.ds_point_pairs = struct.unpack("<12i", r.raw(48))
            n.ds_points_raw = r.d[start:r.p]
    else:
        npts = r.u32()
        if npts:
            n.points = [r.f32(3) for _ in range(npts)]
            n.point_roles = struct.unpack("<5i", r.raw(20))
    r.tag(b"TEXL")
    for _ in range(r.u32()):
        h = r.u32()
        n.textures.append((h, r.s(64)))
    r.tag(b"MATL")
    for _ in range(r.u32()):
        h = r.u32()
        n.materials.append(Material(h, r.f32(4), r.f32(4), r.f32(), r.f32(4)))
    for i in range(2):
        if i == 1 and r.d[r.p:r.p + 4] != b"BBOX":
            break                                    # Vietnam / Global Storm: one BBOX / BSPH pair
        r.tag(b"BBOX")
        box = (r.f32(3), r.f32(3))
        r.raw(96)
        if i == 0:
            n.bbox = box
        r.tag(b"BSPH")
        r.raw(16)
    if n.kind in (0, 3):
        r.tag(b"VBOB")
        for _ in range(r.u32()):
            _read_vbuf(r, n)
        if n.version & 0xFFFF >= 0x49 and n.kind == 0:
            # Global Storm kind 0 (0x5EF909): u32 LOD count L (0-7), then L more VBOB blocks (L = 0 in every file)
            n.ds_vbob_u = r.u32()
            n.lods = []
            for _ in range(n.ds_vbob_u):
                lod = Node()
                lod.version = n.version
                r.tag(b"VBOB")
                for _ in range(r.u32()):
                    _read_vbuf(r, lod)
                n.lods.append(lod.meshes)
    elif n.kind == 2:
        _read_skin(r, n)
    elif n.kind == 1:
        _read_cloth(r, n)
    elif n.kind == 4:
        _read_water(r, n)
    else:
        raise EvoError("unknown geometry kind %d" % n.kind)
    for _ in range(nbspt):
        size = r.u32()
        u = r.u32()
        tree = b""
        if size:
            r.tag()
            tree = r.raw(r.u32())
        n.trees.append((u, bytes(tree)))
    if n.version & 0xFFFF >= 0x40 and r.d[r.p:r.p + 4] not in (b"DONE", b"NEXT"):
        # Vietnam / Global Storm: the hierarchy block follows the collision trees (mapped from the data): char[64] (a node / part
        # name), u32 hash, parent node name[64] ("ROOT" = none), second BBOX / BSPH, u32 (65535), i32 key index,
        # u32 n + n x {char[128] bone, u32 node}, u32 has-keys, char[128], then the keys ("TMDT", u32 n, 52 B each)
        n.ds_part = r.s(64)
        r.u32()
        parent = r.s(64)
        n.parent = "" if parent == "ROOT" else parent
        r.tag(b"BBOX"); r.raw(24 + 96)
        r.tag(b"BSPH"); r.raw(16)
        r.u32()
        n.key_index = struct.unpack("<i", struct.pack("<I", r.u32()))[0]
        for _ in range(r.u32()):
            name = r.s(128)
            n.bones.append((name, r.u32()))
        has_keys = r.u32()
        r.raw(128)
    if has_keys:
        r.tag()
        if n.version & 0xFFFF == VERSION:
            r.u32()           # DS1 only - the Great Escape's / DS2's ReadTransformData (0x508630) reads tag, count
        for _ in range(r.u32()):
            pos, _unused, scale, quat = r.f32(3), r.f32(3), r.f32(3), r.f32(4)
            n.keys.append((pos, scale, quat))
    end = r.tag()
    if end not in (b"DONE", b"NEXT"):
        raise EvoError("expected DONE/NEXT at 0x%x, found %r" % (r.p - 4, end))
    names = {h: s for h, s in n.textures}
    for m in n.meshes:
        name = names.get(m.texture_hash, "")
        m.texture = "" if name.lower() == "default" else name
        # Vietnam: untextured buffers with VBUF flag 0x04 and without 0x01 (0x4E4, INVISPOLY 0xE4) are invisible
        # blockers; drawn buffers have 0x01 (0xC1, 0x41, 0x4E5). From the data - the renderer's test isn't traced.
        fl = getattr(m, "vbuf_flags", None)
        m.invisible = fl is not None and not m.texture and bool(fl & 0x04) and not fl & 0x01
    return n


def _lstr(r):
    n = r.u32()
    return r.raw(n).split(b"\0")[0].decode("latin-1")


def _do_assetref(r):
    """An asset reference (0x6F34D0): GUID, class hash, asset name, package name - returns the name."""
    r.raw(12)
    name = _lstr(r)
    _lstr(r)
    return name


def _do_bbox(r):
    r.tag(b"BBOX")
    box = (r.f32(3), r.f32(3))
    r.raw(96)
    return box


def _do_vbuf(r, node, keep):
    """CVBufferObject::Read 0x76C480 (package mode): material reference instead of texture / material hashes,
    32 / 40-byte vertices (pos, normal, uv[, uv2]), optional colour stream, strip / restart list / triangle list,
    from version 10 a triangle octree, VSTP / FACE / VBPL, 2 x u32, tangents."""
    r.tag(b"VBUF")
    ver = r.u32()
    if ver > 1:
        r.u32()                                       # external
    flags = r.u32()
    material = _do_assetref(r)
    r.u32(); r.u32()                                  # material keys
    is_strip = r.u32()
    nvert, nstrip, nextra, nlist = r.u32(), r.u32(), r.u32(), r.u32()
    r.u32()
    u28 = r.u32()
    r.u32(); r.u32()
    uvsets = r.u32() if ver >= 9 else (2 if u28 else 1)
    if ver >= 9:
        r.u32()
    stride = {1: 32, 2: 40}.get(uvsets)
    if stride is None:
        raise EvoError("VBUF with %d uv sets at 0x%x" % (uvsets, r.p))
    m = Mesh()
    if stride == 40:
        m.uvs2 = []
    _vertices_cv(r, m, nvert, flags & 0x80, stride)
    if is_strip & 1:
        strip = r.u16s(nstrip)
        r.raw(2 * nextra)
        lst = r.u16s(nlist)
    else:
        strip, lst = None, r.u16s(nlist)
    m.triangles = _triangles(strip, lst, nvert)
    if ver > 9:                                       # CTriListOcTree::Read 0x782810
        n = r.u32()
        _do_bbox(r)
        r.raw(3 * n)
    r.tag(); r.raw(r.u32())                           # VSTP
    if ver == 5:
        r.tag(); r.raw(2 * r.u32())
    if ver > 4:
        r.tag(); r.raw(12 * r.u32())                  # FACE
    r.tag(); r.raw(r.u32() * (2 if ver < 4 else 4))   # VBPL
    if ver >= 3:
        r.raw(8)
    r.tag(); r.raw(24 * r.u32())                      # tangents
    m.material = material
    m.texture = material                              # resolved to the material's diffuse texture by AssetFinder
    m.vbuf_flags = flags
    if keep and m.triangles:
        node.meshes.append(m)


def _do_vbob(r, node, keep=True):
    r.tag(b"VBOB")
    for _ in range(r.u32()):
        _do_vbuf(r, node, keep)


def _do_vaob(r, node):
    """Kind 1 animated vertex lists (cloth, tank tracks, the bird, the multiplayer flag): read by evo54.py's VAOB
    reader, each list a Mesh - frame 1 = positions / normals / uvs, the others Mesh.frames (as DS1's cloth)."""
    from . import evo54
    er = evo54._R(r.d, r.p)
    a = evo54._read_vaob(er)
    r.p = er.p
    for v in a["lists"]:
        m = Mesh()
        m.material = m.texture = evo54.text(v["material"]["name"])
        n = v["nvert"]
        first = struct.unpack_from("<%df" % (8 * n), v["verts"], 0) if n else ()
        m.positions = [first[i * 8:i * 8 + 3] for i in range(n)]
        m.normals = [first[i * 8 + 3:i * 8 + 6] for i in range(n)]
        m.uvs = [first[i * 8 + 6:i * 8 + 8] for i in range(n)]
        m.colours = [(1.0, 1.0, 1.0, 1.0)] * n
        for f in range(1, v["frames"]):
            vals = struct.unpack_from("<%df" % (6 * n), v["verts"], 32 * n + 24 * n * (f - 1))
            m.frames.append(([vals[i * 6:i * 6 + 3] for i in range(n)], [vals[i * 6 + 3:i * 6 + 6] for i in range(n)]))
        strip = list(struct.unpack("<%dH" % v["nstrip"], v["strip"])) if v["strip"] else None
        lst = list(struct.unpack("<%dH" % v["nlist"], v["list"])) if v["list"] else []
        m.triangles = _triangles(strip, lst, n)
        node.meshes.append(m)


def _do_skin(r, node):
    """CSkinObject::Read 0x7845A0 (package mode): tag, u32 strip flag, vertex / strip / restart / list counts, the
    material reference, per vertex u32 n + n x 42-byte influences (pos, normal in the bone's space, colour, uv, u16
    bone, f32 weight), indices (strip flag: strip + restart + list, the list unused by the game), EDGE {u32 n, n B} /
    SSTP {u32} chunks up to SHAD {u32 n, n x 29 B}, then tangents (tag, u32 n, n x 24 B; 0x784480)."""
    r.tag()
    is_strip = r.u32()
    nvert, nstrip, nextra, nlist = r.u32(), r.u32(), r.u32(), r.u32()
    m = Mesh()
    m.material = _do_assetref(r)
    r.u32(); r.u32()                                  # material keys (as in VBUF)
    m.texture = m.material
    m.influences = []
    for _ in range(nvert):
        n = r.u32()
        r.need(42 * n)
        infl = []
        for i in range(n):
            x, y, z, nx, ny, nz, c, u, v, bone, w = struct.unpack_from("<6fI2fHf", r.d, r.p)
            r.p += 42
            infl.append((bone, w, (x, y, z), (nx, ny, nz)))
            if i == 0:
                m.colours.append(_colour(c))
                m.uvs.append((u, v))
        if not infl:
            m.colours.append((1.0, 1.0, 1.0, 1.0))
            m.uvs.append((0.0, 0.0))
        m.influences.append(infl)
    if is_strip & 1:
        strip = r.u16s(nstrip)
        r.raw(2 * nextra)
        lst = r.u16s(nlist)
    else:
        strip, lst = None, r.u16s(nlist)
    m.triangles = _triangles(strip, lst, nvert)
    while True:
        tag = r.tag()
        if tag == b"SHAD":
            r.raw(29 * r.u32())
            break
        if tag == b"EDGE":
            r.raw(r.u32())
        elif tag == b"SSTP":
            r.u32()
        else:
            raise EvoError("unexpected %r in a skin at 0x%x" % (tag, r.p - 4))
    r.tag()
    r.raw(24 * r.u32())
    node.meshes.append(m)


def is_world_piece(node):
    """A level / world-piece model's first object: type 5 in Desert Storm, Desert Storm II and The Great Escape,
    type 3 ("landscape", the loader the first object's type picks) in Vietnam / Global Storm. Denied Ops: the type-5
    rule, not checked against its levels."""
    v = node.version & 0xFFFF
    return node.type == (3 if 0x40 <= v < 0x54 else 5)


def _read_node_do(r, n):
    """Denied Ops EOBJ 0x54 (CEnvObject::ReadInEnvObjectLevel 0x764F60, package mode): u32 type, model / node as
    length-prefixed strings, hash, flags, kind, 3 x u32, collision source[128], shadow[128], BSPT count, points,
    char[0x23], u32, [TEXL outside packages], BBOX, BSPH, geometry (kind 0/5: VBOB + LOD VBOBs, 3: VBOB, 6: none),
    BSPT entries, then the physics / hierarchy trailer (node, hash, parent ...)."""
    n.type = r.u32()
    n.model = _lstr(r)
    n.name = _lstr(r)
    n.hash, n.flags, n.kind = r.u32(), r.u32(), r.u32()
    r.raw(12)
    n.collision_source = r.s(128)
    n.shadow_name = r.s(128)
    nbspt = r.u32()
    if r.u32():                                       # points (0x786390)
        r.tag()
        npts, m, k = r.u32(), r.u32(), r.u32()
        n.points = [r.f32(3) for _ in range(npts)]
        roles = struct.unpack("<8i", r.raw(32)) if npts else (-1,) * 8
        n.point_roles = roles[:5]
        r.raw((12 if m else 0) + (24 if k else 0))
    r.raw(0x23)
    r.u32()
    if r.d[r.p:r.p + 4] == b"TEXL":
        r.tag()
        for _ in range(r.u32()):
            h = r.u32()
            n.textures.append((h, r.s(64)))
    if r.d[r.p:r.p + 4] == b"MATL":
        raise EvoError("MATL in a Denied Ops object (not a package stream) at 0x%x" % r.p)
    n.bbox = _do_bbox(r)
    r.tag(b"BSPH"); r.raw(16)
    if n.kind in (0, 5):
        _do_vbob(r, n)
        for _ in range(r.u32() & 7):                  # lower levels of detail
            _do_vbob(r, n, keep=False)
    elif n.kind == 3:
        _do_vbob(r, n)
    elif n.kind == 2:
        _do_skin(r, n)
    elif n.kind == 1:
        _do_vaob(r, n)
    elif n.kind != 6:
        raise EvoError("Denied Ops geometry kind %d isn't read yet" % n.kind)
    if n.kind != 6:
        for _ in range(nbspt & 7):
            size = r.u32()
            if size == 0x445113AF:                    # end marker (the reader steps back)
                r.p -= 4
                break
            u = r.u32()
            tree = b""
            if size:
                r.tag()
                tree = r.raw(r.u32())
            n.trees.append((u, bytes(tree)))
    if n.type == 7 and r.d[r.p:r.p + 4] not in (b"NEXT", b"DONE"):
        r.u32()                                       # CShatterEnvObject: one u32 after the BSPT entries (evo54.py)
    n.parent, n.key_index = "", -1
    if r.d[r.p:r.p + 4] not in (b"NEXT", b"DONE"):
        # CPhysicsEnvObject 0x769970 / CHierObject 0x76EE70: node, hash, parent ("ROOT"), then (not on type 7-9
        # objects) the physics name[128], 2 x vec3, name[128]; CHierObject continues with BBOX, BSPH, 2 x u32, bones,
        # has-keys, skeleton[128], skeleton reference, keys (tag, u32 n, 52 B each)
        n.ds_part = _lstr(r)
        r.u32()
        parent = _lstr(r)
        n.parent = "" if parent.upper() == "ROOT" else parent
        if r.d[r.p:r.p + 4] not in (b"NEXT", b"DONE"):
            n.physics_name = r.s(128)
            r.raw(24)
            r.raw(128)
            if r.d[r.p:r.p + 4] == b"BBOX":
                _do_bbox(r)
                r.tag(b"BSPH"); r.raw(16)
                r.u32()                               # a group / flag value (CHierObject +0x1F4)
                n.key_index = struct.unpack("<i", struct.pack("<I", r.u32()))[0]   # this object's key (+0x1EC)
                for _ in range(r.u32()):
                    name = r.s(128)
                    n.bones.append((name, r.u32()))
                has_keys = r.u32()
                n.skeleton = r.s(128)                 # the shared skeleton of a character body (C5_SKELETON)
                _do_assetref(r)
                if has_keys:
                    r.tag()
                    for _ in range(r.u32()):
                        pos, _unused, scale, quat = r.f32(3), r.f32(3), r.f32(3), r.f32(4)
                        n.keys.append((pos, scale, quat))
    end = r.tag()
    if end not in (b"DONE", b"NEXT"):
        raise EvoError("expected DONE/NEXT at 0x%x, found %r" % (r.p - 4, end))
    for m in n.meshes:
        m.invisible = False
    return n


def read(path_or_bytes):
    """All objects of an .EVO file (list of Node)."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as f:
            data = f.read()
    r = Reader(data)
    nodes = []
    while r.p + 4 <= len(data):
        try:
            nodes.append(read_node(r))
        except EvoError as e:
            # Denied Ops breakable (shatter) models run past the object layout the reader knows (the 3 of Comms Base's
            # 754 that fail this way are all *_DYN shatter models) - say so instead of a bare "end of file"
            if data[:4] == b"EOBJ" and 0x54 <= struct.unpack_from("<I", data, 4)[0] & 0xFFFF < 0x100 and \
                    "unexpected end of file" in str(e):
                raise EvoError("a Denied Ops object layout the reader doesn't know yet - breakable (shatter) "
                               "models aren't read yet (%s)" % e) from None
            raise
    if not nodes:
        raise EvoError("no objects in file")
    _cv_key_order(nodes)
    return nodes


def _cv_key_order(nodes):
    """DS2 keeps the root's keys in key-index order and a skin's bone list names key indices, not object indices
    (each object's header carries its key index). Re-order both to object order - what Scene / the writers / Blender
    expect; the file's originals stay in ``keys_raw`` / ``bones_raw``. Verified on RIFLEMAN_DF_STANDARD: every
    multi-bone vertex lands on one point (spread 0.0) only this way."""
    keys = nodes[0].keys
    idx = [getattr(n, "key_index", -1) for n in nodes]
    # every key used, every object on a key; more objects than keys = added skin objects sharing the main skin's key
    # (multi-part characters, evo_write.rebuild). More keys than objects (Global Storm vehicles: C04_BMP1 has 27 keys
    # for 24 objects) is fine while every object has a key of its own - without this the turrets / guns on their
    # connectors stayed at the model's origin
    if not keys or any(not 0 <= k < len(keys) for k in idx):
        return
    spare = len(nodes) < len(keys)
    if (spare and len(set(idx)) != len(idx)) or (not spare and len(set(idx)) != len(keys)):
        return
    if nodes[0].version & 0xFFFF == VERSION:
        # DS1: keys and bone lists are already in object order; only the extra skin objects need their key
        if len(nodes) > len(keys):
            nodes[0].keys_raw = list(keys)
            nodes[0].keys = [keys[k] for k in idx]
        return
    nodes[0].keys_raw = list(keys)
    nodes[0].keys = [keys[k] for k in idx]
    obj_of = {}
    for i, k in enumerate(idx):
        obj_of.setdefault(k, i)          # a shared key belongs to the first object on it (the template's)
    # an influence's bone number IS a key index (DS1: a position in the skin's bone list) -> a bone list in key
    # order makes bones[number] the right object again
    if spare:
        return                           # a bone list needs every key on an object (characters always have that)
    by_key = [(nodes[obj_of[k]].name, obj_of[k]) for k in range(len(keys))]
    for n in nodes:
        if n.bones:
            n.bones_raw = list(n.bones)
            n.bones = by_key


# ---------------------------------------------------------------------------------------------------- scene --

def mat_identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def mat_from_trs(pos, quat, scale):
    """Column-vector 4x4 (v' = M v) from translation, rotation quaternion (x, y, z, w) and scale."""
    x, y, z, w = quat
    ln = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / ln, y / ln, z / ln, w / ln
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    m = mat_identity()
    for i in range(3):
        for j in range(3):
            m[i][j] = r[i][j] * scale[j]
        m[i][3] = pos[i]
    return m


def mat_apply(m, v, w=1.0):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] + m[i][3] * w for i in range(3))


def mat_invert_affine(m):
    a = [row[:3] for row in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        return mat_identity()
    inv = [[(a[(j + 1) % 3][(i + 1) % 3] * a[(j + 2) % 3][(i + 2) % 3]
             - a[(j + 1) % 3][(i + 2) % 3] * a[(j + 2) % 3][(i + 1) % 3]) / det for j in range(3)] for i in range(3)]
    t = [-sum(inv[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [inv[0] + [t[0]], inv[1] + [t[1]], inv[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def _normalise(v):
    ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / ln, v[1] / ln, v[2] / ln) if ln > 1e-12 else (0.0, 1.0, 0.0)


class SceneObject:
    def __init__(self, node, index):
        self.node = node
        self.index = index
        self.name = node.label
        self.parent = None     # index into Scene.objects
        self.local = mat_identity()
        self.world = mat_identity()
        self.children = []
        self.role = "mesh"     # mesh, skin, bone, connector, collision, shadow, hitbox

    @property
    def is_helper(self):
        return self.role in ("connector", "collision", "shadow", "hitbox", "bone")


class Scene:
    """Objects of one file with their transforms (game space) and skinned meshes posed in model space."""

    def __init__(self, nodes, name=""):
        self.nodes = nodes
        self.name = name or nodes[0].model
        self.objects = [SceneObject(n, i) for i, n in enumerate(nodes)]
        keys = nodes[0].keys if len(nodes[0].keys) == len(nodes) else None
        cv = getattr(nodes[0], "version", 0) & 0xFFFF >= 0x40
        by_name = {}
        for o in self.objects:
            if o.node.name:
                by_name.setdefault(o.node.name.upper(), o.index)
        for o in self.objects:
            n = o.node
            if n.parent and n.parent.upper() in by_name and by_name[n.parent.upper()] != o.index:
                o.parent = by_name[n.parent.upper()]
                self.objects[o.parent].children.append(o.index)
            if keys:
                pos, scale, q = keys[o.index]
                if cv:   # Vietnam / Global Storm (0x40+) store the rotation itself (bodies stand 1.8 m in T-pose)
                    o.local = mat_from_trs(pos, q, scale)
                else:
                    o.local = mat_from_trs(pos, (-q[0], -q[1], -q[2], q[3]), scale)
            if n.kind == 2:
                o.role = "skin"
            elif n.flags & FLAG_COLLISION:
                o.role = "collision"
            elif n.flags & FLAG_SHADOW:
                o.role = "shadow"
            elif n.flags & FLAG_CONNECTOR:
                o.role = "connector"
            elif n.flags & FLAG_HITBOX or not n.meshes:
                o.role = "hitbox" if n.flags & FLAG_HITBOX else "empty"
            elif n.kind == 3:
                o.role = "bone"
        done = set()

        def world(i, depth=0):
            o = self.objects[i]
            if i in done or depth > 256:
                return o.world
            o.world = mat_mul(world(o.parent, depth + 1), o.local) if o.parent is not None else o.local
            done.add(i)
            return o.world

        for o in self.objects:
            world(o.index)
        self.skinned = any(o.role == "skin" for o in self.objects)
        # bones = objects referenced by a skin + their ancestors (characters: the whole biped)
        self.bone_ids = set()
        for o in self.objects:
            if o.role == "skin":
                for _, idx in o.node.bones:
                    j = idx if idx < len(self.objects) else None
                    while j is not None and j not in self.bone_ids:
                        self.bone_ids.add(j)
                        j = self.objects[j].parent
                self._pose_skin(o)
        if self.skinned:
            for o in self.objects:
                # a skin object inside the skeleton is a Desert Storm bone carrying an extra skin part
                # (evo_write._hosted_skin): still a bone of the armature (and of the animations)
                if o.role == "bone" or (o.role == "skin" and o.parent is not None and o.node.version & 0xFFFF == VERSION):
                    self.bone_ids.add(o.index)

    def _pose_skin(self, o):
        bones = o.node.bones
        for m in o.node.meshes:
            m.positions, m.normals = [], []
            for infl in m.influences:
                p, nrm, total = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0
                for bone, w, lp, ln in infl:
                    if bone >= len(bones) or bones[bone][1] >= len(self.objects):
                        continue
                    bw = self.objects[bones[bone][1]].world
                    wp, wn = mat_apply(bw, lp), mat_apply(bw, ln, 0.0)
                    for k in range(3):
                        p[k] += w * wp[k]
                        nrm[k] += w * wn[k]
                    total += w
                if total > 0:
                    p = [c / total for c in p]
                m.positions.append(tuple(p))
                m.normals.append(_normalise(nrm))

    def vertex_weights(self, o, mesh):
        """Per vertex [(bone object index, weight)] normalised, merged per bone."""
        bones = o.node.bones
        out = []
        for infl in mesh.influences:
            acc = {}
            for bone, w, _, _ in infl:
                if bone < len(bones) and bones[bone][1] < len(self.objects):
                    acc[bones[bone][1]] = acc.get(bones[bone][1], 0.0) + w
            total = sum(acc.values()) or 1.0
            out.append(sorted(((b, w / total) for b, w in acc.items()), key=lambda t: -t[1]))
        return out


def load(path, only=None):
    """Scene of a file; only = one object's index (a zone chunk "<MODEL>NNN" placed on its own - world pieces are
    in world space, so the object alone is complete)."""
    nodes = read(path)
    if only is not None:
        return Scene([nodes[only]])
    do_skel = next((getattr(n, "skeleton", "") for n in nodes if n.kind == 2 and getattr(n, "skeleton", "")), "")
    if do_skel:
        import os
        sk = os.path.join(os.path.dirname(os.path.abspath(path)), do_skel + ".EVO")
        if os.path.exists(sk) and os.path.abspath(sk) != os.path.abspath(path):
            nodes = attach_do_skeleton(nodes, read(sk))
        # the bind pose of Denied Ops bodies isn't decoded yet: drawn from the skeleton's keys the influences of a
        # vertex land up to 20-30 cm apart - refuse instead of drawing a broken body (the editor shows a marker)
        if _skin_spread(nodes, 100) > DO_SKIN_TOLERANCE:
            raise EvoError("Denied Ops character bodies can't be posed yet (skeleton bind pose not decoded)")
        return Scene(nodes)
    if _needs_skeleton(nodes):
        import copy
        import os
        folder = os.path.dirname(os.path.abspath(path))
        best = None
        for name in SKELETON_FILES:
            sk = os.path.join(folder, name)
            if not os.path.exists(sk):
                continue
            # nothing in the body file names its skeleton (Global Storm: male C04_SKELETON / female
            # C04_SKELETON_FEMALE, different bone order) - take the one the influences fit: with the right skeleton
            # every vertex's influences land on one point
            cand = attach_skeleton(copy.deepcopy(nodes), read(sk))
            spread = _skin_spread(cand)
            if best is None or spread < best[0]:
                best = (spread, cand, sk)
        if best:
            nodes = best[1]
            scene = Scene(nodes)
            scene.path, scene.skeleton_path = path, best[2]     # Global Storm animations bind through its .SKL
            return scene
    scene = Scene(nodes)
    scene.path = path
    return scene


# Soldiers (Vietnam, Global Storm): the body file holds only the skinned mesh; its influences name key indices of a shared skeleton
SKELETON_FILES = ("C04_SKELETON.EVO", "C04_SKELETON_FEMALE.EVO", "MPD_SKELETON.EVO")   # Global Storm, Vietnam


def _skin_spread(nodes, sample=400):
    """Mean distance between where a vertex's influences put it (0 = the skeleton fits), over a sample of vertices."""
    s = Scene(nodes)
    total, n = 0.0, 0
    for o in s.objects:
        if o.role != "skin":
            continue
        bones = o.node.bones
        for m in o.node.meshes:
            step = max(1, len(m.influences) // sample)
            for infl in m.influences[::step]:
                pts = [mat_apply(s.objects[bones[b][1]].world, lp) for b, _w, lp, _n in infl
                       if b < len(bones) and bones[b][1] < len(s.objects)]
                if len(pts) < 2:
                    continue
                total += max(sum((a - c) ** 2 for a, c in zip(p, pts[0])) ** 0.5 for p in pts[1:])
                n += 1
    return total / n if n else float("inf")


def _needs_skeleton(nodes):
    for n in nodes:
        for m in n.meshes:
            infl = getattr(m, "influences", None)
            if infl and max((b for i in infl for b, *_ in i), default=0) >= len(nodes):
                return True
    return False


DO_SKIN_TOLERANCE = 1.0    # cm


def attach_do_skeleton(skin_nodes, skeleton):
    """Denied Ops: as attach_skeleton - the skeleton's keys are stored in key-index order (each object's hierarchy
    trailer names its key, CHierObject::Read 0x76EE70; read() puts them in object order) and an influence's bone
    number is a key index (C5_SKELETON: bones 0..31, ROOT_OBJECT 32, eyes / connectors 33..50). Rotations as
    stored, not conjugated."""
    return attach_skeleton(skin_nodes, skeleton)


def attach_skeleton(skin_nodes, skeleton):
    """The skeleton's objects followed by the skin objects, whose bone lists map an influence's number (a key index
    of the skeleton) to that skeleton object. One identity key is added per skin object."""
    idx = [getattr(n, "key_index", -1) for n in skeleton]
    by_key = {k: i for i, k in enumerate(idx) if k >= 0}
    top = max(by_key) + 1 if by_key else 0
    bones = [(skeleton[by_key[k]].name, by_key[k]) if k in by_key else ("", 0) for k in range(top)]
    out = list(skeleton)
    root_keys = list(skeleton[0].keys)
    for n in skin_nodes:
        if n.bones or any(getattr(m, "influences", None) for m in n.meshes):
            n.bones = bones
        n.parent = ""
        out.append(n)
        root_keys.append(((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)))
    skeleton[0].keys = root_keys if len(root_keys) == len(out) else skeleton[0].keys
    return out




# --------------------------------------------------------------------------------------------------- assets --

TEXTURE_EXTS = (".png", ".dds", ".tga", ".bmp")


def _lfsr_byte(x, c):
    for b in range(8):
        i = ((c >> b) & 1) ^ (x & 1) ^ ((x >> 1) & 1) ^ ((x >> 21) & 1) ^ ((x >> 31) & 1)
        x = ((x << 1) & 0xFFFFFFFF) | i
    return x


# the LFSR step is linear over GF(2): state' = A(state) ^ B(byte), with A split into one table per state byte
_HA = [[_lfsr_byte(v << (8 * k), 0) for v in range(256)] for k in range(4)]
_HB = [_lfsr_byte(0, c) for c in range(256)]


def archive_hash(name, state=1, upper=None):
    """Name hash of the game's .dat archives: LFSR over the name - upper-cased in Desert Storm (FUN_004b9a10),
    lower-cased from Desert Storm II on (DS2 FUN_004c0b30; "mission01.env" = 0x5A909764), games.current() decides -
    start 1, per bit LSB first (in = bit ^ h0 ^ h1 ^ h21 ^ h31, h = h << 1 | in). Entries whose name is unknown are
    extracted as _<HASH>.<EXT>. state continues a previous hash: archive_hash("B", archive_hash("A")) ==
    archive_hash("AB"); upper = force the case (None: the current game's)."""
    a0, a1, a2, a3 = _HA
    b = _HB
    x = state
    if upper is None:
        upper = games.current().hash_upper
    for c in (name.upper() if upper else name.lower()).encode("latin-1", "replace"):
        x = a0[x & 255] ^ a1[x >> 8 & 255] ^ a2[x >> 16 & 255] ^ a3[x >> 24] ^ b[c]
    return x


class AssetFinder:
    """Finds extracted files by name (case-insensitive): first in the model's folder, then in its sibling folders
    (the extractor writes one folder per archive) and any extra folders. Files extracted without a name
    (_<HASH>.<EXT>) are found through the archive hash of NAME.EXT."""

    def __init__(self, model_path=None, extra_dirs=(), siblings=True):
        import os
        self.by_stem = {}              # STEM -> {".ext": path}, first folder wins
        dirs = []
        if model_path:
            here = os.path.dirname(os.path.abspath(model_path))
            dirs.append(here)
            if siblings:
                parent = os.path.dirname(here)
                try:
                    dirs += sorted(os.path.join(parent, d) for d in os.listdir(parent)
                                   if os.path.isdir(os.path.join(parent, d)) and not d.startswith("_"))
                except OSError:
                    pass
        dirs += [d for d in extra_dirs if d]
        self.dirs = dirs
        self.materials = {}            # Denied Ops: material name -> its texture slots (apkg.py _MATERIALS.TSV)
        for d in dirs:
            try:
                files = os.listdir(d)
            except OSError:
                continue
            for f in files:
                stem, ext = os.path.splitext(f)
                self.by_stem.setdefault(stem.upper(), {}).setdefault(ext.lower(), os.path.join(d, f))
            if "_MATERIALS.TSV" in (f.upper() for f in files):
                from . import apkg
                for k, v in apkg.read_materials(d).items():
                    self.materials.setdefault(k, v)

    def find(self, name, exts=TEXTURE_EXTS):
        if not name:
            return None
        stem = name.upper()
        known = tuple(e.upper() for e in TEXTURE_EXTS + (".evo", ".prb", ".dyn", ".txt", ".rfx"))
        if stem.endswith(known):
            stem = stem.rsplit(".", 1)[0]
        hit = self.by_stem.get(stem, {})
        for e in exts:
            if e in hit:
                return hit[e]
        slots = self.materials.get(stem)
        if slots and slots[0] and slots[0].upper() != stem:   # Denied Ops: a material - its diffuse texture
            return self.find(slots[0], exts)
        for e in exts:
            hashed = self.by_stem.get("_%08X" % archive_hash(stem + e.upper()))
            if hashed:
                for e2 in exts + (".bin",):
                    if e2 in hashed:
                        return hashed[e2]
                return next(iter(hashed.values()))
        return None

    def files(self, ext):
        """Paths of all files with an extension (one per name)."""
        return [v[ext] for v in self.by_stem.values() if ext in v]

    def animations(self):
        """{name: path} of all .prb animations, including unnamed ones (_HASH.PRB / _HASH.BIN with a prb header), and Global
        Storm's .ani (<NAME>.ANI, or _HASH.BIN from an older extraction)."""
        out = {}
        for stem, v in self.by_stem.items():
            if ".prb" in v:
                out.setdefault(stem, v[".prb"])
            elif ".ani" in v:                                         # Global Storm, named by the extraction
                out.setdefault(stem, v[".ani"])
            elif stem.startswith("_") and ".bin" in v:
                try:
                    with open(v[".bin"], "rb") as f:
                        head = f.read(12)
                    if head[:4] == b"\x02\x00\x00\x00" and 0 < struct.unpack_from("<I", head, 4)[0] < 256:
                        out.setdefault(stem, v[".bin"])
                    elif head[:6] in (b"STATE_", b"OVERLA"):         # Global Storm .ani: named by its header
                        with open(v[".bin"], "rb") as f:
                            data = f.read()
                        if is_ani_cg(data):
                            out.setdefault(data[:44].split(b"\0")[0].decode("latin-1"), v[".bin"])
                except OSError:
                    pass
        return out


TextureFinder = AssetFinder


# -------------------------------------------------------------------------------------------- render effects --

def read_rfx(finder):
    """Normal maps from the .RFX effect lists: REFX_DOT3_BUMP "MODEL" NORMAL_MAP "X" (whole model) or
    REFX_DOT3_BUMP "MODEL" { BASE_MAP "TEX" NORMAL_MAP "X" ... } (per texture).
    Returns {"model": {MODEL: X}, "texture": {(MODEL, TEX): X}, "any": {TEX: X}} (names upper-case)."""
    import re
    out = {"model": {}, "texture": {}, "any": {}}
    for path in finder.files(".rfx"):
        try:
            with open(path, encoding="latin-1") as f:
                text = f.read()
        except OSError:
            continue
        text = re.sub(r"//[^\n]*", "", text)
        toks = re.findall(r'"[^"]*"|[{}]|[^\s"{}]+', text)
        i = 0
        while i < len(toks):
            effect = toks[i]
            i += 1
            if not effect.startswith("REFX_"):
                continue
            model = ""
            if i < len(toks) and toks[i].startswith('"'):
                model = toks[i].strip('"').upper()
                i += 1
            entries = []
            if i < len(toks) and toks[i] == "{":
                i += 1
                cur = None
                while i < len(toks) and toks[i] != "}":
                    if toks[i] == "BASE_MAP" and i + 1 < len(toks):
                        cur = {"BASE_MAP": toks[i + 1].strip('"')}
                        entries.append(cur)
                        i += 2
                    elif cur is not None and i + 1 < len(toks):
                        cur[toks[i]] = toks[i + 1].strip('"')
                        i += 2
                    else:
                        i += 1
                i += 1
            else:
                cur = {}
                entries.append(cur)
                while i + 1 < len(toks) and not toks[i].startswith("REFX_"):
                    cur[toks[i]] = toks[i + 1].strip('"')
                    i += 2
            if effect != "REFX_DOT3_BUMP":
                continue
            for e in entries:
                nm = e.get("NORMAL_MAP")
                if not nm:
                    continue
                if "BASE_MAP" in e:
                    tex = e["BASE_MAP"].upper()
                    out["texture"][(model, tex)] = nm
                    out["any"].setdefault(tex, nm)
                elif model:
                    out["model"][model] = nm
    return out


def normal_map_for(rfx, model, texture, finder):
    """Normal map name for a texture of a model (None if the game has none)."""
    model, tex = (model or "").upper(), (texture or "").upper()
    nm = rfx["texture"].get((model, tex)) or rfx["any"].get(tex) or rfx["model"].get(model)
    if nm:
        # characters: the model entry names the UK map; the US skin has its own (HERO01BUMP_US_01)
        for skin, other in (("_US_", "_UK_"), ("_UK_", "_US_")):
            if skin in tex and other in nm.upper():
                alt = nm.upper().replace(other, skin)
                if finder.find(alt, (".tga", ".png", ".dds")):
                    nm = alt
    return nm


# ------------------------------------------------------------------------------------------------ animations --

def _qmul(a, b):
    """Quaternion product a * b (x, y, z, w): rotate by b, then by a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _quat_of(m):
    """Rotation quaternion (x, y, z, w) of a 4x4's upper 3x3 (columns normalised)."""
    c = [math.sqrt(m[0][j] ** 2 + m[1][j] ** 2 + m[2][j] ** 2) or 1.0 for j in range(3)]
    r = [[m[i][j] / c[j] for j in range(3)] for i in range(3)]
    t = r[0][0] + r[1][1] + r[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s)
    if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        return (0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    if r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        return ((r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
    return ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s)


def read_skl(path):
    """Global Storm skeleton .SKL: u32 n, n x {name NUL, i32 parent, vec3 LocalPosition, quat FixOrientation}, then
    IK handles (u32 k, k x {name NUL, u32 bones, u32 index, u32 flags}) and n x u32 BoneOrder (not returned).
    [(name, parent, local position, fix orientation)]."""
    with open(path, "rb") as f:
        d = f.read()
    n = struct.unpack_from("<I", d, 0)[0]
    p, out = 4, []
    for _ in range(n):
        e = d.index(b"\0", p)
        name = d[p:e].decode("latin-1")
        parent = struct.unpack_from("<i", d, e + 1)[0]
        v = struct.unpack_from("<7f", d, e + 5)
        out.append((name, parent, v[0:3], v[3:7]))
        p = e + 33
    return out


_SKL_CACHE = {}


def skeleton_of(scene):
    """The .SKL bones belonging to a Global Storm scene (the skeleton .EVO it was loaded with / is), or None."""
    import os
    for path in (getattr(scene, "skeleton_path", None), getattr(scene, "path", None)):
        if not path:
            continue
        skl = os.path.splitext(path)[0] + ".SKL"
        if not os.path.exists(skl):
            continue
        key = os.path.normcase(os.path.abspath(skl))
        if key not in _SKL_CACHE:
            try:
                _SKL_CACHE[key] = read_skl(skl)
            except (OSError, ValueError, struct.error):
                _SKL_CACHE[key] = None
        if _SKL_CACHE[key]:
            return _SKL_CACHE[key]
    return None


def is_ani_cg(d):
    """A Global Storm animation (.ani; the unnamed .BIN files in the level archives): name[44] (0xCC fill), then a
    header whose counts give the file's exact size."""
    if len(d) < 0x44 or not d[:1].isalpha():
        return False
    name = d[:44].split(b"\0")[0]
    if not name or not all(48 <= c < 91 or c == 95 for c in name):
        return False
    _dur, a, n, b = struct.unpack_from("<f3I", d, 44)
    pf, rf = struct.unpack_from("<2H", d, 60)
    c = struct.unpack_from("<I", d, 64)[0]
    return 68 + n * (8 + 16 * rf) + a * (8 + 12 * pf) + b * 24 + c * 28 == len(d)


def read_ani_cg(d, name=""):
    """Global Storm animation (ConflictGlobal.exe loader FUN_0061A1A0, exporter FUN_00619A80 Animations/<name>.ani):
    name[44], f32 duration, u32 A position tracks, u32 N rotation tracks, u32 B constant rotations, u16 position
    keys, u16 rotation keys, u32 C root-motion samples; N x {u32 bone, f32 (ignored), keys x quat}; A x {u32 bone,
    f32, keys x vec3 offset from the bone's LocalPosition}; B x {u32 bone, f32, quat}; C x {vec3, quat} (30 Hz root
    motion, kept in cg["root"]). Bones = .SKL indices; resolved per scene by Animation._resolve_cg."""
    a = Animation()
    a.game = "cg"
    a.name = name or d[:44].split(b"\0")[0].decode("latin-1")
    dur, na, nn, nb = struct.unpack_from("<f3I", d, 44)
    pf, rf = struct.unpack_from("<2H", d, 60)
    nc = struct.unpack_from("<I", d, 64)[0]
    p = 68
    items = {"rot": [], "pos": [], "const": []}      # file order, repeats kept (2 files list a constant bone twice)
    for _ in range(nn):
        bone = struct.unpack_from("<I", d, p)[0]
        items["rot"].append((bone, [struct.unpack_from("<4f", d, p + 8 + 16 * k) for k in range(rf)]))
        p += 8 + 16 * rf
    for _ in range(na):
        bone = struct.unpack_from("<I", d, p)[0]
        items["pos"].append((bone, [struct.unpack_from("<3f", d, p + 8 + 12 * k) for k in range(pf)]))
        p += 8 + 12 * pf
    for _ in range(nb):
        bone = struct.unpack_from("<I", d, p)[0]
        items["const"].append((bone, struct.unpack_from("<4f", d, p + 8)))
        p += 24
    rot, pos, const = dict(items["rot"]), dict(items["pos"]), dict(items["const"])
    root = [(struct.unpack_from("<3f", d, p + 28 * k), struct.unpack_from("<4f", d, p + 28 * k + 12))
            for k in range(nc)]
    a.frames = max(1, rf)
    a.fps = (rf - 1) / dur if rf > 1 and dur > 0 else 30.0
    a.cg = {"rot": rot, "pos": pos, "const": const, "rot_frames": max(1, rf), "pos_frames": max(1, pf),
            "duration": dur, "root": root, "name_raw": d[:44], "rf_raw": rf, "pf_raw": pf,
            "items": items}
    a.tracks = []
    return a


def _rv2q(v):
    """Quaternion of a rotation vector (axis x angle in radians)."""
    a = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if a < 1e-12:
        return (v[0] * 0.5, v[1] * 0.5, v[2] * 0.5, 1.0)     # keeps the zeros' signs (-0.0 in game files)
    s = math.sin(a / 2) / a
    return (v[0] * s, v[1] * s, v[2] * s, math.cos(a / 2))


def _q2rv(q):
    """Rotation vector (axis x angle in radians, |v| <= pi) of a quaternion (x, y, z, w) - the inverse of _rv2q."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    s = math.sqrt(x * x + y * y + z * z)
    if s < 1e-12:
        return (x * 2.0, y * 2.0, z * 2.0)
    a = 2.0 * math.atan2(s, w)
    return (x * a / s, y * a / s, z * a / s)


class Animation:
    """A .prb animation; every track has one key per frame or a single constant key. Keys are local to the parent
    like the EVO keys. Desert Storm: one track per object of the skeleton's .EVO file (in file order). Desert Storm II:
    the tracks name their object by its header hash (``hashes``; the game binds them by it, objects without a track
    keep their rest pose) - ``tracks_for(scene)`` / ``anim_worlds`` follow that. Vietnam: keys are rotations relative
    to the bone's rest rotation (``deltas``) - ``tracks_for(scene)`` turns them into plain keys for that skeleton."""

    def __init__(self):
        self.name = ""
        self.fps = 10.0
        self.frames = 1
        self.tracks = []       # [[(pos, scale, quat)]], quat as a plain rotation (x, y, z, w)
        self.events = []       # [(type, time in frames, value)]: 1 = footstep (value sign = foot, 1 walk / 25 run)
        self.game = "ds1"      # file layout: "ds1", "ds2" (also The Great Escape's) or "cv" (Vietnam)
        self.hashes = None     # DS2: per track the object's name hash (EOBJ header hash), else None
        self.track_flags = []  # DS2: per track 1, or 3 for connectors (names with CONNECTOR)
        self.rates = []        # DS2: per track keys per second (u16)
        self.raw = {}          # DS2: what a byte-exact rewrite needs - header flag, pad, bounds / events bytes
        self.deltas = None     # Vietnam: per track [(pos or None = rest, delta quat)]; tracks follow the last scene
        self.cg = None         # Global Storm: raw .ani tracks by skeleton bone (read_ani_cg); resolved per scene
        self._map = {}         # id(scene) -> [track index or None per object]
        self._resolved = None  # Vietnam: id(scene) the tracks were resolved for

    @property
    def duration(self):
        return self.frames / self.fps if self.fps else 0.0

    def key(self, track, frame):
        keys = self.tracks[track]
        return keys[min(frame, len(keys) - 1)]

    def tracks_for(self, scene):
        """Per object of `scene` the index of its track, or None (DS1: track i = object i)."""
        if self.cg is not None:
            if self._resolved != id(scene):
                self._resolve_cg(scene)
            return self._map[id(scene)]
        got = self._map.get(id(scene))
        if got is None:
            if self.hashes is None:
                got = [i if i < len(self.tracks) else None for i in range(len(scene.objects))]
            else:
                by_hash = {}
                for t, h in enumerate(self.hashes):
                    by_hash.setdefault(h, t)
                got = [by_hash.get(o.node.hash) for o in scene.objects]
            self._map[id(scene)] = got
        if self.deltas is not None and self._resolved != id(scene):
            self._resolve(scene, got)
        return got

    def _resolve(self, scene, got):
        """Vietnam: plain keys for `scene` - rotation = delta x the bound object's rest rotation (the model's keys
        hold the rotation itself in Vietnam), position = the key's, or the rest position for rotation-only tracks.
        Checked on standing idles and walk cycles: the lower foot stays on the ground (12-17 cm) in every frame."""
        keys = scene.nodes[0].keys if scene.nodes and len(scene.nodes[0].keys) == len(scene.objects) else None
        obj_of = {}
        for i, t in enumerate(got):
            if t is not None:
                obj_of.setdefault(t, i)
        tracks = []
        for t, deltas in enumerate(self.deltas):
            i = obj_of.get(t)
            if keys is None or i is None:
                rpos, rscale, rq = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)
            else:
                rpos, rscale, rq = keys[i]
            tracks.append([(pos if pos is not None else tuple(rpos), tuple(rscale), _qmul(dq, rq)) for pos, dq in deltas])
        self.tracks = tracks
        self._resolved = id(scene)

    def _resolve_cg(self, scene):
        """Global Storm: one track per object of `scene` that is a bone of its skeleton (.SKL next to it), with plain
        local keys. The game's pose (ConflictGlobal.exe FUN_00612EE0): local rotation = the key (absolute; identity
        for a bone without a track), local position = SKL LocalPosition + the position track's offset; world_q =
        parent_q x local_q, world_p = parent_p + R(parent_q) local_p; each bone's final rotation = world_q x
        FixOrientation (not passed on to children). With no animation this rebuilds C04_SKELETON.EVO's rest pose
        exactly. Objects that aren't skeleton bones (or every object, without a matching .SKL) get no track."""
        skl = skeleton_of(scene)
        n = len(scene.objects)
        got = [None] * n
        tracks = [[((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0))] for _ in range(n)]
        cg = self.cg
        if skl and max(list(cg["rot"]) + list(cg["pos"]) + list(cg["const"]) + [-1]) < len(skl):
            names = {o.name.upper().replace("_", " "): o.index for o in scene.objects}
            bone_obj = {i: names.get(b[0].upper().replace("_", " ")) for i, b in enumerate(skl)}
            obj_bone = {j: i for i, j in bone_obj.items() if j is not None}
            rf, pf = cg["rot_frames"], cg["pos_frames"]
            per_obj = {j: [] for j in obj_bone}
            for f in range(self.frames):
                fp = int(round(f * (pf - 1) / (rf - 1))) if rf > 1 and pf > 1 else 0
                hq, hp = {}, {}

                def bone(i, depth=0):
                    if i in hq:
                        return
                    _name, parent, lpos, _fix = skl[i]
                    if i in cg["rot"]:
                        q = cg["rot"][i][min(f, len(cg["rot"][i]) - 1)]
                    else:
                        q = cg["const"].get(i, (0.0, 0.0, 0.0, 1.0))
                    off = cg["pos"][i][min(fp, len(cg["pos"][i]) - 1)] if i in cg["pos"] else (0.0, 0.0, 0.0)
                    lp = (lpos[0] + off[0], lpos[1] + off[1], lpos[2] + off[2])
                    if 0 <= parent < len(skl) and depth < 256:
                        bone(parent, depth + 1)
                        rp = mat_apply(mat_from_trs((0.0, 0.0, 0.0), hq[parent], (1.0, 1.0, 1.0)), lp, 0.0)
                        hp[i] = (hp[parent][0] + rp[0], hp[parent][1] + rp[1], hp[parent][2] + rp[2])
                        hq[i] = _qmul(hq[parent], q)
                    else:
                        hp[i], hq[i] = lp, q
                world = {}
                for i in range(len(skl)):
                    bone(i)
                    world[i] = mat_from_trs(hp[i], _qmul(hq[i], skl[i][3]), (1.0, 1.0, 1.0))
                for j, i in obj_bone.items():
                    o = scene.objects[j]
                    if o.parent is None:
                        parent_w = mat_identity()
                    elif o.parent in obj_bone:
                        parent_w = world[obj_bone[o.parent]]
                    else:
                        parent_w = scene.objects[o.parent].world
                    local = mat_mul(mat_invert_affine(parent_w), world[i])
                    per_obj[j].append(((local[0][3], local[1][3], local[2][3]), (1.0, 1.0, 1.0), _quat_of(local)))
            for j, keys in per_obj.items():
                got[j] = j
                tracks[j] = keys
        self.tracks = tracks
        self._map[id(scene)] = got
        self._resolved = id(scene)

    def local(self, track, frame):
        pos, scale, q = self.key(track, frame)
        return mat_from_trs(pos, q, scale)


def read_prb(path_or_bytes, name=""):
    """Compiled animation (FUN_00503b70): u32 version 2, u32 track count, u32 bounds count, u32 n, u32 frames,
    u32, 2 x pad; bounds x 160 B; n x 40 B; track offsets u32[tracks]; track = {u32 key count, u32 keys per
    second, u32 (pointer slot), keys x 52 B {vec3 pos, vec3 unused, vec3 scale, quat (conjugated)}}.
    Desert Storm II (CAnim binary load 0x5625D0, a dump of the CPRSAnim tracks the text .prs reader 0x5635A0 builds):
    the same header, bounds (160 B; the 6 words DS1 fills with 0xFEEDFACE hold the box's half size and centre; one
    block, or one per frame) and events (40 B, u32 9 = a stale next-pointer); track = {u32 object name hash (upper-case
    LFSR = the EOBJ header hash), u32 flags (1; 3 = connector), u16 key count, u16 keys per second, u32 (pointer
    slot), keys x 28 B {vec3 pos, quat (conjugated)}} - no scale. The Great Escape: the same layout with 40-byte keys
    {vec3 pos, vec3 scale, quat (conjugated)} (told by the track sizes, raw["key"])."""
    import os
    if isinstance(path_or_bytes, (bytes, bytearray)):
        d = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as f:
            d = f.read()
        name = name or os.path.splitext(os.path.basename(path_or_bytes))[0]
    if is_ani_cg(d):
        return read_ani_cg(d, name if isinstance(path_or_bytes, (bytes, bytearray)) else "")
    if len(d) < 32 or struct.unpack_from("<I", d, 0)[0] != 2:
        raise EvoError("not a .prb animation")
    _, ntracks, nbounds, n3, frames = struct.unpack_from("<5I", d, 0)
    a = Animation()
    a.name = name
    a.frames = max(1, frames)
    ev = 32 + nbounds * 160
    off = ev + n3 * 40
    if off + 4 * ntracks > len(d):
        raise EvoError("truncated .prb")
    for i in range(n3):        # 40 B: u32 type, f32 time, 2 x 0, f32 value, 4 x 0, u32 (run-time slot)
        etype, etime = struct.unpack_from("<If", d, ev + 40 * i)
        a.events.append((etype, etime, struct.unpack_from("<f", d, ev + 40 * i + 16)[0]))
    offsets = struct.unpack_from("<%dI" % ntracks, d, off)
    if offsets and offsets[0] + 16 <= len(d) and struct.unpack_from("<I", d, offsets[0] + 12)[0] == 0xFFFFFFFF \
            and struct.unpack_from("<I", d, offsets[0] + 8)[0] != 0xFFFFFFFF:
        return _read_prb_ds2(a, d, offsets, nbounds, n3)
    for i, o in enumerate(offsets):
        if o + 12 > len(d):
            raise EvoError("truncated .prb")
        count, rate = struct.unpack_from("<II", d, o)
        if i == 0 and rate:
            a.fps = float(rate)
        if o + 12 + 52 * count > len(d):
            raise EvoError("truncated .prb")
        keys = []
        for k in range(count):
            v = struct.unpack_from("<13f", d, o + 12 + 52 * k)
            keys.append((v[0:3], v[6:9], (-v[9], -v[10], -v[11], v[12])))
        a.tracks.append(keys or [((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0))])
    return a


def _prb_key_size(d, offsets):
    """Key size of a hash-bound .prb: 28 (Desert Storm II: pos, quat) or 40 (The Great Escape: pos, scale, quat),
    told by the track sizes (each track = 16-byte header + its keys); "cv" for Vietnam's layout (12-byte rotation-only
    tracks, or track flag 4 = has positions - DS2 / Great Escape tracks have flags 1 or 3 only)."""
    ends = sorted(set(offsets)) + [len(d)]
    nxt = {o: ends[ends.index(o) + 1] for o in set(offsets)}
    sizes = set()
    for o in offsets:
        if o + 16 > len(d):
            raise EvoError("truncated .prb")
        flags, count = struct.unpack_from("<IH", d, o + 4)
        if flags & 4:
            return "cv"
        if count:
            sizes.add((nxt[o] - o - 16) / count)
    if 12.0 in sizes:
        return "cv"
    if sizes and sizes <= {40.0}:
        return 40
    return 28


def _read_prb_ds2(a, d, offsets, nbounds, nevents):
    a.game = "ds2"
    a.hashes = []
    ev = 32 + nbounds * 160
    key = _prb_key_size(d, offsets)
    if key == "cv":
        a.game = "cv"          # Vietnam: hash-bound like DS2, keys relative to rest (evo_write.write_prb_cv)
    a.raw = {"flag": struct.unpack_from("<I", d, 20)[0], "pad": d[24:32], "bounds": d[32:ev],
             "events": d[ev:ev + nevents * 40], "key": key}
    for i, o in enumerate(offsets):
        if o + 16 > len(d):
            raise EvoError("truncated .prb")
        h, flags, count, rate = struct.unpack_from("<2I2H", d, o)
        if key != "cv" and o + 16 + key * count > len(d):
            raise EvoError("truncated .prb")
        if i == 0 and rate:
            a.fps = float(rate)    # the game takes the first track's rate (0x5625D0)
        keys = []
        if key == "cv":
            # Vietnam: flag 4 = 28-byte keys {quat x y z w, pos} (pos absolute), else 12-byte rotation vectors
            # (axis x angle, |v| <= pi); both rotations relative to the bone's rest rotation (Animation._resolve)
            size = 28 if flags & 4 else 12
            if o + 16 + size * count > len(d):
                raise EvoError("truncated .prb")
            deltas = []
            for k in range(count):
                if flags & 4:
                    v = struct.unpack_from("<7f", d, o + 16 + 28 * k)
                    deltas.append((v[4:7], v[0:4]))
                else:
                    deltas.append((None, _rv2q(struct.unpack_from("<3f", d, o + 16 + 12 * k))))
            a.deltas = (a.deltas or []) + [deltas or [(None, (0.0, 0.0, 0.0, 1.0))]]
            a.hashes.append(h)
            a.track_flags.append(flags)
            a.rates.append(rate)
            a.tracks.append([((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0))] * len(deltas or [0]))
            continue
        for k in range(count):
            if key == 40:          # The Great Escape: pos, scale, quat (stored like DS2's)
                v = struct.unpack_from("<10f", d, o + 16 + 40 * k)
                keys.append((v[0:3], v[3:6], (-v[6], -v[7], -v[8], v[9])))
                continue
            v = struct.unpack_from("<7f", d, o + 16 + 28 * k)
            keys.append((v[0:3], (1.0, 1.0, 1.0), (-v[3], -v[4], -v[5], v[6])))
        a.hashes.append(h)
        a.track_flags.append(flags)
        a.rates.append(rate)
        a.tracks.append(keys or [((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0))])
    return a


def anim_worlds(scene, anim, frame):
    """World (model space) matrices of all objects at a frame; objects without a track keep their rest pose."""
    out = [None] * len(scene.objects)
    tracks = anim.tracks_for(scene)

    def get(i, depth=0):
        if out[i] is not None:
            return out[i]
        o = scene.objects[i]
        local = anim.local(tracks[i], frame) if tracks[i] is not None else o.local
        out[i] = mat_mul(get(o.parent, depth + 1), local) if o.parent is not None and depth < 256 else local
        return out[i]

    for i in range(len(scene.objects)):
        get(i)
    return out


def retarget(anim, source, target):
    """An animation of another skeleton (`source`: the scene it was made for, e.g. another game's soldier) for
    `target`, by bone name: every target object whose name the source has takes the source object's world transform
    in each frame, the others keep their rest offset from their parent. That is what the converted skins need (the
    cross-game export moves a skin so it sits on the target's rest bones as on the source's - same world bones, same
    deformation). Only skeleton bones are matched: connectors (hip pouches, hat, gun hand ...) are each body's own
    attachment points and keep the target body's rest offset, as the games' own animations leave them. Returns an
    Animation with one track per target object (tracks_for = object order), the source's rate, frames and events.
    None when no bone name matches."""
    names = {o.name.upper(): o.index for o in source.objects
             if o.name and o.role in ("bone", "mesh", "empty") and o.role != "connector"}
    hits = [j for j, o in enumerate(target.objects) if o.name and o.name.upper() in names]
    if not hits:
        return None
    out = Animation()
    out.name, out.fps, out.frames, out.events = anim.name, anim.fps, anim.frames, list(anim.events)
    keys = [[] for _ in target.objects]
    for f in range(anim.frames):
        src = anim_worlds(source, anim, f)
        world = [None] * len(target.objects)

        def get(j, depth=0):
            if world[j] is not None:
                return world[j]
            o = target.objects[j]
            k = names.get((o.name or "").upper()) if o.role != "connector" else None
            if k is not None:
                world[j] = src[k]
            elif o.parent is not None and depth < 256:
                world[j] = mat_mul(get(o.parent, depth + 1), o.local)
            else:
                world[j] = o.local
            return world[j]

        for j, o in enumerate(target.objects):
            w = get(j)
            parent = get(o.parent) if o.parent is not None else mat_identity()
            local = mat_mul(mat_invert_affine(parent), w)
            keys[j].append(((local[0][3], local[1][3], local[2][3]), (1.0, 1.0, 1.0), _quat_of(local)))
    out.tracks = keys
    out.hashes = None
    return out


def compatible(scene, anim):
    """The animation fits the model. DS1: one track per object. DS2 binds tracks by name hash and skips the rest, so
    a track list only needs to cover the body: at least three quarters of the bones the skin is weighted to get a
    track (vehicle animations also move the vehicle's gun: VEHICLEGUNNERAIM has 62 tracks for a 40-object soldier),
    and the bones' offsets from their parents (Biped bone lengths, fixed per skeleton) match the model's - the goats
    are Bipeds named like the soldiers. Checked on all 274 DS2 animations: the 270 soldier ones fit every soldier
    body, the 4 goat ones only the goat."""
    if anim.cg is not None:
        # Global Storm: the skeleton's .SKL must have every bone the animation names (male / female skeletons differ)
        return any(t is not None for t in anim.tracks_for(scene))
    if anim.hashes is None:
        return len(anim.tracks) == len(scene.objects)
    tracks = anim.tracks_for(scene)
    weighted = set()
    for o in scene.objects:
        if o.role == "skin":
            for m in o.node.meshes:
                for infl in m.influences or ():
                    weighted.update(o.node.bones[b][1] for b, *_ in infl if b < len(o.node.bones))
    weighted = weighted or set(scene.bone_ids)
    if not weighted or sum(1 for i in weighted if tracks[i] is not None) * 4 < len(weighted) * 3:
        return False
    keys = scene.nodes[0].keys
    if len(keys) != len(scene.objects):
        return True
    off = []
    for i, o in enumerate(scene.objects):
        if tracks[i] is None or o.parent is None:
            continue
        rest = keys[i][0]
        length = math.sqrt(sum(c * c for c in rest))
        if length >= 2.0:                                   # cm; joints at the parent tell nothing
            off.append(math.dist(rest, anim.tracks[tracks[i]][0][0]) / length)
    off.sort()
    return not off or off[len(off) // 2] <= 0.15


# --------------------------------------------------------------------------------------------------- vehicles --

def read_dyn(path):
    """Vehicle dynamics (.DYN, text): tracks "LEFT_MODEL RIGHT_MODEL width" and wheels
    "flags radius f f x y z radius f f f MODEL" (positions in the vehicle's space)."""
    tracks, wheels = [], []
    try:
        with open(path, encoding="latin-1") as f:
            lines = f.read().splitlines()
    except OSError:
        return {"tracks": tracks, "wheels": wheels}

    def num(t):
        try:
            float(t)
            return True
        except ValueError:
            return False

    for line in lines:
        t = line.split()
        if len(t) == 3 and not num(t[0]) and not num(t[1]) and num(t[2]):
            tracks += [t[0], t[1]]
        elif len(t) >= 12 and not num(t[-1]) and all(num(x) for x in t[:-1]):
            wheels.append((t[-1], (float(t[4]), float(t[5]), float(t[6]))))
    return {"tracks": tracks, "wheels": wheels}


def _table(finder, name):
    """Rows of a catalog table (WEAPS, VEHICLE ...): <NAME>.TXT, else the extractor's _<HASH>.BIN for that name (an
    extraction that couldn't name it - Global Storm's). Comma-separated rows; a row without a comma (Global Storm's
    VEHICLE.TXT) is split at whitespace."""
    path = finder.find(name, (".txt",))
    if not path:
        try:
            path = finder.find("_%08X" % archive_hash(name.upper() + ".TXT"), (".bin",))
        except Exception:
            path = None
    if not path:
        return []
    try:
        with open(path, encoding="latin-1") as f:
            return [line.strip().split(",") if "," in line else line.split() for line in f if line.strip()]
    except OSError:
        return []


def vehicle_parts(scene, finder):
    """Parts the game attaches to a vehicle at run time: [(model name, connector object index or None,
    (x, y, z) position or None, kind)] from <MODEL>.DYN (tracks, wheels) and the catalog tables
    VEHICLE.TXT -> VEHWPNS.TXT -> WEAPS.TXT (weapon / barrel model + connector)."""
    model = scene.name.upper()
    parts = []
    dyn = finder.find(model, (".dyn",))
    if dyn:
        d = read_dyn(dyn)
        parts += [(t, None, None, "track") for t in d["tracks"]]
        parts += [(n, None, p, "wheel") for n, p in d["wheels"]]
    ids = {r[0].upper() for r in _table(finder, "VEHICLE") if len(r) > 2 and model in (r[1].upper(), r[2].upper())}
    if ids:
        weapons = [r[1].upper() for r in _table(finder, "VEHWPNS") if len(r) > 1 and r[0].upper() in ids]
        weaps = {r[0].upper(): r for r in _table(finder, "WEAPS") if len(r) > 6}
        conn = {o.name.upper(): o.index for o in scene.objects}
        seen = set()
        for w in weapons:
            r = weaps.get(w)
            if not r or r[1] in ("-1", "") or r[1].upper() in seen:
                continue
            seen.add(r[1].upper())
            parts.append((r[1], conn.get(r[6].upper()), None, "weapon"))
    return parts


# ------------------------------------------------------------------------------------------------- textures --

def default_skin_candidates(scene):
    """Characters are drawn with a texture the game picks at run time (TEXL says "Default"):
    DS1 HERO01_ARMSTRONG -> HERO01_UK_01 / HERO01_US_01 / HERO01_IR_01 / HERO01_RU_01; DS2 US_MARINE -> US_MARINE_01,
    GEN_DF_SOLDIER -> DF_SOLDIER_01 (the unit's own skins are numbered after the model)."""
    name = scene.name.upper()
    prefix = name.split("_")[0]
    out = [prefix + s for s in ("_UK_01", "_US_01", "_IR_01", "_RU_01")] + [name, name + "_01"]
    if name.startswith("GEN_"):
        out.append(name[4:] + "_01")
    return out


def _skin_by_prefix(scene, finder):
    """Last resort for an untextured character: the first texture named after the model (DF_SERGEANT ->
    SERGEANT_DF_..., LOWPOLY_SOLDIER -> LOWPOLY_...) - sorted, so the choice is stable."""
    words = [w for w in scene.name.upper().split("_") if w and w not in ("GEN", "SOLDIER", "POLY")]
    if not words or not getattr(finder, "by_stem", None):
        return None
    hits = sorted(st for st, ex in finder.by_stem.items()
                  if any(e in ex for e in TEXTURE_EXTS) and all(w in st.split("_") for w in words)
                  and not st.endswith(("_BUMP", "_PORTRAIT")))
    hits.sort(key=lambda st: "TRAINING" in st)          # training-camp variants only when there's nothing else
    return hits[0] if hits else None


def resolve_textures(scene, finder, skin="", override=""):
    """{mesh id: (texture name, file path or None)} and the texture used for untextured skinned meshes.
    Untextured ("Default") static meshes try a texture named like their object or model."""
    fallback = None
    if scene.skinned:
        for cand in ([skin] if skin else default_skin_candidates(scene)):
            if finder.find(cand):
                fallback = cand
                break
        if fallback is None and not skin:
            fallback = _skin_by_prefix(scene, finder)
    # override (Global Storm: a scene record's texture field): replaces the model's main texture - its first TEXL entry
    # (cars: the magenta / yellow C04_V_TextureOverride placeholder; crates / barrels / doors: regional variants)
    main = ""
    if override and finder.find(override):
        main = next((t for o in scene.objects for _h, t in o.node.textures if t and t.lower() != "default"), "")
    out = {}
    for o in scene.objects:
        for m in o.node.meshes:
            name = m.texture
            if main and name and name.lower() == main.lower() and o.role != "skin":
                name = override
            if skin and fallback and o.role == "skin":  # a chosen skin replaces the model's own texture
                name = fallback
            elif not name and o.role in ("skin", "bone"):
                name = fallback or ""
            elif not name and o.role == "mesh":
                name = next((n for n in (o.node.name, scene.name) if n and finder.find(n)), "")
            out[id(m)] = (name, finder.find(name) if name else None)
    return out, fallback
