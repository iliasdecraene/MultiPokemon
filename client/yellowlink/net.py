"""WebSocket connection running in a background thread.

The game loop never blocks: it calls `send()` and drains `poll()` every frame.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time

import websockets

PROTOCOL_VERSION = 1
log = logging.getLogger("yellowlink.net")


class NetClient:
    def __init__(self, url: str, hello: dict):
        self.url = url
        self.hello = dict(hello, type="hello", protocol=PROTOCOL_VERSION)
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.status = "connecting"   # connecting | online | offline | rejected
        self.status_detail = ""
        self._loop = None
        self._out: asyncio.Queue | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="yl-net", daemon=True)
        self.rtt_ms = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        loop = self._loop
        if loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass

    def send(self, msg: dict):
        loop, out = self._loop, self._out
        if loop is None or out is None or self.status != "online":
            return False
        try:
            loop.call_soon_threadsafe(out.put_nowait, msg)
            return True
        except RuntimeError:
            return False

    def poll(self):
        while True:
            try:
                yield self.inbox.get_nowait()
            except queue.Empty:
                return

    # -- thread side --
    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self):
        delay = 1.0
        while not self._stop:
            self.status = "connecting"
            try:
                async with websockets.connect(self.url, max_size=1 << 20, open_timeout=8,
                                              ping_interval=20, ping_timeout=20) as ws:
                    self._out = asyncio.Queue()
                    await ws.send(json.dumps(self.hello))
                    self.status = "online"
                    self.status_detail = ""
                    log.info("websocket connected")
                    self.inbox.put({"type": "_connected"})
                    delay = 1.0
                    await self._pump(ws)
                    # Normal close (e.g. server restart): still a disconnect.
                    self._closed(ws.close_code, ws.close_reason)
                    if ws.close_code in (4001, 4002, 4003):
                        return
            except websockets.exceptions.ConnectionClosedError as e:
                code = e.rcvd.code if e.rcvd else None
                self._closed(code, e.rcvd.reason if e.rcvd else str(e))
                if code in (4001, 4002, 4003):
                    return
            except websockets.exceptions.ConnectionClosedOK as e:
                code = e.rcvd.code if e.rcvd else None
                self._closed(code, e.rcvd.reason if e.rcvd else "closed")
                if code in (4001, 4002, 4003):
                    return
            except Exception as e:  # noqa: BLE001 - network errors of all kinds
                self._closed(None, str(e) or e.__class__.__name__)
            self._out = None
            if self._stop:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def _closed(self, code, reason):
        log.info("websocket closed: code=%s reason=%s", code, reason)
        if code == 4001:
            self.status = "rejected"
            self.status_detail = "wrong room password"
        elif code == 4002:
            self.status = "rejected"
            self.status_detail = "logged in from another window"
        elif code == 4003:
            self.status = "rejected"
            self.status_detail = reason or "client/server version mismatch"
        else:
            self.status = "offline"
            self.status_detail = reason or ""
        self.inbox.put({"type": "_disconnected", "reason": self.status_detail})

    async def _pump(self, ws):
        async def reader():
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "pong":
                    self.rtt_ms = int((time.time() - msg.get("t", time.time())) * 1000)
                    continue
                self.inbox.put(msg)

        async def writer():
            last_ping = 0.0
            while True:
                try:
                    msg = await asyncio.wait_for(self._out.get(), timeout=1.0)
                    await ws.send(json.dumps(msg))
                except asyncio.TimeoutError:
                    pass
                if self._stop:
                    while not self._out.empty():          # flush last words (e.g. challenge_leave)
                        await ws.send(json.dumps(self._out.get_nowait()))
                    await ws.close()
                    return
                if time.time() - last_ping > 5:
                    last_ping = time.time()
                    await ws.send(json.dumps({"type": "ping", "t": last_ping}))

        r = asyncio.create_task(reader())
        w = asyncio.create_task(writer())
        done, pending = await asyncio.wait({r, w}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            if t.exception():
                raise t.exception()
