# Supported games

One kit and one editor for Pivotal's engine line. The game is told by its exe (game folder) or by the `.game`
marker the extractor writes; formats are read by the version stored in each file. Profiles: `conflict_ds_tools/games.py`.
Per-game editor data (script API names, base missions): `editor/games/<id>/`.
Detailed format notes from the pre-merge forks: [status/DS2.md](status/DS2.md), [status/VIETNAM.md](status/VIETNAM.md),
[status/GLOBAL_STORM.md](status/GLOBAL_STORM.md).

| Game | Id | Exe | Archives | Scenes (.ENV) | Models (.EVO) | Routes | Editor | Saves for the game |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Conflict: Desert Storm (2002) | ds1 | DesertStorm.exe | 0x8000, upper-case hash | v70–72, all 28 | EOBJ 0x2F, all 7086 | .rmd v1 | full | DesertStormFix: mods, Lua missions, Play |
| The Great Escape (2003) | ge | GreatEscape.exe | 0x8000, lower-case hash | v99, all 18 | EOBJ 0x34, all 4643 (127 characters fit) | own .rmd (80-byte markers) | opens, draws, edits | no plugin yet |
| Conflict: Desert Storm II (2003) | ds2 | CDS II.exe | 0x8000, lower-case hash | v106, all 14 | EOBJ 0x35, all 5565 (written too: Blender export) | .rmd v3 | opens, draws, edits (+ zone chunks, cover points) | DesertStorm2Fix: Mods folder (files by name), Save as mod writes `.envpatch` / `.rmdpatch` (no game data; octree + AI navigation rebuilt in game), mission DLLs / `.dllpatch` / `.level`, no Lua host yet |
| Conflict: Vietnam (2004) | cv | Vietnam.exe | 0xC000, lower-case hash | v150, all 22 | EOBJ 0x40, all 7107 models | .rmd v4 | opens, draws, edits, lights | VietnamFix: Save as mod loads in game |
| Conflict: Global Storm (2005) | cg | ConflictGlobal.exe | 0xC000, lower-case hash | v197–209 split (.ART/.DES/.SND), all 16 | EOBJ 0x49, all 10902 | .RDA (+ walk map) | opens, draws, edits, lights, .SI objectives | GlobalFix: not ported |
| Conflict: Denied Ops (2008) | do | ConflictDeniedOps.exe | 0xC000, lower-case hash, `*.pc.dat` | v304-315 split, all 10 | EOBJ 0x54 inside `APKG` packages (unpacked by `apkg.py`): 748 of 754 in Comms Base incl. characters (cloth / shatter pieces not yet) | .RDA v8, all 10 | opens, draws, edits scenes and routes | - |

All counts: every shipped file of the PC releases read, scenes and routes written back byte for byte.

## Notes per game

- **Desert Storm**: the reference; everything the editor does was built and checked against it in game.
- **The Great Escape**: same engine generation as DS1 / DS2. Models: DS1's object layout with a 20-byte block and a
  key index after the parent name (skin bones first, as DS2 orders them), DS1's 44-byte skin influences, no extra
  word in the key block. Scenes: objects have two extra name lists in the AI block (from v54 / v70) and one value
  more before the AI list (v66); route markers carry a weight, an int and a distance per link. Two scenes mix LF and
  CRLF line ends (kept as they are when unchanged). Reader functions: object 0x481290, object record 0x482B20, markers
  0x412C40 (GreatEscape.exe in the Ghidra project `/greatescape`).
- **The Great Escape in Blender** (import + export): the header is DS2's 20-byte block (triangles, vertices, buffer
  count, 5, 5) and parent name, then ONE i32 key index (+236, no 65535 word) - the bone list starts at +240, 4 bytes
  before DS1 / DS2's. Everything else DS1's (VBUF 6, 44-byte influences, collision trees u 0 / 1) with DS2's ordering
  (keys and skin bone numbers by key index, extra skin parts = objects of their own) and DS2-style shadow objects
  (type -1, counts 0, buffer count 1). Animations: DS2's `.prb` layout (tracks by object name hash) with **40-byte
  keys** {pos, scale, quat (stored conjugated like DS2)}; 368 of 393 fit every human body (the rest: dog, chicken,
  rat, bike). Default.spl = DS2's 63 rows. Checked on all 2413 models (`scripts/check_models.py --game ge`) and all
  393 animations (`scripts/check_animations.py --game ge`: rewritten byte for byte). GreatEscapeFix serves
  `<game>\Mods` by file name, so Into a Mod Folder writes there (a new weapon model gets a starter `.weapon`).
- **Mission objectives** (Desert Storm II, Vietnam, Global Storm): each level's `<LEVEL>.SI` (MISSION_SCRIPT_INFO,
  versions 4 / 12 / 23; `conflict_ds_tools/scriptinfo.py`) - shown in the editor with their texts (the games' text
  bundles in catalog.dat and each level archive): DS2 104 objectives, Vietnam 130, Global Storm 173. DS2's script
  addresses them by index (complete 0x5AC, fail 0x5B4, reopen 0x5BC).
- **Desert Storm II models and animations**: the Blender add-on imports and exports them like Desert Storm's -
  `evo_write.py` follows the template's EOBJ version (DS2: 20-byte header block, parent name at +172, VBUF version 7,
  42-byte skin influences numbering key indices, `#SHA_` objects of type -1), `.prb` tracks name their object by its
  header hash (keys 28 bytes, no scale; CAnim binary load 0x5625D0), collision surfaces include Default.spl rows 51-62
  (Snow, Soft Mud, Tree Canopy, Gravel, Plastic, Rubber + Blocking twins). Checked offline on all 3280 models and all
  2998 animation files (`scripts/check_models.py`, `scripts/check_animations.py`) and in the game through
  DesertStorm2Fix's Mods folder (Mission 1: a re-textured Land Rover, edited gunner / passenger animations, a prop's
  rebuilt collision - served and used, no load errors).
- **Multi-part characters** (1.8.0, both games): one skin object per material / per 65 535 vertices or triangles,
  each with its own texture - DS2 adds objects + keys, DS1 puts up to 6 extra parts on spare connectors (its animations have
  one track per object). In game DS2 shows every part's texture; DS1's squad wears its run-time uniform on every
  part. Engine notes: [status/DS2.md](status/DS2.md) "Multi-part skins".
- **Desert Storm II**: script API names for the mission-logic view: DS1's carried over by
  `editor/games/ds2/api_align_ds1.txt` (instruction similarity; 330 slots, confidence capped by the match) plus the
  objective slots read in CDS II.exe. Generic soldiers' skins are found by name (GEN_DF_SOLDIER -> DF_SOLDIER_01); Jones' bomb
  disposal model and 534_POLY_SOLDIER have no texture of their own in the data.
- **Vietnam**: base-mission data recorded with DS1's script API rules (meanings unchecked; its API is laid out
  differently); Save as mission / New level / Play are refused until VietnamFix has a Lua host.
- **Global Storm**: scenes are split into part files (joined while editing, written back per part); objectives come
  from the level's `.SI`; no script API names yet.
- **Denied Ops**: models, textures and materials live in APKG packages inside each level archive; the editor unpacks them once (`apkg.unpack_extracted`: `<MODEL>.EVO` = the EOBJ 0x54 stream, `<TEXTURE>.DDS`, `_MATERIALS.TSV` material -> texture slots; ~10 s for all levels) and finds a mesh's texture through its material. Scenes: ENVI / PHYI (physics props, read as ENVI) / HLOI / REGN / ENVSND layouts of v300+ in `env.py`, parents from `<SGLINKS>` (`"@zone"` = top level). Routes: `.RDA` route block version 8 (58-byte markers with a distance per link, recomputed for moved markers). Characters: skinned bodies and heads on the shared skeleton (`C5_SKELETON`; keys stored in key-index order, each object's hierarchy trailer names its key, influences number key indices, rotations as stored), body texture and head from the unit table in catalog.pc (an unnamed `_A80473CD.BIN`, Global Storm's UNITS.TXT columns), a head named on an HLOU line wins, attachments by bone. Blender: its own add-on (`conflict_do_tools`, import only) - models, characters, scenes and archives; Comms Base: 748 of 754 models, the scene's 2788 objects, an unchanged scene exported back byte for byte (not offered yet). Not yet: cloth (birds, tracks, flags) and shatter pieces (drawn as placeholders in the editor, refused with a message in Blender), writing models (they go back inside APKG packages; DeniedOpsFix's Mods folder replaces whole files / whole packages by name, and the packages' file names aren't known - the extractor writes `_<HASH>.BIN`), saving scenes (Save as mod is refused), scripts. Research notes: `ConflictGamefiles/_analysis/DeniedOps_tools/crate/*.md`.
  the art lives in `APKG` packages - a separate project.

- **Mission DLLs** (Desert Storm): `list_dll_missions` / `open_dll_mission`. Export ships a DLL made from a game DLL
  as a `.dllpatch` (`gamerefs.make_dll_patch`; format read by DesertStormFix mods.cpp `ApplyDllPatch`), and a DLL of
  its own as it is. MAKING_MODS 7.3b.

## Models between games (Blender)

Plan for porting whole asset packs, levels and missions between games: [PORTING_PLAN.md](PORTING_PLAN.md); the
batch porter for models and animations into Desert Storm: `cli/ds_port.py <recipe>` (`conflict_ds_tools/porting.py`).

A static model imported from one game exports into another through the target game's File > Export entry
(blender.py, `cross` in EXPORT_SCENE_OT_evo): the target's generic prop (or a target model under Based On) takes the
source's render parts as one static object; markers (connector / hit-region meshes) are left out; collision surfaces
the target's Default.spl lacks go through `bsp.convert_surface` (SURFACE_FALLBACK - every chain ends in DS1's 51 rows);
the source game's textures are saved as `<SOURCE ID>_<name>` (e.g. `DS2_WALLMAP`). Done for Desert Storm <-> Desert Storm
II (the other writers take the same path: Great Escape, Vietnam / Global Storm static models).
Characters (DS1 <-> DS2, and DS1 / DS2 -> Vietnam / Global Storm): onto GENERIC_CHARACTERS (or a Based On body); `cross_repose` moves the skin from the source
skeleton's rest pose onto the target's by linear blend skinning (target bone x source bone^-1 per weight; both games'
rigs share the Biped bone names - DS2's rest is DS1's about 4 cm lower); weights on bones the target lacks (DS2's eye /
eyelid / jaw bones) go to the nearest ancestor it has; rigid parts hung on a bone get its full weight; the source's
per-bone helpers are left out (the body keeps the target's hit regions). Vietnam / Global Storm bodies hold only the
skin and are written against their skeleton file (`rebuild(..., skeleton=)`); the generic bodies come from the game's
archives into a cache folder together with MPD_SKELETON / C04_SKELETON[_FEMALE] (.EVO + Global Storm's .SKL bone
lists, which its animations bind to). Several skin parts (one per material - e.g. DS2's eyes / eyelids / teeth) =
several skin objects in the body file, copies of its skin object named <name>_2 ... (shipped bodies have one;
both games drew a two-part body in game, 2026-10-28).
Animations: the import retargets an animation of another game (`evo.retarget`: the source game's soldier skeleton,
ANIM_SKELETONS, found next to the animation file; every target skeleton bone takes the source bone's world transform
by name each frame - what the converted skins need -, connectors and bones the source lacks keep the target's rest
offsets). DS2 animations bind to DS1 skeletons by name hash anyway, but are retargeted all the same (DS2's rest is
DS1's 4 cm lower).

## Not yet for every game

- Writing models: Blender export for Desert Storm, The Great Escape and Desert Storm II in full; Vietnam / Global Storm
  static models, collision, points, character skins with one or several parts and added shadow meshes
  (`evo_write.rebuild_cv`); terrain patches,
  roads and kit pieces (editor) only for Desert Storm.
- **Vietnam / Global Storm model layout** (loaders traced: Vietnam object header 0x4E8F48, VBUF 0x4F09A3 / streams
  0x4F074C, SKIN 0x4FBD33, SCON 0x4FD34F, hierarchy 0x4FA443; Global Storm 0x5E4B36 / 0x5EE26F / 0x5EE045 / 0x5FC6C0 /
  0x5FDBFE / 0x5FA7F6; every shipped file parses to its last byte): the first object's version must be 0x30040 /
  0x30049 and its type (0 static, 1 vertex anim, 2 hierarchical, 3 landscape, 4 water) is every object's; header
  +152 / +156 triangle / vertex sums (unused by the loader), +160 = VBUF count (Global Storm keeps 5 bits: <= 31),
  +420 BSPT count (Global Storm: 3 bits); SCON {n, a, b; n x vec3 + 7 (Global Storm 12) role indices; a: 24 B; b:
  48 B}; one BBOX / BSPH; Global Storm kind 0: u32 LOD count + LOD VBOBs after the buffers; trees: u 0 collision, u 2
  a second tree falling back to u 0, u 1 not loaded - Desert Storm's tree layout; type-2 files: hierarchy block after
  the trees (parents before children). VBUF 7: ..., u32 1, z0 (0 = 32-byte vertices / format 4, else 40-byte with a
  second uv / format 5), format id, stride, detail texture hash + u / v tiling, vertices, colours only with flag 0x80,
  strip, list, VSTP, FACE (recomputed by the game), VBPL, two "uv within one tile" words. SKIN: 42-byte influences
  (u16 bone = the skeleton file's key index), SSTP = one u32, SHAD n x 29 B kept by the game (neighbours by position;
  unused bytes 0xCD); the writer gives small bodies a list only (has-strip 0) when a strip would be longer than it.
- Blender import: every game's models, characters (armature + weights - checked: bones on the model's objects, bodies
  standing), animations (all but Denied Ops) and level scenes with their lights and ambient sounds (Vietnam / Global
  Storm light records use env.py's named fields - levelscene `_named_light`; moved / turned lights are written back
  by sceneexport `_light_keys`).
- Animations (`.prb`): read + written for Desert Storm, The Great Escape, Desert Storm II and Vietnam (all 409
  rewrite byte for byte; `evo_write.write_prb_cv` / `cv_deltas`: delta = key x rest^-1, a track gets flag 4 when a
  key leaves the rest position, zero vectors keep their sign - the game files hold -0.0; keys per second differ per
  track and are kept). Vietnam's layout: DS2's header and track header {u32 name hash, u32 flags, u16 keys, u16 keys per second, u32 -1}, flag 4 =
  28-byte keys {quat x y z w, pos} (pos absolute), else 12-byte keys = rotation vector (axis x angle, |v| <= pi, pos =
  rest); every rotation is a delta on the bone's rest rotation: local = delta x rest (evo.Animation._resolve; decided
  on standing idles + walk cycles - the other readings lie the body down or sink the feet). `_UB` animations are
  upper-body layers. Tracks the game's own carry (257 fitting MPD_US_MOTOWN): ROOT_OBJECT (256 - the walk's travel),
  every body bone and their connectors; never the face bones - the exporter's set without a template.
- **Global Storm animations** (read: `evo.read_ani_cg`; written: `evo_write.write_ani_cg` / `cg_tracks`): the unnamed `.BIN` files named `STATE_*` / `OVERLAY_*` =
  the game's `Animations\<name>.ani` (loader 0x61A1A0, exporter 0x619A80; 465): name[44], f32 duration, u32 A
  position tracks, u32 N rotation tracks, u32 B constant rotations, u16 position keys, u16 rotation keys, u32 C root
  samples; N x {u32 bone, f32 (ignored), keys x quat}, A x {u32 bone, f32, keys x vec3 offset}, B x {u32 bone, f32,
  quat}, C x {vec3, quat} (30 Hz root motion; its use not traced). Bones = `.SKL` indices: u32 n + {name, i32 parent,
  vec3 LocalPosition, quat FixOrientation}, IK handles, BoneOrder. Pose (0x612EE0): local rotation = key (absolute,
  identity without a track), local position = LocalPosition + offset, world_q = parent_q x local_q, world_p =
  parent_p + R(parent_q) local_p, final rotation = world_q x FixOrientation (not inherited) - rebuilds
  C04_SKELETON.EVO's rest pose exactly; the walk keeps the lower foot at 8.8-12.9 cm. Layers (lower / upper /
  overlay) blend by track set. Writing (all 465 rewrite byte for byte - two list a constant bone twice, the reader
  keeps cg["items"] in file order): per .SKL bone, own rotation = world x FixOrientation^-1, local = parent^-1 x
  own, offset = R(parent)^-1 (p - parent p) - LocalPosition; changing rotations = a track, constant ones = a
  constant, offsets over 0.01 cm = a position track; per-track float 1.0, name upper case + NUL + 0xCC fill. A
  new animation has no root-motion samples unless a game animation of its name gives them (their use isn't traced).
- Script API names (`editor/games/<id>/api_slots.json`): Desert Storm, Desert Storm II (aligned); Vietnam / Global
  Storm not yet (their tables are bigger and differently ordered - Vietnam's named movie-event table at 0x6E2Exx is
  not the API).
