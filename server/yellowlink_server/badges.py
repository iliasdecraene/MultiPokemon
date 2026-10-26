"""Badges per player and region (SPEC.md section 9): main-game reports from clients + Soul Link awards from runs.

Stored in <data_dir>/badges.json: {"version": 1, "players": {uid: {region: {"champion": ts, "dex": ts,
"shinies": [key, ...], "soullink": "gold"|"silver", "soullink_ts": ts}}}}. Nothing is ever removed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time

log = logging.getLogger("yellowlink.badges")

REGIONS = ("kanto", "johto", "sinnoh")
REGION_NAMES = {"kanto": "Kanto", "johto": "Johto", "sinnoh": "Sinnoh"}
REGION_OF_GAME = {"yellow": "kanto", "crystal": "johto", "platinum": "sinnoh"}
SHINY_TIERS = (1, 10, 25, 50, 100)
SHINY_MAX = 1000                  # shiny keys kept per region (the count saturates there)
KEY_RE = re.compile(r"^[A-Za-z0-9_:-]{1,40}$")
SOULLINK_RANK = {"silver": 1, "gold": 2}
MIN_TS = 1e9                      # 2001: anything older isn't a real unix time
FUTURE = 86400.0                  # clocks may be a bit off; a day ahead is the limit


class BadgeError(Exception):
    """Invalid `badges` report; the text goes back as an error."""


def shiny_tier(count: int) -> int:
    return max((t for t in SHINY_TIERS if count >= t), default=0)


def _ts(v, what: str, now: float):
    if v is None or v is False:
        return None
    if v is True:
        return now
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not MIN_TS <= v <= now + FUTURE:
        raise BadgeError(f"{what} must be a time")
    return float(v)


def validate_report(earned, now: float | None = None) -> dict:
    """{region: {"champion": ts|None, "dex": ts|None, "shinies": [key]}} from a client's `badges.earned`."""
    now = time.time() if now is None else now
    if not isinstance(earned, dict):
        raise BadgeError("earned must be an object")
    out = {}
    for region, r in earned.items():
        if region not in REGIONS:
            raise BadgeError("unknown region")
        if not isinstance(r, dict):
            raise BadgeError(f"{region} must be an object")
        shinies = r.get("shinies", [])
        if shinies is None:
            shinies = []
        if not isinstance(shinies, list) or len(shinies) > SHINY_MAX or len(set(map(str, shinies))) != len(shinies) \
                or not all(isinstance(k, str) and KEY_RE.match(k) for k in shinies):
            raise BadgeError(f"shinies must be a list of up to {SHINY_MAX} different keys")
        out[region] = {"champion": _ts(r.get("champion"), "champion", now),
                       "dex": _ts(r.get("dex"), "dex", now), "shinies": shinies}
    return out


class BadgeStore:
    def __init__(self, data_dir: str, debounce: float = 1.0):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "badges.json")
        self.debounce = debounce
        self.players: dict[str, dict] = {}
        self._save_task = None
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.error("could not read %s (%s); keeping a copy and starting empty", self.path, exc)
            try:
                os.replace(self.path, self.path + f".bad-{int(time.time())}")
            except OSError:
                pass
            return
        players = raw.get("players") if isinstance(raw, dict) else None
        for uid, regions in (players or {}).items():
            if not isinstance(regions, dict):
                continue
            clean = {}
            for region, r in regions.items():
                if region in REGIONS and isinstance(r, dict):
                    clean[region] = {k: v for k, v in r.items()
                                     if k in ("champion", "dex", "soullink", "soullink_ts", "shinies")}
                    if not isinstance(clean[region].get("shinies", []), list):
                        clean[region].pop("shinies")
            if clean:
                self.players[str(uid)] = clean

    def save_now(self):
        os.makedirs(self.data_dir, exist_ok=True)
        payload = json.dumps({"version": 1, "players": self.players}, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(prefix=".badges-", suffix=".tmp", dir=self.data_dir)
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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:              # startup backfill (no loop yet): write right away
            try:
                self.save_now()
            except Exception:
                log.exception("failed to save badges")
            return
        if self._save_task is not None and not self._save_task.done():
            return
        self._save_task = loop.create_task(self._delayed_save())

    async def _delayed_save(self):
        try:
            await asyncio.sleep(self.debounce)
        except asyncio.CancelledError:
            return
        try:
            self.save_now()
        except Exception:
            log.exception("failed to save badges")

    async def flush(self):
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        if self.players or os.path.exists(self.path):
            try:
                self.save_now()
            except Exception:
                log.exception("failed to save badges")

    # ------------------------------------------------------------------ changes
    def get(self, uid: str) -> dict | None:
        return self.players.get(uid)

    def merge_report(self, uid: str, report: dict) -> list:
        """Merge a validated report (never removes). Returns what's new: [(region, "champion"|"dex"|"shiny", tier)]."""
        new, any_change = [], False
        p = self.players.setdefault(uid, {})
        for region, r in report.items():
            cur = p.get(region) or {}
            changed = False
            for what in ("champion", "dex"):
                ts = r.get(what)
                if ts is None:
                    continue
                if cur.get(what) is None:
                    cur[what] = ts
                    new.append((region, what, 0))
                    changed = True
                elif ts < cur[what]:
                    cur[what] = ts                  # keep the earliest time
                    changed = True
            if r.get("shinies"):
                have = list(cur.get("shinies") or [])
                seen = set(have)
                before = shiny_tier(len(have))
                for k in r["shinies"]:
                    if len(have) >= SHINY_MAX:
                        break
                    if k not in seen:
                        seen.add(k)
                        have.append(k)
                if len(have) != len(cur.get("shinies") or []):
                    cur["shinies"] = have
                    changed = True
                    after = shiny_tier(len(have))
                    if after > before:
                        new.append((region, "shiny", after))
            if changed:
                p[region] = cur
                any_change = True
        if not p:
            self.players.pop(uid, None)
        if any_change:
            self.schedule_save()
        return new

    def award_soullink(self, uid: str, region: str, tier: str, ts: float) -> bool:
        """Soul Link badge from a run's Hall of Fame. Gold outranks silver. True if it's new or an upgrade."""
        if region not in REGIONS or tier not in SOULLINK_RANK:
            return False
        r = self.players.setdefault(uid, {}).setdefault(region, {})
        if SOULLINK_RANK.get(r.get("soullink"), 0) >= SOULLINK_RANK[tier]:
            return False
        r["soullink"], r["soullink_ts"] = tier, ts
        self.schedule_save()
        return True

    def backfill(self, records) -> int:
        """Award Soul Link badges for every `hof` already in the run ledgers (runs from before 0.7.5)."""
        n = 0
        for rec in records:
            if rec.get("kind") != "soul_link":
                continue
            region = REGION_OF_GAME.get(rec.get("game"))
            tier = run_tier(rec)
            for ev in rec.get("ledger") or []:
                if isinstance(ev, dict) and ev.get("t") == "hof" and isinstance(ev.get("p"), str) \
                        and ev["p"] in rec.get("players", []):
                    ts = ev.get("ts") if isinstance(ev.get("ts"), (int, float)) else time.time()
                    if self.award_soullink(ev["p"], region, tier, float(ts)):
                        n += 1
        if n:
            log.info("awarded %d Soul Link badges from earlier runs", n)
        return n

    # ------------------------------------------------------------------ views
    def summary(self, uid: str) -> dict:
        """Roster form: {region: {champion: bool, dex: bool, shiny: int, soullink}} (only regions with something)."""
        out = {}
        for region, r in (self.players.get(uid) or {}).items():
            s = {"champion": r.get("champion") is not None, "dex": r.get("dex") is not None,
                 "shiny": len(r.get("shinies") or []), "soullink": r.get("soullink")}
            if s["champion"] or s["dex"] or s["shiny"] or s["soullink"]:
                out[region] = s
        return out

    def view(self, uid: str) -> dict:
        """Full form for `badges_data`: times instead of bools."""
        out = {}
        for region, r in (self.players.get(uid) or {}).items():
            out[region] = {"champion": r.get("champion"), "dex": r.get("dex"),
                           "shiny": len(r.get("shinies") or []), "soullink": r.get("soullink"),
                           "soullink_ts": r.get("soullink_ts")}
        return out


def run_tier(rec: dict) -> str:
    """Gold = played with Exp Share All off (or a run from before the rule existed), silver = on."""
    return "silver" if (rec.get("rules") or {}).get("exp_share") else "gold"


def news_text(name: str, region: str, what: str, tier: int) -> str:
    rn = REGION_NAMES.get(region, region)
    if what == "champion":
        return f"{name} earned the {rn} Champion badge!"
    if what == "dex":
        return f"{name} completed the {rn} Pokédex!"
    if tier == 1:
        return f"{name} caught a shiny in {rn} - Shiny hunter badge!"
    return f"{name} reached Shiny hunter {tier} in {rn}!"
