r"""Better sound effects for the PC game from your own GameCube disc.

  python ds_gc_audio.py <gamecube image> [-g <PC game folder>] [-o <mod folder>] [--levels mission1,mission3]
                        [--all] [--list]

The GameCube version stores every sound effect and voice line as uncompressed 16-bit audio, and 285 of Mission 1's
828 at a higher sample rate than the PC (11 kHz -> 20 or 44.1 kHz, 5.7 kHz -> 14 kHz ...). This reads the level
sound caches from a GameCube disc image you own (.iso / .gcm, or .ciso), takes each sample that has a higher rate
there than in your PC game, encodes it in the PC's ADPCM format at that rate and writes one file per level
(<mod folder>\MISSION1.SCH ...). DesertStormFix uses a mod .sch named like a level's sound cache to replace that
level's samples (same id and language); everything else in the level stays as it is.

Default mod folder: <PC game folder>\Mods\GameCubeAudio. Turn it off in the in-game MODS menu like any mod.
--all also re-encodes samples whose rate is the same (no audible gain: the PC can only play 4-bit ADPCM, so the
cleaner GameCube source ends up with the same ADPCM noise). --list prints the comparison without writing anything.
Nothing from the disc is shipped with DesertStormFix - every player converts their own copy.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools import archive, gcaudio  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="GameCube disc image (.iso / .gcm / .ciso)")
    ap.add_argument("-g", "--game", help="PC game folder (default: found in the registry)")
    ap.add_argument("-o", "--out", help="mod folder (default: <game>\\Mods\\GameCubeAudio)")
    ap.add_argument("--levels", help="comma-separated level names (default: all)")
    ap.add_argument("--all", action="store_true", help="also replace samples with the same rate")
    ap.add_argument("--list", action="store_true", help="only print what would change")
    a = ap.parse_args()

    game = a.game or archive.find_game_dir()
    if not game or not os.path.isfile(os.path.join(game, "DesertStorm.exe")):
        sys.exit("PC game folder not found - pass -g <folder with DesertStorm.exe>")
    r = gcaudio.convert(a.image, game, a.out, a.levels.split(",") if a.levels else None, a.all, a.list)
    if not a.list:
        print(f"wrote {r['out']}: {r['written'] / 1e6:.1f} MB of replacement samples, {r['encoded']} encoded, "
              f"{r['seconds']:.0f} s")


if __name__ == "__main__":
    main()
