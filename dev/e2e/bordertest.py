# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import os, sys, time, subprocess, tempfile, pathlib, json
os.environ["SDL_VIDEODRIVER"]="dummy"; os.environ["SDL_AUDIODRIVER"]="dummy"
os.environ["YL_DEV_LOAD_STATE"]=_os.path.join(STATES, "pallet_after_lab.state")
sys.path.insert(0,_os.path.join(_REPO, "client"))
import pygame
from yellowlink.config import Config
from yellowlink import app as appmod, game
from yellowlink.game import Mon

tmp=pathlib.Path(tempfile.mkdtemp())
srv=subprocess.Popen([sys.executable,"-m","yellowlink_server","--port","8799"],cwd=_os.path.join(_REPO, "server"),
    env=dict(os.environ, YL_RAIDS="off", YL_KEY="test",YL_DATA_DIR=str(tmp/"data"),PYTHONPATH=_os.path.join(_REPO, "server")),
    stdout=open(tmp/"srv.log","w"),stderr=subprocess.STDOUT)
time.sleep(1.5)

class Cfg(Config):
    def __init__(self, d, name):
        d.mkdir(parents=True, exist_ok=True); self.d=d
        super().__init__(d/"config.json")
        self.update(name=name, server_url="ws://127.0.0.1:8799", room_key="test", rom_path=ROM, seen_help=True, wide_view=True, wide_scale=2)
    @property
    def save_path(self): return self.d/"yellow.sav"
    @property
    def backup_dir(self):
        p=self.d/"bk"; p.mkdir(exist_ok=True); return p

A=appmod.App(Cfg(tmp/"a","Ash")); B=appmod.App(Cfg(tmp/"b","Brock"))
sys.path.insert(0, _os.path.join(_REPO, "dev", "worldview"))
from nav import Walker
def run(n):
    for _ in range(n): A.step(); B.step(); time.sleep(0.001)
run(120)
for app, extra in ((A, 0), (B, 4)):
    w = Walker(app.pyboy, lambda: None)
    w.go("left", 3); w.go("up", 10); w.goto(10, 2)
    if extra: w.go("up", extra)
run(120)
pa, pb_ = A.game.position(), B.game.position()
print("A", pa["map"], pa["x"], pa["y"], "| B", pb_["map"], pb_["x"], pb_["y"])
print("Route 1 offset seen from Pallet:", A.wv.neighbor_offset(A.pyboy.memory, pb_["map"]))
tags = A.draw_friends(A.pyboy.screen.ndarray)
print("A draws Brock at:", tags)
A.render(); pygame.image.save(A.screen, str(tmp/"border.png")); print("shot", tmp/"border.png")
A.shutdown(); B.shutdown(); srv.terminate()
