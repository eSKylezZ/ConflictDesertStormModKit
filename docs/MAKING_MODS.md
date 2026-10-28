# Making a mod, step by step

This guide takes you from nothing to your own uniform, then your own weapon, then further. You need:

- Conflict: Desert Storm (Steam or GOG) with the [DesertStormFix](https://github.com/eSKylezZ/ConflictDesertStormPatch)
  plugin installed (`dinput8.dll` next to `DesertStorm.exe`). Missions and levels (7), `.replace.txt` tables (4.22),
  `.strings` texts (4.23) and a `.skin`'s voice or normal map (4.5, 4.5c) need **DesertStormFix 3.0 or later**.
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

- Files can sit in sub-folders; the plugin finds them by name. New mods keep them **in folders by type** - what the
  Blender add-ons, the editor and the example builder make:

  ```text
  Mods\MyFirstMod\
    evos\     models (.evo, .dyn)
    dds\      textures (.dds .tga .png), image sheets (.img)
    anims\    animations (.prb)
    sounds\   sound packs (.sch), .wav, streams (.psf: sounds\SAS\ / sounds\Delta\ for one accent)
    tables\   table rows (.txt, .replace.txt), texts (.strings)
    defs\     .weapon .skin .unit .vehicle .gear .pouches
    levels\   scenes, patches, routes, .lua / .dll missions
    campaign.txt, records\, frames\   always in the mod's top folder
  ```

  Older mods have every file in the mod's top folder ("flat") - that works just the same, and the tools keep a flat
  mod flat. In this guide "put X in your mod" means its folder above (or the top folder of a flat mod). A file a
  `.weapon` / `.skin` names (`shot sound = bang.wav`) is found next to it first, then anywhere in the mods by name.
- A folder whose name starts with `_` is switched off (`Mods\_MyFirstMod`).
- In the game, **MODS** on the main menu lists every mod folder, numbered in **load order**: switch them on and off,
  move one with **Left / Right** (pad: L1 / R1), and press **Apply** (Tab, or Square / X on a pad). No restart needed
  for most changes. When two mods have a file of the same name, the mod loaded **last** wins; a new mod loads last.
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
- **A normal map of its own**: `normal map = MyUniform_Bump` (a TGA made with `ds_texture.py normal`, see 4.5).
- **A voice of its own**: `voice = Delta`, and your own WAV files for any line (see 4.5c).

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
a `body` line too. A body from a mod gets the soldier's normal map (the bumps of folds and pockets) unless the skin
brings its own (below).

**Its own normal map** (QuiltedSAS). The game's normal maps belong to the body, not the uniform: every SAS Bradley has
`HERO01BUMP_UK_01`, every Delta Bradley `HERO01BUMP_US_01` (`HERO02` Connors, `HERO03` Jones, `HERO04` Foley). A
uniform with new folds, pouches or straps can bring its own:

1. Start from the soldier's: `python cli/ds_texture.py png extracted\chardata\HERO01BUMP_UK_01.TGA`. It uses the
   same layout as the uniform texture.
2. Paint or bake it (256 x 256 like the game's). The game's maps are DirectX style (green = down).
3. Save it in the game's format: `python cli/ds_texture.py normal MyUniform_Bump.png`. Add `--opengl` if it comes
   from Blender or another program that makes OpenGL-style maps (green = up).
4. In the `.skin` file:

```
texture    = MyUniform_UK
normal map = MyUniform_Bump        # MyUniform_Bump.tga (or .dds) in the mod folder
```

That works with or without a `body` line: the plugin gives the skin its own copy of the body for the new normal map,
so the soldier's other uniforms keep theirs.

#### 4.5c A voice of its own (RadioSAS)

A `.skin` can bring a voice: whoever wears it speaks with it (the VOICE row of the loadout screen shows
"AUTO - OWN (MOD)"; players can still pick SAS or DELTA there).

1. Save the soldier's lines as WAV files, with the actors you want to start from:
   `python cli/ds_sound.py wav "<game>\mission3.sch" Bradley lines --accent delta` (or `--accent sas`; the banks are
   `Bradley`, `Foley`, `Ramirez` for Connors and `Jones`). That gives `Bradley_line01_watch_your_fire.wav` …
   `Bradley_line47_unused.wav` - the number is what counts, the rest says what the line is (table below).
2. Listen, then record or edit the ones you want to change. Keep the numbers; any sample rate, mono or stereo.
3. In the `.skin` file:

```
voice        = Delta          # the actors for every line you don't replace: SAS or Delta
voice from   = Bradley        # optional: another squad soldier's lines (Bradley, Foley, Connors, Jones)
voice line 5 = hello.wav      # one line ...
voice folder = lines          # ... or every ...lineNN.wav in a folder
```

`voice = Delta` alone (no WAVs) gives a uniform the American voice by default. Lines you don't replace keep the
chosen actor, so a partly done voice still works - it just changes speaker on those lines.

**The lines.** All four soldiers have the same 47, with the same numbers, for both actors. Most come in two takes: a
*loud* one shouted in combat and a *quiet* one used while the squad is calm (fire at will off, nobody shooting, no
enemy known). For a complete voice record lines 1-36 and 38-46 (45 lines); **37 and 47 are never played**, and **41 /
42** are two short non-verbal sounds (a grunt / breath, picked at random) that all four soldiers share - replace them
only if they clash with your voice.

| Line | When he says it | Line | When he says it |
|---|---|---|---|
| 1 | "Watch your fire" - you shoot with a squadmate crouched in front of you | 25 | "Man down!" - he sees a squadmate fall |
| 2 / 6 | Fire at will ON (loud / quiet) | 26 / 33 | Order: everybody down (quiet / loud) |
| 3 / 27 | Order: everybody up (loud / quiet) | 28 | He destroyed a tank or tracked vehicle (radio) |
| 4 / 9 | Order: advance to the marker (quiet / loud) | 29 / 14 | Spotted a manned gun - MG nest, AA, SAM (loud / quiet) |
| 5 / 21 | "Fire in the hole" - throws a grenade / fires the M203 (quiet / loud) | 30 | He destroyed a jet, Scud or gun emplacement |
| 7 / 23 | Placed C4 or a mine (quiet / loud) | 31 / 13 | Spotted a tank (loud / quiet) |
| 8 | He destroyed a wheeled vehicle (radio) | 32 / 11 | Spotted a wheeled vehicle (quiet / loud) |
| 10 | Death cry | 34 / 17 | Spotted a helicopter (loud / quiet) |
| 12 / 20 | Hold fire - fire at will OFF (quiet / loud) | 35 / 19 | "Negative" - can't reach the marker (loud / quiet) |
| 15 / 40 | Spotted enemy soldiers (quiet / loud) | 36 | He destroyed a helicopter |
| 16 / 22 | "Moving" - answers an advance order (loud / quiet) | 37 | never played |
| 18 | "I'm hit" - wounded (radio) | 38 | "Grenade!" - a grenade lands near him |
| 24 | Killed an enemy soldier (radio) | 39 | Downed and bleeding out - calls for help, every ~10 s |
| 41 / 42 | Grunt / breath, shared by all four | 43 / 44 | Order: hold position (quiet / loud) |
| 45 / 46 | Order: follow me (quiet / loud) | 47 | never played |

"Radio" lines play flat (no direction), as if over the squad radio. Orders are what he says as the leader when you
control him; the reply lines (16 / 22, 35 / 19) are what he says when you order him. Mission scripts never use these
lines, and the cutscenes have their own audio (4.5d).

#### 4.5d Cutscene and objective audio (RadioCutscene)

The cutscenes are played by the game engine, not videos; their sound is one file per scene in
`<game>\Data\Sound\English` (the SAS voices) and `\American` (Delta's): `CS_M01_S01.psf` … for the cutscenes (`CS_T01`
Training, `CS_OUTRO` the ending) and `OBJ_M1_1A.psf` … for the objective voice-overs. Each file is the whole mix -
music, effects and every speaker - so a scene is replaced as a whole:

1. `python cli/ds_sound.py stream "<game>\Data\Sound\English\CS_M01_S01.psf" scene.wav` (stereo, 44.1 kHz).
2. Edit it in an audio editor - put your lines over the original, mute the speech you replace. Keep the length: the
   pictures don't wait for the sound (a longer file plays on, a shorter one ends early). To find the speech, decode
   the `American` file too and compare: music and effects are the same in both, so wherever they differ someone
   talks (the RadioCutscene example's `ABOUT.md` walks through it).
3. `python cli/ds_sound.py psf scene.wav CS_M01_S01.psf` and put it in your mod as `Sound\SAS\CS_M01_S01.psf` (the
   SAS version), `Sound\Delta\CS_M01_S01.psf` (the Delta version) or `Sound\CS_M01_S01.psf` (both). Other language
   folders work too (`Sound\French\…`).

Which version plays follows the game's speech language - in the English game that is the SAS one now that uniforms are
chosen per soldier (the ModKit's Delta voices are for the squad lines above). Many scenes have no squad speech at all
and are the same in both folders (Mission 2 S02/S04/S05, 3 S01, 4 S02/S03, all of 5A and 12A, 6 S03/S04, 8, 9, 10 S01,
11A, the Outro and Training); the rest carry SAS / Delta speech. The texts of what is said are in each mission's
`MnnStrs.loc` (keys `SPEECHn`, `OBJ_Mn_*`).

#### 4.5b A body in several parts (several textures, big meshes)

A character can be more than one mesh / material: the exporter writes one **skin part** per material (each with its
own texture, saved as `.dds` with **Save Textures**), and cuts a part with more than 65 535 vertices or triangles into
pieces (the game's limit per part). Characters made in other programs - one mesh with a dozen materials and 100 000
vertices - export as they are, nothing to join or bake. A body with one material exports as before (one part, the
game's uniform texture).

What the game does with the parts (both games load, animate and draw every part with its own texture):

- **Desert Storm II**: every soldier shows each part's own texture.
- **Vietnam, Global Storm**: each part becomes a skin object of its own in the body file, with its own texture
  (both games draw and animate every one).
- **Desert Storm**: every character shows each part's own texture; the squad (Bradley, Foley, Connors, Jones) also
  wears the uniform of his skin on the **main** skin part (the one the exporter keeps in the body's own skin slot -
  the part with the original body's material). This needs DesertStormFix 3.3 or later; with older versions (or the
  game alone) the squad's uniform covers every part. A Desert Storm body carries its
  extra parts on spare connectors of its skeleton (an animation has one track per object, so the file can't grow): up
  to 6 extra parts on a soldier body (the hand-forearm, shoulder and hip connectors nothing uses) - join materials if
  you have more. Bodies exported with 1.8.x put them on bones instead, which crashes Desert Storm at the first
  footstep (0x424D5C) - export those again. `body =` in a `.skin` (4.5) takes such a body like any other.

Base the export on the game's own body (**Based On**) - not on a body you exported before (refused: its parts would
stay).

#### 4.6 Change a soldier's body for everyone (_BigHead)

Steps 1-3 of 4.5, but export under the game's own name (`HERO01_ARMSTRONG.evo`). Every Bradley in every level uses
it. Folders that replace game files are best shipped switched off (`_` in front of the folder name), so players
choose.

### Gear: hats, packs, face pieces and pouches

Gear is the small models hung on a soldier's body - not part of his uniform texture. On the briefing screen's
**CHARACTER** tab the HEADGEAR, BACKPACK, FACE and POUCHES rows pick them for each soldier, and every piece fits
every soldier (they all share one skeleton). A mod adds pieces with `.gear` files and pouch sets with `.pouches`
files.

A `.gear` file:

```
id       = PINK_BERET      # the new model's name: letters, digits and _ (max 35)
slot     = head            # head, back, face or pouch - which rows offer it
name     = Pink Beret      # shown on the row
based on = IR_BERET_RED    # a game model to start from ...
texture  = PinkBeret       # ... with your texture (PinkBeret.dds in the folder, same layout and size)
```

Instead of `based on` + `texture`, `model = MYHAT` uses a model the mod ships (`MYHAT.evo`); with neither, the mod
ships `<id>.evo`. Get a game piece's texture to paint over with the extractor (its texture is named like the model,
e.g. `IR_BERET_RED.dds`). `texture <part> = X` works as for weapons when a model has more than one texture.

Game pieces to start from (model names): hats `UK_CAP_GREEN`, `UK_HAT_DESERT`, `UK_WOOLLYHAT`, `UK_HELMET_DESERT`,
`US_CAP_DESERT`, `US_HELMET_DESERT`, `US_BANDANA_SANDY`, `RU_FURHAT`, `IR_BERET_RED`, `IR_SHEMAGH01_RED` …; packs
`UK_BACKPACKMEDIUM`, `US_BACKPACKLARGE`, `IR_BACKPACK_PLAIN`, `AN_RADIOPACK` …; face `AN_SUNGLASSES`,
`AN_SPECTACLES`, `AN_CIGAR`, `IR_BEARD01`, `IR_MOUSTACHE01` …; pouches `UK_AMMOPOUCH01`, `UK_BOTTLEPOUCH01`,
`US_HIPRPOUCH`, `US_HIPSATCHEL`, `US_CHESTTORCH`, `RU_AMMOPOUCH01`, `IR_HIPHOLSTER` … (the full list is the game's
`CONNOBJS.TXT` plus the ones the extractor names under `chardata`). Pieces that aren't in `chardata.dat`
(`RU_HIPHOLSTER`, `IR_AMMOPOUCH01`, `AN_WRENCH`, `AN_CHESTGRENADE`) exist only in missions whose scene uses them - use
them as a base only if the mod ships the result, or expect them to be missing elsewhere.

#### 4.6b Headgear (PinkBeret)

`slot = head`. Hats hang on the head's hat point, so a hat made from another hat sits right on every soldier.

#### 4.6c A backpack (DesertRadio)

`slot = back`. The game takes packs off while a soldier rides a vehicle and puts them back when he gets out -
yours too.

#### 4.6d A face piece (GoldShades)

`slot = face`: glasses, cigars, beards. Night-vision goggles use the same point: they replace the piece while worn,
and it comes back when they come off.

#### 4.6e A pouch, and a pouch set (TanPouches)

`slot = pouch`. With **OPTIONS > DESERTSTORMFIX > FREE POUCH CHOICE** on, each pouch spot has its own row and
offers every pouch. With it off (the default), pouches are picked as sets on the POUCHES row - so a pouch mod
usually brings a set too. A `.pouches` file:

```
name        = Tan Belt
belt        = TAN_BOTTLE      # a .gear pouch by its id ...
belt left   = TAN_AMMOPOUCH
belt right  = TAN_AMMOPOUCH
hip right   = US_HIPRPOUCH    # ... or a game pouch by its model name
```

Spots: `belt` (back of the belt), `belt left`, `belt right`, `hip left`, `hip right`, `chest left`, `chest right`;
left out or `none` = empty. A piece made for one spot works on another but can sit a little off - check it on the
briefing screen (it turns the soldier round).

#### 4.6f Pouch sets from the game's pieces only (PouchBundles)

A `.pouches` file needs no models or textures: PouchBundles is three text files (Gunslinger, Toolbelt, Bandolier)
mixing the SAS, US, Russian and Iraqi pieces.

### Vehicles

#### 4.6g A vehicle skin (DesertVehicles)

Missions where the squad has a vehicle of its own (Mission 8's Humvee, Mission 9's Bradley) get a **VEHICLE** row on
the briefing screen's MISSION tab: any ground vehicle with seats for the crew can take its place, from any faction.
A `.vehicle` file adds one more choice:

```
id        = DESERT_HUMVEE        # the vehicle's name: letters, digits and _ (max 31)
based on  = US_VEH_Hum_Vee       # the game vehicle it starts from (VEHICLE.TXT)
name      = Humvee (Desert)      # shown on the row

texture humvee01 = DesertHumvee01   # one line per texture slot you change
texture humvee02 = DesertHumvee02
```

Seats, handling, guns and sounds stay the base vehicle's. Most vehicles use several textures - name each slot as the
model does (the Humvee: `humvee01` .. `humvee04`; the Abrams: `VHAbrams01` .. `VHAbrams03`; the extractor's PNGs
carry the same names). `texture = X` without a slot is for one-texture models. Turrets' guns are separate models
(VEHWPNS.TXT -> WEAPS.TXT) and keep their colours. `damaged texture <slot> = X` skins the wreck.

#### 4.6h A new vehicle

`model = MYTANK` instead of textures: a model your mod ships (`MYTANK.evo`, exported from Blender based on the game
vehicle so its parts, crew connectors and exits are where the game expects them). Tracks and wheels come from
`MYTANK.DYN` - without one the base vehicle's is used so a level can still place it, but the vehicle isn't offered
on the loadout screen's VEHICLE tab (DesertStormFix 3.3+: another body on the base's wheels / tracks can't be
driven - ship a `.DYN` that fits your model). `damaged model = X` for its wreck, `faction = Iraqi` (or US,
UK) to show the side next to the name. For different seats, handling or guns, add rows to the tables
(`Vehicle.txt`, `VStat.txt`, `VehWpns.txt`, recipe 4.21) - a vehicle defined there by a row of its own is offered
too.

**Its own guns** (DesertStormFix 3.3+): `gun = US_VWPN_M1ABRAMS_MAINCANNON -> MY_CANNON` puts another weapon where
the base vehicle has that gun (its `VehWpns.txt` weapon) - usually a `.weapon` based on that gun with your model
(`based on = US_VWPN_M1ABRAMS_MAINCANNON`, `model = MY_CANNON`): it fires and sounds like the base's, looks like
yours, and sits on the same connector. `gun = US_VWPN_BRADLEY_TOW -> -` leaves that gun off. One line per gun; a
gun the base vehicle doesn't have is reported in the mod report. The porter (README, "Port from another Conflict game") writes these for a ported vehicle's own guns, gun by gun
(same kind first); when two base guns share one connector (the MILAN Land Rover), the second goes on a free
`CONNECTOR_WEAPONnn` through its `.weapon`'s `column 7`.

**Its own stats** (DesertStormFix 3.3+): `stat column 9 = 60.00` changes `VStat.txt` column 9 (1-based) in a copy of
the base's stats row named `<ID>_STATS` - armour, speeds and the other numbers the game reads from VStat. Columns
2 to the row's end, except 13 (the engine bank: use `engine sound`); a wrong column is reported. The porter writes
these for a Desert Storm II vehicle where DS2's VStat column means the same as DS1's (same value in 90 % of the
vehicles both games have); The Great Escape's are left as the base's (too few vehicles in common to tell).

**Gunner poses.** A seat whose connector is a `CONNECTOR_GUNNERnn` mans a gun (the first *n* of them, n = Vehicle.txt
column 26). How the gunner stands is the gun's `Weaps.txt` column 54: `VehicleGunnerAim` (crouched at a hatch, the
Humvee), `VehicleGunnerAim_TOW`, `VehicleGunner01` (standing, hands low) - or an animation your mod ships (`.prb`,
recipe 4.14). The game leaves the gunner's own weapon in his hands; `VehicleGunnerAim` happens to hide it inside
the gun, other poses show it. DesertStormFix 3.3+ doesn't draw a gunner's own weapon while he is in a gunner seat (it
comes back when he gets out). Example `LandRoverVariants`: `VehicleGunnerAim_LR`, the hatch pose stood up for the Land
Rover's rear .50 cal and MILAN, made by `examples/build_examples.py` from your game files. Guns go to the gunner seats
in VehWpns order, which on the game's two-gun Land Rover crossed them over - the example swaps its two gunner seats.

#### 4.6h2 Engine sound and handling (Hovercraft example, DesertStormFix 3.2+)

`engine sound = Hovercraft` in a `.vehicle` gives it its own engine: a bank named so in a `.sch` bank pack in the
mod, laid out like the game's `Wheels` bank (8 events: 0x0056 start, 0x0E50 stop, 0x0ECE idle loop, 0x0B46 revving
loop, 0x0BB5 driving loop - its pitch follows the speed -, 0x03C0 rolling loop, 0x0E00 / 0x046E acceleration /
deceleration one-shots). `grip = 0.6` sets a tyre friction - the game's vehicles have unlimited grip and never slide;
low values drift - and `spin damping = 30` how quickly the body stops turning (the game's 0.2: a sliding vehicle spins
on like a puck). Measured on the Hovercraft example: grip 0.6 alone kept spinning at ~60 deg/s after a turn; with spin
damping 30 it drifts through the turn (up to ~30 deg sideways), stops rotating within ~1.5 s and glides on.

Everything else about handling is the model's `.DYN` (text, numbers in this order; the Humvee's values):
type `1` (wheeled) and `0`, `10`, max steer `1.22` (radians), ride height `53`, mass `10`, chassis box `200 100 500`,
body offset `0 0 0` (z y x - a negative y lowers the centre of mass), collision box `275 150 550` and its offset,
suspension `5 0.005 0.5 0.5 1.2` (travel cm, limit softness, damping ratio, travel split, rest), tyre slip / softness
front `0.02 0.001` and rear `0.05 0.001`, wheelbase, track, wheel mass, wheel count, then one line per wheel (flags -
0x20 = front -, radius, …, position, …, wheel model). More travel and less damping = bouncier (too much rolls over in
turns), a lower body offset = steadier.

`examples/hovercraft/build_hovercraft.py` builds the whole mod from your game files - hull model on the Humvee's seats
and exits, invisible wheels, synthesised fan / turbine engine, hover handling (options `--grip`, `--travel`, …).

Vehicles from other missions just work: the plugin copies whatever the swapped-in vehicle needs (models, textures,
tracks, sounds) from the game's other archives when the mission loads. A saved game keeps the vehicles it was saved
with, so turning a vehicle mod off can make such a save use the original vehicles again.

### Soldiers

#### 4.6i A new soldier type (`.unit`, DesertStormFix 3.2+)

A `.unit` file adds a soldier type you can place in missions (editor or scene file) like the game's own:

```
id         = Iraqi_Militia        # max 35 characters, no commas
based on   = Iraqi_Conscript      # the game soldier type it starts from (Units.txt)
type       = SOLDIER              # SOLDIER, CIVILIAN, GOAT HERDER, EMIR, MISSILE EXPERT or POW
model      = IraqInfantry01_Sandy # any soldier model - the game's or yours
health     = 60
experience = Veteran              # Rookie, Green, Trained, Regular, Veteran, Expert, Professional, Guard, Crack,
                                  # Elite, Specialist, Skill One .. Skill Four
voice      = Iraqi_Civilian       # speech bank; one from another mission is copied in
item       = IR_WPN_AKSU          # the kit, one line per weapon / item ("item = X, amount")
item       = MediKit, 1
```

Only `id` and `based on` are required; everything else is the base's. Item lines replace the base's kit (`item =
none` for none). `column N = x` and `stat column N = x` set any other Units.txt / HStat.txt column (1-based) - the
HStat columns besides health aren't decoded yet.

What the game can't do: other **types** than the six above (each is built-in behaviour), and **height** - every
soldier uses the same 51-bone skeleton and the animations set every bone's length, so a taller model shrinks back when
it moves. A model must keep that skeleton (objects and order, export it from a game soldier); the report says so if it
doesn't.

`examples/aliens/build_aliens.py` builds three alien enemies this way from your game files: bodies reshaped from a game
soldier on the same skeleton (elongated skull, big black eyes, thin limbs - `make_body.py` scales each bone's vertices
along and across the bone, so the animations still fit), three energy guns (`.weapon` files with script-built models,
pictures and synthesised shots) and alien voices (copies of the Iraqi soldier voice bank with synthesised calls).

The report also checks raw table rows (`Units.txt`, `UnitWpns.txt`, `HStat.txt` parts, recipe 4.21): rows the game
can't load are left out with a reason, and Units.txt's last column (the number of kit slots) is set from the kit rows -
with the plain game a slot without a UnitWpns row crashed the mission, and more rows than slots dropped the whole kit
table.

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
every level (banks and samples the level doesn't have yet). An `.sch` **named like a level's cache** (`MISSION1.SCH`)
instead **replaces** that level's samples with the same id and language; the rest of the level stays as it is.

**Better sound effects from a GameCube disc you own**: the GameCube version keeps every sound effect and voice line
uncompressed, many at a higher sample rate (11 kHz on PC → 20 or 44.1 kHz). In Blender: **File › Import ›
Conflict: Desert Storm GameCube Audio (.iso)**, pick the disc image. Or on the command line: `python cli/ds_gc_audio.py "<GameCube
image>.iso"` (also `.gcm` / `.ciso`; `-g <game folder>` if the registry points at another install) writes
`<game>\Mods\GameCubeAudio` with one replacement cache per level - only the samples that have a higher rate on the
GameCube, re-encoded for the PC (it can only play its 4-bit ADPCM). `--list` shows what would change. Each player
converts their own disc; nothing from it ships with DesertStormFix.

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

#### 4.16b Change a game weapon itself

`based on` set to the weapon's **own id** changes the game's weapon in place - every soldier, pick-up and level that
uses it gets the new numbers, and no new weapon appears:

```
id            = US_WPN_M16A2
based on      = US_WPN_M16A2   # the same id: change it, don't copy it
magazine size = 40
damage        = 19
```

The editor's *Records…* window writes these files for you (only the values you changed).

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

#### 4.19b What it collides with, and its shadow

The game doesn't collide with the model you see but with a simpler collision mesh, and props cast their shadow from
a separate shadow mesh. Import with **Helpers** ticked to see both in the `helpers` collection:

- `#BSP_<name>` - the collision. Each face has a **surface**, shown as a material `Surface: <name>`, which picks the
  footstep sounds and the bullet-hit effects (dirt, sparks, splinters …) and decides what the face stops:

  | Surface | Soldiers | Bullets, grenades, rockets |
  | --- | --- | --- |
  | Sand, Metal, Wood, Concrete … (and their `Blocking …` twins) | stopped | stopped |
  | `Blocking Invisible` / `Invisible` | stopped | fly through |
  | `Player Barrier` | only your squad is stopped | fly through |
  | `Enemy Barrier` | only the enemy is stopped | fly through |
  | Chain Link Fence, Iron Railings, Wood Fence, Hedge, Forest Floor | stopped | fly through |
  | Water | stopped | bullets fly through, rockets don't |

  So: a solid wall = any normal surface; a wall soldiers can't pass but can shoot through = `Blocking Invisible`;
  a wall only for the player = `Player Barrier`. (Tested in game: walking into and shooting at walls of Concrete,
  Blocking Invisible and Player Barrier; the other rows come from the game's code. The panel has these three as
  **Blocks All / Soldiers Only / Your Squad** buttons.) Each surface also has a `Blocking …`
  twin, which marks obstacles for the computer-controlled soldiers' route map: plain for ground, floors and ramps
  they should walk on, Blocking for walls, props and anything they should go around. The game levels come with their
  route maps already made (`<level>.qtd` / `.rmd`), so a changed level piece blocks everyone straight away but the AI
  only plans around it once the level's routes are redone - the level editor (`editor/`) does that.
- `#SHA_<name>` - the shadow mesh. Some props and vehicles have one; a static model (prop, weapon, building) without one
  gets one when you export a `#SHA_` object with it (the panel's **Shadow Mesh (Hull)** makes one). Keep it low-poly and **closed** (no holes):
  the game casts no shadow from an open edge.

To change them, edit those objects (move, scale, model a new shape, change face materials) and select them with the
model when you export. The exporter's **Collision** option:

| Collision | Result |
| --- | --- |
| From #BSP_ Objects (default) | the selected `#BSP_` objects are the collision; with none selected the original's is kept |
| From the Model | every exported part collides with its own mesh (a quick start for a new prop) |
| Keep the Original | the collision of the model it's based on, whatever you changed |
| None | nothing collides (soldiers and bullets pass through) |

Faces whose material isn't a surface get the **Default Surface** (Blocking Concrete). Level pieces (whole streets,
cliffs) work the same way: each piece's collision object is tied to its own part of the level. A new collision object of your
own: name it `#BSP_` + the model's name and give its faces `Surface: …` materials (or materials named just after a
surface, e.g. `Metal`). The exporter's report lists the surfaces it wrote. Keep collision simple - boxes and big flat
faces; every triangle costs time each frame something moves near it.

#### 4.19c Points: muzzle, shell ejection, ladder tops

Some models carry a few points, imported (with **Helpers**) as empties you can move:

| Empty | What it is |
| --- | --- |
| `#PT_MUZZLE` | where bullets and tracers leave the gun - just past the end of the barrel (guns, vehicle cannons, rocket pods) |
| `#PT_LAUNCHER` | the under-barrel grenade launcher's muzzle (M16 + M203) |
| `#PT_EJECT` | where spent shells fly out; on rocket launchers the back blast |
| `#PT_LADDER_BOTTOM` / `#PT_LADDER_TOP` | where soldiers get on and off a ladder |

Made the barrel longer or added a suppressor? Move `#PT_MUZZLE` to the new end and select it with the gun when you
export. A taller ladder needs `#PT_LADDER_TOP` at its new top. Points you don't select keep the original's position.

#### Exporter options worth knowing

- **Into a Mod Folder** writes straight into `<game>\Mods\<Mod>` (with the new textures). Normally that's a new
  model, named like the file you pick, and a new weapon or body gets a starter `.weapon` / `.skin` next to it, already
  pointing at the model - open it and fill in the rest. Tick **Replace the Game's Model** to save it under the
  original's name instead (4.19). Check the **Game Folder** if you have the game twice (Steam and GOG).
- **Check Only** lists what the game would trip over - too many vertices, missing or oversized textures, no UV map,
  a model far bigger or smaller than the original (usually a wrong **Scale**) - and writes nothing. The same checks
  run on every export; problems that would break the model stop it.
- Vertex colours (the baked lighting on buildings and many props) are exported from the mesh's colour attribute, as
  the import made it.
- **Based On › Auto / Generic Prop** builds a brand-new prop without a game model to start from (on the game's
  `BH_BODGEPLATE` - Desert Storm II: `WALLMAP` - found in your extracted folders or read from your game archives). Without a `#BSP_` object it
  collides with its own mesh; its origin is Blender's world origin. The same happens when nothing at all is given.
- Images loaded from the extracted game folders aren't saved into the mod again - the game has them already.
- After one export **Into a Mod Folder**, the sidebar's **Export to Mod** button repeats it for the selection in one
  click (same options, mod and model name - both editable in the sidebar).

#### Sidebar tools (3D View › N › Desert Storm)

- **Model check**: vertices and triangles; export vertices per texture (positions × UV seams - hard edges add a few
  more) against the game's 65535; images over 2048 (over 1024 needs DesertStormFix 3.2+) or not a power of two; collision triangles (over 3000 is slow).
  Counts follow the mesh in Object Mode; **Refresh** after UV edits.
- **Show Collision Surfaces**: `#BSP_` objects drawn solid, one colour per surface (the list shows the active
  object's), instead of wireframes. In Edit Mode the eyedropper beside the surface list takes the active face's
  surface, so **Assign** can paint it elsewhere.
- **Check Skin Weights** (a mesh bound to a character's skeleton): the game blends at most 4 bones per vertex - the
  export keeps the 4 strongest quietly; this does it visibly and normalises the rest. Vertices with no bone weight
  (they'd stick to the root bone) are selected in Edit Mode; vertex groups that aren't bones are listed.
- **Generate Road** (active object a curve - Add › Curve): cross-sections every **Sample Every** along its first
  spline, **Width**, a game texture by name (`M01_ROAD01` … from mission1) repeated every **Texture Repeat**, lifted a
  few cm, plus a `#BSP_` copy on a walkable surface (Tarmac, Baked Earth, Sand …). Running it again on the same curve
  replaces the road. Select both and export with **Auto / Generic Prop**; the road keeps the curve's world position.

#### 4.20 Replace a game animation (_HeadTilt, _BouncyRun)

Steps 1-3 of 4.14, but export under the game animation's own name (`upright_relaxed_with_rifle.prb` is the standing
idle). Footsteps are timeline markers on the action (Action Editor › View › Show Pose Markers): `FS_LEFT` /
`FS_RIGHT` = a walk step, `FS_LEFT_25` / `FS_RIGHT_25` = a run step (the number is the game's value for it; the sign
tells the feet apart - which one is left isn't confirmed), `EVENT_2` / `EVENT_3` sit on stance animations. The
import makes them, the export writes whatever markers the action has (move them to retime the steps, add them to a
new cycle; other marker names are ignored with a warning). An action without markers keeps the footsteps of the game
animation with the same name (or the one in the exporter's **Footsteps From**).

#### 4.20a Desert Storm II models and animations

The add-on works the same way on **Conflict: Desert Storm II**, through its own menu entries: extract its archives
(**File › Import › Conflict: Desert Storm II Archives (extract)**), import its models and animations (**Conflict:
Desert Storm II (.evo)** / **… Animation (.prb)**), and export with **File › Export › Conflict: Desert Storm II
(.evo)**. Those entries use Desert Storm II's own game folder, extracted folder and active mod; a Desert Storm model or
skeleton is refused there (and flagged on import). What's
different:

- **Collision surfaces**: Desert Storm II has twelve more (Snow, Soft Mud, Tree Canopy, Gravel, Plastic, Rubber and
  their `Blocking …` twins). They're in the surface lists, marked "Desert Storm II only"; a Desert Storm model refuses
  them.
- **Animations** name the bone each track moves (Desert Storm's go by position), so one animation plays on every
  soldier body with those bones. Export with **Footsteps From** set to the game animation it replaces: the new one
  keeps that animation's tracks, including those for objects this body doesn't have (the vehicle gunner animations
  also move the gun). Which one a seat uses is in the catalog tables: a vehicle's guns (`VEHWPNS.TXT`) name their
  gunner animation in `WEAPS.TXT` (Land Rover MK19: `BunkerGunnerAim`, .50 cal: `VehicleGunnerAim`).
- **Brand-new props** start from Desert Storm II's `WALLMAP` (one flat panel that collides with itself).
- **Mods**: DesertStorm2Fix's Mods folder replaces a game file by its **file name** (`<game>\Mods\<Mod>\` -
  sub folders don't matter, a `_` in front switches the mod off; `Mods\DesertStorm2Fix-report.txt` lists every file it
  served). So export with **Into a Mod Folder** and **Replace the Game's Model** (the model under the original's
  name), animations under the game animation's name, new textures with **Save Textures**. A model under a new name is
  used nowhere until a game file names it; for a new weapon model the exporter writes a starter `.weapon` (DS2's
  plugin reads them). Details: the patch's `docs/sdk/ds2-mods.md`.
- **Test in Game** exports and starts Desert Storm II at its launcher: DesertStorm2Fix has no `+level` start, so pick
  the mission in the game's menus (the Test Level field only works for Desert Storm).

**The Great Escape, Vietnam, Global Storm**: their plugins (GreatEscapeFix, VietnamFix, GlobalStormFix) serve
`<game>\Mods` the same way - a file replaces the game's by its file name, in any folder of the mod. **Into a Mod
Folder** works for all three: export with **Replace the Game's Model** to change a game model. A new weapon model in
The Great Escape gets a starter `.weapon` (GreatEscapeFix reads them); in Vietnam and Global Storm a new name is used
only once a game file names it. **Test in Game** exports and starts the game at its launcher / front end - only
Desert Storm has the `+level` start. GreatEscapeFix and GlobalStormFix list what they served in
`Mods\<plugin>-report.txt` (VietnamFix has no report yet).

### For experts: tables

#### 4.20b Change records field by field (`records\*.lua`) - the recommended way

Every weapon, soldier type, vehicle, item, door and so on is a **record** with a fixed ID - `ds1:weapon/us_wpn_m16a2`,
`ds1:unit/bradley`, `ds1:vehicle_stats/tankstat_m1a1`. A mod changes records with small files in
`Mods\<YourMod>\records\` (any name ending in `.lua`) that name **only the fields they change**:

```lua
return {
  { id = "ds1:weapon/us_wpn_m16a2", magazine_size = 60, damage = 16 },
  { id = "ds1:weapon/my_rifle", based_on = "ds1:weapon/us_wpn_m16a2", key = "MY_RIFLE", damage = 30 },
  { id = "ds1:feature/nu_prop_crate5#2", delete = true },
}
```

- Two mods changing *different* fields of one weapon both apply; when they set the *same* field differently, the mod
  that loads later wins and `Mods\DesertStormFix-report.txt` says so (`Records: conflict: ...`).
- To see every record and field name: `python cli/ds_records.py -o <folder>` writes them from your own game
  (`forms\weapon.lua`, `forms\unit.lua` ...; keep that folder to yourself - it's game data), `--levels` adds each
  level's placed objects, waypoints and regions with their editor IDs.
- The editor's commands `forms_types`, `forms_list`, `form_get`, `form_set`, `form_new`, `form_delete`,
  `form_revert`, `forms_report` (also over MCP) write `records\editor.lua` for you.
- Full format: DesertStormFix's SDK page `docs/sdk/data-files.md`. Needs DesertStormFix 3.3 or newer.

The older ways below (`.txt` rows, `.replace.txt`, `.weapon` files) still work and are applied on top of record
edits; prefer records for new mods - they combine with other mods instead of replacing whole rows.

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

#### 4.22 Change or delete game rows (`<table>.replace.txt`)

A file named `<table>.replace.txt` (`Units.replace.txt`, `UnitWpns.replace.txt`, `HStat.replace.txt` …) holds rows
that **replace** the game's rows with the same first column; the rest of the table stays. Tables with several rows per
key - a soldier type's kit in `UnitWpns.txt`, a vehicle's guns in `VehWpns.txt` - are replaced key by key: list all
the rows the key should have. A line `-<key>` deletes the key. Example - Iraqi conscripts carry an AK-74 and a pistol,
and have less health:

```
UnitWpns.replace.txt   Iraqi_Conscript,IR_WPN_AK74,WEAPON,0
                       Iraqi_Conscript,IR_WPN_Makarov,WEAPON,0
HStat.replace.txt      IR_Infantryman00,20,0,0,0,0,90,60.00,10,0.00,0.00,0.00,0.00,90,100.00,100,5.00,1.50
```

A soldier type's kit slots (last column of `Units.txt`) and a vehicle's gun slots (`Vehicle.txt` column 26) follow
the kit / gun rows automatically (DesertStormFix 3.2+); with an older version keep them equal to the number of rows -
the example above needs `Iraqi_Conscript`'s last column raised to 2.

Plain `<table>.txt` files still only add rows. The editor's *Records…* window shows every table with named columns
and links between them (a soldier type → its stats, experience level and kit; a vehicle → its stats and guns) and
writes these files.

#### 4.23 Texts: change the game's, add your own (`.strings`)

A `.strings` file in a mod: `KEY = text` lines (`#` comments, `\n` for a new line). A key the game has replaces its
text everywhere - menus, objectives, messages (`S_PLAYER = CAMPAIGN` renames SINGLE PLAYER on the main menu). A new
key is a text for your mission: the game finds it by the key's hash, which the editor's *Records… › Texts* tab shows
(the same number `conflict_ds_tools.records.hash_key("MY_KEY")` gives) - pass it where a mission script takes a
text, e.g. an objective. One text for all languages.

Each mission also has its own texts (objectives, briefing pages, cutscene subtitles, the date and place of its intro)
in a file named after it: `M01` = Mission 1, `T02` = Training 2, `M05B` = the second part of Mission 5. Missions
reuse keys with different texts (`MOV_TXT1` is every mission's date, `SPEECH1` its first subtitle), so put a mission's
changes under its name - they apply only while that mission runs:

```
S_PLAYER = CAMPAIGN            # every level
[M03]
MOV_TXT1 = 17:00, January 17, 1991
OBJECTIVE1A = Reach the radar site before nightfall.
[all]                          # every level again
```

The *Texts* tab lists a mission key once per mission (search `M03` for one mission's texts) and writes the section
for you.

**Texts for your own level** (DesertStormFix 3.3+): a section name no game file has, e.g. `[MYOP]`, is a text file of
its own - a `.lua` level switches it on with `game.load_strings("MYOPStrs.loc")` (the plugin sees there is no such
file and uses the section; the texts don't touch any other level). A key known only by its number is written
`#4ECE85EA = Neutralize the Sniper` - what the porter's `texts` lines write for other games' texts (README, "Port from another Conflict game").

## 5. Sharing

Zip your mod's folder. Players unzip it into their `Mods` folder; it needs the DesertStormFix plugin. Don't include
the game's own files (textures, models, sounds you didn't change) - only what you made.

Missions saved by the level editor are ready to share as they are: they hold no game files (the plugin builds what
they borrow from each player's own copy of the game), so zip the mod folder without changing it.

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

## 7. Missions and levels

Your own missions - on a campaign level or on a new level of your own - need **DesertStormFix 3.0 or later**, for you
and for the players you share them with.

### 7.1 Make one in the editor

The kit's level editor, **ConflictEditor.exe**, makes a whole mission without code: start it, pick the game (it
extracts the game's archives the first time) and open a level.

1. Open a level (Mission 1 …), or **New level…** for an empty one with a ground you can sculpt.
2. Place soldiers, vehicles and props from the Library; give soldiers a patrol path (**Paths & Regions…**); place
   trigger regions and cameras.
3. **Objectives…**: what the player has to do (kill or destroy, reach a region, survive, or done by a rule), and
   **Event rules** (*When … Only if … Do …*) for everything else - reinforcements, doors, radio lines, cutscenes, the
   mission's end.
4. **Save as mission…** with a mod name and a mission name (and a campaign name to list it in the game's menu), then
   **▶ Play** starts the game straight into it.

A mission made from a campaign level keeps that level's own mission logic (briefing, cutscenes, objectives), and
yours runs on top. The mod holds only your changes, so it can be shared as it is (5). The full tour of the editor:
[editor/README.md](../editor/README.md).

Things DesertStormFix 3.2+ adds that matter for missions:

- **Difficulty**: the DIFFICULTY screen can also offer VERY EASY and VERY HARD. A rule's *difficulty* condition
  (Only if … difficulty is / at least / at most …) lets a mission add or hold back enemies per difficulty.
- **Kills for cutscenes**: *Kill / destroy* has a "blown up" choice - the soldier is thrown with gibs and a large
  blood pool, as if a grenade went off (soldiers only).
- **Unreachable spots**: an AI soldier ordered somewhere it can't walk to (no route on the walk map) now teleports
  there when no player can see him or the spot (the Japanese release's fix) - so check the walk map warnings rather
  than relying on a soldier never arriving.
- **Online co-op**: a custom mission can be played online when both players have the mod. Rules stay in step on
  both PCs, random chance included.

### 7.2 A custom campaign (`campaign.txt`)

A `campaign.txt` in a mod's folder lists missions to play in order:

```
name = Operation Sandstorm
mission = sandstorm1.lua
mission = sandstorm2.lua
```

`#` starts a comment. In the game: **SINGLE PLAYER › CUSTOM CAMPAIGNS** lists every campaign, then the difficulty,
then the first mission. Winning a mission goes on to the next one, and back to the main menu after the last. The
editor writes this file when you give a campaign name on *Save as mission*.

### 7.3 Start a mission straight away

```
DesertStorm.exe +level sandstorm1.lua
```

skips the launcher and the menus and loads that mission (the editor's *Play* does this). Also works with a game
mission (`+level Mission3.dll`).

### 7.3b Missions written as a DLL

Missions can also be DLLs, like the game's own `MissionN.dll`. Put the DLL in your mod folder:

- **A changed game DLL** (e.g. your edited `Mission6.dll`) replaces the game's DLL while the mod is on. You can't
  share the game's code, so *Export Mod Package* ships it as `Mission6.dllpatch`: only the bytes you changed. Each
  player's plugin builds the DLL from their own copy. A copy of a game DLL under a new name (`MyOp.dll` made from
  `Mission6.dll`) is shipped as `MyOp.dllpatch` in the same way. A player whose game DLL is a different version gets
  a line in `Mods\DesertStormFix-report.txt`, and the game keeps its own DLL.
- **A DLL of your own** (your own code) is shipped as it is.

In the editor, *Open mission* lists the mission DLLs in your mods. Opening one shows its scene, routes and logic.
The scene is the one the DLL loads; if it isn't a game level's, you pick the level whose models it uses. *Save as
mission* / *Play* keep your DLL running underneath (`--@base MyOp.dll`). A DLL with a new name also gets
`MyOp.level`, which contains `--@archive mission6` (the game level whose archive and sounds it uses). Without that
file, `+level MyOp.dll` would look for `MyOp.dat`.

### 7.3c Changing a game mission's script (readable missions, `--@replaces`)

The game's missions can be turned into Lua you can read and edit, made from **your own** mission DLLs.

**In the editor:** *Open mission…* lists the game's missions as "Mission1 (game mission as Lua)". Pick one and name a
mod folder: the first time, its Lua is made from your game's DLL (`Mods\<YourMod>\mission_src\Mission1\`), the level's
scene opens, and **Script…** shows the mission's events. Every save rebuilds `Mods\<YourMod>\Mission1.lua` (with the
`--@replaces` line below), so the game plays your version; a save that doesn't load as Lua is reported with its line.
*Export mod* leaves both out (they're made from the game's code - see the end of this section). Commands, also over
MCP: `game_missions`, `game_mission_open`, then `get_script` / `save_script`.

**By hand:**

```bat
python cli/ds_mission2lua_readable.py "D:\Games\Conflict - Desert Storm\Training1.dll" -o work
```

`work\Training1\scripts\events.lua` is the mission logic: every function of the DLL, the script API by name
(`api.set_path(...)`), messages as `on("killed", ...)`, scene objects by their editor ID (`obj.ARMOURER`). Every
other script API call is there for you too (`api.play_stream`, `api.set_view_camera` …, in
`scripts/runtime.lua`). After editing, `--bundle work\Training1` rebuilds `work\Training1.lua`.

To play your version **instead of the game's** `Training1.dll`, add this line under the first line of
`Training1.lua` and put it in a mod folder:

```lua
--@replaces Training1.dll
```

The level stays the game's level (its menu entry, archive, briefing and saves); only its script comes from your mod
(DesertStormFix 3.3+; [PSE docs: mission replacements](https://github.com/eSKylezZ/ConflictDesertStormPatch/blob/main/docs/sdk/reference/mission-replacement.md)).
That Lua is made from the game's code, so it's for your own PC: **don't share it**.

**To share a changed mission, write a mission patch** (DesertStormFix 3.3+; [PSE docs: mission
patches](https://github.com/eSKylezZ/ConflictDesertStormPatch/blob/main/docs/sdk/reference/mission-patches.md)). This is
a `Training1.patch.lua` in your mod folder that holds only your own Lua. DesertStormFix makes the same readable Lua
from the player's own `Training1.dll` in the game, applies your patch to it and runs the result. The patch has:

```lua
--@patches Training1.dll
--@readable 1

--[[@edit
        f_47B0(2705)
        g_14258 = 19
--@to
        f_47B0(2705)
        my_heli_flyover()
        g_14258 = 19
]]

function my_heli_flyover()
  api.activate_object(SCENE.objects.HELI_FLYOVER)
end
```

- **Edit blocks** change lines of `scripts/events.lua`. Copy the lines from your `work\` output; they must appear there
  exactly once.
- **The rest is your Lua.** It runs in the mission's own environment before the level starts, so it can define new
  functions or replace the mission's (keep the old one in a local to call it). DesertStormFix adds:
  - `SCENE.objects` / `.waypoints` / `.regions` / `.cameras`: every editor ID of the level's scene;
  - `TEXT.KEY` / `MOVIE.KEY`: text hashes;
  - `X`: the mission runtime.

Every mod's patch for a mission applies, in the mods' load order. A patch that doesn't fit, for example because
another patch changed the same lines, is left out and named in the mod report. Check yours before playing:
`python cli/ds_mission_patch.py "<game>\Training1.dll" Training1.patch.lua` (it applies the patch to the readable
mission and compiles the result). The example **RestoredMissions** (`examples/restored_missions/RestoredMissions/`) is
four such patches. It puts cut content back into the training missions and Mission 1, and names the scene's objects,
waypoints, regions and cameras by editor ID (`SCENE.objects.ARMOURER`, `SCENE.cameras.CS_S2_CLOSEUP`).

Adding a film to a level that has none: don't call `api.set_sound_mode(1)` unless a stream plays after its
`set_sound_mode(0)`. The movie sound mode silences every ordinary sound and only switches back when the next stream
starts, so the level's speech stays silent (the first version of the training restoration did this - see the PSE page's "Pitfall").

Soldiers can climb ladders in a script: the game has an unused script command `mount_ladder(soldier, 1)` (slot
0x568) that puts him on the nearest ladder, and the climb itself is the movement rule the player's input uses
(`MSR_LADDER_CLIMB_UP` 0x3C, down 0x3D) applied every frame - the game dismounts him at the other end. RestoredMissions'
`common.lua` has it ready: `local c = tr_climb(id, true)` then `tr_climb_step(c)` each frame until it returns "done"
(Training 2: Johnson climbs the observation tower).

Changing where a soldier walks: check whether the code you replace runs once or every frame. Mission 1's wave step
calls `set_path` for the small-bridge guard on every frame until the truck reaches its next waypoint, and a path set
again each frame never gets past its first waypoint (he circles it). RestoredMissions' `mission1.lua` starts its run
once.

### 7.4 Writing a mission script by hand (Lua)

A mission is a Lua 5.4 script, `<name>.lua`, anywhere in a mod folder (the editor writes one for you, and you can
change it in its *Script…* window). Lines at the top say which level it is built on:

| Line | Meaning |
| --- | --- |
| `--@archive mission1` | Use Mission 1's level data and sounds (`none`: the level's files are loose in the mod) |
| `--@base Mission1.dll` | Run Mission 1's own mission logic first; your script runs after it |
| `--@scene MYMISSION.ENV` | The scene to load with `--@base` (default: the script's name) |

The game calls these functions of your script:

| Function | When |
| --- | --- |
| `init()` | Once, while the level loads. Set up the level, objectives and kits here |
| `update()` | Every frame while the player has control (not during cutscenes) |
| `on_message(id, a, b, c, d, e)` | Something happened (`id` is one of `game.EVENT`, `a` … `e` its details). For a question (`…_QUERY`), return `true` / `false` or a number to answer it |

`game.EVENT` names: `KILLED` (a victim, b killer), `REMOVED`, `DROPPED` / `PICKED_UP` (a soldier, b item), `COMBAT`
(an enemy reached combat alert), `DESTROYED`, `WAYPOINT` (a unit, b waypoint), `PATH_DONE`, `ARRIVED`, `HEALED`,
`DAMAGED`, `SWITCH`, `RADIO_CALL`, `LASER`, `SPAWN`, `COMPLETE`, `START` (play begins), `ACTION`, `DOOR` (a door, b 0
closing / 1 opening / 2 closed / 3 open), `TIMER` (a timer id), `PLANTED`, `EXPLOSION`, `MOUNTED` / `DISMOUNTED` (a
soldier, b vehicle), `SEAT`, `LAUNCHER_DROPPED`, `VEHICLE_DAMAGED`, `OBJECTIVES_SCREEN`, `SPOTTED` (a observer, b
target), `SHOT` (a weapon, b shooter), `FIRE_MODE`, `ZOOM`, `TRIGGER`, `UNLOCK`, `SAVE_LOADED`, `ENTERED` / `LEFT` (a
region name, b player 0-3); questions: `MOUNT_QUERY`, `ACTION_QUERY`, `DROP_QUERY`, `PICKUP_QUERY`,
`VEHICLE_ITEMS_QUERY`, `SWITCH_SOLDIER_QUERY`, `GIVE_QUERY`, `LADDER_QUERY`, `ORDERS_QUERY`, `INVENTORY_QUERY`.

Objects are named by their **script id** (the number the editor shows for each soldier, vehicle or item). Texts are
**text hashes**: `game.text("MY_KEY", "Reach the radio mast")` makes one, or use a key from a `.strings` file (4.23).

| Function | What it does |
| --- | --- |
| **Objectives and the end** | |
| `game.add_objective(title[, description])` | Adds an objective (text hashes), returns its number - keep it, don't guess it |
| `game.show_objective(i)` / `hide_objective(i)` | Shows (and tracks) / hides it |
| `game.complete_objective(i)` / `reopen_objective(i)` | Marks it done (with the game's sound) / not done |
| `game.end_mission(result)` | 2 = success, 1 = failure: the game's end screen |
| `game.set_next_level(name)` | The level after a success (`"Mission2.dll"`, `"next.lua"`); without it the campaign's next mission, the main menu after the last |
| `game.next_mission()` | The name of the campaign's next mission |
| **Timers and saved games** | |
| `game.timer(id, seconds[, repeating])` | `on_message(game.EVENT.TIMER, id)` after that many seconds of game time (repeating: every time) |
| `game.stop_timer(id)` | Stops a repeating timer after its next tick |
| `game.time_ms()` | Game time in milliseconds |
| `game.state` | A table kept in saved games (numbers, strings, booleans and tables of them; up to 2 KB) |
| `game.loading_save()` | True in `init()` when a saved game is being loaded (skip your intro); `SAVE_LOADED` follows |
| `game.allow_pause(on)` | Pause menu (and so saving) on or off - off around your own cutscenes |
| **Objects** | |
| `game.activate(id)` / `deactivate(id)` | Brings an object into play / takes it out |
| `game.layers`, `game.show_layer(name)` / `hide_layer(name)` | Named groups of script ids (the editor writes them), brought into / out of play together |
| `game.position(id)`, `game.distance(id, x, y, z)` | An object's position (cm) / its distance from a point |
| `game.set_team(id, team)` | A soldier's side (0 = the squad) |
| `game.captive(id, on[, keep_kit])` | Makes a soldier a prisoner like Foley in Mission 1 (`false` frees him into the squad) |
| `game.keep_awake(id, on)` | The game lets enemy soldiers the player hasn't seen yet sleep, so their patrols and orders wait until the player comes near; this keeps one awake. Returns true when it worked |
| `game.kill(id)` | Kills a soldier the normal way (death animation, blood pool). Returns true when it was a living soldier. Needs DesertStormFix 3.2+ (the rules' Kill / destroy uses the game's own kill call, which also destroys vehicles and props) |
| `game.explode(id)` | Kills a soldier as if hit by an explosion: the body is thrown, gibs and a large blood pool - for cutscenes. Returns true when it was a living soldier. Needs DesertStormFix 3.2+ |
| `game.difficulty()` | The difficulty: 0 EASY, 1 MEDIUM, 2 HARD, 3 VERY HARD, 4 VERY EASY (3 and 4 are DesertStormFix's own; the game's is_difficulty_hard / is_difficulty_easy answer VERY HARD as hard and VERY EASY as easy) |
| `math.random` | Seeded the same on every PC in an online co-op game (the session's seed), so random choices stay in step - keep using it rather than reading the clock |
| **Players and regions** | |
| `game.player_soldier(n)`, `game.player_position(n)` | Player n's (0-3) soldier / position, nil when there is none |
| `game.regions` | Trigger regions by name (the editor writes them); entering / leaving one sends `ENTERED` / `LEFT` |
| `game.in_region(name, x, y, z)`, `game.player_in_region(name[, n])` | Is the point / player n in the region |
| **Texts, kits and the briefing** | |
| `game.text(key, text)` | A text of your own, returns its hash |
| `game.set_string(key, text)` | Replaces one of the level's own texts by key (e.g. the loading screen's `SECTIONHDR`, `SECTIONTEXT`) |
| `game.popup(text[, seconds])` | A message in the bar at the bottom of the screen |
| `game.set_briefing_text(hash, slot)` | The briefing: slot 0 title, 1 date, 2 place, 3 and up the next page |
| `game.briefing(text)` | The text on the loadout screen's BRIEFING tab |
| `game.kit(id, {uniform = …, main = …, sidearm = …, launcher = …, grenades = …, lock = true})` | A soldier's uniform (skin texture) and weapons (weapon ids, `"-"` = none) at the start; `lock` = the player can't change them |
| `game.loadout(false)` | No loadout screen at the start (kits still apply) |
| **Films and sound** | |
| `game.film(true, draw)` / `game.film(false)` | World frozen, player input off and HUD hidden; `draw()` runs every frame to draw the film |
| `game.image(name)` | Loads a `.png` / `.dds` of the mod, returns a handle |
| `game.draw(img, x, y, w, h[, alpha, rot, argb])`, `game.draw_part(…, u0, v0, u1, v1 …)`, `game.fill(x0, y0, x1, y1, argb)` | Draws an image, part of one, or a rectangle. Units are screen heights, x from the centre |
| `game.screen()` | Half the screen's width and its height in those units |
| `game.music(name[, loop])` / `game.music()` | Plays a `.wav` of the mod / stops it |
| `game.clock()` | Real seconds (runs while the world is frozen) |
| `game.skip_pressed()` | Esc / Enter / Space / pad Cross pressed since the last call |
| **Level set-up** (the editor writes these) | |
| `game.load_scene(file)`, `load_strings`, `load_sound_bank`, `load_map_image`, `game.anim(state, file)`, `game.set_level_bounds(…)` | Load a level's scene, texts, sounds, map and animations |
| `game.set_time_of_day(hour)`, `game.get_time_of_day()` | The hour of the day |
| **Other** | |
| `print(…)`, `game.log(…)` | A line in `DesertStormFix.log` (dev builds of the plugin) |
| `game.call(slot, …)`, `game.callf(slot, …)` | **For experts**: calls one of the game's own mission functions by its number, with up to 12 arguments (a whole / a decimal result). The editor's rule action *Call a game function* lists them by name. A wrong number or wrong arguments can crash the game |

A small mission on Mission 1's map:

```lua
--@archive mission1

function init()
    game.load_scene("MISSION1.ENV")
    game.loadout(false)
    if game.loading_save() then return end
    game.timer(1, 0.5)                  -- set the objective up once the mission runs
end

function on_message(id, a)
    if id == game.EVENT.TIMER and a == 1 then
        game.state.goal = game.add_objective(game.text("MY_GOAL", "Hold out for two minutes"))
        game.show_objective(game.state.goal)
        game.timer(2, 120)
    elseif id == game.EVENT.TIMER and a == 2 then
        game.complete_objective(game.state.goal)
        game.end_mission(2)
    end
end
```

A real level needs more set-up than this (sounds, map, animations) - start from a script the editor wrote. Script
errors go to `Mods\DesertStormFix-report.txt` with their line numbers (the editor's *Script…* window shows them).
