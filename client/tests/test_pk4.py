"""PK4 codec + Gen-1/2 <-> Gen-4 conversion tests (no ROM needed: a small fake ROM supplies species data)."""
import base64
import os
import random
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import ndsrom, pk4, text  # noqa: E402

# ---------------------------------------------------------------------------------------------------------
# fakes (made-up species data; only the shape matters)
# ---------------------------------------------------------------------------------------------------------
SPECIES = {   # dex: (growth rate, gender ratio, abilities, name, base stats)
    16: (3, 127, (51, 77), "PIDGEY", (40, 45, 40, 56, 35, 35)),
    25: (0, 127, (9, 0), "PIKACHU", (35, 55, 30, 90, 50, 40)),
    29: (3, 254, (38, 79), "NIDORAN♀", (55, 47, 52, 41, 40, 40)),
    64: (3, 63, (28, 39), "KADABRA", (40, 35, 30, 105, 120, 70)),
    65: (3, 63, (28, 39), "ALAKAZAM", (55, 50, 45, 120, 135, 95)),
    81: (0, 255, (42, 5), "MAGNEMITE", (25, 35, 70, 45, 95, 55)),
    122: (0, 127, (43, 111), "MR. MIME", (40, 45, 65, 90, 100, 120)),
    132: (0, 255, (7, 0), "DITTO", (48,) * 6),
    152: (3, 31, (65, 0), "CHIKORITA", (45, 49, 65, 45, 49, 65)),
    201: (0, 255, (26, 0), "UNOWN", (48, 72, 48, 48, 72, 48)),
    387: (3, 31, (65, 0), "TURTWIG", (55, 68, 64, 31, 45, 55)),
}


class FakeRom:
    def growth_rate(self, dex):
        return SPECIES.get(dex, (0,))[0]

    def gender_ratio(self, dex):
        return SPECIES[dex][1] if dex in SPECIES else 255

    def abilities(self, dex):
        return SPECIES[dex][2] if dex in SPECIES else (1, 0)

    def species_name(self, dex):
        return SPECIES[dex][3] if dex in SPECIES else "MON%d" % dex

    def species_name_codes(self, dex):
        return ndsrom.g4_encode(self.species_name(dex))[:-1]

    def base_stats(self, dex):
        return SPECIES[dex][4] if dex in SPECIES else (50,) * 6

    def base_friendship(self, dex):
        return 70

    def move_pp(self, move):
        return {1: 35, 33: 35, 45: 40, 84: 30, 144: 10, 165: 1, 200: 10, 300: 15}.get(move, 20 if move else 0)

    def exp_for_level(self, dex, level):
        return pk4.exp_for(self.growth_rate(dex), level)

    def level_for_exp(self, dex, exp):
        return pk4._level_from(self.growth_rate(dex), exp)


G1_INDEX = {16: 0x24, 25: 0x54, 29: 0x0F, 64: 0x26, 65: 0x95, 81: 0xAD, 122: 0x2A, 132: 0x4C}


class FakeG1Rom:
    """Yellow-RomData-shaped fake: internal index <-> dex, (hp, atk, def, spd, spc, type1, type2)."""
    def dex_no(self, species):
        return next((d for d, s in G1_INDEX.items() if s == species), 0)

    def species_for_dex(self, dex):
        return G1_INDEX.get(dex, 0)

    def base_stats(self, species):
        dex = self.dex_no(species)
        if not dex:
            return None
        b = SPECIES[dex][4]
        return (b[0], b[1], b[2], b[3], b[4], 0x18, 0x18)

    def catch_rate(self, species):
        return 45

    def move_pp(self, move):
        return FakeRom().move_pp(move)

    def growth_rate(self, species):
        return SPECIES[self.dex_no(species)][0]

    def species_name_raw(self, species):
        name = SPECIES[self.dex_no(species)][3].replace(". ", ".")
        return text.encode(name[:10], 11)


class FakeG2Rom:
    def growth_rate(self, dex):
        return SPECIES.get(dex, (0,))[0]

    def move_pp(self, move):
        return FakeRom().move_pp(move)

    def species_name_raw(self, dex):
        return text.encode(SPECIES[dex][3].replace(". ", "."), 11)


ROM, G1ROM, G2ROM = FakeRom(), FakeG1Rom(), FakeG2Rom()


def gen1_rec(dex=25, level=10, nick=None, ot="ASH", otid=0x1234, dvs=0x8C3A, moves=(84, 45, 0, 0), statexp=None,
             pp_ups=(0, 0, 0, 0), catch_rate=0xA3):
    growth = SPECIES[dex][0]
    d = bytearray(33)
    d[0] = G1_INDEX[dex]
    d[3] = level
    d[5] = d[6] = 0x18
    d[7] = catch_rate
    d[8:12] = bytes(moves)
    d[12], d[13] = otid >> 8, otid & 0xFF
    d[14:17] = pk4.exp_for(growth, level).to_bytes(3, "big")
    for i, se in enumerate(statexp or (0,) * 5):
        d[17 + 2 * i:19 + 2 * i] = se.to_bytes(2, "big")
    d[27], d[28] = dvs >> 8, dvs & 0xFF
    for i, m in enumerate(moves):
        if m:
            base = ROM.move_pp(m)
            d[29 + i] = (pp_ups[i] << 6) | (base + pp_ups[i] * min(base // 5, 7))
    from yellowlink.game import calc_stats
    party = bytearray(44)
    party[:33] = d
    party[33] = level
    hp = calc_stats(bytes(party), G1ROM.base_stats(d[0]))[0]
    d[1], d[2] = hp >> 8, hp & 0xFF
    name = nick if nick is not None else SPECIES[dex][3].replace(". ", ".")
    ot_raw = text.encode(ot, 11)
    ot_raw = ot_raw[:ot_raw.index(0x50) + 1] + bytes(11 - ot_raw.index(0x50) - 1)   # Yellow pads OT with 00
    return bytes(d) + ot_raw + text.encode(name, 11)


def gen2_rec(dex=152, level=12, nick=None, ot="KRIS", otid=0x4321, dvs=0x9B5D, item=0, moves=(33, 45, 0, 0),
             statexp=None, happiness=88, pokerus=0x21, caught=(0x8C, 0x85), egg=False, pp_ups=(0, 0, 0, 0)):
    growth = SPECIES[dex][0]
    d = bytearray(32)
    d[0], d[1] = dex, item
    d[2:6] = bytes(moves)
    d[6], d[7] = otid >> 8, otid & 0xFF
    d[8:11] = pk4.exp_for(growth, level).to_bytes(3, "big")
    for i, se in enumerate(statexp or (0,) * 5):
        d[11 + 2 * i:13 + 2 * i] = se.to_bytes(2, "big")
    d[21], d[22] = dvs >> 8, dvs & 0xFF
    for i, m in enumerate(moves):
        if m:
            base = ROM.move_pp(m)
            d[23 + i] = (pp_ups[i] << 6) | (base + pp_ups[i] * min(base // 5, 7))
    d[27], d[28] = happiness, pokerus
    d[29], d[30] = caught
    d[31] = level
    name = nick if nick is not None else SPECIES[dex][3].replace(". ", ".")
    return bytes(d) + text.encode(ot, 11) + text.encode(name, 11) + bytes([1 if egg else 0])


def native(species=387, level=20, pid=0x12345678, tid=1111, sid=2222, moves=(33, 45, 0, 0), item=0, nick=None,
           ivs=(31, 20, 10, 5, 0, 17), evs=(0, 0, 0, 0, 0, 0), egg=False):
    pk = pk4.PK4()
    pk.pid, pk.species, pk.item, pk.tid, pk.sid = pid, species, item, tid, sid
    pk.exp = pk4.exp_for(ROM.growth_rate(species), level)
    pk.friendship, pk.language = 70, 2
    pk.moves = list(moves)
    pk.pp = [ROM.move_pp(m) for m in moves]
    pk.ivs, pk.evs = list(ivs), list(evs)
    pk.is_egg = egg
    name = nick or ROM.species_name(species)
    pk.nickname_codes = ndsrom.g4_encode(name, 11)
    pk.has_nickname = nick is not None
    pk.ot_codes = ndsrom.g4_encode("LUCAS", 8)
    pk.origin_game = 12
    return pk.box_bytes()


# ---------------------------------------------------------------------------------------------------------
# codec
# ---------------------------------------------------------------------------------------------------------
def test_encrypt_decrypt_roundtrip_every_shuffle():
    rnd = random.Random(7)
    for sv in range(32):
        plain = bytearray(rnd.randbytes(pk4.PARTY_SIZE))
        struct.pack_into("<I", plain, 0, (sv << 13) | rnd.randrange(1 << 13) | (rnd.randrange(1 << 14) << 18))
        struct.pack_into("<H", plain, 4, 0)
        enc = pk4.encrypt(bytes(plain))
        struct.pack_into("<H", plain, 6, pk4.checksum(plain))
        assert pk4.decrypt(enc) == bytes(plain)
        assert pk4.valid(enc) and pk4.valid(enc[:136])
        assert pk4.decrypt(enc[:136]) == bytes(plain[:136])


def test_block_order_and_lcrng_by_hand():
    pk = pk4.PK4()
    pk.pid = 1 << 13                                   # shuffle value 1: A B D C
    pk.species = 0x0102
    pk.ot_codes = [0x0201] * 8                         # block D
    enc = pk.box_bytes()
    chk = pk4.checksum(pk.plain())
    assert struct.unpack_from("<H", enc, 6)[0] == chk
    seed = (chk * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
    assert struct.unpack_from("<H", enc, 8)[0] ^ (seed >> 16) == 0x0102            # A first
    dec = pk4._crypt(enc[8:], chk)
    assert struct.unpack_from("<H", dec, 2 * 32)[0] == 0x0201                      # D stored third


def test_checksum_failure_detected():
    enc = bytearray(native())
    enc[40] ^= 0x01
    assert not pk4.valid(bytes(enc))
    assert pk4.to_gen2(bytes(enc), ROM) is None
    with pytest.raises(ValueError):
        pk4.decrypt(b"\0" * 100)


def test_field_access_and_party_section():
    pk = pk4.PK4.from_encrypted(native(nick="Turty", ivs=(1, 2, 3, 4, 5, 6), evs=(10, 20, 30, 40, 50, 60)))
    assert (pk.species, pk.tid, pk.sid, pk.pid) == (387, 1111, 2222, 0x12345678)
    assert pk.nickname == "Turty" and pk.has_nickname and pk.ot_name == "LUCAS"
    assert pk.ivs == [1, 2, 3, 4, 5, 6] and pk.evs == [10, 20, 30, 40, 50, 60]
    assert pk.level(ROM) == 20 and pk.nature == 0x12345678 % 25
    party = pk4.PK4.from_encrypted(pk4.to_party(native(), ROM))
    assert party.is_party and party.party_level == 20 and party.hp == party.stats[0] > 0
    assert pk4.valid(pk4.to_party(native(), ROM))


def test_empty_slots():
    assert pk4.EMPTY_BOX == pk4.encrypt(bytes(136)) and pk4.EMPTY_PARTY == pk4.encrypt(bytes(236))
    assert pk4.is_empty(pk4.EMPTY_BOX) and pk4.is_empty(pk4.EMPTY_PARTY) and pk4.valid(pk4.EMPTY_BOX)
    assert not pk4.is_empty(native()) and not pk4.is_empty(b"x")


def test_party_tail_is_no_mail():
    party = pk4.decrypt(pk4.to_party(native(), ROM))
    assert party[0x88:0x8C] == bytes(4) and party[0x9C:] == pk4.PARTY_TAIL and len(pk4.PARTY_TAIL) == 80


def test_gen4_text_codec():
    codes = ndsrom.g4_encode("Mr. Mime-2!", 11)
    assert len(codes) == 11 and codes[-1] == ndsrom.EOS
    assert ndsrom.g4_decode(codes) == "Mr. Mime-2"
    assert ndsrom.g4_decode([0x012B, 0x0145, 0x0121, 0x01DE, 0x01BB, ndsrom.EOS, 0x012B]) == "Aa0 ♂"


def test_stats_with_nature():
    pk = pk4.PK4.from_encrypted(native(pid=1))      # Lonely: +Atk -Def
    base = pk4.calc_stats(pk, ROM)
    pk2 = pk4.PK4.from_encrypted(native(pid=0))     # Hardy: neutral
    neutral = pk4.calc_stats(pk2, ROM)
    assert base[1] == neutral[1] * 110 // 100 and base[2] == neutral[2] * 90 // 100 and base[0] == neutral[0]


# ---------------------------------------------------------------------------------------------------------
# Gen 1 -> Gen 4
# ---------------------------------------------------------------------------------------------------------
def test_from_gen1_fields():
    rec = gen1_rec(dex=25, level=10, nick="SPARKY", statexp=(100, 0, 65535, 4, 9))
    b = pk4.from_gen1(rec, ROM, dex=25)
    assert len(b) == 136 and pk4.valid(b)
    pk = pk4.PK4.from_encrypted(b)
    assert pk.species == 25 and pk.level(ROM) == 10 and pk.exp == 1000
    assert pk.tid == 0x1234 and pk.sid == 0 and pk.ot_name == "ASH"
    assert pk.nickname == "SPARKY" and pk.has_nickname
    assert pk.moves == [84, 45, 0, 0] and pk.pp == [30, 40, 0, 0]
    atk, dfn, spd, spc = 8, 0xC, 3, 0xA
    hp = ((atk & 1) << 3) | ((dfn & 1) << 2) | ((spd & 1) << 1) | (spc & 1)
    assert pk.ivs == [2 * hp + 1, 17, 25, 7, 21, 21]
    assert pk.evs == [10, 0, 255, 2, 3, 3]
    assert pk.language == 2 and pk.ball == 4 and pk.met_location == 55 and pk.met_location_pt == 55
    assert pk.origin_game == pk4.VERSION_FIRERED and pk.met_level == 10 and pk.met_date == (0, 1, 1)
    assert pk.friendship == 70 and pk.item == 0 and pk.g1_catch_rate == 0xA3
    assert pk.ability == 9 and pk.gender_bits == pk4.gender_of(127, pk.pid)
    assert pk4.from_gen1(rec, ROM, dex=25) == b                       # deterministic
    assert pk4.from_gen1(rec, ROM, g1rom=G1ROM) == b


def test_from_gen1_not_nicknamed_takes_gen4_name():
    pk = pk4.PK4.from_encrypted(pk4.from_gen1(gen1_rec(dex=122, moves=(1, 0, 0, 0)), ROM, dex=122))
    assert pk.nickname == "MR. MIME" and not pk.has_nickname


def test_from_gen1_refuses():
    assert pk4.from_gen1(gen1_rec(), ROM, dex=None) is None            # no dex known
    assert pk4.from_gen1(gen1_rec(moves=(0, 0, 0, 0)), ROM, dex=25) is None
    bad = bytearray(gen1_rec())
    bad[8] = 166
    assert pk4.from_gen1(bytes(bad), ROM, dex=25) is None
    assert pk4.from_gen1(gen1_rec()[:40], ROM, dex=25) is None


@pytest.mark.parametrize("dvs,shiny", [(0xEAAA, True), (0x2AAA, True), (0xFAAA, True), (0x8AAA, False),
                                       (0xEAAB, False), (0x0000, False)])
def test_shininess_preserved(dvs, shiny):
    pk = pk4.PK4.from_encrypted(pk4.from_gen1(gen1_rec(dvs=dvs), ROM, dex=25))
    assert pk.shiny == shiny


@pytest.mark.parametrize("dex,ratio,atk,female", [(16, 127, 7, True), (16, 127, 8, False), (152, 31, 1, True),
                                                  (152, 31, 2, False), (64, 63, 3, True), (64, 63, 4, False)])
def test_gender_from_attack_dv(dex, ratio, atk, female):
    dvs = (atk << 12) | 0x0555
    rec = gen2_rec(dex=dex, dvs=dvs, moves=(33, 0, 0, 0)) if dex == 152 else \
        gen1_rec(dex=dex, dvs=dvs, moves=(33, 0, 0, 0))
    b = pk4.from_gen2(rec, ROM) if dex == 152 else pk4.from_gen1(rec, ROM, dex=dex)
    pk = pk4.PK4.from_encrypted(b)
    assert (pk.gender_bits == pk4.GENDER_FEMALE) == female
    assert pk4.gender_of(ratio, pk.pid) == pk.gender_bits


def test_shiny_female_together():
    pk = pk4.PK4.from_encrypted(pk4.from_gen1(gen1_rec(dex=16, dvs=0x2AAA, moves=(33, 0, 0, 0)), ROM, dex=16))
    assert pk.shiny and pk.gender_bits == pk4.GENDER_FEMALE


def test_genderless_and_ability_slot():
    pk = pk4.PK4.from_encrypted(pk4.from_gen1(gen1_rec(dex=81, moves=(33, 0, 0, 0)), ROM, dex=81))
    assert pk.gender_bits == pk4.GENDER_NONE
    assert pk.ability == (5 if pk.pid & 1 else 42)


def test_ev_cap_510_keeps_ratio():
    pk = pk4.PK4.from_encrypted(pk4.from_gen1(gen1_rec(statexp=(65535,) * 5), ROM, dex=25))
    assert sum(pk.evs) <= 510 and pk.evs == [85] * 6


def test_pp_ups_and_heal():
    rec = bytearray(gen1_rec(pp_ups=(3, 1, 0, 0)))
    rec[29] = (3 << 6) | 2                                            # nearly empty
    pk = pk4.PK4.from_encrypted(pk4.from_gen1(bytes(rec), ROM, dex=25))
    assert pk.pp_ups == [3, 1, 0, 0] and pk.pp == [30 + 18, 40 + 8, 0, 0]


# ---------------------------------------------------------------------------------------------------------
# Gen 2 -> Gen 4
# ---------------------------------------------------------------------------------------------------------
def test_from_gen2_fields():
    rec = gen2_rec(item=0x52, happiness=123, pokerus=0x13, caught=(0x48, 0x81))
    pk = pk4.PK4.from_encrypted(pk4.from_gen2(rec, ROM))
    assert pk.species == 152 and pk.item == 221 and pk.friendship == 123 and pk.pokerus == 0x13
    assert pk.met_level == 8 and pk.ot_female and pk.g2_caught == 0x8148
    assert pk.origin_game == pk4.VERSION_HEARTGOLD and pk.ot_name == "KRIS" and pk.nickname == "CHIKORITA"
    assert not pk.has_nickname and pk.g1_catch_rate == 0


def test_from_gen2_unknown_item_dropped_and_egg_refused():
    assert pk4.PK4.from_encrypted(pk4.from_gen2(gen2_rec(item=0x9E), ROM)).item == 0      # mail
    assert pk4.from_gen2(gen2_rec(egg=True), ROM) is None
    assert pk4.from_gen2(gen2_rec(dex=152, moves=(252, 0, 0, 0)), ROM) is None


def test_unown_form_from_dvs():
    rec = gen2_rec(dex=201, dvs=0x6666, moves=(237 - 200, 0, 0, 0))
    pk = pk4.PK4.from_encrypted(pk4.from_gen2(rec, ROM))
    v = ((6 & 6) << 5) | ((6 & 6) << 3) | ((6 & 6) << 1) | ((6 & 6) >> 1)
    assert pk.form == v // 10


def test_item_tables_invert():
    for g2, g4 in pk4.G2_TO_G4_ITEM.items():
        assert pk4.G2_TO_G4_ITEM[pk4.G4_TO_G2_ITEM[g4]] == g4
    assert pk4.G4_TO_G2_ITEM[251] == 0x68


# ---------------------------------------------------------------------------------------------------------
# round trips + Gen 4 -> old
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw", [{}, {"nick": "BOLT"}, {"dvs": 0xEAAA}, {"statexp": (0, 1, 4, 9, 40000)},
                                {"dex": 122, "moves": (1, 165, 0, 0)}, {"pp_ups": (2, 3, 0, 0), "level": 100},
                                {"dex": 64, "catch_rate": 0x60, "moves": (1, 0, 0, 0), "level": 31}])
def test_gen1_roundtrip_identical(kw):
    rec = gen1_rec(**kw)
    dex = kw.get("dex", 25)
    back = pk4.to_gen1(pk4.from_gen1(rec, ROM, dex=dex), G1ROM, ROM)
    assert back == rec
    back2 = pk4.to_gen1(pk4.from_gen1(rec, ROM, dex=dex), G1ROM)      # no Platinum data needed
    assert back2 == rec


def test_gen1_roundtrip_statexp_keeps_stat_bonus():
    rec = gen1_rec(statexp=(1000, 2000, 30000, 3, 5000))     # EV total 395 <= 510
    back = pk4.to_gen1(pk4.from_gen1(rec, ROM, dex=25), G1ROM, ROM)
    from yellowlink.game import calc_stats
    p1, p2 = bytearray(44), bytearray(44)
    p1[:33], p2[:33] = rec[:33], back[:33]
    p1[33] = p2[33] = 10
    base = G1ROM.base_stats(rec[0])
    assert calc_stats(bytes(p1), base) == calc_stats(bytes(p2), base)
    assert back[:17] == rec[:17] and back[27:] == rec[27:]
    assert pk4.to_gen1(pk4.from_gen1(back, ROM, dex=25), G1ROM, ROM) == back   # stable after one trip


@pytest.mark.parametrize("kw", [{}, {"nick": "LEAFY"}, {"item": 0x8F}, {"dvs": 0xEAAA}, {"caught": (0, 0)},
                                {"pokerus": 0, "happiness": 255, "pp_ups": (3, 3, 0, 0)},
                                {"statexp": (16, 25, 36, 49, 64)}, {"dex": 122, "moves": (1, 0, 0, 0)}])
def test_gen2_roundtrip_identical(kw):
    rec = gen2_rec(**kw)
    assert pk4.to_gen2(pk4.from_gen2(rec, ROM), ROM, G2ROM) == rec
    assert pk4.to_gen2(pk4.from_gen2(rec, ROM), g2rom=G2ROM) == rec    # level from Crystal's growth data


def test_gen1_to_gen2_paths_agree():
    """A Yellow mon reaches Gen 4 with the same PID/key directly or via Crystal's Time Capsule conversion."""
    from yellowlink.crystal import gen1_to_gen2
    rec = gen1_rec(dex=16, moves=(33, 0, 0, 0))
    a = pk4.from_gen1(rec, ROM, dex=16)
    b = pk4.from_gen2(gen1_to_gen2(rec, 16), ROM)
    assert pk4.mon_key(a) == pk4.mon_key(b)


def test_to_old_refusals():
    assert pk4.to_gen2(native(species=387), ROM) is None               # Sinnoh species
    assert pk4.to_gen2(native(species=152, moves=(300, 0, 0, 0)), ROM) is None
    assert pk4.to_gen2(native(species=152, egg=True), ROM) is None
    assert pk4.to_gen1(native(species=152), G1ROM, ROM) is None        # Johto species
    assert pk4.to_gen1(native(species=25, moves=(200, 0, 0, 0)), G1ROM, ROM) is None
    assert pk4.to_gen2(native(species=25, moves=(200, 0, 0, 0)), ROM) is not None
    assert pk4.to_gen2(native(species=25)) is None                     # no growth data at all


def test_native_to_gen2_and_gen1():
    b = native(species=25, level=33, nick="Zappy", ivs=(31, 30, 21, 9, 4, 25), evs=(252, 0, 6, 252, 0, 0),
               item=233, tid=777)
    r2 = pk4.to_gen2(b, ROM, G2ROM)
    assert r2[0] == 25 and r2[1] == 0x8F and r2[31] == 33 and (r2[6] << 8 | r2[7]) == 777
    assert r2[21:23] == bytes([(15 << 4) | 10, (4 << 4) | 2])
    assert int.from_bytes(r2[11:13], "big") == 252 * 252 and int.from_bytes(r2[19:21], "big") == 0
    assert text.decode(r2[43:54]) == "Zappy" and text.decode(r2[32:43]) == "LUCAS" and r2[54] == 0
    r1 = pk4.to_gen1(b, G1ROM, ROM)
    assert r1[0] == 0x54 and r1[3] == 33 and r1[7] == 45 and text.decode(r1[44:55]) == "Zappy"
    r1b = pk4.to_gen1(native(species=122, moves=(1, 0, 0, 0)), G1ROM, ROM)
    assert text.decode(r1b[44:55]) == "MR.MIME"


# ---------------------------------------------------------------------------------------------------------
# store helpers
# ---------------------------------------------------------------------------------------------------------
def test_mon_key_stable_across_evolution_and_forms():
    b = native(species=64, pid=0xCAFEBABE, tid=12, sid=34)
    key = pk4.mon_key(b)
    assert key == "cafebabe000c0022"
    pk = pk4.PK4.from_encrypted(b)
    pk.species = 65
    pk.exp += 5000
    assert pk4.mon_key(pk.box_bytes()) == key
    assert pk4.mon_key(pk4.decrypt(pk.box_bytes())) == key             # plain bytes accepted too
    assert pk4.mon_key(pk4.to_party(b, ROM)) == key


def test_entry_from_pk4():
    b = native(species=387, level=14, nick="Shelly")
    e = pk4.entry_from_pk4(pk4.to_party(b, ROM), ROM, entry_id="abc")
    assert e == {"id": "abc", "origin": "platinum", "dex": 387, "level": 14, "nick": "Shelly", "ot": "LUCAS",
                 "otid": 1111, "gen1": None, "gen2": None, "gen4": base64.b64encode(b).decode()}
    assert len(pk4.entry_from_pk4(b, ROM)["id"]) == 32


def test_standin():
    entry = {"id": "e1", "nick": "Chikorita", "level": 17, "dex": 152}
    s = pk4.standin(entry)
    assert pk4.valid(s) and pk4.is_standin(s) and pk4.is_standin(pk4.to_party(s, ROM))
    pk = pk4.PK4.from_encrypted(s)
    assert pk.nickname == "?CHIKORITA" and pk.species == 132 and pk.level(ROM) == 17 and pk.moves[0] == 144
    assert not pk.shiny and pk.ot_name == "YLINK" and pk.tid == 0 and pk.sid == 0
    assert pk4.standin(entry) == s and pk4.standin({**entry, "id": "e2"}) != s
    assert not pk4.is_standin(native())
    assert not pk4.is_standin(b"\0" * 10)


def test_record_for_precedence():
    own = native()
    assert pk4.record_for({"id": "x", "gen4": base64.b64encode(own).decode(), "gen1": "zz"}, ROM) == own
    g2 = gen2_rec()
    e2 = {"id": "y", "dex": 152, "gen1": None, "gen2": base64.b64encode(g2).decode()}
    assert pk4.record_for(e2, ROM) == pk4.from_gen2(g2, ROM)
    g1 = gen1_rec()
    e1 = {"id": "z", "dex": 25, "gen1": base64.b64encode(g1).decode(), "gen2": None}
    assert pk4.record_for(e1, ROM) == pk4.from_gen1(g1, ROM, dex=25)
    egg = {"id": "w", "dex": 152, "nick": "EGG", "level": 5, "gen2": base64.b64encode(gen2_rec(egg=True)).decode()}
    assert pk4.is_standin(pk4.record_for(egg, ROM))


def test_old_game_record_helpers():
    b = native(species=25, level=20)
    e = {"id": "q", "dex": 25, "gen1": None, "gen2": None, "gen4": base64.b64encode(b).decode()}
    assert pk4.gen1_record(e, G1ROM, ROM) == pk4.to_gen1(b, G1ROM, ROM)
    assert pk4.gen2_record(e, ROM) == pk4.to_gen2(b, ROM)
    sinnoh = {**e, "gen4": base64.b64encode(native()).decode()}
    assert pk4.gen1_record(sinnoh, G1ROM, ROM) is None and pk4.gen2_record(sinnoh, ROM) is None
    g1 = gen1_rec()
    assert pk4.gen1_record({**e, "gen1": base64.b64encode(g1).decode()}, G1ROM) == g1


def test_exp_formulas_basics():
    assert pk4.exp_for(0, 100) == 1000000 and pk4.exp_for(1, 100) == 600000 and pk4.exp_for(2, 100) == 1640000
    assert pk4.exp_for(3, 100) == 1059860 and pk4.exp_for(4, 100) == 800000 and pk4.exp_for(5, 100) == 1250000
    assert pk4.exp_for(3, 1) == 0 and pk4._level_from(3, 9) == 2 and pk4._level_from(0, 10 ** 9) == 100
