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
SPLASH, TELEPORT, PSYCHIC_M = 150, 100, 94
LATIAS = 380
SLEEP_3 = 3
TOUCH_FIGHT = (128, 90)
TOUCH_MOVE = [(64, 48), (192, 48), (64, 104), (192, 104)]


class Server:
    def __init__(self, pool):
        self.pool = self.max = pool
        self.hits = []

    def hit(self, d):
        self.pool = max(0, self.pool - d)
        self.hits.append(d)


class RaidPT:
    def __init__(self, d, species, level, moves, local_max, server, clamp=True):
        self.d, self.pm, self.r = d, PlatinumMemory(d), Regs(d.emu)
        self.caller = Caller(d)
        self.species, self.level, self.moves = species, level, moves
        self.local_max, self.server, self.clamp = local_max, server, clamp
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
            self.set_moves(ctx, self.cur_moves)
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
        d = self.d
        bm = self.bmon(ctx)
        self.real = {"moves": [d.r16(bm + BM_MOVES + 2 * i) for i in range(4)], "maxhp": d.r32(bm + BM_MAX_HP)}
        self.set_moves(ctx, self.moves)
        d.w16(bm + BM_DEF, 5)            # test only: let a Lv7 Turtwig do visible damage
        d.w16(bm + BM_SPDEF, 5)
        d.w32(bm + BM_MAX_HP, self.local_max)
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
        self.set_moves(ctx, [SPLASH])
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
        if idle_b and r.wait_since is None and r.frame - last > 90 and not r.pm.is_safe():
            press(r, "b", after=10)
            last = r.frame
        if r.wait_since is not None:
            last = r.frame
    return False


def scenario_fight_and_catch(d):
    print("== Platinum: raid battle via injected calls, pool, timer, ball block, guaranteed catch ==")
    srv = Server(100)
    r = RaidPT(d, LATIAS, 40, [SPLASH], 40, srv)
    pm = r.pm
    balls0 = dict(pm.balls())
    party0 = len(pm.party_checked() or [])
    r.start()
    ok = run_until(r, lambda: r.phase == "fight" and r.wait_since is not None, 3000)
    calls = [x for x in r.caller.log if x.startswith("call")]
    check("4 game functions called from Python (DTO new/init, CreateWildMon_Scripted, Encounter_NewVsWild)",
          len(calls) == 4 and not any("error" in x for x in r.caller.log), str(r.caller.log))
    bi = pm.battle_info()
    check("wild LATIAS Lv40 battle running, first command menu reached", ok and bi and bi["dex"] == LATIAS
          and bi["level"] == 40, "battle_info %s" % (bi and {k: bi[k] for k in ("dex", "level", "kind")}))
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
    for _ in range(60000):
        todo = st.pop("todo", None)
        if todo == "fight":                         # what the 10-s timeout injects: FIGHT, move slot 0
            r.tick(20)
            touch(r, TOUCH_FIGHT)
            touch(r, TOUCH_MOVE[0])
        elif todo in ("ball", "throw"):            # BAG -> Poké Balls pocket -> first ball -> USE
            r.tick(20)
            bag_throw(r, slot=1 if todo == "throw" else 0)
        r.tick()
        if not pm.in_battle() and r.phase != "starting":
            end = r.frame
            break
        if r.wait_since is None and r.frame % 90 == 0 and not pm.is_safe():
            press(r, "b", after=10)
    ev = [e[1] for e in r.events]
    info("events (first 30): %s" % ev[:30])
    info("decision frames (scripted): %s" % [b - a for a, b in r.decisions][:12])
    check("ball picked during the fight is cancelled back to the menu", any(e.startswith("ball blocked") for e in ev))
    check("damage from HP drops adds up to the pool", sum(srv.hits) == 100 and srv.pool == 0, "hits %s" % srv.hits)
    check("pool ran over several segments; boss never fainted locally",
          ev.count("clamped to 1") >= 2, "clamps %d" % ev.count("clamped to 1"))
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
        mi = __import__("yellowlink.platinum", fromlist=["mon_info"]).mon_info(party[-1])
        p = pk4.PK4(pk4._plain_of(party[-1]))
        info("caught: %s moves %s hp %s" % (mi, p.moves, p.hp))
        check("caught mon is LATIAS with its real moves (battle copy changes don't leak)",
              mi and mi["dex"] == LATIAS and list(getattr(p, "moves", [])) == r.real["moves"],
              "real %s" % r.real["moves"])
    b1 = dict(pm.balls())
    check("balls: blocked throw kept its ball, catch used one", sum(b1.values()) == sum(balls0.values()) - 1,
          "%s -> %s" % (balls0, b1))
    d.shot(os.path.join(OUT, "pt_raid_after_catch.png"))


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
    r = RaidPT(d, LATIAS, 40, [SPLASH], 40, srv)
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
    res = r.pm.battle_info()
    check("boss with only Teleport leaves; back in the overworld", ok and r.pm.is_safe(), str(ev[-6:]))


def scenario_faint_margin(d):
    print("== Platinum: frames between enemy HP 0 and the end of the battle (no clamp) ==")
    srv = Server(1)
    r = RaidPT(d, LATIAS, 40, [SPLASH], 40, srv, clamp=False)
    r.start()
    run_until(r, lambda: r.phase == "fight" and r.wait_since is not None, 3000)
    r.tick(20)
    touch(r, TOUCH_FIGHT)
    touch(r, TOUCH_MOVE[0])
    fainted_cmd = None
    for _ in range(3000):
        r.tick()
        ctx = r.ctx()
        if r.hp_zero_at is not None and ctx and fainted_cmd is None and r.d.r32(ctx + CTX_COMMAND) in (33, 40):
            fainted_cmd = r.frame
        if not r.pm.in_battle():
            break
    margin = fainted_cmd - r.hp_zero_at if fainted_cmd and r.hp_zero_at else None
    check("HP reaches 0 several frames before the faint handling (watchdog has time)",
          margin is not None and margin >= 5, "margin %s frames" % margin)


if __name__ == "__main__":
    which = sys.argv[1:] or ["catch", "flee", "margin"]
    state = os.path.join(STATES, "pt_sl_route202.dsst")
    d = DS(state=state)
    for name, fn in (("catch", scenario_fight_and_catch), ("flee", scenario_flee), ("margin", scenario_faint_margin)):
        if name in which:
            d.load(state)
            fn(d)
    raise SystemExit(done())
