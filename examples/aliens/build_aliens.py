r"""build_aliens.py <out folder> [--install] [--game DIR] - the Aliens example mod: three alien enemy types.

Generated from the player's own game files (no game data shipped):
  ALIEN_DRONE / ALIEN_WARRIOR / ALIEN_SNIPER .evo + .dds   make_body.py (a soldier body reshaped on the standard
                                                          skeleton: elongated skull, big black eyes, thin limbs)
  ALIEN_PULSE_RIFLE / NEEDLE_RIFLE / PLASMA_CASTER        make_weapons.py (.weapon + model + picture + shot WAVs)
  AlienDrone / AlienWarrior / AlienSniper .sch            make_voice.py (speech banks: chirps, screeches, growls)
  Alien_Drone / Alien_Warrior / Alien_Sniper .unit        soldier types (DesertStormFix 3.2+), enemies (Iraqi side)
Place them in missions with the editor (unit Alien_Drone ...) on the enemy team."""
import argparse, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
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
ap.add_argument("--install", action="store_true", help="copy the mod to <game>\\Mods\\Aliens")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)


def run(script, *a):
    subprocess.run([sys.executable, os.path.join(HERE, script), args.out, "--game", args.game, *a], check=True)


UNITS = [
    # id, based on, body kind, health, experience, voice (bank, pitch), weapon
    ("Alien_Drone", "Iraqi_Conscript", "drone", 60, "Green", ("AlienDrone", 1.0), "ALIEN_PULSE_RIFLE"),
    ("Alien_Warrior", "Iraqi_Republican", "warrior", 130, "Regular", ("AlienWarrior", 0.6), "ALIEN_PLASMA_CASTER"),
    ("Alien_Sniper", "Iraqi_Republican", "sniper", 50, "Veteran", ("AlienSniper", 1.5), "ALIEN_NEEDLE_RIFLE"),
]
for uid, base, kind, health, exp, (bank, pitch), weapon in UNITS:
    run("make_body.py", "--kind", kind)
    run("make_voice.py", "--bank", bank, "--pitch", str(pitch))
    with open(os.path.join(args.out, uid + ".unit"), "w") as f:
        f.write(f"# An alien enemy (examples/aliens): body ALIEN_{kind.upper()}, own voice and gun.\n"
                f"id         = {uid}\n"
                f"based on   = {base}\n"
                f"model      = ALIEN_{kind.upper()}\n"
                f"health     = {health}\n"
                f"experience = {exp}\n"
                f"voice      = {bank}\n"
                f"item       = {weapon}\n")
run("make_weapons.py")
for f in os.listdir(args.out):   # previews only
    if f.endswith((".wav",)) and not f.startswith("ALIEN_") or f.endswith("_texture.png"):
        os.remove(os.path.join(args.out, f))
if args.install:
    dest = os.path.join(args.game, "Mods", "Aliens")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(args.out, dest)
    print("installed", dest)
print("files:", len(os.listdir(args.out)))
