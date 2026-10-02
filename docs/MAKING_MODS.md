# Making a mod, step by step

This guide takes you from nothing to your own uniform, then your own weapon, then further. You need:

- Conflict: Desert Storm (Steam or GOG) with the [DesertStormFix](https://github.com/eSKylezZ/ConflictDesertStormPatch)
  plugin installed (`dinput8.dll` next to `DesertStorm.exe`).
- A text editor (Notepad is fine).
- For textures: a paint program that saves `.dds` ([paint.net](https://www.getpaint.net) does it out of the box;
  GIMP 2.10+ too), or this kit's `ds_texture.py`.
- Later, for models and animations: [Blender](https://www.blender.org) 3.6 or newer with this kit's add-on.

Steps 1 and 2 need no programming at all.

## 1. Where mods live

In the game folder, make a folder called **`Mods`**. Each mod gets its own folder inside it:

```
conflict_desert_storm\
  DesertStorm.exe
  dinput8.dll
  Mods\
    MyFirstMod\
      ...your files...
```

- Files can sit in sub-folders; the plugin finds them by name.
- A folder whose name starts with `_` is switched off (`Mods\_MyFirstMod`).
- In the game, **MODS** on the main menu lists every mod folder: switch them on and off there and press **Apply**
  (Tab, or Square / X on a pad). No restart needed for most changes.
- Every time the game starts (and on Apply), the plugin writes **`Mods\DesertStormFix-report.txt`**: what each mod
  added, notes, and problems marked **`[fail]`**. When something doesn't show up, look there first. The main menu
  also says "N PROBLEMS" at the bottom left when there are any.

## 2. Your first mod: a new uniform

You'll give Bradley a new uniform that he can wear from the Squad Loadout screen at the start of a mission.

### 2.1 Get the original texture

Each soldier's uniform is one 512 x 512 texture holding his clothes, face and hands:

| Soldier | SAS texture | US Delta texture |
| --- | --- | --- |
| Bradley | `HERO01_UK_01` | `HERO01_US_01` |
| Connors | `HERO02_UK_01` | `HERO02_US_01` |
| Jones | `HERO03_UK_01` | `HERO03_US_01` |
| Foley | `HERO04_UK_01` | `HERO04_US_01` |

To get them, extract the game's archives once:

- **In Blender** (with the add-on): **File › Import › Conflict: Desert Storm Archives (extract)**, tick **PNG
  Copies**, pick an output folder. Or
- **Command line**: `python cli/ds_extract.py -o extracted` (PNG copies are made by default).

The soldier textures end up in `extracted\chardata\`, e.g. `HERO01_UK_01.DDS` and `HERO01_UK_01.png`.

### 2.2 Paint it

Open the PNG in your paint program and recolour the **clothes**. Leave the face, hair and hands alone, and keep
everything where it is: the texture wraps around the body exactly as laid out. Don't change the size.

Save it as **`MyCamo.dds`** in `Mods\MyFirstMod\`:

- paint.net: **Save As › DDS**, format **BC1 (DXT1)**, no mipmaps.
- GIMP: **Export As › .dds**, compression **BC1 / DXT1**, no mipmaps.
- or save a PNG and convert it: `python cli/ds_texture.py dds MyCamo.png` (writes `MyCamo.dds` next to it).

Use a **new file name**. Using the game's name (`HERO01_UK_01`) wouldn't add a uniform - it would replace Bradley's
normal SAS texture for everyone (see 4.18).

### 2.3 Describe it

Next to the texture, make a text file **`MyCamo.skin`** (check that Notepad doesn't save it as `MyCamo.skin.txt`):

```
uniform      = SAS            # the side it belongs to: SAS or Delta (also Russian, Iraqi)
uniform name = SAS (Jungle)   # what the UNIFORM row shows
soldier      = Bradley        # Bradley, Foley, Connors or Jones
texture      = MyCamo         # the .dds file name, without .dds
```

### 2.4 Try it

Start the game, begin any mission (SINGLE PLAYER › DESERT STORM CAMPAIGN). After the intro the **Squad Loadout**
screen appears: on Bradley's **UNIFORM** row press Left / Right until you see **SAS (JUNGLE)**. Start the mission
to see it in play.

Not there? Open `Mods\DesertStormFix-report.txt` and look for `[fail]` lines that mention your file.

### 2.5 Extras

- **Portrait** for the soldier panel: a 64 x 64 picture saved as DXT3 (`ds_texture.py dds face.png --format DXT3`),
  then `portrait = face` in the `.skin` file.
- **A new man instead of a new uniform** (an extra choice on the SOLDIER row): leave out `uniform name` and add
  `first name = John` and `last name = Smith`. His face is the one painted on your texture.
- **A body of its own**: `body = MYBODY` gives whoever wears this skin the body mesh `MYBODY.evo` from your mod
  folder (see 4: made in Blender from that soldier's own body). Only for this skin - other uniforms keep the normal
  body, and nothing of the game is replaced.

## 3. Your first weapon

A weapon mod is a `.weapon` text file that starts from one of the game's weapons and lists only what changes.

### 3.1 A tougher pistol

Make `Mods\MyGuns\HandCannon.weapon`:

```
id            = US_WPN_HandCannon      # your weapon's own name: letters, digits and _
based on      = US_WPN_DesertEagle     # the weapon it starts as
display name  = Hand Cannon            # shown in the inventory and on the loadout screen
damage        = 35                     # Desert Eagle 20
magazine size = 7
magazines     = 4                      # what the loadout screen hands out, the one in the gun included
```

Start a mission: the **SIDEARM** row of every soldier now offers **Hand Cannon**. Weapons from mods are always
there; the game's own unlock as you find them.

Weapons you can start from: `US_WPN_M16A2`, `US_WPN_M16`, `US_WPN_HKPSG1`, `US_WPN_BarrettSniper`,
`US_WPN_AccuracyInter`, `US_WPN_M60E3`, `US_WPN_SAW_lightmg`, `US_WPN_MP5SilencedSubMG`, `US_WPN_Remmington870`,
`US_WPN_FranchiSPAS`, `US_WPN_SIGP228`, `US_WPN_Beretta92F`, `US_WPN_DesertEagle`, `US_WPN_LAW66`, `US_WPN_LAW80`,
`US_WPN_FIM92`, `US_WPN_GrenadeSmoke` (and the Russian / Iraqi ones such as `IR_WPN_AK47`, `IR_WPN_Makarov`).

Two rules from the game itself:

- Only the M16A2 may have the grenade launcher: a weapon based on it becomes a plain assault rifle.
- There can't be a second frag grenade, so nothing can be based on `US_WPN_GrenadeFrag`.

### 3.2 Make it look and sound different

Add to the same file:

```
texture   = HandCannonGold          # HandCannonGold.dds in the mod folder, same layout and size as the original
hud icon  = HandCannonGold_Icon     # HandCannonGold_Icon.png: the picture in the HUD and inventory
sound     = US_WPN_BarrettSniper    # borrow any weapon's gunshot ...
# shot sound = my_shot.wav          # ... or use your own WAV (16-bit, mono or stereo)
```

- Get the gun's original texture the same way as the uniform: weapon textures are in the extracted `frontend`
  and mission folders (`extracted\frontend\PISTOL01_DESERTEAGLE.png` for the Desert Eagle).
- The HUD picture is a PNG with a transparent background, drawn at 1 pixel = 1 pixel of an 800 x 600 screen
  (the SAW's is 115 x 35). A weapon that looks different should get a matching picture.

All the settings (reload time, range, hearing range, silenced, animations …) are listed in the plugin's
`DesertStormFix-README.txt`, and section 4 has a recipe for each.

## 4. Recipes: everything a mod can do

Each recipe is a complete, working example. A name in brackets is the matching example in
[examples/](../examples/README.md), a finished mod you can copy - `python examples/build_examples.py --install`
builds them all from your copy of the game.

Getting the game's files to start from: extract the archives once (2.1). Below, `extracted\` means that folder.

### Soldiers and uniforms

#### 4.1 A new uniform for a squad soldier (PinkSAS, WinterDelta)

The tutorial in step 2. Soldier names on the `soldier` line: `Bradley`, `Foley`, `Connors`, `Jones` - or by role:
`rifleman`, `sniper`, `heavy`, `engineer`. `uniform` is the side it's listed with: `SAS`, `Delta`, `Russian` or
`Iraqi`.

#### 4.2 A portrait for the soldier panel (PinkSAS)

1. Get the soldier's own portrait to paint over:
   `python cli/ds_texture.py png extracted\chardata\BRADLEYSASPORTRAIT.DDS` (also `FOLEYSASPORTRAIT`,
   `RAMIREZSASPORTRAIT` for Connors, `JONESSASPORTRAIT`; the Delta ones are `BRADLEYPORTRAIT`, `FOLEYPORTRAIT` …).
2. Paint it (64 x 64) and save it as DXT3: `python cli/ds_texture.py dds MyFace.png --format DXT3`.
3. In the `.skin` file: `portrait = MyFace`. Without a portrait line the soldier's own is used.

#### 4.3 A new recruit (another man on the SOLDIER row)

A recruit is a `.skin` file without `uniform name`, with a first and last name. His face is the one on his texture.

```
uniform    = SAS
soldier    = Connors          # he replaces Connors: same body, same kit
texture    = HartleyFace      # HartleyFace.dds - Connors' layout (HERO02_UK_01) with another face painted on
portrait   = HartleyPortrait  # optional
first name = Tom
last name  = Hartley
```

On the loadout screen, Connors' **SOLDIER** row now offers **TOM HARTLEY**. (The ExtraRecruit example does the same
with raw table rows - see 4.21.)

#### 4.4 A uniform from a texture already in the game (RussianConnors)

The game has textures the squad never wears - the multiplayer Russian and Iraqi skins in `extracted\chardata`
(`HERO02_RU_01` … `_07`, `HERO02_IR_01` …; the hero number is the soldier, so the layout fits). A mod can be one
text file:

```
uniform      = Delta           # listed after the Delta uniforms
uniform name = Spetsnaz
soldier      = Connors
texture      = HERO02_RU_01    # the game's own file, nothing to ship
```

#### 4.5 A uniform with its own body (BigHeadUniform)

A body is a mesh made in Blender from the soldier's own body, so it keeps his skeleton and animations:

1. In Blender, **File › Import › Conflict: Desert Storm (.evo)** the soldier's body:
   `extracted\frontend\HERO01_ARMSTRONG.EVO` (Bradley), `HERO02_RAMIREZ` (Connors), `HERO03_JONES` (Jones),
   `HERO04_FOLEY` (Foley).
2. Edit the body mesh (`NEWSKIN`): reshape it, scale the head, sculpt. Keep it one connected mesh, weighted to the
   skeleton (vertices you add need weights too, up to 4 bones each), and don't move the UVs - the uniform texture is
   laid out for them.
3. Select the body and **File › Export › Conflict: Desert Storm (.evo)** as `BIGHEAD_BRADLEY.evo` in your mod folder
   (**Based On** is filled in from the import).
4. A `.skin` with its own texture name plus the body:

```
uniform      = SAS
uniform name = SAS (Big Head)
soldier      = Bradley
texture      = BigHead_UK          # a texture name of its own: the body goes with this texture
body         = BIGHEAD_BRADLEY
```

He wears that body only with this uniform; any other uniform gives him his normal body back. Recruits (4.3) can have
a `body` line too.

#### 4.6 Change a soldier's body for everyone (_BigHead)

Steps 1-3 of 4.5, but export under the game's own name (`HERO01_ARMSTRONG.evo`). Every Bradley in every level uses
it. Folders that replace game files are best shipped switched off (`_` in front of the folder name), so players
choose.

### Weapons

Every weapon recipe is a `.weapon` file: `id` (your weapon's name), `based on` (the game weapon it starts from) and
only what changes. The full list of settings is in the plugin's `DesertStormFix-README.txt`. The loadout row it
appears on follows the weapon it's based on: rifles, machine guns, shotguns and sniper rifles = MAIN WEAPON, pistols
= SIDEARM, rocket launchers = LAUNCHER.

#### 4.7 Different numbers (GoldenEagle, TigerSAW)

```
id                 = US_WPN_SAW_Fast
based on           = US_WPN_SAW_lightmg
display name       = M249 SAW (Fast)
damage             = 18        # per bullet (SAW 15)
time between shots = 0.06      # seconds between shots: 1000 rounds a minute (SAW 0.10)
magazine size      = 200
magazines          = 3         # handed out on the loadout screen, the one in the gun included
reload time        = 3.0       # seconds
range              = 1000      # SAW 850
hearing range      = 120       # how far enemies hear it (normal guns 80, silenced 8)
```

#### 4.8 A new look: textures, also for extra parts (GoldenEagle, TigerSAW)

1. Find the gun's textures: import its model in Blender (e.g. `extracted\mission1\LMG01_M249SAW.EVO`) and look at
   the image names, or look in `extracted\frontend` / the mission folders - the SAW has `LMG01_M249SAW` (the gun)
   and `LMG01_M249AMMOBOX` (its ammo box).
2. Repaint them at the same size (`ds_texture.py png` to get a PNG, `ds_texture.py dds` to convert back).
3. In the `.weapon` file - the main texture, then any other part by its texture name:

```
texture                   = TigerSAW        # TigerSAW.dds: the gun
texture LMG01_M249AmmoBox = TigerAmmoBox    # TigerAmmoBox.dds: the ammo box
```

#### 4.9 A matching HUD / inventory picture (TigerSAW, GoldenEagle, SilencedSAW)

1. Get the game's picture of the gun: `python cli/ds_texture.py icon US_WPN_SAW_lightmg SAW.png`.
2. Repaint it - keep the transparent background; 1 pixel = 1 pixel of the game's 800 x 600 HUD (the SAW's is
   115 x 35). Draw a suppressor on for a silenced version, cut one off for an unsilenced one.
3. Save it as a PNG in the mod folder and name it in the `.weapon` file: `hud icon = TigerSAW_Icon` (without `.png`).

#### 4.10 Borrow another gunshot (GoldenEagle, LoudMP5)

`sound = US_WPN_BarrettSniper` - any weapon id; the sound is found in whichever mission has it. Or a sound bank by
name, `sound bank = BREACHER`, with a bank pack (`.sch`) in your mod folder made from a game bank:

```
python cli/ds_sound.py list mission3.sch                                    # the banks in a level's sound cache
python cli/ds_sound.py pack mission3.sch REMMINGTON870 BREACHER.sch --rename BREACHER
```

The level sound caches (`mission1.sch` …) are next to `DesertStorm.exe`. Every `.sch` in a mod folder is added to
every level.

#### 4.11 Your own gunshots (TigerSAW)

```
shot sound   = tiger_shot1.wav    # WAV in the mod folder: 16-bit, mono or stereo, e.g. 22050 Hz
shot sound 2 = tiger_shot2.wav    # optional second variation (the game picks one at random)
tail sound   = tiger_tail.wav     # optional: the echo heard from far away
```

To start from the game's sounds: `python cli/ds_sound.py wav mission3.sch M16A2 out` saves the M16A2's click, tail
and two shots as WAV files. `python cli/ds_sound.py encode my_shot.wav check.wav` lets you hear how the game's
compression will sound.

#### 4.12 Silenced or unsilenced (SilencedSAW, LoudMP5)

```
silenced = yes       # no muzzle flash, the MP5SD's quiet shot, enemies only hear it up close
```

For the opposite (an unsilenced version of a silenced gun): `muzzle flash = yes`, `hearing range = 80` and a louder
`sound`. Either way the gun should look the part: a model with or without a suppressor (4.13) and a picture to match
(4.9). The plugin's report notes silenced weapons that keep a model without one.

#### 4.13 A new model (SilencedSAW, LoudMP5)

1. **File › Import › Conflict: Desert Storm (.evo)** the gun (`extracted\mission1\LMG01_M249SAW.EVO`).
2. Change it: add a cylinder at the muzzle for a suppressor, pull vertices back to remove one, reshape. New parts
   need a material with an image - the gun's own, or a new image (saved as `.dds` by the exporter).
3. Select every mesh of the gun and **File › Export › Conflict: Desert Storm (.evo)** under a new name (`MYSAW.evo`)
   in your mod folder. **Based On** keeps the hand and muzzle positions, so it fits the soldier's hands.
4. In the `.weapon` file: `model = MYSAW`.

#### 4.14 New animations (SpeedReload)

1. Import the soldier (`extracted\frontend\HERO01_ARMSTRONG.EVO`), select his skeleton (`…_rig`), then
   **File › Import › Conflict: Desert Storm Animation (.prb)** the animation to start from, e.g.
   `UPRIGHT_RELAXED_RELOAD_RIFLE.PRB`.
2. Change it with Blender's animation tools - or make it faster by raising the scene's frame rate before exporting
   (SpeedReload doubles it).
3. With the skeleton selected, **File › Export › Conflict: Desert Storm Animation (.prb)** under a new name
   (`UPRIGHT_RELOAD_FAST.prb`).
4. In the `.weapon` file - any of `reload animation`, `aim animation`, `throw animation` and their `prone` versions:

```
reload animation       = UPRIGHT_RELOAD_FAST
prone reload animation = PRONE_RELOAD_FAST
reload time            = 0.75     # match the animation's length
```

#### 4.15 Which sides use it

`faction = Russian, Iraqi` lists the weapon first on the loadout rows of soldiers wearing those uniforms (everyone can
still pick it). Words: `SAS`, `Delta`, `Russian`, `Iraqi`, `allied` (SAS + Delta), `eastern` (Russian + Iraqi),
`all`. Without a faction line a weapon has its base weapon's.

#### 4.16 Make a game weapon choosable as it is

A `.weapon` file with only the game weapon's own id (no `based on`) puts that weapon on the loadout screen in every
mission, unchanged - for example the Russian and Iraqi guns the squad never finds:

```
id      = IR_WPN_AK47        # no "based on": the game's AK-47 as it is
faction = Iraqi
```

Its model has to be loadable in the mission; where it isn't, ship the game's model files (from the extracted
files) in the mod.

#### 4.17 Anything else in the weapon table

Any column of the game's weapon table (`Weaps.txt`, in `extracted\catalog`) by number, counting from 1 like a
spreadsheet: `column 24 = 1000` (24 is the range). Compare the game's rows to see what a column does.

### The world: replacing what's there

These use a game file's exact name, so they change that thing everywhere. Ship them switched off (`_` in front of the
folder name) unless that's the point of the mod.

#### 4.18 Replace a texture (walls, cliffs, vehicles …)

1. Find the texture: import the model in Blender and read its image names, or browse the PNGs in the extracted
   mission folder (`extracted\mission1\CLIFFRPT.png` is Mission 1's cliff face).
2. Repaint it at the same size and save it under exactly that name: `CLIFFRPT.dds` (`ds_texture.py dds CLIFFRPT.png`).
3. Put it in a mod folder. Every level that uses `CLIFFRPT` now shows yours.

#### 4.19 Replace a prop or vehicle model (_HazardDrums)

1. Import it (`extracted\mission1\OILDRUM.EVO`) and change it - here only its image, swapped for a new one
   (`HazardDrum.png`, saved as `HazardDrum.dds` by the exporter).
2. Export it under the game's name: `OILDRUM.evo`. For one object only, give the new texture its own name (as here)
   rather than replacing the game texture (4.18), which other models may share.

#### 4.20 Replace a game animation (_HeadTilt, _BouncyRun)

Steps 1-3 of 4.14, but export under the game animation's own name (`upright_relaxed_with_rifle.prb` is the standing
idle). Walk and run cycles keep the game animation's footstep sounds when the name is the same (or pick one in the
exporter's **Footsteps From**).

### For experts: tables

#### 4.21 Add rows to a game table (ExtraRecruit)

A `.txt` named like a game table (`extracted\catalog`: `CSkins_SAS_C.txt`, `fNames.txt`, `Weaps.txt` …) holding only
your new rows - they're added to the game's rows and to other mods'. No comments in tables. Example: a recruit made
the raw way, three files:

```
CSkins_SAS_C.txt   JonesSkinRaw1,SASReplacement_10,SASPortrait_10,DECLAN,OKAFOR,950
fNames.txt         DECLAN,5001
sNames.txt         OKAFOR,5001
```

`.skin` and `.weapon` files write these rows for you, so you rarely need this.

## 5. Sharing

Zip your mod's folder. Players unzip it into their `Mods` folder; it needs the DesertStormFix plugin. Don't include
the game's own files (textures, models, sounds you didn't change) - only what you made.

## 6. When something doesn't work

Open `Mods\DesertStormFix-report.txt`. Common `[fail]` lines and what they mean:

| Report says | Fix |
| --- | --- |
| `unknown setting "..."` | A typo in a setting name (spaces don't matter, spelling does), see the README's list |
| `needs "id = ..." and "based on = <weapon id>"` | The `based on` weapon name is misspelt, or `id` is missing |
| `the game allows only one weapon of type ...` | See the two rules in 3.1 |
| `"..." ignored: ... is a game weapon` | A file with only a game weapon's id makes it choosable (4.16); to change it, make a new weapon based on it |
| `picture ... not loaded` | The HUD icon file is missing, or not a normal PNG |
| `body ... doesn't fit this soldier` / `didn't load as a character` | Export the body from that soldier's own body in Blender, so it keeps his skeleton (4.5) |
| `faction "..."` | Use SAS, Delta, Russian, Iraqi, allied, eastern or all (4.15) |

Not in the report at all:

- **Your mod isn't listed**: the folder name starts with `_`, or the files aren't inside `Mods\<your folder>`.
- **The texture shows white, black or the game's original**: keep it DXT-compressed (DXT1 / DXT5), the same size
  as the texture it's based on, with a name of its own.
- **"Out of memory" when a mission loads**: a texture is too big or uncompressed.
- **A replaced texture doesn't change**: the file name must be exactly the game texture's name (check the
  extracted files), and the level has to be loaded again - restart the mission after changing a file.
