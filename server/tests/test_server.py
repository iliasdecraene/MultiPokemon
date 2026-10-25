import asyncio
import base64
import json
import os

import pytest
from websockets.asyncio.client import connect

from conftest import KEY, make_mon
from yellowlink_server.config import Config, load_config
from yellowlink_server.server import YellowLinkServer

SPRITE = base64.b64encode(bytes(range(256)) + bytes(128)).decode()


def state_msg(**kw):
    m = dict(type="state", map=0, x=5, y=6, px=80, py=96, facing=0, img=0, visible=True)
    m.update(kw)
    return m


# --------------------------------------------------------------------- auth

async def test_bad_key_closes_4001(connect_client):
    c = await connect_client("Ilias", key="nope", expect_welcome=False)
    err = await c.expect("error")
    assert "key" in err["message"]
    assert await asyncio.wait_for(c.closed, 3) == 4001


async def test_first_message_must_be_hello(server):
    async with connect(f"ws://127.0.0.1:{server.port}") as ws:
        await ws.send(json.dumps({"type": "chat", "text": "hi"}))
        assert json.loads(await ws.recv())["type"] == "error"
        await asyncio.sleep(0.1)
        with pytest.raises(Exception):
            await ws.recv()
        assert ws.close_code == 4003


async def test_duplicate_uid_kicks_old(connect_client):
    old = await connect_client("Ilias", uid="same-uid")
    new = await connect_client("Ilias", uid="same-uid")
    assert await asyncio.wait_for(old.closed, 3) == 4002
    assert new.welcome["you"] == "same-uid"


async def test_welcome_contents(connect_client):
    a = await connect_client("Ilias")
    w = a.welcome
    assert w["you"] == a.uid and w["players"] == []
    assert w["latest_client"] == "0.1.0" and w["motd"] == "hi"
    assert w["download_url"] == "https://example.invalid/dl"
    assert [r["uid"] for r in w["roster"]] == [a.uid]


# ----------------------------------------------------------------- presence

async def test_join_leave_broadcasts(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    j = await a.expect("player_join")
    assert j["player"] == {"uid": b.uid, "name": "Alex", "state": None, "has_sprite": False}
    await a.expect("system", text="Alex joined")
    r = await a.expect("roster")
    while not any(e["uid"] == b.uid for e in r["roster"]):
        r = await a.expect("roster")
    assert any(e["uid"] == b.uid and e["online"] for e in r["roster"])
    assert [p["uid"] for p in b.welcome["players"]] == [a.uid]

    await b.close()
    await a.expect("player_leave", uid=b.uid)
    await a.expect("system", text="Alex left")
    r = await a.expect("roster", timeout=3)
    entry = next(e for e in r["roster"] if e["uid"] == b.uid)
    assert entry["online"] is False


async def test_state_relay_and_replay(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(**state_msg(x=7))
    s = await b.expect("state")
    assert s["uid"] == a.uid and s["x"] == 7 and s["visible"] is True
    await a.expect_none("state")  # sender gets no echo
    c = await connect_client("Sam")
    pa = next(p for p in c.welcome["players"] if p["uid"] == a.uid)
    assert pa["state"]["x"] == 7


async def test_invalid_state_rejected(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(**state_msg(x="seven"))
    await a.expect("error")
    await b.expect_none("state")


async def test_sprite_relay_and_replay_to_newcomer(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="sprite", data=SPRITE)
    s = await b.expect("sprite")
    assert s == {"type": "sprite", "uid": a.uid, "data": SPRITE}
    c = await connect_client("Sam")
    assert next(p for p in c.welcome["players"] if p["uid"] == a.uid)["has_sprite"] is True
    s = await c.expect("sprite")
    assert s["uid"] == a.uid and s["data"] == SPRITE


async def test_bad_sprite_rejected(connect_client):
    a = await connect_client("Ilias")
    await a.send(type="sprite", data=base64.b64encode(b"short").decode())
    await a.expect("error")


# --------------------------------------------------------------------- chat

async def test_chat(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="chat", text="hi\x00 there\n" + "x" * 300)
    m = await b.expect("chat")
    assert m["uid"] == a.uid and m["name"] == "Ilias"
    assert m["text"].startswith("hi there") and len(m["text"]) == 200
    assert "\x00" not in m["text"] and "\n" not in m["text"]
    await a.expect("chat")  # sender receives the echo too


async def test_ping_pong(connect_client):
    a = await connect_client("Ilias")
    await a.send(type="ping", t=12345)
    assert (await a.expect("pong"))["t"] == 12345


async def test_rate_limit_drops_excess(connect_client):
    a = await connect_client("Ilias")
    for i in range(60):
        await a.send(type="ping", t=i)
    await asyncio.sleep(0.3)
    pongs = [m for m in a.inbox if m["type"] == "pong"]
    assert 25 <= len(pongs) <= 30


async def test_oversize_message_closes(connect_client):
    a = await connect_client("Ilias")
    await a.send(type="chat", text="x" * 70000)
    assert await asyncio.wait_for(a.closed, 3) == 1009


# ------------------------------------------------- party / roster / persist

async def test_roster_and_get_party_offline_after_restart(tmp_path, connect_client, server):
    a = await connect_client("Ilias")
    b = await connect_client("Alex", uid="alex-uid")
    mons = [make_mon(0x54, 12), make_mon(0x99, 7)]
    await b.send(type="party", mons=mons, trainer="ALEX", badges=3)
    r = await a.expect("roster", timeout=3)
    while not any(e["uid"] == "alex-uid" and e["party_size"] == 2 for e in r["roster"]):
        r = await a.expect("roster")
    e = next(e for e in r["roster"] if e["uid"] == "alex-uid")
    assert e["levels"] == [12, 7] and e["species"] == [0x54, 0x99] and e["trainer"] == "ALEX"
    await b.close()
    await a.expect("player_leave", uid="alex-uid")

    await a.send(type="get_party", uid="alex-uid")
    pd = await a.expect("party_data")
    assert pd["name"] == "Alex" and pd["trainer"] == "ALEX" and pd["mons"] == mons

    # Persistence: restart a fresh server on the same data dir.
    await asyncio.sleep(0.2)  # let the debounced save fire
    path = os.path.join(server.config.data_dir, "players.json")
    assert os.path.exists(path)
    cfg2 = Config(host="127.0.0.1", port=0, key=KEY, data_dir=server.config.data_dir)
    srv2 = YellowLinkServer(cfg2)
    await srv2.start()
    try:
        async with connect(f"ws://127.0.0.1:{srv2.port}") as ws:
            await ws.send(json.dumps({"type": "hello", "name": "Sam", "uid": "sam",
                                      "key": KEY, "version": "0.1.0", "protocol": 1}))
            welcome = json.loads(await ws.recv())
            entry = next(e for e in welcome["roster"] if e["uid"] == "alex-uid")
            assert entry["online"] is False and entry["party_size"] == 2
            assert entry["last_seen"] > 0
            await ws.send(json.dumps({"type": "get_party", "uid": "alex-uid"}))
            while True:
                m = json.loads(await ws.recv())
                if m["type"] == "party_data":
                    break
            assert m["mons"] == mons and m["name"] == "Alex"
    finally:
        await srv2.stop()


async def test_get_party_unknown(connect_client):
    a = await connect_client("Ilias")
    await a.send(type="get_party", uid="nobody")
    pd = await a.expect("party_data")
    assert pd["uid"] == "nobody" and "error" in pd


async def test_party_invalid_mon_rejected(connect_client):
    a = await connect_client("Ilias")
    bad = make_mon(species=0)
    await a.send(type="party", mons=[bad], trainer="X", badges=0)
    await a.expect("error")


async def test_battle_result_broadcast(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="battle_result", opponent_uid=b.uid, won=True)
    await b.expect("system", text="Ilias beat Alex's team!")
    await a.send(type="battle_result", opponent_uid=b.uid, won=False)
    await b.expect("system", text="Alex's team defeated Ilias!")
    await a.send(type="battle_result", opponent_uid="ghost", won=True)
    await a.expect("error")


# ------------------------------------------------------------------- trades

async def open_trade(a, b):
    await a.send(type="trade_request", to=b.uid)
    req = await b.expect("trade_request")
    assert req["from"] == a.uid and req["name"] == a.name
    tid = req["trade_id"]
    await b.send(type="trade_respond", trade_id=tid, accept=True)
    sa = await a.expect("trade_started", trade_id=tid)
    sb = await b.expect("trade_started", trade_id=tid)
    assert sa["with"] == b.uid and sa["name"] == b.name
    assert sb["with"] == a.uid and sb["name"] == a.name
    return tid


async def test_trade_happy_path(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    mon_a, mon_b = make_mon(0x54, 10), make_mon(0x26, 20)  # PIKACHU-ish, KADABRA
    await a.send(type="trade_offer", trade_id=tid, slot=2, mon=mon_a)
    off = await b.expect("trade_offer")
    assert off == {"type": "trade_offer", "trade_id": tid, "from": a.uid, "slot": 2, "mon": mon_a}
    await a.expect_none("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, slot=0, mon=mon_b)
    await a.expect("trade_offer", **{"from": b.uid})

    await a.send(type="trade_confirm", trade_id=tid)
    cs = await a.expect("trade_confirm_state")
    assert (cs["you"], cs["them"]) == (True, False)
    cs = await b.expect("trade_confirm_state")
    assert (cs["you"], cs["them"]) == (False, True)
    await b.send(type="trade_confirm", trade_id=tid)

    ea = await a.expect("trade_execute")
    eb = await b.expect("trade_execute")
    assert ea == {"type": "trade_execute", "trade_id": tid, "give_slot": 2, "receive": mon_b}
    assert eb == {"type": "trade_execute", "trade_id": tid, "give_slot": 0, "receive": mon_a}

    # cancel no longer allowed while executing
    await a.send(type="trade_cancel", trade_id=tid)
    await a.expect("error")

    await a.send(type="trade_done", trade_id=tid, ok=True)
    await a.expect_none("trade_complete")
    await b.send(type="trade_done", trade_id=tid, ok=True)
    await a.expect("trade_complete", trade_id=tid)
    await b.expect("trade_complete", trade_id=tid)
    sysmsg = await a.expect("system", text="Ilias traded PIKACHU with Alex for PIKACHU!")
    assert sysmsg

    # both are free to trade again
    tid2 = await open_trade(b, a)
    assert tid2 != tid


async def test_reoffer_resets_confirmation(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon(0x54))
    await b.expect("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, slot=1, mon=make_mon(0x99))
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await a.expect("trade_confirm_state", you=True, them=False)
    await b.expect("trade_confirm_state", you=False, them=True)

    await b.send(type="trade_offer", trade_id=tid, slot=3, mon=make_mon(0x55))
    off = await a.expect("trade_offer", slot=3)
    assert off["from"] == b.uid
    await a.expect("trade_confirm_state", you=False, them=False)
    await b.expect("trade_confirm_state", you=False, them=False)

    # a single confirm no longer executes
    await b.send(type="trade_confirm", trade_id=tid)
    await a.expect("trade_confirm_state", you=False, them=True)
    await a.expect_none("trade_execute")


async def test_confirm_requires_both_offers(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await a.send(type="trade_confirm", trade_id=tid)
    await a.expect("error")


async def test_trade_cancel(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await b.send(type="trade_cancel", trade_id=tid)
    ca = await a.expect("trade_cancelled", trade_id=tid)
    cb = await b.expect("trade_cancelled", trade_id=tid)
    assert ca["reason"] == cb["reason"] == "cancelled"
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await a.expect("error")


async def test_trade_decline(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="trade_request", to=b.uid)
    tid = (await b.expect("trade_request"))["trade_id"]
    await b.send(type="trade_respond", trade_id=tid, accept=False)
    assert (await a.expect("trade_cancelled", trade_id=tid))["reason"] == "declined"


async def test_trade_busy_and_non_participant(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await connect_client("Sam")
    tid = await open_trade(a, b)
    await c.send(type="trade_request", to=a.uid)
    assert "busy" in (await c.expect("error"))["message"]
    await c.send(type="trade_cancel", trade_id=tid)
    await c.expect("error")
    await c.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await c.expect("error")
    await a.expect_none("trade_cancelled")


async def test_partner_disconnect_mid_trade(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await b.close()
    c = await a.expect("trade_cancelled", trade_id=tid)
    assert c["reason"] == "partner left"


async def test_partner_disconnect_during_execute(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await b.expect("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.expect("trade_confirm_state", them=True)
    await b.send(type="trade_confirm", trade_id=tid)
    await a.expect("trade_execute")
    await a.send(type="trade_done", trade_id=tid, ok=True)
    await b.close()
    c = await a.expect("trade_cancelled", trade_id=tid)
    assert c["reason"] == "execution failed"


async def test_trade_done_failure(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await b.expect("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.expect("trade_confirm_state", them=True)
    await b.send(type="trade_confirm", trade_id=tid)
    await b.expect("trade_execute")
    await b.send(type="trade_done", trade_id=tid, ok=False)
    c = await a.expect("trade_cancelled", trade_id=tid)
    assert c["reason"] == "execution failed"


@pytest.mark.parametrize("mon,slot", [
    ({"data": base64.b64encode(bytes([1]) + bytes(42)).decode(),
      "ot": base64.b64encode(bytes(11)).decode(),
      "nick": base64.b64encode(bytes(11)).decode()}, 0),            # 43 bytes
    (None, 0),                                                     # species 0 (filled below)
    ({"data": "!!!notbase64", "ot": "", "nick": ""}, 0),
    ("MON", 0),
    (None, 6),                                                     # bad slot
])
async def test_invalid_mon_rejected(connect_client, mon, slot):
    if mon is None:
        mon = make_mon(species=0) if slot == 0 else make_mon()
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=slot, mon=mon)
    await a.expect("error")
    await b.expect_none("trade_offer")


async def test_trade_request_expires(connect_client, monkeypatch):
    import yellowlink_server.server as srvmod
    monkeypatch.setattr(srvmod, "TRADE_REQUEST_TIMEOUT", 0.2)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="trade_request", to=b.uid)
    tid = (await b.expect("trade_request"))["trade_id"]
    assert (await a.expect("trade_cancelled", trade_id=tid))["reason"] == "timeout"
    assert (await b.expect("trade_cancelled", trade_id=tid))["reason"] == "timeout"
    await b.send(type="trade_respond", trade_id=tid, accept=True)
    await b.expect("error")


async def test_requester_can_cancel_without_id(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="trade_request", to=b.uid)
    tid = (await b.expect("trade_request"))["trade_id"]
    await a.send(type="trade_cancel", trade_id="")
    assert (await b.expect("trade_cancelled", trade_id=tid))["reason"] == "cancelled"


# ------------------------------------------------------------------- config

def test_load_config_env_and_cli(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"key": "file", "port": 1111, "motd": "m", "bogus": 1}))
    cfg = load_config(str(p), env={})
    assert (cfg.key, cfg.port, cfg.motd) == ("file", 1111, "m")
    cfg = load_config(str(p), env={"YL_KEY": "env", "YL_PORT": "2222", "YL_DATA_DIR": "/d"})
    assert (cfg.key, cfg.port, cfg.data_dir) == ("env", 2222, "/d")
    cfg = load_config(str(p), port=3333, host="127.0.0.1", env={"YL_PORT": "2222"})
    assert (cfg.port, cfg.host) == (3333, "127.0.0.1")
    assert load_config(None, env={}).port == 8765


async def _execute(a, b):
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon())
    await b.expect("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, slot=0, mon=make_mon(0x26))
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.expect("trade_confirm_state", them=True)
    await b.send(type="trade_confirm", trade_id=tid)
    await a.expect("trade_execute")
    await b.expect("trade_execute")
    return tid


async def test_trade_status_after_disconnect_is_failed(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await _execute(a, b)
    await b.send(type="trade_done", trade_id=tid, ok=True)
    await a.send(type="trade_status", trade_id=tid)
    assert (await a.expect("trade_status", trade_id=tid))["state"] == "pending"
    await a.close()  # A drops after possibly applying
    await b.expect("trade_cancelled", trade_id=tid)
    a2 = await connect_client("Ilias", uid=a.uid)
    await a2.send(type="trade_status", trade_id=tid)
    assert (await a2.expect("trade_status", trade_id=tid))["state"] == "failed"


async def test_trade_status_complete_and_unknown(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await _execute(a, b)
    await a.send(type="trade_done", trade_id=tid, ok=True)
    await b.send(type="trade_done", trade_id=tid, ok=True)
    await a.expect("trade_complete", trade_id=tid)
    await a.send(type="trade_status", trade_id=tid)
    assert (await a.expect("trade_status", trade_id=tid))["state"] == "complete"
    await a.send(type="trade_status", trade_id="nope")
    assert (await a.expect("trade_status", trade_id="nope"))["state"] == "unknown"
