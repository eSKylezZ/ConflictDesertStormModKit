"""Base forms - the records layer's catalogue (PSE DESIGN §13.3): the game's data tables (Weaps.txt, Units.txt, ...)
as records with namespaced string IDs ("ds1:weapon/us_wpn_m16a2") and named fields, in PSE data files (pse_data).

    schema = forms.load_schema(path)            # PSE pse/records/ds1/schema.lua (committed: column names / kinds / links)
    docs = forms.generate(game_dir, schema)      # {type: pse_data.Document}: one file per record type
    files = forms.tables(docs, schema)           # {"WEAPS.TXT": bytes, ...}: the game's tables again, byte for byte
    forms.check(game_dir, schema)                # generate + tables + compare with the game's own files

Records are made from the player's own install and never shipped. Nothing here knows the game's numbers - the schema
only names columns and says how their text reads:

  kind "int"     an integer ("-10")              kind "fixed2"  a float with 2 decimals ("0.10" -> 0.1);
                                                  "fixed1" .. "fixed9" the same with 1 - 9 decimals
  kind "yesno"   YES / NO -> true / false          kind "text"    a string
  kind "ref"     a key of another record type -> its ID ("TankStat_M1A1" -> "ds1:vehicle_stats/tankstat_m1a1");
                 ref = "type" or { "type", "type2" } (the first type that has the key)
  none = "-1"    text / ref columns: that cell = the field is left out

A cell whose text doesn't come back the same from its kind (e.g. "1.5" in a fixed2 column) is kept as a plain string,
so a record never changes the game's data when written back. Tables with several rows per key (a soldier type's kit,
a vehicle's weapons ...) become one record per key with `rows = { {...}, ... }`.
"""
import os
import re

from . import archive, games, pse_data

_INT = re.compile(r"-?[0-9]+")
_FIXED = re.compile(r"-?[0-9]+\.[0-9]+")


def _lower(s):
    """ASCII lower case (IDs; the C++ generator does the same)."""
    return s.translate(_LOWER)


_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
_UPPER = {c + 32: c for c in range(ord("A"), ord("Z") + 1)}

ID_PREFIX = "ds1"


def load_schema(path):
    return pse_data.load(path).root


def _fixed_digits(kind):
    """kind "fixed<N>" (N = 1..9) -> N, else -1 (forms.cpp FixedDigits)."""
    return int(kind[5]) if isinstance(kind, str) and len(kind) == 6 and kind.startswith("fixed") and "1" <= kind[5] <= "9" else -1


def _fmt(v, kind):
    """A field's value as cell text. The column's own form when the value fits it, else by the value's type (a mod
    can put any literal anywhere): true / false -> YES / NO, numbers in pse_data's form, a table -> "" (same in C++)."""
    is_num = isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "int" and isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if _fixed_digits(kind) > 0 and is_num:
        return "%.*f" % (_fixed_digits(kind), v)
    if isinstance(v, bool):
        return "YES" if v else "NO"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return pse_data.format_float(v)
    if isinstance(v, str):
        return v
    return ""


def _parse(cell, kind):
    """Typed value of a cell, or the cell itself when the kind wouldn't write it back the same."""
    try:
        if kind == "int":
            if not _INT.fullmatch(cell):
                return cell
            v = int(cell)
            if not -(1 << 63) <= v < (1 << 63):
                return cell
        elif _fixed_digits(kind) > 0:
            if not _FIXED.fullmatch(cell):
                return cell
            v = float(cell)
        elif kind == "yesno":
            if cell not in ("YES", "NO"):
                return cell
            v = cell == "YES"
        else:
            return cell
    except ValueError:
        return cell
    return v if _fmt(v, kind) == cell else cell


def record_id(rtype, key):
    return "%s:%s/%s" % (ID_PREFIX, rtype, _lower(key))


def _split(raw):
    """(lines without their ends, line end, ends with a line end)."""
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    final = text.endswith(eol)
    body = text[:-len(eol)] if final else text
    return (body.split(eol) if body else []), eol, final


def _tables_of(schema):
    return [t for t in schema["tables"].items]


def _key_index(schema, game_files):
    """{record type: {lower key: original key}} for every keyed (one row per key) table - the targets of refs."""
    out = {}
    for t in _tables_of(schema):
        if t.get("rows_per_key"):
            continue
        raw = game_files.get(t["file"])
        if raw is None:
            continue
        lines, _, _ = _split(raw)
        keys = {}
        for line in lines:
            if line.strip():
                k = line.split(",")[0]
                keys.setdefault(_lower(k), k)
        out[t["type"]] = keys
    return out


def generate(game_dir, schema, game_files=None):
    """{record type: pse_data.Document} from the game's tables."""
    gf = game_files or archive.GameFiles(game_dir)
    keyidx = _key_index(schema, gf)
    docs = {}
    for t in _tables_of(schema):
        raw = gf.get(t["file"])
        if raw is None:
            continue
        cols = t["columns"].items
        lines, eol, final = _split(raw)
        width = max((len(l.split(",")) for l in lines), default=0)
        recs = pse_data.Table()
        table_info = pse_data.from_python({"file": t["file"], "eol": "crlf" if eol == "\r\n" else "lf",
                                           "final_eol": final, "width": width})
        per_key = bool(t.get("rows_per_key", False))
        grouped = True
        if per_key:
            # one record per key; when a key's rows aren't together in the file, every row keeps its line (_line)
            keys_in_order = [_lower(l.split(",")[0]) for l in lines]
            seen = set()
            for i, k in enumerate(keys_in_order):
                if k in seen and keys_in_order[i - 1] != k:
                    grouped = False
                seen.add(k)
            if not grouped:
                table_info.set("by_line", True)
        groups = {}
        ids_seen = set()
        for n, line in enumerate(lines):
            cells = line.split(",")
            if not line:
                row = pse_data.from_python({"_blank": True})
            else:
                row = pse_data.Table()
                for i, cell in enumerate(cells):
                    col = cols[i] if i < len(cols) else None
                    name = col["name"] if col else "col%d" % (i + 1)
                    kind = col.get("kind", "text") if col else "text"
                    if i == 0 and not per_key:
                        continue   # the key: the record's own "key" field
                    if col and col.get("none") is not None and cell == col["none"] and kind in ("text", "ref"):
                        continue
                    if kind == "ref":   # ref = a record type, or a list of them (tried in order)
                        targets = col["ref"].items if isinstance(col["ref"], pse_data.Table) else [col["ref"]]
                        value = next((record_id(tt, cell) for tt in targets
                                      if keyidx.get(tt, {}).get(_lower(cell)) == cell), cell)
                    else:
                        value = _parse(cell, kind)
                    row.set(name, value)
                if len(cells) != width:
                    row.set("_width", len(cells))
            key = cells[0] if line else ""
            if per_key:
                if not grouped:
                    row.set("_line", n)
                gk = _lower(key)
                if gk not in groups:
                    rec = pse_data.Table()
                    rec.set("id", record_id(t["type"], key))
                    rec.set("key", key)
                    if t.get("owner"):
                        rec.set("owner", record_id(t["owner"], key))
                    rec.set("rows", pse_data.Table())
                    groups[gk] = rec
                    recs.push(rec)
                groups[gk]["rows"].push(row)
            else:
                if line:
                    rid = record_id(t["type"], key)
                    if rid in ids_seen:   # the game's own data repeats a few keys (FEATS.TXT NU_PROP_Crate5): kept,
                        k = 2             # as "<id>#2" - the table's lookups find the first row, like the game
                        while "%s#%d" % (rid, k) in ids_seen:
                            k += 1
                        rid = "%s#%d" % (rid, k)
                        row.set("_duplicate", True)
                    ids_seen.add(rid)
                    row.set("id", rid)
                    row.set("key", key)
                recs.push(row)
        root = pse_data.Table()
        root.set("table", table_info)
        root.set("records", recs)
        docs[t["type"]] = pse_data.Document(root, [
            "%s %s records - made from your own game files by the records generator; not shipped, not for editing" %
            (ID_PREFIX, t["type"]), "(mods change records in their own files; this one is rebuilt from the game)"])
    return docs


def _cell(value, col, ids):
    kind = col.get("kind", "text") if col else "text"
    if isinstance(value, str):
        if kind == "ref" and value.startswith(ID_PREFIX + ":"):
            return ids.get(value, value)
        return value
    return _fmt(value, kind)


def _row_text(row, cols, width, ids, key=None):
    if row.get("_blank"):
        return ""
    n = row.get("_width", width)
    cells = []
    for i in range(n):
        col = cols[i] if i < len(cols) else None
        name = col["name"] if col else "col%d" % (i + 1)
        if i == 0 and key is not None:
            cells.append(key)
            continue
        v = row.get(name)
        if v is None:
            cells.append(col["none"] if col and col.get("none") is not None else "")
        else:
            cells.append(_cell(v, col, ids))
    return ",".join(cells)


def tables(docs, schema):
    """{table file: bytes} - the records written back as the game's tables."""
    ids = {}   # record id -> original key (refs back to the key text)
    for doc in docs.values():
        for r in doc.root["records"].items:
            if isinstance(r, pse_data.Table) and r.get("id") and r.get("key") is not None:
                ids.setdefault(r["id"], r["key"])
    out = {}
    for t in _tables_of(schema):
        doc = docs.get(t["type"])
        if not doc:
            continue
        info = doc.root["table"]
        cols = t["columns"].items
        width = info["width"]
        if t.get("rows_per_key"):
            # a row's first column = its record's key (a new record copied from another gets its own key there)
            first = cols[0]["name"]
            rows = []
            for rec in doc.root["records"].items:
                for row in rec["rows"].items:
                    own = row.get(first)
                    keyed = rec["key"] if not isinstance(own, str) or _lower(own) != _lower(rec["key"]) else None
                    rows.append((row, keyed))
            if info.get("by_line"):
                rows.sort(key=lambda rk: rk[0].get("_line", 1 << 30))
            lines = [_row_text(row, cols, width, ids, key=keyed) for row, keyed in rows]
        else:
            lines = [_row_text(rec, cols, width, ids, key=None if rec.get("_blank") else rec["key"])
                     for rec in doc.root["records"].items]
        eol = "\r\n" if info["eol"] == "crlf" else "\n"
        text = eol.join(lines) + (eol if info["final_eol"] else "")
        out[t["file"]] = text.encode("latin-1", errors="replace")   # (a mod's character beyond latin-1 -> "?")
    return out


def check(game_dir, schema):
    """[(table, problem)] - empty when every table comes back byte for byte."""
    gf = archive.GameFiles(game_dir)
    docs = generate(game_dir, schema, gf)
    # through the text format and back, as a saved file would
    docs = {k: pse_data.loads(pse_data.dumps(d)) for k, d in docs.items()}
    back = tables(docs, schema)
    problems = []
    for t in _tables_of(schema):
        raw = gf.get(t["file"])
        if raw is None:
            problems.append((t["file"], "not in the game's archives"))
        elif back.get(t["file"]) != raw:
            a, b = raw, back.get(t["file"], b"")
            at = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
            problems.append((t["file"], "differs at byte %d: game %r / records %r" % (at, a[max(0, at - 30):at + 30],
                                                                                         b[max(0, at - 30):at + 30])))
    return problems


# ---- drafting a schema from the data (once, by hand-reviewed script) ----

TYPES = {"WEAPS.TXT": "weapon", "INVEN.TXT": "item", "UNITS.TXT": "unit", "HSTAT.TXT": "unit_stats",
         "UNITWPNS.TXT": "unit_kit", "HSKILLS.TXT": "unit_skills", "EXMODS.TXT": "experience_level",
         "VEHICLE.TXT": "vehicle", "VSTAT.TXT": "vehicle_stats", "VEHWPNS.TXT": "vehicle_weapons",
         "PROPS.TXT": "propulsion", "FEATS.TXT": "feature", "FEATSTAT.TXT": "feature_stats", "STRUCTS.TXT": "structure",
         "DOORS.TXT": "door", "SWITCHES.TXT": "switch", "TARGETS.TXT": "target", "CONNOBJS.TXT": "role_gear",
         "MPSKINITEMS.TXT": "mp_kit"}
OWNERS = {"UNITWPNS.TXT": "unit", "HSKILLS.TXT": None, "VEHWPNS.TXT": "vehicle", "CONNOBJS.TXT": None,
          "MPSKINITEMS.TXT": None}


def _snake(name):
    out = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out or "col"


def draft_schema(game_dir):
    """A first schema from the data + records.NAMES (kinds by what every cell reads as; links by key matches)."""
    import re
    from . import records
    rec = records.Records(game_dir)
    gf = archive.GameFiles(game_dir)
    tables_ = []
    for file, rtype in TYPES.items():
        raw = gf.get(file)
        if raw is None:
            continue
        lines, _, _ = _split(raw)
        rows = [l.split(",") for l in lines if l]
        width = max(len(r) for r in rows)
        inferred = rec.schema(file)
        cols = []
        used = set()
        for i in range(width):
            vals = [r[i] for r in rows if i < len(r)]
            real = [v for v in vals if v not in ("", "-1")]
            name = records.NAMES.get(file, {}).get(i)
            name = _snake(name) if name else ("key" if i == 0 else "col%d" % (i + 1))
            if name in used:
                name = "%s_%d" % (name, i + 1)
            used.add(name)
            if real and all(re.fullmatch(r"-?\d+", v) for v in vals if v != ""):
                kind = "int"
            elif real and all(re.fullmatch(r"-?\d+\.\d\d", v) or v == "-1" for v in vals):
                kind = "fixed2"
            elif real and all(v in ("YES", "NO") for v in vals):
                kind = "yesno"
            else:
                kind = "text"
            col = {"name": name, "kind": kind}
            link = inferred[i]["link"] if i < len(inferred) else None
            if kind == "text" and link and link in TYPES and i > 0:
                col["kind"] = "ref"
                col["ref"] = TYPES[link]
            if col["kind"] in ("text", "ref") and "-1" in vals:
                col["none"] = "-1"
            cols.append(col)
        t = {"file": file, "type": rtype, "columns": cols}
        if records.TABLES.get(file, ("", False))[1]:
            t["rows_per_key"] = True
            if OWNERS.get(file):
                t["owner"] = OWNERS[file]
        tables_.append(t)
    return {"game": ID_PREFIX, "version": 1, "tables": tables_}


# ---- mods: record edits merged field by field (PSE DESIGN §13.3) ----
#
# A mod's Mods\<Mod>\records\*.lua files (PSE data files) hold a list of edits:
#   { id = "ds1:weapon/us_wpn_m16a2", damage = 20 }            change fields (others stay as they are)
#   { id = "ds1:weapon/my_rifle", based_on = "ds1:weapon/us_wpn_m16a2", key = "MY_RIFLE", damage = 30 }
#                                                              a new record: a copy of another + changes
#   { id = "ds1:weapon/old_gun", delete = true }               remove a record
#   { id = "ds1:unit/x", clear = { "speech_bank" } }           fields back to "none" (left out)
# Mods apply in load order; each field takes the last mod's value. Two mods giving one field different values is a
# conflict: the later wins and it is reported. A list field (a kit's rows) is one field.

EDIT_KEYS = ("id", "based_on", "delete", "clear")


def load_mod_edits(mods_dir):
    """[(mod name, file, [edit Table])] for the switched-on mods (folders not starting with _ or .), in load order
    (folder name order, as DesertStormFix's mod index)."""
    out = []
    if not os.path.isdir(mods_dir):
        return out
    for mod in sorted(os.listdir(mods_dir), key=str.lower):
        folder = os.path.join(mods_dir, mod, "records")
        if mod.startswith(("_", ".")) or not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder), key=str.lower):
            if f.lower().endswith(".lua"):
                doc = pse_data.load(os.path.join(folder, f))
                out.append((mod, f, [e for e in doc.root.items if isinstance(e, pse_data.Table)]))
    return out


def _type_of(rid):
    return rid.split(":", 1)[1].split("/", 1)[0] if ":" in rid and "/" in rid else None


def _copy(v):
    return pse_data.loads(pse_data.dumps(pse_data.Document(v))).root if isinstance(v, pse_data.Table) else v


def _column(schema, rtype, field):
    if schema is None:
        return None
    for t in _tables_of(schema):
        if t["type"] == rtype:
            return next((c for c in t["columns"].items if c["name"] == field), None)
    return None


def _spec(schema, rtype):
    if schema is None:
        return None
    return next((t for t in _tables_of(schema) if t["type"] == rtype), None)


def _field_column(spec, field):
    """The column a field writes (the record's "key" is column 1); None when the schema doesn't name it."""
    if spec is None:
        return None
    cols = spec["columns"].items
    if field == "key":
        return cols[0] if cols else None
    return next((c for c in cols if c["name"] == field), None)


def _too_long(col, value):
    """Length of a text value longer than the column's max_len (a ref given as a record id is that record's key:
    checked there), else None."""
    m = col.get("max_len") if col is not None else None
    if not isinstance(m, int) or isinstance(m, bool) or m <= 0 or not isinstance(value, str):
        return None
    if col.get("kind", "text") == "ref" and value.startswith(ID_PREFIX + ":"):
        return None
    return len(value) if len(value) > m else None


def merge(docs, edits, schema=None):
    """Applies the mods' edits to the base records (in place on copies). Returns (docs, report) - report lines:
    "conflict: <id> <field>: <mod a> <a> / <mod b> <b> (<mod b> wins)", "[fail] ...", "new <id>", "deleted <id>"."""
    docs = {k: pse_data.loads(pse_data.dumps(d)) for k, d in docs.items()}
    index = {}
    for rtype, doc in docs.items():
        for r in doc.root["records"].items:
            if isinstance(r, pse_data.Table) and r.get("id"):
                index[r["id"]] = (rtype, r)
    setby = {}   # (id, field) -> (mod, value text)
    report = []
    for mod, file, entries in edits:
        for e in entries:
            rid = e.get("id")
            if not isinstance(rid, str) or _type_of(rid) is None:
                report.append("[fail] %s/%s: an edit without a valid id (ds1:<type>/<name>)" % (mod, file))
                continue
            rtype = _type_of(rid)
            if rid.startswith(ID_PREFIX + ":") and rid.count("/") == 2:
                continue   # a placed reference (ds1:<level>/<kind>/<editor id>): placed.apply_edits
            if rtype not in docs:
                report.append("[fail] %s/%s: %s - no record type %s" % (mod, file, rid, rtype))
                continue
            if e.get("delete") is True:
                if rid in index:
                    recs = docs[rtype].root["records"]
                    recs.entries = [x for x in recs.entries if x.value is not index[rid][1]]
                    del index[rid]
                    report.append("deleted %s (%s)" % (rid, mod))
                continue
            spec = _spec(schema, rtype)
            rec = index.get(rid, (None, None))[1]
            created = False
            new_line = None
            if rec is None:
                base = e.get("based_on")
                if not isinstance(base, str) or base not in index:
                    report.append("[fail] %s/%s: %s isn't a game record - a new one needs based_on = <a record id>"
                                  % (mod, file, rid))
                    continue
                max_rows = spec.get("max_rows") if spec is not None else None
                if isinstance(max_rows, int) and not isinstance(max_rows, bool) and max_rows > 0:
                    rows = sum(1 for r in docs[rtype].root["records"].items
                               if isinstance(r, pse_data.Table) and not r.get("_blank"))
                    if rows >= max_rows:
                        report.append("[fail] %s/%s: %s not added - %s holds at most %d records"
                                      % (mod, file, rid, spec["file"], max_rows))
                        continue
                rec = _copy(index[base][1])
                rec.set("id", rid)
                for f in ("_duplicate", "_line"):
                    rec.remove(f)
                if e.get("key") is None:
                    rec.set("key", rid.split("/", 1)[1].translate(_UPPER))
                docs[rtype].root["records"].push(rec)
                index[rid] = (rtype, rec)
                created = True
                new_line = "new %s based on %s (%s)" % (rid, base, mod)
            first_line = len(report)   # the "new" line goes before this entry's other lines
            key_len = None             # a new record's key that doesn't fit
            clear = e.get("clear")
            for f in (clear.items if isinstance(clear, pse_data.Table) else []):
                col = _column(schema, rtype, f)
                if schema is not None and (col is None or col.get("none") is None):
                    report.append("[fail] %s/%s: %s %s can't be cleared - the game has no \"none\" for that field"
                                  % (mod, file, rid, f))
                    continue
                rec.remove(f)
                setby[(rid, f)] = (mod, "none")
            for ent in e.entries:
                if ent.key is None or ent.key in EDIT_KEYS:
                    continue
                col = _field_column(spec, ent.key) if isinstance(ent.key, str) else None
                n = _too_long(col, ent.value)
                if n is not None:
                    if created and ent.key == "key":   # reported once below: the record isn't added
                        key_len = n
                        continue
                    report.append("[fail] %s/%s: %s %s is %d characters - the game reads at most %d; not changed"
                                  % (mod, file, rid, ent.key, n, col["max_len"]))
                    continue
                text = pse_data.dumps(pse_data.Document(pse_data.from_python({"v": ent.value})))
                prev = setby.get((rid, ent.key))
                if prev and prev[0] != mod and prev[1] != text:
                    report.append("conflict: %s %s: %s / %s (%s wins)" % (rid, ent.key, prev[0], mod, mod))
                setby[(rid, ent.key)] = (mod, text)
                rec.set(ent.key, _copy(ent.value))
            key_col = _field_column(spec, "key")
            n = key_len if key_len is not None else (_too_long(key_col, rec.get("key")) if created else None)
            if created and n is not None:
                del report[first_line:]
                recs = docs[rtype].root["records"]
                recs.entries = [x for x in recs.entries if x.value is not rec]
                del index[rid]
                report.append("[fail] %s/%s: %s not added - its key is %d characters, the game reads at most %d"
                              % (mod, file, rid, n, key_col["max_len"]))
            elif created:
                report.insert(first_line, new_line)
    return docs, report


def merged_tables(game_dir, schema, mods_dir=None):
    """The game's tables with every switched-on mod's record edits: ({file: bytes}, report)."""
    docs = generate(game_dir, schema)
    edits = load_mod_edits(mods_dir or os.path.join(game_dir, "Mods"))
    docs, report = merge(docs, edits, schema)
    return tables(docs, schema), report
