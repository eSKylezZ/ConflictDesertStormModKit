"""Writer for Conflict: Desert Storm .EVO meshes: a new model from a game model used as the template.

The template keeps everything the game needs that isn't geometry - the object list and hierarchy, attachment points
(the hand grip, muzzle ...), node keys, collision/shadow helpers, materials - and each part given new geometry gets:
  TEXL   its textures {texture hash, name}; hash = texture_hash(name) (FUN_004b9ab0, the archive LFSR over the
         lower-cased name - the draw batches find their texture by it)
  BBOX   x2: min, max, six planes (+-axis, -min / max), then BSPH: centre, radius
  VBOB   one VBUF (version 6) per texture: vertices {pos, normal, BGRA colour, uv} in the part's own space, a
         triangle strip (the game draws strips: every model has one) + the same triangles as a list, empty VSTP
         and FACE, VBPL with a zero entry per strip triangle (the game reads past it), and the two trailing u32s
         = texture clamped on U / V (1: every coordinate in one tile, moved into tile 0; 0: the texture repeats -
         the game's own rule, FUN_005259c0).
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

The Great Escape models (EOBJ 0x34) are DS1's VBUF 6 / 44-byte influences / collision trees with DS2's 20-byte header
block, then one i32 key index after the parent (no 65535 word: the bone list starts at +240), DS2's ordering (keys and
skin bone numbers by key index, animations bound by name hash, added skin objects) and shadow objects of type -1 -
checked on all 2413 models (scripts/check_models.py --game ge).

Desert Storm II models (EOBJ 0x35, reader FUN_005087b0 in CDS II.exe) are written the same way from a DS2 template;
the differences the writer follows (checked against the game's readers and all 3280 DS2 models):
  header   20-byte block after the kind (DS1: 24), then the parent name, u32 65535, i32 key index - the bone-list count
           ends up at the same offset as in DS1, the parent name 4 bytes earlier (+172)
  VBUF     version 7 (FUN_00554d10: DS1's version-6 layout; every DS2 buffer is version 7)
  SKIN     influences 42 bytes: bone u16, no padding (CSkinObject::Read 0x556B70); the bone number is the object's
           key index (evo._cv_key_order makes the bone list key-ordered, so the writer's list position is it)
  #SHA_    type -1 and zero counts in the header block (the 45 static DS2 models with one: DS1 keeps type 0 and the
           triangle / vertex counts)
  surfaces Default.spl rows 0-50 as DS1, plus 51-62 (Snow ... Blocking Rubber) - DS1 templates refuse those
TEXL / geometry texture hashes, BBOX / BSPH, collision trees (BSPT), points and keys are DS1's layout in DS2 too.

Game space: Direct3D left-handed, Y up, centimetres, front towards -Z. Pure Python, no dependencies.
"""
import math
import struct

from . import bsp, evo

DS2_VERSION = 0x35
GE_VERSION = 0x34
# games whose .prb tracks name their object by its header hash (Desert Storm II's layout, write_prb_ds2): The Great
# Escape's 393 animations use it too; Desert Storm's have one track per object in file order (write_prb)
PRB_BY_HASH = ("ds2", "ge")
WRITABLE = {evo.VERSION: ("ds1", "Desert Storm"), GE_VERSION: ("ge", "The Great Escape"),
            DS2_VERSION: ("ds2", "Desert Storm II")}     # EOBJ versions the writer knows -> (game id, name)


class _Layout:
    """What differs between the games' object layouts (picked by the template's EOBJ version).
    Header (offsets from EOBJ): +8 model, +72 node, +136 hash, +140 type, +144 flags, +148 kind, +152 block (DS1 24 B,
    DS2 / The Great Escape 20 B), parent name, then DS1: u32 slot (+240); DS2: u32 65535, i32 key index (+240);
    The Great Escape: i32 key index (+236) - the bone-list count follows (DS1 / DS2 +244, The Great Escape +240)."""

    def __init__(self, version):
        self.version = version & 0xFFFF
        if self.version not in WRITABLE:
            raise evo.EvoError("writing models is only supported for %s so far (this template is EOBJ version 0x%X)"
                               % (", ".join(n for _g, n in WRITABLE.values()), self.version))
        self.game, self.title = WRITABLE[self.version]
        self.ds2 = self.version == DS2_VERSION
        self.ge = self.version == GE_VERSION
        self.parent = 176 if self.game == "ds1" else 172  # parent name in the header (after the 24 / 20-byte block)
        self.key_at = 236 if self.ge else 240             # key index (DS1: the object's slot)
        self.bones_at = 240 if self.ge else 244           # bone-list count; has-keys follows the list
        self.vbuf = 7 if self.ds2 else 6                  # VBUF version the writer emits
        self.influence = "<6fI2fHf" if self.ds2 else "<6fI2fB3xf"     # The Great Escape: DS1's 44-byte influence
        self.max_bones = 65536 if self.ds2 else 256
        self.surfaces = bsp.SURFACE_COUNTS[self.game]
        # DS2 / The Great Escape bind animation tracks by object name hash and number skin bones by key index: a
        # multi-part character gets skin objects of its own (+ keys); DS1 hosts the extra parts on spare connectors
        self.added_skins = self.game != "ds1"
        # shadow objects: DS1 type 0 with the triangle / vertex counts; DS2 type -1, zero block; The Great Escape
        # type -1, counts 0, buffer count, then the root's 5, 5
        self.shadow_type = 0 if self.game == "ds1" else -1


class Part:
    """New geometry for one template object: triangles over vertices in the object's own (game) space."""

    def __init__(self, texture, positions, normals, uvs, triangles, colours=None, frames=None):
        self.texture = texture                    # texture name without extension
        self.positions = positions                # [(x, y, z)] cm
        self.normals = normals                    # [(x, y, z)]
        self.uvs = uvs                            # [(u, v)], v down (DirectX)
        self.triangles = triangles                # [(a, b, c)] clockwise seen from the front (the game's winding)
        self.colours = colours                    # [0xAARRGGBB] or None (white)
        self.frames = frames                      # kind-1 (animated, e.g. tank tracks): [(positions, normals)] for
                                                  # frames 2.. (frame 1 = positions / normals), or None
        self.extra_flags = 0                      # VBUF flags added to the template's (0x03 with an alpha texture:
                                                  # Desert Storm's fences, rotor blades, foliage have it)


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
        dummy.version = node.version     # _read_skin picks the influence size by version
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


def _one_tile(values):
    """(flag, offset) for one texture axis the way Desert Storm decides it for buffers that don't store it
    (FUN_005259c0): 1 when every coordinate lies in the same whole tile - the coordinates are moved into tile 0 and
    the texture is clamped on that axis - else 0 (the texture repeats). Stored as the two u32s after VBPL (U, V); a
    tiled texture written as 1 is clamped to its edge texels (a chain-link fence's wire, U 0..32, vanished)."""
    tiles = {math.floor(x) for x in values}
    return (1, -tiles.pop()) if len(tiles) == 1 else (0, 0)


def _vbuf(part, flags, material_hash, version=6):
    nv = len(part.positions)
    if nv > 65535:
        raise evo.EvoError("part with texture %s has %d vertices (the game's limit is 65535)" % (part.texture, nv))
    strip = _strip(part.triangles)
    lst = [i for t in part.triangles for i in t]
    out = bytearray(b"VBUF")
    out += struct.pack("<9I", version, 0, flags, material_hash, texture_hash(part.texture), 1, nv, len(strip), len(lst))
    out += struct.pack("<I", 1)
    cols = part.colours or [0xFFFFFFFF] * nv
    clamp_u, du = _one_tile([uv[0] for uv in part.uvs])
    clamp_v, dv = _one_tile([uv[1] for uv in part.uvs])
    for p, n, c, uv in zip(part.positions, part.normals, cols, part.uvs):
        out += struct.pack("<6fI2f", p[0], p[1], p[2], n[0], n[1], n[2], c, uv[0] + du, uv[1] + dv)
    out += struct.pack("<%dH" % len(strip), *strip)
    out += struct.pack("<%dH" % len(lst), *lst)
    out += b"VSTP" + struct.pack("<I", 0)
    out += b"FACE" + struct.pack("<I", 0)
    ntri = max(0, len(strip) - 2)                                       # VBPL: read past by the game (0x525920)
    out += b"VBPL" + struct.pack("<I", ntri) + b"\0" * (4 * ntri)
    out += struct.pack("<2I", clamp_u, clamp_v)
    return bytes(out)


def _vlst(part, flags, material_hash):
    """One kind-1 (per-frame animated) mesh, Desert Storm's layout (FUN_0052d600, evo._read_cloth): VLST, version 3,
    0, flags, material, texture hash, strip 1, vertex / strip / list counts, frames, vertices (36 B: pos, normal,
    colour, uv), (frames - 1) x vertices x {pos, normal}, strip, list, an empty VASP block - as the game's
    T62_TRACKLEFT."""
    nv = len(part.positions)
    if nv > 65535:
        raise evo.EvoError("part with texture %s has %d vertices (the game's limit is 65535)" % (part.texture, nv))
    frames = part.frames or []
    if any(len(f[0]) != nv or len(f[1]) != nv for f in frames):
        raise evo.EvoError("every frame of an animated part needs one position and normal per vertex")
    strip = _strip(part.triangles)
    lst = [i for t in part.triangles for i in t]
    out = bytearray(b"VLST")
    out += struct.pack("<10I", 3, 0, flags, material_hash, texture_hash(part.texture), 1, nv, len(strip), len(lst),
                       1 + len(frames))
    cols = part.colours or [0xFFFFFFFF] * nv
    for p, n, c, uv in zip(part.positions, part.normals, cols, part.uvs):
        out += struct.pack("<6fI2f", p[0], p[1], p[2], n[0], n[1], n[2], c, uv[0], uv[1])
    for pos, nrm in frames:
        out += b"".join(struct.pack("<6f", *a, *b) for a, b in zip(pos, nrm))
    out += struct.pack("<%dH" % len(strip), *strip)
    out += struct.pack("<%dH" % len(lst), *lst)
    out += b"VASP" + struct.pack("<I", 0)
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
            out[p.texture.lower()] = m = Part(p.texture, list(p.positions), list(p.normals), list(p.uvs),
                                              list(p.triangles), list(p.colours) if p.colours else None)
            m.extra_flags = getattr(p, "extra_flags", 0)
            continue
        m.extra_flags |= getattr(p, "extra_flags", 0)
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

    def __init__(self, positions, normals, uvs, triangles, weights, texture=None):
        self.positions = positions                # [(x, y, z)] cm, model space (bind pose)
        self.normals = normals
        self.uvs = uvs
        self.triangles = triangles                # clockwise from the front (game winding)
        self.weights = weights                    # per vertex [(bone object name, weight)]
        self.texture = texture                    # None = keep the template's (the uniform); else this texture


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


def _skin(part, header, bones, bone_world, max_influences=4, layout=None, fill=0xFA):
    """SKIN chunk: header = (texture hash, material hash) of the template's; bones = the object's bone list
    [(name, node index)] - an influence names its bone by list position (DS2: the list is in key order, so that is
    the key index); bone_world = {node index: game 4x4}."""
    layout = layout or _Layout(evo.VERSION)
    if len(bones) > layout.max_bones:
        raise evo.EvoError("the skeleton has %d bones - more than the format can number" % len(bones))
    index_of = {name.upper(): i for i, (name, _node) in enumerate(bones)}
    inverse = {i: evo.mat_invert_affine(bone_world[node]) for i, (_n, node) in enumerate(bones) if node in bone_world}
    nv = len(part.positions)
    if nv > 65535:
        raise evo.EvoError("the skin has %d vertices (the game's limit is 65535 per skin part)" % nv)
    if len(part.triangles) > 65535:
        # SHAD numbers each triangle's neighbours in 16 bits
        raise evo.EvoError("the skin has %d triangles (the game's limit is 65535 per skin part)" % len(part.triangles))
    strip = _strip(part.triangles)
    lst = [i for t in part.triangles for i in t]
    if len(strip) > len(lst):
        # Desert Storm's skin loader (FUN_00528de0) sizes the strip's index buffer by the LIST count, so a longer strip
        # overran it (the heap crash). has-strip 0 = no strip: the loader fills the buffer from the list and both skin
        # draws (FUN_0052a5a0, shader path FUN_00529ac0) then draw it as a triangle list. Vietnam / Global Storm read
        # has-strip 0 the same way.
        strip = []
    out = bytearray(b"SKIN")
    out += struct.pack("<6I", 1 if strip else 0, nv, len(strip), len(lst), header[0], header[1])
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
            out += struct.pack(layout.influence, *lp, *ln, 0xFFFFFFFF, uv[0], uv[1], b, w / total)
    out += struct.pack("<%dH" % len(strip), *strip)
    out += struct.pack("<%dH" % len(lst), *lst)
    out += b"SSTP" + struct.pack("<I", 0)
    adj = _adjacency(part.positions, part.triangles)
    out += b"SHAD" + struct.pack("<I", len(adj))
    f = bytes([fill])
    for a in adj:   # 6 unused bytes, the 3 neighbours, 12 unused bytes, u32 1, 1 unused byte (the game's fill: 0xFA;
        # Vietnam / Global Storm 0xCD)
        out += f * 6 + struct.pack("<3H", *a) + f * 12 + struct.pack("<I", 1) + f
    return bytes(out), unbound


def _texl(textures):
    return b"TEXL" + struct.pack("<I", len(textures)) + b"".join(
        struct.pack("<I", texture_hash(t)) + _name64(t) for t in textures)


def _skin_name(o, k, taken):
    name = "%s_%d" % (o.name, k)
    if len(name) > 63:
        name = name[-63:]
    while name.upper() in taken:
        name = name[:-1] + "X" if len(name) >= 63 else name + "X"
    taken.add(name.upper())
    return name


def _skin_geometry(template, o, part, header, bones, bone_world, layout, warnings, label):
    """(TEXL, SKIN chunk) of one skin part: its own texture, or the template skin object's."""
    texture = part.texture
    skin, unbound = _skin(part, (texture_hash(texture) if texture else header[0], header[1]), bones, bone_world,
                          layout=layout)
    if unbound:
        warnings.append("%s: %d vertices had no weight on a bone of the skeleton (bound to the root)" % (label, unbound))
    return (_texl([texture]) if texture else template[o.texl:o.matl]), skin


def _extra_skin(template, o, part, name, key_index, header, bones, bone_world, model_name, layout, warnings):
    """Desert Storm II: one more skin object (EOBJ ... DONE) made from the template's skin object `o`: same header
    (parent ROOT, bone list, flags), node `name` with its name hash, its own key index (the root gets a key for it -
    a DS2 instance sizes its per-object pose arrays by the root's key count and walks every object, 0x560331 /
    0x56125D; animations bind tracks by name hash, so an object without a track is fine), its own TEXL, the
    template's MATL and bounds, a SKIN chunk of `part`. The game draws every kind-2 object of a model, skinned from
    the bone matrices, each with its own texture (0x55F240 -> 0x556290) - unless the draw gets a run-time skin
    texture (render instance +0x20C, CAnimInstance SetSkinTexture 0x55DD60), which then covers every skin object."""
    head = bytearray(template[o.start:o.texl])
    if model_name:
        head[8:8 + 64] = _name64(model_name)
    head[72:72 + 64] = _name64(name)
    struct.pack_into("<I", head, 136, evo.archive_hash(name, upper=True))   # parent / track lookups use it
    struct.pack_into("<i", head, layout.key_at, key_index)
    texl, skin = _skin_geometry(template, o, part, header, bones, bone_world, layout, warnings, name)
    return bytes(head) + texl + template[o.matl:o.geom] + skin + template[o.geom_end:o.end - 4] + b"DONE"


def _hosted_skin(template, o, host, part, header, bones, bone_world, model_name, layout, warnings):
    """Desert Storm: a skin part carried by an existing spare connector object (`host`, never a kind-3 bone: the game
    finds the feet as the n-th kind-3 object (FUN_00503610, footsteps 0x42EA80) and a hosted bone crashed the first
    footstep at 0x424D5C). DS1 can't take new objects - an
    animation has exactly one track per object slot and the pose loop reads it unchecked (0x502762), and the
    instance's per-object arrays follow the key count - so the bone keeps its name, parent, slot (+0xF0), flags and
    collision tree (hit tests) and becomes a skin (kind 2, the skin's bone list, TEXL, MATL, bounds, SKIN); its
    small marker box goes. The skinning uses only the bone matrices of the bone list (0x529A30), not the object's own,
    and the draw loop 0x5032D0 draws every kind-2 object with its own texture. The soldier's run-time skin texture
    (render instance +0x2B0, FUN_00503290: the squad's uniform) covers every skin object in the game itself;
    DesertStormFix 3.3+ (bodies.cpp SkinDrawStub at 0x503384) gives it only to the LAST kind-2 object of the model -
    so the template's skin must stay after every hosted part (_skin_hosts picks connectors before it)."""
    skin_head = template[o.start:o.texl]
    host_head = bytearray(template[host.start:host.texl])
    if model_name:
        host_head[8:8 + 64] = _name64(model_name)
    struct.pack_into("<I", host_head, 148, 2)                               # geometry kind: skinned
    b = layout.bones_at
    nb_skin = struct.unpack_from("<I", skin_head, b)[0]
    nb_host = struct.unpack_from("<I", host_head, b)[0]
    head = host_head[:b] + skin_head[b:b + 4 + 132 * nb_skin] + host_head[b + 4 + 132 * nb_host:]
    texl, skin = _skin_geometry(template, o, part, header, bones, bone_world, layout, warnings, host.name)
    return bytes(head) + texl + template[o.matl:o.geom] + skin + template[host.geom_end:host.end]


# Connectors of every Desert Storm soldier body that nothing names (no table, no mission script, not the exe): the
# objects that carry a multi-part body's extra skin parts. Never a kind-3 bone: the game finds body parts by counting
# kind-3 objects (FUN_00503610 - the n-th one, n from 0x643A68: the footstep code FUN_00424cc0 asks for the feet),
# so a bone turned into a skin made the count run short and the first footstep crashed (0x424D5C, a ported soldier
# walking in Mission 1).
SPARE_CONNECTORS = ("BIP01 R FOREARM_4 CONNECTOR", "BIP01 L FOREARM_4 CONNECTOR", "BIP01 R SHOULDER CONNECTOR",
                    "BIP01 L SHOULDER CONNECTOR", "BIP01 L HIP CONNECTOR", "BIP01 R HIP CONNECTOR")


def _skin_hosts(template, objs, skin_index, count):
    """Desert Storm: `count` objects to carry extra skin parts - the soldier's unused connectors (SPARE_CONNECTORS),
    before the skin (the plugin's uniform goes to the last skin object, which must stay the template's), without
    points, smallest marker first."""
    cands = []
    for i, o in enumerate(objs):
        if i == 0 or i >= skin_index or o.kind == 3 or (o.name or "").upper() not in SPARE_CONNECTORS:
            continue
        node = evo.read_node(evo.Reader(template, o.start))
        if node.points or node.keys:
            continue
        mn, mx = node.bbox or ((0, 0, 0), (0, 0, 0))
        cands.append(((mx[0] - mn[0]) * (mx[1] - mn[1]) * (mx[2] - mn[2]), -i, i))
    cands.sort()
    if len(cands) < count:
        raise evo.EvoError("this Desert Storm body can carry %d more skin parts (one per spare connector), not %d - join "
                           "materials / use fewer textures" % (len(cands), count))
    return [i for _v, _o, i in cands[:count]]


def _header_offsets(data, start, layout):
    """(collision name, shadow name, BSPT count) offsets in an EOBJ header."""
    p = start + layout.bones_at
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


def _shadow_object(template, root, new, name, model, layout=None):
    """A new shadow object for a static model, laid out like the game's (DS1: 335 props / weapons, DS2: 45): a copy of
    the root's header with name #SHA_<model>, flags 0x130, parent ROOT, collision source THIS, shadow NOSHADOW, no
    BSPT, no points, no keys; the root's TEXL / MATL; bounds and one VBUF (flags 0xC0, not drawn) per Part; ends
    DONE. DS1 puts the triangle / vertex counts in the header block, DS2 type -1 and zeros there."""
    layout = layout or _Layout(evo.VERSION)
    head = bytearray(template[root.start:root.texl])
    col_off, sha_off, count_off = (x - root.start for x in _header_offsets(template, root.start, layout))
    head[8:8 + 64] = _name64(name)                       # helpers carry their own name in the model field too
    head[72:72 + 64] = _name64(name)
    struct.pack_into("<I", head, 144, 0x130)              # every game shadow object (all 335 under a 0x30 root)
    ntris = sum(len(p.triangles) for p in new)
    nverts = sum(len(p.positions) for p in new)
    if layout.shadow_type == -1:
        struct.pack_into("<i", head, 140, -1)              # type -1 (DS2 sub-objects) and block counts 0, like the
        struct.pack_into("<2I", head, 152, 0, 0)          # game's own shadow objects
        if layout.ge:
            struct.pack_into("<I", head, 160, len(new))   # The Great Escape: the buffer count stays (all 12: 1)
    else:
        struct.pack_into("<2I", head, 152, ntris, nverts)
    head[layout.parent:layout.parent + 64] = _name64("ROOT")
    nbones = struct.unpack_from("<I", head, layout.bones_at)[0]
    if nbones:
        raise evo.EvoError("the model's first object is skinned - a shadow mesh can't be added to it")
    struct.pack_into("<I", head, layout.bones_at + 4, 0)  # has keys
    head[col_off:col_off + 128] = _name128("THIS")
    head[sha_off:sha_off + 128] = _name128("NOSHADOW")
    npts = struct.unpack_from("<I", head, count_off + 4)[0]
    head = head[:count_off] + struct.pack("<2I", 0, 0) + head[count_off + 8 + (npts * 12 + 20 if npts else 0):]
    textures = [t for t in dict.fromkeys(p.texture for p in new)]
    texl = b"TEXL" + struct.pack("<I", len(textures)) + b"".join(
        struct.pack("<I", texture_hash(t)) + _name64(t) for t in textures)
    matl = template[root.matl:root.bbox]
    material = root.materials[0].hash if root.materials else 0
    vbob = b"VBOB" + struct.pack("<I", len(new)) + b"".join(_vbuf(p, 0xC0, material, layout.vbuf) for p in new)
    return bytes(head) + texl + matl + _bounds(new) + vbob + b"DONE"


# ------------------------------------------------------------------------------------- Vietnam / Global Storm --
# EOBJ 0x40 (Vietnam) / 0x49 (Global Storm), from the games' loaders (Vietnam / Global Storm addresses; RE notes in the
# patch repo session report cv_cg_evo_layout.md) and checked on every shipped file (7107 / 10902 parse to the byte):
#   header  +0 EOBJ, +4 version (first object 0x30040 / 0x30049), +8 type (the first object's picks the loader: 0
#           static, 1 vertex anim, 2 hierarchical, 3 landscape, 4 water - every object the same), +12 model[64], +76
#           node[64], +140 name hash, +144 flags, +148 kind, +152 triangles, +156 vertices (sums over the buffers or 0;
#           the loader doesn't use them), +160 buffer count (= VBUF count; Global Storm keeps 5 bits), +164 collision
#           source[128] (ignored), +292 shadow[128], +420 BSPT count (Global Storm: 3 bits), +424 has-points [SCON]
#   then    TEXL, MATL (56 B entries), ONE BBOX/BSPH, geometry (kind 0/3: VBOB n x VBUF; Global Storm kind 0: u32 LOD
#           count + that many VBOBs), BSPT entries (u 0 = collision tree, u 2 = second tree falling back to u 0, u 1
#           skipped), type-2 files: hierarchy block (part, hash, parent - parents first -, BBOX/BSPH, u32, key index,
#           bone list, has-keys, skeleton[128], keys), NEXT/DONE
#   VBUF 7  version, external, flags (0x80 = colour stream, the only layout flag), material, texture hash, is-strip,
#           nv, ns, nl, 1, z0 (0 = 32-byte vertices / format 4; else 40-byte with uv2 / format 5), format (4 / 5),
#           stride (32 / 40), detail texture hash, detail tiling u, v (1.0 without one); nv x {pos, normal, uv};
#           [nv x BGRA]; strip (is-strip); list; VSTP 0; FACE (recomputed by the game - written empty); VBPL 0; two
#           "u / v within one tile" words (1 when every coordinate lies in [0, 1])
CV_VERSIONS = {0x40: ("cv", "Vietnam"), 0x49: ("cg", "Global Storm")}


def _cv_header_end(data, start):
    """Offset of TEXL in a Vietnam / Global Storm object (after has-points and the SCON block)."""
    gs = struct.unpack_from("<I", data, start + 4)[0] & 0xFFFF >= 0x49
    p = start + 428
    if struct.unpack_from("<I", data, start + 424)[0]:
        n, a, b = struct.unpack_from("<3I", data, p + 4)
        p += 16
        if n:
            p += 12 * n + (48 if gs else 28)
        if a:
            p += 24
        if b:
            p += 48
    if data[p:p + 4] != b"TEXL":
        raise evo.EvoError("unexpected object header at 0x%x" % start)
    return p


def walk_cv(data):
    """Objects of a Vietnam / Global Storm .EVO with their section offsets."""
    r = evo.Reader(data)
    out = []
    while r.p < len(data):
        o = _Obj()
        o.start = r.p
        node = evo.read_node(r)
        o.end = r.p
        o.node = node
        o.name, o.kind, o.model = node.name, node.kind, node.model
        o.materials = node.materials
        o.texl = _cv_header_end(data, o.start)
        ntex = struct.unpack_from("<I", data, o.texl + 4)[0]
        o.matl = o.texl + 8 + 68 * ntex
        nmat = struct.unpack_from("<I", data, o.matl + 4)[0]
        o.bbox = o.matl + 8 + 56 * nmat
        o.geom = o.bbox + 4 + 24 + 96 + 4 + 16
        g = evo.Reader(data, o.geom)
        dummy = evo.Node()
        dummy.version = node.version
        o.vbuf_flags = 0xC1
        if o.kind in (0, 3):
            g.tag(b"VBOB")
            first = g.p + 4
            for _ in range(g.u32()):
                evo._read_vbuf(g, dummy)
            if g.p > first:
                o.vbuf_flags = struct.unpack_from("<I", data, first + 12)[0]
            o.vbob_end = g.p
            if node.version & 0xFFFF >= 0x49 and o.kind == 0:
                lods = g.u32()
                for _ in range(lods):
                    g.tag(b"VBOB")
                    for _ in range(g.u32()):
                        evo._read_vbuf(g, dummy)
        elif o.kind == 2:
            evo._read_skin(g, dummy)
        elif o.kind == 1:
            evo._read_cloth(g, dummy)
        elif o.kind == 4:
            evo._read_water(g, dummy)
        o.geom_end = g.p
        nbspt = struct.unpack_from("<I", data, o.start + 420)[0]
        o.bspt_end = _bspt_end(data, o.geom_end, nbspt)
        out.append(o)
    return out


def _vbuf7(part, flags, material_hash):
    """A Vietnam / Global Storm VBUF (version 7, 32-byte vertices, no detail texture)."""
    nv = len(part.positions)
    if nv > 65535:
        raise evo.EvoError("part with texture %s has %d vertices (the game's limit is 65535)" % (part.texture, nv))
    strip = _strip(part.triangles)
    lst = [i for t in part.triangles for i in t]
    out = bytearray(b"VBUF")
    out += struct.pack("<10I", 7, 0, flags, material_hash, texture_hash(part.texture), 1, nv, len(strip), len(lst), 1)
    out += struct.pack("<4I2f", 0, 4, 32, 0, 1.0, 1.0)   # z0 0, format 4, stride 32, no detail texture, tiling 1, 1
    for p, n, uv in zip(part.positions, part.normals, part.uvs):
        out += struct.pack("<8f", p[0], p[1], p[2], n[0], n[1], n[2], uv[0], uv[1])
    if flags & 0x80:
        cols = part.colours or [0xFFFFFFFF] * nv
        out += struct.pack("<%dI" % nv, *cols)
    out += struct.pack("<%dH" % len(strip), *strip)
    out += struct.pack("<%dH" % len(lst), *lst)
    out += b"VSTP" + struct.pack("<I", 0) + b"FACE" + struct.pack("<I", 0) + b"VBPL" + struct.pack("<I", 0)
    us = [uv[0] for uv in part.uvs] or [0.0]
    vs = [uv[1] for uv in part.uvs] or [0.0]
    out += struct.pack("<2I", int(0.0 <= min(us) and max(us) <= 1.0), int(0.0 <= min(vs) and max(vs) <= 1.0))
    return bytes(out)


def _bounds_one(pts):
    """One BBOX + BSPH pair around points (Vietnam / Global Storm objects have one)."""
    return _bounds_pts(pts, pts)[:144]


class _CvSkinLayout:
    """Vietnam / Global Storm SKIN influences (0x4FBD33 / 0x5FC6C0): 42 bytes, u16 bone = the skeleton's key index."""
    influence = "<6fI2fHf"
    max_bones = 65536


def rebuild_cv(template, parts, model_name=None, warnings=None, collision=None, points=None, add_shadow=None,
               skeleton=None):
    """rebuild() for Vietnam / Global Storm templates: new static geometry (kinds 0 / 3), collision trees, points, and
    a character's skin (kind 2: one SkinPart; `skeleton` = the bytes of the skeleton file the body is skinned to -
    MPD_SKELETON / C04_SKELETON[_FEMALE] - whose key indices the influences number). `add_shadow` = [Part, ...]
    gives a static model (first object type 0) without a shadow object one, laid out like the shipped ones (last
    object #SHA_<MODEL>, flags 0x130, source THIS / NOSHADOW, one VBUF flags 0x40 with the root's first texture and
    MATL, Global Storm: LOD count 0; the root's shadow field names it). A skin object given several SkinParts (one
    per material) becomes that many skin objects, copies of the template's with node names <name>_2, _3 ... - both
    games draw every skin object of a body file, each skinned to the skeleton with its own texture (checked in game
    2026-10-28: Vietnam Mission 1, Global Storm Training; shipped bodies have one)."""
    warnings = warnings if warnings is not None else []
    collision = collision or {}
    version = struct.unpack_from("<I", template, 4)[0] & 0xFFFF
    game, title = CV_VERSIONS[version]
    gs = version >= 0x49
    points = points or {}
    objs = walk_cv(template)
    expanded = _cv_expand_skins(template, objs, parts, collision, points)
    if expanded is not None:
        template, parts, collision, points = expanded
        return rebuild_cv(template, parts, model_name, warnings, collision, points, add_shadow, skeleton)
    if add_shadow:
        if struct.unpack_from("<I", template, 8)[0] != 0:
            raise evo.EvoError("a shadow mesh can only be added to a static %s model (props, weapons, buildings)"
                               % title)
        if any((o.name or "").upper().startswith("#SHA_") for o in objs):
            raise evo.EvoError("the model has a shadow object already - export into it (#SHA_ object) instead")
    names = {o.name for o in objs}
    keys = set(parts) | set(collision) | set(points)
    unknown = {k for k in keys if not (isinstance(k, int) and 0 <= k < len(objs)) and k not in names}
    if unknown:
        raise evo.EvoError("the template has no object %s" % ", ".join(sorted(map(str, unknown))))
    rows = bsp.SURFACE_COUNTS[game]

    def pick(d, i, o):
        return d[i] if i in d else d.get(o.name)

    out = bytearray()
    for i, o in enumerate(objs):
        chunk = bytearray(template[o.start:o.end])
        rel = lambda off: off - o.start  # noqa: E731
        if model_name:
            chunk[12:12 + 64] = _name64(model_name)
        new = pick(parts, i, o)
        tris = pick(collision, i, o)
        moved = pick(points, i, o)
        if moved is not None:
            # SCON (Vietnam 0x4FD34F / Global Storm 0x5FDBFE): "SCON", n, a, b; n > 0: n x vec3 + one i32 per role
            # (Vietnam 7, Global Storm 12; roles 0-4 = evo.POINT_ROLES, the others kept as they are); a: 24 bytes,
            # b: 48 bytes (kept). A role moved in place keeps its point; a new one appends a point; None unsets it.
            node = o.node
            nroles = 12 if gs else 7
            pts = list(node.points)
            roles = list(getattr(node, "ds_point_roles", None) or (-1,) * nroles)
            for role, pos in moved.items():
                role = evo.POINT_ROLES.index(role.upper()) if isinstance(role, str) else int(role)
                if pos is None:
                    roles[role] = -1
                elif 0 <= roles[role] < len(pts):
                    pts[roles[role]] = tuple(float(c) for c in pos)
                else:
                    roles[role] = len(pts)
                    pts.append(tuple(float(c) for c in pos))
            raw = node.ds_points_raw or b""
            a = struct.unpack_from("<I", raw, 8)[0] if raw else 0
            b = struct.unpack_from("<I", raw, 12)[0] if raw else 0
            tail = b""
            if a:
                tail += node.ds_point_block_a
            if b:
                tail += struct.pack("<12i", *node.ds_point_pairs)
            scon = b"SCON" + struct.pack("<3I", len(pts), a, b)
            if pts:
                scon += b"".join(struct.pack("<3f", *p) for p in pts) + struct.pack("<%di" % nroles, *roles)
            scon += tail
            has = bool(pts) or bool(a) or bool(b)
            block = struct.pack("<I", 1 if has else 0) + (scon if has else b"")
            chunk = chunk[:424] + block + chunk[rel(o.texl):]
            shift = len(block) - (o.texl - o.start - 424)
            # every later offset (TEXL on) moved by the new block's size; template offsets stay for template reads
            rel = lambda off, s=shift: off - o.start + s  # noqa: E731
        if new is None and tris is None:
            out += chunk
            continue
        nbspt = struct.unpack_from("<I", template, o.start + 420)[0]
        entries = _bspt_entries(template[o.geom_end:o.bspt_end])
        if tris is not None:
            tris = [(t, bsp.surface_index(s)) for t, s in tris]
            wrong = sorted({s & 0x3FF for _t, s in tris if not 0 <= s & 0x3FF < rows})
            if wrong:
                raise evo.EvoError("surface %s isn't in %s's Default.spl (rows 0-%d)" % (
                    ", ".join(bsp.surface_name(s) for s in wrong), title, rows - 1))
            built = bsp.from_triangles(tris)
            if tris and not built:
                warnings.append("%s: the collision mesh has no usable (non-degenerate) triangles" % (o.name or i))
            # the new tree is the u = 0 one; a u = 2 tree would be the old shape (the game falls back to u = 0
            # without one), u = 1 entries aren't loaded - both dropped
            entries = ([(0, built)] if built else [])
        bspt = b"".join(bsp.chunk(t, u) for u, t in entries)
        if gs and len(entries) > 7:
            raise evo.EvoError("%s: Global Storm keeps at most 7 collision trees per object" % (o.name or i))
        tree_pts = [v for u, t in entries if u == 0 and t for p in bsp.polygons(bsp.read(t)) for v in p.verts]
        if new is not None and o.kind == 2:
            skin_parts = list(new) if isinstance(new, (list, tuple)) else [new]
            if not skin_parts or not all(isinstance(p, SkinPart) for p in skin_parts):
                raise evo.EvoError("%s is a character skin - it needs bone weights" % (o.name or i))
            if skeleton is None:
                raise evo.EvoError("%s: writing a %s character needs its skeleton file (MPD_SKELETON / C04_SKELETON "
                                   "next to the model)" % (o.name or i, title))
            # the body file holds only the skin: its influences number the skeleton's key indices (attach_skeleton
            # makes the bone list key-ordered, so the list position written is the key index)
            sc = evo.Scene(evo.attach_skeleton([evo.read_node(evo.Reader(template, o.start))], evo.read(skeleton)))
            node = sc.objects[-1].node
            part = skin_parts[0]
            header = struct.unpack_from("<2I", template, o.geom + 20)
            skin, unbound = _skin(part, (texture_hash(part.texture) if part.texture else header[0], header[1]),
                                  node.bones, {so.index: so.world for so in sc.objects}, layout=_CvSkinLayout,
                                  fill=0xCD)
            if unbound:
                warnings.append("%s: %d vertices had no weight on a bone of the skeleton (bound to the root)"
                                % (o.name or i, unbound))
            texl = _texl([part.texture]) if part.texture else bytes(chunk[rel(o.texl):rel(o.matl)])
            head = bytearray(chunk[:rel(o.texl)])
            if struct.unpack_from("<2I", head, 152) != (0, 0):
                struct.pack_into("<2I", head, 152, len(part.triangles), len(part.positions))
            chunk = (head + texl + chunk[rel(o.matl):rel(o.bbox)] + _bounds_one(list(part.positions)) + skin +
                     chunk[rel(o.geom_end):])
            out += chunk
            continue
        if new is not None:
            if o.kind not in (0, 3) or isinstance(new, SkinPart) or any(isinstance(p, SkinPart) for p in new):
                raise evo.EvoError("%s isn't a static object - only static parts can take new geometry in %s models "
                                   "so far" % (o.name or i, title))
            new = merge(new)
            if any(getattr(m, "uvs2", None) for m in o.node.meshes):
                warnings.append("%s: the original's second (lightmap) UV set isn't written - the new geometry has none"
                                % (o.name or i))
            if gs and len(new) > 31:
                raise evo.EvoError("%s has %d textures - Global Storm takes at most 31 per object" % (o.name or i,
                                                                                                    len(new)))
            textures = list(dict.fromkeys(p.texture for p in new))
            texl = b"TEXL" + struct.pack("<I", len(textures)) + b"".join(
                struct.pack("<I", texture_hash(t)) + _name64(t) for t in textures)
            material = o.materials[0].hash if o.materials else 0
            # flags per buffer: the template's buffer with the same texture (else the object's first); the colour
            # stream (0x80, the only layout flag) when the part has colours other than white
            by_tex = {}
            for m in o.node.meshes:
                by_tex.setdefault((m.texture or "Default").lower(), m.vbuf_flags)

            def flags_of(p):
                f = by_tex.get(p.texture.lower(), o.vbuf_flags)
                coloured = bool(p.colours) and any(c != 0xFFFFFFFF for c in p.colours)
                return f | 0x80 if coloured else f & ~0x80
            vbob = b"VBOB" + struct.pack("<I", len(new)) + b"".join(_vbuf7(p, flags_of(p), material) for p in new)
            if gs and o.kind == 0:
                vbob += struct.pack("<I", 0)                   # no LODs (every shipped file has none)
            render = [p for part in new for p in part.positions]
            head = bytearray(chunk[:rel(o.texl)])
            ntri = sum(len(p.triangles) for p in new)
            nver = sum(len(p.positions) for p in new)
            if struct.unpack_from("<2I", head, 152) != (0, 0):
                struct.pack_into("<2I", head, 152, ntri, nver)
            struct.pack_into("<I", head, 160, len(new))
            chunk = (head + texl + chunk[rel(o.matl):rel(o.bbox)] + _bounds_one(render + tree_pts) + vbob +
                     bspt + chunk[rel(o.bspt_end):])
        else:
            mn, mx = struct.unpack_from("<3f", template, o.bbox + 4), struct.unpack_from("<3f", template, o.bbox + 16)
            if o.kind in (0, 3):
                render = [p for m in o.node.meshes for p in m.positions]
            else:
                render = _corners((mn, mx))
            bounds = _bounds_one(render + tree_pts)
            chunk = (chunk[:rel(o.bbox)] + bounds + chunk[rel(o.bbox) + 144:rel(o.geom_end)] + bspt +
                     chunk[rel(o.bspt_end):])
        struct.pack_into("<I", chunk, 420, len(entries))
        if tris is not None:
            chunk[164:164 + 128] = _name128("THIS" if entries else "NOBSP")
        out += chunk
    if add_shadow:
        out = _cv_add_shadow(template, objs[0], out, add_shadow, model_name, gs)
    return bytes(out)


def _cv_expand_skins(template, objs, parts, collision, points):
    """Multi-part skins (rebuild_cv): the template with every skin object that was given n > 1 SkinParts repeated n
    times, and the parts / collision / points re-keyed to it - or None when no skin has several parts."""
    def given(i, o):
        new = parts[i] if i in parts else parts.get(o.name)
        return list(new) if isinstance(new, (list, tuple)) and len(new) > 1 and \
            all(isinstance(p, SkinPart) for p in new) else None
    many = {i: given(i, o) for i, o in enumerate(objs) if o.kind == 2 and given(i, o)}
    if not many:
        return None
    taken = {(o.name or "").upper() for o in objs}
    out, new_parts, index = bytearray(), {}, {}
    k = 0
    for i, o in enumerate(objs):
        chunk = template[o.start:o.end]
        if chunk[-4:] not in (b"NEXT", b"DONE"):
            raise evo.EvoError("unexpected end of object %s" % (o.name or i))
        copies = many.get(i) or [None]
        index[i] = k
        for c, part in enumerate(copies):
            piece = bytearray(chunk)
            piece[-4:] = b"DONE" if i == len(objs) - 1 and c == len(copies) - 1 else b"NEXT"
            if c:
                piece[76:76 + 64] = _name64(_skin_name(o, c + 1, taken))
            if part is not None:
                new_parts[k] = [part]
            out += piece
            k += 1
    expanded_names = {objs[i].name for i in many}

    def rekey(d, skip=False):
        res = {}
        for key, v in (d or {}).items():
            if skip and (key in many or key in expanded_names):
                continue
            res[index[key] if isinstance(key, int) and key in index else key] = v
        return res
    new_parts.update(rekey(parts, skip=True))
    return bytes(out), new_parts, rekey(collision), rekey(points)


def _cv_add_shadow(template, root, out, shadow_parts, model_name, gs):
    """Append a shadow object to a rebuilt Vietnam / Global Storm static model (rebuild_cv's add_shadow)."""
    model = model_name or template[root.start + 12:root.start + 76].split(b"\0")[0].decode("latin-1")
    name = ("#SHA_" + model)[:63]
    if not out.endswith(b"DONE"):
        raise evo.EvoError("unexpected end of the model")
    # the root's shadow field names the new object (written into the rebuilt root, which starts the output)
    out = bytearray(out)
    out[292:292 + 128] = _name128(name)
    ntex = struct.unpack_from("<I", template, root.texl + 4)[0]
    texture = (template[root.texl + 12:root.texl + 76].split(b"\0")[0].decode("latin-1") if ntex else "") or "Default"
    part = merge(shadow_parts)
    one = Part(texture, [p for q in part for p in q.positions], [n for q in part for n in q.normals],
               [uv for q in part for uv in q.uvs], [])
    base = 0
    for q in part:
        one.triangles += [(a + base, b + base, c + base) for a, b, c in q.triangles]
        base += len(q.positions)
    head = bytearray(template[root.start:root.start + 424])
    head[12:12 + 64] = _name64(name)
    head[76:76 + 64] = _name64(name)
    struct.pack_into("<6I", head, 140, 0, 0x130, 0, 0, 0, 1)    # hash, flags, kind, triangles, vertices, buffers
    head[164:164 + 128] = _name128("THIS")
    head[292:292 + 128] = _name128("NOSHADOW")
    struct.pack_into("<I", head, 420, 0)                         # no collision trees
    material = root.materials[0].hash if root.materials else 0
    nmat = struct.unpack_from("<I", template, root.matl + 4)[0]
    matl = b"MATL" + struct.pack("<I", min(nmat, 1)) + template[root.matl + 8:root.matl + 8 + 56 * min(nmat, 1)]
    obj = (bytes(head) + struct.pack("<I", 0) + _texl([texture]) + matl +      # shipped shadows: one material
           _bounds_one(list(one.positions)) + b"VBOB" + struct.pack("<I", 1) + _vbuf7(one, 0x40, material) +
           (struct.pack("<I", 0) if gs else b"") + b"DONE")
    return out[:-4] + b"NEXT" + obj


def rebuild(template, parts, model_name=None, warnings=None, collision=None, points=None, add_shadow=None,
            skeleton=None, key_offsets=None):
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
    key_offsets = {object name / index: (dx, dy, dz)} moves objects in their parent's space by shifting their rest
    key (Desert Storm / DS2 / Great Escape hierarchies: the root's key list) - e.g. a hat connector onto a taller head.
    Problems that don't stop the export are appended to `warnings`."""
    if len(template) >= 8 and struct.unpack_from("<I", template, 4)[0] & 0xFFFF in CV_VERSIONS:
        if key_offsets:
            raise evo.EvoError("moving objects' keys isn't supported for Vietnam / Global Storm models yet")
        return rebuild_cv(template, parts, model_name, warnings, collision, points, add_shadow, skeleton)
    points = points or {}
    warnings = warnings if warnings is not None else []
    collision = collision or {}
    scene = None
    head = evo.read_node(evo.Reader(template))
    root_keys = len(head.keys)            # (head is reused for byte buffers below)
    # the writer knows Desert Storm's, The Great Escape's and Desert Storm II's object layouts; Vietnam / Global Storm
    # models rewritten with them would be corrupt (_Layout refuses them)
    layout = _Layout(head.version)
    objs = walk(template)
    names = {o.name for o in objs}
    key_offsets = key_offsets or {}
    keys = set(parts) | set(collision) | set(points) | set(key_offsets)
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
            wrong = sorted({s & 0x3FF for _t, s in tris if not 0 <= s & 0x3FF < layout.surfaces})
            if wrong:
                raise evo.EvoError("surface %s isn't in this game's Default.spl (%s has rows 0-%d)" % (
                    ", ".join(bsp.surface_name(s) for s in wrong), layout.title,
                    layout.surfaces - 1))
            oi = index_of(owner)
            o = objs[oi]
            col_off, _sha, _n = _header_offsets(template, o.start, layout)
            source = template[col_off:col_off + 128].split(b"\0", 1)[0].decode("latin-1")
            built = bsp.from_triangles(tris)
            if tris and not built:
                warnings.append("%s: the collision mesh has no usable (non-degenerate) triangles" % (o.name or oi))
            helper = source if source in names and source != o.name else None
            if not built:
                # no collision: the object says NOBSP and its #BSP_ helper keeps its mesh (an emptied helper crashes
                # Desert Storm's model loader - seen with a ported vehicle gun that has no collision)
                helper = None
            trees[owner] = (built, helper)
            if helper:
                hi = index_of(helper)
                if pick(parts, hi, objs[hi]) is None:
                    to_helper = evo.mat_mul(evo.mat_invert_affine(scene.objects[hi].world), scene.objects[oi].world)
                    texture = next((t for _h, t in scene.objects[hi].node.textures), "Default")
                    parts[hi] = [_helper_part(tris, texture, to_helper)]
    out = bytearray()
    extra = []                                  # DS2: added skin objects (multi-part characters), appended at the end
    taken = {o.name.upper() for o in objs}
    hosted = {}                                 # DS1: bone object index -> (skin object index, SkinPart)
    if not layout.added_skins:
        for key, new in parts.items():
            if not isinstance(new, (list, tuple)) or len(new) < 2:
                continue
            si = index_of(key)
            if objs[si].kind != 2:
                continue
            for hi, part in zip(_skin_hosts(template, objs, si, len(new) - 1), new[1:]):
                hosted[hi] = (si, part)
        busy = [h for h in hosted if pick(parts, h, objs[h]) is not None or pick(collision, h, objs[h]) is not None
                or pick(points, h, objs[h]) is not None]
        if busy:
            raise evo.EvoError("%s carries a skin part - it can't take other changes" % objs[busy[0]].name)
    root_len = None
    for i, o in enumerate(objs):
        if i in hosted:
            si, part = hosted[i]
            so = objs[si]
            if scene is None:
                scene = evo.Scene(evo.read(template))
            node = next(x for x in scene.objects if x.node.name == so.name).node
            header = struct.unpack_from("<2I", template, so.geom + 20)
            out += _hosted_skin(template, so, o, part, header, node.bones, {x.index: x.world for x in scene.objects},
                                model_name, layout, warnings)
            if i == 0:
                root_len = len(out)
            continue
        chunk = bytearray(template[o.start:o.end])
        rel = lambda off: off - o.start  # noqa: E731
        col_off, _sha_off, count_off = (x - o.start for x in _header_offsets(template, o.start, layout))
        nbspt = struct.unpack_from("<I", template, o.start + count_off)[0]
        bspt_start, bspt_end = o.geom_end, _bspt_end(template, o.geom_end, nbspt)
        old_bspt = template[bspt_start:bspt_end]
        render_pts = None
        if model_name:
            chunk[8:8 + 64] = _name64(model_name)
        new = pick(parts, i, o)
        if new is not None and o.kind == 2:
            # character skin: the template's MATL / bounds stay, the geometry becomes a new SKIN chunk bound to the
            # template's bone list; TEXL too unless the part names its own texture (the uniform texture the game
            # sets at run time still covers every skin object - see _extra_skin). More than one SkinPart: the
            # first replaces this object, the others become skin objects of their own appended to the file
            skin_parts = list(new) if isinstance(new, (list, tuple)) else [new]
            if not skin_parts or not all(isinstance(p, SkinPart) for p in skin_parts):
                raise evo.EvoError("%s is a character skin - it needs bone weights" % o.name)
            if scene is None:
                scene = evo.Scene(evo.read(template))
            node = next(so for so in scene.objects if so.node.name == o.name).node
            g = o.geom
            header = struct.unpack_from("<2I", template, g + 20)
            bone_world = {so.index: so.world for so in scene.objects}
            first = skin_parts[0]
            skin, unbound = _skin(first, (texture_hash(first.texture) if first.texture else header[0], header[1]),
                                  node.bones, bone_world, layout=layout)
            if unbound:
                warnings.append("%s: %d vertices had no weight on a bone of the skeleton (bound to the root)"
                                % (o.name, unbound))
            chunk = chunk[:rel(o.geom)] + skin + chunk[rel(o.geom_end):]
            if first.texture:
                chunk = chunk[:rel(o.texl)] + _texl([first.texture]) + chunk[rel(o.matl):]
            bspt_start = o.start + len(chunk) - (o.end - o.geom_end)    # the same tail follows the new geometry
            for k, part in enumerate(skin_parts[1:], 2):
                if layout.added_skins:
                    extra.append((o, part, _skin_name(o, k, taken), header, node.bones, bone_world))
                # Desert Storm: the other parts are carried by bones chosen before the loop (hosted)
        elif new is not None and o.kind == 1:
            # an animated (kind-1) object - tank tracks: new frames in the template's VAOB layout
            new = list(new)
            textures = list(dict.fromkeys(p.texture for p in new))
            texl = b"TEXL" + struct.pack("<I", len(textures)) + b"".join(
                struct.pack("<I", texture_hash(t)) + _name64(t) for t in textures)
            material = o.materials[0].hash if o.materials else 0
            vl = o.geom + 12                                          # VAOB tag, count, frames; then the first VLST
            ver = struct.unpack_from("<I", template, vl + 4)[0]
            flags = struct.unpack_from("<I", template, vl + 8 + (4 if ver >= 2 else 0))[0]
            nframes = max(1 + len(p.frames or []) for p in new)
            vaob = b"VAOB" + struct.pack("<2I", len(new), nframes) + b"".join(_vlst(p, flags, material) for p in new)
            render_pts = [v for p in new for v in p.positions] +                 [v for p in new for pos, _n in (p.frames or []) for v in pos]
            box = Part("", render_pts, [], [], [])
            head = chunk[:rel(o.texl)] + texl + chunk[rel(o.matl):rel(o.bbox)]
            chunk = head + _bounds([box]) + vaob + chunk[rel(o.geom_end):]
            o_bbox = len(head)
            bspt_start = o.start + len(head) + len(_bounds([box])) + len(vaob)
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
            vbob = b"VBOB" + struct.pack("<I", len(new)) + b"".join(
                _vbuf(p, o.vbuf_flags | getattr(p, "extra_flags", 0), material, layout.vbuf) for p in new)
            render_pts = [p for part in new for p in part.positions]
            head = bytearray(chunk[:rel(o.texl)] + texl + chunk[rel(o.matl):rel(o.bbox)])
            if struct.unpack_from("<I", template, o.start + 4)[0] & 0xFFFF in (0x2F, 0x35):
                # Desert Storm / DS2 header +0x98: triangles, vertices, parts of the object's geometry. Desert Storm
                # builds its world-piece buffers from them: a zone object written onto another kept that one's counts
                # and drew untextured (props, which mostly hold 0 there, didn't care)
                struct.pack_into("<3I", head, 0x98, sum(len(p.triangles) for p in new),
                                 sum(len(p.positions) for p in new), len(new))
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
        if i == 0:
            root_len = len(out)
    if key_offsets:
        # the root's key list ends its chunk: u32 count, count x 52 B {pos, unused, scale, quat}, then the end tag
        nkeys = root_keys
        count_at = root_len - 4 - 52 * nkeys - 4
        if not nkeys or struct.unpack_from("<I", out, count_at)[0] != nkeys:
            raise evo.EvoError("the template's first object has no key list - can't move objects")
        for i, o in enumerate(objs):
            d = pick(key_offsets, i, o)
            if d is None:
                continue
            ki = struct.unpack_from("<i", template, o.start + layout.key_at)[0]
            if not 0 <= ki < nkeys:
                raise evo.EvoError("object %s has no key to move" % (o.name or i))
            at = count_at + 4 + 52 * ki
            pos = struct.unpack_from("<3f", out, at)
            struct.pack_into("<3f", out, at, *(a + b for a, b in zip(pos, d)))
    if extra:
        # DS2: one key per added object, appended to the root's list (a copy of the template skin's key)
        nkeys = len(head.keys)
        count_at = root_len - 4 - 52 * nkeys - 4
        if not nkeys or struct.unpack_from("<I", out, count_at)[0] != nkeys:
            raise evo.EvoError("the template's first object has no key list - can't add skin objects to it")
        added = b""
        for o, _part, _name, _header, _bones, _world in extra:
            ki = struct.unpack_from("<i", template, o.start + layout.key_at)[0]
            added += bytes(out[count_at + 4 + 52 * ki:count_at + 4 + 52 * (ki + 1)])
        struct.pack_into("<I", out, count_at, nkeys + len(extra))
        out[root_len - 4:root_len - 4] = added
        for j, (o, part, name, header, bones, bone_world) in enumerate(extra):
            obj = _extra_skin(template, o, part, name, nkeys + j, header, bones, bone_world, model_name, layout,
                              warnings)
            if bytes(out[-4:]) != b"DONE":
                raise evo.EvoError("unexpected end of the model file")
            out[-4:] = b"NEXT"
            out += obj
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
        shadow = _shadow_object(template, root, new, name, model, layout)
        # the root names its shadow; the file's last object now has one after it
        col_off, sha_off, _n = _header_offsets(template, root.start, layout)
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


# Desert Storm II .prb (CAnim binary load 0x5625D0): DS1's header, bounds and events; the bounds' 6 FEEDFACE words
# hold the box's half size and centre; track = {u32 object name hash (EOBJ header hash), u32 flags (1, 3 = connector),
# u16 key count, u16 keys per second, u32 0xFFFFFFFF, keys 28 B {pos, conjugated quaternion}} - no scale. The game
# binds tracks to the skeleton's objects by hash; the ones it doesn't find are skipped.

def prb_track_flags(name):
    """DS2 track flags for an object name: 3 for connectors (the .prs reader 0x5635A0 adds 2 for "CONNECTOR"), else 1."""
    return 3 if "CONNECTOR" in name.upper() else 1


def write_prb_ds2(tracks, fps, points, hashes, flags, events=b"", nevents=0, flag=None, template_frames=None,
                  pad=40.0, bounds=None, rates=None, compress=True, pad_words=b"\xcc" * 8, key_size=28):
    """A Desert Storm II / The Great Escape .prb: tracks [(pos, scale, quat x y z w)] per object (key_size 28 = DS2:
    pos, quat - scale not stored; 40 = The Great Escape: pos, scale, quat), hashes / flags per track (evo.Animation.hashes / track_flags, or EOBJ header hashes + prb_track_flags).
    points / pad / events / template_frames as write_prb. bounds = raw 160-byte blocks to keep (else one block from
    the points), rates = per-track keys per second (else fps), compress = a track whose keys are all equal is written
    as one key (off for an exact rewrite of a game file)."""
    if not (len(tracks) == len(hashes) == len(flags)):
        raise evo.EvoError("tracks, hashes and flags differ in length")
    frames = max(len(t) for t in tracks)
    if frames > 65535:
        raise evo.EvoError("the animation has %d frames (65535 at most)" % frames)
    if events and template_frames and template_frames != frames:
        ev = bytearray(events)
        for i in range(nevents):
            t = struct.unpack_from("<f", ev, 40 * i + 4)[0]
            struct.pack_into("<f", ev, 40 * i + 4, t * frames / template_frames)
        events = bytes(ev)
    if flag is None:
        flag = 0 if nevents else 1
    if bounds is None:
        pts = points or [(0.0, 0.0, 0.0)]
        mn = [min(p[i] for p in pts) - pad for i in range(3)]
        mx = [max(p[i] for p in pts) + pad for i in range(3)]
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        half = [(mx[i] - mn[i]) / 2 for i in range(3)]
        radius = math.sqrt(sum(h * h for h in half))
        planes = [(0, 0, 1, -mn[2]), (0, 0, -1, mx[2]), (1, 0, 0, -mn[0]), (-1, 0, 0, mx[0]), (0, 1, 0, -mn[1]),
                  (0, -1, 0, mx[1])]
        bounds = struct.pack("<12f", *mn, *mx, *half, *centre) + b"".join(struct.pack("<4f", *p) for p in planes) + \
            struct.pack("<4f", *centre, radius)
    if len(bounds) % 160:
        raise evo.EvoError("bounds blocks are 160 bytes each")
    head = struct.pack("<6I", 2, len(tracks), len(bounds) // 160, nevents, frames, flag) + pad_words
    body = []
    for n, keys in enumerate(tracks):
        first = keys[0]
        if compress and all(max(abs(a - b) for a, b in zip(tuple(k[0]) + tuple(k[1]) + tuple(k[2]),
                                                            tuple(first[0]) + tuple(first[1]) + tuple(first[2]))) < 1e-5
                            for k in keys):
            keys = [first]
        rate = rates[n] if rates else int(round(fps))
        t = struct.pack("<2I2HI", hashes[n], flags[n], len(keys), rate, 0xFFFFFFFF)
        for pos, scale, q in keys:
            if key_size == 40:
                t += struct.pack("<10f", pos[0], pos[1], pos[2], *scale, -q[0], -q[1], -q[2], q[3])
            else:
                t += struct.pack("<7f", pos[0], pos[1], pos[2], -q[0], -q[1], -q[2], q[3])
        body.append(t)
    offset = len(head) + len(bounds) + len(events) + 4 * len(tracks)
    table = b""
    for t in body:
        table += struct.pack("<I", offset)
        offset += len(t)
    return head + bounds + events + table + b"".join(body)


def rewrite_prb(anim):
    """The bytes of an animation read by evo.read_prb from a Desert Storm II / The Great Escape file, written back
    (byte for byte when nothing was changed)."""
    if anim.game == "cg":
        return rewrite_ani_cg(anim)
    raw = anim.raw
    if anim.game == "cv":
        return write_prb_cv(anim.deltas, anim.fps, None, anim.hashes, anim.track_flags, raw["events"],
                            len(raw["events"]) // 40, raw["flag"], bounds=raw["bounds"], rates=anim.rates,
                            compress=False, pad_words=raw["pad"])
    if anim.game != "ds2":
        raise evo.EvoError("rewrite_prb is for Desert Storm II / The Great Escape / Vietnam animations")
    return write_prb_ds2(anim.tracks, anim.fps, None, anim.hashes, anim.track_flags, raw["events"],
                         len(raw["events"]) // 40, raw["flag"], bounds=raw["bounds"], rates=anim.rates,
                         compress=False, pad_words=raw["pad"], key_size=raw.get("key", 28))


# Conflict: Vietnam .prb: Desert Storm II's header, bounds, events and track header {u32 object name hash, u32 flags,
# u16 key count, u16 keys per second, u32 0xFFFFFFFF}; flags 1 / 3 (connector) as DS2, + 4 = the track has positions.
# Keys hold the rotation RELATIVE to the bone's rest rotation (the model's key): flag 4 = 28 B {quat x y z w (not
# conjugated), vec3 position (absolute, local to the parent)}, else 12 B rotation vector (axis x angle, |v| <= pi) and
# the bone keeps its rest position. All 409 shipped files: 1 or 3 (+4) only, a key per frame or one constant key, track
# rates differ per track (kept by rewrite_prb).

def cv_deltas(scene, tracks, pos_epsilon=0.01):
    """Vietnam deltas [(pos or None, rest-relative quat)] per track from plain local keys [(pos, scale, quat)] of the
    objects `tracks` names: (object index, keys) pairs. A track gets positions (flag 4) when any key leaves the rest
    position by more than pos_epsilon cm. Returns (deltas, flag bits 4 per track)."""
    rest = scene.nodes[0].keys if scene.nodes and len(scene.nodes[0].keys) == len(scene.objects) else None
    out, bits = [], []
    for i, keys in tracks:
        rpos, _rs, rq = rest[i] if rest is not None else ((0.0, 0.0, 0.0), None, (0.0, 0.0, 0.0, 1.0))
        inv = (-rq[0], -rq[1], -rq[2], rq[3])
        moved = any(math.dist(tuple(k[0]), tuple(rpos)) > pos_epsilon for k in keys)
        out.append([(tuple(k[0]) if moved else None, evo._qmul(tuple(k[2]), inv)) for k in keys])
        bits.append(4 if moved else 0)
    return out, bits


def write_prb_cv(deltas, fps, points, hashes, flags, events=b"", nevents=0, flag=None, template_frames=None,
                 pad=40.0, bounds=None, rates=None, compress=True, pad_words=b"\xcc" * 8):
    """A Conflict: Vietnam .prb from rest-relative deltas per track [(pos or None, quat)] (evo.Animation.deltas, or
    cv_deltas). flags per track: bit 4 = positions (28-byte keys), else 12-byte rotation vectors; a track with bit 4
    but a None position is refused. Other arguments as write_prb_ds2."""
    if not (len(deltas) == len(hashes) == len(flags)):
        raise evo.EvoError("tracks, hashes and flags differ in length")
    frames = max(len(t) for t in deltas)
    if frames > 65535:
        raise evo.EvoError("the animation has %d frames (65535 at most)" % frames)
    if events and template_frames and template_frames != frames:
        ev = bytearray(events)
        for i in range(nevents):
            t = struct.unpack_from("<f", ev, 40 * i + 4)[0]
            struct.pack_into("<f", ev, 40 * i + 4, t * frames / template_frames)
        events = bytes(ev)
    if flag is None:
        flag = 0 if nevents else 1
    if bounds is None:
        pts = points or [(0.0, 0.0, 0.0)]
        mn = [min(p[i] for p in pts) - pad for i in range(3)]
        mx = [max(p[i] for p in pts) + pad for i in range(3)]
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        half = [(mx[i] - mn[i]) / 2 for i in range(3)]
        radius = math.sqrt(sum(h * h for h in half))
        planes = [(0, 0, 1, -mn[2]), (0, 0, -1, mx[2]), (1, 0, 0, -mn[0]), (-1, 0, 0, mx[0]), (0, 1, 0, -mn[1]),
                  (0, -1, 0, mx[1])]
        bounds = struct.pack("<12f", *mn, *mx, *half, *centre) + b"".join(struct.pack("<4f", *p) for p in planes) + \
            struct.pack("<4f", *centre, radius)
    if len(bounds) % 160:
        raise evo.EvoError("bounds blocks are 160 bytes each")
    head = struct.pack("<6I", 2, len(deltas), len(bounds) // 160, nevents, frames, flag) + pad_words
    body = []
    for n, keys in enumerate(deltas):
        has_pos = bool(flags[n] & 4)
        if has_pos and any(p is None for p, _q in keys):
            raise evo.EvoError("track %d has positions (flag 4) but a key without one" % n)
        packed = []
        for p, q in keys:
            if has_pos:
                packed.append(struct.pack("<7f", q[0], q[1], q[2], q[3], p[0], p[1], p[2]))
            else:
                packed.append(struct.pack("<3f", *evo._q2rv(q)))
        if compress and len(set(packed)) == 1:
            packed = packed[:1]
        rate = rates[n] if rates else int(round(fps))
        body.append(struct.pack("<2I2HI", hashes[n], flags[n], len(packed), rate, 0xFFFFFFFF) + b"".join(packed))
    offset = len(head) + len(bounds) + len(events) + 4 * len(deltas)
    table = b""
    for t in body:
        table += struct.pack("<I", offset)
        offset += len(t)
    return head + bounds + events + table + b"".join(body)


# Conflict: Global Storm animation (Animations\<name>.ani; the unnamed STATE_* / OVERLAY_* .BIN files; loader
# FUN_0061A1A0, exporter FUN_00619A80): name[44] (upper case, NUL, 0xCC fill), f32 duration, u32 A position tracks,
# u32 N rotation tracks, u32 B constant rotations, u16 position keys, u16 rotation keys, u32 C root samples;
# N x {u32 bone, f32 1.0, keys x quat}; A x {u32 bone, f32 1.0, keys x vec3 offset from the bone's .SKL
# LocalPosition}; B x {u32 bone, f32 1.0, quat}; C x {vec3, quat} (30 Hz root motion). Bones = .SKL indices; a bone
# with no track has the identity rotation and no offset (see evo.Animation._resolve_cg for the pose). All 466 shipped:
# per-track float 1.0, position keys = rotation keys but in 2.

def _ani_name(name):
    if isinstance(name, (bytes, bytearray)) and len(name) == 44:
        return bytes(name)
    text = "".join(c if c.isalnum() or c == "_" else "_" for c in str(name).upper())[:43] or "ANIMATION"
    raw = text.encode("ascii", "replace")
    return raw + b"\0" + b"\xcc" * (43 - len(raw))


def write_ani_cg(name, duration, rot, pos, const, root=(), rot_frames=None, pos_frames=None):
    """A Global Storm .ani: rot {bone: [quat x y z w per key]}, pos {bone: [offset per key]}, const {bone: quat},
    root [(vec3, quat)] root-motion samples (kept from a game animation, else none). Tracks are written in the dicts'
    order (each may also be a list of (bone, keys) pairs - a game file's own order, repeats included). rot_frames =
    the key count when no track says it (all rotations constant). name = text (upper-cased, letters / digits / _ -
    the game's own names) or the 44 raw bytes."""
    rot, pos, const = [list(x.items()) if isinstance(x, dict) else list(x) for x in (rot, pos, const)]
    rf = rot_frames if rot_frames is not None else (len(rot[0][1]) if rot else len(pos[0][1]) if pos else 1)
    pf = pos_frames if pos_frames is not None else (len(pos[0][1]) if pos else rf)
    if rf > 65535 or pf > 65535:
        raise evo.EvoError("the animation has %d frames (65535 at most)" % max(rf, pf))
    for b, keys in rot:
        if len(keys) != rf:
            raise evo.EvoError("rotation track of bone %d has %d keys, not %d" % (b, len(keys), rf))
    for b, keys in pos:
        if len(keys) != pf:
            raise evo.EvoError("position track of bone %d has %d keys, not %d" % (b, len(keys), pf))
    out = bytearray(_ani_name(name))
    out += struct.pack("<f3I2HI", duration, len(pos), len(rot), len(const), pf, rf, len(root))
    for b, keys in rot:
        out += struct.pack("<If", b, 1.0) + b"".join(struct.pack("<4f", *q) for q in keys)
    for b, keys in pos:
        out += struct.pack("<If", b, 1.0) + b"".join(struct.pack("<3f", *v) for v in keys)
    for b, q in const:
        out += struct.pack("<If4f", b, 1.0, *q)
    for v, q in root:
        out += struct.pack("<7f", *v, *q)
    return bytes(out)


def rewrite_ani_cg(anim):
    """The bytes of a Global Storm animation read by evo.read_ani_cg, written back (byte for byte when unchanged)."""
    cg = anim.cg
    it = cg.get("items") or {"rot": cg["rot"], "pos": cg["pos"], "const": cg["const"]}
    return write_ani_cg(cg["name_raw"], cg["duration"], it["rot"], it["pos"], it["const"], cg["root"],
                        cg["rf_raw"], cg["pf_raw"])


def cg_tracks(scene, tracks, pos_epsilon=0.01, const_epsilon=1e-6):
    """(rot, pos, const) of a Global Storm .ani from plain local keys per object of `scene` (tracks[i] = [(pos, scale,
    quat)] per frame, or None / [] = rest) - the inverse of evo.Animation._resolve_cg. The scene needs its skeleton's
    .SKL (evo.skeleton_of). Per .SKL bone: the object of that name gives the world transform; world rotation x
    FixOrientation^-1 = the bone's own, local = parent^-1 x own; position offset = R(parent)^-1 (p - parent p) -
    LocalPosition. A bone whose local rotation changes gets a rotation track, a constant one a constant; a position
    track only where the offset leaves 0 by more than pos_epsilon cm."""
    skl = evo.skeleton_of(scene)
    if not skl:
        raise evo.EvoError("the model's skeleton bone list (.SKL next to the skeleton .EVO) isn't there")
    names = {o.name.upper().replace("_", " "): o.index for o in scene.objects}
    obj = [names.get(b[0].upper().replace("_", " ")) for b in skl]
    frames = max([len(t) for t in tracks if t] + [1])
    anim = evo.Animation()
    anim.frames = frames
    def rest(o):
        m = o.local
        return ((m[0][3], m[1][3], m[2][3]), (1.0, 1.0, 1.0), evo._quat_of(m))

    anim.tracks = [list(t) if t else [rest(o)] for t, o in zip(tracks, scene.objects)]
    anim.hashes = None
    lq = [[] for _ in skl]
    lo = [[] for _ in skl]
    for f in range(frames):
        worlds = evo.anim_worlds(scene, anim, f)
        hq, hp = {}, {}

        def bone(i, depth=0):
            if i in hq:
                return
            _n, parent, lpos, fix = skl[i]
            has_parent = 0 <= parent < len(skl) and depth < 256
            if has_parent:
                bone(parent, depth + 1)
            if obj[i] is not None:
                w = worlds[obj[i]]
                hp[i] = (w[0][3], w[1][3], w[2][3])
                hq[i] = evo._qmul(evo._quat_of(w), (-fix[0], -fix[1], -fix[2], fix[3]))
            elif has_parent:
                hq[i] = hq[parent]
                rp = evo.mat_apply(evo.mat_from_trs((0.0, 0.0, 0.0), hq[parent], (1.0, 1.0, 1.0)), lpos, 0.0)
                hp[i] = (hp[parent][0] + rp[0], hp[parent][1] + rp[1], hp[parent][2] + rp[2])
            else:
                hq[i], hp[i] = (0.0, 0.0, 0.0, 1.0), tuple(lpos)
            if has_parent:
                pq = hq[parent]
                inv = (-pq[0], -pq[1], -pq[2], pq[3])
                q = evo._qmul(inv, hq[i])
                d = (hp[i][0] - hp[parent][0], hp[i][1] - hp[parent][1], hp[i][2] - hp[parent][2])
                lp = evo.mat_apply(evo.mat_from_trs((0.0, 0.0, 0.0), inv, (1.0, 1.0, 1.0)), d, 0.0)
            else:
                q, lp = hq[i], hp[i]
            if lq[i] and sum(a * b for a, b in zip(q, lq[i][-1])) < 0:
                q = (-q[0], -q[1], -q[2], -q[3])          # same hemisphere as the previous key (interpolation)
            lq[i].append(q)
            lo[i].append((lp[0] - lpos[0], lp[1] - lpos[1], lp[2] - lpos[2]))

        for i in range(len(skl)):
            bone(i)
    rot, pos, const = {}, {}, {}
    for i in range(len(skl)):
        first = lq[i][0]
        if any(max(abs(a - b) for a, b in zip(q, first)) > const_epsilon for q in lq[i]):
            rot[i] = lq[i]
        elif max(abs(first[0]), abs(first[1]), abs(first[2])) > const_epsilon:
            const[i] = first
        if any(math.sqrt(sum(c * c for c in v)) > pos_epsilon for v in lo[i]):
            pos[i] = lo[i]
    return rot, pos, const
