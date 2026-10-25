import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import pcdb, text  # noqa: E402


class FakeMem:
    """Flat 64 KB address space + cartridge RAM banks addressed as mem[bank, addr]."""

    def __init__(self):
        self.ram = bytearray(0x10000)
        self.banks = {b: bytearray(0x2000) for b in range(4)}

    def __getitem__(self, k):
        if isinstance(k, tuple):
            b, a = k
            return self.banks[b][a - 0xA000]
        return self.ram[k]

    def __setitem__(self, k, v):
        if isinstance(k, tuple):
            b, a = k
            self.banks[b][a - 0xA000] = v
        else:
            self.ram[k] = v


class FakeRom:
    def dex_no(self, species):
        return species  # good enough for tests

    def species_for_dex(self, dex):
        return dex if dex <= 151 else 0


CHIKORITA_G2 = pcdb._b64(bytes([152]) + bytes(30) + bytes([12]) + text.encode("ASH") + text.encode("CHIKORITA") + b"\x00")


def rec(species, nick, ot="ASH", otid=1, level=10):
    d = bytearray(33)
    d[0], d[3], d[12], d[13] = species, level, otid >> 8, otid & 0xFF
    return bytes(d) + text.encode(ot) + text.encode(nick)


def setup_game(boxes, cur=0):
    mem = FakeMem()
    mem[pcdb.G1_CUR_BOX] = 0x80 | cur
    pc = pcdb.Gen1PC(mem, FakeRom())
    full = [list(b) for b in boxes] + [[] for _ in range(12 - len(boxes))]
    pc.write_boxes(full)
    return mem, pc


def test_box_codec_roundtrip():
    mons = [rec(0x54, "PIKA"), rec(0xA5, "RATTY")]
    assert pcdb.g1_parse_box(pcdb.g1_build_box(mons)) == mons
    assert pcdb.g1_parse_box(pcdb.g1_build_box([])) == []


def test_write_boxes_checksums_and_placement():
    mem, pc = setup_game([[rec(1, "A")], [], [rec(2, "B")]] + [[]] * 4 + [[rec(3, "C")]], cur=2)
    boxes = pc.read_boxes()
    assert boxes[0][0][0] == 1 and boxes[2][0][0] == 2 and boxes[7][0][0] == 3
    # current box lives in WRAM
    assert mem[pcdb.G1_WBOX] == 1 and mem[pcdb.G1_WBOX + 1] == 2
    for bank in (2, 3):
        for k in range(6):
            bank_, addr = pc.slot((bank - 2) * 6 + k)
            raw = bytes(mem[bank_, addr + i] for i in range(pcdb.G1_BOX_BYTES))
            assert mem[bank, pcdb.G1_BOX_SUMS + k] == pcdb.checksum(raw)
        whole = bytes(mem[bank, 0xA000 + i] for i in range(6 * pcdb.G1_BOX_BYTES))
        assert mem[bank, pcdb.G1_BANK_SUM] == pcdb.checksum(whole)


def test_import_deposit_withdraw_commit(tmp_path):
    mem, pc = setup_game([[rec(0x54, "PIKA"), rec(0xA5, "RATTY")]])
    sync = pcdb.PCSync(tmp_path / "store.json")
    view = sync.open_session("yellow", pc, trust_game=True)
    assert sync.count() == 2 and "yellow" in sync.working["linked"]
    pc.write_boxes(view)
    # player withdraws RATTY and deposits a new mon
    boxes = pc.read_boxes()
    boxes[0] = [boxes[0][0], rec(0x24, "PIDGE")]
    pc.write_boxes(boxes)
    report = sync.close_session("yellow", pc)
    assert report["added"] == 1 and report["removed"] == 1
    names = [e["nick"] for e in sync.working["boxes"][0]]
    assert names == ["PIKA", "PIDGE"]
    # not committed yet: a fresh load sees nothing
    assert pcdb.PCSync(tmp_path / "store.json").count() == 0
    pushed = sync.commit()
    assert pushed and pushed["rev"] == 1
    again = pcdb.PCSync(tmp_path / "store.json")
    assert [e["nick"] for e in again.committed["boxes"][0]] == ["PIKA", "PIDGE"]


def test_rollback_discards_pending(tmp_path):
    mem, pc = setup_game([[rec(1, "A")]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    pc.write_boxes(sync.open_session("yellow", pc, True))
    sync.close_session("yellow", pc)
    sync.commit()
    pc.write_boxes(sync.open_session("yellow", pc, True))
    pc.write_boxes([[]] * 12)            # player withdraws everything...
    sync.close_session("yellow", pc)
    assert sync.count() == 0 and sync.pending
    sync.rollback()                      # ...but the game crashed before the next snapshot
    assert sync.count() == 1 and not sync.pending


def test_standins_cannot_leave(tmp_path):
    mem, pc = setup_game([[]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    sync.working["boxes"][0].append({"id": "johto1", "origin": "crystal", "dex": 152, "level": 12,
                                     "nick": "CHIKORITA", "ot": "ASH", "otid": 1, "gen1": None, "gen2": CHIKORITA_G2})
    sync.working["linked"].append("yellow")
    sync.last_view["yellow"] = [[] for _ in range(12)]
    view = sync.open_session("yellow", pc, True)
    standin = view[0][0]
    assert pcdb.Gen1PC.is_standin(standin) and text.decode(standin[44:]) == "?CHIKORITA"
    pc.write_boxes(view)
    pc.write_boxes([[]] * 12)            # player withdraws (or releases) the stand-in
    report = sync.close_session("yellow", pc, party_records=[standin])
    assert report["standins_returned"] == ["CHIKORITA"] and report["standin_party_slots"] == [0]
    assert sync.working["boxes"][0][0]["id"] == "johto1" and sync.working["boxes"][0][0]["gen2"] == CHIKORITA_G2


def test_identical_twins_and_moves(tmp_path):
    twin = rec(0x54, "PIKA")
    mem, pc = setup_game([[twin, twin], []])
    sync = pcdb.PCSync(tmp_path / "s.json")
    pc.write_boxes(sync.open_session("yellow", pc, True))
    ids = [e["id"] for e in sync.working["boxes"][0]]
    boxes = pc.read_boxes()
    boxes[1] = [boxes[0].pop()]          # move one twin to box 2
    pc.write_boxes(boxes)
    report = sync.close_session("yellow", pc)
    assert report["added"] == 0 and report["removed"] == 0
    assert sorted([sync.working["boxes"][0][0]["id"], sync.working["boxes"][1][0]["id"]]) == sorted(ids)


def test_trade_delta_applies_once_and_traded_mon_cannot_come_back(tmp_path):
    mem, pc = setup_game([[rec(0x54, "PIKA"), rec(0xA5, "RATTY")]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    pc.write_boxes(sync.open_session("yellow", pc, True))
    sync.close_session("yellow", pc)
    sync.commit()
    ratty = next(e for e in sync.committed["boxes"][0] if e["nick"] == "RATTY")
    assert [e["nick"] for _, e in sync.offerable()] == ["PIKA", "RATTY"]
    got = {"id": "new1", "origin": "crystal", "dex": 152, "level": 9, "nick": "LEAFY", "ot": "KRIS", "otid": 5,
           "gen1": None, "gen2": None}
    delta = {"trade_id": "t1", "remove": [ratty["id"]], "add": [got], "srv_rev": 7}
    assert sync.apply_trade(delta) and not sync.apply_trade(delta)
    assert sync.server_rev == 7 and "t1" in sync.committed["trades"]
    assert [e["nick"] for e in sync.working["boxes"][0]] == ["PIKA", "LEAFY"]
    # the game still shows RATTY (it hasn't opened the PC since): opening the PC must not re-import it
    view = sync.open_session("yellow", pc, True)
    names = [text.decode(r[44:]) for r in view[0]]
    assert "RATTY" not in names and sync.count() == 2
    assert not sync.offerable()                     # PC open in-game: nothing can be traded


def test_merge_keeps_both_computers_changes(tmp_path):
    """Laptop and desktop share one store: each one's changes survive, nothing comes back from the dead."""
    a, b, c = (pcdb.empty_store() for _ in range(3))
    e = lambda i, n: {"id": i, "nick": n, "dex": 1, "level": 5, "gen1": None, "gen2": None}  # noqa: E731
    base = pcdb.empty_store()
    base["boxes"][0] = [e("p1", "PIKA"), e("r1", "RATTY")]
    sync = pcdb.PCSync(tmp_path / "s.json")
    sync.committed = copy.deepcopy(base)
    sync.working = copy.deepcopy(base)
    sync.synced = copy.deepcopy(base)
    sync.server_rev = 3
    sync.committed["boxes"][0].append(e("d1", "DEPOSITED_HERE"))   # this computer deposited one
    sync.working = copy.deepcopy(sync.committed)
    remote = copy.deepcopy(base)                                   # the other computer:
    remote["boxes"][0] = [e("p1", "PIKA"), e("o1", "OTHER_PC")]    # withdrew RATTY, deposited OTHER_PC
    remote["srv_rev"] = 5
    assert sync.merge_remote(remote)
    names = sorted(x["nick"] for x in sync.committed["boxes"][0])
    assert names == ["DEPOSITED_HERE", "OTHER_PC", "PIKA"] and sync.server_rev == 5
    assert "r1" in sync.committed["gone"]          # this computer's game PC must not re-import RATTY
    sync.pending = True
    assert not sync.merge_remote(remote) and sync.deferred is remote   # waits until nothing is pending
    del a, b, c


def test_twins_one_traded_away(tmp_path):
    twin = rec(0x54, "PIKA")
    mem, pc = setup_game([[twin, twin, rec(0xA5, "RATTY")]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    pc.write_boxes(sync.open_session("yellow", pc, True))
    sync.close_session("yellow", pc)
    sync.commit()
    first = sync.committed["boxes"][0][0]["id"]
    sync.apply_trade({"trade_id": "t", "remove": [first], "add": [], "srv_rev": 2})
    view = sync.open_session("yellow", pc, True)                   # the game still shows both twins
    assert [text.decode(r[44:]) for r in view[0]] == ["PIKA", "RATTY"] and sync.count() == 2


def test_locked_mon_withdrawn_during_unknown_trade_goes_back(tmp_path):
    mem, pc = setup_game([[rec(0x54, "PIKA"), rec(0xA5, "RATTY")]])
    sync = pcdb.PCSync(tmp_path / "s.json")
    pc.write_boxes(sync.open_session("yellow", pc, True))
    sync.close_session("yellow", pc)
    sync.commit()
    ratty = next(e for e in sync.committed["boxes"][0] if e["nick"] == "RATTY")
    sync.lock("t9", [ratty["id"]])
    assert [e["nick"] for _, e in sync.offerable()] == ["PIKA"]    # can't be offered twice
    pc.write_boxes(sync.open_session("yellow", pc, True))
    withdrawn = pc.read_boxes()[0][1]
    boxes = pc.read_boxes()
    boxes[0] = boxes[0][:1]                                        # the player withdraws RATTY anyway
    pc.write_boxes(boxes)
    report = sync.close_session("yellow", pc, party_records=[rec(0x99, "BULBA", otid=777), withdrawn])
    assert report["locked_returned"] == ["RATTY"] and report["standin_party_slots"] == [1]
    assert sync.count() == 2


def test_full_pc_trade_waits_in_incoming(tmp_path):
    sync = pcdb.PCSync(tmp_path / "s.json")
    filler = lambda i: {"id": f"f{i}", "nick": "F", "dex": 1, "level": 5, "gen1": None, "gen2": None}  # noqa: E731
    for b in range(pcdb.STORE_BOXES):
        sync.committed["boxes"][b] = [filler(b * 20 + k) for k in range(20)]
    sync.working = copy.deepcopy(sync.committed)
    gift = {"id": "g", "nick": "GIFT", "dex": 1, "level": 5, "gen1": None, "gen2": None}
    sync.apply_trade({"trade_id": "t", "remove": [], "add": [gift], "srv_rev": 1})
    assert pcdb.valid_store(sync.committed) and sync.committed["incoming"][0]["id"] == "g"
    assert all(len(b) <= 20 for b in sync.committed["boxes"])


def test_damaged_file_is_kept_and_overfull_repaired(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{ not json")
    sync = pcdb.PCSync(p)
    assert sync.count() == 0 and list(tmp_path.glob("s.bad-*"))
    st = pcdb.empty_store()
    st["boxes"][0] = [{"id": str(i), "nick": "X"} for i in range(21)]
    p.write_text(json.dumps({"store": st}))
    sync = pcdb.PCSync(p)
    assert len(sync.committed["boxes"][0]) == 20 and len(sync.committed["incoming"]) == 1
