"""Exp Share All (modern Gen 6+ rule) on the real Pokémon Yellow and Crystal ROMs (PyBoy, headless).

For each game, real battles are played from the same save state with the hooks absent ("none"), installed but
disabled ("off") and enabled ("on"), and the experience every party mon received is compared with the game's own
formula (expshare_gb.exp_award):
  wild     one participant A; B, C sit out; D fainted; E traded (x1.5) sits out (Crystal: B holds a Lucky Egg,
           F is an egg)        on: A = solo, others floor(own solo / 2), D/F 0;   off: original;   off == none
  switch   A then B fought (one switch, B lands the KO)  on: A and B both the full solo amount (no split)
  trainer  the x1.5 trainer path, same checks as wild
  levelup  non-participants crossing a level threshold: level, stats (recomputed formula), HP, "grew to level",
           a level-up move learned, the forget-a-move prompt (answered YES, first move forgotten), evolution after
           the battle (Yellow Rattata 19 -> Raticate, Crystal Sentret 14 -> Furret)
  expall   Yellow EXP.ALL in the bag / Crystal Exp. Share held by A and C:
           on: exactly the same gains as without the item (no stacking);  off: the original item formula; off == none
  remove   handle.remove() puts the original ROM bytes back
"off == none" compares the whole of WRAM after the battle (Crystal: WRAM0 + bank 1).

RAM prepared for the tests (documented, before the battle or at its battle menu, never during the award):
  the party (structs built from the fixture's own mons: level/exp/stats/moves/HP/OT ID/held item/egg flag),
  the bag (EXP.ALL), enemy HP = 1 at the battle menu (so one hit ends it), enemy party count = 1 in trainer battles.
Needs dev/states/{yellow.gb, soullink_party3.state, soullink_trainer_battle.state, crystal.gbc,
crystal_sl_wild.state, crystal_sl_trainer.state} (made by dev/e2e/soullink_mem_yellow.py and
dev/crystal/soullink_verify.py). Prints PASS/FAIL per check and ends with ALL PASS.
Run: python dev/e2e/expshare_gb.py [yellow|crystal]
"""
import dataclasses
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO, "dev", "crystal"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import soullink_mem_yellow as SY  # noqa: E402
import pc_actions as pa  # noqa: E402
from yellowlink import crystal, game  # noqa: E402
from yellowlink.expshare_gb import CrystalExpShare, YellowExpShare, exp_award, stat_exp_after  # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


def info(msg):
    print("     " + msg, flush=True)


# =================================================================================================================
# Game drivers: same interface for both games
# =================================================================================================================
class Yellow:
    name = "Yellow"
    MON = 44
    EXP, STATEXP, LEVEL, HP, MAXHP, OTID, MOVES, PP = 14, 17, 33, 1, 34, 12, 8, 29
    EXP_ALL = 0x4B
    SAND_ATTACK, HYPER_FANG, TACKLE, TAIL_WHIP, GROWL, LEER = 28, 158, 33, 39, 45, 43
    RATICATE_DEX = 20
    install = staticmethod(game.install_exp_share)
    hook_cls = YellowExpShare

    def __init__(self, case):
        self.rd = SY.RD
        if case == "trainer":
            self.pb, self.g = SY.emu("soullink_trainer_battle.state")      # frame 0 of a trainer battle
        else:
            self.pb, self.g = SY.emu("soullink_party3.state")              # Route 1 grass, party of 3
        self.m = self.pb.memory
        self.case = case

    # --- party ---------------------------------------------------------------------------------------------
    def templates(self):
        """(lead, pidgey, rattata) from soullink_party3.state."""
        pb, g = SY.emu("soullink_party3.state")
        mons = g.party()
        pb.stop(save=False)
        return mons

    def build(self, mon, level=None, exp_minus=None, moves=None, hp=None, ot_id=None, item=None, egg=False):
        d = bytearray(mon.data)
        if level is not None:
            d[3] = d[33] = level
            growth = self.rd.growth_rate(d[0])
            exp = game.exp_for_level(growth, level) if exp_minus is None else \
                game.exp_for_level(growth, level + 1) - exp_minus
            d[14:17] = exp.to_bytes(3, "big")
        if moves is not None:
            mv = list(moves) + [0] * (4 - len(moves))
            d[8:12] = bytes(mv)
            for i, mm in enumerate(mv):
                d[29 + i] = self.rd.move_pp(mm) if mm else 0
        if ot_id is not None:
            d[12:14] = ot_id.to_bytes(2, "big")
        st = game.calc_stats(bytes(d), self.rd.base_stats(d[0]))
        for i, v in enumerate(st):
            d[34 + 2 * i:36 + 2 * i] = v.to_bytes(2, "big")
        d[1:3] = (st[0] if hp is None else hp).to_bytes(2, "big")
        return game.Mon(bytes(d), mon.ot, mon.nick)

    def set_party(self, mons):
        m = self.m
        m[game.PARTY_COUNT] = len(mons)
        for i, mn in enumerate(mons):
            self.g.write_party_slot(i, mn)
        m[game.PARTY_SPECIES + len(mons)] = 0xFF

    def party(self):
        return [mn.data for mn in self.g.party()]

    def eggs(self):
        return set()

    def species_dex(self, d):
        return self.rd.dex_no(d[0])

    def set_bag(self, items):
        SY.set_bag(self.pb, items)

    def wram(self):
        return bytes(self.m[0xC000:0xE000])

    # --- battle --------------------------------------------------------------------------------------------
    def start(self):
        if self.case != "trainer":
            assert SY.step_until_battle(self.pb, ("up", "down")), "no wild battle"
        self.to_menu()
        if self.case == "trainer":
            self.m[game.ENEMY_PARTY_COUNT] = 1

    def to_menu(self):
        SY.advance_record(self.pb, SY.in_battle_menu, lambda: None)

    def enemy(self):
        m = self.m
        return dict(base_exp=m[0xD007], level=m[0xCFF2], stats=[m[0xD001 + i] for i in range(5)],
                    trainer=m[game.IS_IN_BATTLE] == 2, participants=m[0xD057],
                    player_id=(m[game.PLAYER_ID] << 8) | m[game.PLAYER_ID + 1])

    def switch_to(self, slot):
        SY.battle_menu_select(self.pb, 1)
        pa._advance_until(self.pb, lambda p: SY.has(p, "Choose a POKe") or SY.has(p, "SWITCH"), what="party")
        pa._move_cursor(self.pb, slot)
        pa._press(self.pb, "a")
        pa._advance_until(self.pb, lambda p: SY.has(p, "SWITCH"), what="switch menu")
        pa._move_cursor(self.pb, 0)
        pa._press(self.pb, "a")
        self.to_menu()

    def attack(self):
        m = self.m
        m[0xCFE5], m[0xCFE6] = 0, 1                       # wEnemyMonHP = 1
        SY.fight_move(self.pb, 1)                         # first move (the move menu is 1-based)

    def screen(self):
        return " ".join(pa.screen_lines(self.pb))

    def yes_no(self):
        return SY.has(self.pb, "YES", "NO") and not pa._prompt_arrow(self.pb)

    def in_battle(self):
        return self.m[game.IS_IN_BATTLE] != 0

    def idle(self):
        return self.m[game.IS_IN_BATTLE] == 0 and not (self.m[game.FONT_LOADED] & 1)

    def tick(self, n=1):
        for _ in range(n):
            self.pb.tick()

    def press(self, b):
        pa._press(self.pb, b)

    def stop(self):
        self.pb.stop(save=False)


class Crystal:
    name = "Crystal"
    MON = 48
    EXP, STATEXP, LEVEL, HP, MAXHP, OTID, MOVES, PP = 8, 11, 31, 34, 36, 6, 2, 23
    EXP_SHARE, LUCKY_EGG = 0x39, 0x7E
    SAND_ATTACK, QUICK_ATTACK, TACKLE, DEFENSE_CURL, GROWL, LEER = 28, 98, 33, 111, 45, 43
    FURRET_DEX = 162
    install = staticmethod(crystal.install_exp_share)
    hook_cls = CrystalExpShare

    def __init__(self, case):
        import soullink_verify as V
        self.V = V
        self.rd = V.rom_data()
        self.pb = V.load("crystal_sl_trainer.state" if case == "trainer" else "crystal_sl_wild.state")
        self.cm = crystal.CrystalMemory(self.pb.memory, self.rd)
        self.m = self.pb.memory
        self.case = case

    def templates(self):
        """(cyndaquil, pidgey, sentret) from crystal_sl_wild.state (the lead stays untouched: it is in battle)."""
        return self.cm.party()

    def build(self, mon, level=None, exp_minus=None, moves=None, hp=None, ot_id=None, item=None, egg=False):
        d = bytearray(mon.data)
        if level is not None:
            d[31] = level
            growth = self.rd.growth_rate(d[0])
            exp = crystal.exp_for_level(growth, level) if exp_minus is None else \
                crystal.exp_for_level(growth, level + 1) - exp_minus
            d[8:11] = exp.to_bytes(3, "big")
        if moves is not None:
            mv = list(moves) + [0] * (4 - len(moves))
            d[2:6] = bytes(mv)
            for i, mm in enumerate(mv):
                d[23 + i] = self.rd.move_pp(mm) if mm else 0
        if ot_id is not None:
            d[6:8] = ot_id.to_bytes(2, "big")
        if item is not None:
            d[1] = item
        st = crystal.calc_stats(bytes(d), self.rd.base_stats(d[0]))
        d[36:38] = st[0].to_bytes(2, "big")
        for i in range(5):
            d[38 + 2 * i:40 + 2 * i] = st[i + 1].to_bytes(2, "big")
        d[34:36] = (st[0] if hp is None else hp).to_bytes(2, "big")
        return dataclasses.replace(mon, data=bytes(d), egg=egg)

    def set_party(self, mons):
        self.cm.set_party(mons)

    def party(self):
        return [mn.data for mn in self.cm.party()]

    def eggs(self):
        return {i for i in range(self.cm.r(crystal.PARTY_COUNT)) if self.cm.r(crystal.PARTY_SPECIES + i) == crystal.EGG}

    def species_dex(self, d):
        return d[0]

    def set_bag(self, items):
        raise NotImplementedError

    def wram(self):
        return bytes(self.m[0xC000:0xD000]) + bytes(self.m[1, 0xD000:0xDFFF]) + bytes([self.m[1, 0xDFFF]])

    def start(self):
        if self.case == "trainer":
            self.cm.w(0xD280, 1)                          # wOTPartyCount = 1: the battle ends with this mon

    def to_menu(self):
        assert self.V.to_battle_menu(self.pb)

    def enemy(self):
        cm = self.cm
        return dict(base_exp=cm.r(0xD22C), level=cm.r(0xD213), stats=[cm.r(0xD226 + i) for i in range(5)],
                    trainer=cm.r(crystal.BATTLE_MODE) == 2, participants=self.m[0xC664],
                    player_id=cm.player_id())

    def switch_to(self, slot):
        name = self.cm.party()[slot].nick_str()
        assert self.V.battle_menu(self.pb, "PKMN")
        assert self.V.pick_party(self.pb, name), "pick " + name
        self.to_menu()

    def attack(self):
        from play import choose
        from common import ticks
        self.cm.w(0xD216, 0)
        self.cm.w(0xD217, 1)                              # wEnemyMonHP = 1
        assert self.V.battle_menu(self.pb, "FIGHT")
        ticks(self.pb, 20)
        assert choose(self.pb, "TACKLE")

    def screen(self):
        from common import screen_text
        return " ".join(screen_text(self.pb))

    def yes_no(self):
        s = self.screen()
        return "YES" in s and "NO" in s

    def in_battle(self):
        return self.cm.in_battle()

    def idle(self):
        from play import busy
        return not self.cm.in_battle() and not busy(self.pb)

    def tick(self, n=1):
        for _ in range(n):
            self.pb.tick(1, False)

    def press(self, b):
        from common import tap
        tap(self.pb, b, 4, 20)

    def stop(self):
        self.pb.stop(False)


# =================================================================================================================
def finish(D, maxpress=400, h=None, busy=None):
    """Through the award texts, level-ups, move learning (YES to delete, forget the first move), evolution and the
    end-of-battle texts (NO to nicknames) back to the idle overworld. Returns the distinct screens seen."""
    seen, learning = [], False
    last = None
    for _ in range(maxpress):
        if D.idle():
            D.tick(40)
            if D.idle():
                break
        s = D.screen()
        if h is not None:
            busy.append((h.busy(), "gained" in s))
        if s != last:
            seen.append(s)
            last = s
        if "trying to learn" in s:
            learning = True
        if D.yes_no():
            D.press("a" if learning else "b")
        else:
            if "learned" in s:
                learning = False
            D.press("a")
        D.tick(20)
    return seen


def text_seen(seen, *needles):
    return any(all(n in s for n in needles) for s in seen)


def gains(D, before, after):
    out = []
    for b, a in zip(before, after):
        e0, e1 = int.from_bytes(b[D.EXP:D.EXP + 3], "big"), int.from_bytes(a[D.EXP:D.EXP + 3], "big")
        st = [int.from_bytes(a[D.STATEXP + 2 * k:D.STATEXP + 2 * k + 2], "big") -
              int.from_bytes(b[D.STATEXP + 2 * k:D.STATEXP + 2 * k + 2], "big") for k in range(5)]
        out.append((e1 - e0, st))
    return out


def play(G, case, mode, party_fn, switch=None, bag=None):
    """One battle. Returns dict(before, after, enemy, seen, wram, hooks, eggs)."""
    D = G(case)
    try:
        mons = party_fn(D)
        D.set_party(mons)
        if bag is not None:
            D.set_bag(bag)
        h = None
        if mode != "none":
            h = D.install(D.pb, lambda: mode == "on")
        D.start()
        D.to_menu()
        if switch is not None:
            D.switch_to(switch)
        en = D.enemy()
        eggs = D.eggs()
        before = D.party()
        D.attack()
        busy = []
        seen = finish(D, h=h, busy=busy)
        after = D.party()
        return dict(before=before, after=after, enemy=en, seen=seen, wram=D.wram(), awards=h.awards if h else None,
                    eggs=eggs, idle=D.idle(), hook=h, busy=busy, busy_end=h.busy() if h else None)
    finally:
        D.stop()


def expected(D, r, rule, holders=(), lucky=(), exp_all=False):
    """Per-mon (exp, statexp list) the game should award. rule: 'on' (modern), 'off' (original game)."""
    en, before = r["enemy"], r["before"]
    b, lvl, stats, trainer = en["base_exp"], en["level"], en["stats"], en["trainer"]
    parts = en["participants"]
    n = len(before)
    alive = [int.from_bytes(d[D.HP:D.HP + 2], "big") > 0 for d in before]
    traded = [int.from_bytes(d[D.OTID:D.OTID + 2], "big") != en["player_id"] for d in before]
    out = [[0, [0] * 5] for _ in range(n)]

    def give(i, base_exp, st, share=False):
        out[i][0] += exp_award(base_exp, lvl, traded=traded[i], trainer=trainer, lucky_egg=i in lucky, share=share)
        for k in range(5):
            out[i][1][k] = stat_exp_after(out[i][1][k], st[k])
    ps = [i for i in range(n) if parts >> i & 1 and alive[i]]
    if rule == "on":
        for i in range(n):
            if not alive[i] or i in r["eggs"]:
                continue
            if i in ps:
                give(i, b, stats)
            else:
                give(i, b, [s >> 1 for s in stats], share=True)
        return [tuple(x) for x in out]
    # the original game
    data = stats + [0, b]                                  # 5 stats, catch rate (unused), base exp
    if exp_all or holders:
        data = [x >> 1 for x in data]
    k = bin(parts).count("1")
    first = [x // k for x in data] if k >= 2 else list(data)
    for i in ps:
        give(i, first[6], first[:5])
    if exp_all:                                            # Yellow: all party bits, divides the already divided data
        cnt = n
        second = [x // cnt for x in first] if cnt >= 2 else first
        for i in range(n):
            if alive[i]:
                give(i, second[6], second[:5])
    elif holders:                                          # Crystal: the holders, from the halved backup
        hs = [i for i in holders if alive[i]]
        second = [x // len(hs) for x in data] if len(hs) >= 2 else list(data)
        for i in hs:
            give(i, second[6], second[:5])
    return [tuple(x) for x in out]


def compare(D, label, r, exp):
    got = gains(D, r["before"], r["after"])
    ok = [tuple((g[0], g[1])) == (e[0], e[1]) for g, e in zip(got, exp)]
    check(label, all(ok) and len(got) == len(exp), f"got {got} expected {exp}")
    info(label.split(":")[0] + ": exp " + " ".join(f"{g[0]}" for g in got) + "  (expected " +
         " ".join(f"{e[0]}" for e in exp) + ")")


# =================================================================================================================
def main_party(D):
    t = D.templates()
    lead, a, b = t[0], t[1], t[2]
    if isinstance(D, Yellow):
        lead = D.build(lead)                                         # healed (the fixture's Pikachu is at 0 HP)
        return [lead, a, b, D.build(a, hp=0), D.build(b, ot_id=0x1234)]
    return [lead, D.build(a, item=D.LUCKY_EGG), b, D.build(a, hp=0), D.build(b, ot_id=0x1234),
            D.build(b, egg=True)]


def run_game(G):
    print(f"=============== {G.name} ===============", flush=True)
    lucky = (1,) if G is Crystal else ()

    # --- wild: one participant ---------------------------------------------------------------------------------
    res = {mode: play(G, "wild", mode, main_party) for mode in ("none", "off", "on")}
    D = G.__new__(G)                     # class constants only (struct offsets, item and move ids)
    en = res["on"]["enemy"]
    info(f"wild: enemy level {en['level']}, base exp {en['base_exp']}, base stats {en['stats']}, "
         f"participants {en['participants']:06b}")
    check(f"{G.name} wild: hooks off -> WRAM after the battle identical to no hooks at all",
          res["off"]["wram"] == res["none"]["wram"])
    check(f"{G.name} wild: one participant (mask 000001), rule applied once (on) / never (off)",
          en["participants"] == 1 and res["on"]["awards"] == 1 and res["off"]["awards"] == 0)
    compare(D, f"{G.name} wild ON: A solo, B/C half, D fainted 0, E traded half of x1.5"
            + (", B Lucky Egg half of x1.5, F egg 0" if G is Crystal else ""), res["on"],
            expected(D, res["on"], "on", lucky=lucky))
    compare(D, f"{G.name} wild OFF: original game (A solo, nobody else)", res["off"],
            expected(D, res["off"], "off", lucky=lucky))
    solo = gains(D, res["on"]["before"], res["on"]["after"])[0][0]
    shares = [g[0] for g in gains(D, res["on"]["before"], res["on"]["after"])]
    check(f"{G.name} wild ON: the participant gets the same as with the rule off ({solo})",
          solo == gains(D, res["off"]["before"], res["off"]["after"])[0][0])
    check(f"{G.name} wild ON: plain non-participants get floor(solo/2) = {solo // 2}",
          shares[2] == solo // 2 and (G is Crystal or shares[1] == solo // 2))
    check(f"{G.name} wild ON: the fainted mon gets 0 exp and 0 stat exp", res["on"]["before"][3] == res["on"]["after"][3])
    if G is Crystal:
        check("Crystal wild ON: the egg is unchanged", res["on"]["before"][5] == res["on"]["after"][5])
    bon, boff = res["on"]["busy"], res["off"]["busy"]
    check(f"{G.name} wild: busy() True while the 'gained' texts show (on), False back in the overworld; never (off)",
          any(b and g for b, g in bon) and res["on"]["busy_end"] is False
          and not any(b for b, _ in boff), (sum(b for b, _ in bon), len(bon)))
    check(f"{G.name} wild ON: the plain 'gained N EXP. Points!' text (no 'with EXP.ALL')",
          text_seen(res["on"]["seen"], "gained") and not text_seen(res["on"]["seen"], "EXP.ALL"))

    # --- two participants ---------------------------------------------------------------------------------------
    sw = {mode: play(G, "wild", mode, main_party, switch=1) for mode in ("off", "on")}
    en = sw["on"]["enemy"]
    check(f"{G.name} switch: two participants (mask 000011)", en["participants"] == 3, f"{en['participants']:06b}")
    compare(D, f"{G.name} switch ON: A and B both get the full solo amount (no split), C half", sw["on"],
            expected(D, sw["on"], "on", lucky=lucky))
    compare(D, f"{G.name} switch OFF: original split between the two", sw["off"],
            expected(D, sw["off"], "off", lucky=lucky))

    # --- trainer battle (x1.5) ----------------------------------------------------------------------------------
    tr = {mode: play(G, "trainer", mode, main_party) for mode in ("none", "off", "on")}
    en = tr["on"]["enemy"]
    info(f"trainer: enemy level {en['level']}, base exp {en['base_exp']}")
    check(f"{G.name} trainer: it is a trainer battle", en["trainer"])
    check(f"{G.name} trainer: hooks off -> WRAM identical to no hooks", tr["off"]["wram"] == tr["none"]["wram"])
    compare(D, f"{G.name} trainer ON: x1.5 solo for A, half of the boosted amount for the others", tr["on"],
            expected(D, tr["on"], "on", lucky=lucky))
    compare(D, f"{G.name} trainer OFF: original", tr["off"], expected(D, tr["off"], "off", lucky=lucky))

    # --- level-up / move / forget prompt / evolution for non-participants ----------------------------------------
    def lv_party(D):
        t = D.templates()
        lead, a, b = t[0], t[1], t[2]
        if isinstance(D, Yellow):
            lead = D.build(lead)
            return [lead,
                    D.build(a, level=4, exp_minus=1, moves=[0x10]),                       # PIDGEY 4 -> 5 SAND-ATTACK
                    D.build(b, level=13, exp_minus=1, moves=[D.TACKLE, D.TAIL_WHIP, D.GROWL, D.LEER]),  # 14 HYPER FANG
                    D.build(b, level=19, exp_minus=1, moves=[D.TACKLE, D.TAIL_WHIP])]    # 20 -> RATICATE
        return [lead,
                D.build(a, level=4, exp_minus=1, moves=[D.TACKLE]),                       # PIDGEY 4 -> 5 SAND-ATTACK
                D.build(b, level=10, exp_minus=1, moves=[D.TACKLE, D.DEFENSE_CURL, D.GROWL, D.LEER]),  # 11 QUICK ATK
                D.build(b, level=14, exp_minus=1, moves=[D.TACKLE, D.DEFENSE_CURL])]       # 15 -> FURRET
    lv = play(G, "wild", "on", lv_party)
    bef, aft = lv["before"], lv["after"]
    check(f"{G.name} levelup: each non-participant was 1 exp short of the next level and went up exactly one level",
          all(aft[i][D.LEVEL] == bef[i][D.LEVEL] + 1 for i in (1, 2, 3)),
          [(bef[i][D.LEVEL], aft[i][D.LEVEL]) for i in (1, 2, 3)])
    stats_ok, hp_ok = True, True
    for i in (1, 2, 3):
        a = aft[i]
        if G is Yellow:
            want = game.calc_stats(a, SY.RD.base_stats(a[0]))
            got = [int.from_bytes(a[34 + 2 * k:36 + 2 * k], "big") for k in range(5)]
        else:
            want = crystal.calc_stats(a, CRYSTAL_RD.base_stats(a[0]))
            got = [int.from_bytes(a[36 + 2 * k:38 + 2 * k], "big") for k in range(6)]
        stats_ok &= got == want
        hp_ok &= int.from_bytes(a[D.HP:D.HP + 2], "big") == got[0]          # was full -> full at the new max
    check(f"{G.name} levelup: stats recomputed by the game == the stat formula at the new level (and species)", stats_ok)
    check(f"{G.name} levelup: HP raised with max HP (full before -> full after)", hp_ok)
    check(f"{G.name} levelup: 'grew to level N' text shown for each non-participant",
          all(text_seen(lv["seen"], f"level {aft[i][D.LEVEL]}") for i in (1, 2, 3)) and text_seen(lv["seen"], "grew"),
          [aft[i][D.LEVEL] for i in (1, 2, 3)])
    mv1 = list(aft[1][D.MOVES:D.MOVES + 4])
    check(f"{G.name} levelup: non-participant learned SAND-ATTACK at level 5 (game's own LearnMove)",
          D.SAND_ATTACK in mv1 and text_seen(lv["seen"], "learned"), mv1)
    new_move = D.HYPER_FANG if G is Yellow else D.QUICK_ATTACK
    mv2 = list(aft[2][D.MOVES:D.MOVES + 4])
    check(f"{G.name} levelup: 4 moves -> 'trying to learn' + forget prompt, first move replaced by the new one",
          text_seen(lv["seen"], "trying to learn") and text_seen(lv["seen"], "Which move") and mv2[0] == new_move
          and mv2[1:] == list(bef[2][D.MOVES + 1:D.MOVES + 4]), mv2)
    evo_dex = D.RATICATE_DEX if G is Yellow else D.FURRET_DEX
    got_dex = SY.RD.dex_no(aft[3][0]) if G is Yellow else aft[3][0]
    check(f"{G.name} levelup: non-participant crossing its evolution level evolved after the battle (dex {evo_dex})",
          got_dex == evo_dex and text_seen(lv["seen"], "evolv"), got_dex)
    check(f"{G.name} levelup: back in the overworld", lv["idle"])

    # --- EXP.ALL / Exp. Share -----------------------------------------------------------------------------------
    if G is Yellow:
        bag = [(0x04, 5), (G.EXP_ALL, 1)]
        ea = {mode: play(G, "wild", mode, main_party, bag=bag) for mode in ("none", "off", "on")}
        compare(D, "Yellow EXP.ALL ON: same as without EXP.ALL (no stacking)", ea["on"],
                expected(D, ea["on"], "on"))
        check("Yellow EXP.ALL ON: gains identical to the run without EXP.ALL",
              gains(D, ea["on"]["before"], ea["on"]["after"]) == gains(D, res["on"]["before"], res["on"]["after"]))
        compare(D, "Yellow EXP.ALL OFF: the original EXP.ALL (half to A, the other half split over the party)",
                ea["off"], expected(D, ea["off"], "off", exp_all=True))
        check("Yellow EXP.ALL OFF: 'with EXP.ALL' text, WRAM identical to no hooks",
              text_seen(ea["off"]["seen"], "EXP.ALL") and ea["off"]["wram"] == ea["none"]["wram"])
    else:
        def share_party(D):
            p = main_party(D)
            p[0] = D.build(p[0], item=D.EXP_SHARE)              # A (the participant) holds an Exp. Share
            p[2] = D.build(p[2], item=D.EXP_SHARE)              # C (sits out) too
            return p
        ea = {mode: play(G, "wild", mode, share_party) for mode in ("none", "off", "on")}
        compare(D, "Crystal Exp. Share (A and C hold one) ON: same as without (A solo, C half, no stacking)", ea["on"],
                expected(D, ea["on"], "on", lucky=lucky))
        g_on = gains(D, ea["on"]["before"], ea["on"]["after"])
        g_plain = gains(D, res["on"]["before"], res["on"]["after"])
        check("Crystal Exp. Share ON: gains identical to the run without an Exp. Share", g_on == g_plain)
        compare(D, "Crystal Exp. Share OFF: original (A half + quarter, C quarter)", ea["off"],
                expected(D, ea["off"], "off", holders=(0, 2), lucky=lucky))
        check("Crystal Exp. Share OFF: WRAM identical to no hooks", ea["off"]["wram"] == ea["none"]["wram"])

    # --- remove() --------------------------------------------------------------------------------------------
    D2 = G("wild")
    sig = {n: D2.m[b, a] for n, (b, a) in G.hook_cls.HOOKS.items()}
    h = D2.install(D2.pb, lambda: True)
    patched = all(D2.m[b, a] == 0xDB for b, a in G.hook_cls.HOOKS.values())
    h.remove()
    restored = {n: D2.m[b, a] for n, (b, a) in G.hook_cls.HOOKS.items()} == sig == G.hook_cls.SIGNATURES
    h2 = D2.install(D2.pb, lambda: True)                  # can be installed again
    h2.remove()
    check(f"{G.name}: install patches {len(sig)} code bytes, remove() restores them, re-install works",
          patched and restored)
    D2.stop()


CRYSTAL_RD = crystal.RomData.from_file(os.environ.get("YL_CRYSTAL_ROM", os.path.join(_REPO, "dev", "states",
                                                                                     "crystal.gbc")))


if __name__ == "__main__":
    which = sys.argv[1:] or ["yellow", "crystal"]
    if "yellow" in which:
        run_game(Yellow)
    if "crystal" in which:
        run_game(Crystal)
    print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)
