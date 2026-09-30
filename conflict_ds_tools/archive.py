"""Conflict: Desert Storm .dat archives (catalog.dat, chardata.dat, frontend.dat, missionN.dat, ...).

Format: directory of 12-byte entries {u32 name hash, u32 offset, u32 size} from offset 0; data from the first
entry's offset (0x8000). Only hashes are stored: hash = LFSR over the upper-cased file name (FUN_004b9a10, see
evo.archive_hash). Names are recovered from a dictionary of strings found in the game's exe/DLLs/.sch files and
in the archives themselves (mesh names inside .EVO files, texture names, text tables) combined with the
extensions the game uses. Files whose name stays unknown are written as _<HASH>.<EXT> (EXT guessed from content).
"""
import os
import re
import struct

from . import images
from .evo import archive_hash

EXTS = ["DDS", "EVO", "RFX", "PNG", "TGA", "BMP", "IMG", "FAS", "SPL", "ENV", "TXT", "INI", "PSF", "SBK", "PRS",
        "PRB", "AED", "DYN", "PI2", "PAX", "PAD", "OCT", "RDA", "RMD", "QTD", "SKL", "DAT", "CSV", "FNT", "WAV", "NAV",
        "MAP", "GZ", "TEXT", "SCH", "KEY"]


def find_game_dir():
    """Game folder from the registry (the game's own key, then Steam / GOG) or the usual install paths."""
    cands = []
    try:
        import winreg
        for root, key, val in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Pivotal Games\Conflict Desert Storm",
                                "ExePath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Pivotal Games\Conflict Desert Storm", "ExePath"),
                               (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")):
            try:
                with winreg.OpenKey(root, key) as k:
                    v = winreg.QueryValueEx(k, val)[0]
                if val == "SteamPath":
                    lib = os.path.join(v, "steamapps", "libraryfolders.vdf")
                    paths = [v]
                    if os.path.exists(lib):
                        paths += re.findall(r'"path"\s+"([^"]+)"', open(lib, encoding="utf-8", errors="replace").read())
                    cands += [os.path.join(p.replace("\\\\", "\\"), "steamapps", "common", "conflict_desert_storm")
                              for p in paths]
                else:
                    cands.append(os.path.dirname(v))
            except OSError:
                pass
    except ImportError:
        pass
    cands += [r"C:\Program Files (x86)\Steam\steamapps\common\conflict_desert_storm",
              r"C:\GOG Games\Conflict - Desert Storm", r"C:\Program Files (x86)\GOG Galaxy\Games\Conflict - Desert Storm"]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "chardata.dat")):
            return os.path.normpath(c)
    return None


def list_archives(game_dir):
    return sorted(os.path.join(game_dir, f) for f in os.listdir(game_dir)
                  if f.lower().endswith(".dat") and not f.lower().startswith("unins"))


def entries(data):
    if len(data) < 12:
        return []
    first = struct.unpack_from("<I", data, 4)[0]
    out = []
    for p in range(0, min(first, len(data)), 12):
        h, o, s = struct.unpack_from("<III", data, p)
        if (h == 0 and o == 0 and s == 0) or o + s > len(data) or o < first:
            break
        out.append((h, o, s))
    return out


def kind(b):
    m = b[:4]
    if m == b"DDS ":
        return "DDS"
    if m == b"EOBJ":
        return "EVO"
    if m == b"\x89PNG":
        return "PNG"
    if m[:2] == b"BM":
        return "BMP"
    if m == b"REFX" or m[:2] == b"//":
        return "RFX"
    if len(b) >= 32 and m == b"\x02\x00\x00\x00":
        n, bounds = struct.unpack_from("<II", b, 4)
        if 0 < n < 256 and bounds < 64:
            return "PRB"
    return "BIN"


def _strings(data):
    for m in re.finditer(rb"[\x21-\x7e]{3,}", data):
        yield m.group().decode("latin-1")
    for m in re.finditer(rb"(?:[\x21-\x7e]\x00){3,}", data):
        yield m.group().decode("utf-16-le")


def _words(data, words):
    for s in _strings(data):
        for tok in re.split(r"[\s\"'=,;()<>|]+", s):
            tok = re.split(r"[/\\]", tok.strip("@#"))[-1]
            if 2 <= len(tok) <= 64 and re.fullmatch(r"[A-Za-z0-9_\-.]+", tok):
                words.add(tok.upper())


def build_dictionary(game_dir, blobs):
    words = set()
    for f in os.listdir(game_dir):
        if f.lower().endswith((".exe", ".dll", ".sch")):
            with open(os.path.join(game_dir, f), "rb") as fh:
                _words(fh.read(), words)
    for b in blobs:
        _words(b, words)
    table = {}
    stems = set()
    for w in words:
        base, dot, ext = w.rpartition(".")
        if dot and ext in EXTS:
            table.setdefault(archive_hash(w), w)
        stems.add(base if dot else w)
    for stem in stems:
        for e in EXTS:
            table.setdefault(archive_hash(stem + "." + e), stem + "." + e)
    return table


def extract(game_dir, out_dir, archives=None, png=True, progress=None, log=print):
    """Extract archives to out_dir/<archive>/<NAME.EXT> (+ .png next to .dds/.tga when png). progress(i, n)."""
    archives = archives or list_archives(game_dir)
    datas = {}
    for p in archives:
        with open(p, "rb") as f:
            datas[p] = f.read()
    blobs = []
    evo_names = {}
    for d in datas.values():
        for h, o, s in entries(d):
            k = kind(d[o:o + 40])
            if k == "EVO":
                chunk = d[o:o + s]
                blobs.append(chunk)
                name = chunk[8:72].split(b"\0")[0].decode("latin-1").upper()
                if name and archive_hash(name + ".EVO") == h:
                    evo_names[h] = name + ".EVO"
            elif k in ("RFX", "BIN") and s < 4 << 20:
                blobs.append(d[o:o + s])
    table = build_dictionary(game_dir, blobs)
    table.update(evo_names)
    total = sum(len(entries(d)) for d in datas.values())
    done = 0
    summary = []
    for p, d in datas.items():
        arc = os.path.splitext(os.path.basename(p))[0].lower()
        dst = os.path.join(out_dir, arc)
        os.makedirs(dst, exist_ok=True)
        named = unnamed = 0
        index = []
        for h, o, s in entries(d):
            blob = d[o:o + s]
            k = kind(blob[:40])
            name = table.get(h)
            if name and (k == "BIN" or name.endswith("." + k)):   # content type must agree with the name
                named += 1
            else:
                name = "_%08X.%s" % (h, k)
                unnamed += 1
            path = os.path.join(dst, name)
            with open(path, "wb") as f:
                f.write(blob)
            index.append("%08X\t%#x\t%d\t%s" % (h, o, s, name))
            if png and name.upper().endswith((".DDS", ".TGA")):
                try:
                    w, hh, rgba = images.decode_dds(blob) if k == "DDS" else images.decode_tga(blob)
                    with open(path[:-4] + ".png", "wb") as f:
                        f.write(images.encode_png(w, hh, rgba, images.has_alpha(rgba)))
                except (images.ImageError, struct.error, IndexError, ValueError) as e:
                    log("  png failed: %s (%s)" % (name, e))
            done += 1
            if progress and done % 50 == 0:
                progress(done, total)
        with open(os.path.join(dst, "_index.tsv"), "w") as f:
            f.write("\n".join(index) + "\n")
        summary.append((arc, named, unnamed))
        log("%s: %d named, %d unnamed" % (arc, named, unnamed))
    if progress:
        progress(total, total)
    return summary
