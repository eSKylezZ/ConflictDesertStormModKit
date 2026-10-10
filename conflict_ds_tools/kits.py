"""Kit pieces: single objects of the game's world-piece models (cliff faces, wall runs, building shells, road
segments) as props you can place anywhere - like a level-design kit.

A level's ground models (MIS1_ZONE1.EVO ...) are hundreds of objects back to back with no transform keys: every
object's vertices and collision tree are in world space. A piece = one of those objects cut out byte for byte (its
geometry and collision tree untouched) with the header changed: model name, node type 0 (static prop), no parent, last
object (DONE). Nothing is shipped: a mod names the piece (``<name>.uses``: ``piece KIT_MIS1_ZONE1_045 = MIS1_ZONE1.EVO
45``) and DesertStormFix cuts it out of the player's own file at start-up (mods.cpp, same rules as here).

Because the vertices stay in world space, the piece's pivot is the level's origin: to show the piece with its centre
``c`` at ``p`` turned by yaw ``a``, the prop's record gets rotation ``a`` and position ``p - R(a) c`` (``placement``).
"""
from __future__ import annotations

import math
import struct

from . import evo

_EOBJ_TYPE = 8 + 64 + 64 + 4          # u32 node type
_EOBJ_PARENT = 8 + 64 + 64 + 4 + 4 + 4 + 4 + 24


def objects(data: bytes):
    """[(start, end)] of the EOBJ objects (end = after the NEXT / DONE tag)."""
    out, r = [], evo.Reader(data)
    while r.p < len(data):
        start = r.p
        evo.read_node(r)
        out.append((start, r.p))
    return out


def pieces(data: bytes):
    """What a world-piece model offers: [{index, node, textures, centre, size, collision, triangles}]."""
    out = []
    for i, n in enumerate(evo.read(data)):
        (x0, y0, z0), (x1, y1, z1) = n.bbox
        tris = sum(len(m.triangles) for m in n.meshes) if hasattr(n, "meshes") else 0
        out.append({"index": i, "node": n.name, "type": n.type,
                    "textures": sorted({m.texture for m in n.meshes if m.texture}),
                    "centre": ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
                    "size": (x1 - x0, y1 - y0, z1 - z0),
                    "collision": any(u == 0 and t for u, t in n.trees), "triangles": tris})
    return out


def extract(data: bytes, index: int, model_name: str) -> bytes:
    """Object ``index`` of a world-piece model as a one-object static prop named ``model_name``."""
    spans = objects(data)
    if not 0 <= index < len(spans):
        raise evo.EvoError(f"the model has {len(spans)} objects, not {index + 1}")
    start, end = spans[index]
    obj = bytearray(data[start:end])
    if obj[-4:] not in (b"NEXT", b"DONE"):
        raise evo.EvoError("object doesn't end in NEXT / DONE")
    name = model_name.encode("latin-1")[:63]
    obj[8:8 + 64] = name + b"\0" * (64 - len(name))
    struct.pack_into("<I", obj, _EOBJ_TYPE, 0)
    obj[_EOBJ_PARENT:_EOBJ_PARENT + 64] = b"ROOT" + b"\0" * 60
    obj[-4:] = b"DONE"
    return bytes(obj)


def placement(centre, at, yaw_deg):
    """Record position for a piece whose (world-space) centre should be at ``at`` turned by ``yaw_deg``: the game
    turns props about their origin, so the offset of the centre turns with them (yaw: forward (sin, cos) in x / z)."""
    a = math.radians(yaw_deg)
    cx, cy, cz = centre
    rx = cx * math.cos(a) + cz * math.sin(a)
    rz = -cx * math.sin(a) + cz * math.cos(a)
    return (at[0] - rx, at[1] - cy, at[2] - rz)
