"""Platinum PC codec (client/yellowlink/platinum_pc.py) + its use by PCSync, and the cross-generation record_for
rules in Yellow's / Crystal's codecs. No ROM needed (fake species data from test_pk4); the last tests use the real
Platinum ROM data when dev/states/platinum.nds exists."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from yellowlink import crystal, ndsrom, pcdb, pk4, platinum, platinum_pc  # noqa: E402
from yellowlink.platinum_pc import Gen4PC, fold, layout  # noqa: E402
from test_pk4 import G1ROM, G2ROM, ROM, gen1_rec, gen2_rec, native  # noqa: E402

b64 = pcdb._b64
PT_ROM = os.path.join(os.path.dirname(__file__), "..", "..", "dev", "states", "platinum.nds")
EMPTY = pk4.EMPTY_BOX


def mon(i, species=387):
    """A distinct valid Platinum box mon."""
    return native(species=species, pid=0x10000 + i * 7919, tid=i, sid=0, level=5)


def empty_slots():
    return [[EMPTY] * 30 for _ in range(18)]


# ---------------------------------------------------------------------------------------------------------
# fold / layout (pure)
# ---------------------------------------------------------------------------------------------------------
def test_layout_then_fold_is_identity():
    boxes = [[mon(b * 20 + s) for s in range(b % 21)] for b in range(14)]
    slots = layout(boxes)
    assert len(slots) == 18 and all(len(b) == 30 for b in slots)
    assert all(slots[b][s] == EMPTY for b in range(14) for s in range(20, 30))
    assert all(slots[b][s] == EMPTY for b in range(14, 18) for s in range(30))
    got, unplaced, harvested = fold(slots)
    assert got == boxes and unplaced == [] and harvested == 0


def test_fold_closes_holes_and_harvests_extra_slots():
    slots = empty_slots()
    slots[0][3], slots[0][7] = mon(1), mon(2)
    slots[0][25] = mon(3)                   # slot 26 of box 1: box 1 has room -> stays in box 1
    slots[16][0] = mon(4)                   # box 17 -> first box with room (box 1)
    slots[13][29] = mon(5)                  # slot 30 of box 14, but box 14 has room: stays there
    boxes, unplaced, harvested = fold(slots)
    assert boxes[0] == [mon(1), mon(2), mon(3), mon(4)] and boxes[13] == [mon(5)]
    assert unplaced == [] and harvested == 1
    assert all(len(b) == 0 for b in boxes[1:13])


def test_fold_box_with_more_than_20_overflows_into_next_free_box():
    slots = empty_slots()
    for s in range(30):
        slots[2][s] = mon(100 + s)
    boxes, unplaced, harvested = fold(slots)
    assert boxes[2] == [mon(100 + s) for s in range(20)]
    assert boxes[0] == [mon(100 + s) for s in range(20, 30)]
    assert harvested == 10 and not unplaced


def test_full_store_keeps_known_and_leaves_new_ones_in_the_game():
    known = [[mon(b * 20 + s) for s in range(20)] for b in range(14)]
    slots = layout(known)
    new = mon(999, species=25)
    slots[15][4] = new                                    # deposited into box 16
    boxes, unplaced, harvested = fold(slots, {r for b in known for r in b})
    assert boxes == known and unplaced == [new] and harvested == 0
    again = layout(boxes, unplaced)
    assert again[14][0] == new                            # kept in the game, in box 15
    assert sum(r != EMPTY for b in again for r in b) == 281


def test_full_store_new_mon_in_a_hole_bumps_itself_not_a_known_one():
    known = [[mon(b * 20 + s) for s in range(20)] for b in range(14)]
    slots = layout(known)
    # the player moved box 1's first mon to box 1 slot 21 and deposited a new one into slot 1
    new = mon(999, species=25)
    slots[0][20] = slots[0][0]
    slots[0][0] = new
    boxes, unplaced, _ = fold(slots, {r for b in known for r in b})
    assert unplaced == [new]
    assert sorted(r for b in boxes for r in b) == sorted(r for b in known for r in b)


def test_bad_checksum_slots_are_never_harvested_or_erased():
    bad = bytearray(mon(7))
    bad[50] ^= 0xFF
    bad = bytes(bad)
    assert platinum_pc.classify(bad) == "bad" and platinum_pc.classify(EMPTY) == "empty"
    slots = empty_slots()
    slots[0][0] = bad
    slots[0][1] = mon(8)
    boxes, unplaced, _ = fold(slots)
    assert boxes[0] == [mon(8)] and unplaced == [bad]
    assert bad in [r for b in layout(boxes, unplaced) for r in b]


# ---------------------------------------------------------------------------------------------------------
# Gen4PC on a fake DS memory
# ---------------------------------------------------------------------------------------------------------
RAM0 = 0x02000000
SAVE = 0x02200000


class FakeBus:
    def __init__(self):
        self.mem = bytearray(0x400000)

    def read(self, a, n):
        return bytes(self.mem[a - RAM0:a - RAM0 + n])

    def write(self, a, data):
        self.mem[a - RAM0:a - RAM0 + len(data)] = bytes(data)

    def r8(self, a):
        return self.mem[a - RAM0]

    def r16(self, a):
        return struct.unpack_from("<H", self.mem, a - RAM0)[0]

    def r32(self, a):
        return struct.unpack_from("<I", self.mem, a - RAM0)[0]

    def w32(self, a, v):
        struct.pack_into("<I", self.mem, a - RAM0, v)


class FakePM(platinum.PlatinumMemory):
    """PlatinumMemory over FakeBus: SaveData pointer real, game-state checks faked."""
    box_ui = False

    def in_game(self):
        return True

    def pc_open(self):
        return self.box_ui


def game(party=()):
    bus = FakeBus()
    bus.w32(platinum.SAVE_DATA_PTR, SAVE)
    pm = FakePM(bus)
    body = pm.save_body()
    base = body + platinum.PAGE_PC_BOXES
    bus.w32(base, 3)                                                      # current box
    bus.write(base + platinum.BOX_MONS, EMPTY * 540)
    for b in range(18):
        bus.write(base + platinum.BOX_NAMES + b * 40, struct.pack("<20H", *([0x123 + b] + [0xFFFF] * 19)))
        bus.write(base + platinum.BOX_WALLPAPERS + b, bytes([b % 16]))
    party_at = body + platinum.PAGE_PARTY
    bus.w32(party_at, 6)
    bus.w32(party_at + 4, len(party))
    for i in range(6):
        bus.write(party_at + 8 + i * 236, pk4.to_party(party[i], ROM) if i < len(party) else pk4.EMPTY_PARTY)
    return bus, pm, Gen4PC(pm, ROM)


def set_slot(pm, box, slot, rec):
    pm.m.write(pm.box_slot_addr(box, slot), rec)


def test_write_boxes_keeps_names_wallpapers_and_current_box():
    bus, pm, pc = game()
    base = pm.pc_boxes_addr()
    after_mons = bus.read(base + platinum.BOX_NAMES, 0x121D0 - platinum.BOX_NAMES)
    set_slot(pm, 16, 5, mon(1))                                           # something to clear
    pc.write_boxes([[mon(2), mon(3)]] + [[] for _ in range(13)])
    assert pm.box_raw(0)[:3] == [mon(2), mon(3), EMPTY] and pm.box_raw(16)[5] == EMPTY
    assert bus.read(base + platinum.BOX_NAMES, len(after_mons)) == after_mons
    assert pm.current_box() == 3 and pm.box_name(2) and pm.box_wallpaper(17) == 1
    assert pc.read_boxes()[0] == [mon(2), mon(3)] and pc.initialised() and pc.current_box() == 3


def test_write_refused_while_the_box_ui_runs():
    _bus, pm, pc = game()
    pm.box_ui = True
    with pytest.raises(RuntimeError):
        pc.write_boxes([[] for _ in range(14)])


def test_remove_party_slot_shifts_and_never_empties_the_party():
    a, s, b = mon(1), pk4.standin({"id": "x", "nick": "CHIKORITA", "level": 9}, ROM), mon(2)
    _bus, pm, pc = game([a, s, b])
    pc.remove_party_slot(1)
    assert pm.party_count() == 2 and [r[:136] for r in pm.party_raw()] == [a, b]
    assert pm.read(pm.party_addr(2), 236) == pk4.EMPTY_PARTY
    pc.remove_party_slot(0)
    pc.remove_party_slot(0)                                               # the last one stays
    assert pm.party_count() == 1 and pm.party_raw()[0][:136] == b


def test_record_for_current_representation():
    _bus, _pm, pc = game()
    own = mon(5)
    e4 = pk4.entry_from_pk4(own, ROM)
    assert e4["origin"] == "platinum" and pc.record_for(e4) == own
    g1 = {"id": "y1", "origin": "yellow", "dex": 25, "level": 10, "nick": "PIKACHU", "gen1": b64(gen1_rec(25)),
          "gen2": None, "gen4": None}
    conv = pc.record_for(g1)
    assert pk4.valid(conv) and pk4.PK4(pk4.decrypt(conv)).species == 25 and not pc.is_standin(conv)
    assert pc.record_for(g1) == conv                                       # cached, deterministic
    g2 = {"id": "c1", "origin": "crystal", "dex": 152, "level": 12, "nick": "CHIKORITA", "gen1": None,
          "gen2": b64(gen2_rec(152)), "gen4": None}
    assert pk4.PK4(pk4.decrypt(pc.record_for(g2))).species == 152
    egg = dict(g2, id="c2", gen2=b64(gen2_rec(152, egg=True)))
    st = pc.record_for(egg)
    assert pc.is_standin(st) and pk4.PK4(pk4.decrypt(st)).nickname == "?CHIKORITA"
    # origin first: a Crystal-origin entry with a stale gen4 copy shows the Crystal mon
    both = dict(g2, gen4=b64(own))
    assert pk4.PK4(pk4.decrypt(pc.record_for(both))).species == 152
    assert pc.key(own) == pk4.mon_key(own)


def test_pcsync_platinum_session(tmp_path):
    turtwig, chim = mon(1), mon(2, species=390)
    _bus, pm, pc = game([turtwig])
    sync = pcdb.PCSync(tmp_path / "pc.json")
    # a Crystal-only mon (Platinum converts it) and a Yellow mon are already in the store
    g2 = {"id": "c1", "origin": "crystal", "dex": 152, "level": 12, "nick": "CHIKORITA", "ot": "KRIS", "otid": 1,
          "gen1": None, "gen2": b64(gen2_rec(152)), "gen4": None}
    sync.working["boxes"][0].append(g2)
    set_slot(pm, 0, 4, chim)                                              # already in Platinum's PC: imported
    view = sync.open_session("platinum", pc, trust_game=False)
    assert "platinum" in sync.working["linked"] and sync.count() == 2
    pc.write_boxes(view)
    assert [pk4.PK4(pk4.decrypt(r)).species for r in pm.box_raw(0)[:3]] == [152, 390, 0]
    # in the box UI: deposit Turtwig into box 17, move Chimchar to slot 25 of box 1
    set_slot(pm, 16, 0, turtwig)
    set_slot(pm, 0, 24, pm.box_raw(0)[1])
    set_slot(pm, 0, 1, EMPTY)
    rep = sync.close_session("platinum", pc, platinum.party_records(pm))
    assert rep["added"] == 1 and rep["removed"] == 0 and sync.pending
    assert pc.harvested == 1
    by_nick = {e["nick"]: e for e in sync.working["boxes"][0]}
    assert set(by_nick) == {"CHIKORITA", "MON390", "TURTWIG"}
    assert by_nick["TURTWIG"]["origin"] == "platinum" and by_nick["TURTWIG"]["gen4"] == b64(turtwig)
    assert by_nick["CHIKORITA"]["id"] == "c1" and by_nick["CHIKORITA"]["gen2"] == g2["gen2"]
    sync.commit()
    # reopen: nothing changed (folding is invisible to the diff); the boxes are rewritten without holes
    view = sync.open_session("platinum", pc, trust_game=True)
    assert not sync.pending
    pc.write_boxes(view)
    assert pm.box_raw(16)[0] == EMPTY and sum(r != EMPTY for r in pm.box_raw(0)) == 3
    rep = sync.close_session("platinum", pc)
    assert not sync.pending and rep["added"] == rep["removed"] == 0


def test_pcsync_standin_withdrawn_into_the_party_goes_back(tmp_path):
    _bus, pm, pc = game([mon(1)])
    sync = pcdb.PCSync(tmp_path / "pc.json")
    egg = {"id": "c9", "origin": "crystal", "dex": 152, "level": 5, "nick": "EGG", "gen1": None,
           "gen2": b64(gen2_rec(152, egg=True)), "gen4": None}
    sync.working["boxes"][0].append(egg)
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=False))
    st = pm.box_raw(0)[0]
    assert pc.is_standin(st)
    # withdraw: the game moves it into the party
    set_slot(pm, 0, 0, EMPTY)
    party_at = pm.party_addr(1)
    pm.m.write(party_at, pk4.to_party(st, ROM))
    pm.m.w32(pm.save_body() + platinum.PAGE_PARTY + 4, 2)
    rep = sync.close_session("platinum", pc, platinum.party_records(pm))
    assert rep["standins_returned"] == ["EGG"] and rep["standin_party_slots"] == [1]
    for slot in rep["standin_party_slots"]:
        pc.remove_party_slot(slot)
    assert pm.party_count() == 1 and sync.working["boxes"][0][0]["id"] == "c9"


def test_full_store_platinum_deposit_stays_in_game(tmp_path):
    _bus, pm, pc = game()
    sync = pcdb.PCSync(tmp_path / "pc.json")
    for b in range(14):
        for s in range(20):
            sync.working["boxes"][b].append(pk4.entry_from_pk4(mon(b * 20 + s), ROM))
    sync.working["linked"].append("platinum")
    sync.last_view["platinum"] = [[] for _ in range(14)]
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=False))
    new = mon(5000, species=25)
    set_slot(pm, 15, 0, new)
    rep = sync.close_session("platinum", pc)
    assert rep["added"] == 0 and sync.count() == 280 and pc.unplaced == [new]
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=True))
    assert pm.box_raw(14)[0] == new                                       # still in Platinum (box 15)
    sync.close_session("platinum", pc)
    assert pc.unplaced == [new]


# ---------------------------------------------------------------------------------------------------------
# Yellow / Crystal codecs: Platinum entries convert when they can
# ---------------------------------------------------------------------------------------------------------
def test_gen1_record_for_platinum_entries():
    pc = pcdb.Gen1PC(None, G1ROM)
    pid4 = native(species=16, moves=(33, 45, 0, 0), nick=None)
    e = pk4.entry_from_pk4(pid4, ROM)
    rec = pc.record_for(e)
    assert not pc.is_standin(rec) and G1ROM.dex_no(rec[0]) == 16
    turt = pk4.entry_from_pk4(native(species=387), ROM)
    st = pc.record_for(turt)
    assert pc.is_standin(st)
    from yellowlink import text
    assert text.decode(st[44:55]) == "?TURTWIG"
    pc.plat_rom = ROM                                                     # with the Platinum data: same answer
    assert pc.record_for(e)[0] == rec[0]
    # Yellow's own data first for a Yellow entry; a Platinum entry with a stale gen1 copy converts from gen4
    y = {"id": "y", "origin": "yellow", "dex": 25, "gen1": b64(gen1_rec(25)), "gen2": None, "gen4": b64(pid4)}
    assert pc.record_for(y) == gen1_rec(25)
    p = dict(y, origin="platinum")
    assert G1ROM.dex_no(pc.record_for(p)[0]) == 16


def test_gen2_record_for_platinum_entries():
    pc = crystal.Gen2PC.__new__(crystal.Gen2PC)
    pc.rom = G2ROM
    e = pk4.entry_from_pk4(native(species=152, moves=(33, 45, 0, 0)), ROM)
    rec = pc.record_for(e)
    assert rec[0] == 152 and not pc.is_standin(rec)
    st = pc.record_for(pk4.entry_from_pk4(native(species=387), ROM))
    assert pc.is_standin(st) and st[0] == crystal.DITTO


def test_trade_labels_know_gen4_species():
    from yellowlink.trades import TradeMixin

    class T(TradeMixin):
        plat_data = ROM

        def dex_name(self, dex):
            return f"#{dex}"
    t = T()
    assert t.pc_dex_name(387) == "Turtwig" and t.pc_dex_name(600) == "#600"
    assert t.entry_label({"dex": 387, "nick": "TURTWIG", "level": 7}) == "Turtwig Lv7"
    t.plat_data = None
    assert t.pc_dex_name(387) == "#387"


# ---------------------------------------------------------------------------------------------------------
# with the real ROM data
# ---------------------------------------------------------------------------------------------------------
needs_rom = pytest.mark.skipif(not os.path.exists(PT_ROM), reason="needs dev/states/platinum.nds")


@needs_rom
def test_real_rom_conversions_round_trip_through_the_store(tmp_path):
    rom = ndsrom.platinum(PT_ROM)
    _bus, pm, _ = game()
    pc = Gen4PC(pm, rom)
    y = {"id": "y1", "origin": "yellow", "dex": 25, "level": 10, "nick": "PIKACHU", "ot": "ASH", "otid": 0x1234,
         "gen1": b64(gen1_rec(25)), "gen2": None, "gen4": None}
    rec = pc.record_for(y)
    pk = pk4.PK4(pk4.decrypt(rec))
    assert pk.species == 25 and pk.level(rom) == 10 and pk.ot_name == "ASH"
    assert platinum_pc.load_data(PT_ROM) is rom and platinum_pc.load_data(None) is None
    # Yellow's codec turns a Platinum-origin Pikachu back into Gen 1 with the Platinum data for levels
    g1 = pcdb.Gen1PC(None, G1ROM)
    g1.plat_rom = rom
    e = pc.entry_from_record(rec)
    back = g1.record_for(e)
    assert G1ROM.dex_no(back[0]) == 25 and back[3] == 10 and back[8:12] == gen1_rec(25)[8:12]
    assert rom.species_name(493).upper() == "ARCEUS"


# ---------------------------------------------------------------------------------------------------------
# review fixes (each test reproduces a bug found in review)
# ---------------------------------------------------------------------------------------------------------
def all_game_mons(pm):
    return sorted(r for b in range(18) for r in pm.box_raw(b) if platinum_pc.classify(r) == "mon")


def fill_store(sync, n, first=0):
    k = 0
    for b in range(14):
        while len(sync.working["boxes"][b]) < 20 and k < n:
            sync.working["boxes"][b].append(pk4.entry_from_pk4(mon(5000 + first + k, species=25), ROM))
            k += 1


def test_first_link_with_a_nearly_full_store_never_erases_platinum_mons(tmp_path):
    """1: store 270 + Platinum box 1 with 30 -> 10 imported, the other 20 stay in the game (boxes 15-18)."""
    _bus, pm, pc = game()
    sync = pcdb.PCSync(tmp_path / "pc.json")
    fill_store(sync, 270)
    theirs = [mon(i) for i in range(30)]
    for s, r in enumerate(theirs):
        set_slot(pm, 0, s, r)
    view = sync.open_session("platinum", pc, trust_game=False)
    pc.write_boxes(view)
    assert sync.count() == 280 and "platinum" in sync.working["linked"]
    in_game = all_game_mons(pm)
    assert all(r in in_game for r in theirs)                              # nothing erased
    assert len(pc.unplaced) == 20 and all(r in [x for b in range(14, 18) for x in pm.box_raw(b)]
                                          for r in pc.unplaced)
    left = list(pc.unplaced)
    # once there is room (140 traded away, among them the 10 imported ones) the other 20 are harvested
    sync.close_session("platinum", pc)
    sync.commit()
    gone = [e["id"] for b in sync.working["boxes"] for e in b[10:]]
    sync.apply_trade({"trade_id": "t", "remove": gone, "add": [], "srv_rev": 5})
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=True))
    assert sync.count() == 140 and all(r in all_game_mons(pm) for r in left)
    sync.close_session("platinum", pc)
    assert pc.unplaced == [] and sync.count() == 160
    assert all(any(e.get("gen4") == b64(r) for b in sync.working["boxes"] for e in b) for r in left)


def test_first_link_of_a_gb_game_that_does_not_fit_is_refused(tmp_path):
    """1 (Yellow/Crystal): never write a view that would drop the game's own mons."""
    from test_pcdb import rec as g1rec, setup_game
    mons = [g1rec(0x54, "M%d" % i, otid=i) for i in range(20)] + [g1rec(0xA5, "N%d" % i, otid=100 + i)
                                                                    for i in range(10)]
    mem, ypc = setup_game([mons[:20], mons[20:]])
    sync = pcdb.PCSync(tmp_path / "pc.json")
    fill_store(sync, 270)
    before = ypc.read_boxes()
    assert sync.open_session("yellow", ypc, trust_game=False) is None
    assert sync.refused == (30, 10) and "yellow" not in sync.working["linked"] and not sync.session_open
    assert ypc.read_boxes() == before and sync.count() == 270
    for b in sync.working["boxes"]:                                       # with room it links normally
        del b[15:]
    assert sync.open_session("yellow", ypc, trust_game=False) is not None and sync.count() == 205 + 30


def marked(rec, marks=0x05):
    p = pk4.PK4(pk4.decrypt(rec))
    p.markings = marks
    return p.box_bytes()


def with_item(rec, item):
    p = pk4.PK4(pk4.decrypt(rec))
    p.item = item
    return p.box_bytes()


def session(tmp_path, entries):
    _bus, pm, pc = game([mon(900)])
    sync = pcdb.PCSync(tmp_path / "pc.json")
    sync.working["boxes"][0].extend(entries)
    sync.working["linked"].append("platinum")
    sync.last_view["platinum"] = [[] for _ in range(14)]
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=True))
    return pm, pc, sync


def test_box_ui_edits_update_the_same_entry(tmp_path):
    """2: MARK / give an item in Platinum's box UI = the same mon (same id), not a deposit (no clone)."""
    own = pk4.entry_from_pk4(mon(1), ROM)
    yel = {"id": "y1", "origin": "yellow", "dex": 25, "level": 10, "nick": "PIKACHU", "ot": "ASH", "otid": 0x1234,
           "gen1": b64(gen1_rec(25)), "gen2": None, "gen4": None}
    pm, pc, sync = session(tmp_path, [own, yel])
    shown = pm.box_raw(0)[:2]
    set_slot(pm, 0, 0, with_item(shown[0], 155))                          # Oran Berry to our own Turtwig
    set_slot(pm, 0, 1, marked(shown[1]))                                  # a mark on the converted Pikachu
    rep = sync.close_session("platinum", pc)
    assert rep["added"] == 0 and rep["removed"] == 0 and rep["changed"] == 1 and sync.count() == 2
    e0, e1 = sync.working["boxes"][0]
    assert e0["id"] == own["id"] and e0["gen4"] == b64(with_item(shown[0], 155))
    assert e1 == yel                                                      # marks only: Yellow data kept
    sync.commit()
    # the next visit sees the game exactly as recorded: no changes, nothing new
    sync.open_session("platinum", pc, trust_game=True)
    assert not sync.pending and sync.count() == 2


def test_marked_locked_mon_is_not_cloned(tmp_path):
    """2: a mon offered in a trade, then marked in the box UI, stays one (locked) entry."""
    e = pk4.entry_from_pk4(mon(2), ROM)
    pm, pc, sync = session(tmp_path, [e])
    sync.lock("t1", [e["id"]])
    set_slot(pm, 0, 0, marked(pm.box_raw(0)[0]))
    rep = sync.close_session("platinum", pc)
    assert rep["added"] == 0 and sync.count() == 1 and sync.working["boxes"][0][0] == e
    sync.commit()
    sync.open_session("platinum", pc, trust_game=True)
    assert sync.count() == 1 and not sync.pending


def test_item_given_to_a_standin_goes_back_once(tmp_path):
    """2: ?NAME stand-ins keep their real data; an item given to one is returned (exactly once)."""
    egg = {"id": "c9", "origin": "crystal", "dex": 152, "level": 5, "nick": "EGG", "gen1": None,
           "gen2": b64(gen2_rec(152, egg=True)), "gen4": None}
    pm, pc, sync = session(tmp_path, [egg])
    st = pm.box_raw(0)[0]
    assert pc.is_standin(st)
    set_slot(pm, 0, 0, marked(with_item(st, 155)))
    rep = sync.close_session("platinum", pc)
    assert rep["items_back"] == [155] and sync.working["boxes"][0] == [egg] and rep["added"] == 0
    rep = sync.close_session("platinum", pc)                              # seen again before the next write
    assert rep["items_back"] == [] and sync.working["boxes"][0] == [egg]
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=True))
    assert pm.box_raw(0)[0] == st                                         # the stored stand-in, no item


def test_traded_away_mon_left_in_the_box_does_not_come_back(tmp_path):
    """Traded away while the PC was open and left in the box: dropped at close and NOT re-imported at the next
    open (the game still shows it until that open rewrites the boxes)."""
    a, b = pk4.entry_from_pk4(mon(3), ROM), pk4.entry_from_pk4(mon(4), ROM)
    pm, pc, sync = session(tmp_path, [a, b])
    sync.apply_trade({"trade_id": "t", "remove": [a["id"]], "add": [], "srv_rev": 3})
    sync.close_session("platinum", pc)
    assert [e["id"] for e in sync.working["boxes"][0]] == [b["id"]]
    sync.commit()
    sync.open_session("platinum", pc, trust_game=True)
    assert [e["id"] for e in sync.working["boxes"][0]] == [b["id"]]


def test_traded_away_yellow_mon_left_in_the_box_does_not_come_back(tmp_path):
    """Same for Yellow (this used to clone the traded mon on the next PC visit)."""
    from test_pcdb import rec as g1rec, setup_game
    mem, ypc = setup_game([[g1rec(0x54, "PIKA"), g1rec(0xA5, "RATTY", otid=2)]])
    sync = pcdb.PCSync(tmp_path / "pc.json")
    ypc.write_boxes(sync.open_session("yellow", ypc, True))
    sync.close_session("yellow", ypc)
    sync.commit()
    ypc.write_boxes(sync.open_session("yellow", ypc, True))
    pika = sync.working["boxes"][0][0]["id"]
    sync.apply_trade({"trade_id": "t", "remove": [pika], "add": [], "srv_rev": 2})
    sync.close_session("yellow", ypc)
    sync.commit()
    sync.open_session("yellow", ypc, True)
    assert [e["nick"] for e in sync.working["boxes"][0]] == ["RATTY"]


def in_place(rec):
    """What the game's own BoxPokemon_EnterDecryptionContext leaves in RAM: boxDecrypted set, the data blocks
    XOR-decrypted with the checksum seed but still in their PID-shuffled order (pret src/pokemon.c)."""
    rec = bytearray(rec)
    flags, chk = struct.unpack_from("<HH", rec, 4)
    struct.pack_into("<H", rec, 4, flags | 0x2)
    rec[8:136] = pk4._crypt(bytes(rec[8:136]), chk)
    return bytes(rec)


def test_in_place_decrypted_records_every_shuffle():
    """4 / review 2 #1: for every block order, an in-place decrypted record is read as the same mon and turned
    back into exactly the stored bytes (pk4.encrypt would shuffle the blocks a second time)."""
    for sv in range(24):
        r = native(species=387, pid=(sv << 13) | 0x15, tid=7, sid=9, level=12)
        assert ((r and pk4.decrypt(r)) and (struct.unpack_from("<I", r)[0] >> 13) & 31) % 24 == sv
        ip = in_place(r)
        assert not pk4.valid(ip) and pk4.in_place_decrypted(ip)
        assert platinum_pc.normalise(ip) == ("mon", r)
        assert pk4.PK4(pk4._plain_of(ip)).species == 387 and pk4.mon_key(ip) == pk4.mon_key(r)
        assert Gen4PC.same_mon(r, ip) and Gen4PC.held_item(ip) == 0
        exiting = bytearray(ip)                                           # ExitDecryptionContext half-way:
        struct.pack_into("<H", exiting, 4, 0)                             # flags already cleared, data plain
        assert platinum_pc.normalise(bytes(exiting)) == ("mon", r)
    garbage = bytearray(in_place(mon(1)))
    garbage[40] ^= 0x5A                                                   # flag set but not a valid mon
    assert platinum_pc.normalise(bytes(garbage))[0] == "bad"
    ip_party = bytearray(pk4.to_party(mon(1), ROM))
    flags, chk = struct.unpack_from("<HH", ip_party, 4)
    struct.pack_into("<H", ip_party, 4, flags | 0x3)
    ip_party[8:136] = pk4._crypt(bytes(ip_party[8:136]), chk)
    ip_party[136:] = pk4._crypt(bytes(ip_party[136:]), struct.unpack_from("<I", ip_party)[0])
    assert pk4.reencrypt_in_place(bytes(ip_party)) == pk4.to_party(mon(1), ROM)


def test_box_slot_read_mid_update_is_not_a_withdrawal(tmp_path):
    """4: a slot the game has decrypted in place at the close frame is the same mon (no removal, no new entry)."""
    entries = [pk4.entry_from_pk4(native(species=387, pid=(sv << 13) | 0x33, tid=sv, level=9), ROM)
               for sv in range(0, 24, 3)]
    pm, pc, sync = session(tmp_path, entries)
    for s_, r in enumerate(pm.box_raw(0)[:len(entries)]):
        set_slot(pm, 0, s_, in_place(r))
    rep = sync.close_session("platinum", pc)
    assert rep["removed"] == rep["added"] == rep["changed"] == 0 and sync.working["boxes"][0] == entries
    assert pc.unplaced == [] and not sync.pending
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=True))   # and the game gets the real bytes back
    assert [species(r) for r in pm.box_raw(0)[:len(entries)]] == [387] * len(entries)


def species(rec):
    return pk4.PK4(pk4.decrypt(rec)).species


def twin(i, level, species=25):
    """Same PID/TID/SID as mon(i) (so the same mon_key) but a different mon."""
    return native(species=species, pid=0x10000 + i * 7919, tid=i, sid=0, level=level, nick="TWIN")


def test_party_removal_takes_the_right_mon_when_keys_collide(tmp_path):
    """5: converted mons' keys can collide; a locked mon withdrawn into the party must not take a real party mon
    with the same key out instead of (or as well as) itself."""
    a = pk4.entry_from_pk4(mon(6), ROM)
    pm, pc, sync = session(tmp_path, [a])
    sync.lock("t2", [a["id"]])
    shown = pm.box_raw(0)[0]
    set_slot(pm, 0, 0, EMPTY)                                             # withdrawn anyway
    other = twin(6, 30)
    assert pc.key(other) == pc.key(shown) and other != shown
    rep = sync.close_session("platinum", pc, [mon(900), other, shown])
    assert rep["locked_returned"] and rep["standin_party_slots"] == [2]
    assert list(rep["party_removals"]) == [2]


def test_gb_party_removal_one_slot_per_mon(tmp_path):
    """5 (Yellow): two party mons share the locked mon's key -> only the closest one is taken."""
    from test_pcdb import rec as g1rec, setup_game
    mem, ypc = setup_game([[g1rec(0x54, "PIKA"), g1rec(0xA5, "RATTY")]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    ypc.write_boxes(sync.open_session("yellow", ypc, True))
    sync.close_session("yellow", ypc)
    sync.commit()
    ratty = next(e for e in sync.committed["boxes"][0] if e["nick"] == "RATTY")
    sync.lock("t9", [ratty["id"]])
    ypc.write_boxes(sync.open_session("yellow", ypc, True))
    withdrawn = ypc.read_boxes()[0][1]
    boxes = ypc.read_boxes()
    boxes[0] = boxes[0][:1]
    ypc.write_boxes(boxes)
    lookalike = g1rec(0xA5, "OTHER", level=40)                           # same Yellow key, a different mon
    assert ypc.key(lookalike) == ypc.key(withdrawn)
    report = sync.close_session("yellow", ypc, party_records=[lookalike, withdrawn])
    assert report["standin_party_slots"] == [1]


def test_give_item_to_bag():
    _bus, pm, _ = game()
    pockets = [0] * platinum_pc.NUM_ITEMS
    pockets[155], pockets[328] = 4, 3
    pc = Gen4PC(pm, ROM, pockets)
    bag = pm.save_body() + platinum_pc.PAGE_BAG
    berries = bag + platinum_pc.BAG_POCKETS[4][0]
    assert pc.give_item(155) and pc.give_item(155) and pc.give_item(17)
    assert struct.unpack("<HH", pm.read(berries, 4)) == (155, 2)
    assert struct.unpack("<HH", pm.read(bag, 4)) == (17, 1)               # items pocket
    pm.m.write(bag + platinum_pc.BAG_POCKETS[3][0], struct.pack("<HH", 328, 99))
    assert not pc.give_item(328)                                          # TMs stop at 99
    assert not Gen4PC(pm, ROM).give_item(155)                             # no item data: refuse


class FakeApp:
    """Just what App._pt_pc_poll / on_pc_close need."""
    frame = 0

    def __init__(self, pm, pc, sync):
        self.game, self.pc_codec, self.pcsync = pm, pc, sync
        self.closed_with = []
        self._pt_pc, self._pt_pc_wait, self._pt_close_wait, self._pt_close_prev = "open", 0, 0, None
        self._pt_party_fix = None

    def on_pc_close(self, party=None):
        self.closed_with.append(party)


def _app_methods():
    from yellowlink import app as appmod
    for name in ("_pt_pc_poll", "_pt_party_now"):
        setattr(FakeApp, name, getattr(appmod.App, name))


def test_close_waits_for_a_settled_party_and_boxes(tmp_path):
    """3: the close is harvested only when party_checked() reads clean and the boxes read the same twice."""
    _app_methods()
    e = pk4.entry_from_pk4(mon(7), ROM)
    pm, pc, sync = session(tmp_path, [e])
    a = FakeApp(pm, pc, sync)
    first = pm.party_raw()[0]
    garbage = bytearray(first)
    garbage[60] ^= 0xFF
    pm.m.write(pm.party_addr(0), bytes(garbage))                          # mid-update party record
    poll = lambda: a._pt_pc_poll(1)                                       # noqa: E731
    poll()
    assert a._pt_pc == "closing" and not a.closed_with
    poll()
    poll()
    assert not a.closed_with                                              # party still unreadable
    pm.m.write(pm.party_addr(0), first)
    poll()
    assert a.closed_with == [[first[:136]]] and a._pt_pc is None


def test_first_link_that_would_overflow_platinum_is_refused(tmp_path):
    """review 2 #2: store 280 + 300 Platinum mons: 300 don't fit the 260 spare slots -> not linked, untouched."""
    _bus, pm, pc = game()
    sync = pcdb.PCSync(tmp_path / "pc.json")
    fill_store(sync, 280)
    theirs = [mon(i) for i in range(300)]
    for k, r in enumerate(theirs):
        set_slot(pm, k // 30, k % 30, r)
    before = pm.read(pm.box_slot_addr(0, 0), 540 * 136)
    assert sync.open_session("platinum", pc, trust_game=False) is None
    assert sync.refused == (300, 0) and "platinum" not in sync.working["linked"] and not sync.session_open
    assert pm.read(pm.box_slot_addr(0, 0), 540 * 136) == before and sync.count() == 280
    # 260 fit exactly (store full: all of them stay in the game)
    for k in range(260, 300):
        set_slot(pm, k // 30, k % 30, EMPTY)
    pc.write_boxes(sync.open_session("platinum", pc, trust_game=False))
    assert all(r in all_game_mons(pm) for r in theirs[:260])


def test_layout_raises_instead_of_dropping_and_write_writes_nothing():
    _bus, pm, pc = game()
    with pytest.raises(ValueError):
        layout([[] for _ in range(14)], [mon(i) for i in range(261)])
    before = pm.read(pm.box_slot_addr(0, 0), 540 * 136)
    pc.unplaced = [mon(i) for i in range(261)]
    with pytest.raises(ValueError):
        pc.write_boxes([[mon(999)]] + [[] for _ in range(13)])
    assert pm.read(pm.box_slot_addr(0, 0), 540 * 136) == before


def test_abort_open_forgets_the_open(tmp_path):
    e = pk4.entry_from_pk4(mon(1), ROM)
    _bus, pm, pc = game()
    set_slot(pm, 0, 0, mon(2))
    sync = pcdb.PCSync(tmp_path / "pc.json")
    sync.working["boxes"][0].append(e)
    sync.open_session("platinum", pc, trust_game=False)                   # links + imports
    sync.abort_open()
    assert not sync.session_open and sync.working["linked"] == [] and sync.working["boxes"][0] == [e]
    assert "platinum" not in sync.last_view


def test_merge_keeps_an_entry_edited_only_here(tmp_path):
    """review 2 #3: an entry changed in place locally (same id) survives a merge when the server didn't change it;
    a server-side change (trade evolution) still wins when ours didn't change."""
    a = pk4.entry_from_pk4(mon(1), ROM)
    b = pk4.entry_from_pk4(mon(2), ROM)
    sync = pcdb.PCSync(tmp_path / "pc.json")
    sync.committed["boxes"][0] = [a, b]
    sync.synced = pcdb._strip_server(sync.committed)
    a2 = dict(a, gen4=b64(with_item(mon(1), 234)))                        # Leftovers given in the box UI
    sync.committed["boxes"][0] = [a2, b]
    sync.working = pcdb.copy.deepcopy(sync.committed)
    b2 = dict(b, dex=26, nick="RAICHU")                                  # changed on the server
    remote = {"version": 1, "rev": 9, "srv_rev": 4, "boxes": [[a, b2]] + [[] for _ in range(13)], "linked": []}
    assert sync.merge_remote(remote)
    assert sync.committed["boxes"][0] == [a2, b2]


def test_items_taken_from_a_kept_mon_leave_the_bag_again(tmp_path):
    """review 2 #4: a locked mon's stored copy keeps its item -> the copy the box UI put in the bag is taken out
    again; a swap gives the new item back and takes the old one."""
    held = pk4.entry_from_pk4(with_item(mon(1), 234), ROM)               # holds Leftovers
    swap = pk4.entry_from_pk4(with_item(mon(2), 234), ROM)
    pm, pc, sync = session(tmp_path, [held, swap])
    sync.lock("t", [held["id"], swap["id"]])
    shown = pm.box_raw(0)[:2]
    set_slot(pm, 0, 0, with_item(shown[0], 0))                            # taken into the bag
    set_slot(pm, 0, 1, with_item(shown[1], 155))                          # swapped for an Oran Berry
    rep = sync.close_session("platinum", pc)
    assert sorted(rep["items_taken"]) == [234, 234] and rep["items_back"] == [155]
    assert sync.working["boxes"][0] == [held, swap] and rep["added"] == 0
    rep = sync.close_session("platinum", pc)                              # not again before the next write
    assert rep["items_taken"] == rep["items_back"] == []


def test_take_item_from_bag_like_the_game():
    _bus, pm, _ = game()
    pockets = [0] * platinum_pc.NUM_ITEMS
    pc = Gen4PC(pm, ROM, pockets)
    bag = pm.save_body() + platinum_pc.PAGE_BAG
    pm.m.write(bag, struct.pack("<6H", 17, 1, 234, 2, 50, 1))
    assert pc.take_item(234) and struct.unpack("<6H", pm.read(bag, 12)) == (17, 1, 234, 1, 50, 1)
    assert pc.take_item(17)                                               # emptied: the rest moves up
    assert struct.unpack("<6H", pm.read(bag, 12)) == (234, 1, 50, 1, 0, 0)
    assert not pc.take_item(155)


def test_released_locked_mon_takes_no_lookalike_out_of_the_party(tmp_path):
    """review 2 #6: a locked mon that left the box without reaching the party (released) must not take a party
    mon with the same key."""
    a = pk4.entry_from_pk4(mon(6), ROM)
    pm, pc, sync = session(tmp_path, [a])
    sync.lock("t2", [a["id"]])
    set_slot(pm, 0, 0, EMPTY)                                             # released
    lookalike = twin(6, 30)
    rep = sync.close_session("platinum", pc, [mon(900), lookalike])
    assert rep["locked_returned"] and rep["standin_party_slots"] == [] and rep["party_removals"] == {}
    # the same mon given an item in the party is still that mon
    withdrawn = with_item(pc.record_for(a), 234)
    rep = sync.close_session("platinum", pc, [mon(900), lookalike, withdrawn])
    assert rep["standin_party_slots"] == [2]


def test_gb_released_locked_mon_takes_no_lookalike(tmp_path):
    from test_pcdb import rec as g1rec, setup_game
    mem, ypc = setup_game([[g1rec(0x54, "PIKA"), g1rec(0xA5, "RATTY")]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    ypc.write_boxes(sync.open_session("yellow", ypc, True))
    sync.close_session("yellow", ypc)
    sync.commit()
    ratty = next(e for e in sync.committed["boxes"][0] if e["nick"] == "RATTY")
    sync.lock("t9", [ratty["id"]])
    ypc.write_boxes(sync.open_session("yellow", ypc, True))
    boxes = ypc.read_boxes()
    boxes[0] = boxes[0][:1]                                              # released
    ypc.write_boxes(boxes)
    report = sync.close_session("yellow", ypc, party_records=[g1rec(0xA5, "OTHER", level=40)])
    assert report["standin_party_slots"] == []


class FixApp:
    """What App._pt_fix_party / _pt_bag need."""
    frame = 0
    plat_data = None

    def __init__(self, pm, pc):
        self.game, self.pc_codec, self.toasts = pm, pc, []

    def toast(self, s, *a):
        self.toasts.append(s)


def test_party_fix_items_only_for_mons_really_taken_out():
    """review 2 #5: the last party mon can't be taken out -> its item change isn't applied (no duplicate);
    retried until the deadline; a second close merges with a pending fix instead of replacing it."""
    from yellowlink import app as appmod
    for name in ("_pt_fix_party", "_pt_bag"):
        setattr(FixApp, name, getattr(appmod.App, name))
    st = with_item(pk4.standin({"id": "x", "nick": "EGG", "level": 5}, ROM), 234)
    _bus, pm, _ = game([st])                                              # the stand-in is the only party mon
    pockets = [0] * platinum_pc.NUM_ITEMS
    pc = Gen4PC(pm, ROM, pockets)
    a = FixApp(pm, pc)
    a._pt_party_fix = {"remove": [(st, [234], [])], "give": [], "take": [], "until": 10}
    a._pt_fix_party()
    bag = pm.save_body() + platinum_pc.PAGE_BAG
    assert struct.unpack("<HH", pm.read(bag, 4)) == (0, 0)                # nothing given: it's still in the party
    assert a._pt_party_fix and len(a._pt_party_fix["remove"]) == 1
    pm.m.write(pm.party_addr(1), pk4.to_party(mon(3), ROM))               # another mon joins: now it can go
    pm.m.w32(pm.save_body() + platinum.PAGE_PARTY + 4, 2)
    a._pt_fix_party()
    assert a._pt_party_fix is None and pm.party_count() == 1
    assert struct.unpack("<HH", pm.read(bag, 4)) == (234, 1)              # and only now its item goes back
