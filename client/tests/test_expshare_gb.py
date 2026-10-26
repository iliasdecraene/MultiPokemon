"""Exp Share All (Yellow / Crystal): the exp math and the hook state machine against a fake emulator (no ROM).

The real-ROM checks are in dev/e2e/expshare_gb.py; this file pins the arithmetic (verified there against the games)
and the order-of-hooks logic, including "off -> touches nothing" and "link battle -> touches nothing".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import crystal, game  # noqa: E402
from yellowlink.expshare_gb import (CrystalExpShare, YellowExpShare, boost, exp_award, non_participants,  # noqa: E402
                                    stat_exp_after)

Z = 0x80


# --- math ------------------------------------------------------------------------------------------------------------
def test_exp_award_matches_the_games():
    # numbers measured on the real ROMs (dev/e2e/expshare_gb.py)
    assert exp_award(55, 3) == 23                                       # Yellow wild Rattata L3
    assert exp_award(55, 3, share=True) == 11
    assert exp_award(55, 3, traded=True, share=True) == 17              # 23 * 1.5 = 34 -> 17
    assert exp_award(55, 3, divide_by=2) == 11                          # original split: 27 * 3 / 7
    assert exp_award(211, 66, trainer=True) == 2983                     # Yellow trainer
    assert exp_award(211, 66, trainer=True, share=True) == 1491
    assert exp_award(211, 66, traded=True, trainer=True, share=True) == 2237
    assert exp_award(57, 2) == 16                                       # Crystal wild Sentret L2
    assert exp_award(57, 2, lucky_egg=True, share=True) == 12
    assert exp_award(53, 3, trainer=True) == 33                         # Crystal trainer
    assert exp_award(53, 3, trainer=True, lucky_egg=True, share=True) == 24
    assert exp_award(28, 2) == 8 and exp_award(28, 2, divide_by=2) == 4  # Crystal Exp. Share data halved: 57 -> 28


def test_share_is_half_of_the_full_amount_after_boosts():
    for b in range(0, 256, 7):
        for lvl in (1, 2, 5, 37, 100):
            for tr in (False, True):
                for trainer in (False, True):
                    full = exp_award(b, lvl, traded=tr, trainer=trainer)
                    assert exp_award(b, lvl, traded=tr, trainer=trainer, share=True) == full // 2


def test_boost_is_16_bit():
    assert boost(100) == 150 and boost(101) == 151
    assert boost(0xFFFF) == (0xFFFF + 0x7FFF) & 0xFFFF


def test_stat_exp():
    assert stat_exp_after(10, 45) == 55
    assert stat_exp_after(10, 45, share=True) == 32
    assert stat_exp_after(10, 45, share=True, pokerus=True) == 54
    assert stat_exp_after(0xFFF0, 255) == 0xFFFF


def test_non_participants():
    assert non_participants(5, 0b00001, [10, 5, 5, 0, 7]) == 0b10110          # slot 3 fainted
    assert non_participants(6, 0b00011, [1, 1, 1, 1, 1, 1], eggs={5}) == 0b011100
    assert non_participants(1, 0b1, [3]) == 0
    assert non_participants(3, 0, [0, 0, 0]) == 0


# --- fake emulator ---------------------------------------------------------------------------------------------------
class Regs:
    def __init__(self):
        self.A = self.B = self.C = self.D = self.E = 0
        self._F = 0
        self.HL = 0
        self.SP = 0xDFF0
        self.PC = 0

    @property
    def F(self):
        return self._F

    @F.setter
    def F(self, v):
        self._F = v & 0xF0


class Mem:
    """Flat 64 KiB + ROM banks (bank, 4000-7FFF) + WRAM bank 1 for (1, D000-DFFF). strict: flat D000-DFFF raises."""

    def __init__(self, strict=False):
        self.flat = bytearray(0x10000)
        self.rom = {}
        self.wram1 = bytearray(0x1000)
        self.strict = strict

    def __getitem__(self, k):
        if isinstance(k, tuple):
            b, a = k
            if 0xD000 <= a < 0xE000:
                assert b == 1
                return self.wram1[a - 0xD000]
            return self.rom.get((b, a), 0)
        assert not (self.strict and 0xD000 <= k < 0xE000), "unbanked WRAMX access %04X" % k
        return self.flat[k]

    def __setitem__(self, k, v):
        if isinstance(k, tuple):
            b, a = k
            assert b == 1 and 0xD000 <= a < 0xE000
            self.wram1[a - 0xD000] = v & 0xFF
            return
        assert not (self.strict and 0xD000 <= k < 0xE000), "unbanked WRAMX access %04X" % k
        self.flat[k] = v & 0xFF


class FakePyBoy:
    def __init__(self, cls, strict=False):
        self.memory = Mem(strict)
        self.register_file = Regs()
        self.hooks = {}
        for name, (b, a) in cls.HOOKS.items():
            self.memory.rom[(b, a)] = cls.SIGNATURES[name]

    def hook_register(self, bank, addr, cb, ctx):
        assert (bank, addr) not in self.hooks
        self.hooks[(bank, addr)] = (cb, ctx, self.memory.rom[(bank, addr)])
        self.memory.rom[(bank, addr)] = 0xDB

    def hook_deregister(self, bank, addr):
        cb, ctx, orig = self.hooks.pop((bank, addr))
        self.memory.rom[(bank, addr)] = orig

    def run(self, cls, name):
        cb, ctx, _ = self.hooks[cls.HOOKS[name]]
        cb(ctx)


def snapshot(pb):
    r = pb.register_file
    return bytes(pb.memory.flat), bytes(pb.memory.wram1), (r.A, r.F, r.B, r.C, r.D, r.E, r.HL, r.SP, r.PC)


# --- Yellow ----------------------------------------------------------------------------------------------------------
Y = YellowExpShare


def yellow(enabled=True, link=False, party_hp=(20, 15, 10, 0, 7), participants=0b1, f_in=0):
    pb = FakePyBoy(Y)
    m = pb.memory
    m[Y.LINK_STATE] = 4 if link else 0
    m[Y.IS_IN_BATTLE] = 1
    m[Y.PARTY_GAIN_EXP_FLAGS] = participants
    m[Y.PARTY_COUNT] = len(party_hp)
    for i, hp in enumerate(party_hp):
        m[Y.PARTY_MONS + i * 44 + 1], m[Y.PARTY_MONS + i * 44 + 2] = hp >> 8, hp & 0xFF
    for i, v in enumerate((40, 45, 41, 56, 35)):
        m[Y.ENEMY_MON_BASE_STATS + i] = v
    pb.register_file.F = f_in
    flag = {"on": enabled}
    h = game.install_exp_share(pb, lambda: flag["on"])
    return pb, m, h, flag


def ret_target(pb, target=0x1234, sp=0xDFF0):
    """Return address on the stack (Yellow's stack is below DFFF, Crystal's in WRAM0 C000-C0FF)."""
    m, r = pb.memory, pb.register_file
    r.SP = sp
    m.flat[sp], m.flat[sp + 1] = target & 0xFF, target >> 8


def test_yellow_install_checks_signatures_and_remove_restores():
    pb = FakePyBoy(Y)
    pb.memory.rom[Y.HOOKS["amount"]] = 0x00                    # not the verified ROM
    with pytest.raises(RuntimeError):
        game.install_exp_share(pb, lambda: True)
    assert pb.hooks == {}
    pb, m, h, _ = yellow()
    assert all(m.rom[a] == 0xDB for a in Y.HOOKS.values()) and len(pb.hooks) == 6
    h.remove()
    assert pb.hooks == {} and all(m.rom[Y.HOOKS[n]] == Y.SIGNATURES[n] for n in Y.HOOKS)
    h.remove()                                                  # twice is harmless


def test_yellow_full_award_flow():
    pb, m, h, _ = yellow(f_in=0x00)                             # NZ: EXP.ALL in the bag
    r = pb.register_file
    pb.run(Y, "decide")
    assert r.F & Z and h.phase == 1                             # no EXP.ALL halving, pushed flags say "no EXP.ALL"
    ret_target(pb, 0x4321)
    pb.run(Y, "divide")
    assert r.PC == 0x4321 and r.SP == 0xDFF2                    # no split among participants
    m[0xFF97], m[0xFF98] = 0, 23
    pb.run(Y, "amount")
    assert (m[0xFF97], m[0xFF98]) == (0, 23)                    # participants: full
    pb.run(Y, "done")
    assert h.phase == 1
    m[Y.PARTY_GAIN_EXP_FLAGS] = 0b1                             # the game resets it to the mon in battle
    r.F = Z
    pb.run(Y, "second")                                         # at `ret z` after pop af
    assert not r.F & Z and h.shared == 0b10110 and h.phase == 2 # slots 1, 2, 4 (3 fainted)
    m[Y.PARTY_GAIN_EXP_FLAGS] = 0b11111                         # the game's EXP.ALL pass sets everyone
    m[Y.BOOST_EXP_BY_EXP_ALL] = 1
    pb.run(Y, "gain_entry")
    assert m[Y.PARTY_GAIN_EXP_FLAGS] == 0b10110 and m[Y.BOOST_EXP_BY_EXP_ALL] == 0
    assert [m[Y.ENEMY_MON_BASE_STATS + i] for i in range(5)] == [20, 22, 20, 28, 17]
    ret_target(pb, 0x5555)
    pb.run(Y, "divide")
    assert r.PC == 0x5555
    m[0xFF97], m[0xFF98] = 0x01, 0x03                           # 259 -> 129
    pb.run(Y, "amount")
    assert (m[0xFF97] << 8 | m[0xFF98]) == 129
    pb.run(Y, "done")
    assert h.phase == 0 and h.awards == 1


def test_yellow_no_one_to_share_with_ends_the_award():
    pb, m, h, _ = yellow(party_hp=(20, 0), participants=0b1)
    pb.run(Y, "decide")
    pb.register_file.F = 0
    pb.run(Y, "second")
    assert pb.register_file.F & Z and h.phase == 0


def test_yellow_off_and_link_touch_nothing():
    for kw in ({"enabled": False}, {"link": True}):
        pb, m, h, _ = yellow(f_in=0x00, **kw)
        ret_target(pb)
        m[0xFF97], m[0xFF98] = 1, 3
        m[Y.BOOST_EXP_BY_EXP_ALL] = 1
        before = snapshot(pb)
        for name in ("decide", "divide", "amount", "done", "second", "gain_entry", "divide", "amount", "done"):
            pb.run(Y, name)
            assert snapshot(pb) == before, (kw, name)
        assert h.phase == 0 and h.awards == 0


def test_yellow_enabled_is_read_once_per_award():
    pb, m, h, flag = yellow()
    pb.run(Y, "decide")
    flag["on"] = False                                          # switched mid-award: this award keeps the rule
    ret_target(pb, 0x1111)
    pb.run(Y, "divide")
    assert pb.register_file.PC == 0x1111
    pb.run(Y, "done")
    pb.register_file.F = Z
    pb.run(Y, "second")
    assert h.phase == 2
    pb.run(Y, "gain_entry")
    pb.run(Y, "done")
    before = snapshot(pb)
    pb.run(Y, "decide")                                         # next award: original
    assert snapshot(pb) == before and h.phase == 0


def test_enabled_raising_means_original():
    pb, m, h, _ = yellow()
    h.enabled = lambda: 1 / 0
    before = snapshot(pb)
    pb.run(Y, "decide")
    assert snapshot(pb) == before and h.phase == 0


# --- Crystal ---------------------------------------------------------------------------------------------------------
C = CrystalExpShare


def crystal_emu(enabled=True, link=False, tower=False, species=(155, 16, 161, 16, 161, 161), hp=(19, 17, 15, 0, 15, 9),
                participants=0b1):
    pb = FakePyBoy(C, strict=True)
    m = pb.memory
    m[C.LINK_MODE] = 1 if link else 0
    m[1, C.BATTLE_MODE] = 1
    m[C.IN_BATTLE_TOWER_BATTLE] = 1 if tower else 0
    m[C.PARTICIPANTS] = participants
    m[1, C.PARTY_COUNT] = len(species)
    for i, (sp, h) in enumerate(zip(species, hp)):
        m[1, C.PARTY_SPECIES + i] = sp
        m[1, C.PARTY_MONS + i * 48 + 34], m[1, C.PARTY_MONS + i * 48 + 35] = h >> 8, h & 0xFF
    for i, v in enumerate((35, 46, 34, 20, 35)):
        m[C.BACKUP_BASE_STATS + i] = v
    flag = {"on": enabled}
    h = crystal.install_exp_share(pb, lambda: flag["on"])
    return pb, m, h, flag


def test_crystal_full_award_flow_with_an_egg():
    pb, m, h, _ = crystal_emu(species=(155, 16, 161, 16, 161, crystal.EGG))
    r = pb.register_file
    r.F = 0                                                     # NZ: someone holds an Exp. Share
    pb.run(C, "decide")
    assert r.F & Z and h.phase == 1
    ret_target(pb, 0x2222, sp=0xC0F0)
    pb.run(C, "divide")
    assert r.PC == 0x2222 and r.SP == 0xC0F2
    m[0xFFB5], m[0xFFB6] = 0, 16
    pb.run(C, "amount")
    assert m[0xFFB6] == 16
    pb.run(C, "done")
    r.F, r.D = Z, 0b000100                                      # the game's holder mask
    pb.run(C, "second")
    assert not r.F & Z and r.D == 0b010110 and h.phase == 2     # 3 fainted, 5 egg
    assert [m[C.BACKUP_BASE_STATS + i] for i in range(5)] == [17, 23, 17, 10, 17]
    m[C.GIVING_TO_HOLDERS] = 1
    ret_target(pb, 0x3333, sp=0xC0F0)
    pb.run(C, "divide")
    assert r.PC == 0x3333
    m[0xFFB5], m[0xFFB6] = 0, 24                                # Lucky Egg x1.5 already applied by the game
    pb.run(C, "amount")
    assert m[0xFFB6] == 12
    pb.run(C, "done")
    assert h.phase == 0


def test_crystal_off_link_and_battle_tower_touch_nothing():
    for kw in ({"enabled": False}, {"link": True}, {"tower": True}):
        pb, m, h, _ = crystal_emu(**kw)
        pb.register_file.F, pb.register_file.D = 0, 0b100
        m[C.GIVING_TO_HOLDERS] = 1
        m[0xFFB5], m[0xFFB6] = 0, 24
        ret_target(pb, sp=0xC0F0)
        before = snapshot(pb)
        for name in ("decide", "divide", "amount", "done", "second", "divide", "amount", "done"):
            pb.run(C, name)
            assert snapshot(pb) == before, (kw, name)


def test_crystal_install_checks_signatures():
    pb = FakePyBoy(C)
    pb.memory.rom[C.HOOKS["decide"]] = 0x20
    with pytest.raises(RuntimeError):
        crystal.install_exp_share(pb, lambda: True)
    assert pb.hooks == {}


# --- busy(): no resume snapshot while an award runs (its state lives only in Python) ---------------------------------
def test_yellow_busy_through_a_full_award():
    pb, m, h, _ = yellow()
    assert not h.busy()
    pb.run(Y, "decide")
    assert h.busy()
    ret_target(pb)
    pb.run(Y, "divide")
    pb.run(Y, "done")                                           # end of the participants' pass: award goes on
    assert h.busy()
    pb.register_file.F = Z
    pb.run(Y, "second")
    assert h.busy()
    m[Y.BOOST_EXP_BY_EXP_ALL] = 1
    pb.run(Y, "gain_entry")
    pb.run(Y, "amount")
    assert h.busy()
    pb.run(Y, "done")
    assert not h.busy()


def test_yellow_busy_ends_when_nobody_sat_out():
    pb, m, h, _ = yellow(party_hp=(20, 0))
    pb.run(Y, "decide")
    pb.run(Y, "done")
    assert h.busy()
    pb.run(Y, "second")
    assert not h.busy()


def test_busy_false_when_off_or_link():
    for kw in ({"enabled": False}, {"link": True}):
        pb, m, h, _ = yellow(**kw)
        pb.run(Y, "decide")
        assert not h.busy()
    for kw in ({"enabled": False}, {"link": True}, {"tower": True}):
        pb, m, h, _ = crystal_emu(**kw)
        pb.run(C, "decide")
        assert not h.busy()


def test_busy_cannot_get_stuck():
    # battle over without the end hook (e.g. a save state loaded mid-award, then the battle finished)
    pb, m, h, _ = yellow()
    pb.run(Y, "decide")
    pb.register_file.F = Z
    pb.run(Y, "second")
    pb.run(Y, "gain_entry")
    assert h.busy()
    m[Y.IS_IN_BATTLE] = 0
    assert not h.busy() and h.phase == 0
    ret_target(pb)
    before = snapshot(pb)
    for name in ("divide", "amount", "gain_entry"):              # stale hooks do nothing afterwards
        pb.run(Y, name)
    assert snapshot(pb) == before
    # remove() mid-award
    pb, m, h, _ = yellow()
    pb.run(Y, "decide")
    h.remove()
    assert not h.busy()
    # Crystal: wBattleMode 0
    pb, m, h, _ = crystal_emu()
    pb.run(C, "decide")
    assert h.busy()
    m[1, C.BATTLE_MODE] = 0
    assert not h.busy()


def test_crystal_busy_through_a_full_award():
    pb, m, h, _ = crystal_emu()
    pb.run(C, "decide")
    pb.run(C, "done")
    assert h.busy()
    pb.register_file.F = Z
    pb.run(C, "second")
    assert h.busy()
    pb.run(C, "done")
    assert not h.busy()
    pb.run(C, "decide")
    pb.register_file.F = Z
    m[1, C.PARTY_COUNT] = 1                                     # nobody to share with
    pb.run(C, "second")
    assert not h.busy()
