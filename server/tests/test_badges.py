"""Badges (SPEC.md section 9): main-game reports, roster summary, get_badges, Soul Link awards from the ledger."""
import asyncio
import json
import os
import time

import pytest

from yellowlink_server import badges as bdg
from yellowlink_server.config import Config
from yellowlink_server.server import YellowLinkServer

from test_challenges import join_both, start_challenge


def entry(server, uid):
    return next(e for e in server.roster() if e["uid"] == uid)


async def test_welcome_lists_feature_and_empty_badges(server, connect_client):
    a = await connect_client("Ilias")
    assert "badges" in a.welcome.get("features", [])
    me = next(e for e in a.welcome["roster"] if e["uid"] == a.uid)
    assert me["badges"] == {}


async def test_report_merges_and_shows_in_roster(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    now = time.time()
    await a.send(type="badges", earned={"kanto": {"champion": now - 100, "dex": None, "shinies": ["aa01", "bb02"]}})
    r = await b.expect("roster", timeout=3)
    while entry_in(r, a.uid).get("badges") == {}:
        r = await b.expect("roster", timeout=3)
    assert entry_in(r, a.uid)["badges"] == {"kanto": {"champion": True, "dex": False, "shiny": 2, "soullink": None}}
    await asyncio.sleep(0.1)
    sysmsg = [m["text"] for m in b.inbox if m.get("type") == "system" and "Ilias" in m["text"]]
    assert sysmsg == ["Ilias has 2 badges"]       # first report (badges from before): one line, not one per badge

    # a later report can't take anything away; shinies are a union; earliest time kept
    b.clear()
    await a.send(type="badges", earned={"kanto": {"champion": None, "shinies": ["cc03"]},
                                        "johto": {"dex": True}})
    await asyncio.sleep(0.2)
    news = [m["text"] for m in b.inbox if m.get("type") == "system"]
    assert news == ["Ilias completed the Johto Pokédex!"]     # earned just now: announced
    e = entry(server, a.uid)
    assert e["badges"]["kanto"] == {"champion": True, "dex": False, "shiny": 3, "soullink": None}
    assert e["badges"]["johto"] == {"champion": False, "dex": True, "shiny": 0, "soullink": None}
    stored = server.badges.get(a.uid)
    assert abs(stored["kanto"]["champion"] - (now - 100)) < 1
    assert sorted(stored["kanto"]["shinies"]) == ["aa01", "bb02", "cc03"]

    # the full view of one player
    await b.send(type="get_badges", uid=a.uid)
    d = await b.expect("badges_data", uid=a.uid)
    assert d["name"] == "Ilias"
    assert d["badges"]["kanto"]["shiny"] == 3 and abs(d["badges"]["kanto"]["champion"] - (now - 100)) < 1
    assert d["badges"]["kanto"]["soullink"] is None and d["badges"]["johto"]["dex"] > now - 5
    await b.send(type="get_badges", uid="nobody")
    d = await b.expect("badges_data", uid="nobody")
    assert d["badges"] == {}


def entry_in(roster_msg, uid):
    return next(e for e in roster_msg["roster"] if e["uid"] == uid)


async def test_same_report_twice_is_quiet(server, connect_client):
    a = await connect_client("Ilias")
    rep = {"sinnoh": {"champion": True}}
    await a.send(type="badges", earned=rep)
    await a.expect("roster")
    await asyncio.sleep(0.1)
    a.clear()
    await a.send(type="badges", earned=rep)
    await a.expect_none("roster", wait=0.3)
    await a.expect_none("system", wait=0.0)
    await a.expect_none("error", wait=0.0)


@pytest.mark.parametrize("earned, needle", [
    ("nope", "earned"),
    ({"kanto": "yes"}, "kanto"),
    ({"kanto": {"shinies": "aa"}}, "shinies"),
])
async def test_report_validation(server, connect_client, earned, needle):
    a = await connect_client("Ilias")
    await a.send(type="badges", earned=earned)
    err = await a.expect("error")
    assert needle in err["message"]
    assert server.badges.get(a.uid) in (None, {})


async def test_bad_clock_and_odd_values_are_tolerated(server, connect_client):
    """A wrong PC clock or a key we don't know must never block syncing (no red error on every connect)."""
    a = await connect_client("Ilias")
    now = time.time()
    await a.send(type="badges", earned={
        "kanto": {"champion": 12, "dex": now + 10 * 86400, "shinies": ["aa", "aa", "a b", "x" * 41, 5, "bb"]},
        "johto": {"champion": "yesterday"},
        "hoenn": {"champion": True}})                       # a region from a newer client: ignored
    await a.expect("roster")
    await a.expect_none("error", wait=0.2)
    st = server.badges.get(a.uid)
    assert set(st) == {"kanto", "johto"}
    assert abs(st["kanto"]["champion"] - now) < 5 and abs(st["kanto"]["dex"] - now) < 5   # clamped to server time
    assert abs(st["johto"]["champion"] - now) < 5
    assert st["kanto"]["shinies"] == ["aa", "bb"]
    big = [f"k{i}" for i in range(1500)]
    await a.send(type="badges", earned={"sinnoh": {"shinies": big}})
    await asyncio.sleep(0.2)
    assert len(server.badges.get(a.uid)["sinnoh"]["shinies"]) == bdg.SHINY_MAX


async def test_soullink_field_in_report_is_ignored(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(type="badges", earned={"kanto": {"soullink": "gold", "champion": True}})
    await a.expect("roster")
    assert entry(server, a.uid)["badges"]["kanto"]["soullink"] is None


async def test_shiny_keys_capped(server, connect_client):
    a = await connect_client("Ilias")
    for part in range(3):
        await a.send(type="badges", earned={"johto": {"shinies": [f"p{part}k{i}" for i in range(500)]}})
    await asyncio.sleep(0.3)
    assert entry(server, a.uid)["badges"]["johto"]["shiny"] == bdg.SHINY_MAX == 1000


async def test_persisted_and_reloaded(tmp_path, server, connect_client):
    a = await connect_client("Ilias")
    await a.send(type="badges", earned={"kanto": {"dex": True, "shinies": ["aa"]}})
    await a.expect("roster")
    await server.badges.flush()
    with open(os.path.join(server.config.data_dir, "badges.json"), encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw["players"][a.uid]["kanto"]["shinies"] == ["aa"]
    again = bdg.BadgeStore(server.config.data_dir)
    assert again.summary(a.uid) == {"kanto": {"champion": False, "dex": True, "shiny": 1, "soullink": None}}


async def test_old_client_without_badges_has_none(server, connect_client):
    a = await connect_client("Old")
    b = await connect_client("Alex")
    await a.send(type="party", mons=[], trainer="OLD", badges=3)
    await asyncio.sleep(0.2)
    assert entry(server, a.uid)["badges"] == {}
    await b.send(type="get_badges", uid=a.uid)
    d = await b.expect("badges_data", uid=a.uid)
    assert d["badges"] == {} and d["name"] == "Old"


async def finish_run(a, b, game, rules):
    c = await start_challenge(a, b, game=game, rules=rules)
    await join_both(a, b, c["id"])
    return c


async def test_soullink_hof_awards_gold_or_silver(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await finish_run(a, b, "yellow", {"dupes": True, "shiny": False})        # exp_share off -> gold
    await a.send(type="challenge_event", challenge_id=c["id"], event={"t": "hof", "cid": "h1"})
    await a.expect("challenge_event", challenge_id=c["id"])
    await asyncio.sleep(0.2)
    assert entry(server, a.uid)["badges"]["kanto"]["soullink"] == "gold"
    assert entry(server, b.uid)["badges"] == {}                # only the player who entered the HoF
    assert any("Soul Link" in m.get("text", "") for m in b.inbox if m.get("type") == "system")
    await b.send(type="challenge_event", challenge_id=c["id"], event={"t": "hof", "cid": "h2"})
    await asyncio.sleep(0.3)
    assert entry(server, b.uid)["badges"]["kanto"]["soullink"] == "gold"
    # a later silver run doesn't downgrade gold; silver in another region
    await b.send(type="library", games=["yellow", "crystal"], challenge_rules=["exp_share"])
    await a.send(type="library", games=["yellow", "crystal"], challenge_rules=["exp_share"])
    await asyncio.sleep(0.1)
    c2 = await finish_run(a, b, "crystal", {"exp_share": True})
    await a.send(type="challenge_event", challenge_id=c2["id"], event={"t": "hof", "cid": "h3"})
    await asyncio.sleep(0.3)
    e = entry(server, a.uid)["badges"]
    assert e["kanto"]["soullink"] == "gold" and e["johto"]["soullink"] == "silver"
    await a.send(type="get_badges", uid=a.uid)
    d = await a.expect("badges_data", uid=a.uid)
    assert d["badges"]["johto"]["soullink_ts"] > 0
    # main-game reports can't remove a Soul Link badge
    await a.send(type="badges", earned={"kanto": {"champion": True}})
    await asyncio.sleep(0.2)
    assert entry(server, a.uid)["badges"]["kanto"] == {"champion": True, "dex": False, "shiny": 0,
                                                       "soullink": "gold"}


def test_award_rank():
    st = bdg.BadgeStore.__new__(bdg.BadgeStore)
    st.players, st._save_task, st.data_dir, st.path, st.debounce = {}, None, "", "", 0
    st.schedule_save = lambda: None
    assert st.award_soullink("u", "kanto", "silver", 100.0)
    assert st.award_soullink("u", "kanto", "gold", 200.0)
    assert not st.award_soullink("u", "kanto", "silver", 300.0)
    assert not st.award_soullink("u", "kanto", "gold", 400.0)
    assert st.players["u"]["kanto"] == {"soullink": "gold", "soullink_ts": 200.0}


def test_backfill_from_old_runs(tmp_path):
    """Runs completed before 0.7.5: the server awards their Soul Link badges at startup."""
    data = tmp_path / "data"
    data.mkdir()
    led = [{"seq": 1, "p": "A", "ts": 1.7e9, "t": "hof"}, {"seq": 2, "p": "B", "ts": 1.7e9 + 5, "t": "hof"}]
    recs = [
        {"id": "r1", "kind": "soul_link", "game": "platinum", "players": ["A", "B"], "names": {"A": "a", "B": "b"},
         "rules": {"dupes": True}, "status": "completed", "progress": {}, "ledger": led},
        {"id": "r2", "kind": "soul_link", "game": "crystal", "players": ["A", "C"], "names": {},
         "rules": {"exp_share": True}, "status": "active", "progress": {},
         "ledger": [{"seq": 1, "p": "A", "ts": 1.7e9, "t": "hof"}, {"seq": 2, "p": "C", "ts": 1.7e9, "t": "enc",
                                                                   "area": "x", "dex": 1, "status": "open"}]},
    ]
    (data / "challenges.json").write_text(json.dumps({"version": 1, "challenges": recs}))
    srv = YellowLinkServer(Config(data_dir=str(data), key="k"), store_debounce=0.01)
    assert srv.badges.summary("A") == {"sinnoh": {"champion": False, "dex": False, "shiny": 0, "soullink": "gold"},
                                       "johto": {"champion": False, "dex": False, "shiny": 0, "soullink": "silver"}}
    assert srv.badges.summary("B")["sinnoh"]["soullink"] == "gold"
    assert srv.badges.summary("C") == {}
    assert srv.badges.get("B")["sinnoh"]["soullink_ts"] == 1.7e9 + 5


async def test_old_earn_times_are_not_announced_one_by_one(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    now = time.time()
    await a.send(type="badges", earned={"kanto": {"champion": now}})
    await asyncio.sleep(0.2)
    b.clear()
    # a later report bringing badges earned long ago (e.g. played offline last week): one summary line
    await a.send(type="badges", earned={"johto": {"champion": now - 7 * 86400, "dex": now - 86400},
                                        "sinnoh": {"champion": now}})
    await asyncio.sleep(0.2)
    news = [m["text"] for m in b.inbox if m.get("type") == "system"]
    assert news == ["Ilias earned the Sinnoh Champion badge!", "Ilias has 4 badges"]


def test_legacy_run_tier_by_release_time():
    """Runs without the exp_share rule: 0.7.3 (2026-10-26 14:39:30 UTC) applied the personal Exp Share All
    setting (default on) inside runs - a Hall of Fame from then on is silver, before it gold."""
    legacy = {"rules": {"dupes": True}}
    assert bdg.run_tier(legacy, bdg.EXP_SHARE_RELEASE - 1) == "gold"
    assert bdg.run_tier(legacy, bdg.EXP_SHARE_RELEASE) == "silver"
    assert bdg.run_tier({"rules": {"exp_share": False}}, bdg.EXP_SHARE_RELEASE + 99) == "gold"
    assert bdg.run_tier({"rules": {"exp_share": True}}, bdg.EXP_SHARE_RELEASE - 99) == "silver"
    assert bdg.EXP_SHARE_RELEASE == 1793025570


def test_backfill_legacy_after_release_is_silver(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    t = bdg.EXP_SHARE_RELEASE
    recs = [{"id": "r1", "kind": "soul_link", "game": "yellow", "players": ["A", "B"], "names": {},
             "rules": {"dupes": True}, "status": "completed", "progress": {},
             "ledger": [{"seq": 1, "p": "A", "ts": t - 60, "t": "hof"}, {"seq": 2, "p": "B", "ts": t + 60, "t": "hof"}]}]
    (data / "challenges.json").write_text(json.dumps({"version": 1, "challenges": recs}))
    srv = YellowLinkServer(Config(data_dir=str(data), key="k"), store_debounce=0.01)
    assert srv.badges.summary("A")["kanto"]["soullink"] == "gold"
    assert srv.badges.summary("B")["kanto"]["soullink"] == "silver"
