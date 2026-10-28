"""A game file by name from the player's archives (*.dat next to DesertStorm.exe; catalog / chardata first)."""
import glob, os, struct


def archive_hash(name):
    """FUN_004b9a10 over the upper-cased bare name: LFSR start 1, bits LSB-first."""
    h = 1
    for c in os.path.basename(name).upper().encode("latin-1"):
        for b in range(8):
            i = ((c >> b) & 1) ^ (h & 1) ^ ((h >> 1) & 1) ^ ((h >> 21) & 1) ^ ((h >> 31) & 1)
            h = ((h << 1) | i) & 0xFFFFFFFF
    return h


def read_game_file(game, name):
    want = archive_hash(name)
    # the game folder + PSE\Originals (DesertStormFix moves the original archives there)
    found = {}
    for p in glob.glob(os.path.join(game, "PSE", "Originals", "*.dat")) + glob.glob(os.path.join(game, "*.dat")):
        found[os.path.basename(p).lower()] = p
    dats = sorted(found.values(), key=lambda p: (os.path.basename(p).lower() not in ("catalog.dat", "chardata.dat"),
                                                 os.path.basename(p).lower()))
    for dat in dats:
        with open(dat, "rb") as f:
            head = f.read(0x8000)
            for i in range(0, len(head) - 11, 12):
                h, off, size = struct.unpack_from("<3I", head, i)
                if not h and not off and not size:
                    break
                if h == want:
                    f.seek(off)
                    return f.read(size)
    raise FileNotFoundError(name + " is in no game archive")
