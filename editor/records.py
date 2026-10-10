"""Editor commands for the game's data records (the "object window"): weapons, soldier types and their stats / kits,
vehicles, items, scenery, doors - and the game's texts. Changes go into a mod folder in the forms DesertStormFix
loads (conflict_ds_tools/records.py): <TABLE>.replace.txt / <TABLE>.txt rows, .weapon files, <mod>.strings.

Records are addressed by table file name (e.g. "UNITS.TXT") and key (first column). The commands don't touch the open
level, so they work with or without one, and the same commands serve the web UI's Records tab and MCP clients.
"""
from __future__ import annotations

import os

from commands import CommandError, Session, command  # noqa: F401  (registers into the editor's command table)
from conflict_ds_tools import archive, records as rec

if getattr(rec, "_editor_loaded", False):   # the editor reloaded its commands: take library changes too
    import importlib
    importlib.reload(rec)
rec._editor_loaded = True


def _game(s: Session) -> str:
    game = s.game_dir or archive.find_game_dir()
    if not game or not os.path.isdir(game):
        raise CommandError("game folder not known - start the editor with --game <folder>")
    return game


def _records(s: Session) -> rec.Records:
    game = _game(s)
    cached = getattr(s, "_records", None)
    if cached is None or cached[0] != game or not isinstance(cached[1], rec.Records):
        s._records = (game, rec.Records(game))
    return s._records[1]


def _mod_dir(s: Session, mod: str) -> str:
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("mod: a folder name")
    return os.path.join(_game(s), "Mods", mod)


def _table(R: rec.Records, table: str) -> str:
    t = (table or "").upper()
    if not t.endswith(".TXT"):
        t += ".TXT"
    if t not in R.tables:
        raise CommandError(f"unknown table {table} - records_tables lists them")
    return t


def _effective(R: rec.Records, table: str, mod_dir: str | None):
    """{upper key: (rows, state)} with state game / changed / new / deleted, in game order then new records."""
    out = {}
    for r in R.tables[table]:
        out.setdefault(r[0].upper(), ([], "game"))[0].append(list(r))
    if not mod_dir:
        return out
    if table == "WEAPS.TXT":
        for wid, (base, cols, _f, spelled) in rec.weapon_changes(mod_dir).items():
            src = out.get(base.upper()) if base else None
            if wid in out and (not base or base.upper() == wid):
                row = list(out[wid][0][0])
                state = "changed" if base else "game"
            elif src:
                row, state = list(src[0][0]), "new"
                row[0] = spelled
            else:
                continue
            for i, v in cols.items():
                row += ["-1"] * (i + 1 - len(row))
                row[i] = v
            out[wid] = ([row], state)
        return out
    for key, rows in rec.mod_overrides(mod_dir, table).items():
        out[key] = (rows, "deleted" if not rows else ("changed" if key in out else "new"))
    for r in rec.mod_new_rows(mod_dir, table):
        k = r[0].upper()
        if k in out and out[k][1] == "new":
            out[k][0].append(r)
        else:
            out[k] = ([r], "new")
    return out


@command("The game's data tables the records window edits: file name, what one row is, rows, whether a key has "
         "several rows (kits).")
def records_tables(s: Session):
    R = _records(s)
    return [{"table": t, "what": rec.TABLES[t][0], "rows": len(R.tables[t]), "multi": rec.TABLES[t][1]}
            for t in rec.TABLES if t in R.tables]


@command("Records of a table (with a mod's changes when mod is given): key, a short summary, state game / changed / "
         "new / deleted.", table="table file, e.g. UNITS.TXT", mod="mod folder (optional)",
         search="text in any column (optional)", limit="max rows")
def records_list(s: Session, table, mod="", search="", limit=500):
    R = _records(s)
    t = _table(R, table)
    eff = _effective(R, t, _mod_dir(s, mod) if mod else None)
    cols = R.schema(t)
    shown = [c["index"] for c in cols if c["index"] and c["name"] and not c["name"].startswith("column")][:3]
    out = []
    for key, (rows, state) in eff.items():
        r0 = rows[0] if rows else []
        if search and not any(search.lower() in c.lower() for r in rows for c in r) and search.lower() not in key.lower():
            continue
        summary = ", ".join(r0[i] for i in shown if i < len(r0) and r0[i] not in ("", "-1"))
        if rec.TABLES[t][1]:
            summary = f"{len(rows)} rows: " + ", ".join(r[1] for r in rows[:6] if len(r) > 1)
        out.append({"key": r0[0] if r0 else key, "summary": summary, "state": state})
        if len(out) >= int(limit):
            break
    return out


@command("One record: its columns (name, type, link to another table), the game's rows, the rows with the mod's "
         "change, and the records that refer to it.", table="table file", key="record key", mod="mod folder (optional)")
def record_get(s: Session, table, key, mod=""):
    R = _records(s)
    t = _table(R, table)
    eff = _effective(R, t, _mod_dir(s, mod) if mod else None)
    rows, state = eff.get(key.upper(), ([], None))
    if state is None:
        raise CommandError(f"{key} is not in {t}")
    return {"table": t, "key": key, "state": state, "multi": rec.TABLES[t][1], "columns": R.schema(t),
            "game_rows": R.rows(t, key), "rows": rows,
            "used_by": [{"table": a, "key": b, "column": c} for a, b, c in R.references(t, key)][:200]}


def _check(R: rec.Records, t: str, rows: list[list[str]]):
    width = len(R.schema(t))
    for r in rows:
        if not r or not str(r[0]).strip():
            raise CommandError("every row needs its key in the first column")
        if any("," in str(c) or "\n" in str(c) for c in r):
            raise CommandError("values can't contain commas or line breaks")
        if len(r) > width + 4:
            raise CommandError(f"{t} has {width} columns")


@command("Change a record in a mod. rows = the record's full rows (lists of values, key first; several for kit "
         "tables). Weapons are written as a .weapon file (only the columns that differ from the game).",
         table="table file", key="record key", rows="[[value, ...], ...]", mod="mod folder")
def record_set(s: Session, table, key, rows, mod):
    R = _records(s)
    t = _table(R, table)
    folder = _mod_dir(s, mod)
    rows = [[str(c).strip() for c in r] for r in rows]
    _check(R, t, rows)
    game = R.rows(t, key)
    if t == "WEAPS.TXT":
        if len(rows) != 1:
            raise CommandError("a weapon is one row")
        existing = rec.weapon_changes(folder).get(key.upper())
        base = existing[0] if existing and existing[0] else key
        ref = R.rows(t, base)[0] if R.rows(t, base) else []
        changes = {i: v for i, v in enumerate(rows[0]) if i and (i >= len(ref) or ref[i] != v)}
        path = rec.weapon_file(folder, rows[0][0] if not game else game[0][0], base if not game else game[0][0],
                               changes, R.schema(t))
        return {"written": os.path.relpath(path, _game(s)), "changed_columns": sorted(changes)}
    if game:
        rec.set_rows(folder, t, key, rows)
    else:   # a record this mod added
        rec.remove_new_rows(folder, t, key)
        rec.add_rows(folder, t, rows)
    return {"written": os.path.join("Mods", mod, t[:-4] + (".replace.txt" if game else ".txt"))}


@command("A new record in a mod, copied from an existing one (or empty columns), under a new key.",
         table="table file", key="new key", copy_from="key to copy (optional)", mod="mod folder")
def record_new(s: Session, table, key, mod, copy_from=""):
    R = _records(s)
    t = _table(R, table)
    folder = _mod_dir(s, mod)
    if R.rows(t, key):
        raise CommandError(f"{key} already exists in {t}")
    if t == "WEAPS.TXT":
        if not copy_from or not R.rows(t, copy_from):
            raise CommandError("a new weapon is based on a game weapon: copy_from = its id")
        path = rec.weapon_file(folder, key, R.rows(t, copy_from)[0][0], {}, R.schema(t))
        return {"written": os.path.relpath(path, _game(s))}
    src = R.rows(t, copy_from) if copy_from else [["-1"] * len(R.schema(t))]
    if copy_from and not src:
        raise CommandError(f"{copy_from} is not in {t}")
    rows = [[key] + list(r[1:]) for r in src]
    rec.add_rows(folder, t, rows)
    return {"written": os.path.join("Mods", mod, t[:-4] + ".txt"), "rows": rows}


@command("Take a mod's change to a record out again (the game's record comes back; a record the mod added is "
         "removed).", table="table file", key="record key", mod="mod folder")
def record_revert(s: Session, table, key, mod):
    R = _records(s)
    t = _table(R, table)
    folder = _mod_dir(s, mod)
    if t == "WEAPS.TXT":
        hit = rec.weapon_changes(folder).get(key.upper())
        if hit:
            os.remove(os.path.join(folder, hit[2]))
        return {"removed": bool(hit)}
    rec.set_rows(folder, t, key, None)
    rec.remove_new_rows(folder, t, key)
    return {"reverted": key}


@command("Delete a game record in a mod (careful: levels and scripts that use it break).", table="table file",
         key="record key", mod="mod folder")
def record_delete(s: Session, table, key, mod):
    R = _records(s)
    t = _table(R, table)
    if t == "WEAPS.TXT":
        raise CommandError("weapons can't be deleted (levels hand them out) - change them instead")
    if not R.rows(t, key):
        raise CommandError(f"{key} is not a game record - record_revert removes a record the mod added")
    rec.set_rows(_mod_dir(s, mod), t, key, [])
    return {"deleted": key, "used_by": len(R.references(t, key))}


@command("Game texts (catalog keys, English) with a mod's replacements and new texts; search in keys and texts.",
         mod="mod folder (optional)", search="text (optional)", limit="max results")
def strings_list(s: Session, mod="", search="", limit=300):
    R = _records(s)
    own = rec.read_strings(_mod_dir(s, mod)) if mod else {}
    entries = R.text_entries()                       # (source, KEY) -> game text
    shared = {k: v for (src, k), v in entries.items() if src == "catalog"}
    rows = []                                        # (section, key, source, game text)
    for (sec, k) in own:                             # the mod's own texts first
        src = sec or ("catalog" if k.upper() in shared else "mod")
        game = entries.get((sec, k.upper())) if sec else shared.get(k.upper())
        rows.append((sec, k, src, game))
    seen = {(sec, k.upper()) for sec, k in own}
    for (src, k), v in entries.items():
        sec = "" if src == "catalog" else src
        if (sec, k) not in seen:
            rows.append((sec, k, src, v))
    q = search.lower()
    out = []
    for sec, k, src, game in rows:
        mine = own.get((sec, k))
        if mine is None:
            mine = next((v for (s2, k2), v in own.items() if s2 == sec and k2.upper() == k.upper()), None)
        text = mine if mine is not None else (game or "")
        if q and q not in k.lower() and q not in text.lower() and q not in (game or "").lower() and q != src.lower():
            continue
        out.append({"key": k, "mission": sec, "source": src, "game": game, "text": text,
                    "state": "game" if mine is None else ("changed" if game is not None else "new"),
                    "hash": f"0x{rec.hash_key(k):08X}"})
        if len(out) >= int(limit):
            break
    return out


@command("Set a text in a mod: a game key replaces the game's text everywhere it is shown, a new key is a text "
         "mission scripts use by its hash (returned).", key="text key (letters, digits, _)", text="the text",
         mod="mod folder", mission="only in this mission (\"M01\", \"T02\", \"M05B\" = its text file "
         "M01Strs.loc ...); empty = every level. Mission texts reuse keys (MOV_TXT1, SPEECH1), so change those per mission")
def string_set(s: Session, key, text, mod, mission=""):
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,63}", key or ""):
        raise CommandError("key: letters, digits and _")
    if mission and not re.fullmatch(r"[A-Za-z0-9]{2,8}", mission):
        raise CommandError("mission: the text file's code, e.g. M01 or T02")
    rec.set_string(_mod_dir(s, mod), key, str(text), mission or "")
    return {"key": key, "mission": (mission or "").upper(), "hash": f"0x{rec.hash_key(key):08X}"}


@command("Remove a mod's text (a game key shows the game's text again).", key="text key", mod="mod folder",
         mission="the mission the text was set for (empty = every level)")
def string_revert(s: Session, key, mod, mission=""):
    rec.set_string(_mod_dir(s, mod), key, None, mission or "")
    return {"reverted": key}
