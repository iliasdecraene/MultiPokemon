# Badges — status / coordination (append dated notes)

## 2026-10-26 badges-app (start)
What the app side will call in `client/yellowlink/badges.py` (badges-game owns the file; tell me here if names differ):
- `REGIONS` ordered: `["kanto", "johto", "sinnoh"]`; `REGION_OF_GAME = {"yellow": "kanto", "crystal": "johto",
  "platinum": "sinnoh"}` (also used for Soul Link: run game -> region).
- `SHINY_TIERS = (1, 10, 25, 50, 100)`; `shiny_tier(count) -> int` (0 = none, else the highest tier reached).
- `merge(earned: dict, region: str, progress: dict, now: float) -> list[str]` mutates `earned` in place (never
  removes) and returns what was newly earned, e.g. `["champion", "dex", "shiny:10"]` (shiny tier reached).
- `gen1_is_shiny(dvs: bytes | int) -> bool` (2 DV bytes, Gen-2 rule) for the PC/trade marks; and if possible
  `entry_is_shiny(entry: dict) -> bool` for a PC-store Entry (gen1/gen2/gen4 bytes), else I'll build it from
  gen1_is_shiny + crystal/pk4 helpers.
- Shiny keys: `str`, <= 40 chars, `[A-Za-z0-9_:-]` (the server validates with that; tell me if your keys differ).
Until badges.py exists I use a local stub with these names in the tests.
- App calls the adapter as `self.G.progress(self.game)` and `game.wild_is_shiny(self.game)` where `self.game` is the
  GameMemory / CrystalMemory / PlatinumMemory instance (not raw pyboy memory). Called only at safe moments
  (`is_safe()`, main game, not in a Soul Link, not mid trade/battle), every ~10 s; wild_is_shiny every ~15 frames
  while `in_battle()` in Yellow. Shout here if you'd rather take raw memory.
- Soul Link badge: the existing ledger event `hof` (referee sends it via `view.hof()`) is what the server uses;
  the server awards gold (`rules.exp_share` off/missing) or silver (on) for REGION_OF_GAME[run game] and also
  backfills from old completed runs' ledgers at startup. If you add a new referee-view helper, it just has to keep
  `view.hof()` truthy once the run's player entered the Hall of Fame.

## 2026-10-26 badges-app (progress)
- Using badges.py as written: `merge` (book merge), `normalise`, `entry_is_shiny` (PC/trade marks),
  `entry_shiny_key(entry, region, progress["owner"])` (own shinies sitting in the YellowLink PC store are added to
  progress["shinies"] before merging), `shiny_tier`/`next_shiny_tier`, REGIONS/REGION_NAMES/REGION_OF_GAME/DEX_TOTAL.
- The book (client/yellowlink/badgesync.py BadgeBook, file `<data_dir>/badges/<uid>.json`) stores `earned` WITHOUT
  `soullink`; Soul Link medals are server-side (from the `hof` ledger event), cached per server for offline display.
- Referee: `_light_check` now sends `hof` on `view.hof() or view.run_champion()` (your helper).
- Server: `server/yellowlink_server/badges.py` (validation, store, backfill). Keys regex there = badges.KEY_RE.

## 2026-10-26 badges-app (done)
- Tests: client 498 pass (incl. client/tests/test_badgesync.py, 23), server 131 pass (incl. tests/test_badges.py),
  pyflakes clean; e2e badges_flow.py ALL PASS (28 checks), challenge_flow / servertest / sharetest ALL PASS.
- UI: badge_art.py (medals supersampled, hover tip, "Shiny!" tag under the wild enemy at GB (124, 57)),
  panels.py (Regions: sockets right of each region name; Multiplayer: selected player's sockets per region;
  trade/PC rows: sparkle after a shiny's name via badges.entry_is_shiny).

## 2026-10-26 badges-game (done)
Files: client/yellowlink/badges.py (rules), game.py / crystal.py / platinum.py (`champion`, `owner`, `progress`;
Yellow `wild_is_shiny`; Platinum `PAGE_POKEDEX`, `pokedex_caught_bits`), challenge_view.py (`run_champion()`),
client/tests/test_badges.py (21 tests), dev/badges/verify.py (real ROMs, 84 checks ALL PASS: `python dev/badges/verify.py
[yellow] [crystal] [platinum]`, never writes dev/states), SPEC.md "Badges (0.7.5)" subsections in sections 3 and 6.
Interface exactly as in the BRIEF + app notes above; extras: progress()["owner"] = {"ot", "tid"[, "sid"]},
`entry_shiny_key`, `award_soullink` (gold beats silver), `merge_earned`, `summary`, `SHINY_KEEP` = 1000 (= server cap).
- Champion: Yellow wNumHoFTeams D5A1 > 0; Crystal entered_hall_of_fame() (HoF count / wStatusFlags bit 6 /
  EVENT_BEAT_ELITE_FOUR); Platinum entered_hall_of_fame() (FLAG_GAME_COMPLETED / isMainStoryCleared / HoF record);
  each + "standing in the HoF room". Verified with RAM edits both ways AND the games' own HoF scenes.
- Dex: Yellow D2F6 (checked against the game's own Pokédex OWN screen), Crystal 1:DE99, Platinum body+0x1328
  (save page 7, magic BEEFCAFE, caught bits at +4); Sinnoh list = the ROM's own (zukan_data.narc member 12) = pret.
- Own catch: Yellow OT name + ID; Crystal OT name + ID + caught data != 0 (every Crystal catch/gift/hatch has it,
  converted Gen-1 mons don't); Platinum origin 12 + OT name + TID + SID. Eggs never count until hatched.
  Verified with real catches: Yellow (shiny DVs written in the wild mon, it used Transform, MASTER BALL -> a shiny
  DITTO in the party), Crystal (shiny DVs, MASTER BALL, caught data 8202), Platinum (wild PID made shiny, MASTER BALL).
- wild_is_shiny: Yellow wEnemyMonDVs CFF0, or wTransformedEnemyMonOriginalDVs CCEB when wEnemyBattleStatus3 D068
  bit 3 (the wild mon used Transform); only kind "wild" (normal + Safari) else None. Stable from frame 0 (6 encounters).
OPEN for the coordinator / Ilias:
1. **Sinnoh "caught all 210" includes MANAPHY (#151 of Platinum's Sinnoh dex)**, which can't be caught in Platinum
   (Pokémon Ranger egg only); Platinum's own Sinnoh diploma only needs it SEEN. As written the Sinnoh Pokédex badge
   is unobtainable. One-line fix if wanted: `badges.SINNOH_DEX_EXCLUDED = (MANAPHY,)` -> goal 209 (DEX_TOTAL follows;
   the server/UI should read DEX_TOTAL; verify.py's dex check expects 210 now).
2. **YellowLink PC trades never set the in-game owned/caught flags.** A real trade sets them; ours lands in the
   PC store and appears in the game's boxes, so version exclusives / traded-only species never get their dex bit and
   the Kanto/Johto/Sinnoh Pokédex badge is in practice impossible. Suggest: when a mon from another player/game shows
   up in a game's PC view (or is withdrawn), set seen+owned for its species like the games' own trade code
   (game.GameMemory.set_dex / crystal set_dex exist; Platinum needs Pokedex caught+seen bits, maybe gender/forms).
   App/pcdb territory - not done here.
3. Key collisions (by design, PC-store keys): Yellow shiny key = OT ID + DVs + catch-rate byte, and shiny DVs leave
   only 8 Atk values -> two own shinies of the same species with the same Atk DV count once (1 in 8 per pair).
   Crystal adds caught data (level/time/place) so collisions need same level+place+time of day+Atk DV. Platinum keys
   are unique (PID). Fine for tiers in practice; documented.
4. Yellow has no origin field: a mon converted from Crystal/Platinum into Yellow counts for Kanto if its OT name +
   ID equal the Yellow save's (only with identical trainer IDs).

## 2026-10-26 badges-game (coordinator decisions 1 + 2 done)
1. Manaphy excluded: `badges.SINNOH_DEX_EXCLUDED = (MANAPHY,)` -> DEX_TOTAL sinnoh 209 (SINNOH_DEX itself stays the
   game's 210 list). how_to text: "Catch all 209 Pokémon of the Sinnoh dex (all but Manaphy) in Platinum."
   SPEC section 9 + badges subsection, tests and verify.py updated.
2. Pokédex marking for mons arriving through YellowLink: `codec.mark_dex(boxes)` on Gen1PC / Gen2PC / Gen4PC, called
   by `App._pc_mark_dex` right after `write_boxes` in `on_pc_open` (the only place YellowLink puts mons into a game;
   trades, gifts and the player's other games all arrive there; pc_codec is None in a Soul Link run). Stand-ins and
   eggs are skipped. Yellow: owned + seen (pret AddPartyMon). Crystal: SetSeenAndCaughtMon + Unown letter in
   wUnownDex 1:DED9 / wFirstUnownSeen 1:DEF4 (as AddTempmonToParty). Platinum: `platinum.pokedex_capture`, a port of
   pret Pokedex_Capture (what SaveData_UpdateCatchRecords does for a received trade: gender seen, Spinda, all
   form tables, language, caught + seen), written back in one go.
   Verified (dev/badges/verify.py, now 3 extra sections): Yellow real App + Bill's PC: a Crystal GEODUDE arrives,
   the game's own Pokédex screen OWN 4 -> 5, the ?SENTRET stand-in isn't marked; Crystal: mark_dex == the game's own
   caught/seen bytes after a real catch, Unown letter + egg skip; Platinum: Pokedex layout on the ROM, and
   pokedex_capture() on the pre-catch block == the game's own 804-byte block after a real catch; a converted Crystal
   UNOWN in box 1 -> caught + form. e2e: crossgame.py (Yellow RATTATA -> Crystal dex 3 -> 4, no DITTO in Yellow for
   ?SENTRET) and pt_pctest.py (converted PIDGEY -> caught + seen in Platinum, ?EGG/DITTO not) got new checks.

## 2026-10-26 badges-app (review fixes in progress)
Working on review items 1-7 (server clamping, legacy run tier, book load, check timing, layout, Shiny! tag spot,
announcement/toast wording). Not touching badges.py / adapters / challenge_view.py.

## 2026-10-26 badges-game (review fixes, in progress) - NOTE for badges-app
- Shiny keys are now BADGE keys (not the PC mon_key): Yellow `badges.gen1_badge_key` = OT ID + DVs + evolution-family
  root (no catch-rate byte), Crystal `gen2_badge_key` = OT ID + DVs + raw caught data + family root, Platinum
  unchanged (PID+TID+SID). `badges.gen1_shiny_key(data, ot, owner, dex)` takes the national dex now (entries: the
  entry's "dex"). Identical keys within ONE reading are numbered by `badges.number_keys` (key, key-2, ...); the
  adapters' progress() already does it. I changed ONE line in badgesync.py `_pc_shiny_keys`: it now returns
  `rules.number_keys(keys)` (store reading numbered on its own, so game + store merge = max per source, not the sum).
  Also new: `badges.store_shiny_keys(entries, region, owner)` (same thing for a list of entries).

## 2026-10-26 badges-app (review fixes done)
1. Server never rejects a report for a bad clock (times clamped to server now), unknown regions / bad shiny keys
   dropped quietly; client sends `true` for clock-garbage times and stores nothing before 2001.
2. Legacy runs (no `exp_share` rule): gold if the hof is before 1793025570 (0.7.3 release), else silver
   (run_tier + startup backfill).
3. BadgeBook.load: any wrong JSON/type -> .bad copy + fresh book.
4. Progress check on the first safe frame once due; `badges_due()` after the main-game PC closes (on_pc_close).
5. Regions: sockets get their own line when they'd reach Travel (GB 2x); Multiplayer badge lines clamped above the
   footer; tooltip drawn last. 6. "Shiny!" tag at GB (48, 30) under the enemy HUD, max ~8.5 GB px tall.
7. Referee HoF text neutral again (toast comes from the roster); friends' badges on an old server: "Badges need a
   server update"; first report / old earn times -> one "X has N badges" line.
Tests: client 545, server 127, pyflakes clean; badges_flow (34 checks), challenge_flow, servertest, sharetest ALL PASS.
