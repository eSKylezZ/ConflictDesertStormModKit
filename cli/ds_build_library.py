"""The asset library: everything of the other Conflict games converted for Desert Storm (conflict_ds_tools/library.py).

    python cli/ds_build_library.py [ds2 ge cv cg] [--target DIR]

  games       which source games (default: every installed one of Desert Storm II, The Great Escape, Vietnam,
              Global Storm)
  --target    Desert Storm's folder (default: found from the registry)

Writes <Desert Storm>\\PSE\\Library\\<game>: every model, character, vehicle, weapon, soldier type, animation, texture
and sound bank, named with the game's prefix (DS2_..., GE_..., CV_..., CG_...), plus library.txt (what each file is)
and not-converted.txt. DesertStormFix reads a file from the library only when a mod names it. An interrupted build
continues where it stopped; run it again on an existing library to add what's new (--redo to
retry kinds whose converter changed). Made from YOUR copies of the games: keep it local, never share it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from conflict_ds_tools import library  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("games", nargs="*", choices=["ds2", "ge", "cv", "cg"])
    ap.add_argument("--target")
    ap.add_argument("--redo", default="", help="kinds to convert again in an existing library, comma separated: "
                    "animation, weapon, vehicle, unit, model, texture, sound cache, stream")
    a = ap.parse_args()
    for line in library.build_all(a.target, a.games or None, log=lambda s: print(s, flush=True),
                                  redo=tuple(k.strip() for k in a.redo.split(",") if k.strip())):
        print(line, flush=True)


if __name__ == "__main__":
    main()
