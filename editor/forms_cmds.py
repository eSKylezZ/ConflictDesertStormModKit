"""Editor commands for records (PSE docs/sdk/data-files.md): the game's data as records with ds1: IDs, edited field by
field. A mod's changes go into Mods\\<mod>\\records\\editor.lua - only the fields that differ from the game (plus new /
deleted records) - which DesertStormFix merges at start-up with every other mod's record edits (later mods win per
field, conflicts are reported).

Records come from the player's own install (conflict_ds_tools/forms.py with the schema PSE\\Records\\ds1\\schema.lua),
so nothing here ships game data. The older row commands (records_*, editor/records.py) keep working next to these.
"""
from __future__ import annotations

import os

from commands import CommandError, Session, command  # noqa: F401  (registers into the editor's command table)
from conflict_ds_tools import archive, forms, pse_data

EDIT_FILE = "editor.lua"


def _game(s: Session) -> str:
    game = s.game_dir or archive.find_game_dir()
    if not game or not os.path.isdir(game):
        raise CommandError("game folder not known - start the editor with --game <folder>")
    return game


def _schema(s: Session):
    game = _game(s)
    path = os.path.join(game, "PSE", "Records", "ds1", "schema.lua")
    if not os.path.exists(path):
        raise CommandError("no records schema at %s - install DesertStormFix 3.3 or newer" % path)
    return forms.load_schema(path)


def _base(s: Session):
    """(schema, base docs) - cached per game folder."""
    game = _game(s)
    cached = getattr(s, "_forms", None)
    if cached is None or cached[0] != game:
        schema = _schema(s)
        s._forms = (game, schema, forms.generate(game, schema))
    return s._forms[1], s._forms[2]


def _mod_dir(s: Session, mod: str) -> str:
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("mod: a folder name")
    return os.path.join(_game(s), "Mods", mod)


def _edit_path(s, mod):
    return os.path.join(_mod_dir(s, mod), "records", EDIT_FILE)


def _load_edits(s, mod):
    p = _edit_path(s, mod)
    if not os.path.exists(p):
        return pse_data.Document(pse_data.Table(), ["Record edits made with the editor (PSE docs/sdk/data-files.md)."])
    return pse_data.load(p)


def _save_edits(s, mod, doc):
    p = _edit_path(s, mod)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    pse_data.save(p, doc)
    try:
        return os.path.relpath(p, _game(s))
    except ValueError:   # another drive
        return p


def _index(docs):
    out = {}
    for rtype, doc in docs.items():
        for r in doc.root["records"].items:
            if isinstance(r, pse_data.Table) and r.get("id"):
                out[r["id"]] = (rtype, r)
    return out


def _merged(s, mod=None):
    """(docs merged with the switched-on mods' edits (+ `mod`'s even when it's switched off), report)."""
    schema, base = _base(s)
    mods_dir = os.path.join(_game(s), "Mods")
    edits = forms.load_mod_edits(mods_dir)
    if mod and mod.startswith(("_", ".")):
        p = _edit_path(s, mod)
        if os.path.exists(p):
            edits.append((mod, EDIT_FILE, [e for e in pse_data.load(p).root.items if isinstance(e, pse_data.Table)]))
    return forms.merge(base, edits, schema)


def _plain(v):
    return v.to_python() if isinstance(v, pse_data.Table) else v


def _summary(r):
    keep = ("key", "model", "type", "damage", "kind", "role", "stats")
    return {k: _plain(r.get(k)) for k in keep if r.get(k) is not None}


@command("Record types (weapon, unit, vehicle ...) with how many records each has.")
def forms_types(s: Session):
    _schema_, base = _base(s)
    return [{"type": t, "records": len([r for r in d.root["records"].items if isinstance(r, pse_data.Table) and r.get("id")]),
             "table": d.root["table"]["file"]} for t, d in base.items()]


@command("The fields of a record type, from the schema: name, kind (int / fixed<N> / yesno / text / ref), note, the "
         "record types a ref links to, the game's 'nothing' value, most characters. Records of a table with several "
         "rows per key (kits, vehicle weapons ...) have one field `rows`: its columns are `row_columns`.",
         type="record type (forms_types)")
def form_fields(s: Session, type):
    schema, _base_ = _base(s)
    for t in schema["tables"].items:
        if t["type"] != type:
            continue
        cols = []
        for c in t["columns"].items:
            ref = c.get("ref")
            cols.append({"name": c["name"], "kind": c.get("kind", "text"), "note": c.get("note", ""),
                         "ref": ([ref] if isinstance(ref, str) else ref.items) if ref is not None else [],
                         "none": c.get("none"), "max_len": c.get("max_len")})
        rows = bool(t.get("rows_per_key"))
        return {"type": type, "file": t["file"], "rows_per_key": rows, "owner": t.get("owner"),
                "fields": [] if rows else cols[1:], "row_columns": cols if rows else [], "key_column": cols[0]["name"]}
    raise CommandError("unknown record type %s - forms_types lists them" % type)


@command("Records of a type (with every switched-on mod's edits applied): id, summary, state game / changed / new.",
         type="record type (forms_types)", search="text in the id or key (optional)", limit="most rows (500)")
def forms_list(s: Session, type, search="", limit=500):
    _schema_, base = _base(s)
    if type not in base:
        raise CommandError("unknown record type %s - forms_types lists them" % type)
    merged, _report = _merged(s)
    base_idx = _index(base)
    out = []
    for r in merged[type].root["records"].items:
        if not isinstance(r, pse_data.Table) or not r.get("id"):
            continue
        if search and search.lower() not in (r["id"] + " " + str(r.get("key", ""))).lower():
            continue
        b = base_idx.get(r["id"])
        state = "new" if not b else ("game" if b[1] == r else "changed")
        out.append({"id": r["id"], "state": state, **_summary(r)})
        if len(out) >= int(limit):
            break
    return out


@command("One record: the game's fields, the merged fields (every switched-on mod), and this mod's edit of it.",
         id="record id (ds1:<type>/<name>)", mod="mod folder (optional)")
def form_get(s: Session, id, mod=""):
    _schema_, base = _base(s)
    merged, report = _merged(s, mod)
    g = _index(base).get(id)
    m = _index(merged).get(id)
    if not g and not m:
        raise CommandError("no record %s" % id)
    mine = None
    if mod:
        for e in _load_edits(s, mod).root.items:
            if isinstance(e, pse_data.Table) and e.get("id") == id:
                mine = e.to_python()
    return {"id": id, "game": g[1].to_python() if g else None, "merged": m[1].to_python() if m else None,
            "mod_edit": mine, "conflicts": [l for l in report if l.startswith("conflict: %s " % id)]}


def _edit_entry(doc, id):
    for e in doc.root.items:
        if isinstance(e, pse_data.Table) and e.get("id") == id:
            return e
    e = pse_data.Table()
    e.set("id", id)
    doc.root.push(e)
    return e


@command("Change fields of a record in a mod: only fields that differ from the game are kept in the mod's edit. "
         "fields = {name: value}; a value of null takes the mod's change of that field out.",
         id="record id", fields="{field: value}", mod="mod folder")
def form_set(s: Session, id, fields, mod):
    _schema_, base = _base(s)
    g = _index(base).get(id)
    doc = _load_edits(s, mod)
    e = _edit_entry(doc, id)
    if not g and e.get("based_on") is None:
        raise CommandError("%s isn't a game record - form_new makes a new one" % id)
    for name, value in (fields or {}).items():
        if name in forms.EDIT_KEYS:
            raise CommandError("%s can't be set here" % name)
        if value is None or (g and pse_data.from_python(value) == g[1].get(name)):
            e.remove(name)
        else:
            e.set(name, pse_data.from_python(value))
    if len([x for x in e.entries if x.key not in ("id",)]) == 0:
        doc.root.entries = [x for x in doc.root.entries if x.value is not e]
    return {"written": _save_edits(s, mod, doc), "edit": e.to_python()}


@command("A new record in a mod, copied from an existing one; fields = changes on top of the copy.",
         id="new record id (ds1:<type>/<name>)", based_on="record id to copy", mod="mod folder",
         key="name the game's tables use (default: the id's name in upper case)", fields="{field: value} (optional)")
def form_new(s: Session, id, based_on, mod, key="", fields=None):
    _schema_, base = _base(s)
    idx = _index(base)
    if id in idx:
        raise CommandError("%s is a game record - form_set changes it" % id)
    if based_on not in idx:
        raise CommandError("no record %s to copy" % based_on)
    if forms._type_of(id) != forms._type_of(based_on):
        raise CommandError("a new %s can't be based on a %s" % (forms._type_of(id), forms._type_of(based_on)))
    doc = _load_edits(s, mod)
    e = _edit_entry(doc, id)
    e.set("based_on", based_on)
    if key:
        e.set("key", key)
    for name, value in (fields or {}).items():
        e.set(name, pse_data.from_python(value))
    return {"written": _save_edits(s, mod, doc), "edit": e.to_python()}


@command("Delete a game record in a mod (careful: levels and scripts that use it break).", id="record id",
         mod="mod folder")
def form_delete(s: Session, id, mod):
    doc = _load_edits(s, mod)
    e = _edit_entry(doc, id)
    for x in list(e.entries):
        if x.key not in ("id",):
            e.remove(x.key)
    e.set("delete", True)
    return {"written": _save_edits(s, mod, doc)}


@command("Take a mod's edit of a record out again (the game's record comes back; a record the mod added is gone).",
         id="record id", mod="mod folder")
def form_revert(s: Session, id, mod):
    doc = _load_edits(s, mod)
    before = len(doc.root.entries)
    doc.root.entries = [x for x in doc.root.entries if not (isinstance(x.value, pse_data.Table) and x.value.get("id") == id)]
    return {"written": _save_edits(s, mod, doc), "removed": before - len(doc.root.entries)}


@command("What the switched-on mods' record edits do together: new / deleted records, conflicts, problems.")
def forms_report(s: Session):
    _merged_, report = _merged(s)
    return report
