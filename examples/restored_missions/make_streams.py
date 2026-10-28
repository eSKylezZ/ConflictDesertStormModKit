"""RestoredMissions/TRAINING1_STREAMS.sch: the sound cache entries (PFST) for Training 1's cut intro film.

    python examples/restored_missions/make_streams.py [-g <game folder>]

CS_T01_S01.psf / CS_T01_S02.psf are on the disc (Data\\Sound\\<language>\\) but in no level's sound cache, so the game
can't play them; DesertStormFix adds a mod .sch's missing entries to every level's cache. An entry is only data about
the file - language, name hash, length, rate, path; no sound - modelled on the game's own CS_M01_S01 entry in
mission1.sch. Run this again only if the entries need to change: the mod ships the .sch it writes. The mission logic is
in the .patch.lua files (mission patches, DesertStormFix 3.3+).
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from conflict_ds_tools import archive, games, sound  # noqa: E402


def stream_pack(game_dir):
    model = None
    with open(games.game_file(game_dir, "mission1.sch"), "rb") as f:
        for tag, c in sound.chunks(f.read()):
            if tag == b"PFST":
                model = c
                break
    if model is None:
        raise SystemExit("mission1.sch has no stream entry to copy")
    _lang, _id, _n, rate, bits, _pl = struct.unpack_from("<iIIHBB", model, 8)
    out = []
    langs = {"English": 0, "French": 1, "German": 2, "Spanish": 3, "Italian": 4, "American": 6}
    for folder, lang in langs.items():
        for stem in ("CS_T01_S01", "CS_T01_S02"):
            disk = os.path.join(game_dir, "Data", "Sound", folder, stem + ".psf")
            if not os.path.exists(disk):
                continue
            path = ("data\\sound\\%s\\%s.psf" % (folder, stem)).encode("latin-1") + b"\0"
            per_channel = (os.path.getsize(disk) - 8) // 2   # "PSF\x80" + u32, then both channels' blocks
            body = struct.pack("<iIIHBB", lang, sound.elf_hash(stem + ".psf"), per_channel, rate, bits, len(path)) + path
            out.append(b"PFST" + struct.pack("<I", len(body)) + body)
    if not out:
        raise SystemExit("CS_T01_S01.psf / CS_T01_S02.psf not found under Data\\Sound")
    return sound.build(out), len(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-g", "--game", help="game folder (default: found through the registry)")
    a = ap.parse_args()
    game_dir = a.game or archive.find_game_dir()
    pack, n = stream_pack(game_dir)
    out = os.path.join(HERE, "RestoredMissions", "TRAINING1_STREAMS.sch")
    with open(out, "wb") as f:
        f.write(pack)
    print("%s: %d stream entries" % (out, n))


if __name__ == "__main__":
    main()
