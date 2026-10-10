"""A blank scene for a brand-new level: a shipped scene's environment without its world.

Kept from the base scene: the header (map image), the squad's start objects (by unit name), the sun (DLGT), fog, sky
layers, the four colour keys, time of day, glass pane types and the section ends the loader needs. Emptied: world
pieces and props, every other object, portals, point / spot lights, ambient sounds, waypoints, route markers, regions
(count 0), emitters, shrubs (the empty block the training levels have), cameras. ``extra_envi`` lines (ground patches,
props) go into the world section.
"""
from __future__ import annotations

from .env import EnvFile

SQUAD = ("Bradley", "Foley", "Jones", "Connors")
_EMPTY_SHRUBS = ["SHRUBS", "0 0", "1 0 51", ""] + ["0.000000 0 "] * 51 + ["ENDGARDEN", "ENDSHRUBS"]
_DROP_SECTIONS = {"world", "rinfo", "portals", "sounds", "waypoints", "regions", "emit", "cameras"}
_KEEP_KINDS = {"ENDI", "HLOX", "ENDP", "ENDL", "ENDENVS", "ENDWAYPTS", "ENDMARKERS", "ENDREGIONS", "ENDC",
               "<EMIT>", "</EMIT>"}


def blank_lines(base: EnvFile, squad=SQUAD, extra_envi=(), extra_hloi=()) -> list[str]:
    by_line = {r.index: r for r in base.records}
    for r in base.records:
        for c in r.children:
            by_line[c.index] = c
    out, i, n = [], 0, len(base.lines)
    while i < n:
        line = base.lines[i]
        r = by_line.get(i)
        if r is None:                                  # text outside any record (blank lines)
            out.append(line); i += 1; continue
        kind, sec = r.kind, r.section
        if kind == "SHRUBS" or (sec == "shrubs" and kind == ""):
            # replace the whole block up to ENDSHRUBS with the empty one
            while i < n and base.lines[i].strip() != "ENDSHRUBS":
                i += 1
            out.extend(_EMPTY_SHRUBS); i += 1; continue
        if kind in ("ENDGARDEN", "ENDSHRUBS"):
            i += 1; continue
        if kind == "ENDI":
            out.extend(extra_envi)
        if kind == "HLOX":
            out.extend(extra_hloi)
        if kind in _KEEP_KINDS:
            out.append(line)
        elif kind == "NUMSWITCHES":                    # switches open doors the new level doesn't have
            out.append("NUMSWITCHES 0")
        elif kind == "SWITCH":
            pass
        elif sec == "markers" and kind == "":          # the region count that precedes the REGN lines
            out.append("0")
        elif sec in _DROP_SECTIONS or kind in ("NEWENVMARKER", "PORT", "ENVSND", "NEWENVWAYPT", "REGN", "RINFO",
                                                "SLGT", "PLGT", "VAMI", "ANMI"):
            pass
        elif kind == "HLOI":
            if r.fields and str(r.get("unit")) in squad and not (r["flags"] & 0x20000):
                out.append(line)
        elif sec == "emit":
            pass
        elif kind.startswith("CAM"):
            pass
        else:
            out.append(line)
        i += 1
    return out
