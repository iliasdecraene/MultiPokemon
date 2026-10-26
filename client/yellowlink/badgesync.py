"""Badges, app side (SPEC.md section 9): the local badge book, syncing with the server, toasts.

The rules of each badge and the per-game detection live in badges.py and the adapters' `progress(mem)` (the game
side). Here: every ~10 s at a safe moment in the main game the app asks the adapter for progress, merges it into the
book next to the save (`<data_dir>/badges/<uid>.json`, never removes anything), toasts what's new and reports the whole
book to the server, which shows everyone's badges in the roster. Soul Link badges come from the server (it records
them from the run's Hall of Fame ledger event); the book keeps what each server last said, for showing offline.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from . import badges as rules
from . import ui
from .badges import DEX_TOTAL, REGION_NAMES, REGION_OF_GAME, REGIONS

log = logging.getLogger("yellowlink.badges")

MIN_TS = 1e9                      # earn times before 2001 = a wrong clock (stored as this, sent as "now")
SHINY_MAX = 1000                  # keys the server keeps per region (the book keeps rules.SHINY_KEEP)
SOCKETS = ("champion", "dex", "shiny", "soullink")
SOULLINK_RANK = {None: 0, "silver": 1, "gold": 2}
CHECK_EVERY = 600                 # frames between progress checks (~10 s)
WILD_EVERY = 15                   # frames between wild-shiny checks in a Yellow battle

NAMES = {"champion": "Champion", "dex": "Pokédex", "shiny": "Shiny hunter", "soullink": "Soul Link"}


next_tier = rules.next_shiny_tier


def how_to(kind: str, region: str) -> str:
    """How to earn a badge (hover text)."""
    game = {"kanto": "Yellow", "johto": "Crystal", "sinnoh": "Platinum"}.get(region, "the game")
    if kind == "champion":
        return f"Beat the Champion and enter the Hall of Fame in {game}."
    if kind == "dex":
        extra = {"kanto": " (all but Mew)", "johto": " (all but Mew and Celebi)",
                 "sinnoh": " of the Sinnoh dex (all but Manaphy)"}
        return f"Catch all {DEX_TOTAL.get(region, '?')} Pokémon{extra.get(region, '')} in {game}."
    if kind == "shiny":
        note = " (Gen-2 shiny DVs count)" if region == "kanto" else ""
        return f"Catch shinies in {game}{note}. Tiers: 1, 10, 25, 50, 100."
    return f"Finish a Soul Link in {game} (Hall of Fame). Gold: Exp Share All off, silver: on."


def earned_text(region: str, kind: str, tier=None) -> str:
    rn = REGION_NAMES.get(region, region)
    if kind == "shiny":
        return f"Badge earned: {rn} Shiny hunter!" if (tier or 1) <= 1 else f"Shiny hunter {tier}: {rn}!"
    if kind == "soullink":
        return f"Badge earned: {rn} Soul Link ({tier or 'gold'})!"
    return f"Badge earned: {rn} {NAMES[kind]}!"


def blank(summary_region=None) -> dict:
    s = {"champion": False, "dex": False, "shiny": 0, "soullink": None}
    if summary_region:
        s.update({k: summary_region.get(k, s[k]) for k in s})
    return s


def combine(a: dict | None, b: dict | None) -> dict:
    """Two summaries ({region: {champion, dex, shiny, soullink}}) of the same player -> the best of both."""
    out = {}
    for region in REGIONS:
        x, y = (a or {}).get(region), (b or {}).get(region)
        if not x and not y:
            continue
        x, y = blank(x), blank(y)
        sl = x["soullink"] if SOULLINK_RANK.get(x["soullink"], 0) >= SOULLINK_RANK.get(y["soullink"], 0) \
            else y["soullink"]
        out[region] = {"champion": bool(x["champion"] or y["champion"]), "dex": bool(x["dex"] or y["dex"]),
                       "shiny": max(int(x["shiny"] or 0), int(y["shiny"] or 0)), "soullink": sl}
    return out


class BadgeBook:
    """`<data_dir>/badges/<uid>.json`: {"version": 1, "earned": {region: {"champion": ts, "dex": ts, "shinies": [key]}},
    "soullink": {server_id: {region: "gold"|"silver"}}}. Earned badges are never removed."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.earned: dict = {}
        self.soullink: dict = {}
        self.changed = False             # the last apply() changed the book
        self.load()

    def load(self):
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            earned, soullink = raw.get("earned", {}), raw.get("soullink", {})
            earned, soullink = ({} if earned is None else earned), ({} if soullink is None else soullink)
            if not isinstance(earned, dict) or not isinstance(soullink, dict) \
                    or not all(isinstance(v, dict) for v in list(earned.values()) + list(soullink.values())):
                raise ValueError("wrong types")
            self.earned = {r: {k: v for k, v in e.items() if k != "soullink"}
                           for r, e in rules.normalise(earned).items()}
            self.earned = {r: e for r, e in self.earned.items() if e}
            self.soullink = {str(sid): {r: t for r, t in regions.items() if r in REGIONS and t in ("gold", "silver")}
                             for sid, regions in soullink.items()}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.error("badge book %s unreadable (%s); keeping a copy and starting fresh", self.path, exc)
            self.earned, self.soullink = {}, {}
            try:
                os.replace(self.path, self.path.with_name(f"badges.bad-{int(time.time())}.json"))
            except OSError:
                pass

    def save(self) -> bool:
        if self.path is None:
            return False
        data = json.dumps({"version": 1, "earned": self.earned, "soullink": self.soullink}, indent=1)
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            log.exception("could not save the badge book")
            return False

    # --- merging ----------------------------------------------------------------------------------------------
    def apply(self, region: str, progress: dict | None, now: float | None = None) -> list:
        """Merge one progress reading of `region`'s game (badges.merge: never removes anything); returns what is
        new: [("champion", None), ("dex", None), ("shiny", tier)]. Saves when anything changed."""
        self.changed = False
        if region not in REGIONS or not isinstance(progress, dict):
            return []
        before = json.dumps(self.earned.get(region), sort_keys=True)
        now = time.time() if now is None else now
        found = rules.merge(self.earned, region, progress, now if now >= MIN_TS else MIN_TS)
        self.changed = json.dumps(self.earned.get(region), sort_keys=True) != before
        if self.changed:
            self.save()
        out = []
        for item in found:
            kind, _, tier = item.partition(":")
            out.append((kind, int(tier) if tier else None))
        return out

    def note_server(self, server_id: str, summary: dict) -> list:
        """The server's view of our badges (our own roster entry): remember its Soul Link badges. Returns the
        regions whose Soul Link badge is new or upgraded here (for a toast) as [(region, tier)]."""
        mine = self.soullink.setdefault(server_id, {})
        news = []
        for region, s in (summary or {}).items():
            t = (s or {}).get("soullink")
            if region in REGIONS and t in ("gold", "silver") and SOULLINK_RANK[t] > SOULLINK_RANK[mine.get(region)]:
                mine[region] = t
                news.append((region, t))
        if news:
            self.save()
        return news

    # --- views ------------------------------------------------------------------------------------------------
    def wire(self) -> dict:
        """The `badges` message (SPEC.md section 9)."""
        earned = {}
        now = time.time()

        def ts(v):          # a time from a wrong clock goes as True (= "now" on the server's clock)
            if v is None:
                return None
            return v if MIN_TS < v <= now + 86400 else True
        for region, r in self.earned.items():
            earned[region] = {"champion": ts(r.get("champion")), "dex": ts(r.get("dex")),
                              "shinies": list(r.get("shinies") or [])[:SHINY_MAX]}
        return {"type": "badges", "earned": earned}

    def summary(self, server_id: str | None = None) -> dict:
        out = {}
        for region in REGIONS:
            r = self.earned.get(region) or {}
            sl = (self.soullink.get(server_id) or {}).get(region) if server_id else None
            s = {"champion": "champion" in r, "dex": "dex" in r, "shiny": len(r.get("shinies") or []),
                 "soullink": sl}
            if s["champion"] or s["dex"] or s["shiny"] or s["soullink"]:
                out[region] = s
        return out


# --- the app side -------------------------------------------------------------------------------------------
class BadgeMixin:
    """Mixed into App: the badge book, progress checks, server sync, the Yellow wild-shiny flag."""

    def init_badges(self):
        path = getattr(self.cfg, "badges_path", None)
        self.badges = BadgeBook(path)
        self.badge_feature = False       # the server knows badges (welcome.features)
        self.badge_views: dict = {}      # uid -> {region: BadgeView} from badges_data (times for hover)
        self.wild_shiny = False          # Yellow: the wild enemy right now would be shiny in Gen 2
        self._badge_dirty = False        # something new not reported yet
        self._badge_due = CHECK_EVERY // 2   # frame from which the next progress check runs (first safe frame)

    def badges_due(self):
        """Check on the next safe frame (e.g. the PC just closed: count a deposited shiny before it can be traded)."""
        self._badge_due = 0

    @property
    def badge_region(self) -> str | None:
        return REGION_OF_GAME.get(self.game_id)

    def badges_on_welcome(self, msg):
        self.badge_feature = "badges" in (msg.get("features") or [])
        self.badge_views.clear()
        self._badge_dirty = True
        self.badges_report()
        self.badges_on_roster(quiet=True)

    def badges_report(self):
        """Send the whole book (SPEC.md section 9) to a server that knows badges."""
        if not self._badge_dirty or not self.badge_feature or self.net.status != "online":
            return
        if not self.badges.earned:
            self._badge_dirty = False
            return
        if self.net.send(self.badges.wire()):
            self._badge_dirty = False

    def badges_on_roster(self, quiet=False):
        me = next((e for e in self.roster if e.get("uid") == self.cfg["uid"]), None)
        if me is None or "badges" not in me:
            return
        for region, tier in self.badges.note_server(self.cfg.server_id, me.get("badges") or {}):
            if not quiet:
                self.toast(earned_text(region, "soullink", tier), 6, ui.ACCENT)
                self.sys_msg(earned_text(region, "soullink", tier), ui.ACCENT)

    def on_badges_data(self, msg):
        uid = msg.get("uid")
        if isinstance(uid, str) and isinstance(msg.get("badges"), dict):
            self.badge_views[uid] = msg["badges"]

    def request_badges(self, uid):
        if self.badge_feature and uid:
            self.net.send({"type": "get_badges", "uid": uid})

    def badge_summary(self, uid) -> dict:
        """{region: {champion, dex, shiny, soullink}} of a player; yours = your book + what the server knows."""
        e = next((x for x in self.roster if x.get("uid") == uid), None) or {}
        server = e.get("badges") or {}
        if uid == self.cfg["uid"]:
            return combine(self.badges.summary(self.cfg.server_id), server)
        return server

    def badge_time(self, uid, region, kind):
        """When a badge was earned (unix time) if known, else None."""
        if uid == self.cfg["uid"] and kind in ("champion", "dex"):
            t = (self.badges.earned.get(region) or {}).get(kind)
            if t:
                return t
        v = (self.badge_views.get(uid) or {}).get(region) or {}
        return v.get("soullink_ts" if kind == "soullink" else kind) if kind != "shiny" else None

    def _badge_progress(self):
        fn = getattr(self.G, "progress", None)
        if fn is None:
            return None
        return fn(self.game)

    def badges_can_check(self) -> bool:
        return bool(self.full and not self.chal and self.badge_region and getattr(self, "game", None) is not None
                    and not self.trade and not self.battle and not self.live)

    def badges_step(self):
        """Called every frame from App.step()."""
        if self.frame % WILD_EVERY == 0:
            self._update_wild_shiny()
        if self.frame < self._badge_due or not self.badges_can_check():
            self.badges_report()
            return
        g = self.game
        try:
            if not g.in_game() or not g.is_safe():
                return                       # due: try again on the next frame (the first safe one runs it)
            prog = self._badge_progress()
        except Exception:  # noqa: BLE001 - a bad read must never stop the game
            log.exception("badge progress check failed")
            prog, self._badge_due = None, self.frame + CHECK_EVERY
            return
        if prog is None:
            self._badge_due = self.frame + 30    # couldn't read right now (party half-written...)
            return
        self._badge_due = self.frame + CHECK_EVERY
        region = self.badge_region
        prog = dict(prog, shinies=list(prog.get("shinies") or []) + self._pc_shiny_keys(region, prog.get("owner")))
        self.badges_merge(region, prog)

    def _pc_shiny_keys(self, region, owner) -> list:
        """Own shiny catches of this game sitting in the YellowLink PC store (also those the game can't show)."""
        pcs = getattr(self, "pcsync", None)
        if not owner or pcs is None:
            return []
        cache = getattr(self, "_pc_shiny_cache", None)
        if cache is None or cache[0] != (region, repr(sorted(owner.items()))):
            cache = self._pc_shiny_cache = ((region, repr(sorted(owner.items()))), {})
        memo, keys = cache[1], []
        rep = {"kanto": "gen1", "johto": "gen2", "sinnoh": "gen4"}[region]
        try:
            for box in pcs.committed["boxes"]:
                for e in box:
                    ck = (e.get("id"), e.get(rep))
                    if ck not in memo:
                        memo[ck] = rules.entry_shiny_key(e, region, owner)
                    if memo[ck]:
                        keys.append(memo[ck])
        except Exception:  # noqa: BLE001 - a damaged store must not stop the check
            log.exception("PC shiny scan failed")
        if len(memo) > 5000:
            memo.clear()
        return rules.number_keys(keys)       # identical mons in the store: key, key-2, ... (max with the game's own)

    def badges_merge(self, region, prog):
        news = self.badges.apply(region, prog)
        if self.badges.changed:              # also a new shiny without a new tier: the count others see follows
            self._badge_dirty = True
        for kind, tier in news:
            text = earned_text(region, kind, tier)
            log.info("badge: %s", text)
            self.toast(text, 6, ui.ACCENT)
            self.sys_msg(text, ui.ACCENT)
        self.badges_report()
        return news

    def _update_wild_shiny(self):
        shiny = False
        if self.full and self.game_id == "yellow" and getattr(self, "game", None) is not None:
            fn = getattr(self.G, "wild_is_shiny", None)
            try:
                if fn is not None and self.game.in_battle():
                    shiny = bool(fn(self.game))
            except Exception:  # noqa: BLE001
                log.exception("wild shiny check failed")
        if shiny and not self.wild_shiny:
            log.info("wild shiny in battle")
        self.wild_shiny = shiny
