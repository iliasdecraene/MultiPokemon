# Badges — shared brief (0.7.5)

Repo/worktree: `/home/claude/yl-badges` (branch `badges`, based on the 0.7.4 Soul Link branch). Read `CLAUDE.md`,
`MEMORY.md`, `SPEC.md` first. `dev/states/` is a symlink to the main checkout's ROMs + states (never commit them).
Agents do not `git commit`/push — the coordinator commits. Append short dated notes to `dev/badges/STATUS.md`.
Hard rules from CLAUDE.md apply (every RAM/ROM address verified against pret AND on the real ROM; game reads
only behind `App.full`; no Nintendo assets — all badge art is drawn by our own code; protocol in SPEC.md first).

## What Ilias asked for (his decisions)
Per region (Kanto = Yellow, Johto = Crystal, Sinnoh = Platinum), earned in the player's **main game** only:
1. **Champion** — beat that region's Champion (entered the Hall of Fame) in that game.
2. **Pokédex** — CAUGHT (owned flag) the whole regional dex, like the in-game diplomas: Kanto 150 (all but Mew),
   Johto: all 251 except Mew and Celebi (249), Sinnoh: the 210 of the Sinnoh dex.
3. **Shiny hunter** — caught at least one shiny in that game. Tiers by count: 1, 10, 25, 50, 100 (Ilias said
   upgrades come "later", but we track the count now and draw the tier). Count = distinct shiny mons the player
   caught in that game (their own OT name + TID, origin = that game), remembered for good once seen (in the
   party, the game's boxes or the YellowLink PC store), so trading/releasing it later doesn't lower the count.
   **Yellow:** Gen 1 has no shinies; a Yellow mon whose DVs would make it shiny in Gen 2 (Def 10, Spd 10, Spc 10,
   Atk in {2,3,6,7,10,11,14,15}) counts as shiny everywhere in YellowLink (PC panel, trades, badges), and a wild
   one in a Yellow battle gets a small on-screen indicator (drawn by us, e.g. a sparkle + "Shiny!" next to the
   enemy) so the player knows to catch it.
Soul Link (separate from the main-game badges; finishing a run never gives a main-game badge):
4. **Soul Link badge per region** — completing a run = the run's player enters the Hall of Fame. **Gold** = the
   run was played with Exp Share All OFF, **Silver** = with it ON (the run rule `exp_share`). Each player gets it
   when THEY enter the Hall of Fame in the run. Gold outranks silver (if you have gold, show gold).

Display:
- **Regions panel** (panels.py): under each region, a row of badge sockets (Champion, Pokédex, Shiny hunter with
  tier, Soul Link). Not earned = an empty socket. Hover/tooltip or a short label says what it is and how to earn it.
- **Multiplayer tab**: clicking another player shows the badges they collected (same sockets per region).
- Badges are earned once and kept (starting the game over doesn't remove them). Retroactive: a save that already
  has the Hall of Fame / a full dex / shinies earns them on first detection.

## Split
- **Game agent** (`badges-game`): per-game detection, verified on the ROMs. Owns `client/yellowlink/badges.py`
  (pure definitions + rules: region list, dex lists, tier math, gen-1 shiny DV rule, merge of earned state),
  new adapter functions in `game.py` / `crystal.py` / `platinum.py` (+ `pk4.py` helpers if needed), unit tests
  `client/tests/test_badges.py`, dev script `dev/badges/verify.py` (each detection on real states: a save with and
  without the Hall of Fame / dex / shinies — make states by writing RAM where none exist), the Soul Link run
  "entered the Hall of Fame" detection helper for the referee view (challenge_view.py: only add a small method).
- **App agent** (`badges-app`): protocol (SPEC.md), server storage + validation (`server/yellowlink_server/`,
  per-server data, per uid), client sync (`app.py`, `net.py`), UI (`panels.py`, `ui.py`; badge art drawn in code),
  the Yellow in-battle shiny indicator drawing, shiny marks in the PC/trade views, Soul Link badge award flow
  (`soullink.py` / `challenge.py`: a ledger event when a player enters the Hall of Fame in a run), server tests,
  client tests for the UI/sync logic, an e2e `dev/e2e/badges_flow.py` (two clients + local server: earn, see each
  other's badges).

## Interface between the two (agree here; changes go in STATUS.md)
Adapter (`self.G` module) function: `progress(mem) -> dict | None` (None = can't read right now), with
`{"champion": bool, "dex_caught": int, "dex_total": int, "dex_complete": bool, "shinies": [key, ...]}` where a key
is the same stable mon key the PC database uses (so the same mon is never counted twice), shinies = the player's
own shiny catches currently visible in the game (party + boxes + Day Care). `badges.py` keeps the earned state
(`{"<region>": {"champion": ts, "dex": ts, "shinies": [keys], "soullink": "gold"|"silver"}}`) and merges new
progress into it (never removes). Yellow wild-battle shiny: `game.wild_is_shiny(mem) -> bool | None`.
