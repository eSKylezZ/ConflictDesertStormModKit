"""Editor commands for visual mission logic and scene dressing: objectives, event rules ("when ... then ..."), soldier
AI profiles and patrol routes, the scene's atmosphere (sun, fog, colour keys, time of day) and cameras.

Objectives, rules and patrols live in the session and in <NAME>.editor.json; save_mission / save_script compile them
into two blocks of the mission's Lua script, rewritten on every save like the layers block:

  -- @editor begin: objectives ... -- @editor end: objectives   (adding, showing, completing objectives)
  -- @editor begin: rules ...      -- @editor end: rules        (editor_init / editor_on_message + the rules)

The script's own init() and on_message() call editor_init() / editor_on_message(id, a, b) (the editor adds those two
lines when the script lacks them). Everything else in the script stays the modder's.

The AI profiles copy the field values of shipped soldiers whose names say what they do (TENT_GUARD, RUIN_PATROL_3,
HUNTGROUP_2_3, SNIPER_2 ...): the fields themselves (ai_f0..f5, ai_d0..d2, ai_bits) are not decoded yet.
"""
from __future__ import annotations

import math

from commands import CommandError, Session, command, _base_dll, _lua_value, _vec, get_record  # noqa: F401

# ------------------------------------------------------------------------------------------- session state --
EXTRA_DEFAULTS = {"objectives": [], "rules": [], "patrols": {}, "objectives_win": False, "base_overrides": {}}
# production.py's mission data (briefing dossier, soundtrack, multiplayer spawns): kept here so undo and
# <NAME>.editor.json carry it like the logic
PRODUCTION_KEYS = ("briefing", "music", "multiplayer", "tactics", "worldtools")   # tactics: tactics.py (minefields, air support); worldtools: notes, linked prefabs, spawn lists, camera paths (worldtools.py)


def extras(s: Session) -> dict:
    """The logic state as JSON-able data (undo snapshots, <NAME>.editor.json)."""
    return {"objectives": list(getattr(s, "objectives", [])), "rules": list(getattr(s, "rules", [])),
            "patrols": dict(getattr(s, "patrols", {})), "objectives_win": bool(getattr(s, "objectives_win", False)),
            "base_overrides": {k: dict(v) for k, v in (getattr(s, "base_overrides", None) or {}).items()},
            **{k: dict(getattr(s, k, None) or {}) for k in PRODUCTION_KEYS}}


def set_extras(s: Session, d: dict | None):
    d = d or {}
    s.objectives = [dict(o) for o in d.get("objectives") or []]
    import missionrules
    s.rules = [missionrules.upgrade(dict(r)) for r in d.get("rules") or []]
    s.patrols = {str(k): dict(v) for k, v in (d.get("patrols") or {}).items()}
    s.objectives_win = bool(d.get("objectives_win"))
    s.base_overrides = {str(k): dict(v) for k, v in (d.get("base_overrides") or {}).items()}
    for k in PRODUCTION_KEYS:
        setattr(s, k, dict(d.get(k) or {}))


def _scene(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    return s.env


def _regions(s):
    return [str(r["name"]) for r in _scene(s).find("REGN") if "name" in r.fields]


def _script_ids(s):
    return {r["script_id"]: r for r in _scene(s).find("HLOI") if "script_id" in r.fields}


def _switches(s):
    return [str(r["name"]) for r in _scene(s).find("SWITCH") if r.fields]


def _waypoints(s):
    return [r for r in _scene(s).find("NEWENVWAYPT") if "name" in r.fields]


# ------------------------------------------------------------------------------------------------ objectives --
OBJECTIVE_TYPES = {"kill": "kill / destroy objects (script ids)", "region": "a player reaches a region",
                   "timer": "survive for some seconds", "manual": "completed by a rule or your own script"}


def _clean_text(v, what):
    v = str(v or "").strip()
    if not v or "\n" in v or len(v) > 120:
        raise CommandError(f"{what}: one line, up to 120 characters")
    try:
        v.encode("latin-1")
    except UnicodeEncodeError:
        raise CommandError(f"{what}: only characters the game's font has (Western European letters)") from None
    return v


def _lua_text(v):
    """A Lua string literal whose bytes are Latin-1 (the game's text encoding), whatever the script file's encoding."""
    out = []
    for ch in v:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("\\%03d" % ord(ch))
    return '"' + "".join(out) + '"'


def _ids(v):
    vals = v if isinstance(v, list) else [v]
    try:
        out = [int(x) for x in vals]
    except (TypeError, ValueError):
        raise CommandError("kill target: script id(s)") from None
    if not out:
        raise CommandError("kill target: at least one script id")
    return out


def _check_objectives(s, objectives):
    out, seen = [], set()
    ids, regions = _script_ids(s), set(_regions(s))
    for o in objectives:
        if not isinstance(o, dict):
            raise CommandError("each objective is {id, title, type, target, start}")
        try:
            idx = int(o.get("id"))
        except (TypeError, ValueError):
            raise CommandError("objective id: a number (the editor's number for it - rules use it)") from None
        if not 0 <= idx <= 63 or idx in seen:
            raise CommandError(f"objective id {idx}: 0-63 and unique")
        seen.add(idx)
        kind = o.get("type", "manual")
        if kind not in OBJECTIVE_TYPES:
            raise CommandError(f"objective {idx}: type is one of {', '.join(OBJECTIVE_TYPES)}")
        target = o.get("target")
        if kind == "kill":
            target = _ids(target)
            missing = [i for i in target if i not in ids]
            if missing:
                raise CommandError(f"objective {idx}: no object with script id {missing[0]} in this scene")
            target = target[0] if len(target) == 1 else target
        elif kind == "region":
            if str(target) not in regions:
                raise CommandError(f"objective {idx}: no region {target!r} in this scene")
            target = str(target)
        elif kind == "timer":
            try:
                target = float(target)
            except (TypeError, ValueError):
                raise CommandError(f"objective {idx}: timer target = seconds") from None
            if not 0 < target <= 36000:
                raise CommandError(f"objective {idx}: seconds 1 - 36000")
        else:
            target = None
        start = o.get("start", "active")
        if start not in ("active", "hidden"):
            raise CommandError(f"objective {idx}: start is active or hidden")
        out.append({"id": idx, "title": _clean_text(o.get("title"), f"objective {idx} title"), "type": kind,
                    "target": target, "start": start})
    return out


@command("Replace the mission's objectives (the whole list, in display order). Each: {id: the editor's number 0-63 (rules use it), title, "
         "type: kill (target = script id or list) / region (target = region name) / timer (target = seconds) / manual, "
         "start: active (shown at mission start) / hidden (a rule shows it)}. Compiled into the mission script on save.",
         mutates=True, objectives="list of objectives", win_when_all_done="optional: mission success once all are done")
def set_objectives(s: Session, objectives, win_when_all_done=None):
    s.objectives = _check_objectives(s, objectives or [])
    if win_when_all_done is not None:
        s.objectives_win = bool(win_when_all_done)
    used = {o["id"] for o in s.objectives}
    for i, r in enumerate(getattr(s, "rules", [])):     # rules may only point at objectives that still exist
        for item in [r["trigger"], *r["conditions"], *r["actions"]]:
            if "objective" in item and item["objective"] not in used:
                raise CommandError(f"rule {i + 1} uses objective {item['objective']} - change that rule first")
    return {"objectives": s.objectives, "win_when_all_done": s.objectives_win}


# ----------------------------------------------------------------------------------------------------- rules --
def _rule_catalog():
    import missionrules
    return missionrules.catalog()


def _check_rule(s, r, index=None):
    import missionrules
    return missionrules.check_rule(s, r, index)


@command("Replace the mission's rules (the whole list). Rule: {name, enabled, once, trigger: {type, ...}, conditions: "
         "[{type, ..., not}], actions: [{type, ...}]} - rule_catalog lists every trigger / condition / action type with "
         "its parameters. Compiled into the mission script on save.", mutates=True, rules="list of rules")
def set_rules(s: Session, rules):
    s.rules = [_check_rule(s, r, k) for k, r in enumerate(rules or [])]
    return {"rules": s.rules}


@command("Every rule trigger, condition and action type with its parameters (name, type, label, default, choices) - "
         "what set_rules / add_rule accept and the rule builder offers.")
def rule_catalog(s: Session):
    import missionrules
    return missionrules.catalog()


@command("Replace one rule (by its index) with a new version.", mutates=True, index="0-based rule index", rule="the rule")
def update_rule(s: Session, index, rule):
    rules = list(getattr(s, "rules", []))
    if not 0 <= int(index) < len(rules):
        raise CommandError(f"no rule {index}")
    rules[int(index)] = _check_rule(s, rule, int(index))
    s.rules = rules
    return {"index": int(index), "rule": rules[int(index)]}


@command("Move a rule up or down in the list (rules run in list order when several fire at once).", mutates=True,
         index="0-based rule index", to="new index")
def move_rule(s: Session, index, to):
    rules = list(getattr(s, "rules", []))
    i, j = int(index), int(to)
    if not (0 <= i < len(rules) and 0 <= j < len(rules)):
        raise CommandError("index out of range")
    rules.insert(j, rules.pop(i))
    s.rules = rules
    return {"rules": rules}


@command("Add one rule (see set_rules / rule_catalog): either rule = the whole rule, or trigger + actions (+ "
         "conditions, name, once).", mutates=True, rule="the rule", trigger="{type, ...}", actions="[{type, ...}]",
         conditions="[{type, ...}]", name="a name", once="fire only the first time (default true)")
def add_rule(s: Session, rule=None, trigger=None, actions=None, conditions=None, name="", once=True):
    r = rule or {"name": name, "trigger": trigger, "conditions": conditions or [], "actions": actions or [],
                 "once": once, "enabled": True}
    rules = list(getattr(s, "rules", []))
    rules.append(_check_rule(s, r, len(rules)))
    s.rules = rules
    return {"index": len(rules) - 1, "rule": rules[-1]}


@command("Delete an event rule by its index in the list.", mutates=True, index="0-based rule index")
def delete_rule(s: Session, index):
    rules = list(getattr(s, "rules", []))
    if not 0 <= int(index) < len(rules):
        raise CommandError(f"no rule {index}")
    gone = rules.pop(int(index))
    s.rules = rules
    return {"deleted": gone}


# -------------------------------------------------------------------------------------------------- patrols --
@command("Give soldiers a patrol route through waypoints (names or ids, in order); an empty list removes it. "
         "At mission start (or when a rule's 'Walk a path' action runs) they walk it: the chain is set with "
         "set_waypoint_next (API +0xA4) and each soldier started with set_path (+0x110) + set_ai_state 0 (+0x114) - "
         "the shipped missions' pattern.", mutates=True,
         line="a soldier's HLOI line (or lines)", waypoints="waypoint names or ids", loop="back to the first one at the end",
         movement="walk / run / crouch / prone", start="mission (at mission start) or rule (only when a rule says so)",
         lines="several soldiers")
def set_patrol(s: Session, line=None, waypoints=None, loop=True, movement="walk", start="mission", lines=None,
               unseen=True):
    """unseen: keep walking while the player can't see the soldier. The game puts enemy soldiers to sleep when they
    were never on screen / not for 60 s / are beyond the fog (FUN_00428110 -> soldier +0x280): a shipped guard's route
    only starts once the player comes near. False = the game's own behaviour."""
    targets = [int(x) for x in (lines or [])] or ([int(line)] if line is not None else [])
    if not targets:
        raise CommandError("line: the soldier's record line")
    if movement not in ("walk", "run", "crouch", "prone"):
        raise CommandError("movement: walk / run / crouch / prone")
    if start not in ("mission", "rule"):
        raise CommandError("start: mission or rule")
    by_name = {str(w["name"]).upper(): str(w["name"]) for w in _waypoints(s)}
    by_id = {w["id"]: str(w["name"]) for w in _waypoints(s)}
    names = []
    for w in waypoints or []:
        name = by_id.get(w) if isinstance(w, int) else by_name.get(str(w).upper())
        if name is None:
            raise CommandError(f"no waypoint {w!r} in this scene")
        names.append(name)
    if len(names) == 1:
        raise CommandError("a patrol needs at least two waypoints")
    out = []
    for ln in targets:
        r = s.record(ln)
        if r.kind != "HLOI":
            raise CommandError("patrols are for soldiers (HLOI records)")
        sid = str(r["script_id"])
        if names:
            s.patrols[sid] = {"waypoints": names, "loop": bool(loop), "movement": movement, "start": start,
                              "unseen": bool(unseen)}
        else:
            s.patrols.pop(sid, None)
        out.append({"script_id": r["script_id"], "patrol": s.patrols.get(sid)})
    return {"patrols": out}


def _patrols_lua(s) -> list[str]:
    """editor.start_patrols(): chain each patrol's waypoints and start its soldiers (fresh missions; a loaded save
    keeps the game's own AI state)."""
    ids = {str(w["name"]): w["id"] for w in _waypoints(s)}
    L = ["function editor.patrol(o, chain, loop, stance, retry, unseen)   -- chain = waypoint ids in order",
         "    for i = 1, #chain - 1 do game.call(0x0A4, chain[i], chain[i + 1]) end",
         "    game.call(0x0A4, chain[#chain], loop and chain[1] or -1)",
         "    -- the game puts enemy soldiers the player hasn't seen to sleep (their route waits for the player)",
         "    if unseen and game.keep_awake then game.keep_awake(o, true) end",
         "    game.call(0x184, o, 0)   -- a 'Hold position' made him ignore order changes",
         "    game.call(0x198, o, 0)   -- the shipped missions' order: distractions on, path, AI state 0, stance",
         "    game.call(0x110, o, chain[1])",
         "    game.call(0x114, o, 0)",
         "    game.call(stance, o)",
         "    if not retry and editor.patrol_retry then",
         "        editor.patrol_retry[o] = { chain = chain, loop = loop, stance = stance, unseen = unseen, tries = 0,",
         "                                   last = game.time_ms() }",
         "    end",
         "end",
         "editor.patrols = {   -- soldier script id -> its route (Assign path / set_patrol)"]
    stances = {"walk": "0x208", "run": "0x20C", "crouch": "0x210", "prone": "0x218"}
    for sid, p in sorted(getattr(s, "patrols", {}).items(), key=lambda kv: int(kv[0])):
        chain = [ids[n] for n in p["waypoints"] if n in ids]
        if len(chain) < 2:
            continue
        L.append(f"    [{int(sid)}] = {{ chain = {{ {', '.join(map(str, chain))} }}, loop = "
                 f"{'true' if p.get('loop', True) else 'false'}, stance = {stances.get(p.get('movement', 'walk'), '0x208')}, "
                 f"at_start = {'true' if p.get('start', 'mission') == 'mission' else 'false'}, "
                 f"unseen = {'true' if p.get('unseen', True) else 'false'} }},"
                 f"   -- {' -> '.join(p['waypoints'][:6])}{' ...' if len(p['waypoints']) > 6 else ''}")
    L += ["}",
          "function editor.start_patrols()",
          "    for o, p in pairs(editor.patrols) do",
          "        if p.at_start then editor.patrol(o, p.chain, p.loop, p.stance, false, p.unseen) end",
          "    end",
          "end"]
    return L


# ------------------------------------------------------------------------------------------- AI profiles --
_UNSET = -1000.0
AI_PROFILES = {
    "assault": {"label": "Aggressive Assault", "bits": {3: 200.0, 4: 30.0}, "d": (3, 2, 3), "f": {},
                "source": "HUNTGROUP_2_* (Mission 11A): hunting squads"},
    "guard": {"label": "Defensive Guard", "bits": {}, "d": (2, 3, 3), "f": {},
              "source": "most *GUARD* soldiers (TENT_GUARD, CS_BASE_GUARD_5 ...)"},
    "patrol": {"label": "Patrolling", "bits": {3: 100.0}, "d": (2, 2, 3), "f": {2: 70.0},
               "source": "RUIN_PATROL_* / VILLAGE_PATROL_* (Mission 5A)"},
    "sniper": {"label": "Sniper / Overlook", "bits": {}, "d": (3, 2, 2), "f": {0: 120.0, 1: 120.0, 2: 80.0},
               "source": "*SNIPER* soldiers (Missions 3-12)"},
    "ambush": {"label": "Ambush (Dormant)", "bits": {}, "d": (2, 3, 3), "f": {}, "in_play": 0,
               "source": "*AMBUSH* soldiers: guard values, waiting for the script (put them in a layer)"},
}


_SOLDIER_MODELS = ("HERO", "IRAQ", "RU_", "CIV", "GOATHERDER")   # props, doors, weapons and keys carry AI fields too


def _is_soldier(r):
    return (r.kind == "HLOI" and "ai_bits" in r.fields and "_VEH_" not in str(r.get("unit") or "").upper()
            and str(r.get("model") or "").upper().startswith(_SOLDIER_MODELS))


def profile_of(r) -> str:
    """The profile a soldier's AI fields match, or "custom"."""
    if not _is_soldier(r):
        return ""
    try:
        bits = r["ai_bits"] & 0x3F
        # ambush first: it is the guard values plus "waits for the script"
        for key, p in sorted(AI_PROFILES.items(), key=lambda kv: kv[0] != "ambush"):
            want = sum(1 << b for b in p["bits"])
            if bits != want or any(abs(r[f"ai_bit{b}"] - v) > 1e-3 for b, v in p["bits"].items()):
                continue
            if tuple(r[f"ai_d{k}"] for k in range(3)) != p["d"]:
                continue
            if any(abs(r[f"ai_f{k}"] - p["f"].get(k, _UNSET)) > 1e-3 for k in range(6) if f"ai_f{k}" in r.fields):
                continue
            if "in_play" in p and r["in_play"] != p["in_play"]:
                continue
            return key
    except KeyError:
        pass
    return "custom"


@command("The soldier AI profiles set_ai_profile offers, with the field values each one writes and the shipped "
         "soldiers they were copied from.")
def ai_profiles(s: Session):
    return {k: {"label": p["label"], "source": p["source"],
                "fields": {**{f"ai_d{i}": v for i, v in enumerate(p["d"])},
                           **{f"ai_f{i}": p["f"].get(i, _UNSET) for i in range(6)},
                           "ai_bits(low)": sum(1 << b for b in p["bits"]),
                           **{f"ai_bit{b}": v for b, v in p["bits"].items()},
                           **({"in_play": p["in_play"]} if "in_play" in p else {})}}
            for k, p in AI_PROFILES.items()}


def _apply_profile(s, line, key):
    p = AI_PROFILES[key]
    env = s.env
    r = s.record(line)
    if not _is_soldier(r):
        raise CommandError(f"line {line}: AI profiles are for soldiers (HLOI with AI fields, not vehicles)")
    # ai_bits decides which floats follow it: rewrite that span (the bit-6 / shape tokens after it are kept)
    old = r["ai_bits"]
    bi = r.fields["ai_bits"]
    rest = bi + 1 + bin(old & 0x3F).count("1")
    new_bits = (old & ~0x3F) | sum(1 << b for b in p["bits"])
    toks = [str(new_bits)]
    for b in range(6):
        if new_bits & (1 << b):
            v = p["bits"].get(b, r.get(f"ai_bit{b}", 0.0))
            toks.append(f"{float(v):.6f}")
    text, tk = env.lines[r.index], r.tokens
    env.lines[r.index] = text[:tk[bi].start] + " ".join(toks) + text[tk[rest - 1].end:]
    env._parse()
    r = env.record_at(r.index + 1)
    for k, v in enumerate(p["d"]):
        r[f"ai_d{k}"] = v
    for k in range(6):
        if f"ai_f{k}" in r.fields:
            r[f"ai_f{k}"] = p["f"].get(k, _UNSET)
    if "in_play" in p:
        r["in_play"] = p["in_play"]
    elif r["in_play"] == 0 and r["script_id"] not in getattr(s, "layers", {}):
        r["in_play"] = 1                       # out of "Ambush": in play again unless a layer holds it back
    if env.problems:
        raise CommandError("; ".join(env.problems[:3]))
    return r.index + 1


@command("Give soldiers an AI profile: assault / guard / patrol / sniper / ambush (ai_profiles lists the values). "
         "ambush = guard values and waiting for the script (show it with a layer).", mutates=True,
         lines="HLOI line(s)", profile="profile key")
def set_ai_profile(s: Session, profile, lines=None, line=None):
    if profile not in AI_PROFILES:
        raise CommandError(f"profile is one of {', '.join(AI_PROFILES)}")
    targets = [line] if line is not None else (lines if isinstance(lines, list) else [lines])
    return {"lines": [_apply_profile(s, ln, profile) for ln in targets if ln is not None], "profile": profile}


# --------------------------------------------------------------------------------------------- atmosphere --
COLOUR_KEYS = ["SUNAMBCOL", "SUNDIFCOL", "FOGCOL", "FLARECOL", "EASTCOL", "SKYCOL", "WESTCOL"]
COLOUR_NAMES = {"SUNAMBCOL": "ambient light", "SUNDIFCOL": "direct sun", "FOGCOL": "fog", "FLARECOL": "sun flare",
                "EASTCOL": "sky east", "SKYCOL": "sky overhead", "WESTCOL": "sky west"}
TIME_KEYS = ["night", "dawn", "noon", "dusk"]   # the four keys the game blends by the hour (FUN_004efc60)
SCALARS = {"FOGSTART": "fog_start", "FOGSTOP": "fog_stop", "XFOGSTART": "far_fog_start", "XFOGSTOP": "far_fog_stop",
           "TIMEOFDAY": "time_of_day"}


SKY_FIELDS = {"SKYTEXTURE": "texture", "SKYTILEFACTOR": "tile", "SKYFIXEDY": "fixed_y", "SKYSPEED": "speed",
              "SKYCURVATURE": "curvature", "SKYHEIGHT": "height"}


def _sky_layers(env) -> list[dict]:
    """The cloud layers: each starts with SKYTEXTURE and has its tiling, speed (x, z, ?), dome curvature, height (cm)."""
    layers = []
    for r in env.records:
        if r.kind not in SKY_FIELDS or len(r.tokens) < 2:
            continue
        if r.kind == "SKYTEXTURE" or not layers:
            layers.append({"index": len(layers), "lines": {}})
        vals = [t.value for t in r.tokens[1:]]
        layers[-1][SKY_FIELDS[r.kind]] = vals if r.kind == "SKYSPEED" else vals[0]
        layers[-1]["lines"][SKY_FIELDS[r.kind]] = r.index + 1
    return layers


def _sun(env):
    lights = [r for r in env.find("DLGT") if r.fields]
    return next((r for r in lights if "SUN" in str(r["name"]).upper()), lights[0] if lights else None)


@command("The scene's atmosphere: time of day, fog distances (cm), the colour keys the game blends by the hour "
         "(night / dawn / noon / dusk: ambient light, direct sun, fog, flare, sky east / overhead / west) and the sun "
         "record (DLGT: its colours and tilt / heading in degrees).")
def get_atmosphere(s: Session):
    env = _scene(s)
    out = {v: None for v in SCALARS.values()}
    colours = {k: [] for k in COLOUR_KEYS}
    for r in env.records:
        if r.kind in SCALARS and len(r.tokens) > 1:
            out[SCALARS[r.kind]] = r.tokens[1].value
        elif r.kind in colours and len(r.tokens) >= 5:
            colours[r.kind].append({"line": r.index + 1, "rgba": [t.value for t in r.tokens[1:5]]})
    out["colours"] = {k: {TIME_KEYS[i] if i < 4 else str(i): e["rgba"] for i, e in enumerate(v)}
                      for k, v in colours.items() if v}
    out["colour_names"] = COLOUR_NAMES
    out["sky_layers"] = _sky_layers(env)
    sun = _sun(env)
    if sun:
        try:
            out["sun"] = {"line": sun.index + 1, "name": sun["name"],
                          "ambient": [sun[f"ambient{k}"] for k in range(4)],
                          "colour": [sun[f"colour{k}"] for k in range(4)],
                          "tilt": sun["p0"], "heading": sun["p1"]}
        except KeyError:
            out["sun"] = None
    return out


def _rgba(v, what):
    if not isinstance(v, (list, tuple)) or len(v) not in (3, 4):
        raise CommandError(f"{what}: [r, g, b] or [r, g, b, a] in 0..1")
    vals = [min(1.0, max(0.0, float(c))) for c in v]
    return vals + [1.0] if len(vals) == 3 else vals


@command("Change the scene's atmosphere (only what is given). colours = {SUNAMBCOL / SUNDIFCOL / FOGCOL / FLARECOL / "
         "EASTCOL / SKYCOL / WESTCOL: {night / dawn / noon / dusk: [r, g, b(, a)] 0..1}}; sun_colour / sun_ambient = the "
         "sun record's colours; sun_tilt / sun_heading in degrees. Note: a mission script's set_time_of_day overrides "
         "time_of_day, and the game moves the sun with the hour.", mutates=True,
         time_of_day="hour 0-24", fog_start="cm", fog_stop="cm", far_fog_start="cm", far_fog_stop="cm",
         colours="{KEY: {time key: rgba}}", sun_colour="[r, g, b, a]", sun_ambient="[r, g, b, a]",
         sun_tilt="degrees", sun_heading="degrees",
         sky="[{index: cloud layer, tile, speed: [x, z, w], curvature, height (cm)}] - only what is given")
def set_atmosphere(s: Session, time_of_day=None, fog_start=None, fog_stop=None, far_fog_start=None, far_fog_stop=None,
                   colours=None, sun_colour=None, sun_ambient=None, sun_tilt=None, sun_heading=None, sky=None):
    env = _scene(s)
    want = {"TIMEOFDAY": time_of_day, "FOGSTART": fog_start, "FOGSTOP": fog_stop, "XFOGSTART": far_fog_start,
            "XFOGSTOP": far_fog_stop}
    if time_of_day is not None and not 0 <= float(time_of_day) < 24:
        raise CommandError("time_of_day: 0 - 24")
    for k in ("FOGSTART", "FOGSTOP", "XFOGSTART", "XFOGSTOP"):
        if want[k] is not None and not 0 <= float(want[k]) <= 200000:
            raise CommandError(f"{SCALARS[k]}: 0 - 200000 cm")
    a, b = fog_start, fog_stop
    cur = get_atmosphere(s)
    if float(a if a is not None else cur["fog_start"] or 0) >= float(b if b is not None else cur["fog_stop"] or 1e9):
        raise CommandError("fog_start must be below fog_stop")
    changed = []
    for r in list(env.records):
        if r.kind in want and want[r.kind] is not None and len(r.tokens) > 1:
            r.set_token(1, float(want[r.kind]))
            changed.append(SCALARS[r.kind])
    for key, per_time in (colours or {}).items():
        if key not in COLOUR_KEYS:
            raise CommandError(f"colour key is one of {', '.join(COLOUR_KEYS)}")
        recs = [r for r in env.records if r.kind == key]
        for t, v in per_time.items():
            i = TIME_KEYS.index(t) if t in TIME_KEYS else int(t)
            if not 0 <= i < len(recs):
                raise CommandError(f"{key} has no {t} key")
            rgba = _rgba(v, key)
            for k in range(4):
                recs[i].set_token(1 + k, rgba[k])
            changed.append(f"{key}.{TIME_KEYS[i] if i < 4 else i}")
    layers = _sky_layers(env)
    for want_layer in sky or []:
        try:
            lay = layers[int(want_layer.get("index"))]
        except (TypeError, ValueError, IndexError, AttributeError):
            raise CommandError(f"sky: no cloud layer {want_layer!r} (this scene has {len(layers)})") from None
        for key in ("tile", "speed", "curvature", "height"):
            if key not in want_layer or want_layer[key] is None:
                continue
            if key not in lay["lines"]:
                raise CommandError(f"sky layer {lay['index']} has no {key} line")
            rec = env.record_at(lay["lines"][key])
            vals = want_layer[key] if key == "speed" else [want_layer[key]]
            if not isinstance(vals, (list, tuple)) or len(vals) > len(rec.tokens) - 1:
                raise CommandError(f"sky {key}: {len(rec.tokens) - 1} value(s)")
            for k, v in enumerate(vals):
                v = float(v)
                if key == "height" and not 100 <= v <= 1000000:
                    raise CommandError("sky height: 100 - 1000000 cm")
                rec.set_token(1 + k, v)
            changed.append(f"sky{lay['index']}.{key}")
    if any(v is not None for v in (sun_colour, sun_ambient, sun_tilt, sun_heading)):
        sun = _sun(env)
        if sun is None:
            raise CommandError("this scene has no sun (DLGT) record")
        line = sun.index + 1
        for field, v in (("colour", sun_colour), ("ambient", sun_ambient)):
            if v is not None:
                rgba = _rgba(v, f"sun_{field}")
                for k in range(4):
                    env.record_at(line)[f"{field}{k}"] = rgba[k]
                changed.append(f"sun.{field}")
        if sun_tilt is not None:
            env.record_at(line)["p0"] = float(sun_tilt) % 360
            changed.append("sun.tilt")
        if sun_heading is not None:
            env.record_at(line)["p1"] = float(sun_heading) % 360
            changed.append("sun.heading")
    if env.problems:
        raise CommandError("; ".join(env.problems[:3]))
    return {"changed": changed, **get_atmosphere(s)}


# ------------------------------------------------------------------------------------------------- cameras --
# Positional camera tokens (p0..) worked out from all 330 cameras of the shipped scenes: angles in degrees like every
# rot (pitch > 0 looks down, yaw: forward = (sin, cos) in x / z), the field of view where the value is 20-90 in
# nearly every record (other fields there are 0).
_CAM = {"CAMFREE": {"rot": ("p0", "p1", "p2"), "fov": "p16"},
        "CAMTARGPOS": {"target": ("p0", "p1", "p2"), "fov": "p21"},
        "CAMTARGOBJ": {"target_object": "p0", "offset": ("p1", "p2", "p3"), "fov": "p17"},
        "CAMCHASEOBJ": {"target_object": "p0", "fov": "p8"}}


def camera_info(r) -> dict:
    spec = _CAM.get(r.kind, {})
    out = {}
    try:
        for k, f in spec.items():
            out[k] = [r[x] for x in f] if isinstance(f, tuple) else r[f]
    except KeyError:
        return {}
    return out


@command("Change a camera: its target point (CAMTARGPOS), view angles [pitch, yaw, roll] (CAMFREE), field of view.",
         mutates=True, line="camera record line", target="[x, y, z] cm", rot="[pitch, yaw, roll] degrees",
         fov="degrees")
def set_camera(s: Session, line, target=None, rot=None, fov=None):
    r = s.record(line)
    spec = _CAM.get(r.kind)
    if spec is None or not camera_info(r):
        raise CommandError(f"line {line} is not a camera the editor understands")
    for key, value in (("target", target), ("rot", rot)):
        if value is None:
            continue
        if key not in spec:
            raise CommandError(f"{r.kind} has no {key}")
        for f, v in zip(spec[key], _vec(value)):
            s.env.record_at(int(line))[f] = v
    if fov is not None:
        if not 5 <= float(fov) <= 150:
            raise CommandError("fov: 5 - 150 degrees")
        s.env.record_at(int(line))[spec["fov"]] = float(fov)
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return get_record(s, line)


# -------------------------------------------------------------------------------------------- scene / query --
def decorate_scene(s: Session, out: dict):
    """Extra per-record data for the viewport (scene command): camera angles / targets, soldier AI profiles,
    patrols."""
    patrols = getattr(s, "patrols", {})
    for c in out.get("cameras", []):
        try:
            c.update(camera_info(s.record(c["line"])))
        except CommandError:
            pass
    targets = base_targets(s)
    dll = _base_dll(s, s.level) or (f"{s.level.title()}.dll" if targets else None)
    for o in out.get("objects", []):
        if o.get("script_id") in targets:
            o["is_base_target"], o["base_dll"] = True, dll
            o["badge"] = "DLL Critical"
        try:
            r = s.record(o["line"])
        except CommandError:
            continue
        o["ai_profile"] = profile_of(r)
        p = patrols.get(str(o["script_id"]))
        if p:
            o["patrol"] = p


def _warnings(s):
    out = []
    ids, regions = _script_ids(s), set(_regions(s))
    layers = set(getattr(s, "layers", {}).values())
    wps = {str(w["name"]) for w in _waypoints(s)}
    for o in getattr(s, "objectives", []):
        if o["type"] == "kill" and any(i not in ids for i in _ids(o["target"])):
            out.append(f"objective {o['id']}: a target object is no longer in the scene")
        if o["type"] == "region" and o["target"] not in regions:
            out.append(f"objective {o['id']}: region {o['target']} is gone")
    if any(o.get("texts_missing") for o in base_objectives(s)):
        out.append("the base mission's objective texts weren't found - start the editor with --game <game folder> "
                   "once (they are remembered in the data folder's _editor_cache afterwards)")
    gone = sorted(i for i in base_targets(s) if i not in ids)
    if gone:
        out.append(f"script id{'s' if len(gone) > 1 else ''} {', '.join(map(str, gone))}: used by "
                   f"{_base_dll(s, s.level) or 'the base mission'} but no longer in the scene - its logic for "
                   "them won't work (put objects in a hidden layer instead of deleting them)")
    import missionrules
    for i, r in enumerate(getattr(s, "rules", [])):
        try:
            missionrules.check_rule(s, r, i)
        except CommandError as e:
            out.append(str(e))
    for sid, p in getattr(s, "patrols", {}).items():
        if int(sid) not in ids:
            out.append(f"patrol of {sid}: the soldier is gone")
        for w in p["waypoints"]:
            if w not in wps:
                out.append(f"patrol of {sid}: waypoint {w} is gone")
    return out


def _base_objective_count(s) -> int:
    """Objective indices the base mission adds itself: its DLL adds them from 0 (editor/games/<game>/level_base.json). A new level
    has none."""
    return len(base_objectives(s))


# ------------------------------------------------------------------------- the base mission's own data --
# editor/games/<game>/level_base.json (scripts/gen_level_setups.py): per shipped mission the objectives its DLL adds (API +0x4B0
# title hash, +0x4B4 description hash) and the script ids it names. Texts are resolved from the player's own catalog /
# <level>Strs.loc (conflict_ds_tools/records.py) - the JSON holds only numbers. Note: API +0x4E0(hash, slot) in the
# set-up (level_setups.json) is the BRIEFING (0 title, 1 date, 2 place, 3+ story pages), not an objective.
_LEVEL_BASE = {}             # game id -> level_base.json
BASE_TIMER = 8998                 # game.timer id: re-hides the base objectives the modder turned off


def level_base(level) -> dict:
    from conflict_ds_tools import games
    gid = games.current().id
    if gid not in _LEVEL_BASE:
        import json
        import os
        from commands import game_data
        path = game_data("level_base.json")
        try:
            with open(path or "") as f:
                _LEVEL_BASE[gid] = json.load(f)
        except OSError:
            _LEVEL_BASE[gid] = {}
    return _LEVEL_BASE[gid].get((level or "").upper(), {})


def _has_base(s) -> bool:
    return bool(s.level) and not getattr(s, "blank", False) and bool(level_base(s.level))


def _text_source(s) -> str | None:
    """The level's own text file as records.py names it: set-up +0x58 "M01Strs.loc" -> "M01"."""
    from commands import _setups
    for c in _setups().get(s.level or "", []):
        if c["slot"] == 0x58 and c["args"] and isinstance(c["args"][0], str) and c["args"][0].lower().endswith("strs.loc"):
            return c["args"][0][:-8].upper()
    # Global Storm: each level archive's text bundle ends with <M01|T00A>Strs.loc (records.py source "M01" / "T00A")
    import re
    m = re.fullmatch(r"(MISSION|TRAINING)(\d+)([A-Z]?)", s.level or "")
    if m:
        return ("M" if m.group(1) == "MISSION" else "T") + m.group(2).zfill(2) + m.group(3)
    return None


def _game_records(s):
    """The game's records / texts (editor/records.py), or None without a game folder."""
    try:
        import records as editor_records
        return editor_records._records(s)
    except Exception:   # noqa: BLE001  (no game folder / catalog: texts stay unresolved)
        return None


def _resolve_text(s, h):
    """(KEY, text) of a catalog hash - the level's own text file first, then the catalog; (None, None) unknown."""
    R = _game_records(s)
    src = _text_source(s)
    hit = R.text_for_hash(h, tuple(x for x in (src, "catalog") if x)) if R else None
    return hit or (None, None)


def _text_cache_path(s) -> str:
    import os
    return os.path.join(s.data_dir, "_editor_cache", "level_texts.json")


def _text_cache(s, level, part, value=None):
    """Resolved base-mission texts kept in the player's own data folder (not in the editor - no game text ships with
    it): written whenever the game folder's texts resolve, read when they don't (editor started without a game
    folder). value None = read."""
    import json
    import os
    path = _text_cache_path(s)
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if value is None:
        return cache.get(level, {}).get(part)
    if cache.get(level, {}).get(part) == value:
        return value
    cache.setdefault(level, {})[part] = value
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1, ensure_ascii=False)
    except OSError:
        pass
    return value


def _script_info(s):
    """Global Storm: the open level's <LEVEL>.SI (conflict_ds_tools/scriptinfo.py), or None."""
    import os
    cached = getattr(s, "_si_cache", None)
    if cached is not None and cached[0] == (s.level, s.folder):
        return cached[1]
    si = None
    if s.level and s.folder and os.path.isdir(s.folder) and not getattr(s, "blank", False):
        from conflict_ds_tools import scriptinfo
        from conflict_ds_tools.evo import archive_hash
        hashed = "_%08X." % archive_hash(s.level + ".SI")     # extracted before .SI was a known extension (DS2)
        path = next((os.path.join(s.folder, f) for f in os.listdir(s.folder)
                     if f.upper() == s.level + ".SI" or f.upper().startswith(hashed)), None)
        if path:
            try:
                si = scriptinfo.load(path)
            except (OSError, ValueError):
                si = None
    s._si_cache = ((s.level, s.folder), si)
    return si


_SI_WHEN = {
    "SCRIPT_TRIGGERED": "the mission script completes it",
    "ALL_HLOS_DESTROYED": "all of its objects are destroyed",
    "ANY_HLO_DESTROYED": "any of its objects is destroyed",
    "COLLECTER": "its items are collected",
    "ONE_PLAYER_GRUNT_DEAD_OR_ALL_UNCONSCIOUS": "a player's soldier dies or all are unconscious",
    "ANY_ALIVE_PLAYER_HLO_IN_REGION": "any living player soldier is in its region",
    "ALL_ALIVE_PLAYER_HLOS_IN_REGION": "all living player soldiers are in its region",
    "ALL_HLOS_ALIVE_AND_IN_REGION": "all of its objects are alive and in its region",
    "ANY_PLAYER_HLO_HAS_PICKED_UP_ITEM": "a player soldier picks up its item",
    "TRIGGERED_ON_MISSION_COMPLETE": "the mission is completed",
}


def _si_objectives(s, si) -> list[dict]:
    """Global Storm: objectives from the level's .SI - text keys resolved like the DLL objectives, plus how each one
    completes ("si": when, priority, status, refs to regions / objects / waypoints for the dialog)."""
    from conflict_ds_tools.records import hash_key
    regions = [str(r.get("name")) for r in s.env.find("REGN")] if s.env else []
    waypoints = [str(r.get("name")) for r in s.env.find("NEWENVWAYPT")] if s.env else []
    objects = {}
    for r in (s.env.find("HLOI") if s.env else []):
        if "stat" in r.fields:
            objects.setdefault(str(r["stat"]).upper(), r["script_id"])
    out = []
    for i, o in enumerate(si["objectives"]):
        key, text = _resolve_text(s, hash_key(o["title_key"])) if o["title_key"] else (None, None)
        dkey, dtext = _resolve_text(s, hash_key(o["text_key"])) if o["text_key"] else (None, None)
        refs = []
        for n in o["regions"]:
            if n in regions:
                refs.append({"ref": "region", "id": regions.index(n), "text": n})
        for n in o["hlos"]:
            sid = objects.get(n.upper())
            refs.append({"ref": "object", "id": sid, "text": n} if sid is not None else
                        {"ref": "missing", "id": -1, "text": n})
        for n in o["waypoints"]:
            if n in waypoints:
                refs.append({"ref": "waypoint", "id": waypoints.index(n), "text": n})
        when = _SI_WHEN.get(o["completion"], o["completion"].replace("_", " ").lower())
        result = {"MISSION_COMPLETE": "mission succeeds", "MISSION_FAILED": "mission fails"}.get(o["result"])
        if result:
            when += f" -> {result}" + (f" after {o['result_delay_ms'] / 1000:g} s" if o["result_delay_ms"] else "")
        name = o["name"].replace("_", " ").title()
        out.append({"id": i, "title": text if text is not None else (o["title_key"] or name),
                    "key": key or o["title_key"] or None, "description": dtext or "", "description_key": dkey,
                    "base": True, "source": "base", "texts_missing": text is None and bool(o["title_key"]),
                    "title_editable": False, "hash": hash_key(o["title_key"]) if o["title_key"] else 0,
                    "si": {"name": o["name"], "when": when, "priority": o["priority"], "status": o["status"],
                           "xp": o["xp"], "refs": refs}})
    return out


def base_objectives(s) -> list[dict]:
    """The base mission's own objectives (resolved once per open level; [] for new levels / MP / no DLL). Global
    Storm: from the level's .SI (its objectives are data there, not DLL calls)."""
    si = _script_info(s) if s.level else None
    if si is not None and si["objectives"]:
        cached = getattr(s, "_base_objectives", None)
        if cached is not None and cached[0] == s.level:
            return cached[1]
        out = _si_objectives(s, si)
        s._base_objectives = (s.level, out)
        return out
    if not _has_base(s):
        return []
    cached = getattr(s, "_base_objectives", None)
    if cached is not None and cached[0] == s.level:
        return cached[1]
    src, out = _text_source(s), []
    R = _game_records(s)
    cached = (_text_cache(s, s.level, "objectives") or {}) if R is None else {}
    for o in level_base(s.level).get("objectives", []):
        key, text = _resolve_text(s, o["title"])
        dkey, dtext = _resolve_text(s, o["description"]) if "description" in o else (None, None)
        c = cached.get(str(o["index"]), {})
        key, text = key or c.get("key"), text if text is not None else c.get("title")
        dkey, dtext = dkey or c.get("description_key"), dtext if dtext is not None else c.get("description")
        out.append({"id": o["index"],
                    "title": text if text is not None else f"Objective {o['index'] + 1} (text 0x{o['title']:08X})",
                    "key": key, "description": dtext or "", "description_key": dkey, "base": True, "source": "base",
                    "texts_missing": text is None,
                    # game.set_string replaces the level's own texts only (DesertStormFix scripthost)
                    "title_editable": bool(key and src and R and R.level_text(src, key) is not None),
                    "hash": o["title"]})
    if R is not None and any(not o["texts_missing"] for o in out):
        _text_cache(s, s.level, "objectives", {str(o["id"]): {k: o[k] for k in ("title", "key", "description",
                                                                                 "description_key")}
                                               for o in out if not o["texts_missing"]})
    s._base_objectives = (s.level, out)
    return out


def _base_overrides(s) -> dict:
    """{index: {"title": text, "hidden": bool}} - only real changes."""
    return {int(k): dict(v) for k, v in (getattr(s, "base_overrides", None) or {}).items()}


@command("All objectives of the open level: the base mission's own (source 'base' - its DLL adds and runs them; "
         "title / hidden can be overridden with set_base_objectives) and the editor's (source 'custom', "
         "set_objectives). Base titles come from the player's game texts.")
def get_objectives(s: Session):
    _scene(s)
    ov = _base_overrides(s)
    base = []
    for o in base_objectives(s):
        c = ov.get(o["id"], {})
        base.append({**o, "original_title": o["title"], "original_description": o["description"],
                     "title": c.get("title") or o["title"], "hidden": bool(c.get("hidden")),
                     "is_overridden": bool(c), "hash": "0x%08X" % o["hash"], "start": "base mission"})
    custom = [{**o, "source": "custom", "base": False} for o in getattr(s, "objectives", [])]
    return {"objectives": base + custom, "base_count": len(base), "first_free_index": first_free_index(s)}


@command("Change the base mission's own objectives: a new title (written into the level's text with game.set_string; "
         "'' = the game's) and / or hidden (the script hides it whenever the base mission shows it). Only the ones "
         "given change.", mutates=True,
         objectives="[{id: base objective index, title: text or '', hidden: true/false}]")
def set_base_objectives(s: Session, objectives):
    known = {o["id"]: o for o in base_objectives(s)}
    if not known:
        raise CommandError("this level has no base-mission objectives")
    ov = _base_overrides(s)
    for o in objectives or []:
        try:
            idx = int(o.get("id"))
        except (TypeError, ValueError):
            raise CommandError("each entry is {id, title, hidden}") from None
        if idx not in known:
            raise CommandError(f"no base objective {idx} (0 - {len(known) - 1})")
        cur = ov.get(idx, {})
        if "title" in o:
            t = _clean_text(o.get("title") or "", f"objective {idx} title") if o.get("title") else ""
            if t and t != known[idx]["title"]:
                if not known[idx]["title_editable"]:
                    raise CommandError(f"objective {idx}: its title isn't one of the level's own texts - "
                                       "it can't be renamed from a script")
                cur["title"] = t
            else:
                cur.pop("title", None)
        if "hidden" in o:
            if o.get("hidden"):
                cur["hidden"] = True
            else:
                cur.pop("hidden", None)
        if cur:
            ov[idx] = cur
        else:
            ov.pop(idx, None)
    s.base_overrides = {str(k): v for k, v in sorted(ov.items())}
    return get_objectives(s)


@command("The base mission's briefing texts, read from the player's game texts: title / date / place / story pages "
         "(what its DLL sets with API +0x4E0), the intro's date / place captions (MOV_TXT1 / MOV_TXT2), the loading "
         "screen's texts and the map image. Empty for new levels.")
def get_mission_intel(s: Session):
    _scene(s)
    from commands import _setups
    out = {"title": "", "date": "", "location": "", "briefing_text": "", "intro_date": "", "intro_location": "",
           "loading_title": "", "loading_text": "", "map_image": None, "keys": {}, "base": False}
    if not _has_base(s):
        return out
    pages = []
    for c in _setups().get(s.level, []):
        if c["slot"] == 0x4E0 and len(c["args"]) > 1 and isinstance(c["args"][0], int):
            key, text = _resolve_text(s, c["args"][0])
            slot = c["args"][1] if isinstance(c["args"][1], int) else 3
            if text is None:
                continue
            field = {0: "title", 1: "date", 2: "location"}.get(slot)
            if field:
                out[field], out["keys"][field] = text, key
            else:
                pages.append(text)
                out["keys"].setdefault("briefing_text", key)
        elif c["slot"] == 0x4C and c["args"] and isinstance(c["args"][0], str):
            out["map_image"] = c["args"][0]
    out["briefing_text"] = "\n\n".join(pages)
    R, src = _game_records(s), _text_source(s)
    if R and src:
        for field, key in (("intro_date", "MOV_TXT1"), ("intro_location", "MOV_TXT2"), ("loading_title", "SECTIONHDR"),
                           ("loading_text", "SECTIONTEXT")):
            out[field] = R.level_text(src, key) or ""
    out["base"] = True
    fields = ("title", "date", "location", "briefing_text", "intro_date", "intro_location", "loading_title",
              "loading_text", "keys")
    if R is not None and any(out[f] for f in fields[:-1]):
        _text_cache(s, s.level, "intel", {f: out[f] for f in fields})
    elif R is None:
        out.update(_text_cache(s, s.level, "intel") or {})
        out["texts_missing"] = not any(out[f] for f in fields[:-1])
    return out


def base_targets(s) -> dict[int, list[int]]:
    """Script ids the base mission's DLL names directly -> the API slots it uses them with."""
    if not _has_base(s):
        return {}
    return {int(k): v for k, v in level_base(s.level).get("script_ids", {}).items()}


def first_free_index(s) -> int:
    """The next editor objective number: after the base mission's own (0 .. n-1 in the dialog), so the list reads
    in order. The game's own index is handed out at run time (editor.idx in the script)."""
    used = {o["id"] for o in getattr(s, "objectives", [])}
    start = _base_objective_count(s)
    while start in used:
        start += 1
    return start


@command("The mission logic for the Objectives / Events panels: objectives, rules, patrols, the names they can use "
         "(regions, layers, waypoints), problems, and the Lua the editor writes for them.")
def mission_logic(s: Session):
    _scene(s)
    return {**extras(s), "win_when_all_done": getattr(s, "objectives_win", False),
            "base_objectives": [o for o in get_objectives(s)["objectives"] if o["source"] == "base"],
            "first_free_index": first_free_index(s), "objective_types": OBJECTIVE_TYPES, "catalog": _rule_catalog(),
            "rule_texts": _rule_texts(s), "regions": sorted(_regions(s)), "switches": _switches(s), "layers": sorted(set(getattr(s, "layers", {}).values())),
            "waypoints": [{"line": w.index + 1, "id": w["id"], "name": w["name"]} for w in _waypoints(s)],
            "warnings": _warnings(s), "lua": blocks_text(s)}


# ------------------------------------------------------------------------------------------- Lua: the blocks --
OBJ_BEGIN, OBJ_END = "-- @editor begin: objectives", "-- @editor end: objectives"
RULES_BEGIN, RULES_END = "-- @editor begin: rules", "-- @editor end: rules"
SHOW_TIMER, OBJ_TIMER, RULE_TIMER = 8999, 9000, 9100      # game.timer ids the blocks use
KILL = "(id == game.EVENT.KILLED or id == game.EVENT.DESTROYED)"
HOOK_INIT = "    editor_init()   -- the editor's objectives and rules (Objectives..., Events & Actions)"
HOOK_MSG = "    editor_on_message({args})   -- the editor's objectives and rules"
HOOK_UPDATE = "    editor_update()   -- the editor's rules: waits, timed rules, conditions"


def _describe_objective(o):
    t = o["target"]
    if o["type"] == "kill":
        ids = _ids(t)
        return f"kill / destroy script id{'s' if len(ids) > 1 else ''} {', '.join(map(str, ids))}"
    if o["type"] == "region":
        return f'a player reaches region "{t}"'
    if o["type"] == "timer":
        return f"survive {t:g} s"
    return "completed by a rule or your script"


def _rule_texts(s):
    import missionrules
    ctx = missionrules.Ctx(s)
    return [missionrules.describe(r, ctx) for r in getattr(s, "rules", [])]


def _describe_rule(r):
    import missionrules
    return missionrules.describe(r)


def _objectives_block(s) -> str:
    objs = list(getattr(s, "objectives", []))
    L = [f"{OBJ_BEGIN} (rewritten by the editor on every save - change them in Objectives...)",
         "editor = editor or {}",
         "function editor.state()   -- progress, kept in save games (game.state)",
         "    game.state.editor = game.state.editor or {}",
         "    local st = game.state.editor",
         "    st.done, st.shown, st.fired, st.killed = st.done or {}, st.shown or {}, st.fired or {}, st.killed or {}",
         "    return st",
         "end",
         f"editor.objectives = {{ {', '.join(str(o['id']) for o in objs)} }}   -- the editor's objective numbers, "
         "in display order",
         f"editor.win_when_all_done = {'true' if getattr(s, 'objectives_win', False) and objs else 'false'}",
         "-- the game hands out objective indices in the order objectives are added (the base mission's own too, and",
         "-- again after a loaded save): editor number -> the game's index, this session only",
         "editor.idx = {}",
         "local function add(i, title)",
         "    if not editor.idx[i] then editor.idx[i] = game.add_objective(title) end",
         "end",
         "function editor.add_objectives()   -- once per session"]
    for o in objs:
        L.append(f"    add({o['id']}, game.text(\"EDITOR_OBJ_{o['id']}\", {_lua_text(o['title'])}))"
                 f"   -- {_describe_objective(o)}")
    if not objs:
        L.append("    -- (no objectives yet)")
    active = [o["id"] for o in objs if o["start"] == "active"]
    L += ["end"] + _base_lua(s) + [
          "function editor.show(i)   -- show + track objective i (remembered for loaded saves)",
          "    editor.state().shown[i] = true",
          "    if editor.idx[i] then game.show_objective(editor.idx[i]) end",
          "end",
          f"function editor.start(fresh)   -- just after the mission starts (timer {SHOW_TIMER}) and after a loaded save",
          "    if not fresh then editor.repair_base() end",
          "    editor.add_objectives()",
          "    editor.base_texts()",
          "    editor.hide_base()",
          f"    if next(editor.base_hashes) then game.timer({BASE_TIMER}, 1, true) end   -- keeps the base ones right",
          "    local st = editor.state()",
          f"    if fresh then for _, i in ipairs({{ {', '.join(map(str, active))} }}) do st.shown[i] = true end end"
          "   -- the ones that start active",
          "    for _, i in ipairs(editor.objectives) do",
          "        if st.shown[i] and not st.done[i] and editor.idx[i] then game.show_objective(editor.idx[i]) end",
          "    end",
          "    if editor.rules_start then editor.rules_start(fresh) end",
          "end",
          "function editor.complete(i)   -- objective i done (once); with win_when_all_done the last one wins",
          "    local st = editor.state()",
          "    if st.done[i] then return end",
          "    st.done[i] = true",
          "    if editor.idx[i] then game.complete_objective(editor.idx[i]) end",
          "    if editor.rules_on_message then editor.rules_on_message(editor.OBJECTIVE_DONE, i) end",
          "    if not editor.win_when_all_done then return end",
          "    for _, j in ipairs(editor.objectives) do",
          "        if not st.done[j] then return end",
          "    end",
          "    game.end_mission(2)",
          "end",
          "function editor.objective_timers()"]
    timers = [o for o in objs if o["type"] == "timer"]
    for o in timers:
        L.append(f"    game.timer({OBJ_TIMER + o['id']}, {o['target']:g})   -- objective {o['id']}: survive {o['target']:g} s")
    L += ["end", "function editor.objectives_on_message(id, a, b)"]
    for o in objs:
        i, t = o["id"], o["target"]
        L.append(f"    -- {i} {o['title']}: {_describe_objective(o)}")
        if o["type"] == "kill":
            ids = _ids(t)
            if len(ids) == 1:
                L.append(f"    if {KILL} and a == {ids[0]} then editor.complete({i}) end")
            else:
                L += [f"    if {KILL} and ({' or '.join(f'a == {x}' for x in ids)}) then",
                      "        local k = editor.state().killed",
                      "        k[a] = true",
                      f"        if {' and '.join(f'k[{x}]' for x in ids)} then editor.complete({i}) end",
                      "    end"]
        elif o["type"] == "region":
            L.append(f"    if id == game.EVENT.ENTERED and a == {_lua_value(t)} then editor.complete({i}) end")
        elif o["type"] == "timer":
            L.append(f"    if id == game.EVENT.TIMER and a == {OBJ_TIMER + i} then editor.complete({i}) end")
        else:
            L.append(f"    -- (editor.complete({i}) from a rule or your own code)")
    L += ["end", OBJ_END]
    return "\n".join(L)


def _base_lua(s) -> list[str]:
    """The base mission's objectives as the modder changed them: renamed through the level's own text (by key) and
    turned off by hiding them whenever the base mission shows them (found by title hash in the campaign's objective
    list [[0x60EDC0]+0x12E4]: entry +0 index byte, +4 title hash, +0x14 flags (2 shown), next +0xD8)."""
    ov, base = _base_overrides(s), {o["id"]: o for o in base_objectives(s)}
    L = ["editor.base_hashes = {   -- the base mission's own objectives (title hashes)"]
    L += [f"    [0x{o['hash']:08X}] = true,   -- {i} {o['title']}" for i, o in sorted(base.items())]
    L += ["}", "editor.base_hidden = {   -- the base mission's objectives turned off in Objectives... (title hashes)"]
    for i, c in sorted(ov.items()):
        if c.get("hidden") and i in base:
            L.append(f"    [0x{base[i]['hash']:08X}] = true,   -- {i} {base[i]['title']}")
    L += ["}", "function editor.base_texts()   -- the base mission's objective titles renamed in Objectives..."]
    renamed = [(i, c["title"]) for i, c in sorted(ov.items()) if c.get("title") and i in base and base[i]["key"]]
    for i, title in renamed:
        L.append(f"    if game.set_string then game.set_string(\"{base[i]['key']}\", {_lua_text(title)}) end"
                 f"   -- was {base[i]['title']!r}")
    if not renamed:
        L.append("    -- (none renamed)")
    L += ["end",
          "function editor.each_objective(fn)   -- the game's objectives: fn(index, title hash, flags 2 shown 4 done 8 tracked)",
          "    local c = game.read32(0x60EDC0)",
          "    local e = c and c ~= 0 and game.read32(c + 0x12E4) or 0",
          "    while e and e ~= 0 do",
          "        local i, h, f = game.read32(e), game.read32(e + 4), game.read32(e + 0x14)",
          "        if i and h and f then fn(i & 0xFF, h & 0xFFFFFFFF, f & 0xFF) end",
          "        e = game.read32(e + 0xD8)",
          "    end",
          "end",
          "function editor.hide_base()   -- hides the turned-off base objectives the base mission has shown",
          "    if not next(editor.base_hidden) then return end",
          "    editor.each_objective(function(i, h, f)",
          "        if editor.base_hidden[h] and f & 2 ~= 0 then game.hide_objective(i) end",
          "    end)",
          "end",
          "-- A loaded save gets the base objectives' flags back by list position, and ours (added in between) shift",
          "-- them: their flags are kept by title hash in game.state and put right after the load.",
          "function editor.snapshot_base()",
          "    if not next(editor.base_hashes) then return end",
          "    local flags = {}",
          "    editor.each_objective(function(i, h, f) if editor.base_hashes[h] then flags[h] = f & 0x0E end end)",
          "    editor.state().base_flags = flags",
          "end",
          "function editor.repair_base()",
          "    local flags = editor.state().base_flags",
          "    if not flags then return end",
          "    editor.each_objective(function(i, h, f)",
          "        local want = flags[h]",
          "        if not want then return end",
          "        if want & 4 ~= 0 and f & 4 == 0 then game.complete_objective(i)",
          "        elseif want & 4 == 0 and f & 4 ~= 0 then game.reopen_objective(i) end",
          "        if want & 6 == 2 and f & 2 == 0 then game.show_objective(i)",
          "        elseif want & 2 == 0 and f & 2 ~= 0 then game.hide_objective(i) end",
          "    end)",
          "end"]
    return L


def _switch_hash(name) -> int:
    """What message 0x0D carries in a: the archive hash of the switch's name, as a signed int (interactive.py)."""
    from interactive import name_hash, signed
    return signed(name_hash(str(name)))


def _rules_block(s) -> str:
    import missionrules
    rules = list(getattr(s, "rules", []))
    L = [f"{RULES_BEGIN} (rewritten by the editor on every save - change them in Objectives... > Rules)",
         "function editor_init()   -- called from init()",
         "    if game.loading_save() then return end   -- a loaded save: editor.start(false) on SAVE_LOADED",
         f"    game.timer({SHOW_TIMER}, 0.5)   -- add the objectives, start the rules and patrols",
         "    editor.objective_timers()",
         "end",
         "function editor_on_message(id, a, b, c)   -- called from on_message()",
         f"    if id == game.EVENT.TIMER and a == {SHOW_TIMER} then editor.start(true) end",
         "    if id == game.EVENT.SAVE_LOADED then editor.start(false) end",
         f"    if id == game.EVENT.TIMER and a == {BASE_TIMER} then editor.hide_base() end",
         "    editor.rules_on_message(id, a, b, c)",
         "    editor.objectives_on_message(id, a, b)   -- after the rules: a rule that shows an objective comes first",
         "    editor.snapshot_base()   -- the base objectives' state, for loaded saves",
         "end"]
    L += _patrols_lua(s)
    cams = [str(r["name"]) for r in s.env.records if r.kind.startswith("CAM") and r.fields] if s.env else []
    L += missionrules.rules_lua(rules, _regions(s), waits_for_start=_has_base(s), cameras=cams)
    L.append(RULES_END)
    return "\n".join(L)


def blocks_text(s) -> str:
    return _objectives_block(s) + "\n\n" + _rules_block(s)


def has_logic(s) -> bool:
    return bool(getattr(s, "objectives", []) or getattr(s, "rules", []) or getattr(s, "patrols", {})
                or _base_overrides(s))


# ----------------------------------------------------------------------------------- Lua: updating a script --
def _replace_block(text, begin, end, block, after):
    """text with [begin .. end] replaced by block, or block inserted after the `after` position."""
    a = text.find(begin)
    if a >= 0:
        b = text.find(end, a)
        if b > a:
            return text[:a] + block + text[b + len(end):]
    return text[:after] + "\n" + block + "\n" + text[after:]


def ensure_hooks(text):
    """init() calls editor_init() and on_message() calls editor_on_message(...) - added as their first line when
    missing (or the functions added when the script has none)."""
    import re
    own = _without_blocks(text)                # the blocks define editor_init / editor_on_message themselves
    if not re.search(r"^\s*editor_init\(\)", own, re.M):
        m = re.search(r"^function init\(\)[^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + HOOK_INIT + "\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\nfunction init()\n" + HOOK_INIT + "\nend\n"
    if not re.search(r"^\s*editor_on_message\(", own, re.M):
        m = re.search(r"^function on_message\(([^)]*)\)[^\n]*\n", text, re.M)
        if m:
            params = [p.strip() for p in m.group(1).split(",") if p.strip() and p.strip() != "..."]
            args = (params + ["nil", "nil", "nil", "nil"])[:4]
            text = text[:m.end()] + HOOK_MSG.format(args=", ".join(args)) + "\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\nfunction on_message(id, a, b, c)\n" + HOOK_MSG.format(args="id, a, b, c") + "\nend\n"
    if not re.search(r"^\s*editor_update\(\)", own, re.M):
        m = re.search(r"^function update\(\)[^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + HOOK_UPDATE + "\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\nfunction update()\n" + HOOK_UPDATE + "\nend\n"
    return text


def update_blocks(text, s, after):
    """The script with fresh objectives / rules blocks (inserted at `after` - the end of the layers block - when
    missing) and, when the mission has any logic, the hooks in init() / on_message()."""
    text = _replace_block(text, OBJ_BEGIN, OBJ_END, _objectives_block(s), after)
    end_of_obj = text.find(OBJ_END, text.find(OBJ_BEGIN)) + len(OBJ_END)
    text = _replace_block(text, RULES_BEGIN, RULES_END, _rules_block(s), end_of_obj)
    if has_logic(s):
        text = ensure_hooks(text)
    return text


def _without_blocks(text):
    for begin, end in ((OBJ_BEGIN, OBJ_END), (RULES_BEGIN, RULES_END)):
        a = text.find(begin)
        if a >= 0:
            b = text.find(end, a)
            if b > a:
                text = text[:a] + text[b + len(end):]
    return text


def strip(text):
    """The script without the objectives / rules blocks and the hook lines (to compare scripts)."""
    return "\n".join(l for l in _without_blocks(text).splitlines()
                     if "editor_init()" not in l and "editor_on_message(" not in l and "editor_update()" not in l)
