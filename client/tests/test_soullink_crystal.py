"""Unit tests for the Crystal Soul Link helpers (no ROM needed). Real-ROM checks: dev/crystal/soullink_verify.py."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import crystal, text  # noqa: E402
from yellowlink.crystal import CrystalMemory  # noqa: E402

from test_crystal import FakeMem, FakeRom, in_game_mem  # noqa: E402


# --- battle ---------------------------------------------------------------------------------------------------
def start_wild(mem, cm, species=16, dvs=0x1234, level=3, btype=0, loaded=True):
    cm.w(crystal.BATTLE_TYPE, btype)
    cm.w(crystal.BATTLE_MODE, 1)
    cm.w(crystal.ENEMY_MON_SPECIES, species)
    cm.w(crystal.ENEMY_MON_DVS, dvs >> 8)
    cm.w(crystal.ENEMY_MON_DVS + 1, dvs & 0xFF)
    cm.w(crystal.ENEMY_MON_LEVEL, level)
    mem[crystal.ENEMY_STATS + 9] = 12 if loaded else 0


def test_battle_info_none_outside_battle():
    mem, cm = in_game_mem()
    assert cm.battle_info() is None and cm.battle_kind() is None and cm.battle_party_slot() is None


def test_battle_info_wild_waits_for_enemy_load():
    mem, cm = in_game_mem()
    start_wild(mem, cm, loaded=False)
    assert cm.battle_kind() == "wild" and cm.battle_info() is None      # stale wEnemyMon must not be reported
    mem[crystal.ENEMY_STATS + 9] = 12
    assert cm.battle_info() == {"kind": "wild", "dex": 16, "shiny": False, "safari": False, "contest": False,
                                "battle_type": 0, "level": 3, "dvs": 0x1234}


@pytest.mark.parametrize("btype,kind,contest", [
    (crystal.BATTLETYPE_NORMAL, "wild", False), (crystal.BATTLETYPE_CANLOSE, "wild", False),
    (crystal.BATTLETYPE_DEBUG, "special", False), (crystal.BATTLETYPE_TUTORIAL, "special", False),
    (crystal.BATTLETYPE_FISH, "wild", False), (crystal.BATTLETYPE_ROAMING, "wild", False),
    (crystal.BATTLETYPE_CONTEST, "wild", True), (crystal.BATTLETYPE_FORCESHINY, "wild", False),
    (crystal.BATTLETYPE_TREE, "wild", False), (crystal.BATTLETYPE_TRAP, "wild", False),
    (crystal.BATTLETYPE_FORCEITEM, "wild", False), (crystal.BATTLETYPE_CELEBI, "wild", False),
    (crystal.BATTLETYPE_SUICUNE, "wild", False)])
def test_battle_types(btype, kind, contest):
    mem, cm = in_game_mem()
    start_wild(mem, cm, btype=btype)
    bi = cm.battle_info()
    assert bi["kind"] == kind and bi["contest"] is contest and bi["safari"] is False


def test_trainer_link_and_battle_tower():
    mem, cm = in_game_mem()
    cm.w(crystal.BATTLE_MODE, 2)
    cm.w(crystal.ENEMY_MON_SPECIES, 99)                 # stale from an earlier battle
    bi = cm.battle_info()
    assert bi["kind"] == "trainer" and bi["dex"] == 0 and bi["shiny"] is False
    mem[crystal.ENEMY_STATS + 8] = 1
    assert cm.battle_info()["dex"] == 99
    mem[crystal.LINK_MODE] = 1
    assert cm.battle_info()["kind"] == "special"
    mem[crystal.LINK_MODE] = 0
    mem[crystal.IN_BATTLE_TOWER_BATTLE] = 1
    assert cm.battle_kind() == "special"


@pytest.mark.parametrize("dvs,shiny", [(0xEAAA, True), (0x2AAA, True), (0xFAAA, True), (0x3AAA, True),
                                       (0xAAAA, True), (0x0AAA, False), (0xE9AA, False), (0xEAAB, False),
                                       (0xEABA, False), (0x5AAA, False), (0xFFFF, False)])
def test_shiny_rule(dvs, shiny):
    assert crystal.is_shiny(dvs) is shiny


def test_forced_shiny_red_gyarados():
    mem, cm = in_game_mem()
    start_wild(mem, cm, species=130, dvs=0xEAAA, btype=crystal.BATTLETYPE_FORCESHINY)
    assert cm.battle_info()["shiny"] is True and cm.battle_info()["kind"] == "wild"


def party_with(cm, mons, eggs=()):
    cm.set_party([crystal.Mon(m.data, m.ot, m.nick, egg=(i in eggs)) for i, m in enumerate(mons)])


def mk(species, level=5, dvs=0x9888, ot_id=1, nick=None, item=0, moves=(33,)):
    return crystal.make_mon(FakeRom(), species, level, list(moves), ot_id, "CHRIS", dvs=dvs, nick=nick, item=item)


def put_battle_mon(mem, cm, idx):
    p = cm.party()[idx]
    mem[crystal.BATTLE_MON_SPECIES] = p.species
    mem[0xC632], mem[0xC633] = p.data[21], p.data[22]
    mem[0xC639] = p.level
    mem[crystal.BATTLE_MON_HP], mem[crystal.BATTLE_MON_HP + 1] = p.data[34], p.data[35]
    mem[crystal.BATTLE_MON_MAX_HP], mem[crystal.BATTLE_MON_MAX_HP + 1] = p.data[36], p.data[37]
    cm.w(crystal.CUR_BATTLE_MON, idx)


def test_battle_party_slot():
    mem, cm = in_game_mem()
    party_with(cm, [mk(155), mk(16, dvs=0x1111)])
    start_wild(mem, cm)
    assert cm.battle_party_slot() is None                # ClearBattleRAM zeroed wBattleMon, not sent out yet
    put_battle_mon(mem, cm, 0)
    assert cm.battle_party_slot() == (0, cm.party()[0].hp)
    mem[crystal.BATTLE_MON_HP], mem[crystal.BATTLE_MON_HP + 1] = 0, 0
    assert cm.battle_party_slot() == (0, 0)              # fainted (party struct HP follows ~300+ frames later)
    cm.w(crystal.CUR_BATTLE_MON, 1)                      # switch in progress: index updated, struct not yet
    assert cm.battle_party_slot() is None
    put_battle_mon(mem, cm, 1)
    assert cm.battle_party_slot() == (1, cm.party()[1].hp)


def test_battle_party_slot_transformed():
    mem, cm = in_game_mem()
    party_with(cm, [mk(132)])
    start_wild(mem, cm)
    put_battle_mon(mem, cm, 0)
    mem[crystal.BATTLE_MON_SPECIES] = 16                 # Transform copies species + DVs, keeps level/HP/max HP
    mem[0xC632] = 0xAB
    assert cm.battle_party_slot() is None
    mem[0xC66C] = 0x08                                   # SUBSTATUS_TRANSFORMED
    assert cm.battle_party_slot() == (0, cm.party()[0].hp)


# --- bag / hall of fame -----------------------------------------------------------------------------------------
def test_has_poke_balls():
    mem, cm = in_game_mem()
    cm.w(crystal.NUM_BALLS, 0)
    cm.w(crystal.BALLS, 0xFF)
    assert not cm.has_poke_balls()
    for item in crystal.BALL_ITEMS:
        cm.w(crystal.NUM_BALLS, 1)
        cm.w(crystal.BALLS, item)
        cm.w(crystal.BALLS + 1, 3)
        assert cm.has_poke_balls(), item
    cm.w(crystal.BALLS, 0x12)                            # not a ball
    assert not cm.has_poke_balls()
    cm.w(crystal.NUM_BALLS, 200)                         # garbage count
    assert cm.balls() == [] and not cm.has_poke_balls()
    cm.w(crystal.PARK_BALLS_REMAINING, 20)
    assert cm.park_balls_remaining() == 20


def test_hall_of_fame():
    mem, cm = in_game_mem()
    assert not cm.in_hall_of_fame() and not cm.entered_hall_of_fame()
    cm.w(crystal.MAP_GROUP, crystal.HALL_OF_FAME_MAP >> 8)
    cm.w(crystal.MAP_NUMBER, crystal.HALL_OF_FAME_MAP & 0xFF)
    assert cm.in_hall_of_fame()
    for addr, val in ((crystal.EVENT_FLAGS + 8, 0x10), (crystal.STATUS_FLAGS, 0x40), (crystal.HALL_OF_FAME_COUNT, 1)):
        mem2, cm2 = in_game_mem()
        cm2.w(addr, val)
        assert cm2.entered_hall_of_fame(), hex(addr)


# --- forced box moves -------------------------------------------------------------------------------------------
def test_move_party_to_box_and_back():
    mem, cm = in_game_mem()
    a, b, c = mk(155, nick="EMBER"), mk(16, dvs=0x1111), mk(163, dvs=0x2222, moves=(33, 45))
    d = bytearray(c.data)
    d[23] = 0x40 | 3                                     # 1 PP Up, 3 PP left
    d[32] = 0x08                                         # poisoned
    party_with(cm, [a, b, crystal.Mon(bytes(d), c.ot, c.nick)])
    mem.sram[1][crystal.SBOX_COUNT - 0xA000] = 0
    mem.sram[1][crystal.SBOX_SPECIES - 0xA000] = 0xFF
    assert cm.move_party_to_box(2)
    recs = cm.current_box_records()
    assert len(recs) == 1 and recs[0][0] == 163 and recs[0][23] == 0x40 | (35 + 7) and recs[0][24] == 40
    assert recs[0][32:43] == c.ot and recs[0][43:54] == c.nick and recs[0][54] == 0
    assert [m.species for m in cm.party()] == [155, 16] and cm.r(crystal.PARTY_SPECIES + 2) == 0xFF
    assert crystal.mon_key(recs[0]) == crystal.mon_key(c.data)
    assert cm.move_box_to_party(0)
    back = cm.party()[2]
    assert back.species == 163 and back.data[32] == 0 and back.hp == back.max_hp and back.nick == c.nick
    assert cm.current_box_records() == []


def test_move_party_to_box_refusals():
    mem, cm = in_game_mem()
    mem.sram[1][0xD10] = 0
    party_with(cm, [mk(155)])
    assert not cm.move_party_to_box(0)                   # last mon
    egg = mk(155, dvs=0x3333)
    party_with(cm, [mk(155), egg], eggs={1})
    assert not cm.move_party_to_box(0)                   # only an egg would be left
    a, b = mk(155), mk(16)
    b0 = bytearray(b.data)
    b0[34] = b0[35] = 0
    party_with(cm, [a, crystal.Mon(bytes(b0), b.ot, b.nick)])
    assert not cm.move_party_to_box(0)                   # every other mon has fainted
    assert cm.move_party_to_box(1)                       # depositing the fainted one is fine
    party_with(cm, [mk(155), mk(16, item=0xB5)])
    assert not cm.move_party_to_box(1)                   # holds MAIL
    party_with(cm, [mk(155), mk(16)])
    mem.sram[1][0xD10] = 20
    assert not cm.move_party_to_box(1)                   # box full
    assert not cm.move_party_to_box(5)
    mem.sram[1][0xD10] = 0
    assert not cm.move_box_to_party(0)                   # empty box
    party_with(cm, [mk(155)] * 6)
    mem.sram[1][0xD10] = 1
    assert not cm.move_box_to_party(0)                   # party full


def test_egg_keeps_fd_and_zero_hp():
    mem, cm = in_game_mem()
    mem.sram[1][0xD10] = 0
    mem.sram[1][0xD11] = 0xFF
    egg = mk(16, dvs=0x4444)
    party_with(cm, [mk(155), egg], eggs={1})
    assert cm.move_party_to_box(1)
    assert mem.sram[1][0xD11] == crystal.EGG and cm.current_box_records()[0][54] == 1
    assert cm.move_box_to_party(0)
    assert cm.r(crystal.PARTY_SPECIES + 1) == crystal.EGG and cm.party()[1].egg and cm.party()[1].hp == 0


def test_withdraw_recomputes_level_from_exp():
    mem, cm = in_game_mem()
    m = bytearray(mk(16, level=5).data[:32])
    m[8:11] = crystal.exp_for_level(3, 12).to_bytes(3, "big")   # Pidgey (medium slow) has level-12 exp
    m[11:13] = (400).to_bytes(2, "big")                        # some HP stat exp
    raw = crystal.build_box([crystal.record(m, text.encode("CHRIS"), text.encode("PIDGEY"))])
    for i, v in enumerate(raw[:crystal.BOX_BYTES]):
        mem.sram[1][0xD10 + i] = v
    party_with(cm, [mk(155)])
    assert cm.move_box_to_party(0)
    p = cm.party()[1]
    assert p.level == 12
    want = crystal.calc_stats(p.data, FakeRom().base_stats(16))
    assert [(p.data[36 + 2 * i] << 8) | p.data[37 + 2 * i] for i in range(6)] == want and p.hp == want[0]


def test_remove_party_shifts_mail():
    mem, cm = in_game_mem()
    party_with(cm, [mk(155), mk(16), mk(163)])
    mem.sram[1][0xD10] = 0
    for s in range(3):
        for k in range(crystal.MAIL_STRUCT_LENGTH):
            mem.sram[0][0x600 + s * crystal.MAIL_STRUCT_LENGTH + k] = s + 1
    assert cm.move_party_to_box(0)
    mail = mem.sram[0]
    assert mail[0x600] == 2 and mail[0x600 + crystal.MAIL_STRUCT_LENGTH] == 3


def test_calc_level_and_max_pp():
    assert crystal.calc_level(bytes(8) + (0).to_bytes(3, "big"), 0) == 1
    assert crystal.calc_level(bytes(8) + (1000).to_bytes(3, "big"), 0) == 10
    assert crystal.calc_level(bytes(8) + (10 ** 6 + 5).to_bytes(3, "big"), 0) == 100
    assert crystal.max_pp(40, 3) == 61 and crystal.max_pp(35, 3) == 56 and crystal.max_pp(5, 3) == 8


# --- mon_key ------------------------------------------------------------------------------------------------------
def test_mon_key_stable_and_hatch():
    m = bytearray(mk(16).data)
    m[29], m[30] = 0x85, 0x02                            # caught at level 5 on Route 29
    k = crystal.mon_key(m)
    m2 = bytearray(m)
    m2[0], m2[31], m2[8], m2[1] = 17, 18, 9, 3           # evolved, levelled up, item
    assert crystal.mon_key(m2) == k == crystal.mon_key(bytes(m[:32]) + bytes(23))
    egg = bytearray(mk(155, dvs=0x5555).data)
    egg[29] = egg[30] = 0
    hatched = bytearray(egg)
    hatched[29], hatched[30] = 0x41, 0x82                # SetEggMonCaughtData: level 1 + time, location
    assert crystal.mon_key(egg) == crystal.mon_key(hatched)
    assert crystal.mon_key(m) != crystal.mon_key(egg)


def test_daycare_records():
    mem, cm = in_game_mem()
    assert cm.daycare_records() == []
    mon = mk(16, dvs=0x6666, nick="BIRD")
    cm.write(crystal.BREED_MON2_NICK, mon.nick)
    cm.write(crystal.BREED_MON2_NICK + 11, mon.ot)
    cm.write(crystal.BREED_MON2_NICK + 22, mon.data[:32])
    cm.w(crystal.DAYCARE_LADY, 1)
    recs = cm.daycare_records()
    assert recs == [mon.box_record()] and crystal.mon_key(recs[0]) == crystal.mon_key(mon.data)
    cm.w(crystal.DAYCARE_MAN, 0x81)
    assert len(cm.daycare_records()) == 2


# --- areas --------------------------------------------------------------------------------------------------------
def test_areas():
    assert len(crystal.AREAS) == 388
    assert crystal.area_of(24 << 8 | 3) == "route_29" and crystal.area_name("route_29") == "Route 29"
    assert crystal.area_of(24 << 8 | 5) == "new_bark_town"                         # ELMS_LAB
    assert {crystal.area_of(3 << 8 | n) for n in (37, 38, 39)} == {"union_cave"}
    assert crystal.area_name("union_cave") == "Union Cave" and crystal.area_name("mt_silver") == "Mt. Silver"
    assert crystal.area_of(3 << 8 | 49) == "team_rocket_base" and crystal.area_of(2 << 8 | 7) == "mahogany_town"
    assert crystal.area_of(crystal.HALL_OF_FAME_MAP) == "indigo_plateau"
    assert crystal.area_of(0x7777) == "map_7777"
    assert all(k in crystal.AREA_NAMES for k in crystal.AREAS.values())


def test_fake_mem_rejects_unbanked():
    """The helpers must use banked access for D000-DFFF (FakeMem raises otherwise)."""
    mem = FakeMem()
    cm = CrystalMemory(mem, FakeRom())
    cm.battle_info(), cm.has_poke_balls(), cm.entered_hall_of_fame(), cm.daycare_records()
