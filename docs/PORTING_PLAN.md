# Porting between the Conflict games - plan

Goal: take content from one of Pivotal's games and make it work in another - single assets, whole levels, and in the
end mission logic. **First direction: into Desert Storm (DS1)**, the game with the most complete patch (Mods folder,
Lua missions, records layer, editor Play, AI navigation built from the scene) - so every step can be played and checked.

Status (2026-10-23): Part 1 (asset porter, into DS1) works for static models, weapons (+ HUD pictures, magazines),
characters / soldier types (`.unit`, Global Storm heads), animations, static and drivable vehicles (wheeled and
tracked, own tracks / turret, guns by kind) from DS2 / The Great Escape / Vietnam / Global Storm - checked in game.
The porter runs from recipes (`cli/ds_port.py`, Blender File > Import > Conflict: Port Between Games).

## Queue (other games - kept here so nothing is lost)

Done 2026-10-23: **Blender scene (.ENV) export** for all five games the add-on imports (README "Export the scene
back"; Denied Ops scenes stay with the level editor); **Part 1 leftovers**: drivable ports carry the source's own
guns (in game: the Arjun's cannon + NSV), Blender's cross-game character export takes Global Storm heads.
Done 2026-10-23: items 1 and 2 (Part 2 tables / texts / sounds; Part 3 level porter: DS2 Training 3, Mission 1 and 2
load and play in DS1 - see Part 3). Next is item 3.

1. Part 2: tables / texts / sounds - DS2 -> DS1 first (schemas per source game, column mapping into DS1's records),
   then Vietnam / Global Storm sound format survey.
2. Part 3: level porter - one small DS2 level -> DS1 as a `.lua` level (world, scene records, routes).
3. Part 4: mission logic - that level's mission from its `.SI` objectives (rules), then the DS2 mission DLL via
   decompile + slot mapping.
4. The other direction (DS1 / others -> DS2, Vietnam, Global Storm, Great Escape) once each game's patch loads mods
   (VietnamFix's Save as mod already does; DS2Fix has a Mods folder by file name).

## Already works

| What | State |
| --- | --- |
| Static models (props, weapons, level pieces) between games | Blender: any game's model exports into DS1 / DS2 / Great Escape / Vietnam / Global Storm on the target's generic prop or a Based On model; textures renamed `<SRC>_<name>`; collision surfaces mapped to the target's Default.spl (`bsp.convert_surface`) |
| Characters | DS1 <-> DS2 both ways, DS1 / DS2 -> Vietnam / Global Storm (one skin part); re-posed onto the target skeleton (`cross_repose`) |
| Animations | Every game's animation imports onto every other game's skeleton (retargeted by bone name, `evo.retarget`) and exports in the target's format (all five games write) |
| Scenes | The editor reads and writes every game's scenes and routes byte for byte (DS1 v70-72 ... Global Storm's split .ART / .DES / .SND, Denied Ops) |
| Texts | Every game's text bundles read (`records.Records.read_bundle`); DS1 takes replacements through `.strings` |
| Sound caches | DS1 and DS2 `.sch` share the container and samples; DS2's BANK entries carry 20 more bytes each (distances), converted by `sound.bank_to_ds1` (all 714 DS2 banks; the shared MP5SD bank comes out byte-identical to DS1's) |
| DS1 missions | Lua host with `game.*`, editor rules, all 566 script API slots named; mission DLLs decompile to readable Lua (stage 2) |

Not yet tried for this plan: Vietnam / Global Storm characters INTO DS1, vehicles (hierarchy + .DYN + tables) between
games, any sound outside DS1 / DS2.

## The rule that shapes everything: no game data in a shared port

A ported level or asset pack is another game's data. Shared mods already avoid shipping DS1 data (`.uses`,
`.envpatch`, `.rmdpatch` - built on the player's PC from their own archives). Ports do the same one level up:

- A **port recipe** (small text / Lua files, shareable) says what to take from which game and how to convert it.
- The **porter** runs on the player's PC against their installed copy of the source game and writes the converted
  files into a local cache (`<DS1>\PSE\Cache\ports\<recipe>`), like the asset store / mission translation caches.
- The player needs both games installed. Recipes made from your own copies are fine to share; converted output is not.

Decision needed from you (open question 1): is that acceptable, or should some ports be single-machine only (your
own play, never shared)?

## Part 1 - Asset pack porter (Blender + command line)

Batch version of what Blender does one file at a time.

Built so far (pure Python, no Blender needed - so a recipe can run on any player's PC):
- Recipe format (`.port`, see porting.py): `name`, `from`, `to`, `source`, `model NAME|PATTERN [as NEW]`,
  `animation NAME|PATTERN [as NEW]`; `python cli/ds_port.py <recipe> [--source] [--target] [--mods]`, or in Blender
  File › Import › Conflict: Port Between Games (recipe) (report in the Text Editor).
- Sources: a game folder (archives, read with that game's name hashing) or a folder extracted from it.
- Static models (props, weapons, vehicles flattened in their rest pose) onto DS1's generic prop: every render object
  in model space, one part per texture (`<SRC>_<texture>` files copied, DDS format / size checked), collision trees
  with surfaces mapped, the first five points (muzzle ...) kept; connectors, hit boxes and shadow objects left out
  (listed in the report).
- Animations retargeted onto Bradley's skeleton and written as DS1 `.prb` (footsteps kept from DS1 / DS2 sources).
- Report: `port-report.txt` in the mod folder.
- Checked (scratchpad port_check.py): a prop, a weapon and a truck from each of the four games - DS1 model, same
  triangles, every vertex in place, collision area equal, muzzle kept, textures found; a walk from each game fits
  Bradley with the feet on the ground. Unit tests: tests/test_porting.py.
- Placeholder textures: a model whose texture is a stand-in the game swaps at run time (Global Storm cars:
  `C04_V_TextureOverride`, 16 x 16) gets a texture named after the model (C04_ISUZUTRUCK_CIV -> C04_V_ISUZUTRUCK_GREEN;
  the other variants are listed in the report).
- In game (2026-10-23, scratchpad port_photos2.py, Steam dev build, Mission 1 in daylight): a truck from each game
  ported `as T62_IRAQTANKDEAD` stands in the wreck's place 12 m from Bradley, textured - DS2 Land Rover
  (SAS_LANDROVER_WITH_MK19_SAW), Great Escape plumber's truck, Vietnam deuce truck, Global Storm Isuzu; files served, no
  load errors.
- Vehicles whole (static copies): wheels / tracks the game attaches at run time are merged in - DS2 / Great Escape
  from the `.DYN` (+ guns from VEHICLE / VEHWPNS / WEAPS), Vietnam from the vehicle's physics file (unnamed text
  files `Version = 1 / PhysicsCar { PhysicsVehicle { PhysicsObject { Geometry = <MODEL> } ... } Wheel / Door
  { Attached { Geometry, Position, Offset } } }`, metres, position + offset in the model's space; 10 vehicles), Global
  Storm from the game's own `<NAME>_STATIC` model where there is one (wheels and doors included). Seen in game: the DS2
  Land Rover with 4 wheels, the Vietnam deuce truck with 6 + its door, the Global Storm Isuzu from its static version.
- Placing ported props in a level: copies placed by a `records\*.lua` edit (based_on a level prop, `scene = {
  model = "<ported model>" }`) are drawn since DesertStormFix 2026-10-23 rebuilds the culling octree of a
  mod-edited scene (before, added objects were in play but invisible). Seen in game: 12 ported models (a prop,
  weapon and truck from each game) plus a control crate in front of Bradley in Mission 1.
- Weapons (`weapon NAME like DS1_WEAPON_ID [as NEW]`): the gun model plus a starter `<NEW>.weapon` (id, based on the
  named DS1 weapon - its numbers, sounds, animations -, display name "AK47 (Vietnam)", the ported model), refused when
  the DS1 id isn't in Weaps.txt. A held gun is moved from the source game's hand connector onto DS1's
  (`hand_fix`: both connectors relative to BIP01 R HAND from the games' soldier skeletons, target^-1 x source):
  Vietnam / Global Storm guns run along +Z on HAND R CONNECTOR, DS1's along +Y on BIP01 GUNR CONNECTOR - unturned
  they lay across the hand. After it the ported AKs sit within a few cm of DS1's AK47 (barrel to y 57-61, stock to
  -30). In game: each game's rifle given to Bradley in Mission 1 (Dev\LoadoutTest) - accepted as a new weapon,
  HUD shows the base weapon's picture and ammo, the gun held in front of him like DS1's own.
- HUD / inventory picture for ported weapons: rendered from the model (`render_picture`: side on, muzzle left,
  depth-buffered, colours from the gun's own texture per pixel, 4x supersampled, a dark 1-pixel outline like the
  game's) at the size of the DS1 base weapon's own picture (`base_picture`: GWInt.img by Weaps.txt column 5; AK47
  81 x 33, PSG1 110 x 30) -> `<MODEL>_ICON.png`, `hud icon` in the starter .weapon. Seen in game: the HUD weapon panel
  shows each game's rifle with its own picture.
- Gun magazines: a Vietnam / Global Storm gun with a CONNECTOR_CLIP gets its magazine model merged in there - the
  weapon row naming the gun model gives its id, the AMMO row naming that id its magazine (Vietnam INVEN.TXT
  `Ammo_AK47,AMMO,MPD_AK47_AmmoClip,...,VC_WPN_AK47`; Global Storm's same tables are unnamed catalog .BIN files, one
  column further on - found by content, `GameSource.table_rows`). Both AKs now show their magazine (model + picture).
- Shadow meshes: the source's #SHA_ objects (all parts, attached ones too) become one shadow object
  (`rebuild(add_shadow=...)`, held guns moved with the gun); the report gives its triangle count and warns over 100.
  Vietnam AK 92, deuce truck 264; DS2 / Global Storm props without one (NOSHADOW) get none. Not yet seen cast on
  screen in game.
- Characters (`character NAME [for Bradley|Foley|Connors|Jones] [as NEW]`): the body moved onto that squad
  soldier's own body (its skeleton, so DS1's animations drive it) - skin parts with weights by bone name, rigid
  pieces hung on bones (DS2's eyes, eyelids, teeth) with their bone's full weight, re-posed by
  `crossgame.cross_repose` (moved out of blender.py so both the add-on and the porter use it), one skin part per
  texture (DS1 keeps up to 6 extras on spare connectors), tiny pieces whose strip would be longer than their list left out - plus
  a starter `.skin`: uniform "<name> (<game>)" for that soldier, his texture = the main part's, `body` = the ported
  model. Seen in game (Mission 1, the .skin row applied with the game's own skin apply 0x435BE0): Bradley wearing the
  DS2 Iraqi soldier, the Vietnam US soldier and the Global Storm tiger-stripe soldier, animating; DS1's gear (radio
  pack, pouches, cap) stays on top - gear is the loadout screen's.
- **Heads** (`character|unit NAME with HEAD ...`): Global Storm keeps heads in their own models (same skeleton, own
  eyes / teeth); the porter adds the named one, else <NAME>_HEAD if the source has it (C04_500_POLY ->
  C04_500_POLY_HEAD), `with -` none. Seen in game (Mission 1): the Global Storm soldier with his head. Pick the full
  models: C04_500_POLY / C04_160_POLY are distance versions (410 / 146 triangles; the report says so) - the full
  soldier is C04_MILITARY_UNIFORM (3 396) with a full head (C04_HEAVY_HEAD, C04_SOLDIER_HEAD ... 3 100).
- **Head gear**: the body's HAT / HEAD / FACE connectors follow the new head's top and front (Vietnam +3 cm, Global
  Storm -3 cm), so Desert Storm's berets and glasses sit on it. Checked in game next to an unchanged conscript.
- **Soldier types** (`unit NAME like UNIT_ID [as NEW]`): the body fitted to the skeleton of that Desert Storm unit's
  own model (Units.txt column 3, e.g. Iraqi_Conscript -> IraqInfantry01_Sandy) plus a starter `.unit` (id <= 35
  characters, `based on` the unit: type, health, skill, voice and kit come from it). Seen in game (Mission 1, a
  records edit placing copies of conscript NEW_GRUNT02 with the new units): the Vietnam, Global Storm and DS2
  soldiers standing as enemies with the conscript's AK, walking without trouble. Found on the way: the first
  version hosted extra skin parts on spare bones, but DS1 finds the feet by counting kind-3 objects (FUN_00503610)
  and crashed at the first footstep (0x424D5C) - extra parts now go on 6 unused connectors only (evo_write
  SPARE_CONNECTORS), which also fixes Blender multi-part exports for DS1.
- **Global Storm vehicles without a static version**: `.PHY` physics files (same text format as Vietnam's) for
  wheels / tracks / doors (parts at Position; road wheels inside their Track's frame; Offset not drawn - matched
  against C04_MTLB_STATIC), turrets on their connectors (key-index fix in evo.py), guns from the vehicle weapon tables
  (`GameSource.vehicle_guns`). Rendered: Arjun, BMP-1, MT-LB (= the game's static copy), Land Rover MK19 (.50 + MK19),
  pickup.
- Vietnam vehicles' guns from the same tables (M48, T-34, Ontos, patrol boat, APC, DShK jeep).
- **Drivable vehicles** (`vehicle NAME like DS1_VEHICLE_ID [as NEW]`, `port_vehicle`): body onto the base vehicle's
  model, its connectors moved to the source's by name, source wheels on a fitted .DYN, starter .vehicle. In game:
  Global Storm pickup as Mission 8's squad vehicle, crewed and driving (drive_test.py: loadout swap
  VehiclesSPMission8, script API +0x24C mount_vehicle).
- Drivable tracked vehicles: kind-1 track writer (evo_write `_vlst`), source tracks as the .DYN track models. In
  game: Arjun (on the M1 Abrams) driving in Mission 8. Loadout swaps only exist in MissionN levels with a squad
  vehicle (8, 9); Mission 9's script destroys its squad vehicle early (stock Abrams too).
- The source's own guns on drivable vehicles (2026-10-23): each base gun connector matched to a source connector
  with a gun gets that gun on the base gun's model (muzzle point kept, turned from the source connector's axes), a
  `.weapon` based on the base's gun and a `.vehicle` line `gun = <base gun> -> <new>` (DesertStormFix mods.cpp);
  Vietnam's in-place tracks get both sides (side from the mesh, not the position).
- Great Escape vehicles drivable (2026-10-23): tracks from the `.DYN`, guns + kinds from its tables, model-tagged
  connector names (`CONNECTOR_P3J_DRIVER`), unmatched base guns left off (`gun = X -> -`), at most 18 wheels (the
  rest built into the body). In game: Panzer III (on the Abrams) and Hanomag (on the Bradley) in Mission 8.

- Input: a source game folder (or its extracted folder), a target game, a selection (by name pattern, by level, by
  kind: props / weapons / characters / animations / textures), and for characters the target body to fit them on.
- Output: a mod folder for the target (DS1: `Mods\<Pack>`) with the converted files, starter `.weapon` / `.skin` /
  `.unit` files where DS1 needs them to show up in game, and a report: what converted, what was mapped (surfaces,
  bones, textures), what was left out and why.
- Recipe form for sharing: the same selection as a `.port` file; `pse_build` (or the ModKit CLI) runs it on the
  player's PC.
- Gaps to fill on the way:
  - Vietnam / Global Storm characters into DS1 (reverse of what exists; their bodies hold only the skin - the skeleton
    file supplies the bones).
  - Textures: DDS / TGA everywhere so far; check each game's formats and sizes (DS1's heap and texture limits:
    DesertStormFix memory.cpp).
  - Vehicles: model hierarchy + `.DYN` (wheels / tracks) + VEHICLE / VEHWPNS / WEAPS rows; DS1 needs all three.
- Checks: every converted model loads in DS1 (bg run, no `[fail]`, mod report), characters animate with DS1's own
  walk, vehicles drive.
- Effort: small for props / weapons / animations (mostly batching), medium for characters from cv / cg, medium-large
  for vehicles.

## Part 2 - Tables, texts and sounds

Needed by both assets (a ported weapon needs its Weaps row, sound bank, display name) and levels.

- **Tables** (weapons, units, vehicles, skins, gear): DS1's records layer has a schema (19 tables, named columns).
  Write a schema per source game (what its parsers read - the same RE method as DS1's), then a column mapping into
  DS1's tables; values with no DS1 meaning reported, not guessed. Output = `records\*.lua` edits, which DS1 already
  merges.
  - Done (2026-10-23): DS2 weapons -> DS1 (`source_weapon`, `WEAPS_LAYOUT`): DS2's Weaps.txt = DS1's + 4 columns
    from 44; columns compared over the 73 shared weapons (same value for >= 90 %, plus the re-tuned fire interval /
    magazine size / damage) are copied into the ported gun's `.weapon`, the rest stay the base's and are named in the
    report; its shot bank (pack, renumbered samples) and its name (DS2 texts) come along.
  - Done (2026-10-23): Great Escape weapons -> DS1 (segments in `WEAPS_LAYOUT`, `WEAPS_TRUSTED` columns: too few
    weapons in common to compare).
  - Done (2026-10-23): DS2 / Great Escape units -> DS1 `.unit` (`unit_extras`, `UNIT_LAYOUT`): type, experience,
    HStat (compared / health only), kit, voice pack, Great Escape unit skin. 
  - Done (2026-10-23): DS2 vehicles' stats -> `.vehicle` `stat column N` (`vehicle_stat_lines`: VStat columns
    compared over the shared vehicles; DS2 VEHICLE.TXT is space-separated, `GameSource.vehicle_rows`), guns paired
    one by one with the base's (two base guns on one connector handled). Checked in game: DS2 GAZ69 on the Humvee
    (own GAZ69_STATS row, driven 63 m), DS2 Land Rover MK19 / SAW on the MILAN Land Rover (2 guns). Great Escape
    vehicles keep the base's stats. 
  - Done (2026-10-23): texts -> `.strings` (`texts_file`, recipe `texts M01` / `texts GE_LEVEL01` / `texts catalog`):
    the source games hash text keys like DS1, so a ported script's text numbers carry over; a mission's texts go to
    a section `[DS2_M01]` the ported level switches on with `game.load_strings("DS2_M01Strs.loc")` (DesertStormFix:
    a name only a `.strings` section has), hash-only keys as `#1A2B3C4D`; shared texts only where DS1 has none.
    Checked in game: a `.lua` level on Mission 1's map reads DS2's M01 and shared texts, DS1's own unchanged.
- **Texts**: source `.loc` / bundle strings -> DS1 `.strings` (by key, per mission section `[M01]`).
- **Sounds**: DS2's `.sch` is DS1's container and sample format, but its BANK entries are 36 bytes, not 16 - a DS2 bank
  must go through `sound.bank_to_ds1` (an unconverted one made DS1 call the level cache corrupt and delete it). Vietnam / Global Storm sound data
  isn't checked yet (format survey first; decode to WAV, re-encode with DS1's ADPCM encoder in DesertStormFix
  mods.cpp / ModKit `sound.py`). Voice banks map by event id only where the event tables match.
- Effort: medium per game (schemas are the bulk), sounds unknown until surveyed.

## Part 3 - Level porter (ModKit editor + CLI)

A source level becomes a DS1 `.lua` level (a mod) that DS1 loads like an editor-made mission.

- **World geometry**: zone models (EOBJ type 5) through the model converter; collision trees rebuilt for DS1's
  surfaces. Global Storm's world pieces carry a second UV set for lightmaps that DS1 can't draw - bake the lightmap
  into vertex colours (DS1's world is prelit by vertex colour). Size limits to check: vertices per buffer (65535),
  zone counts, the Global Heap.
- **Scene records**: per record kind, a mapping from the source scene's version into DS1 v72 - ENVI (props), HLOI
  (units, vehicles: unit names mapped through Part 2's tables), waypoints (linked), regions, cameras, lights, emitters,
  switches / doors, ambient sounds. env.py already reads every version, so this is field mapping, not new parsing.
- **What DS1 builds itself**: octree (rebuilt for `.lua` levels) and AI navigation (`BuildNav` makes the quadtree +
  markers from collision; hand-placed route markers from the source's routes are kept as DS1's hand-placed block).
- **Zones / portals**: DS1 draws by zone through portals; source portals map across for DS1 / DS2 / Great Escape, while
  Global Storm's split scenes need checking. Fallback: one zone per source zone, every portal open.
- **Order of source games**: DS2 first (same engine generation, same `.sch`, its script API already aligned to DS1's
  for 330 slots), then The Great Escape, then Vietnam, then Global Storm (most different: split scenes, lightmaps,
  887-slot API).
- Checks: the level loads, the player spawns on the ground, AI walks (route probe), no `[fail]`; screenshots per zone.
- Effort: large. DS2 -> DS1 for one level is the first milestone.
- **Started (2026-10-23)**: `conflict_ds_tools/levelport.py`, recipe line `level TRAINING03 [on Mission3] [as NAME]`.
  DS2's scenes are built like DS1's (zone models whose objects are world-space pieces with a record each, props
  parented to them, portals with the same fields), so: zones convert object by object onto DS1's world-piece object
  (MIS1_ZONE2 object 1: names / order kept, collision rebuilt, surfaces mapped); props through the model porter
  (DS2_<MODEL>); records rewritten in v72 on a blank DS1 shell (`blank.blank_lines`, base level's archive, sky,
  sounds), **parents first** (DS1's loader finds a parent only among records already read - DS2 links them afterwards
  with PCLINK, so its file lists props before their zones: 120 props "Unable to find parent"), portals without DS2's
  pair list, the squad moved to DS2's start, DS2's fog / colour keys / time / sun; the `.lua` script = the base
  mission's map set-up (incl. its map picture: API +0x50 crashed without one) + bounds round the zones; `.uses`
  `file NAME.OCT = BASE.OCT` so the octree rebuild has a name. Checked in game: DS2 Training 3 loads on Mission 3's
  archive, Bradley on DS2's ground at its start, the HQ / gate / street as in DS2's own shot, AI navigation built
  from the scene. Fixes on the way (all ports): alpha textures get VBUF flags 0x03 like DS1's (fences, foliage); the
  generic prop's blue-grey material (it tinted every ported prop) written as DS1's white prop material.
  Then (same day): **objects** - `env.convert_record(rec, version)` writes any typed record in another scene version
  by running the reader's own layout function with a writing cursor (every ENVI / HLOI / PORT / waypoint / region /
  sound / light / camera / switch of all 27 DS1 + DS2 scenes writes back identically, 63 000 records); DS2 objects
  whose unit DS1 has (soldier types, weapons, items, vehicles - model from DS1's own scenes / tables) keep it, their
  kit and bone attachments filtered to what DS1 has (a missing pouch model crashed the load), Root objects go into
  the zone that holds them, the shell squad's script ids move above DS2's; other kinds in play (pop-up targets, lamp
  posts, destructible structures, vehicles DS1 lacks) stand in as static props, doors are left out. **Waypoints and
  regions** convert with ids kept; **routes**: DS2's .RMD (v3) as DS1's v1 - BuildNav keeps the hand-placed markers.
  Checked in game: Training 3 (guards on patrol, Black Hawk hovering on its path, 16 + 198 route markers, 1 island)
  and **Mission 1** (11 zones, 296 props, 90+ objects, 219 waypoints, 426 + 918 markers) both load and play.
  Fixes for every port: invisible buffers (untextured, VBUF 0x04 without 0x01 - DS2's camera blockers) are no longer
  drawn; rebuilt objects get their header counts (+0x98 triangles / vertices / parts - wrong ones are harmless for
  props but not for zone pieces).
  Open: chain-link wire invisible (the panels draw; DS1 loads textures with one mip level unless the texture entry
  asks for a chain - a DDS with mips came out white), breakable props (DS2's shard block), animated props (VAMI),
  doors, lights, ambient sounds, cameras, sound banks the base archive lacks (T-62 / ZSU guns, Kuwaiti civilians),
  map picture, sky textures.
  Then: **render effects** - the game loads `<scene>.rfx` (scene name, extension swapped: 0x40C815..0x40C83F), which
  gives each zone / prop base texture its detail texture (REFX_DETAIL ... DETAIL_MAP), bump and reflection maps; a
  ported level had none, so the second texture stage drew whatever it held - the ground's "plank" streaks (seen with
  DS1's own Mission 1 run through the porter too, which is how it was found). `rfx_file` converts the source's
  .RFX (models renamed, base textures prefixed, effect textures copied; water-shader entries dropped). **Doors**:
  DOORS.TXT has the same 16 columns in both games (col 5 = the door's sound bank, col 12 flags & 0x1F); a door DS1
  lacks gets its model ported and its row appended to the mod's DOORS.TXT. **Lights** copy as is (same untyped
  lines; ones attached to an object - flag 0x800 - left out: an unknown object crashed the load), records keep
  their light lists (max 6). **Cameras** in the source's order (scripts pick them by index): DS1's extra int, low 16
  flag bits, object cameras name <MODEL><script id>. **Sound banks** the objects need (voices, engines, guns, items,
  doors) that the base archive's cache lacks become `.uses` "bank" lines. Checked in game: Training 3 (night, lamps,
  doors, gate open), Mission 1, Mission 2 (7 new door kinds).
  Then: **Desert Storm's own models first** - a source model DS1 has under the same name (the games share the
  training camps and a big prop library: 55 of Training 3's 75) is placed as DS1's own copy, its RFX base textures
  unprefixed. **Sky** (both layers, textures copied), **ambient sounds** (ENVSND records +
  their samples, keyed by the ELF hash of the name without extension, as a `<NAME>_AMBIENT.sch` pack), **breakable
  props** (DS1's breakable block via the record converter; the shard models are a library both games share),
  **loading picture** (the source's, when it has one - Mission 3's M3.BMP is missing from DS1 itself).
  Then: **tiled textures** - every VBUF ends with two u32s the writer always wrote as 1, 1: texture clamped on U / V
  (the game's rule for buffers without them, FUN_005259c0: 1 when every coordinate lies in one whole tile, moved into
  tile 0; 0 = repeats). A tiled texture written as clamped shows only its edge texels - the chain-link wire (U 0..32)
  vanished, also on DS1's own fence rebuilt by the writer. The writer now decides them the game's way (matches 97 % of
  DS1's stored flags; the rest end exactly on a whole number, where both draw the same). VBPL is read past by the game
  (0x525920). With that, the same-name rule is **identical models only** (`_same_model`: textures, triangle counts,
  bounds per object): DS2 reworked 29 of Training 3's 55 shared names - its fences have straight posts and a plain top
  rail, DS1's barbed-wire tops - so those are ported. Checked in game: Training 3 with DS2's fences, wire showing.
  Then: **animated props** - VAMI (an ENVI line + frame rate; type 3, a kind-1 model = one mesh per frame) and ANMI
  (+ an animation name) exist in both games with the same layout; env.py types them in every version now (67 records
  in both games, round trip unchanged). port_model puts a source whose render objects are all kind 1 onto DS1's
  FLAG_FLAPPING (one kind-1 object) with all frames (ANIMATED_PROP). **Map picture** - the script DLL's image call
  (DS2 slot +0x74, DS1 +0x4C) and bounds call (+0x7C / +0x54, (max, min) as two stack vectors) are read from the
  code (`source_map`); the image list (imgf version 2 - DS1 reads it too: its GWINT.img is one) is shipped as
  GW<NAME>.img with its page renamed (16-byte field; DS1 has its own GWMission10.png). Training 3 has no map in DS2 -
  the base level's picture stays. Checked in game: Training 3's flags flap; Mission 1 / 2 maps with the squad marker
  where the squad starts.
  **Part 3 done.** Left for later: a map drawn from the scene for levels without one; mission texts / titles on the
  loadout screen (the level's name shows).

## Part 4 - Mission / script porter (research first)

Two ways to bring a mission's logic across, not exclusive:

1. **Translate the source mission DLL** (same x86 code, same LevelSetup / API-table design in every game): decompile
   it to readable Lua as DS1's stage 2 does, then map each source API slot to DS1's `game.*` / API slots by meaning.
   Needs, per source game: its API slots named (DS2: 330 aligned to DS1's; Vietnam and Global Storm: none yet), its
   message ids, its object / waypoint / region ids mapped through Part 3's scene port. Slots with no DS1 equivalent
   become stubs listed in the report (e.g. Global Storm's squad systems, Vietnam-only behaviour).
2. **Rebuild the mission as editor rules**: objectives from the source `.SI` (DS2 / Vietnam / Global Storm all have
   one), triggers / spawns / waypoints from the scene, cutscenes by hand. Less faithful, much less research - a good
   fallback for missions whose logic doesn't map.

- First target: one DS2 mission into DS1, via route 1 (DS2's slots are the closest).
- Effort: large and uncertain; each new source game starts with naming its API.

**Status 2026-10-23 - DS2 Training 3 runs in DS1.** Route 1 on stage 1, not stage 2: DS2's DLLs are MSVC 7 with
whole-program optimisation - internal functions take arguments in registers (Training03 0x10004A00 reads ECX,
0x10004A90 ESI), which the readable translator doesn't follow yet; stage 1 keeps every register, so it does.
`crossmission.adapt_stage1` swaps LevelSetup's API table for a DS2-layout one (0x9F0 bytes, allocated with the game's
allocator 0x4B8450): mapped slots forward to DS1's own function (`NCALL`), Lua slots (version check +0x000), the rest
log + return 0; first use of each slot logged. Fixes on the way: translators - `pushfd` / `popfd` (CRT CPU check:
the ID bit never sticks -> no cpuid -> x87 paths; nothing SSE2 is reachable from the mission's roots), readable -
`__security_check_cookie` and MSVC 7 `sprintf` signatures (C++ gate still 18/18 identical); level porter - squad
keeps DS2's script ids (121-124 in Training 3), Ramirez (Connors' unit) no longer dropped (blank.SQUAD), out-of-play
soldiers -> in play + deactivated at start (crash 0x42DF97); adapter - the script host's standard animation set
(DS2 registers none of DS1's states) + TALKING (Master_sergeant.prs, DS1's trainings) for start_talking.
Run: scratchpad xm_test.py (port under the DS2 name, translate, adapt, play 40 s). Training 3: 48 slots used, 8 unmapped
(+0x00C/+0x010/+0x014 set-up brackets, +0x5D8 briefing text, +0x9C4, +0x6E8, +0x8A0, +0x0D0).
2026-10-24: **message ids** - both exes' send sites (script object DS1 0x60F578 / DS2 0x62FE00, `push id; call
[reg + 8]`) paired by surrounding code (scratchpad msg_align.py): one ordered list DS2 changed in places - 0x00-0x1B
same, 0x1C-0x20 +1, 0x27-0x2B -3, 0x3B-0x49 -9, 0x4C-0x4F -10, 0x53 -11; gaps with differing counts unmapped
(0x21-0x26, 0x2C-0x3A, 0x4A/4B, 0x50-0x52: not delivered, first one logged). `crossmission.MESSAGES` + a wrapper on
the DLL's message callback. Seen in Training 3: waypoint 0x08 / path 0x09 and queries 0x3D 0x53 0x47 0x4D arrive;
0x26 (custom action type) has no counterpart. (DS2Fix's notes call DS2 0x12 "failed" - the alignment says 0x00-0x1B
match, DS1 0x12 = mission start: to check when a level with an overview runs.) **Files** - `mission_files`: the
level's sound cache as a pack (banks converted with sound.bank_to_ds1 - DS2's 20 extra bytes per entry made DS1 call
the merged cache corrupt; banks DS1 has under the same key stay DS1's: 12 converted, 19 kept, 606 samples) and the
texts the DLL loads ("T03Strs.loc" -> [T03] section; DS1 has a T03Strs.loc of its own, the section goes over it).
The DS2 install here has no voice streams at all (Data\Sound = 9 music tracks; the DLL's Tm03_*.psf exist nowhere).
Unmapped slots identified (editor/games/ds2/api_slots.json, CDS II.exe table 0x60D638): +0x00C / +0x010 / +0x014 =
register_standard_anims_c / a / b (the exe's built-in animation sets via 0x421B30 - Lua 0: DS1's script host loads its
standard set), +0x0D0 time_passed(t) (cmp t, [0x680098]+0x10; sbb; neg -> Lua on DS1's clock [0x63C954]+0x10),
+0x5D8 set_briefing_text -> DS1 +0x4E0 (same (hash, slot)); guessed, unmapped: +0x6E8 select_inventory_slot,
+0x8A0 play_object_sound, +0x9C4 soldier_reset_held. With input (WALK=1: W held 6 s between shots) Training 3 moves on:
the script opens the tunnel's blast door (set_door_open -> door message 0x15), shows tutorial text, Bradley walks out
onto the training field.
**Mission 1 runs** (2026-10-24, 75 s, no crash; 68 slots, ~13 unmapped): fixes it needed -
- vehicles Desert Storm hasn't got are **ported**, not stood in for (user: "why are we finding standin instead of
  porting the stuff from ds2"): levelport runs porting.port_vehicle on the source model with the closest Desert Storm
  vehicle as the handling base (_vehicle_alias, single letters no longer count - "T-55" had matched "T-62" on "T";
  else _vehicle_base: T-62 for armour, Humvee for the rest; aircraft stay scenery), its .vehicle keeps the SOURCE
  unit name (UK_VEH_SAS_LandRover_MK19_SAW) so the record and the script still name it. Mission 1's Land Rover (0xC3,
  carries the squad) was scenery and the script's set_ai_flag_200000(0xC3) crashed at 0x487896;
- porting.port_vehicle bug: part names were cut to 31 letters after the suffix - a 31-letter vehicle
  (DS2_SAS_LANDROVER_WITH_MK19_SAW) gave its guns its own name and the gun overwrote the body (2 objects instead of
  16; "Not anim - vehicle ..." at 0x4380DD). _suffixed() shortens the name first (wheels, tracks, guns);
- the adapter checks object arguments (api_slots.json type "object", 188 args) with DS1's lookup FUN_004CA660 and
  skips + logs a call on an object the level hasn't got (DS1's functions don't check);
- message 0x34 enemy spotted -> DS2 0x5B (by code order in both exes' AI spotting functions, same (observer,
  target) block). Seen delivered: killed 0x00, removed 0x01, healed 0x0B, damaged 0x0C, shot 0x3C, vehicle damaged
  0x1F, mounted 0x1C, spotted, waypoint / path, queries.
Still unmapped in Mission 1: +0x004 (DS2's own anim registration - its .prs files are DS2's), +0x018, +0x1F0, +0x358,
+0x478, +0x4A4, +0x4E4, +0x858, +0x870, +0x8A0, +0x9B8, +0x9C0, +0x9D8; messages 0x26 / 0x37 (queries) undelivered.
2026-10-24 (later): **everything a mission names is ported** (user rule: port, don't stand in) - levelport now ports
every object kind the source has and Desert Storm hasn't: vehicles the drivable porter can't take (AA guns, aircraft:
the source model on the base vehicle's own model as template, _port_vehicle_model; jets on IR_VEH_MiG29 /
IR_VEH_MIG29_MISSION01, helicopters on the Hind), soldier types (_port_unit: unit_file + port_character on the
closest Desert Storm type - US_Delta_Force_M2 on US_DELTA_FORCE), weapons (_port_weapon: port_model held + weapon_file,
a pistol base when no name word is shared - the flare gun with its own model, sound bank and HUD picture), and the
STRUCTS / FEATS / TARGETS rows (same layouts in both games: hedgehogs, Mission 2's mortar strike points, lamp posts;
models ported, FEATSTAT rows added, Desert Storm's own TARGETS row now used - Training 3's 67 pop-up targets are
targets again). Each keeps the source's id; models in STRUCTS / FEATS rows fit the 34-letter column
(schema.lua max_len; a 41-letter name was cut and "Unable to find model"). Name words WPN / VWPN / STRCT / DOOR don't
count when picking a base (the flare gun had matched C4 on "WPN"). Slots +0x004 (register_animation: Desert Storm's
registrar, DS2's own SCRIPTED_ANIM_n states loaded with load_animation for play_scripted_animation; the .prs the DLL
names that Desert Storm lacks are ported by mission_files - Wave_Weapon), +0x018, +0x358 (= DS1 +0x180 inverted),
music / view-effect slots as no-ops. Checked in game: Training 3, Mission 1, Mission 2 run with no game errors and no
calls on missing objects.
More slots (CDS II.exe, REA/Ghidra + capstone): +0x2F8 scripted_anim(soldier, n, start) = movement rule
MSR_SCRIPTED_ANIM_n_START 0x6A+n-1 / _STOP 0x6F+n-1 -> DS1 play_scripted_animation +0x52C with the animation loaded
for n (stop = 0); +0x9C4 holster (0x47EFF0(0), DS2's hold-item switch) -> DS1 hold_item(-1); +0x6E8 set_medikit_uses
(0x47D250 first category-8 item, 0x49D280 count +0x58) - no-op; +0x8A0 play_object_sound(obj, sound, bank, flag) ->
DS1 play_sound(bank, sound) (not positional). Training 3 now uses no unmapped slot. Then: +0x1F0 = DS1 set_target_scanning +0x1A0
(0x495FA0: soldier +0x27C bit 0x400 + refresh), +0x478 = is_difficulty_hard +0x3A8 (difficulty object [0x680128] +4),
+0x4A4 squad_in_region_precise -> squad_in_region +0x3D0 (DS2 tests body points), +0x4E4 squad flag 0x1000 (no DS1
counterpart). Mission 2's nine unnamed slots named (api_slots.json, mostly low confidence): stop_object_sound 0x138,
two DS2-only soldier flags at +0x105C (0x684 / 0x690), play_object_sound_at 0x89C, load_keyframe_anims 0x9D4 +
0x9D0 (DS2's key-frame object animations - Desert Storm has no such system), and four unknown (0x2B4, 0x528, 0x7A0).
Queries 0x26 / 0x37 still have no counterpart. Next: a play-through
with input, the remaining DS2 missions; then stage 2 (register arguments).

**Converted campaigns (user decision 2026-10-24).** Converted missions form their own campaign: SINGLE PLAYER's
DESERT STORM CAMPAIGN becomes CAMPAIGNS once a converted campaign is installed - DESERT STORM 1 (the original),
DESERT STORM 2, ... They also work online, but are never synced like mods: every PC converts from its own copy of the
source game (Blender add-on, the coming installer, or the editor - one shared converter, writing one shared asset
store that modders' maps can build on). So: output outside the synced Mods set, a manifest (campaign, mission order,
source game + exe hash, converter version, content fingerprint), the online check comparing that fingerprint and
telling a PC without it to convert (no download offered). Builds on DesertStormFix's custom campaigns (campaigns.cpp,
state 0x45) and the online compat check (onlinecompat.cpp).

**As built (2026-10-24):** `crossmission.convert_campaign` + `cli/ds_convert_campaign.py ds2 [--levels ...]` write
`<Desert Storm>\PSE\Ports\ds2` (levels, adapted stage-1 Lua with each successor baked in, files the DLLs ask for,
campaign.txt, port.txt; MISSION01-02 in ~25 s). DesertStormFix indexes Ports like mods (never in the synced mod
list; online they're compared as `port:<id>` plugin entries), relabels DESERT STORM CAMPAIGN to CAMPAIGNS (single
player and the co-op menu - split screen and online; a converted / custom campaign skips the co-op mission list).
Mission texts are a port-only section `[DS2_M01]` (DS2's load_level_strings 0x080 asks for "DS2_M01Strs.loc"; plain
"[M01]" would have overridden Desert Storm's own Mission 1 texts); DesertStormFix's mission-text lookup (0x4F5E80,
show_movie_text / subtitles) falls back to the `.strings`. Verified in game: menus (SP + 2-player split), DS2
Mission 1 starts, movie texts resolve ("Al-Hadar, Northern Iraq"), Mission 1 completed (debug complete-mission) ->
end screens -> Mission 2 plays. A source level without its loading picture (DS2 Mission 1) keeps the source's name and
shows none. Whole campaign (2026-10-24): all ten missions convert (~1 min, 207 MB) and start / play in the smoke
test (`ports_smoke_test.py`, 25 s each) after slot mapping round 2 (scratchpad ds2slots_A/B/C.json -> DS2
api_slots.json), the 0x12 / 0x13 message fix, `.SI` objectives (scriptinfo.parse_objectives -> OBJECTIVES_LUA) and
spawn points (parse_spawns -> SPAWNS_LUA). Open: objective / spawn state in saves; DS2 areas (per-area spawn budget,
DS1 zone roster slot 0x48); soldier gear (DesertStormFix must add models to the connector-object list 0x5F9FF0);
pick-up / evacuate objective types (unused by the ten); a played-through mission; online session with a port.

## Order

1. Part 1 for props / weapons / animations into DS1 (recipe format + batch + report), with recipes in place from the
   start so the no-data rule holds.
2. Part 2 tables + texts for DS2 -> DS1 (what Part 1's weapons and Part 3's units need).
3. Part 3 with one DS2 level -> DS1.
4. Part 4 route 2 (rules from `.SI`) for that level, then route 1 for its mission DLL.
5. Widen: characters / vehicles from every game, then The Great Escape, Vietnam, Global Storm levels.

## Open questions for you

1. Ports as player-side recipes (both games installed, nothing of the source game shared) - acceptable? Any ports
   meant only for your own machine?
2. Which first level? (Suggestion: a DS2 mission with a small map, so the whole chain can be checked quickly.)
3. Should ported content keep the source look (textures, lighting) or be restyled to DS1's desert palette?
4. Denied Ops: in scope later, or out (its assets live in APKG packages and it has no patch)?
5. The other direction (DS1 into the newer games) waits until their patches load mods - VietnamFix's Save as mod
   already works in game; when should Vietnam become a target?
