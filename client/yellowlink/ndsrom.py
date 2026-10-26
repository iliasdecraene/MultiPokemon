"""Read files from the player's own Nintendo DS ROM (.nds) at runtime: NitroFS (FNT/FAT), NARC archives,
LZ77 (type 0x10) decompression, Gen-4 message banks, and the Pokémon Platinum data the PK4 conversions need.

Nothing from the ROM is ever stored in the repo; everything is read from the player's file when first needed
and cached in memory (a few dozen KB). Paths and layouts come from pret/pokeplatinum
(platinum.us/filesys.csv, include/struct_defs/species.h, include/move_table.h, src/message.c) and were
checked against the real Platinum (US) ROM.

Usage:
    rom = ndsrom.platinum("path/to/platinum.nds")    # cached per path; cheap after the first call
    rom.growth_rate(25); rom.exp_for_level(25, 50); rom.level_for_exp(25, 117360); rom.species_name(25)
"""
from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass

# ---------------------------------------------------------------------------------------------------------
# NitroFS
# ---------------------------------------------------------------------------------------------------------


class NDSError(Exception):
    pass


class NDSRom:
    """Random access to the files of a .nds image. Only the header, FNT and FAT are read up front."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._f = open(path, "rb")
        hdr = self._read(0, 0x180)
        if len(hdr) < 0x180:
            raise NDSError("not a DS ROM (too small)")
        self.title = hdr[0:12].rstrip(b"\0").decode("ascii", "replace")
        self.game_code = hdr[12:16].decode("ascii", "replace")
        self.version = hdr[0x1E]
        fnt_off, fnt_size, fat_off, fat_size = struct.unpack_from("<4I", hdr, 0x40)
        size = os.fstat(self._f.fileno()).st_size
        if not (0 < fnt_off < size and 0 < fat_off < size and fat_size % 8 == 0):
            raise NDSError("not a DS ROM (bad FNT/FAT)")
        self._fat = struct.unpack("<%dI" % (fat_size // 4), self._read(fat_off, fat_size))
        self._paths = self._parse_fnt(self._read(fnt_off, fnt_size))

    def close(self):
        try:
            self._f.close()
        except OSError:
            pass

    def _read(self, off: int, n: int) -> bytes:
        with self._lock:
            self._f.seek(off)
            return self._f.read(n)

    @staticmethod
    def _parse_fnt(fnt: bytes) -> dict:
        """{'/dir/file': file id} for every file."""
        n_dirs = struct.unpack_from("<H", fnt, 6)[0]
        if not 0 < n_dirs <= 4096:
            raise NDSError("bad FNT")
        out = {}

        def walk(dir_id: int, prefix: str, depth: int):
            if depth > 32:
                return
            sub, first = struct.unpack_from("<IH", fnt, (dir_id & 0xFFF) * 8)
            o, fid = sub, first
            while o < len(fnt):
                t = fnt[o]
                o += 1
                if t == 0:
                    break
                n = t & 0x7F
                name = fnt[o:o + n].decode("latin-1")
                o += n
                if t & 0x80:
                    child = struct.unpack_from("<H", fnt, o)[0]
                    o += 2
                    walk(child, prefix + name + "/", depth + 1)
                else:
                    out[prefix + name] = fid
                    fid += 1

        walk(0xF000, "/", 0)
        return out

    def files(self) -> list:
        return sorted(self._paths)

    def file_id(self, path: str) -> int:
        if not path.startswith("/"):
            path = "/" + path
        try:
            return self._paths[path]
        except KeyError:
            raise NDSError("file not in ROM: " + path) from None

    def extent(self, path: str):
        """(start offset, size) of a file in the ROM image."""
        fid = self.file_id(path)
        start, end = self._fat[2 * fid], self._fat[2 * fid + 1]
        if end < start:
            raise NDSError("bad FAT entry for " + path)
        return start, end - start

    def file(self, path: str) -> bytes:
        start, size = self.extent(path)
        return self._read(start, size)

    def narc(self, path: str) -> "Narc":
        start, size = self.extent(path)
        return Narc(self._read, start, size)


class Narc:
    """A NARC archive inside the ROM (members are read on demand)."""

    def __init__(self, read, base: int, size: int):
        self._read = read
        head = read(base, 16)
        if head[:4] != b"NARC":
            raise NDSError("not a NARC")
        hdr_size = struct.unpack_from("<H", head, 12)[0]
        o = base + hdr_size
        btaf = read(o, 12)
        if btaf[:4] != b"BTAF":
            raise NDSError("NARC without BTAF")
        btaf_size, count = struct.unpack_from("<IH", btaf, 4)
        self._fat = struct.unpack("<%dI" % (2 * count), read(o + 12, 8 * count))
        o += btaf_size
        btnf = read(o, 8)
        if btnf[:4] != b"BTNF":
            raise NDSError("NARC without BTNF")
        o += struct.unpack_from("<I", btnf, 4)[0]
        gmif = read(o, 8)
        if gmif[:4] != b"GMIF":
            raise NDSError("NARC without GMIF")
        self._data = o + 8
        self._end = base + size
        self.count = count

    def __len__(self):
        return self.count

    def size(self, i: int) -> int:
        return self._fat[2 * i + 1] - self._fat[2 * i]

    def member(self, i: int, offset: int = 0, n: int | None = None) -> bytes:
        if not 0 <= i < self.count:
            raise IndexError(i)
        start, end = self._fat[2 * i], self._fat[2 * i + 1]
        size = end - start
        n = size - offset if n is None else min(n, size - offset)
        if self._data + start + offset + n > self._end:
            raise NDSError("NARC member out of range")
        return self._read(self._data + start + offset, max(0, n))

    __getitem__ = member


def lz77_decompress(data: bytes) -> bytes:
    """Nintendo LZ77 (type 0x10) as used by DS files; returns data unchanged if it isn't LZ77."""
    if len(data) < 4 or data[0] != 0x10:
        return data
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    out = bytearray()
    i = 4
    while len(out) < size and i < len(data):
        flags = data[i]
        i += 1
        for bit in range(8):
            if len(out) >= size or i >= len(data):
                break
            if flags & (0x80 >> bit):
                b1, b2 = data[i], data[i + 1]
                i += 2
                n = (b1 >> 4) + 3
                disp = (((b1 & 0xF) << 8) | b2) + 1
                if disp > len(out):
                    raise NDSError("bad LZ77 data")
                for _ in range(n):
                    out.append(out[-disp])
            else:
                out.append(data[i])
                i += 1
    return bytes(out[:size])


# ---------------------------------------------------------------------------------------------------------
# Gen-4 text (character table: pret tools/msgenc/charmap.txt; only what names need)
# ---------------------------------------------------------------------------------------------------------
EOS = 0xFFFF                          # string terminator
G4_SPACE = 0x01DE
_G4_DEC = {G4_SPACE: " "}
for _i in range(10):
    _G4_DEC[0x0121 + _i] = chr(48 + _i)
for _i in range(26):
    _G4_DEC[0x012B + _i] = chr(65 + _i)
    _G4_DEC[0x0145 + _i] = chr(97 + _i)
for _code, _ch in {0x01AB: "!", 0x01AC: "?", 0x01AD: ",", 0x01AE: ".", 0x01AF: "…", 0x01B0: "·", 0x01B1: "/",
                   0x01B2: "‘", 0x01B3: "’", 0x01B4: "“", 0x01B5: "”", 0x01B9: "(", 0x01BA: ")", 0x01BB: "♂",
                   0x01BC: "♀", 0x01BD: "+", 0x01BE: "-", 0x01BF: "*", 0x01C0: "#", 0x01C1: "=", 0x01C2: "&",
                   0x01C3: "~", 0x01C4: ":", 0x01C5: ";", 0x01D0: "@", 0x01D2: "%", 0x0188: "é", 0x0168: "É",
                   0x01A8: "$", 0x01E8: "°", 0x01E9: "_"}.items():
    _G4_DEC[_code] = _ch
_G4_ENC = {v: k for k, v in _G4_DEC.items()}
_G4_ENC["'"] = 0x01B3                 # ASCII apostrophe -> right single quote (FARFETCH’D)
_G4_ENC['"'] = 0x01B5


def g4_decode(codes) -> str:
    """Gen-4 character codes (ints) -> str, stopping at the terminator. Unknown codes become '?'."""
    out = []
    for c in codes:
        if c == EOS:
            break
        out.append(_G4_DEC.get(c, "?"))
    return "".join(out)


def g4_encode(s: str, n: int | None = None) -> list:
    """str -> Gen-4 codes + terminator; with n, truncated to n-1 chars and padded with the terminator to n."""
    codes = [_G4_ENC.get(ch, _G4_ENC["?"]) for ch in s]
    if n is not None:
        codes = codes[:n - 1]
        return codes + [EOS] * (n - len(codes))
    return codes + [EOS]


def decode_message_bank(raw: bytes) -> list:
    """A Gen-4 message bank (one member of pl_msg.narc) -> list of strings as lists of char codes."""
    count, seed = struct.unpack_from("<HH", raw, 0)
    out = []
    for i in range(count):
        k = (seed * 765 * (i + 1)) & 0xFFFF
        k |= k << 16
        off, length = struct.unpack_from("<II", raw, 4 + 8 * i)
        off ^= k
        length ^= k
        key = ((i + 1) * 596947) & 0xFFFF
        chars = []
        for j in range(length):
            if off + 2 * j + 2 > len(raw):
                break
            chars.append(struct.unpack_from("<H", raw, off + 2 * j)[0] ^ key)
            key = (key + 18749) & 0xFFFF
        out.append(chars)
    return out


# ---------------------------------------------------------------------------------------------------------
# Pokémon Platinum data
# ---------------------------------------------------------------------------------------------------------
PERSONAL_NARC = "/poketool/personal/pl_personal.narc"   # SpeciesData, 44 bytes, index = species (forms 494+)
GROWTH_NARC = "/poketool/personal/pl_growtbl.narc"      # 8 exp tables x 101 u32
MOVES_NARC = "/poketool/waza/pl_waza_tbl.narc"          # MoveTable, 16 bytes per move (pp at +6)
EVO_NARC = "/poketool/personal/evo.narc"                # 7 x (method u16, param u16, target u16) per species
MSG_NARC = "/msgdata/pl_msg.narc"
EVO_TRADE, EVO_TRADE_WITH_HELD_ITEM = 5, 6
BANK_ITEM_NAMES = 392                                   # generated/text_banks.txt line - 1
BANK_SPECIES_NAMES = 412
BANK_LOCATION_NAMES = 433
BANK_MOVE_NAMES = 647
NUM_SPECIES = 493
NUM_MOVES = 467
PLATINUM_CODES = ("CPUE",)                              # US Platinum (other regions untested)

GENDER_MALE_ONLY, GENDER_FEMALE_ONLY, GENDERLESS = 0, 254, 255
# species -> (personal NARC entry of form 1, number of forms) (pret src/pokemon.c Pokemon_GetFormNarcIndex)
FORM_PERSONAL = {386: (496, 4), 413: (499, 3), 487: (501, 2), 492: (502, 2), 479: (503, 6)}


@dataclass(frozen=True)
class Personal:
    """The fields of SpeciesData (include/struct_defs/species.h) the conversions use."""
    base_stats: tuple        # hp, atk, def, speed, spatk, spdef
    types: tuple
    catch_rate: int
    gender_ratio: int        # 0 male only, 254 female only, 255 genderless, else female if (pid & 0xFF) < ratio
    base_friendship: int
    growth_rate: int         # EXP_RATE_*: 0 medium fast, 1 erratic, 2 fluctuating, 3 medium slow, 4 fast, 5 slow
    abilities: tuple

    @classmethod
    def parse(cls, raw: bytes) -> "Personal":
        if len(raw) < 0x1C:
            raise NDSError("short personal entry")
        return cls(tuple(raw[0:6]), (raw[6], raw[7]), raw[8], raw[16], raw[18], raw[19], (raw[22], raw[23]))


class PlatinumData:
    """Everything the PK4 conversions read from the player's Platinum ROM (the `rom_ctx`)."""

    def __init__(self, path: str):
        rom = NDSRom(path)
        try:
            if rom.game_code not in PLATINUM_CODES:
                raise NDSError("not Pokémon Platinum (US): game code %r" % rom.game_code)
            pers = rom.narc(PERSONAL_NARC)
            # entries 0..493 = species, 494/495 egg / bad egg, 496.. alternate forms (see personal_index)
            self.personal = [Personal.parse(lz77_decompress(pers[i])) for i in range(len(pers))]
            grow = rom.narc(GROWTH_NARC)
            self.exp_tables = []
            for i in range(min(len(grow), 8)):
                raw = lz77_decompress(grow[i])
                self.exp_tables.append(list(struct.unpack_from("<101I", raw)))
            moves = rom.narc(MOVES_NARC)
            self.move_pps = [moves.member(i, 6, 1)[0] if i else 0 for i in range(min(len(moves), NUM_MOVES + 1))]
            evo = rom.narc(EVO_NARC)
            self._evos = []
            for i in range(min(len(evo), NUM_SPECIES + 1)):
                raw = lz77_decompress(evo[i])
                self._evos.append([struct.unpack_from("<3H", raw, 6 * k) for k in range(len(raw) // 6)
                                   if struct.unpack_from("<H", raw, 6 * k)[0]])
            msg = rom.narc(MSG_NARC)
            self._species_names = [g4_decode(s) for s in decode_message_bank(msg[BANK_SPECIES_NAMES])]
            self._species_codes = decode_message_bank(msg[BANK_SPECIES_NAMES])
            self._bank_raw = {BANK_LOCATION_NAMES: msg[BANK_LOCATION_NAMES], BANK_ITEM_NAMES: msg[BANK_ITEM_NAMES],
                              BANK_MOVE_NAMES: msg[BANK_MOVE_NAMES]}
            self._banks = {}
        finally:
            rom.close()
        if len(self.personal) <= NUM_SPECIES or len(self.exp_tables) < 6 or len(self._species_names) <= NUM_SPECIES:
            raise NDSError("Platinum data incomplete")
        if self.exp_tables[0][100] != 1000000 or self.exp_tables[0][2] != 8:
            raise NDSError("unexpected experience table")

    # --- species --------------------------------------------------------------------------------------
    def info(self, dex: int) -> Personal | None:
        return self.personal[dex] if 1 <= dex <= NUM_SPECIES else None

    def personal_index(self, dex: int, form: int = 0) -> int:
        """Personal NARC entry of species + form (pret Pokemon_GetFormNarcIndex): Deoxys forms 1-3 -> 496-498,
        Wormadam 1-2 -> 499-500, Giratina Origin -> 501, Shaymin Sky -> 502, Rotom forms 1-5 -> 503-507; every
        other species/form -> the species' own entry."""
        base, count = FORM_PERSONAL.get(dex, (0, 0))
        if base and 0 < form < count and base - 1 + form < len(self.personal):
            return base - 1 + form
        return dex

    def form_base_stats(self, dex: int, form: int = 0):
        """Base stats of a species in a given form (what Pokemon_CalcStats uses via SpeciesData_LoadForm)."""
        if not 1 <= dex <= NUM_SPECIES:
            return None
        return self.personal[self.personal_index(dex, form)].base_stats

    def growth_rate(self, dex: int) -> int:
        p = self.info(dex)
        return p.growth_rate if p else 0

    def gender_ratio(self, dex: int) -> int:
        p = self.info(dex)
        return p.gender_ratio if p else GENDERLESS

    def abilities(self, dex: int) -> tuple:
        p = self.info(dex)
        return p.abilities if p else (0, 0)

    def base_stats(self, dex: int):
        p = self.info(dex)
        return p.base_stats if p else None

    def base_friendship(self, dex: int) -> int:
        p = self.info(dex)
        return p.base_friendship if p else 70

    def species_name(self, dex: int) -> str:
        """English name as the game shows it (Gen 4 uses capitals: 'MR. MIME')."""
        return self._species_names[dex] if 0 <= dex < len(self._species_names) else "?"

    def species_name_codes(self, dex: int) -> list:
        """The name as Gen-4 character codes (no terminator)."""
        if not 0 <= dex < len(self._species_codes):
            return []
        codes = list(self._species_codes[dex])
        return codes[:codes.index(EOS)] if EOS in codes else codes

    def evolutions(self, dex: int) -> list:
        """[(method, param, target species), ...] (EVO_* ids from pret generated/evolution_methods.txt)."""
        return list(self._evos[dex]) if 0 <= dex < len(self._evos) else []

    def trade_evolutions(self) -> dict:
        """{species: [(target, required held item or None), ...]} for every trade evolution in the ROM."""
        out = {}
        for dex, evos in enumerate(self._evos):
            for method, param, target in evos:
                if method in (EVO_TRADE, EVO_TRADE_WITH_HELD_ITEM):
                    out.setdefault(dex, []).append((target, param if method == EVO_TRADE_WITH_HELD_ITEM else None))
        return out

    # --- experience -----------------------------------------------------------------------------------
    def exp_table(self, rate: int) -> list:
        return self.exp_tables[rate if 0 <= rate < len(self.exp_tables) else 0]

    def exp_for_level(self, dex: int, level: int) -> int:
        return self.exp_table(self.growth_rate(dex))[max(0, min(100, level))]

    def level_for_exp(self, dex: int, exp: int) -> int:
        """SpeciesData_GetLevelAt: the highest level whose requirement is <= exp (1..100)."""
        t = self.exp_table(self.growth_rate(dex))
        lv = 1
        while lv < 100 and t[lv + 1] <= exp:
            lv += 1
        return lv

    # --- moves / other text ---------------------------------------------------------------------------
    def move_pp(self, move: int) -> int:
        return self.move_pps[move] if 0 < move < len(self.move_pps) else 0

    def _bank(self, bank: int) -> list:
        if bank not in self._banks:
            self._banks[bank] = [g4_decode(s) for s in decode_message_bank(self._bank_raw[bank])]
        return self._banks[bank]

    def move_name(self, move: int) -> str:
        names = self._bank(BANK_MOVE_NAMES)
        return names[move] if 0 <= move < len(names) else "?"

    def item_name(self, item: int) -> str:
        names = self._bank(BANK_ITEM_NAMES)
        return names[item] if 0 <= item < len(names) else "?"

    def location_name(self, loc: int) -> str:
        names = self._bank(BANK_LOCATION_NAMES)
        return names[loc] if 0 <= loc < len(names) else "?"


_cache: dict = {}
_cache_lock = threading.Lock()


def platinum(path: str) -> PlatinumData:
    """The Platinum data of the ROM at `path`, loaded once per (path, size, mtime). Raises NDSError/OSError."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            hit = _cache[key] = PlatinumData(path)
        return hit
