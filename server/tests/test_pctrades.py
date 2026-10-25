"""Trades and gifts between players' stored PC databases (SPEC.md: trade state machine, protocol 2)."""
import base64

from yellowlink_server import pcstore
from test_server import open_trade


def g1_entry(eid, species, nick, dex=0):
    rec = bytearray(55)
    rec[0], rec[3] = species, 20
    rec[44:55] = pcstore._encode(nick)
    return {"id": eid, "origin": "yellow", "dex": dex, "level": 20, "nick": nick, "ot": "ASH", "otid": 1,
            "gen1": base64.b64encode(bytes(rec)).decode(), "gen2": None}


def g2_entry(eid, dex, nick, item=0):
    rec = bytearray(55)
    rec[0], rec[1], rec[31] = dex, item, 20
    rec[43:54] = pcstore._encode(nick)
    return {"id": eid, "origin": "crystal", "dex": dex, "level": 20, "nick": nick, "ot": "KRIS", "otid": 2,
            "gen1": None, "gen2": base64.b64encode(bytes(rec)).decode()}


def store(rev, *entries, full=False):
    st = pcstore.empty_store()
    st["rev"] = rev
    st["boxes"][0] = list(entries)
    if full:
        for b in st["boxes"]:
            while len(b) < 20:
                b.append(g1_entry(f"f{id(b)}{len(b)}", 0x99, "FILL"))
    return st


BASES = {}


async def upload(c, st):
    """Compare-and-swap upload based on the last server revision this test client saw."""
    await c.send(type="pc_put", store=st, base=BASES.get(c.uid, 0))
    BASES[c.uid] = (await c.expect("pc_saved"))["srv_rev"]


async def test_cross_game_trade_with_evolution(server, connect_client):
    a = await connect_client("Ilias")          # Yellow player
    b = await connect_client("Alex")           # Crystal player
    await upload(a, store(3, g1_entry("k1", 0x26, "KADABRA", 64), g1_entry("p1", 0x54, "SPARKY", 25)))
    await upload(b, store(5, g2_entry("c1", 155, "CYNDA")))
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, entry_id="k1")
    off = await b.expect("trade_offer")
    assert off["entry"]["nick"] == "KADABRA" and off["from"] == a.uid
    await b.send(type="trade_offer", trade_id=tid, entry_id="c1")
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.send(type="trade_confirm", trade_id=tid)
    da = (await a.expect("pc_trades"))["deltas"][0]
    db = (await b.expect("pc_trades"))["deltas"][0]
    assert da["remove"] == ["k1"] and da["add"][0]["id"] == "c1" and da["srv_rev"] == 2
    assert db["remove"] == ["c1"] and db["add"][0]["dex"] == 65 and db["add"][0]["nick"] == "ALAKAZAM"
    rec = base64.b64decode(db["add"][0]["gen1"])
    assert rec[0] == 0x95                                     # Alakazam's Gen-1 index
    done = await b.expect("trade_complete", trade_id=tid)
    assert done["evolved"] == "ALAKAZAM" and done["gave"]["id"] == "c1"
    await a.expect("system", timeout=3, text="Ilias traded KADABRA with Alex for CYNDA!")
    sa = server._pc_load(a.uid)
    assert [e["id"] for e in sa["boxes"][0]] == ["p1", "c1"] and "k1" in sa["srv_gone"]
    # an old upload that doesn't know about the trade is refused with the deltas to apply first
    await a.send(type="pc_put", store=store(4, g1_entry("k1", 0x26, "KADABRA", 64)), base=1)
    again = await a.expect("pc_trades")
    assert again["deltas"][0]["trade_id"] == tid
    # listing the trade but still holding the given-away entry (a buggy client): refused as a conflict
    bad = store(5, g1_entry("k1", 0x26, "KADABRA", 64), da["add"][0])
    bad["trades"] = [tid]
    await a.send(type="pc_put", store=bad, base=da["srv_rev"])
    await a.expect("pc_conflict")
    # after applying properly, the upload is accepted and the inbox is cleared
    fixed = store(5, g1_entry("p1", 0x54, "SPARKY", 25), da["add"][0])
    fixed["trades"] = [tid]
    BASES[a.uid] = da["srv_rev"]
    await upload(a, fixed)
    assert server._pc_load(a.uid)["inbox"] == []


async def test_gift_one_way(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await upload(a, store(1, g2_entry("o1", 95, "ONIX", item=pcstore.METAL_COAT), g2_entry("x", 16, "PIDGEY")))
    await a.send(type="trade_request", to=b.uid, gift=True)
    req = await b.expect("trade_request")
    assert req["gift"] is True
    await b.send(type="trade_respond", trade_id=req["trade_id"], accept=True)
    st = await b.expect("trade_started")
    assert st["gift"] and st["giver"] == a.uid
    tid = req["trade_id"]
    await b.send(type="trade_offer", trade_id=tid, entry_id="x")
    assert "gift" in (await b.expect("error"))["message"]
    await b.send(type="trade_confirm", trade_id=tid)
    assert "giver" in (await b.expect("error"))["message"]
    await a.send(type="trade_offer", trade_id=tid, entry_id="o1")
    await b.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.send(type="trade_confirm", trade_id=tid)
    got = (await b.expect("pc_trades"))["deltas"][0]
    assert got["remove"] == [] and got["add"][0]["dex"] == 208          # Onix + Metal Coat -> Steelix
    assert base64.b64decode(got["add"][0]["gen2"])[1] == 0              # coat used up
    assert (await a.expect("pc_trades"))["deltas"][0]["remove"] == ["o1"]
    await b.expect("system", timeout=3, text="Ilias gave ONIX to Alex!")
    assert server._pc_load(b.uid)["boxes"][0][0]["nick"] == "STEELIX"   # receiver had no PC yet: created


async def test_offer_must_be_in_saved_pc(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, entry_id="nope")
    assert "saved PC" in (await a.expect("error"))["message"]


async def test_full_pc_cancels(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await upload(a, store(1, g1_entry("p1", 0x54, "SPARKY", 25)))
    await upload(b, store(1, full=True))
    await a.send(type="trade_request", to=b.uid, gift=True)
    req = await b.expect("trade_request")
    await b.send(type="trade_respond", trade_id=req["trade_id"], accept=True)
    await a.send(type="trade_offer", trade_id=req["trade_id"], entry_id="p1")
    await b.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=req["trade_id"])
    await b.send(type="trade_confirm", trade_id=req["trade_id"])
    c = await a.expect("trade_cancelled")
    assert "full" in c["reason"]
    assert [e["id"] for e in server._pc_load(a.uid)["boxes"][0]] == ["p1"]     # nothing changed


async def test_receiver_offline_gets_delta_on_reconnect(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex", uid="alex-uid")
    await upload(a, store(1, g1_entry("p1", 0x54, "SPARKY", 25)))
    await a.send(type="trade_request", to=b.uid, gift=True)
    req = await b.expect("trade_request")
    await b.send(type="trade_respond", trade_id=req["trade_id"], accept=True)
    await a.send(type="trade_offer", trade_id=req["trade_id"], entry_id="p1")
    await b.expect("trade_offer")
    await b.send(type="trade_confirm", trade_id=req["trade_id"])
    await a.send(type="trade_confirm", trade_id=req["trade_id"])
    await a.expect("trade_complete")
    await b.close()                                   # Alex drops before applying it
    b2 = await connect_client("Alex", uid="alex-uid")
    await b2.send(type="pc_get")
    await b2.expect("pc_data")
    d = (await b2.expect("pc_trades"))["deltas"]
    assert d[0]["trade_id"] == req["trade_id"] and d[0]["add"][0]["nick"] == "SPARKY"


def test_evolution_rules():
    e, name = pcstore.evolve_for_trade(g2_entry("e", 64, "PSYCHO"))          # nicknamed: name kept
    assert name == "ALAKAZAM" and e["dex"] == 65 and e["nick"] == "PSYCHO"
    e, name = pcstore.evolve_for_trade(g2_entry("e", 64, "KADABRA", item=pcstore.EVERSTONE))
    assert name is None and e["dex"] == 64
    e, name = pcstore.evolve_for_trade(g2_entry("e", 95, "ONIX"))           # no Metal Coat
    assert name is None
    e, name = pcstore.evolve_for_trade(g1_entry("e", 0x99, "BULBASAUR", 1))
    assert name is None


async def test_crash_between_the_two_saves_is_finished_on_restart(server, connect_client, monkeypatch):
    """The journal makes a trade all-or-nothing even if the server dies between saving the two PCs."""
    from yellowlink_server.config import Config
    from yellowlink_server.server import YellowLinkServer
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await upload(a, store(1, g1_entry("p1", 0x54, "SPARKY", 25)))
    await upload(b, store(1, g1_entry("r1", 0xA5, "RATTY", 19)))
    saves = []
    real_save = server._pc_save

    def crash_on_second(uid, st):
        saves.append(uid)
        if len(saves) == 2:
            raise OSError("disk died")
        real_save(uid, st)
    monkeypatch.setattr(server, "_pc_save", crash_on_second)
    import asyncio
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s, *a, **k: real_sleep(0 if s == 5 else s, *a, **k))
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, entry_id="p1")
    await b.expect("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, entry_id="r1")
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.send(type="trade_confirm", trade_id=tid)
    await a.expect("pc_trades", timeout=5)
    # the retry finished it; and a fresh server replaying a leftover journal is a no-op (idempotent)
    ids = lambda u: sorted(e["id"] for bx in server._pc_load(u)["boxes"] for e in bx)  # noqa: E731
    assert ids(a.uid) == ["r1"] and ids(b.uid) == ["p1"]
    srv2 = YellowLinkServer(Config(host="127.0.0.1", port=0, key="k", data_dir=server.config.data_dir))
    srv2._journal_write({"trade_id": tid, "a": a.uid, "b": b.uid, "gives": {}, "evolved": {},
                         "deltas": {a.uid: {"remove": ["p1"], "add": []}, b.uid: {"remove": [], "add": []}}})
    srv2._replay_journals()
    assert ids(a.uid) == ["r1"] and ids(b.uid) == ["p1"]


async def test_trade_status_after_losing_the_connection(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await upload(a, store(1, g1_entry("p1", 0x54, "SPARKY", 25)))
    await a.send(type="trade_request", to=b.uid, gift=True)
    req = await b.expect("trade_request")
    await b.send(type="trade_respond", trade_id=req["trade_id"], accept=True)
    await a.send(type="trade_offer", trade_id=req["trade_id"], entry_id="p1")
    await b.expect("trade_offer")
    await a.send(type="trade_status", trade_id=req["trade_id"])
    assert (await a.expect("trade_status"))["state"] == "pending"
    await a.send(type="trade_confirm", trade_id=req["trade_id"])
    await b.send(type="trade_confirm", trade_id=req["trade_id"])
    await a.expect("trade_complete")
    await a.send(type="trade_status", trade_id=req["trade_id"])
    assert (await a.expect("trade_status"))["state"] == "complete"
    await a.send(type="trade_status", trade_id="nonexistent")
    assert (await a.expect("trade_status"))["state"] == "failed"


async def test_unreadable_store_is_never_overwritten_by_a_gift(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await upload(a, store(1, g1_entry("p1", 0x54, "SPARKY", 25)))
    with open(server._pc_path(b.uid), "w") as fh:
        fh.write("{ broken")
    await a.send(type="trade_request", to=b.uid, gift=True)
    req = await b.expect("trade_request")
    await b.send(type="trade_respond", trade_id=req["trade_id"], accept=True)
    await a.send(type="trade_offer", trade_id=req["trade_id"], entry_id="p1")
    await b.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=req["trade_id"])
    await b.send(type="trade_confirm", trade_id=req["trade_id"])
    c = await a.expect("trade_cancelled")
    assert "couldn't be read" in c["reason"]
    assert open(server._pc_path(b.uid)).read() == "{ broken"


async def test_damaged_entry_cannot_be_offered(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bad = g1_entry("p1", 0x54, "SPARKY", 25)
    bad["gen1"] = "!!notbase64"
    await upload(a, store(1, bad))
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, entry_id="p1")
    assert "damaged" in (await a.expect("error"))["message"]


async def test_cancel_after_finish_is_quiet(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(type="trade_cancel", trade_id="gone")
    await a.expect_none("error")


async def test_gift_several_and_uneven_trade(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    BASES.pop(a.uid, None)
    BASES.pop(b.uid, None)
    await upload(a, store(1, g1_entry("p1", 0x54, "SPARKY", 25), g1_entry("r1", 0xA5, "RATTY", 19),
                          g1_entry("k1", 0x26, "KADABRA", 64), g1_entry("x1", 0x24, "PIDGEY", 16)))
    await upload(b, store(1, g2_entry("c1", 155, "CYNDA")))
    # gift three at once
    await a.send(type="trade_request", to=b.uid, gift=True)
    req = await b.expect("trade_request")
    tid = req["trade_id"]
    await b.send(type="trade_respond", trade_id=tid, accept=True)
    await a.send(type="trade_offer", trade_id=tid, entry_ids=["p1", "r1", "k1"])
    off = await b.expect("trade_offer")
    assert [e["id"] for e in off["entries"]] == ["p1", "r1", "k1"] and off["entry"]["id"] == "p1"
    await a.send(type="trade_confirm", trade_id=tid)
    await b.send(type="trade_confirm", trade_id=tid)
    got = (await b.expect("pc_trades"))["deltas"][0]
    assert [e["nick"] for e in got["add"]] == ["SPARKY", "RATTY", "ALAKAZAM"]
    done = await b.expect("trade_complete")
    assert len(done["received_all"]) == 3 and done["evolved_all"] == ["ALAKAZAM"]
    await b.expect("system", timeout=3, text="Ilias gave SPARKY, RATTY and KADABRA to Alex!")
    assert (await a.expect("pc_trades"))["deltas"][0]["remove"] == ["p1", "r1", "k1"]
    # uneven trade: 1 for 2 (Alex offers two of the gifted ones back for Ilias's Pidgey)
    tid = await open_trade(b, a)
    await b.send(type="trade_offer", trade_id=tid, entry_ids=["p1", "r1"])
    await a.expect("trade_offer")
    await a.send(type="trade_offer", trade_id=tid, entry_ids=["x1"])
    await b.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.send(type="trade_confirm", trade_id=tid)
    await a.expect("trade_complete", trade_id=tid)
    ids = lambda u: sorted(e["id"] for bx in server._pc_load(u)["boxes"] for e in bx)  # noqa: E731
    assert len(ids(a.uid)) == 2 and len(ids(b.uid)) == 3          # 4-3+2-1 = 2 ; 1+3-2+1 = 3
    # an empty list withdraws the offer; duplicates/too many are refused
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, entry_ids=["x1", "x1"])
    await a.expect("error")
    await a.send(type="trade_offer", trade_id=tid, entry_ids=[f"z{i}" for i in range(21)])
    await a.expect("error")
