"""Another Conflict game's mission maps as empty Desert Storm levels for modders
(conflict_ds_tools/levelport.py convert_maps).

    python cli/ds_convert_maps.py ds2 [--levels MISSION01 MISSION02] [--source DIR] [--target DIR] [--out DIR]

  ds2         the source game (Desert Storm II); its install is found from the registry unless --source is given
  --levels    only these levels (default: all of its missions)
  --target    Desert Storm's folder (default: found from the registry)
  --out       where the maps go (default: <Desert Storm>\\PSE\\Ports\\<game>)

Each map is scenery only (no enemies, vehicles or mission logic) with the squad at the start - run one with
DesertStorm.exe +level mission01.lua or open it in the editor and build your mission. The models, weapons, vehicles
and soldier types the maps use come along. It is made from YOUR copies of both games: keep it local, never share it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from conflict_ds_tools import games, levelport  # noqa: E402
from conflict_ds_tools.porting import GameSource  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", choices=sorted(levelport.MAP_LEVELS))
    ap.add_argument("--levels", nargs="*")
    ap.add_argument("--source")
    ap.add_argument("--target")
    ap.add_argument("--out")
    a = ap.parse_args()
    installs = games.find_installs()
    source = a.source or installs.get(a.game)
    target = a.target or installs.get("ds1")
    if not source or not target:
        sys.exit("game folder not found - pass --source / --target")
    out = a.out or os.path.join(target, "PSE", "Ports", a.game)
    src, dst = GameSource(a.game, source), GameSource("ds1", target)
    for line in levelport.convert_maps(src, dst, out, levels=a.levels, log=print):
        print(line)
    print("-> " + out)


if __name__ == "__main__":
    main()
