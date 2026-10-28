"""The games the kit supports and what differs between them outside the file formats.

The readers decide most things from the files themselves (scene "Version", EOBJ version, route version). What a file
can't say - which exe and plugin a game has, where it is installed, how its archive hashes names - lives in a Game
profile. The tools work on one game at a time: `current()` (Desert Storm until `set_current()` / `use_folder()` picks
another). Per-game editor data (API slot names, base missions) lives in editor/games/<id>/.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    id: str                       # ds1, ds2, cv, cg - folder names under editor/games/
    title: str                    # "Conflict: Desert Storm"
    short: str                    # "Desert Storm" (window titles, messages)
    exes: tuple                   # the game's exe names (first = the one to start)
    plugin: str                   # our dinput8 plugin: <plugin>.log, Mods\<plugin>-report.txt, %TEMP%\<plugin>
    registry: tuple               # (key under HKLM\SOFTWARE[\WOW6432Node], value) - value = exe path or folder
    steam_dirs: tuple             # substrings of steamapps\common folder names
    install_names: tuple          # usual install folder names (retail, GOG ...)
    hash_upper: bool              # archive name hash over the upper-cased name (DS1) or the lower-cased one
    script_host: bool = False     # the plugin runs Lua missions (editor: save as mission, new level, Play)
    game_refs: bool = False       # the plugin builds .envpatch / .rmdpatch / .uses / .dllpatch from the player's install
                                  # (editor Save as mod writes patches instead of copies of the game's scene / routes)
    port: int = 8765              # the editor's default port

    marker_file: str = "catalog.dat"


GAMES = {g.id: g for g in (
    Game("ds1", "Conflict: Desert Storm", "Desert Storm", ("DesertStorm.exe",), "DesertStormFix",
         (("Pivotal Games\\Conflict Desert Storm", "ExePath"),), ("conflict_desert_storm",),
         ("GOG Games\\Conflict - Desert Storm", "GOG Galaxy\\Games\\Conflict - Desert Storm", "Conflict - Desert Storm",
          "Conflict Desert Storm"),
         hash_upper=True, script_host=True, marker_file="chardata.dat", game_refs=True),
    Game("ds2", "Conflict: Desert Storm II", "Desert Storm II", ("CDS II.exe", "CDS_II2.EXE"), "DesertStorm2Fix",
         (("SCi Games\\CDS II", "ExePath"), ("SCi Games\\CDS II", "InstallPath")), ("desert storm ii", "desert storm 2"),
         ("SCi Games\\Conflict Desert Storm II", "SCi Games\\Conflict - Desert Storm II", "Conflict Desert Storm II",
          "Conflict - Desert Storm II", "Conflict Desert Storm 2"),
         hash_upper=False, game_refs=True),
    Game("cv", "Conflict: Vietnam", "Vietnam", ("Vietnam.exe",), "VietnamFix",
         (("Global Star Software\\Conflict Vietnam", "ExePath"), ("SCi Games\\Conflict Vietnam", "InstallPath")),
         ("vietnam",), ("SCi Games\\Conflict Vietnam", "Conflict Vietnam"),
         hash_upper=False, port=8780),
    Game("cg", "Conflict: Global Storm", "Global Storm", ("ConflictGlobal.exe",), "GlobalFix",
         (("Pivotal\\Conflict Global", "ExePath"), ("SCi Games\\Conflict Global Storm", "InstallPath")),
         ("conflict global",), ("SCi Games\\Conflict Global Storm", "Conflict Global Storm", "Conflict Global Terror"),
         hash_upper=False, port=8790),
    # Pivotal's other games on the same engine line (readers: work in progress)
    Game("ge", "The Great Escape", "Great Escape", ("GreatEscape.exe",), "GreatEscapeFix",
         (("SCi Games\\The Great Escape", "InstallPath"), ("SCi\\The Great Escape", "InstallPath")), ("great escape",),
         ("SCi Games\\The Great Escape", "The Great Escape"), hash_upper=False, port=8800),
    Game("do", "Conflict: Denied Ops", "Denied Ops", ("ConflictDeniedOps.exe",), "DeniedOpsFix",
         (("Eidos\\Conflict Denied Ops", "InstallPath"),), ("conflict denied ops",),
         ("Conflict Denied Ops",), hash_upper=False, port=8810, marker_file="catalog.pc.dat"),
)}

_current = GAMES["ds1"]


def current() -> Game:
    return _current


def set_current(game) -> Game:
    """Make `game` (an id or a Game) the one the tools work on."""
    global _current
    _current = game if isinstance(game, Game) else GAMES[str(game).lower()]
    return _current


# DesertStormFix (PSE) moves the game's original archives (*.dat), level sound caches (*.sch) and mission DLLs into
# <game>\PSE\Originals once processed; readers look in the game folder first, then there.
ORIGINALS = os.path.join("PSE", "Originals")


def game_file(folder, name):
    """Path of a game file: the game folder's copy, else the PSE/Originals one (moved by DesertStormFix), else the
    game-folder path (missing)."""
    here = os.path.join(folder, name)
    if os.path.exists(here):
        return here
    there = os.path.join(folder, ORIGINALS, name)
    return there if os.path.exists(there) else here


def game_files(folder, exts=None, prefixes=None):
    """Full paths of the game's own files in the game folder and PSE/Originals (the game folder's copy when both),
    filtered by extension(s) and / or name prefix(es) (lower-case), sorted by lower-case name."""
    out = {}
    for d in (os.path.join(folder, ORIGINALS), folder):   # game folder last: it wins
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            low = f.lower()
            p = os.path.join(d, f)
            if not os.path.isfile(p) or (exts and not low.endswith(tuple(exts))) or                     (prefixes and not low.startswith(tuple(prefixes))):
                continue
            out[low] = p
    return [out[k] for k in sorted(out)]


def detect(folder) -> Game | None:
    """The game installed in `folder` (by its exe), or None."""
    if not folder or not os.path.isdir(folder):
        return None
    names = {f.lower() for f in os.listdir(folder)}
    for g in GAMES.values():
        if any(e.lower() in names for e in g.exes):
            return g
    return None


def use_folder(folder) -> Game | None:
    """Detect the game in a game folder and make it current; None (current unchanged) if no exe is recognised."""
    g = detect(folder)
    if g:
        set_current(g)
    return g


def detect_extracted(folder) -> Game | None:
    """The game an extracted folder (ds_extract.py output) came from, by the level folders it has."""
    if not folder or not os.path.isdir(folder):
        return None
    subs = {d.lower() for d in os.listdir(folder)}
    marker = os.path.join(folder, ".game")
    if os.path.isfile(marker):
        return GAMES.get(open(marker, encoding="utf-8").read().strip().lower())
    if "mission1" in subs or "mission12a" in subs:
        return GAMES["ds1"]
    if "chardata" in subs and "mission01" in subs:
        return GAMES["ds2"]
    if "training00a" in subs:
        return GAMES["cg"]
    if "mission01" in subs:
        return GAMES["cv"]
    return None


def find_installs() -> dict:
    """{game id: folder} for every supported game found on this PC (registry, Steam libraries, usual paths)."""
    out = {}
    for g in GAMES.values():
        d = find_game_dir(g)
        if d:
            out[g.id] = d
    return out


def find_game_dir(game: Game | None = None):
    """The folder with `game`'s exe and archives: its registry keys, Steam libraries, then the usual install paths on
    every drive. None when not found."""
    g = game or current()
    cands = []
    try:
        import winreg
        for key, val in g.registry:
            for root, prefix in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\"),
                                 (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\"),
                                 (winreg.HKEY_CURRENT_USER,
                                  "Software\\Classes\\VirtualStore\\MACHINE\\SOFTWARE\\WOW6432Node\\")):
                for v in (val, "GameDir", "InstallPath", "Path", "ExePath"):
                    try:
                        with winreg.OpenKey(root, prefix + key) as k:
                            p = winreg.QueryValueEx(k, v)[0]
                        cands.append(p if os.path.isdir(p) else os.path.dirname(p))
                    except OSError:
                        pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                steam = winreg.QueryValueEx(k, "SteamPath")[0]
            lib = os.path.join(steam, "steamapps", "libraryfolders.vdf")
            paths = [steam]
            if os.path.exists(lib):
                paths += re.findall(r'"path"\s+"([^"]+)"', open(lib, encoding="utf-8", errors="replace").read())
            for p in paths:
                common = os.path.join(p.replace("\\\\", "\\"), "steamapps", "common")
                if os.path.isdir(common):
                    cands += [os.path.join(common, d) for d in os.listdir(common)
                              if any(s in d.lower() for s in g.steam_dirs)]
        except OSError:
            pass
    except ImportError:
        pass
    for drive in "CDEFGHIJ":
        for base in ("", "Program Files (x86)\\", "Program Files\\", "Games\\", "SCI Games\\"):
            cands += [drive + ":\\" + base + n for n in g.install_names]
    found = []
    for c in cands:
        if c and detect(c) is g and os.path.exists(game_file(c, g.marker_file)):
            c = os.path.normpath(c)
            if c.lower() not in (f.lower() for f in found):
                found.append(c)
    # several installs (e.g. Steam + GOG): the one with a Mods folder is where the player's mods and missions are
    with_mods = [f for f in found if os.path.isdir(os.path.join(f, "Mods"))]
    return (with_mods or found or [None])[0]
