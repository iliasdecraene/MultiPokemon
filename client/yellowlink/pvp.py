"""Live player-vs-player battles using the game's own link-battle engine.

See SPEC.md section 4. The game believes it's talking over a link cable; we intercept
Serial_ExchangeNybble and carry each turn's choice over the network instead.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import game, text
from .game import GameMemory, full_heal

log = logging.getLogger("yellowlink.pvp")

# Hook addresses (Pokémon Yellow USA/Europe)
HOOK_EXCHANGE_START = (0x0F, 0x5777)  # LinkBattleExchangeData
HOOK_EXCHANGE_NYBBLE = (0x00, 0x211F)  # Serial_ExchangeNybble
HOOK_SEND_ZERO = (0x00, 0x2149)        # Serial_SendZeroByte

# RAM
SEND_NYBBLE = 0xCC42
RECV_NYBBLE = 0xCC3E
RNS_INDEX = 0xCCDE
RNS_LIST = 0xD147
LINK_ENEMY_NAME = 0xD886
LINK_STATE = 0xD12A
SERIAL_STATUS = 0xFFAA
BATTLE_MON_HP = 0xD014
ENEMY_MON_HP = 0xCFE5
LINK_STATE_BATTLING = 4
OPP_RIVAL1 = 200 + 0x19


@dataclass
class LiveBattle:
    battle_id: str
    opponent_uid: str
    opponent_name: str
    master: bool
    phase: str = "preparing"   # preparing | ready | starting | running | ending
    snapshot: bytes = b""
    started: float = field(default_factory=time.time)
    exchange: int = 0
    sent: dict = field(default_factory=dict)
    inbox: dict = field(default_factory=dict)
    my_checks: dict = field(default_factory=dict)
    their_checks: dict = field(default_factory=dict)
    result: int | None = None
    end_frames: int = 0
    desync_logged: bool = False


class LinkHooks:
    """Installed once per emulator; does nothing unless `session` is a running LiveBattle."""

    def __init__(self, pyboy, send_move):
        self.pyboy = pyboy
        self.m = pyboy.memory
        self.send_move = send_move
        self.session: LiveBattle | None = None
        pyboy.hook_register(*HOOK_EXCHANGE_START, self._safe(self.on_exchange_start), None)
        pyboy.hook_register(*HOOK_EXCHANGE_NYBBLE, self._safe(self.on_nybble), None)
        pyboy.hook_register(*HOOK_SEND_ZERO, self._safe(self.on_zero), None)

    def _safe(self, fn):
        def wrapped(ctx):
            try:
                fn(ctx)
            except Exception:  # noqa: BLE001 - never let a hook kill the emulator
                log.exception("link hook failed")
        return wrapped

    def _active(self):
        s = self.session
        return s is not None and s.phase in ("starting", "running") and self.m[LINK_STATE] == LINK_STATE_BATTLING

    def _ret(self):
        rf = self.pyboy.register_file
        sp = rf.SP
        rf.PC = self.m[sp] | (self.m[sp + 1] << 8)
        rf.SP = (sp + 2) & 0xFFFF

    def on_exchange_start(self, _):
        if self._active():
            self.session.exchange += 1

    def on_nybble(self, _):
        if not self._active():
            return
        s = self.session
        n = s.exchange
        if n not in s.sent:
            v = self.m[SEND_NYBBLE] & 0x0F
            s.sent[n] = v
            check = self.check_string()
            s.my_checks[n] = check
            self.send_move(s, n, v, check)
            log.info("pvp exchange %d: sent %d (%s)", n, v, check)
        if n in s.inbox:
            self.m[RECV_NYBBLE] = s.inbox[n] & 0x0F
        self._ret()

    def on_zero(self, _):
        if self._active():
            self._ret()

    def check_string(self) -> str:
        m = self.m
        my_hp = (m[BATTLE_MON_HP] << 8) | m[BATTLE_MON_HP + 1]
        en_hp = (m[ENEMY_MON_HP] << 8) | m[ENEMY_MON_HP + 1]
        return f"{my_hp}/{en_hp}"


def compare_checks(s: LiveBattle, n: int):
    """Opponent's 'my/enemy' should mirror ours. Log once if it doesn't."""
    mine, theirs = s.my_checks.get(n), s.their_checks.get(n)
    if not mine or not theirs or s.desync_logged:
        return
    a = mine.split("/")
    b = theirs.split("/")
    if len(a) == 2 and len(b) == 2 and (a[0] != b[1] or a[1] != b[0]):
        s.desync_logged = True
        log.warning("pvp desync suspected at exchange %d: mine %s theirs %s", n, mine, theirs)


def setup_battle(g: GameMemory, s: LiveBattle, opp_mons: list, opp_trainer: str, rns: list):
    """Write everything the link battle engine needs, then trigger the battle from the overworld."""
    m = g.m
    mons = [full_heal(x, g.rom) for x in opp_mons[:6]]
    m[game.ENEMY_PARTY_COUNT] = len(mons)
    for i in range(7):
        m[game.ENEMY_PARTY_SPECIES + i] = mons[i].species if i < len(mons) else 0xFF
    for i, mon in enumerate(mons):
        g.write(game.ENEMY_MONS + i * game.MON_SIZE, mon.data)
        g.write(game.ENEMY_OT + i * game.NAME_SIZE, mon.ot)
        g.write(game.ENEMY_NICKS + i * game.NAME_SIZE, mon.nick)
    name = (opp_trainer or s.opponent_name or "FRIEND").upper()
    g.write(LINK_ENEMY_NAME, text.encode(name, game.NAME_SIZE))
    g.write(RNS_LIST, bytes(int(x) & 0xFF for x in rns[:10]))
    m[RNS_INDEX] = 0
    m[SERIAL_STATUS] = 2 if s.master else 1
    m[LINK_STATE] = LINK_STATE_BATTLING
    m[game.LONE_ATTACK_NO] = 0
    m[game.CUR_OPPONENT] = OPP_RIVAL1


def clear_link_state(g: GameMemory):
    g.m[LINK_STATE] = 0
