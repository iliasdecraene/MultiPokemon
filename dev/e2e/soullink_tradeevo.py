"""Trade evolutions in a Soul Link run (SPEC.md section 7) on the real ROMs, two clients + a local server.

  yellow:   Ilias's Kadabra (linked with Alex's Pidgey): Alex first says no, then yes -> Alakazam in Ilias's game
            (same key, still linked, ledger `evo` on both sides); the game itself shows ALAKAZAM in battle; it faints
            for real -> dead, and Alex's linked Pidgey dies too and goes to his PC
  crystal:  Onix holding a Metal Coat -> Steelix (Metal Coat used up); Kadabra holding an Everstone can't be picked;
            Steelix faints for real -> Alex's linked Pidgey dies and is boxed
  platinum: (one DeSmuME per process: the partner is a protocol client posting its referee's events)
            Kadabra -> Alakazam (plain) and Onix + Metal Coat -> Steelix (item), both by the real flow; the partner's
            linked mon dies -> our Steelix is forced into the PC (real box write); our Alakazam faints in a real battle
            -> dead with the same key, the partner's referee would kill its linked mon; blackout ends the run
Test shortcuts (RAM writes, marked): the party mons that evolve by trade (species/item/nickname written over the
fixture's mons) registered as gifts, low HP + harmless moves for the faints, Poké Balls (Platinum).
Run: python dev/e2e/soullink_tradeevo.py [yellow|crystal|platinum]   (no argument: all three, one process each)
Needs dev/states/: yellow.gb, room.state, soullink_route1_grass.state, crystal.gbc, crystal_party.state,
crystal_sl_route29.state, platinum.nds, pt_starter.dsst, pt_battle.dsst.
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, uuid
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
PHASES = ("yellow", "crystal", "platinum")

if __name__ == "__main__" and len(sys.argv) < 2:
    bad = []
    for ph in PHASES:
        print(f"\n===== {ph} =====", flush=True)
        if subprocess.call([sys.executable, os.path.abspath(__file__), ph]) != 0:
            bad.append(ph)
    print("\nALL PHASES PASS" if not bad else f"\nFAILED PHASES: {bad}")
    sys.exit(1 if bad else 0)

PHASE = sys.argv[1]
tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"yl_tevo_{PHASE}_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  YL_PT_ROM=vanilla (default) | sinnoh: the Sinnoh ROM the app / DS core runs
print("Sinnoh ROM:", ptrom.label())
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO, "dev", "crystal"))
sys.path.insert(0, os.path.join(_REPO, "server"))
import pygame
pygame.quit = lambda: None
from yellowlink import __version__, app as appmod, crystal, game, pk4, text
from yellowlink.config import Config
from yellowlink.net import NetClient
from yellowlink.soullink import SoulLink

FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


def _free_port():
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


PORT = _free_port()
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)],
                       cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)


def finish():
    print("\nscreens in", tmp)
    print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)


def ledger_evos(app):
    return [e for e in app.sl.ledger if e.get("t") == "evo"]


# =====================================================================================================================
# Game Boy phases: two real clients
# =====================================================================================================================
def gb_setup(game_id, rom_file, boot_state, fixture, rules=None):
    rom = os.path.join(STATES, rom_file)

    def make_cfg(name):
        d = tmp / name
        d.mkdir(parents=True, exist_ok=True)
        cfg = Config(d / "config.json")
        cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=rom, seen_help=True,
                   games={game_id: rom}, current_game=game_id)
        return cfg

    ca, cb = make_cfg("Ilias"), make_cfg("Alex")
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, boot_state)
    A, B = appmod.App(ca), appmod.App(cb)

    def run(n, cond=None):
        for _ in range(n):
            A.step(); B.step(); time.sleep(0.002)
            if cond and cond():
                return True
        return False

    run(300, lambda: A.net.status == "online" and B.net.status == "online" and len(A.roster) == 2)
    A.new_challenge_form(cb["uid"], "Alex")
    A.chal_form["game"] = game_id
    A.chal_form.update(rules or {})
    A.send_challenge()
    run(300, lambda: B.prompt and B.prompt.get("kind") == "challenge")
    time.sleep(0.6)
    B.respond_challenge(True)
    run(300, lambda: A.enter_challenge and B.enter_challenge)
    check(f"{game_id} Soul Link started", bool(A.enter_challenge and A.enter_challenge["game"] == game_id))
    for app, cfg in ((A, ca), (B, cb)):
        app.shutdown()
        cfg["active_challenge"] = app.enter_challenge
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, fixture)
    A, B = appmod.App(ca), appmod.App(cb)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return A, B, ca, cb


def gb_run(A, B):
    def run(n, cond=None):
        for _ in range(n):
            A.step(); B.step(); time.sleep(0.002)
            if cond and cond():
                return True
        return False
    return run


class Driven:
    """A PyBoy proxy: every emulated frame also runs both apps' network + referees (like App.step does)."""

    def __init__(self, me, other):
        self.pb, self.me, self.other, self.n = me.pyboy, me, other, 0

    def tick(self, *a, **k):
        r = self.pb.tick(*a, **k)
        self.me.handle_net()
        self.me.chal_step()
        self.n += 1
        if self.n % 4 == 0:
            self.other.step()
        return r

    def __getattr__(self, name):
        return getattr(self.pb, name)


def trade_evolve(A, B, run, opt, answer=True, label=""):
    """The real flow: Ilias asks from the Soul Link panel's picker, Alex answers the prompt (Y/N)."""
    A.toggle_panel("soullink")
    A.open_tradeevo_picker()
    A.render()
    pygame.image.save(A.screen, str(tmp / f"picker{label}.png"))
    A.request_tradeevo(opt)
    check(f"request sent, Ilias waits (evo_wait){label}", A.mode == "evo_wait" and A.evo_out is not None,
          (A.mode, A.toasts[-1:] if A.toasts else None))
    A.render()
    pygame.image.save(A.screen, str(tmp / f"waiting{label}.png"))
    ok = run(600, lambda: B.prompt and B.prompt.get("kind") == "tradeevo" and B.mode == "prompt")
    check(f"Alex gets the prompt{label}", ok, (B.prompt, B.mode))
    B.render()
    pygame.image.save(B.screen, str(tmp / f"prompt{label}.png"))
    time.sleep(0.6)
    B._answer_prompt(answer)
    run(900, lambda: A.evo_out is None)
    run(60)


def phase_yellow():
    A, B, ca, cb = gb_setup("yellow", "yellow.gb", "room.state", "soullink_route1_grass.state")
    # TEST SHORTCUT: a Kadabra in Ilias's party (a Pikachu copy with other DVs) and a Pidgey in Alex's, as gifts
    pika_a, pika_b = A.game.party()[0], B.game.party()[0]
    d = bytearray(pika_a.data)
    d[0], d[5], d[6], d[7], d[27], d[28] = 0x26, 0x18, 0x18, 100, 0x5A, 0x3C
    kad = game.Mon(bytes(d), pika_a.ot, text.encode("KADABRA"))
    d = bytearray(pika_b.data)
    d[0], d[5], d[6], d[7], d[27], d[28] = 0x24, 0x00, 0x02, 255, 0x77, 0x71
    pidgey = game.Mon(bytes(d), pika_b.ot, text.encode("PIDGEY"))
    for app, first, second in ((A, kad, pika_a), (B, pika_b, pidgey)):
        m = app.game.m
        m[game.PARTY_COUNT] = 2
        app.game.write_party_slot(0, first)
        app.game.write_party_slot(1, second)
        m[game.PARTY_SPECIES + 2] = 0xFF
        for mon, area in ((first, None), (second, None)):
            dex = app.rom.dex_no(mon.species)
            area = "PALLET_TOWN" if dex == 25 else "TEST_GIFT_2"
            app._chal_send({"t": "enc", "area": area, "dex": dex, "status": "caught",
                            "key": game.mon_key(mon.data), "gift": True})
    run = gb_run(A, B)
    run(500, lambda: A.chal_wait is None and B.chal_wait is None and A.referee and B.referee)
    run(120)
    kkey, pkey = game.mon_key(kad.data), game.mon_key(pidgey.data)
    check("Kadabra linked with Alex's Pidgey", A.sl.partner_of(kkey) == pkey, A.sl.mine())
    check("Poké Balls -> rules on", A.sl.mine()["balls"] and B.sl.mine()["balls"])
    opts = A.tradeevo_options()
    check("picker lists Kadabra -> Alakazam", [(o.slot, o.name, o.into_name, o.ok) for o in opts or []]
          == [(0, "Kadabra", "Alakazam", True)], opts)

    trade_evolve(A, B, run, opts[0], answer=False, label="_no")
    check("Alex said no: nothing changed", A.game.party()[0].species == 0x26 and not ledger_evos(A)
          and any("said no" in t[0] for t in A.toasts), [t[0] for t in A.toasts][-3:])

    # asking again right away: the server says "wait a moment" (cooldown after a no), quietly - no question for Alex
    declined_at = time.time()
    A.request_tradeevo(A.tradeevo_options()[0])
    run(300, lambda: A.evo_out is None)
    check("asking again right after a no: 'wait a moment', Alex isn't asked", A.evo_out is None
          and B.prompt is None and "wait a moment" in A.toasts[-1][0], [t[0] for t in A.toasts][-2:])
    while time.time() - declined_at < 21:              # (server EVO_COOLDOWN = 20 s)
        run(30)
    trade_evolve(A, B, run, A.tradeevo_options()[0])
    new = A.game.party()[0]
    check("Kadabra evolved into Alakazam in Ilias's game", new.species == 0x95 and new.nick_str() == "ALAKAZAM"
          and A.game.m[game.PARTY_SPECIES] == 0x95, (new.species, new.nick_str()))
    check("same Soul Link key, same OT name + ID", game.mon_key(new.data) == kkey and new.ot == kad.ot
          and new.data[12:14] == kad.data[12:14])
    check("still linked with Alex's Pidgey", A.sl.partner_of(kkey) == pkey and A.sl.status(kkey) == "linked")
    ev = ledger_evos(A)
    check("ledger `evo` on both sides", len(ev) == 1 and ev[0]["key"] == kkey and ev[0]["into"] == 65
          and len(ledger_evos(B)) == 1, ev)
    note = [t[0] for t in B.toasts if "evolved into" in t[0]]
    check("Alex is told", note and note[-1] == "Ilias's Kadabra evolved into Alakazam!", note)
    check("Ilias is told", any("Kadabra evolved into Alakazam!" == t[0] for t in A.toasts), A.toasts[-3:])
    check("Alakazam's stats are Alakazam's", new.max_hp > kad.max_hp, (new.max_hp, kad.max_hp))
    check("pairs list shows Alakazam", any(r[5] == 65 for r in A.sl.pairs()) and any(r[6] == 65 for r in B.sl.pairs()))
    A.toggle_panel("soullink"); A.render(); pygame.image.save(A.screen, str(tmp / "pairs_after.png")); A.close_panel()
    B.render(); pygame.image.save(B.screen, str(tmp / "alex_after.png"))

    # it faints for real
    import soullink_mem_yellow as sm
    import pc_actions as pa
    m = A.pyboy.memory
    m[game.PARTY_MONS + 1], m[game.PARTY_MONS + 2] = 0, 1          # TEST SHORTCUT: Alakazam at 1 HP, only GROWL
    m[game.PARTY_MONS + 8], m[game.PARTY_MONS + 9], m[game.PARTY_MONS + 10], m[game.PARTY_MONS + 11] = 45, 0, 0, 0
    m[game.PARTY_MONS + 29] = 40
    d = Driven(A, B)
    assert sm.step_until_battle(d, ("up", "down")), "no wild encounter"
    pa._advance_until(d, sm.in_battle_menu, what="battle menu")
    check("the game sends out ALAKAZAM", sm.has(d, "ALAKAZAM"))
    pygame.image.save((A.render(), A.screen)[1], str(tmp / "battle_alakazam.png"))
    for _ in range(15):
        pa._advance_until(d, lambda p: sm.in_battle_menu(p) or sm.has(p, "Use next")
                          or p.memory[game.IS_IN_BATTLE] == 0, what="faint")
        if not sm.in_battle_menu(d):
            break
        sm.battle_menu_select(d, 0)
        pa._advance_until(d, lambda p: sm.has(p, "TYPE/"), what="move list")
        pa._press(d, "a")
    check("Alakazam fainted in a real battle", sm.has(d, "Use next"))
    run(30)
    check("...dead in the ledger by its (unchanged) key", A.sl.status(kkey) == "dead")
    pa._press(d, "a")
    pa._advance_until(d, lambda p: sm.has(p, "Bring out which"), what="party menu")
    pa._move_cursor(d, 1)
    pa._press(d, "a")
    pa._advance_until(d, sm.in_battle_menu, what="battle menu")
    for _ in range(10):
        sm.battle_menu_select(d, 3)
        pa._advance_until(d, lambda p: sm.in_battle_menu(p) or p.memory[game.IS_IN_BATTLE] == 0, what="run")
        if d.memory[game.IS_IN_BATTLE] == 0:
            break
    sm.finish_battle(d)
    run(1500, lambda: pkey in [game.mon_key(r) for r in B.game.current_box_records()]
        and kkey in [game.mon_key(r) for r in A.game.current_box_records()])
    check("Alex's linked Pidgey died with it", B.sl.mine()["dead"].get(pkey) == "partner fainted", B.sl.mine()["dead"])
    check("...and both went to the PCs", kkey in [game.mon_key(r) for r in A.game.current_box_records()]
          and pkey in [game.mon_key(r) for r in B.game.current_box_records()],
          ([x.nick_str() for x in A.game.party()], [x.nick_str() for x in B.game.party()]))
    A.shutdown(); B.shutdown()


def phase_crystal():
    import soullink_verify as sv
    import play
    A, B, ca, cb = gb_setup("crystal", "crystal.gbc", "crystal_party.state", "crystal_sl_route29.state",
                            rules={"dupes": False})
    # TEST SHORTCUT: Ilias's Pidgey becomes an Onix holding a Metal Coat, his Sentret a Kadabra holding an Everstone
    cyn, pid, sen = A.game.party()
    d = bytearray(pid.data)
    d[0], d[1] = 95, crystal.METAL_COAT
    onix = crystal.Mon(bytes(d), pid.ot, text.encode("ONIX"))
    d = bytearray(sen.data)
    d[0], d[1] = 64, crystal.EVERSTONE
    kad = crystal.Mon(bytes(d), sen.ot, text.encode("KADABRA"))
    A.game.write_party_slot(1, onix)
    A.game.write_party_slot(2, kad)
    GIFT_AREAS = ["NEW_BARK_TOWN", "TEST_GIFT_2", "TEST_GIFT_3"]
    for app in (A, B):
        for mon, area in zip(app.game.party(), GIFT_AREAS):
            app._chal_send({"t": "enc", "area": area, "dex": mon.species, "status": "caught",
                            "key": crystal.mon_key(mon.data), "gift": True})
    run = gb_run(A, B)
    run(500, lambda: A.chal_wait is None and B.chal_wait is None and A.referee and B.referee)
    run(120)
    okey = crystal.mon_key(onix.data)
    bkeys = [crystal.mon_key(m.data) for m in B.game.party()]
    check("Onix linked with Alex's Pidgey", A.sl.partner_of(okey) == bkeys[1])
    opts = A.tradeevo_options() or []
    check("picker: Onix (Metal Coat) can, Kadabra (Everstone) can't",
          [(o.name, o.into_name, o.ok, o.why) for o in opts]
          == [("Onix", "Steelix", True, ""), ("Kadabra", "Alakazam", False, "holds an Everstone")], opts)
    A.request_tradeevo(opts[1])
    run(60)
    check("Everstone: the request isn't even sent", A.evo_out is None and A.mode == "play"
          and B.prompt is None and "Everstone" in A.toasts[-1][0], A.toasts[-1:])
    trade_evolve(A, B, run, opts[0])
    new = A.game.party()[1]
    check("Onix evolved into Steelix, the Metal Coat is used up", new.species == 208 and new.item == 0
          and A.game.r(crystal.PARTY_SPECIES + 1) == 208, (new.species, new.item))
    check("same key + OT, nickname follows the species", crystal.mon_key(new.data) == okey and new.ot == onix.ot
          and new.nick_str() == "STEELIX")
    check("still linked", A.sl.partner_of(okey) == bkeys[1] and A.sl.status(okey) == "linked")
    ev = ledger_evos(B)
    check("ledger `evo` with the item", len(ev) == 1 and ev[0].get("item") == "Metal Coat" and ev[0]["into"] == 208,
          ev)
    check("Alex is told", any("Ilias's Onix evolved into Steelix! (Metal Coat used up)" == t[0] for t in B.toasts),
          [t[0] for t in B.toasts][-3:])
    check("Kadabra with the Everstone unchanged", A.game.party()[2].species == 64)

    # Steelix faints for real (TEST SHORTCUT: lead, 1 HP, only LEER)
    party = A.game.party()
    st = bytearray(party[1].data)
    st[34], st[35] = 0, 1
    st[2:6] = bytes([43, 0, 0, 0])
    st[23] = 30
    A.game.write_party_slot(0, crystal.Mon(bytes(st), party[1].ot, party[1].nick))
    A.game.write_party_slot(1, party[0])
    run(60)
    d = Driven(A, B)
    fainted, seen = False, []
    for _ in range(6):
        if not A.game.in_battle():
            if not sv.walk_until_battle(d, A.game):
                print("no battle; pos", A.game.position(), "safe", A.game.is_safe(), "mode", A.mode, A.chal_wait)
                break
        assert sv.to_battle_menu(d)
        seen.append(play.screen_has(d, "STEELIX"))
        for _turn in range(30):
            if play.screen_has(d, "Use next") or not A.game.in_battle():
                break
            if play.screen_has(d, "FIGHT"):
                sv.battle_menu(d, "FIGHT")
                play.ticks(d, 20)
                play.choose(d, "LEER")
            for _f in range(240):
                d.tick(1, False)
                if play.screen_has(d, "Use next") or play.screen_has(d, "FIGHT") or not A.game.in_battle():
                    break
            if not play.screen_has(d, "FIGHT") and not play.screen_has(d, "Use next"):
                play.tap(d, "a", 4, 30)
        if play.screen_has(d, "Use next"):
            fainted = True
            break
        sv.finish_battle(d, A.game)
    check("the game sends out STEELIX", any(seen))
    check("Steelix fainted in a real battle", fainted)
    check("...dead by its unchanged key", A.sl.status(okey) == "dead")
    if play.screen_has(d, "Use next") or play.wait_text(d, "Use next", 20, "a", 30):
        assert play.wait_text(d, "YES", 10, "a", 30)
        play.choose(d, "YES")
        play.ticks(d, 30)
        sv.pick_party(d, "CYNDAQUIL")
        sv.run_away(d, A.game)
    run(700, lambda: bkeys[1] in [crystal.mon_key(r) for r in B.game.current_box_records()])
    check("Alex's linked Pidgey died with it and is boxed", B.sl.mine()["dead"].get(bkeys[1]) == "partner fainted"
          and bkeys[1] in [crystal.mon_key(r) for r in B.game.current_box_records()], B.sl.mine()["dead"])
    A.render(); pygame.image.save(A.screen, str(tmp / "ilias_end.png"))
    A.shutdown(); B.shutdown()


# =====================================================================================================================
# Platinum: one DeSmuME per process -> the partner is a protocol client
# =====================================================================================================================
class Partner:
    def __init__(self, name):
        self.name, self.uid = name, str(uuid.uuid4())
        self.inbox, self.online = [], False
        self.net = NetClient(f"ws://127.0.0.1:{PORT}", {"name": name, "uid": self.uid, "key": "test",
                                                        "version": __version__})
        self.net.start()

    def pump(self):
        self.inbox.extend(self.net.poll())
        if self.net.status == "online" and not self.online:
            self.online = True
            self.net.send({"type": "library", "games": ["platinum"], "challenge_games": ["platinum"],
                           "challenge_rules": ["exp_share", "trade_evo"]})

    def take(self, mtype, **match):
        for i, m in enumerate(self.inbox):
            if m.get("type") == mtype and all(m.get(k) == v for k, v in match.items()):
                return self.inbox.pop(i)
        return None

    def events(self):
        return [m["event"] for m in self.inbox if m.get("type") == "challenge_event"]

    def send(self, **msg):
        return self.net.send(msg)

    def event(self, cid, **ev):
        ev.setdefault("cid", uuid.uuid4().hex[:10])
        self.send(type="challenge_event", challenge_id=cid, event=ev)


def phase_platinum():
    from yellowlink import platinum as P
    pt_rom = os.path.join(STATES, "platinum.nds")
    cfg = Config()
    cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=pt_rom, seen_help=True,
               games={"platinum": pt_rom}, current_game="platinum", ds_scale=2)
    cfg.save()
    B = Partner("Alex")
    box = {}

    def boot(state):
        os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
        try:
            return appmod.App(cfg)
        finally:
            os.environ.pop("YL_DEV_LOAD_STATE", None)

    def run(n, cond=None):
        for _ in range(n):
            A.handle_events()
            A.step()
            B.pump()
            if cond and cond():
                return True
        return False

    def wait(cond, secs):
        end = time.time() + secs
        while time.time() < end:
            if run(20, cond):
                return True
        return False

    def tap(x, y, after=60):
        A.nds.touch(x, y)
        run(6)
        A.nds.touch_release()
        run(after)

    def key(k, after=30):
        A.nds.button_press(k)
        run(6)
        A.nds.button_release(k)
        run(after)

    def party():
        """The party records; the game decrypts them in place mid-frame, so a read can miss: try a few frames."""
        for _ in range(120):
            p = A.game.party_checked()
            if p is not None:
                return p
            run(1)
        return []

    def keys():
        return [P.mon_key(r) for r in party()]

    def evo_options():
        for _ in range(120):
            o = A.tradeevo_options()
            if o is not None:
                return o
            run(1)
        return []

    def box_keys():
        return [P.mon_key(r) for r in A.game.current_box_records()]

    def until_overworld(limit=4000, press="b"):
        for _ in range(limit // 40):
            if A.game.battle_info() is None and A.game.is_safe():
                return True
            key(press, 34)
        return A.game.battle_info() is None and A.game.is_safe()

    def walk_until_battle(limit=120):
        for k in range(limit):
            if A.game.battle_info() is not None:
                return True
            A.nds.button_press("left" if k % 2 == 0 else "right")
            run(10)
            A.nds.button_release("left" if k % 2 == 0 else "right")
            run(8)
        return A.game.battle_info() is not None

    def stable_write(addr_fn, recs):
        """TEST SHORTCUT: write party records once the game isn't working on the party (observe_stable)."""
        for _ in range(600):
            if A.game.observe_stable():
                break
            run(1)
        for i, r in enumerate(recs):
            A.game.m.write(addr_fn(i), r)
        A.game.m.w32(A.game.save_body() + P.PAGE_PARTY + 4, len(recs))

    A = boot("pt_starter.dsst")
    run(400, lambda: A.net.status == "online" and A.shared_games(B.uid) == ["platinum"])
    A.new_challenge_form(B.uid, "Alex")
    A.chal_form["dupes"] = False
    A.send_challenge()
    run(300, lambda: box.setdefault("r", B.take("challenge_request")) is not None or box.pop("r") is not None)
    B.send(type="challenge_respond", request_id=(box.get("r") or {}).get("request_id"), accept=True)
    run(300, lambda: A.enter_challenge is not None)
    check("Platinum Soul Link started", bool(A.enter_challenge))
    cid = A.enter_challenge["id"]
    A.shutdown()
    cfg["active_challenge"] = A.enter_challenge
    cfg.save()
    A = boot("pt_battle.dsst")                 # the run starts in a wild battle on Route 201 (before Poké Balls)
    turtwig = party()[0]
    tkey = P.mon_key(turtwig)                  # the starter = Twinleaf Town's gift (before the referee looks)
    A._chal_send({"t": "enc", "area": "twinleaf_town", "dex": 387, "status": "caught", "key": tkey, "gift": True})
    B.send(type="challenge_join", challenge_id=cid)
    wait(lambda: A.chal_wait is None and A.referee is not None, 40)
    check("both in, referee running", A.chal_wait is None and A.referee is not None)
    run(700)
    for _ in range(10):                        # RUN
        if A.game.battle_info() is None:
            break
        tap(128, 178, 120)
    until_overworld()
    # TEST SHORTCUT: Poké Balls + full Pokédex (like soullink_platinum.py), then a Kadabra and an Onix holding a
    # Metal Coat next to the Turtwig (built from the Turtwig's record: new PIDs = other mons), registered as gifts
    body = A.game.save_body()
    A.game.m.w16(body + P.PAGE_BAG + P.BAG_BALLS, 1)
    A.game.m.w16(body + P.PAGE_BAG + P.BAG_BALLS + 2, 5)
    data = A.plat_data
    check("the starter is still the run's Turtwig", P.mon_key(party()[0]) == tkey)
    base = pk4.PK4(pk4.decrypt(turtwig))

    def make(species, pid, item, moves):
        pk = pk4.PK4(bytes(base.d[:pk4.BOX_SIZE]))
        pk.pid, pk.species, pk.item = pid, species, item
        ab = data.abilities(species)
        pk.ability = ab[1] if (ab[1] and pid & 1) else ab[0]
        codes = list(data.species_name_codes(species))[:10]
        pk.nickname_codes = codes + [0xFFFF] + [0] * (10 - len(codes))
        pk.has_nickname = False
        pk.moves = list(moves)
        pk.pp = [data.move_pp(m) for m in moves]
        return pk4.to_party(pk.box_bytes(), data)

    kad = make(64, 0x13572469, 0, (33, 45, 0, 0))                # Tackle, Growl
    onix = make(95, 0x2468ACE1, 233, (33, 0, 0, 0))             # holding a Metal Coat
    kkey, okey = P.mon_key(kad), P.mon_key(onix)
    before = P.pokedex_caught_bits(A.game)
    for area, k, dex in (("test_gift_2", kkey, 64), ("test_gift_3", okey, 95)):
        A._chal_send({"t": "enc", "area": area, "dex": dex, "status": "caught", "key": k, "gift": True})
    stable_write(A.game.party_addr, [kad, turtwig, onix])
    partner = {"twinleaf_town": "b0chimchar0000aa", "test_gift_2": "b1abra000000aa01",
               "test_gift_3": "b1geodude0000a02"}
    for area, k in partner.items():
        B.event(cid, t="enc", area=area, dex=63, status="caught", key=k, gift=True)
    wait(lambda: A.sl.partner_of(okey) == partner["test_gift_3"] and A.sl.mine()["balls"], 30)
    run(120)
    check("Kadabra + Onix linked with the partner's mons", A.sl.partner_of(kkey) == partner["test_gift_2"]
          and A.sl.partner_of(okey) == partner["test_gift_3"])
    check("party as written", keys() == [kkey, tkey, okey], keys())
    opts = evo_options()
    check("picker: Kadabra and Onix (Metal Coat)", [(o.name, o.into_name, o.ok, o.item) for o in opts]
          == [("Kadabra", "Alakazam", True, None), ("Onix", "Steelix", True, "Metal Coat")], opts)

    def evolve(opt, label):
        A.toggle_panel("soullink")
        A.open_tradeevo_picker()
        A.render()
        pygame.image.save(A.screen, str(tmp / f"picker_{label}.png"))
        A.request_tradeevo(opt)
        check(f"{label}: request sent", A.mode == "evo_wait", (A.mode, A.toasts[-1:]))
        req = {}
        wait(lambda: req.setdefault("r", B.take("challenge_evo_request")) is not None or req.pop("r") is not None, 20)
        r = req.get("r") or {}
        check(f"{label}: the partner is asked", r.get("key") == opt.key and r.get("into") == opt.into, r)
        B.send(type="challenge_evo_respond", challenge_id=cid, evo_id=r.get("evo_id"), accept=True)
        wait(lambda: A.evo_out is None, 40)
        run(60)
        return r

    evolve(opts[0], "kadabra")
    recs = party()
    ala = pk4.PK4(pk4.decrypt(recs[0])) if recs else None
    old = pk4.PK4(pk4.decrypt(kad))
    check("Kadabra -> Alakazam in the game's party", ala is not None and ala.species == 65
          and ala.nickname == "ALAKAZAM" and ala.item == 0, ala and (ala.species, ala.nickname))
    check("same key/PID/OT, same level, Alakazam's stats and ability", ala is not None and P.mon_key(recs[0]) == kkey
          and ala.ot_codes == old.ot_codes and ala.party_level == old.party_level
          and ala.stats == pk4.calc_stats(ala, data, old.party_level)
          and ala.ability == data.abilities(65)[0x13572469 & 1 if data.abilities(65)[1] else 0])
    evolve([o for o in evo_options() if o.name == "Onix"][0], "onix")
    recs = party()
    st = pk4.PK4(pk4.decrypt(recs[2])) if recs else None
    check("Onix + Metal Coat -> Steelix, item used up", st is not None and st.species == 208 and st.item == 0
          and P.mon_key(recs[2]) == okey and st.nickname == "STEELIX", st and (st.species, st.item))
    evs = [e for e in B.events() if e.get("t") == "evo"]
    check("partner got both `evo` ledger events", [(e["key"], e["into"], e.get("item")) for e in evs]
          == [(kkey, 65, None), (okey, 208, "Metal Coat")], evs)
    check("still linked after evolving", A.sl.partner_of(kkey) == partner["test_gift_2"]
          and A.sl.partner_of(okey) == partner["test_gift_3"] and A.sl.status(okey) == "linked")
    caught = P.pokedex_caught_bits(A.game)
    has = lambda bits, n: bits is not None and bits[(n - 1) // 8] >> ((n - 1) % 8) & 1   # noqa: E731
    check("Pokédex: Alakazam + Steelix caught now (weren't before)", not has(before, 65) and not has(before, 208)
          and has(caught, 65) and has(caught, 208))
    run(300)
    check("the game keeps the evolved records (300 frames later)", [pk4.PK4(pk4.decrypt(r)).species
                                                                   for r in party()]
          == [65, 387, 208])
    A.toggle_panel("soullink")
    A.render()
    pygame.image.save(A.screen, str(tmp / "pairs_after.png"))
    A.close_panel()

    # partner's linked mons die -> our Steelix and Turtwig go to the PC (real box writes of an evolved record)
    B.event(cid, t="dead", key=partner["test_gift_3"], why="fainted")
    B.event(cid, t="dead", key=partner["twinleaf_town"], why="fainted")
    wait(lambda: okey in box_keys() and tkey in box_keys(), 60)
    check("Steelix dead (partner fainted) and in the PC", A.sl.mine()["dead"].get(okey) == "partner fainted"
          and okey in box_keys() and okey not in keys(), (keys(), box_keys()))
    srec = next((r for r in A.game.current_box_records() if P.mon_key(r) == okey), None)
    check("...its box record is a Steelix", srec is not None and pk4.PK4(pk4.decrypt(srec)).species == 208)
    check("party = Alakazam alone", keys() == [kkey], keys())

    # our Alakazam faints in a real battle -> dead by the same key -> the partner's referee kills its linked mon
    addr = A.game.party_addr(0)
    pk = pk4.PK4(pk4.decrypt(A.game.read(addr, pk4.PARTY_SIZE)))
    pk.hp = 1                                  # TEST SHORTCUT: 1 HP
    A.game.m.write(addr, pk.encrypted())
    run(30)
    check("next battle found", walk_until_battle())
    run(700)
    A.render()
    pygame.image.save(A.screen, str(tmp / "battle.png"))
    for turn in range(12):
        if A.mode == "chal_popup" or A.game.battle_info() is None:
            break
        tap(128, 88, 90)                       # FIGHT
        tap(192, 50, 700)                      # move 2 (Growl: no damage, the wild mon hits back)
        if A.sl.status(kkey) == "dead":
            break
    check("Alakazam fainted for real -> dead by its unchanged key", A.sl.mine()["dead"].get(kkey) == "fainted",
          A.sl.mine()["dead"])
    for _ in range(120):
        if A.mode == "chal_popup":
            break
        key("a", 30)
    run(120)
    mine = B.events()
    check("partner saw it", any(e.get("t") == "dead" and e.get("key") == kkey for e in mine))
    view = SoulLink(B.uid, cfg["uid"], {"dupes": False}, mine)
    check("the partner's referee kills its linked mon", {"t": "dead", "key": partner["test_gift_2"],
                                                         "why": "partner fainted"} in view.needed_deaths(),
          view.needed_deaths())
    check("blackout ends the run (no usable Pokémon left)", A.sl.mine()["blackout"])
    A.chal_close_popup()
    A.shutdown()
    B.net.stop()


{"yellow": phase_yellow, "crystal": phase_crystal, "platinum": phase_platinum}[PHASE]()
finish()
