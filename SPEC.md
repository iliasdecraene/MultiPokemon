# YellowLink — technical spec (source of truth)

Private multiplayer layer for Pokémon Yellow (USA/Europe, SHA1 `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1`).
Each player runs their own single-player game in our client (PyBoy emulator, headless, rendered in our own pygame window).
The client reads/writes game RAM and talks to a small relay server over WebSocket. **No ROM, no Nintendo assets in the repo** —
each player supplies their own ROM; all graphics come from the running game's VRAM.

Features (v1):
1. **See friends walk around** — players on the same map are drawn into your screen with their real sprite + name tag.
2. **Trade** — server-mediated two-phase trade; each client writes the received Pokémon into its party RAM. Trade evolutions handled client-side.
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
{"data": "<b64 44 bytes party_struct>", "ot": "<b64 11 bytes>", "nick": "<b64 11 bytes>"}
```
Server treats these as opaque except for validation: data decodes to exactly 44 bytes, ot/nick exactly 11 bytes, species byte (data[0]) != 0.

### Client -> Server
| type | fields | notes |
|---|---|---|
| `hello` | `name` (1-10 chars, A-Z a-z 0-9 space), `uid` (client-generated uuid4 string, persistent), `key` (room password), `version` (client semver), `protocol` (int) | must be first message. Wrong key -> `error` + close(4001). Duplicate uid already connected -> the old connection is kicked (close 4002). |
| `state` | `map` int, `x` int, `y` int, `px` int, `py` int, `facing` int (0 down,4 up,8 left,12 right), `img` int (sprite image index byte 0..15), `visible` bool | ~10/s, only when changed (plus a keepalive every 2 s). px/py = pixel position in map space (tile*16 + walk offset). |
| `sprite` | `data` b64 (384 bytes: 12 tiles @VRAM 0x8000 + 12 tiles @0x8800) | send on change only |
| `party` | `mons` list[Mon] (0..6), `trainer` str (player's in-game name, decoded), `badges` int (bitfield) | send on change (client checks every ~3 s) |
| `chat` | `text` (<=200 chars) | |
| `get_party` | `uid` | ask for someone's latest party snapshot (online or offline) |
| `battle_result` | `opponent_uid`, `won` bool | server broadcasts a system message |
| `trade_request` | `to` uid | target must be online, neither in a trade |
| `trade_respond` | `trade_id`, `accept` bool | by the target |
| `trade_offer` | `trade_id`, `slot` int 0-5, `mon` Mon | may be re-sent to change offer; changing resets both confirmations |
| `trade_confirm` | `trade_id` | only valid when both have offered |
| `trade_cancel` | `trade_id` | either side, any time before execute |
| `trade_done` | `trade_id`, `ok` bool | after the client wrote the received mon into RAM |
| `trade_status` | `trade_id` | ask the final outcome of a trade (used after reconnecting mid-trade) |
| `ping` | `t` | server answers `pong` with same `t` |

### Server -> Client
| type | fields |
|---|---|
| `welcome` | `you` uid, `players` list[PlayerInfo], `roster` list[RosterEntry], `latest_client` str, `download_url` str, `motd` str |
| `player_join` | `player` PlayerInfo |
| `player_leave` | `uid` |
| `state` | `uid` + all state fields (relayed to everyone else) |
| `sprite` | `uid`, `data` (also replayed to newly joined clients for all online players) |
| `chat` | `uid`, `name`, `text` |
| `system` | `text` (join/leave/battle results/trade completed) |
| `roster` | `roster` list[RosterEntry] (re-sent whenever any party snapshot changes or someone joins/leaves) |
| `party_data` | `uid`, `name`, `trainer`, `mons` list[Mon] (or `error` if unknown) |
| `trade_request` | `trade_id`, `from` uid, `name` |
| `trade_started` | `trade_id`, `with` uid, `name` (sent to both after accept) |
| `trade_offer` | `trade_id`, `from` uid, `slot`, `mon` (sent to the *other* side; the offerer gets nothing back) |
| `trade_confirm_state` | `trade_id`, `you` bool, `them` bool |
| `trade_execute` | `trade_id`, `give_slot` int (your slot to overwrite), `receive` Mon |
| `trade_complete` | `trade_id` (both sides acked ok) |
| `trade_cancelled` | `trade_id`, `reason` str |
| `trade_status` | `trade_id`, `state`: `complete` \| `failed` \| `pending` \| `unknown` |
| `error` | `message` |
| `pong` | `t` |

PlayerInfo: `{uid, name, state: <last state obj or null>, has_sprite: bool}`
RosterEntry: `{uid, name, trainer, online: bool, party_size: int, levels: [int], species: [int], last_seen: unix_ts}`

### Trade state machine (server)
`requested` --accept--> `open` (both may offer/re-offer; any new offer clears confirmations)
`open` + both confirmed --> `executing`: server sends `trade_execute` to both simultaneously.
`executing` + both `trade_done ok` --> `complete` (system broadcast "A traded X with B"). If one side reports ok=false or disconnects during executing,
server sends `trade_cancelled` with reason "execution failed" to the other (best effort; client logs it).
Disconnect in requested/open -> `trade_cancelled` reason "partner left". Timeout: requested expires after 60 s.
The server remembers the outcome of every trade that reached `executing` (last 500). A client that lost its connection
after writing the received mon asks `trade_status` on reconnect and rolls back unless the answer is `complete`.
Clients send `trade_done ok` *before* writing RAM and only while connected.
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
* BaseStats: 0E:43DE → 0x3C3DE, 28 bytes/entry, index dexno-1: [dexno, hp, atk, def, spd, spc, type1, type2, ...].
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
