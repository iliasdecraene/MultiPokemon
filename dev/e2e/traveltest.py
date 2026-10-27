# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import os, sys, time, subprocess, tempfile, pathlib
os.environ["SDL_VIDEODRIVER"]="dummy"; os.environ["SDL_AUDIODRIVER"]="dummy"
os.environ["YL_DEV_LOAD_STATE"]=_os.path.join(STATES, "room.state")
sys.path.insert(0,_os.path.join(_REPO, "client"))
import pygame
from yellowlink.config import Config
from yellowlink import app as appmod, game, text
from yellowlink.game import Mon
tmp=pathlib.Path(tempfile.mkdtemp())
srv=subprocess.Popen([sys.executable,"-m","yellowlink_server","--port","8797"],cwd=_os.path.join(_REPO, "server"),
    env=dict(os.environ, YL_RAIDS="off", YL_KEY="test",YL_DATA_DIR=str(tmp/"data"),PYTHONPATH=_os.path.join(_REPO, "server")),
    stdout=open(tmp/"srv.log","w"),stderr=subprocess.STDOUT)
time.sleep(1.5)
class Cfg(Config):
    def __init__(self, d, name):
        d.mkdir(parents=True, exist_ok=True); self.d=d
        super().__init__(d/"config.json")
        self.update(name=name, server_url="ws://127.0.0.1:8797", room_key="test", rom_path=ROM, seen_help=True)
    @property
    def save_path(self): return self.d/"yellow.sav"
    @property
    def backup_dir(self):
        p=self.d/"bk"; p.mkdir(exist_ok=True); return p
import shutil
os.environ.pop("YL_DEV_LOAD_STATE", None)
from yellowlink import library
d = tmp / "t"
class TCfg(Cfg):
    @property
    def save_path(self): return self.d / f"{self.game_id}.sav"
    @property
    def resume_path(self): return self.d / f"{self.game_id}.state"
c = TCfg(d, "Traveller"); c["auto_resume"] = True
c["games"] = {"yellow": ROM, "red": ROM}        # stand-in: same file registered as a second game
c["current_game"] = "yellow"
shutil.copy(_os.path.join(STATES, "yellow.sav"), c.save_path)
A = appmod.App(c)
for _ in range(60): A.step()
A.render()
print("start:", A.game_id, "full:", A.full, "pos", A.game.position()["x"], A.game.position()["y"])
A.travel("red")
print("travel flag:", A.travel_to, "quit requested:", A.quit_requested, "events->", A.handle_events())
A.shutdown()
library.Library(c, appmod.app_dir()).set_current("red")
B = appmod.App(c)
print("now:", B.game_id, "full:", B.full, "hooks:", B.hooks is not None, "wv:", B.wv is not None, "save file:", c.save_path.name)
errors = 0
for i in range(900):
    if i % 10 == 0: B.pyboy.button_press("a")
    if i % 10 == 3: B.pyboy.button_release("a")
    try:
        B.step(); B.render()
    except Exception as e:
        errors += 1; print("ERR", e)
B.toggle_panel("multiplayer"); B.render(); B.toggle_panel("regions"); B.render()
print("ran 900 frames in the 'other game' with", errors, "errors; panels render ok")
B.shutdown()
print("yellow snapshot kept:", (d / "yellow.state").exists(), "| red snapshot:", (d / "red.state").exists())
library.Library(c, appmod.app_dir()).set_current("yellow")
C = appmod.App(c); [C.step() for _ in range(5)]
print("back in yellow:", C.game_id, C.full, "pos", C.game.position()["x"], C.game.position()["y"])
C.shutdown(); srv.terminate()
