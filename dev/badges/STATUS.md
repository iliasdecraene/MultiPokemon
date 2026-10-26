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
