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

Space: Direct3D, left-handed, Y up, centimetres. Node keys (only in the first object of a hierarchy, one per
object in file order) are local to the parent; the stored quaternion is the conjugate of the rotation
(verified: every skin influence of HERO01_ARMSTRONG lands on the same point to 1e-5).
"""
import math
import struct

VERSION = 0x2F

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


def _read_vbuf(r, node):
    r.tag(b"VBUF")
    ver = r.u32()
    external = r.u32() if ver >= 2 else 0
    r.u32()                           # flags
    m = Mesh()
    m.material_hash = r.u32()
    m.texture_hash = r.u32()
    strip = r.u32()
    nvert, nstrip, nlist = r.u32(), r.u32(), r.u32()
    r.u32()
    if external == 0:
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
    r.tag()                           # VBPL
    r.raw(r.u32() * (4 if ver > 3 else 2))
    if ver >= 3:
        r.raw(8)
    if m.triangles:
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
        r.need(44 * n)
        infl = []
        for i in range(n):
            x, y, z, nx, ny, nz, c, u, v, bone, w = struct.unpack_from("<6fI2fB3xf", r.d, r.p)
            r.p += 44
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
        r.u32()                       # flags
        m = Mesh()
        m.material_hash = r.u32()
        m.texture_hash = r.u32()
        strip = r.u32()
        nvert, nstrip, nlist, nframes = r.u32(), r.u32(), r.u32(), r.u32()
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
    if r.u32() != VERSION:
        raise EvoError("unsupported EOBJ version at 0x%x" % n.offset)
    n.model = r.s(64)
    n.name = r.s(64)
    n.hash = r.u32()
    n.type = r.u32()
    n.flags = r.u32()
    n.kind = r.u32()
    r.raw(24)
    parent = r.s(64)
    n.parent = "" if parent == "ROOT" else parent
    r.u32()
    for _ in range(r.u32()):
        name = r.s(128)
        n.bones.append((name, r.u32()))
    has_keys = r.u32()
    r.raw(0x100)
    nbspt = r.u32()
    npts = r.u32()
    if npts:
        n.points = [r.f32(3) for _ in range(npts)]
        r.raw(20)
    r.tag(b"TEXL")
    for _ in range(r.u32()):
        h = r.u32()
        n.textures.append((h, r.s(64)))
    r.tag(b"MATL")
    for _ in range(r.u32()):
        h = r.u32()
        n.materials.append(Material(h, r.f32(4), r.f32(4), r.f32(), r.f32(4)))
    for i in range(2):
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
    elif n.kind == 2:
        _read_skin(r, n)
    elif n.kind == 1:
        _read_cloth(r, n)
    else:
        raise EvoError("unknown geometry kind %d" % n.kind)
    for _ in range(nbspt):
        size = r.u32()
        r.u32()
        if size:
            r.tag()
            r.raw(r.u32())
    if has_keys:
        r.tag()
        r.u32()
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
        nodes.append(read_node(r))
    if not nodes:
        raise EvoError("no objects in file")
    return nodes


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
                if o.role == "bone":
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


def load(path):
    return Scene(read(path))




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


def archive_hash(name, state=1):
    """Name hash of the game's .dat archives (FUN_004b9a10): LFSR over the upper-cased name, start 1, per bit
    LSB first (in = bit ^ h0 ^ h1 ^ h21 ^ h31, h = h << 1 | in). Entries whose name is unknown are extracted as
    _<HASH>.<EXT>. state continues a previous hash: archive_hash("B", archive_hash("A")) == archive_hash("AB")."""
    a0, a1, a2, a3 = _HA
    b = _HB
    x = state
    for c in name.upper().encode("latin-1", "replace"):
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
        for d in dirs:
            try:
                files = os.listdir(d)
            except OSError:
                continue
            for f in files:
                stem, ext = os.path.splitext(f)
                self.by_stem.setdefault(stem.upper(), {}).setdefault(ext.lower(), os.path.join(d, f))

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
        """{name: path} of all .prb animations, including unnamed ones (_HASH.PRB / _HASH.BIN with a prb header)."""
        out = {}
        for stem, v in self.by_stem.items():
            if ".prb" in v:
                out.setdefault(stem, v[".prb"])
            elif stem.startswith("_") and ".bin" in v:
                try:
                    with open(v[".bin"], "rb") as f:
                        head = f.read(12)
                    if head[:4] == b"\x02\x00\x00\x00" and 0 < struct.unpack_from("<I", head, 4)[0] < 256:
                        out.setdefault(stem, v[".bin"])
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

class Animation:
    """A .prb animation: one track per object of the skeleton's .EVO file (in file order); every track has one
    key per frame or a single constant key. Keys are local to the parent like the EVO keys."""

    def __init__(self):
        self.name = ""
        self.fps = 10.0
        self.frames = 1
        self.tracks = []       # [[(pos, scale, quat)]], quat as a plain rotation (x, y, z, w)

    @property
    def duration(self):
        return self.frames / self.fps if self.fps else 0.0

    def key(self, track, frame):
        keys = self.tracks[track]
        return keys[min(frame, len(keys) - 1)]

    def local(self, track, frame):
        pos, scale, q = self.key(track, frame)
        return mat_from_trs(pos, q, scale)


def read_prb(path_or_bytes, name=""):
    """Compiled animation (FUN_00503b70): u32 version 2, u32 track count, u32 bounds count, u32 n, u32 frames,
    u32, 2 x pad; bounds x 160 B; n x 40 B; track offsets u32[tracks]; track = {u32 key count, u32 keys per
    second, u32 (pointer slot), keys x 52 B {vec3 pos, vec3 unused, vec3 scale, quat (conjugated)}}."""
    import os
    if isinstance(path_or_bytes, (bytes, bytearray)):
        d = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as f:
            d = f.read()
        name = name or os.path.splitext(os.path.basename(path_or_bytes))[0]
    if len(d) < 32 or struct.unpack_from("<I", d, 0)[0] != 2:
        raise EvoError("not a .prb animation")
    _, ntracks, nbounds, n3, frames = struct.unpack_from("<5I", d, 0)
    a = Animation()
    a.name = name
    a.frames = max(1, frames)
    off = 32 + nbounds * 160 + n3 * 40
    if off + 4 * ntracks > len(d):
        raise EvoError("truncated .prb")
    offsets = struct.unpack_from("<%dI" % ntracks, d, off)
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


def anim_worlds(scene, anim, frame):
    """World (model space) matrices of all objects at a frame; objects without a track keep their rest pose."""
    out = [None] * len(scene.objects)

    def get(i, depth=0):
        if out[i] is not None:
            return out[i]
        o = scene.objects[i]
        local = anim.local(i, frame) if i < len(anim.tracks) else o.local
        out[i] = mat_mul(get(o.parent, depth + 1), local) if o.parent is not None and depth < 256 else local
        return out[i]

    for i in range(len(scene.objects)):
        get(i)
    return out


def compatible(scene, anim):
    return len(anim.tracks) == len(scene.objects)


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
    path = finder.find(name, (".txt",))
    if not path:
        return []
    try:
        with open(path, encoding="latin-1") as f:
            return [line.strip().split(",") for line in f if line.strip()]
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
    HERO01_ARMSTRONG -> HERO01_UK_01 / HERO01_US_01 / HERO01_IR_01 / HERO01_RU_01."""
    prefix = scene.name.split("_")[0].upper()
    return [prefix + s for s in ("_UK_01", "_US_01", "_IR_01", "_RU_01")] + [scene.name.upper()]


def resolve_textures(scene, finder, skin=""):
    """{mesh id: (texture name, file path or None)} and the texture used for untextured skinned meshes.
    Untextured ("Default") static meshes try a texture named like their object or model."""
    fallback = None
    if scene.skinned:
        for cand in ([skin] if skin else default_skin_candidates(scene)):
            if finder.find(cand):
                fallback = cand
                break
    out = {}
    for o in scene.objects:
        for m in o.node.meshes:
            name = m.texture
            if skin and fallback and o.role == "skin":  # a chosen skin replaces the model's own texture
                name = fallback
            elif not name and o.role in ("skin", "bone"):
                name = fallback or ""
            elif not name and o.role == "mesh":
                name = next((n for n in (o.node.name, scene.name) if n and finder.find(n)), "")
            out[id(m)] = (name, finder.find(name) if name else None)
    return out, fallback
