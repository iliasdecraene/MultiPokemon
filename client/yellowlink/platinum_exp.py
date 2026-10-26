"""Exp. Share All (the Gen 6+ rule) for Pokémon Platinum (US rev 0), done through the game's own battle code.

    handle = install_exp_share(core, enabled)   # core: nds_core.NDSCore (or dev/platinum/ds.DS for tests)
    handle.busy()      # True while an award we changed is in progress (don't take a resume snapshot then)
    handle.remove()    # mid-award: the rest goes back to the game's rule; the slot being processed is kept

THE RULE (Pokémon X/Y onward): when an enemy Pokémon faints in a wild or trainer battle, every party Pokémon that
took part gets the FULL experience the game would give a single participant (no division by the number of
participants), every other party Pokémon that is not fainted and not an egg gets 50 %, fainted Pokémon and eggs get
nothing. The game's own modifiers stay and are applied by the game afterwards: trainer battle x1.5, Lucky Egg x1.5,
traded x1.5 / foreign language x1.7 ("a boosted ..."). While the rule is on, a held Exp. Share no longer splits
anything (it is replaced by the rule: a holder simply gets its 100 % / 50 % like everybody else).
`enabled()` is asked once per experience award (each fainted enemy): False -> that award is the original Gen 4 one.

Decisions (documented, see also dev/platinum/MEMORY_MAP.md "Exp. Share All"):
  * 50 % = floor(E / 2), at least 1 (E = the game's base award floor(baseExp * enemyLevel / 7); the game itself
    gives at least 1 to a participant). The multipliers are applied by the game after that, each with its own
    floor, exactly as for its own Exp. Share: e.g. a trainer battle gives floor(floor(E/2) * 150 / 100).
  * Effort values: every Pokémon that gets experience under the rule also gets the defeated Pokémon's full EV yield
    (the game's own BattleScript_CalcEffortValues, incl. Macho Brace / Pokérus) - like the modern games, where
    everyone in the party gets full EVs with Exp. Share on. Level-100 Pokémon get neither (Platinum's rule).
  * Double battles: every enemy that faints is its own award (the game already does one award per fainted
    battler); "participants" are the game's own per-enemy participant mask (who was out against that enemy).
  * Multi battles with an AI partner: only the player's party gets experience (the game's rule; unchanged).

HOW (pret/pokeplatinum src/battle/battle_script.c; addresses found in the running ROM, overlay 16 = battle, see
dev/platinum/MEMORY_MAP.md for how): two exec hooks, no exp/level is ever written by us.
  1. HOOK_CALC (inside BtlCmd_CalcExpGain, right after `exp = baseExp * level / 7`): r0 = E, r5 = BattleContext,
     r7 = BattleSystem. We remember E and the game's participant mask M for this award, then widen the mask
     (BattleContext.sideGetExpMask[enemy]) to every party slot that is alive and not an egg. That mask is what
     BattleScript_GetExpTask walks; it clears each bit as it goes, so it ends empty exactly like the original.
  2. HOOK_TASK (entry of BattleScript_GetExpTask, a SysTask run once per frame; r1 = task data): in its START state
     the task is about to read BattleContext.gainedExp / sharedExp for the next slot in the mask; we set
     gainedExp = max(E, 1) if that slot was a real participant (bit in M) else max(E // 2, 1), and sharedExp = 0
     (no Exp. Share split on top). The game then does everything else natively: multipliers, the
     "gained N Exp. Points!" message, exp bar, level-up, stat recalculation + "grew to Lv. N" and the stat window,
     moves learnt (incl. the forget-a-move prompt), friendship, EVs, and the evolution after battle.
Both hooks check that overlay 16 really is loaded (the code bytes at the address) before doing anything: overlays
share load addresses, so the same address is other code outside battles.
"""
from __future__ import annotations

import logging
import struct

from . import pk4
from .platinum import OVERLAY_BATTLE, PlatinumMemory, _ptr

log = logging.getLogger("yellowlink.platinum_exp")

# --- hook addresses (overlay 16 "battle", loaded at 0x0223B140; Thumb code) ----------------------------------
HOOK_CALC = 0x02241E30   # BtlCmd_CalcExpGain (0x02241D34): `cmp r1, #0` = if (totalMonsWithExpShare) after exp calc
HOOK_TASK = 0x02248E74   # BattleScript_GetExpTask (0x02248E75 Thumb, the fn StartGetExpTask hands to SysTask_Start)
# 12 bytes of code at each hook: proves the battle overlay (not another overlay sharing the range) is loaded.
_CODE = {
    # cmp r1,#0; beq 0x02241E76; lsrs r4,r0,#1; ldr r1,[sp,#4]; adds r0,r4,#0; blx _s32_div_f
    HOOK_CALC: bytes.fromhex("002920d044080199201ca0f6"),
    # push {r3-r7,lr}; sub sp,#0xd8; adds r4,r1,#0; str r0,[sp,#0x14]; ldr r0,[r4]; bl BattleSystem_GetMessageLoader
    HOOK_TASK: bytes.fromhex("f8b5b6b00c1c05902068f5f7"),
}

# BattleContext (battle_context.h) offsets, verified in the compiled CalcExpGain / GetExpTask
CTX_FAINTED_MON = 0x74
CTX_GAINED_EXP = 0x9C
CTX_SHARED_EXP = 0xA0
CTX_EXP_MASK = 0xA4          # u32 sideGetExpMask[2], indexed by (faintedMon >> 1) & 1
CTX_TASK_DATA = 0x178        # BattleScriptTaskData *taskData
CTX_BATTLE_MONS = 0x2D40     # BattleMon battleMons[4], 0xC0 each: species u16 +0, level u8 +0x34
BMON_SIZE = 0xC0
BMON_LEVEL = 0x34
# BattleSystem
BS_BATTLE_TYPE = 0x2C
BS_CTX = 0x30
BS_PARTIES = 0x68            # Party *parties[4]; party: capacity u32, count u32, Pokemon[6] (236 bytes each)
# BattleScriptTaskData
TD_CTX = 0x04
TD_SEQ = 0x28
TD_PARTY_SLOT = 0x48         # tmpData[GET_EXP_PARTY_SLOT]
SEQ_GET_EXP_START = 0

BATTLE_TYPE_TRAINER = 0x01
PARTY_MON = 236


# --- the rule (pure; unit-tested) ------------------------------------------------------------------------------
def full_share(exp: int) -> int:
    """What a participant gets before the game's multipliers (the game's own minimum of 1)."""
    return max(int(exp), 1)


def half_share(exp: int) -> int:
    """What a non-participant gets before the game's multipliers: floor(E / 2), at least 1."""
    return max(int(exp) // 2, 1)


def base_award(base_exp: int, enemy_level: int) -> int:
    """Platinum's E (BtlCmd_CalcExpGain): u16(baseExp * level / 7)."""
    return (base_exp * enemy_level // 7) & 0xFFFF


def apply_modifiers(base: int, *, trainer=False, lucky_egg=False, traded=False, foreign=False) -> int:
    """The game's multipliers in its own order (BattleScript_GetExpTask): Lucky Egg, trainer, traded/foreign."""
    v = base
    if lucky_egg:
        v = v * 150 // 100
    if trainer:
        v = v * 150 // 100
    if traded:
        v = v * (170 if foreign else 150) // 100
    return v


def plan(exp: int, participants: int, eligible: int, count: int) -> dict:
    """slot -> base exp under the rule. participants = the game's mask, eligible = alive non-egg slots."""
    out = {}
    for s in range(count):
        if not eligible >> s & 1:
            continue
        out[s] = full_share(exp) if participants >> s & 1 else half_share(exp)
    return out


def original_split(exp: int, participants_alive: int, share_holders_alive: int) -> tuple:
    """Platinum's own (gainedExp, sharedExp) for comparison: (E/2)/n and (E/2)/holders with Exp. Share."""
    n = max(participants_alive, 1)
    if share_holders_alive:
        return max((exp // 2) // n, 1), max((exp // 2) // share_holders_alive, 1)
    return max(exp // n, 1), 0


def mon_eligible(raw: bytes) -> bool:
    """A party record (236 bytes, encrypted or in the game's working state): species, not an egg, HP > 0."""
    try:
        p = pk4.PK4(pk4._plain_of(raw))
    except (ValueError, struct.error):
        return False
    if not p.checksum_ok():
        return False
    return p.species != 0 and not p.is_egg and p.hp > 0


# --- runtime glue ---------------------------------------------------------------------------------------------
class _Regs:
    def __init__(self, core):
        emu = getattr(core, "emu", None)
        self._rd = emu.lib.desmume_memory_read_register

    def __getitem__(self, name):
        return self._rd(("arm9.%s" % name).encode()) & 0xFFFFFFFF


class ExpShareAll:
    """Installed hooks (see the module docstring). `awards` keeps the last awards for tests/diagnostics."""

    def __init__(self, core, enabled):
        self.core = core
        self.enabled = enabled
        self.regs = _Regs(core)
        self._pm = PlatinumMemory(core)
        self.pending = None          # (ctx, mask index, participant mask M, E) for the award in progress
        self._orig = None            # the game's own (gainedExp, sharedExp) of that award
        self.awards = []             # dicts, newest last (max 32)
        self.given = []              # (slot, base exp set) per task step, for tests
        self.trace = None            # tests: set to [] to record the exp task's (search slot, seqNum) steps
        self._installed = []
        for addr, fn in ((HOOK_CALC, self._on_calc), (HOOK_TASK, self._on_task)):
            self._hook(addr, fn)

    # hooks through NDSCore.hook_register (one trampoline per address); a bare ds.DS falls back to py-desmume
    def _hook(self, addr, fn):
        if hasattr(self.core, "hook_register"):
            self.core.hook_register(addr, fn)
        else:
            self.core.emu.memory.register_exec(addr, lambda _a, _s: _safe(fn))
        self._installed.append(addr)

    def remove(self):
        """Uninstall. Called in the middle of an award (see busy()), the rest of that award is handed back to the
        game's own rule; the slot the task is working on (e.g. at a move-learn prompt) is left alone."""
        for addr in self._installed:
            try:
                if hasattr(self.core, "hook_deregister"):
                    self.core.hook_deregister(addr)
                else:
                    self.core.emu.memory.register_exec(addr, None)
            except Exception:  # noqa: BLE001
                log.exception("removing exp hook %08X", addr)
        self._installed = []
        pend, self.pending = self.pending, None
        if pend is not None:
            self._undo(pend)

    def busy(self) -> bool:
        """True while an award we changed is really in progress (its GetExp task is alive in the loaded battle):
        a savestate taken now would resume mid-award without our per-slot steering. A `pending` left over from a
        battle that is gone (soft reset, state load) is dropped here."""
        pend = self.pending
        if pend is None:
            return False
        if self._task_of(pend[0]) is None:
            self.pending = None
            return False
        return True

    def _task_of(self, ctx):
        """The live BattleScriptTaskData of BattleContext ctx, or None (battle overlay not loaded, no exp task,
        or the task belongs to another context)."""
        m = self.core
        try:
            if not (self._code_ok(HOOK_CALC) and self._code_ok(HOOK_TASK)):
                return None
            if self._pm.child_overlay() != OVERLAY_BATTLE or not _ptr(ctx):
                return None
            td = m.r32(ctx + CTX_TASK_DATA)
            if not _ptr(td) or m.r32(td + TD_CTX) != ctx:
                return None
            return td
        except Exception:  # noqa: BLE001
            return None

    def _undo(self, pend):
        """Give the rest of an award back to the game's own rule: mask = the real participants still to do plus
        the slot the task is processing right now, the game's own gainedExp / sharedExp. Only if that award's
        task is really alive - a stale context (battle gone, heap freed) is never written to."""
        ctx, idx, parts, _exp = pend
        td = self._task_of(ctx)
        if td is None:
            return
        m = self.core
        try:
            mask_addr = ctx + CTX_EXP_MASK + 4 * idx
            mask = m.r32(mask_addr)
            keep = parts
            if m.r32(td + TD_SEQ) != SEQ_GET_EXP_START:      # a slot is being processed: its bit must stay
                start = m.r32(td + TD_PARTY_SLOT)
                cur = next((s for s in range(start, 6) if mask >> s & 1), None)
                if cur is not None:
                    keep |= 1 << cur
            m.w32(mask_addr, mask & keep)
            if self._orig is not None:
                m.w32(ctx + CTX_GAINED_EXP, self._orig[0])
                m.w32(ctx + CTX_SHARED_EXP, self._orig[1])
        except Exception:  # noqa: BLE001
            log.exception("exp share: undo failed")

    def _code_ok(self, addr) -> bool:
        return self.core.read(addr, len(_CODE[addr])) == _CODE[addr]

    # 1. BtlCmd_CalcExpGain, E computed
    def _on_calc(self):
        self.pending = None
        if not self._code_ok(HOOK_CALC):
            return
        try:
            on = bool(self.enabled())
        except Exception:  # noqa: BLE001
            log.exception("exp share enabled() failed")
            on = False
        r = self.regs
        exp, ctx, bsys = r["r0"] & 0xFFFF, r["r5"], r["r7"]
        m = self.core
        if not (_ptr(ctx) and _ptr(bsys)) or m.r32(bsys + BS_CTX) != ctx:
            log.warning("exp share: unexpected registers at CalcExpGain (ctx %08X sys %08X)", ctx, bsys)
            return
        idx = (m.r32(ctx + CTX_FAINTED_MON) >> 1) & 1
        mask_addr = ctx + CTX_EXP_MASK + 4 * idx
        parts = m.r32(mask_addr)
        party = m.r32(bsys + BS_PARTIES)
        count = min(m.r32(party + 4), 6) if _ptr(party) else 0
        elig = 0
        for s in range(count):
            if mon_eligible(m.read(party + 8 + PARTY_MON * s, PARTY_MON)):
                elig |= 1 << s
        fainted = m.r32(ctx + CTX_FAINTED_MON)
        bmon = ctx + CTX_BATTLE_MONS + BMON_SIZE * (fainted & 3)
        award = {"exp": exp, "participants": parts, "eligible": elig, "count": count, "on": on,
                 "trainer": bool(m.r32(bsys + BS_BATTLE_TYPE) & BATTLE_TYPE_TRAINER), "fainted_battler": fainted,
                 "enemy_species": m.r16(bmon), "enemy_level": m.r8(bmon + BMON_LEVEL)}
        self.awards = (self.awards + [award])[-32:]
        if not on:
            return
        m.w32(mask_addr, parts | elig)
        self.pending = (ctx, idx, parts, exp)
        self._orig = None

    # 2. BattleScript_GetExpTask, every frame while it runs
    def _on_task(self):
        pend = self.pending
        if pend is None and self.trace is None:
            return
        if not self._code_ok(HOOK_TASK):  # another overlay's code at this address: r1 means nothing
            return
        m = self.core
        data = self.regs["r1"]
        if not _ptr(data):
            return
        if self.trace is not None:
            step = (m.r32(data + TD_PARTY_SLOT), m.r32(data + TD_SEQ))
            if not self.trace or self.trace[-1] != step:
                self.trace.append(step)
        if pend is None:
            return
        ctx, idx, parts, exp = pend
        if m.r32(data + TD_CTX) != ctx:
            return
        if m.r32(data + TD_SEQ) != SEQ_GET_EXP_START:
            return
        start = m.r32(data + TD_PARTY_SLOT)
        mask = m.r32(ctx + CTX_EXP_MASK + 4 * idx)
        slot = next((s for s in range(start, 6) if mask >> s & 1), None)
        if slot is None:                 # the task is finishing (no slot left): award done
            self.pending = None
            return
        if self._orig is None:           # the game's own values (computed after HOOK_CALC), kept for _undo
            self._orig = (m.r32(ctx + CTX_GAINED_EXP), m.r32(ctx + CTX_SHARED_EXP))
        give = full_share(exp) if parts >> slot & 1 else half_share(exp)
        m.w32(ctx + CTX_GAINED_EXP, give)
        m.w32(ctx + CTX_SHARED_EXP, 0)
        self.given = (self.given + [(slot, give)])[-64:]


def _safe(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - never break the emulator from a hook
        log.exception("exp share hook failed")


def install_exp_share(core, enabled) -> ExpShareAll:
    """Install Exp. Share All on a Platinum core. `enabled()` is asked at every experience award."""
    return ExpShareAll(core, enabled)
