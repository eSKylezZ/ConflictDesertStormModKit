"""Make and check mission patches against your own mission DLL (no game needed).

    python cli/ds_mission_patch.py --make "<game>\\Mission1.dll" work\\Mission1\\scripts\\events.lua -o MyMod\\Mission1.patch.lua
    python cli/ds_mission_patch.py "<game>\\Mission1.dll" MyMod\\Mission1.patch.lua [more.patch.lua ...] [-o OUT]

--make: you edited the readable mission's scripts/events.lua (ds_mission2lua_readable.py); this writes a patch whose edit
blocks make exactly those changes (each with enough context lines to match one place) - add your own Lua below them.

Makes the readable mission from the DLL (mission2lua_readable - the same text DesertStormFix makes in the game), applies
the patches in the order given (= the mods' load order in the game), says for each whether it applies, and checks that
the patched mission compiles (needs `lupa`). -o writes OUT\\<Mission>.patched.lua with the patches' Lua inline before
start.lua - an approximation of what the game runs (there each patch is its own chunk in the mission's environment).
The generated file is game code made from YOUR game files: keep it local. If the DLL isn't in the game folder, it is in
<game>\\PSE\\Originals. Reference: the patch repository's docs/sdk/reference/mission-patches.md.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from conflict_ds_tools import mission2lua_readable as m2r  # noqa: E402
from conflict_ds_tools import mission_patch as mp  # noqa: E402


def compile_ok(src):
    try:
        import lupa.lua54 as lua
    except ImportError:
        return None
    rt = lua.LuaRuntime()
    ok, err = rt.execute('return function(s) local f, e = load(s, "=patched"); return f ~= nil, e end')(src)
    return True if ok else err


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll")
    ap.add_argument("patches", nargs="+", help="patches to check (--make: the edited events.lua)")
    ap.add_argument("-o", "--out")
    ap.add_argument("--make", action="store_true")
    a = ap.parse_args()
    parts, _g = m2r.translate(a.dll)
    text = parts["bundle"]
    name = os.path.basename(a.dll)
    if a.make:
        edited = mp.normalize(open(a.patches[0], "rb").read())
        try:
            patch = mp.make(text, parts["scripts/events.lua"], edited, name)
        except mp.PatchError as e:
            sys.exit("[fail] %s" % e)
        n = patch.count("[@edit")
        if a.out:
            with open(a.out, "w", encoding="utf-8", newline="\n") as f:
                f.write(patch)
            print("%s: %d edit block(s) - add your own Lua at the end" % (a.out, n))
        else:
            sys.stdout.write(patch)
        return
    inline, failed = [], 0
    for path in a.patches:
        label = os.path.basename(path)
        body = mp.normalize(open(path, "rb").read())
        ok = compile_ok(body)
        try:
            if ok not in (None, True):
                raise mp.PatchError(ok)
            edits = mp.parse(body, name)
            text = mp.apply(text, edits)
            print("patch %s: %d edit(s) applied" % (label, len(edits)))
            inline.append("do -- %s\n%s\nend\n" % (label, body))
        except mp.PatchError as e:
            failed += 1
            print("[fail] %s: %s - this patch is left out" % (label, e))
    head = 'SCENE = SCENE or {objects = {}, waypoints = {}, regions = {}, cameras = {}}\nX = X86RT\n'
    text = text.replace(mp.START_MARKER, head + "".join(inline) + mp.START_MARKER, 1)
    ok = compile_ok(text)
    if ok not in (None, True):
        failed += 1
        print("[fail] the patched mission doesn't compile: %s" % ok)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        out = os.path.join(a.out, os.path.splitext(name)[0] + ".patched.lua")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("->", out, "(game code: keep it local)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
