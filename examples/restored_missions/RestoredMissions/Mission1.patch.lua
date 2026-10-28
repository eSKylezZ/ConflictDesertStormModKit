--@patches Mission1.dll
--@readable 1
-- RestoredMissions: Mission 1 (RESCUE) - cut content put back. A mission patch (DesertStormFix 3.3+): it runs as part
-- of the readable Lua that DesertStormFix makes from YOUR Mission1.dll - nothing of the game is in this file.
-- The --[[@edit ... --@to ... ]] blocks change those lines of the readable mission (each must match exactly
-- one place); the Lua below them adds the restored parts. Reference: docs/sdk/reference/mission-patches.md.

--[[@edit
        api.give_item(obj.HERO_FOLEY, obj.FOLEY_AMMO)
--@to
        api.give_item(obj.HERO_FOLEY, obj.FOLEY_AMMO)
        tr_foley_takes_medikit()   -- RestoredMissions (PS2 demo)
]]

--[[@edit
        api.set_ignore_distractions(obj.SBridge_guard_2, 0)
        api.set_path(obj.SBridge_guard_2, wp.SMALL_BRIDGE_RUN_1)
        api.set_ai_state(obj.SBridge_guard_2, 0)
        api.set_stance_stand(obj.SBridge_guard_2)
--@to
        tr_bridge_guard_run()   -- RestoredMissions: was the run along SMALL_BRIDGE_RUN_1
]]

-- ============================ RestoredMissions: shared helpers ============================
-- (The same in each RestoredMissions patch. It runs in the mission's own environment: api.*, obj.*, mem.*,
-- scratch(), f32() come from the readable mission's runtime; SCENE, TEXT, MOVIE and X from DesertStormFix.)
TR = TR or {}

function tr_now() return api.get_time_ms() end

-- an object's position (render instance), cm
function tr_pos(id)
  local p = api.get_object_position(scratch(12), id)
  return mem.f32(p), mem.f32(p + 4), mem.f32(p + 8)
end

function tr_wp_pos(wp)
  local p = api.get_waypoint_position(scratch(12), wp)
  return mem.f32(p), mem.f32(p + 4), mem.f32(p + 8)
end

-- horizontal distance (cm) from an object to a waypoint
function tr_dist_to_wp(id, wp)
  local x, _, z = tr_pos(id)
  local wx, _, wz = tr_wp_pos(wp)
  return math.sqrt((x - wx) ^ 2 + (z - wz) ^ 2)
end

-- the game object of a scene id (object table [[0x63C9A0]+0x30])
function tr_object(id)
  local tab = mem.i32(mem.i32(0x63C9A0) + 0x30)
  return tab ~= 0 and mem.i32(tab + 4 * id) or 0
end

-- the soldier holds a Medi-Kit (held item soldier +0x26B0 -> definition +0x24 -> category 8)
function tr_holding_medikit(id)
  local s = tr_object(id)
  if s == 0 then return false end
  local held = mem.i32(s + 0x26B0)
  if held == 0 then return false end
  local def = mem.i32(held + 0x24)
  return def ~= 0 and mem.i32(def) == 8
end

-- send a soldier along a waypoint chain at walking pace
function tr_walk(id, wp)
  api.set_stance_stand_walk(id)
  api.set_path(id, wp)
  api.set_ai_state(id, 0)
end

-- A timeline: {{ms, function}, ...} in order. tr_timeline_step(tl) runs every entry whose time has come and
-- returns true once the last one ran.
function tr_timeline(entries) return {entries = entries, t0 = tr_now(), next = 1} end
function tr_timeline_step(tl)
  local t = tr_now() - tl.t0
  while tl.next <= #tl.entries and tl.entries[tl.next][1] <= t do
    tl.entries[tl.next][2]()
    tl.next = tl.next + 1
  end
  return tl.next > #tl.entries
end

-- Ladders. The game's script API has a "mount ladder" slot (+0x568, object, skip the distance check) that the DLLs
-- never call: it mounts the soldier on the nearest ladder (bottom or top end) through the movement rules. Climbing
-- is the movement rule the player's input applies: MSR_LADDER_CLIMB_UP 0x3C (0x3D down), applied every frame while
-- the soldier is on the ladder; at the end the game dismounts him by itself. Rules go through the soldier's
-- vtable +0x70 (what stop_talking etc. use). Movement state soldier +0x2674: 0x38 / 0x39 mounting (bottom / top),
-- 0x3A on the ladder, 0x3B / 0x3C climbing up / down, 0x3E / 0x3F getting off (bottom / top).
function tr_rule(id, rule)
  local s = tr_object(id)
  if s == 0 then return 0 end
  local a = scratch(8)
  X.w32(a, rule)
  return X.call(X.r32(X.r32(s) + 0x70), a, s)
end
function tr_move_state(id)
  local s = tr_object(id)
  return s ~= 0 and mem.i32(s + 0x2674) or -1
end
-- climb(id, up): a climb object; tr_climb_step(c) each frame -> "climbing", "done" or "failed"
function tr_climb(id, up)
  return {id = id, rule = up == false and 0x3D or 0x3C, t = tr_now()}
end
function tr_climb_step(c)
  if c.result then return c.result end
  local st = tr_move_state(c.id)
  if not c.mounted then
    c.mounted = true
    if call(0x568, false, c.id, 1) == 0 then c.result = "failed"; return c.result end
    return "climbing"
  end
  if st >= 0x38 and st <= 0x3F then
    c.on = true
    if st == 0x3A or st == c.rule - 1 then tr_rule(c.id, c.rule) end   -- 0x3B up / 0x3C down while moving
  elseif c.on then
    c.result = "done"
  elseif tr_now() - c.t > 3000 then
    c.result = "failed"      -- never got onto the ladder
  end
  if not c.result and tr_now() - c.t > 60000 then c.result = "failed" end
  return c.result or "climbing"
end

-- ============================ RestoredMissions: Mission 1 (RESCUE) ============================
-- Restored from the PS2 demo (30 May 2002), whose Mission 1 script still had these; the retail scene keeps the data:
--   * Foley takes his Medi-Kit from the table along with the rifle and ammo (the demo's extra give_item; retail
--     Foley already carries a kit of his own, so the table's one just goes - retail left it lying there for good).
--   * The small-bridge guard raises the alarm: after waving the truck on he runs the demo's route
--     SBridge_guard_2_1 .. _7 -> RAISE_ALARM up to the base (still in the retail scene, unused - retail sends him
--     along the short SMALL_BRIDGE_RUN instead; an edit block above), and at the alarm post he shouts and raises the base
--     alarm (the game's own alarm, f_3130 here) - about 25 s after he sets off. Stop him before he gets there. Retail's leftover follow-up then runs again:
--     once the hunters reach FOLLOW_HUNTERS he joins them against Bradley (b_1809E, never set in retail).

local O = SCENE.objects

local dll_path_done = on_path_done
function on_path_done(unit)
  if unit == O.SBridge_guard_2 then   -- his only route now is the alarm run (also after a loaded save)
    api.play_sound_at_object(O.SBridge_guard_2, 3, 300.0)   -- his shout (his own speech bank)
    f_3130()                                                -- the base alarm (no-op when it's already raised)
    b_1809E = 1
    api.set_ignore_distractions(O.SBridge_guard_2, 0)
    api.clear_never_excluded(O.SBridge_guard_2)
    return
  end
  return dll_path_done(unit)
end

-- called by the edited wave step instead of retail's run along SMALL_BRIDGE_RUN_1. The wave step runs every frame
-- until the truck reaches its next waypoint (retail re-issued its path each of those frames), so the run starts once.
local bridge_run_started = false
function tr_bridge_guard_run()
  if bridge_run_started then return end
  bridge_run_started = true
  api.set_path(O.SBridge_guard_2, SCENE.waypoints.SBridge_guard_2_1)
  api.set_ai_state(O.SBridge_guard_2, 0)
  api.set_stance_stand(O.SBridge_guard_2)   -- running, as the demo
  api.set_ignore_distractions(O.SBridge_guard_2, 1)   -- he goes for the alarm, he doesn't stop to fight
  api.set_never_excluded(O.SBridge_guard_2)
end

-- Foley at the table (single player; in co-op player 2 picks the kit up)
function tr_foley_takes_medikit()
  if api.has_item(O.HERO_FOLEY, O.FOLEY_MEDIKIT) == 0 then api.deactivate_object(O.FOLEY_MEDIKIT) end
end
