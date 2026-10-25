# Soul Link helpers — Pokémon Crystal (verified)

Crystal v1.0 USA (SHA1 `f4cd194b…`). Code: `client/yellowlink/crystal.py` (section "Soul Link"), unit tests
`client/tests/test_soullink_crystal.py`, real-ROM checks `dev/crystal/soullink_verify.py` (ends with `ALL PASS`),
area table generator `dev/crystal/gen_soullink_areas.py`. Every address is from pret/pokecrystal `pokecrystal.sym`
(symbols branch) and exercised in the running ROM. D000-DFFF = WRAM bank 1 (`mem[1, addr]`), C000-CFFF/FFxx unbanked.

## Addresses

| name | addr | use |
|---|---|---|
| wLinkMode | C2DC | ≠ 0 → link battle → kind "special" |
| wBattleMon / Species | C62C | WRAM0; zeroed by ClearBattleRAM (ByteFill wBattle C608, 0x139 bytes) before wBattleMode is set |
| wBattleMonDVs / Level | C632 / C639 | switch / Transform validation in battle_party_slot |
| wBattleMonHP / MaxHP | C63C / C63E | BE; the active mon's live HP |
| wPlayerSubStatus5 | C66C | bit 3 SUBSTATUS_TRANSFORMED |
| wEnemyStats | C6C1 | 5×16 bit; zeroed by ClearBattleRAM, written as the last step of LoadEnemyMon → "enemy loaded" |
| wInBattleTowerBattle | CFC0 | bit 0 → kind "special" |
| wCurBattleMon | 1:D0D4 | party index of the active mon |
| wTempEnemyMonSpecies | 1:D204 | wild species chosen before the battle |
| wEnemyMon / Species | 1:D206 | **not** cleared between battles (stale until LoadEnemyMon's ByteFill) |
| wEnemyMonDVs / Level / MaxHP | 1:D20C / D213 / D218 | |
| wBattleMode | 1:D22D | 0 none, 1 wild, 2 trainer |
| wOtherTrainerClass | 1:D22F | ≠ 0 in trainer battles |
| wBattleType | 1:D230 | BATTLETYPE_*; set by the overworld/script **before** the battle, cleared in CleanUpBattleRAM |
| wStatusFlags | 1:D84C | bit 6 STATUSFLAGS_HALL_OF_FAME_F |
| wStatusFlags2 | 1:D84D | bit 2 STATUSFLAGS2_BUG_CONTEST_TIMER_F |
| wNumBalls / wBalls | 1:D8D7 / D8D8 | count + (item, qty) pairs + FF, max 12 |
| wHallOfFameCount | 1:D95E | +1 per Hall of Fame entry (cap 200) |
| wEventFlags | 1:DA72 | EVENT_BEAT_ELITE_FOUR = 68 → DA7A bit 4 |
| wParkBallsRemaining | 1:DC79 | Bug-Catching Contest balls |
| wDayCareMan / wBreedMon1 | 1:DEF5 / DEF6 | bit 0 HAS_MON; nickname DEF6, OT DF01, box struct DF0C |
| wDayCareLady / wBreedMon2 | 1:DF2C / DF2F | bit 0 HAS_MON; nickname DF2F, OT DF3A, box struct DF45 |
| sPartyMail | 0:A600 | 6 × 0x2F; shifted by RemoveMonFromPartyOrBox |
| sBox (count/species/mons/OTs/nicks) | 1:AD10 / AD11 / AD26 / AFA6 / B082 | current box (end B15E) |
| HALL_OF_FAME map | 16:8 → 0x1008 | |

Helpers used only by the verify script: wBugContestStartTime 1:DC35 (day, hour, min, sec), wBugContestMins/Secs
1:D46C/D46D, wCurDay 1:D4CB, hHours FF94, hMinutes FF96, hSeconds FF98, wStepCount 1:DC73, wPlayerTileCollision 1:D4E4.

## battle_info() / battle_kind()

* `battle_kind()` is right on the **first frame** wBattleMode ≠ 0 (24/24 wild encounters, the trainer battle, the
  tutorial): wBattleType is written before the battle starts.
* `battle_info()` returns **None on frame 0 and the full dict from frame 1** in a wild battle (24/24 encounters
  measured, plus 3 back-to-back battles). Reason: InitEnemyWildmon sets wBattleMode *before* LoadEnemyMon, and
  wEnemyMon still holds the previous battle's mon until LoadEnemyMon's ByteFill — so the code waits for
  wEnemyStats ≠ 0 (cleared by ClearBattleRAM before the mode is set, written at the very end of LoadEnemyMon).
  Never reports a stale mon (checked against wTempEnemyMonSpecies on every frame of 3 consecutive battles).
  → Record the encounter on the **first non-None** battle_info, not on wBattleMode.
* Trainer battles: the dict comes on frame 0 with `dex 0` until the trainer's first mon is out (then its species).
* `shiny`: Gen-2 rule — Def = Spd = Spc DV = 10 and Atk DV ∈ {2,3,6,7,10,11,14,15}.
  Extra keys: `battle_type`, `level`, `dvs`.

### BATTLETYPE classification (values checked in the ROM's compiled `cp` instructions)

| value | constant | used by | kind |
|---|---|---|---|
| 0 | NORMAL | every grass/surf encounter, `loadwildmon` statics (Sudowoodo, Snorlax…), trainers | wild / trainer by wBattleMode |
| 1 | CANLOSE | rival battle in Cherrygrove (no blackout, HealParty after win **and** loss) | trainer |
| 2 | DEBUG | debug battles (unreachable in retail) | special |
| 3 | TUTORIAL | the Dude's catching demo on Route 29 (verified for real) | special |
| 4 | FISH | rod encounters | wild |
| 5 | ROAMING | Raikou / Entei | wild |
| 6 | CONTEST | Bug-Catching Contest (verified for real) | wild, `contest: True` |
| 7 | FORCESHINY | Red Gyarados (DVs EA AA → shiny) | wild |
| 8 | TREE | Headbutt | wild |
| 9 | TRAP | the Electrodes in the Team Rocket Base (can't run) | wild |
| 10 | FORCEITEM | Ho-Oh, Lugia, Snorlax (hold their item) | wild |
| 11 | CELEBI | GS Ball Celebi (can't run) | wild |
| 12 | SUICUNE | Suicune in Tin Tower 1F | wild |

Also "special": link battles (wLinkMode ≠ 0) and Battle Tower battles (wInBattleTowerBattle bit 0; the party is
healed afterwards). In the contest the caught mon goes to wContestMon (not party/box) and the PACK is replaced by
PARK BALL — the rules engine should treat contest catches separately.

## battle_party_slot()

`(wCurBattleMon, wBattleMonHP)` or None. None before the first mon is out (wBattleMon zeroed) and during the instant
of a switch where wCurBattleMon is already the new index but wBattleMon still holds the old mon (the game writes
wCurBattleMon, then InitBattleMon): validated by species + DVs vs. the party struct, or level + max HP while
SUBSTATUS_TRANSFORMED. Verified: after a faint the switch-in reported `(1, 17)` and never `(1, 0)`.

**Faint timing (real faint, Route 29):** wBattleMonHP becomes 0 the moment the damage is applied (before the HP bar
animation); the "… fainted!" text appears ≈ 150 frames later and the party struct HP becomes 0 only ≈ 360 frames
after the hit (UpdateFaintedPlayerMon → UpdateBattleMonInParty, after the text). Status is cleared too.
→ Use battle_party_slot() for live faint detection; the party struct only catches up later.

## Ball pocket

`has_poke_balls()`: any Ball-pocket item with qty > 0. The ROM's ItemAttributes put exactly these 12 in the Ball
pocket: MASTER/ULTRA/GREAT/POKé BALL, Kurt's HEAVY/LEVEL/LURE/FAST/FRIEND/MOON/LOVE BALL **and PARK BALL** (the raw
Apricorns are normal items). During the Bug-Catching Contest the bag is not used: battles throw from
`wParkBallsRemaining` (`park_balls_remaining()`, 20 at the start); `in_bug_contest()` = contest timer flag. Verified:
a real MASTER BALL throw catches and removes it from the pocket.

## Hall of Fame

`in_hall_of_fame()`: map 0x1008 (and a player name exists). It stays True through the HoF scene where the game clock
is paused (`in_game()` is False there) until the credits reset. Persistent proof, all set by the real scene and
surviving SAVE + reload: `entered_hall_of_fame()` = EVENT_BEAT_ELITE_FOUR (set first, by the map script) or
wStatusFlags bit 6 or wHallOfFameCount > 0 (`hall_of_fame_count()`; both set by the HallOfFame routine, which then
saves the game itself). After reloading that save the player is in New Bark Town (1804).

## Current box + forced moves

* `current_box_records()`: sBox as 55-byte Gen2PC records (egg flag from species list 0xFD).
* `move_party_to_box(slot)` mirrors SendGetMonIntoFromBox(PC_DEPOSIT) + RestorePPOfDepositedPokemon +
  RemoveMonFromPartyOrBox: appends to sBox (species list entry copied, so eggs stay 0xFD), restores PP to max keeping
  PP Ups (ComputeMaxPP: +min(base/5, 7) per Up), shifts the party exactly like the game (incl. the "last slot only
  gets FF in its OT name" case) and shifts sPartyMail. Returns False if the box is full, the slot doesn't exist,
  no *other* non-egg party mon has HP > 0 (the game's "last #MON" / "no more usable #MON") or the mon holds MAIL.
* `move_box_to_party(idx)` mirrors SendGetMonIntoFromBox(PC_WITHDRAW): level = CalcLevel(exp) (fixes a wrong level
  byte), stats via CalcMonStats with stat exp, status 0, HP = max HP (egg: 0), byte 33 left untouched; then removes
  the sBox entry. False if the party is full / index out of range. Needs `CrystalMemory(mem, RomData)`.
* **Verified byte-for-byte**: each of 4 deposits (inside slot with mail shift, an egg, the 6th slot, …) and 4
  withdraws (stale-level SPINARAK with stat exp, an egg, a normal mon, the last entry) were done once by
  Bill's PC and once by our function from the same save state; party (DCD7-DE83), sBox (AD10-B15E) and sPartyMail
  were identical. Then: game party menu shows the new party, Bill's PC withdraws/deposits the force-moved mon
  normally, in-game SAVE → CONTINUE works, checksum valid, party and box identical.
* **Nuzlocke caveat — the PC heals:** a box struct has no HP/status, so any withdraw (ours or Bill's PC) gives
  full HP and no status. A fainted mon that is boxed and withdrawn comes back alive (verified: a burned CYNDAQUIL
  with −4 HP came back healed). The rules engine must track dead mons by mon_key, not by HP.
* Call only while `is_safe()` (Bill's PC caches box lists while open). Box writes don't touch the save checksum.

## Day Care

`daycare_records()`: `[man's mon, lady's mon]` (only those whose HAS_MON bit is set) as 55-byte records
(box struct + OT + nickname, egg flag 0 — eggs can't be left there). Verified by leaving PIDGEY with the Day-Care
Lady for real: the record equals the party copy byte for byte and has the same mon_key. The Day Care adds exp
while it's there (key unaffected); on pickup the game recomputes level/stats/moves and **resets exp to the level's
minimum** (a known bug, key unaffected).

## mon_key()

`hex(OT ID[6:8] + DVs[21:23] + caught data[29:31])` (12 hex digits), works on party structs (48 B), box/day-care
records (55 B) and raw 32-byte box structs. Stable across level-ups, evolution (only species/stats change),
deposit/withdraw (bytes 0-31 are copied verbatim — verified), Day Care, nickname changes. **Hatching** writes caught
data over the egg's 0000 (SetEggMonCaughtData: caught level 1 + time of day, location) — so caught data with caught
level 1 (only hatched mons; wild mons are ≥ level 2) is normalised to 0000; verified on a real hatch:
egg key == hatched key.

Collision risk: all of the player's own catches share the OT ID, so uniqueness comes from 16 DV bits + caught data
(level 6 bits, time-of-day 2 bits, location 7 bits, gender bit). Two mons caught at the same level in the same area
at the same time of day collide with probability 1/65536 per pair (~0.4 % for ~25 such pairs within a run is far
less in practice). Weaker cases: gift mons (caught data = level 0 + LANDMARK_GIFT for all of them), mons traded
from Gold/Silver (caught data 0000), and hatched siblings (0000 after normalisation; breeding copies the Def DV and
the Special DV ±8 from a parent, so siblings share ~4-5 of 16 DV bits → roughly 1/2048 per pair). If a collision
matters, combine the key with species family or the moment the mon was first seen.

## Areas (AREAS, area_of, area_name)

All 388 maps, generated from `constants/map_constants.asm` + `data/maps/maps.asm`: the area is the map's **landmark**
(the Pokégear location), which already groups every dungeon floor (Union Cave 3, Tin Tower 10 incl. roof, Whirl
Islands 8, Ruins of Alph 15, Mt. Silver 6 incl. outside + Pokémon Center, Mt. Moon incl. Mt. Moon Square + gift
shop, Lake of Rage incl. its 2 houses, National Park incl. the contest copy, …) and gives interiors/gatehouses the
town/route they belong to (Elm's Lab → New Bark Town; ROUTE_31_VIOLET_GATE → Route 31; Day Care → Route 34;
Victory Road gate → Route 26). Overrides (separate places with a town landmark): Team Rocket Base (Mahogany),
Goldenrod Underground (+ switch room, warehouse; Dept. Store B1F stays Goldenrod City), Olivine Port / Vermilion
Port (+ passages); the LANDMARK_SPECIAL link rooms (Pokémon Center 2F, Trade Center, Colosseum, Time Capsule,
mobile rooms) are one area each. Checked against the ROM's map table (group sizes; every area = one ROM landmark;
landmarks split only by these overrides). Unknown ids → `map_XXXX`.

## Fixtures (dev/states/, gitignored, rebuilt by soullink_verify.py)

crystal_sl_route29.state (Route 29 grass, MASTER BALL + 5 POKé BALL), crystal_sl_wild.state (wild battle menu),
crystal_sl_caught.state, crystal_sl_faint.state (lead at 1 HP in battle), crystal_sl_trainer.state (Bug Catcher Don),
crystal_sl_contest.state, crystal_sl_pc6.state (6-mon party incl. egg + mail, 3 mons in sBox), crystal_sl_daycare.state.

## Not verified in a running game

BATTLETYPE FISH/ROAMING/FORCESHINY/TREE/TRAP/FORCEITEM/CELEBI/SUICUNE/CANLOSE/DEBUG — only by their compiled values
in the ROM and the source (they all go through the same LoadEnemyMon path). Link and Battle Tower battles
(classification by wLinkMode / wInBattleTowerBattle from the source). Transform during battle_party_slot (unit-tested
only). The Day-Care **Man** slot (wBreedMon1): same layout per the symbols and unit-tested, but only the Lady's slot
was filled for real (the Man stands outside only after the Odd Egg event).
