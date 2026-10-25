"""Everything that touches Pokémon Crystal's RAM/ROM (USA v1.0).

Addresses come from the pret/pokecrystal symbols and were verified by running the ROM
(SHA1 f4cd194bdee0d04ca4eac29e09b8e4e9d818c133) in PyBoy -- see dev/crystal/verify.py and
SPEC.md (Crystal section). They are only valid for that ROM.

WRAM banking: Crystal switches the D000-DFFF window (SVBK) between banks 1-7. Every game variable
used here lives in WRAM bank 1, so all D000-DFFF accesses go through mem[1, addr] -- never
mem[addr], which reads whatever bank happens to be selected. C000-CFFF and FF80+ are unbanked.
PyBoy's mem[bank, addr] is only meaningful for D000-DFFF (mem[1, 0xC4A0] would read D4A0!).

`mem` is anything indexable like pyboy.memory (mem[addr], mem[bank, addr]); tests use a fake.
"""
from __future__ import annotations

import base64
import math
import uuid
from dataclasses import dataclass, field

from . import text

CRYSTAL_SHA1 = "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"

# --- WRAM0 (unbanked) ----------------------------------------------------------------------------------
SHADOW_OAM = 0xC400            # wShadowOAM
TILEMAP = 0xC4A0               # wTilemap (20x18)
WINDOW_STACK_SIZE = 0xCF78     # wWindowStackSize: >0 while a menu window is open
GAME_TIMER = 0xCFBC            # wGameTimerPaused: bit 0 set while the game clock runs (= in the game proper)
JOYPAD_DISABLE = 0xCFBE        # wJoypadDisable
SAVE_FILE_EXISTS = 0xCFCD      # wSaveFileExists: 0 none, 1 primary save ok, 2 backup save ok
SCX, SCY = 0xFF43, 0xFF42      # hardware scroll (camera)

# --- WRAM bank 1 ---------------------------------------------------------------------------------------
WBANK = 1
STATE_FLAGS = 0xD0ED           # wStateFlags: bit 6 text state, bit 7 scripted movement
PLAYER_STEP_VECTOR_X = 0xD14E  # signed, pixels the camera still has to catch up (0 when idle)
PLAYER_STEP_VECTOR_Y = 0xD14F
BATTLE_MODE = 0xD22D           # wBattleMode: 0 none, 1 wild, 2 trainer
MAP_STATUS = 0xD432
SCRIPT_FLAGS = 0xD434          # bit 2: script running
SCRIPT_MODE = 0xD437           # 0 = SCRIPT_OFF
PLAYER_ID = 0xD47B             # 2 bytes big endian (start of wPlayerData)
PLAYER_NAME = 0xD47D           # 11 bytes
SAVED_AT_LEAST_ONCE = 0xD4B4   # set when the save's PC boxes have been initialised (first save)
PLAYER_STRUCT = 0xD4D6         # wPlayerStruct (object_struct, 0x28 bytes)
PLAYER_SPRITE = 0xD4D6         # sprite id, 0 before the overworld exists
PLAYER_SPRITE_TILE = 0xD4D8    # first VRAM tile of the player sprite (bit 7 clear -> VRAM bank 1)
PLAYER_PALETTE = 0xD4DC        # low 3 bits: CGB OBJ palette
PLAYER_WALKING = 0xD4DD        # 0xFF standing, else speed*4 + dir (dir 0 down 1 up 2 left 3 right)
PLAYER_DIRECTION = 0xD4DE      # 0 down 4 up 8 left 12 right
PLAYER_STEP_DURATION = 0xD4E0  # logic updates left in the current step
PLAYER_FACING = 0xD4E3         # FACING_STEP_* 0..15 = direction + walk frame (0xFF hidden)
PLAYER_MAP_X = 0xD4E6          # object coords = wXCoord + 4 (destination while stepping)
PLAYER_LAST_MAP_X = 0xD4E8
JOHTO_BADGES = 0xD857
KANTO_BADGES = 0xD858
PLAYER_STATE = 0xD95D          # 0 normal, 1 bike, 2 skate, 4 surf
CUR_BOX = 0xDB72               # wCurBox 0..13
BOX_NAMES = 0xDB75             # 14 x 9
MAP_GROUP = 0xDCB5
MAP_NUMBER = 0xDCB6
Y_COORD = 0xDCB7
X_COORD = 0xDCB8
PARTY_COUNT = 0xDCD7
PARTY_SPECIES = 0xDCD8         # 6 + 0xFF terminator; 0xFD = egg
PARTY_MONS = 0xDCDF            # 6 x 48
PARTY_OT = 0xDDFF              # 6 x 11
PARTY_NICKS = 0xDE41           # 6 x 11
DEX_CAUGHT = 0xDE99            # 32 bytes, bit = dex - 1
DEX_SEEN = 0xDEB9
OB_PALS = 0xD0C0               # wOBPals2 in WRAM bank 5: 8 palettes x 4 colours, RGB555 little endian
OB_PALS_BANK = 5

MON_SIZE = 48
BOX_MON_SIZE = 32
NAME_SIZE = 11
EGG = 0xFD
DITTO = 132
TRANSFORM = 144
BASE_HAPPINESS = 70

# --- VRAM: player sprite ---------------------------------------------------------------------------------
SPRITE_VRAM_BANK = 1
VRAM_SPRITE_A = 0x8000         # tiles 0x00-0x0B: standing down/up/left
VRAM_SPRITE_B = 0x8800         # tiles 0x80-0x8B: walking down/up/left
SPRITE_BYTES = 384
PLAYER_SCREEN_X, PLAYER_SCREEN_Y = 64, 60

# --- ROM tables (file offsets) ---------------------------------------------------------------------------
ROM_BASE_DATA = 0x51424        # 14:5424, 32 bytes/species, index dex-1
ROM_MON_NAMES = 0x53384        # 14:7384, 10 bytes/species
ROM_MOVES = 0x41AFB            # 10:5AFB, 7 bytes/move, pp at +5
ROM_MOVE_NAMES = 0x1C9F29      # 72:5F29, 0x50-terminated list
NUM_SPECIES = 251
NUM_MOVES = 251

# --- hooks (bank, addr) ----------------------------------------------------------------------------------
HOOK_PC_OPEN = (0x03, 0x641C)    # _BillsPC.LogIn (only reached once the party check passed)
HOOK_PC_CLOSE = (0x03, 0x643F)   # _BillsPC.LogOut

# --- SRAM ------------------------------------------------------------------------------------------------
SRAM_CHECK_VALUE_1 = (1, 0xA008)
SRAM_GAME_DATA = (1, 0xA009, 0xAB83)      # sGameData .. sGameDataEnd, 16-bit sum at sChecksum
SRAM_CHECKSUM = (1, 0xAD0D)               # little endian
SRAM_BACKUP_GAME_DATA = (0, 0xB209)
SRAM_BACKUP_CHECKSUM = (0, 0xBF0D)
SRAM_CUR_BOX = (1, 0xAD10)                # sBox: the live copy of the current box (not checksummed)
BOX_BYTES = 0x44E                         # count + species(21) + 20x32 + 20x11 + 20x11
BOX_STRIDE = 0x450                        # + 2 bytes padding (FF 00 after EraseBoxes)
NUM_BOXES = 14
MONS_PER_BOX = 20
REC_SIZE = BOX_MON_SIZE + 2 * NAME_SIZE   # 54: box_struct + OT + nickname
RECORD_SIZE = REC_SIZE + 1                # + 1 flag byte: 1 = egg (Gen 2 marks eggs only in the species list)
STANDIN_OT = "YLINK"

# Gen-1 catch rate -> held item, as the Time Capsule does (data/items/catch_rate_items.asm)
CATCH_RATE_ITEMS = {0x19: 0x92, 0x2D: 0x53, 0x32: 0xAE, 0x5A: 0xAD, 0x64: 0xAD, 0x78: 0xAD,
                    0x87: 0xAD, 0xBE: 0xAD, 0xC3: 0xAD, 0xDC: 0xAD, 0xFA: 0xAD, 0xFF: 0xAD}


def b64(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _signed(v: int) -> int:
    return v - 256 if v > 127 else v


# ---------------------------------------------------------------------------------------------------------
class RomData:
    """Read-only lookups into the player's own Crystal ROM. Gen-2 species ids are dex numbers."""

    def __init__(self, rom: bytes):
        self.rom = rom
        self._move_names = None

    @classmethod
    def from_file(cls, path: str) -> "RomData":
        with open(path, "rb") as f:
            return cls(f.read())

    def dex_no(self, species: int) -> int:
        return species if 1 <= species <= NUM_SPECIES else 0

    def species_name(self, species: int) -> str:
        if not 1 <= species <= NUM_SPECIES:
            return "?"
        o = ROM_MON_NAMES + (species - 1) * 10
        return text.decode(self.rom[o:o + 10])

    def species_name_raw(self, species: int) -> bytes:
        o = ROM_MON_NAMES + (species - 1) * 10
        return (bytes(self.rom[o:o + 10]) + bytes([text.TERMINATOR]))[:NAME_SIZE]

    def base_data(self, species: int) -> bytes | None:
        if not 1 <= species <= NUM_SPECIES:
            return None
        o = ROM_BASE_DATA + (species - 1) * 32
        return bytes(self.rom[o:o + 32])

    def base_stats(self, species: int):
        """(hp, atk, def, spd, satk, sdef, type1, type2) or None."""
        d = self.base_data(species)
        return tuple(d[1:9]) if d else None

    def growth_rate(self, species: int) -> int:
        d = self.base_data(species)
        return d[22] if d else 0

    def move_pp(self, move: int) -> int:
        if not 1 <= move <= NUM_MOVES:
            return 0
        return self.rom[ROM_MOVES + (move - 1) * 7 + 5]

    def move_name(self, move: int) -> str:
        if self._move_names is None:
            names, cur, o = [], [], ROM_MOVE_NAMES
            while len(names) < NUM_MOVES and o < len(self.rom):
                v = self.rom[o]
                o += 1
                if v == text.TERMINATOR:
                    names.append(text.decode(bytes(cur)))
                    cur = []
                else:
                    cur.append(v)
            self._move_names = names
        return self._move_names[move - 1] if 1 <= move <= len(self._move_names) else "-"


def exp_for_level(growth: int, n: int) -> int:
    """Experience needed for level n (data/growth_rates.asm)."""
    if n <= 1:
        return 0
    if growth == 1:                           # slightly fast
        v = 3 * n ** 3 // 4 + 10 * n * n - 30
    elif growth == 2:                         # slightly slow
        v = 3 * n ** 3 // 4 + 20 * n * n - 70
    elif growth == 3:                         # medium slow
        v = 6 * n ** 3 // 5 - 15 * n * n + 100 * n - 140
    elif growth == 4:                         # fast
        v = 4 * n ** 3 // 5
    elif growth == 5:                         # slow
        v = 5 * n ** 3 // 4
    else:                                     # medium fast
        v = n ** 3
    return max(0, v)


def calc_stats(data: bytes, base) -> list:
    """[maxhp, atk, def, spd, satk, sdef] with the Gen-2 formula (verified against the game)."""
    level = data[31]
    atk, dfn = data[21] >> 4, data[21] & 15
    spd, spc = data[22] >> 4, data[22] & 15
    hp = ((atk & 1) << 3) | ((dfn & 1) << 2) | ((spd & 1) << 1) | (spc & 1)
    dvs = [hp, atk, dfn, spd, spc, spc]
    exp_idx = [0, 1, 2, 3, 4, 4]               # SpAtk and SpDef share the Special stat exp
    out = []
    for i in range(6):
        k = exp_idx[i]
        statexp = (data[11 + k * 2] << 8) | data[12 + k * 2]
        bonus = min(255, math.ceil(math.sqrt(statexp))) // 4
        v = ((base[i] + dvs[i]) * 2 + bonus) * level // 100
        v += (level + 10) if i == 0 else 5
        out.append(min(999, v))
    return out


def make_mon(rom: RomData, species: int, level: int, moves, ot_id: int, ot_name: str, dvs=0x9888,
             nick: str | None = None, item: int = 0, happiness: int = BASE_HAPPINESS) -> "Mon":
    """Build a consistent 48-byte party mon (stats, exp, PP all computed from the ROM)."""
    d = bytearray(MON_SIZE)
    d[0], d[1] = species, item
    mv = list(moves)[:4] + [0] * (4 - len(list(moves)[:4]))
    d[2:6] = bytes(mv)
    d[6], d[7] = ot_id >> 8, ot_id & 0xFF
    exp = exp_for_level(rom.growth_rate(species), level)
    d[8:11] = exp.to_bytes(3, "big")
    d[21], d[22] = dvs >> 8, dvs & 0xFF
    for i, m in enumerate(mv):
        d[23 + i] = rom.move_pp(m) if m else 0
    d[27] = happiness
    d[31] = level
    stats = calc_stats(d, rom.base_stats(species))
    d[34:36] = stats[0].to_bytes(2, "big")
    d[36:38] = stats[0].to_bytes(2, "big")
    for i in range(5):
        d[38 + 2 * i:40 + 2 * i] = stats[i + 1].to_bytes(2, "big")
    nick_raw = text.encode(nick, NAME_SIZE) if nick else rom.species_name_raw(species)
    return Mon(bytes(d), text.encode(ot_name, NAME_SIZE), nick_raw)


# ---------------------------------------------------------------------------------------------------------
@dataclass
class Mon:
    """A Crystal party Pokémon: 48-byte party_struct + OT name + nickname (11 bytes each)."""
    data: bytes
    ot: bytes
    nick: bytes
    egg: bool = field(default=False, compare=False)

    @property
    def species(self) -> int:
        return self.data[0]

    @property
    def item(self) -> int:
        return self.data[1]

    @property
    def level(self) -> int:
        return self.data[31]

    @property
    def hp(self) -> int:
        return (self.data[34] << 8) | self.data[35]

    @property
    def max_hp(self) -> int:
        return (self.data[36] << 8) | self.data[37]

    @property
    def moves(self):
        return [m for m in self.data[2:6] if m]

    @property
    def ot_id(self) -> int:
        return (self.data[6] << 8) | self.data[7]

    @property
    def dvs(self) -> int:
        return (self.data[21] << 8) | self.data[22]

    def identity(self):
        return (self.data[0], self.data[6:8], self.data[21:23], self.data[8:11], self.ot)

    def box_record(self) -> bytes:
        """55-byte PC record (box_struct + OT + nickname + egg flag)."""
        return self.data[:BOX_MON_SIZE] + self.ot + self.nick + bytes([1 if self.egg else 0])

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
    d[34], d[35] = d[36], d[37]
    d[32] = 0
    for i in range(4):
        move = d[2 + i]
        ups = d[23 + i] >> 6
        base = rom.move_pp(move)
        cur = min(63, base + (base // 5) * ups) if move else 0
        d[23 + i] = (ups << 6) | cur
    return Mon(bytes(d), mon.ot, mon.nick, mon.egg)



# Trade evolutions (data/pokemon/evos_attacks.asm). Item ids checked against the ROM's item names.
KINGS_ROCK, EVERSTONE, METAL_COAT, DRAGON_SCALE, UP_GRADE = 0x52, 0x70, 0x8F, 0x97, 0xAC
TRADE_EVOLUTIONS = {64: (65, None), 67: (68, None), 75: (76, None), 93: (94, None),        # Kadabra Machoke Graveler Haunter
                    61: (186, KINGS_ROCK), 79: (199, KINGS_ROCK), 95: (208, METAL_COAT),
                    123: (212, METAL_COAT), 117: (230, DRAGON_SCALE), 137: (233, UP_GRADE)}


def evolve_for_trade(mon: Mon, rom: RomData) -> Mon:
    """Evolve a freshly received mon like the game does after a trade (Everstone blocks it)."""
    rule = TRADE_EVOLUTIONS.get(mon.species)
    if mon.egg or not rule or mon.item == EVERSTONE:
        return mon
    new, item = rule
    if item is not None and mon.item != item:
        return mon
    base = rom.base_stats(new)
    if base is None:
        return mon
    d = bytearray(mon.data)
    old_max, old_hp = mon.max_hp, mon.hp
    d[0] = new
    if item is not None:
        d[1] = 0                                   # the item is used up
    stats = calc_stats(d, base)
    d[36:38] = stats[0].to_bytes(2, "big")
    for i in range(5):
        d[38 + 2 * i:40 + 2 * i] = stats[i + 1].to_bytes(2, "big")
    new_hp = max(0, stats[0] - (old_max - old_hp)) if old_hp > 0 else 0
    d[34:36] = new_hp.to_bytes(2, "big")
    nick = mon.nick
    if text.decode(nick) == rom.species_name(mon.species):
        nick = rom.species_name_raw(new)
    return Mon(bytes(d), mon.ot, nick, mon.egg)

# ---------------------------------------------------------------------------------------------------------
class CrystalMemory:
    """Same method names/semantics as game.GameMemory, for Pokémon Crystal."""

    def __init__(self, mem, rom: RomData | None = None):
        self.m = mem
        self.rom = rom

    # raw access (bank-safe) --------------------------------------------------------------------------------
    def r(self, addr: int) -> int:
        return self.m[WBANK, addr] if 0xD000 <= addr < 0xE000 else self.m[addr]

    def w(self, addr: int, v: int):
        if 0xD000 <= addr < 0xE000:
            self.m[WBANK, addr] = v
        else:
            self.m[addr] = v

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self.r(addr + i) for i in range(n))

    def write(self, addr: int, data: bytes):
        for i, v in enumerate(data):
            self.w(addr + i, v)

    # state checks ------------------------------------------------------------------------------------------
    def in_game(self) -> bool:
        """In the game proper (false on title / main menu / CONTINUE card / new-game intro)."""
        return (bool(self.r(GAME_TIMER) & 1) and self.r(MAP_GROUP) != 0 and self.r(MAP_NUMBER) != 0 and self.r(PLAYER_SPRITE) != 0
                and self.r(PLAYER_NAME) not in (0, text.TERMINATOR))

    def in_battle(self) -> bool:
        return self.r(BATTLE_MODE) != 0

    def font_loaded(self) -> bool:
        """Text box / menu open (Yellow's wFontLoaded equivalent): text state or an open menu window."""
        return bool(self.r(STATE_FLAGS) & 0x40) or self.r(WINDOW_STACK_SIZE) != 0

    def script_running(self) -> bool:
        return (self.r(SCRIPT_MODE) != 0 or bool(self.r(SCRIPT_FLAGS) & 0x04)
                or bool(self.r(STATE_FLAGS) & 0x80))

    def joypad_ignored(self) -> bool:
        return self.r(JOYPAD_DISABLE) != 0

    def walking(self) -> bool:
        return (self.r(PLAYER_WALKING) != 0xFF or self.r(PLAYER_STEP_VECTOR_X) != 0
                or self.r(PLAYER_STEP_VECTOR_Y) != 0)

    def is_safe(self) -> bool:
        """Standing still in the overworld with no menu/text/script/battle."""
        return (self.in_game() and not self.in_battle() and not self.font_loaded() and not self.script_running()
                and not self.joypad_ignored() and not self.walking() and self.r(PLAYER_FACING) < 16)

    def overworld_visible(self) -> bool:
        return self.in_game() and not self.in_battle() and not self.font_loaded()

    # position ----------------------------------------------------------------------------------------------
    def map_id(self) -> int:
        """One id per map: group << 8 | number (Crystal maps are identified by group + number)."""
        return (self.r(MAP_GROUP) << 8) | self.r(MAP_NUMBER)

    def position(self) -> dict:
        """px/py = camera position in map pixels (matches the rendered screen, see SPEC)."""
        x, y = self.r(X_COORD), self.r(Y_COORD)
        px, py = x * 16, y * 16
        walking = self.r(PLAYER_WALKING)
        if walking < 12:
            speed = walking >> 2                      # 0 slow, 1 walk, 2 bike
            per, total = (1, 2, 4)[speed], (16, 8, 4)[speed]
            left = self.r(PLAYER_STEP_DURATION)
            if 0 < left <= total:
                moved = per * (total - left)
                dx, dy = ((0, 1), (0, -1), (-1, 0), (1, 0))[walking & 3]
                px += dx * moved
                py += dy * moved
        # the screen trails the step logic by one update; the step vector is that pending amount
        px -= _signed(self.r(PLAYER_STEP_VECTOR_X))
        py -= _signed(self.r(PLAYER_STEP_VECTOR_Y))
        facing = self.r(PLAYER_FACING)
        direction = self.r(PLAYER_DIRECTION) & 0xC
        img = facing & 0xF if facing < 16 else direction
        return {"map": self.map_id(), "x": x, "y": y, "px": px, "py": py, "facing": direction, "img": img}

    # sprite ------------------------------------------------------------------------------------------------
    def sprite_tiles(self):
        """Own player sprite tiles (384 bytes: VRAM bank 1 tiles 0x00-0x0B + 0x80-0x8B) or None."""
        if not self.overworld_visible() or self.r(PLAYER_SPRITE_TILE) != 0:
            return None
        m = self.m
        data = bytes(m[SPRITE_VRAM_BANK, VRAM_SPRITE_A + i] for i in range(192)) + \
            bytes(m[SPRITE_VRAM_BANK, VRAM_SPRITE_B + i] for i in range(192))
        if sum(1 for v in data if v) < 40:
            return None
        return data

    def sprite_palette_index(self) -> int:
        return self.r(PLAYER_PALETTE) & 7

    def sprite_palette(self):
        """The player's 4 OBJ colours as RGBA (index 0 transparent), same conversion PyBoy renders with."""
        base = OB_PALS + 8 * self.sprite_palette_index()
        out = [(0, 0, 0, 0)]
        for i in range(1, 4):
            c = self.m[OB_PALS_BANK, base + 2 * i] | (self.m[OB_PALS_BANK, base + 2 * i + 1] << 8)
            out.append(rgb555(c))
        return out

    # trainer -----------------------------------------------------------------------------------------------
    def player_name(self) -> str:
        return text.decode(self.read(PLAYER_NAME, NAME_SIZE))

    def player_id(self) -> int:
        return (self.r(PLAYER_ID) << 8) | self.r(PLAYER_ID + 1)

    def badges(self) -> int:
        """Johto badges in bits 0-7, Kanto badges in bits 8-15."""
        return self.r(JOHTO_BADGES) | (self.r(KANTO_BADGES) << 8)

    # party -------------------------------------------------------------------------------------------------
    def party(self) -> list:
        n = self.r(PARTY_COUNT)
        if n > 6:
            return []
        out = []
        for i in range(n):
            out.append(Mon(self.read(PARTY_MONS + i * MON_SIZE, MON_SIZE),
                           self.read(PARTY_OT + i * NAME_SIZE, NAME_SIZE),
                           self.read(PARTY_NICKS + i * NAME_SIZE, NAME_SIZE),
                           egg=self.r(PARTY_SPECIES + i) == EGG))
        return out

    def party_checked(self):
        """Party, or None if it looks half-written (the game updates it over several frames)."""
        n = self.r(PARTY_COUNT)
        if n > 6 or self.r(PARTY_SPECIES + n) != 0xFF:
            return None
        party = self.party()
        for i, mon in enumerate(party):
            listed = self.r(PARTY_SPECIES + i)
            if mon.species == 0 or mon.species > NUM_SPECIES or not 1 <= mon.level <= 100:
                return None
            if listed != mon.species and listed != EGG:
                return None
        return party

    def write_party_slot(self, slot: int, mon: Mon):
        self.w(PARTY_SPECIES + slot, EGG if mon.egg else mon.species)
        self.write(PARTY_MONS + slot * MON_SIZE, mon.data)
        self.write(PARTY_OT + slot * NAME_SIZE, mon.ot)
        self.write(PARTY_NICKS + slot * NAME_SIZE, mon.nick)

    def set_party(self, mons: list):
        """Replace the whole party (count, species list + terminator, structs, names)."""
        mons = mons[:6]
        self.w(PARTY_COUNT, len(mons))
        for i in range(7):
            if i >= len(mons):
                self.w(PARTY_SPECIES + i, 0xFF)
        for i, mon in enumerate(mons):
            self.write_party_slot(i, mon)

    def set_dex(self, species: int):
        if not 1 <= species <= NUM_SPECIES:
            return
        i, bit = (species - 1) // 8, (species - 1) % 8
        for base in (DEX_CAUGHT, DEX_SEEN):
            self.w(base + i, self.r(base + i) | (1 << bit))

    def heal_party(self):
        for i, mon in enumerate(self.party()):
            self.write_party_slot(i, full_heal(mon, self.rom))

    def remove_party_slot(self, slot: int):
        party = self.party()
        if 0 <= slot < len(party) and len(party) > 1:
            self.set_party(party[:slot] + party[slot + 1:])

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

    def player_img(self) -> int:
        return self.position()["img"]

    def status_line(self) -> str:
        return (f"in_game={self.in_game()} map={self.map_id():#06x} xy=({self.r(X_COORD)},{self.r(Y_COORD)}) "
                f"battle={self.r(BATTLE_MODE)} font={self.font_loaded()} script={self.script_running()} "
                f"walk={self.r(PLAYER_WALKING)} party={self.r(PARTY_COUNT)}")


def rgb555(c: int):
    r, g, b = c & 31, (c >> 5) & 31, (c >> 10) & 31
    return (r << 3, g << 3, b << 3, 255)


def party_records(cm: CrystalMemory) -> list:
    """Party as 55-byte box records for the PC database (stand-in detection after a withdraw)."""
    return [m.box_record() for m in cm.party()]


# ---------------------------------------------------------------------------------------------------------
# Friend sprite composition
# ---------------------------------------------------------------------------------------------------------
def frame_layout(img: int):
    """(base_tile_id, hflip) for a FACING_STEP_* value 0..15 (data/sprites/facings.asm).

    Identical to Yellow's layout: down 0x00 / up 0x04 / left 0x08, walking frames +0x80,
    right = mirrored left, walking frame 3 of down/up = mirrored frame 1.
    """
    d, f = img & 0xC, img & 3
    base = 8 if d == 12 else d
    if f in (1, 3):
        base += 0x80
    return base, d == 12 or (d in (0, 4) and f == 3)


def compose(blob: bytes, img: int):
    """16x16 colour indices for a 384-byte Crystal sprite blob (same blob layout as Yellow)."""
    from . import sprites
    return sprites.compose(blob, img)


# ---------------------------------------------------------------------------------------------------------
# PC boxes (Gen 2)
# ---------------------------------------------------------------------------------------------------------
def box_slot(i: int):
    """(sram bank, address) of box i (0-based) in the save."""
    return (2, 0xA000 + i * BOX_STRIDE) if i < 7 else (3, 0xA000 + (i - 7) * BOX_STRIDE)


def parse_box(raw: bytes) -> list:
    n = raw[0]
    if n > MONS_PER_BOX:
        return []
    mons_at = 22
    ot_at = mons_at + MONS_PER_BOX * BOX_MON_SIZE
    nick_at = ot_at + MONS_PER_BOX * NAME_SIZE
    out = []
    for i in range(n):
        out.append(bytes(raw[mons_at + i * BOX_MON_SIZE: mons_at + (i + 1) * BOX_MON_SIZE])
                   + bytes(raw[ot_at + i * NAME_SIZE: ot_at + (i + 1) * NAME_SIZE])
                   + bytes(raw[nick_at + i * NAME_SIZE: nick_at + (i + 1) * NAME_SIZE])
                   + bytes([1 if raw[1 + i] == EGG else 0]))
    return out


def build_box(mons: list) -> bytes:
    """BOX_BYTES box block + the 2 padding bytes (FF 00) the game writes when it initialises boxes."""
    raw = bytearray(BOX_STRIDE)
    raw[0] = len(mons)
    for i in range(MONS_PER_BOX + 1):
        raw[1 + i] = (EGG if _is_egg_rec(mons[i]) else mons[i][0]) if i < len(mons) else 0xFF
    ot_at = 22 + MONS_PER_BOX * BOX_MON_SIZE
    nick_at = ot_at + MONS_PER_BOX * NAME_SIZE
    for i, m in enumerate(mons):
        raw[22 + i * BOX_MON_SIZE: 22 + (i + 1) * BOX_MON_SIZE] = m[:BOX_MON_SIZE]
        raw[ot_at + i * NAME_SIZE: ot_at + (i + 1) * NAME_SIZE] = m[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE]
        raw[nick_at + i * NAME_SIZE: nick_at + (i + 1) * NAME_SIZE] = m[BOX_MON_SIZE + NAME_SIZE:REC_SIZE]
    raw[BOX_BYTES], raw[BOX_BYTES + 1] = 0xFF, 0x00
    return bytes(raw)


def _is_egg_rec(rec: bytes) -> bool:
    return len(rec) > REC_SIZE and rec[REC_SIZE] == 1


def record(data32: bytes, ot: bytes, nick: bytes, egg: bool = False) -> bytes:
    return bytes(data32[:BOX_MON_SIZE]) + bytes(ot) + bytes(nick) + bytes([1 if egg else 0])


def checksum16(data) -> int:
    return sum(data) & 0xFFFF


def gen1_to_gen2(rec1: bytes, dex: int) -> bytes | None:
    """Convert a 55-byte Gen-1 box record to a 55-byte Gen-2 one, the way the Time Capsule does.

    dex: national dex number of the Gen-1 species (the Gen-1 record stores Yellow's internal index).
    """
    if not 1 <= dex <= 151 or len(rec1) < 55:
        return None
    d = bytearray(BOX_MON_SIZE)
    d[0] = dex
    d[1] = CATCH_RATE_ITEMS.get(rec1[7], rec1[7])
    d[2:6] = rec1[8:12]
    if any(m > 165 for m in d[2:6]):
        return None
    d[6:8] = rec1[12:14]
    d[8:11] = rec1[14:17]
    d[11:21] = rec1[17:27]
    d[21:23] = rec1[27:29]
    d[23:27] = rec1[29:33]
    d[27] = BASE_HAPPINESS
    d[31] = rec1[3]
    if not 1 <= d[31] <= 100:
        return None
    return bytes(d) + bytes(rec1[33:44]) + bytes(rec1[44:55]) + b"\x00"


class Gen2PC:
    """Read/write all 14 Crystal boxes. Same interface as pcdb.Gen1PC.

    Layout: boxes 1-7 at SRAM bank 2 A000 + i*0x450, boxes 8-14 at bank 3. The current box (wCurBox)
    is used by the game from its live copy sBox (bank 1 AD10); saving copies sBox to its slot, loading
    (CONTINUE / box change) copies the slot to sBox. No box checksums exist in Gen 2; the main save
    checksum (bank 1 A009..AB83) does not cover boxes, so writing boxes never invalidates the save.
    """

    def __init__(self, mem, rom: RomData | None = None):
        self.m = mem
        self.rom = rom
        self.cm = CrystalMemory(mem, rom)

    def _read(self, bank, addr, n):
        return bytes(self.m[bank, addr + i] for i in range(n))

    def _write(self, bank, addr, data):
        for i, v in enumerate(data):
            self.m[bank, addr + i] = v

    @staticmethod
    def slot(i):
        return box_slot(i)

    def current_box(self) -> int:
        v = self.cm.r(CUR_BOX)
        return v if v < NUM_BOXES else 0

    def initialised(self) -> bool:
        """The game has erased/initialised all 14 SRAM boxes (happens on the first save).

        Before that, only the current box (sBox) is real; writing other boxes would be wiped by the
        game's first save (ErasePreviousSave), so write_boxes refuses.
        """
        return self.cm.r(SAVED_AT_LEAST_ONCE) != 0

    def read_boxes(self) -> list:
        cur = self.current_box()
        boxes = []
        for i in range(NUM_BOXES):
            if i == cur:
                raw = self._read(*SRAM_CUR_BOX, BOX_BYTES)
            elif self.initialised():
                raw = self._read(*self.slot(i), BOX_BYTES)
            else:
                raw = bytes([0, 0xFF]) + bytes(BOX_BYTES - 2)
            boxes.append(parse_box(raw))
        return boxes

    def write_boxes(self, boxes: list):
        if not self.initialised():
            raise RuntimeError("Crystal PC boxes are not initialised yet (save the game once first)")
        cur = self.current_box()
        built = [build_box(list(b)[:MONS_PER_BOX]) for b in list(boxes)[:NUM_BOXES]]
        built += [build_box([])] * (NUM_BOXES - len(built))
        for i in range(NUM_BOXES):
            self._write(*self.slot(i), built[i])
        self._write(*SRAM_CUR_BOX, built[cur][:BOX_BYTES])

    def save_checksum_ok(self) -> bool:
        """The main save block's checksum (what CONTINUE verifies). Box writes never change it."""
        bank, a, b = SRAM_GAME_DATA
        calc = checksum16(self._read(bank, a, b - a))
        return calc == (self.m[SRAM_CHECKSUM[0], SRAM_CHECKSUM[1]] | (self.m[SRAM_CHECKSUM[0], SRAM_CHECKSUM[1] + 1] << 8))

    # --- records <-> entries ----------------------------------------------------------------------------
    def entry_from_record(self, rec: bytes, origin="crystal", entry_id=None) -> dict:
        return {"id": entry_id or uuid.uuid4().hex, "origin": origin, "dex": rec[0], "level": rec[31],
                "nick": text.decode(rec[BOX_MON_SIZE + NAME_SIZE:REC_SIZE]),
                "ot": text.decode(rec[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE]),
                "otid": (rec[6] << 8) | rec[7], "gen1": None, "gen2": b64(rec[:RECORD_SIZE])}

    def record_for(self, entry: dict) -> bytes:
        """Bytes to show in Crystal: own Gen-2 data, else a Time-Capsule conversion of the Gen-1 data,
        else a stand-in."""
        if entry.get("gen2"):
            return unb64(entry["gen2"])
        if entry.get("gen1"):
            rec = gen1_to_gen2(unb64(entry["gen1"]), int(entry.get("dex") or 0))
            if rec is not None:
                return rec
        return self.standin(entry)

    def standin(self, entry: dict) -> bytes:
        level = max(2, min(100, int(entry.get("level") or 5)))
        d = bytearray(BOX_MON_SIZE)
        d[0] = DITTO
        d[2] = TRANSFORM
        d[8:11] = (level ** 3).to_bytes(3, "big")   # Ditto: medium fast
        d[23] = 10                                  # Transform PP
        d[27] = BASE_HAPPINESS
        d[31] = level
        name = "?" + (entry.get("nick") or "POKEMON").upper()
        return bytes(d) + text.encode(STANDIN_OT, NAME_SIZE) + text.encode(name[:10], NAME_SIZE) + b"\x00"

    @staticmethod
    def is_standin(rec: bytes) -> bool:
        return rec[0] == DITTO and rec[6] == 0 and rec[7] == 0 and \
            text.decode(rec[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE]) == STANDIN_OT


# ---------------------------------------------------------------------------------------------------------
# fast boot
# ---------------------------------------------------------------------------------------------------------
def _overworld_loaded(cm: CrystalMemory) -> bool:
    return cm.in_game() and cm.is_safe()


def fast_continue(pyboy, max_frames: int = 4000, on_progress=None) -> str:
    """Skip intro + title and press CONTINUE. Returns 'loaded', 'no-save' or 'timeout'.

    Never touches NEW GAME: if the main menu has no CONTINUE (wSaveFileExists stays 0), it stops there.
    """
    cm = CrystalMemory(pyboy.memory)
    m = pyboy.memory
    cont = text.encode("CONTINUE", 9)[:8]
    newgame = text.encode("NEW GAME", 9)[:8]
    f = 0
    last_press = -999

    def tilemap():
        return bytes(m[TILEMAP + i] for i in range(360))

    def press(b):
        nonlocal f
        pyboy.button_press(b)
        for _ in range(3):
            pyboy.tick(1, False, False)
        pyboy.button_release(b)
        f += 3

    # 1. intro + title until the main menu is on screen
    while f < max_frames:
        tm = tilemap() if f % 5 == 0 else b""
        if tm and (cont in tm or newgame in tm):
            break
        if f % 20 == 0:
            press("start")
        pyboy.tick(1, False, False)
        f += 1
        if on_progress and f % 120 == 0:
            on_progress()
    else:
        return "timeout"
    for _ in range(10):
        pyboy.tick(1, False, False)
        f += 1
    if m[SAVE_FILE_EXISTS] == 0 or cont not in tilemap():
        return "no-save"
    # 2. CONTINUE is the default cursor position; A on it, then A to confirm the player card
    while f < max_frames:
        pyboy.tick(1, False, False)
        f += 1
        if _overworld_loaded(cm):
            # let the map finish fading in
            for _ in range(30):
                pyboy.tick(1, False, False)
            return "loaded"
        if f - last_press > 40 and not cm.in_game():
            tm = tilemap()
            if newgame in tm and cont not in tm:
                return "no-save"
            press("a")
            last_press = f
        if on_progress and f % 120 == 0:
            on_progress()
    return "timeout"
