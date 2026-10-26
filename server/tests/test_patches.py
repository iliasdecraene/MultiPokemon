"""Definitive Platinum on the server (SPEC.md section 10): patch files in the data folder served in chunks,
YL_SINNOH, `library.variants` and the variant of Platinum Soul Link runs."""
import asyncio
import base64
import hashlib
import os

import pytest

from conftest import KEY
from test_challenges import NEW_LIB, start_challenge
from yellowlink_server import challenges as chal
from yellowlink_server import patches as patchmod
from yellowlink_server.config import Config, load_config
from yellowlink_server.server import YellowLinkServer

PATCH0 = b"\xd6\xc3\xc4\x00" + bytes((i * 7) & 0xFF for i in range(300_000))       # ~6 chunks, not a real patch
PATCH1 = b"\xd6\xc3\xc4\x00" + b"rev1" * 1000


def put(data_dir, rev, data):
    d = os.path.join(data_dir, "patches", "definitive_platinum")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{rev}.xdelta"), "wb") as f:
        f.write(data)


async def download(c, rev, sha1, size, offset=0, length=patchmod.MAX_RANGE):
    """Fetch [offset, size) with consecutive ranges, like the client does."""
    out = bytearray()
    pos = offset
    while pos < size:
        await c.send(type="patch_get", name="definitive_platinum", rev=rev, sha1=sha1, offset=pos, length=length)
        end = min(pos + length, size)
        while pos < end:
            m = await c.expect("patch_chunk", timeout=5)
            assert m["offset"] == pos and m["sha1"] == sha1 and m["size"] == size and m["rev"] == rev
            data = base64.b64decode(m["data"])
            assert 0 < len(data) <= patchmod.CHUNK
            out += data
            pos += len(data)
    return bytes(out)


def test_store_validates_and_hashes(tmp_path):
    d = str(tmp_path)
    put(d, "rev0", PATCH0)
    put(d, "rev1", b"PK\x03\x04 not a patch at all")
    store = patchmod.PatchStore(d)
    offer = store.offer()
    assert offer == {"definitive_platinum": {"rev0": {"size": len(PATCH0), "sha1": hashlib.sha1(PATCH0).hexdigest()}}}
    put(d, "rev1", PATCH1)                     # fixed by the host: picked up by refresh(), no restart
    store.refresh()
    assert store.offer()["definitive_platinum"]["rev1"]["sha1"] == hashlib.sha1(PATCH1).hexdigest()
    os.remove(store.path("definitive_platinum", "rev0"))
    store.refresh()
    assert list(store.offer()["definitive_platinum"]) == ["rev1"]
    assert patchmod.PatchStore(str(tmp_path / "nothing")).offer() == {}


def test_yl_sinnoh_config(tmp_path):
    assert load_config(env={}).sinnoh == "any"
    assert load_config(env={"YL_SINNOH": "Definitive"}).sinnoh == "definitive"
    assert load_config(env={"YL_SINNOH": "vanilla please"}).sinnoh == "any"     # unknown: logged, default


async def test_welcome_offers_patches(server, connect_client):
    a = await connect_client("Ilias")
    assert "defplat" in a.welcome["features"]
    assert a.welcome["sinnoh"] == "any" and a.welcome["patches"] == {}
    put(server.config.data_dir, "rev0", PATCH0)
    b = await connect_client("Alex")                   # added while running: the next hello sees it
    assert b.welcome["patches"]["definitive_platinum"]["rev0"] == {
        "size": len(PATCH0), "sha1": hashlib.sha1(PATCH0).hexdigest()}


async def test_download_in_ranges_and_resume(server, connect_client):
    put(server.config.data_dir, "rev0", PATCH0)
    a = await connect_client("Ilias")
    info = a.welcome["patches"]["definitive_platinum"]["rev0"]
    got = await download(a, "rev0", info["sha1"], info["size"], length=100_000)
    assert got == PATCH0
    # resume from the middle (after a reconnect)
    await a.close()
    a2 = await connect_client("Ilias", uid=a.uid)
    tail = await download(a2, "rev0", info["sha1"], info["size"], offset=123_456)
    assert tail == PATCH0[123_456:]


async def test_download_does_not_block_others(server, connect_client):
    put(server.config.data_dir, "rev0", PATCH0 * 3)
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    info = a.welcome["patches"]["definitive_platinum"]["rev0"]
    await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1=info["sha1"], offset=0,
                 length=patchmod.MAX_RANGE)
    await b.send(type="chat", text="still here")
    await a.send(type="chat", text="me too")
    assert (await b.expect("chat", text="me too"))["name"] == "Ilias"
    assert (await a.expect("chat", text="still here"))["name"] == "Alex"


async def test_new_request_cancels_the_old_range(server, connect_client):
    put(server.config.data_dir, "rev0", PATCH0 * 4)
    a = await connect_client("Ilias")
    info = a.welcome["patches"]["definitive_platinum"]["rev0"]
    await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1=info["sha1"], offset=0,
                 length=patchmod.MAX_RANGE)
    await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1=info["sha1"], offset=500_000,
                 length=patchmod.CHUNK)
    await asyncio.sleep(0.5)
    chunks = [m for m in a.inbox if m.get("type") == "patch_chunk"]
    assert chunks[-1]["offset"] == 500_000 and len(base64.b64decode(chunks[-1]["data"])) == patchmod.CHUNK
    assert sum(len(base64.b64decode(m["data"])) for m in chunks) < patchmod.MAX_RANGE + patchmod.CHUNK
    assert server.clients[a.uid].download is None


async def test_patch_errors(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1="x", offset=0, length=10)
    assert (await a.expect("patch_error"))["reason"] == "missing"
    await a.send(type="patch_get", name="../../etc/passwd", rev="rev0", sha1="x", offset=0, length=10)
    assert (await a.expect("patch_error"))["reason"] == "bad"
    put(server.config.data_dir, "rev0", PATCH0)
    server.patches.refresh()
    await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1="old", offset=0, length=10)
    e = await a.expect("patch_error")
    assert e["reason"] == "changed" and e["sha1"] == hashlib.sha1(PATCH0).hexdigest() and e["size"] == len(PATCH0)
    sha = e["sha1"]
    for bad in (dict(offset=-1, length=10), dict(offset=0, length=0), dict(offset=0, length=(1 << 20) + 1),
                dict(offset="0", length=10), dict(offset=len(PATCH0), length=10)):
        await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1=sha, **bad)
        await a.expect("error")
    # the last byte only
    await a.send(type="patch_get", name="definitive_platinum", rev="rev0", sha1=sha, offset=len(PATCH0) - 1,
                 length=1000)
    m = await a.expect("patch_chunk")
    assert base64.b64decode(m["data"]) == PATCH0[-1:]


async def test_library_variants_in_roster(server, connect_client):
    a = await connect_client("Ilias")
    await a.send(**NEW_LIB, variants={"platinum": ["vanilla", "definitive", "rev1"], "crystal": ["v11"]})
    await asyncio.sleep(0.1)
    e = next(e for e in server.roster() if e["uid"] == a.uid)
    assert e["variants"] == {"platinum": ["definitive", "vanilla"]}           # only names the server knows
    await a.send(**NEW_LIB, variants=["platinum"])
    assert "variants" in (await a.expect("error"))["message"]


# --- Platinum Soul Link runs carry the variant -------------------------------------------------------------------
def lib(*variants):
    return dict(NEW_LIB, variants={"platinum": list(variants)})


async def test_definitive_run_needs_both(server, connect_client):
    a = await connect_client("Ilias")
    b = await connect_client("Alex")
    await a.send(**lib("vanilla", "definitive"))
    await b.send(**lib("vanilla"))
    await asyncio.sleep(0.1)
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="platinum", variant="definitive")
    assert "Alex doesn't have Definitive Platinum yet" in (await a.expect("error"))["message"]
    await b.send(**lib("vanilla", "definitive"))
    await asyncio.sleep(0.1)
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="platinum", variant="definitive",
                 rules={"dupes": True})
    req = await b.expect("challenge_request")
    assert req["variant"] == "definitive"
    await b.send(type="challenge_respond", request_id=req["request_id"], accept=True)
    c = (await a.expect("challenge_go"))["challenge"]
    assert c["variant"] == "definitive" and server.challenges.records[c["id"]]["variant"] == "definitive"
    # continuing needs it too
    await a.send(type="challenge_leave", challenge_id=c["id"], reason="paused")
    await b.expect("challenge_partner_left")
    await b.send(**lib("vanilla"))
    await asyncio.sleep(0.1)
    await a.send(type="challenge_request", to=b.uid, challenge_id=c["id"])
    assert "Alex doesn't have Definitive Platinum yet" in (await a.expect("error"))["message"]
    server.challenges.save_now()
    assert chal.ChallengeHub(server, server.challenges.data_dir).records[c["id"]]["variant"] == "definitive"


async def test_vanilla_run_default_and_old_clients(server, connect_client):
    a = await connect_client("Ilias")
    old = await connect_client("Alex")
    await a.send(**lib("vanilla", "definitive"))
    await old.send(**NEW_LIB)                      # a 0.7.6 client: no variants = original Platinum only
    await asyncio.sleep(0.1)
    c = await start_challenge(a, old, game="platinum")
    assert c["variant"] == "vanilla"
    await a.send(type="challenge_abandon", challenge_id=c["id"])
    await asyncio.sleep(0.1)
    for x in (a, old):
        x.clear()
    await a.send(type="challenge_request", to=old.uid, kind="soul_link", game="platinum", variant="definitive")
    assert "Alex needs a newer YellowLink for Definitive Platinum" in (await a.expect("error"))["message"]
    # someone with only Definitive can't join an original-Platinum run
    only = await connect_client("Misty")
    await only.send(**lib("definitive"))
    await asyncio.sleep(0.1)
    await a.send(type="challenge_request", to=only.uid, kind="soul_link", game="platinum", variant="vanilla")
    assert "Misty only has Definitive Platinum" in (await a.expect("error"))["message"]
    await a.send(type="challenge_request", to=only.uid, kind="soul_link", game="platinum", variant="purple")
    assert "variant" in (await a.expect("error"))["message"]
    # Yellow runs have no variant
    y = await start_challenge(a, only, game="yellow")
    assert "variant" not in y


@pytest.fixture
async def definitive_server(tmp_path):
    cfg = Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(tmp_path / "data"), latest_client="0.1.0",
                 sinnoh="definitive")
    srv = YellowLinkServer(cfg, store_debounce=0.05)
    await srv.start()
    yield srv
    await srv.stop()


async def test_definitive_server_forces_definitive_runs(definitive_server):
    import uuid
    from conftest import WSClient
    from websockets.asyncio.client import connect
    from yellowlink_server import PROTOCOL_VERSION

    async def join(name):
        ws = await connect(f"ws://127.0.0.1:{definitive_server.port}")
        c = WSClient(ws, str(uuid.uuid4()), name)
        await c.send(type="hello", name=name, uid=c.uid, key=KEY, version="0.7.7", protocol=PROTOCOL_VERSION)
        c.welcome = await c.expect("welcome")
        return c
    a, b = await join("Ilias"), await join("Alex")
    assert a.welcome["sinnoh"] == "definitive"
    await a.send(**lib("vanilla", "definitive"))
    await b.send(**lib("vanilla"))
    await asyncio.sleep(0.1)
    await a.send(type="challenge_request", to=b.uid, kind="soul_link", game="platinum", variant="vanilla")
    assert "Alex doesn't have Definitive Platinum yet" in (await a.expect("error"))["message"]
    await b.send(**lib("vanilla", "definitive"))
    await asyncio.sleep(0.1)
    c = await start_challenge(a, b, game="platinum")          # no variant asked: still Definitive here
    assert c["variant"] == "definitive"
    for x in (a, b):
        await x.close()
