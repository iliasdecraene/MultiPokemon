"""Unit tests for badges.py (pure rules) and the adapters' progress()/wild_is_shiny()/champion() on fake memory.
Real-ROM checks: dev/badges/verify.py."""
import base64
import json
import os
import struct
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from yellowlink import badges, challenge_view, crystal, game, pcdb, pk4, text  # noqa: E402
from yellowlink import platinum as P  # noqa: E402
from yellowlink.ndsrom import g4_encode  # noqa: E402

SHINY = bytes([0xEA, 0xAA])
PLAIN = bytes([0x98, 0x88])
PRET_PT = os.environ.get("YL_PRET_PLATINUM", "/home/claude/pokeplatinum")


# ---------------------------------------------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------------------------------------------
def test_regions():
    assert badges.REGIONS == ("kanto", "johto", "sinnoh")
    assert badges.REGION_OF_GAME == {"yellow": "kanto", "crystal": "johto", "platinum": "sinnoh"}
    assert all(badges.GAME_OF_REGION[badges.REGION_OF_GAME[g]] == g for g in badges.REGION_OF_GAME)


def test_regional_dex_sizes():
    assert len(badges.KANTO_DEX) == 150 and len(set(badges.KANTO_DEX)) == 150
    assert badges.KANTO_DEX == tuple(range(1, 151)) and badges.MEW not in badges.KANTO_DEX
    assert len(badges.JOHTO_DEX) == 249 and len(set(badges.JOHTO_DEX)) == 249
    assert badges.MEW not in badges.JOHTO_DEX and badges.CELEBI not in badges.JOHTO_DEX
    assert set(badges.JOHTO_DEX) == set(range(1, 252)) - {151, 251}
    assert len(badges.SINNOH_DEX) == 210 and len(set(badges.SINNOH_DEX)) == 210
    assert all(1 <= d <= 493 for d in badges.SINNOH_DEX)
    assert badges.SINNOH_DEX[:3] == (387, 388, 389) and badges.SINNOH_DEX[-1] == 487     # Turtwig .. Giratina
    assert 493 not in badges.SINNOH_DEX                                                  # Arceus isn't in it
    assert badges.DEX_TOTAL == {"kanto": 150, "johto": 249, "sinnoh": 210 - len(badges.SINNOH_DEX_EXCLUDED)}


@pytest.mark.skipif(not os.path.exists(os.path.join(PRET_PT, "res", "pokemon", "sinnoh_pokedex.json")),
                    reason="pret/pokeplatinum checkout not available")
def test_sinnoh_dex_matches_pret():
    names = [ln.strip() for ln in open(os.path.join(PRET_PT, "generated", "species.txt")) if ln.strip()]
    idx = {n: i for i, n in enumerate(names)}
    src = json.load(open(os.path.join(PRET_PT, "res", "pokemon", "sinnoh_pokedex.json")))
    skip = ("SPECIES_NONE", "SPECIES_ARCEUS", "SPECIES_EGG", "SPECIES_BAD_EGG")   # speciesproc.c LoadSinnohDex
    assert tuple(idx[n] for n in src if n not in skip) == badges.SINNOH_DEX


def test_gen1_shiny_rule_matches_gen2_everywhere():
    shiny = []
    for v in range(0x10000):
        b = bytes([v >> 8, v & 0xFF])
        s = badges.gen1_is_shiny(b)
        assert s == badges.gen1_is_shiny(v) == crystal.is_shiny(v) == pk4._g2_shiny(v)
        if s:
            shiny.append(v)
    assert len(shiny) == 8
    assert {v >> 12 for v in shiny} == {2, 3, 6, 7, 10, 11, 14, 15}
    assert all(v & 0x0FFF == 0x0AAA for v in shiny)
    assert badges.gen1_is_shiny(SHINY) and not badges.gen1_is_shiny(PLAIN)
    assert not badges.gen1_is_shiny(bytes([0xAA, 0xAB])) and not badges.gen1_is_shiny(bytes([0x1A, 0xAA]))


def test_gen4_shiny():
    for pid, tid, sid in ((0x12345678, 1111, 2222), (0, 0, 7), (0, 0, 8), (0xFFFF0000, 0xFFFF, 0)):
        assert badges.gen4_is_shiny(pid, tid, sid) == pk4.is_shiny(pid, tid, sid)
    assert badges.gen4_is_shiny(0x00070000, 0, 0) and not badges.gen4_is_shiny(0x00080000, 0, 0)


def test_tiers():
    assert badges.SHINY_TIERS == (1, 10, 25, 50, 100)
    assert [badges.shiny_tier(n) for n in (0, 1, 9, 10, 24, 25, 49, 50, 99, 100, 500)] == \
        [0, 1, 1, 10, 10, 25, 25, 50, 50, 100, 100]
    assert [badges.next_shiny_tier(n) for n in (0, 1, 10, 99, 100)] == [1, 10, 25, 100, None]


def test_dex_caught_bits():
    bits = bytearray(64)
    for d in (1, 150, 151, 251, 387, 490):
        bits[(d - 1) // 8] |= 1 << ((d - 1) % 8)
    assert badges.dex_caught(bits, "kanto") == 2
    assert badges.dex_caught(bits, "johto") == 2              # 1, 150 (Mew and Celebi don't count)
    assert badges.dex_caught(bits, "sinnoh") == 2             # Turtwig, Manaphy
    assert badges.dex_caught(bytes(19), "kanto") == 0
    full = bytes([0xFF] * 19)
    p = badges.progress_dict("kanto", False, full, ["a", "a", "b"], {"ot": "X", "tid": 1})
    assert p == {"champion": False, "dex_caught": 150, "dex_total": 150, "dex_complete": True,
                 "shinies": ["a", "b"], "owner": {"ot": "X", "tid": 1}}


# ---------------------------------------------------------------------------------------------------------------
# earned state
# ---------------------------------------------------------------------------------------------------------------
def prog(champion=False, complete=False, shinies=()):
    return {"champion": champion, "dex_caught": 0, "dex_total": 150, "dex_complete": complete,
            "shinies": list(shinies)}


def test_merge_never_removes_and_reports_new():
    e = {}
    assert badges.merge(e, "kanto", None, 1.0) == []
    assert badges.merge(e, "kanto", prog(), 1.0) == [] and e == {}
    assert badges.merge(e, "kanto", prog(champion=True), 5.0) == ["champion"]
    assert badges.merge(e, "kanto", prog(champion=True), 9.0) == [] and e["kanto"]["champion"] == 5.0
    assert badges.merge(e, "kanto", prog(), 10.0) == [] and e["kanto"]["champion"] == 5.0   # new game: kept
    assert badges.merge(e, "kanto", prog(complete=True, shinies=["k1"]), 11.0) == ["dex", "shiny:1"]
    keys = ["k%d" % i for i in range(2, 10)]
    assert badges.merge(e, "kanto", prog(shinies=keys), 12.0) == []                         # 9 shinies
    assert badges.merge(e, "kanto", prog(shinies=["k1", "k10"]), 13.0) == ["shiny:10"]
    assert badges.merge(e, "kanto", prog(), 14.0) == [] and len(e["kanto"]["shinies"]) == 10  # traded away: kept
    assert e["kanto"]["dex"] == 11.0
    assert badges.merge(e, "johto", prog(shinies=["bad key!", 5, "x" * 41]), 1.0) == [] and "johto" not in e
    assert badges.merge(e, "nowhere", prog(champion=True), 1.0) == []


def test_merge_jumps_tiers_and_caps():
    e = {}
    assert badges.merge(e, "sinnoh", prog(shinies=["s%d" % i for i in range(30)]), 1.0) == ["shiny:25"]
    many = ["t%d" % i for i in range(badges.SHINY_KEEP + 50)]
    badges.merge(e, "sinnoh", prog(shinies=many), 2.0)
    assert len(e["sinnoh"]["shinies"]) == badges.SHINY_KEEP
    assert badges.shiny_tier(len(e["sinnoh"]["shinies"])) == 100


def test_soullink_gold_beats_silver():
    e = {}
    assert badges.award_soullink(e, "kanto", "silver") and e["kanto"]["soullink"] == "silver"
    assert not badges.award_soullink(e, "kanto", "silver")
    assert badges.award_soullink(e, "kanto", "gold") and e["kanto"]["soullink"] == "gold"
    assert not badges.award_soullink(e, "kanto", "silver") and e["kanto"]["soullink"] == "gold"
    assert not badges.award_soullink(e, "kanto", "bronze") and not badges.award_soullink(e, "mars", "gold")


def test_merge_earned_and_normalise():
    a = {"kanto": {"champion": 10, "shinies": ["a", "b"], "soullink": "gold"}, "johto": {"dex": 3}}
    b = {"kanto": {"champion": 5, "dex": 7, "shinies": ["b", "c"], "soullink": "silver"},
         "sinnoh": {"soullink": "silver", "junk": 1}, "mars": {"champion": 1}}
    m = badges.merge_earned(a, b)
    assert m == {"kanto": {"champion": 5, "dex": 7, "shinies": ["a", "b", "c"], "soullink": "gold"},
                 "johto": {"dex": 3}, "sinnoh": {"soullink": "silver"}}
    assert a["kanto"]["champion"] == 10                      # inputs untouched
    assert badges.normalise({"kanto": {"champion": True, "dex": -1, "shinies": ["ok", "no way"],
                                       "soullink": "platinum"}}) == {"kanto": {"shinies": ["ok"]}}
    assert badges.normalise(None) == {} and badges.normalise({"kanto": []}) == {}
    s = badges.summary(m, "kanto")
    assert s == {"champion": True, "dex": True, "shinies": 3, "shiny_tier": 1, "soullink": "gold"}
    assert badges.summary({}, "johto") == {"champion": False, "dex": False, "shinies": 0, "shiny_tier": 0,
                                           "soullink": None}


# ---------------------------------------------------------------------------------------------------------------
# records / PC-store entries
# ---------------------------------------------------------------------------------------------------------------
YOWNER = {"ot": "ASH", "tid": 0x1234}


def g1_rec(dvs=SHINY, otid=0x1234, ot="ASH", species=0x54, catch=190):
    d = bytearray(33)
    d[0], d[3], d[7] = species, 5, catch
    d[12], d[13] = otid >> 8, otid & 0xFF
    d[27:29] = dvs
    return bytes(d) + text.encode(ot, 11) + text.encode("PIKACHU", 11)


def g2_rec(dvs=SHINY, otid=0x1234, ot="ASH", caught=(0x82, 0x02), egg=False, species=25):
    d = bytearray(32)
    d[0], d[31] = species, 5
    d[6], d[7] = otid >> 8, otid & 0xFF
    d[21:23] = dvs
    d[29], d[30] = caught
    return crystal.record(bytes(d), text.encode(ot, 11), text.encode("PIKACHU", 11), egg)


def test_gen1_shiny_key():
    r = g1_rec()
    assert badges.gen1_shiny_key(r[:33], r[33:44], YOWNER) == game.mon_key(r) == pcdb.Gen1PC.key(r)
    assert badges.gen1_shiny_key(r[:33], r[33:44], {"ot": "ASH", "tid": 0x1235}) is None
    assert badges.gen1_shiny_key(r[:33], r[33:44], {"ot": "MISTY", "tid": 0x1234}) is None
    assert badges.gen1_shiny_key(r[:33], r[33:44], {"ot": "", "tid": 0x1234}) is None
    p = g1_rec(dvs=PLAIN)
    assert badges.gen1_shiny_key(p[:33], p[33:44], YOWNER) is None


def test_gen2_shiny_key():
    r = g2_rec()
    assert badges.gen2_shiny_key(r[:32], r[32:43], YOWNER) == crystal.mon_key(r) == crystal.Gen2PC.key(r)
    assert badges.gen2_shiny_key(r[:32], r[32:43], YOWNER, egg=True) is None
    z = g2_rec(caught=(0, 0))                          # converted from Gen 1 (no caught data): not a Crystal catch
    assert badges.gen2_shiny_key(z[:32], z[32:43], YOWNER) is None
    gift = g2_rec(caught=(0x00, 0x7E))                 # SetGiftMonCaughtData: level 0, LANDMARK_GIFT
    assert badges.gen2_shiny_key(gift[:32], gift[32:43], YOWNER) is not None
    o = g2_rec(ot="GOLD")
    assert badges.gen2_shiny_key(o[:32], o[32:43], YOWNER) is None


TID, SID = 33356, 2998
POWNER = {"ot": "LUCAS", "tid": TID, "sid": SID}


def shiny_pid(lo=0x51C9, tid=TID, sid=SID):
    return ((lo ^ tid ^ sid) << 16) | lo


def pt_rec(pid=None, species=387, tid=TID, sid=SID, origin=12, ot="LUCAS", egg=False):
    from test_pk4 import native
    pk = pk4.PK4(pk4.decrypt(native(species=species, pid=shiny_pid() if pid is None else pid, tid=tid, sid=sid,
                                    egg=egg)))
    pk.origin_game = origin
    pk.ot_codes = g4_encode(ot, 8)
    return pk.box_bytes()


def test_gen4_shiny_key():
    r = pt_rec()
    assert badges.gen4_shiny_key(r, POWNER) == pk4.mon_key(r) == "%08x%04x%04x" % (shiny_pid(), TID, SID)
    assert badges.gen4_shiny_key(pt_rec(pid=0x12345678), POWNER) is None
    assert badges.gen4_shiny_key(pt_rec(origin=4), POWNER) is None          # converted Gen-1 (FireRed)
    assert badges.gen4_shiny_key(pt_rec(origin=7), POWNER) is None          # converted Gen-2 (HeartGold)
    assert badges.gen4_shiny_key(pt_rec(ot="DAWN"), POWNER) is None
    assert badges.gen4_shiny_key(pt_rec(egg=True), POWNER) is None
    other = pt_rec(pid=shiny_pid(sid=SID ^ 4), sid=SID ^ 4)
    assert badges.gen4_shiny_key(other, POWNER) is None                     # same TID, other SID
    assert badges.gen4_shiny_key(b"\x00" * 136, POWNER) is None
    assert badges.gen4_shiny_key(pk4.EMPTY_BOX, POWNER) is None


def b64(b):
    return base64.b64encode(bytes(b)).decode()


def test_entries():
    e1 = {"id": "1", "origin": "yellow", "gen1": b64(g1_rec()), "gen2": None}
    e2 = {"id": "2", "origin": "crystal", "gen1": None, "gen2": b64(g2_rec())}
    e4 = {"id": "4", "origin": "platinum", "gen1": None, "gen2": None, "gen4": b64(pt_rec())}
    plain = {"id": "5", "origin": "yellow", "gen1": b64(g1_rec(dvs=PLAIN))}
    assert badges.entry_is_shiny(e1) and badges.entry_is_shiny(e2) and badges.entry_is_shiny(e4)
    assert not badges.entry_is_shiny(plain) and not badges.entry_is_shiny({"id": "x"})
    assert not badges.entry_is_shiny({"id": "y", "gen4": b64(pt_rec(pid=0x12345678))})
    assert badges.entry_shiny_key(e1, "kanto", YOWNER) == game.mon_key(g1_rec())
    assert badges.entry_shiny_key(e2, "johto", YOWNER) == crystal.mon_key(g2_rec())
    assert badges.entry_shiny_key(e4, "sinnoh", POWNER) == pk4.mon_key(pt_rec())
    assert badges.entry_shiny_key(e1, "johto", YOWNER) is None              # no gen2 representation
    assert badges.entry_shiny_key(e1, "kanto", None) is None
    assert badges.entry_shiny_key(e4, "sinnoh", dict(POWNER, sid=1)) is None
    assert badges.entry_shiny_key({"id": "z", "gen1": "!!notb64"}, "kanto", YOWNER) is None
    egg = {"id": "e", "gen2": b64(g2_rec(egg=True))}
    assert badges.entry_shiny_key(egg, "johto", YOWNER) is None
    standin = pcdb.Gen1PC(None, None).standin({"id": "s", "nick": "TOGEPI", "level": 5})
    assert not badges.entry_is_shiny({"id": "s", "gen1": b64(standin)})


# ---------------------------------------------------------------------------------------------------------------
# Yellow adapter (fake memory)
# ---------------------------------------------------------------------------------------------------------------
class GBMem:
    """Flat 64 KB + banked SRAM, indexable like pyboy.memory (mem[addr], mem[bank, addr])."""

    def __init__(self):
        self.flat = bytearray(0x10000)
        self.sram = {b: bytearray(0x2000) for b in range(4)}

    def __getitem__(self, k):
        if isinstance(k, tuple):
            return self.sram[k[0]][k[1] - 0xA000]
        return self.flat[k]

    def __setitem__(self, k, v):
        if isinstance(k, tuple):
            self.sram[k[0]][k[1] - 0xA000] = v
        else:
            self.flat[k] = v


class FakeYRom:
    def dex_no(self, species):
        return {0x54: 25, 0x24: 16, 0x4C: 132}.get(species, 0)


def yellow_world(party=()):
    m = GBMem()
    gm = game.GameMemory(m, FakeYRom())
    m[game.PLAYER_PIC] = 1
    gm.write(game.PLAYER_NAME, text.encode("ASH", 11))
    m[game.PLAYER_ID], m[game.PLAYER_ID + 1] = 0x12, 0x34
    m[game.PARTY_COUNT] = len(party)
    for i, rec in enumerate(party):
        d = bytearray(44)
        d[:33] = rec[:33]
        d[33] = 5
        m[game.PARTY_SPECIES + i] = d[0]
        gm.write(game.PARTY_MONS + 44 * i, bytes(d))
        gm.write(game.PARTY_OT + 11 * i, rec[33:44])
        gm.write(game.PARTY_NICKS + 11 * i, rec[44:55])
    m[game.PARTY_SPECIES + len(party)] = 0xFF
    m[game.BOX_SPECIES] = 0xFF
    return m, gm


def test_yellow_progress():
    own, other, plain = g1_rec(), g1_rec(ot="GARY", otid=0x4321), g1_rec(dvs=PLAIN)
    m, gm = yellow_world([own, other, plain])
    for d in (25, 16, 151):
        m[game.DEX_OWNED + (d - 1) // 8] |= 1 << ((d - 1) % 8)
    p = game.progress(gm)
    assert p["shinies"] == [game.mon_key(own)] and p["dex_caught"] == 2 and not p["champion"]
    assert p["owner"] == {"ot": "ASH", "tid": 0x1234} and p["dex_total"] == 150
    m[game.NUM_HOF_TEAMS] = 1
    assert game.progress(gm)["champion"] and game.champion(gm)
    # current box (WRAM) + Day Care
    box_mon = g1_rec(dvs=bytes([0x2A, 0xAA]))
    m[game.BOX_COUNT], m[game.BOX_SPECIES], m[game.BOX_SPECIES + 1] = 1, box_mon[0], 0xFF
    gm.write(game.BOX_MONS, box_mon[:33])
    gm.write(game.BOX_OT, box_mon[33:44])
    day = g1_rec(dvs=bytes([0x6A, 0xAA]))
    m[game.DAYCARE_IN_USE] = 1
    gm.write(game.DAYCARE_MON, day[:33])
    gm.write(game.DAYCARE_MON_OT, day[33:44])
    assert game.progress(gm)["shinies"] == [game.mon_key(own), game.mon_key(box_mon), game.mon_key(day)]
    m[game.PARTY_SPECIES + 3] = 0x12                      # half-written party
    assert game.progress(gm) is None
    m[game.PARTY_SPECIES + 3] = 0xFF
    m[game.PLAYER_PIC] = 0                                # title screen
    assert game.progress(gm) is None


def test_yellow_sram_boxes():
    m, gm = yellow_world([g1_rec(dvs=PLAIN)])
    pc = pcdb.Gen1PC(m, None)
    m[pcdb.G1_CUR_BOX] = 0x80                            # boxes initialised, box 1 current
    boxes = [[] for _ in range(12)]
    boxes[7] = [g1_rec(dvs=bytes([0xEA, 0xAA])), g1_rec(otid=0x9999)]
    pc.write_boxes(boxes)
    assert game.progress(gm)["shinies"] == [game.mon_key(boxes[7][0])]


def test_yellow_wild_is_shiny():
    m, gm = yellow_world([g1_rec(dvs=PLAIN)])
    assert game.wild_is_shiny(gm) is None
    m[game.IS_IN_BATTLE], m[game.ENEMY_MON_SPECIES2] = 1, 0x24
    gm.write(game.ENEMY_MON_DVS, PLAIN)
    assert game.wild_is_shiny(gm) is False
    gm.write(game.ENEMY_MON_DVS, SHINY)
    assert game.wild_is_shiny(gm) is True
    m[game.BATTLE_TYPE] = game.BATTLE_TYPE_SAFARI
    assert game.wild_is_shiny(gm) is True
    m[game.BATTLE_TYPE] = game.BATTLE_TYPE_OLD_MAN                 # not catchable
    assert game.wild_is_shiny(gm) is None
    m[game.BATTLE_TYPE] = game.BATTLE_TYPE_NORMAL
    # transformed: the copied DVs don't count, the original ones do
    m[game.ENEMY_BATTLE_STATUS3] = 1 << game.TRANSFORMED_BIT
    gm.write(game.TRANSFORMED_ENEMY_ORIG_DVS, PLAIN)
    assert game.wild_is_shiny(gm) is False
    gm.write(game.TRANSFORMED_ENEMY_ORIG_DVS, SHINY)
    gm.write(game.ENEMY_MON_DVS, PLAIN)
    assert game.wild_is_shiny(gm) is True
    m[game.IS_IN_BATTLE] = 2                                       # trainer battle
    assert game.wild_is_shiny(gm) is None


# ---------------------------------------------------------------------------------------------------------------
# Crystal adapter (fake memory with WRAM banking)
# ---------------------------------------------------------------------------------------------------------------
def crystal_world(party=()):
    from test_crystal import FakeMem
    mem = FakeMem()
    cm = crystal.CrystalMemory(mem)
    cm.w(crystal.GAME_TIMER, 1)
    cm.w(crystal.MAP_GROUP, 24)
    cm.w(crystal.MAP_NUMBER, 3)
    cm.w(crystal.PLAYER_SPRITE, 1)
    cm.write(crystal.PLAYER_NAME, text.encode("ASH", 11))
    cm.write(crystal.PLAYER_ID, bytes([0x12, 0x34]))
    cm.w(crystal.PARTY_COUNT, len(party))
    for i, rec in enumerate(party):
        d = bytearray(48)
        d[:32] = rec[:32]
        d[31] = 5
        cm.w(crystal.PARTY_SPECIES + i, crystal.EGG if rec[54] else d[0])
        cm.write(crystal.PARTY_MONS + 48 * i, bytes(d))
        cm.write(crystal.PARTY_OT + 11 * i, rec[32:43])
        cm.write(crystal.PARTY_NICKS + 11 * i, rec[43:54])
    cm.w(crystal.PARTY_SPECIES + len(party), 0xFF)
    mem[1, crystal.SBOX_SPECIES] = 0xFF
    return mem, cm


def test_crystal_progress():
    own, egg, gen1 = g2_rec(), g2_rec(dvs=bytes([0x2A, 0xAA]), egg=True), g2_rec(dvs=bytes([0x3A, 0xAA]), caught=(0, 0))
    mem, cm = crystal_world([own, egg, gen1])
    for d in (25, 151, 251, 1):
        a = crystal.DEX_CAUGHT + (d - 1) // 8
        cm.w(a, cm.r(a) | 1 << ((d - 1) % 8))
    p = crystal.progress(cm)
    assert p["shinies"] == [crystal.mon_key(own)] and p["dex_caught"] == 2 and p["dex_total"] == 249
    assert p["owner"] == {"ot": "ASH", "tid": 0x1234} and not p["champion"]
    # Day Care (lady's slot) + the live current box (sBox)
    day = g2_rec(dvs=bytes([0x6A, 0xAA]))
    cm.w(crystal.DAYCARE_LADY, 1)
    cm.write(crystal.BREED_MON2_NICK, day[43:54])
    cm.write(crystal.BREED_MON2_NICK + 11, day[32:43])
    cm.write(crystal.BREED_MON2_NICK + 22, day[:32])
    box = g2_rec(dvs=bytes([0xAA, 0xAA]))
    mem[1, crystal.SBOX_COUNT] = 1
    mem[1, crystal.SBOX_SPECIES], mem[1, crystal.SBOX_SPECIES + 1] = box[0], 0xFF
    for i, v in enumerate(box[:32]):
        mem[1, crystal.SBOX_MONS + i] = v
    for i, v in enumerate(box[32:43]):
        mem[1, crystal.SBOX_OTS + i] = v
    assert crystal.progress(cm)["shinies"] == [crystal.mon_key(own), crystal.mon_key(box), crystal.mon_key(day)]
    for addr, val in ((crystal.HALL_OF_FAME_COUNT, 1), (crystal.STATUS_FLAGS, 0x40),
                      (crystal.EVENT_FLAGS + crystal.EVENT_BEAT_ELITE_FOUR // 8, 1 << (crystal.EVENT_BEAT_ELITE_FOUR % 8))):
        cm.w(addr, val)
        assert crystal.champion(cm) and crystal.progress(cm)["champion"]
        cm.w(addr, 0)
        assert not crystal.champion(cm)
    cm.w(crystal.PARTY_SPECIES + 3, 0x10)                 # half-written
    assert crystal.progress(cm) is None
    cm.w(crystal.PARTY_SPECIES + 3, 0xFF)
    cm.w(crystal.GAME_TIMER, 0)
    assert crystal.progress(cm) is None


# ---------------------------------------------------------------------------------------------------------------
# Platinum adapter (fake bus, see test_soullink_platinum)
# ---------------------------------------------------------------------------------------------------------------
def platinum_world(party_boxes=()):
    from test_soullink_platinum import world
    from test_pk4 import ROM
    party = [pk4.to_party(b, ROM) for b in party_boxes]
    bus, pm = world(party)
    body = pm.save_body()
    bus.write(body + P.TRAINER, struct.pack("<8H", *g4_encode("LUCAS", 8)))
    bus.w32(body + P.PAGE_POKEDEX, P.POKEDEX_MAGIC)
    return bus, pm


def test_platinum_progress():
    own, plain, conv = pt_rec(), pt_rec(pid=0x12345678), pt_rec(pid=shiny_pid(0x1111), origin=4)
    bus, pm = platinum_world([own, plain, conv])
    body = pm.save_body()
    assert pm.player_name() == "LUCAS"
    for d in (387, 490, 1, 151):
        a = body + P.PAGE_POKEDEX + P.POKEDEX_CAUGHT + (d - 1) // 8
        bus.w8(a, bus.r8(a) | 1 << ((d - 1) % 8))
    p = P.progress(pm)
    assert p["shinies"] == [pk4.mon_key(own)] and p["dex_caught"] == 2 and p["dex_total"] == 210
    assert p["owner"] == {"ot": "LUCAS", "tid": TID, "sid": SID} and not p["champion"]
    # a box slot + a Day Care slot; an unreadable box slot is skipped, not fatal
    boxed, day = pt_rec(pid=shiny_pid(0x2222)), pt_rec(pid=shiny_pid(0x3333), species=399)
    pm.write_box_slot(3, 4, boxed)
    bad = bytearray(boxed)
    bad[40] ^= 0xFF
    pm.write_box_slot(3, 5, bytes(bad))
    bus.write(body + P.PAGE_DAYCARE, day)
    keys = P.progress(pm)["shinies"]
    assert keys == [pk4.mon_key(own), pk4.mon_key(boxed), pk4.mon_key(day)]
    # champion proofs
    flag = body + P.PAGE_VARS_FLAGS + P.VF_FLAGS + P.FLAG_GAME_COMPLETED // 8
    bus.w8(flag, 1 << (P.FLAG_GAME_COMPLETED % 8))
    assert P.champion(pm) and P.progress(pm)["champion"]
    bus.w8(flag, 0)
    assert not P.champion(pm)
    # no Pokédex page / party mid-update -> None
    bus.w32(body + P.PAGE_POKEDEX, 0)
    assert P.progress(pm) is None
    bus.w32(body + P.PAGE_POKEDEX, P.POKEDEX_MAGIC)
    a = pm.party_addr(0)
    bus.w8(a + 50, bus.r8(a + 50) ^ 0xFF)
    assert P.progress(pm) is None


# ---------------------------------------------------------------------------------------------------------------
# Soul Link referee view
# ---------------------------------------------------------------------------------------------------------------
def test_run_champion_uses_the_adapter():
    m, gm = yellow_world([g1_rec(dvs=PLAIN)])
    app = types.SimpleNamespace(game=gm, G=game, gen2=False, ds=False, pyboy=types.SimpleNamespace(memory=m),
                                rom=FakeYRom())
    view = challenge_view.ChallengeView(app)
    assert not view.run_champion()
    m[game.NUM_HOF_TEAMS] = 1
    assert view.run_champion() and view.hof()
    broken = types.SimpleNamespace(champion=lambda g: 1 / 0)
    view.G = broken
    assert view.run_champion() is False
