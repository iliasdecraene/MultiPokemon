"""Trade evolutions inside a Soul Link run (SPEC.md section 7, "Trade evolutions in a run")."""
import asyncio

from yellowlink_server import challenges as chal

from test_challenges import join_both, start_challenge


async def run_with_caps(connect_client, a_caps=("exp_share", "trade_evo"), b_caps=("exp_share", "trade_evo"),
                        game="yellow"):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="library", games=["yellow", "crystal"], challenge_rules=list(a_caps))
    await b.send(type="library", games=["yellow", "crystal"], challenge_rules=list(b_caps))
    await asyncio.sleep(0.05)
    c = await start_challenge(a, b, game=game)
    await join_both(a, b, c["id"])
    return a, b, c["id"]


def ask(cid, evo_id="e1", key="1234abcd5678", dex=64, into=65, nick="KADABRA", item=None):
    return dict(type="challenge_evo_request", challenge_id=cid, evo_id=evo_id, key=key, dex=dex, into=into,
                nick=nick, item=item)


async def test_feature_is_announced(server, connect_client):
    a = await connect_client("Ilias")
    assert "trade_evo" in a.welcome["features"]


async def test_full_flow_writes_the_ledger_and_tells_both(server, connect_client):
    a, b, cid = await run_with_caps(connect_client, game="crystal")
    await a.send(**ask(cid, item=None))
    req = await b.expect("challenge_evo_request")
    assert req == {"type": "challenge_evo_request", "challenge_id": cid, "evo_id": "e1", "from": a.uid,
                   "name": "Ilias", "key": "1234abcd5678", "dex": 64, "into": 65, "nick": "KADABRA", "item": None}
    await a.expect_none("challenge_evo_go", wait=0.1)
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    await a.expect("challenge_evo_go", challenge_id=cid, evo_id="e1")
    await b.expect_none("challenge_evo_go", wait=0.05)
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=True)
    ea = await a.expect("challenge_event")
    eb = await b.expect("challenge_event")
    assert ea == eb
    ev = ea["event"]
    assert ev["t"] == "evo" and ev["p"] == a.uid and ev["key"] == "1234abcd5678" and ev["dex"] == 64 \
        and ev["into"] == 65 and ev["nick"] == "KADABRA" and ev["evo"] == "e1" and "item" not in ev
    for c in (a, b):
        end = await c.expect("challenge_evo_end")
        assert end["ok"] is True and end["evo_id"] == "e1"
    # the ledger keeps it (comes back on rejoin), the run's stats don't count it as a catch or a death
    rec = server.challenges.records[cid]
    assert rec["ledger"][-1]["t"] == "evo"
    summary = server.challenges.summary(rec)
    assert summary["stats"][a.uid] == {"caught": 0, "dead": 0}
    # a second one may start now (Crystal item case, the item name is recorded)
    await a.send(**ask(cid, evo_id="e2", key="aa", dex=95, into=208, nick="ROCKY", item="Metal Coat"))
    await b.expect("challenge_evo_request", evo_id="e2")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e2", accept=True)
    await a.expect("challenge_evo_go", evo_id="e2")
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e2", ok=True)
    ev = (await b.expect("challenge_event"))["event"]
    assert ev["item"] == "Metal Coat" and ev["into"] == 208 and ev["nick"] == "ROCKY"


async def test_clients_cannot_post_evo_events_themselves(server, connect_client):
    a, b, cid = await run_with_caps(connect_client)
    await a.send(type="challenge_event", challenge_id=cid, event={"t": "evo", "key": "k", "dex": 64, "into": 65})
    assert "unknown event type" in (await a.expect("error"))["message"]


async def test_decline_and_no_answer(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "EVO_COOLDOWN", 0.0)
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=False)
    for c in (a, b):
        end = await c.expect("challenge_evo_end")
        assert end["ok"] is False and end["reason"] == "declined"
    # a late answer / done for it is stale: ignored (no red error)
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    await a.expect_none("challenge_evo_go", wait=0.1)
    await b.expect_none("error", wait=0.0)
    monkeypatch.setattr(chal, "EVO_ASK_TIMEOUT", 0.2)
    await a.send(**ask(cid, evo_id="e2"))
    await b.expect("challenge_evo_request", evo_id="e2")
    for c in (a, b):
        end = await c.expect("challenge_evo_end", evo_id="e2", timeout=2)
        assert end["reason"] == "no answer"
    assert not server.challenges.evos


async def test_cancel_before_answer_and_failure_after_go(server, connect_client):
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=False)
    assert (await b.expect("challenge_evo_end"))["reason"] == "cancelled"
    await a.expect("challenge_evo_end")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)   # too late: ignored
    await a.expect_none("challenge_evo_go", wait=0.1)
    await a.send(**ask(cid, evo_id="e2"))
    await b.expect("challenge_evo_request", evo_id="e2")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e2", accept=True)
    await a.expect("challenge_evo_go", evo_id="e2")
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e2", ok=False, why="it's not in your party")
    assert (await b.expect("challenge_evo_end", evo_id="e2"))["reason"] == "it's not in your party"
    assert not any(e["t"] == "evo" for e in server.challenges.records[cid]["ledger"])


async def test_done_after_go_times_out(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "EVO_GO_TIMEOUT", 0.2)
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    await a.expect("challenge_evo_go")
    assert (await b.expect("challenge_evo_end", timeout=2))["reason"] == "timed out"
    await a.expect("challenge_evo_end")
    # a late done: answered "expired" to the sender only, no ledger entry, no error
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=True)
    end = await a.expect("challenge_evo_end")
    assert end["reason"] == "expired" and end["ok"] is False
    await b.expect_none("challenge_evo_end", wait=0.1)
    assert not any(e["t"] == "evo" for e in server.challenges.records[cid]["ledger"])


async def test_done_before_go_is_refused(server, connect_client):
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=True)
    assert "answer" in (await a.expect("error"))["message"]
    # the partner can't finish it either
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    await a.expect("challenge_evo_go")
    await b.send(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=True)
    await b.expect("challenge_evo_end", reason="expired")
    assert "evo" not in [e["t"] for e in server.challenges.records[cid]["ledger"]]


async def test_validation(server, connect_client):
    a, b, cid = await run_with_caps(connect_client)
    for bad, words in ((dict(dex=25, into=26), "trade"),           # Pikachu doesn't evolve by trade
                       (dict(dex=0), "dex"), (dict(into=999), "into"), (dict(dex=True), "dex"),
                       (dict(key="no spaces!"), "key"), (dict(evo_id="x" * 30), "evo_id"),
                       (dict(nick="A" * 20), "nick"), (dict(nick="\x00bad"), "nick"),
                       (dict(item="x" * 30), "item")):
        await a.send(**{**ask(cid), **bad})
        assert words in (await a.expect("error"))["message"], bad
    await b.expect_none("challenge_evo_request", wait=0.05)
    # one at a time: the second one ends right away for its sender (no red error)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await b.send(**ask(cid, evo_id="e9", dex=67, into=68))
    end = await b.expect("challenge_evo_end", evo_id="e9")
    assert end["ok"] is False and end["reason"] == "busy"
    await a.expect_none("challenge_evo_end", wait=0.05)
    await b.expect_none("error", wait=0.0)
    # not my run
    x = await connect_client("Misty")
    await x.send(**ask(cid))
    assert "unknown challenge" in (await x.expect("error"))["message"]


async def test_partner_needs_a_new_client(server, connect_client):
    a, b, cid = await run_with_caps(connect_client, b_caps=("exp_share",))
    await a.send(**ask(cid))
    end = await a.expect("challenge_evo_end", evo_id="e1")
    assert end["ok"] is False and end["reason"] == "Alex needs a newer YellowLink (press U to update)"
    await b.expect_none("challenge_evo_request", wait=0.05)
    assert not server.challenges.evos


async def test_both_must_be_in_the_run(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "GRACE", 5.0)
    a, b, cid = await run_with_caps(connect_client)
    await b.close()
    await a.expect("challenge_partner_lost")
    await a.send(**ask(cid))
    assert (await a.expect("challenge_evo_end", evo_id="e1"))["reason"] == "not both in the run"
    # partner back (within the grace time): fine again; then the requester drops before the answer
    b2 = await connect_client("Alex", uid=b.uid)
    await b2.send(type="library", games=["yellow"], challenge_rules=["trade_evo"])
    await b2.send(type="challenge_join", challenge_id=cid)
    await a.expect("challenge_ready")
    await a.send(**ask(cid))
    await b2.expect("challenge_evo_request")
    await a.close()
    await b2.expect("challenge_partner_lost")
    await b2.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    assert (await b2.expect("challenge_evo_end"))["reason"] == "not both in the run"


async def test_session_end_ends_it(server, connect_client):
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    await a.expect("challenge_evo_go")
    await b.send(type="challenge_leave", challenge_id=cid, reason="paused")
    assert (await a.expect("challenge_evo_end"))["reason"] == "session over"
    await a.expect("challenge_partner_left")
    assert not server.challenges.evos
    # outside a session: refused
    await a.send(**ask(cid, evo_id="e2"))
    assert (await a.expect("challenge_evo_end", evo_id="e2"))["reason"] == "not both in the run"
    await a.expect_none("error", wait=0.05)


# --- review fixes ---------------------------------------------------------------------------------------------
async def test_pairs_follow_the_run_game(server, connect_client):
    """Yellow runs only know the four Gen-1 trade evolutions, Crystal the Gen-2 ones; Platinum all of them."""
    a, b, cid = await run_with_caps(connect_client)                 # a Yellow run
    await a.send(**ask(cid, dex=95, into=208, item="Metal Coat"))  # Onix -> Steelix doesn't exist in Yellow
    assert "trade" in (await a.expect("error"))["message"]
    await a.send(**ask(cid, dex=125, into=466))                     # Electabuzz -> Electivire: Gen 4 only
    assert "trade" in (await a.expect("error"))["message"]
    assert chal.trade_evo_pairs("crystal") >= {(95, 208), (117, 230), (64, 65)}
    assert (125, 466) not in chal.trade_evo_pairs("crystal")
    assert {(125, 466), (366, 367), (366, 368), (95, 208)} <= chal.trade_evo_pairs("platinum")
    assert chal.trade_evo_pairs("yellow") == {(64, 65), (67, 68), (75, 76), (93, 94)}


async def test_partner_busy_and_cooldown(server, connect_client, monkeypatch):
    monkeypatch.setattr(chal, "EVO_COOLDOWN", 0.4)
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=False, busy=True)
    assert (await a.expect("challenge_evo_end"))["reason"] == "partner busy"
    # asking again right away: a quiet "wait a moment" (no request reaches the partner, no red error)
    await a.send(**ask(cid, evo_id="e2"))
    end = await a.expect("challenge_evo_end", evo_id="e2")
    assert end["ok"] is False and end["reason"] == "wait a moment"
    await b.expect_none("challenge_evo_request", evo_id="e2", wait=0.05)
    await a.expect_none("error", wait=0.0)
    # the partner isn't held up by it
    await b.send(**ask(cid, evo_id="f1"))
    await a.expect("challenge_evo_request", evo_id="f1")
    await a.send(type="challenge_evo_respond", challenge_id=cid, evo_id="f1", accept=False)
    await b.expect("challenge_evo_end", evo_id="f1")
    await asyncio.sleep(0.45)
    await a.send(**ask(cid, evo_id="e3"))
    await b.expect("challenge_evo_request", evo_id="e3")


async def test_giving_up_ends_it_quietly(server, connect_client):
    """The requester stops waiting (no answer seen / reconnect): done ok:false ends it for both right away."""
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await a.send(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=False, why="no answer")
    assert (await b.expect("challenge_evo_end"))["reason"] == "no answer"
    assert not server.challenges.evos
    await a.send(**ask(cid, evo_id="e2"))                          # no cooldown after giving up
    await b.expect("challenge_evo_request", evo_id="e2")


async def test_done_twice_gives_one_event_and_one_end(server, connect_client):
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await b.send(type="challenge_evo_respond", challenge_id=cid, evo_id="e1", accept=True)
    await a.expect("challenge_evo_go")
    done = dict(type="challenge_evo_done", challenge_id=cid, evo_id="e1", ok=True)
    await a.send(**done)
    await a.send(**done)                                           # a resend right behind it
    await asyncio.sleep(0.3)
    assert [e["t"] for e in server.challenges.records[cid]["ledger"]].count("evo") == 1
    ends_b = [m for m in b.inbox if m.get("type") == "challenge_evo_end"]
    assert len(ends_b) == 1 and ends_b[0]["ok"] is True
    ends_a = [m for m in a.inbox if m.get("type") == "challenge_evo_end"]
    assert [m["reason"] for m in ends_a] == ["evolved", "expired"]


async def test_new_session_ends_an_open_one(server, connect_client):
    """_start_session replacing a session (e.g. a continue request racing a stale one) ends its trade evolution."""
    a, b, cid = await run_with_caps(connect_client)
    await a.send(**ask(cid))
    await b.expect("challenge_evo_request")
    await server.challenges._start_session(server.challenges.records[cid])
    assert (await a.expect("challenge_evo_end"))["reason"] == "session over"
    await b.expect("challenge_evo_end")
    assert not server.challenges.evos
