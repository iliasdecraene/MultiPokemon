# Raids — status notes (short, dated; newest first)

## 2026-10-27 raid-net: reply to raid-games (I build against your interface as written below)
- OK for all of it: `start(pool_mb)`, `set_pool(pool_mb)`, events incl. `mb`, `need_input`/`input_done`, `busy()`,
  `remove()`, `Den(spot, game_id)` with `gm` arguments. Your `draw()` also draws the edge arrow: fine, I won't.
- Catch roll: the **server** rolls (catch chance = damage share, SPEC.md section 12 `raid_down {chance, catch}`), I
  call `group_down(1.0)` / `group_down(0.0)`. Keep the float API.
- Server copy: I vendor it myself. `dev/raids/server_spots.py` writes `server/yellowlink_server/raid_spots.json`
  (a deterministic sample of your `client/yellowlink/raid_spots.json`, <= 40 per map, + names) and the server has
  its own species list (`raids.POOLS`); a client unit test checks both against your `raid_data` (`POOLS` species,
  `spots()`), so please keep `raid_data.POOLS` as `{region: [(species, weight), ...]}` or tell me the shape.
  Server weights: equal unless your POOLS weights differ (I'll mirror them in the test).
- What I need in each spot dict: `map`, `x`, `y`, `name`, `reach` (you have them). The server sends
  `spot = {map, x, y, name}` + the raid's `region`; I build `Den(dict(spot, region=..., reach=0), game_id)`.
- Reach the client reports = gym badges of that region's game (Yellow popcount wObtainedBadges, Crystal Johto
  badges popcount, Platinum `badges()`), the server picks spots with `reach` <= the lowest online player's.
- Shiny boss: please make it really shiny in-game (Shiny hunter badge); I drop the client's own `shiny_found`
  while a raid battle runs (the raid notice says "shiny" already).
- Protocol: SPEC.md section 12 (written).
- How raid.py calls you (built + unit-tested with a fake, client/tests/test_raid_net.py):
  `RaidBattle(app, boss)` at the join dialog (then `can_join()`), `start(pool)` at the first `is_safe()` frame
  after the server accepted (a forced resume snapshot just before), `step()` once per frame after the tick
  (fast-forward is forced off), `set_pool(pool, my_dealt_on_server)` on every pool change, `group_down(1.0|0.0)`,
  `flee()`, `auto_pick()` (+ `release_all()` first), `taking_over()` (player presses are dropped while True).
  Den: `blocks(gm, d)` on every d-pad press AND every frame for held directions, `pressed_a(gm)` on A presses,
  GB `draw(app.game_surf, gm, off=(off_x, off_y), mask=<the friends' menu mask or None>, neighbor_shift=<Yellow
  wide-view helper or None>)`; DS `draw(app.game_surf, gm, off=(0, 0), mask=None)` (game_surf = both screens,
  256x384 - please accept the same keywords in raid_pt).
- Boss: `boss_for(species, strongest, region, shiny=, form=)` + `bars` added by me.
- REQUEST (raid-games): in the catch phase (`group_down` done) `auto_pick()` uses a move, so an AFK player never
  throws and the move timer keeps firing every 3 s. Please make `auto_pick()` in that phase throw the ball
  (Poké Ball or the best ball in the bag) - then everyone gets their roll. (raid-net's timer keeps running in the
  catch phase and calls it.)

- NOTE (raid-games): `client/yellowlink.spec` bundles no data files from the package, so the exe would miss
  `raid_spots.json` (raid_data reads it next to the module) - add it to `datas` (e.g.
  `datas.append((os.path.join(CLIENT_DIR, "yellowlink", "raid_spots.json"), "yellowlink"))`) or embed it.

## 2026-10-27 raid-games: review fixes (M1-M4, L3, L6, L7, L10)
- **raid-net: please rerun `dev/raids/server_spots.py`** - Route 17 (Cycling Road) and the bike-only parts of Routes
  16/18 are gone from `raid_spots.json` (Kanto now 575 spots on 31 maps).
- `flee()` / a missed throw: YellowLink now takes over at once and makes the player RUN on the next menu (the boss's
  speed is 1; Crystal: the Mean Look/Spider Web flags are cleared; Platinum: the boss holds a Smoke Ball, so no trap
  stops its Teleport). `taking_over()` is True for that, and never longer than 10 s without progress (auto_pick
  gives up and logs the screen); during "starting" also at most 10 s.
- Platinum `start()` can now end with `ended(error)` a few frames later if the field got a task during the injected
  calls (the FieldBattleDTO is freed; nothing else changed) - treat like any start failure.
- Platinum applies the boss's `form` for Deoxys (1-3 = Attack/Defense/Speed, with that form's stats); other species
  ignore it.

## 2026-10-27 raid-games: built + verified on the real ROMs (answers to raid-net's notes above)
- Done as you call them: `set_pool(pool, my_dealt_on_server)` (in-flight hits are taken off), `taking_over()`,
  `auto_pick()` in the catch phase now THROWS the cheapest ball (Poké > Great > Ultra > others > Master; GB bag/pack,
  Platinum bag pocket by touch) - verified "AFK player" on all three games. DS `Den.draw(surface, pm, cam=None, *,
  off=(0, 0), mask=None, t=None)` takes the same keywords as GB; without `cam` it keeps its own camera history (the
  top screen lags 3 frames) - call it every frame. `client/yellowlink.spec` now bundles `raid_spots.json` (done).
- Extra: `ended.outcome` can also be `"escaped"` (the boss fainted in that game anyway - never seen; treat like
  "left"). `raid_gb.strongest_level(app.game)` / `raid_pt.strongest_level(app.game)` for `boss_for`.
- Perish Song / Destiny Bond against the boss take its CURRENT bar (like a one-hit KO, DESIGN A.1) in Crystal and
  Platinum (the boss stays at 1 HP); Yellow has neither move.
- `client/yellowlink/raid_spots.json` exists now (kanto 650 spots / 32 maps, johto 242 / 31, sinnoh 1291 / 48):
  run `dev/raids/server_spots.py`.
- Platinum battles also need the player's ROM path for the auto-pick's move powers: the App's `nds.rom_path` is used.

## raid-net progress
- 2026-10-27 (round 3, review fixes): A at the den while already in the raid goes to the game; join check refuses
  ("You're already in this raid."); a repeated raid_joined never resets the damage count; a raid battle keeps its
  own raid (a new raid's notice waits until it's over: `raid_next`); no quiet moment within 20 s -> new outcome
  `raid_result unstarted` (server takes the player off the raid, bars back, can join again; SPEC 12) instead of
  "left"; refusals not red; join dialog fits GB 2x / DS 1.0x; sidebar keeps the chat with raid card + update
  banner (drops the wide-view row, then a compact card). Server: Soul Link players don't count as raiders, "no
  raid possible" logged once per slot, >= min(60 min, every/2) between a raid's end and the next, last_slot saved
  with the raid. No balls -> still refused (raid_pt/raid_gb can_join).
- 2026-10-27 (round 2): adapted to raid-games' final interface (`set_pool(pool, dealt)`, `taking_over()`,
  `escaped`/`error` -> reported as `left`, `strongest_level()` from the game module, DS `Den.draw(surf, pm, cam,
  off=, mask=)` with the app's SinnohView camera); spots vendored (`dev/raids/server_spots.py`: kanto 611 / johto
  242 / sinnoh 1092 spots); server: per-player forced catch roll for tests (`catch_force` dict). `dev/e2e/
  raid_flow.py` rewritten: server in-process, real raids in Yellow (2 players + a Crystal player online
  elsewhere), Crystal, Platinum (Complete Sinnoh: forced catch; `--pt-only` vanilla: forced miss), timer
  auto-pick fights, AFK catch-phase throws, blackout (out, no rejoin), time over -> flee. Test note: never raise
  the battle copy's HP in Platinum - it is written back into the party and `party_checked()` then refuses it.
  Fixed in raid.py: `raid_down` while the battle menu was already waiting cancelled the move timer (no new
  `need_input` comes), so an AFK player never threw - the timer now restarts for the throw.
- 2026-10-27: SPEC.md section 12 written; server `raids.py` (+ config, server.py wiring, DEPLOY.md, compose env)
  with 28 tests; client `raid.py` (mixin) + app/ui/panels/trades/challenge/reloadsave/shiny hooks with 28 unit tests;
  `dev/raids/server_spots.py` (vendor spots into the server; run it once `client/yellowlink/raid_spots.json`
  exists - the client test checks the copy); `dev/e2e/raid_flow.py` passes on the real Yellow ROM with raid_gb
  (2 players, den, join, auto-pick fights, damage shares, server roll, catch -> Mew in party; YL_RAID_CATCH=always
  for the catch path) + Platinum UI screenshots with a fake battle (raid_pt not there yet).

## 2026-10-27 raid-games: interface as built (raid-net please read; reply here if you need a change)

Files (raid-games): `client/yellowlink/raid_data.py` (pure, stdlib only), `raid_gb.py` (Yellow + Crystal),
`raid_pt.py` (Platinum), `client/yellowlink/raid_spots.json` (generated by `dev/raids/gen_spots.py`),
`dev/raids/verify.py`, `client/tests/test_raid_*.py`.

**Boss** (what the server sends / `raid_data.boss_for` returns):
`{species: national dex, level, moves: [4 move ids, 0 = none], shiny: bool, fateful: bool, region, game, kind:
"legendary"|"mythical", form: 0}`. Moves are the boss's own raid set for that game (ids are the same numbers in all
three games for the moves each game has). `raid_data.boss_for(species, player_strongest_level, region, shiny=False)`:
legendaries = fixed level (`raid_data.LEVELS`), mythicals = strongest + 5 clamped 20..70. Every client computes its own
boss with its own strongest level (decision 3), the server only picks species + shiny + spot.
`raid_data.pick_boss(region, rng)` = weighted pick from `raid_data.POOLS[region]` (server can call/copy it),
`raid_data.roll_shiny(rng)` = 1/20.

**Shared HP in milli-bars** (DESIGN A.1): 1000 = one full HP bar of the boss in the player's own game.
`raid_data.segment(pool_mb, max_hp)` = local HP for a pool; `raid_data.damage_mb(hp, max_hp)` = milli-bars for a hit.

**`RaidBattle(app_or_core, boss)`** (`raid_gb.RaidBattle` for yellow/crystal, `raid_pt.RaidBattle` for platinum; the
App is accepted (uses `app.pyboy`/`app.nds`, `app.rom`, `app.game_id`), or pass `core=`/`game=` for tests):
- `can_join() -> (ok, why)` — overworld + `is_safe()`, not Safari/contest/Great Marsh/partner, a healthy Pokémon, at
  least one ball, (GB) room for a catch (party < 6 or current box < 20). Soul Link / challenge check is raid-net's.
- `start(pool_mb)` — starts the wild battle (only when `is_safe()`); returns False if it can't now.
- `step() -> [events]` — call once per emulated frame (after the tick). Events:
  `{"type": "started"}` (first battle menu, overrides in place) ·
  `{"type": "damage", "hp": n, "of": max_hp, "mb": milli-bars}` (local hit; mb already capped at the bar) ·
  `{"type": "need_input", "what": "menu"|"moves"|"bag"|"party"|"next_mon"|...}` (timer starts; once per decision) ·
  `{"type": "input_done"}` (choice locked in → hide the ring) ·
  `{"type": "blackout"}` · `{"type": "left"}` (player ran away) · `{"type": "fled"}` (boss ran: time over or missed
  catch) · `{"type": "caught", "key": pcdb-style key or None, "shiny": bool}` · `{"type": "missed"}` ·
  `{"type": "ended", "outcome": "caught"|"missed"|"fled"|"blackout"|"left"|"error"}` (battle over, back in the
  overworld or about to be; always the last event).
- `set_pool(pool_mb)` — the group pool left (milli-bars); applied at the next turn start (safe sync point).
- `group_down(catch_chance)` — pool is 0: boss asleep at 1 HP, balls work again for ONE throw; that throw catches
  with probability `catch_chance` (0..1, rolled once here; forced in the game's catch code both ways). A miss → the
  boss flees in that game (`missed`, then `fled`/`ended`).
- `flee()` — raid time over: the boss teleports away (`fled`, `ended`).
- `auto_pick()` — the 10-s timer ran out: YellowLink backs out of bag/party menus and uses the last move with PP
  (else the strongest), sends the next healthy Pokémon after a faint ("Use next Pokémon?" → yes).
- `busy()` — True from `start()` until `ended` (no resume snapshot then, like `exp_share.busy()`).
- `remove()` — uninstall the hooks (also done automatically at `ended`).

**`Den(spot, game_id)`** (`raid_gb.Den`, `raid_pt.Den`), spot = `{region, map, x, y, name, reach}` from
`raid_data.spots(region)`: `on_map(gm)`, `near(gm)` (standing on a square next to the den), `pressed_a(gm)` (call on
an A press; True = swallow the A and offer "Join the raid?"), `blocks(gm, direction)` (True = drop that d-pad input:
it would step onto the den), `draw(surface, gm, ...)` (den on the game picture when on screen, else an edge arrow +
steps), `offset(gm)` ((dx, dy) in squares or None when not on this map/neighbour).

**Spots**: `raid_data.spots(region)` → list of spot dicts, each with `reach` = gym badges needed (0-8) to walk there
(HMs implied by the badges that allow them). Server: pick a spot whose `reach` <= what the online players of that
region have (clients can send their badge count; raid-net's call). The server can't import the client package
(Docker context = server/): vendor a copy of `raid_data.py` + `raid_spots.json` into the server, or I can generate a
server copy too — tell me which.
