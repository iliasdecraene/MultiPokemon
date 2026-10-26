# YellowLink — technical spec (source of truth)

Private multiplayer layer for Pokémon Yellow (USA/Europe, SHA1 `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1`).
Each player runs their own single-player game in our client (PyBoy emulator, headless, rendered in our own pygame window).
The client reads/writes game RAM and talks to a small relay server over WebSocket. **No ROM, no Nintendo assets in the repo** —
each player supplies their own ROM; all graphics come from the running game's VRAM.

Features (v1):
1. **See friends walk around** — players on the same map are drawn into your screen with their real sprite + name tag.
2. **Trade / gift** — between PC databases (section 5), never the party: the server swaps the entries in both players' stored PCs atomically, across games (Yellow ↔ Crystal). Trade evolutions are applied server-side. A gift is a trade where only the giver offers.
3. **Battle (ghost battle)** — fight a friend's *current team* (uploaded snapshot; works even if they're offline), AI-controlled by the game.
   Exhibition match: client save-states before, restores after (no blackout, no money/EXP change). Result announced to everyone.
4. Chat, player list, update notice.

Non-goals v1: real-time PvP link battles (needs network-emulated link cable — roadmap).

---------------------------------------------------------------------------------------------------
## 1. Repository layout

```
yellowlink/
  SPEC.md
  README.md                 # player guide + host guide
  LICENSE                   # MIT
  .gitignore                # *.gb *.gbc *.sav *.ram *.state config.json dist/ build/ __pycache__ .venv
  client/
    yellowlink/             # python package
      __init__.py           # __version__ = "0.1.0"
      __main__.py           # entry: python -m yellowlink
      app.py                # main loop, window, input, UI
      game.py               # GameMemory: RAM addresses + read/write helpers (pure, testable with a dict-like memory)
      sprites.py            # tile decode + friend sprite rendering
      net.py                # websocket client in a background thread (asyncio), thread-safe queues
      ui.py                 # overlay UI: chat, player list, trade dialog, battle menu, toasts
      config.py             # config load/save (%APPDATA%/YellowLink/config.json, ~/.config/yellowlink elsewhere)
      audio.py              # PyBoy sound -> pygame mixer streaming
      text.py               # Gen-1 charset encode/decode
    requirements.txt        # pyboy==2.8.1 pygame-ce websockets numpy
    yellowlink.spec / build script for PyInstaller (onefile, windowed)
    tests/
  server/
    yellowlink_server/
      __init__.py
      __main__.py           # python -m yellowlink_server --host 0.0.0.0 --port 8765
      server.py
    requirements.txt        # websockets
    tests/
    Dockerfile
    docker-compose.yml
    yellowlink.service      # systemd unit alternative
    config.example.json
  .github/workflows/
    release.yml             # on tag v*: build Windows exe with PyInstaller, attach to GitHub Release (YellowLink.exe)
    test.yml                # on push: run server + client unit tests (ubuntu)
```

---------------------------------------------------------------------------------------------------
## 2. Wire protocol (JSON text frames over WebSocket)

All messages are JSON objects with a `"type"` field. Binary game data is base64 (standard alphabet) strings.
`PROTOCOL_VERSION = 1`.

### Mon object (used in party / trade / battle)
```json
{"data": "<b64 44 bytes (Yellow) or 48 bytes (Crystal) party_struct>", "ot": "<b64 11 bytes>", "nick": "<b64 11 bytes>"}
```
State `map` for Crystal is `group << 8 | number` (can exceed 255).
State for Platinum (`game` = `platinum`, no server change - the relay already passes these ints): `map` = map header id
(0-592; outdoor maps share one global Sinnoh grid, so x/y/px/py are comparable across them), `x`/`y` = tile (y = the
DS z axis), `px`/`py` = MapObject position in world units minus 8 (= tile*16 when standing), `facing` 0 up, 1 down,
2 left, 3 right, `img` = bit 0 gender (1 = Dawn) | bit 1 running | bits 2-3 avatar (0 walking, 1 bike, 2 surfing,
3 other) | height `pos.y` (world units, signed) << 4 (`client/yellowlink/sinnoh_view.py` pack_img/unpack_img).
Server treats these as opaque except for validation: data decodes to exactly 44 or 48 bytes (roster level is byte 33 or 31), ot/nick exactly 11 bytes, species byte (data[0]) != 0.

### Client -> Server
| type | fields | notes |
|---|---|---|
| `hello` | `name` (1-10 chars, A-Z a-z 0-9 space), `uid` (client-generated uuid4 string, persistent), `key` (room password), `version` (client semver), `protocol` (int) | must be first message. Wrong key -> `error` + close(4001). Duplicate uid already connected -> the old connection is kicked (close 4002). |
| `state` | `map` int, `x` int, `y` int, `px` int, `py` int, `facing` int (0 down,4 up,8 left,12 right), `img` int (sprite image index byte 0..15), `visible` bool, `game` str (`yellow`/`crystal`, default `yellow`) | ~10/s, only when changed (plus a keepalive every 2 s). px/py = pixel position in map space (tile*16 + walk offset). |
| `sprite` | `data` b64 (384 bytes: 12 tiles @VRAM 0x8000 + 12 tiles @0x8800) | send on change only |
| `party` | `mons` list[Mon] (0..6), `trainer` str (player's in-game name, decoded), `badges` int (bitfield, 0..65535: Crystal sends johto \| kanto << 8) | send on change (client checks every ~3 s) |
| `chat` | `text` (<=200 chars) | |
| `get_party` | `uid` | ask for someone's latest party snapshot (online or offline) |
| `battle_result` | `opponent_uid`, `won` bool | server broadcasts a system message |
| `trade_request` | `to` uid, `gift` bool (optional) | target must be online, neither busy. `gift`: requester gives, target only receives |
| `trade_respond` | `trade_id`, `accept` bool | by the target |
| `trade_offer` | `trade_id`, `entry_ids` list (0-20; `entry_id` single still accepted) | entries of the sender's **server** PC store; replaces the previous offer and resets both confirmations; `[]` withdraws it; a gift's receiver can't offer |
| `trade_confirm` | `trade_id` | valid when both offered (gift: when the giver offered) |
| `trade_cancel` | `trade_id` | either side, any time before it completes (unknown/finished trade: ignored) |
| `trade_status` | `trade_id` | outcome of a trade we lost track of (after a reconnect) |
| `bug_report` | `text` (<=60 KB), `reason` | saved to `<data_dir>/reports/`; answered with `report_saved`. Can also be sent on a report-only connection: `hello` with `"mode": "report"` (not logged in, not shown to others), then one `bug_report`. |
| `ping` | `t` | server answers `pong` with same `t` |

### Server -> Client
| type | fields |
|---|---|
| `welcome` | `you` uid, `players` list[PlayerInfo], `roster` list[RosterEntry], `latest_client` str, `download_url` str, `motd` str, `server_name` str (may be empty; env `YL_SERVER_NAME`) |
| `player_join` | `player` PlayerInfo |
| `player_leave` | `uid` |
| `state` | `uid` + all state fields (relayed to everyone else) |
| `sprite` | `uid`, `data` (also replayed to newly joined clients for all online players) |
| `chat` | `uid`, `name`, `text`, `game` (sender's current game id or null; chat goes to everyone, all regions) |
| `system` | `text` (join/leave/battle results/trade completed) |
| `roster` | `roster` list[RosterEntry] (re-sent whenever any party snapshot changes, someone changes game, or someone joins/leaves). RosterEntry includes `game` (current game if online, else last known; may be null) |
| `party_data` | `uid`, `name`, `trainer`, `mons` list[Mon] (or `error` if unknown) |
| `trade_request` | `trade_id`, `from` uid, `name`, `gift` bool |
| `trade_started` | `trade_id`, `with` uid, `name`, `gift` bool, `giver` uid (gift only) (sent to both after accept) |
| `trade_offer` | `trade_id`, `from` uid, `entries` list (the server's copies; to the *other* side), `entry` = first or null (0.6.1 clients) |
| `trade_confirm_state` | `trade_id`, `you` bool, `them` bool |
| `trade_complete` | `trade_id`, `gave_all` / `received_all` lists (after evolution), `evolved_all` names; `gave` / `received` / `evolved` = first item (0.6.1 clients) |
| `trade_cancelled` | `trade_id`, `reason` str |
| `pc_trades` | `deltas`: list of `{trade_id, remove: [entry_id], add: [entry], srv_rev}` for this player's PC |
| `trade_status` | `trade_id`, `state`: `complete` \| `pending` \| `failed` |
| `report_saved` | `file` |
| `error` | `message` |
| `pong` | `t` |

PlayerInfo: `{uid, name, state: <last state obj or null>, has_sprite: bool}`
RosterEntry: `{uid, name, trainer, online: bool, party_size: int, levels: [int], species: [int], last_seen: unix_ts}`

### Trade state machine (server) — protocol 2
`requested` --accept--> `open` (offers/re-offers; any new offer clears confirmations) --both confirmed--> **applied**:
in one step the server loads both stored PCs, checks the offered entries are still there and the receivers have room,
applies trade evolutions, removes each given entry (and adds its id to the store's `gone` list), adds each received
entry to the first box with room, bumps `rev`, appends a delta `{trade_id, remove, add, rev}` to the store's `inbox`,
saves both, then sends `pc_trades` + `trade_complete` to both and a system message. Nothing touches game RAM.
Disconnect in requested/open -> `trade_cancelled` reason "partner left". Timeout: requested expires after 60 s.
**Crash safety:** before touching either store the server writes the whole plan to `data/pc/journal/<trade_id>.json`;
each store records applied trade ids (`srv_trades`), so applying is idempotent; the journal is deleted when both are
saved and replayed at startup if the server died in between. A store that can't be read is never replaced (the trade is
cancelled). Stores are written new-file-first; the old copy is kept as `.1`…`.5`.
**Server-only store fields:** `srv_rev` (bumped on every accepted upload and every trade), `inbox` (undelivered
deltas, never trimmed; more than 100 → new trades refused), `srv_trades`, `srv_gone` (ids given away).
**Uploads are compare-and-swap:** `pc_put {store, base}` is accepted only if `base` == `srv_rev` and the store lists
every inbox trade id and holds no `srv_gone` id; then `pc_saved {srv_rev}`. Missing trades → `pc_trades` (apply, then
upload again); stale base → `pc_conflict {store}`: the client does a three-way merge (its changes since the last
synced copy replayed on the server copy; ids removed elsewhere go to its `gone` list so its game PC can't re-import
them) and uploads again. Merges wait while the game's PC is open or changes are pending.
**Client side:** a delta is applied to the committed, working and synced copies (idempotent via the store's `trades`
list; `server_rev` = delta `srv_rev`). If no box has room it waits in the store's `incoming` list and moves in when
the PC opens (a box never holds more than 20; a damaged local file is kept as `.bad-*` and repaired or merged from
the server, never silently emptied). An offered entry is **locked** (persisted) until the outcome is known
(`trade_cancelled`, the delta, or `trade_status` after a reconnect: `complete` | `pending` | `failed`). When the game's
PC closes, a locked or traded-away (`gone`) entry that was withdrawn is put back / removed from the party. Only
committed, unlocked entries can be offered, never while the in-game PC is open, and no resume snapshot is taken while
it is open. The trade window blocks game input.

Server persists party snapshots (by uid) + names to `data/players.json` (atomic write) so offline friends can be battled.

### Server config (`config.json`, path via `--config`, env overrides `YL_KEY`, `YL_PORT`)
`{"host":"0.0.0.0","port":8765,"key":"change-me","latest_client":"0.1.0","download_url":"https://github.com/<owner>/<repo>/releases/latest","motd":"Welcome!","data_dir":"data"}`

---------------------------------------------------------------------------------------------------
## 3. Game facts (verified against the ROM + pret/pokeyellow symbols)

All addresses WRAM bank 0/1 (Yellow never switches SVBK), read/write via `pyboy.memory[addr]`.

| name | addr | notes |
|---|---|---|
| wSpritePlayerStateData1PictureID | C100 | 0 on title screen; nonzero in-game |
| ImageIndex | C102 | facing (0/4/8/12) + anim frame (0-3) |
| FacingDirection | C109 | 0 down 4 up 8 left 12 right |
| YStepVector / XStepVector | C103 / C105 | signed (0xFF = -1) |
| wShadowOAM | C300 | player = first 4 entries |
| wJoyIgnore | CD6B | |
| wFontLoaded | CFC3 | bit0 set = text box/menu open; walking tiles at 0x8800 are overwritten by font |
| wWalkCounter | CFC4 | 8..1 during a step (decrements every 2 frames); coords update when it hits 0 |
| wTrainerName | D049 | 11 bytes, 0x50 terminated |
| wIsInBattle | D056 | 0 none, 1 wild, 2 trainer, 0xFF lost |
| wCurOpponent | D058 | write 200+class to start a trainer battle from the overworld |
| wLoneAttackNo | D05B | must be 0 |
| wTrainerNo | D05C | |
| wPlayerName | D157 | 11 bytes |
| wPartyCount | D162 | species list D163..D169 (FF terminated) |
| wPartyMons | D16A | 6 × 44 bytes |
| wPartyMonOT | D272 | 6 × 11 |
| wPartyMonNicks | D2B4 | 6 × 11 |
| wPokedexOwned | D2F6 | 19 bytes bitfield (bit = dexno-1) |
| wPokedexSeen | D309 | 19 bytes |
| wObtainedBadges | D355 | |
| wPlayerID | D358 | 2 bytes BE |
| wCurMap | D35D | |
| wYCoord / wXCoord | D360 / D361 | |
| wEnemyPartyCount | D89B | species list D89C.., wEnemyMons D8A3 (6×44), wEnemyMonOT D9AB, wEnemyMonNicks D9ED |

In-game check: `C100 != 0 and D157 != 0`.
Safe-to-write (trade apply / battle start): in-game, `D056 == 0`, `CFC3 & 1 == 0`, `CFC4 == 0`, `CD6B == 0`.

Party struct (44 bytes): 0 species(internal idx) · 1-2 HP BE · 3 box level · 4 status · 5 type1 · 6 type2 · 7 catch rate ·
8-11 moves · 12-13 OT ID · 14-16 exp · 17-26 stat exp (HP,Atk,Def,Spd,Spc ×2) · 27-28 DVs · 29-32 PP (bits7-6 PP-ups, 5-0 current) ·
33 level · 34-35 maxHP · 36-43 Atk,Def,Spd,Spc (2 bytes each BE).

ROM tables (file offset = bank*0x4000 + (addr-0x4000)):
* Moves: 0E:4000 → 0x38000, 6 bytes/move (anim, effect, power, type, accuracy, **pp**), index move_id-1.
* BaseStats: 0E:43DE → 0x383DE, 28 bytes/entry, index dexno-1: [dexno, hp, atk, def, spd, spc, type1, type2, ...].
* PokedexOrder: 10:50B1 → 0x410B1, 1 byte/species, index internal_id-1 → dexno.
* MonsterNames: 3A:4000 → 0xE8000, 10 bytes/species (0x50 padded), index internal_id-1.

Trade evolutions (internal ids): KADABRA 0x26→ALAKAZAM 0x95, MACHOKE 0x29→MACHAMP 0x7E, GRAVELER 0x27→GOLEM 0x31, HAUNTER 0x93→GENGAR 0x0E.
Stat recompute (Gen 1): `stat = floor(((base+DV)*2 + floor(ceil(sqrt(statexp))/4)) * L / 100) + 5`; HP adds `L + 10` instead of 5.
DVs: byte27 hi nibble Atk, lo Def; byte28 hi Spd, lo Spc; HP DV = (Atk&1)<<3 | (Def&1)<<2 | (Spd&1)<<1 | (Spc&1).
On evolve: keep HP damage (newHP = newMax - (oldMax - oldHP)), update species/types, update nickname if it equalled old species name, set dex seen+owned.

Ghost battle recipe (verified): when safe, write my-party heal (optional), `D05B=0`, `D05C=1`, `D058=200+0x1A` (PROF_OAK class, GenericAI, no items).
Tick until `D056 == 2` (happens ~190 frames later, before "X wants to fight!" text), then immediately overwrite enemy party
(count, species list + FF, mons with HP=maxHP/status 0/PP full, OT, nicks) and `wTrainerName` with the friend's name.
Battle ends when `D056` returns to 0 (or player whites out). Before triggering: `pyboy.save_state` to memory; after the battle has ended
and the screen is back to overworld (D056==0 for 60 frames, or whiteout), read `wBattleResult`?—simpler: winner = enemy party all fainted
(read wEnemyMons HP before restore) ; then `load_state` the snapshot. Gen-1 trainer mons display species names, not nicknames (expected).

Sprite rendering (verified by OAM probing): screen position of the local player's 16×16 sprite top-left is (64, 60).
Friend on same map: `sx = 64 + (friend.px - my.px)`, `sy = 60 + (friend.py - my.py)`.
My pixel pos: `px = x*16 + (wc>0 ? sx_vec*2*(8-wc) : 0)`, same for y, where wc = wWalkCounter, vec = signed step vector.
Image index → tiles: `dir = img & 0xC`, `f = img & 3`; `base = 8 if dir==12 else dir`; `if f in (1,3): base += 0x80`.
Tiles base..base+3 arranged TL, TR, BL, BR. Horizontal flip of the whole 16×16 when `dir==12` OR (`dir in (0,4)` and `f==3`).
Color index 0 transparent. Tile data from the sender's VRAM (`sprite` message, 24 tiles: indices 0x00-0x0B then 0x80-0x8B).
Cache own tiles only while `CFC3 & 1 == 0`.


---------------------------------------------------------------------------------------------------
## 4. Live PvP battles (protocol + game recipe) — added in 0.2.0

Both players' games run the game's own **link battle** engine (`wLinkState = LINK_STATE_BATTLING`), started from the
overworld (no Cable Club). Link battles are deterministic given: identical parties on both sides, the same 10-byte
random list (`wLinkBattleRandomNumberList`), and opposite `hSerialConnectionStatus`. Each turn the game calls
`LinkBattleExchangeData`, which exchanges one nybble (move slot 0-3, switch 4-9, D/E/F specials) via
`Serial_ExchangeNybble`. The client hooks that routine and carries the nybble over the server instead of a cable.
Verified: full 2-vs-1-mon battle incl. faint + switch stays HP-identical on both sides, both reach the link result screen.

### Messages (client -> server)
| type | fields | notes |
|---|---|---|
| `battle_request` | `to` uid | target must be online; neither side may be in a trade or battle (error otherwise) |
| `battle_respond` | `battle_id`, `accept` bool | target only |
| `battle_ready` | `battle_id`, `mons` list[Mon] (1-6), `trainer` str | sent once the client has frozen the player and snapshotted its game |
| `battle_move` | `battle_id`, `n` int >= 1, `v` int 0-15, `check` str (optional, <= 64 chars) | relayed verbatim to the opponent |
| `battle_end` | `battle_id`, `result` int: 0 = I won, 1 = I lost, 2 = draw | |
| `battle_leave` | `battle_id` | forfeit / cancel at any stage (also valid with an empty/missing id: acts on own battle) |

### Messages (server -> client)
| type | fields |
|---|---|
| `battle_request` | `battle_id`, `from`, `name` (to target only; requester gets nothing back, like trades) |
| `battle_prepare` | `battle_id`, `with`, `name`, `master` bool (to both after accept; requester is master) |
| `battle_start` | `battle_id`, `with`, `name`, `master`, `rns` list of 10 ints 0..252, `mons` (opponent's Mon list), `trainer` (opponent's in-game name) |
| `battle_move` | `battle_id`, `n`, `v`, `check` |
| `battle_aborted` | `battle_id`, `reason`: `declined` / `timeout` / `cancelled` / `opponent left` / `opponent disconnected` |

### Server state machine
`requested` (60 s timeout) --accept--> `preparing` (60 s timeout for both `battle_ready`) --both ready--> `running`
(server generates `rns`) --first `battle_end`--> closed (system broadcast: "A beat B in a link battle!" or "A and B drew!",
written from the reporter's perspective). `battle_leave` or disconnect at any stage -> `battle_aborted` to the other side
(reason `cancelled`/`declined` before running, `opponent left`/`opponent disconnected` while running).
Messages for a battle that no longer exists (late `battle_move`, second `battle_end`, `battle_leave`) are ignored
silently — **no error message**. A player can be in at most one trade or battle at a time.

### Client recipe (verified)
1. On `battle_prepare`: block input, wait until `is_safe()`, `save_state` snapshot, heal own party, send `battle_ready` with
   own party (as read after healing).
2. On `battle_start`: write opponent's mons (each passed through `full_heal`, same as they did) to the enemy party
   (count, species list + FF padding, structs, OT, nicks), `wLinkEnemyTrainerName` (D886, 11 bytes) = opponent name,
   `wLinkBattleRandomNumberList` (D147, 10 bytes) = rns, `wLinkBattleRandomNumberListIndex` (CCDE) = 0,
   `hSerialConnectionStatus` (FFAA) = 2 if master else 1, `wLinkState` (D12A) = 4, `wLoneAttackNo` = 0,
   `wCurOpponent` = 200 + 0x19 (OPP_RIVAL1). Unblock input.
3. Hooks (registered once at startup, inactive unless a PvP battle runs):
   - `0F:5777 LinkBattleExchangeData` entry: exchange counter n += 1.
   - `00:211F Serial_ExchangeNybble`: on first call for exchange n send `wSerialExchangeNybbleSendData` (CC42) & 0x0F;
     if the opponent's nybble for n has arrived write it to `wSerialExchangeNybbleReceiveData` (CC3E); then emulate `ret`
     (PC = word at SP, SP += 2). The game's own loop polls once per frame, so waiting never blocks the emulator.
   - `00:2149 Serial_SendZeroByte`: emulate `ret`.
4. Battle over when `wIsInBattle` returns to 0: result from `wBattleResult` (CF0B: 0 win, 1 lose, 2 draw); wait ~3 s
   (result screen), send `battle_end`, restore the snapshot.

---------------------------------------------------------------------------------------------------
## 5. PC database ("live HOME") — added in 0.5.0

Each player's stored Pokémon live in **one database per player**, not in the games. A game's PC boxes are a *view*
of it, refreshed whenever the player opens Bill's PC and harvested when they leave it.

### Data model (JSON)
```
PCStore = {"version": 1, "rev": int, "boxes": [[Entry, ...] x 14], "linked": ["yellow", ...]}
Entry   = {"id": "<uuid4 hex>", "origin": "yellow"|"crystal", "dex": int, "level": int, "nick": str, "ot": str,
           "otid": int, "gen1": "<b64 33+11+11 bytes>" | null, "gen2": "<b64 ...>" | null,
           "gen4": "<b64 136-byte encrypted Platinum box PK4>" | null}       # gen4 + origin "platinum": 0.7.0
```
- 14 boxes of up to 20 (Crystal has 14; Yellow shows boxes 1-12 — 13/14 are only visible in Crystal).
- **Current representation** (0.7.0): the one named by `origin` (yellow → gen1, crystal → gen2, platinum → gen4;
  an entry harvested from a game is re-created with that game as origin), else the first present of gen1, gen2,
  gen4. Other games convert from it (`pk4.gen1_record` / `gen2_record` / `record_for`, else a stand-in). A server-side
  trade evolution changes the current representation and nulls the others. Platinum shows store box i in its box i,
  slots 1-20; Pokémon put anywhere else in Platinum's 18x30 boxes are compacted into free store slots on close.
  If the store is full, new Platinum Pokémon stay in the game (boxes 15-18) and the player is told. Platinum sync:
  the view is written on the exec hook `0x0203D754` (FieldSystem_OpenPokemonStorage, after DEPOSIT/WITHDRAW/MOVE is
  picked, before the box app reads PCBoxes); close is polled (box app = field child overlay 19 gone). Party records
  are decrypted in place mid-frame by the game: always read them via `PlatinumMemory.party_checked()`.
  First link of a game whose boxes don't fit the store: Yellow/Crystal are not linked at all (nothing written, the
  player is told); Platinum keeps what doesn't fit in its boxes 15-18. Edits made in Platinum's box screen (MARK,
  MOVE ITEMS) keep the entry id (matched by mon_key); an item given to a stand-in, locked or traded-away Pokémon goes
  back to the bag. The last view records what the game really holds, so a traded-away Pokémon still sitting in a box
  is never harvested again.
- `gen1`/`gen2` hold the exact game bytes (box_struct + OT name + nickname) of the last version seen in that
  generation, so a Pokémon that never leaves its game round-trips byte-identically. Readable fields (dex, level, names)
  are for UI/conversion only.
- A Pokémon that can't exist in a game (e.g. Johto species/moves in Yellow) is shown there as a **stand-in**:
  Ditto with nickname `?NAME` (first 9 letters), OT name `YLINK`, OT ID 0. Stand-ins can't be withdrawn or released:
  if one shows up in the party, or disappears from the boxes, YellowLink puts it back. The real data is never touched.

### Sync rules (client)
- Open (hook `BillsPC_` 08:546F): (1) **reconcile** — diff the game's current boxes against `last_view` (what we
  last wrote/saw) and apply any changes to the store; (2) **materialise** — write the store's view into all 12 boxes
  (current box → WRAM `wBoxDataStart`, others → SRAM bank 2/3 slots with per-box + per-bank checksums; set
  `wCurrentBoxNum` bit 7); (3) remember that as `last_view`.
- Close (hook `ExitBillsPC` 08:553E): diff the game's boxes against `last_view` → **pending** changes:
  appear = new Entry (deposit), vanish = removed (withdraw/release), identical bytes = same Entry (moves).
  Stand-in rules applied. `last_view` := game's boxes.
- **Commit** happens only when an auto-resume snapshot is written (overworld, idle): pending → committed store +
  `last_view` saved together to `%APPDATA%/YellowLink/pc/store.json` (atomic), then pushed to the server. A crash rolls
  the game back to the previous snapshot *and* the store back to the previous commit, so they always agree.
- First contact of a game (not in `linked`): its existing boxes are imported (appended into free box space), then
  the game is added to `linked`.

### Protocol
| dir | type | fields |
|---|---|---|
| C→S | `pc_get` | — |
| S→C | `pc_data` | `store` (PCStore or null if none yet) |
| C→S | `pc_put` | `store` (full PCStore, `rev` = previous rev + 1) |
| S→C | `pc_saved` | `rev` |
| S→C | `pc_conflict` | `store` (server's newer copy) — sent if `store.rev` <= server rev |
Server keeps `<data_dir>/pc/<uid>.json` (atomic writes, last 5 revisions kept as backups). Max message 256 KB.
Client: on connect sends `pc_get`; if the server copy has a higher `rev` than the local one (played on another PC),
it replaces the local store **only while no PC session is open and nothing is pending**.

---------------------------------------------------------------------------------------------------
## 6. Pokémon Crystal facts (verified) — `client/yellowlink/crystal.py`

Crystal v1.0 USA, SHA1 `f4cd194bdee0d04ca4eac29e09b8e4e9d818c133` (CGB-only, MBC3 + RTC). Symbols: pret/pokecrystal
`symbols` branch. Every entry below is asserted by `dev/crystal/verify.py` against the running ROM (fixtures from
`dev/crystal/make_states.py`). **WRAM banking:** Crystal switches SVBK; at the end of a `tick()` the D000-DFFF window
is often bank 5 or 6 (seen ~60 % of battle frames). All D000-DFFF variables are bank 1 → always `mem[1, addr]`.
`mem[bank, addr]` must not be used for C000-CFFF (PyBoy maps `[1, C4A0]` to D4A0).

| name | addr | notes |
|---|---|---|
| wShadowOAM | C400 | player = 4 entries at y 76/84, x 72/80; attr bit 3 (VRAM bank 1) set, palette = wPlayerPalette & 7 |
| wTilemap | C4A0 | 20×18; menu cursor tile 0xED; Bill's PC lists use a sprite cursor (selected species name on row 14) |
| wWindowStackSize | CF78 | > 0 while a menu window is open |
| wGameTimerPaused | CFBC | bit 0 = game clock running → "in game" (0 on title, main menu, CONTINUE card) |
| wJoypadDisable | CFBE | |
| wSaveFileExists | CFCD | main menu: 0 none, 1 save ok, 2 backup ok |
| wStateFlags | 1:D0ED | bit 6 text state (= Yellow wFontLoaded), bit 7 scripted movement |
| wPlayerStepVectorX/Y | 1:D14E/F | signed; px the camera still trails the step logic (2 walking) |
| wBattleMode | 1:D22D | 0 none, 1 wild, 2 trainer |
| wScriptFlags / wScriptMode | 1:D434 / 1:D437 | bit 2 / non-zero = script running |
| wPlayerID / wPlayerName | 1:D47B (BE) / 1:D47D | |
| wSavedAtLeastOnce | 1:D4B4 | 1 once the first save erased/initialised the 14 SRAM boxes |
| wPlayerStruct | 1:D4D6 | +0 sprite, +2 sprite tile (0), +6 palette, +7 walking (FF or speed*4+dir), +8 direction (0/4/8/12), +A step duration, +D facing (FACING_STEP 0-15 = Yellow img), +10 map X (= wXCoord+4) |
| wJohtoBadges / wKantoBadges | 1:D857 / 1:D858 | `badges()` = johto \| kanto << 8 |
| wCurBox | 1:DB72 | 0-13 |
| wMapGroup / wMapNumber | 1:DCB5 / 1:DCB6 | map id = group << 8 \| number |
| wYCoord / wXCoord | 1:DCB7 / 1:DCB8 | update when a step ends |
| wPartyCount / Species / Mons / OTs / Nicks | 1:DCD7 / DCD8 / DCDF (6×48) / DDFF / DE41 | species list 0xFD = egg |
| wPokedexCaught / Seen | 1:DE99 / 1:DEB9 | 32 bytes each, bit = dex − 1 |
| wOBPals2 | 5:D0C0 | OBJ palettes, RGB555 LE; PyBoy renders `c << 3` per channel |

Position: a step is 8 logic updates of 2 px (bike 4×4, slow 16×1), one update per 2 frames. `px = x*16 + dir_x*per*(total
− duration) − stepVectorX` while walking; this equals the camera (px − SCX constant on every frame) and is x*16 at rest.
Sprite: VRAM **bank 1**, tiles 0x00-0x0B + 0x80-0x8B → same 384-byte blob and same `compose()` as Yellow (verified
pixel-exact for all 16 images; the screen shows the facing value from 3 frames earlier).
Party struct 48 bytes: 0 species · 1 item · 2-5 moves · 6-7 OT ID · 8-10 exp · 11-20 stat exp (HP,Atk,Def,Spd,Spc) · 21-22 DVs ·
23-26 PP · 27 happiness · 28 pokérus · 29-30 caught data · 31 level · 32 status · 33 unused · 34-35 HP · 36-37 max HP · 38-47 Atk,
Def, Spd, SpAtk, SpDef. Stats: Gen-1 formula, SpAtk and SpDef both use the Special DV and Special stat exp.
ROM: BaseData 14:5424 (0x51424, 32 B, growth rate at +22), PokemonNames 14:7384, Moves 10:5AFB (7 B, PP +5), MoveNames 72:5F29.

PC: box i at SRAM bank 2 (i < 7) / 3, `A000 + (i%7)*0x450`; 0x44E bytes (count, 21 species, 20×32 box_struct, 20×11 OT,
20×11 nick) + 2 padding bytes FF 00. The current box is used live from **sBox 1:AD10** (deposit/withdraw write there);
saving copies sBox → slot, CONTINUE and CHANGE BOX copy slot → sBox. CHANGE BOX (and MOVE PKMN between boxes) runs a
full save. No box checksums exist; the main checksum (16-bit sum of 1:A009-AB82 at 1:AD0D LE, backup 0:B209/0:BF0D)
does not cover boxes. Before the first save, slots are garbage and the first save erases them → don't materialise.
Records in the PC store: 32 + 11 + 11 + 1 egg-flag byte (55). Hooks: open `_BillsPC.LogIn` 03:641C, close
`_BillsPC.LogOut` 03:643F.

RTC: PyBoy's RTC advances with emulated frames while running (not wall time) and adds the wall time elapsed since it
was saved when a `.rtc` file or save state is loaded. Keep a `.rtc` next to each `.sav` (`stop(rtc_file=...)`), or
PyBoy silently reads/writes `<rom>.rtc` next to the ROM. `rtc_file=io.BytesIO()` (empty) raises "No data".

## 7. Challenges (Soul Link) — added in 0.6.0

A challenge is a co-op run between two players on **fresh, separate saves** of the same game (Yellow or Crystal).
It only runs while **both** players are in it. The server keeps the challenge record and an append-only **ledger**
of rule events; each client derives the rule state from the ledger (`client/yellowlink/soullink.py`) and enforces
it in its own game.

### Local storage (client)
`<server folder>/challenges/<challenge_id>/game.sav` (server folder: see section 8), `resume.state`, `game.rtc`. While playing a challenge,
`cfg["active_challenge"] = {"id", "game"}`; `Config.game_id/save_path/resume_path` point at the challenge.
The main game's library, saves, PC database and party snapshot are untouched. On a fresh start of the app
(e.g. after a crash) `active_challenge` is cleared, so the player always starts in their main game.
Inside a challenge: no PC-database sync, no trades, no live/practice battles, no party publishing, and only
players in the same challenge are drawn.

### Record (server, `data/challenges.json`)
```
{"id", "kind": "soul_link", "game": "yellow"|"crystal", "players": [uidA, uidB], "names": {uid: name},
 "rules": {"dupes": bool, "shiny": bool}, "status": "active"|"completed"|"failed"|"abandoned",
 "created", "updated", "ended", "reason": str, "progress": {uid: {"badges": int, "hof": bool}},
 "ledger": [event, ...]}
```
Summaries (`ledger` omitted, plus `"online": [uid...]` = players currently in the session) go to clients in
`challenge_list` / `challenge_update`. At most one non-finished challenge per pair and kind.

### Messages
| client → server | fields | notes |
|---|---|---|
| `library` | `games` list[str] | games the player has installed (sent on connect); shown in roster as `games` |
| `challenge_request` | `to`, `kind`, `game`, `rules` — or `to`, `challenge_id` to continue | target must be online and free |
| `challenge_respond` | `request_id`, `accept` bool | |
| `challenge_join` | `challenge_id` | sent after loading into the challenge game (and after every reconnect while in it) |
| `challenge_leave` | `challenge_id`, `reason` | pause: back to the main game |
| `challenge_event` | `challenge_id`, `event` | appended to the ledger, echoed to **both** players |
| `challenge_progress` | `challenge_id`, `badges` | display only |
| `challenge_abandon` | `challenge_id` | ends it for both (status abandoned) |

| server → client | fields |
|---|---|
| `challenge_request` | `request_id`, `from`, `name`, `kind`, `game`, `rules`, `challenge_id` (null = new) |
| `challenge_declined` | `request_id`, `name`, `reason` |
| `challenge_go` | `challenge` (summary) — both players travel into the challenge now |
| `challenge_state` | `challenge` (full record incl. ledger) — reply to `challenge_join` |
| `challenge_ready` | `challenge_id` — both players are in; the game unpauses |
| `challenge_partner_lost` | `challenge_id`, `name` — partner's connection dropped; 30 s grace, game paused |
| `challenge_partner_left` | `challenge_id`, `name`, `reason` — session over, go back to the main game |
| `challenge_event` | `challenge_id`, `event` (with `seq`, `p` = uid, `ts`) |
| `challenge_list` | `challenges` list[summary] (after `hello`) |
| `challenge_update` | `challenge` (summary) — any status/progress change |

Session rules (server): a join from a player of an active challenge marks them present; when both are present →
`challenge_ready` to both. `challenge_leave` or a disconnect lasting > 30 s ends the session and the other player
gets `challenge_partner_left`. A player who loads in but whose partner doesn't join within 90 s also gets
`challenge_partner_left` (reason "didn't load in"). Requests expire after 60 s.

### Ledger events (`event` objects; server adds `seq`, `p`, `ts`)
| `t` | fields | meaning |
|---|---|---|
| `enc` | `area`, `dex`, `status`: "open"\|"caught"\|"failed"\|"skipped", `key` (when caught), `gift` bool, `shiny` bool | first encounter of an area for player `p`. "open" when a counting wild battle starts; then "caught"/"failed". "skipped" = didn't count (dupes clause, shiny clause, no balls yet) |
| `dead` | `key`, `why` | a mon fainted, was released, or vanished — permanently unusable |
| `loc` | `key`, `where`: "party"\|"box" | player moved a linked mon (PC rule propagates to the partner) |
| `swap` | `old`, `new` | in-game NPC trade: the link moves to the received mon |
| `balls` | | player has Poké Balls for the first time: the Nuzlocke rules (faint = dead, blackout = fail, first encounters) apply from here; gifts (starters) count before it |
| `hof` | | player entered the Hall of Fame |
| `blackout` | | whole party fainted → challenge failed |

Server reacts to `hof` (both players → status completed) and `blackout` (status failed). An `enc` left "open" when a
player reconnects or reloads is treated as **failed** (no rerolling by closing the game mid-battle). Rule
derivation (in `soullink.py`): two caught encounters in the same area = a linked pair; a caught mon whose partner's
encounter in that area failed is **unlinked** (must stay boxed); `dead` on either side kills both; `loc` to box
on one side boxes the partner.

### Game helpers used by the referee (verified on the real ROMs)
`GameMemory` / `CrystalMemory`: `battle_info()`, `battle_party_slot()`, `has_poke_balls()`, `in_hall_of_fame()`,
`current_box_records()`, `move_party_to_box()`, `move_box_to_party()`, `daycare_records()`, module `mon_key()`,
`AREAS`/`area_of()`/`area_name()`. Addresses, frame timings and gotchas: `dev/notes/soullink_yellow.md` and
`dev/notes/soullink_crystal.md`; checks: `dev/e2e/soullink_mem_yellow.py` (81) and `dev/crystal/soullink_verify.py` (109).
Key facts: Yellow `battle_info` is valid from the first frame of `wIsInBattle`; Crystal's is None on frame 0 and valid
from frame 1 (the referee waits for the first non-None). A faint counts when `battle_party_slot()` shows HP 0 on two
frames in a row. Withdrawing heals in Crystal, so deaths are tracked by `mon_key`, never by HP. Bug-Catching Contest
battles are free; the kept mon arrives later as the National Park "gift". Only call the forced moves while `is_safe()`.

## 8. Servers (one per group of friends) — added in 0.6.4 (client only, no protocol change)

A player can be on several servers (each group of friends runs its own, see server/DEPLOY.md) and switches between
them from Multiplayer → Switch server. Everything that belongs to a group is per server; only the player's name,
game files (ROMs) and settings are shared.

- `config.json`: `servers = [{id, label, url, key, uid, game, seen_name}]`, `server_id` = active one. The active
  server's `url`/`key`/`uid` are mirrored in `server_url`/`room_key`/`uid` (what the rest of the client reads);
  `Config.save()` copies them back into the entry (so the settings window edits the active server).
- **Player id per server**: a new server gets a new `uid`, so no server ever sees a player id from another server
  (the server-side PC store and challenge records are keyed by uid).
- **Folder per server** (`Config.data_dir`): the first server (`id "main"`, created from a pre-0.6.4 config) keeps the
  original folders (`<appdir>/saves`, `resume`, `pc`, `challenges`); others use `<appdir>/servers/<id>/` with the same
  layout. Saves, resume snapshots, backups, the PC database and challenge saves all live there.
- `game` = the region/game last played on that server; restored on switch (each group is somewhere else).
- `label` (the player's own name for it) or else `seen_name` (the server's `server_name` from `welcome`) or else
  the address is shown in the list.
- Switching = save + close + reopen (like travelling between regions). Refused during a trade, battle or challenge;
  `active_challenge` is cleared. Adding a server with an address already in the list is refused. Removing a server
  only forgets it: its folder stays on disk.
- **Merging servers** (0.7.1): `python -m yellowlink_server.merge FROM INTO` (or the file directly with python3)
  copies players.json entries, pc/<uid>.json, challenges.json records and reports; never overwrites (ids that exist
  in both are kept as in INTO and reported), backs INTO up first, refuses while FROM has a trade journal. The old
  server then runs with `YL_MOVED_TO` (+ `YL_MOVED_KEY`): after a valid hello from a client >= 0.7.1 it sends
  `{"type": "moved", "url", "key"}` and closes with **4005**; older clients get a welcome with an empty roster, the
  update notice (`latest_client`) and a motd, and all their messages are ignored (no roster, PC, trades or chat).
  The client (`App.on_moved` → `Config.move_server`) replaces the active entry's url/key, keeping its id, folder and
  uid, and restarts on it. If another entry already has that address (the player was on both servers), the moved
  entry keeps its own save as a second character and gets the label "<old name> (save)". net.py treats 4005 as final
  ("server moved") instead of reconnecting.
