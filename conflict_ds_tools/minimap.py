"""The in-game tactical map (MISSION tab, pause screen): image 0 of a one-page sheet "GW<name>.img" (API +0x4C,
game.load_map_image) stretched over the level bounds (API +0x54, game.set_level_bounds): u = (x - min x) / width,
v = 1 - (z - min z) / depth - so the picture's left edge is min x and its top edge max z.

Every shipped sheet is 300 bytes: "imgf", u16 1, u16 1 page, u16 0x20, u16 1 image, page file name char[256], one
32-byte image entry (u16 page, zeros, then i16 x, y, w, h, offset x, offset y at +0x14). Shared by the level editor
(editor/studio.py) and the Blender add-on's minimap bake.
"""
from __future__ import annotations

import re
import struct

IMG_HEADER = b"imgf" + struct.pack("<HHHH", 1, 1, 0x20, 1)
MAP_SIZE = 256               # every shipped GWMission sheet is one 256 x 256 page

# game.set_level_bounds of the shipped missions (their DLLs' API +0x54 calls): ((min x, min z), (max x, max z)), cm
LEVEL_BOUNDS = {
    "MISSION1": ((-45802.4, 15471.6), (8482.3, 70181.8)),
    "MISSION2": ((-19536.9, -25581.6), (22293.1, 16299.0)),
    "MISSION3": ((21326.5, 25388.9), (74382.0, 78577.2)),
    "MISSION4": ((-15680.6, -20302.3), (26115.3, 20747.0)),
    "MISSION5A": ((-13119.3, -18585.0), (29367.6, 24736.4)),
    "MISSION5B": ((-35559.6, -18686.7), (7852.1, 24882.6)),
    "MISSION6": ((-13159.6, -11758.9), (9608.3, 10877.6)),
    "MISSION7": ((-13367.2, -12784.3), (10181.7, 10745.9)),
    "MISSION8": ((-25426.4, -25544.4), (25864.3, 25450.3)),
    "MISSION9": ((-39533.4, -28532.4), (28940.1, 39910.4)),
    "MISSION10": ((-19378.4, -20911.5), (21890.3, 20555.8)),
    "MISSION11A": ((-10802.5, -12700.6), (16804.0, 14854.3)),
    "MISSION11B": ((-12501.9, -7666.3), (7679.8, 12396.9)),
    "MISSION12A": ((-18817.3, -19294.8), (18605.7, 17760.7)),
    "MISSION12B": ((-7404.1, -9395.8), (10065.2, 8203.4)),
}


def sheet(page_name, size=MAP_SIZE):
    """The 300-byte GW<name>.img sheet: one page (page_name, a PNG next to it), image 0 = the whole page."""
    entry = bytearray(32)
    struct.pack_into("<6h", entry, 0x14, 0, 0, size, size, -size // 2, -size // 2)
    return IMG_HEADER + page_name.encode("latin-1").ljust(256, b"\0") + bytes(entry)


def map_names(name):
    """(sheet file, page file) for a map name: GW<name>.img, GW<name>0.png."""
    return f"GW{name}.img", f"GW{name}0.png"


def pixel_aspect(width, depth):
    """(x, y) render pixel aspect that stretches a width x depth area onto a square picture (both >= 1, as Blender
    wants); the camera's ortho scale then covers the larger side."""
    if width <= 0 or depth <= 0:
        return 1.0, 1.0
    return (width / depth, 1.0) if width >= depth else (1.0, depth / width)


BEGIN, END = "-- @editor begin: minimap", "-- @editor end: minimap"
_HOOK = "    editor_minimap()   -- the editor's tactical map image (Generate Minimap)"


def script_with_map(text, img):
    """The mission script loading `img` as its map: a set-up that loads a map gets the new name; a script on a base
    mission DLL (which loads the shipped map itself) gets an editor_minimap() block called from init().
    Returns (text, how)."""
    own = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)
    pat = re.compile(r'^(\s*game\.load_map_image\()"[^"]*"(\))', re.M)
    if pat.search(own):
        return pat.sub(lambda m: f'{m.group(1)}"{img}"{m.group(2)}', text), "set-up"
    block = (f"{BEGIN}\nfunction editor_minimap()   -- called from init(): replaces the base mission's map\n"
             f'    game.load_map_image("{img}")\nend\n{END}\n')
    if BEGIN in text:
        text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", lambda _m: block, text, flags=re.S)
    else:
        m = re.search(r"^function init\(\)", text, re.M)
        at = m.start() if m else len(text)
        text = text[:at] + block + "\n" + text[at:]
    if not re.search(r"^\s*editor_minimap\(\)", text, re.M):
        m = re.search(r"^function init\(\)[^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + _HOOK + "\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\nfunction init()\n" + _HOOK + "\nend\n"
    return text, "editor_minimap()"
