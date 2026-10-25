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
    env=dict(os.environ,YL_KEY="test",YL_DATA_DIR=str(tmp/"data"),PYTHONPATH=_os.path.join(_REPO, "server")),
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
def run(n):
    for _ in range(n): A.step(); B.step(); time.sleep(0.001)
run(150)
def walk(app, b, steps):
    for _ in range(steps):
        app.press(b, True); run(16); app.press(b, False); run(2)
print("A pos", A.game.position(), "wide", A.wide, "view", A.view_w, A.view_h, "window", A.screen.get_size())
walk(B, "right", 6); run(40)
print("B pos", B.game.position())
A.render(); pygame.image.save(A.screen, str(tmp/"wide.png"))
# open a menu on A: margins should stay
A.press("start", True); run(4); A.press("start", False); run(60)
A.render(); pygame.image.save(A.screen, str(tmp/"wide_menu.png"))
print("last_wide kept during menu:", A.last_wide is not None, "font", A.game.font_loaded())
print("shots", tmp)
A.shutdown(); B.shutdown(); srv.terminate()
