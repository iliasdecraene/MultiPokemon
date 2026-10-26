"""Exp Share All (the modern Gen 6+ rule) for Pokémon Yellow and Pokémon Crystal, through the games' own code.

THE RULE. When the enemy mon faints in a wild or trainer battle:
  * every party mon that took part in the fight against it gets the FULL experience and stat experience the game
    gives a single participant -- no division by the number of participants;
  * every other party mon that is not fainted and not an egg gets 50 % of what it would get as the only participant
    (all the game's own boosts included: traded x1.5, trainer battle x1.5, Crystal's Lucky Egg x1.5);
  * fainted mons and eggs get nothing.
Rounding (50 % share): the experience is halved AFTER the game has computed the full amount with all its boosts,
`floor(full / 2)`; stat experience is the enemy's base stats halved, `floor(base / 2)` per stat (exactly what the
games' own EXP.ALL / Exp. Share already do to the base stats; with Pokérus Crystal adds that half twice).
While the rule is on, Yellow's EXP.ALL (key item in the bag) and Crystal's held Exp. Share do nothing extra: the
rule replaces them (a holder that fought gets 100 %, a holder that sat out gets 50 %, never both).
`enabled()` is asked once per award (when the enemy faints); False -> that award is byte-for-byte the original game.

HOW. Both games already have a two-pass award: pass 1 gives exp to the participants (dividing the enemy's base data
by their number), and when EXP.ALL / an Exp. Share is present a second pass halves the base data and gives exp to a
second set of mons (Yellow: the whole party; Crystal: the holders). PyBoy exec hooks steer that machinery:
  1. "decide"  at the branch on "EXP.ALL in bag / any Exp. Share holder": ask enabled(); if on, force the Z flag so
               the game skips its own halving of the base data (as if no EXP.ALL / Exp. Share).
  2. "divide"  at the entry of the per-participant division routine: emulate `ret` (no split) while the rule runs.
  3. "second"  at the `ret z` that ends the award when there is no EXP.ALL / Exp. Share: if any non-participant
               (HP > 0, not an egg) exists, clear Z so the game runs its own second pass, with OUR set of mons
               (Yellow: wPartyGainExpFlags written at GainExperience's entry; Crystal: register D, which the game
               stores into wBattleParticipantsNotFainted); the enemy's base stats are halved for it (stat exp 50 %).
  4. "amount"  right after the last 1.5x boost of the computed experience (hQuotient+2/+3, 16 bit): in the second
               pass, halve it.
  5. "done"    at the end of the award loop: forget the per-award state.
Everything else -- adding the exp, the cap at level 100, "gained N EXP. Points!", "grew to level N!", stats + HP,
the stats box, level-up moves (and the forget-a-move prompt), evolution after the battle (wCanEvolveFlags /
wEvolvableFlags), Pikachu's happiness -- is the game's own code. Nothing writes exp or levels directly.
Link battles (Yellow wLinkState == LINK_STATE_BATTLING, Crystal wLinkMode != 0) and the Battle Tower never get the
rule (the games give no exp there anyway; the hooks stay passive).

HOOKED ADDRESSES (pret symbols + instruction sizes, ROM bytes checked before installing, see SIGNATURES):
Pokémon Yellow (SHA1 cc7d0326...), engine/battle/core.asm FaintEnemyPokemon, engine/battle/experience.asm:
  0F:461C  FaintEnemyPokemon: `push af` after `call IsItemInBag` (b = EXP_ALL)            -> decide
  0F:4637  FaintEnemyPokemon: `ret z` after `callfar GainExperience; pop af`             -> second
  15:525F  GainExperience (entry)            -> second pass: wPartyGainExpFlags = ours, wBoostExpByExpAll = 0 (the
           plain "gained N EXP. Points!" text instead of "with EXP.ALL"), wEnemyMonBaseStats (D001, 5 B) halved
  15:547B  DivideExpDataByNumMonsGainingExp  -> divide
  15:52EF  GainExperience: `inc hl` after `call nz, BoostExp` (trainer boost)            -> amount (FF97/FF98)
  15:545F  GainExperience.done               -> done
Pokémon Crystal (SHA1 f4cd194b...), engine/battle/core.asm UpdateBattleStateAndExperienceAfterEnemyFaint,
GiveExperiencePoints:
  0F:4E65  `jr z, .skip_exp` after the first `call IsAnyMonHoldingExpShare`              -> decide
  0F:4E88  `ret z` after `call GiveExperiencePoints; call IsAnyMonHoldingExpShare`       -> second (D = our set,
           wBackupEnemyMonBaseStats C720 5 B halved; the game copies it into wEnemyMonBaseStats itself)
  0F:70D4  GiveExperiencePoints.EvenlyDivideExpAmongParticipants                         -> divide
  0F:6EF8  GiveExperiencePoints: `ldh a, [hQuotient + 3]` after the Lucky Egg `call z, BoostExp` -> amount (FFB5/6)
  0F:70D1  GiveExperiencePoints.done                                                     -> done
Verified on the running ROMs by dev/e2e/expshare_gb.py; pure math in client/tests/test_expshare_gb.py.
"""
from __future__ import annotations

import logging

log = logging.getLogger("yellowlink.expshare")

Z_FLAG = 0x80


# --- pure helpers (no emulator) ------------------------------------------------------------------------------------
def boost(q: int) -> int:
    """The games' BoostExp: q + q/2 on the 16-bit hQuotient+2/+3 (the carry out of bit 15 is lost)."""
    q &= 0xFFFF
    return (q + (q >> 1)) & 0xFFFF


def exp_award(base_exp: int, enemy_level: int, *, traded=False, trainer=False, lucky_egg=False,
              divide_by: int = 1, share: bool = False) -> int:
    """Experience one mon gets, computed exactly like the games: base exp (divided by the number of participants,
    integer, only when there are 2+) * enemy level / 7, then traded / trainer / Lucky Egg x1.5 in that order.
    `share` = the modern rule's 50 % for a non-participant (halved after all boosts)."""
    b = base_exp & 0xFF
    if divide_by >= 2:
        b //= divide_by
    q = (b * enemy_level) // 7
    if traded:
        q = boost(q)
    if trainer:
        q = boost(q)
    if lucky_egg:
        q = boost(q)
    if share:
        q >>= 1
    return q


def stat_exp_after(old: int, base: int, *, share=False, pokerus=False) -> int:
    """New 16-bit stat experience: + base stat (halved for a share, added twice with Pokérus), capped at 0xFFFF."""
    add = (base >> 1) if share else base
    new = old + add * (2 if pokerus else 1)
    return min(new, 0xFFFF)


def non_participants(count: int, participants: int, hps, eggs=()) -> int:
    """Bit mask of the party mons that get the 50 % share: not a participant, HP > 0, not an egg."""
    mask = 0
    for i in range(min(count, 6)):
        if participants & (1 << i) or i in eggs:
            continue
        if hps[i] > 0:
            mask |= 1 << i
    return mask


# --- hooks -----------------------------------------------------------------------------------------------------
class _ExpShareHooks:
    """Common part: registration with a ROM signature check, `remove()`, flag/ret helpers."""

    HOOKS: dict = {}          # name -> (bank, addr)
    SIGNATURES: dict = {}     # name -> expected opcode byte at (bank, addr) before hooking

    def __init__(self, pyboy, enabled):
        self.pyboy = pyboy
        self.m = pyboy.memory
        self.enabled = enabled
        self.phase = 0              # 0 idle | 1 participants' pass | 2.. game-specific second-pass phases
        self.participants = 0
        self.shared = 0
        self.awards = 0             # number of awards the rule was applied to (diagnostics / tests)
        self._registered = []
        bad = [n for n, (b, a) in self.HOOKS.items() if self.m[b, a] != self.SIGNATURES[n]]
        if bad:
            raise RuntimeError(f"Exp Share All: unexpected ROM code at {bad} (not the verified ROM?)")
        try:
            for name, (bank, addr) in self.HOOKS.items():
                pyboy.hook_register(bank, addr, self._safe(getattr(self, "on_" + name)), None)
                self._registered.append((bank, addr))
        except Exception:
            self.remove()
            raise

    def remove(self):
        for bank, addr in self._registered:
            try:
                self.pyboy.hook_deregister(bank, addr)
            except Exception:  # noqa: BLE001 - already gone
                log.debug("hook %02X:%04X was not registered", bank, addr)
        self._registered = []
        self.phase = 0

    def _safe(self, fn):
        def wrapped(_ctx):
            try:
                fn()
            except Exception:  # noqa: BLE001 - a hook must never kill the emulator
                log.exception("Exp Share All hook %s failed", fn.__name__)
                self.phase = 0
        return wrapped

    def _want(self) -> bool:
        try:
            return bool(self.enabled())
        except Exception:  # noqa: BLE001
            log.exception("Exp Share All: enabled() failed; original behaviour")
            return False

    def _set_z(self, on: bool):
        rf = self.pyboy.register_file
        rf.F = (rf.F | Z_FLAG) if on else (rf.F & ~Z_FLAG)

    def _ret(self):
        rf = self.pyboy.register_file
        sp = rf.SP
        rf.PC = self.m[sp] | (self.m[sp + 1] << 8)
        rf.SP = (sp + 2) & 0xFFFF

    def _halve_quotient(self, hi_addr: int):
        q = (self.m[hi_addr] << 8) | self.m[hi_addr + 1]
        q >>= 1
        self.m[hi_addr], self.m[hi_addr + 1] = q >> 8, q & 0xFF

    def _halve_bytes(self, addr: int, n: int):
        for i in range(n):
            self.m[addr + i] = self.m[addr + i] >> 1


class YellowExpShare(_ExpShareHooks):
    # Yellow RAM (WRAM, never banked)
    LINK_STATE = 0xD12A                  # wLinkState
    LINK_STATE_BATTLING = 4
    PARTY_GAIN_EXP_FLAGS = 0xD057        # wPartyGainExpFlags
    BOOST_EXP_BY_EXP_ALL = 0xCC5B        # wBoostExpByExpAll (only selects the "with EXP.ALL" text)
    ENEMY_MON_BASE_STATS = 0xD001        # wEnemyMonBaseStats (5), then catch rate D006, base exp D007
    PARTY_COUNT = 0xD162
    PARTY_MONS = 0xD16A                  # 44-byte structs, HP big endian at +1
    MON_SIZE = 44
    H_QUOTIENT_2 = 0xFF97                # hQuotient (FF95) + 2: experience, 16 bit big endian

    HOOKS = {
        "decide": (0x0F, 0x461C),
        "second": (0x0F, 0x4637),
        "gain_entry": (0x15, 0x525F),
        "divide": (0x15, 0x547B),
        "amount": (0x15, 0x52EF),
        "done": (0x15, 0x545F),
    }
    SIGNATURES = {"decide": 0xF5, "second": 0xC8, "gain_entry": 0xFA, "divide": 0xFA, "amount": 0x23, "done": 0x21}

    # phases: 1 participants' pass, 2 second pass requested, 3 second pass running
    def on_decide(self):
        self.phase = 0
        if self.m[self.LINK_STATE] == self.LINK_STATE_BATTLING or not self._want():
            return
        self.participants = self.m[self.PARTY_GAIN_EXP_FLAGS]
        self.phase = 1
        self.awards += 1
        self._set_z(True)               # no EXP.ALL halving; the pushed flags say "no EXP.ALL" too

    def on_second(self):
        if self.phase != 1:
            return
        m = self.m
        n = m[self.PARTY_COUNT]
        hps = [(m[self.PARTY_MONS + i * self.MON_SIZE + 1] << 8) | m[self.PARTY_MONS + i * self.MON_SIZE + 2]
               for i in range(min(n, 6))]
        self.shared = non_participants(n, self.participants, hps)
        if self.shared:
            self.phase = 2
            self._set_z(False)          # go on into the game's EXP.ALL pass (it jumps to GainExperience)
        else:
            self.phase = 0
            self._set_z(True)

    def on_gain_entry(self):
        if self.phase != 2:
            return
        m = self.m
        m[self.PARTY_GAIN_EXP_FLAGS] = self.shared
        m[self.BOOST_EXP_BY_EXP_ALL] = 0
        self._halve_bytes(self.ENEMY_MON_BASE_STATS, 5)
        self.phase = 3

    def on_divide(self):
        if self.phase in (1, 3):
            self._ret()

    def on_amount(self):
        if self.phase == 3:
            self._halve_quotient(self.H_QUOTIENT_2)

    def on_done(self):
        if self.phase == 3:
            self.phase = 0


class CrystalExpShare(_ExpShareHooks):
    # Crystal RAM: C000-CFFF and FF80+ unbanked; party in WRAM bank 1 (mem[1, addr])
    LINK_MODE = 0xC2DC                   # wLinkMode
    IN_BATTLE_TOWER_BATTLE = 0xCFC0      # bit 0
    PARTICIPANTS = 0xC664                # wBattleParticipantsNotFainted
    GIVING_TO_HOLDERS = 0xC71F           # wGivingExperienceToExpShareHolders (1 in the second pass)
    BACKUP_BASE_STATS = 0xC720           # wBackupEnemyMonBaseStats (5), copied to wEnemyMonBaseStats (D226)
    PARTY_COUNT = 0xDCD7
    PARTY_SPECIES = 0xDCD8
    PARTY_MONS = 0xDCDF                  # 48-byte structs, HP big endian at +34
    MON_SIZE = 48
    EGG = 0xFD
    H_QUOTIENT_2 = 0xFFB5                # hQuotient (FFB3) + 2

    HOOKS = {
        "decide": (0x0F, 0x4E65),
        "second": (0x0F, 0x4E88),
        "divide": (0x0F, 0x70D4),
        "amount": (0x0F, 0x6EF8),
        "done": (0x0F, 0x70D1),
    }
    SIGNATURES = {"decide": 0x28, "second": 0xC8, "divide": 0xFA, "amount": 0xF0, "done": 0xC3}

    # phases: 1 participants' pass, 2 second pass (decided -> done)
    def on_decide(self):
        self.phase = 0
        m = self.m
        if m[self.LINK_MODE] or (m[self.IN_BATTLE_TOWER_BATTLE] & 1) or not self._want():
            return
        self.participants = m[self.PARTICIPANTS]
        self.phase = 1
        self.awards += 1
        self._set_z(True)               # skip the Exp. Share halving of the base data

    def on_second(self):
        if self.phase != 1:
            return
        m = self.m
        n = m[1, self.PARTY_COUNT]
        hps, eggs = [], set()
        for i in range(min(n, 6)):
            o = self.PARTY_MONS + i * self.MON_SIZE + 34
            hps.append((m[1, o] << 8) | m[1, o + 1])
            if m[1, self.PARTY_SPECIES + i] == self.EGG:
                eggs.add(i)
        self.shared = non_participants(n, self.participants, hps, eggs)
        if self.shared:
            self.phase = 2
            self.pyboy.register_file.D = self.shared   # the game stores D into wBattleParticipantsNotFainted
            self._halve_bytes(self.BACKUP_BASE_STATS, 5)
            self._set_z(False)
        else:
            self.phase = 0
            self._set_z(True)

    def on_divide(self):
        if self.phase in (1, 2):
            self._ret()

    def on_amount(self):
        if self.phase == 2 and self.m[self.GIVING_TO_HOLDERS] == 1:
            self._halve_quotient(self.H_QUOTIENT_2)

    def on_done(self):
        if self.phase == 2:
            self.phase = 0
