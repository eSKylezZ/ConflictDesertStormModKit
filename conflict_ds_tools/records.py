"""Conflict: Desert Storm game data records - the editor's "object window": weapons, soldiers, vehicles, items, stats.

The game's data tables are comma-separated text files in catalog.dat (Weaps.txt, Units.txt, HStat.txt, ...), one
record per line, the first column its key. This module reads them (from the player's own install or an extracted
catalog folder), describes their columns (type from the data, a name where it is known, links to other tables found
from the values), and writes a mod's changes in the forms DesertStormFix loads:

  <mod>\\<TABLE>.replace.txt   rows replacing the game's rows with the same key ("-KEY" deletes the key); for tables
                               with several rows per key (UnitWpns: one per weapon) all of them are replaced together
  <mod>\\<TABLE>.txt           new rows appended to the table (new records)
  <mod>\\<id>.weapon           weapons: the readable weapon file; a game weapon is changed in place with
                               "based on = <its own id>", a new one is "id = <new id>" + "based on = <a weapon>"
  <mod>\\<mod>.strings          texts: "KEY = text" - a game key (objective, briefing, menu text) replaces the game's
                               text, a new key is found by hash_key(key) (what mission scripts pass)

Pure Python, no dependencies.
"""
from __future__ import annotations

import os
import re
import struct

from . import archive

# Tables the editor shows: file name, what one row is, whether a key can have several rows.
TABLES = {
    "WEAPS.TXT": ("Weapon", False),
    "INVEN.TXT": ("Item (radio, medikit, armour, ammo)", False),
    "UNITS.TXT": ("Soldier type", False),
    "HSTAT.TXT": ("Soldier stats", False),
    "UNITWPNS.TXT": ("Soldier type's kit (one row per item)", True),
    "HSKILLS.TXT": ("Squad member's skills (one row per skill)", True),
    "EXMODS.TXT": ("Experience level", False),
    "VEHICLE.TXT": ("Vehicle", False),
    "VSTAT.TXT": ("Vehicle stats", False),
    "VEHWPNS.TXT": ("Vehicle's weapons (one row per weapon)", True),
    "PROPS.TXT": ("Vehicle propulsion", False),
    "FEATS.TXT": ("Destructible scenery", False),
    "FEATSTAT.TXT": ("Scenery stats", False),
    "STRUCTS.TXT": ("Structure", False),
    "DOORS.TXT": ("Door / gate", False),
    "SWITCHES.TXT": ("Switch / keypad", False),
    "TARGETS.TXT": ("Training target", False),
    "CONNOBJS.TXT": ("Equipment worn by role (one row per item)", True),
    "MPSKINITEMS.TXT": ("Multiplayer faction kit (one row per item)", True),
}

# Known column names (0-based). Weaps.txt follows the .weapon file names (DesertStormFix mods.cpp kFields).
NAMES = {
    "WEAPS.TXT": {0: "id", 1: "model", 4: "hud icon", 5: "type", 6: "hand connector", 10: "time between shots",
                  14: "magazines", 15: "magazine size", 16: "reload time", 22: "damage", 23: "range",
                  35: "muzzle flash", 37: "spent cartridge", 53: "aim animation", 54: "reload animation",
                  55: "throw animation", 56: "prone aim animation", 57: "prone reload animation",
                  58: "prone throw animation", 63: "sound bank", 64: "hearing range"},
    "INVEN.TXT": {0: "id", 1: "type", 2: "model", 5: "hud icon", 6: "hand connector", 7: "ammo for weapon",
                  12: "sound bank"},
    "UNITS.TXT": {0: "id", 1: "kind", 2: "model", 4: "stats", 9: "experience", 10: "speech bank", 11: "role",
                  12: "skin key"},
    "HSTAT.TXT": {0: "id"},
    "UNITWPNS.TXT": {0: "soldier type", 1: "item", 2: "kind", 3: "amount"},
    "HSKILLS.TXT": {0: "soldier", 1: "skill", 2: "level"},
    "EXMODS.TXT": {0: "level"},
    "VEHICLE.TXT": {0: "id", 1: "model", 2: "wreck model", 4: "stats", 22: "wreck debris"},
    "VSTAT.TXT": {0: "id", 12: "propulsion"},
    "VEHWPNS.TXT": {0: "vehicle", 1: "weapon", 2: "kind", 3: "ammo"},
    "PROPS.TXT": {0: "id", 1: "name", 2: "sound bank"},
    "FEATS.TXT": {0: "id", 1: "model", 4: "stats", 5: "kind"},
    "FEATSTAT.TXT": {0: "id"},
    "STRUCTS.TXT": {0: "id", 1: "model", 2: "damaged model", 4: "stats", 5: "wreck debris"},
    "DOORS.TXT": {0: "id", 1: "model", 4: "kind"},
    "SWITCHES.TXT": {0: "id", 1: "model off", 2: "model on"},
    "TARGETS.TXT": {0: "id", 1: "model"},
    "CONNOBJS.TXT": {0: "role", 1: "connector", 2: "model", 3: "faction"},
    "MPSKINITEMS.TXT": {0: "skin", 1: "item", 2: "kind", 3: "amount"},
}

_INT = re.compile(r"-?\d+$")
_FLOAT = re.compile(r"-?\d+\.\d*$")


def hash_key(key: str) -> int:
    """The catalog's hash of a text key (archive LFSR over the lower-cased key) - how the game and mod .strings
    files find a text; mission scripts pass this number (e.g. to add_objective)."""
    x = 1
    for c in key.lower().encode("latin-1", "replace"):
        for b in range(8):
            i = ((c >> b) & 1) ^ (x & 1) ^ ((x >> 1) & 1) ^ ((x >> 21) & 1) ^ ((x >> 31) & 1)
            x = ((x << 1) & 0xFFFFFFFF) | i
    return x


def split_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.replace("\r", "").split("\n"):
        if not line.strip() or line.lstrip().startswith(("#", "//")):
            continue
        cells = [c.strip() for c in line.split(",")]
        while len(cells) > 1 and cells[-1] == "":
            cells.pop()
        rows.append(cells)
    return rows


def join_row(cells: list[str]) -> str:
    return ",".join(str(c) for c in cells)


class Records:
    """The game's data tables. source = the game folder (reads catalog.dat) or an extracted folder holding the
    .TXT files (e.g. <extracted>/catalog)."""

    def __init__(self, source: str):
        self.tables: dict[str, list[list[str]]] = {}
        self._texts: dict[tuple[str, str], str] | None = None
        self._catalog = None
        files = None
        if os.path.isfile(os.path.join(source, "catalog.dat")):
            files = archive.GameFiles(source)
            self._catalog = os.path.join(source, "catalog.dat")
        for name in TABLES:
            data = None
            if files:
                data = files.get(name)
            else:
                for cand in (name, name.lower(), name.title()):
                    p = os.path.join(source, cand)
                    if os.path.isfile(p):
                        data = open(p, "rb").read()
                        break
            if data is not None:
                self.tables[name] = split_rows(data.decode("latin-1"))
        self._schemas: dict[str, list[dict]] = {}

    # ---- description ----
    def keys(self, table: str) -> set[str]:
        return {r[0].upper() for r in self.tables.get(table, []) if r}

    def schema(self, table: str) -> list[dict]:
        """Columns: index, name, type (int / float / text), link (table the values are keys of, if any)."""
        if table in self._schemas:
            return self._schemas[table]
        rows = self.tables.get(table, [])
        width = max((len(r) for r in rows), default=0)
        names = NAMES.get(table, {})
        # link targets: tables with one row per key (a kit table's key is not a record of its own)
        other_keys = {t: self.keys(t) for t in self.tables if t != table and not TABLES.get(t, ("", False))[1]}
        cols = []
        for i in range(width):
            vals = [r[i] for r in rows if i < len(r) and r[i] not in ("", "-1")]
            if vals and all(_INT.match(v) for v in vals):
                typ = "int"
            elif vals and all(_INT.match(v) or _FLOAT.match(v) for v in vals):
                typ = "float"
            else:
                typ = "text"
            link = None
            if (i > 0 or TABLES.get(table, ("", False))[1]) and typ == "text" and vals:
                best = 0.0
                for t, ks in other_keys.items():
                    hit = sum(1 for v in vals if v.upper() in ks) / len(vals)
                    if hit > best and hit >= 0.6:
                        best, link = hit, t
            cols.append({"index": i, "name": names.get(i, "key" if i == 0 else f"column {i + 1}"),
                         "type": typ, "link": link})
        self._schemas[table] = cols
        return cols

    def rows(self, table: str, key: str | None = None) -> list[list[str]]:
        rows = self.tables.get(table, [])
        if key is None:
            return rows
        return [r for r in rows if r and r[0].upper() == key.upper()]

    def references(self, table: str, key: str) -> list[tuple[str, str, str]]:
        """(table, row key, column name) of every row in another table that links to this record."""
        out = []
        for t, rows in self.tables.items():
            for col in self.schema(t):
                if col["link"] != table:
                    continue
                for r in rows:
                    if col["index"] < len(r) and r[col["index"]].upper() == key.upper():
                        out.append((t, r[0], col["name"]))
        return out

    # ---- texts ----
    # mission text files (each in its level's archive): training, missions and their second parts
    LEVEL_TEXTS = ["T01Strs.loc", "T02Strs.loc", "T03Strs.loc"] + [f"M{n:02d}Strs.loc" for n in range(1, 13)] + \
                  ["M05BStrs.loc", "M11BStrs.loc", "M12BStrs.loc"]

    @staticmethod
    def _scan_texts(data: bytes, out: dict, source: str):
        """{u32 hash, u32 language, KEY, text} entries in UTF-16 (catalog.dat and the levels' .loc files): the first
        (English) text of each key -> out[(source, KEY)]."""
        pat = re.compile(rb"((?:[A-Z0-9_][\x00]){2,64})\x00\x00((?:[^\x00][\x00]){0,1200})\x00\x00")
        for m in pat.finditer(data):
            k = m.group(1).decode("utf-16le")
            if re.fullmatch(r"[A-Z][A-Z0-9_]+", k) and (source, k) not in out:
                out[(source, k)] = m.group(2).decode("utf-16le", "replace")

    def text_entries(self) -> dict[tuple[str, str], str]:
        """(source, KEY) -> first / English text; source "catalog" (shared by every level) or a mission's text file
        ("M01" = M01Strs.loc). Missions reuse keys (MOV_TXT1 date, SPEECH1 ...) with their own texts."""
        if self._texts is None:
            self._texts = {}
            if self._catalog:
                self._scan_texts(open(self._catalog, "rb").read(), self._texts, "catalog")
                files = archive.GameFiles(os.path.dirname(self._catalog))
                for name in self.LEVEL_TEXTS:
                    data = files.get(name)
                    if data:
                        self._scan_texts(data, self._texts, name[:-8].upper())
        return self._texts

    def text_for_hash(self, h: int, sources=("catalog",)) -> tuple[str, str] | None:
        """(KEY, English text) of a catalog hash (what mission scripts pass: add_objective, briefing texts), looked
        up in the given text sources in order - a mission's own file first ("M01"), as missions reuse keys."""
        if getattr(self, "_by_hash", None) is None:
            self._by_hash: dict[int, dict[str, tuple[str, str]]] = {}
            for (src, k), v in self.text_entries().items():
                self._by_hash.setdefault(hash_key(k), {}).setdefault(src, (k, v))
        found = self._by_hash.get(h & 0xFFFFFFFF, {})
        for src in sources:
            if src in found:
                return found[src]
        return None

    def level_text(self, source: str, key: str) -> str | None:
        """One key's English text from a mission's text file ("M01") or the catalog."""
        return self.text_entries().get((source, key.upper()))

    def texts(self) -> dict[str, str]:
        """Text keys -> the first (English) text (catalog first, then the first mission that has the key)."""
        out: dict[str, str] = {}
        for (_, k), v in self.text_entries().items():
            out.setdefault(k, v)
        return out


# ---- writing a mod's changes ----
def _read_lines(path: str) -> list[str]:
    if not os.path.isfile(path):
        return []
    return open(path, encoding="latin-1").read().replace("\r", "").split("\n")


def _write_lines(path: str, lines: list[str]):
    lines = [l for l in lines if l.strip()]
    if not lines:
        if os.path.isfile(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="latin-1", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


def mod_overrides(mod_dir: str, table: str) -> dict[str, list[list[str]]]:
    """A mod's replace rows for a table: key -> rows ([] = deleted)."""
    base = table[:-4]
    out: dict[str, list[list[str]]] = {}
    for line in _read_lines(os.path.join(mod_dir, base + ".replace.txt")):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("-"):
            out[s[1:].split(",")[0].strip().upper()] = []
            continue
        cells = split_rows(s)[0]
        out.setdefault(cells[0].upper(), []).append(cells)
    return out


def mod_new_rows(mod_dir: str, table: str) -> list[list[str]]:
    base = table[:-4]
    for cand in (table, base + ".txt", base.title() + ".txt"):
        p = os.path.join(mod_dir, cand)
        if os.path.isfile(p):
            return split_rows(open(p, encoding="latin-1").read())
    return []


def set_rows(mod_dir: str, table: str, key: str, rows: list[list[str]] | None):
    """Replace a record's rows in the mod (None = take the mod's replacement out again, [] = delete the record)."""
    base = table[:-4]
    path = os.path.join(mod_dir, base + ".replace.txt")
    keep = []
    for line in _read_lines(path):
        s = line.strip()
        if not s:
            continue
        k = (s[1:] if s.startswith("-") else s).split(",")[0].strip().upper()
        if k != key.upper():
            keep.append(s)
    if rows is not None:
        keep += [join_row(r) for r in rows] if rows else ["-" + key]
    _write_lines(path, keep)


def add_rows(mod_dir: str, table: str, rows: list[list[str]]):
    """Append new records (the table's .txt part in the mod)."""
    path = os.path.join(mod_dir, table[:-4] + ".txt")
    _write_lines(path, _read_lines(path) + [join_row(r) for r in rows])


def remove_new_rows(mod_dir: str, table: str, key: str):
    path = os.path.join(mod_dir, table[:-4] + ".txt")
    _write_lines(path, [l for l in _read_lines(path) if l.split(",")[0].strip().upper() != key.upper()])


def weapon_file(mod_dir: str, wid: str, base: str, changes: dict[int, str], schema: list[dict]) -> str:
    """Write <mod>/<id>.weapon: a game weapon changed in place (base == wid) or a new weapon based on `base`.
    changes = column index -> value; named columns use their .weapon name, others "column N" (1-based)."""
    names = NAMES["WEAPS.TXT"]
    lines = [f"id = {wid}", f"based on = {base}"]
    for i, v in sorted(changes.items()):
        if i == 0:
            continue
        # named settings that the plugin copies verbatim; muzzle flash (yes / no there), the hand connector and the
        # spent cartridge go in as raw columns
        raw = i in (6, 35, 37) or i not in names
        lines.append(f"column {i + 1} = {v}" if raw else f"{names[i]} = {v}")
    path = os.path.join(mod_dir, f"{wid}.weapon")
    os.makedirs(mod_dir, exist_ok=True)
    with open(path, "w", encoding="latin-1", newline="\r\n") as f:
        f.write("# made by the Desert Storm Editor's records window\n" + "\n".join(lines) + "\n")
    return path


def read_strings(mod_dir: str) -> dict[tuple[str, str], str]:
    """The mod's .strings texts: (section, key) -> text; section "" = every level, "M01" = only the mission whose
    texts are M01Strs.loc ("[M01]" line; "[all]" back to every level)."""
    out = {}
    for f in sorted(os.listdir(mod_dir)) if os.path.isdir(mod_dir) else []:
        if f.lower().endswith(".strings"):
            out.update(read_strings_file(os.path.join(mod_dir, f)))
    return out


def set_string(mod_dir: str, key: str, text: str | None, section: str = ""):
    """Set (or with None remove) a text in <mod>/<mod>.strings; section "M01" = only in that mission."""
    path = os.path.join(mod_dir, os.path.basename(os.path.normpath(mod_dir)) + ".strings")
    texts = read_strings_file(path)
    for k in [k for k in texts if k[0] == section.upper() and k[1].upper() == key.upper()]:
        del texts[k]
    if text is not None:
        texts[(section.upper(), key)] = text
    lines = []
    for sec in sorted({k[0] for k in texts}):
        if sec:
            lines.append(f"[{sec}]")
        lines += [f"{k} = {v.replace(chr(10), chr(92) + 'n')}" for (s2, k), v in texts.items() if s2 == sec]
    _write_lines(path, lines)


def read_strings_file(path: str) -> dict[tuple[str, str], str]:
    out = {}
    section = ""
    for line in _read_lines(path) if os.path.isfile(path) else []:
        t = line.strip()
        if t.startswith("["):
            section = t[1:].split("]", 1)[0].strip().upper()
            section = "" if section == "ALL" else section
        elif "=" in t and not t.startswith("#"):
            k, v = t.split("=", 1)
            out[(section, k.strip())] = v.strip().replace("\\n", "\n")
    return out


def weapon_changes(mod_dir: str) -> dict[str, tuple[str, dict[int, str], str, str]]:
    """The mod's .weapon files: upper-case id -> (based on, {column index: value}, file, id as written). Only the settings that map
    to one column are listed (model, damage, "column N" ...); special ones (sound files, silenced) are not."""
    by_name = {k.replace(" ", ""): i for i, k in NAMES["WEAPS.TXT"].items()}
    by_name.update({"icon": 4, "category": 5, "fireinterval": 10, "magazine": 15})
    out = {}
    if not os.path.isdir(mod_dir):
        return out
    for f in sorted(os.listdir(mod_dir)):
        if not f.lower().endswith(".weapon"):
            continue
        wid = base = ""
        cols: dict[int, str] = {}
        for line in _read_lines(os.path.join(mod_dir, f)):
            line = line.split("#", 1)[0]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = re.sub(r"[\s_]", "", k.lower())
            v = v.strip()
            if k == "id":
                wid = v
            elif k == "basedon":
                base = v
            elif k.startswith("column") and k[6:].isdigit():
                cols[int(k[6:]) - 1] = v
            elif k in by_name and by_name[k] != 0:
                cols[by_name[k]] = v
        if wid:
            out[wid.upper()] = (base, cols, f, wid)
    return out
