"""Editor commands for level dressing and checking: the AI walk map, the tactical map (minimap) image, prefabs
(saved groups of records), vehicle crews and sound audition. Registered into commands.py's command table like
logic.py and records.py, so the UI and the MCP server get the same commands.

Walk map: the game's own AI quadtree (<LEVEL>.QTD, see conflict_ds_tools/routes.py): walkable leaves (one route
marker each, 51 cm above the ground) and blocked leaves. A mission ships no .QTD - the game rebuilds it from the
edited scene at load (DesertStormFix BuildNav) - so the shipped map is shown plus the footprints of what was added
since (props, kit pieces, vehicles), which are what the rebuild will cut out.

Minimap: the in-game map (MISSION tab, pause screen) is image 0 of a one-page sheet "GW<level>.img" (API +0x4C,
game.load_map_image) stretched over the level bounds (API +0x54, game.set_level_bounds): u = (x - min x) / width,
v = 1 - (z - min z) / depth - checked against Mission 1's shipped map (its walkable cells lie on the canyon drawn
there). Every shipped sheet is 300 bytes: "imgf", u16 1, u16 1 page, u16 0x20, u16 1 image, page file name
char[256], one 32-byte image entry (u16 page, zeros, then i16 x, y, w, h, offset x, offset y at +0x14).
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import struct
import threading
import time
import wave

from commands import (CommandError, Session, command, _PASTE_END, _attach_crew, _base_ids, _catalog, _crew,  # noqa: F401
                      _crew_texts, _find_file, _is_vehicle, GUN_MOUNTS, _kit_cache, _kit_centre_world, _library, _nearest_zone,
                      _paste_entries, _script_path, _setups, _vec)
from conflict_ds_tools.env import EnvFile

MARKER_LIFT = 51.0          # generated route markers sit this far above the ground (cm)


def _need_level(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    return s.env


# =========================================================================================== AI walk map ====
def _read_qtd(data):
    """[(kind, data, child offset, centre x, centre z, half size)] of a .QTD (node 0 = the root)."""
    n = struct.unpack_from("<i", data, 0)[0]
    if 4 + n * 20 + 8 != len(data):
        raise ValueError("not a quadtree file")
    return [struct.unpack_from("<HHIfff", data, 4 + i * 20) for i in range(n)]


def _qtd_leaf(nodes, x, z):
    """The leaf containing (x, z), or None outside the tree. Children: (-x -z), (+x -z), (-x +z), (+x +z)."""
    i = 0
    _, _, _, cx, cz, h = nodes[0]
    if abs(x - cx) > h or abs(z - cz) > h:
        return None
    while nodes[i][2]:
        _, _, child, cx, cz, _ = nodes[i]
        i = child // 20 + (1 if x >= cx else 0) + (2 if z >= cz else 0)
    return nodes[i]


def _walk_nodes(s: Session):
    """The level's .QTD nodes (cached per file), or None when the level has none. A new level (new_level: s.blank)
    has none either - its session still names the environment level, whose map is another place; the game builds the
    new level's walk map from the scene at load."""
    if getattr(s, "blank", False):
        return None
    path = _find_file(s.folder, s.level + ".QTD") if s.folder and s.level else None
    if not path:
        return None
    cache = getattr(s, "_qtd_cache", None)
    if not cache or cache[0] != path:
        s._qtd_cache = cache = (path, _read_qtd(open(path, "rb").read()))
    return cache[1]


def walkable_at(s: Session, x, z):
    """True / False = the walk-map cell under (x, z) is walkable / blocked (outside the map counts as blocked); None
    when the level has no walk map. Blocked cells: AI soldiers can't plan a route to or from there (indoor routes use
    hand-placed markers instead, so a blocked waypoint is a risk, not always a failure)."""
    nodes = _walk_nodes(s)
    if nodes is None:
        return None
    leaf = _qtd_leaf(nodes, x, z)
    return bool(leaf and leaf[0] == 3)


def nearest_walkable(s: Session, x, z):
    """Centre [x, y, z] of the nearest walkable cell (y = its ground: the route marker 51 cm above it, or None)."""
    nodes = _walk_nodes(s)
    if nodes is None:
        return None
    best = None
    for kind, d, child, cx, cz, h in nodes:
        if child or kind != 3:
            continue
        # distance to the cell (0 inside it), then to its centre - so a point next to a big cell lands inside it
        dx, dz = max(abs(x - cx) - h, 0.0), max(abs(z - cz) - h, 0.0)
        key = (dx * dx + dz * dz, (x - cx) ** 2 + (z - cz) ** 2)
        if best is None or key < best[0]:
            best = (key, cx, cz, h, d)
    if best is None:
        return None
    _, cx, cz, h, d = best
    y = None
    if s.routes and 0 <= d < len(s.routes.markers):
        y = s.routes.markers[d].pos[1] - MARKER_LIFT
    # inside a big cell: the point itself moved just inside the cell, else the centre
    px = min(max(x, cx - h + 30), cx + h - 30) if h > 60 else cx
    pz = min(max(z, cz - h + 30), cz + h - 30) if h > 60 else cz
    return [round(px, 1), None if y is None else round(y, 1), round(pz, 1)]


@command("Walk-map check of points: is each on a walkable cell of the level's AI walk map (blocked = soldiers can't "
         "plan a route there), and the nearest walkable spot. walkable null = the level has no walk map.",
         points="[[x, y, z], ...] (cm)")
def walk_check(s: Session, points):
    _need_level(s)
    out = []
    for p in points or []:
        x, z = float(p[0]), float(p[2])
        ok = walkable_at(s, x, z)
        n = None if ok in (True, None) else nearest_walkable(s, x, z)
        out.append({"pos": [x, float(p[1]), z], "walkable": ok,
                    **({"nearest": n, "distance": round(math.hypot(n[0] - x, n[2] - z))} if n else {})})
    return out


class _Nearest:
    """Nearest walkable height by x / z (bucketed)."""

    def __init__(self, points, cell=2000.0):
        self.cell, self.grid = cell, {}
        for x, y, z in points:
            self.grid.setdefault((int(x // cell), int(z // cell)), []).append((x, y, z))

    def y(self, x, z, default=0.0):
        if not self.grid:
            return default
        gx, gz = int(x // self.cell), int(z // self.cell)
        for r in range(0, 64):
            best = None
            for i in range(gx - r, gx + r + 1):
                for j in range(gz - r, gz + r + 1):
                    if max(abs(i - gx), abs(j - gz)) != r:
                        continue
                    for px, py, pz in self.grid.get((i, j), ()):
                        d = (px - x) ** 2 + (pz - z) ** 2
                        if best is None or d < best[0]:
                            best = (d, py)
            if best is not None:
                return best[1]
        return default


def _ground_y(s: Session):
    """{(round x, round z): ground y} of the generated route markers (each sits on a walkable cell's centre)."""
    out = {}
    if s.routes:
        for m in s.routes.markers:
            if m.flags & 1:
                out[(round(m.pos[0]), round(m.pos[2]))] = m.pos[1] - MARKER_LIFT
    return out


def _new_obstacles(s: Session):
    """Lines of props (incl. kit pieces) and vehicles that aren't in the base level's scene where they are now: what
    the game's walk-map rebuild will cut out on top of the shipped map. Terrain patches and roads are walkable."""
    path = _find_file(s.folder, s.level + ".ENV") if s.folder else None
    base = EnvFile.load(path) if path else None
    key = lambda r: (str(r.get("model")).upper(), tuple(round(c) for c in r["pos"]))   # noqa: E731
    base_props = {key(r) for r in base.find("ENVI") if r.get("type") == 0} if base else set()
    base_ids = _base_ids(s)
    walk = {k.upper() for k in list(getattr(s, "terrains", {})) + list(getattr(s, "roads", {}))}
    out = []
    for r in s.env.records:
        try:
            if r.kind == "ENVI" and r.fields and r.get("type") == 0 and not r.children:
                if str(r["model"]).upper() not in walk and key(r) not in base_props:
                    out.append(r.index + 1)
            elif _is_vehicle(r) and (r["script_id"] not in base_ids or not base):
                out.append(r.index + 1)
        except KeyError:
            continue
    return out


@command("The AI walk map: the level's quadtree cells (walkable / blocked, centre on the ground, size in cm) and the "
         "props / kit pieces / vehicles added since (footprints the game's rebuild cuts out). resolution > 0: a "
         "regular grid of that cell size instead of the quadtree's own cells.",
         resolution="optional grid cell size in cm (0 = the quadtree's cells)",
         blocked="also return blocked cells (default yes)", cells="false: only the footprints (they change with edits)")
def walkability_grid(s: Session, resolution=0, blocked=True, cells=True):
    _need_level(s)
    if not cells:
        return {"footprints": _new_obstacles(s)}
    # a new level (s.blank) has no map yet: its session names the environment level, whose map is another place
    path = _find_file(s.folder, s.level + ".QTD") if s.folder and not getattr(s, "blank", False) else None
    ground = _ground_y(s)
    cells, source = [], None
    if path:
        nodes = _read_qtd(open(path, "rb").read())
        source = f"{s.level}.QTD (the game's AI walk map)"
        walk_pts = []
        for kind, _d, child, x, z, h in nodes:
            if not child and kind == 3:
                y = ground.get((round(x), round(z)))
                if y is not None:
                    walk_pts.append((x, y, z))
        near = _Nearest(walk_pts)
        res = float(resolution or 0)
        if res > 0:
            if res < 50:
                raise CommandError("resolution: 50 cm or more")
            xs = [p[0] for p in walk_pts] or [nodes[0][3]]
            zs = [p[2] for p in walk_pts] or [nodes[0][4]]
            x0, x1 = min(xs) - 2 * res, max(xs) + 2 * res
            z0, z1 = min(zs) - 2 * res, max(zs) + 2 * res
            nx, nz = int((x1 - x0) // res) + 1, int((z1 - z0) // res) + 1
            if nx * nz > 250000:
                raise CommandError(f"{nx} x {nz} cells - use a larger resolution")
            for i in range(nx):
                for j in range(nz):
                    x, z = x0 + (i + 0.5) * res, z0 + (j + 0.5) * res
                    leaf = _qtd_leaf(nodes, x, z)
                    ok = bool(leaf and leaf[0] == 3)
                    if ok or blocked:
                        cells.append({"pos": [round(x, 1), round(near.y(x, z), 1), round(z, 1)], "walkable": ok,
                                      "size": res})
        else:
            for kind, _d, child, x, z, h in nodes:
                if child:
                    continue
                ok = kind == 3
                if not ok and not blocked:
                    continue
                y = ground.get((round(x), round(z))) if ok else None
                cells.append({"pos": [round(x, 1), round(y if y is not None else near.y(x, z), 1), round(z, 1)],
                              "walkable": ok, "size": 2 * h})
    elif s.routes and any(m.flags & 1 for m in s.routes.markers):
        source = "generated route markers (no .QTD for this level)"
        size = float(resolution or 250)
        for m in s.routes.markers:
            if m.flags & 1:
                cells.append({"pos": [round(m.pos[0], 1), round(m.pos[1] - MARKER_LIFT, 1), round(m.pos[2], 1)],
                              "walkable": True, "size": size})
    walkable = sum(1 for c in cells if c["walkable"])
    return {"source": source or "none (no walk map or route markers for this level - the game builds one at load)",
            "cells": cells, "walkable": walkable, "blocked": len(cells) - walkable,
            "footprints": _new_obstacles(s)}


# =============================================================================================== minimap ====
from conflict_ds_tools import minimap as _minimap  # noqa: E402  (sheet + script hook, shared with the Blender add-on)

MAP_SIZE = _minimap.MAP_SIZE


def _level_bounds(s: Session):
    """((min x, min z), (max x, max z)) the in-game map stretches over: the mission script's set_level_bounds, else
    the base level's set-up (level_setups.json), else the scene's extent."""
    pairs = None
    try:
        text = open(_script_path(s), encoding="utf-8").read() if s.mission else ""
    except (OSError, CommandError):
        text = ""
    m = re.search(r"^\s*game\.set_level_bounds\(([^)]*)\)", text, re.M)
    if m:
        v = [float(t) for t in m.group(1).split(",")]
        if len(v) == 6:
            pairs = (v[0:3], v[3:6])
    if pairs is None:
        for c in _setups().get(s.level, []):
            if c["slot"] == 0x54 and len(c["args"]) == 2:
                pairs = c["args"]
    if pairs is None:
        pts = [r["pos"] for r in s.env.records if r.fields and "pos" in r.fields and r.kind in ("ENVI", "HLOI")]
        if not pts:
            raise CommandError("the level has no bounds and nothing placed")
        pairs = ([max(p[0] for p in pts) + 3000, 0, max(p[2] for p in pts) + 3000],
                 [min(p[0] for p in pts) - 3000, 0, min(p[2] for p in pts) - 3000])
    a, b = pairs
    return (min(a[0], b[0]), min(a[2], b[2])), (max(a[0], b[0]), max(a[2], b[2]))


def _map_names(s: Session):
    up = s.mission[1] if s.mission else s.level
    return f"GW{up}.img", f"GW{up}0.png"


@command("What the tactical map image covers: the level bounds (game x / z, cm) the in-game map stretches over, the "
         "image size and the files save_minimap writes.")
def minimap_info(s: Session):
    _need_level(s)
    (x0, z0), (x1, z1) = _level_bounds(s)
    img, page = _map_names(s)
    return {"min": [x0, z0], "max": [x1, z1], "size": MAP_SIZE, "mission": list(s.mission) if s.mission else None,
            "files": [img, page], "base_map": next((c["args"][0] for c in _setups().get(s.level, []) if c["slot"] == 0x4C), None)}


_sheet = _minimap.sheet
MINIMAP_BEGIN, MINIMAP_END = _minimap.BEGIN, _minimap.END
_script_with_map = _minimap.script_with_map


@command("Save a top-down picture of the level as the mission's tactical map (in-game MISSION tab / pause screen): "
         "GW<NAME>.img + GW<NAME>0.png in the mission's mod folder, and the script loads it. The picture must cover "
         "exactly minimap_info's bounds (left = min x, top = max z).",
         image_data_base64="PNG bytes, base64 (a data: URL is fine), square; scaled to 256 x 256")
def save_minimap(s: Session, image_data_base64):
    from conflict_ds_tools import images
    _need_level(s)
    if not s.mission:
        raise CommandError("save the level as a mission first (Save as mission…) - the map belongs to its script")
    data = str(image_data_base64 or "")
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        w, h, rgba = images.decode_png(base64.b64decode(data))
    except Exception as e:  # noqa: BLE001
        raise CommandError(f"not a PNG: {e}") from None
    if w != h or w < 64:
        raise CommandError(f"the picture must be square (got {w} x {h})")
    if w != MAP_SIZE:
        rgba = _resize_rgba(w, rgba, MAP_SIZE)
    img, page = _map_names(s)
    mod, _up = s.mission
    folder = os.path.join(s.game_dir or "", "Mods", mod)
    if not os.path.isdir(folder):
        raise CommandError(f"the mission's mod folder is missing: {folder}")
    with open(os.path.join(folder, page), "wb") as f:
        f.write(images.encode_png(MAP_SIZE, MAP_SIZE, rgba))
    with open(os.path.join(folder, img), "wb") as f:
        f.write(_sheet(page, MAP_SIZE))
    clean = os.path.join(folder, page[:-4] + ".clean.png")   # production.py's pin-less copy: a new picture replaces it
    if os.path.exists(clean):
        os.remove(clean)
    script, how = _script_path(s), None
    if os.path.exists(script):
        text = open(script, encoding="utf-8").read()
        new, how = _script_with_map(text, img)
        if new != text:
            with open(script, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
    return {"files": [os.path.join(folder, img), os.path.join(folder, page)], "size": MAP_SIZE,
            "script": how or "no script yet - save the mission again", "bounds": minimap_info(s)}


def _resize_rgba(size, rgba, to):
    """Box-filter a square RGBA image (bytes) from size to `to` pixels a side."""
    out = bytearray(to * to * 4)
    f = size / to
    for y in range(to):
        y0, y1 = int(y * f), max(int(y * f) + 1, int((y + 1) * f))
        for x in range(to):
            x0, x1 = int(x * f), max(int(x * f) + 1, int((x + 1) * f))
            acc = [0, 0, 0, 0]
            n = 0
            for yy in range(y0, y1):
                row = yy * size
                for xx in range(x0, x1):
                    o = (row + xx) * 4
                    acc[0] += rgba[o]; acc[1] += rgba[o + 1]; acc[2] += rgba[o + 2]; acc[3] += rgba[o + 3]
                    n += 1
            o = (y * to + x) * 4
            out[o:o + 4] = bytes(int(a / n + 0.5) for a in acc)
    return bytes(out)


# =============================================================================================== prefabs ====
def _prefab_dir(s: Session):
    return os.path.join(s.data_dir, "_editor_cache", "prefabs")


def _prefab_path(s: Session, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _\-]{0,47}", str(name or "")):
        raise CommandError("prefab name: letters, digits, space, _ and - (up to 48)")
    return os.path.join(_prefab_dir(s), name.strip() + ".json")


@command("Save records (objects with their vehicle crews, props, kit pieces, waypoints, regions, sounds, cameras) as a "
         "prefab: their offsets from the group's centre (on its lowest point), stored for every level and mission.",
         name="prefab name", lines="record lines", note="optional description")
def save_prefab(s: Session, name, lines, note=""):
    _need_level(s)
    path = _prefab_path(s, name)
    lines = [int(l) for l in (lines if isinstance(lines, list) else [lines])]
    recs = [s.record(l) for l in dict.fromkeys(lines)]
    crew_ids = {cid for r in recs for _u, _seat, cid in _crew(r)}
    walk = {k.upper() for k in list(getattr(s, "terrains", {})) + list(getattr(s, "roads", {}))}
    entries, centres = [], []
    for r in recs:
        if r.kind not in _PASTE_END or (r.kind == "ENVI" and (r.get("type") != 0 or r.children)):
            raise CommandError(f"line {r.index + 1}: {r.kind} records can't go into a prefab (world pieces stay with their zone)")
        if r.kind == "ENVI" and str(r["model"]).upper() in walk:
            raise CommandError(f"line {r.index + 1}: terrain patches and roads can't go into a prefab")
        if r.kind == "HLOI" and r["script_id"] in crew_ids:
            continue                            # comes along as its vehicle's crew
        e = {"kind": r.kind, "text": r.text, "pos": list(r["pos"]),
             "crew": _crew_texts(s.env, r) if r.kind == "HLOI" else [],
             "label": str(r.get("unit") or r.get("model") or r.get("name") or r.kind)}
        centre = list(r["pos"])
        kit = getattr(s, "kits", {}).get(str(r.get("model")).upper()) if r.kind == "ENVI" else None
        if kit:
            e["kit"] = {"name": str(r["model"]).upper(), **kit}
            centre = list(_kit_centre_world(r, kit))
        entries.append(e)
        centres.append(centre)
    if not entries:
        raise CommandError("nothing to save - select objects or props (Shift+click several)")
    anchor = [sum(c[0] for c in centres) / len(centres), min(c[1] for c in centres),
              sum(c[2] for c in centres) / len(centres)]
    os.makedirs(_prefab_dir(s), exist_ok=True)
    doc = {"name": name.strip(), "description": str(note or ""), "level": s.level, "saved": time.strftime("%Y-%m-%d %H:%M"),
           "anchor": anchor, "entries": entries}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    return {"name": doc["name"], "records": len(entries), "crew": sum(len(e["crew"]) for e in entries), "path": path}


@command("The saved prefabs: name, record count, kinds, the level they were made in.")
def list_prefabs(s: Session):
    out = []
    folder = _prefab_dir(s)
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not f.lower().endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(folder, f), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        es = d.get("entries", [])
        out.append({"name": d.get("name", f[:-5]), "description": d.get("description", ""), "level": d.get("level"),
                    "saved": d.get("saved"), "records": len(es), "crew": sum(len(e.get("crew") or []) for e in es),
                    "kinds": sorted({e["kind"] for e in es}), "contents": [e.get("label", e["kind"]) for e in es][:12]})
    return out


@command("Stamp a prefab: its centre goes to pos, the group turned by yaw degrees; new script ids, unique names, "
         "nearest zones, vehicles with their crews (one undo step).", mutates=True,
         name="prefab name", pos="[x, y, z] where the group's centre (on its lowest point) goes", yaw="turn in degrees")
def paste_prefab(s: Session, name, pos, yaw=0.0):
    path = _prefab_path(s, name)
    if not os.path.exists(path):
        raise CommandError(f"no prefab {name!r}")
    d = json.load(open(path, encoding="utf-8"))
    entries = d.get("entries") or []
    if not entries:
        raise CommandError("the prefab is empty")
    for e in entries:                           # kit pieces: the piece model must be known to this session
        k = e.get("kit")
        if k and k["name"] not in getattr(s, "kits", {}):
            try:
                _kit_cache(s, k["level"], k["source"][:-4] if k["source"].upper().endswith(".EVO") else k["source"],
                           k["index"], k["name"])
            except Exception as ex:  # noqa: BLE001
                raise CommandError(f"kit piece {k['name']}: {ex}") from None
            if not hasattr(s, "kits"):
                s.kits = {}
            s.kits[k["name"]] = {x: k[x] for x in ("level", "source", "index", "centre", "size") if x in k}
    lines = _paste_entries(s, entries, _vec(pos), float(yaw or 0.0), anchor=d.get("anchor"))
    return {"lines": lines, "name": d.get("name", name)}


@command("Delete a saved prefab.", name="prefab name")
def delete_prefab(s: Session, name):
    path = _prefab_path(s, name)
    if not os.path.exists(path):
        raise CommandError(f"no prefab {name!r}")
    os.remove(path)
    return {"deleted": name}


# ========================================================================================= vehicle crews ====
_SEAT_WORDS = {"DRIVER": "Driver", "GUNNER": "Gunner", "COMMANDER": "Commander", "PASSENGER": "Passenger"}


def _seats(s: Session, unit):
    """[(seat, label)] of a vehicle unit: VEHICLE.TXT columns 6-13 are its seat connectors in seat order
    (CONNECTOR_DRIVER01, CONNECTOR_GUNNER01 ...; T-62: 0 driver, 1 gunner, 2 commander, 3 passenger)."""
    if str(unit or "").upper() in GUN_MOUNTS:
        return [(1, "Gunner")]                   # tripod guns: every shipped gunner sits in seat 1
    rows = _catalog(s, "VEHICLE.TXT").get(str(unit or "").upper())
    out = []
    if rows:
        for seat, col in enumerate(rows[0][6:14]):
            if col in ("-1", ""):
                continue
            m = re.search(r"CONNECTOR_([A-Z]+?)0*(\d+)$", col.upper())
            word = _SEAT_WORDS.get(m.group(1), m.group(1).title()) if m else f"Seat {seat}"
            n = int(m.group(2)) if m else 1
            out.append((seat, f"{word} {n}" if word == "Passenger" or n > 1 else word))
    return out


def _soldier(r):
    import logic
    return logic._is_soldier(r)


def _crew_pool(s: Session):
    """Crew soldiers of the shipped levels' vehicles: {vehicle unit: [crew entry]}, {side prefix: [crew entry]}."""
    by_unit, by_side = {}, {}
    for e in _library(s):
        for c in e.get("crew") or []:
            if re.search(r"\bHLOI\s+\"?HERO", c["text"], re.I) or str(c["unit"]).upper() in ("BRADLEY", "FOLEY", "JONES", "RAMIREZ"):
                continue                        # the squad rides in some trucks - never spawn a hero
            by_unit.setdefault(str(e.get("unit")).upper(), []).append(c)
            by_side.setdefault(str(e.get("unit")).upper().split("_")[0], []).append(c)
    return by_unit, by_side


def _crew_template(s: Session, unit):
    by_unit, by_side = _crew_pool(s)
    u = str(unit).upper()
    side = u.split("_")[0]
    for pool in (by_unit.get(u), by_side.get(side), by_side.get({"NU": "IR", "UK": "US"}.get(side, "")), by_side.get("IR")):
        if pool:
            return pool[0]
    return None


def _crew_card(s: Session, vehicle):
    ids = {h["script_id"]: h for h in s.env.find("HLOI")}
    crew = _crew(vehicle)
    seats = _seats(s, vehicle["unit"])
    names = dict(seats)
    for _u, seat, _c in crew:
        names.setdefault(seat, f"Seat {seat}")
    if not names:
        names = {k: f"Seat {k}" for k in range(4)}
    out = []
    for seat in sorted(names):
        e = next((c for c in crew if c[1] == seat), None)
        sol = None
        if e:
            r = ids.get(e[2])
            sol = {"script_id": e[2], "unit": e[0], "line": r.index + 1 if r else None,
                   "model": str(r.get("model")) if r else None,
                   "ok": bool(r and str(r.get("unit")).upper() == str(e[0]).upper())}
        out.append({"seat": seat, "name": names[seat], "soldier": sol})
    in_vehicles = {}
    for r in s.env.find("HLOI"):
        for _u, seat, cid in _crew(r):
            in_vehicles[cid] = (r.index + 1, seat)
    vp = vehicle["pos"]
    cands = []
    for r in s.env.find("HLOI"):
        if not _soldier(r):
            continue
        d = math.dist((r["pos"][0], r["pos"][2]), (vp[0], vp[2]))
        where = in_vehicles.get(r["script_id"])
        cands.append({"line": r.index + 1, "script_id": r["script_id"], "unit": r["unit"], "model": r["model"],
                      "distance_m": round(d / 100, 1),
                      "in_vehicle": None if not where else {"line": where[0], "seat": where[1]}})
    cands.sort(key=lambda c: c["distance_m"])
    tpl = _crew_template(s, vehicle["unit"])
    return {"line": vehicle.index + 1, "unit": vehicle["unit"], "script_id": vehicle["script_id"], "seats": out,
            "candidates": cands[:30], "spawn_unit": tpl["unit"] if tpl else None,
            "known_seats": bool(seats)}


@command("A vehicle's seats (driver, gunner, commander, passengers - from VEHICLE.TXT) with the soldier in each, the "
         "nearest soldiers that could take one and the unit a spawned crewman would be.", line="the vehicle's HLOI line")
def vehicle_crew(s: Session, line):
    r = s.record(line)
    if not _is_vehicle(r):
        raise CommandError(f"line {line} isn't a vehicle (an HLOI with a _VEH_ unit)")
    return _crew_card(s, r)


@command("Put a soldier into a vehicle's seat (soldier_line), spawn a new crewman there (spawn), or empty it (neither). "
         "The soldier leaves any other seat; crew start in play with their vehicle.", mutates=True,
         vehicle_line="the vehicle's HLOI line", seat="seat number (vehicle_crew)",
         soldier_line="a soldier's HLOI line", spawn="true: a new crewman of the vehicle's side")
def assign_crew(s: Session, vehicle_line, seat, soldier_line=None, spawn=False):
    veh = s.record(vehicle_line)
    if not _is_vehicle(veh):
        raise CommandError(f"line {vehicle_line} isn't a vehicle")
    seat = int(seat)
    vidx = veh.index
    if spawn:
        tpl = _crew_template(s, veh["unit"])
        if not tpl:
            raise CommandError("no crewman to copy - no shipped vehicle of this side has a crew")
        end = next(r.index for r in s.env.records if r.kind == "HLOX")
        s.env.lines.insert(end, tpl["text"])
        s.env._parse()
        if end <= vidx:
            vidx += 1
        new = s.env.record_at(end + 1)
        veh = s.env.record_at(vidx + 1)
        new["script_id"] = max(o["script_id"] for o in s.env.find("HLOI")) + 1
        new["in_play"] = veh["in_play"]
        new["pos"] = tuple(veh["pos"])
        zone = _nearest_zone(s, "HLOI", new["pos"], skip=new.index)
        if zone:
            new["zone"] = zone
        soldier = new
    elif soldier_line not in (None, "", 0):
        soldier = s.record(soldier_line)
        if not _soldier(soldier):
            raise CommandError(f"line {soldier_line} isn't a soldier")
    else:
        soldier = None
    sid = soldier["script_id"] if soldier is not None else None
    if sid is not None:                          # out of every other seat (this vehicle's included)
        for r in list(s.env.find("HLOI")):
            crew = _crew(r)
            kept = [c for c in crew if c[2] != sid]
            if kept != crew and r.index != vidx:
                s.env.set_list(r, "kit_count", kept)
        soldier = s.env.record_at(soldier.index + 1)
        soldier["in_play"] = s.env.record_at(vidx + 1)["in_play"]   # the crew starts with its vehicle
    veh = s.env.record_at(vidx + 1)
    entries = [c for c in _crew(veh) if c[1] != seat and c[2] != sid]
    if sid is not None:
        entries.append((str(soldier["unit"]), seat, sid))
    entries.sort(key=lambda c: c[1])
    veh = s.env.set_list(veh, "kit_count", entries)
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return {**_crew_card(s, veh), "spawned": soldier.index + 1 if spawn else None}


# ======================================================================================== sound audition ====
_SCH, _WAV = {}, {}
_SOUND_LOCK = threading.Lock()
HERO_BANKS = {"HERO01": "Bradley", "HERO02": "Ramirez", "HERO03": "Jones", "HERO04": "Foley"}


def _sch(s: Session, level):
    from conflict_ds_tools import sound
    if not s.game_dir:
        raise CommandError("game folder unknown - start the editor with --game (the sounds are in <level>.sch there)")
    path = _find_file(s.game_dir, f"{level}.SCH")
    if not path:
        raise CommandError(f"no {level.lower()}.sch in the game folder")
    t = os.path.getmtime(path)
    with _SOUND_LOCK:
        hit = _SCH.get(path)
        if hit and hit[0] == t:
            return hit[1], hit[2]
    cl = sound.chunks(open(path, "rb").read())
    have = sound.samples(cl)
    with _SOUND_LOCK:
        _SCH[path] = (t, cl, have)
    return cl, have


def _pick(chunks):
    """The sample in the language to audition: shared (-1), then English (0), then any."""
    from conflict_ds_tools import sound
    for lang in (-1, 0):
        for c in chunks:
            if sound.sample_language(c) == lang:
                return c
    return chunks[0]


def _sample_chunk(s: Session, level, name=None, bank=None, line=None):
    from conflict_ds_tools import sound
    cl, have = _sch(s, level)
    if bank:
        b = sound.bank_chunk(cl, bank)
        if b is None:
            raise CommandError(f"bank {bank} isn't in {level.lower()}.sch")
        order = sound.bank_samples(b, have)
        n = int(line or 1)
        if not 1 <= n <= len(order):
            raise CommandError(f"{bank} has lines 1-{len(order)}")
        sid = order[n - 1]
    else:
        stem = os.path.splitext(os.path.basename(str(name or "")))[0]
        if not stem:
            raise CommandError("no sound name")
        sid = int(stem, 16) if re.fullmatch(r"0x[0-9A-Fa-f]+", stem) else sound.elf_hash(stem)
    if sid not in have:
        raise CommandError(f"{name or bank} isn't in {level.lower()}.sch")
    return sid, _pick(have[sid])


def sample_wav(s: Session, level, name=None, bank=None, line=None):
    """A sample of a level's sound cache as WAV bytes (decoded from the game's ADPCM; cached)."""
    from conflict_ds_tools import sound
    level = str(level or s.level or "").upper()
    key = (level, str(name or "").upper(), str(bank or "").upper(), int(line or 0))
    with _SOUND_LOCK:
        if key in _WAV:
            return _WAV[key]
    sid, chunk = _sample_chunk(s, level, name, bank, line)
    if chunk[:4] == b"PFST":
        raise CommandError("a streamed sound (cutscene / speech file) - only cached samples can be auditioned")
    _sid, rate, bits, data = sound.sample_info(chunk)
    pcm = sound.decode(data, bits)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(sound.pcm_bytes(pcm))
    out = buf.getvalue()
    with _SOUND_LOCK:
        if len(_WAV) > 96:
            _WAV.pop(next(iter(_WAV)))
        _WAV[key] = out
    return out


def _audio_url(level, name=None, bank=None, line=None):
    from urllib.parse import quote
    if bank:
        return f"/audio/{quote(level)}/{quote(bank)}/{int(line)}.wav"
    return f"/audio/{quote(level)}/{quote(os.path.splitext(os.path.basename(name))[0])}.wav"


@command("A sound to audition: an ambient sound (ENVSND 'sound', e.g. 1BaseKlaxon.psf) or a voice bank's line; its "
         "length, rate and the editor URL that plays it as WAV (GET /audio/<LEVEL>/<name>.wav or "
         "/audio/<LEVEL>/<BANK>/<line>.wav).", name="sample file name", bank="voice bank (Bradley, Foley ...)",
         line="1-based line of the bank", level="level whose sound cache (default: the open one)")
def sound_info(s: Session, name=None, bank=None, line=None, level=None):
    from conflict_ds_tools import sound
    level = str(level or s.level or "").upper()
    if not level:
        raise CommandError("no level open")
    sid, chunk = _sample_chunk(s, level, name, bank, line)
    _sid, rate, bits, data = sound.sample_info(chunk)
    streamed = chunk[:4] == b"PFST"
    samples = len(data) // 16 * (30 if bits == 4 else 15)
    return {"id": "0x%08X" % sid, "rate": rate, "seconds": None if streamed else round(samples / max(rate, 1), 2),
            "language": sound.sample_language(chunk), "streamed": streamed,
            "url": None if streamed else _audio_url(level, name, bank, line)}


@command("The squad voice banks in a level's sound cache (Bradley, Foley, Jones, Ramirez - which are there depends on "
         "the level) with their lines (ModKit sound.VOICE_LINES names).", level="level (default: the open one)")
def voice_banks(s: Session, level=None):
    from conflict_ds_tools import sound
    level = str(level or s.level or "").upper()
    cl, have = _sch(s, level)
    out = []
    for hero in ("Bradley", "Foley", "Jones", "Ramirez"):
        b = sound.bank_chunk(cl, hero)
        if b is None:
            continue
        n = len(sound.bank_samples(b, have))
        out.append({"bank": hero, "lines": [{"line": i, "name": sound.VOICE_LINES.get(i, f"line_{i}"),
                                              "url": _audio_url(level, bank=hero, line=i)} for i in range(1, n + 1)]})
    return out
