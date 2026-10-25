# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import os, sys, time, shutil, tempfile, pathlib
os.environ["SDL_VIDEODRIVER"]="dummy"; os.environ["SDL_AUDIODRIVER"]="dummy"
sys.path.insert(0,_os.path.join(_REPO, "client"))
import pygame
from yellowlink.config import Config
from yellowlink import app as appmod, game
tmp=pathlib.Path(tempfile.mkdtemp())
class Cfg(Config):
    def __init__(self, d, name):
        d.mkdir(parents=True, exist_ok=True); self.d=d
        super().__init__(d/"config.json")
        self.update(name=name, server_url="ws://127.0.0.1:1", room_key="x", rom_path=ROM, seen_help=True)
    @property
    def save_path(self): return self.d/"yellow.sav"
    @property
    def backup_dir(self):
        p=self.d/"bk"; p.mkdir(exist_ok=True); return p
c=Cfg(tmp/"a","Ash"); shutil.copy(_os.path.join(STATES, "yellow.sav"), c.save_path)
t=time.time(); A=appmod.App(c); print(f"boot took {time.time()-t:.1f}s")
for _ in range(30): A.step()
print("in overworld after 30 frames:", A.game.is_safe(), "map", A.game.m[game.CUR_MAP], "xy", A.game.position()["x"], A.game.position()["y"])
A.render()
bar=A.volume_bar; print("slider rect:", bar, "initial volume", round(A.audio.volume*100))
pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(bar.x + bar.w//4, bar.centery)))
pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(bar.x + bar.w//4, bar.centery)))
A.handle_events(); print("after click at 25%:", round(A.audio.volume*100), "saved cfg:", c["volume"])
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_EQUALS)); print("after '+':", round(A.audio.volume*100))
A.render(); pygame.image.save(A.screen, str(tmp/"boot.png")); print("shot", tmp/"boot.png")
A.shutdown()
c2=Cfg(tmp/"b","New"); B=appmod.App(c2)
print("no save -> at title/intro (not in game):", not B.game.in_game())
B.shutdown()
