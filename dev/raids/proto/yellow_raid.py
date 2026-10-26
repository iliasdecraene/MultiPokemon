"""Raid prototype on the real Pokémon Yellow ROM (PyBoy, headless). Throwaway - proves the risky parts only.

Scenarios (each from dev/states/soullink_route1_grass.state: Pikachu Lv5, 5 Poké Balls + 3 Master Balls, Route 1):
  1. start a wild battle on demand (any species/level) from the overworld; override moves/stats/HP/catch rate
     before the HUD is drawn
  2. shared HP pool: local enemy HP = the current "segment" of a server pool; per-frame watchdog keeps the boss
     at >= 1 HP until the pool is empty (it never faints locally); damage reported = observed HP drops
  3. 10-second move timer stand-in: detect "battle menu waiting" (hook DisplayBattleMenu) and the choice being
     locked in (hook MainInBattleLoop.selectEnemyMove); auto-pick by injected buttons
  4. balls blocked during the fight (hook ItemUseBall -> ItemUseNotTime: no turn used, ball kept, Master Ball too)
  5. catch phase: pool 0 -> HP 1 + sleep + catch rate 255 + Splash only -> one Poké Ball catches; Pokédex set
  6. flee: moves = [Teleport] + level 100 -> "ran away" ends the battle (raid timer over / throws used up)
  7. faint-check margin: frames between the enemy HP reaching 0 and the game's faint check (no clamp)
  8. player blackout against the boss is the game's own (wIsInBattle 0xFF -> Pokémon Center)
Run: python dev/raids/proto/yellow_raid.py   (prints PASS/FAIL, screenshots to /tmp/yl-raid-proto)
"""
import os

from common import STATES, OUT, check, info, done, sym
from pyboy import PyBoy
from yellowlink import game, text

ROM = os.path.join(STATES, "yellow.gb")
RD = game.RomData.from_file(ROM)


def Y(name):
    return sym("yellow", name)


A = {k: Y(k)[1] for k in (
    "wIsInBattle", "wCurOpponent", "wCurEnemyLevel", "wEnemyMonSpecies", "wEnemyMonHP", "wEnemyMonStatus",
    "wEnemyMonCatchRate", "wEnemyMonMoves", "wEnemyMonDVs", "wEnemyMonLevel", "wEnemyMonMaxHP",
    "wEnemyMonDefense", "wEnemyMonSpecial", "wEnemyMonPP", "wBattleMonHP", "wPartyCount", "wBattleResult",
    "wNumBagItems", "wBagItems", "wEnemyMonActualCatchRate", "wTileMap", "wCurMap", "wBattleType", "wActionResultOrTookBattleTurn")}
MEW, SPLASH, TELEPORT, PSYCHIC_M, SLASH = 0x15, 0x96, 0x64, 0x5E, 0xA3
POKE_BALL, MASTER_BALL = 4, 1
SLP = 7
# what the 10-s timeout injects: cursor to FIGHT (the menu remembers ITEM/RUN), A, first move (up x3), A
AUTO_FIGHT = ("up", "left", "a")       # + the move menu hook moves the cursor to auto_move and presses A


def screen_lines(m):
    tm = bytes(m[A["wTileMap"] + i] for i in range(360))
    return [text.decode(tm[r * 20:(r + 1) * 20] + b"\x50").strip() for r in range(18)]


def bag(m):
    n = m[A["wNumBagItems"]]
    return {m[A["wBagItems"] + 2 * i]: m[A["wBagItems"] + 2 * i + 1] for i in range(n)}


def r16(m, a):
    return m[a] << 8 | m[a + 1]


def w16(m, a, v):
    m[a], m[a + 1] = v >> 8, v & 0xFF


class Server:
    """Stand-in for the server's pool: pool HP in local HP units (pool = B x the boss's local max HP)."""

    def __init__(self, pool):
        self.pool = self.max = pool
        self.hits = []

    def hit(self, d):
        self.pool = max(0, self.pool - d)
        self.hits.append(d)


class RaidClient:
    """What the client would do during a raid battle (prototype of the real logic)."""

    def __init__(self, pb, species, level, moves, local_max, server, *, clamp=True):
        self.pb, self.m = pb, pb.memory
        self.species, self.level, self.moves = species, level, moves
        self.local_max, self.server, self.clamp = local_max, server, clamp
        self.phase = "starting"          # starting -> fight -> catch -> flee / over
        self.overridden = False
        self.frame = 0
        self.last_hp = None
        self.events = []                 # (frame, what)
        self.menu_open_at = None         # DisplayBattleMenu frame (timer start)
        self.decisions = []              # (menu frame, locked-in frame)
        self.input_queue = []            # (frame, button)
        self.on_menu = None              # callback(self) when the battle menu waits for input
        self.hp_zero_at = None
        self.idle_b = True
        self.auto_move = 0                 # the move the timeout picks (slot 0 here)
        self.last_input = self.last_hook = 0
        for name, fn in (("DisplayBattleMenu", self._menu), ("MainInBattleLoop", self._turn_start),
                         ("MainInBattleLoop.selectEnemyMove", self._locked_in), ("ItemUseBall", self._ball),
                         ("HandleEnemyMonFainted", self._faint_check), ("MoveSelectionMenu", self._move_menu),
                         ("_InitBattleCommon", self._init_common)):
            b, a = Y(name)
            pb.hook_register(b, a, fn, name)

    def log(self, what):
        self.events.append((self.frame, what))
        self.last_hook = self.frame

    # --- hooks -----------------------------------------------------------------------------------------------
    def _menu(self, _):
        self.menu_open_at = self.frame
        self.log("menu")
        if self.phase == "starting":
            self.phase = "fight"
        if self.on_menu:
            self.on_menu(self)

    def _init_common(self, _):
        """After LoadEnemyMonData + the battle transition, before the enemy HUD is drawn: our overrides."""
        m = self.m
        if self.phase == "starting" and m[A["wIsInBattle"]] == 1 and m[A["wEnemyMonSpecies"]] == self.species:
            self._override()

    def _move_menu(self, _):
        self.log("move menu")
        if self.auto_move is not None and not self.input_queue:
            cur = self.m[Y("wPlayerMoveListIndex")[1]]      # the menu opens on the last move used
            d = self.auto_move - cur
            self.press_later(25, *(["down"] * d if d > 0 else ["up"] * -d), "a")

    def _locked_in(self, _):
        self.log("locked in")
        if self.menu_open_at is not None:
            self.decisions.append((self.menu_open_at, self.frame))
            self.menu_open_at = None

    def _turn_start(self, _):
        """Turn start = safe sync point: local HP := current segment of the pool (before the game's HP-0 check)."""
        if self.phase == "fight" and self.server.pool > 0:
            seg = (self.server.pool - 1) % self.local_max + 1
            w16(self.m, A["wEnemyMonHP"], seg)
            self.last_hp = seg
        self.log("turn start pool=%d" % self.server.pool)

    def _ball(self, _):
        if self.phase != "catch":
            # redirect to the game's own "This isn't the time to use that!" (same ROM bank 3): no turn, no ball used
            self.pb.register_file.PC = Y("ItemUseNotTime")[1]
            self.log("ball blocked")
        else:
            self.log("ball allowed")

    def _faint_check(self, _):
        self.log("faint check")

    # --- per frame -------------------------------------------------------------------------------------------
    def tick(self, n=1):
        for _ in range(n):
            while self.input_queue and self.input_queue[0][0] <= self.frame:
                _, btn = self.input_queue.pop(0)
                self.pb.button(btn, 3)
                self.last_input = self.frame
            # test driver only: a player who keeps dismissing text boxes (B also answers NO to "nickname?")
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
        m = self.m
        if self.phase not in ("fight", "catch") or m[A["wIsInBattle"]] != 1:
            return
        hp = r16(m, A["wEnemyMonHP"])
        if self.last_hp is not None and hp < self.last_hp:
            d = self.last_hp - hp
            if self.phase == "fight":
                self.server.hit(d)
                self.log("hit %d -> pool %d" % (d, self.server.pool))
        if hp == 0 and self.hp_zero_at is None:
            self.hp_zero_at = self.frame
        if hp == 0 and self.clamp:
            w16(m, A["wEnemyMonHP"], 1)          # never faints locally
            hp = 1
            self.log("clamped to 1")
        self.last_hp = hp
        if self.phase == "fight" and self.server.pool == 0 and self.clamp:
            self.enter_catch()

    def _override(self):
        m = self.m
        for i in range(4):
            mv = self.moves[i] if i < len(self.moves) else 0
            m[A["wEnemyMonMoves"] + i] = mv
            m[A["wEnemyMonPP"] + i] = RD.move_pp(mv) if mv else 0
        w16(m, A["wEnemyMonMaxHP"], self.local_max)
        seg = (self.server.pool - 1) % self.local_max + 1
        w16(m, A["wEnemyMonHP"], seg)
        w16(m, A["wEnemyMonDefense"], 5)          # test only: let a Lv5 Pikachu do visible damage
        w16(m, A["wEnemyMonSpecial"], 5)
        m[A["wEnemyMonCatchRate"]] = 3
        self.last_hp = seg
        self.overridden = True
        self.log("override")

    def enter_catch(self):
        m = self.m
        self.phase = "catch"
        w16(m, A["wEnemyMonHP"], 1)
        self.last_hp = 1
        m[A["wEnemyMonStatus"]] = SLP
        m[A["wEnemyMonCatchRate"]] = 255
        m[A["wEnemyMonActualCatchRate"]] = 255     # the one ItemUseBall really reads (not the battle struct's)
        self.set_moves([SPLASH])
        self.log("catch phase")

    def enter_flee(self):
        self.phase = "flee"
        self.set_moves([TELEPORT])
        self.m[A["wCurEnemyLevel"]] = 100          # enemy Teleport always works when its level >= the player's
        self.log("flee")

    def set_moves(self, moves):
        m = self.m
        for i in range(4):
            mv = moves[i] if i < len(moves) else 0
            m[A["wEnemyMonMoves"] + i] = mv
            m[A["wEnemyMonPP"] + i] = RD.move_pp(mv) if mv else 0


def emu(state="soullink_route1_grass.state"):
    pb = PyBoy(ROM, window="null", sound_emulated=False, log_level="ERROR")
    pb.set_emulation_speed(0)
    with open(os.path.join(STATES, state), "rb") as f:
        pb.load_state(f)
    return pb, game.GameMemory(pb.memory, RD)


def start_raid_battle(rc, gm, species, level, max_frames=1500):
    """Trigger: the overworld loop starts a wild battle when wCurOpponent != 0 (< 200 = species)."""
    m = rc.m
    for _ in range(60):
        if gm.is_safe():
            break
        rc.tick()
    assert gm.is_safe()
    m[A["wCurOpponent"]] = species
    m[A["wCurEnemyLevel"]] = level
    t0 = rc.frame
    battle_at = None
    for _ in range(max_frames):
        rc.tick()
        if m[A["wIsInBattle"]] and (rc.frame - t0) % 60 == 59:
            rc.pb.button("a", 3)                 # "Wild MEW appeared!" waits for a button, like any battle
        if battle_at is None and m[A["wIsInBattle"]]:
            battle_at = rc.frame - t0
        if rc.phase == "fight":
            break
    return battle_at, rc.frame - t0


def shot(pb, name):
    try:
        pb.screen.image.save(os.path.join(OUT, name))
    except Exception as e:  # noqa: BLE001
        info("screenshot failed: %s" % e)


# --- scenario 1-5 -------------------------------------------------------------------------------------------
def scenario_fight_and_catch():
    print("== Yellow: raid battle, pool, timer, ball block, catch ==")
    pb, gm = emu()
    m = pb.memory
    srv = Server(100)                       # pool = 2.5 x local max (40)
    rc = RaidClient(pb, MEW, 30, [SPLASH], 40, srv)
    balls0 = bag(m)
    party0 = m[A["wPartyCount"]]
    battle_at, fight_at = start_raid_battle(rc, gm, MEW, 30)
    check("wild battle starts from the overworld by wCurOpponent=MEW", battle_at is not None and battle_at < 5,
          "wIsInBattle after %s frames, menu after %d" % (battle_at, fight_at))
    check("enemy is MEW Lv30 with our moves/HP before the first menu",
          m[A["wEnemyMonSpecies"]] == MEW and m[A["wEnemyMonLevel"]] == 30 and m[A["wEnemyMonMoves"]] == SPLASH
          and r16(m, A["wEnemyMonMaxHP"]) == 40 and r16(m, A["wEnemyMonHP"]) == 20,
          "hp %d/%d moves %s" % (r16(m, A["wEnemyMonHP"]), r16(m, A["wEnemyMonMaxHP"]),
                                 [m[A["wEnemyMonMoves"] + i] for i in range(4)]))
    shot(pb, "yellow_raid_start.png")
    info("screen: " + " | ".join(x for x in screen_lines(m)[12:18] if x))

    # turn 1: try a Poké Ball during the fight (ITEM = DOWN, A; first bag item = POKé BALL; A), back out with B;
    # later turns: FIGHT -> first move (what the 10-s timeout would inject); catch phase: ITEM -> POKé BALL
    plan = {"script": [("ball", ("down", "a", "a"))]}

    def on_menu(rc_):
        if rc_.phase == "catch":
            plan["threw"] = True
            rc_.press_later(40, "down", "a", "a")
        elif plan["script"]:
            _, keys = plan["script"].pop(0)
            rc_.press_later(40, *keys, gap=70)
        else:
            rc_.press_later(30, *AUTO_FIGHT)

    rc.on_menu = on_menu
    # first menu already fired before on_menu was set: drive it now
    on_menu(rc)
    for _ in range(40000):
        rc.tick()
        if rc.phase == "catch" and m[A["wIsInBattle"]] == 0:
            break
        if m[A["wIsInBattle"]] == 0 and rc.phase != "starting":
            break
        if rc.frame % 300 == 0:
            info("f%d phase %s pool %d | %s" % (rc.frame, rc.phase, srv.pool, " | ".join(x for x in screen_lines(m)[12:18] if x.strip("? "))))
        if ("ball blocked" in [e[1] for e in rc.events]) and "shot_block" not in plan:
            plan["shot_block"] = rc.frame
        if "shot_block" in plan and rc.frame == plan["shot_block"] + 120:
            shot(pb, "yellow_raid_ball_blocked.png")
            info("after blocked ball: " + " | ".join(x for x in screen_lines(m)[12:18] if x))
    ev = [e[1] for e in rc.events]
    cat = [i for i, e in enumerate(ev) if e == "catch phase"]
    info("events from the catch phase on: %s" % rc.events[cat[0]:cat[0] + 20] if cat else "no catch phase")
    blocked = [e for e in rc.events if e[1] == "ball blocked"]
    check("Poké Ball during the fight is refused (ItemUseNotTime)", len(blocked) >= 1)
    hits = srv.hits
    check("damage reported as HP drops add up to the pool", sum(hits) == 100 and srv.pool == 0,
          "hits %s" % hits)
    check("boss never fainted locally while the pool had HP (no faint routine reached)",
          "faint check" not in ev, "events %s" % [e for e in ev if "faint" in e])
    clamps = ev.count("clamped to 1")
    info("segment ends clamped: %d; turns: %d" % (clamps, ev.count("menu")))
    check("pool ran over several local segments (multi-bar boss)", clamps >= 2)
    dec = rc.decisions
    info("menu->locked-in frames per turn: %s" % [b - a for a, b in dec])
    check("timer hooks: menu shown and choice locked in are seen every turn", len(dec) >= 3)
    check("catch phase reached and one Poké Ball caught MEW", m[A["wPartyCount"]] == party0 + 1
          and gm.party()[-1].species == MEW, "party %s" % [(RD.species_name(x.species), x.level) for x in gm.party()])
    caught = gm.party()[-1]
    check("caught MEW: player's OT, level 30, Pokédex owned bit set (moves = its natural level-30 set: the game "
          "rebuilds the caught mon from species/level/DVs, not from our battle copy)",
          caught.ot_id == r16(m, game.PLAYER_ID) and caught.level == 30 and m[game.DEX_OWNED + 150 // 8] >> (150 % 8) & 1,
          "moves %s" % caught.moves)
    b1 = bag(m)
    check("balls: the blocked throw kept its ball, the catch used one",
          b1.get(POKE_BALL, 0) == balls0.get(POKE_BALL, 0) - 1 and b1.get(MASTER_BALL) == balls0.get(MASTER_BALL),
          "before %s after %s" % (balls0, b1))
    shot(pb, "yellow_raid_after_catch.png")
    pb.stop(save=False)


def scenario_master_ball_blocked_and_flee():
    print("== Yellow: Master Ball blocked, then the boss flees (Teleport) ==")
    pb, gm = emu()
    m = pb.memory
    srv = Server(10_000)
    rc = RaidClient(pb, MEW, 30, [SPLASH], 40, srv)
    start_raid_battle(rc, gm, MEW, 30)
    balls0 = bag(m)
    st = {"n": 0}

    def on_menu(rc_):
        st["n"] += 1
        if st["n"] == 1:
            rc_.press_later(40, "down", "a", "down", "a", gap=40)   # ITEM -> MASTER BALL (idle B backs out)
        else:
            rc_.enter_flee()
            rc_.press_later(30, *AUTO_FIGHT)                     # player attacks, then the boss teleports away

    rc.on_menu = on_menu
    on_menu(rc)
    for _ in range(6000):
        rc.tick()
        if m[A["wIsInBattle"]] == 0:
            break
    ev = [e[1] for e in rc.events]
    check("Master Ball refused during the fight, kept in the bag",
          "ball blocked" in ev and bag(m).get(MASTER_BALL) == balls0.get(MASTER_BALL), str(bag(m)))
    check("boss with only Teleport ends the battle (no faint, no catch, party unchanged)",
          m[A["wIsInBattle"]] == 0 and "faint check" not in ev and m[A["wPartyCount"]] == 1)
    for _ in range(200):
        rc.tick()
    check("back in the overworld afterwards (is_safe)", any(gm.is_safe() or rc.tick() for _ in range(300)))
    pb.stop(save=False)


def scenario_faint_margin():
    print("== Yellow: frames between enemy HP 0 and the game's faint check (no clamp) ==")
    pb, gm = emu()
    m = pb.memory
    srv = Server(1)
    rc = RaidClient(pb, MEW, 30, [SPLASH], 40, srv, clamp=False)
    start_raid_battle(rc, gm, MEW, 30)
    rc.phase = "fight"
    rc.on_menu = lambda r: r.press_later(30, *AUTO_FIGHT)
    rc.press_later(30, *AUTO_FIGHT)
    faint_at = None
    for _ in range(3000):
        rc.tick()
        if faint_at is None and any(e[1] == "faint check" for e in rc.events):
            faint_at = next(f for f, e in rc.events if e == "faint check")
            break
    margin = (faint_at - rc.hp_zero_at) if faint_at and rc.hp_zero_at else None
    check("HP reaches 0 well before the faint check (a per-frame watchdog has time)",
          margin is not None and margin >= 10, "margin %s frames" % margin)
    pb.stop(save=False)


def scenario_blackout():
    print("== Yellow: the player blacks out against the boss ==")
    pb, gm = emu()
    m = pb.memory
    srv = Server(10_000)
    rc = RaidClient(pb, MEW, 30, [PSYCHIC_M], 40, srv)
    start_raid_battle(rc, gm, MEW, 30)
    rc.on_menu = lambda r: r.press_later(30, *AUTO_FIGHT)
    w16(m, A["wBattleMonHP"], 1)
    w16(m, game.PARTY_MONS + 1, 1)
    rc.press_later(30, *AUTO_FIGHT)
    lost = False
    for _ in range(4000):
        rc.tick()
        if m[A["wIsInBattle"]] == 0xFF:
            lost = True
        if lost and m[A["wIsInBattle"]] == 0 and gm.is_safe():
            break
        if not lost and rc.frame % 60 == 0 and m[A["wIsInBattle"]] == 1:
            pass
    check("blackout is the game's own (wIsInBattle 0xFF), back at a Pokémon Center healed",
          lost and gm.party()[0].hp == gm.party()[0].max_hp, "map %02X" % m[A["wCurMap"]])
    pb.stop(save=False)


if __name__ == "__main__":
    scenario_fight_and_catch()
    scenario_master_ball_blocked_and_flee()
    scenario_faint_margin()
    scenario_blackout()
    raise SystemExit(done())
