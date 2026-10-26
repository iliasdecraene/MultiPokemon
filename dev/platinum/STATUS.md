# Platinum work status (append short notes; newest last)

- coordinator: branch platinum created; dev/platinum/ds.py + BRIEF.md written; ROM at dev/states/platinum.nds.
- coordinator: branch platinum pushed to origin (gh workflow run release.yml --ref platinum is possible once you have changes; tell me in STATUS.md and I'll commit+push them).
- 2026-10-26 02:42 explorer: dev/states/pt_room.dsst = new game LUCAS (boy) / rival BARRY, controllable in bedroom right after the intro TV scene, idle. Note: the first step triggers Barry's scripted visit.
- 2026-10-26 02:46 explorer: dev/states/pt_town.dsst = outside in Twinleaf Town (map 411, x=116 z=887), one step south of the player's house door, idle, no Pokémon yet.
- 2026-10-26 02:57 explorer: dev/states/pt_starter.dsst = Twinleaf Town outside the player's house (map 411, x=116 z=887), idle, TURTWIG Lv6 in party (rival BARRY has Chimchar), running shoes, game SAVED in-game just before (play time 0:10).
- 2026-10-26 02:57 explorer: battery formats: `emu.backup.export_file("x.sav")` writes the raw 512 KiB flash image (OK, valid Gen-4 save: newest general block at 0x40000 this time); `export_file("x.dsv")` returns False (not supported). **libdesmume auto-loads/auto-writes the battery to `~/.config/desmume/<rom basename>.dsv`** (= raw 512 KiB + 122-byte DeSmuME footer "|<--Snip above here...|-DESMUME SAVE-|") on every in-game save — every process opening a ROM called platinum.nds shares that file! Core agent: per-server saves must account for this (import_file after open, or point the config dir elsewhere, e.g. XDG_CONFIG_HOME). dev/states/pt_saved.dsv = copy of that auto .dsv; dev/states/pt_saved.sav = raw export (same bytes minus footer).
- 2026-10-26 core: client/yellowlink/nds_core.py `NDSCore` ready (memory bus read/r8/r16/r32/write/w8/w16/w32 as in BRIEF,
  tick, button_press/release("a".."r"), touch, save_state/load_state (file obj or path), export_save, hook_register(addr, fn)).
  ONE DeSmuME per process and it can NOT be re-created after desmume_free (segfault) -> nds_core keeps a process-wide
  singleton; NDSCore re-opens the ROM in it. app.py runs Platinum when the library game is "platinum" (library.KNOWN_GAMES,
  region "sinnoh"); it uses `platinum.PlatinumMemory(core)` + `platinum.fast_continue(core, on_progress)` +
  `platinum.PLATINUM_SHA1` as soon as client/yellowlink/platinum.py exists (App.full needs all three); until then a stand-in.
  Facts: libdesmume's own battery file is <rom name>.dsv in $XDG_CONFIG_HOME/desmume (Linux) / next to the exe (Windows):
  shared between servers -> app always imports the per-server save (or a blank 512 KB) right after open and blanks it on close.
  e2e/dev scripts should set XDG_CONFIG_HOME to a temp dir, or they share ~/.config/desmume/platinum.dsv with ds.py runs.
  Soul Link: challenge.py already only offers yellow/crystal (shared_games) and the server's challenges.GAMES rejects
  platinum -> nothing to change there for now.
- 2026-10-26 pk4 agent: DONE client/yellowlink/pk4.py (PK4 codec + Gen1/2<->Gen4 conversion, all choices documented
  at the top of the file), client/yellowlink/ndsrom.py (NitroFS/NARC/LZ77/message banks; `ndsrom.platinum(path)` =
  cached PlatinumData, ~60 ms: personal data, exp tables, move PP, evolutions, species/item/move/location names),
  server pcstore.py (gen4 entries + Gen-4 trade evolutions), tests test_pk4.py / test_ndsrom.py (ROM tests skip
  without dev/states/platinum.nds) / server test_pcstore_gen4.py. All client + server tests pass.
  Verified on the real game: the party Turtwig/Chimchar in pt_starter decrypt -> encrypt byte-identically and
  pk4.to_party(box) reproduces the game's 236-byte party record exactly (stats, mail defaults). Converted Yellow/Crystal
  mons + a ?NAME stand-in written into the party show correctly in the summary ("Johto, Jan. 1 2000, Oran Berry").
  API for the PC codec: pk4.record_for(entry, rom[, g1rom]) -> 136 bytes for Platinum (gen4, else from_gen2/from_gen1,
  else standin); pk4.entry_from_pk4(bytes, rom); pk4.mon_key(bytes) (PID+TID+SID, enc/plain/box/party);
  pk4.is_standin; pk4.is_empty / pk4.EMPTY_BOX (empty slot = zeros encrypted with seed 0); pk4.to_party(box, rom).
  ADDRESS HINTS for the explorer (seen in pt_starter AND pt_town, verify on a fresh boot / via pointers):
  party = 0x0227E278 (u32 capacity 6, u32 count, 6 x 236); PC box mons = 0x0228B110 (18 boxes x 30 x 136, 540
  EMPTY_BOX slots found contiguous; the u32 before it is 0 = current box?).
- 2026-10-26 pk4 agent PROPOSED RULE (implemented in server pcstore.py): an entry's *current* representation is the
  one named by `origin` (yellow->gen1, crystal->gen2, platinum->gen4; entries are re-created with origin = the game
  they were last harvested from), falling back to the first present of gen1, gen2, gen4. A trade evolution is applied
  to the current representation only; if it evolves, the other representations are set to null (stale), so every
  game converts from the evolved bytes. Clients should treat entries the same way (own-gen bytes first is fine as
  long as nobody stores several representations; if someone does, prefer `origin`'s).
- 2026-10-26 pk4 agent REQUESTS (files I don't own):
  * SPEC.md section 5: Entry gains `"gen4": "<b64 136-byte encrypted Platinum box mon>" | null`, origin "platinum",
    and the current-representation rule above (coordinator).
  * pcdb.Gen1PC.record_for: before the stand-in, try `pk4.gen1_record(entry, self.rom, plat_rom_or_None)` (works
    without the Platinum ROM). crystal.Gen2PC.record_for: try `pk4.gen2_record(entry, plat_rom_or_None, self.rom)`.
    Otherwise Platinum-only entries always show as ?NAME in Yellow/Crystal even when they could be converted.
  * Platinum codec (core/explorer): entry dex for Gen-1 conversion = entry["dex"] (national), so record_for needs
    no Yellow ROM. rom = ndsrom.platinum(path of the player's Platinum ROM).
- 2026-10-26 core: Platinum runs in the app (dev/e2e/platinum_boot.py: ALL PASS with a stand-in adapter - boot, both
  screens, keys incl. X/Y/L/R (D/C/Q/W), mouse = touch + drag, in-game save -> <server folder>/saves/platinum.sav only,
  resume across restart, travel Yellow<->Platinum, friend sees "Sinnoh", second server gets a blank save).
  COORDINATOR: please commit+push my files (list: client/yellowlink/{nds_core,app,library,ui,panels,setup_dialog,audio}.py,
  client/yellowlink.spec, client/requirements.txt, .github/workflows/release.yml, client/tests/test_nds_core.py,
  dev/e2e/platinum_boot.py) so I can run `gh workflow run release.yml --ref platinum` (Windows exe + DS smoke test).
  audio.py: one-line fix - AudioOut crashed (IndexError) when the PC has no sound device at all; now silent.
- coordinator: SPEC.md section 5 updated with gen4 + current-representation rule (pk4 agent's request). Gen1PC/Gen2PC record_for changes go to the PC-integration agent (next).
