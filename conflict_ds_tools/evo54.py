"""Conflict: Denied Ops EVO 0x54 object streams - parse into a raw model and write back byte for byte.

evo.py reads these streams for viewing / converting (Node, Mesh); this module keeps EVERY field, so a stream can be
edited and written back for the game: write(parse(x)) == x. Fixed-size char arrays keep their raw bytes (the exporter
left stack garbage after the NUL - the m_bas and m_cmb copies of one model differ only there and in the material's
package name), length-prefixed strings keep their NUL.

A model asset in an APKG package (apkg.py) = the common asset header (apkg.header_end) followed by the stream
`EOBJ ... NEXT EOBJ ... DONE`; nothing may follow DONE (the game reads sequentially: CEnvObject 0x766450 ->
0x765C80). Layout from ConflictDeniedOps.exe (package mode: no TEXL / MATL, every VBUF / SKIN names its material
by an asset reference):

  EOBJ     CEnvObject::ReadInEnvObjectLevel 0x764F60: 'EOBJ', u16 version (>= 0x54), u16 flag (0 / 3), u32 type
           (must match the caller's class: 0 static, 2 hier, 5 physics, 7 shatter, 9 ...), lstr model, lstr node,
           u32 name hash, u32 flags (+0x4C), u32 geometry kind (+0x48), u32 triangles, u32 vertices (exporter
           statistics, unread), u32 (x 8 -> +0xF0 bits 3..7), char[128] collision source, char[128] shadow,
           u32 BSPT count (& 7), u32 has-points [+ points block 0x786390], char[0x23], u32 (+0x138),
           [TEXL outside packages], BBOX, BSPH, geometry by kind, BSPT entries, class trailer, NEXT / DONE
  geometry kind 0/5: VBOB + u32 LOD count (& 7) x VBOB; 3: VBOB; 2: SKIN (CSkinObject::Read 0x7845A0); 1: VAOB
           (animated vertex lists: cloth, tank tracks, the bird, the multiplayer flag - _read_vaob); 6: none
  VBUF     CVBufferObject::Read 0x76C480 + ReadStandard 0x76B240: version (10 on PC), external, flags (0x80 = colour
           stream), material asset ref, 2 x u32 material keys, is-strip, vertex / strip / restart / list counts, u32,
           u32 +0x28, FVF / stride hints, [v >= 9: uv sets (1 -> 32-byte, 2 -> 40-byte vertices), u32 +0xB4],
           vertices, [colours], strip + restart + list u16s, [v > 9: triangle octree 0x782810: u32 n, BBOX,
           n x {u16, u8}], VSTP, [v5 chunk], [v > 4 FACE], VBPL, [v >= 3: 2 x u32], tangents {tag, u32 n, n x 24 B}
  SKIN     tag, u32 is-strip, vertex / strip / restart / list counts, material asset ref, 2 x u32 keys, per vertex
           {u32 n, n x 42-byte influence (pos, normal in the bone's space, colour, uv, u16 bone, f32 weight)},
           indices, EDGE {u32 n, n B} / SSTP {u32} chunks up to SHAD {u32 n, n x 29 B}, tangents {tag, u32 n, 24 B}
  BSPT     0x763210: u32 size (0x445113AF ends the list early - the reader steps back), u32 use, [size: tag,
           u32 n, n bytes = the collision tree, bsp.py's layout]
  trailer  CPhysicsEnvObject 0x769970 / CHierObject 0x76EE70: lstr node, u32 hash, lstr parent, then (not on the
           short type 7-9 form) char[128] physics name, 2 x vec3, char[128]; CHierObject continues with BBOX, BSPH,
           2 x u32, bones {char[128], u32}, has-keys, char[128] skeleton, asset ref, [keys: tag, u32 n, n x 52 B];
           CShatterEnvObject (type 7, *_DYN glass / signs): one u32 after the BSPT entries (mapped from the data,
           meaning not traced)

Objects are plain dicts (field names as in the comments of read_object); the stream is a list of them. Pure Python.
"""
import struct

TAG_NEXT, TAG_DONE = b"NEXT", b"DONE"
BSPT_END = 0x445113AF
VERSION = 0x54


class EvoError(Exception):
    pass


class UnmappedError(EvoError):
    """A layout this module doesn't know (geometry kind 1 cloth): the stream can't be walked, not that it's broken."""


class _R:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def raw(self, n):
        if n < 0 or self.p + n > len(self.d):
            raise EvoError("unexpected end of stream at 0x%x (+%d)" % (self.p, n))
        b = bytes(self.d[self.p:self.p + n])
        self.p += n
        return b

    def u32(self):
        return struct.unpack("<I", self.raw(4))[0]

    def f32(self, n):
        return struct.unpack("<%df" % n, self.raw(4 * n))

    def tag(self, want=None):
        t = self.raw(4)
        if want is not None and t != want:
            raise EvoError("expected %r at 0x%x, got %r" % (want, self.p - 4, t))
        return t

    def lstr(self):
        """0x63C640 (read): u32 length incl. the NUL, then that many bytes (kept as they are)."""
        n = self.u32()
        if n > 0x10000:
            raise EvoError("bad string length %d at 0x%x" % (n, self.p - 4))
        return self.raw(n)

    def peek(self, n=4):
        return bytes(self.d[self.p:self.p + n])


class _W:
    def __init__(self):
        self.b = bytearray()

    def raw(self, x):
        self.b += x

    def u32(self, v):
        self.b += struct.pack("<I", v & 0xFFFFFFFF)

    def f32(self, vals):
        self.b += struct.pack("<%df" % len(vals), *vals)

    def lstr(self, s):
        self.u32(len(s))
        self.b += s


def lstr(text):
    """A length-prefixed string field's bytes (incl. the NUL) from text."""
    return text.encode("latin-1") + b"\0"


def text(b):
    """The text of a string field (up to the NUL)."""
    return bytes(b).split(b"\0", 1)[0].decode("latin-1")


def fixed(s, n):
    """A char[n] field from text (NUL-padded)."""
    b = s.encode("latin-1")[:n - 1]
    return b + b"\0" * (n - len(b))


# ---------------------------------------------------------------- chunks

def _read_bbox(r):
    """0x6BE980: 'BBOX', vec3 min, vec3 max, 6 planes (n, d)."""
    r.tag(b"BBOX")
    return {"min": r.f32(3), "max": r.f32(3), "planes": [r.f32(4) for _ in range(6)]}


def _write_bbox(w, b):
    w.raw(b"BBOX")
    w.f32(b["min"]); w.f32(b["max"])
    for p in b["planes"]:
        w.f32(p)


def bbox_from(mn, mx):
    """A BBOX with the game's plane order (+z, -z, +x, -x, +y, -y; inward, n.p + d >= 0)."""
    return {"min": tuple(mn), "max": tuple(mx), "planes": [
        (0.0, 0.0, -1.0, mx[2]), (0.0, 0.0, 1.0, -mn[2]),
        (-1.0, 0.0, 0.0, mx[0]), (1.0, 0.0, 0.0, -mn[0]),
        (0.0, -1.0, 0.0, mx[1]), (0.0, 1.0, 0.0, -mn[1])]}


def _read_points(r):
    """0x786390: tag, u32 n, m, k, n x vec3, [n: 8 x u32 roles], [m: 3 x u32], [k: 3 x (u32, u32)]."""
    p = {"tag": r.tag(), "n": r.u32(), "m": r.u32(), "k": r.u32()}
    p["pts"] = [r.f32(3) for _ in range(p["n"])]
    p["roles"] = r.raw(32) if p["n"] else b""
    p["mdata"] = r.raw(12) if p["m"] else b""
    p["kdata"] = r.raw(24) if p["k"] else b""
    return p


def _write_points(w, p):
    w.raw(p["tag"]); w.u32(p["n"]); w.u32(p["m"]); w.u32(p["k"])
    for v in p["pts"]:
        w.f32(v)
    w.raw(p["roles"]); w.raw(p["mdata"]); w.raw(p["kdata"])


def _read_ref(r):
    """Asset reference 0x6F34D0: GUID (lo, hi), class hash, lstr asset name, lstr package (the containing one)."""
    return {"id": (r.u32(), r.u32()), "type": r.u32(), "name": r.lstr(), "package": r.lstr()}


def _write_ref(w, a):
    w.u32(a["id"][0]); w.u32(a["id"][1]); w.u32(a["type"]); w.lstr(a["name"]); w.lstr(a["package"])


def _blob(r, unit):
    n = r.u32()
    return n, r.raw(n * unit)


_VBUF_U32 = ("mat_u14", "mat_u0c", "is_strip", "nvert", "nstrip", "nextra", "nlist", "u_local4", "u28",
             "fvf_hint", "stride_hint")


def _read_vbuf(r):
    v = {}
    r.tag(b"VBUF")
    v["ver"] = ver = r.u32()
    v["external"] = r.u32() if ver > 1 else None
    v["flags"] = r.u32()                           # +0x20; 0x80 = colour stream
    v["material"] = _read_ref(r)
    for k in _VBUF_U32:                            # +0x14, +0x0C keys; +0x78; counts; unused; +0x28; +0x7C; +0x80
        v[k] = r.u32()
    if ver >= 9:
        v["uvsets"], v["ub4"] = r.u32(), r.u32()
    else:
        v["uvsets"], v["ub4"] = (2 if v["u28"] else 1), None
    stride = {1: 32, 2: 40}.get(v["uvsets"])
    if stride is None:
        raise EvoError("VBUF with %d uv sets at 0x%x" % (v["uvsets"], r.p))
    v["stride"] = stride
    v["verts"] = r.raw(stride * v["nvert"])        # pos, normal, uv[, uv2]
    v["colours"] = r.raw(4 * v["nvert"]) if v["flags"] & 0x80 else b""
    if v["is_strip"] & 1:
        v["strip"], v["extra"] = r.raw(2 * v["nstrip"]), r.raw(2 * v["nextra"])
    else:
        v["strip"] = v["extra"] = b""
    v["list"] = r.raw(2 * v["nlist"])
    v["octree"] = None
    if ver > 9:                                    # CTriListOcTree::Read 0x782810
        n = r.u32()
        v["octree"] = {"n": n, "box": _read_bbox(r), "items": r.raw(3 * n)}
    v["vstp"] = (r.tag(), _blob(r, 1))
    v["vb5"] = (r.tag(), _blob(r, 2)) if ver == 5 else None
    v["face"] = (r.tag(), _blob(r, 12)) if ver > 4 else None
    v["vbpl"] = (r.tag(), _blob(r, 2 if ver < 4 else 4))
    v["passes"] = (r.u32(), r.u32()) if ver >= 3 else None     # +0xAC, +0xB0
    v["tangents"] = (r.tag(), _blob(r, 24))                    # 0x76C390
    return v


def _write_vbuf(w, v):
    ver = v["ver"]
    w.raw(b"VBUF"); w.u32(ver)
    if ver > 1:
        w.u32(v["external"])
    w.u32(v["flags"])
    _write_ref(w, v["material"])
    for k in _VBUF_U32:
        w.u32(v[k])
    if ver >= 9:
        w.u32(v["uvsets"]); w.u32(v["ub4"])
    w.raw(v["verts"]); w.raw(v["colours"])
    w.raw(v["strip"]); w.raw(v["extra"]); w.raw(v["list"])
    if ver > 9:
        o = v["octree"]
        w.u32(o["n"]); _write_bbox(w, o["box"]); w.raw(o["items"])
    for key in ("vstp", "vb5", "face", "vbpl"):
        if v[key] is not None:
            t, (n, b) = v[key]
            w.raw(t); w.u32(n); w.raw(b)
    if v["passes"] is not None:
        w.u32(v["passes"][0]); w.u32(v["passes"][1])
    t, (n, b) = v["tangents"]
    w.raw(t); w.u32(n); w.raw(b)


def _read_vbob(r):
    r.tag(b"VBOB")
    return [_read_vbuf(r) for _ in range(r.u32())]


def _write_vbob(w, bufs):
    w.raw(b"VBOB"); w.u32(len(bufs))
    for v in bufs:
        _write_vbuf(w, v)


def _read_skin(r):
    """CSkinObject::Read 0x7845A0 (package mode). Influences are kept raw per vertex (u32 n + n x 42 B)."""
    s = {"tag": r.tag(), "is_strip": r.u32(), "nvert": r.u32(), "nstrip": r.u32(), "nextra": r.u32(),
         "nlist": r.u32()}
    s["material"] = _read_ref(r)
    s["mat_u14"], s["mat_u0c"] = r.u32(), r.u32()
    verts = []
    for _ in range(s["nvert"]):
        n = r.u32()
        verts.append(r.raw(42 * n))
    s["verts"] = verts                             # per vertex: n x {6 f32, u32 colour, 2 f32 uv, u16 bone, f32 w}
    if s["is_strip"] & 1:
        s["strip"], s["extra"] = r.raw(2 * s["nstrip"]), r.raw(2 * s["nextra"])
    else:
        s["strip"] = s["extra"] = b""
    s["list"] = r.raw(2 * s["nlist"])
    chunks = []                                    # EDGE {u32 n, n B} / SSTP {u32}, up to SHAD {u32 n, n x 29 B}
    while True:
        t = r.tag()
        if t == b"SHAD":
            n = r.u32()
            chunks.append((t, n, r.raw(29 * n)))
            break
        if t == b"EDGE":
            n = r.u32()
            chunks.append((t, n, r.raw(n)))
        elif t == b"SSTP":
            chunks.append((t, r.u32(), None))
        else:
            raise EvoError("unexpected %r in a skin at 0x%x" % (t, r.p - 4))
    s["chunks"] = chunks
    s["tangents"] = (r.tag(), _blob(r, 24))        # 0x784480
    return s


def _write_skin(w, s):
    w.raw(s["tag"])
    for k in ("is_strip", "nvert", "nstrip", "nextra", "nlist"):
        w.u32(s[k])
    _write_ref(w, s["material"])
    w.u32(s["mat_u14"]); w.u32(s["mat_u0c"])
    for v in s["verts"]:
        w.u32(len(v) // 42); w.raw(v)
    w.raw(s["strip"]); w.raw(s["extra"]); w.raw(s["list"])
    for t, n, b in s["chunks"]:
        w.raw(t); w.u32(n)
        if b is not None:
            w.raw(b)
    t, (n, b) = s["tangents"]
    w.raw(t); w.u32(n); w.raw(b)


def _read_vaob(r):
    """Kind 1 animated vertex lists (cloth, tank tracks, the bird, the multiplayer flag; 0x7860C0, mapped from the
    data on DS1's layout): 'VAOB', u32 n, u32 frames, n x VLST {tag, u32 version (3), [v >= 2: u32 external],
    u32 flags, material asset ref, 2 x u32 keys, u32 is-strip, vertex / strip / restart / list counts, u32 frames,
    vertex data = n x 32 B (pos, normal, uv) + (frames - 1) x n x 24 B (pos, normal) - kept as one block -,
    strip + restart + list u16s, [v > 2: VASP {u32 n, n B}]}."""
    a = {"tag": r.tag(b"VAOB")}
    n = r.u32()
    a["frames"] = r.u32()
    lists = []
    for _ in range(n):
        v = {"tag": r.tag(b"VLST"), "ver": r.u32()}
        v["external"] = r.u32() if v["ver"] >= 2 else None
        v["flags"] = r.u32()
        v["material"] = _read_ref(r)
        for k in ("mat_u14", "mat_u0c", "is_strip", "nvert", "nstrip", "nextra", "nlist", "frames"):
            v[k] = r.u32()
        if v["frames"] < 1:
            raise UnmappedError("VLST with %d frames at 0x%x" % (v["frames"], r.p))
        v["verts"] = r.raw(v["nvert"] * (32 + 24 * (v["frames"] - 1)))
        if v["is_strip"] & 1:
            v["strip"], v["extra"] = r.raw(2 * v["nstrip"]), r.raw(2 * v["nextra"])
        else:
            v["strip"] = v["extra"] = b""
        v["list"] = r.raw(2 * v["nlist"])
        v["vasp"] = (r.tag(), _blob(r, 1)) if v["ver"] > 2 else None
        lists.append(v)
    a["lists"] = lists
    return a


def _write_vaob(w, a):
    w.raw(a["tag"]); w.u32(len(a["lists"])); w.u32(a["frames"])
    for v in a["lists"]:
        w.raw(v["tag"]); w.u32(v["ver"])
        if v["ver"] >= 2:
            w.u32(v["external"])
        w.u32(v["flags"])
        _write_ref(w, v["material"])
        for k in ("mat_u14", "mat_u0c", "is_strip", "nvert", "nstrip", "nextra", "nlist", "frames"):
            w.u32(v[k])
        w.raw(v["verts"]); w.raw(v["strip"]); w.raw(v["extra"]); w.raw(v["list"])
        if v["vasp"] is not None:
            t, (n, b) = v["vasp"]
            w.raw(t); w.u32(n); w.raw(b)


# ---------------------------------------------------------------- object

_HEAD_U32 = ("hash", "flags", "kind", "ntris", "nverts", "u_f0")


def read_object(r):
    """One object, up to and including its NEXT / DONE tag."""
    o = {"off": r.p}
    r.tag(b"EOBJ")
    o["version"], o["vflag"] = struct.unpack("<HH", r.raw(4))
    if o["version"] < VERSION or o["vflag"] not in (0, 3):
        raise EvoError("EOBJ version %#x/%d at 0x%x" % (o["version"], o["vflag"], o["off"]))
    o["type"] = r.u32()
    o["model"] = r.lstr()
    o["node"] = r.lstr()
    for k in _HEAD_U32:                  # name hash, flags (+0x4C), kind (+0x48), triangles, vertices, +0xF0 bits
        o[k] = r.u32()
    o["collision_src"] = r.raw(128)      # "#BSP_<name>" / "THIS" / "NOBSP" (unused by the game)
    o["shadow"] = r.raw(128)             # "NOSHADOW" / "(NULL)" / name -> +0x124
    o["nbspt"] = r.u32()
    o["has_points"] = r.u32()
    o["points"] = _read_points(r) if o["has_points"] else None
    o["name35"] = r.raw(0x23)            # "NONE" or a name -> +0x134
    o["u138"] = r.u32()
    o["texl"] = None
    if r.peek() == b"TEXL":              # only outside package mode (0x762BD0)
        r.tag()
        o["texl"] = [(r.u32(), r.raw(64)) for _ in range(r.u32())]
    if r.peek() == b"MATL":
        raise EvoError("MATL section (not a package stream) at 0x%x" % r.p)
    o["bbox"] = _read_bbox(r)
    r.tag(b"BSPH")
    o["bsph"] = r.f32(4)
    k = o["kind"]
    o["vbob"], o["nlod"], o["lods"], o["skin"], o["vaob"] = None, None, [], None, None
    if k in (0, 5):
        o["vbob"] = _read_vbob(r)
        o["nlod"] = r.u32()
        o["lods"] = [_read_vbob(r) for _ in range(o["nlod"] & 7)]
    elif k == 3:
        o["vbob"] = _read_vbob(r)
    elif k == 2:
        o["skin"] = _read_skin(r)
    elif k == 1:
        o["vaob"] = _read_vaob(r)
    elif k != 6:
        raise UnmappedError("geometry kind %d isn't mapped at 0x%x" % (k, r.p))
    o["bspt"] = []
    if k != 6:
        for _ in range(o["nbspt"] & 7):
            size = r.u32()
            if size == BSPT_END:
                r.p -= 4
                break
            u = r.u32()
            if size:
                t = r.tag()
                o["bspt"].append({"size": size, "u": u, "tag": t, "tree": r.raw(r.u32())})
            else:
                o["bspt"].append({"size": 0, "u": u, "tag": None, "tree": None})
    o["shatter"] = None
    if o["type"] == 7 and r.peek() not in (TAG_NEXT, TAG_DONE):
        o["shatter"] = r.u32()           # CShatterEnvObject (*_DYN glass / signs): one u32 after the BSPT entries
    o["phys"] = o["hier"] = None
    if r.peek() not in (TAG_NEXT, TAG_DONE):
        ph = {"node": r.lstr(), "hash": r.u32(), "parent": r.lstr()}
        o["phys"] = ph
        ph["short"] = r.peek() in (TAG_NEXT, TAG_DONE)     # type 7-9 objects: node / hash / parent only
        if not ph["short"]:
            ph.update({"physname": r.raw(128), "v0": r.f32(3), "v1": r.f32(3), "name2": r.raw(128)})
            if r.peek() == b"BBOX":                        # CHierObject::Read continues
                h = {"bbox": _read_bbox(r)}
                r.tag(b"BSPH")
                h["bsph"] = r.f32(4)
                h["u1f4"], h["key_index"] = r.u32(), r.u32()
                h["bones"] = [(r.raw(128), r.u32()) for _ in range(r.u32())]
                h["has_keys"] = r.u32()
                h["skeleton"] = r.raw(128)
                h["skel_ref"] = _read_ref(r)
                h["keys"] = None
                if h["has_keys"]:                          # 0x76ED40: tag, u32 n, n x 52 B
                    t = r.tag()
                    n = r.u32()
                    h["keys"] = (t, n, r.raw(52 * n))
                o["hier"] = h
    o["end_tag"] = r.tag()
    if o["end_tag"] not in (TAG_NEXT, TAG_DONE):
        raise EvoError("expected NEXT/DONE at 0x%x, got %r" % (r.p - 4, o["end_tag"]))
    o["end"] = r.p
    return o


def write_object(w, o):
    w.raw(b"EOBJ"); w.raw(struct.pack("<HH", o["version"], o["vflag"]))
    w.u32(o["type"]); w.lstr(o["model"]); w.lstr(o["node"])
    for k in _HEAD_U32:
        w.u32(o[k])
    w.raw(o["collision_src"]); w.raw(o["shadow"])
    w.u32(o["nbspt"]); w.u32(o["has_points"])
    if o["points"] is not None:
        _write_points(w, o["points"])
    w.raw(o["name35"]); w.u32(o["u138"])
    if o["texl"] is not None:
        w.raw(b"TEXL"); w.u32(len(o["texl"]))
        for h, name in o["texl"]:
            w.u32(h); w.raw(name)
    _write_bbox(w, o["bbox"])
    w.raw(b"BSPH"); w.f32(o["bsph"])
    if o["vbob"] is not None:
        _write_vbob(w, o["vbob"])
    if o["nlod"] is not None:
        w.u32(o["nlod"])
        for lod in o["lods"]:
            _write_vbob(w, lod)
    if o["skin"] is not None:
        _write_skin(w, o["skin"])
    if o.get("vaob") is not None:
        _write_vaob(w, o["vaob"])
    for b in o["bspt"]:
        w.u32(b["size"]); w.u32(b["u"])
        if b["size"]:
            w.raw(b["tag"]); w.u32(len(b["tree"])); w.raw(b["tree"])
    if o.get("shatter") is not None:
        w.u32(o["shatter"])
    p = o["phys"]
    if p is not None:
        w.lstr(p["node"]); w.u32(p["hash"]); w.lstr(p["parent"])
        if not p["short"]:
            w.raw(p["physname"]); w.f32(p["v0"]); w.f32(p["v1"]); w.raw(p["name2"])
    h = o["hier"]
    if h is not None:
        _write_bbox(w, h["bbox"]); w.raw(b"BSPH"); w.f32(h["bsph"])
        w.u32(h["u1f4"]); w.u32(h["key_index"]); w.u32(len(h["bones"]))
        for name, idx in h["bones"]:
            w.raw(name); w.u32(idx)
        w.u32(h["has_keys"]); w.raw(h["skeleton"]); _write_ref(w, h["skel_ref"])
        if h["keys"] is not None:
            t, n, b = h["keys"]
            w.raw(t); w.u32(n); w.raw(b)
    w.raw(o["end_tag"])


def parse(data):
    """The objects of a stream (`EOBJ ... DONE`, nothing after it)."""
    r = _R(data)
    objs = []
    while True:
        o = read_object(r)
        objs.append(o)
        if o["end_tag"] == TAG_DONE:
            break
    if r.p != len(data):
        raise EvoError("%d bytes after DONE" % (len(data) - r.p))
    return objs


def write(objs):
    """The stream bytes; the last object's end tag is forced to DONE and the others to NEXT."""
    w = _W()
    for i, o in enumerate(objs):
        o = dict(o, end_tag=TAG_DONE if i == len(objs) - 1 else TAG_NEXT)
        write_object(w, o)
    return bytes(w.b)


# ---------------------------------------------------------------- helpers

def vertices(v):
    """[(pos, normal, uv, uv2 | None)] of a VBUF."""
    s = v["stride"]
    out = []
    for i in range(v["nvert"]):
        f = struct.unpack_from("<%df" % (s // 4), v["verts"], i * s)
        out.append((f[0:3], f[3:6], f[6:8], f[8:10] if s == 40 else None))
    return out


def triangles(v):
    """The list triangles of a VBUF / SKIN (u16 index triples)."""
    idx = struct.unpack("<%dH" % v["nlist"], v["list"])
    return [idx[i:i + 3] for i in range(0, len(idx) - 2, 3)]


def material_refs(objs):
    """Every material asset reference of a stream (VBUFs incl. LODs, skins) - dicts, so they can be edited in place
    (e.g. the package name when a model moves to another package)."""
    for o in objs:
        for bob in ([o["vbob"]] if o["vbob"] else []) + list(o["lods"]):
            for v in bob:
                yield v["material"]
        if o["skin"] is not None:
            yield o["skin"]["material"]
        if o.get("vaob") is not None:
            for v in o["vaob"]["lists"]:
                yield v["material"]


# AHEA budget types a model stream uses (apkg.Package.alloc_counts): object type -> its class's instance pool
OBJECT_POOLS = {0: "CEnvObject", 1: "CEnvObject", 3: "CEnvObject", 2: "CHierObject", 5: "CPhysicsEnvObject",
                7: "CShatterEnvObject", 9: "CModelSwapEnvObject"}


def alloc_counts(objs):
    """What a stream takes from its package's AHEA pools (matched against every shipped package by
    scripts/check_do_packages.py): one instance per object of its class (OBJECT_POOLS), CVBufferObject per VBOB (LODs
    included), CVertexBuffer per VBUF, CSkinObject / CSkin per skin, SKINVERTEX per influence, CVAnimObject per VAOB,
    CVertexAnim per VLST, _MATRIX per key + bone, BSPDATA = collision tree bytes."""
    c = {}

    def add(k, n=1):
        c[k] = c.get(k, 0) + n

    for o in objs:
        pool = OBJECT_POOLS.get(o["type"])
        if pool:
            add(pool)
        for bob in ([o["vbob"]] if o["vbob"] is not None else []) + list(o["lods"]):
            add("CVBufferObject")
            add("CVertexBuffer", len(bob))
        if o["skin"] is not None:
            add("CSkinObject"); add("CSkin")
            add("SKINVERTEX", sum(len(v) // 42 for v in o["skin"]["verts"]))
        if o.get("vaob") is not None:
            add("CVAnimObject")
            add("CVertexAnim", len(o["vaob"]["lists"]))
        if o["hier"] is not None:
            add("_MATRIX", len(o["hier"]["bones"]) + (o["hier"]["keys"][1] if o["hier"]["keys"] else 0))
        for b in o["bspt"]:
            if b["tree"]:
                add("BSPDATA", len(b["tree"]))
    return c
