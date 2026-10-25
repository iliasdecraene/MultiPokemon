# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import os, sys, time, subprocess, tempfile, pathlib, json
os.environ["SDL_VIDEODRIVER"]="dummy"; os.environ["SDL_AUDIODRIVER"]="dummy"
os.environ["YL_DEV_LOAD_STATE"]=_os.path.join(STATES, "room.state")
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
        self.update(name=name, server_url="ws://127.0.0.1:8799", room_key="test", rom_path=ROM, seen_help=True)
    @property
    def save_path(self): return self.d/"yellow.sav"
    @property
    def backup_dir(self):
        p=self.d/"bk"; p.mkdir(exist_ok=True); return p

A=appmod.App(Cfg(tmp/"a","Ash")); B=appmod.App(Cfg(tmp/"b","Brock"))
def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.002)
        if cond and cond(): return True
    return False
def shot(app, name):
    app.render(); pygame.image.save(app.screen, str(tmp/name)); print("shot", tmp/name)

run(200)
import numpy as np
def tap(app, b, hold=4, wait=40):
    app.press(b, True); run(hold); app.press(b, False); run(wait)
tap(B, "up", 18, 60)                         # Brock at (3,6), right above Ash (3,7)
print("B pos", B.game.position()["x"], B.game.position()["y"])
tap(B, "start", 4, 60)                       # Brock opens his START menu
f = list(A.friends.values())[0]
print("Brock in menu -> visible flag at A:", f.state.get("visible"), "| B font loaded:", B.game.font_loaded())
A.render(); arr = A.pyboy.screen.ndarray
raw = pygame.image.frombuffer(arr.tobytes(), (160,144), "RGBA")
drawn = pygame.surfarray.array3d(A.game_surf).swapaxes(0,1); rawa = arr[:,:,:3]
print("A draws Brock while Brock is in a menu:", (drawn[44:60,64:80] != rawa[44:60,64:80]).any())
shot(A, "a_no_menu.png")
tap(A, "start", 4, 60)                       # now Ash opens his menu too
A.render(); arr = A.pyboy.screen.ndarray
drawn = pygame.surfarray.array3d(A.game_surf).swapaxes(0,1)
print("A font loaded:", A.game.font_loaded(), "| Brock still drawn with Ash's menu open:", (drawn[44:60,64:80] != arr[44:60,64:80,:3]).any(),
      "| menu area untouched:", (drawn[0:100,84:160] == arr[0:100,84:160,:3]).all())
shot(A, "a_menu.png")
A.shutdown(); B.shutdown(); srv.terminate()
