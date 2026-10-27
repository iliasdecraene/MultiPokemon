# Raids — design and feasibility (draft for Ilias)

Status: **design + prototypes only, nothing built into YellowLink yet.** Branch `raids` (from 0.7.6).
Prototypes: `dev/raids/proto/` (throwaway scripts that run on the real ROMs; how to run them is at the end).

---------------------------------------------------------------------------------------------------

## 1. The short version

**What you asked for works in all three games.** I tested the hard parts on the real Yellow, Crystal
and Platinum ROMs. In each game I could:

| | Yellow | Crystal | Platinum |
|---|:-:|:-:|:-:|
| start a wild battle against any Pokémon, at any level, anywhere, whenever we want | ✅ | ✅ | ✅ |
| give the boss our own moves (the Pokémon you catch is a normal one; in Platinum it keeps its raid moves unless we put its natural ones back) | ✅ | ✅ | ✅ |
| shared HP: the boss **never faints in your game** until the group's HP pool is empty | ✅ | ✅ | ✅ |
| count your damage and send it to the server, then sync your boss to the group's HP every turn | ✅ | ✅ | ✅ |
| block Poké Balls while the boss still has HP (even the Master Ball; the ball isn't used up) | ✅ | ✅ | ✅ |
| when the boss is down: a **guaranteed** catch with any ball (normal catch: Pokédex, nickname, PC if your party is full) | ✅ | ✅ | ✅ |
| make the boss run away (raid time over / no more throws) | ✅ | ✅ | ✅ |
| see when the game waits for your move, and pick a move automatically (the 10-second timer) | ✅ | ✅ | ✅ |
| blacking out works like a normal battle (Pokémon Center, no catch) | ✅ | ✅ | ✅ (game's own) |
| draw a raid den in the overworld | ✅ | (same code as Yellow) | ✅ |
| pick random reachable spots from the map data | ✅ Kanto: 11,263 spots on 36 maps | to do | to do |

Platinum was the scary one: there is no way to "write a Pokémon and start a battle" by changing memory,
like on the Game Boy. I found a trick to make the DS game call its own functions on command (the
same ones the game uses for Giratina or Darkrai), so a raid battle in Platinum is a 100 % normal battle
of the game itself (details in the appendix).

**How it would feel (my proposal):**

1. Every 2 hours a raid opens on your server. Everyone online gets a chat line, a toast and a sound:
   *"RAID! Rayquaza appeared on Route 210 (Sinnoh) - 15 minutes!"*. The sidebar shows a raid card
   (region, place, time left, who is fighting, HP left).
2. In that region a **raid den** stands on that spot (a glowing stone tower, our own drawing - Spiritomb's
   Hallowed Tower style for Sinnoh). An arrow at the edge of the game screen points to it.
3. Walk up to it and press A → "Join the raid against Rayquaza?" → Yes → a normal wild battle starts.
4. You fight **in your own game, at your own pace** - nobody waits for anyone. Every hit you do comes off
   the group's HP. The HP bar shows the boss's current "bar" (big bosses have several bars, like in a
   fighting game) and our banner above the game shows the whole group HP, the timer and who is in.
5. When the group brings it down, everyone still in the battle gets the catch: the boss falls asleep at
   1 HP, stops attacking, and your next ball catches it.
6. If your team faints you black out as usual and you're out (no catch). If the 15 minutes run out first,
   the boss runs away in everyone's game.

**What I need from you** is in section 4 (decisions). The big ones: who gets a catch, guaranteed catch or
real odds, how strong the bosses are (same for everyone or scaled to each player), and which Pokémon.

---------------------------------------------------------------------------------------------------

## 2. Which Pokémon (proposal)

A region's raids happen in that region's game, so every boss must exist in that game. Gen 1 can't hold
Gen 2+ Pokémon, so Kanto's list is short.

**Sinnoh (Platinum)** - your list, all of them can't be caught in Platinum:
Mewtwo, Lugia, Ho-Oh, Raikou, Entei, Suicune, Latias, Latios, Kyogre, Groudon, Rayquaza,
and the mythicals Mew, Celebi, Jirachi, Deoxys (any of its 4 forms), Manaphy, Phione.
Also uncatchable in a normal Platinum, if you want them (your choice):
- Darkrai, Shaymin, Arceus (event-only: Member Card, Oak's Letter, Azure Flute)
- Regirock, Regice, Registeel and Regigigas (in Platinum they need each other / a Pal Park transfer)
- *Not* the Kanto birds (they roam Sinnoh after the National Dex) or Uxie/Mesprit/Azelf/Cresselia/Heatran/
  Giratina/Dialga/Palkia (all catchable in Platinum).

**Johto (Crystal)**: Celebi (the GS Ball event never came out on the original US cartridge), Mew,
Mewtwo, Articuno, Zapdos, Moltres (none of these are in Crystal). Optional "second chance" raids:
Lugia, Ho-Oh, Suicune, Raikou, Entei (only one each per save).

**Kanto (Yellow)**: Mew. Optional second chances: Articuno, Zapdos, Moltres, Mewtwo (one each per save).
(If Kanto feels thin, Yellow's missing Pokémon - e.g. Weedle/Ekans/Meowth/Koffing lines - could be
"rare raids", but they're not legendary.)

Mythicals in Platinum get the game's "fateful encounter" flag, so Mew/Deoxys/Darkrai/Shaymin/Arceus obey
(the games check it). The caught Pokémon goes through your PC database like any catch, so you can trade
it, gift it, and bring it to the other games (as a placeholder where the game can't hold it).

Boss move sets: picked by us per species (2-4 signature moves). Never moves that end the battle or knock
the boss out by itself (Explosion, Selfdestruct, Memento, Teleport, Roar, Whirlwind, Metronome).

---------------------------------------------------------------------------------------------------

## 3. How it works (plain language)

**One boss per player, one HP pool for the group.** Each player fights their own copy of the boss in
their own game. YellowLink watches the boss's HP in your game; every point you take off is sent to the
server, which keeps the group's HP. At the start of each of your turns YellowLink sets your boss's HP to
the group's current value (so you also see everyone else's damage). Your boss can never faint in your
game while the group still has HP: if your hit would knock it out, it's left at 1 HP and the rest is
counted for the group. Proven on all three ROMs: the boss stayed alive through several "bars" and the
damage added up exactly.

**Group HP size.** Each player who joins adds HP (proposal: 2 "bars" each - a bar is the boss's normal HP).
So a solo raid needs 2 bars, 4 players need 8 bars between them. Easy to tune on the server.

**The 10-second move timer.** YellowLink knows when the game waits for your choice (battle menu, move
list, bag, party, run). A ring counts down 10 seconds. When it runs out, YellowLink closes whatever menu
you're in and picks a move for you. Note: because everyone fights in their own game, nobody ever waits
for a slow player - a slow player only does less damage. So the timer is about pace and fairness, not
about stalling the group. (That's why I'd also switch off fast-forward during raid battles: with
fast-forward one player would get more turns in the 15 minutes.)

**The catch.** When the group HP hits 0 the boss falls asleep at 1 HP (and stops attacking); Poké Balls work
again and (proposal) the first ball always catches it. If your party is full it goes to the PC, exactly
like a normal catch (Yellow/Crystal refuse to throw if party *and* current box are full - YellowLink
will warn you when you join). Caught raid Pokémon are normal Pokémon: their real moves for their level,
your OT, your Pokédex.

**Before the Champion.** Anyone can join. A weak team blacks out (normal blackout: Pokémon Center, lose
some money) and doesn't get a catch. Option: scale the boss's level to each player's team (section 4).

**Where raids appear.** Random spots on outdoor routes and towns, on open walkable ground (not in grass,
water, buildings, not on doors, signs or people, and reachable on foot inside that map). I generated
these from the map data for Kanto: 11,263 spots on 36 routes/towns. Some places need HMs or badges to
reach (Cinnabar, Route 21, Victory Road side...), so the spot lists get a "reachable from" level by hand
and the server picks spots that the players online can reach.

**Finding it.** Chat line + toast + sidebar card with the place name; inside that region an arrow on the
edge of the screen points to the den with the distance in steps. Drawing a marker on the games' own Town
Map / Pokégear / Pokétch screen is possible later but is a lot more work per game.

**Raids and the rest of YellowLink:**
- **Soul Link runs**: no raids inside a run (runs are isolated - no PC database, no trades). You see the
  notice but can't join until you're back in your main game.
- **Exp Share All**: nothing changes - the boss never faints, so a raid gives no EXP to anyone.
- **Badges**: a caught raid Pokémon counts for the Pokédex like any catch (the Pokédex badges already
  leave out Mew/Celebi/Manaphy). A shiny raid boss: your choice (section 4).
- **PC database / trades / gifts**: raid catches are normal; you can trade them. While you're in a raid
  battle nobody can send you trade/battle requests (you're "busy", like in a link battle).
- **Closing the game mid-raid**: you leave the raid; when you open YellowLink again you're back where you
  were just before the raid battle (the same rule Exp Share All uses).
- **Friends in another region**: they see the notice and can travel there (save + switch region) if
  they want to join in time.
- **Your two servers**: each server has its own raids for its own group; your shared save gets the
  catches from whichever server you're playing on.
- **Fast-forward**: switched off during raid battles (proposal).
- **Safari Zone / Bug-Catching Contest / Great Marsh / walking with a partner (Platinum)**: can't join
  from there (those change how battles work).

---------------------------------------------------------------------------------------------------

## 4. Decisions for you (my recommendation first)

1. **Who gets a catch?** (a) everyone still in the battle when the group HP hits 0 (**recommended**),
   (b) only players who did at least 5 % of the damage, (c) everyone who joined, even if they blacked out.
2. **Catch rule**: (a) **guaranteed**: first ball catches (**recommended** - it's a friends' game and you
   waited 2 hours), (b) real odds with a boost and 3 throws, then it runs away, (c) no catching: it's
   sent to your PC as a reward.
3. **Boss level**: (a) **scaled per player**: your boss is (your strongest Pokémon + 5), between 20 and
   70 - everyone can contribute, weak teams still struggle (**recommended**, possible because everyone
   has their own copy and the server counts damage in "bars", not HP points), (b) one fixed level per raid
   (e.g. 50: early players mostly black out, like you said), (c) "star" levels like Sword/Shield
   (★1 = 20 ... ★5 = 70), shown in the notice.
4. **Level of the caught Pokémon**: the boss's level (a) - or capped at your strongest Pokémon's level (b).
   Your own catches always obey, so (a) is fine but gives early players a big jump.
5. **Group HP**: 2 bars per player (recommended), or a fixed amount per raid.
6. **The 10-second timer** - what it picks: (a) **the move you used last turn, else your strongest move**
   (recommended), (b) always your first move, (c) a random move. In menus (bag/party) it backs out and
   attacks; after your Pokémon fainted it sends out the next healthy one. Keep 10 s or go to 15 s?
7. **Schedule**: every 2 hours per server; only between e.g. 10:00 and 24:00? Which region: (a) **the
   region where most players are right now** (recommended), (b) take turns Kanto → Johto → Sinnoh,
   (c) all three at once. Skip a raid when nobody is online (recommended).
8. **Shiny bosses**: never / normal odds / boosted odds; and does a shiny raid catch count for the Shiny
   hunter badge?
9. **After a blackout**: can you rejoin the same raid (heal, walk back) or are you out (recommended: you
   may rejoin while it's still open - it only adds more HP to the pool... or not: your call)?
10. **Extra rules**: Darkrai/Shaymin/Arceus and the Regis in Sinnoh's list? Second-chance raids for
    one-per-save legendaries in Kanto/Johto?
11. **Moves of the caught Pokémon**: its natural moves for its level (like a normal catch; automatic in
    Yellow/Crystal, an extra write after the catch in Platinum), or the boss's special raid moves.
12. **The den's look**: our own drawn stone tower (works the same in all games), or later in Platinum a
    real game object (a rock or a legendary's overworld sprite) that you bump into like an NPC.

---------------------------------------------------------------------------------------------------

## 5. Risks, ranked - and what the prototypes proved

| # | risk | how bad if it fails | proven? |
|---|---|---|---|
| 1 | Platinum: starting a battle with any Pokémon on demand | no Sinnoh raids at all | **Yes** - the game's own functions called from YellowLink; Darkrai/Latias battles started in Twinleaf, on Route 202, in a Pokémon Center and the Day Care |
| 2 | Boss must not faint locally while the group has HP | raids break | **Yes** (all 3 games). Game Boy: the game tests for a faint 24-111 frames after the HP hits 0, so checking every frame is enough. Platinum: **checking every frame was too late** (the boss fainted and my Turtwig got EXP in the first try) - fixed by stopping the damage at 1 HP inside the game's own HP update, the way a Focus Sash does; that path also covers Perish Song / Destiny Bond / recoil there. Game Boy Perish Song / Destiny Bond: not yet (appendix A.6) |
| 3 | Guaranteed catch after the pool is empty | catch rule (b) only | **Yes** (Y/C: catch rate 255 + 1 HP + sleep; P: forced in the game's catch formula) |
| 4 | Balls blocked during the fight | someone catches it alone | **Yes** (all 3, incl. Master Ball; ball not used, no turn lost) |
| 5 | Timer: know when the game waits; auto-pick | timer can't work | **Yes** for the battle menu + move list (all 3). Not yet: the party menu after a faint, Platinum's "Use next Pokémon?" |
| 6 | Caught Pokémon is clean (real stats, no leftovers from the raid battle) | weird Pokémon | **Yes** (Y: the game rebuilds it with its natural moves; C: natural moves kept in the game's wild-move copy; P: the battle writes back into the Pokémon, so it gets its raid moves on purpose - or we put its natural moves back after the catch, your choice) |
| 7 | Spots: reachable, sensible, not unreachable before HMs/badges | den where nobody can go | Kanto generator **works**; Johto/Sinnoh generators not written; "reachable from" levels need hand checks |
| 8 | Den interaction + collision (you shouldn't walk through it) | cosmetic | drawing **proven**; A-press + blocking the step: not prototyped (simple input filter) |
| 9 | Network: lag, disconnects mid-raid, server restart | some damage lost | design only (damage is sent as a running total, so resending is safe) |
| 10 | Closing the app mid-raid / resume snapshots | rollbacks | design only (same rule as Exp Share All, already working) |

---------------------------------------------------------------------------------------------------

## 6. Prototype results (what ran)

All three ran on the real ROMs (Yellow `cc7d0326…`, Crystal `f4cd194b…`, Platinum `ce81046e…`), ending
with `ALL PASS`. The fight was "Lv5 starter vs a weakened boss" so the test finishes quickly; the boss
used Splash so the starter survives.

**Yellow** (`yellow_raid.py`): Mew Lv30 started 3 frames after the trigger; overrides in place before
the HUD; first ball (and Master Ball) refused with Oak's "This isn't the time to use that!", ball kept;
12 hits summed exactly to the pool (100 HP = 2.5 bars of 40), boss clamped at 1 HP three times and never
reached the faint routine; menu → move locked in seen every turn; pool 0 → one Poké Ball caught Mew
(Pokédex owned, player's OT, moves = its real Lv30 set); Teleport-only boss ended the battle; real
blackout back to the healing spot; the faint check comes 24-111 frames after HP 0.

**Crystal** (`crystal_raid.py`): Celebi Lv30 via the game's own wild-battle script; same results; the
caught Celebi has its real moves (Heal Bell, Safeguard, AncientPower, Future Sight), the game's
whiteout script handled the blackout; faint check 40 frames after HP 0.

**Platinum** (`pt_raid.py`, `pt_common.py`): Latias (Lv12 so a Lv7 Turtwig can hurt it without stat
tricks) started by calling the game's own 4 functions; first command menu reached; a Master Ball picked
in the bag was turned back into "cancel" (menu again, ball kept); 31 hits added up exactly to the pool
(1.5 bars of 41 HP), two bar ends clamped to 1 HP inside the game's HP update; menu → move → locked-in
seen every turn; the Turtwig got no EXP; pool 0 → sleep → one Poké Ball, forced 4 shakes, "Gotcha!",
Pokédex, nickname, Latias in the party at 1 HP with the same PID, Poké Balls 5 → 4; a Teleport-only
boss left the battle. The road there found three real problems (now in the appendix): a frame watchdog
is too late in Platinum, the battle copy is written back into the caught Pokémon, and **B on Platinum's
battle menu is the Run shortcut** (my test player fled once) - the auto-pick must use touch, never B.

**Placement** (`yellow_spots.py`): 36 Kanto outdoor maps, 11,263 candidate squares (e.g. Route 1: 326,
Cerulean: 510, Route 21: 2 - it's almost all water).

**Den drawing** (`den_draw.py`): screenshots with the den drawn on Route 1 (Yellow) and in Twinleaf
(Platinum, projected with the same camera maths that draws your friends).

---------------------------------------------------------------------------------------------------

## 7. Open questions

- Your decisions in section 4.
- Perish Song / Destiny Bond against the boss (appendix A.6): block those moves, or accept that the boss
  "escapes" from that one player?
- Boss move sets per species - I can propose a table once you've picked the Pokémon lists.
- Platinum: the bag's ball pocket is ours to read, but if a player has no balls at all at catch time,
  should YellowLink give them one Poké Ball? (Recommended: yes, one.)
- Should there be a small reward for everyone who helped but didn't catch (Rare Candy? nothing)?

---------------------------------------------------------------------------------------------------
---------------------------------------------------------------------------------------------------

# Appendix A — technical (for whoever builds it)

Everything below was found on the real ROMs; addresses are only valid for the three verified SHA1s
(`App.full`). Every feature here must stay behind `App.full`, outside challenges, and follow CLAUDE.md
(no ROM data shipped, protocol in SPEC.md first, server+client together).

## A.1 Shared-HP model (all games)

- Server pool in **milli-bars**: 1 bar = 1000 = one full HP bar of the boss in the attacker's own game.
  Damage d on a local boss with max HP M counts `d * 1000 / M`. This makes per-player boss levels
  possible (decision 3a): different M per player, same pool.
- Local HP = the current segment: `seg = ((pool - 1) % 1000 + 1) * M / 1000` rounded up, >= 1.
  The game's HP bar therefore shows the current bar; our banner shows bars left + total %.
- Sync points (write local HP = segment, refill boss PP, set the phase's moves):
  Yellow `MainInBattleLoop` (0F:4249, before the game's own HP-0 check), Crystal `BattleTurn.loop`
  (0F:412F, before AIChooseMove), Platinum: `BattleContext.command` becomes
  `BATTLE_CONTROL_INIT_COMMAND_SELECTION` (4).
- Damage = observed drops of the boss's HP between frames, while in the fight phase. HP increases (drain
  moves, Leftovers...) are ignored and undone at the next sync. A hit that crosses a bar boundary is
  capped at the rest of that bar (like Sword/Shield shields) - simple and idempotent.
- **Watchdog (Yellow/Crystal)**: every frame, if boss HP == 0 and the pool > 0 → write 1. Measured
  margins from HP 0 to the game's faint handling: Yellow 24-111 frames, Crystal 40.
- **Platinum: clamp at the HP update, not after it.** Two things went wrong on the way and are worth
  knowing:
  1. A frame watchdog is too late: the HP update (`BtlCmd_UpdateHealthBarValue`, battle command 26) and
     the faint test (`BtlCmd_TryFaintMon`, command 28) can run in the same frame - the boss fainted and
     the lead got EXP.
  2. Fixing HP to 1 at the faint test isn't enough either: every HP update also writes the battle copy
     back into the Pokémon (`BattleMon_CopyToParty` → `EmitUpdatePartyMon`), that Pokémon keeps 0 HP,
     the battle copy is later reloaded from it, and `CheckBattleOver` (it only tests `curHP == 0`) ended
     the battle in one run.
  So the clamp works like the game's own Focus Sash (`BtlCmd_CheckHoldOnWith1HP`): exec hook at
  **0x02241814** inside `BtlCmd_UpdateHealthBarValue`, right after the battler is resolved (r2 = battler,
  r4 = BattleContext; code `1c48171c2518c020...` checked): if r2 == 1 (the boss), `hpCalcTemp`
  (ctx +0x215C, s32, negative = damage) would take it to <= 0 and the raid isn't over → write
  `hpCalcTemp = -(curHP - 1)`. The HP bar animates down to 1 px, the Pokémon never has 0 HP, every HP
  change in Platinum goes through this command (moves, poison, recoil, weather, Perish Song...). A second
  hook on the faint test's read of curHP (**0x022418E8**, `ldr r1, [r2, r1]`, r4 = battler) stays as a
  safety net. Both proven in `pt_raid.py` (bar boundaries + the final hit; caught at 1 HP, no EXP).
- OHKO moves / Super Fang / Endeavor take at most the current bar (they act on the local segment).

## A.2 Starting the battle

**Yellow**: when `is_safe()`: `wCurOpponent` (D058) = species (internal id < 200), `wCurEnemyLevel` (D126)
= level. The overworld loop starts a wild battle on the next frame (InitWildBattle). Overrides at the
hook `_InitBattleCommon` (3D:60EB): after LoadEnemyMonData, before the enemy HUD: `wEnemyMonMoves`
(CFEC), `wEnemyMonPP` (CFFD), `wEnemyMonMaxHP`/`HP` (CFF3/CFE5), stats. "Wild X appeared!" waits for a
button (normal).

**Crystal**: set what `loadwildmon` sets - `wTempWildMonSpecies` (1:D22E), `wCurPartyLevel` (1:D143),
`wBattleScriptFlags` (1:D459) = 0, `wBattleType` (1:D230) = BATTLETYPE_NORMAL - then queue a memory
script: `wMapReentryScriptBank/Address` (1:D45D/E) = 25:7CFA (= the ROM's `WildBattleScript` + 1:
`startbattle / reloadmapafterbattle / end`) and `wMapReentryScriptQueueFlag` (1:D45C) = 1.
`PlayerEvents → RunMemScript` runs it within a few frames (battle 183 frames later incl. the transition).
The game's own script handles whiteout and the "box full - Bill calls" message.
Overrides at `BackUpBGMap2` (0F:7568, called right after InitEnemy): `wEnemyMonMoves`/`PP` (1:D208/D20E),
HP/max (1:D216/D218), and **`wWildMonPP`** (C739): Crystal spends the wild mon's PP there, indexed by
the move slot (our first prototype ran out of "Heal Bell" PP). Never change `wWildMonMoves` (C735): a
catch copies wWildMonMoves/PP into the party - restore `wWildMonPP` to the real values when the ball is
thrown.

**Platinum**: four game functions, called from Python (see A.3):
```
dto = FieldBattleDTO_New(HEAP_ID_FIELD2 = 11, BATTLE_TYPE_WILD_MON = 0)   0x02051D8D
FieldBattleDTO_Init(dto, fieldSystem)                                      0x02052315
CreateWildMon_Scripted(fieldSystem, species, level, dto)                   0x022420D5 (overlay 6, check code bytes)
dto->battleStatusMask (+0x18C) |= BATTLE_STATUS_LEGENDARY (8)              legendary music/intro
[mythicals: set MON_DATA_FATEFUL_ENCOUNTER on dto->parties[1] mon 0 (pk4.py)]
Encounter_NewVsWild(fieldSystem, dto)                                      0x02050E11
```
= what `ScrCmd_StartWildBattle` → `Encounter_NewVsSpeciesAtLevel` does (found by disassembling
`gFieldScriptCommands[292]` at 0x020EAC58), but with `Encounter_NewVsWild`, which creates its own field
task (`FieldTask_WildEncounter`: battle, party copy-back, blackout, catch into PC). Refuse when the
safari flag is set (it would make a Great Marsh battle) or FLAG_HAS_PARTNER (double with a partner).
Battle starts ~190 frames later (transition).
**Moves go into the wild Pokémon itself** (`dto->parties[1]` → Party {capacity, count, 236-byte mons},
decrypt/encrypt with pk4.py) between CreateWildMon_Scripted and Encounter_NewVsWild: the battle copy is
written back into that Pokémon on every HP update, so a move change made only in the battle copy leaks
into the caught Pokémon (seen: a caught Latias that only knew Splash). Consequence: in Platinum the caught
boss knows its raid moves (decision: keep them - "it knows its raid moves" - or put its natural moves back
after the catch with a normal party/PC write). Battle copy `BattleContext.battleMons[1]` (ctx +0x2D40 +
0xC0): moves +0x0C, ppCur +0x2C, curHP +0x4C (s32), maxHP +0x50, status +0x6C - only curHP (sync),
ppCur (refill every turn: out of PP → Struggle, which in the test knocked out the player's Pokémon after
20 turns) and status (catch phase) are written. It is filled over a few frames: wait until maxHP is set,
curHP == maxHP and the level is right.

## A.3 Calling a Platinum function from Python (new technique)

DeSmuME's exec hook runs after the instruction was fetched, so writing r15 does nothing (tested).
Registers r0-r14 and memory can be written. So the call goes through a `pop {…, pc}`:

- Hook `ExecuteFieldProcesses`' epilogue **0x0203CCE0** (`pop {r3, pc}`, runs once per field frame in
  every map; code `08 bd` checked). With a call queued: save the frame (`[sp]`, `[sp+4]`) and r0; write
  the 4th argument into the frame's r3 slot and `fn|1` into its PC slot; r0-r2 = arguments;
  LR = 0x0203CCE1. The pop "returns" into the function; it returns to 0x0203CCE0.
- Back at the hook (SP = saved SP + 8): r0 = result; write the saved frame back at SP-8, set r13 = SP-8,
  r0 = the saved return value; the pop now returns to the real caller.
- One call per field frame, <= 4 register arguments, the field keeps running normally. Proven: 4 chained
  calls in Twinleaf, Route 202, the Sandgem Pokémon Center and the Day Care, battles started every time.
  (`pt_common.EpilogueCaller`; an earlier variant through a `blx r4` table dispatcher only runs on some
  maps - kept in the file for reference.)
- This also enables later: spawning a real MapObject for the den (`MapObjectMan_AddMapObject`), playing
  sounds, opening the town map with a marker...

## A.4 Timer: "waiting for the player" and auto-pick

| game | waiting starts | choice locked in | auto-pick input |
|---|---|---|---|
| Yellow | hook `DisplayBattleMenu` (0F:4F78); move list `MoveSelectionMenu`; also `ChooseNextMon`, `DoUseNextMonDialogue` | hook `MainInBattleLoop.selectEnemyMove` (0F:42BC) | Up, Left (cursor to FIGHT: the menu remembers ITEM/RUN), A; in the move list the cursor starts at `wPlayerMoveListIndex` (CC2E): Up/Down to the slot, A |
| Crystal | hook `BattleMenu` (0F:6139), `MoveSelectionScreen` (0F:64BC) | hook `EnemyTriesToFlee` (0F:4300, right after ParsePlayerAction) | same idea; move cursor = `wCurMoveNum` (1:D0D5); pack pocket/cursor are remembered (`wLastPocket`, `wBallsPocketCursor`) |
| Platinum | `ctx->command` (+0x08) == 5 and `curCommandState[0]` (+0x00) ∈ {1 menu, 4 moves, 6 target, 8 bag, 10 party, 12 run} | state 13/14 or command != 5 | touch: FIGHT (128,90), moves (64,48) (192,48) (64,104) (192,104) on the bottom screen |

Measured (scripted input): Yellow 135 frames menu → locked in, Crystal 141, Platinum 63.
**Platinum: never press B on the command menu - it is the Run shortcut** (my test player ran away from
the raid that way). Back out of sub-menus with the touch screen's back button or by detecting the state. Timer = 10 s wall clock
(not frames) from "waiting starts", counted across sub-menus of the same turn. On timeout: B until the
battle menu, then FIGHT + the chosen slot. Policy (decision 6a): last used move with PP, else the highest
`power × type effectiveness × STAB` from ROM move data (we already read move tables).

## A.5 Balls and the catch

| game | block balls during the fight | catch phase |
|---|---|---|
| Yellow | hook `ItemUseBall` (03:53AD): PC = `ItemUseNotTime` (03:64B5) - PyBoy allows PC writes; no turn used, ball kept | HP 1, status SLP, `wEnemyMonActualCatchRate` (**D006**, not the battle struct's CFEB) = 255 → any ball catches; moves = [Splash] |
| Crystal | hook `PokeBallEffect` (03:68A2): PC = `IsntTheTimeMessage` (03:77ED) | HP 1, SLP, `wEnemyMonCatchRate` (1:D22B) = 255; moves [Splash]; restore `wWildMonPP` at the throw |
| Platinum | exec hook at `BattleControllerPlayer_CommandSelectionInput` entry (0x0224BB28, overlay 16, code checked): if `curCommandState[0]` == 8 (bag) and `ioBuffer[0][0]` (ctx +0x2300) is a ball (item 1-16) → write `PLAYER_INPUT_CANCEL` (0xFF): the menu comes back, the ball stays (it's only removed later in ItemCommand) | HP 1 and **sleep** (re-applied every turn; no move change, see A.2 - asleep, it doesn't attack); exec hook at 0x0224A8D6 (`cmp r1, #0xff` in `BattleScript_CalcCatchShakes`, r1 = catch rate) sets r1 = 255 → 4 shakes. The game then does "Gotcha!", the Pokédex page, the nickname question, party/PC |

Flee (time over / decision 2b): boss moves = [Teleport]. Yellow also needs `wCurEnemyLevel` >= the
player's level (else it can fail); Crystal's wild Teleport always works (pret bug note); Platinum: Teleport in the battle copy's moves
works (the battle ends, back in the overworld; for a flee the leak into the Pokémon doesn't matter).
Party full: the game puts the catch in the PC itself (Gen 1/2: current box, refused when party *and* box
are full - check at join; Gen 4: next box with room). The PC database then picks it up as usual.

## A.6 Known gaps (not prototyped)

- **Game Boy faint without a slow HP drop**: Perish Song (Crystal: HP set to 0 and checked in the same
  routine), Destiny Bond. Fix like Platinum: hook the game's faint tests (Yellow `MainInBattleLoop` /
  after-move checks, Crystal `CheckFaint_*` / `HandleEnemyMonFaint`; PyBoy hooks can also change PC), or
  take away the PP of Perish Song/Destiny Bond on the player's battle mon during raids. If a boss still
  faints in someone's game: that player's battle ends as a win (they get EXP) and they're marked "escaped".
- Party menu after the player's Pokémon faints; Platinum "Use next Pokémon?" (timer + auto-pick).
- Den interaction (A while facing the den square, `is_safe()`), collision (drop the D-pad direction that
  would step onto the den square - also when running/biking), drawing behind menus (existing friend
  logic) and in wide view.
- Johto + Sinnoh spot generators (Crystal: pret `.blk` + blocksets + collision attributes; Platinum:
  outdoor land data permissions from the player's ROM, outdoor maps share one global grid) and the
  "reachable from" (badges/HMs) table per map.
- Platinum: the HP bar's own copy of the HP isn't redrawn when we write the segment at turn start (it
  catches up at the next hit) - our banner shows the true value.

## A.7 Server

Per server instance (each friend group has its own raids). New module `raids.py`:

- **Scheduler**: every `YL_RAID_EVERY` minutes (default 120) at a random minute inside the slot,
  `YL_RAID_OPEN` = 15, `YL_RAID_HOURS` = "10-24", `YL_RAIDS=off` to disable. Region = where most
  raid-capable players are (decision 7). Skip when nobody is online.
- **Raid record** (memory + `data/raids.json` history): `{id, region, game, species, form, shiny,
  level_rule, spot {map, x, y, name}, opens, closes, pool, pool_max, bars_per_player, phase
  open|down|over, participants {uid: {name, joined, dealt, state fighting|out|caught|missed|left}}}`.
- **Pool**: starts at 0; each `raid_join` adds `bars_per_player * 1000`. `dealt` is a running total per
  player (resends are harmless: the server takes the difference). Hits allowed only from a joined player
  in phase open, capped (<= 1000 per message, <= 1 bar per 2 s per player). Pool 0 → phase down →
  `raid_down` to everyone; joins closed.
- **Anti-cheat (light, friends only)**: caps above, region/game must match the raid, no joins from inside
  a challenge, damage log per player in the history. A modified client can always fake it; not worth more.
- **Busy**: a fighting participant counts as busy for trade/battle/challenge requests.

### Protocol sketch (SPEC.md first when building; additive, `welcome.features` += `"raids"`)

| dir | type | fields |
|---|---|---|
| S→C | `raid` | full summary (spawn, and in `welcome` while one is open): `id, region, game, species, form, shiny, spot {map, x, y, name}, closes, pool, pool_max, phase, participants [{uid, name, dealt, state}]` |
| S→C | `raid_state` | `id, pool, pool_max, phase, participants` (throttled ≤ 2/s) |
| C→S | `raid_join` | `id, level, max_hp` → `raid_joined {id, pool, pool_max}` or `raid_refused {id, reason}` |
| C→S | `raid_hit` | `id, dealt` (running total, milli-bars) |
| S→C | `raid_down` | `id` - catch phase for everyone still fighting |
| C→S | `raid_result` | `id, outcome: caught \| missed \| blackout \| left` |
| S→C | `raid_over` | `id, reason: down \| time, caught [names]` + a system chat line |

Clients only send these when the server lists `raids`; older clients never see a den.
Unknown/finished raid ids are ignored silently (CLAUDE.md: no red errors for stale messages).

## A.8 Client

- `raids.py` (pure, unit-tested): pool ↔ segment maths, phase machine (idle → offered → starting →
  fight → catch → done), timer, auto-pick policy, message handling.
- Per game: `raid_gb.py` (Yellow + Crystal hooks through PyBoy) and `raid_pt.py` (EpilogueCaller +
  overlay-16 hooks with code-signature checks), same method names on the adapter (`start_raid_battle`,
  `boss_hp`/`set_boss`, `waiting()`, `block_balls`, `force_catch`, `flee`).
- UI: sidebar raid card, banner over the game in the battle (group HP, bars, timer ring, players),
  den overlay + edge arrow, toasts/sound.
- Resume: a forced snapshot right before the raid battle; no snapshot while a raid battle runs (like
  `exp_share.busy()`); closing = leaving. Fast-forward off in raid battles.
- Gating: `App.full`, not in a challenge/trade/battle, the raid's game = the current game, not in
  Safari Zone / Bug-Catching Contest / Great Marsh / with a walking partner.

## A.9 Build order (suggestion)

1. Server scheduler + protocol + tests; client card + chat notice (no battles yet) - lets you see raids.
2. Yellow raids end to end (lowest risk), e2e script with two clients.
3. Crystal.
4. Platinum (call injection + hooks; the prototype code is the starting point).
5. Spot generators for Johto/Sinnoh, reachability table, arrow, polish (sounds, leaderboard).

## A.10 Running the prototypes

Needs `dev/states/` (ROMs + states, see dev/README.md) and the pret checkouts in /home/claude
(`pokeyellow`, `pokecrystal`, `pokeplatinum`) + `/home/claude/pret-syms` (or `YL_SYMS`, `PRET_YELLOW`).
```
python dev/raids/proto/yellow_raid.py      # ~1 min,  ALL PASS
python dev/raids/proto/crystal_raid.py     # ~2 min,  ALL PASS
python dev/raids/proto/pt_raid.py          # Platinum, ~3 min (catch | flee | margin)
python dev/raids/proto/den_draw.py         # screenshots to /tmp/yl-raid-proto (keep out of git)
python dev/raids/proto/yellow_spots.py     # Kanto spot list (--json out.json)
python dev/raids/proto/pt_find.py          # how the Platinum addresses were found
```

---------------------------------------------------------------------------------------------------

## Ilias's decisions (2026-10-27) — these override the recommendations in section 4
1. **Who catches:** everyone in the group still in the battle when the group HP runs out gets a catch attempt.
   **Catch chance = that player's share of the total damage** (did 100 % → guaranteed; did 50 % → 50 %).
2. **Not guaranteed** — see 1 (one attempt each; on a miss the boss runs away in that player's game).
3. **Boss level:** mythicals are scaled per player (strongest + 5, 20–70); **legendaries have a fixed, proper level**
   like their in-game encounters (e.g. Dialga/Palkia-class 60–70, birds/beasts ~50, Latis ~40–50 — pick per species
   from their canonical encounter levels across the games).
4. Caught level = the boss's level.
5. Group HP: 2 bars per player who joins.
6. Timer: repeats the last move (else strongest); 10 s; fast-forward off in raid battles.
7. **Every 2 hours, in a random region** (skip when nobody is online).
8. **Shiny bosses: 1/20** (like Pokémon GO); a shiny raid catch counts for the Shiny hunter badge.
9. After a blackout you're out of that raid.
10. **Add Darkrai, Shaymin, Arceus and the Regis (+ Regigigas) to the Sinnoh raid list** too; second-chance raids
    for one-per-save legendaries in Kanto/Johto: yes.
11. Caught Pokémon gets its normal moves for its level.
12. Den: our own drawn stone tower (Hallowed Tower style), same in all games.
