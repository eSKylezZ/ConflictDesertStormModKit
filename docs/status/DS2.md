> Snapshot of the pre-merge fork's status notes (`ConflictDesertStorm2ModKit`, 2026-10-10), kept for its research details.
> Paths: `conflict_ds2_tools` is now `conflict_ds_tools`, `editor/*.json` is `editor/games/<id>/`. Current state: [GAMES.md](../GAMES.md).

# What works on Desert Storm II data

Checked 2026-10-10 against the PC retail install (`CDS II.exe`, 10 missions + 4 training levels) with the DS1 code.

| Part | DS2 | Notes |
| --- | --- | --- |
| `.dat` archives (`archive.py`) | **done** | same layout as DS1, but names are hashed **lower-cased** (DS2 `FUN_004c0b30`; `mission01.env` = 0x5A909764) - 15534 of 15969 files named on extraction |
| Game folder detection | **done** | `CDS II.exe` + `chardata.dat` (no install path in the registry) |
| Scenes `.ENV` v106 (`env.py`) | **done** | all 14 levels: 0 problems, byte-identical round trip. From DS2's loader (`FUN_0050aa80`, HLOI `FUN_00509980`, waypoints `FUN_004d9bf0`, cameras `FUN_004e0180`): ENVI +u0 (v89) / no parent (v79+) / longer breakable block / +u5 (v86); parents on `PCLINK` lines after ENDI (`parent` alias, kept in step on duplicate / rename / delete; no line = Root); HLOI +u_z (v95), +ai_instance (v94), +u_v (v86); HLOU squad lines (+profile) parse as HLOI; PORT +pair list (v79); waypoints +4 ints; cameras no u1 (v84+). `ENVCOVERPOINT` still untyped |
| Models `.evo` 0x35 (`evo.py`) | **reads all 3280** | header 20 + 2 u32 changes (`FUN_005087b0`), skin influences 42 bytes / u16 bone (`FUN_00556b70`), keys without the extra u32 (`FUN_00508630`). Hierarchies: each object header carries a key index (2nd u32 after the parent); the root's keys are in that order and skin influences name key indices directly - `_ds2_key_order` puts both in object order (all 28 skins: every vertex's influences meet at one point, soldiers stand 1.73 m in T-pose) |
| Models written (`evo_write.py`) | **done 2026-10-22** | follows the template's version: parent name at +172, VBUF 7 (`FUN_00554d10` = DS1's v6 layout), 42-byte influences, `#SHA_` objects type -1 with zero block counts like the game's 45; TEXL hashes = DS1's lower-case LFSR (20065 / 20065). `scripts/check_models.py` on all 3280: no-op rebuild byte-identical 3280, own geometry 3263, skins 28 / 28 (within 0.05 cm), collision trees 2901 (hit / miss like the game's), points 51, added shadows 3054 |
| Collision surfaces | **done** | Default.spl = DS1's 51 rows + 51-62 Snow, Blocking Snow, Soft Mud, Blocking Soft Mud, Tree Canopy, Blocking Tree Canopy, Gravel, Plastic, Rubber, Blocking Gravel / Plastic / Rubber (`bsp.SURFACES_DS2`); same tree layout (all 6929 trees re-serialise to the same layout) |
| Routes `.rmd` v3 (`routes.py`) | **done** | 29-byte markers {pos, weight, 5 links, u16 flags, u8}; all 11 files round-trip |
| `.sch` sound caches (`sound.py`) | same format | |
| Animations `.prb` | **done 2026-10-22** | binary load `FUN_005625d0` (a dump of the CPRSAnim tracks the .prs reader `0x5635a0` builds): header / bounds / events as DS1 (the bounds' 6 FEEDFACE words = half size + centre; one block or one per frame), track = {u32 object name hash = EOBJ header hash, u32 flags 1 / 3 connector, u16 keys, u16 keys per second, u32 slot, 28-byte keys pos + conjugated quat}. Tracks bind by hash; an animation fits a body when it drives 3/4 of the skin's weighted bones with matching bone lengths (269 of 274 fit every soldier - vehicle animations carry extra gun tracks -, the 4 goat ones only the goat). `scripts/check_animations.py`: 2998 / 2998 files rewrite byte for byte, 269 re-pose identically from a fresh write |
| Editor (`editor/`) | **opens and draws levels** | "Desert Storm II Editor"; Mission 1: 11 zones, 787 props, 112 objects, waypoints, regions, routes; models convert to glTF. Lights show 0; base-mission data (`level_base.json` ...) still DS1's |
| Blender add-on (`blender.py`) | **import + export** | one add-on for both games (the model file decides). Headless Blender 4.2.23 checks: weapon / prop / vehicle (GAZ69B, 19 objects) / soldier skin / animation round trips, edits (longer barrel + muzzle point, Gravel collision, added shadow, head moved, head turned in an animation) re-import correctly, generic prop on `WALLMAP`, Into a Mod Folder (46 checks). **In game 2026-10-22** (background runs, Mission 1, DesertStorm2Fix's Mods folder): Blender-exported Land Rover with a new texture shown, edited `BUNKERGUNNERAIM` (the MK19 gunner: left arm off the gun) and `VEHICLEPASSENGER01` played, a dragon's-teeth prop with rebuilt collision loaded - every file "served", no load errors. Test in Game: starts the launcher (DesertStorm2Fix has `+level` since 2026-10-23, see below) |
| GameCube audio | DS1 only | |

Not copied: `examples/` and `packs/` (DS1 mods built from DS1 data).

## Multi-part skins (2026-10-22)

A character can be several skin (kind 2) objects, each with its own texture and up to 65 535 vertices - no game
model does it (all 28 DS2 / 33 DS1 characters have one skin object; the biggest has 1 506 vertices), but both
engines handle it. From `CDS II.exe` (and `DesertStorm.exe` for DS1):

- **Load**: every EOBJ is read on its own (`FUN_005087b0` / DS1 `FUN_004f7980`); kind 2 → `CSkinObject::Read`
  `0x556B70` (DS1 `FUN_00528de0`). Each skin object gets its own dynamic vertex buffer (vertex count, FVF 0x152,
  36-byte stride, `FUN_005369a0`) and a 16-bit index buffer - the 65 535-vertex limit is per skin object, not per
  model. No bone-count / palette limit: skinning is on the CPU (`0x557530`, DS1 `0x529A30`) from the instance's bone
  matrices (DS2 +0x1C8 by key index, DS1 +0x228 by object slot); a skin object's own matrix isn't used.
- **Draw**: the instance's render loop walks every object and skins + draws every kind-2 one (DS2 `0x55F1F0` loop at
  `0x55F240`, skipping objects hidden in the bitmask +0x240 by key index; DS1 `0x5032D0`). The draw (`0x556290`,
  DS1 `0x52A5A0`) binds the draw's texture argument / the instance's run-time skin texture if there is one, else the
  object's own (`CSkinObject` data +4, from its TEXL hash).
- **Run-time skin texture**: DS2 render instance +0x20C (`SetSkinTexture` `0x55DD60`; `GetSkinTexture` `0x55DD20` =
  it, else the first skin object's texture), DS1 +0x2B0 (`FUN_00503290`). When set it covers **every** skin object.
  DS2 sets it from the soldier skin table only when the instance already has one (`0x475087`), for scene objects
  whose root has flag 0x400 (`0x4CF279`, `0x50AFF5` - no soldier body has it), and on a distance-LOD model switch
  (`0x55FBCE`, instance flag 0x100000: pins the first skin object's texture - not seen on soldiers in the test runs).
  So in DS2 the squad and the enemies show each part's own texture (verified in game). DS1 calls it for the squad
  from the CSkins tables (skin apply `FUN_00435be0`, calls `0x435C9C` / `0x435CC0`) and for script uniforms
  (`0x487A69`): squad bodies wear the uniform on every part; other characters keep their parts' textures.
  DesertStormFix 3.3 (2026-10-22, `bodies.cpp` `SkinDrawStub` at `0x503384`) passes the uniform only to the last
  kind-2 object (the body's main skin slot) and null to the extra parts on bones, so the squad shows the parts too.
- **Helpers that look at "the" skin**: DS2 blood / colourize (`CAnimInstance` +0x228, `0x55D890` / `0x55D8C0`) and
  the LOD pin use the first kind-2 object only - the writer keeps the template's skin object first with the biggest
  part. DS1 `0x5032A0` (texture release) walks all of them.
- **Object count**: DS2 sizes an instance's per-object pose arrays by the root's key count (+0x1C4 = `[[model+0xEC]]`,
  `0x560331`) and walks every object (`0x56125D`, stride 0x18) - an added object needs its own key (an extra object
  without one crashed at `0x56129E`). Animations bind tracks by name hash, so a track-less object is fine.
  DS1 numbers objects by the slot in their header (object +0xF0, the u32 after the parent name = the object's index in
  every game file); an animation has one track per slot and the pose loop reads `[anim+0x20][slot]` unchecked
  (`0x502762`, no bound against the track count), so a DS1 model can't grow - every shipped animation has 51 tracks
  for the soldiers' 51 objects.
- **Writer** (`evo_write.rebuild`, parts[skin] = [SkinPart, ...], each with `texture`): the first part replaces the
  template's skin object; DS2: each other part = a new object `<skin>_<n>` (the skin's header, name hash, its own key
  index = a new key appended to the root's list - a copy of the skin's), TEXL with its texture, the skin's MATL /
  bounds, its SKIN chunk; DS1: each other part is carried by an existing bone (kind 3 → 2, the bone keeps its name,
  parent, slot, flags and collision tree; its marker box goes; smallest bones first - a soldier body has 23 spare),
  because a new DS1 object would read past every animation's track table.
- **In game** (2026-10-22, background runs, DesertStorm2Fix PSE build, Mission 1): the 8 squad bodies and 2 Iraqi
  bodies split into 3 skin objects (red / green / blue test textures) load, animate (sitting in the Land Rover,
  aiming) and draw each part with its own texture - the squad included (no run-time skin texture on them in
  Mission 1: none of the 4 showed the uniform), no errors. A Blender-exported 145 172-vertex body (9 skin objects,
  pieces of up to 65 535 triangles) - see below. DS1 (Steam dev build, Mission 1): the 4 hero bodies and 4 Iraqi
  bodies with 2 parts on bones load and animate; Bradley (run-time uniform +0x2B0 set) wears the uniform on every
  part; with +0x2B0 cleared through the dev server the same body draws red / green / blue.
- **For the plugins** (patch repo): DS2's squad shows the parts' textures as it is. DS1's squad needs a change for
  per-part textures: the draw loop's call `0x503391` (`FUN_0052a5a0(skin, [instance +0x2B0])`) would pass the
  uniform only to the skin object that is the body's original (parent ROOT; extra parts sit on bones, parent = a
  bone) or whose own texture is the body's original one, and null to the others. `bodies.cpp`'s fit check (same bone
  and object count) passes such bodies (the object count doesn't change); its `BindNormalMap` binds one normal map to
  every part (fine while the uniform covers them all; parts with their own UVs would want none or their own).

- **DS2 `.skin` files** (DesertStorm2Fix `ds2fix.skins`, patch repo `games/ds2/src/features/skins.cpp`, modder page
  `docs/sdk/ds2-mods.md`): `body = <MODEL>` builds the soldier's instance on a mod body at creation (scene record +0x64 /
  +0x20 swapped around the CHLOGrunt ctor `0x4835D0`), so bodies with extra keys / skin objects work; a runtime switch
  (`CAnimInstance::SetModel` `0x55F6D0`) only switches between models with the same object and key counts. Per-part
  textures (`texture <object name | n> = X`) are chosen per soldier at the draw call `0x55F334`; parts without a line
  keep the body's own TEXL texture. Verified in Mission 1 with a 3-part body written by `evo_write.rebuild` (42 keys).

## Next

1. Editor: lights (0 drawn), `ENVCOVERPOINT` records, DS2 base-mission data (objectives / API slots from the
   MissionNN.dll scripts), the new HLOI / ENVI fields named once their meaning is known.
2. The editor's terrain patches, roads and kit pieces for DS2 (the model writer handles DS2 now; `kits.extract` cuts
   DS2 pieces - EOBJ 0x35 parent at +0xAC - byte-identical with DesertStorm2Fix's `.uses` `piece` lines, 2026-10-22).
   Game references (2026-10-22): `games.Game.game_refs` - the editor's Save as mod writes `<LEVEL>.envpatch` (+
   `.rmdpatch` when the routes changed) instead of copies of the game's scene / routes; DesertStorm2Fix
   (`ds2fix.modlevels`) builds them from the player's install, rebuilds the octree and the AI navigation (hand-placed
   markers kept). `.dllpatch` / `<Level>.level` work too (patch repo `docs/sdk/ds2-mods.md` "Levels and missions").
3. A human play-test of exported models (walking into rebuilt collision, a soldier body, a weapon in hand).

## Lua missions in DS2 (patch side, 2026-10-23)

DesertStorm2Fix runs `.lua` levels now (`ds2fix.scripthost`; patch repo `docs/sdk/ds2-mods.md` "Lua missions and custom
campaigns"): `--@archive` / `--@base MissionNN.dll` / `--@scene` headers, the `game.*` API on DS2's slots, objectives,
timers, regions, saves, `campaign.txt` campaigns, and `"CDS II.exe" +level <mission>.lua` (launcher Play pressed by
itself). So the editor's **Play** (`commands.play`: save_mission + `<exe> +level <mission>.lua`) has what it needs on the
game side. Still missing on the editor side (not built): a DS2 profile for `save_mission`'s generated script - its
set-up calls and rule actions use DS1 API slot numbers (`game.call(0x4B0, ...)`, `level_base.json` = DS1 missions);
DS2 needs DS2 slots (`editor/games/ds2/api_slots.json`; objectives through `game.add_objective`, which the DS2 host
maps itself) and the DS2 base missions' set-up (Mission01.dll init: sound banks, `M01Strs.loc`, scene, map image +
bounds, mission index / texts). `_require_script_host` should accept DesertStorm2Fix for DS2.
