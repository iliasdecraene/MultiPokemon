"""Raid battles in Pokémon Platinum (US rev 0 and our Complete Sinnoh build - same code), through the game's own
battle engine. Same interface as raid_gb (dev/raids/STATUS.md):

    rb = RaidBattle(app, boss)       # app.nds (nds_core.NDSCore) + app.game (PlatinumMemory); or core=..., pm=...
    rb.can_join(); rb.start(pool_mb); each frame: rb.step() -> events
    rb.set_pool(pool_mb[, dealt_mb]); rb.group_down(chance); rb.flee(); rb.auto_pick()

HOW (DESIGN.md appendix A.1-A.5; every address was found in the running ROM, Platinum has no symbol file; code bytes
are checked before each use because overlays share load addresses):
  start    there is no "write a mon and start a battle" in RAM, so YellowLink makes the field call the game's own
           functions (what ScrCmd_StartWildBattle does), one per field frame, through the `pop {r3, pc}` epilogue
           of ExecuteFieldProcesses (0x0203CCE0): the frame's saved PC is pointed at the function, its result
           comes back to the epilogue, the frame is restored (A.3):
             dto = FieldBattleDTO_New(HEAP_ID_FIELD2, BATTLE_TYPE_WILD_MON)    0x02051D8D
             FieldBattleDTO_Init(dto, fieldSystem)                             0x02052315
             CreateWildMon_Scripted(fieldSystem, species, level, dto)          0x022420D5 (overlay 6)
             [the wild mon itself: raid moves, shiny PID, fateful flag; legendary battle intro/music]
             Encounter_NewVsWild(fieldSystem, dto)                             0x02050E11
           The game's own FieldTask_WildEncounter then runs the battle, the party copy-back, blackout and catch.
  shared HP  BattleContext.battleMons[1] (the boss's battle copy): the turn start (command selection) writes
           curHP = the pool's segment and refills the boss's PP. Every HP change in Platinum goes through
           BtlCmd_UpdateHealthBarValue; a hook right after its battler is resolved (0x02241814, r2 = battler,
           r4 = BattleContext) turns a hit that would take the boss to 0 into one that leaves 1 HP (like the game's
           own Focus Sash): poison, recoil, Perish Song, Destiny Bond... all take this path. A second hook on the
           faint test's HP read (BtlCmd_TryFaintMon, 0x022418E8) is the safety net. Damage is counted in the hook.
  balls    BattleControllerPlayer_CommandSelectionInput (0x0224BB28): a ball picked in the bag while fighting is
           turned into PLAYER_INPUT_CANCEL (the menu comes back, the ball stays). Catch phase: one ball; the
           shake count is forced at the end of BattleScript_CalcCatchShakes (0x0224A94E, r5 = shakes): 4 = caught,
           3 = "Aargh! Almost had it!". On a catch the boss's natural moves (what CreateWildMon_Scripted gave it)
           are put back into the Pokémon the game is about to add to the party/PC (decision 11). A miss wakes the
           boss and it teleports away.
  timer    BattleContext.command == COMMAND_SELECTION_INPUT and curCommandState[0] = 1 menu, 4 moves, 6 target,
           8 bag, 10 party, 12 run; BtlCmd_WaitYesNoResult (0x02247C04) / BtlCmd_WaitMonSelection
           (0x0224200C) run every frame while "Use next Pokémon?" / the party screen wait after a faint. auto_pick
           touches the bottom screen (never B: on the command menu B is the Run shortcut).
"""
from __future__ import annotations

import logging
import random
import struct
import time
from ctypes import c_char_p

from . import pk4, raid_art, raid_data
from .platinum import (BATTLE_MON_SIZE, CTX_BATTLE_MONS, FLAG_HAS_PARTNER, FLAG_SAFARI_GAME_ACTIVE,
                       PlatinumMemory, _ptr)

log = logging.getLogger("yellowlink.raid")

SPLASH, TELEPORT = 150, 100
# --- field: calling game functions ---------------------------------------------------------------------------
EPILOGUE = 0x0203CCE0                          # ExecuteFieldProcesses: pop {r3, pc}
EPILOGUE_CODE = bytes.fromhex("08bd")
FieldBattleDTO_New = 0x02051D8D
FieldBattleDTO_Init = 0x02052315
CreateWildMon_Scripted = 0x022420D5
Encounter_NewVsWild = 0x02050E11
OV6_CODE = (0x022420D4, bytes.fromhex("f8b588b0051c0292"))   # CreateWildMon_Scripted: push {r3-r7,lr}; ...
HEAP_ID_FIELD2 = 0xB
BATTLE_TYPE_WILD_MON = 0
S_FIELD_SYSTEM = 0x021C07DC
DTO_PARTIES = 0x04
DTO_RESULT = 0x14                              # FieldBattleDTO.resultMask
DTO_BATTLE_STATUS = 0x18C
BATTLE_STATUS_LEGENDARY = 0x8
RESULT_WIN, RESULT_LOSE, RESULT_CAPTURED = 1, 2, 4
RESULT_PLAYER_FLED, RESULT_ENEMY_FLED = 5, 6
# --- battle (overlay 16) ----------------------------------------------------------------------------------------
CTX_CUR_CMD_STATE = 0x00
CTX_COMMAND = 0x08
CTX_HP_CALC_TEMP = 0x215C
CTX_IO_BUFFER = 0x2300
BS_PARTIES = 0x68
BM_MOVES, BM_TYPE1, BM_PP, BM_LEVEL, BM_CUR_HP, BM_MAX_HP, BM_STATUS = 0x0C, 0x24, 0x2C, 0x34, 0x4C, 0x50, 0x6C
CONTROL_INIT_COMMAND_SELECTION, CONTROL_COMMAND_SELECTION_INPUT = 4, 5
WAITING = {1: "menu", 4: "moves", 6: "target", 8: "bag", 10: "party", 12: "run"}
LOCKED = {13, 14}
ITEM_SELECT = 8
PLAYER_INPUT_CANCEL = 0xFF
SLEEP_3 = 3
HOOKS = {   # name: (address, 12 code bytes there)
    "select": (0x0224BB28, bytes.fromhex("f8b5a8b0071c0791f2f7f4f9")),     # BattleControllerPlayer_CommandSelectionInput
    "hp": (0x02241814, bytes.fromhex("1c48171c2518c02047431b48")),         # BtlCmd_UpdateHealthBarValue (battler known)
    "faint": (0x022418E8, bytes.fromhex("5158002913d16c6736f66cff")),      # BtlCmd_TryFaintMon: ldr r1, [r2, r1]
    "shakes": (0x0224A94E, bytes.fromhex("281c02b0f8bd1c31000038e6")),     # BattleScript_CalcCatchShakes: r0 = r5
    "yesno": (0x02247C04, bytes.fromhex("f8b50d1c061c281c002111f0")),      # BtlCmd_WaitYesNoResult
    "partymenu": (0x0224200C, bytes.fromhex("f8b584b000900d1cfbf782ff")),  # BtlCmd_WaitMonSelection (party after a faint)
}
TYPE_CHART = (0x0226ECD4, bytes.fromhex("0005050008050a0a050a0b05"))      # overlay 16: (atk, def, x10) ... FE ... FF
# --- touch screen (bottom screen coordinates, read off screenshots) ---------------------------------------------
TOUCH_FIGHT = (128, 90)
TOUCH_MOVE = [(64, 48), (192, 48), (64, 104), (192, 104)]
TOUCH_BACK = (236, 174)                         # the bottom-right "back" arrow (bag, party, move list)
TOUCH_YES_NEXT = (128, 68)                      # "Use next Pokémon" (the other button is "Flee")
TOUCH_PARTY = [(64, 24), (192, 24), (64, 72), (192, 72), (64, 120), (192, 120)]
TOUCH_SHIFT = (128, 110)
TOUCH_BAG = (40, 168)                           # command menu: BAG
TOUCH_BAG_BALLS = (192, 56)                     # bag: the Poké Balls pocket
TOUCH_BAG_SLOTS = [(64, 30), (192, 30), (64, 78), (192, 78), (64, 126), (192, 126)]   # first page of a pocket
TOUCH_BAG_USE = (100, 174)
TOUCH_BAG_NEXT = (60, 168)                      # bag pocket: next page (6 items per page)
TOUCH_RUN = (128, 176)                          # command menu: RUN
FieldBattleDTO_Free = 0x020520A5
FREE_CODE = (0x020520A4, bytes.fromhex("f8b5071c00243d1c68680028"))   # push {r3-r7,lr}; ... loops Heap_Free
FS_TASK = 0x10                                  # FieldSystem.task (non-NULL while a script/menu/event runs)
BM_SPEED, BM_ABILITY, BM_ITEM = 0x06, 0x27, 0x78
ITEM_SMOKE_BALL = 228                           # HOLD_EFFECT_FLEE: Battler_IsTrappedMsg is FALSE for its holder
DEOXYS = 386
NO_PROGRESS = 600                               # frames (10 s): auto_pick gives up driving without progress


class _Regs:
    def __init__(self, core):
        lib = core.emu.lib
        self._rd = lib.desmume_memory_read_register
        self._wr = lib.desmume_memory_write_register

    def __getitem__(self, n):
        return self._rd(c_char_p(("arm9." + n).encode())) & 0xFFFFFFFF

    def __setitem__(self, n, v):
        self._wr(c_char_p(("arm9." + n).encode()), int(v) & 0xFFFFFFFF)


def _s32(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


class _Hooks:
    """Exec hooks through NDSCore.hook_register (one handler per address) or a bare dev/platinum ds.DS."""

    def __init__(self, core):
        self.core = core
        self.addrs = []

    def add(self, addr, fn):
        if hasattr(self.core, "hook_register"):
            self.core.hook_register(addr, fn)
        else:
            self.core.emu.memory.register_exec(addr, lambda _a, _s: fn())
        self.addrs.append(addr)

    def remove_all(self):
        for addr in self.addrs:
            try:
                if hasattr(self.core, "hook_deregister"):
                    self.core.hook_deregister(addr)
                else:
                    self.core.emu.memory.register_exec(addr, None)
            except Exception:  # noqa: BLE001
                pass
        self.addrs = []


class FieldCaller:
    """Call game functions (<= 4 register arguments) from Python, one per field frame (DESIGN.md A.3)."""

    def __init__(self, core, hooks: _Hooks, regs: _Regs):
        self.core = core
        self.r = regs
        self.queue = []
        self.cur = None
        self.log = []
        hooks.add(EPILOGUE, self._cb)

    precheck = None        # () -> bool: may the next queued call run now? (checked calls only)
    on_abort = None        # () -> None: a checked call was refused (the queue has been cleared)

    def call(self, fn, args, on_result=None, check=True):
        self.queue.append((fn, list(args), on_result, check))

    def busy(self):
        return bool(self.queue) or self.cur is not None

    def _cb(self):
        try:
            self._on_pop()
        except Exception as e:  # noqa: BLE001
            self.log.append("hook error %r" % e)
            log.exception("raid field call")

    def _on_pop(self):
        d, r = self.core, self.r
        if d.read(EPILOGUE, 2) != EPILOGUE_CODE:
            return
        sp = r["r13"]
        if self.cur is None:
            if not self.queue:
                return
            if self.queue[0][3] and self.precheck is not None and not self.precheck():
                self.queue.clear()
                self.log.append("refused")
                if self.on_abort:
                    self.on_abort()
                return
            fn, args, cb, _check = self.queue.pop(0)
            args = args + [0] * (4 - len(args))
            self.cur = {"cb": cb, "sp": sp, "frame": (d.r32(sp), d.r32(sp + 4)), "r0": r["r0"]}
            d.w32(sp, args[3])                 # the pop loads r3 from here ...
            d.w32(sp + 4, fn | 1)              # ... and PC from here: it "returns" into the function
            for i in range(3):
                r["r%d" % i] = args[i]
            r["r14"] = EPILOGUE | 1            # the function returns to the epilogue again
            self.log.append("call %08X" % fn)
            return
        cur = self.cur
        if sp != cur["sp"] + 8:
            self.log.append("unexpected sp %08X" % sp)
            return
        result = r["r0"]
        base = cur["sp"]
        d.w32(base, cur["frame"][0])           # the real frame back; the pop now returns to the real caller
        d.w32(base + 4, cur["frame"][1])
        r["r13"] = base
        r["r0"] = cur["r0"]
        self.cur = None
        if cur["cb"]:
            cur["cb"](result)


def strongest_level(pm) -> int:
    """The player's strongest party level (decision 3: a mythical boss is this + 5); eggs don't count."""
    try:
        from .platinum import mon_info
        infos = [mon_info(r) for r in (pm.party_checked() or [])]
        return raid_data.strongest_level(i["level"] for i in infos if i and not i["egg"])
    except Exception:  # noqa: BLE001
        return 0


def shiny_pid(pid: int, tid: int, sid: int) -> int:
    """A shiny PID for this trainer that keeps the nature (PID % 25), the gender byte and the ability bit."""
    lo = pid & 0xFFFF
    nature = pid % 25
    for hi_byte in range(256):
        lo2 = (lo & 0x00FF) | (hi_byte << 8)
        base = lo2 ^ tid ^ sid
        for k in range(8):
            cand = ((base ^ k) << 16) | lo2
            if cand % 25 == nature and pk4.is_shiny(cand, tid, sid):
                return cand
    return (((lo ^ tid ^ sid) << 16) | lo) & 0xFFFFFFFF          # shiny, nature changed (never needed so far)


class RaidBattle:
    """One raid battle in the player's own Platinum (see the module docstring and dev/raids/STATUS.md)."""

    game_id = "platinum"

    def __init__(self, app_or_core=None, boss: dict | None = None, *, pm=None, data=None, rng=None):
        core = getattr(app_or_core, "nds", None) or app_or_core
        self.core = core
        self.pm = pm or getattr(app_or_core, "game", None) or PlatinumMemory(core)
        self.data = data if data is not None else getattr(app_or_core, "plat_data", None)
        self.rng = rng or random.Random()
        self.boss = dict(boss)
        self.species = int(boss["species"])
        self.level = int(boss["level"])
        self.moves = [int(x) for x in (list(boss.get("moves") or []) + [0, 0, 0, 0])[:4]]
        if not any(self.moves):
            self.moves = [SPLASH, 0, 0, 0]
        self.shiny = bool(boss.get("shiny"))
        self.fateful = bool(boss.get("fateful"))
        self.regs = _Regs(core)
        self.hooks = _Hooks(core)
        self.caller = None
        self.phase = "idle"
        self.pool = 0
        self.dealt = 0
        self.meter = raid_data.BarMeter()
        self.max_hp = 0
        self.events = []
        self.frame = 0
        self.decision = None
        self.auto = False
        self.ui = None
        self._ui_since = 0
        self._next_touch = 0
        self._touch_until = None
        self._party_step = 0
        self.last_slot = None
        self._pp_turn = None
        self.catch_success = None
        self.throws = 0
        self.missed = False
        self.flee_asked = False
        self.escaped = False
        self.outcome = None
        self.natural = None            # the boss's natural moves + PP (restored into a caught boss)
        self.pid = None
        self.dto = None
        self.result = 0
        self._last_cmd = None
        self._turn_synced = False
        self._yesno_frame = -999
        self._party_frame = -999
        self._started = False
        self._chart = None
        self._move_info = None
        self.log = []

    # ------------------------------------------------------------------------------------------------------------
    def can_join(self):
        pm = self.pm
        try:
            if not pm.in_game():
                return False, "not in the game yet"
            if not pm.is_safe():
                return False, "stand still in the overworld first"
            if pm.flag(FLAG_SAFARI_GAME_ACTIVE):
                return False, "not from the Great Marsh"
            if pm.flag(FLAG_HAS_PARTNER):
                return False, "not while walking with a partner"
            if self.core.read(OV6_CODE[0], len(OV6_CODE[1])) != OV6_CODE[1]:
                return False, "can't start a battle here"
            party = pm.party_checked()
            if not party:
                return False, "you need a healthy Pokémon"
            from .platinum import mon_info
            infos = [mon_info(r) for r in party]
            if not any(i and not i["egg"] and i["hp"] > 0 for i in infos):
                return False, "you need a healthy Pokémon"
            if not any(1 <= item <= 16 and qty > 0 for item, qty in pm.balls()):
                return False, "you need a Poké Ball"
        except Exception as e:  # noqa: BLE001
            log.exception("raid can_join")
            return False, "can't check the game right now (%s)" % e
        return True, ""

    def start(self, pool_mb: int) -> bool:
        if self.phase != "idle" or not self.pm.is_safe():
            return False
        d = self.core
        if d.read(OV6_CODE[0], len(OV6_CODE[1])) != OV6_CODE[1]:
            return False
        fs = d.r32(S_FIELD_SYSTEM)
        if not _ptr(fs):
            return False
        self.pool = int(pool_mb)
        self._install()
        c = self.caller
        st = {}

        def field_free():
            # each injected call only while the field has no task (a trainer's sight line, a script, the start
            # menu... appearing during these 4 frames would get a battle started on top of it)
            return not d.r32(fs + FS_TASK)

        def abort():
            self._note("start refused: the field started a task (%s)" % c.log[-4:])
            dto = st.get("dto")
            if dto and d.read(FREE_CODE[0], len(FREE_CODE[1])) == FREE_CODE[1]:
                c.call(FieldBattleDTO_Free, [dto], lambda _r: self._set_start_error("field busy"), check=False)
            else:
                self._set_start_error("field busy")

        c.precheck = field_free
        c.on_abort = abort

        def got_dto(v):
            if not _ptr(v):
                self._note("FieldBattleDTO_New failed")
                self._finish("error")
                return
            st["dto"] = self.dto = v
            c.call(FieldBattleDTO_Init, [v, fs])
            c.call(CreateWildMon_Scripted, [fs, self.species, self.level, v], got_mon)

        def got_mon(_):
            try:
                self._prepare_wild_mon(st["dto"])
            except Exception:  # noqa: BLE001
                log.exception("raid: preparing the wild mon")
            dto = st["dto"]
            d.w32(dto + DTO_BATTLE_STATUS, d.r32(dto + DTO_BATTLE_STATUS) | BATTLE_STATUS_LEGENDARY)
            c.call(Encounter_NewVsWild, [fs, dto])

        c.call(FieldBattleDTO_New, [HEAP_ID_FIELD2, BATTLE_TYPE_WILD_MON], got_dto)
        self.phase = "starting"
        self._start_frame = self.frame
        self._note("start %d Lv%d pool %d" % (self.species, self.level, self.pool))
        return True

    def _set_start_error(self, why):
        self._start_error = why
        self.dto = None

    def _prepare_wild_mon(self, dto):
        """Raid moves (the battle copy is written back into this Pokémon on every HP update, so the moves must be
        set here, not only in the battle copy), shiny PID, fateful flag."""
        d = self.core
        party = d.r32(dto + DTO_PARTIES + 4 * 1)
        rec = d.read(party + 8, pk4.PARTY_SIZE)
        pkm = pk4.PK4(pk4._plain_of(rec))
        self.natural = {"moves": list(pkm.moves), "pp": list(pkm.pp), "pp_ups": list(pkm.pp_ups)}
        pkm.moves = list(self.moves)
        pkm.pp = [self._pp(m) for m in self.moves]
        if self.shiny and not pkm.shiny:
            new = shiny_pid(pkm.pid, pkm.tid, pkm.sid)
            pkm.pid = new
        if self.fateful:
            pkm.d[pk4.B + 0x18] |= 1
        form = int(self.boss.get("form") or 0)
        if self.species == DEOXYS and 0 < form <= 3 and self.data is not None:
            # Deoxys' forms (Normal/Attack/Defense/Speed): the form bits + its stats for that form (the battle
            # takes the stats stored in the party record; Pokemon_CalcStats does the same maths)
            pkm.form = form
            st = pk4.calc_stats(pkm, self.data, self.level)
            pkm.stats = st
            pkm.hp = st[0]
        self.pid = pkm.pid
        d.write(party + 8, pkm.encrypted())
        self._note("wild mon ready: pid %08X natural moves %s" % (self.pid, self.natural["moves"]))

    def _pp(self, move):
        if not move:
            return 0
        if self.data is not None:
            try:
                return self.data.move_pp(move) or 20
            except Exception:  # noqa: BLE001
                pass
        return 20

    def step(self) -> list:
        self.frame += 1
        if self.phase in ("idle", "over"):
            return self._drain()
        if getattr(self, "_start_error", None) and not (self.caller and self.caller.busy()):
            self._finish("error")
            return self._drain()
        if self.phase == "starting" and self.frame - self._start_frame > 60 * 30 and not self.pm.in_battle():
            self._finish("error")
            return self._drain()
        try:
            self._frame()
        except Exception:  # noqa: BLE001
            log.exception("raid step")
        return self._drain()

    def set_pool(self, pool_mb: int, dealt_mb: int | None = None):
        v = int(pool_mb)
        if dealt_mb is not None:
            v -= max(0, self.dealt - int(dealt_mb))
        self.pool = max(0, v)

    def group_down(self, catch_chance: float):
        if self.phase not in ("fight", "starting"):
            return
        self.catch_success = self.rng.random() < float(catch_chance)
        self.phase = "down"
        ctx = self._ctx()
        if ctx:
            self._apply_down(ctx)
        self._note("group down, catch %s" % self.catch_success)

    def flee(self):
        if self.phase in ("over", "idle"):
            return
        self.flee_asked = True
        if self.phase == "starting":
            self.phase = "fleeing"
            return
        self.phase = "fleeing"
        if self.decision is not None:                 # waiting for the player right now: YellowLink runs away
            self.auto = True
            self._progress_frame = self.frame
        ctx = self._ctx()
        if ctx:
            self._apply_flee(ctx)
        self._note("flee")

    def auto_pick(self):
        if self.decision is not None and self.phase in ("fight", "down", "fleeing"):
            self.auto = True
            self._next_touch = 0
            self._party_step = 0
            self._progress_frame = self.frame

    def taking_over(self) -> bool:
        """True while YellowLink drives the game: the battle is being started (the game functions are called over
        the next field frames - a START press then would open a menu in between), or auto_pick is touching."""
        if self.phase == "starting":
            return self.frame - getattr(self, "_start_frame", self.frame) < NO_PROGRESS
        return self.auto

    def busy(self) -> bool:
        return self.phase not in ("idle", "over")

    def dealt_mb(self) -> int:
        return self.dealt

    def remove(self):
        self.hooks.remove_all()
        try:
            self.core.touch_release() if hasattr(self.core, "touch_release") else self.core.emu.input.touch_release()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------------------------------------------------
    def _install(self):
        if self.hooks.addrs:
            return
        self.caller = FieldCaller(self.core, self.hooks, self.regs)
        for name, (addr, _code) in HOOKS.items():
            self.hooks.add(addr, self._guarded(name))

    def _guarded(self, name):
        addr, code = HOOKS[name]
        fn = getattr(self, "_on_" + name)

        def cb():
            try:
                if self.phase in ("idle", "over") or self.core.read(addr, len(code)) != code:
                    return                         # another overlay's code at this address
                fn()
            except Exception:  # noqa: BLE001
                log.exception("raid hook %s", name)
        return cb

    def _ctx(self):
        try:
            _bs, ctx = self.pm._battle_ctx()
            return ctx
        except Exception:  # noqa: BLE001
            return None

    def _bmon(self, ctx, i=1):
        return ctx + CTX_BATTLE_MONS + BATTLE_MON_SIZE * i

    def _boss_ok(self, ctx) -> bool:
        return self.core.r16(self._bmon(ctx)) == self.species

    # --- hooks
    def _on_select(self):
        d = self.core
        ctx = self._ctx()
        if not ctx or not self._boss_ok(ctx) or d.r8(ctx + CTX_CUR_CMD_STATE) != ITEM_SELECT:
            return
        v = d.r32(ctx + CTX_IO_BUFFER)
        item = v & 0xFFFF
        if not v or (v & 0xFF) == PLAYER_INPUT_CANCEL or not 1 <= item <= 16:
            return
        if self.phase == "down" and self.throws == 0 and self.catch_success is not None:
            self.throws = 1
            self._note("ball thrown (forced %s)" % ("catch" if self.catch_success else "miss"))
            self.ui = None
            self._close_decision()
            return
        d.w32(ctx + CTX_IO_BUFFER, PLAYER_INPUT_CANCEL)
        self._note("ball blocked (item %d)" % item)

    def _on_hp(self):
        if self.phase not in ("fight", "down", "fleeing") or self.regs["r2"] != 1:
            return
        d = self.core
        ctx = self.regs["r4"]
        if not _ptr(ctx) or not self._boss_ok(ctx):
            return
        bm = self._bmon(ctx)
        cur = _s32(d.r32(bm + BM_CUR_HP))
        calc = _s32(d.r32(ctx + CTX_HP_CALC_TEMP))
        if calc >= 0 or cur <= 0:
            return
        zeroed = cur + calc <= 0
        if zeroed:
            d.w32(ctx + CTX_HP_CALC_TEMP, (-(cur - 1)) & 0xFFFFFFFF)     # Focus-Sash style: stays at 1 HP
            self._note("clamped to 1 (hit %d)" % -calc)
        if self.phase == "fight":
            self._observe(max(1, cur + calc), zeroed)

    def _on_faint(self):
        if self.phase not in ("fight", "down", "fleeing") or self.regs["r4"] != 1:
            return
        ctx = self._ctx()
        if not ctx or not self._boss_ok(ctx):
            return
        bm = self._bmon(ctx)
        if _s32(self.core.r32(bm + BM_CUR_HP)) <= 0:
            self.core.w32(bm + BM_CUR_HP, 1)
            self._note("guard: HP 0 -> 1 at the faint test")
            if self.phase == "fight":
                self._observe(0, True)

    def _on_shakes(self):
        if self.phase != "down" or self.throws != 1 or self.catch_success is None or getattr(self, "_shaken", False):
            return
        ctx = self._ctx()
        if not ctx or not self._boss_ok(ctx):
            return
        self._shaken = True
        if self.catch_success:
            self.regs["r5"] = 4
            self._restore_natural(ctx)
            self._note("forced 4 shakes (caught)")
        else:
            self.regs["r5"] = 3
            self._note("forced 3 shakes (broke free)")
            self._on_miss(ctx)

    def _on_yesno(self):
        if self.phase in ("fight", "down", "fleeing"):
            if self.frame - self._yesno_frame > 2:
                self._next_mon_wait("yesno")
            self._yesno_frame = self.frame

    def _on_partymenu(self):
        if self.phase in ("fight", "down", "fleeing"):
            if self.frame - self._party_frame > 2:
                self._next_mon_wait("party_faint")
            self._party_frame = self.frame

    def _next_mon_wait(self, ui):
        if self.decision is None or self.decision["what"] != "next_mon":
            self._close_decision()
            self._open_decision("next_mon")
        if self.ui != ui:
            self._set_ui(ui)

    def _restore_natural(self, ctx):
        """The boss is being caught: its natural moves go back into the Pokémon the game adds to the party/PC
        (the battle's own copy of the wild party, slot 0) and into the battle copy (written back into it)."""
        d = self.core
        nat = self.natural
        if not nat:
            return
        bs = d.r32(self.pm._battle_app() + 0x1C) if self.pm._battle_app() else 0
        bm = self._bmon(ctx)
        for i in range(4):
            d.w16(bm + BM_MOVES + 2 * i, nat["moves"][i])
            d.w8(bm + BM_PP + i, nat["pp"][i])
            d.w8(bm + BM_PP + 4 + i, nat["pp_ups"][i])
        if _ptr(bs):
            party = d.r32(bs + BS_PARTIES + 4 * 1)
            if _ptr(party):
                a = party + 8
                pkm = pk4.PK4(pk4._plain_of(d.read(a, pk4.PARTY_SIZE)))
                if pkm.species == self.species:
                    pkm.moves = nat["moves"]
                    pkm.pp = nat["pp"]
                    pkm.pp_ups = nat["pp_ups"]
                    d.write(a, pkm.encrypted())

    def _on_miss(self, ctx):
        if self.missed:
            return
        self.missed = True
        self.events.append({"type": "missed"})
        self.phase = "fleeing"
        self._apply_flee(ctx)

    # --- per frame
    def _frame(self):
        d = self.core
        ctx = self._ctx()
        self._read_result()
        if not ctx or not self._boss_ok(ctx):
            self._check_end()
            return
        bm = self._bmon(ctx)
        if self.max_hp == 0:
            # the battle copy is filled over a few frames: wait until it is complete
            mx = d.r32(bm + BM_MAX_HP)
            if not (mx and d.r32(bm + BM_CUR_HP) == mx and d.r8(bm + BM_LEVEL) == self.level):
                return
            self.max_hp = mx
            hp = self.meter.sync(self.pool, mx)
            d.w32(bm + BM_CUR_HP, max(1, hp))
            if self.phase == "starting":
                self.phase = "fight"
            elif self.phase == "fleeing":
                self._apply_flee(ctx)
            elif self.phase == "down":              # the group took it down while this battle was starting
                self._apply_down(ctx)
            self._note("battle up: max hp %d, segment %d" % (mx, hp))
        cmd = d.r32(ctx + CTX_COMMAND)
        state = d.r8(ctx + CTX_CUR_CMD_STATE)
        # turn start (the game is about to ask for the player's command): sync to the group pool
        if cmd in (CONTROL_INIT_COMMAND_SELECTION, CONTROL_COMMAND_SELECTION_INPUT):
            if not self._turn_synced:
                self._turn_synced = True
                self._turn_start(ctx)
        else:
            self._turn_synced = False
        # waiting for the player
        now = WAITING.get(state) if cmd == CONTROL_COMMAND_SELECTION_INPUT else None
        if now:
            if not self._started:
                self._started = True
                self.events.append({"type": "started"})
            if self.decision is not None and self.decision["what"] == "next_mon":
                self._close_decision()
            self._open_decision("menu")
            if now != self.ui:
                if self.ui == "moves" and now == "menu":
                    pass
                self._set_ui(now)
        elif self.decision is not None and self.decision["what"] == "menu" and \
                (cmd != CONTROL_COMMAND_SELECTION_INPUT or state in LOCKED):
            if self.ui == "moves":
                self.last_slot = self._touched_slot if getattr(self, "_touched_slot", None) is not None else None
            self._set_ui(None)
            self._close_decision()
        if self.decision is not None and self.decision["what"] == "next_mon" and \
                self.frame - max(self._yesno_frame, self._party_frame) > 30 and not now:
            self._set_ui(None)
            self._close_decision()
        self._last_cmd = cmd
        if self.auto:
            self._autopilot(ctx)

    def _read_result(self):
        """The battle's result (FieldBattleDTO.resultMask), read every frame for as long as the DTO is still ours:
        the battle writes it as it ends and the encounter task frees the DTO a few frames after the battle app -
        a frame-exact window, so we keep watching the same memory (same parties pointer) after the battle too.
        Also: did the player's side still have a healthy Pokémon at the end (for a blackout whose result we missed)?"""
        d = self.core
        dto = self.dto
        if not dto:
            return
        try:
            if getattr(self, "_dto_party1", None) is None:
                p1 = d.r32(dto + DTO_PARTIES + 4)
                if _ptr(p1):
                    self._dto_party1 = p1
            if d.r32(dto + DTO_PARTIES + 4) == self._dto_party1 and d.r32(dto) == BATTLE_TYPE_WILD_MON:
                r = d.r32(dto + DTO_RESULT)
                if r in (RESULT_WIN, RESULT_LOSE, RESULT_CAPTURED, RESULT_PLAYER_FLED, RESULT_ENEMY_FLED, 3):
                    self.result = r
            app = self.pm._battle_app()
            if app:
                bs = d.r32(app + 0x1C)
                party = d.r32(bs + BS_PARTIES) if _ptr(bs) else 0
                if _ptr(party):
                    n = min(d.r32(party + 4), 6)
                    alive, clean = 0, True
                    for i in range(n):
                        raw = d.read(party + 8 + i * pk4.PARTY_SIZE, pk4.PARTY_SIZE)
                        p = pk4.PK4(pk4._plain_of(raw))
                        if not p.checksum_ok():
                            clean = False                 # being worked on this frame: read again next frame
                            break
                        if p.species and not p.is_egg and p.hp > 0:
                            alive += 1
                    if n and clean:
                        self._player_alive = alive
        except Exception:  # noqa: BLE001
            pass

    def _turn_start(self, ctx):
        d = self.core
        bm = self._bmon(ctx)
        if self.phase == "fight":
            old = _s32(d.r32(bm + BM_CUR_HP))
            hp = self.meter.sync(self.pool, self.max_hp)
            d.w32(bm + BM_CUR_HP, max(1, hp))
            if hp == 0:
                self.meter.done = True
            self._note("sync: pool %d -> segment %d (was %d)" % (self.pool, hp, old))
            for i in range(4):
                if d.r16(bm + BM_MOVES + 2 * i):
                    d.w8(bm + BM_PP + i, max(5, self._pp(d.r16(bm + BM_MOVES + 2 * i))))
        elif self.phase == "down":
            self._apply_down(ctx)
        elif self.phase == "fleeing":
            self._apply_flee(ctx)
        # the player's last used move = the slot whose PP went down since the last turn start
        pm0 = self._bmon(ctx, 0)
        pp = [d.r8(pm0 + BM_PP + i) for i in range(4)]
        mv = [d.r16(pm0 + BM_MOVES + 2 * i) for i in range(4)]
        if self._pp_turn and self._pp_turn[1] == mv:
            used = [i for i in range(4) if pp[i] < self._pp_turn[0][i]]
            if used:
                self.last_slot = used[0]
        self._pp_turn = (pp, mv)

    def _apply_down(self, ctx):
        d = self.core
        bm = self._bmon(ctx)
        d.w32(bm + BM_CUR_HP, 1)
        d.w32(bm + BM_STATUS, SLEEP_3)                # asleep: it doesn't attack
        for i in range(4):
            d.w16(bm + BM_MOVES + 2 * i, SPLASH if i == 0 else 0)
            d.w8(bm + BM_PP + i, 30 if i == 0 else 0)

    def _apply_flee(self, ctx):
        d = self.core
        bm = self._bmon(ctx)
        d.w32(bm + BM_STATUS, 0)                      # awake: a sleeping boss can't teleport
        # never trapped (Shadow Tag, Arena Trap, Magnet Pull, Mean Look/Block/Spider Web, Ingrain...): a Smoke Ball
        # in the battle copy makes Battler_IsTrappedMsg FALSE for the boss; speed 1: the player's RUN always works
        d.w16(bm + BM_ITEM, ITEM_SMOKE_BALL)
        d.w16(bm + BM_SPEED, 1)
        for i in range(4):
            d.w16(bm + BM_MOVES + 2 * i, TELEPORT if i == 0 else 0)
            d.w8(bm + BM_PP + i, 30 if i == 0 else 0)

    def _observe(self, hp, zeroed):
        drop, mb = self.meter.observe(hp, zeroed)
        if drop or mb:
            self.dealt += mb
            self.pool = max(0, self.pool - mb)
            self.events.append({"type": "damage", "hp": drop, "of": self.max_hp, "mb": mb})

    # --- decisions / timer
    def _set_ui(self, ui):
        if ui != self.ui:
            self._progress_frame = self.frame          # the game moved on: auto_pick is making progress
        self.ui = ui
        self._ui_since = self.frame

    def _open_decision(self, what):
        if self.decision is not None:
            return
        self.decision = {"what": what, "t": time.time(), "frame": self.frame}
        self.events.append({"type": "need_input", "what": what})
        if self.phase == "fleeing":                   # the raid is over for this player: YellowLink runs away
            self.auto = True
            self._progress_frame = self.frame

    def _close_decision(self):
        if self.decision is None:
            return
        self.decision = None
        self.auto = False
        self._release_touch()
        self.events.append({"type": "input_done"})

    def _touch(self, xy, gap=40):
        d = self.core
        if hasattr(d, "touch"):
            d.touch(*xy)
        else:
            d.emu.input.touch_set_pos(*xy)
        self._touch_until = self.frame + 6
        self._next_touch = self.frame + gap

    def _release_touch(self):
        if self._touch_until is None:
            return
        d = self.core
        try:
            if hasattr(d, "touch_release"):
                d.touch_release()
            else:
                d.emu.input.touch_release()
        except Exception:  # noqa: BLE001
            pass
        self._touch_until = None

    def _autopilot(self, ctx):
        if self._touch_until is not None and self.frame >= self._touch_until:
            self._release_touch()
        if self.frame - getattr(self, "_progress_frame", self.frame) > NO_PROGRESS:
            self._give_up(ctx)
            return
        if self.frame < self._next_touch or self.frame - self._ui_since < 20 or self._touch_until is not None:
            return
        ui = self.ui
        throw = self.phase == "down" and self.throws == 0 and self.catch_success is not None
        if self.phase == "fleeing" and ui in ("menu", "moves", "bag", "party", "target"):
            # time over / missed: the boss teleports on its turn; if it can't (or the player's turn comes first) the
            # player runs (the boss's speed is 1 then, so it always works)
            self._touch(TOUCH_RUN if ui == "menu" else TOUCH_BACK, 60)
        elif ui == "menu" and throw:
            self._bag_plan = None
            self._touch(TOUCH_BAG, 60)
        elif ui == "bag" and throw:
            # bag: balls pocket -> page -> the cheapest ball -> USE (the raid decides the result: never waste a
            # Master Ball); if the bag is still open after that, back out and start again from the menu
            plan = getattr(self, "_bag_plan", None)
            if plan is None:
                idx = self.ball_choice([item for item, _q in self.pm.balls()])
                idx = 0 if idx is None else idx
                plan = self._bag_plan = [TOUCH_BAG_BALLS] + [TOUCH_BAG_NEXT] * (idx // 6) + \
                    [TOUCH_BAG_SLOTS[idx % 6], TOUCH_BAG_USE, TOUCH_BACK, TOUCH_BACK]
            if plan:
                self._touch(plan.pop(0), 60)
            else:
                self._bag_plan = None
                self._touch(TOUCH_BACK, 60)
        elif ui == "menu":
            self._touch(TOUCH_FIGHT)
        elif ui == "moves":
            slot = self.choose_slot(ctx)
            self._touched_slot = slot
            self._touch(TOUCH_MOVE[slot])
        elif ui == "yesno":
            self._touch(TOUCH_YES_NEXT)
        elif ui == "party_faint":
            slot = self.first_healthy_slot()
            if self._party_step % 2 == 0 and slot is not None:
                self._touch(TOUCH_PARTY[slot], 50)
            else:
                self._touch(TOUCH_SHIFT, 50)
            self._party_step += 1
        elif ui in ("bag", "party", "run", "target"):
            self._touch(TOUCH_BACK, 50)

    def _give_up(self, ctx):
        """auto_pick made no progress for 10 s: hand the controls back, and log what the game showed (F3 reports)."""
        self.auto = False
        self._release_touch()
        d = self.core
        try:
            state = "command %d state %s" % (d.r32(ctx + CTX_COMMAND), list(d.read(ctx + CTX_CUR_CMD_STATE, 4)))
        except Exception:  # noqa: BLE001
            state = "?"
        log.warning("raid: auto-pick gave up after %d frames without progress (phase %s, ui %s, decision %s, %s, "
                    "yes/no %d party %d frames ago)", NO_PROGRESS, self.phase, self.ui, self.decision, state,
                    self.frame - self._yesno_frame, self.frame - self._party_frame)
        self._note("auto-pick gave up (ui %s, %s)" % (self.ui, state))

    BALL_ORDER = (4, 3, 2, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 1)   # Poké, Great, Ultra, the rest, Master last

    def ball_choice(self, items):
        best = None
        for i, it in enumerate(items):
            if it in self.BALL_ORDER and (best is None or self.BALL_ORDER.index(it) < self.BALL_ORDER.index(items[best])):
                best = i
        return best

    def choose_slot(self, ctx) -> int:
        usable = self.usable_slots(ctx)
        if not usable:
            return 0
        if self.last_slot in usable:
            return self.last_slot
        return max(usable, key=lambda s: (self.move_score(ctx, s), -s))

    def usable_slots(self, ctx) -> list:
        d = self.core
        bm = self._bmon(ctx, 0)
        return [i for i in range(4) if d.r16(bm + BM_MOVES + 2 * i) and d.r8(bm + BM_PP + i)]

    def move_score(self, ctx, slot) -> float:
        d = self.core
        bm = self._bmon(ctx, 0)
        mv = d.r16(bm + BM_MOVES + 2 * slot)
        info = self._move(mv)
        if info is None:
            return 1.0
        power, mtype, cls = info
        if cls == 2 or power <= 1:
            return power * 0.1
        mine = {d.r8(bm + BM_TYPE1), d.r8(bm + BM_TYPE1 + 1)}
        boss = self._bmon(ctx)
        eff = 1.0
        for t in {d.r8(boss + BM_TYPE1), d.r8(boss + BM_TYPE1 + 1)}:
            eff *= self._type_mult(mtype, t)
        return power * (1.5 if mtype in mine else 1.0) * eff

    def _move(self, move):
        """(power, type, class) from the player's own ROM (waza_tbl), or None."""
        if self._move_info is None:
            self._move_info = {}
            try:
                from . import ndsrom
                path = getattr(self.core, "rom_path", None)
                if path:
                    rom = ndsrom.NDSRom(path)
                    try:
                        narc = rom.narc(ndsrom.MOVES_NARC)
                        for i in range(1, min(len(narc), 468)):
                            raw = narc.member(i, 0, 6)
                            self._move_info[i] = (raw[3], raw[4], raw[2])
                    finally:
                        rom.close()
            except Exception:  # noqa: BLE001
                log.exception("raid: move data")
        return self._move_info.get(move)

    def _type_mult(self, atk, dfn) -> float:
        if self._chart is None:
            chart = {}
            a, code = TYPE_CHART
            d = self.core
            if d.read(a, len(code)) == code:
                raw = d.read(a, 3 * 130)
                o = 0
                while o + 3 <= len(raw) and raw[o] != 0xFF:
                    if raw[o] == 0xFE:
                        o += 3
                        continue
                    chart.setdefault((raw[o], raw[o + 1]), raw[o + 2] / 10)
                    o += 3
            self._chart = chart
        return self._chart.get((atk, dfn), 1.0)

    def first_healthy_slot(self):
        """The first party Pokémon that can battle (the battle's own party copy, battler 0)."""
        try:
            d = self.core
            bs = d.r32(self.pm._battle_app() + 0x1C)
            party = d.r32(bs + BS_PARTIES)
            n = min(d.r32(party + 4), 6)
            ctx = self._ctx()
            active = d.r8(ctx + 0x219C) if ctx else -1          # selectedPartySlot[0]
            for i in range(n):
                p = pk4.PK4(pk4._plain_of(d.read(party + 8 + i * pk4.PARTY_SIZE, pk4.PARTY_SIZE)))
                if p.species and not p.is_egg and p.hp > 0 and i != active:
                    return i
        except Exception:  # noqa: BLE001
            log.exception("raid: first healthy")
        return None

    # --- end
    def _check_end(self):
        if self.phase == "starting":
            return
        if self.pm.in_battle():
            return
        if not self.pm.is_safe():
            self._end_wait = getattr(self, "_end_wait", 0) + 1
            if self._end_wait < 60 * 120 and self.result != RESULT_LOSE:
                return
        r = self.result
        if r == 0 and getattr(self, "_player_alive", 1) == 0 and not self.missed:
            r = RESULT_LOSE                           # no result seen, but nobody on the player's side could fight
        if r == RESULT_LOSE:
            self._finish("blackout")
        elif r == RESULT_CAPTURED or (self.phase == "down" and self.catch_success and self.throws):
            self.events.append({"type": "caught", "key": self._caught_key(), "shiny": self.shiny})
            self._finish("caught")
        elif self.missed:
            self.events.append({"type": "fled"})
            self._finish("missed")
        elif r == RESULT_ENEMY_FLED or self.flee_asked or self.phase == "fleeing":
            self.events.append({"type": "fled"})
            self._finish("fled")
        elif r == RESULT_WIN:
            self.escaped = True
            self._finish("escaped")
        else:
            self.events.append({"type": "left"})
            self._finish("left")

    def _caught_key(self):
        if self.pid is None:
            return None
        try:
            for rec in self.pm.party_checked() or []:
                if struct.unpack_from("<I", rec, 0)[0] == self.pid:
                    from .platinum import mon_key
                    return mon_key(rec)
            p = pk4.PK4(pk4._plain_of(self.pm.party_raw()[0]))       # PC: same PID + the player's IDs
            return "%08x%04x%04x" % (self.pid, p.tid, p.sid)
        except Exception:  # noqa: BLE001
            return None

    def _finish(self, outcome):
        if self.phase == "over":
            return
        if outcome == "blackout":
            self.events.append({"type": "blackout"})
        self.phase = "over"
        self.outcome = outcome
        self.decision = None
        self.auto = False
        self.remove()
        self.events.append({"type": "ended", "outcome": outcome})
        self._note("ended: %s (result %d)" % (outcome, self.result))

    def _drain(self):
        ev, self.events = self.events, []
        return ev

    def _note(self, what):
        self.log.append((self.frame, what))
        if len(self.log) > 400:
            del self.log[:100]
        log.debug("raid: %s", what)


# --------------------------------------------------------------------------------------------------------------
# The den
# --------------------------------------------------------------------------------------------------------------
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
FACING = {0: (0, -1), 1: (0, 1), 2: (-1, 0), 3: (1, 0)}       # Platinum DIR_*
DEN_W, DEN_H = 32, 48                                         # world units: 2 x 3 tiles (a friend is 32 x 32)


class Den:
    """The raid den on a Platinum tile: spot = {map (header id), x, y (global tile coords on the outdoor grid)}."""

    def __init__(self, spot: dict, game_id: str = "platinum"):
        self.spot = dict(spot)
        self.map = int(spot["map"])
        self.x, self.y = int(spot["x"]), int(spot["y"])
        self._bump = None
        self._matrix = {}

    def _matrix_of(self, pm):
        from . import sinnoh_view
        return lambda m: sinnoh_view.map_matrix(pm.m, m, self._matrix)

    def on_map(self, pm) -> bool:
        try:
            if not pm.in_game():
                return False
            from . import sinnoh_view
            return sinnoh_view.same_space(pm.map_id(), self.map, self._matrix_of(pm))
        except Exception:  # noqa: BLE001
            return False

    def offset(self, pm, neighbor_shift=None):
        if not self.on_map(pm):
            return None
        p = pm.position()
        return self.x - p["x"], self.y - p["y"]

    def near(self, pm) -> bool:
        if not self.on_map(pm) or pm.walking():
            return False
        p = pm.position()
        return abs(self.x - p["x"]) + abs(self.y - p["y"]) == 1

    def facing_den(self, pm) -> bool:
        p = pm.position()
        v = FACING.get(p["facing"] & 3)
        return v is not None and (p["x"] + v[0], p["y"] + v[1]) == (self.x, self.y)

    def blocks(self, pm, direction: str) -> bool:
        d = DIRS.get(direction)
        if d is None or not self.on_map(pm):
            return False
        p = pm.position()                       # x/y = the destination tile while stepping
        if (p["x"] + d[0], p["y"] + d[1]) != (self.x, self.y):
            return False
        self._bump = (time.time(), d)
        return True

    def pressed_a(self, pm) -> bool:
        try:
            if not pm.is_safe() or not self.near(pm):
                return False
        except Exception:  # noqa: BLE001
            return False
        if self.facing_den(pm):
            return True
        p = pm.position()
        b = self._bump
        return bool(b and time.time() - b[0] < 3 and (p["x"] + b[1][0], p["y"] + b[1][1]) == (self.x, self.y))

    def _camera(self, pm):
        """The camera the top screen shows now (it lags DISPLAY_LAG emulated frames behind RAM), from a small history
        of our own - for callers that don't pass the app's SinnohView camera."""
        from . import sinnoh_view
        cams = self.__dict__.setdefault("_cams", [])
        fs = pm.field_system() if pm.overworld_running() else None
        cam = sinnoh_view.read_camera(pm.m, fs) if fs else None
        frame = getattr(pm.m, "frame_count", None)
        if frame is None:
            return cam
        if cams and frame <= cams[-1][0]:
            cams.clear()
        cams.append((frame, cam))
        del cams[:-12]
        best = None
        for f, c in cams:
            if f <= frame - sinnoh_view.DISPLAY_LAG:
                best = (f, c)
        if best is None or any(c is None for f, c in cams if f >= best[0]):
            return None
        return best[1]

    def draw(self, surface, pm, cam=None, *, off=(0, 0), mask=None, t=None, height=None, neighbor_shift=None):
        """Draw the den on the top screen part (256 x 192 at `off`) of `surface`, or an edge arrow when it's off
        screen. cam = the camera the top screen shows (SinnohView.display); None = YellowLink's own camera history
        (call draw every frame then). mask/neighbor_shift: accepted for the same call as raid_gb.Den (unused).
        Returns True if the den is visible."""
        import pygame
        from . import sinnoh_view
        try:
            if not pm.overworld_running() or pm.field_task():
                return False
            d = self.offset(pm)
            if cam is None:
                cam = self._camera(pm)
        except Exception:  # noqa: BLE001
            return False
        if d is None:
            return False
        t = time.time() if t is None else t
        ox, oy = off
        p = pm.position()
        ph = p["ph"] if height is None else height
        if cam is not None:
            anchor = sinnoh_view.billboard_anchor(self.x * 16, ph, self.y * 16)
            r = cam.billboard(anchor, DEN_W, DEN_H)
            if r:
                left, top, w, h, _z = r
                if left < 256 and left + w > 0 and top < 192 and top + h > 0:
                    img = pygame.transform.smoothscale(raid_art.ds_den(t), (max(1, int(w)), max(1, int(h))))
                    clip = surface.get_clip()
                    surface.set_clip(pygame.Rect(ox, oy, 256, 192))
                    surface.blit(img, (int(round(left)) + ox, int(round(top)) + oy))
                    surface.set_clip(clip)
                    return True
        raid_art.arrow(surface, (ox, oy, 256, 192), d[0], d[1], abs(d[0]) + abs(d[1]), t)
        return False
