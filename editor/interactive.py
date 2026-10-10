"""Editor commands for switches (keypads, gate controls) and particle emitters (smoke, fire, dust). Registered into
commands.py's command table like logic.py, so the web UI and the MCP server get the same commands.

Switches (scene SWITCH lines, conflict_ds_tools/env.py; game: switches.cpp FUN_004f69a0 / FUN_004f7220): a switch is
a Switches.txt type (two models: off / on, e.g. KeyPad_Red / KeyPad_Green), a REGN the player must stand in, and up to
two door objects (HLOI script ids whose unit is a Doors.txt row). Using it toggles the models, opens / closes both doors
through the door code (other switches on the same doors follow) and sends script message 0x0D (game.EVENT.SWITCH) with
a = the archive hash of the switch's name, b = 1.0, c = the soldier - which the editor's "switch used" rule trigger
listens for. The game has no lock or key for these: a door a switch drives just opens.

Emitters ("* ..." rows between <EMIT> and </EMIT>; game: Particles.cpp FUN_004f2d70, run by FUN_004f2840): a particle
type (4 flash, 6 smoke, 8 / 9 dust, 11 fire, 12 flame, 18 flare - named after the shipped emitters that use them),
spawn interval, velocity / size / life ranges, colour, optionally a REGN to spawn anywhere inside (stored as the
region name's archive hash) or a parent object to follow. New emitters start as a copy of a shipped one (a preset).
"""
from __future__ import annotations

import os

from commands import CommandError, Session, command, _catalog, _find_file, _nearest_zone, _unique, _vec  # noqa: F401
from conflict_ds_tools.archive import archive_hash

PARTICLE_TYPES = {4: "Flash / flak burst", 6: "Smoke", 8: "Dust (small)", 9: "Dust (large)", 11: "Fire", 12: "Flame",
                  18: "Flare"}
DEFAULT_SWITCH = "IR_SWITCH_HangerDoor"
AUTO_LINK = 3000         # cm: switch_add links the nearest door only within this distance
_PRESETS = None          # [{id, name, level, type, label, text}] from every extracted scene (built once)


def signed(h: int) -> int:
    """A hash as the game hands it to Lua (int32)."""
    h &= 0xFFFFFFFF
    return h - (1 << 32) if h & 0x80000000 else h


def _scene(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    return s.env


def _switch_types(s: Session) -> dict:
    """Switches.txt: {TYPE (upper): {type, model_off, model_on}} (catalog.dat, extracted)."""
    out = {}
    for key, rows in _catalog(s, "SWITCHES.TXT").items():
        r = rows[0]
        out[key] = {"type": r[0], "model_off": r[1] if len(r) > 1 else "", "model_on": r[2] if len(r) > 2 else ""}
    return out


def _door_units(s: Session) -> set[str]:
    return set(_catalog(s, "DOORS.TXT"))


def doors(s: Session) -> list[dict]:
    """Door objects of the scene: HLOI records whose unit is a Doors.txt row (IR_DOOR_* in the shipped data)."""
    units = _door_units(s)
    out = []
    for r in _scene(s).find("HLOI"):
        if not r.fields:
            continue
        u = str(r["unit"])
        if u.upper() in units or (not units and u.upper().startswith("IR_DOOR")):
            out.append({"line": r.index + 1, "script_id": r["script_id"], "unit": u, "model": r["model"],
                        "pos": list(r["pos"])})
    return out


def _regions(s: Session) -> dict[str, object]:
    return {str(r["name"]): r for r in _scene(s).find("REGN") if r.fields}


def switch_models(s: Session, rec) -> list[str]:
    """The two models a SWITCH line shows (for packing into a mission)."""
    t = _switch_types(s).get(str(rec.get("type")).upper())
    return [m for m in (t["model_off"], t["model_on"]) if m and m.upper() != "NONE"] if t else []


def decorate_scene(s: Session, out: dict):
    """scene command: switches (with their doors and region) and emitters (with their region's name)."""
    env = _scene(s)
    by_hash = {archive_hash(n): n for n in _regions(s)}
    ids = {r["script_id"]: r for r in env.find("HLOI") if r.fields}
    types = _switch_types(s)
    out["switches"], out["emitters"] = [], []
    for r in env.records:
        if not r.fields:
            continue
        if r.kind == "SWITCH":
            targets = [t for t in (r["target_a"], r["target_b"]) if t != -1]
            t = types.get(str(r["type"]).upper(), {})
            out["switches"].append({
                "line": r.index + 1, "name": r["name"], "pos": list(r["pos"]), "rot": list(r["rot"]),
                "type": r["type"], "model": t.get("model_off") or "", "region": r["region"], "targets": targets,
                "target_pos": [list(ids[i]["pos"]) for i in targets if i in ids],
                "missing": [i for i in targets if i not in ids] + ([] if r["region"] in _regions(s) else [r["region"]]),
                "message_a": signed(archive_hash(r["name"]))})
        elif r.kind == "EMITTER":
            out["emitters"].append({
                "line": r.index + 1, "name": r["name"], "pos": list(r["pos"]), "type": r["type"],
                "type_name": PARTICLE_TYPES.get(r["type"], f"type {r['type']}"),
                "region": by_hash.get(r["region"] & 0xFFFFFFFF) if r.get("region") else None,
                "parent": None if r["parent"] == "-NULL_OBJECT-" else r["parent"],
                "velocity": [(a + b) / 2 for a, b in zip(r["velocity_min"], r["velocity_max"])],
                "size": r["size"], "on": bool(r["flags"] & 1),
                "colour": [r["colour_r"], r["colour_g"], r["colour_b"], r["colour_a"]]})


# ------------------------------------------------------------------------------------------------- emitters --
def presets(s: Session) -> list[dict]:
    """Every shipped emitter (all extracted scenes) as a template, named after its scene row."""
    global _PRESETS
    if _PRESETS is None:
        from conflict_ds_tools.env import EnvFile
        found, seen = [], set()
        for d in sorted(os.listdir(s.data_dir)):
            path = _find_file(os.path.join(s.data_dir, d), d.upper() + ".ENV")
            if not path:
                continue
            try:
                env = EnvFile.load(path)
            except (OSError, UnicodeError):
                continue
            for r in env.find("EMITTER"):
                if not r.fields:
                    continue
                key = (str(r["name"]).upper(), r["type"])
                if key in seen or str(r["name"]).isdigit():
                    continue
                seen.add(key)
                found.append({"name": r["name"], "level": d.upper(), "type": r["type"],
                              "type_name": PARTICLE_TYPES.get(r["type"], f"type {r['type']}"),
                              "region": bool(r.get("region")), "parent": r["parent"] != "-NULL_OBJECT-",
                              "text": r.text, "version": env.version})
        found.sort(key=lambda p: (p["type_name"], p["name"].upper()))
        for i, p in enumerate(found):
            p["id"] = i
        _PRESETS = found
    return _PRESETS


@command("Switch types (Switches.txt), door objects of the open level, its regions and the emitter presets (every "
         "shipped emitter, grouped by particle type) for the switch / emitter tools.")
def interactive_options(s: Session):
    _scene(s)
    return {"switch_types": list(_switch_types(s).values()), "doors": doors(s), "regions": sorted(_regions(s)),
            "particle_types": {str(k): v for k, v in PARTICLE_TYPES.items()},
            "emitter_presets": [{k: v for k, v in p.items() if k != "text"} for p in presets(s)]}


def _box_region(s: Session, name, pos, half):
    env = _scene(s)
    end = next((r.index for r in env.records if r.kind == "ENDREGIONS"), None)
    if end is None:
        raise CommandError("this scene has no regions section")
    x, y, z = pos
    env.lines.insert(end, f'REGN "{name}" 0 {x:.6f} {y:.6f} {z:.6f} 0.000000 0.000000 0.000000 '
                          f'{-half:.6f} {-half:.6f} {-half:.6f} {half:.6f} {half:.6f} {half:.6f} ')
    env._parse()                                 # the region count before the REGN lines follows (env._sync_counts)
    return env.find("REGN", name=name)[0]


def _door_pair(s: Session, door_a):
    """The door a switch should drive when none is given (the nearest), and its other half (a door within 8 m whose
    unit differs only by Left / Right - the hangar / main gate halves)."""
    ds = doors(s)
    if not ds:
        return -1, -1
    a = next((d for d in ds if d["script_id"] == door_a), None)
    if a is None:
        return -1, -1
    stem = a["unit"].upper().replace("LEFT", "").replace("RIGHT", "")
    near = [d for d in ds if d is not a and d["unit"].upper() != a["unit"].upper()
            and d["unit"].upper().replace("LEFT", "").replace("RIGHT", "") == stem
            and sum((p - q) ** 2 for p, q in zip(d["pos"], a["pos"])) < 800 ** 2]
    near.sort(key=lambda d: sum((p - q) ** 2 for p, q in zip(d["pos"], a["pos"])))
    return a["script_id"], near[0]["script_id"] if near else -1


def _check_door(s: Session, sid):
    sid = int(sid)
    if sid == -1:
        return sid
    if sid not in {d["script_id"] for d in doors(s)}:
        hit = _scene(s).find("HLOI", script_id=sid)
        if not hit:
            raise CommandError(f"no object with script id {sid}")
        raise CommandError(f"object {sid} ({hit[0]['unit']}) isn't a door - switches drive objects whose unit is a "
                           "Doors.txt row (place one from the Library: IR_DOOR_...)")
    return sid


@command("Add a switch (keypad / gate control) that opens and closes up to two doors: it stands at pos facing yaw, a "
         "box region of use_size cm around it is where the player can use it. Without doors the nearest door object "
         "within 30 m and its other half are linked. Using it sends game.EVENT.SWITCH (a = message_a), which rules can react to.",
         mutates=True, pos="[x, y, z]", door_a="script id of a door (-1 none; default: the nearest door within 30 m)",
         door_b="script id of the second door (-1 none; default: door_a's other half)", yaw="facing in degrees",
         type="Switches.txt type (default IR_SWITCH_HangerDoor = a red / green keypad)",
         use_size="width of the use region in cm (default 200)", name="switch name (default switch_<n>)")
def switch_add(s: Session, pos, door_a=None, door_b=None, yaw=0.0, type=DEFAULT_SWITCH, use_size=200, name=None):
    env = _scene(s)
    pos = _vec(pos)
    types = _switch_types(s)
    if types and str(type).upper() not in types:
        raise CommandError(f"unknown switch type {type} - one of {', '.join(t['type'] for t in types.values())}")
    if types:
        type = types[str(type).upper()]["type"]
    if door_a is None:                           # the nearest door, if it is close enough to be the one meant
        ds = sorted(doors(s), key=lambda d: sum((p - q) ** 2 for p, q in zip(d["pos"], pos)))
        near = ds and sum((p - q) ** 2 for p, q in zip(ds[0]["pos"], pos)) <= AUTO_LINK ** 2
        door_a = ds[0]["script_id"] if near else -1
        if door_b is None:
            door_a, door_b = _door_pair(s, door_a)
    door_a = _check_door(s, door_a)
    door_b = _check_door(s, -1 if door_b is None else door_b)
    if door_a == -1 and door_b != -1:
        door_a, door_b = door_b, -1
    name = _unique(s, "SWITCH", "name", str(name or f"switch_{len(env.find('SWITCH'))}"))
    half = max(50.0, min(2000.0, float(use_size) / 2))
    region = _unique(s, "REGN", "name", f"{name.upper()}_USE")
    zone = _nearest_zone(s, "HLOI", pos) or "Root"
    reg = _box_region(s, region, pos, half)
    number = max([r["number"] for r in env.find("SWITCH") if r.fields and "number" in r.fields] + [-1]) + 1
    x, y, z = pos
    text = (f'SWITCH "{name}" "{zone}"  {x:.2f} {y:.2f} {z:.2f}  0.00 {float(yaw) % 360:.2f} 0.00  "{type}" '
            f'"{region}" {door_a} {door_b}' + (f" {number} " if env.version >= 62 else " "))
    try:
        rec = env.insert("SWITCH", text)
    except ValueError as e:
        env.delete(reg)
        raise CommandError(str(e)) from None
    return {"line": rec.index + 1, "name": name, "region": region, "region_line": reg.index + 1,
            "doors": [d for d in (door_a, door_b) if d != -1], "message_a": signed(archive_hash(name))}


@command("Change a switch: its doors (script ids, -1 none), type, or the size of its use region (cm).", mutates=True,
         line="SWITCH line", door_a="script id or -1", door_b="script id or -1", type="Switches.txt type",
         use_size="use region width in cm")
def switch_set(s: Session, line, door_a=None, door_b=None, type=None, use_size=None):
    r = s.record(line)
    if r.kind != "SWITCH":
        raise CommandError(f"line {line} is a {r.kind}, not a switch")
    if door_a is not None:
        r["target_a"] = _check_door(s, door_a)
    if door_b is not None:
        r["target_b"] = _check_door(s, door_b)
    if type is not None:
        types = _switch_types(s)
        if types and str(type).upper() not in types:
            raise CommandError(f"unknown switch type {type}")
        r["type"] = types[str(type).upper()]["type"] if types else str(type)
    if use_size is not None:
        reg = _regions(s).get(str(r["region"]))
        if reg is None or reg["type"] != 0:
            raise CommandError(f"the switch's region {r['region']} isn't a box in this scene")
        half = max(50.0, min(2000.0, float(use_size) / 2))
        reg["min"], reg["max"] = (-half, -half, -half), (half, half, half)
    return {"line": int(line), "fields": s.record(line).as_dict()}


@command("Add a particle emitter at pos: a copy of a preset (interactive_options lists them; default: a smoke column), "
         "optionally spawning anywhere inside a region instead of at pos.", mutates=True, pos="[x, y, z]",
         preset="emitter preset id", name="name (default: the preset's, made unique)",
         region="region name to spawn inside (optional)")
def emitter_add(s: Session, pos, preset=None, name=None, region=None):
    env = _scene(s)
    ps = presets(s)
    if not ps:
        raise CommandError("no emitter presets - the data folder has no extracted scenes")
    if preset is None:
        p = next((p for p in ps if p["name"].upper() == "CAMP_FIRE_SMOKE"), ps[0])
    elif not 0 <= int(preset) < len(ps):
        raise CommandError("unknown preset")
    else:
        p = ps[int(preset)]
    if p["version"] != env.version and (p["version"] >= 53) != (env.version >= 53):
        raise CommandError("that preset comes from a scene version with another emitter layout")
    try:
        rec = env.insert("EMITTER", p["text"])
    except ValueError as e:
        raise CommandError(str(e)) from None
    rec["pos"] = _vec(pos)
    env._replace_token(rec, rec.fields["parent"], "-NULL_OBJECT-")   # the preset's parent isn't in this scene
    rec = s.record(rec.index + 1)
    rec["flags"] = rec["flags"] | 1              # on from the start (shipped ones the script turns on start off)
    if "growth" in rec.fields and rec["flags"] & 0x20 and abs(rec["growth"]) > 1e6:
        rec["growth"] = 0.0                      # unset in the shipped row (0xCDCDCDCD) and unused with flag 0x20
    rec["name"] = _unique(s, "EMITTER", "name", str(name or p["name"]))
    if "region" in rec.fields:
        rec["region"] = 0
    if region:
        emitter_set(s, rec.index + 1, region=region)
    return {"line": rec.index + 1, "name": rec["name"], "preset": p["name"], "type": p["type_name"]}


@command("Change an emitter: spawn region (name, '' = at its position), colour [r, g, b, a] 0..1, on (emits from the "
         "start) or particle type.", mutates=True, line="emitter line", region="region name or ''",
         colour="[r, g, b, a]", on="true / false", type="particle type number")
def emitter_set(s: Session, line, region=None, colour=None, on=None, type=None):
    r = s.record(line)
    if r.kind != "EMITTER":
        raise CommandError(f"line {line} is a {r.kind}, not an emitter")
    if region is not None:
        if "region" not in r.fields:
            raise CommandError("this scene's emitters have no region field (scene version < 53)")
        if region and region not in _regions(s):
            raise CommandError(f"no region {region!r} in this scene")
        r["region"] = signed(archive_hash(region)) if region else 0
    if colour is not None:
        c = [float(v) for v in colour]
        if len(c) != 4 or not all(0 <= v <= 1 for v in c):
            raise CommandError("colour: [r, g, b, a], each 0 - 1")
        for ch, v in zip("rgba", c):
            r[f"colour_{ch}"] = v
    if on is not None:
        r["flags"] = (r["flags"] | 1) if on else (r["flags"] & ~1)
    if type is not None:
        if int(type) not in PARTICLE_TYPES:
            raise CommandError(f"particle type: one of {', '.join(map(str, PARTICLE_TYPES))}")
        r["type"] = int(type)
    return {"line": int(line), "fields": s.record(line).as_dict()}
