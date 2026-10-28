--@patches Training1.dll
--@readable 1
-- RestoredMissions: Training 1 (BASIC TRAINING) - cut content put back. A mission patch (DesertStormFix 3.3+): it runs as part
-- of the readable Lua that DesertStormFix makes from YOUR Training1.dll - nothing of the game is in this file.
-- The --[[@edit ... --@to ... ]] blocks change those lines of the readable mission (each must match exactly
-- one place); the Lua below them adds the restored parts. Reference: docs/sdk/reference/mission-patches.md.

--[[@edit
      api.set_target_raised(obj.RANGETARGET_10, 1.0)
      g_14258 = 45
--@to
      api.set_target_raised(obj.RANGETARGET_10, 1.0)
      g_14258 = 41   -- RestoredMissions: was 45
]]

--[[@edit
    elseif f_4840(1000) == 0 then
      return
    else
      D.g_142B0 = 0
      f_47B0(2689)
--@to
    elseif f_4840(1000) == 0 then
      return
    elseif not tr_armourer_fetch() then   -- RestoredMissions
      return
    else
      D.g_142B0 = 0
      f_47B0(2689)
]]

--[[@edit
        f_47B0(2705)
        g_14258 = 19
--@to
        f_47B0(2705)
        tr_heli_flyover()   -- RestoredMissions
        g_14258 = 19
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

-- ============================ RestoredMissions: Training 1 (BASIC TRAINING) ============================
-- Restored (everything below was on the disc / in the scene, unused):
--   * the intro film: scene 1 on the parade ground (CS_T01_S01.psf - the marching cadence) and scene 2 in the
--     commander's office (CS_T01_S02.psf: "Enter!" ... "Okay then soldier, get moving." / "Sir, yes sir!"), with the
--     scene's own actors (CS_S1_*, CS_S2_*, marching guards), paths and cameras, the date / place cards (MOV_TXT1/2)
--     and the subtitles SPEECH1-6 timed to the stream. Skippable like every film (movie cancel / SkipCutscenes).
--   * the "switch between targets" lesson (an edit block above: step 40 -> 41; the DLL's steps 41-44 do the rest).
--   * the armourer fetching your kit: line 2688 "Hold on a minute, I'll just get your kit." while he turns to the
--     shelf and the guns go from the shelf to the counter, then the DLL's line 2689 "There you go, sir."
--   * the HELI_FLYOVER Black Hawk crossing the assault course as it starts.
--   * the course fly-bys: CS_ASSAULT_CAM pans over the assault course during "ahead of you is my assault course"
--     and CS_SHOOTINGRANGE_CAM over the range during "Now step up to the firing plate behind you!" (both scene
--     cameras pan along their CS_*_PAN waypoints by themselves over 4 s).
--   * T01_COMPLETED ("CONGRATULATIONS") before the end screen.

local O, W, C = SCENE.objects, SCENE.waypoints, SCENE.cameras
local MARCHERS = {
  {O.CS_MARCHING_GUARD_COMMANDER, W.MARCHING_GUARD_COMMANDER_W1},
  {O.CS_MARCHING_GUARD_1, W.MARCHING_GUARD_1_W1},
  {O.CS_MARCHING_GUARD_2, W.MARCHING_GUARD_2_W1},
}

local function black_in(ms) api.fade_in(f32(ms + 0.0), 0, 0, 0) end
local function black_out(ms) api.fade_out(f32(ms + 0.0), 0, 0, 0) end
local function text(h) api.show_movie_text(h, 0) end

local function intro_timeline()
  local s1 = api.get_stream_id("CS_T01_S01.psf")
  local s2 = api.get_stream_id("CS_T01_S02.psf")
  game.log(string.format("RestoredMissions: intro streams %08X %08X", s1 & 0xFFFFFFFF, s2 & 0xFFFFFFFF))
  local S2 = 18300   -- scene 2 starts here
  return tr_timeline({
    -- scene 1: the parade ground, the cadence (18 s)
    -- no set_sound_mode(1) here: the game's movie sound mode refuses every ordinary sound and only switches back
    -- when the next sound starts - with no stream after the film nothing ever did, and the commander stayed
    -- silent for the whole level (his lines then also followed each other a second apart)
    {0, function()
      api.movie_start()
      api.set_player_control(0)
      api.letterbox_on()
      api.set_view_camera(C.CS_S1_SCENE_CAM)
      black_in(1500)
      if s1 ~= 0 then api.play_stream(s1) end
      for _, m in ipairs(MARCHERS) do
        api.activate_object(m[1])
        tr_walk(m[1], m[2])
      end
      tr_walk(O.CS_S1_BRADLEY, W.CS_S1_BRADLEY_PATH1)
    end},
    {500, function() text(MOVIE.MOV_TXT1) end},
    {2000, function() text(MOVIE.MOV_TXT2) end},
    {7500, function() api.set_view_camera(C.CS_S1_FLAG_CAM) end},
    {S2 - 1100, function() black_out(1000) end},
    -- scene 2: the commander's office (37 s); times from the transcribed stream
    {S2, function()
      api.set_view_camera(C.CS_S2_WIDE_VIEW)
      api.place_at_waypoint_rotated(O.CS_S2_BRADLEY, W.CS_S2_BRADLEY_PATH1)
      black_in(800)
      if s2 ~= 0 then api.play_stream(s2) end
    end},
    {S2 + 1400, function() text(MOVIE.SPEECH1) end},                       -- "Enter!"
    {S2 + 2300, function() api.set_door_open(O.CS_Z1_STARTDOOR, 1.0) end},
    {S2 + 2900, function() tr_walk(O.CS_S2_BRADLEY, W.CS_S2_BRADLEY_PATH2) end},
    {S2 + 8000, function() api.set_view_camera(C.CS_S2_CLOSEUP); text(MOVIE.SPEECH2) end},
    {S2 + 11300, function() api.set_view_camera(C.CS_S2_BRADLEY_SHOULDER); text(MOVIE.SPEECH3) end},
    {S2 + 12400, function() api.set_view_camera(C.CS_S2_CLOSEUP); text(MOVIE.SPEECH4) end},
    {S2 + 15800, function() text(MOVIE.SPPECH4B) end},
    {S2 + 19500, function() api.set_view_camera(C.CS_S2_BRADLEY_SHOULDER) end},
    {S2 + 23100, function() api.set_view_camera(C.CS_S2_CLOSEUP); text(MOVIE.SPEECH4C) end},
    {S2 + 30200, function() text(MOVIE.SPEECH4D) end},                      -- "Any questions?"
    {S2 + 32300, function() api.set_view_camera(C.CS_S2_WIDE_VIEW); text(MOVIE.SPEECH5) end},
    {S2 + 33400, function() api.set_view_camera(C.CS_S2_CLOSEUP); text(MOVIE.SPEECH6) end},
    {S2 + 35300, function() api.set_view_camera(C.CS_S2_BRADLEY_SHOULDER) end},   -- "Sir, yes sir!"
    {S2 + 36300, function() black_out(800) end},
    {S2 + 37300, function() end},
  })
end

local function intro_end()
  api.stop_stream()
  api.run_queued_calls()
  api.set_view_camera(0)
  api.stop_all_cameras()
  api.letterbox_off()
  for _, m in ipairs(MARCHERS) do api.deactivate_object(m[1]) end
  api.set_player_control(1)
  api.movie_end()
  black_in(600)
end

-- a loaded save starts in the middle of the level: no film
local dll_init = mission_init
function mission_init()
  dll_init()
  if api.is_loading_save() ~= 0 then TR.intro = "done" end
end

-- the first update frames: the film, then the DLL's own start (its first frame restores the cutscene actors)
local dll_update = mission_update
function mission_update()
  if TR.intro ~= "done" then
    if TR.intro == nil then
      TR.intro = intro_timeline()
    end
    local finished = tr_timeline_step(TR.intro)
    if not finished and TR.intro.next > 2 and api.movie_cancel_pressed() == 1 then finished = true end
    if not finished then return end
    intro_end()
    TR.intro = "done"
  end
  return dll_update()
end

-- step 33: the armourer fetches the kit (true once it is on the counter). The scene's ARMOURER_SHELF /
-- ARMOURER_COUNTER waypoints would have walked him to the shelf and back, but he can't route there (tested: his
-- path is set, he never moves - the walk map built for this level doesn't connect the space behind the counter),
-- so he turns to the shelf while he says his line and the guns go from the shelf to the counter.
function tr_armourer_fetch()
  local a = TR.armourer
  local armourer = O.ARMOURER
  if a == nil then
    a = {phase = 1, t = tr_now()}
    TR.armourer = a
    api.clear_look_target(armourer)
    api.face_waypoint(armourer, W.ARMOURER_SHELF)
    api.play_object_sound_2d(armourer, 2688, D.g_14284)   -- "Hold on a minute, I'll just get your kit."
    for _, id in ipairs({O.PISTOL, O.RIFLE, O.SNIPER_RIFLE}) do api.deactivate_object(id) end
    return false
  end
  local t = tr_now() - a.t
  if a.phase == 1 then
    if (t > 500 and api.object_sound_playing(armourer) == 0) or t > 6000 then   -- his line has ended
      for _, id in ipairs({O.SHELF_PISTOL, O.SHELF_RIFLE, O.SHELF_SNIPER_RIFLE}) do api.deactivate_object(id) end
      a.phase, a.t = 2, tr_now()
    end
  elseif a.phase == 2 then
    if t > 1500 then
      for _, id in ipairs({O.PISTOL, O.RIFLE, O.SNIPER_RIFLE}) do api.activate_object(id) end
      api.set_look_target(armourer, obj.BRADLEY)
      a.phase = 3
      return true
    end
  else
    return true
  end
  return false
end

-- the assault course starts: the Black Hawk flyover
function tr_heli_flyover()
  if TR.heli then return end
  TR.heli = true
  api.activate_object(O.HELI_FLYOVER)
  api.set_path(O.HELI_FLYOVER, W.HELI_FLYOVER_1)
  api.set_ai_state(O.HELI_FLYOVER, 0)
end
local dll_path_done = on_path_done
function on_path_done(unit)
  if unit == O.HELI_FLYOVER then
    api.deactivate_object(unit)
    return
  end
  return dll_path_done(unit)
end

-- a course fly-by: a scene camera (it pans by itself) while the commander's line plays, then back to the player
local function flyby_step()
  local f = TR.flyby
  if not f then return end
  local t = tr_now() - f.t
  if not f.on then
    f.on = true
    api.set_player_control(0)
    api.letterbox_on()
    api.set_view_camera(f.cam)
  elseif t > 4300 and (api.object_sound_playing(O.COMMANDER) == 0 or t > 6000) then   -- the pan is 4 s; some
    -- lines end in seconds of silence, so the shot doesn't wait for more than 6 s
    api.set_view_camera(0)
    api.stop_all_cameras()
    api.letterbox_off()
    api.set_player_control(1)
    TR.flyby = nil
  end
end
local FLYBYS = {[18] = C.CS_ASSAULT_CAM, [36] = C.CS_SHOOTINGRANGE_CAM}
local dll_update3 = mission_update
function mission_update()
  local r = dll_update3()
  local cam = FLYBYS[g_14258]
  local entered = TR.step_seen ~= g_14258
  TR.step_seen = g_14258
  if TR.intro == "done" and cam and entered and not (TR.flown or {})[g_14258] then
    TR.flown = TR.flown or {}
    TR.flown[g_14258] = true
    TR.flyby = {cam = cam, t = tr_now()}
  end
  flyby_step()
  return r
end

-- CONGRATULATIONS, then the DLL's end 3 s later (on_complete comes every frame of the last step, or once from the
-- "complete" message; the end itself runs from the update below)
local dll_complete = on_complete
function on_complete()
  if TR.complete_at == nil then
    TR.complete_at = tr_now() + 3000
    api.show_message(TEXT.T01_COMPLETED, 3000)
  end
end
local dll_update2 = mission_update
function mission_update()
  local r = dll_update2()
  if TR.complete_at and not TR.completed and tr_now() >= TR.complete_at then
    TR.completed = true
    dll_complete()
  end
  return r
end
