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


def test_adopt_remote_only_when_idle(tmp_path):
    sync = pcdb.PCSync(tmp_path / "s.json")
    remote = pcdb.empty_store()
    remote["rev"] = 5
    sync.pending = True
    assert not sync.adopt_remote(remote)
    sync.pending = False
    assert sync.adopt_remote(remote) and sync.committed["rev"] == 5
