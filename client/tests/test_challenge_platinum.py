"""Soul Link in Pokémon Platinum without the ROM: ChallengeView's Platinum branch (Gen-4 records read safely),
the referee driving it, and the challenge client's game choice (old clients never get Platinum)."""
import os
import struct
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import challenge as chalmod  # noqa: E402
from yellowlink import pk4, platinum  # noqa: E402
from yellowlink.challenge import ChallengeMixin  # noqa: E402
from yellowlink.challenge_view import ChallengeView  # noqa: E402
from yellowlink.ndsrom import g4_encode  # noqa: E402
from yellowlink.platinum_pc import ALL_SLOTS, BOX_MON_SIZE  # noqa: E402
from yellowlink.soullink import Referee, SoulLink, family  # noqa: E402


def mk(species, pid, tid=33356, sid=2998, nick="", hp=20, egg=False, party=True):
    """An encrypted Gen-4 record (236-byte party or 136-byte box)."""
    pk = pk4.PK4(bytes(pk4.PARTY_SIZE if party else pk4.BOX_SIZE))
    pk.pid, pk.species, pk.tid, pk.sid = pid, species, tid, sid
    pk.nickname_codes = g4_encode(nick or "MON", 11)
    if egg:
        pk.is_egg = True
    if party:                       # a party section the game would accept (platinum.party_section_ok)
        pk.party_level, pk.stats, pk.hp = 5, [30, 10, 10, 10, 10, 10], hp
    return pk.encrypted()


def in_place(rec: bytes) -> bytes:
    """What the game leaves in RAM mid-frame (BoxPokemon_EnterDecryptionContext: XOR only, flag set)."""
    flags, chk = struct.unpack_from("<HH", rec, 4)
    return rec[:4] + struct.pack("<H", flags | pk4.FLAG_BOX_DECRYPTED) + rec[6:8] + pk4._crypt(rec[8:136], chk) \
        + rec[136:]


class Bus:
    def __init__(self):
        self.mem = bytearray(ALL_SLOTS * BOX_MON_SIZE)
        for i in range(ALL_SLOTS):
            self.mem[i * BOX_MON_SIZE:(i + 1) * BOX_MON_SIZE] = pk4.EMPTY_BOX

    def read(self, a, n):
        return bytes(self.mem[a - 0x1000:a - 0x1000 + n])

    def put(self, box, slot, rec):
        o = (box * 30 + slot) * BOX_MON_SIZE
        self.mem[o:o + BOX_MON_SIZE] = rec[:BOX_MON_SIZE]


class FakePM:
    """PlatinumMemory with the Soul Link helpers (platinum.py), scripted."""

    def __init__(self):
        self.m = Bus()
        self.party = []                 # encrypted 236-byte records
        self.party_ok = True
        self.box = []                   # current_box_records(): encrypted 136-byte records
        self.daycare = []
        self.battle = None
        self.slot = None
        self.balls = False
        self.mask = 0
        self.hof_flag = False
        self.map = 0
        self.moved = []
        self.pc = False

    # PlatinumMemory basics
    def in_game(self):
        return True

    def is_safe(self):
        return self.battle is None

    def map_id(self):
        return self.map

    def pc_boxes_addr(self):
        return 0x1000 - 4                 # Gen4PC reads at +4 (BOX_MONS)

    def pc_open(self):
        return self.pc

    def party_checked(self):
        if not self.party_ok or not all(platinum.record_checksum_ok(r) and platinum.party_section_ok(r)
                                        for r in self.party):
            return None
        return list(self.party)

    # Soul Link helpers (same names as crystal.py)
    def battle_info(self):
        return self.battle

    def battle_party_slot(self):
        return self.slot

    def has_poke_balls(self):
        return self.balls

    def current_box_records(self):
        return list(self.box)

    def daycare_records(self):
        return list(self.daycare)

    def move_party_to_box(self, slot):
        if len(self.party) <= 1:
            return False
        self.box.append(self.party.pop(slot)[:136])
        self.moved.append(("box", slot))
        return True

    def move_box_to_party(self, index):
        if len(self.party) >= 6:
            return False
        self.moved.append(("party", index))
        rec = pk4.PK4(pk4.decrypt(self.box.pop(index)) + bytes(100))
        rec.party_level, rec.stats, rec.hp = 5, [30, 10, 10, 10, 10, 10], 20
        self.party.append(rec.encrypted())
        return True

    def badges(self):
        return bin(self.mask).count("1")

    def badge_mask(self):
        return self.mask

    def entered_hall_of_fame(self):
        return self.hof_flag

    def in_hall_of_fame(self):
        return False


def view_for(pm):
    app = types.SimpleNamespace(game=pm, G=platinum, gen2=False, ds=True)
    return ChallengeView(app)


# --- ChallengeView, Platinum branch -----------------------------------------------------------------------------
def test_party_reads_gen4_records():
    pm = FakePM()
    pm.party = [mk(387, 0x11111111, nick="TURTWIG", hp=21), mk(396, 0x22222222, hp=0),
                mk(172, 0x33333333, egg=True, hp=0)]
    v = view_for(pm)
    p = v.party()
    assert [(m.dex, m.hp, m.egg) for m in p] == [(387, 21, False), (396, 0, False), (172, 0, True)]
    assert p[0].nick == "TURTWIG" and p[0].key == pk4.mon_key(pm.party[0]) == "11111111824c0bb6"
    pm.party_ok = False
    assert v.party() is None
    pm.party_ok = True
    pm.party[1] = in_place(pm.party[1])          # the game works on that record right now: not readable yet
    assert v.party() is None


def test_box_skips_unreadable_and_maps_indices():
    pm = FakePM()
    good1, good2 = mk(399, 0xAAAA0001, party=False), mk(401, 0xAAAA0002, party=False)
    broken = bytearray(mk(10, 0xAAAA0003, party=False))
    broken[40] ^= 0xFF                                  # a Bad Egg (checksum doesn't match)
    pm.box = [good1, bytes(broken), in_place(good2), pk4.EMPTY_BOX]
    v = view_for(pm)
    b = v.box()
    assert [(m.dex, m.hp) for m in b] == [(399, -1), (401, -1)]     # in-place decrypted record read as the mon
    assert b[1].key == pk4.mon_key(good2)
    pm.party = [mk(387, 1)]
    assert v.to_party(1) and pm.moved == [("party", 3 - 1)]          # box() index 1 = record 2
    assert not v.to_party(5)


def test_all_keys_party_boxes_daycare():
    pm = FakePM()
    pm.party = [mk(387, 1)]
    pm.m.put(0, 0, mk(399, 2, party=False))
    pm.m.put(17, 29, in_place(mk(396, 3, party=False)))     # last slot of box 18, mid-update -> still found
    pm.daycare = [mk(25, 4, party=False)]
    v = view_for(pm)
    want = {pk4.mon_key(mk(387, 1)), pk4.mon_key(mk(399, 2)), pk4.mon_key(mk(396, 3)), pk4.mon_key(mk(25, 4))}
    assert v.all_keys() == want and v.boxes_ok()
    bad = bytearray(mk(16, 5, party=False))
    bad[50] ^= 1
    pm.m.put(3, 3, bytes(bad))
    assert v.all_keys() is None                     # can't tell which mon that is: no "released" verdict now
    pm.m.put(3, 3, pk4.EMPTY_BOX)
    pm.party_ok = False
    assert v.all_keys() is None


def test_progress_hof_hold_and_areas():
    pm = FakePM()
    v = view_for(pm)
    pm.mask = 0b101
    assert v.badges() == 0b101 and not v.hof() and not v.hold()
    pm.hof_flag = True
    assert v.hof()
    pm.map = 411                                     # Twinleaf Town (MAP_LOCATION)
    assert v.area_name(v.area()) == platinum.area_name(platinum.area_of(411))
    assert v.area_name(v.area() + "*2") == v.area_name(v.area())


def test_referee_on_platinum_wild_catch_faint_and_pc_rule():
    """The real referee driving the Platinum view: encounter in the right area, catch linked, faint kills both."""
    sent = []
    pm = FakePM()
    v = view_for(pm)
    sl = SoulLink("me", "you", {"dupes": True, "shiny": True})
    ref = Referee(sl, v, lambda ev: (sent.append(ev), sl.add({**ev, "p": "me", "seq": len(sent)})),
                  lambda *a: None)
    ref.fresh_load = False
    pm.map = 411
    pm.party = [mk(387, 0x100)]                     # starter: a gift in Twinleaf Town
    for _ in range(Referee.SAFE_EVERY * 2):
        ref.step()
    starter = pk4.mon_key(pm.party[0])
    gift = [e for e in sent if e["t"] == "enc"]
    assert gift and gift[0]["gift"] and gift[0]["key"] == starter and gift[0]["dex"] == 387
    pm.balls = True
    pm.map = 0                                       # somewhere else: a route
    route = platinum.area_of(0)
    pm.battle = {"kind": "wild", "dex": 396, "shiny": False, "safari": False}
    for _ in range(3):
        ref.step()
    assert sent[-1] == {"t": "enc", "area": route, "dex": 396, "status": "open", "cid": sent[-1]["cid"]}
    pm.battle = None
    pm.party.append(mk(396, 0x200))                  # caught
    for _ in range(Referee.END_FRAMES + 2):
        ref.step()
    caught = [e for e in sent if e["t"] == "enc" and e.get("status") == "caught" and not e.get("gift")]
    assert caught and caught[0]["key"] == pk4.mon_key(mk(396, 0x200)) and caught[0]["area"] == route
    # partner caught in the same area -> linked; then the partner's mon faints -> ours dies and is boxed
    sl.add({"t": "enc", "area": route, "dex": 16, "status": "caught", "key": "p1", "p": "you", "seq": 100})
    assert sl.partner_of(caught[0]["key"]) == "p1"
    sl.add({"t": "dead", "key": "p1", "p": "you", "seq": 101})
    for _ in range(Referee.SAFE_EVERY * 3):
        ref.step()
    assert sl.status(caught[0]["key"]) == "dead"
    assert ("box", 1) in pm.moved and len(pm.party) == 1


def test_family_table_covers_gen4():
    assert len(__import__("yellowlink.soullink", fromlist=["FAMILY"]).FAMILY) == 494
    assert family(462) == family(81)            # Magnezone / Magnemite
    assert family(446) == family(143)           # Munchlax / Snorlax
    assert family(470) == family(471) == family(133)
    assert family(389) == family(387) and family(396) == family(398)
    assert family(25) == family(172)            # unchanged Gen-1/2 families


# --- the challenge client (no game): which games are offered ----------------------------------------------------
class FakeNet:
    def __init__(self):
        self.sent = []
        self.status = "online"

    def send(self, m):
        self.sent.append(m)
        return True


class FakeApp(ChallengeMixin):
    def __init__(self, games, roster):
        self.cfg = {"uid": "me", "name": "Ilias"}
        self.lib = types.SimpleNamespace(games={g: f"/roms/{g}" for g in games}, rom_path=lambda g: f"/roms/{g}")
        self.roster = roster
        self.net = FakeNet()
        self.toasts = []
        self.chal = None
        self.trade = self.battle = self.live = self.prompt = None

    def _game_supported(self, g):
        return g in self.lib.games

    def toast(self, s, *a):
        self.toasts.append(s)


@pytest.fixture
def plat_helpers(monkeypatch):
    """platinum.PlatinumMemory with the referee's helpers (whatever platinum.py has right now)."""
    for name in chalmod.REFEREE_HELPERS:
        if not hasattr(platinum.PlatinumMemory, name):
            monkeypatch.setattr(platinum.PlatinumMemory, name, lambda self: None, raising=False)


def test_shared_games_needs_partner_challenge_games_for_platinum(plat_helpers, monkeypatch):
    monkeypatch.setenv("YL_PT_ROM", "vanilla")          # plain Platinum on both sides (Complete Sinnoh: tests below)
    old = {"uid": "old", "games": ["crystal", "platinum", "yellow"]}             # a 0.7.2 client
    new = {"uid": "new", "games": ["platinum", "yellow"], "challenge_games": ["platinum", "yellow"]}
    unknown = {"uid": "unk"}                                                      # never sent `library`
    app = FakeApp(["yellow", "crystal", "platinum"], [old, new, unknown])
    assert app.challenge_games() == ["yellow", "crystal", "platinum"]
    assert app.shared_games("old") == ["yellow", "crystal"]
    assert app.shared_games("new") == ["yellow", "platinum"]
    assert app.shared_games("unk") == ["yellow", "crystal"]          # as before for Yellow/Crystal, never Platinum
    app.chal_on_connected()
    assert app.net.sent[-1] == {"type": "library", "games": ["crystal", "platinum", "yellow"],
                                "challenge_games": ["yellow", "crystal", "platinum"],
                                "challenge_rules": ["exp_share", "trade_evo"]}


def test_platinum_needs_the_referee_helpers(monkeypatch):
    app = FakeApp(["yellow", "platinum"], [])
    monkeypatch.delattr(platinum.PlatinumMemory, "battle_info", raising=False)
    assert app.challenge_games() == ["yellow"]
    for name in chalmod.REFEREE_HELPERS:
        monkeypatch.setattr(platinum.PlatinumMemory, name, lambda self: None, raising=False)
    assert app.challenge_games() == ["yellow", "platinum"]


def test_platinum_invite_without_platinum_is_declined():
    app = FakeApp(["yellow"], [])
    app.on_challenge_request({"request_id": "r1", "name": "Alex", "game": "platinum", "kind": "soul_link"})
    assert app.net.sent[-1] == {"type": "challenge_respond", "request_id": "r1", "accept": False}
    assert app.prompt is None and "Pokémon Platinum" in app.toasts[-1]


def test_dex_name_uses_the_platinum_rom_names():
    app = FakeApp(["platinum"], [])
    app.ds, app.gen2 = True, False
    app.plat_data = types.SimpleNamespace(species_name=lambda d: {387: "TURTWIG", 493: "ARCEUS"}[d])
    assert app.dex_name(387) == "Turtwig" and app.dex_name(493) == "Arceus" and app.dex_name(0) == "#0"


@pytest.mark.parametrize("pc_now", [True, False])
def test_platinum_pc_poll_tells_the_referee(pc_now):
    app = FakeApp(["platinum"], [])
    app.ds = True
    app.game = types.SimpleNamespace(pc_open=lambda: pc_now)
    calls = []
    app.referee = types.SimpleNamespace(pc_open=not pc_now, on_pc_open=lambda: calls.append("open"),
                                        on_pc_close=lambda: calls.append("close"))
    app._chal_pc_poll()
    assert calls == (["open"] if pc_now else ["close"])


def test_ds_failing_inside_a_challenge_goes_back_to_the_main_game(tmp_path, monkeypatch):
    """Platinum can't start for the run: drop active_challenge (no crash loop into the run), then the usual fallback."""
    from yellowlink import app as appmod, nds_core
    from yellowlink.config import Config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = Config()
    cfg.update(name="Ilias", server_url="ws://1.2.3.4:8765", rom_path=__file__, games={"platinum": __file__},
               current_game="platinum")
    starts = []

    class FakeApp:
        def __init__(self, c, diag=None):
            starts.append(c.challenge)
            if c.challenge:
                raise nds_core.DSUnavailable("no DLL")
            self.enter_challenge = {"id": "run1", "game": "platinum"} if len(starts) == 1 else None
            self.pending_share = self.switch_to = self.travel_to = self.restart_into = None
            self.leave_challenge = False

        def run(self):
            pass

    seen = []
    monkeypatch.setattr(appmod, "App", FakeApp)
    monkeypatch.setattr(appmod, "_ds_fallback", lambda c, d, e: (seen.append(c.challenge), False)[1])
    diag = types.SimpleNamespace(errors=0)
    appmod._main([], cfg, diag)
    assert starts == [None, {"id": "run1", "game": "platinum"}]
    assert seen == [None] and "active_challenge" not in Config()


# --- review fixes: doubles, spills, partner battles, poison, unreadable slots -------------------------------------
class Ledger:
    """Both players' SoulLink over one shared ledger; our events come from a Referee on a FakePM."""

    def __init__(self, rules=None):
        self.sent = []
        self.sl = SoulLink("me", "you", {"dupes": True, "shiny": True, **(rules or {})})
        self.seq = 0

    def post(self, ev, p="me"):
        self.seq += 1
        ev = {**ev, "p": p, "seq": self.seq}
        if p == "me":
            self.sent.append(ev)
        self.sl.add(ev)

    def referee(self, pm):
        ref = Referee(self.sl, view_for(pm), lambda ev: self.post(ev), lambda *a: None)
        ref.fresh_load = False
        return ref


def settle(ref, n=Referee.SAFE_EVERY * 3):
    for _ in range(n):
        ref.step()


def started(pm, led, ref, starter=mk(387, 0x100)):
    """Starter as a gift (linked with the partner's), balls in the bag, standing on a route."""
    pm.map, pm.party = 411, [starter]
    settle(ref)
    led.post({"t": "enc", "area": platinum.area_of(411), "dex": 390, "status": "caught", "key": "pstart", "gift": True},
             p="you")
    pm.balls, pm.map = True, 0
    settle(ref)
    return pk4.mon_key(starter)


def battle(ref, pm, info, frames=3):
    pm.battle = info
    settle(ref, frames)


def end_battle(ref, pm):
    pm.battle = None
    settle(ref, Referee.END_FRAMES + 2)


def wild2(k1, d1, k2, d2, **kw):
    return {"kind": "wild", "dex": d1, "shiny": False, "safari": False, "contest": False, "key": k1,
            "keys": [k1, k2], "dexes": [d1, d2], **kw}


def test_double_battle_catch_of_the_second_mon_counts():
    pm, led = FakePM(), Ledger()
    ref = led.referee(pm)
    started(pm, led, ref)
    route = platinum.area_of(0)
    first, second = mk(396, 0x201), mk(399, 0x202)
    battle(ref, pm, wild2(pk4.mon_key(first), 396, pk4.mon_key(second), 399))
    assert led.sent[-1]["status"] == "open" and led.sent[-1]["area"] == route
    pm.party.append(second)                          # the player caught the SECOND wild mon
    end_battle(ref, pm)
    settle(ref)
    enc = led.sl.mine()["enc"][route]
    assert enc["status"] == "caught" and enc["key"] == pk4.mon_key(second) and enc["dex"] == 399
    assert not [e for e in led.sent if e["t"] == "dead"]                  # not an "extra gift"
    assert led.sl.usable(pk4.mon_key(second))


def test_double_battle_catching_the_dupe_is_a_missed_encounter():
    pm, led = FakePM(), Ledger()
    ref = led.referee(pm)
    started(pm, led, ref, starter=mk(396, 0x100))   # Starly family already caught
    route = platinum.area_of(0)
    dupe, other = mk(397, 0x201), mk(399, 0x202)
    battle(ref, pm, wild2(pk4.mon_key(dupe), 397, pk4.mon_key(other), 399))
    assert led.sent[-1] == {**led.sent[-1], "status": "open", "dex": 399}      # the encounter = the non-dupe
    pm.party.append(dupe)
    end_battle(ref, pm)
    assert led.sl.mine()["enc"][route]["status"] == "failed"
    assert led.sl.mine()["dead"].get(pk4.mon_key(dupe)) == "not first encounter"


@pytest.mark.parametrize("with_keys", [True, False])
def test_catch_spilling_into_another_box_is_not_confused_with_an_older_mon(with_keys):
    """Full party + full current box: the catch lands in the next box, next to an older mon of the same species."""
    pm, led = FakePM(), Ledger({"dupes": False})
    ref = led.referee(pm)
    started(pm, led, ref)
    pm.party += [mk(16 + i, 0x300 + i) for i in range(5)]
    pm.box = [mk(10, 0x400 + i, party=False) for i in range(30)]
    old = mk(399, 0x500, party=False)
    pm.m.put(1, 0, old)                              # box 2 already holds an older Bidoof
    for i, r in enumerate(pm.box):
        pm.m.put(0, i, r)
    new = mk(399, 0x501, party=False)
    info = {"kind": "wild", "dex": 399, "shiny": False, "safari": False, "contest": False}
    if with_keys:
        info.update(key=pk4.mon_key(new), keys=[pk4.mon_key(new)], dexes=[399])
    battle(ref, pm, info)
    pm.m.put(1, 1, new)
    pm.box = [old, new]                              # box 2 is the current box now
    end_battle(ref, pm)
    enc = led.sl.mine()["enc"][platinum.area_of(0)]
    assert enc["status"] == "caught" and enc["key"] == pk4.mon_key(new)


def _partner_battle(result, spare=True):
    """A partner (tag) battle in which all of the player's mons faint; the battle ends with `result`."""
    pm, led = FakePM(), Ledger()
    ref = led.referee(pm)
    a = started(pm, led, ref)
    pm.party.append(mk(399, 0x600))
    b = pk4.mon_key(pm.party[1])
    led.post({"t": "enc", "area": "test_b", "dex": 399, "status": "caught", "key": b, "gift": True})
    led.post({"t": "enc", "area": "test_b", "dex": 16, "status": "caught", "key": "pb", "gift": True}, p="you")
    if spare:
        c = mk(25, 0x700, party=False)
        pm.box = [c]
        pm.m.put(0, 0, c)
        led.post({"t": "enc", "area": "test_c", "dex": 25, "status": "caught", "key": pk4.mon_key(c), "gift": True})
        led.post({"t": "enc", "area": "test_c", "dex": 25, "status": "caught", "key": "pc", "gift": True}, p="you")
    settle(ref)
    pm.battle = {"kind": "trainer", "dex": 0, "shiny": False, "safari": False, "contest": False, "result": 0}
    for slot in (0, 1):
        pm.slot = (slot, 0)
        settle(ref, 3)
    pm.battle = dict(pm.battle, result=result)       # the game's verdict, on the battle's last frames
    settle(ref, 3)
    pm.slot = None
    pm.party = [mk(387, 0x100, hp=30), mk(399, 0x600, hp=30)]       # healed after the battle
    end_battle(ref, pm)
    return led, ref, a, b


def test_partner_battle_all_fainted_but_won_is_no_blackout():
    led, ref, a, b = _partner_battle(1)                              # BATTLE_RESULT_WIN
    assert led.sl.status(a) == "dead" and led.sl.status(b) == "dead"  # the deaths count
    assert not [e for e in led.sent if e["t"] == "blackout"] and not ref.ended
    settle(ref)                                                      # the run goes on with the spare from the PC
    assert not [e for e in led.sent if e["t"] == "blackout"]


@pytest.mark.parametrize("result", [2, 3])                          # LOSE, DRAW: the game whites out
def test_lost_battle_is_a_blackout(result):
    led, ref, a, b = _partner_battle(result)
    assert [e for e in led.sent if e["t"] == "blackout"] and ref.ended


def test_faint_in_battle_kills_both_partners():
    pm, led = FakePM(), Ledger()
    ref = led.referee(pm)
    a = started(pm, led, ref)
    pm.party.append(mk(399, 0x600))
    battle(ref, pm, {"kind": "trainer", "dex": 0, "shiny": False, "safari": False, "contest": False}, 2)
    pm.slot = (0, 21)
    settle(ref, 2)
    pm.slot = (0, 0)
    settle(ref, 1)
    assert led.sl.status(a) != "dead"                # one frame at 0 HP isn't enough (switch in progress)
    settle(ref, 1)
    assert led.sl.mine()["dead"].get(a) == "fainted"
    theirs = SoulLink("you", "me", None, led.sl.ledger)
    assert {"t": "dead", "key": "pstart", "why": "partner fainted"} in theirs.needed_deaths()


def test_platinum_field_poison_leaves_one_hp():
    """pret Pokemon_DoPoisonDamage: HP never drops below 1 outside battle (then the poison is cured) - checked on
    the ROM too (dev/e2e/soullink_platinum.py). A poisoned mon at 1 HP is alive."""
    pm, led = FakePM(), Ledger()
    ref = led.referee(pm)
    a = started(pm, led, ref)
    pm.party = [mk(387, 0x100, hp=1)]
    settle(ref)
    assert led.sl.usable(a) and not [e for e in led.sent if e["t"] in ("dead", "blackout")]


def test_unreadable_day_care_skips_the_full_scan():
    pm = FakePM()
    pm.party = [mk(387, 1)]
    v = view_for(pm)
    assert v.all_keys() is not None
    pm.daycare = None                               # platinum.daycare_records(): a slot can't be read right now
    assert v.all_keys() is None


def test_a_bad_egg_is_logged_once(caplog):
    pm = FakePM()
    pm.party = [mk(387, 1)]
    bad = bytearray(mk(16, 5, party=False))
    bad[50] ^= 1
    pm.m.put(2, 7, bytes(bad))
    v = view_for(pm)
    with caplog.at_level("WARNING", logger="yellowlink.challenge"):
        for _ in range(6):
            assert v.all_keys() is None
    hits = [r for r in caplog.records if "box 3 slot 8" in r.getMessage()]
    assert len(hits) == 1


def test_battle_result_read_from_the_battle_data():
    pm = FakePM()
    pm.battle = {"kind": "wild", "dex": 399}
    pm.battle_dto = lambda: 0x3000
    pm.r32 = lambda a: 5 if a == 0x3000 + 0x14 else 0
    assert view_for(pm).battle()["result"] == 5


def test_forced_move_without_rom_data_is_logged_once(caplog):
    pm = FakePM()

    def boom(_i):
        raise RuntimeError("move_party_to_box needs PlatinumMemory(bus, rom)")
    pm.move_party_to_box = pm.move_box_to_party = boom
    v = view_for(pm)
    with caplog.at_level("ERROR", logger="yellowlink.challenge"):
        assert not v.to_box(0) and not v.to_box(1) and not v.to_party(0)
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


def test_game_tip_blames_an_old_server_or_an_old_client():
    from yellowlink import panels
    app = FakeApp(["platinum"], [])
    app.challenge_games = lambda: ["platinum"]
    form = {"uid": "x", "name": "Alex"}
    app.roster = [{"uid": "x", "games": ["platinum"]}]                          # no challenge_games at all
    assert "server needs updating" in panels._game_tip(app, form, "platinum")
    app.roster = [{"uid": "x", "games": ["platinum"], "challenge_games": None}]   # new server, old client
    assert "Alex needs to update" in panels._game_tip(app, form, "platinum")


def test_waiting_for_the_partner_keeps_ds_sound_paused():
    from yellowlink import app as appmod
    calls = []
    fake = types.SimpleNamespace(nds=types.SimpleNamespace(closed=False, pause_audio=calls.append),
                                 _ds_chal_paused=True)
    appmod.App._pause_ds_audio(fake, False)           # e.g. after the F3 window / a file picker
    assert calls == []
    fake._ds_chal_paused = False
    appmod.App._pause_ds_audio(fake, False)
    assert calls == [False]


def test_platinum_soul_link_needs_the_same_complete_sinnoh(plat_helpers, monkeypatch):
    """Complete Sinnoh (SPEC.md section 11): clients advertise `sinnoh_v<spec>` in challenge_rules; Platinum runs
    only between players with the same wild Pokémon (same version, or both plain Platinum)."""
    sha = "ab" * 20
    monkeypatch.setitem(platinum.VARIANTS, sha, platinum.Variant("sinnoh", sha, "Complete Sinnoh", 3))
    same = {"uid": "same", "games": ["platinum"], "challenge_games": ["platinum"],
            "challenge_rules": ["exp_share", "sinnoh_v3", "trade_evo"]}
    older = {"uid": "older", "games": ["platinum"], "challenge_games": ["platinum"],
             "challenge_rules": ["exp_share", "trade_evo"]}                        # 0.7.6: plain Platinum
    other = {"uid": "other", "games": ["platinum"], "challenge_games": ["platinum"],
             "challenge_rules": ["sinnoh_v4"]}
    app = FakeApp(["yellow", "platinum"], [same, older, other])
    app.complete_sinnoh, app.rom_sha1 = True, sha
    assert app._sinnoh_caps() == ["sinnoh_v3"]
    assert app.shared_games("same") == ["platinum"]
    assert app.shared_games("older") == [] and app.shared_games("other") == []
    app.chal_on_connected()
    assert app.net.sent[-1]["challenge_rules"] == ["exp_share", "trade_evo", "sinnoh_v3"]
    app.complete_sinnoh = False                                                   # plain Platinum (build failed)
    app.sinnoh_problem = "disk full"
    assert app._sinnoh_caps() == [] and app.shared_games("older") == ["platinum"]


def test_yellow_or_crystal_client_advertises_complete_sinnoh(plat_helpers, monkeypatch):
    """H1: a client playing Yellow/Crystal right now (ds False, no build in this process yet) still plays Complete
    Sinnoh when it starts Platinum, so it advertises the spec version this YellowLink builds."""
    from yellowlink import sinnoh_rom
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", None)
    monkeypatch.delenv("YL_PT_ROM", raising=False)
    spec = sinnoh_rom.load_spec()
    want = ["sinnoh_v%d" % spec.SPEC_VERSION]
    assert sinnoh_rom.expected_sha1(spec)                         # the shipped spec has a checked build
    partner = {"uid": "pt", "games": ["platinum", "yellow"], "challenge_games": ["platinum", "yellow"],
               "challenge_rules": ["exp_share", "trade_evo"] + want}
    app = FakeApp(["yellow", "platinum"], [partner])
    app.ds = False                                                # on Yellow right now
    assert app._sinnoh_caps() == want
    assert app.shared_games("pt") == ["yellow", "platinum"]
    app.chal_on_connected()
    assert app.net.sent[-1]["challenge_rules"] == ["exp_share", "trade_evo"] + want
    # dev: YL_PT_ROM=vanilla -> plain Platinum everywhere
    monkeypatch.setenv("YL_PT_ROM", "vanilla")
    assert app._sinnoh_caps() == []
    monkeypatch.delenv("YL_PT_ROM")
    # this process failed to build (e.g. on an earlier Platinum start): plain Platinum + say so to the others
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", "disk full")
    assert app._sinnoh_caps() == [] and app.shared_games("pt") == ["yellow"]
    app.chal_on_connected()
    assert app.net.sent[-1]["challenge_rules"] == ["exp_share", "trade_evo", chalmod.SINNOH_FAILED]


def test_game_tip_when_a_complete_sinnoh_build_failed(plat_helpers, monkeypatch):
    """L5: a failed build is not 'update YellowLink' - say whose build failed and to restart."""
    from yellowlink import panels, sinnoh_rom
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", None)
    monkeypatch.delenv("YL_PT_ROM", raising=False)
    v = "sinnoh_v%d" % sinnoh_rom.load_spec().SPEC_VERSION
    form = {"uid": "x", "name": "Alex"}
    failed = {"uid": "x", "games": ["platinum"], "challenge_games": ["platinum"],
              "challenge_rules": ["exp_share", chalmod.SINNOH_FAILED]}
    app = FakeApp(["platinum"], [failed])
    app.ds = False
    tip = panels._game_tip(app, form, "platinum")
    assert "Alex" in tip and "couldn't be prepared" in tip and "restart" in tip and "update" not in tip
    app.roster = [{"uid": "x", "games": ["platinum"], "challenge_games": ["platinum"], "challenge_rules": [v]}]
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", "disk full")
    tip = panels._game_tip(app, form, "platinum")
    assert tip.startswith("Your Complete Sinnoh") and "restart" in tip and "update" not in tip
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", None)
    app.roster = [{"uid": "x", "games": ["platinum"], "challenge_games": ["platinum"], "challenge_rules": ["sinnoh_v1"]}]
    assert "update YellowLink" in panels._game_tip(app, form, "platinum")
