"""Raid battles in Pokémon Yellow and Pokémon Crystal (PyBoy), through the games' own battle engine.

    rb = RaidBattle(app, boss)          # boss = raid_data.boss_for(...); interface: dev/raids/STATUS.md
    ok, why = rb.can_join()
    rb.start(pool_mb)                   # only while game.is_safe()
    each frame: for ev in rb.step(): ...
    rb.set_pool(pool_mb[, dealt_mb])    # group HP left; rb.group_down(chance); rb.flee(); rb.auto_pick()

HOW (DESIGN.md appendix A; addresses = pret symbols, checked against the ROM's code bytes before installing):
  start    Yellow: wCurOpponent = species, wCurEnemyLevel = level while is_safe() -> the overworld loop starts a
           wild battle on the next frame (InitWildBattle). Crystal: what `loadwildmon` sets + a memory script
           pointing at the ROM's own WildBattleScript + 1 (startbattle / reloadmapafterbattle / end), so the
           game's own script handles the battle, the blackout and "box full - Bill calls".
  override at Yellow _InitBattleCommon / Crystal BackUpBGMap2 (after the enemy is loaded, before its HUD and
           the shiny check): raid moves + PP, HP = the pool's current segment, shiny DVs (EA AA) for a shiny
           boss. The boss keeps its real max HP (milli-bars make every player's bar the same size).
  turn     at Yellow MainInBattleLoop / Crystal BattleTurn.loop (before the game's HP-0 test and the AI's move
           choice): local HP := segment of the group pool, boss PP refilled (Crystal spends wWildMonPP),
           per phase: fight = raid moves; down = asleep at 1 HP, Splash; flee = Teleport (Yellow: level 100).
  shared HP  the boss never faints while the group pool has HP: every frame (VBlank hook) an HP of 0 is set to 1
           (Yellow: 24-111 frames between HP 0 and the faint test, Crystal 40); Crystal also has guard hooks on
           its faint tests (HasEnemyFainted and every `ld hl, wEnemyMonHP / ld a, [hli] / or [hl]`, which run
           right after Perish Song's kill (HandlePerishSong.kill_enemy) and Destiny Bond's (BattleCommand_CheckFaint)
           set the boss's HP to 0): those take the boss's current bar, like a one-hit KO (DESIGN A.1) - the same as in
           Platinum. Yellow has neither move (Gen 1).
           Damage = drops of the boss's HP, turned into milli-bars by raid_data.BarMeter (exact per bar).
  balls    blocked while fighting (ItemUseBall / PokeBallEffect -> the game's "This isn't the time to use
           that!": no turn used, the ball stays). Catch phase: ONE throw; its result is forced inside the
           game's own catch code: Yellow ItemUseBall.loop -> .captured (success) or B = $63 + .setAnimData
           ("Shoot! It was so close too!"); Crystal .skip_hp_calc -> .catch_without_fail (success) or
           .catch_without_fail -> A = 0 + .fail_to_catch (miss). A miss -> the boss wakes up and teleports away.
  timer    waiting = Yellow DisplayBattleMenu / MoveSelectionMenu / DoUseNextMonDialogue / ChooseNextMon (+ bag
           and party lists), Crystal BattleMenu / MoveSelectionScreen / AskUseNextPokemon /
           ForcePickPartyMonInBattle (+ pack / party); locked in = Yellow MainInBattleLoop.selectEnemyMove,
           Crystal EnemyTriesToFlee. auto_pick() drives the buttons: B out of lists, FIGHT, the last move with
           PP (else the strongest by power x STAB x type), YES to "Use next POKéMON?", the first healthy mon.
Everything else (the catch itself, Pokédex, nickname, PC when the party is full, blackout, EXP: none, the boss
never faints) is the game's own code.
"""
from __future__ import annotations

import logging
import random
import time

from . import raid_art, raid_data

log = logging.getLogger("yellowlink.raid")

SPLASH, TELEPORT = 150, 100
SHINY_DVS = (0xEA, 0xAA)                  # Gen-2 shiny DVs (Crystal's ATKDEFDV_SHINY / SPDSPCDV_SHINY)
SLP = 7                                   # sleep counter bits: 7 turns (re-applied every turn)
VBLANK = (0x00, 0x0040)                   # interrupt vector: once per frame


def _ok_opcode(m, bank, addr, expect):
    try:
        return m[bank, addr] == expect if bank else m[addr] == expect
    except Exception:  # noqa: BLE001
        return False


class _Mem:
    """RAM access; Crystal's D000-DFFF through WRAM bank 1 (CLAUDE.md)."""

    def __init__(self, memory, banked: bool):
        self.m = memory
        self.banked = banked

    def __getitem__(self, a):
        return self.m[1, a] if self.banked and 0xD000 <= a < 0xE000 else self.m[a]

    def __setitem__(self, a, v):
        if self.banked and 0xD000 <= a < 0xE000:
            self.m[1, a] = v
        else:
            self.m[a] = v

    def r16(self, a):
        return self[a] << 8 | self[a + 1]

    def w16(self, a, v):
        self[a], self[a + 1] = (v >> 8) & 0xFF, v & 0xFF


# --------------------------------------------------------------------------------------------------------------
# per-game addresses (pret pokeyellow.sym / pokecrystal.sym; dev/raids/verify.py re-checks every one of them)
# --------------------------------------------------------------------------------------------------------------
class YellowSpec:
    game = "yellow"
    banked = False
    # hooks: name -> (bank, addr, first opcode byte at that address)
    HOOKS = {
        "init": (0x3D, 0x60EB, None),           # _InitBattleCommon
        "menu": (0x0F, 0x4F78, None),           # DisplayBattleMenu
        "moves": (0x0F, 0x5320, None),          # MoveSelectionMenu
        "turn": (0x0F, 0x4249, None),           # MainInBattleLoop
        "locked": (0x0F, 0x42BC, None),         # MainInBattleLoop.selectEnemyMove
        "ball": (0x03, 0x53AD, None),           # ItemUseBall
        "ball_roll": (0x03, 0x5443, None),      # ItemUseBall.loop (call Random: Rand1)
        "next_mon": (0x0F, 0x47FF, None),       # DoUseNextMonDialogue
        "party_faint": (0x0F, 0x483C, None),    # ChooseNextMon
        "bag": (0x0F, 0x510A, None),            # BagWasSelected
        "party": (0x0F, 0x51CD, None),          # PartyMenuOrRockOrRun
        "enemy_fainted": (0x0F, 0x453B, None),  # HandleEnemyMonFainted (never reached in a raid; diagnostics)
        "blackout": (0x0F, 0x489C, None),       # HandlePlayerBlackOut
    }
    NOT_TIME = (0x03, 0x64B5)                   # ItemUseNotTime
    CAPTURED = (0x03, 0x54D4)                   # ItemUseBall.captured
    SET_ANIM = (0x03, 0x554A)                   # ItemUseBall.setAnimData (B = shakes/anim value)
    MISS_ANIM = 0x63                            # 3 shakes: "Shoot! It was so close too!"
    IN_BATTLE = 0xD056                          # wIsInBattle: 1 wild, 2 trainer, 0xFF lost
    CUR_OPPONENT = 0xD058
    CUR_ENEMY_LEVEL = 0xD126
    BATTLE_TYPE = 0xD059
    ENEMY_SPECIES = 0xCFE4                      # wEnemyMonSpecies (internal index)
    ENEMY_HP = 0xCFE5
    ENEMY_STATUS = 0xCFE8
    ENEMY_TYPES = 0xCFE9
    ENEMY_CATCH_RATE = 0xCFEB
    ENEMY_MOVES = 0xCFEC
    ENEMY_DVS = 0xCFF0
    ENEMY_LEVEL = 0xCFF2
    ENEMY_MAX_HP = 0xCFF3
    ENEMY_PP = 0xCFFD
    ACTUAL_CATCH_RATE = 0xD006                  # wEnemyMonActualCatchRate (what ItemUseBall reads)
    ENEMY_SELECTED_MOVE = 0xCCDD                # wEnemySelectedMove
    BATTLE_MON_TYPES = 0xD018
    BATTLE_MON_MOVES = 0xD01B
    BATTLE_MON_PP = 0xD02C
    DISABLED_MOVE = 0xD06C                      # wPlayerDisabledMove: hi nibble = slot + 1
    PARTY_COUNT = 0xD162
    PARTY_MONS = 0xD16A
    PARTY_MON_SIZE = 44
    BOX_COUNT = 0xDA7F
    CUR_MENU_ITEM = 0xCC26                      # wCurrentMenuItem (move menu: slot + 1)
    MOVE_LIST_INDEX = 0xCC2E                    # wPlayerMoveListIndex (last move slot picked)
    MOVES_ROM = 0x38000                         # Moves (0E:4000): 6 bytes, power +2, type +3
    MOVE_SIZE = 6
    TYPE_CHART = 0x3E5FA                        # TypeEffects (0F:65FA): (atk, def, x10) ... $FF
    SAFARI = 2
    MAX_BOX = 20


class CrystalSpec:
    game = "crystal"
    banked = True
    HOOKS = {
        "init": (0x0F, 0x7568, None),           # BackUpBGMap2 (right after InitEnemy)
        "menu": (0x0F, 0x6139, None),           # BattleMenu
        "moves": (0x0F, 0x64BC, None),          # MoveSelectionScreen
        "turn": (0x0F, 0x412F, None),           # BattleTurn.loop
        "locked": (0x0F, 0x4300, None),         # EnemyTriesToFlee (right after ParsePlayerAction)
        "ball": (0x03, 0x68A2, None),           # PokeBallEffect
        "ball_roll": (0x03, 0x698E, None),      # PokeBallEffect.skip_hp_calc
        "ball_result": (0x03, 0x699C, None),    # PokeBallEffect.catch_without_fail
        "next_mon": (0x0F, 0x51F8, None),       # AskUseNextPokemon
        "party_faint": (0x0F, 0x5362, None),    # ForcePickPartyMonInBattle
        "bag": (0x0F, 0x61C7, None),            # BattleMenu_Pack
        "party": (0x0F, 0x628D, None),          # BattleMenu_PKMN
        "enemy_fainted": (0x0F, 0x4D55, None),  # HandleEnemyMonFaint (never reached in a raid; diagnostics)
        "blackout": (0x0F, 0x538E, None),       # LostBattle
        "guard": (0x0F, 0x470B, None),          # HasEnemyFainted
        "guard_cmd": (0x0D, 0x51C0, None),      # BattleCommand_CheckFaint
        "guard_pmf": (0x0F, 0x514E, None),      # HandlePlayerMonFaint
        "perish": (0x0F, 0x485C, 0x21),         # HandlePerishSong.kill_enemy (ld hl, wEnemyMonHP)
        "dbond": (0x0D, 0x51D5, 0x28),          # BattleCommand_CheckFaint: jr z, .no_dbond
    }
    DBOND_SIG = (0x0D, 0x51D3, (0xCB, 0x77))    # bit SUBSTATUS_DESTINY_BOND, a
    NOT_TIME = (0x03, 0x77ED)                   # IsntTheTimeMessage
    CATCH_OK = (0x03, 0x699C)                   # PokeBallEffect.catch_without_fail
    CATCH_FAIL = (0x03, 0x699F)                 # PokeBallEffect.fail_to_catch (a = 0 -> wWildMon = 0)
    GUARD_PATTERN = bytes.fromhex("2116D22AB6")   # ld hl, wEnemyMonHP / ld a, [hli] / or [hl]
    BATTLE_TURN = 0xFFE4                        # hBattleTurn
    IN_BATTLE = 0xD22D                          # wBattleMode (bank 1)
    BATTLE_TYPE = 0xD230
    TEMP_WILD_SPECIES = 0xD22E
    CUR_PARTY_LEVEL = 0xD143
    BATTLE_SCRIPT_FLAGS = 0xD459
    REENTRY_FLAG = 0xD45C
    REENTRY_BANK = 0xD45D
    REENTRY_ADDR = 0xD45E
    WILD_BATTLE_SCRIPT = (0x25, 0x7CFA)         # WildBattleScript (25:7CF9) + 1
    ENEMY_SPECIES = 0xD206
    ENEMY_MOVES = 0xD208
    ENEMY_DVS = 0xD20C
    ENEMY_PP = 0xD20E
    ENEMY_LEVEL = 0xD213
    ENEMY_STATUS = 0xD214
    ENEMY_HP = 0xD216
    ENEMY_MAX_HP = 0xD218
    ENEMY_TYPES = 0xD224
    ENEMY_CATCH_RATE = 0xD22B
    WILD_MON_MOVES = 0xC735
    WILD_MON_PP = 0xC739
    CUR_ENEMY_MOVE = 0xC6E4                     # wCurEnemyMove
    CUR_ENEMY_MOVE_NUM = 0xC6E9
    BATTLE_MON_MOVES = 0xC62E
    BATTLE_MON_PP = 0xC634
    BATTLE_MON_TYPES = 0xC64A
    DISABLED_MOVE = 0xC6F5                      # wDisabledMove (the player's disabled move id)
    PARTY_COUNT = 0xDCD7
    PARTY_MONS = 0xDCDF
    PARTY_MON_SIZE = 48
    MENU_CURSOR_Y = 0xCFA9                      # wMenuCursorY (1-based)
    PARTY_MENU_CURSOR = 0xD0D8                  # wPartyMenuCursor (1-based; the battle party list uses wMenuCursorY)
    CUR_MOVE_NUM = 0xD0D5                       # wCurMoveNum (last move slot picked)
    MOVES_ROM = 0x41AFB                         # Moves (10:5AFB): 7 bytes, power +2, type +3
    MOVE_SIZE = 7
    TYPE_CHART = 0x34BB1                        # TypeMatchups (0D:4BB1): (atk, def, x10) ... $FE (Foresight) ... $FF
    SAFARI = None
    CONTEST = 4                                 # BATTLETYPE_CONTEST
    MAX_BOX = 20


def spec_for(game_id: str):
    return {"yellow": YellowSpec, "crystal": CrystalSpec}[game_id]


def strongest_level(gm) -> int:
    """The player's strongest party level (decision 3: a mythical boss is this + 5); eggs don't count."""
    try:
        return raid_data.strongest_level(p.level for p in gm.party() if getattr(p, "species", 0) not in (0, 0xFD))
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------------------------------------------
class RaidBattle:
    """One raid battle in the player's own game (see the module docstring and dev/raids/STATUS.md)."""

    def __init__(self, app_or_core=None, boss: dict | None = None, *, game: str | None = None, rom=None,
                 gm=None, rng=None):
        pyboy = getattr(app_or_core, "pyboy", None) or app_or_core
        self.pyboy = pyboy
        self.game_id = game or getattr(app_or_core, "game_id", None) or boss.get("game")
        self.S = spec_for(self.game_id)
        self.rom = rom if rom is not None else getattr(app_or_core, "rom", None)
        if self.rom is None:
            raise ValueError("RaidBattle needs the game's RomData")
        if gm is None:
            gm = getattr(app_or_core, "game", None)
        if gm is None:
            from . import crystal, game as yellow
            gm = (crystal.CrystalMemory if self.game_id == "crystal" else yellow.GameMemory)(pyboy.memory, self.rom)
        self.gm = gm
        self.m = _Mem(pyboy.memory, self.S.banked)
        self.boss = dict(boss)
        self.rng = rng or random.Random()
        dex = int(boss["species"])
        self.species = self.rom.species_for_dex(dex) if self.game_id == "yellow" else dex
        if not self.species:
            raise ValueError("species %d doesn't exist in %s" % (dex, self.game_id))
        self.level = int(boss["level"])
        self.moves = [int(x) for x in (list(boss.get("moves") or []) + [0, 0, 0, 0])[:4]]
        if not any(self.moves):
            self.moves = [SPLASH, 0, 0, 0]
        self.shiny = bool(boss.get("shiny"))
        self.phase = "idle"          # idle -> starting -> fight -> down -> (fleeing) -> over
        self.pool = 0                # milli-bars left (our best knowledge, incl. our unacknowledged hits)
        self.dealt = 0               # milli-bars this player has taken off (running total)
        self.meter = raid_data.BarMeter()
        self.max_hp = 0
        self.events = []
        self.frame = 0
        self.ui = None               # what the game waits for right now (menu, moves, bag, party, next_mon, ...)
        self.decision = None         # open decision {"what", "t"} (timer)
        self.auto = False            # auto_pick() in progress
        self._next_press = 0
        self._menu_seq = 0
        self.last_slot = None        # the move slot the player used last
        self.catch_success = None    # forced result of the one throw (None = not decided yet)
        self.throws = 0
        self.outcome = None
        self.missed = False
        self.flee_asked = False
        self.escaped = False
        self.blacked_out = False
        self._was_in_battle = False
        self._party0 = self._box0 = 0
        self._end_wait = 0
        self._installed = []
        self._busy = False
        self.log = []                # (frame, text) diagnostics

    @property
    def ui(self):
        return self._ui

    @ui.setter
    def ui(self, v):
        if v != getattr(self, "_ui", None) or v == "menu":
            self._ui_since = getattr(self, "frame", 0)
        self._ui = v

    # ------------------------------------------------------------------------------------------------------------
    # public API
    def can_join(self):
        S, m, gm = self.S, self.m, self.gm
        try:
            if not gm.in_game():
                return False, "not in the game yet"
            if not gm.is_safe():
                return False, "stand still in the overworld first"
            if self.game_id == "yellow":
                from . import game as yellow
                if m[S.BATTLE_TYPE] == S.SAFARI or yellow.SAFARI_ZONE_EAST <= gm.map_id() <= yellow.SAFARI_ZONE_CENTER:
                    return False, "not from the Safari Zone"
            else:
                if gm.r(0xD84D) & 0x04:              # wStatusFlags2 bit 2: Bug-Catching Contest running
                    return False, "not during the Bug-Catching Contest"
            party = gm.party()
            if not any(p.hp > 0 for p in party if getattr(p, "species", 0) not in (0, 0xFD)):
                return False, "you need a healthy Pokémon"
            if not gm.has_poke_balls():
                return False, "you need a Poké Ball"
            if self._party_count() >= 6 and self._box_count() >= S.MAX_BOX:
                return False, "your party and current PC box are full"
        except Exception as e:  # noqa: BLE001
            log.exception("raid can_join")
            return False, "can't check the game right now (%s)" % e
        return True, ""

    def start(self, pool_mb: int) -> bool:
        if self.phase != "idle" or not self.gm.is_safe():
            return False
        S, m = self.S, self.m
        self._install()
        self.pool = int(pool_mb)
        self._party0, self._box0 = self._party_count(), self._box_count()
        if self.game_id == "yellow":
            m[S.CUR_OPPONENT] = self.species
            m[S.CUR_ENEMY_LEVEL] = self.level
            m[S.BATTLE_TYPE] = 0
        else:
            m[S.TEMP_WILD_SPECIES] = self.species
            m[S.CUR_PARTY_LEVEL] = self.level
            m[S.BATTLE_SCRIPT_FLAGS] = 0
            m[S.BATTLE_TYPE] = 0                         # BATTLETYPE_NORMAL
            bank, addr = S.WILD_BATTLE_SCRIPT
            m[S.REENTRY_BANK] = bank
            m[S.REENTRY_ADDR] = addr & 0xFF
            m[S.REENTRY_ADDR + 1] = addr >> 8
            m[S.REENTRY_FLAG] = 1
        self.phase = "starting"
        self._busy = True
        self._start_frame = self.frame
        self._note("start %s Lv%d pool %d" % (self.boss.get("species"), self.level, self.pool))
        return True

    def step(self) -> list:
        """Call once per emulated frame; returns the new events."""
        self.frame += 1
        if self.phase in ("idle", "over"):
            return self._drain()
        if self.phase == "starting" and self.frame - self._start_frame > 60 * 30:
            self._finish("error")                      # the battle never started (should not happen)
            return self._drain()
        if self.auto:
            self._autopilot()
        self._check_end()
        return self._drain()

    def set_pool(self, pool_mb: int, dealt_mb: int | None = None):
        """The group pool from the server. dealt_mb = this player's total the server had counted when it sent
        the pool: damage we reported after that is still in flight and taken off here."""
        v = int(pool_mb)
        if dealt_mb is not None:
            v -= max(0, self.dealt - int(dealt_mb))
        self.pool = max(0, v)

    def group_down(self, catch_chance: float):
        if self.phase not in ("fight", "starting"):
            return
        self.catch_success = self.rng.random() < float(catch_chance)
        self.phase = "down"
        self._apply_down()
        self._note("group down, catch %s" % self.catch_success)

    def flee(self):
        if self.phase in ("over", "idle"):
            return
        self.flee_asked = True
        if self.phase == "starting":
            self.phase = "fleeing"
            return
        self._start_flee()

    def auto_pick(self):
        if self.decision is not None and self.phase in ("fight", "down", "fleeing"):
            self.auto = True
            self._menu_seq = 0
            self._next_press = 0

    def taking_over(self) -> bool:
        """True while YellowLink drives the game: the battle is being started, or auto_pick drives the buttons (the
        app should drop the player's own input then)."""
        return self.auto or self.phase == "starting"

    def busy(self) -> bool:
        return self._busy

    def dealt_mb(self) -> int:
        return self.dealt

    def remove(self):
        for bank, addr in self._installed:
            try:
                self.pyboy.hook_deregister(bank, addr)
            except Exception:  # noqa: BLE001
                pass
        self._installed = []

    # ------------------------------------------------------------------------------------------------------------
    # hooks
    def _install(self):
        if self._installed:
            return
        S, mem = self.S, self.pyboy.memory
        hooks = dict(S.HOOKS)
        sites = []
        if self.game_id == "crystal":
            b, a, sig = S.DBOND_SIG
            if (mem[b, a], mem[b, a + 1]) != sig:
                raise RuntimeError("raid: unexpected ROM code (Destiny Bond check)")
            sites = self._guard_sites()
        for name, (bank, addr, opcode) in hooks.items():
            if opcode is not None and not _ok_opcode(mem, bank, addr, opcode):
                raise RuntimeError("raid: unexpected ROM code at %s (%02X:%04X)" % (name, bank, addr))
        try:
            for name, (bank, addr, _op) in hooks.items():
                handler = self._on_guard if name.startswith("guard") else getattr(self, "_on_" + name)
                self.pyboy.hook_register(bank, addr, self._safe(handler), None)
                self._installed.append((bank, addr))
            for bank, addr in sites:
                if (bank, addr) in self._installed:
                    continue
                self.pyboy.hook_register(bank, addr, self._safe(self._on_guard), None)
                self._installed.append((bank, addr))
            self.pyboy.hook_register(*VBLANK, self._safe(self._on_vblank), None)
            self._installed.append(VBLANK)
        except Exception:
            self.remove()
            raise

    def _guard_sites(self):
        """Every `ld hl, wEnemyMonHP / ld a, [hli] / or [hl]` (an HP-0 test) in Crystal's battle bank 0F."""
        mem = self.pyboy.memory
        pat = self.S.GUARD_PATTERN
        out = []
        data = bytes(mem[0x0F, 0x4000:0x7FFF]) + bytes([mem[0x0F, 0x7FFF]])
        i = data.find(pat)
        while i >= 0:
            out.append((0x0F, 0x4000 + i))
            i = data.find(pat, i + 1)
        return out

    def _safe(self, fn):
        def wrapped(_ctx):
            try:
                fn()
            except Exception:  # noqa: BLE001 - a hook must never kill the emulator
                log.exception("raid hook %s failed", fn.__name__)
        return wrapped

    def _in_raid_battle(self) -> bool:
        return self.phase in ("fight", "down", "fleeing") and self.m[self.S.IN_BATTLE] == 1

    def _guarding(self) -> bool:
        return self._in_raid_battle()

    # --- battle set-up
    def _on_init(self):
        S, m = self.S, self.m
        if self.phase not in ("starting", "fleeing", "down") or m[S.IN_BATTLE] != 1 \
                or m[S.ENEMY_SPECIES] != self.species or self.max_hp:
            return
        self.max_hp = m.r16(S.ENEMY_MAX_HP)
        if self.game_id == "crystal":
            self.real_pp = [m[S.WILD_MON_PP + i] for i in range(4)]
        self._set_moves(self.moves)
        if self.shiny:
            m[S.ENEMY_DVS], m[S.ENEMY_DVS + 1] = SHINY_DVS
        hp = self.meter.sync(self.pool, self.max_hp)
        m.w16(S.ENEMY_HP, max(1, hp))
        self._was_in_battle = True
        if self.phase == "fleeing":
            self._start_flee()
        elif self.phase == "down":                  # the group took it down while this battle was starting
            self._apply_down()
        else:
            self.phase = "fight"
        self._note("override: max hp %d, segment %d" % (self.max_hp, hp))

    def _set_moves(self, moves):
        S, m = self.S, self.m
        self.cur_moves = list(moves)
        for i in range(4):
            mv = moves[i] if i < len(moves) else 0
            pp = self.rom.move_pp(mv) if mv else 0
            m[S.ENEMY_MOVES + i] = mv
            m[S.ENEMY_PP + i] = pp
            if self.game_id == "crystal":
                m[S.WILD_MON_PP + i] = pp          # Crystal spends the wild mon's PP here (never change wWildMonMoves)

    # --- turn start: sync to the group pool
    def _on_turn(self):
        S, m = self.S, self.m
        if not self._in_raid_battle():
            return
        if self.decision and self.decision["what"] == "next_mon":
            self._close_decision()
        if self.phase == "fight":
            hp = self.meter.sync(self.pool, self.max_hp)
            if hp > 0:
                m.w16(S.ENEMY_HP, hp)
            else:                                   # our pool estimate is empty: wait at 1 HP for the server
                m.w16(S.ENEMY_HP, 1)
                self.meter.done = True
            self._set_moves(self.moves)
        elif self.phase == "down":
            self._apply_down()
        elif self.phase == "fleeing":
            self._apply_flee()

    def _apply_down(self):
        S, m = self.S, self.m
        if m[S.IN_BATTLE] != 1:
            return
        m.w16(S.ENEMY_HP, 1)
        m[S.ENEMY_STATUS] = SLP
        self._set_moves([SPLASH])
        if self.game_id == "yellow":
            m[S.ENEMY_CATCH_RATE] = 255
            m[S.ACTUAL_CATCH_RATE] = 255
        else:
            m[S.ENEMY_CATCH_RATE] = 255

    def _start_flee(self):
        self.phase = "fleeing"
        self._apply_flee()
        self._note("flee")

    def _apply_flee(self):
        S, m = self.S, self.m
        if m[S.IN_BATTLE] != 1:
            return
        m[S.ENEMY_STATUS] = 0                       # awake: a sleeping boss can't teleport
        self._set_moves([TELEPORT])
        if self.game_id == "yellow":
            m[S.CUR_ENEMY_LEVEL] = 100              # enemy Teleport works when its level >= the player's
            m[S.ENEMY_SELECTED_MOVE] = TELEPORT     # this very turn, if its move was already picked
        else:
            m[S.CUR_ENEMY_MOVE] = TELEPORT
            m[S.CUR_ENEMY_MOVE_NUM] = 0

    # --- shared HP safety
    def _on_vblank(self):
        self.frame_hw = getattr(self, "frame_hw", 0) + 1
        S, m = self.S, self.m
        inb = m[S.IN_BATTLE]
        if inb == 0xFF and self.phase in ("fight", "down", "fleeing"):
            self.blacked_out = True
        if not self._in_raid_battle() or not self.max_hp:
            return
        hp = m.r16(S.ENEMY_HP)
        zeroed = False
        if hp == 0:
            m.w16(S.ENEMY_HP, 1)                    # never faints locally while the raid runs
            zeroed = True
            self._note("clamped to 1")
        if self.phase == "fight":
            self._observe(hp, zeroed)

    def _on_guard(self):
        S, m = self.S, self.m
        if not self._guarding():
            return
        if m.r16(S.ENEMY_HP) == 0:
            m.w16(S.ENEMY_HP, 1)
            self._note("guard: HP 0 -> 1")
            if self.phase == "fight":
                self._observe(0, True)

    def _observe(self, hp, zeroed):
        drop, mb = self.meter.observe(hp, zeroed)
        if drop or mb:
            self.dealt += mb
            self.pool = max(0, self.pool - mb)
            self.events.append({"type": "damage", "hp": drop, "of": self.max_hp, "mb": mb})

    def _on_perish(self):
        # Perish Song's count ran out for the boss: HandlePerishSong.kill_enemy sets its HP to 0; the guards on the
        # faint tests right after (CheckFaint_* -> HasEnemyFainted / HandlePlayerMonFaint) put it back to 1 and
        # count the boss's current bar for the player - like a one-hit KO (DESIGN A.1), the same as Platinum.
        if self._guarding():
            self._note("perish song: takes the boss's current bar")

    def _on_dbond(self):
        # Destiny Bond: the boss knocked out a Destiny Bond user -> the game sets the boss's HP to 0 here; the guards
        # / the frame watch keep it at 1 and count the current bar (see _on_perish).
        rf = self.pyboy.register_file
        if self._guarding() and self.pyboy.memory[self.S.BATTLE_TURN] != 0 and not rf.F & 0x80:
            self._note("destiny bond: takes the boss's current bar")

    def _on_enemy_fainted(self):
        if self.phase in ("fight", "down", "fleeing"):
            self.escaped = True
            self._note("the boss fainted in this game (escaped)")

    def _on_blackout(self):
        if self.phase in ("fight", "down", "fleeing"):
            self.blacked_out = True
            self._note("blackout")

    # --- balls
    def _on_ball(self):
        S, m = self.S, self.m
        if self.m[S.IN_BATTLE] != 1 or self.phase not in ("fight", "down", "fleeing"):
            return
        if self.phase == "down" and self.throws == 0 and self.catch_success is not None:
            self.throws = 1
            if self.game_id == "crystal":
                for i in range(4):                     # the catch copies wWildMonMoves/PP: its real PP back
                    m[S.WILD_MON_PP + i] = self.real_pp[i]
            self._note("ball thrown (forced %s)" % ("catch" if self.catch_success else "miss"))
            self.ui = None
            self._close_decision()                    # the player has acted: the timer stops here
            return
        self.pyboy.register_file.PC = S.NOT_TIME[1]    # "This isn't the time to use that!": no turn, ball kept
        self._note("ball blocked")

    def _on_ball_roll(self):
        if self.phase != "down" or self.throws != 1 or self.catch_success is None:
            return
        rf = self.pyboy.register_file
        if self.game_id == "yellow":
            if self.catch_success:
                rf.PC = self.S.CAPTURED[1]
            else:
                rf.B = self.S.MISS_ANIM
                rf.PC = self.S.SET_ANIM[1]
                self._on_miss()
        elif self.catch_success:
            rf.PC = self.S.CATCH_OK[1]

    def _on_ball_result(self):
        # Crystal .catch_without_fail (also the Master Ball's way in): a forced miss goes to .fail_to_catch, a = 0
        if self.phase != "down" or self.throws != 1 or self.catch_success is not False:
            return
        rf = self.pyboy.register_file
        rf.A = 0
        rf.PC = self.S.CATCH_FAIL[1]
        self._on_miss()

    def _on_miss(self):
        if self.missed:
            return
        self.missed = True
        self.events.append({"type": "missed"})
        self._start_flee()

    # --- waiting for the player (timer)
    def _open_decision(self, what):
        if self.decision is not None:
            return
        self.decision = {"what": what, "t": time.time(), "frame": self.frame}
        self.events.append({"type": "need_input", "what": what})

    def _close_decision(self):
        if self.decision is None:
            return
        self.decision = None
        self.auto = False
        self.events.append({"type": "input_done"})

    def _on_menu(self):
        if not self._in_raid_battle():
            return
        if self.phase == "fight" and not getattr(self, "_started", False):
            self._started = True
            self.events.append({"type": "started"})
        if self.decision and self.decision["what"] == "next_mon":
            self._close_decision()
        self.ui = "menu"
        self._menu_seq = 0
        self._open_decision("menu")

    def _on_moves(self):
        if self._in_raid_battle():
            self.ui = "moves"

    def _on_bag(self):
        if self._in_raid_battle():
            self.ui = "bag"

    def _on_party(self):
        if self._in_raid_battle():
            self.ui = "party"

    def _on_next_mon(self):
        if not self._in_raid_battle():
            return
        self._close_decision()
        self.ui = "next_mon"
        self._open_decision("next_mon")

    def _on_party_faint(self):
        if self._in_raid_battle():
            self.ui = "party_faint"
            self._open_decision("next_mon")

    def _on_locked(self):
        if not self._in_raid_battle():
            return
        if self.ui == "moves":
            self.last_slot = self._cur_move_slot_after_pick()
        self.ui = None
        self._close_decision()

    def _cur_move_slot_after_pick(self):
        S, m = self.S, self.m
        return m[S.MOVE_LIST_INDEX] if self.game_id == "yellow" else m[S.CUR_MOVE_NUM]

    # --- auto-pick (the 10-s timer ran out)
    def _press(self, btn, gap=16, hold=3):
        self.pyboy.button(btn, hold)
        self._next_press = self.frame + gap

    def _autopilot(self):
        if self.decision is None or not self._in_raid_battle():
            self.auto = False
            return
        if self.frame < self._next_press or self.frame - self._ui_since < 24:
            return                                  # let the menu settle (presses before it reads input are lost)
        S, m = self.S, self.m
        ui = self.ui
        throw = self.phase == "down" and self.throws == 0 and self.catch_success is not None
        if ui == "menu":
            # cursor to FIGHT (the menu remembers ITEM/PACK/RUN), then A; in the catch phase: ITEM/PACK (below FIGHT)
            seq = ("up", "left", "down", "a") if throw else ("up", "left", "a")
            if self._menu_seq >= len(seq):
                self._menu_seq = 0
            self._press(seq[self._menu_seq], 20)
            self._menu_seq += 1
        elif ui == "bag" and throw:
            self._autothrow()
        elif ui == "moves":
            target = self.choose_slot()
            cur = (m[S.CUR_MENU_ITEM] - 1) if self.game_id == "yellow" else (m[S.MENU_CURSOR_Y] - 1)
            if cur < target:
                self._press("down")
            elif cur > target:
                self._press("up")
            else:
                self._press("a", 30)
        elif ui == "next_mon":
            yes = (m[S.CUR_MENU_ITEM] == 0) if self.game_id == "yellow" else (m[S.MENU_CURSOR_Y] == 1)
            self._press("a" if yes else "up", 30)
        elif ui == "party_faint":
            target = self.first_healthy_slot()
            cur = m[S.CUR_MENU_ITEM] if self.game_id == "yellow" else m[S.MENU_CURSOR_Y] - 1
            if target is None or cur == target:
                self._press("a", 30)
            else:
                self._press("down" if cur < target else "up")
        else:                                         # bag / party list / anything else: back out
            self._press("b", 20)

    # cheapest first, the Master Ball last (Yellow: POKé, GREAT, ULTRA, SAFARI, MASTER; Crystal: + Kurt's balls)
    BALL_ORDER = {"yellow": (4, 3, 2, 8, 1),
                  "crystal": (5, 4, 2, 0x9D, 0x9F, 0xA0, 0xA1, 0xA4, 0xA5, 0xA6, 0xB1, 1)}

    def ball_choice(self, items):
        """Index in `items` ([item ids] as the bag/pocket lists them) of the ball to throw: the cheapest one (the
        result is decided by the raid anyway - never waste a Master Ball if there is anything else)."""
        order = self.BALL_ORDER[self.game_id]
        best = None
        for i, it in enumerate(items):
            if it in order and (best is None or order.index(it) < order.index(items[best])):
                best = i
        return best

    def _autothrow(self):
        """Catch phase, the timer ran out: throw a ball from the bag (Yellow list / Crystal balls pocket)."""
        S, m = self.S, self.m
        if self.game_id == "yellow":
            items = [m[0xD31D + 2 * i] for i in range(min(m[0xD31C], 20))]       # wBagItems
            target = self.ball_choice(items)
            if target is None:
                self._press("b", 20)
                return
            cur = m[0xCC36] + m[S.CUR_MENU_ITEM]                                # wListScrollOffset + cursor
            if cur < target:
                self._press("down")
            elif cur > target:
                self._press("up")
            else:
                self._press("a", 40)
            return
        pocket = m[0xCF65]                                                     # wCurPocket: 1 = balls
        if pocket != 1:
            self._press("right" if pocket == 0 else "left", 24, hold=8)    # the pack only sees longer presses
            return
        items = [m[0xD8D8 + 2 * i] for i in range(min(m[0xD8D7], 12))]          # wBalls
        target = self.ball_choice(items)
        if target is None:
            self._press("b", 20)
            return
        cur = m[0xD0E4] + m[S.MENU_CURSOR_Y] - 1                                # wMenuScrollPosition + cursor
        if getattr(self, "_use_menu", False):
            self._use_menu = False
            self._press("a", 40)                                               # USE
        elif cur < target:
            self._press("down")
        elif cur > target:
            self._press("up")
        else:
            self._use_menu = True
            self._press("a", 30)

    def choose_slot(self) -> int:
        """Decision 6: the move used last turn if it still has PP (and isn't disabled), else the strongest."""
        usable = self.usable_slots()
        if not usable:
            return 0
        if self.last_slot in usable:
            return self.last_slot
        return max(usable, key=lambda s: (self.move_score(s), -s))

    def usable_slots(self) -> list:
        S, m = self.S, self.m
        out = []
        disabled = None
        if self.game_id == "yellow":
            d = m[S.DISABLED_MOVE] >> 4
            disabled = d - 1 if d else None
        else:
            dm = m[S.DISABLED_MOVE]
            disabled = next((i for i in range(4) if dm and m[S.BATTLE_MON_MOVES + i] == dm), None)
        for i in range(4):
            if m[S.BATTLE_MON_MOVES + i] and m[S.BATTLE_MON_PP + i] & 0x3F and i != disabled:
                out.append(i)
        return out

    def move_score(self, slot) -> float:
        S, m = self.S, self.m
        mv = m[S.BATTLE_MON_MOVES + slot]
        if not mv:
            return -1
        o = S.MOVES_ROM + (mv - 1) * S.MOVE_SIZE
        power, mtype = self.rom.rom[o + 2], self.rom.rom[o + 3]
        if power <= 1:                               # status moves (and fixed-damage ones count as weak)
            return power * 0.1
        mine = {m[S.BATTLE_MON_TYPES], m[S.BATTLE_MON_TYPES + 1]}
        stab = 1.5 if mtype in mine else 1.0
        eff = 1.0
        for t in {m[S.ENEMY_TYPES], m[S.ENEMY_TYPES + 1]}:
            eff *= self._type_mult(mtype, t)
        return power * stab * eff

    def _type_mult(self, atk, dfn) -> float:
        chart = getattr(self, "_chart", None)
        if chart is None:
            chart = {}
            rom, o = self.rom.rom, self.S.TYPE_CHART
            while rom[o] != 0xFF and len(chart) < 200:
                if rom[o] == 0xFE:                   # Crystal: Foresight separator
                    o += 1
                    continue
                chart.setdefault((rom[o], rom[o + 1]), rom[o + 2] / 10)
                o += 3
            self._chart = chart
        return chart.get((atk, dfn), 1.0)

    def first_healthy_slot(self):
        S, m = self.S, self.m
        n = min(self._party_count(), 6)
        for i in range(n):
            o = S.PARTY_MONS + i * S.PARTY_MON_SIZE + (1 if self.game_id == "yellow" else 34)
            if m.r16(o) > 0:
                if self.game_id == "crystal" and m[0xDCD8 + i] == 0xFD:      # egg
                    continue
                return i
        return None

    # --- end of the battle
    def _check_end(self):
        S, m = self.S, self.m
        inb = m[S.IN_BATTLE]
        if self.phase == "starting":
            return
        if inb == 0xFF:
            self.blacked_out = True
        if inb not in (0, 0xFF) or not self._was_in_battle:
            if inb == 1:
                self._was_in_battle = True
            return
        if self.blacked_out:
            self._finish("blackout")
            return
        # battle over: wait until the overworld is back (catch: the Pokédex/nickname screens come first)
        if not self.gm.is_safe():
            self._end_wait += 1
            if self._end_wait < 60 * 120:
                return
        if self.phase == "down" and self.throws and self.catch_success:
            key = self._caught_key()
            self.events.append({"type": "caught", "key": key, "shiny": self.shiny})
            self._finish("caught")
        elif self.missed:
            self.events.append({"type": "fled"})
            self._finish("missed")
        elif self.phase == "fleeing" or self.flee_asked:
            self.events.append({"type": "fled"})
            self._finish("fled")
        elif self.escaped:
            self._finish("escaped")
        else:
            self.events.append({"type": "left"})
            self._finish("left")

    def _caught_key(self):
        try:
            if self.game_id == "yellow":
                from . import game as G
            else:
                from . import crystal as G
            if self._party_count() > self._party0:
                p = self.gm.party()[-1]
                rec = p.box_record() if hasattr(p, "box_record") else p.data
                return G.mon_key(rec)
            recs = self.gm.current_box_records()
            if recs and self._box_count() > self._box0:
                return G.mon_key(recs[-1])
        except Exception:  # noqa: BLE001
            log.exception("raid: caught key")
        return None

    def _finish(self, outcome):
        if self.phase == "over":
            return
        if self.blacked_out and outcome not in ("blackout",):
            outcome = "blackout"
        if outcome == "blackout":
            self.events.append({"type": "blackout"})
        self.phase = "over"
        self.outcome = outcome
        self.auto = False
        self.decision = None
        self.remove()
        self._busy = False
        self.events.append({"type": "ended", "outcome": outcome})
        self._note("ended: %s" % outcome)

    # --- helpers
    def _party_count(self):
        return self.m[self.S.PARTY_COUNT]

    def _box_count(self):
        try:
            if self.game_id == "yellow":
                return self.m[self.S.BOX_COUNT]
            return len(self.gm.current_box_records())
        except Exception:  # noqa: BLE001
            return 0

    def _drain(self):
        ev, self.events = self.events, []
        return ev

    def _note(self, what):
        self.log.append((self.frame, what))
        if len(self.log) > 400:
            del self.log[:100]
        log.debug("raid: %s", what)


# --------------------------------------------------------------------------------------------------------------
# The den (drawn by us, interaction through input filtering)
# --------------------------------------------------------------------------------------------------------------
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
PLAYER_SCREEN_X, PLAYER_SCREEN_Y = 64, 60      # the player sprite's top-left on the 160x144 screen (4 px up)


class Den:
    """The raid den on a Game Boy map square (spot = {map, x, y, ...}; map = the game's own map id)."""

    def __init__(self, spot: dict, game_id: str):
        self.spot = dict(spot)
        self.game_id = game_id
        self.map = int(spot["map"])
        self.x, self.y = int(spot["x"]), int(spot["y"])
        self._bump = None          # (time, dx, dy) the player pushed against the den

    def on_map(self, gm) -> bool:
        try:
            return gm.in_game() and gm.map_id() == self.map
        except Exception:  # noqa: BLE001
            return False

    def _square(self, gm):
        """The square the player stands on - or will, at the end of the current step."""
        p = gm.position()
        x, y = p["x"], p["y"]
        if self.game_id == "yellow" and gm.walking():
            # Yellow updates wXCoord/wYCoord when the step ends (MEMORY.md): add the walking direction
            m = gm.m
            from . import game as yellow
            xv, yv = m[yellow.PLAYER_XVEC], m[yellow.PLAYER_YVEC]
            x += (xv - 256 if xv > 127 else xv)
            y += (yv - 256 if yv > 127 else yv)
        return x, y

    def offset(self, gm, neighbor_shift=None):
        """(dx, dy) from the player to the den in squares, or None. neighbor_shift(map_id) -> (sx, sy) squares
        of a connected map relative to the current one (the app's Yellow helper), for dens on the next map."""
        if not gm.in_game():
            return None
        p = gm.position()
        if gm.map_id() == self.map:
            return self.x - p["x"], self.y - p["y"]
        if neighbor_shift is not None:
            sh = neighbor_shift(self.map)
            if sh is not None:
                return self.x + sh[0] - p["x"], self.y + sh[1] - p["y"]
        return None

    def near(self, gm) -> bool:
        if not self.on_map(gm) or gm.walking():
            return False
        x, y = self._square(gm)
        return abs(self.x - x) + abs(self.y - y) == 1

    def facing_den(self, gm) -> bool:
        x, y = self._square(gm)
        f = gm.position()["facing"]
        vec = {0x0: (0, 1), 0x4: (0, -1), 0x8: (-1, 0), 0xC: (1, 0)}.get(f & 0xC)
        return vec is not None and (x + vec[0], y + vec[1]) == (self.x, self.y)

    def blocks(self, gm, direction: str) -> bool:
        """True = drop this d-pad press: it would step onto the den (also while walking/biking up to it)."""
        d = DIRS.get(direction)
        if d is None or not self.on_map(gm):
            return False
        x, y = self._square(gm)
        if (x + d[0], y + d[1]) != (self.x, self.y):
            return False
        self._bump = (time.time(), d)
        return True

    def pressed_a(self, gm) -> bool:
        """On an A press: True = it's for the den (swallow it, ask "Join the raid?")."""
        try:
            if not gm.is_safe() or not self.near(gm):
                return False
        except Exception:  # noqa: BLE001
            return False
        if self.facing_den(gm):
            return True
        x, y = self._square(gm)
        b = self._bump
        return bool(b and time.time() - b[0] < 3 and (x + b[1][0], y + b[1][1]) == (self.x, self.y))

    def draw(self, surface, gm, *, off=(0, 0), mask=None, neighbor_shift=None, t=None):
        """Draw the den (or the edge arrow) onto the 160x144 game picture at `off` (wide view: the centre's
        offset). mask = the app's 18x20 menu mask (the den stands behind menus/text boxes). Returns True if the
        den itself is visible."""
        try:
            if not gm.in_game() or gm.in_battle():
                return False
            d = self.offset(gm, neighbor_shift)
        except Exception:  # noqa: BLE001
            return False
        if d is None:
            return False
        t = time.time() if t is None else t
        p = gm.position()
        sx = PLAYER_SCREEN_X + (self.x * 16 - p["px"]) + (d[0] - (self.x - p["x"])) * 16
        sy = PLAYER_SCREEN_Y + 4 + (self.y * 16 - p["py"]) + (d[1] - (self.y - p["y"])) * 16 - 8
        ox, oy = off
        if -16 < sx < 160 and -24 < sy < 144:
            img = raid_art.gb_den(t)
            if mask is not None:
                img = img.copy()
                for cy in range(3):
                    for cx in range(2):
                        r, c = (sy + cy * 8) // 8, (sx + cx * 8) // 8
                        if 0 <= r < 18 and 0 <= c < 20 and mask[r][c]:
                            img.fill((0, 0, 0, 0), (cx * 8, cy * 8, 8, 8))
            surface.blit(img, (sx + ox, sy + oy))
            return True
        if mask is None or not _mostly_covered(mask):
            raid_art.arrow(surface, (ox, oy, 160, 144), d[0], d[1], abs(d[0]) + abs(d[1]), t)
        return False


def _mostly_covered(mask) -> bool:
    try:
        return sum(sum(1 for v in row if v) for row in mask) > 0.85 * 18 * 20
    except Exception:  # noqa: BLE001
        return False
