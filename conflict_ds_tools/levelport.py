"""Level porter (docs/PORTING_PLAN.md, Part 3): a source game's level as a Desert Storm `.lua` level mod.

Desert Storm II's levels are built like Desert Storm's: the scene (<LEVEL>.ENV, version 106 against Desert Storm's 72)
names zone models whose objects are world-space pieces of the ground, each with a record of its own, props parented to
those pieces, portals between the zones, units, waypoints and regions. So a level ports in two halves:

- zone models (`port_zone`): object by object onto a Desert Storm world-piece object (geometry per texture, collision
  rebuilt with Desert Storm's surfaces), names kept - the scene's records find the pieces by name;
- the scene: its records rewritten in Desert Storm's layout (scene part, next step).

Nothing of the source game is shipped: the porter runs on the player's PC from their own copy of both games.
"""
from __future__ import annotations

import os
import re
import struct

from . import bsp, evo, evo_write, kits
from .porting import PortError, _texture_files, static_model_parts

# Desert Storm's world-piece object the zone objects are written onto: Mission 1's MIS1_ZONE2, object 1 (geometry and
# a collision tree, type 5, parent ROOT)
ZONE_TEMPLATE = ("MIS1_ZONE2", 1)


def _object_template(dst):
    """One Desert Storm world-piece object as a model of its own (type 5 and parent ROOT kept, last object)."""
    name, index = ZONE_TEMPLATE
    data = dst.get(name + ".EVO")
    if data is None:
        raise PortError("Desert Storm's %s.EVO (the world-piece template) isn't in its files" % name)
    start, end = kits.objects(data)[index]
    obj = bytearray(data[start:end])
    obj[-4:] = b"DONE"
    return bytes(obj)


def _rename(obj: bytes, name: str, last: bool) -> bytes:
    out = bytearray(obj)
    raw = name.encode("latin-1")[:63]
    out[8:8 + 64] = raw + b"\0" * (64 - len(raw))
    out[-4:] = b"DONE" if last else b"NEXT"
    return bytes(out)


def port_zone(src, dst, model, prefix=None):
    """(zone model bytes, {texture file: bytes}, notes) - the source zone model `model` (all its world-space objects)
    in Desert Storm's format: one object per source object, same order and names, so the scene's chunk records
    (`<MODEL>nnn`) still find theirs. Textures are renamed with the source game's prefix (DS2_...)."""
    data = src.get(model + ".EVO")
    if data is None:
        raise PortError("%s.EVO isn't in the %s files" % (model, src.game.short))
    prefix = src.game.id.upper() + "_" if prefix is None else prefix
    scene = evo.Scene(evo.read(data))
    template = _object_template(dst)
    tscene = evo.Scene(evo.read(template))
    inv = evo.mat_invert_affine(tscene.objects[0].world)
    names = [n for n in (o.name for o in scene.objects)]
    out, textures, notes, all_parts, built = [], {}, [], [], []
    empty = surfaces = 0
    converted = {}
    for i, o in enumerate(scene.objects):
        # render_trees: Desert Storm II leaves the ground beside its vehicles' paths without collision (only a u 1
        # "render only" tree, which its loader skips too - its drivers stay on the road); Desert Storm's AI drivers
        # swing wide and reverse at bends, and the squad Land Rover in Mission 1 backed onto such a slope and fell
        # through the world. The ported pieces get those polygons as collision.
        parts, tris, _points, pnotes, _shadow = static_model_parts(scene, prefix, keep=lambda x, i=i: x.index == i,
                                                                   render_trees=True)
        all_parts += parts
        coll = []
        for (a, b, c), s in tris:
            new, old = bsp.convert_surface(s, dst.game.id)
            if old:
                converted[old] = bsp.surface_name(new)
            coll.append(((tuple(evo.mat_apply(inv, a)), tuple(evo.mat_apply(inv, b)), tuple(evo.mat_apply(inv, c))), new))
        surfaces += len(coll)
        for p in parts:
            p.positions = [tuple(evo.mat_apply(inv, v)) for v in p.positions]
            p.normals = [tuple(evo.mat_apply(inv, n, 0.0)) for n in p.normals]
        if not parts:
            empty += 1
        built.append((parts, coll))
    # textures first: they decide flags of the parts (alpha-tested textures)
    textures.update(_texture_files(src, dst, all_parts, model, prefix, notes))
    for i, (parts, coll) in enumerate(built):
        warn = []
        with dst.current():
            obj = evo_write.rebuild(template, {0: parts}, model_name=names[i] or model, warnings=warn,
                                    collision={0: coll})
        notes += ["%s: %s" % (names[i], w) for w in warn]
        out.append(_rename(obj, names[i] or model, i == len(built) - 1))
    if converted:
        notes.append("surfaces %s has no row for: %s" % (dst.game.short, ", ".join(
            "%s -> %s" % kv for kv in sorted(converted.items()))))
    notes.insert(0, "%d object(s), %d collision triangle(s), %d texture(s)%s" % (
        len(out), surfaces, len(textures), ", %d without render geometry" % empty if empty else ""))
    return b"".join(out), textures, notes


# ------------------------------------------------------------------------------------------------- scene --

def _q(s):
    return '"%s"' % s


def _f(x):
    return "%.6f" % float(x)


def _v3(v):
    return " ".join(_f(x) for x in v)


def envi_line(name, parent, kind, model, texture, pos, rot, scale, lights=(0,)):
    """An ENVI record in Desert Storm's layout (version 72, loader 0x4F8D00): flags, an int, name, parent, type,
    model, texture, position, rotation, scale, two ints, the lights that light it, an id (-1 = none, like the
    editor's own pieces). No flags: no breakable block, no RINFO line after it."""
    return "ENVI 0 0 %s %s %d %s %s %s %s %s 0 0 %d %s -1 " % (
        _q(name), _q(parent), kind, _q(model), _q(texture), _v3(pos), _v3(rot), _v3(scale), len(lights),
        " ".join(str(x) for x in lights))


def port_line(r):
    """A PORT record in Desert Storm's layout (version 72): Desert Storm II's adds a list of int pairs at the end."""
    n = r["vertex_count"]
    verts = " ".join(_v3(r["vertex%d" % k]) for k in range(n))
    return "PORT %s %d %s %s %d %d %s %s %d %s %s %s %d %d " % (
        _q(r["name"]), r["id"], _q(r["zone_a"]), _q(r["zone_b"]), r["u1"], r["u2"], _v3(r["plane"]),
        _f(r["plane.d"]), n, verts, _v3(r["centre"]), _f(r["radius"]), r["u3"], r["u4"])


# Desert Storm II's squad (its training levels' *_Training units) -> Desert Storm's squad units
SQUAD_NAMES = {"BRADLEY": "Bradley", "FOLEY": "Foley", "JONES": "Jones", "RAMIREZ": "Connors", "CONNORS": "Connors"}
ATMOSPHERE = ("FOGSTART", "FOGSTOP", "XFOGSTART", "XFOGSTOP", "SUNAMBCOL", "SUNDIFCOL", "FOGCOL", "FLARECOL",
              "EASTCOL", "SKYCOL", "WESTCOL", "TIMEOFDAY")


def port_level(src, dst, level, base="Mission3", name=None, log=print, empty=False):
    """({file name: bytes}, notes, level name) for one source level as a Desert Storm `.lua` level on `base`'s
    archive (its soldiers, weapons, sounds, sky): zones, props, portals, the squad's start and the atmosphere of the
    source scene, its objects (units Desert Storm has by name, targets / lamp posts as scenery), waypoints and
    regions, AI route markers. Lights, ambient sounds, cameras and the mission's logic aren't ported yet.
    empty: scenery only - the soldiers / vehicles / items the scene places are ported as files (.unit, .vehicle,
    weapon rows) but not placed; the squad start stays (convert_maps)."""
    from . import blank
    from .env import EnvFile, convert_record
    from .porting import port_model
    level = level.upper()
    data = src.get(level + ".ENV")
    if data is None:
        raise PortError("%s has no scene %s.ENV" % (src.game.short, level))
    with src.current():
        scene = EnvFile.read(data)
    bdata = dst.get(base.upper() + ".ENV")
    if bdata is None:
        raise PortError("%s has no scene %s.ENV (the base level)" % (dst.game.short, base))
    with dst.current():
        shell = EnvFile.read(bdata)
    name = (name or "%s_%s" % (src.game.id.upper(), level)).upper()
    prefix = src.game.id.upper() + "_"
    files, notes = {}, []

    # zones: every type-5 record under Root names a zone model; its other type-5 records are that model's objects
    # (VAMI: an ENVI line + its frame rate - flags, washing lines; ANMI: + an animation name; both games have them)
    envi = [r for r in scene.records if r.kind in ("ENVI", "VAMI", "ANMI") and r.fields]
    zone_models = sorted({str(r["model"]) for r in envi if r["type"] == 5 and str(r["parent"]).lower() == "root"})
    lo, hi = [1e30] * 3, [-1e30] * 3
    zone_boxes = {}                                 # zone root record -> its model's box (placing Root objects)
    for m in zone_models:
        log("zone %s" % m)
        zdata, ztex, znotes = port_zone(src, dst, m)
        files[m.upper() + ".EVO"] = zdata
        files.update(ztex)
        notes.append("zone %s: %s" % (m, "; ".join(znotes)))
        zlo, zhi = [1e30] * 3, [-1e30] * 3
        for n in evo.read(zdata):
            for k in range(3):
                zlo[k] = min(zlo[k], n.bbox[0][k])
                zhi[k] = max(zhi[k], n.bbox[1][k])
        for k in range(3):
            lo[k], hi[k] = min(lo[k], zlo[k]), max(hi[k], zhi[k])
        for r in envi:
            if r["type"] == 5 and str(r["model"]) == m and str(r["parent"]).lower() == "root":
                zone_boxes[str(r["name"])] = (zlo, zhi)

    # props: one Desert Storm model per source model (DS2_<MODEL>), placed by the scene's records
    prop_models, failed, flags_dropped = {}, {}, {}
    breakables = 0
    animated = {}                      # VAMI / ANMI records written
    shard_models, failed_shards = {}, {}   # breakables' shard models the target lacks: ported name / why not
    native = set()                     # models Desert Storm has itself under the same name (shared libraries)

    def get_model(model):
        """The Desert Storm model to place for a source model: Desert Storm's own copy when it has the same model
        under that name (the games share their training camps and many props; _same_model), else the source's
        ported (DS2_<MODEL>)."""
        if _same_model(src, dst, model):
            native.add(model.upper())
            return model.upper()
        try:
            pm, pdata, ptex, _pn = port_model(src, dst, model)
        except (PortError, evo.EvoError) as e:
            failed[model] = str(e)
            return None
        files[pm + ".EVO"] = pdata
        files.update(ptex)
        return pm
    lines = []
    # Desert Storm's loader finds a record's parent among the records it has already read ("Unable to find parent");
    # Desert Storm II links them afterwards (PCLINK), so its files list props before their zones: parents first here
    by_name = {str(r["name"]): r for r in envi}

    def depth(r, seen=()):
        p = by_name.get(str(r["parent"]))
        return 0 if p is None or p in seen else 1 + depth(p, seen + (r,))
    envi = sorted(envi, key=depth)                  # (stable: the source order within one depth)
    dropped = {}                                    # records left out -> their parent (children move up to it)
    for r in envi:
        model, parent = str(r["model"]), str(r["parent"])
        while parent in dropped:
            parent = dropped[parent]
        texture = str(r["texture"])
        if r["type"] == 5:
            lines.append(envi_line(r["name"], parent, 5, model, "NOTEXTURE", r["pos"], r["rot"], r["scale"],
                                   _lights(r)))
            continue
        if model not in prop_models and model not in failed:
            log("prop %s" % model)
            got = get_model(model)
            if got:
                prop_models[model] = got
        if model in failed:
            dropped[str(r["name"])] = parent
            continue
        if texture.upper() != "NOTEXTURE":
            # a record-level retexture: the texture comes along under the prefixed name
            for ext in (".DDS", ".TGA"):
                t = src.get(texture + ext)
                if t is not None:
                    files[(prefix + texture)[:63] + ext] = t
                    break
            texture = (prefix + texture)[:63]
        for bit, what in ((0x20000000, "flag 0x20000000"), (0x1000000, "render info")):
            if r["flags"] & bit:
                flags_dropped[what] = flags_dropped.get(what, 0) + 1
        lights = _lights(r)
        # a record's type must fit its model: 4 = a hierarchy instance (Desert Storm II's alarm lights, doors as
        # props); a model ported onto Desert Storm's static prop is one static object (type 0) - type 4 on it made the
        # loader refuse the file ("Attempting to re-compile EVO file for X.pi2"). Desert Storm's own model keeps it.
        rtype = 0 if r["type"] == 4 and prop_models[model].upper() not in native else r["type"]
        kind = r.kind
        if kind == "ANMI" and (rtype != r["type"] or str(r["animation"]).upper() == "NOANIM"):
            kind = "ENVI"                     # no animation to play: a plain prop
        if r["flags"] & 0x400000 and "break_health" in r.fields:
            # breakable (glass, crates): Desert Storm's own breakable block (health, shard count / texture / sizes /
            # alpha) - the record converter drops Desert Storm II's extra shard fields. The shard field names a shard
            # model (shard_wood_03.pi2 ...): both games share that library, so it stays as it is
            over = {"flags": 0x400000, "parent": parent, "model": prop_models[model], "texture": texture,
                    "u4": -1, "light_count": len(lights)}
            over.update({"light%d" % k: v for k, v in enumerate(lights)})
            over["type"] = rtype
            # the shard model (shard_barrel_02.pi2 = shard_barrel_02.evo): most are in both games' libraries; one only
            # the source has comes along ported (DS2_shard_...), else the game can't break the object
            shard = str(r["shard_texture"])
            stem = shard[:-4] if shard.lower().endswith(".pi2") else shard
            if stem and not _has_model(dst, stem):
                if stem not in shard_models:
                    shard_models[stem] = None
                    if src.get(stem + ".EVO") is not None:
                        try:
                            pm, pdata, ptex, _pn = port_model(src, dst, stem)
                            files[pm + ".EVO"] = pdata
                            files.update(ptex)
                            shard_models[stem] = pm
                        except (PortError, evo.EvoError) as e:
                            failed_shards[stem] = str(e)
                if shard_models[stem]:
                    over["shard_texture"] = shard_models[stem] + ".pi2"
            with dst.current():
                line = convert_record(r, shell.version, kind, **over)
            if kind != r.kind and r.kind == "ANMI":
                line = re.sub(r'\s*"[^"]*"\s*$', " ", line)      # (the ANMI layout's trailing animation name)
            lines.append(line)
            breakables += 1
            continue
        line = envi_line(r["name"], parent, rtype, prop_models[model], texture, r["pos"], r["rot"], r["scale"],
                         lights)
        if kind == "VAMI":
            line = "VAMI" + line[4:] + "%s " % _f(r["vami"])
            animated["VAMI"] = animated.get("VAMI", 0) + 1
        elif kind == "ANMI":
            anim = str(r["animation"])
            if anim.upper() != "NOANIM" and dst.get(anim + ".PRB") is None:
                notes.append("%s: animation %s isn't in %s's files - left still" % (r["name"], anim, dst.game.short))
                anim = "NOANIM"
            line = "ANMI" + line[4:] + "%s " % _q(anim)
            animated["ANMI"] = animated.get("ANMI", 0) + 1
        lines.append(line)
    notes.append("%d prop model(s) (%d Desert Storm's own) for %d record(s)%s" % (len(prop_models), len(native), sum(
        1 for r in envi if r["type"] != 5 and str(r["model"]) in prop_models),
        ", %d breakable" % breakables if breakables else ""))
    if failed:
        notes.append("props left out: " + "; ".join("%s (%s)" % kv for kv in sorted(failed.items())))
    if any(shard_models.values()):
        notes.append("shard models ported from %s: %s" % (src.game.short, ", ".join(
            sorted(k for k, v in shard_models.items() if v))))
    missing_shards = sorted(k for k, v in shard_models.items() if not v)
    if missing_shards:
        notes.append("shard models no game has / that didn't port: " + ", ".join(missing_shards))
    if flags_dropped:
        notes.append("record flags not carried over (Desert Storm reads them differently): " + ", ".join(
            "%s x%d" % kv for kv in sorted(flags_dropped.items())))
    if animated:
        notes.append("animated props: " + ", ".join("%d %s" % (n, k) for k, n in sorted(animated.items())))

    # objects (HLOI): units Desert Storm has under the same name (soldier types, weapons, items, vehicles) keep it,
    # with Desert Storm's model; kinds it has no row for (pop-up targets, lamp posts, destructible structures, other
    # vehicles) stand in as scenery - a static prop of their model, if they're in play at the start; doors are left
    # out (as scenery they'd block their way for good)
    known = _ds1_units(dst)
    zone_of = _zone_finder(scene, zone_boxes)
    hloi, units_done, as_props, skipped, missing_links = [], {}, {}, {}, {}
    link_models = {}                        # attachment model -> its port (None: didn't port)
    placed_units = set()                # (the sound banks they need)
    obj_names = {}                      # source script id -> Desert Storm object name (cameras aim at them)
    src_doors = {r[0].strip().upper(): [c.strip() for c in r] for r in _source_table(src, "DOORS.TXT")
                 if r and r[0].strip()}
    door_models, door_rows = {}, {}
    start_inactive = []                 # soldiers the source places out of play (its script activates them later)
    vehicle_ports, ported_vehicles, vehicle_failed = {}, {}, {}   # source vehicles ported as new ones
    unit_ports, ported_units, unit_failed = {}, {}, {}            # source soldier types ported as new ones
    table_rows, ported_rows, table_failed = {}, {}, {}           # structures / features / targets ported as rows
    # weapons first: the ones soldiers carry (kit items) and the ones ammo pickups load (INVEN col 7) - so kits keep
    # them and the ammo rows find them, whatever order the records come in
    from .porting import _table
    src_inven = {r[0].strip().upper(): r for r in _table(src, "INVEN.TXT") if r}
    src_weaps = {r[0].strip().upper() for r in _table(src, "WEAPS.TXT") if r}
    src_vehicles = {r[0].upper() for r in src.vehicle_rows() if r}
    wanted = set()
    for r in scene.records:
        if r.kind != "HLOI" or not r.fields or r.tokens[0].text == "HLOU":
            continue
        names = [str(r["unit"])] + [str(r["kit%d.item" % k]) for k in range(r.get("kit_count") or 0)]
        for n in names:
            inv = src_inven.get(n.upper())
            if inv is not None and len(inv) > 7:
                names.append(inv[7].strip())
        wanted.update(n for n in names if n.upper() in src_weaps and n.upper() not in known)
    for w in sorted(wanted):
        got = _port_weapon(src, dst, w, known, files, table_failed)
        if got:
            known[w.upper()] = got[:2]
            ported_rows[w] = "weapon on %s" % got[2]
    for r in scene.records:
        if r.kind != "HLOI" or not r.fields or r.tokens[0].text == "HLOU":
            continue
        unit = str(r["unit"])
        zone = str(r["zone"])
        if zone.lower() == "root" or zone not in by_name:
            zone = zone_of(r["pos"])
        hit = known.get(unit.upper()) or vehicle_ports.get(unit.upper())
        is_vehicle = "_VEH_" in unit.upper() or unit.upper() in src_vehicles   # (DS2's ScudTransporter_M7)
        if hit is None and is_vehicle and unit.upper() not in vehicle_failed:
            # a vehicle Desert Storm hasn't got: the source's own, ported drivable (porting.port_vehicle: its model,
            # seats, wheels, guns, stats) on the closest Desert Storm vehicle's handling, under the source's unit
            # name - the scene record and the source's mission script keep naming it so
            like = _vehicle_alias(unit, known) or _vehicle_like_stat(src, dst, unit) or _vehicle_base(unit)
            try:
                if like is None:
                    raise PortError("no Desert Storm vehicle to base it on (aircraft stay scenery)")
                from .porting import port_vehicle
                vm, vfiles, vnotes = port_vehicle(src, dst, str(r["model"]), like)
                vfiles[vm + ".vehicle"] = re.sub(rb"(?m)^id( +)= .*$",
                                                 lambda m: b"id" + m.group(1) + b"= " + unit.encode("latin-1"),
                                                 vfiles[vm + ".vehicle"], count=1)
                files.update({k: v for k, v in vfiles.items() if k != vm + ".vehicle"})
                files[unit.upper() + ".vehicle"] = vfiles[vm + ".vehicle"]
                vehicle_ports[unit.upper()] = hit = ("VEHICLE.TXT", vm)
                ported_vehicles[unit] = "%s (on %s's handling)" % (vm, like)
            except (PortError, evo.EvoError) as e:
                # not drivable that way (no wheels / no .DYN on either side: AA guns, aircraft): the source's model on
                # the base's own model (its objects, connectors, rotors stay), as a vehicle under the source's name
                vm = _port_vehicle_model(src, dst, unit, str(r["model"]), like, files)
                if vm:
                    vehicle_ports[unit.upper()] = hit = ("VEHICLE.TXT", vm)
                    ported_vehicles[unit] = "%s (model on %s's)" % (vm, like)
                else:
                    vehicle_failed[unit.upper()] = str(e)
        if hit is None and not is_vehicle and unit.upper() not in unit_failed and \
                unit.upper() not in unit_ports and unit.upper() in _source_soldiers(src):
            # a soldier type Desert Storm hasn't got: the source's body on the closest Desert Storm soldier type
            # (porting.unit_file + port_character), under the source's unit name
            like = _vehicle_alias(unit, known, "UNITS.TXT") or "Iraqi_Conscript"
            try:
                um = _port_unit(src, dst, unit, str(r["model"]), like, files)
                unit_ports[unit.upper()] = ("UNITS.TXT", um)
                ported_units[unit] = "%s (on %s)" % (um, like)
            except (PortError, evo.EvoError) as e:
                unit_failed[unit.upper()] = str(e)
        hit = hit or unit_ports.get(unit.upper())
        if hit is None and unit.upper() not in table_failed:
            got = _port_weapon(src, dst, unit, known, files, table_failed)
            if got:
                unit_ports[unit.upper()] = hit = got[:2]
                ported_rows[unit] = "weapon on %s" % got[2]
        if hit is None and unit.upper() not in table_failed:
            got = _port_table_row(src, dst, unit, files, table_rows, table_failed)
            if got:
                unit_ports[unit.upper()] = hit = got
                ported_rows[unit] = got[0]
        if hit is not None and empty:
            # an empty map (convert_maps): the unit's files are ported for modders, nothing is placed
            units_done[unit] = units_done.get(unit, 0) + 1
            continue
        if hit is not None:
            table, model = hit
            over = {"model": model, "zone": zone, "texture": "NOTEXTURE"}
            if table == "UNITS.TXT" and not r["in_play"] and not r["flags"] & 0x20000:
                # a soldier out of play from the start crashes Desert Storm on its first AI turn (0x42DF97: the
                # animation channels are never set up) - in play in the scene, deactivated by the script at once,
                # as Desert Storm's own missions do with soldiers that wait
                over["in_play"] = 1
                start_inactive.append(int(r["script_id"]))
            kit = [(r["kit%d.item" % k], r["kit%d.category" % k], r["kit%d.amount" % k])
                   for k in range(r.get("kit_count") or 0)]
            kept = [k for k in kit if str(k[0]).upper() in known]
            over["kit_count"] = len(kept)
            for k, (item, cat, amount) in enumerate(kept):
                over["kit%d.item" % k], over["kit%d.category" % k], over["kit%d.amount" % k] = item, cat, amount
            if len(kept) < len(kit):
                notes.append("%s %s: kit items Desert Storm doesn't have left out: %s" % (
                    unit, r["stat"], ", ".join(str(k[0]) for k in kit if k not in kept)))
            # attachments on the body's connectors (caps, pouches): Desert Storm's model of that name, else the
            # source's ported (DS2_<MODEL>) - a model nobody has stops the level load ("Unable to find connector
            # object", then a crash), so one that won't port is left off
            links = []
            for k in range(r.get("link_count") or 0):
                a, n, b = r["link%d.a" % k], r["link%d.n" % k], str(r["link%d.b" % k])
                if PORT_ATTACHMENTS and not _has_model(dst, b):
                    if b not in link_models:
                        link_models[b] = get_model(b) if src.get(b + ".EVO") is not None else None
                    b = link_models[b] or b
                links.append((a, n, b))
            good = [l for l in links if _has_model(dst, str(l[2])) or str(l[2]).upper() + ".EVO" in files]
            over["link_count"] = len(good)
            for k, (a, n, b) in enumerate(good):
                over["link%d.a" % k], over["link%d.n" % k], over["link%d.b" % k] = a, n, b
            for l in links:
                if l not in good:
                    missing_links[str(l[2])] = missing_links.get(str(l[2]), 0) + 1
            with dst.current():
                hloi.append(convert_record(r, shell.version, "HLOI", **over))
            obj_names[int(r["script_id"])] = "%s%d" % (model.upper(), int(r["script_id"]))
            units_done[unit] = units_done.get(unit, 0) + 1
            placed_units.update([unit] + [str(k[0]) for k in kept])
            continue
        door = src_doors.get(unit.upper())
        if door is not None:
            # a door Desert Storm hasn't got: its model ported, the source's DOORS.TXT row (same layout: model, door
            # type, open movement, flags, time) appended to the mod's DOORS.TXT, the record kept as a door
            model = str(door[1])
            if model not in door_models and model not in failed:
                got = get_model(model)
                if got:
                    door_models[model] = got
            if model in door_models:
                if unit.upper() not in door_rows:
                    row = list(door)
                    row[1] = door_models[model]
                    if len(row) > 11 and row[11].strip().lstrip("-").isdigit():
                        row[11] = str(int(row[11]) & 0x1F)     # (DS2's 0x20 bit is unknown to Desert Storm)
                    door_rows[unit.upper()] = ",".join(row)
                with dst.current():
                    hloi.append(convert_record(r, shell.version, "HLOI", model=door_models[model], zone=zone,
                                               texture="NOTEXTURE", kit_count=0, link_count=0))
                obj_names[int(r["script_id"])] = "%s%d" % (door_models[model].upper(), int(r["script_id"]))
                units_done[unit] = units_done.get(unit, 0) + 1
                placed_units.add(unit)
                continue
        if "DOOR" in unit.upper():
            skipped[unit] = skipped.get(unit, 0) + 1    # a door as scenery would block its way for good
            continue
        if not r["in_play"]:
            continue                        # waits for the source's script (a lamp's switched-off copy ...)
        # anything else (targets, lamps, structures, vehicles Desert Storm lacks): a static prop of its model
        model = str(r["model"])
        if model not in prop_models and model not in failed:
            got = get_model(model)
            if got:
                prop_models[model] = got
        if model in prop_models:
            lines.append(envi_line("%s_%d" % (str(r["stat"])[:40], r["script_id"]), zone, 0, prop_models[model],
                                   "NOTEXTURE", r["pos"], r["rot"], (1.0, 1.0, 1.0)))
            as_props[unit] = as_props.get(unit, 0) + 1
            continue
        skipped[unit] = skipped.get(unit, 0) + 1
    if ported_vehicles:
        notes.append("vehicles ported from %s (drivable, own model / guns): %s" % (src.game.short, ", ".join(
            "%s -> %s" % kv for kv in sorted(ported_vehicles.items()))))
    for t, rows in sorted(table_rows.items()):
        files[t] = ("\r\n".join(rows) + "\r\n").encode("latin-1")    # a mod table part: rows added to the game's
    if ported_rows:
        notes.append("structures / features / targets / weapons ported from %s: %s" % (src.game.short, ", ".join(
            "%s (%s)" % kv for kv in sorted(ported_rows.items()))))
    if table_failed:
        notes.append("rows not ported: " + "; ".join("%s (%s)" % kv for kv in sorted(table_failed.items())))
    if ported_units:
        notes.append("soldier types ported from %s: %s" % (src.game.short, ", ".join(
            "%s -> %s" % kv for kv in sorted(ported_units.items()))))
    if unit_failed:
        notes.append("soldier types not ported: " + "; ".join("%s (%s)" % kv for kv in sorted(unit_failed.items())))
    if vehicle_failed:
        notes.append("vehicles not ported (scenery instead): " + "; ".join(
            "%s (%s)" % kv for kv in sorted(vehicle_failed.items())))
    if start_inactive:
        notes.append("%d soldier(s) out of play at the start: in play in the scene, deactivated by the script" %
                     len(start_inactive))
    if units_done:
        notes.append("objects: " + ", ".join("%s x%d" % kv for kv in sorted(units_done.items())))
    if as_props:
        notes.append("placed as scenery (Desert Storm has no row for them): " + ", ".join(
            "%s x%d" % kv for kv in sorted(as_props.items())))
    if door_rows:
        files["DOORS.TXT"] = ("\r\n".join(door_rows.values()) + "\r\n").encode("latin-1")
        notes.append("doors: %d kind(s) added to DOORS.TXT (%s)" % (len(door_rows), ", ".join(sorted(door_rows))))
    if any(link_models.values()):
        notes.append("attachments ported from %s: %s" % (src.game.short, ", ".join(
            sorted(k for k, v in link_models.items() if v))))
    if missing_links:
        notes.append("attachments that didn't port, left off: " + ", ".join(
            "%s x%d" % kv for kv in sorted(missing_links.items())))
    if skipped:
        notes.append("objects left out (doors; scenery whose model didn't port): " + ", ".join(
            "%s x%d" % kv for kv in sorted(skipped.items())))

    port_recs = [r for r in scene.records if r.kind == "PORT" and r.fields]
    ports = [port_line(r) for r in port_recs]
    if any(r.get("pair_count") for r in port_recs):
        notes.append("portal pair lists dropped (Desert Storm has none)")

    # lights: the same untyped lines in both games (DS1 v72 / DS2 v106: id, name, flags, colours, then the kind's
    # position / direction / range and glow); the sun replaces the base's (below), the others come after it. Records
    # name their lights by id (_lights)
    lights, attached = [], []
    for r in scene.records:
        if r.kind in ("DLGT", "PLGT", "SLGT") and '"SUN"' not in r.text:
            flags = r.tokens[3].value if len(r.tokens) > 3 and isinstance(r.tokens[3].value, int) else 0
            # flag 0x800: the light follows an object named on its line (Training 3's on a pop-up target) - Desert
            # Storm's scenes never use that, and an object it can't find crashed the load: left out
            (attached if flags & 0x800 else lights).append(r.text)
    notes.append("%d light(s)%s" % (len(lights), ", %d attached to objects left out" % len(attached) if attached
                                     else ""))

    # ambient sounds: the records as they are, their samples from the source's level cache as a bank pack
    with dst.current():
        sounds = [convert_record(r, shell.version) for r in scene.records if r.kind == "ENVSND" and r.fields]
    pack, anotes = ambient_pack(src, level, scene)
    if pack:
        files[name + "_AMBIENT.sch"] = pack
    notes += anotes

    # waypoints (patrol / flight chains: id, next id, wait) and regions (trigger areas): the same records in Desert
    # Storm's layout - the ids stay the source's, so its mission logic (Part 4) still names them
    with dst.current():
        waypoints = [convert_record(r, shell.version) for r in scene.records if r.kind == "NEWENVWAYPT" and r.fields]
        regions = [convert_record(r, shell.version) for r in scene.records if r.kind == "REGN" and r.fields]
    if len(waypoints) > 600:
        notes.append("%d waypoints - Desert Storm's table holds 600" % len(waypoints))
    notes.append("%d waypoint(s), %d region(s)" % (len(waypoints), len(regions)))

    out = blank.blank_lines(shell, extra_envi=lines, extra_hloi=hloi, extra_ports=ports,
                            extra={"ENDWAYPTS": waypoints, "ENDREGIONS": regions, "ENDL": lights,
                                   "ENDENVS": sounds})
    with dst.current():
        new = EnvFile("\r\n".join(out), "\r\n")

    # the squad at the source's start: the source's squad units by name (Desert Storm II: HLOU *_Training)
    starts, src_ids = {}, {}
    for r in scene.records:
        if r.kind in ("HLOI", "HLOU") and r.fields:
            key = str(r.get("unit", "")).split("_")[0].upper()
            if key in SQUAD_NAMES and SQUAD_NAMES[key] not in starts:
                zone = str(r.get("zone") or r.get("parent"))
                starts[SQUAD_NAMES[key]] = (r["pos"], zone if zone in zone_boxes or zone in by_name else
                                            zone_of(r["pos"]))
                src_ids[SQUAD_NAMES[key]] = int(r["script_id"])
    first = next(iter(starts.values()), None)
    if first is None:
        notes.append("no squad start in the source scene - the squad stays at the base level's spot")
    # the base level's squad records: placed at the source's start with the source squad member's script id (the
    # source's mission logic names its soldiers by it - Part 4 runs that logic), else an id above the source's
    next_id = max([int(r["script_id"]) for r in scene.records if r.kind == "HLOI" and r.fields] + [0]) + 1
    squad = [h for h in new.find("HLOI") if str(h["unit"]) in blank.SQUAD]
    who = lambda h: {"Ramirez": "Connors"}.get(str(h["unit"]), str(h["unit"]))  # noqa: E731 (Connors' unit name)
    taken = {int(r["script_id"]) for r in scene.records if r.kind == "HLOI" and r.fields and r.tokens[0].text != "HLOU"}
    for h in squad:
        sid = src_ids.get(who(h))
        if sid is None or sid in taken:
            sid, next_id = next_id, next_id + 1
        h["script_id"] = sid
        taken.add(sid)
    for k, h in enumerate(squad):
        if first is None:
            break
        pos, zone = starts.get(who(h), first)
        if who(h) not in starts:
            pos = (pos[0] + 150 * (k + 1), pos[1], pos[2])
        h["pos"], h["zone"] = pos, zone
    notes.append("squad start: " + ", ".join("%s at (%.0f, %.0f, %.0f)" % (u, *p) for u, (p, _z) in starts.items()))

    # cameras (cutscene / script views - mission scripts pick them by index, so all of them, in the source's order):
    # Desert Storm's line has one more int after the flags (128) and only the low 16 flag bits; an object camera
    # names its object (<MODEL><script id>) where Desert Storm II gives the script id - a target that didn't come
    # over is replaced by the squad leader, keeping the index
    by_unit = {who(h): h for h in squad}
    for r in scene.records:
        if r.kind == "HLOI" and r.fields and r.tokens[0].text == "HLOU":
            key = str(r.get("unit", "")).split("_")[0].upper()
            h = by_unit.get(SQUAD_NAMES.get(key, ""))
            if h is not None:
                obj_names[int(r["script_id"])] = "%s%d" % (str(h["model"]).upper(), int(h["script_id"]))
    leader = next(iter(obj_names.values()), "HERO01_ARMSTRONG1")
    if squad:
        leader = "%s%d" % (str(squad[0]["model"]).upper(), int(squad[0]["script_id"]))
    cams, lost = [], 0
    for r in scene.records:
        if not r.kind.startswith("CAM") or not r.fields or r.kind == "CAMERA":
            continue
        rest = [r.tokens[r.fields["p%d" % k]].text for k in range(len(r.tokens)) if "p%d" % k in r.fields]
        if r.kind in ("CAMTARGOBJ", "CAMCHASEOBJ") and rest:
            t = rest[0].strip('"')
            m = re.search(r"(\d+)$", t)
            sid = int(t) if t.lstrip("-").isdigit() else int(m.group(1)) if m else None
            name_ = obj_names.get(sid)
            if name_ is None:
                lost += 1
                name_ = leader
            rest[0] = '"%s"' % name_
        cams.append("%s %s %d 128 %s %s %s " % (r.kind, _q(r["name"]), int(r["flags"]) & 0xFFFF, _q(r["zone"]),
                                                _v3(r["pos"]), " ".join(rest)))
    if cams:
        end = next(i for i, l in enumerate(new.lines) if l.strip() == "ENDC")
        new.lines[end:end] = cams
    notes.append("%d camera(s)%s" % (len(cams), ", %d aimed at an object that didn't come over (now the squad "
                                     "leader)" % lost if lost else ""))

    # the atmosphere (fog, colour keys, time of day) and the sun: the same lines in both games
    src_lines = {}
    for r in scene.records:
        if r.kind in ATMOSPHERE or (r.kind == "DLGT" and '"SUN"' in r.text):
            src_lines.setdefault(r.kind, []).append(r.text)
    src_lines.update(sky_lines(scene, prefix, src, files))     # the sky layers (+ their textures)
    text = list(new.lines)
    seen = {}
    for i, line in enumerate(text):
        kind = line.split(" ", 1)[0]
        if kind in src_lines and (kind != "DLGT" or '"SUN"' in line):
            k = seen.get(kind, 0)
            if k < len(src_lines[kind]):
                text[i] = src_lines[kind][k]
            seen[kind] = k + 1
    # the loading picture named in the header ("<picture>"<n> 0): the source's, when its files have it (the base
    # level's isn't always there - Mission 3's M3.BMP is missing from Desert Storm itself: an error line every load)
    m = re.match(r'\s*"([^"]+)"', scene.lines[1]) if len(scene.lines) > 1 else None
    pic = src.get(m.group(1)) if m else None
    if pic is not None and len(text) > 1 and text[1].lstrip().startswith('"'):
        new_pic = (prefix + m.group(1))[:63]
        files[new_pic] = pic
        text[1] = re.sub(r'"[^"]*"', '"%s"' % new_pic, text[1], count=1)
    elif m and len(text) > 1 and text[1].lstrip().startswith('"'):
        # the source names a picture it hasn't got (Desert Storm II's Mission 1: Mission01Load.bmp) - its loading
        # screen has none, so the port's hasn't either (DesertStormFix logs the missing picture, no error box)
        text[1] = re.sub(r'"[^"]*"', '"%s"' % m.group(1), text[1], count=1)
        notes.append("loading picture: %s names %s, which it hasn't got - no picture (as in %s)" % (
            src.game.short, m.group(1), src.game.short))
    files[name + ".ENV"] = ("\r\n".join(text)).encode("latin-1")

    # the map screen: the source's own map picture and the bounds it was drawn for, else the base level's picture
    # (+0x50 sets up the map screen and reads it - without one it crashed) and bounds round the zones
    margin = 2000.0
    map_img, bounds = "GW%s.img" % base, (hi[0] + margin, hi[2] + margin, lo[0] - margin, lo[2] - margin)
    smap = source_map(src, level)
    if smap:
        img, (smax, smin) = smap
        imgdata = src.get(img)
        page = imgdata and _img_pages(imgdata)
        pdata = page and len(page) == 1 and src.get(page[0])
        if pdata:
            stem = (src.game.id.upper() + level[0] + re.sub(r"\D", "", level))[:7] + "MAP0"
            new_img = "GW%s.img" % name
            files[new_img.upper()] = _img_rename_page(imgdata, page[0], stem + ".png")
            files[(stem + ".png").upper()] = pdata
            map_img, bounds = new_img, (smax[0], smax[2], smin[0], smin[2])
            notes.append("map: %s's %s (%s) with its bounds x %.0f..%.0f, z %.0f..%.0f" % (
                src.game.short, img, page[0], smin[0], smax[0], smin[2], smax[2]))
        else:
            notes.append("map: %s's %s or its page is missing - %s's map picture" % (src.game.short, img, base))
    else:
        notes.append("map: %s's %s has none (or its script doesn't say) - %s's map picture" % (
            src.game.short, level, base))
    # the level's script: the base mission's map set-up without its objects, this scene, the map
    script = "\n".join([
        "--@archive %s" % base.lower(),
        "-- %s's %s ported by the ModKit level porter (conflict_ds_tools/levelport.py) onto %s's archive" % (
            src.game.short, level, base),
        "-- (its soldiers, weapons, sounds). Write the mission below.",
        "",
        "local function setup()",
        '    game.load_sound_bank("%s.sbk")' % base,
        "    game.call(0x494, 2); game.call(0x498, 0x81F7E1EB, 0xEC96DD18); game.call(0x4AC, 65)",
        '    game.load_scene("%s.ENV")' % name,
        '    game.load_map_image("%s")' % map_img,
        "    game.call(0x50, 0)",
        "    game.set_level_bounds(%.1f, 0.0, %.1f, %.1f, 0.0, %.1f)" % bounds,
        "    game.call(0x3F0)",
    ] + ["    game.deactivate(%d)   -- out of play at the start in %s" % (i, src.game.short)
         for i in start_inactive] + [
        "end",
        "",
        "function init()",
        "    setup()",
        "end",
        "",
        "function update()",
        "end",
        ""])
    files[name.lower() + ".lua"] = script.encode("latin-1")
    # render effects: the game loads <scene>.rfx (the scene's name, extension swapped - 0x40C815..0x40C83F)
    rfx = src.get(level + ".RFX")
    if rfx is not None:
        names = {m.upper(): m.upper() for m in zone_models}
        names.update({k.upper(): v for k, v in list(prop_models.items()) + list(door_models.items())})
        text, rnotes = rfx_file(src, rfx.decode("latin-1"), names, prefix, files, native)
        files[name + ".RFX"] = text.encode("latin-1")
        notes += rnotes
    else:
        notes.append("render effects: %s has no %s.RFX - the zones are drawn without detail textures" % (
            src.game.short, level))

    # AI routes: the source's route file in Desert Storm's layout (version 1). DesertStormFix keeps its hand-placed
    # markers (first, flags bit 0 clear - the ones mission scripts name by index) and builds the generated rest from
    # this scene's collision (scripthost.cpp BuildNav)
    rmd = src.get(level + ".RMD")
    if rmd is not None:
        from .routes import RouteFile
        try:
            rf = RouteFile.read(rmd)
            rf.version = 1
            files[name + ".RMD"] = rf.to_bytes()
            notes.append("routes: %d hand-placed AI marker(s) of %d (the rest are built from the scene)" % (
                rf.hand_count(), len(rf.markers)))
        except ValueError as e:
            notes.append("routes: %s.RMD not read (%s) - the AI's routes are built from the scene" % (level, e))
    # the octree: the game rebuilds it for a .lua level over a copy the mod index knows (made from the player's own
    # base level - nothing shipped)
    # sound banks the placed objects need (voices, engines, guns) that the base level's sound cache lacks: "bank" lines
    # - DesertStormFix copies them from the player's other level caches (a missing one = "<bank>.sbk NOT FOUND",
    # silent guns / voices)
    banks = _missing_banks(dst, placed_units, base,
                           {u: [c for c in row.split(",")] for u, row in door_rows.items()})
    if banks:
        notes.append("sound banks borrowed from other Desert Storm levels: " + ", ".join(banks))
    files[name + ".uses"] = ("# %s: game files this level uses from the player's own install\nfile %s.OCT = %s.OCT\n"
                             % (name, name, base.upper()) + "".join("bank %s\n" % b for b in banks)).encode("latin-1")
    return files, notes, name


_DS1_UNITS = {}


def _ds1_units(dst):
    """{UNIT NAME: (table, model)} of every object kind Desert Storm can place: soldier types (UNITS.TXT, model = the
    body its own scenes give that unit), weapons (WEAPS.TXT col 2), items (INVEN.TXT col 3), vehicles (VEHICLE.TXT
    col 2), doors (DOORS.TXT), destructible structures (STRUCTS.TXT col 2), features (FEATS.TXT col 2: fences,
    Desert Storm II's mortar strike points), pop-up targets (TARGETS.TXT col 2)."""
    key = id(dst)
    if key in _DS1_UNITS:
        return _DS1_UNITS[key]
    from .env import EnvFile
    from .porting import _table
    seen = {}
    names = [n for n in dst.names(".ENV")] if dst.files is not None else []
    for lv in names or ["MISSION%d" % i for i in range(1, 11)] + ["TRAINING1", "TRAINING2", "TRAINING3"]:
        data = dst.get(lv + ".ENV")
        if not data:
            continue
        try:
            with dst.current():
                env = EnvFile.read(data)
        except Exception:                                   # noqa: BLE001 - a scene the reader can't type
            continue
        for r in env.find("HLOI"):
            if r.fields:
                u = str(r["unit"]).upper()
                if u not in seen or str(r["texture"]).upper() == "NOTEXTURE":
                    seen.setdefault(u, str(r["model"]))
    out = {}
    for table, col in (("UNITS.TXT", None), ("WEAPS.TXT", 1), ("INVEN.TXT", 2), ("VEHICLE.TXT", 1),
                       ("DOORS.TXT", None), ("STRUCTS.TXT", 1), ("FEATS.TXT", 1), ("TARGETS.TXT", 1)):
        for row in _table(dst, table):
            if not row or not row[0].strip():
                continue
            u = row[0].strip().upper()
            model = seen.get(u) or (row[col].strip() if col is not None and len(row) > col else "")
            if u not in out and (model or table == "DOORS.TXT"):
                out[u] = (table, model)
    _DS1_UNITS[key] = out
    return out


_GENERIC_WORDS = {"UK", "US", "IR", "NU", "RU", "VEH", "VWPN", "WPN", "STRCT", "DOOR", "MISSION", "BATTLETANK", "1", "2", "3", "4", "01", "02"}


def _vehicle_alias(unit, known, table_name="VEHICLE.TXT"):
    """The Desert Storm vehicle closest to a source vehicle Desert Storm hasn't got - the base whose handling, seats
    and sounds a port of it gets: most shared name words (side and type words, single letters left out), at least one
    of them a vehicle word (Desert Storm II's UK_VEH_SAS_LandRover_MK19_SAW -> UK_VEH_SAS_LANDROVER_MILAN_LMG_SAW),
    the shortest name on a tie; None if none shares one."""
    words = {w for w in unit.upper().replace("-", "_").split("_") if len(w) > 1 and w not in _GENERIC_WORDS}
    best = None
    for name, (table, _model) in known.items():
        if table != table_name or (table_name == "VEHICLE.TXT" and "_VEH_" not in name):
            continue
        other = {w for w in name.replace("-", "_").split("_") if len(w) > 1 and w not in _GENERIC_WORDS}
        shared = words & other
        if not shared or shared <= {"SAS", "SAW", "LMG", "MG"}:
            continue
        key = (len(shared), -len(name))
        if best is None or key > best[0]:
            best = (key, name)
    return best[1] if best else None


def _vehicle_base(unit):
    """A Desert Storm base by kind when no vehicle shares a name word: helicopters the Hind, jets Mission 1's MiG-29,
    tanks / tracked armour the T-62, other ground vehicles the Humvee."""
    u = unit.upper()
    if any(w in u for w in ("HELI", "APACHE", "HIND", "CHINOOK", "BLACKHAWK")):
        return "IR_VEH_HINDGUNSHIP"
    if any(w in u for w in ("MIG", "A10", "JET", "PLANE", "AIRCRAFT", "TORNADO", "F15", "F16")):
        return "IR_VEH_MIG29_MISSION01"
    if any(w in u for w in ("TANK", "T-55", "T-62", "T-72", "BMP", "BTR", "APC", "M113", "CHALLENGER", "ABRAMS")):
        return "IR_VEH_T-62_BATTLETANK"
    return "US_VEH_HUM_VEE"


def _port_vehicle_model(src, dst, unit, model, like, files):
    """A source vehicle the drivable porter can't take (an AA gun, an aircraft): its model ported onto the base
    vehicle's own model as template (porting.port_model: the base's objects, connectors and rotors stay, its first
    render object takes the geometry) and a .vehicle under the source's unit name based on `like` (handling, seats,
    guns, flight). The model name, or None."""
    from .porting import port_model, _table
    row = next((r for r in _table(dst, "VEHICLE.TXT") if r and r[0].strip().upper() == (like or "").upper()), None)
    if row is None:
        return None
    tmpl = dst.get(row[1].strip() + ".EVO")
    if tmpl is None:
        return None
    try:
        pm, pdata, ptex, _pn = port_model(src, dst, model, template=tmpl)
    except (PortError, evo.EvoError):
        return None
    files[pm + ".EVO"] = pdata
    files.update(ptex)
    files[unit.upper() + ".vehicle"] = "\r\n".join([
        "# %s from %s (model %s), ported by the ModKit level porter onto %s's model and handling." % (
            unit, src.game.short, model, row[0].strip()),
        "", "id       = %s" % unit, "based on = %s" % row[0].strip(), "model    = %s" % pm, ""]).encode("latin-1")
    return pm


def _port_weapon(src, dst, unit, known, files, failed):
    """A source weapon Desert Storm hasn't got (it lies in the level or a script gives it): its model ported for the
    hand (porting.port_model held=True, with a HUD picture) and a .weapon (porting.weapon_file: the source's numbers,
    shot sound and name) based on the closest Desert Storm weapon (a pistol when none shares a name word), under the
    source's id. (table, model, base) or None when the source has no such weapon."""
    from .porting import _table, base_picture, port_model, weapon_file
    row = next((r for r in _table(src, "WEAPS.TXT") if r and r[0].strip().upper() == unit.upper()), None)
    if row is None or len(row) < 2:
        return None
    # the model: column 2 (Desert Storm / II, Vietnam) or 3 (Global Storm has a -1 in column 2)
    model = next((row[c].strip() for c in (1, 2) if len(row) > c and row[c].strip() not in ("", "-1")), "")
    if not model:
        return None
    like = _vehicle_alias(unit, known, "WEAPS.TXT")
    if like and _weapon_type(dst, like) in _SINGLE_TYPES:
        like = None                       # (Desert Storm keeps one weapon of these types - a copy is refused)
    like = like or _weapon_like_type(dst, row) or "US_WPN_SIGP228"
    try:
        pm, pdata, ptex, _pn = port_model(src, dst, model, held=True,
                                          picture=base_picture(dst, like) or (112, 34))
        wname, wtext, _wn, wfiles = weapon_file(src, dst, model, pm, like)
    except (PortError, evo.EvoError) as e:
        failed[unit.upper()] = str(e)
        return None
    files[pm + ".EVO"] = pdata
    files.update(ptex)
    files.update(wfiles)
    wtext = re.sub(r"(?m)^id( +)= .*$", lambda m: "id" + m.group(1) + "= " + unit, wtext, count=1)
    files[unit.upper() + ".weapon"] = wtext.replace("\n", "\r\n").encode("latin-1")
    return "WEAPS.TXT", pm, like


def _vehicle_like_stat(src, dst, unit):
    """The target's vehicle with the same stats row (VEHICLE col 4: Desert Storm II's ScudTransporter_M7 and Desert
    Storm's ScudTransporter both use Scud_Transporter) - a source vehicle whose name shares no word with one. None if
    none."""
    row = next((r for r in src.vehicle_rows() if r and r[0].upper() == unit.upper()), None)
    if row is None or len(row) < 5:
        return None
    stat = row[4].upper()
    return next((r[0] for r in dst.vehicle_rows() if len(r) > 4 and r[4].upper() == stat and
                 not r[0].upper().endswith("_FIRING")), None)


# weapon types Desert Storm's weapons parser keeps one of (DesertStormFix mods.cpp: "More than one ... has been
# loaded!") - a ported weapon can't be based on one
_SINGLE_TYPES = {"GRENADE", "GRENADE LAUNCHER", "GRENADE LAUNCH/RIFLE", "FIST"}


# Attachments (caps, pouches) the target hasn't got: ported (DS2_<MODEL>). Desert Storm's scene loader checks each
# attachment against its built-in list of 83 models (0x4FB700 on 0x5F9FF0) and shows a warning box for others - the
# model still loads; DesertStormFix (mods.cpp ConnectorListed) counts a mod's / converted campaign's model as listed.
# (The crashes first blamed on this were a refused one-of-a-kind grenade in the soldiers' kits: 0x436284.)
PORT_ATTACHMENTS = True


def _weapon_type(dst, weapon):
    from .porting import _table
    row = next((r for r in _table(dst, "WEAPS.TXT") if r and r[0].strip().upper() == weapon.upper()), None)
    return row[5].strip().upper() if row and len(row) > 5 else ""


# source weapon types Desert Storm hasn't got, or keeps only one weapon of (its M16A2 + M203 is the only GRENADE
# LAUNCH/RIFLE - matching words picked the smoke grenade for Desert Storm II's M16A2): the type whose numbers fit
_TYPE_FOR_BASE = {
    "GRENADE LAUNCH/RIFLE": "ASSAULT RIFLE", "AUTOMATIC_SHOTGUN": "SHOTGUN", "BOLT ACTION RIFLE": "SNIPER RIFLE",
    "CROSSBOW": "SNIPER RIFLE", "VEHICLE GRENADE LAUNCHER": "MACHINE GUN", "ANTI TANK WITH DELAY": "ANTI TANK",
    "CLAYMORE": "C4 CHARGE", "NAPALM BOMB": "C4 CHARGE", "FAKE MORTAR": "ANTI TANK", "MORTAR/ILLUM": "ANTI TANK",
    "FLARE GUN": "PISTOL",
}


def _weapon_like_type(dst, row):
    """A Desert Storm weapon of the source weapon's type (WEAPS col 5: "GRENADE", "PHOS GRENADE", "SMOKE GRENADE",
    "SNIPER RIFLE" ...): the same type, else one sharing a word of it (Desert Storm II's incendiary "PHOS GRENADE" ->
    the frag "GRENADE") - so a weapon whose name shares no word still gets the right kind's numbers. None if none."""
    from .porting import _table
    if len(row) < 6:
        return None
    # the type: column 6 (Desert Storm / II, Vietnam) or 8 (Global Storm: column 6 is a number there)
    kind = next((row[c].strip().upper() for c in (5, 7) if len(row) > c and re.search(r"[A-Z]{3}", row[c].upper())
                 and "CONNECTOR" not in row[c].upper()), "")
    if not kind:
        return None
    kind = _TYPE_FOR_BASE.get(kind, kind)
    rows = [r for r in _table(dst, "WEAPS.TXT") if r and len(r) > 5 and r[0].strip().upper().startswith(("US_", "IR_", "UK_"))
            and r[5].strip().upper() not in _SINGLE_TYPES]
    same = [r[0].strip() for r in rows if r[5].strip().upper() == kind]
    if same:
        return same[0]
    words = set(kind.split()) - {"GUN"}            # ("FLARE GUN" isn't a machine gun)
    near = [(len(words & set(r[5].strip().upper().split())), -len(r[5]), r[0].strip()) for r in rows]
    near = [n for n in near if n[0]]
    return max(near)[2] if near else None


# the object tables both games lay out the same (Desert Storm II's rows go into a mod's part of Desert Storm's):
# table -> (model columns, stat column (FEATSTAT), fragment column)
_ROW_TABLES = {"STRUCTS.TXT": ((1, 2), 4, None), "FEATS.TXT": ((1, 2), 4, 7), "TARGETS.TXT": ((1, 2), None, None),
               "INVEN.TXT": ((2,), None, None)}   # items (ammo: col 7 = the weapon it loads)


def _fit_name(src, model, limit):
    """<SOURCE>_<model> within a table column's length (Desert Storm reads STRUCTS / FEATS model names into 34-letter
    fields, pse/records/ds1/schema.lua max_len - a longer name is cut and the model isn't found): shortened with a
    hash of the full name so two long names stay apart."""
    import zlib
    prefix = src.game.id.upper() + "_"
    name = (model if model.upper().startswith(prefix) else prefix + model).upper()
    if len(name) <= limit:
        return name
    return "%s_%04X" % (name[:limit - 5], zlib.crc32(name.encode("latin-1")) & 0xFFFF)


def _port_table_row(src, dst, unit, files, table_rows, failed):
    """A source structure / feature / target Desert Storm hasn't got: the source's row (same layout), its models ported
    (porting.port_model, the row names the ported ones), its FEATSTAT row when Desert Storm lacks that stat, the
    fragment model kept only when Desert Storm has it. Adds the row to `table_rows`; (table, model) or None."""
    from .porting import port_model, _table
    for t, (mcols, scol, fcol) in _ROW_TABLES.items():
        row = next((r for r in _table(src, t) if r and r[0].strip().upper() == unit.upper()), None)
        if row is None:
            continue
        row = [c.strip() for c in row]
        if t == "INVEN.TXT" and len(row) > 7 and row[7] not in ("-1", ""):
            # ammo for a weapon: only when Desert Storm has the weapon or this level ported it (its .weapon file)
            weap = row[7].upper()
            if weap + ".WEAPON" not in {k.upper() for k in files} and                     not any(r and r[0].strip().upper() == weap for r in _table(dst, "WEAPS.TXT")):
                failed[unit.upper()] = "its weapon %s isn't in %s" % (row[7], dst.game.short)
                return None
        if t == "INVEN.TXT" and len(row) > 5:
            # the HUD picture (col 5, an id of the target's interface sheet): one the target hasn't got stops the
            # load with "unable to find interface ID" - its own picture of the same weapon's ammo / the same kind
            own = [r for r in _table(dst, "INVEN.TXT") if r and len(r) > 7]
            ids = {r[5].strip().upper() for r in own} | {r[4].strip().upper() for r in _table(dst, "WEAPS.TXT")
                                                        if r and len(r) > 4}
            if row[5].upper() not in ids:
                same = [r for r in own if len(row) > 7 and r[7].strip().upper() == row[7].upper()] or \
                       [r for r in own if r[1].strip().upper() == row[1].upper()]
                if same:
                    row[5] = same[0][5].strip()
        try:
            for c in mcols:
                if c < len(row) and row[c] not in ("-1", "", "dummy"):
                    if _has_model(dst, row[c]) and _same_model(src, dst, row[c]):
                        continue
                    pm, pdata, ptex, _pn = port_model(src, dst, row[c], new_name=_fit_name(src, row[c], 34))
                    files[pm + ".EVO"] = pdata
                    files.update(ptex)
                    row[c] = pm
        except (PortError, evo.EvoError) as e:
            failed[unit.upper()] = str(e)
            return None
        if scol is not None and scol < len(row):
            own = {r[0].strip().upper() for r in _table(dst, "FEATSTAT.TXT") if r}
            if row[scol].upper() not in own:
                st = next((r for r in _table(src, "FEATSTAT.TXT") if r and r[0].strip().upper() == row[scol].upper()),
                          None)
                if st is not None:
                    rows = table_rows.setdefault("FEATSTAT.TXT", [])
                    line = ",".join(c.strip() for c in st)
                    if line not in rows:
                        rows.append(line)
        if fcol is not None and fcol < len(row) and row[fcol] != "-1" and not _has_model(dst, row[fcol]):
            row[fcol] = "-1"
        table_rows.setdefault(t, []).append(",".join(row))
        return t, row[mcols[0]]
    return None


_SOURCE_SOLDIERS = {}


def _source_soldiers(src):
    """Upper-case names of the source game's soldier types (its UNITS.TXT)."""
    if id(src) not in _SOURCE_SOLDIERS:
        from .porting import _table
        _SOURCE_SOLDIERS[id(src)] = {r[0].strip().upper() for r in _table(src, "UNITS.TXT") if r and r[0].strip()}
    return _SOURCE_SOLDIERS[id(src)]


def _port_unit(src, dst, unit, model, like, files):
    """A source soldier type as a Desert Storm one under the source's unit name: its body on `like`'s
    (porting.port_character with the unit's base body) and a .unit (porting.unit_file) whose id is the source name.
    The ported model's name."""
    from .porting import port_character, unit_file
    prefix = src.game.id.upper() + "_"
    model_name = (model if model.upper().startswith(prefix) else prefix + model)[:35]
    sname, stext, _un, base_body, ufiles = unit_file(src, dst, model, model_name, like)
    files.update(ufiles)
    m, data, textures, _skin, _cn = port_character(src, dst, model, "Bradley", model_name, body=base_body)
    files[m + ".EVO"] = data
    files.update(textures)
    stext = re.sub(r"(?m)^id( +)= .*$", lambda mm: "id" + mm.group(1) + "= " + unit, stext, count=1)
    files[unit.upper() + ".unit"] = stext.replace("\n", "\r\n").encode("latin-1")
    return m


def _zone_finder(scene, zone_boxes):
    """position -> the name of the zone record whose models' box holds it (x / z; the nearest box otherwise)."""
    def find(pos):
        best, best_d = None, None
        for name, (lo, hi) in zone_boxes.items():
            dx = max(lo[0] - pos[0], 0.0, pos[0] - hi[0])
            dz = max(lo[2] - pos[2], 0.0, pos[2] - hi[2])
            d = dx * dx + dz * dz
            if best_d is None or d < best_d:
                best, best_d = name, d
        return best or "Root"
    return find


_MODELS = {}


def _has_model(dst, model):
    """Desert Storm has <model>.EVO (any archive)."""
    key = (id(dst), model.upper())
    if key not in _MODELS:
        _MODELS[key] = dst.get(model + ".EVO") is not None
    return _MODELS[key]


def _model_shape(data):
    """What a model looks like, for comparing two games' copies: per object, its meshes' (texture, triangle count)
    and the vertex bounds to the centimetre. None if it can't be read."""
    try:
        nodes = evo.read(data)
    except evo.EvoError:
        return None
    shape = []
    for nd in nodes:
        meshes = sorted((m.texture.lower(), len(m.triangles)) for m in nd.meshes)
        pts = [p for m in nd.meshes for p in m.positions]
        box = (tuple(round(min(p[i] for p in pts)) for i in range(3)) +
               tuple(round(max(p[i] for p in pts)) for i in range(3))) if pts else ()
        shape.append((meshes, box))
    return shape


def _same_model(src, dst, model):
    """Desert Storm has <model>.EVO and it is the source's model (same meshes, textures and size). Many names are
    shared but not the same model: Desert Storm II reworked 29 of the 55 training-camp models Desert Storm also has
    (its chain-link fences have straight posts, Desert Storm's barbed-wire tops), so only an identical copy is used."""
    if not _has_model(dst, model):
        return False
    a = _model_shape(src.get(model + ".EVO") or b"")
    return a is not None and a == _model_shape(dst.get(model + ".EVO"))


def _missing_banks(dst, units, base, extra_doors=None):
    """Sound banks (names without .sbk) the units need - soldier voices (UNITS.TXT col 11), vehicle engines (VSTAT col
    13 -> PROPS col 3), guns (WEAPS col 64, a vehicle's through VEHWPNS), items (INVEN col 13: the radio's MLRSRadio),
    doors (DOORS col 5, the door type) - that `base`'s level sound cache lacks. ``units`` may also hold door rows the
    port adds (unit name -> row) through ``extra_doors``."""
    import struct
    from . import games, sound
    from .porting import _table

    def rows(table):
        return {r[0].strip().upper(): r for r in _table(dst, table) if r and r[0].strip()}
    units_t, weaps, veh, vstat, props, inven, doors = (rows(t) for t in (
        "UNITS.TXT", "WEAPS.TXT", "VEHICLE.TXT", "VSTAT.TXT", "PROPS.TXT", "INVEN.TXT", "DOORS.TXT"))
    doors.update({k.upper(): v for k, v in (extra_doors or {}).items()})
    vehwpns = {}
    for r in _table(dst, "VEHWPNS.TXT"):
        if len(r) > 1:
            vehwpns.setdefault(r[0].strip().upper(), []).append(r[1].strip().upper())
    wanted = []

    def add(bank):
        bank = bank.strip()
        if bank.lower().endswith(".sbk"):
            bank = bank[:-4]
        if bank and bank != "-1" and bank not in wanted:
            wanted.append(bank)

    def weapon(w):
        r = weaps.get(w.upper())
        if r is not None and len(r) > 63:
            add(r[63])
    for u in sorted(units):
        key = u.upper()
        if key in units_t and len(units_t[key]) > 10:
            add(units_t[key][10])
        weapon(key)
        if key in inven and len(inven[key]) > 12:
            add(inven[key][12])
        if key in doors and len(doors[key]) > 4:
            add(doors[key][4])
        if key in veh and len(veh[key]) > 4:
            st = vstat.get(veh[key][4].strip().upper())
            pr = props.get(st[12].strip().upper()) if st is not None and len(st) > 12 else None
            if pr is not None and len(pr) > 2:
                add(pr[2])
            for w in vehwpns.get(key, []):
                weapon(w)
    sch = games.game_file(dst.folder, base + ".sch") if dst.folder else None
    have = set()
    if sch:
        try:
            for tag, c in sound.chunks(open(sch, "rb").read()):
                if tag == b"BANK":
                    have.add(struct.unpack_from("<I", c, 8)[0])
        except OSError:
            pass
    return [b for b in wanted if sound.bank_key(b) not in have]


def _source_table(src, name):
    """A source game's catalog table, [] when it has none (or isn't readable)."""
    from .porting import _table
    try:
        return _table(src, name)
    except Exception:                                       # noqa: BLE001 - a game without that table
        return []


def _lights(rec, limit=6):
    """The light ids a record's list names, at most ``limit`` (Desert Storm: more = an info box per instance), the
    sun (0) when it names none."""
    ids = [rec.get("light%d" % k) for k in range(rec.get("light_count") or 0)]
    ids = [i for i in ids if isinstance(i, int)]
    return tuple(ids[:limit]) or (0,)


# ------------------------------------------------------------------------------------------------ render effects --

_RFX_MAPS = ("DETAIL_MAP", "NORMAL_MAP", "CUBE_MAP", "ENV_MAP")


# ------------------------------------------------------------------------------------------------------- map --

# script API slots (offsets into the table a mission DLL gets) of "load the map picture" and "map bounds (max, min)":
# Desert Storm +0x4C / +0x54, Desert Storm II +0x74 / +0x7C (Mission01.dll 0x100010A9 / 0x100010EE)
MAP_SLOTS = {"ds1": (0x4C, 0x54), "ds2": (0x74, 0x7C)}


def source_map(src, level):
    """(image list name, (max, min)) the source level's script DLL gives its map screen, or None (no DLL, no map -
    Desert Storm II's Training 3 has none - or a call this reader doesn't follow). Read from the code: the push of the
    .img name before the image call, then the bounds call's two vectors, built on the stack by six movs (a register
    stands for 0.0, only in the height, the value the scripts leave out)."""
    import os
    from .missionflow import Dll
    slots = MAP_SLOTS.get(src.game.id)
    if not slots or not src.folder or not os.path.isdir(src.folder):
        return None
    path = next((os.path.join(src.folder, f) for f in os.listdir(src.folder)
                 if f.upper() == level.upper() + ".DLL"), None)
    if not path:
        return None
    dll = Dll(path)
    img = bytes(dll.img)
    for m in re.finditer(rb"GW[A-Za-z0-9_]*\.img\0", img, re.I):
        va = dll.base + m.start()
        site = img.find(b"\x68" + struct.pack("<I", va))
        if site < 0:
            continue
        p, seen_image, stack = dll.base + site, False, {}
        for _ in range(80):
            ins = dll.insn(p)
            if ins is None:
                break
            p += ins.size
            text = ins.op_str
            if ins.mnemonic == "call" and re.fullmatch(r"dword ptr \[e\w\w \+ 0x%x\]" % slots[0], text):
                seen_image = True
            elif ins.mnemonic == "call" and seen_image and re.fullmatch(r"dword ptr \[e\w\w \+ 0x%x\]" % slots[1], text):
                vals = [stack[k] for k in sorted(stack)][-6:]
                offs = sorted(stack)[-6:]
                if len(vals) < 6 or any(b - a != 4 for a, b in zip(offs, offs[1:])) or None in vals[0::3] + vals[2::3]:
                    return None
                a, b = [0.0 if v is None else v for v in vals[:3]], [0.0 if v is None else v for v in vals[3:]]
                return m.group(0)[:-1].decode("latin-1"), (tuple(map(max, a, b)), tuple(map(min, a, b)))
            elif ins.mnemonic == "call":
                if seen_image:
                    stack.clear()
            elif seen_image and ins.mnemonic == "mov":
                mm = re.fullmatch(r"dword ptr \[esp \+ (0x[0-9a-f]+|\d+)\], (.+)", text)
                if mm:
                    v = mm.group(2)
                    stack[int(mm.group(1), 0)] = struct.unpack("<f", struct.pack("<I", int(v, 0)))[0] \
                        if re.fullmatch(r"0x[0-9a-f]+|\d+", v) else None
        return None
    return None


def _img_pages(data):
    """The page (texture) names of an image list (imgf, versions 1 and 2: u16 version, u16 images, u16 32, u16 pages,
    then 16-byte page names)."""
    if data[:4] != b"imgf":
        return []
    n = struct.unpack_from("<H", data, 10)[0]
    return [data[12 + 16 * k:28 + 16 * k].split(b"\0")[0].decode("latin-1") for k in range(n)]


def _img_rename_page(data, old, new):
    """The image list with one page renamed (at most 15 characters - the field is 16 bytes)."""
    if len(new) > 15:
        raise PortError("page name %s is longer than 15 characters" % new)
    out = bytearray(data)
    for k, name in enumerate(_img_pages(data)):
        if name == old:
            out[12 + 16 * k:28 + 16 * k] = new.encode("latin-1").ljust(16, b"\0")
    return bytes(out)


def rfx_file(src, text, model_names, prefix, files, native=()):
    """(.RFX text for the ported level, notes): the source level's render effects (<LEVEL>.RFX - the game loads
    <scene>.rfx: detail textures on the zones' and props' base textures, bump / reflection maps) with the models
    renamed (`model_names`: source model -> ported name; entries of models that didn't come over are dropped), base
    textures prefixed like the ported models' and the effect textures (detail / normal / cube / env maps) copied into
    `files` under prefixed names. Without it the game draws the zones' ground with whatever its second texture stage
    held - smeared lines (Training 3 / Mission 1 ground). Shader modes Desert Storm doesn't have (WATERBLEND) drop
    their entry."""
    entries, cur = [], None
    for line in text.replace("\r", "").split("\n"):
        s = line.strip()
        if s.startswith("REFX_"):
            cur = [line]
            entries.append(cur)
        elif cur is not None and s:
            cur.append(line)
    out, kept, dropped, copied = [], 0, 0, set()

    def tex(name):
        new = (prefix + name)[:63]
        if new not in copied:
            copied.add(new)
            for ext in (".DDS", ".TGA"):
                data = src.get(name + ext)
                if data is not None:
                    files[new + ext] = data
                    break
        return new
    for e in entries:
        body = "\n".join(e)
        if "WATERBLEND" in body:
            dropped += 1
            continue
        head = e[0].split()
        m = re.match(r'\s*(REFX_\w+)\s+"([^"]*)"', e[0])
        if m and head[0] not in ("REFX_DEFAULT_GLAZIER_REFLECT",):
            model = m.group(2)
            new = model_names.get(model.upper())
            if new is None:
                dropped += 1
                continue
            body = body.replace('"%s"' % model, '"%s"' % new, 1)
        if not (m and m.group(2).upper() in native):     # Desert Storm's own models keep their textures' names
            body = re.sub(r'BASE_MAP\s+"([^"]*)"', lambda k: 'BASE_MAP "%s"' % (prefix + k.group(1))[:63], body)
        for key in _RFX_MAPS:
            body = re.sub(r'%s\s+"([^"]*)"' % key, lambda k, key=key: '%s "%s"' % (key, tex(k.group(1))), body)
        out.append(body)
        kept += 1
    notes = ["render effects: %d entr%s (detail / bump / reflection maps), %d texture(s) copied%s" % (
        kept, "y" if kept == 1 else "ies", len(copied),
        ", %d dropped (models that didn't come over, water shaders)" % dropped if dropped else "")]
    return "\r\n".join(["// %s render effects, ported by the ModKit level porter" % src.game.short, ""] + out +
                       [""]), notes


# ------------------------------------------------------------------------------------------------ sky, ambience --

SKY_KINDS = ("SKYTEXTURE", "SKYTILEFACTOR", "SKYFIXEDY", "SKYSPEED", "SKYCURVATURE", "SKYHEIGHT")


def sky_lines(scene, prefix, src, files):
    """{kind: [lines]} of the source's sky layers (two in both games, the same six lines each - Desert Storm II's
    LAYERFLAGS isn't read by Desert Storm), sky textures prefixed and copied into `files`."""
    out = {}
    for r in scene.records:
        if r.kind not in SKY_KINDS:
            continue
        line = r.text
        if r.kind == "SKYTEXTURE":
            m = re.search(r'"([^"]*)"', line)
            if m:
                new = (prefix + m.group(1))[:63]
                for ext in (".DDS", ".TGA"):
                    data = src.get(m.group(1) + ext)
                    if data is not None:
                        files[new + ext] = data
                        break
                line = line.replace('"%s"' % m.group(1), '"%s"' % new)
        out.setdefault(r.kind, []).append(line)
    return out


def ambient_pack(src, level, scene):
    """(.sch bank pack bytes or None, notes) with the samples the source scene's ambient sounds (ENVSND) play: both
    games key them by the ELF hash of the name without its extension ("01CicadasLoop.psf" -> 01CicadasLoop) in the
    level's sound cache, same PFSM sample format. DesertStormFix adds a mod's .sch packs to the level cache (samples
    it lacks)."""
    import struct
    from . import games, sound
    names = sorted({str(r["sound"]) for r in scene.records if r.kind == "ENVSND" and r.fields})
    if not names:
        return None, []
    path = games.game_file(src.folder, level.lower() + ".sch") if src.folder else None
    if not path:
        return None, ["ambient sounds: %s's %s.sch not found - %d sound(s) silent" % (src.game.short, level,
                                                                                    len(names))]
    with open(path, "rb") as f:
        found = sound.samples(sound.chunks(f.read()))
    chunks, missing = [], []
    for n in names:
        key = sound.elf_hash(n.rsplit(".", 1)[0])
        if key in found:
            chunks += found[key]
        else:
            missing.append(n)
    notes = ["ambient sounds: %d sample(s) packed%s" % (len(names) - len(missing), ", not in the cache: " +
                                                       ", ".join(missing) if missing else "")]
    return (sound.build(chunks) if chunks else None), notes


# ------------------------------------------------------------------------------------------------- maps --

MAP_LEVELS = {"ds2": (["MISSION%02d" % n for n in range(1, 11)], "Mission3")}
_MERGED_TABLES = {"DOORS.TXT", "FEATS.TXT", "STRUCTS.TXT", "FEATSTAT.TXT", "TARGETS.TXT", "INVEN.TXT"}
MAPS_VERSION = 1


def convert_maps(src, dst, out_dir, levels=None, base=None, log=print):
    """A source game's mission maps as empty Desert Storm levels for modders, into `out_dir` (DesertStormFix serves
    <game>/PSE/Ports/<id> like a mod): each level's scenery (port_level with empty=True: zones, props, collision,
    lights, routes, the squad start - no soldiers / vehicles placed, no mission logic) as <level>.lua, plus every
    model, texture, weapon, vehicle and soldier type the maps use. No campaign: the source's mission scripts stay
    out (crossmission.py keeps the script adapter for the editor). The folder is emptied first (it is only ever this
    converter's output). port.txt records what it was made from. Returns the report lines."""
    import hashlib
    from . import games
    clevels, cbase = MAP_LEVELS[src.game.id]
    levels = [lv.upper() for lv in (levels or clevels)]
    base = base or cbase
    os.makedirs(out_dir, exist_ok=True)
    if os.path.isfile(os.path.join(out_dir, "port.txt")):
        for fn in os.listdir(out_dir):
            p = os.path.join(out_dir, fn)
            if os.path.isfile(p):
                os.remove(p)
    files, owner, report, done = {}, {}, [], []
    for level in levels:
        log("map %s" % level)
        try:
            lfiles, notes, _name = port_level(src, dst, level, base=base, name=level, log=lambda s: None, empty=True)
        except PortError as e:
            report.append("[fail] %s: %s" % (level, e))
            continue
        for fn, data in lfiles.items():
            key = fn.upper()
            if key in _MERGED_TABLES and key in files:
                have = files[key].decode("latin-1").splitlines()
                new = [ln for ln in data.decode("latin-1").splitlines() if ln.strip() and ln not in have]
                files[key] = ("\r\n".join(have + new) + "\r\n").encode("latin-1")
            elif key in files and files[key] != data:
                report.append("  [note] %s differs between %s and %s - %s's kept" % (fn, owner[key], level,
                                                                                  owner[key]))
            else:
                files[key], owner[key] = data, level
        done.append(level)
        report.append("%s -> %s.lua (%d files)" % (level, level.lower(), len(lfiles)))
        report += ["    " + n for n in notes]
    for fn, data in files.items():
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(data)
    exe = games.game_file(src.folder, src.game.exes[0]) if src.game.exes else None
    sha = hashlib.sha256(open(exe, "rb").read()).hexdigest().upper() if exe else "?"
    with open(os.path.join(out_dir, "port.txt"), "w", encoding="latin-1", newline="\r\n") as f:
        f.write("# Converted from your own copy of %s - never shared: every player converts it themselves.\n"
                % src.game.short)
        f.write("# Empty maps for modders: run one with DesertStorm.exe +level <level>.lua or open it in the editor.\n")
        f.write("game = %s\nsource exe sha256 = %s\nmaps = %d\nlevels = %s\n" % (
            src.game.id, sha, MAPS_VERSION, " ".join(lv.lower() + ".lua" for lv in done)))
    report.insert(0, "%s maps: %d of %d level(s) converted into %s" % (src.game.short, len(done), len(levels),
                                                                         out_dir))
    return report
