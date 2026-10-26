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
