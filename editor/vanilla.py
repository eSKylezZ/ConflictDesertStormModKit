"""Vanilla lineage: every record of the open scene related back to the shipped game's scene.

"Vanilla" = the base level's scene as it ships (the extracted <LEVEL>.ENV that open_level reads - also for a saved
mission, whose scene is that file + its .envpatch). New levels (new_level) have no vanilla scene.

A record is matched to its vanilla counterpart by identity, not by line (lines shift with every insert / delete):
  HLOI                      script id (unique per level; the DLL addresses objects by it)
  NEWENVWAYPT / NEWENVMARKER id
  everything with a name    kind + name (ENVI names are unique in the shipped scenes; duplicate() keeps the name, so
                            the n-th record of a name matches the n-th vanilla one - copies go to the section end)
Renaming a record (or changing a script id) therefore makes it "custom" and its vanilla original "deleted".

status: "vanilla" (same tokens as shipped), "modified" (changed fields listed), "custom" (no vanilla counterpart).
"""
from __future__ import annotations

from commands import CommandError, EnvFile, Session, _find_file, command, get_record
import logic

_NAMED = {"ENVI", "REGN", "ENVSND", "SWITCH", "EMITTER", "PORT", "DLGT", "SLGT", "PLGT",
          "CAMFREE", "CAMTARGPOS", "CAMTARGOBJ", "CAMCHASEOBJ"}
_BY_ID = {"HLOI": "script_id", "NEWENVWAYPT": "id", "NEWENVMARKER": "id"}
# the scene's atmosphere lines (logic.get_atmosphere / set_atmosphere): reset kind by kind, in order
ATMOS_KINDS = ["TIMEOFDAY", "FOGSTART", "FOGSTOP", "XFOGSTART", "XFOGSTOP", "SKYTEXTURE", "SKYTILEFACTOR", "SKYFIXEDY",
               "SKYSPEED", "SKYCURVATURE", "SKYHEIGHT", *logic.COLOUR_KEYS]


def vanilla_env(s: Session) -> EnvFile | None:
    """The shipped scene of the open level (parsed once per level), None for new levels."""
    if s.env is None or not s.level or getattr(s, "blank", False) or not s.folder:
        return None
    cached = getattr(s, "_vanilla", None)
    if cached is not None and cached[0] == s.level:
        return cached[1]
    path = _find_file(s.folder, s.level + ".ENV")
    env = EnvFile.load(path) if path else None
    s._vanilla = (s.level, env)
    return env


def _ident(r):
    if r.kind in _BY_ID:
        return (r.kind, r[_BY_ID[r.kind]])
    if r.kind in _NAMED:
        return (r.kind, str(r["name"]).upper())
    return None


def _keys(env: EnvFile) -> dict:
    """{record index: (kind, identity, occurrence)} for the records lineage covers."""
    out, seen = {}, {}
    for r in env.records:
        if not r.fields:
            continue
        try:
            base = _ident(r)
        except KeyError:
            continue
        if base is None:
            continue
        n = seen.get(base, 0)
        seen[base] = n + 1
        out[r.index] = (*base, n)
    return out


def _lines(env: EnvFile, r) -> list[str]:
    return env.lines[r.index:r.index + 1 + len(r.children)]


def _values(env: EnvFile, r) -> list:
    out = [t.value for t in r.tokens]
    for c in r.children:
        out += ["|"] + [t.value for t in c.tokens]
    return out


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-4
        except (TypeError, ValueError):
            return False
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _changed_fields(cur, van) -> list[str]:
    a, b = cur.as_dict(), van.as_dict()
    names = [k for k in a if k not in ("line", "kind")] + [k for k in b if k not in a and k not in ("line", "kind")]
    return [k for k in names if k not in a or k not in b or not _same(a[k], b[k])]


class Lineage:
    """Lineage of the current scene against vanilla (rebuilt per call - cheap: one pass over each scene)."""

    def __init__(self, s: Session):
        self.s, self.van = s, vanilla_env(s)
        self.cur_keys = _keys(s.env) if self.van is not None else {}
        self.van_keys = _keys(self.van) if self.van is not None else {}
        self.van_by_key = {k: i for i, k in self.van_keys.items()}
        self.cur_by_key = {k: i for i, k in self.cur_keys.items()}

    def vanilla_of(self, rec):
        k = self.cur_keys.get(rec.index)
        i = self.van_by_key.get(k) if k else None
        return self.van.record_at(i + 1) if i is not None else None

    def status(self, rec):
        if self.van is None:
            return None
        v = self.vanilla_of(rec)
        if v is None:
            return "custom" if rec.index in self.cur_keys else None
        return "vanilla" if _values(self.s.env, rec) == _values(self.van, v) else "modified"

    def deleted(self):
        return [self.van.record_at(i + 1) for i, k in sorted(self.van_keys.items()) if k not in self.cur_by_key]


def _key_json(k):
    return {"kind": k[0], "id": k[1], "occurrence": k[2]}


def _brief(r) -> dict:
    out = {"kind": r.kind}
    for f in ("name", "model", "unit", "script_id", "pos", "rot"):
        v = r.get(f)
        if v is not None:
            out[f] = list(v) if isinstance(v, tuple) else v
    return out


def decorate_scene(s: Session, out: dict):
    """scene(): lineage per drawn record ("vanilla" / "modified" / "custom") and, for modified ones, where the
    vanilla record stood (the 3D view draws a ghost there)."""
    L = Lineage(s)
    if L.van is None:
        out["vanilla"] = None
        return
    counts = {"vanilla": 0, "modified": 0, "custom": 0}
    for items in out.values():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or "line" not in it:
                continue
            try:
                rec = s.env.record_at(it["line"])
            except KeyError:
                continue
            st = L.status(rec)
            if st is None:
                continue
            it["lineage"] = st
            counts[st] += 1
            if st == "modified":
                v = L.vanilla_of(rec)
                for f in ("pos", "rot", "model"):
                    val = v.get(f)
                    if val is not None:
                        it["vanilla_" + f] = list(val) if isinstance(val, tuple) else val
    out["vanilla"] = {**counts, "deleted": len(L.deleted())}


def _need(s: Session) -> Lineage:
    if s.env is None:
        raise CommandError("no level open")
    L = Lineage(s)
    if L.van is None:
        raise CommandError("this level has no vanilla scene (a new level)")
    return L


@command("Where a record comes from: its vanilla (shipped) counterpart, status vanilla / modified / custom, the "
         "changed fields with their vanilla values, and whether the base mission's DLL uses its script id.",
         line="record line")
def vanilla_info(s: Session, line):
    r = s.record(line)
    L = Lineage(s)
    out = {"line": r.index + 1, "kind": r.kind, "status": L.status(r), "has_vanilla_scene": L.van is not None}
    k = L.cur_keys.get(r.index)
    if k:
        out["identity"] = _key_json(k)
    v = L.vanilla_of(r) if L.van is not None else None
    if v is not None:
        cur, van = r.as_dict(), v.as_dict()
        out["vanilla_line"] = v.index + 1
        out["vanilla"] = {f: van[f] for f in van if f not in ("line", "kind")}
        out["changed"] = [{"field": f, "vanilla": van.get(f), "current": cur.get(f)} for f in _changed_fields(r, v)]
        out["vanilla_text"] = "\n".join(_lines(L.van, v))
    if r.kind == "HLOI":
        sid = r.get("script_id")
        slots = logic.base_targets(s).get(sid)
        if slots:
            out["dll_critical"] = {"script_id": sid, "slots": ["+0x%X" % x for x in slots]}
        unit = unit_row(s, r.get("unit"))
        if unit:
            out["unit"] = unit
    return out


# UNITS.TXT columns (0-based) the game reads per soldier type - shared by every soldier of that unit, so they are
# changed in the Records window (UNITS.replace.txt in a mod), not per object. Rank names as the exe lists them
# (string table at 0x5F37B0..0x5F380C, used by the unit loader).
RANKS = ["Rookie", "Green", "Trained", "Regular", "Veteran", "Expert", "Professional", "Guard", "Crack", "Elite",
         "Specialist"]


def unit_row(s: Session, unit) -> dict | None:
    if not unit:
        return None
    try:
        import records as editor_records
        rows = editor_records._records(s).tables.get("UNITS.TXT") or []
    except Exception:   # noqa: BLE001  (no game folder)
        return None
    row = next((r for r in rows if r and r[0].upper() == str(unit).upper()), None)
    if row is None:
        return None
    col = lambda i: row[i] if i < len(row) and row[i] not in ("-1", "") else None  # noqa: E731
    return {"key": row[0], "kind": col(1), "stats": col(4), "experience": col(9), "role": col(11),
            "speech_bank": col(10), "table": "UNITS.TXT"}


@command("Put a record back exactly as it ships (all its fields; an ENVI's VAMI / ANMI lines too).", mutates=True,
         line="record line")
def revert_record(s: Session, line):
    L = _need(s)
    r = s.record(line)
    v = L.vanilla_of(r)
    if v is None:
        raise CommandError(f"line {line} has no vanilla counterpart (added in the editor, renamed or renumbered)")
    i = r.index
    s.env.lines[i:i + 1 + len(r.children)] = _lines(L.van, v)
    s.env._parse()
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return {**get_record(s, i + 1), "action": f"Revert {logic_name(s, i + 1)} to Vanilla"}


@command("Put one field of a record back to its vanilla value (vectors: pos / rot / scale).", mutates=True,
         line="record line", field="field name")
def revert_field(s: Session, line, field):
    L = _need(s)
    r = s.record(line)
    v = L.vanilla_of(r)
    if v is None:
        raise CommandError(f"line {line} has no vanilla counterpart")
    if set(r.fields) != set(v.fields):
        raise CommandError("the record's layout changed (a list grew or shrank) - revert the whole record")
    try:
        r[field] = v[field]
    except (KeyError, ValueError) as e:
        raise CommandError(str(e)) from None
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return get_record(s, line)


@command("Vanilla records that are no longer in the scene (deleted, renamed or renumbered), with where they stood.")
def vanilla_deleted(s: Session):
    L = _need(s)
    out = []
    for v in L.deleted():
        out.append({**_key_json(L.van_keys[v.index]), "vanilla_line": v.index + 1, **_brief(v)})
    return {"deleted": out}


@command("Put a deleted vanilla record back (at the end of its section, as it ships).", mutates=True,
         vanilla_line="its line in the vanilla scene (vanilla_deleted)")
def restore_record(s: Session, vanilla_line):
    L = _need(s)
    try:
        v = L.van.record_at(int(vanilla_line))
    except KeyError:
        raise CommandError(f"no vanilla record on line {vanilla_line}") from None
    k = L.van_keys.get(v.index)
    if k is None:
        raise CommandError("that vanilla line isn't a record the editor tracks")
    if k in L.cur_by_key:
        raise CommandError(f"it is still in the scene (line {L.cur_by_key[k] + 1})")
    if v.kind == "HLOI" and s.env.find("HLOI", script_id=v["script_id"]):
        raise CommandError(f"script id {v['script_id']} is used by another object now - renumber that one first")
    text = _lines(L.van, v)
    if v.kind in ("SWITCH", "EMITTER"):
        try:
            new = s.env.insert(v.kind, text[0])
        except ValueError as e:
            raise CommandError(str(e)) from None
        at = new.index
    else:
        end = s.env._SECTION_END.get(v.kind)
        same = [r for r in s.env.records if r.kind == v.kind]
        at = next((r.index for r in s.env.records if end and r.kind == end), None)
        if at is None:
            if not same:
                raise CommandError(f"the scene has no {v.kind} section to put it in")
            last = same[-1]
            at = last.index + 1 + len(last.children)
        s.env.lines[at:at] = text
        s.env._parse()
    if s.env.problems:
        raise CommandError("; ".join(s.env.problems[:3]))
    return {"line": at + 1, "action": f"Restore {logic_name(s, at + 1)}"}


@command("Put the scene's atmosphere back as it ships: time of day, fog, sky layers, the colour keys and the sun "
         "record.", mutates=True)
def reset_atmosphere(s: Session):
    L = _need(s)
    changed = 0
    for kind in ATMOS_KINDS:
        cur = [r for r in s.env.records if r.kind == kind]
        van = [r for r in L.van.records if r.kind == kind]
        if len(cur) != len(van):
            raise CommandError(f"{kind}: {len(cur)} lines now, {len(van)} as shipped - can't match them up")
        for c, v in zip(cur, van):
            if s.env.lines[c.index] != L.van.lines[v.index]:
                s.env.lines[c.index] = L.van.lines[v.index]
                changed += 1
    s.env._parse()
    sun = logic._sun(s.env)
    if sun is not None:
        v = Lineage(s).vanilla_of(sun)
        if v is not None and s.env.lines[sun.index] != L.van.lines[v.index]:
            s.env.lines[sun.index] = L.van.lines[v.index]
            s.env._parse()
            changed += 1
    return {"changed_lines": changed, "action": "Reset Atmosphere to Vanilla", **logic.get_atmosphere(s)}


def logic_name(s: Session, line) -> str:
    from commands import pretty_name
    try:
        return pretty_name(s.env.record_at(line))
    except KeyError:
        return "Record"


# ------------------------------------------------------------------------------------------- the script --
@command("The open mission's script next to the editor's template for it (what Save as mission writes for a new "
         "mission from this base, with the current objectives / rules / layers blocks): text, template, and the "
         "editor-written line ranges of each.")
def get_vanilla_script(s: Session):
    import commands as C
    if not s.mission:
        raise CommandError("save it as a mission first")
    path = C._script_path(s)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        raise CommandError(f"{path} is missing - save the mission again to generate it") from None
    template = C._mission_script(s, s.mission[1], s.level)
    return {"path": path, "text": text, "template": template, "base_dll": C._base_dll(s, s.level),
            "editor_ranges": editor_ranges(text), "template_editor_ranges": editor_ranges(template)}


def editor_ranges(text) -> list[list[int]]:
    """0-based [first, last] line ranges between "-- @editor begin" and its end marker (rewritten on every save)."""
    out, start = [], None
    for i, l in enumerate(text.split("\n")):
        t = l.strip()
        if t.startswith("-- @editor begin"):
            start = i
        elif start is not None and t.startswith("-- @editor end"):
            out.append([start, i])
            start = None
    return out


@command("Replace the open mission's script with the editor's template (your own code in it is lost; the "
         "objectives / rules / layers blocks are written from the mission as usual).")
def reset_script(s: Session):
    import commands as C
    if not s.mission:
        raise CommandError("save it as a mission first")
    return C.save_script(s, C._mission_script(s, s.mission[1], s.level))
