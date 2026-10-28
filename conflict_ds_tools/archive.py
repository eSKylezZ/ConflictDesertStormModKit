"""Conflict .dat archives (catalog.dat, chardata.dat, frontend.dat, missionN.dat, ...) - the same in every game.

Format: directory of 12-byte entries {u32 name hash, u32 offset, u32 size} from offset 0; data from the first
entry's offset (0x8000; 0xC000 in Vietnam's / Global Storm's bigger archives). Only hashes are stored: hash = LFSR
over the file name, upper-cased in Desert Storm (FUN_004b9a10), lower-cased from Desert Storm II on (see
evo.archive_hash). Names are recovered from a dictionary of strings found in the game's exe/DLLs/.sch files and
in the archives themselves (mesh names inside .EVO files, texture names, text tables) combined with the
extensions the game uses. Files whose name stays unknown are written as _<HASH>.<EXT> (EXT guessed from content).
"""
import os
import re
import struct

from . import games, images
from .evo import archive_hash, is_ani_cg

EXTS = ["DDS", "EVO", "RFX", "PNG", "TGA", "BMP", "IMG", "FAS", "SPL", "ENV", "TXT", "INI", "PSF", "SBK", "PRS",
        "PRB", "AED", "DYN", "PI2", "PAX", "PAD", "OCT", "RDA", "RMD", "QTD", "SKL", "DAT", "CSV", "FNT", "WAV", "NAV",
        "MAP", "GZ", "TEXT", "SCH", "KEY",
        # Global Storm: split scenes (.ENV index -> ART / DES / SI / SND / RDA), editor movies, key bindings, text, shaders
        "ART", "DES", "SI", "SND", "MVS", "C4K", "LOC", "FX", "PCR", "BIK", "XML", "PHY", "LIT", "SKY"]


def find_game_dir(game=None):
    """The folder of `game` (default: the current one, games.current()): see games.find_game_dir."""
    return games.find_game_dir(game)


def list_archives(game_dir):
    # the game folder + PSE\Originals (DesertStormFix moves the originals there)
    return [p for p in games.game_files(game_dir, exts=(".dat",)) if not os.path.basename(p).lower().startswith("unins")]


class GameFiles:
    """Single files out of the game's .dat archives by name (the first archive that has it)."""

    def __init__(self, game_dir):
        self.dir = game_dir
        self.index = {}
        for path in list_archives(game_dir):
            with open(path, "rb") as f:
                head = read_head(f)
            for h, o, s in entries_head(head):
                self.index.setdefault(h, (path, o, s))

    def get(self, name):
        hit = self.index.get(archive_hash(os.path.basename(name)))
        if hit is None:
            return None
        path, o, s = hit
        with open(path, "rb") as f:
            f.seek(o)
            return f.read(s)


def read_head(f):
    """The directory block of an open archive: everything before the first entry's data (0x8000 in DS1 / DS2,
    0xC000 in Vietnam's / Global Storm's bigger archives)."""
    start = f.tell()
    first = f.read(12)
    if len(first) < 12:
        return first
    size = struct.unpack_from("<I", first, 4)[0]
    f.seek(start)
    return f.read(max(12, min(size, 1 << 22)))


def entries_head(head):
    """The index of an archive from its directory block (read_head)."""
    if len(head) < 12:
        return []
    first = struct.unpack_from("<I", head, 4)[0]
    out = []
    for p in range(0, min(first, len(head)), 12):
        h, o, s = struct.unpack_from("<III", head, p)
        if h == o == s == 0:
            break
        out.append((h, o, s))
    return out


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


# catalog tables every game loads by name; seeded because a protected exe (Global Storm's) doesn't show them
TABLE_NAMES = ("WEAPS.TXT", "INVEN.TXT", "UNITWPNS.TXT", "VEHWPNS.TXT", "VEHICLE.TXT", "UNITS.TXT", "HSTAT.TXT",
               "VSTAT.TXT", "PROPS.TXT", "DOORS.TXT", "FEATSTAT.TXT", "MPSKINITEMS.TXT", "SWITCHES.TXT",
               "CONNOBJS.TXT", "FNAMES.TXT", "SNAMES.TXT", "STRUCTS.TXT", "TARGETS.TXT", "TRAPS.TXT", "SCORES.TXT")


def build_dictionary(game_dir, blobs, targets=None, progress=None):
    """{hash: NAME.EXT} for words found in the game's binaries and in archive contents (plus TABLE_NAMES), combined
    with every known extension; only hashes in targets are kept (all when None). progress(fraction) while working."""
    words = set(TABLE_NAMES)
    bins = games.game_files(game_dir, exts=(".exe", ".dll", ".sch"))
    sources = len(bins) + len(blobs)
    for i, f in enumerate(bins):
        with open(f, "rb") as fh:
            _words(fh.read(), words)
        if progress:
            progress(0.5 * i / sources)
    for i, b in enumerate(blobs):
        _words(b, words)
        if progress and i % 200 == 0:
            progress(0.5 * (len(bins) + i) / sources)
    table = {}
    stems = set()
    for w in words:
        base, dot, ext = w.rpartition(".")
        if dot and ext in EXTS:
            h = archive_hash(w)
            if targets is None or h in targets:
                table.setdefault(h, w)
        stems.add(base if dot else w)
    exts = [e.upper() for e in EXTS]
    for n, stem in enumerate(stems):
        if progress and n % 5000 == 0:
            progress(0.5 + 0.5 * n / max(1, len(stems)))
        base = archive_hash(stem + ".")          # hash the stem once, continue per extension
        for e in exts:
            h = archive_hash(e, base)
            if (targets is None or h in targets) and h not in table:
                table[h] = stem + "." + e
    return table


def _do_package_name(blob, h):
    """<NAME>.APAK of a Denied Ops package when the archive index hash agrees, else None."""
    from . import apkg
    name = apkg.package_name(blob)
    if not name:
        return None
    full = name.upper() + apkg.PACKAGE_EXT
    return full if apkg.name_hash(full) == h else None


class Cancelled(Exception):
    pass


def extract(game_dir, out_dir, archives=None, png=True, progress=None, log=print, cancel=None):
    """Extract archives to out_dir/<archive>/<NAME.EXT> (+ .png next to .dds/.tga when png).
    progress(fraction 0..1, text) reports the stage; cancel() returning True stops with Cancelled.
    Safe to run in a worker thread (no Blender calls). Returns [(archive, named, unnamed)]."""
    def report(frac, text):
        if progress:
            progress(min(1.0, max(0.0, frac)), text)
        if cancel and cancel():
            raise Cancelled()

    game = games.use_folder(game_dir) or games.current()    # its exe decides the name hash
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, ".game"), "w", encoding="utf-8") as f:   # games.detect_extracted reads it
        f.write(game.id)
    archives = archives or list_archives(game_dir)
    report(0.0, "Reading archives")
    datas = {}
    for p in archives:
        with open(p, "rb") as f:
            datas[p] = f.read()
    report(0.01, "Scanning archive contents")
    blobs = []
    evo_names = {}
    targets = set()
    for d in datas.values():
        for h, o, s in entries(d):
            targets.add(h)
            k = kind(d[o:o + 40])
            if k == "EVO":
                chunk = d[o:o + s]
                blobs.append(chunk)
                name = chunk[8:72].split(b"\0")[0].decode("latin-1").upper()
                if name and archive_hash(name + ".EVO") == h:
                    evo_names[h] = name + ".EVO"
            elif k in ("RFX", "BIN") and s < 4 << 20:
                blobs.append(d[o:o + s])
    # name recovery is roughly a third of the run (with PNGs), writing the files the rest
    report(0.02, "Recovering file names")
    table = build_dictionary(game_dir, blobs, targets,
                             lambda f: report(0.02 + 0.33 * f, "Recovering file names  (%d%%)" % (f * 100)))
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
            elif game.id == "do" and blob[:4] == b"APKG" and _do_package_name(blob, h):
                # Denied Ops asset package: the archive names it <NAME>.apak after the name in its own manifest
                name = _do_package_name(blob, h)
                named += 1
            elif game.id == "cg" and k == "BIN" and is_ani_cg(blob):
                # Global Storm animation: no name in the index (the hash isn't of "<NAME>.ANI"), the name is inside;
                # written as <NAME>.ANI like the game's Animations\<name>.ani and the add-on's export
                name = blob[:44].split(b"\0")[0].decode("latin-1").upper() + ".ANI"
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
            if done % 25 == 0:
                report(0.35 + 0.65 * done / total, "Extracting %s  (%d / %d files)" % (arc, done, total))
        with open(os.path.join(dst, "_index.tsv"), "w") as f:
            f.write("\n".join(index) + "\n")
        summary.append((arc, named, unnamed))
        log("%s: %d named, %d unnamed" % (arc, named, unnamed))
    if game.id == "do":       # Denied Ops: models, textures and materials are inside APKG packages
        from . import apkg
        c = apkg.unpack_extracted(out_dir, lambda f, t: report(0.98 + 0.02 * f, t))
        log("asset packages: %(models)d models, %(textures)d textures, %(materials)d materials" % c)
    report(1.0, "Done: %d files" % total)
    return summary
