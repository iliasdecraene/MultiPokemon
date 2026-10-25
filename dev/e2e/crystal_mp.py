"""Crystal multiplayer e2e: two Crystal players + one Yellow player on a local server (real ROMs).

Checks: Crystal players see each other walk (and the Yellow player doesn't see them), sprites arrive,
a Crystal<->Crystal trade with a trade evolution (Kadabra -> Alakazam), and that cross-game trades
are refused. Needs dev/states/{crystal.gbc,crystal_party.state,yellow.gb,room.state}.
Run: python dev/e2e/crystal_mp.py
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
sys.path.insert(0, os.path.join(_REPO, "client"))
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
import pygame
from yellowlink.config import Config
from yellowlink import app as appmod, crystal

PORT = 8796
tmp = pathlib.Path(tempfile.mkdtemp())
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


class Cfg(Config):
    def __init__(self, d, name, rom, gid):
        d.mkdir(parents=True, exist_ok=True); self.d = d
        super().__init__(d / "config.json")
        self.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=rom, seen_help=True,
                    games={gid: rom}, current_game=gid)
    save_path = property(lambda self: self.d / "game.sav")
    resume_path = property(lambda self: self.d / "resume.state")
    @property
    def backup_dir(self):
        p = self.d / "bk"; p.mkdir(exist_ok=True); return p


def make(name, rom, gid, state):
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(Cfg(tmp / name, name, os.path.join(STATES, rom), gid))
    return a


A = make("Ash", "crystal.gbc", "crystal", "crystal_party.state")
B = make("Brock", "crystal.gbc", "crystal", "crystal_party.state")
Y = make("Misty", "yellow.gb", "yellow", "room.state")
apps = (A, B, Y)


def run(n, cond=None):
    for _ in range(n):
        for a in apps:
            a.step()
        time.sleep(0.001)
        if cond and cond():
            return True
    return False


def friend(app, name):
    return next((f for f in app.friends.values() if f.name == name), None)


run(300, lambda: friend(A, "Brock") and friend(A, "Brock").state and friend(A, "Brock").sprite)
fb = friend(A, "Brock")
check("Ash sees Brock online with a Crystal state", fb and fb.state and fb.state.get("game") == "crystal")
check("Brock's sprite arrived (384 bytes)", fb and fb.sprite and len(fb.sprite) == 384)
before = B.game.position()
B.press("left", True); run(40); B.press("left", False); run(80)
after = B.game.position()
print("Brock moved", before, "->", after)
run(60, lambda: friend(A, "Brock").state.get("x") == after["x"])
check("Ash gets Brock's new position", friend(A, "Brock").state.get("x") == after["x"] != before["x"])
check("same map id on both sides", friend(A, "Brock").state.get("map") == A.game.map_id())
A.render()
tags = A.draw_friends(A.pyboy.screen.ndarray)
check("Ash draws Brock on screen", any(t[0] == "Brock" for t in tags))
A.render(); pygame.image.save(A.screen, str(tmp / "ash_sees_brock.png"))
Y.render()
check("Misty (Yellow) does not draw Crystal players", not any(t[0] in ("Ash", "Brock") for t in Y.draw_friends(Y.pyboy.screen.ndarray)))
check("Misty (Yellow) is not 'nearby' for Ash", not A.is_nearby(Y.cfg["uid"]))

# give Brock a Kadabra in slot 0
kad = crystal.make_mon(B.rom, 64, 20, [93, 50], B.game.player_id(), "BROCK", nick=None)
B.game.write_party_slot(0, kad)
run(200)

A.net.send({"type": "trade_request", "to": B.cfg["uid"]})
run(150, lambda: B.mode == "prompt")
check("Brock gets the trade prompt", B.mode == "prompt")
time.sleep(0.6)
B.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y))
run(150, lambda: A.mode == "trade" and B.mode == "trade")
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))   # offer slot 0 (Cyndaquil)
B.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))   # offer slot 0 (Kadabra)
run(80)
check("Ash sees Brock's offer as KADABRA", A.trade and A.trade.their_mon and A.trade.their_mon.species == 64)
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)); B.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c))
run(400, lambda: A.trade is None and B.trade is None)
pa = A.game.party(); pb = B.game.party()
print("Ash party", [(A.rom.species_name(m.species), m.level, m.max_hp) for m in pa])
print("Brock party", [(B.rom.species_name(m.species), m.level) for m in pb])
check("Ash received Kadabra and it evolved into Alakazam", pa[0].species == 65)
base = A.rom.base_stats(65)
check("Alakazam's stats were recalculated", crystal.calc_stats(pa[0].data, base)[0] == pa[0].max_hp)
check("Brock received Cyndaquil", pb[0].species == 155)
check("Ash's game accepted the party (checked read)", A.game.party_checked() is not None)

# cross-game: Misty (Yellow) asks Ash (Crystal) to trade -> refused automatically
Y.net.send({"type": "trade_request", "to": A.cfg["uid"]})
run(150)
check("cross-game trade request is declined", A.mode != "prompt" and Y.trade is None)
e = next(r for r in A.roster if r["uid"] == Y.cfg["uid"])
A.action_trade(e)
check("Trade button explains the game mismatch", any("another game" in t[0] for t in A.toasts))

for a in apps:
    a.shutdown()
print("\nfiles in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
