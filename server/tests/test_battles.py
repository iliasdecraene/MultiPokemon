import pytest

from conftest import make_mon
import yellowlink_server.server as srvmod


async def prepare(a, b):
    await a.send(type="battle_request", to=b.uid)
    req = await b.expect("battle_request")
    assert req["from"] == a.uid and req["name"] == a.name
    bid = req["battle_id"]
    await a.expect_none("battle_request", wait=0.05)
    await b.send(type="battle_respond", battle_id=bid, accept=True)
    pa = await a.expect("battle_prepare", battle_id=bid)
    pb = await b.expect("battle_prepare", battle_id=bid)
    assert (pa["with"], pa["name"], pa["master"]) == (b.uid, b.name, True)
    assert (pb["with"], pb["name"], pb["master"]) == (a.uid, a.name, False)
    return bid


async def start(a, b, mons_a=None, mons_b=None):
    bid = await prepare(a, b)
    mons_a = mons_a or [make_mon(0x54, 10)]
    mons_b = mons_b or [make_mon(0x99, 12), make_mon(0x26, 20)]
    await a.send(type="battle_ready", battle_id=bid, mons=mons_a, trainer="ILIAS")
    await b.expect_none("battle_start", wait=0.1)
    await b.send(type="battle_ready", battle_id=bid, mons=mons_b, trainer="ALEX\x07")
    sa = await a.expect("battle_start", battle_id=bid)
    sb = await b.expect("battle_start", battle_id=bid)
    return bid, sa, sb, mons_a, mons_b


async def test_battle_happy_path(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, sa, sb, mons_a, mons_b = await start(a, b)
    assert sa["mons"] == mons_b and sa["trainer"] == "ALEX"
    assert sb["mons"] == mons_a and sb["trainer"] == "ILIAS"
    assert (sa["with"], sa["name"], sa["master"]) == (b.uid, "Alex", True)
    assert (sb["with"], sb["name"], sb["master"]) == (a.uid, "Ilias", False)
    assert sa["rns"] == sb["rns"]
    assert len(sa["rns"]) == 10 and all(isinstance(x, int) and 0 <= x <= 252 for x in sa["rns"])

    await a.send(type="battle_move", battle_id=bid, n=1, v=2, check="abc")
    m = await b.expect("battle_move")
    assert m == {"type": "battle_move", "battle_id": bid, "n": 1, "v": 2, "check": "abc"}
    await a.expect_none("battle_move", wait=0.05)
    await b.send(type="battle_move", battle_id=bid, n=1, v=4)
    m = await a.expect("battle_move")
    assert (m["n"], m["v"], m["check"]) == (1, 4, None)

    await b.send(type="battle_end", battle_id=bid, result=1)
    await a.expect("system", text="Ilias beat Alex in a link battle!")
    await a.send(type="battle_end", battle_id=bid, result=0)  # second end: ignored
    await a.expect_none("error")
    await a.expect_none("system", text="Ilias beat Alex in a link battle!")  # only once
    # both free again
    await prepare(b, a)


async def test_battle_draw_message(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    await a.send(type="battle_end", battle_id=bid, result=2)
    await b.expect("system", text="Ilias and Alex drew!")


async def test_invalid_move_fields(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    for bad in (dict(n=0, v=1), dict(n=1, v=16), dict(n="1", v=1), dict(n=1, v=1, check="x" * 65)):
        await a.send(type="battle_move", battle_id=bid, **bad)
        await a.expect("error")
    await b.expect_none("battle_move")


async def test_late_move_after_end_ignored(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    await a.send(type="battle_end", battle_id=bid, result=0)
    await b.expect("system", text="Ilias beat Alex in a link battle!")
    await b.send(type="battle_move", battle_id=bid, n=5, v=1)
    await b.send(type="battle_leave", battle_id=bid)
    await b.send(type="battle_move", battle_id="no-such-battle", n=1, v=1)
    await b.expect_none("error")
    await a.expect_none("battle_move")
    await a.expect_none("battle_aborted")


async def test_battle_decline(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="battle_request", to=b.uid)
    bid = (await b.expect("battle_request"))["battle_id"]
    await b.send(type="battle_respond", battle_id=bid, accept=False)
    ab = await a.expect("battle_aborted", battle_id=bid)
    assert ab["reason"] == "declined"
    await a.send(type="battle_request", to=b.uid)  # free again
    await b.expect("battle_request")


async def test_battle_requested_timeout(connect_client, monkeypatch):
    monkeypatch.setattr(srvmod, "BATTLE_REQUEST_TIMEOUT", 0.2)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="battle_request", to=b.uid)
    bid = (await b.expect("battle_request"))["battle_id"]
    assert (await a.expect("battle_aborted", battle_id=bid))["reason"] == "timeout"
    assert (await b.expect("battle_aborted", battle_id=bid))["reason"] == "timeout"


async def test_battle_preparing_timeout(connect_client, monkeypatch):
    monkeypatch.setattr(srvmod, "BATTLE_PREPARE_TIMEOUT", 0.2)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid = await prepare(a, b)
    await a.send(type="battle_ready", battle_id=bid, mons=[make_mon()], trainer="ILIAS")
    assert (await a.expect("battle_aborted", battle_id=bid))["reason"] == "timeout"
    assert (await b.expect("battle_aborted", battle_id=bid))["reason"] == "timeout"
    await a.expect_none("battle_start")


async def test_preparing_timer_not_triggered_by_request_timer(connect_client, monkeypatch):
    monkeypatch.setattr(srvmod, "BATTLE_REQUEST_TIMEOUT", 0.2)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await prepare(a, b)
    await a.expect_none("battle_aborted", wait=0.4)


async def test_leave_before_start(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid = await prepare(a, b)
    await b.send(type="battle_leave", battle_id="")  # empty id acts on own battle
    assert (await a.expect("battle_aborted", battle_id=bid))["reason"] == "cancelled"
    await b.expect_none("battle_aborted")


async def test_requester_leave_while_requested(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="battle_request", to=b.uid)
    bid = (await b.expect("battle_request"))["battle_id"]
    await a.send(type="battle_leave")
    assert (await b.expect("battle_aborted", battle_id=bid))["reason"] == "cancelled"


async def test_leave_while_running(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    await a.send(type="battle_leave", battle_id=bid)
    assert (await b.expect("battle_aborted", battle_id=bid))["reason"] == "opponent left"
    await b.send(type="battle_end", battle_id=bid, result=0)  # ignored
    await b.expect_none("error")
    await b.expect_none("system", text="Alex beat Ilias in a link battle!")


async def test_disconnect_while_running(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    await a.close()
    ab = await b.expect("battle_aborted", battle_id=bid)
    assert ab["reason"] == "opponent disconnected"


async def test_disconnect_while_preparing(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid = await prepare(a, b)
    await b.close()
    assert (await a.expect("battle_aborted", battle_id=bid))["reason"] == "cancelled"


async def test_duplicate_uid_kick_aborts_battle(connect_client):
    a = await connect_client("Ilias", uid="ilias-uid")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    await connect_client("Ilias", uid="ilias-uid")
    ab = await b.expect("battle_aborted", battle_id=bid)
    assert ab["reason"] == "opponent disconnected"


async def test_busy_checks(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await connect_client("Sam")
    # a and b in a trade
    await a.send(type="trade_request", to=b.uid)
    await b.expect("trade_request")
    await c.send(type="battle_request", to=a.uid)
    assert "busy" in (await c.expect("error"))["message"]
    await a.send(type="battle_request", to=c.uid)
    assert "already" in (await a.expect("error"))["message"]
    await a.send(type="trade_cancel")
    await b.expect("trade_cancelled")

    # a and b in a battle
    await prepare(a, b)
    await c.send(type="trade_request", to=b.uid)
    assert "busy" in (await c.expect("error"))["message"]
    await c.send(type="battle_request", to=b.uid)
    assert "busy" in (await c.expect("error"))["message"]
    await b.send(type="trade_request", to=c.uid)
    assert "already" in (await b.expect("error"))["message"]
    await b.send(type="battle_request", to=c.uid)
    assert "already" in (await b.expect("error"))["message"]
    await c.expect_none("trade_request")
    await c.expect_none("battle_request")


async def test_non_participant_cannot_act(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    c = await connect_client("Sam")
    bid = await prepare(a, b)
    await c.send(type="battle_ready", battle_id=bid, mons=[make_mon()], trainer="SAM")
    await c.expect("error")
    await c.send(type="battle_leave", battle_id=bid)
    await a.expect_none("battle_aborted")


@pytest.mark.parametrize("mons", [
    [],
    [make_mon()] * 7,
    [make_mon(species=0)],
    [{"data": "AAAA", "ot": "", "nick": ""}],
    "nope",
])
async def test_invalid_ready_mons_rejected(connect_client, mons):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid = await prepare(a, b)
    await b.send(type="battle_ready", battle_id=bid, mons=[make_mon()], trainer="ALEX")
    await a.send(type="battle_ready", battle_id=bid, mons=mons, trainer="ILIAS")
    await a.expect("error")
    await b.expect_none("battle_start")
    # a valid ready still works afterwards
    await a.send(type="battle_ready", battle_id=bid, mons=[make_mon()], trainer="ILIAS")
    await b.expect("battle_start", battle_id=bid)


async def test_battle_moves_not_starved_by_rate_limit(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    bid, *_ = await start(a, b)
    for i in range(40):  # exhaust the general budget with position updates
        await a.send(type="state", map=0, x=i, y=0, px=0, py=0, facing=0, img=0, visible=True)
    await a.send(type="battle_move", battle_id=bid, n=1, v=3)
    m = await b.expect("battle_move")
    assert m["v"] == 3
