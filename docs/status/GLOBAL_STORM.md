> Snapshot of the pre-merge fork's status notes (`ConflictGlobalModKit`, 2026-10-10), kept for its research details.
> Paths: `conflict_cg_tools` is now `conflict_ds_tools`, `editor/*.json` is `editor/games/<id>/`. Current state: [GAMES.md](../GAMES.md).

# What works on Conflict: Global Storm data

Copied from ConflictVietnamModKit on 2026-10-10 (Vietnam / DS1 / DS2 keep improving - port their changes later).
Checked against the EU Global Storm install files (`ConflictGamefiles\_extracted\GlobalTerror\GlobalStorm\install`,
`ConflictGlobal.exe` 2005-08-17, 14 missions + Training00A/B).

| Part | State | Notes |
| --- | --- | --- |
| `.dat` archives (`archive.py`) | **done** | same hash as Vietnam (LFSR over the lower-cased name; `mission01.env` = 0x5A909764), data from 0xC000. 25186 files, 16992 named. Extensions added: ART DES SI SND MVS C4K LOC FX PCR ... |
| Scenes (`env.py`) | **reads all 16** | Version 197-209. `<LEVEL>.ENV` is a SPLIT index -> `.ART` (world, portals, lights, atmosphere), `.DES` (objects, waypoints, cover points, regions, switches, cameras, mechanims), `.SND` (sounds); `.SI` / `.RDA` not text. `EnvFile.load` joins ART + DES + SND with `#PART <file>` lines; `split_files()` gives the parts back (byte-identical on all 16). v190+ layouts from the data (loader not traced): ENVI 4 ints + name + type (0 prop, 2 animated, 3 zone) + model + 2 textures + transform; HLOI/HLOU 3 ints + [profile] + model + zone + body + 3 textures + transform ... unit, script id, stat; waypoints, ENVSND; tails kept positional (`t0..`). 0 parse problems |
| Models `.evo` EOBJ 0x00030049 (`evo.py`) | **10902 / 10902 read** | Vietnam's 0x40 path + VBUF vertex stride 40 (level geometry: second UV = lightmap, `mesh.uvs2`) + one u32 after the VBOB list unless the object has flag 0x8000 (skeleton bones); cloth (kind 1: birds, tank tracks) = 32-byte vertices with no colour block, then 24-byte frames; water (kind 4, `WCOB`): columns x rows grid, texture hash before material hash, 28-byte vertices {x, ?, z, u, v, height, ?} |
| Soldiers | **drawn** | body = HLOI model (skinned) on `C04_SKELETON` or `C04_SKELETON_FEMALE` - picked by fit (`evo._skin_spread`: Sherman 0.02 cm female vs 32 cm male); body texture = HLOI `body`, head model + texture = UNITS.TXT cols 6 / 7 (HLOI `head` overrides; `NOTEXTURE` = unit default). UNITS.TXT is the unnamed `catalog/_A8845E98.BIN`. Attachments (HLOI tail `"bone" n "model" f`: hats, radio, pouches, holsters - rigid, in bone space) parented to that bone of the body in the page |
| Record textures | **applied** | an ENVI's `texture` (HLOI vehicles: `body`) replaces the model's first TEXL texture (`evo.resolve_textures(override=)`): cars' `C04_V_TextureOverride` placeholder (magenta / yellow) -> `C04_V_CivCar01_Black` ..., crates / barrels / doors -> regional variants |
| Lights | **drawn, editable** | DLGT / PLGT / SLGT v190+: id, name, flags, 2 ints, ambient RGBA, colour RGBA, then point: pos, radius, intensity; spot: pitch, yaw, roll, pos, inner / outer cone, range, intensity; sun: pitch, yaw, roll, pos, intensity. Lights layer (bulb in the light's colour, reach sphere / cone / arrow); 1555 lights in all levels |
| AI points | **drawn** (layer off by default) | `.DES` NEWPOSMARKER (id, pos, 2 ints; grouped by NEWJUNCTION) + ENVCOVERPOINT (id, int, name, pos, rot, facing, arc, ...) |
| AI routes + walk map | **read, editable, saved** | `<LEVEL>.RDA` = `" VER"`, u32 1, level bounds (6 f32), the walk-map quadtree exactly like DS1's `.qtd` (u32 count, 20-byte nodes, tail = walkable / blocked counts; kind 3 walkable leaf, 0 blocked), then a route file (-1, version 5, count, 33-byte markers = Vietnam's v4 layout), then door data. `routes.RouteFile.read_rda` / `to_rda_bytes` (byte-identical on all 16; 84-2731 markers, every link valid; each walkable leaf has its marker). Editor: AI routes layer + route_* commands, AI walk map from the .RDA, `save_mod` writes `<LEVEL>.RDA` only when the network changed |
| Mission objectives | **shown** (read-only) | `<LEVEL>.SI` (MISSION_SCRIPT_INFO v23, `conflict_cg_tools/scriptinfo.py`): sections BEGIN_x n ... END_x; objectives parsed (name, XP, award, result + delay, completion mode - SCRIPT_TRIGGERED, ALL_HLOS_DESTROYED, COLLECTER, player(s) in region, picked-up item ..., message keys, status, priority, SP + MP title / text keys, regions / HLO names (= HLOI stat field) / waypoints, objective ids, text points). 183 objectives over the 14 missions (training: none). Objectives dialog: title / description from the level's text bundle, "Completed when ...", primary / bonus / script helper, clickable targets; `#objectives` link opens it. Other .SI sections (spawn points, alarm systems, mounted guns, XP bonus, medals, times, grades) kept as raw tokens |
| Saving (`save_mod`) | **split-aware** | writes only the part files that changed (`MISSION01.DES` after moving a soldier: 1 line) - the game side (GlobalFix mods folder) isn't ported |
| Texts | **read** | `records.Records.read_bundle`: first file of catalog.dat (shared, 1495) and of each level archive (shared + that mission's .loc: source M01 ... / T00A / T00B) - u32 0, n .loc names, count, languages, offsets (from 4 bytes after the list), per language {size, sorted {hash, offset}, UTF-16}. Key hash = the archive LFSR; 542 / 2277 keys named from identifiers in the DLLs / tables / archive text files (+ numbered variants), the rest `#<hash>` (`hash_key` accepts it). Editing writes `.strings` as before - the game side isn't ported |
| Editor | **opens and draws every level** | "Global Editor", port **8790** (Vietnam's uses 8780). Zones, props, objects, waypoints, regions, cameras, sounds, switches |
| Not yet | | the game loading saved mods (GlobalFix port), the MissionNN.dll logic view (`api_slots.json` is DS1's - GT's API has 887 slots), .SI spawn points / alarms on the map, editing objectives |

## Run

```
python cli/ds_extract.py -g "<GT install>" -o extracted
python editor/server.py --data extracted --game "<GT install>"      # http://127.0.0.1:8790/
```
