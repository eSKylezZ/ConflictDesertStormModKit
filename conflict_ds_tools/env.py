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

    def __getitem__(self, name: str):
        if name in self.fields:
            return self.tokens[self.fields[name]].value
        if f"{name}.x" in self.fields:
            return tuple(self[f"{name}.{c}"] for c in "xyz")
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
    v = c.v
    if v > 9: c.d("flags")
    if v > 0: c.d("u1")
    c.q("name"); c.q("parent"); c.d("type"); c.q("model")
    if v > 48: c.q("texture")
    c.v3("pos"); c.v3("rot")
    if v > 36: c.v3("scale")
    if v > 66: c.d("u2"); c.d("u3")
    if c.rec["flags"] & 0x400000:  # breakable prop: shatters into shards
        c.f("break_health"); c.d("shard_count"); c.q("shard_texture"); c.f("shard_f0"); c.f("shard_f1")
        c.d("shard_alpha"); c.d("shard_d0"); c.d("shard_d1")
    # the scene's lights that light this prop (FUN_004fb4d0: count, then light indices; max 6 - more = "Instance ... has
    # n active lights" info box). Flag 0x1000000 = a " RINFO ..." line follows (render info, FUN_004fb2c0) - copies
    # made of this line alone must drop the flag (editor commands._drop_rinfo_flag), or the loader eats the next record.
    n = c.d("light_count")
    for k in range(n): c.d(f"light{k}")
    if v > 51: c.d("u4")


def _hloi(c: _Cursor):
    """Mirrors the loader (0x4F97E5..0x4FA552)."""
    v = c.v
    if v > 9: c.d("flags")
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
        if v >= 40: c.q("ai_name")
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
    if v > 70: c.d("u4")


def _waypoint(c: _Cursor):
    c.d("id")
    if c.v > 10: c.d("u1")
    c.q("name"); c.v3("pos")
    if c.v > 14: c.v3("rot")
    # FUN_004d1570: table [0x63C9B0] entry = id (600 entries of 0x20 B): +0x1C wait on arrival (ms), +0x18 the
    # next waypoint's id (-1 = the path ends) - patrol routes are chains of these; a unit starts on one with API
    # +0x110 set_path(unit, first id) (+ +0x114 set_ai_state(unit, 0)).
    c.d("wait_ms"); c.d("next")


def _marker(c: _Cursor):
    c.d("id"); c.v3("pos"); c.d("flags")
    for k in range(5): c.d(f"link{k}")
    c.s("type")
    if c.rec["flags"] & 0x80: c.s("name")


def _region(c: _Cursor):
    c.q("name"); t = c.d("type"); c.v3("pos")
    if t == 1:
        c.f("radius")
    else:
        c.v3("rot"); c.v3("min"); c.v3("max")


def _envsnd(c: _Cursor):
    c.q("sound"); c.q("name"); c.v3("pos"); c.d("u1")
    c.f("u2"); c.f("min_distance"); c.f("max_distance"); c.f("volume"); c.d("u3"); c.d("u4"); c.d("loop")


def _light(c: _Cursor):
    c.d("id"); c.q("name"); c.d("flags")
    for k in range(4): c.f(f"ambient{k}")
    for k in range(4): c.f(f"colour{k}")
    c.rest("p")


def _switch(c: _Cursor):
    # FUN_004f69a0 (switches.cpp): name (its hash as written, FUN_004b9a10 = what message 0x0D reports), zone, transform, type =
    # Switches.txt row (model off / on, use box), region = REGN the player must stand in, target_a / target_b = script
    # ids of the doors it opens / closes together (-1 none), then (v >= 62) a number the loader stores but nothing reads.
    c.q("name"); c.q("zone"); c.v3("pos"); c.v3("rot"); c.q("type"); c.q("region")
    c.d("target_a"); c.d("target_b")
    if c.v >= 62: c.d("number")


def _emitter(c: _Cursor):
    # "* ..." lines between <EMIT> / </EMIT> (FUN_004f2d70; run per frame by FUN_004f2840). Times in seconds, sizes
    # and speeds in cm; a particle gets a random value between each min / max pair.
    c.f("start_delay")                     # countdown before the first particle
    c.v3("pos"); c.d("type")               # particle type (4 flash, 6 smoke, 8 / 9 dust, 11 fire, 12 flame, 18 flare)
    c.v3("velocity_min"); c.v3("velocity_max")
    c.f("size"); c.f("end_size_min"); c.f("end_size_max")     # end size only with flag 0x10
    if c.v >= 53:
        c.f("growth")                      # size change per tick (flag 0x10 without 0x20; unset in many shipped rows)
        c.d("region")                      # hash of a REGN name as written (FUN_004b9a10): particles start anywhere inside it; 0 = at pos
    c.f("life_min"); c.f("life_max")
    c.f("duration")                        # with flag 2: stops after this many seconds
    c.f("delay_min"); c.f("delay_max")     # time between particles (the game raises < 0.2 to 0.2)
    c.s("parent")                          # object it follows (pos is then local to it) or -NULL_OBJECT-
    c.d("flags")                           # 1 on, 2 limited duration, 8 tint, 0x10 grows, 0x20 growth from life,
    c.q("name")                            # 0x40 tint varies, 0x200 world-space velocity on a parent
    for ch in "rgba": c.f(f"colour_{ch}")


def _camera(c: _Cursor):
    c.q("name"); c.d("flags"); c.d("u1"); c.q("zone"); c.v3("pos")
    c.rest("p")


_LAYOUTS = {
    "ENVI": _envi, "HLOI": _hloi, "PORT": _port, "NEWENVWAYPT": _waypoint, "NEWENVMARKER": _marker,
    "REGN": _region, "ENVSND": _envsnd, "DLGT": _light, "SLGT": _light, "PLGT": _light,
    "CAMFREE": _camera, "CAMTARGPOS": _camera, "CAMTARGOBJ": _camera, "CAMCHASEOBJ": _camera,
    "SWITCH": _switch, "EMITTER": _emitter,
}

# Section of each keyword (for the editor's outline).
_SECTIONS = {
    "ENVI": "world", "VAMI": "world", "ANMI": "world", "ENDI": "world",
    "HLOI": "objects", "HLOX": "objects", "PORT": "portals", "ENDP": "portals",
    "DLGT": "lights", "SLGT": "lights", "PLGT": "lights", "ENDL": "lights",
    "ENVSND": "sounds", "ENDENVS": "sounds", "NEWENVWAYPT": "waypoints", "ENDWAYPTS": "waypoints",
    "NEWENVMARKER": "markers", "ENDMARKERS": "markers", "REGN": "regions", "ENDREGIONS": "regions",
    "CAMFREE": "cameras", "CAMTARGPOS": "cameras", "CAMTARGOBJ": "cameras", "CAMCHASEOBJ": "cameras",
    "ENDC": "cameras", "NUMSWITCHES": "switches", "SWITCH": "switches", "EMITTER": "emit",
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
        return cls(text, "\r\n" if "\r\n" in text else "\n")

    @classmethod
    def load(cls, path) -> "EnvFile":
        with open(path, "rb") as f:
            return cls.read(f.read())

    def to_bytes(self) -> bytes:
        return self.eol.join(self.lines).encode("latin-1")

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    # --- parsing --------------------------------------------------------------------------------------------------
    def _tokens(self, i: int) -> list[Token]:
        return [Token(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(self.lines[i])]

    def _parse(self):
        self.records.clear(); self.problems.clear()
        section, last_envi = "header", None
        for i, line in enumerate(self.lines):
            toks = self._tokens(i)
            if not toks:
                continue
            kind = toks[0].text if re.fullmatch(r"[A-Z<][A-Z0-9_/>]*|Version", toks[0].text) else ""
            if toks[0].text == "*" and section == "emit":
                kind = "EMITTER"                 # particle emitter rows have no keyword of their own
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
        self._sync_counts()

    def _layout(self, rec: Record):
        rec.fields.clear()
        fn = _LAYOUTS.get(rec.kind)
        if not fn:
            return
        try:
            c = _Cursor(rec, self.version)
            fn(c)
            c.done()
        except ValueError as e:
            self.problems.append(str(e))

    def _replace_token(self, rec: Record, i: int, new: str):
        line, tok = self.lines[rec.index], rec.tokens[i]
        self.lines[rec.index] = line[:tok.start] + new + line[tok.end:]
        rec.tokens = self._tokens(rec.index)
        self._layout(rec)

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
            # no section end (cameras, unknown kinds) or a missing one: right after the source record
            line = next((r.index for r in self.records if end and r.kind == end and r.index > rec.index),
                        rec.index + 1 + len(rec.children))
        self.lines.insert(line, self.lines[rec.index])
        self._parse()
        new = self.record_at(line + 1)
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
        first = next((i for i, r in enumerate(self.records) if r.kind in ("REGN", "ENDREGIONS")), None)
        if first:
            head = self.records[first - 1]
            if head.kind == "" and len(head.tokens) == 1 and isinstance(head.tokens[0].value, int):
                n = sum(1 for r in self.records if r.kind == "REGN")
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
        del self.lines[rec.index:rec.index + 1 + len(rec.children)]
        self._parse()
