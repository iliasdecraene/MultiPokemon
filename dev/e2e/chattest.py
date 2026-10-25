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
    env=dict(os.environ,YL_KEY="test",YL_DATA_DIR=str(tmp/"data"),PYTHONPATH=_os.path.join(_REPO, "server")),
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
# per-test resume dir so we never touch the real app folder
_orig_init = Cfg.__init__
Cfg.resume_path = property(lambda self: self.d / "resume.state")

def click(app, label):
    app.render()
    for rect, cb, lab in reversed(app.click_targets):
        if lab == label:
            pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center))
            app.handle_events(); return True
    print("   no target", label); return False
os.environ["YL_DEV_LOAD_STATE"] = _os.path.join(STATES, "room.state")
A=appmod.App(Cfg(tmp/"a","Ash")); B=appmod.App(Cfg(tmp/"b","Brock"))
def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.001)
        if cond and cond(): return True
run(200)
print("click chat box:", click(A, "chat input"), "-> mode", A.mode)
for ch in "hello friends!":
    pygame.event.post(pygame.event.Event(pygame.TEXTINPUT, text=ch))
A.handle_events(); print("typed:", repr(A.chat_input))
A.render(); pygame.image.save(A.screen, str(tmp / "chat_typing.png"))
print("click Send:", click(A, "Send"), "-> mode", A.mode)
print("Brock received:", run(100, lambda: any(m == "hello friends!" for _, m, _ in B.chat_log)))
for i in range(30):
    B.net.send({"type": "chat", "text": f"message number {i} from Brock, a bit longer so it wraps onto two lines"})
    run(3)
run(60)
A.render(); pygame.image.save(A.screen, str(tmp / "chat_many.png"))
pygame.mouse.set_pos(A.chat_rect.center) if hasattr(pygame.mouse, "set_pos") else None
A.chat_scroll += 6
A.render(); pygame.image.save(A.screen, str(tmp / "chat_scrolled.png"))
print("scrolled:", A.chat_scroll, "newer-button:", any(l == "newer" for _, _, l in A.click_targets))
click(A, "newer"); print("back to newest:", A.chat_scroll == 0)
print("wide before:", A.wide, A.screen.get_size()); click(A, "Wide view"); print("wide after:", A.wide, A.screen.get_size())
print("click game area while typing unfocuses:", click(A, "chat input"), A.mode, end=" -> ")
pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(50, 200))); A.handle_events(); print(A.mode)
print("shots", tmp)
A.shutdown(); B.shutdown(); srv.terminate()
