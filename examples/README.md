# Example mods

Small, commented example mods for the [DesertStormFix](https://github.com/eSKylezZ/ConflictDesertStormPatch)
plugin's `Mods` folder that between them use everything it can do. Copy one, change it, and you have your own. New to
modding? [Making a mod, step by step](../docs/MAKING_MODS.md) walks through your first uniform and weapon, and has a
recipe for everything a mod can do.

The text files (`.skin`, `.weapon`, tables) are in `mods/`. The textures, pictures, models, animations and sounds
next to them are made from **your own copy of the game** by `build_examples.py`, so this repository contains no game
files - build once and every example is complete.

## Build and install

```bat
python examples/build_examples.py --blender "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" --install
```

- Finds the game from the registry (or pass `-g "D:\Games\Conflict - Desert Storm"`).
- Output goes to `examples/build/Mods`, one complete folder per example. `--install` also copies them into
  `<game>\Mods`.
- `--blender` (Blender 3.6 or newer) is needed for the examples with models or animations: SilencedSAW, LoudMP5,
  SpeedReload, BigHeadUniform and the four `_` examples. Without it those are left out and the rest is still built.

Folders starting with `_` are switched off: they **replace** something in the game for everyone (Bradley's body, the
run animation …). Remove the `_` (or switch them on in the game's MODS menu) to try one.

Everything is picked on the **Squad Loadout** screen at the start of a mission: new uniforms on the UNIFORM row (the
soldier stays who he is), new recruits on the SOLDIER row, new weapons on the MAIN WEAPON / SIDEARM rows.
`Mods\DesertStormFix-report.txt` (written at every start) says what each mod added.

## What each example shows

| Mod | What you get | What it shows | Its files (recipe for making your own) |
| --- | --- | --- | --- |
| **RussianConnors** | Connors in Spetsnaz kit: UNIFORM "SPETSNAZ" | A uniform from a texture already in the game - the mod is one text file | `Spetsnaz.skin` ([4.4](../docs/MAKING_MODS.md#44-a-uniform-from-a-texture-already-in-the-game-russianconnors)) |
| **ExtraRecruit** | A new SAS recruit for Jones, "Declan Okafor" (SOLDIER row) | Raw table rows (experts) | `CSkins_SAS_C.txt`, `fNames.txt`, `sNames.txt` ([4.21](../docs/MAKING_MODS.md#421-add-rows-to-a-game-table-extrarecruit)) |
| **PinkSAS** | A pink uniform for Bradley: UNIFORM "SAS (PINK)" | `.skin` with `uniform name`: a new uniform for a soldier, with its own portrait | `PinkSAS.skin`, `SASPink_01.dds`, `PinkPortrait.dds` ([2](../docs/MAKING_MODS.md#2-your-first-mod-a-new-uniform), [4.2](../docs/MAKING_MODS.md#42-a-portrait-for-the-soldier-panel-pinksas)) |
| **WinterDelta** | Winter camouflage for Foley: UNIFORM "US DELTA (WINTER)" | A Delta uniform, the soldier named by role | `WinterFoley.skin`, `DeltaWinter_04.dds` ([2](../docs/MAKING_MODS.md#2-your-first-mod-a-new-uniform)) |
| **BigHeadUniform** | Bradley in olive with a bigger head: UNIFORM "SAS (BIG HEAD)" | `.skin` with `body`: a uniform that brings its own body mesh, only while it's worn | `BigHeadUniform.skin`, `BigHead_UK.dds`, `BIGHEAD_BRADLEY.evo` ([4.5](../docs/MAKING_MODS.md#45-a-uniform-with-its-own-body-bigheaduniform)) |
| **GoldenEagle** | A gold Desert Eagle that fires with the Barrett's sound | `.weapon`: `texture`, `hud icon`, a `sound` borrowed from another weapon, damage / magazine / hearing range | `GoldenEagle.weapon`, `GoldEagle.dds`, `GoldEagle_Icon.png` ([4.8](../docs/MAKING_MODS.md#48-a-new-look-textures-also-for-extra-parts-goldeneagle-tigersaw), [4.9](../docs/MAKING_MODS.md#49-a-matching-hud--inventory-picture-tigersaw-goldeneagle-silencedsaw)) |
| **TigerSAW** | A tiger-striped SAW firing 1000 rounds a minute from a 200-round belt | `.weapon`: textures for a two-part model, `hud icon`, own gunshots, `muzzle flash`, rate of fire, `column N` | `TigerSAW.weapon`, `TigerSAW.dds`, `TigerAmmoBox.dds`, `TigerSAW_Icon.png`, `tiger_shot1.wav`, `tiger_shot2.wav`, `tiger_tail.wav` ([4.8](../docs/MAKING_MODS.md#48-a-new-look-textures-also-for-extra-parts-goldeneagle-tigersaw), [4.11](../docs/MAKING_MODS.md#411-your-own-gunshots-tigersaw)) |
| **SilencedSAW** | M249 SAW (Silenced), blue, with a suppressor on the model and in its picture | `.weapon`: `silenced`, own gunshot, a model made in Blender, `hud icon` | `SilencedSAW.weapon`, `MYSAW.evo`, `SAW_Blue.dds`, `SAW_Silenced_Icon.png`, `shot.wav` (and `SAW_Red.dds` for its commented-out `texture` line) ([4.12](../docs/MAKING_MODS.md#412-silenced-or-unsilenced-silencedsaw-loudmp5), [4.13](../docs/MAKING_MODS.md#413-a-new-model-silencedsaw-loudmp5)) |
| **LoudMP5** | The MP5SD without its silencer, with a shotgun's bang | A model with a part taken off in Blender, `hud icon`, a sound bank shipped as a `.sch` bank pack | `LoudMP5.weapon`, `MP5BREACHER.evo`, `Breacher_Icon.png`, `BREACHER.sch` ([4.10](../docs/MAKING_MODS.md#410-borrow-another-gunshot-goldeneagle-loudmp5), [4.13](../docs/MAKING_MODS.md#413-a-new-model-silencedsaw-loudmp5)) |
| **SpeedReload** | A Colt Commando that reloads twice as fast | `.weapon` with new animations made in Blender | `SpeedReload.weapon`, `UPRIGHT_RELOAD_FAST.prb`, `PRONE_RELOAD_FAST.prb` ([4.14](../docs/MAKING_MODS.md#414-new-animations-speedreload)) |
| **_BigHead** | Bradley with a bigger head, in every uniform | A character body replaced by name | `HERO01_ARMSTRONG.evo` ([4.6](../docs/MAKING_MODS.md#46-change-a-soldiers-body-for-everyone-_bighead)) |
| **_HazardDrums** | Yellow-and-black oil drums in Mission 1 | A prop replaced by name, with a new texture saved by the Blender exporter | `OILDRUM.evo`, `HazardDrum.dds` ([4.19](../docs/MAKING_MODS.md#419-replace-a-prop-or-vehicle-model-_hazarddrums)) |
| **_HeadTilt** | Soldiers standing idle with a tilted head | An animation replaced by name | `upright_relaxed_with_rifle.prb` ([4.20](../docs/MAKING_MODS.md#420-replace-a-game-animation-_headtilt-_bouncyrun)) |
| **_BouncyRun** | A bouncing rifle run | A walk / run cycle replaced by name; it keeps the game's footstep sounds | `UPRIGHT_RELAXED_RUN_WITH_RIFLE.prb` ([4.20](../docs/MAKING_MODS.md#420-replace-a-game-animation-_headtilt-_bouncyrun)) |

A `.skin` file makes a **uniform** when it has `uniform name = ...`: the texture is the soldier's own layout, face
included, so it's the same man in different clothes. Leave out `uniform name` and give a `first name` / `last name`
to add a **recruit** instead: another man, with his own face, on the SOLDIER row.

## How the files are made

`build_examples.py` does in code what you would do by hand (the recipes in the guide):

- **Uniform textures**: the soldier's texture (`HERO01_UK_01`, `HERO04_US_01` … in `chardata.dat`) recoloured, with
  the face, hair and hands left alone, saved as a 512 × 512 DXT1 `.dds`.
- **Portraits**: 64 × 64 DXT3 `.dds` (`python cli/ds_texture.py dds face.png --format DXT3`).
- **Weapon textures**: the gun's own texture recoloured, same size (`LMG01_M249SAW` 128 × 32, its ammo box 32 × 32,
  the Desert Eagle 64 × 32).
- **HUD / inventory pictures** (`hud icon = X` → `X.png`): the game's picture of the gun
  (`python cli/ds_texture.py icon US_WPN_SAW_lightmg SAW.png`) recoloured like the gun, with the Silenced SAW's
  suppressor drawn on and the Breacher's cut off. One pixel is one pixel of the game's 800 × 600 HUD.
- **Gunshots**: synthesised 16-bit WAVs - record or download your own. To start from the game's sounds:
  `python cli/ds_sound.py wav mission3.sch M16A2 out/`.
- **Sound bank pack** (LoudMP5): `python cli/ds_sound.py pack mission3.sch REMMINGTON870 BREACHER.sch --rename BREACHER`.

`blender_examples.py` runs inside Blender (started by `build_examples.py`) and does the Blender steps:

- **MYSAW.evo** (SilencedSAW): import `LMG01_M249SAW.EVO`, add a cylinder at the muzzle, give the gun a new image,
  File › Export › Conflict: Desert Storm (.evo). The exporter writes `SAW_Blue.dds` next to it.
- **MP5BREACHER.evo** (LoudMP5): import `SMG01_MP5SD3.EVO`, pull everything past the handguard back to it (the
  suppressor flattens to nothing - the model is low-poly, so deleting it would take the receiver's long top face
  too), add a short thin barrel, export under the new name.
- **HERO01_ARMSTRONG.evo** (_BigHead): import the soldier, scale the head vertices of the body mesh, export under the
  same name. **BIGHEAD_BRADLEY.evo** (BigHeadUniform): the same body under a new name - a body must be exported from
  that soldier's own body so its skeleton fits.
- **OILDRUM.evo** (_HazardDrums): import the drum, swap its image for the new one, export under the same name.
- **upright_relaxed_with_rifle.prb** (_HeadTilt): import the idle onto the soldier's skeleton, turn the head bone in
  every key, File › Export › Conflict: Desert Storm Animation.
- **UPRIGHT_RELAXED_RUN_WITH_RIFLE.prb** (_BouncyRun): lift the root bone in each stride. Because the file keeps the
  game's name, the exporter copies the game animation's footstep events.
- **UPRIGHT_RELOAD_FAST.prb / PRONE_RELOAD_FAST.prb** (SpeedReload): the game's reloads exported at twice the scene
  frame rate, so they play in half the time. The `.weapon` file's `reload time` is shortened to match.

## Rules worth knowing

- A weapon that looks different gets a matching picture (`hud icon`); a silenced version gets a model with a
  suppressor, and an unsilenced version of a silenced gun a model without one. The report notes silenced weapons
  without their own model and restyled ones without their own picture.
- Every file is found by its **name**, so new textures, models and animations need names of their own. A file with a
  game file's name replaces it in every level (the `_` examples).
- Tables (`.txt`) hold only your new rows. They are added to the game's rows and to other mods' rows, and they
  can't hold comments.
- Keep textures DXT-compressed and no bigger than the game's own.
- The game keeps only one weapon each of a few kinds. A weapon based on the M16A2 becomes a plain assault rifle
  (only the M16A2 has the grenade launcher), and new weapons can't be based on the frag grenade.
- `magazines` = what the Squad Loadout screen hands out, the one in the gun included (the HUD shows the spares).
