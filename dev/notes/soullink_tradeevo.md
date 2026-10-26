# Trade evolutions in a Soul Link run (0.7.6)

Ilias's decision: in a Soul Link, trade evolutions are **always allowed** (no run rule). It works as a "trade and
trade back" with the run partner: the mon goes over and comes straight back, evolved. Protocol and rules: SPEC.md
section 7, "Trade evolutions in a run".

## Pieces
| where | what |
|---|---|
| `client/yellowlink/tradeevo.py` | `options(kind, mem, rom, sl)` (party mons that evolve by trade, with the reason when one can't), `evolve(kind, mem, rom, key)` (the RAM write), `TradeEvoMixin` (the flow, mixed into `App`) |
| `client/yellowlink/panels.py` | Soul Link panel: "Trade evolution" button -> picker (`_draw_tradeevo_picker`) |
| `client/yellowlink/app.py` | partner prompt (kind `tradeevo`), requester modal (mode `evo_wait`, Esc/Cancel), `server_features` from `welcome` |
| `client/yellowlink/challenge.py` | `trade_evo` in `library.challenge_rules`; `tradeevo_step()` from `chal_step()`; partner toast on the `evo` ledger event |
| `client/yellowlink/soullink.py` | `evo` events -> `state()[uid]["evo"]` (key -> new dex), used only by `pairs()` for display |
| `server/yellowlink_server/challenges.py` | `on_evo_request/respond/done`, one open `Evo` per run, timers, ledger `evo` written by the server |
| tests | `client/tests/test_tradeevo.py`, `server/tests/test_tradeevo.py`, `dev/e2e/soullink_tradeevo.py` |

## The evolution itself (same code as normal trades)
- **Yellow**: `game.evolve_for_trade` (Kadabra/Machoke/Graveler/Haunter): species, stats recalculated with the DVs and
  stat exp, HP keeps its damage, default name follows the species; `write_party_slot` + `set_dex`.
- **Crystal**: `crystal.evolve_for_trade`: the four + King's Rock / Metal Coat / Dragon Scale / Up-Grade cases, item
  set to 0, Everstone and eggs blocked.
- **Platinum**: `_evolve_gen4` mirrors the server's `pcstore._evolve_gen4` (item 0 for item evolutions, ability
  from the new species' slot `PID & 1`, default name when `has_nickname` is clear) and additionally recomputes the
  party section like `Pokemon_CalcLevelAndStats`: stats at the same level, HP + (new max - old max), 0 stays 0. The
  list of evolutions comes from the player's ROM (`PlatinumData.trade_evolutions()`, Everstone = item 229).
  `platinum.mark_dex` records the new species (caught + seen). Written only when `observe_stable()` says the party
  read identically on an earlier frame (the game decrypts records in place mid-frame).
- Not done (like normal YellowLink trades): the evolution scene and moves learned at evolution.

## Why the Soul Link bookkeeping is untouched
Keys don't change on evolution: Yellow `mon_key` = OT ID + DVs + catch-rate byte (byte 7: `evolve_for_trade` never
writes it), Crystal = OT ID + DVs + caught data (the held item, byte 1, isn't part of it), Platinum = PID + TID +
SID. So links, deaths, the PC rule, release detection and the referee's "unknown mon = gift/swap" check see the same
mon. The dupes clause uses the evolution family of the caught dex (`enc` events), and badge shiny keys use the
family root, so neither changes. The `evo` ledger event is display only (pairs list, toasts).

## Flow and its edges
- Requester (party mon, game `is_safe()`, not in a battle) -> `challenge_evo_request`; the app blocks input (mode
  `evo_wait`, Cancel = `done ok:false`).
- Partner: the prompt waits for a quiet moment (`is_safe()`); it auto-declines if another prompt is open or the
  request is for another run. Both asking at once: the server keeps the first and ends the second "busy" (to its
  sender only); that player's own wait closes and then the partner's question shows.
- A well-formed request that can't happen now (run over, partner not in the run / reconnecting, partner's client
  too old, another one open) is answered with `challenge_evo_end` to the requester, never a red error.
- `go` -> the requester writes at the next safe (and on Platinum stable) frame within 30 s, then `done ok:true`.
  The server appends `evo` to the ledger and ends it for both. If `done` can't be sent (offline), it's kept and
  resent; a reconnect (`challenge_state`) resends it too; a server that no longer knows it answers "expired".
- Client-side limits: no answer within 75 s -> give up waiting; no safe moment within 30 s of `go` -> `done
  ok:false`. Server: 60 s to answer, 180 s from `go` to `done`, session end -> "session over".
- Known gap: if the app is killed in the same frame as the write, the evolution is in the resume snapshot but the
  ledger misses the `evo` event (display only, rules unaffected).

## Verified on the real ROMs (`python dev/e2e/soullink_tradeevo.py`)
- Yellow, two clients: decline then accept; Kadabra -> Alakazam (key, OT, link kept; `evo` on both ledgers; both
  told); the game sends out ALAKAZAM in a real battle; it faints -> dead by the same key -> Alex's linked Pidgey dies
  and both go to the PCs.
- Crystal, two clients: Onix + Metal Coat -> Steelix (item 0, key kept, name follows), Kadabra + Everstone listed
  but not pickable (no request sent); the game sends out STEELIX; it faints -> Alex's linked Pidgey dies + boxed.
- Platinum (partner = protocol client): Kadabra -> Alakazam and Onix + Metal Coat -> Steelix through the real flow;
  stats/ability/name/item/Pokédex as above; records still valid 300 frames later; the partner's linked mon dies ->
  our Steelix is forced into the PC (box record = Steelix); Alakazam (sent out by the game) faints for real -> dead
  by its key; the partner's referee logic kills its linked mon; blackout ends the run.
Party setups are RAM writes (marked TEST SHORTCUT in the script).
