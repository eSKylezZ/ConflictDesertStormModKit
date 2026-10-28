"""What the game's models and skins are, for the Blender side: Asset Browser catalogs for the editable .blend library
and the uniforms (skin textures) a squad soldier can wear. No Blender in here.

Catalogs (Blender's blender_assets.cats.txt in the library's root folder): Vehicles (a <MODEL>.DYN or a VEHICLE.TXT
row), Weapons (a WEAPS.TXT row uses the model), Characters (skinned), Kit Pieces (world-piece models - the level
ground / building runs kit pieces are cut from), Structures (buildings, walls, bridges ... by name), Props (the rest).
"""
from __future__ import annotations

import os
import re
import uuid

from . import modlayout

CATALOGS = ("Vehicles", "Weapons", "Characters", "Structures", "Kit Pieces", "Props")
DS1_TITLE = "Conflict Desert Storm"     # the catalog tree's top name (each game its own; DS1 keeps its first ids)
_NAMESPACE = uuid.UUID("6b1f3c9e-0d7a-4c55-9a8e-6f2b1d3e4c5a")     # fixed: the same catalog ids every run

_STRUCTURE = re.compile(r"BUILD|HOUSE|HUT|WALL|BUNKER|BRIDGE|TOWER|HANGAR|TENT|GATE|FENCE|SHED|BARRACK|MOSQUE|"
                        r"PALACE|SHACK|SILO|PYLON|ROOF|STAIR|DOOR|WINDOW|SANDBAG|GARAGE|FACTORY|DEPOT|FORT", re.I)


# by name, only for a game whose weapon / vehicle tables can't be read (Denied Ops keeps them in XML / packages):
# whole name parts, so FUEL_TANK / GUNBOX / AMMO crates stay props
_WEAPON = re.compile(r"(^|_)(1ST-PER|3RD-PER|RIFLE|PISTOL|GRENADE|LAW|SMG|SHOTGUN|SNIPER|MINIMI|CHAINGUN|RPG\d*|"
                     r"HEALING-?GUN|M\d{2,3}|AK\d*)(_|$)", re.I)
_NOT_WEAPON = re.compile(r"(^|_)(AMMO|GUNBOX|CRATE|BOX)(_|$)", re.I)
_VEHICLE = re.compile(r"(^|_)(HELICOPTER|HIND|HUEY|TRUCK|JEEP|HUMVEE|APC|BMP\d*|BTR\d*|T\d{2}|TANK|BOAT|CAR|"
                      r"VAN|BUS|LANDROVER)(_|$)", re.I)
_NOT_VEHICLE = re.compile(r"(^|_)(FUEL|WATER|OIL|GAS|SHARD|WRECK|DEAD)(_|$)", re.I)


def catalog_id(name, title=DS1_TITLE):
    """Stable UUID string of a catalog of one game's library (Desert Storm's ids stay what they were before the
    other games had libraries)."""
    return str(uuid.uuid5(_NAMESPACE, name if title == DS1_TITLE else title + "/" + name))


def _catalog_line(name, title):
    return f"{catalog_id(name, title)}:{title}/{name}:{title}-{name.replace(' ', '-')}"


def category(name, node_type=0, skinned=False, weapon_models=(), vehicle_models=(), has_dyn=False, by_name=False):
    """Catalog for a model: name (file stem), its first object's node type (5 = world piece - callers pass 5 for any
    game's world piece, evo.is_world_piece), skinned (a character),
    the model names WEAPS.TXT / VEHICLE.TXT use (upper case) and whether a <name>.DYN exists; by_name = the game
    has no readable tables: weapons / vehicles by name parts."""
    up = name.upper()
    if skinned:
        return "Characters"
    if has_dyn or up in vehicle_models:
        return "Vehicles"
    if up in weapon_models:
        return "Weapons"
    if by_name and _VEHICLE.search(up) and not _NOT_VEHICLE.search(up):
        return "Vehicles"
    if by_name and _WEAPON.search(up) and not _NOT_WEAPON.search(up):
        return "Weapons"
    if node_type == 5:
        return "Kit Pieces"
    if _STRUCTURE.search(up):
        return "Structures"
    return "Props"


def catalog_text(names=CATALOGS, title=DS1_TITLE):
    """A blender_assets.cats.txt defining the catalogs (under `title`, the game's name)."""
    lines = ["# This is an Asset Catalog Definition file for Blender.", "#",
             "# Empty lines and lines starting with `#` will be ignored.",
             "# The first non-ignored line should be the version indicator.",
             "# Other lines are of the format \"UUID:catalog/path/for/assets:simple catalog name\"", "",
             "VERSION 1", ""]
    lines += [_catalog_line(n, title) for n in names]
    return "\n".join(lines) + "\n"


def merge_catalog_file(path, names=CATALOGS, title=DS1_TITLE):
    """Write the catalogs into a blender_assets.cats.txt, keeping catalogs already defined there."""
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = ""
    if not old.strip():
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(catalog_text(names, title))
        return
    have = {line.split(":", 1)[0] for line in old.splitlines() if line and not line.startswith("#")}
    add = [n for n in names if catalog_id(n, title) not in have]
    if add:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if not old.endswith("\n"):
                f.write("\n")
            f.write("\n".join(_catalog_line(n, title) for n in add) + "\n")


def table_models(rows, col=None):
    """Upper-case model names in one column of a catalog table's rows (lists of strings); col None = every column
    (the games keep the model in different columns - DS1 WEAPS 1, Global Storm 2 - callers intersect with real
    model names)."""
    if col is None:
        return {c.strip().upper() for r in rows for c in r if c.strip() not in ("", "-1")}
    return {r[col].strip().upper() for r in rows if len(r) > col and r[col].strip() not in ("", "-1")}


# ---- uniforms ----
# squad bodies -> (soldier name, role letter in the CSkins tables: R rifleman, S sniper, H heavy, C engineer)
HERO_BODIES = {"HERO01_ARMSTRONG": ("Bradley", "R"), "HERO02_RAMIREZ": ("Connors", "H"),
               "HERO03_JONES": ("Jones", "C"), "HERO04_FOLEY": ("Foley", "S")}
_HERO_SKIN = re.compile(r"^HERO0([1-4])_(UK|US|IR|RU)_\d+[A-Z]*$", re.I)


def hero_number(model):
    """1-4 for a squad body model name (HERO01_ARMSTRONG ...), else None."""
    m = re.match(r"^HERO0([1-4])_", model or "", re.I)
    return int(m.group(1)) if m else None


def game_skins(stems, hero):
    """Game skin textures for squad body `hero` (1-4) among texture file stems: HERO0n_UK_01, HERO0n_US_01,
    HERO0n_IR_01 ... sorted SAS, Delta, Iraqi, Russian."""
    order = {"UK": 0, "US": 1, "IR": 2, "RU": 3}
    out = []
    for s in stems:
        m = _HERO_SKIN.match(s)
        if m and int(m.group(1)) == hero:
            out.append(s.upper())
    return sorted(set(out), key=lambda s: (order[s.split("_")[1]], s))


def skin_label(stem):
    side = {"UK": "SAS", "US": "US Delta", "IR": "Iraqi", "RU": "Russian"}
    parts = stem.upper().split("_")
    return f"{side.get(parts[1], parts[1])} {parts[2]}" if len(parts) >= 3 else stem


def read_skin_file(text):
    """{key: value} of a mod .skin file (lower-case keys, `key = value` lines, # comments)."""
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def skin_fits(skin, hero):
    """Whether a .skin's `soldier` (a name or role letter) is squad body `hero` (1-4)."""
    soldier = skin.get("soldier", "").strip().lower()
    for body, (name, role) in HERO_BODIES.items():
        if int(body[5]) == hero:
            return soldier in (name.lower(), role.lower(), body.lower()) or \
                (name == "Connors" and soldier == "ramirez")
    return False


def mod_skins(mods_dir, hero, exts=(".dds", ".tga", ".png")):
    """[(texture name, path, label)] of the mods' .skin files for squad body `hero` whose texture file exists."""
    out = []
    try:
        mods = sorted(os.listdir(mods_dir))
    except OSError:
        return out
    for mod in mods:
        folder = os.path.join(mods_dir, mod)
        if not os.path.isdir(folder):
            continue
        # textures anywhere in the mod (organised mods keep .skin files in defs\ and textures in dds\, as the game
        # finds them by name in any folder); two of one name -> the last by sorted path, as the game picks
        by_stem, skins = {}, []
        for rel in modlayout.files(folder):
            stem, ext = os.path.splitext(os.path.basename(rel))
            if ext.lower() in exts:
                by_stem[stem.upper()] = os.path.join(folder, rel)
            elif ext.lower() == ".skin":
                skins.append(os.path.join(folder, rel))
        for path in skins:
            try:
                with open(path, encoding="latin-1") as fh:
                    skin = read_skin_file(fh.read())
            except OSError:
                continue
            tex = skin.get("texture", "").strip()
            if not tex or not skin_fits(skin, hero) or tex.upper() not in by_stem:
                continue
            label = skin.get("uniform name") or " ".join(x for x in (skin.get("first name"), skin.get("last name")) if x)
            out.append((tex.upper(), by_stem[tex.upper()], f"{label or tex} ({mod.lstrip('_')})"))
    return out
