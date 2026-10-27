"""Yellow + Crystal raid checks for dev/raids/verify.py (real ROMs, production raid_gb.py)."""
import os

from pyboy import PyBoy

from yellowlink import crystal, game, raid_data, raid_gb

MEW, CELEBI = 151, 251
SPLASH, PSYCHIC_M, TACKLE = 150, 94, 33
POKE_BALL_Y, MASTER_BALL = 4, 1
POKE_BALL_C = 5


class GB:
    """A headless game + a scripted 'player' + a stand-in for the server's pool."""

    def __init__(self, game_id, state):
        self.game_id = game_id
        rom = os.path.join(STATES, "yellow.gb" if game_id == "yellow" else "crystal.gbc")
        self.mod = game if game_id == "yellow" else crystal
        self.rd = self.mod.RomData.from_file(rom)
        self.pb = PyBoy(rom, window="null", sound_emulated=False, log_level="ERROR")
        self.pb.set_emulation_speed(0)
        with open(os.path.join(STATES, state), "rb") as f:
            self.pb.load_state(f)
        self.gm = (crystal.CrystalMemory if game_id == "crystal" else game.GameMemory)(self.pb.memory, self.rd)
        self.m = raid_gb._Mem(self.pb.memory, game_id == "crystal")
        self.S = raid_gb.spec_for(game_id)
        self.frame = 0
        self.queue = []
        self.rb = None
        self.events = []
        self.last_input = 0
        self.last_event = 0
        self.idle_b = True
        self.pool = 0
        self.pool0 = 0
        self.lag = 0              # frames until a hit reaches the "server"
        self.inflight = []        # (frame due, mb)
        self.on_event = None

    def stop(self):
        try:
            self.pb.stop(save=False)
        except Exception:  # noqa: BLE001
            pass

    def press_later(self, delay, *buttons, gap=40):
        t = self.frame + delay
        for b in buttons:
            self.queue.append((t, b))
            t += gap
        self.queue.sort()

    def tick(self, n=1):
        for _ in range(n):
            while self.queue and self.queue[0][0] <= self.frame:
                _, btn = self.queue.pop(0)
                self.pb.button(btn, 3)
                self.last_input = self.frame
            rb = self.rb
            # a real player answers text boxes; B is only unsafe on the menus themselves (B on "Use next
            # POKéMON?" = NO = run away)
            waiting = rb is not None and (rb.auto or (rb.decision is not None and rb.ui in (
                "menu", "moves", "next_mon", "party_faint")))
            if self.idle_b and not waiting and not self.queue \
                    and self.frame - max(self.last_input, self.last_event) > 90 and not self.gm.is_safe():
                self.pb.button("b", 3)
                self.last_input = self.frame
            self.pb.tick()
            self.frame += 1
            # the "server": hits arrive after `lag` frames, the pool comes back with the player's acked total
            while self.inflight and self.inflight[0][0] <= self.frame:
                _, mb = self.inflight.pop(0)
                self.pool = max(0, self.pool - mb)
                self.acked += mb
                if rb is not None:
                    rb.set_pool(self.pool, self.acked)
            if rb is not None:
                for ev in rb.step():
                    self.events.append((self.frame, ev))
                    self.last_event = self.frame
                    if ev["type"] == "damage":
                        self.inflight.append((self.frame + self.lag, ev["mb"]))
                    if self.on_event:
                        self.on_event(ev)

    def raid(self, boss, pool):
        self.rb = raid_gb.RaidBattle(self.pb, boss, game=self.game_id, rom=self.rd, gm=self.gm)
        self.pool = self.pool0 = pool
        self.acked = 0
        for _ in range(240):
            if self.gm.is_safe():
                break
            self.tick()
        ok, why = self.rb.can_join()
        assert ok, why
        assert self.rb.start(pool)
        return self.rb

    def run_until(self, cond, frames):
        for _ in range(frames):
            self.tick()
            if cond():
                return True
        return False

    def kinds(self):
        return [e["type"] for _f, e in self.events]

    def ended(self):
        return any(e["type"] == "ended" for _f, e in self.events)

    def outcome(self):
        for _f, e in self.events:
            if e["type"] == "ended":
                return e["outcome"]
        return None

    def balls(self):
        if self.game_id == "yellow":
            return dict(self.gm.bag_items())
        mm = self.m
        return {mm[crystal.BALLS + 2 * i]: mm[crystal.BALLS + 2 * i + 1] for i in range(mm[crystal.NUM_BALLS])}

    def shot(self, name):
        try:
            self.pb.screen.image.save(os.path.join(OUT, name))
        except Exception as e:  # noqa: BLE001
            INFO("screenshot failed: %s" % e)

    # test-only: make a Lv5 starter able to hurt the boss (written once the battle is up, not by raid_gb)
    def weaken_boss(self):
        m = self.m
        if self.game_id == "yellow":
            for a in (0xCFF7, 0xCFFB):                 # wEnemyMonDefense, wEnemyMonSpecial
                m.w16(a, 5)
        else:
            m.w16(0xD21C, 5)                           # wEnemyMonDefense
            m.w16(0xD222, 5)                           # wEnemyMonSpclDef

    def ball_keys(self, ball):
        """Battle menu -> ITEM / PACK -> that ball -> use (the test player's buttons)."""
        if self.game_id == "yellow":
            items = [i for i, _q in self.gm.bag_items()]
            k = items.index(ball)
            return ("down", "a") + ("down",) * k + ("a",)
        # Crystal: PACK on the balls pocket, cursor on the first ball (the game remembers both)
        mm = self.m
        mm[0xD0D6] = 1                                 # wLastPocket = BALL_POCKET
        mm[0xCF65] = 1                                 # wCurPocket
        mm[0xD0DB] = 0                                 # wBallsPocketCursor
        mm[0xD0E1] = 0                                 # wBallsPocketScrollPosition
        order = [mm[crystal.BALLS + 2 * i] for i in range(mm[crystal.NUM_BALLS])]
        k = order.index(ball)
        return ("down", "a") + ("down",) * k + ("a", "a")


CHECK = INFO = None
STATES = OUT = None


def boss(game_id, species, level, moves, shiny=False):
    region = raid_data.GAME_REGION[game_id]
    b = raid_data.boss_for(species, 30, region, shiny=shiny)
    b["level"] = level
    b["moves"] = (list(moves) + [0, 0, 0, 0])[:4]
    return b


# -----------------------------------------------------------------------------------------------------------------
def check_addresses(game_id, sym):
    """Every address raid_gb uses = the pret symbol (when a symbol file is there) + plausible code bytes."""
    S = raid_gb.spec_for(game_id)
    names = {
        "yellow": {"init": "_InitBattleCommon", "menu": "DisplayBattleMenu", "moves": "MoveSelectionMenu",
                   "turn": "MainInBattleLoop", "locked": "MainInBattleLoop.selectEnemyMove", "ball": "ItemUseBall",
                   "ball_roll": "ItemUseBall.loop", "next_mon": "DoUseNextMonDialogue",
                   "party_faint": "ChooseNextMon", "bag": "BagWasSelected", "party": "PartyMenuOrRockOrRun",
                   "enemy_fainted": "HandleEnemyMonFainted", "blackout": "HandlePlayerBlackOut"},
        "crystal": {"init": "BackUpBGMap2", "menu": "BattleMenu", "moves": "MoveSelectionScreen",
                    "turn": "BattleTurn.loop", "locked": "EnemyTriesToFlee", "ball": "PokeBallEffect",
                    "ball_roll": "PokeBallEffect.skip_hp_calc", "ball_result": "PokeBallEffect.catch_without_fail",
                    "next_mon": "AskUseNextPokemon", "party_faint": "ForcePickPartyMonInBattle",
                    "bag": "BattleMenu_Pack", "party": "BattleMenu_PKMN", "enemy_fainted": "HandleEnemyMonFaint",
                    "blackout": "LostBattle", "guard": "HasEnemyFainted", "guard_cmd": "BattleCommand_CheckFaint",
                    "guard_pmf": "HandlePlayerMonFaint", "perish": "HandlePerishSong.kill_enemy",
                    "dvs": "LoadEnemyMon.UpdateDVs"},
    }[game_id]
    if not sym:
        INFO("no pret symbol file: address check skipped")
        return
    bad = [n for n, s in names.items() if sym.get(s) != S.HOOKS[n][:2]]
    ram = {"yellow": {"IN_BATTLE": "wIsInBattle", "CUR_OPPONENT": "wCurOpponent", "CUR_ENEMY_LEVEL": "wCurEnemyLevel",
                      "ENEMY_HP": "wEnemyMonHP", "ENEMY_MOVES": "wEnemyMonMoves", "ENEMY_PP": "wEnemyMonPP",
                      "ENEMY_DVS": "wEnemyMonDVs", "ENEMY_MAX_HP": "wEnemyMonMaxHP", "ENEMY_STATUS": "wEnemyMonStatus",
                      "ACTUAL_CATCH_RATE": "wEnemyMonActualCatchRate", "ENEMY_SELECTED_MOVE": "wEnemySelectedMove",
                      "CUR_MENU_ITEM": "wCurrentMenuItem", "MOVE_LIST_INDEX": "wPlayerMoveListIndex",
                      "BATTLE_MON_MOVES": "wBattleMonMoves", "BATTLE_MON_PP": "wBattleMonPP",
                      "DISABLED_MOVE": "wPlayerDisabledMove", "ENEMY_TYPES": "wEnemyMonType1",
                      "BATTLE_MON_TYPES": "wBattleMonType1", "BOX_COUNT": "wBoxCount",
                      "ENEMY_SPEED": "wEnemyMonSpeed"},
           "crystal": {"IN_BATTLE": "wBattleMode", "TEMP_WILD_SPECIES": "wTempWildMonSpecies",
                       "CUR_PARTY_LEVEL": "wCurPartyLevel", "REENTRY_FLAG": "wMapReentryScriptQueueFlag",
                       "ENEMY_HP": "wEnemyMonHP", "ENEMY_MOVES": "wEnemyMonMoves", "ENEMY_PP": "wEnemyMonPP",
                       "ENEMY_DVS": "wEnemyMonDVs", "ENEMY_MAX_HP": "wEnemyMonMaxHP", "ENEMY_STATUS": "wEnemyMonStatus",
                       "WILD_MON_PP": "wWildMonPP", "CUR_ENEMY_MOVE": "wCurEnemyMove", "MENU_CURSOR_Y": "wMenuCursorY",
                       "PARTY_MENU_CURSOR": "wPartyMenuCursor", "CUR_MOVE_NUM": "wCurMoveNum",
                       "BATTLE_MON_MOVES": "wBattleMonMoves", "BATTLE_MON_PP": "wBattleMonPP",
                       "DISABLED_MOVE": "wDisabledMove", "ENEMY_TYPES": "wEnemyMonType1",
                       "BATTLE_MON_TYPES": "wBattleMonType1", "ENEMY_CATCH_RATE": "wEnemyMonCatchRate",
                       "ENEMY_SPEED": "wEnemyMonSpeed", "PLAYER_SUBSTATUS5": "wPlayerSubStatus5",
                       "ENEMY_SUBSTATUS5": "wEnemySubStatus5"}}[game_id]
    bad += [n for n, s in ram.items() if sym.get(s, (None, None))[1] != getattr(S, n)]
    targets = {"yellow": {"NOT_TIME": "ItemUseNotTime", "CAPTURED": "ItemUseBall.captured",
                          "SET_ANIM": "ItemUseBall.setAnimData"},
               "crystal": {"NOT_TIME": "IsntTheTimeMessage", "CATCH_OK": "PokeBallEffect.catch_without_fail",
                           "CATCH_FAIL": "PokeBallEffect.fail_to_catch"}}[game_id]
    bad += [n for n, s in targets.items() if sym.get(s) != getattr(S, n)]
    if game_id == "crystal":
        wb = sym.get("WildBattleScript")
        if not wb or (wb[0], wb[1] + 1) != S.WILD_BATTLE_SCRIPT:
            bad.append("WILD_BATTLE_SCRIPT")
    tbl = {"yellow": ("TypeEffects", "Moves"), "crystal": ("TypeMatchups", "Moves")}[game_id]
    for name, attr in zip(tbl, ("TYPE_CHART", "MOVES_ROM")):
        b, a = sym[name]
        if b * 0x4000 + (a - 0x4000) != getattr(S, attr):
            bad.append(attr)
    CHECK("%s: every hook / RAM address = its pret symbol" % game_id, not bad, "mismatch: %s" % bad)


def scenario_fight_catch(game_id):
    shiny = True
    species = MEW if game_id == "yellow" else CELEBI
    state = "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    print("== %s: raid battle, shared HP over several bars, timer + auto-pick, ball blocked, forced catch "
          "(shiny boss) ==" % game_id, flush=True)
    g.lag = 20
    poke = POKE_BALL_Y if game_id == "yellow" else POKE_BALL_C
    balls0 = g.balls()
    n0 = len(g.gm.party())
    rb = g.raid(boss(game_id, species, 30, [SPLASH], shiny=shiny), 1300)
    st = {"turn": 0, "auto_at": None, "threw": False, "down": False, "weak": False}

    def on_event(ev):
        if ev["type"] == "started" and not st["weak"]:
            g.weaken_boss()
            st["weak"] = True
        if ev["type"] == "need_input" and ev["what"] == "menu":
            st["turn"] += 1
            if rb.phase == "down" and not st["threw"]:
                st["threw"] = True
                g.press_later(40, *g.ball_keys(poke))
            elif st["turn"] == 1:
                g.press_later(40, *g.ball_keys(poke))         # a ball while fighting: refused
                st["auto_at"] = g.frame + 500
            else:
                st["auto_at"] = g.frame + 60                  # the 10-s timer "runs out" quickly here

    g.on_event = on_event
    ok = g.run_until(lambda: rb.phase != "starting", 3000)
    CHECK("%s: wild battle started on demand" % game_id, ok and g.gm.in_battle())
    m, S = g.m, g.S
    CHECK("%s: boss = species/level/raid moves before the first menu" % game_id,
          m[S.ENEMY_SPECIES] == rb.species and m[S.ENEMY_LEVEL] == 30 and m[S.ENEMY_MOVES] == SPLASH,
          "species %d level %d moves %s" % (m[S.ENEMY_SPECIES], m[S.ENEMY_LEVEL],
                                            [m[S.ENEMY_MOVES + i] for i in range(4)]))
    if game_id == "crystal":
        CHECK("crystal: shiny DVs given at LoadEnemyMon.UpdateDVs (before the boss's stats)",
              any(n == "shiny DVs" for _f, n in rb.log))
    bi = g.gm.battle_info() or {}
    CHECK("%s: shiny boss (DVs EA AA, the game's own battle_info says shiny)" % game_id,
          (m[S.ENEMY_DVS], m[S.ENEMY_DVS + 1]) == raid_gb.SHINY_DVS and bi.get("shiny"), str(bi))
    for _ in range(60000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(1.0)
        if g.ended():
            break
        if g.frame % 2000 == 0:
            INFO("f%d phase %s pool %d ui %s" % (g.frame, rb.phase, g.pool, rb.ui))
    dmg = [e for _f, e in g.events if e["type"] == "damage"]
    notes = [t for _f, t in rb.log]
    INFO("last notes: %s" % rb.log[-8:])
    INFO("hits (mb): %s" % [d["mb"] for d in dmg])
    CHECK("%s: damage adds up exactly to the pool (milli-bars)" % game_id, sum(d["mb"] for d in dmg) == g.pool0
          and g.pool == 0, "sum %d of %d" % (sum(d["mb"] for d in dmg), g.pool0))
    CHECK("%s: several bars, the boss never fainted locally" % game_id,
          notes.count("clamped to 1") + sum(1 for n in notes if n.startswith("guard")) >= 1
          and not any("escaped" in n for n in notes), "clamps %d" % notes.count("clamped to 1"))
    CHECK("%s: ball refused while fighting" % game_id, "ball blocked" in notes)
    k = g.kinds()
    CHECK("%s: timer events every turn (need_input / input_done)" % game_id,
          k.count("need_input") >= 3 and k.count("input_done") >= 3,
          "need_input %d input_done %d" % (k.count("need_input"), k.count("input_done")))
    CHECK("%s: catch phase: one ball, forced catch -> caught + ended(caught)" % game_id,
          g.outcome() == "caught" and "caught" in k, "outcome %s events %s" % (g.outcome(), k[-6:]))
    party = g.gm.party()
    if len(party) == n0 + 1:
        c = party[-1]
        dv = bytes(c.data[27:29]) if game_id == "yellow" else bytes(c.data[21:23])
        INFO("caught: species %d level %d moves %s dvs %s" % (c.species, c.level, list(c.moves), dv.hex()))
        CHECK("%s: caught boss = level 30, natural moves (not the raid Splash), shiny DVs, player's OT" % game_id,
              c.species == rb.species and c.level == 30 and SPLASH not in list(c.moves) and any(c.moves)
              and tuple(dv) == raid_gb.SHINY_DVS and c.hp >= 1, "hp %d/%d" % (c.hp, c.max_hp))
        key = [e for _f, e in g.events if e["type"] == "caught"][0]["key"]
        CHECK("%s: caught event carries the mon's key" % game_id, key == g.mod.mon_key(
            c.data if game_id == "yellow" else c.box_record()), "key %s" % key)
    else:
        CHECK("%s: caught boss in the party" % game_id, False, "party %d -> %d" % (n0, len(party)))
    b1 = g.balls()
    CHECK("%s: balls: the refused one kept, the catch used one" % game_id,
          b1.get(poke, 0) == balls0.get(poke, 0) - 1 and b1.get(MASTER_BALL) == balls0.get(MASTER_BALL),
          "%s -> %s" % (balls0, b1))
    CHECK("%s: hooks removed at the end, not busy" % game_id, not rb.busy() and not rb._installed)
    g.shot("%s_raid_caught.png" % game_id)
    g.stop()


def scenario_miss(game_id):
    """Pool down quickly, catch chance 0: the one ball (a Master Ball!) fails and the boss runs away."""
    print("== %s: forced miss with a Master Ball, the boss flees ==" % game_id, flush=True)
    species = MEW if game_id == "yellow" else CELEBI
    state = "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    balls0 = g.balls()
    n0 = len(g.gm.party())
    rb = g.raid(boss(game_id, species, 30, [SPLASH]), 1)
    st = {"down": False, "threw": False, "auto_at": None}

    def on_event(ev):
        if ev["type"] == "started":
            g.weaken_boss()
        if ev["type"] == "need_input" and ev["what"] == "menu":
            if rb.phase == "down" and not st["threw"]:
                st["threw"] = True
                g.press_later(40, *g.ball_keys(MASTER_BALL))
            elif rb.phase == "fight":
                st["auto_at"] = g.frame + 30

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(0.0)
        if g.ended():
            break
    k = g.kinds()
    INFO("last notes: %s" % rb.log[-8:])
    CHECK("%s: miss -> missed, fled, ended(missed); party unchanged" % game_id,
          g.outcome() == "missed" and "missed" in k and "fled" in k and len(g.gm.party()) == n0,
          "outcome %s events %s" % (g.outcome(), k[-5:]))
    b1 = g.balls()
    CHECK("%s: the Master Ball was used up by the missed throw" % game_id,
          b1.get(MASTER_BALL, 0) == balls0.get(MASTER_BALL, 0) - 1, "%s -> %s" % (balls0, b1))
    turns_after = [n for f, n in rb.log if "ball thrown" in n]
    CHECK("%s: back in the overworld afterwards" % game_id, g.run_until(g.gm.is_safe, 600), str(turns_after))
    g.stop()


def scenario_afk_catch(game_id):
    """A player who never presses anything: the timer's auto_pick fights and, in the catch phase, throws the
    cheapest ball (a Poké Ball, not the Master Ball)."""
    print("== %s: AFK player: auto-pick fights and throws the ball in the catch phase ==" % game_id, flush=True)
    species = MEW if game_id == "yellow" else CELEBI
    state = "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    poke = POKE_BALL_Y if game_id == "yellow" else POKE_BALL_C
    balls0 = g.balls()
    n0 = len(g.gm.party())
    rb = g.raid(boss(game_id, species, 30, [SPLASH]), 1)
    st = {"down": False, "auto_at": None}

    def on_event(ev):
        if ev["type"] == "started":
            g.weaken_boss()
        if ev["type"] == "need_input":
            st["auto_at"] = g.frame + 30

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(1.0)
        if g.ended():
            break
    b1 = g.balls()
    CHECK("%s: AFK catch: auto-pick threw a Poké Ball (Master Ball kept), caught" % game_id,
          g.outcome() == "caught" and len(g.gm.party()) == n0 + 1 and b1.get(poke, 0) == balls0.get(poke, 0) - 1
          and b1.get(MASTER_BALL) == balls0.get(MASTER_BALL), "outcome %s balls %s -> %s" % (g.outcome(), balls0, b1))
    g.stop()


def scenario_trapped_flee(game_id):
    """M1: the boss is trapped by the player (Yellow: Wrap - the boss can't act; Crystal: Mean Look - its Teleport
    would fail) when the raid time runs out: the flee still ends the battle (Crystal: the trap is lifted; both: the
    player runs - the boss's speed is 1 then)."""
    move, name = (35, "Wrap") if game_id == "yellow" else (212, "Mean Look")
    print("== %s: time over while the player's %s traps the boss ==" % (game_id, name), flush=True)
    species = MEW if game_id == "yellow" else CELEBI
    g = GB(game_id, "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state")
    rb = g.raid(boss(game_id, species, 30, [SPLASH]), 5000)
    st = {"auto_at": None, "n": 0, "trapped": False}
    moves = 0xD01B if game_id == "yellow" else 0xC62E          # wBattleMonMoves
    pp = 0xD02C if game_id == "yellow" else 0xC634             # wBattleMonPP

    def on_event(ev):
        if ev["type"] == "need_input":
            st["n"] += 1
            if st["n"] == 1:                       # test only: the lead knows just the trapping move
                g.m[moves] = move
                for i in range(1, 4):
                    g.m[moves + i] = 0
                g.m[pp] = 10
            elif not rb.flee_asked:
                if game_id == "crystal":
                    st["trapped"] = bool(g.m[0xC66C] & 0x80)      # Gen 2 sets CANT_RUN on the user
                else:
                    st["trapped"] = True
                rb.flee()                          # time over (YellowLink takes over at once)
            st["auto_at"] = g.frame + 30

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"] and not rb.flee_asked:
            st["auto_at"] = None
            rb.auto_pick()
        if g.ended():
            break
    CHECK("%s: trapped boss (%s) + time over -> fled + ended(fled), back in the overworld" % (game_id, name),
          st["trapped"] and g.outcome() == "fled" and g.run_until(g.gm.is_safe, 900),
          "trapped %s outcome %s notes %s" % (st["trapped"], g.outcome(), [n for _f, n in rb.log][-4:]))
    g.stop()


def scenario_start_cancel(game_id):
    """L3: a start that never turns into a battle takes its request back (wCurOpponent / the memory script)."""
    print("== %s: a start that times out is taken back ==" % game_id, flush=True)
    species = MEW if game_id == "yellow" else CELEBI
    g = GB(game_id, "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state")
    rb = g.raid(boss(game_id, species, 30, [SPLASH]), 5000)
    rb._start_frame -= 60 * 31                     # pretend 31 s went by without a battle
    S = g.S
    ev = rb.step()
    ok = any(e["type"] == "ended" and e["outcome"] == "error" for e in ev)
    left = g.m[S.CUR_OPPONENT] if game_id == "yellow" else g.m[S.REENTRY_FLAG]
    g.tick(400)
    CHECK("%s: timed-out start -> ended(error), request cleared, no battle afterwards" % game_id,
          ok and left == 0 and not g.gm.in_battle(), "events %s left %s" % (ev, left))
    g.stop()


def scenario_flee(game_id):
    print("== %s: raid time over -> the boss teleports away ==" % game_id, flush=True)
    species = MEW if game_id == "yellow" else CELEBI
    state = "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    rb = g.raid(boss(game_id, species, 30, [SPLASH]), 5000)
    st = {"auto_at": None, "fled": False}

    def on_event(ev):
        if ev["type"] == "need_input":
            if not st["fled"]:
                st["fled"] = True
                rb.flee()
            st["auto_at"] = g.frame + 30

    g.on_event = on_event
    for _ in range(12000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.ended():
            break
    CHECK("%s: flee() -> fled + ended(fled), no faint" % game_id, g.outcome() == "fled" and "fled" in g.kinds()
          and not any("escaped" in n for _f, n in rb.log), "outcome %s" % g.outcome())
    CHECK("%s: back in the overworld afterwards" % game_id, g.run_until(g.gm.is_safe, 900))
    g.stop()


def scenario_next_mon_blackout(game_id):
    """The boss knocks out the lead: 'Use next Pokémon?' + party pick by auto_pick, then the last one -> blackout."""
    print("== %s: faint -> auto-pick sends the next healthy Pokémon; then blackout ==" % game_id, flush=True)
    species = MEW if game_id == "yellow" else CELEBI
    state = "soullink_party3.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    if game_id == "crystal":
        g.m.w16(crystal.PARTY_MONS + 48 * 1 + 34, 0)       # Pidgey fainted: the next healthy one is slot 2
    rb = g.raid(boss(game_id, species, 30, [PSYCHIC_M]), 5000)
    st = {"auto_at": None}

    def on_event(ev):
        if ev["type"] == "need_input":
            st["auto_at"] = g.frame + 45

    g.on_event = on_event
    sent = []
    for _ in range(40000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.ended():
            break
    whats = [e.get("what") for _f, e in g.events if e["type"] == "need_input"]
    INFO("decisions: %s" % whats)
    CHECK("%s: 'next_mon' decision after the faint, auto-pick answered it" % game_id,
          "next_mon" in whats and whats.index("next_mon") < len(whats) - 1 or whats.count("next_mon") >= 1,
          str(whats))
    CHECK("%s: blackout -> blackout + ended(blackout)" % game_id, g.outcome() == "blackout" and "blackout" in g.kinds(),
          "outcome %s" % g.outcome())
    party = g.gm.party()
    CHECK("%s: the game's own blackout (healed at a Pokémon Center)" % game_id,
          g.run_until(g.gm.is_safe, 3000) and all(p.hp == p.max_hp for p in g.gm.party()),
          "%s" % [(p.hp, p.max_hp) for p in party])
    del sent
    g.stop()


def scenario_perish_dbond(game_id):
    """Crystal only (Gen 1 has neither move): Perish Song and Destiny Bond never take the boss down."""
    if game_id != "crystal":
        return
    for move, name in ((195, "Perish Song"), (194, "Destiny Bond")):
        print("== crystal: the player's %s against the boss ==" % name, flush=True)
        g = GB(game_id, "crystal_sl_route29.state")
        rb = g.raid(boss(game_id, CELEBI, 30, [SPLASH] if move == 195 else [PSYCHIC_M]), 5000)
        st = {"auto_at": None, "set": False, "fled": False}

        def on_event(ev, g=g, rb=rb, st=st, move=move):
            if ev["type"] == "need_input" and ev["what"] == "menu":
                m = g.m
                if not st["set"]:                     # test only: the lead knows just this move; the boss is slow
                    st["set"] = True
                    m[0xC62E] = move                                 # wBattleMonMoves
                    for i in range(1, 4):
                        m[0xC62E + i] = 0
                    m[0xC634] = 5                                    # wBattleMonPP
                    m.w16(0xD21E, 1)                                 # wEnemyMonSpeed
                    g.weaken_boss()
                st["auto_at"] = g.frame + 30
            if ev["type"] == "need_input" and ev["what"] == "next_mon" and not st["fled"]:
                st["fled"] = True
                rb.flee()
                st["auto_at"] = g.frame + 30

        g.on_event = on_event
        for _ in range(30000):
            g.tick()
            if st["auto_at"] is not None and g.frame >= st["auto_at"]:
                st["auto_at"] = None
                rb.auto_pick()
            if g.ended():
                break
        notes = [n for _f, n in rb.log]
        INFO("notes: %s" % [n for n in notes if "perish" in n or "destiny" in n or "escaped" in n or "ended" in n])
        bars = [e["mb"] for _f, e in g.events if e["type"] == "damage"]
        CHECK("crystal: %s took the boss's current bar (like a one-hit KO), the boss stayed, the raid went on" % name,
              any(name.lower() in n for n in notes) and not any("escaped" in n for n in notes)
              and bars and bars[-1] >= 900 and g.outcome() in ("fled", "blackout"),
              "outcome %s hits %s" % (g.outcome(), bars))
        g.stop()


def scenario_den(game_id):
    print("== %s: den drawn on the map, collision, A to join ==" % game_id, flush=True)
    import pygame
    state = "soullink_route1_grass.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    g.tick(30)
    p = g.gm.position()
    spot = {"region": raid_data.GAME_REGION[game_id], "map": g.gm.map_id(), "x": p["x"], "y": p["y"] - 2,
            "name": "test", "reach": 0}
    den = raid_gb.Den(spot, game_id)
    img = g.pb.screen.image.convert("RGB")
    surf = pygame.image.frombuffer(img.tobytes(), img.size, "RGB").copy()
    vis = den.draw(surf, g.gm, t=0.3)
    pygame.image.save(pygame.transform.scale(surf, (480, 432)), os.path.join(OUT, "den_%s.png" % game_id))
    CHECK("%s: den drawn on screen (screenshot den_%s.png)" % (game_id, game_id), vis)
    # walk up one square: free; the next step would enter the den -> blocked
    CHECK("%s: two squares away: 'up' not blocked" % game_id, not den.blocks(g.gm, "up"))
    g.pb.button("up", 18)
    g.run_until(lambda: g.gm.is_safe() and g.gm.position()["y"] == p["y"] - 1, 200)
    CHECK("%s: next to the den: 'up' is blocked, near() True" % game_id, den.blocks(g.gm, "up") and den.near(g.gm),
          "pos %s" % g.gm.position())
    CHECK("%s: A next to the den (pushed against it) -> join prompt" % game_id, den.pressed_a(g.gm))
    surf2 = pygame.Surface((160, 144))
    far = raid_gb.Den(dict(spot, x=p["x"] + 30, y=p["y"] - 2), game_id)
    CHECK("%s: off screen -> edge arrow, offset in squares" % game_id,
          not far.draw(surf2, g.gm) and far.offset(g.gm)[0] == 30)
    pygame.image.save(pygame.transform.scale(surf2, (480, 432)), os.path.join(OUT, "den_%s_arrow.png" % game_id))
    g.stop()
    # a real spot from client/yellowlink/raid_spots.json, next to a fixture's player
    state = "pallet_after_lab.state" if game_id == "yellow" else "crystal_sl_route29.state"
    g = GB(game_id, state)
    g.tick(30)
    p = g.gm.position()
    region = raid_data.GAME_REGION[game_id]
    near = sorted((s for s in raid_data.spots(region) if s["map"] == g.gm.map_id()),
                  key=lambda s: abs(s["x"] - p["x"]) + abs(s["y"] - p["y"]))
    den = raid_gb.Den(near[0], game_id)
    img = g.pb.screen.image.convert("RGB")
    surf = pygame.image.frombuffer(img.tobytes(), img.size, "RGB").copy()
    vis = den.draw(surf, g.gm, t=0.3)
    pygame.image.save(pygame.transform.scale(surf, (480, 432)), os.path.join(OUT, "den_%s_spot.png" % game_id))
    INFO("real spot %s at (%d, %d), player at (%d, %d): %s" % (near[0]["name"], near[0]["x"], near[0]["y"], p["x"],
                                                              p["y"], "den on screen" if vis else "arrow"))
    g.stop()


def run(game_id, check, info, sym, states, out):
    global CHECK, INFO, STATES, OUT
    CHECK, INFO, STATES, OUT = check, info, states, out
    import pygame
    pygame.init()
    check_addresses(game_id, sym)
    scenario_fight_catch(game_id)
    scenario_miss(game_id)
    scenario_afk_catch(game_id)
    scenario_flee(game_id)
    scenario_trapped_flee(game_id)
    scenario_start_cancel(game_id)
    scenario_next_mon_blackout(game_id)
    scenario_perish_dbond(game_id)
    scenario_den(game_id)
