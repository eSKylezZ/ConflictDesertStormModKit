# ConflictDesertStormModKit

Tools for modding the PC version of Conflict: Desert Storm: unpack the game's archives and bring its models,
characters and animations into Blender or any program that reads glTF/OBJ.

Companion to [ConflictDesertStormPatch](https://github.com/eSKylezZ/ConflictDesertStormPatch) (widescreen,
controllers, split screen and more for the game itself).

You need your own copy of the game (Steam or GOG). No game files are included here.

## Download

- **Blender add-on** (ready to install in Blender): [Nexus Mods](https://www.nexusmods.com/games/conflictdesertstorm/mods/2)
- **DesertStormFix plugin** (the compiled `dinput8.dll` that loads mods):
  [Nexus Mods](https://www.nexusmods.com/games/conflictdesertstorm/mods/1)

What's new in each version: [CHANGELOG.md](CHANGELOG.md).

**New to modding?** Start with [Making a mod, step by step](docs/MAKING_MODS.md): your first uniform and weapon
without any programming, then a recipe for everything a mod can do (28 of them, most with a working example), and
your own missions and levels.

The level editor's missions, and a few newer mod features, need **DesertStormFix 3.0 or later**.

## Features

- **Extract the game archives** (`*.dat`): meshes, textures, animations, vehicle data and tables, with the
  original file names recovered for most files. Optional PNG copies of all textures.
- **Blender importer** (File › Import):
  - **Models** (`.evo`): props, buildings, world pieces and vehicles with their full part hierarchy
  - **Vehicles come assembled**: tracks, road wheels and guns are attached where the game puts them
  - **Characters**: a skeleton, the skin bound to it with the game's weights, and the right uniform texture
  - **Animations** (`.prb`): imported as Blender actions (288 of them fit the soldiers, a few the goats)
  - Textures and normal maps set up automatically; flags and tank tracks get their animation frames as
    shape keys
  - **Level scenes** (`.ENV`): a whole level - ground, buildings, props, kit pieces, soldiers and vehicles, lights,
    trigger regions and ambient sounds - at their places
- **Blender exporter** (File › Export): new geometry for a game model - weapons, props, vehicle parts, level
  pieces and character bodies - plus its new textures as `.dds`, and animations (`.prb`), loaded by the game through
  the DesertStormFix plugin's mod folders. Also exports what the model **collides** with (with the surface of every
  face), its **shadow mesh**, its **points** (muzzle, shell ejection, ladder top …) and **vertex colours**; checks the
  model for what the game would trip over, and can write straight into a mod folder.
- **Level editor** (`editor/`, runs in your browser): missions from start to finish - on a campaign level or a new
  level of your own with a sculpted ground, roads and level pieces; place soldiers, vehicles and props; patrol paths,
  trigger regions and cameras; objectives and event rules without code; cutscenes; game tables and texts. **Play**
  starts the game straight into the mission, and a saved mission holds only your changes, no game files. Needs
  DesertStormFix 3.0 or later. See [editor/README.md](editor/README.md).
- **Sound tools**: game sound banks to WAV and back, the soldiers' voice lines (SAS or Delta actors), cutscene and
  objective sound, and better sound effects from a GameCube disc you own.
- **Command-line converter** to glTF (`.glb` / `.gltf`) or `.obj`, with the same features, including
  animations in glTF.
- Pure Python, no extra packages to install.

## Blender add-on

Works in Blender 3.6, 4.2 and 5.x.

1. Download `conflict_ds_tools-<version>.zip` from NexusMods.
2. In Blender: **Edit › Preferences › Add-ons**, then the **▾** menu at the top right › **Install from Disk…**,
   and pick the zip. (In Blender 3.6: **Install…** on the Add-ons page, then tick the add-on.)

### 1. Extract the game

**File › Import › Conflict: Desert Storm Archives (extract)**

The game folder is filled in automatically when the game is installed; pick an output folder and press OK.
Extraction runs in the background (about a minute) with a progress bar in the status bar at the bottom of
the window. You can keep working meanwhile, or press **Esc** to cancel.

The output has one folder per archive (`chardata`, `frontend`, `mission1` …). Files whose name the game
doesn't store are named after their hash, e.g. `_867B1D52.DDS`; the importer still finds them.

**Better sound effects from a GameCube disc you own**: **File › Import › Conflict: Desert Storm GameCube Audio
(.iso)** (or **GameCube Audio...** in the sidebar's Game box), pick your GameCube disc image (`.iso`, `.gcm` or
`.ciso`). Every sample the GameCube keeps at a higher rate than the PC (11 kHz → 20 or 44.1 kHz) is re-encoded for the
PC into the mod `<game>\Mods\GameCubeAudio`, one replacement sound cache per level. It runs in the background with a
progress bar (all levels: about a minute and a half; **Levels** limits it, e.g. `mission1`), **Esc** cancels. Switch the
mod on and off in the game's MODS menu. Nothing from the disc ships with the kit: everyone converts their own copy.

Tick **Editable Models (.blend)** to also get every model as its own Blender file, in `_blend\<archive>\` next to
the extracted folders (about 2,300 models, roughly 20 minutes; **Include Level Pieces** adds the level geometry). Each
one opens textured, with its collision, shadow mesh and points as helpers, and exports straight back to the game
(**Based On** is already filled in). Keep the `_blend` folder next to the archive folders: the textures are linked
by relative path.

### 2. Import a model

**File › Import › Conflict: Desert Storm (.evo)** and pick a file from the extracted folders, for example:

| Try | File |
| --- | --- |
| A soldier | `frontend/HERO01_ARMSTRONG.EVO` (also `HERO02_RAMIREZ`, `HERO03_JONES`, `HERO04_FOLEY`) |
| A tank | `outro/T72M1.EVO`, `mission1/BMP2.EVO`, `mp_mission4/VHABRAMS_M1A1.EVO` |
| A helicopter | `frontend/BLACKHAWK_UH60.EVO` |
| A weapon | `mission1/LMG01_M249SAW.EVO` |

Textures, other vehicle parts and animations are looked up in the model's folder and the other extracted
folders, so keep the extracted folders together.

Options in the import dialog:

| Option | What it does |
| --- | --- |
| Scale | Game units are centimetres; the default 0.01 imports in metres |
| Armature | Characters: build the skeleton and bind the skin to it |
| Animations | Characters: **All Compatible** imports every animation as an action (see the Action Editor) |
| Vehicle Parts | Attach the tracks, wheels and guns the game adds at run time |
| Normal Maps | Add the game's normal maps to the materials |
| Helpers | Also import the collision (`#BSP_`, one material per surface), shadow meshes (`#SHA_`), points (`#PT_`: muzzle, shell ejection, ladder ends), attachment points and hit regions into a `helpers` collection |
| Cloth Frames as Shape Keys | Flags and tank tracks: one shape key per animation frame |
| Character Skin | Uniform texture for soldiers, e.g. `HERO01_US_01` (default: the UK one) |
| Extra Folder | Another folder to search for textures and parts |

### 3. Export a model for the game

Make new geometry for a game model - a weapon with a suppressor, a different prop - and export it:
**File › Export › Conflict: Desert Storm (.evo)**.

1. Import the model it replaces (e.g. `mission1/LMG01_M249SAW.EVO`) and edit it, or add your own meshes.
2. Select the meshes and export. **Based On** is filled in with the imported model; it keeps the game's
   attachment points (hands, muzzle), materials and part layout, so the new model works wherever the old one did.
   Models with several parts (vehicles) match your objects to their parts by name; a one-part model (weapons,
   most props) takes every selected mesh.
3. The file name is the new model's name, e.g. `MYSAW.evo`. Put it in a mod folder of the
   [DesertStormFix](https://github.com/eSKylezZ/ConflictDesertStormPatch) plugin and use it from a `.weapon` file
   (`model = MYSAW`).

Textures are referenced by name: each face uses its material's image name (without extension). With **Save
Textures** (on by default) every image that isn't one of the game model's own is saved next to the model as a
`.dds` the game reads (DXT1, DXT5 when it has transparency) - use power-of-two sizes (128, 256, 512 …).

**Collision, shadows and points**: import with **Helpers**, edit the `#BSP_` (collision), `#SHA_` (shadow) and
`#PT_` (point) objects and select them with the model when you export (option **Collision**: from these objects, from
the model's own mesh, keep the original's, or none). The **Desert Storm** tab of the 3D View's sidebar (N) assigns
collision surfaces to faces, makes a collision or shadow mesh from the active mesh (box, convex hull, copy) and adds
points. Details: [docs/MAKING_MODS.md](docs/MAKING_MODS.md) 4.19b / 4.19c.

Export options: **Into a Mod Folder** writes into `<game>\Mods\<Mod>` (a new weapon or body gets a starter
`.weapon` / `.skin`), **Replace the Game's Model** saves under the original's name, **Check Only** reports problems
without writing.

**Brand-new props**: set **Based On** to **Auto / Generic Prop** (or just export with nothing to base it on). The
model is built on the game's small `BH_BODGEPLATE` prop, taken from your extracted folders or read straight out of
your own game archives - nothing of the game ships with the add-on. It collides with its own mesh unless you add a
`#BSP_` object. The model's origin is Blender's world origin.

**Sidebar (N) › Desert Storm** also has:

- **Export to Mod** - one click re-exports the selection into the active mod with the last export's options
  (the mod and model name fill in after an export Into a Mod Folder; both can be typed).
- **Model check** - vertices / triangles of the active mesh, export vertices per texture against the game's 65535,
  images that aren't powers of two or are over 1024, collision triangles (keep under 3000).
- **Show Collision Surfaces** draws `#BSP_` objects solid, one colour per surface; in Edit Mode the eyedropper next to
  the surface list picks the active face's surface.
- **Check Skin Weights** (character meshes) - keeps the 4 strongest bones per vertex, normalises, selects vertices
  with no weight and lists vertex groups that aren't bones.
- **Generate Road** (a curve is active) - a textured strip along the curve plus its walkable `#BSP_` collision
  (Tarmac, Baked Earth …); export both with Auto / Generic Prop.
- **Game** - **Test in Game** exports the selection into the active mod (like Export to Mod) and starts
  `DesertStorm.exe`, straight into the **Level** typed there (`Mission1.dll`, or a mod mission such as `Ambush.lua`;
  empty = the launcher). It refuses while the game is already running, since the game reads mods when it starts.
  **GameCube Audio...** is the GameCube sound conversion below.
- **Level** - **Import Scene**, **Bake Minimap** and **Generate Asset Library** (below).
- **Weapon** - **Aim Camera** looks down the sights: add **Rear Sight** and **Front Sight** points (Add Point, 3D
  cursor on each sight; they stay in Blender and aren't exported) and the camera sits on the rear sight looking over
  the front one (or the muzzle point). **Snap to Hand** puts the weapon in a soldier's right hand in the current pose
  to check the grip; press it again to put the weapon back.
- **Vehicle** - **Rig Turret / Gun** adds pivots where the game turns the turret and gun (the gun rides on the
  turret, attached guns follow) with limits (turret all round, gun elevation 20° up / 8° down - the game's tables give
  no limits, so set your own). Sliders turn them; **Reset Vehicle Pose** before exporting (the export warns otherwise).
- **Soldier** - **Uniform** shows the selected squad soldier in another uniform: the game's (SAS, Delta, Iraqi,
  Russian, from the extracted `chardata`) and the `.skin` uniforms of installed mods. A viewport preview only.

**Characters**: import a soldier (e.g. `frontend/HERO01_ARMSTRONG.EVO`), edit the body mesh (`NEWSKIN`) -
reshape it, sculpt a new head - keeping it weighted to the skeleton, and export it under the same name
(`HERO01_ARMSTRONG.evo` replaces Bradley's body in a mod folder). The skin keeps the game's UV layout, because the
uniform/face textures are picked by the game (`.skin` files of the DesertStormFix plugin add new ones). Up to 4
bones per vertex; keep the body one connected mesh.

**Level scenes**: **File › Import › Conflict: Desert Storm Scene (.env)** and pick a level's scene, e.g.
`mission1/MISSION1.ENV` (or an editor mission's `.envpatch` in its mod folder; its base scene comes from the extracted
folders). Every model is imported once into a hidden `<LEVEL> models` collection and placed as collection instances,
so a level stays light (Mission 1 with its ground: about 30 seconds). Kit pieces placed with the level editor are cut
out of their source model like the plugin does. Lights become Blender lights (energy is a guess from each light's
reach), trigger regions wireframe boxes / spheres named like the region, ambient sounds empties whose outer sphere is
where they fall silent. Every object keeps its scene line (`ds_line`) and model as custom properties. Turn off **World
Pieces** for a quick look at the props.

**Bake Minimap** renders the level from straight above into the active mod as the in-game tactical map
(`GW<Name>0.png` + its `GW<Name>.img` sheet). The bounds are filled in for the shipped missions; a mission script
`<Name>.lua` in the mod is pointed at the new map, otherwise the report gives the two lines to add.

**Generate Asset Library** saves every extracted model as an editable `.blend` (like Editable Models on extraction,
which now does the same) marked as an asset, sorted into the catalogs Vehicles, Weapons, Characters, Structures, Kit
Pieces and Props, and adds the `_blend` folder as an asset library (save the Preferences to keep it). Drag models from
the Asset Browser into any scene. Previews aren't made in the batch: right-click in the Asset Browser › Generate
Preview.

### 4. Add animations to a character

Select the character's skeleton (the `…_rig` object), then **File › Import › Conflict: Desert Storm Animation
(.prb)** and pick one or more `.prb` files, e.g. `PRONE_AIM_RIFLE.PRB`. Each becomes an action on the skeleton.

### 5. Export an animation

Select the character's skeleton with the action you made or edited (e.g. an imported `upright_relaxed_with_rifle`
with the head turned) and **File › Export › Conflict: Desert Storm Animation (.prb)**. The action is sampled at
**Keys per Second** (filled in from the imported animation; the game's own use 3-15). Footstep events are timeline
markers on the action (shown in the Action Editor with **View › Show Pose Markers**): the import makes them from the
game's (`FS_LEFT` / `FS_RIGHT` for a walk step, `FS_LEFT_25` / `FS_RIGHT_25` for a run step, `EVENT_2` / `EVENT_3`
on stance animations) and the export writes the markers back - move them, or add them to a new cycle. Without
markers, walk and run cycles keep the footsteps of the game animation with the same name (or the one picked in
**Footsteps From**).

Name the file like a game animation to replace it (in a DesertStormFix mod folder), or give it a new name and
use it from a `.weapon` file (`reload animation = MY_RELOAD`).

## Example mods

[examples/](examples/README.md) has small, commented example mods for the DesertStormFix plugin's `Mods` folder:
new uniforms and recruits (portraits, a uniform with its own body, normal map or voice, raw table rows), new weapons (numbers, textures,
pictures, sounds, a sound bank pack, a Blender model, new animations) and replacing game models and animations by
name. One command builds every example complete from your copy of the game (this repository holds no game files):

```bat
python examples/build_examples.py --blender "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" --install
```

[Making a mod](docs/MAKING_MODS.md) shows how to make each kind of file yourself.

## Command line

Needs Python 3.8 or newer (numpy is used when installed, which makes PNG conversion faster).

Extract the archives (finds the game from the registry, or pass `-g`):

```
python cli/ds_extract.py -o extracted
python cli/ds_extract.py -g "D:\Games\Conflict - Desert Storm" -o extracted --no-png chardata mission1
```

Convert models:

```
python cli/evo_convert.py extracted/outro/T72M1.EVO -o out/                         # out/T72M1.glb
python cli/evo_convert.py extracted/frontend/HERO01_ARMSTRONG.EVO --anims -o out/     # with all animations
python cli/evo_convert.py extracted/frontend/HERO01_ARMSTRONG.EVO --anims PRONE_AIM_RIFLE,COWER -o out/
python cli/evo_convert.py extracted/mission1 -f obj -o out/mission1                   # a whole folder as .obj
```

| Option | |
| --- | --- |
| `-f glb\|gltf\|obj` | Output format (default `glb`, textures embedded) |
| `--anims [all\|NAMES]` | Characters: add animations (glTF only) |
| `--skin HERO01_US_01` | Uniform texture for soldiers |
| `--helpers` | Also export attachment points, collision and shadow meshes |
| `--no-parts` | Don't attach vehicle tracks, wheels and guns |
| `--no-normal-maps` | Leave out normal maps |
| `--scale 0.01` | Game centimetres → output units (default metres) |
| `--no-embed` | Reference texture files instead of embedding them |

Run either script with `-h` for all options.

Textures (PNG / TGA to the game's `.dds` and back):

```
python cli/ds_texture.py png extracted/chardata/HERO01_UK_01.DDS          # to edit in any paint program
python cli/ds_texture.py dds MyCamo.png                                   # DXT1 (DXT5 with transparency)
python cli/ds_texture.py dds face.png --format DXT3                       # soldier-panel portraits
python cli/ds_texture.py icon US_WPN_SAW_lightmg SAW.png                  # a weapon's HUD / inventory picture
python cli/ds_texture.py normal MyBump.png --opengl                      # a .skin's normal map (TGA; --opengl flips green)
```

Sounds (the level sound caches `mission1.sch` … next to `DesertStorm.exe`):

```
python cli/ds_sound.py list mission3.sch                          # banks and their samples
python cli/ds_sound.py wav mission3.sch M16A2 out/                 # a gun's click, tail and shots as WAV
python cli/ds_sound.py wav mission3.sch Bradley lines/ --accent delta  # a soldier's 47 voice lines (SAS or Delta actors)
python cli/ds_sound.py stream Data/Sound/English/CS_M01_S01.psf scene.wav   # a cutscene's sound to edit
python cli/ds_sound.py psf scene.wav CS_M01_S01.psf                   # back, for a mod's Sound/SAS folder
python cli/ds_sound.py pack mission3.sch M16A2 M16.sch --rename MYGUN   # bank pack for a mod folder
python cli/ds_sound.py encode my_shot.wav check.wav                # hear the game's ADPCM on your sound
```

Gun banks are named in `Weaps.txt` column 64 (`M16A2`, `SAW-LIGHTMG`, `MP5SILENCEDSUBMG` …); each refers to four
samples: trigger click, distant tail, shot 1, shot 2. With the DesertStormFix plugin a mod usually needs none of
this - a `.weapon` file's `shot sound = my.wav` takes a WAV directly.

Better sound effects from a GameCube disc you own (the same conversion as the add-on's **GameCube Audio**):

```
python cli/ds_gc_audio.py "Conflict Desert Storm.iso"                    # into <game>\Mods\GameCubeAudio
python cli/ds_gc_audio.py "Conflict Desert Storm.iso" --levels mission1 --list   # only show what would change
```

It reads the level sound caches from the disc image (`.iso`, `.gcm` or `.ciso`), takes every sample the GameCube keeps
at a higher rate than your PC game, encodes it for the PC and writes one sound cache per level into a mod (`-o` for
another folder, `-g` for the game folder).

## Limitations

- Characters, weapons and vehicles need a game model to base the new one on (attachment points, skeleton); plain
  props don't (they are built on a small generic game prop). Animations have to fit the game's 51-object soldier
  skeleton.
- Parts can only replace ones the original model has (collision, points and - on static models - a shadow mesh can be
  added).
- Game levels keep their AI route maps in files (`<level>.qtd` / `.rmd`): changed level collision stops everyone at
  once, but the computer-controlled soldiers only plan around it once the level editor (`editor/`) redoes the routes.
- Two animations have no known name and import as `anim_<hash>`.
- The game's detail textures and reflection effects aren't recreated; only base textures and normal maps.
- Some gear pieces (helmets, pouches) get their texture at run time and may import untextured.

## License

MIT, see [LICENSE](LICENSE). Conflict: Desert Storm and its assets belong to their respective owners; this
project contains no game data.
