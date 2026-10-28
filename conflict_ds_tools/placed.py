"""Placed references - what a level puts in its world (PSE DESIGN §13.3 "references"): the objects, waypoints and
regions of a level's scene (<LEVEL>.ENV) as records with editor IDs, linked to their base forms (forms.py).

    docs = forms.generate(game_dir, schema)               # the base forms (for the links)
    doc = placed.level_refs(game_dir, "mission1", docs)   # pse_data.Document: refs of Mission 1

IDs: "ds1:<level>/<kind>/<editor id in lower case>" - kind object / waypoint / region. The editor IDs are the names
the readable mission scripts use (mission2lua_readable.refs_from_env: obj.HERO_FOLEY, wp.CELL_BLOCK_GUARD_0,
rgn.PICKUP_BLACKHAWK_TRIGGER), so a mission and its records name things the same way.

  object    base (the record of its unit: ds1:unit/... / ds1:vehicle/... / ds1:structure/... ...), editor_id, script_id,
            pos / rot ({ x, y, z }), in_play, model, zone, and the scene record's other fields as they are
  waypoint  editor_id, number, pos, rot, wait_ms, next (the next waypoint's ID - the chain a patrol walks)
  region    editor_id, index, shape (sphere / box), pos and the shape's fields

Made from the player's own files, never shipped. Read-only for now: edits go back into the scene with the editor.
"""
import os
import re

from . import archive, env as envmod, pse_data
from .forms import ID_PREFIX
from .mission2lua_readable import refs_from_env

# record types an HLOI's unit name is looked up in, in this order (a key in several: the first wins)
BASE_TYPES = ("unit", "vehicle", "structure", "feature", "door", "target", "weapon", "item", "switch")


def _lower(s):
    return s.translate({c: c + 32 for c in range(65, 91)})


def _vec(t):
    return pse_data.from_python({"x": float(t[0]), "y": float(t[1]), "z": float(t[2])})


def _value(v):
    if isinstance(v, tuple):
        return pse_data.from_python([float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else x
                                     for x in v])
    return pse_data.from_python(v)


def base_index(docs):
    """{lower key: record id} over the base forms, BASE_TYPES order."""
    out = {}
    for rtype in BASE_TYPES:
        doc = docs.get(rtype)
        if not doc:
            continue
        for r in doc.root["records"].items:
            if isinstance(r, pse_data.Table) and r.get("key") and r.get("id") and "#" not in r["id"]:
                out.setdefault(_lower(r["key"]), r["id"])
    return out


def level_refs(game_dir, level, docs, game_files=None):
    """pse_data.Document with a level's placed references, or None when the level has no scene."""
    gf = game_files or archive.GameFiles(game_dir)
    scene = level.upper() + ".ENV"
    data = gf.get(scene)
    if data is None:
        return None
    names = refs_from_env(data)
    e = envmod.EnvFile.read(data)
    bases = base_index(docs)
    lvl = _lower(level)
    refs = pse_data.Table()

    def rid(kind, editor):
        return "ds1:%s/%s/%s" % (lvl, kind, _lower(editor))

    seen = set()
    for r in sorted(e.find("HLOI"), key=lambda r: r.as_dict().get("script_id", 0)):
        d = r.as_dict()
        sid = d.get("script_id")
        if sid is None or sid < 0 or sid in seen or sid not in names["objects"]:
            continue
        seen.add(sid)
        editor = names["objects"][sid]
        t = pse_data.Table()
        t.set("id", rid("object", editor))
        t.set("editor_id", editor)
        unit = str(d.get("unit") or "")
        base = bases.get(_lower(unit))
        if base:
            t.set("base", base)
        t.set("unit", unit)
        t.set("script_id", sid)
        t.set("pos", _vec(d["pos"]))
        t.set("rot", _vec(d["rot"]))
        t.set("in_play", bool(d.get("in_play")))
        scene_fields = pse_data.Table()
        for k, v in d.items():
            if k in ("line", "kind", "pos", "rot", "unit", "script_id", "in_play"):
                continue
            scene_fields.set(k, _value(v))
        t.set("scene", scene_fields)
        refs.push(t)

    wp_names = names["waypoints"]
    for r in e.find("NEWENVWAYPT"):
        d = r.as_dict()
        n = d.get("id")
        if n not in wp_names:
            continue
        editor = wp_names[n]
        t = pse_data.Table()
        t.set("id", rid("waypoint", editor))
        t.set("editor_id", editor)
        t.set("number", n)
        t.set("pos", _vec(d["pos"]))
        t.set("rot", _vec(d["rot"]))
        t.set("wait_ms", d.get("wait_ms", 0))
        nxt = d.get("next", -1)
        if nxt is not None and nxt >= 0:
            t.set("next", rid("waypoint", wp_names[nxt]) if nxt in wp_names else nxt)
        refs.push(t)

    for k, r in enumerate(e.find("REGN")):
        d = r.as_dict()
        editor = names["regions"][k]
        t = pse_data.Table()
        t.set("id", rid("region", editor))
        t.set("editor_id", editor)
        t.set("index", k)
        t.set("shape", "sphere" if d.get("type") == 1 else "box")
        t.set("pos", _vec(d["pos"]))
        for f, v in d.items():
            if f not in ("line", "kind", "name", "type", "pos"):
                t.set(f, _value(v))
        refs.push(t)

    root = pse_data.Table()
    root.set("level", lvl)
    root.set("scene", scene)
    root.set("refs", refs)
    return pse_data.Document(root, [
        "%s placed references - made from your own game files (%s); not shipped, not for editing" % (lvl, scene)])


LEVELS = ["training1", "training2", "training3", "mission1", "mission2", "mission3", "mission4", "mission5a",
          "mission5b", "mission6", "mission7", "mission8", "mission9", "mission10", "mission11a", "mission11b",
          "mission12a", "mission12b"]


# ---- placed-reference edits -> the level's scene ----
# Same edit syntax as table records (data-files.md "Changing records from a mod"): { id = "ds1:mission1/object/
# tent_guard", pos = { x = ..., y = ..., z = ... }, in_play = false }, { id = ..., based_on = <another object of that
# level> } (a copy with the next free script id; its editor ID becomes its scene name), { id = ..., delete = true }.
# Objects: pos, rot, in_play, base (a record id -> its unit key), scene = { field = value } (scene record fields).
# Waypoints: pos, rot, wait_ms, next (a waypoint id, or -1). Regions: pos and the shape's fields.
# delete on an object takes it out of play instead of removing its line: the game's precomputed level data (octree,
# walk map) refers to scene objects by their place in the file.

def _ref_parts(rid):
    """"ds1:mission1/object/tent_guard" -> ("mission1", "object", "tent_guard") or None."""
    if not isinstance(rid, str) or not rid.startswith(ID_PREFIX + ":"):
        return None
    parts = rid[len(ID_PREFIX) + 1:].split("/")
    return tuple(parts) if len(parts) == 3 else None


def _xyz(v):
    if isinstance(v, pse_data.Table):
        return (v.get("x", 0.0), v.get("y", 0.0), v.get("z", 0.0))
    return None


_DELETE = object()   # an edit's delete = true, as a value for the conflict check


def _upper(s):
    return s.translate({c: c - 32 for c in range(97, 123)})


def _show(v):
    """A value in a report line (the same text as DesertStormFix's twin, pse/records/placed.cpp)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return pse_data.format_float(v)
    if isinstance(v, pse_data.Table):
        return "a table"
    return str(v)


def _set(rec, f, v):
    """rec[f] = v for an edit's value: a table only into a vector field ({ x, y, z }), a vector field only from one."""
    vec = isinstance(f, str) and f + ".x" in rec.fields and f not in rec.fields
    if isinstance(v, pse_data.Table):
        if not vec:
            if f in rec.fields:
                raise ValueError("%s takes a value, not a table" % f)
            raise KeyError("%s has no field %r" % (rec.kind, f))
        rec[f] = _xyz(v)
    elif vec:
        raise ValueError("%s takes { x = ..., y = ..., z = ... }" % f)
    else:
        rec[f] = v


def apply_edits(env, level, edits, docs=None):
    """Applies the placed-reference edits of one level (a list of (mod, file, entries) as forms.load_mod_edits gives,
    only the entries whose id is ds1:<level>/...) to an EnvFile in place. Returns the report lines."""
    from .mission2lua_readable import refs_from_env
    keys = {}
    for doc in (docs or {}).values():
        for r in doc.root["records"].items:
            if isinstance(r, pse_data.Table) and r.get("id") and r.get("key") is not None:
                keys[r["id"]] = r["key"]
    lvl = _lower(level)
    report = []

    def fail(mod, file, msg):
        report.append("[fail] %s/%s: %s" % (mod, file, msg))

    def lookup():
        """{kind: {editor id: record}}, HLOI by script id, waypoint numbers - rebuilt after lines are added (EnvFile
        re-reads its records then; an older Record would point at the wrong line)."""
        names = refs_from_env(env.to_bytes())
        by = {"object": {}, "waypoint": {}, "region": {}}
        hloi = {r.as_dict().get("script_id"): r for r in env.find("HLOI")}
        for sid, editor in names["objects"].items():
            if sid in hloi:
                by["object"][_lower(editor)] = hloi[sid]
        wps = {r.as_dict().get("id"): r for r in env.find("NEWENVWAYPT")}
        numbers = {}
        for n, editor in names["waypoints"].items():
            if n in wps:
                by["waypoint"][_lower(editor)] = wps[n]
                numbers[_lower(editor)] = n
        for k, r in enumerate(env.find("REGN")):
            by["region"][_lower(names["regions"][k])] = r
        return by, hloi, numbers

    todo = []
    for mod, file, entries in edits:
        for e in entries:
            if not isinstance(e, pse_data.Table):
                continue
            parts = _ref_parts(e.get("id"))
            if parts and _lower(parts[0]) == lvl:
                todo.append((mod, file, e, parts[1], parts[2]))
    # pass 1: new objects (copies go to the end of the HLOI section - lines are added)
    by_editor, hloi, wp_number = lookup()
    for mod, file, e, kind, editor in todo:
        if kind not in by_editor or editor in by_editor[kind]:
            continue
        base = _ref_parts(e.get("based_on"))
        src = by_editor[kind].get(base[2]) if base and _lower(base[0]) == lvl and base[1] == kind else None
        if kind != "object" or src is None:
            continue   # reported in pass 2
        sid = max(s for s in hloi if isinstance(s, int)) + 1
        want = e.get("script_id")   # a copy may ask for its script id (mission logic refers to objects by it)
        if isinstance(want, int) and not isinstance(want, bool) and want >= 0:
            if want in hloi:
                fail(mod, file, "%s script id %d is taken - it gets %d" % (e["id"], want, sid))
            else:
                sid = want
        env.duplicate(src, script_id=sid, stat=_upper(editor))
        report.append("new %s based on %s, script id %d (%s)" % (e["id"], e["based_on"], sid, mod))
        by_editor, hloi, wp_number = lookup()
    # pass 2: fields (no lines added or removed). Two mods giving one field different values = a conflict line (the
    # later mod wins), as forms.merge: noted before the value is set; a scene = { ... } part is "scene.<field>", a
    # delete is an in_play value.
    setby = {}

    def note(e, kind, editor, field, mod, value):
        text = "delete" if value is _DELETE else pse_data.dumps(pse_data.Document(pse_data.from_python({"v": value})))
        prev = setby.get((kind, editor, field))
        if prev and prev[0] != mod and prev[1] != text:
            report.append("conflict: %s %s: %s / %s (%s wins)" % (e["id"], field, prev[0], mod, mod))
        setby[(kind, editor, field)] = (mod, text)

    for mod, file, e, kind, editor in todo:
        if kind not in by_editor:
            fail(mod, file, "%s - no placed kind %s" % (e["id"], kind))
            continue
        rec = by_editor[kind].get(editor)
        if rec is None:
            fail(mod, file, "%s isn't in %s's scene - a new one needs based_on = <an object of this level>"
                 % (e["id"], lvl))
            continue
        if e.get("delete") is True:
            if kind == "object":
                note(e, kind, editor, "in_play", mod, _DELETE)
                rec["in_play"] = 0
                report.append("%s taken out of play (%s)" % (e["id"], mod))
            else:
                fail(mod, file, "%s - only objects can be deleted (set it elsewhere instead)" % e["id"])
            continue
        for ent in e.entries:
            f, v = ent.key, ent.value
            if f in ("id", "based_on", "delete", "editor_id", "script_id", "number", "index", "shape"):
                continue
            if f == "scene" and kind == "object" and isinstance(v, pse_data.Table):
                for sub in v.entries:
                    if isinstance(sub.key, str):
                        note(e, kind, editor, "scene." + sub.key, mod, sub.value)
            elif isinstance(f, str):
                note(e, kind, editor, f, mod, v)
            try:
                if f == "in_play" and kind == "object":
                    rec["in_play"] = 1 if v else 0
                elif f == "base" and kind == "object":
                    if not isinstance(v, str) or v not in keys:
                        fail(mod, file, "%s base %s isn't a record" % (e["id"], _show(v)))
                        continue
                    rec["unit"] = keys[v]
                elif f == "scene" and kind == "object" and isinstance(v, pse_data.Table):
                    for sub in v.entries:
                        _set(rec, sub.key, sub.value)
                elif f == "next" and kind == "waypoint":
                    if isinstance(v, int):
                        rec["next"] = v
                    else:
                        p = _ref_parts(v)
                        if not p or p[2] not in wp_number:
                            fail(mod, file, "%s next %s isn't a waypoint of %s" % (e["id"], _show(v), lvl))
                            continue
                        rec["next"] = wp_number[p[2]]
                else:
                    _set(rec, f, v)
            except (KeyError, ValueError) as err:
                fail(mod, file, "%s %s can't be set: %s" % (e["id"], f, err))
    return report


def edit_levels(edits):
    """The levels the edits name (as written, sorted)."""
    levels = set()
    for _mod, _file, entries in edits:
        for e in entries:
            p = _ref_parts(e.get("id")) if isinstance(e, pse_data.Table) else None
            if p:
                levels.add(_lower(p[0]))   # the level part ignores case: ds1:Mission1/... = ds1:mission1/...
    return sorted(levels)


def merged_scenes(game_dir, edits, docs=None, game_files=None):
    """What DesertStormFix does at start-up with the mods' placed-reference edits (its twin pse/records/placed.h):
    every named level's scene with the edits -> ({"<LEVEL>.ENV": bytes}, report lines). The bytes are the game's scene
    with the edits' .envpatch applied (the lines the patch leaves alone stay as the file has them)."""
    from . import gamerefs
    gf = game_files or archive.GameFiles(game_dir)
    scenes, report = {}, []
    for lvl in edit_levels(edits):
        scene = _upper(lvl) + ".ENV"
        data = gf.get(scene)
        if data is None:
            report.append("[fail] no scene %s" % scene)
            continue
        base = envmod.EnvFile.read(data)
        env = envmod.EnvFile.read(data)
        report += apply_edits(env, lvl, edits, docs)
        raw = data.decode("latin-1")
        if "\r\n" in raw:
            raw = re.sub(r"(?<!\r)\n", "\r\n", raw)
        eol = "\r\n" if "\r\n" in raw else "\n"
        lines = gamerefs.apply_env_patch(gamerefs.make_env_patch(base.lines, env.lines, scene), raw.split(eol))
        scenes[scene] = eol.join(lines).encode("latin-1")
    return scenes, report


def write_envpatches(game_dir, mod_dir, edits, docs=None, game_files=None):
    """Applies every level's placed-reference edits to the player's scene and writes <mod>/<LEVEL>.envpatch (only
    the line changes - no game data). Returns {level: report lines}."""
    from . import gamerefs
    gf = game_files or archive.GameFiles(game_dir)
    out = {}
    for lvl in edit_levels(edits):
        scene = lvl.upper() + ".ENV"
        data = gf.get(scene)
        if data is None:
            out[lvl] = ["[fail] no scene %s" % scene]
            continue
        base = envmod.EnvFile.read(data)
        env = envmod.EnvFile.read(data)
        out[lvl] = apply_edits(env, lvl, edits, docs)
        patch = gamerefs.make_env_patch(base.lines, env.lines, scene)
        os.makedirs(mod_dir, exist_ok=True)
        with open(os.path.join(mod_dir, lvl.upper() + ".envpatch"), "w", encoding="latin-1", newline="\n") as f:
            f.write(patch)
    return out


# ---- an edited scene -> placed-reference edits + the rest as scene lines (the editor's save) ----
# Mods merge record edits field by field (data-files.md "Several mods, one level"), so the editor saves what it can as
# them: changed objects / waypoints / regions and copies of objects; everything else (world pieces, lights, deleted
# lines, new waypoints ...) stays scene lines for the .envpatch. Every edit is checked by replaying: the remaining
# lines + the edits (apply_edits, as the game does) must give the edited scene exactly - an edit that doesn't stays a
# line.

def _fields(rec):
    d = rec.as_dict()
    d.pop("line", None)
    d.pop("kind", None)
    return d


def _edit_value(v):
    if isinstance(v, tuple) and len(v) == 3:
        return pse_data.from_python({"x": float(v[0]), "y": float(v[1]), "z": float(v[2])})
    return v


def _field_edit(rid, kind, base_fields, new_fields, fixed):
    """The edit that makes a record of base_fields into new_fields (None: nothing an edit can say)."""
    if set(base_fields) != set(new_fields):
        return None   # a list grew or shrank (kit, links): the line itself
    changed = [k for k in new_fields if new_fields[k] != base_fields[k]]
    if not changed or any(k in fixed for k in changed):
        return None
    t = pse_data.Table()
    t.set("id", rid)
    for k in changed:
        v = new_fields[k]
        t.set(k, bool(v) if (k == "in_play" and kind == "object") else _edit_value(v))
    return t


def split_edits(base_data, edited, level):
    """(edits, lines): placed-reference edits (pse_data Tables) and the scene lines the .envpatch keeps, for a shipped
    level's scene (base_data: its bytes) edited into `edited` (an EnvFile). The game builds the edited scene back from
    them: the lines (as an .envpatch of the base) first, then the edits."""
    lvl = _lower(level)
    base = envmod.EnvFile.read(base_data)
    if base.version >= 190 or edited.version != base.version:
        return [], list(edited.lines)   # Desert Storm's layouts only
    names = refs_from_env(base_data)

    def rid(kind, editor):
        return "ds1:%s/%s/%s" % (lvl, kind, _lower(editor))

    def first_by(recs, key):
        out, twice = {}, set()
        for r in recs:
            k = r.get(key)
            if k in out:
                twice.add(k)
            else:
                out[k] = r
        return {k: r for k, r in out.items() if k not in twice and k is not None}

    cands = []   # {"line": index in edited, "base": base line text (None = a new line), "entry": Table}
    bh = first_by(base.find("HLOI"), "script_id")
    eh = first_by(edited.find("HLOI"), "script_id")
    for sid, r in eh.items():
        if sid in bh:
            b = bh[sid]
            if r.text != b.text and sid in names["objects"]:
                e = _field_edit(rid("object", names["objects"][sid]), "object", _fields(b), _fields(r),
                                ("script_id", "stat"))
                if e is not None:
                    cands.append({"line": r.index, "base": b.text, "entry": e})
            continue
        # a new object: a copy of the most alike object of the base scene (same unit + model, the same fields)
        stat = str(r.get("stat") or "")
        known = {_lower(v) for v in names["objects"].values()}
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", stat) or not re.search(r"[A-Z]", stat) or stat == "NONE" \
                or _lower(stat) in known:
            continue
        nf = _fields(r)
        best = None
        for bsid, b in bh.items():
            bf = _fields(b)
            if bsid not in names["objects"] or bf.get("unit") != nf.get("unit") or bf.get("model") != nf.get("model") \
                    or set(bf) != set(nf):
                continue
            diff = sum(1 for k in nf if k not in ("script_id", "stat") and nf[k] != bf[k])
            if best is None or diff < best[0]:
                best = (diff, bsid, bf)
        if best is None:
            continue
        t = pse_data.Table()
        t.set("id", rid("object", stat))
        t.set("based_on", rid("object", names["objects"][best[1]]))
        t.set("script_id", nf["script_id"])
        for k in nf:
            if k not in ("script_id", "stat") and nf[k] != best[2][k]:
                t.set(k, bool(nf[k]) if k == "in_play" else _edit_value(nf[k]))
        cands.append({"line": r.index, "base": None, "entry": t})
    bw = first_by(base.find("NEWENVWAYPT"), "id")
    for n, r in first_by(edited.find("NEWENVWAYPT"), "id").items():
        b = bw.get(n)
        if b is not None and r.text != b.text and n in names["waypoints"]:
            e = _field_edit(rid("waypoint", names["waypoints"][n]), "waypoint", _fields(b), _fields(r), ("id", "name"))
            if e is not None:
                cands.append({"line": r.index, "base": b.text, "entry": e})
    br, er = base.find("REGN"), edited.find("REGN")
    if [r.get("name") for r in br] == [r.get("name") for r in er]:   # regions are known by their place in the list
        for k, (b, r) in enumerate(zip(br, er)):
            if r.text != b.text:
                e = _field_edit(rid("region", names["regions"][k]), "region", _fields(b), _fields(r), ("name", "type"))
                if e is not None:
                    cands.append({"line": r.index, "base": b.text, "entry": e})
    cands.sort(key=lambda c: c["line"])

    def lines_for(accepted):
        by = {c["line"]: c for c in accepted}
        out = []
        for i, line in enumerate(edited.lines):
            c = by.get(i)
            if c is None:
                out.append(line)
            elif c["base"] is not None:
                out.append(c["base"])
        return out

    accepted = cands
    while accepted:
        lines = lines_for(accepted)
        env = envmod.EnvFile(edited.eol.join(lines), edited.eol)
        report = apply_edits(env, lvl, [("", "", [c["entry"] for c in accepted])])
        if env.lines == edited.lines and not any(l.startswith("[fail]") for l in report):
            return [c["entry"] for c in accepted], lines
        if len(env.lines) == len(edited.lines):
            bad = {i for i, (a, b) in enumerate(zip(env.lines, edited.lines)) if a != b}
            drop = [c for c in accepted if c["line"] in bad]
        else:
            drop = []
        if not drop:   # lines in other places: the copies (they move lines) first, then everything
            drop = [c for c in accepted if c["base"] is None] or accepted
        accepted = [c for c in accepted if all(c is not d for d in drop)]
    return [], list(edited.lines)
