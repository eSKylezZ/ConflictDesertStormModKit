"""A level scene (``<LEVEL>.ENV``, see env.py) as things to place: props, objects, lights, regions and sounds with
their transforms in game space - what the Blender scene import builds. No Blender in here, so it is testable.

Transforms (same as the level editor and kits.placement): positions in cm, rotations ``rot`` = (pitch, yaw, roll) in
degrees, applied as R = Ry(yaw) . Rx(pitch) . Rz(roll) on the game's (x, y up, z) axes; yaw turns forward (0, 0, 1)
to (sin, 0, cos), a positive pitch looks down. World pieces (ENVI type 5) and props are both placed by their record:
props are in world space whatever zone they belong to.

Lights (the tail after the two colours, decoded from the shipped scenes):

=====  =====================================================================================================
DLGT   tilt, heading, 0, gizmo position x y z, 100            - the sun / directional lights
PLGT   position x y z, radius, ...                            - point lights (radius 600-1000 cm in Mission 1)
SLGT   tilt, heading, 0, position x y z, inner?, outer?, range - spot lights (cone angles in degrees: likely)
=====  =====================================================================================================

Kit pieces / referenced files: a mod's ``<name>.uses`` lines (gamerefs.py, mods.cpp BuildReferences) say which model
file a name stands for - ``file X``, ``file A = B``, ``piece KIT_X = MODEL.EVO <index>``.
"""
from __future__ import annotations

import math
import os
import re

from . import env as envmod

SKIP_MODELS = {"", "NULL", "NONE", "(NULL)", "DEFAULT"}


# ---- transforms ----
def rotation(rot):
    """3x3 game-space rotation (rows) for a record's (pitch, yaw, roll) in degrees."""
    p, y, r = (math.radians(float(a)) for a in rot)
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    ry = ((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy))
    rx = ((1.0, 0.0, 0.0), (0.0, cp, -sp), (0.0, sp, cp))
    rz = ((cr, -sr, 0.0), (sr, cr, 0.0), (0.0, 0.0, 1.0))
    return _mul3(_mul3(ry, rx), rz)


def _mul3(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def record_matrix(pos, rot=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """4x4 game-space matrix (rows) T(pos) . R(rot) . S(scale) - the evo.mat_* layout."""
    r = rotation(rot)
    s = tuple(float(c) for c in scale)
    m = [[r[i][j] * s[j] for j in range(3)] + [float(pos[i])] for i in range(3)]
    return m + [[0.0, 0.0, 0.0, 1.0]]


def direction(tilt, heading):
    """Unit game-space vector a light with this tilt / heading (degrees) shines along (the editor's gameForward)."""
    p, y = math.radians(float(tilt)), math.radians(float(heading))
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


# ---- referenced files ----
_USES = re.compile(r"^\s*(file|piece)\s+(\S+)(?:\s*=\s*(\S+)(?:\s+(\d+))?)?\s*$", re.I)


def read_uses(text):
    """{NAME (upper, no extension): (source file, object index or None)} from a .uses file's file / piece lines."""
    out = {}
    for line in text.splitlines():
        m = _USES.match(line)
        if not m:
            continue
        kind, name, src, index = m.groups()
        stem = os.path.splitext(name)[0].upper()
        if kind.lower() == "piece":
            if src and index is not None:
                out[stem] = (src, int(index))
        else:
            out[stem] = (src or name, None)
    return out


def folder_uses(folder):
    """read_uses over every .uses file in a folder (a mod's)."""
    out = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        if n.lower().endswith(".uses"):
            try:
                with open(os.path.join(folder, n), encoding="latin-1") as f:
                    out.update(read_uses(f.read()))
            except OSError:
                pass
    return out


_KIT = re.compile(r"^KIT_(.+)_(\d{3,})$", re.I)


def kit_from_name(model):
    """(source model, object index) for a kit piece named the editor's way (KIT_<MODEL>_<nnn>), else None."""
    m = _KIT.match(model or "")
    return (m.group(1) + ".EVO", int(m.group(2))) if m else None


# ---- the scene ----
def _f(rec, name, default=0.0):
    try:
        v = rec[name]
    except KeyError:
        return default
    return float(v) if isinstance(v, (int, float)) else default


def _ps(rec, n):
    """The light record's tail values p0..p<n-1> as floats (None when the record is shorter)."""
    out = []
    for k in range(n):
        v = rec.get(f"p{k}")
        if not isinstance(v, (int, float)):
            return None
        out.append(float(v))
    return out


def light(rec):
    """A light record as {kind, name, colour, ambient, pos, ...} (kind SUN / POINT / SPOT), or None if unreadable."""
    try:
        colour = tuple(_f(rec, f"colour{k}") for k in range(3))
        ambient = tuple(_f(rec, f"ambient{k}") for k in range(3))
        base = {"line": rec.index + 1, "name": str(rec["name"]), "flags": int(rec["flags"]), "colour": colour,
                "ambient": ambient}
    except (KeyError, TypeError, ValueError):
        return None
    if rec.kind == "PLGT":
        p = _ps(rec, 4)
        if p is None:
            return None
        return dict(base, kind="POINT", pos=tuple(p[0:3]), radius=p[3])
    if rec.kind == "DLGT":
        p = _ps(rec, 6)
        if p is None:
            return None
        return dict(base, kind="SUN", tilt=p[0], heading=p[1], pos=tuple(p[3:6]))
    if rec.kind == "SLGT":
        p = _ps(rec, 9)
        if p is None:
            return None
        return dict(base, kind="SPOT", tilt=p[0], heading=p[1], pos=tuple(p[3:6]), inner=p[6], outer=p[7],
                    range=p[8])
    return None


def region(rec):
    """REGN as {name, shape SPHERE / BOX, pos, radius | rot, centre (local), size}. Box min / max are local to pos
    and turned by rot (as the editor draws them and game.in_region tests them)."""
    try:
        out = {"line": rec.index + 1, "name": str(rec["name"]), "pos": tuple(float(c) for c in rec["pos"])}
        if rec["type"] == 1:
            return dict(out, shape="SPHERE", radius=float(rec["radius"]))
        lo, hi = rec["min"], rec["max"]
        return dict(out, shape="BOX", rot=tuple(float(c) for c in rec["rot"]),
                    centre=tuple((float(a) + float(b)) / 2 for a, b in zip(lo, hi)),
                    size=tuple(abs(float(b) - float(a)) for a, b in zip(lo, hi)))
    except (KeyError, TypeError, ValueError):
        return None


def sound(rec):
    try:
        return {"line": rec.index + 1, "name": str(rec["name"]), "sound": str(rec["sound"]),
                "pos": tuple(float(c) for c in rec["pos"]), "min_distance": float(rec["min_distance"]),
                "max_distance": float(rec["max_distance"]), "volume": float(rec["volume"]), "loop": int(rec["loop"])}
    except (KeyError, TypeError, ValueError):
        return None


def placed(rec):
    """ENVI / HLOI record as {kind, line, name, model, pos, rot, scale, zone, type}, or None (no model / unreadable)."""
    try:
        model = str(rec["model"])
        pos = tuple(float(c) for c in rec["pos"])
        rot = tuple(float(c) for c in rec["rot"])
    except (KeyError, TypeError, ValueError):
        return None
    if model.strip().upper() in SKIP_MODELS:
        return None
    out = {"kind": rec.kind, "line": rec.index + 1, "model": model, "pos": pos, "rot": rot,
           "scale": tuple(float(c) for c in rec["scale"]) if "scale.x" in rec.fields else (1.0, 1.0, 1.0)}
    if rec.kind == "ENVI":
        out.update(name=str(rec.get("name", model)), zone=str(rec.get("parent", "")), type=int(rec.get("type", 0)))
    else:
        out.update(name=f"{model} #{rec.get('script_id', '?')}", zone=str(rec.get("zone", "")), type=-1,
                   script_id=rec.get("script_id"), unit=str(rec.get("unit", "")), in_play=rec.get("in_play"))
    return out


def scene_items(env):
    """Everything placeable in an EnvFile: {"props": [...], "objects": [...], "lights": [...], "regions": [...],
    "sounds": [...]} (props = ENVI incl. world pieces with type 5, objects = HLOI)."""
    out = {"props": [], "objects": [], "lights": [], "regions": [], "sounds": []}
    for rec in env.records:
        if not rec.fields:
            continue
        k = rec.kind
        if k in ("ENVI", "HLOI"):
            item = placed(rec)
            if item:
                out["props" if k == "ENVI" else "objects"].append(item)
        elif k in ("DLGT", "PLGT", "SLGT"):
            item = light(rec)
            if item:
                out["lights"].append(item)
        elif k == "REGN":
            item = region(rec)
            if item:
                out["regions"].append(item)
        elif k == "ENVSND":
            item = sound(rec)
            if item:
                out["sounds"].append(item)
    return out


def read_scene(path, finder=None):
    """EnvFile for a .ENV, or for an editor mod's .envpatch (base scene found with finder: an evo.AssetFinder)."""
    with open(path, "rb") as f:
        data = f.read()
    if not path.lower().endswith(".envpatch"):
        return envmod.EnvFile.read(data)
    from . import gamerefs
    text = data.decode("latin-1").replace("\r\n", "\n")
    base = gamerefs.patch_base(text)
    hit = finder.find(base, (".env",)) if finder and base else None
    if not hit:
        raise FileNotFoundError(f"{os.path.basename(path)} is a patch of {base or '?'}, which isn't in the extracted "
                                f"folders - extract the game archives first")
    with open(hit, "rb") as f:
        base_env = envmod.EnvFile.read(f.read())
    lines = gamerefs.apply_env_patch(text, base_env.lines)
    return envmod.EnvFile("\r\n".join(lines))


def extent(items, margin=3000.0):
    """((min x, min z), (max x, max z)) around everything placed, grown by margin (cm) - the editor's fallback bounds."""
    pts = [i["pos"] for i in items.get("props", []) + items.get("objects", []) if i.get("type") != 5]
    if not pts:
        return None
    return ((min(p[0] for p in pts) - margin, min(p[2] for p in pts) - margin),
            (max(p[0] for p in pts) + margin, max(p[2] for p in pts) + margin))
