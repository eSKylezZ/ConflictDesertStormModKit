"""Mission DLL -> Lua mission (conflict_ds_tools/mission2lua.py, stage 1: faithful x86 translation).

    python cli/ds_mission2lua.py <Mission1.dll | game folder> [-o OUT_DIR] [--all] [--check]

  <dll>       one mission DLL -> OUT_DIR/<name>.lua (e.g. Mission1.lua)
  --all       every Mission*/Training* DLL of the game folder
  --check     also compile each result with Lua 5.4 (needs the `lupa` package) and print the coverage table

The output is made from YOUR game files and is game code: keep it local (default OUT_DIR = ./generated_missions,
gitignored), never commit or share it. To play one: copy it to <game>\\Mods\\<Any>\\Mission1.lua and start the level
"Mission1.lua" (developer console `level Mission1.lua`); DesertStormFix's mission runtime (dsfix.missionrt) runs it.
"""
import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from conflict_ds_tools import mission2lua  # noqa: E402


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
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default="generated_missions")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.all or os.path.isdir(a.input):
        files = sorted({os.path.normcase(p): p for p in glob.glob(os.path.join(a.input, "Mission*.dll")) + glob.glob(os.path.join(a.input, "PSE", "Originals", "Mission*.dll")) +
                        glob.glob(os.path.join(a.input, "PSE", "Originals", "Training*.dll")) +
                        glob.glob(os.path.join(a.input, "Training*.dll"))}.values())
    else:
        files = [a.input]
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for path in files:
        t0 = time.time()
        src, st = mission2lua.translate(path)
        name = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(a.out, name + ".lua")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        comp = compile_ok(src) if a.check else None
        rows.append((name, st, len(src), time.time() - t0, comp))
        print("%-12s %4d functions %6d instructions %3d thunks  unsupported %d  %.1f MB  %.2f s%s" % (
            name, st["functions"], st["instructions"], st["thunks"], sum(st["unsupported"].values()),
            len(src) / 1e6, time.time() - t0, "" if comp in (None, True) else "  LUA ERROR: %s" % comp))
        for k, v in sorted(st["unsupported"].items()):
            print("      unsupported: %s x%d" % (k, v))
        for w in st["warnings"]:
            print("      warning: %s" % w)
    print("written to %s (game code: keep it local)" % os.path.abspath(a.out))


if __name__ == "__main__":
    main()
