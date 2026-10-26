"""Gen-4 (Pokémon Platinum) entries in stored PCs: validation, server-side PK4 codec and trade evolutions."""
import base64
import struct

from yellowlink_server import pcstore
from test_pctrades import g1_entry, g2_entry, store, upload
from test_server import open_trade


def g4name(name):
    codes = pcstore._g4_name(name)
    return codes + [0] * (11 - len(codes))


def pk4(species, item=0, pid=0x01234567, nick=None, egg=False, ability=0, tid=1000, sid=2000):
    """Build an encrypted 136-byte Platinum box mon (plain layout: blocks A,B,C,D at 8/0x28/0x48/0x68)."""
    p = bytearray(136)
    struct.pack_into("<I", p, 0, pid)
    struct.pack_into("<HHHHI", p, 0x08, species, item, tid, sid, 5000)
    p[0x15] = ability
    p[0x17] = 2
    struct.pack_into("<4H", p, 0x28, 33, 0, 0, 0)
    iv = (1 << 30 if egg else 0) | (1 << 31 if nick else 0) | 0x1F
    struct.pack_into("<I", p, 0x38, iv)
    struct.pack_into("<11H", p, 0x48, *g4name(nick or {64: "KADABRA", 61: "POLIWHIRL", 366: "CLAMPERL",
                                                       233: "PORYGON2", 95: "ONIX", 25: "PIKACHU"}[species]))
    struct.pack_into("<8H", p, 0x68, *(pcstore._g4_name("LUCAS") + [0, 0]))
    return pcstore.pk4_encrypt(p)


def g4_entry(eid, species, nick=None, **kw):
    raw = pk4(species, nick=nick, **kw)
    return {"id": eid, "origin": "platinum", "dex": species, "level": 20, "nick": nick or "X", "ot": "LUCAS",
            "otid": 1000, "gen1": None, "gen2": None, "gen4": base64.b64encode(raw).decode()}


def fields(entry):
    p = pcstore.pk4_decrypt(base64.b64decode(entry["gen4"]))
    species, item = struct.unpack_from("<HH", p, 0x08)
    nick = struct.unpack_from("<11H", p, 0x48)
    return species, item, p[0x15], list(nick)


# --- codec + validation ---------------------------------------------------------------------------------------
def test_pk4_codec_roundtrip_all_shuffles():
    for sv in range(32):
        raw = pk4(25, pid=(sv << 13) | 0x1234)
        plain = pcstore.pk4_decrypt(raw)
        assert plain is not None and struct.unpack_from("<H", plain, 0x08)[0] == 25
        assert pcstore.pk4_encrypt(plain) == raw


def test_valid_entry_gen4():
    e = g4_entry("a", 25)
    assert pcstore.valid_entry(e)
    bad = bytearray(base64.b64decode(e["gen4"]))
    bad[50] ^= 1
    assert not pcstore.valid_entry({**e, "gen4": base64.b64encode(bytes(bad)).decode()})       # checksum
    assert not pcstore.valid_entry({**e, "gen4": base64.b64encode(bytes(135)).decode()})       # size
    assert not pcstore.valid_entry({**e, "gen4": "!!"})
    assert not pcstore.valid_entry({**e, "gen4": None})                                         # nothing left
    both = {**g1_entry("b", 0x54, "PIKA"), "gen4": e["gen4"]}
    assert pcstore.valid_entry(both)


# --- trade evolutions ---------------------------------------------------------------------------------------
def test_kadabra_evolves_name_and_ability_follow():
    e, name = pcstore.evolve_for_trade(g4_entry("k", 64, pid=0x101))       # odd PID: second ability slot
    assert name == "ALAKAZAM" and e["dex"] == 65 and e["nick"] == "ALAKAZAM"
    sp, item, ability, nick = fields(e)
    assert sp == 65 and ability == 39 and nick == g4name("ALAKAZAM")
    e, _ = pcstore.evolve_for_trade(g4_entry("k", 64, pid=0x100))
    assert fields(e)[2] == 28


def test_nicknamed_keeps_name():
    e, name = pcstore.evolve_for_trade(g4_entry("k", 64, nick="SPOONY"))
    assert name == "ALAKAZAM" and e["nick"] == "SPOONY" and fields(e)[3] == g4name("SPOONY")


def test_held_item_evolutions_consume_item():
    for species, item, new in [(61, 221, 186), (95, 233, 208), (233, 324, 474), (366, 226, 367), (366, 227, 368)]:
        e, name = pcstore.evolve_for_trade(g4_entry("x", species, item=item))
        sp, held, _, _ = fields(e)
        assert (sp, held, e["dex"]) == (new, 0, new) and name


def test_no_evolution_cases():
    for kw in ({"species": 61}, {"species": 61, "item": 233}, {"species": 64, "item": 229},
               {"species": 64, "egg": True}, {"species": 25}):
        e0 = g4_entry("x", **kw)
        e, name = pcstore.evolve_for_trade(e0)
        assert name is None and e == e0


def test_current_representation_rule():
    # harvested from Platinum: gen4 is current; a stale gen1 copy is dropped when it evolves
    e0 = {**g4_entry("x", 64), "gen1": g1_entry("x", 0x26, "KADABRA", 64)["gen1"]}
    e, name = pcstore.evolve_for_trade(e0)
    assert name == "ALAKAZAM" and e["gen1"] is None and fields(e)[0] == 65
    # harvested from Yellow: gen1 is current, gen4 dropped
    e1 = {**g1_entry("y", 0x26, "KADABRA", 64), "gen4": g4_entry("y", 64)["gen4"]}
    e, name = pcstore.evolve_for_trade(e1)
    assert name == "ALAKAZAM" and e["gen4"] is None and base64.b64decode(e["gen1"])[0] == 0x95
    # current one can't evolve (Poliwhirl without King's Rock in Crystal): nothing changes anywhere
    e2 = {**g2_entry("z", 61, "POLIWHIRL"), "gen4": g4_entry("z", 61, item=221)["gen4"]}
    e, name = pcstore.evolve_for_trade(e2)
    assert name is None and e == e2
    # unknown origin: first representation present
    assert pcstore.current_rep({"gen4": "x", "gen2": "y", "origin": "?"}) == "gen2"
    assert pcstore.current_rep({"gen4": "x"}) == "gen4"


async def test_trade_platinum_with_yellow(server, connect_client):
    a = await connect_client("Ilias")          # Platinum player
    b = await connect_client("Alex")           # Yellow player
    await upload(a, store(1, g4_entry("p4", 95, item=233), g4_entry("keep", 25)))
    await upload(b, store(1, g1_entry("y1", 0x26, "KADABRA", 64)))
    tid = await open_trade(a, b)
    await a.send(type="trade_offer", trade_id=tid, entry_ids=["p4"])
    await b.expect("trade_offer")
    await b.send(type="trade_offer", trade_id=tid, entry_ids=["y1"])
    await a.expect("trade_offer")
    await a.send(type="trade_confirm", trade_id=tid)
    await b.send(type="trade_confirm", trade_id=tid)
    da = (await a.expect("pc_trades"))["deltas"][0]
    db = (await b.expect("pc_trades"))["deltas"][0]
    assert da["add"][0]["dex"] == 65 and db["add"][0]["dex"] == 208 and db["add"][0]["nick"] == "STEELIX"
    assert fields(db["add"][0])[:2] == (208, 0)
    done = await a.expect("trade_complete", trade_id=tid)
    assert sorted(done["evolved_all"]) == ["ALAKAZAM"]
    assert [e["id"] for e in server._pc_load(b.uid)["boxes"][0]] == ["p4"]
