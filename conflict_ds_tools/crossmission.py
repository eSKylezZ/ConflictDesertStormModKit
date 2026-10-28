"""Another game's mission DLL running in Desert Storm (porting plan Part 4, route 1).

The DLL is translated by mission2lua (stage 1: every x86 instruction to Lua - it keeps the source compiler's register
conventions and its CRT, which the readable stage 2 doesn't follow yet for Desert Storm II's optimised DLLs) and then
adapted here: LevelSetup gets a script API table in the SOURCE game's layout instead of Desert Storm's. Each entry
is either Desert Storm's own function for that slot (editor/games/<game>/api_slots.json `ds1_slot`, aligned by code
similarity) or a Lua implementation (``LUA_SLOTS`` - version check, slots with no Desert Storm counterpart); a slot
with neither logs its first use and returns 0. Every slot logs its first call ("ds2 api +0x9C8 name -> ..."), so a
run shows which slots a mission really uses.

Not handled yet: the source game's message ids (the game sends Desert Storm's), its object ids where the level port
renumbered them, float-returning stubs."""
from __future__ import annotations

import json
import os
import struct

from . import scriptinfo

_HERE = os.path.dirname(os.path.abspath(__file__))
SLOT_FILES = {"ds2": os.path.join(_HERE, "..", "editor", "games", "ds2", "api_slots.json")}
TABLE_SIZE = {"ds2": 0x9F0}             # LevelSetup's version check: (1, 7, table size)
STUB_BASE = 0xFEE00000                  # made-up call targets for Lua slots (never real code)
DS1_API = 0x5ED238

# Lua bodies for slots, by source game: ARG(k) = the k-th argument (1-based), the result is returned
LUA_SLOTS = {
    "ds2": {
        0x000: "return 1  -- version check: (1, 7, 0x9F0) is Desert Storm II's table, this one",
        # register_standard_anims_a/b/c: the exe's built-in animation sets - the script host registers Desert
        # Storm's standard set (and EXTRA_ANIMS) instead
        0x00C: "return 0",
        0x010: "return 0",
        0x014: "return 0",
        # time_passed(t): 1 once t is behind the game clock - Desert Storm's get_time_ms (+0x094) clock
        0x0D0: "return ARG(1) < r32((r32(0x63C954) + 0x10) & 0xFFFFFFFF) and 1 or 0",
        0x018: "return 0",                       # register_standard_anims_d (as 0x00C - 0x014)
        # register_animation(state, file): Desert Storm's registrar, except Desert Storm II's own SCRIPTED_ANIM_n
        # states (Desert Storm has none): those animations are loaded (load_animation +0x524) and kept for
        # play_scripted_animation; the files Desert Storm hasn't got come with the port (mission_files)
        0x004: "local state = XS_CSTR(ARG(1))\n"
               "  local n = state:match('^SCRIPTED_ANIM_(%d+)$')\n"
               "  if n then XS_SCRIPTED[tonumber(n)] = XS_DS1(0x524, ARG(2)); return 1 end\n"
               "  return XS_DS1(0x004, ARG(1), ARG(2))",
        # set_ai_flag_40000000(soldier, on) = Desert Storm's set_ai_enabled(soldier, not on)
        0x358: "if XS_OBJ(ARG(1)) == 0 then return 0 end\n"
               "  return XS_DS1(0x180, ARG(1), ARG(2) ~= 0 and 0 or 1)",
        # scripted_anim(soldier, n, start): Desert Storm II's SCRIPTED_ANIM_n movement rules -> Desert Storm's
        # play_scripted_animation (+0x52C) with the animation register_animation loaded for n; stop = animation 0
        0x2F8: "if XS_OBJ(ARG(1)) == 0 then return 0 end\n"
               "  return XS_DS1(0x52C, ARG(1), ARG(3) ~= 0 and (XS_SCRIPTED[ARG(2)] or 0) or 0)",
        # holster(soldier) = Desert Storm's hold_item(soldier, -1)
        0x9C4: "if XS_OBJ(ARG(1)) == 0 then return 0 end; return XS_DS1(0x5CC, ARG(1), 0xFFFFFFFF)",
        # load_level_strings("M01Strs.loc") -> Desert Storm's (+0x058) with "DS2_M01Strs.loc": the texts come with the
        # port as section [DS2_M01] (mission_files) - "M01" is also Desert Storm's own Mission 1 (its texts untouched)
        0x080: "return XS_DS1(0x058, XS_STR('DS2_' .. XS_CSTR(ARG(1))))",
        # ---- slot mapping round 2 (editor/games/ds2/api_slots.json; DS2 = CDS II.exe, DS1 = DesertStorm.exe) ----
        # AI states: DS2's numbers from 3 up are Desert Storm's minus one (DS2's setter 0x498560 has combat 4/5,
        # Desert Storm's FUN_0049D870 5/6)
        0x150: "if XS_OBJ(ARG(1)) == 0 then return 0 end\n"
               "  local s = ARG(2); if s >= 3 and s < 0x80000000 then s = s + 1 end\n"
               "  return XS_DS1(0x114, ARG(1), s)",
        # register_goat_anims / register_defuse_nuke_anim: the same states Desert Storm's own missions register
        0x020: "for _, p in ipairs({{'GOAT_STANDING', 'goat_stand.prs'}, {'GOAT_GRAZING', 'goat_grazing.prs'},\n"
               "    {'GOAT_WALK', 'goat_walk.prs'}, {'GOAT_DEATH', 'goat_death.prs'}}) do\n"
               "    if not X.anim_loaded(XS_STR(p[1])) then XS_DS1(0x004, XS_STR(p[1]), XS_STR(p[2])) end\n"
               "  end\n  return 1",
        0x030: "if not X.anim_loaded(XS_STR('DEFUSE_NUKE')) then\n"
               "    XS_DS1(0x004, XS_STR('DEFUSE_NUKE'), XS_STR('Upright_Defuse.prs')) end\n  return 1",
        # get_average_frame_time: Desert Storm's timer has the same 30-frame average (+0xAC, seconds)
        0x0CC: "return XS_FLOAT(rf((r32(0x63C954) + 0xAC) & 0xFFFFFFFF))",
        # is_in_play: object +0xC bit 0
        0x0F8: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0 end; return r32((o + 0xC) & 0xFFFFFFFF) & 1",
        # get_distance_to_waypoint: 100000.0 out of play / unknown, else the distance (cm)
        0x178: "local o = XS_OBJ(ARG(1)); if o == 0 or r32((o + 0xC) & 0xFFFFFFFF) & 1 == 0 then\n"
               "    return XS_FLOAT(100000.0) end\n"
               "  local w = XS_SCRATCH(12); XS_DS1(0x0A0, w, ARG(2))\n"
               "  local dx = rf(w) - rf((o + 0x30) & 0xFFFFFFFF)\n"
               "  local dy = rf(w + 4) - rf((o + 0x34) & 0xFFFFFFFF)\n"
               "  local dz = rf(w + 8) - rf((o + 0x38) & 0xFFFFFFFF)\n"
               "  return XS_FLOAT(math.sqrt(dx * dx + dy * dy + dz * dz))",
        # team_in_region(region, team): Desert Storm's squad_in_region is the player team only
        0x1B0: "if ARG(2) ~= 0 then return 0 end; return XS_DS1(0x3D0, ARG(1))",
        # has_tracked_targets: Desert Storm's soldier keeps the same 3 tracked-target entries (+0x290, 0x18 each)
        0x1D4: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0 end\n"
               "  for k = 0, 2 do if r32((o + 0x290 + 0x18 * k) & 0xFFFFFFFF) ~= 0 then return 1 end end\n  return 0",
        # set_combat_ready / set_patrol_mode: bundles of Desert Storm's own set-up slots (DS1 state numbering)
        0x318: "if XS_OBJ(ARG(1)) == 0 then return 0 end; local o = ARG(1)\n"
               "  XS_DS1(0x20C, o); XS_DS1(0x188, o); XS_DS1(0x1E4, o, 4); XS_DS1(0x198, o, 0); XS_DS1(0x114, o, 6)\n"
               "  return 0",
        0x31C: "if XS_OBJ(ARG(1)) == 0 then return 0 end; local o = ARG(1)\n"
               "  XS_DS1(0x1E4, o, 4); XS_DS1(0x20C, o); XS_DS1(0x1DC, o, 1); XS_DS1(0x198, o, 1); XS_DS1(0x188, o)\n"
               "  return 0",
        # get_kill_count: Desert Storm keeps the same counters (soldiers +0x2C84, vehicles +0x2C88)
        0x344: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0 end\n"
               "  return (r32((o + 0x2C84) & 0xFFFFFFFF) + r32((o + 0x2C88) & 0xFFFFFFFF)) & 0xFFFFFFFF",
        # vehicle_is_occupied: Desert Storm's get_vehicle_driver gives the first occupied seat or -1
        0x368: "if XS_OBJ(ARG(1)) == 0 then return 0 end; return XS_DS1(0x80C, ARG(1)) ~= 0xFFFFFFFF and 1 or 0",
        # camera_shake_at(pos, radius, strength, p2, duration): Desert Storm shakes every camera (no position)
        0x3EC: "return XS_DS1(0x32C, ARG(3), ARG(4), ARG(5))",
        # spawn points: enable when clear (Desert Storm has no 'units alive' check) / force-enable
        # spawn points (SPAWNS_LUA): enable when its units are all gone / force-enable / enable / disable
        0x438: "if not XS_SPAWN_OK(ARG(1)) then return 0 end\n"
               "  if XS_SP_CLEAR(ARG(1)) then XS_SP_SET(ARG(1), 2) end\n  return 0",
        0x43C: "if not XS_SPAWN_OK(ARG(1)) then return 0 end; XS_DS1(0x378, ARG(1), 1); XS_SPS_ON(ARG(1)); return 0",
        0x434: "if not XS_SPAWN_OK(ARG(1)) then return 0 end; XS_DS1(0x378, ARG(1), ARG(2)); XS_SPS_ON(ARG(1)); return 0",
        0x440: "if not XS_SPAWN_OK(ARG(1)) then return 0 end\n"
               "  XS_SP_SET(ARG(1), ARG(2) ~= 0 and 3 or 1)\n  return 0",
        # push_dynamic_object_at(obj, force*, offset*): Desert Storm pushes at the body centre (strength 1)
        0x448: "if XS_OBJ(ARG(1)) == 0 then return 0 end; return XS_DS1(0x8C8, ARG(1), ARG(2), XS_BITS(1.0))",
        # squad_member_near_object(obj, radius) -> script id of a squad member within radius, else -1
        0x4AC: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0xFFFFFFFF end\n"
               "  local r = XS_TOF(ARG(2))\n"
               "  for _, m in ipairs(XS_TEAM(0)) do\n"
               "    local dx = rf((m + 0x30) & 0xFFFFFFFF) - rf((o + 0x30) & 0xFFFFFFFF)\n"
               "    local dz = rf((m + 0x38) & 0xFFFFFFFF) - rf((o + 0x38) & 0xFFFFFFFF)\n"
               "    if dx * dx + dz * dz <= r * r and m ~= o then return r32((m + 8) & 0xFFFFFFFF) end\n"
               "  end\n  return 0xFFFFFFFF",
        # doors: open + unlock / lock (Desert Storm's door flags: 8 = unlocked, mask 0x18)
        0x520: "if XS_OBJ(ARG(1)) == 0 then return 0 end\n"
               "  XS_DS1(0x440, ARG(1), 0x18, 8); return XS_DS1(0x43C, ARG(1), XS_BITS(1.0))",
        0x528: "if XS_OBJ(ARG(1)) == 0 then return 0 end; return XS_DS1(0x440, ARG(1), 0x18, 0)",
        # set_switch_state_by_name(hash, state): Desert Storm's takes the switch index (manager [0x63C9C4])
        0x548: "local m = r32(0x63C9C4); if m == 0 then return 0 end\n"
               "  local arr, n = r32((m + 8) & 0xFFFFFFFF), r32((m + 0x10) & 0xFFFFFFFF)\n"
               "  for k = 0, n - 1 do\n"
               "    if r32((arr + 100 * k + 0x44) & 0xFFFFFFFF) == ARG(1) then return XS_DS1(0x450, k, ARG(2)) end\n"
               "  end\n  return 0",
        # lights: one on/off slot / colours (Desert Storm II uses colour_b for both)
        0x7B4: "return XS_DS1(ARG(2) ~= 0 and 0x674 or 0x678, ARG(1))",
        0x7BC: "return XS_DS1(0x680, ARG(1), ARG(3), ARG(3), ARG(4))",
        0x79C: "return r32(0x6182FC)",
        # play_object_sound_if_quiet(object, sound, bank, min_distance, lipsync): no lip sync on Desert Storm
        0x89C: "if XS_OBJ(ARG(1)) == 0 then return 0 end; if XS_DS1(0x7E4, ARG(1)) ~= 0 then return 0 end\n"
               "  return XS_DS1(0x7DC, ARG(1), ARG(2), ARG(3), ARG(4))",
        # say_speech(soldier, event, flag, bank, flag2) / say_speech_4e(soldier): from the soldier's bank (+0x40)
        0x340: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0 end\n"
               "  local bank = ARG(4) ~= 0 and ARG(4) or r32((o + 0x40) & 0xFFFFFFFF)\n"
               "  return XS_DS1(0x7DC, ARG(1), ARG(2), bank, XS_BITS(1000.0))",
        0x33C: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0 end\n"
               "  return XS_DS1(0x7DC, ARG(1), 0x4E, r32((o + 0x40) & 0xFFFFFFFF), XS_BITS(1000.0))",
        # random_int(n): Desert Storm's table helper (seeded by the lockstep reseed online); 0 for n = 0
        0x9D0: "if ARG(1) == 0 then return 0 end; return XS_NAT(0x4B7E60, 0, ARG(1))",
        # clear_battlefield: Desert Storm's own clean-up (effects, projectiles, decals, corpses, emitters)
        0x6BC: "XS_NAT(0x486EB0, 0); return 0",
        # register_lipsync_anims: Desert Storm has no facial animation
        0x9D4: "return 0",
        # ---- objectives: Desert Storm II's records (OBJECTIVES_LUA), slot mapping round 2 ----
        0x5AC: "local r, i = XS_REC(ARG(1)); if r then XS_OBJ_ACTIVATE(i) end; return 0",
        0x5B0: "local r = XS_REC(ARG(1)); return (r and r.state == 1) and 1 or 0",
        0x5B4: "local r, i = XS_REC(ARG(1)); if r then XS_OBJ_ACHIEVE(i) end; return 0",
        0x5B8: "local r = XS_REC(ARG(1)); return (r and r.state == 2) and 1 or 0",
        0x5BC: "local r = XS_REC(ARG(1))\n"
               "  if r and r.state == 1 then r.state = 0; if r.disp then XS_DS1(0x4BC, r.disp) end end\n  return 0",
        0x5C4: "local r = XS_REC(ARG(1)); if r and r.disp then XS_OBJ_MARK(r, XS_WP_POS(ARG(2))) end; return 0",
        0x5C8: "local r = XS_REC(ARG(1)); if r and r.disp then\n"
               "    local p = XS_SCRATCH(16); local q = XS_WP_POS(ARG(2))\n"
               "    wf(p, q[1]); wf((p + 4) & 0xFFFFFFFF, q[2]); wf((p + 8) & 0xFFFFFFFF, q[3])\n"
               "    XS_DS1(0x4DC, r.disp, p) end\n  return 0",
        0x5CC: "local r = XS_REC(ARG(1)); if r and r.disp then for k = 0, 11 do XS_DS1(0x4D8, r.disp, k) end end\n"
               "  return 0",
        0x5D0: "local r = XS_REC(ARG(1)); if r and r.disp then XS_DS1(0x4CC, r.disp, ARG(2) ~= 0 and 1 or 0) end\n"
               "  return 0",
        # set_node_rotation(object, angles*, node name): only when the object's model has that node - a model ported
        # onto Desert Storm's static prop is one object, and a controller on a missing node crashed the scene update
        # (Mission 5's radar dishes, 0x4CF848)
        0x8F8: "local o = XS_OBJ(ARG(1)); if o == 0 then return 0 end\n"
               "  local name = XS_CSTR(ARG(3))\n"
               "  local h1, h2 = XS_NAT(0x4B9A10, 0, XS_STR(name)), XS_NAT(0x4B9A10, 0, XS_STR(name:upper()))\n"
               "  local inst = r32((o + 0x2C) & 0xFFFFFFFF)\n"
               "  local n, k = inst ~= 0 and r32((inst + 0xC8) & 0xFFFFFFFF) or 0, 0\n"
               "  while n ~= 0 and k < 256 do\n"
               "    local h = r32((n + 0xE4) & 0xFFFFFFFF)\n"
               "    if h == h1 or h == h2 then return XS_DS1(0x838, ARG(1), ARG(2), ARG(3)) end\n"
               "    n = r32((n + 0xE8) & 0xFFFFFFFF); k = k + 1\n"
               "  end\n"
               "  game.log(string.format('set_node_rotation: object %d has no node %s - skipped', ARG(1), name))\n"
               "  return 0",
        # scale_by_skill_task_time(object, seconds): the time as given (Desert Storm's soldiers have no such skill)
        0x248: "return XS_FLOAT(XS_TOF(ARG(2)))",
        # play_object_sound(object, sound, bank, flag): Desert Storm II plays it on the object's voice channel; the
        # closest Desert Storm has is play_sound(bank, sound) (+0x2F8, not positional)
        0x8A0: "if XS_OBJ(ARG(1)) == 0 then return 0 end; return XS_DS1(0x2F8, ARG(3), ARG(2))",
        # set_medikit_uses(soldier, n): Desert Storm keeps MediKit uses its own way - left out (logged)
        0x6E8: "return 0",
        # Desert Storm II's music volume / fade and view tint / effect reset: no Desert Storm counterpart
        0x9B8: "return 0",
        0x9C0: "return 0",
        0x858: "return 0",
        0x870: "return 0",
    },
}



def _ds2_messages():
    """Desert Storm message id -> Desert Storm II's. Both exes' send sites (mov reg, [script object]; push id;
    call [reg + 8] - DS1 0x60F578, DS2 0x62FE00) were paired by the code around them (scratchpad msg_align.py,
    2026-10-24): the ids are one ordered list that Desert Storm II changed in places. Anchors (paired sends):
    0x09 0x0F 0x11 0x15 0x19 same, 0x1C>0x1D 0x1F>0x20, 0x29>0x26 0x2A>0x27 0x2B>0x28, 0x3B>0x32 0x40>0x37 0x41>0x38
    0x44>0x3B 0x45>0x3C 0x47>0x3E, 0x4D>0x43 0x4E>0x44, 0x53>0x48; between anchors the order holds. Ids in gaps
    where the counts differ aren't mapped (0x21-0x26, 0x2C-0x3A, 0x4A, 0x4B, 0x50-0x52)."""
    m = {i: i for i in range(0x00, 0x1C)}
    m.update({i: i + 1 for i in range(0x1C, 0x21)})
    m.update({i: i - 3 for i in range(0x27, 0x2C)})
    m.update({i: i - 9 for i in range(0x3B, 0x4A)})
    m.update({i: i - 10 for i in range(0x4C, 0x50)})
    m[0x53] = 0x48
    # enemy spotted (observer, target): by code order in the AI spotting functions - DS1 sends 0x17, 0x44, 0x34, 0x3F,
    # 0x3E (0x405493..0x409253), DS2 0x17, 0x3B, 0x5B, 0x36, 0x35 (0x404CFD..0x406FA5); both pass the two script ids
    # first (DS2's block adds two u16 and two zero dwords after them)
    m[0x34] = 0x5B
    # Desert Storm II added a message at 0x12 - "mission failed" (sent after a MISSION_FAILED objective's delay,
    # 0x4366B0) - so Desert Storm's 0x12 (the overview / film ended, play starts: MovieEnd's push 0x12 at 0x486FDE) is
    # Desert Storm II's 0x13 (its movie_end 0x4492C0 sends push 0x13). Passed through as 0x12 it told Desert Storm II's
    # Mission 8 the mission had failed the moment play began. Desert Storm's 0x13 / 0x14 have no anchor before 0x15.
    m[0x12] = 0x13
    m[0x13] = None
    m[0x14] = None
    return m


# message ids: the game sends Desert Storm's; the source's script expects its own (None = not delivered)
MESSAGES = {"ds2": _ds2_messages()}

# Desert Storm animation states the source's scripts lead to (through Desert Storm's movement rules) that the script
# host's standard set lacks - Desert Storm's own missions register these themselves (state, .prs). Without one, the
# soldier's animation channel stays empty and its next AI turn crashes (0x42DF97).
EXTRA_ANIMS = {
    "ds2": [
        ("TALKING", "Master_sergeant.prs"),     # MSR_START_TALKING (start_talking); Desert Storm's training levels'
    ],
}

_MARK = "-- ======================== level ========================"


def mission_files(src, dst, level, dll_bytes):
    """({file: bytes}, notes) a source mission needs besides its scene, named so the DLL's own requests find them:
    - its level sound cache as a pack (``<LEVEL>_MISSION.sch``): Desert Storm adds a mod's packs to the level cache
      (missing banks / samples), so the DLL's sound bank (Training03.sbk) and its voice lines (Desert Storm II keeps
      them as samples in the cache, Tm03_*.psf) are there;
    - its texts as ``<GAME>_<ID>.strings`` with section [<GAME>_<ID>], <ID> taken from the .loc the DLL loads
      ("M01Strs.loc" -> DS2_M01): the adapter's load_level_strings asks for "DS2_M01Strs.loc", which DesertStormFix
      serves from that section (there is no such file) - Desert Storm's own M01Strs.loc (its Mission 1) and its texts
      stay untouched. The movie texts (show_movie_text) come from the same section."""
    import re
    from . import games, porting, sound
    files, notes = {}, []
    path = games.game_file(src.folder, level.lower() + ".sch") if src.folder else None
    if path:
        with open(path, "rb") as f:
            ch = sound.chunks(f.read())
        # banks in Desert Storm's layout (Desert Storm II's entries carry 20 more bytes: "cache corrupt"); a bank
        # Desert Storm has itself (same key: US_Soldier, door and gun banks) stays Desert Storm's
        own = _dst_bank_keys(dst)
        banks, kept = [], 0
        for t, c in ch:
            if t == b"BANK":
                if struct.unpack_from("<I", c, 8)[0] in own:
                    kept += 1
                    continue
                banks.append(sound.bank_to_ds1(c))
        smp = [c for t, c in ch if t in (b"PFSM", b"PFST")]
        files["%s_MISSION.sch" % level.upper()] = sound.build(banks + smp)
        notes.append("sound cache: %s's %s.sch as a pack - %d bank(s) (%d left to %s's own of that name), %d "
                     "sample(s)" % (src.game.short, level, len(banks), kept, dst.game.short, len(smp)))
    else:
        notes.append("sound cache: %s's %s.sch not found - the mission's sounds are missing" % (src.game.short, level))
    # animations the DLL registers (state, "<name>.prs" - the game loads <name>.prb) that Desert Storm hasn't got: the
    # source's, retargeted onto Desert Storm's soldier under the same name, so the DLL's request finds them
    ported, failed = [], []
    for m in sorted(set(re.findall(rb"([A-Za-z0-9_\-]+)\.prs\b", dll_bytes, re.I))):
        anim = m.decode("latin-1")
        if dst.get(anim + ".PRB") is not None or src.get(anim + ".PRB") is None:
            continue
        try:
            fname, data, _an = porting.port_animation(src, dst, anim, lambda a=anim: src.get(a + ".PRB"), new_name=anim)
        except (porting.PortError, Exception) as e:          # noqa: BLE001 - one animation shouldn't stop the rest
            failed.append("%s (%s)" % (anim, e))
            continue
        files[fname] = data
        ported.append(anim)
    if ported:
        notes.append("animations ported from %s: %s" % (src.game.short, ", ".join(ported)))
    if failed:
        notes.append("animations not ported: " + "; ".join(failed))
    for m in sorted(set(re.findall(rb"([A-Za-z0-9]+)Strs\.loc", dll_bytes))):
        sid = m.decode("latin-1").upper()
        try:
            fname, text, tnotes = porting.texts_file(src, dst, sid)     # section DS2_M01 (load_level_strings asks for it)
        except porting.PortError as e:
            notes.append("texts %s: %s" % (sid, e))
            continue
        files[fname] = text.replace("\n", "\r\n").encode("latin-1")     # as the porter's texts recipe writes them
        notes += tnotes
    return files, notes


_BANK_KEYS = {}


def _dst_bank_keys(dst):
    """Keys of every sound bank in the target's level caches (the game folder or, once DesertStormFix's game layer has
    moved them, PSE\\Originals)."""
    import glob
    if id(dst) in _BANK_KEYS:
        return _BANK_KEYS[id(dst)]
    from . import sound
    keys = set()
    for folder in (dst.folder or "", os.path.join(dst.folder or "", "PSE", "Originals")):
        for p in glob.glob(os.path.join(folder, "*.sch")):
            with open(p, "rb") as f:
                try:
                    ch = sound.chunks(f.read())
                except ValueError:
                    continue
            keys |= {struct.unpack_from("<I", c, 8)[0] for t, c in ch if t == b"BANK"}
    _BANK_KEYS[id(dst)] = keys
    return keys


def object_args(game):
    """{source slot offset: [argument numbers (1-based) that are objects]} from the game's api_slots.json."""
    with open(SLOT_FILES[game], encoding="utf-8") as f:
        data = json.load(f)
    return {int(k, 16): [n for n, a in enumerate(v.get("args") or [], 1) if a.get("type") == "object"]
            for k, v in data.items() if any(a.get("type") == "object" for a in v.get("args") or [])}


# Desert Storm II's objective records, run in Lua (CDS II.exe: shown at load in record order, achieve 0x4366B0,
# activate 0x436620, checks 0x436340 / 0x436C90; record format: scriptinfo.py). Record numbers are 0-based like the
# source's API slots; XS_OR[n + 1] holds record n.
OBJECTIVES_LUA = r"""
local XS_OR = @RECORDS@
local XS_OBJ_ENDED, XS_OBJ_FAIL_AT, XS_OBJ_MISSING = false, nil, {}
local function XS_HP(id)                         -- an object's health (nil if the level hasn't got it)
  local o = XS_OBJ(id); if o == 0 then return nil end
  return rf((o + 0x54) & 0xFFFFFFFF)
end
local function XS_GONE(id)                       -- destroyed / finally dead (Desert Storm's is_dead)
  if XS_OBJ(id) == 0 then
    if not XS_OBJ_MISSING[id] then
      XS_OBJ_MISSING[id] = true
      game.log(string.format("objectives: object %d isn't in the level - counted as destroyed", id))
    end
    return true
  end
  return XS_DS1(0x100, id) ~= 0
end
local function XS_IN(id, region) return XS_OBJ(id) ~= 0 and XS_DS1(0x15C, id, region) ~= 0 end
local function XS_PLAYERS()                       -- the player team's soldiers (script ids)
  local out = {}
  for _, m in ipairs(XS_TEAM(0)) do
    local unit = r32(m) == 0x5D88E0 and r32((m + 0x24) & 0xFFFFFFFF) or 0
    local slot = unit ~= 0 and r8((unit + 0x1C7) & 0xFFFFFFFF) or 0xFF   -- squad slot 0-3 (others: 0xFF)
    if slot < 4 then out[#out + 1] = r32((m + 8) & 0xFFFFFFFF) end
  end
  return out
end
local function XS_POPUP(hash) if hash then XS_DS1(0x6AC, hash, 5000) end end
function XS_OBJ_ACHIEVE(i)
  local r = XS_OR[i + 1]
  if not r or XS_OBJ_ENDED or r.state == 2 then return end
  r.state = 2
  game.log(string.format("objectives: record %d achieved (type %d, result %d)%s", i, r.type, r.result,
    r.why and (" - " .. r.why) or ""))
  XS_POPUP(r.achieved)
  if r.disp then XS_DS1(0x4C0, r.disp) end
  XS_SEND(0x4F, i)
  for k, c in ipairs(XS_OR) do                   -- composites waiting on this one
    if c.type == 3 and c.state == 1 and #c.subs > 0 then
      local all = true
      for _, sub in ipairs(c.subs) do if not XS_OR[sub + 1] or XS_OR[sub + 1].state ~= 2 then all = false end end
      if all then XS_OBJ_ACHIEVE(k - 1) end
    end
  end
  if r.result == 0 then XS_OBJ_ENDED = true; XS_SEND(0x11)
  elseif r.result == 1 then XS_OBJ_ENDED = true; XS_OBJ_FAIL_AT = XS_DS1(0x094) + r.delay
  elseif r.result == 2 then XS_OBJ_ACTIVATE(r.other)
  elseif r.result == 3 then
    local o = XS_OR[r.other + 1]
    if o then o.state = 0; if o.disp then XS_DS1(0x4C0, o.disp) end end
  end
end
XS_OBJ_ACTIVATE = function(i)
  local r = XS_OR[i + 1]
  if not r or r.state ~= 0 then return end
  r.state = 1
  XS_POPUP(r.activated)
  if r.disp then XS_DS1(0x4B8, r.disp) end
end
local function XS_OBJ_MET(r)                     -- the record's condition (scriptinfo.TYPES)
  local t = r.type
  if t == 0 or t == 1 then
    if #r.hlos == 0 then return false end
    local n = 0
    for _, id in ipairs(r.hlos) do if XS_GONE(id) then n = n + 1 end end
    return (t == 0 and n == #r.hlos) or (t == 1 and n > 0)
  elseif t == 2 then
    for _, id in ipairs(r.hlos) do
      local hp = XS_HP(id)
      if hp and hp <= 0 then r.why = string.format("object %d at zero life", id); return true end
    end
    return false
  elseif t == 6 then                             -- one player soldier dead for good, or all of them down
    local ps, down = XS_PLAYERS(), 0
    if #ps == 0 then return false end
    for _, id in ipairs(ps) do
      if XS_DS1(0x100, id) ~= 0 then r.why = string.format("squad soldier %d dead", id); return true end
      local hp = XS_HP(id); if hp and hp <= 0 then down = down + 1 end
    end
    if down == #ps then r.why = string.format("all %d squad soldiers down", down); return true end
    return false
  elseif t == 7 or t == 8 then                   -- all alive, and all / any in the region
    if #r.hlos == 0 or #r.regions == 0 then return false end
    local inside = 0
    for _, id in ipairs(r.hlos) do
      local hp = XS_HP(id); if not hp or hp <= 0 then return false end
      if XS_IN(id, r.regions[1]) then inside = inside + 1 end
    end
    return (t == 7 and inside == #r.hlos) or (t == 8 and inside > 0)
  elseif t == 9 or t == 10 then                  -- any alive player in any of the regions / all alive players in it
    local alive, inside = 0, 0
    for _, id in ipairs(XS_PLAYERS()) do
      local hp = XS_HP(id)
      if hp and hp > 0 then
        alive = alive + 1
        local hit = false
        for k, rg in ipairs(r.regions) do if (t == 9 or k == 1) and XS_IN(id, rg) then hit = true end end
        if hit then inside = inside + 1 end
      end
    end
    return (t == 9 and inside > 0) or (t == 10 and alive > 0 and inside == alive)
  end
  return false                                   -- 3 composite (on achieve), 11 script, 12 waypoint: the script
end
function XS_OBJ_FRAME()
  if XS_OBJ_FAIL_AT and XS_DS1(0x094) >= XS_OBJ_FAIL_AT then XS_OBJ_FAIL_AT = nil; XS_SEND(0x12) end
  for k, r in ipairs(XS_OR) do
    if r.state == 1 and XS_OBJ_MET(r) then XS_OBJ_ACHIEVE(k - 1) end
  end
end
function XS_OBJ_MARK(r, pos)               -- a map marker at a position (x, y, z)
  local p = XS_SCRATCH(16)
  wf(p, pos[1]); wf((p + 4) & 0xFFFFFFFF, pos[2]); wf((p + 8) & 0xFFFFFFFF, pos[3])
  XS_DS1(0x4D4, r.disp, p)
end
function XS_WP_POS(wp)
  local w = XS_SCRATCH(12); XS_DS1(0x0A0, w, wp)
  return {rf(w), rf((w + 4) & 0xFFFFFFFF), rf((w + 8) & 0xFFFFFFFF)}
end
function XS_OBJ_INIT()                           -- the shown objectives, in record order (as the source's loader)
  for _, r in ipairs(XS_OR) do
    if r.title then
      local d = XS_DS1(0x4B0, r.title) & 0xFF
      if d ~= 0xFF then
        r.disp = d
        if r.desc then XS_DS1(0x4B4, d, r.desc) end
        if r.state == 1 then XS_DS1(0x4B8, d) elseif r.state == 2 then XS_DS1(0x4C0, d) end
        if r.waypoint >= 0 then XS_OBJ_MARK(r, XS_WP_POS(r.waypoint)) end
        for _, pos in ipairs(r.positions) do XS_OBJ_MARK(r, pos) end
        if r.mark then for _, id in ipairs(r.hlos) do if XS_OBJ(id) ~= 0 then XS_DS1(0x4C8, d, id) end end end
      end
    end
  end
  game.log(string.format("objectives: %d record(s) from the level's .SI", #XS_OR))
end
function XS_REC(v) v = v & 0xFF; if v >= 0x80 then v = v - 0x100 end; return XS_OR[v + 1], v end
local XS_SPAWN_WARNED = {}
function XS_SPAWN_OK(i)                          -- a spawn point Desert Storm's table has (count [0x618344])
  if i < r32(0x618344) then return true end
  if not XS_SPAWN_WARNED[i] then
    XS_SPAWN_WARNED[i] = true
    game.log(string.format("spawn point %d: not in Desert Storm's spawn table (%d) - call skipped", i, r32(0x618344)))
  end
  return false
end
"""


# Desert Storm II's spawn points + damage regions from the level's .SI, on Desert Storm's spawn system (the same
# spawner: budget, 80 m, nearest point, message 0x10, waves; CDS II.exe 0x44E7A0 vs DesertStorm.exe 0x482B80). What
# Desert Storm hasn't got is done here: the point's own units respawned on 0x10 (Desert Storm's scripts do that in
# their 0x10 handler), the dormant state + enable / disable regions, 'enable when clear', messages 0x4B (unit
# respawned) / 0x4C (enabled) / 0x4D (disabled for good) / 0x4E (no waves left) to the source's script. DS2 states:
# 0 dormant, 1 off, 2 on, 3 permanently off. Areas aren't carried over (points get zone -1).
SPAWNS_LUA = r"""
XS_SP = @SPAWNS@
local XS_SPS = {}
function XS_SP_INIT()
  local n = #XS_SP.points
  if n == 0 then return end
  XS_DS1(0x358, n, 0)
  for i, p in ipairs(XS_SP.points) do
    XS_DS1(0x370, i - 1, XS_BITS(p.pos[1]), XS_BITS(p.pos[2]), XS_BITS(p.pos[3]), #p.units, 0xFFFFFFFF,
      p.waves & 0xFFFFFFFF)
    if p.start ~= 0 then XS_SPS[i] = 2 else XS_DS1(0x37C, i - 1, 0); XS_SPS[i] = 0 end
  end
  game.log(string.format("spawn points: %d from the level's .SI", n))
end
function XS_SP_SET(i, state)                     -- (0-based point, DS2 state) on Desert Storm's table too
  local p = XS_SP.points[i + 1]
  if not p then return end
  if state == 2 then XS_DS1(0x378, i, 0) elseif state == 3 then XS_DS1(0x37C, i, 1) else XS_DS1(0x37C, i, 0) end
  XS_SPS[i + 1] = state
end
function XS_SP_SPAWNED(i)                        -- Desert Storm's spawner chose point i: its units come back
  local p = XS_SP.points[i + 1]
  if not p then return end
  for _, id in ipairs(p.units) do
    if XS_OBJ(id) ~= 0 then XS_DS1(0x0DC, id); XS_SEND(0x4B, id, i) end
  end
  XS_SPS[i + 1] = 1                              -- (the spawner switched it off)
  local rec = XS_DS1(0x374, i)
  if rec ~= 0 and r32((rec + 0x14) & 0xFFFFFFFF) == 0 then XS_SEND(0x4E, i) end
end
function XS_SPS_ON(i) if XS_SP.points[i + 1] then XS_SPS[i + 1] = 2 end end
function XS_SPS_GET(i) return XS_SPS[i + 1] or 0 end
function XS_SP_CLEAR(i)                          -- none of the point's units alive and in play
  local p = XS_SP.points[i + 1]
  if not p then return false end
  for _, id in ipairs(p.units) do
    local o = XS_OBJ(id)
    if o ~= 0 and r32((o + 0xC) & 0xFFFFFFFF) & 1 ~= 0 and XS_DS1(0x104, id) == 0 then return false end
  end
  return true
end
local XS_SP_TICK = 0
function XS_SP_FRAME()
  XS_SP_TICK = XS_SP_TICK + 1
  if XS_SP_TICK % 4 == 0 then                    -- region triggers (a few times a second is plenty)
    local players = XS_PLAYERS()
    local function inside(list)
      for _, rg in ipairs(list) do
        for _, id in ipairs(players) do if XS_IN(id, rg) then return true end end
      end
      return false
    end
    for i, p in ipairs(XS_SP.points) do
      local st = XS_SPS[i]
      if st == 0 and #p.enable > 0 and inside(p.enable) then XS_SP_SET(i - 1, 2); XS_SEND(0x4C, i - 1) end
      st = XS_SPS[i]
      if st ~= 3 and #p.disable > 0 and inside(p.disable) then XS_SP_SET(i - 1, 3); XS_SEND(0x4D, i - 1) end
    end
  end
  for _, d in ipairs(XS_SP.damage) do            -- damage regions: every team inside, damage per second
    if d.enabled ~= 0 then
      for team = 0, 3 do XS_DS1(0x2BC, d.region, XS_BITS(d.dps > 0 and d.dps or 100000.0), team) end
    end
  end
end
"""


def float_slots(game):
    """Source slot offsets whose result is a float on the x87 stack (api_slots.json "returns" says float)."""
    with open(SLOT_FILES[game], encoding="utf-8") as f:
        data = json.load(f)
    return {int(k, 16) for k, v in data.items() if str(v.get("returns") or "").lower().startswith("float")}


def slot_map(game):
    """{source slot offset: (name, Desert Storm slot offset or None)} from the game's api_slots.json."""
    with open(SLOT_FILES[game], encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for k, v in data.items():
        ds1 = v.get("ds1_slot")
        out[int(k, 16)] = (v.get("name") or "slot_%03X" % int(k, 16), int(ds1, 16) if ds1 else None)
    return out


END_MISSION = {"ds2": 0x58C}             # the source's end_mission(result) slot
LOAD_MAP_IMAGE = {"ds2": 0x074}          # the source's load_map_image(file) slot


def _lua_str(s):
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def adapt_stage1(text, game="ds2", deactivate=(), next_level=None, map_image=None, objectives=None, spawns=None):
    """A stage-1 translation of `game`'s mission DLL (mission2lua output) made to run on Desert Storm's script API.
    `deactivate`: script ids the level port put in play that the source placed out of play (levelport's
    ``game.deactivate`` lines) - switched off right after LevelSetup, before the first AI turn. `next_level`: the
    level a successful end goes on to (a converted campaign's next mission; None = the game's own rule). `map_image`:
    the map sheet the level port made (its GW<LEVEL>.img) - the DLL asks for the source game's own name
    ("GWMission5.img"), which is Desert Storm's own map of another mission or no file at all. `objectives`: the
    level's objective records (scriptinfo.parse_objectives of its .SI) - Desert Storm II's engine shows and checks
    them itself; here OBJECTIVES_LUA does (the shown ones through Desert Storm's objective slots). `spawns`: its
    spawn points and damage regions (scriptinfo.parse_spawns) - rebuilt on Desert Storm's spawn system (SPAWNS_LUA)."""
    if game not in SLOT_FILES:
        raise ValueError("no script API mapping for %s" % game)
    if _MARK not in text or "ENTER(LEVELSETUP, 0x5ED238)" not in text:
        raise ValueError("not a mission2lua stage-1 translation")
    slots = slot_map(game)
    lua = LUA_SLOTS.get(game, {})
    lines = [
        "-- ======================== %s script API on Desert Storm (crossmission.py) ========================" %
        game.upper(),
        "local XS_NAMES = {",
    ]
    lines += ["  [0x%03X] = %r," % (k, slots[k][0]) for k in sorted(slots)]
    lines += ["}", "local XS_MAP = {"]
    lines += ["  [0x%03X] = 0x%03X," % (k, slots[k][1]) for k in sorted(slots) if slots[k][1] is not None]
    lines += ["}", "local XS_OBJS = {"]
    lines += ["  [0x%03X] = {%s}," % (k, ", ".join(map(str, v))) for k, v in sorted(object_args(game).items())]
    lines += ["}", "local XS_DS1, XS_STR, XS_OBJ                 -- (defined below; the Lua slots use them)",
              "local function XS_CSTR(p)                       -- a C string from the game's memory",
              "  local t = {}",
              "  for k = 0, 255 do local c = r8((p + k) & 0xFFFFFFFF); if c == 0 then break end; t[#t + 1] = string.char(c) end",
              "  return table.concat(t)",
              "end",
              "local XS_SCRIPTED = {}                         -- SCRIPTED_ANIM_n -> the animation load_animation gave",
              "local XS_FLOATS = {%s}" % ", ".join("[0x%03X] = true" % k for k in sorted(float_slots(game))),
              "local function XS_FLOAT(v) return {f = v} end   -- a Lua slot's float result (x87 ST0)",
              "local function XS_BITS(v) return string.unpack('<I4', string.pack('<f', v)) end",
              "local function XS_TOF(b) return string.unpack('<f', string.pack('<I4', b & 0xFFFFFFFF)) end",
              "local XS_SCRATCHES = {}",
              "local function XS_SCRATCH(n)                  -- game memory for out-arguments (kept per size)",
              "  if not XS_SCRATCHES[n] then",
              "    ESP = (ESP - 8) & 0xFFFFFFFF; w32(ESP, n); w32((ESP + 4) & 0xFFFFFFFF, 0)",
              "    XS_SCRATCHES[n] = X.call(0x4B8450, ESP, 0, 0)",
              "    ESP = (ESP + 8) & 0xFFFFFFFF",
              "  end",
              "  return XS_SCRATCHES[n]",
              "end",
              "local function XS_NAT(addr, ecx, ...)          -- a Desert Storm function without a slot (cdecl / thiscall)",
              "  local args = {...}",
              "  ESP = (ESP - 4 * #args) & 0xFFFFFFFF",
              "  for k, v in ipairs(args) do w32((ESP + 4 * (k - 1)) & 0xFFFFFFFF, v) end",
              "  local eax = X.call(addr, ESP, ecx or 0, 0)",
              "  ESP = (ESP + 4 * #args) & 0xFFFFFFFF",
              "  return eax",
              "end",
              "local function XS_TEAM(team)                   -- the objects of a team (0 = the player's): its active list",
              "  local mgr, out = r32(0x63C9A0), {}",
              "  if mgr == 0 then return out end",
              "  if team == 0 then team = r32(0x5F9B94) end",
              "  local o = XS_NAT(0x4C9720, mgr, team, 1)",
              "  while o ~= 0 and #out < 256 do out[#out + 1] = o; o = r32((o + 0x14) & 0xFFFFFFFF) end",
              "  return out",
              "end",
              "local XS_LUA = {}"]
    if next_level and game in END_MISSION:
        # the source's mission DLLs never name the next level (the source exe chains them): a converted campaign
        # bakes its successor in - end_mission(2) sets it first (Desert Storm's set_next_level +0x490)
        lua = dict(lua)
        lua[END_MISSION[game]] = ("if ARG(1) == 2 then XS_DS1(0x490, XS_STR(%s)) end\n"
                                  "  return XS_DS1(0x48C, ARG(1))" % _lua_str(next_level))
    if map_image and game in LOAD_MAP_IMAGE:
        lua = dict(lua)
        lua[LOAD_MAP_IMAGE[game]] = "return XS_DS1(0x04C, XS_STR(%s))" % _lua_str(map_image)
    for k in sorted(lua):
        lines += ["XS_LUA[0x%03X] = function(ARG)" % k, "  " + lua[k], "end"]
    lines += [
        "local XS_SEEN = {}",
        "local function XS_FIRST(off, what)",
        "  if not XS_SEEN[off] then",
        "    XS_SEEN[off] = true",
        '    game.log(string.format("%s api +0x%%03X %%s -> %%s", off, XS_NAMES[off] or "?", what))' % game,
        "  end",
        "end",
        "XS_OBJ = function(id)                           -- Desert Storm's object of a script id (FUN_004CA660), or 0",
        "  local mgr = r32(0x63C9A0)",
        "  if mgr == 0 then return 0 end",
        "  ESP = (ESP - 4) & 0xFFFFFFFF; w32(ESP, id)",
        "  local obj = X.call(0x4CA660, ESP, mgr, 0)",
        "  ESP = (ESP + 4) & 0xFFFFFFFF",
        "  return obj",
        "end",
        "local XS_MISSING = {}",
        "local function XS_TABLE()",
        "  local n = 0x%X" % TABLE_SIZE[game],
        "  ESP = (ESP - 8) & 0xFFFFFFFF; w32(ESP, n); w32((ESP + 4) & 0xFFFFFFFF, 0)",
        "  local mem = X.call(0x4B8450, ESP, 0, 0)        -- the game's allocator, cdecl(size, tag)",
        "  ESP = (ESP + 8) & 0xFFFFFFFF",
        "  for off = 0, n - 4, 4 do",
        "    local addr = 0x%X + off" % STUB_BASE,
        "    local impl, ds1 = XS_LUA[off], XS_MAP[off]",
        "    if impl then",
        "      FN[addr] = function()",
        '        XS_FIRST(off, "Lua")',
        "        local r = impl(function(k) return r32((ESP + 4 * k) & 0xFFFFFFFF) end)",
        "        if type(r) == 'table' then fpush(r.f)",
        "        elseif XS_FLOATS[off] then fpush(XS_TOF(r or 0))     -- (a float slot answered with bits)",
        "        else EAX = (r or 0) & 0xFFFFFFFF end",
        "        ESP = (ESP + 4) & 0xFFFFFFFF",
        "      end",
        "    elseif ds1 then",
        "      local native = r32(0x%X + ds1)" % DS1_API,
        "      FN[addr] = function()",
        '        XS_FIRST(off, string.format("DS1 +0x%03X", ds1))',
        "        ESP = (ESP + 4) & 0xFFFFFFFF",
        "        for _, k in ipairs(XS_OBJS[off] or {}) do   -- Desert Storm's functions don't check the object",
        "          local id = r32((ESP + 4 * (k - 1)) & 0xFFFFFFFF)",
        "          if id <= 65534 and XS_OBJ(id) == 0 then",
        "            local key = off * 0x10000 + id",
        "            if not XS_MISSING[key] then",
        "              XS_MISSING[key] = true",
        '              game.log(string.format("%s api +0x%%03X %%s: object %%d is not in the level - call skipped", '
        'off, XS_NAMES[off] or "?", id))' % game,
        "            end",
        "            EAX = 0",
        "            return",
        "          end",
        "        end",
        "        NCALL(native, ESP)                        -- cdecl: the caller pops the arguments",
        "      end",
        "    else",
        "      FN[addr] = function()",
        '        XS_FIRST(off, "not mapped - returns 0")',
        "        EAX = 0",
        "        if XS_FLOATS[off] then fpush(0.0) end        -- a float slot: 0.0 on the x87 stack (the caller pops it)",
        "        ESP = (ESP + 4) & 0xFFFFFFFF",
        "      end",
        "    end",
        "    w32(mem + off, addr)",
        "  end",
        "  return mem",
        "end",
        "XS_DS1 = function(off, ...)                     -- a Desert Storm script API slot, called natively",
        "  local args = {...}",
        "  ESP = (ESP - 4 * #args) & 0xFFFFFFFF",
        "  for k, v in ipairs(args) do w32((ESP + 4 * (k - 1)) & 0xFFFFFFFF, v) end",
        "  NCALL(r32(0x%X + off), ESP)" % DS1_API,
        "  ESP = (ESP + 4 * #args) & 0xFFFFFFFF",
        "  return EAX",
        "end",
        "local XS_INACTIVE = {%s}" % ", ".join(str(i) for i in deactivate),
        "XS_STR = function(s)                            -- a C string in the game's memory",
        "  ESP = (ESP - 8) & 0xFFFFFFFF; w32(ESP, #s + 1); w32((ESP + 4) & 0xFFFFFFFF, 0)",
        "  local mem = X.call(0x4B8450, ESP, 0, 0)",
        "  ESP = (ESP + 8) & 0xFFFFFFFF",
        "  for k = 1, #s do w8(mem + k - 1, s:byte(k)) end",
        "  w8(mem + #s, 0)",
        "  return mem",
        "end",
        "local XS_MSG = {%s}" % ", ".join("[0x%02X] = 0x%02X" % kv for kv in sorted(MESSAGES.get(game, {}).items())
                                         if kv[1] is not None),
        "local XS_MSG_SEEN = {}",
        "local XS_MSG_ORIG",
        "local function XS_SEND(id, ...)                 -- a message in the source's own ids to its script",
        "  if not XS_MSG_ORIG then return end",
        "  local args, p = {...}, XS_SCRATCH(64)",
        "  for k = 1, 8 do w32((p + 4 * (k - 1)) & 0xFFFFFFFF, args[k] or 0) end",
        "  ESP = (ESP - 12) & 0xFFFFFFFF",
        "  w32(ESP, 0); w32((ESP + 4) & 0xFFFFFFFF, id); w32((ESP + 8) & 0xFFFFFFFF, p)",
        "  XS_MSG_ORIG()                                 -- (pops its return address; the arguments are ours)",
        "  ESP = (ESP + 8) & 0xFFFFFFFF",
        "end",
        "local function XS_WRAP(object)                  -- the script's message callback gets the source's ids",
        "  local mva = r32(object + 8)",
        "  local key = F[(mva - D) & 0xFFFFFFFF] and ((mva - D) & 0xFFFFFFFF) or mva",
        "  local orig = F[key]",
        "  if not orig then game.log('crossmission: no message callback found - ids not translated'); return end",
        "  XS_MSG_ORIG = orig",
        "  F[key] = function()",
        "    local id = r32((ESP + 4) & 0xFFFFFFFF)",
        "    local to = XS_MSG[id]",
        "    if not XS_MSG_SEEN[id] then",
        "      XS_MSG_SEEN[id] = true",
        '      game.log(to and string.format("%s message 0x%%02X -> 0x%%02X", id, to) or' % game,
        '        string.format("%s message 0x%%02X: no counterpart known - not delivered", id))' % game,
        "    end",
        "    if id == 0x10 then XS_SP_SPAWNED(r32(r32((ESP + 8) & 0xFFFFFFFF))) end",
        "    if to == nil then EAX = 0; ESP = (ESP + 4) & 0xFFFFFFFF; return end",
        "    w32((ESP + 4) & 0xFFFFFFFF, to)",
        "    return orig()",
        "  end",
        "end",
        OBJECTIVES_LUA.replace("@RECORDS@", scriptinfo.objectives_lua(objectives or [])),
        SPAWNS_LUA.replace("@SPAWNS@", scriptinfo.spawns_lua(spawns)),
        "local function XS_WRAP_UPDATE(object)          -- the objectives are checked every frame the script runs",
        "  local uva = r32(object + 4)",
        "  local key = F[(uva - D) & 0xFFFFFFFF] and ((uva - D) & 0xFFFFFFFF) or uva",
        "  local orig = F[key]",
        "  if not orig or (#XS_OR == 0 and #XS_SP.points == 0 and #XS_SP.damage == 0) then return end",
        "  F[key] = function() XS_OBJ_FRAME(); XS_SP_FRAME(); return orig() end",
        "end",
        "-- saves: the records' / spawn points' states after the DLL's own block ('XSO1', u16 records, u16 points, a",
        "-- byte each); Desert Storm restores the shown objectives' flags itself (by list position)",
        "local function XS_WRAP_SAVES(object)",
        "  local function cb(off)",
        "    local va = r32(object + off)",
        "    local key = F[(va - D) & 0xFFFFFFFF] and ((va - D) & 0xFFFFFFFF) or va",
        "    return key, F[key]",
        "  end",
        "  local extra = 8 + #XS_OR + #XS_SP.points",
        "  if extra == 8 then return end",
        "  local ks, save = cb(0xC); local kl, load = cb(0x10); local kz, size = cb(0x14)",
        "  if not (save and load and size) then game.log('crossmission: save callbacks not found'); return end",
        "  F[kz] = function() size(); EAX = (EAX + extra) & 0xFFFFFFFF end",
        "  F[ks] = function()",
        "    save()",
        "    local p = EAX",
        "    w32(p, 0x314F5358); w16((p + 4) & 0xFFFFFFFF, #XS_OR); w16((p + 6) & 0xFFFFFFFF, #XS_SP.points)",
        "    for i, r in ipairs(XS_OR) do w8((p + 7 + i) & 0xFFFFFFFF, r.state) end",
        "    for i = 1, #XS_SP.points do w8((p + 7 + #XS_OR + i) & 0xFFFFFFFF, XS_SPS_GET(i - 1)) end",
        "    EAX = (p + extra) & 0xFFFFFFFF",
        "    game.log(string.format('crossmission: saved %d objective / %d spawn point state(s)', #XS_OR, #XS_SP.points))",
        "  end",
        "  F[kl] = function()",
        "    load()",
        "    local p = EAX",
        "    if r32(p) ~= 0x314F5358 or r16((p + 4) & 0xFFFFFFFF) ~= #XS_OR or",
        "       r16((p + 6) & 0xFFFFFFFF) ~= #XS_SP.points then",
        "      game.log('crossmission: save has no objective / spawn states (an older save) - kept as at the start')",
        "      return",
        "    end",
        "    for i, r in ipairs(XS_OR) do r.state = r8((p + 7 + i) & 0xFFFFFFFF) end",
        "    for i = 1, #XS_SP.points do XS_SP_SET(i - 1, r8((p + 7 + #XS_OR + i) & 0xFFFFFFFF)) end",
        "    EAX = (p + extra) & 0xFFFFFFFF",
        "    game.log(string.format('crossmission: restored %d objective / %d spawn point state(s)', #XS_OR, #XS_SP.points))",
        "  end",
        "end",
        "local function XS_ANIMS()                       -- Desert Storm states the source's scripts lead to",
        "  for _, a in ipairs({%s}) do" % ", ".join("{%r, %r}" % a for a in EXTRA_ANIMS.get(game, [])).replace(
            "'", '"'),
        "    if not X.anim_loaded(XS_STR(a[1])) then XS_DS1(0x04, XS_STR(a[1]), XS_STR(a[2])) end",
        "  end",
        "end",
        "",
    ]
    text = text.replace(_MARK, "\n".join(lines) + _MARK, 1)
    # Desert Storm's mission DLLs register every animation state themselves (the translation says so in its header);
    # another game's don't register Desert Storm's states - the script host's standard set is needed (else the
    # soldiers' animation channels stay empty: crash 0x42DF97 on their first AI turn)
    text = text.replace("--@standard_anims off\n", "", 1)
    text = text.replace("local object = ENTER(LEVELSETUP, 0x5ED238)",
                        "XS_ANIMS()\n  local object = ENTER(LEVELSETUP, XS_TABLE())\n"
                        "  XS_WRAP(object)\n"
                        "  XS_WRAP_UPDATE(object)\n"
                        "  XS_WRAP_SAVES(object)\n"
                        "  XS_OBJ_INIT()\n"
                        "  XS_SP_INIT()\n"
                        "  for _, id in ipairs(XS_INACTIVE) do XS_DS1(0xC8, id) end",
                        1)
    if "XS_ANIMS()\n" not in text:
        raise ValueError("the translation's LevelSetup call wasn't found")
    return text


# ------------------------------------------------------------------------------------------- converted campaigns --

CONVERTER_VERSION = 1
# the source game's campaign: (campaign name, its missions in order, the Desert Storm level they're built on)
CAMPAIGNS = {"ds2": ("DESERT STORM 2", ["MISSION%02d" % n for n in range(1, 11)], "Mission3")}
# tables several levels add rows to: one file in the campaign, rows merged
_MERGED_TABLES = {"DOORS.TXT", "FEATS.TXT", "STRUCTS.TXT", "FEATSTAT.TXT", "TARGETS.TXT", "INVEN.TXT"}


def convert_campaign(src, dst, out_dir, levels=None, name=None, base=None, log=print):
    """A source game's campaign converted for Desert Storm into `out_dir` (DesertStormFix reads <game>/PSE/Ports/<id>):
    every level ported (levelport.port_level, under the source's level name), its mission DLL translated
    (mission2lua stage 1) and adapted (adapt_stage1, each with its successor baked in), the files the DLL asks for
    (mission_files), plus campaign.txt (the campaign's name and missions in order) and port.txt (what it was made
    from: game, its exe's SHA-256, converter version - the online check compares the folder's content, every player
    converts from his own copy; nothing of it is synced). Returns the report lines."""
    import hashlib
    import re
    from . import games, levelport, mission2lua
    cname, clevels, cbase = CAMPAIGNS[src.game.id]
    levels = [lv.upper() for lv in (levels or clevels)]
    name, base = name or cname, base or cbase
    os.makedirs(out_dir, exist_ok=True)
    files, owner, report = {}, {}, []

    def put(fname, data, level):
        key = fname.upper()
        if key in _MERGED_TABLES and key in files:
            have = files[key].decode("latin-1").splitlines()
            new = [ln for ln in data.decode("latin-1").splitlines() if ln.strip() and ln not in have]
            files[key] = ("\r\n".join(have + new) + "\r\n").encode("latin-1")
            return
        if key in files and files[key] != data:
            report.append("  [note] %s differs between %s and %s - %s's kept" % (fname, owner[key], level,
                                                                              owner[key]))
            return
        files[key], owner[key] = data, level

    real = {}
    for i, level in enumerate(levels):
        log("convert %s" % level)
        dll = next((os.path.join(src.folder, f) for f in os.listdir(src.folder) if f.upper() == level + ".DLL"), None)
        if dll is None:
            report.append("[fail] %s: %s.dll not found" % (level, level))
            continue
        lfiles, notes, lname = levelport.port_level(src, dst, level, base=base, name=level, log=lambda s: None)
        with open(dll, "rb") as f:
            dbytes = f.read()
        mfiles, mnotes = mission_files(src, dst, level, dbytes)
        script = level.lower() + ".lua"
        inactive = [int(x) for x in re.findall(r"game\.deactivate\((\d+)\)", lfiles[script].decode("latin-1"))]
        nxt = levels[i + 1].lower() + ".lua" if i + 1 < len(levels) else "FrontEnd.dll"
        text, _st = mission2lua.translate(dll)
        # the level port's map sheet (the source's map), else the base level's (as the port's own script does)
        mimg = "GW%s.img" % level if "GW%s.IMG" % level.upper() in {k.upper() for k in lfiles} else "GW%s.img" % base
        # its objectives: the records of the level's .SI (Desert Storm II's engine runs them; here the adapter does)
        si = src.get(level + ".SI")
        try:
            objs = scriptinfo.parse_objectives(si.decode("latin-1")) if si else []
        except scriptinfo.ScriptInfoError as e:
            objs = []
            notes.append("objectives: %s.SI unreadable (%s) - none" % (level, e))
        if objs:
            notes.append("objectives: %d record(s) from %s.SI, %d shown" % (
                len(objs), level, sum(1 for o in objs if o["title"])))
        try:
            spawns = scriptinfo.parse_spawns(si.decode("latin-1")) if si else None
        except scriptinfo.ScriptInfoError as e:
            spawns = None
            notes.append("spawn points: %s.SI unreadable (%s) - none" % (level, e))
        if spawns and spawns["spawn_points"]:
            notes.append("spawn points: %d from %s.SI (areas not carried over: %d)%s" % (
                len(spawns["spawn_points"]), level, len(spawns["areas"]),
                ", %d damage region(s)" % len(spawns["damage_regions"]) if spawns["damage_regions"] else ""))
        text = adapt_stage1(text, src.game.id, inactive, next_level=nxt, map_image=mimg, objectives=objs,
                            spawns=spawns)
        text = "--@archive %s\n" % base.lower() + text.split("\n", 1)[1]   # the base level's archive
        lfiles[script] = text.encode("latin-1")
        for fn, data in list(lfiles.items()) + list(mfiles.items()):
            put(fn, data, level)
        real[level] = script
        report.append("%s -> %s (%d files)" % (level, script, len(lfiles) + len(mfiles)))
        report += ["    " + n for n in notes + mnotes]
    # the source's shared texts Desert Storm hasn't got (objective pop-ups "NEW_OBJECTIVE" ...); its own stay
    try:
        from . import porting
        fname, ctext, cnotes = porting.texts_file(src, dst, "catalog")
        files[fname.upper()] = ctext.replace("\n", "\r\n").encode("latin-1")
        report += ["    " + n for n in cnotes]
    except Exception as e:                                  # noqa: BLE001 - texts are optional
        report.append("  [note] shared texts not ported: %s" % e)
    for fn, data in files.items():
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(data)
    exe = games.game_file(src.folder, src.game.exes[0]) if src.game.exes else None
    sha = hashlib.sha256(open(exe, "rb").read()).hexdigest().upper() if exe else "?"
    with open(os.path.join(out_dir, "campaign.txt"), "w", encoding="latin-1", newline="\r\n") as f:
        f.write("# %s's campaign converted for Desert Storm by the ModKit (conflict_ds_tools/crossmission.py)\n"
                % src.game.short)
        f.write("name = %s\n" % name)
        for level in levels:
            if level in real:
                f.write("mission = %s\n" % real[level])
    with open(os.path.join(out_dir, "port.txt"), "w", encoding="latin-1", newline="\r\n") as f:
        f.write("# Converted from your own copy of %s - never shared: every player converts it themselves.\n"
                % src.game.short)
        f.write("game = %s\nsource exe sha256 = %s\nconverter = %d\nlevels = %s\n" % (
            src.game.id, sha, CONVERTER_VERSION, " ".join(real.get(lv, "-") for lv in levels)))
    report.insert(0, "%s: %d of %d level(s) converted into %s" % (name, len(real), len(levels), out_dir))
    return report
