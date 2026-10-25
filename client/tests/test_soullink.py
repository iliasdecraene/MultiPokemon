import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink.soullink import MonInfo, Referee, SoulLink, family  # noqa: E402


class FakeView:
    """A tiny scripted game: party/box lists, a battle, the current area."""

    def __init__(self):
        self.party_mons = []
        self.box_mons = []
        self.other_boxes = []
        self.cur_area = "ROUTE_1"
        self.in_battle = None
        self.slot = None
        self.balls = False
        self.hall = False
        self.badge_bits = 0
        self.unreadable = False
        self.box_full = False

    def ready(self):
        return True

    def safe(self):
        return self.in_battle is None

    def battle(self):
        return self.in_battle

    def battle_slot(self):
        return self.slot

    def has_balls(self):
        return self.balls

    def area(self):
        return self.cur_area

    def area_name(self, a):
        return a.replace("_", " ").title()

    def party(self):
        return None if self.unreadable else list(self.party_mons)

    def box(self):
        return list(self.box_mons)

    def boxes_ok(self):
        return True

    def all_keys(self):
        return {m.key for m in self.party_mons + self.box_mons + self.other_boxes}

    def to_box(self, i):
        if self.box_full or len(self.party_mons) <= 1:
            return False
        self.box_mons.append(self.party_mons.pop(i))
        return True

    def to_party(self, j):
        if len(self.party_mons) >= 6:
            return False
        self.party_mons.append(self.box_mons.pop(j))
        return True

    def hof(self):
        return self.hall

    def badges(self):
        return self.badge_bits


class Player:
    """One side: SoulLink + Referee + FakeView, wired to a shared fake server ledger."""

    def __init__(self, world, uid, other, rules=None):
        self.uid = uid
        self.view = FakeView()
        self.sl = SoulLink(uid, other, rules)
        self.msgs = []
        self.ref = Referee(self.sl, self.view, lambda ev: world.post(uid, ev),
                           lambda text, kind="info": self.msgs.append(text))
        self.ref.fresh_load = False

    def run(self, frames=Referee.SAFE_EVERY * 3 + Referee.END_FRAMES):
        for _ in range(frames):
            self.ref.step()


class World:
    def __init__(self, rules=None):
        self.ledger = []
        self.a = Player(self, "A", "B", rules)
        self.b = Player(self, "B", "A", rules)

    def post(self, uid, ev):
        ev = {**ev, "p": uid, "seq": len(self.ledger) + 1}
        self.ledger.append(ev)
        for p in (self.a, self.b):
            p.sl.add(ev)

    def events(self, t=None, p=None):
        return [e for e in self.ledger if (t is None or e["t"] == t) and (p is None or e["p"] == p)]

    def run(self, n=1):
        for _ in range(n):
            self.a.run()
            self.b.run()


def mon(key, dex, hp=20, nick=""):
    return MonInfo(key, dex, hp, False, nick or f"M{key}")


def wild_catch(p, key, dex, area="ROUTE_1", catch=True, shiny=False):
    p.view.cur_area = area
    p.view.in_battle = {"kind": "wild", "dex": dex, "shiny": shiny, "safari": False}
    p.run(3)
    p.view.in_battle = None
    if catch:
        (p.view.party_mons if len(p.view.party_mons) < 6 else p.view.box_mons).append(mon(key, dex))
    p.run()


def started(world):
    """Both players got their starters (gifts in the lab) and Poké Balls."""
    for p, key, dex in ((world.a, "a0", 25), (world.b, "b0", 25)):
        p.view.cur_area = "PALLET_TOWN"
        p.view.party_mons.append(mon(key, dex))
        p.run()
        p.view.balls = True
        p.run()
    world.run()


def test_family_table():
    assert family(19) == family(20) and family(172) == family(25) == family(26)
    assert family(237) == family(106) and family(169) == family(41) and family(16) != family(19)


def test_starters_are_gifts_and_linked():
    w = World()
    started(w)
    gifts = w.events("enc")
    assert [(e["p"], e["area"], e["gift"]) for e in gifts] == [("A", "PALLET_TOWN", True), ("B", "PALLET_TOWN", True)]
    assert w.a.sl.partner_of("a0") == "b0" and w.b.sl.partner_of("b0") == "a0"
    assert len(w.events("balls")) == 2


def test_first_encounter_links_and_second_is_illegal():
    w = World()
    started(w)
    wild_catch(w.a, "a1", 16)
    wild_catch(w.b, "b1", 19)
    assert w.a.sl.status("a1") == "linked" and w.a.sl.partner_of("a1") == "b1"
    wild_catch(w.a, "a2", 21)                          # second encounter on Route 1
    w.run(2)
    assert ("a2", "not first encounter") in [(e["key"], e["why"]) for e in w.events("dead", "A")]
    assert all(m.key != "a2" for m in w.a.view.party_mons) and any(m.key == "a2" for m in w.a.view.box_mons)


def test_faint_kills_both_partners_and_they_get_boxed():
    w = World()
    started(w)
    wild_catch(w.a, "a1", 16)
    wild_catch(w.b, "b1", 19)
    # A's Pidgey (party slot 1) faints in a trainer battle
    w.a.view.in_battle = {"kind": "trainer", "dex": 0, "shiny": False, "safari": False}
    w.a.run(2)
    w.a.view.slot = (1, 0)
    w.a.ref.step()
    assert not w.events("dead"), "one frame of HP 0 is not a faint yet"
    w.a.run(2)
    assert [(e["p"], e["key"]) for e in w.events("dead")] == [("A", "a1")]
    w.a.view.slot = None
    w.a.view.in_battle = None
    w.a.view.party_mons[1] = mon("a1", 16, hp=0)
    w.run(3)
    assert {(e["p"], e["key"], e["why"]) for e in w.events("dead")} == {("A", "a1", "fainted"),
                                                                        ("B", "b1", "partner fainted")}
    assert [m.key for m in w.a.view.party_mons] == ["a0"] and [m.key for m in w.b.view.party_mons] == ["b0"]
    # withdrawing a dead mon puts it straight back
    w.b.view.party_mons.append(w.b.view.box_mons.pop())
    w.run(2)
    assert [m.key for m in w.b.view.party_mons] == ["b0"]


def test_missed_encounter_makes_partner_catch_unusable():
    w = World()
    started(w)
    wild_catch(w.a, "a1", 16, area="ROUTE_2")
    wild_catch(w.b, "b1", 19, area="ROUTE_2", catch=False)
    w.run(2)
    assert [e["status"] for e in w.events("enc", "B") if e["area"] == "ROUTE_2"] == ["open", "failed"]
    assert w.a.sl.status("a1") == "dead" and w.a.sl.mine()["dead"]["a1"] == "partner missed"
    assert all(m.key != "a1" for m in w.a.view.party_mons)


def test_dupes_and_shiny_clause():
    w = World({"dupes": True, "shiny": True})
    started(w)
    wild_catch(w.a, "a1", 19, area="ROUTE_1")         # Rattata
    w.a.view.cur_area = "ROUTE_22"
    w.a.view.in_battle = {"kind": "wild", "dex": 20, "shiny": False, "safari": False}   # Raticate: dupe
    w.a.run(2)
    w.a.view.in_battle = None
    w.a.run()
    assert [e["status"] for e in w.events("enc", "A") if e["area"] == "ROUTE_22"] == ["skipped"]
    assert not w.a.sl.area_used("ROUTE_22")
    wild_catch(w.a, "a2", 56, area="ROUTE_22")        # Mankey: counts
    assert w.a.sl.mine()["enc"]["ROUTE_22"]["key"] == "a2"
    wild_catch(w.a, "a3", 21, area="ROUTE_22", shiny=True)   # shiny Spearow after the area was used
    assert w.a.sl.usable("a3") and w.a.sl.status("a3") == "waiting"
    assert any(e["area"].startswith("ROUTE_22*") and e.get("shiny") for e in w.events("enc", "A"))


def test_no_balls_no_rules_and_no_death_in_rival_fight():
    w = World()
    p = w.a
    p.view.cur_area = "PALLET_TOWN"
    p.view.party_mons.append(mon("a0", 25))
    p.run()
    p.view.in_battle = {"kind": "trainer", "dex": 0, "shiny": False, "safari": False}
    p.run()
    p.view.slot = (0, 0)
    p.run()
    p.view.in_battle, p.view.slot = None, None
    p.view.party_mons[0] = mon("a0", 25, hp=0)
    p.run()
    assert not w.events("dead") and not w.events("blackout")
    # wild battles before Poké Balls don't use up the area
    p.view.party_mons[0] = mon("a0", 25)
    p.view.cur_area = "ROUTE_1"
    p.view.in_battle = {"kind": "wild", "dex": 16, "shiny": False, "safari": False}
    p.run()
    p.view.in_battle = None
    p.run()
    assert not p.sl.area_used("ROUTE_1")


def test_pc_rule_propagates_both_ways():
    w = World()
    started(w)
    wild_catch(w.a, "a1", 16)
    wild_catch(w.b, "b1", 19)
    w.run()
    # A deposits Pidgey -> B's Rattata goes to B's PC
    w.a.view.box_mons.append(w.a.view.party_mons.pop(1))
    w.run(3)
    assert [e["key"] for e in w.events("loc")] == ["a1"]
    assert [m.key for m in w.b.view.box_mons] == ["b1"] and [m.key for m in w.b.view.party_mons] == ["b0"]
    assert not w.events("loc", "B"), "a forced move must not be reported as the player's own move"
    # B withdraws Rattata -> A's Pidgey comes back out
    w.b.view.party_mons.append(w.b.view.box_mons.pop())
    w.run(3)
    assert [m.key for m in w.a.view.party_mons] == ["a0", "a1"]
    assert [(e["p"], e["where"]) for e in w.events("loc")] == [("A", "box"), ("B", "party")]


def test_blackout_fails_and_last_mon_rules():
    w = World()
    started(w)
    w.a.view.in_battle = {"kind": "trainer", "dex": 0, "shiny": False, "safari": False}
    w.a.run()
    w.a.view.slot = (0, 0)
    w.a.run()
    w.a.view.in_battle, w.a.view.slot = None, None
    w.a.view.party_mons[0] = mon("a0", 25, hp=0)
    w.a.run()
    assert len(w.events("blackout", "A")) == 1 and w.a.ref.ended


def test_last_partner_death_with_spare_in_box_swaps_in():
    w = World()
    started(w)
    wild_catch(w.b, "b1", 19)
    wild_catch(w.a, "a1", 16)
    w.a.view.box_mons.append(w.a.view.party_mons.pop(1))       # A keeps Pidgey in the PC
    w.b.view.box_mons.append(w.b.view.party_mons.pop(1))
    w.run(2)
    w.post("B", {"t": "dead", "key": "b0", "why": "fainted"})  # B's starter died -> A's starter dies too
    w.run(3)
    assert [m.key for m in w.a.view.party_mons] == ["a1"] and "a0" in [m.key for m in w.a.view.box_mons]


def test_rollback_detection_and_open_encounter_after_reload():
    w = World()
    started(w)
    wild_catch(w.a, "a1", 16)
    # A's game reloads an older save: Pidgey is gone, and an encounter was left open mid-battle
    w.post("A", {"t": "enc", "area": "ROUTE_2", "dex": 10, "status": "open"})
    w.a.view.party_mons = [m for m in w.a.view.party_mons if m.key != "a1"]
    w.a.ref.fresh_load = True
    w.a.ref.scan_due = True
    w.run(2)
    assert ("a1", "released or lost") in [(e["key"], e["why"]) for e in w.events("dead", "A")]
    assert [e["status"] for e in w.events("enc", "A") if e["area"] == "ROUTE_2"] == ["open", "failed"]


def test_npc_trade_moves_the_link():
    w = World()
    started(w)
    wild_catch(w.a, "a1", 16)
    wild_catch(w.b, "b1", 19)
    w.a.view.party_mons[1] = mon("t1", 122)                    # traded Pidgey for Mr. Mime
    w.run(2)
    assert w.events("swap") and w.a.sl.partner_of("t1") == "b1" and w.a.sl.usable("t1")
    assert not w.events("dead")


def test_hall_of_fame_and_badges_progress():
    w = World()
    started(w)
    got = []
    w.a.ref.progress = got.append
    w.a.view.badge_bits = 3
    w.a.view.hall = True
    w.run(2)
    assert len(w.events("hof", "A")) == 1 and got[-1] == 3


def test_pending_events_are_not_resent():
    sl = SoulLink("A", "B")
    sl.local({"t": "dead", "key": "k1", "why": "fainted"})
    assert sl.status("k1") == "dead"
    sl.add({"t": "dead", "key": "k1", "why": "fainted", "p": "A", "seq": 1})
    assert not sl.pending and sl.status("k1") == "dead"
