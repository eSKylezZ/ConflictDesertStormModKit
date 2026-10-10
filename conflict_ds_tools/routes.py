"""Conflict: Desert Storm AI route markers (``<LEVEL>.RMD``, in missionN.dat).

The route network soldiers path along. It is authored as the scene's ``NEWENVMARKER`` lines (see ``env.py``), but the
game skips those and loads this compiled copy instead (RouteMarker.cpp: load `FUN_00504f00`, per marker
`FUN_00503f40`, writer `FUN_00504fd0`). Debug overlay: start-up flag ``m``, key T ("Toggle RouteMarkerDisplay")
draws every marker as ``id (n) [weight]`` with arrows along its links.

File::

    i32 -1, i32 version (1)      # absent in version-0 files: the first i32 is then the count
    i32 count
    count x marker (32 bytes; 28 in version 0 - no weight):
        f32 x, y, z              # cm, Y up
        u16 link[5]              # marker indices this one leads to, 0xFFFF = none (directed: ~15-30 % one-way)
        u16 unknown              # 0 in every shipped file
        u16 flags                # 0x80 = named in the scene
        u16 filler               # read and discarded by the game (0xCDCD - uninitialised memory)
        f32 weight               # 1.0 in every shipped file

Markers come in two kinds (the scene's NEWENVMARKER type letter; not stored here): ``M`` = placed by hand (the first
indices, Mission 1: 0-338) and ``Q`` = one per walkable cell of the level's quadtree ``<LEVEL>.QTD``, exactly at the
cell centre (Mission 1: 339-2011). The debug overlay draws markers no quadtree leaf refers to in green (verified with
markers added by this module), the others red.

``<LEVEL>.QTD`` (AI walkable map, "Toggle Quad Tree Display": blue walkable / red blocked)::

    i32 count
    count x node (20 bytes): u16 kind, u16 data, u32 child, f32 centre x, f32 centre z, f32 half size (cm)
        internal: kind 0, data 0xCDCD, child = byte offset of the first of 4 children (from the first node)
        walkable leaf: kind 3, data = its Q marker, child 0       (Mission 1: half sizes 125-1000)
        blocked leaf:  kind 0, data = a marker (probably the nearest walkable one), child 0
    i32 walkable leaves, i32 blocked leaves

Shipped files: the 15 single-player levels (none for MP, Training 1-2, Outro). They match the scene's markers except
for a few dozen links / flags per level - the .rmd was compiled from a slightly different scene, and it is what runs.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

NONE = 0xFFFF
FILLER = 0xCDCD


@dataclass
class Marker:
    pos: tuple[float, float, float]
    links: list[int] = field(default_factory=list)  # up to 5 marker indices
    flags: int = 0
    unknown: int = 0
    weight: float = 1.0
    filler: int = FILLER

    def as_dict(self, index: int) -> dict:
        return {"index": index, "pos": [round(c, 2) for c in self.pos], "links": list(self.links),
                "flags": self.flags, "weight": self.weight}


class RouteFile:
    def __init__(self, markers: list[Marker] | None = None, version: int = 1):
        self.markers: list[Marker] = markers or []
        self.version = version

    # --- I/O ------------------------------------------------------------------------------------------------------
    @classmethod
    def read(cls, data: bytes) -> "RouteFile":
        first, = struct.unpack_from("<i", data, 0)
        if first == -1:
            version, count = struct.unpack_from("<ii", data, 4)
            o = 12
        else:
            version, count, o = 0, first, 4
        size = 32 if version > 0 else 28
        if o + count * size != len(data):
            raise ValueError(f"route file size {len(data)} doesn't fit {count} markers of {size} bytes")
        markers = []
        for i in range(count):
            x, y, z, *links, unknown, flags, filler = struct.unpack_from("<3f5HHHH", data, o)
            weight = struct.unpack_from("<f", data, o + 28)[0] if version > 0 else 1.0
            markers.append(Marker((x, y, z), [l for l in links if l != NONE], flags, unknown, weight, filler))
            o += size
        return cls(markers, version)

    @classmethod
    def load(cls, path) -> "RouteFile":
        with open(path, "rb") as f:
            return cls.read(f.read())

    def to_bytes(self) -> bytes:
        out = bytearray()
        if self.version > 0:
            out += struct.pack("<ii", -1, self.version)
        out += struct.pack("<i", len(self.markers))
        for m in self.markers:
            if len(m.links) > 5:
                raise ValueError(f"marker at {m.pos} has {len(m.links)} links (max 5)")
            links = list(m.links) + [NONE] * (5 - len(m.links))
            out += struct.pack("<3f5HHHH", *m.pos, *links, m.unknown, m.flags, m.filler)
            if self.version > 0:
                out += struct.pack("<f", m.weight)
        return bytes(out)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    # --- from the scene ---------------------------------------------------------------------------------------------
    @classmethod
    def from_env(cls, env) -> "RouteFile":
        """Compile the scene's NEWENVMARKER lines (marker ids must be 0..n-1 in order, as in every shipped scene)."""
        recs = env.find("NEWENVMARKER")
        for i, r in enumerate(recs):
            if r["id"] != i:
                raise ValueError(f"line {r.index + 1}: marker id {r['id']} out of order (expected {i})")
        markers = [Marker(r["pos"], [l for l in (r[f"link{k}"] for k in range(5)) if l != NONE], r["flags"])
                   for r in recs]
        return cls(markers)

    # --- queries / edits (command layer) ---------------------------------------------------------------------------
    def nearest(self, pos, count: int = 1) -> list[int]:
        d = sorted(range(len(self.markers)),
                   key=lambda i: sum((a - b) ** 2 for a, b in zip(self.markers[i].pos, pos)))
        return d[:count]

    def add(self, pos, links=(), flags: int = 0) -> int:
        """New marker linked both ways to ``links``; returns its index."""
        self.markers.append(Marker(tuple(pos), [], flags))
        i = len(self.markers) - 1
        for j in links:
            self.link(i, j)
        return i

    def hand_count(self) -> int:
        """Hand-placed markers come first (flags bit 0 clear); the rest are generated from the walk map (one per walkable
        .qtd cell, bit 0 set) - DesertStormFix rebuilds those from the scene for editor missions."""
        n = 0
        while n < len(self.markers) and not self.markers[n].flags & 1:
            n += 1
        return n

    def insert(self, i: int, pos, links=(), flags: int = 0) -> int:
        """New marker at index ``i`` (later ones shift up, links follow), linked both ways to ``links`` (old indices)."""
        for m in self.markers:
            m.links = [l + (l >= i) for l in m.links]
        self.markers.insert(i, Marker(tuple(pos), [], flags))
        for j in links:
            self.link(i, j + (j >= i))
        return i

    def link(self, a: int, b: int, both: bool = True):
        """Link ``a`` -> ``b`` (and ``b`` -> ``a`` unless ``both`` is false); no-op where already linked."""
        pairs = ((a, b), (b, a)) if both else ((a, b),)
        for x, y in pairs:  # check both ends first, so a full marker leaves neither changed
            ls = self.markers[x].links
            if y not in ls and len(ls) >= 5:
                raise ValueError(f"marker {x} already has 5 links")
        for x, y in pairs:
            if y not in self.markers[x].links:
                self.markers[x].links.append(y)

    def unlink(self, a: int, b: int):
        for x, y in ((a, b), (b, a)):
            if y in self.markers[x].links:
                self.markers[x].links.remove(y)

    def move(self, i: int, pos):
        self.markers[i].pos = tuple(pos)

    def delete(self, i: int):
        """Remove marker ``i``; links to it are dropped and higher indices shift down."""
        del self.markers[i]
        for m in self.markers:
            m.links = [l - (l > i) for l in m.links if l != i]

    def problems(self) -> list[str]:
        """Links out of range or to itself, more than 5 links. (One-way links are normal.)"""
        out = []
        n = len(self.markers)
        for i, m in enumerate(self.markers):
            if len(m.links) > 5:
                out.append(f"marker {i}: {len(m.links)} links (max 5)")
            for l in m.links:
                if not 0 <= l < n or l == i:
                    out.append(f"marker {i}: bad link {l}")
        return out

    def one_way(self) -> list[tuple[int, int]]:
        return [(i, l) for i, m in enumerate(self.markers) for l in m.links
                if 0 <= l < len(self.markers) and i not in self.markers[l].links]
