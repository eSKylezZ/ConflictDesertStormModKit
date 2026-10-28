"""Desert Storm II's mission script info (<SCENE>.SI in the level archive: "MISSION_SCRIPT_INFO" / "VERSION 4").

The level loader reads it (CDS II.exe LoadEnvironment 0x411CBF -> 0x432CC0 -> CLevelInformation::
ReadMissionScriptInfo 0x4319E0) into the mission data manager 0x631848: areas, spawn points, damage regions and the
objectives. Desert Storm II's objectives live here, not in the mission DLL: 0x54-byte records the engine checks itself
(0x436340 on deaths / damage / spawns, 0x436C90 every frame, 0x4367F0 on pick-ups) and the DLL only switches through
API slots 0x5AC-0x5D0. Desert Storm has nothing like it - crossmission.py rebuilds it in Lua from parse_objectives().

One objective (tokens in file order -> record field):
    NAME "x"                                 (not stored)
    <RESULT> <delay ms>                      +0 / +4   MISSION_COMPLETE 0, MISSION_FAILED 1, ACTIVATE_OBJECTIVE 2,
                                                       DEACTIVATE_OBJECTIVE 3, NONE 4
    <TYPE>                                   +8        TYPES below
    <other record>                           +0xC      (ACTIVATE / DEACTIVATE target)
    "achieved text" "activated text"         +0x10 / +0x14  catalog keys of the pop-ups ("" none)
    OBJECTIVE_STATUS_*                       +0x18     INACTIVE 0, ACTIVE 1, COMPLETED 2
    "title" "description"                    +0x1C / +0x20  text keys - a record with a title is shown
    <int>                                    +0x24     (always 0, no reader found)
    NUM_REGIONS n (id "name")*n              +0x28     region ids (item ids for the pick-up type)
    NUM_HLOS n (id "name")*n                 +0x30     object script ids
    <waypoint> <mark HLOs on the map>        +0x38 / +0x50
    NUM_OBJECTIVE_IDS n id*n                 +0x3C     the COLLECTER's sub-records
    NUM_POSITIONS n (x y z)*n                +0x44     map markers
Text keys hash like Desert Storm's catalog keys (lower-cased LFSR, evo_write.texture_hash)."""
from __future__ import annotations

import re

RESULTS = ["MISSION_COMPLETE", "MISSION_FAILED", "ACTIVATE_OBJECTIVE", "DEACTIVATE_OBJECTIVE", "NONE"]
TYPES = ["ALL_HLOS_DESTROYED", "ANY_HLO_DESTROYED", "ANY_HLO_ZERO_LIFE", "COLLECTER",
         "ANY_PLAYER_HLO_HAS_PICKED_UP_ITEM", "EVACUATE_ALL_ALIVE_PLAYERS_TO_UNIT",
         "ONE_PLAYER_GRUNT_DEAD_OR_ALL_UNCONSCIOUS", "ALL_HLOS_ALIVE_AND_IN_REGION",
         "ALL_HLOS_ALIVE_AND_ANY_HLO_IN_REGION", "ANY_ALIVE_PLAYER_HLO_IN_REGION",
         "ALL_ALIVE_PLAYER_HLOS_IN_REGION", "SCRIPT_TRIGGERED", "HLO_REACHED_WAYPOINT"]
STATUS = {"OBJECTIVE_STATUS_INACTIVE": 0, "OBJECTIVE_STATUS_ACTIVE": 1, "OBJECTIVE_STATUS_COMPLETED": 2}


class ScriptInfoError(ValueError):
    pass


def _tokens(text):
    for m in re.finditer(r'"([^"]*)"|(\S+)', text):
        yield m.group(1) if m.group(1) is not None else m.group(2)


def parse_objectives(text: str) -> list[dict]:
    """The objective records of an .SI file, in file order (record index = position): dicts with result, delay,
    type, other, achieved / activated (text keys or ""), state, title / description (keys or ""), regions, hlos
    (script ids), waypoint, mark_hlos, subs, positions. [] when the file has no objectives section."""
    i = text.find("BEGIN_OBJECTIVES")
    if i < 0:
        return []
    t = _tokens(text[i:])

    def expect(word):
        got = next(t)
        if got != word:
            raise ScriptInfoError("expected %s, found %r" % (word, got))

    try:
        expect("BEGIN_OBJECTIVES")
        recs = []
        for _ in range(int(next(t))):
            expect("NAME")
            r = {"name": next(t)}
            r["result"] = RESULTS.index(next(t))
            r["delay"] = int(next(t))
            r["type"] = TYPES.index(next(t))
            r["other"] = int(next(t))
            r["achieved"], r["activated"] = next(t), next(t)
            r["state"] = STATUS.get(next(t), 0)
            r["title"], r["description"] = next(t), next(t)
            r["field_24"] = int(next(t))
            expect("NUM_REGIONS")
            r["regions"] = [(int(next(t)), next(t))[0] for _ in range(int(next(t)))]
            expect("NUM_HLOS")
            r["hlos"] = [(int(next(t)), next(t))[0] for _ in range(int(next(t)))]
            r["waypoint"] = int(next(t))
            r["mark_hlos"] = int(next(t)) != 0
            expect("NUM_OBJECTIVE_IDS")
            r["subs"] = [int(next(t)) for _ in range(int(next(t)))]
            expect("NUM_POSITIONS")
            r["positions"] = [tuple(float(next(t)) for _ in range(3)) for _ in range(int(next(t)))]
            recs.append(r)
        expect("END_OBJECTIVES")
    except (StopIteration, ValueError) as e:
        raise ScriptInfoError("objectives section unreadable: %s" % e) from None
    return recs


def objectives_lua(recs) -> str:
    """The records as a Lua table literal for crossmission's objective runtime (text keys as catalog hashes)."""
    from .evo_write import texture_hash

    def h(key):
        return "0x%08X" % texture_hash(key) if key else "nil"

    def ints(xs):
        return "{%s}" % ", ".join(str(x) for x in xs)

    rows = []
    for r in recs:
        pos = ", ".join("{%r, %r, %r}" % p for p in r["positions"])
        rows.append("  {result = %d, delay = %d, type = %d, other = %d, achieved = %s, activated = %s, state = %d, "
                    "title = %s, desc = %s, regions = %s, hlos = %s, waypoint = %d, mark = %s, subs = %s, "
                    "positions = {%s}}" % (
                        r["result"], r["delay"], r["type"], r["other"], h(r["achieved"]), h(r["activated"]),
                        r["state"], h(r["title"]), h(r["description"]), ints(r["regions"]), ints(r["hlos"]),
                        r["waypoint"], "true" if r["mark_hlos"] else "false", ints(r["subs"]), pos))
    return "{\n%s\n}" % ",\n".join(rows) if rows else "{}"


def parse_spawns(text: str) -> dict:
    """Areas, spawn points and damage regions of an .SI file (CDS II.exe CLevelInformation::ReadMissionScriptInfo):
    {"areas": [{"name", "zones", "grunts": [(script id, enabled)]}], "spawn_query": int, "spawn_points": [{"name",
    "start_enabled", "pos", "units" (script ids), "waves" (-1 unlimited), "field_28", "area" (index or -1),
    "enable_regions", "disable_regions"}], "damage_regions": [{"name", "enabled", "flag", "dps", "region"}]}.
    A spawn point line: start_enabled x y z unit_count waves field_28 "area"; then its units (script id "name"), its
    enable regions and its disable regions (n, then id "name" each). DS2's spawner (0x44E7A0) respawns the listed
    placed units - it creates none."""
    t = _tokens(text)

    def expect(word):
        got = next(t)
        if got != word:
            raise ScriptInfoError("expected %s, found %r" % (word, got))

    try:
        expect("MISSION_SCRIPT_INFO")
        expect("VERSION")
        version = int(next(t))
        expect("BEGIN_AREAS")
        areas = []
        for _ in range(int(next(t))):
            a = {"name": next(t)}
            expect("NUM_ZONES")
            a["zones"] = [next(t) for _ in range(int(next(t)))]
            expect("NUM_GRUNTS")
            a["grunts"] = []
            for _ in range(int(next(t))):
                flag, sid = int(next(t)), int(next(t))
                next(t)
                a["grunts"].append((sid, flag))
            areas.append(a)
        expect("END_AREAS")
        names = [a["name"] for a in areas]
        expect("BEGIN_SPAWN_POINTS")
        n = int(next(t))
        out = {"version": version, "areas": areas, "spawn_query": int(next(t)), "spawn_points": [],
               "damage_regions": []}
        for _ in range(n):
            expect("NAME")
            s = {"name": next(t), "start_enabled": int(next(t)), "pos": tuple(float(next(t)) for _ in range(3))}
            count = int(next(t))
            s["waves"], s["field_28"] = int(next(t)), int(next(t))
            area = next(t)
            s["area"] = names.index(area) if area in names else -1
            s["units"] = [(int(next(t)), next(t))[0] for _ in range(count)]
            s["enable_regions"] = [(int(next(t)), next(t))[0] for _ in range(int(next(t)))] if version >= 4 else []
            s["disable_regions"] = [(int(next(t)), next(t))[0] for _ in range(int(next(t)))] if version > 1 else []
            out["spawn_points"].append(s)
        expect("END_SPAWN_POINTS")
        expect("BEGIN_DAMAGE_REGIONS")
        for _ in range(int(next(t))):
            d = {"name": next(t)}
            if version > 2:
                d["enabled"], d["flag"], d["dps"], d["region"] = (int(next(t)) for _ in range(4))
            else:
                d["enabled"], d["dps"], d["region"] = (int(next(t)) for _ in range(3))
                d["flag"] = 0
            next(t)
            out["damage_regions"].append(d)
        expect("END_DAMAGE_REGIONS")
    except (StopIteration, ValueError) as e:
        raise ScriptInfoError("spawn section unreadable: %s" % e) from None
    return out


def spawns_lua(info) -> str:
    """parse_spawns() as a Lua table literal for crossmission's spawn runtime."""
    if not info:
        return "{points = {}, damage = {}}"

    def ints(xs):
        return "{%s}" % ", ".join(str(x) for x in xs)

    pts = ["    {start = %d, pos = {%r, %r, %r}, units = %s, waves = %d, area = %d, enable = %s, disable = %s}" % (
        s["start_enabled"], s["pos"][0], s["pos"][1], s["pos"][2], ints(s["units"]), s["waves"], s["area"],
        ints(s["enable_regions"]), ints(s["disable_regions"])) for s in info["spawn_points"]]
    dmg = ["    {enabled = %d, dps = %d, region = %d}" % (d["enabled"], d["dps"], d["region"])
           for d in info["damage_regions"]]
    return "{query = %d, points = {\n%s\n  }, damage = {\n%s\n  }}" % (
        info["spawn_query"], ",\n".join(pts), ",\n".join(dmg))


# ---- the whole file (DS2 VERSION 4, Vietnam 12, Global Storm 23): the editor's objectives view (editor/logic.py) ----
_TOKEN = re.compile(r'"[^"\r\n]*"|\S+')


class _Tokens:
    def __init__(self, text: str):
        self.t = _TOKEN.findall(text)
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def word(self):
        if self.i >= len(self.t):
            raise ScriptInfoError("unexpected end of file")
        v = self.t[self.i]
        self.i += 1
        return v

    def q(self):
        v = self.word()
        if not v.startswith('"'):
            raise ScriptInfoError(f"expected a quoted string at token {self.i - 1}, found {v!r}")
        return v[1:-1]

    def d(self):
        v = self.word()
        try:
            return int(v)
        except ValueError:
            raise ScriptInfoError(f"expected a number at token {self.i - 1}, found {v!r}") from None

    def f(self):
        return float(self.word())

    def expect(self, kw):
        v = self.word()
        if v != kw:
            raise ScriptInfoError(f"expected {kw} at token {self.i - 1}, found {v!r}")

    def counted(self, kw, item):
        self.expect(kw)
        return [item() for _ in range(self.d())]


def _objective(t: _Tokens, version: int = 23) -> dict:
    """Vietnam (VERSION 12) and Global Storm (VERSION 23); Vietnam has no co-op title / text keys and no text points."""
    t.expect("NAME")
    o = {"name": t.q()}
    t.expect("XPS")
    o["xp"] = t.d()
    o["award"] = t.word()
    o["result"] = t.word()
    o["result_delay_ms"] = t.d()
    o["completion"] = t.word()
    o["u1"] = t.d()
    o["complete_message"] = t.q()
    o["new_message"] = t.q()
    o["status"] = t.word().replace("OBJECTIVE_STATUS_", "").lower()
    o["priority"] = t.word().replace("OBJECTIVE_PRIORITY_", "").lower()
    o["title_key"], o["text_key"] = t.q(), t.q()
    o["mp_title_key"], o["mp_text_key"] = (t.q(), t.q()) if version > 12 else ("", "")
    o["u2"] = t.d()
    o["regions"] = t.counted("NUM_REGIONS", t.q)
    o["hlos"] = t.counted("NUM_HLOS", t.q)
    o["waypoints"] = t.counted("NUM_WAYPOINTS", t.q)
    o["u3"] = t.d()
    o["objective_ids"] = t.counted("NUM_OBJECTIVE_IDS", t.d)
    # compass entries (Vietnam): {from waypoint, to waypoint / object, area}
    o["compass"] = t.counted("NUM_COMPASS_ENTRIES", lambda: (t.q(), t.q(), t.q()))
    o["text_points"] = []
    if t.peek() == "NUM_TEXT_POINTS":
        o["text_points"] = t.counted("NUM_TEXT_POINTS", lambda: {"waypoint": t.q(), "text_key": t.q(), "u1": t.q(),
                                                                "u2": t.d(), "distance": t.f(), "u3": t.q()})
    return o


def _objective_v4(t: _Tokens) -> dict:
    """Desert Storm II (VERSION 4, reader 0x4319E0 -> objective records of 0x54 bytes the script's objective slots
    address by index): no XP / award / priority / co-op texts; regions and objects are "id name" pairs; positions."""
    t.expect("NAME")
    o = {"name": t.q(), "xp": 0, "award": ""}
    o["result"] = t.word()                       # MISSION_COMPLETE / MISSION_FAILED / ACTIVATE_ / DEACTIVATE_OBJECTIVE / NONE
    o["result_delay_ms"] = t.d()
    o["completion"] = t.word()
    o["u1"] = t.d()                              # +0xC: the objective a result acts on (-1 none)
    o["new_message"] = t.q()                     # +0x10 (hash)
    o["complete_message"] = t.q()                # +0x14 (hash)
    o["status"] = t.word().replace("OBJECTIVE_STATUS_", "").lower()
    o["title_key"], o["text_key"] = t.q(), t.q()
    o["mp_title_key"] = o["mp_text_key"] = ""
    o["priority"] = "primary" if o["title_key"] else "script"
    o["u2"] = t.d()                              # +0x24
    o["regions"] = [r for _i, r in t.counted("NUM_REGIONS", lambda: (t.d(), t.q()))]
    o["hlos"] = [h for _i, h in t.counted("NUM_HLOS", lambda: (t.d(), t.q()))]
    o["waypoints"] = []
    o["u3"] = t.d()                              # +0x38: an object id (-1 none)
    o["u4"] = t.d()                              # +0x50: flag
    o["objective_ids"] = t.counted("NUM_OBJECTIVE_IDS", t.d)
    o["positions"] = t.counted("NUM_POSITIONS", lambda: (t.f(), t.f(), t.f()))
    o["text_points"] = []
    return o


def read(text: str) -> dict:
    """{"version": n, "sections": {NAME: [raw tokens]}, "objectives": [dict]} of a .SI file's text."""
    t = _Tokens(text)
    t.expect("MISSION_SCRIPT_INFO")
    t.expect("VERSION")
    out = {"version": t.d(), "sections": {}, "objectives": []}
    while t.peek() is not None:
        kw = t.word()
        if not kw.startswith("BEGIN_"):
            continue
        name = kw[6:]
        count = t.d()
        if name == "OBJECTIVES":
            v = out["version"]
            out["objectives"] = [_objective_v4(t) if v < 10 else _objective(t, v) for _ in range(count)]
            t.expect("END_OBJECTIVES")
            continue
        start = t.i
        while t.peek() is not None and t.peek() != "END_" + name:
            t.i += 1
        out["sections"][name] = {"count": count, "tokens": t.t[start:t.i]}
        t.word()
    return out


def load(path) -> dict:
    with open(path, "rb") as f:
        return read(f.read().decode("latin-1"))
