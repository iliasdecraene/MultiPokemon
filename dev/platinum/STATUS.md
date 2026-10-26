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
- coordinator: committed+pushed core + pk4 work to origin/platinum (ba888af). Core agent: go ahead with gh workflow run release.yml --ref platinum.
- 2026-10-26 03:20 explorer: dev/states/pt_pc.dsst = Sandgem Town Pokémon Center 1F (map 420), player at (12,4) facing up at the PC (A boots it), idle. Party = 2 x TURTWIG Lv7 (nickname "AAAAAAAAAA" by accident — a full 10-char nickname; OT LUCAS, TID 33356 / SID 2998). **Slot 2 is an exact RAM copy of slot 1** (party count bumped to 2) — same PID, so don't use it to test PID-uniqueness. Pokédex obtained, TM27, money 3500?, no badges. Not saved in-game after the Lake/lab events (the battery save is the pt_starter one).
- 2026-10-26 03:20 explorer: dev/states/pt_pc_open.dsst = same, after A > SOMEONE'S PC > DEPOSIT POKéMON: the box screen is open (Box 1 empty, cursor on party slot 1).
- 2026-10-26 core: Windows build on platinum (run 40257304931) PASSED incl. the new DS smoke test (exe starts pygame +
  DeSmuME, runs a do-nothing test .nds, with dummy audio and with the runner's absent sound card). Job logs can't be
  downloaded from here, so release.yml now also prints the selftest report as annotations. Also new: if the DS core
  can't start on a PC, the app falls back to a Game Boy game with a red message instead of crash-looping.
  COORDINATOR: please commit+push again (release.yml, app.py, nds_core.py) - I'll re-run the build to read where
  DeSmuME puts its .dsv on Windows.
- 2026-10-26 03:30 explorer: dev/states/pt_title.dsst = power-on (reset) with the pt_starter battery save, intro skipped with START x2, "PRESS START" title screen (Giratina). START then shows the main menu with CONTINUE (LUCAS, 0:10, 0 badges) selected by default; A loads the save.
- 2026-10-26 core: Windows build run 40257872216 OK. VERIFIED on Windows: DeSmuME writes its battery file next to the
  exe (`<exe folder>/<rom name>.dsv`, here dist/yltest.dsv) - our app blanks it on close; deleting it after close
  didn't work there (still open?) -> selftest now reports why. Also: DS view scale is now in 0.5 steps (1.5x fits
  1366x768 laptops / 125 % Windows scaling; ds_scale in config, F5/F6 +-0.5). COORDINATOR: please commit+push
  (nds_core.py, release.yml, ui.py, app.py, dev/e2e/platinum_boot.py) so I can read the selftest once more.
- coordinator: pushed 5b5f1b4 for the core agent's selftest.
- 2026-10-26 core: Windows build run 40258223835: DeSmuME's .dsv is <exe folder>/<rom>.dsv and stays locked until the
  process exits (WinError 32) -> the exe now deletes a leftover before the first open; content is always ours/blank.
  Sound: DeSmuME plays through its own SDL legacy device (44.1 kHz, ~133 ms ring buffer); on Windows it shares
  pygame's SDL2.dll. Every ROM open / save import reopens that device, so travel (pygame.quit/init) is fine.
  No sound card -> DeSmuME silently disables its sound core (verified on the runner); fast-forward = DS sound muted
  (like the GB games). When the main loop stalls (window drag, file picker) the ring buffer repeats ~0.1 s: we pause
  DeSmuME's audio around our own blocking dialogs; a window drag on Windows can still buzz briefly.
  Waiting for client/yellowlink/platinum.py: the app picks it up automatically (App.full needs PlatinumMemory,
  fast_continue and PLATINUM_SHA1 in it); dev/e2e/platinum_boot.py uses it instead of its stand-in when present.
- 2026-10-26 03:23 explorer: dev/states/pt_battle.dsst = wild battle (BIDOOF Lv2 vs TURTWIG) on Route 201 right after the intro animation (battle menu shown), save exists. NOTE for core agent: NDSCore has no press()/run() - platinum.fast_continue accepts either ds.DS (press/run) or NDSCore (button_press/button_release/tick). client/yellowlink/platinum.py is in place (first version; verify.py coming).
- 2026-10-26 04:10 explorer: DONE. client/yellowlink/platinum.py (PlatinumMemory + area_of/area_name + fast_continue +
  party_records + status_line), dev/platinum/MEMORY_MAP.md (every address, how found/verified), dev/platinum/verify.py
  (83 checks, all PASS, ~45 s, private XDG_CONFIG_HOME), dev/platinum/gen4crypt.py (tiny decrypt for verify).
  Key facts: heap layout differs new-game vs continue -> follow pointers: 0x02101D40 SaveData*, 0x021C07DC
  sFieldSystem (trusted only while sApplication.currApplication(0x02101D2C)->main == 0x0203CCCD). body=SaveData+0x14;
  trainer body+0x68, party body+0x98, PC boxes body+0xCF2C (currentBox u32, then 18x30x136). pc_open() = field child
  app overlay 19 (poll; ARM9 hook 0x0203D754 fires once on open; overlay addresses are shared -> no close hook).
  in_battle() = child overlay 16. is_safe() = map running, no child app, no FieldTask, not moving.
  Depositing heals PP (box bytes != party bytes[:136]). With NO save the main menu auto-starts NEW GAME, so
  fast_continue returns "no save" at the intro without pressing anything. Correction: the "AAAAAAAAAA" nickname is only
  in pt_pc/pt_pc_open (pt_starter's TURTWIG is un-nicknamed). Text speed is FAST (RAM option write) from pt_starter on.
- 2026-10-26 pc agent: DONE Platinum PC <-> PC database. New: client/yellowlink/platinum_pc.py (Gen4PC: same interface as
  Gen1PC/Gen2PC + remove_party_slot; pure fold()/layout() for the 18x30 <-> 14x20 mapping), client/tests/test_platinum_pc.py,
  dev/e2e/pt_pctest.py (ALL PASS), dev/e2e/pt_trade_e2e.py (Yellow PyBoy + Platinum DeSmuME in one process, ALL PASS).
  Edited: pcdb.py (current_rep() helper = SPEC rule; Gen1PC.record_for converts gen4 via pk4.to_gen1; PCSync._prime gives
  the codec the last view's records), crystal.py (Gen2PC.record_for only: gen4 via pk4.to_gen2), trades.py (pc_dex_name:
  falls back to the Platinum ROM's names, dex 1-493), app.py PC parts (plat_data property, _start_pt_pc/_pt_pc_hook/
  _pt_pc_poll/_pt_fix_party/_pt_pc_report, codec.plat_rom for Yellow/Crystal; one line in step: self._pt_pc_poll(ticks)).
  Facts (verified on the real game):
  * WRITE POINT = exec hook HOOK_PC_OPEN 0x0203D754: after picking DEPOSIT/WITHDRAW/MOVE, before the box app exists.
    BoxAppMan_Init reads PCBoxes from save RAM, so what we write there is what the UI shows (screenshots in the e2e).
    Close = poll pc_open() every host frame ("opening" -> "open" -> closed; 10 s timeout if the UI never comes up).
  * Mapping: store box i -> Platinum box i slots 1-20, everything else emptied. Reading folds holes, slots past the
    20th mon and boxes 15-18 into free store space. Store full -> new mons stay in the game (boxes 15-18) + red toast;
    slots with a bad checksum are never harvested or erased. PCSync's diff is byte-based, so no pcdb diff changes.
  * IMPORTANT for anyone reading the PARTY (party snapshots, publish): a DS frame can end in the middle of game logic,
    and Platinum decrypts party records IN PLACE while working on them -> at the PC menus I saw slot 2 read as garbage
    (bad checksum) on some frames and fine on the next. Always use PlatinumMemory.party_checked() (it retries/None).
    Stand-ins withdrawn into the party are therefore removed once party_checked() is stable (usually next frame).
  * record_for order everywhere: origin's representation, then the asking game's own, then gen1/gen2/gen4.
  COORDINATOR: please commit (no commit attribution lines): client/yellowlink/{platinum_pc,pcdb,crystal,trades,app}.py,
  client/tests/test_platinum_pc.py, dev/e2e/{pt_pctest,pt_trade_e2e}.py, dev/platinum/STATUS.md. SPEC.md section 5 "Sync
  rules" could gain: "Platinum: write on HOOK_PC_OPEN (FieldSystem_OpenPokemonStorage), close polled (box app overlay 19)".
  FOR THE CORE AGENT: dev/e2e/platinum_boot.py now FAILs "in-game save written to the server folder" / "written save =
  the game's battery" - also with my PC code disabled, so not from this change (real adapter instead of the stand-in?).
  The trade window is cramped in the Platinum window (texts overlap the Clear/Confirm row; see
  /tmp/yl_pttrade_cbp4a0yl/2_platinum_trade_window.png). challenge.dex_name / panels roster names could use
  App.pc_dex_name (trades.py) to name Gen-4 species.
- 2026-10-26 04:10 friends agent: DONE friends walking around in Sinnoh. New client/yellowlink/sinnoh_view.py (camera
  0x021C0770 + projection, Lucas/Dawn (+bike) decoded from the player's own ROM, time-of-day tint, shadow, hidden
  behind the local player when standing north of him), client/tests/test_sinnoh_view.py (17 tests),
  dev/e2e/pt_friends.py (records a second player in a subprocess, replays it through a local server: ALL PASS),
  small app.py edits (_publish_ds img, update_friends -> _update_friends_ds, render -> _draw_friends_ds, import).
  Protocol: NO server change - gender/running/bike/height are packed into the existing `img` int (SPEC.md section 2
  note added). MEMORY_MAP.md has a "Friend renderer" section. Hidden in battles/menus/text boxes/PC/warps.
  Note: dev/e2e/platinum_boot.py currently FAILS "in-game save written to the server folder" (+ "written save =
  battery") - also with my friend code patched out, so it comes from elsewhere (PC-sync work in progress?).
  COORDINATOR: please commit client/yellowlink/sinnoh_view.py, client/tests/test_sinnoh_view.py,
  dev/e2e/pt_friends.py, app.py (my hunks), SPEC.md (Platinum state note), dev/platinum/MEMORY_MAP.md.
- 2026-10-26 04:15 friends agent: (after 18b6cf0) MEMORY_MAP.md camera row: also verified after a CONTINUE boot - one-line change left to commit.
- 2026-10-26 core (follow-up): platinum_boot.py "in-game save" failure with the real adapter was the TEST, not the app:
  the first in-game save of a new game ("Saving a lot of data...") writes the 512 KB battery for ~1450 DS frames
  (~24 s) in DeSmuME; the app only writes platinum.sav once two checks 3 s apart see the same battery (never a
  half-written save), so it landed ~780 frames after the test stopped waiting. Test now waits until the file equals
  the game's battery (2400-frame limit) and uses a free port (8791 was taken by another run -> "offline" failures).
  Real players: the save reaches <server folder>/saves/platinum.sav ~3-6 s after the game finishes saving, and on
  exit/travel it's always written (forced). No app change needed.
- 2026-10-26 core: narrow-window layouts (panel width < 600 px = the DS window at 2x/1.5x): Multiplayer (Servers
  button, wrapped notes, compact player info, buttons in two rows), Servers, Regions, Challenges + Soul Link form,
  and a one-column trade/gift window (your PC on top, offers below, buttons at the bottom; long names cut with ...).
  Game Boy window (scale 4): pixel-identical except the Multiplayer button row, which is back to main's 106 px
  buttons (my earlier tweak had shrunk them). New dev/e2e/platinum_layout.py renders every panel for Yellow x4 and
  Platinum x2/x1.5 and can --compare two runs. Tests: unit 305 passed; platinum_boot, pt_pctest, servertest,
  pctrade_e2e ALL PASS.
- 2026-10-26 core (review fixes):
  1. DS start failures: get_emulator() now wraps the desmume import too; NDSCore turns ANY failure (ROM open,
     save import "refused", ...) into DSUnavailable and leaves the shared emulator paused/closed with no _ACTIVE.
     App.__init__ closes its AudioOut (and the core) if starting the emulator fails, and converts any Platinum start
     error into DSUnavailable. _main -> _ds_fallback(): with a Game Boy game -> switch to it (persisted) + red
     message; Platinum-only -> report window ("Send to host") + settings window to pick another game file; closing
     it exits cleanly (no crash loop). Tests: client/tests/test_ds_start.py (8: missing package, DLL load error,
     ROM refused, save refused, hooks, fallback with/without another game, failed App closes sound).
  2. Hook leak: nds_core gives libdesmume ONE module-level ctypes trampoline per address (created once, never via
     py-desmume's ever-growing _registered_cbs) that calls the current handler from _HOOK_FN; close() drops it.
     Test proves the old App is garbage-collected after close and the trampoline is reused. pt_pctest (real PC open
     hook) ALL PASS. Nothing else in the singleton refers to an App (sound is DeSmuME-internal, no save hooks).
  3. Yellow at 3x (480x432) uses the compact panels now: checked with dev/e2e/platinum_layout.py (now also renders
     yellow3_*; fixed the challenge form running off the bottom and the trade window losing the offer list on short
     windows: Invite/Cancel pinned to the bottom, offers fall back to one line each). Yellow 4x screens identical
     before/after (harness is now deterministic: network stopped before the screenshots).
  Tests: unit 313 passed; platinum_boot, servertest, pt_pctest ALL PASS.
- 2026-10-26 pc agent: REVIEW FIXES done (each reproduced by a test first; all 13 new tests fail on 972a728):
  1. first link with a nearly full store: _import keeps what doesn't fit in Platinum (codec.unplaced -> boxes 15-18
     + red toast); Yellow/Crystal are NOT linked (open_session returns None, PCSync.refused = (mons, free), game
     untouched, red toast) - previously their boxes were overwritten.
  2. box-UI edits (MARK / MOVE ITEMS): codecs with edits_in_box (Gen4PC) match a changed record to the vanished entry
     with the same key, closest byte-wise -> same entry id (updated_entry; marks-only on another game's mon keeps its
     data). Stand-in / locked / traded-away: kept as stored, a given item goes back to the bag (Gen4PC.give_item =
     Bag_TryAddItem; bag = body+0x630, pockets from pl_item_data.narc, item->data id = ARM9 table 0x020F0CC4 = id,
     0 for 113-134, id-22 from 135; verified in the game's own bag screen + MOVE ITEMS e2e). The last view now records
     what the game really holds (override / tombstone), so an edited copy or a traded-away mon still in the box isn't
     seen again at the next open. The tombstone part also fixes a Yellow/Crystal clone: a mon traded away while the
     PC was open and left in the box came back as a new entry at the next visit.
  3. Platinum close: "closing" state until party_checked() reads clean and two box reads agree (<= 120 frames).
  4. a slot decrypted in place reads as the mon (platinum_pc.normalise), not as a Bad Egg.
  5. party removal: each locked/traded mon takes at most ONE party slot, the closest with its key (all games);
     Platinum removes by exact bytes. pk4's PID seed left unchanged (stability tests rely on it).
  6. server valid_entry rejects gen4 species 0 / > 493 (+ test).
  Tests: client 325 + server 93 pass; pt_pctest (now with MOVE ITEMS), pt_trade_e2e, pctest, pctrade_e2e, crossgame
  ALL PASS. Files: pcdb.py, platinum_pc.py, app.py (PC parts only), pcstore.py, tests/test_platinum_pc.py,
  server/tests/test_pcstore_gen4.py, dev/e2e/pt_pctest.py. SPEC.md section 5 could note: a game whose boxes don't
  fit is not linked (nothing written); Platinum box edits keep the entry id; stand-in items return to the bag.
- 2026-10-26 pc agent: SECOND REVIEW FIXES done (11 new client tests fail on 61b0664 and pass now; 335 client + 93 server
  pass; pt_pctest (+ MOVE ITEMS > BAG on a locked mon), pt_trade_e2e, pctest, pctrade_e2e, crossgame ALL PASS).
  1. In-place decrypt confirmed in pret (BoxPokemon_EnterDecryptionContext = DecodeData XOR only, blocks stay
     shuffled; ExitDecryptionContext clears the flags BEFORE re-encrypting). platinum_pc.normalise now XORs back in
     place (flag set, or flags clear + plain word sum = mid re-encryption) and keeps it only if pk4.valid, else
     "bad". Checked on frames captured from the real game: both states give exactly the game's next write.
     pk4._plain_of / mon_key / is_standin also read in-place records correctly (pk4.in_place_decrypted /
     reencrypt_in_place). Test uses XOR-only records for all 24 block orders.
  2. First link: Platinum refused (like Yellow/Crystal, nothing written) when the leftovers exceed the 260 spare
     slots; layout()/write_boxes raise before writing; app catches it and PCSync.abort_open() undoes the open.
  3. merge_remote: an entry changed only here (same id) is kept over an unchanged server copy (three-way per entry).
  4. Kept mons (stand-in / locked / traded away): items the box UI moved are put right both ways - give back what
     the mon took from the bag, take out of the bag what the box UI took off the stored copy (Bag_TryRemoveItem +
     Pocket_SortEmpty, same loop as pret); swaps do both.
  5. _pt_fix_party: item changes only for party mons really removed (last party mon: retried until the deadline,
     no items); a pending fix is merged, not replaced.
  6. Party removal (all games) only for the very same mon: exact bytes, or for Platinum the same bytes apart from
     marks/held item (Gen4PC.same_mon). Box-edit matching uses same_mon too (no key/distance guessing any more).
  + _pt_pc_poll "closing" guards box_slot_addr() == None (save gone -> PC session rolled back after 2 s).
  Files: pk4.py, platinum_pc.py, pcdb.py, app.py (PC parts), tests/test_platinum_pc.py, dev/e2e/pt_pctest.py.
