# Pokémon Platinum (US rev 0) memory map — verified

ROM: `POKEMON PL` / CPUE, SHA1 `ce81046eda7d232513069519cb2085349896dec7`. Reference: pret/pokeplatinum (structs and
code; it has no symbol addresses, so every address was found in the running ROM — scans for known structures,
pointer chasing, disassembling a few Thumb instructions). Everything below is asserted by
`python dev/platinum/verify.py` (83 checks, ~45 s, fixtures `dev/states/pt_*`). Adapter: `client/yellowlink/platinum.py`.

## Big picture

* All game data lives in main RAM 0x02000000-0x023FFFFF and is **heap-allocated**. The heap layout depends on how
  the game was entered: SaveData is at 0x0227E1CC after NEW GAME but 0x0227E1AC after CONTINUE (FieldSystem
  0x0229F9A8 vs 0x0229F988, ...). Never hard-code a heap address — follow the static pointers below.
* The overworld logic runs at 30 Hz (object positions change every 2nd frame).
* Platinum keeps the **whole save image in RAM** (SaveData body, 128 KiB) from power-on: the trainer, party, PC
  boxes are readable even on the title screen; the game edits them in place and an in-game save writes them to flash.

## Static pointers (ARM9 .bss — fixed addresses)

| address | what | how found | verified |
|---|---|---|---|
| 0x02101D28 | `sApplication` (main.c): +0 currOverlayID, +4 currApplication, +8 nextOverlayID, +0xC nextApplication, +0x10 args {unk_00 = -1, error, saveData} | pointer scan for SaveData → 3 hits; the one at 0x02101D40 is preceded by -1, 0 (= `args.unk_00 = -1`, `error`) and a pointer to an ApplicationManager whose `args` (+0x18) = 0x02101D38 | all states: args = {-1, 0, SaveData}; currOverlayID 77 on title/intro movie, 97 on the main menu, -1 in the field |
| 0x02101D2C | `sApplication.currApplication` = the "well-known cheat base pointer" | (as above) cheat offsets work because the heap layout is deterministic: e.g. party mon 1 = [0x02101D2C] + 0xD094 | — |
| 0x02101D40 | `sApplication.args.saveData` → **SaveData** | page table inside it matches the decomp's save table | every state (new game + continue boots) |
| 0x021C0794 | another SaveData pointer (probably `sSaveDataPtr` of savedata.c) | pointer scan | same value as 0x02101D40 in all states (not used) |
| 0x021C07DC | **`sFieldSystem`** (field_system.c) → FieldSystem | pointer scan for the FieldSystem (found via `fs->saveData`, `fs->location` = body+0x1280, `fs->mapObjMan`) — 0x021BF680 also points to it in the field, but holds other things in battle/menus | NULL on boot/intro/title/main menu; = top-level app's data in the field incl. battles and the PC UI. Not cleared on teardown → only trusted when `currApplication->template.main == 0x0203CCCD` (ExecuteFieldProcesses) and `currApplication->data == sFieldSystem` |

## SaveData (address from 0x02101D40)

| offset | field | verified |
|---|---|---|
| +0x00 | backupExists | 1 in all states |
| +0x04 | **dataExists**: 1 once a save was loaded at boot or written | 0 in pt_room/pt_town (never saved), 1 after the in-game save, on title/main menu with a save, 0 with a blank battery |
| +0x08 | isNewGameData | |
| +0x14 | **body** (0x20000 bytes) | |
| +0x20024 | page table, 38 x {u32 id, u32 size, u32 offset, u16 checksum, u16 block} | read back in every state |

Body page offsets (from the page table; identical in all states): SYSTEM 0x0, **PLAYER 0x64**, **PARTY 0x98**, BAG 0x630,
VARS_FLAGS 0xDAC, POKETCH 0x1160, **FIELD_PLAYER_STATE 0x1280**, POKEDEX 0x1328, DAYCARE 0x1654, …,
**PC_BOXES 0xCF2C** (block 1 "boxes", size 0x121D0). The general block is 0xCF18 bytes + 0x14-byte footer.

### Trainer (body + 0x64 PlayerSave: +0 Options u16, +4 TrainerInfo)

| body offset | field | verified |
|---|---|---|
| 0x64 | Options u16: textSpeed bits 0-3 (0 slow, 1 mid, 2 fast) | pt_room 1; set to 2 by RAM write during the Route 201 event (all later states/saves have FAST text) |
| 0x68 | name, 8 x u16 Gen-4 chars, 0xFFFF-terminated | "LUCAS" in all states, also in the .sav and on the trainer card |
| 0x78 | id u32 = TID (low 16) / SID (high 16) | 33356 / 2998 = trainer card ID No. and the starter's OT TID/SID |
| 0x7C | money u32 | 3000 → 3500 after beating Barry; RAM write 123456 → trainer card shows ₽123456 |
| 0x80 | gender u8 (0 boy) | 0 (boy chosen); also PlayerAvatar+0x20. Girl not tested |
| 0x81 | language (2 = English) | |
| 0x82 | badgeMask u8 (popcount = badges) | 0; RAM write 0x05 → save dialog shows "BADGES: 2" |

### Party (body + 0x98)

`u32 capacity (6)`, `u32 count`, then 6 x 236-byte Pokémon (136-byte encrypted box part + 100-byte encrypted party
part). Verified: decrypting (PID/checksum, block shuffle `((PID>>13)&31)%24`, LCRNG 0x41C64E6D/0x6073 seeded by the
checksum; party part seeded by the PID — `dev/platinum/gen4crypt.py`) gives TURTWIG (387), OT LUCAS 33356/2998,
valid checksum, level 6/7, in pt_starter/pt_pc; the PK4 agent verified pk4.py re-encrypts byte-identically.

### PC boxes (body + 0xCF2C = PCBoxes)

| offset | field | verified |
|---|---|---|
| +0x0 | currentBoxID u32 (0-17) | 0; switching boxes with R in MOVE POKéMON mode changes it **live** to 1 |
| +0x4 | boxMons[18][30], 136-byte encrypted box records | empty slot = 136 zero bytes encrypted with checksum 0 (all 540 in every state); deposit writes slot 1 of box 1 immediately (same PID as the party mon, **PP restored**, checksum valid), party count drops to 1 at once |
| +0x11EE4 | names[18][20] u16 | "BOX 1".."BOX 18" |
| +0x121B4 | wallpapers[18] | 0..15, 0, 1 |
| +0x121C6 | unlockedWallpapers | 0 |

"Initialised": PCBoxes_Init runs at NEW GAME (names/wallpapers/empty slots are valid in pt_room, before any save).
There is no Crystal-style "first save erases the boxes" step → boxes may be read/written whenever in game & safe.
(In a fresh-boot RAM the boxes were at 0x0228B10C, mons at 0x0228B110 — new-game layout only.)

## FieldSystem (address from 0x021C07DC; only valid while in_game())

| offset | field | use / verified |
|---|---|---|
| +0x00 | processManager → {parent app (field map), child app, pause, kill} | idle: parent ≠ 0, child = 0. Battle: parent = 0, child = battle app. PC UI: parent = 0, child = box app |
| +0x0C | saveData | = SaveData (sanity check) |
| +0x10 | **task** (FieldTask*) | non-NULL while any script/event, text box, start menu, PC menus, door/warp transition, battle runs; NULL when the player has control (it is the game's own input gate: `!pause && runningFieldMap && !task`, HandleFieldInput) |
| +0x1C | location → Location {mapHeaderID, warpId, x, z, faceDirection} = body + 0x1280 | mapHeaderID = current map: 415 bedroom, 414 1F, 411 Twinleaf, 342 Route 201, 334 Verity Lakefront, 311 Lake Verity, 418 Sandgem, 422 lab, 420 Pokémon Center; x/z follow the player one frame after the MapObject |
| +0x38 | mapObjMan | MapObjectManager {…, +0x124 MapObject array (0x128 each), +0x128 fieldSystem} |
| +0x3C | playerAvatar | PlayerAvatar: +0x08 movementAction, +0x0C speed (1 face, 4 walk, 5 run), +0x14 **moveState** (0 idle, 1 moving, 2 turning), +0x1C avatar state (0 walk), +0x20 gender, +0x30 **MapObject*** |
| +0x68 | runningFieldMap | 1 when the map runs |

Child app = `processManager->child` (ApplicationManager: template {init, main, exit, overlayID} at +0):
`overlayID` 16 = battle (template 0x020EA358, functions in ARM9 0x0203D1B9…), 19 = **pc_boxes** (template
0x020EA2C8 = BoxAppMan_Init 0x021D0D81 / Main 0x021D0DED / Exit 0x021D0E59), 78 = choose_starter, …

### Player MapObject (PlayerAvatar+0x30; localID 0xFF at +0x8)

| offset | field | verified |
|---|---|---|
| +0x28 / +0x2C | facingDir / movingDir: 0 up, 1 down, 2 left, 3 right | walking left → 2 |
| +0x64/+0x68/+0x6C | x / y (height) / z tile coords | x/z switch to the **destination** tile on the first frame of a step; outdoors they are global coordinates of the whole Sinnoh matrix (Twinleaf (116,887), Route 201 (113,863) — seamless across maps); indoors local |
| +0x70 | pos VecFx32 (x, y, z), 16 units/tile, tile centre = tile*16+8 | walking: 2 units every 2nd frame (16 frames/tile); running (B): 4 units every 2nd frame (8 frames/tile, action 88-91 RUN_*); pos.y = 8*height (16 for y=2) |
| +0xA4 / +0xA8 | movementAction / movementStep | FACE_* 0-3 idle, WALK_NORMAL_* 12-15, RUN_* 88-91, WALK_ON_SPOT_FASTER 40-43 when turning |

`walking()` = PlayerAvatar.moveState ≠ 0 or pos not at a tile centre. Verified on real input: moving during a step,
idle (and is_safe) 7 frames after the step ends.

## States and detection (platinum.PlatinumMemory)

| method | rule | verified in |
|---|---|---|
| in_game() / save_loaded() | sFieldSystem valid (see above) | true in all field states incl. battle/PC/menu; false on pt_title, main menu, Rowan intro |
| has_save() | SaveData.dataExists | see SaveData |
| is_safe() | overworld_running (parent ≠ 0, child = 0, !pause, runningFieldMap) && task == NULL && !walking() | true idle in pt_room/town/starter/pc and after closing the start menu / PC; false with start menu, text box, PC UI, battle, while stepping |
| in_battle() | child app overlay 16 | pt_battle (wild) and a trainer battle |
| pc_open() | child app overlay 19 | true from the frame the box UI app is created until it exits (open via PC → SOMEONE'S PC → any of DEPOSIT/WITHDRAW/MOVE; B closes) |
| field_task() | FieldSystem.task ≠ NULL | menus/text/scripts/warps |
| top_overlay() | sApplication.currOverlayID: 77 game_opening, 97 main_menu, -1 field/Rowan intro | |

**Exec hooks:** `HOOK_PC_OPEN = 0x0203D754` (FieldSystem_OpenPokemonStorage, ARM9 — 4 Thumb instructions: ldr r3
=FieldSystem_StartChildProcess (0x0203CD85); mov r2,r1; ldr r1 =template; bx r3) fires exactly once per PC UI open.
Hooks on overlay code are **ambiguous**: overlays share load addresses (overlay 19's BoxAppMan_Init at 0x021D0D80
is also code of the field overlay 5 that runs every 2nd frame; BoxAppMan_Exit 0x021D0E58 fired on close but also
afterwards). There is no good main-ARM9 "close" function → poll `pc_open()` each frame instead.

## Other verified data

* `sMapHeaders` at 0x020E601C (ARM9 .rodata): 593 x 24 bytes; `mapLabelTextID` at +18 equals pret's
  `include/data/map_headers.h` for all 593 maps → `MAP_LOCATION` table + location names (`area_of`, `area_name`).
* Battery (`emu.backup.export_file("x.sav")`): raw 512 KiB flash, two copies of each block (0x00000 / 0x40000); the
  general block footer at +0xCF18 = {saveCounter, blockCounter, size 0xCF2C, signature 0x20060623, id, crc}. The body
  layout in the file = the RAM body (name at +0x68, TID/SID at +0x78). `export_file("x.dsv")` returns False.
  libdesmume itself auto-loads/-writes `$XDG_CONFIG_HOME/desmume/<rom name>.dsv` (raw + 122-byte footer).
* Main menu (main_menu.c): with **no save** it does not wait for input — it jumps straight into the new-game intro
  (`isNewGame` → NEXT_APP_GAME_INTRO). So `fast_continue` returns "no save" as soon as the intro starts, without
  pressing anything (the game then loops intro movie ↔ title on its own).

## Not verified / limitations

* Girl player (gender 1), bike/surf avatar states, heights other than 0/2, underground/union-room map load types.
* Badges/money only via RAM writes shown in the UI (no real badge earned).
* Daycare, bag, Pokédex pages: offsets known from the page table, contents not checked.
* Party records can be mid-update for a frame while the game edits them → `party_checked()` (checksums + two reads).
* During battles the battle engine works on copies of the party (decomp); whether/when the save-RAM party changes
  mid-battle was not checked — don't read/write it while in_battle().

## Friend renderer (client/yellowlink/sinnoh_view.py) — verified 2026-10-26

| what | value | how found / verified |
|---|---|---|
| `sActiveCamera` (camera.c) | **0x021C0770** (ARM9 .bss) → Camera | pointer scan for FieldSystem.camera (+0x24); same pointer in pt_town (new game) and pt_pc / pt_starter |
| Camera | +0x00 perspective {sinFovY, cosFovY, aspect, near, far} fx32, +0x14 lookAt {position, target, up}, +0x38 distance, +0x3C angle u16[4], +0x44 projection u8 (0 persp., 1 ortho), +0x46 fovY | Twinleaf: fov 8.09°, distance 666.92, near/far 150/900 = field_camera.c CAMERA_TYPE_DEFAULT; Pokémon Center: projection 1, distance 1563.54 = CAMERA_TYPE_INTERIOR_ORTHOGRAPHIC; lookAt.target = player MapObject pos |
| billboard anchor | MapObject pos + spriteJumpOffset (+0x7C) + spritePosOffset (+0x88) + spriteTerrainOffset (+0x94) + (0, 0, 6) | ov5_021EDEB4; quad = generic_32x32 model (x −16..16, y 0..32, camera-facing) |
| projection | MTX_LookAt + G3_Perspective(sin/cos of fovY, 4:3) / G3_Ortho(±tan(fovY)·distance), viewport 256x192 | NPCs moved (RAM) to 12 tiles outdoors / 7 indoors: the NPC's own ROM texture drawn at the projected quad matches the screen with best shift ≤ 1 px (dev/e2e/pt_friends.py); perspective needs a +1 px x / +0.5 px y nudge in DeSmuME |
| display lag | the top screen shows the camera of **3 frames earlier** | stationary NPC while the player walks: error 0.67 px mean at lag 3 vs 3.6 at lag 0 |
| `sMapHeaders[id].mapMatrixID` (+2 u16) | 0 = outdoor Sinnoh grid (84 maps); interiors have own matrices, some shared by several maps (e.g. 116 for Pokémon Centers) | read from RAM table |
| mmodel.narc (/data/mmodel/mmodel.narc) | member 90 pl_boy01c, 91 pl_girl01c (32 frames), 92 pl_cyclehero, 93 pl_cycheroine (24 frames); BTX0, 4bpp 32x32, colour 0 transparent | decoded frames match the player sprite pixel-for-pixel indoors; time-of-day lighting = per-channel gain (Twinleaf at night ≈ 0.53/0.52/0.88) |
| shadow | ellipse ~13x6 px ending at the anchor, ~10 % darker | screen diff with/without an NPC |
