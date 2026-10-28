"""Ports models and animations from another Conflict game into Desert Storm (docs/PORTING_PLAN.md, part 1).

  python cli/ds_port.py <recipe.port> [--source <folder>] [--target <folder>] [--mods <folder>]

  recipe     what to take (conflict_ds_tools/porting.py has the format): name, from, model / animation lines
  --source   the source game's folder or a folder extracted from it (default: the recipe's 'source =', else the
             installed game)
  --target   the target game's folder or an extracted folder (default: the installed Desert Storm)
  --mods     where the mod folder goes (default: <target game folder>\\Mods)

Writes <mods>\\<name>\\ (models, textures, animations, port-report.txt). Everything is made from your own copies of
both games: share the recipe, not the output.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools import games, porting  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("recipe")
ap.add_argument("--source")
ap.add_argument("--target")
ap.add_argument("--mods")
a = ap.parse_args()

try:
    recipe = porting.read_recipe(open(a.recipe, encoding="utf-8").read())
    mods = a.mods
    if not mods:
        tdir = a.target or games.find_game_dir(games.GAMES[recipe.target_game])
        if not tdir or games.detect(tdir) is None:
            sys.exit("pass --mods: the target game folder isn't known (%s)" % (tdir or "not found"))
        mods = os.path.join(tdir, "Mods")
    report = porting.run(recipe, mods, a.source, a.target, log=lambda s: print(s, flush=True))
except porting.PortError as e:
    sys.exit("error: %s" % e)
print(report[-1])
sys.exit(1 if "[fail]" in "\n".join(report) else 0)
