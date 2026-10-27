# "Complete Sinnoh" — shared brief

Worktree `/home/claude/yl-sinnoh` (branch `sinnoh-complete`, from main 0.7.6). Read `CLAUDE.md`, `MEMORY.md`, `SPEC.md`,
`dev/platinum/MEMORY_MAP.md` first. `dev/states/` is a symlink to the ROMs/states (gitignored; never commit ROMs, saves,
states or anything copied out of the ROM). pret decomp (read-only reference): `/home/claude/pokeplatinum`
(`res/field/encounters/*.json` = the wild tables, `generated/species.txt`, `res/pokemon/*/data.json`, scripts).
Agents don't commit; notes in `dev/sinnoh/STATUS.md`.

Reference only (paused, not shipping): branch `definitive-platinum` / worktree `/home/claude/yl-defplat` — its
`dev/defplat/STATUS.md` has useful findings: all wild tables are ONE file `/fielddata/encountdata/pl_enc_data.narc`
(183 tables x 424 bytes, fixed size → edits are in place, no resizing); game code at all our hook addresses is unaffected
by data edits; `platinum.py` variant-table idea (`select_rom`, `SUPPORTED_SHA1S`). Don't copy anything from Definitive
Platinum's data — Ilias chose to make our OWN version.

## Ilias's decision
Normal Platinum stays normal Platinum (difficulty, trainers, story, trade evolutions), plus:
1. **Every Pokémon family obtainable in Sinnoh, our own design, "fair and making sense"**: add the missing species to
   the wild encounter lists of normal Platinum. Today (counted from pret, by evolution family, 246 families):
   117 families have a member in the normal wild tables; 64 only via swarms / Poké Radar / the GBA-slot ("dual-slot")
   replacements; 65 have no wild member (about half are in-game gifts/static encounters: fossils (Underground),
   Porygon, Eevee?, Riolu egg, Rotom, Spiritomb, Drifloon (Fridays), lake trio, Dialga/Palkia/Giratina, Heatran,
   Cresselia, Regigigas, the Kanto birds roaming post-game ...). Re-check all of this carefully (Trophy Garden and
   Great Marsh daily lists, honey trees, gifts and static encounters in the scripts, Pal Park = needs a GBA cart = no).
2. **All special events active**: the Mystery-Gift-only key items/events that are in the game: Member Card (Darkrai,
   Newmoon Island), Oak's Letter (Shaymin, Seabreak Path), Azure Flute (Arceus, Hall of Origin) — and anything else
   Platinum has behind distribution flags (check pret: e.g. the Rotom room / appliances, the Regi/Regigigas path, any
   wonder-card-gated script). They should unlock at sensible story points (e.g. once the game's own requirement
   such as the National Dex is met), through the game's own scripts.
3. **Mythicals with no in-game event** (Mew, Celebi, Jirachi, Deoxys, Manaphy/Phione): NOT now — later a multiplayer
   "raid" feature. Leave them out (badges already exclude Manaphy from the Sinnoh dex).

## Design rules for the additions (agent `sinnoh-design`)
- Fair + sensible: habitat/type fits the place (water types on water routes / rods, cave dwellers in caves, forest bugs
  in Eterna Forest, ice in the north, fire near Stark Mountain, etc.), levels fit the area like the game's own tables,
  rarity fits strength (pseudo-legendary lines and starters are rare and/or post-game; early routes keep their early
  Pokémon). Prefer the game's own mechanisms where they already hold the species (time of day, swarm list, Poké
  Radar, Great Marsh / Trophy Garden) and decide what to do with the GBA dual-slot species (e.g. move them into normal
  slots of the same map, since nobody can insert a GBA cartridge here).
- Never make a vanilla species unobtainable (if a slot is replaced, the species must still be elsewhere with similar
  access). Don't change catch rates, stats or anything outside the wild tables + event unlocks.
- Base forms in the wild (middle stages only where the game itself does it, late areas). Split evolutions/babies are
  fine via evolving/breeding.
- Other-region legendaries (Mewtwo, Lugia, Ho-Oh, Raikou/Entei/Suicune, Latias/Latios, Kyogre/Groudon/Rayquaza, the
  Regis): propose options (e.g. via Yellow/Crystal for Gen 1/2 ones through YellowLink's PC; a Platinum mechanism like
  roaming post-game or the Regigigas ruins) — list them separately for Ilias to decide; don't put legendaries in normal
  wild tables without asking.
- Output: a reviewable plan `dev/sinnoh/PLAN.md` (one row per added family: species, where, how (slot/time/rod/radar),
  levels, rarity %, why) + the machine-readable spec `client/yellowlink/sinnoh_complete.py` (our own data only: map/
  table ids, slot kinds, species ids, levels; generated against pret names, checked against the ROM's table layout),
  plus a check script that proves: every non-mythical family has at least one way to get it in single-player Platinum
  with the edits (wild/gift/static/event), and no vanilla family lost access.

## Engine (agent `sinnoh-engine`)
- Apply the spec to the player's OWN vanilla Platinum at load: build a modified copy of the ROM in YellowLink's data
  folder (pl_enc_data.narc edited in place; nothing else changed), deterministic → one known SHA1 per spec version; the
  original is never modified. Verify: the original is the supported vanilla SHA1; the result differs only inside that
  NARC; the result's SHA1 equals the expected one for this spec version (store the expected SHA1 in code once
  computed). Code is untouched, so every vanilla feature/hook stays valid → treat the result like vanilla (same
  adapter, `App.full` True); make the variant table explicit.
- Everyone runs the same data (always on, versioned), so trades, badges and Soul Link areas stay fair; check Soul
  Link's area keys/encounter logic (platinum_areas.py) are unaffected or updated (e.g. new encounter kinds).
- Events: unlock the event key items / flags at the right moment at a safe frame through RAM writes the game itself
  would make (bag + any flags), verified on the ROM; then the game's own scripts run (play them on the ROM: get each
  legendary through its real event in a dev state).
- Existing saves keep working (only future encounters change). Resume snapshots from the plain ROM vs the modified
  ROM: decide + test (they share code; a savestate from vanilla loaded into the modified ROM must be safe, or the
  first run re-boots from the battery save).
- Tests: unit tests for the NARC editing + spec validation; dev/sinnoh/verify.py on the real ROM (encounters actually
  appear: force encounters on a few maps and check species/levels; each event plays through); keep the whole
  Platinum suite passing (verify.py, pt_pctest, pt_trade_e2e, pt_friends, expshare_pt, soullink_platinum,
  challenge_flow_platinum, soullink_tradeevo, badges verify, platinum_boot).
