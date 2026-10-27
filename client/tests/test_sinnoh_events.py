"""Complete Sinnoh event unlocks (sinnoh_events.py) on a fake save in RAM: what is written, when, and only once."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import sinnoh_events as ev  # noqa: E402

BODY = 0x02200000


class Bus:
    def __init__(self):
        self.mem = bytearray(0x20000)
        self.writes = []

    def _o(self, a):
        assert BODY <= a < BODY + len(self.mem), hex(a)
        return a - BODY

    def read(self, a, n):
        return bytes(self.mem[self._o(a):self._o(a) + n])

    def r8(self, a):
        return self.mem[self._o(a)]

    def r16(self, a):
        return struct.unpack_from("<H", self.mem, self._o(a))[0]

    def r32(self, a):
        return struct.unpack_from("<I", self.mem, self._o(a))[0]

    def write(self, a, data):
        self.writes.append((a, bytes(data)))
        self.mem[self._o(a):self._o(a) + len(data)] = data


class PM:
    def __init__(self, natdex=False, safe=True):
        self.m = Bus()
        self.safe = safe
        struct.pack_into("<I", self.m.mem, ev.PAGE_POKEDEX, ev.POKEDEX_MAGIC)
        self.m.mem[ev.PAGE_POKEDEX + ev.POKEDEX_NATIONAL] = int(natdex)

    def save_body(self):
        return BODY

    def is_safe(self):
        return self.safe

    def key(self, i):
        return struct.unpack_from("<HH", self.m.mem, ev.PAGE_BAG + ev.BAG_KEY_ITEMS + 4 * i)

    def set_key(self, i, item, qty):
        struct.pack_into("<HH", self.m.mem, ev.PAGE_BAG + ev.BAG_KEY_ITEMS + 4 * i, item, qty)

    def set_flag(self, n, on=True):
        o = ev.PAGE_VARS_FLAGS + ev.VF_FLAGS + n // 8
        self.m.mem[o] = (self.m.mem[o] | (1 << n % 8)) if on else (self.m.mem[o] & ~(1 << n % 8))

    def set_caught(self, species):
        i = species - 1
        self.m.mem[ev.PAGE_POKEDEX + ev.POKEDEX_CAUGHT + i // 8] |= 1 << i % 8

    def legends(self):
        self.set_flag(ev.FLAG_CAUGHT_DIALGA)
        self.set_flag(ev.FLAG_CAUGHT_PALKIA)
        return self

    def var(self, v):
        return struct.unpack_from("<H", self.m.mem, ev.PAGE_VARS_FLAGS + 2 * (v - 0x4000))

    def set_var(self, v, x):
        struct.pack_into("<H", self.m.mem, ev.PAGE_VARS_FLAGS + 2 * (v - 0x4000), x)


def run(u, frames):
    out = []
    for _ in range(frames):
        out += u.step()
    return out


def test_constants_match_pret():
    # pret generated/items.txt, vars_flags.txt, src/system_vars.c sDistributionEventMagicNumbers
    assert [(e.id, e.item, e.var, e.magic) for e in ev.EVENTS] == [
        ("member_card", 454, 0x4043, 0x1209), ("oaks_letter", 452, 0x4044, 0x1112),
        ("azure_flute", 455, 0x4045, 0x1123), ("secret_key", 467, 0x4046, 0x1103)]
    assert ev.BAG_KEY_ITEMS == 0x294 and ev.PAGE_BAG + ev.BAG_KEY_ITEMS == 0x8C4
    assert ev.VAR_SHAYMIN_EVENT_STATE == 0x4057


def test_nothing_before_national_dex():
    pm = PM(natdex=False)
    u = ev.EventUnlocker(pm, every=1)
    assert ev.pending(pm) == [] and run(u, 10) == [] and pm.m.writes == []
    struct.pack_into("<I", pm.m.mem, ev.PAGE_POKEDEX, 0)          # garbage Pokédex block -> no National Dex
    pm.m.mem[ev.PAGE_POKEDEX + ev.POKEDEX_NATIONAL] = 1
    assert not ev.national_dex(pm)


def test_gives_all_four_like_the_deliveryman():
    pm = PM(natdex=True).legends()
    pm.set_key(0, 428, 1)                    # an existing key item stays where it is
    u = ev.EventUnlocker(pm, every=1)
    assert run(u, 1) == []                   # first look: noted, waits for a second identical look
    got = run(u, 1)
    assert [e.id for e in got] == ["member_card", "oaks_letter", "azure_flute", "secret_key"]
    assert [pm.key(i) for i in range(5)] == [(428, 1), (454, 1), (452, 1), (455, 1), (467, 1)]
    assert pm.var(0x4043) == (0x1209,) and pm.var(0x4044) == (0x1112,) and pm.var(0x4045) == (0x1123,)
    assert pm.var(0x4046) == (0x1103,) and pm.var(0x4057) == (1,)
    assert all(ev.given(pm, e) for e in ev.EVENTS) and ev.pending(pm) == []
    n = len(pm.m.writes)
    assert run(u, 50) == [] and len(pm.m.writes) == n      # idempotent: nothing more


def test_conditions_per_event():
    # Rotom caught before the National Dex: only the Secret Key
    pm = PM(natdex=False)
    pm.set_caught(ev.SPECIES_ROTOM)
    assert [e.id for e in ev.pending(pm)] == ["secret_key"]
    u = ev.EventUnlocker(pm, every=1)
    assert [e.id for e in run(u, 3)] == ["secret_key"]
    pm.set_caught(ev.SPECIES_ROTOM - 1)      # neighbouring bits don't matter
    assert ev.caught(pm, ev.SPECIES_ROTOM) and not ev.caught(pm, 480) and ev.caught(pm, 478)
    # Dialga + Palkia flags without the National Dex: nothing (the flute needs both)
    pm2 = PM(natdex=False).legends()
    assert ev.pending(pm2) == []
    # National Dex: Member Card, Oak's Letter, Secret Key - the flute waits for Dialga AND Palkia
    pm3 = PM(natdex=True)
    assert [e.id for e in ev.pending(pm3)] == ["member_card", "oaks_letter", "secret_key"]
    pm3.set_flag(ev.FLAG_CAUGHT_DIALGA)
    assert "azure_flute" not in [e.id for e in ev.pending(pm3)]
    pm3.set_flag(ev.FLAG_CAUGHT_PALKIA)
    assert "azure_flute" in [e.id for e in ev.pending(pm3)]
    u3 = ev.EventUnlocker(pm3, every=1)
    assert len(run(u3, 3)) == 4 and ev.pending(pm3) == []


def test_only_when_safe_and_keeps_progress():
    pm = PM(natdex=True, safe=False).legends()
    u = ev.EventUnlocker(pm, every=1)
    assert run(u, 20) == [] and pm.m.writes == []
    pm.safe = True
    pm.set_var(0x4057, 2)                    # Shaymin event already further along: never set back to 1
    pm.set_key(3, 452, 1)                    # already has Oak's Letter (e.g. from an older run of this code)
    got = run(u, 2)
    assert len(got) == 4
    assert pm.var(0x4057) == (2,)
    assert sum(1 for i in range(50) if pm.key(i)[0] == 452) == 1
    assert pm.key(3) == (452, 1)


def test_every_and_pocket_full():
    pm = PM(natdex=True).legends()
    for i in range(ev.KEY_ITEM_POCKET_SIZE):
        pm.set_key(i, 400 + i, 1)            # full pocket (450 not in the list of our items)
    pm.set_key(10, 0, 0)                     # one free slot
    u = ev.EventUnlocker(pm, every=30)
    assert run(u, 29) == []
    assert [e.id for e in run(u, 31)] == ["member_card"]    # frame 30: first look, frame 60: give (1 free slot)
    got = ev.pending(pm)
    assert [e.id for e in got] == ["oaks_letter", "azure_flute", "secret_key"]   # member card took the slot
    assert pm.key(10) == (454, 1)
    assert ev.give(pm, ev.EVENTS_BY_ID["azure_flute"]) is False                   # no room: nothing written
    assert pm.var(0x4045) == (0,)


def test_slot_with_item_but_zero_quantity_is_reused():
    pm = PM(natdex=True)
    pm.set_key(0, 455, 0)
    assert ev.give(pm, ev.EVENTS_BY_ID["azure_flute"])
    assert pm.key(0) == (455, 1) and pm.key(1) == (0, 0)


def test_errors_never_raise():
    class Broken(PM):
        def is_safe(self):
            raise RuntimeError("boom")
    u = ev.EventUnlocker(Broken(natdex=True).legends(), every=1)
    assert run(u, 3) == []
