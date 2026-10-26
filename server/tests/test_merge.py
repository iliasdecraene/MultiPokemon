"""Merging one server into another (SPEC.md section 8): the merge tool and the "moved" forwarding mode."""
import asyncio
import json
import os
import uuid

import pytest
from websockets.asyncio.client import connect

from conftest import KEY, WSClient
from yellowlink_server import PROTOCOL_VERSION
from yellowlink_server.config import Config, load_config
from yellowlink_server.merge import merge
from yellowlink_server.server import YellowLinkServer


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def challenge(cid, players):
    return {"id": cid, "kind": "soul_link", "game": "yellow", "status": "active", "players": players,
            "names": {p: p for p in players}, "rules": {}, "progress": {}, "ledger": []}


@pytest.fixture
def two_servers(tmp_path):
    src, dst = tmp_path / "fly", tmp_path / "main"
    write(src / "players.json", {"version": 1, "players": {
        "u-bea": {"name": "Bea", "last_seen": 1}, "u-ilias-fly": {"name": "Ilias", "last_seen": 2},
        "u-both": {"name": "Odd", "last_seen": 3}}})
    write(dst / "players.json", {"version": 1, "players": {
        "u-ilias": {"name": "Ilias", "last_seen": 5}, "u-both": {"name": "Kept", "last_seen": 9}}})
    write(src / "pc" / "u-bea.json", {"rev": 3, "srv_rev": 7, "boxes": [[] for _ in range(14)]})
    write(src / "pc" / "u-both.json", {"rev": 1, "srv_rev": 1, "boxes": [[] for _ in range(14)]})
    write(dst / "pc" / "u-both.json", {"rev": 9, "srv_rev": 9, "boxes": [[] for _ in range(14)]})
    write(src / "challenges.json", {"version": 1, "challenges": [challenge("c-fly", ["u-bea", "u-ilias-fly"])]})
    write(dst / "challenges.json", {"version": 1, "challenges": [challenge("c-main", ["u-ilias", "u-x"])]})
    os.makedirs(src / "reports")
    (src / "reports" / "r1.txt").write_text("report")
    return str(src), str(dst)


def test_merge_copies_everything_and_never_overwrites(two_servers):
    src, dst = two_servers
    r = merge(src, dst)
    players = read(os.path.join(dst, "players.json"))["players"]
    assert set(players) == {"u-ilias", "u-both", "u-bea", "u-ilias-fly"}
    assert players["u-both"]["name"] == "Kept"                     # existing entry untouched
    assert r["players_kept"] == ["u-both"] and r["same_names"] == ["Ilias"]
    assert read(os.path.join(dst, "pc", "u-bea.json"))["srv_rev"] == 7
    assert read(os.path.join(dst, "pc", "u-both.json"))["rev"] == 9  # not overwritten
    assert r["pc_added"] == 1 and r["pc_kept"] == ["u-both.json"]
    ids = [c["id"] for c in read(os.path.join(dst, "challenges.json"))["challenges"]]
    assert ids == ["c-main", "c-fly"]
    assert os.path.exists(os.path.join(dst, "reports", "r1.txt"))
    assert r["backup"] and read(os.path.join(r["backup"], "players.json"))["players"].keys() == {"u-ilias", "u-both"}
    again = merge(src, dst, backup=False)                          # running it twice adds nothing
    assert not again["players_added"] and again["pc_added"] == 0 and again["challenges_added"] == 0


def test_merge_dry_run_writes_nothing(two_servers):
    src, dst = two_servers
    before = read(os.path.join(dst, "players.json"))
    r = merge(src, dst, dry=True)
    assert len(r["players_added"]) == 2 and r["backup"] is None
    assert read(os.path.join(dst, "players.json")) == before
    assert not os.path.exists(os.path.join(dst, "pc", "u-bea.json"))


def test_merge_refuses_with_unfinished_trade(two_servers):
    src, dst = two_servers
    write(os.path.join(src, "pc", "journal", "t1.json"), {"trade_id": "t1"})
    with pytest.raises(SystemExit):
        merge(src, dst)


def test_merged_data_loads_in_a_server(two_servers):
    src, dst = two_servers
    merge(src, dst)
    srv = YellowLinkServer(Config(data_dir=dst, key=KEY))
    assert {"u-bea", "u-ilias-fly", "u-ilias"} <= set(srv.store.players)
    assert {"c-fly", "c-main"} <= set(srv.challenges.records)


def test_moved_config_from_env():
    cfg = load_config(env={"YL_MOVED_TO": "1.2.3.4:8765", "YL_MOVED_KEY": "pw"})
    assert cfg.moved_to == "ws://1.2.3.4:8765" and cfg.moved_key == "pw"


@pytest.fixture
async def moved_server(tmp_path):
    cfg = Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(tmp_path / "data"), latest_client="0.7.1",
                 moved_to="ws://10.0.0.1:8765", moved_key="main-pw")
    srv = YellowLinkServer(cfg, store_debounce=0.05)
    await srv.start()
    yield srv
    await srv.stop()


async def hello(srv, version, key=KEY):
    ws = await connect(f"ws://127.0.0.1:{srv.port}")
    c = WSClient(ws, str(uuid.uuid4()), "Bea")
    await c.send(type="hello", name="Bea", uid=c.uid, key=key, version=version, protocol=PROTOCOL_VERSION)
    return c


async def test_new_client_is_sent_to_the_new_server(moved_server):
    c = await hello(moved_server, "0.7.1")
    m = await c.expect("moved")
    assert m == {"type": "moved", "url": "ws://10.0.0.1:8765", "key": "main-pw"}
    assert await asyncio.wait_for(c.closed, 3) == 4005
    assert not moved_server.clients


async def test_moved_server_still_checks_the_password(moved_server):
    c = await hello(moved_server, "0.7.1", key="wrong")
    await c.expect("error")
    assert await asyncio.wait_for(c.closed, 3) == 4001


async def test_old_client_only_gets_the_update_notice(moved_server):
    c = await hello(moved_server, "0.7.0")
    w = await c.expect("welcome")
    assert w["latest_client"] == "0.7.1" and w["roster"] == [] and "moved" in w["motd"]
    await c.send(type="chat", text="hello?")
    await c.send(type="pc_get")
    await c.expect_none("error")
    await c.expect_none("chat")
    assert not moved_server.clients                     # not a real session: no roster, no PC, no trades
    await c.close()


def test_merge_copies_badges(two_servers):
    src, dst = two_servers
    write(os.path.join(src, "badges.json"), {"version": 1, "players": {
        "u-bea": {"kanto": {"champion": 1.7e9}}, "u-both": {"johto": {"dex": 1.7e9}}}})
    write(os.path.join(dst, "badges.json"), {"version": 1, "players": {"u-both": {"kanto": {"champion": 1.8e9}}}})
    r = merge(src, dst)
    got = read(os.path.join(dst, "badges.json"))["players"]
    assert got["u-bea"] == {"kanto": {"champion": 1.7e9}}
    assert got["u-both"] == {"kanto": {"champion": 1.8e9}}           # existing entry untouched
    assert r["badges_added"] == 1
