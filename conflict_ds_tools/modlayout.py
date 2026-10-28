"""Where a mod's files go inside <game>\\Mods\\<mod> - the organised layout (new mods) and the flat one (older mods).

Every game's plugin finds a mod's files by their bare name at any depth (DesertStormFix, DesertStorm2Fix,
GreatEscapeFix, VietnamFix, GlobalStormFix, DeniedOpsFix), so the layout is only for people: new mods keep their files
in folders by type, mods made before stay flat (every file in the mod's top folder) and keep working.

    evos\\     .evo models, .dyn vehicle data, their editor sources (.road.json, .terrain.json)
    dds\\      textures (.dds .tga .png .bmp), image sheets (.img: minimaps)
    anims\\    animations (.prb .ani)
    sounds\\   sound caches / banks (.sch .sbk), .wav sources, .psf streams (Desert Storm: sounds\\<language>\\x.psf -
              the folder names the language: SAS / Delta / English / American ...; sounds\\ = every language)
    tables\\   table parts (.txt, .replace.txt), texts (.strings)
    defs\\     .weapon .skin .unit .vehicle .gear .pouches
    levels\\   scenes and everything next to them (.env, split scene parts, patches, routes, octrees, .lua / .dll
              levels, .level, editor .editor.json)
    top       campaign.txt, readme, records\\ (Desert Storm record edits), frames\\ (Desert Storm photo frames), anything
              else - these paths are read from the top folder by the plugins, so they never move

Rules (docs/internal/modding-parity.md in the patch repo):
- path_for: a file the mod already has is rewritten where it is; a flat mod (game files in its top folder) stays flat;
  otherwise the file goes to its layout folder.
- find: two files of one name in a mod -> the last by sorted relative path wins, as the plugins do (a mod priority
  list orders mods the same way: the one loaded last wins).
"""
from __future__ import annotations

import os

ORGANISED = "organised"
FLAT = "flat"
LAYOUTS = (ORGANISED, FLAT)

FOLDERS = {
    "evos": (".evo", ".dyn", ".road.json", ".terrain.json"),
    "dds": (".dds", ".tga", ".png", ".bmp", ".img"),
    "anims": (".prb", ".ani"),
    "sounds": (".sch", ".sbk", ".wav", ".psf"),
    "tables": (".txt", ".strings"),
    "defs": (".weapon", ".skin", ".unit", ".vehicle", ".gear", ".pouches"),
    "levels": (".env", ".envpatch", ".rmd", ".rmdpatch", ".uses", ".oct", ".lua", ".dll", ".dllpatch", ".level",
               ".editor.json", ".art", ".des", ".snd", ".si", ".rda", ".qtd", ".fas", ".spt", ".lay", ".lgt", ".emt",
               ".eml", ".grs", ".dmp"),
}
# read from the mod's top folder by the plugins (campaigns: DS1 / DS2 / TGE; records + frames: DS1)
TOP_FILES = ("campaign.txt",)
TOP_FOLDERS = ("records", "frames")
# Desert Storm's stream language folders (mods.cpp ScanFolder: the .psf's folder name)
PSF_LANGUAGES = ("SAS", "UK", "BRITISH", "ENGLISH", "DELTA", "US", "AMERICAN", "FRENCH", "GERMAN", "SPANISH",
                 "ITALIAN", "JAPANESE", "POLISH")

_BY_EXT = sorted(((ext, folder) for folder, exts in FOLDERS.items() for ext in exts), key=lambda e: -len(e[0]))


def folder_for(name: str) -> str:
    """The organised layout's folder for a file name ("" = the mod's top folder)."""
    low = os.path.basename(name).lower()
    if low in TOP_FILES or low.startswith("readme"):
        return ""
    for ext, folder in _BY_EXT:
        if low.endswith(ext):
            return folder
    return ""


def files(mod_dir: str) -> list:
    """Every file of a mod as a relative path ("evos\\GUN.EVO") in the plugins' order: folder by folder, names sorted
    case-insensitively (a folder's contents where its name sorts) - for a name the mod has twice, the last one wins."""
    out = []
    for dirpath, dirs, names in os.walk(mod_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            if not n.startswith("."):
                out.append(os.path.relpath(os.path.join(dirpath, n), mod_dir))
    return sorted(out, key=lambda r: [p.lower() for p in r.split(os.sep)])


def find_all(mod_dir: str, name: str) -> list:
    """Full paths of every file of that bare name in the mod (any depth), in the plugins' order (last one wins)."""
    want = os.path.basename(name).lower()
    if not os.path.isdir(mod_dir):
        return []
    return [os.path.join(mod_dir, r) for r in files(mod_dir) if os.path.basename(r).lower() == want]


def find(mod_dir: str, name: str):
    """The file of that bare name the game uses (any depth; two of them -> the last by sorted path), or None."""
    hits = find_all(mod_dir, name)
    return hits[-1] if hits else None


def duplicates(mod_dir: str) -> dict:
    """{name: [relative paths]} for names the mod has more than once - the last path is the one the game uses."""
    seen = {}
    for r in files(mod_dir):
        seen.setdefault(os.path.basename(r).upper(), []).append(r)
    return {k: v for k, v in seen.items() if len(v) > 1}


def is_flat(mod_dir: str) -> bool:
    """A mod made before the organised layout: it has game files (ones the layout would sort) in its top folder."""
    if not os.path.isdir(mod_dir):
        return False
    for n in os.listdir(mod_dir):
        if os.path.isfile(os.path.join(mod_dir, n)) and folder_for(n):
            return True
    return False


def path_for(mod_dir: str, name: str, layout: str = ORGANISED, language: str = "", create: bool = True) -> str:
    """Where a file of the mod is written.

    `name` = a bare file name, or a path under a top folder the plugins read as such ("records\\x.lua",
    "frames\\x.png" - kept as given). The mod's own copy anywhere is rewritten in place; a flat mod (or layout FLAT)
    gets the top folder; else the organised folder (language = Desert Storm's stream language folder for a .psf).
    `create` makes the folders."""
    norm = name.replace("/", os.sep).replace("\\", os.sep)
    head = norm.split(os.sep)[0].lower() if os.sep in norm else ""
    if head in TOP_FOLDERS:
        path = os.path.join(mod_dir, norm)
    elif os.sep in norm:
        raise ValueError("path_for takes a bare file name or records\\ / frames\\ paths, not %r" % name)
    else:
        existing = find(mod_dir, norm)
        if existing:
            return existing
        folder = "" if layout == FLAT or is_flat(mod_dir) else folder_for(norm)
        if folder == "sounds" and language and norm.lower().endswith(".psf"):
            folder = os.path.join(folder, language)
        path = os.path.join(mod_dir, folder, norm) if folder else os.path.join(mod_dir, norm)
    if create:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
