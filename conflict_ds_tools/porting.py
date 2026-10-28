"""Porting between the Conflict games - part 1 of docs/PORTING_PLAN.md: asset packs.

A recipe (.port, text, shareable) names what to take from a source game and the game it goes to. The porter runs on
the player's PC against their own copy of the source game (its folder, or a folder extracted from it) and writes a
mod folder for the target plus a report. Nothing of the source game is in the recipe.

    # comments
    name = DS2Props              the mod folder (Mods\\<name>)
    from = ds2                   source game id (ds1 ge ds2 cv cg)
    to = ds1                     target game id (Desert Storm so far)
    source = D:\\Games\\CDS2       optional: the source game folder or an extracted folder (default: the installed game)
    model WALLMAP                a static model by name; * and ? patterns over the names the source has
    model JEEP_* as MYJEEP       one model under another name (no pattern)
    weapon MPD_AK47 like IR_WPN_AK47 [as NEW]
                                 a gun model plus a starter .weapon: a new weapon on the loadout screen that handles
                                 like the named Desert Storm weapon (its numbers, sounds, animations - edit the file)
    vehicle C04_PICKUPTRUCK like US_VEH_HUM_VEE_M60 [as NEW]
                                 a drivable vehicle: the body on that Desert Storm vehicle's model (its seats, exits,
                                 gun mount moved to the source's own; its handling, guns, sounds), the source wheels on
                                 a fitted .DYN, a starter .vehicle (wheeled vehicles so far)
    unit IRAQI_SOLDIER_01 [with HEAD] like Iraqi_Conscript [as NEW]
                                 a soldier type for missions: the body on that Desert Storm unit's own model (its
                                 skeleton) plus a starter .unit that starts from the unit (kit, skill, voice ...)
    character IRAQI_SOLDIER_01 [with HEAD] for Bradley [as NEW]
                                 a soldier's body on Bradley's (Foley's, Connors', Jones') skeleton plus a starter
                                 .skin: a new uniform for him on the loadout screen that wears this body
                                 'with HEAD': a separate head model on the body (Global Storm keeps heads apart from
                                 bodies: C04_500_POLY + C04_RIFLEMAN_HEAD); without it <NAME>_HEAD when the source
                                 has one, 'with -' none
    animation STEALTHY_WALK*     animations: retargeted onto the target's soldier skeleton

Models land on the target's generic prop (one static object; a hierarchy is flattened in its rest pose) under the
name <SOURCE>_<NAME>; their textures go in under <SOURCE>_<texture> (a file named like one of the target's textures
would replace it everywhere); collision keeps its surfaces where the target's Default.spl has them (bsp.convert_surface);
the first five points (muzzle, launcher, eject, ladder bottom / top) mean the same in every game and are kept.
Animations are retargeted by bone name (evo.retarget) onto the target's soldier and written in its format; a Desert
Storm target keeps the source's footstep events when the source has them.
Vehicles come over as static copies, whole: the parts the game hangs on them at run time are merged in where they
hang - wheels / tracks from the .DYN and guns from VEHICLE -> VEHWPNS -> WEAPS (Desert Storm II, The Great Escape),
wheels and doors from the vehicle's physics file (Vietnam's unnamed "PhysicsCar" text files); a Global Storm vehicle
with a static version of its own (C04_ISUZUTRUCK_STATIC) is taken from that. Characters and drivable vehicles aren't
ported yet - the report says so."""
import fnmatch
import math
import os
import re
import struct
import tempfile
import zlib

from . import archive, bsp, crossgame, evo, evo_write, games, images, records, sound

TARGETS = ("ds1",)
# Desert Storm's squad: the body a ported character is fitted to (its skeleton) per soldier
SQUAD_BODIES = {"Bradley": "HERO01_ARMSTRONG", "Connors": "HERO02_RAMIREZ", "Jones": "HERO03_JONES",
                "Foley": "HERO04_FOLEY"}
# the target's generic prop (one static object, own-mesh collision) and soldier skeleton per game
GENERIC_PROP = {"ds1": "BH_BODGEPLATE", "ds2": "WALLMAP", "ge": "104"}
# the target's plain animated prop (one kind-1 object: a mesh per frame) - flags, washing lines, windsocks (scene VAMI
# records) are ported onto it with all their frames; on the generic prop they'd stand still
ANIMATED_PROP = {"ds1": "FLAG_FLAPPING"}
SOLDIER = {"ds1": "HERO01_ARMSTRONG", "ds2": "534_POLY_SOLDIER", "ge": "GEN_GER_SOLDIER", "cv": "MPD_US_MOTOWN",
           "cg": "C04_500_POLY"}
# the connector a soldier's right hand holds his gun on, per game (a gun model is made in that connector's space)
GUN_CONNECTOR = {"ds1": "BIP01 GUNR CONNECTOR", "ds2": "BIP01 GUNR CONNECTOR", "ge": "BIP01 GUNR CONNECTOR",
                 "cv": "HAND R CONNECTOR", "cg": "HAND R CONNECTOR"}
# DDS FourCCs Desert Storm's Direct3D 8 renderer draws (plus uncompressed RGB / RGBA)
DS1_DDS = (b"DXT1", b"DXT2", b"DXT3", b"DXT4", b"DXT5")


class PortError(Exception):
    pass


# ------------------------------------------------------------------------------------------------- sources --

def _phy_parse(text):
    """A physics file as nested blocks: {"name", "fields" {lower-case key: value text}, "children" [blocks]}."""
    root = {"name": "", "fields": {}, "children": []}
    stack, pending = [root], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "{":
            block = {"name": pending or "", "fields": {}, "children": []}
            stack[-1]["children"].append(block)
            stack.append(block)
            pending = None
        elif line == "}":
            if len(stack) > 1:
                stack.pop()
            pending = None
        elif "=" in line:
            key, _eq, value = line.partition("=")
            stack[-1]["fields"][key.strip().lower()] = value.strip()
            pending = None
        else:
            pending = line
    return root


def _phy_find(block, name):
    """The first block of that name below `block` (depth first), or None."""
    for c in block["children"]:
        if c["name"] == name:
            return c
        hit = _phy_find(c, name)
        if hit:
            return hit
    return None


def _phy_vec(text):
    try:
        return [float(v) for v in text.strip("() ").split(",")][:3]
    except (ValueError, AttributeError):
        return [0.0, 0.0, 0.0]


def _phy_attached(block, base=(0.0, 0.0, 0.0)):
    """[(geometry, (x, y, z) cm)] of every Attached under `block`: each at base + Position; blocks nested in a block
    that has an Attached of its own (a Track's road wheels) are placed from that Attached's Position. The Offset is a
    physics setting, not where the model is drawn: checked against Global Storm's C04_MTLB_STATIC (the game's own
    merged copy) - track and doors land on it exactly at Position (0.0 / 0.9 cm), 10 / 18 cm off with the Offset
    (the Arjun's tracks, Offset -50 cm, hung below its wheels)."""
    out = []
    for c in block["children"]:
        att = next((a for a in c["children"] if a["name"] == "Attached"), None)
        inner = base
        if att is not None:
            pos = _phy_vec(att["fields"].get("position"))
            geometry = att["fields"].get("geometry")
            if geometry:
                out.append((geometry.upper(), tuple(base[i] + 100.0 * pos[i] for i in range(3))))
            inner = tuple(base[i] + 100.0 * pos[i] for i in range(3))
        if c["name"] != "Attached":
            out += _phy_attached(c, inner)
    return out


class GameSource:
    """Files of one game, read by name: from its folder (archives, the game's own hashing) or an extracted folder."""

    def __init__(self, game, folder=None):
        self.game = games.GAMES[game] if isinstance(game, str) else game
        folder = folder or games.find_game_dir(self.game) or ""
        self.folder = folder
        self.files = self.finder = None
        if folder and games.detect(folder) is self.game:
            with self.current():
                self.files = archive.GameFiles(folder)
        elif folder and games.detect_extracted(folder) is self.game:
            subs = [os.path.join(folder, d) for d in sorted(os.listdir(folder))
                    if not d.startswith("_") and os.path.isdir(os.path.join(folder, d))]
            self.finder = evo.AssetFinder(None, subs)
        else:
            raise PortError("%s isn't a %s game folder or a folder extracted from it" % (folder or "(not found)",
                                                                                     self.game.short))
        self._names = None
        self._anims = None
        self._cache = os.path.join(tempfile.gettempdir(), "conflict_ds_tools", "port", self.game.id)

    def current(self):
        """with source.current(): archive names hash by this game's rule."""
        game = self.game

        class _Ctx:
            def __enter__(self):
                self.old = games.current()
                games.set_current(game)

            def __exit__(self, *exc):
                games.set_current(self.old)
        return _Ctx()

    def get(self, name):
        """The bytes of a file by name (NAME.EXT), or None."""
        if self.files is not None:
            with self.current():
                return self.files.get(name)
        stem, ext = os.path.splitext(name)
        with self.current():               # unnamed extracted files (_HASH.EXT) are found by this game's hash
            path = self.finder.find(stem, (ext.lower(),))
        if not path:
            return None
        with open(path, "rb") as f:
            return f.read()

    def names(self, ext):
        """Upper-case names (without extension) of the source's files with that extension that can be named: an
        extracted folder's files; a game folder's archive entries whose name is found in the game's binaries (the
        extractor's dictionary - patterns can miss files whose name appears nowhere)."""
        ext = ext.upper()
        if self._names is None:
            if self.finder is not None:
                self._names = {(stem + e).upper() for stem, by_ext in self.finder.by_stem.items() for e in by_ext}
            else:
                with self.current():
                    self._names = set(archive.build_dictionary(self.folder, [], set(self.files.index)).values())
        return sorted(os.path.splitext(n)[0] for n in self._names if n.endswith(ext))

    def animations(self):
        """{NAME: bytes loader} of the source's animations (.PRB; Global Storm: the unnamed .BIN files, named by the
        name inside)."""
        if self._anims is not None:
            return self._anims
        out = {}
        if self.finder is not None:
            with self.current():
                found = self.finder.animations()
            for name, path in found.items():
                out[name.upper()] = (lambda p=path: open(p, "rb").read())
        elif self.game.id == "cg":
            for h, (path, o, s) in self.files.index.items():
                with open(path, "rb") as f:
                    f.seek(o)
                    head = f.read(68)
                if len(head) == 68 and head[:1].isalpha() and evo.is_ani_cg(head + b"\0" * (s - 68)):
                    name = head[:44].split(b"\0")[0].decode("latin-1").upper()
                    out.setdefault(name, (lambda p=path, o=o, s=s: _read_at(p, o, s)))
        else:
            for name in self.names(".PRB"):
                out[name] = (lambda n=name: self.get(n + ".PRB"))
        self._anims = out
        return out

    def physics_attached(self, model):
        """[(part model, (x, y, z) cm)] a vehicle's physics file hangs on it, [] if none. Vietnam: unnamed text files,
        Global Storm: <MODEL>.PHY - both "Version = 1 / PhysicsCar|PhysicsTank { PhysicsVehicle { PhysicsObject {
        Geometry = <MODEL> ... } Door { Attached { Geometry, Position, Offset } } } Wheel { Attached ... } Track {
        Attached ... Wheel { Attached ... } } }" in metres, model space. A tracked vehicle's road wheels sit inside
        its Track blocks, placed from the track's Position (checked against Global Storm's C04_ISUZUTRUCK_STATIC and
        C04_MTLB_STATIC: parts at Position - the Offset isn't drawn - and right-hand wheels not mirrored)."""
        if not hasattr(self, "_physics"):
            self._physics = {}
            for data in self._text_blobs(b"Version = ", extra_ext=(".phy",)):
                if b"PhysicsObject" not in data:
                    continue
                tree = _phy_parse(data.decode("latin-1"))
                body = _phy_find(tree, "PhysicsObject")
                geometry = body and body["fields"].get("geometry")
                if geometry:
                    self._physics.setdefault(geometry.upper(), _phy_attached(tree))
        return self._physics.get(model.upper(), [])

    def table_rows(self, token):
        """Comma-separated rows that contain `token` (upper case) from the source's small text tables - named
        (WEAPS.TXT, INVEN.TXT) or not (Global Storm keeps its catalog tables in unnamed .BIN files)."""
        if not hasattr(self, "_tables"):
            self._tables = []
            for data in self._small_files():
                head = data[:256]
                if b"," in head and sum(32 <= c < 127 or c in (9, 10, 13) for c in head) > 0.95 * len(head):
                    self._tables.append(data.decode("latin-1").upper())
        token = token.upper()
        for text in self._tables:
            if token in text:
                for line in text.splitlines():
                    if token in line:
                        yield [f.strip() for f in line.split(",")]

    def _small_files(self, limit=1 << 18, exts=(".txt", ".bin")):
        if self.finder is not None:
            for stem, by_ext in self.finder.by_stem.items():
                for ext, path in by_ext.items():
                    if ext.lower() in exts and os.path.getsize(path) < limit:
                        with open(path, "rb") as f:
                            yield f.read()
            return
        for h, (path, o, size) in self.files.index.items():
            if 16 < size < limit:
                with open(path, "rb") as f:
                    f.seek(o)
                    yield f.read(size)

    def vehicle_guns(self, model, connectors):
        """[(gun model, connector name)] a Vietnam / Global Storm vehicle carries: their tables have no row naming the
        model, so the vehicle is found by name (C04_BMP1 -> TR_VEH_BMP1, C04_ARJUN -> TR_VEH_ARJUN_TANK,
        C04_LANDROVER_MK19ANDMG -> UK_VEH_SAS_LANDROVER_MK19_SAW, C04_MTLBANDTURRET -> TR_VEH_MTLB, MPD_M48_PATTON ->
        US_VEH_M48_PATTON: the vehicle id's words found in the model name, every gun's connector on the model);
        its vehicle-weapon rows (vehicle, weapon, WEAPON, ...) name weapon rows - Vietnam (id, gun model, ...,
        connector in column 6) like Desert Storm, Global Storm one column on (id, -1, gun model, ..., column 8).
        [] when nothing fits."""
        if self.game.id in ("ds2", "ge"):
            return self._table_guns(model, connectors)
        if self.game.id not in ("cg", "cv"):
            return []
        # words any vehicle id may carry - they don't identify one (C04_MTLBANDTURRET isn't TR_VEH_T62_TANK_180_TURRET)
        generic = {"TR", "VEH", "UK", "US", "SAS", "RUS", "STATIC", "BROKEN", "CIV", "DEAD", "NONDRIVEABLE", "TURRET",
                   "CANNON", "TANK", "GUN", "MG", "HMG", "VC", "NVA", "ARVN", "AUS"}
        name = model.upper()
        for lead in ("C04_", "MPD_"):
            name = name[len(lead):] if name.startswith(lead) else name
        if not hasattr(self, "_vweapons"):
            self._vweapons, self._weapon_rows, self._weapon_kind = {}, {}, {}
            for row in self.table_rows("_VWPN_"):
                if len(row) > 8 and row[1] == "-1" and row[8].upper().startswith("CONNECTOR"):
                    self._weapon_rows[row[0].upper()] = (row[2].upper(), row[8].upper())        # Global Storm
                    self._weapon_kind[row[8].upper(), row[2].upper()] = row[7].strip().upper()
                elif len(row) > 6 and row[1] != "-1" and row[6].upper().startswith("CONNECTOR"):
                    self._weapon_rows[row[0].upper()] = (row[1].upper(), row[6].upper())        # Vietnam
                    self._weapon_kind[row[6].upper(), row[1].upper()] = row[5].strip().upper()
                elif len(row) > 2 and row[2].upper() == "WEAPON" and "_VEH_" in row[0].upper():
                    self._vweapons.setdefault(row[0].upper(), []).append(row[1].upper())
        have = {c.upper() for c in connectors}
        best, best_score = None, 0
        for vid, weapons in sorted(self._vweapons.items()):
            guns = [self._weapon_rows[w] for w in weapons if w in self._weapon_rows]
            if not guns or any(conn not in have for _g, conn in guns):
                continue
            # the vehicle id's words found in the model name (C04_MTLBANDTURRET holds MTLB): longer matches count
            # more, words the model doesn't have count against
            vwords = [w for w in vid.split("_") if len(w) > 1 and not w.isdigit() and w not in generic]
            hit = [w for w in vwords if w in name]
            # ties: the vehicle with more guns that fit (C04_MTLBANDTURRET: TR_VEH_MTLB_CANNON's HMG + cannon)
            score = sum(len(w) for w in hit) * 100 - 10 * (len(vwords) - len(hit)) + len(guns)
            if hit and score > best_score:
                best, best_score = vid, score
        if best is None:
            return []
        out, seen = [], set()
        for w in self._vweapons[best]:
            gun = self._weapon_rows.get(w)
            if gun and gun[0] != "-1" and gun not in seen:
                seen.add(gun)
                out.append(gun)
        return out

    # Desert Storm II / The Great Escape weapon rows: (model, category, connector) columns
    _GUN_COLUMNS = {"ds2": (1, 5, 6), "ge": (1, 4, 5)}

    def vehicle_rows(self):
        """VEHICLE.TXT rows of this game: comma-separated (Desert Storm, The Great Escape) or space-separated (Desert
        Storm II - the table reader above only sees comma rows)."""
        out = []
        for line in (self.get("VEHICLE.TXT") or b"").decode("latin-1").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append([c.strip() for c in line.split(",")] if "," in line else line.split())
        return out

    def _table_guns(self, model, connectors):
        """Desert Storm II / Great Escape: VEHICLE.TXT rows drawing the model -> VEHWPNS.TXT -> WEAPS.TXT, like Desert
        Storm's own chain (The Great Escape's weapon rows are one column shorter in front)."""
        mcol, kcol, ccol = self._GUN_COLUMNS[self.game.id]
        if not hasattr(self, "_weapon_kind"):
            self._weapon_kind = {}
        have = {c.upper() for c in connectors}
        vids = {r[0].upper() for r in self.vehicle_rows() if len(r) > 2 and r[1].upper() == model.upper()}
        out = []
        for vid in sorted(vids):
            for r in self.table_rows(vid):
                if len(r) < 3 or r[0].upper() != vid or r[2].upper() != "WEAPON":
                    continue
                wid = r[1].upper()
                for w in self.table_rows(wid):
                    if w[0].upper() == wid and len(w) > ccol and w[ccol].upper() in have and w[mcol] not in ("-1", ""):
                        gun = (w[mcol].upper(), w[ccol].upper())
                        self._weapon_kind[gun[1], gun[0]] = w[kcol].strip().upper()
                        if gun not in out:
                            out.append(gun)
        return out

    def gun_kinds(self, model, connectors):
        """{connector name: weapon category (ANTI TANK, MACHINE GUN ...)} of vehicle_guns' guns."""
        guns = self.vehicle_guns(model, connectors)
        return {conn: self._weapon_kind.get((conn, gun), "") for gun, conn in guns}

    def gun_clip(self, model):
        """The magazine model a gun shows on its CONNECTOR_CLIP (Vietnam / Global Storm): the weapon row naming the
        model -> its id, the AMMO row naming that id -> its model (Vietnam INVEN.TXT Ammo_AK47,AMMO,MPD_AK47_AmmoClip,
        ...,VC_WPN_AK47; Global Storm the same one column further on), or None."""
        ids = {r[0] for r in self.table_rows(model) if "_WPN_" in r[0] and model.upper() in r[1:4]}
        for wid in ids:
            for r in self.table_rows(wid):
                if len(r) > 3 and r[1] == "AMMO" and wid in r[3:12] and r[2] not in ("-1", ""):
                    return r[2]
        return None

    def _text_blobs(self, magic, limit=1 << 18, extra_ext=()):
        """Bytes of the source's small files that start with `magic` (unnamed text files the dictionary can't name;
        in an extracted folder also files with the extensions in extra_ext, e.g. Global Storm's .PHY)."""
        if self.finder is not None:
            exts = (".bin",) + tuple(extra_ext)
            for stem, by_ext in self.finder.by_stem.items():
                for ext, path in by_ext.items():
                    if ext.lower() in exts and os.path.getsize(path) < limit:
                        with open(path, "rb") as f:
                            head = f.read(len(magic))
                            if head == magic:
                                yield head + f.read()
            return
        for h, (path, o, size) in self.files.index.items():
            if size < limit:
                with open(path, "rb") as f:
                    f.seek(o)
                    head = f.read(len(magic))
                    if head == magic:
                        yield head + f.read(size - len(magic))

    def scene_file(self, name):
        """A path to the model NAME.EVO on disk (a cache copy when it comes from the archives), with the skeleton
        files a Vietnam / Global Storm body needs next to it - for evo.load."""
        if self.finder is not None:
            with self.current():
                path = self.finder.find(name, (".evo",))
            if path:
                return path
        os.makedirs(self._cache, exist_ok=True)
        for f in [name + ".EVO"] + [s for e in evo.SKELETON_FILES for s in (e, os.path.splitext(e)[0] + ".SKL")]:
            dst = os.path.join(self._cache, f)
            if not os.path.isfile(dst):
                data = self.get(f)
                if data:
                    with open(dst, "wb") as fh:
                        fh.write(data)
        path = os.path.join(self._cache, name + ".EVO")
        return path if os.path.isfile(path) else None


class _CacheFinder:
    """evo.AssetFinder's find() over a GameSource: files fetched by name into the source's cache folder (for helpers
    that open files, such as evo.vehicle_parts' .DYN / table readers)."""

    def __init__(self, src):
        self.src = src

    def find(self, stem, exts=evo.TEXTURE_EXTS):
        # (texture extensions by default, as evo.AssetFinder: resolve_textures asks for skins without one)
        if self.src.finder is not None:
            with self.src.current():
                return self.src.finder.find(stem, exts)
        for ext in exts:
            name = (stem + ext).upper()
            path = os.path.join(self.src._cache, name)
            if os.path.isfile(path):
                return path
            data = self.src.get(name)
            if data is not None:
                os.makedirs(self.src._cache, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
                return path
        return None


def _read_at(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


# ----------------------------------------------------------------------------------------------- recipes --

class Recipe:
    def __init__(self):
        self.name = ""
        self.source_game = None
        self.target_game = "ds1"
        self.source = None
        self.items = []           # (kind "model" / "weapon" / "animation", pattern, new name or None)
        self.likes = {}           # item index (weapon lines) -> the target weapon it is based on
        self.soldiers = {}        # item index (character lines) -> the squad soldier who wears it
        self.heads = {}           # item index (character / unit lines) -> 'with' head model ("-" = none)
        self.bases = {}           # item index (level lines) -> the target level whose archive it uses


def read_recipe(text):
    r = Recipe()
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line and line.split("=", 1)[0].strip().lower() in ("name", "from", "to", "source"):
            k, v = [x.strip() for x in line.split("=", 1)]
            k = k.lower()
            if k == "name":
                r.name = v
            elif k == "from":
                r.source_game = v.lower()
            elif k == "to":
                r.target_game = v.lower()
            else:
                r.source = v
            continue
        words = line.split()
        kind = words[0].lower()
        if kind == "weapon":
            if len(words) not in (4, 6) or words[2].lower() != "like" or (len(words) == 6 and words[4].lower() != "as"):
                raise PortError("line %d: %s - expected 'weapon NAME like TARGET_WEAPON_ID [as NEW]'" % (n, line))
            if any(c in words[1] for c in "*?"):
                raise PortError("line %d: weapon lines take one model, not a pattern" % n)
            r.items.append(("weapon", words[1].upper(), words[5].upper() if len(words) == 6 else None))
            r.likes[len(r.items) - 1] = words[3]
            continue
        head = None
        if kind in ("unit", "character") and len(words) >= 3 and words[2].lower() == "with":
            if len(words) < 4 or any(c in words[3] for c in "*?"):
                raise PortError("line %d: 'with' needs a head model name (or - for none)" % n)
            head, words = words[3].upper(), words[:2] + words[4:]
        if head is not None:
            r.heads[len(r.items)] = head
        if kind == "level":
            rest, base = words[2:], "Mission3"
            if rest[:1] and rest[0].lower() == "on":
                if len(rest) < 2:
                    raise PortError("line %d: 'on' needs a Desert Storm level (Mission3 ...)" % n)
                base, rest = rest[1], rest[2:]
            if len(words) < 2 or (rest and (len(rest) != 2 or rest[0].lower() != "as")) or                     any(c in words[1] for c in "*?"):
                raise PortError("line %d: %s - expected 'level SOURCE_LEVEL [on DS1_LEVEL] [as NEW]'" % (n, line))
            r.items.append(("level", words[1].upper(), rest[1].upper() if rest else None))
            r.bases[len(r.items) - 1] = base
            continue
        if kind == "texts":
            if len(words) not in (2, 4) or (len(words) == 4 and words[2].lower() != "as"):
                raise PortError("line %d: %s - expected 'texts SOURCE_TEXTS [as SECTION]' (a mission's texts: M01, "
                                "GE_LEVEL01 ..., or catalog)" % (n, line))
            r.items.append(("texts", words[1].upper(), words[3].upper() if len(words) == 4 else None))
            continue
        if kind == "vehicle":
            if len(words) not in (4, 6) or words[2].lower() != "like" or (len(words) == 6 and words[4].lower() != "as")                     or any(c in words[1] for c in "*?"):
                raise PortError("line %d: %s - expected 'vehicle NAME like TARGET_VEHICLE_ID [as NEW]'" % (n, line))
            r.items.append(("vehicle", words[1].upper(), words[5].upper() if len(words) == 6 else None))
            r.likes[len(r.items) - 1] = words[3]
            continue
        if kind == "unit":
            if len(words) not in (4, 6) or words[2].lower() != "like" or (len(words) == 6 and words[4].lower() != "as")                     or any(c in words[1] for c in "*?"):
                raise PortError("line %d: %s - expected 'unit NAME like TARGET_UNIT_ID [as NEW]'" % (n, line))
            r.items.append(("unit", words[1].upper(), words[5].upper() if len(words) == 6 else None))
            r.likes[len(r.items) - 1] = words[3]
            continue
        if kind == "character":
            rest = words[2:]
            soldier = "Bradley"
            if rest[:1] and rest[0].lower() == "for":
                if len(rest) < 2:
                    raise PortError("line %d: 'for' needs a soldier (Bradley, Foley, Connors, Jones)" % n)
                soldier, rest = rest[1].capitalize(), rest[2:]
            if soldier not in SQUAD_BODIES:
                raise PortError("line %d: %s isn't a squad soldier (Bradley, Foley, Connors, Jones)" % (n, soldier))
            if len(words) < 2 or (rest and (len(rest) != 2 or rest[0].lower() != "as")) or \
                    any(c in words[1] for c in "*?"):
                raise PortError("line %d: %s - expected 'character NAME [for SOLDIER] [as NEW]'" % (n, line))
            r.items.append(("character", words[1].upper(), rest[1].upper() if rest else None))
            r.soldiers[len(r.items) - 1] = soldier
            continue
        if kind not in ("model", "animation") or len(words) not in (2, 4) or (len(words) == 4 and
                                                                             words[2].lower() != "as"):
            raise PortError("line %d: %s - expected 'model NAME [as NEW]' or 'animation NAME [as NEW]'" % (n, line))
        if len(words) == 4 and any(c in words[1] for c in "*?"):
            raise PortError("line %d: a pattern can't be renamed with 'as'" % n)
        r.items.append((kind, words[1].upper(), words[3].upper() if len(words) == 4 else None))
    if not r.name or not r.source_game:
        raise PortError("a recipe needs 'name =' and 'from ='")
    if r.source_game not in games.GAMES:
        raise PortError("unknown game '%s' (%s)" % (r.source_game, " ".join(games.GAMES)))
    if r.target_game not in TARGETS:
        raise PortError("ports go to %s so far, not %s" % (", ".join(TARGETS), r.target_game))
    if r.source_game == r.target_game:
        raise PortError("the source and target game are the same")
    return r


# -------------------------------------------------------------------------------------------- conversion --

def _colour(c):
    r, g, b, a = (max(0, min(255, int(round(x * 255)))) for x in c)
    return (a << 24) | (r << 16) | (g << 8) | b


def _normals(positions, triangles):
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c in triangles:
        pa, pb, pc = positions[a], positions[b], positions[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        for i in (a, b, c):
            for k in range(3):
                acc[i][k] += n[k]
    out = []
    for n in acc:
        length = math.sqrt(sum(x * x for x in n)) or 1.0
        out.append((n[0] / length, n[1] / length, n[2] / length))
    return out


_CELL = 400.0       # cm: grid of the u 0 polygons for _covered


def _plane(verts):
    a, b, c = verts[0], verts[1], verts[2]
    n = ((b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
         (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
         (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    length = math.sqrt(sum(x * x for x in n))
    if length < 1e-9:
        return None
    n = tuple(x / length for x in n)
    return n, -sum(n[i] * a[i] for i in range(3))


def _u0_grid(scene):
    """{(cell x, cell z): [(verts, normal, d)]} of every object's u 0 polygons in world space, cached on the scene."""
    grid = getattr(scene, "_u0_grid", None)
    if grid is not None:
        return grid
    grid = {}
    for o in scene.objects:
        for u, tree in getattr(o.node, "trees", []) or []:
            if u != 0 or not tree:
                continue
            root = bsp.read(tree)
            for poly in bsp.polygons(root) if root else []:
                verts = [tuple(evo.mat_apply(o.world, v)) for v in poly.verts]
                pl = _plane(verts)
                if pl is None:
                    continue
                xs, zs = [v[0] for v in verts], [v[2] for v in verts]
                for cx in range(int(math.floor(min(xs) / _CELL)), int(math.floor(max(xs) / _CELL)) + 1):
                    for cz in range(int(math.floor(min(zs) / _CELL)), int(math.floor(max(zs) / _CELL)) + 1):
                        grid.setdefault((cx, cz), []).append((verts, pl[0], pl[1]))
    scene._u0_grid = grid
    return grid


def _covered(scene, verts):
    """True when the scene's u 0 collision has ground (a polygon that isn't a wall) anywhere in the vertical column
    through this polygon's centre. Desert Storm II's u 0 trees are a smoothed shell a little off the detailed render
    ground - where they exist the render polygons stay out (driving on them shook the vehicles, their rocks blocked
    paths); only columns with no collision at all (hillsides beside the roads) get the render polygons."""
    if _plane(verts) is None:
        return True
    c = tuple(sum(v[i] for v in verts) / len(verts) for i in range(3))
    for pverts, n, d in _u0_grid(scene).get((int(math.floor(c[0] / _CELL)), int(math.floor(c[2] / _CELL))), ()):
        if abs(n[1]) < 0.2:     # walls don't count as ground
            continue
        # centre inside the (convex) polygon, seen from above
        i0, i1 = 0, 2
        sign = 0
        for k in range(len(pverts)):
            a, b = pverts[k], pverts[(k + 1) % len(pverts)]
            cross = (b[i0] - a[i0]) * (c[i1] - a[i1]) - (b[i1] - a[i1]) * (c[i0] - a[i0])
            if abs(cross) < 1e-6:
                continue
            s = 1 if cross > 0 else -1
            if sign and s != sign:
                break
            sign = s
        else:
            return True
    return False


def static_model_parts(scene, prefix, extras=(), keep=None, render_trees=False):
    """(parts per texture, collision triangles, points {role: pos}, notes) of a source model in model space: every
    render object ("mesh" role) in its rest pose, one Part per texture (prefixed); collision from each object's
    u 0 tree (polygons fanned into triangles, surfaces as stored) - with render_trees also its u 1 tree (the
    "render only" polygons neither game loads: zone pieces off the paths, see levelport.port_zone); the first five
    point roles of the first object
    that has them. extras = [(scene, placement matrix)]: parts the game attaches at run time (wheels, tracks, guns),
    merged in where they hang (their points aren't kept). keep(object) -> False leaves one of the scene's own objects
    out (port_vehicle: the parts that become their own objects)."""
    by_tex = {}
    tris_out = []
    points = {}
    skipped = {}
    shadow = evo_write.Part("Default", [], [], [], [], None)
    items = [(o, o.world, True) for o in scene.objects] +         [(o, evo.mat_mul(place, o.world), False) for es, place in extras for o in es.objects]
    for o, w, own in items:
        if own and keep is not None and not keep(o):
            continue
        if o.role == "shadow":
            # shadow meshes (#SHA_...): gathered into one; the target adds it as the model's shadow object
            for m in o.node.meshes:
                base = len(shadow.positions)
                shadow.positions += [tuple(evo.mat_apply(w, p)) for p in m.positions]
                shadow.normals += [(0.0, 1.0, 0.0)] * len(m.positions)
                shadow.uvs += [(0.0, 0.0)] * len(m.positions)
                shadow.triangles += [(a + base, b + base, c + base) for a, b, c in m.triangles]
            continue
        if o.role != "mesh":
            if own:
                skipped[o.role] = skipped.get(o.role, 0) + 1
            continue
        for m in o.node.meshes:
            if not m.triangles or getattr(m, "invisible", False):
                # invisible buffers (untextured, VBUF flag 0x04 without 0x01: DS2's camera blockers, collision-only
                # faces in its zones) aren't drawn by their game - drawn here they smeared whatever texture was bound
                continue
            tex = m.texture if m.texture and m.texture.lower() not in ("default", "untextured") else "Default"
            key = tex if tex == "Default" else (prefix + tex)[:63]
            part = by_tex.setdefault(key, evo_write.Part(key, [], [], [], [], []))
            base = len(part.positions)
            normals = m.normals if len(m.normals) == len(m.positions) else _normals(m.positions, m.triangles)
            for i, p in enumerate(m.positions):
                part.positions.append(tuple(evo.mat_apply(w, p)))
                n = evo.mat_apply(w, normals[i], 0.0)
                length = math.sqrt(sum(x * x for x in n)) or 1.0
                part.normals.append((n[0] / length, n[1] / length, n[2] / length))
                part.uvs.append(tuple(m.uvs[i]) if i < len(m.uvs) else (0.0, 0.0))
                part.colours.append(_colour(m.colours[i]) if i < len(m.colours) else 0xFFFFFFFF)
            part.triangles += [(a + base, b + base, c + base) for a, b, c in m.triangles]
        for u, tree in getattr(o.node, "trees", []) or []:
            if not tree or not (u == 0 or (render_trees and u == 1)):
                continue
            root = bsp.read(tree)
            for poly in bsp.polygons(root) if root else []:
                verts = [tuple(evo.mat_apply(w, v)) for v in poly.verts]
                # the u 1 tree repeats the u 0 polygons, split finer: a second layer on the same surface doubles
                # the contacts (vehicles shuddered) - only polygons no u 0 polygon of the scene covers are added
                if u == 1 and _covered(scene, verts):
                    continue
                for k in range(1, len(verts) - 1):
                    tris_out.append(((verts[0], verts[k], verts[k + 1]), poly.surface & 0x3FF))
        roles = getattr(o.node, "point_roles", ()) or () if own else ()
        for r, idx in enumerate(roles[:5]):
            if 0 <= idx < len(o.node.points) and evo.POINT_ROLES[r] not in points:
                points[evo.POINT_ROLES[r]] = tuple(evo.mat_apply(w, o.node.points[idx]))
    for p in by_tex.values():
        if all(c == 0xFFFFFFFF for c in p.colours):
            p.colours = None
    notes = []
    if skipped:
        notes.append("left out: " + ", ".join("%d %s object(s)" % (n, r) for r, n in sorted(skipped.items())))
    return list(by_tex.values()), tris_out, points, notes, (shadow if shadow.triangles else None)


def dds_problem(data):
    """None when the target (Desert Storm, Direct3D 8) can draw this texture file, else why not."""
    if data[:4] != b"DDS " or len(data) < 128:
        return None if data[:2] in (b"BM", b"\x89P") or data[:1] == b"\0" else "not a DDS / TGA / BMP / PNG file"
    h, w = struct.unpack_from("<2I", data, 12)
    flags, fourcc = struct.unpack_from("<I4s", data, 80)
    if flags & 4 and fourcc not in DS1_DDS:
        return "DDS format %s (Desert Storm draws DXT1-5 and plain RGB(A))" % fourcc.decode("latin-1", "replace")
    if max(w, h) > 2048:
        return "%dx%d - over 2048 (Desert Storm's limit with DesertStormFix)" % (w, h)
    return None


_GENERIC_WORDS = {"C04", "MPD", "CIV", "DEAD", "DESTROYED", "DAMAGED", "CLOSED", "OPEN", "SAS", "US", "IR", "RU", "GE",
                  "WITH", "THE", "LEFT", "RIGHT", "DS", "V", "SOV", "CHIN", "WPN", "RIFLE", "POLY", "LOWPOLY",
                  "GEN"}


def _placeholder(name, data, game="cg"):
    """True for a stand-in texture the game swaps at run time (Global Storm cars: C04_V_TextureOverride, 16 x 16).
    The size rule is Global Storm's only: other games have real small textures (The Great Escape's steering wheel and
    track links, 32 px)."""
    if "OVERRIDE" in name.upper():
        return True
    if game == "cg" and data and data[:4] == b"DDS " and len(data) >= 20:
        h, w = struct.unpack_from("<2I", data, 12)
        return max(w, h) <= 32
    return False


def _variant_texture(src, model, placeholder=""):
    """(texture, other variants) named after the model - C04_ISUZUTRUCK_CIV -> C04_V_ISUZUTRUCK_GREEN (RED) - for a
    model whose own texture is a placeholder; (None, []) when nothing is named so. Names are compared without
    underscores and by shrinking prefixes of the model's words (C04_MTLBANDTURRET -> MTLB -> C04_V_MT_LB_GREEN); a
    wheel / track / turret placeholder takes a texture of that kind (C04_Wheel_Type_L_S -> C04_V_MTLB_WHEELS_GREEN),
    any other one a body texture; the game's vehicle paints (C04_V_...) come first."""
    words = sorted((w for w in model.upper().split("_") if w not in _GENERIC_WORDS and len(w) > 2), key=len,
                   reverse=True)
    names = [n for n in src.names(".DDS") if not n.endswith(("_BUMP", "_NORMAL", "_SPEC", "_GLOSS"))]
    compact = {n: n.replace("_", "") for n in names}
    ph = placeholder.upper()
    kind = next((k for k in ("WHEEL", "TRACK", "TURRET") if k in ph), None)
    hits = []
    for w in words:
        for size in range(len(w), 3, -1):
            key = w[:size]
            hits = [n for n in names if key in compact[n] and n != ph]
            if hits:
                break
        if hits:
            break
    body = [n for n in hits if not any(k in n for k in ("WHEEL", "TRACK", "TURRET", "DEAD", "BURNT"))] or hits
    # a kind with no texture of its own (Global Storm's BMP-1 track) is painted from the body's sheet
    hits = ([n for n in hits if kind in n] or body) if kind else body
    hits.sort(key=lambda n: ("_V_" not in n, n))
    return (hits[0], hits[1:]) if hits else (None, [])


_HAND_FIX = {}


def hand_fix(src, dst):
    """The matrix that turns a gun made for the source game's hand connector into one for the target's: both games'
    connectors relative to the right hand bone (BIP01 R HAND - the Biped hand's frame is the same in every game), from
    their soldier skeletons: target^-1 x source. Vietnam / Global Storm guns run along +Z, the older games' along +Y:
    without it a ported gun lies across the hand."""
    key = (src.game.id, dst.game.id)
    if key not in _HAND_FIX:
        rels = []
        for g in (dst, src):
            path = g.scene_file(SOLDIER[g.game.id])
            if not path:
                raise PortError("%s's soldier %s isn't in its files" % (g.game.short, SOLDIER[g.game.id]))
            with g.current():
                sc = evo.load(path)
            by = {o.name.upper(): o for o in sc.objects if o.name}
            hand, conn = by.get("BIP01 R HAND"), by.get(GUN_CONNECTOR[g.game.id])
            if hand is None or conn is None:
                raise PortError("%s's soldier has no %s on BIP01 R HAND" % (g.game.short, GUN_CONNECTOR[g.game.id]))
            rels.append(evo.mat_mul(evo.mat_invert_affine(hand.world), conn.world))
        _HAND_FIX[key] = evo.mat_mul(evo.mat_invert_affine(rels[0]), rels[1])
    return _HAND_FIX[key]


def base_picture(dst, weapon_id):
    """(w, h) of a target weapon's HUD / inventory picture (Desert Storm: GWInt.img image named by Weaps.txt column 5,
    272-byte entries from 284 {u16 page, name[256], pad, x, y, w, h, offset x, y}), or None."""
    table = (dst.get("WEAPS.TXT") or b"").decode("latin-1")
    row = next((r.split(",") for r in table.splitlines() if r.split(",")[0].strip().lower() == weapon_id.lower()), None)
    img = dst.get("GWINT.IMG")
    if not row or len(row) < 5 or not img:
        return None
    for i in range((len(img) - 284) // 272):
        o = 284 + i * 272
        if img[o + 2:o + 258].partition(bytes(1))[0].decode("latin-1").lower() == row[4].strip().lower():
            return struct.unpack_from("<2H", img, o + 264)
    return None


def render_picture(parts, textures, w, h, sample=4):
    """A HUD / inventory picture of a gun (Desert Storm's layout: side on, muzzle to the left, transparent around it)
    rendered from its parts (model space, barrel along +y, up +z): depth-buffered triangles, colour from each part's
    texture at the pixel's uv, lit from the front and above; drawn `sample` times larger and averaged down. RGBA bytes."""
    tex = {}
    for name, data in textures.items():
        stem = os.path.splitext(name)[0].upper()
        try:
            tex[stem] = images.decode_dds(data) if data[:4] == b"DDS " else images.decode_tga(data)
        except Exception:                             # noqa: BLE001 - an unreadable texture draws grey
            pass
    pts = [v for p in parts for v in p.positions]
    if not pts:
        return bytes(w * h * 4)
    us = [-v[1] for v in pts]
    vs = [-v[2] for v in pts]
    W, H = w * sample, h * sample
    span_u, span_v = (max(us) - min(us)) or 1.0, (max(vs) - min(vs)) or 1.0
    k = min((W - 2 * sample) / span_u, (H - 2 * sample) / span_v)
    ou = (W - span_u * k) / 2 - min(us) * k
    ov = (H - span_v * k) / 2 - min(vs) * k
    depth = [1e30] * (W * H)
    colour = [None] * (W * H)
    light = (-0.6, 0.0, 0.8)                         # from the viewer's side (-x) and above
    ln = math.sqrt(sum(c * c for c in light))
    light = tuple(c / ln for c in light)
    for p in parts:
        t = tex.get(p.texture.upper())
        for a, b, c in p.triangles:
            A, B, C = p.positions[a], p.positions[b], p.positions[c]
            sx = [-A[1] * k + ou, -B[1] * k + ou, -C[1] * k + ou]
            sy = [-A[2] * k + ov, -B[2] * k + ov, -C[2] * k + ov]
            area = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0])
            if abs(area) < 1e-9:
                continue
            e1 = [B[i] - A[i] for i in range(3)]
            e2 = [C[i] - A[i] for i in range(3)]
            n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            nl = math.sqrt(sum(x * x for x in n)) or 1.0
            shade = 0.6 + 0.6 * abs(sum(n[i] / nl * light[i] for i in range(3)))
            uv = [p.uvs[a] if a < len(p.uvs) else (0, 0), p.uvs[b] if b < len(p.uvs) else (0, 0),
                  p.uvs[c] if c < len(p.uvs) else (0, 0)]
            x0, x1 = max(0, int(min(sx))), min(W - 1, int(max(sx)) + 1)
            y0, y1 = max(0, int(min(sy))), min(H - 1, int(max(sy)) + 1)
            for y in range(y0, y1 + 1):
                py = y + 0.5
                for x in range(x0, x1 + 1):
                    px = x + 0.5
                    w0 = ((sx[1] - px) * (sy[2] - py) - (sx[2] - px) * (sy[1] - py)) / area
                    w1 = ((sx[2] - px) * (sy[0] - py) - (sx[0] - px) * (sy[2] - py)) / area
                    w2 = 1.0 - w0 - w1
                    if w0 < 0 or w1 < 0 or w2 < 0:
                        continue
                    d = w0 * A[0] + w1 * B[0] + w2 * C[0]      # nearer = smaller x (the viewer is at -x)
                    i = y * W + x
                    if d >= depth[i]:
                        continue
                    depth[i] = d
                    if t:
                        tw, th, tp = t
                        u = (w0 * uv[0][0] + w1 * uv[1][0] + w2 * uv[2][0]) % 1.0
                        v = (w0 * uv[0][1] + w1 * uv[1][1] + w2 * uv[2][1]) % 1.0
                        o = (min(th - 1, int(v * th)) * tw + min(tw - 1, int(u * tw))) * 4
                        r, g, bl = tp[o], tp[o + 1], tp[o + 2]
                    else:
                        r = g = bl = 128
                    colour[i] = (r * shade, g * shade, bl * shade)
    out = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            acc = [0.0, 0.0, 0.0]
            hits = 0
            for dy in range(sample):
                for dx in range(sample):
                    c = colour[(y * sample + dy) * W + x * sample + dx]
                    if c is not None:
                        hits += 1
                        for j in range(3):
                            acc[j] += c[j]
            if hits:
                o = (y * w + x) * 4
                out[o:o + 4] = bytes([min(255, int(acc[j] / hits)) for j in range(3)] +
                                     [int(255 * hits / (sample * sample))])
    # a dark 1-pixel outline round the gun, as the game's own pictures have (they read on any HUD colour)
    edge = bytearray(out)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            if out[o + 3] >= 128:
                continue
            near = max((out[((y + dy) * w + x + dx) * 4 + 3] for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))
                        if 0 <= y + dy < h and 0 <= x + dx < w), default=0)
            if near >= 128:
                edge[o:o + 4] = bytes((16, 16, 16, 230))
    return bytes(edge)


def _white_materials(data):
    """The model with every material's first colour white (opaque): Desert Storm's props have white materials (263 of
    Mission 1's 280), its generic prop BH_BODGEPLATE a blue-grey one that tinted every ported prop dark blue."""
    out, p = bytearray(data), 0
    while True:
        p = out.find(b"MATL", p)
        if p < 0:
            return bytes(out)
        n = struct.unpack_from("<I", out, p + 4)[0]
        if 0 < n < 64:
            for k in range(n):
                # the white material Desert Storm's own props use (materials are found by hash: keeping the plate's
                # hash would let the shipped blue-grey entry win when the plate is loaded too)
                struct.pack_into("<I4f", out, p + 8 + k * 56, 0x8DE6CA26, 1.0, 1.0, 1.0, 1.0)
        p += 4


def port_model(src, dst, name, new_name=None, held=False, picture=None, template=None, fix=None,
               collision_only=False):
    """(model name, model bytes, {texture file name: bytes}, notes) for one source model ported onto dst's generic
    prop (collision_only: a model with collision but nothing to draw - an invisible blocker - comes over as a blocker
    instead of being refused) - or onto `template` (model bytes: its first render object takes the geometry, its points stay unless the
    source has its own, its other render objects are emptied). `fix`: a matrix applied to the source geometry first
    (a vehicle gun turned from the source vehicle's connector to the target's)."""
    data = src.get(name + ".EVO")
    if data is None:
        raise PortError("%s.EVO isn't in the %s files" % (name, src.game.short))
    static_note = None
    words = name.upper().split("_")
    for cand in (name.upper() + "_STATIC", "_".join(words[:-1]) + "_STATIC" if len(words) > 2 else ""):
        if cand and cand != name.upper():
            sdata = src.get(cand + ".EVO")
            if sdata is not None:
                # the game's own static version (Global Storm: C04_ISUZUTRUCK_STATIC - body, wheels, doors in one)
                data, static_note = sdata, "geometry from the game's static version %s (wheels and doors included)" % cand
                break
    scene = evo.Scene(evo.read(data))
    if any(o.role == "skin" for o in scene.objects):
        raise PortError("%s is a character - characters go through Blender for now (File > Export onto the target's "
                        "body)" % name)
    prefix = src.game.id.upper() + "_"
    # a vehicle's wheels / tracks (.DYN) and guns (VEHICLE -> VEHWPNS -> WEAPS) are separate models the game hangs on
    # it at run time: merged in where they hang, so the static copy looks whole
    scene.name = name
    extras, added, missing = [], {}, []
    finder = _CacheFinder(src)
    with src.current():
        for pname, conn, pos, kind in evo.vehicle_parts(scene, finder):
            pdata = src.get(pname + ".EVO") if src.files is not None else None
            ppath = None if pdata is not None else finder.find(pname, (".evo",))
            if pdata is None and ppath:
                with open(ppath, "rb") as f:
                    pdata = f.read()
            if pdata is None:
                missing.append(pname)
                continue
            place = scene.objects[conn].world if conn is not None else                 evo.mat_from_trs(pos, (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)) if pos is not None else evo.mat_identity()
            extras.append((evo.Scene(evo.read(pdata)), place))
            added[kind] = added.get(kind, 0) + 1
        clip = src.gun_clip(name) if any(o.name.upper() == "CONNECTOR_CLIP" for o in scene.objects) else None
        if clip:
            conn = next(o for o in scene.objects if o.name.upper() == "CONNECTOR_CLIP")
            cdata = src.get(clip + ".EVO")
            if cdata is None:
                missing.append(clip)
            else:
                extras.append((evo.Scene(evo.read(cdata)), conn.world))
                added["magazine"] = added.get("magazine", 0) + 1
        conns = {o.name.upper(): o for o in scene.objects if o.name}
        for gun, conn in ([] if static_note else src.vehicle_guns(name, conns)):
            gdata = src.get(gun + ".EVO")
            if gdata is None:
                missing.append(gun)
                continue
            extras.append((evo.Scene(evo.read(gdata)), conns[conn].world))
            added["gun"] = added.get("gun", 0) + 1
        for pname, pos in ([] if static_note else src.physics_attached(name)):
            pdata = src.get(pname + ".EVO")
            if pdata is None:
                missing.append(pname)
                continue
            extras.append((evo.Scene(evo.read(pdata)), evo.mat_from_trs(pos, (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))))
            kind = "wheel" if "WHEEL" in pname else "door" if "DOOR" in pname else "part"
            added[kind] = added.get(kind, 0) + 1
    parts, tris, points, notes, shadow = static_model_parts(scene, prefix, extras)
    if not parts and not (collision_only and tris):
        raise PortError("%s has no textured geometry" % name)
    if not parts:
        notes.append("collision only (an invisible blocker): %d triangle(s), nothing drawn" % len(tris))
    if held:
        fix = hand_fix(src, dst)     # a gun held in the hand: moved from the source game's hand connector onto the target's
    if fix is not None:
        for p in parts:
            p.positions = [tuple(evo.mat_apply(fix, v)) for v in p.positions]
            p.normals = [tuple(evo.mat_apply(fix, n, 0.0)) for n in p.normals]
        tris = [(tuple(tuple(evo.mat_apply(fix, v)) for v in t), surf) for t, surf in tris]
        if shadow is not None:
            shadow.positions = [tuple(evo.mat_apply(fix, v)) for v in shadow.positions]
        points = {r: tuple(evo.mat_apply(fix, v)) for r, v in points.items()}
        angle = math.degrees(math.acos(max(-1.0, min(1.0, (fix[0][0] + fix[1][1] + fix[2][2] - 1) / 2))))
        if held and (angle > 1.0 or math.sqrt(sum(fix[i][3] ** 2 for i in range(3))) > 0.5):
            notes.append("moved from %s's %s onto %s's %s (turned %.0f degrees)" % (
                src.game.short, GUN_CONNECTOR[src.game.id], dst.game.short, GUN_CONNECTOR[dst.game.id], angle))
    meshes = [o for o in scene.objects if o.role == "mesh"]
    animated = template is None and not extras and meshes and all(o.node.kind == 1 for o in meshes) and \
        ANIMATED_PROP.get(dst.game.id)
    if animated:
        # an animated prop: every frame comes along (parts keep the source meshes' vertex order), onto the target's
        # own animated prop - a kind-1 object the game plays frame by frame
        by_tex = {}
        for o in meshes:
            for m in o.node.meshes:
                frames = [([tuple(evo.mat_apply(o.world, v)) for v in pos],
                           [tuple(evo.mat_apply(o.world, n, 0.0)) for n in nrm]) for pos, nrm in m.frames]
                by_tex.setdefault((prefix + m.texture)[:63] if m.texture else "Default", []).append(
                    (len(m.positions), frames))
        for p in parts:
            got = by_tex.get(p.texture, [])
            if len(got) == 1 and got[0][0] == len(p.positions):
                p.frames = got[0][1]
            else:
                animated = None
        if animated:
            template = dst.get(animated + ".EVO")
            notes.append("animated: %d frame(s) on %s's %s" % (1 + max(len(p.frames or []) for p in parts),
                                                              dst.game.short, animated))
        else:
            for p in parts:
                p.frames = None
            notes.append("animated in %s - a still copy (its frames don't map onto one mesh each)" % src.game.short)
    if static_note:
        notes.insert(0, static_note)
    if added:
        notes.append("attached parts merged in: " + ", ".join("%d %s(s)" % (n, k) for k, n in sorted(added.items())))
    if missing:
        notes.append("attached parts not found: " + ", ".join(missing))
    if any(o.parent is not None for o in scene.objects if o.role == "mesh") or extras:
        notes.append("a static copy: moving parts, wheels and guns are fixed in their rest pose")
    tdata = template if template is not None else dst.get(GENERIC_PROP[dst.game.id] + ".EVO")
    if tdata is None:
        raise PortError("the %s generic prop %s isn't in its files" % (dst.game.short, GENERIC_PROP[dst.game.id]))
    if template is None:
        tdata = _white_materials(tdata)
    tscene = evo.Scene(evo.read(tdata))
    target = next(o for o in tscene.objects if o.role == "mesh")
    inv = evo.mat_invert_affine(target.world)
    for p in parts + ([shadow] if shadow is not None else []):
        p.positions = [tuple(evo.mat_apply(inv, v)) for v in p.positions]
        p.normals = [tuple(evo.mat_apply(inv, n, 0.0)) for n in p.normals]
        if getattr(p, "frames", None):
            p.frames = [([tuple(evo.mat_apply(inv, v)) for v in pos], [tuple(evo.mat_apply(inv, n, 0.0)) for n in nrm])
                        for pos, nrm in p.frames]
    if shadow is not None:
        notes.append("shadow mesh: %d triangle(s) from the source's shadow objects%s" % (
            len(shadow.triangles), " - over 100, the game may skip it as a caster" if len(shadow.triangles) > 100 else ""))
    converted = {}
    coll = []
    for (a, b, c), s in tris:
        new, old = bsp.convert_surface(s, dst.game.id)
        if old:
            converted[old] = bsp.surface_name(new)
        coll.append(((tuple(evo.mat_apply(inv, a)), tuple(evo.mat_apply(inv, b)), tuple(evo.mat_apply(inv, c))), new))
    if converted:
        notes.append("surfaces %s has no row for: %s" % (dst.game.short, ", ".join(
            "%s -> %s" % kv for kv in sorted(converted.items()))))
    if not tris:
        notes.append("no collision in the source - none written")
    pts = {target.index: {r: tuple(evo.mat_apply(inv, v)) for r, v in points.items()}} if points else None
    # <SOURCE>_<name>, unless the source's own name already starts so (The Great Escape's GE_NL17_...)
    model = (new_name or (name if name.upper().startswith(prefix) else prefix + name))[:63].upper()
    textures = _texture_files(src, dst, parts, name, prefix, notes)
    warn = []
    new_parts = {target.index: parts}
    if template is not None:
        # the template's other render objects go (a base gun's second barrel ...); a template with a shadow object of
        # its own keeps it (a shadow is only added to a model without one)
        new_parts.update({o.index: [] for o in tscene.objects if o.role == "mesh" and o is not target})
        has_shadow = any(o.role == "shadow" for o in tscene.objects)
        if has_shadow and shadow is not None:
            sha = next(o for o in tscene.objects if o.role == "shadow")
            sinv = evo.mat_invert_affine(sha.world)
            shadow.positions = [tuple(evo.mat_apply(sinv, evo.mat_apply(target.world, v))) for v in shadow.positions]
            new_parts[sha.index] = [shadow]
            shadow = None
    with dst.current():
        out = evo_write.rebuild(tdata, new_parts, model_name=model, warnings=warn,
                                collision={target.index: coll}, points=pts,
                                add_shadow=[shadow] if shadow is not None else None)
    notes += warn
    if points:
        notes.append("points kept: " + ", ".join(sorted(points)))
    if picture:
        # a HUD / inventory picture rendered from the model at the size of the target weapon's own
        pw, ph = picture
        px = render_picture(parts, textures, pw, ph)
        textures[model + "_ICON.png"] = images.encode_png(pw, ph, px, True)
        notes.append("HUD / inventory picture %s_ICON.png (%d x %d) rendered from the model" % (model, pw, ph))
    return model, out, textures, notes


def _has_alpha(data):
    """A texture with transparency: DXT3 / DXT5 / 32-bit uncompressed DDS, or a 32-bit TGA."""
    if data[:4] == b"DDS ":
        four = data[84:88]
        return four in (b"DXT2", b"DXT3", b"DXT4", b"DXT5") or (
            four == b"\0\0\0\0" and struct.unpack_from("<I", data, 88)[0] == 32 and
            struct.unpack_from("<I", data, 104)[0] != 0)
    return len(data) > 18 and data[2] in (2, 10) and data[16] == 32


def _texture_files(src, dst, parts, name, prefix, notes):
    """{texture file: bytes} for the parts' textures (prefixed names), placeholders swapped for the model's paint."""
    textures = {}
    for p in parts:
        if p.texture == "Default":
            continue
        orig = p.texture[len(prefix):]
        if _placeholder(orig, src.get(orig + ".DDS"), src.game.id):
            # the game puts the real texture on at run time (Global Storm vehicles): take one named after the model
            variant, others = _variant_texture(src, name, orig)
            if variant:
                notes.append("%s is a placeholder the game replaces at run time - used %s%s" % (
                    orig, variant, " (other variants: %s)" % ", ".join(others) if others else ""))
                orig = variant
                p.texture = (prefix + variant)[:63]
            else:
                notes.append("%s is a placeholder the game replaces at run time - no texture named after %s found"
                             % (orig, name))
        for ext in (".DDS", ".TGA"):
            tex = src.get(orig + ext)
            if tex is not None:
                problem = dds_problem(tex)
                if problem:
                    notes.append("texture %s: %s" % (orig, problem))
                if _has_alpha(tex):
                    # Desert Storm draws a texture's holes only when the mesh says so (VBUF flags 0x03, as on its
                    # barbed wire, rotor blades, the fuel truck's Ural03); Desert Storm II and the others go by the
                    # texture (its chain-link fences came out as bare posts)
                    p.extra_flags |= 0x03
                textures[p.texture + ext] = tex
                break
        else:
            notes.append("texture %s not found in the %s files" % (orig, src.game.short))
    return textures


_SEAT_WORDS = {"DRIVER", "PASSENGER", "GUNNER", "COMMANDER", "MOUNT", "WEAPON", "TURRET", "CANNON", "OPERATOR", "MG",
               "LOADER"}
# role names one game uses for another's: Global Storm's turret is Desert Storm's gun mount, The Great Escape's tanks
# have a cannon gunner and an MG operator where Desert Storm has gunners 1 and 2
_SEAT_ALIASES = {"TURRET": ("MOUNT", 0), "CANNON_GUNNER": ("GUNNER", 0), "MG_OPERATOR": ("GUNNER", 1),
                 "EXIT_CANNON_GUNNER": ("EXIT_GUNNER", 0), "EXIT_MG_OPERATOR": ("EXIT_GUNNER", 1)}


def _connector_key(name):
    """CONNECTOR_PASSENGER01 / CONNECTOR_PASSENGER1 -> ("PASSENGER", 1); CONNECTOR_EXIT_DRIVER -> ("EXIT_DRIVER", 1):
    the games name a vehicle's seats, exits and gun mounts alike, numbered or not. A model tag in front of the role
    is dropped (The Great Escape: CONNECTOR_P3J_DRIVER -> DRIVER, CONNECTOR_EXIT_P3J_DRIVER -> EXIT_DRIVER)."""
    n = name.upper()
    n = n[len("CONNECTOR_"):] if n.startswith("CONNECTOR_") else n
    m = re.match(r"(.*?)_?(\d*)$", n)
    words = m.group(1).split("_")
    lead = ["EXIT"] if words[:1] == ["EXIT"] else []
    rest = words[len(lead):]
    while len(rest) > 1 and rest[0] not in _SEAT_WORDS and any(w in _SEAT_WORDS for w in rest[1:]):
        rest = rest[1:]                              # a model tag (P3J_, M2A1_ ...) before the role
    base = "_".join(lead + rest)
    number = int(m.group(2) or 1)
    if base in _SEAT_ALIASES:
        base, extra = _SEAT_ALIASES[base]
        number += extra
    return base, number


MAX_WHEELS = 18          # Desert Storm: wheel instance slots per vehicle (vehicle +0x2AF8)


def vehicle_stat_lines(src, dst, model, base_stat):
    """(.vehicle "stat column" lines, notes) from the source vehicle drawn with `model`: its VStat.txt row (VEHICLE.TXT
    column 4 in every game) against the base's, column by column. A column is taken when it holds the same value in
    90 % of the stat rows both games have (Desert Storm II: VStat = Desert Storm's + 6 at the end); with too few rows
    in common (The Great Escape) nothing is taken - said in the report, not guessed. Column 13 (engine bank) never."""
    if not hasattr(src, "vehicle_rows") or src.game.id == dst.game.id:
        return [], []
    vrow = next((r for r in src.vehicle_rows() if len(r) > 4 and r[1].upper() == model.upper()), None)
    if vrow is None:
        return [], []
    sstats = {r[0].upper(): r for r in _table(src, "VSTAT.TXT")}
    dstats = {r[0].upper(): r for r in _table(dst, "VSTAT.TXT")}
    srow, brow = sstats.get(vrow[4].upper()), dstats.get(base_stat.upper())
    if not srow or not brow:
        return [], []
    common = [k for k in sstats if k in dstats]
    if len(common) < 10:
        diff = [j + 1 for j in range(1, min(len(srow), len(brow))) if srow[j] != brow[j] and j != 12]
        return [], ["stats: %s's %s not taken (too few vehicles in common to check what its VStat columns mean): "
                    "handles like the base" % (src.game.short, vrow[4])] if diff else []
    lines, taken, kept = [], [], []
    for j in range(1, min(len(srow), len(brow))):
        if j == 12 or srow[j] == brow[j] or not _num(srow[j]):
            continue
        agree = sum(1 for k in common if len(sstats[k]) > j and len(dstats[k]) > j and sstats[k][j] == dstats[k][j])
        if agree >= 0.9 * len(common):
            lines.append("stat column %d = %s" % (j + 1, srow[j]))
            taken.append(str(j + 1))
        else:
            kept.append(str(j + 1))
    notes = []
    if taken:
        notes.append("stats from %s's %s: VStat columns %s" % (src.game.short, vrow[4], ", ".join(taken)))
    if kept:
        notes.append("VStat columns kept as the base's (mean something else in %s): %s" % (src.game.short,
                                                                                          ", ".join(kept)))
    return lines, notes


def _dyn_fit(template_dyn, wheels, model_of, radius_of, tracks=None):
    """A .DYN for the ported wheels: the base vehicle's file with its wheel lines rewritten - per source wheel the
    base's line of the same end (front / back) and side as the model (its drive / steer / suspension numbers), with
    the wheel's radius, position (x, y, z; the suspension rest height = y) and model; the header's wheelbase, track
    and wheel count follow. Wheel line: "f radius steer f x y z rest offset f f MODEL"."""
    lines = template_dyn.splitlines()

    def num(t):
        try:
            float(t)
            return True
        except ValueError:
            return False

    wheel_at = [i for i, ln in enumerate(lines) if len(ln.split()) >= 12 and not num(ln.split()[-1]) and
                all(num(x) for x in ln.split()[:-1])]
    if not wheel_at:
        raise PortError("the base vehicle's .DYN has no wheels to fit")
    tmpl = [lines[i].split() for i in wheel_at]
    tz = [float(t[6]) for t in tmpl]
    sz = [p[2] for _g, p in wheels]

    def along(z, lo, hi):
        return (z - lo) / (hi - lo) if hi > lo else 0.5

    out_wheels = []
    for g, (x, y, z) in wheels:
        # the base's line of the same side nearest along the hull (front / back - and on tracks the sprocket, road
        # wheel or idler line of that spot): its drive / steer / suspension numbers
        f = along(z, min(sz), max(sz))
        pick = min(tmpl, key=lambda t: ((float(t[4]) >= 0) != (x >= 0), abs(along(float(t[6]), min(tz), max(tz)) - f)))
        t = list(pick)
        r = radius_of[g]
        t[1], t[4], t[5], t[6], t[7] = "%.6f" % r, "%.6f" % x, "%.6f" % y, "%.6f" % z, "%.6f" % y
        t[-1] = model_of[g]
        out_wheels.append(" ".join(t) + " ")
    head_at = wheel_at[0] - 1
    head = lines[head_at].split()
    if len(head) == 1 and head[0].isdigit():
        lines[head_at] = "%d " % len(wheels)            # tracked: the wheel count stands alone
    elif len(head) >= 4 and all(num(x) for x in head[:4]):
        xs = [p[0] for _g, p in wheels]
        zs = [p[2] for _g, p in wheels]
        head[0], head[1], head[3] = "%.6f" % (max(zs) - min(zs)), "%.6f" % (max(xs) - min(xs)), str(len(wheels))
        lines[head_at] = " ".join(head) + " "
    if tracks:
        # tracked: "LEFT_MODEL RIGHT_MODEL width"
        for i, ln in enumerate(lines[:wheel_at[0]]):
            t = ln.split()
            if len(t) == 3 and not num(t[0]) and not num(t[1]) and num(t[2]):
                lines[i] = "%s %s %.6f " % tracks
    new = lines[:wheel_at[0]] + out_wheels + lines[wheel_at[-1] + 1:]
    return "\r\n".join(new) + "\r\n"


def _suffixed(model, suffix, limit=31):
    """<model><suffix> within the name limit, the model's name shortened first: the suffix must survive - cut after
    it, a 31-letter vehicle name (DS2_SAS_LANDROVER_WITH_MK19_SAW) gave its guns its own name and they overwrote it."""
    return model[:limit - len(suffix)] + suffix


def port_vehicle(src, dst, name, like, new_name=None):
    """A drivable copy of a source vehicle on a Desert Storm vehicle (`like` = its VEHICLE.TXT id): the base model's
    objects (seats, exits, gun mount, hit boxes, collision helper) with the source body as its body, the source's own
    seat / exit / mount connectors moved onto it (matched by name: DRIVER01, PASSENGER01, EXIT_DRIVER ...), the
    source wheels as their own models on a .DYN fitted from the base's, and a starter .vehicle based on `like` (its
    handling, guns, sounds; mods.cpp). Tracked vehicles (base and source both on tracks) get the source's animated
    track meshes as the two track models of the .DYN. Returns (model, {file name: bytes}, notes)."""
    rows = [ln.split(",") for ln in (dst.get("VEHICLE.TXT") or b"").decode("latin-1").splitlines() if "," in ln]
    row = next((r for r in rows if r[0].strip().upper() == like.upper()), None)
    if row is None:
        words = [w for w in re.split(r"[_\W]+", like.upper()) if len(w) > 1 and w not in ("US", "UK", "IR", "VEH")]
        near = [r[0].strip() for r in rows if any(w in r[0].upper() for w in words)][:6]
        raise PortError("%s isn't a %s vehicle (VEHICLE.TXT)%s" % (like, dst.game.short, " - did you mean: " +
                        ", ".join(near) if near else " - e.g. US_VEH_HUM_VEE_M60, UK_VEH_SAS_LANDROVER"))
    base = row[1].strip().upper()
    bdata = dst.get(base + ".EVO")
    dyn = dst.get(base + ".DYN")
    if bdata is None or dyn is None:
        raise PortError("%s's model %s (and its .DYN) isn't in the %s files" % (like, base, dst.game.short))
    dyn = dyn.decode("latin-1")
    base_tracks = next((ln.split()[:2] for ln in dyn.splitlines() if len(ln.split()) == 3 and
                        not ln.split()[0].replace(".", "").replace("-", "").isdigit()), None)
    data = src.get(name + ".EVO")
    if data is None:
        raise PortError("%s.EVO isn't in the %s files" % (name, src.game.short))
    scene = evo.Scene(evo.read(data))
    scene.name = name
    prefix = src.game.id.upper() + "_"
    notes = []
    attached = src.physics_attached(name)
    wheels = [(g, p) for g, p in attached if "WHEEL" in g]
    with src.current():
        dyn_parts = evo.vehicle_parts(scene, _CacheFinder(src))
        wheels += [(g, p) for g, _c, p, kind in dyn_parts if kind == "wheel" and p]
        extras = []
        for g, p in attached:
            if "WHEEL" in g or "TRACK" in g:
                continue
            pdata = src.get(g + ".EVO")
            if pdata is not None:
                extras.append((evo.Scene(evo.read(pdata)), evo.mat_from_trs(p, (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))))
    if not wheels:
        raise PortError("%s has no wheels in its physics / .DYN file - can't make it drivable" % name)
    if len(wheels) > MAX_WHEELS:
        # Desert Storm keeps a vehicle's wheels in 18 slots (vehicle +0x2AF8): more overwrite what follows (the Panzer
        # III's 22 crashed the level load). The highest ones go - a tank's return rollers, which never touch the
        # ground - into the body as static parts, so they still show
        wheels.sort(key=lambda gp: gp[1][1])
        wheels, extra_wheels = wheels[:MAX_WHEELS], wheels[MAX_WHEELS:]
        for g, p in extra_wheels:
            wdata = src.get(g + ".EVO")
            if wdata is not None:
                extras.append((evo.Scene(evo.read(wdata)), evo.mat_from_trs(p, (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))))
        notes.append("%d wheel(s) over Desert Storm's %d built into the body (the highest: %s)" % (
            len(extra_wheels), MAX_WHEELS, ", ".join(sorted({g for g, _p in extra_wheels}))))
    # tracks: Vietnam / Global Storm physics files place them; Desert Storm II / The Great Escape's .DYN lists them,
    # modelled in place at the vehicle's origin
    src_tracks = [(g, p) for g, p in attached if "TRACK" in g] +         [(g, (0.0, 0.0, 0.0)) for g, _c, _p, kind in dyn_parts if kind == "track"]
    if bool(base_tracks) != bool(src_tracks):
        raise PortError("%s %s but %s %s - base it on a %s Desert Storm vehicle" % (
            name, "runs on tracks" if src_tracks else "has wheels", like,
            "runs on tracks" if base_tracks else "has wheels", "tracked" if src_tracks else "wheeled"))
    tscene = evo.Scene(evo.read(bdata))
    root = tscene.objects[0]

    def depth(o):
        d = 0
        while o.parent is not None:
            o, d = tscene.objects[o.parent], d + 1
        return d

    # which base connector goes where on the source: guns by what they are (the base's VEHWPNS -> WEAPS rows and the
    # source's weapon tables: the Abrams carries its cannon on WEAPON02 and a .50 cal on WEAPON01, the Arjun the
    # other way round), a gun mount where the matched gun's mount is, everything else by name
    sconn = [o for o in scene.objects if o.role == "connector" and o.name]
    own = {_connector_key(o.name): o for o in sconn}
    src_kind = src.gun_kinds(name, [o.name for o in sconn]) if hasattr(src, "gun_kinds") else {}
    # the base's guns in VehWpns order: (weapon id, gun model, connector, category) - several may share a connector
    # (Desert Storm's MILAN Land Rover hangs its .50 cal and its MILAN both on CONNECTOR_WEAPON01)
    base_guns = []
    for vrow in dst.table_rows(like.upper()):
        if len(vrow) > 2 and vrow[0].strip().upper() == like.upper() and vrow[2].strip().upper() == "WEAPON":
            w = vrow[1].strip().upper()
            for wrow in dst.table_rows(w):
                if wrow[0].strip().upper() == w and len(wrow) > 6 and wrow[6].strip().upper().startswith("CONNECTOR"):
                    base_guns.append((vrow[1].strip(), wrow[1].strip().upper(), wrow[6].strip().upper(),
                                      wrow[5].strip().upper()))
                    break
    base_kind = {c: k for _w, _m, c, k in base_guns}
    # the source's guns: (gun model, connector)
    if src.game.id in ("cg", "cv", "ds2", "ge"):
        src_guns = src.vehicle_guns(name, [o.name for o in sconn])
    else:
        with src.current():
            src_guns = [(g, scene.objects[c].name.upper()) for g, c, _p, kind in
                        evo.vehicle_parts(scene, _CacheFinder(src)) if kind in ("gun", "weapon") and c is not None]
    by_name = {o.name.upper(): o for o in sconn}
    conn_obj = {o.name.upper(): o for o in tscene.objects if o.role == "connector" and o.name}
    # gun by gun: a source gun of the same kind, else any one left; each pair gets its own base connector (the gun's,
    # else a free WEAPON connector of the base), moved onto the source gun's
    mate_of, used, claimed, gun_pairs = {}, set(), set(), []
    gun_conns = {c for _w, _m, c, _k in base_guns}
    # same kind first for every base gun, then the rest take what is left (in VehWpns order a TOW launcher would
    # otherwise take the Hanomag's only gun, an MG42, before the Bradley's chain gun could)
    picks, taken = {}, set()
    for i, (_w, _m, _c, kind) in enumerate(base_guns):
        hit = next(((g, c.upper()) for g, c in src_guns if c.upper() in by_name and c.upper() not in taken
                    and src_kind.get(c.upper()) == kind), None)
        if hit is not None:
            picks[i] = hit
            taken.add(hit[1])
    for i in range(len(base_guns)):
        if i not in picks:
            hit = next(((g, c.upper()) for g, c in src_guns if c.upper() in by_name and c.upper() not in taken), None)
            if hit is not None:
                picks[i] = hit
                taken.add(hit[1])
    for i, (wid, wmodel, bconn, kind) in enumerate(base_guns):
        pick = picks.get(i)
        bobj = conn_obj.get(bconn) if bconn not in claimed else None
        if bobj is None:
            bobj = next((o for n, o in sorted(conn_obj.items()) if _connector_key(n)[0] == "WEAPON" and
                         n not in claimed and n not in gun_conns), None)
        if pick is not None and bobj is not None:
            used.add(pick[1])
            claimed.add(bobj.name.upper())
            mate_of[bobj.index] = by_name[pick[1]]
        gun_pairs.append((wid, wmodel, bconn, bobj, pick[0] if pick is not None and bobj is not None else None))
    for o in tscene.objects:
        if o.role != "connector" or _connector_key(o.name or "")[0] != "MOUNT":
            continue
        # a mount follows the gun it carries: the matched gun's own mount on the source
        for c in tscene.objects:
            if c.parent == o.index and c.index in mate_of and mate_of[c.index].parent is not None:
                sp = scene.objects[mate_of[c.index].parent]
                if sp.role == "connector":
                    mate_of[o.index] = sp
                    break
    for o in tscene.objects:
        if o.role == "connector" and o.name and o.index not in mate_of and \
                (o.name.upper() not in gun_conns or not src_guns):
            hit = own.get(_connector_key(o.name))
            if hit is not None and hit.name.upper() not in used:
                mate_of[o.index] = hit
    # the turret: the base's render object hanging on a gun mount (the Abrams' TURRET on MOUNT01) takes the source's
    # objects on the matched mount, so it turns in game; the hull takes the rest
    turret, turret_src = None, None
    for o in sorted((x for x in tscene.objects if x.role == "mesh" and x.parent is not None), key=depth):
        p = tscene.objects[o.parent]
        if p.role == "connector" and _connector_key(p.name)[0] == "MOUNT" and p.index in mate_of:
            turret, turret_src = o, mate_of[p.index]
            break

    def under(o, anc):
        while o is not None:
            if o is anc:
                return True
            o = scene.objects[o.parent] if o.parent is not None else None
        return False

    on_turret = (lambda o: under(o, turret_src)) if turret_src is not None else (lambda o: False)
    parts, tris, _points, pnotes, shadow = static_model_parts(scene, prefix, extras, keep=lambda o: not on_turret(o))
    notes += [n for n in pnotes if "left out" in n]
    if not parts:
        raise PortError("%s has no textured geometry" % name)
    tparts, ttris, tshadow = [], [], None
    if turret is not None:
        tparts, ttris, _tp, _tn, tshadow = static_model_parts(scene, prefix, (), keep=on_turret)

    # connectors moved top down (a moved mount carries its gunner seat, guns and turret)
    moved = {}                                          # object index -> world translation added
    key_offsets, unmatched = {}, []
    for o in sorted((x for x in tscene.objects if x.role == "connector" and x.name), key=depth):
        inherited = moved.get(o.parent, (0.0, 0.0, 0.0)) if o.parent is not None else (0.0, 0.0, 0.0)
        mate = mate_of.get(o.index)
        if mate is None:
            moved[o.index] = inherited
            if _connector_key(o.name)[0] not in ("THERMAL_HOTSPOT", "EXHAUST", "AERIAL"):
                unmatched.append(o.name)
            continue
        want = evo.mat_apply(mate.world, (0.0, 0.0, 0.0))
        have = evo.mat_apply(o.world, (0.0, 0.0, 0.0))
        world = tuple(w - h for w, h in zip(want, have))
        moved[o.index] = world
        extra = tuple(a - b for a, b in zip(world, inherited))
        if max(abs(c) for c in extra) >= 0.5:
            pw = tscene.objects[o.parent].world if o.parent is not None else evo.mat_identity()
            key_offsets[o.index] = tuple(evo.mat_apply(evo.mat_invert_affine(pw), extra, 0.0))

    def placed(o):
        """o's world matrix once its connectors moved (render objects / helpers ride their parent)."""
        w = [list(r) for r in o.world]
        q = o
        while q is not None and q.index not in moved:
            q = tscene.objects[q.parent] if q.parent is not None else None
        d = moved.get(q.index, (0.0, 0.0, 0.0)) if q is not None else (0.0, 0.0, 0.0)
        for i in range(3):
            w[i][3] += d[i]
        return w

    def into(obj, plist, shad, triangles):
        inv = evo.mat_invert_affine(placed(obj))
        for p in plist:
            p.positions = [tuple(evo.mat_apply(inv, v)) for v in p.positions]
            p.normals = [tuple(evo.mat_apply(inv, n, 0.0)) for n in p.normals]
        out_tris = []
        for (a, b, c), surf in triangles:
            new, _old = bsp.convert_surface(surf, dst.game.id)
            out_tris.append(((tuple(evo.mat_apply(inv, a)), tuple(evo.mat_apply(inv, b)),
                              tuple(evo.mat_apply(inv, c))), new))
        return out_tris

    new_parts, collision = {}, {}
    collision[root.index] = into(root, parts, None, tris)
    new_parts[root.index] = parts
    # the base's other render objects go (the Humvee's gun carriage, the Abrams' hatch): the source has its own
    for o in tscene.objects[1:]:
        if o.role == "mesh":
            new_parts[o.index] = []
    if turret is not None and tparts:
        collision[turret.index] = into(turret, tparts, None, ttris)
        new_parts[turret.index] = tparts
        notes.append("turret: the %s model's objects on %s turn with %s's %s" % (
            src.game.short, turret_src.name, like, turret.name))
    for sha in (o for o in tscene.objects if o.role == "shadow"):
        owner = tscene.objects[sha.parent] if sha.parent is not None else root
        sh = tshadow if turret is not None and owner is turret else shadow if owner is root else None
        if sh is not None and sh.triangles:
            sinv = evo.mat_invert_affine(placed(sha))
            sh.positions = [tuple(evo.mat_apply(sinv, v)) for v in sh.positions]
            new_parts[sha.index] = [sh]
    if base_guns and src_guns:
        notes.append("guns: " + ", ".join("%s %s (%s) -> %s's %s" % (b.name, wid, kind, src.game.short,
                                                                       mate_of[b.index].name)
                                          for (wid, _m, _c, b, g), (_w, _mm, _cc, kind) in zip(gun_pairs, base_guns)
                                          if g is not None))
    model = (new_name or (name if name.upper().startswith(prefix) else prefix + name))[:31].upper()
    files = {}
    textures = _texture_files(src, dst, parts + tparts, name, prefix, notes)
    files.update(textures)
    warn = []
    with dst.current():
        out = evo_write.rebuild(bdata, new_parts, model_name=model, warnings=warn, collision=collision,
                                key_offsets=key_offsets)
    files[model + ".EVO"] = out
    notes += warn
    notes.append("on %s's %s: seats / exits / mount moved to the %s model's: %d of the base's connectors%s" % (
        like, base, src.game.short, len(key_offsets),
        "; kept where the base has them (the source has none): " + ", ".join(unmatched) if unmatched else ""))
    # wheels: each distinct model ported as its own, a .DYN fitted from the base's
    model_of, radius_of = {}, {}
    for k, g in enumerate(sorted({g for g, _p in wheels}), 1):
        wname = _suffixed(model, "_W%d" % k)
        wmodel, wdata, wtex, _wn = port_model(src, dst, g, wname)
        files[wmodel + ".EVO"] = wdata
        files.update(wtex)
        model_of[g] = wmodel
        ws = evo.Scene(evo.read(src.get(g + ".EVO")))
        ys = [evo.mat_apply(o.world, v)[1] for o in ws.objects if o.role == "mesh" for m in o.node.meshes
              for v in m.positions]
        radius_of[g] = (max(ys) - min(ys)) / 2.0 if ys else 40.0
    track_line = None
    if src_tracks:
        # each side's track: the source's animated track mesh (all its frames), placed where its physics file puts
        # it, on the base's own track model (kind 1, drawn at the vehicle's origin)
        tl = dst.get(base_tracks[0].upper() + ".EVO")
        if tl is None:
            raise PortError("%s's track model %s isn't in the %s files" % (like, base_tracks[0], dst.game.short))
        side_models, width = {}, 0.0
        for g, (x, y, z) in src_tracks:
            with src.current():
                tnodes = evo.read(src.get(g + ".EVO"))
            # the side from where the track mesh lies (Vietnam models its tracks in place: both at position 0)
            cx = [v[0] for nd in tnodes for mm in nd.meshes for v in mm.positions]
            side = "L" if x + (sum(cx) / len(cx) if cx else 0.0) < 0 else "R"
            if side in side_models:
                continue
            tparts = []
            for m in (mm for nd in tnodes for mm in nd.meshes):
                tex = (prefix + m.texture)[:63] if m.texture else "Default"
                move = lambda v: (v[0] + x, v[1] + y, v[2] + z)  # noqa: E731
                tparts.append(evo_write.Part(tex, [move(v) for v in m.positions], list(m.normals), list(m.uvs),
                                             list(m.triangles), None,
                                             [([move(v) for v in pos], list(nrm)) for pos, nrm in m.frames]))
                xs = [v[0] for v in m.positions]
                width = max(width, max(xs) - min(xs))
            tname = _suffixed(model, "_T%s" % side)
            with dst.current():
                files[tname + ".EVO"] = evo_write.rebuild(tl, {0: tparts}, model_name=tname)
            files.update(_texture_files(src, dst, tparts, name, prefix, notes))   # paint by the vehicle's name
            side_models[side] = tname
        if len(side_models) == 1:                       # one track given: the same model mirrored by position
            side_models["R" if "L" in side_models else "L"] = next(iter(side_models.values()))
        track_line = (side_models["L"], side_models["R"], width)
        notes.append("tracks %s / %s: the %s track meshes (%d frames) on %s's track model" % (
            side_models["L"], side_models["R"], src.game.short,
            1 + max(len(m.frames) for nd in tnodes for m in nd.meshes), base_tracks[0]))
    files[model + ".DYN"] = _dyn_fit(dyn, wheels, model_of, radius_of, track_line).encode("latin-1")
    notes.append("%d wheel(s) on a .DYN fitted from %s.DYN (%s)" % (len(wheels), base, ", ".join(
        "%s r %.0f" % (model_of[g], radius_of[g]) for g in sorted(model_of))))
    # the source's own guns: each base gun paired with a source gun (above) gets that gun - ported onto the base gun's
    # model (its muzzle point stays unless the gun has its own), turned from the source connector's axes to the base's
    # - as a .weapon based on the base's gun: same damage / sounds, the source's look, on the pair's base connector
    gun_lines, gun_notes = [], []
    for k, (wid, wmodel, bconn, o, g) in enumerate(gun_pairs, 1):
        if g is None or o is None:
            # the source has no gun for it: the base's would hang on the new body - left off (the plugin sets the
            # vehicle's gun slots to the guns that are left)
            gun_lines.append("gun      = %s -> -" % wid)
            gun_notes.append("%s's %s left off (no %s gun for it)" % (like, wid, src.game.short))
            continue
        mate = mate_of[o.index]
        tgun = dst.get(wmodel + ".EVO")
        if tgun is None:
            gun_notes.append("%s keeps %s (its model %s isn't in the %s files)" % (o.name, wid, wmodel, dst.game.short))
            continue
        # rotation only: the connectors stand at the same spot
        rb = [[o.world[i][j] for j in range(3)] + [0.0] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
        rs = [[mate.world[i][j] for j in range(3)] + [0.0] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
        fix = evo.mat_mul(evo.mat_invert_affine(rb), rs)
        gname = _suffixed(model, "_G%d" % k)
        try:
            gmodel, gdata, gtex, gn = port_model(src, dst, g, gname, template=tgun, fix=fix)
        except PortError as e:
            gun_notes.append("%s keeps %s (%s)" % (o.name, wid, e))
            continue
        files[gmodel + ".EVO"] = gdata
        files.update(gtex)
        # Weaps.txt column 7 = the connector: the pair's own when the base gun shared one with another
        conn_line = ["column 7      = %s" % o.name] if o.name.upper() != bconn else []
        files[gmodel + ".weapon"] = "\n".join([
            "# %s's gun %s from %s on %s's %s, ported by the ModKit porter." % (name, g, src.game.short, like, o.name),
            "# Fires like %s (damage, rate, sounds); the vehicle uses it through 'gun = ... -> %s'." % (wid, gmodel),
            "",
            "id            = %s" % gmodel,
            "based on      = %s" % wid,
            "display name  = %s" % _title(src.game, g),
            "model         = %s" % gmodel] + conn_line + [
            ""]).encode("latin-1")
        gun_lines.append("gun      = %s -> %s" % (wid, gmodel))
        gun_notes.append("%s: %s's %s on %s (fires like %s)" % (o.name, src.game.short, g, gmodel, wid))
    if gun_notes:
        notes.append("guns: " + "; ".join(gun_notes))
    stat_lines, snotes = vehicle_stat_lines(src, dst, name, row[4].strip())
    notes += snotes
    title = _title(src.game, name).split(" (")[0]
    files[model + ".vehicle"] = "\n".join([
        "# %s from %s, drivable, ported by the ModKit porter (conflict_ds_tools/porting.py)." % (name, src.game.short),
        "# Handling, seats, guns and sounds come from %s (change 'based on' to try another); see MAKING_MODS.md 4.6g."
        % like,
        "",
        "id       = %s" % model,
        "based on = %s" % like,
        "name     = %s (%s)" % (title[:30], src.game.short),
        "model    = %s" % model] + gun_lines + stat_lines + [
        ""]).encode("latin-1")
    return model, files, notes


# Units.txt columns per game: model columns, HStat row, experience, voice bank. Desert Storm: id, type, model, -1,
# stats, ..., experience 9, voice 10. Desert Storm II has a second model (its Delta Force look) at 3 and everything
# after one on; The Great Escape names voice banks without ".sbk". HStat.txt and UnitWpns.txt are Desert Storm's
# layout in both (The Great Escape's HStat has 4 more columns at the end).
UNIT_LAYOUT = {"ds2": {"models": (2, 3), "stat": 5, "experience": 10, "voice": 11},
               "ge": {"models": (2,), "stat": 4, "experience": 9, "voice": 10}}
def unit_skin(src, model):
    """The skin texture the source game's unit drawn with `model` wears, or None. The Great Escape names it in Units.txt
    column 12 (Feldgrau01) and keeps it as <skin>_<skin without its number> (Feldgrau01_Feldgrau) - the model's own
    look is another unit's (Goon_Soldier01)."""
    if src.game.id != "ge":
        return None
    for r in _table(src, "UNITS.TXT"):
        if len(r) > 12 and r[2].upper() == model.upper() and r[12] not in ("-1", ""):
            for cand in (r[12] + "_" + r[12].rstrip("0123456789"), r[12]):
                if src.get(cand + ".DDS") is not None:
                    return cand
            return None
    return None


UNIT_TYPES = ("SOLDIER", "CIVILIAN", "GOAT HERDER", "EMIR", "MISSILE EXPERT", "POW")
EXPERIENCE = ("ROOKIE", "GREEN", "TRAINED", "REGULAR", "VETERAN", "EXPERT", "PROFESSIONAL", "GUARD", "CRACK",
              "ELITE", "SPECIALIST", "SKILL ONE", "SKILL TWO", "SKILL THREE", "SKILL FOUR")


def _table(game_src, name):
    """A table's rows: comma-separated, or - for a table with no comma rows at all (Desert Storm II's VEHICLE.TXT,
    as vehicle_rows reads it) - space-separated."""
    lines = [ln for ln in (game_src.get(name) or b"").decode("latin-1").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if any("," in ln for ln in lines):
        return [[c.strip() for c in ln.split(",")] for ln in lines if "," in ln]
    return [ln.split() for ln in lines]


def unit_extras(src, dst, model, like_row):
    """(.unit lines, notes, {file: bytes}) from the source game's own unit drawn with `model`: its type, experience,
    health and other base stats (HStat columns where they differ from the base unit's), its kit (UnitWpns rows whose
    weapons / items Desert Storm has) and its voice (its speech bank as a pack - the games share the speech event
    ids: Desert Storm II's Bradley bank has all 46 of Desert Storm's and 3 more)."""
    lay = UNIT_LAYOUT.get(src.game.id)
    if lay is None or dst.game.id != "ds1":
        return [], [], {}
    units = _table(src, "UNITS.TXT")
    hits = [r for r in units if any(len(r) > c and r[c].upper() == model.upper() for c in lay["models"])]
    if not hits:
        return [], ["%s has no unit drawn with %s - type, health, kit and voice are %s's" % (
            src.game.short, model, like_row[0].strip())], {}
    row = hits[0]
    lines, notes, files = [], ["unit values from %s's %s%s" % (src.game.short, row[0], " (%d other unit(s) wear the "
                               "model too: %s)" % (len(hits) - 1, ", ".join(r[0] for r in hits[1:4])) if len(hits) > 1
                               else "")], {}
    if row[1].upper() in UNIT_TYPES and row[1].upper() != like_row[1].strip().upper():
        lines.append("type       = %s" % row[1])
    exp = row[lay["experience"]] if len(row) > lay["experience"] else ""
    if exp.upper() in EXPERIENCE and exp.upper() != like_row[9].strip().upper():
        lines.append("experience = %s" % exp)
    # base stats: HStat columns 2-18 (Desert Storm's), the first = health
    sstats = {r[0].upper(): r for r in _table(src, "HSTAT.TXT")}
    dstats = {r[0].upper(): r for r in _table(dst, "HSTAT.TXT")}
    srow = sstats.get(row[lay["stat"]].upper()) if len(row) > lay["stat"] else None
    brow = dstats.get(like_row[4].strip().upper())
    if srow and brow:
        # a column counts when it holds the same value in 90 % of the stat rows both games have (Desert Storm II: 23);
        # with too few in common (The Great Escape: none) only health is taken - the rest isn't guessed
        common = [k for k in sstats if k in dstats]
        if len(common) >= 10:
            trusted = {j for j in range(1, 18) if sum(1 for k in common if len(sstats[k]) > j and len(dstats[k]) > j
                                                     and sstats[k][j] == dstats[k][j]) >= 0.9 * len(common)} | {1}
        else:
            trusted = {1}
        changed, kept = [], []
        for j in range(1, min(18, len(srow), len(brow))):
            if srow[j] != brow[j] and _num(srow[j]) and j not in trusted:
                kept.append("column %d" % (j + 1))
                continue
            if srow[j] != brow[j] and _num(srow[j]):
                lines.append("health     = %s" % srow[j] if j == 1 else "stat column %d = %s" % (j + 1, srow[j]))
                changed.append("health" if j == 1 else "column %d" % (j + 1))
        if changed:
            notes.append("base stats (HStat %s): %s" % (srow[0], ", ".join(changed)))
        if kept:
            notes.append("kept %s's (not checked what they mean in %s): %s" % (like_row[0].strip(), src.game.short,
                                                                              ", ".join(kept)))
    # kit: what Desert Storm has of it
    have = {r[0].upper() for r in _table(dst, "WEAPS.TXT")} | {r[0].upper() for r in _table(dst, "INVEN.TXT")}
    kit, missing = [], []
    for r in _table(src, "UNITWPNS.TXT"):
        if r[0].upper() == row[0].upper() and len(r) > 1:
            (kit if r[1].upper() in have else missing).append(r)
    for r in kit:
        lines.append("item       = %s%s" % (r[1], ", %s" % r[3] if len(r) > 3 and r[3] not in ("", "0") else ""))
    if missing:
        notes.append("kit items %s doesn't have (port them with weapon lines and add them to the .unit): %s" % (
            dst.game.short, ", ".join(r[1] for r in missing)))
        if not kit:
            lines.append("# none of its kit exists in %s - it keeps %s's" % (dst.game.short, like_row[0].strip()))
    # voice
    bank = row[lay["voice"]] if len(row) > lay["voice"] else ""
    bank = bank[:-4] if bank.lower().endswith(".sbk") else bank
    if bank and bank != "-1":
        snd = ("%s_%s" % (src.game.id.upper(), bank)).upper()[:31]
        got = _bank_pack(src, bank, snd)
        if got:
            files["%s_VOICE.sch" % snd] = got[0]
            lines.append("voice      = %s" % snd)
            notes.append("voice: %s's speech bank %s (from %s) as %s" % (src.game.short, bank, got[1], snd))
        else:
            notes.append("voice: %s's speech bank %s isn't in its sound caches - speaks like %s" % (
                src.game.short, bank, like_row[0].strip()))
    return lines, notes, files


def unit_file(src, dst, name, model, like):
    """(file name, text, notes, base body, {file: bytes}) of a starter .unit for a ported body: a soldier type that
    starts from the named Desert Storm unit (Units.txt) and wears the ported model - with the source unit's own type,
    experience, stats, kit and voice where the source game's tables have them; refused when `like` isn't one of the
    target's units."""
    rows = [line.split(",") for line in (dst.get("UNITS.TXT") or b"").decode("latin-1").splitlines() if "," in line]
    by_id = {r[0].strip().upper(): r for r in rows}
    row = by_id.get(like.upper())
    if row is None:
        raise PortError("%s isn't a %s unit (Units.txt) - e.g. Iraqi_Conscript, Iraqi_Regular" % (like, dst.game.short))
    uid = model[:35]
    extra, enotes, efiles = unit_extras(src, dst, name, [c.strip() for c in row])
    text = "\n".join([
        "# %s from %s as a soldier type, ported by the ModKit porter (conflict_ds_tools/porting.py)." % (
            name, src.game.short),
        "# Starts from %s (type, health, skill, voice, kit - change any of them, MAKING_MODS.md 4.6i); place it in a" % (
            row[0].strip()),
        "# mission like the game's own (editor, or a records edit's scene = { unit = \"%s\" })." % uid,
        "",
        "id         = %s" % uid,
        "based on   = %s" % row[0].strip(),
        "model      = %s" % model] + extra + [
        ""])
    return uid + ".unit", text, ["starter %s.unit based on %s" % (uid, row[0].strip())] + enotes, row[2].strip(), \
        efiles


def _character_groups(src, scene, textures_of, prefix, rigid=True):
    """[(texture, SkinPart)] of a source character scene in model space: its skins, and with `rigid` the pieces hung
    on bones with a real texture (DS2's eyes, eyelids, teeth) with their bone's full weight."""
    groups = []
    for o in scene.objects:
        for m in o.node.meshes:
            if not m.triangles:
                continue
            tex = textures_of.get(id(m), (m.texture, None))[0] or ""
            normals = m.normals if len(m.normals) == len(m.positions) else _normals(m.positions, m.triangles)
            if o.role == "skin":
                weights = [[(scene.objects[b].name, w) for b, w in vw] for vw in scene.vertex_weights(o, m)]
                positions, normals = list(m.positions), list(normals)
            elif rigid and o.role == "mesh" and o.parent is not None and scene.objects[o.parent].role == "bone" and \
                    tex.lower() not in ("", "default", "untextured") and \
                    (src.get(tex + ".DDS") is not None or src.get(tex + ".TGA") is not None):
                # a rigid piece hung on a bone with a real texture (DS2's eyes, eyelids, teeth); marker meshes of the
                # skeleton file (Vietnam's MPD_SKELETON, no such texture) are left out
                bone = scene.objects[o.parent].name
                weights = [[(bone, 1.0)] for _ in m.positions]
                positions = [tuple(evo.mat_apply(o.world, p)) for p in m.positions]
                normals = [tuple(evo.mat_apply(o.world, nv, 0.0)) for nv in normals]
            else:
                continue
            uvs = list(m.uvs) if len(m.uvs) == len(positions) else [(0.0, 0.0)] * len(positions)
            part = evo_write.SkinPart(positions, normals, uvs, list(m.triangles), weights)
            groups.append(((prefix + tex)[:63] if tex else "", part))
    return groups


def _rest_of(scene):
    """(bone name -> world matrix, bone -> parent), upper-case, of a character scene."""
    rest = {o.name.upper(): o.world for o in scene.objects if o.name}
    parents = {o.name.upper(): scene.objects[o.parent].name.upper() for o in scene.objects
               if o.name and o.parent is not None}
    return rest, parents


def port_character(src, dst, name, soldier="Bradley", new_name=None, body=None, head=None, skin=None):
    """(model name, model bytes, {texture file: bytes}, .skin (file name, text), notes) for a source character put on
    a Desert Storm squad soldier's body: its skin (and rigid pieces hung on bones - DS2's eyes, eyelids, teeth - with
    their bone's full weight) moved from the source skeleton's rest pose onto the soldier's (crossgame.cross_repose,
    the games' Biped bones share their names), one skin part per texture (DS1 keeps up to 6 extras on spare
    connectors), the textures under <SOURCE>_<name>. The starter .skin makes it a uniform of that soldier that wears
    this body. `head`: a separate head model put on too (Global Storm keeps heads apart: C04_500_POLY wears
    C04_500_POLY_HEAD or C04_RIFLEMAN_HEAD ...); None = <name>_HEAD if the source has it, "-" = none. The head
    model brings its own eyes and teeth, so the body's are left out then."""
    path = src.scene_file(name)
    if not path:
        raise PortError("%s.EVO isn't in the %s files" % (name, src.game.short))
    with src.current():
        scene = evo.load(path)
        textures_of, fallback = evo.resolve_textures(scene, _CacheFinder(src), skin=skin or "")
    if not any(o.role == "skin" for o in scene.objects):
        raise PortError("%s isn't a character (no skinned body)" % name)
    prefix = src.game.id.upper() + "_"
    notes = []
    head_scene = head_textures = None
    if head != "-":
        hname = head or name + "_HEAD"
        hpath = src.scene_file(hname)
        if hpath:
            with src.current():
                head_scene = evo.load(hpath)
                head_textures, _hf = evo.resolve_textures(head_scene, _CacheFinder(src))
            if not any(o.role == "skin" for o in head_scene.objects):
                raise PortError("%s isn't a head model (no skinned mesh)" % hname)
            notes.append("head %s" % hname)
        elif head:
            raise PortError("head %s.EVO isn't in the %s files" % (head, src.game.short))
    groups = _character_groups(src, scene, textures_of, prefix, rigid=head_scene is None)
    sets = [(groups, scene)]
    if head_scene is not None:
        hgroups = _character_groups(src, head_scene, head_textures, prefix)
        groups = groups + hgroups
        sets.append((hgroups, head_scene))
    elif src.game.id == "cg" and head is None:
        heads = sorted(x for x in src.names(".EVO") if x.endswith("_HEAD"))
        notes.append("no head model: Global Storm keeps heads apart from bodies - add 'with <HEAD>' (%s)"
                     % ", ".join(heads[:8] + (["..."] if len(heads) > 8 else [])))
    body = body or SQUAD_BODIES[soldier]
    tpath = dst.scene_file(body)
    if not tpath:
        raise PortError("%s's body %s isn't in the %s files" % (soldier, body, dst.game.short))
    with dst.current():
        target = evo.load(tpath)
    target_rest = {o.name.upper(): o.world for o in target.objects if o.name}
    moved = {}
    for set_groups, set_scene in sets:              # each model by its own rest pose (body, then head)
        source_rest, parents = _rest_of(set_scene)
        for _tex, part in set_groups:
            moved.update(crossgame.cross_repose(part, source_rest, target_rest, parents))
    if moved:
        notes.append("bones %s's skeleton doesn't have (weight moved to): %s" % (
            dst.game.short, ", ".join("%s -> %s" % kv for kv in sorted(moved.items()))))
    parts = crossgame.character_skin_parts(groups, "")
    if not parts:
        raise PortError("%s has no skin triangles" % name)
    textures = {}
    for p in parts:
        if not p.texture:
            continue
        orig = p.texture[len(prefix):]
        for ext in (".DDS", ".TGA"):
            tex = src.get(orig + ext)
            if tex is not None:
                problem = dds_problem(tex)
                if problem:
                    notes.append("texture %s: %s" % (orig, problem))
                textures[p.texture + ext] = tex
                break
        else:
            notes.append("texture %s not found in the %s files" % (orig, src.game.short))
    model = (new_name or (name if name.upper().startswith(prefix) else prefix + name))[:63].upper()
    skin_obj = next(o for o in target.objects if o.role == "skin")
    tdata = open(tpath, "rb").read()
    # hats / glasses: the body's head connectors moved onto the new head's top and front
    fit = crossgame.head_connector_offsets(target, parts)
    if fit:
        notes.append("head gear points moved to the new head: %s" % ", ".join(
            "%s %+.1f cm" % (target.objects[i].name, max(d, key=abs)) for i, d in sorted(fit.items())))
    body_tris = sum(len(t) for _tex, part in sets[0][0] for t in [part.triangles])
    if body_tris < 800:
        notes.append("low-detail model: %s has %d triangles (Desert Storm's soldiers ~1 400) - it is the game's "
                     "distance version; the full models are larger (e.g. Global Storm C04_MILITARY_UNIFORM)"
                     % (name, body_tris))
    warn = []
    with dst.current():
        out = evo_write.rebuild(tdata, {skin_obj.index: parts if len(parts) > 1 else parts[0]},
                                model_name=model, warnings=warn, key_offsets=fit)
    notes += warn
    notes.append("%d skin part(s) on %s's skeleton (%s)" % (len(parts), soldier, body))
    core = _title(src.game, name).split(" (")[0]
    if core in ("", name.upper()) or len(core) < 5:
        # a name that says nothing (Global Storm's C04_500_POLY): the main texture's instead (C BDU BRADLEY)
        core = _title(src.game, (parts[0].texture or name)[len(prefix):]).split(" (")[0]
    label = "%s (%s)" % (core, src.game.short)
    skin = (model + ".skin", "\n".join([
        "# %s from %s on %s's body, ported by the ModKit porter (conflict_ds_tools/porting.py)." % (
            name, src.game.short, soldier),
        "# Squad Loadout screen: UNIFORM row \"%s\" for %s. The body goes with this uniform only." % (label, soldier),
        "",
        "uniform      = SAS",
        "uniform name = %s" % label[:40],
        "soldier      = %s" % soldier,
        "texture      = %s" % (parts[0].texture or ""),
        "body         = %s" % model,
        ""]))
    return model, out, textures, skin, notes


def port_animation(src, dst, name, load, new_name=None, _cache={}):
    """(file name, bytes, notes) of one source animation retargeted onto dst's soldier and written in dst's format."""
    key = (src.game.id, dst.game.id)
    if key not in _cache:
        sp, dp = src.scene_file(SOLDIER[src.game.id]), dst.scene_file(SOLDIER[dst.game.id])
        if not sp or not dp:
            raise PortError("soldier skeleton missing: %s" % (SOLDIER[src.game.id] if not sp else SOLDIER[dst.game.id]))
        with src.current():
            sscene = evo.load(sp)
        with dst.current():
            dscene = evo.load(dp)
        _cache[key] = (sscene, dscene)
    sscene, dscene = _cache[key]
    data = load()
    anim = evo.read_prb(data, name)
    with src.current():
        fits = evo.compatible(sscene, anim)
        # Vietnam's upper-body layer animations (*_UB: 29 tracks, played over a leg animation): every track a bone of
        # the soldier - they come over whole-body with the legs in the rest pose (Desert Storm doesn't layer)
        layer = not fits and anim.cg is None and name.upper().endswith("_UB") and len(anim.tracks) >= 20 and \
            sum(1 for t in anim.tracks_for(sscene) if t is not None) == len(anim.tracks)
    if not fits and not layer:
        raise PortError("%s doesn't fit %s's soldier skeleton (an animal, vehicle or layer animation)" % (
            name, src.game.short))
    with src.current():
        out = evo.retarget(anim, sscene, dscene)
    if out is None:
        raise PortError("%s: no bone of %s's soldier matches %s's" % (name, src.game.short, dst.game.short))
    notes = ["upper-body layer animation: the legs stay in the rest pose"] if layer else []
    points = []
    for f in range(out.frames):
        for m in evo.anim_worlds(dscene, out, f):
            points.append((m[0][3], m[1][3], m[2][3]))
    events = b""
    nevents = 0
    if anim.events and anim.game in ("ds1", "ds2"):
        events, nevents = evo_write.pack_events(anim.events)
    elif src.game.id in ("cv", "cg"):
        notes.append("no footstep events (%s animations carry none)" % src.game.short)
    prefix = src.game.id.upper() + "_"
    fname = (new_name or (name if name.upper().startswith(prefix) else prefix + name)).upper() + ".PRB"
    return fname, evo_write.write_prb(out.tracks, out.fps, points, events, nevents), notes


def _title(src_game, name):
    """A display name for a ported weapon: the source's model name without its prefixes, + the game it came from."""
    words = [w for w in name.upper().split("_") if w not in _GENERIC_WORDS and not w.isdigit()]
    words = [w for w in words if not re.fullmatch(r"(RIFLE|LMG|HMG|SMG|PISTOL|SNIPER|SHOTGUN)\d+", w)]  # RIFLE04_ tags
    return "%s (%s)" % (" ".join(words) or name.upper(), src_game.short)


# Weapon tables laid out like Desert Storm's with columns inserted: game -> (first inserted column, how many).
# Desert Storm II's Weaps.txt has 4 more (accuracy factors) from column 44 (0-based): matched over the 73 weapons both
# games have, every column before lines up, every column after is 4 on.
#
# As segments (source first, source end, Desert Storm first). The Great Escape's (74 columns): one column fewer in
# front (Desert Storm's second -1), 11 animation columns where Desert Storm has 6 (not mapped), then its columns 63-73
# = Desert Storm's 59-69 (the sound bank at 67 / 63) - matched by the fixed landmarks (100,100,100,100, DIRECT,
# Testdecal.png) and the values (Luger: 12 magazines of 8, 0.2 s between shots, damage 11; Welrod hearing range 5).
WEAPS_LAYOUT = {"ds1": [(0, 999, 0)], "ds2": [(0, 44, 0), (48, 999, 44)],
                "ge": [(0, 2, 0), (2, 52, 3), (63, 999, 59)]}
DS1_WEAPS_COLUMNS = 75
# Games with too few weapons in common with Desert Storm to compare columns (The Great Escape: the fist): only these
# columns are copied - fire interval, magazines, magazine size, reload time, damage, hearing range
WEAPS_TRUSTED = {"ge": {10, 14, 15, 16, 22, 64}}
# Columns a game re-tuned (different values for some shared weapons) that still mean the same: fire interval,
# magazine size, damage (Desert Storm II's AK-47 does 14 like Desert Storm's; its M60 and Barrett hit harder)
WEAPS_TUNED = {"ds2": {10, 15, 22}}
# Named .weapon fields (DesertStormFix mods.cpp kFields), 0-based Desert Storm Weaps.txt columns
WEAPON_FIELDS = {10: "time between shots", 14: "magazines", 15: "magazine size", 16: "reload time", 22: "damage",
                 23: "range", 64: "hearing range"}


def _weaps_rows(game_src):
    """{id upper: [columns]} of a game's Weaps.txt (first row per id)."""
    out = {}
    for line in (game_src.get("WEAPS.TXT") or b"").decode("latin-1").splitlines():
        r = [c.strip() for c in line.split(",")]
        if len(r) > 10 and r[0]:
            out.setdefault(r[0].upper(), r)
    return out


def _num(v):
    try:
        float(v)
        return True
    except ValueError:
        return False


def source_weapon(src, dst, model):
    """(source weapon id, its row in the target's column layout, {column: True} of the columns that mean the same in
    both games) for the source weapon drawn with `model`, or None. A column counts as the same when it holds the same
    value for at least 90 % of the weapons both games have (the rest are tuned per game, or mean something else: Desert
    Storm II's range column is 100 everywhere, Desert Storm's 780 - 915)."""
    if src.game.id not in WEAPS_LAYOUT or dst.game.id != "ds1":
        return None
    srows, drows = _weaps_rows(src), _weaps_rows(dst)
    segments = WEAPS_LAYOUT[src.game.id]

    def lay(r):
        out = [""] * max(DS1_WEAPS_COLUMNS, len(r) if src.game.id == "ds1" else 0)
        for first, end, at in segments:
            for j in range(first, min(end, len(r))):
                if at + j - first < len(out):
                    out[at + j - first] = r[j]
        return out
    hits = [(k, lay(r)) for k, r in srows.items() if r[1].upper() == model.upper()]
    if not hits:
        return None
    # a model several rows draw (Desert Storm II's M16 + its grenade launcher): the gun itself, not the launcher
    hits.sort(key=lambda kr: (kr[1][4] in ("-1", ""), "LAUNCHER" in kr[1][5].upper() and "RIFLE" not in kr[1][5].upper()))
    wid, row = hits[0]
    common = [k for k in srows if k in drows]
    same = {}
    if len(common) >= 20:
        width = min(len(r) for r in drows.values())
        for j in range(width):
            agree = sum(1 for k in common if j < len(lay(srows[k])) and lay(srows[k])[j] == drows[k][j])
            same[j] = agree >= 0.9 * len(common) or j in WEAPS_TUNED.get(src.game.id, ())
    else:
        same = {j: True for j in WEAPS_TRUSTED.get(src.game.id, ())}
    return row[0], row, same


def _bank_pack(src, bank, rename):
    """A Desert Storm bank pack (.sch bytes) of the source game's sound bank `bank` under the name `rename`, its
    samples under ids of their own, or None (no level cache of the source has it)."""
    folders = [f for f in (src.folder, games.find_game_dir(src.game)) if f and os.path.isdir(f)]
    for folder in folders:
        for f in sorted(os.listdir(folder)):
            if not f.lower().endswith(".sch"):
                continue
            with open(os.path.join(folder, f), "rb") as fh:
                data = fh.read()
            try:
                pack = sound.extract_bank(data, bank, rename)
            except ValueError:
                continue
            if pack:
                # Desert Storm II's banks carry 20 more bytes per sample entry: into Desert Storm's layout
                try:
                    pack = sound.build([sound.bank_to_ds1(c) if t == b"BANK" else c for t, c in sound.chunks(pack)])
                except ValueError:
                    return None                      # a bank layout Desert Storm can't take: sounds like the base
                first = 0x5D000000 | (zlib.crc32(rename.upper().encode()) & 0xFFFF) << 8
                return sound.renumber(pack, first), f
    return None


def weapon_file(src, dst, name, model, like):
    """(file name, text, notes, {extra file: bytes}) of a starter .weapon for a ported gun model; refused when `like`
    isn't one of the target's weapons. A Desert Storm II gun brings its own numbers (the columns both games read the
    same way), its shot sound (its bank as a pack) and its name (the game's text)."""
    table = dst.get("WEAPS.TXT")
    ids = {line.split(",")[0].strip().upper(): line.split(",")[0].strip()
           for line in (table or b"").decode("latin-1").splitlines() if "," in line}
    if like.upper() not in ids:
        raise PortError("%s isn't a %s weapon (Weaps.txt) - pick the one the new gun should handle like, e.g. "
                        "IR_WPN_AK47, US_WPN_M16A2" % (like, dst.game.short))
    base = ids[like.upper()]
    extra_lines, notes, files = [], [], {}
    title = _title(src.game, name)
    found = source_weapon(src, dst, name)
    if found:
        sid, srow, same = found
        brow = _weaps_rows(dst).get(like.upper(), [])
        changed, kept = [], []
        for j in range(2, min(len(srow), len(brow))):
            if j in (4, 5, 6, 63) or not _num(srow[j]) or srow[j] == brow[j]:
                continue          # picture, type, connector: the base's; the sound: below
            if same.get(j):
                key = WEAPON_FIELDS.get(j)
                extra_lines.append("%-13s = %s" % (key, srow[j]) if key else "column %-6d = %s" % (j + 1, srow[j]))
                changed.append(key or "column %d" % (j + 1))
            else:
                kept.append(WEAPON_FIELDS.get(j) or "column %d" % (j + 1))
        notes.append("numbers from %s's %s: %s" % (src.game.short, sid, ", ".join(changed) or "all as %s" % base))
        if len(brow) > 5 and srow[5].upper() != brow[5].upper():
            notes.append("type: %s's %s is a %s, the new gun a %s like %s (Desert Storm's type decides how it fires)"
                         % (src.game.short, sid, srow[5], brow[5], base))
        if kept:
            why = ("too few weapons in common to check what they mean" if src.game.id in WEAPS_TRUSTED else
                   "the column means something else in %s" % src.game.short)
            notes.append("kept %s's (%s): %s" % (base, why, ", ".join(kept)))
        bank = srow[63] if len(srow) > 63 else "-1"
        if bank not in ("-1", ""):
            snd = ("%s_%s" % (src.game.id.upper(), bank)).upper()[:31]
            got = _bank_pack(src, bank, snd)
            if got:
                files["%s_SND.sch" % model] = got[0]
                extra_lines.append("sound         = %s" % snd)
                notes.append("shot sound: %s's bank %s (from %s) as %s" % (src.game.short, bank, got[1], snd))
            else:
                notes.append("shot sound: %s's bank %s isn't in its sound caches - sounds like %s" % (
                    src.game.short, bank, base))
        try:
            text = records.Records(src.folder if games.detect(src.folder) is src.game else
                                   games.find_game_dir(src.game) or "").texts()
            text = next((v for k, v in text.items() if k.upper() == sid.upper()), None)
        except Exception:                                   # noqa: BLE001 - texts are a nice-to-have
            text = None
        if text:
            title = "%s (%s)" % (text, src.game.short)
            notes.append("name from %s's texts: %s" % (src.game.short, text))
    elif src.game.id not in WEAPS_LAYOUT:
        notes.append("numbers: %s's weapon table isn't mapped yet - handles like %s" % (src.game.short, base))
    text = "\n".join([
        "# %s ported from %s by the ModKit porter (conflict_ds_tools/porting.py)." % (name, src.game.short),
        "# Handles like %s: its damage, magazine, sounds and animations - change the numbers below" % base,
        "# (field names: MAKING_MODS.md). Its HUD / inventory picture is rendered from the model: replace",
        "# %s_ICON.png with your own (the size of %s's) if you like." % (model, base),
        "",
        "id            = %s" % model,
        "based on      = %s" % ids[like.upper()],
        "display name  = %s" % title,
        "model         = %s" % model,
        "hud icon      = %s_ICON" % model] + extra_lines + [
        ""])
    return model + ".weapon", text, ["starter %s.weapon based on %s" % (model, ids[like.upper()])] + notes, files


def _game_records(game_source):
    folder = game_source.folder if games.detect(game_source.folder) is game_source.game else         games.find_game_dir(game_source.game) or ""
    return records.Records(folder)


def _strings_value(text):
    """A text as one .strings value: Desert Storm's fonts are Latin-1, new lines written as \n."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip().replace("\n", "\\n")
    return text.encode("latin-1", "replace").decode("latin-1")


def texts_file(src, dst, source_id, section=None):
    """(file name, .strings text, notes) for one text file of the source game: a mission's ("M01" = Desert Storm II's
    M01 texts, "GE_LEVEL01" = The Great Escape's GE_LEVEL01.LOC) or the shared catalog ("CATALOG").

    The source games hash text keys the way Desert Storm does (lower-cased key, the archive LFSR), so a ported mission
    script's text numbers find these. A mission's texts go to a "[SECTION]" (default <GAME>_<ID>) that the ported level
    switches on with game.load_strings("<SECTION>Strs.loc") - DesertStormFix serves that name from the .strings when
    no such file exists, and the texts don't touch any other level. Keys known only by their hash are written as
    #1A2B3C4D. The catalog's texts go to every level, but only those Desert Storm doesn't have (its own stay)."""
    entries = _game_records(src).text_entries()
    sid = source_id.upper()
    if sid.endswith(".LOC"):
        sid = sid[:-4]
    srcs = sorted({s for s, _k in entries})
    pick = next((s for s in srcs if s.upper() in (sid, sid + ".LOC")), None)
    if pick is None:
        raise PortError("%s has no text file %s (it has: %s)" % (src.game.short, source_id, ", ".join(srcs)))
    rows = [(k, v) for (s, k), v in entries.items() if s == pick and v.strip()]
    notes = []
    if pick == "catalog":
        own = {records.hash_key(k) for k in dst_texts_keys(dst)}
        new = [(k, v) for k, v in rows if records.hash_key(k) not in own]
        notes.append("%d of %s's %d shared texts are new to %s (the rest keep %s's own wording)" % (
            len(new), src.game.short, len(rows), dst.game.short, dst.game.short))
        rows, section, fname = new, None, "%s_CATALOG.strings" % src.game.id.upper()
    else:
        section = (section or ("%s_%s" % (src.game.id.upper(), sid) if not sid.startswith(src.game.id.upper() + "_")
                               else sid)).upper()
        fname = section + ".strings"
        notes.append("%d text(s) in section [%s]: the level loads them with game.load_strings(\"%sStrs.loc\")" % (
            len(rows), section, section))
    lossy = sum(1 for _k, v in rows if "?" in _strings_value(v) and "?" not in v)
    if lossy:
        notes.append("%d text(s) have characters Desert Storm's fonts don't have (shown as ?)" % lossy)
    lines = ["# %s's %s texts, ported by the ModKit porter (conflict_ds_tools/porting.py)." % (src.game.short, pick),
             "# KEY = text; #1A2B3C4D = a text known only by its hash (the source game stores no key name).", ""]
    if section:
        lines += ["[%s]" % section]
    lines += ["%s = %s" % (k, _strings_value(v)) for k, v in sorted(rows)]
    return fname, "\n".join(lines) + "\n", notes


def dst_texts_keys(dst):
    try:
        return [k for (s, k) in _game_records(dst).text_entries() if s == "catalog"]
    except Exception:                                       # noqa: BLE001 - no target texts: every text is new
        return []


def _mod_file(out_dir, name):
    """Where a ported file goes in its mod: the mod's layout (conflict_ds_tools.modlayout - organised folders for a new
    mod, flat mods stay flat); a name with a folder of its own is kept as given."""
    from . import modlayout
    try:
        return modlayout.path_for(out_dir, name)
    except ValueError:
        path = os.path.join(out_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path


def run(recipe, mods_dir, source_folder=None, target_folder=None, log=print):
    """Ports a recipe into mods_dir\\<name>: returns the report lines (also written as port-report.txt)."""
    src = GameSource(recipe.source_game, source_folder or recipe.source)
    dst = GameSource(recipe.target_game, target_folder)
    out_dir = os.path.join(mods_dir, recipe.name)
    os.makedirs(out_dir, exist_ok=True)
    report = ["Port %s: %s -> %s (source %s)" % (recipe.name, src.game.short, dst.game.short, src.folder), ""]
    done = fails = 0
    for index, (kind, pattern, new) in enumerate(recipe.items):
        if kind in ("model", "weapon"):
            names = [pattern] if not any(c in pattern for c in "*?") else \
                [n for n in src.names(".EVO") if fnmatch.fnmatchcase(n, pattern)]
            if not names:
                report.append("[fail] model %s: no %s model of that name" % (pattern, src.game.short))
                fails += 1
            for n in names:
                try:
                    picture = None
                    if kind == "weapon":
                        picture = base_picture(dst, recipe.likes[index]) or (112, 34)
                    model, data, textures, notes = port_model(src, dst, n, new, held=kind == "weapon",
                                                              picture=picture)
                except (PortError, evo.EvoError) as e:
                    report.append("[fail] model %s: %s" % (n, e))
                    fails += 1
                    continue
                if kind == "weapon":
                    try:
                        wname, wtext, wnotes, wfiles = weapon_file(src, dst, n, model, recipe.likes[index])
                    except PortError as e:
                        report.append("[fail] weapon %s: %s" % (n, e))
                        fails += 1
                        continue
                    with open(_mod_file(out_dir, wname), "w", encoding="utf-8") as f:
                        f.write(wtext)
                    for fn, fb in wfiles.items():
                        with open(_mod_file(out_dir, fn), "wb") as f:
                            f.write(fb)
                    notes = wnotes + notes
                with open(_mod_file(out_dir, model + ".EVO"), "wb") as f:
                    f.write(data)
                for tn, tb in textures.items():
                    with open(_mod_file(out_dir, tn), "wb") as f:
                        f.write(tb)
                report.append("%s %s -> %s.EVO (%d texture(s))%s" % (kind, n, model, len(textures), "".join(
                    "\n    " + x for x in notes)))
                done += 1
        elif kind == "level":
            from .levelport import port_level
            try:
                lfiles, notes, lname = port_level(src, dst, pattern, base=recipe.bases[index], name=new, log=log)
            except (PortError, evo.EvoError) as e:
                report.append("[fail] level %s: %s" % (pattern, e))
                fails += 1
                continue
            for fname, fb in lfiles.items():
                with open(_mod_file(out_dir, fname), "wb") as f:
                    f.write(fb)
            report.append("level %s -> %s.lua + %s.ENV (%d file(s))%s" % (
                pattern, lname.lower(), lname, len(lfiles), "".join("\n    " + x for x in notes)))
            done += 1
        elif kind == "texts":
            try:
                fname, ttext, notes = texts_file(src, dst, pattern, new)
            except PortError as e:
                report.append("[fail] texts %s: %s" % (pattern, e))
                fails += 1
                continue
            with open(_mod_file(out_dir, fname), "w", encoding="latin-1", newline="\r\n") as f:
                f.write(ttext)
            report.append("texts %s -> %s%s" % (pattern, fname, "".join("\n    " + x for x in notes)))
            done += 1
        elif kind == "vehicle":
            try:
                model, vfiles, notes = port_vehicle(src, dst, pattern, recipe.likes[index], new)
            except (PortError, evo.EvoError) as e:
                report.append("[fail] vehicle %s: %s" % (pattern, e))
                fails += 1
                continue
            for fname, fb in vfiles.items():
                with open(_mod_file(out_dir, fname), "wb") as f:
                    f.write(fb)
            report.append("vehicle %s -> %s.EVO + %s.vehicle (%d file(s))%s" % (
                pattern, model, model, len(vfiles), "".join("\n    " + x for x in notes)))
            done += 1
        elif kind in ("character", "unit"):
            try:
                if kind == "unit":
                    prefix = src.game.id.upper() + "_"
                    model_name = (new or (pattern if pattern.startswith(prefix) else prefix + pattern))[:35]
                    sname, stext, unotes, base_body, ufiles = unit_file(src, dst, pattern, model_name,
                                                                        recipe.likes[index])
                    for fn, fb in ufiles.items():
                        with open(_mod_file(out_dir, fn), "wb") as f:
                            f.write(fb)
                    uskin = unit_skin(src, pattern)
                    model, data, textures, _skin, notes = port_character(src, dst, pattern, "Bradley", model_name,
                                                                        body=base_body,
                                                                        head=recipe.heads.get(index), skin=uskin)
                    if uskin:
                        notes.insert(0, "skin %s (the unit's own)" % uskin)
                    notes = unotes + [n.replace("Bradley's skeleton", base_body + "'s skeleton") for n in notes]
                else:
                    model, data, textures, (sname, stext), notes = port_character(
                        src, dst, pattern, recipe.soldiers[index], new, head=recipe.heads.get(index))
            except (PortError, evo.EvoError) as e:
                report.append("[fail] %s %s: %s" % (kind, pattern, e))
                fails += 1
                continue
            with open(_mod_file(out_dir, model + ".EVO"), "wb") as f:
                f.write(data)
            for tn, tb in textures.items():
                with open(_mod_file(out_dir, tn), "wb") as f:
                    f.write(tb)
            with open(_mod_file(out_dir, sname), "w", encoding="utf-8") as f:
                f.write(stext)
            report.append("%s %s -> %s.EVO + %s (%d texture(s))%s" % (kind, pattern, model, sname, len(textures),
                                                                     "".join("\n    " + x for x in notes)))
            done += 1
        else:
            anims = src.animations()
            names = [pattern] if not any(c in pattern for c in "*?") else \
                sorted(n for n in anims if fnmatch.fnmatchcase(n, pattern))
            if not names:
                report.append("[fail] animation %s: no %s animation of that name" % (pattern, src.game.short))
                fails += 1
            for n in names:
                if n not in anims:
                    report.append("[fail] animation %s: not in the %s files" % (n, src.game.short))
                    fails += 1
                    continue
                try:
                    fname, data, notes = port_animation(src, dst, n, anims[n], new)
                except (PortError, evo.EvoError) as e:
                    report.append("[fail] animation %s: %s" % (n, e))
                    fails += 1
                    continue
                with open(_mod_file(out_dir, fname), "wb") as f:
                    f.write(data)
                report.append("animation %s -> %s%s" % (n, fname, "".join("\n    " + x for x in notes)))
                done += 1
        log(report[-1].splitlines()[0])
    report += ["", "%d ported, %d failed" % (done, fails)]
    with open(os.path.join(out_dir, "port-report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    return report
