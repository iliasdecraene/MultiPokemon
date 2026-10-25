"""Soul Link on the real Yellow ROM: two clients in a challenge, real battles driven by button presses.

  1. both on Route 1 with Poké Balls (starter Pikachu = linked gift from Pallet Town)
  2. Ilias catches his first Route 1 encounter, Alex catches his -> linked pair
  3. Ilias's second Route 1 encounter: caught anyway -> doesn't count, goes to the PC
  4. Ilias's Pikachu faints for real -> Alex's Pikachu dies too and is moved to Alex's PC
  5. Ilias's last usable mon faints -> blackout -> challenge failed, both get the pop-up
Needs dev/states/soullink_route1_grass.state (made by dev/e2e/soullink_mem_yellow.py).
Run: python dev/e2e/soullink_yellow.py
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config
from yellowlink import app as appmod, game
import pc_actions as pa
import soullink_mem_yellow as sm

PORT = 8793
ROM = os.path.join(STATES, "yellow.gb")
FIX = os.path.join(STATES, "soullink_route1_grass.state")
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
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
               games={"yellow": ROM}, current_game="yellow")
    return cfg


ca, cb = make_cfg("Ilias"), make_cfg("Alex")
os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "room.state")
A, B = appmod.App(ca), appmod.App(cb)


def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.002)
        if cond and cond():
            return True
    return False


# --- start a challenge through the real protocol --------------------------------------------------------------
run(200, lambda: A.net.status == "online" and B.net.status == "online" and len(A.roster) == 2)
A.new_challenge_form(cb["uid"], "Alex")
A.send_challenge()
run(200, lambda: B.prompt and B.prompt.get("kind") == "challenge")
time.sleep(0.6)
B.respond_challenge(True)
run(200, lambda: A.enter_challenge and B.enter_challenge)
cid = A.enter_challenge["id"]
for app, cfg in ((A, ca), (B, cb)):
    app.shutdown()
    cfg["active_challenge"] = app.enter_challenge
# Test shortcut: both runs start on Route 1 with Pikachu and Poké Balls (instead of playing the intro).
os.environ["YL_DEV_LOAD_STATE"] = FIX
A, B = appmod.App(ca), appmod.App(cb)
os.environ.pop("YL_DEV_LOAD_STATE", None)                # let autosave/rules run normally
# The starter came from Oak's Lab: register it as the Pallet Town gift (the fixture skips that part).
for app in (A, B):
    pika = app.game.party()[0]
    app._chal_send({"t": "enc", "area": "PALLET_TOWN", "dex": 25, "status": "caught",
                    "key": game.mon_key(pika.data), "gift": True})
run(400, lambda: A.chal_wait is None and B.chal_wait is None and A.referee and B.referee)
check("both in the challenge with a referee", A.chal_wait is None and B.chal_wait is None and bool(A.referee))
run(60)
check("starters are linked", A.sl.partner_of(game.mon_key(A.game.party()[0].data)) is not None)
check("Poké Balls -> rules on (balls event)", A.sl.mine()["balls"] and B.sl.mine()["balls"])


class Driven:
    """A PyBoy proxy: every emulated frame also runs both apps' network + referees (like App.step does)."""

    def __init__(self, me, other):
        self.pb, self.me, self.other = me.pyboy, me, other
        self.n = 0

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


def fight_first(d):
    """FIGHT -> the (only) move."""
    sm.battle_menu_select(d, 0)
    pa._advance_until(d, lambda p: sm.has(p, "TYPE/"), what="move list")
    pa._press(d, "a")


def settle(app, other, frames=200):
    for _ in range(frames):
        app.step(); other.step(); time.sleep(0.001)


def events(t=None, who=None):
    led = A.sl.ledger
    return [e for e in led if (t is None or e["t"] == t) and (who is None or e["p"] == who)]


def wild_battle(app, other, action):
    d = Driven(app, other)
    assert sm.step_until_battle(d, ("up", "down")), "no wild encounter"
    pa._advance_until(d, sm.in_battle_menu, what="battle menu")
    dex = app.game.battle_info()["dex"]
    if action == "catch":
        sm.use_item_in_battle(d, 1)                        # Master Ball
    else:
        for _ in range(10):                                # run (may take a few tries)
            sm.battle_menu_select(d, 3)
            pa._advance_until(d, lambda p: sm.in_battle_menu(p) or p.memory[game.IS_IN_BATTLE] == 0,
                              what="run result")
            if d.memory[game.IS_IN_BATTLE] == 0:
                break
    sm.finish_battle(d)
    settle(app, other)
    return dex


# 2. first encounters on Route 1
dex_a = wild_battle(A, B, "catch")
enc = A.sl.mine()["enc"].get("ROUTE_1", {})
check("Ilias: first Route 1 encounter caught and logged", enc.get("status") == "caught" and enc.get("dex") == dex_a,
      enc)
mon_a = A.game.party()[-1]
key_a = game.mon_key(mon_a.data)
check("Ilias's catch is in his party and usable", enc.get("key") == key_a and A.sl.usable(key_a))
wild_battle(B, A, "catch")
settle(A, B)
check("Alex's Route 1 catch is linked to Ilias's", A.sl.partner_of(key_a) is not None and A.sl.status(key_a) == "linked")

# 3. a second Route 1 encounter doesn't count
wild_battle(A, B, "catch")
settle(A, B, 300)
third = [m for m in A.game.party() if game.mon_key(m.data) not in (key_a, game.mon_key(A.game.party()[0].data))]
box_keys = [game.mon_key(r) for r in A.game.current_box_records()]
illegal = [e for e in events("dead", ca["uid"]) if e["why"] == "not first encounter"]
check("second Route 1 catch is marked as not counting", len(illegal) == 1)
check("...and was moved to Ilias's PC", illegal and illegal[0]["key"] in box_keys and not third,
      (box_keys, [m.nick_str() for m in A.game.party()]))

# 4. Ilias's Pikachu faints for real
m = A.pyboy.memory
pika_a_key = game.mon_key(A.game.party()[0].data)
pika_b_key = game.mon_key(B.game.party()[0].data)
m[game.PARTY_MONS + 1], m[game.PARTY_MONS + 2] = 0, 1          # setup: Pikachu at 1 HP
m[game.PARTY_MONS + 8], m[game.PARTY_MONS + 9] = 45, 0         # setup: only GROWL, so it can't win
m[game.PARTY_MONS + 29] = 40
d = Driven(A, B)
assert sm.step_until_battle(d, ("up", "down"))
for _ in range(15):
    pa._advance_until(d, lambda p: sm.in_battle_menu(p) or sm.has(p, "Use next") or p.memory[game.IS_IN_BATTLE] == 0,
                      what="faint")
    if not sm.in_battle_menu(d):
        break
    fight_first(d)
check("Pikachu fainted in a real battle", sm.has(d, "Use next"))
settle(A, B, 30)
check("Ilias's Pikachu is dead in the ledger right away", A.sl.status(pika_a_key) == "dead")
# send the next mon and run
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
settle(A, B, 400)
check("Alex's linked Pikachu died with it", B.sl.status(pika_b_key) == "dead"
      and B.sl.mine()["dead"].get(pika_b_key) == "partner fainted")
check("dead Pikachus were moved to both PCs",
      pika_a_key in [game.mon_key(r) for r in A.game.current_box_records()]
      and pika_b_key in [game.mon_key(r) for r in B.game.current_box_records()]
      and all(game.mon_key(x.data) != pika_b_key for x in B.game.party()),
      ([x.nick_str() for x in A.game.party()], [x.nick_str() for x in B.game.party()]))
pygame.image.save((A.render(), A.screen)[1], str(tmp / "after_faint.png"))
A.toggle_panel("soullink"); A.render(); pygame.image.save(A.screen, str(tmp / "soullink_panel.png")); A.close_panel()

# 5. blackout: Ilias's last usable mon faints
check("Ilias has one mon left", len(A.game.party()) == 1)
m[game.PARTY_MONS + 1], m[game.PARTY_MONS + 2] = 0, 1
m[game.PARTY_MONS + 8], m[game.PARTY_MONS + 9], m[game.PARTY_MONS + 10], m[game.PARTY_MONS + 11] = 45, 0, 0, 0
d = Driven(A, B)
assert sm.step_until_battle(d, ("up", "down"))
for _ in range(15):
    pa._advance_until(d, lambda p: sm.in_battle_menu(p) or p.memory[game.IS_IN_BATTLE] == 0, what="blackout")
    if not sm.in_battle_menu(d):
        break
    fight_first(d)
for _ in range(1500):
    d.tick()
    if A.mode == "chal_popup":
        break
settle(A, B, 200)
check("blackout logged", len(events("blackout")) == 1)
check("challenge failed for both", A.challenges[cid]["status"] == "failed" and B.challenges[cid]["status"] == "failed",
      A.challenges[cid].get("reason"))
check("both get the 'failed' pop-up", A.mode == "chal_popup" and B.mode == "chal_popup"
      and "failed" in A.chal_popup["title"])
B.render(); pygame.image.save(B.screen, str(tmp / "failed_popup.png"))
A.shutdown(); B.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
