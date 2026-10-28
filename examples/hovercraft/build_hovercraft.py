r"""build_hovercraft.py <out folder> [--install] [--game DIR] [handling options] - the Hovercraft example mod.

Everything is generated from the player's own game files (no game data shipped):
  HOVERCRAFT.evo / HOVER_PAD.evo / HOVERCRAFT01.dds   make_model.py (the Humvee's object tree, new hull)
  Hovercraft.sch                                      make_sound.py (engine bank "Hovercraft": fan, turbine, air)
  HOVERCRAFT.DYN                                      the Humvee's .DYN with hover handling (below), wheels -> HOVER_PAD
  Hovercraft.vehicle                                  id HOVERCRAFT, based on the Humvee, model HOVERCRAFT, engine sound

Handling = the .DYN (DesertStormFix research/custom_units.md has the field list): tyre slip per speed, front / rear
(Humvee 0.02 / 0.05 - higher = the craft slides sideways and drifts through turns), suspension travel (cm, Humvee 5)
and damping ratio (Humvee 0.5 - lower = bobs on the cushion), max steer (rad, Humvee 1.22)."""
import argparse, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gamefiles import read_game_file  # noqa: E402

def _default_game():
    """The installed Desert Storm (env CONFLICT_DS_GAME, else registry / Steam libraries / usual paths)."""
    kit = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if kit not in sys.path:
        sys.path.insert(0, kit)
    from conflict_ds_tools import games
    return os.environ.get("CONFLICT_DS_GAME") or games.find_game_dir(games.GAMES["ds1"])


ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--game", default=_default_game())
ap.add_argument("--install", action="store_true", help="copy the mod to <game>\\Mods\\Hovercraft")
ap.add_argument("--slip-front", type=float, default=0.6)
ap.add_argument("--slip-rear", type=float, default=0.6)
ap.add_argument("--travel", type=float, default=12.0)
ap.add_argument("--damping", type=float, default=0.3)
ap.add_argument("--steer", type=float, default=0.7)
ap.add_argument("--com-y", type=float, default=-40.0, help="centre of mass height offset (cm; negative = lower, steadier)")
ap.add_argument("--grip", type=float, default=0.6, help="tyre friction (DesertStormFix 3.2+; game vehicles: unlimited) - low = slides")
ap.add_argument("--spin", type=float, default=30.0, help="spin damping (DesertStormFix 3.2+; the game's 0.2) - stops spinning on; < 0 = leave out")
ap.add_argument("--skip-art", action="store_true", help="only rewrite the .DYN and .vehicle (art already built)")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

if not args.skip_art:
    for script in ("make_model.py", "make_sound.py"):
        subprocess.run([sys.executable, os.path.join(HERE, script), args.out, "--game", args.game], check=True)
    for f in os.listdir(args.out):
        if f.endswith(".wav"):
            os.remove(os.path.join(args.out, f))   # previews only

# .DYN: token order as the game reads it (2 header numbers, then the struct; see research/custom_units.md)
dyn = read_game_file(args.game, "HUMVEE.DYN").decode("latin-1").split("\n")
rows = [l.split() for l in dyn]
flat = [(r, c) for r, row in enumerate(rows) for c in range(len(row))]
set_ = {3: args.steer, 10: args.com_y, 18: args.travel, 20: args.damping, 23: args.slip_front, 25: args.slip_rear}
for n, v in set_.items():
    r, c = flat[n]
    rows[r][c] = "%f" % v
for row in rows:
    if row and row[-1].upper() in ("HUMVEERIGHTWHEEL", "HUMVEELEFTWHEEL"):
        row[-1] = "HOVER_PAD"
open(os.path.join(args.out, "HOVERCRAFT.DYN"), "w", newline="\r\n").write("\n".join(" ".join(r) for r in rows).rstrip() + "\n")

open(os.path.join(args.out, "Hovercraft.vehicle"), "w").write(
    "# A hovercraft: the Humvee's seats, exits and gun mount on a new hull, hover handling (HOVERCRAFT.DYN),\n"
    "# its own engine sound (Hovercraft.sch). Placed in missions with the editor (unit HOVERCRAFT), or picked on the\n"
    "# briefing's MISSION tab where the squad has a vehicle.\n"
    "id           = HOVERCRAFT\n"
    "based on     = US_VEH_Hum_Vee\n"
    "name         = Hovercraft\n"
    "model        = HOVERCRAFT\n"
    "engine sound = Hovercraft\n"
    "faction      = US\n"
    "grip         = %g\n" % args.grip
    + ("spin damping = %g\n" % args.spin if args.spin >= 0 else ""))
print("handling:", {k: v for k, v in vars(args).items() if k in ("slip_front", "slip_rear", "travel", "damping", "steer", "com_y", "grip", "spin")})
if args.install:
    dest = os.path.join(args.game, "Mods", "Hovercraft")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(args.out, dest)
    print("installed", dest)
print("files:", sorted(os.listdir(args.out)))
