import asyncio
import base64
import json
import uuid

import pytest
from websockets.asyncio.client import connect

from yellowlink_server import PROTOCOL_VERSION
from yellowlink_server.config import Config
from yellowlink_server.server import YellowLinkServer

KEY = "test-key"


def make_mon(species=0x54, level=12, nick=b"\x8f\x88\x8a\x80\x82\x87\x94\x50"):
    data = bytearray(44)
    data[0] = species
    data[33] = level
    return {
        "data": base64.b64encode(bytes(data)).decode(),
        "ot": base64.b64encode(b"\x80\x81\x50" + bytes(8)).decode(),
        "nick": base64.b64encode(nick.ljust(11, b"\x50")).decode(),
    }


class WSClient:
    """Websocket client that buffers incoming messages for selective waiting."""

    def __init__(self, ws, uid, name):
        self.ws, self.uid, self.name = ws, uid, name
        self.inbox: list[dict] = []
        self._cond = asyncio.Condition()
        self._reader = asyncio.create_task(self._read())
        self.closed = asyncio.get_running_loop().create_future()

    async def _read(self):
        try:
            async for raw in self.ws:
                async with self._cond:
                    self.inbox.append(json.loads(raw))
                    self._cond.notify_all()
        except Exception:
            pass
        finally:
            if not self.closed.done():
                self.closed.set_result(self.ws.close_code)
            async with self._cond:
                self._cond.notify_all()

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg))

    async def expect(self, mtype, timeout=3.0, **match):
        def find():
            for i, m in enumerate(self.inbox):
                if m.get("type") == mtype and all(m.get(k) == v for k, v in match.items()):
                    return self.inbox.pop(i)
            return None

        async def wait():
            async with self._cond:
                while True:
                    m = find()
                    if m is not None:
                        return m
                    await self._cond.wait()

        return await asyncio.wait_for(wait(), timeout)

    async def expect_none(self, mtype, wait=0.3, **match):
        await asyncio.sleep(wait)
        hits = [m for m in self.inbox if m.get("type") == mtype
                and all(m.get(k) == v for k, v in match.items())]
        assert not hits, f"unexpected {mtype}: {hits}"

    def clear(self):
        self.inbox.clear()

    async def close(self):
        await self.ws.close()
        self._reader.cancel()


@pytest.fixture
async def server(tmp_path):
    cfg = Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(tmp_path / "data"),
                 latest_client="0.1.0", download_url="https://example.invalid/dl", motd="hi")
    srv = YellowLinkServer(cfg, store_debounce=0.05)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
def connect_client(server):
    clients = []

    async def _connect(name, uid=None, key=KEY, expect_welcome=True):
        uid = uid or str(uuid.uuid4())
        ws = await connect(f"ws://127.0.0.1:{server.port}")
        c = WSClient(ws, uid, name)
        clients.append(c)
        await c.send(type="hello", name=name, uid=uid, key=key, version="0.1.0",
                     protocol=PROTOCOL_VERSION)
        if expect_welcome:
            c.welcome = await c.expect("welcome")
        return c

    yield _connect
    for c in clients:
        c._reader.cancel()
