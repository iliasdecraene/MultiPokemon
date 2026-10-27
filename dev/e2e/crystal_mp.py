"""Crystal multiplayer e2e: two Crystal players + one Yellow player on a local server (real ROMs).

Checks: Crystal players see each other walk (and the Yellow player doesn't see them), sprites arrive.
(Trades are PC-database based since 0.6.1: dev/e2e/pctrade_e2e.py.) Needs dev/states/{crystal.gbc,crystal_party.state,yellow.gb,room.state}.
Run: python dev/e2e/crystal_mp.py
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
sys.path.insert(0, os.path.join(_REPO, "client"))
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
import pygame
from yellowlink.config import Config
from yellowlink import app as appmod

PORT = 8796
tmp = pathlib.Path(tempfile.mkdtemp())
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
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

# trades/gifts are PC-database based and work across games now: see dev/e2e/pctrade_e2e.py
for a in apps:
    a.shutdown()
print("\nfiles in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
