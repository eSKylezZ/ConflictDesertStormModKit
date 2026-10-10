"""Writer for Conflict: Desert Storm .EVO meshes: a new model from a game model used as the template.

The template keeps everything the game needs that isn't geometry - the object list and hierarchy, attachment points
(the hand grip, muzzle ...), node keys, collision/shadow helpers, materials - and each part given new geometry gets:
  TEXL   its textures {texture hash, name}; hash = texture_hash(name) (FUN_004b9ab0, the archive LFSR over the
         lower-cased name - the draw batches find their texture by it)
  BBOX   x2: min, max, six planes (+-axis, -min / max), then BSPH: centre, radius
  VBOB   one VBUF (version 6) per texture: vertices {pos, normal, BGRA colour, uv} in the part's own space, a
         triangle strip (the game draws strips: every model has one) + the same triangles as a list, empty VSTP
         and FACE, VBPL with a zero entry per strip triangle, and the template's two trailing u32s.
Everything else of the template object (header, MATL, collision trees, keys, NEXT/DONE) is copied; the model name in
every object header can be renamed. Collision can be replaced too (rebuild(collision=...)): the object's BSPT entries
become one u = 0 tree built by bsp.py (the game skips u = 1 render trees), the header's BSPT count and collision source
name ("THIS" / "NOBSP", or the template's "#BSP_" helper, whose mesh is rebuilt) follow, and both bounding boxes grow
to take the collision in. Shadow (#SHA_) objects are plain meshes - give them new geometry through `parts`; the game
computes their face table itself.

Static parts (geometry kinds 0 and 3: weapons, props, vehicles) take Parts; a character's skin (kind 2) takes a
SkinPart: SKIN {u32 has-strip, vertex count, strip count, list count, texture hash, material hash (the template's),
per vertex {u32 n, n x {pos, normal in the bone's bind space, BGRA, uv, u8 bone list index, 3 pad, f32 weight}},
strip, list}, SSTP {u32 0}, SHAD {u32 n, per list triangle 29 B: 6 unused, u16 neighbour across v0v1 / v1v2 / v2v0
(by position; none = itself), 12 unused, u32 1, 1 unused} - unused bytes are 0xFA like the game's own files.

Game space: Direct3D left-handed, Y up, centimetres, front towards -Z. Pure Python, no dependencies.
"""
import math
import struct

from . import bsp, evo


class Part:
    """New geometry for one template object: triangles over vertices in the object's own (game) space."""

    def __init__(self, texture, positions, normals, uvs, triangles, colours=None):
        self.texture = texture                    # texture name without extension
        self.positions = positions                # [(x, y, z)] cm
        self.normals = normals                    # [(x, y, z)]
        self.uvs = uvs                            # [(u, v)], v down (DirectX)
        self.triangles = triangles                # [(a, b, c)] clockwise seen from the front (the game's winding)
        self.colours = colours                    # [0xAARRGGBB] or None (white)


def texture_hash(name):
    x = 1
    for c in name.lower().encode("latin-1"):
        for b in range(8):
            i = ((c >> b) & 1) ^ (x & 1) ^ ((x >> 1) & 1) ^ ((x >> 21) & 1) ^ ((x >> 31) & 1)
            x = ((x << 1) & 0xFFFFFFFF) | i
    return x


class _Obj:
    pass


def walk(data):
    """Objects of an .EVO with the offsets of their sections."""
    r = evo.Reader(data)
    out = []
    while r.p < len(data):
        o = _Obj()
        o.start = r.p
        node = evo.read_node(r)          # validates and moves to the end of the object
        o.end = r.p
        o.name, o.kind, o.model = node.name, node.kind, node.model
        o.materials = node.materials
        o.texl = data.index(b"TEXL", o.start)
        o.matl = data.index(b"MATL", o.texl)
        o.bbox = data.index(b"BBOX", o.matl)
        o.geom = o.bbox + 2 * (4 + 24 + 96 + 4 + 16)
        # the geometry ends where read_node's BSP / keys / end tag part starts: re-read the geometry alone
        g = evo.Reader(data, o.geom)
        dummy = evo.Node()
        if o.kind in (0, 3):
            g.tag(b"VBOB")
            for _ in range(g.u32()):
                evo._read_vbuf(g, dummy)
        elif o.kind == 2:
            evo._read_skin(g, dummy)
        elif o.kind == 1:
            evo._read_cloth(g, dummy)
        o.geom_end = g.p
        o.vbuf_tail = b"\0" * 8
        o.vbuf_flags = 0xC1
        if o.kind in (0, 3):
            p = data.find(b"VBUF", o.geom, o.geom_end)
            if p != -1 and struct.unpack_from("<I", data, p + 4)[0] >= 2:
                o.vbuf_flags = struct.unpack_from("<I", data, p + 12)[0]
        out.append(o)
    return out


def _same_winding(t, u):
    """t and u are the same triangle with the same orientation."""
    return tuple(u) in ((t[0], t[1], t[2]), (t[1], t[2], t[0]), (t[2], t[0], t[1]))


def _strips(triangles):
    """Greedy stripification: runs of triangles that share an edge, each keeping its winding."""
    edge_tris = {}
    for ti, t in enumerate(triangles):
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edge_tris.setdefault(frozenset((a, b)), []).append(ti)
    used = [False] * len(triangles)

    def free_neighbours(ti):
        t = triangles[ti]
        return sum(1 for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))
                   for x in edge_tris[frozenset((a, b))] if x != ti and not used[x])

    order = sorted(range(len(triangles)), key=free_neighbours)
    runs = []
    for start in order:
        if used[start]:
            continue
        used[start] = True
        t = triangles[start]
        best = None
        for rot in ((t[0], t[1], t[2]), (t[1], t[2], t[0]), (t[2], t[0], t[1])):
            s = list(rot)
            taken = []
            while True:
                i = len(s) - 2                       # the next triangle is (s[-2], s[-1], v) at position i
                nxt = None
                for x in edge_tris[frozenset((s[-2], s[-1]))]:
                    if used[x] or x in taken:
                        continue
                    v = next(k for k in triangles[x] if k not in (s[-2], s[-1]))
                    tri = (s[-2], s[-1], v) if i % 2 == 0 else (s[-1], s[-2], v)
                    if _same_winding(triangles[x], tri):
                        nxt = (x, v)
                        break
                if nxt is None:
                    break
                taken.append(nxt[0])
                s.append(nxt[1])
            if best is None or len(taken) > len(best[1]):
                best = (s, taken)
        for x in best[1]:
            used[x] = True
        runs.append(best[0])
    return runs


def _strip(triangles):
    """One triangle strip for a list of triangles: greedy runs joined by degenerate triangles (winding kept). The
    game's strips are shorter than its lists; a strip longer than the list crashed the game (a skin with a 3-index-
    per-triangle strip corrupted the heap on level load)."""
    s = []
    for run in _strips(triangles):
        if s:
            s += [s[-1], run[0]]         # degenerate triangles to jump to the next run
            if len(s) % 2:               # the run has to start on an even position to keep its winding
                s.append(run[0])
        s += run
    return s


def _vbuf(part, flags, material_hash):
    nv = len(part.positions)
    if nv > 65535:
        raise evo.EvoError("part with texture %s has %d vertices (the game's limit is 65535)" % (part.texture, nv))
    strip = _strip(part.triangles)
    lst = [i for t in part.triangles for i in t]
    out = bytearray(b"VBUF")
    out += struct.pack("<9I", 6, 0, flags, material_hash, texture_hash(part.texture), 1, nv, len(strip), len(lst))
    out += struct.pack("<I", 1)
    cols = part.colours or [0xFFFFFFFF] * nv
    for p, n, c, uv in zip(part.positions, part.normals, cols, part.uvs):
        out += struct.pack("<6fI2f", p[0], p[1], p[2], n[0], n[1], n[2], c, uv[0], uv[1])
    out += struct.pack("<%dH" % len(strip), *strip)
    out += struct.pack("<%dH" % len(lst), *lst)
    out += b"VSTP" + struct.pack("<I", 0)
    out += b"FACE" + struct.pack("<I", 0)
    ntri = max(0, len(strip) - 2)
    out += b"VBPL" + struct.pack("<I", ntri) + b"\0" * (4 * ntri)
    out += struct.pack("<2I", 1, 1)
    return bytes(out)


def _bounds(parts):
    pts = [p for part in parts for p in part.positions] or [(0.0, 0.0, 0.0)]
    mn = [min(p[i] for p in pts) for i in range(3)]
    mx = [max(p[i] for p in pts) for i in range(3)]
    centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
    radius = max(math.sqrt(sum((p[i] - centre[i]) ** 2 for i in range(3))) for p in pts)
    planes = [(0, 0, 1, -mn[2]), (0, 0, -1, mx[2]), (1, 0, 0, -mn[0]), (-1, 0, 0, mx[0]), (0, 1, 0, -mn[1]),
              (0, -1, 0, mx[1])]
    box = b"BBOX" + struct.pack("<6f", *mn, *mx) + b"".join(struct.pack("<4f", *pl) for pl in planes)
    sph = b"BSPH" + struct.pack("<4f", *centre, radius)
    return (box + sph) * 2


def _name64(name):
    b = name.encode("latin-1")
    if len(b) > 63:
        raise evo.EvoError("name too long (63 characters at most): %s" % name)
    return b + b"\0" * (64 - len(b))


def merge(parts):
    """Parts with the same texture as one (one draw batch per texture)."""
    out = {}
    for p in parts:
        m = out.get(p.texture.lower())
        if m is None:
            out[p.texture.lower()] = Part(p.texture, list(p.positions), list(p.normals), list(p.uvs),
                                          list(p.triangles), list(p.colours) if p.colours else None)
            continue
        base = len(m.positions)
        if m.colours or p.colours:
            m.colours = (m.colours or [0xFFFFFFFF] * base) + (p.colours or [0xFFFFFFFF] * len(p.positions))
        m.positions += p.positions
        m.normals += p.normals
        m.uvs += p.uvs
        m.triangles += [(a + base, b + base, c + base) for a, b, c in p.triangles]
    return list(out.values())


class SkinPart:
    """New geometry for a character's skin (kind 2) object: vertices in model space with bone weights."""

    def __init__(self, positions, normals, uvs, triangles, weights):
        self.positions = positions                # [(x, y, z)] cm, model space (bind pose)
        self.normals = normals
        self.uvs = uvs
        self.triangles = triangles                # clockwise from the front (game winding)
        self.weights = weights                    # per vertex [(bone object name, weight)]


def _adjacency(positions, triangles):
    """Per triangle the triangle across each edge (v0v1, v1v2, v2v0), matched by vertex position; none = itself."""
    key = [tuple(round(c, 3) for c in p) for p in positions]
    edges = {}
    for ti, t in enumerate(triangles):
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edges.setdefault(frozenset((key[a], key[b])), []).append(ti)
    out = []
    for ti, t in enumerate(triangles):
        adj = []
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            others = [x for x in edges[frozenset((key[a], key[b]))] if x != ti]
            adj.append(others[0] if others else ti)
        out.append(adj)
    return out


def _skin(part, header, bones, bone_world, max_influences=4):
    """SKIN chunk: header = (texture hash, material hash) of the template's; bones = the object's bone list
    [(name, node index)]; bone_world = {node index: game 4x4}."""
    index_of = {name.upper(): i for i, (name, _node) in enumerate(bones)}
    inverse = {i: evo.mat_invert_affine(bone_world[node]) for i, (_n, node) in enumerate(bones) if node in bone_world}
    nv = len(part.positions)
    if nv > 65535:
        raise evo.EvoError("the skin has %d vertices (the game's limit is 65535)" % nv)
    strip = _strip(part.triangles)
    lst = [i for t in part.triangles for i in t]
    if len(strip) > len(lst):
        raise evo.EvoError("the skin's triangle strip (%d) would be longer than its list (%d), which crashes the game "
                           "- the mesh has too many separate pieces; join them" % (len(strip), len(lst)))
    out = bytearray(b"SKIN")
    out += struct.pack("<6I", 1, nv, len(strip), len(lst), header[0], header[1])
    unbound = 0
    for p, n, uv, ws in zip(part.positions, part.normals, part.uvs, part.weights):
        ws = sorted(((index_of[b.upper()], w) for b, w in ws if b.upper() in index_of and w > 0), key=lambda t: -t[1])
        ws = [(b, w) for b, w in ws if b in inverse][:max_influences]
        if not ws:
            unbound += 1
            root = next(iter(inverse))
            ws = [(root, 1.0)]
        total = sum(w for _, w in ws)
        out += struct.pack("<I", len(ws))
        for b, w in ws:
            lp = evo.mat_apply(inverse[b], p)
            ln = evo.mat_apply(inverse[b], n, 0.0)
            out += struct.pack("<6fI2fB3xf", *lp, *ln, 0xFFFFFFFF, uv[0], uv[1], b, w / total)
    out += struct.pack("<%dH" % len(strip), *strip)
    out += struct.pack("<%dH" % len(lst), *lst)
    out += b"SSTP" + struct.pack("<I", 0)
    adj = _adjacency(part.positions, part.triangles)
    out += b"SHAD" + struct.pack("<I", len(adj))
    for a in adj:   # 6 unused bytes, the 3 neighbours, 12 unused bytes, u32 1, 1 unused byte (the game's fill: 0xFA)
        out += b"\xfa" * 6 + struct.pack("<3H", *a) + b"\xfa" * 12 + struct.pack("<I", 1) + b"\xfa"
    return bytes(out), unbound


def _header_offsets(data, start):
    """(collision name, shadow name, BSPT count) offsets in an EOBJ header."""
    p = start + 8 + 128 + 16 + 24 + 64 + 4
    nbones = struct.unpack_from("<I", data, p)[0]
    p += 4 + nbones * 132 + 4
    return p, p + 128, p + 256


def _bspt_end(data, p, count):
    """End of `count` BSPT entries starting at p."""
    for _ in range(count):
        size = struct.unpack_from("<I", data, p)[0]
        p += 8 + (8 + size if size else 0)
    return p


def _name128(name):
    return name.encode("latin-1")[:127].ljust(128, b"\0")


def _boxes(data, bbox):
    """The two (min, max) boxes stored at a BBOX offset."""
    return [(struct.unpack_from("<3f", data, bbox + 4 + k * 144), struct.unpack_from("<3f", data, bbox + 16 + k * 144))
            for k in range(2)]


def _corners(box):
    mn, mx = box
    return [(x, y, z) for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]


def _bounds_pts(first, second):
    """BBOX + BSPH for two point sets (the object's own bounds, then the second pair)."""
    out = b""
    for pts in (first, second):
        pts = pts or [(0.0, 0.0, 0.0)]
        mn = [min(p[i] for p in pts) for i in range(3)]
        mx = [max(p[i] for p in pts) for i in range(3)]
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        radius = max(math.sqrt(sum((p[i] - centre[i]) ** 2 for i in range(3))) for p in pts)
        planes = [(0, 0, 1, -mn[2]), (0, 0, -1, mx[2]), (1, 0, 0, -mn[0]), (-1, 0, 0, mx[0]), (0, 1, 0, -mn[1]),
                  (0, -1, 0, mx[1])]
        out += b"BBOX" + struct.pack("<6f", *mn, *mx) + b"".join(struct.pack("<4f", *pl) for pl in planes)
        out += b"BSPH" + struct.pack("<4f", *centre, radius)
    return out


def _helper_part(triangles, texture, to_helper):
    """Part for a #BSP_ helper object: the collision triangles moved into the helper's space, flat normals."""
    positions, normals, uvs, tris = [], [], [], []
    for (a, b, c), _surface in triangles:
        a, b, c = (tuple(evo.mat_apply(to_helper, v)) for v in (a, b, c))
        n = bsp._cross(bsp._sub(b, a), bsp._sub(c, a))
        length = math.sqrt(bsp._dot(n, n)) or 1.0
        n = (n[0] / length, n[1] / length, n[2] / length)
        k = len(positions)
        positions += [a, b, c]
        normals += [n, n, n]
        uvs += [(0.0, 0.0)] * 3
        tris.append((k, k + 1, k + 2))
    return Part(texture, positions, normals, uvs, tris)


def _points_block(points, roles):
    """Header points: u32 n, n x vec3, 5 x i32 role -> index (nothing more when n = 0)."""
    if not points:
        return struct.pack("<I", 0)
    return (struct.pack("<I", len(points)) + b"".join(struct.pack("<3f", *p) for p in points) +
            struct.pack("<5i", *roles))


def _shadow_object(template, root, new, name, model):
    """A new shadow object for a static model, laid out like the game's (335 props / weapons): a copy of the root's
    header with name #SHA_<model>, flags 0x130, parent ROOT, collision source THIS, shadow NOSHADOW, no BSPT, no
    points, no keys; the root's TEXL / MATL; bounds and one VBUF (flags 0xC0, not drawn) per Part; ends DONE."""
    head = bytearray(template[root.start:root.texl])
    col_off, sha_off, count_off = (x - root.start for x in _header_offsets(template, root.start))
    head[8:8 + 64] = _name64(name)                       # helpers carry their own name in the model field too
    head[72:72 + 64] = _name64(name)
    struct.pack_into("<I", head, 144, 0x130)              # every game shadow object (all 335 under a 0x30 root)
    ntris = sum(len(p.triangles) for p in new)
    nverts = sum(len(p.positions) for p in new)
    struct.pack_into("<2I", head, 152, ntris, nverts)
    head[176:176 + 64] = _name64("ROOT")
    nbones = struct.unpack_from("<I", head, 244)[0]
    if nbones:
        raise evo.EvoError("the model's first object is skinned - a shadow mesh can't be added to it")
    struct.pack_into("<I", head, 248, 0)                 # has keys
    head[col_off:col_off + 128] = _name128("THIS")
    head[sha_off:sha_off + 128] = _name128("NOSHADOW")
    npts = struct.unpack_from("<I", head, count_off + 4)[0]
    head = head[:count_off] + struct.pack("<2I", 0, 0) + head[count_off + 8 + (npts * 12 + 20 if npts else 0):]
    textures = [t for t in dict.fromkeys(p.texture for p in new)]
    texl = b"TEXL" + struct.pack("<I", len(textures)) + b"".join(
        struct.pack("<I", texture_hash(t)) + _name64(t) for t in textures)
    matl = template[root.matl:root.bbox]
    material = root.materials[0].hash if root.materials else 0
    vbob = b"VBOB" + struct.pack("<I", len(new)) + b"".join(_vbuf(p, 0xC0, material) for p in new)
    return bytes(head) + texl + matl + _bounds(new) + vbob + b"DONE"


def rebuild(template, parts, model_name=None, warnings=None, collision=None, points=None, add_shadow=None):
    """A new .EVO from `template` (bytes): parts = {object name: [Part, ...] for static objects, or a SkinPart for a
    character's skin} replaces those objects' geometry; model_name renames the model in every object header.
    collision = {object name: [((v0, v1, v2), surface), ...] in that object's own space (cm, game winding), or [] for
    none} replaces the object's collision tree (bsp.py; surface = Default.spl row or name) - when the template keeps
    the collision in a #BSP_ helper object, that helper's mesh is rebuilt from the same triangles. Objects without an
    entry keep the template's collision. points = {object name: {role: (x, y, z) or None}} moves / adds / removes the
    object's points (role = evo.POINT_ROLES index or name, object space, cm); roles not given keep the template's.
    add_shadow = [Part, ...] gives a static model (first object type 0: props, weapons, buildings) that has no shadow
    object a new one (#SHA_<model>, appended last, the root's shadow name set) - the game attaches it at load and works
    out its faces itself; a model that has one takes new shadow geometry through `parts` as usual.
    Keys of parts / collision / points are object names or object indices (file order) - level pieces have hundreds of
    unnamed objects, which only an index tells apart; a name key applies to every object of that name.
    Problems that don't stop the export are appended to `warnings`."""
    points = points or {}
    warnings = warnings if warnings is not None else []
    collision = collision or {}
    scene = None
    objs = walk(template)
    names = {o.name for o in objs}
    keys = set(parts) | set(collision) | set(points)
    unknown = {k for k in keys if not (isinstance(k, int) and 0 <= k < len(objs)) and k not in names}
    if unknown:
        raise evo.EvoError("the template has no object %s" % ", ".join(sorted(map(str, unknown))))

    def pick(d, i, o):
        return d[i] if i in d else d.get(o.name)

    def index_of(key):
        return key if isinstance(key, int) else next(i for i, x in enumerate(objs) if x.name == key)

    parts = dict(parts)
    trees = {}
    if collision:
        scene = evo.Scene(evo.read(template))
        for owner, tris in collision.items():
            tris = [(t, bsp.surface_index(s)) for t, s in tris]
            oi = index_of(owner)
            o = objs[oi]
            col_off, _sha, _n = _header_offsets(template, o.start)
            source = template[col_off:col_off + 128].split(b"\0", 1)[0].decode("latin-1")
            built = bsp.from_triangles(tris)
            if tris and not built:
                warnings.append("%s: the collision mesh has no usable (non-degenerate) triangles" % (o.name or oi))
            helper = source if source in names and source != o.name else None
            trees[owner] = (built, helper)
            if helper:
                hi = index_of(helper)
                if pick(parts, hi, objs[hi]) is None:
                    to_helper = evo.mat_mul(evo.mat_invert_affine(scene.objects[hi].world), scene.objects[oi].world)
                    texture = next((t for _h, t in scene.objects[hi].node.textures), "Default")
                    parts[hi] = [_helper_part(tris, texture, to_helper)]
    out = bytearray()
    for i, o in enumerate(objs):
        chunk = bytearray(template[o.start:o.end])
        rel = lambda off: off - o.start  # noqa: E731
        col_off, _sha_off, count_off = (x - o.start for x in _header_offsets(template, o.start))
        nbspt = struct.unpack_from("<I", template, o.start + count_off)[0]
        bspt_start, bspt_end = o.geom_end, _bspt_end(template, o.geom_end, nbspt)
        old_bspt = template[bspt_start:bspt_end]
        render_pts = None
        if model_name:
            chunk[8:8 + 64] = _name64(model_name)
        new = pick(parts, i, o)
        if new is not None and o.kind == 2:
            # character skin: the template's TEXL / MATL / bounds stay (the game picks the uniform texture), the
            # geometry becomes a new SKIN chunk bound to the template's bone list
            if not isinstance(new, SkinPart):
                raise evo.EvoError("%s is a character skin - it needs bone weights" % o.name)
            if scene is None:
                scene = evo.Scene(evo.read(template))
            node = next(so for so in scene.objects if so.node.name == o.name).node
            g = o.geom
            header = struct.unpack_from("<2I", template, g + 20)
            skin, unbound = _skin(new, header, node.bones, {so.index: so.world for so in scene.objects})
            if unbound:
                warnings.append("%s: %d vertices had no weight on a bone of the skeleton (bound to the root)"
                                % (o.name, unbound))
            chunk = chunk[:rel(o.geom)] + skin + chunk[rel(o.geom_end):]
            bspt_start = o.start + len(chunk) - (o.end - o.geom_end)    # the same tail follows the new geometry
        elif new is not None:
            new = merge(new)
            if o.kind not in (0, 3):
                raise evo.EvoError("%s is a skinned/cloth object - only static objects can take new geometry" % o.name)
            textures = []
            for p in new:
                if p.texture not in textures:
                    textures.append(p.texture)
            texl = b"TEXL" + struct.pack("<I", len(textures)) + b"".join(
                struct.pack("<I", texture_hash(t)) + _name64(t) for t in textures)
            material = o.materials[0].hash if o.materials else 0
            vbob = b"VBOB" + struct.pack("<I", len(new)) + b"".join(_vbuf(p, o.vbuf_flags, material) for p in new)
            render_pts = [p for part in new for p in part.positions]
            head = chunk[:rel(o.texl)] + texl + chunk[rel(o.matl):rel(o.bbox)]
            chunk = head + _bounds(new) + vbob + chunk[rel(o.geom_end):]
            o_bbox = len(head)                                           # bounds now start here (chunk-relative)
            bspt_start = o.start + len(head) + len(_bounds(new)) + len(vbob)
        tree = pick(trees, i, o)
        if new is not None and o.kind != 2 or tree is not None:
            # bounds: the object's own (render geometry, kept collision included like the game's props) and the second
            # pair (vehicle roots: the whole hierarchy - kept when the template's differed)
            if tree is not None:
                tree_pts = [v for p in bsp.polygons(bsp.read(tree[0])) for v in p.verts] if tree[0] else []
            else:
                kept = [e for e in _bspt_entries(old_bspt) if e[0] == 0]
                tree_pts = [v for p in bsp.polygons(bsp.read(kept[0][1])) for v in p.verts] if kept else []
            first, second = _boxes(template, o.bbox)
            if render_pts is not None:
                base1 = list(render_pts)
                base2 = list(render_pts) + (_corners(second) if first != second else [])
            else:
                base1, base2 = _corners(first), _corners(second)
            bounds = _bounds_pts(base1 + tree_pts, base2 + tree_pts)
            at = o_bbox if render_pts is not None else rel(o.bbox)
            chunk[at:at + len(bounds)] = bounds
        moved = pick(points, i, o)
        if moved is not None:
            # header points sit right after the BSPT count (offsets before them are unchanged)
            p0 = count_off + 4
            old_n = struct.unpack_from("<I", chunk, p0)[0]
            old_len = 4 + (old_n * 12 + 20 if old_n else 0)
            node = evo.read_node(evo.Reader(template, o.start))
            by_role = {r: node.points[i] for r, i in enumerate(node.point_roles) if 0 <= i < len(node.points)}
            for role, pos in moved.items():
                role = evo.POINT_ROLES.index(role.upper()) if isinstance(role, str) else int(role)
                if pos is None:
                    by_role.pop(role, None)
                else:
                    by_role[role] = tuple(float(c) for c in pos)
            order = sorted(by_role)
            block = _points_block([by_role[r] for r in order],
                                  [order.index(r) if r in by_role else -1 for r in range(5)])
            chunk = chunk[:p0] + block + chunk[p0 + old_len:]
            bspt_start += len(block) - old_len
            if render_pts is not None:
                o_bbox += len(block) - old_len
        if tree is not None:
            built, helper = tree
            new_bspt = bsp.chunk(built, 0) if built else b""
            at = bspt_start - o.start
            chunk = chunk[:at] + new_bspt + chunk[at + len(old_bspt):]
            struct.pack_into("<I", chunk, count_off, 1 if built else 0)
            if not helper:
                chunk[col_off:col_off + 128] = _name128("THIS" if built else "NOBSP")
        out += chunk
    if add_shadow:
        root = objs[0]
        if any(struct.unpack_from("<I", template, o.start + 144)[0] & 0x100 for o in objs):
            raise evo.EvoError("the model already has a shadow mesh - give it new geometry instead")
        if struct.unpack_from("<I", template, root.start + 140)[0] != 0:
            raise evo.EvoError("shadow meshes can only be added to static models (props, weapons, buildings); "
                               "vehicles and characters attach theirs differently")
        model = model_name or root.model
        name = "#SHA_" + model.upper()
        new = merge(add_shadow)
        shadow = _shadow_object(template, root, new, name, model)
        # the root names its shadow; the file's last object now has one after it
        col_off, sha_off, _n = _header_offsets(template, root.start)
        sha_off -= root.start
        out[sha_off:sha_off + 128] = _name128(name)
        if bytes(out[-4:]) != b"DONE":
            raise evo.EvoError("unexpected end of the model file")
        out[-4:] = b"NEXT"
        out += shadow
    return bytes(out)


def _bspt_entries(raw):
    """[(u, tree bytes)] of an object's BSPT entries."""
    out, p = [], 0
    while p < len(raw):
        size, u = struct.unpack_from("<2I", raw, p)
        out.append((u, raw[p + 16:p + 16 + size] if size else b""))
        p += 8 + (8 + size if size else 0)
    return out


# ------------------------------------------------------------------------------------------------ animations --
# .prb (FUN_00503b70): u32 2, u32 tracks, u32 bounds count (1), u32 events, u32 frames, u32 flag (1 = no events),
# 2 x u32 0xCCCCCCCC; bounds 160 B = min, max, 6 x f32 0xFEEDFACE, 6 planes, sphere (centre, radius); events 40 B each
# (walk cycles: 2 footsteps); u32 offset per track; track = {u32 key count (1 = constant), u32 keys per second,
# u32 0xFFFFFFFF, keys 52 B {pos, 3 x f32 0xFEEDFACE, scale, conjugated quaternion (x, y, z, w)}}.
_FACE = struct.pack("<I", 0xFEEDFACE)


def prb_events(template):
    """(events bytes, count, flag, frames) of a game animation, to keep its footsteps in a new version of it."""
    _v, ntracks, nbounds, nevents, frames, flag = struct.unpack_from("<6I", template, 0)
    o = 32 + nbounds * 160
    return template[o:o + 40 * nevents], nevents, flag, frames


def pack_events(events):
    """(bytes, count) of .prb events [(type, time in frames, value)] (see evo.Animation.events): 40 B each, the game's
    layout - u32 type, f32 time, 2 x 0, f32 value (footsteps: sign = foot, 1 walk / 25 run), 5 x 0."""
    out = b"".join(struct.pack("<If8xf20x", int(t), float(time), float(value))
                   for t, time, value in sorted(events, key=lambda e: e[1]))
    return out, len(events)


def write_prb(tracks, fps, points, events=b"", nevents=0, flag=None, template_frames=None, pad=40.0):
    """A .prb from per-track keys [(pos, scale, quat x y z w)] (one per frame, or one = constant); points = joint
    positions over the animation (cm, model space) for the bounds. Footstep events from a template animation keep
    their place in the cycle (times scaled to the new length)."""
    frames = max(len(t) for t in tracks)
    if events and template_frames and template_frames != frames:
        ev = bytearray(events)
        for i in range(nevents):   # event time (frames) at +4
            t = struct.unpack_from("<f", ev, 40 * i + 4)[0]
            struct.pack_into("<f", ev, 40 * i + 4, t * frames / template_frames)
        events = bytes(ev)
    if flag is None:
        flag = 0 if nevents else 1
    pts = points or [(0.0, 0.0, 0.0)]
    mn = [min(p[i] for p in pts) - pad for i in range(3)]
    mx = [max(p[i] for p in pts) + pad for i in range(3)]
    centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
    radius = math.sqrt(sum((mx[i] - mn[i]) ** 2 for i in range(3))) / 2
    planes = [(0, 0, 1, -mn[2]), (0, 0, -1, mx[2]), (1, 0, 0, -mn[0]), (-1, 0, 0, mx[0]), (0, 1, 0, -mn[1]),
              (0, -1, 0, mx[1])]
    bounds = struct.pack("<6f", *mn, *mx) + _FACE * 6 + b"".join(struct.pack("<4f", *p) for p in planes) + \
        struct.pack("<4f", *centre, radius)
    head = struct.pack("<6I", 2, len(tracks), 1, nevents, frames, flag) + struct.pack("<2I", 0xCCCCCCCC, 0xCCCCCCCC)
    body = []
    for keys in tracks:
        first = keys[0]
        const = all(max(abs(a - b) for a, b in zip(k[0] + k[1] + tuple(k[2]), first[0] + first[1] + tuple(first[2])))
                    < 1e-5 for k in keys)
        keys = [first] if const else keys
        t = struct.pack("<3I", len(keys), int(round(fps)), 0xFFFFFFFF)
        for pos, scale, q in keys:
            t += struct.pack("<3f", *pos) + _FACE * 3 + struct.pack("<3f", *scale) + struct.pack("<4f", -q[0], -q[1], -q[2], q[3])
        body.append(t)
    table_at = len(head) + len(bounds) + len(events)
    offset = table_at + 4 * len(tracks)
    table = b""
    for t in body:
        table += struct.pack("<I", offset)
        offset += len(t)
    return head + bounds + events + table + b"".join(body)
