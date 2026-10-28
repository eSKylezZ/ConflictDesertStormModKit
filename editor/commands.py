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
import itertools
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
from conflict_ds_tools import games  # noqa: E402
from conflict_ds_tools import modlayout  # noqa: E402
from conflict_ds_tools.env import EnvFile, ZONE_TYPES  # noqa: E402
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


def catalog_dir(s) -> str:
    """The extracted catalog archive's folder ("catalog"; Denied Ops: "catalog.pc")."""
    for name in ("catalog", "catalog.pc"):
        d = os.path.join(s.data_dir, name)
        if os.path.isdir(d):
            return d
    return os.path.join(s.data_dir, "catalog")


def _find_file(folder, name):
    """A file in an extracted archive folder by name, or as _<HASH>.* when it was extracted without a name."""
    for f in os.listdir(folder):
        if f.upper() == name.upper():
            return os.path.join(folder, f)
    hashed = glob.glob(os.path.join(folder, "_%08X.*" % archive_hash(name.upper())))
    return hashed[0] if hashed else None


def mod_layout(s) -> str:
    """The layout new mods get (Session.mod_layout: --mod-layout / set_mod_layout; default organised)."""
    return getattr(s, "mod_layout", None) or modlayout.ORGANISED


def _mod_out(s, folder, name, create=True):
    """Where the editor writes `name` into a mod folder (conflict_ds_tools.modlayout: the mod's own copy anywhere is
    rewritten in place, a flat mod stays flat, else the organised folder)."""
    return modlayout.path_for(folder, name, layout=mod_layout(s), create=create)


def _mod_path(s, folder, name):
    """A mod file to read and write back: the mod's copy anywhere, else where a new one would go (no folders made)."""
    return modlayout.find(folder, name) or _mod_out(s, folder, name, create=False)


def _mod_remove(folder, name):
    """Remove every copy of `name` in the mod (a flat leftover and an organised copy can both be there); True if any."""
    hits = modlayout.find_all(folder, name)
    for p in hits:
        os.remove(p)
    return bool(hits)


_entry_ids = itertools.count(1)       # undo entries' ids (Session._state_token)


class Session:
    def __init__(self, data_dir, game_dir=None):
        self.data_dir = os.path.abspath(data_dir)
        self.game_dir = game_dir
        # the game: its folder's exe, else the extracted folder's .game marker (ds_extract writes it), else the current one
        self.profile = games.use_folder(game_dir) or games.detect_extracted(data_dir) or games.current()
        games.set_current(self.profile)       # (s.game is the game process Play started)
        if self.profile.id == "do":           # Denied Ops: models / textures live in APKG packages - unpack them once
            from conflict_ds_tools import apkg
            apkg.unpack_extracted(self.data_dir)
        self.level = None          # e.g. "MISSION1"
        self.folder = None         # extracted folder of its archive
        self.env: EnvFile | None = None
        self.routes: RouteFile | None = None
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.layers = {}           # script id -> layer name
        self.layer_start = {}      # layer name -> "hidden" (waits for game.show_layer, the default) / "shown"
        self.mission = None        # (mod folder, NAME) once saved / opened as a mission
        self.game_mission = None   # (mod, Mission stem, source folder): a game mission opened as Lua (game_missions.py)
        self.terrains = {}         # model name -> conflict_ds_tools.terrain.Terrain (placed by an ENVI record)
        self.roads = {}            # model name -> conflict_ds_tools.roads.Road (placed by an ENVI record)
        self.kits = {}             # KIT_<MODEL>_<n> -> {"level", "source", "index", "centre"} (kits.py pieces)
        self.objectives, self.rules, self.patrols, self.objectives_win = [], [], {}, False   # logic.py
        self.mod_layout = modlayout.ORGANISED   # where new mods' files go (modlayout.ORGANISED / FLAT; older mods keep theirs)

    # --- unsaved changes: the history position at the last save / open, compared with the current one, so undoing back
    # to the saved state is clean again. Each undo entry carries an id that moves with it between the undo and redo
    # stacks. dirty = False marks the current position as saved; dirty = True forces "unsaved" until the next save.
    def _state_token(self):
        top = self.undo_stack[-1] if self.undo_stack else None
        return top[3] if top is not None and len(top) > 3 else (0 if top is None else id(top))

    @property
    def dirty(self):
        if self.__dict__.pop("dirty", None):         # a session from before this was a property (server reload)
            self._saved_token = object()             # kept unsaved: its undo history is cleared by the reload
        return self._state_token() != getattr(self, "_saved_token", 0)

    @dirty.setter
    def dirty(self, value):
        self.__dict__.pop("dirty", None)
        self._saved_token = object() if value else self._state_token()

    # --- undo: a snapshot of the scene lines and the route markers before each change ---------------------------
    def _snapshot(self):
        return (list(self.env.lines), _copy.deepcopy(self.routes.markers) if self.routes else None, dict(self.layers),
                dict(self.layer_start), {k: t.dumps() for k, t in getattr(self, "terrains", {}).items()},
                {k: r.dumps() for k, r in getattr(self, "roads", {}).items()}, json.dumps(logic.extras(self)),
                _copy.deepcopy(getattr(self, "kits", {})))

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
        if len(snap) > 7:
            self.kits = _copy.deepcopy(snap[7])
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
                raise CommandError("no level open - open_level or open_mission first")
            before = self._snapshot()
            subject = _subject(self, args or {})     # named before the change: a delete removes the record
            try:
                result = fn(self, **(args or {}))
            except Exception as e:
                self._restore(before)
                workflow.log_edit(self, name, args, error=e)
                raise
            title = _action_title(name, subject, result)
            if self._snapshot() == before:           # same value, a dry run: nothing to undo, nothing unsaved
                workflow.log_edit(self, name, args, title, unchanged=True)
                return {**result, "unchanged": True} if isinstance(result, dict) else result
            if isinstance(result, dict) and "action" not in result:
                result = {**result, "action": title}
            self.undo_stack.append((title, before, name, next(_entry_ids)))
            self.redo_stack.clear()
            self.revision = getattr(self, "revision", 0) + 1   # the browser redraws when someone else changed it
            workflow.log_edit(self, name, args, title)
            workflow.after_change(self)
            return result
        result = fn(self, **(args or {}))
        if name in _CHANGES_VIEW:
            self.revision = getattr(self, "revision", 0) + 1
        return result

    def record(self, line):
        if self.env is None:
            raise CommandError("no level open - open_level or open_mission first")
        try:
            return self.env.record_at(int(line))
        except KeyError:
            raise CommandError(f"no record on line {line} - lines shift after inserts / deletes; search or find "
                               "gives the current ones") from None
        except (TypeError, ValueError):
            raise CommandError(f"line: a record's line number, not {line!r}") from None


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
@command("The game the editor works on: id (ds1 ds2 cv cg), title, short name, exe, plugin.")
def game_info(s: Session):
    g = s.profile
    return {"id": g.id, "title": g.title, "short": g.short, "exe": g.exes[0], "plugin": g.plugin,
            "script_host": g.script_host, "mod_layout": mod_layout(s)}


@command("Levels in the data folder that have a scene file.")
def list_levels(s: Session):
    return sorted(_level_map(s))


def _level_map(s: Session) -> dict:
    """{LEVEL: extracted folder} - a level is a scene file (<LEVEL>.ENV starting "Version"); usually in the archive
    folder of the same name (mission1/MISSION1.ENV), The Great Escape's in another (level01/GE_LEVEL01.ENV)."""
    cached = getattr(s, "_levels", None)
    if cached is not None and cached[0] == s.data_dir:
        return cached[1]
    out = {}
    for d in sorted(os.listdir(s.data_dir)):
        folder = os.path.join(s.data_dir, d)
        if not os.path.isdir(folder) or d.startswith("_"):
            continue
        for f in os.listdir(folder):
            if f.upper().endswith(".ENV") and not f.startswith("_"):
                with open(os.path.join(folder, f), "rb") as fh:
                    if fh.read(7) == b"Version":
                        out.setdefault(f[:-4].upper(), folder)
    s._levels = (s.data_dir, out)
    return out


def level_folder(s: Session, level) -> str:
    """The extracted folder with a level's scene (see _level_map); <data>/<level> when unknown."""
    level = str(level).upper()
    d = os.path.join(s.data_dir, level.lower())
    if os.path.isdir(d) and _find_file(d, level + ".ENV"):
        return d
    return _level_map(s).get(level, d)


@command("Open a level's scene and routes.", level="level name, e.g. MISSION1")
def open_level(s: Session, level):
    level = level.upper()
    s.game_mission = None   # (game_mission_open sets it again after opening the level)
    folder = level_folder(s, level)
    path = _find_file(folder, level + ".ENV") if os.path.isdir(folder) else None
    if not path:
        raise CommandError(f"no scene for {level} in {s.data_dir}")
    s.level, s.folder = level, folder
    s.env = EnvFile.load(path)
    rmd = _find_file(folder, level + ".RMD") or _find_file(folder, level + ".RDA")   # Global Storm: routes in .RDA
    try:
        s.routes = RouteFile.load(rmd) if rmd else None
    except ValueError:                         # a route layout the kit doesn't read yet (Denied Ops' .RDA)
        s.routes = None
    s.undo_stack.clear(), s.redo_stack.clear()
    s.dirty = False
    s.layers, s.layer_start, s.mission = {}, {}, None
    s.mission_dll = None
    s.terrains = {}
    s.roads = {}
    s.kits = {}
    s.blank = False
    logic.set_extras(s, None)
    s._base_objectives = None
    # the base mission's own data (editor/games/<game>/level_base.json, texts from the player's game): its objectives, briefing,
    # music and the objects its DLL needs - resolved now so the panels start filled
    s.base_objectives = logic.base_objectives(s)
    return {"level": level, **s.env.summary(), "route_markers": len(s.routes.markers) if s.routes else 0,
            "base_objectives": len(s.base_objectives), "base_targets": len(logic.base_targets(s))}


def _unit_rows(s: Session):
    """UNITS.TXT rows by unit name (upper case), from the extracted catalog (Global Storm: col 2 body model, 5 body
    texture, 6 head model, 7 head texture). Cached per data folder."""
    cache = getattr(s, "_units_cache", None)
    if cache is None or cache[0] != s.data_dir:
        rows = {}
        folder = catalog_dir(s)
        path = _find_file(folder, "UNITS.TXT") if os.path.isdir(folder) else None
        if path is None and os.path.isdir(folder):
            # Denied Ops: the table's name isn't recovered - it is the unnamed catalog file of "<unit>,SOLDIER,<body>" rows
            for f in sorted(os.listdir(folder)):
                p = os.path.join(folder, f)
                if f.upper().endswith(".BIN") and os.path.getsize(p) < 1 << 20:
                    head = open(p, "rb").read(4096)
                    if re.search(rb"\n\w+,SOLDIER,\w+,", head):
                        path = p
                        break
        if path:
            for line in open(path, encoding="latin-1").read().splitlines():
                cols = [c.strip() for c in line.split(",")]
                if len(cols) > 7:
                    rows.setdefault(cols[0].upper(), cols)
        cache = s._units_cache = (s.data_dir, rows)
    return cache[1]


def _soldier_look(s: Session, r) -> dict:
    """Global Storm: the body texture ("skin") and head {model, skin} of an HLOI soldier - its own body / head texture
    fields, NOTEXTURE = the unit's defaults from UNITS.TXT. Empty for other games / objects.
    Denied Ops: the head model is on the line itself (a name before the zone) and attachments are {"bone", int,
    "model"} without the float."""
    if s.profile.id == "do":
        if r.kind != "HLOI" or "unit" not in r.fields:
            return {}
        row = _unit_rows(s).get(str(r["unit"]).upper())
        out = {}
        # the unit's row (Global Storm's UNITS.TXT layout): body texture col 5, head model col 6, head texture col 7;
        # a head named on the line (HLOU) replaces the unit's
        if row and row[5] not in ("", "-1"):
            out["skin"] = row[5]
        head = str(r["head"]).upper() if "head" in r.fields else (row[6].upper() if row and row[6] not in ("", "-1") else "")
        if head:
            out["head"] = {"model": head, "skin": "" if "head" in r.fields or not row else row[7]}
        toks, att = r.tokens, []
        for i in range(len(toks) - 2):
            b, m = toks[i], toks[i + 2]
            if b.quoted and m.quoted and re.fullmatch(r"-?\d+", toks[i + 1].text) and                     (b.text[1:].upper().startswith("BIP01") or "CONNECTOR" in b.text.upper()):
                att.append({"bone": b.text.strip('"'), "model": m.text.strip('"').upper()})
        if att:
            out["attach"] = att
        return out
    if "body" not in r.fields:
        return {}
    def tex(v):
        v = str(v or "")
        return "" if v.upper() in ("", "NOTEXTURE", "-1") else v
    row = _unit_rows(s).get(str(r["unit"]).upper())
    out = {}
    # attachments at the end of the line: n x {"bone", int, "model", float} (hats, radio, pouches, holsters) - rigid
    # models in that bone's space
    toks, att = r.tokens, []
    for i in range(len(toks) - 3):
        b, m = toks[i], toks[i + 2]
        if b.quoted and m.quoted and re.fullmatch(r"-?\d+", toks[i + 1].text) and \
                re.fullmatch(r"-?\d+\.\d+", toks[i + 3].text) and \
                (b.text[1:].upper().startswith("BIP01") or "CONNECTOR" in b.text.upper()):
            att.append({"bone": b.text.strip('"'), "model": m.text.strip('"').upper()})
    if att:
        out["attach"] = att
    skin = tex(r["body"]) or (tex(row[5]) if row else "")
    if skin:
        out["skin"] = skin
    if row and tex(row[6]):
        out["head"] = {"model": row[6].upper(), "skin": tex(r["head"]) or tex(row[7])}
    return out


@command("Everything the viewport draws: world zones, props, objects, waypoints, regions, cameras, sounds, lights, "
         "route markers.")
def scene(s: Session):
    _spread_lifts(s)
    if s.env is None:
        raise CommandError("no level open")
    out = {"level": s.level, "mission": list(s.mission) if s.mission else None, "zones": [], "chunks": [], "props": [], "objects": [], "waypoints": [], "regions": [], "cameras": [],
           "sounds": [], "lights": [], "routes": []}
    chunks = _chunk_map(s)
    for r in s.env.records:
        k, line = r.kind, r.index + 1
        try:
            if k == "ENVI" and r.fields:
                ground = getattr(s, "terrains", {}).get(str(r["model"]).upper())
                if r["type"] in r.file.zone_types() and ground is not None and ground.zone:
                    out["props"].append({"line": line, "name": r["name"], "model": r["model"], "pos": list(ground.origin),
                                         "rot": (0, 0, 0), "scale": (1, 1, 1), "terrain": ground.to_json(),
                                         "ground": True})
                elif r["type"] in r.file.zone_types() and r["parent"].upper() == "ROOT" and str(r["model"]).upper() not in chunks:
                    out["zones"].append({"line": line, "name": r["name"], "model": r["model"], "pos": r["pos"],
                                         "rot": r["rot"], "scale": r["scale"]})   # MP_MISSION1's sits at (600,10,-16000)
                elif r["type"] in r.file.zone_types() and str(r["model"]).upper() in chunks:      # one chunk of a zone model
                    c = chunks[str(r["model"]).upper()]
                    if c["root"]:
                        out["zones"].append({"line": line, "name": r["name"], "model": r["model"], "pos": r["pos"],
                                             "rot": r["rot"], "scale": r["scale"], "chunked": True})
                    out["chunks"].append({"line": line, "name": r["name"], "model": str(r["model"]).upper(),
                                          "zone": c["zone"], "index": c["index"], "root": c["root"], "pos": r["pos"],
                                          "rot": r["rot"], "scale": r["scale"],
                                          "pivot": list(_kit_centre_world(r, c))})
                elif r.file.is_prop(r):
                    out["props"].append({"line": line, "name": r["name"], "model": r["model"], "pos": r["pos"],
                                         "rot": r["rot"], "scale": r["scale"] if "scale.x" in r.fields else (1, 1, 1)})
                    tex = str(r.get("texture") or "")     # the record's own texture (Global Storm: car paint, variants)
                    if tex and tex.upper() != "NOTEXTURE":
                        out["props"][-1]["skin"] = tex
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
                                       **_soldier_look(s, r),
                                       "script_id": r["script_id"], "pos": r["pos"], "rot": r["rot"],
                                       "active": r["in_play"], "layer": s.layers.get(r["script_id"], ""),
                                       "cutscene": bool(r["flags"] & 0x20000)})
            elif k == "NEWENVWAYPT" and r.fields:
                out["waypoints"].append({"line": line, "id": r["id"], "name": r["name"], "pos": r["pos"],
                                         "next": r.get("next", -1), "wait": (r.get("wait_ms") or 0) / 1000})
            elif k == "REGN" and r.fields:
                reg = {"line": line, "name": r["name"], "type": r["type"], "pos": r["pos"]}
                if r["type"] == 1:
                    reg["radius"] = r["radius"]
                else:
                    reg.update(rot=r["rot"], min=r["min"], max=r["max"])
                out["regions"].append(reg)
            elif k.startswith("CAM") and r.fields:
                out["cameras"].append({"line": line, "kind": k, "name": r["name"], "pos": r["pos"]})
            elif k == "NEWPOSMARKER" and r.fields:      # Global Storm AI position markers
                out.setdefault("aipoints", []).append({"line": line, "kind": "marker", "id": r["id"], "pos": r["pos"],
                                                       "name": f"marker {r['id']}"})
            elif k == "ENVCOVERPOINT" and r.fields:     # cover points (Global Storm: + facing and arc)
                out.setdefault("aipoints", []).append({"line": line, "kind": "cover", "id": r["id"], "pos": r["pos"],
                                                       "name": r["name"], "facing": r.get("facing"), "arc": r.get("arc")})
            elif k in ("PLGT", "SLGT", "DLGT") and "pos.x" in r.fields:
                lt = {"line": line, "kind": k, "name": r["name"], "pos": r["pos"],
                      "colour": [r[f"colour{i}"] for i in range(3)]}
                if k == "PLGT":
                    lt["radius"] = r["radius"]
                elif "pitch" in r.fields:             # Global Storm
                    lt["dir"] = [r["pitch"], r["yaw"]]
                    if k == "SLGT":
                        lt.update(range=r["range"], cone=r["cone_outer"])
                elif k == "SLGT":                     # Vietnam: rot vector, cones cone0 / cone1
                    lt.update(dir=list(r["rot"])[:2], range=r["range"], cone=r["cone1"])
                out["lights"].append(lt)
            elif k == "ENVSND" and r.fields:
                out["sounds"].append({"line": line, "name": r["name"], "sound": r["sound"], "pos": r["pos"],
                                      "min_distance": r["min_distance"], "max_distance": r["max_distance"]})
        except KeyError:
            continue
    if s.routes:
        out["routes"] = [m.as_dict(i) for i, m in enumerate(s.routes.markers)]
    logic.decorate_scene(s, out)
    interactive.decorate_scene(s, out)
    vanilla.decorate_scene(s, out)
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
    _chunk_guard(s, r, "changed")
    kit = (getattr(s, "kits", {}).get(str(r.get("model")).upper()) or _chunk(s, r)) if r.kind == "ENVI" else None
    if kit and field == "rot" and isinstance(value, list):   # kit pieces / zone chunks turn about their centre
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
    if r.kind == "HLOI" and field == "script_id":
        _check_script_id(s, r, value)
    if isinstance(value, list):
        value = _vec(value)
    try:
        r[field] = value
    except (KeyError, ValueError) as e:
        raise CommandError(str(e)) from None
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return get_record(s, line)


def _check_script_id(s: Session, r, value):
    """Script ids are how scripts (and the base mission's DLL) find objects: unique, and the DLL's ones fixed."""
    try:
        new = int(value)
    except (TypeError, ValueError):
        raise CommandError("script_id: a whole number") from None
    old = r["script_id"]
    if new == old:
        return
    targets = logic.base_targets(s)
    dll = _base_dll(s, s.level) or "the base mission's DLL"
    if old in targets:
        raise CommandError(f"script id {old} is DLL critical: {dll} uses it (API slots "
                           f"{', '.join('+0x%X' % x for x in targets[old])}) - changing it would break or crash the level")
    if new in targets:
        raise CommandError(f"script id {new} is reserved: {dll} addresses that object directly")
    if any(o is not r and o["script_id"] == new for o in s.env.find("HLOI") if o.fields):
        raise CommandError(f"script id {new} is already used by another object")


@command("Move a record (props, objects, waypoints, regions, cameras, sounds) to a position.", mutates=True,
         line="record line", pos="[x, y, z] game units")
def move(s: Session, line, pos):
    r = s.record(line)
    t = getattr(s, "terrains", {}).get(str(r.get("model")).upper()) if r.kind == "ENVI" else None
    if t is not None and t.zone:                # a level's ground: world-space vertices around its origin
        t.origin = tuple(_vec(pos))
        return get_record(s, line)
    _chunk_guard(s, r, "moved")
    kit = (getattr(s, "kits", {}).get(str(r.get("model")).upper()) or _chunk(s, r)) if r.kind == "ENVI" else None
    if kit:                                     # a kit piece / zone chunk: pos = where its centre goes (pivot = origin)
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
        stat = str(r.get("stat") or "")
        if s.profile.id == "ds1" and re.search(r"[A-Za-z]", stat) and stat.upper() != "NONE":
            # its own name = its own editor ID: save_mod writes the copy as a record edit (based_on), which other mods'
            # copies don't collide with (placed.split_edits)
            changes["stat"] = _unique(s, "HLOI", "stat", stat)
    if r.kind == "NEWENVWAYPT":
        changes["id"] = paths._free_ids(s, 1)[0]
        changes["next"] = -1
    if r.kind == "ENVI" and r.fields and "name" in r.fields:
        _chunk_guard(s, r, "duplicated")
        changes["name"] = _unique(s, "ENVI", "name", str(r["name"]))   # world piece / prop names are unique per scene
    crew = _crew_texts(s.env, r) if r.kind == "HLOI" else []
    new = s.env.duplicate(r, **changes)
    _drop_rinfo_flag(new)
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
        if r.kind not in _PASTE_END or (r.kind == "ENVI" and (not r.file.is_prop(r) or r.children)):
            raise CommandError(f"line {ln}: {r.kind} records can't be copied (world pieces stay with their zone)")
        out.append({"kind": r.kind, "text": r.text, "pos": list(r["pos"]), "level": s.level, **_block_of(s.env, r),
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


RINFO_FOLLOWS = 0x1000000


def _drop_rinfo_flag(r):
    """A copied prop record loses ENVI flag 0x1000000 = "a RINFO line follows" (the scene loader FUN_004f8d00 then reads
    the next line as the prop's render info, FUN_004fb2c0 - its baked colour for the original spot). Copies are made of
    the ENVI line alone, so with the flag left on the loader ate the following record and every read after it was off
    (a copied CRATE2 made the next crate "100 active lights" - its break health 100.0 read as a light count)."""
    if r.kind == "ENVI" and r.fields and "flags" in r.fields and int(r["flags"]) & RINFO_FOLLOWS:
        r["flags"] = int(r["flags"]) & ~RINFO_FOLLOWS


def _missing_rinfo(s: Session):
    """ENVI records with the RINFO flag whose next line isn't a RINFO line."""
    out = []
    for r in s.env.find("ENVI"):
        if r.fields and "flags" in r.fields and int(r["flags"]) & RINFO_FOLLOWS:
            nxt = next((x for x in s.env.records if x.index > r.index + len(r.children)), None)
            if nxt is None or nxt.kind != "RINFO":
                out.append(r)
    return out


@command("Clear the RINFO flag of props whose RINFO line is missing (copies made by older editor versions).",
         mutates=True)
def fix_rinfo(s: Session):
    fixed = [r.index + 1 for r in _missing_rinfo(s)]
    for ln in fixed:
        _drop_rinfo_flag(s.record(ln))
    return {"fixed": fixed}


def _insert_new(s: Session, keyword, text, block=None, tag=None):
    """Insert a new ENVI / HLOI (a library template or a copy) where the scene keeps that kind; returns its record.
    Denied Ops: the whole block (env.do_block) at the end of its section, counts and links kept by env.py; other
    games: the line before the section end (ENDI / HLOX), the RINFO flag dropped (copies are the line alone)."""
    if s.env.version >= 300:
        if block is None:
            if keyword != "ENVI":
                raise CommandError("objects in a Denied Ops scene can only be copied from a Denied Ops scene")
            block, tag = [' <SGNODE> 1 0 "" </SGNODE>', text, "<MaterialOverrides> 0", "</MaterialOverrides>"], \
                "ENVINSTANCES"
        before = len(s.env.lines)
        try:
            new = s.env.insert_block(block, tag or ("ENVINSTANCES" if keyword == "ENVI" else "HLOS"), keyword)
        except (KeyError, StopIteration) as e:
            raise CommandError(str(e).strip("'\""))
        # (where, how many lines): the block went in before the section's close tag; the counts / link lines it
        # changed or added lie after it, below any earlier paste in that section
        s.last_insert = (new.index - block.index(next(b for b in block if b.lstrip().startswith(keyword) or
                                                     b.lstrip().startswith(("PHYI", "HLOU")))),
                         len(block))
        return new
    end = _PASTE_END[keyword]
    at = next((r.index for r in s.env.records if r.kind == end), None)
    if at is None:
        raise CommandError(f"this scene has no {end} section")
    s.env.lines.insert(at, text)
    s.env._parse()
    new = s.env.record_at(at + 1)
    _drop_rinfo_flag(new)
    s.last_insert = (at, 1)
    return new


def _block_of(env, r):
    """{"block", "tag"} for a Denied Ops record (copied with its block), else {}."""
    if env.version < 300:
        return {}
    block, tag = env.do_block(r)
    return {"block": block, "tag": tag}


def _paste_entries(s: Session, entries, pos, yaw=0.0, anchor=None):
    """Insert copied records ({kind, text, pos, crew}) so that `anchor` (default: the first one's position) lands on
    pos, the group turned by yaw degrees about it; new script ids / waypoint ids / unique names, nearest zones.
    Returns the new records' lines."""
    anchor = anchor if anchor is not None else entries[0]["pos"]
    yaw = float(yaw or 0.0)
    new_lines = []
    for e in entries:
        r = _insert_new(s, e["kind"], e["text"], e.get("block"), e.get("tag"))
        at, added = s.last_insert
        new_lines = [l + added if l >= at + 1 else l for l in new_lines]   # earlier pastes below the insertion move
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
            r["id"] = paths._free_ids(s, 1)[0]         # the game's table holds ids 0-599
            r["next"] = -1
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
    if not _is_vehicle(rec) or "kit_count" not in rec.fields:     # other games' HLOI layouts don't name the kit
        return []
    return [(rec[f"kit{k}.item"], rec[f"kit{k}.category"], rec[f"kit{k}.amount"]) for k in range(rec["kit_count"])]


# Inven.txt column 2 (the item's type) -> its kit category: the game's own table (strcmp chain at 0x493B40 in
# DesertStorm.exe). Weapons (Weaps.txt) are category 5.
ITEM_TYPES = {"HUMAN": 1, "MACHINE": 2, "BUILDING": 3, "FEATURE": 4, "WEAPON": 5, "UNIFORM": 6, "PHRASE BOOK": 7,
              "MEDI KIT": 8, "C4 DETONATOR": 9, "RADIO": 10, "BODY ARMOUR": 11, "HEAD ARMOUR": 12, "AMMO": 14, "DOOR": 15,
              "BINOCULARS": 16, "NIGHT VISION GOGGLES": 17, "LASER DESIGNATOR": 18, "KEY": 20, "TARGET": 21,
              "MISCELLANEOUS": 22, "COMPUTER HACKING KIT": 24, "WIRECUTTERS": 25, "BOMB DEFUSING KIT": 26}


def _kit_categories(s: Session):
    """Kit item -> category: weapons 5, inventory items by their Inven.txt type."""
    out = {w: 5 for w in _table_keys(s, "WEAPS.TXT")}
    for cols in _table_rows(s, "INVEN.TXT"):
        if len(cols) > 1 and cols[1].upper() in ITEM_TYPES:
            out[cols[0]] = ITEM_TYPES[cols[1].upper()]
    return out


def _table_rows(s: Session, name):
    folder = catalog_dir(s)
    path = _find_file(folder, name) if os.path.isdir(folder) else None
    if not path:
        return []
    return [[c.strip() for c in line.split(",")] for line in open(path, errors="replace").read().splitlines() if "," in line]


def _table_keys(s: Session, name):
    return [cols[0] for cols in _table_rows(s, name)]


@command("What a soldier's kit can hold: weapons (Weaps.txt, category 5) and inventory items (Inven.txt) with their kit "
         "category (from the item's type column, the game's own table; null = a type the game doesn't know).")
def kit_options(s: Session):
    cats = _kit_categories(s)
    return {"weapons": [{"id": w, "category": 5} for w in _table_keys(s, "WEAPS.TXT")],
            "items": [{"id": i, "category": cats.get(i)} for i in _table_keys(s, "INVEN.TXT")]}


@command("Replace a soldier's / object's starting kit (not a vehicle's: its kit list is the crew - assign_crew).",
         mutates=True, line="HLOI record line", items="[{item, amount, category}] - category: 5 for weapons, else the "
         "item's own (kit_options)")
def set_kit(s: Session, line, items):
    r = s.record(line)
    if r.kind != "HLOI":
        raise CommandError("only objects (HLOI) have a kit")
    if _is_vehicle(r):
        raise CommandError("a vehicle's kit list is its crew - use the Crew section (assign_crew)")
    cats = _kit_categories(s)
    entries = []
    for it in items or []:
        name = str(it.get("item") or "").strip()
        if not name or any(c in name for c in '" \t'):
            raise CommandError(f"kit item: a Weaps.txt / Inven.txt id, not {name!r}")
        cat = it.get("category")
        cat = int(cat) if cat not in (None, "") else cats.get(name)
        if cat is None:
            raise CommandError(f"{name}: not a Weaps.txt / Inven.txt id - give its kit category (weapons 5, keys 20, "
                               "goggles 17 ...)")
        try:
            amount = int(it.get("amount", 1))
        except (TypeError, ValueError):
            raise CommandError(f"{name}: amount is a whole number") from None
        entries.append((name, cat, max(0, amount)))
    s.env.set_list(r, "kit_count", entries)
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return {"line": line, "kit": [list(e) for e in entries]}


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


@command("Delete a record (and an ENVI's VAMI / ANMI lines). Objects the base mission's DLL uses (DLL critical) are "
         "refused unless force - hide them in a layer instead.", mutates=True, line="record line",
         force="true: delete a DLL-critical object anyway")
def delete(s: Session, line, force=False):
    r = s.record(line)
    _chunk_guard(s, r, "deleted")
    sid = r["script_id"] if r.kind == "HLOI" and "script_id" in r.fields else None
    if sid is not None and sid in logic.base_targets(s) and not force:
        raise CommandError(f"DLL critical: {_base_dll(s, s.level) or 'the base mission'} uses script id {sid} - "
                           "deleting it breaks the level's logic (it can crash). Put it in a layer that starts hidden "
                           "instead, or delete with force")
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
    return {"undone": title}


@command("Redo the last undone change.")
def redo(s: Session):
    if not s.redo_stack:
        raise CommandError("nothing to redo")
    title, snap, *cmd = s.redo_stack.pop()
    s.undo_stack.append((title, s._snapshot(), *cmd))
    s._restore(snap)
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
# that real record (all its AI / kit fields stay valid) and changes position, facing, zone and script id. One list per
# game folder: the app opens every game in one process, so a single cached list handed the last game's to the next.
_LIBRARY = {}


def _library(s: Session):
    key = os.path.normcase(s.data_dir)
    if key in _LIBRARY:
        return _LIBRARY[key]
    entries = {}
    for level in list_levels(s):
        folder = level_folder(s, level)
        path = _find_file(folder, level + ".ENV")
        try:
            env = EnvFile.load(path)
        except (OSError, ValueError):
            continue
        for r in env.records:
            if not r.fields or r.kind not in ("ENVI", "HLOI"):
                continue
            if r.kind == "ENVI":
                if not r.file.is_prop(r) or r.children:
                    continue
                key = ("prop", r["model"].upper())
                label = r["model"]
            else:
                key = ("object", r["model"].upper(), r["unit"].upper())   # one per type, not per role
                label = f"{r['unit']} ({r['model']})"
            if key not in entries:
                entries[key] = {"id": len(entries), "kind": key[0], "keyword": r.kind, "model": r["model"],
                                "label": label, "level": level, "text": r.text, "version": env.version,
                                **_block_of(env, r),
                                "unit": r.get("unit"), "stat": r.get("stat"),
                                "script_id": r.get("script_id") if r.kind == "HLOI" else None,
                                "crew": _crew_texts(env, r) if r.kind == "HLOI" else []}
    _LIBRARY[key] = list(entries.values())
    return _LIBRARY[key]


def _models_in_level(s: Session):
    names = set()
    for f in os.listdir(s.folder):
        stem, ext = os.path.splitext(f)
        if ext.upper() == ".EVO":
            names.add(stem.upper())
        elif stem.startswith("_"):
            names.add(stem.upper())          # _HASH: matched through the hash below
    return names


@command("Prop and object types to place: the open game's shipped levels, (Desert Storm) the converted libraries "
         "of the other games and every installed mod - each entry with its source (source / source_label / "
         "source_kind game / converted / mod, from). in_level = the model is in the open level's archive (shipped "
         "models from other levels get packed into the mod; mod / converted items bring their files on save).",
         search="filter text", kind="prop / object / all", source="optional source id (game, @LIB-DS2, @MOD-<name>)")
def library(s: Session, search="", kind="all", source=""):
    if s.env is None:
        raise CommandError("no level open")
    here = _models_in_level(s)
    q = search.lower()
    game = s.profile.id
    game_label = s.profile.short.upper()
    out = []
    for e in _all_library(s):
        src = e.get("source") or "game"
        if kind != "all" and e["kind"] != kind:
            continue
        if source and src.upper() != source.upper():
            continue
        if q and q not in (e["label"] + " " + (e["stat"] or "") + " " + e.get("from", e["level"])).lower():
            continue
        m = e["model"].upper()
        in_level = m in here or ("_%08X" % archive_hash(m + ".EVO")) in here
        out.append({k: e.get(k) for k in ("id", "kind", "model", "label", "level", "unit", "stat")} | {
            "in_level": in_level, "source": src, "source_kind": e.get("source_kind", "game"),
            "source_label": e.get("source_label") or game_label,
            "from": e.get("from") or e["level"].title(), "thumb": e.get("thumb") or e["level"]})
    return out

# ---- library sources: the open game, (Desert Storm) the converted libraries, every mod ----------------------------
# Every library entry names its source: {"source": id, "source_label", "source_kind": game / converted / mod, "from"}.
# Converted libraries = <Desert Storm>\PSE\Library\<game> (conflict_ds_tools/library.py: DS2_ / GE_ / CV_ / CG_ files
# the plugin serves by name when a scene asks for them). Mods = <game>\Mods\<mod> (switched-off "_" mods left out).
# Props from them are placed on one of the game's own prop records with the model swapped; soldier types / vehicles
# (.unit / .vehicle with "based on" a game type) on that type's object record with unit and model swapped. Files a
# placed item needs that the target mod doesn't have (its definition, a mod's model + textures) are copied into the
# mod when it's saved (_pack_sources) - converted models themselves stay in the library (served by name).
GAME_TAGS = {"ds1": "DS1", "ge": "TGE", "ds2": "DS2", "cv": "CV", "cg": "CGS", "do": "DO"}
_EXTRA = {}


def _game_root(s: Session):
    g = s.game_dir if s.game_dir and os.path.isdir(s.game_dir) else None
    if not g:
        try:
            g = games.find_game_dir(s.profile)
        except Exception:  # noqa: BLE001
            g = None
    return g if g and os.path.isdir(g) else None


def _def_fields(path):
    """id / based on / model of a .unit / .vehicle file (key = value lines)."""
    out = {}
    try:
        with open(path, encoding="latin-1") as f:
            for line in f:
                line = line.split("#", 1)[0]
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip().lower()
                    if k in ("id", "based on", "model", "name") and k not in out:
                        out[k] = v.strip()
    except OSError:
        pass
    return out


def _template_for(game_lib, kind, based_on=None):
    """The game's library entry an item from another source is placed on: its first prop (props), the object entry
    of the type it is based on (soldier types / vehicles)."""
    if kind == "prop":
        return next((e for e in game_lib if e["kind"] == "prop"), None)
    b = (based_on or "").upper()
    return next((e for e in game_lib if e["kind"] == "object" and str(e.get("unit") or "").upper() == b), None)


def _texture_files(model_path, folder):
    """The textures a mod's model names that the same mod has (copied with it)."""
    out = []
    try:
        from conflict_ds_tools import evo
        names = {m.texture for o in evo.load(model_path).objects for m in o.node.meshes if m.texture}
    except Exception:  # noqa: BLE001
        return out
    for t in sorted(names):
        for ext in (".dds", ".tga", ".png"):
            hit = modlayout.find(folder, t + ext)
            if hit:
                out.append(hit)
                break
    return out


def _extra_entries(s: Session, game_lib):
    """Library entries of the converted libraries and the mods (cached per game folder + the mods' file times)."""
    root = _game_root(s)
    if not root:
        return []
    mods_dir = os.path.join(root, "Mods")
    lib_dir = os.path.join(root, "PSE", "Library") if s.profile.id == "ds1" else None
    stamp = []
    for d in (mods_dir, lib_dir):
        if d and os.path.isdir(d):
            stamp += [(n, os.path.getmtime(os.path.join(d, n))) for n in sorted(os.listdir(d))]
    key = (os.path.normcase(root), s.profile.id, tuple(stamp))
    if _EXTRA.get("key") == key:
        return _EXTRA["entries"]
    out = []

    def add(kind, model, label, source, source_label, source_kind, where, files, template, unit=None):
        if template is None:
            return
        out.append({"kind": kind, "keyword": template["keyword"], "model": model, "label": label,
                    "level": template["level"], "text": template["text"], "version": template.get("version"),
                    "block": template.get("block"), "tag": template.get("tag"), "crew": template.get("crew") or [],
                    "unit": unit or template.get("unit"), "stat": template.get("stat"),
                    "script_id": None, "source": source, "source_label": source_label,
                    "source_kind": source_kind, "from": where, "files": files,
                    "model_override": model, "unit_override": unit, "thumb": source})

    prop_tpl = _template_for(game_lib, "prop")
    # converted libraries (Desert Storm only: the porter converts into Desert Storm)
    if lib_dir and os.path.isdir(lib_dir):
        for gid in sorted(os.listdir(lib_dir)):
            folder = os.path.join(lib_dir, gid)
            index = os.path.join(folder, "library.txt")
            if not os.path.isfile(index):
                continue
            game = games.GAMES.get(gid)
            src_id = "@LIB-" + gid.upper()
            src_label = "%s \u2192 %s" % (GAME_TAGS.get(gid, gid.upper()), GAME_TAGS.get(s.profile.id, ""))
            short = game.short if game else gid.upper()
            with open(index, encoding="latin-1") as f:
                rows = [ln.rstrip("\r\n").split("\t") for ln in f if ln.strip() and not ln.startswith("#")]
            for r in rows:
                if len(r) < 3:
                    continue
                name, kind = r[0], r[1]
                stem, ext = os.path.splitext(name)
                where = "%s: %s (converted)" % (short, r[2])
                if ext.upper() == ".EVO" and kind == "model":
                    add("prop", stem, stem, src_id, src_label, "converted", where, [], prop_tpl)
                elif ext.upper() in (".UNIT", ".VEHICLE"):
                    d = _def_fields(os.path.join(folder, name))
                    if d.get("id") and d.get("model"):
                        label = "%s (%s)" % (d["id"], d.get("name") or d["model"])
                        add("object", d["model"], label, src_id, src_label, "converted", where,
                            [os.path.join(folder, name)], _template_for(game_lib, "object", d.get("based on")),
                            unit=d["id"])
    # mods
    if os.path.isdir(mods_dir):
        for mod in sorted(os.listdir(mods_dir)):
            folder = os.path.join(mods_dir, mod)
            if mod.startswith(("_", ".")) or not os.path.isdir(folder):
                continue
            src_id = "@MOD-" + mod
            for rel in modlayout.files(folder):
                path = os.path.join(folder, rel)
                stem, ext = os.path.splitext(os.path.basename(rel))
                where = "%s\\%s" % (mod, rel)
                if ext.upper() == ".EVO":
                    add("prop", stem, stem, src_id, "MOD: " + mod, "mod", where,
                        [path] + _texture_files(path, folder), prop_tpl)
                elif ext.upper() in (".UNIT", ".VEHICLE"):
                    d = _def_fields(path)
                    if d.get("id") and d.get("model"):
                        model_file = modlayout.find(folder, d["model"] + ".evo")
                        files = [path] + ([model_file] + _texture_files(model_file, folder) if model_file else [])
                        add("object", d["model"], "%s (%s)" % (d["id"], d.get("name") or d["model"]), src_id,
                            "MOD: " + mod, "mod", where, files, _template_for(game_lib, "object", d.get("based on")),
                            unit=d["id"])
    base = len(game_lib)
    for k, e in enumerate(out):
        e["id"] = base + k
    _EXTRA.update(key=key, entries=out)
    return out


def _all_library(s: Session):
    """The game's own library (shipped levels) + the other sources; ids are list positions."""
    game_lib = _library(s)
    return game_lib + _extra_entries(s, game_lib)


def source_folder(s: Session, source, model):
    """The folder a library source's model is in (model thumbnails: server.py /model/@LIB-DS2/<MODEL>.glb)."""
    root = _game_root(s)
    if not root:
        return None
    if source.upper().startswith("@LIB-"):
        return os.path.join(root, "PSE", "Library", source[5:].lower())
    if source.upper().startswith("@MOD-"):
        folder = os.path.join(root, "Mods", source[5:])
        hit = modlayout.find(folder, model + ".evo") if os.path.isdir(folder) else None
        return os.path.dirname(hit) if hit else folder
    return None


def _pack_sources(s: Session, folder):
    """Copy what items placed from mods / converted libraries need into the mod being saved (names already there are
    kept: the mod's own copy wins). Returns the file names copied."""
    import shutil
    used = {str(r.get("model")).upper() for r in s.env.records if r.kind in ("ENVI", "HLOI") and r.fields} | \
           {str(r.get("unit")).upper() for r in s.env.records if r.kind == "HLOI" and r.fields}
    copied = []
    for key, paths in sorted(getattr(s, "foreign_files", {}).items()):
        if key not in used:
            continue                            # placed, then deleted again
        for src in paths:
            name = os.path.basename(src)
            if not os.path.isfile(src) or modlayout.find(folder, name):
                continue
            if os.path.normcase(os.path.dirname(os.path.abspath(src))).startswith(os.path.normcase(os.path.abspath(folder))):
                continue                        # already this mod's file
            shutil.copy2(src, _mod_out(s, folder, name))
            copied.append(name)
    return copied



def _zones(s: Session):
    """Names a record can hang from: "Root" and every ENVI of the scene (zones such as MIS1_ZONE1-6821, interiors,
    and props - a cigarette pack hangs from its table)."""
    # GLOBALZONE (Vietnam on): a zone built into the engine, always present - 1286 links in Vietnam's missions
    engine = {"GLOBALZONE"} if s.env.version >= 107 else set()
    return {"Root"} | engine | {str(r.get("name")) for r in s.env.records if r.kind in ("ENVI", "ANMI", "VAMI")}


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
        if keyword == "ENVI" and not r.file.is_prop(r):
            continue
        if str(z) not in zones:
            continue
        d = sum((a - b) ** 2 for a, b in zip(p, pos))
        if best is None or d < best:
            best, zone = d, z
    if zone is None:                            # nothing placed yet (a new level): the level's ground zone
        zone = next((str(r["name"]) for r in s.env.find("ENVI")
                     if r.get("type") in r.file.zone_types() and str(r.get("parent")) == "Root" and r.index != skip), None)
    return zone


@command("Place a library template at a position (new script id for objects, zone of the nearest record).",
         mutates=True, template="library id", pos="[x, y, z]", yaw="facing in degrees",
         scale="optional size factor for props (e.g. 0.9-1.1 for variety)")
def place(s: Session, template, pos, yaw=0.0, scale=None):
    lib = _all_library(s)
    if not 0 <= int(template) < len(lib):
        raise CommandError("unknown template")
    e = lib[int(template)]
    pos = _vec(pos)
    # a Denied Ops scene takes Denied Ops templates (whole blocks); other games' lines don't fit its layout
    same = e.get("version", s.env.version) >= 300 if s.env.version >= 300 else True
    if not same:
        raise CommandError("%s comes from another game's scene - it can't be placed in a Denied Ops scene"
                           % e["label"])
    new = _insert_new(s, e["keyword"], e["text"], e.get("block"), e.get("tag"))
    if e.get("model_override"):                 # an item from a mod / converted library on a game template record
        new["model"] = e["model_override"]
        if e.get("unit_override") and "unit" in new.fields:
            new["unit"] = e["unit_override"]
        new = s.env.record_at(new.index + 1)
        if e.get("files"):
            ff = s.__dict__.setdefault("foreign_files", {})
            ff[(e.get("unit_override") or e["model_override"]).upper()] = list(e["files"])
    new["pos"] = pos
    rot = new["rot"]
    new["rot"] = (rot[0], float(yaw) % 360.0, rot[2])
    if e["keyword"] != "HLOI" and "name" in new.fields:
        # world piece names are unique per scene - before the zone: a parent link line is written under this name
        new["name"] = _unique(s, "ENVI", "name", f"{e['model']}{new.index + 1}")
    line = new.index
    zone = _nearest_zone(s, e["keyword"], pos, skip=new.index)
    if zone and (e["keyword"] != "HLOI" or "zone" in new.fields):
        new["zone" if e["keyword"] == "HLOI" else "parent"] = zone
        new = s.env.record_at(line + 1)         # a parent link line re-reads the scene (the block sits above it)
    result = {"line": new.index + 1}
    if e["keyword"] == "HLOI":
        result["script_id"] = new["script_id"] = max(o["script_id"] for o in s.env.find("HLOI")) + 1
        if "in_play" in new.fields:
            new["in_play"] = 1                 # templates from levels that spawn them later start hidden
        new = _attach_crew(s, new, e.get("crew") or [])
        result["line"] = new.index + 1
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


# Zone chunks: a level's ground / building models (ENVI type 5) are hundreds of objects, and the scene gives every
# object its own record - the zone's root record (parent Root, model = the file) is object 0, then one record per
# object (model = the object's own name, e.g. MIS1_ZONE4013) hanging from it. The game creates each object from its
# record (FUN_004f8d00 -> FUN_004dd740 by name, FUN_004ddb10, placed by FUN_004fb4d0), so deleting a record removes that
# chunk and its pos / rot move it. Their vertices are in world space (records at 0), so they move by their centre like
# kit pieces. Objects without a record are never created.
def _chunk_map(s: Session):
    """{OBJECT NAME: {"zone", "index", "centre", "root"}} for the open level's zone models (files read once per level)."""
    cached = getattr(s, "_chunk_models", None)
    known = cached[1] if cached and cached[0] == s.level else {}
    out = {}
    from conflict_ds_tools import evo
    for r in s.env.find("ENVI"):
        if not r.fields or r.get("type") not in r.file.zone_types() or str(r.get("parent")).upper() != "ROOT":
            continue
        model = str(r["model"]).upper()
        if model in getattr(s, "terrains", {}):
            continue
        if model not in known:
            try:
                nodes = evo.read(_kit_data(s, s.level, model))
            except (CommandError, evo.EvoError, OSError, ValueError):
                known[model] = {}
                continue
            objs = {}
            for i, n in enumerate(nodes):
                (x0, y0, z0), (x1, y1, z1) = n.bbox
                objs[str(n.model).upper()] = {"zone": model, "index": i, "root": i == 0,
                                              "centre": ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)}
            known[model] = objs
        out.update(known[model])
    s._chunk_models = (s.level, known)
    return out


def _chunk_guard(s: Session, r, action):
    """A zone's root record carries every other chunk of the zone (they hang from it): it stays where it is."""
    c = _chunk(s, r)
    if c and c["root"]:
        raise CommandError(f"{r['name']} is the root of zone {c['zone']} - every chunk of the zone hangs from it, so it "
                           f"can't be {action}. Select one of its chunks instead")


def _chunk(s: Session, r):
    """The zone chunk a record places (see _chunk_map), or None."""
    if r.kind != "ENVI" or not r.fields or r.get("type") not in r.file.zone_types():
        return None
    return _chunk_map(s).get(str(r.get("model")).upper())


def _kit_data(s: Session, level, model):
    from conflict_ds_tools import evo
    folder = level_folder(s, level)
    path = evo.AssetFinder(_find_file(folder, level.upper() + ".ENV") or os.path.join(folder, "x")).find(model, (".evo",))
    if not path:
        raise CommandError(f"no {model}.EVO in {level}")
    return open(path, "rb").read()


@command("World-piece models of a level that kit pieces can come from (its ground / zone models).", level="level name")
def kit_models(s: Session, level=None):
    level = (level or s.level).upper()
    env = EnvFile.load(_find_file(level_folder(s, level), level + ".ENV"))
    return sorted({str(r["model"]) for r in env.find("ENVI") if r.get("type") in r.file.zone_types() and str(r.get("parent")) == "Root"})


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
        with open(_mod_out(s, folder, model + ".EVO"), "wb") as f:
            f.write(road.to_evo(template, model))
        with open(_mod_out(s, folder, model + ".road.json"), "w") as f:
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
        with open(_mod_out(s, folder, model + ".EVO"), "wb") as f:
            if t.zone:
                f.write(t.to_evo(open(zone_template, "rb").read(), model, warnings, offset=t.origin))
            else:
                f.write(t.to_evo(open(template, "rb").read(), model, warnings))
        with open(_mod_out(s, folder, model + ".terrain.json"), "w") as f:
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
    _require_script_host(s, "A new level")
    base = s.env
    start = next((r for r in base.find("HLOI") if r.fields and r["unit"] == "Bradley" and not r["flags"] & 0x20000), None)
    if start is None:
        raise CommandError(f"{environment} has no squad start (Bradley) - pick a campaign mission")
    centre = start["pos"]
    tpl = next(r for r in base.find("ENVI") if r.get("type") in r.file.zone_types() and str(r.get("parent")) == "Root")
    lines = blank.blank_lines(base, extra_envi=[tpl.text])

    s.env.lines = lines
    s.env._parse()
    g = next(r for r in s.env.find("ENVI"))
    # the ground model is named after the level: models are found by file name across all mods, so two new levels
    # both called GROUND took each other's ground (a mission loaded another mod's GROUND.EVO)
    ground = "GROUND_" + re.sub(r"[^A-Z0-9_]", "_", str(name).upper())
    zone = ground + "-1"                       # record name = model + "-1", like every shipped zone (MIS1_ZONE1-6821 ...)
    g["name"], g["model"], g["pos"], g["rot"] = zone, ground, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    n = int(vertices)
    s.terrains = {ground: Terrain(n=n, cell=float(size) * 100 / (n - 1), texture=_ground_textures(s)[0],
                                    steep_texture=_steep_default(s), zone=True, origin=(centre[0], centre[1] - 10, centre[2]))}
    k = 0
    for h in s.env.find("HLOI"):
        h["zone"] = zone
        h["in_play"] = 1
        h["pos"] = (centre[0] + 200 * k, centre[1], centre[2])     # the squad side by side at the start
        k += 1
    from conflict_ds_tools.routes import RouteFile as _RF
    s.routes = _RF([])
    s.layers, s.layer_start, s.blank = {}, {}, True
    s.undo_stack.clear(); s.redo_stack.clear()
    saved = save_mission(s, mod, name)
    s.revision = getattr(s, "revision", 0) + 1
    return {"mission": [mod, name.upper()], "level": saved["level"], "ground": ground, "start": list(centre)}


# ---------------------------------------------------------------------------------------------------- save --
def _same_as_shipped(s: Session):
    """The open scene (and routes) are exactly the level's shipped files."""
    path = _find_file(s.folder, s.level + ".ENV") if s.folder and os.path.isdir(s.folder) else None
    if not path or EnvFile.load(path).lines != s.env.lines:
        return False
    rmd = _find_file(s.folder, s.level + ".RMD") or _find_file(s.folder, s.level + ".RDA")
    if s.routes is None or rmd is None:
        return s.routes is None and rmd is None
    with open(rmd, "rb") as f:
        return f.read() == (s.routes.to_rda_bytes() if s.routes.is_rda else s.routes.to_bytes())


def _save_mod_references(s: Session, mod, game_dir, allow_unchanged):
    """save_mod for plugins that build game data from the player's install (gamerefs.py; DesertStorm2Fix
    mods_levels.cpp): the scene and routes as changes against the shipped level - no game file in the mod. Older full
    copies of <LEVEL>.ENV / .RMD in the mod are removed (a real file would win over the patch); the plugin rebuilds the
    octree and the AI navigation (walk map + generated route markers, the level's hand-placed markers kept) for the
    changed scene."""
    from conflict_ds_tools import gamerefs
    if not allow_unchanged and _same_as_shipped(s):
        raise CommandError("no changes: the scene is exactly the shipped level")
    game = game_dir or s.game_dir
    if not game or not os.path.isdir(game):
        raise CommandError("game folder unknown - start the editor with --game")
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("bad mod name")
    base_env = _find_file(s.folder, s.level + ".ENV")
    if not base_env:
        raise CommandError(f"the shipped {s.level}.ENV isn't in the extraction - can't write the scene as changes")
    folder = os.path.join(game, "Mods", mod)
    os.makedirs(folder, exist_ok=True)
    records = os.path.join("records", s.level.lower() + ".placed.lua")
    names = [s.level + ".ENV", s.level + ".RMD", s.level + ".envpatch", s.level + ".rmdpatch", records]
    backup = workflow.backup_files(s, folder, names, mod, s.level)
    written, removed = [], []
    base_lines = EnvFile.load(base_env).lines
    # Desert Storm: changed / copied objects, waypoints and regions as record edits (mods merge them field by field -
    # docs/sdk/data-files.md "Several mods, one level"); the .envpatch keeps only the rest
    edits, lines = [], s.env.lines
    if s.profile.id == "ds1":
        from conflict_ds_tools import placed
        with open(base_env, "rb") as f:
            edits, lines = placed.split_edits(f.read(), s.env, s.level)
    rec_path = _mod_out(s, folder, records, create=False)   # records\ stays where the plugin reads it
    if edits:
        from conflict_ds_tools import pse_data
        os.makedirs(os.path.dirname(rec_path), exist_ok=True)
        doc = pse_data.Document(pse_data.from_python(edits), [
            f"{s.level} - objects, waypoints and regions changed with the Conflict Editor (written by save_mod: the",
            "next save replaces this file; other mods' edits of the same level merge with these field by field)"])
        with open(rec_path, "wb") as f:
            f.write(pse_data.dumps_bytes(doc))
        written.append(records)
    elif os.path.exists(rec_path):
        os.remove(rec_path)
        removed.append(records)
    if lines != base_lines:
        with open(_mod_out(s, folder, s.level + ".envpatch"), "w", encoding="latin-1", newline="\n") as f:
            f.write(gamerefs.make_env_patch(base_lines, lines, s.level + ".ENV"))
        written.append(s.level + ".envpatch")
    elif _mod_remove(folder, s.level + ".envpatch"):
        removed.append(s.level + ".envpatch")
    base_rmd = _find_file(s.folder, s.level + ".RMD")
    if s.routes and base_rmd:
        base_routes = RouteFile.load(base_rmd)
        if base_routes.to_bytes() != s.routes.to_bytes():
            with open(_mod_out(s, folder, s.level + ".rmdpatch"), "w", newline="\n") as f:
                f.write(gamerefs.make_rmd_patch(base_routes, s.routes, s.level + ".RMD"))
            written.append(s.level + ".rmdpatch")
        else:
            _mod_remove(folder, s.level + ".rmdpatch")   # routes unchanged: the plugin rebuilds them for the changed scene
    elif s.routes:
        s.routes.save(_mod_out(s, folder, s.level + ".RMD"))
        written.append(s.level + ".RMD")
    for old in (s.level + ".ENV", s.level + ".RMD"):
        if old not in written and _mod_remove(folder, old):
            removed.append(old)
    if problems := s.env.problems:
        raise CommandError("saved, but the scene has problems: " + "; ".join(problems[:3]))
    s.dirty = False
    written += _pack_sources(s, folder)
    return {"folder": folder, "files": written, "removed": removed, "backup": backup, "record_edits": len(edits)}


def _mod_level_scene(s: Session, folder, level):
    """A shipped level's scene as one mod's files make it in the game: the mod's own <LEVEL>.ENV, else the shipped
    scene + the mod's <LEVEL>.envpatch, then the placed-reference edits of its records\\*.lua (files by name, as
    DesertStormFix applies them). None when the mod changes nothing of that level."""
    from conflict_ds_tools import gamerefs, placed, pse_data
    own = modlayout.find(folder, level + ".ENV")
    if own:
        return EnvFile.load(own)
    base = _find_file(s.folder, level + ".ENV")
    if not base:
        return None
    env = EnvFile.load(base)
    changed = False
    patch = modlayout.find(folder, level + ".envpatch")
    if patch:
        env.lines = gamerefs.apply_env_patch(open(patch, encoding="latin-1").read(), env.lines)
        env._parse()
        changed = True
    rec = os.path.join(folder, "records")
    if os.path.isdir(rec):
        mod = os.path.basename(folder)
        edits = []
        for f in sorted(os.listdir(rec), key=str.lower):
            if f.lower().endswith(".lua"):
                doc = pse_data.load(os.path.join(rec, f))
                edits.append((mod, f, [e for e in doc.root.items if isinstance(e, pse_data.Table)]))
        if level.lower() in placed.edit_levels(edits):
            placed.apply_edits(env, level, edits)
            changed = True
    return env if changed else None


@command("Open a shipped level as a mod changed it (save_mod's files: the mod's <LEVEL>.ENV, or the level + its "
         ".envpatch + its records' placed edits) - to go on editing it. Save with save_mod to the same mod.",
         mod="mod folder", level="level name, e.g. MISSION1")
def open_mod_level(s: Session, mod, level):
    from conflict_ds_tools import gamerefs
    folder = _mod_folder(s, mod, create=False)
    open_level(s, level)
    env = _mod_level_scene(s, folder, s.level)
    if env is None:
        raise CommandError(f"Mods\\{mod} doesn't change {s.level}")
    s.env = env
    rpatch, rmd = modlayout.find(folder, s.level + ".rmdpatch"), modlayout.find(folder, s.level + ".RMD")
    if rpatch and s.routes:
        s.routes = gamerefs.apply_rmd_patch(open(rpatch).read(), s.routes)
    elif rmd:
        s.routes = RouteFile.load(rmd)
    s.dirty = False
    return {"level": s.level, "mod": mod, "problems": s.env.problems[:5]}


@command("Write the edited scene (and routes) as a mod: <game>\\Mods\\<mod>\\<LEVEL>.ENV / .RMD - or, for a game whose plugin "
         "builds them from the player's install (Desert Storm, Desert Storm II), <LEVEL>.envpatch / .rmdpatch with only "
         "the changes (Desert Storm: changed / copied objects, waypoints and regions go to records\\<level>.placed.lua "
         "as record edits instead, which other mods' edits of the level merge with). "
         "Refuses a scene identical to the shipped level (it would only copy game files into the mod) unless "
         "allow_unchanged.",
         mod="mod folder name", game_dir="game folder (default: the session's)",
         allow_unchanged="write it even when nothing differs from the shipped level",
         references="patches instead of full copies (default: what the game's plugin supports)")
def save_mod(s: Session, mod, game_dir=None, allow_unchanged=False, references=None):
    if s.env is None:
        raise CommandError("no level open")
    if references is None:
        references = getattr(s.profile, "game_refs", False)
    if references and not s.env.split_files() and not (s.routes and s.routes.is_rda):
        return _save_mod_references(s, mod, game_dir, allow_unchanged)
    if s.profile.id == "do":
        raise CommandError("Denied Ops reads levels only from its archives (no Mods folder): saving needs an archive "
                           "rebuild or a plugin, which the editor doesn't have yet - the level can be viewed and edited")
    if not allow_unchanged and _same_as_shipped(s):
        raise CommandError("no changes: the scene is exactly the shipped level - saving would only copy the game's "
                           f"{s.level}.ENV / .RMD into the mod (and Export refuses copies of game files)")
    game = game_dir or s.game_dir
    if not game or not os.path.isdir(game):
        raise CommandError("game folder unknown - start the editor with --game")
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("bad mod name")
    folder = os.path.join(game, "Mods", mod)
    os.makedirs(folder, exist_ok=True)
    parts = s.env.split_files()
    if parts:
        # Global Storm: the .ENV is only an index - write the part files (.ART / .DES / .SND) that differ from the
        # shipped ones; the game finds them by name like any other file
        changed = {}
        for name, data in parts.items():
            shipped = _find_file(s.folder, name)
            if shipped is None or open(shipped, "rb").read() != data:
                changed[name.upper()] = data
        backup = workflow.backup_files(s, folder, list(changed) + [s.level + ".RMD"], mod, s.level)
        written = []
        for name, data in changed.items():
            with open(_mod_out(s, folder, name), "wb") as f:
                f.write(data)
            written.append(name)
    else:
        backup = workflow.backup_files(s, folder, [s.level + ".ENV", s.level + ".RMD"], mod, s.level)
        written = []
        s.env.save(_mod_out(s, folder, s.level + ".ENV"))
        written.append(s.level + ".ENV")
    if s.routes and s.routes.is_rda:
        # Global Storm: the routes live in <LEVEL>.RDA (with the walk-map quadtree and door data) - written only when
        # the network changed
        shipped = _find_file(s.folder, s.level + ".RDA")
        data = s.routes.to_rda_bytes()
        if shipped is None or open(shipped, "rb").read() != data:
            with open(_mod_out(s, folder, s.level + ".RDA"), "wb") as f:
                f.write(data)
            written.append(s.level + ".RDA")
    elif s.routes:
        s.routes.save(_mod_out(s, folder, s.level + ".RMD"))
        written.append(s.level + ".RMD")
    if problems := s.env.problems:
        raise CommandError("saved, but the scene has problems: " + "; ".join(problems[:3]))
    s.dirty = False
    written += _pack_sources(s, folder)
    return {"folder": folder, "files": written, "backup": backup}


# ------------------------------------------------------------------------------------------ save as mission --
# A new mission = a mod with the edited scene under a new name and a Lua script that sets the map up the way the base
# mission's DLL does (editor/games/<game>/level_setups.json, scripts/gen_level_setups.py) but loads that scene. The game names the
# octree / AI walk map / routes after the scene, so the base level's .OCT / .QTD are copied under the new name (the game
# rebuilds the .OCT over that copy when it loads a .lua level - edited props need it - and the copy makes the name known
# to its mod index); the
# script's "--@archive <base>" line gives it the base level's archive and sound cache (DesertStormFix scripthost).
_SETUPS = {}                 # game id -> level_setups.json
_SINGLE_ARG = {0x0C, 0x44, 0x4C, 0x58}   # sound bank, scene, map image, strings
_NAMED = {0x0C: "game.load_sound_bank", 0x44: "game.load_scene", 0x4C: "game.load_map_image",
          0x58: "game.load_strings", 0x4E0: "game.set_briefing_text"}   # +0x4E0: briefing title / date / place / pages
_ENEMY = ("IRAQ", "RU_", "RUSSIAN")
_NOT_ENEMY = ("GOATHERDER", "CIVILIAN", "IRAQCIV")


def game_data(name):
    """editor/games/<game id>/<name> - per-game data (API slot names, base missions) - or None when the game has none
    yet (Desert Storm II / Global Storm: their script APIs aren't mapped)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", games.current().id, name)
    return path if os.path.isfile(path) else None


def _setups():
    gid = games.current().id
    if gid not in _SETUPS:
        path = game_data("level_setups.json")
        _SETUPS[gid] = {}
        if path:
            with open(path) as f:
                _SETUPS[gid] = json.load(f)
    return _SETUPS[gid]


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
    """The mission DLL a level's script runs underneath (--@base): a custom DLL mission opened with open_dll_mission,
    else the shipped one of the level (Mission1.dll ...) in the game folder, or None (MP levels, no game folder)."""
    own = getattr(s, "mission_dll", None)
    if own and base == s.level:
        return os.path.basename(own["path"])
    if not s.game_dir:
        return None
    path = _find_file(s.game_dir, base + ".DLL")
    return os.path.basename(path) if path else None


def _is_mission_dll(path):
    """A mission DLL exports LevelSetup (the game calls it with the script API table)."""
    try:
        import pefile
        with open(path, "rb") as f:            # (pefile keeps a file it opened mapped)
            pe = pefile.PE(data=f.read(), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        return any(e.name == b"LevelSetup" for e in getattr(pe, "DIRECTORY_ENTRY_EXPORT", None).symbols)
    except Exception:  # noqa: BLE001 - not a PE / no exports
        return False


def mission_dll(s: Session, name, mod=None):
    """Where the game finds <name>.dll: {path, mod (None = game folder), shipped (the game folder has one of that
    name)}. Mods first (the given one, then the enabled ones - a later one wins, as in the game), then the game folder."""
    game = s.game_dir
    if not game or not name:
        return None
    file = name if name.lower().endswith(".dll") else name + ".dll"
    shipped = games.game_file(game, file)   # (or PSE/Originals)
    shipped = shipped if os.path.isfile(shipped) else None
    mods = [m for m in reversed(_enabled_mods(game))]
    if mod:
        mods = [mod] + [m for m in mods if m != mod]
    for m in mods:
        path = modlayout.find(os.path.join(game, "Mods", m), file)
        if path:
            return {"path": path, "mod": m, "shipped": bool(shipped)}
    return {"path": shipped, "mod": None, "shipped": True} if shipped else None


def script_base(path):
    """The DLL named on a mission script's --@base line, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("--@base"):
                    return line.split(None, 1)[1].strip() if len(line.split()) > 1 else None
                if line.strip() and not line.startswith("--"):
                    break
    except OSError:
        pass
    return None


def _dll_scene(analysis):
    """The scene a mission DLL loads (script API +0x44 with a file name) - in LevelSetup's init or what it calls."""
    funcs, seen, todo = analysis["functions"], set(), [analysis["init_fn"]]
    while todo:
        va = todo.pop(0)
        if va in seen:
            continue
        seen.add(va)
        stack = list(funcs.get(va, []))
        while stack:
            st = stack.pop(0)
            if st.get("op") == "api" and st.get("slot") == 0x44 and st["args"] and st["args"][0].get("k") == "str":
                return st["args"][0]["s"]
            for k in ("then", "else", "body"):
                stack[0:0] = st.get(k) or []
            for c in st.get("cases", []):
                stack[0:0] = c["body"]
        todo += analysis["calls"].get(va, [])
    return None


@command("Custom mission DLLs in the Mods folders: DLLs exporting LevelSetup. shipped = named like one of the game's "
         "(a changed copy - it is packaged as a byte patch against the player's own DLL), else a mission of its own.",
         game_dir="game folder (default: the session's)")
def list_dll_missions(s: Session, game_dir=None):
    game = game_dir or s.game_dir
    root = os.path.join(game or "", "Mods")
    out = []
    if os.path.isdir(root):
        for m in sorted(os.listdir(root), key=str.lower):
            folder = os.path.join(root, m)
            if not os.path.isdir(folder):
                continue
            for rel in modlayout.files(folder):
                p, f = os.path.join(folder, rel), os.path.basename(rel)
                if f.lower().endswith(".dll") and _is_mission_dll(p):
                    out.append({"mod": m, "dll": f, "shipped": os.path.isfile(games.game_file(game, f)),
                                "enabled": not m.startswith(("_", "."))})
    return out


@command("Open a mission DLL from a mod (a mission someone built as a DLL): its scene - from the mod when it has the "
         "file, else the shipped level's - and routes become editable, its logic shows in the mission-logic view. Save "
         "as mission keeps the DLL running underneath (--@base); Play runs it.", mod="mod folder",
         dll="the DLL's file name", base="the shipped level whose models / archive the scene uses (needed only when "
         "the scene isn't a shipped level's)")
def open_dll_mission(s: Session, mod, dll, base=None):
    _require_script_host(s, "Mission DLLs")
    found = mission_dll(s, dll, mod)
    if not found or found["mod"] != mod:
        raise CommandError(f"no {dll} in Mods\\{mod}")
    from conflict_ds_tools import missionflow
    try:
        flow = missionflow.analyse(found["path"])
    except Exception as e:  # noqa: BLE001
        raise CommandError(f"{dll} isn't a mission DLL the editor can read ({type(e).__name__}: {e})")
    scene = _dll_scene(flow)
    if not scene:
        raise CommandError(f"{dll} loads no scene in its set-up (script API +0x44) - can't tell which level it plays on")
    stem = os.path.splitext(os.path.basename(scene))[0].upper()
    folder = os.path.join(s.game_dir, "Mods", mod)
    own = modlayout.find(folder, stem + ".ENV")
    levels = _level_map(s)
    if base:
        base = base.upper()
    elif stem in levels:
        base = stem
    else:
        raise CommandError(f"{dll} plays on its own scene {scene} - pick the shipped level whose models it uses (base)")
    open_level(s, base)
    if env := _mod_level_scene(s, folder, stem):   # the mod's own scene, or the level + its patch / record edits
        s.env = env
    if own:
        rmd = modlayout.find(folder, stem + ".RMD")
        if rmd:
            s.routes = RouteFile.load(rmd)
    name = os.path.splitext(found["path"])[0]
    s.mission_dll = {"path": found["path"], "mod": mod, "shipped": found["shipped"], "scene": scene}
    s.mission = (mod, os.path.basename(name).upper())
    return {"mission": list(s.mission), "base": base, "dll": os.path.basename(found["path"]), "scene": scene,
            "scene_from_mod": bool(own), "changed_copy": found["shipped"], **s.env.summary()}


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
-- {name}: a new level made with the Conflict Editor ({base}'s sky, sounds and textures).
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
{logic.HOOK_UPDATE}
end

function on_message(id, a, b, c)
{logic.HOOK_MSG.format(args="id, a, b, c")}
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
-- {name}: made with the Conflict Editor from {base}.
-- {dll} runs the original mission on this scene (objectives, cutscenes, triggers, mission end); this script
-- runs after it each frame and gets every message after it. Add to the mission here:
-- game.EVENT has the game's events, game.* the helpers (DesertStormFix scripthost.cpp).

{_layers_block(s)}

{logic.blocks_text(s)}

function init()
{logic.HOOK_INIT}
end

function update()
{logic.HOOK_UPDATE}
end

function on_message(id, a, b, c)
{logic.HOOK_MSG.format(args="id, a, b, c")}
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
-- {name}: made with the Conflict Editor from {base}.
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
{logic.HOOK_UPDATE}
    if not started and game.time_ms() > 0 then
        started = true
{start}
    end
end

function on_message(id, a, b, c)
{logic.HOOK_MSG.format(args="id, a, b, c")}
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
        path = _find_file(catalog_dir(s), table)
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
    base = games.game_file(game, s.level + ".sch") if game else None   # (or PSE/Originals)
    base = base if base and os.path.isfile(base) else None
    if not base:
        return []
    have = _level_banks(base)
    wanted = []
    for r in s.env.find("HLOI"):
        for w in _vehicle_weapons(s, r.get("unit")):
            bank = w[63] if len(w) > 63 else ""
            if bank and bank != "-1" and sound.bank_key(bank) not in have and bank not in wanted:
                wanted.append(bank)
    import fnmatch
    for rel in modlayout.files(folder):        # bank packs of older saves (anywhere in the mod): referenced now
        if fnmatch.fnmatch(os.path.basename(rel).lower(), (name + "_*.sch").lower()):
            os.remove(os.path.join(folder, rel))
    return wanted                              # "bank X" lines: DesertStormFix copies them from the player's caches


def _remove_game_copies(s: Session, folder, up, refs):
    """Files older saves copied out of the game: the full scene / routes / nav files of this mission (now patches and
    references) and copies identical to a referenced game file. Anything that differs from the game's is the modder's
    and stays."""
    removed = []
    for ext in (".ENV", ".RMD", ".OCT", ".QTD"):
        if (ext != ".RMD" or _find_file(s.folder, s.level + ".RMD")) and _mod_remove(folder, up + ext):
            removed.append(up + ext)
    for name, src in refs.items():
        for path in modlayout.find_all(folder, name):
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
        if r.kind not in ("HLOI", "ENVI") or not r.fields or (r.kind == "ENVI" and not r.file.is_prop(r)):
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


# Lua missions (save_mission / new_level / play) run on the plugin's script host (DesertStormFix scripthost.cpp). The
# other games' plugins have none yet, and their script API tables are laid out differently (Vietnam: scene +0x1A0,
# strings +0x980, sound bank +0xD0, map image +0x974, bounds +0x97C, teams +0xB10 / +0xB14 - see
# scripts/gen_level_setups.py), so they are refused there; Save as mod (the level's own scene / routes replaced) works.
def _require_script_host(s: Session, what):
    if not s.profile.script_host:
        raise CommandError(f"{what} needs {s.profile.plugin}'s Lua mission host, which isn't there yet - use Save as mod")


@command("Save the edited level as a new mission: <game>\\Mods\\<mod>\\ gets <NAME>.ENV/.RMD/.OCT/.QTD, models placed "
         "from other levels, and <name>.lua (generated once - an existing script is kept). campaign = add it to that "
         "mod's campaign.txt (created with this name if missing).", mod="mod folder", name="mission name (letters, "
         "digits, _)", campaign="optional campaign name", game_dir="game folder (default: the session's)")
def save_mission(s: Session, mod, name, campaign=None, game_dir=None):
    import re
    if s.env is None:
        raise CommandError("no level open")
    _require_script_host(s, "Saving as a mission")
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
    backup = workflow.backup_files(s, folder, workflow.mission_files(folder, up), mod, up)   # before overwriting
    written = []
    # No game files in the mod (gamerefs.py): the scene and routes as changes against the base level's, the base's
    # nav files and the models / banks placed from other levels as references to the player's own install.
    from conflict_ds_tools import gamerefs
    refs = {}
    base_env = EnvFile.load(_find_file(s.folder, s.level + ".ENV"))
    with open(_mod_out(s, folder, up + ".envpatch"), "w", encoding="latin-1", newline="\n") as f:
        f.write(gamerefs.make_env_patch(base_env.lines, s.env.lines, s.level + ".ENV"))
    written.append(up + ".envpatch")
    own = modlayout.find(folder, up + ".ENV")
    if own:                                    # a real scene file in the mod wins over the patch -
        s.env.save(own)                        # keep it the edited one (a DLL mission's own scene)
        written.append(os.path.basename(own))
    base_rmd = _find_file(s.folder, s.level + ".RMD")
    if s.routes and base_rmd:
        with open(_mod_out(s, folder, up + ".rmdpatch"), "w", newline="\n") as f:
            f.write(gamerefs.make_rmd_patch(RouteFile.load(base_rmd), s.routes, s.level + ".RMD"))
        written.append(up + ".rmdpatch")
    elif s.routes:                             # the base has no route file: the network is the scene's own markers
        s.routes.save(_mod_out(s, folder, up + ".RMD")); written.append(up + ".RMD")
    renamed = {}
    # The walk map (.qtd) is left out on purpose: without one the game builds it - and the generated route markers -
    # from this mission's scene, so placed objects block the AI's paths; DesertStormFix adds the hand-placed markers
    # from <NAME>.RMD back at their indices (scripthost.cpp BuildNav). The octree is rebuilt anyway; its file must exist.
    if _find_file(s.folder, s.level + ".OCT"):
        renamed[up + ".OCT"] = s.level + ".OCT"
    script = _mod_path(s, folder, name.lower() + ".lua")
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
    written += _pack_sources(s, folder)
    banks = _pack_banks(s, folder, up)
    lines = [f"# {up}: game files this mission uses from the player's own install (DesertStormFix builds them at start-up -",
             "# nothing of the game is shipped with the mod). Written by the Conflict Editor."]
    lines += [f"file {dst} = {src}" for dst, src in renamed.items()]
    lines += [f"file {dst}" for dst in sorted(refs)]
    lines += [f"bank {b}" for b in banks]
    used_models = {str(r.get("model")).upper() for r in s.env.find("ENVI")}
    lines += [f"piece {name} = {k['source']} {k['index']}" for name, k in sorted(getattr(s, "kits", {}).items())
              if name in used_models]
    with open(_mod_out(s, folder, up + ".uses"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    written.append(up + ".uses")
    removed = _remove_game_copies(s, folder, up, refs)
    if campaign:
        cfile = os.path.join(folder, "campaign.txt")   # always the mod's top folder (the plugins read it there)
        lines = open(cfile).read().splitlines() if os.path.exists(cfile) else [f"name = {campaign}"]
        entry = f"mission = {name.lower()}.lua"
        if not any(l.strip().lower() == entry for l in lines):
            lines.append(entry)
        with open(cfile, "w", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        written.append("campaign.txt")
    with open(_mod_out(s, folder, up + ".editor.json"), "w", newline="\n") as f:
        json.dump({"base": s.level, "blank": getattr(s, "blank", False), "terrains": sorted(getattr(s, "terrains", {})),
                   "roads": sorted(getattr(s, "roads", {})), "kits": getattr(s, "kits", {}),
                   "layers": {str(k): v for k, v in s.layers.items()},
                   "layer_start": s.layer_start, **logic.extras(s),
                   **({"dll": os.path.basename(s.mission_dll["path"])} if getattr(s, "mission_dll", None) else {})},
                  f, indent=1)
    written.append(up + ".editor.json")
    own_dll = getattr(s, "mission_dll", None)
    if own_dll and own_dll.get("mod") and not own_dll.get("shipped"):
        # a DLL of its own played as a level (X.dll) loads X.dat / X.sch by its name - <X>.level points the plugin at
        # the base level's archive instead (kept when the modder wrote one)
        dll_mod = os.path.join(s.game_dir or game, "Mods", own_dll["mod"])
        side_name = os.path.splitext(os.path.basename(own_dll["path"]))[0] + ".level"
        if not modlayout.find(dll_mod, side_name):
            side = _mod_out(s, dll_mod, side_name)
            with open(side, "w", newline="\n") as f:
                f.write(f"--@archive {s.level.lower()}\n")
            written.append(os.path.basename(side))
    s.dirty = False
    s.mission = (mod, up)
    return {"folder": folder, "files": written, "script_kept": kept, "packed_models": packed, "packed_banks": banks,
            "removed_game_copies": removed,
            "level": name.lower() + ".lua", "backup": backup}


def _script_path(s: Session):
    if not s.mission:
        raise CommandError("open or save a mission first (Save as mission / Open mission)")
    mod, up = s.mission
    return _mod_path(s, os.path.join(s.game_dir or "", "Mods", mod), up.lower() + ".lua")   # any depth in the mod


@command("The open mission's Lua script.")
def get_script(s: Session):
    if s.game_mission:   # a game mission opened as Lua: its events (editor/game_missions.py)
        return game_missions.script_get(s)
    path = _script_path(s)
    if not os.path.exists(path):
        raise CommandError(f"{path} is missing - save the mission again to generate it")
    with open(path, encoding="utf-8") as f:
        return {"path": path, "text": f.read()}


@command("Write the open mission's Lua script (the editor's layers / regions block is refreshed from the scene).",
         text="the whole script")
def save_script(s: Session, text):
    if s.game_mission:
        return game_missions.script_save(s, text)
    path = _script_path(s)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("\r\n", "\n"))
    _update_layers_block(path, s)
    with open(path, encoding="utf-8") as f:
        return {"path": path, "text": f.read()}


@command("The game's messages about the open mission's script from Mods\\<plugin>-report.txt (load / run "
         "errors with their line numbers) - written when the game runs it.")
def script_report(s: Session):
    path = _script_path(s)
    name = os.path.basename(path).lower()
    report = os.path.join(s.game_dir or "", "Mods", s.profile.plugin + "-report.txt")
    if not os.path.exists(report):
        return {"lines": [], "report": report}
    with open(report, encoding="utf-8", errors="replace") as f:
        lines = [l.rstrip() for l in f if name in l.lower()]
    return {"lines": lines[-20:], "report": report}


def _game_running():
    import subprocess
    exe = games.current().exes[0]
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    return exe.lower() in out.lower()


@command("Play the open mission: saves it (scene, routes, layers, regions - the script keeps your code), then starts "
         "the game's exe with +level <mission>.lua (DesertStormFix presses Play in the launcher and loads the mission; "
         "the game quits after its end screen).")
def play(s: Session):
    import subprocess
    if not s.mission:
        raise CommandError("save it as a mission first (Save as mission…)")
    game = s.game_dir
    _require_script_host(s, "Play")
    exe = os.path.join(game or "", s.profile.exes[0])
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
    if "rinfo" in found:
        s.call("fix_rinfo")
    problems = scene_problems(s)
    if problems:
        raise CommandError("fix these first - they crash the game: " +
                           "; ".join(f"line {p['line']}: {p['problem']}" for p in problems[:3]))
    mod, up = s.mission
    saved = save_mission(s, mod, up)
    import time
    proc = subprocess.Popen([exe, "+level", saved["level"]], cwd=game, creationflags=0x00000008)   # DETACHED_PROCESS
    s.game = {"proc": proc, "start": time.time(), "level": saved["level"], "mod": mod, "crash": None}
    return {"level": saved["level"], "mod": mod, "mods": _enabled_mods(game)}


def _enabled_mods(game):
    """The Mods folders the game loads (all of them, in name order - a later one wins a file both have); a _ / .
    prefix switches one off."""
    root = os.path.join(game or "", "Mods")
    if not os.path.isdir(root):
        return []
    return sorted((m for m in os.listdir(root) if os.path.isdir(os.path.join(root, m)) and not m.startswith(("_", "."))),
                  key=str.lower)


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
        if r.kind not in ("HLOI", "ENVI") or not r.fields or (r.kind == "ENVI" and not r.file.is_prop(r)):
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
    for r in _missing_rinfo(s):
        out.append({"line": r.index + 1, "name": r.get("name"), "fix": "rinfo",
                    "problem": f"prop {r.get('name')} says a RINFO line follows but none does (copied before the editor "
                               f"knew) - the game misreads every record after it. Fix clears the flag"})
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
        if r.kind not in ("HLOI", "ENVI") or not r.fields or (r.kind == "ENVI" and not r.file.is_prop(r)):
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
        if games.current().exes[0].lower() not in event.lower():
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


@command("The game console: new <plugin>.log lines since `offset` (bytes), whether the game the editor started "
         "is running, and a crash report when it stopped with an error.", offset="bytes already read (0 = from the start)",
         all_lines="include file opens and voice counters")
def game_console(s: Session, offset=0, all_lines=False):
    game = getattr(s, "game", None)
    path = os.path.join(s.game_dir or "", s.profile.plugin + ".log")
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
    if game:                                   # last seen: the press-any-key prompt, or the level running past it
        for l in lines:
            if "PLEASE PRESS ANY KEY" in l.upper():
                game["waiting"] = True
            elif game.get("waiting") and ("levelInitialise" in l or "DONE" in l):
                game["waiting"] = False
    state = {"log": path if os.path.exists(path) else None, "offset": offset, "lines": shown,
             "running": _game_running(), "level": game and game["level"], "exit_code": None, "crash": None,
             "mods": _enabled_mods(s.game_dir),
             # the loading screen waits for a key (the game's own "PLEASE PRESS ANY KEY TO BEGIN")
             "waiting_for_key": False}
    if game:
        code = game["proc"].poll()
        state["running"] = code is None
        state["waiting_for_key"] = bool(game.get("waiting")) and code is None
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
                names = {os.path.basename(r)[:-len(".editor.json")].upper() for r in modlayout.files(folder)
                         if r.upper().endswith(".EDITOR.JSON")}       # flat mods: top folder, organised: levels\
                out += [{"mod": mod, "name": n} for n in sorted(names)]
    return out


@command("Open a mission saved with save_mission (its scene, routes and layers; models from its base level).",
         mod="mod folder", name="mission name", game_dir="game folder (default: the session's)")
def open_mission(s: Session, mod, name, game_dir=None):
    folder = os.path.join(game_dir or s.game_dir or "", "Mods", mod)
    up = name.upper()
    meta_path = modlayout.find(folder, up + ".editor.json")   # the mod's files at any depth (flat or organised)
    if not meta_path:
        raise CommandError(f"no mission {up} in {folder}")
    with open(meta_path) as f:
        meta = json.load(f)
    open_level(s, meta["base"])
    from conflict_ds_tools import gamerefs
    patch = modlayout.find(folder, up + ".envpatch")
    if patch:                                  # scene = base scene + this mission's changes
        lines = gamerefs.apply_env_patch(open(patch, encoding="latin-1").read(), s.env.lines)
        s.env.lines = lines
        s.env._parse()
    else:                                      # saved before patches: a full copy
        full = modlayout.find(folder, up + ".ENV")
        if not full:
            raise CommandError(f"mission {up} in {folder} has neither {up}.envpatch nor {up}.ENV")
        s.env = EnvFile.load(full)
    rpatch, rmd = modlayout.find(folder, up + ".rmdpatch"), modlayout.find(folder, up + ".RMD")
    if rpatch and s.routes:
        s.routes = gamerefs.apply_rmd_patch(open(rpatch).read(), s.routes)
    elif rmd:
        s.routes = RouteFile.load(rmd)
    s.layers = {int(k): v for k, v in meta.get("layers", {}).items()}
    s.blank = bool(meta.get("blank"))
    from conflict_ds_tools.terrain import Terrain
    s.terrains = {}
    for model in meta.get("terrains", []):
        src = modlayout.find(folder, model + ".terrain.json")
        if src:
            s.terrains[model] = Terrain.from_json(json.load(open(src)))
    from conflict_ds_tools.roads import Road
    s.roads = {}
    for model in meta.get("roads", []):
        src = modlayout.find(folder, model + ".road.json")
        if src:
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
    if meta.get("dll"):                        # saved from a mission DLL (open_dll_mission): its logic runs underneath
        found = mission_dll(s, meta["dll"], mod)
        if found:
            s.mission_dll = {"path": found["path"], "mod": found["mod"], "shipped": found["shipped"], "scene": None}
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
_FILE_RELATIVE_KEYS = {"shot sound", "shot sound 2", "tail sound"}   # paths from the .weapon's folder (DesertStormFix)
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
    path = _find_file(catalog_dir(s), "WEAPS.TXT") if os.path.isdir(catalog_dir(s)) else None
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


def _write_mod_file(s, folder, filename, keys, fields):
    unknown = [k for k in fields if k not in keys]
    if unknown:
        raise CommandError(f"unknown setting(s): {', '.join(unknown)}")
    path = _mod_out(s, folder, filename)       # defs\ in an organised mod
    lines = [f"# made with the Conflict Editor"]
    width = max((len(k) for k in fields), default=0)
    for k in keys:
        v = str(fields.get(k, "")).strip()
        if v:
            if "\n" in v or "#" in v:
                raise CommandError(f"{k}: no line breaks or # in values")
            if k in _FILE_RELATIVE_KEYS and not re.search(r"[\\/]", v):
                # DesertStormFix reads these .wav files from the definition's own folder (mods.cpp ReadWav(dir + v)):
                # an uploaded one elsewhere in the mod (organised: sounds\) is named by its path from there
                hit = modlayout.find(folder, v)
                if hit and os.path.dirname(hit) != os.path.dirname(path):
                    v = os.path.relpath(hit, os.path.dirname(path))
            lines.append(f"{k.ljust(width)} = {v}")
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
    path = _write_mod_file(s, _mod_folder(s, mod), wid + ".weapon", WEAPON_KEYS, fields)
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
    path = _write_mod_file(s, _mod_folder(s, mod), name + ".skin", SKIN_KEYS, fields)
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
    path = _mod_out(s, _mod_folder(s, mod), name)   # dds\ / evos\ / sounds\ ... in an organised mod
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path, "name": stem, "file": name, "bytes": len(data)}


@command("Files in a mod folder: relative paths, sub-folders included (an organised mod keeps them in evos\\, dds\\, "
         "defs\\ ...).", mod="mod folder")
def mod_files(s: Session, mod):
    folder = _mod_folder(s, mod, create=False)
    return modlayout.files(folder) if os.path.isdir(folder) else []


@command("The layout new mods get: organised (files in folders by type - evos\\, dds\\, defs\\, levels\\ ...) or flat "
         "(every file in the mod's top folder). Mods that already have files keep their own layout either way.",
         layout="organised or flat (omit to just read it)")
def set_mod_layout(s: Session, layout=None):
    if layout is not None:
        if layout not in modlayout.LAYOUTS:
            raise CommandError(f"layout: one of {', '.join(modlayout.LAYOUTS)}")
        s.mod_layout = layout
    return {"layout": mod_layout(s)}


@command("The command list with descriptions and arguments (what an MCP client sees).")
def help(s: Session):  # noqa: A001
    import inspect
    out = {}
    for n, (fn, d, a, m) in COMMANDS.items():
        params = list(inspect.signature(fn).parameters.values())[1:]
        out[n] = {"description": d, "args": a, "changes_level": m,
                  "required": [p.name for p in params if p.default is inspect.Parameter.empty and p.name in a],
                  "types": {p.name: t for p in params if p.name in a and (t := _arg_type(p.default, a[p.name]))},
                  "writes_files": n in workflow.WRITES_FILES, "internal": n in workflow.INTERNAL}
    return out


def _arg_type(default, desc):
    """A JSON Schema type for an argument (MCP tool schemas): from its default value, else from its description
    ("[x, y, z]" -> array of 3 numbers, "{...}" -> object). None = any (the command converts it itself)."""
    import inspect
    if default is not inspect.Parameter.empty and default is not None:
        if isinstance(default, bool):
            return {"type": "boolean"}
        if isinstance(default, int):
            return {"type": "number"}      # sizes / counts given as 2.5 too; the command converts
        if isinstance(default, float):
            return {"type": "number"}
        if isinstance(default, str):
            return {"type": "string"}
        if isinstance(default, (list, tuple)):
            return {"type": "array"}
        if isinstance(default, dict):
            return {"type": "object"}
    d = str(desc).lstrip().lower()
    if d.startswith(("[x, y, z]", "optional [x, y, z]", "camera [x, y, z]")):
        return {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    if d.startswith(("{", "optional {", "its arguments {")):
        return {"type": "object"}
    if d.startswith(("[{", "list of", "[")):
        return {"type": "array"}
    if d.startswith(("record line", "1-based line", "the soldier's hloi line", "0-based")):
        return {"type": "integer"}
    return None

import logic  # noqa: E402  (objectives, rules, AI profiles, atmosphere, cameras: editor/logic.py)
if getattr(logic, "_registered", False):     # reloaded: register its commands into the new COMMANDS
    importlib.reload(logic)
logic._registered = True
import records  # noqa: E402  (data tables: editor/records.py)
if getattr(records, "_registered", False):   # this file was reloaded: COMMANDS is new, register the records again
    importlib.reload(records)
records._registered = True
import game_missions  # noqa: E402  (the game's missions opened as readable Lua: editor/game_missions.py)
if getattr(game_missions, "_registered", False):
    importlib.reload(game_missions)
game_missions._registered = True
import forms_cmds  # noqa: E402  (records with ds1: IDs, edited field by field: editor/forms_cmds.py)
if getattr(forms_cmds, "_registered", False):
    importlib.reload(forms_cmds)
forms_cmds._registered = True
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
import missionlogic  # noqa: E402  (the base mission's DLL logic, readable: editor/missionlogic.py)
if getattr(missionlogic, "_registered", False):
    importlib.reload(missionlogic)
missionlogic._registered = True
import paths  # noqa: E402  (AI paths / waypoints / regions: editor/paths.py)
if getattr(paths, "_registered", False):
    importlib.reload(paths)
paths._registered = True
import missionrules  # noqa: E402  (the rule catalogue and Lua compiler logic.py uses)
if getattr(missionrules, "_registered", False):
    importlib.reload(missionrules)
missionrules._registered = True
import interactive  # noqa: E402  (switches + doors, particle emitters: editor/interactive.py)
if getattr(interactive, "_registered", False):
    importlib.reload(interactive)
interactive._registered = True
import vanilla  # noqa: E402  (vanilla lineage: status / revert / restore against the shipped scene: editor/vanilla.py)
if getattr(vanilla, "_registered", False):
    importlib.reload(vanilla)
vanilla._registered = True
import workflow  # noqa: E402  (status, batch, preview, references, check_mission, recovery, screenshot: editor/workflow.py)
if getattr(workflow, "_registered", False):
    importlib.reload(workflow)
workflow._registered = True
import worldtools  # noqa: E402  (Creation-Kit-style world tools: object palette, duplicate array, replace base, path test: editor/worldtools.py)
if getattr(worldtools, "_registered", False):
    importlib.reload(worldtools)
worldtools._registered = True
