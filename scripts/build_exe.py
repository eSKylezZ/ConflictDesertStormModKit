"""Build the editor as one Windows exe: dist/ConflictEditor.exe (editor/app.py - native window, game picker,
first-run extraction, the editor). Needs PyInstaller and pywebview (pip install pyinstaller pywebview).
Icon: editor/icon.ico (scripts/gen_icon.py).

  python scripts/build_exe.py

Bundled: the editor's Python modules, its web UI (editor/web) and per-game data (editor/games), the kit's library
(conflict_ds_tools minus the Blender UI) and cli/evo_convert.py. No game data - the exe extracts the player's own.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR = os.path.join(ROOT, "editor")
PKG = os.path.join(ROOT, "conflict_ds_tools")


def main():
    mods = [f[:-3] for f in sorted(os.listdir(EDITOR)) if f.endswith(".py") and f not in ("app.py", "mcp_server.py")]
    lib = ["conflict_ds_tools." + f[:-3] for f in sorted(os.listdir(PKG))
           if f.endswith(".py") and f not in ("__init__.py", "blender.py")]
    sep = os.pathsep
    # the editor's modules have plain top-level names (workflow, records, paths ...) - some match PyPI packages that
    # pyinstaller-hooks-contrib has hooks for; our own empty hooks take precedence
    hooks = os.path.join(ROOT, "build", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for m in mods + ["server", "evo_convert"]:
        with open(os.path.join(hooks, f"hook-{m}.py"), "w") as f:
            f.write("# the Conflict editor's own module - nothing to collect\n")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", "ConflictEditor", "--icon", os.path.join(EDITOR, "icon.ico"),
            "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build", "pyinstaller"),
            "--specpath", os.path.join(ROOT, "build"),
            "--paths", ROOT, "--paths", EDITOR, "--paths", os.path.join(ROOT, "cli"),
            "--additional-hooks-dir", hooks,
            "--add-data", f"{os.path.join(EDITOR, 'web')}{sep}web",
            "--add-data", f"{os.path.join(EDITOR, 'games')}{sep}games",
            "--exclude-module", "bpy", "--exclude-module", "tkinter", "--exclude-module", "matplotlib"]
    for m in mods + lib + ["evo_convert", "server"]:
        args += ["--hidden-import", m]
    try:                                    # World Tools' script check compiles the mission Lua with Lua 5.4 (lupa)
        import lupa.lua54  # noqa: F401
        args += ["--hidden-import", "lupa", "--hidden-import", "lupa.lua54"]
    except ImportError:
        print("lupa not installed - the exe's script check will only flag unknown game.* calls")
    args.append(os.path.join(EDITOR, "app.py"))
    print(" ".join(a if " " not in a else f'"{a}"' for a in args[3:]))
    subprocess.run(args, check=True, cwd=ROOT)
    exe = os.path.join(ROOT, "dist", "ConflictEditor.exe")
    print(f"-> {exe} ({os.path.getsize(exe) / 1e6:.0f} MB)")
    shutil.rmtree(os.path.join(ROOT, "build", "pyinstaller"), ignore_errors=True)


if __name__ == "__main__":
    main()
