"""Raid data (pools, levels, moves, shared-HP maths, spots) - no ROM needed."""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from yellowlink import raid_data as rd  # noqa: E402


def test_pools_shape_and_games():
    assert set(rd.POOLS) == {"kanto", "johto", "sinnoh"}
    for region, pool in rd.POOLS.items():
        assert pool and all(isinstance(s, int) and w > 0 for s, w in pool)
        gen = rd.GAME_GEN[rd.REGION_GAME[region]]
        for s, _w in pool:
            assert s <= rd.MAX_SPECIES[gen], (region, s)


def test_ilias_lists():
    sinnoh = {s for s, _ in rd.POOLS["sinnoh"]}
    for s in (rd.DARKRAI, rd.SHAYMIN, rd.ARCEUS, rd.REGIROCK, rd.REGICE, rd.REGISTEEL, rd.REGIGIGAS, rd.MEWTWO,
              rd.LUGIA, rd.HO_OH, rd.RAIKOU, rd.ENTEI, rd.SUICUNE, rd.LATIAS, rd.LATIOS, rd.KYOGRE, rd.GROUDON,
              rd.RAYQUAZA, rd.MEW, rd.CELEBI, rd.JIRACHI, rd.DEOXYS, rd.MANAPHY, rd.PHIONE):
        assert s in sinnoh
    assert {s for s, _ in rd.POOLS["kanto"]} == {rd.MEW, rd.ARTICUNO, rd.ZAPDOS, rd.MOLTRES, rd.MEWTWO}
    johto = {s for s, _ in rd.POOLS["johto"]}
    assert {rd.CELEBI, rd.MEW, rd.MEWTWO, rd.LUGIA, rd.HO_OH, rd.SUICUNE, rd.RAIKOU, rd.ENTEI} <= johto


@pytest.mark.parametrize("region", ["kanto", "johto", "sinnoh"])
def test_every_boss_has_legal_raid_moves(region):
    game = rd.REGION_GAME[region]
    gen = rd.GAME_GEN[game]
    for s, _w in rd.POOLS[region]:
        moves = rd.moves_for(s, game)
        assert len(moves) == 4 and any(moves)
        for mv in moves:
            assert 0 <= mv <= rd.MAX_MOVE[gen], (region, s, mv)
            assert mv not in rd.BANNED_MOVES, (region, s, mv)


def test_levels():
    for s in rd.LEVELS:
        assert s not in rd.MYTHICALS
        assert 40 <= rd.LEVELS[s] <= 70
    for region, pool in rd.POOLS.items():
        for s, _w in pool:
            assert s in rd.LEVELS or s in rd.MYTHICALS, s
    assert rd.boss_level(rd.MEWTWO, 5) == 70
    assert rd.boss_level(rd.MEW, 5) == 20            # strongest + 5, at least 20
    assert rd.boss_level(rd.MEW, 40) == 45
    assert rd.boss_level(rd.MEW, 100) == 70          # at most 70
    assert rd.boss_level(rd.ARCEUS, 0) == 20


def test_boss_for_shape():
    b = rd.boss_for(rd.DARKRAI, 33, "sinnoh", shiny=True)
    assert b == {"species": rd.DARKRAI, "level": 38, "moves": rd.moves_for(rd.DARKRAI, "platinum"), "shiny": True,
                 "fateful": True, "region": "sinnoh", "game": "platinum", "kind": "mythical", "form": 0}
    b = rd.boss_for(rd.ZAPDOS, 10, "kanto")
    assert b["level"] == 50 and b["kind"] == "legendary" and not b["fateful"] and b["game"] == "yellow"
    with pytest.raises(KeyError):
        rd.boss_for(rd.DARKRAI, 10, "kanto")


def test_pick_and_shiny_rates():
    rng = random.Random(7)
    seen = {rd.pick_boss("sinnoh", rng) for _ in range(2000)}
    assert seen == {s for s, _ in rd.POOLS["sinnoh"]}
    rng = random.Random(1)
    n = sum(rd.roll_shiny(rng) for _ in range(20000))
    assert 800 < n < 1200                              # 1/20


def test_segment_maths():
    assert rd.bar_remainder(0) == 0
    assert rd.bar_remainder(1) == 1
    assert rd.bar_remainder(1000) == 1000
    assert rd.bar_remainder(1001) == 1
    assert rd.bar_remainder(4000) == 1000
    assert rd.segment(4000, 93) == 93
    assert rd.segment(1500, 46) == 23
    assert rd.segment(1, 300) == 1                      # never 0 while the pool has HP
    assert rd.segment(0, 300) == 0
    assert rd.damage_mb(46, 46) == 1000 and rd.damage_mb(2, 46) == 43


@pytest.mark.parametrize("seed", range(20))
def test_bar_meter_is_exact(seed):
    """Whatever the hits (incl. heals and over-kills), a bar taken down reports exactly its milli-bars."""
    rng = random.Random(seed)
    max_hp = rng.randint(20, 400)
    pool = rng.randint(1, 5000)
    m = rd.BarMeter()
    total = 0
    while pool > 0:
        bar = rd.bar_remainder(pool)
        hp = m.sync(pool, max_hp)
        got = 0
        while hp > 0:
            hit = rng.randint(-3, max_hp // 3 + 1)
            if hit < 0:
                hp = min(m.seg, hp - hit)          # a heal: doesn't count
                m.observe(hp)
                continue
            new = hp - hit
            zeroed = new <= 0
            _drop, mb = m.observe(max(new, 1 if zeroed else 0), zeroed)
            got += mb
            hp = 0 if zeroed else new
        assert got == bar
        pool -= got
        total += got
    assert pool == 0


def test_spots_file():
    for region in ("kanto", "johto", "sinnoh"):
        sp = rd.spots(region)
        assert len(sp) > 100, region
        for s in sp:
            assert set(s) == {"region", "map", "x", "y", "name", "reach"}
            assert 0 <= s["reach"] <= 8 and s["x"] >= 0 and s["y"] >= 0 and s["name"]
    rng = random.Random(3)
    for _ in range(50):
        s = rd.pick_spot("johto", max_reach=0, rng=rng)
        assert s["reach"] == 0
    assert rd.pick_spot("sinnoh", max_reach=-1) is None
    names = {s["name"] for s in rd.spots("sinnoh")}
    assert "Route 201" in names and "Twinleaf Town" in names
    assert {s["map"] for s in rd.spots("kanto") if s["name"] == "Route 1"} == {12}      # wCurMap ROUTE_1
    assert {s["map"] for s in rd.spots("johto") if s["name"] == "Route 29"} == {(24 << 8) | 3}
    assert {s["map"] for s in rd.spots("sinnoh") if s["name"] == "Twinleaf Town"} == {411}
