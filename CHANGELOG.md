# Changelog

Mod kit versions follow the Blender add-on's version.

## Unreleased

### Editor

- **Library by source**: the library lists the open game's shipped levels, (Desert Storm) the converted libraries of
  the other games (`PSE\Library\<game>`: DS2 / TGE / Vietnam / Global Storm props, soldier types and vehicles) and every
  installed mod - a filter chip and a collapsible section per source, and on every item a badge (game / converted /
  mod) and where it comes from. Converted and mod items place like the game's own; saving copies what they need into
  your mod (a soldier type's / vehicle's definition, a mod's model and textures).
- **Denied Ops: every prop shows** - its scene types differ from the other games' (5 / 7 = physics props and
  destructible walls, 9 = props; only 3 is a zone): 484 of Comms Base's props were never drawn, listed or copyable.
- **Denied Ops: placing and pasting works** (was "this scene has no ENDI"): a prop / object goes in as a whole block
  in its section, with the section's count and the parent link kept right.
- Fixed: opening a DS2 / Vietnam / Global Storm level failed (`scriptinfo has no attribute load`); the library failed
  on other games' vehicles (`kit_count`); after switching games the library could show the previous game's items.

### Blender add-on

- **Into a Mod Folder for The Great Escape, Vietnam and Global Storm**: their plugins serve `<game>\Mods` by file
  name, so the export (and the sidebar's Export to Mod / Test in Game) writes there instead of refusing. Test in Game
  starts those games at their launcher / front end (only Desert Storm has `+level`).
- **Vietnam / Global Storm scene lights and sounds import**: suns, point and spot lights and ambient sounds come in
  with a scene (every record of every level: Vietnam 1009 lights / 347 sounds, Global Storm 1555 / 328); a light moved
  or turned in Blender is written back by the scene export. Before, both games' scenes came in without lights and
  Global Storm's without sounds.
- **Vietnam / Global Storm characters with several parts**: a body with several materials exports as one skin
  object per material (the extra parts were dropped before). Both games draw them - checked in game with a body
  split into two textured halves.
- **Asset library for every game**: the catalogs are named after the game (Desert Storm keeps its catalogs);
  Vietnam / Global Storm level pieces (their type 3) are Kit Pieces; Global Storm's weapons / vehicles are found
  (its tables come out of the extraction as `_<hash>.BIN` and VEHICLE.TXT is space-separated); Denied Ops, whose
  tables are XML, sorts weapons and vehicles by name. Checked on all six games.
- **Denied Ops cloth and shatter models import** (Blender, editor 3D view): flags, tank tracks and the bird with their
  animation frames, breakable glass / signs - before they were refused.
- **Denied Ops writer** (kit, no Blender / editor button yet): `conflict_ds_tools/evo54.py` reads and writes Denied
  Ops model streams (EVO 0x54, every kind incl. skinned, cloth / tracks and shatter models) and `apkg.Package` whole
  asset packages, byte for byte on the whole game (53 / 53 packages, 8113 / 8113 models - `scripts/check_do_packages.py`);
  `apkg.replace_asset` swaps one model inside a package and keeps the package's pool sizes right.
- **Denied Ops packages are named**: extraction writes `<NAME>.APAK` (the name in the package's own manifest - how the
  game opens them, `Packages\<NAME>.apak`) instead of `_<HASH>.BIN`; older extractions still unpack.
- **Extraction names the catalog tables** (WEAPS.TXT, INVEN.TXT ...) even where the game's exe doesn't show their
  names (Global Storm).
- **Vietnam / Global Storm shadow meshes**: a `#SHA_` object exported with a static model that has none adds one,
  laid out like the shipped ones (checked against all 107).
- **Starter `.weapon` for Desert Storm II and The Great Escape**: a new weapon model exported into a mod gets one, as
  in Desert Storm - both plugins read `.weapon` files now.

## 1.9.2 – 2026-10-27

### Blender add-on

- **Global Storm animations are .ani on both sides**: File > Import says *Animation (.ani)* and its file browser
  shows `.ani` files (export already wrote `.ani`; the import entry said `.prb` and hid `.ani` files). The Global
  Storm add-on's buttons read *Import / Export ANI Animation*; the export no longer turns a typed `<name>.ani` into
  `<name>.ani.prb` in the every-game add-on.
- **Extraction names Global Storm animations**: `<NAME>.ANI` by the name inside instead of `_<HASH>.BIN` (the archive
  index has no name for them). Old extractions with `_<HASH>.BIN` still work.

## 1.9.1 – 2026-10-26

### Mod folders

- **New mods are organised**: the Blender add-ons, the editor (Save as mod / mission, records, asset forms, roads,
  terrain, minimap), the porter, GameCube audio and the example / pack builders write a new mod's files into folders
  by type - `evos`, `dds`, `anims`, `sounds`, `tables`, `defs`, `levels` (`campaign.txt`, `records\`, `frames\` stay
  in the top folder). Flat mods (every file in the mod's folder) keep working and stay flat; a file a mod has is
  rewritten where it is. `conflict_ds_tools/modlayout.py`; Blender preference **New Mods**, editor `--mod-layout` /
  `set_mod_layout`, builders `--layout organised|flat`. The games find a mod's files by name in any folder.

### Blender add-on

- **One add-on per game**: `conflict_ds1_tools`, `conflict_ds2_tools`, `conflict_cv_tools`, `conflict_cg_tools`,
  `conflict_ge_tools` - each with only its game's File menu entries, preferences and sidebar tools, the sidebar tab
  named after the game (GameCube audio and porting from the other games in the Desert Storm one). The every-game
  add-on `conflict_ds_tools` is still built, unchanged. All six install side by side: a game add-on's operators,
  panel and Scene properties have its own prefix (`import_scene.cds1_evo`, `Scene.cds1_surface` ...).
  `scripts/build_zip.py` builds all seven (`--game ds1` ... for some); the release zip carries all of them.
- **Denied Ops in Blender** (`conflict_do_tools`, import only): models out of the level archives' asset packages
  (extract with File > Import > Conflict: Denied Ops Archives - the packages are unpacked into `<MODEL>.EVO` /
  `.DDS` on the way), textured through their materials; characters on `C5_SKELETON`; level scenes (Comms Base: 2788
  objects). Cloth (birds, tank tracks, flags) and breakable shatter models are refused with a message - the shatter
  ones used to stop with a bare "unexpected end of file". Nothing is exported yet (models go back inside APKG
  packages; not built), so the add-on has no export entries or export tools.

## 1.9.0 – 2026-10-24

### Exporter

- **Skins made of many separate pieces export again** instead of stopping with "the skin's triangle strip would be
  longer than its list … join them". The game's skin loader sizes the strip buffer by the list, so such a strip did
  overrun it; the exporter now writes that skin without a strip, and the game draws its triangle list (both skin
  draw paths handle it). The porter no longer leaves small skin pieces out for this reason either.

### Porting between games

- **Asset library** (`cli/ds_build_library.py`, `conflict_ds_tools/library.py`): every model, character, vehicle,
  weapon, soldier type, animation, texture and sound bank of Desert Storm II, The Great Escape, Vietnam and Global Storm
  converted for Desert Storm into `<Desert Storm>\PSE\Library\<game>`, every file named with the game's prefix
  (`DS2_`, `GE_`, `CV_`, `CG_`); `library.txt` says what each file is, `not-converted.txt` what didn't convert and why.
  Names come from every archive file's contents (96 % of Desert Storm II's entries, every model of every game). Sound
  banks come from the loose level caches as bank packs, renumbered into a range per game; samples no bank uses go in
  one pack per cache; `.psf` streams copied. Collision-only models (invisible blockers) come over as blockers
  (`port_model(collision_only=True)`); aircraft and other non-drivable vehicles as model-only vehicles. Resumable,
  built in a staging folder and swapped in. DesertStormFix reads the library only when a mod names a file in it.
  Running it again updates an existing library in place; `--redo animation,weapon,...` retries kinds whose converter
  changed.
- Sound: Global Storm's and Denied Ops' caches (an `HDRSND` header in front of the cache) are read; Vietnam's (28 more
  bytes per sample entry) and Global Storm's (48) banks convert to Desert Storm's layout - the extra size is found per
  bank (`sound.bank_to_ds1`).
- Weapons: Global Storm's `WEAPS.TXT` (model in column 3, type in column 8) ports; weapon types Desert Storm hasn't got
  or keeps only one of get the closest type's numbers (Desert Storm II's M16A2 + M203 started from the smoke grenade;
  now an assault rifle).
- Animations: Vietnam's upper-body layer animations (`*_UB`) come over whole-body with the legs in the rest pose;
  Global Storm's `.ani` animations go into the library.
- **Maps for modders** (`cli/ds_convert_maps.py`, `levelport.convert_maps`): Desert Storm II's ten mission maps as
  empty Desert Storm levels (scenery, collision, lights, routes, squad start - no enemies, vehicles or mission logic)
  plus the models, weapons, vehicles and soldier types they use, into `<Desert Storm>\PSE\Ports\ds2`. Replaces the
  playable converted campaign: its mission scripts kept failing on behaviour differences. The script adapter
  (`crossmission.py`, `cli/ds_convert_campaign.py`) stays for development and the Desert Storm II editor.
- Converted levels: where a zone has no collision at all (Desert Storm II leaves the hillsides beside its vehicle
  paths without any - its drivers stay on the road), its "render only" polygons (the u 1 tree neither game loads)
  become collision. Desert Storm's AI drivers swing wide and reverse at bends: the squad Land Rover in DS2 Mission 1
  backed into a hillside and fell through the world (mission failed). Wherever Desert Storm II has collision it is
  kept as it is - adding the detailed render ground on top of its smoothed collision made vehicles shudder.
- **Porter** (`conflict_ds_tools/porting.py`, `cli/ds_port.py`, Blender File › Import › Conflict: Port Between Games; plan: docs/PORTING_PLAN.md): a recipe (`.port`
  text file) names models and animations of Desert Storm II, The Great Escape, Vietnam or Global Storm; the porter
  converts them on your PC from your own copy of that game into a Desert Storm mod folder with a report. Models land
  on DS1's generic prop with their collision (surfaces mapped), textures and muzzle / eject points; animations are
  retargeted onto Bradley; a placeholder texture the source game swaps at run time (Global Storm cars) is replaced by
  one named after the model. Checked offline on a prop, a weapon and a truck plus a walk from each game, and in game: a
  truck from each game, ported in place of Mission 1's T-62 wreck, shows up textured and whole: the wheels, doors and
  guns the source game attaches at run time are merged in (from the `.DYN`, Vietnam's vehicle physics files, or Global
  Storm's own static versions).
- Porter recipes take `weapon NAME like DS1_WEAPON_ID [as NEW]`: the gun model plus a starter `.weapon` file - a new
  weapon on the loadout screen that handles like the named Desert Storm weapon. Guns are turned onto Desert Storm's
  hand connector (Vietnam / Global Storm guns lay across the hand otherwise); checked in game with each game's rifle in
  Bradley's hands. Each gets a HUD / inventory picture rendered from its model (side on, its own colours, outlined,
  the size of the base weapon's picture) - seen in the game's HUD. Vietnam / Global Storm guns get the magazine the
  source game hangs on them at run time (found through its weapon and ammo tables). Shadow meshes come over too.
- Porter recipes take `character NAME [for Bradley|Foley|Connors|Jones]`: another game's soldier fitted to that squad
  member's skeleton plus a starter `.skin` - a new uniform on the loadout screen that wears this body. Checked in game:
  Bradley as the Desert Storm II Iraqi soldier, the Vietnam US soldier and the Global Storm tiger-stripe soldier.
  (The character conversion the Blender add-on uses moved to `conflict_ds_tools/crossgame.py`, shared by both.)
- Ported Desert Storm II vehicles bring their stats (armour, speeds: VStat columns that mean the same in both games,
  as `.vehicle` `stat column N` lines) and their guns are paired one by one with the base vehicle's - a gun the source
  doesn't have is left off (`gun = X -> -`), two base guns on one connector get a connector each. Checked in game:
  DS2's GAZ69 and Land Rover MK19 / SAW driven in Mission 8.
- Porter recipes take `level TRAINING03 [on Mission3] [as NAME]`: a Desert Storm II level as a Desert Storm `.lua`
  level - its zones (every world piece with its collision), props, portals, the squad's start and its fog / sky colours
  / time of day, on a Desert Storm level's archive for soldiers, weapons and sounds. First milestone: Training 3 loads
  and plays; its soldiers, weapons, items and vehicles come along where Desert Storm has them (others as scenery),
  with their waypoint routes, trigger regions and the AI's hand-placed route markers. Checked with Training 3 and
  Mission 1.
- Ported levels use Desert Storm's own models where it has the same ones (training-camp buildings, fences, common
  props), and bring their sky, ambient sounds, breakable props and loading picture.
- Ported levels bring their doors (new door kinds added to DOORS.TXT), lights, cameras, the ground's detail textures
  (the level's render effects - without them the ground showed smeared lines) and the sound banks their soldiers,
  vehicles and items need.
- Ported models: invisible collision-only meshes (camera blockers) are no longer drawn as smeared surfaces.
- Ported props: textures with transparency are drawn as such (DS1 needs the mesh flag DS2 doesn't set), and props
  are no longer tinted dark blue (the generic prop's material).
- Every model the kit writes for Desert Storm (Blender export, porter): textures that repeat across a mesh are no
  longer clamped to their edge (the two flags after each vertex buffer are now set by the game's own rule) - a
  chain-link fence's wire disappeared, other tiled textures smeared.
- Ported levels bring their animated props (flags, washing lines, windsocks: the scene's VAMI / ANMI records, all
  frames, on Desert Storm's own animated prop) and their own map picture with the bounds the source mission gives it
  (read from its script DLL) - the loadout screen's MISSION tab shows the source level's map with the squad on it.
  Checked in game: Training 3's flags flap; Desert Storm II's Mission 1 and 2 maps.
- **Mission logic across games, first step** (`conflict_ds_tools/crossmission.py`, porting plan Part 4): a Desert
  Storm II mission DLL, translated by `mission2lua` (stage 1), runs in Desert Storm on its ported level - its
  LevelSetup gets a script API table in Desert Storm II's layout whose slots call Desert Storm's own functions (323 of
  the 330 slots Desert Storm II's missions use have a counterpart) or Lua; each slot logs its first use. Checked in
  game: Desert Storm II's Training 3 runs (set-up, regions, paths, AI states, timers - 48 slots, 8 still unmapped).
  The translators learnt Desert Storm II's compiler: `pushfd` / `popfd` (its CRT's CPU check - the x87 paths are
  taken), the stack-cookie check and its `sprintf` (readable translator; register-argument functions still stop it).
  The mission also gets its own message ids (Desert Storm II renumbered part of the list; ids without a known
  counterpart aren't delivered), its level's sound banks (converted to Desert Storm's bank layout) and its texts.
  Calls on objects the level hasn't got are skipped (logged) instead of crashing. Desert Storm II's Mission 1 runs too.
- **Converted DS2 missions, round 2**: every script-API slot the ten missions use is identified (86 more, DS2 catalogue
  now 429 entries, about a dozen old guesses corrected) and called on Desert Storm's own function or a small Lua body;
  float results land on the x87 stack (a missing one stopped Mission 3's set-up); DS2's AI states 3+ are Desert
  Storm's +1; Desert Storm's "play starts" message is DS2's 0x13 (passed as 0x12 it was DS2's "mission failed" - Mission
  8 failed at once). **Objectives** come from the level's `.SI` (`conflict_ds_tools/scriptinfo.py`): shown through
  Desert Storm's objective list, checked every frame (targets destroyed, squad down, units in regions, composites) with
  DS2's results (complete, fail after a delay, activate / deactivate another), the script's objective calls work on
  them; DS2's shared texts Desert Storm lacks come along (`DS2_CATALOG.strings`). **Spawn points** and damage regions
  from the `.SI` run on Desert Storm's spawn system (dormant points, enable / disable regions, the point's own units
  respawned). Each mission's map is its own (DS2's names hit Desert Storm's maps of other missions - Mission 5 crashed).
  Level port: weapons are ported before the scene (kits keep DS2-only weapons, ammo pickups load them), bases by weapon
  type (not Desert Storm's one-of-a-kind grenade / launcher / fist), DS2's space-separated VEHICLE.TXT is read (Mission
  7's SCUD launchers are vehicles on Desert Storm's SCUD), static props written as plain records, breakables' shard
  models ported. All ten missions start and play in the smoke test. Soldier gear stays off (Desert Storm only hangs
  attachments from its built-in list - ported ones crashed); objective states aren't saved yet; areas not carried over.
- **Converted campaigns** (`cli/ds_convert_campaign.py ds2 [--levels MISSION01 MISSION02]`,
  `crossmission.convert_campaign`): Desert Storm II's campaign converted from your own copy into
  `<Desert Storm>\PSE\Ports\ds2` - DesertStormFix lists it under SINGLE PLAYER > CAMPAIGNS (also in co-op) next to
  DESERT STORM 1, missions chain to the next. Mission texts go to their own section (`[DS2_M01]`), so Desert Storm's
  Mission 1 keeps its texts and the intro's movie texts show (they were "MISSING MOVIE TEXT"). Checked in game: Mission 1
  completed -> end screens -> Mission 2 plays. A level the source game has no loading picture for keeps the source's
  name and shows none (it got Desert Storm's Mission 3 picture name, missing there too).
- Ported levels port every object kind Desert Storm hasn't got instead of leaving it as scenery, each under the
  source's own name so the source's script finds it: vehicles (drivable with the source's model and guns on the
  closest Desert Storm vehicle's handling; AA guns and aircraft on the base's own model), soldier types, weapons
  (model, numbers, shot sound, HUD picture), destructible structures, features and pop-up targets (their table rows,
  models and stats). Desert Storm's own pop-up targets are used again where it has them.
- Ported vehicles with a long name no longer lose their body to their own gun (part names were cut after the suffix).
- Ported levels: the squad gets the source squad's script ids (the source's mission logic names its soldiers by
  them), Connors comes along (his unit is called Ramirez - new editor levels lost him too), and soldiers the source
  places out of play start in play and are deactivated by the script (Desert Storm crashes on an out-of-play
  soldier's first AI turn).
- Ported levels use Desert Storm's model of the same name only when it is the same model: Desert Storm II reworked many
  shared training-camp models (its chain-link fences have straight posts, Desert Storm's barbed-wire tops).
- Porter recipes take `texts M01` / `texts GE_LEVEL01` / `texts catalog`: another game's mission or shared texts as
  a `.strings` file - a mission's in its own section for a ported level (`game.load_strings("DS2_M01Strs.loc")`), the
  shared ones only where Desert Storm has no text of that key. Checked in game with Desert Storm II's Mission 1.
- Porter recipes take `unit NAME like UNIT_ID [as NEW]`: another game's soldier as a new soldier type for missions -
  fitted to that Desert Storm unit's own body, with a starter `.unit` based on the unit (kit, skill, voice). Checked in
  game: Vietnam, Global Storm and Desert Storm II soldiers placed in Mission 1 as Iraqi conscripts.
- Characters converted between games (porter and Blender export) keep the source's trunk proportions: the games' Biped
  spines are split at different heights, and moving each vertex with its own spine bone pulled the chest down while
  the head stayed put - the Vietnam soldier had a long neck in Desert Storm. Pelvis, spine, neck and head now take
  the target's rotations at joints spaced like the source's; arms and legs still follow the target's joints exactly.
- Forearm twist bones convert onto the forearm: Vietnam's Biped hangs its ForeTwist bone (most of the forearm's
  weight) off the upper arm, and Desert Storm's skeleton has none, so the forearm skin went to the upper arm -
  flattened forearms and hands bent back whenever an elbow bent. Twist bones (ForeTwist, UpArmTwist, ThighTwist,
  CalfTwist) now go to the bone they twist.
- Global Storm keeps heads apart from bodies (C04_500_POLY + C04_500_POLY_HEAD / C04_RIFLEMAN_HEAD ...): a ported
  Global Storm soldier came out headless. `character` / `unit` lines take `with <HEAD>` (`with -` = none); without it
  the porter puts on <NAME>_HEAD when the source has one, else the report lists the heads to choose from.
- Hats and face gear fit ported heads: Desert Storm hangs berets, helmets, glasses and cigars on the body's HAT / HEAD
  / FACE connectors, placed for its own heads - on a taller head (Vietnam's Motown, +3 cm) the beret floated, on a
  shorter one it sank in. The porter moves those connectors by how much the new head's top and front differ
  (`crossgame.head_connector_offsets`, `evo_write.rebuild(key_offsets=...)`), and says so in the report.
- Global Storm vehicles without a static version port whole: wheels, tracks, doors from the game's `.PHY` physics
  files (road wheels placed inside their track), turrets on their mounts (Global Storm models with more keys than
  objects - C04_BMP1: 27 for 24 - had every connector at the origin, turrets inside the hull; now read by key index),
  guns from its vehicle weapon tables (the vehicle found by name: C04_ARJUN -> TR_VEH_ARJUN_TANK, gun models on their
  CONNECTOR_WEAPONnn). Checked in Blender on the Arjun, BMP-1, MT-LB, Land Rover MK19, pickup.
- Vietnam vehicles get their guns too (its weapon rows have Desert Storm's layout: model in column 1, connector in 6):
  M48 cannon + .50 cal, T-34 cannon + DShK, Ontos recoilless rifles + .30 cal, patrol boat guns, APC / jeep MGs.
- Drivable vehicles: recipe line `vehicle NAME like DS1_VEHICLE_ID [as NEW]` (wheeled so far). The source body goes
  onto the base vehicle's model (its hit boxes, collision helper, seats; the base's own gun carriage removed), the
  base's seat / exit / gun-mount connectors move to the source's own (matched by name: DRIVER01, PASSENGER01,
  EXIT_DRIVER, MOUNT01, WEAPON01 ...), the source wheels become wheel models on a .DYN fitted from the base's
  (positions, radius, wheelbase, track), plus a starter `.vehicle` (`model =`, `based on =`: handling, guns, sounds
  from the base). Checked in game: Global Storm's pickup as Mission 8's squad vehicle (loadout swap) - built with its
  4 seats, crewed, driven 66 m in 5 s, the .50 cal on the roll bar; Blender with the .DYN wheels in the arches.
  Tracked vehicles too: evo_write writes kind-1 (per-frame animated) meshes now (`Part.frames`, VAOB / VLST as the
  game's T62_TRACKLEFT - round trip identical), the source's track meshes (all frames) become the .DYN's two track
  models placed where its physics file puts them, wheel lines are matched by side and place along the hull
  (sprocket / road wheel / idler), CONNECTOR_TURRET counts as the base's gun mount. In game: Global Storm's Arjun on
  the M1 Abrams, driven 25 m in Mission 8 with its turret and the base's cannon turning; in Mission 9 it behaves
  exactly like the game's own Abrams (the mission's script destroys the squad vehicle early on - both ways).
- Drivable ports keep a turning turret and the guns in the right places: guns are matched by what they are (the
  base's VEHWPNS -> WEAPS category and the source's weapon tables - the Abrams has its cannon on WEAPON02 and a .50
  cal on WEAPON01, the Arjun the other way round; matched by number the .50 cal sat on a post where the cannon
  goes), a gun mount follows the matched gun's own mount, and the source's turret (its objects on the matched
  mount) goes into the base's turret object instead of the hull, with its collision and shadow. In game: the Arjun's
  turret turns with the cannon in Mission 8.
- Drivable ports carry the source vehicle's own guns: each gun goes onto the base gun's model (its muzzle point kept
  unless the gun has its own), turned from the source connector's axes to the base's, with a `.weapon` based on the
  base's gun (fires and sounds the same) and a `gun = <base gun> -> <new>` line in the `.vehicle` (DesertStormFix
  3.3+). In game: the Arjun in Mission 8 with its own cannon and NSV machine gun. Vietnam vehicles' in-place tracks
  now get both sides (the M48 had one).
- Ported Desert Storm II guns bring their own numbers, shot sound and name: the source weapon (its Weaps.txt row
  drawn with the model) is read in Desert Storm's column layout (Desert Storm II has 4 more columns from 44 on);
  a column is copied when it means the same in both games - the same value for 90 % of the 73 weapons both have,
  plus the re-tuned fire interval, magazine size and damage - and the others stay the base's (named in the report:
  Desert Storm II's range column is 100 everywhere). Its shot bank comes from Desert Storm II's level sound caches as
  a pack `<MODEL>_SND.sch` (bank `DS2_<BANK>`, samples under ids of their own so they don't clash with Desert
  Storm's - `sound.renumber`; Desert Storm II's bank entries carry 20 more bytes each, dropped by `sound.bank_to_ds1` - an
  unconverted bank made Desert Storm call the level cache corrupt), its name from Desert Storm II's texts ("AKSU-74 Suppressed Sub-Machine Gun"). A
  different weapon type (the flare gun) is reported: Desert Storm's type decides how it fires.
- Great Escape vehicles port drivable: the Panzer III (on the Abrams) with its turret, cannon and MG34, the Hanomag
  (on the Bradley) with its MG42 and tracks, the Kubelwagen (on the Humvee). Tracks listed in a Desert Storm II /
  Great Escape `.DYN` come along (they were only taken from Vietnam / Global Storm physics files); guns and their
  kinds come from those games' Vehicle -> VehWpns -> Weaps tables too; connector names with a model tag
  (`CONNECTOR_P3J_DRIVER`, `CONNECTOR_P3J_MG_OPERATOR`) match Desert Storm's seats; a base gun the source has no gun for
  is left off (`gun = <base gun> -> -`, DesertStormFix 3.3+) instead of hanging on the new body.
- Drivable ports keep to Desert Storm's 18 wheel slots per vehicle: the Panzer III's 22 wheels overwrote the memory
  after them (crash while the level loaded); wheels over 18 - the highest, a tank's return rollers - are built into
  the body. In game (Mission 8): Panzer III and Hanomag crewed and driving with their own guns. On the Abrams base a
  second gun sits on the commander's mount, which turns on its own - the Panzer's coaxial MG34 swings independently.
- Placeholder textures: the "32 px or less" rule is Global Storm's only - The Great Escape's real small textures
  (steering wheel, track links) were swapped for others.
- Ported Great Escape guns bring their numbers, shot sound and name too. Its Weaps.txt is laid out differently (one
  column fewer in front, 11 animation columns where Desert Storm has 6) and shares only the fist with Desert Storm,
  so nothing can be compared: only fire interval, magazines, magazine size, reload time, damage and hearing range
  are copied (Luger: 12 magazines of 8, damage 11; MP40: 32 rounds, 0.18 s); the rest stays the base weapon's, said
  so in the report. Its 671 sound banks convert like Desert Storm II's. In game: the MP40 in Bradley's hands with its
  Great Escape shot bank, no cache warning.
- Ported soldier types (`unit` lines) carry the source unit's own values: type, experience, health and the other
  base stats that mean the same in both games (Desert Storm II: HStat columns compared over the 23 rows both have;
  The Great Escape: health only, nothing to compare), its kit where Desert Storm has the items, and its voice - its
  speech bank as a pack (the games share the speech event ids: Desert Storm II's Bradley bank has all 46 of Desert
  Storm's and 3 more). A Great Escape unit wears its own skin (Units.txt column 12 -> `<skin>_<skin without its
  number>`, Feldgrau01_Feldgrau) instead of the model's first look. In game (Mission 1): the Great Escape guard
  (health 40, field-grey uniform, his GermanGuard voice) and Desert Storm II's Delta soldier (health 350, M16A2,
  US_Soldier voice) - each soldier's speech bank checked in memory.
- The porter's file finder looked for models when asked for a texture (the default `evo.AssetFinder` asks for
  textures): skins named only by the archives' name hashes weren't found.
- Model writer: an object whose new collision is empty says NOBSP and keeps its `#BSP_` helper's mesh - emptying the
  helper crashed Desert Storm's model loader (a ported gun without collision on the Abrams cannon's model).
- Blender: a Global Storm soldier exports into another game with his separate head - import the body and the head
  (e.g. C04_MILITARY_UNIFORM + C04_HEAVY_HEAD), select both: each piece is re-posed from its own model's rest pose,
  the body's own eyes / teeth are left out (the head has its own), same result as the porter. Exporting a Global Storm
  body alone says how to add the head.
- Vehicle parts from physics files go where the file's Position says - the Offset is a physics setting, not drawn:
  checked against the game's own merged C04_MTLB_STATIC (track 0.0 cm, doors 0.9 cm off; 10 / 18 cm with the Offset).
  The Arjun's tracks had hung below its wheels; Vietnam's APC / M48 tracks (modelled in place) fit the same rule.
- Placeholder vehicle textures pick the right paint: names compared without underscores and by word prefixes
  (C04_MTLBANDTURRET -> C04_V_MT_LB_GREEN), wheel / track / turret placeholders take a texture of their kind
  (C04_Wheel_Type_L_S -> C04_V_MTLB_WHEELS_GREEN; were magenta), the game's C04_V_ paints first.
- The report flags low-detail source bodies (Global Storm's C04_500_POLY / C04_160_POLY, Vietnam's MPD_500_POLY: the
  games' distance versions, a few hundred triangles) and points to the full models.

### Blender add-on: scene export

- **File › Export › (game) Scene (.env)** for Desert Storm, The Great Escape, Desert Storm II, Vietnam and Global
  Storm (`conflict_ds_tools/sceneexport.py`): writes an imported level scene back with the changes made in Blender
  and leaves every other line as it was (no changes = the original byte for byte, checked on one scene per game).
  Props and objects moved / turned / scaled, Shift+D copies (objects get the next script id and a unique editor ID
  `NAME_2`), deleted ones removed, model instances added from the level's models collection, new meshes written as
  models (`.evo` + their new textures as `.dds`) next to the scene and placed as props, lights / regions / ambient
  sounds moved, suns and spots turned. A record moved more than 5 m, a copy and a new prop take the zone of the
  nearest placed record. Global Storm writes its split scene parts. Checked in Blender for all five games (import,
  edit, export, read back) and in Desert Storm: Mission 1 from a mod folder with four new textured boxes and a copied
  barrel around Bradley's start - all drawn, the copy in play, the octree rebuilt for the edited scene.
- Generic prop templates for Vietnam (`MPD_INVISBLOCKING3M`) and Global Storm (`INVISPOLY`): **Based On › Auto /
  Generic Prop** works for every game now (both are one untextured object with its own mesh as collision, like
  Desert Storm's `BH_BODGEPLATE`).
- Scene import stores the lines it brought in (`ds_lines` on the level collection), so the export can tell a deleted
  object from a group that wasn't imported. Scenes imported by an older add-on export without deletions (a note says
  so).

### Blender add-on: more games

- **Models between games** (Desert Storm <-> Desert Storm II; the same path will serve the others): a prop, weapon,
  building or vehicle imported from one game exports into the other - pick the target game's File > Export entry.
  It lands on that game's generic prop (or, with Based On, on one of its models - e.g. to replace it in the game):
  one static model with the source's render parts (connectors / hit-region markers left out), its collision (surfaces
  the target lacks become the nearest it has: Gravel -> Baked Earth, Snow -> Sand, Plastic -> Wood ...), points and
  shadow mesh. The source game's textures are saved into the mod renamed `DS2_<name>` / `DS1_<name>`, so they never
  replace one of the target game's textures. Checked in Blender: a DS2 trolley and the 19-part GAZ69B into DS1 and
  the DS1 SAW into DS2 keep every triangle (same area and bounds) and the muzzle point; in Desert Storm, the DS2 GAZ69B
  exported onto Mission 1's T-62 wreck loads and shows in its place.
- **Characters between games** (Desert Storm <-> Desert Storm II): a character imported from one game exports onto
  the other's generic body (DS1 Bradley's HERO01_ARMSTRONG, DS2's 534_POLY_SOLDIER) or a body named in Based On. The
  games share their Biped bone names, so the skin is moved from the source skeleton's rest pose onto the target's,
  bone by bone (every vertex keeps its place on its bones); DS2's eyes, eyelid and teeth (rigid parts on face bones
  DS1 doesn't have) go onto the head; the body keeps the target's hit regions. Checked in Blender both ways: every
  triangle, every single-bone vertex in place, the target game's walk plays with the feet on the ground; in Desert
  Storm, DS2's IRAQI_SOLDIER_01 on Mission 1's conscript body renders with its own texture and animates.
- **Animations between games**: importing another game's animation onto a character retargets it (that game's
  soldier skeleton is found next to the animation file): every skeleton bone takes the source bone's world pose by
  name, connectors keep the body's own offsets; export it through the character's game as usual. Desert Storm <->
  Desert Storm II checked in Blender: bones exactly where the source animation puts them, written files pose the
  soldier to 0.0001 cm, footstep events kept. The Great Escape's, Vietnam's and Global Storm's animations retarget the
  same way (checked offline on a walk of each onto the DS1 soldier: 21-24 bones matched by name, exact, feet on the
  ground); writing animations for those three games themselves comes later.

- **File › Import** has entries for **The Great Escape**, **Conflict: Vietnam** and **Conflict: Global Storm** next to
  the two Desert Storms: models (props, weapons, vehicles, characters with their skeleton), level scenes and archive
  extraction; animations for The Great Escape. Each game keeps its own game folder, extracted folder and mod in the
  add-on preferences. Checked on every model of the three games (2413 / 4457 / 6916 read) and in Blender 4.2.
- **The Great Escape: export** of models (weapons, props, vehicle parts, characters incl. multi-part skins, collision
  with surfaces, shadow meshes, points; Auto / Generic Prop uses the game's `104` quad) and of **animations** (`.prb`).
  Checked on all 2413 models (`scripts/check_models.py --game ge`: unchanged rebuilds byte for byte, parts, skins,
  collision trees, points, shadows) and all 393 animations (rewritten byte for byte). There's no Great Escape plugin
  yet, so **Into a Mod Folder** is refused for it - export to a file.
- **Vietnam animations** import (File › Import › Conflict: Vietnam Animation, and All Compatible on the character
  import): all 409 read (257 fit the soldier body; the rest are upper-body layers, animals, vehicles). Their keys are
  rotations *relative to the bone's rest pose* - 12-byte rotation vectors, or 28-byte rotation + position where a track
  has flag 4 - checked on walk cycles in Blender: the lower foot stays on the ground (13-17 cm) in every frame.
- **Vietnam / Global Storm export** (static models: weapons, props, vehicle parts, level pieces): new geometry per
  part, collision with surfaces (Vietnam 65 / Global Storm 76 rows), points (muzzle ...). Their object layout was
  traced in the games' loaders and checked on every shipped file; unchanged rebuilds are byte for byte on all 4456 /
  6916 models, part rebuilds and collision trees on all of them (`scripts/check_models.py --game cv|cg`).
- **Vietnam / Global Storm characters** export: the body is written skinned to its skeleton file (MPD_SKELETON /
  C04_SKELETON[_FEMALE] next to the model, or fetched from the game's archives for the generic bodies). Every shipped
  body rebuilds in place (Vietnam 13 / 13, Global Storm 28 / 29 - the last only merges duplicate bones); in Blender
  MPD_US_MOTOWN and C04_500_POLY export unchanged to 0.0000 cm. One skin part per body so far (Global Storm keeps
  heads as models of their own): a converted body keeps its biggest part and names the ones left out.
- **Characters into Vietnam / Global Storm**: a Desert Storm or Desert Storm II soldier exports onto the target's
  generic body (MPD_US_MOTOWN / C04_500_POLY) re-posed to its skeleton; checked in Blender: DS1 Bradley -> Vietnam and
  the DS2 Iraqi soldier -> Global Storm re-import with their armatures and the target game's walk plays with the feet
  on the ground (12.5-17 / 8.8-12.9 cm).
- **Vietnam animation export** (File › Export › Conflict: Vietnam Animation): keys turned back into rest-relative
  rotations (`evo_write.write_prb_cv`); all 409 game animations rewrite byte for byte and, written anew from their
  decoded keys, put every bone where the game does (257 / 257 within 0.01 cm, `scripts/check_animations.py --game cv`).
  In Blender: the game's walk exported unchanged is exact, an edited head turn changes only the head, and a Desert
  Storm walk retargeted onto the Vietnam body exports as a Vietnam animation with the feet on the ground. Without a
  game animation of the same name it writes the tracks the game's own carry (ROOT_OBJECT with the walk's travel, body
  bones, connectors; no face bones).
- **Global Storm animation export** (File › Export › Conflict: Global Storm Animation (.ani); the game loads
  `Animations\<name>.ani` by the name inside): keys turned into the skeleton file's own terms (`evo_write.cg_tracks`:
  per .SKL bone a local rotation without FixOrientation and an offset from LocalPosition; `write_ani_cg`). All 465 game
  animations rewrite byte for byte (two list one bone twice - kept) and, written anew, put every object where the game
  does (`check_animations.py --game cg`). In Blender: the game's walk exported unchanged is exact, a new name is written
  inside the file, an edited head turn changes only the head, and a Desert Storm walk retargeted onto the Global Storm
  body exports and plays with the feet down. A game animation of the same name gives the new one its name and its
  root-motion samples.
- Added shadow meshes aren't written for Vietnam / Global Storm yet (refused with a message); Global Storm
  level geometry loses its second (lightmap) UV set when re-exported (the export says so). Neither game's plugin
  reads a Mods folder yet, so exports go to files.
- **Global Storm animations** import: they're the unnamed `STATE_*` / `OVERLAY_*` `.BIN` files (the game's `.ani`),
  now listed by their own names. All 465 read; posed the game's way through the skeleton's `.SKL` (absolute local
  rotations, position offsets, a per-bone fix-up rotation) - with no keys that rebuilds the skeleton model's rest pose
  exactly, and a walk keeps the lower foot on the ground in every frame (8.8-12.9 cm).
- Collision surfaces know every game's `Default.spl`: The Great Escape = Desert Storm II's 63 rows, Vietnam 65
  (+ Ignored by Exporter, Steaming Pipe), Global Storm 76 (+ Vehicle Barrier, Air Duct, Brick, Plaster, Electronics,
  Foliage ...); a surface the model's game doesn't have is refused.

### Fixes

- The Great Escape's animations were read with Desert Storm II's 28-byte keys - theirs are 40 bytes (position, scale,
  rotation), so every moving track went wrong after its first key. Now read and written right.
- Global Storm level scenes (`.ENV` = an index of `.ART` / `.DES` / `.SND` parts) imported empty in Blender; the parts
  are joined now (all 16 levels).
- Vietnam / Global Storm models with points: the reader skipped part of the points block (Global Storm's 12 role
  indices, an optional 24-byte block) and fell back to searching for the next section; Global Storm's LOD count after
  a model's buffers is now read as one.
- An import / export started from a script (add-on registered without preferences) now uses the game it was asked
  for.

## 1.8.0

### Blender add-on

- **Characters in several parts**: a character mesh with several materials (or several meshes on the skeleton) no
  longer has to be joined into one object with one texture: the export writes one skin part per material, each with
  its own texture (the `.dds` files Save Textures writes are now used), and cuts a part over 65 535 vertices or
  triangles into pieces. Desert Storm II: the extra parts are added to the model (each with a key of its own); Desert
  Storm: they ride on spare connectors of the skeleton (up to 6 on a soldier body - Desert Storm's animations have one
  track per object, so the model can't grow; an earlier build used bones, and the game finds the feet by counting
  bones - those bodies crashed at the first footstep). In game: Desert Storm II shows every part's texture; Desert Storm puts
  the squad's uniform on every part (enemies and other characters keep their parts' textures) - the export report
  says so. A one-material body exports exactly as before. Import shows the parts as meshes on the one armature;
  exporting onto an already multi-part model is refused (base it on the game's model)
- A character part's texture now follows its material when it isn't the model's own (Desert Storm II soldiers and
  Desert Storm's non-squad characters show it)

### Editor + records

- **Desert Storm levels saved so mods can share them**: `save_mod` no longer copies the whole edited `<LEVEL>.ENV`
  into the mod (game data, and it shut every other mod out of that level). Changed or copied objects, waypoints and
  regions go to `records\<level>.placed.lua` as placed-reference record edits, which DesertStormFix merges field by
  field with other mods' edits of the same level; only the rest (world pieces, lights, deleted lines ...) goes to
  `<LEVEL>.envpatch`, which DesertStormFix now also merges between mods. Every saved edit is checked by replaying it
  (`placed.split_edits`); one that wouldn't give the same scene stays a patch line. Needs DesertStormFix 3.3+.
- **Records tab** in *Records…* (Desert Storm): the game's data as `ds1:` records, edited field by field in the
  mod (`records\editor.lua`, merged with other mods' edits per field); this mod's / other mods' changes and conflicts
  shown per field, links, row lists, copy / revert / delete, report. New command `form_fields`. The raw-row editor is
  the *Tables (rows)* tab now.
- `open_mod_level(mod, level)`: open a shipped level as a mod changed it (its own scene, or the level + its
  `.envpatch` + its placed record edits) to go on editing; `open_dll_mission` reads such mods too.
- `duplicate` gives a Desert Storm object copy its own stat name (`GUARD_A` -> `GUARD_A_2`) - its own editor ID, so
  it is saved as a record copy.
- `placed.py`: `split_edits`, `merged_scenes`, `edit_levels`; a copy may ask for its script id (`script_id = 9` on a
  `based_on` edit; taken = the next free one + a `[fail]` line); conflict lines between mods (`conflict: <id> <field>:
  A / B (B wins)`); the level part of an id ignores case; bad values are `[fail]` lines instead of Python errors.
  `forms.merge` leaves placed-reference ids to `placed.py` (they were `[fail] ... no record type`).

### Model writer (`evo_write`)

- `rebuild(parts={skin: [SkinPart, ...]})`, `SkinPart(texture=...)`: several skin objects for a character (see
  docs/status/DS2.md "Multi-part skins" for the engine findings); `scripts/check_models.py` checks it on every game
  character (3 parts, re-posed within 0.05 cm: DS2 27 of 28 - LOWPOLY_SOLDIER's thirds are too scattered for
  triangle strips -, DS1 33 of 33)

## 1.7.0

### Blender add-on

- **One File menu entry per game instead of a Game switch**: File › Import has *Conflict: Desert Storm (.evo)* and
  *Conflict: Desert Storm II (.evo)* (and the same for Animation (.prb), Scene (.env) and Archives (extract)); File ›
  Export has *Conflict: Desert Storm (.evo)* / *Conflict: Desert Storm II (.evo)* and their Animation (.prb). The
  entry decides the game: its extracted folder, game folder and active mod, and the check that refuses a model or
  skeleton of the other game. The Game dropdowns of 1.6.x (sidebar, dialogs, preferences) are gone; the preferences
  keep one box of folders per game, and the sidebar shows the game of the last import / export, which Export to Mod
  and Test in Game use

## 1.6.1

### Blender add-on

- **Game in the dialogs**: every import / export / extract dialog starts with a **Game** dropdown (the same setting
  as the sidebar's); changing it there switches the game and refills the dialog's game folder, mod and output folder.
  Scripts can pass `game="ds2"` to the operators
- **Menu names say the game**: File › Import / Export entries are now *Conflict Model .evo (Game: Desert Storm II)*,
  *Conflict Animation .prb (Game: …)*, *Conflict Scene .env (Game: …)*, *Conflict Archives (extract) (Game: …)* - the
  1.6.0 names looked the same for Desert Storm as before

## 1.6.0

### Blender add-on

- **Game profiles**: **Sidebar (N) › Desert Storm › Game** (and the add-on's preferences) picks Desert Storm or Desert
  Storm II. The File › Import / Export entries name the game; extraction and the .evo import browser start in that
  game's extracted folder (Desert Storm II: `Documents\Conflict Desert Storm II extracted`); exports, Export to Mod,
  Test in Game and the level tools use that game's folder and active mod; a model or animation skeleton from the
  other game is refused on export with a message saying which game to switch to, and flagged on import. Each game
  keeps its own game folder, extracted folder and active mod in the add-on's preferences (shared by every .blend;
  the per-scene Game Folder / Active Mod of older files aren't read any more). Replaces 1.5.1's export-dialog guess

## 1.5.1

### Blender add-on

- **Export dialog picks the model's game**: Game Folder starts at the game the model is from (a Desert Storm II
  model -> the Desert Storm II install), so Into a Mod Folder no longer lands in, or is refused for, Desert Storm's
  folder when both games are installed. A folder you picked is kept when it is that game's. Brand-new props (Based
  On: Auto / Generic Prop) still follow the sidebar's Game Folder - set it to the Desert Storm II folder for those

## 1.5.0

### Blender add-on

**Conflict: Desert Storm II models and animations**

- **Export for Desert Storm II**: a model based on a Desert Storm II model (`.evo` from its extracted archives) is
  written in Desert Storm II's layout - weapons, props, vehicles with their part hierarchy, level pieces and soldier
  bodies (skinned to the game's skeleton), with collision, shadow meshes and points, like Desert Storm's. The model
  file decides the game; nothing to switch
- **Desert Storm II animations** (`.prb`): import onto a Desert Storm II soldier (269 of the game's 274 animations
  fit the soldiers, the vehicle gunner / passenger ones included; the goats' only fit the goat) and export back,
  footstep markers included. Desert Storm II's tracks name the bone they move, so an animation also plays on the other
  bodies that share those bones; tracks for objects the body doesn't have (a vehicle's gun) are kept
- **Collision surfaces**: Desert Storm II's extra ones (Snow, Soft Mud, Tree Canopy, Gravel, Plastic, Rubber and
  their Blocking twins) are in the surface lists, marked "Desert Storm II only"; a Desert Storm model refuses them
- **Brand-new props** for Desert Storm II are built on its small `WALLMAP` model (Desert Storm: `BH_BODGEPLATE`)
- **Into a Mod Folder** writes Desert Storm II models into `<game>\Mods\<Mod>`, which DesertStorm2Fix's Mods folder
  serves by file name (tick **Replace the Game's Model**). No starter `.weapon` / `.skin` (those are DesertStormFix's).
  **Test in Game** exports and starts Desert Storm II at its launcher - DesertStorm2Fix has no `+level` start, so the
  Test Level is ignored there
- **Tried in the game** (Desert Storm II Mission 1, DesertStorm2Fix with its Mods folder): a Land Rover with a new
  texture, the MK19 gunner's and the passengers' animations edited, a prop's collision rebuilt on a Desert Storm II
  surface - all served by the Mods folder and used, no load errors

**Fixes (both games)**

- Collision: merging the faces of a collision mesh could join slivers into an outline that doubles back on itself,
  which covered other ground than the faces did (seen as gaps up to 30 cm when rebuilding game collision). Such
  merges are refused now
- A model with a single part no longer takes in the small marker meshes of its other objects (Desert Storm II's
  connectors have some), and a character's body is picked over them
- Textures extracted without their name (`_FA71C197.DDS`) export under the model's own texture name again instead
  of the hash file's name, which the game wouldn't find

### Tools

- `scripts/check_models.py <extracted folder>`: rebuilds every model of a game (no change, own geometry, skins,
  collision, points, an added shadow) and checks the result; `scripts/check_animations.py`: the same for `.prb`
  animations. Game data stays outside the repo
- `evo_convert.py` binds animation tracks the way the game does (Desert Storm II by bone name)

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
