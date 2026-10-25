"""Challenges (Soul Link): persistent records, requests, play sessions and the rule-event ledger.

See SPEC.md section 7. The server is the referee's notebook: it stores what happened (the ledger) and who is
playing right now (sessions). The clients derive and enforce the rules from the ledger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger("yellowlink.challenges")

KINDS = {"soul_link": "Soul Link"}
GAMES = {"yellow": "Pokémon Yellow", "crystal": "Pokémon Crystal"}
REQUEST_TIMEOUT = 60.0
LOAD_TIMEOUT = 90.0          # both players must load into the challenge within this
GRACE = 30.0                 # a dropped connection may come back within this
LEDGER_MAX = 5000
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
AREA_RE = re.compile(r"^[A-Za-z0-9_*-]{1,40}$")   # "*" marks a shiny-clause bonus catch ("ROUTE_1*1")
CID_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
EVENT_TYPES = {"enc", "dead", "loc", "swap", "hof", "blackout", "balls"}
ENC_STATUS = {"open", "caught", "failed", "skipped"}


class ChallengeError(Exception):
    """Invalid challenge message; the text goes back to the client as an error."""


def _str(v, what, maxlen=40, pattern=ID_RE):
    if not isinstance(v, str) or len(v) > maxlen or (pattern and not pattern.match(v)):
        raise ChallengeError(f"{what} is invalid")
    return v


def validate_event(ev) -> dict:
    """Whitelist the fields of a ledger event."""
    if not isinstance(ev, dict):
        raise ChallengeError("event must be an object")
    t = ev.get("t")
    if t not in EVENT_TYPES:
        raise ChallengeError("unknown event type")
    out = {"t": t}
    if ev.get("cid") is not None:
        out["cid"] = _str(ev.get("cid"), "cid", 16, CID_RE)
    if t == "enc":
        out["area"] = _str(ev.get("area"), "area", pattern=AREA_RE)
        dex = ev.get("dex", 0)
        if isinstance(dex, bool) or not isinstance(dex, int) or not 0 <= dex <= 255:
            raise ChallengeError("dex is invalid")
        out["dex"] = dex
        if ev.get("status") not in ENC_STATUS:
            raise ChallengeError("status is invalid")
        out["status"] = ev["status"]
        if ev.get("key") is not None:
            out["key"] = _str(ev.get("key"), "key")
        for flag in ("gift", "shiny", "auto"):
            if ev.get(flag):
                out[flag] = True
        if ev.get("why") is not None:
            out["why"] = _str(ev.get("why"), "why", 30, None)
    elif t == "dead":
        out["key"] = _str(ev.get("key"), "key")
        out["why"] = _str(ev.get("why", "fainted"), "why", 30, None)
    elif t == "loc":
        out["key"] = _str(ev.get("key"), "key")
        if ev.get("where") not in ("party", "box"):
            raise ChallengeError("where is invalid")
        out["where"] = ev["where"]
    elif t == "blackout":
        if ev.get("why") is not None:
            out["why"] = _str(ev.get("why"), "why", 30, None)
    elif t == "swap":
        out["old"] = _str(ev.get("old"), "old")
        out["new"] = _str(ev.get("new"), "new")
    return out


@dataclass(eq=False)
class Request:
    id: str
    frm: str
    to: str
    kind: str
    game: str
    rules: dict
    challenge_id: str | None
    timer: asyncio.Task | None = None


@dataclass(eq=False)
class Session:
    challenge_id: str
    present: set = field(default_factory=set)
    lost: dict = field(default_factory=dict)        # uid -> grace task
    load_timer: asyncio.Task | None = None
    ready: bool = False


class ChallengeHub:
    def __init__(self, server, data_dir: str, debounce: float = 1.0):
        self.server = server
        self.path = os.path.join(data_dir, "challenges.json")
        self.data_dir = data_dir
        self.debounce = debounce
        self.records: dict[str, dict] = {}
        self.requests: dict[str, Request] = {}
        self.sessions: dict[str, Session] = {}
        self._save_task = None
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            for rec in raw.get("challenges", []):
                if self._valid_record(rec):
                    self.records[rec["id"]] = rec
                else:
                    log.warning("skipping a damaged challenge record: %.200r", rec)
            log.info("loaded %d challenges", len(self.records))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.error("could not read %s (%s); keeping a copy and starting empty", self.path, exc)
            try:
                os.replace(self.path, self.path + f".bad-{int(time.time())}")
            except OSError:
                pass

    @staticmethod
    def _valid_record(rec) -> bool:
        try:
            return (isinstance(rec, dict) and isinstance(rec["id"], str) and rec["kind"] in KINDS
                    and rec["game"] in GAMES and rec["status"] in ("active", "completed", "failed", "abandoned")
                    and isinstance(rec["players"], list) and len(rec["players"]) == 2
                    and all(isinstance(p, str) for p in rec["players"]) and isinstance(rec["names"], dict)
                    and isinstance(rec["rules"], dict) and isinstance(rec["progress"], dict)
                    and isinstance(rec["ledger"], list))
        except (KeyError, TypeError):
            return False

    def save_now(self):
        os.makedirs(self.data_dir, exist_ok=True)
        payload = json.dumps({"version": 1, "challenges": list(self.records.values())}, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(prefix=".challenges-", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def schedule_save(self):
        if self._save_task is not None and not self._save_task.done():
            return
        self._save_task = asyncio.get_running_loop().create_task(self._delayed_save())

    async def _delayed_save(self):
        try:
            await asyncio.sleep(self.debounce)
        except asyncio.CancelledError:
            return
        try:
            self.save_now()
        except Exception:
            log.exception("failed to save challenges")

    async def flush(self):
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        for s in self.sessions.values():
            self._cancel_timers(s)
        for r in self.requests.values():
            if r.timer:
                r.timer.cancel()
        try:
            self.save_now()
        except Exception:
            log.exception("failed to save challenges")

    # ------------------------------------------------------------------ helpers
    def summary(self, rec: dict) -> dict:
        out = {k: v for k, v in rec.items() if k != "ledger"}
        s = self.sessions.get(rec["id"])
        out["online"] = sorted(s.present) if s else []
        stats = {uid: {"caught": 0, "dead": 0} for uid in rec["players"]}
        for ev in rec.get("ledger", []):
            st = stats.get(ev.get("p"))
            if st is None:
                continue
            if ev["t"] == "enc" and ev.get("status") == "caught":
                st["caught"] += 1
            elif ev["t"] == "dead":
                st["dead"] += 1
        out["stats"] = stats
        out["events"] = len(rec.get("ledger", []))
        return out

    def mine(self, uid: str) -> list:
        recs = [r for r in self.records.values() if uid in r["players"]]
        recs.sort(key=lambda r: r.get("updated", 0), reverse=True)
        return [self.summary(r) for r in recs[:50]]

    def open_between(self, a: str, b: str, kind: str) -> dict | None:
        return next((r for r in self.records.values() if r["kind"] == kind and r["status"] == "active"
                     and set(r["players"]) == {a, b}), None)

    def other(self, rec: dict, uid: str) -> str:
        return next(p for p in rec["players"] if p != uid)

    def client(self, uid):
        return self.server.clients.get(uid)

    async def send(self, uid, msg):
        await self.server.send(self.client(uid), msg)

    async def update_both(self, rec):
        rec["updated"] = time.time()
        self.schedule_save()
        msg = {"type": "challenge_update", "challenge": self.summary(rec)}
        for uid in rec["players"]:
            await self.send(uid, msg)

    def _get(self, msg, uid) -> dict:
        cid = msg.get("challenge_id")
        rec = self.records.get(cid) if isinstance(cid, str) else None
        if rec is None or uid not in rec["players"]:
            raise ChallengeError("unknown challenge")
        return rec

    # ------------------------------------------------------------------ requests
    async def on_request(self, client, msg):
        to = msg.get("to")
        target = self.client(to) if isinstance(to, str) else None
        if target is None:
            raise ChallengeError("that player is not online")
        if target is client:
            raise ChallengeError("you can't challenge yourself")
        if client.busy:
            raise ChallengeError("finish what you're doing first")
        if target.busy:
            raise ChallengeError(f"{target.name} is busy right now")
        if any({r.frm, r.to} == {client.uid, target.uid} for r in self.requests.values()):
            raise ChallengeError("there's already a challenge request between you two")
        cid = msg.get("challenge_id")
        if cid is not None:
            rec = self._get(msg, client.uid)
            if rec["status"] != "active":
                raise ChallengeError("that challenge is already over")
            if target.uid not in rec["players"]:
                raise ChallengeError("that challenge is with someone else")
            kind, game, rules = rec["kind"], rec["game"], rec["rules"]
            for c in (client, target):
                if c.games is not None and game not in c.games:
                    who = "You don't" if c is client else f"{c.name} doesn't"
                    raise ChallengeError(f"{who} have {GAMES[game]} (v1.0 for Crystal) added anymore")
        else:
            kind, game = msg.get("kind"), msg.get("game")
            if kind not in KINDS:
                raise ChallengeError("unknown challenge type")
            if game not in GAMES:
                raise ChallengeError("challenges work in Pokémon Yellow and Pokémon Crystal")
            rules_in = msg.get("rules") if isinstance(msg.get("rules"), dict) else {}
            rules = {"dupes": bool(rules_in.get("dupes", True)), "shiny": bool(rules_in.get("shiny", True))}
            existing = self.open_between(client.uid, target.uid, kind)
            if existing:
                raise ChallengeError(f"you already have a {KINDS[kind]} with {target.name} - continue it, "
                                     f"or abandon it first")
            for c in (client, target):
                if c.games is not None and game not in c.games:
                    who = "You don't" if c is client else f"{c.name} doesn't"
                    raise ChallengeError(f"{who} have {GAMES[game]} added")
        req = Request(uuid.uuid4().hex[:12], client.uid, target.uid, kind, game, rules, cid)
        self.requests[req.id] = req
        req.timer = asyncio.get_running_loop().create_task(self._expire(req))
        log.info("challenge request %s: %s -> %s (%s, %s, continue=%s)", req.id, client.name, target.name,
                 kind, game, cid)
        await self.server.send(target, {"type": "challenge_request", "request_id": req.id, "from": client.uid,
                                        "name": client.name, "kind": kind, "game": game, "rules": rules,
                                        "challenge_id": cid})

    async def _expire(self, req):
        await asyncio.sleep(REQUEST_TIMEOUT)
        if self.requests.pop(req.id, None) is req:
            await self.send(req.frm, {"type": "challenge_declined", "request_id": req.id,
                                      "name": self._name(req.to), "reason": "no answer"})

    def _name(self, uid):
        c = self.client(uid)
        if c:
            return c.name
        p = self.server.store.get(uid)
        return p.get("name", "?") if p else "?"

    async def on_respond(self, client, msg):
        rid = msg.get("request_id")
        req = self.requests.get(rid) if isinstance(rid, str) else None
        if req is None or req.to != client.uid:
            return                                   # expired or not ours: ignore quietly
        del self.requests[req.id]
        if req.timer:
            req.timer.cancel()
        requester = self.client(req.frm)
        if not msg.get("accept"):
            await self.server.send(requester, {"type": "challenge_declined", "request_id": req.id,
                                               "name": client.name, "reason": "declined"})
            return
        if requester is None:
            raise ChallengeError("they went offline")
        if requester.busy or client.busy:
            await self.server.send(requester, {"type": "challenge_declined", "request_id": req.id,
                                               "name": client.name, "reason": "busy"})
            raise ChallengeError("one of you is busy right now")
        if req.challenge_id:
            rec = self.records.get(req.challenge_id)
            if rec is None or rec["status"] != "active":
                raise ChallengeError("that challenge is already over")
        else:
            if self.open_between(req.frm, req.to, req.kind):
                raise ChallengeError("you already have this challenge together")
            now = time.time()
            rec = {"id": uuid.uuid4().hex[:12], "kind": req.kind, "game": req.game,
                   "players": [req.frm, req.to], "names": {req.frm: requester.name, req.to: client.name},
                   "rules": req.rules, "status": "active", "created": now, "updated": now, "ended": None,
                   "reason": "", "progress": {req.frm: {"badges": 0, "hof": False},
                                              req.to: {"badges": 0, "hof": False}},
                   "ledger": []}
            self.records[rec["id"]] = rec
            self.schedule_save()
            log.info("challenge %s created: %s + %s (%s, %s)", rec["id"], requester.name, client.name,
                     rec["kind"], rec["game"])
        rec["names"][client.uid] = client.name
        rec["names"][requester.uid] = requester.name
        await self._start_session(rec)

    # ------------------------------------------------------------------ sessions
    async def _start_session(self, rec):
        old = self.sessions.pop(rec["id"], None)
        if old:
            self._cancel_timers(old)
        s = Session(rec["id"])
        self.sessions[rec["id"]] = s
        s.load_timer = asyncio.get_running_loop().create_task(self._load_deadline(s))
        for uid in rec["players"]:
            c = self.client(uid)
            if c:
                c.challenge_id = rec["id"]
        rec["updated"] = time.time()
        self.schedule_save()
        msg = {"type": "challenge_go", "challenge": self.summary(rec)}
        for uid in rec["players"]:
            await self.send(uid, msg)

    def _cancel_timers(self, s):
        if s.load_timer:
            s.load_timer.cancel()
        for t in s.lost.values():
            t.cancel()
        s.lost.clear()

    async def _load_deadline(self, s):
        await asyncio.sleep(LOAD_TIMEOUT)
        if self.sessions.get(s.challenge_id) is not s or s.ready:
            return
        rec = self.records[s.challenge_id]
        missing = [u for u in rec["players"] if u not in s.present]
        await self.end_session(rec, missing[0] if missing else None, "didn't load in")

    async def end_session(self, rec, leaver, reason, notify=True):
        s = self.sessions.pop(rec["id"], None)
        if s is None:
            return
        self._cancel_timers(s)
        for uid in rec["players"]:
            c = self.client(uid)
            if c and c.challenge_id == rec["id"]:
                c.challenge_id = None
            if notify and uid != leaver:
                await self.send(uid, {"type": "challenge_partner_left", "challenge_id": rec["id"],
                                      "name": self._name(leaver) if leaver else "?", "reason": reason})
        log.info("challenge %s session ended (%s by %s)", rec["id"], reason, self._name(leaver) if leaver else "-")
        rec["updated"] = time.time()
        self.schedule_save()
        for uid in rec["players"]:
            await self.send(uid, {"type": "challenge_update", "challenge": self.summary(rec)})
        await self.server.broadcast_roster()          # "in Soul Link" labels change

    async def on_join(self, client, msg):
        rec = self._get(msg, client.uid)
        s = self.sessions.get(rec["id"])
        if rec["status"] != "active" or s is None:
            # Session is gone (partner left while we were reconnecting, server restarted...): go home.
            await self.server.send(client, {"type": "challenge_partner_left", "challenge_id": rec["id"],
                                            "name": self._name(self.other(rec, client.uid)),
                                            "reason": "not playing anymore" if rec["status"] == "active"
                                            else rec["status"]})
            return
        t = s.lost.pop(client.uid, None)
        if t:
            t.cancel()
        s.present.add(client.uid)
        client.challenge_id = rec["id"]
        await self.server.send(client, {"type": "challenge_state", "challenge": {**self.summary(rec),
                                                                                  "ledger": rec["ledger"]}})
        if s.present >= set(rec["players"]):
            s.ready = True
            if s.load_timer:
                s.load_timer.cancel()
                s.load_timer = None
            for uid in rec["players"]:
                await self.send(uid, {"type": "challenge_ready", "challenge_id": rec["id"]})

    async def on_leave(self, client, msg):
        rec = self._get(msg, client.uid)
        reason = msg.get("reason") if isinstance(msg.get("reason"), str) else "left"
        await self.end_session(rec, client.uid, reason[:40])

    async def client_gone(self, client):
        """Connection dropped (or replaced): give the player GRACE seconds to come back."""
        cid = client.challenge_id
        s = self.sessions.get(cid) if cid else None
        if s is None:
            return
        rec = self.records[cid]
        s.present.discard(client.uid)
        if not s.ready:
            return            # still loading in (the app restarts to enter a run): the load deadline covers it
        if client.uid in s.lost:
            return
        s.lost[client.uid] = asyncio.get_running_loop().create_task(self._grace(rec, s, client.uid))
        await self.send(self.other(rec, client.uid), {"type": "challenge_partner_lost", "challenge_id": cid,
                                                       "name": client.name})

    async def _grace(self, rec, s, uid):
        await asyncio.sleep(GRACE)
        if self.sessions.get(rec["id"]) is s and uid not in s.present:
            s.lost.pop(uid, None)
            await self.end_session(rec, uid, "disconnected")

    # ------------------------------------------------------------------ ledger & progress
    async def on_event(self, client, msg):
        rec = self._get(msg, client.uid)
        if rec["status"] != "active":
            return                                   # late event after the end: ignore quietly
        ev = validate_event(msg.get("event"))
        if ev.get("cid"):
            dup = next((e for e in reversed(rec["ledger"]) if e.get("cid") == ev["cid"] and e["p"] == client.uid), None)
            if dup is not None:                       # a resend after a connection blip: echo the original again
                await self.server.send(client, {"type": "challenge_event", "challenge_id": rec["id"], "event": dup})
                return
        if len(rec["ledger"]) >= LEDGER_MAX:
            raise ChallengeError("challenge log is full")
        ev = {"seq": len(rec["ledger"]) + 1, "p": client.uid, "ts": round(time.time(), 1), **ev}
        rec["ledger"].append(ev)
        rec["updated"] = time.time()
        self.schedule_save()
        out = {"type": "challenge_event", "challenge_id": rec["id"], "event": ev}
        for uid in rec["players"]:
            await self.send(uid, out)
        if ev["t"] == "hof":
            rec["progress"].setdefault(client.uid, {"badges": 0})["hof"] = True
            if all(rec["progress"].get(u, {}).get("hof") for u in rec["players"]):
                rec["status"], rec["ended"], rec["reason"] = "completed", time.time(), "both entered the Hall of Fame"
                log.info("challenge %s completed", rec["id"])
                await self.server.system(f"{' and '.join(rec['names'].get(u, '?') for u in rec['players'])} "
                                         f"completed a {KINDS[rec['kind']]}!")
            await self.update_both(rec)
            if rec["status"] == "completed":
                await self.end_session(rec, None, "completed", notify=False)
        elif ev["t"] == "blackout":
            rec["status"], rec["ended"] = "failed", time.time()
            rec["reason"] = (f"{client.name} has no usable Pokémon left" if ev.get("why") == "no Pokémon left"
                             else f"{client.name} blacked out")
            log.info("challenge %s failed: %s", rec["id"], rec["reason"])
            await self.update_both(rec)
            await self.end_session(rec, None, "failed", notify=False)

    async def on_progress(self, client, msg):
        rec = self._get(msg, client.uid)
        badges = msg.get("badges")
        if isinstance(badges, bool) or not isinstance(badges, int) or not 0 <= badges <= 0xFFFF:
            raise ChallengeError("badges is invalid")
        prog = rec["progress"].setdefault(client.uid, {"badges": 0, "hof": False})
        if prog.get("badges") != badges:
            prog["badges"] = badges
            await self.update_both(rec)

    async def on_abandon(self, client, msg):
        rec = self._get(msg, client.uid)
        if rec["status"] != "active":
            return
        rec["status"], rec["ended"], rec["reason"] = "abandoned", time.time(), f"abandoned by {client.name}"
        await self.update_both(rec)
        if rec["id"] in self.sessions:
            await self.end_session(rec, client.uid, "abandoned")
