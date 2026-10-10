"""Editor commands for tactical encounter design: soldiers' detection ranges (vision cones), minefields, heavy-weapon
emplacements and laser-designated air support. Registered into commands.py's command table like logic.py, studio.py
and production.py, so the UI and the MCP server get the same commands with the same undo.

What the game does (DesertStorm.exe, read 2026-10-10):
- Sight range = soldier +0x2E0 (getter FUN_00403bf0), set when the soldier is made (0x421FDE): the HLOI's ai_f2 in
  metres (x100 cm) when the scene gives one (not -1000), else the soldier type's HSTAT.TXT column 7 (squad and Delta
  70 m, Iraqi rows 60 m; loader FUN_004918b0 - UNITS.TXT column 4 names the HSTAT row). The getter then scales it by
  the alert state (+0x2D8): 1 sees nothing, 2 (the normal, unaware state) x 0.75, 3 / 4 (alerted, combat) the full
  cap; outside 8-16 h it shrinks with the hour down to x 0.75 at night (FUN_004cc190); and it is capped at
  (fog distance - 5 m) x 0.99 (0x40F89F: atmosphere +0xC, the scene's FOGSTOP as far as the shipped levels show -
  8000 cm in all of them, so a 74 m cap).
- The view angle was not found in the code: the cone widths here are editor guidance per AI profile (a sniper
  narrow, a patrol wide), not game values.
- Mines: the shipped scenes place US_WPN_MineAntiTank objects (MINE03_ANTITANK, Missions 9, 10, 12A) - minefields copy
  that record. There is no anti-personnel mine to place: US_WPN_MineAntiPersonel's model (Mine02_AntiPersonnel) is in
  no level archive.
- Emplacements: tripod guns HMG01_M2HB (.50 cal) / HMG02_NSV (12.7 mm, the game's Soviet heavy MG - there is no
  DShK) are VEHICLE.TXT gun mounts; the gunner is a crew entry (unit, seat 1, script id) like a vehicle's, mounted at
  level start. Sandbag mounts and offsets are copied from the shipped scenes (Mission 5A M5_MG_SANDBAGMOUNT1, Mission 8
  M8_MG_NEST). The game has no mortar weapon or model (US_WPN_MORTOR_STRIKE is a scripted strike).
- Air support: message 0x0F (game.EVENT.LASER) is a query the script answers - 1 lets the laser designator mark its
  target - so the compiled Lua counts strikes and the re-arm time and refuses with a pop-up. The strike itself is the
  game's own; the aircraft / payload choice is written into the script as data (editor.air_support) because the
  strike's aircraft isn't mapped. The squad gets a designator pick-up (Mission 12B's Laser_designator) next to the
  first squad soldier. Not yet tried in game.

Minefields and air support live in the session (s.tactics, saved in <NAME>.editor.json and in undo snapshots through
logic.PRODUCTION_KEYS); the Lua lives between `-- @editor begin: tactics` and `-- @editor end: tactics`.
"""
from __future__ import annotations

import math
import random
import re

from commands import (CommandError, Session, command, GUN_MOUNTS, _catalog, _ENEMY, _NOT_ENEMY, _library,  # noqa: F401
                      _lua_value, _to_local, _turn, _vec, place, pretty_name)
import logic
from logic import _lua_text


def _need_level(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    return s.env


def _state(s: Session) -> dict:
    t = getattr(s, "tactics", None)
    if not isinstance(t, dict):
        t = {}
    t.setdefault("minefields", [])
    t.setdefault("air_support", {})
    s.tactics = t
    return t


def _template(s: Session, kind, model=None, unit=None):
    """Library index of the first shipped record with this model (props) / unit (objects)."""
    for i, e in enumerate(_library(s)):
        if e["kind"] != kind:
            continue
        if model and str(e["model"]).upper() != model.upper():
            continue
        if unit and str(e.get("unit") or "").upper() != unit.upper():
            continue
        return i
    raise CommandError(f"no shipped {kind} {model or unit} in the extracted levels")


def _record_by_id(s: Session, script_id):
    for r in s.env.find("HLOI"):
        if r.get("script_id") == script_id:
            return r
    return None


def _ground_y(s: Session, x, z, default):
    """The height of a terrain patch under (x, z) if one covers it, else `default` (the editor sends heights it
    sampled from the 3D view)."""
    for model, t in getattr(s, "terrains", {}).items():
        try:
            lx, lz = _to_local(s, model, (x, 0, z))
        except Exception:                        # noqa: BLE001 - a patch without its record
            continue
        half = t.size / 2
        if abs(lx) <= half and abs(lz) <= half:
            base = t.origin[1] if t.zone else next((r["pos"][1] for r in s.env.find("ENVI")
                                                    if str(r.get("model")).upper() == str(model).upper()), 0.0)
            return base + t.height_at(lx, lz)
    return default


# ======================================================================================= detection profiles ====
SIGHT_COL = 7                 # HSTAT.TXT column 7: sight in metres
UNAWARE = 0.75                # alert state 2 (the normal one) in FUN_00403bf0
# editor guidance, not game values (the view angle isn't decoded): degrees across, per AI profile
FOV_GUIDE = {"sniper": 50.0, "guard": 110.0, "ambush": 110.0, "patrol": 130.0, "assault": 140.0, "custom": 120.0}
HEAD = 160.0                  # cm above the record's position (standing)


def _is_enemy(r) -> bool:
    text = (str(r.get("model")) + " " + str(r.get("unit"))).upper()
    return (logic._is_soldier(r) and any(k in text for k in _ENEMY) and not any(k in text for k in _NOT_ENEMY))


def _night_factor(hour) -> float:
    """FUN_004cc190: full sight between 8 and 16 h, then down to 0.75 at 20 h / 4 h and below."""
    if hour is None or 8.0 < hour < 16.0:
        return 1.0
    return max(0.75, 1.0 - (abs(hour - 12.0) - 4.0) / 4.0)


def _sight_cap(s: Session) -> float:
    atm = logic.get_atmosphere(s)
    fog = atm.get("fog_stop")
    return ((float(fog) if fog else 7000.0) - 500.0) * 0.99


@command("Every enemy soldier's detection: position, facing, the cone the editor draws (fov_angle: editor guidance "
         "per AI profile - the game's view angle isn't decoded) and its sight ranges from the game's rules: range_m "
         "unaware (the normal state, x 0.75), alert_range_m (alerted / in combat), with the night factor of the "
         "scene's time of day and the fog cap. sight_source says where the base sight came from (the HLOI's ai_f2 or "
         "HSTAT.TXT column 7 of its soldier type).")
def soldier_detection_profiles(s: Session):
    env = _need_level(s)
    units, hstat = _catalog(s, "UNITS.TXT"), _catalog(s, "HSTAT.TXT")
    atm = logic.get_atmosphere(s)
    hour = atm.get("time_of_day")
    night = _night_factor(float(hour) if hour is not None else None)
    cap = _sight_cap(s)
    out = []
    for r in env.find("HLOI"):
        if not r.fields or not _is_enemy(r):
            continue
        try:
            own = float(r["ai_f2"])
        except KeyError:
            own = -1000.0
        if own > -999.0 and own != 0.0:
            sight, source = own * 100.0, "scene (ai_f2)"
        else:
            urow = (units.get(str(r["unit"]).upper()) or [[]])[0]
            hrow = (hstat.get(urow[4].upper()) or [[]])[0] if len(urow) > 4 else []
            try:
                sight, source = float(hrow[SIGHT_COL]) * 100.0, f"HSTAT {hrow[0]}"
            except (IndexError, ValueError):
                sight, source = 6000.0, "default (no HSTAT row)"
        profile = logic.profile_of(r) or "custom"
        unaware = min(sight * UNAWARE * night, cap)
        alert = min(sight, cap)               # alerted: no x 0.75 and no night factor (FUN_00403bf0)
        pos = list(r["pos"])
        out.append({"line": r.index + 1, "script_id": r["script_id"], "name": pretty_name(r),
                    "pos": pos, "eye": [pos[0], pos[1] + HEAD, pos[2]], "facing_yaw": r["rot"][1],
                    "fov_angle": FOV_GUIDE.get(profile, FOV_GUIDE["custom"]), "profile": profile,
                    "range_meters": round(unaware / 100.0, 1), "alert_range_meters": round(alert / 100.0, 1),
                    "sight_meters": round(sight / 100.0, 1), "sight_source": source,
                    "state": "unaware" if r["in_play"] else "waiting for the script"})
    return {"soldiers": out, "time_of_day": hour, "night_factor": round(night, 3), "cap_meters": round(cap / 100.0, 1),
            "notes": "Ranges follow the game's code; fov_angle is editor guidance (the view angle isn't decoded). "
                     "Hearing isn't modelled here: a shot is heard within its weapon's hearing range (WEAPS.TXT "
                     "column 64, 80 m for most guns, 8 m silenced)."}


# ================================================================================================ minefields ====
MINES = {"AT": {"unit": "US_WPN_MineAntiTank", "label": "anti-tank"}}
MINE_SPACING = 150.0          # cm between mines


@command("Scatter anti-tank mines (the shipped US_WPN_MineAntiTank object) in a circle, flush with the ground: "
         "positions = [[x, y, z], ...] places them exactly there (the editor's brush sends ground heights it sampled), "
         "otherwise `count` random spots at least 1.5 m apart at the centre's height (or a terrain patch's under "
         "them). The field is remembered (a hazard circle in the editor, minefield_delete removes it with its mines). "
         "mine_type AP is refused: the game has no anti-personnel mine model.", mutates=True,
         center_pos="[x, y, z] cm", radius="cm (default 1000)", count="mines (default 6, max 60)",
         mine_type="AT (AP isn't in the game data)", positions="optional exact [[x, y, z], ...]",
         seed="optional random seed")
def minefield_paint(s: Session, center_pos, radius=1000, count=6, mine_type="AT", positions=None, seed=None):
    _need_level(s)
    mine_type = str(mine_type).upper()
    if mine_type == "AP":
        raise CommandError("no anti-personnel mine: US_WPN_MineAntiPersonel's model (Mine02_AntiPersonnel) is in no "
                           "level archive - use AT")
    if mine_type not in MINES:
        raise CommandError("mine_type: AT")
    cx, cy, cz = _vec(center_pos)
    radius = float(radius)
    if not 100 <= radius <= 20000:
        raise CommandError("radius: 100 - 20000 cm")
    if positions:
        spots = [_vec(p) for p in positions][:60]
    else:
        count = int(count)
        if not 1 <= count <= 60:
            raise CommandError("count: 1 - 60")
        rnd = random.Random(seed)
        spots, tries = [], 0
        while len(spots) < count and tries < count * 200:
            tries += 1
            a, d = rnd.uniform(0, 2 * math.pi), radius * math.sqrt(rnd.random())
            x, z = cx + d * math.sin(a), cz + d * math.cos(a)
            if all(math.hypot(x - p[0], z - p[2]) >= MINE_SPACING for p in spots):
                spots.append((x, _ground_y(s, x, z, cy), z))
    if not spots:
        raise CommandError("no room for a mine")
    tpl = _template(s, "object", unit=MINES[mine_type]["unit"])
    rnd = random.Random(seed)
    ids = []
    for p in spots:
        r = place(s, tpl, p, yaw=rnd.uniform(0, 360))
        ids.append(r["script_id"])
    st = _state(s)
    field = {"id": max([f["id"] for f in st["minefields"]], default=0) + 1, "center": [cx, cy, cz],
             "radius": radius, "type": mine_type, "script_ids": ids}
    st["minefields"] = st["minefields"] + [field]
    return {"action": f"Paint Minefield ({len(ids)} {MINES[mine_type]['label']} mines)", "minefield": field,
            "lines": [_record_by_id(s, i).index + 1 for i in ids]}


@command("Remove a minefield: its remaining mines and its hazard circle.", mutates=True, id="minefield id")
def minefield_delete(s: Session, id):  # noqa: A002
    st = _state(s)
    field = next((f for f in st["minefields"] if f["id"] == int(id)), None)
    if field is None:
        raise CommandError(f"no minefield {id}")
    removed = 0
    for sid in field["script_ids"]:
        r = _record_by_id(s, sid)
        if r is not None and str(r.get("unit")).upper() == MINES[field["type"]]["unit"].upper():
            s.env.delete(r)
            removed += 1
    st["minefields"] = [f for f in st["minefields"] if f["id"] != int(id)]
    return {"action": f"Delete Minefield ({removed} mines)", "removed": removed}


# ============================================================================================== emplacements ====
# prop + gun: the gun's offset in the prop's own frame (cm) and its facing relative to the prop, from shipped pairs
EMPLACEMENTS = {
    "M2_50Cal": {"label": "Sandbag Bunker + .50 Cal Nest", "prop": "M5_MG_SANDBAGMOUNT1", "gun": "HMG01_M2HB",
                 "offset": (30.2, 125.0, -14.0), "gun_yaw": 180.0,
                 "source": "Mission 5A: M5_MG_SANDBAGMOUNT1 with its tripod gun (M2HB on the same tripod model)"},
    "NSV_127": {"label": "MG Nest + NSV 12.7 mm", "prop": "M8_MG_NEST", "gun": "HMG02_NSV",
                "offset": (276.4, 105.5, -13.6), "gun_yaw": 90.0,
                "source": "Mission 8: M8_MG_NEST with its NSV (the game's Soviet heavy MG - there is no DShK)"},
    "NSV_Rooftop": {"label": "Rooftop NSV 12.7 mm (gun only)", "prop": None, "gun": "HMG02_NSV",
                    "offset": (0.0, 0.0, 0.0), "gun_yaw": 0.0,
                    "source": "Mission 8's bridge NSV: a tripod gun on its own - place it on a roof"},
}
ALIASES = {"DSHK_127": "NSV_127", "DSHK": "NSV_127", "M2": "M2_50Cal", "50CAL": "M2_50Cal"}


def _preset(name):
    key = ALIASES.get(str(name).upper().replace(" ", "_"), name)
    key = next((k for k in EMPLACEMENTS if k.upper() == str(key).upper()), None)
    if key is None:
        if "MORTAR" in str(name).upper():
            raise CommandError("no mortar in the game: no mortar weapon or model (US_WPN_MORTOR_STRIKE is a scripted "
                               "strike) - presets: " + ", ".join(EMPLACEMENTS))
        raise CommandError("preset is one of " + ", ".join(EMPLACEMENTS))
    return key, EMPLACEMENTS[key]


@command("Emplacement presets place_emplacement offers, with the shipped records they copy.")
def emplacement_presets(s: Session):
    return {k: {"label": p["label"], "prop": p["prop"], "gun": p["gun"], "source": p["source"]}
            for k, p in EMPLACEMENTS.items()}


@command("Stamp a heavy-weapon emplacement: the sandbag revetment prop (ENVI), the tripod gun (HLOI) facing `yaw`, and "
         "with_gunner = an enemy soldier in its gunner seat (crew list, mounted at level start). Presets: M2_50Cal "
         "(sandbag mount + .50 cal), NSV_127 (MG nest + NSV - also for DShK_127, the game has no DShK), NSV_Rooftop "
         "(gun only). There is no mortar in the game data.", mutates=True,
         preset="M2_50Cal / NSV_127 / NSV_Rooftop", pos="[x, y, z] cm of the revetment (the gun for NSV_Rooftop)",
         yaw="degrees the gun faces", with_gunner="put an enemy gunner on it (default true)")
def place_emplacement(s: Session, preset, pos, yaw=0.0, with_gunner=True):
    import studio
    _need_level(s)
    key, p = _preset(preset)
    pos = _vec(pos)
    yaw = float(yaw) % 360.0
    out = {"preset": key}
    if p["prop"]:
        prop_yaw = (yaw - p["gun_yaw"]) % 360.0
        out["prop_line"] = place(s, _template(s, "prop", model=p["prop"]), pos, yaw=prop_yaw)["line"]
        off = _turn(p["offset"], prop_yaw)
        gun_pos = (pos[0] + off[0], pos[1] + off[1], pos[2] + off[2])
    else:
        gun_pos = pos
    gun = place(s, _template(s, "object", unit=p["gun"]), gun_pos, yaw=yaw)
    out["script_id"] = gun["script_id"]
    line = _record_by_id(s, gun["script_id"]).index + 1
    if with_gunner:                              # (the ENVI section comes before the HLOIs: the prop line stays)
        card = studio.assign_crew(s, line, 1, spawn=True)
        out["gunner_line"] = card.get("spawned")
        line = _record_by_id(s, gun["script_id"]).index + 1
    out["gun_line"] = line
    out["action"] = f"Place {p['label']}" + (" with Gunner" if with_gunner else "")
    return out


# ============================================================================================== air support ====
STRIKES = {"A10_Maverick": {"aircraft": "A-10 Thunderbolt II", "payload": "AGM-65 Maverick"},
           "A10_GunRun": {"aircraft": "A-10 Thunderbolt II", "payload": "GAU-8 gun run"},
           "Apache_Hellfire": {"aircraft": "AH-64 Apache", "payload": "AGM-114 Hellfire"}}
DESIGNATOR = "Laser_designator"


def _squad_leader(s):
    heroes = [r for r in s.env.find("HLOI") if r.fields and str(r.get("model")).upper().startswith("HERO")]
    return heroes[0] if heroes else None


@command("Air support for the mission: laser-designated strikes the squad can call. Stored with the mission and "
         "compiled into its Lua on save: the script answers the game's laser query (EVENT.LASER) - allowed while "
         "strikes are left and the re-arm time has passed, else a pop-up says why. strike_type A10_Maverick / "
         "A10_GunRun / Apache_Hellfire is written into the script as data (the strike the game flies is its own - its "
         "aircraft isn't mapped). give_designator places Mission 12B's laser designator pick-up next to the first "
         "squad soldier (removed again when air support is switched off).", mutates=True,
         enabled="on / off", strike_type="A10_Maverick / A10_GunRun / Apache_Hellfire", strikes="strikes (1-10)",
         cooldown="seconds between strikes (0-600)", give_designator="place a designator pick-up (default true)")
def set_air_support(s: Session, enabled=True, strike_type="A10_Maverick", strikes=2, cooldown=60,
                    give_designator=True):
    _need_level(s)
    st = _state(s)
    old = dict(st["air_support"])
    if strike_type not in STRIKES:
        raise CommandError("strike_type is one of " + ", ".join(STRIKES))
    strikes, cooldown = int(strikes), float(cooldown)
    if not 1 <= strikes <= 10:
        raise CommandError("strikes: 1 - 10")
    if not 0 <= cooldown <= 600:
        raise CommandError("cooldown: 0 - 600 s")
    cfg = {"enabled": bool(enabled), "strike_type": strike_type, "strikes": strikes, "cooldown": cooldown,
           **STRIKES[strike_type], "designator_id": old.get("designator_id")}
    have = _record_by_id(s, cfg["designator_id"]) if cfg["designator_id"] is not None else None
    if have is not None and str(have.get("unit")).upper() != DESIGNATOR.upper():
        have, cfg["designator_id"] = None, None
    if enabled and give_designator and have is None:
        leader = _squad_leader(s)
        if leader is None:
            raise CommandError("no squad soldier in this scene to leave the designator next to")
        lp = leader["pos"]
        off = _turn((120.0, 0.0, 60.0), leader["rot"][1])
        cfg["designator_id"] = place(s, _template(s, "object", unit=DESIGNATOR),
                                     (lp[0] + off[0], lp[1], lp[2] + off[2]))["script_id"]
    elif (not enabled or not give_designator) and have is not None:
        s.env.delete(have)
        cfg["designator_id"] = None
    st["air_support"] = cfg
    what = (f"{cfg['aircraft']} {cfg['payload']}, {strikes} strike(s), {cooldown:g} s re-arm"
            if enabled else "off")
    return {"action": f"Set Air Support ({what})", "air_support": cfg}


@command("The tactical layer for the viewport: minefields (centre, radius, mines still in the scene), air support, "
         "emplacement presets and the Lua the editor writes for it.")
def tactics_info(s: Session):
    _need_level(s)
    st = _state(s)
    ids = {r.get("script_id") for r in s.env.find("HLOI")}
    fields = [{**f, "remaining": [i for i in f["script_ids"] if i in ids]} for f in st["minefields"]]
    return {"minefields": fields, "air_support": st["air_support"], "strike_types": STRIKES,
            "presets": emplacement_presets(s), "lua": block_text(s), "written": has_tactics(s)}


# ============================================================================================== script block ====
TAC_BEGIN, TAC_END = "-- @editor begin: tactics", "-- @editor end: tactics"
HOOK_INIT = "    editor_tactics_init()   -- the editor's air support (Mission Settings > Air Support)"
# a query is answered by on_message's return value: this line returns only for EVENT.LASER
HOOK_MSG = "    local tactics_answer = editor_tactics_on_message({args})   -- air support (answers EVENT.LASER)\n" \
           "    if tactics_answer ~= nil then return tactics_answer end"
HOLD_MS = 15000               # the same target keeps its yes this long (the query may come every frame while aiming)


def has_tactics(s) -> bool:
    return bool((getattr(s, "tactics", None) or {}).get("air_support", {}).get("enabled"))


def block_text(s) -> str:
    a = (getattr(s, "tactics", None) or {}).get("air_support") or {}
    on = bool(a.get("enabled"))
    L = [f"{TAC_BEGIN} (air support - rewritten by the editor on every save: Mission Settings > Air Support)",
         "editor = editor or {}",
         f"editor.air_support = {{ enabled = {'true' if on else 'false'}, strike = {_lua_value(a.get('strike_type', ''))}, "
         f"aircraft = {_lua_text(a.get('aircraft', ''))}, payload = {_lua_text(a.get('payload', ''))}, "
         f"strikes = {int(a.get('strikes', 0))}, cooldown = {float(a.get('cooldown', 0))!r} }}",
         "function editor.air_state()   -- strikes used, kept in save games",
         "    game.state.editor_air = game.state.editor_air or { used = 0, last = -1000000, target = nil, until_ms = 0 }",
         "    return game.state.editor_air",
         "end",
         "function editor_tactics_init() end   -- called from init()",
         "function editor_tactics_on_message(id, a, b)   -- called first in on_message(); nil = not answered here",
         "    local cfg = editor.air_support",
         "    if id ~= game.EVENT.LASER or not cfg.enabled then return nil end",
         "    local st, now = editor.air_state(), game.time_ms()",
         f"    if st.target == b and now < st.until_ms then return 1 end   -- still marking the approved target",
         "    if st.used >= cfg.strikes then",
         "        game.popup(\"No air support left\", 3)",
         "        return 0",
         "    end",
         "    local wait = cfg.cooldown * 1000 - (now - st.last)",
         "    if wait > 0 then",
         "        game.popup(string.format(\"Air support re-arming: %d s\", math.ceil(wait / 1000)), 3)",
         "        return 0",
         "    end",
         f"    st.used, st.last, st.target, st.until_ms = st.used + 1, now, b, now + {HOLD_MS}",
         "    game.popup(string.format(\"%s inbound (%s) - %d strike(s) left\", cfg.aircraft, cfg.payload, "
         "cfg.strikes - st.used), 4)",
         "    return 1",
         "end",
         TAC_END]
    return "\n".join(L)


def update_text(text, s) -> str:
    """The script with a fresh tactics block (before init(), or at the end) and its hook lines - once the mission
    has air support switched on, or the block is there already."""
    a = text.find(TAC_BEGIN)
    if a < 0 and not has_tactics(s):
        return text
    block = block_text(s)
    if a >= 0:
        b = text.find(TAC_END, a)
        text = text[:a] + block + text[b + len(TAC_END):] if b > a else text
    else:
        m = re.search(r"^function init\(\)", text, re.M)
        at = m.start() if m else len(text)
        text = text[:at] + block + "\n\n" + text[at:]
    own = re.sub(re.escape(TAC_BEGIN) + r".*?" + re.escape(TAC_END), "", text, flags=re.S)
    if not re.search(r"^\s*editor_tactics_init\(\)", own, re.M):
        m = re.search(r"^function init\(\)[^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + HOOK_INIT + "\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\nfunction init()\n" + HOOK_INIT + "\nend\n"
    if not re.search(r"editor_tactics_on_message\(", own):
        m = re.search(r"^function on_message\(([^)]*)\)[^\n]*\n", text, re.M)
        if m:
            params = [p.strip() for p in m.group(1).split(",") if p.strip() and p.strip() != "..."]
            args = (params + ["nil", "nil", "nil"])[:3]
            text = text[:m.end()] + HOOK_MSG.format(args=", ".join(args)) + "\n" + text[m.end():]
        else:
            text = (text.rstrip("\n") + "\n\nfunction on_message(id, a, b)\n" + HOOK_MSG.format(args="id, a, b")
                    + "\nend\n")
    return text


def update_script(path, s):
    import os
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()
    new = update_text(text, s)
    if new != text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)


def strip(text):
    """The script without the tactics block and its hooks (to compare scripts)."""
    text = re.sub(re.escape(TAC_BEGIN) + r".*?" + re.escape(TAC_END) + r"\n*", "", text, flags=re.S)
    return "\n".join(l for l in text.splitlines()
                     if "editor_tactics_init()" not in l and "editor_tactics_on_message(" not in l
                     and "tactics_answer ~= nil" not in l)
