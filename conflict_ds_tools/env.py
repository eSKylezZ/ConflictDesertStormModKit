"""Conflict: Desert Storm scene files (``<LEVEL>.ENV``, "Version 70-72").

A scene is plain text that the game reads token by token (its own scanner `FUN_004b9dc0`: ``%d``/``%f`` stop at the
first non-digit, ``%s`` = up to whitespace; quoted strings by `FUN_004bf6f0`). Line breaks mean nothing to the game,
but the original tool wrote one record per line, and so do we.

Sections, in file order (loader `FUN_004f8d00`, version gates as ``v > n``):

====================  ===========================================================================================
header                ``Version 72`` / ``"M1.BMP"807 0`` (map image v>44, number v>45, scene flags v>=48)
``ENVI`` … ``ENDI``   world pieces: hierarchy, model, transform (+ ``VAMI f`` / ``ANMI "x"`` follow-ups)
``HLOI`` … ``HLOX``   high-level objects: soldiers, vehicles, items, doors – model, unit, AI fields, kit
``PORT`` … ``ENDP``   portals between zones (plane, polygon, bounding sphere)
lights … ``ENDL``     ``DLGT`` / ``SLGT`` / ``PLGT`` (`FUN_0051ed20`)
``ENVSND`` …          positional ambient sounds (`FUN_0055f8e0`)
``NEWENVWAYPT`` …     named waypoints (`FUN_004d1570`)
``NEWENVMARKER`` …    legacy marker graph – **read and discarded by the game** (`FUN_00505090` stores nothing)
``REGN`` …            trigger regions: type 1 sphere, type 0 oriented box (`FUN_004fc9e0`)
``NUMSWITCHES`` …     switches (`FUN_004f69a0`)
atmosphere            fog / sky layers / 4 colour keys / ``TIMEOFDAY``
``<GLAZIER>``         breakable glass panes (`FUN_00510f80`)
``<EMIT>``            particle emitters (`FUN_004f2630`)
``SHRUBS`` …          vegetation (`FUN_0050a7d0`)
``AALAUNCHERS`` …     anti-aircraft launchers (`FUN_004ff8e0`)
cameras … ``ENDC``    ``CAMFREE`` / ``CAMTARGPOS`` / ``CAMTARGOBJ`` / ``CAMCHASEOBJ`` (cutscenes, briefing)
====================  ===========================================================================================

The model is lossless: every line keeps its exact text, and only the tokens you change are re-rendered (numbers in
the token's original style). ``EnvFile.read(data).to_bytes() == data`` for every shipped scene.

Records whose layout is decoded get named fields (``ENVI``, ``HLOI``, ``PORT``, ``NEWENVWAYPT``, ``REGN``,
``ENVSND``, lights, cameras, ``SWITCH``, emitter rows – kind ``EMITTER``, their line starts with ``*``); the rest are kept as positional tokens (``rec.tokens``) until decoded.

Mission scripts (``MissionN.dll``) find objects by the numeric ``script_id`` of their ``HLOI`` – keep it when editing.

The query / edit functions at the bottom are the command layer an editor UI (and its MCP server) both call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ENVI type of world-zone records (root + one per chunk): 5 in Desert Storm 1 / 2, 3 in Vietnam / Global Storm
ZONE_TYPES = (3, 5)

_TOKEN = re.compile(r'"[^"\r\n]*"|[^\s"]+')


@dataclass
class Token:
    text: str
    start: int  # offset inside the line
    end: int

    @property
    def quoted(self) -> bool:
        return self.text.startswith('"')

    @property
    def value(self):
        t = self.text
        if t.startswith('"'):
            return t[1:-1]
        try:
            return int(t)
        except ValueError:
            try:
                return float(t)
            except ValueError:
                return t


@dataclass
class Record:
    """One line of the scene. ``kind`` = its keyword (or ``""`` for headerless lines such as shrub rows)."""
    file: "EnvFile"
    index: int  # line number (0-based)
    kind: str
    tokens: list[Token]
    fields: dict[str, int] = field(default_factory=dict)  # name -> token index (vectors: name.x / name.y / name.z)
    children: list["Record"] = field(default_factory=list)  # VAMI / ANMI lines that belong to an ENVI
    section: str = ""
    # fields kept on another line (DS2 scenes: an ENVI's "parent" is on its PCLINK line) -> (record, field there)
    aliases: dict[str, tuple["Record", str]] = field(default_factory=dict)

    def __getitem__(self, name: str):
        if name in self.fields:
            return self.tokens[self.fields[name]].value
        if f"{name}.x" in self.fields:
            return tuple(self[f"{name}.{c}"] for c in "xyz")
        if name in self.aliases:
            rec, other = self.aliases[name]
            return other if rec is None else rec[other]   # rec None: a fixed value (an ENVI with no PCLINK = Root)
        raise KeyError(f"{self.kind} has no field {name!r}")

    def get(self, name: str, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def __setitem__(self, name: str, value):
        if f"{name}.x" in self.fields and name not in self.fields:
            for c, v in zip("xyz", value):
                self[f"{name}.{c}"] = v
            return
        if name not in self.fields and name in self.aliases:
            rec, other = self.aliases[name]
            if rec is None:
                self.file.set_link_parent(self, str(value))
            else:
                rec[other] = value
            return
        if name not in self.fields:
            raise KeyError(f"{self.kind} has no field {name!r}")
        self.set_token(self.fields[name], value)

    def set_token(self, i: int, value):
        tok = self.tokens[i]
        self.file._replace_token(self, i, _format_like(tok.text, value))

    def as_dict(self) -> dict:
        out = {"line": self.index + 1, "kind": self.kind}
        names = set()
        for n in self.fields:
            base = n.rsplit(".", 1)[0] if n[-2:] in (".x", ".y", ".z") else n
            if base not in names:
                names.add(base)
                out[base] = self[base]
        for n in self.aliases:
            out.setdefault(n, self[n])
        return out

    @property
    def text(self) -> str:
        return self.file.lines[self.index]


def _format_like(old: str, value) -> str:
    """Render ``value`` the way ``old`` was written (quoted / integer / same number of decimals)."""
    if old.startswith('"'):
        s = str(value)
        if '"' in s or "\n" in s:
            raise ValueError(f"string can't contain quotes or newlines: {s!r}")
        return f'"{s}"'
    if not re.fullmatch(r"-?\d+(\.\d*)?", old):   # a bare word (DS2 PCLINK names, a switch's UNDEFINED region)
        s = str(value)
        if not s or re.search(r'[\s"]', s):
            raise ValueError(f"a bare name can't be empty or contain spaces / quotes: {s!r}")
        return s
    if isinstance(value, str):
        raise ValueError(f"expected a number for token {old!r}, got {value!r}")
    if "." in old:
        decimals = len(old) - old.index(".") - 1
        return f"{float(value):.{decimals}f}"
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"token {old!r} is an integer, got {value!r}")
    return str(int(value))


# --- record layouts -------------------------------------------------------------------------------------------------

class _Cursor:
    def __init__(self, rec: Record, version: int):
        self.rec, self.v, self.i = rec, version, 1  # token 0 = keyword

    def _take(self, name: str | None, check):
        if self.i >= len(self.rec.tokens):
            raise ValueError(f"line {self.rec.index + 1}: {self.rec.kind} ended before {name or 'a field'}")
        t = self.rec.tokens[self.i]
        if not check(t):
            raise ValueError(f"line {self.rec.index + 1}: {self.rec.kind} field {name} = {t.text!r} has the wrong type")
        if name:
            self.rec.fields[name] = self.i
        self.i += 1
        return t.value

    def d(self, name=None):
        return self._take(name, lambda t: not t.quoted and re.fullmatch(r"-?\d+", t.text) is not None)

    def f(self, name=None):
        return self._take(name, lambda t: not t.quoted and re.fullmatch(r"-?\d+(\.\d*)?", t.text) is not None)

    def q(self, name=None):
        return self._take(name, lambda t: t.quoted)

    def s(self, name=None):
        return self._take(name, lambda t: not t.quoted)

    def any(self, name=None):
        return self._take(name, lambda t: True)

    def opt_d(self, name=None):
        """An int the game reads with %d but files often leave out (the scanner then reads 0 without consuming)."""
        t = self.rec.tokens[self.i] if self.i < len(self.rec.tokens) else None
        if t is not None and not t.quoted and re.fullmatch(r"-?\d+", t.text):
            return self.d(name)
        return 0

    def int_list(self, prefix):
        n = self.d(f"{prefix}_count")
        for k in range(n): self.d(f"{prefix}{k}")

    def v3(self, name):
        for c in "xyz":
            self.f(f"{name}.{c}")

    def rest(self, prefix):
        n = 0
        while self.i < len(self.rec.tokens):
            self.any(f"{prefix}{n}")
            n += 1

    def done(self):
        if self.i != len(self.rec.tokens):
            extra = " ".join(t.text for t in self.rec.tokens[self.i:])
            raise ValueError(f"line {self.rec.index + 1}: {self.rec.kind} has extra tokens: {extra[:80]}")


def _envi(c: _Cursor):
    """Mirrors the loaders (DS1 0x4F8D00, DS2 0x50AB80..0x50B310, Vietnam 0x5928E1..0x593579; Global Storm v190+
    from the data). From version 79 the parent is no longer on the line: the PCLINK lines after ENDI give it
    (``parent`` is then an alias, see EnvFile._link_parents). ANMI / VAMI lines (Vietnam) are whole ENVI records
    with another keyword (+ an animation name / a float at the end). Flag 0x1000000: a RINFO line follows."""
    v = c.v
    if v >= 300:
        # Denied Ops (v304-316, from the data): 5 ints, name, type (0 zone / prop, 5 physics), model, an int, 2 textures,
        # transform; RINFO / material overrides follow on their own lines. PHYI (physics props) has the same layout.
        c.d("flags"); c.d("u0"); c.d("u1"); c.d("u1b"); c.d("u1c")
        c.q("name"); c.d("type"); c.q("model"); c.d("u2"); c.q("texture"); c.q("texture2")
        c.v3("pos"); c.v3("rot"); c.v3("scale")
        c.rest("t")
        return
    if v >= 190:
        # Global Storm (v209, mapped from the data, loader not traced yet): 4 ints, name, type (0 prop, 2 animated,
        # 3 zone), model, 2 textures, transform; the tail (breakable block, zone list, lods) is kept positional
        c.d("flags"); c.d("u0"); c.d("u1"); c.d("u1b")
        c.q("name"); c.d("type"); c.q("model"); c.q("texture"); c.q("texture2")
        c.v3("pos"); c.v3("rot"); c.v3("scale")
        c.rest("t")
        return
    if v > 9: c.d("flags")
    if v >= 89: c.d("u0")
    if v > 0: c.d("u1")
    c.q("name")
    if v < 79: c.q("parent")
    c.d("type"); c.q("model")
    if v > 48: c.q("texture")
    c.v3("pos"); c.v3("rot")
    if v > 36: c.v3("scale")
    if v > 66: c.d("u2"); c.d("u3")
    if v >= 113: c.d("fog")        # Vietnam: ENVFOGSETTING id (its name's archive hash, signed), 0 = none
    if c.rec["flags"] & 0x400000 and v > 50:  # breakable prop: shatters into shards
        c.f("break_health")
        if v >= 102: c.f("break_f1")
        c.d("shard_count"); c.q("shard_texture")
        if v >= 101: c.q("shard_texture2")
        c.f("shard_f0"); c.f("shard_f1")
        if v >= 102: c.d("shard_d")
        if v >= 59: c.d("shard_alpha"); c.d("shard_d0"); c.d("shard_d1")
        if v >= 103: c.d("shard_d2"); c.d("shard_d3")
        if v >= 117: c.f("shard_f2")
    if v >= 86: c.d("u5")
    # the scene's lights that light this prop (DS1 FUN_004fb4d0: count, then light indices; max 6 - more = "Instance
    # ... has n active lights" info box). Flag 0x1000000 = a " RINFO ..." line follows (render info, FUN_004fb2c0) -
    # copies made of this line alone must drop the flag (editor commands._drop_rinfo_flag), or the loader eats the
    # next record.
    n = c.d("light_count")
    for k in range(n): c.d(f"light{k}")
    if v >= 146: c.int_list("list2")   # Vietnam (read by 0x58F380 after the first list)
    if v > 51: c.d("u4")
    if v >= 64 and c.rec["flags"] & 0x800000: c.opt_d("u6")   # read by the game, absent in every shipped line
    kw = c.rec.tokens[0].text
    if kw == "ANMI": c.any("animation")
    elif kw == "VAMI": c.f("vami")


def _pclink(c: _Cursor):
    """Version 79+: 'child parent value' lines after ENDI (and after HLOSX) - the scene hierarchy. No keyword:
    the child's name is token 0."""
    c.i = 0
    if c.v >= 300:                              # Denied Ops quotes the names
        c.q("name"); c.any("parent"); c.d("value")
        return
    c.s("name"); c.s("parent"); c.d("value")


def _great_escape():
    from . import games
    return games.current().id == "ge"


def _hloi(c: _Cursor):
    """Mirrors the loader (0x4F97E5..0x4FA552)."""
    v = c.v
    if v >= 300:
        # Denied Ops (from the data): 4 ints, 2-4 names (model, [profile / second body,] [head,] zone), an int, 4
        # textures, a quoted "-1", a texture, transform, 3 ints, unit, int, script id, stat (an AI area name), 2 ints;
        # the AI block, kit and attachments ("BIP01 HEAD" 0 "C5_H_BERETRED" ...) stay positional
        c.d("flags"); c.d("u_z"); c.d("u_y"); c.d("u_x")
        names = []
        while c.i < len(c.rec.tokens) and c.rec.tokens[c.i].quoted:
            names.append(c.i)
            c.i += 1
        if len(names) < 2:
            raise ValueError(f"line {c.rec.index + 1}: HLOI has {len(names)} names before its textures")
        c.rec.fields["model"], c.rec.fields["zone"] = names[0], names[-1]
        tok = c.rec.tokens
        if len(names) > 2 and tok[names[-2]].text.upper() != tok[names[0]].text.upper():
            c.rec.fields["head"] = names[-2]       # (some lines name the body twice: no head of their own)
        if len(names) > 3:
            c.rec.fields["body"] = names[1]
        c.d("u_w"); c.q("texture"); c.q("texture2"); c.q("texture3"); c.q("texture4"); c.q("u_str"); c.q("texture5")
        c.v3("pos"); c.v3("rot")
        c.d("u_a"); c.d("u_b"); c.d("u_c")
        c.q("unit"); c.d("u_d"); c.d("script_id"); c.q("stat"); c.d("u_e"); c.d("in_play")
        c.rest("t")
        return
    if v >= 190:
        # Global Storm (v209, from the data): 3 ints, [HLOU: profile], model, zone, body, 3 textures (head ...),
        # transform, 3 ints, unit, int, script id, stat, 2 ints; the AI block / kit / attachments stay positional.
        # Each record is followed by a " 0" line.
        c.d("flags"); c.d("u_z"); c.d("u_y")
        if c.rec.tokens[0].text == "HLOU": c.q("profile")
        c.q("model"); c.q("zone"); c.q("body"); c.q("texture"); c.q("head"); c.q("texture2")
        c.v3("pos"); c.v3("rot")
        c.d("u_a"); c.d("u_b"); c.d("u_c")
        c.q("unit"); c.d("u_d"); c.d("script_id"); c.q("stat"); c.d("u_e"); c.d("in_play")
        c.rest("t")
        return
    if v > 9: c.d("flags")
    if v >= 95: c.d("u_z")
    kw = c.rec.tokens[0].text
    if kw == "HLOU": c.q("profile")   # DS2 squad units: an extra name before the model
    if kw == "HLOS":                  # Vietnam reader 0x5916E0 with 4 names (no shipped sample)
        for k in range(3): c.q(f"name{k}")
    c.q("model"); c.q("zone")
    if v > 48: c.q("texture")
    c.v3("pos"); c.v3("rot")
    # active: 1 = in play from the mission start, 0 = waits for the script's activate (API +0xC4, e.g. Mission 1's
    # extraction Black Hawk and air-strike A-10s; the squad is activated by the player set-up). flags 0x20000 = a
    # cutscene actor.
    c.d("u_a"); c.d("u_b"); c.d("u_c")
    # in_play: 1 = in play when the level starts, 0 = waits for the script (API +0xC4 activate; Mission 1's Black Hawk
    # 76 / A-10s, tanks Mission 4 spawns later). Verified in game: object +0xC bit 0 follows it.
    c.q("unit"); c.d("u_d"); c.d("script_id"); c.q("stat"); c.d("u_e"); c.d("in_play")
    if v > 8: c.d("u_g"); c.d("u_h"); c.d("u_i")
    if v > 26: c.d("u_j")
    if v >= 69: c.d("u_k")
    c.d("u_l"); c.d("u_m")
    if v > 18:
        for k in range(5): c.f(f"ai_f{k}")
        c.d("ai_d0")
        if v > 24: c.f("ai_f5"); c.d("ai_d1")
        if v > 35: c.d("ai_d2")
        if v > 38: c.d("ai_d3")
        if v > 41: c.d("ai_d4"); c.d("ai_d5")
        if v >= 94: c.q("ai_instance")   # DS2: an object looked up by name
        if v >= 40: c.q("ai_name")
        if _great_escape():
            # The Great Escape's reader (0x482B20) has two blocks the Conflict games don't: from version 54 two ints and
            # a list of names, from version 70 another list of names
            if v > 53:
                c.d("ge_a"); c.d("ge_b")
                n = c.d("ge_names_count")
                for k in range(n): c.q(f"ge_name{k}")
            if v > 69:
                n = c.d("ge_names2_count")
                for k in range(n): c.q(f"ge_name2_{k}")
        bits = c.d("ai_bits")
        for b in range(6):
            if bits & (1 << b): c.f(f"ai_bit{b}")
        if bits & 0x40:
            c.d("ai_bit6")
            if v > 25: c.f("ai_bit6f")
        if bits & 0x100:
            t = c.d("ai_shape")
            if v > 21:
                for k in range({0: 1, 1: 4, 2: 8}.get(t, 0)): c.f(f"ai_shape{k}")
            elif t == 2:
                for k in range(4): c.f(f"ai_shape{k}")
                c.d("ai_shape4")
            if 20 < v < 36: c.d("ai_shape_d")
        if _great_escape() and v > 65: c.d("ge_c")
        n = c.d("ai_list_count")
        for k in range(n): c.d(f"ai_list{k}")
    if v > 19: c.f("u_n"); c.f("u_o")
    n = c.d("kit_count")
    for k in range(n):
        c.q(f"kit{k}.item"); c.d(f"kit{k}.category"); c.d(f"kit{k}.amount")
    if v >= 45: c.d("u_p")
    if v >= 50: c.f("u_q"); c.f("u_r")
    if v >= 23:
        c.v3("v1"); c.v3("v2"); c.d("u_s")
        if v < 63: c.v3("v3"); c.v3("v4"); c.d("u_t")
    if v >= 30:
        c.d("u_u")
        n = c.d("group_count")
        for k in range(n): c.d(f"group{k}")
    if v >= 146: c.int_list("list2")   # Vietnam
    if v >= 86: c.d("u_v")
    n = c.d("link_count")
    for k in range(n):
        c.q(f"link{k}.a"); c.d(f"link{k}.n"); c.q(f"link{k}.b")


def _port(c: _Cursor):
    v = c.v
    c.q("name")
    if v > 41: c.d("id")
    c.q("zone_a"); c.q("zone_b"); c.d("u1"); c.d("u2")
    c.f("plane.x"); c.f("plane.y"); c.f("plane.z"); c.f("plane.d")
    n = c.d("vertex_count")
    for k in range(n): c.v3(f"vertex{k}")
    if v > 6: c.v3("centre"); c.f("radius"); c.d("u3")
    if v >= 190:
        c.rest("t")                        # Global Storm: more fields after the sphere (not mapped)
        return
    if v > 70: c.d("u4")
    if v >= 121: c.d("u5"); c.f("u6")   # Vietnam (0x593D38)
    if v > 78:   # DS2 (0x50C293): a list of int pairs
        n = c.d("pair_count")
        for k in range(n): c.d(f"pair{k}.a"); c.d(f"pair{k}.b")


def _waypoint(c: _Cursor):
    if c.v >= 190:
        # Global Storm: id, 2 ints, name, pos, rot, wait ms, wait s?, next id, 5 more
        c.d("id"); c.d("u1"); c.d("u1b"); c.q("name"); c.v3("pos"); c.v3("rot")
        c.d("wait_ms"); c.f("wait_f"); c.d("next")
        c.rest("t")
        return
    c.d("id")
    if c.v > 10: c.d("u1")
    c.q("name"); c.v3("pos")
    if c.v > 14: c.v3("rot")
    # FUN_004d1570: table [0x63C9B0] entry = id (600 entries of 0x20 B): +0x1C wait on arrival (ms), +0x18 the
    # next waypoint's id (-1 = the path ends) - patrol routes are chains of these; a unit starts on one with API
    # +0x110 set_path(unit, first id) (+ +0x114 set_ai_state(unit, 0)).
    c.d("wait_ms")
    if c.v >= 107: c.f("u6")   # Vietnam (0x53C9D0)
    c.d("next")
    # DS2 reader FUN_004d9bf0: more ints from version 79 / 82 / 88
    if c.v >= 79: c.d("u2"); c.d("u3")
    if c.v >= 82: c.d("u4")
    if c.v >= 88: c.d("u5")


def _marker(c: _Cursor):
    c.d("id"); c.v3("pos"); c.d("flags")
    # The Great Escape (reader 0x412C40): each link is "link weight int distance" (DS1 / DS2 scenes: the link alone,
    # whatever their version says) - told by the float after the first link; a name follows with flag 0x400
    toks = c.rec.tokens
    extended = c.i + 1 < len(toks) and "." in toks[c.i + 1].text and not toks[c.i + 1].quoted
    for k in range(5):
        c.d(f"link{k}")
        if extended:
            c.f(f"link{k}.weight"); c.d(f"link{k}.d"); c.f(f"link{k}.distance")
    c.s("type")
    if c.rec["flags"] & (0x400 if extended else 0x80): c.s("name")


def _posmarker(c: _Cursor):
    """Global Storm NEWPOSMARKER (.DES): AI position markers - id, pos, 2 ints (5-7 / 0 so far; NEWJUNCTION lines
    group them by id)."""
    c.d("id"); c.v3("pos"); c.d("u1"); c.d("u2")


def _coverpoint(c: _Cursor):
    """Global Storm ENVCOVERPOINT (.DES): id, int, name, pos, rot, facing (deg, game yaw), arc (deg), then distances
    and flags (positional)."""
    c.d("id"); c.d("u1"); c.q("name"); c.v3("pos"); c.v3("rot"); c.f("facing"); c.f("arc")
    c.rest("t")


def _region(c: _Cursor):
    c.q("name"); t = c.d("type"); c.v3("pos")
    if t == 1:
        c.f("radius")
    else:
        c.v3("rot"); c.v3("min"); c.v3("max")
    if c.v >= 300:
        c.d("u1"); c.d("u2")                    # Denied Ops: two more ints


def _envsnd(c: _Cursor):
    if c.v >= 300:
        # Denied Ops (from the data): type (0 ambient everywhere, 1 positional), the acoustic zones it plays in,
        # volume, delay range (ms), 2 ints, position, distances
        c.q("sound"); c.q("name"); c.d("type"); c.int_list("zones"); c.f("volume"); c.d("delay_min")
        c.d("delay_max"); c.d("u3"); c.d("u4"); c.v3("pos"); c.f("min_distance"); c.f("max_distance")
        c.rest("t")
        return
    c.q("sound"); c.q("name"); c.v3("pos"); c.d("u1")
    if c.v >= 190:
        c.f("u2"); c.d("u2b"); c.d("u2c"); c.f("min_distance"); c.f("max_distance"); c.f("volume")
        c.rest("t")
        return
    c.f("u2")
    if c.v >= 139: c.d("u5"); c.int_list("list")   # Vietnam (0x52600E)
    c.f("min_distance"); c.f("max_distance"); c.f("volume"); c.d("u3"); c.d("u4"); c.d("loop")
    if c.v >= 139: c.d("u6")                        # Vietnam: four trailing ints


def _light(c: _Cursor):
    """Typed from Vietnam's loader (0x4EEE84: DLGT 0x4EE0E9, SLGT 0x4EE3AE, PLGT 0x4EE795, tail 0x4EC338) and
    Global Storm's data (v190+); older versions keep the untyped p0.. tail."""
    if c.v >= 190:
        # Global Storm (from the data): id, name, flags, 2 ints, ambient RGBA, colour RGBA, then
        # PLGT point: pos, radius (cm), intensity; SLGT spot: pitch, yaw, roll, pos, inner / outer cone (deg), range,
        # intensity; DLGT directional: pitch, yaw, roll, pos (zone suns), intensity. Glow / zone tail kept positional.
        kind = c.rec.tokens[0].text
        c.d("id"); c.q("name"); c.d("flags"); c.d("u1"); c.d("u2")
        for k in range(4): c.f(f"ambient{k}")
        for k in range(4): c.f(f"colour{k}")
        if kind == "PLGT":
            c.v3("pos"); c.f("radius"); c.f("intensity")
        elif kind == "SLGT":
            c.f("pitch"); c.f("yaw"); c.f("roll"); c.v3("pos"); c.f("cone_inner"); c.f("cone_outer"); c.f("range")
            c.f("intensity")
        else:
            c.f("pitch"); c.f("yaw"); c.f("roll"); c.v3("pos"); c.f("intensity")
        c.rest("t")
        return
    v, kw = c.v, c.rec.kind
    c.d("id"); c.q("name"); c.d("flags")
    if v >= 144: c.d("u1")
    for k in range(4): c.f(f"ambient{k}")
    for k in range(4): c.f(f"colour{k}")
    if v < 144:
        c.rest("p")
        return
    fl = c.rec["flags"]
    if kw == "DLGT":
        c.v3("dir"); c.v3("v1")
    elif kw == "SLGT":
        c.v3("rot"); c.v3("pos"); c.f("cone0"); c.f("cone1"); c.f("range")
    else:
        c.v3("pos"); c.f("radius")
    if fl & 0x400: c.f("f0"); c.f("f1")
    c.f("intensity")
    c.s("flare"); c.v3("v2")
    if fl & 0x200000: c.any("texture")
    if fl & 0x8000000:
        c.f("f2"); c.f("f3"); c.d("d0"); c.d("d1")
    c.f("f4"); c.f("f5")
    if not fl & 0x800: c.s("zone")


def _glow(c: _Cursor):
    # Vietnam (v148+): second light loop after the first ENDL (0x4EB216)
    c.d("id"); c.q("name"); c.d("flags"); c.d("u1"); c.v3("pos")
    for k in range(4): c.f(f"f{k}")
    c.s("image"); c.s("zone")


def _cover(c: _Cursor):
    # Vietnam ENVCOVERPOINT (0x503B0E, v92+); Global Storm's (v190+) differ
    if c.v >= 190:
        return _coverpoint(c)
    c.d("id"); c.d("u1"); c.q("name"); c.v3("pos"); c.v3("rot")
    for k in range(6): c.f(f"f{k}")
    c.d("d0"); c.d("d1"); c.d("d2"); c.d("d3")
    if c.v >= 104: c.q("ref")
    if c.v >= 106: c.d("d4"); c.d("d5")
    if c.v >= 109: c.d("d6")


def _fog_setting(c: _Cursor):
    # Vietnam ENVFOGSETTING (0x5775F0, v113+): id = lower-cased archive hash of the name as a signed int
    c.q("name"); c.d("id"); c.d("colour"); c.f("start"); c.f("end")
    if c.v >= 114: c.f("density")


def _rinfo(c: _Cursor):
    # Vietnam (0x58EFA0): the line after an ENVI / HLOI with flag 0x1000000
    rf = c.d("rflags")
    if rf & 1:
        c.s("r_word"); n = c.d("r_count")
        for k in range(n): c.q(f"r{k}.a"); c.f(f"r{k}.f"); c.q(f"r{k}.b")
    if rf & 2: c.d("r_d")
    if rf & 4: c.q("r_q")


def _switch(c: _Cursor):
    # FUN_004f69a0 (switches.cpp): name (its hash as written, FUN_004b9a10 = what message 0x0D reports), zone, transform, type =
    # Switches.txt row (model off / on, use box), region = REGN the player must stand in, target_a / target_b = script
    # ids of the doors it opens / closes together (-1 none), then (v >= 62) a number the loader stores but nothing reads.
    # region: the game's string reader also takes a bare word (DS2 Mission 5: UNDEFINED)
    c.q("name"); c.q("zone"); c.v3("pos"); c.v3("rot"); c.q("type"); c.any("region")
    c.d("target_a"); c.d("target_b")
    if c.v >= 62: c.d("number")


def _emitter(c: _Cursor):
    # "* ..." lines between <EMIT> / </EMIT> (FUN_004f2d70; run per frame by FUN_004f2840). Times in seconds, sizes
    # and speeds in cm; a particle gets a random value between each min / max pair.
    c.f("start_delay")                     # countdown before the first particle
    c.v3("pos"); c.d("type")               # particle type (4 flash, 6 smoke, 8 / 9 dust, 11 fire, 12 flame, 18 flare)
    if c.v >= 190:
        c.rest("t")                        # Global Storm: layout not mapped yet
        return
    if c.v >= 122:                         # Vietnam (0x560FA0): flags moved up here, + 2 small ints and a float
        c.d("flags"); c.d("u1"); c.d("u2"); c.f("u3")
    c.v3("velocity_min"); c.v3("velocity_max")
    c.f("size"); c.f("end_size_min"); c.f("end_size_max")     # end size only with flag 0x10
    if c.v >= 53:
        c.f("growth")                      # size change per tick (flag 0x10 without 0x20; unset in many shipped rows)
        c.d("region")                      # hash of a REGN name as written (FUN_004b9a10): particles start anywhere inside it; 0 = at pos
    c.f("life_min"); c.f("life_max")
    c.f("duration")                        # with flag 2: stops after this many seconds
    c.f("delay_min"); c.f("delay_max")     # time between particles (the game raises < 0.2 to 0.2)
    c.s("parent")                          # object it follows (pos is then local to it) or -NULL_OBJECT-
    if c.v < 122: c.d("flags")             # 1 on, 2 limited duration, 8 tint, 0x10 grows, 0x20 growth from life,
    c.q("name")                            # 0x40 tint varies, 0x200 world-space velocity on a parent
    for ch in "rgba": c.f(f"colour_{ch}")


def _camera(c: _Cursor):
    c.q("name"); c.d("flags")
    if 18 <= c.v < 84: c.d("u1")   # DS2 reader 0x4E01DE
    c.q("zone"); c.v3("pos")
    if not 150 <= c.v < 190:       # Global Storm's cameras: not mapped yet
        c.rest("p")
        return
    # Vietnam (0x545D40): typed through the shared blocks; field names generic except rot / target
    kw, fl, n = c.rec.kind, c.rec["flags"], [0]

    def floats(k=1):
        for _ in range(k):
            c.f(f"p{n[0]}"); n[0] += 1

    def path():
        c.d("path_d")
        if fl & 0x2000000:
            c.int_list("path"); floats()
        else:
            c.d("path_d2"); floats(3)
    if kw == "CAMFREE":
        c.v3("rot"); path(); floats(7 + 5 + 3)
    elif kw == "CAMTARGPOS":
        c.v3("target"); path(); c.d("e0"); c.d("e1"); floats(3 + 7 + 5 + 3)
    elif kw == "CAMTARGOBJ":
        c.d("target_id"); c.v3("offset"); path(); floats(7 + 5 + 3)
    else:
        c.any("target"); c.v3("offset"); c.v3("offset2"); floats(5 + 3)
    floats(); c.int_list("cams"); floats()
    if kw == "CAMCHASEOBJ" and fl & 0x4000000: c.any("extra")


# Keywords typed only in some scene versions (other versions keep them as untyped lines, as those games' readers
# did): the layouts were mapped from Vietnam's / Global Storm's loaders.
# (first, last + 1) scene version a keyword is typed in; Global Storm's v190+ layouts of these aren't mapped yet
_TYPED = {"ANMI": (0, 190), "VAMI": (0, 190), "GLOW": (148, 190), "ENVFOGSETTING": (113, 190), "RINFO": (150, 190),
          "ENVCOVERPOINT": (92, 999), "NEWPOSMARKER": (190, 999)}

_LAYOUTS = {
    "ENVI": _envi, "HLOI": _hloi, "PORT": _port, "NEWENVWAYPT": _waypoint, "NEWENVMARKER": _marker,
    "REGN": _region, "ENVSND": _envsnd, "DLGT": _light, "SLGT": _light, "PLGT": _light,
    "CAMFREE": _camera, "CAMTARGPOS": _camera, "CAMTARGOBJ": _camera, "CAMCHASEOBJ": _camera,
    "SWITCH": _switch, "EMITTER": _emitter, "PCLINK": _pclink,
    "ANMI": _envi, "VAMI": _envi, "GLOW": _glow, "ENVCOVERPOINT": _cover, "ENVFOGSETTING": _fog_setting,
    "RINFO": _rinfo, "NEWPOSMARKER": _posmarker,
}

# Section of each keyword (for the editor's outline).
_SECTIONS = {
    "ENVI": "world", "VAMI": "world", "ANMI": "world", "ENDI": "world", "RINFO": "world",
    "ENVFOGSETTING": "fog", "ENDFOGSETTINGS": "fog", "GLOW": "lights",
    "ENVCOVERPOINT": "coverpoints", "ENDCOVERPOINTS": "coverpoints",
    "HLOI": "objects", "HLOX": "objects", "PORT": "portals", "ENDP": "portals",
    "DLGT": "lights", "SLGT": "lights", "PLGT": "lights", "ENDL": "lights",
    "ENVSND": "sounds", "ENDENVS": "sounds", "NEWENVWAYPT": "waypoints", "ENDWAYPTS": "waypoints",
    "NEWENVMARKER": "markers", "ENDMARKERS": "markers", "REGN": "regions", "ENDREGIONS": "regions",
    "CAMFREE": "cameras", "CAMTARGPOS": "cameras", "CAMTARGOBJ": "cameras", "CAMCHASEOBJ": "cameras",
    "ENDC": "cameras", "NUMSWITCHES": "switches", "SWITCH": "switches", "EMITTER": "emit",
    "NEWPOSMARKER": "posmarkers", "ENDPOSMARKERS": "posmarkers",
}


class EnvFile:
    def __init__(self, text: str, eol: str = "\r\n"):
        self.eol = eol
        self.lines: list[str] = text.split(eol)
        self.version = 0
        self.records: list[Record] = []
        self.problems: list[str] = []
        self._parse()

    @classmethod
    def read(cls, data: bytes | str) -> "EnvFile":
        text = data.decode("latin-1") if isinstance(data, bytes) else data
        original = None
        if "\r\n" in text and "\n" in text.replace("\r\n", ""):
            # mixed line ends (LF-only sections in CRLF files: Vietnam's cameras / weapons / post effects, two Great
            # Escape sound sections): one style, so every record is its own line - the game's token reader takes
            # either. An unchanged scene still writes back its original bytes (to_bytes).
            original = text
            text = re.sub(r"(?<!\r)\n", "\r\n", text)
        env = cls(text, "\r\n" if "\r\n" in text else "\n")
        if original is not None:
            env._mixed = (text, original)
        return env

    # Global Storm: <LEVEL>.ENV is an index ("Version 209", "SPLIT", then the part files) - the scene itself is in
    # .ART (world, portals, lights, atmosphere), .DES (objects, waypoints, regions, switches, cameras) and .SND
    # (ambient sounds). .SI (script info) and .RDA (binary) aren't scene text. The parts are joined into one line
    # list with a "#PART <file>" line in front of each, so every command works on one scene; split_files() cuts it
    # back into the part files.
    SPLIT_TEXT_PARTS = (".ART", ".DES", ".SND")
    PART_MARK = "#PART "

    @classmethod
    def load(cls, path) -> "EnvFile":
        with open(path, "rb") as f:
            data = f.read()
        if data[:40].replace(b"\r", b"").split(b"\n")[1:2] == [b"SPLIT"]:
            return cls.load_split(path, data)
        return cls.read(data)

    @classmethod
    def load_split(cls, path, index: bytes) -> "EnvFile":
        import os
        folder = os.path.dirname(path)
        files = {f.upper(): os.path.join(folder, f) for f in os.listdir(folder)}
        text = index.decode("latin-1")
        lines = []
        for m in re.finditer(r'"([^"\r\n]+)"', text):
            name = m.group(1).replace("\\", "/").split("/")[-1]
            if os.path.splitext(name)[1].upper() not in cls.SPLIT_TEXT_PARTS:
                continue
            p = files.get(name.upper())
            if p is None:
                raise FileNotFoundError(f"scene part {name} not found next to {os.path.basename(path)}")
            part = open(p, "rb").read().decode("latin-1")
            lines.append(cls.PART_MARK + name)
            lines += part.split("\r\n" if "\r\n" in part else "\n")
        env = cls("\r\n".join(lines), "\r\n")
        env.split_index = index
        return env

    def split_files(self) -> dict:
        """{part file name: bytes} of a SPLIT scene (empty for a one-file scene)."""
        out, name, cur = {}, None, []
        for line in self.lines + [self.PART_MARK]:
            if line.startswith(self.PART_MARK):
                if name:
                    out[name] = self.eol.join(cur).encode("latin-1")
                name, cur = line[len(self.PART_MARK):].strip(), []
            else:
                cur.append(line)
        return out if getattr(self, "split_index", None) else {}

    def to_bytes(self) -> bytes:
        text = self.eol.join(self.lines)
        mixed = getattr(self, "_mixed", None)
        if mixed and text == mixed[0]:
            text = mixed[1]                     # unchanged: the original mixed line ends
        return text.encode("latin-1")

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    # --- parsing --------------------------------------------------------------------------------------------------
    def _tokens(self, i: int) -> list[Token]:
        return [Token(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(self.lines[i])]

    def _parse(self):
        self.records.clear(); self.problems.clear()
        section, last_envi = "header", None
        in_links = False
        for i, line in enumerate(self.lines):
            toks = self._tokens(i)
            if not toks:
                continue
            kind = toks[0].text if re.fullmatch(r"[A-Z<][A-Z0-9_/>]*|Version", toks[0].text) else ""
            if toks[0].text == "*" and section == "emit":
                kind = "EMITTER"                 # particle emitter rows have no keyword of their own
            if in_links:                         # version 79+: hierarchy lines until ENDPCLINKS
                if kind in ("ENDPCLINKS", "</SGLINKS>"):
                    in_links = False
                elif not (len(toks) == 1 and toks[0].text.isdigit()):   # (Denied Ops: the block's count line)
                    kind = "PCLINK"
            elif (kind in ("ENDI", "HLOSX") and 79 <= self.version < 300) or (kind == "<SGLINKS>" and self.version >= 300):
                in_links = True                  # Denied Ops: <SGLINKS> n, then "child" "parent" 0 lines
            if kind == "PHYI" and self.version >= 300:
                kind = "ENVI"                    # Denied Ops physics props: an ENVI line with another keyword
            if kind == "HLOU":
                kind = "HLOI"                    # DS2 squad units: an HLOI with one more name (token 0 keeps HLOU)
            if kind == "Version" and len(toks) > 1:
                self.version = int(toks[1].text)
            rec = Record(self, i, kind, toks)
            section = _SECTIONS.get(kind, section if kind == "" else kind.lower().strip("<>/"))
            rec.section = section
            self._layout(rec)
            if kind in ("VAMI", "ANMI") and last_envi is not None:
                last_envi.children.append(rec)
            if kind == "ENVI":
                last_envi = rec
            self.records.append(rec)
        self._link_parents()
        self._sync_counts()

    def _link_parents(self):
        """Version 79+: give every ENVI a ``parent`` alias onto its PCLINK line (the line the game reads it from)."""
        links = {str(r.get("name")): r for r in self.records if r.kind == "PCLINK" and r.fields}
        for r in self.records:
            r.aliases.clear()
            if r.kind in ("ENVI", "VAMI", "ANMI") and "parent" not in r.fields and r.fields:
                link = links.get(str(r.get("name")))
                if link is not None and str(link.get("parent", "")).startswith("@"):
                    link = None                  # Denied Ops: a zone links to "@<itself>" = top level
                r.aliases["parent"] = (link, "parent") if link is not None else (None, "Root")

    def zone_types(self) -> tuple:
        """ENVI types that are zones / world pieces: ZONE_TYPES (3 landscape, 5 world piece); Denied Ops (version 300+)
        only 3 - its 5 / 7 are PHYI physics props / destructible walls and 9 plain props (checked on Comms Base)."""
        return (3,) if self.version >= 300 else ZONE_TYPES

    def is_prop(self, rec: Record) -> bool:
        """A placed prop (copied, moved, listed in the library): ENVI type 0; Denied Ops every type but its zones."""
        if rec.kind != "ENVI" or not rec.fields:
            return False
        t = rec.get("type")
        return t not in self.zone_types() if self.version >= 300 else t == 0

    def set_link_parent(self, rec: Record, parent: str):
        """Give an ENVI without a PCLINK line one (the game reads no line = Root). Denied Ops: a quoted line in
        <SGLINKS> (its count follows, _sync_counts)."""
        if parent == "Root":
            return
        if self.version >= 300:
            end = next((r for r in self.records if r.kind == "</SGLINKS>"), None)
            if end is None:
                raise KeyError("no <SGLINKS> section to put the parent link in")
            self.insert_line(end.index, f' "{rec["name"]}" "{parent}" 0')
            return
        end = next((r for r in self.records if r.kind == "ENDPCLINKS"), None)
        if end is None:
            raise KeyError("no ENDPCLINKS section to put the parent link in")
        self.insert_line(end.index, f"{rec['name']} {parent} 0")

    # --- Denied Ops (version 300+): a placed thing is a block inside a tagged section --------------------------------
    # <SGNODE> 1 n "" </SGNODE> / the record line / its follow-up lines (RINFO, a bare number) / <MaterialOverrides> n
    # + n lines / </MaterialOverrides>, inside <ENVINSTANCES> (props, counted) or <HLOS>, <HLOS_HUMAN> ... (objects).
    def do_block(self, rec: Record) -> tuple[list[str], str | None]:
        """A Denied Ops record as the lines of a new block (material overrides left empty - theirs are named after
        the source instance) and the tag of the section it sits in."""
        lines = self.lines
        start = rec.index - 1 if rec.index and "<SGNODE>" in lines[rec.index - 1] else rec.index
        out = lines[start:rec.index + 1]
        k = rec.index + 1
        while k < len(lines) and not lines[k].lstrip().startswith(("<MaterialOverrides>", "<SGNODE>", "</")):
            out.append(lines[k])
            k += 1
        out += ["<MaterialOverrides> 0", "</MaterialOverrides>"]
        tag = None
        for j in range(start - 1, -1, -1):
            t = lines[j].strip()
            if t.startswith("<") and not t.startswith(("</", "<SGNODE>", "<MaterialOverrides>")) and \
                    not t.endswith("/>"):
                m = re.match(r"<([A-Za-z_]+)", t)
                tag = m.group(1) if m else None
                break
        return out, tag

    def insert_block(self, block: list[str], tag: str, keyword: str) -> Record:
        """Insert a Denied Ops block (do_block) at the end of section <tag>; returns its record (kind keyword)."""
        close = next((r.index for r in self.records if r.kind.upper() == f"</{tag.upper()}>"), None)
        if close is None:
            raise KeyError(f"this scene has no <{tag}> section")
        for k, line in enumerate(block):
            self.lines.insert(close + k, line)
        self._parse()
        return next(r for r in self.records if close <= r.index < close + len(block) and r.kind == keyword)

    def _layout(self, rec: Record):
        rec.fields.clear()
        fn = _LAYOUTS.get(rec.kind)
        lo, hi = _TYPED.get(rec.kind, (0, 999))
        if not lo <= self.version < hi:
            fn = None
        if not fn:
            return
        try:
            c = _Cursor(rec, self.version)
            fn(c)
            c.done()
        except ValueError as e:
            self.problems.append(str(e))

    def _replace_token(self, rec: Record, i: int, new: str, relink: bool = True):
        renamed = relink and rec.kind == "ENVI" and rec.fields.get("name") == i and "parent" in rec.aliases
        link = self._link_of(rec) if renamed else None
        line, tok = self.lines[rec.index], rec.tokens[i]
        self.lines[rec.index] = line[:tok.start] + new + line[tok.end:]
        rec.tokens = self._tokens(rec.index)
        self._layout(rec)
        if link is not None:                      # version 79+: the PCLINK line follows the ENVI's new name
            i = link.fields["name"]             # written like the link's token (Denied Ops quotes its names)
            self._replace_token(link, i, _format_like(link.tokens[i].text, str(rec["name"])))
        if renamed:
            self._link_parents()

    # --- queries (command layer) ----------------------------------------------------------------------------------
    def find(self, kind: str | None = None, **match) -> list[Record]:
        """Records of ``kind`` whose fields equal ``match`` (strings compare case-insensitively)."""
        out = []
        for r in self.records:
            if kind and r.kind != kind:
                continue
            ok = True
            for k, want in match.items():
                got = r.get(k)
                if isinstance(want, str) and isinstance(got, str):
                    ok = got.lower() == want.lower()
                else:
                    ok = got == want
                if not ok:
                    break
            if ok:
                out.append(r)
        return out

    def record_at(self, line: int) -> Record:
        """Record on 1-based ``line`` (the stable handle an editor / MCP client uses)."""
        for r in self.records:
            if r.index == line - 1:
                return r
        raise KeyError(f"no record on line {line}")

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.records:
            if r.kind:
                counts[r.kind] = counts.get(r.kind, 0) + 1
        return {"version": self.version, "lines": len(self.lines), "records": counts, "problems": len(self.problems)}

    # --- edits (command layer) ------------------------------------------------------------------------------------
    _SECTION_END = {"ENVI": "ENDI", "HLOI": "HLOX", "PORT": "ENDP", "ENVSND": "ENDENVS", "NEWENVWAYPT": "ENDWAYPTS",
                    "REGN": "ENDREGIONS", "DLGT": "ENDL", "SLGT": "ENDL", "PLGT": "ENDL",
                    "EMITTER": "</EMIT>"}

    def duplicate(self, rec: Record, at_end: bool = True, **changes) -> Record:
        """Copy ``rec`` and apply ``changes`` (field -> value). Returns the new record.

        The copy goes to the end of its section (``at_end``, default) so existing records keep their order -
        the game's precomputed level data refers to objects by position (see ``HLOI``)."""
        if rec.kind in ("ENVI",) and rec.children:
            raise ValueError("duplicating world pieces with VAMI/ANMI lines isn't supported yet")
        line = rec.index + 1
        if at_end:
            end = self._SECTION_END.get(rec.kind)
            if rec.tokens[0].text == "HLOU":
                end = "HLOUX"                     # DS2 squad units have their own section
            # no section end (cameras, unknown kinds) or a missing one: right after the source record
            line = next((r.index for r in self.records if end and r.kind == end and r.index > rec.index),
                        rec.index + 1 + len(rec.children))
        linked = rec.kind == "ENVI" and "parent" in rec.aliases   # version 79+: the parent is on a PCLINK line
        parent = str(rec["parent"]) if linked else None
        changes = dict(changes)
        new_parent = changes.pop("parent", parent) if linked else None
        self.lines.insert(line, self.lines[rec.index])
        self._parse()
        new = self.record_at(line + 1)
        if linked:
            # a scene name must stay unique (the PCLINK lines find their object by it)
            names = {str(r.get("name")) for r in self.records if r.kind == "ENVI" and r is not new}
            name = str(changes.pop("name", new["name"]))
            base, k = name, 1
            while name in names:
                k += 1
                name = f"{base}_{k}"
            self._replace_token(new, new.fields["name"], f'"{name}"', relink=False)
            self._link_parents()
            new.aliases["parent"] = (None, "Root")
            if new_parent and new_parent != "Root":
                self.set_link_parent(new, new_parent)
                new = next(r for r in self.records if r.kind == "ENVI" and str(r.get("name")) == name)
        for k, v in changes.items():
            new[k] = v
        return new

    def insert(self, kind: str, text: str) -> Record:
        """Add a new record from its line text: ``SWITCH`` after the last switch (``NUMSWITCHES`` follows), ``EMITTER``
        ("* ..." row) at the end of the <EMIT> block. Returns the parsed record (check ``problems``)."""
        if kind == "SWITCH":
            head = next((r for r in self.records if r.kind == "NUMSWITCHES"), None)
            if head is None:
                raise ValueError("the scene has no NUMSWITCHES line")
            line = max([r.index for r in self.records if r.kind == "SWITCH"] + [head.index]) + 1
        elif kind == "EMITTER":
            end = next((r for r in self.records if r.kind == "</EMIT>"), None)
            if end is None:
                raise ValueError("the scene has no <EMIT> block (scene version < 38)")
            line = end.index
        else:
            raise ValueError(f"insert: {kind} records are added with duplicate")
        before = len(self.problems)
        self.lines.insert(line, text)
        self._parse()
        new = self.record_at(line + 1)
        if new.kind != kind or len(self.problems) > before:
            del self.lines[line]
            self._parse()
            raise ValueError(f"not a valid {kind} line: {text[:80]}")
        return new

    def _sync_counts(self):
        """Counts the game reads instead of looking for an end keyword: ``NUMSWITCHES n`` (FUN_004f69a0 reads exactly n
        switches) and the bare number before the REGN lines (FUN_004fc9e0 reads exactly that many regions, then one
        token for ENDREGIONS - a region added without the count is ignored by the game). Kept equal to the records."""
        head = next((r for r in self.records if r.kind == "NUMSWITCHES"), None)
        if head is not None and len(head.tokens) >= 2:
            n = sum(1 for r in self.records if r.kind == "SWITCH")
            if head.tokens[1].value != n:
                self._replace_token(head, 1, str(n))
        if self.version >= 300:              # Denied Ops: "<ENVINSTANCES>" / "<SGLINKS>" then a count line
            for tag, kind in (("<ENVINSTANCES>", "ENVI"), ("<SGLINKS>", "PCLINK")):
                at = next((i for i, r in enumerate(self.records) if r.kind == tag), None)
                if at is None or at + 1 >= len(self.records):
                    continue
                head = self.records[at + 1]
                close = next((r.index for r in self.records[at:] if r.kind == "</" + tag[1:]), None)
                if close is None or not (len(head.tokens) == 1 and isinstance(head.tokens[0].value, int)):
                    continue
                n = sum(1 for r in self.records if r.kind == kind and head.index < r.index < close)
                if head.tokens[0].value != n:
                    self._replace_token(head, 0, str(n))
        for kind, end in (("REGN", "ENDREGIONS"), ("NEWENVWAYPT", "ENDWAYPTS")):
            if kind == "NEWENVWAYPT" and not 85 <= self.version < 190:
                continue
            first = next((i for i, r in enumerate(self.records) if r.kind in (kind, end)), None)
            if not first:
                continue
            head = self.records[first - 1]
            if head.kind == "" and len(head.tokens) == 1 and isinstance(head.tokens[0].value, int):
                n = sum(1 for r in self.records if r.kind == kind)
                if head.tokens[0].value != n:
                    self._replace_token(head, 0, str(n))

    def set_list(self, rec: Record, count_field: str, entries: list[tuple]) -> Record:
        """Replace a counted list (e.g. ``kit_count`` + ``kit<k>.item / .category / .amount``) with ``entries``
        (tuples in the list's field order; strings are written quoted). Returns the re-parsed record."""
        ci = rec.fields[count_field]
        n = rec[count_field]
        # tokens per item, from the record's own item 0: "kit0.item" ... (dotted) or "ai_list0" / "group0" (plain)
        base = count_field[:-len("_count")] + "0"
        width = sum(1 for k in rec.fields if k == base or k.startswith(base + "."))
        if n and not width:
            raise ValueError(f"{rec.kind} {count_field}: can't tell the list's item width")
        last = ci + n * width if n else ci
        parts = [str(len(entries))]
        for e in entries:
            parts += [f'"{v}"' if isinstance(v, str) else str(v) for v in e]
        line = self.lines[rec.index]
        toks = rec.tokens
        self.lines[rec.index] = line[:toks[ci].start] + " ".join(parts) + line[toks[last].end:]
        self._parse()
        return self.record_at(rec.index + 1)

    def delete(self, rec: Record):
        if rec.kind in ("ENDI", "HLOX", "ENDP", "ENDL", "ENDENVS", "ENDWAYPTS", "ENDMARKERS", "ENDREGIONS", "ENDC",
                        "NUMSWITCHES", "<EMIT>", "</EMIT>"):
            raise ValueError(f"{rec.kind} ends a section and can't be deleted")
        link = self._link_of(rec)
        drop = [rec.index + k for k in range(1 + len(rec.children))] + ([link.index] if link is not None else [])
        for i in sorted(drop, reverse=True):
            del self.lines[i]
        self._parse()

    def _link_of(self, rec: Record):
        """The PCLINK line holding an ENVI's parent (version 79+), or None."""
        a = rec.aliases.get("parent")
        return a[0] if a and a[0] is not None else None

    def insert_line(self, index: int, text: str):
        self.lines.insert(index, text)
        self._parse()


# --- writing a record in another scene version ----------------------------------------------------------------------

class _EmitRecord:
    """What a layout function reads from ``c.rec`` while converting: the source record's values, the target keyword."""

    def __init__(self, source: "Record", keyword: str, overrides: dict):
        self._src, self._over = source, overrides
        self.kind = source.kind
        self.tokens = [Token(keyword, 0, len(keyword))]
        self.index = source.index

    def __getitem__(self, name):
        if name in self._over:
            return self._over[name]
        return self._src[name]

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default


class _EmitCursor:
    """A layout function's cursor that writes instead of reads: each field it would read at ``version`` is written
    from the source record (by field name; a field the source doesn't have gets 0 / 0.0 / "" ...), so the version
    rules in one place (the readers above) serve both directions. Counts and flags the layout branches on come back
    as the values written."""

    def __init__(self, source: "Record", version: int, keyword: str, overrides: dict):
        self.rec = _EmitRecord(source, keyword, overrides)
        self.v, self.i = version, 1
        self.out = [keyword]
        self.missing = []

    def _value(self, name, default):
        if name is None:
            return default
        try:
            v = self.rec[name]
        except KeyError:
            self.missing.append(name)
            return default
        return default if v is None else v

    def d(self, name=None):
        v = int(self._value(name, 0))
        self.out.append(str(v))
        return v

    def f(self, name=None):
        v = float(self._value(name, 0.0))
        self.out.append("%.6f" % v)
        return v

    def q(self, name=None):
        v = str(self._value(name, ""))
        self.out.append('"%s"' % v)
        return v

    def s(self, name=None):
        src = self.rec._src
        if name in src.fields and name not in self.rec._over:
            tok = src.tokens[src.fields[name]]          # a bare name may be quoted in some files (switch regions)
            self.out.append(tok.text)
            return str(tok.value)
        v = str(self._value(name, "0"))
        self.out.append(v)
        return v

    def any(self, name=None):
        src = self.rec._src
        if name in src.fields and name not in self.rec._over:
            tok = src.tokens[src.fields[name]]          # untyped: written as it was (quoted or not)
            self.out.append(tok.text)
            return tok.value
        v = self._value(name, "0")
        self.out.append('"%s"' % v if isinstance(v, str) and (not v or re.search(r"\s", v)) else str(v))
        return v

    def opt_d(self, name=None):
        if name in self.rec._src.fields or name in self.rec._over:
            return self.d(name)
        return 0

    def int_list(self, prefix):
        n = self.d(f"{prefix}_count")
        for k in range(n): self.d(f"{prefix}{k}")

    def v3(self, name):
        for c in "xyz":
            self.f(f"{name}.{c}")

    def rest(self, prefix):
        n = 0
        while f"{prefix}{n}" in self.rec._src.fields:
            self.any(f"{prefix}{n}")
            n += 1

    def done(self):
        pass


def convert_record(rec: "Record", version: int, keyword: str | None = None, **overrides) -> str:
    """The record as a line of a scene of ``version`` (e.g. a Desert Storm II HLOI as Desert Storm's version 72),
    written by the same layout function that reads it: fields both versions have keep their values, fields only
    the target has are 0 / "", fields only the source has are dropped. ``overrides`` replace field values (name ->
    value, counts included: ``kit_count=0`` writes no kit). ``keyword``: the line's keyword (default the record's;
    Desert Storm II's HLOU squad lines are HLOI to Desert Storm). Raises KeyError for kinds without a layout."""
    fn = _LAYOUTS.get(rec.kind)
    if fn is None:
        raise KeyError(f"no layout for {rec.kind!r}")
    c = _EmitCursor(rec, version, keyword or rec.tokens[0].text, overrides)
    fn(c)
    return " ".join(c.out) + " "
