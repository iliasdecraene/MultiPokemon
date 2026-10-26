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
# 0.7.3 was published at 2026-10-26 14:39:30 UTC: it applied the player's own Exp Share All setting (default on)
# inside Soul Link runs. Runs without the `exp_share` rule (from before 0.7.4) count as "with Exp Share" from then.
EXP_SHARE_RELEASE = 1793025570
BACKFILL_AGE = 300.0              # an earn time older than this (vs the report) = earned before: not announced


class BadgeError(Exception):
    """Invalid `badges` report; the text goes back as an error."""


def shiny_tier(count: int) -> int:
    return max((t for t in SHINY_TIERS if count >= t), default=0)


def _ts(v, now: float):
    """A report's earn time: None = not earned. A wrong client clock (or an odd value) never blocks the sync: times
    before 2001 or more than a day ahead are taken as 'now' (server time)."""
    if v is None or v is False:
        return None
    if v is True or isinstance(v, bool) or not isinstance(v, (int, float)) or v != v \
            or not MIN_TS <= v <= now + FUTURE:
        return now
    return float(v)


def validate_report(earned, now: float | None = None) -> dict:
    """{region: {"champion": ts|None, "dex": ts|None, "shinies": [key]}} from a client's `badges.earned`.
    Only a wrong shape is refused; unknown regions (newer clients) and keys we can't use are left out quietly."""
    now = time.time() if now is None else now
    if not isinstance(earned, dict):
        raise BadgeError("earned must be an object")
    out = {}
    for region, r in earned.items():
        if region not in REGIONS:
            continue
        if not isinstance(r, dict):
            raise BadgeError(f"{region} must be an object")
        shinies = r.get("shinies")
        if shinies is None:
            shinies = []
        if not isinstance(shinies, list):
            raise BadgeError("shinies must be a list of keys")
        keys = list(dict.fromkeys(k for k in shinies if isinstance(k, str) and KEY_RE.match(k)))[:SHINY_MAX]
        out[region] = {"champion": _ts(r.get("champion"), now), "dex": _ts(r.get("dex"), now), "shinies": keys}
    return out


class BadgeStore:
    def __init__(self, data_dir: str, debounce: float = 1.0):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "badges.json")
        self.debounce = debounce
        self.players: dict[str, dict] = {}
        self.reported: dict[str, float] = {}     # uid -> time of their first main-game report
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
        rep = raw.get("reported") if isinstance(raw, dict) else None
        if isinstance(rep, dict):
            self.reported = {str(u): float(t) for u, t in rep.items() if isinstance(t, (int, float))}
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
        payload = json.dumps({"version": 1, "players": self.players, "reported": self.reported},
                             separators=(",", ":"))
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

    def merge_report(self, uid: str, report: dict, now: float | None = None) -> tuple:
        """Merge a validated report (never removes). Returns (news, old): what's new to announce as
        [(region, "champion"|"dex"|"shiny", tier)], and how many badges arrived that were earned before (the first
        report from this player, or earn times older than BACKFILL_AGE) - those get one summary line at most."""
        now = time.time() if now is None else now
        first = uid not in self.reported
        if first:
            self.reported[uid] = now
        new, old, any_change = [], 0, first
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
                    if first or ts < now - BACKFILL_AGE:
                        old += 1
                    else:
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
                        if first:
                            old += 1 if before == 0 else 0
                        else:
                            new.append((region, "shiny", after))
            if changed:
                p[region] = cur
                any_change = True
        if not p:
            self.players.pop(uid, None)
        if any_change:
            self.schedule_save()
        return new, old

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
            for ev in rec.get("ledger") or []:
                if isinstance(ev, dict) and ev.get("t") == "hof" and isinstance(ev.get("p"), str) \
                        and ev["p"] in rec.get("players", []):
                    ts = ev.get("ts") if isinstance(ev.get("ts"), (int, float)) else time.time()
                    if self.award_soullink(ev["p"], region, run_tier(rec, float(ts)), float(ts)):
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


def run_tier(rec: dict, hof_ts: float) -> str:
    """Gold = played with Exp Share All off, silver = on. Runs without the rule (created before 0.7.4): gold if the
    Hall of Fame was before 0.7.3 came out (no Exp Share All yet), else silver (0.7.3 applied the personal setting,
    default on, inside runs)."""
    rules = rec.get("rules") or {}
    if "exp_share" in rules:
        return "silver" if rules["exp_share"] else "gold"
    return "gold" if hof_ts < EXP_SHARE_RELEASE else "silver"


def count(summary: dict) -> int:
    """Badges in a summary (a shiny hunter badge counts once, whatever its tier)."""
    return sum(bool(s.get("champion")) + bool(s.get("dex")) + bool(s.get("shiny")) + bool(s.get("soullink"))
               for s in summary.values())


def news_text(name: str, region: str, what: str, tier: int) -> str:
    rn = REGION_NAMES.get(region, region)
    if what == "champion":
        return f"{name} earned the {rn} Champion badge!"
    if what == "dex":
        return f"{name} completed the {rn} Pokédex!"
    if tier == 1:
        return f"{name} caught a shiny in {rn} - Shiny hunter badge!"
    return f"{name} reached Shiny hunter {tier} in {rn}!"
