"""Platinum raid checks for dev/raids/verify.py: production raid_pt.py on nds_core.NDSCore (like the app), on
the ROM dev/platinum/ptrom.py picks (YL_PT_ROM=vanilla | sinnoh; run both)."""
import os
import shutil
import tempfile

# DeSmuME's battery file goes into $XDG_CONFIG_HOME/desmume: a temp folder of ours (removed at the end)
_TMP = tempfile.mkdtemp(prefix="yl-raid-pt-")
os.environ["XDG_CONFIG_HOME"] = _TMP

import ptrom  # noqa: E402  (dev/platinum: YL_PT_ROM, cleans its ROM copies)
from yellowlink import nds_core, pk4, raid_data, raid_pt  # noqa: E402
from yellowlink.platinum import PlatinumMemory, mon_info  # noqa: E402

CHECK = INFO = None
STATES = OUT = None
MEW, LATIAS = 151, 380
SPLASH, PSYCHIC_M = 150, 94
MASTER_BALL, POKE_BALL = 1, 4
BAG_BUTTON = (40, 168)
BAG_BALLS_POCKET = (192, 56)
BAG_SLOTS = [(64, 30), (192, 30), (64, 78), (192, 78)]
BAG_USE = (100, 174)


class PT:
    def __init__(self, core, state):
        self.d = core
        core.load_state(os.path.join(STATES, state))
        core.release_all()
        self.pm = PlatinumMemory(core)
        self.frame = 0
        self.rb = None
        self.events = []
        self.pool = self.pool0 = 0
        self.acked = 0
        self.inflight = []
        self.lag = 20
        self.on_event = None
        self.script = []           # [(frame, ("touch", xy) | ("key", name))]
        self._held = None

    def tick(self, n=1):
        for _ in range(n):
            if self._held and self.frame >= self._held[0]:
                kind, what = self._held[1][:2]
                if kind == "touch":
                    self.d.touch_release()
                else:
                    self.d.button_release(what)
                self._held = None
            if self._held is None and self.script and self.script[0][0] <= self.frame:
                _f, act = self.script.pop(0)
                if act[0] == "touch":
                    self.d.touch(*act[1])
                else:
                    self.d.button_press(act[1])
                self._held = (self.frame + (act[2] if len(act) > 2 else 6), act)
            self.d.tick(1)
            self.frame += 1
            while self.inflight and self.inflight[0][0] <= self.frame:
                _f, mb = self.inflight.pop(0)
                self.pool = max(0, self.pool - mb)
                self.acked += mb
                if self.rb:
                    self.rb.set_pool(self.pool, self.acked)
            if self.rb:
                for ev in self.rb.step():
                    self.events.append((self.frame, ev))
                    if ev["type"] == "damage":
                        self.inflight.append((self.frame + self.lag, ev["mb"]))
                    if self.on_event:
                        self.on_event(ev)

    def later(self, delay, *acts, gap=60):
        t = self.frame + delay
        for a in acts:
            self.script.append((t, a))
            t += gap
        self.script.sort(key=lambda x: x[0])

    def bag_ball(self, slot, delay=30):
        self.later(delay, ("touch", BAG_BUTTON), ("touch", BAG_BALLS_POCKET), ("touch", BAG_SLOTS[slot]),
                   ("touch", BAG_USE), gap=60)

    def raid(self, boss, pool):
        self.rb = raid_pt.RaidBattle(self.d, boss, pm=self.pm, data=DATA)
        self.pool = self.pool0 = pool
        for _ in range(300):
            if self.pm.is_safe():
                break
            self.tick()
        ok, why = self.rb.can_join()
        assert ok, why
        assert self.rb.start(pool)
        return self.rb

    def kinds(self):
        return [e["type"] for _f, e in self.events]

    def outcome(self):
        for _f, e in self.events:
            if e["type"] == "ended":
                return e["outcome"]
        return None

    def ended(self):
        return self.outcome() is not None

    def balls(self):
        return dict(self.pm.balls())

    def party(self):
        return [mon_info(r) for r in (self.pm.party_checked() or [])]

    def shot(self, name):
        import pygame
        raw = self.d.screen_rgb()
        img = pygame.image.frombuffer(raw, (256, 384), "RGB")
        pygame.image.save(img, os.path.join(OUT, name))


DATA = None


def boss(species, level, moves, shiny=False):
    b = raid_data.boss_for(species, 30, "sinnoh", shiny=shiny)
    b["level"] = level
    b["moves"] = (list(moves) + [0, 0, 0, 0])[:4]
    return b


def strong_lead(g):
    """Test only: the player's battle copy hits hard, so a multi-bar pool takes a few turns, not fifty."""
    ctx = g.rb._ctx()
    if ctx:
        g.d.w16(ctx + raid_pt.CTX_BATTLE_MONS + 2, 400)        # battleMons[0].attack
        g.d.w16(ctx + raid_pt.CTX_BATTLE_MONS + 8, 400)        # spAttack


def after_catch_input(g):
    """The test player after a throw: the 'Give a nickname?' Yes/No buttons are up -> touch No (the blue button);
    anything else (Pokédex page, texts) -> A."""
    raw = g.d.screen_rgb()
    x, y = 30, 192 + 140
    r, gg, b = raw[3 * (y * 256 + x):3 * (y * 256 + x) + 3]
    if b > 150 and r < 120:
        g.later(0, ("touch", (128, 140)))
    else:
        g.later(0, ("key", "a"))


def drive(g, rb, frames, *, down_chance=None, first_ball=None, throw_slot=1, flee_at_first=False, strong=False):
    """The test player: auto-pick after a short 'timer', a ball in the first turn (blocked), the one throw in the
    catch phase, B through the after-catch screens (B = no nickname)."""
    st = {"auto_at": None, "turn": 0, "down": False, "threw": False, "fled": False}

    def on_event(ev):
        if ev["type"] == "started" and strong:
            strong_lead(g)
        if ev["type"] == "need_input":
            st["turn"] += 1
            if ev["what"] == "menu" and rb.phase == "down" and not st["threw"]:
                st["threw"] = True
                g.bag_ball(throw_slot, 40)
            elif ev["what"] == "menu" and st["turn"] == 1 and first_ball is not None:
                g.bag_ball(first_ball, 40)
                st["auto_at"] = g.frame + 600
            else:
                if flee_at_first and not st["fled"]:
                    st["fled"] = True
                    rb.flee()
                st["auto_at"] = g.frame + 40

    g.on_event = on_event
    last_b = 0
    for _ in range(frames):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if down_chance is not None and g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(down_chance)
        if rb.throws and rb.decision is None and not g.script and g.frame - last_b > 80 and not g.pm.is_safe():
            after_catch_input(g)                  # after the throw: Pokédex page (A), "nickname?" -> No
            last_b = g.frame
        if g.ended():
            break
    for _ in range(600):
        if g.pm.is_safe():
            break
        if not g.script and g.frame - last_b > 80:
            after_catch_input(g)
            last_b = g.frame
        g.tick()


def scenario_catch(core):
    print("== platinum: raid battle via the game's own functions, shared HP, timer, ball blocked, forced catch "
          "(shiny + fateful Mew) ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    balls0 = g.balls()
    party0 = g.party()
    lead_exp0 = pk4.PK4(pk4._plain_of(g.pm.party_checked()[0])).exp
    rb = g.raid(boss(MEW, 12, [SPLASH], shiny=True), 1500)
    ok = False
    for _ in range(3000):
        g.tick()
        if rb.phase == "fight" and rb.max_hp:
            ok = True
            break
    bi = g.pm.battle_info() or {}
    CHECK("platinum: 4 game functions called, wild MEW Lv12 battle running", ok and bi.get("dex") == MEW
          and bi.get("level") == 12, "calls %s info %s" % (rb.caller.log, {k: bi.get(k) for k in ("dex", "level")}))
    CHECK("platinum: shiny boss (the game's own shiny rule on the wild mon)", bi.get("shiny") is True)
    g.shot("pt_raid_start.png")
    drive(g, rb, 40000, down_chance=1.0, first_ball=0, strong=True)
    notes = [n for _f, n in rb.log]
    INFO("notes: %s" % [n for n in notes if "clamp" in n or "ball" in n or "shake" in n or "ended" in n][:12])
    dmg = [e for _f, e in g.events if e["type"] == "damage"]
    INFO("hits (mb): %s" % [x["mb"] for x in dmg])
    CHECK("platinum: damage adds up exactly to the pool", sum(x["mb"] for x in dmg) == g.pool0 and g.pool == 0,
          "%d of %d" % (sum(x["mb"] for x in dmg), g.pool0))
    CHECK("platinum: several bars, clamped at the HP update (never 0 HP)", sum("clamped" in n for n in notes) >= 1
          and not any("escaped" in n for n in notes))
    CHECK("platinum: ball refused while fighting (menu back, ball kept)", any(n.startswith("ball blocked") for n in notes))
    k = g.kinds()
    CHECK("platinum: timer events every turn", k.count("need_input") >= 3 and k.count("input_done") >= 3,
          "need_input %d input_done %d" % (k.count("need_input"), k.count("input_done")))
    CHECK("platinum: forced catch -> caught + ended(caught)", g.outcome() == "caught" and "caught" in k,
          "outcome %s last %s" % (g.outcome(), k[-5:]))
    party = g.pm.party_checked() or []
    if len(party) == len(party0) + 1:
        p = pk4.PK4(pk4._plain_of(party[-1]))
        INFO("caught: dex %d lv %s moves %s hp %d shiny %s fateful %s pid %08X" % (
            p.species, mon_info(party[-1])["level"], p.moves, p.hp, p.shiny, p.fateful, p.pid))
        CHECK("platinum: caught boss: Mew Lv12, natural moves (not Splash), HP >= 1, shiny, fateful, same PID",
              p.species == MEW and mon_info(party[-1])["level"] == 12 and SPLASH not in p.moves and any(p.moves)
              and p.hp >= 1 and p.shiny and p.fateful and p.pid == rb.pid and p.checksum_ok())
        key = [e for _f, e in g.events if e["type"] == "caught"][0]["key"]
        CHECK("platinum: caught event carries the mon's key", key == pk4.mon_key(party[-1]), str(key))
    else:
        CHECK("platinum: caught boss in the party", False, "party %d -> %d" % (len(party0), len(party)))
    lead_exp1 = pk4.PK4(pk4._plain_of(party[0])).exp if party else None
    CHECK("platinum: no EXP for the lead (the boss never fainted)", lead_exp0 == lead_exp1,
          "%s -> %s" % (lead_exp0, lead_exp1))
    b1 = g.balls()
    CHECK("platinum: balls: refused one kept, catch used one Poké Ball",
          b1.get(POKE_BALL, 0) == balls0.get(POKE_BALL, 0) - 1 and b1.get(MASTER_BALL) == balls0.get(MASTER_BALL),
          "%s -> %s" % (balls0, b1))
    CHECK("platinum: hooks removed, not busy", not rb.busy() and not rb.hooks.addrs)
    g.shot("pt_raid_after_catch.png")


def scenario_miss(core):
    print("== platinum: forced miss with a Master Ball -> the boss flees ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    balls0 = g.balls()
    n0 = len(g.party())
    rb = g.raid(boss(LATIAS, 12, [SPLASH]), 1)
    drive(g, rb, 20000, down_chance=0.0, throw_slot=0)
    k = g.kinds()
    INFO("notes: %s" % [n for _f, n in rb.log][-6:])
    CHECK("platinum: miss -> missed, fled, ended(missed), party unchanged",
          g.outcome() == "missed" and "missed" in k and "fled" in k and len(g.party()) == n0,
          "outcome %s %s" % (g.outcome(), k[-5:]))
    CHECK("platinum: the Master Ball was used up", g.balls().get(MASTER_BALL, 0) == balls0.get(MASTER_BALL, 0) - 1,
          "%s -> %s" % (balls0, g.balls()))
    CHECK("platinum: back in the overworld", g.pm.is_safe())


def scenario_afk_catch(core):
    print("== platinum: AFK player: auto-pick fights and throws the ball in the catch phase ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    balls0 = g.balls()
    n0 = len(g.party())
    rb = g.raid(boss(LATIAS, 12, [SPLASH]), 1)
    st = {"auto_at": None, "down": False, "last": 0}

    def on_event(ev):
        if ev["type"] == "need_input":
            st["auto_at"] = g.frame + 40

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(1.0)
        if rb.throws and rb.decision is None and not g.script and g.frame - st["last"] > 80 and not g.pm.is_safe():
            after_catch_input(g)
            st["last"] = g.frame
        if g.ended():
            break
    b1 = g.balls()
    CHECK("platinum: AFK catch: auto-pick threw a Poké Ball (Master Ball kept), caught",
          g.outcome() == "caught" and len(g.party()) == n0 + 1 and b1.get(POKE_BALL, 0) == balls0.get(POKE_BALL, 0) - 1
          and b1.get(MASTER_BALL) == balls0.get(MASTER_BALL), "outcome %s %s -> %s notes %s" % (
              g.outcome(), balls0, b1, [n for _f, n in rb.log][-4:]))


def scenario_trapped_flee(core):
    """M1: Mean Look on the boss + Shadow Tag on the player's lead, then time over: Teleport would fail
    (Battler_IsTrappedMsg) - the boss holds a Smoke Ball while fleeing and the player runs (boss speed 1)."""
    print("== platinum: time over while Mean Look + Shadow Tag trap the boss ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    rb = g.raid(boss(LATIAS, 12, [SPLASH]), 5000)
    st = {"n": 0, "auto_at": None}

    def on_event(ev):
        if ev["type"] == "need_input" and ev["what"] == "menu":
            st["n"] += 1
            ctx = rb._ctx()
            b0 = ctx + raid_pt.CTX_BATTLE_MONS
            if st["n"] == 1:                       # test only: the lead knows Mean Look and has Shadow Tag
                g.d.w16(b0 + raid_pt.BM_MOVES, 212)
                for i in range(1, 4):
                    g.d.w16(b0 + raid_pt.BM_MOVES + 2 * i, 0)
                g.d.w8(b0 + raid_pt.BM_PP, 5)
                g.d.w8(b0 + raid_pt.BM_ABILITY, 23)
                st["auto_at"] = g.frame + 40
            elif not rb.flee_asked:
                rb.flee()

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.ended():
            break
    for _ in range(600):
        if g.pm.is_safe():
            break
        g.tick()
    CHECK("platinum: trapped boss + time over -> fled + ended(fled), back in the overworld",
          g.outcome() == "fled" and g.pm.is_safe(), "outcome %s result %d notes %s" % (
              g.outcome(), rb.result, [n for _f, n in rb.log][-4:]))


def scenario_start_refused(core):
    """M2: the field gets a task (a trainer's sight line, a script...) during the 4 injected calls: the start is
    refused, the FieldBattleDTO is freed again, no battle. The task is simulated (the precheck says "busy" after the
    second call); then a normal raid starts fine (nothing left behind)."""
    print("== platinum: the field gets busy while the battle is being started ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    g.rb = rb = raid_pt.RaidBattle(g.d, boss(LATIAS, 12, [SPLASH]), pm=g.pm, data=DATA)
    for _ in range(300):
        if g.pm.is_safe():
            break
        g.tick()
    assert rb.start(5000)
    real = rb.caller.precheck
    rb.caller.precheck = lambda: len([x for x in rb.caller.log if x.startswith("call")]) < 2 and real()
    for _ in range(300):
        g.tick()
        if g.ended():
            break
    g.tick(120)
    calls = rb.caller.log
    CHECK("platinum: start refused -> ended(error), FieldBattleDTO_Free called, no battle, still safe",
          g.outcome() == "error" and "refused" in calls and "call %08X" % raid_pt.FieldBattleDTO_Free in calls
          and not g.pm.in_battle() and g.pm.is_safe() and not rb.hooks.addrs, "outcome %s calls %s" % (
              g.outcome(), calls))
    g.events = []
    rb2 = g.raid(boss(LATIAS, 12, [SPLASH]), 5000)
    ok = False
    for _ in range(3000):
        g.tick()
        if rb2.max_hp:
            ok = True
            break
    CHECK("platinum: ... then a normal raid battle starts", ok and g.pm.in_battle())
    rb2.flee()
    drive(g, rb2, 8000)


def scenario_deoxys_form(core):
    """L7: the server's Deoxys form (here Attack Forme) is the boss's form, with that form's stats."""
    print("== platinum: Deoxys in its Attack Forme ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    b = boss(386, 20, [SPLASH])
    b["form"] = 1
    rb = g.raid(b, 5000)
    for _ in range(3000):
        g.tick()
        if rb.max_hp:
            break
    ctx = rb._ctx()
    bm = rb._bmon(ctx)
    form = g.d.r8(bm + 0x26) & 0x1F
    atk = g.d.r16(bm + 2)
    pkm = pk4.PK4(pk4._plain_of(g.d.read(g.d.r32(rb.dto + raid_pt.DTO_PARTIES + 4) + 8, pk4.PARTY_SIZE)))
    want = pk4.calc_stats(pkm, DATA, 20)
    CHECK("platinum: Deoxys boss in form 1 with the form's stats (attack %d)" % atk,
          form == 1 and pkm.form == 1 and atk == want[1] and rb.max_hp == want[0], "form %d want %s" % (form, want))
    rb.flee()
    drive(g, rb, 8000)
    CHECK("platinum: ... and it leaves (fled)", g.outcome() == "fled", str(g.outcome()))


def scenario_ball_page2(core):
    """L10: the cheapest ball is on the pocket's second page (10 kinds of balls): AFK auto-pick still finds it."""
    print("== platinum: AFK throw with the Poké Ball on the second bag page ==", flush=True)
    from yellowlink import platinum as P
    g = PT(core, "pt_sl_route202.dsst")
    g.tick(10)
    body = g.pm.save_body()
    for i, it in enumerate([1, 2, 3, 6, 7, 8, 9, 10, 11, 4]):         # test only: write the ball pocket
        core.w32(body + P.PAGE_BAG + P.BAG_BALLS + 4 * i, it | (3 << 16))
    balls0 = g.balls()
    n0 = len(g.party())
    rb = g.raid(boss(LATIAS, 12, [SPLASH]), 1)
    st = {"auto_at": None, "down": False, "last": 0}

    def on_event(ev):
        if ev["type"] == "need_input":
            st["auto_at"] = g.frame + 40

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(1.0)
        if rb.throws and rb.decision is None and not g.script and g.frame - st["last"] > 80 and not g.pm.is_safe():
            after_catch_input(g)
            st["last"] = g.frame
        if g.ended():
            break
    b1 = g.balls()
    used = {k: balls0[k] - b1.get(k, 0) for k in balls0 if balls0[k] != b1.get(k, 0)}
    CHECK("platinum: AFK throw used the Poké Ball from page 2 (nothing else), caught",
          g.outcome() == "caught" and len(g.party()) == n0 + 1 and used == {POKE_BALL: 1}, "used %s" % used)


def _set_ball_pocket(g, entries):
    """test-only: rewrite the bag's Balls pocket (15 x {u16 item, u16 quantity})."""
    from yellowlink import platinum as P
    body = g.pm.save_body()
    for i in range(P.BALL_POCKET_SIZE):
        it, q = entries[i] if i < len(entries) else (0, 0)
        g.d.w32(body + P.PAGE_BAG + P.BAG_BALLS + 4 * i, it | (q << 16))


def scenario_free_ball(core):
    """No ball of any kind: joining gives exactly one Poké Ball (Bag_TryAddItem's way, at the safe overworld frame
    before the battle), it shows in the Balls pocket and the catch works with it. Pocket full: still refused."""
    print("== platinum: no Poké Ball: YellowLink gives one, the catch uses it; full pocket: refused ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    g.tick(10)
    _set_ball_pocket(g, [(17 + i, 1) for i in range(15)])          # (not balls: only the full case matters here)
    rb = raid_pt.RaidBattle(g.d, boss(LATIAS, 12, [SPLASH]), pm=g.pm, data=DATA)
    ok, why = rb.can_join()
    CHECK("platinum: no ball + full Balls pocket: still refused (\"%s\")" % why,
          not ok and why == "you need a Poké Ball")
    CHECK("platinum: full pocket: start() gives nothing and doesn't start",
          not rb.start(1000) and not rb.ball_given and rb.phase == "idle" and POKE_BALL not in g.balls())
    g = PT(core, "pt_sl_route202.dsst")
    g.tick(10)
    _set_ball_pocket(g, [])
    CHECK("platinum: test bag has no ball", not g.balls())
    n0 = len(g.party())
    rb = g.raid(boss(LATIAS, 12, [SPLASH]), 1)
    b0 = g.balls()
    CHECK("platinum: joined; exactly one Poké Ball given before the battle (ball_given)",
          b0 == {POKE_BALL: 1} and rb.ball_given and {"type": "ball_given"} in rb.events, "pocket %s" % b0)
    st = {"down": False, "threw": False, "shot": None, "last": 0, "auto_at": None}

    def on_event(ev):
        if ev["type"] == "need_input" and ev["what"] == "menu" and rb.phase == "down" and not st["threw"]:
            st["threw"] = True
            g.bag_ball(0, delay=40)                    # the player picks the given ball in the Balls pocket
            st["shot"] = g.frame + 40 + 60 * 2 + 40
        elif ev["type"] == "need_input" and rb.phase == "fight":
            st["auto_at"] = g.frame + 40

    g.on_event = on_event
    for _ in range(20000):
        g.tick()
        if st["auto_at"] is not None and g.frame >= st["auto_at"]:
            st["auto_at"] = None
            rb.auto_pick()
        if st["shot"] is not None and g.frame >= st["shot"]:
            st["shot"] = None
            g.shot("pt_free_ball_bag.png")
        if g.pool == 0 and not st["down"] and rb.phase == "fight":
            st["down"] = True
            rb.group_down(1.0)
        if rb.throws and rb.decision is None and not g.script and g.frame - st["last"] > 80 and not g.pm.is_safe():
            after_catch_input(g)
            st["last"] = g.frame
        if g.ended():
            break
    b1 = g.balls()
    CHECK("platinum: the catch works with the given ball (caught, ball used up)",
          g.outcome() == "caught" and len(g.party()) == n0 + 1 and not b1.get(POKE_BALL),
          "outcome %s %s -> %s notes %s" % (g.outcome(), b0, b1, [n for _f, n in rb.log][-4:]))


def scenario_flee(core):
    print("== platinum: time over -> the boss teleports away ==", flush=True)
    g = PT(core, "pt_sl_route202.dsst")
    rb = g.raid(boss(LATIAS, 12, [SPLASH]), 5000)
    drive(g, rb, 12000, flee_at_first=True)
    CHECK("platinum: flee() -> fled + ended(fled)", g.outcome() == "fled" and "fled" in g.kinds(),
          "outcome %s notes %s" % (g.outcome(), [n for _f, n in rb.log][-4:]))
    CHECK("platinum: back in the overworld", g.pm.is_safe())


def scenario_next_mon_blackout(core):
    print("== platinum: faint -> 'Use next Pokémon?' + party by auto-pick; then blackout ==", flush=True)
    g = PT(core, "pt_sl_caught.dsst")
    rb = g.raid(boss(LATIAS, 30, [PSYCHIC_M]), 5000)
    drive(g, rb, 30000)
    whats = [e.get("what") for _f, e in g.events if e["type"] == "need_input"]
    INFO("decisions: %s" % whats)
    sent = [n for _f, n in rb.log]
    CHECK("platinum: 'next_mon' decision answered by auto-pick (the next Pokémon came out)",
          "next_mon" in whats and whats.index("next_mon") < len(whats) - 1, str(whats))
    CHECK("platinum: blackout -> blackout + ended(blackout)", g.outcome() == "blackout" and "blackout" in g.kinds(),
          "outcome %s %s" % (g.outcome(), sent[-3:]))
    for _ in range(3000):
        if g.pm.is_safe():
            break
        g.tick()
        if g.frame % 90 == 0:
            g.later(0, ("key", "a"))
    party = g.party()
    CHECK("platinum: the game's own blackout (back at a Pokémon Center, healed)", g.pm.is_safe()
          and all(p["hp"] > 0 for p in party), str([(p["dex"], p["hp"]) for p in party]))


def scenario_perish_dbond(core):
    """The player's Perish Song / Destiny Bond never take the boss down (they go through the HP update: clamped)."""
    for move, name in ((195, "Perish Song"), (194, "Destiny Bond")):
        print("== platinum: the player's %s against the boss ==" % name, flush=True)
        g = PT(core, "pt_sl_route202.dsst")
        rb = g.raid(boss(LATIAS, 30 if move == 194 else 12, [SPLASH] if move == 195 else [PSYCHIC_M]), 5000)
        st = {"set": False, "auto_at": None}

        def on_event(ev, g=g, rb=rb, st=st, move=move):
            if ev["type"] == "need_input" and not st["set"]:
                st["set"] = True                       # test only: the lead knows just this move; the boss is slow
                ctx = rb._ctx()
                b0 = ctx + raid_pt.CTX_BATTLE_MONS
                g.d.w16(b0 + raid_pt.BM_MOVES, move)
                for i in range(1, 4):
                    g.d.w16(b0 + raid_pt.BM_MOVES + 2 * i, 0)
                g.d.w8(b0 + raid_pt.BM_PP, 5)
                g.d.w16(ctx + raid_pt.CTX_BATTLE_MONS + raid_pt.BATTLE_MON_SIZE + 6, 1)     # boss speed
            if ev["type"] == "need_input":
                st["auto_at"] = g.frame + 40

        g.on_event = on_event
        for _ in range(30000):
            if st["auto_at"] is not None and g.frame >= st["auto_at"]:
                st["auto_at"] = None
                rb.auto_pick()
            g.tick()
            if g.ended():
                break
        notes = [n for _f, n in rb.log]
        INFO("notes: %s" % [n for n in notes if "clamp" in n or "guard" in n or "ended" in n])
        bars = [e["mb"] for _f, e in g.events if e["type"] == "damage"]
        CHECK("platinum: %s took the boss's current bar (clamped at 1 HP, no win), the raid went on" % name,
              any("clamp" in n or "guard" in n for n in notes) and g.outcome() in ("blackout",) and rb.result != 1
              and bars and bars[-1] >= 900, "outcome %s result %d hits %s" % (g.outcome(), rb.result, bars))


def scenario_den(core):
    print("== platinum: den drawn on the top screen, collision, A to join ==", flush=True)
    import pygame
    from yellowlink import sinnoh_view
    g = PT(core, "pt_sl_route202.dsst")
    g.tick(20)
    pm = g.pm
    p = pm.position()
    spot = {"region": "sinnoh", "map": pm.map_id(), "x": p["x"], "y": p["y"] - 2, "name": "test", "reach": 0}
    den = raid_pt.Den(spot)
    fs = pm.field_system()
    cam = sinnoh_view.read_camera(core, fs)
    g.tick(sinnoh_view.DISPLAY_LAG)
    surf = pygame.image.frombuffer(core.screen_rgb(), (256, 384), "RGB").copy()
    vis = den.draw(surf, pm, cam, t=0.3)
    pygame.image.save(pygame.transform.scale(surf, (512, 768)), os.path.join(OUT, "den_platinum.png"))
    CHECK("platinum: den drawn on the top screen (den_platinum.png)", vis)
    CHECK("platinum: two tiles away: 'up' not blocked", not den.blocks(pm, "up"))
    g.later(0, ("key", "up", 14))
    for _ in range(120):
        g.tick()
        if pm.is_safe() and pm.position()["y"] == p["y"] - 1:
            break
    CHECK("platinum: next to the den: 'up' blocked, near()", den.blocks(pm, "up") and den.near(pm),
          str(pm.position()))
    CHECK("platinum: A next to the den -> join prompt", den.pressed_a(pm))
    far = raid_pt.Den(dict(spot, x=p["x"] + 40))
    surf2 = pygame.Surface((256, 384))
    CHECK("platinum: off screen -> edge arrow, offset in tiles", not far.draw(surf2, pm, cam)
          and far.offset(pm)[0] == 40)
    # a real spot from client/yellowlink/raid_spots.json (Twinleaf Town, next to the fixture's player)
    g = PT(core, "pt_town.dsst")
    g.tick(20)
    p = pm_pos = g.pm.position()
    near = sorted((s for s in raid_data.spots("sinnoh") if s["map"] == g.pm.map_id()),
                  key=lambda s: abs(s["x"] - p["x"]) + abs(s["y"] - p["y"]))
    den = raid_pt.Den(near[0])
    cam = sinnoh_view.read_camera(core, g.pm.field_system())
    g.tick(sinnoh_view.DISPLAY_LAG)
    surf = pygame.image.frombuffer(core.screen_rgb(), (256, 384), "RGB").copy()
    vis = den.draw(surf, g.pm, cam, t=0.3)
    pygame.image.save(pygame.transform.scale(surf, (512, 768)), os.path.join(OUT, "den_platinum_spot.png"))
    INFO("real spot %s at (%d, %d), player at (%d, %d): %s" % (near[0]["name"], near[0]["x"], near[0]["y"],
                                                              pm_pos["x"], pm_pos["y"], "on screen" if vis else "arrow"))
    CHECK("platinum: den on a real Twinleaf Town spot (den_platinum_spot.png)", vis)
    den2 = raid_pt.Den(near[0])
    seen = False
    for _ in range(8):                             # no camera given: the den keeps its own (lagged) history
        g.tick()
        surf3 = pygame.Surface((256, 384))
        seen = den2.draw(surf3, g.pm, off=(0, 0), mask=None) or seen
    CHECK("platinum: den.draw(surface, pm, off=, mask=) without a camera (own camera history)", seen)


def run(check, info, states, out):
    global CHECK, INFO, STATES, OUT, DATA
    CHECK, INFO, STATES, OUT = check, info, states, out
    import pygame
    pygame.init()
    print("Platinum ROM: %s" % ptrom.label(), flush=True)
    from yellowlink import ndsrom
    DATA = ndsrom.PlatinumData(ptrom.ROM)
    core = nds_core.NDSCore(ptrom.ROM, None)
    try:
        scenario_catch(core)
        scenario_miss(core)
        scenario_afk_catch(core)
        scenario_ball_page2(core)
        scenario_free_ball(core)
        scenario_flee(core)
        scenario_trapped_flee(core)
        scenario_start_refused(core)
        scenario_deoxys_form(core)
        scenario_next_mon_blackout(core)
        scenario_perish_dbond(core)
        scenario_den(core)
    finally:
        core.close()
        shutil.rmtree(_TMP, ignore_errors=True)
