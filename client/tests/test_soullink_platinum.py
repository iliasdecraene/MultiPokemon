"""Unit tests for the Platinum Soul Link helpers in platinum.py (fake memory, no ROM needed; the last tests use the
real ROM data when dev/states/platinum.nds exists). Real-game checks: dev/platinum/soullink_verify.py."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from yellowlink import ndsrom, pk4, platinum as P, platinum_areas  # noqa: E402
from test_pk4 import ROM, native  # noqa: E402

PT_ROM = os.path.join(os.path.dirname(__file__), "..", "..", "dev", "states", "platinum.nds")
RAM0 = 0x02000000
SAVE = 0x02200000
FS = 0x02300000
APP = 0x02301000
PMGR = 0x02302000
CHILD = 0x02303000
DTO = 0x02304000
BS = 0x02305000
CTX = 0x02306000
PARTIES = 0x02310000          # 4 DTO parties, then 4 battle parties, 0x600 apart
TID, SID = 33356, 2998


class FakeBus:
    def __init__(self):
        self.mem = bytearray(0x400000)
        self.frame = 0                      # like ds.DS.frame / NDSCore.frame_count

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

    def w8(self, a, v):
        self.mem[a - RAM0] = v & 0xFF

    def w16(self, a, v):
        struct.pack_into("<H", self.mem, a - RAM0, v & 0xFFFF)

    def w32(self, a, v):
        struct.pack_into("<I", self.mem, a - RAM0, v & 0xFFFFFFFF)


def stable(pm):
    """Two frames of the referee's observe_stable() with nothing changing (forced moves need that)."""
    for _ in range(2):
        pm.observe_stable()
        pm.m.frame += 1
    return pm


def mon(i, species=387, level=10, **kw):
    return native(species=species, pid=0x10000 + i * 7919, tid=TID, sid=SID, level=level, **kw)


def party_rec(box, hp=None):
    pk = pk4.PK4(pk4.decrypt(pk4.to_party(box, ROM)))
    if hp is not None:
        pk.hp = hp
    return pk.encrypted()


def world(party=(), rom=ROM):
    """Fake game in the overworld: SaveData + FieldSystem + field app; party of 236-byte records."""
    bus = FakeBus()
    bus.w32(P.SAVE_DATA_PTR, SAVE)
    bus.w32(P.FIELD_SYSTEM_PTR, FS)
    bus.w32(P.APP_CUR, APP)
    bus.w32(APP + 4, P.FIELD_APP_MAIN)
    bus.w32(APP + 0x1C, FS)
    bus.w32(FS + P.FS_SAVE_DATA, SAVE)
    bus.w32(FS + P.FS_PROCESS_MANAGER, PMGR)
    bus.w32(PMGR, 0x02308000)                    # parent = the field map, no child
    pm = P.PlatinumMemory(bus, rom)
    body = pm.save_body()
    bus.write(body + P.TRAINER + 0x10, struct.pack("<HH", TID, SID))
    base = body + P.PAGE_PC_BOXES
    bus.write(base + P.BOX_MONS, pk4.EMPTY_BOX * 540)
    set_party(pm, party)
    return bus, pm


def set_party(pm, party):
    body = pm.save_body()
    pm.m.w32(body + P.PAGE_PARTY, 6)
    pm.m.w32(body + P.PAGE_PARTY + 4, len(party))
    for i in range(6):
        pm.m.write(body + P.PAGE_PARTY + 8 + i * 236, party[i] if i < len(party) else pk4.EMPTY_PARTY)


def write_party_struct(bus, at, recs):
    bus.w32(at, 6)
    bus.w32(at + 4, len(recs))
    for i, r in enumerate(recs):
        bus.write(at + 8 + i * 236, r)


def start_battle(bus, pm, btype=0, enemies=(), enemies2=(), player=None, partner=()):
    """Battle app as the field's child: DTO (args) + BattleSystem (data) with the battle's own party copies."""
    bus.w32(PMGR, 0)
    bus.w32(PMGR + 4, CHILD)
    bus.w32(CHILD + 0x0C, P.OVERLAY_BATTLE)
    bus.w32(CHILD + 0x18, DTO)
    bus.w32(CHILD + 0x1C, BS)
    bus.w32(DTO, btype)
    bus.w32(DTO + P.DTO_MAP_HEADER, 343)
    player = player if player is not None else pm.party_raw()
    for k, recs in enumerate((player, enemies, partner, enemies2)):
        bus.w32(DTO + P.DTO_PARTIES + 4 * k, PARTIES + 0x600 * k)
        write_party_struct(bus, PARTIES + 0x600 * k, recs)
        bus.w32(BS + P.BS_PARTIES + 4 * k, PARTIES + 0x600 * (k + 4))
        write_party_struct(bus, PARTIES + 0x600 * (k + 4), recs)
    bus.w32(BS + P.BS_BATTLE_TYPE, btype)
    bus.w32(BS + P.BS_CTX, CTX)


def send_out(bus, battler, slot, rec, hp):
    """BattleContext: selectedPartySlot[battler] + battleMons[battler] {species, ..., curHP, maxHP, personality}."""
    p = pk4.PK4(pk4.decrypt(rec))
    bus.w8(CTX + P.CTX_SELECTED_SLOT + battler, slot)
    bm = CTX + P.CTX_BATTLE_MONS + battler * P.BATTLE_MON_SIZE
    bus.w16(bm + P.BM_SPECIES, p.species)
    bus.w32(bm + P.BM_CUR_HP, hp)
    bus.w32(bm + P.BM_MAX_HP, p.stats[0] if p.is_party else 30)
    bus.w32(bm + P.BM_PID, p.pid)


def end_battle(bus):
    bus.w32(PMGR, 0x02308000)
    bus.w32(PMGR + 4, 0)


# --- battle ----------------------------------------------------------------------------------------------------
def test_none_outside_battle():
    bus, pm = world([party_rec(mon(1))])
    assert pm.battle_info() is None and pm.battle_kind() is None and pm.battle_party_slot() is None
    assert pm.battle_type() is None and pm.battle_dto() is None


@pytest.mark.parametrize("btype,kind", [
    (0, "wild"), (P.BATTLE_TYPE_ROAMER, "wild"), (P.BATTLE_TYPE_SAFARI, "wild"),
    (P.BATTLE_TYPE_DOUBLES | P.BATTLE_TYPE_2VS2 | P.BATTLE_TYPE_AI, "wild"),           # wild double with a partner
    (P.BATTLE_TYPE_TRAINER, "trainer"), (P.BATTLE_TYPE_TRAINER | P.BATTLE_TYPE_DOUBLES, "trainer"),
    (P.BATTLE_TYPE_TAG | P.BATTLE_TYPE_TRAINER | P.BATTLE_TYPE_DOUBLES, "trainer"),    # two enemy trainers
    (P.BATTLE_TYPE_TRAINER | P.BATTLE_TYPE_DOUBLES | P.BATTLE_TYPE_2VS2 | P.BATTLE_TYPE_AI, "special"),  # + partner
    (P.BATTLE_TYPE_CATCH_TUTORIAL, "special"), (P.BATTLE_TYPE_PAL_PARK, "special"),
    (P.BATTLE_TYPE_LINK | P.BATTLE_TYPE_TRAINER, "special"), (P.BATTLE_TYPE_FRONTIER | P.BATTLE_TYPE_TRAINER, "special"),
    (P.BATTLE_TYPE_DEBUG, "special")])
def test_battle_kinds(btype, kind):
    assert P.battle_kind_of(btype) == kind
    bus, pm = world([party_rec(mon(1))])
    start_battle(bus, pm, btype, [party_rec(mon(50, species=396, level=4))])
    assert pm.battle_kind() == kind and pm.battle_info()["kind"] == kind
    assert pm.battle_info()["safari"] is bool(btype & P.BATTLE_TYPE_SAFARI)


def test_battle_info_wild():
    bus, pm = world([party_rec(mon(1))])
    wild = party_rec(mon(50, species=396, level=4))
    start_battle(bus, pm, 0, [wild])
    info = pm.battle_info()
    pid = struct.unpack_from("<I", wild)[0]
    assert info == {"kind": "wild", "dex": 396, "shiny": False, "safari": False, "contest": False, "level": 4,
                    "battle_type": 0, "pid": pid, "key": pk4.mon_key(wild), "map": 343, "others": [],
                    "keys": [pk4.mon_key(wild)], "dexes": [396]}
    end_battle(bus)
    assert pm.battle_info() is None


def test_battle_info_reads_in_place_decrypted_enemy():
    bus, pm = world([party_rec(mon(1))])
    wild = party_rec(mon(50, species=396, level=4))
    d = bytearray(wild)                         # the battle works on it: XOR-decrypted in place, flag set
    chk = struct.unpack_from("<H", d, 6)[0]
    d[8:136] = pk4._crypt(bytes(d[8:136]), chk)
    struct.pack_into("<H", d, 4, pk4.FLAG_BOX_DECRYPTED)
    start_battle(bus, pm, 0, [bytes(d)])
    assert pm.battle_info()["dex"] == 396 and pm.battle_info()["key"] == pk4.mon_key(wild)


def test_battle_info_shiny_gen4_rule():
    bus, pm = world([party_rec(mon(1))])
    pid = ((TID ^ SID ^ 0x1234) << 16) | 0x1234 ^ 5          # (tid ^ sid ^ hi ^ lo) = 5 < 8
    shiny = native(species=399, pid=pid, tid=TID, sid=SID, level=3)
    start_battle(bus, pm, 0, [party_rec(shiny)])
    assert pm.battle_info()["shiny"] is True
    plain = native(species=399, pid=pid ^ 0x80000, tid=TID, sid=SID, level=3)
    start_battle(bus, pm, 0, [party_rec(plain)])
    assert pm.battle_info()["shiny"] is False


def test_battle_info_double_and_trainer():
    bus, pm = world([party_rec(mon(1))])
    a, b = party_rec(mon(60, species=399, level=3)), party_rec(mon(61, species=396, level=3))
    start_battle(bus, pm, P.BATTLE_TYPE_DOUBLES | P.BATTLE_TYPE_2VS2 | P.BATTLE_TYPE_AI, [a], enemies2=[b],
                 partner=[party_rec(native(species=113, pid=99, tid=1, sid=2, level=20))])
    info = pm.battle_info()
    assert info["kind"] == "wild" and info["dex"] == 399 and info["others"] == [396]
    assert info["keys"] == [pk4.mon_key(a), pk4.mon_key(b)] and info["dexes"] == [399, 396] and info["key"] == \
        pk4.mon_key(a)
    start_battle(bus, pm, P.BATTLE_TYPE_TRAINER, [party_rec(mon(70, species=399, level=5)),
                                                  party_rec(mon(71, species=403, level=5))])
    info = pm.battle_info()
    assert info["kind"] == "trainer" and info["dex"] == 399 and info["others"] == [] and info["dexes"] == [399]
    bad = bytearray(party_rec(mon(72, species=399)))               # unreadable this frame -> None, not the next mon
    bad[40] ^= 1
    start_battle(bus, pm, 0, [bytes(bad)], enemies2=[b])
    assert pm.battle_info() is None and pm.battle_kind() == "wild"


def test_battle_party_slot_by_pid_and_switch():
    p0, p1 = party_rec(mon(1)), party_rec(mon(2, species=396))
    bus, pm = world([p0, p1])
    start_battle(bus, pm, 0, [party_rec(mon(50, species=399, level=3))])
    assert pm.battle_party_slot() is None                         # BattleContext still zeroed
    send_out(bus, 0, 0, p0, 17)
    assert pm.battle_party_slot() == (0, 17)
    send_out(bus, 0, 0, p0, 0)
    assert pm.battle_party_slot() == (0, 0)
    bus.w8(CTX + P.CTX_SELECTED_SLOT, 1)                          # switch: slot index written first
    assert pm.battle_party_slot() is None
    send_out(bus, 0, 1, p1, 12)
    assert pm.battle_party_slot() == (1, 12)
    bus.w32(CTX + P.CTX_BATTLE_MONS + P.BM_CUR_HP, 0xFFFFFFFF)     # negative HP -> 0
    assert pm.battle_party_slot() == (1, 0)


def test_battle_party_slot_maps_battle_order_to_save_party():
    """Frontier-style battle party (other order) is ignored; a reordered copy still maps by PID."""
    p0, p1, p2 = party_rec(mon(1)), party_rec(mon(2)), party_rec(mon(3))
    bus, pm = world([p0, p1, p2])
    start_battle(bus, pm, 0, [party_rec(mon(50))], player=[p2, p0])
    send_out(bus, 0, 0, p2, 9)
    assert pm.battle_party_slot() == (2, 9)
    start_battle(bus, pm, P.BATTLE_TYPE_FRONTIER | P.BATTLE_TYPE_TRAINER, [party_rec(mon(50))], player=[p2])
    send_out(bus, 0, 0, p2, 0)
    assert pm.battle_party_slot() is None


def test_battle_party_slot_doubles_and_partner():
    p0, p1 = party_rec(mon(1)), party_rec(mon(2))
    bus, pm = world([p0, p1])
    start_battle(bus, pm, P.BATTLE_TYPE_TRAINER | P.BATTLE_TYPE_DOUBLES, [party_rec(mon(50))])
    send_out(bus, 0, 0, p0, 20)
    bus.w32(BS + P.BS_PARTIES + 8, PARTIES + 0x600 * 4)            # doubles: battler 2 uses the player's party
    send_out(bus, 2, 1, p1, 0)
    assert pm.battle_party_slot() == (1, 0)                       # the fainted one is preferred
    send_out(bus, 2, 1, p1, 5)
    assert pm.battle_party_slot() == (0, 20)
    partner = party_rec(native(species=113, pid=99, tid=1, sid=2, level=20))
    start_battle(bus, pm, P.BATTLE_TYPE_DOUBLES | P.BATTLE_TYPE_2VS2 | P.BATTLE_TYPE_AI, [party_rec(mon(50))],
                 partner=[partner])
    send_out(bus, 0, 0, p0, 20)
    send_out(bus, 2, 0, partner, 0)                               # the partner's mon fainting is not ours
    assert pm.battle_party_slot() == (0, 20)


@pytest.mark.parametrize("btype", [P.BATTLE_TYPE_CATCH_TUTORIAL, P.BATTLE_TYPE_SAFARI, P.BATTLE_TYPE_PAL_PARK,
                                   P.BATTLE_TYPE_LINK | P.BATTLE_TYPE_TRAINER])
def test_battle_party_slot_none_without_player_mon(btype):
    p0 = party_rec(mon(1))
    bus, pm = world([p0])
    start_battle(bus, pm, btype, [party_rec(mon(50))])
    send_out(bus, 0, 0, p0, 20)
    assert pm.battle_party_slot() is None


# --- bag / flags / Hall of Fame ----------------------------------------------------------------------------------
def test_has_poke_balls_and_safari():
    bus, pm = world()
    pocket = pm.save_body() + P.PAGE_BAG + P.BAG_BALLS
    assert not pm.has_poke_balls() and pm.balls() == []
    bus.w16(pocket, 4)                                            # POKé BALL x0
    assert not pm.has_poke_balls()
    bus.w16(pocket + 6 * 2, 0)
    bus.w16(pocket + 2, 3)
    assert pm.has_poke_balls() and pm.balls() == [(4, 3)]
    bus.w32(pocket, 0)
    bus.write(pocket + 4 * 14, struct.pack("<HH", 16, 1))          # CHERISH BALL in the last slot
    assert pm.has_poke_balls()
    bus.w32(pocket + 4 * 14, 0)
    flags = pm.save_body() + P.PAGE_VARS_FLAGS + P.VF_FLAGS
    bus.w8(flags + P.FLAG_SAFARI_GAME_ACTIVE // 8, 1 << (P.FLAG_SAFARI_GAME_ACTIVE % 8))
    assert pm.in_safari_game() and pm.has_poke_balls()


def test_flags_vars_partner():
    bus, pm = world()
    body = pm.save_body()
    bus.w8(body + P.PAGE_VARS_FLAGS + P.VF_FLAGS + P.FLAG_HAS_PARTNER // 8, 1 << (P.FLAG_HAS_PARTNER % 8))
    bus.w16(body + P.PAGE_VARS_FLAGS + 2 * (P.VAR_PARTNER_TRAINER_ID - P.VARS_START), 608)
    assert pm.has_partner() and pm.flag(P.FLAG_HAS_PARTNER) and not pm.flag(P.FLAG_HAS_PARTNER + 1)
    assert pm.var(P.VAR_PARTNER_TRAINER_ID) == 608 and pm.var(5) == 0 and not pm.flag(-1)


def records_raw(values, seed_mod=0x1234):
    """Encrypted GameRecords bytes like game_records.c: u32[71] + u16[77] + pad, records 1.. XORed."""
    plain = bytearray(P.GAME_RECORDS_SIZE)
    for rid, v in values.items():
        if rid < 71:
            struct.pack_into("<I", plain, 4 * rid, v)
        else:
            struct.pack_into("<H", plain, 284 + 2 * (rid - 71), v)
    byte_sum = sum(plain[4:440]) & 0xFFFF
    struct.pack_into("<HH", plain, 440, byte_sum, seed_mod)
    return bytes(plain[:4]) + pk4._crypt(bytes(plain[4:440]), byte_sum | seed_mod << 16) + bytes(plain[440:])


def test_game_record_decrypt():
    raw = records_raw({0: 77, 1: 5000, P.RECORD_TIMES_ENTERED_HALL_OF_FAME: 3, 100: 9})
    assert P.game_record(raw, 0) == 77 and P.game_record(raw, 1) == 5000
    assert P.game_record(raw, P.RECORD_TIMES_ENTERED_HALL_OF_FAME) == 3 and P.game_record(raw, 100) == 9
    assert P.game_record(raw[:10], 73) == 0


def test_hall_of_fame():
    bus, pm = world()
    body = pm.save_body()
    assert not pm.entered_hall_of_fame() and pm.hall_of_fame_count() == 0 and not pm.in_hall_of_fame()
    bus.write(body + P.PAGE_GAME_RECORDS, records_raw({P.RECORD_TIMES_ENTERED_HALL_OF_FAME: 1}))
    assert pm.hall_of_fame_count() == 1 and pm.entered_hall_of_fame()
    bus.write(body + P.PAGE_GAME_RECORDS, records_raw({}))
    bus.w8(body + P.TRAINER + 0x1D, 1)                            # TrainerInfo.isMainStoryCleared
    assert pm.entered_hall_of_fame()
    bus.w8(body + P.TRAINER + 0x1D, 0)
    bus.w8(body + P.PAGE_VARS_FLAGS + P.VF_FLAGS + P.FLAG_GAME_COMPLETED // 8, 1 << (P.FLAG_GAME_COMPLETED % 8))
    assert pm.entered_hall_of_fame()
    bus.w32(SAVE + P.SAVE_BODY + P.PAGE_FIELD_PLAYER, P.HALL_OF_FAME_MAP)
    bus.w32(FS + P.FS_LOCATION, SAVE + P.SAVE_BODY + P.PAGE_FIELD_PLAYER)
    assert pm.in_hall_of_fame()


# --- party / box / day care ----------------------------------------------------------------------------------------
def test_party_checked_rejects_decrypted_party_section():
    rec = party_rec(mon(1))
    bus, pm = world([rec])
    assert pm.party_checked() == [rec] and P.party_section_ok(rec)
    d = bytearray(rec)                                            # party section decrypted in place, box part fine
    d[136:] = pk4._crypt(bytes(d[136:]), struct.unpack_from("<I", d)[0])
    set_party(pm, [bytes(d)])
    assert pk4.valid(bytes(d)) and pm.party_checked() is None


def test_current_box_records_and_index():
    bus, pm = world([party_rec(mon(1)), party_rec(mon(2))])
    a, b = mon(10), mon(11)
    pm.write_box_slot(0, 3, a)
    pm.write_box_slot(0, 17, b)
    pm.write_box_slot(1, 0, mon(12))
    assert pm.current_box_records() == [a, b]
    bus.w32(pm.pc_boxes_addr(), 1)
    assert [pk4.mon_key(r) for r in pm.current_box_records()] == [pk4.mon_key(mon(12))]


def test_move_party_to_box_restores_pp_and_shifts():
    recs = [party_rec(mon(i)) for i in range(4)]
    pk = pk4.PK4(pk4.decrypt(recs[1]))
    pk.pp = [1, 2, 0, 0]
    pk.pp_ups = [3, 0, 0, 0]
    recs[1] = pk.encrypted()
    bus, pm = world(recs)
    pm.write_box_slot(0, 0, mon(20))
    assert stable(pm).move_party_to_box(1)
    assert pm.party_count() == 3 and pm.party_raw() == [recs[0], recs[2], recs[3]]
    assert pm.read(pm.save_body() + P.PAGE_PARTY + 8 + 3 * 236, 236) == pk4.EMPTY_PARTY
    box = pm.current_box_records()
    stored = pk4.PK4(pk4.decrypt(box[1]))
    assert len(box) == 2 and pk4.mon_key(box[1]) == pk4.mon_key(recs[1])
    assert stored.pp[:2] == [35 + 35 * 3 // 5, 40]                 # TACKLE 35 with 3 PP Ups, GROWL 40
    assert box[1] == pk4.encrypt(bytes(bytearray(stored.plain())))


def test_move_party_to_box_refusals():
    recs = [party_rec(mon(i)) for i in range(3)]
    bus, pm = world(recs[:1])
    assert not stable(pm).move_party_to_box(0) and not stable(pm).move_party_to_box(5)
    set_party(pm, [recs[0], party_rec(mon(5), hp=0), party_rec(mon(6, egg=True))])
    assert not stable(pm).move_party_to_box(0)                            # nobody else could fight
    assert stable(pm).move_party_to_box(1) and pm.party_count() == 2      # a fainted one can go
    mail = pk4.PK4(pk4.decrypt(recs[1]))
    mail.item = 137
    set_party(pm, [recs[0], mail.encrypted(), recs[2]])
    assert not stable(pm).move_party_to_box(1)
    capsule = pk4.PK4(pk4.decrypt(recs[1]))
    capsule.d[0x8D] = 3
    set_party(pm, [recs[0], capsule.encrypted(), recs[2]])
    assert not stable(pm).move_party_to_box(1)
    bad = bytearray(recs[1])
    bad[40] ^= 0xFF                                               # mid-update: no forced move
    set_party(pm, [recs[0], bytes(bad), recs[2]])
    assert not stable(pm).move_party_to_box(0)
    pm_norom = P.PlatinumMemory(bus)
    with pytest.raises(RuntimeError):
        pm_norom.move_party_to_box(0)


def test_forced_moves_need_a_state_seen_on_an_earlier_frame():
    recs = [party_rec(mon(i)) for i in range(3)]
    bus, pm = world(recs)
    pm.write_box_slot(0, 0, mon(20))
    assert not pm.move_party_to_box(1) and pm.party_count() == 3      # first look: only recorded
    assert not pm.move_party_to_box(1) and pm.party_count() == 3      # same frame again: no evidence
    bus.frame += 1
    assert pm.move_party_to_box(1) and pm.party_count() == 2          # identical on a later frame: done
    bus.frame += 1
    assert not pm.move_box_to_party(0)                               # state just changed: wait
    pm.observe_stable()
    bus.frame += 1
    pm.write_box_slot(0, 5, mon(21))                                 # the game changed something meanwhile
    assert not pm.move_box_to_party(0) and pm.party_count() == 2
    bus.frame += 1
    assert pm.move_box_to_party(0) and pm.party_count() == 3
    # observe_stable() is what the referee's view calls each check; the move then works at once
    bus.frame += 1
    pm.observe_stable()
    assert not pm.observe_stable()                                   # same frame
    bus.frame += 1
    assert pm.observe_stable() and pm.move_party_to_box(0)
    d = bytearray(pm.party_raw()[0])                                 # mid-update -> never stable
    d[40] ^= 1
    set_party(pm, [bytes(d)] + pm.party_raw()[1:])
    assert not stable(pm).observe_stable()


def test_move_party_to_box_full_box_goes_to_next_and_switches():
    recs = [party_rec(mon(i)) for i in range(3)]
    bus, pm = world(recs)
    bus.w32(pm.pc_boxes_addr(), 17)
    for i in range(30):
        pm.write_box_slot(17, i, mon(100 + i))
    pm.write_box_slot(0, 0, mon(200))
    assert stable(pm).move_party_to_box(2)
    assert pm.current_box() == 0 and [pk4.mon_key(r) for r in pm.current_box_records()] == \
        [pk4.mon_key(mon(200)), pk4.mon_key(recs[2])]
    for b in range(18):
        for i in range(30):
            pm.write_box_slot(b, i, mon(1000 + b * 30 + i))
    assert not stable(pm).move_party_to_box(1) and pm.party_count() == 2


def test_move_party_to_box_shaymin_land_form():
    sky = pk4.PK4(pk4.decrypt(mon(1, species=P.SPECIES_SHAYMIN)))
    sky.form = 1
    recs = [party_rec(mon(2)), pk4.to_party(sky.box_bytes(), ROM)]
    bus, pm = world(recs)
    assert stable(pm).move_party_to_box(1)
    assert pk4.PK4(pk4.decrypt(pm.current_box_records()[0])).form == 0


def test_move_box_to_party():
    recs = [party_rec(mon(i), hp=3) for i in range(2)]
    bus, pm = world(recs)
    pm.write_box_slot(0, 4, mon(30, level=12))
    pm.write_box_slot(0, 9, mon(31, level=15))
    assert stable(pm).move_box_to_party(1)
    assert pm.party_count() == 3 and pm.party_raw()[2] == pk4.to_party(mon(31, level=15), ROM)
    assert pm.box_raw(0)[9] == pk4.EMPTY_BOX and [pk4.mon_key(r) for r in pm.current_box_records()] == \
        [pk4.mon_key(mon(30))]
    assert not stable(pm).move_box_to_party(1) and not stable(pm).move_box_to_party(-1)
    set_party(pm, [party_rec(mon(i)) for i in range(6)])
    assert not stable(pm).move_box_to_party(0)


def test_daycare_records():
    bus, pm = world()
    dc = pm.save_body() + P.PAGE_DAYCARE
    bus.write(dc, pk4.EMPTY_BOX)
    bus.write(dc + P.DAYCARE_MON_SIZE, pk4.EMPTY_BOX)
    assert pm.daycare_records() == []
    bus.write(dc + P.DAYCARE_MON_SIZE, mon(7))
    assert pm.daycare_records() == [mon(7)]
    bus.write(dc, mon(8))
    assert pm.daycare_records() == [mon(8), mon(7)]
    bad = bytearray(mon(8))
    bad[50] ^= 1                                                   # mid-decryption / corrupt: "unknown"
    bus.write(dc, bytes(bad))
    assert pm.daycare_records() is None


# --- keys / info / areas ---------------------------------------------------------------------------------------
def test_mon_key_and_info():
    box = mon(5, species=396, level=7, nick="BIRD")
    party = party_rec(box)
    k = P.mon_key(box)
    assert k == P.mon_key(party) == "%08x%04x%04x" % (0x10000 + 5 * 7919, TID, SID)
    i = P.mon_info(party)
    assert i == {"key": k, "dex": 396, "hp": pk4.PK4(pk4.decrypt(party)).hp, "egg": False, "nick": "BIRD", "level": 7}
    assert P.mon_info(box)["hp"] == -1 and P.mon_info(pk4.EMPTY_BOX) is None and P.mon_info(b"\x01" * 136) is None
    egg = mon(6, egg=True)
    assert P.mon_info(egg)["egg"] is True
    evolved = pk4.PK4(pk4.decrypt(box))
    evolved.species = 397                                          # evolution / level-up / deposit keep the key
    evolved.exp += 1000
    assert P.mon_key(evolved.box_bytes()) == k


def test_areas():
    assert len(platinum_areas.AREAS) == P.NUM_MAP_HEADERS
    assert P.area_of(342) == "route_201" and P.area_name("route_201") == "Route 201"
    assert P.area_of(437) == "solaceon_town" and P.location_of(437) == "pokemon_day_care"
    assert {P.area_of(m) for m in range(207, 220)} == {"mt_coronet"}
    assert {P.area_of(m) for m in range(357, 362)} == {"lost_tower"} and P.area_name("lost_tower") == "Lost Tower"
    assert P.area_of(59) == "oreburgh_city" and P.area_of(11) == "jubilife_city"
    assert P.area_of(187) == "pokemon_league" and P.area_of(5000) == "map_1388"
    assert P.area_name("route_201*2") == "Route 201" and P.area_name("weird_key") == "Weird Key"
    assert all(P.area_name(a) for a in platinum_areas.AREAS)
    # every area is one ROM location label, except buildings merged into the town/route they stand in
    labels = {}
    for m, a in enumerate(platinum_areas.AREAS):
        labels.setdefault(a, set()).add(P.location_of(m))
    assert all(len(v) == 1 or a in v for a, v in labels.items() if a != "lost_tower")


def test_area_keys_fit_the_server():
    import re
    server = os.path.join(os.path.dirname(__file__), "..", "..", "server", "yellowlink_server", "challenges.py")
    pattern = re.search(r'AREA_RE = re.compile\(r"(.+?)"\)', open(server, encoding="utf-8").read()).group(1)
    area_re = re.compile(pattern)
    assert all(area_re.match(a) for a in platinum_areas.AREAS)
    assert all(len(a) <= 37 for a in platinum_areas.AREAS)           # room for a shiny-clause "*nn" suffix
    assert P.area_of(352) == "unused_eterna_gate_route_206"


class FormRom:
    """FakeRom + form base stats (like ndsrom.PlatinumData.form_base_stats)."""
    def __getattr__(self, name):
        return getattr(ROM, name)

    def form_base_stats(self, dex, form=0):
        if dex == P.SPECIES_SHAYMIN and form == 1:
            return (100, 103, 75, 127, 120, 75)
        return ROM.base_stats(dex)


def test_calc_stats_uses_the_form():
    land = pk4.PK4(pk4.decrypt(mon(1, species=P.SPECIES_SHAYMIN, level=50)))
    sky = pk4.PK4(land.plain())
    sky.form = 1
    rom = FormRom()
    assert pk4.calc_stats(land, rom, 50) == pk4.calc_stats(land, ROM, 50)
    st = pk4.calc_stats(sky, rom, 50)
    assert st != pk4.calc_stats(land, rom, 50)
    iv, ev, nat = sky.ivs, sky.evs, sky.nature
    spd = (2 * 127 + iv[3] + ev[3] // 4) * 50 // 100 + 5
    m = pk4.nature_mult(nat, 3)
    assert st[3] == (spd * 110 // 100 if m > 0 else spd * 90 // 100 if m < 0 else spd)
    party = pk4.PK4(pk4.decrypt(pk4.to_party(sky.box_bytes(), rom)))
    assert party.stats == st and party.hp == st[0]


# --- real ROM data ---------------------------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.exists(PT_ROM), reason="needs dev/states/platinum.nds")
def test_real_rom_withdraw_and_names():
    rom = ndsrom.platinum(PT_ROM)
    bus, pm = world([party_rec(mon(1))], rom=rom)
    starly = native(species=396, pid=0xBEEF, tid=TID, sid=SID, level=4)
    pm.write_box_slot(0, 0, starly)
    assert stable(pm).move_box_to_party(0)
    p = pk4.PK4(pk4.decrypt(pm.party_raw()[1]))
    assert p.species == 396 and p.party_level == p.level(rom) and p.hp == p.stats[0] > 0
    assert P.area_name("mt_coronet") == rom.location_name(50) == "Mt. Coronet"


@pytest.mark.skipif(not os.path.exists(PT_ROM), reason="needs dev/states/platinum.nds")
@pytest.mark.parametrize("dex,form,index,base", [
    (386, 0, 386, (50, 150, 50, 150, 150, 50)), (386, 1, 496, (50, 180, 20, 150, 180, 20)),
    (386, 3, 498, (50, 95, 90, 180, 95, 90)), (413, 2, 500, (60, 69, 95, 36, 69, 95)),
    (487, 1, 501, (150, 120, 100, 90, 120, 100)), (492, 1, 502, (100, 103, 75, 127, 120, 75)),
    (479, 5, 507, (50, 65, 107, 86, 105, 107)), (479, 6, 479, (50, 50, 77, 91, 95, 77)), (25, 1, 25, None)])
def test_real_rom_form_personal_entries(dex, form, index, base):
    rom = ndsrom.platinum(PT_ROM)
    assert rom.personal_index(dex, form) == index
    assert rom.form_base_stats(dex, form) == (base or rom.base_stats(dex))
    pk = pk4.PK4(pk4.decrypt(native(species=dex, pid=0x4321, tid=TID, sid=SID, level=40)))
    pk.form = form
    st = pk4.calc_stats(pk, rom, 40)
    assert st[1] == pk4.calc_stats(pk, type("R", (), {"base_stats": lambda self, d: rom.form_base_stats(dex, form)})(),
                                   40)[1]
