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


async def upload(c, st):
    await c.send(type="pc_put", store=st)
    await c.expect("pc_saved", rev=st["rev"])


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
    assert da["remove"] == ["k1"] and da["add"][0]["id"] == "c1" and da["rev"] == 4
    assert db["remove"] == ["c1"] and db["add"][0]["dex"] == 65 and db["add"][0]["nick"] == "ALAKAZAM"
    rec = base64.b64decode(db["add"][0]["gen1"])
    assert rec[0] == 0x95                                     # Alakazam's Gen-1 index
    done = await b.expect("trade_complete", trade_id=tid)
    assert done["evolved"] == "ALAKAZAM" and done["gave"]["id"] == "c1"
    await a.expect("system", timeout=3, text="Ilias traded KADABRA with Alex for CYNDA!")
    sa = server._pc_load(a.uid)
    assert [e["id"] for e in sa["boxes"][0]] == ["p1", "c1"] and "k1" in sa["gone"]
    # an old upload that doesn't know about the trade is refused with the deltas to apply first
    await a.send(type="pc_put", store=store(4, g1_entry("k1", 0x26, "KADABRA", 64)))
    again = await a.expect("pc_trades")
    assert again["deltas"][0]["trade_id"] == tid
    # after applying (trade id listed), the upload is accepted and the inbox is cleared
    fixed = store(5, g1_entry("p1", 0x54, "SPARKY", 25), da["add"][0])
    fixed["trades"] = [tid]
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
