--@patches Training2.dll
--@readable 1
-- RestoredMissions: Training 2 (ADVANCED WEAPONS TRAINING) - cut content put back. A mission patch (DesertStormFix 3.3+): it runs as part
-- of the readable Lua that DesertStormFix makes from YOUR Training2.dll - nothing of the game is in this file.
-- The --[[@edit ... --@to ... ]] blocks change those lines of the readable mission (each must match exactly
-- one place); the Lua below them adds the restored parts. Reference: docs/sdk/reference/mission-patches.md.

--[[@edit
        D.g_13248 = 0
        f_38F0(1401)
        g_131E0 = 26
--@to
        D.g_13248 = 0
        f_38F0(1401)
        g_131E0 = 25   -- RestoredMissions: was 26
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

-- ============================ RestoredMissions: Training 2 (ADVANCED WEAPONS TRAINING) ============================
-- Restored (everything below was on the disc / in the scene, unused):
--   * step 25, the gap between the anti-tank and vehicle sections (an edit block above: step 24 -> 25): after Master
--     Sergeant Booth's "Good luck, son!" comes the unused line 1402 "About time, you better hustle. Franks is
--     waiting for you in the next training area. Take the truck, okay?" - then the DLL's step 26 (the truck).
--   * the people of the base the scene places and the script never brings into play: the SAS commander pacing his
--     office (OUTRO_SAS_COMMANDER, his own waypoint loop with its 10 s / 3 s pauses), the office guard, and the
--     off-map support instructor (MS_OFFMAP) waiting at the viewport bunker.
--   * the A-10 chase shot: the scene's VEHICLE_CHASE_CAMERA follows the second A-10 (A10_SMALL136) on its attack
--     run after the laser lock, until its bombs hit.
--   * the briefing's story text T02_BRF_TXT (never shown).
--   * GUARD_3's patrol by the start gate (waypoints _3 -> _4: ~14 m up and down, 5 s / 10 s waits). The script uses him as the
--     place for the first air strike's sound, which is played 100 m round him - he stays near his start spot.
--   * Johnson's walk to the observation tower (MS_JOHNSON_1 -> MS_JOHNSON_MOUNTLADDER) and his climb up LADDER_8M
--     with the game's own ladder code (common.lua tr_climb); the scene had him standing on the tower from the start.

local O, W, C = SCENE.objects, SCENE.waypoints, SCENE.cameras

local dll_init = mission_init
function mission_init()
  dll_init()
  if api.is_loading_save() ~= 0 then TR.johnson = {phase = "up"} end   -- a save: he is wherever the save put him
  api.set_briefing_text(TEXT.T02_BRF_TXT, 3)
end

-- step 25: line 1402 once Booth's 1401 has finished, then the truck (step 26)
function tr_step25()
  local s = TR.s25
  if not s then
    if api.object_sound_playing(O.MS_JOHNSON) ~= 0 then return end
    TR.s25 = {t = tr_now()}
    return
  end
  if not s.played then
    if tr_now() - s.t < 800 then return end
    f_38F0(1402)   -- the DLL's speech call: Training2.sbk, played as its other lines are
    s.played = true
    return
  end
  if api.object_sound_playing(O.MS_JOHNSON) == 0 then
    g_131E0 = 26
  end
end

-- the A-10 chase shot
local dll_laser = on_laser
function on_laser(a)
  local r = dll_laser(a)
  if a == O.M1_Abrams_drone3 and r == 1 and not TR.chase then
    TR.chase = {phase = 1, t = tr_now()}
  end
  return r
end
local dll_waypoint = on_waypoint
function on_waypoint(unit, waypoint)
  if TR.chase and unit == O.A10_Tankbuster2 and waypoint == W.A10_Tankbuster2_WP01 then
    TR.chase.hit = tr_now()
  end
  return dll_waypoint(unit, waypoint)
end
function tr_chase_step()
  local c = TR.chase
  if not c or c.phase == 9 then return end
  local t = tr_now() - c.t
  if c.phase == 1 and t > 2500 then   -- after "Target coordinates received! Strike inbound! Over!"
    api.set_player_control(0)
    api.letterbox_on()
    api.set_view_camera(C.VEHICLE_CHASE_CAMERA)
    c.phase = 2
  elseif c.phase == 2 and ((c.hit and tr_now() - c.hit > 3500) or t > 30000) then
    api.set_view_camera(0)
    api.stop_all_cameras()
    api.letterbox_off()
    api.set_player_control(1)
    c.phase = 9
  end
end

-- Johnson walks to the tower and climbs it; if anything goes wrong he is put back on the tower
local J = O.MS_JOHNSON
local TOWER_SPOT = {1400.24, 809.2, 1274.1}   -- where the scene puts him
function tr_johnson_step()
  local j = TR.johnson
  if j == nil then
    j = {phase = "walk", t = tr_now()}
    TR.johnson = j
    api.place_at_waypoint_rotated(J, W.MS_JOHNSON_1)
    tr_walk(J, W.MS_JOHNSON_1)
    return
  end
  if j.phase == "walk" then
    if tr_dist_to_wp(J, W.MS_JOHNSON_MOUNTLADDER) < 80 and api.is_path_finished(J) ~= 0 then
      j.phase, j.climb = "climb", tr_climb(J, true)
    elseif tr_now() - j.t > 40000 then
      j.phase = "failed"
    end
  end
  if j.phase == "climb" then
    local r = tr_climb_step(j.climb)
    if r == "done" then j.phase = "up" elseif r == "failed" then j.phase = "failed" end
  end
  if j.phase == "failed" then
    local b = scratch(12)
    mem.wf(b, TOWER_SPOT[1]); mem.wf(b + 4, TOWER_SPOT[2]); mem.wf(b + 8, TOWER_SPOT[3])
    api.place_at_position(J, b)
    game.log("RestoredMissions: Johnson's climb failed - put on the tower")
    j.phase = "up"
  end
end

local dll_update = mission_update
function mission_update()
  local r = dll_update()
  if not TR.ambient then
    TR.ambient = true
    api.activate_object(O.COMMANDER)
    tr_walk(O.COMMANDER, W._5)
    api.activate_object(O.OFFICEGUARD01)
    api.activate_object(O.MS_OFFMAP)
    api.activate_object(O.GUARD_3)
    tr_walk(O.GUARD_3, W._3)
  end
  if not TR.johnson or TR.johnson.phase ~= "up" then tr_johnson_step() end
  if g_131E0 == 25 then tr_step25() end
  tr_chase_step()
  return r
end
