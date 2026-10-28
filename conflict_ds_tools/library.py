"""The asset library: everything of the other Conflict games converted for Desert Storm, for modders.

Every model, character, vehicle, weapon, soldier type, animation, texture and sound bank of a source game (Desert
Storm II, The Great Escape, Vietnam, Global Storm - Denied Ops needs its own readers first) converted from the
player's own copy into <Desert Storm>\\PSE\\Library\\<id>, each file named with the game's prefix (DS2_T72M1.EVO).

DesertStormFix doesn't load the library at start-up (mods.cpp LibraryFind): a file is read from it only when something
asks for that name - a mod's scene, its .weapon / .vehicle / .unit (copy one out of the library into the mod and change
it), a table row - so the library never changes a level nobody built on it. Sound banks come as bank packs
(<PREFIX><BANK>.SCH) whose samples are renumbered into the game's own range (SAMPLE_BASE: Desert Storm's sample ids
are ELF hashes, always under 0x10000000), so they can't clash with Desert Storm's sounds or another game's.

library.txt (tab separated: file, kind, source name, note) lists every file. The mission maps are a separate thing
(levelport.convert_maps -> PSE\\Ports\\<id>: playable empty levels).

Nothing of a source game is shipped: every player builds the library from their own copies.
"""
from __future__ import annotations

import os
import pickle
import re
import shutil
import struct
import tempfile
import time
import traceback

from . import archive, evo, games, levelport, sound
from .porting import GameSource, PortError, dds_problem, port_animation, port_character, port_model, port_vehicle, \
    _table

PREFIX = {"ds2": "DS2_", "ge": "GE_", "cv": "CV_", "cg": "CG_", "do": "DO_"}
SAMPLE_BASE = {"ds2": 0x51000000, "ge": 0x52000000, "cv": 0x53000000, "cg": 0x54000000, "do": 0x55000000}
LIBRARY_VERSION = 1
_DEFINITIONS = (".WEAPON", ".VEHICLE", ".UNIT", ".SKIN")


class Library:
    """One game's library being built in `stage` (files, index, resume state)."""

    def __init__(self, src, dst, stage, log=print):
        self.src, self.dst, self.stage, self.log = src, dst, stage, log
        self.prefix = PREFIX[src.game.id]
        os.makedirs(stage, exist_ok=True)
        self.index = {}                     # file -> (kind, source, note)
        self.done = {}                      # "kind:source" -> "ok" / error text
        state = os.path.join(stage, "_state.pkl")
        if os.path.exists(state):
            self.index, self.done = pickle.load(open(state, "rb"))
        with dst.current():
            self.ds1_hashes = set(dst.files.index) if dst.files is not None else set()
        self.collisions = []

    def save_state(self):
        pickle.dump((self.index, self.done), open(os.path.join(self.stage, "_state.pkl"), "wb"))

    def name(self, fname):
        """The library name of an output file: the game's prefix in front (definitions keep their name, their id is
        prefixed separately)."""
        base = os.path.basename(fname)
        return base if base.upper().startswith(self.prefix) else self.prefix + base

    def put(self, fname, data, kind, source, note=""):
        name = self.name(fname)
        if name.upper().endswith(_DEFINITIONS):
            text = data.decode("latin-1")
            text = re.sub(r"(?m)^(id\s*=\s*)(.*)$", lambda m: m.group(1) + (
                m.group(2).strip() if m.group(2).strip().upper().startswith(self.prefix) else
                self.prefix + m.group(2).strip()), text, count=1)
            data = text.encode("latin-1")
        with self.dst.current():
            if evo.archive_hash(name) in self.ds1_hashes:
                self.collisions.append(name)
        path = os.path.join(self.stage, name.upper())
        if name.upper() in self.index and os.path.exists(path):
            return name                     # shared textures: written once
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self.index[name.upper()] = (kind, source, note)
        return name

    def put_all(self, files, kind, source, skip_tables=True):
        for fn, data in files.items():
            up = os.path.basename(fn).upper()
            if skip_tables and up.endswith(".TXT") and not up.startswith(self.prefix):
                continue                    # mod table parts: a .weapon / .vehicle / .unit builds its own rows
            self.put(fn, data, kind, source)

    def step(self, kind, source, fn):
        key = "%s:%s" % (kind, source)
        if key in self.done:
            return
        try:
            fn()
            self.done[key] = "ok"
        except (PortError, evo.EvoError, ValueError, KeyError, IndexError, struct.error) as e:
            self.done[key] = str(e)[:200] or type(e).__name__
        except Exception as e:                                  # noqa: BLE001 - one asset never stops the build
            self.done[key] = "%s: %s" % (type(e).__name__, str(e)[:160])
            self.log("  [error] %s %s\n%s" % (kind, source, traceback.format_exc(limit=3)))
        if len(self.done) % 50 == 0:
            self.save_state()


# ------------------------------------------------------------------------------------------------- names --

def _archive_signature(src):
    sig = []
    for p in sorted({path for path, _o, _s in src.files.index.values()}):
        st = os.stat(p)
        sig.append((os.path.basename(p).upper(), st.st_size, int(st.st_mtime)))
    return tuple(sig)


def names_of(src):
    """{hash: NAME.EXT} of a source game's archive entries: words of its binaries AND of every archive file's
    contents (scenes, tables, models name the files they use) - 96 % of Desert Storm II's entries; cached per game,
    keyed on the archives' sizes and dates."""
    cache = os.path.join(tempfile.gettempdir(), "conflict_ds_tools", "library")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, src.game.id + "_names.pkl")
    sig = _archive_signature(src)
    if os.path.exists(path):
        got_sig, table = pickle.load(open(path, "rb"))
        if got_sig == sig:
            return table
    blobs = []
    with src.current():
        for _h, (p, o, sz) in src.files.index.items():
            with open(p, "rb") as f:
                f.seek(o)
                blobs.append(f.read(sz))
        table = archive.build_dictionary(src.folder, blobs, set(src.files.index))
    pickle.dump((sig, table), open(path, "wb"))
    return table


# ------------------------------------------------------------------------------------------------ assets --

def _models(lib, names, weapon_models, vehicle_models):
    src, dst = lib.src, lib.dst
    evos = sorted(n[:-4] for n in names.values() if n.upper().endswith(".EVO"))
    lib.log("  %d models" % len(evos))
    for k, name in enumerate(evos):
        if k % 250 == 0 and k:
            lib.log("    models %d / %d" % (k, len(evos)))
        up = name.upper()
        if up in weapon_models or up in vehicle_models:
            continue                        # their own steps: with the .weapon / .vehicle that use them

        def one(name=name):
            with src.current():
                nodes = evo.read(src.get(name + ".EVO"))
            if nodes and nodes[0].type == 5:
                raise PortError("a level piece - it comes with the mission maps (PSE\\Ports)")
            if any(n.kind == 2 for n in nodes):
                m, data, tex, skin, notes = port_character(src, dst, name, "Bradley")
                lib.put(m + ".EVO", data, "character", name, "; ".join(notes)[:200])
                lib.put_all(tex, "texture", name)
                if skin:
                    lib.put(skin[0], skin[1].replace("\n", "\r\n").encode("latin-1"), "skin", name)
                return
            m, data, tex, notes = port_model(src, dst, name, collision_only=True)
            lib.put(m + ".EVO", data, "model", name, "; ".join(notes)[:200])
            lib.put_all(tex, "texture", name)
        lib.step("model", name, one)


def _weapons(lib):
    src, dst = lib.src, lib.dst
    known = levelport._ds1_units(dst)
    rows = [r for r in _table(src, "WEAPS.TXT") if len(r) > 1 and r[0].strip()]
    lib.log("  %d weapons" % len(rows))
    for r in rows:
        unit = r[0].strip()

        def one(unit=unit):
            files, failed = {}, {}
            got = levelport._port_weapon(src, dst, unit, known, files, failed)
            if not got:
                raise PortError(next(iter(failed.values()), "no model to port"))
            lib.put_all(files, "weapon", unit)
        lib.step("weapon", unit, one)


def _vehicles(lib):
    src, dst = lib.src, lib.dst
    known = levelport._ds1_units(dst)
    rows = [r for r in src.vehicle_rows() if len(r) > 1 and r[0].strip()]
    lib.log("  %d vehicles" % len(rows))
    for r in rows:
        unit, model = r[0].strip(), r[1].strip()

        def one(unit=unit, model=model):
            like = levelport._vehicle_alias(unit, known) or levelport._vehicle_like_stat(src, dst, unit) or \
                levelport._vehicle_base(unit)
            files = {}
            try:
                if like is None:
                    raise PortError("no Desert Storm vehicle to base it on")
                vm, vfiles, _vn = port_vehicle(src, dst, model, like)
                files.update(vfiles)
                note = "drivable, on %s's handling" % like
            except (PortError, evo.EvoError) as e:
                vm = levelport._port_vehicle_model(src, dst, unit, model, like, files)
                if not vm:
                    raise PortError(str(e))
                note = "model on %s's (not drivable: %s)" % (like, str(e)[:80])
            for fn in list(files):
                if fn.upper().endswith(".VEHICLE"):
                    data = files.pop(fn)
                    data = re.sub(rb"(?m)^id( +)= .*$", lambda m: b"id" + m.group(1) + b"= " + unit.encode("latin-1"),
                                  data, count=1)
                    files[unit.upper() + ".vehicle"] = data
            lib.put_all(files, "vehicle", unit)
            lib.index.setdefault(lib.name(unit + ".vehicle").upper(), ("vehicle", unit, note))
        lib.step("vehicle", unit, one)


def _units(lib):
    src, dst = lib.src, lib.dst
    known = levelport._ds1_units(dst)
    rows = [r for r in _table(src, "UNITS.TXT") if len(r) > 2 and r[0].strip() and r[2].strip() not in ("", "-1")]
    lib.log("  %d soldier types" % len(rows))
    for r in rows:
        unit, model = r[0].strip(), r[2].strip()

        def one(unit=unit, model=model):
            like = levelport._vehicle_alias(unit, known, "UNITS.TXT") or "Iraqi_Conscript"
            files = {}
            levelport._port_unit(src, dst, unit, model, like, files)
            lib.put_all(files, "unit", unit)
        lib.step("unit", unit, one)


def _animations(lib, names):
    src, dst = lib.src, lib.dst
    # .PRB by the library's names (archive contents too: Vietnam 409 vs GameSource's 250); Global Storm's .ani are
    # unnamed archive files named by the name inside (GameSource.animations)
    if src.game.id == "cg":
        loaders = src.animations()
    else:
        loaders = {n[:-4].upper(): (lambda n=n: src.get(n)) for n in names.values() if n.upper().endswith(".PRB")}
    lib.log("  %d animations" % len(loaders))
    for name in sorted(loaders):
        def one(name=name):
            fname, data, notes = port_animation(src, dst, name, loaders[name])
            lib.put(fname, data, "animation", name, "; ".join(notes)[:200])
        lib.step("animation", name, one)


def _textures(lib, names):
    """Textures no converted model brought along: as they are, under the prefix (both games read DDS / TGA)."""
    src = lib.src
    tex = sorted(n for n in names.values() if n.upper().rsplit(".", 1)[-1] in ("DDS", "TGA", "BMP", "PNG"))
    lib.log("  %d textures" % len(tex))
    for name in tex:
        if lib.name(name).upper() in lib.index:
            continue

        def one(name=name):
            data = src.get(name)
            if data is None:
                raise PortError("not readable")
            if name.upper().endswith(".DDS"):
                problem = dds_problem(data)
                if problem:
                    raise PortError(problem)
            lib.put(name, data, "texture", name)
        lib.step("texture", name, one)


def _bank_names(src, names):
    """{bank key: name} for the words of the game's binaries / tables that name sound banks."""
    out = {}
    words = {n.rsplit(".", 1)[0] for n in names.values()}
    for w in list(words):
        out.setdefault(sound.bank_key(w), w)
        out.setdefault(sound.bank_key(w.lower()), w)
    for blob_name in ("WEAPS.TXT", "VSTAT.TXT", "PROPS.TXT", "UNITS.TXT", "DOORS.TXT"):
        for r in _table(src, blob_name):
            for c in r:
                c = c.strip()
                if 2 < len(c) < 48 and re.fullmatch(r"[A-Za-z0-9_\-]+", c):
                    out.setdefault(sound.bank_key(c), c)
    return out


def _sounds(lib, names):
    """Every bank of the game's loose sound caches as a bank pack, renumbered into the game's sample range; the
    samples no bank refers to (voice lines, cutscene streams' entries) as one pack per cache; the .psf streams copied."""
    src = lib.src
    folder = src.folder
    caches = sorted(os.path.join(r, f) for r, _d, fs in os.walk(folder) for f in fs if f.lower().endswith(".sch"))
    streams = sorted(os.path.join(r, f) for r, _d, fs in os.walk(folder) for f in fs if f.lower().endswith(".psf"))
    lib.log("  %d sound caches, %d streams" % (len(caches), len(streams)))
    bank_names = _bank_names(src, names)
    next_id = [SAMPLE_BASE[src.game.id] + lib.done.get("_next_sample_offset", 0)]
    seen = set()
    for cache in caches:
        cname = os.path.splitext(os.path.basename(cache))[0]

        def one(cache=cache, cname=cname):
            with open(cache, "rb") as f:
                data = f.read()
            try:
                cl = sound.chunks(data)
            except ValueError as e:
                raise PortError(str(e))
            have = sound.samples(cl)
            used = set()
            for tag, c in cl:
                if tag != b"BANK":
                    continue
                key = struct.unpack_from("<I", c, 8)[0]
                if key in seen:
                    used.update(sound.bank_samples(c, have))
                    continue
                seen.add(key)
                bname = bank_names.get(key, "BANK_%08X" % key)
                lname = lib.name(bname)
                try:
                    b = bytearray(sound.bank_to_ds1(c))
                except ValueError:
                    lib.done["bank:%s" % bname] = "a bank layout Desert Storm can't read"
                    continue
                struct.pack_into("<I", b, 8, sound.bank_key(lname))
                refs = sound.bank_samples(b, have)
                used.update(refs)
                pack = sound.build([bytes(b)] + [x for i in refs for x in have[i]])
                pack = sound.renumber(pack, next_id[0])
                next_id[0] += max(1, len(refs))
                lib.put(lname + ".SCH", pack, "sound bank", "%s in %s" % (bname, cname),
                        "%d sample(s)" % len(refs))
            rest = [i for i in have if i not in used]
            if rest:
                pack = sound.build([x for i in rest for x in have[i]])
                # samples without a bank: renumbered one after another (renumber works on bank order - none here)
                out = []
                for t, c in sound.chunks(pack):
                    c = bytearray(c)
                    struct.pack_into("<I", c, 12, next_id[0] + rest.index(sound.sample_id(bytes(c))))
                    out.append(bytes(c))
                next_id[0] += len(rest)
                lib.put("%s_SAMPLES.SCH" % cname, sound.build(out), "sound samples", cname,
                        "%d sample(s) no bank refers to (voice lines, stream entries)" % len(rest))
            lib.done["_next_sample_offset"] = next_id[0] - SAMPLE_BASE[src.game.id]   # (resume never reuses ids)
        lib.step("sound cache", cname, one)
    for s in streams:
        def one(s=s):
            with open(s, "rb") as f:
                lib.put(os.path.basename(s), f.read(), "sound stream", os.path.relpath(s, folder))
        lib.step("stream", os.path.relpath(s, folder), one)


# ------------------------------------------------------------------------------------------------- build --

def build_library(src, dst, out_root, log=print, redo=()):
    """Builds <out_root>\\<id> for one source game (staging folder <out_root>\\<id>.building, resumable, swapped in
    at the end). An existing library is updated in place: what converted stays, `redo` (kinds: "animation", "weapon",
    "sound cache" ...) retries those kinds - converter fixes reach a library without a full rebuild. Returns the
    report lines."""
    gid = src.game.id
    if gid not in PREFIX or gid == "do":
        raise PortError("%s isn't supported yet" % src.game.short)
    final = os.path.join(out_root, gid)
    stage = final + ".building"
    t0 = time.time()
    if os.path.isdir(final) and not os.path.isdir(stage):
        os.rename(final, stage)             # update in place: its _state.pkl says what's done
    lib = Library(src, dst, stage, log)
    for key in [k for k in lib.done if k.split(":", 1)[0] in redo or (k.startswith("bank:") and "sound cache" in redo)]:
        del lib.done[key]
    if "sound cache" in redo:
        lib.done.pop("_next_sample_offset", None)
        for name in [n for n, v in lib.index.items() if v[0] in ("sound bank", "sound samples")]:
            del lib.index[name]
            try:
                os.remove(os.path.join(stage, name))
            except OSError:
                pass
    log("%s: names" % src.game.short)
    names = names_of(src)
    weapon_models = {r[1].strip().upper() for r in _table(src, "WEAPS.TXT") if len(r) > 1}
    vehicle_models = {r[1].strip().upper() for r in src.vehicle_rows() if len(r) > 1}
    log("%s: weapons, vehicles, soldier types" % src.game.short)
    _weapons(lib)
    _vehicles(lib)
    _units(lib)
    log("%s: models" % src.game.short)
    _models(lib, names, weapon_models, vehicle_models)
    log("%s: animations, textures, sounds" % src.game.short)
    _animations(lib, names)
    _textures(lib, names)
    _sounds(lib, names)
    lib.save_state()
    with open(os.path.join(stage, "library.txt"), "w", encoding="latin-1", newline="\r\n") as f:
        f.write("# %s asset library for Desert Storm (ModKit conflict_ds_tools/library.py, version %d)\n" % (
            src.game.short, LIBRARY_VERSION))
        f.write("# Made from your own copy - never share it. A mod uses a file by naming it; copy a .weapon / .vehicle /"
                " .unit / .skin into your mod to use and change it.\n")
        f.write("# file\tkind\tsource\tnote\n")
        for name in sorted(lib.index):
            kind, source, note = lib.index[name]
            f.write("%s\t%s\t%s\t%s\n" % (name, kind, source, note))
    failed = {k: v for k, v in lib.done.items() if v != "ok" and not k.startswith("_")}
    with open(os.path.join(stage, "not-converted.txt"), "w", encoding="latin-1", newline="\r\n") as f:
        f.write("# What didn't convert and why\n")
        for k in sorted(failed):
            f.write("%s\t%s\n" % (k, failed[k]))
    if os.path.isdir(final):
        shutil.rmtree(final)
    os.rename(stage, final)
    kinds = {}
    for kind, _s, _n in lib.index.values():
        kinds[kind] = kinds.get(kind, 0) + 1
    report = ["%s: %d files in %s (%.0f s)" % (src.game.short, len(lib.index), final, time.time() - t0),
              "    " + ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())),
              "    %d not converted (not-converted.txt)" % len(failed)]
    if lib.collisions:
        report.append("    [warn] %d name(s) also used by Desert Storm: %s" % (
            len(lib.collisions), ", ".join(lib.collisions[:8])))
    return report


def build_all(dst_folder=None, games_wanted=None, log=print, redo=()):
    """build_library for every installed source game (or `games_wanted`). Returns the report lines."""
    installs = games.find_installs()
    dst_folder = dst_folder or installs.get("ds1")
    if not dst_folder:
        raise PortError("Desert Storm's folder not found")
    dst = GameSource("ds1", dst_folder)
    out_root = os.path.join(dst_folder, "PSE", "Library")
    report = []
    for gid in games_wanted or [g for g in ("ds2", "ge", "cv", "cg") if g in installs]:
        if gid not in installs:
            report.append("%s: not installed" % gid)
            continue
        try:
            report += build_library(GameSource(gid, installs[gid]), dst, out_root, log, redo)
        except PortError as e:
            report.append("%s: %s" % (gid, e))
    return report
