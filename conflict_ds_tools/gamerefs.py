"""Game data by reference: mods that reuse the game's files without shipping them.

A mission made with the editor uses game files - models and textures from other levels, its base level's AI walk map,
the base scene it was edited from. Shipping those would redistribute the game's data, so a mod carries only:

  <name>.uses      "file T72M1.EVO"                    the player's own copy (from any game .dat) under that name
                   "file AMBUSH.QTD = MISSION1.QTD"    the player's copy of the right-hand file, renamed
                   "bank VehicleTankWeaponT72"         a sound bank out of the game's level sound caches
  <name>.envpatch  the scene as line changes against the game's scene:
                   "base MISSION1.ENV", then hunks "@ <start> <delete> <insert>" followed by <insert> new lines
                   (start = 0-based line of the base file; hunks in order)
  <name>.rmdpatch  the AI route network as changes: "base MISSION1.RMD", "count <n>", then one line
                   "<i> x y z l0 l1 l2 l3 l4 flags weight" for every marker that differs from base marker <i>

DesertStormFix (mods.cpp BuildReferences) builds the full files from the player's install at start-up; this module
writes and applies the same formats for the editor.
"""
from __future__ import annotations

import copy
import difflib
import struct

from .routes import NONE, Marker, RouteFile


# ---- scenes ----
def make_env_patch(base_lines: list[str], new_lines: list[str], base_name: str) -> str:
    out = [f"base {base_name}"]
    sm = difflib.SequenceMatcher(None, base_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.append(f"@ {i1} {i2 - i1} {j2 - j1}")
        out.extend(new_lines[j1:j2])
    return "\n".join(out) + "\n"


def patch_base(text: str) -> str:
    """The base file a patch applies to."""
    first = text.split("\n", 1)[0].strip()
    if not first.startswith("base "):
        raise ValueError("not a game-data patch (no 'base' line)")
    return first[5:].strip()


def apply_env_patch(patch: str, base_lines: list[str]) -> list[str]:
    lines = patch.split("\n")
    out, at, i = [], 0, 1
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.startswith("@"):
            continue
        start, delete, insert = (int(x) for x in line[1:].split())
        if start < at or start + delete > len(base_lines):
            raise ValueError(f"patch hunk {line!r} doesn't fit the base scene")
        out.extend(base_lines[at:start])
        at = start + delete
        out.extend(lines[i:i + insert])
        i += insert
    out.extend(base_lines[at:])
    return out


# ---- route networks ----
def _row(m: Marker) -> tuple:
    links = list(m.links) + [NONE] * (5 - len(m.links))
    return (*(struct.unpack("<f", struct.pack("<f", c))[0] for c in m.pos), *links, m.flags,
            struct.unpack("<f", struct.pack("<f", m.weight))[0])


def make_rmd_patch(base: RouteFile, new: RouteFile, base_name: str) -> str:
    out = [f"base {base_name}", f"count {len(new.markers)}"]
    for i, m in enumerate(new.markers):
        if i < len(base.markers) and _row(base.markers[i]) == _row(m):
            continue
        x, y, z, *links, flags, w = _row(m)
        out.append(f"{i} {x:.9g} {y:.9g} {z:.9g} " + " ".join(str(l) for l in links) + f" {flags} {w:.9g}")
    return "\n".join(out) + "\n"


def apply_rmd_patch(patch: str, base: RouteFile) -> RouteFile:
    lines = [l for l in patch.split("\n") if l.strip()]
    count = int(lines[1].split()[1])
    # markers past the base that no line describes stay unlinked at the origin (never None)
    markers = [copy.deepcopy(base.markers[i]) if i < len(base.markers) else Marker(pos=(0.0, 0.0, 0.0))
               for i in range(count)]
    for line in lines[2:]:
        f = line.split()
        i = int(f[0])
        m = markers[i]
        m.pos = (float(f[1]), float(f[2]), float(f[3]))
        m.links = [int(x) for x in f[4:9] if int(x) != NONE]
        m.flags, m.weight = int(f[9]), float(f[10])
        markers[i] = m
    return RouteFile(markers, base.version)
