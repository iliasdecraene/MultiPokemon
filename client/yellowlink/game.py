"""Everything that touches Pokémon Yellow's RAM/ROM.

Addresses come from pret/pokeyellow symbols and were verified against the
USA/Europe ROM (see SPEC.md section 3). `mem` is anything indexable like
`pyboy.memory` (mem[addr] get/set); tests use a bytearray.
"""
from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass

from . import text

YELLOW_SHA1 = "cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1"

# --- RAM ---------------------------------------------------------------
PLAYER_PIC = 0xC100
PLAYER_IMG = 0xC102
PLAYER_YVEC = 0xC103
PLAYER_XVEC = 0xC105
PLAYER_FACING = 0xC109
JOY_IGNORE = 0xCD6B
BATTLE_RESULT = 0xCF0B
FONT_LOADED = 0xCFC3
WALK_COUNTER = 0xCFC4
TRAINER_NAME = 0xD049
IS_IN_BATTLE = 0xD056
CUR_OPPONENT = 0xD058
LONE_ATTACK_NO = 0xD05B
TRAINER_NO = 0xD05C
PLAYER_NAME = 0xD157
PARTY_COUNT = 0xD162
PARTY_SPECIES = 0xD163
PARTY_MONS = 0xD16A
PARTY_OT = 0xD272
PARTY_NICKS = 0xD2B4
DEX_OWNED = 0xD2F6
DEX_SEEN = 0xD309
BADGES = 0xD355
PLAYER_ID = 0xD358
CUR_MAP = 0xD35D
Y_COORD = 0xD360
X_COORD = 0xD361
ENEMY_PARTY_COUNT = 0xD89B
ENEMY_PARTY_SPECIES = 0xD89C
ENEMY_MONS = 0xD8A3
ENEMY_OT = 0xD9AB
ENEMY_NICKS = 0xD9ED

MON_SIZE = 44
NAME_SIZE = 11
VRAM_SPRITE_A = 0x8000  # 12 tiles: standing frames
VRAM_SPRITE_B = 0x8800  # 12 tiles: walking frames
SPRITE_BYTES = 384

GHOST_TRAINER_CLASS = 0x1A  # PROF_OAK: GenericAI, no item use
OPP_ID_OFFSET = 200

TRADE_EVOLUTIONS = {0x26: 0x95, 0x29: 0x7E, 0x27: 0x31, 0x93: 0x0E}

# --- ROM tables (file offsets) -----------------------------------------
ROM_MOVES = 0x38000          # 6 bytes/move, pp at +5, index move-1
ROM_BASE_STATS = 0x3C3DE     # 28 bytes/entry, index dexno-1
ROM_DEX_ORDER = 0x410B1      # index species-1 -> dexno
ROM_MON_NAMES = 0xE8000      # 10 bytes/species, index species-1
ROM_MOVE_NAMES = 0xBC000     # 0x50-terminated list


def sha1_of(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def b64(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


class RomData:
    """Read-only lookups into the player's own ROM."""

    def __init__(self, rom: bytes):
        self.rom = rom
        self._move_names = None

    @classmethod
    def from_file(cls, path: str) -> "RomData":
        with open(path, "rb") as f:
            return cls(f.read())

    def dex_no(self, species: int) -> int:
        if not 1 <= species <= 190:
            return 0
        return self.rom[ROM_DEX_ORDER + species - 1]

    def species_name(self, species: int) -> str:
        if not 1 <= species <= 190:
            return "?"
        o = ROM_MON_NAMES + (species - 1) * 10
        return text.decode(self.rom[o:o + 10])

    def species_name_raw(self, species: int) -> bytes:
        o = ROM_MON_NAMES + (species - 1) * 10
        raw = bytes(self.rom[o:o + 10])
        return (raw + bytes([text.TERMINATOR]))[:NAME_SIZE]

    def base_stats(self, species: int):
        """(hp, atk, def, spd, spc, type1, type2) or None."""
        dex = self.dex_no(species)
        if not 1 <= dex <= 151:
            return None
        o = ROM_BASE_STATS + (dex - 1) * 28
        return tuple(self.rom[o + 1:o + 8])

    def move_pp(self, move: int) -> int:
        if not 1 <= move <= 165:
            return 0
        return self.rom[ROM_MOVES + (move - 1) * 6 + 5]

    def move_name(self, move: int) -> str:
        if self._move_names is None:
            names, cur, o = [], [], ROM_MOVE_NAMES
            while len(names) < 165 and o < len(self.rom):
                v = self.rom[o]
                o += 1
                if v == text.TERMINATOR:
                    names.append(text.decode(bytes(cur)))
                    cur = []
                else:
                    cur.append(v)
            self._move_names = names
        if 1 <= move <= len(self._move_names):
            return self._move_names[move - 1]
        return "-"


# --- Mon helpers -------------------------------------------------------
@dataclass
class Mon:
    data: bytes  # 44
    ot: bytes    # 11
    nick: bytes  # 11

    @property
    def species(self) -> int:
        return self.data[0]

    @property
    def level(self) -> int:
        return self.data[33]

    @property
    def hp(self) -> int:
        return (self.data[1] << 8) | self.data[2]

    @property
    def max_hp(self) -> int:
        return (self.data[34] << 8) | self.data[35]

    @property
    def moves(self):
        return [m for m in self.data[8:12] if m]

    @property
    def ot_id(self) -> int:
        return (self.data[12] << 8) | self.data[13]

    def identity(self):
        """What must match for a party slot to still be 'the same mon'."""
        return (self.data[0], self.data[12:14], self.data[27:29], self.data[14:17], self.ot)

    def to_wire(self) -> dict:
        return {"data": b64(self.data), "ot": b64(self.ot), "nick": b64(self.nick)}

    @classmethod
    def from_wire(cls, d: dict) -> "Mon":
        m = cls(unb64(d["data"]), unb64(d["ot"]), unb64(d["nick"]))
        if len(m.data) != MON_SIZE or len(m.ot) != NAME_SIZE or len(m.nick) != NAME_SIZE or m.data[0] == 0:
            raise ValueError("bad mon data")
        return m

    def nick_str(self) -> str:
        return text.decode(self.nick)

    def ot_str(self) -> str:
        return text.decode(self.ot)


def full_heal(mon: Mon, rom: RomData) -> Mon:
    d = bytearray(mon.data)
    d[1], d[2] = d[34], d[35]
    d[4] = 0
    for i in range(4):
        move = d[8 + i]
        ups = d[29 + i] >> 6
        base = rom.move_pp(move)
        cur = min(63, base + (base // 5) * ups) if move else 0
        d[29 + i] = (ups << 6) | cur
    return Mon(bytes(d), mon.ot, mon.nick)


def calc_stats(data: bytes, base) -> list:
    """Recompute [maxhp, atk, def, spd, spc] with the Gen-1 formula."""
    level = data[33]
    dv_atk, dv_def = data[27] >> 4, data[27] & 15
    dv_spd, dv_spc = data[28] >> 4, data[28] & 15
    dv_hp = ((dv_atk & 1) << 3) | ((dv_def & 1) << 2) | ((dv_spd & 1) << 1) | (dv_spc & 1)
    dvs = [dv_hp, dv_atk, dv_def, dv_spd, dv_spc]
    out = []
    for i in range(5):
        statexp = (data[17 + i * 2] << 8) | data[18 + i * 2]
        bonus = min(255, math.ceil(math.sqrt(statexp))) // 4
        v = ((base[i] + dvs[i]) * 2 + bonus) * level // 100
        v += (level + 10) if i == 0 else 5
        out.append(min(999, v))
    return out


def evolve_for_trade(mon: Mon, rom: RomData) -> Mon:
    """Apply trade evolution (Kadabra/Machoke/Graveler/Haunter)."""
    new = TRADE_EVOLUTIONS.get(mon.species)
    if not new:
        return mon
    base = rom.base_stats(new)
    if base is None:
        return mon
    d = bytearray(mon.data)
    old_max, old_hp = mon.max_hp, mon.hp
    stats = calc_stats(d, base)
    d[0] = new
    d[5], d[6] = base[5], base[6]
    d[34], d[35] = stats[0] >> 8, stats[0] & 0xFF
    for i in range(4):
        d[36 + i * 2], d[37 + i * 2] = stats[i + 1] >> 8, stats[i + 1] & 0xFF
    new_hp = max(0, stats[0] - (old_max - old_hp)) if old_hp > 0 else 0
    d[1], d[2] = new_hp >> 8, new_hp & 0xFF
    nick = mon.nick
    if text.decode(nick) == rom.species_name(mon.species):
        nick = rom.species_name_raw(new)
    return Mon(bytes(d), mon.ot, nick)


# --- RAM access --------------------------------------------------------
class GameMemory:
    def __init__(self, mem, rom: RomData):
        self.m = mem
        self.rom = rom

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self.m[addr + i] for i in range(n))

    def write(self, addr: int, data: bytes):
        for i, v in enumerate(data):
            self.m[addr + i] = v

    # -- state checks --
    def in_game(self) -> bool:
        return self.m[PLAYER_PIC] != 0 and self.m[PLAYER_NAME] not in (0, text.TERMINATOR)

    def in_battle(self) -> bool:
        return self.m[IS_IN_BATTLE] != 0

    def font_loaded(self) -> bool:
        return bool(self.m[FONT_LOADED] & 1)

    def is_safe(self) -> bool:
        """Standing still in the overworld with no menu/text open."""
        return (self.in_game() and not self.in_battle() and not self.font_loaded()
                and self.m[WALK_COUNTER] == 0 and self.m[JOY_IGNORE] == 0 and self.m[CUR_OPPONENT] == 0)

    def overworld_visible(self) -> bool:
        return self.in_game() and not self.in_battle() and not self.font_loaded()

    # -- position --
    def position(self) -> dict:
        m = self.m
        x, y = m[X_COORD], m[Y_COORD]
        wc = m[WALK_COUNTER]
        px, py = x * 16, y * 16
        if wc:
            xv = m[PLAYER_XVEC]
            yv = m[PLAYER_YVEC]
            xv = xv - 256 if xv > 127 else xv
            yv = yv - 256 if yv > 127 else yv
            px += xv * 2 * (8 - wc)
            py += yv * 2 * (8 - wc)
        return {"map": m[CUR_MAP], "x": x, "y": y, "px": px, "py": py,
                "facing": m[PLAYER_FACING] & 0xC, "img": m[PLAYER_IMG] & 0xF}

    def sprite_tiles(self):
        """Own player sprite tiles (24 tiles) or None if VRAM isn't trustworthy now."""
        if not self.overworld_visible():
            return None
        a = self.read(VRAM_SPRITE_A, 192)
        b = self.read(VRAM_SPRITE_B, 192)
        data = a + b
        if sum(1 for v in data if v) < 40:
            return None
        return data

    def player_name(self) -> str:
        return text.decode(self.read(PLAYER_NAME, NAME_SIZE))

    def badges(self) -> int:
        return self.m[BADGES]

    # -- party --
    def party(self) -> list:
        n = self.m[PARTY_COUNT]
        if n > 6:
            return []
        out = []
        for i in range(n):
            out.append(Mon(self.read(PARTY_MONS + i * MON_SIZE, MON_SIZE),
                           self.read(PARTY_OT + i * NAME_SIZE, NAME_SIZE),
                           self.read(PARTY_NICKS + i * NAME_SIZE, NAME_SIZE)))
        return out

    def party_checked(self):
        """Party, or None if it looks half-written (the game updates it over several frames)."""
        n = self.m[PARTY_COUNT]
        if n > 6 or self.m[PARTY_SPECIES + n] != 0xFF:
            return None
        party = self.party()
        for i, mon in enumerate(party):
            if mon.species == 0 or self.m[PARTY_SPECIES + i] != mon.species or not 1 <= mon.level <= 100:
                return None
        return party

    def write_party_slot(self, slot: int, mon: Mon):
        self.m[PARTY_SPECIES + slot] = mon.species
        self.write(PARTY_MONS + slot * MON_SIZE, mon.data)
        self.write(PARTY_OT + slot * NAME_SIZE, mon.ot)
        self.write(PARTY_NICKS + slot * NAME_SIZE, mon.nick)

    def set_dex(self, species: int):
        dex = self.rom.dex_no(species)
        if not 1 <= dex <= 151:
            return
        i, bit = (dex - 1) // 8, (dex - 1) % 8
        for base in (DEX_OWNED, DEX_SEEN):
            self.m[base + i] = self.m[base + i] | (1 << bit)

    def apply_trade(self, slot: int, expected: Mon, receive: Mon) -> Mon:
        """Replace party slot with the received mon. Raises if the slot changed."""
        party = self.party()
        if slot >= len(party) or party[slot].identity() != expected.identity():
            raise RuntimeError("party changed since the offer")
        new = evolve_for_trade(receive, self.rom)
        self.write_party_slot(slot, new)
        self.set_dex(receive.species)
        self.set_dex(new.species)
        return new

    # -- ghost battle --
    def heal_party(self):
        for i, mon in enumerate(self.party()):
            self.write_party_slot(i, full_heal(mon, self.rom))

    def trigger_trainer_battle(self):
        self.m[LONE_ATTACK_NO] = 0
        self.m[TRAINER_NO] = 1
        self.m[CUR_OPPONENT] = OPP_ID_OFFSET + GHOST_TRAINER_CLASS

    def inject_enemy(self, mons: list, trainer_name: str):
        mons = [full_heal(m, self.rom) for m in mons[:6]]
        self.m[ENEMY_PARTY_COUNT] = len(mons)
        for i in range(7):
            self.m[ENEMY_PARTY_SPECIES + i] = mons[i].species if i < len(mons) else 0xFF
        for i, mon in enumerate(mons):
            self.write(ENEMY_MONS + i * MON_SIZE, mon.data)
            self.write(ENEMY_OT + i * NAME_SIZE, mon.ot)
            self.write(ENEMY_NICKS + i * NAME_SIZE, mon.nick)
        self.write(TRAINER_NAME, text.encode(trainer_name.upper(), NAME_SIZE))


# --- fast boot ---------------------------------------------------------
SAVE_FILE_STATUS = 0xD087   # 0 = not checked yet, 1 = no save, 2 = save found
SHADOW_OAM = 0xC300
TILEMAP = 0xC3A0


def _overworld_loaded(m) -> bool:
    return (m[SHADOW_OAM] == 76 and m[SHADOW_OAM + 1] == 72 and not (m[FONT_LOADED] & 1)
            and m[IS_IN_BATTLE] == 0)


def fast_continue(pyboy, max_frames: int = 3000, on_progress=None) -> str:
    """Skip intro + title and press CONTINUE. Returns 'loaded', 'no-save' or 'timeout'.

    Never touches NEW GAME: if the game reports no save, it stops at the main menu.
    """
    m = pyboy.memory
    cont = text.encode("CONTINUE", 9)[:8]
    player = text.encode("PLAYER", 7)[:6]
    f = 0
    while f < max_frames and m[SAVE_FILE_STATUS] == 0:      # intro + title screen
        if f % 10 == 0:
            pyboy.button_press("a")
        if f % 10 == 3:
            pyboy.button_release("a")
        pyboy.tick(1, False, False)
        f += 1
        if on_progress and f % 120 == 0:
            on_progress()
    pyboy.button_release("a")
    for _ in range(90):
        if m[SAVE_FILE_STATUS] == 2:
            break
        pyboy.tick(1, False, False)
        f += 1
    if m[SAVE_FILE_STATUS] != 2:
        return "no-save"
    last_press = -999
    while f < max_frames:
        pyboy.tick(1, False, False)
        f += 1
        if _overworld_loaded(m):
            return "loaded"
        tm = bytes(m[TILEMAP + i] for i in range(360)) if f % 5 == 0 else b""
        if tm and (cont in tm or player in tm) and f - last_press > 45:
            pyboy.button_press("a")
            for _ in range(3):
                pyboy.tick(1, False, False)
            pyboy.button_release("a")
            f += 3
            last_press = f
    return "timeout"
