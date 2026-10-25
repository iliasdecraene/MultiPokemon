"""Soul Link Nuzlocke: rule bookkeeping (SoulLink) and in-game enforcement (Referee). See SPEC.md section 7.

The server keeps the ledger (what happened); everything here is derived from it, so both players always agree.

Rules (Nuzlocke + Soul Link):
- From the moment you have Poké Balls, only the first wild encounter per area counts. Gifts (starters, Eevee...)
  count as the area's encounter at any time, so both starters are partners.
- Two catches in the same area = a linked pair. If one player's encounter there failed, the other's catch is
  unusable ("no partner"). A fainted (or released/lost) mon is dead, and so is its partner.
- PC rule: moving a linked mon between party and PC moves its partner the same way.
- Blackout (whole party fainted) or running out of usable Pokémon = challenge failed.
- Optional: dupes clause (a first encounter whose evolution family you already caught doesn't count; keep looking)
  and shiny clause (a shiny can always be caught and doesn't use up the area).
Unusable mons are moved to the PC automatically and moved back there if withdrawn.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

log = logging.getLogger("yellowlink.soullink")

# National dex -> evolution family id (lowest dex in the family). Derived from Crystal's evolution table
# (all 251 species; babies like Pichu belong to their family).
FAMILY = [0, 1, 1, 1, 4, 4, 4, 7, 7, 7, 10, 10, 10, 13, 13, 13, 16, 16, 16, 19, 19, 21, 21, 23, 23, 25, 25, 27, 27,
          29, 29, 29, 32, 32, 32, 35, 35, 37, 37, 39, 39, 41, 41, 43, 43, 43, 46, 46, 48, 48, 50, 50, 52, 52, 54, 54,
          56, 56, 58, 58, 60, 60, 60, 63, 63, 63, 66, 66, 66, 69, 69, 69, 72, 72, 74, 74, 74, 77, 77, 79, 79, 81, 81,
          83, 84, 84, 86, 86, 88, 88, 90, 90, 92, 92, 92, 95, 96, 96, 98, 98, 100, 100, 102, 102, 104, 104, 106, 106,
          108, 109, 109, 111, 111, 113, 114, 115, 116, 116, 118, 118, 120, 120, 122, 123, 124, 125, 126, 127, 128,
          129, 129, 131, 132, 133, 133, 133, 133, 137, 138, 138, 140, 140, 142, 143, 144, 145, 146, 147, 147, 147,
          150, 151, 152, 152, 152, 155, 155, 155, 158, 158, 158, 161, 161, 163, 163, 165, 165, 167, 167, 41, 170,
          170, 25, 35, 39, 175, 175, 177, 177, 179, 179, 179, 43, 183, 183, 185, 60, 187, 187, 187, 190, 191, 191,
          193, 194, 194, 133, 133, 198, 79, 200, 201, 202, 203, 204, 204, 206, 207, 95, 209, 209, 211, 123, 213,
          214, 215, 216, 216, 218, 218, 220, 220, 222, 223, 223, 225, 226, 227, 228, 228, 116, 231, 231, 137, 234,
          235, 106, 106, 124, 125, 126, 241, 113, 243, 244, 245, 246, 246, 246, 249, 250, 251]


def family(dex: int) -> int:
    return FAMILY[dex] if 0 < dex < len(FAMILY) else dex


# ---------------------------------------------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------------------------------------------
class SoulLink:
    """Rule state of one challenge, derived from the server ledger plus our own not-yet-echoed events."""

    def __init__(self, me: str, partner: str, rules: dict | None = None, ledger: list | None = None):
        self.me, self.partner = me, partner
        self.rules = {"dupes": True, "shiny": True, **(rules or {})}
        self.ledger: list = []
        self.pending: list = []          # our events sent but not echoed yet (applied after the ledger)
        self._cache = None
        for ev in ledger or []:
            self.add(ev)

    # --- input -----------------------------------------------------------------------------------------------
    def add(self, ev: dict):
        """An event echoed by the server (ours or the partner's)."""
        if any(e.get("seq") == ev.get("seq") for e in self.ledger[-20:]):
            return
        self.ledger.append(ev)
        if ev.get("p") == self.me:
            cid, sig = ev.get("cid"), self._sig(ev)
            for i, p in enumerate(self.pending):
                if (cid and p.get("cid") == cid) or (not cid and self._sig(p) == sig):
                    del self.pending[i]
                    break
        self._cache = None

    def local(self, ev: dict) -> dict:
        """Record one of our own events before the server echoes it; returns the event to send."""
        ev = dict(ev)
        ev.setdefault("cid", uuid.uuid4().hex[:10])     # lets the server drop a resend after a connection blip
        self.pending.append({**ev, "p": self.me, "seq": None})
        self._cache = None
        return ev

    @staticmethod
    def _sig(ev):
        return (ev.get("t"), ev.get("area"), ev.get("status"), ev.get("key"), ev.get("where"), ev.get("new"))

    # --- derived state ---------------------------------------------------------------------------------------
    def state(self) -> dict:
        if self._cache is not None:
            return self._cache
        st = {uid: {"enc": {}, "keys": {}, "dead": {}, "loc": {}, "balls": False, "hof": False, "blackout": False}
              for uid in (self.me, self.partner)}
        order = 0
        for ev in self.ledger + self.pending:
            order += 1
            s = st.get(ev.get("p"))
            if s is None:
                continue
            t = ev.get("t")
            if t == "enc":
                if ev.get("status") == "skipped":
                    continue
                s["enc"][ev["area"]] = ev
                if ev.get("status") == "caught" and ev.get("key"):
                    s["keys"][ev["key"]] = ev["area"]
            elif t == "dead":
                s["dead"].setdefault(ev["key"], ev.get("why", "fainted"))
            elif t == "loc":
                s["loc"][ev["key"]] = (order, ev["where"])
            elif t == "swap":
                area = s["keys"].pop(ev["old"], None)
                if area is not None:
                    s["keys"][ev["new"]] = area
                    enc = dict(s["enc"].get(area, {}))
                    enc["key"] = ev["new"]
                    s["enc"][area] = enc
            elif t in ("balls", "hof", "blackout"):
                s[t] = True
        self._cache = st
        return st

    def mine(self) -> dict:
        return self.state()[self.me]

    def theirs(self) -> dict:
        return self.state()[self.partner]

    def area_used(self, area: str, uid: str | None = None) -> bool:
        return area in self.state()[uid or self.me]["enc"]

    def known(self, key: str) -> bool:
        m = self.mine()
        return key in m["keys"] or key in m["dead"]

    def partner_of(self, key: str, uid: str | None = None) -> str | None:
        """The linked partner's key (other player), if both caught in that area."""
        uid = uid or self.me
        other = self.partner if uid == self.me else self.me
        area = self.state()[uid]["keys"].get(key)
        if area is None:
            return None
        enc = self.state()[other]["enc"].get(area)
        return enc.get("key") if enc and enc.get("status") == "caught" else None

    def status(self, key: str) -> str:
        """unknown | dead | linked | waiting (partner hasn't had this area yet)."""
        m = self.mine()
        if key in m["dead"]:
            return "dead"
        area = m["keys"].get(key)
        if area is None:
            return "unknown"
        return "linked" if self.partner_of(key) else "waiting"

    def usable(self, key: str) -> bool:
        return self.status(key) in ("linked", "waiting")

    def is_dupe(self, dex: int) -> bool:
        fam = family(dex)
        m = self.mine()
        return any(family(e.get("dex", 0)) == fam for e in m["enc"].values() if e.get("status") == "caught")

    def desired_location(self, key: str) -> str | None:
        """Where the PC rule wants our mon: the latest party/PC move of this mon or its partner wins."""
        mine = self.mine()["loc"].get(key)
        pk = self.partner_of(key)
        theirs = self.theirs()["loc"].get(pk) if pk else None
        best = max((x for x in (mine, theirs) if x), default=None)
        return best[1] if best else None

    def partner_moved_last(self, key: str) -> bool:
        mine = self.mine()["loc"].get(key)
        pk = self.partner_of(key)
        theirs = self.theirs()["loc"].get(pk) if pk else None
        return bool(theirs and (not mine or theirs[0] > mine[0]))

    def needed_deaths(self) -> list:
        """Our mons the rules kill because of what happened on the partner's side."""
        out = []
        mine, theirs = self.mine(), self.theirs()
        for key, area in mine["keys"].items():
            if key in mine["dead"]:
                continue
            enc = theirs["enc"].get(area)
            if enc and enc.get("status") == "failed":
                out.append({"t": "dead", "key": key, "why": "partner missed"})
                continue
            pk = enc.get("key") if enc and enc.get("status") == "caught" else None
            if pk and pk in theirs["dead"]:
                out.append({"t": "dead", "key": key, "why": "partner fainted"})
        return out

    def pairs(self) -> list:
        """[(area, my_key, their_key, my_status)] for display."""
        mine, theirs = self.mine(), self.theirs()
        rows = []
        for area in sorted(set(mine["enc"]) | set(theirs["enc"])):
            a, b = mine["enc"].get(area, {}), theirs["enc"].get(area, {})
            rows.append((area, a.get("key"), b.get("key"), a.get("status"), b.get("status"),
                         a.get("dex"), b.get("dex")))
        return rows

    def counts(self) -> dict:
        m = self.mine()
        alive = sum(1 for k in m["keys"] if self.usable(k))
        return {"alive": alive, "dead": len(m["dead"]), "caught": len(m["keys"])}


# ---------------------------------------------------------------------------------------------------------------
# Enforcement in the running game
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class MonInfo:
    key: str
    dex: int
    hp: int
    egg: bool = False
    nick: str = ""


class Referee:
    """Watches one player's game through a `view` and enforces the rules.

    view interface (implemented per game in challenge_view.py):
      ready() safe() battle() battle_slot() has_balls() area() area_name(area) party() box()
      all_keys() boxes_ok() to_box(slot) to_party(box_index) hof() badges() hold()
    hold() is True while the game hides part of the party (Crystal's Bug-Catching Contest): no scans, no forced
    moves, no blackout/gift checks until a while after it ends.
    party()/box() return lists of MonInfo (party() None while the game is mid-update).
    send(event) sends a ledger event; notify(text, kind) shows a message ("info" | "good" | "bad").
    """

    SAFE_EVERY = 6            # frames between light checks while standing in the overworld
    SCAN_EVERY = 600          # frames between full PC scans (release/rollback detection)
    END_FRAMES = 8            # frames without battle data before a battle counts as over
    HOLD_AFTER = 300          # frames to keep checks off after a party-hiding event (contest) ends

    def __init__(self, sl: SoulLink, view, send, notify):
        self.sl, self.view, self._send, self.notify = sl, view, send, notify
        self.battle = None
        self.frame = 0
        self.fresh_load = True        # first checks after loading the game: settle "open" encounters
        self.pc_open = False
        self.scan_due = True
        self.last_party: list = []
        self.last_loc: dict = {}      # key -> "party"|"box" as of the previous light check
        self.forced: set = set()      # keys we moved ourselves (don't report as the player's move)
        self.told: set = set()        # one-time messages already shown
        self.last_badges = None
        self.progress = None          # callback(badges) set by the app
        self.ended = False
        self.hold_until = 0
        self.holding = False
        self.hold_n = 0

    def send(self, ev):
        self._send(self.sl.local(ev))

    def tell_once(self, tag, text, kind="info"):
        if tag not in self.told:
            self.told.add(tag)
            self.notify(text, kind)

    # --- main entry: call every frame --------------------------------------------------------------------------
    def step(self):
        self.frame += 1
        if self.ended or not self.view.ready():
            return
        # Crystal's Bug-Catching Contest hides all but the lead from the gate until the judging is over: stay on
        # hold until the party is back to its size from before (the contest flag clears earlier than that).
        if self.view.hold():
            if not self.holding:
                self.holding, self.hold_n = True, len(self.last_party)
            self.hold_until = self.frame + self.HOLD_AFTER
        elif self.holding:
            p = self.view.party()
            if p is not None and len(p) >= self.hold_n:
                self.holding = False
            else:
                self.hold_until = self.frame + self.HOLD_AFTER
        held = self.frame < self.hold_until
        info = self.view.battle()
        if info is not None and self.battle is None:
            self._battle_start(info, resumed=self.fresh_load)
        if self.battle is not None:
            if info is not None:
                self.battle["gone"] = 0
                self._battle_tick()
            else:                                    # over once "no battle" holds for a few frames
                self.battle["gone"] = self.battle.get("gone", 0) + 1
                if self.battle["gone"] >= self.END_FRAMES:
                    self.battle["over"] = True
            if self.battle.get("over") and self._battle_end(held):
                self.battle = None
            return
        if held or self.pc_open or self.frame % self.SAFE_EVERY or not self.view.safe():
            return
        self._light_check()
        if self.scan_due or self.frame % self.SCAN_EVERY == 0:
            self.scan_due = False
            self._full_scan()

    def on_pc_open(self):
        self.pc_open = True

    def on_pc_close(self):
        self.pc_open = False
        self.scan_due = True

    # --- battles ---------------------------------------------------------------------------------------------
    def _battle_start(self, info, resumed=False):
        area = self.view.area()
        b = {"info": info, "area": area, "mode": "none", "fainted": set(), "party": None, "over": False,
             "before": self._present_keys()}
        self.battle = b
        if info.get("kind") != "wild":
            return
        if resumed and self.sl.mine()["enc"].get(area, {}).get("status") == "open":
            b["mode"] = "count"                      # the run was paused/reloaded in this very encounter: go on
            return
        name = self.view.area_name(area)
        rules_on = self.sl.mine()["balls"] or self.view.has_balls()
        if info.get("contest"):
            b["mode"] = "free"                       # Bug-Catching Contest: the mon you keep arrives later as a gift
        elif self.sl.rules["shiny"] and info.get("shiny"):
            b["mode"] = "shiny"
            self.notify("A shiny! Shiny clause: catch it - it doesn't use up this area.", "good")
        elif not rules_on:
            b["mode"] = "free"                       # no Poké Balls yet: nothing counts
        elif self.sl.area_used(area):
            b["mode"] = "illegal"
            self.tell_once(f"used:{area}", f"You already had your encounter in {name} - you can't keep this one.")
        elif self.sl.rules["dupes"] and self.sl.is_dupe(info.get("dex", 0)):
            b["mode"] = "illegal"
            self.send({"t": "enc", "area": area, "dex": info.get("dex", 0), "status": "skipped", "why": "dupe"})
            self.notify(f"Dupes clause: you already caught this family. This one doesn't count - "
                        f"your next encounter in {name} does.")
        else:
            b["mode"] = "count"
            self.send({"t": "enc", "area": area, "dex": info.get("dex", 0), "status": "open"})
            self.notify(f"First encounter in {name}! Catch it to link it with your partner's.", "good")

    def _battle_tick(self):
        b = self.battle
        if b["party"] is None:
            p = self.view.party()
            b["party"] = p if p is not None else None
        slot = self.view.battle_slot()
        if slot is None or b["party"] is None:
            return
        idx, hp = slot
        if hp != 0 or not 0 <= idx < len(b["party"]):
            b["zero"] = None
            return
        # count a faint only when HP 0 holds on two frames in a row (a switch writes index and HP separately)
        if b.get("zero") != idx:
            b["zero"] = idx
            return
        mon = b["party"][idx]
        if mon.key not in b["fainted"]:
            b["fainted"].add(mon.key)
            self._kill(mon, "fainted")

    def _kill(self, mon, why):
        if not self.sl.mine()["balls"] and not self.view.has_balls():
            return                                   # before the Nuzlocke starts (first rival fight)
        if self.sl.known(mon.key) and mon.key not in self.sl.mine()["dead"]:
            self.send({"t": "dead", "key": mon.key, "why": why})
            nick = mon.nick or "Your Pokémon"
            self.notify(f"{nick} fainted. Rest in peace - its partner is gone too.", "bad")

    def _battle_end(self, held=False) -> bool:
        """Settle the battle once the party is readable again. Returns True when done."""
        party = self.view.party()
        if party is None:
            return False
        b = self.battle
        for m in party:
            if m.hp == 0 and not m.egg and m.key not in b["fainted"]:
                b["fainted"].add(m.key)
                self._kill(m, "fainted")
        rules_on = self.sl.mine()["balls"] or self.view.has_balls()
        living = [m for m in party if not m.egg]
        if rules_on and not held and living and all(m.hp == 0 or m.key in b["fainted"] for m in living):
            self.send({"t": "blackout"})
            self.notify("You blacked out. The Soul Link is over...", "bad")
            self.ended = True
        dex = b["info"].get("dex", 0)
        new = [m for m in party + (self.view.box() or []) if m.key not in b["before"]]
        caught = next((m for m in new if m.dex == dex), None)
        area, mode = b["area"], b["mode"]
        if mode == "count":
            if caught:
                self.send({"t": "enc", "area": area, "dex": dex, "status": "caught", "key": caught.key})
            else:
                self.send({"t": "enc", "area": area, "dex": dex, "status": "failed"})
                self.notify(f"Missed the encounter in {self.view.area_name(area)}. "
                            f"Your partner's catch there can't be used.", "bad")
        elif mode == "shiny" and caught:
            n = sum(1 for a in self.sl.mine()["enc"] if a.startswith(area + "*"))
            self.send({"t": "enc", "area": f"{area}*{n + 1}"[:40], "dex": dex, "status": "caught",
                       "key": caught.key, "shiny": True})
        elif mode == "free" and caught:
            self.send({"t": "enc", "area": area, "dex": dex, "status": "caught", "key": caught.key})
        elif mode == "illegal" and caught:
            self.send({"t": "dead", "key": caught.key, "why": "not first encounter"})
            self.notify("That catch doesn't count - it goes to the PC and stays there.", "bad")
        self.last_party = party
        self.scan_due = True
        return True

    # --- overworld checks ------------------------------------------------------------------------------------
    def _present_keys(self) -> set:
        party = self.view.party()
        keys = {m.key for m in (party if party is not None else self.last_party)}
        for m in self.view.box() or []:
            keys.add(m.key)
        return keys

    def _light_check(self):
        party = self.view.party()
        if party is None:
            return
        box = self.view.box() or []
        sl = self.sl
        if not sl.mine()["balls"] and self.view.has_balls():
            self.send({"t": "balls"})
            self.notify("You've got Poké Balls - the Soul Link rules are on now!", "good")
        if self.fresh_load:
            self.fresh_load = False
            for area, enc in list(sl.mine()["enc"].items()):
                if enc.get("status") == "open":       # the game was closed/reloaded mid-battle
                    self.send({"t": "enc", "area": area, "dex": enc.get("dex", 0), "status": "failed",
                               "auto": True})
        if self.view.hof() and not sl.mine()["hof"]:
            self.send({"t": "hof"})
            self.notify("Hall of Fame! When your partner gets here too, the Soul Link is complete.", "good")
        badges = self.view.badges()
        if badges != self.last_badges:
            self.last_badges = badges
            if self.progress:
                self.progress(badges)
        # new mons outside battle: gifts, eggs, or an in-game trade
        present = {m.key: m for m in party + box}
        unknown = [m for m in present.values() if not sl.known(m.key)]
        if unknown:
            gone = [k for k in self.last_loc if k not in present and sl.usable(k)]
            if len(unknown) == 1 and len(gone) == 1 and len(party) == len(self.last_party):
                self.send({"t": "swap", "old": gone[0], "new": unknown[0].key})
                self.notify("Traded! The link moves to your new Pokémon.")
            else:
                area = self.view.area()
                for m in unknown:
                    if not sl.area_used(area):
                        self.send({"t": "enc", "area": area, "dex": m.dex, "status": "caught", "key": m.key,
                                   "gift": True})
                        self.notify(f"Gift Pokémon - it counts as your encounter in {self.view.area_name(area)}.")
                    else:
                        self.send({"t": "dead", "key": m.key, "why": "extra gift"})
                        self.notify("You already had your encounter here, so this Pokémon goes to the PC.", "bad")
        # fainted outside battle (poison in Gen 1)
        for m in party:
            if m.hp == 0 and not m.egg and sl.usable(m.key):
                self._kill(m, "fainted")
        # deaths caused by the partner's side
        for ev in sl.needed_deaths():
            self.send(ev)
            if ev["why"] == "partner fainted":
                self.notify("Your partner's Pokémon fainted - its Soul Link partner is gone too.", "bad")
            else:
                self.notify("Your partner missed their encounter there - your catch can't be used.", "bad")
        # the player's own party <-> PC moves (PC rule propagates them)
        loc = {m.key: "party" for m in party}
        for m in box:
            loc[m.key] = "box"
        for key, where in loc.items():
            old = self.last_loc.get(key)
            if old and old != where and key not in self.forced and sl.usable(key) and sl.partner_of(key):
                if sl.desired_location(key) != where:
                    self.send({"t": "loc", "key": key, "where": where})
        self.forced.clear()
        self.last_loc = loc
        self.last_party = party
        self._enforce(party, box)

    def _enforce(self, party, box):
        sl = self.sl
        # 1. unusable mons out of the party
        for i in range(len(party) - 1, -1, -1):
            m = party[i]
            if m.egg or sl.usable(m.key) or not sl.known(m.key):
                continue
            if len(party) == 1:
                spare = next((j for j, b in enumerate(box) if sl.usable(b.key)), None)
                if spare is not None and self.view.to_party(spare):
                    self.forced.add(box[spare].key)
                    self.notify("Your last Pokémon can't fight anymore - swapped in one from the PC.")
                    self.scan_due = True
                    return
                if not self._any_usable():
                    if sl.mine()["balls"]:
                        self.send({"t": "blackout", "why": "no Pokémon left"})
                        self.notify("No usable Pokémon left. The Soul Link is over...", "bad")
                        self.ended = True
                    return
                self.tell_once(f"last:{m.key}", "Your only Pokémon can't be used - withdraw another one from "
                                               "the PC.", "bad")
                return
            if self.view.to_box(i):
                self.forced.add(m.key)
                self.notify(f"{m.nick or 'A Pokémon'} can't be used anymore and went to the PC.", "bad")
            else:
                self.tell_once(f"full:{m.key}", f"Couldn't put {m.nick or 'a Pokémon'} in the PC (box full, or it "
                                                f"holds Mail) - please box it yourself.", "bad")
            return                                    # one move per check; the next check rereads the party
        # 2. PC rule: follow the partner's moves
        for i, m in enumerate(party):
            if sl.usable(m.key) and sl.partner_moved_last(m.key) and sl.desired_location(m.key) == "box":
                if len(party) > 1 and self.view.to_box(i):
                    self.forced.add(m.key)
                    self.notify(f"Your partner put {m.nick or 'a Pokémon'}'s partner in the PC, so it went to "
                                f"yours too.")
                    return
                self.tell_once(f"follow:{m.key}", f"Your partner boxed {m.nick or 'a Pokémon'}'s partner - please "
                                                  f"put it in the PC too (your box is full or it's your last one).")
        for j, m in enumerate(box):
            if sl.usable(m.key) and sl.partner_moved_last(m.key) and sl.desired_location(m.key) == "party":
                if len(party) < 6 and self.view.to_party(j):
                    self.forced.add(m.key)
                    self.notify(f"Your partner took {m.nick or 'a Pokémon'}'s partner out of the PC, so it joined "
                                f"your party too.")
                    return
                self.tell_once(f"wd:{m.key}", f"Your partner withdrew {m.nick or 'a Pokémon'}'s partner - "
                                              f"make room in your party for it.")

    def _any_usable(self) -> bool:
        return any(self.sl.usable(k) for k in self.sl.mine()["keys"])

    def _full_scan(self):
        """Mons we caught that are nowhere anymore (released in the PC, lost by reloading an old save)."""
        if not self.view.boxes_ok():
            return
        everywhere = self.view.all_keys()
        if everywhere is None:
            return
        for key in list(self.sl.mine()["keys"]):
            if key not in everywhere and self.sl.usable(key):
                self.send({"t": "dead", "key": key, "why": "released or lost"})
                self.notify("A Pokémon from this run is gone (released, or the game was reloaded) - it counts "
                            "as dead.", "bad")
