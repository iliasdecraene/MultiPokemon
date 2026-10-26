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
    if party:
        pk.hp = hp
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
        if not self.party_ok or not all(platinum.record_checksum_ok(r) for r in self.party):
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
        rec.hp = 20
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


def test_shared_games_needs_partner_challenge_games_for_platinum(plat_helpers):
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
                                "challenge_games": ["yellow", "crystal", "platinum"]}


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
