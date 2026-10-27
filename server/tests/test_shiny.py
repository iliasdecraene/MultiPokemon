"""Shiny alerts (SPEC.md section 10): `shiny_found` -> a system chat line with a `shiny` field for everyone."""
import asyncio

import pytest

from yellowlink_server import server as srvmod

from test_challenges import join_both, start_challenge


async def alert(c, timeout=3.0):
    """The next system message that is a shiny alert (join/leave lines are skipped)."""
    while True:
        m = await c.expect("system", timeout=timeout)
        if "shiny" in m:
            return m


async def two(connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await asyncio.sleep(0.05)
    a.clear(); b.clear()
    return a, b


def found(**kw):
    m = dict(type="shiny_found", species="Pidgey", dex=16, area="Route 1", game="yellow")
    m.update(kw)
    return m


async def test_feature_is_announced(server, connect_client):
    a = await connect_client("Ilias")
    assert "shiny_alert" in a.welcome["features"]
    assert "badges" in a.welcome["features"] and "trade_evo" in a.welcome["features"]


async def test_everyone_gets_the_line_and_the_alert(server, connect_client):
    a, b = await two(connect_client)
    await a.send(**found())
    for c in (a, b):
        m = await c.expect("system", text="✨ Ilias found a shiny Pidgey on Route 1!")
        assert m["shiny"] == {"uid": a.uid, "name": "Ilias", "species": "Pidgey", "dex": 16, "area": "Route 1",
                              "game": "yellow", "soullink": False}


@pytest.mark.parametrize("species", ["Nidoran♀", "Mr.Mime", "Farfetch'd", "Farfetch’d", "Porygon-Z", "Mime Jr.",
                                     "Ho-Oh", "Porygon2"])
async def test_real_names_are_accepted(server, connect_client, species):
    a = await connect_client("Ilias")
    await a.send(**found(species=species))
    m = await alert(a)
    shown = species.replace("♀", " F")
    assert m["shiny"]["species"] == shown and m["text"] == f"✨ Ilias found a shiny {shown} on Route 1!"


@pytest.mark.parametrize("area, line", [
    ("Route 202", "✨ Ilias found a shiny Pidgey on Route 202!"),
    ("Viridian Forest", "✨ Ilias found a shiny Pidgey in Viridian Forest!"),
    ("Mt. Moon", "✨ Ilias found a shiny Pidgey in Mt. Moon!"),
    ("Eterna City", "✨ Ilias found a shiny Pidgey in Eterna City!")])
async def test_on_routes_in_places(server, connect_client, area, line):
    a = await connect_client("Ilias")
    await a.send(**found(area=area))
    assert (await alert(a))["text"] == line


async def test_gender_symbols_become_letters(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(**found(species="Nidoran♂", dex=32))
    m = await alert(a)
    assert m["text"] == "✨ Ilias found a shiny Nidoran M on Route 1!" and m["shiny"]["species"] == "Nidoran M"


@pytest.mark.parametrize("bad, needle", [
    (dict(species=""), "species"), (dict(species=5), "species"), (dict(species="A" * 13), "species"),
    (dict(species="<b>hi</b>"), "species"), (dict(species=" Pidgey"), "species"),
    (dict(dex=0), "dex"), (dict(dex=494), "dex"), (dict(dex="16"), "dex"), (dict(dex=True), "dex"),
    (dict(area=""), "area"), (dict(area=None), "area"), (dict(area="\x00\x01"), "area"),
    (dict(game="Yellow!"), "game"), (dict(game=None), "game"),
])
async def test_validation(server, connect_client, bad, needle):
    a, b = await two(connect_client)
    await a.send(**found(**bad))
    err = await a.expect("error")
    assert needle in err["message"]
    await b.expect_none("system", wait=0.1)


async def test_long_area_is_cut_and_cleaned(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(**found(area="Route\x00 1" + " x" * 40))
    m = await alert(a)
    assert len(m["shiny"]["area"]) <= 40 and "\x00" not in m["text"]


async def test_rate_limited_per_player_silently(server, connect_client, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(srvmod, "shiny_clock", lambda: t[0])
    a, b = await two(connect_client)
    await a.send(**found())
    await alert(b)
    t[0] += 5
    await a.send(**found(species="Rattata", dex=19))
    await b.expect_none("system", wait=0.15)
    await a.expect_none("error", wait=0.0)                         # dropped silently, no red error
    await b.send(**found(species="Onix", dex=95, area="Union Cave", game="crystal"))   # others aren't limited
    await a.expect("system", text="✨ Alex found a shiny Onix in Union Cave!")
    t[0] += srvmod.SHINY_COOLDOWN
    await a.send(**found(species="Rattata", dex=19))
    await b.expect("system", text="✨ Ilias found a shiny Rattata on Route 1!")


async def test_rate_limit_survives_a_reconnect(server, connect_client, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(srvmod, "shiny_clock", lambda: t[0])
    a = await connect_client("Ilias", uid="ilias-uid")
    b = await connect_client("Alex")
    await a.send(**found())
    await alert(b)
    await a.close()
    a2 = await connect_client("Ilias", uid="ilias-uid")
    await a2.send(**found())
    await b.expect_none("system", text="✨ Ilias found a shiny Pidgey on Route 1!", wait=0.15)


async def test_soul_link_run_says_so(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(type="library", games=["yellow"])
    await b.send(type="library", games=["yellow"])
    c = await start_challenge(a, b)
    await join_both(a, b, c["id"])
    a.clear(); b.clear()
    await a.send(**found())
    m = await b.expect("system", text="✨ Ilias found a shiny Pidgey on Route 1 in their Soul Link!")
    assert m["shiny"]["soullink"] is True
