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


def install_exp_share(pyboy, enabled):
    """Install the Exp Share All hooks (Gen 6+ rule: participants 100 % each, other able party mons 50 %, eggs and
    fainted mons 0; replaces the held Exp. Share while on) on this emulator; `enabled()` is asked at every experience
    award (False -> the original game for that award). Returns a handle with `.remove()`. Hooks 0F:4E65, 0F:4E88
    (UpdateBattleStateAndExperienceAfterEnemyFaint), 0F:70D4, 0F:6EF8, 0F:70D1 (GiveExperiencePoints) -- details in
    expshare_gb.py. Only for the verified Crystal ROM (the code bytes are checked first; RuntimeError otherwise)."""
    from .expshare_gb import CrystalExpShare
    return CrystalExpShare(pyboy, enabled)

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

# --- Soul Link (Nuzlocke) helpers: battle / bag / Hall of Fame / forced box moves ---------------------------
# All from pokecrystal.sym and exercised in the ROM by dev/crystal/soullink_verify.py (dev/notes/soullink_crystal.md).
LINK_MODE = 0xC2DC               # wLinkMode (WRAM0): non-zero in link battles
BATTLE_MON_SPECIES = 0xC62C      # wBattleMon (WRAM0, cleared by ClearBattleRAM before wBattleMode is set)
BATTLE_MON_HP = 0xC63C           # wBattleMonHP, big endian
BATTLE_MON_MAX_HP = 0xC63E       # wBattleMonMaxHP
ENEMY_STATS = 0xC6C1             # wEnemyStats (WRAM0, 5 x 16 bit): cleared by ClearBattleRAM, written at the very
                                 # end of LoadEnemyMon -> non-zero = the wEnemyMon struct of this battle is complete
IN_BATTLE_TOWER_BATTLE = 0xCFC0  # wInBattleTowerBattle bit 0
CUR_BATTLE_MON = 0xD0D4          # wCurBattleMon: party index of the player's active mon
TEMP_ENEMY_MON_SPECIES = 0xD204  # wTempEnemyMonSpecies
ENEMY_MON_SPECIES = 0xD206       # wEnemyMon / wEnemyMonSpecies
ENEMY_MON_DVS = 0xD20C           # wEnemyMonDVs (2 bytes: atk<<4|def, spd<<4|spc)
ENEMY_MON_LEVEL = 0xD213
ENEMY_MON_MAX_HP = 0xD218
BATTLE_TYPE = 0xD230             # wBattleType (set by the overworld/script BEFORE wBattleMode; 0 after the battle)
STATUS_FLAGS = 0xD84C            # wStatusFlags: bit 6 = STATUSFLAGS_HALL_OF_FAME_F
STATUS_FLAGS2 = 0xD84D           # wStatusFlags2: bit 2 = STATUSFLAGS2_BUG_CONTEST_TIMER_F
NUM_BALLS = 0xD8D7               # wNumBalls; wBalls = (item, qty) pairs + 0xFF
BALLS = 0xD8D8
MAX_BALLS = 12
HALL_OF_FAME_COUNT = 0xD95E      # wHallOfFameCount: +1 (cap 200) each time the HallOfFame routine runs
EVENT_FLAGS = 0xDA72             # wEventFlags
EVENT_BEAT_ELITE_FOUR = 68       # set by the HALL_OF_FAME map script right before the halloffame command
PARK_BALLS_REMAINING = 0xDC79    # wParkBallsRemaining (Bug-Catching Contest)
SRAM_BOX = 1                     # sBox lives in SRAM bank 1
SBOX_COUNT = 0xAD10
SBOX_SPECIES = 0xAD11            # 20 + 0xFF terminator; 0xFD = egg
SBOX_MONS = 0xAD26               # 20 x 32
SBOX_OTS = 0xAFA6                # 20 x 11
SBOX_NICKS = 0xB082              # 20 x 11
SRAM_PARTY_MAIL = (0, 0xA600)    # sPartyMail: 6 x MAIL_STRUCT_LENGTH
MAIL_STRUCT_LENGTH = 0x2F
DAYCARE_MAN = 0xDEF5             # wDayCareMan: bit 0 HAS_MON (wBreedMon1 = nickname, OT, box struct)
BREED_MON1_NICK = 0xDEF6         # wBreedMon1Nickname; OT at +11 (DF01), box struct at +22 (DF0C)
DAYCARE_LADY = 0xDF2C            # wDayCareLady: bit 0 HAS_MON
BREED_MON2_NICK = 0xDF2F         # wBreedMon2Nickname; OT DF3A, struct DF45
CAUGHT_EGG_LEVEL = 1            # caught level written when an egg hatches (SetEggMonCaughtData)
UNOWN_DEX = 0xDED9               # wUnownDex: 26 letters (1-26) in the order first seen, 0 = free
FIRST_UNOWN_SEEN = 0xDEF4        # wFirstUnownSeen
NUM_UNOWN = 26
UNOWN = 201

# wBattleType values (constants/battle_constants.asm) and how Soul Link treats them
BATTLETYPE_NORMAL, BATTLETYPE_CANLOSE, BATTLETYPE_DEBUG, BATTLETYPE_TUTORIAL = 0, 1, 2, 3
BATTLETYPE_FISH, BATTLETYPE_ROAMING, BATTLETYPE_CONTEST, BATTLETYPE_FORCESHINY = 4, 5, 6, 7
BATTLETYPE_TREE, BATTLETYPE_TRAP, BATTLETYPE_FORCEITEM, BATTLETYPE_CELEBI, BATTLETYPE_SUICUNE = 8, 9, 10, 11, 12
SPECIAL_BATTLE_TYPES = {BATTLETYPE_DEBUG, BATTLETYPE_TUTORIAL}
# Ball pocket items (data/items/attributes.asm: pocket BALL). Apricorn balls from Kurt live here too.
BALL_ITEMS = {0x01: "MASTER BALL", 0x02: "ULTRA BALL", 0x04: "GREAT BALL", 0x05: "POKé BALL",
              0x9D: "HEAVY BALL", 0x9F: "LEVEL BALL", 0xA0: "LURE BALL", 0xA1: "FAST BALL",
              0xA4: "FRIEND BALL", 0xA5: "MOON BALL", 0xA6: "LOVE BALL", 0xB1: "PARK BALL"}
MAIL_ITEMS = {0x9E} | set(range(0xB5, 0xBE))   # MailItems: FLOWER_MAIL, SURF_MAIL .. MIRAGE_MAIL
HALL_OF_FAME_MAP = (16 << 8) | 8   # INDIGO group 16, map 8 (constants/map_constants.asm)

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

    def note_unown(self, letter: int):
        """pret UpdateUnownDex (letter 1-26 appended to wUnownDex unless listed) + wFirstUnownSeen if still 0 (as
        AddTempmonToParty does for a received UNOWN)."""
        for i in range(NUM_UNOWN):
            v = self.r(UNOWN_DEX + i)
            if v == letter:
                break
            if v == 0:
                self.w(UNOWN_DEX + i, letter)
                break
        if self.r(FIRST_UNOWN_SEEN) == 0:
            self.w(FIRST_UNOWN_SEEN, letter)

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

    # Soul Link: battle --------------------------------------------------------------------------------------
    def _enemy_loaded(self) -> bool:
        """wEnemyStats is zeroed by ClearBattleRAM (before wBattleMode is set) and written at the very end of
        LoadEnemyMon, so non-zero means wEnemyMon belongs to this battle (wEnemyMon itself is NOT cleared between
        battles and holds the previous battle's mon for a moment). Sp.Def >= 5 is never 0."""
        return (self.r(ENEMY_STATS + 8) | self.r(ENEMY_STATS + 9)) != 0

    def battle_kind(self) -> str | None:
        """'wild' | 'trainer' | 'special' (no catching / not the player's own battle) or None outside battles.

        Reliable on the first frame wBattleMode is non-zero: wBattleType is set before the battle starts."""
        mode = self.r(BATTLE_MODE)
        if mode == 0:
            return None
        if (self.r(LINK_MODE) != 0 or self.r(IN_BATTLE_TOWER_BATTLE) & 1
                or self.r(BATTLE_TYPE) in SPECIAL_BATTLE_TYPES):
            return "special"
        return "trainer" if mode == 2 else "wild"

    def battle_info(self) -> dict | None:
        """None outside battles -- and in a wild battle until the enemy mon is loaded (1 frame after wBattleMode
        turns non-zero in every measured encounter). Then: kind, dex (= species, Gen 2 ids are dex numbers),
        shiny (Gen-2 DV rule), safari (always False), contest (Bug-Catching Contest), plus battle_type, level, dvs.
        In trainer battles the dict comes right away; dex/shiny describe the trainer's current mon (dex 0 until
        the first one is sent out)."""
        kind = self.battle_kind()
        if kind is None:
            return None
        mode = self.r(BATTLE_MODE)
        loaded = self._enemy_loaded()
        if mode == 1 and not loaded:
            return None
        dvs = (self.r(ENEMY_MON_DVS) << 8) | self.r(ENEMY_MON_DVS + 1) if loaded else 0
        btype = self.r(BATTLE_TYPE)
        return {"kind": kind, "dex": self.r(ENEMY_MON_SPECIES) if loaded else 0, "shiny": loaded and is_shiny(dvs),
                "safari": False, "contest": btype == BATTLETYPE_CONTEST, "battle_type": btype,
                "level": self.r(ENEMY_MON_LEVEL) if loaded else 0, "dvs": dvs}

    def battle_party_slot(self):
        """(party index of the player's active mon, its current battle HP) or None.

        None outside battles, before the first mon is sent out (ClearBattleRAM zeroes wBattleMon) and in the
        instant of a switch where wCurBattleMon already names the new mon but wBattleMon still holds the old one
        (checked by matching species + DVs -- or level + max HP while Transformed -- against the party struct)."""
        if self.r(BATTLE_MODE) == 0:
            return None
        m = self.m
        species = m[BATTLE_MON_SPECIES]
        max_hp = (m[BATTLE_MON_MAX_HP] << 8) | m[BATTLE_MON_MAX_HP + 1]
        idx = self.r(CUR_BATTLE_MON)
        n = self.r(PARTY_COUNT)
        if species == 0 or max_hp == 0 or idx >= n or n > 6:
            return None
        p = PARTY_MONS + idx * MON_SIZE
        same = species == self.r(p) and m[0xC632] == self.r(p + 21) and m[0xC633] == self.r(p + 22)  # wBattleMonDVs
        if not same and m[0xC66C] & 0x08:                                    # wPlayerSubStatus5 TRANSFORMED
            same = (m[0xC639] == self.r(p + 31)                                 # wBattleMonLevel
                    and max_hp == (self.r(p + 36) << 8) | self.r(p + 37))
        if not same:
            return None
        return idx, (m[BATTLE_MON_HP] << 8) | m[BATTLE_MON_HP + 1]

    # Soul Link: bag / Hall of Fame ---------------------------------------------------------------------------
    def balls(self) -> list:
        """[(item id, quantity)] in the Ball pocket."""
        n = self.r(NUM_BALLS)
        if n > MAX_BALLS:
            return []
        return [(self.r(BALLS + 2 * i), self.r(BALLS + 2 * i + 1)) for i in range(n)]

    def has_poke_balls(self) -> bool:
        """Any ball in the Ball pocket (Poké/Great/Ultra/Master, Kurt's Apricorn balls, a Park Ball item).
        During the Bug-Catching Contest the game uses wParkBallsRemaining instead (see park_balls_remaining)."""
        return any(item in BALL_ITEMS and qty > 0 for item, qty in self.balls())

    def park_balls_remaining(self) -> int:
        return self.r(PARK_BALLS_REMAINING)

    def in_bug_contest(self) -> bool:
        """The contest timer runs (STATUSFLAGS2_BUG_CONTEST_TIMER_F) -- set from the gate until the contest ends."""
        return bool(self.r(STATUS_FLAGS2) & 0x04)

    def in_hall_of_fame(self) -> bool:
        """Standing in the HALL_OF_FAME map -- stays True through the HoF scene (where the game clock is paused,
        so not in_game()) until the credits reset the game."""
        return self.map_id() == HALL_OF_FAME_MAP and self.r(PLAYER_NAME) not in (0, text.TERMINATOR)

    def hall_of_fame_count(self) -> int:
        return self.r(HALL_OF_FAME_COUNT)

    def event_flag(self, n: int) -> bool:
        return bool(self.r(EVENT_FLAGS + n // 8) & (1 << (n % 8)))

    def entered_hall_of_fame(self) -> bool:
        """Persistent (saved) proof the champion has been beaten: EVENT_BEAT_ELITE_FOUR (set first, in the Hall
        of Fame map script), wStatusFlags bit 6 and wHallOfFameCount (both set by the HallOfFame routine)."""
        return (self.event_flag(EVENT_BEAT_ELITE_FOUR) or bool(self.r(STATUS_FLAGS) & 0x40)
                or self.hall_of_fame_count() > 0)

    # Soul Link: current box + forced moves (mirror the game's SendGetMonIntoFromBox / RemoveMonFromPartyOrBox) --
    def _sr(self, addr: int) -> int:
        return self.m[SRAM_BOX, addr]

    def _sw(self, addr: int, v: int):
        self.m[SRAM_BOX, addr] = v

    def _sread(self, addr: int, n: int) -> bytes:
        return bytes(self._sr(addr + i) for i in range(n))

    def _swrite(self, addr: int, data: bytes):
        for i, v in enumerate(data):
            self._sw(addr + i, v)

    def box_count(self) -> int:
        return self._sr(SBOX_COUNT)

    def current_box_records(self) -> list:
        """The current box (sBox, the copy the game deposits to / withdraws from) as 55-byte Gen2PC records."""
        return parse_box(self._sread(SBOX_COUNT, BOX_BYTES))

    def move_party_to_box(self, slot: int) -> bool:
        """Deposit party slot into the current box like Bill's PC does. Call only while is_safe().

        False (nothing changed) if the box is full, the slot doesn't exist, it would leave no other party mon
        with HP > 0 that isn't an egg (the game's "last #MON" / "no more usable #MON" rules), or the mon holds
        MAIL (the game refuses: "Remove MAIL")."""
        n = self.r(PARTY_COUNT)
        count = self.box_count()
        if not 0 <= slot < n or n > 6 or count >= MONS_PER_BOX:
            return False
        others_ok = any(self.r(PARTY_SPECIES + i) != EGG and
                        (self.r(PARTY_MONS + i * MON_SIZE + 34) | self.r(PARTY_MONS + i * MON_SIZE + 35))
                        for i in range(n) if i != slot)
        if n < 2 or not others_ok:
            return False
        data = bytearray(self.read(PARTY_MONS + slot * MON_SIZE, BOX_MON_SIZE))
        if data[1] in MAIL_ITEMS:
            return False
        if self.rom is None:
            raise RuntimeError("move_party_to_box needs RomData (move PP)")
        listed = self.r(PARTY_SPECIES + slot)
        # SendGetMonIntoFromBox (PC_DEPOSIT): append to sBox, then RestorePPOfDepositedPokemon
        for i in range(4):
            move = data[2 + i]
            if not move:
                break
            data[23 + i] = (data[23 + i] & 0xC0) + max_pp(self.rom.move_pp(move), data[23 + i] >> 6)
        self._sw(SBOX_COUNT, count + 1)
        self._sw(SBOX_SPECIES + count, listed)
        self._sw(SBOX_SPECIES + count + 1, 0xFF)
        self._swrite(SBOX_MONS + count * BOX_MON_SIZE, data)
        self._swrite(SBOX_OTS + count * NAME_SIZE, self.read(PARTY_OT + slot * NAME_SIZE, NAME_SIZE))
        self._swrite(SBOX_NICKS + count * NAME_SIZE, self.read(PARTY_NICKS + slot * NAME_SIZE, NAME_SIZE))
        self._remove_party(slot)
        return True

    def move_box_to_party(self, box_index: int) -> bool:
        """Withdraw sBox entry into the party like Bill's PC does (level from exp, stats with stat exp, status
        cleared, HP = max HP, eggs HP 0). False if the party is full or the index doesn't exist."""
        n = self.r(PARTY_COUNT)
        count = self.box_count()
        if n >= 6 or not 0 <= box_index < min(count, MONS_PER_BOX):
            return False
        if self.rom is None:
            raise RuntimeError("move_box_to_party needs RomData (growth rate, base stats)")
        listed = self._sr(SBOX_SPECIES + box_index)
        box = self._sread(SBOX_MONS + box_index * BOX_MON_SIZE, BOX_MON_SIZE)
        base = self.rom.base_stats(box[0])
        if base is None:
            return False
        p = PARTY_MONS + n * MON_SIZE
        self.w(PARTY_COUNT, n + 1)
        self.w(PARTY_SPECIES + n, listed)
        self.w(PARTY_SPECIES + n + 1, 0xFF)
        self.write(p, box)
        self.write(PARTY_OT + n * NAME_SIZE, self._sread(SBOX_OTS + box_index * NAME_SIZE, NAME_SIZE))
        self.write(PARTY_NICKS + n * NAME_SIZE, self._sread(SBOX_NICKS + box_index * NAME_SIZE, NAME_SIZE))
        data = bytearray(self.read(p, MON_SIZE))
        data[31] = calc_level(box, self.rom.growth_rate(box[0]))
        stats = calc_stats(data, base)
        for i in range(6):
            data[36 + 2 * i:38 + 2 * i] = stats[i].to_bytes(2, "big")
        data[32] = 0
        data[34:36] = bytes(2) if listed == EGG else data[36:38]
        self.w(p + 31, data[31])
        self.write(p + 32, bytes([data[32]]))          # byte 33 (unused) is left as it was, like the game
        self.write(p + 34, data[34:48])
        self._remove_box(box_index)
        return True

    def _remove_party(self, slot: int):
        """RemoveMonFromPartyOrBox (REMOVE_PARTY), byte for byte, including the sPartyMail shift."""
        n = self.r(PARTY_COUNT) - 1
        self.w(PARTY_COUNT, n)
        i = slot
        while True:                                   # species list: shift up to and including the 0xFF
            v = self.r(PARTY_SPECIES + i + 1)
            self.w(PARTY_SPECIES + i, v)
            i += 1
            if v == 0xFF:
                break
        if slot == 5:
            self.w(PARTY_OT + slot * NAME_SIZE, 0xFF)
        else:
            self._shift(PARTY_OT + slot * NAME_SIZE, NAME_SIZE, PARTY_NICKS, self.r, self.w)
            self._shift(PARTY_MONS + slot * MON_SIZE, MON_SIZE, PARTY_OT, self.r, self.w)
            self._shift(PARTY_NICKS + slot * NAME_SIZE, NAME_SIZE, PARTY_NICKS + 6 * NAME_SIZE, self.r, self.w)
        if self.r(LINK_MODE) == 0 and slot != n:
            bank, base = SRAM_PARTY_MAIL
            for b in range(slot, n):
                for k in range(MAIL_STRUCT_LENGTH):
                    self.m[bank, base + b * MAIL_STRUCT_LENGTH + k] = \
                        self.m[bank, base + (b + 1) * MAIL_STRUCT_LENGTH + k]

    def _remove_box(self, idx: int):
        """RemoveMonFromPartyOrBox (REMOVE_BOX) on sBox."""
        self._sw(SBOX_COUNT, self.box_count() - 1)
        i = idx
        while True:
            v = self._sr(SBOX_SPECIES + i + 1)
            self._sw(SBOX_SPECIES + i, v)
            i += 1
            if v == 0xFF:
                break
        if idx == MONS_PER_BOX - 1:
            self._sw(SBOX_OTS + idx * NAME_SIZE, 0xFF)
            return
        self._shift(SBOX_OTS + idx * NAME_SIZE, NAME_SIZE, SBOX_NICKS, self._sr, self._sw)
        self._shift(SBOX_MONS + idx * BOX_MON_SIZE, BOX_MON_SIZE, SBOX_OTS, self._sr, self._sw)
        self._shift(SBOX_NICKS + idx * NAME_SIZE, NAME_SIZE, SBOX_NICKS + MONS_PER_BOX * NAME_SIZE, self._sr, self._sw)

    @staticmethod
    def _shift(dst: int, size: int, end: int, rd, wr):
        """CopyDataUntil(dst + size -> dst, until the source reaches `end`)."""
        for a in range(dst + size, end):
            wr(a - size, rd(a))

    def daycare_records(self) -> list:
        """Mons left at the Route 34 Day Care as 55-byte Gen2PC records: [man's mon (wBreedMon1), lady's mon
        (wBreedMon2)], each only if its HAS_MON flag (bit 0 of wDayCareMan / wDayCareLady) is set. The game
        stores nickname, OT and the 32-byte box struct (DepositBreedmon); eggs can't be left there."""
        out = []
        for flag, nick in ((DAYCARE_MAN, BREED_MON1_NICK), (DAYCARE_LADY, BREED_MON2_NICK)):
            if self.r(flag) & 1:
                ot = self.read(nick + NAME_SIZE, NAME_SIZE)
                data = self.read(nick + 2 * NAME_SIZE, BOX_MON_SIZE)
                out.append(record(data, ot, self.read(nick, NAME_SIZE)))
        return out

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
# Soul Link helpers
# ---------------------------------------------------------------------------------------------------------
def unown_letter(dvs: int) -> int:
    """pret GetUnownLetter: the middle 2 bits of each DV (atk def spd spc) as one byte, / 10, + 1 -> 1-26."""
    atk, dfn, spd, spc = dvs >> 12, (dvs >> 8) & 15, (dvs >> 4) & 15, dvs & 15
    return (((atk & 6) << 5) | ((dfn & 6) << 3) | ((spd & 6) << 1) | ((spc & 6) >> 1)) // 10 + 1


def is_shiny(dvs: int) -> bool:
    """Gen-2 shiny rule: Def, Speed and Special DV = 10 and Attack DV in {2,3,6,7,10,11,14,15}."""
    return (dvs >> 8) & 0x2F == 0x2A and dvs & 0xFF == 0xAA


def max_pp(base: int, ups: int) -> int:
    """ComputeMaxPP: each PP Up adds base/5, but at most 7 (40-PP moves top out at 61)."""
    return (base + ups * min(base // 5, 7)) & 0x3F


def calc_level(data: bytes, growth: int) -> int:
    """CalcLevel: the highest level 2..100 whose exp requirement the mon's exp (bytes 8-10) meets, else 1."""
    exp = int.from_bytes(bytes(data[8:11]), "big")
    level = 1
    for n in range(2, 101):
        if exp < exp_for_level(growth, n):
            break
        level = n
    return level


def mon_key(record_or_party_data: bytes) -> str:
    """Identity of one mon within a save, stable across level-ups, evolution, deposit/withdraw (party struct and
    box record share offsets 0-31), nickname changes and hatching.

    OT ID (6-7) + DVs (21-22) + caught data (29-30) as 12 hex digits. Hatching writes caught data (level 1 +
    time + location) over the egg's 0000, so caught data with caught level 1 (only hatched mons have it; wild
    mons are >= level 2) is normalised to 0000 -- egg and hatched mon get the same key."""
    d = bytes(record_or_party_data)
    caught = d[29:31]
    if d[29] & 0x3F == CAUGHT_EGG_LEVEL:
        caught = b"\x00\x00"
    return (d[6:8] + d[21:23] + caught).hex()


def area_of(map_id: int) -> str:
    """Nuzlocke area key of a map (unknown ids get their own key)."""
    return AREAS.get(map_id, "map_%04x" % map_id)


def area_name(key: str) -> str:
    return AREA_NAMES.get(key, key.replace("_", " ").title())


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

    plat_rom = None             # Platinum ROM data (ndsrom.PlatinumData) if the player has that game

    def record_for(self, entry: dict) -> bytes:
        """Bytes to show in Crystal, from the entry's current representation (SPEC.md section 5): own Gen-2 data,
        else a Time-Capsule conversion of the Gen-1 data or a conversion of the Gen-4 data, else a stand-in."""
        from .pcdb import current_rep
        rep, data = current_rep(entry, "gen2")
        rec = None
        try:
            if rep == "gen2":
                return data
            if rep == "gen1":
                rec = gen1_to_gen2(data, int(entry.get("dex") or 0))
            elif rep == "gen4":
                from . import pk4
                rec = pk4.to_gen2(data, rom=self.plat_rom, g2rom=self.rom)
        except Exception:  # noqa: BLE001 - damaged data from a friend: show a stand-in instead
            rec = None
        return rec if rec is not None else self.standin(entry)

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
    def key(rec: bytes) -> str:
        return mon_key(rec)

    @staticmethod
    def is_standin(rec: bytes) -> bool:
        return rec[0] == DITTO and rec[6] == 0 and rec[7] == 0 and \
            text.decode(rec[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE]) == STANDIN_OT

    def mark_dex(self, boxes) -> list:
        """Pokédex for the mons YellowLink shows in the boxes, like the game's own trade receive (pret
        AddTempmonToParty: SetSeenAndCaughtMon, and for UNOWN GetUnownLetter + UpdateUnownDex, wFirstUnownSeen):
        stand-ins and eggs left out. Call right after write_boxes (PC open hook, main game only). Returns the dex
        numbers newly caught."""
        cm = self.cm
        new = set()
        for box in boxes:
            for rec in box:
                if self.is_standin(rec) or (len(rec) > REC_SIZE and rec[REC_SIZE]):
                    continue
                species = rec[0]
                if not 1 <= species <= NUM_SPECIES:
                    continue
                if not cm.r(DEX_CAUGHT + (species - 1) // 8) & (1 << ((species - 1) % 8)):
                    new.add(species)
                cm.set_dex(species)
                if species == UNOWN:
                    cm.note_unown(unown_letter((rec[21] << 8) | rec[22]))
        return sorted(new)


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


# --- Soul Link areas (generated by dev/crystal/gen_soullink_areas.py from pret/pokecrystal map_constants.asm + data/maps/maps.asm) ---
AREAS = {
    0x0101: 'olivine_city',  # OLIVINE_POKECENTER_1F (INDOOR)
    0x0102: 'olivine_city',  # OLIVINE_GYM (INDOOR)
    0x0103: 'olivine_city',  # OLIVINE_TIMS_HOUSE (INDOOR)
    0x0104: 'olivine_city',  # OLIVINE_HOUSE_BETA (INDOOR)
    0x0105: 'olivine_city',  # OLIVINE_PUNISHMENT_SPEECH_HOUSE (INDOOR)
    0x0106: 'olivine_city',  # OLIVINE_GOOD_ROD_HOUSE (INDOOR)
    0x0107: 'olivine_city',  # OLIVINE_CAFE (INDOOR)
    0x0108: 'olivine_city',  # OLIVINE_MART (INDOOR)
    0x0109: 'route_38',  # ROUTE_38_ECRUTEAK_GATE (GATE)
    0x010A: 'route_39',  # ROUTE_39_BARN (INDOOR)
    0x010B: 'route_39',  # ROUTE_39_FARMHOUSE (INDOOR)
    0x010C: 'route_38',  # ROUTE_38 (ROUTE)
    0x010D: 'route_39',  # ROUTE_39 (ROUTE)
    0x010E: 'olivine_city',  # OLIVINE_CITY (TOWN)
    0x0201: 'mahogany_town',  # MAHOGANY_RED_GYARADOS_SPEECH_HOUSE (INDOOR)
    0x0202: 'mahogany_town',  # MAHOGANY_GYM (INDOOR)
    0x0203: 'mahogany_town',  # MAHOGANY_POKECENTER_1F (INDOOR)
    0x0204: 'route_42',  # ROUTE_42_ECRUTEAK_GATE (GATE)
    0x0205: 'route_42',  # ROUTE_42 (ROUTE)
    0x0206: 'route_44',  # ROUTE_44 (ROUTE)
    0x0207: 'mahogany_town',  # MAHOGANY_TOWN (TOWN)
    0x0301: 'sprout_tower',  # SPROUT_TOWER_1F (DUNGEON)
    0x0302: 'sprout_tower',  # SPROUT_TOWER_2F (DUNGEON)
    0x0303: 'sprout_tower',  # SPROUT_TOWER_3F (DUNGEON)
    0x0304: 'tin_tower',  # TIN_TOWER_1F (DUNGEON)
    0x0305: 'tin_tower',  # TIN_TOWER_2F (DUNGEON)
    0x0306: 'tin_tower',  # TIN_TOWER_3F (DUNGEON)
    0x0307: 'tin_tower',  # TIN_TOWER_4F (DUNGEON)
    0x0308: 'tin_tower',  # TIN_TOWER_5F (DUNGEON)
    0x0309: 'tin_tower',  # TIN_TOWER_6F (DUNGEON)
    0x030A: 'tin_tower',  # TIN_TOWER_7F (DUNGEON)
    0x030B: 'tin_tower',  # TIN_TOWER_8F (DUNGEON)
    0x030C: 'tin_tower',  # TIN_TOWER_9F (DUNGEON)
    0x030D: 'burned_tower',  # BURNED_TOWER_1F (DUNGEON)
    0x030E: 'burned_tower',  # BURNED_TOWER_B1F (CAVE)
    0x030F: 'national_park',  # NATIONAL_PARK (ROUTE)
    0x0310: 'national_park',  # NATIONAL_PARK_BUG_CONTEST (ROUTE)
    0x0311: 'radio_tower',  # RADIO_TOWER_1F (INDOOR)
    0x0312: 'radio_tower',  # RADIO_TOWER_2F (INDOOR)
    0x0313: 'radio_tower',  # RADIO_TOWER_3F (INDOOR)
    0x0314: 'radio_tower',  # RADIO_TOWER_4F (INDOOR)
    0x0315: 'radio_tower',  # RADIO_TOWER_5F (INDOOR)
    0x0316: 'ruins_of_alph',  # RUINS_OF_ALPH_OUTSIDE (ROUTE)
    0x0317: 'ruins_of_alph',  # RUINS_OF_ALPH_HO_OH_CHAMBER (DUNGEON)
    0x0318: 'ruins_of_alph',  # RUINS_OF_ALPH_KABUTO_CHAMBER (DUNGEON)
    0x0319: 'ruins_of_alph',  # RUINS_OF_ALPH_OMANYTE_CHAMBER (DUNGEON)
    0x031A: 'ruins_of_alph',  # RUINS_OF_ALPH_AERODACTYL_CHAMBER (DUNGEON)
    0x031B: 'ruins_of_alph',  # RUINS_OF_ALPH_INNER_CHAMBER (DUNGEON)
    0x031C: 'ruins_of_alph',  # RUINS_OF_ALPH_RESEARCH_CENTER (INDOOR)
    0x031D: 'ruins_of_alph',  # RUINS_OF_ALPH_HO_OH_ITEM_ROOM (DUNGEON)
    0x031E: 'ruins_of_alph',  # RUINS_OF_ALPH_KABUTO_ITEM_ROOM (DUNGEON)
    0x031F: 'ruins_of_alph',  # RUINS_OF_ALPH_OMANYTE_ITEM_ROOM (DUNGEON)
    0x0320: 'ruins_of_alph',  # RUINS_OF_ALPH_AERODACTYL_ITEM_ROOM (DUNGEON)
    0x0321: 'ruins_of_alph',  # RUINS_OF_ALPH_HO_OH_WORD_ROOM (DUNGEON)
    0x0322: 'ruins_of_alph',  # RUINS_OF_ALPH_KABUTO_WORD_ROOM (DUNGEON)
    0x0323: 'ruins_of_alph',  # RUINS_OF_ALPH_OMANYTE_WORD_ROOM (DUNGEON)
    0x0324: 'ruins_of_alph',  # RUINS_OF_ALPH_AERODACTYL_WORD_ROOM (DUNGEON)
    0x0325: 'union_cave',  # UNION_CAVE_1F (CAVE)
    0x0326: 'union_cave',  # UNION_CAVE_B1F (CAVE)
    0x0327: 'union_cave',  # UNION_CAVE_B2F (CAVE)
    0x0328: 'slowpoke_well',  # SLOWPOKE_WELL_B1F (CAVE)
    0x0329: 'slowpoke_well',  # SLOWPOKE_WELL_B2F (CAVE)
    0x032A: 'olivine_lighthouse',  # OLIVINE_LIGHTHOUSE_1F (DUNGEON)
    0x032B: 'olivine_lighthouse',  # OLIVINE_LIGHTHOUSE_2F (DUNGEON)
    0x032C: 'olivine_lighthouse',  # OLIVINE_LIGHTHOUSE_3F (DUNGEON)
    0x032D: 'olivine_lighthouse',  # OLIVINE_LIGHTHOUSE_4F (DUNGEON)
    0x032E: 'olivine_lighthouse',  # OLIVINE_LIGHTHOUSE_5F (DUNGEON)
    0x032F: 'olivine_lighthouse',  # OLIVINE_LIGHTHOUSE_6F (DUNGEON)
    0x0330: 'mahogany_town',  # MAHOGANY_MART_1F (INDOOR)
    0x0331: 'team_rocket_base',  # TEAM_ROCKET_BASE_B1F (DUNGEON)
    0x0332: 'team_rocket_base',  # TEAM_ROCKET_BASE_B2F (DUNGEON)
    0x0333: 'team_rocket_base',  # TEAM_ROCKET_BASE_B3F (DUNGEON)
    0x0334: 'ilex_forest',  # ILEX_FOREST (CAVE)
    0x0335: 'goldenrod_underground',  # GOLDENROD_UNDERGROUND (DUNGEON)
    0x0336: 'goldenrod_underground',  # GOLDENROD_UNDERGROUND_SWITCH_ROOM_ENTRANCES (DUNGEON)
    0x0337: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_B1F (DUNGEON)
    0x0338: 'goldenrod_underground',  # GOLDENROD_UNDERGROUND_WAREHOUSE (DUNGEON)
    0x0339: 'mt_mortar',  # MOUNT_MORTAR_1F_OUTSIDE (CAVE)
    0x033A: 'mt_mortar',  # MOUNT_MORTAR_1F_INSIDE (CAVE)
    0x033B: 'mt_mortar',  # MOUNT_MORTAR_2F_INSIDE (CAVE)
    0x033C: 'mt_mortar',  # MOUNT_MORTAR_B1F (CAVE)
    0x033D: 'ice_path',  # ICE_PATH_1F (CAVE)
    0x033E: 'ice_path',  # ICE_PATH_B1F (CAVE)
    0x033F: 'ice_path',  # ICE_PATH_B2F_MAHOGANY_SIDE (CAVE)
    0x0340: 'ice_path',  # ICE_PATH_B2F_BLACKTHORN_SIDE (CAVE)
    0x0341: 'ice_path',  # ICE_PATH_B3F (CAVE)
    0x0342: 'whirl_islands',  # WHIRL_ISLAND_NW (CAVE)
    0x0343: 'whirl_islands',  # WHIRL_ISLAND_NE (CAVE)
    0x0344: 'whirl_islands',  # WHIRL_ISLAND_SW (CAVE)
    0x0345: 'whirl_islands',  # WHIRL_ISLAND_CAVE (CAVE)
    0x0346: 'whirl_islands',  # WHIRL_ISLAND_SE (CAVE)
    0x0347: 'whirl_islands',  # WHIRL_ISLAND_B1F (CAVE)
    0x0348: 'whirl_islands',  # WHIRL_ISLAND_B2F (CAVE)
    0x0349: 'whirl_islands',  # WHIRL_ISLAND_LUGIA_CHAMBER (CAVE)
    0x034A: 'mt_silver',  # SILVER_CAVE_ROOM_1 (CAVE)
    0x034B: 'mt_silver',  # SILVER_CAVE_ROOM_2 (CAVE)
    0x034C: 'mt_silver',  # SILVER_CAVE_ROOM_3 (CAVE)
    0x034D: 'mt_silver',  # SILVER_CAVE_ITEM_ROOMS (CAVE)
    0x034E: 'dark_cave',  # DARK_CAVE_VIOLET_ENTRANCE (CAVE)
    0x034F: 'dark_cave',  # DARK_CAVE_BLACKTHORN_ENTRANCE (CAVE)
    0x0350: 'dragons_den',  # DRAGONS_DEN_1F (CAVE)
    0x0351: 'dragons_den',  # DRAGONS_DEN_B1F (CAVE)
    0x0352: 'dragons_den',  # DRAGON_SHRINE (INDOOR)
    0x0353: 'tohjo_falls',  # TOHJO_FALLS (CAVE)
    0x0354: 'digletts_cave',  # DIGLETTS_CAVE (CAVE)
    0x0355: 'mt_moon',  # MOUNT_MOON (CAVE)
    0x0356: 'underground_path',  # UNDERGROUND_PATH (GATE)
    0x0357: 'rock_tunnel',  # ROCK_TUNNEL_1F (CAVE)
    0x0358: 'rock_tunnel',  # ROCK_TUNNEL_B1F (CAVE)
    0x0359: 'fuchsia_city',  # SAFARI_ZONE_FUCHSIA_GATE_BETA (INDOOR)
    0x035A: 'fuchsia_city',  # SAFARI_ZONE_BETA (CAVE)
    0x035B: 'victory_road',  # VICTORY_ROAD (CAVE)
    0x0401: 'ecruteak_city',  # ECRUTEAK_TIN_TOWER_ENTRANCE (INDOOR)
    0x0402: 'ecruteak_city',  # WISE_TRIOS_ROOM (INDOOR)
    0x0403: 'ecruteak_city',  # ECRUTEAK_POKECENTER_1F (INDOOR)
    0x0404: 'ecruteak_city',  # ECRUTEAK_LUGIA_SPEECH_HOUSE (INDOOR)
    0x0405: 'ecruteak_city',  # DANCE_THEATER (INDOOR)
    0x0406: 'ecruteak_city',  # ECRUTEAK_MART (INDOOR)
    0x0407: 'ecruteak_city',  # ECRUTEAK_GYM (INDOOR)
    0x0408: 'ecruteak_city',  # ECRUTEAK_ITEMFINDER_HOUSE (INDOOR)
    0x0409: 'ecruteak_city',  # ECRUTEAK_CITY (TOWN)
    0x0501: 'blackthorn_city',  # BLACKTHORN_GYM_1F (INDOOR)
    0x0502: 'blackthorn_city',  # BLACKTHORN_GYM_2F (INDOOR)
    0x0503: 'blackthorn_city',  # BLACKTHORN_DRAGON_SPEECH_HOUSE (INDOOR)
    0x0504: 'blackthorn_city',  # BLACKTHORN_EMYS_HOUSE (INDOOR)
    0x0505: 'blackthorn_city',  # BLACKTHORN_MART (INDOOR)
    0x0506: 'blackthorn_city',  # BLACKTHORN_POKECENTER_1F (INDOOR)
    0x0507: 'blackthorn_city',  # MOVE_DELETERS_HOUSE (INDOOR)
    0x0508: 'route_45',  # ROUTE_45 (ROUTE)
    0x0509: 'route_46',  # ROUTE_46 (ROUTE)
    0x050A: 'blackthorn_city',  # BLACKTHORN_CITY (TOWN)
    0x0601: 'cinnabar_island',  # CINNABAR_POKECENTER_1F (INDOOR)
    0x0602: 'cinnabar_island',  # CINNABAR_POKECENTER_2F_BETA (INDOOR)
    0x0603: 'route_19',  # ROUTE_19_FUCHSIA_GATE (GATE)
    0x0604: 'seafoam_islands',  # SEAFOAM_GYM (INDOOR)
    0x0605: 'route_19',  # ROUTE_19 (ROUTE)
    0x0606: 'route_20',  # ROUTE_20 (ROUTE)
    0x0607: 'route_21',  # ROUTE_21 (ROUTE)
    0x0608: 'cinnabar_island',  # CINNABAR_ISLAND (TOWN)
    0x0701: 'cerulean_city',  # CERULEAN_GYM_BADGE_SPEECH_HOUSE (INDOOR)
    0x0702: 'cerulean_city',  # CERULEAN_POLICE_STATION (INDOOR)
    0x0703: 'cerulean_city',  # CERULEAN_TRADE_SPEECH_HOUSE (INDOOR)
    0x0704: 'cerulean_city',  # CERULEAN_POKECENTER_1F (INDOOR)
    0x0705: 'cerulean_city',  # CERULEAN_POKECENTER_2F_BETA (INDOOR)
    0x0706: 'cerulean_city',  # CERULEAN_GYM (INDOOR)
    0x0707: 'cerulean_city',  # CERULEAN_MART (INDOOR)
    0x0708: 'route_10',  # ROUTE_10_POKECENTER_1F (INDOOR)
    0x0709: 'route_10',  # ROUTE_10_POKECENTER_2F_BETA (INDOOR)
    0x070A: 'power_plant',  # POWER_PLANT (INDOOR)
    0x070B: 'route_25',  # BILLS_HOUSE (INDOOR)
    0x070C: 'route_4',  # ROUTE_4 (ROUTE)
    0x070D: 'route_9',  # ROUTE_9 (ROUTE)
    0x070E: 'route_10',  # ROUTE_10_NORTH (ROUTE)
    0x070F: 'route_24',  # ROUTE_24 (ROUTE)
    0x0710: 'route_25',  # ROUTE_25 (ROUTE)
    0x0711: 'cerulean_city',  # CERULEAN_CITY (TOWN)
    0x0801: 'azalea_town',  # AZALEA_POKECENTER_1F (INDOOR)
    0x0802: 'azalea_town',  # CHARCOAL_KILN (INDOOR)
    0x0803: 'azalea_town',  # AZALEA_MART (INDOOR)
    0x0804: 'azalea_town',  # KURTS_HOUSE (INDOOR)
    0x0805: 'azalea_town',  # AZALEA_GYM (INDOOR)
    0x0806: 'route_33',  # ROUTE_33 (ROUTE)
    0x0807: 'azalea_town',  # AZALEA_TOWN (TOWN)
    0x0901: 'lake_of_rage',  # LAKE_OF_RAGE_HIDDEN_POWER_HOUSE (INDOOR)
    0x0902: 'lake_of_rage',  # LAKE_OF_RAGE_MAGIKARP_HOUSE (INDOOR)
    0x0903: 'route_43',  # ROUTE_43_MAHOGANY_GATE (GATE)
    0x0904: 'route_43',  # ROUTE_43_GATE (GATE)
    0x0905: 'route_43',  # ROUTE_43 (ROUTE)
    0x0906: 'lake_of_rage',  # LAKE_OF_RAGE (TOWN)
    0x0A01: 'route_32',  # ROUTE_32 (ROUTE)
    0x0A02: 'route_35',  # ROUTE_35 (ROUTE)
    0x0A03: 'route_36',  # ROUTE_36 (ROUTE)
    0x0A04: 'route_37',  # ROUTE_37 (ROUTE)
    0x0A05: 'violet_city',  # VIOLET_CITY (TOWN)
    0x0A06: 'violet_city',  # VIOLET_MART (INDOOR)
    0x0A07: 'violet_city',  # VIOLET_GYM (INDOOR)
    0x0A08: 'violet_city',  # EARLS_POKEMON_ACADEMY (INDOOR)
    0x0A09: 'violet_city',  # VIOLET_NICKNAME_SPEECH_HOUSE (INDOOR)
    0x0A0A: 'violet_city',  # VIOLET_POKECENTER_1F (INDOOR)
    0x0A0B: 'violet_city',  # VIOLET_KYLES_HOUSE (INDOOR)
    0x0A0C: 'route_32',  # ROUTE_32_RUINS_OF_ALPH_GATE (GATE)
    0x0A0D: 'route_32',  # ROUTE_32_POKECENTER_1F (INDOOR)
    0x0A0E: 'route_35',  # ROUTE_35_GOLDENROD_GATE (GATE)
    0x0A0F: 'route_35',  # ROUTE_35_NATIONAL_PARK_GATE (INDOOR)
    0x0A10: 'route_36',  # ROUTE_36_RUINS_OF_ALPH_GATE (GATE)
    0x0A11: 'route_36',  # ROUTE_36_NATIONAL_PARK_GATE (INDOOR)
    0x0B01: 'route_34',  # ROUTE_34 (ROUTE)
    0x0B02: 'goldenrod_city',  # GOLDENROD_CITY (TOWN)
    0x0B03: 'goldenrod_city',  # GOLDENROD_GYM (INDOOR)
    0x0B04: 'goldenrod_city',  # GOLDENROD_BIKE_SHOP (INDOOR)
    0x0B05: 'goldenrod_city',  # GOLDENROD_HAPPINESS_RATER (INDOOR)
    0x0B06: 'goldenrod_city',  # BILLS_FAMILYS_HOUSE (INDOOR)
    0x0B07: 'goldenrod_city',  # GOLDENROD_MAGNET_TRAIN_STATION (INDOOR)
    0x0B08: 'goldenrod_city',  # GOLDENROD_FLOWER_SHOP (INDOOR)
    0x0B09: 'goldenrod_city',  # GOLDENROD_PP_SPEECH_HOUSE (INDOOR)
    0x0B0A: 'goldenrod_city',  # GOLDENROD_NAME_RATER (INDOOR)
    0x0B0B: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_1F (INDOOR)
    0x0B0C: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_2F (INDOOR)
    0x0B0D: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_3F (INDOOR)
    0x0B0E: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_4F (INDOOR)
    0x0B0F: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_5F (INDOOR)
    0x0B10: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_6F (INDOOR)
    0x0B11: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_ELEVATOR (INDOOR)
    0x0B12: 'goldenrod_city',  # GOLDENROD_DEPT_STORE_ROOF (INDOOR)
    0x0B13: 'goldenrod_city',  # GOLDENROD_GAME_CORNER (INDOOR)
    0x0B14: 'goldenrod_city',  # GOLDENROD_POKECENTER_1F (INDOOR)
    0x0B15: 'goldenrod_city',  # POKECOM_CENTER_ADMIN_OFFICE_MOBILE (INDOOR)
    0x0B16: 'route_34',  # ILEX_FOREST_AZALEA_GATE (GATE)
    0x0B17: 'route_34',  # ROUTE_34_ILEX_FOREST_GATE (GATE)
    0x0B18: 'route_34',  # DAY_CARE (INDOOR)
    0x0C01: 'route_6',  # ROUTE_6 (ROUTE)
    0x0C02: 'route_11',  # ROUTE_11 (ROUTE)
    0x0C03: 'vermilion_city',  # VERMILION_CITY (TOWN)
    0x0C04: 'vermilion_city',  # VERMILION_FISHING_SPEECH_HOUSE (INDOOR)
    0x0C05: 'vermilion_city',  # VERMILION_POKECENTER_1F (INDOOR)
    0x0C06: 'vermilion_city',  # VERMILION_POKECENTER_2F_BETA (INDOOR)
    0x0C07: 'vermilion_city',  # POKEMON_FAN_CLUB (INDOOR)
    0x0C08: 'vermilion_city',  # VERMILION_MAGNET_TRAIN_SPEECH_HOUSE (INDOOR)
    0x0C09: 'vermilion_city',  # VERMILION_MART (INDOOR)
    0x0C0A: 'vermilion_city',  # VERMILION_DIGLETTS_CAVE_SPEECH_HOUSE (INDOOR)
    0x0C0B: 'vermilion_city',  # VERMILION_GYM (INDOOR)
    0x0C0C: 'route_6',  # ROUTE_6_SAFFRON_GATE (GATE)
    0x0C0D: 'route_6',  # ROUTE_6_UNDERGROUND_PATH_ENTRANCE (GATE)
    0x0D01: 'route_1',  # ROUTE_1 (ROUTE)
    0x0D02: 'pallet_town',  # PALLET_TOWN (TOWN)
    0x0D03: 'pallet_town',  # REDS_HOUSE_1F (INDOOR)
    0x0D04: 'pallet_town',  # REDS_HOUSE_2F (INDOOR)
    0x0D05: 'pallet_town',  # BLUES_HOUSE (INDOOR)
    0x0D06: 'pallet_town',  # OAKS_LAB (INDOOR)
    0x0E01: 'route_3',  # ROUTE_3 (ROUTE)
    0x0E02: 'pewter_city',  # PEWTER_CITY (TOWN)
    0x0E03: 'pewter_city',  # PEWTER_NIDORAN_SPEECH_HOUSE (INDOOR)
    0x0E04: 'pewter_city',  # PEWTER_GYM (INDOOR)
    0x0E05: 'pewter_city',  # PEWTER_MART (INDOOR)
    0x0E06: 'pewter_city',  # PEWTER_POKECENTER_1F (INDOOR)
    0x0E07: 'pewter_city',  # PEWTER_POKECENTER_2F_BETA (INDOOR)
    0x0E08: 'pewter_city',  # PEWTER_SNOOZE_SPEECH_HOUSE (INDOOR)
    0x0F01: 'olivine_port',  # OLIVINE_PORT (ROUTE)
    0x0F02: 'vermilion_port',  # VERMILION_PORT (ROUTE)
    0x0F03: 'fast_ship',  # FAST_SHIP_1F (INDOOR)
    0x0F04: 'fast_ship',  # FAST_SHIP_CABINS_NNW_NNE_NE (INDOOR)
    0x0F05: 'fast_ship',  # FAST_SHIP_CABINS_SW_SSW_NW (INDOOR)
    0x0F06: 'fast_ship',  # FAST_SHIP_CABINS_SE_SSE_CAPTAINS_CABIN (INDOOR)
    0x0F07: 'fast_ship',  # FAST_SHIP_B1F (INDOOR)
    0x0F08: 'olivine_port',  # OLIVINE_PORT_PASSAGE (INDOOR)
    0x0F09: 'vermilion_port',  # VERMILION_PORT_PASSAGE (INDOOR)
    0x0F0A: 'mt_moon',  # MOUNT_MOON_SQUARE (ROUTE)
    0x0F0B: 'mt_moon',  # MOUNT_MOON_GIFT_SHOP (INDOOR)
    0x0F0C: 'tin_tower',  # TIN_TOWER_ROOF (ROUTE)
    0x1001: 'route_23',  # ROUTE_23 (TOWN)
    0x1002: 'indigo_plateau',  # INDIGO_PLATEAU_POKECENTER_1F (INDOOR)
    0x1003: 'indigo_plateau',  # WILLS_ROOM (INDOOR)
    0x1004: 'indigo_plateau',  # KOGAS_ROOM (INDOOR)
    0x1005: 'indigo_plateau',  # BRUNOS_ROOM (INDOOR)
    0x1006: 'indigo_plateau',  # KARENS_ROOM (INDOOR)
    0x1007: 'indigo_plateau',  # LANCES_ROOM (INDOOR)
    0x1008: 'indigo_plateau',  # HALL_OF_FAME (INDOOR)
    0x1101: 'route_13',  # ROUTE_13 (ROUTE)
    0x1102: 'route_14',  # ROUTE_14 (ROUTE)
    0x1103: 'route_15',  # ROUTE_15 (ROUTE)
    0x1104: 'route_18',  # ROUTE_18 (ROUTE)
    0x1105: 'fuchsia_city',  # FUCHSIA_CITY (TOWN)
    0x1106: 'fuchsia_city',  # FUCHSIA_MART (INDOOR)
    0x1107: 'fuchsia_city',  # SAFARI_ZONE_MAIN_OFFICE (INDOOR)
    0x1108: 'fuchsia_city',  # FUCHSIA_GYM (INDOOR)
    0x1109: 'fuchsia_city',  # BILLS_OLDER_SISTERS_HOUSE (INDOOR)
    0x110A: 'fuchsia_city',  # FUCHSIA_POKECENTER_1F (INDOOR)
    0x110B: 'fuchsia_city',  # FUCHSIA_POKECENTER_2F_BETA (INDOOR)
    0x110C: 'fuchsia_city',  # SAFARI_ZONE_WARDENS_HOME (INDOOR)
    0x110D: 'route_15',  # ROUTE_15_FUCHSIA_GATE (GATE)
    0x1201: 'route_8',  # ROUTE_8 (ROUTE)
    0x1202: 'route_12',  # ROUTE_12 (ROUTE)
    0x1203: 'route_10',  # ROUTE_10_SOUTH (ROUTE)
    0x1204: 'lavender_town',  # LAVENDER_TOWN (TOWN)
    0x1205: 'lavender_town',  # LAVENDER_POKECENTER_1F (INDOOR)
    0x1206: 'lavender_town',  # LAVENDER_POKECENTER_2F_BETA (INDOOR)
    0x1207: 'lavender_town',  # MR_FUJIS_HOUSE (INDOOR)
    0x1208: 'lavender_town',  # LAVENDER_SPEECH_HOUSE (INDOOR)
    0x1209: 'lavender_town',  # LAVENDER_NAME_RATER (INDOOR)
    0x120A: 'lavender_town',  # LAVENDER_MART (INDOOR)
    0x120B: 'lavender_town',  # SOUL_HOUSE (INDOOR)
    0x120C: 'lavender_radio_tower',  # LAV_RADIO_TOWER_1F (INDOOR)
    0x120D: 'route_8',  # ROUTE_8_SAFFRON_GATE (GATE)
    0x120E: 'route_12',  # ROUTE_12_SUPER_ROD_HOUSE (INDOOR)
    0x1301: 'route_28',  # ROUTE_28 (ROUTE)
    0x1302: 'mt_silver',  # SILVER_CAVE_OUTSIDE (TOWN)
    0x1303: 'mt_silver',  # SILVER_CAVE_POKECENTER_1F (INDOOR)
    0x1304: 'route_28',  # ROUTE_28_STEEL_WING_HOUSE (INDOOR)
    0x1401: 'pokecenter_2f',  # POKECENTER_2F (INDOOR)
    0x1402: 'trade_center',  # TRADE_CENTER (INDOOR)
    0x1403: 'colosseum',  # COLOSSEUM (INDOOR)
    0x1404: 'time_capsule',  # TIME_CAPSULE (INDOOR)
    0x1405: 'mobile_trade_room',  # MOBILE_TRADE_ROOM (INDOOR)
    0x1406: 'mobile_battle_room',  # MOBILE_BATTLE_ROOM (INDOOR)
    0x1501: 'route_7',  # ROUTE_7 (ROUTE)
    0x1502: 'route_16',  # ROUTE_16 (ROUTE)
    0x1503: 'route_17',  # ROUTE_17 (ROUTE)
    0x1504: 'celadon_city',  # CELADON_CITY (TOWN)
    0x1505: 'celadon_city',  # CELADON_DEPT_STORE_1F (INDOOR)
    0x1506: 'celadon_city',  # CELADON_DEPT_STORE_2F (INDOOR)
    0x1507: 'celadon_city',  # CELADON_DEPT_STORE_3F (INDOOR)
    0x1508: 'celadon_city',  # CELADON_DEPT_STORE_4F (INDOOR)
    0x1509: 'celadon_city',  # CELADON_DEPT_STORE_5F (INDOOR)
    0x150A: 'celadon_city',  # CELADON_DEPT_STORE_6F (INDOOR)
    0x150B: 'celadon_city',  # CELADON_DEPT_STORE_ELEVATOR (INDOOR)
    0x150C: 'celadon_city',  # CELADON_MANSION_1F (INDOOR)
    0x150D: 'celadon_city',  # CELADON_MANSION_2F (INDOOR)
    0x150E: 'celadon_city',  # CELADON_MANSION_3F (INDOOR)
    0x150F: 'celadon_city',  # CELADON_MANSION_ROOF (INDOOR)
    0x1510: 'celadon_city',  # CELADON_MANSION_ROOF_HOUSE (INDOOR)
    0x1511: 'celadon_city',  # CELADON_POKECENTER_1F (INDOOR)
    0x1512: 'celadon_city',  # CELADON_POKECENTER_2F_BETA (INDOOR)
    0x1513: 'celadon_city',  # CELADON_GAME_CORNER (INDOOR)
    0x1514: 'celadon_city',  # CELADON_GAME_CORNER_PRIZE_ROOM (INDOOR)
    0x1515: 'celadon_city',  # CELADON_GYM (INDOOR)
    0x1516: 'celadon_city',  # CELADON_CAFE (INDOOR)
    0x1517: 'route_16',  # ROUTE_16_FUCHSIA_SPEECH_HOUSE (INDOOR)
    0x1518: 'route_16',  # ROUTE_16_GATE (GATE)
    0x1519: 'route_7',  # ROUTE_7_SAFFRON_GATE (GATE)
    0x151A: 'route_17',  # ROUTE_17_ROUTE_18_GATE (GATE)
    0x1601: 'route_40',  # ROUTE_40 (ROUTE)
    0x1602: 'route_41',  # ROUTE_41 (ROUTE)
    0x1603: 'cianwood_city',  # CIANWOOD_CITY (TOWN)
    0x1604: 'cianwood_city',  # MANIAS_HOUSE (INDOOR)
    0x1605: 'cianwood_city',  # CIANWOOD_GYM (INDOOR)
    0x1606: 'cianwood_city',  # CIANWOOD_POKECENTER_1F (INDOOR)
    0x1607: 'cianwood_city',  # CIANWOOD_PHARMACY (INDOOR)
    0x1608: 'cianwood_city',  # CIANWOOD_PHOTO_STUDIO (INDOOR)
    0x1609: 'cianwood_city',  # CIANWOOD_LUGIA_SPEECH_HOUSE (INDOOR)
    0x160A: 'cianwood_city',  # POKE_SEERS_HOUSE (INDOOR)
    0x160B: 'battle_tower',  # BATTLE_TOWER_1F (INDOOR)
    0x160C: 'battle_tower',  # BATTLE_TOWER_BATTLE_ROOM (INDOOR)
    0x160D: 'battle_tower',  # BATTLE_TOWER_ELEVATOR (INDOOR)
    0x160E: 'battle_tower',  # BATTLE_TOWER_HALLWAY (INDOOR)
    0x160F: 'battle_tower',  # ROUTE_40_BATTLE_TOWER_GATE (GATE)
    0x1610: 'battle_tower',  # BATTLE_TOWER_OUTSIDE (ROUTE)
    0x1701: 'route_2',  # ROUTE_2 (ROUTE)
    0x1702: 'route_22',  # ROUTE_22 (ROUTE)
    0x1703: 'viridian_city',  # VIRIDIAN_CITY (TOWN)
    0x1704: 'viridian_city',  # VIRIDIAN_GYM (INDOOR)
    0x1705: 'viridian_city',  # VIRIDIAN_NICKNAME_SPEECH_HOUSE (INDOOR)
    0x1706: 'viridian_city',  # TRAINER_HOUSE_1F (INDOOR)
    0x1707: 'viridian_city',  # TRAINER_HOUSE_B1F (INDOOR)
    0x1708: 'viridian_city',  # VIRIDIAN_MART (INDOOR)
    0x1709: 'viridian_city',  # VIRIDIAN_POKECENTER_1F (INDOOR)
    0x170A: 'viridian_city',  # VIRIDIAN_POKECENTER_2F_BETA (INDOOR)
    0x170B: 'route_2',  # ROUTE_2_NUGGET_HOUSE (INDOOR)
    0x170C: 'route_2',  # ROUTE_2_GATE (GATE)
    0x170D: 'route_26',  # VICTORY_ROAD_GATE (GATE)
    0x1801: 'route_26',  # ROUTE_26 (ROUTE)
    0x1802: 'route_27',  # ROUTE_27 (ROUTE)
    0x1803: 'route_29',  # ROUTE_29 (ROUTE)
    0x1804: 'new_bark_town',  # NEW_BARK_TOWN (TOWN)
    0x1805: 'new_bark_town',  # ELMS_LAB (INDOOR)
    0x1806: 'new_bark_town',  # PLAYERS_HOUSE_1F (INDOOR)
    0x1807: 'new_bark_town',  # PLAYERS_HOUSE_2F (INDOOR)
    0x1808: 'new_bark_town',  # PLAYERS_NEIGHBORS_HOUSE (INDOOR)
    0x1809: 'new_bark_town',  # ELMS_HOUSE (INDOOR)
    0x180A: 'route_26',  # ROUTE_26_HEAL_HOUSE (INDOOR)
    0x180B: 'route_26',  # DAY_OF_WEEK_SIBLINGS_HOUSE (INDOOR)
    0x180C: 'route_27',  # ROUTE_27_SANDSTORM_HOUSE (INDOOR)
    0x180D: 'route_29',  # ROUTE_29_ROUTE_46_GATE (GATE)
    0x1901: 'route_5',  # ROUTE_5 (ROUTE)
    0x1902: 'saffron_city',  # SAFFRON_CITY (TOWN)
    0x1903: 'saffron_city',  # FIGHTING_DOJO (INDOOR)
    0x1904: 'saffron_city',  # SAFFRON_GYM (INDOOR)
    0x1905: 'saffron_city',  # SAFFRON_MART (INDOOR)
    0x1906: 'saffron_city',  # SAFFRON_POKECENTER_1F (INDOOR)
    0x1907: 'saffron_city',  # SAFFRON_POKECENTER_2F_BETA (INDOOR)
    0x1908: 'saffron_city',  # MR_PSYCHICS_HOUSE (INDOOR)
    0x1909: 'saffron_city',  # SAFFRON_MAGNET_TRAIN_STATION (INDOOR)
    0x190A: 'saffron_city',  # SILPH_CO_1F (INDOOR)
    0x190B: 'saffron_city',  # COPYCATS_HOUSE_1F (INDOOR)
    0x190C: 'saffron_city',  # COPYCATS_HOUSE_2F (INDOOR)
    0x190D: 'route_5',  # ROUTE_5_UNDERGROUND_PATH_ENTRANCE (GATE)
    0x190E: 'route_5',  # ROUTE_5_SAFFRON_GATE (GATE)
    0x190F: 'route_5',  # ROUTE_5_CLEANSE_TAG_HOUSE (INDOOR)
    0x1A01: 'route_30',  # ROUTE_30 (ROUTE)
    0x1A02: 'route_31',  # ROUTE_31 (ROUTE)
    0x1A03: 'cherrygrove_city',  # CHERRYGROVE_CITY (TOWN)
    0x1A04: 'cherrygrove_city',  # CHERRYGROVE_MART (INDOOR)
    0x1A05: 'cherrygrove_city',  # CHERRYGROVE_POKECENTER_1F (INDOOR)
    0x1A06: 'cherrygrove_city',  # CHERRYGROVE_GYM_SPEECH_HOUSE (INDOOR)
    0x1A07: 'cherrygrove_city',  # GUIDE_GENTS_HOUSE (INDOOR)
    0x1A08: 'cherrygrove_city',  # CHERRYGROVE_EVOLUTION_SPEECH_HOUSE (INDOOR)
    0x1A09: 'route_30',  # ROUTE_30_BERRY_HOUSE (INDOOR)
    0x1A0A: 'route_30',  # MR_POKEMONS_HOUSE (INDOOR)
    0x1A0B: 'route_31',  # ROUTE_31_VIOLET_GATE (GATE)
}
AREA_NAMES = {
    'azalea_town': 'Azalea Town',
    'battle_tower': 'Battle Tower',
    'blackthorn_city': 'Blackthorn City',
    'burned_tower': 'Burned Tower',
    'celadon_city': 'Celadon City',
    'cerulean_city': 'Cerulean City',
    'cherrygrove_city': 'Cherrygrove City',
    'cianwood_city': 'Cianwood City',
    'cinnabar_island': 'Cinnabar Island',
    'colosseum': 'Colosseum',
    'dark_cave': 'Dark Cave',
    'digletts_cave': "Diglett's Cave",
    'dragons_den': "Dragon's Den",
    'ecruteak_city': 'Ecruteak City',
    'fast_ship': 'Fast Ship',
    'fuchsia_city': 'Fuchsia City',
    'goldenrod_city': 'Goldenrod City',
    'goldenrod_underground': 'Goldenrod Underground',
    'ice_path': 'Ice Path',
    'ilex_forest': 'Ilex Forest',
    'indigo_plateau': 'Indigo Plateau',
    'lake_of_rage': 'Lake of Rage',
    'lavender_radio_tower': 'Lavender Radio Tower',
    'lavender_town': 'Lavender Town',
    'mahogany_town': 'Mahogany Town',
    'mobile_battle_room': 'Mobile Battle Room',
    'mobile_trade_room': 'Mobile Trade Room',
    'mt_moon': 'Mt. Moon',
    'mt_mortar': 'Mt. Mortar',
    'mt_silver': 'Mt. Silver',
    'national_park': 'National Park',
    'new_bark_town': 'New Bark Town',
    'olivine_city': 'Olivine City',
    'olivine_lighthouse': 'Olivine Lighthouse',
    'olivine_port': 'Olivine Port',
    'pallet_town': 'Pallet Town',
    'pewter_city': 'Pewter City',
    'pokecenter_2f': 'Pokémon Center 2F',
    'power_plant': 'Power Plant',
    'radio_tower': 'Radio Tower',
    'rock_tunnel': 'Rock Tunnel',
    'route_1': 'Route 1',
    'route_10': 'Route 10',
    'route_11': 'Route 11',
    'route_12': 'Route 12',
    'route_13': 'Route 13',
    'route_14': 'Route 14',
    'route_15': 'Route 15',
    'route_16': 'Route 16',
    'route_17': 'Route 17',
    'route_18': 'Route 18',
    'route_19': 'Route 19',
    'route_2': 'Route 2',
    'route_20': 'Route 20',
    'route_21': 'Route 21',
    'route_22': 'Route 22',
    'route_23': 'Route 23',
    'route_24': 'Route 24',
    'route_25': 'Route 25',
    'route_26': 'Route 26',
    'route_27': 'Route 27',
    'route_28': 'Route 28',
    'route_29': 'Route 29',
    'route_3': 'Route 3',
    'route_30': 'Route 30',
    'route_31': 'Route 31',
    'route_32': 'Route 32',
    'route_33': 'Route 33',
    'route_34': 'Route 34',
    'route_35': 'Route 35',
    'route_36': 'Route 36',
    'route_37': 'Route 37',
    'route_38': 'Route 38',
    'route_39': 'Route 39',
    'route_4': 'Route 4',
    'route_40': 'Route 40',
    'route_41': 'Route 41',
    'route_42': 'Route 42',
    'route_43': 'Route 43',
    'route_44': 'Route 44',
    'route_45': 'Route 45',
    'route_46': 'Route 46',
    'route_5': 'Route 5',
    'route_6': 'Route 6',
    'route_7': 'Route 7',
    'route_8': 'Route 8',
    'route_9': 'Route 9',
    'ruins_of_alph': 'Ruins of Alph',
    'saffron_city': 'Saffron City',
    'seafoam_islands': 'Seafoam Islands',
    'slowpoke_well': 'Slowpoke Well',
    'sprout_tower': 'Sprout Tower',
    'team_rocket_base': 'Team Rocket Base',
    'time_capsule': 'Time Capsule',
    'tin_tower': 'Tin Tower',
    'tohjo_falls': 'Tohjo Falls',
    'trade_center': 'Trade Center',
    'underground_path': 'Underground Path',
    'union_cave': 'Union Cave',
    'vermilion_city': 'Vermilion City',
    'vermilion_port': 'Vermilion Port',
    'victory_road': 'Victory Road',
    'violet_city': 'Violet City',
    'viridian_city': 'Viridian City',
    'whirl_islands': 'Whirl Islands',
}
# --- end of generated Soul Link areas ---


# --- Badges (0.7.5; badges.py) -----------------------------------------------------------------------------------
def champion(cm: "CrystalMemory") -> bool:
    """Entered the (Johto) Hall of Fame in this save: the saved proofs of entered_hall_of_fame()
    (EVENT_BEAT_ELITE_FOUR, wStatusFlags bit 6, wHallOfFameCount) or standing in the HALL_OF_FAME room now."""
    return cm.entered_hall_of_fame() or cm.in_hall_of_fame()


def owner(cm: "CrystalMemory") -> dict:
    return {"ot": cm.player_name(), "tid": cm.player_id()}


def progress(cm: "CrystalMemory"):
    """Badge progress of this Crystal save (badges.py): champion, Johto dex caught (#1-251 minus Mew and Celebi)
    and the player's own shiny catches (own OT name + ID, Crystal caught data) in the party, the 14 boxes (only the
    current one before the first save) and the Day Care, keyed like the PC database (mon_key). None while not in
    game or while the party is half-written."""
    from . import badges
    if not cm.in_game():
        return None
    party = cm.party_checked()
    if party is None:
        return None
    me = owner(cm)
    keys = [badges.gen2_shiny_key(m.data, m.ot, me, m.egg) for m in party]
    recs = [r for box in Gen2PC(cm.m, cm.rom).read_boxes() for r in box] + cm.daycare_records()
    keys += [badges.gen2_shiny_key(r[:BOX_MON_SIZE], r[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE], me,
                                   len(r) > REC_SIZE and bool(r[REC_SIZE])) for r in recs]
    return badges.progress_dict("johto", champion(cm), cm.read(DEX_CAUGHT, 32), [k for k in keys if k], me)
