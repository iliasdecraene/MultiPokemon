"""Raid prototype on the real Pokémon Crystal ROM (PyBoy, headless). Throwaway - proves the risky parts only.

Same scenarios as yellow_raid.py, with Crystal's own mechanisms:
  * start: the overworld runs a "memory script" (RunMemScript: wMapReentryScriptQueueFlag/Bank/Address) pointed at
    the ROM's own WildBattleScript + 1 (25:7CFA = startbattle / reloadmapafterbattle / end), after we set what
    `loadwildmon` would set (wTempWildMonSpecies, wCurPartyLevel, wBattleScriptFlags). The game's own script then
    does the battle, the blackout and the "box full -> Bill calls" handling.
  * overrides at BackUpBGMap2 (right after InitEnemy -> LoadEnemyMon, before the HUD), sync at BattleTurn.loop,
    timer hooks BattleMenu / EnemyTriesToFlee (= choice locked in), balls blocked by PokeBallEffect ->
    IsntTheTimeMessage, catch phase, Teleport flee (wild Teleport always works in Crystal: pret bug note).
Fixture: dev/states/crystal_sl_route29.state (Cyndaquil Lv5 + Pidgey + Sentret, 1 Master Ball + 5 Poké Balls).
Run: python dev/raids/proto/crystal_raid.py
"""
import os

from common import STATES, OUT, check, info, done, sym
from pyboy import PyBoy
from yellowlink import crystal, text

ROM = os.path.join(STATES, "crystal.gbc")
RD = crystal.RomData.from_file(ROM)


def C(name):
    return sym("crystal", name)


W = {k: C(k)[1] for k in (
    "wBattleMode", "wTempWildMonSpecies", "wCurPartyLevel", "wBattleScriptFlags", "wBattleType",
    "wMapReentryScriptQueueFlag", "wMapReentryScriptBank", "wMapReentryScriptAddress", "wEnemyMonSpecies",
    "wEnemyMonMoves", "wEnemyMonPP", "wEnemyMonLevel", "wEnemyMonStatus", "wEnemyMonHP", "wEnemyMonMaxHP",
    "wEnemyMonDefense", "wEnemyMonSpclDef", "wEnemyMonCatchRate", "wBattleResult", "wCurMoveNum",
    "wNumBalls", "wBalls", "wWildMonMoves", "wWildMonPP", "wCurPocket", "wLastPocket")}
BALL_POCKET = 1
CELEBI, SPLASH, TELEPORT, PSYCHIC_M = 251, 150, 100, 94
POKE_BALL, MASTER_BALL = 5, 1
SLP = 7
WILD_BATTLE_SCRIPT_STARTBATTLE = (0x25, C("WildBattleScript")[1] + 1)
AUTO_FIGHT = ("up", "left", "a")


class Mem:
    """D000-DFFF through WRAM bank 1 (CLAUDE.md: Crystal WRAM banking), C000-CFFF plain."""

    def __init__(self, m):
        self.m = m

    def __getitem__(self, a):
        return self.m[1, a] if 0xD000 <= a < 0xE000 else self.m[a]

    def __setitem__(self, a, v):
        if 0xD000 <= a < 0xE000:
            self.m[1, a] = v
        else:
            self.m[a] = v

    def r16(self, a):
        return self[a] << 8 | self[a + 1]

    def w16(self, a, v):
        self[a], self[a + 1] = v >> 8, v & 0xFF


def screen_lines(pb):
    m = pb.memory
    tm = bytes(m[crystal.TILEMAP + i] for i in range(360))
    return [text.decode(tm[r * 20:(r + 1) * 20] + b"\x50").strip() for r in range(18)]


def balls(mm):
    return {mm[W["wBalls"] + 2 * i]: mm[W["wBalls"] + 2 * i + 1] for i in range(mm[W["wNumBalls"]])}


class Server:
    def __init__(self, pool):
        self.pool = self.max = pool
        self.hits = []

    def hit(self, d):
        self.pool = max(0, self.pool - d)
        self.hits.append(d)


class RaidClient:
    def __init__(self, pb, species, level, moves, local_max, server, *, clamp=True):
        self.pb, self.mm = pb, Mem(pb.memory)
        self.species, self.level, self.moves = species, level, moves
        self.local_max, self.server, self.clamp = local_max, server, clamp
        self.phase = "starting"
        self.frame = 0
        self.last_hp = None
        self.events = []
        self.menu_open_at = None
        self.decisions = []
        self.input_queue = []
        self.on_menu = None
        self.hp_zero_at = None
        self.idle_b = True
        self.auto_move = 0
        self.last_input = self.last_hook = 0
        self.real_moves = None
        self.real_pp = None
        self.cur_moves = list(moves)
        for name, fn in (("BattleMenu", self._menu), ("BattleTurn.loop", self._turn_start),
                         ("EnemyTriesToFlee", self._locked_in), ("PokeBallEffect", self._ball),
                         ("HandleEnemyMonFaint", self._faint_check), ("MoveSelectionScreen", self._move_menu),
                         ("BackUpBGMap2", self._after_init_enemy)):
            b, a = C(name)
            pb.hook_register(b, a, fn, name)

    def log(self, what):
        self.events.append((self.frame, what))
        self.last_hook = self.frame

    def _after_init_enemy(self, _):
        mm = self.mm
        if self.phase == "starting" and mm[W["wBattleMode"]] == 1 and mm[W["wEnemyMonSpecies"]] == self.species:
            self._override()

    def _menu(self, _):
        self.menu_open_at = self.frame
        self.log("menu")
        if self.phase == "starting":
            self.phase = "fight"
        if self.on_menu:
            self.on_menu(self)

    def _move_menu(self, _):
        self.log("move menu")
        if self.auto_move is not None and not self.input_queue:
            cur = self.mm[W["wCurMoveNum"]]
            d = self.auto_move - cur
            self.press_later(25, *(["down"] * d if d > 0 else ["up"] * -d), "a")

    def _locked_in(self, _):
        self.log("locked in")
        if self.menu_open_at is not None:
            self.decisions.append((self.menu_open_at, self.frame))
            self.menu_open_at = None

    def _turn_start(self, _):
        if self.phase == "fight" and self.server.pool > 0:
            seg = (self.server.pool - 1) % self.local_max + 1
            self.mm.w16(W["wEnemyMonHP"], seg)
            self.last_hp = seg
        if self.phase in ("fight", "catch", "flee"):
            # AIChooseMove runs right after this: refill the raid moves' PP (the game spends wWildMonPP)
            self.set_moves(self.cur_moves)
        self.log("turn start pool=%d" % self.server.pool)

    def _ball(self, _):
        if self.phase != "catch":
            self.pb.register_file.PC = C("IsntTheTimeMessage")[1]
            self.log("ball blocked")
        else:
            # the turn's enemy move (Splash) is already chosen: give the mon its real moves back before the game
            # copies wEnemyMon into the party
            # (the game copies wWildMonMoves/PP into the caught mon; we never change wWildMonMoves)
            mm = self.mm
            for i in range(4):
                mm[W["wWildMonPP"] + i] = self.real_pp[i]
            self.log("ball allowed")

    def _faint_check(self, _):
        self.log("faint check")

    def tick(self, n=1):
        for _ in range(n):
            while self.input_queue and self.input_queue[0][0] <= self.frame:
                _, btn = self.input_queue.pop(0)
                self.pb.button(btn, 3)
                self.last_input = self.frame
            if self.idle_b and not self.input_queue and self.frame - max(self.last_input, self.last_hook) > 90:
                self.pb.button("b", 3)
                self.last_input = self.frame
            self.pb.tick()
            self.frame += 1
            self._after_frame()

    def press_later(self, delay, *buttons, gap=40):
        t = self.frame + delay
        for b in buttons:
            self.input_queue.append((t, b))
            t += gap
        self.input_queue.sort()

    def _after_frame(self):
        mm = self.mm
        if self.phase not in ("fight", "catch") or mm[W["wBattleMode"]] != 1:
            return
        hp = mm.r16(W["wEnemyMonHP"])
        if self.last_hp is not None and hp < self.last_hp and self.phase == "fight":
            d = self.last_hp - hp
            self.server.hit(d)
            self.log("hit %d -> pool %d" % (d, self.server.pool))
        if hp == 0 and self.hp_zero_at is None:
            self.hp_zero_at = self.frame
        if hp == 0 and self.clamp:
            mm.w16(W["wEnemyMonHP"], 1)
            hp = 1
            self.log("clamped to 1")
        self.last_hp = hp
        if self.phase == "fight" and self.server.pool == 0 and self.clamp:
            self.enter_catch()

    def _override(self):
        mm = self.mm
        self.real_moves = [mm[W["wWildMonMoves"] + i] for i in range(4)]
        self.real_pp = [mm[W["wWildMonPP"] + i] for i in range(4)]
        self.set_moves(self.moves)
        mm.w16(W["wEnemyMonMaxHP"], self.local_max)
        seg = (self.server.pool - 1) % self.local_max + 1
        mm.w16(W["wEnemyMonHP"], seg)
        mm.w16(W["wEnemyMonDefense"], 5)
        mm.w16(W["wEnemyMonSpclDef"], 5)
        self.last_hp = seg
        self.log("override")

    def enter_catch(self):
        mm = self.mm
        self.phase = "catch"
        mm.w16(W["wEnemyMonHP"], 1)
        self.last_hp = 1
        mm[W["wEnemyMonStatus"]] = SLP
        mm[W["wEnemyMonCatchRate"]] = 255
        self.set_moves([SPLASH])
        self.log("catch phase")

    def enter_flee(self):
        self.phase = "flee"
        self.set_moves([TELEPORT])
        self.log("flee")

    def set_moves(self, moves):
        """Battle moves: wEnemyMonMoves/PP + the PP the game really spends for a wild mon (wWildMonPP, indexed by
        the same slot). wWildMonMoves keeps the mon's real moves: that's what a catch copies into the party."""
        mm = self.mm
        self.cur_moves = list(moves)
        for i in range(4):
            mv = moves[i] if i < len(moves) else 0
            pp = RD.move_pp(mv) if mv else 0
            mm[W["wEnemyMonMoves"] + i] = mv
            mm[W["wEnemyMonPP"] + i] = pp
            mm[W["wWildMonPP"] + i] = pp


def emu(state="crystal_sl_route29.state"):
    pb = PyBoy(ROM, window="null", sound_emulated=False, log_level="ERROR")
    pb.set_emulation_speed(0)
    with open(os.path.join(STATES, state), "rb") as f:
        pb.load_state(f)
    return pb, crystal.CrystalMemory(pb.memory, RD)


def start_raid_battle(rc, cm, species, level, max_frames=2500):
    mm = rc.mm
    for _ in range(120):
        if cm.is_safe():
            break
        rc.tick()
    assert cm.is_safe(), "not safe"
    mm[W["wTempWildMonSpecies"]] = species
    mm[W["wCurPartyLevel"]] = level
    mm[W["wBattleScriptFlags"]] = 0
    mm[W["wBattleType"]] = 0                       # BATTLETYPE_NORMAL
    bank, addr = WILD_BATTLE_SCRIPT_STARTBATTLE
    mm[W["wMapReentryScriptBank"]] = bank
    mm[W["wMapReentryScriptAddress"]] = addr & 0xFF
    mm[W["wMapReentryScriptAddress"] + 1] = addr >> 8
    mm[W["wMapReentryScriptQueueFlag"]] = 1
    t0 = rc.frame
    battle_at = None
    for _ in range(max_frames):
        rc.tick()
        if battle_at is None and mm[W["wBattleMode"]]:
            battle_at = rc.frame - t0
        if mm[W["wBattleMode"]] and (rc.frame - t0) % 60 == 59 and rc.phase == "starting":
            rc.pb.button("a", 3)
        if rc.phase == "fight":
            break
    return battle_at, rc.frame - t0


def shot(pb, name):
    try:
        pb.screen.image.save(os.path.join(OUT, name))
    except Exception as e:  # noqa: BLE001
        info("screenshot failed: %s" % e)


def balls_pocket(rc):
    """Test driver: open the PACK on the Poké Balls pocket (the game remembers the last pocket)."""
    rc.mm[W["wLastPocket"]] = BALL_POCKET
    rc.mm[W["wCurPocket"]] = BALL_POCKET
    rc.mm[C("wBallsPocketCursor")[1]] = 0          # cursor on the first ball (the game remembers it too)
    rc.mm[C("wBallsPocketScrollPosition")[1]] = 0


def party_species(cm):
    return [(x.species, x.level) for x in cm.party()]


def scenario_fight_and_catch():
    print("== Crystal: raid battle, pool, timer, ball block, catch ==")
    pb, cm = emu()
    mm = Mem(pb.memory)
    srv = Server(100)
    rc = RaidClient(pb, CELEBI, 30, [SPLASH], 40, srv)
    b0 = balls(mm)
    n0 = mm[crystal.PARTY_COUNT]
    battle_at, fight_at = start_raid_battle(rc, cm, CELEBI, 30)
    check("wild battle starts from the overworld via a memory script -> ROM WildBattleScript+1",
          battle_at is not None, "wBattleMode after %s frames, menu after %d" % (battle_at, fight_at))
    check("enemy is CELEBI Lv30 with our moves/HP before the first menu",
          mm[W["wEnemyMonSpecies"]] == CELEBI and mm[W["wEnemyMonLevel"]] == 30
          and mm[W["wEnemyMonMoves"]] == SPLASH and mm.r16(W["wEnemyMonHP"]) == 20,
          "hp %d/%d moves %s real %s" % (mm.r16(W["wEnemyMonHP"]), mm.r16(W["wEnemyMonMaxHP"]),
                                         [mm[W["wEnemyMonMoves"] + i] for i in range(4)], rc.real_moves))
    shot(pb, "crystal_raid_start.png")
    plan = {"script": [("ball", ("down", "a", "down", "a", "a"))]}   # PACK, POKé BALL, USE

    def on_menu(rc_):
        if rc_.phase == "catch":
            plan["threw"] = True
            balls_pocket(rc_)
            rc_.press_later(40, "down", "a", "down", "a", "a")   # PACK -> balls -> 2nd = POKé BALL -> USE
        elif plan["script"]:
            balls_pocket(rc_)
            _, keys = plan["script"].pop(0)
            rc_.press_later(40, *keys)
        else:
            rc_.press_later(30, *AUTO_FIGHT)

    rc.on_menu = on_menu
    on_menu(rc)
    for _ in range(40000):
        rc.tick()
        if rc.frame % 600 == 0:
            info("f%d phase %s pool %d | %s" % (rc.frame, rc.phase, srv.pool,
                                               " | ".join(x for x in screen_lines(pb)[12:18] if x.strip("? "))))
        if mm[W["wBattleMode"]] == 0 and rc.phase != "starting":
            break
        if plan.get("shot") is None and any(e[1] == "ball blocked" for e in rc.events):
            plan["shot"] = rc.frame
        if plan.get("shot") and rc.frame == plan["shot"] + 100:
            shot(pb, "crystal_raid_ball_blocked.png")
            info("after blocked ball: " + " | ".join(x for x in screen_lines(pb)[12:18] if x.strip("? ")))
    ev = [e[1] for e in rc.events]
    cat = [i for i, e in enumerate(ev) if e == "catch phase"]
    info("events from the catch phase on: %s" % (rc.events[cat[0]:cat[0] + 12] if cat else "none"))
    check("Poké Ball during the fight is refused (IsntTheTimeMessage)", "ball blocked" in ev)
    check("damage reported as HP drops adds up to the pool", sum(srv.hits) == 100 and srv.pool == 0,
          "hits %s" % srv.hits)
    check("boss never fainted locally (HandleEnemyMonFaint never reached)", "faint check" not in ev)
    check("pool ran over several local segments", ev.count("clamped to 1") >= 2, "clamps %d" % ev.count("clamped to 1"))
    info("menu->locked-in frames per turn: %s" % [b - a for a, b in rc.decisions])
    check("timer hooks seen every turn", len(rc.decisions) >= 3)
    for _ in range(600):
        rc.tick()
    party = cm.party()
    check("catch phase: one Poké Ball caught CELEBI", mm[crystal.PARTY_COUNT] == n0 + 1 and party[-1].species == CELEBI,
          "party %s" % party_species(cm))
    if party and party[-1].species == CELEBI:
        c = party[-1]
        info("caught CELEBI: level %d moves %s hp %d/%d (real moves at start %s)" % (
            c.level, c.moves, c.hp, c.max_hp, rc.real_moves))
        check("caught CELEBI got its real moves back (restored at the throw), player's OT",
              list(c.moves) == list(rc.real_moves) and c.ot_id == mm.r16(crystal.PLAYER_ID))
        check("Pokédex caught bit for CELEBI", mm[crystal.DEX_CAUGHT + 250 // 8] >> (250 % 8) & 1)
    b1 = balls(mm)
    check("balls: blocked throw kept, catch used one", b1.get(POKE_BALL, 0) == b0.get(POKE_BALL, 0) - 1
          and b1.get(MASTER_BALL) == b0.get(MASTER_BALL), "before %s after %s" % (b0, b1))
    shot(pb, "crystal_raid_after_catch.png")
    pb.stop(save=False)


def scenario_master_ball_and_flee():
    print("== Crystal: Master Ball blocked, then the boss flees (Teleport) ==")
    pb, cm = emu()
    mm = Mem(pb.memory)
    srv = Server(10_000)
    rc = RaidClient(pb, CELEBI, 30, [SPLASH], 40, srv)
    start_raid_battle(rc, cm, CELEBI, 30)
    b0 = balls(mm)
    st = {"n": 0}

    def on_menu(rc_):
        st["n"] += 1
        if st["n"] == 1:
            balls_pocket(rc_)
            rc_.press_later(40, "down", "a", "a", "a")    # PACK -> balls -> 1st = MASTER BALL -> USE
        else:
            rc_.enter_flee()
            rc_.press_later(30, *AUTO_FIGHT)

    rc.on_menu = on_menu
    on_menu(rc)
    for _ in range(8000):
        rc.tick()
        if mm[W["wBattleMode"]] == 0:
            break
    ev = [e[1] for e in rc.events]
    info("events: %s" % ev[:20])
    check("ball refused during the fight, bag unchanged", "ball blocked" in ev and balls(mm) == b0,
          "%s -> %s" % (b0, balls(mm)))
    check("boss with only Teleport ends the battle (no faint, party unchanged)",
          mm[W["wBattleMode"]] == 0 and "faint check" not in ev and mm[crystal.PARTY_COUNT] == 3)
    ok = False
    for _ in range(900):
        rc.tick()
        if cm.is_safe():
            ok = True
            break
    check("back in the overworld afterwards (is_safe)", ok)
    pb.stop(save=False)


def scenario_faint_margin():
    print("== Crystal: frames between enemy HP 0 and the faint routine (no clamp) ==")
    pb, cm = emu()
    srv = Server(1)
    rc = RaidClient(pb, CELEBI, 30, [SPLASH], 40, srv, clamp=False)
    start_raid_battle(rc, cm, CELEBI, 30)
    rc.on_menu = lambda r: r.press_later(30, *AUTO_FIGHT)
    rc.press_later(30, *AUTO_FIGHT)
    faint_at = None
    for _ in range(4000):
        rc.tick()
        f = [fr for fr, e in rc.events if e == "faint check"]
        if f:
            faint_at = f[0]
            break
    margin = (faint_at - rc.hp_zero_at) if faint_at is not None and rc.hp_zero_at is not None else None
    check("HP reaches 0 well before HandleEnemyMonFaint (a per-frame watchdog has time)",
          margin is not None and margin >= 10, "margin %s frames" % margin)
    pb.stop(save=False)


def scenario_blackout():
    print("== Crystal: the player blacks out against the boss ==")
    pb, cm = emu()
    mm = Mem(pb.memory)
    srv = Server(10_000)
    rc = RaidClient(pb, CELEBI, 30, [PSYCHIC_M], 40, srv)
    for i in range(3):     # lead at 1 HP, the others fainted already
        mm.w16(crystal.PARTY_MONS + 48 * i + 34, 1 if i == 0 else 0)
    start_raid_battle(rc, cm, CELEBI, 30)
    mm.w16(crystal.BATTLE_MON_HP, 1)
    rc.auto_move = 0
    rc.on_menu = lambda r: r.press_later(30, *AUTO_FIGHT)
    rc.press_later(30, *AUTO_FIGHT)
    lost_at = None
    for _ in range(12000):
        rc.tick()
        if lost_at is None and mm[W["wBattleMode"]] == 0 and rc.phase != "starting":
            lost_at = rc.frame
        if lost_at and cm.is_safe():
            break
    p = cm.party()
    check("blackout is the game's own (reloadmapafterbattle -> whiteout script), party healed at a Pokémon Center",
          lost_at is not None and all(x.hp == x.max_hp for x in p),
          "map %d:%d hp %s" % (mm[crystal.MAP_GROUP], mm[crystal.MAP_NUMBER], [(x.hp, x.max_hp) for x in p]))
    pb.stop(save=False)


if __name__ == "__main__":
    scenario_fight_and_catch()
    scenario_master_ball_and_flee()
    scenario_faint_margin()
    scenario_blackout()
    raise SystemExit(done())
