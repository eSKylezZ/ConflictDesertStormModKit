"""Editor commands: the game's own missions opened as readable Lua (PSE docs/sdk/reference/mission-replacement.md).

game_mission_open makes Mods\\<mod>\\mission_src\\<Mission>\\ (mission.lua, refs.lua, scripts/events.lua ... - Lua
made from the player's own mission DLL by conflict_ds_tools/mission2lua_readable.py) once, opens the level's scene,
and from then on the Script panel (get_script / save_script) edits that mission's scripts/events.lua; every save
rebuilds Mods\\<mod>\\<Mission>.lua - the bundle with "--@replaces <Mission>.dll", which DesertStormFix 3.3 runs
instead of the game's DLL.

Both are game code made from the player's files: export_mod_zip leaves them out (they can't be shared as they are).
"""
from __future__ import annotations

import glob
import os

from commands import CommandError, Session, command, open_level  # noqa: F401
from conflict_ds_tools import archive, games

SRC = "mission_src"
GAME_CODE_MARK = "Game code: never commit or distribute"


def _game(s: Session) -> str:
    game = s.game_dir or archive.find_game_dir()
    if not game or not os.path.isdir(game):
        raise CommandError("game folder not known - start the editor with --game <folder>")
    return game


def _dlls(game):
    """{lower stem: path} of the game's mission DLLs (game folder + PSE\\Originals)."""
    return {os.path.splitext(os.path.basename(p))[0].lower(): p
            for p in games.game_files(game, exts=(".dll",), prefixes=("mission", "training"))}


def _mod_dir(s, mod):
    if not mod or any(c in mod for c in '\\/:*?"<>|'):
        raise CommandError("mod: a folder name")
    return os.path.join(_game(s), "Mods", mod)


def is_game_mission_file(path):
    """A readable game mission (bundle or source file): made from the player's DLL, never packaged."""
    norm = path.replace("\\", "/").lower()
    if "/%s/" % SRC in norm:
        return True
    if norm.endswith(".lua"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
        except OSError:
            return False
        return GAME_CODE_MARK in head
    return False


@command("The game's missions that can be opened as Lua, and which mods already have one as Lua.")
def game_missions(s: Session):
    game = _game(s)
    have = {}
    for p in glob.glob(os.path.join(game, "Mods", "*", SRC, "*", "mission.lua")):
        mission_dir = os.path.dirname(p)
        mod = os.path.basename(os.path.dirname(os.path.dirname(mission_dir)))
        have.setdefault(os.path.basename(mission_dir).lower(), []).append(mod)
    return [{"mission": os.path.splitext(os.path.basename(p))[0], "level": stem.upper(), "mods": have.get(stem, [])}
            for stem, p in sorted(_dlls(game).items())]


def _bundle_path(s, mod, stem):
    """The mod's <Mission>.lua bundle: where the mod already has it (outside mission_src\\), else where the mod's layout
    puts a new .lua (conflict_ds_tools.modlayout: levels\\ in an organised mod, the top folder in a flat one)."""
    from commands import mod_layout
    from conflict_ds_tools import modlayout
    folder = _mod_dir(s, mod)
    src = os.path.join(folder, SRC) + os.sep
    hits = [p for p in modlayout.find_all(folder, stem + ".lua") if not p.lower().startswith(src.lower())]
    if hits:
        return hits[-1]
    if mod_layout(s) == modlayout.FLAT or modlayout.is_flat(folder):
        return os.path.join(folder, stem + ".lua")
    out = os.path.join(folder, modlayout.folder_for(stem + ".lua"), stem + ".lua")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return out


def _write_bundle(s, mod, stem, src):
    from conflict_ds_tools import mission2lua_readable as m2r
    text = m2r.bundle_folder(src)
    lines = text.split("\n")
    lines.insert(1, "--@replaces %s.dll" % stem)
    out = _bundle_path(s, mod, stem)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return out


@command("Open one of the game's missions as Lua in a mod: the first time its readable Lua is made from your "
         "game's DLL (Mods\\<mod>\\mission_src\\<Mission>); the level's scene opens, and the Script panel edits the "
         "mission's events (saving rebuilds the mod's <Mission>.lua, which the game runs instead of the DLL).",
         mission="mission name (Mission1, Training2 ...)", mod="mod folder (made if missing)")
def game_mission_open(s: Session, mission, mod):
    from conflict_ds_tools import mission2lua_readable as m2r
    game = _game(s)
    dlls = _dlls(game)
    stem_l = mission.lower().removesuffix(".dll")
    if stem_l not in dlls:
        raise CommandError("no game mission %s - game_missions lists them" % mission)
    dll = dlls[stem_l]
    stem = os.path.splitext(os.path.basename(dll))[0]
    root = os.path.join(_mod_dir(s, mod), SRC)
    src = os.path.join(root, stem)
    made = False
    if not os.path.exists(os.path.join(src, "mission.lua")):
        os.makedirs(root, exist_ok=True)
        parts, _g = m2r.translate(dll, game_dir=game)
        m2r.write_folder(parts, root, stem)
        stray = os.path.join(root, stem + ".lua")   # write_folder's own bundle: the mod's one is rebuilt below
        if os.path.exists(stray):
            os.remove(stray)
        made = True
    bundle = _write_bundle(s, mod, stem, src)
    level = stem.upper()
    open_level(s, level)
    s.mission = None
    s.game_mission = (mod, stem, src)
    return {"mission": [mod, stem], "base": level, "made": made, "source": src, "bundle": bundle,
            **s.env.summary()}


def script_get(s):
    """get_script for an open game mission: its scripts/events.lua."""
    mod, stem, src = s.game_mission
    path = os.path.join(src, "scripts", "events.lua")
    with open(path, encoding="utf-8") as f:
        return {"path": path, "text": f.read()}


def script_save(s, text):
    mod, stem, src = s.game_mission
    path = os.path.join(src, "scripts", "events.lua")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("\r\n", "\n"))
    bundle = _write_bundle(s, mod, stem, src)
    problem = None
    try:
        import lupa.lua54 as lua
        rt = lua.LuaRuntime()
        ok, err = rt.execute('return function(s) local f, e = load(s, "=' + stem + '"); return f ~= nil, e end')(
            open(bundle, encoding="utf-8").read())
        problem = None if ok else err
    except ImportError:
        pass
    with open(path, encoding="utf-8") as f:
        out = {"path": path, "text": f.read(), "bundle": bundle}
    if problem:
        out["lua_error"] = problem
    return out
