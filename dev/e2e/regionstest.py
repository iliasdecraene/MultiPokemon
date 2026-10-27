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
os.environ["YL_DEV_LOAD_STATE"] = _os.path.join(STATES, "room.state")
def shot(c, name):
    A = appmod.App(c); [A.step() for _ in range(5)]
    A.toggle_panel("regions"); A.render()
    pygame.image.save(A.screen, str(tmp / name))
    labels = [l for _, _, l in A.click_targets]
    A.shutdown(); return labels
c1 = TCfg(tmp / "one", "Solo"); c1["games"] = {"yellow": ROM}; c1["current_game"] = "yellow"
print("yellow only:", [l for l in shot(c1, "regions_one.png") if l in ("Travel",) or l.startswith("version:")])
c2 = TCfg(tmp / "two", "Duo"); c2["games"] = {"yellow": ROM, "red": ROM}; c2["current_game"] = "yellow"
labels = shot(c2, "regions_two.png")
print("yellow+red:", [l for l in labels if l in ("Travel",) or l.startswith("version:")])
print("shots", tmp); srv.terminate()
