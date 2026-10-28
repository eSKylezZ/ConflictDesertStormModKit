"""Conflict: Denied Ops asset packages (APKG v4) - read, and unpack into plain files for the editor and converters.

Denied Ops keeps its models, textures and materials inside APKG packages stored in the level archives
(``m_cmb.pc.dat`` ...; the extractor writes them as ``_<HASH>.BIN``). Layout (ConflictDeniedOps.exe, reader
``CAssetPackage::BeginReading`` 0x6F66B0 / ``ReadAssets`` 0x6F5070; full notes in the research folder's
APKG_FORMAT.md):

    'APKG' u32 version (4), u32 kind, u32 ts_hi, u32 ts_lo, [version > 1] u32 n + n x {char[64], u32, u32},
    u32, u32, u32 asset_count, 'MANI' manifest, folder list, str, then asset_count records
    {u32 flags, str name, u32 class_hash, u32 guid_lo, u32 guid_hi, u32 tag, u32 version, u32 size, payload}

A payload starts with the common asset header (flags, folder hash, name, GUID, 'AUID' chunk). After it:
env objects (CEnvObject, CPhysicsEnvObject, CHierObject ...) = an EVO 0x54 object stream ``EOBJ ... DONE``
(evo.py reads it); CTexturePage = a whole .dds; CMaterial = colours + 6 texture references (slot 0 diffuse).

    unpack(package_bytes, folder)  ->  <MODEL>.EVO, <TEXTURE>.DDS and _MATERIALS.TSV (material -> diffuse texture)

``str`` = u32 length including the NUL + bytes.
"""
from __future__ import annotations

import os
import struct

U32 = struct.Struct("<I")

CLASS_TEXTURE = 0x045CED2B        # CTexturePage
CLASS_MATERIAL = 0x9B7940E0       # CMaterial
MATERIALS_FILE = "_MATERIALS.TSV"


def name_hash(name: str) -> int:
    """The archive / class / package name hash (0x6337A0): LFSR over the lower-cased name."""
    h = 1
    for ch in name.lower().encode("latin-1"):
        for bit in range(8):
            inp = ((ch >> bit) & 1) ^ (h & 1) ^ ((h >> 1) & 1) ^ ((h >> 21) & 1) ^ ((h >> 31) & 1)
            h = ((h << 1) | inp) & 0xFFFFFFFF
    return h


def _cstr(b) -> str:
    return bytes(b).split(b"\0", 1)[0].decode("latin-1")


class _R:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u32(self):
        v = U32.unpack_from(self.d, self.p)[0]
        self.p += 4
        return v

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def raw(self, n):
        if self.p + n > len(self.d):
            raise ValueError("read past the end at 0x%x (+%d)" % (self.p, n))
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def string(self):
        n = self.u32()
        if n > 0x100000:
            raise ValueError("bad string length %d at 0x%x" % (n, self.p - 4))
        return self.raw(n)

    def text(self):
        return _cstr(self.string())


class Record:
    __slots__ = ("flags", "name", "class_hash", "guid", "tag", "version", "payload")

    def __repr__(self):
        return "<Record %s class=%08X %s v%d %d bytes>" % (self.name, self.class_hash,
                                                            bytes(self.tag).decode("latin-1"), self.version,
                                                            len(self.payload))


def _skip_asset(r, mani_ver):
    r.u32(); r.u32()
    if mani_ver >= 2:
        r.u32()
    r.u32(); r.u32(); r.string()
    for _ in range(r.u32()):
        _skip_asset(r, mani_ver)


def read(data) -> list[Record]:
    """The records of a package (payloads are zero-copy slices of `data`)."""
    data = memoryview(data)
    r = _R(data)
    if bytes(r.raw(4)) != b"APKG":
        raise ValueError("not an APKG package")
    version = r.u32()
    r.u32(); r.u32(); r.u32()                     # kind, timestamp
    if version > 1:
        for _ in range(r.u32()):
            r.raw(0x40); r.u32(); r.u32()         # dependency packages
    r.u32(); r.u32()
    count = r.u32()
    if bytes(r.raw(4)) != b"MANI":
        raise ValueError("MANI expected at 0x%x" % (r.p - 4))
    mani_ver = r.u32()
    for _ in range(r.u32()):                      # package entries with their AHEA allocation budgets
        r.u32(); r.u32(); r.string()
        r.raw(4); r.u32()
        for _ in range(r.u32()):
            r.string(); r.u32()
    for _ in range(r.u32()):
        _skip_asset(r, mani_ver)
    for _ in range(r.u32()):                      # external references
        r.u32(); r.u32(); r.string()
        n = r.u32()
        for _ in range(n if n < 0x80000000 else 0):
            _skip_asset(r, mani_ver)
    for _ in range(r.u32()):                      # asset folders
        r.u32(); r.u32(); r.string()
    r.string()
    out = []
    for _ in range(count):
        rec = Record()
        rec.flags = r.u32()
        rec.name = r.text()
        rec.class_hash = r.u32()
        rec.guid = (r.u32(), r.u32())
        rec.tag = r.raw(4)
        rec.version = r.u32()
        rec.payload = r.raw(r.u32())
        out.append(rec)
    return out


def header_end(payload) -> int:
    """Offset after the common asset header (CAsset 0x6F3950 + AUID 0x6F9C80)."""
    r = _R(payload)
    r.u32(); r.u32(); r.string(); r.u32(); r.u32()
    if bytes(r.raw(4)) != b"AUID":
        raise ValueError("AUID expected")
    ver = r.u32()
    for _ in range(4):
        r.string()
    r.raw(16)
    r.u32()
    if ver > 1:
        r.u32()
    return r.p


def _ref(r):
    r.u32(); r.u32(); r.u32()
    name = r.text()
    r.string()
    return name


def material_textures(payload) -> list[str]:
    """CMaterial (0x662B80): the 6 texture slots' names (0 diffuse, 3 normal, 4 specular; "" = empty)."""
    r = _R(payload, header_end(payload))
    r.raw(4); r.u32(); r.u32()                    # ASST, version, +0x30
    if r.u32() == 0x10001:
        r.string(); r.u32(); r.raw(16); r.raw(4); r.u32()
    return [_ref(r) for _ in range(6)]


def texture_page(payload) -> tuple[str, bytes]:
    """CTexturePage (0x660E60): (name, the .dds file)."""
    r = _R(payload, header_end(payload))
    name = r.text()
    r.u32(); r.u32(); r.raw(4); r.u32(); r.u8(); r.u8(); r.u32(); r.u32(); r.u32(); r.u32(); r.u32()
    for _ in range(3):
        _ref(r)
    return name, bytes(r.raw(r.u32()))


PACKAGE_EXT = ".APAK"          # how an archive names a package: <NAME>.apak (all 9 of Comms Base / catalog.pc match)


def package_name(data) -> str | None:
    """A package's own name, from its manifest ("MANI" u32 2, u32, u32, u32 name_hash(name), str name) - the archive
    stores it as <name>.apak (name_hash(name + ".apak") = the archive index hash). None if not a package."""
    if data[:4] != b"APKG":
        return None
    m = data.find(b"MANI", 0, 8192)
    if m < 0:
        return None
    try:
        h, n = struct.unpack_from("<2I", data, m + 16)
        name = bytes(data[m + 24:m + 24 + n]).split(b"\0", 1)[0].decode("latin-1")
    except (struct.error, UnicodeDecodeError):
        return None
    return name if name and name_hash(name) == h else None


def is_package(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"APKG"
    except OSError:
        return False


def unpack(data, folder, materials=None, log=None) -> dict:
    """Write a package's models (<NAME>.EVO = the EOBJ stream), textures (<NAME>.DDS) into `folder`; a file
    already there is kept (the first package wins). Materials go into `materials` ({name: [slot textures]}).
    Returns counts."""
    counts = {"models": 0, "textures": 0, "materials": 0, "skipped": 0}
    existing = {f.upper() for f in os.listdir(folder)}
    for rec in read(data):
        try:
            if rec.class_hash == CLASS_TEXTURE:
                name, dds = texture_page(rec.payload)
                fn = (name or rec.name) + ".DDS"
                if fn.upper() not in existing and dds[:4] == b"DDS ":
                    with open(os.path.join(folder, fn), "wb") as f:
                        f.write(dds)
                    existing.add(fn.upper())
                    counts["textures"] += 1
            elif rec.class_hash == CLASS_MATERIAL:
                if materials is not None:
                    materials.setdefault(rec.name.upper(), material_textures(rec.payload))
                counts["materials"] += 1
            else:
                start = header_end(rec.payload)
                if bytes(rec.payload[start:start + 4]) != b"EOBJ":
                    counts["skipped"] += 1
                    continue
                fn = rec.name + ".EVO"
                if fn.upper() not in existing:
                    with open(os.path.join(folder, fn), "wb") as f:
                        f.write(rec.payload[start:])
                    existing.add(fn.upper())
                    counts["models"] += 1
        except (ValueError, struct.error, IndexError) as e:
            counts["skipped"] += 1
            if log:
                log(f"  {rec.name}: {e}")
    return counts


def write_materials(folder, materials):
    """_MATERIALS.TSV: material name, then its texture slots (tab-separated; empty = none)."""
    with open(os.path.join(folder, MATERIALS_FILE), "w", encoding="latin-1", newline="\n") as f:
        for name in sorted(materials):
            f.write("\t".join([name] + materials[name]) + "\n")


def read_materials(folder) -> dict:
    out = {}
    path = os.path.join(folder, MATERIALS_FILE)
    if os.path.exists(path):
        for line in open(path, encoding="latin-1"):
            cols = line.rstrip("\n").split("\t")
            if cols and cols[0]:
                out[cols[0].upper()] = cols[1:]
    return out


UNPACKED = ".apkg-unpacked"


def unpack_extracted(root, progress=None, log=None) -> dict:
    """Unpack every APKG in an extracted Denied Ops folder (one sub-folder per archive), each into its own folder.
    Marked done per folder (UNPACKED), so a second call does nothing."""
    total = {"models": 0, "textures": 0, "materials": 0, "skipped": 0}
    folders = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    for i, d in enumerate(folders):
        folder = os.path.join(root, d)
        if os.path.exists(os.path.join(folder, UNPACKED)):
            continue
        if progress:
            progress(i / max(1, len(folders)), f"Unpacking the asset packages of {d}")
        materials = read_materials(folder)
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if f.upper().endswith((".BIN", PACKAGE_EXT)) and is_package(p):   # .BIN: extracted before 1.9.3
                with open(p, "rb") as fh:
                    data = fh.read()
                c = unpack(data, folder, materials, log)
                for k in total:
                    total[k] += c[k]
                if log:
                    log(f"{d}/{f}: {c}")
        write_materials(folder, materials)
        open(os.path.join(folder, UNPACKED), "w").close()
    return total


# ---------------------------------------------------------------------------------------------------------------------
# Writing: the whole package as a model (Package.read / write byte for byte) and replacing one asset's payload.
#
# Header (0x6F66B0): 'APKG' u32 version, u32 kind (1 library, 2 level package), FILETIME (hi, lo),
#   [version > 1] u32 n + n x {char[64] name (raw: garbage after the NUL kept), u32 ts_hi, u32 ts_lo} dependencies,
#   u32 h0c (5), u32 h18 (0), u32 asset_count (= the number of records)
# MANI v2 (0x6F8F10): u32 n x {u32 a, u32 package hash, str name, 'AHEA' u32 ver, u32 m x {str type, u32 budget}},
#   u32 n x ASSET, u32 n x {u32 a, u32 b, str name, i32 m, m x ASSET} (external references)
#   ASSET (0x6F8180) = {u32 a, u32 package hash, [MANI v >= 2] u32 class hash, u32 guid lo, hi, str name,
#   u32 n children, children}
# u32 n x {u32 a, u32 folder hash, str name} (asset folders, 0x6F4030), str (package string)
# records (0x6F5070), asset_count x {u32 flags, str name, u32 class hash, u32 guid lo, hi, u32 tag, u32 version,
#   u32 size, payload}, then the tail kept raw (u32 native-buffer count = 0 on PC, 'HIST' chunk).
# Nothing checksums the package and nothing totals or locates the records: a record is delimited by its own size only,
# so replacing a payload rewrites that one u32. The game does NOT bound a record by its size - it reads on wherever
# the class reader stops - so a payload must be exactly what the class reader consumes (replace_asset checks model
# streams with evo54.parse, which refuses bytes after DONE). AHEA budgets (CBufferAllocInfo, 0x636650 -> pooled
# allocators 0x636040) size per-type pools for the whole package; in every shipped package they equal what its records
# add up to (scripts/check_do_packages.py; evo54.alloc_counts per model), so replace_asset moves them with the model.
# Unknown: whether the game minds a budget that is too large or too small (not tested in game) - kept exact.


class _Asset:
    """A manifest entry (ASSET::Serialize 0x6F8180)."""
    __slots__ = ("a", "package", "class_hash", "guid", "name", "children")

    @classmethod
    def read(cls, r, mani_ver):
        a = cls()
        a.a = r.u32()
        a.package = r.u32()
        a.class_hash = r.u32() if mani_ver >= 2 else None
        a.guid = (r.u32(), r.u32())
        a.name = bytes(r.string())
        a.children = [cls.read(r, mani_ver) for _ in range(r.u32())]
        return a

    def write(self, w):
        w.u32(self.a); w.u32(self.package)
        if self.class_hash is not None:
            w.u32(self.class_hash)
        w.u32(self.guid[0]); w.u32(self.guid[1])
        w.string(self.name)
        w.u32(len(self.children))
        for c in self.children:
            c.write(w)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class _W:
    def __init__(self):
        self.parts = []

    def u32(self, v):
        self.parts.append(U32.pack(v & 0xFFFFFFFF))

    def raw(self, b):
        self.parts.append(bytes(b))

    def string(self, s):
        self.u32(len(s))
        self.raw(s)

    def bytes(self):
        return b"".join(self.parts)


class Entry:
    """One record of a Package: raw name bytes (incl. the NUL) kept; `payload` may be any bytes-like object."""
    __slots__ = ("flags", "raw_name", "class_hash", "guid", "tag", "version", "payload")

    @property
    def name(self) -> str:
        return _cstr(self.raw_name)

    def header_bytes(self) -> bytes:
        w = _W()
        w.u32(self.flags); w.string(self.raw_name); w.u32(self.class_hash)
        w.u32(self.guid[0]); w.u32(self.guid[1])
        w.raw(self.tag); w.u32(self.version); w.u32(len(self.payload))
        return w.bytes()

    def stream_offset(self) -> int | None:
        """Where the EVO 0x54 object stream starts in the payload (after the asset header), or None if the payload
        isn't a model."""
        try:
            start = header_end(self.payload)
        except (ValueError, struct.error, IndexError):
            return None
        return start if bytes(self.payload[start:start + 4]) == b"EOBJ" else None

    def __repr__(self):
        return "<Entry %s class=%08X guid=%08X:%08X %s v%d %d bytes>" % (
            self.name, self.class_hash, self.guid[0], self.guid[1], bytes(self.tag).decode("latin-1"),
            self.version, len(self.payload))


class Package:
    """An APKG package as a model: every field kept, write() gives the same bytes back. Payloads are zero-copy slices
    of the bytes read (keep that buffer alive while the package is used)."""

    @classmethod
    def read(cls, data) -> "Package":
        data = memoryview(data)
        r = _R(data)
        p = cls()
        if bytes(r.raw(4)) != b"APKG":
            raise ValueError("not an APKG package")
        p.version = r.u32()
        p.kind = r.u32()
        p.timestamp = (r.u32(), r.u32())
        p.deps = []
        if p.version > 1:
            for _ in range(r.u32()):
                p.deps.append((bytes(r.raw(0x40)), r.u32(), r.u32()))
        p.h0c, p.h18 = r.u32(), r.u32()
        count = r.u32()
        if bytes(r.raw(4)) != b"MANI":
            raise ValueError("MANI expected at 0x%x" % (r.p - 4))
        p.mani_ver = r.u32()
        p.packages = []                            # [a, hash, name, AHEA version, [[type, budget], ...]]
        for _ in range(r.u32()):
            a, h, name = r.u32(), r.u32(), bytes(r.string())
            if bytes(r.raw(4)) != b"AHEA":
                raise ValueError("AHEA expected at 0x%x" % (r.p - 4))
            ver = r.u32()
            p.packages.append([a, h, name, ver, [[bytes(r.string()), r.u32()] for _ in range(r.u32())]])
        p.assets = [_Asset.read(r, p.mani_ver) for _ in range(r.u32())]
        p.externals = []                           # [a, b, name, n (raw i32), [ASSET]]
        for _ in range(r.u32()):
            a, b, name, n = r.u32(), r.u32(), bytes(r.string()), r.u32()
            p.externals.append([a, b, name, n, [_Asset.read(r, p.mani_ver) for _ in range(n if n < 0x80000000 else 0)]])
        p.folders = [(r.u32(), r.u32(), bytes(r.string())) for _ in range(r.u32())]
        p.pkg_string = bytes(r.string())
        p.entries = []
        for _ in range(count):
            e = Entry()
            e.flags = r.u32()
            e.raw_name = bytes(r.string())
            e.class_hash = r.u32()
            e.guid = (r.u32(), r.u32())
            e.tag = bytes(r.raw(4))
            e.version = r.u32()
            e.payload = r.raw(r.u32())
            p.entries.append(e)
        p.tail = bytes(data[r.p:])
        return p

    @property
    def name(self) -> str:
        """The package's own name (its first manifest entry) - the archive file is <name>.apak."""
        return _cstr(self.packages[0][2]) if self.packages else ""

    def header_bytes(self) -> bytes:
        """Everything before the first record."""
        w = _W()
        w.raw(b"APKG"); w.u32(self.version); w.u32(self.kind)
        w.u32(self.timestamp[0]); w.u32(self.timestamp[1])
        if self.version > 1:
            w.u32(len(self.deps))
            for name, hi, lo in self.deps:
                w.raw(name); w.u32(hi); w.u32(lo)
        w.u32(self.h0c); w.u32(self.h18); w.u32(len(self.entries))
        w.raw(b"MANI"); w.u32(self.mani_ver)
        w.u32(len(self.packages))
        for a, h, name, ver, budgets in self.packages:
            w.u32(a); w.u32(h); w.string(name)
            w.raw(b"AHEA"); w.u32(ver); w.u32(len(budgets))
            for t, v in budgets:
                w.string(t); w.u32(v)
        w.u32(len(self.assets))
        for a in self.assets:
            a.write(w)
        w.u32(len(self.externals))
        for a, b, name, n, kids in self.externals:
            w.u32(a); w.u32(b); w.string(name); w.u32(n)
            for k in kids:
                k.write(w)
        w.u32(len(self.folders))
        for a, h, name in self.folders:
            w.u32(a); w.u32(h); w.string(name)
        w.string(self.pkg_string)
        return w.bytes()

    def iter_chunks(self):
        """The package's bytes in pieces (header, then per record its header and payload, then the tail) - for
        writing a big package to a file without joining it in memory."""
        yield self.header_bytes()
        for e in self.entries:
            yield e.header_bytes()
            yield e.payload
        yield self.tail

    def size(self) -> int:
        return sum(len(c) for c in self.iter_chunks())

    def write(self) -> bytes:
        return b"".join(bytes(c) for c in self.iter_chunks())

    def write_to(self, f) -> int:
        """Write into an open binary file; returns the byte count."""
        n = 0
        for c in self.iter_chunks():
            f.write(c)
            n += len(c)
        return n

    # ------------------------------------------------------------------ lookup / editing
    def find(self, key) -> list:
        """Entries by name (str, case-insensitive) or GUID ((lo, hi) or a 64-bit int lo | hi << 32)."""
        if isinstance(key, str):
            k = key.upper()
            return [e for e in self.entries if e.name.upper() == k]
        g = tuple(key) if isinstance(key, tuple) else (key & 0xFFFFFFFF, key >> 32)
        return [e for e in self.entries if e.guid == g]

    def entry(self, key) -> Entry:
        hits = self.find(key)
        if len(hits) != 1:
            raise KeyError("%r matches %d assets in package %s" % (key, len(hits), self.name))
        return hits[0]

    def replace_asset(self, key, new_payload, check=True, budgets=True) -> dict:
        """Give one asset (by name or GUID) a new payload - the whole payload: asset header + object. The record's
        size follows the payload; nothing else in the package locates or totals it.
        check: a model payload (header + EOBJ stream) must be a stream evo54 walks to its DONE with nothing after it
        - the game reads exactly that much and then the next record.
        budgets: the AHEA pools follow the model (evo54.alloc_counts of the old and the new stream: objects per
        class, buffers, skin vertices, matrices, collision tree bytes) - every shipped package's budgets equal what
        its records add up to, except BSPDATA, which libraries (kind 1) leave at 0 (not pooled there: kept 0).
        Returns {type: (old budget, new budget)} for the budgets changed."""
        e = self.entry(key)
        new_payload = bytes(new_payload)
        new_objs = _payload_stream(e, new_payload, check)
        changed = {}
        if budgets and new_objs is not None:
            from . import evo54
            old_objs = _payload_stream(e, e.payload, False)
            if old_objs is not None:
                old_c, new_c = evo54.alloc_counts(old_objs), evo54.alloc_counts(new_objs)
                have = self.alloc_counts()
                for t in sorted(set(old_c) | set(new_c)):
                    delta = new_c.get(t, 0) - old_c.get(t, 0)
                    if not delta or t not in have:
                        continue
                    if t == "BSPDATA" and self.kind == 1 and have[t] == 0:
                        continue
                    value = max(0, have[t] + delta)
                    self.set_alloc(t, value)
                    changed[t] = (have[t], value)
        e.payload = new_payload
        return changed

    def all_assets(self):
        """Every manifest ASSET (own and external), depth first."""
        for a in self.assets:
            yield from a.walk()
        for g in self.externals:
            for k in g[4]:
                yield from k.walk()

    # ------------------------------------------------------------------ AHEA budgets
    def alloc_counts(self, package=0) -> dict:
        """AHEA {type name: budget} of a manifest entry: per-type pool sizes for the whole package (instances such as
        CTexturePage / CMaterial, or elements such as SKINVERTEX / BSPDATA / _MATRIX)."""
        return {_cstr(t): v for t, v in self.packages[package][4]}

    def set_alloc(self, type_name, value, package=0) -> int:
        """Set one AHEA budget; returns the old value."""
        for row in self.packages[package][4]:
            if _cstr(row[0]) == type_name:
                old, row[1] = row[1], value
                return old
        raise KeyError(type_name)


def _payload_stream(entry, payload, check):
    """The parsed EVO 0x54 stream of a model payload (None: not a model, or check off and it doesn't parse).
    check: refuse a payload that changes what the asset is, or a model stream evo54 can't walk to its end."""
    from . import evo54
    try:
        start = header_end(payload)
    except (ValueError, struct.error, IndexError) as e:
        if check:
            raise ValueError("the new payload for %s has no valid asset header (%s)" % (entry.name, e)) from None
        return None
    is_model = bytes(payload[start:start + 4]) == b"EOBJ"
    if check and (entry.stream_offset() is not None) != is_model:
        raise ValueError("the new payload for %s is %s, the asset it replaces %s"
                         % (entry.name, "a model" if is_model else "not a model",
                            "is" if entry.stream_offset() is not None else "isn't"))
    if not is_model:
        return None
    try:
        return evo54.parse(bytes(payload[start:]))
    except evo54.UnmappedError:
        return None                                 # a layout evo54 doesn't map: taken as it is, budgets unchanged
    except evo54.EvoError as e:
        if check:
            raise ValueError("the new payload for %s isn't a valid model stream: %s" % (entry.name, e)) from None
        return None


def replace_asset(package_bytes, asset_name, new_payload, check=True, budgets=True) -> bytes:
    """The package with one asset's payload replaced (by name or GUID; see Package.replace_asset)."""
    p = Package.read(package_bytes)
    p.replace_asset(asset_name, new_payload, check, budgets)
    return p.write()


def model_payload(entry, objs) -> bytes:
    """An asset's payload with its model stream replaced by `objs` (evo54 objects); the asset header is kept."""
    from . import evo54
    start = entry.stream_offset()
    if start is None:
        raise ValueError("%s isn't a model asset" % entry.name)
    return bytes(entry.payload[:start]) + evo54.write(objs)


# ---------------------------------------------------------------------------------------------------------------------
# Denied Ops *.pc.dat archives: 0x0000 index of 4096 x {u32 name hash, u32 offset, u32 size} (used slots first, in
# offset order), data from 0xC000, each entry at a 2048-byte boundary. Read-only helpers for finding the packages.

DAT_INDEX = 0xC000


def dat_entries(path) -> list:
    """[(hash, offset, size)] of a .pc.dat archive's used index slots."""
    with open(path, "rb") as f:
        idx = f.read(DAT_INDEX)
    out = []
    for p in range(0, len(idx) - 11, 12):
        h, o, s = struct.unpack_from("<3I", idx, p)
        if h or o or s:
            out.append((h, o, s))
    return out


def dat_packages(path):
    """Yields (hash, bytes) for every APKG package in a .pc.dat archive, one at a time (they can be 270 MB)."""
    with open(path, "rb") as f:
        for h, o, s in dat_entries(path):
            f.seek(o)
            if f.read(4) != b"APKG":
                continue
            f.seek(o)
            yield h, f.read(s)
