# Desert Storm Editor (early)

A world editor for Conflict: Desert Storm missions that runs in your browser. It shows a level's world and everything
placed in it, and lets you move, copy, delete and change objects, AI routes, waypoints, trigger regions, cameras and
sounds. Saving writes a mod that the DesertStormFix plugin loads.

## Run it

```
python editor/server.py --data <extracted game folder> [--game <game folder>]
```

- `--data`: the folder `cli/ds_extract.py` (or the Blender add-on's extract) wrote, one sub-folder per archive.
- `--game`: the game folder, where *Save as mod* writes `Mods\<name>\<LEVEL>.ENV` / `.RMD`. Found automatically when
  the game is installed.
- The editor opens at `http://127.0.0.1:8765/`. Only this PC can reach it.

No packages to install: Python 3 and a browser. Models are converted to `.glb` on first use and cached in
`<data>/_editor_cache`.

## Using it

| Action | How |
| --- | --- |
| Look around | Left drag orbit, right drag pan, wheel zoom (toward the cursor) |
| Fly | W A S D, Q / E down / up, Shift faster |
| Get close | Double-click a surface: the view centres there and moves in (into buildings, under bridges) |
| Select | Click an object, prop, waypoint, region, camera, sound or route marker; or pick from the Objects list |
| Move | Drag the gizmo's arrows |
| Turn | Hold Alt and drag the ring (the facing of soldiers, vehicles and props) |
| Change a value | Edit it in the right panel, Enter to apply |
| Place from the Library | Drag a row into the 3D view (a see-through preview follows the ground or building under the cursor), or click a row and then click in the world |
| Copy / paste | Ctrl+C the selection (Shift+click for several objects and props), Ctrl+V pastes under the mouse - also into another level or mission. Pasted soldiers get new script ids, names stay unique |
| Duplicate / delete | Ctrl+D / Del |
| Undo / redo | Ctrl+Z / Ctrl+Y |
| Focus | C (double-click also dives in) |
| Drop to ground | F (or *⤓ Drop to ground* in the view's toolbar): the selection - one or several - drops straight down onto the ground or the surface under it (a floor, a roof below it; not one above it). One undo step per record |
| Random turn & size | View toolbar: props placed from the Library get a random facing and ±10 % size (objects a random facing) - for rocks, crates, barrels. Remembered per browser |
| Place props / objects | *Library* tab: pick one, click a spot in the world (Esc cancels) |
| Layer | Select an object, type a layer name: it waits until the mission script calls `game.show_layer("name")` (drawn see-through) |
| Save (replace a level) | Type a mod name, *Save as mod* |
| Save a new mission | *Save as mission…*: scene, routes, navigation files, models from other levels and a Lua script; with a campaign name it appears under SINGLE PLAYER › CUSTOM CAMPAIGNS |
| Reopen a mission | *Open mission…* |
| New weapon / uniform | *Assets…*: forms that write a `.weapon` or `.skin` into a mod; picked files (textures, models, HUD pictures, WAVs) are uploaded into the mod folder, PNG textures converted to DDS |
| Game data (records) | *Records…*: every data table of the game - weapons, items, soldier types, their stats, kits and skills, experience levels, vehicles and their stats / guns, scenery, structures, doors - with named columns, links between them (→ opens the linked record) and *Used by*. Change a record, copy it as a new one, revert or delete it; changes go into the mod named in the header (`.weapon` files for weapons, `<table>.replace.txt` / `<table>.txt` rows otherwise). *Texts* tab: search the game's texts - shared ones and each mission's own (objectives, briefing, subtitles; tagged M01, T02 ...) - change them or add your own (`<mod>.strings`, mission texts under `[M01]` so they change only in that mission; the hash shown is what scripts use). Deep links: `#records/UNITS/Iraqi_Conscript`, `#texts/objective`. MCP: records_tables / records_list / record_get / record_set / record_new / record_revert / record_delete, strings_list / string_set / string_revert (with `mission`) |
| Test it | *▶ Play* (also in the script window): saves the mission and starts the game straight into it - the launcher clicks itself, and the game quits after the mission's end screen. Needs DesertStormFix with `+level` support |
| Sharing a mission | The mission's mod folder holds only your work: the script, `<NAME>.envpatch` (your scene as changes to the base level's), `<NAME>.rmdpatch` (route changes) and `<NAME>.uses` (the game files it borrows: models from other levels, the base level's nav files, sound banks). DesertStormFix builds the full files from the player's own copy of the game when it starts, so no game data is redistributed. Saving an older mission converts it and deletes the game-file copies it had (only files identical to the game's own) |
| AI navigation | A mission ships no walk map: the game builds it - and the route markers on it - from your scene when the mission loads, so props and vehicles you place block the AI's paths (0.3 s). The hand-placed route markers (yellow; mission scripts use them by number) are kept from the level's route file; the generated ones (dim green) can't be moved or deleted - change the props instead. New markers you add are hand-placed |
| Terrain | *Terrain…*: make a patch (size, vertices, the level's ground textures, walkable surface) at the mouse, then drag on it with Raise / Lower / Smooth / Flatten (radius, strength), add rolling hills, change the texture. A patch is a prop: move / rotate / copy it like one. It saves with the mission as its own model with collision - the AI walks on it. MCP: terrain_new / terrain_brush / terrain_noise / terrain_set |
| New level | *New level…*: an empty level - pick whose sky, sounds and textures it uses, a size - with a sculptable ground (Terrain…) and the squad; it is a mission straight away. Its script has the level set-up and nothing of a shipped mission |
| Roads | *Road…*: click points on the ground, Enter: a smoothed road that follows the ground (sampled every 2 m), with a road texture from any level (borrowed from the player's install), a width and a walkable surface. Select a road to change it. MCP: road_new / road_set |
| Kits | *Kits* tab: the pieces of any level's ground models (cliff faces, wall runs, building shells, road segments - Mission 1 alone has 1312) - pick a level and model, search by texture, click or drag a piece in; identical pieces are listed once (×N). Move / rotate / copy it like a prop (the gizmo sits on the piece). The mod only references it ("piece KIT_MIS1_ZONE1_170 = MIS1_ZONE1.EVO 170"); DesertStormFix cuts it out of the player's own files |
| Snapping | View toolbar *Snap* (0.5 / 1 / 2 / 4 / 5 / 8 m) and *Turn* (15° / 45° / 90°): moves and turns snap while you drag; kit pieces snap by their corner when you let go, so 8 m landscape tiles meet edge to edge on the original grid. Remembered per browser |
| Mission logic | A mission saved from a shipped level runs that level's own logic underneath (`--@base Mission1.dll` at the top of the script): briefing, cutscenes, objectives, triggers, Foley's cell. Your Lua runs after it. Keep the original objects' script ids (the editor does) - deleting an object the original script uses can break it |
| Game console | *Console* (opens by itself on *▶ Play*): the game's log (`DesertStormFix.log`, dev / Steam build of the plugin) while it runs; when the game crashes, the error, where it happened, the likely cause, the last mod file it loaded and the scene lines that cause it (click to select). *Play* refuses a scene with a known crasher, such as a vehicle model used as a zone prop - vehicles go in as objects from the Library |
| Objectives | *Objectives…*: the mission's objectives without Lua - a title (shown in game), what completes it (kill / destroy objects - type script ids or *◎ Pick* them in the view; reach a region; survive N seconds; or a rule), shown or hidden at the start, the order; *Mission succeeds when all are done*. New ones get indices after the base mission's own objectives (Mission 1 adds 0-3). Tabs: *Event rules* (all rules, incl. timed ones) and *Lua* (what the editor writes) |
| Events & Actions | Select a region or an object: *Events & Actions* in the right panel - "When a player enters / leaves" (regions) or "When it is killed / destroyed" (objects) → show / hide a layer, show / complete an objective, mission success / failure; first time only or every time. Timed rules ("N seconds after the start") in *Objectives… › Event rules* |
| Soldier behaviour | Select a soldier: *Behaviour › AI profile* - Aggressive Assault, Defensive Guard, Patrolling, Sniper / Overlook, Ambush (dormant: waits for the script - put it in a layer and show the layer from a rule). The profiles copy the AI values of shipped soldiers with that job (the fields themselves aren't decoded); Shift+click several soldiers to set them together. *Assign patrol…*: click waypoints in order, Enter - drawn as a dashed orange loop. Patrols are saved with the mission (`editor.patrols` in the script) but don't move soldiers yet: the game's command for walking a waypoint route isn't mapped |
| Cameras | Select a camera: *👁 View through camera* (Esc returns), its field of view; dashed lines show where every camera looks, a target camera's look-at point is a wireframe box you can select and move |
| Atmosphere | *Atmosphere…*: time of day, the direct sun / ambient / fog / sky colours of the four keys the game blends by the hour (night, dawn, noon, dusk), fog distances, the scene's sun record (colour, tilt, heading) - previewed in the 3D view while the panel is open. Each change is one undo step |
| Shadows | The sun casts shadows from the ground, props, vehicles and soldiers. The shadow area follows what you look at (sharp close up, coarser zoomed out). *Shadows* in the view's toolbar turns them off on slow graphics; remembered per browser |
| AI walk map | Layers › *AI walk map*: the game's own walk map for the level (`<LEVEL>.QTD`) - green cells soldiers can walk on, red blocked ones (red outlines for big blocked areas off the playable ground). Props, kit pieces and vehicles you added get a red box: the game rebuilds the map from your scene when the mission loads and cuts those out. MCP: walkability_grid |
| Tactical map | *Generate Minimap*: a top-down picture of the ground and props over exactly the area the game stretches its map across (the level bounds), 256 × 256 like the game's own maps. *Save to mission* writes `GW<NAME>.img` + `GW<NAME>0.png` into the mission's mod folder and makes the script load it (the set-up's `game.load_map_image`, or an `editor_minimap()` block called from `init()` when a base mission DLL loads the shipped map). Needs a saved mission. MCP: minimap_info / save_minimap |
| Prefabs | *Prefabs* tab: Shift+click objects and props (a bunker, sandbags, an ammo box, the gunner), name them, *Save selection as prefab*. Click a prefab and click in the world (or drag it in) to stamp the whole group - turned 0 / 90 / 180 / 270° or at random; new script ids and names, vehicles with their crews, one undo step. Prefabs are kept for every level and mission (`<data>/_editor_cache/prefabs`). MCP: save_prefab / list_prefabs / paste_prefab / delete_prefab |
| Vehicle crews | Select a vehicle: *Crew* lists its seats (driver, gunner, commander, passengers - from the game's VEHICLE.TXT) with who sits in each. Pick a soldier from the list (nearest first; one already in a seat leaves it), *(empty)* clears a seat, *Spawn* adds a new crewman of the vehicle's side, → selects the crewman. Crew mount at level start and start in play with their vehicle. MCP: vehicle_crew / assign_crew |
| Sounds | Select an ambient sound: *▶ Audition* plays it (decoded from the level's sound cache in the game folder), *loop* like the game; in the view the inner sphere is full volume, the outer one where it fades out. Select Bradley, Foley, Jones or Connors: their radio lines (the ones this level's sound cache has). MCP: sound_info / voice_banks; the browser plays `GET /audio/<LEVEL>/<sample>.wav` / `/audio/<LEVEL>/<BANK>/<line>.wav` |
| Vision cones | Layers › *Vision cones* (off by default): every enemy soldier's sight from head height - the solid cone is the unaware range (the normal state), the dashed amber fan the alerted range. Ranges follow the game's code: the HLOI's `ai_f2` in metres, else the soldier type's HSTAT.TXT column 7 (Iraqi 60 m, squad 70 m); × 0.75 while unaware; shorter at night (down to × 0.75, from the scene's time of day); capped at the fog distance − 5 m (74 m in the shipped levels). The cone's width is editor guidance per AI profile (sniper 50°, patrol 130° …) - the game's view angle isn't decoded. Cones follow a soldier while you turn it and change with the time of day. MCP: soldier_detection_profiles |
| Minefields | *Library › Emplacements & minefields › ☠ Minefield brush*: drag over sand, roads or terrain - anti-tank mines (the shipped `US_WPN_MineAntiTank` object, Missions 9 / 10 / 12A) land flush with the surface, one minefield per stroke (one undo step), shown with a red ☠ circle (editor only). The list under the brush removes a field with its mines. There is no anti-personnel mine: its model isn't in any level archive. Not yet tried in game. MCP: minefield_paint / minefield_delete / tactics_info |
| Emplacements | *Library › Emplacements & minefields*: one click stamps *Sandbag Bunker + .50 Cal Nest* (Mission 5A's sandbag mount + the M2HB tripod), *MG Nest + NSV 12.7 mm* (Mission 8's nest - the game's Soviet heavy MG; there is no DShK) or *Rooftop NSV* (gun only), facing *Gun faces*, with an enemy gunner in its seat (crew list, mounted at level start). Offsets copied from the shipped scenes. There is no mortar in the game data. MCP: place_emplacement / emplacement_presets |
| Air support | *Mission Settings… › Air Support*: aircraft (A-10 / AH-64), payload, strikes, re-arm time, a laser designator pick-up next to the squad. Compiled into the script (`-- @editor begin: tactics`): the script answers the game's laser query (`game.EVENT.LASER`) - yes while strikes are left and re-armed, otherwise a pop-up says why. The strike itself is the game's own; the aircraft / payload is written into the script as data. Not yet tried in game. MCP: set_air_support |
| Environment preview | View toolbar: ☀️ Day / 🌪️ Sandstorm (beige fog, dim sun, blowing dust) / 🌙 Night Ops (moonlight) / 🟢 NVG (night through a green-phosphor goggle shader) - how silhouettes read in those conditions. Editor only: the scene isn't changed (use *Atmosphere…* for that) |
| History | *History* (Ctrl+H): every change in order, by name ("Move T-72 Battle Tank", "Paint Minefield (12 anti-tank mines)"). Click a step to go straight back to just after it - or forward again; later steps stay redoable until the next change. Undo / Redo show what they would undo. MCP: history (timeline) / history_jump; every changing command's result carries its `action` title |
| Write the mission | *Script…*: the mission's Lua with snippets (kills, regions, timers, layers, objectives, mission end), the scene's region / layer names, the selected object's id, and the errors from the last game run. Ctrl+S saves |

Marker colours: green squad, red enemy, blue vehicle, yellow item; route links green both ways, orange one way.

## Missions and their scripts

*Save as mission* writes `Mods\<mod>\<NAME>.ENV/.RMD/.OCT/.QTD`, `<NAME>.editor.json` (base level, layers) and
`<name>.lua`. The script is generated once: `setup()` replays the base mission's map set-up (from
`level_setups.json`, made by `scripts/gen_level_setups.py`) with the new scene, and the default goal is to kill every
enemy soldier. After that it is yours - saving again only rewrites the editor's blocks: `-- @editor begin: layers` … `-- @editor end`
(`game.layers` and `game.regions`), `-- @editor begin: objectives` … `-- @editor end: objectives` and
`-- @editor begin: rules` … `-- @editor end: rules` (the Objectives… / Events & Actions logic compiled to commented Lua).
`init()` calls `editor_init()` and `on_message()` calls `editor_on_message(id, a, b)` - the editor adds those two lines
to an older script when it gets objectives or rules. The logic itself is kept in `<NAME>.editor.json`. Objective and
rule progress is kept in save games (`game.state.editor`); the editor doesn't re-arm timers (survive
objectives, timed rules) after a save is loaded. With the regions there, the game sends the script
`game.EVENT.ENTERED` / `LEFT` (region name, player) when a player walks in or out.

Opening a shipped mission also reads what its DLL does (`level_base.json`, made by the same script; only hashes and
numbers - the texts come from your own game): its objectives (API +0x4B0, shown in Objectives… as *Campaign
Objective* - rename them or turn them off; the script renames by text key and hides them whenever the DLL shows them),
its briefing (API +0x4E0: title / date / place / pages - the Briefing designer starts from it), its score stream, and
the script ids it names directly (a shield in the outliner, *DLL Critical* in the inspector; deleting one warns -
hide it in a layer instead). Ids the DLL reaches through tables aren't seen.

## For tools and AI assistants

Every change is a named command in `editor/commands.py` (`POST /api/<command>` with JSON arguments; `GET /api/help`
lists them with their arguments). The UI uses nothing else, so a script or an MCP client can do anything the UI does.


## AI assistants (MCP)

`mcp_server.py` makes every editor command a tool for MCP clients (Claude Code, Claude Desktop, other assistants) -
the same command layer as the UI, with the same undo. With the editor running, the assistant edits the session you
have open and the page redraws by itself ("updated from outside the editor"):

```
claude mcp add desert-storm-editor -- python <ModKit>\editor\mcp_server.py
```

`--standalone --data <extracted game folder> [--game <game folder>]` gives the assistant its own session instead (no
editor window). Tools: open_level / open_mission, scene / find / get_record, set_field / move / place / duplicate /
delete / copy / paste, route_*, set_layer & co., scene_problems / fix_*, save_mission, get_script / save_script, play,
game_console, create_weapon / create_skin, walkability_grid, minimap_info / save_minimap, save_prefab / paste_prefab,
vehicle_crew / assign_crew, sound_info / voice_banks, undo / redo - `help` lists them with their arguments.
