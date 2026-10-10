"""The base mission's own logic, readable: what its MissionN.dll does from start to end (editor commands).

conflict_ds_tools/missionflow.py turns the player's own mission DLL into structured statements; this module renders
them for the open level - API slots by name (editor/api_slots.json), script ids as the scene's objects, region
indices as region names, objective indices / text hashes as their texts, message parameters by meaning - and groups
them into events: the set-up before the mission, one entry per game message the DLL reacts to (killed, picked up,
waypoint reached, timer ...) and the checks it runs every frame. It also derives:

* mission groups - objects the DLL brings into play / takes out of play together (what an editor "layer" is for an
  editor mission; the game has no layers of its own: scene objects start in play or waiting, and the DLL activates
  them by script id when something happens);
* per-object uses - every line of the logic that names a script id (the inspector's "Base mission logic").

The output is a tree of nodes {text: [segments], kind, children, open}. Segments are strings or references
{ref: "object" | "region" | "objective" | "global" | "waypoint", id, text} the UI makes clickable. Nothing of the DLL
is shipped: it is read from the player's game folder each time (cached per level for the session).
"""
from __future__ import annotations

import json
import math
import os
import struct

from commands import CommandError, Session, command, _find_file  # noqa: F401

MESSAGES = {
    0x00: ("killed", "When a unit is killed", ["the victim", "the killer", "the time"]),
    0x01: ("removed", "When a dead soldier is removed", ["the soldier"]),
    0x02: ("dropped", "When a soldier drops an item", ["the soldier", "the item"]),
    0x03: ("picked_up", "When a soldier picks up an item", ["the soldier", "the item"]),
    0x04: ("combat", "When an enemy goes on combat alert", ["the enemy"]),
    0x05: ("msg_05", "Message 0x05", ["a", "b"]),
    0x06: ("msg_06", "Message 0x06", ["a", "b"]),
    0x07: ("destroyed", "When an object is destroyed", ["the object", "the attacker"]),
    0x08: ("waypoint", "When a unit reaches a waypoint", ["the unit", "the waypoint"]),
    0x09: ("path_done", "When a unit finishes its path", ["the unit"]),
    0x0A: ("arrived", "When a unit arrives", ["the unit"]),
    0x0B: ("healed", "When a unit is healed", ["the unit"]),
    0x0C: ("damaged", "When a unit is damaged", ["the unit"]),
    0x0D: ("switch", "When a switch is used", ["the switch (name hash)", "1.0", "the soldier"]),
    0x0E: ("radio_call", "When an air strike is called in", ["the soldier", "x", "y", "z", "near"]),
    0x0F: ("laser", "Laser designation query", ["a", "the target"]),
    0x10: ("spawn", "When a spawn point fires", ["the spawn point"]),
    0x11: ("complete", "When the mission is complete", []),
    0x12: ("start", "When the mission starts (after the overview)", []),
    0x13: ("action", "When a soldier uses a custom action", ["the soldier", "the action"]),
    0x14: ("msg_14", "Message 0x14", ["a", "b"]),
    0x15: ("door", "When a door moves", ["the door", "its state"]),
    0x16: ("timer", "When a script timer fires", ["the timer"]),
    0x17: ("planted", "When an explosive is planted", ["the soldier"]),
    0x18: ("explosion", "When something explodes", ["x", "y", "z"]),
    0x19: ("msg_19", "Message 0x19", ["a", "b"]),
    0x1A: ("mount_query", "Can a soldier mount? (query)", ["the soldier", "the vehicle"]),
    0x1B: ("mounted", "When a soldier mounts a vehicle / gun", ["the soldier", "the vehicle"]),
    0x1C: ("dismounted", "When a soldier dismounts", ["the soldier", "the vehicle"]),
    0x1D: ("seat", "When a soldier changes seat", ["the soldier", "the vehicle", "the seat"]),
    0x1E: ("launcher_dropped", "When a launcher is discarded", ["the soldier", "the launcher"]),
    0x1F: ("vehicle_damaged", "When a vehicle is damaged", ["the vehicle", "the attacker"]),
    0x20: ("objectives_screen", "When the objectives screen opens / closes", ["open (1) / closed (0)"]),
    0x26: ("action_query", "Which action may a soldier use? (query, every frame)", ["the soldier", "answer"]),
    0x27: ("query_27", "Query 0x27", ["a", "answer"]),
    0x28: ("drop_query", "May a soldier drop an item? (query)", ["the soldier", "the item", "answer"]),
    0x29: ("pickup_query", "May a soldier pick up an item? (query)", ["the soldier", "the item", "answer"]),
    0x2A: ("vehicle_items_query", "May a soldier use a vehicle's items? (query)", ["the soldier", "the vehicle", "answer"]),
    0x2B: ("switch_soldier_query", "May the player switch soldiers? (query)", ["answer"]),
    0x2C: ("query_2c", "Query 0x2C", ["a", "answer"]),
    0x33: ("give_query", "May a soldier give an item? (query)", ["the giver", "the receiver", "the item", "answer"]),
    0x34: ("spotted", "When an enemy spots someone", ["the observer", "the target"]),
    0x37: ("query_37", "Query 0x37", ["answer"]),
    0x3B: ("overview", "When the briefing closes (the overview starts)", []),
    0x3C: ("shot", "When a weapon is fired", ["the weapon", "the shooter"]),
    0x3D: ("query_3d", "Query 0x3D", ["answer"]),
    0x40: ("fire_mode", "When a soldier changes fire mode", ["the soldier"]),
    0x41: ("zoom", "When a soldier zooms", ["the soldier"]),
    0x44: ("trigger", "When a soldier pulls the trigger", ["the soldier", "the weapon", "consume ammo?"]),
    0x46: ("ladder_query", "May a soldier use a ladder? (query)", ["the soldier", "answer"]),
    0x47: ("orders_query", "Which orders are allowed? (query)", ["the soldier", "answer"]),
    0x4A: ("unlock", "When a soldier unlocks something", ["the soldier", "the held item"]),
    0x4C: ("inventory_query", "May a soldier use the inventory? (query)", ["the soldier", "the item", "answer"]),
    0x52: ("save_loaded", "When a saved game is loaded", []),
}
_OBJECT_WORDS = ("victim", "killer", "soldier", "item", "unit", "object", "vehicle", "door", "enemy", "observer",
                 "target", "attacker", "giver", "receiver", "launcher", "weapon", "shooter")


def param_type(name):
    n = (name or "").lower()
    if "waypoint" in n:
        return "waypoint"
    if "timer" in n:
        return "timer"
    if any(w in n for w in _OBJECT_WORDS):
        return "object"
    return None


NOISE = {0x30C, 0x318}      # debug print (a no-op in the shipped game) and debug key checks: not shown
QUEUE_CALL = 0x518          # queue_call(callback, a, b, delay ms) - shown as "After N s: <what the callback does>"
# conditions as sentences: slot name -> (when true, when false); {0}, {1} = the call's arguments as text
PHRASES = {
    "squad_in_region": ("the squad is in {0}", "the squad is not in {0}"),
    "object_in_region": ("{0} is in {1}", "{0} is not in {1}"),
    "squad_in_zone": ("the squad is in zone {0}", "the squad is not in zone {0}"),
    "is_active": ("{0} is in play", "{0} is not in play"),
    "is_down": ("{0} is down", "{0} is not down"),
    "is_dead": ("{0} is dead", "{0} is alive"),
    "is_door_closed": ("{0} is closed", "{0} is open"),
    "is_path_finished": ("{0} has finished its path", "{0} is still on its path"),
    "is_in_vehicle": ("{0} is in {1}", "{0} is not in {1}"),
    "near_waypoint": ("{0} is near {1}", "{0} is not near {1}"),
    "player_near_object": ("a player is near {0}", "no player is near {0}"),
    "player_near_point": ("a player is near a point", "no player is near the point"),
    "team_none_downed": ("nobody in team {0} is down", "someone in team {0} is down"),
    "team_has_members": ("team {0} has members left", "team {0} is wiped out"),
    "is_loading_save": ("a saved game is loading", "the mission starts fresh"),
    "get_mission_result": ("the mission has ended", "the mission is still running"),
}
INTERNAL_CALLS = {"get_mission_result", "is_loading_save"}    # true of nearly every frame: never the trigger
EFFECT_VERB = {"into play": "appear", "into play (respawned)": "respawn", "out of play": "are removed",
               "shown": "become visible", "hidden": "are hidden"}
EVENT_PHRASE = {"init": "When the level loads", "msg_12": "When the mission starts", "update": "During the mission",
                "msg_10": "When a spawn point fires", "msg_3b": "When the overview starts"}


def unit_label(unit: str) -> str:
    """IR_VEH_Iraqi_Truck1 -> Iraqi Truck1, Iraqi_Conscript -> Iraqi Conscript."""
    u = str(unit)
    for pre in ("IR_VEH_", "US_VEH_", "UK_VEH_", "IR_DOOR_", "US_DOOR_", "US_WPN_", "IR_WPN_"):
        if u.upper().startswith(pre):
            u = u[len(pre):]
            break
    return u.replace("_", " ").strip()


PLAY_STATE = {0x0C4: "into play", 0x0DC: "into play (respawned)", 0x2C0: "shown", 0x0C8: "out of play",
              0x2C4: "hidden"}     # slots that change whether an object is in the game - what mission groups collect
DEBUG_FLAG = 0x390
STUBS = {0x8A8: 1, 0x394: 1, 0x398: 0, 0x03C: 0}   # slots that return a constant in the shipped game: folded
DOOR_STATES = {0: "closing", 1: "opening", 2: "closed", 3: "open"}          # "debug mappings on?" - always off for players: its branches are dropped
_SLOTS = None
_CACHE = {}


def slots() -> dict[int, dict]:
    """editor/api_slots.json: {"0x0C4": {name, args: [{name, type}], returns, desc, confidence}}."""
    global _SLOTS
    if _SLOTS is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_slots.json")
        try:
            with open(path, encoding="utf-8") as f:
                _SLOTS = {int(k, 16): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            _SLOTS = {}
    return _SLOTS


def _game_dir(s: Session):
    if s.game_dir:
        return s.game_dir
    from conflict_ds_tools import archive
    return archive.find_game_dir()


def _dll_path(s: Session):
    game = _game_dir(s)
    if not game or not s.level:
        return None
    return _find_file(game, s.level + ".DLL")


def analysis(s: Session):
    path = _dll_path(s)
    if not path:
        return None, None
    key = (path, os.path.getmtime(path))
    if key not in _CACHE:
        from conflict_ds_tools import missionflow
        _CACHE[key] = missionflow.analyse(path)
    return _CACHE[key], os.path.basename(path)


# --------------------------------------------------------------------------------------------- the scene --
class Scene:
    def __init__(self, s: Session):
        import logic
        self.objects = {}
        for r in s.env.find("HLOI"):
            if r.fields:
                self.objects[r["script_id"]] = r
        self.regions = [str(r["name"]) for r in s.env.find("REGN") if r.fields]
        self.waypoints = [str(r["name"]) for r in s.env.find("NEWENVWAYPT") if r.fields]
        self.base_objectives = {o["hash"]: o for o in logic.base_objectives(s)}
        self.objective_titles = {o["id"]: o["title"] for o in logic.base_objectives(s)}
        self.text = lambda h: logic._resolve_text(s, h)[1]
        self.s = s

    def object_text(self, sid):
        r = self.objects.get(sid)
        if r is None:
            return f"object #{sid} (not in the scene)"
        unit = str(r["unit"])
        return f"{unit} #{sid}"


def _as_float(v):
    try:
        f = struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
    except struct.error:
        return None
    return f if math.isfinite(f) else None


def _looks_float(v):
    if not isinstance(v, int) or -0x10000 < v < 0x10000:
        return False
    f = _as_float(v)
    return f is not None and 1e-4 < abs(f) < 1e7 and abs(f * 64 - round(f * 64)) < 1e-3 * max(1, abs(f))


# ---------------------------------------------------------------------------------------------- rendering --
class Renderer:
    def __init__(self, s: Session, a: dict, dll: str):
        self.s, self.a, self.dll = s, a, dll
        self.scene = Scene(s)
        self.slots = slots()
        self.api_at = {}                # call address -> api statement (to name results / objective globals)
        for body in a["functions"].values():
            self._index(body)
        self.global_names = self._name_globals()
        self.uses: dict[int, list] = {}     # script id -> [(event title, line text)]
        self.groups: dict[tuple, dict] = {}
        self._event = None
        self._event_id = None
        self._path = []
        self.flag_sets: dict[int, list] = {}

    def _index(self, body):
        for st in body:
            if st["op"] == "api":
                self.api_at[st["at"]] = st
            for k in ("then", "else", "body"):
                if k in st:
                    self._index(st[k])
            for c in st.get("cases", []):
                self._index(c["body"])

    # --- globals ---
    def _name_globals(self):
        names = {}
        a = self.a
        # objective indices: written from the result of add_objective (+0x4B0)
        for g, info in a["globals"].items():
            for w in info["writes"]:
                v = w["value"]
                if v.get("k") == "result" and v.get("slot") == 0x4B0:
                    call = self.api_at.get(v["at"])
                    title = None
                    if call and call["args"] and call["args"][0].get("k") == "int":
                        h = call["args"][0]["v"] & 0xFFFFFFFF
                        o = self.scene.base_objectives.get(h)
                        title = o["title"] if o else self.scene.text(h)
                    names[g] = f"objective “{title}”" if title else "an objective"
        # the mission phase: the global update branches on with several values
        upd = a["functions"].get(a["update_fn"], [])
        counts = {}

        def scan(stmts):
            for st in stmts:
                if st["op"] == "if":
                    c = st["cond"]
                    if c.get("l", {}).get("k") == "global" and c.get("r", {}).get("k") == "int":
                        counts.setdefault(c["l"]["a"], set()).add(c["r"]["v"])
                    scan(st["then"]); scan(st["else"])
        scan(upd)
        phase = max(counts, key=lambda g: len(counts[g]), default=None)
        if phase is not None and len(counts[phase]) >= 2:
            names[phase] = "the mission phase"
            self.phase = phase
        else:
            self.phase = None
        n = 0
        for g in sorted(a["globals"]):
            if g not in names:
                n += 1
                names[g] = f"flag {n}"
        return names

    def global_label(self, g):
        return self.global_names.get(g, f"G_{g:X}")

    # --- values ---
    def value(self, v, ctx, typ=None):
        k = v.get("k")
        if k == "argfield" and v["i"] == 0 and ctx.get("queue") is not None:
            from conflict_ds_tools.missionflow import QUEUE_FIELDS
            q = QUEUE_FIELDS.get(v["off"])
            if q is not None and q < len(ctx["queue"]):
                return self.value(ctx["queue"][q], ctx.get("outer", {}), typ)
        if k == "arg":
            args = ctx.get("args")
            if args is not None and v["i"] < len(args):
                return self.value(args[v["i"]], ctx.get("outer", {}), typ)
            return [f"argument {v['i'] + 1}"]
        if k == "msg":
            names = ctx.get("msg_names") or []
            return [names[v["i"]] if v["i"] < len(names) else f"message value {v['i'] + 1}"]
        if k == "global":
            g = v["a"]
            return [{"ref": "global", "id": g, "text": self.global_label(g)}]
        if k == "str":
            return [f"“{v['s']}”"]
        if k == "result":
            call = self.api_at.get(v.get("at"))
            if call:
                return ["(result of ", *self.call_text(call, ctx), ")"]
            return ["(result of a routine)"]
        if k == "int":
            return self.number(v["v"], typ)
        if k == "minus":
            return [*self.value(v["x"], ctx), f" − {v['n']}"]
        if k == "op":
            return [f"{v['o']} of ", *self.value(v["x"], ctx)]
        if k == "addr":
            return ["(a value on the stack)"]
        if k == "api":
            return ["the script API"]
        return ["?"]

    def number(self, n, typ):
        sc = self.scene
        if typ == "object":
            if n == -1:
                return ["nobody (−1)"]
            if n in sc.objects:
                self._use(n)
                return [{"ref": "object", "id": n, "text": sc.object_text(n)}]
            return [f"object #{n}"]
        if typ == "region" and 0 <= n < len(sc.regions):
            return [{"ref": "region", "id": n, "text": sc.regions[n]}]
        if typ == "waypoint" and 0 <= n < len(sc.waypoints):
            return [{"ref": "waypoint", "id": n, "text": sc.waypoints[n]}]
        if typ == "objective":
            t = sc.objective_titles.get(n)
            return [{"ref": "objective", "id": n, "text": f"objective {n}" + (f" “{t}”" if t else "")}]
        if typ == "text":
            t = sc.text(n & 0xFFFFFFFF)
            return [f"“{t}”" if t else f"text 0x{n & 0xFFFFFFFF:08X}"]
        if typ == "float" or (typ is None and _looks_float(n)):
            f = _as_float(n)
            if f is not None:
                return [f"{f:g}"]
        if typ == "bool" and n in (0, 1):
            return ["yes" if n else "no"]
        if typ in ("time_ms",):
            return [f"{n / 1000:g} s"]
        if typ is None and n in sc.objects and n > 20:
            # an untyped number that is a script id of this scene: mark it, don't claim it
            return [f"{n}"]
        return [hex(n) if abs(n) > 0xFFFF else str(n)]

    def _use(self, sid):
        if self._event is not None:
            self.uses.setdefault(sid, [])

    # --- statements ---
    def call_text(self, st, ctx):
        info = self.slots.get(st["slot"], {})
        name = info.get("name") or f"api_0x{st['slot']:03X}"
        types = [a.get("type") for a in info.get("args", [])]
        anames = [a.get("name") for a in info.get("args", [])]
        words = name.replace("_", " ")
        out = [words[:1].upper() + words[1:]]
        for k, v in enumerate(st["args"]):
            t = types[k] if k < len(types) else None
            out.append(": " if k == 0 else ", ")
            seg = self.value(v, ctx, t)
            named = any(isinstance(x, dict) for x in seg)
            if not named and k < len(anames) and anames[k] and len(st["args"]) > 1 \
                    and anames[k] not in ("object", "unit", "soldier", "region"):
                out.append(f"{anames[k].replace('_', ' ')} ")
            out += seg
        return out

    def phrase_of_call(self, call, ctx, true=True):
        """A condition on a call as a sentence (PHRASES), else None."""
        info = self.slots.get(call["slot"], {})
        ph = PHRASES.get(info.get("name"))
        if not ph:
            return None
        types = [a.get("type") for a in info.get("args", [])]
        parts = [self.value(v, ctx, types[k] if k < len(types) else None) for k, v in enumerate(call["args"])]
        tmpl = ph[0 if true else 1]
        out = []
        for chunk in __import__("re").split(r"(\{\d\})", tmpl):
            m = __import__("re").fullmatch(r"\{(\d)\}", chunk)
            if m:
                out += parts[int(m.group(1))] if int(m.group(1)) < len(parts) else ["?"]
            elif chunk:
                out.append(chunk)
        return out

    def cond_text(self, c, ctx):
        if "not" in c:
            return ["not (", *self.cond_text(c["not"], ctx), ")"]
        if "text" in c:
            return ["(a condition the analysis can't read)"]
        l, op, r = c["l"], c["op"], c["r"]
        lt = self._type_of(l, ctx)
        if l.get("k") == "result" and r == {"k": "int", "v": 0} and op in ("==", "!="):
            call = self.api_at.get(l.get("at"))
            if call:
                ph = self.phrase_of_call(call, ctx, op == "!=")
                if ph:
                    return ph
                head = [] if op == "!=" else ["not: "]
                return head + self.call_text(call, ctx)
        if r == {"k": "int", "v": 0} and op in ("==", "!=") and l.get("k") == "global":
            return [*self.value(l, ctx), " is " + ("not set" if op == "==" else "set")]
        if c.get("test"):
            return [*self.value(l, ctx), " has bits ", *self.value(r, ctx), " " + ("clear" if op == "==" else "set")]
        lv, lctx = self._resolve_ctx(l, ctx)
        if lv.get("k") == "msg" and lctx.get("msg_names") and lv["i"] < len(lctx["msg_names"]) \
                and lctx["msg_names"][lv["i"]] == "its state" and r.get("k") == "int" and r["v"] in DOOR_STATES \
                and op in ("==", "!="):
            return ["the door is " + ("" if op == "==" else "not ") + DOOR_STATES[r["v"]]]
        word = {"==": " is ", "!=": " is not ", "<": " < ", ">": " > ", "<=": " ≤ ", ">=": " ≥ "}.get(op, f" {op} ")
        return [*self.value(l, ctx), word, *self.value(r, ctx, lt)]

    def _type_of(self, v, ctx=None):
        ctx = ctx or {}
        while v.get("k") == "arg" and ctx.get("args") is not None and v["i"] < len(ctx["args"]):
            v, ctx = ctx["args"][v["i"]], ctx.get("outer", {})
        if v.get("k") == "msg":
            names = ctx.get("msg_names") or []
            return param_type(names[v["i"]]) if v["i"] < len(names) else None
        if v.get("k") == "result":
            call = self.api_at.get(v.get("at"))
            ret = (self.slots.get(call["slot"], {}) if call else {}).get("returns", "")
            return "object" if str(ret).startswith("object") else None
        return None

    def render(self, stmts, ctx, depth=0):
        out = []
        k = 0
        while k < len(stmts):
            st = stmts[k]
            o = st["op"]
            nxt = stmts[k + 1] if k + 1 < len(stmts) else None
            if o == "api":
                if st["slot"] in NOISE or st["slot"] == DEBUG_FLAG:
                    k += 1; continue
                # a call whose result the next if tests: shown once, as the condition
                if st["slot"] in STUBS:
                    k += 1; continue
                if nxt and nxt["op"] == "if" and st["at"] in (nxt["cond"].get("l", {}).get("at"),
                                                              nxt["cond"].get("r", {}).get("at")):
                    k += 1; continue
                if st["slot"] == QUEUE_CALL and st["args"] and st["args"][0].get("k") == "code":
                    out += self._queued(st, ctx, depth)
                    k += 1; continue
                out.append(self.node(self.call_text(st, ctx), "action", api=st["slot"]))
                self._record_action(st, ctx)
            elif o == "call":
                body = self.a["functions"].get(st["fn"], [])
                inner = {"args": st["args"], "outer": ctx, "msg_names": None}
                children = self.render(body, inner, depth + 1) if depth < 5 else []
                if children:
                    if len(children) <= 2 and depth < 4:
                        out += children        # short routines read better inline
                    else:
                        out.append(self.node([self._summary(children)], "routine", children, open_=False))
            elif o == "set":
                g = st["var"].get("a")
                if st["value"].get("k") == "int" and st["value"]["v"] != 0:
                    conds = [self._plain(p[0]) for p in self._path if p and not p[1]]
                    conds = [c for c in conds if "flag" not in c and "the mission phase" not in c]
                    self.flag_sets.setdefault(g, []).append((self._event, conds[-1] if conds else None))
                out.append(self.node([{"ref": "global", "id": g, "text": self.global_label(g)}, " = ",
                                      *self.value(st["value"], ctx)], "set"))
            elif o == "if":
                c = st["cond"]
                if c.get("l", {}).get("slot") == DEBUG_FLAG and c.get("r") == {"k": "int", "v": 0} \
                        and c.get("op") in ("==", "!="):
                    off = st["else"] if c["op"] == "!=" else st["then"]     # players never have debug mappings on
                    out += self.render(off, ctx, depth)
                    k += 1; continue
                fixed = self._fold(c)
                if fixed is not None:
                    out += self.render(st["then"] if fixed else st["else"], ctx, depth)
                    k += 1; continue
                cond = self.cond_text(c, ctx)
                if self._debug_only(st):
                    k += 1; continue
                from conflict_ds_tools.missionflow import negate
                phase = c.get("l", {}).get("k") == "global" and c["l"].get("a") == self.phase
                self._path.append(None if phase else (cond, self._is_internal(c, ctx) or "?" in self._plain(cond)))
                then = self.render(st["then"], ctx, depth)
                self._path.pop()
                ncond = self.cond_text(negate(c), ctx)
                self._path.append(None if phase else (ncond, self._is_internal(negate(c), ctx) or "?" in self._plain(ncond)))
                els = self.render(st["else"], ctx, depth)
                self._path.pop()
                if then:
                    out.append(self.node(["If ", *cond], "if", then))
                if els:
                    if then:
                        out.append(self.node(["Otherwise"], "else", els))
                    else:
                        from conflict_ds_tools.missionflow import negate
                        out.append(self.node(["If ", *self.cond_text(negate(c), ctx)], "if", els))
            elif o == "switch":
                cases = []
                on = st["on"]
                if on.get("k") == "minus" and isinstance(on.get("n"), int):
                    st = dict(st, on=on["x"], cases=[dict(c, values=[v + on["n"] for v in c["values"]])
                                                     for c in st["cases"]])
                for c in st["cases"]:
                    self._path.append(([*self.value(st["on"], ctx), " is ",
                                        ", ".join(self._case_val(st["on"], v, ctx) for v in c["values"][:6])],
                                       self._resolve(st["on"], ctx).get("k") not in ("msg", "result")))
                    body = self.render(c["body"], ctx, depth)
                    self._path.pop()
                    if body:
                        cases.append(self.node(["When it is ", ", ".join(self._case_val(st["on"], v, ctx)
                                                                        for v in c["values"][:12])], "case", body))
                if cases:
                    out.append(self.node(["Depending on ", *self.value(st["on"], ctx)], "switch", cases))
            elif o == "loop":
                body = self.render(st["body"], ctx, depth)
                if body:
                    out.append(self.node(["Repeat:"], "loop", body))
            elif o == "import":
                pass
            k += 1
        return out

    def _summary(self, nodes):
        """"Steps: fade out, play music cue, …" from the first actions inside."""
        names = []

        def walk(ns):
            for n in ns:
                if len(names) >= 3:
                    return
                if n["kind"] in ("action", "queued"):
                    t = self._plain(n["text"])
                    t = t.split("(")[0].replace("After ", "after ").strip()
                    if t and t not in names:
                        names.append(t)
                walk(n["children"])
        walk(nodes)
        return "Steps: " + (", ".join(names) + " …" if names else "(conditions only)")

    def _is_internal(self, c, ctx):
        """Bookkeeping a reader doesn't need as a trigger: the DLL's own flags / bit sets, clocks, its phase."""
        if "not" in c:
            return self._is_internal(c["not"], ctx)
        l, r = c.get("l", {}), c.get("r", {})
        l, r = self._resolve(l, ctx), self._resolve(r, ctx)
        readable = ("msg", "int", "result", "str")
        if l.get("k") not in readable or r.get("k") not in readable:
            return True                          # values the analysis couldn't follow
        if c.get("op") == "!=" and l.get("k") == "msg":
            return True                          # "the waypoint is not X": the other branch of a test
        for v in (l, r):
            if v.get("k") in ("global", "minus", "op", "unknown", "addr"):
                return True
            if v.get("k") == "result":
                call = self.api_at.get(v.get("at"))
                name = (self.slots.get(call["slot"], {}).get("name", "") if call else "")
                if "time" in name or "random" in name:
                    return True
        if l.get("k") == "int" and r.get("k") == "int":
            return True                          # constant against constant: compiler leftovers
        if c.get("op") in ("<", ">", "<=", ">=") and l.get("k") in ("msg", "arg"):
            return True                          # range checks before a switch on the same value
        if l.get("k") == "result":
            call = self.api_at.get(l.get("at"))
            if call and self.slots.get(call["slot"], {}).get("name") in INTERNAL_CALLS:
                return True
        if l.get("k") == "result" and r == {"k": "int", "v": 0} and c.get("op") == "==":
            call = self.api_at.get(l.get("at"))     # negative checks ("not in region") rarely name the trigger
            name = self.slots.get(call["slot"], {}).get("name", "") if call else ""
            return name in PHRASES and name not in ("is_dead", "is_door_closed", "is_loading_save")
        return False

    def _fold(self, c):
        """A condition on a stub's constant result: True / False, else None."""
        l, r = c.get("l", {}), c.get("r", {})
        if l.get("k") != "result" or l.get("slot") not in STUBS or r.get("k") != "int":
            return None
        a, b = STUBS[l["slot"]], r["v"]
        return {"==": a == b, "!=": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}.get(c.get("op"))

    def _queued(self, st, ctx, depth):
        """queue_call(callback, a, b, delay): the callback's one API call, after the delay."""
        args = st["args"]
        body = self.a["functions"].get(args[0]["a"], [])
        inner = {"queue": [args[1] if len(args) > 1 else {"k": "unknown"},
                           args[2] if len(args) > 2 else {"k": "unknown"}], "outer": ctx}
        children = self.render(body, inner, depth + 1)
        delay = None
        if len(args) > 3 and args[3].get("k") == "int":
            from struct import pack, unpack
            delay = unpack("<f", pack("<I", args[3]["v"] & 0xFFFFFFFF))[0] / 1000
        head = f"After {delay:g} s: " if delay is not None and delay > 0 else "Next: " if delay == 0 else "Later: "
        if len(children) == 1 and not children[0]["children"]:
            return [self.node([head, *children[0]["text"]], "queued", api=children[0].get("api"))]
        return [self.node([head.rstrip(": ") + ":"], "queued", children)] if children else []

    def _case_val(self, on, v, ctx):
        seg = self.number(v, self._type_of(on, ctx))
        return "".join(x if isinstance(x, str) else x["text"] for x in seg)

    def _debug_only(self, st):
        """if (debug build) print(...) - the shipped game's debug check around debug prints."""
        def only_noise(stmts):
            return all(x["op"] == "api" and x["slot"] in NOISE or x["op"] == "return" for x in stmts)
        return only_noise(st["then"]) and only_noise(st["else"])

    def node(self, text, kind, children=None, open_=True, **extra):
        return {"text": text, "kind": kind, "children": children or [], "open": open_, **extra}

    # --- groups / uses ---
    def _record_action(self, st, ctx):
        info = self.slots.get(st["slot"], {})
        act = PLAY_STATE.get(st["slot"])
        if act is None:
            return
        if not st["args"]:
            return
        v = self._resolve(st["args"][0], ctx)
        if v.get("k") != "int" or v["v"] not in self.scene.objects:
            return
        when = [self._plain(p[0]) for p in self._path if p and not p[1]][-2:]
        detail = [self._plain(p[0]) for p in self._path if p]
        key = (self._event, act, tuple(when))
        g = self.groups.setdefault(key, {"event": self._event, "event_id": self._event_id, "action": act,
                                         "when": when, "detail": detail, "objects": [], "how": set()})
        g["how"].add(info.get("name") or f"api_0x{st['slot']:03X}")
        if v["v"] not in g["objects"]:
            g["objects"].append(v["v"])

    def _resolve_ctx(self, v, ctx):
        while v.get("k") == "arg" and ctx.get("args") is not None and v["i"] < len(ctx["args"]):
            v, ctx = ctx["args"][v["i"]], ctx.get("outer", {})
        return v, ctx

    def _resolve(self, v, ctx):
        from conflict_ds_tools.missionflow import QUEUE_FIELDS
        for _ in range(8):
            if v.get("k") == "arg" and ctx.get("args") is not None and v["i"] < len(ctx["args"]):
                v, ctx = ctx["args"][v["i"]], ctx.get("outer", {})
            elif v.get("k") == "argfield" and ctx.get("queue") is not None and v["off"] in QUEUE_FIELDS:
                v, ctx = ctx["queue"][QUEUE_FIELDS[v["off"]]], ctx.get("outer", {})
            else:
                break
        return v

    @staticmethod
    def _plain(segs):
        return "".join(x if isinstance(x, str) else x.get("text", "") for x in segs)


def _walk_refs(nodes, event, out, when=None):
    for n in nodes:
        text = Renderer._plain(n["text"])
        for seg in n["text"]:
            if isinstance(seg, dict) and seg.get("ref") == "object":
                out.setdefault(seg["id"], []).append({"event": event, "text": text, "when": when})
        _walk_refs(n["children"], event, out, text if n["kind"] in ("if", "case", "else") else when)


def _summarise_groups(R) -> list[dict]:
    """Mission groups for people: "When the squad is in TRIGGER_PATROL" -> "3 appear: Iraqi Conscript ×2, Truck1".
    Groups with the same objects and effect but several triggers are merged (any of the triggers)."""
    from collections import Counter
    merged = {}
    for g in R.groups.values():
        if not g["objects"]:
            continue
        key = (g["action"], tuple(sorted(g["objects"])))
        m = merged.setdefault(key, {"action": g["action"], "objects": sorted(g["objects"]), "triggers": [],
                                    "how": set()})
        base = EVENT_PHRASE.get(g["event_id"]) or g["event"]
        if g["event_id"] == "msg_10" and len(g["when"]) == 1 and g["when"][0].startswith("the spawn point is "):
            trig = f"When spawn point {g['when'][0][len('the spawn point is '):]} fires"
        elif g["when"]:
            trig = ("When " if base == "During the mission" else base + ", if ") + " and ".join(g["when"])
        else:
            trig = base
        if trig not in [t["text"] for t in m["triggers"]]:
            m["triggers"].append({"text": trig, "event": g["event"], "detail": g["detail"]})
        m["how"] |= g["how"]
    out = []
    for m in merged.values():
        names = Counter(unit_label(R.scene.objects[i]["unit"]) for i in m["objects"] if i in R.scene.objects)
        kinds = ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in names.most_common())
        n = len(m["objects"])
        verb = EFFECT_VERB.get(m["action"], m["action"])
        if n == 1:
            verb = {"appear": "appears", "respawn": "respawns", "are removed": "is removed",
                    "become visible": "becomes visible", "are hidden": "is hidden"}.get(verb, verb)
        title = m["triggers"][0]["text"] + (f" (or {len(m['triggers']) - 1} more)" if len(m["triggers"]) > 1 else "")
        first = m["triggers"][0]
        cat = ("reinforcements" if any(t["text"].startswith("When spawn point") for t in m["triggers"])
               else "start" if first["text"] in ("When the level loads", "When the mission starts")
               else "triggered" if first["event"] == "Every frame" else "events")
        out.append({"title": title, "effect": f"{n} {verb}: {kinds}", "action": m["action"], "category": cat,
                    "objects": [{"id": i, "text": R.scene.object_text(i)} for i in m["objects"]],
                    "triggers": m["triggers"], "how": sorted(m["how"]),
                    # for the old fields
                    "event": m["triggers"][0]["event"], "when": [m["triggers"][0]["text"]]})
    order = {"When the level loads": 0, "When the mission starts": 1}
    out.sort(key=lambda g: (order.get(g["title"], 2), g["title"]))
    return out


def build(s: Session) -> dict:
    a, dll = analysis(s)
    if a is None:
        return {"dll": None, "events": [], "groups": [], "globals": [], "uses": {}}
    R = Renderer(s, a, dll)
    _render_events(R, a)                       # pass 1: where flags are set
    for g, sets in R.flag_sets.items():
        if R.global_names.get(g, "").startswith("flag ") and len(sets) == 1 and sets[0][1]:
            R.global_names[g] = f"flag “{sets[0][1][:60]}”"
    R.groups, R.uses = {}, {}
    events = _render_events(R, a)
    uses = {}
    for e in events:
        _walk_refs(e["nodes"], e["title"], uses)
    groups = _summarise_groups(R)
    glob = [{"id": g, "name": R.global_label(g), "writes": len(info["writes"])}
            for g, info in sorted(a["globals"].items())]
    return {"dll": dll, "events": events, "groups": groups, "globals": glob,
            "uses": {str(k): v for k, v in uses.items()}, "named_slots": len(R.slots)}


def _render_events(R, a):
    events = []

    def add(eid, title, body, ctx, note=None, open_=False):
        R._event, R._event_id = title, eid
        nodes = R.render(body, ctx)
        if nodes:
            events.append({"id": eid, "title": title, "nodes": nodes, "note": note, "open": open_})
    add("init", "Set-up (level load, before the intro)", a["functions"].get(a["init_fn"], []), {},
        "Loads the scene, texts, sounds and map; adds the objectives; sets up teams.")
    handled = a["messages"]
    by_body = {}
    for mid, body in sorted((k, v) for k, v in handled.items() if k != "all"):
        key = json.dumps(body, sort_keys=True, default=str)
        by_body.setdefault(key, []).append(mid)
    for key, mids in by_body.items():
        body = handled[mids[0]]
        for mid in mids:
            name, title, params = MESSAGES.get(mid, (f"msg_{mid:02x}", f"Message 0x{mid:02X}", []))
            add(f"msg_{mid:02x}", title, body, {"msg_names": params}, f"game message 0x{mid:02X} ({name})")
    add("update", "Every frame", a["functions"].get(a["update_fn"], []), {},
        "Runs each frame; the mission phase decides which part runs (intro film, overview, playing).")
    return events


def _cached(s: Session):
    key = (s.level, id(s.env), len(s.env.lines) if s.env else 0, getattr(s, "_mf_gen", 0))
    c = getattr(s, "_mission_flow", None)
    if c is None or c[0] != key:
        s._mission_flow = (key, build(s))
    return s._mission_flow[1]


@command("How the base mission works, end to end: its DLL's set-up, every game message it reacts to (killed, picked "
         "up, waypoint, timer, door, ...) and its per-frame checks, as readable steps with the scene's objects, "
         "regions and objectives named. Read from the player's own MissionN.dll. Empty for levels without one.")
def mission_flow(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    out = dict(_cached(s))
    out.pop("uses", None)
    return out


@command("Objects the base mission brings into play or takes out of play together, and when - the game's equivalent "
         "of layers (it has none of its own: its DLL activates objects by script id).")
def mission_groups(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    f = _cached(s)
    return {"dll": f["dll"], "groups": f["groups"]}


@command("Every line of the base mission's logic that names this object (by script id).", script_id="script id")
def object_logic(s: Session, script_id):
    if s.env is None:
        raise CommandError("no level open")
    f = _cached(s)
    return {"dll": f["dll"], "uses": f["uses"].get(str(int(script_id)), [])}


@command("The game's script functions (API slots) the 'Call a game function' rule action can call: slot, name, "
         "arguments (name, type), what it returns, a description and how sure the naming is.")
def api_functions(s: Session):
    return {"functions": [{"slot": k, "name": v.get("name"), "args": v.get("args", []), "returns": v.get("returns"),
                           "desc": v.get("desc", ""), "confidence": v.get("confidence")}
                          for k, v in sorted(slots().items())]}
