"""Unit tests for the Soul Link helpers in yellowlink.game (fake RAM/ROM, no real ROM needed).
The same behaviour is checked on the real ROM by dev/e2e/soullink_mem_yellow.py."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import game, text  # noqa: E402
from yellowlink.game import GameMemory, Mon, RomData  # noqa: E402

PIKACHU, PIDGEY, RATTATA, GASTLY, MAROWAK = 0x54, 0x24, 0xA5, 0x19, 0x91
DEX = {PIKACHU: 25, PIDGEY: 16, RATTATA: 19, GASTLY: 92, MAROWAK: 105}
# base hp, atk, def, spd, spc, type1, type2, catch rate, growth rate
BASE = {PIKACHU: (35, 55, 30, 90, 50, 0x17, 0x17, 190, 0), PIDGEY: (40, 45, 40, 56, 35, 0, 2, 255, 3),
        RATTATA: (30, 56, 35, 72, 25, 0, 0, 255, 0)}
PLAYER = "ASH"
PLAYER_ID = 0x1234


def fake_rom():
    rom = bytearray(0x100000)
    for sp, dex in DEX.items():
        rom[game.ROM_DEX_ORDER + sp - 1] = dex
    for sp, (hp, atk, df, spd, spc, t1, t2, cr, gr) in BASE.items():
        o = game.ROM_BASE_STATS + (DEX[sp] - 1) * 28
        rom[o:o + 10] = bytes([DEX[sp], hp, atk, df, spd, spc, t1, t2, cr, 0])
        rom[o + 19] = gr
    return RomData(bytes(rom))


def make_mon(species, level, dvs=b"\xa7\x5c", otid=PLAYER_ID, ot=PLAYER, nick=None, statexp=0, hp=None, rom=None):
    rom = rom or fake_rom()
    d = bytearray(44)
    d[0] = species
    base = rom.base_stats(species)
    d[5], d[6] = base[5], base[6]
    d[7] = BASE[species][7]
    d[8] = 0x21
    d[12:14] = otid.to_bytes(2, "big")
    exp = game.exp_for_level(rom.growth_rate(species), level)
    d[14:17] = exp.to_bytes(3, "big")
    for i in range(5):
        d[17 + 2 * i:19 + 2 * i] = statexp.to_bytes(2, "big")
    d[27:29] = dvs
    d[29] = 35
    d[33] = level
    stats = game.calc_stats(bytes(d), base)
    for k, v in enumerate(stats):
        d[34 + 2 * k:36 + 2 * k] = v.to_bytes(2, "big")
    cur = stats[0] if hp is None else hp
    d[1:3] = cur.to_bytes(2, "big")
    d[3] = 0 if species == PIKACHU else level        # Oak's Pikachu really has box level 0 in the party
    name = nick or {PIKACHU: "PIKACHU", PIDGEY: "PIDGEY", RATTATA: "RATTATA"}[species]
    return Mon(bytes(d), text.encode(ot), text.encode(name))


@pytest.fixture
def gm():
    mem = bytearray(0x10000)
    g = GameMemory(mem, fake_rom())
    mem[game.PLAYER_PIC] = 1
    g.write(game.PLAYER_NAME, text.encode(PLAYER))
    mem[game.PLAYER_ID], mem[game.PLAYER_ID + 1] = PLAYER_ID >> 8, PLAYER_ID & 0xFF
    mem[game.BOX_SPECIES] = 0xFF
    mem[game.PARTY_SPECIES] = 0xFF
    mem[game.NUM_BAG_ITEMS] = 0
    mem[game.BAG_ITEMS] = 0xFF
    mem[game.PIKACHU_HAPPINESS] = 90
    mem[game.PIKACHU_MOOD] = 0x80
    return g


def set_party(g, mons):
    g.m[game.PARTY_COUNT] = len(mons)
    for i, mon in enumerate(mons):
        g.write_party_slot(i, mon)
    g.m[game.PARTY_SPECIES + len(mons)] = 0xFF


def set_bag(g, items):
    g.m[game.NUM_BAG_ITEMS] = len(items)
    for i, (it, q) in enumerate(items):
        g.m[game.BAG_ITEMS + 2 * i], g.m[game.BAG_ITEMS + 2 * i + 1] = it, q
    g.m[game.BAG_ITEMS + 2 * len(items)] = 0xFF


def start_wild(g, species, battle_type=0, map_id=0x0C):
    g.m[game.CUR_MAP] = map_id
    g.m[game.BATTLE_TYPE] = battle_type
    g.m[game.ENEMY_MON_SPECIES2] = species
    g.m[game.IS_IN_BATTLE] = 1


# --- battle_info -------------------------------------------------------------------------------------------------
def test_battle_info_none_outside_battle(gm):
    assert gm.battle_info() is None
    gm.m[game.IS_IN_BATTLE] = 0xFF            # LOST_BATTLE from poison in the overworld: not a battle
    assert gm.battle_info() is None


def test_battle_info_wild(gm):
    start_wild(gm, PIDGEY)
    assert gm.battle_info() == {"kind": "wild", "dex": 16, "shiny": False, "safari": False}


def test_battle_info_safari(gm):
    start_wild(gm, PIDGEY, battle_type=game.BATTLE_TYPE_SAFARI, map_id=game.SAFARI_ZONE_CENTER)
    assert gm.battle_info() == {"kind": "wild", "dex": 16, "shiny": False, "safari": True}


@pytest.mark.parametrize("btype", [game.BATTLE_TYPE_OLD_MAN, game.BATTLE_TYPE_RUN, game.BATTLE_TYPE_PIKACHU])
def test_battle_info_special_battle_types(gm, btype):
    start_wild(gm, RATTATA, battle_type=btype)
    assert gm.battle_info()["kind"] == "special"


def test_battle_info_ghost_needs_silph_scope(gm):
    start_wild(gm, GASTLY, map_id=game.POKEMON_TOWER_1F + 2)
    assert gm.battle_info()["kind"] == "special"
    set_bag(gm, [(0x04, 3), (game.SILPH_SCOPE, 1)])
    assert gm.battle_info() == {"kind": "wild", "dex": 92, "shiny": False, "safari": False}
    gm.m[game.CUR_MAP] = 0x0C                  # Gastly outside the tower is a normal wild mon
    set_bag(gm, [])
    assert gm.battle_info()["kind"] == "wild"


def test_battle_info_marowak_always_special(gm):
    set_bag(gm, [(game.SILPH_SCOPE, 1)])
    start_wild(gm, MAROWAK, map_id=game.POKEMON_TOWER_6F)
    assert gm.battle_info() == {"kind": "special", "dex": 105, "shiny": False, "safari": False}


def test_battle_info_trainer(gm):
    gm.m[game.ENEMY_PARTY_COUNT] = 2
    gm.m[game.ENEMY_PARTY_SPECIES], gm.m[game.ENEMY_PARTY_SPECIES + 1] = RATTATA, PIDGEY
    gm.m[game.ENEMY_PARTY_SPECIES + 2] = 0xFF
    gm.m[game.ENEMY_MON_SPECIES2] = 0
    gm.m[game.ENEMY_MON_PARTY_POS] = 0xFF      # before the first send-out
    gm.m[game.BATTLE_TYPE] = game.BATTLE_TYPE_OLD_MAN   # ignored for trainers
    gm.m[game.IS_IN_BATTLE] = 2
    assert gm.battle_info() == {"kind": "trainer", "dex": 19, "shiny": False, "safari": False}
    gm.m[game.ENEMY_MON_PARTY_POS] = 1
    assert gm.battle_info()["dex"] == 16


# --- battle_party_slot ---------------------------------------------------------------------------------------------
def test_battle_party_slot(gm):
    set_party(gm, [make_mon(PIKACHU, 5), make_mon(PIDGEY, 3)])
    assert gm.battle_party_slot() is None
    start_wild(gm, RATTATA)
    gm.m[game.BATTLE_MON_SPECIES] = 0          # zeroed by InitBattleVariables until the send-out
    gm.m[game.BATTLE_MON_HP + 1] = 0           # stale HP from an earlier battle must not look like a faint
    assert gm.battle_party_slot() is None
    gm.m[game.PLAYER_MON_NUMBER] = 1
    gm.m[game.BATTLE_MON_SPECIES] = PIDGEY
    gm.m[game.BATTLE_MON_HP], gm.m[game.BATTLE_MON_HP + 1] = 0x01, 0x02
    assert gm.battle_party_slot() == (1, 0x102)
    gm.m[game.BATTLE_MON_HP] = gm.m[game.BATTLE_MON_HP + 1] = 0
    assert gm.battle_party_slot() == (1, 0)
    gm.m[game.PLAYER_MON_NUMBER] = 5           # out of party range
    assert gm.battle_party_slot() is None


@pytest.mark.parametrize("btype", [game.BATTLE_TYPE_OLD_MAN, game.BATTLE_TYPE_SAFARI, game.BATTLE_TYPE_PIKACHU])
def test_battle_party_slot_none_without_player_mon(gm, btype):
    set_party(gm, [make_mon(PIKACHU, 5)])
    start_wild(gm, RATTATA, battle_type=btype)
    gm.m[game.BATTLE_MON_SPECIES] = PIKACHU
    assert gm.battle_party_slot() is None


# --- bag -----------------------------------------------------------------------------------------------------------
def test_has_poke_balls(gm):
    assert not gm.has_poke_balls()
    set_bag(gm, [(0x14, 2), (0x0B, 1)])        # POTION, ANTIDOTE
    assert not gm.has_poke_balls()
    for ball in game.BALL_ITEMS:
        set_bag(gm, [(0x14, 2), (ball, 1)])
        assert gm.has_poke_balls()
    set_bag(gm, [(0x04, 0)])
    assert not gm.has_poke_balls()
    gm.m[game.NUM_BAG_ITEMS] = 0xFF            # garbage count
    assert gm.bag_items() == [] and not gm.has_poke_balls()


def test_safari_ball_counter_only_in_safari_zone(gm):
    gm.m[game.NUM_SAFARI_BALLS] = 30
    gm.m[game.CUR_MAP] = 0x07                  # Fuchsia City: leftover counter doesn't count
    assert not gm.has_poke_balls()
    gm.m[game.CUR_MAP] = game.SAFARI_ZONE_EAST
    assert gm.has_poke_balls()
    gm.m[game.NUM_SAFARI_BALLS] = 0
    assert not gm.has_poke_balls()


# --- Hall of Fame --------------------------------------------------------------------------------------------------
def test_hall_of_fame(gm):
    assert not gm.in_hall_of_fame() and gm.hall_of_fame_count() == 0
    gm.m[game.CUR_MAP] = game.HALL_OF_FAME_MAP
    assert gm.in_hall_of_fame()
    gm.m[game.NUM_HOF_TEAMS] = 2
    assert gm.hall_of_fame_count() == 2
    gm.m[game.PLAYER_PIC] = 0                  # title screen
    assert not gm.in_hall_of_fame()


# --- box moves -----------------------------------------------------------------------------------------------------
def test_move_party_to_box(gm):
    pika, pidgey, rattata = make_mon(PIKACHU, 5), make_mon(PIDGEY, 7, dvs=b"\x12\x34"), make_mon(RATTATA, 4)
    set_party(gm, [pika, pidgey, rattata])
    assert gm.move_party_to_box(1)
    assert [m.data for m in gm.party()] == [pika.data, rattata.data]
    assert list(gm.m[game.PARTY_SPECIES:game.PARTY_SPECIES + 3]) == [PIKACHU, RATTATA, 0xFF]
    recs = gm.current_box_records()
    assert len(recs) == 1 and len(recs[0]) == 55
    assert recs[0] == pidgey.data[:33] + pidgey.ot + pidgey.nick
    assert gm.m[game.BOX_COUNT] == 1 and list(gm.m[game.BOX_SPECIES:game.BOX_SPECIES + 2]) == [PIDGEY, 0xFF]
    assert gm.m[game.PIKACHU_HAPPINESS] == 90                 # not the starter: no happiness change


def test_move_party_to_box_sets_box_level(gm):
    pika = make_mon(PIKACHU, 5)
    assert pika.data[3] == 0
    set_party(gm, [make_mon(PIDGEY, 3), pika])
    assert gm.move_party_to_box(1)
    rec = gm.current_box_records()[0]
    assert rec[3] == 5 and rec[:3] + rec[4:33] == pika.data[:3] + pika.data[4:33]


@pytest.mark.parametrize("happy,mood,want", [(90, 0x80, (87, 0x62)), (150, 0x50, (147, 0x50)), (250, 0x90, (245, 0x62)),
                                             (2, 0x62, (0, 0x62))])
def test_starter_pikachu_deposit_happiness(gm, happy, mood, want):
    set_party(gm, [make_mon(PIDGEY, 3), make_mon(PIKACHU, 5)])
    gm.m[game.PIKACHU_HAPPINESS], gm.m[game.PIKACHU_MOOD] = happy, mood
    assert gm.move_party_to_box(1)
    assert (gm.m[game.PIKACHU_HAPPINESS], gm.m[game.PIKACHU_MOOD]) == want
    assert gm.move_box_to_party(0)                            # withdrawing doesn't change happiness
    assert (gm.m[game.PIKACHU_HAPPINESS], gm.m[game.PIKACHU_MOOD]) == want


def test_traded_pikachu_is_not_the_starter(gm):
    other = make_mon(PIKACHU, 5, otid=0x9999)
    renamed_ot = make_mon(PIKACHU, 5, ot="GARY")
    assert not gm.is_starter_pikachu(other.data, other.ot)
    assert not gm.is_starter_pikachu(renamed_ot.data, renamed_ot.ot)
    assert gm.is_starter_pikachu(make_mon(PIKACHU, 5).data, make_mon(PIKACHU, 5).ot)
    set_party(gm, [make_mon(PIDGEY, 3), other])
    assert gm.move_party_to_box(1)
    assert gm.m[game.PIKACHU_HAPPINESS] == 90


def test_move_party_to_box_refusals(gm):
    set_party(gm, [make_mon(PIKACHU, 5)])
    assert not gm.move_party_to_box(0)                        # last mon
    set_party(gm, [make_mon(PIKACHU, 5), make_mon(PIDGEY, 3)])
    assert not gm.move_party_to_box(2) and not gm.move_party_to_box(-1)
    gm.m[game.BOX_COUNT] = 20
    assert not gm.move_party_to_box(1)                        # box full
    gm.m[game.BOX_COUNT] = 0
    gm.m[game.IS_IN_BATTLE] = 1
    assert not gm.move_party_to_box(1)                        # never during a battle
    assert gm.m[game.PARTY_COUNT] == 2


def test_move_box_to_party_recalculates(gm):
    pidgey = make_mon(PIDGEY, 12, statexp=900, hp=5)
    set_party(gm, [make_mon(PIKACHU, 5), pidgey])
    assert gm.move_party_to_box(1)
    # stale stats / level in the box copy must not matter: the game recomputes them on withdraw
    assert gm.move_box_to_party(0)
    back = gm.party()[1]
    assert back.data == pidgey.data[:3] + bytes([12]) + pidgey.data[4:]
    assert back.hp == 5                                       # current HP is kept
    assert gm.current_box_records() == [] and gm.m[game.BOX_SPECIES] == 0xFF
    assert list(gm.m[game.PARTY_SPECIES:game.PARTY_SPECIES + 3]) == [PIKACHU, PIDGEY, 0xFF]


def test_move_box_to_party_level_from_exp(gm):
    pidgey = make_mon(PIDGEY, 12)
    set_party(gm, [make_mon(PIKACHU, 5), pidgey])
    gm.move_party_to_box(1)
    # exp now worth level 14 (e.g. Day Care-like growth); box level byte still says 12
    exp = game.exp_for_level(3, 14) + 5
    gm.write(game.BOX_MONS + 14, exp.to_bytes(3, "big"))
    assert gm.move_box_to_party(0)
    back = gm.party()[1]
    assert back.level == 14 and back.data[3] == 12
    assert [back.max_hp] == game.calc_stats(back.data, fake_rom().base_stats(PIDGEY))[:1]


def test_remove_box_slot_middle(gm):
    mons = [make_mon(PIDGEY, 3 + i, dvs=bytes([i, i])) for i in range(4)]
    set_party(gm, [make_mon(PIKACHU, 5)] + mons)
    for _ in range(4):
        assert gm.move_party_to_box(1)
    keys = [game.mon_key(r) for r in gm.current_box_records()]
    assert gm.move_box_to_party(1)
    assert [game.mon_key(r) for r in gm.current_box_records()] == [keys[0], keys[2], keys[3]]
    assert list(gm.m[game.BOX_SPECIES:game.BOX_SPECIES + 4]) == [PIDGEY] * 3 + [0xFF]
    assert game.mon_key(gm.party()[1].data) == keys[1]


def test_move_box_to_party_refusals(gm):
    set_party(gm, [make_mon(PIKACHU, 5), make_mon(PIDGEY, 3)])
    assert not gm.move_box_to_party(0)                        # empty box
    gm.move_party_to_box(1)
    set_party(gm, [make_mon(PIKACHU, 5)] * 6)
    assert not gm.move_box_to_party(0)                        # party full
    assert len(gm.current_box_records()) == 1


def test_current_box_records_garbage(gm):
    gm.m[game.BOX_COUNT] = 0xFF
    assert gm.current_box_records() == []


# --- Day Care ------------------------------------------------------------------------------------------------------
def test_daycare_records(gm):
    assert gm.daycare_records() == []
    pidgey = make_mon(PIDGEY, 9, dvs=b"\x77\x43")
    gm.m[game.DAYCARE_IN_USE] = 1
    rec = bytearray(pidgey.data[:33])
    rec[3] = 9
    gm.write(game.DAYCARE_MON, rec)
    gm.write(game.DAYCARE_MON_OT, pidgey.ot)
    gm.write(game.DAYCARE_MON_NAME, pidgey.nick)
    recs = gm.daycare_records()
    assert recs == [bytes(rec) + pidgey.ot + pidgey.nick]
    assert game.mon_key(recs[0]) == game.mon_key(pidgey.data)
    gm.m[game.DAYCARE_IN_USE] = 0
    assert gm.daycare_records() == []


# --- mon_key -------------------------------------------------------------------------------------------------------
def test_mon_key_stable(gm):
    rom = fake_rom()
    pika = make_mon(PIKACHU, 5, dvs=b"\x84\x9f")
    key = game.mon_key(pika.data)
    assert key == "1234849fbe"
    evolved = bytearray(pika.data)
    evolved[0] = 0x55                          # RAICHU: species/types/stats/level/exp/moves/nick may change
    evolved[1:7] = b"\x00\x30\x10\x00\x17\x17"
    evolved[8:12] = b"\x55\x56\x57\x58"
    evolved[14:27] = bytes(range(13))
    evolved[29:44] = bytes(range(15))
    assert game.mon_key(bytes(evolved)) == key
    set_party(gm, [make_mon(PIDGEY, 3), pika])
    gm.move_party_to_box(1)
    assert game.mon_key(gm.current_box_records()[0]) == key   # 55-byte record
    assert game.mon_key(gm.current_box_records()[0][:33]) == key
    gm.move_box_to_party(0)
    assert game.mon_key(gm.party()[1].data) == key
    assert game.mon_key(make_mon(PIKACHU, 5, dvs=b"\x84\x9e", rom=rom).data) != key
    assert game.mon_key(make_mon(PIKACHU, 5, dvs=b"\x84\x9f", otid=0x1235, rom=rom).data) != key


# --- experience ----------------------------------------------------------------------------------------------------
def test_exp_tables():
    assert [game.exp_for_level(0, n) for n in (2, 50, 100)] == [8, 125000, 1000000]       # medium fast
    assert game.exp_for_level(3, 100) == 1059860                                          # medium slow
    assert game.exp_for_level(4, 100) == 800000 and game.exp_for_level(5, 100) == 1250000
    assert game.exp_for_level(3, 1) == (6 // 5 - 15 + 100 - 140) & 0xFFFFFF               # the game's wrap-around
    for gr in range(6):
        for lvl in (2, 5, 36, 99, 100):
            e = game.exp_for_level(gr, lvl)
            assert game.level_from_exp(gr, e) == lvl
            assert game.level_from_exp(gr, e - 1) == lvl - 1 or lvl == 2
    assert game.level_from_exp(3, 0) == 1


# --- areas ---------------------------------------------------------------------------------------------------------
def test_areas_cover_every_map():
    assert sorted(game.AREAS) == list(range(0xF9))
    assert all(isinstance(game.area_name(k), str) and game.area_name(k) for k in game.AREAS.values())


@pytest.mark.parametrize("maps,key,name", [
    ((0x3B, 0x3C, 0x3D), "MT_MOON", "Mt. Moon"),
    ((0x52, 0xE8), "ROCK_TUNNEL", "Rock Tunnel"),
    ((0x9F, 0xA0, 0xA1, 0xA2, 0xC0), "SEAFOAM_ISLANDS", "Seafoam Islands"),
    (tuple(range(0x8E, 0x95)), "POKEMON_TOWER", "Pokémon Tower"),
    ((0x6C, 0xC2, 0xC6), "VICTORY_ROAD", "Victory Road"),
    ((0xE2, 0xE3, 0xE4), "CERULEAN_CAVE", "Cerulean Cave"),
    ((0xB5, 0xCF, 0xD5, 0xE9, 0xEB, 0xEC), "SILPH_CO", "Silph Co."),
    ((0xA5, 0xD6, 0xD7, 0xD8), "POKEMON_MANSION", "Pokémon Mansion"),
    ((0x53,), "POWER_PLANT", "Power Plant"),
    ((0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xE1), "SAFARI_ZONE", "Safari Zone"),
    ((0x00, 0x25, 0x26, 0x27, 0x28), "PALLET_TOWN", "Pallet Town"),
    ((0x0D, 0x2E, 0x2F, 0x30, 0x31, 0x32), "ROUTE_2", "Route 2"),
    ((0x06, 0x87, 0x89), "CELADON_CITY", "Celadon City"),
    ((0x0C,), "ROUTE_1", "Route 1"),
    ((0x33,), "VIRIDIAN_FOREST", "Viridian Forest"),
    ((0x51, 0x15), "ROUTE_10", "Route 10"),
])
def test_area_groups(maps, key, name):
    assert {game.area_of(i) for i in maps} == {key}
    assert game.area_name(key) == name


def test_area_unknown_map():
    assert game.area_of(0xFF) == "MAP_FF"
    assert game.area_name("MAP_FF") == "Map Ff"
    assert game.area_name(game.area_of(0x69)).startswith("Unused map")
