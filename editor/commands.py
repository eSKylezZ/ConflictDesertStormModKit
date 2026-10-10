"""Editor command layer: every edit is a named command with JSON arguments, undoable, and the only way the UI (and
later the MCP server) changes a level.

A Session holds one open level: its scene (conflict_ds_tools.env.EnvFile) and AI routes (routes.RouteFile), read from
an extracted game folder (the layout ds_extract.py writes: one folder per archive). Records are addressed by their
1-based line in the scene file - the stable handle shown in the UI and given to MCP clients; a command that inserts
or deletes lines returns the new line numbers.

Coordinates in commands are game units (centimetres, left-handed, Y up), as in the scene file.
"""
from __future__ import annotations

import copy as _copy
import json
import re
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools.archive import archive_hash  # noqa: E402
import importlib  # noqa: E402
import conflict_ds_tools.env as _env_module  # noqa: E402
import conflict_ds_tools.terrain as _terrain_module  # noqa: E402
import conflict_ds_tools.roads as _roads_module  # noqa: E402
if "commands" in sys.modules and sys.modules["commands"].__dict__.get("EnvFile") is not None:
    importlib.reload(_env_module)   # the server reloaded this file: pick up field-layout changes in env.py too
    importlib.reload(_terrain_module)
    importlib.reload(_roads_module)
from conflict_ds_tools.env import EnvFile  # noqa: E402
from conflict_ds_tools.routes import RouteFile  # noqa: E402

COMMANDS = {}  # name -> (function, description, {arg: description}, mutates)
_CHANGES_VIEW = {"open_level", "open_mission", "undo", "redo", "history_jump", "save_mission", "save_mod", "save_script"}   # not "mutates" (no undo step) but a new view


def command(description, mutates=False, **args):
    def register(fn):
        COMMANDS[fn.__name__] = (fn, description, args, mutates)
        return fn
    return register


class CommandError(Exception):
    pass


def _find_file(folder, name):
    """A file in an extracted archive folder by name, or as _<HASH>.* when it was extracted without a name."""
    for f in os.listdir(folder):
        if f.upper() == name.upper():
            return os.path.join(folder, f)
    hashed = glob.glob(os.path.join(folder, "_%08X.*" % archive_hash(name.upper())))
    return hashed[0] if hashed else None


class Session:
    def __init__(self, data_dir, game_dir=None):
        self.data_dir = os.path.abspath(data_dir)
        self.game_dir = game_dir
        self.level = None          # e.g. "MISSION1"
        self.folder = None         # extracted folder of its archive
        self.env: EnvFile | None = None
        self.routes: RouteFile | None = None
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.layers = {}           # script id -> layer name
        self.layer_start = {}      # layer name -> "hidden" (waits for game.show_layer, the default) / "shown"
        self.mission = None        # (mod folder, NAME) once saved / opened as a mission
        self.terrains = {}         # model name -> conflict_ds_tools.terrain.Terrain (placed by an ENVI record)
        self.roads = {}            # model name -> conflict_ds_tools.roads.Road (placed by an ENVI record)
        self.kits = {}             # KIT_<MODEL>_<n> -> {"level", "source", "index", "centre"} (kits.py pieces)
        self.objectives, self.rules, self.patrols, self.objectives_win = [], [], {}, False   # logic.py

    # --- undo: a snapshot of the scene lines and the route markers before each change ---------------------------
    def _snapshot(self):
        return (list(self.env.lines), _copy.deepcopy(self.routes.markers) if self.routes else None, dict(self.layers),
                dict(self.layer_start), {k: t.dumps() for k, t in getattr(self, "terrains", {}).items()},
                {k: r.dumps() for k, r in getattr(self, "roads", {}).items()}, json.dumps(logic.extras(self)))

    def _restore(self, snap):
        lines, markers, layers, starts = snap[:4]
        if len(snap) > 4:
            from conflict_ds_tools.terrain import Terrain
            self.terrains = {k: Terrain.from_json(json.loads(v)) for k, v in snap[4].items()}
        if len(snap) > 5:
            from conflict_ds_tools.roads import Road
            self.roads = {k: Road.from_json(json.loads(v)) for k, v in snap[5].items()}
        if len(snap) > 6:
            logic.set_extras(self, json.loads(snap[6]))
        self.layers, self.layer_start = dict(layers), dict(starts)
        self.env.lines = list(lines)
        self.env._parse()
        if self.routes is not None and markers is not None:
            self.routes.markers = _copy.deepcopy(markers)

    def call(self, name, args=None):
        if self.env is not None and type(self.env) is not EnvFile:   # env.py reloaded: re-read with the new layout
            self.env.__class__ = EnvFile
            self.env._parse()
        for k, t in list(getattr(self, "terrains", {}).items()):      # terrain.py reloaded: patches onto the new class
            if type(t) is not _terrain_module.Terrain:
                self.terrains[k] = _terrain_module.Terrain.from_json(t.to_json())
        for k, r in list(getattr(self, "roads", {}).items()):         # roads.py reloaded
            if type(r) is not _roads_module.Road:
                self.roads[k] = _roads_module.Road.from_json(r.to_json())
        if name not in COMMANDS:
            raise CommandError(f"unknown command {name!r}")
        fn, _, _, mutates = COMMANDS[name]
        if mutates:
            if self.env is None:
                raise CommandError("no level open")
            before = self._snapshot()
            subject = _subject(self, args or {})     # named before the change: a delete removes the record
            try:
                result = fn(self, **(args or {}))
            except Exception:
                self._restore(before)
                raise
            title = _action_title(name, subject, result)
            if isinstance(result, dict) and "action" not in result:
                result = {**result, "action": title}
            self.undo_stack.append((title, before, name))
            self.redo_stack.clear()
            self.dirty = True
            self.revision = getattr(self, "revision", 0) + 1   # the browser redraws when someone else changed it
            return result
        result = fn(self, **(args or {}))
        if name in _CHANGES_VIEW:
            self.revision = getattr(self, "revision", 0) + 1
        return result

    def record(self, line):
        if self.env is None:
            raise CommandError("no level open")
        try:
            return self.env.record_at(int(line))
        except KeyError:
            raise CommandError(f"no record on line {line}") from None


# --- action titles for the undo history ("Move T-72 Battle Tank", "Paint Minefield") -----------------------------
# A command may name its change itself (an "action" string in its result); otherwise: a verb per command + the record
# its line argument points at, read before the command runs.
_ACTION_VERBS = {"set_field": "Change", "move": "Move", "duplicate": "Duplicate", "delete": "Delete", "paste": "Paste",
                 "place": "Place", "set_layer": "Set Layer of", "set_ai_profile": "Set AI Profile of",
                 "assign_crew": "Change Crew of", "set_patrol": "Set Patrol of", "set_camera": "Change Camera",
                 "set_objectives": "Edit Objectives", "add_rule": "Add Event Rule", "set_rules": "Edit Event Rules",
                 "delete_rule": "Delete Event Rule", "set_atmosphere": "Change Atmosphere",
                 "route_add": "Add Route Marker", "route_link": "Link Route Markers",
                 "route_unlink": "Unlink Route Markers", "route_move": "Move Route Marker",
                 "route_delete": "Delete Route Marker", "terrain_new": "New Terrain Patch",
                 "terrain_brush": "Sculpt Terrain", "terrain_noise": "Add Hills", "terrain_set": "Change Terrain",
                 "road_new": "New Road", "road_set": "Change Road", "kit_place": "Place Kit Piece",
                 "fix_hidden": "Fix Hidden Objects", "fix_crews": "Fix Vehicle Crews", "fix_zones": "Fix Zones"}
_WITH_SUBJECT = {"set_field", "move", "duplicate", "delete", "set_layer", "set_ai_profile", "assign_crew",
                 "set_patrol", "set_camera"}
_UNIT_PREFIX = re.compile(r"^(?:US|IR|UK|NU|RU)_(?:VEH_|WPN_)?", re.I)
_FRIENDLY = {"HMG01_M2HB": "M2HB .50 Cal", "HMG02_NSV": "NSV Heavy MG", "US_WPN_MINEANTITANK": "Anti-Tank Mine",
             "LASER_DESIGNATOR": "Laser Designator"}


def pretty_name(rec) -> str:
    """A record as people call it: its unit without the side / kind prefix, words split ("IR_VEH_T-72_BattleTank" ->
    "T-72 Battle Tank"), else its model or name."""
    raw = ""
    for key in ("unit", "model", "name"):
        if key in rec.fields:
            v = str(rec[key] or "")
            if v and v not in ("-1", "NONE"):
                raw = v
                break
    if not raw:
        return rec.kind
    if raw.upper() in _FRIENDLY:
        return _FRIENDLY[raw.upper()]
    raw = _UNIT_PREFIX.sub("", raw)
    raw = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw.replace("_", " "))
    words = [w if any(c.isdigit() for c in w) or (w.isupper() and len(w) <= 4) else w.capitalize() for w in raw.split()]
    return " ".join(words) or rec.kind


def _subject(s, args):
    line = args.get("line", args.get("vehicle_line"))
    if line is None and isinstance(args.get("lines"), list):
        if len(args["lines"]) > 1:
            return f"{len(args['lines'])} Records"
        line = args["lines"][0] if args["lines"] else None
    if line is None or s.env is None:
        return ""
    try:
        return pretty_name(s.env.record_at(int(line)))
    except (KeyError, ValueError, TypeError, IndexError):
        return ""


def _action_title(name, subject, result):
    if isinstance(result, dict) and isinstance(result.get("action"), str):
        return result["action"]
    verb = _ACTION_VERBS.get(name) or name.replace("_", " ").title().replace("Ai ", "AI ")
    return f"{verb} {subject}" if subject and name in _WITH_SUBJECT else verb


def _vec(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise CommandError("expected [x, y, z]")
    return tuple(float(c) for c in v)


# ------------------------------------------------------------------------------------------------- queries --
@command("Levels in the data folder that have a scene file.")
def list_levels(s: Session):
    out = []
    for d in sorted(os.listdir(s.data_dir)):
        folder = os.path.join(s.data_dir, d)
        if not os.path.isdir(folder) or d.startswith("_"):
            continue
        env = _find_file(folder, d.upper() + ".ENV")
        if env and open(env, "rb").read(7) == b"Version":
            out.append(d.upper())
    return out


@command("Open a level's scene and routes.", level="level name, e.g. MISSION1")
def open_level(s: Session, level):
    level = level.upper()
    folder = os.path.join(s.data_dir, level.lower())
    if not os.path.isdir(folder):
        folder = os.path.join(s.data_dir, level)
    path = _find_file(folder, level + ".ENV") if os.path.isdir(folder) else None
    if not path:
        raise CommandError(f"no scene for {level} in {s.data_dir}")
    s.level, s.folder = level, folder
    s.env = EnvFile.load(path)
    rmd = _find_file(folder, level + ".RMD")
    s.routes = RouteFile.load(rmd) if rmd else None
    s.undo_stack.clear(), s.redo_stack.clear()
    s.dirty = False
    s.layers, s.layer_start, s.mission = {}, {}, None
    s.terrains = {}
    s.roads = {}
    s.kits = {}
    s.blank = False
    logic.set_extras(s, None)
    s._base_objectives = None
    # the base mission's own data (editor/level_base.json, texts from the player's game): its objectives, briefing,
    # music and the objects its DLL needs - resolved now so the panels start filled
    s.base_objectives = logic.base_objectives(s)
    return {"level": level, **s.env.summary(), "route_markers": len(s.routes.markers) if s.routes else 0,
            "base_objectives": len(s.base_objectives), "base_targets": len(logic.base_targets(s))}


@command("Everything the viewport draws: world zones, props, objects, waypoints, regions, cameras, sounds, lights, "
         "route markers.")
def scene(s: Session):
    _spread_lifts(s)
    if s.env is None:
        raise CommandError("no level open")
    out = {"level": s.level, "mission": list(s.mission) if s.mission else None, "zones": [], "props": [], "objects": [], "waypoints": [], "regions": [], "cameras": [],
           "sounds": [], "lights": [], "routes": []}
    for r in s.env.records:
        k, line = r.kind, r.index + 1
        try:
            if k == "ENVI" and r.fields:
                ground = getattr(s, "terrains", {}).get(str(r["model"]).upper())
                if r["type"] == 5 and ground is not None and ground.zone:
                    out["props"].append({"line": line, "name": r["name"], "model": r["model"], "pos": list(ground.origin),
                                         "rot": (0, 0, 0), "scale": (1, 1, 1), "terrain": ground.to_json(),
                                         "ground": True})
                elif r["type"] == 5 and r["parent"].upper() == "ROOT":
                    out["zones"].append({"line": line, "name": r["name"], "model": r["model"], "pos": r["pos"],
                                         "rot": r["rot"], "scale": r["scale"]})   # MP_MISSION1's sits at (600,10,-16000)
                elif r["type"] == 0:
                    out["props"].append({"line": line, "name": r["name"], "model": r["model"], "pos": r["pos"],
                                         "rot": r["rot"], "scale": r["scale"] if "scale.x" in r.fields else (1, 1, 1)})
                    t = getattr(s, "terrains", {}).get(str(r["model"]).upper())
                    if t:
                        out["props"][-1]["terrain"] = t.to_json()
                    kit = getattr(s, "kits", {}).get(str(r["model"]).upper())
                    if kit:
                        out["props"][-1]["pivot"] = list(_kit_centre_world(r, kit))
                        out["props"][-1]["kit"] = {"source": kit["source"], "index": kit["index"], "level": kit["level"],
                                                   "size": kit.get("size")}
                    road = getattr(s, "roads", {}).get(str(r["model"]).upper())
                    if road:
                        pos, _n, uv, tris = road.strip()
                        out["props"][-1]["road"] = {**road.to_json(), "mesh": {"pos": pos, "uv": uv, "tris": tris}}
            elif k == "HLOI" and r.fields:
                out["objects"].append({"line": line, "model": r["model"], "unit": r["unit"], "stat": r["stat"],
                                       "script_id": r["script_id"], "pos": r["pos"], "rot": r["rot"],
                                       "active": r["in_play"], "layer": s.layers.get(r["script_id"], ""),
                                       "cutscene": bool(r["flags"] & 0x20000)})
            elif k == "NEWENVWAYPT" and r.fields:
                out["waypoints"].append({"line": line, "id": r["id"], "name": r["name"], "pos": r["pos"]})
            elif k == "REGN" and r.fields:
                reg = {"line": line, "name": r["name"], "type": r["type"], "pos": r["pos"]}
                if r["type"] == 1:
                    reg["radius"] = r["radius"]
                else:
                    reg.update(rot=r["rot"], min=r["min"], max=r["max"])
                out["regions"].append(reg)
            elif k.startswith("CAM") and r.fields:
                out["cameras"].append({"line": line, "kind": k, "name": r["name"], "pos": r["pos"]})
            elif k == "ENVSND" and r.fields:
                out["sounds"].append({"line": line, "name": r["name"], "sound": r["sound"], "pos": r["pos"],
                                      "min_distance": r["min_distance"], "max_distance": r["max_distance"]})
        except KeyError:
            continue
    if s.routes:
        out["routes"] = [m.as_dict(i) for i, m in enumerate(s.routes.markers)]
    logic.decorate_scene(s, out)
    interactive.decorate_scene(s, out)
    return out


@command("All fields of one record.", line="1-based line in the scene file")
def get_record(s: Session, line):
    r = s.record(line)
    return {"line": r.index + 1, "kind": r.kind, "section": r.section, "text": r.text, "fields": r.as_dict()}


@command("Records of a kind matching field values.", kind="keyword, e.g. HLOI", match="{field: value}", limit="max results")
def find(s: Session, kind=None, match=None, limit=200):
    if s.env is None:
        raise CommandError("no level open")
    return [r.as_dict() for r in s.env.find(kind, **(match or {}))][:int(limit)]


@command("Undo / redo history and whether there are unsaved changes.")
def history(s: Session):
    return {"undo": [e[0] for e in s.undo_stack], "redo": [e[0] for e in s.redo_stack], "dirty": s.dirty,
            "timeline": _timeline(s),
            "revision": getattr(s, "revision", 0),
            "level": s.level, "mission": list(s.mission) if s.mission else None}


# -------------------------------------------------------------------------------------------------- layers --
def _apply_start(s: Session, name):
    """Objects of a layer: active 0 when it starts hidden, 1 when it starts shown."""
    want = 1 if s.layer_start.get(name, "hidden") == "shown" else 0
    for r in s.env.find("HLOI"):
        if s.layers.get(r["script_id"]) == name and r["in_play"] != want:
            r["in_play"] = want


@command("Put objects into a layer, or take them out with an empty name (back in play from the start). A new layer "
         "starts hidden (its objects wait for game.show_layer); see set_layer_start.", mutates=True,
         lines="HLOI record line(s)", layer="layer name or ''")
def set_layer(s: Session, lines=None, layer="", line=None):
    targets = [line] if line is not None else (lines if isinstance(lines, list) else [lines])
    layer = (layer or "").strip()
    done = []
    by_id = {h["script_id"]: h.index + 1 for h in s.env.find("HLOI")}
    for ln in list(targets):                   # a vehicle's crew goes with it (else it appears without the tank)
        for _, _, cid in _crew(s.record(ln)) if s.record(ln).kind == "HLOI" else []:
            if cid in by_id and by_id[cid] not in targets:
                targets.append(by_id[cid])
    for ln in targets:
        r = s.record(ln)
        if r.kind != "HLOI":
            raise CommandError(f"line {ln}: only objects (HLOI) can be in a layer - the game shows them by script id")
        sid = r["script_id"]
        if layer:
            s.layers[sid] = layer
        else:
            s.layers.pop(sid, None)
            r["in_play"] = 1
        done.append(sid)
    if layer:
        s.layer_start.setdefault(layer, "hidden")
        _apply_start(s, layer)
    return {"script_ids": done, "layer": layer}


@command("Whether a layer's objects start hidden (waiting for game.show_layer) or shown (in play; game.hide_layer "
         "takes them out).", mutates=True, layer="layer name", start="hidden / shown")
def set_layer_start(s: Session, layer, start):
    if start not in ("hidden", "shown"):
        raise CommandError("start: hidden or shown")
    if layer not in set(s.layers.values()):
        raise CommandError(f"no layer {layer!r}")
    s.layer_start[layer] = start
    _apply_start(s, layer)
    return {"layer": layer, "start": start}


@command("Rename a layer (or merge it into another one).", mutates=True, layer="layer name", to="new name")
def rename_layer(s: Session, layer, to):
    to = (to or "").strip()
    if not to:
        raise CommandError("the new name is empty")
    ids = [sid for sid, n in s.layers.items() if n == layer]
    if not ids:
        raise CommandError(f"no layer {layer!r}")
    for sid in ids:
        s.layers[sid] = to
    s.layer_start.setdefault(to, s.layer_start.pop(layer, "hidden"))
    _apply_start(s, to)
    return {"layer": to, "objects": len(ids)}


@command("Delete a layer: its objects go back in play from the start.", mutates=True, layer="layer name")
def delete_layer(s: Session, layer):
    ids = {sid for sid, n in s.layers.items() if n == layer}
    if not ids:
        raise CommandError(f"no layer {layer!r}")
    for r in s.env.find("HLOI"):
        if r["script_id"] in ids:
            r["in_play"] = 1
    for sid in ids:
        del s.layers[sid]
    s.layer_start.pop(layer, None)
    return {"deleted": layer, "objects": len(ids)}


@command("Layers: start state and the objects' script ids and lines.")
def list_layers(s: Session):
    out = {}
    line_of = {r["script_id"]: r.index + 1 for r in s.env.find("HLOI")} if s.env else {}
    for sid, name in sorted(s.layers.items()):
        e = out.setdefault(name, {"start": s.layer_start.get(name, "hidden"), "script_ids": [], "lines": []})
        e["script_ids"].append(sid)
        if sid in line_of:
            e["lines"].append(line_of[sid])
    return out


# --------------------------------------------------------------------------------------------------- edits --
@command("Set one field of a record (numbers keep the token's style).", mutates=True,
         line="record line", field="field name (vectors: pos, rot, scale)", value="new value")
def set_field(s: Session, line, field, value):
    r = s.record(line)
    kit = getattr(s, "kits", {}).get(str(r.get("model")).upper()) if r.kind == "ENVI" else None
    if kit and field == "rot" and isinstance(value, list):
        from conflict_ds_tools import kits as _k
        centre = _kit_centre_world(r, kit)
        r["rot"] = _vec(value)
        r["pos"] = _k.placement(kit["centre"], centre, r["rot"][1])
        return get_record(s, line)
    if r.kind == "ENVI" and field == "model" and isinstance(value, str):
        unit = _vehicle_models(s).get(value.upper())
        if unit:
            raise CommandError(f"{value} is a vehicle ({unit}) - as a zone prop it crashes the game. Place it from the "
                               f"Library's objects instead (or use a wreck model such as {value}DEAD if there is one)")
    if isinstance(value, list):
        value = _vec(value)
    try:
        r[field] = value
    except (KeyError, ValueError) as e:
        raise CommandError(str(e)) from None
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return get_record(s, line)


@command("Move a record (props, objects, waypoints, regions, cameras, sounds) to a position.", mutates=True,
         line="record line", pos="[x, y, z] game units")
def move(s: Session, line, pos):
    r = s.record(line)
    t = getattr(s, "terrains", {}).get(str(r.get("model")).upper()) if r.kind == "ENVI" else None
    if t is not None and t.zone:                # a level's ground: world-space vertices around its origin
        t.origin = tuple(_vec(pos))
        return get_record(s, line)
    kit = getattr(s, "kits", {}).get(str(r.get("model")).upper()) if r.kind == "ENVI" else None
    if kit:                                     # a kit piece: pos = where its centre goes (its pivot is the origin)
        from conflict_ds_tools import kits as _k
        return set_field(s, line, "pos", list(_k.placement(kit["centre"], _vec(pos), r["rot"][1])))
    if r.kind == "SWITCH" and r.fields:            # its use region (switch_add's box) moves with it
        old = r["pos"]
        for reg in s.env.find("REGN", name=str(r["region"])):
            if str(reg["name"]).upper().endswith("_USE"):
                reg["pos"] = tuple(p + n - o for p, n, o in zip(reg["pos"], _vec(pos), old))
    return set_field(s, line, "pos", list(_vec(pos)))


@command("Copy a record to the end of its section, optionally at another position; new HLOI copies get the next free "
         "script id.", mutates=True, line="record line", pos="optional [x, y, z]")
def duplicate(s: Session, line, pos=None):
    r = s.record(line)
    changes = {}
    if pos is not None:
        changes["pos"] = _vec(pos)
    if r.kind == "HLOI":
        changes["script_id"] = max(o["script_id"] for o in s.env.find("HLOI")) + 1
    crew = _crew_texts(s.env, r) if r.kind == "HLOI" else []
    new = s.env.duplicate(r, **changes)
    new = _attach_crew(s, new, crew)
    return {"line": new.index + 1, **({"script_id": changes["script_id"]} if "script_id" in changes else {})}


# ---------------------------------------------------------------------------------------------- copy / paste --
# The clipboard belongs to the editor (module level), so records copied in one level paste into another.
_CLIPBOARD = []
_PASTE_END = {"HLOI": "HLOX", "ENVI": "ENDI", "NEWENVWAYPT": "ENDWAYPTS", "REGN": "ENDREGIONS", "ENVSND": "ENDENVS",
              "CAMFREE": "ENDC", "CAMTARGPOS": "ENDC", "CAMTARGOBJ": "ENDC", "CAMCHASEOBJ": "ENDC", "EMITTER": "</EMIT>"}


@command("Copy records (objects, props, waypoints, regions, sounds, cameras) to the editor's clipboard.",
         lines="record line(s)")
def copy(s: Session, lines):
    global _CLIPBOARD
    out = []
    for ln in lines if isinstance(lines, list) else [lines]:
        r = s.record(ln)
        if r.kind not in _PASTE_END or (r.kind == "ENVI" and (r.get("type") != 0 or r.children)):
            raise CommandError(f"line {ln}: {r.kind} records can't be copied (world pieces stay with their zone)")
        out.append({"kind": r.kind, "text": r.text, "pos": list(r["pos"]), "level": s.level,
                    "crew": _crew_texts(s.env, r) if r.kind == "HLOI" else []})
    _CLIPBOARD = out
    return {"copied": len(out), "kinds": sorted({e["kind"] for e in out})}


def _unique(s: Session, kind, field, base):
    taken = {str(r.get(field)) for r in s.env.find(kind)}
    stem = base.rstrip("0123456789_") or base
    n = 2
    name = base
    while name in taken:
        name = f"{stem}_{n}"
        n += 1
    return name


@command("Paste the clipboard: the first copied record goes to pos, the others keep their offsets; objects get new "
         "script ids, waypoints new ids, props / regions / sounds / cameras unique names, objects and props the zone of "
         "the nearest record.", mutates=True, pos="[x, y, z] where the first copied record goes")
def paste(s: Session, pos):
    if not _CLIPBOARD:
        raise CommandError("the clipboard is empty - copy something first (Ctrl+C)")
    return {"lines": _paste_entries(s, _CLIPBOARD, _vec(pos))}


def _turn(offset, yaw_deg):
    """An x / z offset turned like the game turns a record by rot.y (forward (sin, cos) in x / z, kits.placement)."""
    import math
    a = math.radians(yaw_deg)
    x, y, z = offset
    return (x * math.cos(a) + z * math.sin(a), y, -x * math.sin(a) + z * math.cos(a))


def _paste_entries(s: Session, entries, pos, yaw=0.0, anchor=None):
    """Insert copied records ({kind, text, pos, crew}) so that `anchor` (default: the first one's position) lands on
    pos, the group turned by yaw degrees about it; new script ids / waypoint ids / unique names, nearest zones.
    Returns the new records' lines."""
    anchor = anchor if anchor is not None else entries[0]["pos"]
    yaw = float(yaw or 0.0)
    new_lines = []
    for e in entries:
        end = _PASTE_END[e["kind"]]
        at = next((r.index for r in s.env.records if r.kind == end), None)
        if at is None:
            raise CommandError(f"this scene has no {end} section")
        s.env.lines.insert(at, e["text"])
        s.env._parse()
        new_lines = [l + 1 if l >= at + 1 else l for l in new_lines]   # earlier pastes below the insertion move
        r = s.env.record_at(at + 1)
        off = _turn([c - a for c, a in zip(e["pos"], anchor)], yaw)
        target = tuple(p + o for p, o in zip(pos, off))
        r["pos"] = target
        rot = r.get("rot") if yaw else None
        if rot is not None:
            r["rot"] = (rot[0], round((rot[1] + yaw) % 360.0, 4), rot[2])
        if r.kind == "HLOI":
            r["script_id"] = max(o["script_id"] for o in s.env.find("HLOI")) + 1
            zone = _nearest_zone(s, "HLOI", target, skip=r.index)
            if zone:
                r["zone"] = zone
            s.inserted_lines = []
            crew = [dict(c, offset=list(_turn(c["offset"], yaw))) for c in e.get("crew") or []]
            r = _attach_crew(s, r, crew)
            for at_line in s.inserted_lines:        # crew lines before earlier pastes move them down
                new_lines = [l + 1 if l >= at_line + 1 else l for l in new_lines]
        elif r.kind == "ENVI":
            r["name"] = _unique(s, "ENVI", "name", r["name"])
            zone = _nearest_zone(s, "ENVI", target, skip=r.index)
            if zone:
                r["parent"] = zone
        elif r.kind == "NEWENVWAYPT":
            r["id"] = max(w["id"] for w in s.env.find("NEWENVWAYPT")) + 1
            r["name"] = _unique(s, "NEWENVWAYPT", "name", r["name"])
        elif "name" in r.fields:
            r["name"] = _unique(s, r.kind, "name", r["name"])
        new_lines.append(r.index + 1)
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return new_lines


# ----------------------------------------------------------------------------------------------- vehicle crews --
# A vehicle's HLOI "kit" list is its crew: (unit, seat, script id of the soldier HLOI that starts in that seat) -
# Mission 4's T-62: ("Iraqi_Conscript", 1, 70), ("Iraqi_Conscript", 2, 71). At level start each soldier mounts the
# vehicle (FUN_00437cd0 -> mount FUN_00433f00). A crew id that points at something else (a vehicle placed from another
# level kept its ids: in Mission 1, 70 = the cooler keys) makes the game treat that object as a soldier and crash
# (0x433FC2, movement state table). So vehicles travel with their crew, re-numbered.
# Tripod guns (VEHICLE.TXT rows with stat Gunmount) keep their gunner in the same crew list as vehicles: Mission 8's
# NSVs hold ("Iraqi_Republican", seat 1, script id) - pasted without re-numbering they would point at other objects.
GUN_MOUNTS = ("HMG01_M2HB", "HMG02_NSV")


def _is_vehicle(rec):
    unit = str(rec.get("unit") or "").upper()
    return rec.kind == "HLOI" and ("_VEH_" in unit or unit in GUN_MOUNTS)


def _crew(rec):
    if not _is_vehicle(rec):
        return []
    return [(rec[f"kit{k}.item"], rec[f"kit{k}.category"], rec[f"kit{k}.amount"]) for k in range(rec["kit_count"])]


def _crew_texts(env, rec):
    """The crew's soldier records (text, offset from the vehicle) from the scene the vehicle is in."""
    ids = None
    out = []
    for unit, seat, cid in _crew(rec):
        if ids is None:
            ids = {h["script_id"]: h for h in env.find("HLOI")}
        c = ids.get(cid)
        if c is not None and str(c.get("unit")).upper() == str(unit).upper():
            out.append({"seat": seat, "unit": unit, "text": c.text,
                        "offset": [a - b for a, b in zip(c["pos"], rec["pos"])]})
    return out


def _attach_crew(s: Session, vehicle, crew):
    """Give a placed / pasted vehicle its own crew: the soldiers in `crew` are added with new script ids next to it
    and the vehicle's crew list points at them (entries without a soldier are dropped). Returns the vehicle record."""
    if not _is_vehicle(vehicle):
        return vehicle
    vline = vehicle.index
    entries = []
    for c in crew:
        end = next(r.index for r in s.env.records if r.kind == "HLOX")
        s.env.lines.insert(end, c["text"])
        s.inserted_lines = getattr(s, "inserted_lines", []) + [end]
        s.env._parse()
        if end <= vline:
            vline += 1
        soldier = s.env.record_at(end + 1)
        soldier["script_id"] = max(o["script_id"] for o in s.env.find("HLOI")) + 1
        soldier["in_play"] = s.env.record_at(vline + 1)["in_play"]   # the crew starts with its vehicle
        soldier["pos"] = tuple(p + o for p, o in zip(s.env.record_at(vline + 1)["pos"], c["offset"]))
        zone = _nearest_zone(s, "HLOI", soldier["pos"], skip=soldier.index)
        if zone:
            soldier["zone"] = zone
        entries.append((c["unit"], c["seat"], soldier["script_id"]))
    vehicle = s.env.record_at(vline + 1)
    if entries != _crew(vehicle):
        vehicle = s.env.set_list(vehicle, "kit_count", entries)
    return vehicle


def _base_ids(s: Session):
    """Script ids of the objects the base level itself has (theirs keep the level's own start state)."""
    path = _find_file(s.folder, s.level + ".ENV") if s.folder else None
    if not path:
        return set()
    return {r["script_id"] for r in EnvFile.load(path).find("HLOI")}


@command("Put objects that wait for the script but are in no layer (placed from levels that spawn them later) in play "
         "from the start.", mutates=True)
def fix_hidden(s: Session):
    base, fixed = _base_ids(s), []
    for r in list(s.env.find("HLOI")):
        if r["in_play"] == 0 and r["script_id"] not in base and r["script_id"] not in s.layers:
            r["in_play"] = 1
            fixed.append(r.index + 1)
    return {"fixed": fixed}


@command("Remove crew entries of vehicles that don't point at a soldier of the right unit in this scene (they crash "
         "the game at level start).", mutates=True)
def fix_crews(s: Session):
    ids = {h["script_id"]: h for h in s.env.find("HLOI")}
    fixed = []
    for r in list(s.env.find("HLOI")):
        crew = _crew(r)
        good = [c for c in crew if c[2] in ids and str(ids[c[2]].get("unit")).upper() == str(c[0]).upper()]
        if good != crew:
            s.env.set_list(s.env.record_at(r.index + 1), "kit_count", good)
            fixed.append(r.index + 1)
    return {"fixed": fixed}


@command("Delete a record (and an ENVI's VAMI / ANMI lines).", mutates=True, line="record line")
def delete(s: Session, line):
    r = s.record(line)
    sid = r["script_id"] if r.kind == "HLOI" and "script_id" in r.fields else None
    own_region = None
    if r.kind == "SWITCH" and r.fields and str(r["region"]).upper().endswith("_USE") and not any(
            o is not r and o.fields and o["region"] == r["region"] for o in s.env.find("SWITCH")):
        own_region = str(r["region"])            # the use region switch_add made for it goes too
    s.env.delete(r)
    if own_region:
        for reg in s.env.find("REGN", name=own_region):
            s.env.delete(reg)
    out = {"deleted": int(line)}
    if sid is not None and sid in logic.base_targets(s):
        out["warning"] = (f"Object #{sid} is required by {_base_dll(s, s.level) or 'the base mission'} - its logic for it "
                          "won't work (undo, and hide it in a layer instead)")
    return out


@command("Undo the last change.")
def undo(s: Session):
    if not s.undo_stack:
        raise CommandError("nothing to undo")
    title, snap, *cmd = s.undo_stack.pop()
    s.redo_stack.append((title, s._snapshot(), *cmd))
    s._restore(snap)
    s.dirty = True
    return {"undone": title}


@command("Redo the last undone change.")
def redo(s: Session):
    if not s.redo_stack:
        raise CommandError("nothing to redo")
    title, snap, *cmd = s.redo_stack.pop()
    s.undo_stack.append((title, s._snapshot(), *cmd))
    s._restore(snap)
    s.dirty = True
    return {"redone": title}


def _timeline(s: Session):
    """Every recorded change in order: the done ones (undo stack, oldest first), then the undone ones (redo stack).
    State index k = the scene after change k (0 = as opened); the current state is len(undo stack)."""
    out = [{"index": i + 1, "title": e[0], "command": e[2] if len(e) > 2 else "", "done": True}
           for i, e in enumerate(s.undo_stack)]
    n = len(out)
    out += [{"index": n + j + 1, "title": e[0], "command": e[2] if len(e) > 2 else "", "done": False}
            for j, e in enumerate(reversed(s.redo_stack))]
    return out


@command("Jump straight to a point of the undo history: index = the state after that many changes (0 = as opened; "
         "history's timeline lists them, the current state is the number of done ones). The changes after it stay "
         "redoable until the next edit.", index="state index 0 .. len(timeline)")
def history_jump(s: Session, index):
    index = int(index)
    total = len(s.undo_stack) + len(s.redo_stack)
    if not 0 <= index <= total:
        raise CommandError(f"index: 0 - {total}")
    steps = 0
    while len(s.undo_stack) > index:
        undo(s)
        steps -= 1
    while len(s.undo_stack) < index:
        redo(s)
        steps += 1
    return {"index": index, "steps": steps, "title": s.undo_stack[-1][0] if s.undo_stack else "(as opened)"}


# -------------------------------------------------------------------------------------------------- routes --
def _routes(s):
    if not s.routes:
        raise CommandError("this level has no route file")
    return s.routes


@command("Add a route marker, linked both ways to the given markers.", mutates=True, pos="[x, y, z]",
         links="marker indices")
def route_add(s: Session, pos, links=()):
    r = _routes(s)
    at = r.hand_count()          # hand-placed markers are a block at the start; generated ones follow (and shift up)
    return {"index": r.insert(at, _vec(pos), [int(l) for l in links])}


def _hand_only(s: Session, index, what):
    r = _routes(s)
    if int(index) >= r.hand_count():
        raise CommandError(f"marker {index} is generated from the walk map - the game rebuilds those from the scene for "
                           f"every mission load, so it can't be {what}. Move or add props to change where the AI walks, "
                           f"or add a hand-placed marker")


@command("Link two route markers (both ways unless one_way).", mutates=True, a="marker", b="marker",
         one_way="only a -> b")
def route_link(s: Session, a, b, one_way=False):
    _routes(s).link(int(a), int(b), both=not one_way)
    return {"a": _routes(s).markers[int(a)].as_dict(int(a))}


@command("Remove the links between two route markers.", mutates=True, a="marker", b="marker")
def route_unlink(s: Session, a, b):
    _routes(s).unlink(int(a), int(b))
    return {"a": _routes(s).markers[int(a)].as_dict(int(a))}


@command("Move a route marker.", mutates=True, index="marker", pos="[x, y, z]")
def route_move(s: Session, index, pos):
    _hand_only(s, index, "moved")
    _routes(s).move(int(index), _vec(pos))
    return _routes(s).markers[int(index)].as_dict(int(index))


@command("Delete a route marker (links to it are dropped, higher indices shift down).", mutates=True, index="marker")
def route_delete(s: Session, index):
    _hand_only(s, index, "deleted")
    _routes(s).delete(int(index))
    return {"deleted": int(index)}


# ------------------------------------------------------------------------------------------------- library --
# Every prop (ENVI type 0) and object (HLOI) type of every shipped scene, as a template record: placing one copies
# that real record (all its AI / kit fields stay valid) and changes position, facing, zone and script id.
_LIBRARY = None


def _library(s: Session):
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY
    entries = {}
    for level in list_levels(s):
        folder = os.path.join(s.data_dir, level.lower())
        path = _find_file(folder, level + ".ENV")
        try:
            env = EnvFile.load(path)
        except (OSError, ValueError):
            continue
        for r in env.records:
            if not r.fields or r.kind not in ("ENVI", "HLOI"):
                continue
            if r.kind == "ENVI":
                if r.get("type") != 0 or r.children:
                    continue
                key = ("prop", r["model"].upper())
                label = r["model"]
            else:
                key = ("object", r["model"].upper(), r["unit"].upper())   # one per type, not per role
                label = f"{r['unit']} ({r['model']})"
            if key not in entries:
                entries[key] = {"id": len(entries), "kind": key[0], "keyword": r.kind, "model": r["model"],
                                "label": label, "level": level, "text": r.text,
                                "unit": r.get("unit"), "stat": r.get("stat"),
                                "script_id": r.get("script_id") if r.kind == "HLOI" else None,
                                "crew": _crew_texts(env, r) if r.kind == "HLOI" else []}
    _LIBRARY = list(entries.values())
    return _LIBRARY


def _models_in_level(s: Session):
    names = set()
    for f in os.listdir(s.folder):
        stem, ext = os.path.splitext(f)
        if ext.upper() == ".EVO":
            names.add(stem.upper())
        elif stem.startswith("_"):
            names.add(stem.upper())          # _HASH: matched through the hash below
    return names


@command("Prop and object types of all shipped levels (templates for place). in_level = the model is in the open "
         "level's archive (others need packing into the mod).", search="filter text", kind="prop / object / all")
def library(s: Session, search="", kind="all"):
    if s.env is None:
        raise CommandError("no level open")
    here = _models_in_level(s)
    q = search.lower()
    out = []
    for e in _library(s):
        if kind != "all" and e["kind"] != kind:
            continue
        if q and q not in (e["label"] + " " + (e["stat"] or "")).lower():
            continue
        m = e["model"].upper()
        in_level = m in here or ("_%08X" % archive_hash(m + ".EVO")) in here
        out.append({k: e[k] for k in ("id", "kind", "model", "label", "level", "unit", "stat")} | {"in_level": in_level})
    return out


def _zones(s: Session):
    """Names a record can hang from: "Root" and every ENVI of the scene (zones such as MIS1_ZONE1-6821, interiors,
    and props - a cigarette pack hangs from its table)."""
    return {"Root"} | {str(r.get("name")) for r in s.env.find("ENVI")}


def _nearest_zone(s: Session, keyword, pos, skip=None):
    """Zone of the nearest record of that kind that is in one of this scene's zones (skip = a record's line index:
    the record just placed or pasted still carries its source level's zone)."""
    field = "zone" if keyword == "HLOI" else "parent"
    zones = _zones(s)
    best, zone = None, None
    for r in s.env.find(keyword):
        if r.index == skip:
            continue
        try:
            p, z = r["pos"], r[field]
        except KeyError:
            continue
        if keyword == "ENVI" and r.get("type") != 0:
            continue
        if str(z) not in zones:
            continue
        d = sum((a - b) ** 2 for a, b in zip(p, pos))
        if best is None or d < best:
            best, zone = d, z
    if zone is None:                            # nothing placed yet (a new level): the level's ground zone
        zone = next((str(r["name"]) for r in s.env.find("ENVI")
                     if r.get("type") == 5 and str(r.get("parent")) == "Root" and r.index != skip), None)
    return zone


@command("Place a library template at a position (new script id for objects, zone of the nearest record).",
         mutates=True, template="library id", pos="[x, y, z]", yaw="facing in degrees",
         scale="optional size factor for props (e.g. 0.9-1.1 for variety)")
def place(s: Session, template, pos, yaw=0.0, scale=None):
    lib = _library(s)
    if not 0 <= int(template) < len(lib):
        raise CommandError("unknown template")
    e = lib[int(template)]
    pos = _vec(pos)
    end = {"HLOI": "HLOX", "ENVI": "ENDI"}[e["keyword"]]
    at = next((r.index for r in s.env.records if r.kind == end), None)
    if at is None:
        raise CommandError(f"the scene has no {end}")
    s.env.lines.insert(at, e["text"])
    s.env._parse()
    new = s.env.record_at(at + 1)
    new["pos"] = pos
    rot = new["rot"]
    new["rot"] = (rot[0], float(yaw) % 360.0, rot[2])
    zone = _nearest_zone(s, e["keyword"], pos, skip=new.index)
    if zone:
        new["zone" if e["keyword"] == "HLOI" else "parent"] = zone
    result = {"line": new.index + 1}
    if e["keyword"] == "HLOI":
        result["script_id"] = new["script_id"] = max(o["script_id"] for o in s.env.find("HLOI")) + 1
        new["in_play"] = 1                     # templates from levels that spawn them later start hidden
        new = _attach_crew(s, new, e.get("crew") or [])
        result["line"] = new.index + 1
    elif "name" in new.fields:
        new["name"] = f"{e['model']}{new.index + 1}"   # world piece names are unique per scene
    if scale is not None and e["keyword"] == "ENVI" and "scale.x" in new.fields:
        k = float(scale)
        if not 0.1 <= k <= 10:
            raise CommandError("scale: 0.1 - 10")
        new["scale"] = tuple(c * k for c in new["scale"])
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return result


# ------------------------------------------------------------------------------------------------- terrain --
# Terrain patches (conflict_ds_tools/terrain.py): heightfields placed like props by an ENVI record (model TERRAIN_<n>),
# sculpted here, exported as <model>.EVO + collision with the mission, the source kept as <model>.terrain.json.
_GROUND = {}


def _ground_textures(s: Session):
    """The level's ground textures, most used first (from its world pieces)."""
    if s.level not in _GROUND:
        import collections
        from conflict_ds_tools import evo
        count = collections.Counter()
        try:
            finder = evo.AssetFinder(_find_file(s.folder, s.level + ".ENV"))
            for z in scene(s)["zones"]:
                path = finder.find(z["model"], (".evo",))
                if path:
                    for name, _ in evo.resolve_textures(evo.load(path), finder)[0].values():
                        if name:
                            count[name] += 1
        except Exception:  # noqa: BLE001 - only a suggestion list
            pass
        _GROUND[s.level] = [n for n, _ in count.most_common(24)] or ["Sand_1_Basic"]
    return _GROUND[s.level]


def _terrain(s: Session, model):
    t = getattr(s, "terrains", {}).get(str(model).upper())
    if not t:
        raise CommandError(f"{model} isn't a terrain patch")
    return t


def _terrain_record(s: Session, model):
    for r in s.env.find("ENVI"):
        if str(r.get("model")).upper() == str(model).upper():
            return r
    raise CommandError(f"no prop places {model}")


def _to_local(s: Session, model, pos):
    """World position (cm) -> the patch's own x / z (its record's position and yaw)."""
    import math
    r = _terrain_record(s, model)
    t = s.terrains.get(str(model).upper())
    if t is not None and t.zone:
        return pos[0] - t.origin[0], pos[2] - t.origin[2]
    px, _, pz = r["pos"]
    a = math.radians(-r["rot"][1])
    dx, dz = pos[0] - px, pos[2] - pz
    return dx * math.cos(a) + dz * math.sin(a), -dx * math.sin(a) + dz * math.cos(a)


@command("Ground textures of the open level (most used first) and the collision surfaces terrain can have.")
def terrain_options(s: Session):
    from conflict_ds_tools import bsp
    tex = _ground_textures(s)
    return {"textures": tex, "steep": _steep_default(s), "surfaces": [n for n in bsp.SURFACES if not n.startswith("Blocking")]}


def _steep_default(s: Session):
    """The level's cliff texture for steep faces (the first ground texture named like one), else its second texture."""
    tex = _ground_textures(s)
    return next((t for t in tex if any(k in t.upper() for k in ("CLIFF", "ROCK", "CUTTING", "WALL"))), tex[1] if len(tex) > 1 else None)


@command("A new terrain patch (a sculptable heightfield placed like a prop): size in metres, vertices per side (17-129), "
         "texture (default: the level's main ground texture), tile = metres per texture repeat, surface = collision "
         "type (walkable: Sand, Rock, Grass ...).", mutates=True, pos="[x, y, z] centre (cm)", size="metres per side",
         vertices="vertices per side", texture="game texture name", tile="metres per texture repeat",
         surface="collision surface", steep_texture="texture of faces steeper than slope ('' = none)",
         slope="degrees")
def terrain_new(s: Session, pos, size=60, vertices=41, texture=None, tile=8, surface="Sand", steep_texture=None,
                slope=35):
    from conflict_ds_tools.terrain import Terrain
    n = int(vertices)
    t = Terrain(n=n, cell=float(size) * 100 / (n - 1), texture=texture or _ground_textures(s)[0], tile=float(tile) * 100,
                surface=surface, steep_texture=_steep_default(s) if steep_texture is None else (steep_texture or None),
                slope=float(slope))
    if not hasattr(s, "terrains"):
        s.terrains = {}
    k = 1
    while f"TERRAIN_{k}" in s.terrains or any(str(r.get("model")).upper() == f"TERRAIN_{k}" for r in s.env.find("ENVI")):
        k += 1
    model = f"TERRAIN_{k}"
    lib = _library(s)
    tpl = next((e for e in lib if e["kind"] == "prop" and e["model"].upper() == "BH_BODGEPLATE"), None) or \
        next(e for e in lib if e["kind"] == "prop")
    r = place(s, tpl["id"], pos, 0.0)
    rec = s.env.record_at(r["line"])
    rec["model"] = model
    rec["name"] = f"{model}-{r['line']}"
    if "scale.x" in rec.fields:
        rec["scale"] = (1.0, 1.0, 1.0)
    s.terrains[model] = t
    return {"line": r["line"], "model": model, "terrain": t.to_json()}


@command("Sculpt a terrain patch: op raise / lower (strength = cm at the centre), smooth / flatten (strength 0..1, "
         "flatten towards target cm or the height under the brush).", mutates=True, model="terrain model (TERRAIN_n)",
         op="raise / lower / smooth / flatten", pos="[x, y, z] brush centre (world cm)", radius="cm",
         strength="see op", target="flatten height (cm, local)")
def terrain_brush(s: Session, model, op, pos, radius=500, strength=100, target=None):
    t = _terrain(s, model)
    x, z = _to_local(s, model, _vec(pos))
    t.brush(op, x, z, float(radius), float(strength), None if target is None else float(target))
    return {"model": model}


@command("Rolling hills over a terrain patch (adds to the heights; the edges stay at 0).", mutates=True,
         model="terrain model", amplitude="cm", scale="cm across a hill", seed="random seed")
def terrain_noise(s: Session, model, amplitude=400, scale=3000, seed=1):
    _terrain(s, model).noise(float(amplitude), float(scale), int(seed))
    return {"model": model}


@command("Set a terrain patch's heights (the editor's brush strokes), texture, tiling or surface.", mutates=True,
         model="terrain model", heights="all n*n heights (cm)", texture="game texture", tile="metres per repeat",
         surface="collision surface", steep_texture="cliff texture ('' = none)", slope="degrees")
def terrain_set(s: Session, model, heights=None, texture=None, tile=None, surface=None, steep_texture=None, slope=None):
    t = _terrain(s, model)
    if heights is not None:
        if len(heights) != t.n * t.n:
            raise CommandError(f"{model} has {t.n * t.n} heights")
        t.heights = [float(h) for h in heights]
    if texture:
        t.texture = texture
    if steep_texture is not None:
        t.steep_texture = steep_texture or None
    if slope is not None:
        t.slope = float(slope)
    if tile:
        t.tile = float(tile) * 100
    if surface:
        from conflict_ds_tools import bsp
        bsp.surface_index(surface)
        t.surface = surface
    return {"model": model, "terrain": t.to_json()}


# ----------------------------------------------------------------------------------------------------- kits --
# Kit pieces (conflict_ds_tools/kits.py): single objects of the levels' world-piece models placed as props. The record
# holds the piece's world-space vertices turned about the level origin, so the editor works with the piece's centre.
_KIT_PIECES = {}


def _kit_centre_world(rec, kit):
    import math
    a = math.radians(rec["rot"][1])
    cx, cy, cz = kit["centre"]
    px, py, pz = rec["pos"]
    return (px + cx * math.cos(a) + cz * math.sin(a), py + cy, pz - cx * math.sin(a) + cz * math.cos(a))


def _kit_data(s: Session, level, model):
    from conflict_ds_tools import evo
    folder = os.path.join(s.data_dir, level.lower())
    path = evo.AssetFinder(_find_file(folder, level.upper() + ".ENV") or os.path.join(folder, "x")).find(model, (".evo",))
    if not path:
        raise CommandError(f"no {model}.EVO in {level}")
    return open(path, "rb").read()


@command("World-piece models of a level that kit pieces can come from (its ground / zone models).", level="level name")
def kit_models(s: Session, level=None):
    level = (level or s.level).upper()
    env = EnvFile.load(_find_file(os.path.join(s.data_dir, level.lower()), level + ".ENV"))
    return sorted({str(r["model"]) for r in env.find("ENVI") if r.get("type") == 5 and str(r.get("parent")) == "Root"})


@command("The pieces of a world-piece model: index, textures, size (m), collision - search filters by texture name; "
         "group = identical pieces (same textures, size and triangle count) listed once with their count.",
         level="level name", model="world-piece model", search="texture name part (cliff, wall, road ...)",
         group="list identical pieces once (default true)")
def kit_pieces(s: Session, model, level=None, search="", group=True):
    from conflict_ds_tools import kits
    level = (level or s.level).upper()
    key = (level, model.upper())
    if key not in _KIT_PIECES:
        _KIT_PIECES[key] = kits.pieces(_kit_data(s, level, model))
    q = (search or "").upper()
    out, seen = [], {}
    for p in _KIT_PIECES[key]:
        if q and not any(q in t.upper() for t in p["textures"]):
            continue
        sig = (tuple(p["textures"]), tuple(round(v / 10) for v in p["size"]), p["triangles"])
        if group and sig in seen:
            seen[sig]["count"] += 1
            continue
        out.append({"count": 1,"index": p["index"], "textures": p["textures"], "collision": p["collision"],
                    "size": [round(v / 100, 1) for v in p["size"]], "centre": p["centre"],
                    "name": f"KIT_{model.upper()}_{p['index']:03d}"[:60]})
        seen[sig] = out[-1]
    return out


def _kit_cache(s: Session, level, model, index, name):
    """The cut piece in the open level's extracted folder (local, for the editor's view only - never shipped)."""
    from conflict_ds_tools import kits
    path = os.path.join(s.folder, name + ".EVO")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(kits.extract(_kit_data(s, level, model), int(index), name))
    # its .glb straight into the view's model cache (the model server looks there first; its folder index was made
    # before the piece existed)
    glb = os.path.join(s.data_dir, "_editor_cache", os.path.basename(s.folder).upper(), name + ".glb")
    if not os.path.exists(glb):
        import types
        import assets
        from conflict_ds_tools import evo
        os.makedirs(os.path.dirname(glb), exist_ok=True)
        opts = types.SimpleNamespace(**vars(assets.OPTS))
        m = assets.evo_convert.Model(path, evo.AssetFinder(path), opts)
        assets.evo_convert.GltfWriter(m, [], opts, glb + ".tmp").write(glb + ".tmp", True)
        os.replace(glb + ".tmp", glb)
    return name


@command("Prepare a kit piece's model for the editor's view (the preview while dragging); returns its model name.",
         level="level", model="world-piece model", index="piece index")
def kit_preview(s: Session, model, index, level=None):
    level = (level or s.level).upper()
    name = f"KIT_{model.upper()}_{int(index):03d}"[:60]
    _kit_cache(s, level, model, index, name)
    return {"model": name, "level": s.level}


@command("Place a kit piece: its centre at pos, turned by yaw (degrees). The mod references it (DesertStormFix cuts it "
         "out of the player's own game files).", mutates=True, level="level the piece comes from",
         model="world-piece model", index="piece index", pos="[x, y, z] the surface it stands on (its centre with on_centre)",
         yaw="degrees", on_centre="pos is the centre, not the ground under it")
def kit_place(s: Session, model, index, pos, level=None, yaw=0.0, on_centre=False):
    from conflict_ds_tools import kits
    level = (level or s.level).upper()
    piece = next(p for p in kit_pieces(s, model, level) if p["index"] == int(index))
    name = piece["name"]
    _kit_cache(s, level, model, index, name)
    if not hasattr(s, "kits"):
        s.kits = {}
    s.kits[name] = {"level": level, "source": model.upper() + ".EVO", "index": int(index), "centre": list(piece["centre"]),
                    "size": [v * 100 for v in piece["size"]]}
    lib = _library(s)
    tpl = next((e for e in lib if e["kind"] == "prop" and e["model"].upper() == "BH_BODGEPLATE"), None) or \
        next(e for e in lib if e["kind"] == "prop")
    at = _vec(pos)
    if not on_centre:                           # pos is the surface it stands on: its bottom goes there
        at = (at[0], at[1] + piece["size"][1] * 100 / 2, at[2])
    r = place(s, tpl["id"], at, 0.0)
    rec = s.env.record_at(r["line"])
    rec["model"], rec["name"] = name, f"{name}-{r['line']}"
    if "scale.x" in rec.fields:
        rec["scale"] = (1.0, 1.0, 1.0)
    rec["rot"] = (0.0, float(yaw) % 360, 0.0)
    rec["pos"] = kits.placement(piece["centre"], at, float(yaw))
    return {"line": r["line"], "model": name}


# ---------------------------------------------------------------------------------------------------- roads --
# Roads (conflict_ds_tools/roads.py): a strip along a centreline on the ground, model ROAD_<n> placed by an ENVI
# record at its first point, exported with the mission as <model>.EVO + collision, the source as <model>.road.json.
_ROAD_WORDS = ("ROAD", "TARMAC", "ASPHALT", "DIRT", "TRACK", "COBBLE", "PAVE", "CONCRETE")
_ROAD_SKIP = ("TRANS", "END", "HEDGEHOG", "BAG_", "BMP", "LINK", "TYRE", "BRIDGE", "BARRIER", "CURB", "SIGN", "UNDER",
              "WALL", "LAMP")
_ROAD_TEX = None


_TEX_INFO = {}


def _spread_lifts(s: Session):
    """Roads at the same height above the ground flicker where they overlap: give each its own (6, 8, 10 ... cm)."""
    seen = set()
    for k, model in enumerate(sorted(getattr(s, "roads", {}), key=lambda m: int(m.split("_")[-1]) if m.split("_")[-1].isdigit() else 0)):
        road = s.roads[model]
        if road.lift in seen:
            road.lift = 6.0 + 2.0 * (k % 8)
            while road.lift in seen:
                road.lift += 1.0
        seen.add(road.lift)


def _road_texture_info(s: Session, name):
    """From the texture's picture: which way its road runs (rows change less along it), its length per width (the
    repeat length = road width x that), and whether it repeats seamlessly along the road (the brightness along it stays
    level - M01_ROAD02's scorch mark or a road end doesn't)."""
    key = name.upper()
    if key not in _TEX_INFO:
        info = {"across": False, "aspect": 1.0, "tileable": True}
        hits = glob.glob(os.path.join(s.data_dir, "*", key + ".png"))
        if hits:
            try:
                from PIL import Image
                im = Image.open(hits[0]).convert("L")
                w, h = im.size
                px = list(im.getdata())
                rows = [sum(px[y * w:(y + 1) * w]) / w for y in range(h)]
                cols = [sum(px[x::w]) / h for x in range(w)]
                change = lambda v: sum(abs(v[i + 1] - v[i]) for i in range(len(v) - 1)) / max(1, len(v) - 1)
                along_v = change(rows) <= change(cols)
                prof = rows if along_v else cols
                mean = sum(prof) / len(prof)
                spread = (sum((v - mean) ** 2 for v in prof) / len(prof)) ** 0.5
                info = {"across": not along_v, "aspect": (h / w) if along_v else (w / h), "tileable": spread < 9.0}
            except Exception:  # noqa: BLE001 - no PIL / unreadable: plain mapping
                pass
        _TEX_INFO[key] = info
    return _TEX_INFO[key]


@command("Road textures (repeating ones of this level first, then other levels'; one-off variants such as scorch marks "
         "and road ends last) and the walkable surfaces a road can have.")
def road_options(s: Session):
    global _ROAD_TEX
    from conflict_ds_tools import bsp
    if _ROAD_TEX is None:
        names = {}
        for path in glob.glob(os.path.join(s.data_dir, "*", "*.DDS")):
            n = os.path.splitext(os.path.basename(path))[0]
            u = n.upper()
            if not n.startswith("_") and any(w in u for w in _ROAD_WORDS) and not any(w in u for w in _ROAD_SKIP):
                names.setdefault(u, os.path.basename(os.path.dirname(path)).upper())
        _ROAD_TEX = names
    here = {n for n in _ROAD_TEX if _find_file(s.folder, n + ".DDS")}
    order = sorted(_ROAD_TEX, key=lambda n: (not _road_texture_info(s, n)["tileable"], n not in here, n))
    return {"textures": order, "variants": [n for n in order if not _road_texture_info(s, n)["tileable"]],
            "surfaces": ["Tarmac", "Baked Earth", "Concrete", "Sand", "Rock", "Grass"]}


@command("A road through points on the ground (two or more): width in metres, a road texture, a walkable surface. "
         "dense = the points are already the centreline on the ground (the editor samples it); otherwise the road is "
         "smoothed through them with heights in between interpolated.", mutates=True, points="[[x, y, z], ...] cm",
         width="metres", texture="road texture", surface="collision surface", dense="points already sampled")
def road_new(s: Session, points, width=6, texture="M01_ROAD01", surface="Tarmac", dense=False):
    from conflict_ds_tools.roads import Road, road_from_clicks
    pts = [_vec(p) for p in points]
    line = pts if dense else road_from_clicks(pts)
    origin = line[0]
    info = _road_texture_info(s, texture)
    k_lift = len(getattr(s, "roads", {}))
    road = Road([(p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]) for p in line], width=float(width) * 100,
                texture=texture, surface=surface, tile=float(width) * 100 * info["aspect"], across=info["across"],
                lift=6.0 + 2.0 * (k_lift % 8))      # each road a little higher: overlaps don't flicker
    if not hasattr(s, "roads"):
        s.roads = {}
    k = 1
    while f"ROAD_{k}" in s.roads or any(str(r.get("model")).upper() == f"ROAD_{k}" for r in s.env.find("ENVI")):
        k += 1
    model = f"ROAD_{k}"
    lib = _library(s)
    tpl = next((e for e in lib if e["kind"] == "prop" and e["model"].upper() == "BH_BODGEPLATE"), None) or \
        next(e for e in lib if e["kind"] == "prop")
    r = place(s, tpl["id"], origin, 0.0)
    rec = s.env.record_at(r["line"])
    rec["model"], rec["name"] = model, f"{model}-{r['line']}"
    if "scale.x" in rec.fields:
        rec["scale"] = (1.0, 1.0, 1.0)
    s.roads[model] = road
    return {"line": r["line"], "model": model, "points": len(line)}


@command("Change a road: width (m), texture, surface, texture direction (across = the texture's road runs sideways), "
         "tile (metres per texture repeat).", mutates=True, model="road model (ROAD_n)", width="metres",
         texture="road texture", surface="collision surface", across="true / false", tile="metres")
def road_set(s: Session, model, width=None, texture=None, surface=None, across=None, tile=None):
    road = getattr(s, "roads", {}).get(str(model).upper())
    if not road:
        raise CommandError(f"{model} isn't a road")
    if width:
        road.width = float(width) * 100
    if texture:
        road.texture = texture
    if width or texture:                         # the repeat follows the texture's proportions and the width
        info = _road_texture_info(s, road.texture)
        road.tile = road.width * info["aspect"]
        if across is None:
            road.across = info["across"]
    if surface:
        road.surface = surface
    if across is not None:
        road.across = bool(across)
    if tile:
        road.tile = float(tile) * 100
    return {"model": model, "road": road.to_json()}


@command("A texture from the game data as a PNG data URL (for drawing terrain patches).", name="texture name")
def texture_png(s: Session, name):
    import base64
    from conflict_ds_tools import evo
    finder = evo.AssetFinder(_find_file(s.folder, s.level + ".ENV"))
    path = finder.find(name, (".png",))
    if not path:
        hits = glob.glob(os.path.join(s.data_dir, "*", name.upper() + ".png")) or \
            glob.glob(os.path.join(s.data_dir, "*", name + ".png"))
        path = hits[0] if hits else None
    if not path:
        raise CommandError(f"no picture of {name} in the extracted files")
    return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()


def _save_roads(s: Session, folder, refs):
    from conflict_ds_tools import evo
    from conflict_ds_tools import terrain as _tm
    used = {str(r.get("model")).upper() for r in s.env.find("ENVI")}
    finder = evo.AssetFinder(_find_file(s.folder, s.level + ".ENV"))
    template = open(finder.find(_tm.TEMPLATE, (".evo",)), "rb").read()
    written = []
    for model, road in getattr(s, "roads", {}).items():
        if model not in used:
            continue
        with open(os.path.join(folder, model + ".EVO"), "wb") as f:
            f.write(road.to_evo(template, model))
        with open(os.path.join(folder, model + ".road.json"), "w") as f:
            f.write(road.dumps())
        written += [model + ".EVO", model + ".road.json"]
        if not (_find_file(s.folder, road.texture + ".DDS") or _find_file(s.folder, road.texture + ".TGA")):
            hits = glob.glob(os.path.join(s.data_dir, "*", road.texture.upper() + ".DDS"))
            if hits:
                refs[road.texture.upper() + ".DDS"] = hits[0]
    return written


def _save_terrains(s: Session, folder, refs):
    """<model>.EVO + <model>.terrain.json for every placed patch; its texture referenced when the base level lacks it."""
    from conflict_ds_tools import evo
    used = {str(r.get("model")).upper() for r in s.env.find("ENVI")}
    finder = evo.AssetFinder(_find_file(s.folder, s.level + ".ENV"))
    from conflict_ds_tools import terrain as _tm
    template = finder.find(_tm.TEMPLATE, (".evo",))
    zone_template = finder.find(_tm.ZONE_TEMPLATE, (".evo",))
    written = []
    for model, t in getattr(s, "terrains", {}).items():
        if model not in used:
            continue
        if t.zone:                                  # objects under the sculpted ground come up onto it
            half = t.size / 2
            for h in s.env.find("HLOI"):
                x, y, z = h["pos"]
                lx, lz = x - t.origin[0], z - t.origin[2]
                if abs(lx) < half and abs(lz) < half:
                    ground = t.origin[1] + t.height_at(lx, lz)
                    if y < ground + 5:
                        h["pos"] = (x, ground + 10, z)
        warnings = []
        with open(os.path.join(folder, model + ".EVO"), "wb") as f:
            if t.zone:
                f.write(t.to_evo(open(zone_template, "rb").read(), model, warnings, offset=t.origin))
            else:
                f.write(t.to_evo(open(template, "rb").read(), model, warnings))
        with open(os.path.join(folder, model + ".terrain.json"), "w") as f:
            f.write(t.dumps())
        written += [model + ".EVO", model + ".terrain.json"]
        for name in filter(None, (t.texture, t.steep_texture)):
            if not (_find_file(s.folder, name + ".DDS") or _find_file(s.folder, name + ".TGA")):
                tex = finder.find(name, (".dds", ".tga"))
                if tex:
                    refs[name.upper() + os.path.splitext(tex)[1].upper()] = tex
    return written


@command("A brand-new level: an empty scene with the sky, fog, sun, sounds and textures of `environment` (a shipped "
         "level), the squad, and a sculptable ground (a terrain zone, size metres across, its main ground texture). "
         "Saved right away as mission <name> in Mods\\<mod>.", environment="level whose environment it uses, e.g. MISSION1",
         mod="mod folder", name="mission / level name", size="ground size in metres", vertices="ground vertices per side")
def new_level(s: Session, environment, mod, name, size=400, vertices=65):
    from conflict_ds_tools import blank
    from conflict_ds_tools.terrain import Terrain
    open_level(s, environment)
    base = s.env
    start = next((r for r in base.find("HLOI") if r.fields and r["unit"] == "Bradley" and not r["flags"] & 0x20000), None)
    if start is None:
        raise CommandError(f"{environment} has no squad start (Bradley) - pick a campaign mission")
    centre = start["pos"]
    tpl = next(r for r in base.find("ENVI") if r.get("type") == 5 and str(r.get("parent")) == "Root")
    lines = blank.blank_lines(base, extra_envi=[tpl.text])

    s.env.lines = lines
    s.env._parse()
    g = next(r for r in s.env.find("ENVI"))
    g["name"], g["model"], g["pos"], g["rot"] = "GROUND-1", "GROUND", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    n = int(vertices)
    s.terrains = {"GROUND": Terrain(n=n, cell=float(size) * 100 / (n - 1), texture=_ground_textures(s)[0],
                                    steep_texture=_steep_default(s), zone=True, origin=(centre[0], centre[1] - 10, centre[2]))}
    k = 0
    for h in s.env.find("HLOI"):
        h["zone"] = "GROUND-1"
        h["in_play"] = 1
        h["pos"] = (centre[0] + 200 * k, centre[1], centre[2])     # the squad side by side at the start
        k += 1
    from conflict_ds_tools.routes import RouteFile as _RF
    s.routes = _RF([])
    s.layers, s.layer_start, s.blank = {}, {}, True
    s.undo_stack.clear(); s.redo_stack.clear()
    saved = save_mission(s, mod, name)
    s.revision = getattr(s, "revision", 0) + 1
    return {"mission": [mod, name.upper()], "level": saved["level"], "ground": "GROUND", "start": list(centre)}


# ---------------------------------------------------------------------------------------------------- save --
@command("Write the edited scene (and routes) as a mod: <game>\\Mods\\<mod>\\<LEVEL>.ENV / .RMD.", mod="mod folder name",
         game_dir="game folder (default: the session's)")
def save_mod(s: Session, mod, game_dir=None):
    if s.env is None:
        raise CommandError("no level open")
    game = game_dir or s.game_dir
    if not game or not os.path.isdir(game):
        raise CommandError("game folder unknown - start the editor with --game")
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("bad mod name")
    folder = os.path.join(game, "Mods", mod)
    os.makedirs(folder, exist_ok=True)
    written = []
    s.env.save(os.path.join(folder, s.level + ".ENV"))
    written.append(s.level + ".ENV")
    if s.routes:
        s.routes.save(os.path.join(folder, s.level + ".RMD"))
        written.append(s.level + ".RMD")
    if problems := s.env.problems:
        raise CommandError("saved, but the scene has problems: " + "; ".join(problems[:3]))
    s.dirty = False
    return {"folder": folder, "files": written}


# ------------------------------------------------------------------------------------------ save as mission --
# A new mission = a mod with the edited scene under a new name and a Lua script that sets the map up the way the base
# mission's DLL does (editor/level_setups.json, scripts/gen_level_setups.py) but loads that scene. The game names the
# octree / AI walk map / routes after the scene, so the base level's .OCT / .QTD are copied under the new name (the game
# rebuilds the .OCT over that copy when it loads a .lua level - edited props need it - and the copy makes the name known
# to its mod index); the
# script's "--@archive <base>" line gives it the base level's archive and sound cache (DesertStormFix scripthost).
_SETUPS = None
_SINGLE_ARG = {0x0C, 0x44, 0x4C, 0x58}   # sound bank, scene, map image, strings
_NAMED = {0x0C: "game.load_sound_bank", 0x44: "game.load_scene", 0x4C: "game.load_map_image",
          0x58: "game.load_strings", 0x4E0: "game.set_briefing_text"}   # +0x4E0: briefing title / date / place / pages
_ENEMY = ("IRAQ", "RU_", "RUSSIAN")
_NOT_ENEMY = ("GOATHERDER", "CIVILIAN", "IRAQCIV")


def _setups():
    global _SETUPS
    if _SETUPS is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "level_setups.json")) as f:
            _SETUPS = json.load(f)
    return _SETUPS


def _lua_value(v):
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, float):
        return repr(v)
    return "0x%X" % v if isinstance(v, int) and v > 0xFFFF else str(v)


def _setup_lines(base, scene_file, legacy=False):
    """The base mission's set-up calls as Lua. legacy: as editors before 2026-10-10 wrote them (+0x4E0, the briefing
    text, was called game.add_objective) - only to recognise untouched old scripts."""
    out = []
    for c in _setups().get(base, []):
        slot, args = c["slot"], list(c["args"])
        if slot in _SINGLE_ARG:
            args = args[:1]
        if None in args:
            out.append(f"    -- (+0x{slot:X}: arguments the editor couldn't read)")
            continue
        if slot == 0x44:
            out.append(f'    game.load_scene("{scene_file}")        -- this mission\'s scene (saved next to this script)')
        elif slot == 0x54:
            a, b = args
            out.append(f"    game.set_level_bounds({', '.join(map(repr, a))}, {', '.join(map(repr, b))})")
        elif slot in _NAMED:
            fn = "game.add_objective" if legacy and slot == 0x4E0 else _NAMED[slot]
            out.append(f"    {fn}({', '.join(_lua_value(a) for a in args)})")
        else:
            out.append(f"    game.call(0x{slot:X}{''.join(', ' + _lua_value(a) for a in args)})")
    return out


def _base_dll(s: Session, base):
    """The shipped mission DLL of a level (Mission1.dll ...) in the game folder, or None (MP levels, no game folder)."""
    if not s.game_dir:
        return None
    path = _find_file(s.game_dir, base + ".DLL")
    return os.path.basename(path) if path else None


_SAFE_SETUP = {0x0C, 0x44, 0x4C, 0x50, 0x54, 0x58, 0x494, 0x498, 0x4AC, 0x3F0, 0x8A8, 0x70}


def _blank_script(s: Session, name, base):
    """A new level's script: the base level's map set-up without anything about its objects (they're gone), bounds
    around the ground."""
    import re
    lines = []
    for l in _setup_lines(base, name + ".env"):
        m = re.search(r"game\.call\(0x([0-9A-F]+)", l)
        if "set_briefing_text" in l or (m and int(m.group(1), 16) not in _SAFE_SETUP):
            continue
        if "set_level_bounds" in l:
            g = next((t for t in getattr(s, "terrains", {}).values() if t.zone), None)
            if g:
                half = g.size / 2 + 2000
                ox, _, oz = g.origin
                l = f"    game.set_level_bounds({ox + half:.1f}, 0.0, {oz + half:.1f}, {ox - half:.1f}, 0.0, {oz - half:.1f})"
        lines.append(l)
    setup = "\n".join(lines)
    return f"""--@archive {base.lower()}
-- {name}: a new level made with the Desert Storm Editor ({base}'s sky, sounds and textures).
-- setup() loads the scene and the level data; write the mission below it. game.EVENT has the game's events,
-- game.* the helpers (DesertStormFix scripthost.cpp).

local function setup()
{setup}
end

{_layers_block(s)}

{logic.blocks_text(s)}

function init()
{logic.HOOK_INIT}
    setup()
end

function update()
end

function on_message(id, a, b)
{logic.HOOK_MSG.format(args="id, a, b")}
end
"""


def _mission_script(s: Session, name, base):
    if getattr(s, "blank", False):
        return _blank_script(s, name, base)
    """A new mission's Lua script. Made from a shipped mission: that mission's own logic runs underneath ("--@base
    Mission1.dll": DesertStormFix loads the DLL with this mission's scene - cutscenes, objectives, triggers, Foley's
    cell all as in the original) and the script adds to it. Without a DLL: a stand-alone script that sets the map up."""
    dll = _base_dll(s, base)
    if dll:
        return f"""--@archive {base.lower()}
--@base {dll}
-- {name}: made with the Desert Storm Editor from {base}.
-- {dll} runs the original mission on this scene (objectives, cutscenes, triggers, mission end); this script
-- runs after it each frame and gets every message after it. Add to the mission here:
-- game.EVENT has the game's events, game.* the helpers (DesertStormFix scripthost.cpp).

{_layers_block(s)}

{logic.blocks_text(s)}

function init()
{logic.HOOK_INIT}
end

function update()
end

function on_message(id, a, b)
{logic.HOOK_MSG.format(args="id, a, b")}
end
"""
    return _mission_script_standalone(s, name, base)


def _mission_script_standalone(s: Session, name, base, legacy=False):
    enemies = []
    for r in s.env.find("HLOI"):
        model, stat = r["model"].upper(), r["stat"].upper()
        if model.startswith(_ENEMY) and not any(k in model for k in _NOT_ENEMY) and not stat.startswith("CS_") \
                and "VEH" not in model:
            enemies.append(r["script_id"])
    ids = ", ".join(f"[{i}] = true" for i in sorted(enemies))
    setup = "\n".join(_setup_lines(base, name + ".env", legacy))
    if legacy:   # editors before 2026-10-10: objective 0 was never added (game.add_objective was +0x4E0)
        start = "        game.show_objective(0)"
        won = "            game.complete_objective(0)"
        goal = ""
    else:
        start = ("        goal = game.add_objective(game.text(\"EDITOR_KILL_ALL\", \"Eliminate all enemy forces\"))\n"
                 "        if goal then game.show_objective(goal) end")
        won = "            if goal then game.complete_objective(goal) end"
        goal = "local goal   -- the game's index of the kill-everyone objective (handed out when it is added)\n"
    return f"""--@archive {base.lower()}
-- {name}: made with the Desert Storm Editor from {base}.
-- setup() sets the map up the way {base.title()}.dll does, with this mission's scene. Write the mission below it:
-- game.EVENT has the game's events, game.* the helpers (DesertStormFix scripthost.cpp).

local function setup()
{setup}
end

-- enemy soldiers in the scene when it was saved (script ids); killing all of them wins the mission
local enemies = {{ {ids} }}

{_layers_block(s)}

{logic.blocks_text(s)}

function init()
{logic.HOOK_INIT}
    setup()
end

{goal}local started = false
function update()
    if not started and game.time_ms() > 0 then
        started = true
{start}
    end
end

function on_message(id, a, b)
{logic.HOOK_MSG.format(args="id, a, b")}
    if id == game.EVENT.KILLED and enemies[a] then
        enemies[a] = nil
        if next(enemies) == nil then
{won}
            game.end_mission(2)
        end
    end
end
"""


LAYERS_BEGIN = "-- @editor begin: layers"
LAYERS_END = "-- @editor end"


def _layers_block(s: Session):
    """game.layers for the mission script; the editor rewrites the lines between the markers on every save."""
    groups = {}
    for sid, name in sorted(s.layers.items()):
        groups.setdefault(name, []).append(sid)
    body = "".join(f"    [{_lua_value(n)}] = {{ {', '.join(map(str, ids))} }},   -- starts "
                   f"{s.layer_start.get(n, 'hidden')}\n" for n, ids in sorted(groups.items()))
    return (f"{LAYERS_BEGIN} (rewritten by the editor on every save - change layers and regions there; "
            f"game.show_layer(name) puts a layer's objects into play)\ngame.layers = {{\n{body}}}\n"
            f"{_regions_lua(s)}\n{LAYERS_END}")


def _regions_lua(s: Session):
    """game.regions: the scene's trigger regions for game.in_region / the host's ENTERED and LEFT events."""
    rows = []
    for r in s.env.find("REGN"):
        try:
            x, y, z = r["pos"]
            if r["type"] == 1:
                rows.append(f"    [{_lua_value(r['name'])}] = {{ {x!r}, {y!r}, {z!r}, radius = {r['radius']!r} }},")
            else:
                mn, mx = r["min"], r["max"]
                rows.append(f"    [{_lua_value(r['name'])}] = {{ {x!r}, {y!r}, {z!r}, yaw = {r['rot'][1]!r}, "
                            f"min = {{ {', '.join(map(repr, mn))} }}, max = {{ {', '.join(map(repr, mx))} }} }},")
        except KeyError:
            continue
    return "game.regions = {\n" + "\n".join(rows) + ("\n" if rows else "") + "}"


def _strip_layers(text):
    """The script without the editor-written parts (layers / objectives / rules blocks and their hook lines, the
    generated enemy list - it follows the scene)."""
    a = text.find(LAYERS_BEGIN)
    b = text.find(LAYERS_END, a) if a >= 0 else -1
    if a >= 0 and b > a:
        text = text[:a] + text[b + len(LAYERS_END):]
    text = tactics.strip(production.strip(logic.strip(text)))
    return "\n".join(l for l in text.splitlines() if not l.startswith("local enemies = {"))


def _upgrade_script(path, s: Session, name):
    """A script still exactly as the editor first wrote it without base-mission logic (map set-up replay + kill-all
    goal) becomes the base-mission script - nothing of the modder's is lost. Returns True when upgraded."""
    text = open(path, encoding="utf-8").read()
    if "--@base" in text or not _base_dll(s, s.level) or getattr(s, "blank", False):
        return False
    mine = _strip_layers(text).strip()
    if all(mine != _strip_layers(_mission_script_standalone(s, name, s.level, legacy)).strip()
           for legacy in (False, True)):
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_mission_script(s, name, s.level))
    return True


def _update_layers_block(path, s: Session):
    """An existing mission script: replace its layers block (or add one after the first line), then the objectives /
    rules blocks after it (logic.py)."""
    text = open(path, encoding="utf-8").read()
    block = _layers_block(s)
    a = text.find(LAYERS_BEGIN)
    b = text.find(LAYERS_END, a) if a >= 0 else -1    # its own end (the other blocks' end markers start the same)
    if a >= 0 and b > a:
        text = text[:a] + block + text[b + len(LAYERS_END):]
        after = a + len(block)
    else:
        first = text.find("\n") + 1
        text = text[:first] + "\n" + block + "\n" + text[first:]
        after = first + 1 + len(block)
    text = logic.update_blocks(text, s, after)
    text = production.update_text(text, s)          # briefing, soundtrack, spawns (production.py)
    text = tactics.update_text(text, s)             # air support (tactics.py)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


_CATALOG = {}


def _catalog(s: Session, table):
    """A catalog.dat CSV table as {first column (upper case): [rows]}."""
    if table not in _CATALOG:
        rows = {}
        path = _find_file(os.path.join(s.data_dir, "catalog"), table)
        if path:
            for line in open(path, errors="replace").read().splitlines():
                cols = [c.strip() for c in line.split(",")]
                if cols and cols[0]:
                    rows.setdefault(cols[0].upper(), []).append(cols)
        _CATALOG[table] = rows
    return _CATALOG[table]


def _vehicle_weapons(s: Session, unit):
    """WEAPS.TXT rows of a vehicle unit's guns (VEHWPNS.TXT)."""
    out = []
    for row in _catalog(s, "VEHWPNS.TXT").get(str(unit or "").upper(), []):
        out += _catalog(s, "WEAPS.TXT").get(row[1].upper() if len(row) > 1 else "", [])
    return out


def _level_banks(path):
    """Bank keys in a level sound cache (.sch)."""
    from conflict_ds_tools import sound
    import struct
    keys = set()
    for tag, c in sound.chunks(open(path, "rb").read()):
        if tag == b"BANK":
            keys.add(struct.unpack_from("<I", c, 8)[0])
    return keys


def _pack_banks(s: Session, folder, name):
    """Sound banks of placed vehicles' guns (WEAPS.TXT column 64) that the base level's sound cache lacks, taken from a
    level that has them, as one bank pack per bank (<NAME>_<BANK>.sch - DesertStormFix adds mod .sch packs to the level
    cache). Without them the guns are silent and the game logs "<bank>.sbk NOT FOUND"."""
    from conflict_ds_tools import sound
    import glob
    game = s.game_dir
    base = _find_file(game, s.level + ".sch") if game else None
    if not base:
        return []
    have = _level_banks(base)
    wanted = []
    for r in s.env.find("HLOI"):
        for w in _vehicle_weapons(s, r.get("unit")):
            bank = w[63] if len(w) > 63 else ""
            if bank and bank != "-1" and sound.bank_key(bank) not in have and bank not in wanted:
                wanted.append(bank)
    for old in glob.glob(os.path.join(folder, name + "_*.sch")):
        os.remove(old)                         # bank packs of older saves: the banks are referenced now
    return wanted                              # "bank X" lines: DesertStormFix copies them from the player's caches


def _remove_game_copies(s: Session, folder, up, refs):
    """Files older saves copied out of the game: the full scene / routes / nav files of this mission (now patches and
    references) and copies identical to a referenced game file. Anything that differs from the game's is the modder's
    and stays."""
    removed = []
    for ext in (".ENV", ".RMD", ".OCT", ".QTD"):
        path = os.path.join(folder, up + ext)
        if os.path.exists(path) and (ext != ".RMD" or _find_file(s.folder, s.level + ".RMD")):
            os.remove(path); removed.append(up + ext)
    for name, src in refs.items():
        path = _find_file(folder, name)
        if path:
            with open(path, "rb") as a, open(src, "rb") as b:
                same = a.read() == b.read()
            if same:
                os.remove(path); removed.append(os.path.basename(path))
    return removed


def _needed_models(s: Session, model, unit, finder):
    """Models a placed object needs besides its own: for a vehicle (VEHICLE.TXT row of its unit) the destroyed model,
    wreck shard and the guns (VEHWPNS.TXT -> WEAPS.TXT column 1), and names in its .DYN (tracks, wheels) that are
    models. A missing one stops the level load ("Attempting to re-compile EVO file ... in release build")."""
    names = []
    for row in _catalog(s, "VEHICLE.TXT").get(str(unit or "").upper(), []):
        names += row[1:]
    for w in _vehicle_weapons(s, unit):
        names += w[1:]                         # model (col 2), muzzle flash (col 36), ... - whatever is a model
    dyn = finder.find(model, (".dyn",))
    if dyn:
        names += open(dyn, errors="replace").read().split()
    out = []
    for n in names:
        if n and n[0].isalpha() and n.upper() != model.upper() and n.upper() not in out and finder.find(n, (".evo",)):
            out.append(n.upper())
    return out


def _pack_models(s: Session, folder, refs):
    """Models placed from other levels: their .EVO, textures and vehicle data into the mod (by name), with the models
    they need (vehicle guns, destroyed models, wrecks)."""
    from conflict_ds_tools import evo
    here = _models_in_level(s)
    finder = None
    packed = []
    todo = []
    for r in s.env.records:
        if r.kind == "SWITCH" and r.fields:     # its off / on models (Switches.txt)
            todo += [(m.upper(), None) for m in interactive.switch_models(s, r)]
            continue
        if r.kind not in ("HLOI", "ENVI") or not r.fields or (r.kind == "ENVI" and r.get("type") != 0):
            continue
        todo.append((r["model"].upper(), r.get("unit") if r.kind == "HLOI" else None))
        if r.kind == "HLOI":
            todo += [(str(r[f"link{k}.b"]).upper(), None) for k in range(r.get("link_count") or 0)]
    while todo:
        m, unit = todo.pop(0)
        if m in here or ("_%08X" % archive_hash(m + ".EVO")) in here or m in packed:
            continue
        if finder is None:
            finder = evo.AssetFinder(_find_file(s.folder, s.level + ".ENV"))
        src = finder.find(m, (".evo",))
        if not src:
            continue
        todo += [(n, None) for n in _needed_models(s, m, unit, finder)]
        files = {m + ".EVO": src}
        try:
            scene = evo.load(src)
            for name, _ in evo.resolve_textures(scene, finder)[0].values():
                game_tex = finder.find(name, (".dds", ".tga")) if name else None   # not the extractor's PNG copies
                if game_tex:
                    files.setdefault(name.upper() + os.path.splitext(game_tex)[1].upper(), game_tex)
        except evo.EvoError:
            pass
        dyn = finder.find(m, (".dyn",))
        if dyn:
            files[m + ".DYN"] = dyn
        for dst, path in files.items():
            refs[dst] = path                   # the player's own copy is used (<name>.uses), nothing is copied
        packed.append(m)
    return packed


@command("Save the edited level as a new mission: <game>\\Mods\\<mod>\\ gets <NAME>.ENV/.RMD/.OCT/.QTD, models placed "
         "from other levels, and <name>.lua (generated once - an existing script is kept). campaign = add it to that "
         "mod's campaign.txt (created with this name if missing).", mod="mod folder", name="mission name (letters, "
         "digits, _)", campaign="optional campaign name", game_dir="game folder (default: the session's)")
def save_mission(s: Session, mod, name, campaign=None, game_dir=None):
    import re
    if s.env is None:
        raise CommandError("no level open")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,23}", name or ""):
        raise CommandError("mission name: a letter, then up to 23 letters, digits or _")
    if s.level not in _setups():
        raise CommandError(f"{s.level} has no recorded map set-up (missions only)")
    game = game_dir or s.game_dir
    if not game or not os.path.isdir(game):
        raise CommandError("game folder unknown - start the editor with --game")
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("bad mod name")
    folder = os.path.join(game, "Mods", mod)
    os.makedirs(folder, exist_ok=True)
    up = name.upper()
    written = []
    # No game files in the mod (gamerefs.py): the scene and routes as changes against the base level's, the base's
    # nav files and the models / banks placed from other levels as references to the player's own install.
    from conflict_ds_tools import gamerefs
    refs = {}
    base_env = EnvFile.load(_find_file(s.folder, s.level + ".ENV"))
    with open(os.path.join(folder, up + ".envpatch"), "w", encoding="latin-1", newline="\n") as f:
        f.write(gamerefs.make_env_patch(base_env.lines, s.env.lines, s.level + ".ENV"))
    written.append(up + ".envpatch")
    base_rmd = _find_file(s.folder, s.level + ".RMD")
    if s.routes and base_rmd:
        with open(os.path.join(folder, up + ".rmdpatch"), "w", newline="\n") as f:
            f.write(gamerefs.make_rmd_patch(RouteFile.load(base_rmd), s.routes, s.level + ".RMD"))
        written.append(up + ".rmdpatch")
    elif s.routes:                             # the base has no route file: the network is the scene's own markers
        s.routes.save(os.path.join(folder, up + ".RMD")); written.append(up + ".RMD")
    renamed = {}
    # The walk map (.qtd) is left out on purpose: without one the game builds it - and the generated route markers -
    # from this mission's scene, so placed objects block the AI's paths; DesertStormFix adds the hand-placed markers
    # from <NAME>.RMD back at their indices (scripthost.cpp BuildNav). The octree is rebuilt anyway; its file must exist.
    if _find_file(s.folder, s.level + ".OCT"):
        renamed[up + ".OCT"] = s.level + ".OCT"
    script = os.path.join(folder, name.lower() + ".lua")
    if os.path.exists(script):
        kept = not _upgrade_script(script, s, up)
        _update_layers_block(script, s)
    else:
        kept = False
        with open(script, "w", encoding="utf-8", newline="\n") as f:   # read back as UTF-8 (get_script)
            f.write(_mission_script(s, up, s.level))
        production.update_script(script, s)
        tactics.update_script(script, s)
        written.append(name.lower() + ".lua")
    written += _save_terrains(s, folder, refs)
    written += _save_roads(s, folder, refs)
    packed = _pack_models(s, folder, refs)
    banks = _pack_banks(s, folder, up)
    lines = [f"# {up}: game files this mission uses from the player's own install (DesertStormFix builds them at start-up -",
             "# nothing of the game is shipped with the mod). Written by the Desert Storm Editor."]
    lines += [f"file {dst} = {src}" for dst, src in renamed.items()]
    lines += [f"file {dst}" for dst in sorted(refs)]
    lines += [f"bank {b}" for b in banks]
    used_models = {str(r.get("model")).upper() for r in s.env.find("ENVI")}
    lines += [f"piece {name} = {k['source']} {k['index']}" for name, k in sorted(getattr(s, "kits", {}).items())
              if name in used_models]
    with open(os.path.join(folder, up + ".uses"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    written.append(up + ".uses")
    removed = _remove_game_copies(s, folder, up, refs)
    if campaign:
        cfile = os.path.join(folder, "campaign.txt")
        lines = open(cfile).read().splitlines() if os.path.exists(cfile) else [f"name = {campaign}"]
        entry = f"mission = {name.lower()}.lua"
        if not any(l.strip().lower() == entry for l in lines):
            lines.append(entry)
        with open(cfile, "w", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        written.append("campaign.txt")
    with open(os.path.join(folder, up + ".editor.json"), "w", newline="\n") as f:
        json.dump({"base": s.level, "blank": getattr(s, "blank", False), "terrains": sorted(getattr(s, "terrains", {})),
                   "roads": sorted(getattr(s, "roads", {})), "kits": getattr(s, "kits", {}),
                   "layers": {str(k): v for k, v in s.layers.items()},
                   "layer_start": s.layer_start, **logic.extras(s)}, f, indent=1)
    written.append(up + ".editor.json")
    s.dirty = False
    s.mission = (mod, up)
    return {"folder": folder, "files": written, "script_kept": kept, "packed_models": packed, "packed_banks": banks,
            "removed_game_copies": removed,
            "level": name.lower() + ".lua"}


def _script_path(s: Session):
    if not s.mission:
        raise CommandError("open or save a mission first (Save as mission / Open mission)")
    mod, up = s.mission
    return os.path.join(s.game_dir or "", "Mods", mod, up.lower() + ".lua")


@command("The open mission's Lua script.")
def get_script(s: Session):
    path = _script_path(s)
    if not os.path.exists(path):
        raise CommandError(f"{path} is missing - save the mission again to generate it")
    with open(path, encoding="utf-8") as f:
        return {"path": path, "text": f.read()}


@command("Write the open mission's Lua script (the editor's layers / regions block is refreshed from the scene).",
         text="the whole script")
def save_script(s: Session, text):
    path = _script_path(s)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("\r\n", "\n"))
    _update_layers_block(path, s)
    with open(path, encoding="utf-8") as f:
        return {"path": path, "text": f.read()}


@command("The game's messages about the open mission's script from Mods\\DesertStormFix-report.txt (load / run "
         "errors with their line numbers) - written when the game runs it.")
def script_report(s: Session):
    path = _script_path(s)
    name = os.path.basename(path).lower()
    report = os.path.join(s.game_dir or "", "Mods", "DesertStormFix-report.txt")
    if not os.path.exists(report):
        return {"lines": [], "report": report}
    with open(report, encoding="utf-8", errors="replace") as f:
        lines = [l.rstrip() for l in f if name in l.lower()]
    return {"lines": lines[-20:], "report": report}


def _game_running():
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DesertStorm.exe", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    return "DesertStorm.exe" in out


@command("Play the open mission: saves it (scene, routes, layers, regions - the script keeps your code), then starts "
         "DesertStorm.exe +level <mission>.lua (DesertStormFix presses Play in the launcher and loads the mission; "
         "the game quits after its end screen).")
def play(s: Session):
    import subprocess
    if not s.mission:
        raise CommandError("save it as a mission first (Save as mission…)")
    game = s.game_dir
    exe = os.path.join(game or "", "DesertStorm.exe")
    if not os.path.exists(exe):
        raise CommandError("game folder unknown - start the editor with --game")
    if _game_running():
        raise CommandError("the game is already running - close it first")
    found = {p.get("fix") for p in scene_problems(s)}
    if "zones" in found:
        s.call("fix_zones")                    # undoable like any edit
    if "crews" in found:
        s.call("fix_crews")
    if "hidden" in found:
        s.call("fix_hidden")
    problems = scene_problems(s)
    if problems:
        raise CommandError("fix these first - they crash the game: " +
                           "; ".join(f"line {p['line']}: {p['problem']}" for p in problems[:3]))
    mod, up = s.mission
    saved = save_mission(s, mod, up)
    import time
    proc = subprocess.Popen([exe, "+level", saved["level"]], cwd=game, creationflags=0x00000008)   # DETACHED_PROCESS
    s.game = {"proc": proc, "start": time.time(), "level": saved["level"], "mod": mod, "crash": None}
    return {"level": saved["level"], "mod": mod}


# ------------------------------------------------------------------------------------------------ game console --
# What the game is doing: DesertStormFix.log (written by dev / Steam builds of the plugin, rewritten at each start),
# whether the game the editor started is still running, and when it crashed, why: the Windows error report (event 1000:
# exception, address in DesertStorm.exe), the last mod file it loaded and the known causes.
_NOISE = ("[file] ", "Voices: ")
_KNOWN_CRASHES = {
    0x527FCE: "a zone prop (ENVI) whose model has no static mesh - usually a vehicle model placed as a prop",
}


def _vehicle_models(s: Session):
    """Models the shipped levels only use as vehicles (HLOI with a _VEH_ unit), never as zone props: model -> unit."""
    props, vehicles = set(), {}
    for e in _library(s):
        if e["kind"] == "prop":
            props.add(e["model"].upper())
        elif "_VEH_" in str(e.get("unit") or "").upper():
            vehicles.setdefault(e["model"].upper(), e["unit"])
    return {m: u for m, u in vehicles.items() if m not in props}


@command("Things in the open scene that crash the game (e.g. a vehicle model used as a zone prop), with lines.")
def scene_problems(s: Session):
    if not s.env:
        return []
    vehicles = _vehicle_models(s)
    zones = _zones(s)
    out = []
    for r in s.env.records:
        if r.kind not in ("HLOI", "ENVI") or not r.fields or (r.kind == "ENVI" and r.get("type") != 0):
            continue
        z = str(r.get("zone" if r.kind == "HLOI" else "parent"))
        if z not in zones:
            out.append({"line": r.index + 1, "name": r.get("name") or r.get("unit"), "fix": "zones",
                        "problem": f"{r.get('model')} hangs from {z}, which this level doesn't have (placed from "
                                   f"another level) - Fix puts it in the nearest zone"})
    base = _base_ids(s)
    for r in s.env.find("HLOI"):
        if r["in_play"] == 0 and r["script_id"] not in base and r["script_id"] not in s.layers:
            out.append({"line": r.index + 1, "name": r.get("stat"), "fix": "hidden",
                        "problem": f"{r['model']} ({r.get('unit')}) waits for the script but is in no layer, so it "
                                   f"never appears (its own level spawns it later). Fix puts it in play from the start"})
    ids = {h["script_id"]: h for h in s.env.find("HLOI")}
    for r in s.env.find("HLOI"):
        for unit, seat, cid in _crew(r):
            c = ids.get(cid)
            if c is None or str(c.get("unit")).upper() != str(unit).upper():
                what = f"{c['model']} ({c.get('unit')})" if c is not None else "nothing"
                out.append({"line": r.index + 1, "name": r.get("stat"), "fix": "crews",
                            "problem": f"{r['model']}'s crew seat {seat} ({unit}) is object {cid}, which here is "
                                       f"{what} - the game would put it in the vehicle and crash. Fix removes it"})
    for r in s.env.find("ENVI"):
        unit = vehicles.get(str(r.get("model", "")).upper())
        if unit:
            out.append({"line": r.index + 1, "name": r.get("name"),
                        "problem": f"zone prop {r.get('name')} uses the vehicle model {r['model']} ({unit}) - "
                                   f"place vehicles as objects from the Library"})
    return out


@command("Put every object / prop whose zone this level doesn't have into the zone of its nearest neighbour.",
         mutates=True)
def fix_zones(s: Session):
    zones, fixed = _zones(s), []
    for r in s.env.records:
        if r.kind not in ("HLOI", "ENVI") or not r.fields or (r.kind == "ENVI" and r.get("type") != 0):
            continue
        field = "zone" if r.kind == "HLOI" else "parent"
        if str(r.get(field)) in zones:
            continue
        zone = _nearest_zone(s, r.kind, r["pos"], skip=r.index)
        if zone:
            r[field] = zone
            fixed.append(r.index + 1)
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return {"fixed": fixed}


def _crash_event(since):
    """The newest Windows error report (Application log, event 1000) for DesertStorm.exe after `since`."""
    import subprocess, time, re
    age_ms = int(max(0, time.time() - since) * 1000) + 60000
    q = f"*[System[(EventID=1000) and TimeCreated[timediff(@SystemTime) <= {age_ms}]]]"
    try:
        out = subprocess.run(["wevtutil", "qe", "Application", f"/q:{q}", "/c:5", "/rd:true", "/f:text"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for event in out.split("Event["):
        if "DesertStorm.exe" not in event:
            continue
        get = lambda key: (re.search(key + r":\s*(\S+)", event) or [None, None])[1]
        module, code, offset = get("Faulting module name"), get("Exception code"), get("Fault offset")
        if not offset:
            continue
        info = {"module": (module or "").rstrip(","), "exception": code, "offset": offset}
        if info["module"].lower() == "desertstorm.exe":
            info["address"] = 0x400000 + int(offset, 16)
        return info
    return None


_EXCEPTIONS = {0xC0000005: "access violation (read / write through a bad pointer)", 0xC00000FD: "stack overflow",
               0xC0000094: "division by zero", 0xC000001D: "illegal instruction", 0x80000003: "breakpoint"}


def _crash_report(s: Session, code, log_lines):
    event = _crash_event(s.game["start"]) or {}
    report = {"exit_code": f"0x{code & 0xFFFFFFFF:08X}",
              "exception": _EXCEPTIONS.get(code & 0xFFFFFFFF, "the game stopped with an error")}
    if event:
        report["module"] = event["module"]
        if "address" in event:
            report["address"] = f"0x{event['address']:06X}"
            if event["address"] in _KNOWN_CRASHES:
                report["cause"] = _KNOWN_CRASHES[event["address"]]
        else:
            report["address"] = f"{event['module']} + {event['offset']}"
    mods = [l for l in log_lines if "Mods: " in l and " -> " in l]
    if mods:
        report["last_mod_file"] = mods[-1].split("Mods: ", 1)[1]
    plain = [l for l in log_lines if l.strip()]
    if plain:
        report["last_log_line"] = plain[-1]
    report["problems"] = scene_problems(s)
    return report


@command("The game console: new DesertStormFix.log lines since `offset` (bytes), whether the game the editor started "
         "is running, and a crash report when it stopped with an error.", offset="bytes already read (0 = from the start)",
         all_lines="include file opens and voice counters")
def game_console(s: Session, offset=0, all_lines=False):
    game = getattr(s, "game", None)
    path = os.path.join(s.game_dir or "", "DesertStormFix.log")
    lines, size = [], 0
    if os.path.exists(path):
        size = os.path.getsize(path)
        if size < offset:                      # a new run rewrote the log
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read(min(size - offset, 2 * 1024 * 1024))
        cut = chunk.rfind(b"\n") + 1           # whole lines only; the rest comes next time
        chunk = chunk[:cut]
        offset += len(chunk)
        lines = chunk.decode("utf-8", "replace").splitlines()
    shown = lines if all_lines else [l for l in lines if not any(n in l for n in _NOISE)]
    state = {"log": path if os.path.exists(path) else None, "offset": offset, "lines": shown,
             "running": _game_running(), "level": game and game["level"], "exit_code": None, "crash": None}
    if game:
        code = game["proc"].poll()
        state["running"] = code is None
        if code is not None:
            state["exit_code"] = code
            if code != 0 and game["crash"] is None:
                with open(path, encoding="utf-8", errors="replace") as f:
                    game["crash"] = _crash_report(s, code, f.read().splitlines()[-400:])
            state["crash"] = game["crash"]
    return state


@command("Missions saved with save_mission, by mod folder.", game_dir="game folder (default: the session's)")
def list_missions(s: Session, game_dir=None):
    game = game_dir or s.game_dir
    mods = os.path.join(game or "", "Mods")
    out = []
    if os.path.isdir(mods):
        for mod in sorted(os.listdir(mods)):
            folder = os.path.join(mods, mod)
            if os.path.isdir(folder):
                for f in sorted(os.listdir(folder)):
                    if f.upper().endswith(".EDITOR.JSON"):
                        out.append({"mod": mod, "name": f[:-len(".editor.json")].upper()})
    return out


@command("Open a mission saved with save_mission (its scene, routes and layers; models from its base level).",
         mod="mod folder", name="mission name", game_dir="game folder (default: the session's)")
def open_mission(s: Session, mod, name, game_dir=None):
    folder = os.path.join(game_dir or s.game_dir or "", "Mods", mod)
    up = name.upper()
    meta_path = os.path.join(folder, up + ".editor.json")
    if not os.path.exists(meta_path):
        raise CommandError(f"no mission {up} in {folder}")
    with open(meta_path) as f:
        meta = json.load(f)
    open_level(s, meta["base"])
    from conflict_ds_tools import gamerefs
    patch = os.path.join(folder, up + ".envpatch")
    if os.path.exists(patch):                  # scene = base scene + this mission's changes
        lines = gamerefs.apply_env_patch(open(patch, encoding="latin-1").read(), s.env.lines)
        s.env.lines = lines
        s.env._parse()
    else:                                      # saved before patches: a full copy
        s.env = EnvFile.load(os.path.join(folder, up + ".ENV"))
    rpatch, rmd = os.path.join(folder, up + ".rmdpatch"), os.path.join(folder, up + ".RMD")
    if os.path.exists(rpatch) and s.routes:
        s.routes = gamerefs.apply_rmd_patch(open(rpatch).read(), s.routes)
    elif os.path.exists(rmd):
        s.routes = RouteFile.load(rmd)
    s.layers = {int(k): v for k, v in meta.get("layers", {}).items()}
    s.blank = bool(meta.get("blank"))
    from conflict_ds_tools.terrain import Terrain
    s.terrains = {}
    for model in meta.get("terrains", []):
        src = os.path.join(folder, model + ".terrain.json")
        if os.path.exists(src):
            s.terrains[model] = Terrain.from_json(json.load(open(src)))
    from conflict_ds_tools.roads import Road
    s.roads = {}
    for model in meta.get("roads", []):
        src = os.path.join(folder, model + ".road.json")
        if os.path.exists(src):
            s.roads[model] = Road.from_json(json.load(open(src)))
    s.kits = dict(meta.get("kits", {}))
    for name, k in s.kits.items():             # the editor's local copies for the view
        try:
            _kit_cache(s, k["level"], k["source"][:-4], k["index"], name)
        except Exception:  # noqa: BLE001 - shown as a box then
            pass
    s.layer_start = dict(meta.get("layer_start", {}))
    logic.set_extras(s, meta)
    s.mission = (mod, up)
    return {"mission": [mod, up], "base": meta["base"], **s.env.summary()}


# -------------------------------------------------------------------------------------------- asset forms --
# .weapon / .skin files (the DesertStormFix mod formats, ModKit docs/MAKING_MODS.md) written from form fields, and the
# files they name uploaded into the mod folder.
WEAPON_KEYS = ["id", "based on", "display name", "faction", "damage", "time between shots", "magazine size",
               "magazines", "reload time", "range", "hearing range", "silenced", "muzzle flash", "texture", "model",
               "hud icon", "sound", "shot sound", "shot sound 2", "tail sound", "reload animation",
               "prone reload animation", "aim animation", "prone aim animation"]
SKIN_KEYS = ["uniform", "uniform name", "soldier", "texture", "first name", "last name", "portrait", "body",
             "normal map", "voice"]
UPLOAD_EXTS = {".dds", ".tga", ".png", ".evo", ".wav", ".prb", ".sch"}
HAND_WEAPONS = {"MACHINE GUN", "PISTOL", "SNIPER RIFLE", "SUB-MACHINE GUN", "ASSAULT RIFLE", "SHOTGUN",
                "GRENADE LAUNCH/RIFLE", "ANTI TANK", "SMOKE GRENADE"}


def _mod_folder(s: Session, mod, create=True):
    if not mod or any(c in mod for c in '\\/:*?"<>|') or mod.startswith("."):
        raise CommandError("bad mod name")
    if not s.game_dir:
        raise CommandError("game folder unknown - start the editor with --game")
    folder = os.path.join(s.game_dir, "Mods", mod)
    if create:
        os.makedirs(folder, exist_ok=True)
    return folder


@command("Choices for the asset forms: weapons to base on, soldiers, uniforms, factions, existing mods.")
def asset_options(s: Session):
    weapons = []
    path = _find_file(os.path.join(s.data_dir, "catalog"), "WEAPS.TXT") if os.path.isdir(os.path.join(s.data_dir, "catalog")) else None
    if path:
        for line in open(path, errors="replace").read().splitlines():
            cols = [c.strip() for c in line.split(",")]
            if len(cols) > 5 and cols[4] != "-1" and cols[5] in HAND_WEAPONS and cols[0] != "US_WPN_GrenadeFrag":
                weapons.append({"id": cols[0], "kind": cols[5].title(), "model": cols[1]})
    mods = []
    if s.game_dir and os.path.isdir(os.path.join(s.game_dir, "Mods")):
        mods = sorted(d for d in os.listdir(os.path.join(s.game_dir, "Mods"))
                      if os.path.isdir(os.path.join(s.game_dir, "Mods", d)))
    return {"weapons": weapons, "soldiers": ["Bradley", "Foley", "Connors", "Jones"],
            "uniforms": ["SAS", "Delta", "Russian", "Iraqi"],
            "factions": ["SAS", "Delta", "Russian", "Iraqi", "allied", "eastern", "all"], "voices": ["SAS", "Delta"],
            "mods": mods, "weapon_keys": WEAPON_KEYS, "skin_keys": SKIN_KEYS}


def _write_mod_file(folder, filename, keys, fields):
    unknown = [k for k in fields if k not in keys]
    if unknown:
        raise CommandError(f"unknown setting(s): {', '.join(unknown)}")
    lines = [f"# made with the Desert Storm Editor"]
    width = max((len(k) for k in fields), default=0)
    for k in keys:
        v = str(fields.get(k, "")).strip()
        if v:
            if "\n" in v or "#" in v:
                raise CommandError(f"{k}: no line breaks or # in values")
            lines.append(f"{k.ljust(width)} = {v}")
    path = os.path.join(folder, filename)
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


@command("Write a .weapon file into a mod (only the settings given; see the ModKit guide for each).",
         mod="mod folder", fields="{setting: value} - id and based on at least")
def create_weapon(s: Session, mod, fields):
    import re
    wid = str(fields.get("id", "")).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,40}", wid):
        raise CommandError("id: letters, digits and _ (e.g. US_WPN_HandCannon)")
    path = _write_mod_file(_mod_folder(s, mod), wid + ".weapon", WEAPON_KEYS, fields)
    return {"path": path}


@command("Write a .skin file into a mod (a uniform when 'uniform name' is set, else a new recruit with first / last "
         "name).", mod="mod folder", name="file name (without .skin)", fields="{setting: value}")
def create_skin(s: Session, mod, name, fields):
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", name or ""):
        raise CommandError("file name: letters, digits and _")
    for k in ("uniform", "soldier", "texture"):
        if not str(fields.get(k, "")).strip():
            raise CommandError(f"{k} is needed")
    if not str(fields.get("uniform name", "")).strip() and not (fields.get("first name") and fields.get("last name")):
        raise CommandError("give a uniform name (a new uniform) or a first and last name (a new recruit)")
    path = _write_mod_file(_mod_folder(s, mod), name + ".skin", SKIN_KEYS, fields)
    return {"path": path}


@command("Put a file (texture, model, picture, sound, animation) into a mod folder. as_dds: a PNG is stored as "
         "<name>.dds (DXT1, DXT5 with transparency, or dds_format e.g. DXT3 for portraits) - the game's texture format.",
         mod="mod folder", filename="file name", data_base64="the file's bytes, base64", as_dds="PNG -> DDS",
         dds_format="DXT1 / DXT3 / DXT5 (default: by transparency)")
def upload_file(s: Session, mod, filename, data_base64, as_dds=False, dds_format=None):
    import base64, re
    name = os.path.basename(filename or "")
    stem, ext = os.path.splitext(name)
    if ext.lower() not in UPLOAD_EXTS or not re.fullmatch(r"[A-Za-z0-9_\- ]{1,60}", stem):
        raise CommandError(f"allowed: {', '.join(sorted(UPLOAD_EXTS))} with a plain file name")
    data = base64.b64decode(data_base64)
    if len(data) > 32 * 1024 * 1024:
        raise CommandError("file over 32 MB")
    if as_dds and ext.lower() == ".png":
        from conflict_ds_tools import images
        try:
            w, h, rgba = images.decode_png(data)
        except Exception as e:  # noqa: BLE001
            raise CommandError(f"can't read the PNG: {e}") from None
        if w & (w - 1) or h & (h - 1) or w < 4 or h < 4:
            raise CommandError(f"the game needs power-of-two sizes (256, 512 ...): this one is {w} x {h}")
        data = images.encode_dds(w, h, rgba, fmt=dds_format)
        name = stem + ".dds"
    path = os.path.join(_mod_folder(s, mod), name)
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path, "name": stem, "file": name, "bytes": len(data)}


@command("Files in a mod folder.", mod="mod folder")
def mod_files(s: Session, mod):
    folder = _mod_folder(s, mod, create=False)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


@command("The command list with descriptions and arguments (what an MCP client sees).")
def help(s: Session):  # noqa: A001
    import inspect
    out = {}
    for n, (fn, d, a, m) in COMMANDS.items():
        params = list(inspect.signature(fn).parameters.values())[1:]
        out[n] = {"description": d, "args": a, "changes_level": m,
                  "required": [p.name for p in params if p.default is inspect.Parameter.empty and p.name in a]}
    return out

import logic  # noqa: E402  (objectives, rules, AI profiles, atmosphere, cameras: editor/logic.py)
if getattr(logic, "_registered", False):     # reloaded: register its commands into the new COMMANDS
    importlib.reload(logic)
logic._registered = True
import records  # noqa: E402  (data tables: editor/records.py)
if getattr(records, "_registered", False):   # this file was reloaded: COMMANDS is new, register the records again
    importlib.reload(records)
records._registered = True
import studio  # noqa: E402  (walk map, minimap, prefabs, vehicle crews, sound audition: editor/studio.py)
if getattr(studio, "_registered", False):
    importlib.reload(studio)
studio._registered = True
import production  # noqa: E402  (campaigns, briefing, soundtrack, budget, spawns, release zip: editor/production.py)
if getattr(production, "_registered", False):
    importlib.reload(production)
production._registered = True
import tactics  # noqa: E402  (vision cones, minefields, emplacements, air support: editor/tactics.py)
if getattr(tactics, "_registered", False):
    importlib.reload(tactics)
tactics._registered = True
import interactive  # noqa: E402  (switches + doors, particle emitters: editor/interactive.py)
if getattr(interactive, "_registered", False):
    importlib.reload(interactive)
interactive._registered = True
