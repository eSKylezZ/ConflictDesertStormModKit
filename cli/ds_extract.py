"""Extract the Conflict: Desert Storm .dat archives (meshes, textures, animations, tables).

  python ds_extract.py [-g GAME_DIR] [-o OUT_DIR] [--no-png] [archive names ...]

GAME_DIR defaults to the installed game (registry: the game's key / Steam library / GOG). Output:
OUT_DIR/<archive>/<NAME.EXT>, PNG copies of DDS/TGA textures (skip with --no-png), and _index.tsv per archive.
Names the game only stores as hashes are recovered from its own files; the rest are _<HASH>.<EXT>.
Pure Python (numpy speeds up the PNG conversion when installed).
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools import archive  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archives", nargs="*", help="archive names or paths (default: all), e.g. chardata mission1")
    ap.add_argument("-g", "--game", help="game folder (default: found from the registry)")
    ap.add_argument("-o", "--out", default="extracted", help="output folder (default: ./extracted)")
    ap.add_argument("--no-png", action="store_true", help="don't write PNG copies of DDS/TGA textures")
    a = ap.parse_args()
    game = a.game or archive.find_game_dir()
    if not game or not os.path.isdir(game):
        sys.exit("game folder not found - pass it with -g")
    paths = []
    for n in a.archives:
        p = n if os.path.exists(n) else os.path.join(game, n if n.lower().endswith(".dat") else n + ".dat")
        if not os.path.exists(p):
            sys.exit("no archive " + p)
        paths.append(p)
    print("game:", game)
    t = time.time()
    last = [0.0]

    def progress(frac, text):
        if time.time() - last[0] > 2 or frac >= 1.0:
            last[0] = time.time()
            print("  %3d%%  %s" % (frac * 100, text), flush=True)

    summary = archive.extract(game, a.out, paths or None, not a.no_png, progress)
    named = sum(s[1] for s in summary)
    total = named + sum(s[2] for s in summary)
    print("done in %.0f s: %d files, %d named -> %s" % (time.time() - t, total, named, os.path.abspath(a.out)))


if __name__ == "__main__":
    main()
