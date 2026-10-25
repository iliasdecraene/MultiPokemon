# Soul Link memory helpers — Pokémon Yellow (findings to merge into SPEC.md)

ROM: English Yellow, SHA1 `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1`. Symbols from pret/pokeyellow `pokeyellow.sym`
(symbols branch) and the matching sources. Every address below was exercised on the real ROM in PyBoy by
`dev/e2e/soullink_mem_yellow.py` (ends with `ALL PASS`); unit tests in `client/tests/test_soullink_yellow.py`.

## New addresses (WRAM bank 0, `pyboy.memory[addr]`)

| name | addr | notes |
|---|---|---|
| wPlayerMonNumber | CC2F | party index of the player's mon in battle; zeroed by InitBattleVariables |
| wEnemyMonSpecies2 | CFD7 | wild/Old Man/Oak species, written **before** wIsInBattle (TryDoWildEncounter / InitOpponent); zeroed for trainers |
| wEnemyMonNick | CFD9 | "GHOST" for an unidentified ghost |
| wEnemyMonPartyPos | CFE7 | trainer mon index in battle; 0xFF until the first send-out |
| wEnemyMonCatchRate / wEnemyMonDVs | CFEB / CFF0 | already final on battle frame 0 (= caught mon's bytes 7 / 27-28) |
| wBattleMonSpecies | D013 | 0 from InitBattleVariables until the player's first send-out |
| wBattleMonHP | D014 | 2 bytes BE |
| wBattleType | D059 | 0 normal, 1 Old Man, 2 Safari, 3 RUN (never set in Yellow), 4 Oak's Pikachu; reset by EndOfBattle |
| wNumberOfNoRandomBattleStepsLeft | D13B | (test setup only) |
| wNumBagItems / wBagItems | D31C / D31D | (id, qty) pairs, FF-terminated, max 20 |
| wNumberOfWarps / wWarpEntries | D3AD / D3AE | 4 bytes: y, x, dest warp id, dest map (test setup only, see "warp trick") |
| wPikachuOverworldStateFlags | D42F | |
| wPikachuHappiness / wPikachuMood | D46F / D470 | global, not stored in the mon |
| wNumHoFTeams | D5A1 | Hall of Fame entries; saturates at 255. SRAM copy: bank 1 **A84E** (sMainData A5A3 + D5A1 − D2F6) |
| wViridianCityCurScript | D5F3 | (test setup only) |
| wSafariSteps | D70C | 2 bytes BE (test setup only) |
| wNumSafariBalls | DA46 | Safari Balls live here, **not** in the bag |
| wDayCareInUse | DA47 | 0/1 |
| wDayCareMonName / wDayCareMonOT / wDayCareMon | DA48 / DA53 / DA5E | 11 / 11 / 33-byte box struct |
| wBoxCount / wBoxSpecies / wBoxMons / wBoxMonOT / wBoxMonNicks | DA7F / DA80 / DA95 / DD29 / DE05 | current box (WRAM copy) |
| wSpritePikachuStateData1ImageIndex | C1F2 | 0xFF = following Pikachu not drawn |
| ROM base stats byte 19 | 0E:43DE + 28·(dex−1) + 19 | growth rate (GROWTH_*) |

Constants: SILPH_SCOPE 0x48, balls MASTER 1 / ULTRA 2 / GREAT 3 / POKE 4 / SAFARI 8, STARTER_PIKACHU = PIKACHU 0x54,
RESTLESS_SOUL = MAROWAK 0x91, POKEMON_TOWER_1F..7F 0x8E..0x94, SAFARI_ZONE_EAST..CENTER 0xD9..0xDC, HALL_OF_FAME 0x76.

## battle_info()

* `wIsInBattle` 1 = wild, 2 = trainer; 0 and 0xFF (LOST_BATTLE, used for overworld poison whiteout) → `None`.
* Wild `dex` from wEnemyMonSpecies2. Trainer `dex` = wEnemyPartySpecies[wEnemyMonPartyPos], or the first enemy mon
  before the first send-out.
* `special` (wild only) = wBattleType in {1, 3, 4} **or** pret `IsGhostBattle` (map in Pokémon Tower 1F-7F and no
  Silph Scope in the bag) **or** (map POKEMON_TOWER_6F and species MAROWAK). The Marowak is uncatchable even with
  the scope (ItemUseBall checks RESTLESS_SOUL on 6F) and the game still draws it as GHOST until the scope animation.
* `safari` = wild and wBattleType == 2. InitBattleVariables sets it from the map *before* wIsInBattle.
* **Reliable from frame 0** (the first frame wIsInBattle ≠ 0), for every kind. Verified identical on every frame from
  0 to the battle menu: Route 1 wild (Pidgey), Tower 3F ghost (special) and with scope (wild), Marowak with/without
  scope (special), Old Man demo (special, via the game's own SCRIPT_VIRIDIANCITY_OLD_MAN_START_CATCH_TRAINING),
  Oak's Pikachu at the start of the game (special, wBattleType 4, played from the bedroom state), Safari Zone
  (wild + safari), trainer (Prof. Oak class via the SPEC ghost recipe).
* The caught mon's catch-rate byte and DVs equal wEnemyMonCatchRate/wEnemyMonDVs already on frame 0, so the final
  `mon_key` of a wild mon is known at the encounter (party OT ID = wPlayerID).
* Not a battle: the Old Man and Oak's Pikachu battles don't send out a player mon.

## battle_party_slot() and faints

* Returns `(wPlayerMonNumber, wBattleMonHP)`; `None` outside battles, when wBattleType ≠ 0 (Old Man, Safari, Oak's
  Pikachu: no player mon out) and while wBattleMonSpecies == 0 (before the first send-out — wBattleMonHP still holds
  the previous battle's value then, possibly 0; guarding on the species avoids a false faint).
* Wild battle: `None` until frame ~454 (send-out animation), battle menu at ~597.
* Faint (real: Pikachu at 1 HP, Growl until Rattata's Tackle): wBattleMonHP becomes 0 on the damage frame and
  `battle_party_slot()` reports `(0, 0)` on that same frame. **The party struct HP gets 0 written** 29 frames later by
  RemoveFaintedPlayerMon → ReadPlayerMonCurHPAndStatus, before the "fainted!" text.
* Battle mon → party copy (ReadPlayerMonCurHPAndStatus: HP + status) happens at the start of every turn
  (MainInBattleLoop), on a faint (both sides), and when the mon is switched out. EndOfBattle doesn't copy again, so the
  party HP can lag wBattleMonHP by up to one turn during a battle.
* Sending out the next mon: wPlayerMonNumber and wBattleMon change together (same routine, no frame delay between
  them in ChooseNextMon/SwitchPlayerMon); observed `(1, 16)` from the first frame after choosing. Caveat: the frame
  boundary can in theory fall between the two writes (a few dozen cycles of 70224), so a rules engine should count a
  faint only when `hp == 0` holds on two consecutive frames, or confirm with the party HP 0 ~30 frames later.

## has_poke_balls()

Bag (wNumBagItems/wBagItems) holds any of ids 1/2/3/4/8 with qty > 0. Safari Balls are never in the bag in Gen 1
(they're the wNumSafariBalls counter), so inside SAFARI_ZONE_EAST..CENTER a non-zero counter also counts.

## Hall of Fame

* `HALL_OF_FAME_MAP = 0x76`, `in_hall_of_fame()` = in game and wCurMap == 0x76.
* Persistent indicator: `wNumHoFTeams` (D5A1) → `hall_of_fame_count()`. Verified with the game's own HoF script:
  it goes 0→1 ~1613 frames after arriving (AnimateHallOfFame, before the team display), the game then shows the credits
  and only after that saves (SRAM 1:A84E = 1 after ~8900 frames) and resets. Reloading that save gives 1.
  If the player resets during the credits, the increment is lost (it was never saved).
* HallOfFameResetEventsAndSaveScript also sets an "unused beat Elite 4" bit in wElite4Flags (D733); not verified,
  use wNumHoFTeams.

## Day Care (daycare_records)

`[wDayCareMon(33) + wDayCareMonOT + wDayCareMonName]` when wDayCareInUse ≠ 0. Real deposit at the Route 5 Day Care:
the game's MoveMon (PARTY_TO_DAYCARE) copies the box struct and writes the party level into byte 3, like a PC
deposit, so the record is identical to the box format and `mon_key` matches the party copy; withdrawing (¥100) gives
the same key back in the party.

## Box moves

* `move_party_to_box(slot)` = Bill's PC deposit: MoveMon PARTY_TO_BOX (first 33 bytes, **byte 3 = party level**,
  OT, nick appended) + RemovePokemon (shift species list/structs/OTs/nicks). If the mon is the starter Pikachu
  (IsThisPartyMonStarterPikachu: species PIKACHU, OT ID = wPlayerID, first 5 OT chars = player name) it also applies
  ModifyPikachuHappiness(PIKAHAPPY_DEPOSITED): happiness −3/−3/−5 (by <100/<200/≥200) and mood capped at 0x62.
  Refuses: in battle, invalid slot, last party mon, box full (20). It does **not** reproduce the PC's refusal of a
  "sleeping" (not-following) Pikachu.
* `move_box_to_party(i)` = Bill's PC withdraw: MoveMon BOX_TO_PARTY: copy the 33 bytes, level =
  CalcLevelFromExperience (growth rate from base stats byte 19, the 24-bit CalcExperience incl. its wrap-around),
  stats = CalcStats with stat exp; current HP and status are kept; byte 3 stays as stored. Refuses: in battle, party
  full, bad index, unknown species.
* Verified on the real ROM (Viridian Pokémon Center): forced deposit → party menu shows the right names/levels/HP →
  Bill's PC lists it → real withdraw gives the original party struct byte-for-byte → real deposit gives a record
  identical to ours → forced withdraw gives the original struct → START>SAVE → reload: party and box identical.
* The methods don't check `is_safe()`; call them only when it is True (they write 3-4 regions over one call, and the
  game must not be mid-menu). Note for the app: a forced move outside a Bill's PC session looks to `pcdb.PCSync` like
  a deposit/withdraw that happened in the game (next `open_session` with trust_game picks it up).
* Oak's Pikachu has byte 3 = 0 in the party; after a deposit (real or forced) it is 5 and stays 5 after withdraw —
  same as the real PC.

### Following Pikachu

Compared with the real Bill's PC on the same state (happiness 89, mood 128):
* deposit → happiness 86, mood 98 (0x62) — forced move identical; box record identical to the real one.
* The following sprite disappears on the very next frame (SpawnPikachu_ runs every frame and
  IsStarterPikachuAliveInOurParty now fails) and stays away while walking.
* While boxed, Bill's PC still recognises it (IsThisBoxMonStarterPikachu): RELEASE is refused.
* withdraw → like the real PC, the sprite stays hidden until the player's first step, then Pikachu spawns next to
  the player and follows; happiness is not changed by a withdraw (walking can raise mood/happiness as usual).
* Party menu, START>SAVE and reload are fine; Pikachu follows after the reload.

## mon_key

`f"{OTID:04x}{DVs:04x}{catch_rate:02x}"` from bytes 12-13, 27-28 and 7 (same offsets in the 44-byte party struct,
33-byte box struct, 55-byte records and the Day Care record).
* Byte 7 (catch rate) is set at capture from the species and is not touched by evolution (Evolution copies the
  loaded struct back with only species/stats/types/moves changed), deposit/withdraw, Day Care, trades in YellowLink or
  renaming. Safari bait/rocks change wEnemyMonActualCatchRate (D006), not this byte.
* Collision risk: all the player's own mons share the OT ID, so two of them collide only if they have the same DVs
  (16 random bits) **and** were caught as species with the same catch rate. With n such mons,
  P(any collision) ≈ 1 − exp(−n²/131072): 30 → 0.7 %, 50 → 1.9 %, 100 → 7 %. The rules engine should handle a
  duplicate key (e.g. suffix a counter when a *new* encounter produces an existing key).
* Caveat: a mon that goes through Crystal and back (pcdb `gen2_to_gen1`) gets byte 7 = catch rate of its *current*
  species, so its key changes if it evolved since capture. Use `mon_key(x)[:8]` (OT ID + DVs) if keys must survive
  that trip.

## Areas

`AREAS` (map id → key, all 249 ids 0x00-0xF8) generated from pret `constants/map_constants.asm`: cities/routes are
their own area; indoor maps take their `end_indoor_group` town/route (numbered duplicates like `CERULEAN_CITY_2`
merged); dungeons share one key per dungeon (Mt. Moon, Rock Tunnel, Seafoam Islands, Pokémon Tower, Victory Road,
Cerulean Cave, Silph Co. incl. 1F and elevator, Pokémon Mansion, Power Plant, Viridian Forest, Diglett's Cave,
Rocket Hideout, S.S. Anne, Safari Zone incl. rest houses). Overrides: Rock Tunnel Pokécenter → Route 10, Mt. Moon
Pokécenter → Route 4, Safari Zone Gate → Fuchsia City, Bill's house → Route 25, Vermilion Dock → Vermilion City,
Diglett's Cave entrance huts → Route 2 / Route 11, Elite Four rooms + Champion + Hall of Fame → Indigo Plateau,
Summer Beach House → Route 19, the two Underground Path tunnels and Trade Center/Colosseum are their own areas,
unused ids are `UNUSED_MAP_xx`. Unknown ids → `MAP_xx`.

## Warp trick (test setup)

To reach a map without playing there, the e2e script rewrites the destination of Oak's Lab door in the loaded
`wWarpEntries` (Pallet, entry at y=11 x=12) and walks through it: the game's own warp code loads the target map, its
scripts, wild data and tileset, and places the player on the target's warp `id`. Used for Pokémon Tower 3F/6F,
Viridian City, Safari Zone Center, the Day Care and the Hall of Fame.

## Fixtures written to dev/states/

`soullink_route1_grass.state` (Route 1 grass, Poké + Master Balls), `soullink_wild_battle.state` (frame 0 of a wild
battle), `soullink_faint.state` ("Use next POKéMON?" after a real faint), `soullink_party3.state` (3 mons, 2 caught
for real), `soullink_trainer_battle.state` (frame 0 of a trainer battle), `soullink_daycare.state` (Pidgey in the Day
Care). The script rebuilds them from room/pallet_after_lab/pokecenter_pc on every run.
