"""Editor commands for mission production and release: custom campaigns (campaign.txt), the briefing dossier, the
mission soundtrack, the scene's resource budget, co-op / versus spawns and the release package (a clean .zip).
Registered into commands.py's command table like logic.py, records.py and studio.py, so the UI and the MCP server get
the same commands.

What the game does with each (DesertStormFix):
- campaign.txt: SINGLE PLAYER > CUSTOM CAMPAIGNS lists `name = ...`, plays the `mission = <name>.lua` lines in order
  (campaigns.cpp; unknown keys are ignored - `difficulty` is for players and the README).
- Briefing: game.briefing(text) = the BRIEFING tab of the squad screen; game.set_string() replaces the level's own texts
  while this Lua level runs (cleared at the next level): SECTIONHDR / SECTIONDATE / SECTIONTEXT on the loading screen,
  MOV_TXT1 / MOV_TXT2 = the date / place captions of a base mission's intro. Pins are drawn into the tactical map
  picture (the MISSION tab map) - the game has no pin markers of its own.
- Soundtrack: game.music(<mod .wav>, loop) plays through PlaySound next to the level's own interactive score (IMUS in
  <level>.sch, which the game keeps running - it can't be switched off from a script yet). Combat = EVENT.COMBAT (an
  enemy reached combat alert); back to the ambient track after `calm_seconds` without COMBAT / SHOT / SPOTTED.
- Co-op spawns: players control squad soldiers by squad slot (FUN_00436f70): 2 players - Bradley + Connors P1, Foley +
  Jones P2; 3 players - Foley + Jones P1, Bradley P2, Connors P3; 4 players - Bradley, Foley, Connors, Jones = P1-P4.
  A player's spawn is where their soldier starts, so a spawn pad moves that squad soldier's record.
- Versus spawns: the PC game has no networking and no split-screen versus mode yet - stored and written into the
  script as data (editor.spawns) for later use.

The compiled Lua lives between `-- @editor begin: production` and `-- @editor end: production`, called from init() /
on_message() through editor_production_init() / editor_production_on_message(...) (added when missing).
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import time
import zipfile

from commands import (CommandError, Session, command, _find_file, _lua_value, _mod_folder, _script_path,  # noqa: F401
                      _vec, scene_problems)
import logic
from logic import _lua_text

HERE = os.path.dirname(os.path.abspath(__file__))
RELEASE_DIR = os.path.join(os.path.dirname(HERE), "dist", "releases")   # ModKit dist/ (gitignored)


def _need_level(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    return s.env


def _mods_dir(s: Session):
    if not s.game_dir:
        raise CommandError("game folder unknown - start the editor with --game")
    return os.path.join(s.game_dir, "Mods")


def _plain(v, what, n=80):
    v = str(v or "").strip()
    if "\n" in v or "\r" in v or len(v) > n:
        raise CommandError(f"{what}: one line, up to {n} characters")
    try:
        v.encode("latin-1")
    except UnicodeEncodeError:
        raise CommandError(f"{what}: only characters the game's font has (Western European letters)") from None
    return v


# ============================================================================================ campaigns ====
DIFFICULTIES = ["", "Easy", "Normal", "Hard", "Veteran"]
MAX_CAMPAIGNS = 8                     # campaigns.cpp reads at most 8


def _mission_file(m):
    m = os.path.basename(str(m or "").strip())
    if m.lower().endswith(".lua"):
        m = m[:-4]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,23}", m):
        raise CommandError(f"mission {m!r}: a mission name (letters, digits, _) or <name>.lua")
    return m.lower() + ".lua"


def _read_campaign(path):
    """{name, difficulty, missions, other} of a campaign.txt (other = lines the editor doesn't own, kept)."""
    out = {"name": "", "difficulty": "", "missions": [], "other": []}
    for line in open(path, encoding="latin-1").read().splitlines():
        t = line.strip()
        key, _, value = t.partition("=")
        key, value = key.strip().lower(), value.strip()
        if t and not t.startswith("#") and _ and key in ("name", "difficulty", "mission"):
            if key == "mission":
                out["missions"].append(value)
            else:
                out[key] = value
        elif t and not t.startswith("# made with") and not t.startswith("# Custom campaign"):
            out["other"].append(line)
    return out


def _write_campaign(path, name, difficulty, missions, other=()):
    lines = ["# Custom campaign - SINGLE PLAYER > CUSTOM CAMPAIGNS (DesertStormFix). Written by the Desert Storm Editor.",
             f"name = {name}"]
    if difficulty:
        lines.append(f"difficulty = {difficulty}")
    lines += list(other)
    lines += [f"mission = {m}" for m in missions]
    with open(path, "w", encoding="latin-1", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _lua_missions(mods, mod):
    """Mission scripts in a mod folder: [{file, mission, title}] (title from the editor's briefing when saved there)."""
    folder = os.path.join(mods, mod)
    out = []
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not f.lower().endswith(".lua"):
            continue
        meta = os.path.join(folder, f[:-4].upper() + ".editor.json")
        title, base = "", ""
        if os.path.exists(meta):
            try:
                m = json.load(open(meta))
                title, base = (m.get("briefing") or {}).get("title", ""), m.get("base", "")
            except (OSError, ValueError):
                pass
        out.append({"file": f.lower(), "mission": f[:-4].upper(), "title": title, "base": base})
    return out


@command("Custom campaigns: every mod's campaign.txt (title, difficulty, missions in play order - each with whether its "
         "script exists) and the mission scripts each mod has to sequence. The game lists up to 8 campaigns under "
         "SINGLE PLAYER > CUSTOM CAMPAIGNS (mods starting with _ are switched off).",
         mod="only this mod folder (default: all)")
def campaign_list(s: Session, mod=None):
    mods = _mods_dir(s)
    names = [mod] if mod else (sorted(d for d in os.listdir(mods) if os.path.isdir(os.path.join(mods, d)))
                               if os.path.isdir(mods) else [])
    every = {}                     # mission file -> mod (any mod can hold a campaign's mission: the index is global)
    for m in names if mod is None else sorted(os.listdir(mods)) if os.path.isdir(mods) else []:
        if os.path.isdir(os.path.join(mods, m)) and not m.startswith(("_", ".")):
            for e in _lua_missions(mods, m):
                every.setdefault(e["file"], m)
    out = []
    for m in names:
        folder = os.path.join(mods, m)
        if not os.path.isdir(folder):
            raise CommandError(f"no mod folder {m}")
        missions = _lua_missions(mods, m)
        entry = {"mod": m, "enabled": not m.startswith(("_", ".")), "available": missions, "campaign": None}
        path = os.path.join(folder, "campaign.txt")
        if os.path.exists(path):
            c = _read_campaign(path)
            own = {e["file"]: e for e in missions}
            seq = []
            for i, f in enumerate(c["missions"], 1):
                e = own.get(f.lower())
                seq.append({"order": i, "file": f, "title": e["title"] if e else "", "in_mod": bool(e),
                            "found_in": m if e else every.get(f.lower())})
            entry["campaign"] = {"name": c["name"] or m, "difficulty": c["difficulty"], "missions": seq}
        out.append(entry)
    active = [e for e in out if e["campaign"] and e["enabled"]]
    return {"mods": out, "active_campaigns": len(active), "max_campaigns": MAX_CAMPAIGNS, "difficulties": DIFFICULTIES}


@command("Write a mod's campaign.txt: its title in CUSTOM CAMPAIGNS, an optional difficulty rating (for players and "
         "the README - the game ignores it) and the missions in play order. Lines the editor doesn't own are kept.",
         mod="mod folder", name="campaign title", missions="mission names or <name>.lua files, in play order",
         difficulty="'', Easy, Normal, Hard or Veteran")
def campaign_save(s: Session, mod, name, missions, difficulty=""):
    folder = _mod_folder(s, mod)
    name = _plain(name, "campaign title", 60)
    if not name:
        raise CommandError("the campaign needs a title")
    if difficulty not in DIFFICULTIES:
        raise CommandError(f"difficulty: one of {', '.join(d or '(none)' for d in DIFFICULTIES)}")
    files = [_mission_file(m) for m in missions or []]
    if not files:
        raise CommandError("a campaign needs at least one mission")
    if len(set(files)) != len(files):
        raise CommandError("a mission is in the list twice")
    path = os.path.join(folder, "campaign.txt")
    other = _read_campaign(path)["other"] if os.path.exists(path) else []
    _write_campaign(path, name, difficulty, files, other)
    have = {e["file"] for e in _lua_missions(_mods_dir(s), mod)}
    missing = [f for f in files if f not in have]
    return {"path": path, "missions": files, "not_in_this_mod": missing,
            "note": "missions not in this mod must come from another enabled mod" if missing else ""}


@command("Reorder the missions of a mod's campaign.txt.", mod="mod folder",
         mission_order="the campaign's missions in the new order (names / files), or their current 1-based positions")
def campaign_reorder(s: Session, mod, mission_order):
    folder = _mod_folder(s, mod, create=False)
    path = os.path.join(folder, "campaign.txt")
    if not os.path.exists(path):
        raise CommandError(f"{mod} has no campaign.txt - save a campaign first")
    c = _read_campaign(path)
    cur = c["missions"]
    if all(isinstance(x, int) for x in mission_order or []):
        if sorted(mission_order) != list(range(1, len(cur) + 1)):
            raise CommandError(f"positions: each of 1..{len(cur)} once")
        new = [cur[i - 1] for i in mission_order]
    else:
        new = [_mission_file(m) for m in mission_order or []]
        if sorted(new) != sorted(m.lower() for m in cur):
            raise CommandError("the new order must hold exactly the campaign's missions: " + ", ".join(cur))
    _write_campaign(path, c["name"] or mod, c["difficulty"], new, c["other"])
    return {"path": path, "missions": new}


# =============================================================================================== briefing ====
PIN_TYPES = {"objective": "objective crosshair", "insertion": "insertion arrow", "extraction": "extraction LZ"}
BRIEF_BEGIN, BRIEF_END = "-- @editor begin: production", "-- @editor end: production"
HOOK_INIT = "    editor_production_init()   -- the editor's briefing, soundtrack and spawns (Briefing..., Atmosphere...)"
HOOK_MSG = "    editor_production_on_message({args})   -- the editor's soundtrack"


def _briefing(s):
    b = dict(getattr(s, "briefing", None) or {})
    return {"title": b.get("title", ""), "date": b.get("date", ""), "location": b.get("location", ""),
            "text": b.get("text", ""), "pins": list(b.get("pins", []))}


@command("The mission's briefing dossier: operation title, date / time, location, briefing text and the tactical map "
         "pins (game x / z in cm), plus the map bounds and the pin types.")
def briefing_get(s: Session):
    _need_level(s)
    out = {**_briefing(s), "pin_types": PIN_TYPES}
    out["base"] = logic.get_mission_intel(s)          # the base mission's own texts (to start from; empty if none)
    try:
        import studio
        out["map"] = studio.minimap_info(s)
    except CommandError as e:
        out["map"] = None
        out["map_problem"] = str(e)
    return out


@command("Set the briefing dossier (only the parts given). Compiled into the mission script on save: BRIEFING tab text "
         "(game.briefing), loading-screen title / date / place and the intro's date / place captions (game.set_string, "
         "this mission only). Pins are drawn into the tactical map with briefing_burn_map.", mutates=True,
         title="operation title (one line)", date="date / time line, e.g. '17 JAN 1991 - 02:40'",
         location="location line", text="briefing text (paragraphs; up to 2000 characters)",
         pins="[{type: objective|insertion|extraction, pos: [x, z] game cm, label, heading: degrees (insertion)}]")
def briefing_set(s: Session, title=None, date=None, location=None, text=None, pins=None):
    b = _briefing(s)
    if title is not None:
        b["title"] = _plain(title, "title", 60)
    if date is not None:
        b["date"] = _plain(date, "date", 60)
    if location is not None:
        b["location"] = _plain(location, "location", 60)
    if text is not None:
        t = str(text).replace("\r\n", "\n").strip()
        if len(t) > 2000:
            raise CommandError("briefing text: up to 2000 characters")
        try:
            t.encode("latin-1")
        except UnicodeEncodeError:
            raise CommandError("briefing text: only characters the game's font has") from None
        b["text"] = t
    if pins is not None:
        clean = []
        for p in pins:
            if p.get("type") not in PIN_TYPES:
                raise CommandError(f"pin type: one of {', '.join(PIN_TYPES)}")
            pos = p.get("pos")
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                raise CommandError("pin pos: [x, z] in game cm")
            clean.append({"type": p["type"], "pos": [round(float(pos[0]), 1), round(float(pos[1]), 1)],
                          "label": _plain(p.get("label", ""), "pin label", 24),
                          "heading": round(float(p.get("heading") or 0.0), 1)})
        if len(clean) > 24:
            raise CommandError("up to 24 pins")
        b["pins"] = clean
    s.briefing = b
    return b


def _page_paths(s: Session):
    import studio
    if not s.mission:
        raise CommandError("save the level as a mission first (Save as mission…) - the map belongs to its script")
    _img, page = studio._map_names(s)
    folder = os.path.join(s.game_dir or "", "Mods", s.mission[0])
    return folder, os.path.join(folder, page), os.path.join(folder, page[:-4] + ".clean.png")


@command("The tactical map picture to draw pins on: the clean copy (before pins were burnt in), else the saved map, "
         "else none (Generate Minimap first). A data: URL.")
def briefing_map_image(s: Session):
    _need_level(s)
    _folder, page, clean = _page_paths(s)
    for p in (clean, page):
        if os.path.exists(p):
            return {"image": "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode(),
                    "clean": p == clean}
    return {"image": None, "clean": False}


@command("Save the tactical map with the briefing pins drawn in (the picture briefing_map_image gave, with pins). The "
         "pin-less picture is kept as GW<NAME>0.clean.png so pins can be moved and burnt in again.",
         image_data_base64="PNG (data: URL fine), square, the map with pins")
def briefing_burn_map(s: Session, image_data_base64):
    import studio
    _need_level(s)
    _folder, page, clean = _page_paths(s)
    keep = open(clean, "rb").read() if os.path.exists(clean) else open(page, "rb").read() if os.path.exists(page) else None
    if keep is None:
        raise CommandError("no tactical map yet - Generate Minimap and save it to the mission first")
    r = studio.save_minimap(s, image_data_base64)
    with open(clean, "wb") as f:                    # save_minimap forgets the clean copy (a new picture): put it back
        f.write(keep)
    return {**r, "clean_copy": clean}


# ============================================================================================= soundtrack ====
def _imus(s: Session, level):
    """The level's interactive score streams named by its sound cache's IMUS chunks."""
    import studio
    try:
        cl, _have = studio._sch(s, level)
    except CommandError:
        return []
    out = []
    for c in cl:
        tag, raw = (c[0], c[1]) if isinstance(c, tuple) else (c[:4], c)     # sound.chunks: (tag, chunk bytes)
        if tag == b"IMUS":
            name = raw[16:16 + 128].split(b"\0", 1)[0].decode("latin-1")
            stem = os.path.basename(name.replace("\\", "/"))
            out.append({"stream": stem, "kind": "sting" if re.search(r"Mission(Success|Failure)", stem, re.I) else "score"})
    return out


def _wav_seconds(path):
    import wave
    try:
        with wave.open(path) as w:
            return round(w.getnframes() / max(w.getframerate(), 1), 1)
    except (wave.Error, EOFError, OSError):
        return None


@command("Soundtrack choices: the level's own interactive score (IMUS streams in <level>.sch - the game plays it "
         "itself), the .wav tracks in the mission's mod folder (playable by the script, with audition URLs) and the "
         "current selection.")
def music_options(s: Session):
    _need_level(s)
    tracks = []
    if s.mission and s.game_dir:
        folder = os.path.join(s.game_dir, "Mods", s.mission[0])
        for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if f.lower().endswith(".wav"):
                tracks.append({"file": f, "seconds": _wav_seconds(os.path.join(folder, f)),
                               "url": f"/modfile/{s.mission[0]}/{f}"})
    m = getattr(s, "music", None) or {}
    from commands import _setups
    bank = next((c["args"][0] for c in _setups().get(s.level or "", []) if c["slot"] == 0x0C and c["args"]
                 and isinstance(c["args"][0], str)), None)
    score = _imus(s, s.level)
    # one interactive score stream per level (the game shifts its intensity itself - there are no separate ambient /
    # combat streams), plus the success / failure stings; the base DLL loads the level's sound bank (+0x0C)
    base = {"sound_bank": bank, "score": [x["stream"] for x in score if x["kind"] == "score"],
            "stings": [x["stream"] for x in score if x["kind"] == "sting"]} if (bank or score) else None
    return {"level_score": score, "base": base, "tracks": tracks,
            "ambient_track": m.get("ambient", ""), "combat_track": m.get("combat", ""),
            "calm_seconds": m.get("calm_seconds", 20), "mission": list(s.mission) if s.mission else None,
            "note": "The level's own score keeps playing underneath; a .wav track plays on top (PlaySound, looped)."}


@command("Choose the mission's music: a looping ambient / stealth track from the start and a combat track when an "
         "enemy reaches combat alert, back to ambient after calm_seconds of quiet. Tracks are .wav files in the "
         "mission's mod folder ('' = none - only the level's own score).", mutates=True,
         ambient_track=".wav in the mod folder or ''", combat_track=".wav in the mod folder or ''",
         calm_seconds="seconds without combat before the ambient track returns (5-300)")
def music_set(s: Session, ambient_track="", combat_track="", calm_seconds=20):
    out = {}
    for key, v in (("ambient", ambient_track), ("combat", combat_track)):
        v = os.path.basename(str(v or "").strip())
        if v and (not v.lower().endswith(".wav") or not re.fullmatch(r"[A-Za-z0-9_\- .]{1,64}", v)):
            raise CommandError(f"{key} track: a .wav file name from the mod folder")
        if v and s.mission and s.game_dir and not os.path.exists(os.path.join(s.game_dir, "Mods", s.mission[0], v)):
            raise CommandError(f"{v} isn't in Mods\\{s.mission[0]} - upload it first")
        out[key] = v
    calm = float(calm_seconds)
    if not 5 <= calm <= 300:
        raise CommandError("calm_seconds: 5 - 300")
    out["calm_seconds"] = calm
    s.music = out
    return out


# ================================================================================================ spawns ====
SQUAD = [("HERO01", "Bradley"), ("HERO04", "Foley"), ("HERO02", "Connors"), ("HERO03", "Jones")]   # squad slots 0-3
OWNERS = {1: (0, 0, 0, 0), 2: (0, 1, 0, 1), 3: (1, 0, 2, 0), 4: (0, 1, 2, 3)}   # player index per slot (FUN_00436f70)
FACTIONS = {"allied": "US / UK", "iraqi": "Iraqi Army"}


def _squad(s: Session):
    """Squad slot -> its record (the playable one: not a cutscene double)."""
    out = {}
    for r in _need_level(s).find("HLOI"):
        model = str(r.get("model", "")).upper()
        for slot, (prefix, _name) in enumerate(SQUAD):
            if model.startswith(prefix) and slot not in out and not (int(r.get("flags", 0)) & 0x20000):
                out[slot] = r
    return out


def _spawn_info(s: Session):
    squad = _squad(s)
    pads = []
    for slot, r in sorted(squad.items()):
        pads.append({"slot": slot, "soldier": SQUAD[slot][1], "line": r.index + 1, "pos": list(r["pos"]),
                     "players": {str(n): OWNERS[n][slot] + 1 for n in (2, 3, 4)}})
    mp = getattr(s, "multiplayer", None) or {}
    soldiers = len(squad)
    return {"coop": pads, "max_players": min(4, max(1, soldiers)), "deathmatch": mp.get("deathmatch", {}),
            "factions": FACTIONS, "mode": mp.get("mode", "coop"),
            "note": "Co-op: each player starts on their squad soldier (P1-P4 = the 4-player assignment); with 2 or 3 "
                    "players a player leads several soldiers. Versus spawns are stored for scripts - the PC game has "
                    "no versus mode yet."}


@command("Co-op / versus spawn set-up: the squad soldiers players start on (with which player gets each for 2, 3 and "
         "4 players) and the stored versus spawn areas.")
def multiplayer_info(s: Session):
    return _spawn_info(s)


@command("Set multiplayer spawns. coop: spawns {P1..P4 or Bradley/Foley/Connors/Jones: [x, y, z]} moves that squad "
         "soldier's start (P1-P4 = squad slots 0-3, the 4-player assignment). deathmatch: spawns {allied: [[x, y, z] "
         "...], iraqi: [...]} replaces the stored faction spawn areas (data for scripts - no versus mode on PC yet).",
         mutates=True, mode="coop or deathmatch", spawns="see above")
def multiplayer_setup(s: Session, mode="coop", spawns=None):
    spawns = spawns or {}
    mp = dict(getattr(s, "multiplayer", None) or {})
    moved = []
    if mode == "coop":
        squad = _squad(s)
        names = {n.lower(): i for i, (_p, n) in enumerate(SQUAD)}
        for key, pos in spawns.items():
            k = str(key).strip().lower()
            slot = int(k[1:]) - 1 if re.fullmatch(r"p[1-4]", k) else names.get(k)
            if slot is None:
                raise CommandError(f"coop spawn {key!r}: P1-P4 or Bradley / Foley / Connors / Jones")
            r = squad.get(slot)
            if r is None:
                raise CommandError(f"{SQUAD[slot][1]} isn't in this level (only {len(squad)} squad soldier(s) - "
                                   f"Training 1-3 have 1, Missions 1-2 have 2)")
            r["pos"] = _vec(pos)
            moved.append({"soldier": SQUAD[slot][1], "line": r.index + 1, "pos": list(r["pos"])})
        if s.env.problems:
            raise CommandError("; ".join(s.env.problems[:3]))
    elif mode == "deathmatch":
        dm = {k: [list(p) for p in v] for k, v in (mp.get("deathmatch") or {}).items()}
        for side, pts in spawns.items():
            if side not in FACTIONS:
                raise CommandError(f"faction: {', '.join(FACTIONS)}")
            if len(pts) > 16:
                raise CommandError("up to 16 spawns per faction")
            dm[side] = [list(_vec(p)) for p in pts]
        mp["deathmatch"] = dm
    else:
        raise CommandError("mode: coop or deathmatch")
    mp["mode"] = mode
    s.multiplayer = mp
    warn = []
    if moved and s.level == "MISSION1" and any(m["soldier"] == "Foley" for m in moved):
        warn.append("Mission 1's Foley is the prisoner in the cell - the base mission's rescue expects him there")
    return {"moved": moved, "warnings": warn, **_spawn_info(s)}


# ============================================================================================ script block ====
COMBAT_TIMER = 9300


def has_production(s) -> bool:
    b, m = _briefing(s), getattr(s, "music", None) or {}
    dm = (getattr(s, "multiplayer", None) or {}).get("deathmatch") or {}
    return bool(b["title"] or b["date"] or b["location"] or b["text"] or b["pins"] or m.get("ambient")
                or m.get("combat") or any(dm.values()))


def block_text(s) -> str:
    b, m = _briefing(s), getattr(s, "music", None) or {}
    dm = (getattr(s, "multiplayer", None) or {}).get("deathmatch") or {}
    L = [f"{BRIEF_BEGIN} (briefing, soundtrack, spawns - rewritten by the editor on every save: Briefing..., "
         "Atmosphere..., Spawns...)", "editor = editor or {}"]
    # briefing: texts for this level only (the host clears game.set_string texts at the next level)
    sets = [("SECTIONHDR", b["title"]), ("SECTIONDATE", b["date"]), ("SECTIONTEXT", b["location"]),
            ("MOV_TXT1", b["date"]), ("MOV_TXT2", b["location"])]
    if any(v for _k, v in sets):
        L.append("if game.set_string then   -- loading screen title / date / place, the intro's date / place captions")
        L += [f"    game.set_string(\"{k}\", {_lua_text(v)})" for k, v in sets if v]
        L.append("end")
    full = "\n".join(x for x in (b["title"].upper(), " - ".join(v for v in (b["date"], b["location"]) if v),
                                 "", b["text"]) if x is not None).strip("\n")
    if b["text"] or b["title"]:
        L.append(f"if game.briefing then game.briefing({_lua_text(full)}) end   -- the squad screen's BRIEFING tab")
    L.append("editor.briefing_pins = {   -- tactical map pins (game x / z, cm), drawn into the map picture")
    for p in b["pins"]:
        L.append(f"    {{ type = \"{p['type']}\", x = {p['pos'][0]!r}, z = {p['pos'][1]!r}, label = {_lua_text(p['label'])}"
                 f", heading = {p.get('heading', 0.0)!r} }},")
    L.append("}")
    L.append("editor.spawns = {   -- versus spawn areas per faction (no versus mode on PC yet: data for scripts)")
    for side in FACTIONS:
        pts = ", ".join("{ %r, %r, %r }" % tuple(p) for p in dm.get(side, []))
        L.append(f"    {side} = {{ {pts} }},")
    L.append("}")
    amb, cmb = m.get("ambient", ""), m.get("combat", "")
    calm = float(m.get("calm_seconds", 20))
    L += [f"editor.music = {{ ambient = {_lua_value(amb) if amb else 'nil'}, combat = {_lua_value(cmb) if cmb else 'nil'}, "
          f"calm = {calm!r}, mode = nil, last = 0 }}",
          "function editor.play_music(mode)   -- 'ambient' / 'combat': loops that track (game.music, a mod .wav)",
          "    local m = editor.music",
          "    if m.mode == mode or not game.music then return end",
          "    m.mode = mode",
          "    local track = m[mode]",
          "    if track then game.music(track, true) elseif mode == 'ambient' then game.music(nil) end",
          "end",
          "function editor_production_init()   -- called from init()",
          "    if game.loading_save() then return end   -- a loaded save: SAVE_LOADED starts the music",
          "    editor.play_music('ambient')",
          f"    if editor.music.combat then game.timer({COMBAT_TIMER}, 2, true) end   -- checks for the end of combat",
          "end",
          "function editor_production_on_message(id, a, b)   -- called from on_message()",
          "    local m = editor.music",
          "    if id == game.EVENT.SAVE_LOADED then",
          "        m.mode = nil",
          "        editor.play_music('ambient')",
          f"        if m.combat then game.timer({COMBAT_TIMER}, 2, true) end",
          "    elseif id == game.EVENT.COMBAT and m.combat then   -- an enemy reached combat alert",
          "        m.last = game.time_ms()",
          "        editor.play_music('combat')",
          "    elseif (id == game.EVENT.SHOT or id == game.EVENT.SPOTTED) and m.mode == 'combat' then",
          "        m.last = game.time_ms()   -- the fight goes on",
          f"    elseif id == game.EVENT.TIMER and a == {COMBAT_TIMER} and m.mode == 'combat' then",
          "        if game.time_ms() - m.last > m.calm * 1000 then editor.play_music('ambient') end",
          "    end",
          "end",
          BRIEF_END]
    return "\n".join(L)


def update_text(text, s) -> str:
    """The script with a fresh production block (added before init(), or at the end) and its hook lines - only once
    the mission has a briefing, music or spawns, or the block is there already."""
    a = text.find(BRIEF_BEGIN)
    if a < 0 and not has_production(s):
        return text
    block = block_text(s)
    if a >= 0:
        b = text.find(BRIEF_END, a)
        text = text[:a] + block + text[b + len(BRIEF_END):] if b > a else text
    else:
        m = re.search(r"^function init\(\)", text, re.M)
        at = m.start() if m else len(text)
        text = text[:at] + block + "\n\n" + text[at:]
    own = re.sub(re.escape(BRIEF_BEGIN) + r".*?" + re.escape(BRIEF_END), "", text, flags=re.S)
    if not re.search(r"^\s*editor_production_init\(\)", own, re.M):
        m = re.search(r"^function init\(\)[^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + HOOK_INIT + "\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\nfunction init()\n" + HOOK_INIT + "\nend\n"
    if not re.search(r"^\s*editor_production_on_message\(", own, re.M):
        m = re.search(r"^function on_message\(([^)]*)\)[^\n]*\n", text, re.M)
        if m:
            params = [p.strip() for p in m.group(1).split(",") if p.strip() and p.strip() != "..."]
            args = (params + ["nil", "nil", "nil"])[:3]
            text = text[:m.end()] + HOOK_MSG.format(args=", ".join(args)) + "\n" + text[m.end():]
        else:
            text = (text.rstrip("\n") + "\n\nfunction on_message(id, a, b)\n" + HOOK_MSG.format(args="id, a, b")
                    + "\nend\n")
    return text


def update_script(path, s):
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()
    new = update_text(text, s)
    if new != text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)


def strip(text):
    """The script without the production block and its hooks (to compare scripts)."""
    text = re.sub(re.escape(BRIEF_BEGIN) + r".*?" + re.escape(BRIEF_END) + r"\n*", "", text, flags=re.S)
    return "\n".join(l for l in text.splitlines()
                     if "editor_production_init()" not in l and "editor_production_on_message(" not in l)


@command("The Lua the editor writes for the briefing, soundtrack and spawns (the production block).")
def production_lua(s: Session):
    _need_level(s)
    return {"lua": block_text(s), "written": has_production(s)}


# ================================================================================================ budget ====
# Guide values: the largest shipped single-player level by each measure (tools: production.measure over all 26
# levels, 2026-10-10). Past them the level is beyond anything the game was tested with - not a known crash limit.
# The one limit known to fail is the texture pool ("Out of memory" with large uncompressed textures).
SHIPPED_MAX = {"triangles": None, "objects": None, "texture_mb": None}   # filled from _shipped.json when measured
_MODEL_STATS = {}        # file path -> (mtime, triangles, [texture names])
_TEX_BYTES = {}          # file path -> bytes in video memory (with mip maps)
_FINDERS = {}


def _finder(folder):
    from conflict_ds_tools import evo
    if folder not in _FINDERS:
        any_file = next((os.path.join(folder, f) for f in os.listdir(folder)), os.path.join(folder, "x"))
        _FINDERS[folder] = (evo.AssetFinder(any_file), {})
    return _FINDERS[folder]


def _resolve(folder, model):
    """A model's .EVO (world pieces: <ZONE>nnn are objects of <ZONE>.EVO)."""
    finder, memo = _finder(folder)
    key = model.upper()
    if key not in memo:
        p = finder.find(key, (".evo",))
        if p is None and re.search(r"\d{3}$", key):
            p = finder.find(key[:-3], (".evo",))
        memo[key] = p
    return memo[key]


def _model_stats(path):
    from conflict_ds_tools import evo
    t = os.path.getmtime(path)
    hit = _MODEL_STATS.get(path)
    if hit and hit[0] == t:
        return hit[1], hit[2]
    tris, tex = 0, set()
    try:
        for n in evo.read(path):
            if n.flags & 0x180 or n.name.upper().startswith(("#BSP_", "#SHA_", "#HR_")):
                continue                     # collision / shadow / hit-region helpers aren't drawn
            tris += sum(len(m.triangles) for m in n.meshes)
            tex.update(name.upper() for _h, name in n.textures if name and name.upper() != "DEFAULT")
    except Exception:  # noqa: BLE001 - an unreadable model counts as empty
        pass
    _MODEL_STATS[path] = (t, tris, sorted(tex))
    return tris, sorted(tex)


def _texture_bytes(folder, name):
    finder, _memo = _finder(folder)
    p = finder.find(name, (".dds", ".tga"))
    if p is None:
        return 0
    if p not in _TEX_BYTES:
        size = 0
        try:
            with open(p, "rb") as f:
                head = f.read(128)
            if head[:4] == b"DDS ":
                size = os.path.getsize(p) - 128          # compressed + mips: what the card holds
            elif len(head) >= 18:
                w, h, bpp = struct.unpack_from("<HHB", head, 12)
                size = int(w * h * 4 * 4 / 3)            # TGA: 32-bit on the card, with mips
        except OSError:
            pass
        _TEX_BYTES[p] = size
    return _TEX_BYTES[p]


def measure(env, folder, s: Session | None = None):
    """Triangles / objects / texture memory of a scene (env) whose models come from `folder`."""
    tris = {"world": 0, "props": 0, "objects": 0, "terrain": 0}
    objects = {"objects": 0, "props": 0, "world_pieces": 0}
    textures, missing, world_files = set(), set(), set()
    per_model = {}
    for r in env.records:
        if r.kind not in ("ENVI", "HLOI") or not r.fields or "model" not in r.fields:
            continue
        model = str(r["model"]).upper()
        if r.kind == "ENVI" and r.get("type") == 5:
            objects["world_pieces"] += 1
            p = _resolve(folder, model)
            if p and p not in world_files:
                world_files.add(p)
                t, tex = _model_stats(p)
                tris["world"] += t
                textures.update(tex)
            continue
        objects["objects" if r.kind == "HLOI" else "props"] += 1
        if s is not None and (model in getattr(s, "terrains", {}) or model in getattr(s, "roads", {})
                              or model in getattr(s, "kits", {})):
            continue                         # the editor's own models: counted below
        p = _resolve(folder, model)
        if p is None:
            missing.add(model)
            continue
        t, tex = _model_stats(p)
        tris["objects" if r.kind == "HLOI" else "props"] += t
        textures.update(tex)
        per_model[model] = per_model.get(model, 0) + t
    if s is not None:
        for t in getattr(s, "terrains", {}).values():
            tris["terrain"] += 2 * (t.n - 1) ** 2 if t.n > 1 else 0
        for road in getattr(s, "roads", {}).values():
            tri = road.strip()[3]
            tris["terrain"] += len(tri) // 3 if tri and not isinstance(tri[0], (list, tuple)) else len(tri)
        for name, k in getattr(s, "kits", {}).items():
            tris["terrain"] += _kit_triangles(s, k)
    tex_bytes = sum(_texture_bytes(folder, n) for n in textures)
    heavy = sorted(per_model.items(), key=lambda kv: -kv[1])[:8]
    return {"triangles": sum(tris.values()), "triangles_by": tris, "objects": sum(objects.values()),
            "objects_by": objects, "textures": {"count": len(textures), "mb": round(tex_bytes / 2 ** 20, 1),
                         "counts": "model textures (DDS / TGA with mips); skins, sky and HUD art not counted"},
            "heaviest": [{"model": m, "triangles": t} for m, t in heavy], "missing_models": sorted(missing)[:20]}


def _kit_triangles(s: Session, k):
    """A placed kit piece's triangles (kits.pieces of its source model; cached by commands.kit_pieces)."""
    import commands
    try:
        key = (k["level"].upper(), k["source"][:-4].upper())
        if key not in commands._KIT_PIECES:
            commands.kit_pieces(s, k["source"][:-4], level=k["level"])
        return next((p["triangles"] for p in commands._KIT_PIECES[key] if p["index"] == k["index"]), 0)
    except Exception:  # noqa: BLE001 - a kit whose source is gone counts as empty
        return 0


def _shipped_path(s: Session):
    return os.path.join(s.data_dir, "_editor_cache", "shipped_budget.json")


def shipped_maxima(s: Session, refresh=False):
    """The largest shipped single-player level by each measure (measured once, cached in the data folder)."""
    from commands import EnvFile, _setups
    path = _shipped_path(s)
    if not refresh and os.path.exists(path):
        try:
            return json.load(open(path))
        except (OSError, ValueError):
            pass
    per = {}
    for level in sorted(_setups()):
        folder = os.path.join(s.data_dir, level.lower())
        env_path = _find_file(folder, level + ".ENV") if os.path.isdir(folder) else None
        if env_path:
            m = measure(EnvFile.load(env_path), folder)
            per[level] = {"triangles": m["triangles"], "objects": m["objects"], "texture_mb": m["textures"]["mb"]}
    out = {k: max((v[k] for v in per.values()), default=0) for k in ("triangles", "objects", "texture_mb")}
    out["by"] = {k: max(per, key=lambda lv: per[lv][k]) if per else "" for k in ("triangles", "objects", "texture_mb")}
    out["levels"] = per
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    return out


@command("Resource budget of the open scene: triangles drawn (world pieces once per zone file, props and objects per "
         "placement, the editor's terrain / roads / kit pieces), objects (HLOI + ENVI records), texture memory (DDS / TGA "
         "sizes with mips), against guide values = the largest shipped level (green up to it, yellow to +25 %, red "
         "beyond - untested territory, not a known crash limit). The first call measures every shipped level once "
         "(about a minute; cached).", refresh="re-measure the shipped levels")
def scene_budget(s: Session, refresh=False):
    _need_level(s)
    t0 = time.time()
    m = measure(s.env, s.folder, s)
    ref = shipped_maxima(s, bool(refresh))
    limits, ratio = {}, {}
    for k, cur in (("triangles", m["triangles"]), ("objects", m["objects"]), ("texture_mb", m["textures"]["mb"])):
        guide = ref.get(k) or 1
        limits[k] = {"guide": guide, "warn": round(guide * 1.25, 1), "largest_shipped": ref.get("by", {}).get(k, "")}
        ratio[k] = round(cur / guide, 3)
    level_ref = (ref.get("levels") or {}).get(s.level)
    return {**m, "limits": limits, "ratio": ratio, "base_level": level_ref, "seconds": round(time.time() - t0, 2),
            "notes": ["Guides are the largest shipped level, not measured crash points.",
                      "Known hard limits: 65535 vertices per texture in one model (exporter check); large "
                      "uncompressed textures fill the game's texture pool ('Out of memory') - keep DXT."]}


# ================================================================================================ release ====
SOURCE_SUFFIXES = (".editor.json", ".terrain.json", ".road.json", ".clean.png")
JUNK = {"thumbs.db", "desktop.ini", ".ds_store", "desertstormfix-report.txt"}
NEVER = (".dat", ".exe", ".dll", ".asi", ".sav", ".iso", ".7z", ".zip", ".rar")
_GAME_INDEX = {}


def _game_index(s: Session):
    """Upper-case file name -> [paths] of the player's game files: the extracted archives (incl. _<HASH> names) and the
    game folder (without Mods)."""
    from conflict_ds_tools.archive import archive_hash
    key = (s.data_dir, s.game_dir)
    if key in _GAME_INDEX:
        return _GAME_INDEX[key]
    names, hashes = {}, {}
    roots = [d for d in (s.data_dir, s.game_dir) if d and os.path.isdir(d)]
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in ("mods", "_editor_cache", "_collections")]
            for f in files:
                p = os.path.join(dirpath, f)
                m = re.fullmatch(r"_([0-9A-Fa-f]{8})\.\w+", f)
                if m:
                    hashes.setdefault(int(m.group(1), 16), []).append(p)
                else:
                    names.setdefault(f.upper(), []).append(p)
    _GAME_INDEX[key] = (names, hashes, archive_hash)
    return _GAME_INDEX[key]


def _same_bytes(a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def _game_copy(s: Session, path):
    """The game file `path` is a byte-for-byte copy of, or None (name or archive hash matches + same bytes)."""
    names, hashes, archive_hash = _game_index(s)
    up = os.path.basename(path).upper()
    for g in names.get(up, []) + hashes.get(archive_hash(up), []):
        try:
            if _same_bytes(path, g):
                return g
        except OSError:
            continue
    return None


def _uses_refs(folder):
    """Files a mod's .uses lists (built from the player's install at start-up)."""
    out = set()
    for f in os.listdir(folder):
        if f.lower().endswith(".uses"):
            for line in open(os.path.join(folder, f), errors="replace"):
                m = re.match(r"\s*(file|piece)\s+(\S+)", line)
                if m:
                    out.add(m.group(2).upper())
    return out


def _check_missions(s: Session, mod):
    """[(mission, problem)] - every mission of the mod opened in its own session and checked like Play does."""
    from commands import Session as _S
    out, summaries = [], []
    folder = os.path.join(_mods_dir(s), mod)
    for f in sorted(os.listdir(folder)):
        if not f.upper().endswith(".EDITOR.JSON"):
            continue
        name = f[:-len(".editor.json")].upper()
        t = _S(s.data_dir, s.game_dir)
        try:
            t.call("open_mission", {"mod": mod, "name": name})
        except Exception as e:  # noqa: BLE001
            out.append((name, f"doesn't open: {e}"))
            continue
        for p in scene_problems(t):
            out.append((name, f"line {p['line']}: {p['problem']}"))
        meta = json.load(open(os.path.join(folder, f)))
        summaries.append({"mission": name, "base": meta.get("base", ""), "briefing": meta.get("briefing") or {},
                          "objectives": [o.get("title", "") for o in meta.get("objectives") or []],
                          "players": len(_squad(t)) or 1,
                          "script": os.path.exists(os.path.join(folder, name.lower() + ".lua"))})
    return out, summaries


def _readme(mod, version, creator, campaign, summaries, files, plugin):
    title = (campaign or {}).get("name") or mod
    L = [title, "=" * len(title), "",
         f"Version {version}" + (f" - by {creator}" if creator else ""),
         f"A mission mod for Conflict: Desert Storm (PC), made with the Desert Storm Editor.", ""]
    if campaign and campaign.get("difficulty"):
        L += [f"Difficulty: {campaign['difficulty']}", ""]
    L += ["REQUIREMENTS", "------------",
          "- Conflict: Desert Storm for PC (Steam or GOG).",
          f"- DesertStormFix {plugin} or newer (dinput8.dll in the game folder) - it runs the Lua missions and builds",
          "  the game files this mod uses from your own install. No game files are included in this package.", "",
          "INSTALL", "-------",
          "1. Open the game folder (the one with DesertStorm.exe).",
          f"2. Extract this archive there, so you get Mods\\{mod}\\ next to DesertStorm.exe.",
          "3. Start the game. The mod is on by default (main menu > MODS lists it; a folder name starting with _",
          "   switches it off).", "",
          "PLAY", "----"]
    if campaign:
        L.append(f"SINGLE PLAYER > CUSTOM CAMPAIGNS > {title}, then pick the difficulty.")
        L.append("Missions in order:")
        titles = {m["mission"].lower() + ".lua": m["briefing"].get("title", "") for m in summaries}
        for i, m in enumerate(campaign.get("missions", []), 1):
            L.append(f"  {i}. {titles.get(m.lower()) or re.sub(r'(?i)[.]lua$', '', m)}")
    else:
        L.append("The missions are Lua levels: add them to a campaign (campaign.txt) or start one directly with")
        L.append("  DesertStorm.exe +level <mission>.lua")
    L.append("")
    if summaries:
        L += ["MISSIONS", "--------"]
        for m in summaries:
            b = m["briefing"]
            L.append(f"{m['mission']}" + (f" - {b['title']}" if b.get("title") else "") + f"  (map: {m['base']})")
            meta = " - ".join(x for x in (b.get("date"), b.get("location")) if x)
            if meta:
                L.append(f"  {meta}")
            if b.get("text"):
                words, line = b["text"].split(), "  "
                for w in words[:120]:
                    if len(line) + len(w) > 78:
                        L.append(line.rstrip())
                        line = "  "
                    line += w + " "
                L.append(line.rstrip() + (" ..." if len(words) > 120 else ""))
            if m["objectives"]:
                L.append("  Objectives: " + "; ".join(o for o in m["objectives"] if o))
            L.append(f"  Squad: {m['players']} soldier(s) - split-screen co-op for up to {m['players']} player(s).")
            L.append("")
    L += ["FILES", "-----"] + [f"  Mods\\{mod}\\{f.replace('/', chr(92))}" for f in files] + ["",
          "Conflict: Desert Storm is (c) SCi / Pivotal Games. This mod contains only its author's own work; files it",
          "needs from the game are taken from your installation at start-up (the .uses / .envpatch / .rmdpatch files).",
          ""]
    return "\r\n".join(L)


def _plugin_version():
    p = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ConflictDesertStormPatch", "CMakeLists.txt")
    try:
        m = re.search(r"project\(DesertStormFix VERSION (\S+)", open(p).read())
        return m.group(1) if m else "2.0.0"
    except OSError:
        return "2.0.0"


@command("Package a mod for release: checks every mission like Play does (crash causes), refuses byte copies of the "
         "game's files and archives, leaves the editor's own files out (unless include_sources), writes a README.txt "
         "(install steps, campaign, mission summaries) and zips it as dist/releases/<mod>-v<version>.zip in the ModKit "
         "(layout Mods\\<mod>\\..., README.txt at the top). Returns the file list and a download URL.",
         mod="mod folder", version="version, e.g. 1.0", creator="author name for the README",
         include_sources="also pack the editor files (.editor.json, terrain / road sources) so others can edit it")
def export_mod_zip(s: Session, mod, version="1.0", creator="", include_sources=False):
    folder = _mod_folder(s, mod, create=False)
    if not os.path.isdir(folder):
        raise CommandError(f"no mod folder {mod}")
    version = str(version or "1.0").strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.\-]{0,19}", version):
        raise CommandError("version: e.g. 1.0 or 1.2-beta")
    creator = _plain(creator, "creator", 60)
    if s.mission and s.mission[0] == mod and s.dirty:
        raise CommandError("the open mission has unsaved changes - save it (Save as mission / Play) first")
    problems, summaries = _check_missions(s, mod)
    if problems:
        raise CommandError("fix these first - they crash the game: " +
                           "; ".join(f"{m}: {p}" for m, p in problems[:5]))
    refs = _uses_refs(folder)
    files, left_out, copies, replaces = [], [], [], []
    for dirpath, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
        for f in sorted(names):
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, folder).replace("\\", "/")
            low = f.lower()
            if low in JUNK or low.endswith(("~", ".bak", ".tmp")):
                left_out.append({"file": rel, "why": "clutter"})
                continue
            if low.endswith(NEVER):
                left_out.append({"file": rel, "why": "archives / programs / saves are never packaged"})
                continue
            if low.endswith(SOURCE_SUFFIXES) and not include_sources:
                left_out.append({"file": rel, "why": "editor source (include_sources packs it)"})
                continue
            game = _game_copy(s, p)
            if game:
                if f.upper() in refs:
                    left_out.append({"file": rel, "why": "copy of a game file the mod's .uses builds anyway"})
                else:
                    copies.append(f"{rel} (= {os.path.relpath(game, os.path.dirname(game.rstrip(os.sep)))})")
                continue
            if f.upper() in _game_index(s)[0]:
                replaces.append(rel)
            files.append(rel)
    if copies:
        raise CommandError(f"{len(copies)} file(s) in the mod are exact copies of game files, which can't be shared: "
                           + ", ".join(copies[:6]) + ("…" if len(copies) > 6 else "") + ". Remove them (Remove "
                           "game copies), or re-save the mission so it references them in its .uses instead")
    if not files:
        raise CommandError("nothing to package")
    campaign = None
    cpath = os.path.join(folder, "campaign.txt")
    if os.path.exists(cpath):
        c = _read_campaign(cpath)
        campaign = {"name": c["name"] or mod, "difficulty": c["difficulty"], "missions": c["missions"]}
        missing = [m for m in c["missions"] if m.lower() not in {f.lower() for f in files}]
        if missing:
            left_out.append({"file": "campaign.txt", "why": "warning - missions not in this mod (the player needs "
                                                            "them from another mod): " + ", ".join(missing)})
    readme = _readme(mod, version, creator, campaign, summaries, files, _plugin_version())
    os.makedirs(RELEASE_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", mod)
    out = os.path.join(RELEASE_DIR, f"{safe}-v{version}.zip")
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("README.txt", readme)
        for rel in files:
            z.write(os.path.join(folder, rel.replace("/", os.sep)), f"Mods/{mod}/{rel}")
    with zipfile.ZipFile(tmp) as z:                  # read back: every entry intact
        bad = z.testzip()
        if bad:
            raise CommandError(f"the zip came out damaged at {bad}")
        listed = z.namelist()
    os.replace(tmp, out)
    return {"zip": out, "bytes": os.path.getsize(out), "files": listed, "left_out": left_out,
            "replaces_game_files": replaces, "missions": [m["mission"] for m in summaries],
            "url": f"/download/{os.path.basename(out)}", "readme": readme}


@command("Move the files of a mod that are exact copies of game files (what export_mod_zip refuses - e.g. a Save as mod "
         "of an unchanged level) out of the mod into the editor's backups. Copies the mod's .uses builds anyway go too.",
         mutates=False, mod="mod folder")
def remove_game_copies(s: Session, mod):
    import workflow
    folder = _mod_folder(s, mod, create=False)
    if not os.path.isdir(folder):
        raise CommandError(f"no mod folder {mod}")
    found = []
    for dirpath, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
        for f in names:
            p = os.path.join(dirpath, f)
            if _game_copy(s, p):
                found.append(os.path.relpath(p, folder))
    if not found:
        return {"removed": [], "backup": None}
    backup = workflow.backup_files(s, folder, found, mod, "game-copies")
    for rel in found:
        os.remove(os.path.join(folder, rel))
    return {"removed": [r.replace(os.sep, "/") for r in found], "backup": backup}

