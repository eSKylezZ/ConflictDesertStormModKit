# ConflictDesertStormModKit

Tools for modding the PC version of Conflict: Desert Storm: unpack the game's archives and bring its models,
characters and animations into Blender or any program that reads glTF/OBJ.

Companion to [ConflictDesertStormPatch](https://github.com/eSKylezZ/ConflictDesertStormPatch) (widescreen,
controllers, split screen and more for the game itself).

You need your own copy of the game (Steam or GOG). No game files are included here.

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
- **Command-line converter** to glTF (`.glb` / `.gltf`) or `.obj`, with the same features, including
  animations in glTF.
- Pure Python, no extra packages to install.

## Blender add-on

Works in Blender 3.6, 4.2 and 5.x.

1. Download `conflict_ds_tools-<version>.zip` from the releases (or build it, see below).
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

### 3. Add animations to a character

Select the character's skeleton (the `…_rig` object), then **File › Import › Conflict: Desert Storm Animation
(.prb)** and pick one or more `.prb` files, e.g. `PRONE_AIM_RIFLE.PRB`. Each becomes an action on the skeleton.

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

## Building the add-on zip

```
python scripts/build_zip.py        # dist/conflict_ds_tools-<version>.zip
```

The version comes from `conflict_ds_tools/blender_manifest.toml`.

## Limitations

- Import only for now: models and animations can't be written back to the game's formats yet.
- Two animations have no known name and import as `anim_<hash>`.
- The game's detail textures and reflection effects aren't recreated; only base textures and normal maps.
- Some gear pieces (helmets, pouches) get their texture at run time and may import untextured.

## License

MIT, see [LICENSE](LICENSE). Conflict: Desert Storm and its assets belong to their respective owners; this
project contains no game data.
