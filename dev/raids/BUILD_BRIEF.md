# Raids — build brief (target 0.7.9)

Worktree `/home/claude/yl-raids` (branch `raids`, merged with main 0.7.8). Read `CLAUDE.md`, `MEMORY.md`, `SPEC.md`,
and **`dev/raids/DESIGN.md` fully — especially the last section "Ilias's decisions", which overrides section 4**, the
technical appendix (A.1–A.9) and the prototypes in `dev/raids/proto/` (they run on the real ROMs and are the starting
point; addresses there are verified). Agents don't commit; notes in `dev/raids/STATUS.md` (short, dated).

Disk space is tight on this machine (~1 GB free): Platinum harnesses copy ROMs into temp folders — use
`dev/platinum/ptrom.py`'s cleanup (it deletes ROM copies at exit), delete your own temp folders when a script ends,
and never leave 128 MB copies behind. Never run two `dev/sinnoh/verify.py` at once.
Platinum here = Complete Sinnoh by default for players (`YL_PT_ROM=sinnoh` in harnesses; vanilla must keep working).

## Ilias's decisions (summary — DESIGN.md has the full list)
Co-op; every 2 h a raid in a RANDOM region (skip when nobody online), 15 min, random reachable spot, den = our own
drawn stone tower (Hallowed Tower style) + edge-of-screen arrow + place name; notice to everyone (chat + toast + sound);
open before Champion. Group HP = 2 bars per player who joins. Each player fights their own copy; the boss never faints
locally until the group HP is 0. 10-s move timer → repeats last move (else strongest); fast-forward off in raid
battles. When the group HP hits 0, everyone still in the battle gets ONE catch attempt with **catch chance = their share
of the total damage** (100 % → guaranteed, 50 % → 50 %); a miss → the boss flees in that game. Legendaries at a fixed
proper level (per species, from canonical encounters); mythicals scaled per player (strongest + 5, 20–70). Caught level
= boss level; normal moves for its level. Shiny boss 1/20 (counts for the Shiny hunter badge; it also fires the shiny
alert). Blackout → out of that raid. Sinnoh list incl. Darkrai/Shaymin/Arceus/Regis/Regigigas; Kanto/Johto pools per
DESIGN section 2 incl. second-chance one-per-save legendaries. No raids inside Soul Link runs (notice only).

## Split
- **`raid-net`** (server + protocol + client app/UI): SPEC.md section 12 first (protocol: raid_open/raid_update/raid_end,
  raid_join, raid_damage, raid_result..., `welcome.features += "raids"`), server scheduler (2 h, random region weighted
  to regions someone online can play; per server; deterministic boss pick from the pools; HP pool math in bars; damage
  accounting; per-player damage share; catch chance = share; 15-min lifetime; persistence across server restart; spam/
  sanity limits), client raid state machine (`client/yellowlink/raid.py`), sidebar raid card, notice (chat + toast + a
  short sound — reuse shiny.py's jingle approach with a different jingle), the 10-s timer ring UI, join confirmation,
  'busy' while raiding (no trade/battle requests), Soul Link isolation, resume/close mid-raid rules (DESIGN), badges/
  shiny integration. Owns: server/*, client/yellowlink/raid.py, app.py/panels.py/ui.py/net.py raid parts, SPEC section
  12, server tests, client tests, `dev/e2e/raid_flow.py` (local server + 2 clients, using raid-games' modules).
- **`raid-games`** (per-game battle integration, production modules from the prototypes): `client/yellowlink/raid_gb.py`
  (Yellow + Crystal) and `client/yellowlink/raid_pt.py` (Platinum, the call-injection technique) with ONE common
  interface (below); boss move sets + fixed levels per species table (`raid_data.py`), spot lists per region with a
  reachability level (Kanto generator exists; make Johto + Sinnoh — from map data/ROM, walkable outdoor tiles, not
  grass/water/doors/NPCs; reachability by badges/HMs/story), den drawing + collision + 'press A' interaction in each
  game, waiting-for-input detection + auto-pick per game, close the known gaps (DESIGN A.6: party menu / 'use next
  Pokémon?' after a faint, Perish Song / Destiny Bond on GB, walking into the den), shiny boss, caught Pokémon gets its
  normal moves (Platinum extra write), fateful flag for mythicals in Platinum. Owns: raid_gb.py, raid_pt.py, raid_data.py,
  spot data files (our own generated data only — no ROM bytes), adapter additions in game.py/crystal.py/platinum.py,
  `dev/raids/verify.py` on the real ROMs (each game: start, shared HP, timer auto-pick, blackout, catch with a given
  chance (force the RNG both ways), flee, den interaction), unit tests.

## Interface (agree here first, changes in STATUS.md)
`raid_<game>.RaidBattle(app_or_core, boss: dict)` where boss = {species (national dex), level, moves[4], shiny, fateful,
bars, region} with methods polled every frame by the app: `step() -> events` (list of dicts: {"type": "damage", "hp":
n, "of": max}, {"type": "need_input"}, {"type": "blackout"}, {"type": "fled"}, {"type": "caught", "key": pcdb key},
{"type": "missed"}, {"type": "ended"}), `set_pool(fraction_left)` (sync local HP to the group pool), `group_down(catch_
chance: float)` (sleep at 1 HP, allow one ball, the catch succeeds with that probability), `flee()` (time over),
`auto_pick()` (timer expired), `can_join() -> (ok, why)`, `start()`; plus `raid_<game>.Den(spot)` for drawing/
collision/interaction (`near()`, `pressed_a()`), and `raid_data.boss_for(species, player_strongest_level, region)`.
