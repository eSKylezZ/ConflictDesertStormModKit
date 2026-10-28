"""Mission rules: "when <trigger>, if <conditions>, do <actions>" - the editor's way to write mission logic without Lua.

A rule is plain data (kept with the mission in <NAME>.editor.json):

    {"name": "Ambush at the bridge", "enabled": true, "once": true,
     "trigger": {"type": "region_enter", "region": "BRIDGE_TRIGGER", "who": "player"},
     "conditions": [{"type": "alive", "object": 164}, {"type": "flag", "name": "alarm", "value": false, "not": false}],
     "actions": [{"type": "activate", "objects": [238, 239]}, {"type": "wait", "seconds": 3},
                 {"type": "patrol", "units": [238], "path": "BRIDGE_PATROL_0", "movement": "run"}]}

TRIGGERS / CONDITIONS / ACTIONS below describe every type: its label, its parameters (name, type, label, default) and
how it compiles to Lua. Parameter types are what the UI picks with: object, objects, region, waypoint, path (a path's
first waypoint), objective, layer, switch, text, number, seconds, metres, percent, team, flag, counter, bool, choice,
slot (a script API slot for "call a game function"), lua.

Compiled (logic.py's rules block) into a small interpreter: each rule is a table of steps (functions, or a number =
wait that many seconds); waits are kept in game.state (rule, step, due time) so a saved game resumes them. Message
triggers are matched in editor_on_message, the rest (mission start, after N s, every N s, conditions becoming true,
objects reaching regions) are polled in editor_update(), which the script's update() calls.

Game functions are called by their script API slot (editor/games/<game>/api_slots.json) through game.call / game.callf -
the slot table is the game's own, decoded from the exe; the ones used here were checked against the shipped
mission DLLs' calls (see missionlogic.py). Objects are scene script ids, regions names, waypoints ids.
"""
from __future__ import annotations

import json
import math
import os

from commands import CommandError, Session, _lua_value  # noqa: F401

# --------------------------------------------------------------------------------------------- the catalogue --
# param: (name, type, label, default) - default None = required


def P(name, typ, label, default=None, **kw):
    return {"name": name, "type": typ, "label": label, "default": default, **kw}


# difficulty choices in order (easiest first); value = the game's level number (difficulty.cpp: 0-2 the game's own,
# 3 VERY HARD, 4 VERY EASY), compared by rank
DIFFICULTIES = {"very_easy": "VERY EASY", "easy": "EASY", "medium": "MEDIUM", "hard": "HARD", "very_hard": "VERY HARD"}
DIFFICULTY_LEVEL = {"very_easy": 4, "easy": 0, "medium": 1, "hard": 2, "very_hard": 3}
DIFFICULTY_RANK_LUA = "({[4]=0,[0]=1,[1]=2,[2]=3,[3]=4})[game.difficulty and game.difficulty() or 1]"

TEAMS = {"0": "the squad (team 0)", "1": "team 1", "2": "enemies (team 2)", "3": "team 3", "4": "team 4"}

TRIGGERS = {
    "mission_start": {"label": "When the mission starts", "params": [], "group": "Time",
                      "help": "when play begins - after the intro film and the overview on levels made from a campaign mission"},
    "after_time": {"label": "Some seconds after the mission starts", "params": [P("seconds", "seconds", "after", 30)],
                   "group": "Time"},
    "every": {"label": "Every few seconds", "params": [P("seconds", "seconds", "every", 10)], "group": "Time"},
    "becomes_true": {"label": "When the conditions become true", "params": [], "group": "Time",
                     "help": "checked 4 times a second; fires when all conditions turn true (again after they were false)"},
    "region_enter": {"label": "When someone enters a region",
                     "params": [P("region", "region", "region"),
                                P("who", "choice", "who", "player", choices={"player": "a player", "squad": "the whole squad",
                                                                              "object": "this object"}),
                                P("object", "object", "object", -1, show_if={"who": "object"})], "group": "Places"},
    "region_leave": {"label": "When someone leaves a region",
                     "params": [P("region", "region", "region"),
                                P("who", "choice", "who", "player", choices={"player": "a player", "object": "this object"}),
                                P("object", "object", "object", -1, show_if={"who": "object"})], "group": "Places"},
    "killed": {"label": "When something is killed / destroyed",
               "params": [P("objects", "objects", "any of these (empty = anything)", []),
                          P("mode", "choice", "fires", "any", choices={"any": "for each one", "all": "when all are dead"})],
               "group": "Combat"},
    "damaged": {"label": "When a unit is damaged", "params": [P("objects", "objects", "these (empty = any)", [])],
                "group": "Combat"},
    "alerted": {"label": "When an enemy goes on combat alert", "params": [P("objects", "objects", "these (empty = any)", [])],
                "group": "Combat"},
    "spotted": {"label": "When an enemy spots someone",
                "params": [P("objects", "objects", "spotted by (empty = any)", [])], "group": "Combat"},
    "picked_up": {"label": "When an item is picked up", "params": [P("item", "object", "item (−1 = any)", -1)],
                  "group": "Items"},
    "dropped": {"label": "When an item is dropped", "params": [P("item", "object", "item (−1 = any)", -1)],
                "group": "Items"},
    "waypoint": {"label": "When a unit reaches a waypoint",
                 "params": [P("waypoint", "waypoint", "waypoint"), P("unit", "object", "unit (−1 = any)", -1)],
                 "group": "Movement"},
    "path_done": {"label": "When a unit finishes its path", "params": [P("unit", "object", "unit (−1 = any)", -1)],
                  "group": "Movement"},
    "mounted": {"label": "When a soldier gets into a vehicle / gun",
                "params": [P("vehicle", "object", "vehicle (−1 = any)", -1)], "group": "Movement"},
    "dismounted": {"label": "When a soldier gets out of a vehicle / gun",
                   "params": [P("vehicle", "object", "vehicle (−1 = any)", -1)], "group": "Movement"},
    "door": {"label": "When a door opens / closes",
             "params": [P("door", "object", "door"),
                        P("state", "choice", "when it is", "3", choices={"1": "opening", "3": "open", "0": "closing",
                                                                         "2": "closed"})], "group": "Places"},
    "switch": {"label": "When a switch is used", "params": [P("switch", "switch", "switch")], "group": "Places"},
    "objective_done": {"label": "When an objective is completed", "params": [P("objective", "objective", "objective")],
                       "group": "Mission"},
}

CONDITIONS = {
    "alive": {"label": "is alive", "params": [P("object", "object", "object")]},
    "dead": {"label": "is dead / destroyed", "params": [P("object", "object", "object")]},
    "all_dead": {"label": "all of these are dead", "params": [P("objects", "objects", "objects")]},
    "any_alive": {"label": "any of these is alive", "params": [P("objects", "objects", "objects")]},
    "in_region": {"label": "is in a region", "params": [P("who", "choice", "who", "squad",
                                                          choices={"squad": "the squad", "player": "a player",
                                                                   "object": "this object"}),
                                                        P("object", "object", "object", -1, show_if={"who": "object"}),
                                                        P("region", "region", "region")]},
    "player_near": {"label": "a player is near", "params": [P("object", "object", "object"),
                                                            P("metres", "metres", "within (m)", 10)]},
    "near": {"label": "is near another object", "params": [P("object", "object", "object"), P("other", "object", "other"),
                                                           P("metres", "metres", "within (m)", 10)]},
    "flag": {"label": "flag is set", "params": [P("name", "flag", "flag")]},
    "counter": {"label": "counter compares", "params": [P("name", "counter", "counter"),
                                                        P("op", "choice", "is", ">=", choices={">=": "≥", "<=": "≤",
                                                                                              "==": "=", "~=": "≠",
                                                                                              ">": ">", "<": "<"}),
                                                        P("value", "number", "value", 1)]},
    "objective_done": {"label": "objective is completed", "params": [P("objective", "objective", "objective")]},
    "door_closed": {"label": "door is closed", "params": [P("door", "object", "door")]},
    "in_vehicle": {"label": "soldier is in a vehicle", "params": [P("soldier", "object", "soldier"),
                                                                 P("vehicle", "object", "vehicle")]},
    "has_item": {"label": "soldier has an item", "params": [P("soldier", "object", "soldier"), P("item", "object", "item")]},
    "path_finished": {"label": "unit has finished its path", "params": [P("unit", "object", "unit")]},
    "alert": {"label": "enemy alert level at least", "params": [P("object", "object", "enemy"),
                                                                P("level", "choice", "level", "4",
                                                                  choices={"1": "1 suspicious", "2": "2", "3": "3",
                                                                           "4": "4 combat"})]},
    "team_left": {"label": "team still has members", "params": [P("team", "team", "team", "2")]},
    "mission_time": {"label": "mission time is at least", "params": [P("seconds", "seconds", "seconds", 60)]},
    "chance": {"label": "random chance", "params": [P("percent", "percent", "chance (%)", 50)]},
    "difficulty": {"label": "difficulty", "params": [
        P("op", "choice", "is", "==", choices={"==": "is", ">=": "at least", "<=": "at most"}),
        P("level", "choice", "level", "medium", choices=DIFFICULTIES)],
        "help": "VERY EASY and VERY HARD need DesertStormFix 3.2+ (OPTIONS > DESERTSTORMFIX)"},
}

ACTIONS = {
    # objects and layers
    "activate": {"label": "Bring into play", "params": [P("objects", "objects", "objects")], "group": "Objects"},
    "deactivate": {"label": "Take out of play", "params": [P("objects", "objects", "objects")], "group": "Objects"},
    "respawn": {"label": "Respawn", "params": [P("objects", "objects", "objects")], "group": "Objects"},
    "show_layer": {"label": "Show a layer", "params": [P("layer", "layer", "layer")], "group": "Objects"},
    "hide_layer": {"label": "Hide a layer", "params": [P("layer", "layer", "layer")], "group": "Objects"},
    "kill": {"label": "Kill / destroy", "params": [P("objects", "objects", "objects"),
                                                  P("how", "choice", "how", "normal",
                                                    choices={"normal": "normal death",
                                                             "explosion": "blown up (gibs, large blood pool)"})],
             "group": "Objects",
             "help": "blown up: soldiers only (vehicles / props are destroyed normally); needs DesertStormFix 3.2+ - "
                     "older versions kill normally"},
    "invulnerable": {"label": "Make invulnerable", "params": [P("objects", "objects", "objects")], "group": "Objects"},
    "set_health": {"label": "Set health", "params": [P("objects", "objects", "objects"),
                                                     P("percent", "percent", "health (%)", 100)], "group": "Objects"},
    "teleport": {"label": "Teleport to a waypoint", "params": [P("objects", "objects", "objects"),
                                                              P("waypoint", "waypoint", "waypoint")], "group": "Objects"},
    # AI
    "patrol": {"label": "Walk a path", "params": [P("units", "objects", "units"), P("path", "path", "path"),
                                                  P("movement", "choice", "pace", "walk",
                                                    choices={"walk": "walk", "run": "run", "crouch": "crouched",
                                                             "prone": "crawl"}),
                                                  P("strict", "bool", "stay on the path (ignore distractions)", False),
                                                  P("unseen", "bool", "keep walking when the player can't see them",
                                                    True)],
               "group": "AI"},
    "stop": {"label": "Stop moving", "params": [P("units", "objects", "units")], "group": "AI"},
    "attack": {"label": "Attack a target", "params": [P("units", "objects", "units"), P("target", "object", "target")],
               "group": "AI"},
    "alert": {"label": "Set alert level", "params": [P("units", "objects", "units"),
                                                     P("level", "choice", "level", "4",
                                                       choices={"0": "0 calm", "1": "1", "2": "2", "3": "3",
                                                                "4": "4 combat"})], "group": "AI"},
    "team_alert": {"label": "Alert a whole team", "params": [P("team", "team", "team", "2"),
                                                             P("level", "choice", "level", "4",
                                                               choices={"0": "0 calm", "2": "2", "4": "4 combat"})],
                   "group": "AI"},
    "set_team": {"label": "Change team", "params": [P("units", "objects", "units"), P("team", "team", "team", "0")],
                 "group": "AI"},
    "follow": {"label": "Follow a leader", "params": [P("units", "objects", "followers"), P("leader", "object", "leader")],
               "group": "AI"},
    "ai_enabled": {"label": "Switch AI on / off", "params": [P("units", "objects", "units"), P("on", "bool", "on", True)],
                   "group": "AI"},
    "mount": {"label": "Get into a vehicle", "params": [P("units", "objects", "soldiers"), P("vehicle", "object", "vehicle")],
              "group": "AI"},
    "dismount": {"label": "Get out of the vehicle", "params": [P("units", "objects", "soldiers")], "group": "AI"},
    # mission
    "show_objective": {"label": "Show an objective", "params": [P("objective", "objective", "objective")],
                       "group": "Mission"},
    "complete_objective": {"label": "Complete an objective", "params": [P("objective", "objective", "objective")],
                           "group": "Mission"},
    "end_mission": {"label": "End the mission", "params": [P("result", "choice", "result", "win",
                                                             choices={"win": "success", "lose": "failure"})],
                    "group": "Mission"},
    "message": {"label": "Show a message", "params": [P("text", "text", "text"), P("seconds", "seconds", "for", 5)],
                "group": "Mission"},
    "set_flag": {"label": "Set a flag", "params": [P("name", "flag", "flag"), P("value", "bool", "to", True)],
                 "group": "Logic"},
    "add_counter": {"label": "Add to a counter", "params": [P("name", "counter", "counter"),
                                                            P("amount", "number", "add", 1)], "group": "Logic"},
    "set_counter": {"label": "Set a counter", "params": [P("name", "counter", "counter"), P("value", "number", "to", 0)],
                    "group": "Logic"},
    "wait": {"label": "Wait", "params": [P("seconds", "seconds", "seconds", 2)], "group": "Logic"},
    "run_rule": {"label": "Run another rule's actions", "params": [P("rule", "rule", "rule")], "group": "Logic"},
    # world and presentation
    "door_open": {"label": "Open / close a door", "params": [P("door", "object", "door"),
                                                            P("open", "bool", "open", True)], "group": "World"},
    "give_item": {"label": "Give an item", "params": [P("soldier", "object", "soldier"), P("item", "object", "item")],
                  "group": "World"},
    "player_control": {"label": "Player control on / off", "params": [P("on", "bool", "on", True)], "group": "World"},
    "fade": {"label": "Fade the screen", "params": [P("direction", "choice", "fade", "out",
                                                      choices={"out": "to black", "in": "back in"}),
                                                    P("seconds", "seconds", "over", 1)], "group": "World"},
    "camera_shake": {"label": "Shake the camera", "params": [P("strength", "number", "strength", 25),
                                                             P("seconds", "seconds", "for", 2)], "group": "World"},
    "birds": {"label": "Scare up birds", "params": [P("region", "region", "region"), P("count", "number", "birds", 8)],
              "group": "World"},
    # AI: whole-squad orders
    "hunt": {"label": "Hunt the squad", "params": [P("units", "objects", "units")], "group": "AI",
             "help": "combat alert, then attack player 1's soldier"},
    "hold": {"label": "Hold position", "params": [P("units", "objects", "units")], "group": "AI"},
    # cutscenes (camera n = the scene's n-th camera record; 0 = PLAYERVIEW, the player's own view)
    "cutscene_start": {"label": "Start a cutscene", "params": [P("letterbox", "bool", "letterbox bars", True),
                                                              P("viewfinder", "bool", "camcorder viewfinder", False),
                                                              P("control", "bool", "player keeps control", False)],
                       "group": "Cutscene"},
    "camera_shot": {"label": "Camera shot", "params": [P("camera", "camera", "camera"),
                                                       P("seconds", "seconds", "hold for", 4)], "group": "Cutscene"},
    "cutscene_end": {"label": "End the cutscene", "params": [], "group": "Cutscene"},
    "radio": {"label": "Radio line", "params": [P("file", "wav", "sound (.wav in the mod)"),
                                                P("text", "text", "subtitle", ""), P("seconds", "seconds", "show for", 4)],
              "group": "Cutscene"},
    # advanced
    "call": {"label": "Call a game function", "params": [P("slot", "slot", "function"), P("args", "args", "arguments", [])],
             "group": "Advanced"},
    "lua": {"label": "Run Lua", "params": [P("code", "lua", "Lua code")], "group": "Advanced"},
}

MSG = {"killed": "game.EVENT.KILLED", "destroyed": "game.EVENT.DESTROYED", "damaged": "game.EVENT.DAMAGED",
       "alerted": "game.EVENT.COMBAT", "spotted": "game.EVENT.SPOTTED", "picked_up": "game.EVENT.PICKED_UP",
       "dropped": "game.EVENT.DROPPED", "waypoint": "game.EVENT.WAYPOINT", "path_done": "game.EVENT.PATH_DONE",
       "mounted": "game.EVENT.MOUNTED", "dismounted": "game.EVENT.DISMOUNTED", "door": "game.EVENT.DOOR",
       "switch": "game.EVENT.SWITCH"}
POLLED = {"mission_start", "after_time", "every", "becomes_true"}


def catalog():
    return {"triggers": TRIGGERS, "conditions": CONDITIONS, "actions": ACTIONS, "teams": TEAMS}


# ---------------------------------------------------------------------------------------------- validation --
class Ctx:
    """What a rule may name in the open scene."""

    def __init__(self, s: Session):
        from logic import _regions, _script_ids, _switches, _waypoints
        self.ids = _script_ids(s)
        self.regions = set(_regions(s))
        self.waypoints = {w["id"]: w for w in _waypoints(s)}
        self.wp_names = {str(w["name"]).upper(): w["id"] for w in _waypoints(s)}
        self.switches = set(_switches(s))
        self.layers = set(getattr(s, "layers", {}).values())
        self.objectives = {o["id"] for o in getattr(s, "objectives", [])}
        self.cameras = [str(r["name"]) for r in s.env.records if r.kind.startswith("CAM") and r.fields]
        self.rule_count = 0
        from missionlogic import slots
        self.slots = slots()


def _num(v, what, lo=None, hi=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise CommandError(f"{what}: a number") from None
    if (lo is not None and f < lo) or (hi is not None and f > hi):
        raise CommandError(f"{what}: {lo} - {hi}")
    return int(f) if f.is_integer() else f


def _name(v, what):
    v = str(v or "").strip()
    if not v or len(v) > 40 or not all(c.isalnum() or c in "_- " for c in v):
        raise CommandError(f"{what}: a short name (letters, digits, _ - and spaces)")
    return v


def _check_value(ctx: Ctx, p, v, where):
    t, what = p["type"], f"{where}: {p['label']}"
    if v is None:
        if p["default"] is None:
            raise CommandError(f"{what} is missing")
        v = p["default"]
    if t == "object":
        v = int(_num(v, what))
        if v != -1 and v not in ctx.ids:
            raise CommandError(f"{what}: no object #{v} in this scene")
        if v == -1 and p["default"] != -1:
            raise CommandError(f"{what}: pick an object")
        return v
    if t == "objects":
        vals = v if isinstance(v, list) else [v]
        out = []
        for x in vals:
            x = int(_num(x, what))
            if x not in ctx.ids:
                raise CommandError(f"{what}: no object #{x} in this scene")
            if x not in out:
                out.append(x)
        if not out and p["default"] is None:
            raise CommandError(f"{what}: pick at least one object")
        return out
    if t == "region":
        if str(v) not in ctx.regions:
            raise CommandError(f"{what}: no region {v!r} in this scene")
        return str(v)
    if t in ("waypoint", "path"):
        if isinstance(v, str) and not v.lstrip("-").isdigit():
            if v.upper() not in ctx.wp_names:
                raise CommandError(f"{what}: no waypoint {v!r} in this scene")
            v = ctx.wp_names[v.upper()]
        v = int(_num(v, what))
        if v not in ctx.waypoints:
            raise CommandError(f"{what}: no waypoint {v} in this scene")
        return v
    if t == "switch":
        if str(v) not in ctx.switches:
            raise CommandError(f"{what}: no switch {v!r} in this scene")
        return str(v)
    if t == "layer":
        if str(v) not in ctx.layers:
            raise CommandError(f"{what}: no layer {v!r} (put objects in a layer first)")
        return str(v)
    if t == "objective":
        v = int(_num(v, what))
        if v not in ctx.objectives:
            raise CommandError(f"{what}: no objective {v} (add it in the Objectives tab)")
        return v
    if t == "text":
        from logic import _clean_text
        if not v and p["default"] == "":
            return ""
        return _clean_text(v, what)
    if t in ("seconds",):
        return _num(v, what, 0, 36000)
    if t == "metres":
        return _num(v, what, 0, 10000)
    if t == "percent":
        return _num(v, what, 0, 100)
    if t == "number":
        return _num(v, what, -1e9, 1e9)
    if t == "team":
        return str(int(_num(v, what, 0, 15)))
    if t in ("flag", "counter"):
        return _name(v, what)
    if t == "bool":
        return bool(v)
    if t == "choice":
        if str(v) not in p["choices"]:
            raise CommandError(f"{what}: one of {', '.join(p['choices'])}")
        return str(v)
    if t == "rule":
        v = int(_num(v, what))
        return v
    if t == "slot":
        v = int(str(v), 16) if isinstance(v, str) and v.lower().startswith("0x") else int(_num(v, what))
        if v not in ctx.slots:
            raise CommandError(f"{what}: unknown game function 0x{v:X}")
        return v
    if t == "args":
        if not isinstance(v, list) or len(v) > 12:
            raise CommandError(f"{what}: a list of up to 12 values")
        out = []
        for x in v:
            if isinstance(x, bool) or isinstance(x, (int, float)):
                out.append(x)
            elif isinstance(x, str) and len(x) < 200:
                out.append(x)
            else:
                raise CommandError(f"{what}: numbers or texts")
        return out
    if t == "camera":
        if str(v) not in ctx.cameras:
            raise CommandError(f"{what}: no camera {v!r} in this scene")
        return str(v)
    if t == "wav":
        v = str(v or "").strip()
        if not v.lower().endswith(".wav") or any(c in v for c in '\\/:*?"<>|') or len(v) > 60:
            raise CommandError(f"{what}: a .wav file name in the mission's mod folder (upload it with Assets…)")
        return v
    if t == "lua":
        code = str(v)
        if len(code) > 4000:
            raise CommandError(f"{what}: up to 4000 characters")
        return code
    raise CommandError(f"{what}: unknown parameter type {t}")


def _check_item(ctx, spec_table, item, where):
    if not isinstance(item, dict) or item.get("type") not in spec_table:
        raise CommandError(f"{where}: type is one of {', '.join(spec_table)}")
    spec = spec_table[item["type"]]
    out = {"type": item["type"]}
    for p in spec["params"]:
        out[p["name"]] = _check_value(ctx, p, item.get(p["name"]), where)
    if "not" in item:
        out["not"] = bool(item["not"])
    return out


def upgrade(r: dict) -> dict:
    """An old rule ({trigger: {type, target}, actions: [{type, target}]}) in the new form."""
    if "conditions" in r or "name" in r:
        return r
    t = dict(r.get("trigger") or {})
    kind, target = t.get("type"), t.get("target")
    trig = {"region_enter": {"type": "region_enter", "region": target, "who": "player", "object": -1},
            "region_leave": {"type": "region_leave", "region": target, "who": "player", "object": -1},
            "killed": {"type": "killed", "objects": [target] if target is not None else [], "mode": "any"},
            "timer": {"type": "after_time", "seconds": target},
            "switch": {"type": "switch", "switch": target}}.get(kind, {"type": kind})
    acts = []
    for a in r.get("actions") or []:
        at, tg = a.get("type"), a.get("target")
        acts.append({"show_layer": {"type": "show_layer", "layer": tg}, "hide_layer": {"type": "hide_layer", "layer": tg},
                     "show_objective": {"type": "show_objective", "objective": tg},
                     "complete_objective": {"type": "complete_objective", "objective": tg},
                     "end_mission": {"type": "end_mission", "result": "win" if tg == "win" else "lose"}}.get(
            at, {"type": at}))
    return {"name": "", "enabled": True, "once": bool(r.get("once", True)), "trigger": trig, "conditions": [],
            "actions": acts}


def check_rule(s: Session, r, index=None):
    ctx = Ctx(s)
    if not isinstance(r, dict):
        raise CommandError("a rule is {name, trigger, conditions, actions, once, enabled}")
    r = upgrade(r)
    where = f"rule {index + 1}" if index is not None else "rule"
    name = str(r.get("name") or "").strip()[:80]
    trig = _check_item(ctx, TRIGGERS, r.get("trigger"), f"{where} trigger")
    if trig["type"] in ("region_enter", "region_leave") and trig.get("who") == "object" and trig.get("object", -1) == -1:
        raise CommandError(f"{where} trigger: pick the object")
    conds = [_check_item(ctx, CONDITIONS, c, f"{where} condition {k + 1}") for k, c in enumerate(r.get("conditions") or [])]
    for k, c in enumerate(conds):
        if c["type"] == "in_region" and c["who"] == "object" and c["object"] == -1:
            raise CommandError(f"{where} condition {k + 1}: pick the object")
    if trig["type"] == "becomes_true" and not conds:
        raise CommandError(f"{where}: 'when the conditions become true' needs at least one condition")
    acts = [_check_item(ctx, ACTIONS, a, f"{where} action {k + 1}") for k, a in enumerate(r.get("actions") or [])]
    if not acts:
        raise CommandError(f"{where}: add at least one action")
    return {"name": name, "enabled": bool(r.get("enabled", True)), "once": bool(r.get("once", True)),
            "trigger": trig, "conditions": conds, "actions": acts}


# ---------------------------------------------------------------------------------------------- describing --
def _obj(ctx, i):
    r = ctx.ids.get(i) if ctx else None
    return f"{r['unit']} #{i}" if r is not None else f"#{i}"


def _objs(ctx, ids):
    if not ids:
        return "anything"
    if len(ids) > 3:
        return f"{len(ids)} objects"
    return ", ".join(_obj(ctx, i) for i in ids)


def _wp(ctx, i):
    w = ctx.waypoints.get(i) if ctx else None
    return str(w["name"]) if w else f"waypoint {i}"


def describe_trigger(t, ctx=None):
    k = t["type"]
    if k == "mission_start":
        return "the mission starts"
    if k == "after_time":
        return f"{t['seconds']:g} s into the mission"
    if k == "every":
        return f"every {t['seconds']:g} s"
    if k == "becomes_true":
        return "the conditions become true"
    if k in ("region_enter", "region_leave"):
        who = {"player": "a player", "squad": "the whole squad"}.get(t.get("who"), None) or _obj(ctx, t.get("object"))
        return f"{who} {'enters' if k == 'region_enter' else 'leaves'} {t['region']}"
    if k == "killed":
        ids = t.get("objects") or []
        return ("all of " if t.get("mode") == "all" and len(ids) > 1 else "") + f"{_objs(ctx, ids)} is killed"
    if k in ("damaged", "alerted", "spotted"):
        verb = {"damaged": "is damaged", "alerted": "goes on combat alert", "spotted": "spots someone"}[k]
        return f"{_objs(ctx, t.get('objects') or [])} {verb}" if t.get("objects") else f"an enemy {verb}"
    if k in ("picked_up", "dropped"):
        return f"{'an item' if t['item'] == -1 else _obj(ctx, t['item'])} is {'picked up' if k == 'picked_up' else 'dropped'}"
    if k == "waypoint":
        return f"{'a unit' if t['unit'] == -1 else _obj(ctx, t['unit'])} reaches {_wp(ctx, t['waypoint'])}"
    if k == "path_done":
        return f"{'a unit' if t['unit'] == -1 else _obj(ctx, t['unit'])} finishes its path"
    if k in ("mounted", "dismounted"):
        v = "a vehicle" if t["vehicle"] == -1 else _obj(ctx, t["vehicle"])
        return f"someone gets {'into' if k == 'mounted' else 'out of'} {v}"
    if k == "door":
        return f"{_obj(ctx, t['door'])} is {TRIGGERS['door']['params'][1]['choices'][t['state']]}"
    if k == "switch":
        return f"switch {t['switch']} is used"
    if k == "objective_done":
        return f"objective {t['objective']} is completed"
    return k


def describe_condition(c, ctx=None):
    k = c["type"]
    neg = c.get("not")
    if k == "alive":
        s = f"{_obj(ctx, c['object'])} is alive"
    elif k == "dead":
        s = f"{_obj(ctx, c['object'])} is dead"
    elif k == "all_dead":
        s = f"all of {_objs(ctx, c['objects'])} are dead"
    elif k == "any_alive":
        s = f"any of {_objs(ctx, c['objects'])} is alive"
    elif k == "in_region":
        who = {"squad": "the squad", "player": "a player"}.get(c["who"]) or _obj(ctx, c["object"])
        s = f"{who} is in {c['region']}"
    elif k == "player_near":
        s = f"a player is within {c['metres']:g} m of {_obj(ctx, c['object'])}"
    elif k == "near":
        s = f"{_obj(ctx, c['object'])} is within {c['metres']:g} m of {_obj(ctx, c['other'])}"
    elif k == "flag":
        s = f"flag “{c['name']}” is set"
    elif k == "counter":
        s = f"counter “{c['name']}” {CONDITIONS['counter']['params'][1]['choices'][c['op']]} {c['value']:g}"
    elif k == "objective_done":
        s = f"objective {c['objective']} is completed"
    elif k == "door_closed":
        s = f"{_obj(ctx, c['door'])} is closed"
    elif k == "in_vehicle":
        s = f"{_obj(ctx, c['soldier'])} is in {_obj(ctx, c['vehicle'])}"
    elif k == "has_item":
        s = f"{_obj(ctx, c['soldier'])} has {_obj(ctx, c['item'])}"
    elif k == "path_finished":
        s = f"{_obj(ctx, c['unit'])} has finished its path"
    elif k == "alert":
        s = f"{_obj(ctx, c['object'])} is at alert {c['level']}+"
    elif k == "team_left":
        s = f"{TEAMS.get(c['team'], 'team ' + c['team'])} has members left"
    elif k == "mission_time":
        s = f"{c['seconds']:g} s have passed"
    elif k == "chance":
        s = f"a {c['percent']:g}% chance"
    elif k == "difficulty":
        op = CONDITIONS["difficulty"]["params"][0]["choices"][c["op"]]
        s = f"difficulty {op} {DIFFICULTIES.get(c['level'], c['level'])}"
    else:
        s = k
    return ("not: " + s) if neg else s


def describe_action(a, ctx=None):
    k = a["type"]
    spec = ACTIONS.get(k, {"label": k})
    if k == "kill" and a.get("how") == "explosion":
        return f"Blow up: {_objs(ctx, a['objects'])}"
    if k in ("activate", "deactivate", "respawn", "kill", "invulnerable"):
        return f"{spec['label']}: {_objs(ctx, a['objects'])}"
    if k == "patrol":
        return f"{_objs(ctx, a['units'])} {a['movement']} the path from {_wp(ctx, a['path'])}"
    if k == "wait":
        return f"wait {a['seconds']:g} s"
    if k in ("show_layer", "hide_layer"):
        return f"{spec['label'].lower()} “{a['layer']}”"
    if k in ("show_objective", "complete_objective"):
        return f"{spec['label'].lower()} {a['objective']}"
    if k == "end_mission":
        return "mission " + ("success" if a["result"] == "win" else "failure")
    if k == "message":
        return f"show “{a['text']}”"
    if k == "set_flag":
        return f"{'set' if a['value'] else 'clear'} flag “{a['name']}”"
    if k == "add_counter":
        return f"add {a['amount']:g} to counter “{a['name']}”"
    if k == "set_counter":
        return f"counter “{a['name']}” = {a['value']:g}"
    if k == "attack":
        return f"{_objs(ctx, a['units'])} attack {_obj(ctx, a['target'])}"
    if k in ("hunt", "hold"):
        return f"{_objs(ctx, a['units'])} {'hunt the squad' if k == 'hunt' else 'hold position'}"
    if k == "camera_shot":
        return f"camera {a['camera']} for {a['seconds']:g} s"
    if k == "radio":
        return f"radio {a['file']}" + (f" “{a['text']}”" if a.get("text") else "")
    if k == "call":
        from missionlogic import slots
        return f"call {slots().get(a['slot'], {}).get('name', hex(a['slot']))}({', '.join(map(str, a['args']))})"
    if k == "lua":
        return "run Lua: " + a["code"].strip().splitlines()[0][:60] if a["code"].strip() else "run Lua"
    return spec["label"].lower()


def describe(r, ctx=None):
    t = describe_trigger(r["trigger"], ctx)
    c = " and ".join(describe_condition(x, ctx) for x in r["conditions"])
    a = ", then ".join(describe_action(x, ctx) for x in r["actions"])
    return f"When {t}{', if ' + c if c else ''}: {a}"


# --------------------------------------------------------------------------------------------------- Lua --
def _lua_list(ids):
    return "{" + ", ".join(str(int(i)) for i in ids) + "}"


def _f(x):
    """A Lua float literal (game.call passes Lua floats as float bits)."""
    return f"{float(x):.4f}"


def _cond_lua(c):
    k = c["type"]
    if k == "alive":
        e = f"editor.alive({c['object']})"
    elif k == "dead":
        e = f"not editor.alive({c['object']})"
    elif k == "all_dead":
        e = f"editor.all_dead({_lua_list(c['objects'])})"
    elif k == "any_alive":
        e = f"not editor.all_dead({_lua_list(c['objects'])})"
    elif k == "in_region":
        if c["who"] == "squad":
            e = f"game.call(0x3D0, editor.region_index({_lua_value(c['region'])})) ~= 0"
        elif c["who"] == "player":
            e = f"editor.player_in({_lua_value(c['region'])})"
        else:
            e = f"game.call(0x15C, {c['object']}, editor.region_index({_lua_value(c['region'])})) ~= 0"
    elif k == "player_near":
        e = f"game.call(0x3B4, {c['object']}, {_f(c['metres'] * 100)}) ~= 0"
    elif k == "near":
        e = f"game.call(0x140, {c['object']}, {c['other']}, {_f(c['metres'] * 100)}) ~= 0"
    elif k == "flag":
        e = f"editor.vars()[{_lua_value(c['name'])}] == true"
    elif k == "counter":
        e = f"(editor.vars()[{_lua_value(c['name'])}] or 0) {c['op']} {c['value']}"
    elif k == "objective_done":
        e = f"editor.state().done[{c['objective']}] == true"
    elif k == "door_closed":
        e = f"game.call(0x438, {c['door']}) ~= 0"
    elif k == "in_vehicle":
        e = f"game.call(0x250, {c['soldier']}, {c['vehicle']}) ~= 0"
    elif k == "has_item":
        e = f"game.call(0x5C0, {c['soldier']}, {c['item']}) ~= 0"
    elif k == "path_finished":
        e = f"game.call(0x0F8, {c['unit']}) ~= 0"
    elif k == "alert":
        e = f"game.call(0x1EC, {c['object']}) >= {int(c['level'])}"
    elif k == "team_left":
        e = f"game.call(0x420, {int(c['team'])}) ~= 0"
    elif k == "mission_time":
        e = f"editor.mission_time() >= {c['seconds']}"
    elif k == "chance":
        e = f"math.random() * 100 < {c['percent']}"   # online: every PC's math.random is seeded alike (the patch)
    elif k == "difficulty":
        order = list(DIFFICULTIES)
        e = f"{DIFFICULTY_RANK_LUA} {c['op']} {order.index(c['level']) if c['level'] in order else 2}"
    else:
        e = "true"
    return f"not ({e})" if c.get("not") else e


def _each(ids, body):
    return f"for _, o in ipairs({_lua_list(ids)}) do {body} end"


def _action_lua(a, index_of_rule) -> str:
    k = a["type"]
    if k == "activate":
        return _each(a["objects"], "game.call(0x0C4, o)")
    if k == "deactivate":
        return _each(a["objects"], "game.call(0x0C8, o)")
    if k == "respawn":
        return _each(a["objects"], "game.call(0x0DC, o)")
    if k == "show_layer":
        return f"game.show_layer({_lua_value(a['layer'])})"
    if k == "hide_layer":
        return f"game.hide_layer({_lua_value(a['layer'])})"
    if k == "kill":
        # awake first: an unseen (sleeping) soldier's death only completes when the game updates him
        awake = "if game.keep_awake then game.keep_awake(o, true) end "
        if a.get("how") == "explosion":   # game.explode: the explosion death (gibs); not a soldier / older patch -> normal
            return _each(a["objects"], awake + "if not (game.explode and game.explode(o)) then game.call(0x0FC, o, -1) end")
        return _each(a["objects"], awake + "game.call(0x0FC, o, -1)")
    if k == "invulnerable":
        return _each(a["objects"], "game.call(0x5F8, o)")
    if k == "set_health":
        return _each(a["objects"], f"game.call(0x1A4, o, {_f(a['percent'] / 100)})")
    if k == "teleport":
        return _each(a["objects"], f"game.call(0x50C, o, {a['waypoint']})")
    if k == "patrol":
        stance = {"walk": "0x208", "run": "0x20C", "crouch": "0x210", "prone": "0x218"}[a["movement"]]
        strict = "game.call(0x198, o, 1) game.call(0x1DC, o, 1) " if a.get("strict") else ""
        # the game puts enemy soldiers the player hasn't seen to sleep - their route would wait for the player
        awake = "if game.keep_awake then game.keep_awake(o, true) end " if a.get("unseen", True) else ""
        return _each(a["units"], f"{awake}game.call(0x184, o, 0) game.call(0x198, o, 0) game.call(0x110, o, {a['path']}) "
                                 f"game.call(0x114, o, 0) game.call({stance}, o) {strict}")
    if k == "stop":
        return _each(a["units"], "game.call(0x110, o, -1) game.call(0x204, o)")
    if k == "attack":
        return _each(a["units"], f"game.call(0x11C, o, {a['target']}) game.call(0x63C, o, {a['target']})")
    if k == "alert":
        return _each(a["units"], f"game.call(0x1E8, o, {int(a['level'])})")
    if k == "team_alert":
        return f"game.call(0x400, {int(a['team'])}, {int(a['level'])})"
    if k == "set_team":
        return _each(a["units"], f"game.call(0x0E0, o, {int(a['team'])})")
    if k == "follow":
        return _each(a["units"], f"game.call(0x120, o, {a['leader']})")
    if k == "ai_enabled":
        return _each(a["units"], f"game.call(0x180, o, {1 if a['on'] else 0})")
    if k == "mount":
        return _each(a["units"], f"game.call(0x24C, o, {a['vehicle']})")
    if k == "dismount":
        return _each(a["units"], "game.call(0x650, o)")
    if k == "show_objective":
        return f"editor.show({a['objective']})"
    if k == "complete_objective":
        return f"editor.complete({a['objective']})"
    if k == "end_mission":
        return f"game.end_mission({2 if a['result'] == 'win' else 1})"
    if k == "message":
        return f"game.popup({_lua_text(a['text'])}, {a['seconds']})"
    if k == "set_flag":
        return f"editor.set_var({_lua_value(a['name'])}, {'true' if a['value'] else 'false'})"
    if k == "add_counter":
        return f"editor.set_var({_lua_value(a['name'])}, (editor.vars()[{_lua_value(a['name'])}] or 0) + {a['amount']})"
    if k == "set_counter":
        return f"editor.set_var({_lua_value(a['name'])}, {a['value']})"
    if k == "run_rule":
        return f"editor.start_rule({int(a['rule'])}, true)"
    if k == "door_open":
        return f"game.call(0x43C, {a['door']}, {_f(1 if a['open'] else 0)})"
    if k == "give_item":
        return f"game.call(0x5B0, {a['soldier']}, {a['item']})"
    if k == "player_control":
        return f"game.call(0x51C, {1 if a['on'] else 0})"
    if k == "fade":
        return f"game.call({'0x540' if a['direction'] == 'out' else '0x53C'}, {_f(a['seconds'] * 1000)}, 0, 0, 0)"
    if k == "camera_shake":
        return f"game.call(0x32C, {_f(a['strength'])}, 0.0, {_f(a['seconds'] * 1000)})"
    if k == "birds":
        return f"game.call(0x074, editor.region_index({_lua_value(a['region'])}), {int(a['count'])})"
    if k == "call":
        args = []
        for x in a["args"]:
            if isinstance(x, bool):
                args.append("1" if x else "0")
            elif isinstance(x, float) and not x.is_integer():
                args.append(_f(x))
            elif isinstance(x, (int, float)):
                args.append(str(int(x)))
            else:
                args.append(_lua_value(str(x)))
        return f"game.call(0x{a['slot']:03X}{''.join(', ' + x for x in args)})"
    if k == "lua":
        return "do\n" + a["code"] + "\nend"
    if k == "hunt":
        # awake: hunters far from the player would otherwise sleep (unseen enemies); 0x184 off: a "Hold position"
        # before made them ignore order changes
        return _each(a["units"], "if game.keep_awake then game.keep_awake(o, true) end game.call(0x184, o, 0) "
                                 "local t = game.call(0x3BC, 0) game.call(0x1E8, o, 4) "
                                 "if t >= 0 then game.call(0x11C, o, t) game.call(0x63C, o, t) end")
    if k == "hold":
        return _each(a["units"], "game.call(0x110, o, -1) game.call(0x204, o) game.call(0x184, o, 1)")
    if k == "cutscene_start":
        return ("game.call(0x594) " + ("game.call(0x530) " if a["letterbox"] else "")
                + ("game.call(0x7F4) " if a["viewfinder"] else "") + ("" if a["control"] else "game.call(0x51C, 0)"))
    if k == "camera_shot":
        return f"game.call(0x4FC, editor.camera_index({_lua_value(a['camera'])}))"
    if k == "cutscene_end":
        return ("game.call(0x4FC, 0) game.call(0x4F4) game.call(0x534) game.call(0x7F8) game.call(0x51C, 1) "
                "game.call(0x598)")
    if k == "radio":
        txt = f" game.popup({_lua_text(a['text'])}, {a['seconds']})" if a.get("text") else ""
        return f"game.music({_lua_value(a['file'])}){txt}"
    return f"-- (unknown action {k})"


def _lua_text(v):
    from logic import _lua_text as lt
    return lt(v)


def _trigger_match(t, n) -> str | None:
    """The condition on (id, a, b, c) for a message trigger; None for polled triggers."""
    k = t["type"]

    def any_of(var, ids):
        return f"editor.has({_lua_list(ids)}, {var})" if ids else "true"
    if k == "killed":
        return f"(id == {MSG['killed']} or id == {MSG['destroyed']}) and {any_of('a', t['objects'])}"
    if k in ("damaged", "alerted"):
        return f"id == {MSG[k]} and {any_of('a', t['objects'])}"
    if k == "spotted":
        return f"id == {MSG['spotted']} and {any_of('a', t['objects'])}"
    if k in ("picked_up", "dropped"):
        return f"id == {MSG[k]}" + (f" and b == {t['item']}" if t["item"] != -1 else "")
    if k == "waypoint":
        return f"id == {MSG['waypoint']} and b == {t['waypoint']}" + (f" and a == {t['unit']}" if t["unit"] != -1 else "")
    if k == "path_done":
        return f"id == {MSG['path_done']}" + (f" and a == {t['unit']}" if t["unit"] != -1 else "")
    if k in ("mounted", "dismounted"):
        return f"id == {MSG[k]}" + (f" and b == {t['vehicle']}" if t["vehicle"] != -1 else "")
    if k == "door":
        return f"id == {MSG['door']} and a == {t['door']} and b == {int(t['state'])}"
    if k == "switch":
        from logic import _switch_hash
        return f"id == {MSG['switch']} and a == {_switch_hash(t['switch'])}"
    if k in ("region_enter", "region_leave") and t.get("who") == "player":
        ev = "game.EVENT.ENTERED" if k == "region_enter" else "game.EVENT.LEFT"
        return f"id == {ev} and a == {_lua_value(t['region'])}"
    if k == "objective_done":
        return f"id == editor.OBJECTIVE_DONE and a == {t['objective']}"
    return None


def rules_lua(rules, regions, waits_for_start=False, cameras=()) -> list[str]:
    """The rules as Lua: the interpreter, then editor.rules = {...}."""
    L = [
        "editor.camera_names = { " + ", ".join(_lua_value(c) for c in cameras) + " }   -- camera n = the n-th CAM record",
        "function editor.camera_index(name)",
        "    for i, n in ipairs(editor.camera_names) do if n == name then return i - 1 end end",
        "    return 0",
        "end",
        "editor.OBJECTIVE_DONE = 0x7001   -- the editor's own event: an objective completed (a = its number)",
        "editor.region_names = { " + ", ".join(_lua_value(r) for r in regions) + " }   -- scene order = the game's index",
        "function editor.region_index(name)",
        "    for i, n in ipairs(editor.region_names) do if n == name then return i - 1 end end",
        "    return -1",
        "end",
        "function editor.vars() local st = editor.state(); st.vars = st.vars or {}; return st.vars end",
        "function editor.set_var(name, v) editor.vars()[name] = v end",
        "function editor.has(list, x) for _, v in ipairs(list) do if v == x then return true end end return false end",
        "-- a soldier at 0 health is dead even if the game hasn't finished his death yet: an enemy the player hasn't seen",
        "-- is asleep (no updates), and his death only completes when he's updated",
        "function editor.alive(o)",
        "    local s = game.object(o)",
        "    if s == 0 or game.call(0x100, o) ~= 0 then return false end",
        "    if game.read32(s + 4) == 1 and (game.readf(s + 0x54) or 1) <= 0 then return false end",
        "    return true",
        "end",
        "function editor.all_dead(list) for _, o in ipairs(list) do if editor.alive(o) then return false end end return true end",
        "function editor.player_in(region) return game.player_in_region and game.player_in_region(region) or false end",
        "function editor.mission_time() local st = editor.state(); return (game.time_ms() - (st.t0 or game.time_ms())) / 1000 end",
        "-- the interpreter: a rule's steps run in order; a number is a wait in seconds. A wait is a game timer (its",
        "-- message comes through the game's event queue - the script's update() isn't called while the player has no",
        "-- control, e.g. in a cutscene) and is kept in game.state, so a loaded save re-arms it and carries on.",
        "editor.TIMER_BASE, editor.TIMER_SPAN = 9200, 700",
        "function editor.arm(p, seconds)",
        "    local st = editor.state()",
        "    st.next_timer = ((st.next_timer or 0) % editor.TIMER_SPAN) + 1",
        "    p[4] = editor.TIMER_BASE + st.next_timer",
        "    game.timer(p[4], math.max(0.05, seconds))",
        "end",
        "function editor.run_steps(n, k)",
        "    local r = editor.rules[n]",
        "    while r and k <= #r.steps do",
        "        local step = r.steps[k]",
        "        if type(step) == 'number' then",
        "            local p = { n, k + 1, game.time_ms() + step * 1000 }",
        "            table.insert(editor.state().pending, p)",
        "            editor.arm(p, step)",
        "            return",
        "        end",
        "        local ok, err = pcall(step)",
        "        if not ok then print('editor rule ' .. n .. ' step ' .. k .. ': ' .. tostring(err)) end",
        "        k = k + 1",
        "    end",
        "end",
        "function editor.start_rule(n, forced)",
        "    local r, st = editor.rules[n], editor.state()",
        "    if not r or (not forced and (not r.on or (r.once and st.fired[n]))) then return end",
        "    if not forced and r.cond and not r.cond() then return end",
        "    if not forced then st.fired[n] = true end",
        "    editor.run_steps(n, 1)",
        "end",
        "function editor.run_due(match_timer)   -- pending waits that are over (or whose timer fired)",
        "    local st, now = editor.state(), game.time_ms()",
        "    st.pending = st.pending or {}",
        "    for i = #st.pending, 1, -1 do",
        "        local p = st.pending[i]",
        "        if p and (now >= p[3] or (match_timer and p[4] == match_timer)) then",
        "            table.remove(st.pending, i)",
        "            editor.run_steps(p[1], p[2])",
        "        end",
        "    end",
        "end",
        "function editor.rules_on_message(id, a, b, c)",
        "    if id == 0x12 and editor.started then editor.play_begins() end   -- game.EVENT.START",
        "    if id == game.EVENT.TIMER and type(a) == 'number' and a > editor.TIMER_BASE",
        "            and a <= editor.TIMER_BASE + editor.TIMER_SPAN then editor.run_due(a) return end",
        "    for n, r in ipairs(editor.rules) do",
        "        if r.match and r.match(id, a, b, c) then",
        "            if r.all and not editor.all_dead(r.all) then else editor.start_rule(n) end",
        "        end",
        "    end",
        "end",
        "function editor_update()   -- called from update(): waits, timed rules, conditions becoming true",
        "    if not editor.started then return end",
        "    local st, now = editor.state(), game.time_ms()",
        "    editor.run_due(nil)   -- backup for the timers",
        "    editor.tick = (editor.tick or 0) + 1",
        "    if editor.tick % 15 ~= 0 then return end   -- polled triggers 4x a second",
        "    local t = editor.mission_time()",
        "    if not editor.playing then",
        "        if t > 300 then editor.play_begins() else return end   -- no start message in 5 min: play anyway",
        "    end",
        "    editor.patrol_check(now)",
        "    st.edge = st.edge or {}",
        "    st.last = st.last or {}",
        "    for n, r in ipairs(editor.rules) do",
        "        if r.on then",
        "            if r.after and t >= r.after and not st.fired[n] then editor.start_rule(n) end",
        "            if r.every and t - (st.last[n] or 0) >= r.every then st.last[n] = t; editor.start_rule(n) end",
        "            if r.poll then",
        "                local v = r.poll() and true or false",
        "                if v and not st.edge[n] then editor.start_rule(n) end",
        "                st.edge[n] = v",
        "            end",
        "        end",
        "    end",
        "end",
        "function editor.rules_start(fresh)   -- after the objectives are up (fresh = a new mission, not a loaded save)",
        "    local st = editor.state()",
        "    st.pending, st.vars = st.pending or {}, st.vars or {}",
        "    if fresh or not st.t0 then st.t0 = game.time_ms() end",
        "    editor.started = true",
        "    if not fresh then   -- a loaded save is already in play; its waits get new timers",
        "        editor.playing = true",
        "        local now = game.time_ms()",
        "        for _, p in ipairs(st.pending) do editor.arm(p, math.max(0.05, (p[3] - now) / 1000)) end",
        "    end",
        f"    if fresh and not {'true' if waits_for_start else 'false'} then editor.play_begins() end",
        "end",
        "-- play begins: after the intro film and the overview on levels made from a campaign mission (game message",
        "-- 0x12, sent when the overview ends), at once otherwise. Mission-start rules and patrols start then -",
        "-- started during the intro, the game resets the soldiers' orders when the film ends.",
        "function editor.play_begins()",
        "    if editor.playing then return end",
        "    editor.playing = true",
        "    if editor.start_patrols then editor.start_patrols() end",
        "    for n, r in ipairs(editor.rules) do if r.at_start then editor.start_rule(n) end end",
        "end",
        "-- patrols keep being given until the soldier is on the route (current waypoint set): a few frames after the",
        "-- film the game may still be handing out its own orders",
        "editor.patrol_retry = {}",
        "function editor.patrol_check(now)",
        "    for o, p in pairs(editor.patrol_retry) do",
        "        if game.call(0x10C, o) ~= -1 or p.tries >= 15 then editor.patrol_retry[o] = nil",
        "        elseif now - p.last >= 2000 then",
        "            p.tries, p.last = p.tries + 1, now",
        "            editor.patrol(o, p.chain, p.loop, p.stance, true, p.unseen)",
        "        end",
        "    end",
        "end",
        "editor.rules = {",
    ]
    for n, r in enumerate(rules, 1):
        t = r["trigger"]
        L.append(f"    -- rule {n}{': ' + r['name'] if r.get('name') else ''} - {describe(r)}")
        L.append("    {")
        L.append(f"        on = {'true' if r.get('enabled', True) else 'false'}, once = {'true' if r['once'] else 'false'},")
        match = _trigger_match(t, n)
        k = t["type"]
        if match:
            L.append(f"        match = function(id, a, b, c) return {match} end,")
            if k == "killed" and t.get("mode") == "all" and t.get("objects"):
                L.append(f"        all = {_lua_list(t['objects'])},")
        elif k == "mission_start":
            L.append("        at_start = true,")
        elif k == "after_time":
            L.append(f"        after = {t['seconds']},")
        elif k == "every":
            L.append(f"        every = {t['seconds']},")
        if k == "becomes_true":
            L.append(f"        poll = function() return {' and '.join('(' + _cond_lua(c) + ')' for c in r['conditions'])} end,")
        elif k in ("region_enter", "region_leave") and t.get("who") in ("object", "squad"):
            inside = (f"game.call(0x3D0, editor.region_index({_lua_value(t['region'])})) ~= 0" if t["who"] == "squad"
                      else f"game.call(0x15C, {t['object']}, editor.region_index({_lua_value(t['region'])})) ~= 0")
            L.append(f"        poll = function() return {'(' + inside + ')' if k == 'region_enter' else 'not (' + inside + ')'} end,")
        if r["conditions"] and k != "becomes_true":
            L.append(f"        cond = function() return {' and '.join('(' + _cond_lua(c) + ')' for c in r['conditions'])} end,")
        L.append("        steps = {")
        for a in r["actions"]:
            if a["type"] == "wait":
                L.append(f"            {a['seconds']},   -- wait")
            elif a["type"] == "camera_shot":
                L.append(f"            function() {_action_lua(a, n)} end,   -- {describe_action(a)}")
                L.append(f"            {a['seconds']},   -- hold the shot")
            else:
                body = _action_lua(a, n).replace("\n", "\n                ")
                L.append(f"            function() {body} end,   -- {describe_action(a)}")
        L.append("        },")
        L.append("    },")
    L.append("}")
    return L
