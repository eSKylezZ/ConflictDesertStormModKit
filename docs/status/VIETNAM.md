> Snapshot of the pre-merge fork's status notes (`ConflictVietnamModKit`, 2026-10-10), kept for its research details.
> Paths: `conflict_cv_tools` is now `conflict_ds_tools`, `editor/*.json` is `editor/games/<id>/`. Current state: [GAMES.md](../GAMES.md).

# What works on Conflict: Vietnam data

Kit copied from the Desert Storm II kit on 2026-10-10 (`conflict_ds2_tools` → `conflict_cv_tools`, Blender ids
`cv_*`). Checked against the PC install (`Vietnam.exe`, Sep 2004 build, 14 missions,
no training levels, no chardata.dat). DS1 / DS2 improvements still to be ported over later.

| Part | Vietnam | Notes |
| --- | --- | --- |
| `.dat` archives (`archive.py`) | **done** | lower-cased LFSR hash like DS2; directory block is **0xC000** in the bigger archives (was 0x8000) - read up to the first entry's offset. Names recovered for a minority (dictionary still DS2's) |
| Game folder detection | **done** | `Vietnam.exe` + `catalog.dat`; registry `SOFTWARE\Global Star Software\Conflict Vietnam` (the exe's own key) |
| Routes `.rmd` v4 (`routes.py`) | **done** | 33-byte markers {pos, weight, f32 (1.0 everywhere), 5 links, u16 flags, u8}; all 12 named files round-trip byte-identical |
| Models `.evo` 0x40 (`evo.py`) | **reads every model: 7107 / 7121** (the 14 others are copies of one non-model file) | mapped from the data, version check confirmed in the exe (`0x4E9BF3`: 64, sub-version 0 / 3). Header: u32 type (0 static, 2 hierarchy) before the names, no parent / bone list there; points in an `SCON` chunk (u32 n, a, b, n x vec3, 7 roles - DS1/2: 5, [b: 6 int pairs]); one BBOX/BSPH pair; VBUF 7: 3 extra u32 + stride 32, vertices {pos, normal, uv}, colours as a separate BGRA array only with flag 0x80; untextured buffers with flag 0x04 and not 0x01 are invisible blockers (`Mesh.invisible`, hidden in the editor's view); hierarchy block (part name, parent, 2nd box, key index, bones, has-keys, char[128]) **after** the collision trees, keys in `TMDT`; rotations stored as-is (DS1/2: conjugate). Kind 4 = water grid (`WCOB`: columns x rows points {x, ?, z, u, v, y, ?}); kind 1 cloth (tracks, birds, fish) as DS1 with the 32-byte vertex and no colour array. Soldiers' bodies skin against the shared `MPD_SKELETON.EVO` by key index (`evo.attach_skeleton`): stand 1.8 m, median 1.2 cm between a vertex's bone placements (95 % < 9 cm - bind pose not exact yet) |
| Scenes `.ENV` v150 (`env.py`) | **done** | layouts from Vietnam.exe's loader (`0x592740`; per-record addresses in the readers' comments). All 22 scenes: 0 problems, byte-identical round trip; 49806 ENVI, 3907 HLOI, 1854 PORT, 8995 waypoints, 2623 cover points, 445 emitters, 1009 lights, 244 cameras typed. New: ENVFOGSETTING (+ ENVI `fog` = its id), ENVI `list2` / ANMI / VAMI as whole ENVI records / RINFO lines (flag 0x1000000), HLOI `list2` (+ HLOS 4 names, code only), PORT `u5`/`u6`, waypoint float before `next`, ENVSND list + 4th trailing int, typed lights + GLOW loop, emitter flags moved before the velocities, typed cameras, ENVCOVERPOINT. NEWENVMARKER: keep the file layout (the game's v43+ reader misreads it and discards everything). World zones are ENVI **type 3** (DS1/2: 5) - `env.ZONE_TYPES` |
| Editor (`editor/`) | **opens, draws and edits all 14 levels; Save as mod works** | "Vietnam Editor", port **8780**. Sweep: every level opens and every model it uses converts (0 missing / failed). Smoke test (Mission 1): move, set field, duplicate, undo / redo, place, waypoint / region / route marker / emitter add, search, delete, scene_problems (0), check_mission (0 errors), save_mod -> `<LEVEL>.ENV` + `.RMD` with 0 problems. Zone chunks placed on their own (`<MODEL>NNN`) are served as that one object. `GLOBALZONE` (engine zone, 1286 links) counts as a valid parent; waypoint writer + count line follow v150. **Save as mission / New level / Play are refused**: they need a Lua mission host in VietnamFix, and Vietnam's script API table is laid out differently (scripts/gen_level_setups.py). level_base.json is empty per level (objective / object-id slots not mapped). Missions 07 / 13 ship no .rmd / .qtd. Lights typed but not drawn. **Verified in game**: a Save-as-mod Mission 1 with an added Huey loads through VietnamFix's Mods folder and the Huey shows in play (background run, 2026-10-10) |
| `.sch`, `.prb`, Blender export, GameCube audio | not checked | |

Not copied: `examples/`, `packs/`, `docs/screenshots/` (DS1 content).

## Next

1. Done 2026-10-10: Save as mod -> VietnamFix Mods folder -> in game (background run, Mission 1: the editor-placed Huey stands 6 m in front of the start). Next: more edit types in play (moved props, waypoints / patrols, routes), Save as mission once a Lua host exists.
2. EVO: exact soldier bind pose; meaning of the 7 point roles / the int pairs; evo_write for 0x40 (Blender export).
3. Name dictionary for Vietnam (more file names on extraction).
4. Vietnam's script API slot map (MissionNN.dll calls; the table at LevelSetup) → level_base.json, Lua host.
5. Check whether HLOI `in_play` means the same in Vietnam (176 of 190 Mission 7 objects "wait for the script").
