"""Mission DLL -> readable Lua mission (conflict_ds_tools/mission2lua_readable.py, stage 2 of the translation).

    python cli/ds_mission2lua_readable.py <Mission1.dll | game folder> [-o OUT_DIR] [--all] [--check]
    python cli/ds_mission2lua_readable.py --bundle OUT_DIR/Mission1          (rebuild Mission1.lua after editing)

  <dll>       one mission DLL -> OUT_DIR/<name>/ (mod-folder layout) + OUT_DIR/<name>.lua (the level the game runs)
  --all       every Mission*/Training* DLL of the game folder
  --check     compile each result with Lua 5.4 (needs `lupa`) and print the coverage table
  --bundle F  rebuild F.lua from the edited folder F (mission.lua, refs.lua, scripts/*.lua)

Layout of OUT_DIR/<name>/ (DESIGN.md §13.4):
  mission.lua          data: name, DLL hash, archive, scene, objectives, event ids, save-block layout, the memory the
                       game sees (strings, tables), the callbacks the game calls - literals only
  refs.lua             data: editor ID -> number for the scene objects / waypoints / regions the logic names
  scripts/events.lua   the mission logic (edit this)
  scripts/runtime.lua  generated glue to the game (the script API by name, memory, callbacks) - regenerate, don't edit
  scripts/start.lua    level start (maps the memory, runs the DLL's LevelSetup, installs the script object)

Generated output is game code (made from YOUR game files): keep it local (default OUT_DIR = ./generated_missions/
readable, gitignored), never commit or share it. To play one: copy <name>.lua to <game>\\Mods\\<Any>\\ and start the
level "<name>.lua" (developer console `level Mission1.lua`); DesertStormFix's mission runtime (dsfix.missionrt) runs it.
"""
import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from conflict_ds_tools import mission2lua_readable as m2r  # noqa: E402


def compile_ok(src):
    try:
        import lupa.lua54 as lua
    except ImportError:
        return None
    rt = lua.LuaRuntime()
    ok, err = rt.execute('return function(s) local f, e = load(s, "=mission"); return f ~= nil, e end')(src)
    return True if ok else err


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?")
    ap.add_argument("-o", "--out", default=os.path.join("generated_missions", "readable"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--bundle")
    a = ap.parse_args()
    if a.bundle:
        folder = os.path.abspath(a.bundle)
        text = m2r.bundle_folder(folder)
        out = folder.rstrip("\\/") + ".lua"
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        ok = compile_ok(text)
        print("%s (%d lines)%s" % (out, len(text.splitlines()), "" if ok in (None, True) else "  LUA ERROR: %s" % ok))
        return
    if not a.input:
        ap.error("a mission DLL or the game folder")
    if a.all or os.path.isdir(a.input):
        files = sorted({os.path.normcase(p): p for p in glob.glob(os.path.join(a.input, "Mission*.dll")) + glob.glob(os.path.join(a.input, "PSE", "Originals", "Mission*.dll")) +
                        glob.glob(os.path.join(a.input, "PSE", "Originals", "Training*.dll")) +
                        glob.glob(os.path.join(a.input, "Training*.dll"))}.values())
    else:
        files = [a.input]
    os.makedirs(a.out, exist_ok=True)
    print("%-12s %5s %6s %5s %6s %6s %6s %5s %6s %6s %6s %5s" % (
        "mission", "fns", "lines", "goto", "named", "unnam", "%named", "raw", "Dvars", "refs", "undef", "time"))
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        t0 = time.time()
        try:
            parts, g = m2r.translate(path)
        except m2r.Unsupported as e:
            print("%-12s unsupported: %s" % (name, e))
            continue
        m2r.write_folder(parts, a.out, name)
        st = g.stats
        named, unnamed = st["api_calls_named"], st["api_calls_unnamed"]
        comp = compile_ok(parts["bundle"]) if a.check else None
        print("%-12s %5d %6d %5d %6d %6d %5.1f%% %5d %6d %6d %6d %4.1fs%s" % (
            name, len(g.tr.funcs), len(parts["scripts/events.lua"].splitlines()), st["gotos"], named, unnamed,
            100.0 * named / max(1, named + unnamed), st["raw_memory"], st["memory_globals"], st["refs_named"],
            st["undefined_values"], time.time() - t0, "" if comp in (None, True) else "  LUA ERROR: %s" % comp))
    print("written to %s (game code: keep it local)" % os.path.abspath(a.out))


if __name__ == "__main__":
    main()
