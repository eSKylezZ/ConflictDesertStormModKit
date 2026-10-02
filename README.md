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

**New to modding?** Start with [Making a mod, step by step](docs/MAKING_MODS.md): your first uniform and weapon
without any programming, then a recipe for everything a mod can do (21 of them, each with a working example).

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
- **Blender exporter** (File › Export): new geometry for a game model - weapons, props, vehicle parts and
  character bodies - plus its new textures as `.dds`, and animations (`.prb`), loaded by the game through the DesertStormFix plugin's mod
  folders.
- **Sound tools**: game sound banks to WAV and back.
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
| Helpers | Also import attachment points, collision and shadow meshes into a hidden `helpers` collection |
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

**Characters**: import a soldier (e.g. `frontend/HERO01_ARMSTRONG.EVO`), edit the body mesh (`NEWSKIN`) -
reshape it, sculpt a new head - keeping it weighted to the skeleton, and export it under the same name
(`HERO01_ARMSTRONG.evo` replaces Bradley's body in a mod folder). The skin keeps the game's UV layout, because the
uniform/face textures are picked by the game (`.skin` files of the DesertStormFix plugin add new ones). Up to 4
bones per vertex; keep the body one connected mesh.

### 4. Add animations to a character

Select the character's skeleton (the `…_rig` object), then **File › Import › Conflict: Desert Storm Animation
(.prb)** and pick one or more `.prb` files, e.g. `PRONE_AIM_RIFLE.PRB`. Each becomes an action on the skeleton.

### 5. Export an animation

Select the character's skeleton with the action you made or edited (e.g. an imported `upright_relaxed_with_rifle`
with the head turned) and **File › Export › Conflict: Desert Storm Animation (.prb)**. The action is sampled at
**Keys per Second** (filled in from the imported animation; the game's own use 3-15). Walk and run cycles keep the
footstep events of the game animation with the same name (or the one picked in **Footsteps From**).

Name the file like a game animation to replace it (in a DesertStormFix mod folder), or give it a new name and
use it from a `.weapon` file (`reload animation = MY_RELOAD`).

## Example mods

[examples/](examples/README.md) has small, commented example mods for the DesertStormFix plugin's `Mods` folder:
new uniforms and recruits (portraits, a uniform with its own body, raw table rows), new weapons (numbers, textures,
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
```

Sounds (the level sound caches `mission1.sch` … next to `DesertStorm.exe`):

```
python cli/ds_sound.py list mission3.sch                          # banks and their samples
python cli/ds_sound.py wav mission3.sch M16A2 out/                 # a gun's click, tail and shots as WAV
python cli/ds_sound.py pack mission3.sch M16A2 M16.sch --rename MYGUN   # bank pack for a mod folder
python cli/ds_sound.py encode my_shot.wav check.wav                # hear the game's ADPCM on your sound
```

Gun banks are named in `Weaps.txt` column 64 (`M16A2`, `SAW-LIGHTMG`, `MP5SILENCEDSUBMG` …); each refers to four
samples: trigger click, distant tail, shot 1, shot 2. With the DesertStormFix plugin a mod usually needs none of
this - a `.weapon` file's `shot sound = my.wav` takes a WAV directly.

## Limitations

- Export needs a game model to base the new one on (attachment points, skeleton); animations have to fit the
  game's 51-object soldier skeleton.
- Two animations have no known name and import as `anim_<hash>`.
- The game's detail textures and reflection effects aren't recreated; only base textures and normal maps.
- Some gear pieces (helmets, pouches) get their texture at run time and may import untextured.

## License

MIT, see [LICENSE](LICENSE). Conflict: Desert Storm and its assets belong to their respective owners; this
project contains no game data.
