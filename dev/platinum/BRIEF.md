# Pokémon Platinum in YellowLink — shared brief for all agents

Repo: `/home/claude/yellowlink` (branch `platinum`). Read `CLAUDE.md`, `SPEC.md` (sections 5 PC database, 2 trades,
8 servers) and `MEMORY.md` first. Python 3.13 here; the shipped exe is Windows/Python 3.12 (PyInstaller).

## Goal
Add **Pokémon Platinum (US, rev 0, SHA1 `ce81046eda7d232513069519cb2085349896dec7`)** as a third game/region
(**Sinnoh**) with the features Yellow/Crystal have, except Soul Link (later), live/practice battles and wide view
(those are Yellow-only anyway):
1. Play Platinum inside YellowLink (both DS screens, mouse = touch screen, keyboard/gamepad, sound, in-game saves,
   resume snapshots, per-server save folders, fast-forward toggle, travel between regions, chat/roster/region names).
2. PC database: Platinum's boxes are a *view* of the player's YellowLink PC store (like Yellow/Crystal), with
   Gen1/Gen2 ↔ Gen4 conversion and `?NAME` stand-ins for Pokémon a game can't hold. Trades/gifts across all games.
3. Friends visible walking around in Sinnoh (other Platinum players on the same map).

## Facts already established
- Emulator: **py-desmume 0.0.9** (`pip install py-desmume`; Windows wheel `py_desmume-0.0.9-cp312-cp312-win_amd64.whl`
  ships `desmume/libdesmume.dll` + `desmume/SDL2.dll`). DeSmuME 0.9.12, interpreter CPU, ~200 fps headless here.
- **Only ONE DeSmuME instance per process** (libdesmume is global). Two-Platinum-player tests need two processes.
- Headless here needs `SDL_AUDIODRIVER=dummy` (else `desmume_init` fails: no audio device).
- API: `emu.open(path)`, `emu.cycle(with_joystick=False)` (1 frame), `emu.input.keypad_add_key/rm_key(keymask(Keys.KEY_A))`,
  `emu.input.touch_set_pos(x,y)/touch_release()`, `emu.screenshot()` (PIL 256x384: top above bottom),
  `emu.display_buffer_as_rgbx()`, `emu.savestate.save_file/load_file(path)`, `emu.backup.import_file(path)/export_file(path)`
  (battery save), `emu.memory.unsigned.read_byte/short/long(addr)`, `emu.memory.write_byte/short/long`,
  `emu.memory.register_exec(addr, cb)` (exec hook), `emu.volume_set(0..100)`. Source:
  `/usr/local/lib/python3.13/site-packages/desmume/emulator.py`.
- `dev/platinum/ds.py` = small shared driver (press/hold/touch/run/shot/save/load/r8..r32). Use it in dev scripts.
- ROM for dev: `dev/states/platinum.nds` (gitignored). Yellow/Crystal ROMs + states are also in `dev/states/`.
- Reference: `/home/claude/pokeplatinum` = pret decompilation (C source + structs; **no symbol addresses** — the
  build isn't available). Find addresses by reading structs/code there **and verifying in the running ROM**
  (e.g. scan RAM for structures with valid checksums, follow pointers). Never contribute/post anything to pret.
- Hard project rules (CLAUDE.md): never commit/distribute ROMs, saves, states or Nintendo assets (all graphics must
  come from the player's own ROM at runtime); every address verified on the real ROM; game-state writes only when
  safe; protocol changes go in SPEC.md first. **Agents do not `git commit`/push** — the coordinator commits.

## Interfaces (agree on these; don't change them without writing it in dev/platinum/STATUS.md)
**Memory bus** (provided by `client/yellowlink/nds_core.py` `NDSCore`, also by `dev/platinum/ds.py` `DS`):
`read(addr, n) -> bytes`, `r8/r16/r32(addr)`, `write(addr, data: bytes)`, `w8/w16/w32(addr, v)`.

**`client/yellowlink/platinum.py`** (adapter module, like `game.py`/`crystal.py`; the app uses it as `self.G`):
- `PLATINUM_SHA1`, `class PlatinumMemory(bus)` with: `in_game()`, `is_safe()`, `in_battle()`, `walking()`,
  `map_id()`, `position() -> dict` (at least `map`, `x`, `y` (tile coords, y = the DS "z" axis), `facing`,
  `moving`, `gender`; plus any sub-tile/height info the friend renderer needs), `player_name()`, `trainer_id()`,
  `badges()` (count), `party_count()`, `party_raw() -> list[bytes]` (encrypted 236-byte party records),
  `box_base()` (address of the PC box storage or None), `pc_open()` (Pokémon Storage System UI active),
  `save_loaded()`.
- Module-level helpers mirroring game.py/crystal.py where the app needs them: `area_of(map_id)`, `area_name(area)`,
  `fast_continue(core, on_progress)` (title screen → Continue into the saved game, returns a short result string).

**`client/yellowlink/pk4.py`**: `decrypt(box_or_party_bytes) -> bytes`, `encrypt(plain) -> bytes`, `checksum(plain)`,
`class PK4` field access (species, level/exp, nickname, OT name, TID/SID, moves, PP, IVs/EVs, held item, PID,
nature, ability, gender, shiny, met data, language, version), Gen-4 text codec, and conversions
`from_gen1(rec55, rom_ctx) / from_gen2(rec, rom_ctx) / to_gen1(...) / to_gen2(...)` returning None when the target
generation can't hold the Pokémon (→ stand-in). `client/yellowlink/ndsrom.py`: read files from the player's .nds
(FNT/FAT + NARC), e.g. personal data (growth rates, abilities, gender ratio) and sprites.

**PC store entries** (SPEC.md §5) gain `"gen4": "<b64 136-byte encrypted box PK4>" | null`. The store stays
14 boxes x 20 (Platinum shows store box i in its box i, slots 1-20; anything deposited elsewhere is compacted into
free store slots on close). The server (`server/yellowlink_server/pcstore.py`) must accept `gen4` (136 bytes) and
apply Gen-4 trade evolutions without a ROM.

## File ownership (to avoid edit collisions)
- Explorer: `client/yellowlink/platinum.py`, `dev/platinum/*` (except BRIEF.md), `dev/states/pt_*`.
- PK4 agent: `client/yellowlink/pk4.py`, `client/yellowlink/ndsrom.py`, `client/tests/test_pk4.py`,
  `client/tests/test_ndsrom.py`, `server/yellowlink_server/pcstore.py`, `server/tests/test_pcstore_gen4.py`.
- Core agent: `client/yellowlink/nds_core.py`, `app.py`, `library.py`, `ui.py`, `panels.py`, `setup_dialog.py`,
  `config.py`, `client/yellowlink.spec`, `client/requirements.txt`, `.github/workflows/*`, `dev/e2e/platinum_*.py`.
- Anyone may append to `dev/platinum/STATUS.md` (short dated notes: what's done, where files are, open questions).
