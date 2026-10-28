"""World-building tools modelled on the Fallout 4 Creation Kit's Render Window (CreationKit.exe dialogs mapped with REA,
2026-10-15 - feature ideas only, no CK code or data):

- Object Palette (CK dialog 375 "Object Palette": objects to create from, per-object random X / Y / Z angle, scale,
  sink, "Conform to slope", saved palettes) -> palette_* commands and palette_paint.
- Duplicate Selection (dialog 5816: Count, Prefix, Suffix, Search, Replace) -> duplicate_array, plus an offset and a
  turn per copy (arrays of fence posts, rows of tents).
- Search & Replace (dialog 198: scope current cell / selection) -> replace_base: swap the base object of props or
  objects, keeping where they stand.
- Apply Transform To Refs (dialog 490: translate / rotate) -> transform_selection, exact numeric offsets.
- Paste in Place (Edit menu, Ctrl+Shift+V) -> paste_in_place.
- NavMesh "Normal Pathing Test" -> path_test: the route between two points over the level's route markers, searched
  like the game's router (directed links, A*), with the gaps it can't cross.
- File > Export > "Ref Placements for Selection" -> export_placements / import_placements (CSV round trip).

Editor-only view settings from the CK (Show/Hide window, Picking Preferences, layer hide / freeze, Go to Location,
Render Window Hotkeys) live in web/worldtools.js - they change nothing in the scene.
"""
from __future__ import annotations

import csv
import heapq
import io
import json
import math
import os
import random
import re
import time

import commands as _c
from commands import CommandError, Session, command, _paste_entries, _turn, _unique, _vec, _PASTE_END  # noqa: F401
from commands import _crew_texts, _is_vehicle, _library, _all_library, _drop_rinfo_flag, _nearest_zone, _chunk  # noqa: F401
import logic


def _need_level(s: Session):
    if s.env is None:
        raise CommandError("no level open - open_level or open_mission first")


def _lines(lines):
    out = [int(l) for l in (lines if isinstance(lines, list) else [lines])]
    if not out:
        raise CommandError("no records given - select something first")
    return list(dict.fromkeys(out))


def _copyable(r):
    return r.kind in _PASTE_END and not (r.kind == "ENVI" and (not r.file.is_prop(r) or r.children))


def _key(r):
    """What finds a record again after line numbers shift: script id for objects, else kind + name."""
    if r.kind == "HLOI":
        return ("HLOI", r["script_id"])
    if r.kind == "NEWENVWAYPT":
        return ("NEWENVWAYPT", r["id"])
    return (r.kind, str(r.get("name")))


def _line_of(s: Session, key):
    kind, val = key
    for r in s.env.find(kind):
        if not r.fields:
            continue
        if (kind == "HLOI" and r["script_id"] == val) or (kind == "NEWENVWAYPT" and r["id"] == val) \
                or (kind not in ("HLOI", "NEWENVWAYPT") and str(r.get("name")) == val):
            return r.index + 1
    return None


# =========================================================================================== rotation maths ====
# The viewport draws a record as three.js Euler (-rot.x, -rot.y, rot.z) in order YXZ (app.js placeProp), with game
# (x, y, z) -> three (x, y, -z). Conform-to-slope builds the wanted three-space rotation (yaw about up, then tilt up
# onto the ground normal) and turns it back into game degrees the same way, so the editor shows exactly what it wrote.
def _rot_y(a):
    c, s_ = math.cos(a), math.sin(a)
    return [[c, 0, s_], [0, 1, 0], [-s_, 0, c]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _align_up(n):
    """Rotation taking three-space up (0, 1, 0) onto unit vector n (Rodrigues)."""
    ax = (n[2], 0.0, -n[0])                     # up x n
    sin_a = math.hypot(ax[0], ax[2])
    cos_a = n[1]
    if sin_a < 1e-9:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]] if cos_a > 0 else [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
    kx, ky, kz = ax[0] / sin_a, 0.0, ax[2] / sin_a
    t = 1 - cos_a
    return [[cos_a + kx * kx * t, kx * ky * t - kz * sin_a, kx * kz * t + ky * sin_a],
            [ky * kx * t + kz * sin_a, cos_a + ky * ky * t, ky * kz * t - kx * sin_a],
            [kz * kx * t - ky * sin_a, kz * ky * t + kx * sin_a, cos_a + kz * kz * t]]


def _euler_yxz(m):
    """three.js Euler.setFromRotationMatrix, order YXZ."""
    m23 = max(-1.0, min(1.0, m[1][2]))
    x = math.asin(-m23)
    if abs(m23) < 0.9999999:
        y = math.atan2(m[0][2], m[2][2])
        z = math.atan2(m[1][0], m[1][1])
    else:
        y = math.atan2(-m[2][0], m[0][0])
        z = 0.0
    return x, y, z


def conform_rot(yaw_deg, normal_game, max_tilt=45.0):
    """Game rot (x, y, z) degrees: facing yaw_deg, its up along the ground normal (tilt capped at max_tilt)."""
    nx, ny, nz = normal_game
    n = (nx, ny, -nz)                           # -> three space
    ln = math.sqrt(sum(c * c for c in n)) or 1.0
    n = tuple(c / ln for c in n)
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, n[1]))))
    if tilt > max_tilt > 0:                     # too steep (a cliff face): lean only part of the way
        h = math.hypot(n[0], n[2]) or 1.0
        a = math.radians(max_tilt)
        n = (n[0] / h * math.sin(a), math.cos(a), n[2] / h * math.sin(a))
    m = _mul(_align_up(n), _rot_y(-math.radians(yaw_deg)))
    x, y, z = _euler_yxz(m)
    return (round(-math.degrees(x), 3), round((-math.degrees(y)) % 360.0, 3), round(math.degrees(z), 3))


# ============================================================================================ object palette ====
# A palette = objects to create from, each with a weight and its own random ranges. Stored per editor install (like
# prefabs) by library identity (kind, model, unit), so a palette made in one level paints in any level whose library
# has those types (the library is every shipped level's props and objects).
def _palette_dir(s: Session):
    return os.path.join(s.data_dir, "_editor_cache", "palettes")


def _palette_path(s: Session, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _\-]{0,47}", str(name or "")):
        raise CommandError("palette name: letters, digits, space, _ and - (up to 48)")
    return os.path.join(_palette_dir(s), name.strip() + ".json")


_ENTRY_DEFAULTS = {"weight": 1.0, "yaw": [0.0, 360.0], "tilt": 0.0, "scale": [1.0, 1.0], "sink": [0.0, 0.0],
                   "conform": False, "max_tilt": 30.0}


def _lib_identity(e):
    return {"kind": e["kind"], "model": e["model"], "unit": e.get("unit"), "label": e["label"]}


def _resolve(s: Session, entry):
    """A palette entry -> its library entry (by template id when given, else by kind / model / unit)."""
    lib = _all_library(s)
    if entry.get("template") is not None and not entry.get("model"):
        t = int(entry["template"])
        if not 0 <= t < len(lib):
            raise CommandError(f"unknown template {t}")
        return lib[t]
    model = str(entry.get("model") or "").upper()
    unit = str(entry.get("unit") or "").upper()
    for e in lib:
        if e["kind"] == entry.get("kind", e["kind"]) and e["model"].upper() == model \
                and (e["kind"] == "prop" or str(e.get("unit") or "").upper() == unit):
            return e
    raise CommandError(f"no {entry.get('kind', 'template')} {entry.get('model')} {entry.get('unit') or ''} in the "
                       "library (no shipped level has it)")


def _clean_entry(s: Session, entry):
    lib = _resolve(s, entry)
    out = {**_lib_identity(lib), "template": lib["id"]}
    for k, d in _ENTRY_DEFAULTS.items():
        v = entry.get(k, d)
        if isinstance(d, list):
            if not isinstance(v, (list, tuple)):
                v = [v, v]
            v = [float(v[0]), float(v[1])]
            if v[0] > v[1]:
                v = [v[1], v[0]]
        elif isinstance(d, bool):
            v = bool(v)
        else:
            v = float(v)
        out[k] = v
    if out["weight"] <= 0:
        raise CommandError(f"{out['label']}: weight must be above 0")
    if not (0.1 <= out["scale"][0] and out["scale"][1] <= 10):
        raise CommandError(f"{out['label']}: scale 0.1 - 10")
    if lib["kind"] != "prop":                   # soldiers / vehicles stay upright and full size (physics, animation)
        out.update(tilt=0.0, scale=[1.0, 1.0], conform=False)
    return out


@command("Object Palette (Creation Kit style): the saved palettes - name, objects, note.")
def palette_list(s: Session):
    out = []
    folder = _palette_dir(s)
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if f.lower().endswith(".json"):
            try:
                d = json.load(open(os.path.join(folder, f), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"name": d.get("name", f[:-5]), "note": d.get("note", ""),
                        "objects": [e.get("label") for e in d.get("entries", [])], "saved": d.get("saved")})
    return out


@command("One saved palette with its entries (each: kind, model, unit, label, template id in this level's library, "
         "weight, yaw [min, max] degrees, tilt ± degrees, scale [min, max], sink [min, max] cm, conform, max_tilt).",
         name="palette name")
def palette_get(s: Session, name):
    path = _palette_path(s, name)
    if not os.path.exists(path):
        raise CommandError(f"no palette {name!r}")
    d = json.load(open(path, encoding="utf-8"))
    entries = []
    for e in d.get("entries", []):
        try:
            entries.append(_clean_entry(s, e))
        except CommandError as ex:
            entries.append({**e, "missing": str(ex)})
    return {"name": d.get("name", name), "note": d.get("note", ""), "entries": entries}


@command("Save an Object Palette: the objects a brush paints, each with a weight (how often it is picked) and random "
         "ranges - yaw [min, max] degrees, tilt ± degrees (props), scale [min, max] (props), sink [min, max] cm into the "
         "ground, conform (props lean with the slope, up to max_tilt degrees). Entries name a library template "
         "(`template` id) or kind / model / unit.", name="palette name", entries="list of entries", note="optional")
def palette_save(s: Session, name, entries, note=""):
    _need_level(s)
    if not isinstance(entries, list) or not entries:
        raise CommandError("entries: at least one object")
    clean = [_clean_entry(s, e) for e in entries]
    os.makedirs(_palette_dir(s), exist_ok=True)
    doc = {"name": name.strip(), "note": str(note or ""), "saved": time.strftime("%Y-%m-%d %H:%M"),
           "entries": [{k: v for k, v in e.items() if k != "template"} for e in clean]}
    with open(_palette_path(s, name), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    return {"name": doc["name"], "objects": len(clean)}


@command("Delete a saved Object Palette.", name="palette name")
def palette_delete(s: Session, name):
    path = _palette_path(s, name)
    if not os.path.exists(path):
        raise CommandError(f"no palette {name!r}")
    os.remove(path)
    return {"deleted": name}


def _uniform(rng, pair):
    return pair[0] if pair[0] == pair[1] else rng.uniform(pair[0], pair[1])


@command("Paint with an Object Palette: one object per point, picked by weight, with that entry's random facing, tilt, "
         "size and sink; with conform on, props lean with the ground (point normal, game axes, y up). One undo step. "
         "Give a saved palette's name or the entries themselves.", mutates=True,
         points="list of {pos: [x, y, z], normal: optional [x, y, z]}", name="saved palette (or entries)",
         entries="palette entries (instead of name)", seed="optional random seed (same seed = same result)",
         spacing="optional minimum distance in cm between painted objects and earlier ones of the stroke")
def palette_paint(s: Session, points, name=None, entries=None, seed=None, spacing=0):
    if entries is None:
        if not name:
            raise CommandError("give a palette name or entries")
        entries = palette_get(s, name)["entries"]
    clean = [e for e in (_clean_entry(s, e) for e in entries if not e.get("missing"))]
    if not clean:
        raise CommandError("the palette has no object this level's library knows")
    if not isinstance(points, list) or not points:
        raise CommandError("points: at least one {pos}")
    if len(points) > 500:
        raise CommandError("at most 500 points per stroke")
    rng = random.Random(seed)
    lib = _all_library(s)
    total = sum(e["weight"] for e in clean)
    placed, keys, kept = [], [], []
    spacing = float(spacing or 0)
    for p in points:
        pos = _vec(p["pos"] if isinstance(p, dict) else p)
        if spacing and any(math.dist(pos, q) < spacing for q in kept):
            continue
        kept.append(pos)
        pick, acc = rng.uniform(0, total), 0.0
        e = clean[-1]
        for cand in clean:
            acc += cand["weight"]
            if pick <= acc:
                e = cand
                break
        yaw = _uniform(rng, e["yaw"]) % 360.0
        sink = _uniform(rng, e["sink"])
        at = (pos[0], pos[1] - sink, pos[2])
        r = _c.place(s, lib[e["template"]]["id"], list(at), yaw=yaw)
        rec = s.record(r["line"])
        normal = p.get("normal") if isinstance(p, dict) else None
        if lib[e["template"]]["kind"] == "prop":
            if e["conform"] and normal:
                rx, ry, rz = conform_rot(yaw, _vec(normal), e["max_tilt"])
            else:
                rx, ry, rz = 0.0, yaw, 0.0
            if e["tilt"]:
                rx += rng.uniform(-e["tilt"], e["tilt"])
                rz += rng.uniform(-e["tilt"], e["tilt"])
            rec["rot"] = (round(rx, 3), round(ry, 3), round(rz, 3))
            k = _uniform(rng, e["scale"])
            if k != 1.0 and "scale.x" in rec.fields:
                rec["scale"] = tuple(round(c * k, 4) for c in rec["scale"])
        keys.append(_key(rec))
        placed.append(e["label"])
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    lines = [_line_of(s, k) for k in keys]
    return {"lines": [l for l in lines if l], "placed": len(placed),
            "action": f"Paint {name or 'Palette'} ({len(placed)} object{'s' if len(placed) != 1 else ''})"}


# ======================================================================================== duplicate selection ====
@command("Duplicate Selection (Creation Kit style): `count` copies of the selected records, copy k moved by k x offset "
         "and turned by k x yaw_step degrees about the group's centre (arrays: fence lines, tent rows, a ring of "
         "sandbags). Names of the copies: search -> replace, then prefix + name + suffix (kept unique). Objects get new "
         "script ids, vehicles their own crews. One undo step.", mutates=True,
         lines="record lines", count="number of copies (1-200)", offset="[x, y, z] cm between copies",
         yaw_step="degrees each copy turns more", prefix="optional", suffix="optional", search="optional",
         replace="optional")
def duplicate_array(s: Session, lines, count=1, offset=(0, 0, 0), yaw_step=0.0, prefix="", suffix="", search="",
                    replace=""):
    _need_level(s)
    count = int(count)
    if not 1 <= count <= 200:
        raise CommandError("count: 1 - 200")
    offset = _vec(list(offset))
    yaw_step = float(yaw_step or 0.0)
    recs = [s.record(l) for l in _lines(lines)]
    for r in recs:
        if not _copyable(r):
            raise CommandError(f"line {r.index + 1}: {r.kind} records can't be duplicated (world pieces stay with their zone)")
    crew_ids = {cid for r in recs for _u, _seat, cid in _c._crew(r)}
    src = [r for r in recs if not (r.kind == "HLOI" and r["script_id"] in crew_ids)]
    centre = [sum(r["pos"][i] for r in src) / len(src) for i in range(3)]
    entries, plan = [], []
    for k in range(1, count + 1):
        turn = yaw_step * k
        base = [c + o * k for c, o in zip(centre, offset)]
        for r in src:
            off = _turn([a - b for a, b in zip(r["pos"], centre)], turn)
            crew = [dict(c, offset=list(_turn(c["offset"], turn))) for c in (_crew_texts(s.env, r) if r.kind == "HLOI" else [])]
            entries.append({"kind": r.kind, "text": r.text, "pos": [b + o for b, o in zip(base, off)], "crew": crew})
            plan.append((r, turn))
    new = _paste_entries(s, entries, (0.0, 0.0, 0.0), 0.0, anchor=(0.0, 0.0, 0.0))
    keys = []
    for line, (r_src, turn) in zip(new, plan):
        rec = s.record(line)
        if turn and rec.get("rot") is not None:
            rot = rec["rot"]
            rec["rot"] = (rot[0], round((rot[1] + turn) % 360.0, 4), rot[2])
        if rec.kind != "HLOI" and "name" in rec.fields and (prefix or suffix or search):
            old = str(r_src.get("name"))
            stem = old.replace(search, replace) if search else old
            rec["name"] = _unique(s, rec.kind, "name", f"{prefix}{stem}{suffix}")
        keys.append(_key(rec))
    lines_now = [_line_of(s, k) for k in keys]
    n = len(src)
    return {"lines": [l for l in lines_now if l], "copies": count, "records": len(new),
            "action": f"Duplicate {n} record{'s' if n != 1 else ''} x{count}"}


# =============================================================================================== paste in place ====
@command("Paste in Place (Ctrl+Shift+V): the clipboard's records where they were copied from - into another level or "
         "mission at the same coordinates. New script ids, unique names.", mutates=True)
def paste_in_place(s: Session):
    clip = _c._CLIPBOARD
    if not clip:
        raise CommandError("the clipboard is empty - copy something first (Ctrl+C)")
    return {"lines": _paste_entries(s, clip, _vec(clip[0]["pos"])),
            "action": f"Paste in Place ({len(clip)} record{'s' if len(clip) != 1 else ''})"}


# =========================================================================================== apply transform ====
@command("Apply Transform (Creation Kit style): move the records by an exact offset and turn them by yaw degrees - "
         "about the group's centre (around 'group', they keep their arrangement) or each about itself ('each').",
         mutates=True, lines="record lines", translate="[x, y, z] cm", yaw="degrees", around="group / each")
def transform_selection(s: Session, lines, translate=(0, 0, 0), yaw=0.0, around="group"):
    _need_level(s)
    if around not in ("group", "each"):
        raise CommandError("around: group or each")
    t = _vec(list(translate))
    yaw = float(yaw or 0.0)
    recs = [s.record(l) for l in _lines(lines)]
    keys = [_key(r) for r in recs]
    starts = [tuple(r["pos"]) for r in recs]
    centre = [sum(p[i] for p in starts) / len(starts) for i in range(3)]
    for key, p in zip(keys, starts):
        line = _line_of(s, key)
        if around == "group" and yaw:
            off = _turn([a - b for a, b in zip(p, centre)], yaw)
            target = [c + o + d for c, o, d in zip(centre, off, t)]
        else:
            target = [a + d for a, d in zip(p, t)]
        _c.move(s, line, target)
        r = s.record(_line_of(s, key))
        if yaw and r.get("rot") is not None:
            rot = r["rot"]
            r["rot"] = (rot[0], round((rot[1] + yaw) % 360.0, 4), rot[2])
    return {"lines": [_line_of(s, k) for k in keys],
            "action": f"Transform {len(keys)} record{'s' if len(keys) != 1 else ''}"}


# ============================================================================================ replace base ====
@command("Search & Replace base object (Creation Kit style): every prop / object of one type (find: model, or "
         "model + unit for objects) - in the selection, or the whole level - becomes the library template, where the old "
         "one stood (position, facing, size, zone, name; objects keep their script id, so scripts and layers still find "
         "them). Vehicles (crews) aren't swapped. One undo step.", mutates=True,
         template="library id of the new type", find="model name to replace (optional with lines)",
         unit="optional: object unit to match", lines="optional: only these records", force="also DLL-critical objects")
def replace_base(s: Session, template, find=None, unit=None, lines=None, force=False):
    _need_level(s)
    lib = _library(s)
    if not 0 <= int(template) < len(lib):
        raise CommandError("unknown template")
    new_e = lib[int(template)]
    keyword = new_e["keyword"]
    if keyword == "HLOI" and new_e.get("crew"):
        raise CommandError(f"{new_e['label']} is a vehicle - vehicles bring crews; place it instead")
    want = str(find or "").upper()
    wunit = str(unit or "").upper()
    if lines:
        cands = [s.record(l) for l in _lines(lines)]
    else:
        if not want:
            raise CommandError("give find (a model) or lines")
        cands = list(s.env.find(keyword))
    targets = []
    for r in cands:
        if not r.fields or r.kind != keyword:
            continue
        if keyword == "ENVI" and (not r.file.is_prop(r) or r.children or _chunk(s, r)):
            continue
        if want and str(r.get("model")).upper() != want:
            continue
        if wunit and str(r.get("unit")).upper() != wunit:
            continue
        if keyword == "HLOI" and _is_vehicle(r):
            continue
        targets.append(r)
    if not targets:
        raise CommandError(f"nothing to replace ({keyword} records matching {find or 'the selection'})")
    critical = logic.base_targets(s) if keyword == "HLOI" else {}
    keep_hlo = ("pos", "rot", "zone", "script_id", "in_play")
    keep_env = ("pos", "rot", "scale", "name", "parent")
    keys = []
    for r in sorted(targets, key=lambda x: -x.index):     # bottom up: lines above don't move
        if keyword == "HLOI" and r["script_id"] in critical and not force:
            raise CommandError(f"object #{r['script_id']} is DLL critical - the base mission addresses it directly "
                               "(force to swap it anyway)")
        kept = {}
        for k in keep_hlo if keyword == "HLOI" else keep_env:
            try:
                kept[k] = r[k]
            except (KeyError, ValueError):
                pass
        at = r.index
        s.env.lines[at] = new_e["text"]
        s.env._parse()
        rec = s.env.record_at(at + 1)
        _drop_rinfo_flag(rec)
        for k, v in kept.items():
            try:
                rec[k] = v
            except (KeyError, ValueError):
                pass
        keys.append(_key(rec))
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return {"replaced": len(keys), "lines": [_line_of(s, k) for k in keys],
            "action": f"Replace {len(keys)} x {find or 'selection'} with {new_e['label']}"}


# =============================================================================================== path test ====
# The game's router (FUN_00509d00 in DesertStorm.exe) runs A* over the route markers (<level>.RMD, links directed,
# up to 5 per marker) and answers 0 found / 1 closest reachable / 2 refused. The test does the same on the editor's
# routes, so a mission maker sees whether soldiers can get from A to B with the markers as they are now.
@command("Pathing test (Creation Kit 'Normal Pathing Test'): the AI route from `start` to `goal` over the level's route "
         "markers - nearest marker to each point, links followed in their direction, shortest by distance (A*, like "
         "the game's router). Returns the markers, their positions, the length, and when there is no way the closest "
         "marker it gets to.", start="[x, y, z]", goal="[x, y, z]")
def path_test(s: Session, start, goal):
    _need_level(s)
    if not s.routes or not s.routes.markers:
        raise CommandError("this level has no route markers")
    ms = s.routes.markers
    a = s.routes.nearest(_vec(start))[0]
    b = s.routes.nearest(_vec(goal))[0]
    pos = [m.pos for m in ms]
    h = lambda i: math.dist(pos[i], pos[b])   # noqa: E731
    best, came, done = {a: 0.0}, {}, set()
    heap = [(h(a), 0.0, a)]
    closest = a
    expanded = 0
    while heap:
        _, g, i = heapq.heappop(heap)
        if i in done:
            continue
        done.add(i)
        expanded += 1
        if h(i) < h(closest):
            closest = i
        if i == b:
            break
        for j in ms[i].links:
            if j is None or j < 0 or j >= len(ms) or j == 0xFFFF:
                continue
            ng = g + math.dist(pos[i], pos[j])
            if ng < best.get(j, math.inf):
                best[j], came[j] = ng, i
                heapq.heappush(heap, (ng + h(j), ng, j))
    found = b in done
    end = b if found else closest
    route = [end]
    while route[-1] != a:
        route.append(came[route[-1]])
    route.reverse()
    length = sum(math.dist(pos[x], pos[y]) for x, y in zip(route, route[1:]))
    return {"found": found, "result": "found" if found else "closest", "start_marker": a, "goal_marker": b,
            "markers": route, "points": [[round(c, 1) for c in pos[i]] for i in route], "length_cm": round(length, 1),
            "searched": expanded, "start_gap_cm": round(math.dist(_vec(start), pos[a]), 1),
            "goal_gap_cm": round(math.dist(_vec(goal), pos[b]), 1),
            "unreached_cm": 0.0 if found else round(math.dist(pos[closest], pos[b]), 1)}


# ===================================================================================== placements CSV export ====
_CSV_COLS = ["key", "kind", "label", "model", "x", "y", "z", "rot_x", "rot_y", "rot_z", "scale"]


def _placement_row(r):
    key = f"#{r['script_id']}" if r.kind == "HLOI" else (f"wp{r['id']}" if r.kind == "NEWENVWAYPT" else str(r.get("name")))
    rot = r.get("rot") or (0.0, 0.0, 0.0)
    sc = r.get("scale") if "scale.x" in r.fields else None
    return {"key": key, "kind": r.kind, "label": str(r.get("unit") or r.get("name") or ""), "model": str(r.get("model") or ""),
            "x": r["pos"][0], "y": r["pos"][1], "z": r["pos"][2], "rot_x": rot[0], "rot_y": rot[1], "rot_z": rot[2],
            "scale": sc[0] if sc else ""}


@command("Export placements (Creation Kit 'Ref Placements for Selection'): position, rotation and size of the given "
         "records - or every prop and object - as CSV text (key = #script id for objects, the name for others). Edit it "
         "in a spreadsheet or a script and bring it back with import_placements.", lines="optional record lines")
def export_placements(s: Session, lines=None):
    _need_level(s)
    recs = [s.record(l) for l in _lines(lines)] if lines else \
        [r for r in s.env.records if r.fields and r.kind in ("HLOI", "ENVI") and _copyable(r)]
    buf = io.StringIO()
    w = csv.DictWriter(buf, _CSV_COLS, lineterminator="\n")
    w.writeheader()
    for r in recs:
        if "pos" in r.fields or "pos.x" in r.fields:
            w.writerow(_placement_row(r))
    return {"csv": buf.getvalue(), "rows": len(recs)}


@command("Import placements: CSV with the export_placements columns (key, kind, x, y, z, rot_x, rot_y, rot_z, scale - "
         "others ignored); each row moves / turns / sizes the record with that key. Unknown keys are listed, nothing "
         "else changes. One undo step.", mutates=True, csv_text="the CSV")
def import_placements(s: Session, csv_text):
    _need_level(s)
    rows = list(csv.DictReader(io.StringIO(str(csv_text))))
    if not rows:
        raise CommandError("no rows")
    index = {}
    for r in s.env.records:
        if r.fields and r.kind in _PASTE_END and _copyable(r):
            index[(r.kind, _placement_row(r)["key"])] = _key(r)
    done, missing = 0, []
    for row in rows:
        k = (row.get("kind", "").strip(), row.get("key", "").strip())
        if k not in index:
            missing.append(k[1])
            continue
        line = _line_of(s, index[k])
        try:
            pos = [float(row["x"]), float(row["y"]), float(row["z"])]
        except (KeyError, ValueError):
            raise CommandError(f"row {k[1]}: x, y, z must be numbers") from None
        rec = s.record(line)
        if [round(c, 3) for c in rec["pos"]] != [round(c, 3) for c in pos]:
            _c.move(s, line, pos)
        rec = s.record(_line_of(s, index[k]))
        if row.get("rot_y", "") != "" and rec.get("rot") is not None:
            rec["rot"] = (float(row.get("rot_x") or 0), float(row["rot_y"]), float(row.get("rot_z") or 0))
        if row.get("scale", "") not in ("", None) and "scale.x" in rec.fields:
            f = float(row["scale"])
            if not 0.1 <= f <= 10:
                raise CommandError(f"row {k[1]}: scale 0.1 - 10")
            old = rec["scale"]
            if old[0]:
                rec["scale"] = tuple(round(c * f / old[0], 4) for c in old)
        done += 1
    return {"updated": done, "missing": missing[:50], "action": f"Import Placements ({done} records)"}


# ================================================================================ per-mission world-tools state ====
# Kept in s.worldtools (a logic.PRODUCTION_KEYS entry): undo snapshots and <NAME>.editor.json carry it.
#   notes:   [{id, pos, text, colour}]                                 editor-only, never in the game
#   prefabs: {instance id: {name, pos, yaw, keys: [[kind, value]]}}     records stamped from a linked prefab
#   spawns:  {list name: {pos, yaw, variants: [{label, template, weight, layer, keys}], empty_weight}}
#   cameras: {path name: {shots: [{camera, seconds}], trigger, letterbox, control}}
def _wt(s: Session) -> dict:
    d = getattr(s, "worldtools", None)
    if not isinstance(d, dict):
        d = {}
        s.worldtools = d
    d.setdefault("notes", [])
    d.setdefault("prefabs", {})
    d.setdefault("spawns", {})
    d.setdefault("cameras", {})
    d.setdefault("doors", {})
    return d


# ================================================================================================ editor notes ====
_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")


@command("Editor notes: sticky notes placed in the world for yourself or a team ('fix this cliff gap') - saved with the "
         "mission, never sent to the game.")
def notes_list(s: Session):
    return {"notes": _wt(s)["notes"]}


@command("Add an editor note at a position.", mutates=True, pos="[x, y, z]", text="the note", colour="optional #rrggbb")
def note_add(s: Session, pos, text, colour="#ffd23f"):
    _need_level(s)
    text = str(text or "").strip()
    if not text:
        raise CommandError("the note is empty")
    w = _wt(s)
    nid = max([n["id"] for n in w["notes"]] + [0]) + 1
    w["notes"] = w["notes"] + [{"id": nid, "pos": [round(c, 2) for c in _vec(pos)], "text": text[:500],
                                "colour": colour if _COLOUR.fullmatch(str(colour)) else "#ffd23f"}]
    return {"id": nid, "action": f"Add Note \"{text[:30]}\""}


@command("Change an editor note (text, position, colour - the parts given).", mutates=True, id="note id", text="optional",
         pos="optional [x, y, z]", colour="optional #rrggbb")
def note_set(s: Session, id, text=None, pos=None, colour=None):
    w = _wt(s)
    notes = [dict(n) for n in w["notes"]]
    n = next((x for x in notes if x["id"] == int(id)), None)
    if n is None:
        raise CommandError(f"no note {id}")
    if text is not None:
        n["text"] = str(text).strip()[:500]
    if pos is not None:
        n["pos"] = [round(c, 2) for c in _vec(pos)]
    if colour is not None and _COLOUR.fullmatch(str(colour)):
        n["colour"] = colour
    w["notes"] = notes
    return {"note": n, "action": "Edit Note"}


@command("Delete an editor note.", mutates=True, id="note id")
def note_delete(s: Session, id):
    w = _wt(s)
    left = [n for n in w["notes"] if n["id"] != int(id)]
    if len(left) == len(w["notes"]):
        raise CommandError(f"no note {id}")
    w["notes"] = left
    return {"deleted": int(id), "action": "Delete Note"}


# =============================================================================================== record keys ====
def _safe_key(r):
    try:
        return _key(r)
    except (KeyError, ValueError):
        return None


def _all_keys(s: Session):
    return {k for k in (_safe_key(r) for r in s.env.records if r.fields and r.kind in _PASTE_END) if k is not None}


def _lines_of_keys(s: Session, keys):
    want = {tuple(k) for k in keys}
    return [r.index + 1 for r in s.env.records if r.fields and r.kind in _PASTE_END and _safe_key(r) in want]


def _delete_keys(s: Session, keys):
    for line in sorted(_lines_of_keys(s, keys), reverse=True):
        _c.delete(s, line, force=True)


# ============================================================================================ linked prefabs ====
# Every stamp of a prefab is remembered as an instance (the records it added, where, turned how). An edited copy can
# become the prefab (prefab_apply_instance) and every other instance is re-stamped in place - the Creation Kit's
# Pack-in "Edit in Render Window" idea.
import studio as _studio  # noqa: E402

_plain_paste_prefab = getattr(_studio, "_plain_paste_prefab", None) or _studio.paste_prefab
_studio._plain_paste_prefab = _plain_paste_prefab


def _stamp(s: Session, name, pos, yaw):
    before = _all_keys(s)
    r = _plain_paste_prefab(s, name, pos, yaw)
    keys = sorted(_all_keys(s) - before, key=str)
    return r, [list(k) for k in keys]


def paste_prefab(s: Session, name, pos, yaw=0.0):
    r, keys = _stamp(s, name, pos, yaw)
    w = _wt(s)
    iid = max([int(k) for k in w["prefabs"]] + [0]) + 1
    w["prefabs"] = {**w["prefabs"], str(iid): {"name": r.get("name", name), "pos": list(_vec(pos)),
                                                "yaw": float(yaw or 0.0), "keys": keys}}
    return {**r, "instance": iid}


_desc, _args, _mut = _c.COMMANDS["paste_prefab"][1:]
if "linked instance" not in _desc:
    _desc += " The stamp is remembered as a linked instance (prefab_instances / prefab_apply_instance)."
_c.COMMANDS["paste_prefab"] = (paste_prefab, _desc, _args, _mut)


@command("Linked prefab instances in the open level / mission: id, prefab, position, turn, the lines of its records "
         "still there.", name="optional prefab name")
def prefab_instances(s: Session, name=None):
    _need_level(s)
    now = _all_keys(s)
    out = []
    for iid, inst in sorted(_wt(s)["prefabs"].items(), key=lambda kv: int(kv[0])):
        if name and inst["name"] != name:
            continue
        alive = [k for k in inst["keys"] if tuple(k) in now]
        out.append({"id": int(iid), "name": inst["name"], "pos": inst["pos"], "yaw": inst["yaw"],
                    "records": len(alive), "lines": _lines_of_keys(s, alive)})
    return {"instances": out}


@command("Which linked prefab instance a record belongs to ({id: null} when none).", line="record line")
def prefab_instance_of(s: Session, line):
    k = list(_key(s.record(line)))
    for iid, inst in _wt(s)["prefabs"].items():
        if k in inst["keys"]:
            return {"id": int(iid), "name": inst["name"]}
    return {"id": None}


@command("Re-stamp linked instances of a prefab from its saved version (all of them, or the ones given): each instance's "
         "records are removed and the prefab stamped again at the same spot and turn. One undo step.", mutates=True,
         name="prefab name", instances="optional instance ids")
def prefab_update(s: Session, name, instances=None):
    _need_level(s)
    w = _wt(s)
    ids = {str(i) for i in instances} if instances else {k for k, v in w["prefabs"].items() if v["name"] == name}
    if not ids:
        raise CommandError(f"no linked instances of {name!r} here")
    prefabs = dict(w["prefabs"])
    done = 0
    for iid in sorted(ids, key=int):
        inst = prefabs.get(iid)
        if not inst:
            continue
        _delete_keys(s, inst["keys"])
        _r, keys = _stamp(s, inst["name"], inst["pos"], inst["yaw"])
        prefabs[iid] = {**inst, "keys": keys}
        done += 1
    w["prefabs"] = prefabs
    return {"updated": done, "action": f"Update {done} x {name}"}


@command("Make an edited linked instance the prefab's new version (its records as they are now; extra_lines adds more) "
         "and re-stamp every other instance of it to match. One undo step.", mutates=True,
         instance="instance id", extra_lines="optional: more record lines to add to the instance first")
def prefab_apply_instance(s: Session, instance, extra_lines=None):
    _need_level(s)
    w = _wt(s)
    inst = w["prefabs"].get(str(instance))
    if not inst:
        raise CommandError(f"no linked instance {instance}")
    now = _all_keys(s)
    keys = [list(k) for k in inst["keys"] if tuple(k) in now]
    for line in _lines(extra_lines) if extra_lines else []:
        k = list(_key(s.record(line)))
        if k not in keys:
            keys.append(k)
    lines = _lines_of_keys(s, keys)
    if not lines:
        raise CommandError("the instance has no records left")
    # saved unturned about the instance's own spot, so the other instances keep their spot and turn
    yaw, anchor = inst["yaw"], inst["pos"]
    snap = s._snapshot()
    try:
        for line in lines:
            r = s.record(line)
            off = _turn([a - b for a, b in zip(r["pos"], anchor)], -yaw)
            r["pos"] = tuple(b + o for b, o in zip(anchor, off))
            if yaw and r.get("rot") is not None:
                rot = r["rot"]
                r["rot"] = (rot[0], round((rot[1] - yaw) % 360.0, 4), rot[2])
        _studio.save_prefab(s, inst["name"], lines)
    finally:
        s._restore(snap)
    path = _studio._prefab_path(s, inst["name"])
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    doc["anchor"] = list(anchor)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    w = _wt(s)
    w["prefabs"] = {**w["prefabs"], str(instance): {**inst, "keys": keys}}
    others = [k for k, v in w["prefabs"].items() if v["name"] == inst["name"] and k != str(instance)]
    n = prefab_update(s, inst["name"], others)["updated"] if others else 0
    return {"saved": inst["name"], "updated": n,
            "action": f"Apply {inst['name']} to {n} other instance{'s' if n != 1 else ''}"}


@command("Unlink a prefab instance (its records stay; later prefab updates leave them alone).", mutates=True,
         instance="instance id")
def prefab_unlink(s: Session, instance):
    w = _wt(s)
    if str(instance) not in w["prefabs"]:
        raise CommandError(f"no linked instance {instance}")
    w["prefabs"] = {k: v for k, v in w["prefabs"].items() if k != str(instance)}
    return {"unlinked": int(instance), "action": "Unlink Prefab Instance"}


# =============================================================================================== spawn lists ====
# The Creation Kit's leveled lists with what the game has: each variant is a real soldier record in its own layer
# (starts hidden: waits for game.show_layer); a 'mission start' rule shows one by weight - or none - each playthrough.
_SPAWN_RULE = "Spawn list: "


# difficulty names in the menu's order -> the game's level (game.difficulty(): 0 easy, 1 medium, 2 hard, 3 very hard,
# 4 very easy - difficulty.cpp)
DIFFICULTIES = (("very_easy", 4), ("easy", 0), ("medium", 1), ("hard", 2), ("very_hard", 3))


def _by_difficulty(w, default):
    """Optional per-difficulty weights: {name: weight} or a list in menu order -> {game level: weight}."""
    if not w:
        return None
    if isinstance(w, (list, tuple)):
        w = {name: v for (name, _), v in zip(DIFFICULTIES, w)}
    out = {}
    for name, level in DIFFICULTIES:
        v = w.get(name, default) if isinstance(w, dict) else default
        v = float(default if v in (None, "") else v)
        if v < 0:
            raise CommandError(f"{name.replace('_', ' ')} weight: 0 or more")
        out[level] = v
    return out


def _lua_weights(base, by):
    if not by:
        return f"{float(base)}"
    by = {int(k): float(v) for k, v in by.items()}
    return "{" + ", ".join(f"[{lv}] = {by[lv]}" for lv in sorted(by)) + "}"


def _spawn_rule(name, sp):
    rows = ", ".join(f"{{{json.dumps(v['layer'])}, {_lua_weights(v['weight'], v.get('by_difficulty'))}}}"
                     for v in sp["variants"])
    code = "\n".join([
        f"local list = {{{rows}}}",
        f"local nobody = {_lua_weights(sp.get('empty_weight') or 0, sp.get('empty_by_difficulty'))}",
        "local level = game.difficulty and game.difficulty() or 1",
        "local function w(x) if type(x) == 'table' then return x[level] or 0 end return x end",
        "local total = w(nobody)",
        "for _, v in ipairs(list) do total = total + w(v[2]) end",
        "local pick = math.random() * total",
        "for _, v in ipairs(list) do",
        "  pick = pick - w(v[2])",
        "  if w(v[2]) > 0 and pick <= 0 then game.show_layer(v[1]) break end",
        "end"])
    return {"name": _SPAWN_RULE + name, "enabled": True, "once": True, "trigger": {"type": "mission_start"},
            "conditions": [], "actions": [{"type": "lua", "code": code}]}


def _put_rule(s: Session, rule):
    rules = [r for r in getattr(s, "rules", []) if r.get("name") != rule["name"]]
    if rule.get("actions"):
        rules.append(logic._check_rule(s, rule, len(rules)))
    s.rules = rules


@command("Random spawn list (Creation Kit leveled-list idea): soldier / item types with weights at one spot. Each variant "
         "is placed there in its own hidden layer and a 'mission start' rule shows one of them at random each "
         "playthrough (empty_weight = the chance nobody is there). Replaces a list of the same name. One undo step.",
         mutates=True, name="list name (letters, digits, _)", pos="[x, y, z]", yaw="facing (degrees)",
         variants="[{template: library id, weight, by_difficulty: optional {very_easy, easy, medium, hard, very_hard} or "
                  "5 weights in that order}]", empty_weight="optional weight for 'nobody'",
         empty_by_difficulty="optional per-difficulty weights for 'nobody'")
def spawn_list_set(s: Session, name, pos, variants, yaw=0.0, empty_weight=0.0, empty_by_difficulty=None):
    _need_level(s)
    if not re.fullmatch(r"[A-Za-z0-9_]{1,24}", str(name or "")):
        raise CommandError("name: letters, digits and _ (up to 24)")
    if not isinstance(variants, list) or not variants:
        raise CommandError("variants: at least one {template, weight}")
    lib = _library(s)
    w = _wt(s)
    old = w["spawns"].get(name)
    if old:
        _delete_keys(s, [k for v in old["variants"] for k in v["keys"]])
    out = []
    for i, v in enumerate(variants, 1):
        t = int(v.get("template", -1))
        if not 0 <= t < len(lib) or lib[t]["kind"] != "object":
            raise CommandError(f"variant {i}: an object (soldier) template from the library")
        if lib[t].get("crew"):
            raise CommandError(f"variant {i}: {lib[t]['label']} is a vehicle - spawn lists hold soldiers and items")
        weight = float(v.get("weight", 1))
        if weight <= 0:
            raise CommandError(f"variant {i}: weight must be above 0")
        before = _all_keys(s)
        r = _c.place(s, t, list(_vec(pos)), yaw=float(yaw or 0.0))
        layer = f"spawn_{name}_{i}"
        _c.set_layer(s, lines=[r["line"]], layer=layer)
        s.layer_start[layer] = "hidden"
        by = _by_difficulty(v.get("by_difficulty"), weight)
        out.append({"label": lib[t]["label"], "template": t, "weight": weight, "layer": layer,
                    **({"by_difficulty": by} if by else {}),
                    "keys": [list(k) for k in sorted(_all_keys(s) - before, key=str)]})
    sp = {"pos": list(_vec(pos)), "yaw": float(yaw or 0.0), "variants": out, "empty_weight": float(empty_weight or 0)}
    eby = _by_difficulty(empty_by_difficulty, float(empty_weight or 0))
    if eby:
        sp["empty_by_difficulty"] = eby
    w["spawns"] = {**w["spawns"], name: sp}
    _put_rule(s, _spawn_rule(name, sp))
    return {"name": name, "variants": len(out), "lines": _lines_of_keys(s, [k for v in out for k in v["keys"]]),
            "action": f"Spawn List {name} ({len(out)} variants)"}


@command("The random spawn lists of the open mission (name, spot, variants with weights and layers).")
def spawn_lists(s: Session):
    return {"lists": [{"name": k, "pos": v["pos"], "yaw": v["yaw"], "empty_weight": v.get("empty_weight", 0),
                       "empty_by_difficulty": v.get("empty_by_difficulty"),
                       "variants": [{**{x: e[x] for x in ("label", "template", "weight", "layer")},
                                     "by_difficulty": e.get("by_difficulty")} for e in v["variants"]]}
                      for k, v in sorted(_wt(s)["spawns"].items())]}


@command("Delete a random spawn list: its soldiers, layers and rule.", mutates=True, name="list name")
def spawn_list_delete(s: Session, name):
    w = _wt(s)
    sp = w["spawns"].get(name)
    if not sp:
        raise CommandError(f"no spawn list {name!r}")
    _delete_keys(s, [k for v in sp["variants"] for k in v["keys"]])
    for v in sp["variants"]:
        s.layer_start.pop(v["layer"], None)
    w["spawns"] = {k: v for k, v in w["spawns"].items() if k != name}
    _put_rule(s, {"name": _SPAWN_RULE + name, "actions": []})
    return {"deleted": name, "action": f"Delete Spawn List {name}"}


# ============================================================================================== camera paths ====
# The Creation Kit's Camera Paths: an ordered shot list played as a cutscene, compiled into one event rule (cutscene
# start, camera shots with their hold times, cutscene end) - the game's own cutscene calls do the rest.
_CAM_RULE = "Camera path: "


@command("Camera path (Creation Kit style): scene cameras played in order as a cutscene, each held for some seconds, "
         "started by a trigger ({type: mission_start} / after_time {seconds} / region_enter {region, who}) - compiled "
         "into an event rule. Replaces a path of the same name. Cameras by name, or the scene's camera number "
         "(0 = PLAYERVIEW).",
         mutates=True, name="path name", shots="[{camera: name or number, seconds}]", trigger="{type, ...} (default mission_start)",
         letterbox="letterbox bars (default true)", control="player keeps control (default false)")
def camera_path_set(s: Session, name, shots, trigger=None, letterbox=True, control=False):
    _need_level(s)
    name = str(name or "").strip()
    if not name:
        raise CommandError("name the camera path")
    if not isinstance(shots, list) or not shots:
        raise CommandError("shots: at least one {camera, seconds}")
    names = [str(r["name"]) for r in s.env.records if r.kind.startswith("CAM") and r.fields]
    clean = []
    for i, sh in enumerate(shots, 1):
        cam, sec = sh.get("camera"), float(sh.get("seconds", 4))
        if isinstance(cam, (int, float)) and not isinstance(cam, bool):
            if not 0 <= int(cam) < len(names):
                raise CommandError(f"shot {i}: camera 0 - {len(names) - 1}")
            cam = names[int(cam)]
        if str(cam) not in names:
            raise CommandError(f"shot {i}: no camera {cam!r} in this scene")
        cam = str(cam)
        if not 0.2 <= sec <= 120:
            raise CommandError(f"shot {i}: 0.2 - 120 seconds")
        clean.append({"camera": cam, "seconds": sec})
    trig = dict(trigger or {"type": "mission_start"})
    actions = [{"type": "cutscene_start", "letterbox": bool(letterbox), "viewfinder": False, "control": bool(control)}]
    actions += [{"type": "camera_shot", "camera": c["camera"], "seconds": c["seconds"]} for c in clean]
    actions.append({"type": "cutscene_end"})
    _put_rule(s, {"name": _CAM_RULE + name, "enabled": True, "once": True, "trigger": trig, "conditions": [],
                  "actions": actions})
    w = _wt(s)
    w["cameras"] = {**w["cameras"], name: {"shots": clean, "trigger": trig, "letterbox": bool(letterbox),
                                            "control": bool(control)}}
    return {"name": name, "shots": len(clean), "seconds": sum(c["seconds"] for c in clean),
            "action": f"Camera Path {name} ({len(clean)} shots)"}


@command("The camera paths of the open mission.")
def camera_paths(s: Session):
    return {"paths": [{"name": k, **v} for k, v in sorted(_wt(s)["cameras"].items())]}


@command("Delete a camera path and its rule.", mutates=True, name="path name")
def camera_path_delete(s: Session, name):
    w = _wt(s)
    if name not in w["cameras"]:
        raise CommandError(f"no camera path {name!r}")
    w["cameras"] = {k: v for k, v in w["cameras"].items() if k != name}
    _put_rule(s, {"name": _CAM_RULE + name, "actions": []})
    return {"deleted": name, "action": f"Delete Camera Path {name}"}


# ============================================================================================ route islands ====
@command("Route islands (Creation Kit 'Check NavMeshes' idea): from a start point (default: the squad's first soldier), "
         "which route markers the AI reaches following links in their direction, which it can't reach at all, and "
         "'traps' - reachable, but with no way back to the start.", start="optional [x, y, z]", limit="max markers listed")
def route_islands(s: Session, start=None, limit=3000):
    _need_level(s)
    if not s.routes or not s.routes.markers:
        raise CommandError("this level has no route markers")
    ms = s.routes.markers
    if start is None:
        squad = [r for r in s.env.find("HLOI") if r.fields and str(r.get("model")).upper().startswith("HERO")]
        if not squad:
            raise CommandError("no squad soldier in the scene - give a start point")
        start = squad[0]["pos"]
    a = s.routes.nearest(_vec(start))[0]
    n = len(ms)
    fwd = [[j for j in m.links if j is not None and 0 <= j < n] for m in ms]
    back = [[] for _ in range(n)]
    for i, js in enumerate(fwd):
        for j in js:
            back[j].append(i)

    def reach(graph):
        seen, stack = {a}, [a]
        while stack:
            for j in graph[stack.pop()]:
                if j not in seen:
                    seen.add(j)
                    stack.append(j)
        return seen
    there, home = reach(fwd), reach(back)
    unreachable = [i for i in range(n) if i not in there]
    traps = sorted(i for i in there if i not in home)

    def pts(idx):
        return [{"index": i, "pos": [round(c, 1) for c in ms[i].pos]} for i in idx[:int(limit)]]
    return {"start_marker": a, "markers": n, "reachable": len(there), "unreachable": pts(unreachable),
            "unreachable_count": len(unreachable), "traps": pts(traps), "trap_count": len(traps)}


# ================================================================================================ use report ====
@command("Use report (Creation Kit 'Use Info' for a base object): every prop, world chunk and object in the open scene "
         "that uses a model (objects: optionally of one unit type), with lines - select them all, then replace or move "
         "them.", model="model name", unit="optional object unit")
def model_use(s: Session, model, unit=None):
    _need_level(s)
    m, u = str(model or "").upper(), str(unit or "").upper()
    if not m:
        raise CommandError("give a model")
    hits = []
    for r in s.env.records:
        if not r.fields or r.kind not in ("ENVI", "HLOI") or str(r.get("model")).upper() != m:
            continue
        if u and str(r.get("unit")).upper() != u:
            continue
        if r.kind == "HLOI":
            kind = "object"
        elif _chunk(s, r):
            kind = "chunk"
        elif not r.file.is_prop(r) or r.children:
            kind = "zone"
        else:
            kind = "prop"
        hits.append({"line": r.index + 1, "kind": kind, "name": str(r.get("unit") or r.get("name")),
                     "pos": [round(c, 1) for c in r["pos"]]})
    by = {}
    for h in hits:
        by[h["kind"]] = by.get(h["kind"], 0) + 1
    return {"model": model, "count": len(hits), "by_kind": by, "uses": hits}


# ========================================================================================= check every level ====
@command("Check every level (and every saved mission in the Mods folder) like check_mission, one row each: errors, "
         "warnings and the first problems - the Creation Kit's 'Test All Cells' idea. Each opens in its own session (the "
         "open one is left alone). Takes a while.", missions="also the saved missions (default true)",
         limit="problems listed per level")
def check_all_levels(s: Session, missions=True, limit=5):
    import workflow
    jobs = [("level", lv, None) for lv in _c.list_levels(s)]
    if missions and s.game_dir:
        jobs += [("mission", f"{m['mod']}/{m['name']}", m) for m in _c.list_missions(s)]
    rows = []
    for kind, label, m in jobs:
        t = time.time()
        try:
            other = Session(s.data_dir, s.game_dir)
            if m:
                _c.open_mission(other, m["mod"], m["name"])
            else:
                _c.open_level(other, label)
            r = workflow.check_mission(other, limit=int(limit))
            rows.append({"kind": kind, "name": label, "errors": r.get("errors", 0), "warnings": r.get("warnings", 0),
                         "issues": [f"{i['severity']}: {i['message']}" for i in r.get("issues", [])][:int(limit)],
                         "seconds": round(time.time() - t, 1)})
        except Exception as ex:  # noqa: BLE001 - one broken level is a result, not a failure
            rows.append({"kind": kind, "name": label, "errors": 1, "warnings": 0, "issues": [f"can't open: {ex}"],
                         "seconds": round(time.time() - t, 1)})
    return {"checked": len(rows), "with_errors": sum(1 for r in rows if r["errors"]), "rows": rows}


# ============================================================================================ export files ====
# The editor runs as ConflictEditor.exe (a WebView2 window), not a browser tab: exports are written here by the
# server instead of downloaded by the page.
def export_dir():
    d = os.path.join(os.path.expanduser("~"), "Documents", "Conflict Editor Exports")
    os.makedirs(d, exist_ok=True)
    return d


@command("Write an export file (World Tools: placements CSV, heightmap PNG) into Documents\\Conflict Editor Exports; "
         "returns its path. An existing file of that name gets a number.", name="file name", text="text content",
         data_base64="or binary content, base64")
def save_export(s: Session, name, text=None, data_base64=None):
    import base64
    base = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", os.path.basename(str(name or "")))[:120]
    if not base or base.startswith("."):
        raise CommandError("file name")
    stem, ext = os.path.splitext(base)
    if ext.lower() not in (".csv", ".png", ".txt", ".json"):
        raise CommandError("exports: .csv, .png, .txt or .json")
    path, n = os.path.join(export_dir(), base), 2
    while os.path.exists(path):
        path, n = os.path.join(export_dir(), f"{stem} ({n}){ext}"), n + 1
    if data_base64 is not None:
        data = base64.b64decode(str(data_base64).split(",")[-1])
    else:
        data = str(text or "").encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path, "bytes": len(data)}


@command("Open the exports folder (Documents\\Conflict Editor Exports) in Explorer.")
def open_export_folder(s: Session):
    d = export_dir()
    os.startfile(d)  # noqa: S606 - Windows-only editor app
    return {"path": d}


# ============================================================================================ collision view ====
# The Creation Kit's "Collision Geometry" view (F4): what the game collides with, coloured by surface. Each model's
# u = 0 BSP trees (the ones the game loads, conflict_ds_tools/bsp.py) are read once, put into model space by their
# object's world matrix, and placed by the record exactly as the viewport places the model (app.js placeProp: game
# (x, y, z) cm -> three (x, -z) m; Euler (-rx, -ry, rz) order YXZ; scale) - so the triangles come back in viewport
# space and the page only has to draw them.
from conflict_ds_tools import bsp as _bsp, evo as _evo  # noqa: E402

_COLLISION = {}          # (data dir, model) -> {object index: (polys [(surface, [three pts])], centre, radius)}


def _surface_class(i):
    name = _bsp.SURFACES[i] if 0 <= i < len(_bsp.SURFACES) else ""
    if name.endswith("Barrier"):
        return "barrier"
    if name == "Render Only":
        return "render"
    if name.startswith("Blocking"):
        return "invisible" if name == "Blocking Invisible" else "blocking"
    return "walk"


_FINDERS = {}


def _finder(s: Session):
    """One AssetFinder per extracted folder (building one lists every extracted folder - slow)."""
    key = (s.data_dir, s.folder)
    if key not in _FINDERS:
        _FINDERS[key] = _evo.AssetFinder(_c._find_file(s.folder, s.level + ".ENV"))
    return _FINDERS[key]


def _bounds(pts):
    lo = [min(q[i] for q in pts) for i in range(3)]
    hi = [max(q[i] for q in pts) for i in range(3)]
    return [(a + b) / 2 for a, b in zip(lo, hi)], math.dist(lo, hi) / 2


def _model_collision(s: Session, model, index=None):
    """(polys, centre, radius) of a model's collision in model space (three axes, m) - one object when index is given."""
    key = (s.data_dir, str(model).upper())
    if key not in _COLLISION:
        per = {}
        try:
            path = _finder(s).find(str(model).upper(), (".evo",))
            scene = _evo.Scene(_evo.read(path)) if path else None
        except Exception:  # noqa: BLE001 - a model the reader can't take simply shows no collision
            scene = None
        for o in scene.objects if scene else []:
            polys = []
            for u, tree in o.node.trees:
                if u != 0:
                    continue
                try:
                    root = _bsp.read(tree)
                except Exception:  # noqa: BLE001
                    continue
                for p in _bsp.polygons(root) if root else []:
                    pts = []
                    for v in p.verts:
                        x, y, z = _evo.mat_apply(o.world, v)
                        pts.append((x / 100.0, y / 100.0, -z / 100.0))
                    polys.append((p.surface & 0x3FF, pts))
            if polys:
                per[o.index] = (polys, *_bounds([q for _, pts in polys for q in pts]))
        _COLLISION[key] = per
    per = _COLLISION[key]
    if index is not None:
        return per.get(index, ([], [0.0, 0.0, 0.0], 0.0))
    polys = [p for v in per.values() for p in v[0]]
    if not polys:
        return [], [0.0, 0.0, 0.0], 0.0
    if (key, "all") not in _COLLISION:
        _COLLISION[(key, "all")] = (polys, *_bounds([q for _, pts in polys for q in pts]))
    return _COLLISION[(key, "all")]


def _record_matrix(pos, rot, scale):
    """3x4 viewport matrix of a record (three space, metres) - the same as app.js placeProp."""
    x, y, z = -math.radians(rot[0]), -math.radians(rot[1]), math.radians(rot[2])
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    r = _mul(_mul(ry, rx), rz)
    k = [float(c) for c in (scale or (1, 1, 1))]
    t = (pos[0] / 100.0, pos[1] / 100.0, -pos[2] / 100.0)
    return [[r[i][j] * k[j] for j in range(3)] + [t[i]] for i in range(3)]


def _apply(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])


@command("Collision view (Creation Kit 'Collision Geometry'): the collision surfaces the game uses (each model's BSP "
         "tree) within `radius` m of a point, coloured by surface - triangles in the editor's view space (metres, x, y, "
         "-z), packed as base64 float32 positions + one uint8 surface per triangle. Also: per surface triangle counts "
         "and each surface's class (walk / blocking / invisible / barrier / render).",
         center="[x, y, z] game units", radius="metres (default 60, max 300)", max_triangles="default 250000")
def collision_view(s: Session, center, radius=60, max_triangles=250000):
    import base64
    import struct as _st
    _need_level(s)
    sc = _c.scene(s)
    c = _vec(center)
    ct = (c[0] / 100.0, c[1] / 100.0, -c[2] / 100.0)
    rad = min(300.0, max(5.0, float(radius)))
    jobs = []
    for z in sc["zones"]:
        if not z.get("chunked"):
            jobs.append((z["model"], None, z))
    def near(p, margin):
        q = (p[0] / 100.0, p[1] / 100.0, -p[2] / 100.0)
        return math.dist(q, ct) <= rad + margin
    for ch in sc["chunks"]:
        if near(ch.get("pivot") or ch["pos"], 150.0):     # pivot = the piece's centre in the world
            jobs.append((ch.get("zone") or ch["model"], ch["index"], ch))   # model = the record's own name (MIS1_ZONE1158)
    for p in sc["props"]:
        if p.get("terrain") or p.get("road"):
            continue                              # editor-made ground: its collision is built when the mission saves
        if not near(p.get("pivot") or p["pos"], 80.0):
            continue
        if p.get("kit"):
            k = p["kit"]
            src = k["source"][:-4] if str(k["source"]).upper().endswith(".EVO") else k["source"]
            jobs.append((src, k["index"], p))
        else:
            jobs.append((p["model"], None, p))
    for o in sc["objects"]:
        if "_VEH_" in str(o.get("unit") or "").upper() and near(o["pos"], 40.0):
            jobs.append((o["model"], None, {**o, "scale": (1, 1, 1)}))
    pos, surf = [], []
    counts = {}
    truncated = False
    for model, index, rec in jobs:
        polys, centre, r0 = _model_collision(s, model, index)
        if not polys:
            continue
        m = _record_matrix(rec["pos"], rec.get("rot") or (0, 0, 0), rec.get("scale") or (1, 1, 1))
        k = max(abs(float(v)) for v in (rec.get("scale") or (1, 1, 1)))
        if math.dist(_apply(m, centre), ct) > rad + r0 * k:
            continue
        for sfc, pts in polys:
            w = [_apply(m, q) for q in pts]
            if min(math.dist(q, ct) for q in w) > rad:
                continue
            for i in range(1, len(w) - 1):
                for q in (w[0], w[i], w[i + 1]):
                    pos.extend(q)
                surf.append(sfc)
                counts[sfc] = counts.get(sfc, 0) + 1
            if len(surf) >= int(max_triangles):
                truncated = True
                break
        if truncated:
            break
    return {"triangles": len(surf), "truncated": truncated,
            "positions": base64.b64encode(_st.pack(f"<{len(pos)}f", *pos)).decode(),
            "surfaces": base64.b64encode(bytes(surf)).decode(),
            "legend": [{"surface": i, "name": _bsp.SURFACES[i] if i < len(_bsp.SURFACES) else f"#{i}",
                        "class": _surface_class(i), "triangles": n} for i, n in sorted(counts.items(), key=lambda kv: -kv[1])]}


# ============================================================================================ play from here ====
# Test from any spot: Play, but the squad starts where the editor's camera looks. The mission is saved as usual, then a
# marked block is added to its script (moves the squad on the first frame the player has control, with the game's
# place_at_position, API +0x510 - a 12-byte string is the float[3] it wants) and taken out again once the level has
# loaded; every save strips it too, so it never ships with the mission.
PLAY_BEGIN, PLAY_END = "-- @editor begin: play from here (test only - removed again by the editor)", \
    "-- @editor end: play from here"


def strip_play_block(text):
    a = text.find(PLAY_BEGIN)
    if a < 0:
        return text
    b = text.find(PLAY_END, a)
    if b < 0:
        return text
    return (text[:a].rstrip("\n") + "\n" + text[b + len(PLAY_END):].lstrip("\n")).rstrip("\n") + "\n"


def _play_block(pos, yaw):
    spots = []
    for k in range(4):                       # players 1-4 side by side, 1.5 m apart
        off = _turn((150.0 * (k % 2) - 75.0, 0.0, -150.0 * (k // 2)), yaw)
        spots.append(f"{{{pos[0] + off[0]:.1f}, {pos[1] + 30:.1f}, {pos[2] + off[2]:.1f}}}")
    return "\n".join([
        PLAY_BEGIN,
        "do",
        "  local spots = {" + ", ".join(spots) + "}",
        "  local tries, done = 0, false",
        "  local user_update = update",
        "  function update(...)",
        "    if not done then                  -- each frame until every player's soldier stands there (a few tries:",
        "      tries = tries + 1               -- intro / loadout screens can put them back)",
        "      local all, any = true, false",
        "      for n = 0, 3 do                 -- each player's own soldier (game.player_soldier: address, script id at +8)",
        "        local soldier = game.player_soldier(n)",
        "        if soldier ~= 0 then",
        "          any = true",
        "          local sp = spots[n + 1]",
        "          local x, y, z = game.player_position(n)",
        "          if not x or math.abs(x - sp[1]) + math.abs(z - sp[3]) > 300 then",
        "            all = false",
        "            local id = game.read32(soldier + 8)",
        "            game.call(0x510, id, string.pack('<fff', sp[1], sp[2], sp[3]))",
        "            if tries == 1 or tries % 100 == 0 then",
        "              game.log(string.format('editor: play from here - player %d (#%d) at %.0f %.0f %.0f -> %.0f %.0f %.0f (try %d)',",
        "                                     n + 1, id, x or 0, y or 0, z or 0, sp[1], sp[2], sp[3], tries))",
        "            end",
        "          end",
        "        end",
        "      end",
        "      if all and any then done = true; game.log('editor: play from here - squad moved (' .. tries .. ' frames)') end",
        "      if not done and tries >= 3000 then done = true; game.log('editor: play from here - gave up, the squad stays at its start') end",
        "    end",
        "    if user_update then return user_update(...) end",
        "  end",
        "end",
        PLAY_END, ""])


def _squad_ids(s: Session):
    out = []
    for r in s.env.find("HLOI"):
        if not r.fields or not str(r.get("model")).upper().startswith("HERO"):
            continue
        flags = 0
        try:
            flags = int(r.get("flags") or 0)
        except (TypeError, ValueError):
            pass
        if r.get("in_play") == 0 or flags & 0x20000:
            continue                               # cutscene actors and soldiers waiting for the script
        out.append(r["script_id"])
    return out[:4]


def _remove_play_block_later(path, game_dir, started):
    import threading

    def work():
        log = os.path.join(game_dir or "", "DesertStormFix.log")
        deadline = started + 150
        while time.time() < deadline:
            time.sleep(2)
            try:
                if os.path.getmtime(log) >= started:
                    with open(log, encoding="utf-8", errors="replace") as f:
                        if "levelInitialise" in f.read():
                            time.sleep(3)
                            break
            except OSError:
                pass
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            new = strip_play_block(text)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new)
        except OSError:
            pass
    threading.Thread(target=work, daemon=True, name="play-from-here cleanup").start()


@command("Play from here: save the mission and start the game in it with the squad standing at `pos` (facing yaw) - "
         "moved there on the first frame the player has control (after the intro). A test only: the start point is "
         "added to the script for this launch and taken out again once the level has loaded (and by every save).",
         pos="[x, y, z] game units", yaw="facing in degrees")
def play_from_here(s: Session, pos, yaw=0.0):
    _need_level(s)
    if not _squad_ids(s):
        raise CommandError("no squad soldier in the scene to move")
    p = _vec(pos)
    original = _c.save_mission

    def save_then_add(*a, **k):
        out = original(*a, **k)
        path = _c._script_path(s)
        with open(path, encoding="utf-8") as f:
            text = strip_play_block(f.read())
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.rstrip("\n") + "\n\n" + _play_block(p, float(yaw or 0.0)))
        return out
    _c.save_mission = save_then_add
    try:
        r = _c.play(s)
    finally:
        _c.save_mission = original
    _remove_play_block_later(_c._script_path(s), s.game_dir, time.time() - 5)
    return {**r, "from": list(p)}


# every save writes the script without a leftover start point
if not getattr(_c._update_layers_block, "_strips_play", False):
    _orig_update_layers = _c._update_layers_block

    def _update_layers_block(path, s):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if PLAY_BEGIN in text:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(strip_play_block(text))
        except OSError:
            pass
        return _orig_update_layers(path, s)
    _update_layers_block._strips_play = True
    _c._update_layers_block = _update_layers_block


# ============================================================================================= script check ====
# Before Play: the mission's Lua compiled (not run) by Lua 5.4 - the plugin's version - through lupa, so a syntax error
# shows with its line instead of a level that won't start; plus calls of game.<name> the plugin doesn't have
# (games/<id>/lua_api.json, scripts/gen_lua_api.py).
def _lua_api(s: Session):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", s.profile.id, "lua_api.json")
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f)["game"])
    except (OSError, ValueError, KeyError):
        return None


def _lua_compile_error(text):
    try:
        from lupa import lua54
    except ImportError:
        return None, "Lua syntax check unavailable (lupa isn't installed)"
    rt = lua54.LuaRuntime(register_eval=False, register_builtins=False)
    check = rt.eval('function(src) local fn, err = load(src, "=script", "t", {}); return err end')
    return check(text), None


@command("Check the mission's Lua before Play: syntax errors (compiled by Lua 5.4 like the plugin does - nothing runs) "
         "and calls of game.<name> functions the plugin doesn't have. Checks `text` if given, else the saved script.",
         text="optional script text (the script window's)")
def check_script(s: Session, text=None):
    if text is None:
        path = _c._script_path(s)
        if not os.path.exists(path):
            raise CommandError("the mission has no script yet - save it as a mission first")
        with open(path, encoding="utf-8") as f:
            text = f.read()
    text = str(text)
    errors, warnings = [], []
    err, note = _lua_compile_error(text)
    if note:
        warnings.append({"line": None, "message": note})
    if err:
        m = re.match(r"script:(\d+):\s*(.*)", str(err))
        errors.append({"line": int(m.group(1)) if m else None, "message": m.group(2) if m else str(err)})
    api = _lua_api(s)
    if api:
        defined = set(re.findall(r"function\s+game\.([A-Za-z_]\w*)|\bgame\.([A-Za-z_]\w*)\s*=(?!=)", text))
        mine = {a or b for a, b in defined}
        seen = set()
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("--", 1)[0]
            for name in re.findall(r"\bgame\.([A-Za-z_]\w*)", code):
                if name not in api and name not in mine and (name, i) not in seen:
                    seen.add((name, i))
                    warnings.append({"line": i, "message": f"game.{name} isn't a function of the game's Lua host "
                                                           "(typo? - it would fail when that line runs)"})
    return {"ok": not errors, "errors": errors, "warnings": warnings[:50], "lines": text.count("\n") + 1}


# ============================================================================================== doors & keys ====
# The Creation Kit's "View all locks": every door (HLOI with a Doors.txt unit), the switches that drive it, the keys and
# lock changes the base mission's DLL sets up (API +0x5AC set_door_key (key, door), +0x440 set_door_flags (door, mask,
# value) - flag 8 = locked, from the DLL analysis; medium confidence), and the editor's own settings, which compile into
# one mission-start rule. Not yet verified in game.
_DOOR_RULE = "Doors & keys"
_LOCKED = 8


def _dll_door_calls(s: Session):
    try:
        import missionlogic
        a, _name = missionlogic.analysis(s)
    except Exception:  # noqa: BLE001 - no DLL / not readable
        return [], []
    if not a:
        return [], []
    keys, flags = [], []
    for _va, body in a["functions"].items():
        for st in missionlogic._walk(body):
            if st.get("op") != "api":
                continue
            args, slot = st.get("args") or [], st.get("slot")
            ints = [x.get("v") if x.get("k") == "int" else None for x in args]
            if slot == 0x5AC and len(ints) >= 2 and None not in ints[:2]:
                keys.append({"key": ints[0], "door": ints[1]})
            elif slot == 0x440 and len(ints) >= 3 and None not in ints[:3]:
                flags.append({"door": ints[0], "mask": ints[1], "value": ints[2]})
    return keys, flags


def _door_rule(s: Session):
    lines = []
    for door, d in sorted(_wt(s)["doors"].items(), key=lambda kv: int(kv[0])):
        if d.get("locked") is not None:
            lines.append(f"game.call(0x440, {int(door)}, {_LOCKED}, {_LOCKED if d['locked'] else 0})   -- "
                         f"door #{door} {'locked' if d['locked'] else 'unlocked'}")
        if d.get("key") is not None and int(d["key"]) >= 0:
            lines.append(f"game.call(0x5AC, {int(d['key'])}, {int(door)})   -- key #{d['key']} opens door #{door}")
    return {"name": _DOOR_RULE, "enabled": True, "once": True, "trigger": {"type": "mission_start"}, "conditions": [],
            "actions": [{"type": "lua", "code": "\n".join(lines)}] if lines else []}


@command("Doors & keys (Creation Kit 'View all locks'): every door object with its position, the switches that drive it, "
         "the keys and lock changes the base mission's DLL sets up, and the editor's own lock / key settings.")
def doors_report(s: Session):
    _need_level(s)
    import interactive
    ds = interactive.doors(s)
    sc = _c.scene(s)
    by_door = {}
    for sw in sc.get("switches") or []:
        for d in sw.get("doors") or []:
            by_door.setdefault(d if isinstance(d, int) else d.get("script_id"), []).append(sw.get("name"))
    keys, flags = _dll_door_calls(s)
    objs = {r["script_id"]: r for r in s.env.find("HLOI") if r.fields}
    mine = _wt(s)["doors"]
    out = []
    for d in ds:
        sid = d["script_id"]
        out.append({**d, "switches": by_door.get(sid, []),
                    "dll_keys": [{"key": k["key"], "item": str(objs[k["key"]]["unit"]) if k["key"] in objs else None}
                                 for k in keys if k["door"] == sid],
                    "dll_flags": [f for f in flags if f["door"] == sid],
                    "editor": mine.get(str(sid))})
    return {"doors": out, "dll_keys": len(keys), "dll_flag_calls": len(flags)}


@command("Set a door's lock and key for the mission (compiled into the 'Doors & keys' mission-start rule): locked "
         "true / false / null (leave as the level has it), key = the script id of an item that unlocks it (-1 none). "
         "Uses the game's set_door_flags (flag 8) and set_door_key - not yet verified in game.", mutates=True,
         door="door script id", locked="true / false / null", key="item script id or -1")
def door_set(s: Session, door, locked=None, key=None):
    _need_level(s)
    import interactive
    ids = {d["script_id"] for d in interactive.doors(s)}
    if int(door) not in ids:
        raise CommandError(f"#{door} isn't a door object (Doors.txt unit)")
    objs = {r["script_id"]: r for r in s.env.find("HLOI") if r.fields}
    if key is not None and int(key) >= 0 and int(key) not in objs:
        raise CommandError(f"no object #{key} in the scene for the key")
    w = _wt(s)
    doors = dict(w["doors"])
    entry = {"locked": None if locked is None else bool(locked), "key": None if key is None or int(key) < 0 else int(key)}
    if entry["locked"] is None and entry["key"] is None:
        doors.pop(str(int(door)), None)
    else:
        doors[str(int(door))] = entry
    w["doors"] = doors
    _put_rule(s, _door_rule(s))
    return {"door": int(door), **entry, "action": f"Door #{door}: " + ", ".join(
        x for x in ("locked" if entry["locked"] else "unlocked" if entry["locked"] is False else "",
                    f"key #{entry['key']}" if entry["key"] is not None else "") if x) or f"Door #{door}: as the level"}


# ================================================================================================== portals ====
# The Creation Kit's "Portals and Rooms" view and "Validate room/portal alignment". PORT records join two zones with a
# convex polygon; the renderer draws a zone only when a portal to it is in view, so a portal that no longer matches the
# geometry makes walls vanish. Shipped data (720 portals over 26 levels): the plane satisfies n.v + d = 0 within 8.7 cm,
# the centre is the corners' average (0.015 cm); the radius isn't a plain bounding radius (left alone).
_PLANE_TOL, _CENTRE_TOL = 15.0, 5.0


def _portal_info(s: Session, r, zone_names, moved):
    n = (r["plane.x"], r["plane.y"], r["plane.z"])
    d = r["plane.d"]
    vs = [tuple(r[f"vertex{k}"]) for k in range(r["vertex_count"])]
    problems = []
    for z in (r["zone_a"], r["zone_b"]):
        if str(z) not in zone_names:
            problems.append({"severity": "error", "message": f"zone {z} isn't in the scene"})
    if len(vs) < 3:
        problems.append({"severity": "error", "message": f"only {len(vs)} corners"})
    else:
        off = max(abs(sum(a * b for a, b in zip(n, v)) + d) for v in vs)
        if off > _PLANE_TOL:
            problems.append({"severity": "warning", "message": f"corners lie up to {off:.0f} cm off the portal's plane",
                             "fix": True})
        c = [sum(v[i] for v in vs) / len(vs) for i in range(3)]
        if math.dist(c, r["centre"]) > _CENTRE_TOL:
            problems.append({"severity": "warning", "message": f"centre is {math.dist(c, r['centre']):.0f} cm from the "
                                                               "corners' middle", "fix": True})
    for z in (r["zone_a"], r["zone_b"]):
        if str(z) in moved:
            problems.append({"severity": "warning", "message": f"pieces of {z} were moved - check the portal still "
                                                               "fits the opening (else walls can vanish in game)"})
    return {"line": r.index + 1, "name": str(r["name"]), "zone_a": str(r["zone_a"]), "zone_b": str(r["zone_b"]),
            "vertices": [list(v) for v in vs], "centre": list(r["centre"]), "normal": list(n), "problems": problems}


def _moved_zones(s: Session):
    """Zones (root record names) with a piece moved / turned from where its model puts it (chunk record pos / rot)."""
    out = set()
    for r in s.env.find("ENVI"):
        if not r.fields or r.file.is_prop(r):
            continue
        if any(abs(c) > 0.5 for c in r["pos"]) or any(abs(c) > 0.01 for c in r["rot"]):
            parent = str(r.get("parent"))
            out.add(parent if parent.upper() != "ROOT" else str(r["name"]))
    return out


@command("Portals (Creation Kit 'Portals and Rooms' + 'Validate room/portal alignment'): every PORT record - the two "
         "zones it joins, its corners, centre and plane normal - with problems: missing zones, corners off the plane, a "
         "stale centre, zones whose pieces were moved.")
def portals(s: Session):
    _need_level(s)
    names = {str(r.get("name")) for r in s.env.records if r.kind == "ENVI" and r.fields}
    moved = _moved_zones(s)
    out = [_portal_info(s, r, names, moved) for r in s.env.find("PORT") if r.fields]
    return {"portals": out, "with_problems": sum(1 for p in out if p["problems"])}


@command("Recompute a portal's plane and centre from its corners (or every portal that needs it): the plane through "
         "them (normal kept on the same side), the centre = their average. One undo step.", mutates=True,
         line="optional PORT line (default: all with a plane / centre problem)")
def portal_fix(s: Session, line=None):
    _need_level(s)
    recs = [s.record(line)] if line is not None else [r for r in s.env.find("PORT") if r.fields]
    fixed = []
    for r in recs:
        if r.kind != "PORT":
            raise CommandError(f"line {r.index + 1} isn't a portal")
        vs = [tuple(r[f"vertex{k}"]) for k in range(r["vertex_count"])]
        if len(vs) < 3:
            continue
        c = [sum(v[i] for v in vs) / len(vs) for i in range(3)]
        nx = ny = nz = 0.0                      # Newell's method: robust for any convex polygon
        for a, b in zip(vs, vs[1:] + vs[:1]):
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-9:
            continue
        n = [nx / ln, ny / ln, nz / ln]
        old = (r["plane.x"], r["plane.y"], r["plane.z"])
        if sum(a * b for a, b in zip(n, old)) < 0:
            n = [-x for x in n]
        d = -sum(a * b for a, b in zip(n, c))
        off = max(abs(sum(a * b for a, b in zip(old, v)) + r["plane.d"]) for v in vs)
        if line is None and off <= _PLANE_TOL and math.dist(c, r["centre"]) <= _CENTRE_TOL:
            continue
        r["plane.x"], r["plane.y"], r["plane.z"], r["plane.d"] = (round(n[0], 6), round(n[1], 6), round(n[2], 6),
                                                                  round(d, 4))
        r["centre"] = tuple(round(x, 4) for x in c)
        fixed.append(r.index + 1)
    return {"fixed": fixed, "action": f"Fix {len(fixed)} portal{'s' if len(fixed) != 1 else ''}"}
