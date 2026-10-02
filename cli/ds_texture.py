"""Textures for Conflict: Desert Storm mods: PNG / TGA -> the game's .dds, and .dds -> PNG to edit.

  python ds_texture.py dds <in.png> [out.dds] [--format DXT1|DXT3|DXT5]   for a mod folder
  python ds_texture.py png <in.dds> [out.png]                              to edit in any paint program
  python ds_texture.py icon <weapon id> [out.png] [-g game folder]         a weapon's HUD / inventory picture

dds: DXT1 for plain textures, DXT5 when the picture has transparency (picked automatically), DXT3 for the
64 x 64 soldier-panel portraits (--format DXT3). Sizes must be powers of two (64, 128, 256, 512); keep the size of
the texture you replace - the game runs out of texture memory with bigger ones.

icon: the picture the HUD weapon panel, the inventory and pick-ups show for a weapon (e.g. US_WPN_SAW_lightmg, or a
picture name such as IMAGE_WEAPON_SAW), cut out of the game's HUD sheet - a starting point for a .weapon's "hud icon".
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import struct  # noqa: E402

from conflict_ds_tools import archive, images  # noqa: E402


def power_of_two(n):
    return n > 0 and n & (n - 1) == 0


def cmd_dds(a):
    w, h, rgba = images.decode_file(a.input)
    if not (power_of_two(w) and power_of_two(h)):
        raise SystemExit(f"{a.input} is {w} x {h}: the game needs powers of two (64, 128, 256, 512 ...)")
    if w > 1024 or h > 1024:
        print(f"note: {w} x {h} is bigger than any of the game's own textures - it may not load")
    fmt = a.format.upper() if a.format else None
    data = images.encode_dds(w, h, rgba, fmt=fmt)
    out = a.output or os.path.splitext(a.input)[0] + ".dds"
    with open(out, "wb") as f:
        f.write(data)
    print(f"{out}: {w} x {h} {data[84:88].decode()}")


def cmd_png(a):
    w, h, rgba = images.decode_file(a.input)
    out = a.output or os.path.splitext(a.input)[0] + ".png"
    with open(out, "wb") as f:
        f.write(images.encode_png(w, h, rgba, images.has_alpha(rgba)))
    print(f"{out}: {w} x {h}")


def cmd_icon(a):
    game_dir = a.game or archive.find_game_dir()
    if not game_dir:
        raise SystemExit("game not found - pass -g <game folder>")
    files = archive.GameFiles(game_dir)
    picture = a.weapon
    if not picture.upper().startswith("IMAGE_"):  # a weapon id: its picture is column 5 of Weaps.txt
        table = (files.get("WEAPS.TXT") or b"").decode("latin-1")
        rows = [r.split(",") for r in table.splitlines()]
        row = next((r for r in rows if r and r[0].lower() == a.weapon.lower()), None)
        if not row or len(row) < 5:
            raise SystemExit(f"{a.weapon} is not a weapon of the game (Weaps.txt)")
        picture = row[4]
    img = files.get("GWINT.IMG")
    for i in range((len(img) - 284) // 272):
        o = 284 + i * 272
        if img[o + 2:o + 258].partition(bytes(1))[0].decode("latin-1").lower() == picture.lower():
            page = struct.unpack_from("<H", img, o)[0]
            x, y, w, h = struct.unpack_from("<4H", img, o + 260)
            pw, ph, px = images.decode_png(files.get("GWINT%d.PNG" % page))
            out = bytearray()
            for row in range(y, y + h):
                out += px[(row * pw + x) * 4:(row * pw + x + w) * 4]
            path = a.output or picture + ".png"
            with open(path, "wb") as f:
                f.write(images.encode_png(w, h, bytes(out), True))
            print(f"{path}: {w} x {h} ({picture})")
            return
    raise SystemExit(f"no picture {picture} on the HUD sheet")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("dds", help="PNG / TGA -> .dds")
    s.add_argument("input")
    s.add_argument("output", nargs="?")
    s.add_argument("--format", choices=["DXT1", "DXT3", "DXT5", "dxt1", "dxt3", "dxt5"])
    s.set_defaults(fn=cmd_dds)
    s = sub.add_parser("png", help=".dds / TGA -> PNG")
    s.add_argument("input")
    s.add_argument("output", nargs="?")
    s.set_defaults(fn=cmd_png)
    s = sub.add_parser("icon", help="a weapon's HUD / inventory picture -> PNG")
    s.add_argument("weapon")
    s.add_argument("output", nargs="?")
    s.add_argument("-g", "--game", help="game folder (default: found from the registry)")
    s.set_defaults(fn=cmd_icon)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
