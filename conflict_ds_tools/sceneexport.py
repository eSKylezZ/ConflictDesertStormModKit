"""Writing a level scene back from edits (the Blender scene export): placed records moved / turned / scaled, copied,
deleted, and new props for new models - applied to the scene that was imported, so everything the edit doesn't touch
(AI settings, waypoints, scripts' ids, portals, lights' other values) stays byte for byte. Pure Python (env.py,
levelscene.py), so it is testable without Blender; every game's scene versions env.py reads.

Edits are given in game space (cm, the record's pitch / yaw / roll convention, levelscene.record_matrix):

    moves   = {line: matrix}                      a placed record (ENVI / HLOI) on 1-based `line` gets this transform;
                                                  regions / sounds / lights take the position only
    aims    = {line: direction}                   a sun / spot light turned to shine along this game-space vector
    copies  = [(source line, matrix)]             a new record copied from the one on `source line`
    deletes = {line, ...}                          records removed
    props   = [(model, matrix)]                   new props (ENVI) placed from a plain prop of the scene

Line numbers are the imported scene's (the importer stores them as ds_line). A record whose transform didn't change
(matrices equal within 0.05 cm / 1e-4) is left exactly as it was.
"""
from __future__ import annotations

import math
import os

from . import env as envmod
from . import levelscene
from .env import ZONE_TYPES

POS_EPS = 0.05          # cm
ROT_EPS = 1e-4          # matrix entries
RINFO_FLAG = 0x1000000  # an ENVI followed by an indented RINFO line: never copied with it (editor _drop_rinfo_flag)


def record_from_matrix(m):
    """(pos, rot (pitch, yaw, roll) degrees, scale) of a game-space 4x4 / 3x4 matrix (rows) - the inverse of
    levelscene.record_matrix (R = Ry(yaw) . Rx(pitch) . Rz(roll), scale per column). Yaw in [0, 360)."""
    cols = [[m[i][j] for i in range(3)] for j in range(3)]
    scale = tuple(math.sqrt(sum(c * c for c in col)) or 1.0 for col in cols)
    r = [[m[i][j] / scale[j] for j in range(3)] for i in range(3)]
    sp = max(-1.0, min(1.0, -r[1][2]))
    pitch = math.asin(sp)
    if abs(math.cos(pitch)) > 1e-6:
        yaw = math.atan2(r[0][2], r[2][2])
        roll = math.atan2(r[1][0], r[1][1])
    else:                                   # looking straight up / down: roll folded into yaw
        yaw = math.atan2(-r[2][0], r[0][0])
        roll = 0.0
    rot = (math.degrees(pitch), math.degrees(yaw) % 360.0, math.degrees(roll))
    rot = tuple(0.0 if abs(a) < 1e-7 or abs(a - 360.0) < 1e-7 else a for a in rot)
    return tuple(float(m[i][3]) for i in range(3)), rot, scale


def _same(a, b):
    return all(abs(a[i][3] - b[i][3]) <= POS_EPS for i in range(3)) and \
        all(abs(a[i][j] - b[i][j]) <= ROT_EPS * max(1.0, abs(b[i][j])) for i in range(3) for j in range(3))


def _current(rec):
    """(matrix, has scale fields) of a placed record."""
    scaled = "scale.x" in rec.fields
    s = tuple(float(c) for c in rec["scale"]) if scaled else (1.0, 1.0, 1.0)
    return levelscene.record_matrix(tuple(float(c) for c in rec["pos"]), tuple(float(c) for c in rec["rot"]), s), scaled


def _place(rec, m, notes):
    """Write a matrix into a record's pos / rot / scale (or only pos for records without a rotation)."""
    pos, rot, scale = record_from_matrix(m)
    if "rot.x" not in rec.fields:
        rec["pos"] = pos
        return
    rec["pos"] = pos
    rec["rot"] = rot
    if "scale.x" in rec.fields:
        rec["scale"] = scale
    elif any(abs(s - 1.0) > 1e-3 for s in scale):
        notes.append("%s on line %d can't be scaled in this scene version (scale %.2f %.2f %.2f left out)"
                     % (rec.kind, rec.index + 1, *scale))


def _fingerprint(env, line):
    """(text, occurrence) that finds a record again after lines move (copies / deletes re-number them)."""
    text = env.lines[line - 1]
    n = sum(1 for r in env.records[:] if r.index < line - 1 and env.lines[r.index] == text)
    return text, n


def _find(env, fp):
    text, n = fp
    hits = [r for r in env.records if env.lines[r.index] == text]
    return hits[n] if n < len(hits) else (hits[0] if hits else None)


def _template_prop(env):
    """A plain prop record to copy new props from: an ENVI that isn't a world piece, has no VAMI / ANMI lines, no RINFO
    line and no breakable block (flags 0x400000); else any ENVI."""
    best = None
    for r in env.records:
        if r.kind != "ENVI" or r.children:
            continue
        try:
            typ = int(r.get("type", 0))
            flags = int(r.get("flags", 0))
        except (TypeError, ValueError):
            continue
        if typ in r.file.zone_types():
            continue
        if not flags & (RINFO_FLAG | 0x400000):
            return r
        best = best or r
    return best


def _zone_of(r):
    """The zone a placed record sits in (ENVI parent / HLOI zone), or None for world pieces and unreadable ones."""
    try:
        if r.kind == "ENVI":
            if int(r.get("type", 0)) in r.file.zone_types():
                return None
            return str(r["parent"])
        if r.kind == "HLOI":
            return str(r["zone"])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _nearest_zone(env, pos, skip=None):
    """The zone of the placed record nearest to pos (props and objects, world pieces left out) - where a new or moved
    record belongs: the game draws and culls zone by zone (the level editor's fix_zones does the same)."""
    best, d_best = None, None
    for r in env.records:
        if r is skip or r.kind not in ("ENVI", "HLOI"):
            continue
        zone = _zone_of(r)
        if not zone:
            continue
        try:
            p = tuple(float(c) for c in r["pos"])
        except (KeyError, TypeError, ValueError):
            continue
        d = sum((a - b) ** 2 for a, b in zip(p, pos))
        if d_best is None or d < d_best:
            best, d_best = zone, d
    return best or "Root"


REZONE_DIST = 500.0         # cm: a record moved further than this takes the zone of where it now stands


def _rezone(env, rec, notes):
    """Give a moved / copied prop or object the zone of its new place (if that differs)."""
    old = _zone_of(rec)
    if old is None:
        return
    new = _nearest_zone(env, tuple(float(c) for c in rec["pos"]), skip=rec)
    if new != "Root" and new.upper() != old.upper():
        rec["parent" if rec.kind == "ENVI" else "zone"] = new


LIGHT_POS = {"PLGT": 0, "DLGT": 3, "SLGT": 3}     # first of the three p<n> fields holding a light's position


def _light_keys(rec):
    """(position field names, (tilt, heading) field names) of a light record: DS1 / DS2 / TGE keep both in the p<n>
    tail; Vietnam / Global Storm records have named fields (levelscene._named_light)."""
    if "p0" in rec.fields:
        first = LIGHT_POS[rec.kind]
        return ["p%d" % (first + i) for i in range(3)], ("p0", "p1")
    pos = "pos" if "pos.x" in rec.fields else "v1"
    angles = next(((a, b) for a, b in (("dir.x", "dir.y"), ("rot.x", "rot.y"), ("pitch", "yaw"))
                   if a in rec.fields), (None, None))
    return ["%s.%s" % (pos, c) for c in "xyz"], angles


def _light(rec, m, aim):
    """Move a light record and turn a sun / spot to `aim` (game-space direction it shines along: tilt / heading,
    levelscene.direction). True if anything changed."""
    keys, (tilt_key, heading_key) = _light_keys(rec)
    old = [float(rec[k]) for k in keys]
    changed = False
    if any(abs(m[i][3] - old[i]) > POS_EPS for i in range(3)):
        for i, k in enumerate(keys):
            rec[k] = float(m[i][3])
        changed = True
    if aim is not None and rec.kind != "PLGT" and tilt_key:
        x, y, z = aim
        n = math.sqrt(x * x + y * y + z * z) or 1.0
        # the same direction has many tilt / heading pairs (325 = -35, any heading straight down): compare vectors
        old_dir = levelscene.direction(float(rec[tilt_key]), float(rec[heading_key]))
        if sum(a * b / n for a, b in zip(aim, old_dir)) < 1.0 - 1e-6:
            rec[tilt_key] = math.degrees(math.asin(max(-1.0, min(1.0, -y / n))))
            rec[heading_key] = math.degrees(math.atan2(x, z)) % 360.0
            changed = True
    return changed


def apply(env, moves=None, copies=(), deletes=(), props=(), aims=None):
    """Apply the edits to `env` (env.EnvFile) in place; returns notes (what was done, what couldn't be).
    `aims` = {line: game-space direction} for suns / spot lights turned in the edit."""
    notes = []
    moves = moves or {}
    aims = aims or {}
    counts = {"moved": 0, "copied": 0, "deleted": 0, "added": 0}
    # 1. moves: field writes only, no line numbers change
    for line, m in sorted(moves.items()):
        rec = env.record_at(line)
        if rec.kind in LIGHT_POS:
            try:
                if _light(rec, m, aims.get(line)):
                    counts["moved"] += 1
            except (KeyError, TypeError, ValueError):
                notes.append("%s on line %d not moved (unreadable)" % (rec.kind, line))
            continue
        if rec.kind in ("ENVI", "HLOI"):
            cur, _scaled = _current(rec)
            if _same(m, cur):
                continue
            far = sum((m[i][3] - cur[i][3]) ** 2 for i in range(3)) > REZONE_DIST ** 2
        else:
            old = tuple(float(c) for c in rec["pos"])
            if all(abs(m[i][3] - old[i]) <= POS_EPS for i in range(3)):
                continue
        _place(rec, m, notes)
        if rec.kind in ("ENVI", "HLOI") and far:
            _rezone(env, rec, notes)
        counts["moved"] += 1
    # 2. copies and deletes re-number lines: their records are found again by their text
    copy_fps = [(_fingerprint(env, line), m) for line, m in copies]
    del_fps = [_fingerprint(env, line) for line in sorted(deletes)]
    ids = [int(r.get("script_id")) for r in env.records if r.kind == "HLOI" and isinstance(r.get("script_id"), int)]
    next_id = max(ids, default=0) + 1
    for fp, m in copy_fps:
        src = _find(env, fp)
        if src is None:
            notes.append("a copied record wasn't found again - left out")
            continue
        changes = {}
        if src.kind == "HLOI" and "script_id" in src.fields:
            changes["script_id"] = next_id       # a script id is the object's handle: unique per level
            next_id += 1
        if src.kind == "HLOI" and "stat" in src.fields:
            # the editor ID (records layer, placed edits): unique like the level editor's copies (NAME_2, NAME_3 ...)
            stats = {str(r.get("stat", "")).upper() for r in env.records if r.kind == "HLOI"}
            base = str(src["stat"])
            n = 2
            while ("%s_%d" % (base, n)).upper() in stats:
                n += 1
            suffix = "_%d" % n
            changes["stat"] = base[:35 - len(suffix)] + suffix     # the loader's stat buffer holds 35 characters
        if src.kind == "ENVI" and "flags" in src.fields and int(src.get("flags", 0)) & RINFO_FLAG:
            changes["flags"] = int(src["flags"]) & ~RINFO_FLAG
        try:
            new = env.duplicate(src, **changes)
        except ValueError as e:
            notes.append("%s on line %d not copied: %s" % (src.kind, src.index + 1, e))
            continue
        _place(new, m, notes)
        _rezone(env, new, notes)
        counts["copied"] += 1
    for fp in del_fps:
        rec = _find(env, fp)
        if rec is None:
            continue
        try:
            env.delete(rec)
            counts["deleted"] += 1
        except ValueError as e:
            notes.append(str(e))
    # 3. new props: copies of a plain prop with the new model, in the zone of the nearest prop
    if props:
        tmpl = _template_prop(env)
        if tmpl is None:
            notes.append("the scene has no prop to copy - %d new model(s) not placed" % len(props))
        else:
            tfp = _fingerprint(env, tmpl.index + 1)
            for model, m in props:
                pos = record_from_matrix(m)[0]
                src = _find(env, tfp)
                changes = {"model": model}
                if "name" in src.fields:
                    changes["name"] = model
                zone = _nearest_zone(env, pos)
                if "parent" in src.fields or "parent" in src.aliases:
                    changes["parent"] = zone
                if "flags" in src.fields and int(src.get("flags", 0)) & RINFO_FLAG:
                    changes["flags"] = int(src["flags"]) & ~RINFO_FLAG
                new = env.duplicate(src, **changes)
                _place(new, m, notes)
                counts["added"] += 1
    notes.insert(0, "%(moved)d moved, %(copied)d copied, %(deleted)d deleted, %(added)d new prop(s)" % counts)
    return notes


def write(env, path):
    """Write the scene: one file, or a split scene's index (`path`) and its parts next to it (Global Storm)."""
    parts = env.split_files()
    if parts:
        folder = os.path.dirname(os.path.abspath(path))
        with open(path, "wb") as f:
            f.write(env.split_index)
        for name, data in parts.items():
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
        return [path] + [os.path.join(folder, n) for n in parts]
    env.save(path)
    return [path]
