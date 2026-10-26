"""Complete Sinnoh swarm rotation (sinnoh_swarm.py): the shared 2-hour pick and the writes to a fake save."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import sinnoh_swarm as sw  # noqa: E402

BODY = 0x02200000
BASE = sw.PAGE_SPECIAL_ENCOUNTERS


class Bus:
    def __init__(self):
        self.mem = bytearray(0x10000)
        self.writes = []

    def r8(self, a):
        return self.mem[a - BODY]

    def r32(self, a):
        return struct.unpack_from("<I", self.mem, a - BODY)[0]

    def write(self, a, data):
        self.writes.append((a, bytes(data)))
        self.mem[a - BODY:a - BODY + len(data)] = data


class PM:
    def __init__(self, enabled=True, value=0x352CA612, safe=True):
        self.m = Bus()
        self.safe = safe
        struct.pack_into("<I", self.m.mem, BASE + sw.SE_SWARM_DAILY, value)
        self.m.mem[BASE + sw.SE_SWARM_ENABLED] = int(enabled)

    def save_body(self):
        return BODY

    def is_safe(self):
        return self.safe


def test_pret_table_and_layout():
    assert sw.NUM_SWARMS == 22 and len(set(sw.SWARM_MAPS)) == 22
    assert sw.SWARM_MAPS[0] == 342 and sw.SWARM_MAPS[-2:] == (200, 203)     # Route 201 ... Eterna Forest
    # SpecialEncounter: 2 ints, trophy garden 8, honey trees 4+21*8, radar chains 12, recent routes 8,
    # roamers 6*20, roamer route indexes 6 -> swarmEnabled
    assert sw.SE_SWARM_ENABLED == 8 + 8 + (4 + 21 * 8) + 12 + 8 + 6 * 20 + 6


def test_rotation_is_fixed_fair_and_never_repeats():
    picks = [sw.pick(s) for s in range(0, 22 * 60)]
    assert all(0 <= p < 22 for p in picks)
    assert all(a != b for a, b in zip(picks, picks[1:]))                   # never the same swarm twice in a row
    for c in range(60):                                                    # every cycle visits all 22
        assert sorted(picks[22 * c:22 * c + 22]) == list(range(22))
    # the same for everybody: a pure function of time (pinned so a change of the formula is noticed)
    assert sw.slot_of(7199.9) == 0 and sw.slot_of(7200) == 1
    cur = sw.current(1793052000.5)
    assert cur["slot"] == 249035 and cur["ends"] == 249036 * 7200 and cur["map"] == sw.SWARM_MAPS[cur["index"]]


def test_pinned_values():
    # the same for everybody: a pure function of time. If this fails, players on different YellowLink versions
    # would see different swarms - change the formula/SEED only on purpose.
    assert [sw.pick(s) for s in range(249035, 249041)] == [10, 11, 19, 3, 21, 15]


def test_rotator_writes_and_announces():
    t = [249035 * 7200 + 10.0]
    pm = PM(enabled=True)
    r = sw.SwarmRotator(pm, every=1, clock=lambda: t[0])
    news = r.step()
    want = sw.pick(249035)
    assert sw.swarm_value(pm) == want and news == sw.current(t[0])
    assert r.step() is None and len(pm.m.writes) == 1                       # nothing more in the same slot
    struct.pack_into("<I", pm.m.mem, BASE + sw.SE_SWARM_DAILY, 0xDEADBEEF)  # the game's own daily change
    assert r.step() is None and sw.swarm_index(pm) == want                  # put back, not announced again
    t[0] += 7200
    news = r.step()
    assert news["slot"] == 249036 and sw.swarm_index(pm) == sw.pick(249036)
    # marsh value (the int before) and everything else untouched
    assert all(a == BODY + BASE + sw.SE_SWARM_DAILY for a, _ in pm.m.writes)


def test_rotator_disabled_swarms_and_safety():
    t = 249035 * 7200.0
    pm = PM(enabled=False, safe=False)
    r = sw.SwarmRotator(pm, every=1, clock=lambda: t)
    assert r.step() is None and pm.m.writes == []                          # not safe: nothing
    pm.safe = True
    assert r.step() is None and sw.swarm_index(pm) == sw.pick(249035)      # written, but no toast: swarms off
    pm.m.mem[BASE + sw.SE_SWARM_ENABLED] = 1                                 # the sister turned them on
    assert r.step()["slot"] == 249035
    r2 = sw.SwarmRotator(pm, every=30, clock=lambda: t)
    assert [r2.step() for _ in range(29)] == [None] * 29 and r2.step() is not None


def test_errors_never_raise():
    class Broken(PM):
        def is_safe(self):
            raise RuntimeError("boom")
    assert sw.SwarmRotator(Broken(), every=1).step() is None
    assert sw.swarm_value(type("X", (), {"save_body": lambda self: 0})()) is None


def test_species_of():
    tables = [bytes(424)] * 3
    t = bytearray(424)
    struct.pack_into("<II", t, 100, 84, 84)
    tables[2] = bytes(t)
    assert sw.species_of(342, lambda m: 2, tables) == 84
    assert sw.species_of(342, lambda m: None, tables) is None
    assert sw.species_of(342, lambda m: 0, tables) is None
