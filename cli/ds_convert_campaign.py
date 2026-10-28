"""Another Conflict game's campaign converted for Desert Storm (conflict_ds_tools/crossmission.py convert_campaign).

NOT the player / modder path (that is cli/ds_convert_maps.py: empty maps, no scripts). This keeps the mission script
adapter runnable for development and the Desert Storm II editor; DesertStormFix doesn't list converted campaigns.

    python cli/ds_convert_campaign.py ds2 [--levels MISSION01 MISSION02] [--source DIR] [--target DIR] [--out DIR]

  ds2         the source game (Desert Storm II); its install is found from the registry unless --source is given
  --levels    only these levels (default: the whole campaign, in order)
  --target    Desert Storm's folder (default: found from the registry)
  --out       where the converted campaign goes (default: <Desert Storm>\\PSE\\Ports\\<game>)

It is made from YOUR copies of both games: keep it local, never share it. Online every player converts from their own copy - the
folders are compared, never sent.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from conflict_ds_tools import crossmission, games  # noqa: E402
from conflict_ds_tools.porting import GameSource  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", choices=sorted(crossmission.CAMPAIGNS))
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
    for line in crossmission.convert_campaign(src, dst, out, levels=a.levels, log=print):
        print(line)
    print("-> " + out)


if __name__ == "__main__":
    main()
