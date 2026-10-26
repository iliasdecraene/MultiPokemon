"""Exp. Share All for Platinum (client/yellowlink/platinum_exp.py): the rule's numbers, and the two hooks driven by a
fake DS that runs a Python model of Platinum's BtlCmd_CalcExpGain + BattleScript_GetExpTask (pret
src/battle/battle_script.c) over a fake BattleContext. No ROM needed; the real-ROM check is dev/e2e/expshare_pt.py."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import pk4  # noqa: E402
from yellowlink import platinum_exp as X  # noqa: E402

EXP_SHARE, LUCKY_EGG = 216, 231


# --- the rule ------------------------------------------------------------------------------------------------
def test_shares_and_rounding():
    assert X.full_share(24) == 24 and X.half_share(24) == 12
    assert X.half_share(25) == 12                     # floor
    assert X.half_share(1) == 1 and X.full_share(0) == 1   # never 0 (the game's own minimum for a participant)
    assert X.base_award(58, 3) == 24                  # Bidoof Lv 3 (measured on the ROM)
    assert X.base_award(56, 5) == 40                  # Starly Lv 5 (Youngster Tristan)
    assert X.base_award(255, 100) == 3642


def test_modifiers_in_game_order():
    assert X.apply_modifiers(12, trainer=True) == 18
    assert X.apply_modifiers(12, lucky_egg=True) == 18
    assert X.apply_modifiers(13, lucky_egg=True, trainer=True) == 13 * 150 // 100 * 150 // 100   # floor each time
    assert X.apply_modifiers(10, traded=True) == 15
    assert X.apply_modifiers(10, traded=True, foreign=True) == 17


def test_plan_full_half_nothing():
    # slots: 0 lead (participant), 1 + 2 benched, 3 fainted, 4 egg  -> eligible = 0b00111
    assert X.plan(24, 0b00001, 0b00111, 5) == {0: 24, 1: 12, 2: 12}
    assert X.plan(24, 0b00011, 0b00111, 5) == {0: 24, 1: 24, 2: 12}      # two participants: both full
    assert X.plan(1, 0b1, 0b11, 2) == {0: 1, 1: 1}


def test_original_split_matches_platinum():
    assert X.original_split(24, 1, 0) == (24, 0)
    assert X.original_split(24, 2, 0) == (12, 0)
    assert X.original_split(24, 1, 1) == (12, 12)
    assert X.original_split(25, 3, 2) == (4, 6)
    assert X.original_split(1, 2, 0) == (1, 0)


def party_mon(species=387, hp=20, egg=False, item=0, pid=0x1234, in_place=False):
    p = pk4.PK4(bytes(pk4.PARTY_SIZE))
    p.pid, p.species, p.hp, p.item = pid, species, hp, item
    p.is_egg = egg
    enc = p.encrypted()
    if in_place:          # the game's working state: flags set, blocks XOR-decrypted where they lie
        enc = bytearray(enc)
        chk = struct.unpack_from("<H", enc, 6)[0]
        enc[8:136] = pk4._crypt(bytes(enc[8:136]), chk)
        struct.pack_into("<H", enc, 4, pk4.FLAG_BOX_DECRYPTED)
        enc = bytes(enc)
    return enc


def test_mon_eligible():
    assert X.mon_eligible(party_mon())
    assert X.mon_eligible(party_mon(in_place=True))
    assert not X.mon_eligible(party_mon(hp=0))
    assert not X.mon_eligible(party_mon(egg=True))
    assert not X.mon_eligible(party_mon(species=0))
    assert not X.mon_eligible(pk4.EMPTY_PARTY)
    bad = bytearray(party_mon())
    bad[0x20] ^= 0xFF
    assert not X.mon_eligible(bytes(bad))


# --- the hooks on a fake DS ----------------------------------------------------------------------------------
CTX, SYS, PARTY, TASK = 0x02300000, 0x02310000, 0x02320000, 0x02330000


class FakeLib:
    def __init__(self):
        self.regs = {}

    def desmume_memory_read_register(self, name):
        return self.regs.get(name.decode().split(".")[1], 0)


class FakeEmu:
    def __init__(self):
        self.lib = FakeLib()


class FakeCore:
    """Memory + hook API of nds_core.NDSCore, and a model of Platinum's exp award code calling the hooks."""

    def __init__(self, mons, items=None, trainer=False, battle_code=True):
        self.mem = {}
        self.emu = FakeEmu()
        self.hooks = {}
        self.mons = mons
        self.items = items or [0] * len(mons)
        self.w32(SYS + X.BS_CTX, CTX)
        self.w32(SYS + X.BS_PARTIES, PARTY)
        self.w32(SYS + X.BS_BATTLE_TYPE, X.BATTLE_TYPE_TRAINER if trainer else 0)
        self.w32(PARTY, 6)
        self.w32(PARTY + 4, len(mons))
        for i, rec in enumerate(mons):
            self.write(PARTY + 8 + 236 * i, rec)
        if battle_code:
            for addr, code in X._CODE.items():
                self.write(addr, code)
        self.w32(TASK + X.TD_CTX, CTX)

    # sparse byte memory
    def read(self, a, n):
        return bytes(self.mem.get(a + i, 0) for i in range(n))

    def write(self, a, data):
        for i, b in enumerate(bytes(data)):
            self.mem[a + i] = b

    def r8(self, a):
        return self.read(a, 1)[0]

    def r16(self, a):
        return struct.unpack("<H", self.read(a, 2))[0]

    def r32(self, a):
        return struct.unpack("<I", self.read(a, 4))[0]

    def w32(self, a, v):
        self.write(a, struct.pack("<I", v & 0xFFFFFFFF))

    def hook_register(self, addr, fn):
        self.hooks[addr] = fn

    def hook_deregister(self, addr):
        self.hooks.pop(addr, None)

    def _call(self, addr, **regs):
        self.emu.lib.regs = regs
        if addr in self.hooks:
            self.hooks[addr]()

    # --- model of the game -----------------------------------------------------------------------------------
    def alive(self, s):
        return X.mon_eligible(self.mons[s]) or (pk4.PK4(pk4.decrypt(self.mons[s])).is_egg
                                               and pk4.PK4(pk4.decrypt(self.mons[s])).hp > 0)

    def award(self, exp, participants, fainted=1, after_first=None):
        """BtlCmd_CalcExpGain + BattleScript_GetExpTask; returns slot -> exp the game gives."""
        idx = (fainted >> 1) & 1
        mask_addr = CTX + X.CTX_EXP_MASK + 4 * idx
        self.w32(CTX + X.CTX_FAINTED_MON, fainted)
        self.w32(mask_addr, participants)
        n = sum(1 for s in range(len(self.mons)) if self.alive(s) and participants >> s & 1)
        holders = sum(1 for s in range(len(self.mons)) if self.alive(s) and self.items[s] == EXP_SHARE)
        g, sh = X.original_split(exp, max(n, 1), holders)
        self._call(X.HOOK_CALC, r0=exp, r5=CTX, r7=SYS, r1=holders)
        self.w32(CTX + X.CTX_GAINED_EXP, g)
        self.w32(CTX + X.CTX_SHARED_EXP, sh)
        trainer = bool(self.r32(SYS + X.BS_BATTLE_TYPE) & X.BATTLE_TYPE_TRAINER)
        out = {}
        start = 0
        while True:
            self.w32(TASK + X.TD_SEQ, X.SEQ_GET_EXP_START)
            self.w32(TASK + X.TD_PARTY_SLOT, start)
            self._call(X.HOOK_TASK, r0=0, r1=TASK)
            mask = self.r32(mask_addr)
            slot = next((s for s in range(start, len(self.mons))
                         if self.items[s] == EXP_SHARE or mask >> s & 1), None)
            if slot is None:
                break
            p = pk4.PK4(pk4.decrypt(self.mons[slot]))
            total = 0
            if p.hp:                                        # (level 100 not modelled)
                if mask >> slot & 1:
                    total = self.r32(CTX + X.CTX_GAINED_EXP)
                if self.items[slot] == EXP_SHARE:
                    total += self.r32(CTX + X.CTX_SHARED_EXP)
                total = X.apply_modifiers(total, lucky_egg=self.items[slot] == LUCKY_EGG, trainer=trainer)
            if total:
                out[slot] = total
            self.w32(mask_addr, self.r32(mask_addr) & ~(1 << slot))      # SEQ_GET_EXP_CHECK_DONE
            for _ in range(3):                                           # other states: hook must not act
                self.w32(TASK + X.TD_SEQ, 7)
                self._call(X.HOOK_TASK, r0=0, r1=TASK)
            start = slot + 1
            if after_first:
                after_first()
                after_first = None
        assert self.r32(mask_addr) == 0                                  # consumed, like the original
        return out


def party5():
    return [party_mon(pid=1), party_mon(pid=2), party_mon(pid=3), party_mon(hp=0, pid=4), party_mon(egg=True, pid=5)]


def test_off_is_original():
    core = FakeCore(party5())
    h = X.install_exp_share(core, lambda: False)
    assert core.award(24, 0b1) == {0: 24}
    assert core.award(24, 0b11) == {0: 12, 1: 12}
    assert h.pending is None and h.awards[-1]["on"] is False


def test_on_lead_alone():
    core = FakeCore(party5())
    h = X.install_exp_share(core, lambda: True)
    assert core.award(24, 0b1) == {0: 24, 1: 12, 2: 12}            # fainted (3) and egg (4) nothing
    assert h.awards[-1] == {"exp": 24, "participants": 1, "eligible": 0b111, "count": 5, "on": True,
                            "trainer": False, "fainted_battler": 1, "enemy_species": 0, "enemy_level": 0}
    assert h.pending is None


def test_on_two_participants_and_second_enemy_slot():
    core = FakeCore(party5())
    X.install_exp_share(core, lambda: True)
    assert core.award(25, 0b11) == {0: 25, 1: 25, 2: 12}
    assert core.award(25, 0b100, fainted=3) == {0: 12, 1: 12, 2: 25}   # double battle: enemy 2's own mask


def test_on_trainer_and_lucky_egg():
    core = FakeCore(party5()[:3], items=[0, LUCKY_EGG, 0], trainer=True)
    X.install_exp_share(core, lambda: True)
    assert core.award(40, 0b1) == {0: 60, 1: 20 * 150 // 100 * 150 // 100, 2: 30}


def test_exp_share_holder_on_and_off():
    mons = [party_mon(pid=1), party_mon(pid=2), party_mon(pid=3)]
    core = FakeCore(mons, items=[0, EXP_SHARE, 0])
    flag = {"on": False}
    X.install_exp_share(core, lambda: flag["on"])
    assert core.award(24, 0b1) == {0: 12, 1: 12}                   # Platinum's own Exp. Share
    flag["on"] = True
    assert core.award(24, 0b1) == {0: 24, 1: 12, 2: 12}            # no double for the holder
    assert core.award(24, 0b10) == {0: 12, 1: 24, 2: 12}           # holder that fought: full, not more


def test_fainted_holder_before_eligible_slot():
    mons = [party_mon(pid=1), party_mon(hp=0, pid=2), party_mon(pid=3)]
    core = FakeCore(mons, items=[0, EXP_SHARE, 0])
    X.install_exp_share(core, lambda: True)
    assert core.award(24, 0b1) == {0: 24, 2: 12}


def test_enabled_checked_per_award_and_errors_mean_off():
    core = FakeCore(party5())
    seq = iter([True, False])
    X.install_exp_share(core, lambda: next(seq))
    assert core.award(24, 0b1) == {0: 24, 1: 12, 2: 12}
    assert core.award(24, 0b1) == {0: 24}

    def boom():
        raise RuntimeError("settings gone")
    core2 = FakeCore(party5())
    X.install_exp_share(core2, boom)
    assert core2.award(24, 0b1) == {0: 24}


def test_not_the_battle_overlay_does_nothing():
    core = FakeCore(party5(), battle_code=False)
    h = X.install_exp_share(core, lambda: True)
    assert core.award(24, 0b1) == {0: 24}
    assert h.awards == [] and h.pending is None


def test_remove():
    core = FakeCore(party5())
    h = X.install_exp_share(core, lambda: True)
    assert set(core.hooks) == {X.HOOK_CALC, X.HOOK_TASK}
    h.remove()
    assert core.hooks == {}
    assert core.award(24, 0b1) == {0: 24}


def test_remove_in_the_middle_of_an_award_gives_the_rest_back_to_the_game():
    core = FakeCore(party5())
    h = X.install_exp_share(core, lambda: True)
    # lead (slot 0) already got the rule's E; then the rule is removed: the bench gets Platinum's own 0
    assert core.award(24, 0b1, after_first=h.remove) == {0: 24}
    core = FakeCore(party5())
    h = X.install_exp_share(core, lambda: True)
    assert core.award(24, 0b11, after_first=h.remove) == {0: 24, 1: 12}     # slot 1: the game's own E/2


def test_code_signatures_are_thumb_from_the_rom():
    # bytes read from the running ROM in battle (see platinum_exp docstring); keep them intact
    assert X._CODE[X.HOOK_CALC][:2] == b"\x00\x29"                  # cmp r1, #0
    assert X._CODE[X.HOOK_TASK][:4] == bytes.fromhex("f8b5b6b0")    # push {r3-r7,lr}; sub sp, #0xd8
    assert X.HOOK_TASK & 1 == 0 and X.HOOK_CALC & 1 == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
