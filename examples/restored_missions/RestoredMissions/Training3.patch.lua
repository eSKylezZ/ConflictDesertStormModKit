--@patches Training3.dll
--@readable 1
-- RestoredMissions: Training 3 (SQUAD TRAINING) - cut content put back. A mission patch (DesertStormFix 3.3+): it runs as part
-- of the readable Lua that DesertStormFix makes from YOUR Training3.dll - nothing of the game is in this file.
-- The --[[@edit ... --@to ... ]] blocks change those lines of the readable mission (each must match exactly
-- one place); the Lua below them adds the restored parts. Reference: docs/sdk/reference/mission-patches.md.

--[[@edit
      f_45E0(120)
      f_45E0(96)
      g_1374C = 3
--@to
      f_45E0(120)
      f_45E0(96)
      tr_franks_leave()   -- RestoredMissions
      g_1374C = 3
]]

--[[@edit
      api.activate_object(obj.F15_03)
      mission_phase = 1
--@to
      api.activate_object(obj.F15_03)
      tr_franks_loudspeaker()   -- RestoredMissions
      mission_phase = 1
]]

--[[@edit
  elseif g_1374C == 50 then
    if f_44E0(1000) ~= 0 then
      mission_phase = 0
      D.g_13778 = 0
      f_4440(2451)
      api.give_item(obj.BRADLEY, obj.MEDIKIT)
      api.hold_medikit(obj.BRADLEY)
      g_1374C = 51
    end
--@to
  elseif g_1374C == 50 then
    if not tr_medikit_selected() then   -- RestoredMissions: T3_15
      if api.object_in_region(obj.BRADLEY, rgn.YELLOW_FLAGSTATION) == 0 then
        g_1374C = 41
      end
      return
    end
    if f_44E0(1000) ~= 0 then
      mission_phase = 0
      D.g_13778 = 0
      f_4440(2451)
      g_1374C = 51
    end
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

-- ============================ RestoredMissions: Training 3 (SQUAD TRAINING) ============================
-- Restored (everything below was on the disc / in the scene, unused):
--   * Master Sergeant Franks with his loudspeaker: once he has sent you to the door ("Step to the door ahead of you
--     and we'll begin"), Franks walks off along his platform (MASTER_SERGEANT_1 -> _2), and on the other side of the
--     blast door FRANK_2 - Franks again, carrying the Loudspeaker (the only reason LOUDSPEAKER.EVO is in this level)
--     - walks to BEGIN_SPEAKING above the command arena and watches you from there.
--   * the Medi-Kit inventory lesson (T3_15 "Hold down Inventory, highlight the Medi-Kit and then release
--     Inventory"): the DLL put the Medi-Kit straight into your hands; now you select it yourself first.
--   * "Careful Private, that was a civilian!" - the range's penalty for a wrong hit went to a debug print that does
--     nothing in the shipped game; now it is shown (your Training3.dll's own text, shown at run time).
--   * the Black Hawk leaves: its route ends at "Blackhawk17_deactivate", but the DLL never took it out of play
--     there - it hung outside the map for the rest of the mission.
--   * the briefing's story text T03_BRF_TXT and T03_COMPLETED ("CONGRATULATIONS") before the end screen.

local O, W = SCENE.objects, SCENE.waypoints
local CONTROL_INVENTORY = 6   -- get_control_name index of INVENTORY (table 0x5D9338)

local dll_init = mission_init
function mission_init()
  dll_init()
  api.set_briefing_text(TEXT.T03_BRF_TXT, 3)
end

-- step 2 -> 3: the start gate and the blast door open; Franks leaves along his platform
function tr_franks_leave()
  if TR.franks_left then return end
  TR.franks_left = true
  api.clear_look_target(O.MASTER_SERGEANT)
  tr_walk(O.MASTER_SERGEANT, W.MASTER_SERGEANT_1)
end

-- step 3: you're through the blast door - Franks with the loudspeaker takes his place above the arena
function tr_franks_loudspeaker()
  if TR.frank2 then return end
  TR.frank2 = {t = tr_now()}
  api.activate_object(O.FRANK_2)
  tr_walk(O.FRANK_2, W._2)   -- "2" -> "3" -> BEGIN_SPEAKING
end
function tr_frank2_step()
  local f = TR.frank2
  if not f or f.done then return end
  if tr_dist_to_wp(O.FRANK_2, W.BEGIN_SPEAKING) < 100 or tr_now() - f.t > 30000 then
    api.set_look_target(O.FRANK_2, obj.BRADLEY)
    f.done = true
  end
end

-- step 50: select the Medi-Kit yourself (true once you hold it)
function tr_medikit_selected()
  if f_44E0(1000) == 0 then return false end   -- the sergeant is still talking
  if not TR.medikit_given then
    TR.medikit_given = true
    api.give_item(obj.BRADLEY, obj.MEDIKIT)
  end
  if tr_holding_medikit(obj.BRADLEY) then return true end   -- (is_holding by id misses it: the kit is a new item)
  api.show_tutorial_text(TEXT.T3_15, api.get_control_name(CONTROL_INVENTORY), api.get_control_name(CONTROL_INVENTORY))
  return false
end

-- the Black Hawk reaches Blackhawk17_deactivate
local dll_path_done = on_path_done
function on_path_done(unit)
  if unit == O.BLACKHAWK then
    api.deactivate_object(unit)
    return
  end
  return dll_path_done(unit)
end

-- CONGRATULATIONS, then the DLL's own end
local dll_complete = on_complete
function on_complete()   -- (from the last step every frame, or once from the "complete" message)
  if TR.complete_at == nil then
    TR.complete_at = tr_now() + 3000
    api.show_message(TEXT.T03_COMPLETED, 3000)
  end
end

local dll_update = mission_update
function mission_update()
  local r = dll_update()
  tr_frank2_step()
  if TR.complete_at and not TR.completed and tr_now() >= TR.complete_at then
    TR.completed = true
    dll_complete()
  end
  return r
end

-- "Careful Private, that was a civilian!": the range's penalty for a wrong hit went to a debug print, which does
-- nothing in the shipped game - shown in the message bar instead (the text is your Training3.dll's own: it reaches
-- this function as the debug print's argument)
local dll_debug_print = api.debug_print
function api.debug_print(text, ...)
  if type(text) == "string" and text:find("civilian", 1, true) then return api.show_message_raw(text, 3000) end
  return dll_debug_print(text, ...)
end
