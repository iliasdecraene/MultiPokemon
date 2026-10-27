# Complete Sinnoh — the plan

**Version 2** — updated after Ilias's review: starters before the League, swarms change every 2 hours, the
Pokédex area map shows the new spots, legendaries from other regions come with the raids later.

**What this is.** Normal Pokémon Platinum stays normal Platinum (same story, trainers, difficulty, trade
evolutions), but every Pokémon family can be found in Sinnoh in single player. This plan lists every Pokémon
we add to the wild, where and how often, and why it fits there. Any row can still be changed.

How it works for players: YellowLink makes a modified copy of each player's own Platinum file in its data folder
(the original is never touched) where only the wild-Pokémon lists are changed. Everyone in the group plays with the
same lists, so trades, badges and Soul Link stay fair. Saves keep working; only future encounters change.

## The rules I followed

1. **Early game unchanged.** Routes 201–204, Lake Verity, Twinleaf, Oreburgh Gate/Mine, Ravaged Path, Valley
   Windworks and Route 205 south keep exactly their vanilla Pokémon. Later areas keep all their Pokémon too: we only
   take over a "spare" slot (a Pokémon that appears more than once in the same list, or that is just as easy to find
   nearby at the same point of the story).
2. **Habitat fits.** Bugs in Eterna Forest, cave dwellers in caves, desert Pokémon on the desert route, water
   Pokémon by surfing or fishing, the moon rock at night and the sun rock by day, fire starters by fire and rock.
   Where Nintendo already placed a Pokémon somewhere in Sinnoh (the GBA-cartridge slots, Diamond/Pearl's lists — the
   Platinum ROM still contains both), we use that same place.
3. **Levels like the area.** Every new Pokémon gets the level (range) of the slot it takes over, so nothing is
   suddenly stronger or weaker than its neighbours.
4. **Rarity fits strength.** Normal Pokémon 5–10 %. All 12 starters (Kanto, Johto, Hoenn and the two Sinnoh ones
   you didn't pick) are rare (5 %) and live in the **middle of the game** (from about the 2nd badge on, before the
   League — not on the first routes). No legendary or mythical goes into a wild list.
5. **Base forms only** (Caterpie, not Butterfree). Evolve or breed for the rest.
6. **Nothing lost.** A script checks that every Pokémon that was catchable before is still catchable in the same
   way (or easier) and no later in the story than before.
7. **Only the wild lists change** (plus the Pokédex area map, so it shows the new spots) — no catch rates, stats,
   trainers or story. The four "Mystery Gift" key items are handed out by YellowLink at the right moment, then the
   game's own events run (see Special events).

## Audit: what normal US Platinum offers today (by evolution family)

Platinum knows 493 Pokémon = **247 evolution families** (Phione counted apart from Manaphy). Checked against pret's
Platinum source and the real ROM (all 183 wild lists identical); gifts, static Pokémon and events checked in the
game's scripts.

| How you can get the family (best way) | vanilla | with this plan |
|---|---:|---:|
| Wild, normal (grass, caves, surfing, fishing, any time of day) | 87 | **120** |
| Wild, special only (time of day, swarm, Poké Radar, Great Marsh / Trophy Garden daily, honey trees, Feebas) | 76 | 80 |
| Gift / egg / in-game trade / fossil / static / roaming legendary | 28 | 23 (starters and fossils now also wild) |
| Mystery-Gift event only (Darkrai, Shaymin, Arceus) | 3 | 3 (we unlock them) |
| GBA cartridge slot only (impossible here, no GBA slot) | 17 | **0** |
| Not obtainable at all | 30 | **15** (11 other-region legendaries → raids later; Regis + Regigigas → opened by YellowLink, see below) |
| Mythical, left for the YellowLink raid (Mew, Celebi, Jirachi, Deoxys, Manaphy, Phione) | 6 | 6 |

Not obtainable in vanilla single-player (30): Bulbasaur, Charmander, Squirtle, Chikorita, Cyndaquil, Totodile,
Treecko, Torchic, Mudkip, **Murkrow, Misdreavus, Glameow, Stunky** (all four are in the Sinnoh Pokédex!),
Trapinch, Clamperl, Mewtwo, Raikou, Entei, Suicune, Lugia, Ho-Oh, Latias, Latios, Kyogre, Groudon, Rayquaza,
Regirock, Regice, Registeel, Regigigas. Plus two "one of three / one of two" cases: you only get **one starter**,
and the Underground gives **either** Cranidos (odd Trainer ID) **or** Shieldon (even Trainer ID), never both.
→ Today nobody can complete the Sinnoh Pokédex badge (209) alone. With this plan everyone can.

Families that exist only through special mechanisms (kept as Nintendo made them):
- **Swarms** (post-game, 22 — in YellowLink the swarm changes **every 2 hours**, the same for everyone, instead of
  once a day): Farfetch'd, Doduo, Drowzee, Krabby, Voltorb, Cubone,
  Pinsir, Natu, Dunsparce, Snubbull, Corsola, Delibird, Phanpy, Larvitar, Zigzagoon, Slakoth, Makuhita, Skitty,
  Electrike, Spoink, Spinda, Beldum.
- **Poké Radar** (post-game, 24): Nidoran♀, Nidoran♂, Venonat, Mankey, Slowpoke, Grimer, Tauros, Sentret, Mareep,
  Hoppip, Sunkern, Stantler, Smeargle, Tyrogue, Miltank, Poochyena, Taillow, Nincada, Whismur, Aron, Torkoal,
  Baltoy, Wynaut, Bagon.
- **Great Marsh daily** (8): Paras, Exeggcute, Kangaskhan, Shroomish, Gulpin, Kecleon (these six after the National
  Dex), Skorupi, Carnivine. **Trophy Garden daily** (post-game, 6): Meowth, Ditto, Igglybuff, Plusle, Minun,
  Castform. **Honey trees** (6): Aipom, Heracross, Burmy, Combee, Cherubi, Munchlax. **Feebas** tiles (Mt. Coronet).
- **Only at one time of day** (9): Hoothoot, Ledyba, Spinarak, Clefairy/Cleffa, Shuppet, Tropius, Snorunt,
  Kricketot, Chatot.
- **Gifts etc.** (vanilla, unchanged): Eevee (Bebe), Porygon (Veilstone), Togepi egg (Cynthia), Riolu egg (Riley),
  fossils, in-game trades (Abra, Chatot, Haunter, Magikarp), Drifloon (Fridays), Rotom (Old Chateau TV at night),
  Spiritomb, the lake trio, Dialga, Palkia, Giratina, Heatran, Cresselia, roaming Articuno/Zapdos/Moltres.

## The additions (one row per family)

Rarity = chance per encounter in that spot. "Night"/"day" = only at that time (the game's own clock slots).

### Sinnoh Pokédex Pokémon that Platinum forgot

| Family | Where | How | Levels | Rarity | Why it fits |
|---|---|---|---|---|---|
| Murkrow | Eterna Forest | grass, **night** | 10 | 10 % | Dark crow that comes out at night in the dark forest next to the haunted Old Chateau. In Diamond it lives exactly here (ROM's Diamond list). |
| Misdreavus | Eterna Forest | grass, **night** | 12 | 10 % | Ghost of the same forest at night; Pearl's spot. |
| Stunky | Route 206 (Cycling Road) / Route 214 | grass | 18 / 24 | 10 % / 9 % | Diamond's own two spots for Stunky; rocky, dry routes. |
| Glameow | Route 218 | grass | 30–31 | 10 % | Pearl's own spot (the coast west of Jubilife). |
| Cranidos **and** Shieldon | Victory Road, post-game side rooms (Rock Climb) | cave | 48 | 5 % each | Fossil Pokémon living in ancient rock deep in Victory Road. The Underground still gives one of them early; this gives every save the other one after the Hall of Fame. |
| Turtwig, Chimchar, Piplup | see Starters | | | | the two you didn't choose |

### Pokémon that were only behind the GBA-cartridge slots (see the GBA section)

| Family | Where | How | Levels | Rarity | Why it fits |
|---|---|---|---|---|---|
| Caterpie | Eterna Forest | grass | 11 | 10 % | Forest bug; the game's own GBA spot (FireRed slot). |
| Weedle | Eterna Forest | grass | 12 | 10 % | Forest bug; the game's own GBA spot (LeafGreen slot). |
| Seedot | Eterna Forest / Route 210 south | grass | 13 / 21 | 4 % / 5 % | Acorn of the forest trees; the game's own GBA spots (Ruby). |
| Pineco | Route 210 south | grass | 21 | 5 % | Bagworm hanging in the trees of the foggy route; the game's own GBA spot (Emerald). |
| Lotad | Route 205 north | grass | 13–14 | 10 % | Lily pad Pokémon by the river below Eterna; the game's own GBA spot (Sapphire). |
| Teddiursa | Route 211 west / Route 211 east | grass | 14 / 29 | 10 % / 10 % | Little bear of the Mt. Coronet foothills; Platinum's GBA spot (211 west, Emerald) and Diamond/Pearl's (211 east). |
| Growlithe | Route 207 | grass | 8 | 5 % | The game put it on Routes 201/202 (FireRed) — but those stay vanilla, so it moves next door to the fire horses (Ponyta) on the dusty slope below Mt. Coronet. Rare-ish because Arcanine is strong. |
| Zangoose, Seviper | Route 208 | grass | 19–20 | 5 % each | The famous rivals share one meadow, as in Hoenn; the game's own GBA spot (Ruby/Sapphire). |
| Vulpix | Route 209 / Route 214 | grass | 19 / 24 | 5 % / 4 % | The game's own GBA spots (LeafGreen). |
| Ekans | Route 212 south | grass | 25 | 10 % | Snake of the rainy marshland; the game's own GBA spot (FireRed). |
| Sandshrew | Wayward Cave 1F | cave | 19–20 | 10 % | Digs in the sandy cave; the game's own GBA spot (LeafGreen). |
| Sableye, Mawile | Iron Island B1F–B3F (5 rooms) | cave | 30–32 | 5 % each | Gem- and iron-eating cave Pokémon in the iron mine; the game's own GBA spots (Sapphire / Ruby). |
| Solrock | Mt. Coronet 2F and 3F | cave, **day** | 36 | 10 % | The sun meteorite by day … (the game's GBA spot is Mt. Coronet, Ruby slot) |
| Lunatone | Mt. Coronet 2F and 3F | cave, **night** | 37 | 10 % | … and the moon meteorite at night, in the mountain where the world began. |
| Shuckle | Route 224 (post-game) | grass | 50 | 4 % | Hides under coastal rocks; the game's own GBA spot (Emerald). |

### Starters (middle of the game, all 5 %)

Each starter takes over one "spare" 5 % slot in a place that fits, after the first two gyms and before the League.
Levels are those of the area (higher than the usual level 5, like any wild Pokémon there).

| Family | Where | How | Levels | When reachable | Why it fits |
|---|---|---|---|---|---|
| Cyndaquil | Route 206 (under Cycling Road) | grass | 17 | after badge 2 | Dry, rocky route of the fire horses (Ponyta). |
| Chikorita | Route 209 | grass | 19 | after badge 2 | Gentle meadows with Roselia and Ralts. |
| Treecko | Route 215 | grass | 21 | after badge 3 | Rainy, wooded route. |
| Bulbasaur | Route 212 north | grass | 23 | after badge 3 | Lush, rainy grassland by the Pokémon Mansion. |
| Squirtle | Route 212 north | **surfing** | 20–30 | with Surf | Its ponds. |
| Totodile | Route 208 | **surfing** | 20–30 | with Surf | River below Hearthome. |
| Turtwig | Valor Lakefront | grass | 26 | after badge 4 | Turtwig lives along lakes — the shore of Lake Valor. |
| Charmander, Torchic | Fuego Ironworks (outside) | grass | 28 / 29 | with Surf (from Route 205) | Next to the ironworks furnace, with Magmar. |
| Chimchar | Route 211 east | grass | 28 | after badge 5 | Rocky mountain path of Mt. Coronet. |
| Mudkip | Lake Valor | **surfing** | 20–30 | after badge 6 | Fresh water of the lake (the Great Marsh would fit too, but you can't surf in the Safari game). |
| Piplup | Lake Acuity | **surfing** | 20–30 | after badge 7 | Cold northern lake — Piplup lives on cold shores. |

### Other missing families

| Family | Where | How | Levels | Rarity | Why it fits |
|---|---|---|---|---|---|
| Trapinch | Route 228 (post-game desert) | grass | 49 | 10 % | The sandstorm desert; Diamond/Pearl had Trapinch/Vibrava here (Poké Radar). |
| Clamperl | Route 219 / Route 221 | **Good Rod** / **Super Rod** | 20–30 / 35–45 | 4 % / 15 % | Sea bed south of Sandgem; Diamond/Pearl fished it on these routes. |

## What gets rarer (every changed area, before → after)

Only these 32 areas change (77 slot changes). Everything not listed is exactly vanilla.

- **Eterna Forest:** Budew 30→20 % (40→30 by day), Buneary 24→20 %, Bidoof 10→0 % (still everywhere around),
  at night Kricketot and Hoothoot 10→0 % (both still on Route 205 north etc.); new Caterpie 10, Weedle 10, Seedot 4,
  Murkrow 10 (night), Misdreavus 10 (night).
- **Route 205 north:** Bidoof 30→20 %; Lotad 10 %.
- **Route 206:** Geodude 30→20 %, Machop 20→15 %; Stunky 10, Cyndaquil 5 %.
- **Route 207:** Machop 35→30 %; Growlithe 5 %.
- **Route 208:** Bibarel 20→10 %; Zangoose 5, Seviper 5 %; surfing Golduck 10→5 %, Totodile 5 %.
- **Route 209:** Roselia 25→20 %, Ralts 20→15 %; Vulpix 5, Chikorita 5 %.
- **Route 210 south:** Ponyta 25→20 %, Roselia 15→10 %; Pineco 5, Seedot 5 %.
- **Route 211 west:** Machop 15→5 %; Teddiursa 10 %. **Route 211 east:** Machoke 15→5 %, Bronzor 10→5 %;
  Teddiursa 10, Chimchar 5 %.
- **Route 212 north:** Kirlia 20→15 %; Bulbasaur 5 %; surfing Golduck 10→5 %, Squirtle 5 %.
- **Route 212 south:** Shellos 45→35 %; Ekans 10 %. **Wayward Cave 1F:** Geodude 40→30 %; Sandshrew 10 %.
- **Route 214:** Graveler 35→30 %, Rhyhorn 30→26 %, Houndour 15→11 %; Stunky 9, Vulpix 4 %.
- **Route 215:** Marill 25→20 %; Treecko 5 %. **Valor Lakefront:** Bibarel 30→25 %; Turtwig 5 %.
- **Fuego Ironworks:** Magnemite 30→20 %; Charmander 5, Torchic 5 %.
- **Route 218:** Floatzel 30→20 %; Glameow 10 %.
- **Iron Island B1F–B3F (5 rooms):** Graveler −10 % each; Sableye 5, Mawile 5 %.
- **Mt. Coronet 2F, 3F:** by day Graveler 30→20 % (Solrock 10 %), at night Golbat 15→5 % (Lunatone 10 %).
- **Lake Valor surfing:** Golduck 10→5 %; Mudkip 5 %. **Lake Acuity surfing:** Golduck 10→5 %; Piplup 5 %.
- **Route 219 Good Rod:** Lumineon 5→1 %; Clamperl 4 %. **Route 221 Super Rod:** Gyarados 55→40 %; Clamperl 15 %.
- **Victory Road post-game rooms 1 and 3:** Steelix 5→0 % (still in the other Victory Road rooms); Shieldon / Cranidos 5 %.
- **Route 224 (post-game):** Dustox 5→1 %; Shuckle 4 %. **Route 228 (post-game):** Dugtrio 30→20 %; Trapinch 10 %.

## The Pokédex "Area" map

The Pokédex's area screen has its own file, which Nintendo builds from the same wild lists. YellowLink now updates it
too, exactly the way Nintendo's own build would: every new spot appears (with its time of day — e.g. Murkrow in
Eterna Forest only on the night map, Solrock only by day), and the 5 spots where a Pokémon no longer lives disappear
(Bidoof, and at night Kricketot and Hoothoot, in Eterna Forest). That is 123 small changes to the area lists.
Swarms, Poké Radar and the GBA slots work as before on the area screen.

## The GBA dual-slot Pokémon

On a real Nintendo DS, a Game Boy Advance game in the second slot (Ruby, Sapphire, Emerald, FireRed, LeafGreen) adds
two extra Pokémon to many areas after the National Dex. Nobody can insert a cartridge in YellowLink, so those 17
families were impossible. Decision: **move each one into a normal slot of (one of) the same maps Nintendo chose**, so
they live where the game designers wanted them — just without the cartridge and from the start of that area
instead of only after the National Dex. The one exception is Growlithe (its only spots are Routes 201/202, which we
keep vanilla) → Route 207 next door. The unused GBA slots themselves are left as they are (harmless).

## Legendaries from other regions and mythicals → raids (later)

Mewtwo, Lugia, Ho-Oh, Raikou, Entei, Suicune, Latias, Latios, Kyogre, Groudon, Rayquaza and the mythicals (Mew,
Celebi, Jirachi, Deoxys, Manaphy, Phione) stay out of the wild lists: they will come with the YellowLink **raids**
feature later. (Mewtwo, Lugia, Ho-Oh and the three dogs can also already come from Yellow/Crystal through
YellowLink's PC.) Articuno, Zapdos and Moltres already roam Sinnoh after the National Dex (Oak in Eterna).

**Regirock, Regice, Registeel, Regigigas:** in normal Platinum they lock each other — the three Regi statues
(Rock Peak Ruins, Iceberg Ruins, Iron Ruins) only open with an *event* Regigigas in the party, and the Snowpoint
Temple Regigigas only wakes up with all three Regis. YellowLink opens this loop (the ruins work without the event
Regigigas, or a character gives one), so after the Hall of Fame all four can be caught through the game's own
ruins and temple.

## Special events (Mystery-Gift content already inside Platinum)

Platinum's scripts check exactly four distribution events (a key item in the bag + a "magic number" the real
Mystery Gift writes). YellowLink gives them at a safe moment with a short message; after that the game's own story
runs. The game itself also requires the Hall of Fame and the National Dex for the first three.

| Event | Key item | What it unlocks (game's own script) | Game's own requirements | Proposed moment |
|---|---|---|---|---|
| Secret Key | Secret Key | Rotom's room in the Galactic building in Eterna: Rotom's five appliance forms | Galactic left the Eterna building | When you **catch your first Rotom** (Old Chateau TV, at night) or get the National Dex, whichever is first |
| Oak's Letter | Oak's Letter | Route 224: Oak and the tablet → Seabreak Path → **Shaymin** (lv 30) in Flower Paradise | Hall of Fame + National Dex | When you get the **National Dex** |
| Member Card | Member Card | Sleep in the Canalave Harbor Inn → Newmoon Island → **Darkrai** (lv 50) | Hall of Fame + National Dex + the Lunar Wing quest (Cresselia, the sailor's son) | When you get the **National Dex** (Darkrai then waits for the Cresselia quest, as in the real event) |
| Azure Flute | Azure Flute | Play it at the top of Spear Pillar → Hall of Origin → **Arceus** (lv 80) | Hall of Fame + National Dex | National Dex **and** Dialga + Palkia caught — the finale of the Spear Pillar story |

There is no other Mystery-Gift-gated content in Platinum's scripts. Other real-life distributions were Pokémon sent
as data (for example the Manaphy egg from Pokémon Ranger) — mythicals, left for the raid.

## Decisions (Ilias, version 2)

1. Starters: before the League (mid-game), 5 %, early game unchanged. ✔
2. Swarms change every 2 hours for everyone; swarm-only and Poké-Radar-only Pokémon stay as they are. ✔
3. The Pokédex area map shows the new spots. ✔
4. Other-region legendaries and mythicals: raids later. Regis/Regigigas: opened by YellowLink. ✔
5. Event moments as proposed above. ✔

## How it is checked

- `python dev/sinnoh/check_plan.py` — compares all 183 wild lists of pret with the real ROM, then proves with the
  edits applied (through the same code the ROM builder uses): every non-mythical family is obtainable (the raid
  legendaries and the Regis reported separately), the Sinnoh Pokédex can be completed, no Pokémon lost access (same
  way or easier, no later in the story), and the Pokédex area map: our copy of Nintendo's area builder gives exactly
  the ROM's file, and with our changes it shows every new spot with its time of day. `--audit` prints the per-family audit, `--changes` the
  before→after list per area.
- `client/tests/test_sinnoh_complete.py` — spec sanity: valid Pokémon / list / slot numbers, levels within the
  slot's own level band, rarity weights sum to 100 %, no legendary/mythical in wild lists, base forms only, early
  areas untouched, starters mid-game and ≤ 5 %, every added spot on the area map, the patchers change exactly the
  intended bytes.
- The machine-readable spec is `client/yellowlink/sinnoh_complete.py` (version 2: 77 slot changes, 123 area-map
  changes).
