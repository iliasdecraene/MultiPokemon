"""Raids (SPEC.md section 12): a co-op boss every 2 hours in a random region, one group HP pool in milli-bars.

The server only decides WHAT and WHERE (region, species, shiny, form, spot) and keeps the pool, everyone's damage and
the catch rolls; every client fights its own copy of the boss (the level is the client's, `raid_data.boss_for`).
Persisted in <data_dir>/raids.json: {"version": 1, "salt", "current", "last_slot", "last", "history"}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import secrets
import tempfile
import time

log = logging.getLogger("yellowlink.raids")

BAR = 1000                       # milli-bars per bar (one full HP bar of the boss in the attacker's own game)
BARS_PER_PLAYER = 2              # Ilias's decision 5
BURST = 1000                     # accepted damage right after joining ...
RATE = 500                       # ... plus this per second (1 bar per 2 s)
DOWN_GRACE = 180.0               # seconds the catch phase waits for everyone's result
HISTORY_MAX = 100
DEALT_MAX = 10_000_000
SHINY_ODDS = 20                  # 1 in 20 (decision 8)
TICK = 5.0                       # scheduler interval (seconds)
MIN_GAP_MAX = 3600.0             # a raid opens at least min(this, every / 2) after the previous one ended
RETRY_MARGIN = 30.0              # the slot's raid must open at least this long before the slot's end - open time

REGIONS = ("kanto", "johto", "sinnoh")
REGION_NAMES = {"kanto": "Kanto", "johto": "Johto", "sinnoh": "Sinnoh"}
GAME_OF_REGION = {"kanto": "yellow", "johto": "crystal", "sinnoh": "platinum"}

# DESIGN.md section 2 + Ilias's decision 10 (second chances for one-per-save legendaries; Darkrai/Shaymin/Arceus +
# the Regis in Sinnoh). National dex numbers. Mirrored by the client's raid_data.POOLS (client unit test checks it).
POOLS = {
    "kanto": (151, 144, 145, 146, 150),
    "johto": (251, 151, 150, 144, 145, 146, 249, 250, 245, 243, 244),
    "sinnoh": (150, 249, 250, 243, 244, 245, 380, 381, 382, 383, 384, 151, 251, 385, 386, 490, 489, 491, 492, 493,
               377, 378, 379, 486),
}
SPECIES_NAMES = {
    144: "Articuno", 145: "Zapdos", 146: "Moltres", 150: "Mewtwo", 151: "Mew", 243: "Raikou", 244: "Entei",
    245: "Suicune", 249: "Lugia", 250: "Ho-Oh", 251: "Celebi", 377: "Regirock", 378: "Regice", 379: "Registeel",
    380: "Latias", 381: "Latios", 382: "Kyogre", 383: "Groudon", 384: "Rayquaza", 385: "Jirachi", 386: "Deoxys",
    486: "Regigigas", 489: "Phione", 490: "Manaphy", 491: "Darkrai", 492: "Shaymin", 493: "Arceus",
}
DEOXYS = 386
OUTCOMES = ("caught", "missed", "blackout", "left", "fled", "unstarted")
SPOTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raid_spots.json")
RAID_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class RaidInvalid(Exception):
    """A malformed raid message (sent back as a red `error`)."""


# ----------------------------------------------------------------------------------------------- pure helpers
def place_phrase(name: str) -> str:
    """'on Route 210' / 'in Cerulean City' (the shiny alerts' rule)."""
    return f"on {name}" if name.startswith("Route") else f"in {name}"


def allowed_total(seconds_since_join: float) -> int:
    """Damage a participant can have dealt in total this long after joining (light anti-cheat)."""
    return int(BURST + RATE * max(0.0, seconds_since_join))


def shares(participants: dict) -> dict:
    """uid -> share of everyone's damage (the catch chance, decision 1)."""
    total = sum(max(0, int(p.get("dealt", 0))) for p in participants.values())
    return {uid: (max(0, int(p.get("dealt", 0))) / total if total else 0.0) for uid, p in participants.items()}


def names_text(names: list) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def parse_force(text: str):
    """YL_RAID_FORCE: 'region', 'region:species' or 'region:species:shiny' -> (region, species|None, shiny|None)."""
    parts = [p.strip().lower() for p in str(text or "").split(":") if p.strip()]
    if not parts or parts[0] not in POOLS:
        return None
    species = None
    if len(parts) > 1:
        try:
            species = int(parts[1])
        except ValueError:
            return None
        if species not in SPECIES_NAMES:
            return None
    shiny = True if len(parts) > 2 and parts[2] == "shiny" else None
    return parts[0], species, shiny


def load_spots(path: str = SPOTS_FILE) -> dict:
    """The vendored spot sample: {region: {"names": {map: name}, "spots": [[map, x, y, reach], ...]}}."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        log.warning("no raid spot data (%s): no raids until it exists", path)
        return {}
    except (OSError, ValueError) as exc:
        log.error("raid spot data unreadable (%s): %s", path, exc)
        return {}
    out = {}
    for region, r in (raw.get("regions") or {}).items():
        if region in POOLS and isinstance(r, dict):
            spots = [s for s in r.get("spots", []) if isinstance(s, list) and len(s) == 4]
            if spots:
                out[region] = {"names": dict(r.get("names") or {}), "spots": spots}
    return out


def pick(seed: str, online: list, spots: dict, last: dict, force=None) -> dict | None:
    """Region, boss and spot for a raid. `online` = [{"games": [...], "reach": {region: badges}}] (raid-capable
    clients). Deterministic for a seed. None = no region possible."""
    rng = random.Random(seed)
    force = force or (None, None, None)
    weights = {}
    for region in REGIONS:
        if region not in spots or not spots[region]["spots"]:
            continue
        n = sum(1 for o in online if GAME_OF_REGION[region] in (o.get("games") or ()))
        if n:
            weights[region] = n
    if force[0]:
        weights = {force[0]: 1} if force[0] in spots else {}
    if not weights:
        return None
    regions = sorted(weights)
    region = rng.choices(regions, [weights[r] for r in regions])[0]
    prev = (last.get(region) or {})
    pool = [s for s in POOLS[region] if s != prev.get("species")] or list(POOLS[region])
    species = force[1] or rng.choice(pool)
    shiny = rng.randrange(SHINY_ODDS) == 0
    if force[2] is not None:
        shiny = force[2]
    form = rng.randrange(4) if species == DEOXYS else 0
    # spot: reachable for everyone online who plays that region (the lowest badge count among them)
    reaches = [int((o.get("reach") or {}).get(region, 0) or 0) for o in online
               if GAME_OF_REGION[region] in (o.get("games") or ())]
    limit = min(reaches) if reaches else 0
    data = spots[region]
    cands = [s for s in data["spots"] if s[3] <= limit]
    if not cands:
        low = min(s[3] for s in data["spots"])
        cands = [s for s in data["spots"] if s[3] == low]
    maps = sorted({s[0] for s in cands})                   # each map equally likely, then a spot on it
    m = rng.choice([v for v in maps if v != prev.get("map")] or maps)
    x, y = rng.choice([(s[1], s[2]) for s in cands if s[0] == m])
    name = data["names"].get(str(m)) or f"map {m}"
    return {"region": region, "species": species, "shiny": shiny, "form": form,
            "spot": {"map": m, "x": x, "y": y, "name": name}}


def new_participant(name: str, level: int, now: float) -> dict:
    return {"name": name, "level": level, "joined": now, "claimed": 0, "dealt": 0, "hits": 0, "state": "fighting"}


# ----------------------------------------------------------------------------------------------- the hub
class RaidHub:
    def __init__(self, server, data_dir: str, config, debounce: float = 1.0):
        self.server = server
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "raids.json")
        self.enabled = bool(getattr(config, "raids", True))
        self.auto = self.enabled
        self.every = float(getattr(config, "raid_every", 120.0)) * 60
        self.open_secs = float(getattr(config, "raid_open", 15.0)) * 60
        self.force = parse_force(getattr(config, "raid_force", ""))
        self.first = float(getattr(config, "raid_first", -1.0))
        self.catch_force = {"always": True, "never": False}.get(str(getattr(config, "raid_catch", "")).lower())
        self.debounce = debounce
        self.clock = time.time
        self.rng = random.SystemRandom()
        self.update_every = 0.5
        self.spots = load_spots(getattr(config, "raid_spots", "") or SPOTS_FILE) if self.enabled else {}
        self.salt = secrets.token_hex(8)
        self.current: dict | None = None
        self.last_slot = -1
        self.last: dict = {}
        self.history: list = []
        self._first_at = None
        self._quiet_slot = None
        self._save_task = None
        self._loop_task = None
        self._update_task = None
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.error("could not read %s (%s); keeping a copy and starting empty", self.path, exc)
            try:
                os.replace(self.path, self.path + f".bad-{int(time.time())}")
            except OSError:
                pass
            return
        if isinstance(raw.get("salt"), str) and raw["salt"]:
            self.salt = raw["salt"]
        if isinstance(raw.get("last_slot"), int):
            self.last_slot = raw["last_slot"]
        if isinstance(raw.get("last"), dict):
            self.last = raw["last"]
        if isinstance(raw.get("history"), list):
            self.history = raw["history"][-HISTORY_MAX:]
        cur = raw.get("current")
        if isinstance(cur, dict) and self._valid(cur):
            self.current = cur
            log.info("raid %s (%s) continues after the restart", cur["id"], cur["name"])

    @staticmethod
    def _valid(r) -> bool:
        try:
            return (isinstance(r["id"], str) and r["region"] in POOLS and isinstance(r["species"], int)
                    and r["phase"] in ("open", "down") and isinstance(r["participants"], dict)
                    and isinstance(r["pool"], int) and isinstance(r["closes"], (int, float))
                    and isinstance(r["spot"], dict))
        except (KeyError, TypeError):
            return False

    def save_now(self):
        os.makedirs(self.data_dir, exist_ok=True)
        payload = json.dumps({"version": 1, "salt": self.salt, "current": self.current, "last_slot": self.last_slot,
                              "last": self.last, "history": self.history[-HISTORY_MAX:]}, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(prefix=".raids-", suffix=".tmp", dir=self.data_dir)
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
        try:
            self._save_task = asyncio.get_running_loop().create_task(self._delayed_save())
        except RuntimeError:
            self._try_save()

    async def _delayed_save(self):
        try:
            await asyncio.sleep(self.debounce)
        except asyncio.CancelledError:
            return
        self._try_save()

    def _try_save(self):
        try:
            self.save_now()
        except Exception:  # noqa: BLE001
            log.exception("failed to save raids")

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        if self.first >= 0:
            self._first_at = self.clock() + self.first
        if self.enabled:
            self._loop_task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self):
        for t in (self._loop_task, self._update_task, self._save_task):
            if t is not None and not t.done():
                t.cancel()
        if self.enabled or os.path.exists(self.path):
            self._try_save()

    async def _loop(self):
        while True:
            try:
                await asyncio.sleep(TICK)
                if self.auto:
                    await self.tick()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - the scheduler must keep running
                log.exception("raid scheduler failed")

    # ------------------------------------------------------------------ scheduler
    def slot_start_time(self, slot: int) -> float:
        """When slot n's raid opens: a random moment inside the slot (seeded), so that it closes in the slot."""
        span = max(0.0, self.every - self.open_secs - RETRY_MARGIN)
        return slot * self.every + random.Random(f"{self.salt}:{slot}:at").uniform(0, span)

    def capable_online(self) -> list:
        out = []
        for c in self.server.clients.values():
            reach = getattr(c, "raid_reach", None)
            if reach is not None and not c.challenge_id:     # inside a Soul Link run nobody can raid
                out.append({"games": list(c.games or []), "reach": reach})
        return out

    async def tick(self):
        now = self.clock()
        r = self.current
        if r is not None:
            if r["phase"] == "open" and now >= r["closes"]:
                await self.end(r, "time")
            elif r["phase"] == "down" and (now >= r.get("down_at", now) + DOWN_GRACE or self._all_reported(r)):
                await self.end(r, "down")
            return
        if not self.enabled:
            return
        if self._first_at is not None:                        # testing: YL_RAID_FIRST
            if now >= self._first_at and self.capable_online():
                self._first_at = None
                await self.spawn(slot=int(now // self.every))
            return
        slot = int(now // self.every)
        if slot <= self.last_slot:
            return
        if now < self.slot_start_time(slot):
            return
        if now > (slot + 1) * self.every - self.open_secs:
            log.info("raid slot %d skipped (no raid could open in time)", slot)
            self.last_slot = slot
            self.schedule_save()
            return
        last_end = self.history[-1].get("ended") if self.history else None
        if isinstance(last_end, (int, float)) and now < last_end + min(MIN_GAP_MAX, self.every / 2):
            return                                            # the previous raid (a late one) ended too recently
        if not self.capable_online():
            return                                            # try again at the next tick
        await self.spawn(slot=slot, scheduled=True)

    async def spawn(self, region=None, species=None, shiny=None, slot=None, scheduled=False):
        """Open a raid now (scheduler, YL_RAID_FIRST, tests). Returns the live record or None. `scheduled`: the
        slot's raid (last_slot is set and saved together with it)."""
        now = self.clock()
        slot = int(now // self.every) if slot is None else slot
        force = self.force or (None, None, None)
        if region is not None:
            force = (region, species, shiny)
        online = self.capable_online()
        if force[0] and not online:
            online = [{"games": [GAME_OF_REGION[force[0]]], "reach": {}}]
        n = len(self.history) + 1
        choice = pick(f"{self.salt}:{slot}:{n}", online, self.spots, self.last, force)
        if choice is None:
            if self._quiet_slot != slot:                  # the scheduler retries every few seconds: say it once
                self._quiet_slot = slot
                log.info("no raid possible right now (regions online: none with spot data)")
            return None
        rid = f"{slot}-{secrets.token_hex(3)}"
        name = SPECIES_NAMES.get(choice["species"], f"#{choice['species']}")
        self.current = r = {
            "id": rid, "region": choice["region"], "game": GAME_OF_REGION[choice["region"]],
            "species": choice["species"], "name": name, "form": choice["form"], "shiny": choice["shiny"],
            "spot": choice["spot"], "opens": now, "closes": now + self.open_secs, "pool": 0, "pool_max": 0,
            "bars_per_player": BARS_PER_PLAYER, "phase": "open", "participants": {}}
        self.last[r["region"]] = {"species": r["species"], "map": r["spot"]["map"]}
        if scheduled:
            self.last_slot = max(self.last_slot, slot)
        log.info("raid %s: %s%s in %s at %s", rid, "shiny " if r["shiny"] else "", name, r["region"], r["spot"])
        self._try_save()
        minutes = max(1, int(round(self.open_secs / 60)))
        who = f"A shiny {name}" if r["shiny"] else name
        await self.server.broadcast({"type": "raid_open", "raid": self.summary(r), "fresh": True})
        await self.server.system(("✨ " if r["shiny"] else "") + f"RAID! {who} appeared {place_phrase(r['spot']['name'])} "
                                 f"({REGION_NAMES[r['region']]}) - {minutes} minutes!")
        return r

    def _all_reported(self, r) -> bool:
        return not any(p["state"] == "fighting" for p in r["participants"].values())

    async def end(self, r, reason: str):
        if self.current is not r:
            return
        r["phase"] = "over"
        r["ended"] = self.clock()
        r["reason"] = reason
        for p in r["participants"].values():
            if p["state"] == "fighting" and reason == "time":
                p["state"] = "fled"
        caught = [p["name"] for p in r["participants"].values() if p["state"] == "caught"]
        self.current = None
        self.history.append(r)
        del self.history[:-HISTORY_MAX]
        self._try_save()
        self._set_busy(r)
        log.info("raid %s over (%s): caught by %s", r["id"], reason, caught or "nobody")
        await self.server.broadcast({"type": "raid_end", "id": r["id"], "reason": reason, "caught": caught,
                                     "raid": self.summary(r)})
        if reason == "time":
            await self.server.system(f"{r['name']} fled - the raid is over.")
        elif caught:
            await self.server.system(f"Raid over: {names_text(caught)} caught {r['name']}!")
        else:
            await self.server.system(f"Raid over: {r['name']} got away.")

    # ------------------------------------------------------------------ views
    def summary(self, r) -> dict:
        parts = sorted(r["participants"].items(), key=lambda kv: (-kv[1]["dealt"], kv[1]["joined"]))
        return {k: r[k] for k in ("id", "region", "game", "species", "name", "form", "shiny", "spot", "opens", "closes",
                                  "pool", "pool_max", "bars_per_player", "phase")} | {
            "now": self.clock(),
            "participants": [{"uid": uid, "name": p["name"], "dealt": p["dealt"], "state": p["state"]}
                             for uid, p in parts]}

    def busy(self, uid: str) -> bool:
        r = self.current
        return bool(r and (r["participants"].get(uid) or {}).get("state") == "fighting")

    def _set_busy(self, r=None):
        for uid, c in self.server.clients.items():
            c.raiding = self.busy(uid)

    def _changed(self):
        """Coalesced raid_update (<= 2 per second) + a debounced save."""
        self.schedule_save()
        if self._update_task is not None and not self._update_task.done():
            return
        self._update_task = asyncio.get_running_loop().create_task(self._send_update())

    async def _send_update(self):
        try:
            await asyncio.sleep(self.update_every)
        except asyncio.CancelledError:
            return
        r = self.current
        if r is not None:
            await self.server.broadcast({"type": "raid_update", "raid": self.summary(r)})

    # ------------------------------------------------------------------ connections
    async def on_hello(self, client):
        client.raiding = self.busy(client.uid)
        r = self.current
        if r is None or not self.enabled:
            return
        await self.server.send(client, {"type": "raid_open", "raid": self.summary(r), "fresh": False})
        p = r["participants"].get(client.uid)
        if r["phase"] == "down" and p and p["state"] == "fighting" and "catch" in p:
            await self.server.send(client, {"type": "raid_down", "id": r["id"], "chance": p["chance"],
                                            "catch": p["catch"]})

    # ------------------------------------------------------------------ messages
    def _raid_of(self, msg):
        rid = msg.get("id")
        if not isinstance(rid, str) or not RAID_ID_RE.fullmatch(rid):
            raise RaidInvalid("id must be a raid id")
        r = self.current
        return r if r is not None and r["id"] == rid else None

    async def on_join(self, client, msg):
        r = self._raid_of(msg)
        level = msg.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 100:
            raise RaidInvalid("level must be 1-100")

        async def refuse(reason):
            await self.server.send(client, {"type": "raid_refused", "id": msg["id"], "reason": reason})

        now = self.clock()
        if r is None:
            return await refuse("This raid is over.")
        p = r["participants"].get(client.uid)
        if p is not None and p["state"] == "fighting":
            client.raiding = True
            await self.server.send(client, {"type": "raid_joined", "id": r["id"], "raid": self.summary(r)})
            return
        if r["phase"] != "open":
            return await refuse("The boss is already down.")
        if now >= r["closes"]:
            return await refuse("Time's up for this raid.")
        if p is not None:
            return await refuse("You're out of this raid.")
        if client.challenge_id:
            return await refuse("You're in a Soul Link run - raids are for your main game.")
        if (client.state or {}).get("game") != r["game"]:
            return await refuse(f"This raid is in {REGION_NAMES[r['region']]} - travel there to join.")
        if client.trade_id or client.battle_id:
            return await refuse("Finish your trade or battle first.")
        r["participants"][client.uid] = new_participant(client.name, level, now)
        r["pool"] += r["bars_per_player"] * BAR
        r["pool_max"] += r["bars_per_player"] * BAR
        client.raiding = True
        log.info("raid %s: %s joined (Lv%d boss), pool %d/%d", r["id"], client.name, level, r["pool"], r["pool_max"])
        await self.server.send(client, {"type": "raid_joined", "id": r["id"], "raid": self.summary(r)})
        self._changed()

    async def on_damage(self, client, msg):
        r = self._raid_of(msg)
        dealt = msg.get("dealt")
        if isinstance(dealt, bool) or not isinstance(dealt, int) or not 0 <= dealt <= DEALT_MAX:
            raise RaidInvalid("dealt must be an integer (milli-bars)")
        if r is None or r["phase"] != "open":
            return
        p = r["participants"].get(client.uid)
        if p is None or p["state"] != "fighting":
            return
        now = self.clock()
        if dealt > p["claimed"]:
            p["hits"] += 1
            p["claimed"] = dealt
        accepted = min(p["claimed"], allowed_total(now - p["joined"]))
        delta = accepted - p["dealt"]
        if delta <= 0:
            return
        p["dealt"] = accepted
        r["pool"] = max(0, r["pool"] - delta)
        if r["pool"] == 0:
            await self._down(r)
        else:
            self._changed()

    async def _down(self, r):
        r["phase"] = "down"
        r["down_at"] = self.clock()
        share = shares(r["participants"])
        log.info("raid %s: the boss is down; shares %s", r["id"],
                 {r["participants"][u]["name"]: round(s, 3) for u, s in share.items()})
        self._try_save()
        for uid, p in r["participants"].items():
            if p["state"] != "fighting":
                continue
            chance = round(share[uid], 4)
            p["chance"] = chance
            p["catch"] = bool(chance >= 1.0 or (chance > 0 and self.rng.random() < chance))
            force = self.catch_force.get(uid) if isinstance(self.catch_force, dict) else self.catch_force
            if force is not None:                             # testing only (YL_RAID_CATCH; e2e: per player)
                p["catch"] = bool(force)
            await self.server.send(self.server.clients.get(uid),
                                   {"type": "raid_down", "id": r["id"], "chance": chance, "catch": p["catch"]})
        self._try_save()
        await self.server.broadcast({"type": "raid_update", "raid": self.summary(r)})

    async def on_result(self, client, msg):
        r = self._raid_of(msg)
        outcome = msg.get("outcome")
        if outcome not in OUTCOMES:
            raise RaidInvalid(f"outcome must be one of {', '.join(OUTCOMES)}")
        if r is None:
            return
        p = r["participants"].get(client.uid)
        if p is None or p["state"] != "fighting":
            return
        if outcome in ("caught", "missed") and r["phase"] != "down":
            outcome = "left"                        # no catch before the pool is empty
        if outcome == "unstarted":
            if p["dealt"] > 0 or p["claimed"] > 0 or r["phase"] != "open":
                outcome = "left"                    # it did fight: its share stays, it's out
            else:
                # the battle never started in that game: as if they had never joined (can join again)
                del r["participants"][client.uid]
                bars = r["bars_per_player"] * BAR
                r["pool"] -= min(bars, max(0, r["pool"] - 1))
                r["pool_max"] = max(r["pool"], r["pool_max"] - bars)
                client.raiding = False
                log.info("raid %s: %s -> unstarted (taken off, pool %d/%d)", r["id"], client.name, r["pool"],
                         r["pool_max"])
                self._changed()
                return
        p["state"] = outcome
        p["at"] = self.clock()
        client.raiding = False
        log.info("raid %s: %s -> %s", r["id"], client.name, outcome)
        if outcome == "caught":
            what = f"the shiny {r['name']}" if r["shiny"] else r["name"]
            await self.server.system(f"{client.name} caught {what}!")
        if r["phase"] == "down" and self._all_reported(r):
            await self.end(r, "down")
        else:
            self._changed()
