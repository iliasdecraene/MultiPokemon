"""YellowLink relay server: auth, presence relay, chat, roster, battles, trades."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from . import PROTOCOL_VERSION
from .config import Config
from .store import PlayerStore

log = logging.getLogger("yellowlink.server")

MAX_MESSAGE_BYTES = 64 * 1024
RATE_LIMIT_PER_SEC = 30
HELLO_TIMEOUT = 10.0
TRADE_REQUEST_TIMEOUT = 60.0
CHAT_MAX = 200
SPRITE_BYTES = 384

CLOSE_BAD_KEY = 4001
CLOSE_REPLACED = 4002
CLOSE_BAD_HELLO = 4003

NAME_RE = re.compile(r"^[A-Za-z0-9 ]{1,10}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")

# ---------------------------------------------------------------- validation


class Invalid(Exception):
    """Raised for a malformed client message; text is sent back as `error`."""


def _b64(value, length: int, what: str) -> bytes:
    if not isinstance(value, str):
        raise Invalid(f"{what} must be a base64 string")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise Invalid(f"{what} is not valid base64") from None
    if len(raw) != length:
        raise Invalid(f"{what} must decode to {length} bytes")
    return raw


def validate_mon(mon) -> dict:
    if not isinstance(mon, dict):
        raise Invalid("mon must be an object")
    data = _b64(mon.get("data"), 44, "mon.data")
    _b64(mon.get("ot"), 11, "mon.ot")
    _b64(mon.get("nick"), 11, "mon.nick")
    if data[0] == 0:
        raise Invalid("mon species must not be 0")
    return {"data": mon["data"], "ot": mon["ot"], "nick": mon["nick"]}


def _int(msg: dict, key: str, lo: int | None = None, hi: int | None = None) -> int:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise Invalid(f"{key} must be an integer")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise Invalid(f"{key} out of range")
    return v


def _str(msg: dict, key: str) -> str:
    v = msg.get(key)
    if not isinstance(v, str) or not v:
        raise Invalid(f"{key} must be a non-empty string")
    return v


def clean_text(text: str, limit: int) -> str:
    return CONTROL_RE.sub("", text).strip()[:limit]


_GEN1 = {0x7F: " ", 0xE3: "-", 0xE6: "?", 0xE7: "!", 0xE8: ".", 0xF3: "/", 0xF4: ","}
_GEN1.update({0x80 + i: chr(ord("A") + i) for i in range(26)})
_GEN1.update({0xA0 + i: chr(ord("a") + i) for i in range(26)})
_GEN1.update({0xF6 + i: chr(ord("0") + i) for i in range(10)})
_GEN1.update({0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";", 0x9E: "[", 0x9F: "]",
              0xE0: "'", 0xEF: "M", 0xF5: "F"})


def decode_gen1(raw: bytes) -> str:
    out = []
    for b in raw:
        if b == 0x50:
            break
        out.append(_GEN1.get(b, ""))
    return "".join(out).strip()


def mon_nick(mon: dict) -> str:
    try:
        return decode_gen1(base64.b64decode(mon["nick"])) or "a Pokemon"
    except Exception:
        return "a Pokemon"


# ------------------------------------------------------------------- models


@dataclass(eq=False)
class Client:
    ws: ServerConnection
    uid: str
    name: str
    version: str = ""
    state: dict | None = None
    sprite: str | None = None
    trade_id: str | None = None
    _rate_window: float = 0.0
    _rate_count: int = 0
    _rate_warned: bool = False

    def allow(self) -> bool:
        now = time.monotonic()
        if now - self._rate_window >= 1.0:
            self._rate_window, self._rate_count, self._rate_warned = now, 0, False
        self._rate_count += 1
        return self._rate_count <= RATE_LIMIT_PER_SEC

    def info(self) -> dict:
        return {"uid": self.uid, "name": self.name, "state": self.state,
                "has_sprite": self.sprite is not None}


@dataclass(eq=False)
class Trade:
    id: str
    a: str  # requester uid
    b: str  # target uid
    status: str = "requested"  # requested | open | executing
    offers: dict = field(default_factory=dict)  # uid -> (slot, mon)
    confirmed: set = field(default_factory=set)
    done: set = field(default_factory=set)
    timer: asyncio.Task | None = None

    def other(self, uid: str) -> str:
        return self.b if uid == self.a else self.a

    def has(self, uid: str) -> bool:
        return uid in (self.a, self.b)


# ------------------------------------------------------------------- server


class YellowLinkServer:
    def __init__(self, config: Config, *, store_debounce: float = 1.0):
        self.config = config
        self.store = PlayerStore(config.data_dir, debounce=store_debounce)
        self.clients: dict[str, Client] = {}
        self.trades: dict[str, Trade] = {}
        # Final outcome of trades that reached "executing": trade_id -> "complete" | "failed".
        # Clients that lost their connection mid-trade ask for this on reconnect.
        self.trade_outcomes: dict[str, str] = {}
        self._server = None

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        self._server = await serve(
            self.handler, self.config.host, self.config.port,
            max_size=MAX_MESSAGE_BYTES, ping_interval=20, ping_timeout=20,
        )
        return self._server

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for t in list(self.trades.values()):
            if t.timer:
                t.timer.cancel()
        await self.store.flush()

    # -------------------------------------------------------------- sending
    async def send(self, client: Client | None, msg: dict) -> None:
        if client is None:
            return
        try:
            await client.ws.send(json.dumps(msg, separators=(",", ":")))
        except ConnectionClosed:
            pass
        except Exception:
            log.exception("send to %s failed", client.name)

    async def broadcast(self, msg: dict, exclude: Client | None = None) -> None:
        data = json.dumps(msg, separators=(",", ":"))
        targets = [c for c in self.clients.values() if c is not exclude]
        await asyncio.gather(*(self._send_raw(c, data) for c in targets))

    async def _send_raw(self, client: Client, data: str) -> None:
        try:
            await client.ws.send(data)
        except ConnectionClosed:
            pass
        except Exception:
            log.exception("send to %s failed", client.name)

    async def system(self, text: str) -> None:
        log.info("system: %s", text)
        await self.broadcast({"type": "system", "text": text})

    def roster(self) -> list[dict]:
        entries = []
        for uid, p in self.store.players.items():
            levels, species = [], []
            for mon in p.get("party", []):
                try:
                    raw = base64.b64decode(mon["data"])
                    species.append(raw[0])
                    levels.append(raw[33])
                except Exception:
                    continue
            online = uid in self.clients
            entries.append({
                "uid": uid,
                "name": self.clients[uid].name if online else p.get("name", "?"),
                "trainer": p.get("trainer", ""),
                "online": online,
                "party_size": len(species),
                "levels": levels,
                "species": species,
                "last_seen": int(time.time()) if online else int(p.get("last_seen", 0)),
            })
        entries.sort(key=lambda e: (not e["online"], e["name"].lower()))
        return entries

    async def broadcast_roster(self) -> None:
        await self.broadcast({"type": "roster", "roster": self.roster()})

    # ------------------------------------------------------------ connection
    async def handler(self, ws: ServerConnection) -> None:
        peer = ws.remote_address
        client = await self._handshake(ws)
        if client is None:
            return
        log.info("%s (%s) connected from %s, client %s", client.name, client.uid, peer, client.version)
        try:
            async for raw in ws:
                if self.clients.get(client.uid) is not client:
                    break  # replaced by a newer connection
                if not client.allow():
                    if not client._rate_warned:
                        client._rate_warned = True
                        log.warning("rate limit: dropping messages from %s", client.name)
                    continue
                await self._dispatch(client, raw)
        except ConnectionClosed:
            pass
        finally:
            await self._disconnect(client)

    async def _handshake(self, ws: ServerConnection) -> Client | None:
        async def reject(message: str, code: int) -> None:
            try:
                await ws.send(json.dumps({"type": "error", "message": message}))
                await ws.close(code, message[:100])
            except ConnectionClosed:
                pass

        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionClosed):
            await reject("hello expected", CLOSE_BAD_HELLO)
            return None
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            msg = None
        if not isinstance(msg, dict) or msg.get("type") != "hello":
            await reject("first message must be hello", CLOSE_BAD_HELLO)
            return None
        key = msg.get("key")
        if not isinstance(key, str) or not hmac.compare_digest(
                key.encode("utf-8"), self.config.key.encode("utf-8")):
            log.warning("bad key from %s", ws.remote_address)
            await reject("wrong room key", CLOSE_BAD_KEY)
            return None
        name, uid = msg.get("name"), msg.get("uid")
        if not isinstance(name, str) or not NAME_RE.match(name) or not name.strip():
            await reject("invalid name (1-10 chars: A-Z a-z 0-9 space)", CLOSE_BAD_HELLO)
            return None
        if not isinstance(uid, str) or not (1 <= len(uid) <= 64):
            await reject("invalid uid", CLOSE_BAD_HELLO)
            return None
        if msg.get("protocol") != PROTOCOL_VERSION:
            await reject(f"protocol mismatch (server speaks {PROTOCOL_VERSION}); please update "
                         f"YellowLink: {self.config.download_url}", CLOSE_BAD_HELLO)
            return None
        version = msg.get("version") if isinstance(msg.get("version"), str) else ""

        client = Client(ws=ws, uid=uid, name=name.strip(), version=version[:32])

        old = self.clients.get(uid)
        if old is not None:
            log.info("uid %s reconnected; kicking old connection", uid)
            await self._cancel_trade_of(old, "partner left")
            del self.clients[uid]
            await self.broadcast({"type": "player_leave", "uid": uid})
            asyncio.get_running_loop().create_task(
                old.ws.close(CLOSE_REPLACED, "logged in from another place"))

        self.store.touch(uid, client.name)
        others = list(self.clients.values())
        self.clients[uid] = client

        await self.send(client, {
            "type": "welcome",
            "you": uid,
            "players": [c.info() for c in others],
            "roster": self.roster(),
            "latest_client": self.config.latest_client,
            "download_url": self.config.download_url,
            "motd": self.config.motd,
        })
        for c in others:
            if c.sprite is not None:
                await self.send(client, {"type": "sprite", "uid": c.uid, "data": c.sprite})
        await self.broadcast({"type": "player_join", "player": client.info()}, exclude=client)
        await self.system(f"{client.name} joined")
        await self.broadcast_roster()
        return client

    async def _disconnect(self, client: Client) -> None:
        if self.clients.get(client.uid) is not client:
            return  # already replaced; the replacement handled cleanup
        del self.clients[client.uid]
        await self._cancel_trade_of(client, "partner left")
        self.store.touch(client.uid, client.name)
        log.info("%s (%s) disconnected", client.name, client.uid)
        await self.broadcast({"type": "player_leave", "uid": client.uid})
        await self.system(f"{client.name} left")
        await self.broadcast_roster()

    # -------------------------------------------------------------- dispatch
    async def _dispatch(self, client: Client, raw) -> None:
        if isinstance(raw, bytes):
            await self.send(client, {"type": "error", "message": "text frames only"})
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            await self.send(client, {"type": "error", "message": "invalid JSON"})
            return
        if not isinstance(msg, dict):
            await self.send(client, {"type": "error", "message": "message must be an object"})
            return
        mtype = msg.get("type")
        fn = self._handlers.get(mtype) if isinstance(mtype, str) else None
        if fn is None:
            await self.send(client, {"type": "error", "message": f"unknown type {mtype!r}"})
            return
        try:
            await fn(self, client, msg)
        except Invalid as exc:
            await self.send(client, {"type": "error", "message": str(exc)})
        except Exception:
            log.exception("error handling %s from %s", mtype, client.name)
            await self.send(client, {"type": "error", "message": "internal server error"})

    # ------------------------------------------------------- basic messages
    async def on_hello(self, client: Client, msg: dict) -> None:
        raise Invalid("already said hello")

    async def on_ping(self, client: Client, msg: dict) -> None:
        await self.send(client, {"type": "pong", "t": msg.get("t")})

    async def on_state(self, client: Client, msg: dict) -> None:
        state = {k: _int(msg, k) for k in ("map", "x", "y", "px", "py", "facing", "img")}
        vis = msg.get("visible")
        if not isinstance(vis, bool):
            raise Invalid("visible must be a boolean")
        state["visible"] = vis
        client.state = state
        await self.broadcast({"type": "state", "uid": client.uid, **state}, exclude=client)

    async def on_sprite(self, client: Client, msg: dict) -> None:
        _b64(msg.get("data"), SPRITE_BYTES, "sprite data")
        client.sprite = msg["data"]
        await self.broadcast({"type": "sprite", "uid": client.uid, "data": client.sprite},
                             exclude=client)

    async def on_party(self, client: Client, msg: dict) -> None:
        mons = msg.get("mons")
        if not isinstance(mons, list) or len(mons) > 6:
            raise Invalid("mons must be a list of 0..6 mons")
        mons = [validate_mon(m) for m in mons]
        trainer = msg.get("trainer", "")
        if not isinstance(trainer, str):
            raise Invalid("trainer must be a string")
        trainer = clean_text(trainer, 20)
        badges = _int(msg, "badges", 0, 255)
        if self.store.set_party(client.uid, mons, trainer, badges):
            await self.broadcast_roster()

    async def on_chat(self, client: Client, msg: dict) -> None:
        text = msg.get("text")
        if not isinstance(text, str):
            raise Invalid("text must be a string")
        text = clean_text(text, CHAT_MAX)
        if not text:
            return
        log.info("chat <%s> %s", client.name, text)
        await self.broadcast({"type": "chat", "uid": client.uid, "name": client.name, "text": text})

    async def on_get_party(self, client: Client, msg: dict) -> None:
        uid = msg.get("uid")
        p = self.store.get(uid) if isinstance(uid, str) else None
        if p is None:
            await self.send(client, {"type": "party_data", "uid": uid, "error": "unknown player"})
            return
        await self.send(client, {"type": "party_data", "uid": uid, "name": p["name"],
                                 "trainer": p["trainer"], "mons": p["party"]})

    async def on_battle_result(self, client: Client, msg: dict) -> None:
        opp_uid = msg.get("opponent_uid")
        won = msg.get("won")
        if not isinstance(won, bool):
            raise Invalid("won must be a boolean")
        opp = self.store.get(opp_uid) if isinstance(opp_uid, str) else None
        if opp is None or opp_uid == client.uid:
            raise Invalid("unknown opponent")
        opp_name = self.clients[opp_uid].name if opp_uid in self.clients else opp["name"]
        if won:
            await self.system(f"{client.name} beat {opp_name}'s team!")
        else:
            await self.system(f"{opp_name}'s team defeated {client.name}!")

    # ---------------------------------------------------------------- trades
    def _trade_for(self, client: Client, msg: dict) -> Trade:
        tid = msg.get("trade_id")
        if not tid and client.trade_id:
            tid = client.trade_id  # lenient: requester may not know the id yet
        trade = self.trades.get(tid) if isinstance(tid, str) else None
        if trade is None or not trade.has(client.uid):
            raise Invalid("no such trade")
        return trade

    async def _end_trade(self, trade: Trade) -> None:
        self.trades.pop(trade.id, None)
        if trade.timer is not None and trade.timer is not asyncio.current_task():
            trade.timer.cancel()
        for uid in (trade.a, trade.b):
            c = self.clients.get(uid)
            if c is not None and c.trade_id == trade.id:
                c.trade_id = None

    def _record_outcome(self, trade: Trade, outcome: str) -> None:
        self.trade_outcomes[trade.id] = outcome
        while len(self.trade_outcomes) > 500:
            self.trade_outcomes.pop(next(iter(self.trade_outcomes)))

    async def _cancel(self, trade: Trade, reason: str, notify: tuple[str, ...]) -> None:
        log.info("trade %s cancelled: %s", trade.id, reason)
        if trade.status == "executing":
            self._record_outcome(trade, "failed")
        await self._end_trade(trade)
        for uid in notify:
            await self.send(self.clients.get(uid),
                            {"type": "trade_cancelled", "trade_id": trade.id, "reason": reason})

    async def _cancel_trade_of(self, client: Client, reason: str) -> None:
        trade = self.trades.get(client.trade_id) if client.trade_id else None
        client.trade_id = None
        if trade is None:
            return
        other = trade.other(client.uid)
        if trade.status == "executing":
            reason = "execution failed"
        await self._cancel(trade, reason, (other,))

    async def _expire(self, trade: Trade) -> None:
        await asyncio.sleep(TRADE_REQUEST_TIMEOUT)
        if self.trades.get(trade.id) is trade and trade.status == "requested":
            await self._cancel(trade, "timeout", (trade.a, trade.b))

    async def _send_confirm_state(self, trade: Trade) -> None:
        for uid in (trade.a, trade.b):
            other = trade.other(uid)
            await self.send(self.clients.get(uid), {
                "type": "trade_confirm_state", "trade_id": trade.id,
                "you": uid in trade.confirmed, "them": other in trade.confirmed,
            })

    async def on_trade_request(self, client: Client, msg: dict) -> None:
        to = msg.get("to")
        target = self.clients.get(to) if isinstance(to, str) else None
        if target is None:
            raise Invalid("that player is not online")
        if target is client:
            raise Invalid("cannot trade with yourself")
        if client.trade_id:
            raise Invalid("you are already in a trade")
        if target.trade_id:
            raise Invalid(f"{target.name} is busy with another trade")
        trade = Trade(id=uuid.uuid4().hex[:12], a=client.uid, b=target.uid)
        self.trades[trade.id] = trade
        client.trade_id = target.trade_id = trade.id
        trade.timer = asyncio.get_running_loop().create_task(self._expire(trade))
        log.info("trade %s requested: %s -> %s", trade.id, client.name, target.name)
        await self.send(target, {"type": "trade_request", "trade_id": trade.id,
                                 "from": client.uid, "name": client.name})

    async def on_trade_respond(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status != "requested" or client.uid != trade.b:
            raise Invalid("cannot respond to this trade")
        accept = msg.get("accept")
        if not isinstance(accept, bool):
            raise Invalid("accept must be a boolean")
        if not accept:
            await self._cancel(trade, "declined", (trade.a, trade.b))
            return
        trade.status = "open"
        if trade.timer is not None:
            trade.timer.cancel()
            trade.timer = None
        a, b = self.clients.get(trade.a), self.clients.get(trade.b)
        log.info("trade %s open", trade.id)
        await self.send(a, {"type": "trade_started", "trade_id": trade.id, "with": trade.b,
                            "name": b.name if b else "?"})
        await self.send(b, {"type": "trade_started", "trade_id": trade.id, "with": trade.a,
                            "name": a.name if a else "?"})

    async def on_trade_offer(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status != "open":
            raise Invalid("trade is not open for offers")
        slot = _int(msg, "slot", 0, 5)
        mon = validate_mon(msg.get("mon"))
        trade.offers[client.uid] = (slot, mon)
        had_confirmations = bool(trade.confirmed)
        trade.confirmed.clear()
        await self.send(self.clients.get(trade.other(client.uid)),
                        {"type": "trade_offer", "trade_id": trade.id, "from": client.uid,
                         "slot": slot, "mon": mon})
        if had_confirmations:
            await self._send_confirm_state(trade)

    async def on_trade_confirm(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status != "open":
            raise Invalid("trade is not open")
        if len(trade.offers) < 2:
            raise Invalid("both players must offer a Pokemon first")
        trade.confirmed.add(client.uid)
        await self._send_confirm_state(trade)
        if trade.confirmed >= {trade.a, trade.b}:
            trade.status = "executing"
            log.info("trade %s executing", trade.id)
            sends = []
            for uid in (trade.a, trade.b):
                my_slot, _ = trade.offers[uid]
                _, their_mon = trade.offers[trade.other(uid)]
                sends.append(self.send(self.clients.get(uid), {
                    "type": "trade_execute", "trade_id": trade.id,
                    "give_slot": my_slot, "receive": their_mon}))
            await asyncio.gather(*sends)

    async def on_trade_cancel(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status == "executing":
            raise Invalid("trade is already executing")
        await self._cancel(trade, "cancelled", (trade.a, trade.b))

    async def on_trade_done(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status != "executing":
            raise Invalid("trade is not executing")
        ok = msg.get("ok")
        if not isinstance(ok, bool):
            raise Invalid("ok must be a boolean")
        if not ok:
            await self._cancel(trade, "execution failed", (trade.other(client.uid),))
            return
        trade.done.add(client.uid)
        if trade.done >= {trade.a, trade.b}:
            self._record_outcome(trade, "complete")
            await self._end_trade(trade)
            for uid in (trade.a, trade.b):
                await self.send(self.clients.get(uid),
                                {"type": "trade_complete", "trade_id": trade.id})
            name_a = self.store.get(trade.a)["name"]
            name_b = self.store.get(trade.b)["name"]
            nick_a = mon_nick(trade.offers[trade.a][1])
            nick_b = mon_nick(trade.offers[trade.b][1])
            await self.system(f"{name_a} traded {nick_a} with {name_b} for {nick_b}!")

    async def on_trade_status(self, client: Client, msg: dict) -> None:
        tid = _str(msg, "trade_id")
        state = self.trade_outcomes.get(tid)
        if state is None:
            state = "pending" if tid in self.trades else "unknown"
        await self.send(client, {"type": "trade_status", "trade_id": tid, "state": state})

    _handlers = {
        "hello": on_hello,
        "ping": on_ping,
        "trade_status": on_trade_status,
        "state": on_state,
        "sprite": on_sprite,
        "party": on_party,
        "chat": on_chat,
        "get_party": on_get_party,
        "battle_result": on_battle_result,
        "trade_request": on_trade_request,
        "trade_respond": on_trade_respond,
        "trade_offer": on_trade_offer,
        "trade_confirm": on_trade_confirm,
        "trade_cancel": on_trade_cancel,
        "trade_done": on_trade_done,
    }
