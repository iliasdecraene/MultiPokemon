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
| `badges` | `earned` {region: {`champion`, `dex`, `shinies`}} | main-game badges (0.7.5, section 9); only if `welcome.features` has `badges` |
| `get_badges` | `uid` | full badge view of one player (section 9) |

### Server -> Client
| type | fields |
|---|---|
| `welcome` | `you` uid, `players` list[PlayerInfo], `roster` list[RosterEntry], `latest_client` str, `download_url` str, `motd` str, `server_name` str (may be empty; env `YL_SERVER_NAME`), `features` list[str] (0.7.5: `["badges"]`, 0.7.6: + `"trade_evo"` (section 7), 0.7.7: + `"defplat"`; missing = older server), 0.7.7: `sinnoh`, `patches` (section 10) |
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
| `badges_data` | `uid`, `name`, `badges` {region: BadgeView} (section 9; unknown player: `badges` {} ) |

PlayerInfo: `{uid, name, state: <last state obj or null>, has_sprite: bool}`
RosterEntry: `{uid, name, trainer, online: bool, party_size: int, levels: [int], species: [int], last_seen: unix_ts,
badges: {region: {champion: bool, dex: bool, shiny: int, soullink: "gold"|"silver"|null}}}` (`badges` 0.7.5: only
regions with something earned; `{}` = none yet)

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

### Badges (0.7.5) — `game.progress` / `champion` / `wild_is_shiny` (verified: `dev/badges/verify.py`)
| name | addr | notes |
|---|---|---|
| wNumHoFTeams | D5A1 | +1 by AnimateHallOfFame (saturates at 255), inside wMainData (saved). `champion()` = count > 0 or standing in HALL_OF_FAME (map $76) |
| wPokedexOwned | D2F6 | 19 bytes, bit dex−1; Kanto goal = #1-150 (the game's own Pokédex OWN = ours + Mew, checked on the dex screen) |
| wEnemyMonDVs | CFF0 | wild DVs (LoadEnemyMonData, right after InitWildBattle sets wIsInBattle; constant from frame 0 to the menu in 6 real encounters) |
| wTransformedEnemyMonOriginalDVs | CCEB | the wild mon's own DVs once it used Transform (wEnemyMonDVs then = the target's) |
| wEnemyBattleStatus3 | D068 | bit 3 TRANSFORMED |

Own catch = OT name + OT ID equal to wPlayerName/wPlayerID (Gen 1 has no origin field). Shiny = Gen-2 rule on DVs
(bytes 27-28). Read from the party, all 12 boxes (pcdb.Gen1PC) and the Day Care. Key = **badge key**
`badges.gen1_badge_key` = OT ID + DVs + evolution-family root (`soullink.family`), 11 hex digits - not the PC/Soul
Link `mon_key`: its catch-rate byte is 255 for many species and the Time Capsule rewrites it (Yellow -> Crystal ->
Yellow), so different shinies merged and one mon could get two keys. Identical keys within one reading are numbered
(`badges.number_keys`: key, key-2, ...); the game reading and the PC-store reading (`badges.store_shiny_keys`) are
numbered apart and merged, so a mon in both counts once (max per source). `battle_info()["shiny"]` and
`wild_is_shiny` = the Gen-2 rule on the wild mon's DVs (`GameMemory.wild_dvs`), only for kind "wild" (normal +
Safari; wild_is_shiny None otherwise, battle_info shiny False). Catching a transformed mon gives a DITTO with the
original DVs (pret ItemUseBall), so the indicator matches what is caught.


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
  `wCurrentBoxNum` bit 7); (3) remember that as `last_view`; (4) **Pokédex** (0.7.5) — every species in the view
  counts in that game's Pokédex like a received trade does in the real game (`codec.mark_dex`, all three games, at
  the same hook, main game only; stand-ins and eggs left out): Yellow seen + owned (pret AddPartyMon), Crystal
  SetSeenAndCaughtMon + Unown letter (UpdateUnownDex, wFirstUnownSeen), Platinum a port of Pokedex_Capture (gender
  seen, Spinda pattern, Unown/Burmy/Wormadam/Shellos/Gastrodon/Deoxys/Shaymin/Giratina/Rotom forms, language,
  caught + seen; equal byte for byte to the game's own catch, `dev/badges/verify.py`). Without it, Pokémon that
  only come by trade could never complete a Pokédex badge.
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

### Badges (0.7.5) — `crystal.progress` / `champion` (verified: `dev/badges/verify.py`)
| name | addr | notes |
|---|---|---|
| wPokedexCaught | 1:DE99 | 32 bytes, bit dex−1; Johto goal = #1-251 minus Mew (151) and Celebi (251) = 249 |
| wHallOfFameCount / wStatusFlags bit 6 / EVENT_BEAT_ELITE_FOUR (68 in wEventFlags 1:DA72) | 1:D95E / 1:D84C | `champion()` = any of these (`entered_hall_of_fame()`, saved) or standing in HALL_OF_FAME (16:8); each proof checked alone + the real HoF scene |
| wPlayerID / wPlayerName | 1:D47B / 1:D47D | own catch = OT name + OT ID equal |

Origin = Crystal: caught data (bytes 29-30) non-zero - the game writes it for every catch, gift (`givepoke` without a
trainer OT goes through SetCaughtData), contest prize and hatch; YellowLink's Gen-1 conversions have 0000. A real
Route 29 catch got caught data 8202. Eggs don't count until hatched. Read from the party, the 14 boxes (Gen2PC: only
the live sBox before the first save) and both Day Care slots (wBreedMon1/2). Key = badge key `badges.gen2_badge_key`
= OT ID + DVs + RAW caught data + family root (15 hex digits; the PC `mon_key` zeroes a hatched mon's caught data,
which merged all hatched shinies with the same Atk DV), numbered like Yellow's.
Pokédex for mons arriving through the PC view (section 5): wUnownDex 1:DED9 (26 letters 1-26, 0 = free),
wFirstUnownSeen 1:DEF4; letter = pret GetUnownLetter (`crystal.unown_letter`).

### Badges (0.7.5) — Platinum, `platinum.progress` / `champion` (full memory map: dev/platinum/MEMORY_MAP.md)
| name | where | notes |
|---|---|---|
| Pokedex | save body + 0x1328 (save page 7, 812 bytes) | u32 magic 0xBEEFCAFE, then caughtPokemon u32[16] (+0x04), seenPokemon (+0x44); bit species−1 (pret include/pokedex.h) |
| Sinnoh dex | ROM `/application/zukanlist/zkn_data/zukan_data.narc` member 12 | the 210 species = `badges.SINNOH_DEX`; `/poketool/pl_pokezukan.narc` member 0 = national→Sinnoh number. #151 is MANAPHY (490): event-only, so the goal leaves it out (`SINNOH_DEX_EXCLUDED`) = 209 |
| Champion | FLAG_GAME_COMPLETED 2404 / TrainerInfo.isMainStoryCleared / RECORD_TIMES_ENTERED_HALL_OF_FAME | `champion()` = `entered_hall_of_fame()` (all set by ClearGame, saved) or standing in the Hall of Fame room (map 187); checked alone + the real scene |

Own catch = origin game 12 (Platinum) + OT name + TID + SID equal to TrainerInfo (a real catch has all of them;
converted Gen-1/2 mons are origin 4/7 with SID 0). Shiny = (TID ^ SID ^ PID_hi ^ PID_lo) < 8; eggs don't count until
hatched. Read from `party_checked()` (None -> progress None), all 18 x 30 box slots and the Day Care
(`platinum_pc.normalise`; a slot that can't be read is skipped that time).
Pokedex block layout used by `platinum.pokedex_capture` (checked on the ROM): +0x84 recordedGenders[2][64 bytes],
+0x104 spindaForm, +0x108..0x10B Shellos/Gastrodon/Burmy/Wormadam forms, +0x10C unownFormsSeen[28] (0xFF free),
+0x128 recordedLanguages[496] (bit per language JP EN FR DE IT ES), +0x31C Rotom forms (3 bits each, 7 free),
+0x320/0x321 Shaymin/Giratina forms, Deoxys forms in the top nibbles of caught[15]/seen[15]; size 0x324.

### Exp Share All hooks (Yellow + Crystal) — `client/yellowlink/expshare_gb.py`
Modern rule (Gen 6+): participants get the full single-participant exp + stat exp (no split), other party mons with
HP > 0 that aren't eggs get `floor(full / 2)` exp (after all x1.5 boosts) and `floor(base / 2)` stat exp; fainted mons
and eggs get 0; it replaces Yellow's EXP.ALL / Crystal's held Exp. Share while on. `install_exp_share(pyboy, enabled)`
in `game` / `crystal`; `enabled()` is read once per award (when the enemy faints), False = the original game. It steers
the games' own two-pass award (EXP.ALL / Exp. Share machinery), so level-ups, moves, evolution are native.

| game | hook | where (pret) | what |
|---|---|---|---|
| Yellow | 0F:461C | FaintEnemyPokemon `push af` after IsItemInBag(EXP_ALL) | decide; force Z (no halving, no EXP.ALL pass) |
| Yellow | 0F:4637 | FaintEnemyPokemon `ret z` after GainExperience | clear Z when someone sat out -> game's 2nd pass |
| Yellow | 15:525F | GainExperience entry | 2nd pass: wPartyGainExpFlags D057 = ours, wBoostExpByExpAll CC5B = 0, wEnemyMonBaseStats D001 (5) halved |
| Yellow | 15:547B | DivideExpDataByNumMonsGainingExp | emulate `ret` (no split) |
| Yellow | 15:52EF | GainExperience `inc hl` after the trainer BoostExp | 2nd pass: hQuotient+2/+3 (FF97/8) halved |
| Yellow | 15:545F | GainExperience.done | end of pass |
| Crystal | 0F:4E65 | UpdateBattleStateAndExperienceAfterEnemyFaint `jr z` after IsAnyMonHoldingExpShare | decide; force Z |
| Crystal | 0F:4E88 | same, `ret z` after the 2nd IsAnyMonHoldingExpShare | clear Z, D = our mask (-> wBattleParticipantsNotFainted C664), wBackupEnemyMonBaseStats C720 (5) halved |
| Crystal | 0F:70D4 | GiveExperiencePoints.EvenlyDivideExpAmongParticipants | emulate `ret` |
| Crystal | 0F:6EF8 | GiveExperiencePoints `ldh a, [hQuotient+3]` after the Lucky Egg BoostExp | 2nd pass (C71F = 1): FFB5/6 halved |
| Crystal | 0F:70D1 | GiveExperiencePoints.done | end of pass |

Passive in link battles (Yellow wLinkState D12A = 4, Crystal wLinkMode C2DC != 0) and the Battle Tower. The opcode
byte at every hook is checked before installing. Verified by `dev/e2e/expshare_gb.py` (real battles, ALL PASS).

**Closing mid-award (all games).** The per-award steering lives in Python (`handle.busy()`), so no resume snapshot
is taken while busy. On close/leave/travel the app first lets the game run (no buttons held, ≤ 3 s) until the award
is over (`App._settle_exp_award`). If it still waits for a button: in the main game the last snapshot (before this
battle) is kept; in a Soul Link the hooks are removed (Platinum undoes its changes) and the snapshot is taken anyway,
so the rest of that one award follows the game's own rule - an older resume would make the referee treat mons caught
since then as gone.

## 7. Challenges (Soul Link) — added in 0.6.0

A challenge is a co-op run between two players on **fresh, separate saves** of the same game (Yellow, Crystal or
Platinum - Platinum since 0.7.4).
It only runs while **both** players are in it. The server keeps the challenge record and an append-only **ledger**
of rule events; each client derives the rule state from the ledger (`client/yellowlink/soullink.py`) and enforces
it in its own game.

### Local storage (client)
`<server folder>/challenges/<challenge_id>/game.sav` (server folder: see section 8; always the server's OWN folder,
also when it shares another server's main save - a run belongs to the server it's played on. Clients up to 0.7.3 put
a sharing server's runs in the shared save's folder: such a run is found wherever it is - the app folder or any
`servers/<id>/` folder, also after the save was un-shared (run ids are unique)), `resume.state`, `game.rtc` (Crystal). Platinum: `game.sav` = the 512 KB
battery, `resume.state` = a DeSmuME savestate. While playing a challenge,
`cfg["active_challenge"] = {"id", "game"}`; `Config.game_id/save_path/resume_path` point at the challenge.
The main game's library, saves, PC database and party snapshot are untouched. On a fresh start of the app
(e.g. after a crash) `active_challenge` is cleared, so the player always starts in their main game.
Inside a challenge: no PC-database sync, no trades (only trade evolutions with the partner, see "Trade evolutions
in a run"), no live/practice battles, no party publishing, and only players in the same challenge are drawn.

### Record (server, `data/challenges.json`)
```
{"id", "kind": "soul_link", "game": "yellow"|"crystal"|"platinum", "players": [uidA, uidB], "names": {uid: name},
 "rules": {"dupes": bool, "shiny": bool, "exp_share": bool}, "status": "active"|"completed"|"failed"|"abandoned",
 "created", "updated", "ended", "reason": str, "progress": {uid: {"badges": int, "hof": bool}},
 "ledger": [event, ...]}
```
Summaries (`ledger` omitted, plus `"online": [uid...]` = players currently in the session) go to clients in
`challenge_list` / `challenge_update`. At most one non-finished challenge per pair and kind.

### Messages
| client → server | fields | notes |
|---|---|---|
| `library` | `games` list[str], `challenge_games` list[str] (optional), `challenge_rules` list[str] (optional) | games the player has installed with the verified ROM (sent on connect); shown in roster as `games`. `challenge_games` (clients 0.7.4+) = the games this client can run a challenge in; `challenge_rules` (clients 0.7.4+) = optional run rules it knows, today `["exp_share"]`, plus (clients 0.7.6+) `trade_evo` = it can do Soul Link trade evolutions (below; not a rule, always allowed). Both shown in the roster under the same names (null = offline or older client). 0.7.7: `variants` = `{"platinum": [...]}` (section 10) |
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

Rules: `dupes` (default on), `shiny` (default on; not used in Yellow), `exp_share` (Exp Share All for BOTH players
of the run, default off; set when the run is created and never changed). A run whose record has no `exp_share`
(created before it existed) = off: the original games' rule. In a run the client applies the run's `exp_share`, not
the player's own Settings toggle (the main game keeps that). A client that doesn't know the rule would apply its own
setting, so the server refuses a request with `exp_share` on unless BOTH players listed `exp_share` in
`challenge_rules` ("X needs a newer YellowLink for Exp Share All in a Soul Link - update (U) or turn it off"); the
same check runs when such a run is continued. Off works with every client.

Which games (server, `challenges.can_play`): Yellow/Crystal need the game in `games` (unknown `games` = allowed,
as before). Platinum also needs it in `challenge_games`: 0.7.0-0.7.3 clients list `platinum` in `games` but have no
Platinum Soul Link, so they are never sent a Platinum request (the requester gets "X needs a newer YellowLink...") and
can't start one. The same check runs when a run is continued.

Session rules (server): a join from a player of an active challenge marks them present; when both are present →
`challenge_ready` to both. `challenge_leave` or a disconnect lasting > 30 s ends the session and the other player
gets `challenge_partner_left`. A player who loads in but whose partner doesn't join within 90 s also gets
`challenge_partner_left` (reason "didn't load in"). Requests expire after 60 s.

### Ledger events (`event` objects; server adds `seq`, `p`, `ts`)
| `t` | fields | meaning |
|---|---|---|
| `enc` | `area`, `dex` (national dex 0-493), `status`: "open"\|"caught"\|"failed"\|"skipped", `key` (when caught), `gift` bool, `shiny` bool | first encounter of an area for player `p`. "open" when a counting wild battle starts; then "caught"/"failed". "skipped" = didn't count (dupes clause, shiny clause, no balls yet) |
| `dead` | `key`, `why` | a mon fainted, was released, or vanished — permanently unusable |
| `loc` | `key`, `where`: "party"\|"box" | player moved a linked mon (PC rule propagates to the partner) |
| `swap` | `old`, `new` | in-game NPC trade: the link moves to the received mon |
| `balls` | | player has Poké Balls for the first time: the Nuzlocke rules (faint = dead, blackout = fail, first encounters) apply from here; gifts (starters) count before it |
| `hof` | | player entered the Hall of Fame |
| `blackout` | | whole party fainted → challenge failed |
| `evo` | `key`, `dex`, `into`, `nick`, `item` (optional), `evo` | trade evolution with the partner (0.7.6, below): player `p`'s mon `key` evolved from `dex` into `into` (national dex); `item` = name of the held item used up. Written **by the server only** (a client can't post it as a `challenge_event`). Display only: the key, the link, deaths and the dupes clause don't change |

Server reacts to `hof` (both players → status completed) and `blackout` (status failed). An `enc` left "open" when a
player reconnects or reloads is treated as **failed** (no rerolling by closing the game mid-battle). Rule
derivation (in `soullink.py`): two caught encounters in the same area = a linked pair; a caught mon whose partner's
encounter in that area failed is **unlinked** (must stay boxed); `dead` on either side kills both; `loc` to box
on one side boxes the partner.

### Trade evolutions in a run — added in 0.7.6
Trades stay off inside a run (no swaps between the two players, nothing to or from the main game), but trade
evolutions are **always allowed** (Ilias's decision; not a run rule): a "trade and trade back" with the run partner.
The player picks one of their **party** Pokémon that would evolve by trade (Soul Link panel → "Trade evolution");
the partner gets a prompt "Help Ilias evolve Kadabra? [Yes/No]"; on yes the mon "goes over and comes straight back"
and evolves in its owner's game. Nothing of it ever reaches the partner's game.

Which mons (client, `tradeevo.py`; the same evolution code as normal trades):
- Yellow: Kadabra, Machoke, Graveler, Haunter (`game.TRADE_EVOLUTIONS` / `game.evolve_for_trade`).
- Crystal: those four + Poliwhirl/Slowpoke holding a King's Rock, Onix/Scyther + Metal Coat, Seadra + Dragon Scale,
  Porygon + Up-Grade (`crystal.TRADE_EVOLUTIONS` / `crystal.evolve_for_trade`). Everstone blocks it; eggs never.
- Platinum: every trade evolution in the player's ROM (`ndsrom.PlatinumData.trade_evolutions()`: the Crystal ones
  plus Porygon2 + Dubious Disc, Rhydon + Protector, Electabuzz + Electirizer, Magmar + Magmarizer, Dusclops + Reaper
  Cloth, Clamperl + Deep Sea Tooth/Scale). Everstone blocks it; eggs never.
- A mon that needs an item but doesn't hold it, holds an Everstone, or is dead in the run is listed but can't be
  picked. The held item is used up like in the games.

The evolved mon stays the owner's own: same OT name/ID (no traded exp boost, no disobedience), same PID/DVs, so the
same **Soul Link key** (Yellow OT ID + DVs + catch-rate byte, Crystal OT ID + DVs + caught data, Platinum PID + TID +
SID - none of them change), so its link, death/box tracking, the dupes clause (evolution families) and badge keys
(family root) are untouched. Nickname kept unless it is the species name (then the new species' name, like the
games). Stats are recalculated at the same level; current HP keeps its damage (Gen 1/2: new max - (old max - HP);
Platinum: HP + (new max - old max); a fainted mon stays at 0). The Pokédex gets the new species (caught + seen).
No evolution scene and no new moves at evolution (like normal YellowLink trades): it happens instantly with a toast
on both sides.

Flow (one trade evolution per run at a time; the server forwards and gates, the requester's client does the RAM
write when its game is safe, `is_safe()`; Platinum also needs the party to read identically on an earlier frame,
`observe_stable()`):

| dir | type | fields | notes |
|---|---|---|---|
| C→S | `challenge_evo_request` | `challenge_id`, `evo_id` (client-made, 1-16 letters/digits), `key`, `dex`, `into`, `nick` (≤ 12 chars), `item` (name or null) | malformed fields, an unknown run or a (`dex`, `into`) that isn't a trade evolution **of the run's game** (Yellow: the four Gen-1 ones; Crystal: the Gen-2 list; Platinum: the Gen-4 list): error (red). A well-formed request that can't happen right now is answered with `challenge_evo_end` (`ok` false) to the sender only: "the run is over", "not both in the run" (the session isn't ready, or a player is reconnecting), "X needs a newer YellowLink (press U to update)" (the partner's client doesn't list `trade_evo`), "busy" (another trade evolution is open in this run - e.g. both asked at once: the first one wins, the other player then gets the question), "wait a moment" (within 20 s after this player's last request was declined or answered busy) |
| S→C | `challenge_evo_request` | `challenge_id`, `evo_id`, `from`, `name`, `key`, `dex`, `into`, `nick`, `item` | to the partner; expires after 60 s (`challenge_evo_end` "no answer" to both) |
| C→S | `challenge_evo_respond` | `challenge_id`, `evo_id`, `accept`, `busy` (optional bool) | by the partner. Unknown/finished `evo_id`: ignored. Accept needs both still in the run (else end "not both in the run"). A client that can't show the question (another question is up) answers `accept: false, busy: true` -> end "partner busy" instead of "declined". Both start the requester's 20 s cooldown |
| S→C | `challenge_evo_go` | `challenge_id`, `evo_id` | to the requester: evolve it now (it waits for a safe moment, at most 30 s; time while the run is paused doesn't count). Right after the RAM write it takes a resume snapshot (`autosave(force=True)`), then sends `done` |
| C→S | `challenge_evo_done` | `challenge_id`, `evo_id`, `ok` bool, `why` (optional, ≤ 30) | by the requester: `ok` = written to the game (send right after the write). `ok: false` before `go` = cancel ("cancelled"; "no answer" when the requester saw nothing for 75 s; "connection lost" when it reconnected while asking); after `go` = couldn't (`why` is neutral, the partner sees it: "not in the party anymore", "lost in this run", "no quiet moment", "connection lost"...). Ends it for both right away, no cooldown. An unknown `evo_id` is answered with `challenge_evo_end` (`ok` false, "expired") to the sender only, so a resent `done` stops |
| S→C | `challenge_evo_end` | `challenge_id`, `evo_id`, `ok`, `reason` | to both: over. On `ok` the server first appends the ledger event `evo` (echoed to both as a normal `challenge_event`); reasons: "declined", "no answer", "cancelled", "not both in the run", "session over", "timed out", or the requester's `why` (plus the refusals above, to the requester only) |

Server timers: an unanswered request ends after 60 s; after `go`, if no `done` arrives within 180 s the evolution
ends "timed out". When the run's session ends (leave, disconnect past the grace time, completed, failed) every open
trade evolution of it ends "session over" (also when a new session replaces an old one). The server takes an evolution
off its open list before writing the ledger, so a `done` resent behind the first one gives one `evo` event and one end
(the resend gets "expired"). A requester who reconnects (`challenge_state`) resends a `done` it couldn't send and
cancels one still asking or at go ("connection lost"). Clients keep the ids they ended themselves, so the server's
end for those is silent (the player was told already); the partner is told "Ilias's Kadabra didn't evolve (...)".
Clients 0.7.6+ only offer the button when the server lists `trade_evo` in `welcome.features` and the partner's
roster entry lists `trade_evo` in `challenge_rules`; older clients never see any of these messages.

### Game helpers used by the referee (verified on the real ROMs)
`GameMemory` / `CrystalMemory`: `battle_info()`, `battle_party_slot()`, `has_poke_balls()`, `in_hall_of_fame()`,
`current_box_records()`, `move_party_to_box()`, `move_box_to_party()`, `daycare_records()`, module `mon_key()`,
`AREAS`/`area_of()`/`area_name()`. Addresses, frame timings and gotchas: `dev/notes/soullink_yellow.md` and
`dev/notes/soullink_crystal.md`; checks: `dev/e2e/soullink_mem_yellow.py` (81) and `dev/crystal/soullink_verify.py` (109).
Key facts: Yellow `battle_info` kind/dex are valid from the first frame of `wIsInBattle`, its `shiny` (badges 0.7.5:
Gen-2 DV rule, so the shiny clause applies in Yellow too) only from the next frame (LoadEnemyMonData writes the DVs
right after wIsInBattle is set), so `ChallengeView.battle()` returns None on a Yellow battle's first frame; Crystal's
is None on frame 0 and valid from frame 1 (the referee waits for the first non-None). A faint counts when `battle_party_slot()` shows HP 0 on two
frames in a row. Withdrawing heals in Crystal, so deaths are tracked by `mon_key`, never by HP. Bug-Catching Contest
battles are free; the kept mon arrives later as the National Park "gift". Only call the forced moves while `is_safe()`.

**Pokémon Platinum** (`PlatinumMemory`, platinum.py "Soul Link" section by the same names + `badge_mask()`,
module `mon_key()`/`mon_info()`/`area_of()`/`area_name()`; details: `dev/notes/soullink_platinum.md`):
- Areas = the map's location label (`twinleaf_town` for the town and every house in it, `route_201`, ...).
- Mon key = PID + TID + SID (16 hex digits): unchanged by level-up, evolution, PC, Day Care, hatching. Dex = Gen-4
  species id (= national dex). The dupes clause uses evolution families for all 493 species (`soullink.FAMILY`).
- The game decrypts records in place mid-frame: the party is read with `party_checked()` (None = read again next
  frame); box/Day Care records are normalised (`platinum_pc.normalise`); a slot that can't be read makes the full PC
  scan return None (no "released" verdict from an unreadable slot). `current_box_records()` leaves empty/unreadable
  slots out; `move_box_to_party(i)` takes an index into that list.
- The forced moves rebuild the party/box record like the game (PP, stats) and need the ROM data:
  in a challenge the app sets `PlatinumMemory.rom` (ndsrom.PlatinumData).
- Catches are matched by key: `battle_info()` lists every wild mon (`keys`, `dexes`; Yellow/Crystal give one `dex`
  and no key -> matched by species as before). The mons the save already holds (party + all 18 boxes) are noted at
  the battle's start, because a catch with a full party and a full current box lands in another box (which becomes
  the current one) next to older mons. Double wild battles (walking with a partner): the encounter is the first wild
  mon the dupes clause allows ("open" with its dex); catching it -> "caught" with the dex/key of the mon actually
  caught; catching the other one (a dupe) -> the encounter "failed" and that catch is dead ("not first encounter");
  with a shiny among them the outcome is decided by the mon caught (shiny -> bonus area key).
- Blackout = the game's own verdict: `battle_info()["result"]` (FieldBattleDTO.resultMask, written on the battle's
  last ~7 frames: 1 won, 2 lost, 3 draw, 4 caught, 5 fled). The field whites out only on lost/draw
  (CheckPlayerWonBattle), so in a partner (tag) battle all of your Pokémon can faint while the partner wins: no
  blackout, the party is healed - the deaths still count (and no usable Pokémon left still ends the run).
  Yellow/Crystal have no result: blackout = every party mon fainted, as before.
- Field poison can't kill in Platinum: `Pokemon_DoPoisonDamage` stops at 1 HP and cures the mon (checked on the ROM
  in soullink_platinum.py), so there is no out-of-battle faint or white-out to watch for.
- `daycare_records()` None (a slot can't be read right now) skips that full PC scan; a PC slot that stays
  unreadable (a real Bad Egg) is logged once and keeps release detection off while it's there.
- Forced moves write only what read identically on an earlier frame (`observe_stable()`, sampled by the view's
  `safe()`); without the ROM data they can't be done (logged once, the player is asked instead).
- No hold state (no Bug-Catching Contest). The Great Marsh counts like any area (Safari Balls count as balls while
  the safari game runs).
- The PC: no PC-database hook inside a challenge, so the box UI running (`pc_open()`) is polled every frame and given
  to the referee as PC open/close.
- Friends: the Sinnoh `state` carries `challenge` while in a run; only players in the same run are drawn.
- While the run is paused (partner loading / reconnecting / own connection) DeSmuME isn't ticked and its sound
  device is paused. If the DS emulator can't start inside a challenge, `active_challenge` is dropped (main game
  next start; the partner gets "didn't load in").

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
  layout. Saves, resume snapshots, backups, the PC database and challenge saves all live there (challenge saves stay
  in the server's own folder even when it shares another server's save, see section 7).
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
- **One save on several servers** (0.7.2): an entry can use another entry's save (`save_dir` = that entry's id,
  `save_uid` = its player id; `Config.share_save`, no chains, an entry whose save others use can't switch to another
  save). `data_dir` follows `save_dir`; `pc_path` = that folder's `pc/<save_uid>.json`; the player keeps their own
  `uid` per server (each group sees its own player). The PC store then syncs with several servers: `PCSync(path,
  server_key, home_key)` keeps `sync[server_key] = {server_rev, synced}` per server (older files: the top-level copy
  belongs to the home server, and is still written for older versions), and trade locks remember their server
  (`lock_servers`): only that server's `trade_status`/`trade_cancelled` can release them. While the active entry
  shares a save, trades wait until this server confirmed our PC (`pc_ready`, set by `pc_saved`). Every entry that
  leaves the store (commit, trade) is listed in `left` (last 1000); a merge never brings a `left` entry back, so an
  outdated server copy (lost upload confirmation, another server sharing the save) can't duplicate a Pokémon. On
  close the client waits up to 3 s for the last upload's confirmation. Changing the save of the server you're on is
  applied after the game closed (`App.pending_share` in `_main`).

## 9. Badges — added in 0.7.5

Per region (`kanto` = Yellow, `johto` = Crystal, `sinnoh` = Platinum), shown as a row of sockets in the Regions panel
(your own) and in the Multiplayer panel (a friend's, when you click them). Badge art is drawn by our code
(`client/yellowlink/badge_art.py`), never taken from the games.

| badge | earned when (main game only, never inside a Soul Link run) |
|---|---|
| Champion | entered the Hall of Fame in that region's game |
| Pokédex | caught (owned flag) the regional dex: Kanto 150 (all but Mew), Johto 249 (all 251 but Mew + Celebi), Sinnoh 209 (the 210 of Platinum's Sinnoh dex but Manaphy: event-only mythicals are left out, like the in-game diplomas leave out Mew) |
| Shiny hunter | caught >= 1 shiny in that game; tiers 1/10/25/50/100 by count of distinct own shiny catches (OT + TID, origin that game), remembered for good once seen (party, boxes, Day Care). Yellow: a mon whose DVs would be shiny in Gen 2 (Def/Spd/Spc 10, Atk 2,3,6,7,10,11,14,15) |
| Soul Link | entered the Hall of Fame in a Soul Link run of that region's game: **gold** = run rule `exp_share` off, **silver** = on. Runs without the rule (created before 0.7.4): gold if that Hall of Fame was before 0.7.3 came out (2026-10-26 14:39:30 UTC, unix 1793025570 - no Exp Share All yet), silver from then on (0.7.3 applied the personal setting, default on, inside runs). Gold outranks silver |

Badges are never taken away (a new game, trading a shiny away, releasing it: all kept). Retroactive: a save that
already has them earns them on first detection.

### Client (detection + local book)
Detection is per game (`badges.py` rules + the adapters' `progress(mem)`; badges-game's part). Every ~10 s (on the
first safe frame once it's due, and right after the PC closes) in the main game (`is_safe()`, not in a run, trade or battle) the app calls `self.G.progress(self.game)`,
merges it into its **badge book** (`badgesync.BadgeBook.apply`: never removes) and on anything new shows a toast
("Badge earned: Kanto Champion!") and reports the book. The book lives next to the save, in the save's folder, named
like the PC store: `<data_dir>/badges/<save_uid or uid>.json` = `{"version": 1, "earned": {region: {"champion": ts, "dex": ts, "shinies": [key, ...]}},
"soullink": {server_id: {region: "gold"|"silver"}}}` (`soullink` = what each server last said, for display while
offline). A save shared by several servers (section 8) shares the book, so every server it is played on gets the
same main-game badges (reported on connect).

### Protocol
The server lists `"badges"` in `welcome.features`; clients only send `badges` / `get_badges` to such servers (an
older server would answer an unknown type with an error shown in red). Old clients send nothing = no badges.

| dir | type | fields |
|---|---|---|
| C→S | `badges` | `earned` = `{region: {"champion": ts\|null, "dex": ts\|null, "shinies": [key, ...]}}` — the whole main-game book. Sent after `welcome` and whenever something new was earned |
| C→S | `get_badges` | `uid` |
| S→C | `badges_data` | `uid`, `name`, `badges` = `{region: BadgeView}` |

BadgeView = `{"champion": ts|null, "dex": ts|null, "shiny": int, "soullink": "gold"|"silver"|null,
"soullink_ts": ts|null}`; the roster entry carries the compact form (bools + count + soullink, see RosterEntry).

Server checks of `badges`: only a wrong shape is an error (`earned` not an object, a known region not an object,
`shinies` not a list). Everything else is tolerated so a player can never get stuck with a red error on every
connect: unknown regions are skipped (room for new regions later); `champion`/`dex` null/absent/false = not
earned, `true` or a time outside 1e9 <= ts <= server now + 1 day (a wrong PC clock) = server now; shiny keys that
aren't strings matching `[A-Za-z0-9_:-]{1,40}` are dropped, duplicates too, at most 1000 kept; other fields (e.g.
`soullink`) ignored — Soul Link badges only come from the run ledger. The client sends `true` for an earn time its
own clock got wrong (before 2001, or more than a day ahead) and never stores a time before 2001.

Server storage: `<data_dir>/badges.json` = `{"version": 1, "players": {uid: {region: {"champion": ts, "dex": ts,
"shinies": [key...], "soullink": "gold"|"silver", "soullink_ts": ts}}}, "reported": {uid: ts of their first
report}}` (atomic write, debounced). Merging a report: union of shiny keys (capped at 1000 per region: the count
saturates there), earliest timestamp kept, nothing ever removed (only a future admin tool may). When something new
arrives the roster is re-sent and a system message tells everyone ("Ilias earned the Kanto Champion badge!", "...
completed the Kanto Pokédex!", "... reached Shiny hunter 10 in Kanto!"). Badges from before are not announced one
by one: everything in a player's FIRST report (e.g. the first connect after updating, with an old save), and any
Champion/Pokédex earned more than 5 minutes before the report (played offline), gives one line at most:
"Ilias has 5 badges".

Soul Link: when the ledger gets a `hof` event of player p (section 7), the server awards p the Soul Link badge of
the run's region (gold/silver as in the table above), saves it, re-sends the roster and tells everyone. At
startup it also awards it for every `hof` already in `challenges.json` (runs completed before 0.7.5). The client
toasts it when its own roster entry shows a new or better Soul Link badge (the referee's own Hall of Fame message
doesn't mention the badge). A server without the `badges` feature: friends' badges show "Badges need a server
update" instead of empty sockets. A badge book that isn't valid JSON of the right shape is kept aside as
`badges.bad-<time>.json` and a fresh one is started (detection fills it again). `merge.py` (section 8) copies badges.json entries like players.

## 10. Definitive Platinum — added in 0.7.7

"Pokémon Definitive Platinum" by BigNation (PokéCommunity: https://www.pokecommunity.com/threads/pok%C3%A9mon-definitive-platinum-complete.539178/)
is a second **variant** of the Sinnoh game. The game id stays `platinum` everywhere (state, roster, PC store, badges,
challenges); only the ROM file differs. YellowLink never ships the hack or the patch: the server's host puts BigNation's
two xdelta patches in the server's data folder, and each client patches **the player's own** Platinum.

| variant | ROM | SHA1 |
|---|---|---|
| `vanilla` | Pokémon Platinum (USA) rev 0 | `ce81046eda7d232513069519cb2085349896dec7` |
| `definitive` | rev 0 or rev 1 + BigNation's patch (both patches give the same file, 105,001,364 bytes, code `CPUE`) | `2f368f0f68f98058699a8dd0b1f9af3c970172a2` |
| (rev 1) | Pokémon Platinum (USA) rev 1: header game code `CPUE` and ROM version byte 0x1E = 1. Not playable as vanilla, only a patch source (the patch's Adler-32 checks + the final SHA1 prove it) | - |

### Server
- Patch files: `<data_dir>/patches/definitive_platinum/rev0.xdelta` and `rev1.xdelta` (Docker: `server/data/...` on
  the host). Looked at on start and again on every `hello` (size + mtime; the SHA1 is recomputed only when a file
  changed, so the host can add them without a restart). A file is offered only if it starts with the VCDIFF magic
  `D6 C3 C4` and is at most 64 MB.
- `YL_SINNOH` (config `sinnoh`): `any` (default: players choose vanilla or Definitive) | `definitive` (the server asks
  every player to switch; new Platinum Soul Link runs are Definitive). Anything else = `any` (logged).
- `welcome` gets `features` + `"defplat"` (the messages below are known), `sinnoh` (`"any"` | `"definitive"`) and
  `patches` = `{"definitive_platinum": {"rev0": {"size": int, "sha1": hex}, "rev1": {...}}}` (only the files that
  are there; `{}` when none).

| dir | type | fields |
|---|---|---|
| C→S | `patch_get` | `name` = `"definitive_platinum"`, `rev` = `"rev0"` \| `"rev1"`, `sha1` (of the file the client is downloading, from `welcome`), `offset` (>= 0), `length` (1..1 MiB) |
| S→C | `patch_chunk` | `name`, `rev`, `sha1`, `size` (whole file), `offset`, `data` (base64, at most 48 KiB raw per chunk); one request is answered by consecutive chunks covering `[offset, min(offset+length, size))` |
| S→C | `patch_error` | `name`, `rev`, `reason`: `"missing"` (no such file now) \| `"changed"` (the file's SHA1 isn't `sha1` any more; `sha1` + `size` of the current file are included) \| `"bad"` (bad request), `message` |

One range at a time per client: a new `patch_get` cancels the chunks still to be sent for the previous one, and a
disconnect cancels everything. Chunks are sent by a per-client task (`await send` = backpressure), so a download never
holds up other players or that player's own messages. `patch_get` counts against the normal rate limit (1 per MiB).

### Client
- **Library** (`library.py`): `cfg["games"]["platinum"]` stays the vanilla rev 0 file (`roms/platinum.nds`, as
  before); other variants in `cfg["variants"]["platinum"]` = `{"definitive": path, "rev1": path}`
  (`roms/platinum-definitive.nds`, `roms/platinum-rev1.nds`). "Add a game file" accepts all three (rev 1 by header).
  `cfg["variant"]["platinum"]` = the player's pick. The variant used: in a run, the run's `variant`; else
  Definitive when the server (last `welcome.sinnoh`, remembered in the server entry as `sinnoh`) requires it and the
  player has it; else the pick if that file exists; else whichever exists (vanilla first). `Library.games` lists
  `platinum` when vanilla or Definitive exists (rev 1 alone: no).
- `App.full` on Definitive = the Platinum adapter lists its SHA1 in `platinum.SUPPORTED_SHA1S` (defplat-game owns it).
- **Making it** (`defplat.py`, Regions panel, Sinnoh card): when the server offers a patch for a Platinum the player
  has (rev 0 preferred, else rev 1) and they don't have Definitive yet: "Make Definitive Platinum from your Platinum"
  (optional) — or, with `sinnoh: definitive`, a prompt when they play vanilla Sinnoh (and a chat line elsewhere).
  Download (`patch_get` ranges of 1 MiB to `<appdir>/downloads/definitive_platinum-<rev>-<sha1[:12]>.xdelta.part`,
  resumed from the part's size after a reconnect/restart, restarted on `changed`; the finished file's SHA1 must match),
  then patch in a background thread (`vcdiff.decode_file`, progress bar; result written as `.part`, kept only if the
  SHA1 is `2f368f0f…`), store it as `roms/platinum-definitive.nds`, delete the patch, pick Definitive. The player's
  original ROM is never written. A wrong ROM (e.g. a damaged rev 1) ends with "This Platinum file can't be made into
  Definitive Platinum" and nothing is stored.
- **Switching** (Regions, Sinnoh card: "Play with: Platinum | Definitive", hidden when the server requires Definitive)
  = travel (save + restart). Both variants use the same battery save `saves/platinum.sav` (BigNation: vanilla saves
  stay compatible). Before the first start of Definitive with an existing save, the save is copied once per server
  folder to `saves/backups/before-definitive-platinum-<time>.sav` (never pruned). Resume snapshots remember the ROM
  they were taken on (`<resume>.rom` = SHA1); a snapshot from the other variant is never loaded (kept as
  `.other-rom`), the game starts from the battery save instead (so: save in-game before switching; the switch
  button says so). A snapshot without `.rom` is from vanilla (older clients).
- `library` gets `variants` = `{"platinum": ["vanilla", "definitive"]}` (the playable ones); the server keeps it on the
  client and in the roster entry (`variants`, null = offline or older client: treated as `["vanilla"]` if `games` has
  `platinum`).

### Soul Link
A Platinum run has a `variant` (`"vanilla"` | `"definitive"`; missing = vanilla, also for every run before 0.7.7).
`challenge_request` (new run, game `platinum`) takes `variant` (default vanilla); on a `sinnoh: definitive` server it is
always `definitive`. Both players must have that variant (from `library.variants`), checked again when the run is
continued: "X doesn't have Definitive Platinum yet (Regions → Make Definitive Platinum)" / "X needs a newer
YellowLink for Definitive Platinum (press U)". The record and its summaries carry `variant`; `challenge_request`
(server → client) too. The client plays the run on that variant's ROM (`active_challenge.variant`).
