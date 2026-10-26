"""Generation-4 Pokémon data (PK4, as stored by Pokémon Platinum) and Gen-1/Gen-2 <-> Gen-4 conversion.

Format (pret/pokeplatinum include/struct_defs/pokemon.h, src/pokemon.c; Project Pokémon "PK4"):
  box mon   = 136 bytes: PID u32, flags u16, checksum u16, then four 32-byte blocks A/B/C/D, shuffled by
              ((PID >> 13) & 31) % 24 and XOR-encrypted with the LCRNG (x*0x41C64E6D + 0x6073) seeded by the
              checksum (= 16-bit sum of the plain blocks).
  party mon = 236 bytes: the box mon + 100 bytes of battle stats/mail/capsule encrypted with the LCRNG seeded by PID.
Inside this module "plain" bytes are the decrypted mon with the blocks in A, B, C, D order (same length as the
encrypted form). `decrypt`/`encrypt` convert; `PK4` gives field access over plain bytes.

Store records (SPEC.md section 5): gen1 = 55 bytes (33-byte Yellow box struct + OT + nickname),
gen2 = 55 bytes (32-byte Crystal box struct + OT + nickname + egg flag), gen4 = 136 encrypted box bytes.

Conversion choices (Gen 1/2 -> Gen 4), modelled on the Time Capsule / Pal Park / Virtual Console transfers:
  * species: national dex (Gen 1 records store Yellow's internal index, so the caller passes `dex` - the store
    entry's "dex" field - or a Yellow RomData as `g1rom`). Eggs (gen2 egg flag) are not converted (-> stand-in).
  * PID: deterministic (the same old record always gives the same PK4) from OT ID + DVs + Gen-2 caught data,
    searched with the Gen-4 LCRNG until it keeps the original's **gender** (Gen-2 rule: female iff Attack DV*16
    <= gender ratio, ratio from the Platinum ROM) and its **shininess** (Gen-2 shiny DVs <=> Gen-4 shiny with
    TID = old OT ID, SID = 0). Nature = PID % 25, ability = species ability slot PID & 1 (ROM). The gender bits
    in block B are set to match. Because the PID ignores level/species, mon_key() survives level-ups/evolution.
  * level/exp: exp is copied and clamped to the Gen-4 range of the level it gives (levels come from exp, as the
    Gen-1 box level byte is stale for party mons); growth table from the ROM.
  * moves: copied (ids 1-251 are identical); PP Ups copied; PP set to the Gen-4 maximum (Gen 4 heals mons put in
    the PC, and a healed record survives withdraw/deposit byte-identically).
  * DVs -> IVs: IV = 2*DV + 1 (HP from the derived HP DV; SpAtk and SpDef both from Special). Back: DV = IV >> 1,
    so DVs round-trip exactly.
  * stat exp -> EVs: EV = min(255, ceil(sqrt(stat exp))) - this keeps the Gen-1/2 stat bonus floor(EV/4)
    exactly - Special goes to SpAtk and SpDef; if the total exceeds 510 all EVs are scaled down by 510/total.
    Back: stat exp = EV^2 (Special = max(SpAtk, SpDef) EV). Lossy: stat exp is not restored bit-exactly.
  * nickname: kept if the mon was nicknamed; otherwise the Gen-4 species name from the ROM ("MR. MIME"), flag
    clear, so Platinum renames it on evolution like its own mons. OT name converted (A-Z a-z 0-9 punctuation).
  * TID = old 16-bit OT ID, SID = 0. Language English, ball Poké Ball, met at Pal Park (location 55) on
    2000-01-01, met level = Gen-2 caught level or the current level; origin game FireRed for Gen-1 records and
    HeartGold for Gen-2 records (Platinum then shows "met in Kanto" / "met in Johto"). OT gender from the Gen-2
    caught data, else male.
  * held item: Gen 2 items mapped by name to their Gen-4 id (G2_TO_G4_ITEM; Gen-2 berries -> the Gen-3+ berry
    with the same effect, Pink/Polkadot Bow -> Silk Scarf); unknown/mail/apricorns -> none. Gen 1: none.
  * friendship: Gen 2 copied; Gen 1 gets the species' base friendship. Pokérus byte copied (same format).
  * Unown: Gen-4 form letter from the Gen-2 DVs.
  * Two otherwise-unused Platinum fields keep data the old games need back:
      block C +0x16 (unused)           = Yellow's catch-rate byte (Yellow's mon_key uses it; 0 = none)
      block D +0x1E (HGSS-only u16)    = the Gen-2 caught data bytes (Crystal's mon_key uses them; 0 = none)

Gen 4 -> Gen 1/2 (`to_gen1`/`to_gen2`): only species <= 151/251 whose moves are all <= 165/251 and that aren't
eggs; otherwise None (the game shows a ?NAME stand-in). IVs->DVs and EVs->stat exp as above, exp/level copied,
PP copied (capped), PP Ups kept, item mapped back (Gen 2; dropped for Gen 1), friendship/Pokérus kept (Gen 2),
caught data from the stash (else none), Yellow catch rate from the stash (else the species' catch rate from the
Yellow ROM), nickname converted or - if not nicknamed - the old game's species name (from its ROM when given).
Gen-4 shininess/gender/nature/ability don't carry back (Gen 1/2 derive them from DVs).
Round trip gen1 -> gen4 -> gen1 / gen2 -> gen4 -> gen2 keeps everything except stat exp (only the stat bonus
survives), current HP/status of Gen-1 box mons (Gen 4 heals) and the name padding bytes after the terminator.
"""
from __future__ import annotations

import base64
import hashlib
import math
import struct
import uuid

from . import text
from .ndsrom import EOS, g4_decode, g4_encode

BOX_SIZE = 136
PARTY_SIZE = 236
BLOCK = 32
G1_REC = 55
G2_REC = 55

# block offsets in plain (unshuffled) data
A, B, C, D = 0x08, 0x28, 0x48, 0x68

LANG_ENGLISH = 2
VERSION_FIRERED, VERSION_HEARTGOLD, VERSION_PLATINUM = 4, 7, 12
BALL_POKE = 4
LOC_PAL_PARK = 55
MET_DATE = (0, 1, 1)                 # 2000-01-01
DITTO, TRANSFORM, UNOWN = 132, 144, 201
LIMBER = 7
STANDIN_OT = "YLINK"
NUM_SPECIES = 493
GENDER_MALE, GENDER_FEMALE, GENDER_NONE = 0, 1, 2
G1_MAX_MOVE, G2_MAX_MOVE = 165, 251

# Gen-2 item id -> Platinum item id (pret item constants matched by name; berries by effect)
G2_TO_G4_ITEM = {
    0x01: 1, 0x02: 2, 0x03: 213, 0x04: 3, 0x05: 4, 0x06: 442, 0x07: 450, 0x08: 81, 0x09: 18, 0x0A: 19, 0x0B: 20,
    0x0C: 21, 0x0D: 22, 0x0E: 23, 0x0F: 24, 0x10: 25, 0x11: 26, 0x12: 17, 0x13: 78, 0x14: 79, 0x15: 41, 0x16: 82,
    0x17: 83, 0x18: 84, 0x1A: 45, 0x1B: 46, 0x1C: 47, 0x1D: 48, 0x1E: 256, 0x1F: 49, 0x20: 50, 0x21: 60, 0x22: 85,
    0x23: 257, 0x24: 92, 0x25: 63, 0x26: 27, 0x27: 28, 0x28: 29, 0x29: 55, 0x2A: 76, 0x2B: 77, 0x2C: 56, 0x2E: 30,
    0x2F: 31, 0x30: 32, 0x31: 57, 0x33: 58, 0x34: 59, 0x35: 61, 0x36: 444, 0x39: 216, 0x3A: 445, 0x3B: 446,
    0x3D: 447, 0x3E: 51, 0x3F: 38, 0x40: 39, 0x41: 40, 0x43: 464, 0x44: 456, 0x48: 33, 0x49: 217, 0x4A: 151,
    0x4C: 237, 0x4D: 244, 0x4E: 149, 0x4F: 153, 0x50: 152, 0x51: 245, 0x52: 221, 0x53: 156, 0x54: 150, 0x56: 86,
    0x57: 87, 0x58: 222, 0x5B: 223, 0x5E: 224, 0x5F: 243, 0x60: 248, 0x62: 241, 0x66: 240, 0x68: 251, 0x69: 259,
    0x6A: 228, 0x6B: 246, 0x6C: 242, 0x6D: 157, 0x6E: 88, 0x6F: 89, 0x70: 229, 0x71: 247, 0x75: 239, 0x76: 258,
    0x77: 230, 0x79: 34, 0x7A: 35, 0x7B: 36, 0x7C: 37, 0x7D: 238, 0x7E: 231, 0x83: 90, 0x84: 91, 0x8A: 249,
    0x8B: 43, 0x8C: 232, 0x8F: 233, 0x90: 250, 0x92: 234, 0x96: 154, 0x97: 235, 0x9C: 44, 0xA3: 236, 0xA9: 80,
    0xAA: 251, 0xAC: 252, 0xAD: 155, 0xAE: 158}
G4_TO_G2_ITEM = {}
for _g2, _g4 in sorted(G2_TO_G4_ITEM.items(), reverse=True):
    G4_TO_G2_ITEM[_g4] = _g2          # Silk Scarf -> Pink Bow (0x68) rather than Polkadot Bow

# block order: for shuffle value sv, BLOCK_POS[sv] = positions of A, B, C, D in the stored data
BLOCK_POS = [(0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2), (0, 2, 3, 1), (0, 3, 2, 1),
             (1, 0, 2, 3), (1, 0, 3, 2), (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
             (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2), (2, 3, 0, 1), (3, 2, 0, 1),
             (1, 2, 3, 0), (1, 3, 2, 0), (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0)]


def _b64(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


# ---------------------------------------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------------------------------------
def _crypt(data: bytes, seed: int) -> bytes:
    """XOR 16-bit words with the Gen-4 LCRNG stream (src/math_util.c EncodeData; its own inverse)."""
    n = len(data) // 2
    words = struct.unpack("<%dH" % n, data[:2 * n])
    out = []
    s = seed & 0xFFFFFFFF
    for w in words:
        s = (s * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        out.append(w ^ (s >> 16))
    return struct.pack("<%dH" % n, *out)


def _shuffle_value(pid: int) -> int:
    return ((pid >> 13) & 31) % 24


def _empty(n: int) -> bytes:
    return bytes(8) + _crypt(bytes(BOX_SIZE - 8), 0) + _crypt(bytes(n - BOX_SIZE), 0)


def checksum(plain: bytes) -> int:
    """16-bit sum of the 64 words of the (decrypted) data blocks."""
    return sum(struct.unpack_from("<64H", plain, 8)) & 0xFFFF


def decrypt(data: bytes) -> bytes:
    """Encrypted box (136) or party (236) mon -> plain bytes (blocks in A,B,C,D order, party section decrypted)."""
    data = bytes(data)
    if len(data) not in (BOX_SIZE, PARTY_SIZE):
        raise ValueError("PK4 data must be 136 or 236 bytes, not %d" % len(data))
    pid, _flags, chk = struct.unpack_from("<IHH", data, 0)
    blocks = _crypt(data[8:BOX_SIZE], chk)
    pos = BLOCK_POS[_shuffle_value(pid)]
    plain = bytearray(data[:8])
    for k in range(4):
        plain += blocks[pos[k] * BLOCK:(pos[k] + 1) * BLOCK]
    if len(data) == PARTY_SIZE:
        plain += _crypt(data[BOX_SIZE:], pid)
    return bytes(plain)


def encrypt(plain: bytes) -> bytes:
    """Plain bytes -> encrypted (recomputes the checksum; clears the in-RAM 'decrypted' flags)."""
    plain = bytearray(plain)
    if len(plain) not in (BOX_SIZE, PARTY_SIZE):
        raise ValueError("PK4 data must be 136 or 236 bytes, not %d" % len(plain))
    pid = struct.unpack_from("<I", plain, 0)[0]
    chk = checksum(plain)
    struct.pack_into("<HH", plain, 4, struct.unpack_from("<H", plain, 4)[0] & ~0x7, chk)
    pos = BLOCK_POS[_shuffle_value(pid)]
    stored = [b""] * 4
    for k in range(4):
        stored[pos[k]] = bytes(plain[8 + k * BLOCK:8 + (k + 1) * BLOCK])
    out = bytes(plain[:8]) + _crypt(b"".join(stored), chk)
    if len(plain) == PARTY_SIZE:
        out += _crypt(bytes(plain[BOX_SIZE:]), pid)
    return out


def is_empty(data: bytes) -> bool:
    """An empty PC/party slot (BoxPokemon_Init: zeros encrypted with seed 0, i.e. species 0)."""
    try:
        return struct.unpack_from("<H", decrypt(data), A)[0] == 0
    except ValueError:
        return False


def valid(data: bytes) -> bool:
    """True if `data` is an encrypted box/party mon whose checksum matches."""
    try:
        p = decrypt(data)
    except ValueError:
        return False
    return checksum(p) == struct.unpack_from("<H", p, 6)[0]


FLAG_PARTY_DECRYPTED, FLAG_BOX_DECRYPTED, FLAG_CHECKSUM_FAILED = 1, 2, 4      # BoxPokemon +4 bit field


def in_place_decrypted(data: bytes) -> bool:
    """The game is working on this mon right now (BoxPokemon_EnterDecryptionContext: boxDecrypted set, the data
    blocks XOR-decrypted in place but still in their PID-shuffled order; pret src/pokemon.c). A DS frame can end in
    the middle of that."""
    data = bytes(data)
    return len(data) >= BOX_SIZE and bool(struct.unpack_from("<H", data, 4)[0] & FLAG_BOX_DECRYPTED) and \
        checksum(data) == struct.unpack_from("<H", data, 6)[0]          # the word sum ignores the block order


def reencrypt_in_place(data: bytes) -> bytes:
    """What BoxPokemon_ExitDecryptionContext / Pokemon_ExitDecryptionContext write back for an in-place decrypted
    mon: flags cleared, XOR with the checksum (blocks stay where they are), party section XOR with the PID if it
    was decrypted too. Encrypted data is returned with the flags cleared."""
    data = bytearray(data)
    flags, chk = struct.unpack_from("<HH", data, 4)
    if flags & FLAG_BOX_DECRYPTED:
        data[8:BOX_SIZE] = _crypt(bytes(data[8:BOX_SIZE]), chk)
    if len(data) == PARTY_SIZE and flags & FLAG_PARTY_DECRYPTED:
        data[BOX_SIZE:] = _crypt(bytes(data[BOX_SIZE:]), struct.unpack_from("<I", data, 0)[0])
    struct.pack_into("<H", data, 4, flags & ~0x7)
    return bytes(data)


def _plain_of(data: bytes) -> bytes:
    """Accept encrypted, plain (A,B,C,D order, as decrypt() returns) or in-place decrypted bytes (the game's
    working state, see in_place_decrypted); returns plain."""
    data = bytes(data)
    if len(data) in (BOX_SIZE, PARTY_SIZE) and struct.unpack_from("<H", data, 4)[0] & FLAG_BOX_DECRYPTED:
        if in_place_decrypted(data):
            return decrypt(reencrypt_in_place(data))
    p = decrypt(data)
    if checksum(p) == struct.unpack_from("<H", p, 6)[0]:
        return p
    if checksum(data) == struct.unpack_from("<H", data, 6)[0]:
        return data
    return p


EMPTY_BOX = _empty(BOX_SIZE)            # how the game stores an empty box slot (seen 540x in Platinum's PC)
EMPTY_PARTY = _empty(PARTY_SIZE)


# ---------------------------------------------------------------------------------------------------------
# Field access
# ---------------------------------------------------------------------------------------------------------
NATURES = ["Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed", "Impish", "Lax",
           "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild", "Quiet", "Bashful", "Rash",
           "Calm", "Gentle", "Sassy", "Careful", "Quirky"]


class PK4:
    """Field access over plain (decrypted) 136/236 bytes. `PK4.from_encrypted(data)`, `.encrypted()`."""

    def __init__(self, plain: bytes | None = None):
        self.d = bytearray(plain if plain is not None else BOX_SIZE)
        if len(self.d) not in (BOX_SIZE, PARTY_SIZE):
            raise ValueError("bad PK4 size")

    @classmethod
    def from_encrypted(cls, data: bytes) -> "PK4":
        return cls(decrypt(data))

    def encrypted(self) -> bytes:
        return encrypt(bytes(self.d))

    def box_bytes(self) -> bytes:
        """136 encrypted bytes (what the store keeps)."""
        return encrypt(bytes(self.d[:BOX_SIZE]))

    def plain(self) -> bytes:
        return bytes(self.d)

    def checksum_ok(self) -> bool:
        return checksum(self.d) == self._u16(6)

    # raw helpers
    def _u8(self, o):
        return self.d[o]

    def _u16(self, o):
        return struct.unpack_from("<H", self.d, o)[0]

    def _u32(self, o):
        return struct.unpack_from("<I", self.d, o)[0]

    def _s8(self, o, v):
        self.d[o] = v & 0xFF

    def _s16(self, o, v):
        struct.pack_into("<H", self.d, o, v & 0xFFFF)

    def _s32(self, o, v):
        struct.pack_into("<I", self.d, o, v & 0xFFFFFFFF)

    # header
    pid = property(lambda s: s._u32(0), lambda s, v: s._s32(0, v))
    species = property(lambda s: s._u16(A + 0x00), lambda s, v: s._s16(A + 0x00, v))
    item = property(lambda s: s._u16(A + 0x02), lambda s, v: s._s16(A + 0x02, v))
    otid = property(lambda s: s._u32(A + 0x04), lambda s, v: s._s32(A + 0x04, v))
    tid = property(lambda s: s._u16(A + 0x04), lambda s, v: s._s16(A + 0x04, v))
    sid = property(lambda s: s._u16(A + 0x06), lambda s, v: s._s16(A + 0x06, v))
    exp = property(lambda s: s._u32(A + 0x08), lambda s, v: s._s32(A + 0x08, v))
    friendship = property(lambda s: s._u8(A + 0x0C), lambda s, v: s._s8(A + 0x0C, v))
    ability = property(lambda s: s._u8(A + 0x0D), lambda s, v: s._s8(A + 0x0D, v))
    markings = property(lambda s: s._u8(A + 0x0E), lambda s, v: s._s8(A + 0x0E, v))
    language = property(lambda s: s._u8(A + 0x0F), lambda s, v: s._s8(A + 0x0F, v))

    @property
    def evs(self) -> list:
        """[hp, atk, def, speed, spatk, spdef]"""
        return list(self.d[A + 0x10:A + 0x16])

    @evs.setter
    def evs(self, v):
        self.d[A + 0x10:A + 0x16] = bytes(max(0, min(255, x)) for x in v)

    @property
    def moves(self) -> list:
        return list(struct.unpack_from("<4H", self.d, B))

    @moves.setter
    def moves(self, v):
        v = (list(v) + [0, 0, 0, 0])[:4]
        struct.pack_into("<4H", self.d, B, *v)

    @property
    def pp(self) -> list:
        return list(self.d[B + 0x08:B + 0x0C])

    @pp.setter
    def pp(self, v):
        self.d[B + 0x08:B + 0x0C] = bytes((list(v) + [0, 0, 0, 0])[:4])

    @property
    def pp_ups(self) -> list:
        return list(self.d[B + 0x0C:B + 0x10])

    @pp_ups.setter
    def pp_ups(self, v):
        self.d[B + 0x0C:B + 0x10] = bytes((list(v) + [0, 0, 0, 0])[:4])

    @property
    def iv_word(self) -> int:
        return self._u32(B + 0x10)

    @property
    def ivs(self) -> list:
        """[hp, atk, def, speed, spatk, spdef]"""
        w = self.iv_word
        return [(w >> (5 * i)) & 31 for i in range(6)]

    @ivs.setter
    def ivs(self, v):
        w = self.iv_word & 0xC0000000
        for i, x in enumerate(v[:6]):
            w |= (max(0, min(31, x)) & 31) << (5 * i)
        self._s32(B + 0x10, w)

    @property
    def is_egg(self) -> bool:
        return bool(self.iv_word >> 30 & 1)

    @is_egg.setter
    def is_egg(self, v):
        self._s32(B + 0x10, (self.iv_word & ~(1 << 30)) | (bool(v) << 30))

    @property
    def has_nickname(self) -> bool:
        return bool(self.iv_word >> 31 & 1)

    @has_nickname.setter
    def has_nickname(self, v):
        self._s32(B + 0x10, (self.iv_word & 0x7FFFFFFF) | (bool(v) << 31))

    @property
    def fateful(self) -> bool:
        return bool(self.d[B + 0x18] & 1)

    @property
    def gender_bits(self) -> int:
        return (self.d[B + 0x18] >> 1) & 3

    @gender_bits.setter
    def gender_bits(self, v):
        self.d[B + 0x18] = (self.d[B + 0x18] & ~0x06) | ((v & 3) << 1)

    @property
    def form(self) -> int:
        return self.d[B + 0x18] >> 3

    @form.setter
    def form(self, v):
        self.d[B + 0x18] = (self.d[B + 0x18] & 0x07) | ((v & 31) << 3)

    egg_location_pt = property(lambda s: s._u16(B + 0x1C), lambda s, v: s._s16(B + 0x1C, v))
    met_location_pt = property(lambda s: s._u16(B + 0x1E), lambda s, v: s._s16(B + 0x1E, v))

    @property
    def nickname_codes(self) -> list:
        return list(struct.unpack_from("<11H", self.d, C))

    @nickname_codes.setter
    def nickname_codes(self, v):
        v = (list(v) + [0] * 11)[:11]
        struct.pack_into("<11H", self.d, C, *v)

    @property
    def nickname(self) -> str:
        return g4_decode(self.nickname_codes)

    g1_catch_rate = property(lambda s: s._u8(C + 0x16), lambda s, v: s._s8(C + 0x16, v))   # stash (unused byte)
    origin_game = property(lambda s: s._u8(C + 0x17), lambda s, v: s._s8(C + 0x17, v))

    @property
    def ot_codes(self) -> list:
        return list(struct.unpack_from("<8H", self.d, D))

    @ot_codes.setter
    def ot_codes(self, v):
        v = (list(v) + [0] * 8)[:8]
        struct.pack_into("<8H", self.d, D, *v)

    @property
    def ot_name(self) -> str:
        return g4_decode(self.ot_codes)

    @property
    def met_date(self):
        return tuple(self.d[D + 0x13:D + 0x16])

    @met_date.setter
    def met_date(self, v):
        self.d[D + 0x13:D + 0x16] = bytes(v[:3])

    egg_location = property(lambda s: s._u16(D + 0x16), lambda s, v: s._s16(D + 0x16, v))
    met_location = property(lambda s: s._u16(D + 0x18), lambda s, v: s._s16(D + 0x18, v))
    pokerus = property(lambda s: s._u8(D + 0x1A), lambda s, v: s._s8(D + 0x1A, v))
    ball = property(lambda s: s._u8(D + 0x1B), lambda s, v: s._s8(D + 0x1B, v))

    @property
    def met_level(self) -> int:
        return self.d[D + 0x1C] & 0x7F

    @met_level.setter
    def met_level(self, v):
        self.d[D + 0x1C] = (self.d[D + 0x1C] & 0x80) | (v & 0x7F)

    @property
    def ot_female(self) -> bool:
        return bool(self.d[D + 0x1C] & 0x80)

    @ot_female.setter
    def ot_female(self, v):
        self.d[D + 0x1C] = (self.d[D + 0x1C] & 0x7F) | (0x80 if v else 0)

    encounter_type = property(lambda s: s._u8(D + 0x1D), lambda s, v: s._s8(D + 0x1D, v))
    g2_caught = property(lambda s: s._u16(D + 0x1E), lambda s, v: s._s16(D + 0x1E, v))       # stash (HGSS-only)

    # party section (only for 236-byte data)
    @property
    def is_party(self) -> bool:
        return len(self.d) == PARTY_SIZE

    status = property(lambda s: s._u32(0x88), lambda s, v: s._s32(0x88, v))
    party_level = property(lambda s: s._u8(0x8C), lambda s, v: s._s8(0x8C, v))
    hp = property(lambda s: s._u16(0x8E), lambda s, v: s._s16(0x8E, v))

    @property
    def stats(self) -> list:
        """[maxhp, atk, def, speed, spatk, spdef] from the party section."""
        return list(struct.unpack_from("<6H", self.d, 0x90))

    @stats.setter
    def stats(self, v):
        struct.pack_into("<6H", self.d, 0x90, *v)

    # derived
    @property
    def nature(self) -> int:
        return self.pid % 25

    @property
    def shiny(self) -> bool:
        return is_shiny(self.pid, self.tid, self.sid)

    def level(self, rom) -> int:
        return rom.level_for_exp(self.species, self.exp)


def is_shiny(pid: int, tid: int, sid: int) -> bool:
    return (tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)) < 8


def gender_of(ratio: int, pid: int) -> int:
    """SpeciesData_GetGenderOf."""
    if ratio == 0:
        return GENDER_MALE
    if ratio == 254:
        return GENDER_FEMALE
    if ratio == 255:
        return GENDER_NONE
    return GENDER_FEMALE if ratio > (pid & 0xFF) else GENDER_MALE


def nature_mult(nature: int, stat: int) -> int:
    """+1 / -1 / 0 for stat 1..5 (atk, def, speed, spatk, spdef)."""
    up, down = nature // 5, nature % 5
    if up == down:
        return 0
    return 1 if stat - 1 == up else (-1 if stat - 1 == down else 0)


def calc_stats(pk: PK4, rom, level: int | None = None) -> list:
    """Pokemon_CalcStats: [maxhp, atk, def, speed, spatk, spdef]. Uses the base stats of the mon's FORM (Deoxys,
    Wormadam, Giratina Origin, Shaymin Sky, Rotom appliances) when the ROM data knows forms."""
    form_stats = getattr(rom, "form_base_stats", None)
    base = form_stats(pk.species, pk.form) if form_stats else rom.base_stats(pk.species)
    lv = level if level is not None else pk.level(rom)
    iv, ev = pk.ivs, pk.evs
    out = []
    for i in range(6):
        if i == 0:
            v = 1 if pk.species == 292 else (2 * base[0] + iv[0] + ev[0] // 4) * lv // 100 + lv + 10
        else:
            v = (2 * base[i] + iv[i] + ev[i] // 4) * lv // 100 + 5
            m = nature_mult(pk.nature, i)
            v = v * 110 // 100 if m > 0 else (v * 90 // 100 if m < 0 else v)
        out.append(v)
    return out


# Party bytes 0x9C-0xEB of a mon with no mail and no ball capsule, as Pokemon_FromBoxPokemon leaves them
# (Mail_New: language English, version Platinum, mail type 0xFF, empty names/sentences). Seen on the real game.
PARTY_TAIL = bytes.fromhex("0000000000020cff" + "ff" * 22 + "0000ffff0000ffffffffffff0000ffffffffffff9c01ffffffff"
                           + "00" * 24)


def to_party(box: bytes, rom) -> bytes:
    """Encrypted 136-byte box mon -> encrypted 236-byte party mon at full health, no status/mail/capsule
    (what the game builds when a mon is withdrawn)."""
    pk = PK4(decrypt(box[:BOX_SIZE]) + bytes(0x9C - BOX_SIZE) + PARTY_TAIL)
    lv = pk.level(rom)
    pk.party_level = lv
    st = calc_stats(pk, rom, lv)
    pk.stats = st
    pk.hp = st[0]
    return pk.encrypted()


# ---------------------------------------------------------------------------------------------------------
# experience
# ---------------------------------------------------------------------------------------------------------
def exp_for(rate: int, n: int) -> int:
    """Gen-3/4 growth curves (EXP_RATE_*: 0 medium fast, 1 erratic, 2 fluctuating, 3 medium slow, 4 fast,
    5 slow); same numbers as the ROM's pl_growtbl (checked by test_ndsrom)."""
    if n <= 1:
        return 0
    c = n ** 3
    if rate == 1:
        if n <= 50:
            return c * (100 - n) // 50
        if n <= 68:
            return c * (150 - n) // 100
        if n <= 98:
            return c * ((1911 - 10 * n) // 3) // 500
        return c * (160 - n) // 100
    if rate == 2:
        if n <= 15:
            return c * ((n + 1) // 3 + 24) // 50
        if n <= 36:
            return c * (n + 14) // 50
        return c * (n // 2 + 32) // 50
    if rate == 3:
        return 6 * c // 5 - 15 * n * n + 100 * n - 140
    if rate == 4:
        return 4 * c // 5
    if rate == 5:
        return 5 * c // 4
    return c


def _level_from(rate: int, exp: int) -> int:
    lv = 1
    while lv < 100 and exp_for(rate, lv + 1) <= exp:
        lv += 1
    return lv


class _Growth:
    """Level <-> exp for a dex number, from the Platinum ROM if given, else from an old game's RomData
    (Gen-1/2 growth ids 0, 3, 4, 5 equal Gen 4's; no Gen-1/2 species uses 1 or 2)."""

    def __init__(self, rom=None, g1rom=None, g2rom=None):
        self.rom, self.g1rom, self.g2rom = rom, g1rom, g2rom

    def rate(self, dex: int):
        if self.rom is not None:
            return self.rom.growth_rate(dex)
        if self.g2rom is not None and 1 <= dex <= 251:
            return self.g2rom.growth_rate(dex)
        if self.g1rom is not None and 1 <= dex <= 151:
            return self.g1rom.growth_rate(self.g1rom.species_for_dex(dex))
        return None

    def level(self, dex: int, exp: int):
        r = self.rate(dex)
        return None if r is None else _level_from(r, exp)

    def exp(self, dex: int, level: int):
        r = self.rate(dex)
        return None if r is None else exp_for(r, level)


# ---------------------------------------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------------------------------------
def _norm_name(s: str) -> str:
    return "".join(ch for ch in s.upper().replace("’", "'") if ch not in " .'")


def _old_to_g4(raw: bytes, n: int) -> list:
    """Gen-1/2 name bytes -> Gen-4 codes, n slots (terminator, then zeros as the game's cleared struct has)."""
    codes = g4_encode(text.decode(raw))[:n - 1]
    codes.append(EOS)
    return codes + [0] * (n - len(codes))


def _g4_to_old(codes, n: int = 11, pad: int = text.TERMINATOR) -> bytes:
    """Gen-4 codes -> Gen-1/2 name bytes: chars, 0x50 terminator, then `pad` (Crystal pads names with 0x50,
    Yellow's player names - hence OT names of the player's own mons - with 0x00)."""
    s = g4_decode(codes).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    raw = text.encode(s, n)
    end = raw.index(text.TERMINATOR) if text.TERMINATOR in raw else n
    return raw[:end + 1] + bytes([pad]) * (n - end - 1)


def _g1_species_name(g4name: str) -> str:
    """Gen-4 species name -> the Gen-1/2 spelling (MR. MIME -> MR.MIME, FARFETCH’D -> FARFETCH'D)."""
    return g4name.replace(". ", ".").replace("’", "'")


# ---------------------------------------------------------------------------------------------------------
# old-format helpers
# ---------------------------------------------------------------------------------------------------------
def _hp_dv(atk, dfn, spd, spc):
    return ((atk & 1) << 3) | ((dfn & 1) << 2) | ((spd & 1) << 1) | (spc & 1)


def _g2_shiny(dvs: int) -> bool:
    return (dvs >> 8) & 0x2F == 0x2A and dvs & 0xFF == 0xAA


def _g2_female(ratio: int, atk_dv: int) -> bool | None:
    """Gen-2 gender from the Attack DV; None for single-gender/genderless species."""
    if ratio in (0, 254, 255):
        return None
    return atk_dv * 16 <= ratio


def _unown_form(dvs: int) -> int:
    atk, dfn, spd, spc = dvs >> 12, (dvs >> 8) & 15, (dvs >> 4) & 15, dvs & 15
    v = ((atk & 6) << 5) | ((dfn & 6) << 3) | ((spd & 6) << 1) | ((spc & 6) >> 1)
    return v // 10


def _evs_from_statexp(se: list) -> list:
    """[hp, atk, def, spd, spc] stat exp -> six EVs (see module doc)."""
    ev = [min(255, math.isqrt(x - 1) + 1 if x > 0 else 0) for x in se]
    ev = [ev[0], ev[1], ev[2], ev[3], ev[4], ev[4]]
    total = sum(ev)
    if total > 510:
        ev = [e * 510 // total for e in ev]
    return ev


def _statexp_from_evs(ev: list) -> list:
    return [min(65535, e * e) for e in (ev[0], ev[1], ev[2], ev[3], max(ev[4], ev[5]))]


def _pid_for(seed: int, tid: int, sid: int, shiny: bool, ratio: int, female) -> int:
    """Deterministic PID with the wanted shininess and gender (Gen-4 LCRNG from `seed`)."""
    s = seed & 0xFFFFFFFF

    def nxt():
        nonlocal s
        s = (s * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        return s >> 16

    pid = 0
    for _ in range(100000):
        lo, hi = nxt(), nxt()
        if shiny:
            hi = tid ^ sid ^ lo ^ (nxt() & 7)
        pid = (hi << 16) | lo
        if is_shiny(pid, tid, sid) != shiny:
            continue
        if female is not None and (gender_of(ratio, pid) == GENDER_FEMALE) != female:
            continue
        return pid
    return pid


def _seed(otid: int, dvs: int, caught: int) -> int:
    h = hashlib.sha1(b"yl-pk4-%d-%d-%d" % (otid, dvs, caught)).digest()
    return struct.unpack("<I", h[:4])[0]


def _max_pp(rom, move: int, ups: int, cur: int) -> int:
    base = rom.move_pp(move) if rom is not None else 0
    if not base:
        return cur
    return base + base * ups // 5


def _build(rom, *, dex, exp, moves, pp_bytes, dvs, statexp, otid, ot_raw, nick_raw, item, friendship, pokerus,
           caught, catch_rate, origin) -> bytes:
    """Common Gen-1/2 -> Gen-4 builder (old fields already extracted). Returns 136 encrypted bytes."""
    info_ratio = rom.gender_ratio(dex)
    atk, dfn, spd, spc = dvs >> 12, (dvs >> 8) & 15, (dvs >> 4) & 15, dvs & 15
    pid = _pid_for(_seed(otid, dvs, caught), otid, 0, _g2_shiny(dvs), info_ratio, _g2_female(info_ratio, atk))
    pk = PK4()
    pk.pid = pid
    pk.species = dex
    pk.item = item
    pk.tid, pk.sid = otid, 0
    level = rom.level_for_exp(dex, exp)
    if level >= 100:
        pk.exp = rom.exp_for_level(dex, 100)
    else:
        pk.exp = max(rom.exp_for_level(dex, level), min(exp, rom.exp_for_level(dex, level + 1) - 1))
    pk.friendship = friendship
    ab = rom.abilities(dex)
    pk.ability = ab[1] if (ab[1] and pid & 1) else ab[0]
    pk.language = LANG_ENGLISH
    pk.evs = _evs_from_statexp(statexp)
    mv = [m if 0 < m <= G2_MAX_MOVE else 0 for m in moves]
    pk.moves = mv
    ups = [(b >> 6) if m else 0 for b, m in zip(pp_bytes, mv)]
    pk.pp_ups = ups
    pk.pp = [min(255, _max_pp(rom, m, u, b & 0x3F)) if m else 0 for m, u, b in zip(mv, ups, pp_bytes)]
    pk.ivs = [2 * _hp_dv(atk, dfn, spd, spc) + 1, 2 * atk + 1, 2 * dfn + 1, 2 * spd + 1, 2 * spc + 1, 2 * spc + 1]
    pk.gender_bits = gender_of(info_ratio, pid)
    if dex == UNOWN:
        pk.form = _unown_form(dvs)
    g4name = rom.species_name(dex)
    nicked = _norm_name(text.decode(nick_raw)) != _norm_name(g4name)
    if nicked:
        pk.nickname_codes = _old_to_g4(nick_raw, 11)
    else:
        codes = list(rom.species_name_codes(dex))[:10] if hasattr(rom, "species_name_codes") else \
            g4_encode(g4name)[:-1][:10]
        pk.nickname_codes = codes + [EOS] + [0] * (10 - len(codes))
    pk.has_nickname = nicked
    pk.origin_game = origin
    pk.g1_catch_rate = catch_rate
    pk.ot_codes = _old_to_g4(ot_raw, 8)
    pk.met_date = MET_DATE
    pk.met_location = LOC_PAL_PARK
    pk.met_location_pt = LOC_PAL_PARK
    pk.pokerus = pokerus
    pk.ball = BALL_POKE
    pk.met_level = caught & 0x3F if caught & 0x3F else level
    pk.ot_female = bool(caught >> 15 & 1)
    pk.g2_caught = caught
    return pk.box_bytes()


# ---------------------------------------------------------------------------------------------------------
# Gen 1 / Gen 2 -> Gen 4
# ---------------------------------------------------------------------------------------------------------
def from_gen1(rec: bytes, rom, dex: int | None = None, g1rom=None) -> bytes | None:
    """55-byte Yellow record -> 136-byte encrypted PK4, or None. `dex` = national dex of rec[0] (the store
    entry's "dex"); alternatively pass Yellow's RomData as g1rom."""
    rec = bytes(rec)
    if len(rec) < G1_REC:
        return None
    if dex is None and g1rom is not None:
        dex = g1rom.dex_no(rec[0])
    if not dex or not 1 <= dex <= 151:
        return None
    moves = list(rec[8:12])
    if any(m > G1_MAX_MOVE for m in moves) or not any(moves):
        return None
    exp = int.from_bytes(rec[14:17], "big")
    statexp = [int.from_bytes(rec[17 + 2 * i:19 + 2 * i], "big") for i in range(5)]
    return _build(rom, dex=dex, exp=exp, moves=moves, pp_bytes=list(rec[29:33]), dvs=(rec[27] << 8) | rec[28],
                  statexp=statexp, otid=(rec[12] << 8) | rec[13], ot_raw=rec[33:44], nick_raw=rec[44:55], item=0,
                  friendship=rom.base_friendship(dex), pokerus=0, caught=0, catch_rate=rec[7],
                  origin=VERSION_FIRERED)


def from_gen2(rec: bytes, rom) -> bytes | None:
    """55-byte Crystal record (54 accepted) -> 136-byte encrypted PK4, or None (eggs, bad data)."""
    rec = bytes(rec)
    if len(rec) < 54 or (len(rec) > 54 and rec[54]):
        return None
    dex = rec[0]
    if not 1 <= dex <= 251:
        return None
    moves = list(rec[2:6])
    if any(m > G2_MAX_MOVE for m in moves) or not any(moves):
        return None
    exp = int.from_bytes(rec[8:11], "big")
    statexp = [int.from_bytes(rec[11 + 2 * i:13 + 2 * i], "big") for i in range(5)]
    return _build(rom, dex=dex, exp=exp, moves=moves, pp_bytes=list(rec[23:27]), dvs=(rec[21] << 8) | rec[22],
                  statexp=statexp, otid=(rec[6] << 8) | rec[7], ot_raw=rec[32:43], nick_raw=rec[43:54],
                  item=G2_TO_G4_ITEM.get(rec[1], 0), friendship=rec[27], pokerus=rec[28],
                  caught=rec[29] | (rec[30] << 8), catch_rate=0, origin=VERSION_HEARTGOLD)


# ---------------------------------------------------------------------------------------------------------
# Gen 4 -> Gen 2 / Gen 1
# ---------------------------------------------------------------------------------------------------------
def to_gen2(data: bytes, rom=None, g2rom=None) -> bytes | None:
    """PK4 (box/party, encrypted or plain) -> 55-byte Crystal record, or None if Gen 2 can't hold it.
    Level needs a growth table: the Platinum ROM data (`rom`) or Crystal's RomData (`g2rom`)."""
    pk = PK4(_plain_of(data)[:BOX_SIZE])
    if not pk.checksum_ok() or pk.is_egg:
        return None
    dex = pk.species
    if not 1 <= dex <= 251:
        return None
    moves = pk.moves
    if any(m > G2_MAX_MOVE for m in moves) or not any(moves):
        return None
    growth = _Growth(rom=rom, g2rom=g2rom)
    level = growth.level(dex, pk.exp)
    if level is None:
        return None
    exp = min(pk.exp, growth.exp(dex, 100), 0xFFFFFF)
    iv = pk.ivs
    atk, dfn, spd, spc = iv[1] >> 1, iv[2] >> 1, iv[3] >> 1, iv[4] >> 1
    d = bytearray(32)
    d[0] = dex
    d[1] = G4_TO_G2_ITEM.get(pk.item, 0)
    d[2:6] = bytes(moves)
    d[6], d[7] = pk.tid >> 8, pk.tid & 0xFF
    d[8:11] = exp.to_bytes(3, "big")
    for i, se in enumerate(_statexp_from_evs(pk.evs)):
        d[11 + 2 * i:13 + 2 * i] = se.to_bytes(2, "big")
    d[21], d[22] = (atk << 4) | dfn, (spd << 4) | spc
    for i, (m, p, u) in enumerate(zip(moves, pk.pp, pk.pp_ups)):
        if m:
            cap = 63
            if g2rom is not None and g2rom.move_pp(m):
                base = g2rom.move_pp(m)
                cap = min(63, base + u * min(base // 5, 7))
            d[23 + i] = (min(u, 3) << 6) | min(p, cap)
    d[27] = pk.friendship
    d[28] = pk.pokerus
    caught = pk.g2_caught
    d[29], d[30] = caught & 0xFF, caught >> 8
    d[31] = level
    ot = _g4_to_old(pk.ot_codes)
    if pk.has_nickname:
        nick = _g4_to_old(pk.nickname_codes)
    elif g2rom is not None:
        nick = g2rom.species_name_raw(dex)
    else:
        nick = text.encode(_g1_species_name(pk.nickname), 11)
    return bytes(d) + ot + nick + b"\x00"


def to_gen1(data: bytes, g1rom, rom=None) -> bytes | None:
    """PK4 -> 55-byte Yellow record, or None (species > 151, a move > 165, egg). Needs Yellow's RomData
    (internal species index, types, catch rate, HP formula); `rom` (Platinum data) is optional."""
    from .game import calc_stats as g1_stats
    pk = PK4(_plain_of(data)[:BOX_SIZE])
    if not pk.checksum_ok() or pk.is_egg or not 1 <= pk.species <= 151:
        return None
    if any(m > G1_MAX_MOVE for m in pk.moves):
        return None
    species = g1rom.species_for_dex(pk.species)
    base = g1rom.base_stats(species) if species else None
    if not species or base is None:
        return None
    rec2 = to_gen2(data, rom=rom if rom is not None else _G1Growth(g1rom))
    if rec2 is None:
        return None
    level = rec2[31]
    d = bytearray(33)
    d[0] = species
    d[3] = level
    d[5], d[6] = base[5], base[6]
    d[7] = pk.g1_catch_rate or g1rom.catch_rate(species)
    d[8:12] = rec2[2:6]
    d[12:14] = rec2[6:8]
    d[14:17] = rec2[8:11]
    d[17:27] = rec2[11:21]
    d[27:29] = rec2[21:23]
    for i, m in enumerate(d[8:12]):
        b = rec2[23 + i]
        if m and g1rom.move_pp(m):
            bp = g1rom.move_pp(m)
            b = (b & 0xC0) | min(b & 0x3F, bp + min(bp // 5, 7) * (b >> 6))
        d[29 + i] = b
    party = bytearray(44)                       # game.calc_stats works on the party layout (level at 33)
    party[:33] = d
    party[33] = level
    hp = g1_stats(bytes(party), base)[0]
    d[1], d[2] = hp >> 8, hp & 0xFF
    nick = rec2[43:54] if pk.has_nickname else g1rom.species_name_raw(species)
    ot = _g4_to_old(pk.ot_codes, 11, 0x00)
    return bytes(d) + ot + bytes(nick)


class _G1Growth:
    """Adapter: growth rate by dex from Yellow's RomData (for to_gen2 inside to_gen1 without Platinum data)."""

    def __init__(self, g1rom):
        self.g1 = g1rom

    def growth_rate(self, dex):
        return self.g1.growth_rate(self.g1.species_for_dex(dex))


# _Growth treats any object with growth_rate(dex) as the Platinum-style source
# ---------------------------------------------------------------------------------------------------------
# store helpers (PC database, SPEC.md section 5)
# ---------------------------------------------------------------------------------------------------------
def mon_key(data: bytes) -> str:
    """Identity of a Gen-4 mon (box or party, encrypted or plain): PID + TID + SID as 16 hex digits. None of
    these change on level-up, evolution, deposit/withdraw or renaming; converted mons get a PID derived from
    OT ID + DVs (+ Gen-2 caught data), so one old record always maps to the same key."""
    p = _plain_of(bytes(data))
    return "%08x%04x%04x" % (struct.unpack_from("<I", p, 0)[0], struct.unpack_from("<H", p, A + 4)[0],
                             struct.unpack_from("<H", p, A + 6)[0])


def entry_from_pk4(data: bytes, rom, origin: str = "platinum", entry_id: str | None = None) -> dict:
    """Store entry for a Gen-4 mon (box or party bytes, encrypted). gen4 = the 136 encrypted box bytes."""
    data = bytes(data)
    box = data[:BOX_SIZE]
    pk = PK4(decrypt(box))
    level = pk.level(rom) if 1 <= pk.species <= NUM_SPECIES else 1
    nick = pk.nickname if not pk.is_egg else "EGG"
    return {"id": entry_id or uuid.uuid4().hex, "origin": origin, "dex": pk.species, "level": level,
            "nick": nick, "ot": pk.ot_name, "otid": pk.tid,
            "gen1": None, "gen2": None, "gen4": _b64(box)}


def standin(entry: dict, rom=None) -> bytes:
    """136-byte encrypted stand-in for a mon Platinum can't hold: Ditto named ?NAME (first 9 letters), OT YLINK,
    TID/SID 0, Transform. PID derived from the entry id (stable per entry, never shiny)."""
    level = max(2, min(100, int(entry.get("level") or 5)))
    pk = PK4()
    h = hashlib.sha1(("yl-standin-" + str(entry.get("id", ""))).encode()).digest()
    pid = struct.unpack("<I", h[:4])[0]
    while is_shiny(pid, 0, 0):
        pid = (pid * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
    pk.pid = pid
    pk.species = DITTO
    pk.exp = level ** 3                                  # Ditto: medium fast
    pk.friendship = 70
    ab = rom.abilities(DITTO) if rom is not None else (LIMBER, 0)
    pk.ability = ab[0] or LIMBER
    pk.language = LANG_ENGLISH
    pk.moves = [TRANSFORM]
    pk.pp = [10]
    pk.gender_bits = GENDER_NONE
    name = ("?" + (entry.get("nick") or "POKEMON").upper())[:10]
    codes = g4_encode(name)
    pk.nickname_codes = codes + [0] * (11 - len(codes))
    pk.has_nickname = True
    pk.origin_game = VERSION_PLATINUM
    codes = g4_encode(STANDIN_OT)
    pk.ot_codes = codes + [0] * (8 - len(codes))
    pk.met_date = MET_DATE
    pk.met_location = pk.met_location_pt = LOC_PAL_PARK
    pk.ball = BALL_POKE
    pk.met_level = level
    return pk.box_bytes()


def is_standin(data: bytes) -> bool:
    try:
        pk = PK4(_plain_of(bytes(data))[:BOX_SIZE])
    except ValueError:
        return False
    return pk.species == DITTO and pk.tid == 0 and pk.sid == 0 and pk.ot_name == STANDIN_OT


def record_for(entry: dict, rom, g1rom=None) -> bytes:
    """136 encrypted bytes to show in Platinum's PC: own Gen-4 data, else a conversion of the Gen-2 or Gen-1
    data, else a stand-in. (A Gen-1 conversion needs entry["dex"] or Yellow's RomData.)"""
    if entry.get("gen4"):
        return _unb64(entry["gen4"])
    rec = None
    if entry.get("gen2"):
        rec = from_gen2(_unb64(entry["gen2"]), rom)
    if rec is None and entry.get("gen1"):
        rec = from_gen1(_unb64(entry["gen1"]), rom, dex=int(entry.get("dex") or 0) or None, g1rom=g1rom)
    return rec if rec is not None else standin(entry, rom)


def gen1_record(entry: dict, g1rom, rom=None) -> bytes | None:
    """For Yellow's codec: the entry's Gen-1 bytes, else a conversion of its Gen-4 data (None if impossible).
    (Gen 2 -> Gen 1 stays in pcdb.gen2_to_gen1.)"""
    if entry.get("gen1"):
        return _unb64(entry["gen1"])
    if entry.get("gen4"):
        return to_gen1(_unb64(entry["gen4"]), g1rom, rom)
    return None


def gen2_record(entry: dict, rom=None, g2rom=None) -> bytes | None:
    """For Crystal's codec: the entry's Gen-2 bytes, else a conversion of its Gen-4 data (None if impossible)."""
    if entry.get("gen2"):
        return _unb64(entry["gen2"])
    if entry.get("gen4"):
        return to_gen2(_unb64(entry["gen4"]), rom=rom, g2rom=g2rom)
    return None
