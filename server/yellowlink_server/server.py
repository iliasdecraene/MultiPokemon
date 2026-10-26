"""YellowLink relay server: auth, presence relay, chat, roster, battles, trades."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import random
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from . import PROTOCOL_VERSION
from .config import Config
from . import pcstore
from .challenges import ChallengeError, ChallengeHub
from .store import PlayerStore

log = logging.getLogger("yellowlink.server")

MAX_MESSAGE_BYTES = 256 * 1024   # a full PC store is ~100-150 KB
RATE_LIMIT_PER_SEC = 30
HELLO_TIMEOUT = 10.0
TRADE_REQUEST_TIMEOUT = 60.0
MAX_OFFER = 20                   # Pokémon per side in one trade/gift (a box)
BATTLE_REQUEST_TIMEOUT = 60.0
BATTLE_PREPARE_TIMEOUT = 60.0
BATTLE_RNS_COUNT = 10
BATTLE_RNS_MAX = 252
BATTLE_CHECK_MAX = 64
# Trade/battle messages get their own rate budget so position spam can never cause a
# dropped battle_move (which would desync a link battle) or a dropped trade_done.
CRITICAL_PREFIXES = ("trade_", "battle_", "challenge_")
CHAT_MAX = 200
SPRITE_BYTES = 384

CLOSE_BAD_KEY = 4001
CLOSE_REPLACED = 4002
CLOSE_BAD_HELLO = 4003
CLOSE_MOVED = 4005          # this server was merged into another one (Config.moved_to); see SPEC.md section 8
MOVE_SUPPORTED = (0, 7, 1)  # clients from this version on follow a "moved" message by themselves


def _version_tuple(v: str) -> tuple:
    out = []
    for part in str(v or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)

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
    if not isinstance(mon.get("data"), str):
        raise Invalid("mon.data must be a base64 string")
    # 44 bytes = Gen 1 (Yellow) party struct, 48 bytes = Gen 2 (Crystal)
    size = 48 if len(mon["data"]) == 64 else 44
    data = _b64(mon.get("data"), size, "mon.data")
    _b64(mon.get("ot"), 11, "mon.ot")
    _b64(mon.get("nick"), 11, "mon.nick")
    if data[0] == 0:
        raise Invalid("mon species must not be 0")
    return {"data": mon["data"], "ot": mon["ot"], "nick": mon["nick"]}


def mons_text(entries: list) -> str:
    """'PIKA', 'PIKA and RATTY', 'PIKA, RATTY and 3 more'."""
    nicks = [(e or {}).get("nick") or "a Pokémon" for e in entries]
    if not nicks:
        return "nothing"
    if len(nicks) == 1:
        return nicks[0]
    if len(nicks) <= 3:
        return ", ".join(nicks[:-1]) + " and " + nicks[-1]
    return f"{nicks[0]}, {nicks[1]} and {len(nicks) - 2} more"


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
    battle_id: str | None = None
    challenge_id: str | None = None   # set while in (or loading into) a challenge session
    games: list | None = None         # installed games (from `library`), None = unknown (old client)
    last_report: float = 0.0
    _rate_window: float = 0.0
    _rate_count: int = 0
    _rate_warned: bool = False
    _crit_count: int = 0
    limited: bool = False             # moved server + old client: only shown the update notice

    def allow(self, critical: bool = False) -> bool:
        """Per-second budget: RATE_LIMIT_PER_SEC general messages plus the same number of
        trade/battle messages, counted separately."""
        now = time.monotonic()
        if now - self._rate_window >= 1.0:
            self._rate_window, self._rate_count, self._crit_count = now, 0, 0
            self._rate_warned = False
        if critical:
            self._crit_count += 1
            return self._crit_count <= RATE_LIMIT_PER_SEC
        self._rate_count += 1
        return self._rate_count <= RATE_LIMIT_PER_SEC

    @property
    def busy(self) -> bool:
        return bool(self.trade_id or self.battle_id or self.challenge_id)

    def info(self) -> dict:
        return {"uid": self.uid, "name": self.name, "state": self.state,
                "has_sprite": self.sprite is not None}


@dataclass(eq=False)
class Trade:
    id: str
    a: str  # requester uid
    b: str  # target uid
    status: str = "requested"  # requested | open | applying
    gift: bool = False          # a gives, b only receives
    offers: dict = field(default_factory=dict)  # uid -> [PC entries] (the server's stored copies)
    confirmed: set = field(default_factory=set)
    done: set = field(default_factory=set)
    timer: asyncio.Task | None = None

    def other(self, uid: str) -> str:
        return self.b if uid == self.a else self.a

    def has(self, uid: str) -> bool:
        return uid in (self.a, self.b)


@dataclass(eq=False)
class Battle:
    id: str
    a: str  # requester uid (master)
    b: str  # target uid
    status: str = "requested"  # requested | preparing | running
    ready: dict = field(default_factory=dict)  # uid -> {"mons": [...], "trainer": str}
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
        self.battles: dict[str, Battle] = {}
        self.challenges = ChallengeHub(self, config.data_dir, debounce=store_debounce)
        self._server = None

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        self._replay_journals()
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
        for t in [*self.trades.values(), *self.battles.values()]:
            if t.timer:
                t.timer.cancel()
        await self.challenges.flush()
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
                    levels.append(raw[31] if len(raw) == 48 else raw[33])   # Crystal : Yellow
                except Exception:
                    continue
            online = uid in self.clients
            entries.append({
                "uid": uid,
                "name": self.clients[uid].name if online else p.get("name", "?"),
                "trainer": p.get("trainer", ""),
                "game": (self.clients[uid].state or {}).get("game") if online and self.clients[uid].state
                else p.get("game"),
                "games": (self.clients[uid].games if online and self.clients[uid].games is not None
                          else p.get("games")),
                "challenge": self._challenge_kind(self.clients[uid]) if online else None,
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
                msg = self._parse(raw)
                mtype = msg.get("type") if isinstance(msg, dict) else None
                critical = isinstance(mtype, str) and mtype.startswith(CRITICAL_PREFIXES)
                if not client.allow(critical):
                    if not client._rate_warned:
                        client._rate_warned = True
                        log.warning("rate limit: dropping messages from %s", client.name)
                    continue
                await self._dispatch(client, msg)
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
        version = msg.get("version") if isinstance(msg.get("version"), str) else ""

        if msg.get("mode") == "report":
            # Report-only connection (client couldn't stay connected): save one report, don't log in.
            try:
                raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT)
                rep_msg = json.loads(raw)
                text = rep_msg.get("text") if isinstance(rep_msg, dict) else None
                if isinstance(text, str) and text.strip():
                    fname = self._save_report(name.strip(), uid, text, str(rep_msg.get("reason", "")))
                    await ws.send(json.dumps({"type": "report_saved", "file": fname}))
                await ws.close()
            except (asyncio.TimeoutError, ConnectionClosed, ValueError, TypeError):
                pass
            return None

        if msg.get("protocol") != PROTOCOL_VERSION:
            await reject(f"protocol mismatch (server speaks {PROTOCOL_VERSION}); please update "
                         f"YellowLink: {self.config.download_url}", CLOSE_BAD_HELLO)
            return None
        if self.config.moved_to:
            return await self._moved_hello(ws, uid, name.strip(), version[:32])
        client = Client(ws=ws, uid=uid, name=name.strip(), version=version[:32])

        old = self.clients.get(uid)
        if old is not None:
            log.info("uid %s reconnected; kicking old connection", uid)
            await self._cancel_trade_of(old, "partner left")
            await self._abort_battle_of(old, disconnected=True)
            del self.clients[uid]
            await self.challenges.client_gone(old)
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
            "server_name": self.config.name,
        })
        await self.send(client, {"type": "challenge_list", "challenges": self.challenges.mine(uid)})
        for c in others:
            if c.sprite is not None:
                await self.send(client, {"type": "sprite", "uid": c.uid, "data": c.sprite})
        await self.broadcast({"type": "player_join", "player": client.info()}, exclude=client)
        await self.system(f"{client.name} joined")
        await self.broadcast_roster()
        return client

    async def _moved_hello(self, ws: ServerConnection, uid: str, name: str, version: str) -> Client | None:
        """This server was merged into another one: send new clients there, show older ones the update notice."""
        if _version_tuple(version) >= MOVE_SUPPORTED:
            log.info("%s (%s) told the server moved to %s", name, uid, self.config.moved_to)
            try:
                await ws.send(json.dumps({"type": "moved", "url": self.config.moved_to,
                                          "key": self.config.moved_key}))
                await ws.close(CLOSE_MOVED, "server moved")
            except ConnectionClosed:
                pass
            return None
        log.info("%s (%s) on old client %s: update notice only (server moved)", name, uid, version or "?")
        client = Client(ws=ws, uid=uid, name=name, version=version, limited=True)
        await self.send(client, {
            "type": "welcome", "you": uid, "players": [], "roster": [],
            "latest_client": self.config.latest_client, "download_url": self.config.download_url,
            "motd": "This server has moved. Press U to update YellowLink - it then moves you to the new server by "
                    "itself, with all your saves.",
            "server_name": self.config.name,
        })
        return client

    async def _disconnect(self, client: Client) -> None:
        if self.clients.get(client.uid) is not client:
            return  # already replaced; the replacement handled cleanup
        del self.clients[client.uid]
        await self._cancel_trade_of(client, "partner left")
        await self._abort_battle_of(client, disconnected=True)
        await self.challenges.client_gone(client)
        self.store.touch(client.uid, client.name)
        log.info("%s (%s) disconnected", client.name, client.uid)
        await self.broadcast({"type": "player_leave", "uid": client.uid})
        await self.system(f"{client.name} left")
        await self.broadcast_roster()

    # -------------------------------------------------------------- dispatch
    @staticmethod
    def _parse(raw) -> dict | str:
        """Decode a frame: the message dict, or an error string for the client."""
        if isinstance(raw, bytes):
            return "text frames only"
        try:
            msg = json.loads(raw)
        except ValueError:
            return "invalid JSON"
        if not isinstance(msg, dict):
            return "message must be an object"
        return msg

    async def _dispatch(self, client: Client, msg) -> None:
        if client.limited:
            return                    # moved server: an old client can only read the update notice
        if not isinstance(msg, dict):
            msg = self._parse(msg)  # raw frame (kept for callers passing text)
        if isinstance(msg, str):
            await self.send(client, {"type": "error", "message": msg})
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
        game = msg.get("game", "yellow")       # older clients only ever play Yellow
        if not isinstance(game, str) or not re.fullmatch(r"[a-z0-9]{1,16}", game):
            raise Invalid("game must be a short lowercase id")
        state["game"] = game
        chal = msg.get("challenge")
        if chal is not None:
            if not isinstance(chal, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", chal):
                raise Invalid("challenge must be a short id")
            state["challenge"] = chal
        client.state = state
        await self.broadcast({"type": "state", "uid": client.uid, **state}, exclude=client)
        if chal is None and self.store.set_game(client.uid, game):
            await self.broadcast_roster()           # the trainer list shows everyone's region

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
        badges = _int(msg, "badges", 0, 0xFFFF)     # Crystal: Johto badges | Kanto badges << 8
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
        # chat goes to everyone, whatever game they're in; "game" lets clients show the sender's region
        game = (client.state or {}).get("game") or (self.store.get(client.uid) or {}).get("game")
        await self.broadcast({"type": "chat", "uid": client.uid, "name": client.name, "text": text, "game": game,
                              "challenge": self._challenge_kind(client)})

    async def on_get_party(self, client: Client, msg: dict) -> None:
        uid = msg.get("uid")
        p = self.store.get(uid) if isinstance(uid, str) else None
        if p is None:
            await self.send(client, {"type": "party_data", "uid": uid, "error": "unknown player"})
            return
        await self.send(client, {"type": "party_data", "uid": uid, "name": p["name"],
                                 "trainer": p["trainer"], "mons": p["party"]})

    async def on_bug_report(self, client: Client, msg: dict) -> None:
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            raise Invalid("text must be a non-empty string")
        now = time.time()
        if now - client.last_report < 20:
            raise Invalid("please wait a moment before sending another report")
        client.last_report = now
        fname = self._save_report(client.name, client.uid, text, str(msg.get("reason", "")))
        await self.send(client, {"type": "report_saved", "file": fname})

    def _save_report(self, name: str, uid: str, text: str, reason: str) -> str:
        reason = clean_text(reason, 60) or "report"
        folder = os.path.join(self.config.data_dir, "reports")
        os.makedirs(folder, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9]", "", name) or "player"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(folder, f"{stamp}-{safe_name}-{uuid.uuid4().hex[:4]}.txt")
        with open(path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(f"From: {name} ({uid})  reason: {reason}\n\n")
            fh.write(text[:60000])
        # keep the newest 200 reports
        files = sorted(f for f in os.listdir(folder) if f.endswith(".txt"))
        for old in files[:-200]:
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
        log.info("bug report from %s saved to %s", name, path)
        return os.path.basename(path)

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
        if client.busy:
            raise Invalid("you are already in a trade, battle or challenge")
        if target.busy:
            raise Invalid(f"{target.name} is busy right now")
        gift = bool(msg.get("gift"))
        trade = Trade(id=uuid.uuid4().hex[:12], a=client.uid, b=target.uid, gift=gift)
        self.trades[trade.id] = trade
        client.trade_id = target.trade_id = trade.id
        trade.timer = asyncio.get_running_loop().create_task(self._expire(trade))
        log.info("%s %s requested: %s -> %s", "gift" if gift else "trade", trade.id, client.name, target.name)
        await self.send(target, {"type": "trade_request", "trade_id": trade.id,
                                 "from": client.uid, "name": client.name, "gift": gift})

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
        extra = {"gift": trade.gift, "giver": trade.a if trade.gift else None}
        await self.send(a, {"type": "trade_started", "trade_id": trade.id, "with": trade.b,
                            "name": b.name if b else "?", **extra})
        await self.send(b, {"type": "trade_started", "trade_id": trade.id, "with": trade.a,
                            "name": a.name if a else "?", **extra})

    async def on_trade_offer(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status != "open":
            raise Invalid("trade is not open for offers")
        if trade.gift and client.uid != trade.a:
            raise Invalid("this is a gift - you just receive")
        ids = msg.get("entry_ids")
        if ids is None:
            ids = [_str(msg, "entry_id")]                    # 0.6.1 clients offer one at a time
        if not isinstance(ids, list) or len(ids) > MAX_OFFER or not all(isinstance(i, str) for i in ids) \
                or len(set(ids)) != len(ids):
            raise Invalid(f"offer 0-{MAX_OFFER} different Pokémon")
        store = self._pc_load(client.uid)
        entries = []
        for eid in ids:
            hit = pcstore.find(store, eid) if self._pc_valid(store) else None
            if hit is None:
                raise Invalid("a Pokémon isn't in your saved PC yet - wait a few seconds and try again")
            if not pcstore.valid_entry(hit[2]):
                raise Invalid(f"{hit[2].get('nick', 'a Pokémon')}'s saved data is damaged and can't be traded")
            entries.append(hit[2])
        if [e["id"] for e in trade.offers.get(client.uid, [])] == ids:
            return
        if entries:
            trade.offers[client.uid] = entries
        else:
            trade.offers.pop(client.uid, None)
        had_confirmations = bool(trade.confirmed)
        trade.confirmed.clear()
        await self.send(self.clients.get(trade.other(client.uid)),
                        {"type": "trade_offer", "trade_id": trade.id, "from": client.uid, "entries": entries,
                         "entry": entries[0] if entries else None})
        if had_confirmations:
            await self._send_confirm_state(trade)

    async def on_trade_confirm(self, client: Client, msg: dict) -> None:
        trade = self._trade_for(client, msg)
        if trade.status != "open":
            raise Invalid("trade is not open")
        needed = {trade.a} if trade.gift else {trade.a, trade.b}
        if not needed <= set(trade.offers):
            raise Invalid("the giver must choose a Pokémon first" if trade.gift
                          else "both players must offer a Pokémon first")
        trade.confirmed.add(client.uid)
        await self._send_confirm_state(trade)
        if trade.confirmed >= {trade.a, trade.b}:
            await self._apply_trade(trade)

    async def _apply_trade(self, trade: Trade) -> None:
        """Swap the offered entries between both stored PCs (SPEC.md: trade state machine).

        Crash-safe: the full plan is written to a journal first; the journal is replayed on startup until both
        stores list the trade id, so a crash between the two saves can't duplicate or lose anything."""
        trade.status = "applying"
        try:
            plan = self._plan_trade(trade)
        except Exception:  # noqa: BLE001 - never leave a trade (and both players) stuck
            log.exception("trade %s: planning failed", trade.id)
            plan = "the trade failed (server error)"
        if isinstance(plan, str):
            await self._cancel(trade, plan, (trade.a, trade.b))
            return
        self._journal_write(plan)
        self._record_outcome(trade, "complete")
        await self._end_trade(trade)
        for attempt in range(1000):
            try:
                deltas = self._journal_apply(plan)
                break
            except Exception:  # noqa: BLE001 - disk trouble: the journal keeps the trade safe; retry
                log.exception("trade %s: applying failed (attempt %d); retrying", trade.id, attempt + 1)
                await asyncio.sleep(5)
        else:
            return
        log.info("trade %s applied to both PCs", trade.id)
        for uid in (trade.a, trade.b):
            c = self.clients.get(uid)
            d = deltas[uid]
            await self.send(c, {"type": "pc_trades", "deltas": [d]})
            gave = plan["gives"][uid]
            evolved = plan["evolved"].get(uid) or []
            await self.send(c, {"type": "trade_complete", "trade_id": trade.id,
                                "gave_all": gave, "received_all": d["add"], "evolved_all": evolved,
                                # single-Pokémon fields for 0.6.1 clients
                                "gave": gave[0] if gave else None, "received": d["add"][0] if d["add"] else None,
                                "evolved": evolved[0] if evolved else None})
        name = lambda u: (self.store.get(u) or {}).get("name", "?")  # noqa: E731
        gives = plan["gives"]
        if trade.gift:
            await self.system(f"{name(trade.a)} gave {mons_text(gives[trade.a])} to {name(trade.b)}!")
        else:
            await self.system(f"{name(trade.a)} traded {mons_text(gives[trade.a])} with {name(trade.b)} "
                              f"for {mons_text(gives[trade.b])}!")

    def _plan_trade(self, trade: Trade):
        """Check everything and compute both deltas. Returns the plan, or a reason string to cancel."""
        stores = {}
        for uid in (trade.a, trade.b):
            st, status = self._pc_load_status(uid)
            c = self.clients.get(uid)
            who = c.name if c else "a player"
            if status == "corrupt":
                return f"{who}'s saved PC couldn't be read - ask the host"
            st = st or pcstore.empty_store()
            if len(st.get("inbox") or []) >= pcstore.INBOX_MAX:
                return f"{who} has too many trades still syncing - try again in a minute"
            stores[uid] = st
        gives = {uid: list(trade.offers.get(uid) or []) for uid in (trade.a, trade.b)}
        for uid, entries in gives.items():
            if any(pcstore.find(stores[uid], e["id"]) is None for e in entries):
                return "a Pokémon was moved out of the PC"
        plan = {"trade_id": trade.id, "a": trade.a, "b": trade.b, "gives": gives, "evolved": {}, "deltas": {}}
        for uid in (trade.a, trade.b):
            add, names = [], []
            for entry in gives[trade.other(uid)]:
                evolved, new_name = pcstore.evolve_for_trade(entry)
                add.append(evolved)
                if new_name:
                    names.append(new_name)
            if names:
                plan["evolved"][uid] = names
            room = sum(pcstore.MONS_PER_BOX - len(b) for b in stores[uid]["boxes"]) + len(gives[uid])
            if room < len(add):
                c = self.clients.get(uid)
                who = f"{c.name}'s" if c else "their"
                return f"{who} PC is full (room for {max(0, room)} more, needs {len(add)})"
            plan["deltas"][uid] = {"remove": [e["id"] for e in gives[uid]], "add": add}
        return plan

    def _journal_dir(self) -> str:
        d = os.path.join(self.config.data_dir, "pc", "journal")
        os.makedirs(d, exist_ok=True)
        return d

    def _journal_write(self, plan: dict) -> None:
        path = os.path.join(self._journal_dir(), f"{plan['trade_id']}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _journal_apply(self, plan: dict) -> dict:
        """Apply a journalled trade to both stores (idempotent), then delete the journal."""
        out = {}
        for uid in (plan["a"], plan["b"]):
            st, status = self._pc_load_status(uid)
            if status == "corrupt":
                raise RuntimeError(f"PC store of {uid} unreadable")
            st = st or pcstore.empty_store()
            d = plan["deltas"][uid]
            out[uid] = pcstore.apply_delta(st, plan["trade_id"], d["remove"], d["add"])
            self._pc_save(uid, st)
        os.remove(os.path.join(self._journal_dir(), f"{plan['trade_id']}.json"))
        return out

    def _replay_journals(self) -> None:
        for name in sorted(os.listdir(self._journal_dir())):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._journal_dir(), name), encoding="utf-8") as fh:
                    plan = json.load(fh)
                self._journal_apply(plan)
                log.warning("finished an interrupted trade from the journal: %s", plan.get("trade_id"))
            except Exception:  # noqa: BLE001
                log.exception("could not replay trade journal %s", name)

    async def on_trade_status(self, client: Client, msg: dict) -> None:
        """Outcome of a trade this client offered in but lost track of (connection dropped)."""
        tid = _str(msg, "trade_id")
        st, _ = self._pc_load_status(client.uid)
        if self.trade_outcomes.get(tid) == "complete" or (st and pcstore.applied(st, tid)):
            state = "complete"
        elif tid in self.trades or os.path.exists(os.path.join(self._journal_dir(), f"{tid}.json")):
            state = "pending"
        else:
            state = "failed"
        await self.send(client, {"type": "trade_status", "trade_id": tid, "state": state})

    async def on_trade_cancel(self, client: Client, msg: dict) -> None:
        tid = msg.get("trade_id") or client.trade_id
        trade = self.trades.get(tid) if isinstance(tid, str) else None
        if trade is None or not trade.has(client.uid) or trade.status == "applying":
            return                                    # already finished/cancelled: nothing to do
        await self._cancel(trade, "cancelled", (trade.a, trade.b))

    # --------------------------------------------------------------- battles
    def _battle_for(self, client: Client, msg: dict) -> Battle | None:
        """The client's battle named by battle_id (empty id = own battle), or None."""
        bid = msg.get("battle_id")
        if not bid and client.battle_id:
            bid = client.battle_id
        battle = self.battles.get(bid) if isinstance(bid, str) else None
        if battle is None or not battle.has(client.uid):
            return None
        return battle

    def _end_battle(self, battle: Battle) -> None:
        self.battles.pop(battle.id, None)
        if battle.timer is not None and battle.timer is not asyncio.current_task():
            battle.timer.cancel()
        battle.timer = None
        for uid in (battle.a, battle.b):
            c = self.clients.get(uid)
            if c is not None and c.battle_id == battle.id:
                c.battle_id = None

    async def _abort_battle(self, battle: Battle, reason: str, notify: tuple[str, ...]) -> None:
        log.info("battle %s aborted: %s", battle.id, reason)
        self._end_battle(battle)
        for uid in notify:
            await self.send(self.clients.get(uid),
                            {"type": "battle_aborted", "battle_id": battle.id, "reason": reason})

    async def _abort_battle_of(self, client: Client, *, disconnected: bool) -> None:
        battle = self.battles.get(client.battle_id) if client.battle_id else None
        client.battle_id = None
        if battle is None:
            return
        if battle.status == "running":
            reason = "opponent disconnected" if disconnected else "opponent left"
        else:
            reason = "cancelled"
        await self._abort_battle(battle, reason, (battle.other(client.uid),))

    def _start_battle_timer(self, battle: Battle, status: str, timeout: float) -> None:
        if battle.timer is not None:
            battle.timer.cancel()

        async def expire():
            await asyncio.sleep(timeout)
            if self.battles.get(battle.id) is battle and battle.status == status:
                await self._abort_battle(battle, "timeout", (battle.a, battle.b))

        battle.timer = asyncio.get_running_loop().create_task(expire())

    async def on_battle_request(self, client: Client, msg: dict) -> None:
        to = msg.get("to")
        target = self.clients.get(to) if isinstance(to, str) else None
        if target is None:
            raise Invalid("that player is not online")
        if target is client:
            raise Invalid("cannot battle yourself")
        if client.busy:
            raise Invalid("you are already in a trade or battle")
        if target.busy:
            raise Invalid(f"{target.name} is busy with another trade or battle")
        battle = Battle(id=uuid.uuid4().hex[:12], a=client.uid, b=target.uid)
        self.battles[battle.id] = battle
        client.battle_id = target.battle_id = battle.id
        self._start_battle_timer(battle, "requested", BATTLE_REQUEST_TIMEOUT)
        log.info("battle %s requested: %s -> %s", battle.id, client.name, target.name)
        await self.send(target, {"type": "battle_request", "battle_id": battle.id,
                                 "from": client.uid, "name": client.name})

    async def on_battle_respond(self, client: Client, msg: dict) -> None:
        battle = self._battle_for(client, msg)
        if battle is None or battle.status != "requested" or client.uid != battle.b:
            raise Invalid("cannot respond to this battle")
        accept = msg.get("accept")
        if not isinstance(accept, bool):
            raise Invalid("accept must be a boolean")
        if not accept:
            await self._abort_battle(battle, "declined", (battle.a,))
            return
        battle.status = "preparing"
        self._start_battle_timer(battle, "preparing", BATTLE_PREPARE_TIMEOUT)
        log.info("battle %s preparing", battle.id)
        for uid in (battle.a, battle.b):
            other = self.clients.get(battle.other(uid))
            await self.send(self.clients.get(uid), {
                "type": "battle_prepare", "battle_id": battle.id, "with": battle.other(uid),
                "name": other.name if other else "?", "master": uid == battle.a})

    async def on_battle_ready(self, client: Client, msg: dict) -> None:
        battle = self._battle_for(client, msg)
        if battle is None or battle.status != "preparing":
            raise Invalid("battle is not preparing")
        mons = msg.get("mons")
        if not isinstance(mons, list) or not 1 <= len(mons) <= 6:
            raise Invalid("mons must be a list of 1..6 mons")
        mons = [validate_mon(m) for m in mons]
        trainer = msg.get("trainer", "")
        if not isinstance(trainer, str):
            raise Invalid("trainer must be a string")
        battle.ready[client.uid] = {"mons": mons, "trainer": clean_text(trainer, 20)}
        if len(battle.ready) < 2:
            return
        battle.status = "running"
        if battle.timer is not None:
            battle.timer.cancel()
            battle.timer = None
        rng = random.SystemRandom()
        rns = [rng.randint(0, BATTLE_RNS_MAX) for _ in range(BATTLE_RNS_COUNT)]
        log.info("battle %s running", battle.id)
        sends = []
        for uid in (battle.a, battle.b):
            opp_uid = battle.other(uid)
            opp = self.clients.get(opp_uid)
            sends.append(self.send(self.clients.get(uid), {
                "type": "battle_start", "battle_id": battle.id, "with": opp_uid,
                "name": opp.name if opp else "?", "master": uid == battle.a, "rns": rns,
                "mons": battle.ready[opp_uid]["mons"],
                "trainer": battle.ready[opp_uid]["trainer"]}))
        await asyncio.gather(*sends)

    async def on_battle_move(self, client: Client, msg: dict) -> None:
        battle = self._battle_for(client, msg)
        if battle is None or battle.status != "running":
            return  # late / stray move: ignore silently
        n = _int(msg, "n", 1)
        v = _int(msg, "v", 0, 15)
        check = msg.get("check")
        if check is not None and (not isinstance(check, str) or len(check) > BATTLE_CHECK_MAX):
            raise Invalid(f"check must be a string of at most {BATTLE_CHECK_MAX} chars")
        await self.send(self.clients.get(battle.other(client.uid)),
                        {"type": "battle_move", "battle_id": battle.id, "n": n, "v": v,
                         "check": check})

    async def on_battle_end(self, client: Client, msg: dict) -> None:
        battle = self._battle_for(client, msg)
        if battle is None or battle.status != "running":
            return  # second / late battle_end: ignore silently
        result = _int(msg, "result", 0, 2)
        self._end_battle(battle)
        me = client.name
        other = self.clients.get(battle.other(client.uid))
        them = other.name if other else self.store.get(battle.other(client.uid))["name"]
        log.info("battle %s ended (reported by %s: %d)", battle.id, me, result)
        if result == 0:
            await self.system(f"{me} beat {them} in a link battle!")
        elif result == 1:
            await self.system(f"{them} beat {me} in a link battle!")
        else:
            await self.system(f"{me} and {them} drew!")

    async def on_battle_leave(self, client: Client, msg: dict) -> None:
        if self._battle_for(client, msg) is None:
            return  # nothing to leave: ignore silently
        await self._abort_battle_of(client, disconnected=False)

    # ------------------------------------------------------- PC database (SPEC section 5)
    def _pc_path(self, uid: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", uid)[:64] or "unknown"
        folder = os.path.join(self.config.data_dir, "pc")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{safe}.json")

    def _pc_load_status(self, uid: str):
        """(store, "ok" | "missing" | "corrupt") - a damaged file must never be replaced by an empty one."""
        path = self._pc_path(uid)
        if not os.path.exists(path):
            return None, "missing"
        try:
            with open(path, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            log.error("PC store %s is unreadable", path)
            return None, "corrupt"
        if not self._pc_valid(st):
            log.error("PC store %s is invalid", path)
            return None, "corrupt"
        return st, "ok"

    def _pc_load(self, uid: str):
        return self._pc_load_status(uid)[0]

    @staticmethod
    def _pc_valid(store) -> bool:
        if not isinstance(store, dict) or not isinstance(store.get("rev"), int) or store["rev"] < 0:
            return False
        boxes = store.get("boxes")
        if not isinstance(boxes, list) or len(boxes) != 14:
            return False
        for box in boxes:
            if not isinstance(box, list) or len(box) > 20:
                return False
            for e in box:
                if not isinstance(e, dict) or not isinstance(e.get("id"), str):
                    return False
        return isinstance(store.get("linked", []), list) and isinstance(store.get("incoming", []), list)

    def _pc_save(self, uid: str, store: dict) -> None:
        """New file first, then keep the old one as backup 1..5 (the live file is never missing)."""
        path = self._pc_path(uid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(store, fh)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(path):
            for i in range(4, 0, -1):
                older = f"{path}.{i}"
                if os.path.exists(older):
                    os.replace(older, f"{path}.{i + 1}")
            shutil.copy2(path, f"{path}.1")
        os.replace(tmp, path)

    async def on_pc_get(self, client: Client, msg: dict) -> None:
        store = self._pc_load(client.uid)
        await self.send(client, {"type": "pc_data", "store": store})
        inbox = (store or {}).get("inbox") or []
        if inbox:
            await self.send(client, {"type": "pc_trades", "deltas": inbox})

    async def on_pc_put(self, client: Client, msg: dict) -> None:
        """Compare-and-swap upload: accepted only if it's based on the server's current copy (`base` ==
        srv_rev) and knows every trade (SPEC.md section 5). Otherwise the client merges and tries again."""
        store = msg.get("store")
        if not self._pc_valid(store):
            raise Invalid("invalid PC store")
        base = msg.get("base")
        if isinstance(base, bool) or not isinstance(base, int):
            raise Invalid("base must be an integer")
        current, status = self._pc_load_status(client.uid)
        if status == "corrupt":
            path = self._pc_path(client.uid)
            os.replace(path, f"{path}.corrupt-{int(time.time())}")
            log.warning("replacing the unreadable PC store of %s with their upload (kept a copy)", client.name)
            current = None
        current = current or {}
        applied = set(store.get("trades") or [])
        unacked = [d for d in current.get("inbox") or [] if d.get("trade_id") not in applied]
        if unacked:
            await self.send(client, {"type": "pc_trades", "deltas": unacked})
            return
        gone = set(current.get("srv_gone") or [])
        holds_gone = any(e.get("id") in gone for b in store["boxes"] for e in b)
        if base != int(current.get("srv_rev", 0)) or holds_gone:
            await self.send(client, {"type": "pc_conflict", "store": current or None})
            return
        new = {k: v for k, v in store.items() if k not in pcstore.SERVER_KEYS}
        new.update(srv_rev=base + 1, inbox=[], srv_trades=current.get("srv_trades", []),
                   srv_gone=current.get("srv_gone", []))
        self._pc_save(client.uid, new)
        count = sum(len(b) for b in new["boxes"])
        log.info("PC store of %s saved: srv_rev %d, %d mons", client.name, new["srv_rev"], count)
        await self.send(client, {"type": "pc_saved", "srv_rev": new["srv_rev"]})

    def _challenge_kind(self, client) -> str | None:
        """Challenge type the player is playing right now (for chat/roster labels), or None."""
        cid = client.challenge_id if client else None
        s = self.challenges.sessions.get(cid) if cid else None
        if s is None or client.uid not in s.present:
            return None
        return self.challenges.records[cid]["kind"]

    async def on_library(self, client: Client, msg: dict) -> None:
        games = msg.get("games")
        if not isinstance(games, list) or len(games) > 8 or not all(
                isinstance(g, str) and re.fullmatch(r"[a-z0-9]{1,16}", g) for g in games):
            raise Invalid("games must be a list of game ids")
        client.games = sorted(set(games))
        if self.store.set_games(client.uid, client.games):
            await self.broadcast_roster()

    def _chal(name):  # noqa: N805 - builds a handler that maps ChallengeError -> Invalid
        async def handler(self, client, msg):
            before = (client.challenge_id, self._challenge_kind(client))
            try:
                await getattr(self.challenges, name)(client, msg)
            except ChallengeError as exc:
                raise Invalid(str(exc)) from None
            if (client.challenge_id, self._challenge_kind(client)) != before:
                await self.broadcast_roster()       # "in Soul Link" labels
        return handler

    _handlers = {
        "library": on_library,
        "challenge_request": _chal("on_request"),
        "challenge_respond": _chal("on_respond"),
        "challenge_join": _chal("on_join"),
        "challenge_leave": _chal("on_leave"),
        "challenge_event": _chal("on_event"),
        "challenge_progress": _chal("on_progress"),
        "challenge_abandon": _chal("on_abandon"),
        "pc_get": on_pc_get,
        "pc_put": on_pc_put,
        "hello": on_hello,
        "ping": on_ping,
        "bug_report": on_bug_report,
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
        "trade_status": on_trade_status,
        "battle_request": on_battle_request,
        "battle_respond": on_battle_respond,
        "battle_ready": on_battle_ready,
        "battle_move": on_battle_move,
        "battle_end": on_battle_end,
        "battle_leave": on_battle_leave,
    }
