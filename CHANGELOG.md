# Changelog

Mod kit versions follow the Blender add-on's version.

## 1.4.0

Goes with **DesertStormFix 3.0**. The level editor's missions and levels, `<table>.replace.txt` tables, `.strings`
texts, a `.skin`'s voice or normal map and cutscene audio in `Sound\SAS\` / `Sound\Delta\` need DesertStormFix 3.0 or
later.

### Blender add-on

**Exporting**

- **Into a Mod Folder** writes straight into `<game>\Mods\<Mod>`, with a starter `.weapon` / `.skin` for a new weapon
  or body; **Replace the Game's Model** saves under the original's name
- **Checks before every export** (too many vertices per texture, texture names, wrong scale, missing UVs, large
  textures or collision) and **Check Only** to see them without writing
- **Vertex colours** are exported (they were dropped, so models came out white)
- Mirrored objects (negative scale) no longer export inside out
- **Brand-new props without a game model to start from**: leave **Based On** empty (or pick **Auto / Generic
  Prop**) and the model is built on the game's small `BH_BODGEPLATE` prop, taken from your extracted folders or read
  straight out of your own game archives. It collides with its own mesh unless you add a `#BSP_` object
- **Export to Mod** (sidebar): one click re-exports the selection into the active mod with the last export's options,
  no file dialog. The mod and model name fill in after an export Into a Mod Folder, and both can be typed
- **Test in Game** (sidebar › Game): exports into the active mod, then starts `DesertStorm.exe`, straight into the
  level typed there (`Mission1.dll`, or a mod mission such as `Ambush.lua`; empty = the launcher). It refuses while
  the game is already running, because the game reads mods when it starts
- **Model check** (sidebar): vertices and triangles of the active mesh, export vertices per texture against the
  game's limit of 65535, images that aren't powers of two or are over 1024, collision triangles (keep under 3000)

**Collision, shadows and points**

- **Collision**: import shows what a model collides with as `#BSP_` objects with one material per surface (Blocking
  Concrete, Sand, Player Barrier …); export writes it back. Option **Collision**: from these objects, from the model's
  own mesh, keep the original's, or none
- **Shadow meshes** (`#SHA_`): edit or replace them; a static model without one can get one
- **Points** (`#PT_`): muzzle, launcher, shell ejection and ladder ends, imported as empties and exported from them
- **Desert Storm tab** in the 3D View's sidebar (N): assign collision surfaces to faces or objects, make a collision or
  shadow mesh from the active mesh (box, convex hull, copy), add points
- **Show Collision Surfaces** draws collision objects solid, one colour per surface; in Edit Mode an eyedropper picks
  the active face's surface

**Characters and animations**

- **Check Skin Weights**: keeps the 4 strongest bones per vertex and rescales the weights to add up to 1, selects the
  vertices that have no weight, and lists vertex groups that aren't bones of the armature
- **Footstep events as timeline markers**: importing a `.prb` turns its events into markers on the action (`FS_LEFT`
  / `FS_RIGHT` for a walk step, `FS_LEFT_25` / `FS_RIGHT_25` for a run step, `EVENT_2` / `EVENT_3` on stance
  animations), and the export writes the markers back, so you can move them or add them to a new cycle. An unchanged
  game animation exports with the same event data
- **Uniform preview** (sidebar › Soldier): shows the selected squad soldier in another uniform: the game's own (SAS,
  Delta, Iraqi, Russian) or the `.skin` uniforms of installed mods. A viewport preview only

**Weapons and vehicles**

- **Aim Camera**: add Rear Sight and Front Sight points and the camera sits on the rear sight, looking over the front
  one (or the muzzle point). The sight points stay in Blender and aren't exported
- **Snap to Hand**: puts the weapon in a soldier's right hand in the current pose to check the grip; press it again to
  put the weapon back
- **Rig Turret / Gun**: pivots where the game turns a vehicle's turret and gun (the gun rides on the turret, attached
  guns follow), with limits (turret all the way round, gun 20° up / 8° down), and sliders to turn them.
  **Reset Vehicle Pose** puts everything back before exporting (the export warns if you forget)

**Sound**

- **GameCube Audio** (File › Import, or the sidebar's Game box): better sound effects from your own GameCube disc
  (`.iso`, `.gcm` or `.ciso`). Samples the GameCube keeps at a higher rate are re-encoded for the PC into a
  `GameCubeAudio` mod, one sound cache per level, in the background with a progress bar (Esc cancels)

**Levels**

- **Editable Models (.blend)** on extraction: every model as its own Blender file, textured, with its collision,
  shadow mesh and points, ready to export back (**Include Level Pieces** adds the level geometry)
- **Scene import** (File › Import › Conflict: Desert Storm Scene (.env)): a whole level at its places: ground,
  buildings, props, kit pieces, soldiers and vehicles, lights, trigger regions (wireframe, named like the region) and
  ambient sounds (spheres showing where they fall silent). Models are imported once and placed as collection
  instances, so Mission 1 with its ground takes about 30 seconds. Also opens a level editor mission's `.envpatch`
- **Bake Minimap**: renders the level from straight above into the active mod as the in-game tactical map (`GW<Name>0.png`
  and its `GW<Name>.img` sheet, 256 × 256 like the game's own, or 512). The bounds are filled in for the shipped missions,
  and a mission script `<Name>.lua` in the mod is pointed at the new map
- **Generate Road**: with a curve active, makes a textured road along it plus its walkable collision (Tarmac, Baked
  Earth …). Generating again replaces the road
- **Generate Asset Library**: every extracted model as an editable `.blend` marked as an asset, sorted into the
  catalogs Vehicles, Weapons, Characters, Structures, Kit Pieces and Props, with the folder added as an asset library.
  Drag models from the Asset Browser into any scene

### Level editor (new, early)

- `editor/`: a world editor for missions that runs in your browser (`python editor/server.py --data <extracted
  folder>`). It shows a level's world and everything placed in it, and lets you move, copy, delete and change objects,
  props, AI routes, waypoints, trigger regions, cameras and sounds; move, turn, copy or delete the level's own ground,
  cliffs and buildings chunk by chunk (*Edit world*); place props and vehicles from a library;
  sculpt terrain patches, lay roads and place kit pieces; edit the mission's Lua script; and edit game tables and texts
  in a Records window. *Save as mission* writes a mod for the DesertStormFix plugin that holds only your changes, never
  game files; *Save as mod* replaces a level's scene for every mission (the whole edited scene and routes).
  Play starts the game straight into the mission. Can also be driven by an AI assistant through MCP (`editor/mcp_server.py`).
  See [editor/README.md](editor/README.md)
- Working safely, for people and assistants alike: `status`, `search` (paged, by distance), `references` (what uses a
  record before you delete it), `preview` (any change, rolled back), `batch` (many changes as one undo step, all or
  nothing), `set_fields`, `check_mission` (one issue list with fixes), `mission_report` / `revert_record` (changes
  against the base level), `screenshot` / `focus` (the 3D view as an image, or shown in the page), recovery copies of
  unsaved work every 5 minutes, an edit log, backups before every save; edits that change nothing leave no undo step.
  MCP: typed tool schemas, `--read-only`, image results

**Missions**

- **Save as mission**: a mission of your own on any campaign level, with a Lua script, layers, objectives and rules.
  Give it a campaign name and it shows up under SINGLE PLAYER › CUSTOM CAMPAIGNS. A mission made from a shipped level
  runs that level's own logic underneath (briefing, cutscenes, objectives, triggers), and your rules run after it
- **The mod holds only your work**: your scene as changes to the base level (`.envpatch`), route changes
  (`.rmdpatch`) and a list of the game files it borrows (`.uses`). DesertStormFix builds the full files from each
  player's own copy of the game, so a shared mission contains no game data
- **New level**: an empty level with the sky, fog, sounds and textures of a level you pick, a ground you can sculpt and
  the squad. The ground model is named after the level (`GROUND_<NAME>`), so two new levels no longer load each
  other's ground
- **Objectives** without code: kill or destroy objects, reach a region, survive for a while, or complete one from a
  rule. Shown or hidden at the start, in your order; the mission can succeed when all of them are done. On a shipped
  level, its own objectives can be renamed or switched off
- **How the base mission runs**: opening a campaign level shows what its mission script does, read from your own copy
  of the game, as readable steps (set-up, what it reacts to, its per-frame checks), plus the groups of objects it
  brings into play together. Select an object to see every step that names it

**Event rules**

- **When … Only if … Do …** rules without Lua: 19 triggers (mission start, timers, regions, killed, damaged, alerted,
  spotted, picked up / dropped, waypoint reached, path finished, vehicles, doors, switches, objective completed …),
  18 conditions (each can be negated) and 42 actions (bring objects into or out of play, layers, kill, health,
  teleport, AI orders, teams, vehicles, objectives, end the mission, messages, flags and counters, waits, doors, items,
  player control, fades, camera shake, birds, any game function, Lua)
- **Cutscenes from rules**: *Start a cutscene* (letterbox, camcorder viewfinder, player control off), *Camera shot*
  (a scene camera for some seconds), *Radio line* (a `.wav` from the mod plus a subtitle) and *End the cutscene*.
  New cameras are saved from the 3D view
- **Hunt the squad** (combat alert, then attack the player) and **Hold position** orders
- Waits in rules keep counting during cutscenes and carry on after a saved game is loaded
- Rules that start "when the mission starts" now run when play begins: after the intro film and the overview on levels
  made from a campaign mission (started earlier, the film's end wiped their orders)

**AI paths, regions and cameras**

- **Paths & Regions…** panel: click points on the ground for a path (a chain of waypoints, looped or not, with a wait
  at each point), pick soldiers, a pace (walk, run, crouched, crawl) and when they set off, and **Assign**. The same
  panel places trigger regions (box or sphere), single waypoints and cameras
- **Patrols walk in game**: the soldiers follow their path from the start of play (or when a rule says so), the way
  the shipped guard routes work
- **Keep walking while the player can't see them** (on by default): the game puts enemy soldiers the player hasn't
  seen yet to sleep, so a far-away patrol used to wait for the player. Patrols, *Walk a path*, *Kill* and *Hunt the
  squad* keep their soldiers awake
- **Walk-map checks**: path and waypoint points on ground the AI can't walk on are flagged, and **Snap points to
  walkable ground** moves them to the nearest open spot (`walk_check` for assistants). `check_mission` warns about
  patrol soldiers and route points on blocked ground

**Fixes**

- Copied, pasted or duplicated props (crates, barrels …) no longer break the records after them in the scene (one copied
  crate could turn the next one into a light). `check_mission` finds copies made by older versions and fixes them
  (*Fix these*, or *Play* does it by itself)
- *Kill* and objectives or rules waiting for a death now also work on enemy soldiers the player hasn't seen yet
- *Hunt the squad* works on soldiers that were told to *Hold position* before, and on hunters far away from the player

### Command line

- `ds_texture.py normal`: a normal map in the game's format (32-bit TGA) for a `.skin`'s `normal map`; `--opengl`
  flips the green channel of maps made in Blender
- `ds_sound.py wav … --accent delta`: a soldier's 47 voice lines from the Delta (American) actors instead of the SAS
- `ds_sound.py stream` / `psf`: a cutscene's or objective's sound as a stereo WAV and back, for a mod's `Sound\SAS\` /
  `Sound\Delta\` folder
- `ds_gc_audio.py`: the same GameCube sound conversion as the add-on's, from the command line (`--list` shows what
  would change)

### Library (`conflict_ds_tools`)

- New modules for the level tools: `env` (level scenes, lossless), `levelscene` (a scene as things to place), `routes`
  (AI route files), `terrain`, `roads`, `kits` (level-piece kits), `gamerefs` (mods that reference game files instead of
  copying them), `records` (game tables), `minimap`, `assetlib`, `blank` (new empty levels), `gamecube` (GameCube disc images),
  `gcaudio` (the GameCube sound conversion)
- `sound`: cutscene / objective streams (decode and encode), WAV resampling, voice-line languages

### Modding guide and examples

- New guide sections: 4.5c a uniform with its own voice, 4.5d cutscene and objective audio, 4.16b changing a game
  weapon itself, 4.22 changing or deleting game table rows (`<table>.replace.txt`), 4.23 changing the game's texts and
  adding your own (`.strings`), the Blender sidebar tools and exporter options, and 7 missions and levels (the editor,
  custom campaigns, `+level`, and a reference of the Lua functions for mission scripts)
- New examples: **RadioSAS** (a uniform with its own voice: the Delta actor's lines over a crackly radio),
  **RadioCutscene** (Mission 1's opening cutscene with the speech over a radio) and **QuiltedSAS** (a padded jacket with
  its own normal map)

## 1.2.0

- First release with export: models, character bodies, textures (`.dds`) and animations (`.prb`) back to the game's
  formats, sound and texture tools, the modding guide and example mods
