# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import os, sys, time, subprocess, tempfile, pathlib
os.environ["SDL_VIDEODRIVER"]="dummy"; os.environ["SDL_AUDIODRIVER"]="dummy"
sys.path.insert(0,_os.path.join(_REPO, "client"))
import pygame
from yellowlink.config import Config
from yellowlink import app as appmod, game, diag as diagmod
tmp=pathlib.Path(tempfile.mkdtemp())
srv=subprocess.Popen([sys.executable,"-m","yellowlink_server","--port","8798"],cwd=_os.path.join(_REPO, "server"),
    env=dict(os.environ, YL_RAIDS="off", YL_KEY="test",YL_DATA_DIR=str(tmp/"data"),PYTHONPATH=_os.path.join(_REPO, "server")),
    stdout=open(tmp/"srv.log","w"),stderr=subprocess.STDOUT)
time.sleep(1.5)
D=diagmod.Diagnostics(tmp/"appdir")
class Cfg(Config):
    def __init__(self, d, name):
        d.mkdir(parents=True, exist_ok=True); self.d=d
        super().__init__(d/"config.json")
        self.update(name=name, server_url="ws://127.0.0.1:8798", room_key="test", rom_path=ROM)
    @property
    def save_path(self): return self.d/"yellow.sav"
    @property
    def backup_dir(self):
        p=self.d/"bk"; p.mkdir(exist_ok=True); return p
A=appmod.App(Cfg(tmp/"a","Newbie"), D)
print("first-launch mode:", A.mode)
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
# play through the intro using the 'a' key alias (keydown/keyup through handlers)
for _ in range(600): A.step()
for i in range(700):
    k = pygame.K_a if i % 9 else pygame.K_DOWN
    A.on_key(pygame.event.Event(pygame.KEYDOWN, key=k))
    for _ in range(4): A.step()
    A.press(appmod.KEYMAP[k], False)
    for _ in range(8): A.step()
    if A.game.in_game() and A.game.m[game.CUR_MAP]==0x26 and A.game.is_safe() and i>300: break
print("after intro: in_game", A.game.in_game(), "safe", A.game.is_safe(), "name", A.game.player_name(), "net", A.net.status)
# error tolerance: make render blow up a few times
orig=A.render; n={"c":0}
def bad():
    n["c"]+=1
    if n["c"]<4: raise ValueError("synthetic render failure")
    return orig()
A.render=bad
for _ in range(6):
    try:
        A.handle_events(); A.step(); A.render()
    except Exception: A._loop_error()
print("errors counted:", D.errors, "toasts:", [t[0] for t in A.toasts][-1:])
# report text + send via live connection and via sync path
txt=D.report_text("test", "state: "+A.debug_snapshot())
print("report chars:", len(txt), "| has traceback:", "synthetic render failure" in txt, "| has keys:", "key a" in txt)
A.net.send({"type":"bug_report","text":txt,"reason":"test"})
for _ in range(60): A.step(); time.sleep(0.01)
print("sync send:", diagmod.send_report_sync(A.cfg, "hello from sync", "sync test"))
for _ in range(30): A.step(); time.sleep(0.01)
print("reports on server:", sorted(os.listdir(tmp/"data"/"reports")))
print("player list still has Newbie online:", [ (e['name'],e['online']) for e in A.roster])
A.shutdown(); D.clean_exit(); srv.terminate()
print("log file lines:", sum(1 for _ in open(D.path)), "marker removed:", not D.marker.exists())
# previous-crash detection
D.marker.write_text(str(D.path)); D2=diagmod.Diagnostics(tmp/"appdir"); print("previous crash detected:", D2.previous_crash_log is not None)
