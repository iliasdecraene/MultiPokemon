"""Soul Link on the real Crystal ROM: two clients in a challenge, real battles and Bill's PC by button presses.

  1. both on Route 29 with balls; party Cyndaquil/Pidgey/Sentret registered as linked gifts
  2. Ilias catches his first Route 29 encounter, Alex runs from his -> Ilias's catch has no partner: boxed
  3. Alex's Cyndaquil faints for real -> Ilias's Cyndaquil dies too and is boxed
  4. PC rule: Ilias deposits Pidgey at the Cherrygrove PC for real -> Alex's Pidgey goes to his PC
Needs dev/states/crystal_sl_route29.state (made by dev/crystal/soullink_verify.py).
Run: python dev/e2e/soullink_crystal.py
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "crystal"))
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config
from yellowlink import app as appmod, crystal
import soullink_verify as sv
import play

PORT = 8792
ROM = os.path.join(STATES, "crystal.gbc")
FIX = os.path.join(STATES, "crystal_sl_route29.state")
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def make_cfg(name):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = Config(d / "config.json")
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=ROM, seen_help=True,
               games={"crystal": ROM}, current_game="crystal")
    return cfg


ca, cb = make_cfg("Ilias"), make_cfg("Alex")
os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "crystal_party.state")
A, B = appmod.App(ca), appmod.App(cb)


def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.002)
        if cond and cond():
            return True
    return False


run(200, lambda: A.net.status == "online" and B.net.status == "online" and len(A.roster) == 2)
A.new_challenge_form(cb["uid"], "Alex")
A.chal_form["game"] = "crystal"
A.chal_form["dupes"] = False      # the fixture party already has Pidgey + Sentret, both common on Route 29
A.send_challenge()
run(200, lambda: B.prompt and B.prompt.get("kind") == "challenge")
time.sleep(0.6)
B.respond_challenge(True)
run(200, lambda: A.enter_challenge and B.enter_challenge)
check("Crystal Soul Link started", bool(A.enter_challenge and A.enter_challenge["game"] == "crystal"))
for app, cfg in ((A, ca), (B, cb)):
    app.shutdown()
    cfg["active_challenge"] = app.enter_challenge
os.environ["YL_DEV_LOAD_STATE"] = FIX                     # test shortcut: start on Route 29 with a party + balls
A, B = appmod.App(ca), appmod.App(cb)
os.environ.pop("YL_DEV_LOAD_STATE", None)
GIFT_AREAS = ["NEW_BARK_TOWN", "TEST_GIFT_2", "TEST_GIFT_3"]   # pretend each starting mon was a separate gift
for app in (A, B):
    for mon, area in zip(app.game.party(), GIFT_AREAS):
        app._chal_send({"t": "enc", "area": area, "dex": mon.species, "status": "caught",
                        "key": crystal.mon_key(mon.data), "gift": True})
run(400, lambda: A.chal_wait is None and B.chal_wait is None and A.referee and B.referee)
check("both in, referees running", A.chal_wait is None and B.chal_wait is None and bool(A.referee and B.referee))
run(60)
keys = lambda app: [crystal.mon_key(m.data) for m in app.game.party()]
cyn_a, pid_a, _ = keys(A)
cyn_b, pid_b, _ = keys(B)
check("starting mons linked", A.sl.partner_of(cyn_a) == cyn_b and A.sl.partner_of(pid_a) == pid_b)
area = A.referee.view.area()
check("on Route 29", A.referee.view.area_name(area) == "Route 29", area)


class Driven:
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


def settle(frames=300):
    for _ in range(frames):
        A.step(); B.step(); time.sleep(0.001)


# 2. first encounter: Ilias catches, Alex runs
d = Driven(A, B)
assert sv.walk_until_battle(d, A.game)
sv.throw_ball(d, "MASTER BALL")
sv.finish_battle(d, A.game)
settle()
enc = A.sl.mine()["enc"].get(area, {})
check("Ilias's Route 29 catch logged", enc.get("status") == "caught", enc)
caught_a = enc.get("key")
d = Driven(B, A)
assert sv.walk_until_battle(d, B.game)
sv.run_away(d, B.game)
settle()
check("Alex's Route 29 encounter: failed", B.sl.mine()["enc"].get(area, {}).get("status") == "failed")
check("Ilias's catch has no partner -> dead + boxed", A.sl.mine()["dead"].get(caught_a) == "partner missed"
      and caught_a in [crystal.mon_key(r) for r in A.game.current_box_records()]
      and caught_a not in keys(A))

# 3. Alex's Cyndaquil faints for real
B.game.w(crystal.PARTY_MONS + 34, 0)
B.game.w(crystal.PARTY_MONS + 35, 1)
d = Driven(B, A)
fainted = False
for _ in range(6):
    if not B.game.in_battle():
        if not sv.walk_until_battle(d, B.game):
            print("no battle; pos", B.game.position(), "safe", B.game.is_safe(), "mode", B.mode, B.chal_wait)
            print("\n".join(play.screen_text(d)))
            raise SystemExit(1)
    assert sv.to_battle_menu(d)
    for _turn in range(30):
        if play.screen_has(d, "Use next") or not B.game.in_battle():
            break
        if play.screen_has(d, "FIGHT"):
            sv.battle_menu(d, "FIGHT")
            play.ticks(d, 20)
            play.choose(d, "LEER")
        for _f in range(240):
            d.tick(1, False)
            if play.screen_has(d, "Use next") or play.screen_has(d, "FIGHT") or not B.game.in_battle():
                break
        if not play.screen_has(d, "FIGHT") and not play.screen_has(d, "Use next"):
            play.tap(d, "a", 4, 30)
    if play.screen_has(d, "Use next"):
        fainted = True
        break
    sv.finish_battle(d, B.game)
check("Cyndaquil fainted in a real battle", fainted)
check("...and is dead in the ledger right away", B.sl.status(cyn_b) == "dead")
if not play.screen_has(d, "Use next") and not play.wait_text(d, "Use next", 20, "a", 30):
    print("dead why:", B.sl.mine()["dead"], "in battle", B.game.in_battle(), "party hp", [m.hp for m in B.game.party()])
    print("\n".join(play.screen_text(d)))
    raise SystemExit(1)
assert play.wait_text(d, "YES", 10, "a", 30)
play.choose(d, "YES")
play.ticks(d, 30)
sv.pick_party(d, "PIDGEY")
sv.run_away(d, B.game)
settle(500)
check("Ilias's Cyndaquil died with it", A.sl.mine()["dead"].get(cyn_a) == "partner fainted")
check("both dead Cyndaquil are in the PCs",
      cyn_a in [crystal.mon_key(r) for r in A.game.current_box_records()] and cyn_a not in keys(A)
      and cyn_b in [crystal.mon_key(r) for r in B.game.current_box_records()] and cyn_b not in keys(B),
      (keys(A), keys(B)))

# 4. PC rule with the real Bill's PC
d = Driven(A, B)
play.pc_open(d)
check("PC open seen by the referee", A.referee.pc_open)
play.pc_deposit(d, "PIDGEY")
play.pc_close(d)
settle(600)
check("Ilias's own deposit is logged (loc box)", any(e["t"] == "loc" and e["key"] == pid_a and e["where"] == "box"
                                                     for e in A.sl.ledger))
check("Alex's Pidgey followed into his PC", pid_b in [crystal.mon_key(r) for r in B.game.current_box_records()]
      and pid_b not in keys(B), keys(B))
check("Alex's game is still fine (party readable)", B.game.party_checked() is not None)
B.render(); pygame.image.save(B.screen, str(tmp / "alex.png"))
A.toggle_panel("soullink"); A.render(); pygame.image.save(A.screen, str(tmp / "ilias_panel.png"))
A.shutdown(); B.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
