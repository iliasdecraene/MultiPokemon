"""Raid prototype on the real Pokémon Platinum ROM (py-desmume, headless). Throwaway - proves the risky parts only.

  1. start: four game functions called from Python through the field's table dispatcher (pt_common.Caller):
     FieldBattleDTO_New -> FieldBattleDTO_Init -> CreateWildMon_Scripted(species, level) -> [legendary flag] ->
     Encounter_NewVsWild. The game's own FieldTask_WildEncounter then runs the battle, the blackout, the catch.
  2. battle copy overridden (BattleContext.battleMons[1]: moves/PP/def/spdef/maxHP/curHP), never the DTO mon
  3. pool: per-frame watchdog on battleMons[1].curHP (0 -> 1), sync at BATTLE_CONTROL_INIT_COMMAND_SELECTION
  4. timer: ctx->command == COMMAND_SELECTION_INPUT and curCommandState[0] = a waiting state; auto-pick by touch
  5. balls blocked: exec hook at BattleControllerPlayer_CommandSelectionInput entry turns a ball picked in the
     bag into PLAYER_INPUT_CANCEL (the menu comes back, the ball stays in the bag)
  6. catch: exec hook at `cmp r1, #0xff` in BattleScript_CalcCatchShakes sets catchRate = 255 -> 4 shakes
  7. flee: boss moves = [Teleport] -> the wild mon flees
Fixture: dev/states/pt_sl_route202.dsst (Turtwig Lv7, 10 Master Balls + 5 Poké Balls).
Run: python dev/raids/proto/pt_raid.py
"""
import os
import struct
import sys

from common import STATES, OUT, check, info, done
from pt_common import (EpilogueCaller as Caller, FieldBattleDTO_New, FieldBattleDTO_Init, CreateWildMon_Scripted,
                       Encounter_NewVsWild, HEAP_ID_FIELD2, BATTLE_TYPE_WILD_MON, DTO_BATTLE_STATUS,
                       BATTLE_STATUS_LEGENDARY, S_FIELD_SYSTEM, OV6_CODE, Regs)
from ds import DS
from yellowlink import pk4
from yellowlink.platinum import PlatinumMemory, CTX_BATTLE_MONS, BATTLE_MON_SIZE

# BattleContext / BattleMon offsets (pret structs, confirmed in the compiled code: see DESIGN.md appendix)
CTX_CUR_CMD_STATE = 0x00      # u8[4]
CTX_COMMAND = 0x08            # int: BATTLE_CONTROL_*
CTX_IO_BUFFER = 0x2300        # u32 ioBuffer[4][64] (BattleContext_IOBufferVal @0x02259AB4: ldrb [ctx + i*0x100 + 0x2300])
BM_DEF, BM_SPDEF, BM_MOVES, BM_PP, BM_CUR_HP, BM_MAX_HP, BM_STATUS = 0x04, 0x0A, 0x0C, 0x2C, 0x4C, 0x50, 0x6C
CONTROL_INIT_COMMAND_SELECTION, CONTROL_COMMAND_SELECTION_INPUT = 4, 5
WAITING = {1: "menu", 4: "moves", 6: "target", 8: "bag", 10: "party", 12: "run"}
LOCKED = {13, 14}             # COMMAND_SELECTION_WAIT / END
ITEM_SELECT = 8
PLAYER_INPUT_CANCEL = 0xFF
HOOK_SELECT_INPUT = 0x0224BB28          # BattleControllerPlayer_CommandSelectionInput (overlay 16)
SIG_SELECT_INPUT = bytes.fromhex("f8b5a8b0071c0791f2f7f4f9")
HOOK_CATCH_CMP = 0x0224A8D6             # BattleScript_CalcCatchShakes: cmp r1, #0xff (r1 = catch rate)
SIG_CATCH_CMP = bytes.fromhex("ff2901d3042537e025480022")
HOOK_HP_UPDATE = 0x02241814             # BtlCmd_UpdateHealthBarValue (battle cmd 26) after the battler is resolved:
SIG_HP_UPDATE = bytes.fromhex("1c48171c2518c02047431b48")   # r2 = battler, r4 = BattleContext; next it applies hpCalcTemp
CTX_HP_CALC_TEMP = 0x215C                # s32 battleCtx->hpCalcTemp (negative = damage)
HOOK_FAINT_TEST = 0x022418E8            # BtlCmd_TryFaintMon: ldr r1, [r2, r1] = battleMons[r4].curHP; cmp r1, #0
SIG_FAINT_TEST = bytes.fromhex("5158002913d16c6736f66cff")
SPLASH, TELEPORT, PSYCHIC_M = 150, 100, 94
LATIAS = 380
TEST_LEVEL = 12             # low level so the Lv7 Turtwig does visible damage (no stat hacks)
BARS = 1.5
SLEEP_3 = 3
TOUCH_FIGHT = (128, 90)
TOUCH_MOVE = [(64, 48), (192, 48), (64, 104), (192, 104)]


def write_bytes(d, addr, data):
    """dev/platinum/ds.DS has no bulk write: 32-bit words (data is 4-aligned here)."""
    for i in range(0, len(data), 4):
        d.w32(addr + i, struct.unpack_from("<I", data, i)[0])


class Server:
    def __init__(self, pool=None):
        self.pool = self.max = pool        # None: set from the boss's real max HP x bars when the battle starts
        self.hits = []

    def hit(self, d):
        self.pool = max(0, self.pool - d)
        self.hits.append(d)


class RaidPT:
    def __init__(self, d, species, level, moves, bars, server, clamp=True):
        self.d, self.pm, self.r = d, PlatinumMemory(d), Regs(d.emu)
        self.caller = Caller(d)
        self.species, self.level, self.moves = species, level, moves
        self.bars, self.server, self.clamp = bars, server, clamp
        self.local_max = None
        self.phase = "idle"
        self.frame = 0
        self.events = []
        self.last_hp = None
        self.last_cmd = None
        self.wait_since = None
        self.decisions = []
        self.overridden = False
        self.cur_moves = list(moves)
        self.hp_zero_at = None
        self.on_wait = None
        self._last_wait = None
        d.emu.memory.register_exec(HOOK_SELECT_INPUT, self._hook(self._on_select_input))
        d.emu.memory.register_exec(HOOK_CATCH_CMP, self._hook(self._on_catch_cmp))
        d.emu.memory.register_exec(HOOK_FAINT_TEST, self._hook(self._on_faint_test))
        d.emu.memory.register_exec(HOOK_HP_UPDATE, self._hook(self._on_hp_update))
        self.guard = True

    def _hook(self, fn):
        def cb(addr, size):
            try:
                fn(addr)
            except Exception as e:  # noqa: BLE001
                self.log("hook error %r" % e)
            return 0
        return cb

    def log(self, what):
        self.events.append((self.frame, what))

    def ctx(self):
        _bs, ctx = self.pm._battle_ctx()
        return ctx

    def bmon(self, ctx, i=1):
        return ctx + CTX_BATTLE_MONS + BATTLE_MON_SIZE * i

    # --- hooks -----------------------------------------------------------------------------------------------
    def _on_select_input(self, addr):
        if self.d.read(addr, 12) != SIG_SELECT_INPUT or self.phase == "catch":
            return
        ctx = self.ctx()
        if not ctx or self.d.r8(ctx + CTX_CUR_CMD_STATE) != ITEM_SELECT:
            return
        v = self.d.r32(ctx + CTX_IO_BUFFER)
        if v and (v & 0xFF) != PLAYER_INPUT_CANCEL and 1 <= (v & 0xFFFF) <= 16:   # BattleItemUse.item = a ball
            self.d.w32(ctx + CTX_IO_BUFFER, PLAYER_INPUT_CANCEL)
            self.log("ball blocked (item %d)" % (v & 0xFFFF))

    def _on_hp_update(self, addr):
        """Focus-Sash style: a hit that would take the boss to 0 while the raid isn't over leaves it at 1 HP
        (like the game's own CheckHoldOnWith1HP), so neither the battle copy nor the Pokémon it is written
        back into (EmitUpdatePartyMon) ever has 0 HP. The last point is counted here; the frame watch counts
        the rest of the drop."""
        if self.d.read(addr, 12) != SIG_HP_UPDATE or not self.guard:
            return
        if self.r["r2"] != 1 or self.phase not in ("fight", "catch"):
            return
        ctx = self.r["r4"]
        bm = self.bmon(ctx)
        cur = self.d.r32(bm + BM_CUR_HP)
        calc = struct.unpack("<i", struct.pack("<I", self.d.r32(ctx + CTX_HP_CALC_TEMP)))[0]
        if calc < 0 and cur + calc <= 0 and cur > 0:
            self.d.w32(ctx + CTX_HP_CALC_TEMP, (-(cur - 1)) & 0xFFFFFFFF)
            if self.phase == "fight":
                self.server.hit(1)
            self.log("hp update clamped: %d -> 1 (damage %d)" % (cur, -calc))

    def _on_faint_test(self, addr):
        """The game's own faint test (BtlCmd_TryFaintMon). A frame watchdog is too late in Platinum: the HP
        update and this test can run in the same frame. Boss at 0 HP while the raid isn't over -> 1 HP, and the
        rest of the bar is counted for the group here (the watchdog may never see the 0)."""
        if self.d.read(addr, 12) != SIG_FAINT_TEST:
            return
        if self.r["r4"] != 1 or self.phase not in ("fight", "catch"):
            return
        ctx = self.ctx()
        if not ctx:
            return
        bm = self.bmon(ctx)
        self.log("faint test hp=%d" % self.d.r32(bm + BM_CUR_HP))
        if self.d.r32(bm + BM_CUR_HP) != 0:
            return
        if self.hp_zero_at is None:
            self.hp_zero_at = self.frame
        if not self.guard:
            return
        if self.phase == "fight" and self.last_hp:
            self.server.hit(self.last_hp)
            self.log("hit %d -> pool %d (at faint test)" % (self.last_hp, self.server.pool))
        self.d.w32(bm + BM_CUR_HP, 1)
        self.last_hp = 1
        self.log("guarded at 1")
        if self.phase == "fight" and self.server.pool == 0:
            self.enter_catch(ctx)

    def _on_catch_cmp(self, addr):
        if self.d.read(addr, 12) != SIG_CATCH_CMP:
            return
        self.log("catch calc rate=%d" % self.r["r1"])
        if self.phase == "catch":
            self.r["r1"] = 255

    # --- start -----------------------------------------------------------------------------------------------
    def start(self):
        d, c = self.d, self.caller
        assert self.pm.is_safe() and d.read(*OV6_CODE[:1], 8) == OV6_CODE[1]
        fs = d.r32(S_FIELD_SYSTEM)
        st = {}

        def got_dto(v):
            st["dto"] = v
            c.call(FieldBattleDTO_Init, [v, fs])
            c.call(CreateWildMon_Scripted, [fs, self.species, self.level, v], got_mon)

        def got_mon(_):
            dto = st["dto"]
            # raid moves go into the wild Pokémon itself (the battle copy is written back into it on every HP
            # update, so editing only the battle copy leaks into the caught Pokémon)
            party = d.r32(dto + 4 + 4 * 1)
            rec = d.read(party + 8, 236)
            pkm = pk4.PK4.from_encrypted(rec)
            self.wild_before = {"moves": list(pkm.moves), "pid": struct.unpack_from("<I", rec, 0)[0]}
            pkm.moves = (self.moves + [0, 0, 0, 0])[:4]
            pkm.pp = [20 if m else 0 for m in pkm.moves]
            write_bytes(d, party + 8, pkm.encrypted())
            self.raid_moves = list(pkm.moves)
            d.w32(dto + DTO_BATTLE_STATUS, d.r32(dto + DTO_BATTLE_STATUS) | BATTLE_STATUS_LEGENDARY)
            c.call(Encounter_NewVsWild, [fs, dto])

        c.call(FieldBattleDTO_New, [HEAP_ID_FIELD2, BATTLE_TYPE_WILD_MON], got_dto)
        self.phase = "starting"
        self.st = st

    # --- per frame -------------------------------------------------------------------------------------------
    def tick(self, n=1):
        for _ in range(n):
            self.d.run(1)
            self.frame += 1
            self._after_frame()

    def _after_frame(self):
        d = self.d
        ctx = self.ctx()
        if not ctx:
            return
        bm = self.bmon(ctx)
        if d.r16(bm) != self.species:
            return
        if self.phase == "starting":
            # the battle mon is filled in over a few frames: wait until it is complete (max HP set, full HP)
            if not (d.r32(bm + BM_MAX_HP) and d.r32(bm + BM_CUR_HP) == d.r32(bm + BM_MAX_HP)
                    and d.r8(bm + 0x34) == self.level):
                return
            self._override(ctx)
            self.phase = "fight"
        cmd = d.r32(ctx + CTX_COMMAND)
        state = d.r8(ctx + CTX_CUR_CMD_STATE)
        hp = d.r32(bm + BM_CUR_HP)
        # turn start: resync the local segment, refill the boss's PP
        if cmd == CONTROL_INIT_COMMAND_SELECTION and self.last_cmd != cmd:
            if self.phase == "fight" and self.server.pool > 0:
                seg = (self.server.pool - 1) % self.local_max + 1
                d.w32(bm + BM_CUR_HP, seg)
                hp = seg
            for i in range(4):                        # refill the boss's PP (out of PP -> Struggle)
                if d.r16(bm + BM_MOVES + 2 * i):
                    d.w8(bm + BM_PP + i, 20)
            if self.phase == "flee":
                self.set_moves(ctx, [TELEPORT])
            if self.phase == "catch":
                d.w32(bm + BM_STATUS, SLEEP_3)        # asleep: the boss doesn't attack (no move change)
            self.log("turn start pool=%d" % self.server.pool)
        # damage + watchdog
        if self.last_hp is not None and hp < self.last_hp and self.phase == "fight":
            self.server.hit(self.last_hp - hp)
            self.log("hit %d -> pool %d" % (self.last_hp - hp, self.server.pool))
        if hp == 0 and self.hp_zero_at is None:
            self.hp_zero_at = self.frame
        if hp == 0 and self.clamp and self.phase in ("fight", "catch"):
            d.w32(bm + BM_CUR_HP, 1)
            hp = 1
            self.log("clamped to 1")
        self.last_hp = hp
        if self.phase == "fight" and self.server.pool == 0 and self.clamp:
            self.enter_catch(ctx)
        # timer: waiting = the controller's command-selection state is one that needs the player
        now = WAITING.get(state) if cmd == CONTROL_COMMAND_SELECTION_INPUT else None
        if now and now != self._last_wait:
            if self.wait_since is None:
                self.wait_since = self.frame
            self.log("waiting (%s)" % now)
            if self.on_wait:
                self.on_wait(self, now)
        if self.wait_since is not None and (cmd != CONTROL_COMMAND_SELECTION_INPUT or state in LOCKED):
            self.decisions.append((self.wait_since, self.frame))
            self.wait_since = None
            self.log("locked in")
        self._last_wait = now
        self.last_cmd = cmd

    def _override(self, ctx):
        """Battle copy: nothing to change but HP (moves came from the DTO). Local max = the boss's real max HP;
        the pool is counted in bars of that size."""
        d = self.d
        bm = self.bmon(ctx)
        self.real = {"moves": [d.r16(bm + BM_MOVES + 2 * i) for i in range(4)], "maxhp": d.r32(bm + BM_MAX_HP)}
        self.local_max = self.real["maxhp"]
        if self.server.pool is None:
            self.server.pool = self.server.max = int(self.bars * self.local_max)
        seg = (self.server.pool - 1) % self.local_max + 1
        d.w32(bm + BM_CUR_HP, seg)
        self.last_hp = seg
        self.log("override (real moves %s, max hp %d)" % (self.real["moves"], self.real["maxhp"]))

    def set_moves(self, ctx, moves):
        d = self.d
        bm = self.bmon(ctx)
        self.cur_moves = list(moves)
        for i in range(4):
            mv = moves[i] if i < len(moves) else 0
            d.w16(bm + BM_MOVES + 2 * i, mv)
            d.w8(bm + BM_PP + i, 30 if mv else 0)

    def enter_catch(self, ctx):
        d = self.d
        bm = self.bmon(ctx)
        self.phase = "catch"
        d.w32(bm + BM_CUR_HP, 1)
        self.last_hp = 1
        d.w32(bm + BM_STATUS, SLEEP_3)
        self.log("catch phase")

    def enter_flee(self):
        self.phase = "flee"
        ctx = self.ctx()
        if ctx:
            self.set_moves(ctx, [TELEPORT])
        self.log("flee")


def touch(r, xy, frames=6, after=30):
    r.d.emu.input.touch_set_pos(*xy)
    r.tick(frames)
    r.d.emu.input.touch_release()
    r.tick(after)


def press(r, key, after=30):
    from ds import KEYS
    from desmume.controls import keymask
    m = keymask(KEYS[key])
    r.d.emu.input.keypad_add_key(m)
    r.tick(6)
    r.d.emu.input.keypad_rm_key(m)
    r.tick(after)


def ball_pocket(pm):
    return pm.balls()


def run_until(r, cond, max_frames, idle_b=True):
    """Tick until cond(); a test 'player' presses B every 90 idle frames (text boxes; B answers NO)."""
    last = r.frame
    for _ in range(max_frames):
        r.tick()
        if cond():
            return True
        if idle_b and r.wait_since is None and r.frame - last > 90 and not r.pm.is_safe() \
                and not r.pm.in_battle():          # never in battle: B on the command menu = RUN
            press(r, "b", after=10)
            last = r.frame
        if r.wait_since is not None:
            last = r.frame
    return False


def scenario_fight_and_catch(d):
    print("== Platinum: raid battle via injected calls, pool, timer, ball block, guaranteed catch ==")
    srv = Server()
    r = RaidPT(d, LATIAS, TEST_LEVEL, [SPLASH], BARS, srv)
    pm = r.pm
    balls0 = dict(pm.balls())
    p0 = pm.party_checked() or []
    party0 = len(p0)
    lead0 = pk4.PK4(pk4._plain_of(p0[0])).exp if p0 else None
    r.start()
    ok = run_until(r, lambda: r.phase == "fight" and r.wait_since is not None, 3000)
    calls = [x for x in r.caller.log if x.startswith("call")]
    check("4 game functions called from Python (DTO new/init, CreateWildMon_Scripted, Encounter_NewVsWild)",
          len(calls) == 4 and not any("error" in x for x in r.caller.log), str(r.caller.log))
    bi = pm.battle_info()
    check("wild LATIAS battle running at the requested level, first command menu reached", ok and bi
          and bi["dex"] == LATIAS and bi["level"] == TEST_LEVEL, "battle_info %s" % (bi and {k: bi[k] for k in ("dex", "level", "kind")}))
    d.shot(os.path.join(OUT, "pt_raid_start.png"))
    st = {"script": ["ball"], "threw": 0}

    def on_wait(rr, what):
        if what != "menu":
            return
        if rr.phase == "catch":
            st["threw"] += 1
            st["todo"] = "throw"
        elif st["script"]:
            st["todo"] = st["script"].pop(0)
        else:
            st["todo"] = "fight"

    r.on_wait = on_wait
    on_wait(r, "menu")
    end = None
    for _ in range(30000):
        todo = st.pop("todo", None)
        if todo == "fight":                         # what the 10-s timeout injects: FIGHT, move slot 0
            r.tick(20)
            touch(r, TOUCH_FIGHT)
            touch(r, TOUCH_MOVE[0])
        elif todo == "ball":                       # BAG -> Poké Balls pocket -> Master Ball -> USE (blocked)
            r.tick(20)
            bag_throw(r, slot=0)
        elif todo == "throw":                      # catch phase: Poké Ball, then the after-catch screens
            r.tick(20)
            bag_throw(r, slot=1)
            after_catch_inputs(r)
            end = r.frame
            break
        r.tick()
        if r.frame % 600 == 0:
            info("f%d phase %s pool %d events %s" % (r.frame, r.phase, srv.pool, [e[1] for e in r.events[-3:]]))
        if not pm.in_battle() and r.phase != "starting":
            end = r.frame
            break
        # (no idle B in battle: on Platinum's command menu B is the RUN shortcut)
    ev = [e[1] for e in r.events]
    info("events (first 30): %s" % ev[:30])
    cat = ev.index("catch phase") if "catch phase" in ev else None
    info("events from the catch phase: %s" % (ev[cat:cat + 25] if cat is not None else None))
    d.shot(os.path.join(OUT, "pt_raid_end_of_loop.png"))
    info("decision frames (scripted): %s" % [b - a for a, b in r.decisions][:12])
    check("ball picked during the fight is cancelled back to the menu", any(e.startswith("ball blocked") for e in ev))
    check("damage adds up to the pool (%s bars of %s HP)" % (BARS, r.local_max), sum(srv.hits) == srv.max
          and srv.pool == 0, "hits %s" % srv.hits)
    guards = sum(1 for e in ev if e.startswith("hp update clamped") or e in ("guarded at 1", "clamped to 1"))
    check("pool ran over several bars; boss never reached 0 HP (Focus-Sash style clamp at the HP update)",
          guards >= 2 and "faint test hp=0" not in ev, "clamps %d" % guards)
    check("timer: waiting/locked-in detected every turn", len(r.decisions) >= 4)
    rates = [e for e in ev if e.startswith("catch calc")]
    info("catch calc seen: %s" % rates)
    for _ in range(900):
        r.tick()
        if pm.is_safe():
            break
    party = pm.party_checked() or []
    check("guaranteed catch in the catch phase: LATIAS in the party", len(party) == party0 + 1,
          "party %d -> %d, end frame %s" % (party0, len(party), end))
    if len(party) == party0 + 1:
        from yellowlink.platinum import mon_info
        p = pk4.PK4(pk4._plain_of(party[-1]))
        info("caught: %s moves %s hp %s (raid moves %s, natural %s)" % (mon_info(party[-1]), p.moves, p.hp,
                                                                        r.raid_moves, r.wild_before["moves"]))
        check("caught mon = the raid boss (same PID), raid moveset, no leftovers from the battle copy",
              struct.unpack_from("<I", party[-1], 0)[0] == r.wild_before["pid"] and list(p.moves) == r.raid_moves)
        fixed = repair_caught(r, party)
        r.tick(10)
        p2 = pk4.PK4(pk4._plain_of((pm.party_checked() or party)[-1]))
        check("HP repaired after the catch (battle HP was %d)" % p.hp, p.hp > 0 or (fixed is not None and p2.hp == 1
              and p2.checksum_ok()), "hp now %d" % p2.hp)
    lead1 = pk4.PK4(pk4._plain_of(party[0])).exp if party else None
    check("no EXP for the lead (the boss never fainted)", lead0 is not None and lead0 == lead1, "%s -> %s" % (lead0, lead1))
    b1 = dict(pm.balls())
    check("balls: blocked throw kept its ball, catch used one", sum(b1.values()) == sum(balls0.values()) - 1,
          "%s -> %s" % (balls0, b1))
    d.shot(os.path.join(OUT, "pt_raid_after_catch.png"))


def after_catch_inputs(r):
    """Test player: A through "Gotcha!", the Pokédex page and into the nickname screen (A answers YES), then
    START (cursor to OK) + A. The A presses type a nickname - fine for a test."""
    for i in range(30):
        r.tick(60)
        if i >= 3 and r.pm.in_battle():
            press(r, "a", after=0)
    press(r, "start", after=40)
    press(r, "a", after=60)
    for i in range(1500):
        r.tick()
        if r.pm.is_safe():
            return True
        if i % 120 == 60:
            press(r, "b", after=0)
    return False


def repair_caught(r, party):
    """After the catch: the battle wrote the boss's battle HP (0 at a bar end) into the Pokémon. Give it
    1 HP (it was caught at 1 HP) - written to the save party only while is_safe(), like the other forced
    writes. Returns the slot fixed or None."""
    pm, d = r.pm, r.d
    for i, rec in enumerate(party):
        p = pk4.PK4(pk4._plain_of(rec))
        if struct.unpack_from("<I", rec, 0)[0] == r.wild_before["pid"] and p.hp == 0 and pm.is_safe():
            p.hp = 1
            write_bytes(d, pm.party_addr(i), p.encrypted())
            return i
    return None


def bag_throw(r, slot=0):
    """BAG (bottom screen) -> balls pocket -> slot -> USE. Coordinates from screenshots of the battle bag."""
    touch(r, BAG_BUTTON, after=60)
    touch(r, BAG_BALLS_POCKET, after=60)
    touch(r, BAG_ITEM_SLOTS[slot], after=60)
    touch(r, BAG_USE, after=40)


BAG_BUTTON = (40, 168)          # bottom-screen coordinates read off screenshots of the battle bag
BAG_BALLS_POCKET = (192, 56)
BAG_ITEM_SLOTS = [(64, 30), (192, 30)]   # pocket order here: Master Ball, Poké Ball
BAG_USE = (100, 174)


def scenario_flee(d):
    print("== Platinum: the boss flees (Teleport) ==")
    srv = Server(10_000)
    r = RaidPT(d, LATIAS, TEST_LEVEL, [SPLASH], BARS, srv)
    r.start()
    run_until(r, lambda: r.phase == "fight" and r.wait_since is not None, 3000)
    r.enter_flee()
    r.tick(20)
    touch(r, TOUCH_FIGHT)
    touch(r, TOUCH_MOVE[0])
    ok = run_until(r, lambda: not r.pm.in_battle(), 3000)
    for _ in range(600):
        r.tick()
        if r.pm.is_safe():
            break
    ev = [e[1] for e in r.events]
    check("boss with only Teleport leaves; back in the overworld", ok and r.pm.is_safe(), str(ev[-6:]))


def scenario_faint_margin(d):
    print("== Platinum: frames between enemy HP 0 and the end of the battle (no clamp) ==")
    srv = Server(1)
    r = RaidPT(d, LATIAS, TEST_LEVEL, [SPLASH], BARS, srv, clamp=False)
    r.guard = False
    r.start()
    run_until(r, lambda: r.phase == "fight" and r.wait_since is not None, 3000)
    r.tick(20)
    touch(r, TOUCH_FIGHT)
    touch(r, TOUCH_MOVE[0])
    fainted_cmd = None
    for _ in range(3000):
        r.tick()
        tests = [f for f, e in r.events if e == "faint test hp=0"]
        if fainted_cmd is None and tests:
            fainted_cmd = tests[0]
        if not r.pm.in_battle():
            break
    margin = fainted_cmd - r.hp_zero_at if fainted_cmd and r.hp_zero_at else None
    info("frames from the first frame we SEE HP 0 to the game's faint test: %s "
         "(0 or less = a per-frame watchdog is too late -> the faint-test hook is needed)" % margin)
    check("the game's faint test (hook) sees the boss at 0 HP", fainted_cmd is not None, "margin %s" % margin)


if __name__ == "__main__":
    which = sys.argv[1:] or ["catch", "flee", "margin"]
    state = os.path.join(STATES, "pt_sl_route202.dsst")
    d = DS(state=state)
    for name, fn in (("catch", scenario_fight_and_catch), ("flee", scenario_flee), ("margin", scenario_faint_margin)):
        if name in which:
            d.load(state)
            fn(d)
    raise SystemExit(done())
