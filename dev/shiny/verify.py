"""Verify the game side of the shiny alerts (SPEC.md section 10) on the real ROMs.

  Yellow   real Route 1 encounters: EncounterWatch (polled every 15 frames like the app, several phases) settles
           before the battle menu on the reading the game keeps; a shiny planted into wEnemyMonDVs on the battle's
           first frame -> one alert with the right species/area (ShinyMixin on the real GameMemory); the enemy HUD
           check (enemy_hud_shown) and that the caught-ball tile (1, 1) is blank whenever the HUD shows; the
           Pokédex-owned flag; a trainer battle never alerts.
  Crystal  real Route 29 encounters + a planted shiny (wEnemyMonDVs on the first loaded frame); trainer battle.
  Platinum real Route 202 encounters + a shiny planted into the wild mon's PID (FieldBattleDTO party, re-encrypted);
           a double battle with the SECOND wild mon shiny; trainer battle; our pygame mixer + the jingle while
           DeSmuME runs.

Needs dev/states/{yellow.gb, crystal.gbc, platinum.nds} and the Soul Link states (soullink_*.state,
crystal_sl_*.state, pt_sl_*.dsst). Never writes into dev/states. pret symbols: YL_PRET_SYMS (default
/home/claude/pret-syms). Usage: python dev/shiny/verify.py [yellow] [crystal] [platinum]
"""
import importlib.util
import io
import os
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="ylshiny_verify_")   # DeSmuME's own battery file
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in ("client", "dev/e2e", "dev/crystal", "dev/platinum"):
    sys.path.insert(0, os.path.join(REPO, p))
SYMS = os.environ.get("YL_PRET_SYMS", "/home/claude/pret-syms")
PRET_Y = os.environ.get("YL_PRET_YELLOW", "/home/claude/pokeyellow")

from yellowlink import badges, crystal, game, pk4, shiny  # noqa: E402
from yellowlink import platinum as P  # noqa: E402

RESULTS = []
SHINY_DVS = bytes([0xEA, 0xAA])


def ok(cond, what):
    RESULTS.append((bool(cond), what))
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    return bool(cond)


def note(s):
    print("     " + s, flush=True)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def syms(name):
    path = os.path.join(SYMS, name)
    if not os.path.exists(path):
        return None
    out = {}
    for line in open(path):
        parts = line.split()
        if line.startswith(";") or len(parts) < 2 or ":" not in parts[0]:
            continue
        bank, addr = parts[0].split(":")
        out.setdefault(parts[1], (int(bank, 16), int(addr, 16)))
    return out


class Poller:
    """The app's polling: EncounterWatch fed every shiny.POLL_EVERY frames (starting at `phase`)."""

    def __init__(self, reader, phase=0):
        self.reader, self.f = reader, phase
        self.w = shiny.EncounterWatch()
        self.settled = None
        self.at = None
        self.frames = 0

    def tick(self):
        if self.f % shiny.POLL_EVERY == 0:
            r = self.w.feed(self.reader())
            if r is not None:
                self.settled, self.at = r, self.frames
        self.f += 1
        self.frames += 1


class FakeNet:
    status = "online"

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return True


def mixin_app(gm, G, game_id, dex_name):
    """ShinyMixin on a real adapter: what the App does every frame, minus the window."""
    app = shiny.ShinyMixin()
    app.cfg = {"uid": "me"}
    app.net, app.game, app.G, app.game_id = FakeNet(), gm, G, game_id
    app.full, app.chal, app.live, app.battle = True, None, None, None
    app.server_features = {"shiny_alert"}
    app.frame = 0
    app.lines, app.jingles = [], 0
    app.dex_name = dex_name
    app.sys_msg = lambda s, *a: app.lines.append(s)

    def jingle():
        app.jingles += 1
    app.play_shiny_jingle = jingle
    app.init_shiny()

    def step():
        app.shiny_step()
        app.frame += 1
    app.step = step
    return app


# =====================================================================================================================
def yellow():
    print("=== Yellow ===", flush=True)
    Y = load_module("sl_yellow", os.path.join(REPO, "dev", "e2e", "soullink_mem_yellow.py"))
    RD = Y.RD
    t = syms("pokeyellow.sym")
    if t:
        pairs = [("wEnemyMonNick", game.ENEMY_MON_NICK), ("wTileMap", game.TILEMAP), ("wPokedexOwned", game.DEX_OWNED),
                 ("wIsInBattle", game.IS_IN_BATTLE), ("wEnemyMonDVs", game.ENEMY_MON_DVS),
                 ("wEnemyMonSpecies2", game.ENEMY_MON_SPECIES2)]
        bad = [(lab, hex(a)) for lab, a in pairs if t.get(lab, (0, -1))[1] != a]
        ok(not bad, "pret pokeyellow symbols: wEnemyMonNick CFD9, wTileMap C3A0, wPokedexOwned, wIsInBattle, "
           "wEnemyMonDVs, wEnemyMonSpecies2 %s" % (bad or ""))
    else:
        ok(True, "pokeyellow.sym missing - symbol checks SKIPPED")
    asm_path = os.path.join(PRET_Y, "engine", "battle", "draw_hud_pokeball_gfx.asm")
    if os.path.exists(asm_path):
        asm = open(asm_path).read()
        ok("db $74 ; lower-left corner tile of the HUD" in asm and "hlcoord 1, 2" in asm,
           "pret: the enemy HUD's lower-left corner tile is $74 (PlaceEnemyHUDTiles from (1, 2) -> corner at (1, 3))")
    else:
        ok(True, "pret/pokeyellow checkout missing - HUD tile source check SKIPPED")

    def name_of(dex):
        return RD.species_name(RD.species_for_dex(dex))

    base = io.BytesIO()
    pb, g = Y.emu("soullink_route1_grass.state")
    pb.save_state(base)
    settled_ok = hud_ok = blank_ok = intro_ok = 0
    trials = 6
    for trial in range(trials):
        base.seek(0)
        pb.load_state(base)
        Y.tick(pb, 3 + 11 * trial)
        if not Y.step_until_battle(pb, ("up", "down")):
            note("trial %d: no encounter" % trial)
            continue
        poll = Poller(g.battle_info, phase=trial * 4)
        poll.tick()
        huds = []

        def sample():
            poll.tick()
            shown = g.enemy_hud_shown()
            huds.append((shown, g.read(game.TILEMAP + 20 + 1, 3)))
            return shown
        Y.advance_record(pb, Y.in_battle_menu, sample)
        final = g.battle_info()
        dvs = g.read(game.ENEMY_MON_DVS, 2)
        settled_ok += (poll.settled == final and poll.settled["kind"] == "wild"
                       and poll.settled["shiny"] == badges.gen1_is_shiny(dvs)
                       and shiny.wild_shinies(poll.settled) == ([final["dex"]] if final["shiny"] else []))
        hud_ok += bool(huds) and huds[-1][0] and g.enemy_hud_shown()
        blank_ok += all(row == b"\x7f\x7f\x7f" for shown, row in huds if shown)
        intro_ok += not huds[0][0]
        if trial == 0:
            note("first: %s (%s) settled after %d frames of %d to the menu" % (name_of(final["dex"]), final,
                                                                              poll.at, len(huds)))
    ok(settled_ok == trials, "%d real encounters: the watch settles before the battle menu on the reading the game "
       "keeps (kind wild, shiny = Gen-2 rule on the final DVs) (%d/%d)" % (trials, settled_ok, trials))
    ok(intro_ok == trials and hud_ok == trials,
       "enemy_hud_shown(): False on the battle's first frames (intro), True at the battle menu (%d/%d, %d/%d)"
       % (intro_ok, trials, hud_ok, trials))
    ok(blank_ok == trials, "whenever the enemy HUD shows, tiles (1..3, 1) are blank (0x7F): the caught ball "
       "covers nothing (%d/%d)" % (blank_ok, trials))

    # a planted shiny on frame 0 -> ShinyMixin: one shiny_found, banner, jingle; owned flag
    base.seek(0)
    pb.load_state(base)
    Y.tick(pb, 7)
    ok(Y.step_until_battle(pb, ("up", "down")), "encounter for the planted shiny")
    g.write(game.ENEMY_MON_DVS, SHINY_DVS)
    dex = RD.dex_no(pb.memory[game.ENEMY_MON_SPECIES2])
    owned_before = g.dex_owned(dex)
    app = mixin_app(g, game, "yellow", name_of)
    Y.advance_record(pb, Y.in_battle_menu, app.step)
    for _ in range(60):
        pb.tick()
        app.step()
    area = game.area_name(game.area_of(g.map_id()))
    want = shiny.found_message(shiny.species_label(name_of(dex)), dex, area, "yellow")
    ok(app.net.sent == [want] and app.jingles == 1 and app.banners.current_title() ==
       "A shiny %s appeared!" % shiny.species_label(name_of(dex)),
       "shiny DVs EA AA on frame 0: exactly one %s, banner + jingle" % want)
    ok(area == "Route 1", "area name of the battle's map: %r" % area)
    ok(app.wild_owned is owned_before and not owned_before,
       "%s not in wPokedexOwned -> no caught ball" % name_of(dex))
    snap = io.BytesIO()
    pb.save_state(snap)
    m = pb.memory
    m[game.DEX_OWNED + (dex - 1) // 8] |= 1 << ((dex - 1) % 8)
    app2 = mixin_app(g, game, "yellow", name_of)
    for _ in range(60):
        pb.tick()
        app2.step()
    ok(app2.wild_owned and g.enemy_hud_shown(), "with its owned bit set: wild_owned True (a fresh app joining "
       "mid-battle settles too) and the HUD shows")
    ok(app2.net.sent == [want], "... and that app alerts once as well (one alert per app and battle)")
    # catch it: the HUD goes away (Pokédex page, nickname) and the battle ends -> the flags reset
    snap.seek(0)
    pb.load_state(snap)
    app.wild_owned = True
    shown_log = []
    Y.use_item_in_battle(pb, 1)                             # MASTER BALL

    def frame():
        pb.tick()
        app.step()
        shown_log.append(g.enemy_hud_shown())
    for f in range(8000):                                   # Y.finish_battle, sampling every frame
        if not g.in_battle() and not g.font_loaded():
            break
        button = "b" if Y.has(pb, "YES", "NO") and not Y.pa._prompt_arrow(pb) else ("a" if f % 20 == 0 else None)
        if button:
            pb.button_press(button)
            for _ in range(3):
                frame()
            pb.button_release(button)
            for _ in range(4):
                frame()
        else:
            frame()
    ok(Y.finish_battle(pb), "caught it with the game's MASTER BALL")
    for _ in range(shiny.POLL_EVERY * (shiny.END_READS + 1)):
        pb.tick()
        app.step()
    ok(False in shown_log and not g.enemy_hud_shown() and not app.wild_owned and not app.shiny_watch.in_battle,
       "after the catch: HUD gone (%d of %d frames shown), wild_owned False, battle over for the watch"
       % (sum(shown_log), len(shown_log)))
    ok(len(app.net.sent) == 1, "no second alert for the same battle")
    pb.stop(save=False)

    pb, g = Y.emu("soullink_trainer_battle.state")
    g.write(game.ENEMY_MON_DVS, SHINY_DVS)
    app = mixin_app(g, game, "yellow", name_of)
    for _ in range(200):
        pb.tick()
        app.step()
    ok(app.shiny_watch.done and app.net.sent == [] and not app.wild_owned,
       "trainer battle with shiny-looking DVs: settled as a trainer battle, no alert, no ball")
    pb.stop(save=False)


# =====================================================================================================================
def crystal_checks():
    print("=== Crystal ===", flush=True)
    C = load_module("sl_crystal", os.path.join(REPO, "dev", "crystal", "soullink_verify.py"))
    from common import ROM as CROM, ticks
    rom = crystal.RomData.from_file(CROM)

    def load(st):
        pb = C.load(st)
        cm = crystal.CrystalMemory(pb.memory, rom)
        ticks(pb, 2)
        return pb, cm

    def name_of(dex):
        return rom.species_name(dex)

    pb, cm = load("crystal_sl_route29.state")
    base = C.snapshot(pb)
    good = 0
    trials = 5
    for trial in range(trials):
        C.restore(pb, base)
        ticks(pb, trial * 7 % 53)
        poll = Poller(cm.battle_info, phase=trial * 5)
        assert C.walk_until_battle(pb, cm, period=40 + trial % 17)
        for _ in range(240):
            poll.tick()
            pb.tick(1, False)
        final = cm.battle_info()
        good += (poll.settled == final and final["kind"] == "wild" and poll.at is not None and poll.at < 60
                 and shiny.wild_shinies(final) == ([final["dex"]] if final["shiny"] else []))
        if trial == 0:
            note("first: %s %s, settled after %d frames" % (name_of(final["dex"]), final, poll.at))
    ok(good == trials, "%d real Route 29 encounters: settled within 4 polls on the battle's own reading (%d/%d)"
       % (trials, good, trials))

    C.restore(pb, base)
    assert C.walk_until_battle(pb, cm)
    pb.tick(1, False)                                        # frame 1: the wild mon is loaded
    cm.write(crystal.ENEMY_MON_DVS, SHINY_DVS)
    app = mixin_app(cm, crystal, "crystal", name_of)
    for _ in range(240):
        app.step()
        pb.tick(1, False)
    dex = cm.battle_info()["dex"]
    area = crystal.area_name(crystal.area_of(cm.map_id()))
    want = shiny.found_message(shiny.species_label(name_of(dex)), dex, area, "crystal")
    ok(app.net.sent == [want] and app.jingles == 1 and not app.wild_owned,
       "shiny DVs on the first loaded frame: one %s (no caught ball outside Yellow)" % want)
    ok(area == "Route 29", "area name %r" % area)
    pb.stop(False)

    pb, cm = load("crystal_sl_trainer.state")
    cm.write(crystal.ENEMY_MON_DVS, SHINY_DVS)
    app = mixin_app(cm, crystal, "crystal", name_of)
    for _ in range(200):
        app.step()
        pb.tick(1, False)
    ok(app.shiny_watch.done and app.net.sent == [], "trainer battle with shiny DVs: no alert")
    pb.stop(False)


# =====================================================================================================================
def make_shiny_at(d, at):
    """Rewrite the party record at `at` (236 bytes, the game's encryption) with a PID shiny for its own TID/SID."""
    from yellowlink import platinum_pc
    _k, rec = platinum_pc.normalise(d.read(at, 136))
    pk = pk4.PK4(pk4.decrypt(bytes(rec) + d.read(at + 136, 100)))
    lo = pk.pid & 0xFFFF
    pk.pid = ((lo ^ pk.tid ^ pk.sid) << 16) | lo
    d.write(at, pk.encrypted())
    return pk.pid


def platinum_checks():
    print("=== Platinum ===", flush=True)
    S = load_module("sl_platinum", os.path.join(REPO, "dev", "platinum", "soullink_verify.py"))
    rig = S.Rig()
    pm, d = rig.pm, rig.d

    def name_of(dex):
        return rig.rom.species_name(dex)

    rig.load("pt_sl_route202.dsst")
    good = 0
    for n in range(3):
        poll = Poller(pm.battle_info, phase=n * 6)
        rig.walk_to_battle(on=poll.tick)
        rig.wait_menu(on=poll.tick)
        final = pm.battle_info()
        good += (poll.settled == final and final["kind"] == "wild"
                 and shiny.wild_shinies(final) == ([final["dex"]] if final["shiny"] else []))
        note("wild %s %s settled after %s frames" % (name_of(final["dex"]), final["key"], poll.at))
        rig.run_away()
    ok(good == 3, "3 real Route 202 encounters: settled on the battle's own reading (%d/3)" % good)

    # planted: the wild mon's PID made shiny in the FieldBattleDTO right at the battle's first frame
    state = {"pid": None}
    app = mixin_app(pm, P, "platinum", name_of)

    def on():
        if state["pid"] is None and pm.battle_dto():
            dto = pm.battle_dto()
            party = d.r32(dto + P.DTO_PARTIES + 4)
            state["pid"] = make_shiny_at(d, party + 8)
        app.step()
    rig.walk_to_battle(on=on)
    rig.wait_menu(on=on)
    info = pm.battle_info()
    area = P.area_name(P.area_of(pm.map_id()))
    want = shiny.found_message(shiny.species_label(name_of(info["dex"])), info["dex"], area, "platinum")
    ok(info["shiny"] and info["pid"] == state["pid"] and app.net.sent == [want] and app.jingles == 1,
       "shiny PID %08X planted on the battle's first frame: one %s" % (state["pid"] or 0, want))
    ok(area == "Route 202", "area name %r" % area)
    rig.run_away()

    rig.load("pt_sl_double.dsst")
    info = pm.battle_info()
    ok(info and info["kind"] == "wild" and len(info["keys"]) == 2, "double wild battle state: two wild mons %s"
       % (info and info["dexes"]))
    dto = pm.battle_dto()
    at = None
    for battler in (1, 3):
        party = d.r32(dto + P.DTO_PARTIES + 4 * battler)
        n = d.r32(party + 4) if P._ptr(party) else 0
        for slot in range(min(n, 6)):
            if pm._party_mon(party, slot) is not None:
                at = party + 8 + slot * P.PARTY_MON_SIZE      # the last wild mon = the second one
    make_shiny_at(d, at)
    app = mixin_app(pm, P, "platinum", name_of)
    for _ in range(60):
        app.step()
        d.run(1)
    info = pm.battle_info()
    ok(not info["shiny"] and shiny.wild_shinies(info) == [info["dexes"][1]]
       and app.net.sent and app.net.sent[0]["dex"] == info["dexes"][1],
       "double battle, only the SECOND wild mon shiny: battle_info()['shiny'] (first mon) False, the alert names "
       "the second (%s)" % (app.net.sent and app.net.sent[0]["species"]))

    rig.load("pt_sl_trainer.dsst")
    dto = pm.battle_dto()
    if dto:
        make_shiny_at(d, d.r32(dto + P.DTO_PARTIES + 4) + 8)
    app = mixin_app(pm, P, "platinum", name_of)
    for _ in range(60):
        app.step()
        d.run(1)
    ok(pm.battle_info()["kind"] == "trainer" and app.shiny_watch.done and app.net.sent == [],
       "trainer battle with a shiny mon: no alert")

    # our mixer + jingle while DeSmuME plays its own sound
    import pygame
    from yellowlink.audio import AudioOut
    pygame.init()
    out = AudioOut(60)
    snd = shiny.jingle_sound()
    played = out.play_fx(snd)
    d.run(30)
    ok(out.ok and snd is not None and played and out.fx_channel.get_busy(),
       "DS: our pygame mixer opens next to DeSmuME, the jingle plays on its own channel (mixer %s)"
       % (pygame.mixer.get_init(),))
    out.close()


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or ["yellow", "crystal", "platinum"]
    for name, fn in (("yellow", yellow), ("crystal", crystal_checks), ("platinum", platinum_checks)):
        if name in names:
            fn()
    bad = [w for c, w in RESULTS if not c]
    print("\n%d checks, %d failed" % (len(RESULTS), len(bad)))
    if bad:
        for w in bad:
            print("FAILED: " + w)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
