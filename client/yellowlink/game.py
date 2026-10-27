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
ROM_BASE_STATS = 0x383DE     # 28 bytes/entry, index dexno-1 (0E:43DE)
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

    def species_for_dex(self, dex: int) -> int:
        """Yellow's internal species index for a national dex number (0 if none)."""
        if not 1 <= dex <= 151:
            return 0
        table = self.rom[ROM_DEX_ORDER:ROM_DEX_ORDER + 190]
        try:
            return table.index(dex) + 1
        except ValueError:
            return 0

    def growth_rate(self, species: int) -> int:
        """GROWTH_* index (byte 19 of the base stats entry), 0 if unknown."""
        dex = self.dex_no(species)
        return self.rom[ROM_BASE_STATS + (dex - 1) * 28 + 19] if 1 <= dex <= 151 else 0

    def catch_rate(self, species: int) -> int:
        dex = self.dex_no(species)
        return self.rom[ROM_BASE_STATS + (dex - 1) * 28 + 8] if 1 <= dex <= 151 else 0

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

    def map_id(self) -> int:
        return self.m[CUR_MAP]

    def walking(self) -> bool:
        return self.m[WALK_COUNTER] != 0

    def player_img(self) -> int:
        return self.m[PLAYER_IMG]

    def status_line(self) -> str:
        m = self.m
        return (f"in_game={self.in_game()} map={m[CUR_MAP]} xy=({m[X_COORD]},{m[Y_COORD]}) "
                f"battle={m[IS_IN_BATTLE]} font={m[FONT_LOADED]} joyignore={m[JOY_IGNORE]} "
                f"walk={m[WALK_COUNTER]} opp={m[CUR_OPPONENT]} party={m[PARTY_COUNT]}")

    def remove_party_slot(self, slot: int):
        remove_party_slot(self, slot)

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

    # -- Soul Link helpers (see dev/notes/soullink_yellow.md for the verification) --
    def bag_items(self) -> list:
        """[(item_id, quantity), ...] from wNumBagItems/wBagItems ([] if the list looks invalid)."""
        n = self.m[NUM_BAG_ITEMS]
        if n > BAG_CAPACITY:
            return []
        return [(self.m[BAG_ITEMS + 2 * i], self.m[BAG_ITEMS + 2 * i + 1]) for i in range(n)]

    def has_item(self, item: int) -> bool:
        return any(i == item and q > 0 for i, q in self.bag_items())

    def has_poke_balls(self) -> bool:
        """True if the bag holds a Poké/Great/Ultra/Master/Safari Ball. Inside the Safari Zone the game
        counts Safari Balls in wNumSafariBalls instead of the bag, so those count there too."""
        if any(i in BALL_ITEMS and q > 0 for i, q in self.bag_items()):
            return True
        return SAFARI_ZONE_EAST <= self.m[CUR_MAP] <= SAFARI_ZONE_CENTER and self.m[NUM_SAFARI_BALLS] > 0

    def _ghost_battle(self) -> bool:
        """pret IsGhostBattle: wild battle in Pokémon Tower 1F-7F without the Silph Scope."""
        return (self.m[IS_IN_BATTLE] == 1 and POKEMON_TOWER_1F <= self.m[CUR_MAP] <= POKEMON_TOWER_7F
                and not self.has_item(SILPH_SCOPE))

    def battle_info(self):
        """None outside a battle, else {"kind", "dex", "shiny", "safari"}. kind/dex/safari are reliable from the
        first frame wIsInBattle is non-zero (written before that byte); "shiny" (Gen-2 rule on the wild mon's DVs,
        YellowLink counts such Yellow mons as shiny; False for trainer/special battles) from the next frame on."""
        state = self.m[IS_IN_BATTLE]
        if state not in (1, 2):                 # 0 = none, 0xFF = lost/whiteout (not a battle)
            return None
        btype = self.m[BATTLE_TYPE]
        if state == 2:
            pos = self.m[ENEMY_MON_PARTY_POS]
            n = self.m[ENEMY_PARTY_COUNT]
            species = self.m[ENEMY_PARTY_SPECIES + (pos if pos < min(n, 6) else 0)]
            kind = "trainer"
        else:
            species = self.m[ENEMY_MON_SPECIES2]
            special = (btype in (BATTLE_TYPE_OLD_MAN, BATTLE_TYPE_RUN, BATTLE_TYPE_PIKACHU) or self._ghost_battle()
                       or (self.m[CUR_MAP] == POKEMON_TOWER_6F and species == RESTLESS_SOUL))
            kind = "special" if special else "wild"
        shiny = kind == "wild" and _gen2_shiny(self.wild_dvs())
        return {"kind": kind, "dex": self.rom.dex_no(species), "shiny": shiny,
                "safari": state == 1 and btype == BATTLE_TYPE_SAFARI}

    def wild_dvs(self) -> bytes:
        """The wild mon's own DVs: wEnemyMonDVs, or wTransformedEnemyMonOriginalDVs once it used Transform
        (Transform copies the target's DVs; a catch restores the original ones - pret LoadEnemyMonData). Written by
        LoadEnemyMonData right after InitWildBattle sets wIsInBattle: on that very frame they can still be the
        previous battle's (ChallengeView waits one frame for Yellow)."""
        if self.m[ENEMY_BATTLE_STATUS3] & (1 << TRANSFORMED_BIT):
            return self.read(TRANSFORMED_ENEMY_ORIG_DVS, 2)
        return self.read(ENEMY_MON_DVS, 2)

    def dex_owned(self, dex: int) -> bool:
        """wPokedexOwned bit of a national dex number (1-151)."""
        if not 1 <= dex <= 151:
            return False
        return bool(self.m[DEX_OWNED + (dex - 1) // 8] & (1 << ((dex - 1) % 8)))

    def enemy_hud_shown(self) -> bool:
        """The enemy's battle HUD is on screen right now (shiny alerts' caught ball, SPEC.md section 10): wTileMap
        row 0 holds wEnemyMonNick from column 1, 2 or 3 (pret DrawEnemyHUDAndHPBar + CenterMonName) and tile (1, 3)
        is the HUD's lower-left corner. False during the intro, under full-screen menus, on the Pokédex page after
        a catch and while the game has cleared the HUD."""
        if self.m[IS_IN_BATTLE] not in (1, 2) or self.m[TILEMAP + 3 * 20 + 1] != HUD_CORNER_TILE:
            return False
        nick = self.read(ENEMY_MON_NICK, NAME_SIZE).split(bytes([text.TERMINATOR]))[0]
        if not nick:
            return False
        row = self.read(TILEMAP, 20)
        return any(row[c:c + len(nick)] == nick for c in (1, 2, 3))

    def battle_party_slot(self):
        """(party index of the player's active mon, its in-battle HP), or None outside battle / before the
        first mon is sent out / in battles where the player has no mon out (Old Man, Safari, Oak's Pikachu)."""
        if self.m[IS_IN_BATTLE] not in (1, 2) or self.m[BATTLE_TYPE] != BATTLE_TYPE_NORMAL:
            return None
        idx = self.m[PLAYER_MON_NUMBER]
        if self.m[BATTLE_MON_SPECIES] == 0 or idx >= min(self.m[PARTY_COUNT], 6):
            return None
        return idx, (self.m[BATTLE_MON_HP] << 8) | self.m[BATTLE_MON_HP + 1]

    def in_hall_of_fame(self) -> bool:
        return self.in_game() and self.m[CUR_MAP] == HALL_OF_FAME_MAP

    def hall_of_fame_count(self) -> int:
        """wNumHoFTeams: Hall of Fame entries in this save (0 = never entered; saturates at 255)."""
        return self.m[NUM_HOF_TEAMS]

    # -- current box (WRAM copy at wBoxCount; the game writes it to SRAM on save / box change) --
    def current_box_records(self) -> list:
        """Current box as 55-byte records (33-byte box struct + OT + nickname), like pcdb."""
        n = self.m[BOX_COUNT]
        if n > MONS_PER_BOX:
            return []
        return [self.read(BOX_MONS + i * BOX_MON_SIZE, BOX_MON_SIZE) + self.read(BOX_OT + i * NAME_SIZE, NAME_SIZE)
                + self.read(BOX_NICKS + i * NAME_SIZE, NAME_SIZE) for i in range(n)]

    def daycare_records(self) -> list:
        """The Route 5 Day Care mon as [55-byte record] (box struct + OT + nickname), [] if empty."""
        if self.m[DAYCARE_IN_USE] == 0 or self.m[DAYCARE_MON] == 0:
            return []
        return [self.read(DAYCARE_MON, BOX_MON_SIZE) + self.read(DAYCARE_MON_OT, NAME_SIZE)
                + self.read(DAYCARE_MON_NAME, NAME_SIZE)]

    def is_starter_pikachu(self, data: bytes, ot: bytes) -> bool:
        """pret IsThisMonStarterPikachu: species PIKACHU, OT ID == player ID, first 5 OT chars == player name."""
        return (data[0] == STARTER_PIKACHU and data[12] == self.m[PLAYER_ID] and data[13] == self.m[PLAYER_ID + 1]
                and bytes(ot[:5]) == self.read(PLAYER_NAME, 5))

    def _pikachu_deposited(self):
        """ModifyPikachuHappiness(PIKAHAPPY_DEPOSITED), as Bill's PC does when the starter Pikachu is deposited."""
        h = self.m[PIKACHU_HAPPINESS]
        delta = (-3, -3, -5)[0 if h < 100 else 1 if h < 200 else 2]
        self.m[PIKACHU_HAPPINESS] = max(0, h + delta)
        if self.m[PIKACHU_MOOD] >= PIKAMOOD_DEPOSITED:
            self.m[PIKACHU_MOOD] = PIKAMOOD_DEPOSITED

    def _remove_box_slot(self, i: int):
        """pret _RemovePokemon with wRemoveMonFromBox=1: shift species, structs, OTs and nicknames up."""
        n = self.m[BOX_COUNT]
        recs = self.current_box_records()
        del recs[i]
        self.m[BOX_COUNT] = n - 1
        for k in range(i, MONS_PER_BOX + 1):
            nxt = self.m[BOX_SPECIES + k + 1] if k < MONS_PER_BOX else 0xFF
            self.m[BOX_SPECIES + k] = nxt
            if nxt == 0xFF:
                break
        for k in range(i, len(recs)):
            self._write_box_slot(k, recs[k])

    def _write_box_slot(self, k: int, rec: bytes):
        self.write(BOX_MONS + k * BOX_MON_SIZE, rec[:BOX_MON_SIZE])
        self.write(BOX_OT + k * NAME_SIZE, rec[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE])
        self.write(BOX_NICKS + k * NAME_SIZE, rec[BOX_MON_SIZE + NAME_SIZE:BOX_MON_SIZE + 2 * NAME_SIZE])

    def move_party_to_box(self, slot: int) -> bool:
        """Deposit party `slot` into the current box like Bill's PC (MoveMon PARTY_TO_BOX + RemovePokemon).
        False if in battle, the slot is invalid, it is the last party mon, or the box is full."""
        n, bc = self.m[PARTY_COUNT], self.m[BOX_COUNT]
        if self.in_battle() or n > 6 or not 0 <= slot < n or n <= 1 or bc >= MONS_PER_BOX:
            return False
        mon = self.party()[slot]
        if self.is_starter_pikachu(mon.data, mon.ot):
            self._pikachu_deposited()
        rec = bytearray(mon.data[:BOX_MON_SIZE])
        rec[3] = mon.data[33]                      # box level byte = party level (MoveMon does this)
        self.m[BOX_COUNT] = bc + 1
        self.m[BOX_SPECIES + bc] = mon.species
        self.m[BOX_SPECIES + bc + 1] = 0xFF
        self._write_box_slot(bc, bytes(rec) + mon.ot + mon.nick)
        remove_party_slot(self, slot)
        return True

    def move_box_to_party(self, box_index: int) -> bool:
        """Withdraw current-box entry `box_index` into the party like Bill's PC (MoveMon BOX_TO_PARTY:
        level from experience, stats recalculated with stat exp; current HP/status kept)."""
        n, bc = self.m[PARTY_COUNT], self.m[BOX_COUNT]
        if self.in_battle() or n >= 6 or bc > MONS_PER_BOX or not 0 <= box_index < bc:
            return False
        rec = self.current_box_records()[box_index]
        base = self.rom.base_stats(rec[0])
        if base is None:
            return False
        d = bytearray(MON_SIZE)
        d[:BOX_MON_SIZE] = rec[:BOX_MON_SIZE]
        d[33] = level_from_exp(self.rom.growth_rate(rec[0]), (rec[14] << 16) | (rec[15] << 8) | rec[16])
        stats = calc_stats(bytes(d), base)
        for k, v in enumerate(stats):
            d[34 + 2 * k], d[35 + 2 * k] = v >> 8, v & 0xFF
        mon = Mon(bytes(d), rec[BOX_MON_SIZE:BOX_MON_SIZE + NAME_SIZE], rec[BOX_MON_SIZE + NAME_SIZE:])
        self.m[PARTY_COUNT] = n + 1
        self.m[PARTY_SPECIES + n + 1] = 0xFF
        self.write_party_slot(n, mon)
        self._remove_box_slot(box_index)
        return True


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


def remove_party_slot(gm: "GameMemory", slot: int):
    """Remove one Pokémon from the party, shifting the rest up (like the game does)."""
    n = gm.m[PARTY_COUNT]
    if not 0 <= slot < n:
        return
    party = gm.party()
    del party[slot]
    gm.m[PARTY_COUNT] = n - 1
    for i in range(6):
        gm.m[PARTY_SPECIES + i] = party[i].species if i < len(party) else 0xFF
    gm.m[PARTY_SPECIES + 6] = 0xFF
    for i, mon in enumerate(party):
        gm.write_party_slot(i, mon)


def party_records(gm: "GameMemory") -> list:
    """Party as 55-byte box-style records (first 33 struct bytes + OT + nickname) for the PC database."""
    return [m.data[:33] + m.ot + m.nick for m in gm.party()]


# --- Soul Link: battle / bag / box / Hall of Fame / areas ---------------------------------------------------------
# All addresses from pret/pokeyellow pokeyellow.sym, exercised on the real ROM by dev/e2e/soullink_mem_yellow.py
# (findings in dev/notes/soullink_yellow.md).
PLAYER_MON_NUMBER = 0xCC2F    # wPlayerMonNumber: party index of the mon in battle
ENEMY_MON_SPECIES2 = 0xCFD7   # wEnemyMonSpecies2: wild species, written before wIsInBattle
ENEMY_MON_PARTY_POS = 0xCFE7  # wEnemyMonPartyPos: trainer mon in battle (0xFF until the first send-out)
BATTLE_MON_SPECIES = 0xD013   # wBattleMonSpecies: zeroed by InitBattleVariables, set when the mon is sent out
BATTLE_MON_HP = 0xD014        # wBattleMonHP (2 bytes BE)
BATTLE_TYPE = 0xD059          # wBattleType
NUM_BAG_ITEMS = 0xD31C        # wNumBagItems
BAG_ITEMS = 0xD31D            # wBagItems: (id, qty) pairs, 0xFF terminated
PIKACHU_HAPPINESS = 0xD46F    # wPikachuHappiness
PIKACHU_MOOD = 0xD470         # wPikachuMood
NUM_HOF_TEAMS = 0xD5A1        # wNumHoFTeams
NUM_SAFARI_BALLS = 0xDA46     # wNumSafariBalls
DAYCARE_IN_USE = 0xDA47       # wDayCareInUse (0/1)
DAYCARE_MON_NAME = 0xDA48     # wDayCareMonName (11)
DAYCARE_MON_OT = 0xDA53       # wDayCareMonOT (11)
DAYCARE_MON = 0xDA5E          # wDayCareMon (33-byte box struct)
BOX_COUNT = 0xDA7F            # wBoxCount (current box, WRAM copy)
BOX_SPECIES = 0xDA80          # wBoxSpecies
BOX_MONS = 0xDA95             # wBoxMons (20 x 33)
BOX_OT = 0xDD29               # wBoxMonOT (20 x 11)
BOX_NICKS = 0xDE05            # wBoxMonNicks (20 x 11)

BOX_MON_SIZE = 33
MONS_PER_BOX = 20
BAG_CAPACITY = 20

BATTLE_TYPE_NORMAL, BATTLE_TYPE_OLD_MAN, BATTLE_TYPE_SAFARI, BATTLE_TYPE_RUN, BATTLE_TYPE_PIKACHU = range(5)
BALL_ITEMS = (0x01, 0x02, 0x03, 0x04, 0x08)    # MASTER, ULTRA, GREAT, POKE, SAFARI
SILPH_SCOPE = 0x48
STARTER_PIKACHU = 0x54                         # PIKACHU (internal index)
RESTLESS_SOUL = 0x91                           # MAROWAK (internal index)
PIKAMOOD_DEPOSITED = 0x62

POKEMON_TOWER_1F, POKEMON_TOWER_6F, POKEMON_TOWER_7F = 0x8E, 0x93, 0x94
SAFARI_ZONE_EAST, SAFARI_ZONE_CENTER = 0xD9, 0xDC
HALL_OF_FAME_MAP = 0x76                        # HALL_OF_FAME

# GrowthRateTable: a/b*n^3 + c*n^2 + d*n - e  (index = GROWTH_* byte of the base stats)
GROWTH_RATES = ((1, 1, 0, 0, 0), (3, 4, 10, 0, 30), (3, 4, 20, 0, 70), (6, 5, -15, 100, 140),
                (4, 5, 0, 0, 0), (5, 4, 0, 0, 0))


def exp_for_level(growth: int, n: int) -> int:
    """pret CalcExperience, including its 24-bit wrap-around (Medium Slow at level 1 is 'negative')."""
    a, b, c, d, e = GROWTH_RATES[growth if 0 <= growth < len(GROWTH_RATES) else 0]
    mask = 0xFFFFFF
    cubic = ((n * n * n * a) // b) & mask
    val = (n * d - e) & mask
    val = (val + c * n * n) & mask
    return (val + cubic) & mask


def level_from_exp(growth: int, exp: int) -> int:
    """pret CalcLevelFromExperience: highest level whose required exp is <= exp (levels from 2 up)."""
    level = 1
    while level < 255 and exp >= exp_for_level(growth, level + 1):
        level += 1
    return level


def mon_key(record_or_party_data: bytes) -> str:
    """Stable identity for Soul Link: OT ID + DVs + catch-rate byte (all at the same offsets in the 44-byte
    party struct, the 33-byte box struct and 55-byte records). None of these change on evolution, level-up,
    deposit/withdraw or renaming. Two of the player's own mons collide only with equal DVs (16 random bits)
    and equal catch rate of the species they were caught as; see dev/notes/soullink_yellow.md."""
    d = record_or_party_data
    return f"{d[12]:02x}{d[13]:02x}{d[27]:02x}{d[28]:02x}{d[7]:02x}"


# Nuzlocke "areas" (first-encounter rule), generated from pret/pokeyellow constants/map_constants.asm:
# cities/routes are their own area; interiors and gatehouses belong to their town/route (pret's indoor groups);
# all floors of a dungeon share one area. Unused map ids are their own (unreachable) area.
AREAS = {
    0x00: 'PALLET_TOWN',  # PALLET_TOWN
    0x01: 'VIRIDIAN_CITY',  # VIRIDIAN_CITY
    0x02: 'PEWTER_CITY',  # PEWTER_CITY
    0x03: 'CERULEAN_CITY',  # CERULEAN_CITY
    0x04: 'LAVENDER_TOWN',  # LAVENDER_TOWN
    0x05: 'VERMILION_CITY',  # VERMILION_CITY
    0x06: 'CELADON_CITY',  # CELADON_CITY
    0x07: 'FUCHSIA_CITY',  # FUCHSIA_CITY
    0x08: 'CINNABAR_ISLAND',  # CINNABAR_ISLAND
    0x09: 'INDIGO_PLATEAU',  # INDIGO_PLATEAU
    0x0A: 'SAFFRON_CITY',  # SAFFRON_CITY
    0x0B: 'UNUSED_MAP_0B',  # UNUSED_MAP_0B
    0x0C: 'ROUTE_1',  # ROUTE_1
    0x0D: 'ROUTE_2',  # ROUTE_2
    0x0E: 'ROUTE_3',  # ROUTE_3
    0x0F: 'ROUTE_4',  # ROUTE_4
    0x10: 'ROUTE_5',  # ROUTE_5
    0x11: 'ROUTE_6',  # ROUTE_6
    0x12: 'ROUTE_7',  # ROUTE_7
    0x13: 'ROUTE_8',  # ROUTE_8
    0x14: 'ROUTE_9',  # ROUTE_9
    0x15: 'ROUTE_10',  # ROUTE_10
    0x16: 'ROUTE_11',  # ROUTE_11
    0x17: 'ROUTE_12',  # ROUTE_12
    0x18: 'ROUTE_13',  # ROUTE_13
    0x19: 'ROUTE_14',  # ROUTE_14
    0x1A: 'ROUTE_15',  # ROUTE_15
    0x1B: 'ROUTE_16',  # ROUTE_16
    0x1C: 'ROUTE_17',  # ROUTE_17
    0x1D: 'ROUTE_18',  # ROUTE_18
    0x1E: 'ROUTE_19',  # ROUTE_19
    0x1F: 'ROUTE_20',  # ROUTE_20
    0x20: 'ROUTE_21',  # ROUTE_21
    0x21: 'ROUTE_22',  # ROUTE_22
    0x22: 'ROUTE_23',  # ROUTE_23
    0x23: 'ROUTE_24',  # ROUTE_24
    0x24: 'ROUTE_25',  # ROUTE_25
    0x25: 'PALLET_TOWN',  # REDS_HOUSE_1F
    0x26: 'PALLET_TOWN',  # REDS_HOUSE_2F
    0x27: 'PALLET_TOWN',  # BLUES_HOUSE
    0x28: 'PALLET_TOWN',  # OAKS_LAB
    0x29: 'VIRIDIAN_CITY',  # VIRIDIAN_POKECENTER
    0x2A: 'VIRIDIAN_CITY',  # VIRIDIAN_MART
    0x2B: 'VIRIDIAN_CITY',  # VIRIDIAN_SCHOOL_HOUSE
    0x2C: 'VIRIDIAN_CITY',  # VIRIDIAN_NICKNAME_HOUSE
    0x2D: 'VIRIDIAN_CITY',  # VIRIDIAN_GYM
    0x2E: 'ROUTE_2',  # DIGLETTS_CAVE_ROUTE_2
    0x2F: 'ROUTE_2',  # VIRIDIAN_FOREST_NORTH_GATE
    0x30: 'ROUTE_2',  # ROUTE_2_TRADE_HOUSE
    0x31: 'ROUTE_2',  # ROUTE_2_GATE
    0x32: 'ROUTE_2',  # VIRIDIAN_FOREST_SOUTH_GATE
    0x33: 'VIRIDIAN_FOREST',  # VIRIDIAN_FOREST
    0x34: 'PEWTER_CITY',  # MUSEUM_1F
    0x35: 'PEWTER_CITY',  # MUSEUM_2F
    0x36: 'PEWTER_CITY',  # PEWTER_GYM
    0x37: 'PEWTER_CITY',  # PEWTER_NIDORAN_HOUSE
    0x38: 'PEWTER_CITY',  # PEWTER_MART
    0x39: 'PEWTER_CITY',  # PEWTER_SPEECH_HOUSE
    0x3A: 'PEWTER_CITY',  # PEWTER_POKECENTER
    0x3B: 'MT_MOON',  # MT_MOON_1F
    0x3C: 'MT_MOON',  # MT_MOON_B1F
    0x3D: 'MT_MOON',  # MT_MOON_B2F
    0x3E: 'CERULEAN_CITY',  # CERULEAN_TRASHED_HOUSE
    0x3F: 'CERULEAN_CITY',  # CERULEAN_MELANIES_HOUSE
    0x40: 'CERULEAN_CITY',  # CERULEAN_POKECENTER
    0x41: 'CERULEAN_CITY',  # CERULEAN_GYM
    0x42: 'CERULEAN_CITY',  # BIKE_SHOP
    0x43: 'CERULEAN_CITY',  # CERULEAN_MART
    0x44: 'ROUTE_4',  # MT_MOON_POKECENTER
    0x45: 'CERULEAN_CITY',  # CERULEAN_TRASHED_HOUSE_COPY
    0x46: 'ROUTE_5',  # ROUTE_5_GATE
    0x47: 'ROUTE_5',  # UNDERGROUND_PATH_ROUTE_5
    0x48: 'ROUTE_5',  # DAYCARE
    0x49: 'ROUTE_6',  # ROUTE_6_GATE
    0x4A: 'ROUTE_6',  # UNDERGROUND_PATH_ROUTE_6
    0x4B: 'ROUTE_6',  # UNDERGROUND_PATH_ROUTE_6_COPY
    0x4C: 'ROUTE_7',  # ROUTE_7_GATE
    0x4D: 'ROUTE_7',  # UNDERGROUND_PATH_ROUTE_7
    0x4E: 'ROUTE_7',  # UNDERGROUND_PATH_ROUTE_7_COPY
    0x4F: 'ROUTE_8',  # ROUTE_8_GATE
    0x50: 'ROUTE_8',  # UNDERGROUND_PATH_ROUTE_8
    0x51: 'ROUTE_10',  # ROCK_TUNNEL_POKECENTER
    0x52: 'ROCK_TUNNEL',  # ROCK_TUNNEL_1F
    0x53: 'POWER_PLANT',  # POWER_PLANT
    0x54: 'ROUTE_11',  # ROUTE_11_GATE_1F
    0x55: 'ROUTE_11',  # DIGLETTS_CAVE_ROUTE_11
    0x56: 'ROUTE_11',  # ROUTE_11_GATE_2F
    0x57: 'ROUTE_12',  # ROUTE_12_GATE_1F
    0x58: 'ROUTE_25',  # BILLS_HOUSE
    0x59: 'VERMILION_CITY',  # VERMILION_POKECENTER
    0x5A: 'VERMILION_CITY',  # POKEMON_FAN_CLUB
    0x5B: 'VERMILION_CITY',  # VERMILION_MART
    0x5C: 'VERMILION_CITY',  # VERMILION_GYM
    0x5D: 'VERMILION_CITY',  # VERMILION_PIDGEY_HOUSE
    0x5E: 'VERMILION_CITY',  # VERMILION_DOCK
    0x5F: 'SS_ANNE',  # SS_ANNE_1F
    0x60: 'SS_ANNE',  # SS_ANNE_2F
    0x61: 'SS_ANNE',  # SS_ANNE_3F
    0x62: 'SS_ANNE',  # SS_ANNE_B1F
    0x63: 'SS_ANNE',  # SS_ANNE_BOW
    0x64: 'SS_ANNE',  # SS_ANNE_KITCHEN
    0x65: 'SS_ANNE',  # SS_ANNE_CAPTAINS_ROOM
    0x66: 'SS_ANNE',  # SS_ANNE_1F_ROOMS
    0x67: 'SS_ANNE',  # SS_ANNE_2F_ROOMS
    0x68: 'SS_ANNE',  # SS_ANNE_B1F_ROOMS
    0x69: 'UNUSED_MAP_69',  # UNUSED_MAP_69
    0x6A: 'UNUSED_MAP_6A',  # UNUSED_MAP_6A
    0x6B: 'UNUSED_MAP_6B',  # UNUSED_MAP_6B
    0x6C: 'VICTORY_ROAD',  # VICTORY_ROAD_1F
    0x6D: 'UNUSED_MAP_6D',  # UNUSED_MAP_6D
    0x6E: 'UNUSED_MAP_6E',  # UNUSED_MAP_6E
    0x6F: 'UNUSED_MAP_6F',  # UNUSED_MAP_6F
    0x70: 'UNUSED_MAP_70',  # UNUSED_MAP_70
    0x71: 'INDIGO_PLATEAU',  # LANCES_ROOM
    0x72: 'UNUSED_MAP_72',  # UNUSED_MAP_72
    0x73: 'UNUSED_MAP_73',  # UNUSED_MAP_73
    0x74: 'UNUSED_MAP_74',  # UNUSED_MAP_74
    0x75: 'UNUSED_MAP_75',  # UNUSED_MAP_75
    0x76: 'INDIGO_PLATEAU',  # HALL_OF_FAME
    0x77: 'UNDERGROUND_PATH_NORTH_SOUTH',  # UNDERGROUND_PATH_NORTH_SOUTH
    0x78: 'INDIGO_PLATEAU',  # CHAMPIONS_ROOM
    0x79: 'UNDERGROUND_PATH_WEST_EAST',  # UNDERGROUND_PATH_WEST_EAST
    0x7A: 'CELADON_CITY',  # CELADON_MART_1F
    0x7B: 'CELADON_CITY',  # CELADON_MART_2F
    0x7C: 'CELADON_CITY',  # CELADON_MART_3F
    0x7D: 'CELADON_CITY',  # CELADON_MART_4F
    0x7E: 'CELADON_CITY',  # CELADON_MART_ROOF
    0x7F: 'CELADON_CITY',  # CELADON_MART_ELEVATOR
    0x80: 'CELADON_CITY',  # CELADON_MANSION_1F
    0x81: 'CELADON_CITY',  # CELADON_MANSION_2F
    0x82: 'CELADON_CITY',  # CELADON_MANSION_3F
    0x83: 'CELADON_CITY',  # CELADON_MANSION_ROOF
    0x84: 'CELADON_CITY',  # CELADON_MANSION_ROOF_HOUSE
    0x85: 'CELADON_CITY',  # CELADON_POKECENTER
    0x86: 'CELADON_CITY',  # CELADON_GYM
    0x87: 'CELADON_CITY',  # GAME_CORNER
    0x88: 'CELADON_CITY',  # CELADON_MART_5F
    0x89: 'CELADON_CITY',  # GAME_CORNER_PRIZE_ROOM
    0x8A: 'CELADON_CITY',  # CELADON_DINER
    0x8B: 'CELADON_CITY',  # CELADON_CHIEF_HOUSE
    0x8C: 'CELADON_CITY',  # CELADON_HOTEL
    0x8D: 'LAVENDER_TOWN',  # LAVENDER_POKECENTER
    0x8E: 'POKEMON_TOWER',  # POKEMON_TOWER_1F
    0x8F: 'POKEMON_TOWER',  # POKEMON_TOWER_2F
    0x90: 'POKEMON_TOWER',  # POKEMON_TOWER_3F
    0x91: 'POKEMON_TOWER',  # POKEMON_TOWER_4F
    0x92: 'POKEMON_TOWER',  # POKEMON_TOWER_5F
    0x93: 'POKEMON_TOWER',  # POKEMON_TOWER_6F
    0x94: 'POKEMON_TOWER',  # POKEMON_TOWER_7F
    0x95: 'LAVENDER_TOWN',  # MR_FUJIS_HOUSE
    0x96: 'LAVENDER_TOWN',  # LAVENDER_MART
    0x97: 'LAVENDER_TOWN',  # LAVENDER_CUBONE_HOUSE
    0x98: 'FUCHSIA_CITY',  # FUCHSIA_MART
    0x99: 'FUCHSIA_CITY',  # FUCHSIA_BILLS_GRANDPAS_HOUSE
    0x9A: 'FUCHSIA_CITY',  # FUCHSIA_POKECENTER
    0x9B: 'FUCHSIA_CITY',  # WARDENS_HOUSE
    0x9C: 'FUCHSIA_CITY',  # SAFARI_ZONE_GATE
    0x9D: 'FUCHSIA_CITY',  # FUCHSIA_GYM
    0x9E: 'FUCHSIA_CITY',  # FUCHSIA_MEETING_ROOM
    0x9F: 'SEAFOAM_ISLANDS',  # SEAFOAM_ISLANDS_B1F
    0xA0: 'SEAFOAM_ISLANDS',  # SEAFOAM_ISLANDS_B2F
    0xA1: 'SEAFOAM_ISLANDS',  # SEAFOAM_ISLANDS_B3F
    0xA2: 'SEAFOAM_ISLANDS',  # SEAFOAM_ISLANDS_B4F
    0xA3: 'VERMILION_CITY',  # VERMILION_OLD_ROD_HOUSE
    0xA4: 'FUCHSIA_CITY',  # FUCHSIA_GOOD_ROD_HOUSE
    0xA5: 'POKEMON_MANSION',  # POKEMON_MANSION_1F
    0xA6: 'CINNABAR_ISLAND',  # CINNABAR_GYM
    0xA7: 'CINNABAR_ISLAND',  # CINNABAR_LAB
    0xA8: 'CINNABAR_ISLAND',  # CINNABAR_LAB_TRADE_ROOM
    0xA9: 'CINNABAR_ISLAND',  # CINNABAR_LAB_METRONOME_ROOM
    0xAA: 'CINNABAR_ISLAND',  # CINNABAR_LAB_FOSSIL_ROOM
    0xAB: 'CINNABAR_ISLAND',  # CINNABAR_POKECENTER
    0xAC: 'CINNABAR_ISLAND',  # CINNABAR_MART
    0xAD: 'CINNABAR_ISLAND',  # CINNABAR_MART_COPY
    0xAE: 'INDIGO_PLATEAU',  # INDIGO_PLATEAU_LOBBY
    0xAF: 'SAFFRON_CITY',  # COPYCATS_HOUSE_1F
    0xB0: 'SAFFRON_CITY',  # COPYCATS_HOUSE_2F
    0xB1: 'SAFFRON_CITY',  # FIGHTING_DOJO
    0xB2: 'SAFFRON_CITY',  # SAFFRON_GYM
    0xB3: 'SAFFRON_CITY',  # SAFFRON_PIDGEY_HOUSE
    0xB4: 'SAFFRON_CITY',  # SAFFRON_MART
    0xB5: 'SILPH_CO',  # SILPH_CO_1F
    0xB6: 'SAFFRON_CITY',  # SAFFRON_POKECENTER
    0xB7: 'SAFFRON_CITY',  # MR_PSYCHICS_HOUSE
    0xB8: 'ROUTE_15',  # ROUTE_15_GATE_1F
    0xB9: 'ROUTE_15',  # ROUTE_15_GATE_2F
    0xBA: 'ROUTE_16',  # ROUTE_16_GATE_1F
    0xBB: 'ROUTE_16',  # ROUTE_16_GATE_2F
    0xBC: 'ROUTE_16',  # ROUTE_16_FLY_HOUSE
    0xBD: 'ROUTE_12',  # ROUTE_12_SUPER_ROD_HOUSE
    0xBE: 'ROUTE_18',  # ROUTE_18_GATE_1F
    0xBF: 'ROUTE_18',  # ROUTE_18_GATE_2F
    0xC0: 'SEAFOAM_ISLANDS',  # SEAFOAM_ISLANDS_1F
    0xC1: 'ROUTE_22',  # ROUTE_22_GATE
    0xC2: 'VICTORY_ROAD',  # VICTORY_ROAD_2F
    0xC3: 'ROUTE_12',  # ROUTE_12_GATE_2F
    0xC4: 'VERMILION_CITY',  # VERMILION_TRADE_HOUSE
    0xC5: 'DIGLETTS_CAVE',  # DIGLETTS_CAVE
    0xC6: 'VICTORY_ROAD',  # VICTORY_ROAD_3F
    0xC7: 'ROCKET_HIDEOUT',  # ROCKET_HIDEOUT_B1F
    0xC8: 'ROCKET_HIDEOUT',  # ROCKET_HIDEOUT_B2F
    0xC9: 'ROCKET_HIDEOUT',  # ROCKET_HIDEOUT_B3F
    0xCA: 'ROCKET_HIDEOUT',  # ROCKET_HIDEOUT_B4F
    0xCB: 'ROCKET_HIDEOUT',  # ROCKET_HIDEOUT_ELEVATOR
    0xCC: 'UNUSED_MAP_CC',  # UNUSED_MAP_CC
    0xCD: 'UNUSED_MAP_CD',  # UNUSED_MAP_CD
    0xCE: 'UNUSED_MAP_CE',  # UNUSED_MAP_CE
    0xCF: 'SILPH_CO',  # SILPH_CO_2F
    0xD0: 'SILPH_CO',  # SILPH_CO_3F
    0xD1: 'SILPH_CO',  # SILPH_CO_4F
    0xD2: 'SILPH_CO',  # SILPH_CO_5F
    0xD3: 'SILPH_CO',  # SILPH_CO_6F
    0xD4: 'SILPH_CO',  # SILPH_CO_7F
    0xD5: 'SILPH_CO',  # SILPH_CO_8F
    0xD6: 'POKEMON_MANSION',  # POKEMON_MANSION_2F
    0xD7: 'POKEMON_MANSION',  # POKEMON_MANSION_3F
    0xD8: 'POKEMON_MANSION',  # POKEMON_MANSION_B1F
    0xD9: 'SAFARI_ZONE',  # SAFARI_ZONE_EAST
    0xDA: 'SAFARI_ZONE',  # SAFARI_ZONE_NORTH
    0xDB: 'SAFARI_ZONE',  # SAFARI_ZONE_WEST
    0xDC: 'SAFARI_ZONE',  # SAFARI_ZONE_CENTER
    0xDD: 'SAFARI_ZONE',  # SAFARI_ZONE_CENTER_REST_HOUSE
    0xDE: 'SAFARI_ZONE',  # SAFARI_ZONE_SECRET_HOUSE
    0xDF: 'SAFARI_ZONE',  # SAFARI_ZONE_WEST_REST_HOUSE
    0xE0: 'SAFARI_ZONE',  # SAFARI_ZONE_EAST_REST_HOUSE
    0xE1: 'SAFARI_ZONE',  # SAFARI_ZONE_NORTH_REST_HOUSE
    0xE2: 'CERULEAN_CAVE',  # CERULEAN_CAVE_2F
    0xE3: 'CERULEAN_CAVE',  # CERULEAN_CAVE_B1F
    0xE4: 'CERULEAN_CAVE',  # CERULEAN_CAVE_1F
    0xE5: 'LAVENDER_TOWN',  # NAME_RATERS_HOUSE
    0xE6: 'CERULEAN_CITY',  # CERULEAN_BADGE_HOUSE
    0xE7: 'UNUSED_MAP_E7',  # UNUSED_MAP_E7
    0xE8: 'ROCK_TUNNEL',  # ROCK_TUNNEL_B1F
    0xE9: 'SILPH_CO',  # SILPH_CO_9F
    0xEA: 'SILPH_CO',  # SILPH_CO_10F
    0xEB: 'SILPH_CO',  # SILPH_CO_11F
    0xEC: 'SILPH_CO',  # SILPH_CO_ELEVATOR
    0xED: 'UNUSED_MAP_ED',  # UNUSED_MAP_ED
    0xEE: 'UNUSED_MAP_EE',  # UNUSED_MAP_EE
    0xEF: 'TRADE_CENTER',  # TRADE_CENTER
    0xF0: 'COLOSSEUM',  # COLOSSEUM
    0xF1: 'UNUSED_MAP_F1',  # UNUSED_MAP_F1
    0xF2: 'UNUSED_MAP_F2',  # UNUSED_MAP_F2
    0xF3: 'UNUSED_MAP_F3',  # UNUSED_MAP_F3
    0xF4: 'UNUSED_MAP_F4',  # UNUSED_MAP_F4
    0xF5: 'INDIGO_PLATEAU',  # LORELEIS_ROOM
    0xF6: 'INDIGO_PLATEAU',  # BRUNOS_ROOM
    0xF7: 'INDIGO_PLATEAU',  # AGATHAS_ROOM
    0xF8: 'ROUTE_19',  # SUMMER_BEACH_HOUSE
}
AREA_NAMES = {
    'PALLET_TOWN': 'Pallet Town',
    'VIRIDIAN_CITY': 'Viridian City',
    'PEWTER_CITY': 'Pewter City',
    'CERULEAN_CITY': 'Cerulean City',
    'LAVENDER_TOWN': 'Lavender Town',
    'VERMILION_CITY': 'Vermilion City',
    'CELADON_CITY': 'Celadon City',
    'FUCHSIA_CITY': 'Fuchsia City',
    'CINNABAR_ISLAND': 'Cinnabar Island',
    'INDIGO_PLATEAU': 'Indigo Plateau',
    'SAFFRON_CITY': 'Saffron City',
    'UNUSED_MAP_0B': 'Unused map $0B',
    'ROUTE_1': 'Route 1',
    'ROUTE_2': 'Route 2',
    'ROUTE_3': 'Route 3',
    'ROUTE_4': 'Route 4',
    'ROUTE_5': 'Route 5',
    'ROUTE_6': 'Route 6',
    'ROUTE_7': 'Route 7',
    'ROUTE_8': 'Route 8',
    'ROUTE_9': 'Route 9',
    'ROUTE_10': 'Route 10',
    'ROUTE_11': 'Route 11',
    'ROUTE_12': 'Route 12',
    'ROUTE_13': 'Route 13',
    'ROUTE_14': 'Route 14',
    'ROUTE_15': 'Route 15',
    'ROUTE_16': 'Route 16',
    'ROUTE_17': 'Route 17',
    'ROUTE_18': 'Route 18',
    'ROUTE_19': 'Route 19',
    'ROUTE_20': 'Route 20',
    'ROUTE_21': 'Route 21',
    'ROUTE_22': 'Route 22',
    'ROUTE_23': 'Route 23',
    'ROUTE_24': 'Route 24',
    'ROUTE_25': 'Route 25',
    'VIRIDIAN_FOREST': 'Viridian Forest',
    'MT_MOON': 'Mt. Moon',
    'ROCK_TUNNEL': 'Rock Tunnel',
    'POWER_PLANT': 'Power Plant',
    'SS_ANNE': 'S.S. Anne',
    'UNUSED_MAP_69': 'Unused map $69',
    'UNUSED_MAP_6A': 'Unused map $6A',
    'UNUSED_MAP_6B': 'Unused map $6B',
    'VICTORY_ROAD': 'Victory Road',
    'UNUSED_MAP_6D': 'Unused map $6D',
    'UNUSED_MAP_6E': 'Unused map $6E',
    'UNUSED_MAP_6F': 'Unused map $6F',
    'UNUSED_MAP_70': 'Unused map $70',
    'UNUSED_MAP_72': 'Unused map $72',
    'UNUSED_MAP_73': 'Unused map $73',
    'UNUSED_MAP_74': 'Unused map $74',
    'UNUSED_MAP_75': 'Unused map $75',
    'UNDERGROUND_PATH_NORTH_SOUTH': 'Underground Path (N-S)',
    'UNDERGROUND_PATH_WEST_EAST': 'Underground Path (W-E)',
    'POKEMON_TOWER': 'Pokémon Tower',
    'SEAFOAM_ISLANDS': 'Seafoam Islands',
    'POKEMON_MANSION': 'Pokémon Mansion',
    'SILPH_CO': 'Silph Co.',
    'DIGLETTS_CAVE': "Diglett's Cave",
    'ROCKET_HIDEOUT': 'Rocket Hideout',
    'UNUSED_MAP_CC': 'Unused map $CC',
    'UNUSED_MAP_CD': 'Unused map $CD',
    'UNUSED_MAP_CE': 'Unused map $CE',
    'SAFARI_ZONE': 'Safari Zone',
    'CERULEAN_CAVE': 'Cerulean Cave',
    'UNUSED_MAP_E7': 'Unused map $E7',
    'UNUSED_MAP_ED': 'Unused map $ED',
    'UNUSED_MAP_EE': 'Unused map $EE',
    'TRADE_CENTER': 'Trade Center',
    'COLOSSEUM': 'Colosseum',
    'UNUSED_MAP_F1': 'Unused map $F1',
    'UNUSED_MAP_F2': 'Unused map $F2',
    'UNUSED_MAP_F3': 'Unused map $F3',
    'UNUSED_MAP_F4': 'Unused map $F4',
}


def area_of(map_id: int) -> str:
    return AREAS.get(map_id, f"MAP_{map_id & 0xFF:02X}")


def area_name(key: str) -> str:
    if key in AREA_NAMES:
        return AREA_NAMES[key]
    if key.startswith("UNUSED_MAP_"):
        return "Unused map $" + key[11:]
    return key.replace("_", " ").title()


# --- Exp Share All (modern rule) ---------------------------------------------------------------------------------
def install_exp_share(pyboy, enabled):
    """Install the Exp Share All hooks (Gen 6+ rule: participants 100 % each, other able party mons 50 %) on this
    emulator; `enabled()` is asked at every experience award (False -> the original game for that award).
    Returns a handle with `.remove()`. Hooks 0F:461C, 0F:4637 (FaintEnemyPokemon), 15:525F, 15:547B, 15:52EF,
    15:545F (GainExperience / DivideExpDataByNumMonsGainingExp) -- details in expshare_gb.py. Only for the verified
    Yellow ROM (the code bytes are checked first; RuntimeError otherwise)."""
    from .expshare_gb import YellowExpShare
    return YellowExpShare(pyboy, enabled)


# --- Badges (0.7.5; badges.py) -----------------------------------------------------------------------------------
# pret/pokeyellow symbols, checked on the ROM by dev/badges/verify.py (SPEC.md section 3, "Badges").
ENEMY_MON_DVS = 0xCFF0                # wEnemyMonDVs (2 bytes): LoadEnemyMonData writes random DVs for a wild mon
TRANSFORMED_ENEMY_ORIG_DVS = 0xCCEB   # wTransformedEnemyMonOriginalDVs: the wild mon's own DVs once it used Transform
ENEMY_BATTLE_STATUS3 = 0xD068         # wEnemyBattleStatus3: bit 3 TRANSFORMED
# Shiny alerts (0.7.7, SPEC.md section 10): the caught ball next to the wild mon's name
ENEMY_MON_NICK = 0xCFD9               # wEnemyMonNick (11 bytes, 0x50 terminated): the name the enemy HUD shows
HUD_CORNER_TILE = 0x74                # EnemyBattleHUDGraphicsTiles: lower-left corner, placed at tile (1, 3)
TRANSFORMED_BIT = 3


def _gen2_shiny(dvs) -> bool:
    from .badges import gen1_is_shiny
    return gen1_is_shiny(dvs)


def champion(gm: "GameMemory") -> bool:
    """Entered the Hall of Fame in this save: wNumHoFTeams > 0 (AnimateHallOfFame increments it, saved with the
    game), or standing in the HALL_OF_FAME room right now (only reachable after beating the Champion)."""
    return gm.hall_of_fame_count() > 0 or gm.in_hall_of_fame()


def owner(gm: "GameMemory") -> dict:
    """The player's catches are the ones with this OT name + OT ID."""
    return {"ot": gm.player_name(), "tid": (gm.m[PLAYER_ID] << 8) | gm.m[PLAYER_ID + 1]}


def progress(gm: "GameMemory"):
    """Badge progress of this Yellow save (badges.py): champion, Kanto dex owned (#1-150) and the player's own
    shiny catches (Gen-2 DV rule) in the party, all 12 boxes and the Day Care, as badge keys
    (badges.gen1_badge_key, numbered). None while not in game or while the party is half-written."""
    from . import badges
    from .pcdb import Gen1PC, G1_STRUCT, G1_NAME
    if not gm.in_game():
        return None
    party = gm.party_checked()
    if party is None:
        return None
    me = owner(gm)
    dex = gm.rom.dex_no
    keys = [badges.gen1_shiny_key(m.data, m.ot, me, dex(m.species)) for m in party]
    pc = Gen1PC(gm.m, gm.rom)
    recs = [r for box in pc.read_boxes() for r in box] + gm.daycare_records()
    keys += [badges.gen1_shiny_key(r[:G1_STRUCT], r[G1_STRUCT:G1_STRUCT + G1_NAME], me, dex(r[0])) for r in recs]
    return badges.progress_dict("kanto", champion(gm), gm.read(DEX_OWNED, 19),
                                badges.number_keys([k for k in keys if k]), me)


def wild_is_shiny(gm: "GameMemory"):
    """In a catchable wild battle (wIsInBattle 1, normal or Safari - not the Old Man / Oak's Pikachu / ghost
    battles): would the enemy be shiny in Gen 2? None outside such a battle. Reads wEnemyMonDVs, or
    wTransformedEnemyMonOriginalDVs once the wild mon has used Transform (Transform copies the target's DVs;
    catching it restores the original ones - pret LoadEnemyMonData). LoadEnemyMonData runs right after
    InitWildBattle sets wIsInBattle, so the very first battle frame can still show the previous DVs."""
    info = gm.battle_info()
    if info is None or info["kind"] != "wild":
        return None
    return info["shiny"]
