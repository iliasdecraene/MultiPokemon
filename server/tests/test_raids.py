"""Raids (SPEC.md section 12): scheduler, joins, the group HP pool in milli-bars, damage shares, the catch roll,
the end of a raid, busy players and persistence across a restart."""
import asyncio
import json
import os
import random

import pytest

from yellowlink_server import raids as R
from yellowlink_server.config import Config, load_config
from yellowlink_server.server import YellowLinkServer

from conftest import KEY

SPOTS = {
    "kanto": {"names": {"12": "Route 1", "3": "Cerulean City"},
              "spots": [[12, 5, 7, 0], [12, 6, 7, 0], [3, 10, 10, 2], [3, 11, 10, 8]]},
    "johto": {"names": {"6170": "Route 29"}, "spots": [[6170, 4, 4, 0]]},
    "sinnoh": {"names": {"400": "Route 210", "411": "Twinleaf Town"},
               "spots": [[400, 20, 30, 5], [411, 3, 3, 0]]},
}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FixedRng(random.Random):
    """random() always returns `value` (the catch roll); everything else is a normal seeded Random."""

    def __init__(self, value):
        super().__init__(1)
        self.value = value

    def random(self):
        return self.value


@pytest.fixture
def hub(server):
    h = server.raids
    h.auto = False                       # tests drive the scheduler themselves
    h.clock = Clock()
    h.spots = json.loads(json.dumps(SPOTS))
    h.update_every = 0.02
    h.salt = "test-salt"
    return h


async def raider(connect_client, name, game="yellow", reach=None, state=True):
    c = await connect_client(name)
    await c.send(type="library", games=["yellow", "crystal", "platinum"],
                 raid_reach=reach if reach is not None else {"kanto": 8, "johto": 8, "sinnoh": 8})
    if state:
        await c.send(type="state", map=12, x=5, y=8, px=80, py=128, facing=4, img=0, visible=True, game=game)
    await asyncio.sleep(0.05)
    return c


async def open_raid(hub, c, region="kanto", species=151, shiny=False):
    raid = await hub.spawn(region=region, species=species, shiny=shiny)
    m = await c.expect("raid_open", fresh=True)
    assert m["raid"]["id"] == raid["id"]
    return raid


# ----------------------------------------------------------------------- pure helpers
def test_pools_and_names():
    assert set(R.POOLS) == {"kanto", "johto", "sinnoh"}
    assert set(R.POOLS["kanto"]) == {151, 144, 145, 146, 150}
    assert {251, 151, 150, 144, 145, 146, 249, 250, 245, 243, 244} == set(R.POOLS["johto"])
    assert {491, 492, 493, 377, 378, 379, 486, 384, 386, 489, 490} <= set(R.POOLS["sinnoh"])
    assert not {480, 481, 482, 483, 484, 485, 487, 488} & set(R.POOLS["sinnoh"])   # catchable in Platinum
    for pool in R.POOLS.values():
        for sp in pool:
            assert R.SPECIES_NAMES[sp]


def test_place_phrase():
    assert R.place_phrase("Route 210") == "on Route 210"
    assert R.place_phrase("Cerulean City") == "in Cerulean City"


def test_share_and_allowance():
    assert R.allowed_total(0) == 1000 and R.allowed_total(10) == 6000
    f = "fighting"
    parts = {"a": {"dealt": 3000, "state": f}, "b": {"dealt": 1000, "state": f}, "c": {"dealt": 0, "state": f}}
    assert R.shares(parts) == {"a": 0.75, "b": 0.25, "c": 0.0}
    # Ilias's decision: only the players still in the fight count (blacked out / left / fled don't)
    parts["d"] = {"dealt": 4000, "state": "blackout"}
    parts["e"] = {"dealt": 2000, "state": "left"}
    assert R.shares(parts) == {"a": 0.75, "b": 0.25, "c": 0.0}
    # one fighter left: 100 %, whatever they dealt
    assert R.shares({"a": {"dealt": 0, "state": f}, "d": {"dealt": 4000, "state": "blackout"}}) == {"a": 1.0}
    # several fighters who dealt nothing (the others did it all): an even split
    assert R.shares({"a": {"dealt": 0, "state": f}, "b": {"dealt": 0, "state": f},
                     "d": {"dealt": 4000, "state": "left"}}) == {"a": 0.5, "b": 0.5}


def test_config_from_env(tmp_path):
    cfg = load_config(None, env={"YL_RAIDS": "off", "YL_RAID_EVERY": "60", "YL_RAID_OPEN": "10",
                                 "YL_RAID_FORCE": "sinnoh:384:shiny"})
    assert cfg.raids is False and cfg.raid_every == 60 and cfg.raid_open == 10 and cfg.raid_force == "sinnoh:384:shiny"
    cfg = load_config(None, env={})
    assert cfg.raids is True and cfg.raid_every == 120 and cfg.raid_open == 15


# ----------------------------------------------------------------------- feature + catch-up
async def test_feature_listed(server, hub, connect_client):
    c = await connect_client("Ilias")
    assert "raids" in c.welcome["features"]


async def test_feature_off(tmp_path):
    cfg = Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(tmp_path / "d"), raids=False)
    srv = YellowLinkServer(cfg, store_debounce=0.05)
    await srv.start()
    try:
        assert "raids" not in srv.features()
        assert srv.raids.auto is False
    finally:
        await srv.stop()


async def test_spawn_notice_and_catch_up(server, hub, connect_client):
    a = await raider(connect_client, "Ilias", reach={"kanto": 0})
    raid = await open_raid(hub, a, region="kanto", species=151)
    m = await a.expect("system", text="RAID! Mew appeared on Route 1 (Kanto) - 15 minutes!")
    assert raid["phase"] == "open" and raid["pool"] == 0 and raid["pool_max"] == 0
    assert raid["closes"] - raid["opens"] == 15 * 60 and raid["game"] == "yellow"
    assert raid["spot"]["name"] == "Route 1" and raid["spot"]["map"] == 12
    # someone connecting later gets the raid without the "fresh" notice
    b = await connect_client("Alex")
    m = await b.expect("raid_open")
    assert m["fresh"] is False and m["raid"]["id"] == raid["id"] and m["raid"]["species"] == 151


async def test_shiny_and_in_place_wording(server, hub, connect_client):
    a = await raider(connect_client, "Ilias", reach={"kanto": 8})
    hub.spots["kanto"]["spots"] = [[3, 10, 10, 2]]
    await open_raid(hub, a, region="kanto", species=150, shiny=True)
    await a.expect("system", text="✨ RAID! A shiny Mewtwo appeared in Cerulean City (Kanto) - 15 minutes!")


# ----------------------------------------------------------------------- joining
async def test_join_adds_two_bars_each(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level=35)
    j = await a.expect("raid_joined")
    assert j["raid"]["pool"] == 2000 and j["raid"]["pool_max"] == 2000
    await b.send(type="raid_join", id=raid["id"], level=40)
    j = await b.expect("raid_joined")
    assert j["raid"]["pool"] == 4000 and j["raid"]["pool_max"] == 4000
    names = {p["name"]: p["state"] for p in j["raid"]["participants"]}
    assert names == {"Ilias": "fighting", "Alex": "fighting"}
    u = await a.expect("raid_update")
    assert u["raid"]["pool_max"] >= 2000
    # joining again while fighting (reconnect) adds nothing
    await a.send(type="raid_join", id=raid["id"], level=35)
    j = await a.expect("raid_joined")
    assert j["raid"]["pool_max"] == 4000


@pytest.mark.parametrize("why", ["region", "challenge", "unknown", "trade"])
async def test_join_refusals(server, hub, connect_client, why):
    a = await raider(connect_client, "Ilias", game="crystal" if why == "region" else "yellow")
    raid = await open_raid(hub, a)
    rid = raid["id"]
    if why == "challenge":
        server.clients[a.uid].challenge_id = "c1"
    if why == "unknown":
        rid = "nope"
    if why == "trade":
        server.clients[a.uid].trade_id = "t1"
    await a.send(type="raid_join", id=rid, level=30)
    m = await a.expect("raid_refused")
    assert m["reason"]
    assert {"region": "Kanto", "challenge": "Soul Link", "unknown": "over", "trade": "trade"}[why] in m["reason"]
    await a.expect_none("error")


async def test_bad_join_is_an_error(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level="high")
    await a.expect("error")


# ----------------------------------------------------------------------- damage + down + catch
async def test_damage_running_total_rate_limit_and_down(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    raid = await open_raid(hub, a)
    for c in (a, b):
        await c.send(type="raid_join", id=raid["id"], level=30)
        await c.expect("raid_joined")
    hub.rng = FixedRng(0.5)
    # burst cap: right after joining at most 1 bar counts
    await a.send(type="raid_damage", id=raid["id"], dealt=1800)
    await asyncio.sleep(0.1)
    r = hub.current
    assert r["participants"][a.uid]["dealt"] == 1000 and r["pool"] == 3000
    # the same running total again: nothing new; later the held-back part counts
    hub.clock.t += 4
    await a.send(type="raid_damage", id=raid["id"], dealt=1800)
    await asyncio.sleep(0.1)
    assert r["participants"][a.uid]["dealt"] == 1800 and r["pool"] == 2200
    # a smaller total (old resend) changes nothing
    await a.send(type="raid_damage", id=raid["id"], dealt=900)
    await asyncio.sleep(0.05)
    assert r["pool"] == 2200
    await b.send(type="raid_damage", id=raid["id"], dealt=600)
    await asyncio.sleep(0.05)
    assert r["pool"] == 1600
    hub.clock.t += 10
    await a.send(type="raid_damage", id=raid["id"], dealt=3400)        # takes the pool to 0
    da = await a.expect("raid_down")
    db = await b.expect("raid_down")
    # shares: Ilias 3400 of 4000, Alex 600 -> 0.85 / 0.15; the roll 0.5 catches for Ilias only
    assert da["chance"] == pytest.approx(0.85) and da["catch"] is True
    assert db["chance"] == pytest.approx(0.15) and db["catch"] is False
    assert r["phase"] == "down" and r["pool"] == 0
    # damage after the pool is empty: ignored
    await b.send(type="raid_damage", id=raid["id"], dealt=2000)
    await asyncio.sleep(0.05)
    assert r["participants"][b.uid]["dealt"] == 600
    # joins are closed now
    c3 = await raider(connect_client, "Max")
    await c3.send(type="raid_join", id=raid["id"], level=30)
    assert "down" in (await c3.expect("raid_refused"))["reason"]
    # results: the raid ends when everyone fighting at "down" reported
    await a.send(type="raid_result", id=raid["id"], outcome="caught")
    await a.expect("system", text="Ilias caught Mew!")
    await b.send(type="raid_result", id=raid["id"], outcome="missed")
    end = await a.expect("raid_end")
    assert end["reason"] == "down" and end["caught"] == ["Ilias"] and end["raid"]["phase"] == "over"
    await b.expect("system", text="Raid over: Ilias caught Mew!")
    assert hub.current is None and hub.history[-1]["id"] == raid["id"]
    # late messages for a finished raid: silently ignored
    await a.send(type="raid_result", id=raid["id"], outcome="caught")
    await a.send(type="raid_damage", id=raid["id"], dealt=5000)
    await a.expect_none("error")


async def test_solo_raid_is_a_sure_catch(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level=30)
    await a.expect("raid_joined")
    hub.rng = FixedRng(0.999999)
    hub.clock.t += 10
    await a.send(type="raid_damage", id=raid["id"], dealt=2000)
    d = await a.expect("raid_down")
    assert d["chance"] == 1.0 and d["catch"] is True


async def test_blackout_is_out_and_doesnt_count_for_the_share(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    raid = await open_raid(hub, a)
    for c in (a, b):
        await c.send(type="raid_join", id=raid["id"], level=30)
        await c.expect("raid_joined")
    hub.clock.t += 10
    await b.send(type="raid_damage", id=raid["id"], dealt=1000)
    await b.send(type="raid_result", id=raid["id"], outcome="blackout")
    await asyncio.sleep(0.1)
    assert hub.current["participants"][b.uid]["state"] == "blackout"
    # out for good: no rejoin
    await b.send(type="raid_join", id=raid["id"], level=30)
    assert "out of this raid" in (await b.expect("raid_refused"))["reason"]
    hub.rng = FixedRng(0.0)
    await a.send(type="raid_damage", id=raid["id"], dealt=3000)
    d = await a.expect("raid_down")
    assert d["chance"] == 1.0 and d["catch"] is True          # the only one still fighting: 100 %
    await b.expect_none("raid_down")


async def test_share_only_among_fighters(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    c = await raider(connect_client, "Max")
    raid = await open_raid(hub, a)
    for x in (a, b, c):
        await x.send(type="raid_join", id=raid["id"], level=30)
        await x.expect("raid_joined")
    hub.clock.t += 20
    await c.send(type="raid_damage", id=raid["id"], dealt=3000)
    await c.send(type="raid_result", id=raid["id"], outcome="left")
    await b.send(type="raid_damage", id=raid["id"], dealt=1000)
    await asyncio.sleep(0.1)
    hub.rng = FixedRng(0.9)
    await a.send(type="raid_damage", id=raid["id"], dealt=2000)        # pool 6000 -> 0
    da, db = await a.expect("raid_down"), await b.expect("raid_down")
    assert da["chance"] == pytest.approx(2 / 3, abs=1e-3) and db["chance"] == pytest.approx(1 / 3, abs=1e-3)
    assert da["catch"] is False and db["catch"] is False
    await c.expect_none("raid_down")


async def test_time_over_and_down_grace(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level=30)
    await a.expect("raid_joined")
    assert server.clients[a.uid].busy
    hub.clock.t = raid["closes"] + 1
    await hub.tick()
    end = await a.expect("raid_end")
    assert end["reason"] == "time" and end["caught"] == []
    assert end["raid"]["participants"][0]["state"] == "fled"
    await a.expect("system", text="Mew fled - the raid is over.")
    assert not server.clients[a.uid].busy
    # down phase: ends after DOWN_GRACE even if someone never reports
    raid = await open_raid(hub, a, species=150)
    await a.send(type="raid_join", id=raid["id"], level=30)
    await a.expect("raid_joined")
    hub.clock.t += 10
    await a.send(type="raid_damage", id=raid["id"], dealt=2000)
    await a.expect("raid_down")
    hub.clock.t += R.DOWN_GRACE + 1
    await hub.tick()
    end = await a.expect("raid_end")
    assert end["reason"] == "down"
    await a.expect("system", text="Raid over: Mewtwo got away.")


async def test_busy_blocks_trades_and_battles(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level=30)
    await a.expect("raid_joined")
    await b.send(type="trade_request", to=a.uid)
    assert "busy" in (await b.expect("error"))["message"]
    await b.send(type="battle_request", to=a.uid)
    assert "busy" in (await b.expect("error"))["message"]
    await a.send(type="raid_result", id=raid["id"], outcome="left")
    await asyncio.sleep(0.1)
    assert not server.clients[a.uid].busy


async def test_reconnect_keeps_fighting_and_resends_down(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level=30)
    await a.expect("raid_joined")
    hub.clock.t += 10
    await a.send(type="raid_damage", id=raid["id"], dealt=1500)
    await asyncio.sleep(0.1)
    await a.close()
    await asyncio.sleep(0.1)
    assert hub.current["participants"][a.uid]["state"] == "fighting"
    a2 = await connect_client("Ilias", uid=a.uid)
    m = await a2.expect("raid_open")
    me = [p for p in m["raid"]["participants"] if p["uid"] == a.uid][0]
    assert me["state"] == "fighting" and me["dealt"] == 1500
    assert server.clients[a.uid].busy
    await a2.send(type="raid_damage", id=raid["id"], dealt=2000)
    d = await a2.expect("raid_down")
    await a2.close()
    await asyncio.sleep(0.1)
    a3 = await connect_client("Ilias", uid=a.uid)
    await a3.expect("raid_open")
    d2 = await a3.expect("raid_down")
    assert d2["catch"] == d["catch"] and d2["chance"] == d["chance"]


# ----------------------------------------------------------------------- scheduler
async def test_scheduler_slot_region_and_skip(server, hub, connect_client):
    hub.clock.t = 7200 * 250000 + 1        # start of a slot
    # nobody raid-capable online: nothing, and nothing later in the slot either
    plain = await connect_client("Old")    # an older client: never sent raid_reach
    await hub.tick()
    assert hub.current is None
    hub.clock.t += 7200 - 60               # too late for this slot (a raid wouldn't fit any more)
    await hub.tick()
    assert hub.current is None and hub.last_slot == 250000
    # next slot, a Crystal-only player online: the raid is in Johto, at the slot's own random minute
    hub.clock.t = 7200 * 250001
    c = await connect_client("Alex")
    await c.send(type="library", games=["crystal"], raid_reach={"johto": 3})
    await asyncio.sleep(0.05)
    at = hub.slot_start_time(250001)
    assert 7200 * 250001 <= at <= 7200 * 250002 - 15 * 60
    hub.clock.t = at - 1
    await hub.tick()
    assert hub.current is None
    hub.clock.t = at
    await hub.tick()
    raid = hub.current
    assert raid and raid["region"] == "johto" and raid["game"] == "crystal" and raid["species"] in R.POOLS["johto"]
    assert raid["spot"]["map"] == 6170
    await c.expect("raid_open", fresh=True)
    await plain.expect("raid_open", fresh=True)        # old clients get it too (they ignore it) + the chat line
    # only one raid per slot
    hub.clock.t = raid["closes"] + 1
    await hub.tick()
    await hub.tick()
    assert hub.current is None


async def test_spot_reach_is_the_lowest_online(server, hub, connect_client):
    await raider(connect_client, "Ilias", reach={"kanto": 8})
    await raider(connect_client, "Alex", reach={"kanto": 1})
    for _ in range(20):
        raid = await hub.spawn(region="kanto", species=151)
        assert raid["spot"]["map"] == 12          # reach 2/8 spots are out of Alex's reach
        await hub.end(raid, "time")


def test_pick_is_deterministic_and_varied():
    online = [{"games": ["yellow", "crystal", "platinum"], "reach": {}}] * 2
    picks = [R.pick(f"salt:{n}", online, SPOTS, {}) for n in range(200)]
    again = [R.pick(f"salt:{n}", online, SPOTS, {}) for n in range(200)]
    assert picks == again
    regions = {p["region"] for p in picks}
    assert regions == {"kanto", "johto", "sinnoh"}
    shinies = sum(p["shiny"] for p in picks)
    assert 2 <= shinies <= 25                          # 1 in 20
    assert all(p["species"] in R.POOLS[p["region"]] for p in picks)
    deoxys = [p for p in picks if p["species"] == 386]
    assert all(0 <= p["form"] <= 3 for p in deoxys)
    # never the region's previous boss twice in a row
    last = {"kanto": {"species": 151, "map": 12}}
    for n in range(50):
        p = R.pick(f"x:{n}", [{"games": ["yellow"], "reach": {"kanto": 8}}], SPOTS, last)
        assert p["species"] != 151


def test_force_parsing():
    assert R.parse_force("") is None
    assert R.parse_force("sinnoh") == ("sinnoh", None, None)
    assert R.parse_force("sinnoh:384") == ("sinnoh", 384, None)
    assert R.parse_force("kanto:151:shiny") == ("kanto", 151, True)
    assert R.parse_force("nowhere:1") is None


# ----------------------------------------------------------------------- persistence
async def test_raid_survives_a_restart(tmp_path):
    cfg = Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(tmp_path / "d"))
    srv = YellowLinkServer(cfg, store_debounce=0.05)
    srv.raids.auto = False
    srv.raids.spots = json.loads(json.dumps(SPOTS))
    clock = Clock()
    srv.raids.clock = clock
    await srv.start()
    raid = await srv.raids.spawn(region="sinnoh", species=384)
    srv.raids.current["participants"]["u1"] = R.new_participant("Ilias", 50, clock())
    srv.raids.current["pool"] = srv.raids.current["pool_max"] = 2000
    await srv.stop()
    assert os.path.exists(os.path.join(cfg.data_dir, "raids.json"))
    srv2 = YellowLinkServer(cfg, store_debounce=0.05)
    srv2.raids.auto = False
    srv2.raids.clock = clock
    await srv2.start()
    try:
        cur = srv2.raids.current
        assert cur["id"] == raid["id"] and cur["participants"]["u1"]["state"] == "fighting"
        assert srv2.raids.salt == srv.raids.salt
        clock.t = raid["closes"] + 5
        await srv2.raids.tick()
        assert srv2.raids.current is None and srv2.raids.history[-1]["id"] == raid["id"]
    finally:
        await srv2.stop()


async def test_damaged_raids_file_is_kept_aside(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "raids.json").write_text("{not json")
    srv = YellowLinkServer(Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(d)), store_debounce=0.05)
    assert srv.raids.current is None
    assert any(n.startswith("raids.json.bad-") for n in os.listdir(d))


def test_server_spot_file_shape():
    """The vendored spot sample the server ships (dev/raids/server_spots.py)."""
    data = R.load_spots()
    for region, r in data.items():
        assert region in R.POOLS
        for s in r["spots"]:
            assert len(s) == 4 and all(isinstance(v, int) for v in s)
            assert str(s[0]) in r["names"]


async def test_testing_catch_override(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    raid = await open_raid(hub, a)
    await a.send(type="raid_join", id=raid["id"], level=30)
    await a.expect("raid_joined")
    hub.catch_force = False                         # YL_RAID_CATCH=never
    hub.clock.t += 10
    await a.send(type="raid_damage", id=raid["id"], dealt=2000)
    d = await a.expect("raid_down")
    assert d["chance"] == 1.0 and d["catch"] is False


# ----------------------------------------------------------------------- review fixes (round 3)
async def test_unstarted_takes_the_player_off_and_lets_them_rejoin(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    raid = await open_raid(hub, a)
    for c in (a, b):
        await c.send(type="raid_join", id=raid["id"], level=30)
        await c.expect("raid_joined")
    assert hub.current["pool"] == 4000
    await a.send(type="raid_result", id=raid["id"], outcome="unstarted")
    await asyncio.sleep(0.1)
    r = hub.current
    assert a.uid not in r["participants"] and r["pool"] == 2000 and r["pool_max"] == 2000
    assert not server.clients[a.uid].busy
    await a.send(type="raid_join", id=raid["id"], level=30)
    j = await a.expect("raid_joined")
    assert j["raid"]["pool_max"] == 4000
    # with damage dealt already, unstarted counts as leaving (the share stays)
    hub.clock.t += 10
    await a.send(type="raid_damage", id=raid["id"], dealt=500)
    await asyncio.sleep(0.05)
    await a.send(type="raid_result", id=raid["id"], outcome="unstarted")
    await asyncio.sleep(0.1)
    assert r["participants"][a.uid]["state"] == "left"


async def test_unstarted_never_empties_the_pool(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    b = await raider(connect_client, "Alex")
    raid = await open_raid(hub, a)
    for c in (a, b):
        await c.send(type="raid_join", id=raid["id"], level=30)
        await c.expect("raid_joined")
    hub.clock.t += 10
    await b.send(type="raid_damage", id=raid["id"], dealt=3000)
    await asyncio.sleep(0.05)
    assert hub.current["pool"] == 1000
    await a.send(type="raid_result", id=raid["id"], outcome="unstarted")
    await asyncio.sleep(0.1)
    r = hub.current
    assert r["phase"] == "open" and r["pool"] == 1 and a.uid not in r["participants"]


async def test_soul_link_players_dont_count_as_raiders(server, hub, connect_client):
    a = await raider(connect_client, "Ilias")
    assert hub.capable_online()
    server.clients[a.uid].challenge_id = "c1"
    assert hub.capable_online() == []


async def test_next_raid_waits_after_a_late_one(server, hub, connect_client):
    await raider(connect_client, "Ilias", reach={"kanto": 8, "johto": 8, "sinnoh": 8})
    hub.every = 7200.0
    hub.slot_start_time = lambda n: n * 7200.0            # every slot's raid would open right at its start
    slot = 250000
    hub.clock.t = slot * 7200 + 7200 - 15 * 60 - 60          # the slot's raid opens late (just fits)
    hub.last_slot = slot - 1
    await hub.tick()
    raid = hub.current
    assert raid and hub.last_slot == slot
    with open(hub.path) as fh:
        assert json.load(fh)["last_slot"] == slot           # saved together with the raid
    hub.clock.t = raid["closes"] + 1
    await hub.tick()
    assert hub.current is None
    # the next slot starts ~1 minute later: its raid must wait until an hour after this one ended
    hub.clock.t = (slot + 1) * 7200 + 5
    await hub.tick()
    assert hub.current is None
    hub.clock.t = raid["closes"] + 1 + R.MIN_GAP_MAX + 1
    await hub.tick()
    assert hub.current is not None


async def test_no_raid_possible_is_logged_once(server, hub, connect_client, caplog):
    await raider(connect_client, "Ilias", reach={"kanto": 8})
    hub.spots = {}                                          # no spot data at all
    hub.clock.t = 250010 * 7200 + 7200 - 15 * 60 - 100
    hub.last_slot = 250009
    caplog.set_level("INFO", logger="yellowlink.raids")
    for _ in range(5):
        await hub.tick()
    assert sum("no raid possible" in r.message for r in caplog.records) == 1
