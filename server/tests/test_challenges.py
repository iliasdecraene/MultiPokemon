import asyncio
import json
import os

from yellowlink_server import challenges as chal


async def start_challenge(a, b, game="yellow", rules=None, cid=None):
    msg = dict(type="challenge_request", to=b.uid)
    if cid:
        msg["challenge_id"] = cid
    else:
        msg.update(kind="soul_link", game=game, rules=rules or {"dupes": True, "shiny": False})
    await a.send(**msg)
    req = await b.expect("challenge_request")
    assert req["from"] == a.uid and req["kind"] == "soul_link" and req["challenge_id"] == cid
    await b.send(type="challenge_respond", request_id=req["request_id"], accept=True)
    ga = await a.expect("challenge_go")
    gb = await b.expect("challenge_go")
    assert ga["challenge"]["id"] == gb["challenge"]["id"]
    return ga["challenge"]


async def join_both(a, b, cid):
    await a.send(type="challenge_join", challenge_id=cid)
    st = await a.expect("challenge_state")
    assert st["challenge"]["id"] == cid and "ledger" in st["challenge"]
    await a.expect_none("challenge_ready", wait=0.1)
    await b.send(type="challenge_join", challenge_id=cid)
    await b.expect("challenge_state")
    await a.expect("challenge_ready", challenge_id=cid)
    await b.expect("challenge_ready", challenge_id=cid)


async def test_request_accept_creates_record(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b, rules={"dupes": False})
    assert c["players"] == [a.uid, b.uid] and c["status"] == "active" and c["game"] == "yellow"
    assert c["rules"] == {"dupes": False, "shiny": True}
    assert c["names"] == {a.uid: "Ilias", b.uid: "Alex"}
    # it's in both players' lists when they reconnect
    await a.close()
    a2 = await connect_client("Ilias", uid=a.uid)
    lst = await a2.expect("challenge_list")
    assert [x["id"] for x in lst["challenges"]] == [c["id"]]


async def test_decline_and_validation(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="yellow")
    req = await b.expect("challenge_request")
    await b.send(type="challenge_respond", request_id=req["request_id"], accept=False)
    d = await a.expect("challenge_declined")
    assert d["reason"] == "declined" and d["name"] == "Alex"
    await a.send(type="challenge_request", to=b.uid, kind="nope", game="yellow")
    assert "type" in (await a.expect("error"))["message"]
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="red")
    assert "Yellow" in (await a.expect("error"))["message"]
    # the partner doesn't have Crystal
    await b.send(type="library", games=["yellow"])
    await asyncio.sleep(0.1)
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="crystal")
    assert "Alex doesn't have" in (await a.expect("error"))["message"]
    roster = server.roster()
    assert next(e for e in roster if e["uid"] == b.uid)["games"] == ["yellow"]


async def test_one_open_challenge_per_pair_and_continue(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    await a.send(type="challenge_leave", challenge_id=c["id"], reason="paused")
    left = await b.expect("challenge_partner_left")
    assert left["name"] == "Ilias" and left["reason"] == "paused"
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="yellow")
    assert "already have" in (await a.expect("error"))["message"]
    c2 = await start_challenge(b, a, cid=c["id"])          # continue, requested by the other player
    assert c2["id"] == c["id"]
    await join_both(a, b, c["id"])


async def test_busy_players_cannot_trade_or_be_challenged(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    x = await connect_client("Misty")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    await x.send(type="trade_request", to=a.uid)
    assert "busy" in (await x.expect("error"))["message"]
    await x.send(type="challenge_request", to=b.uid, kind="soul_link", game="yellow")
    assert "busy" in (await x.expect("error"))["message"]
    # chat and roster show they're in a Soul Link
    await a.send(type="chat", text="hi")
    assert (await x.expect("chat", text="hi"))["challenge"] == "soul_link"
    assert next(e for e in server.roster() if e["uid"] == a.uid)["challenge"] == "soul_link"


async def test_events_are_logged_and_echoed(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    await a.send(type="challenge_event", challenge_id=c["id"],
                 event={"t": "enc", "area": "ROUTE_1", "dex": 16, "status": "open", "evil": 1})
    ea = await a.expect("challenge_event")
    eb = await b.expect("challenge_event")
    assert ea == eb and ea["event"] == {"seq": 1, "p": a.uid, "ts": ea["event"]["ts"], "t": "enc",
                                       "area": "ROUTE_1", "dex": 16, "status": "open"}
    await b.send(type="challenge_event", challenge_id=c["id"], event={"t": "dead", "key": "00ab12"})
    assert (await a.expect("challenge_event"))["event"]["seq"] == 2
    for bad in ({"t": "boom"}, {"t": "enc", "area": "x" * 99, "status": "open"},
                {"t": "loc", "key": "k", "where": "moon"}, "nope"):
        await a.send(type="challenge_event", challenge_id=c["id"], event=bad)
        await a.expect("error")
    # the full ledger comes back on (re)join
    await a.send(type="challenge_join", challenge_id=c["id"])
    st = await a.expect("challenge_state")
    assert [e["seq"] for e in st["challenge"]["ledger"]] == [1, 2]
    assert st["challenge"]["stats"][b.uid]["dead"] == 1


async def test_disconnect_grace_then_partner_left(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "GRACE", 0.3)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    await a.close()
    lost = await b.expect("challenge_partner_lost")
    assert lost["name"] == "Ilias"
    left = await b.expect("challenge_partner_left", timeout=3)
    assert left["reason"] == "disconnected"


async def test_reconnect_within_grace_keeps_session(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "GRACE", 1.0)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    await a.close()
    await b.expect("challenge_partner_lost")
    a2 = await connect_client("Ilias", uid=a.uid)
    await a2.send(type="challenge_join", challenge_id=c["id"])
    await a2.expect("challenge_state")
    await b.expect("challenge_ready")
    await b.expect_none("challenge_partner_left", wait=1.2)


async def test_partner_never_loads_in(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "LOAD_TIMEOUT", 0.3)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await a.send(type="challenge_join", challenge_id=c["id"])
    left = await a.expect("challenge_partner_left", timeout=3)
    assert left["reason"] == "didn't load in" and left["name"] == "Alex"
    # a late join is sent home instead of being stuck alone
    await b.send(type="challenge_join", challenge_id=c["id"])
    await b.expect("challenge_partner_left")


async def test_hall_of_fame_completes_and_blackout_fails(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    await a.send(type="challenge_event", challenge_id=c["id"], event={"t": "hof"})
    u = await b.expect("challenge_update")
    assert u["challenge"]["status"] == "active" and u["challenge"]["progress"][a.uid]["hof"]
    await b.send(type="challenge_event", challenge_id=c["id"], event={"t": "hof"})
    u = await a.expect("challenge_update", timeout=3)
    while u["challenge"]["status"] != "completed":
        u = await a.expect("challenge_update")
    await b.expect("system", text="Ilias and Alex completed a Soul Link!")
    assert c["id"] not in server.challenges.sessions
    # a new one can be started after completion
    c2 = await start_challenge(a, b, game="yellow")
    assert c2["id"] != c["id"]
    await join_both(a, b, c2["id"])
    await b.send(type="challenge_event", challenge_id=c2["id"], event={"t": "blackout"})
    u = await a.expect("challenge_update")
    while u["challenge"]["status"] != "failed":
        u = await a.expect("challenge_update")
    assert u["challenge"]["reason"] == "Alex blacked out"
    await a.expect_none("challenge_partner_left", wait=0.2)
    assert c2["id"] not in server.challenges.sessions


async def test_abandon_and_progress_and_persistence(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b, game="crystal")
    await join_both(a, b, c["id"])
    await a.send(type="challenge_progress", challenge_id=c["id"], badges=0x0103)
    u = await b.expect("challenge_update")
    assert u["challenge"]["progress"][a.uid]["badges"] == 0x0103
    await b.send(type="challenge_abandon", challenge_id=c["id"])
    left = await a.expect("challenge_partner_left")
    assert left["reason"] == "abandoned"
    await server.challenges.flush()
    with open(os.path.join(server.config.data_dir, "challenges.json")) as fh:
        saved = json.load(fh)["challenges"]
    assert saved[0]["status"] == "abandoned" and saved[0]["reason"] == "abandoned by Alex"


async def test_state_with_challenge_does_not_change_main_region(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(type="state", map=1, x=1, y=1, px=16, py=16, facing=0, img=0, visible=True, game="yellow")
    await asyncio.sleep(0.1)
    await a.send(type="state", map=1, x=1, y=1, px=16, py=16, facing=0, img=0, visible=True, game="crystal",
                 challenge="abc123")
    await asyncio.sleep(0.1)
    assert server.store.get(a.uid)["game"] == "yellow"
    assert server.clients[a.uid].state["challenge"] == "abc123"


async def test_shiny_area_and_resend_dedupe(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    ev = {"t": "enc", "area": "ROUTE_22*1", "dex": 21, "status": "caught", "key": "abc", "shiny": True, "cid": "x1"}
    await a.send(type="challenge_event", challenge_id=c["id"], event=ev)
    first = await a.expect("challenge_event")
    assert first["event"]["area"] == "ROUTE_22*1" and first["event"]["cid"] == "x1"
    await b.expect("challenge_event")
    await a.send(type="challenge_event", challenge_id=c["id"], event=ev)        # resend after a blip
    again = await a.expect("challenge_event")
    assert again["event"]["seq"] == first["event"]["seq"]
    await b.expect_none("challenge_event", wait=0.2)
    assert len(server.challenges.records[c["id"]]["ledger"]) == 1


async def test_disconnect_while_loading_is_not_lost(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "GRACE", 0.2)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await start_challenge(a, b)
    await a.close()                       # the app restarts to load the run
    await b.send(type="challenge_join", challenge_id=c["id"])
    await b.expect("challenge_state")
    await b.expect_none("challenge_partner_lost", wait=0.2)
    await b.expect_none("challenge_partner_left", wait=0.3)
    a2 = await connect_client("Ilias", uid=a.uid)
    await a2.send(type="challenge_join", challenge_id=c["id"])
    await b.expect("challenge_ready")


async def test_damaged_record_is_skipped(tmp_path):
    from yellowlink_server.challenges import ChallengeHub
    d = tmp_path / "data"
    d.mkdir()
    (d / "challenges.json").write_text(json.dumps({"challenges": [{"id": "bad"}, {
        "id": "ok", "kind": "soul_link", "game": "yellow", "status": "active", "players": ["a", "b"],
        "names": {}, "rules": {}, "progress": {}, "ledger": []}]}))
    hub = ChallengeHub(None, str(d))
    assert list(hub.records) == ["ok"]
