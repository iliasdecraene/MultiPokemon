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
# per-test resume dir so we never touch the real app folder
_orig_init = Cfg.__init__
Cfg.resume_path = property(lambda self: self.d / "resume.state")

def click(app, label):
    app.render()
    for rect, cb, lab in reversed(app.click_targets):
        if lab == label:
            pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center))
            app.handle_events()
            return True
    print("   (no target", label, "- have:", [l for _, _, l in app.click_targets][-12:], ")")
    return False

# ---- 1. auto-resume: walk, close, reopen -> same spot (no YL_DEV_LOAD_STATE here)
os.environ.pop("YL_DEV_LOAD_STATE", None)
c = Cfg(tmp / "r", "Resumer"); c["auto_resume"] = True
shutil.copy(_os.path.join(STATES, "yellow.sav"), c.save_path)
R = appmod.App(c)
for _ in range(40): R.step()
for d in ("right", "right"):
    R.press(d, True); [R.step() for _ in range(16)]; R.press(d, False); [R.step() for _ in range(4)]
before = R.game.position(); R.shutdown()
print("resume snapshot written:", c.resume_path.exists(), "position before close:", before["x"], before["y"])
R2 = appmod.App(c); [R2.step() for _ in range(5)]
after = R2.game.position(); print("position after reopen:", after["x"], after["y"], "same:", (after["x"], after["y"]) == (before["x"], before["y"]))
R2.shutdown()

# ---- 2. mouse-driven multiplayer panel: click Multiplayer -> Brock -> Battle
os.environ["YL_DEV_LOAD_STATE"] = _os.path.join(STATES, "room.state")
A=appmod.App(Cfg(tmp/"a","Ash")); B=appmod.App(Cfg(tmp/"b","Brock"))
rom=A.rom
def mk(sp, lvl, hp, t1, t2, moves, nick, ot, otid, st=30):
    d=bytearray(44); d[0]=sp; d[1:3]=hp.to_bytes(2,"big"); d[3]=lvl; d[5]=t1; d[6]=t2
    for i,mv in enumerate(moves): d[8+i]=mv; d[29+i]=rom.move_pp(mv)
    d[12:14]=otid.to_bytes(2,"big"); d[27:29]=b"\xaa\xaa"; d[33]=lvl; d[34:36]=hp.to_bytes(2,"big")
    for i in range(4): d[36+i*2:38+i*2]=st.to_bytes(2,"big")
    return Mon(bytes(d), text.encode(ot), text.encode(nick))
for app,mons in ((A,[mk(0x54,20,50,0x17,0x17,[0x54,0x62],"SPARKY","ASH",111)]),
                 (B,[mk(0xA5,18,45,0,0,[0x21,0x27],"RATTY","BROCK",222,28)])):
    g=app.game; g.m[game.PARTY_COUNT]=len(mons)
    for i,mn in enumerate(mons): g.write_party_slot(i,mn)
    g.m[game.PARTY_SPECIES+len(mons)]=0xFF
def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.001)
        if cond and cond(): return True
run(250)
print("roster:", [(e["name"], e["uid"][:6], e["online"]) for e in A.roster], "A uid", A.cfg["uid"][:6], "B uid", B.cfg["uid"][:6])
print("click Multiplayer:", click(A, "Multiplayer"), "-> mode", A.mode, A.panel)
print("click Brock row:", click(A, "player:Brock"), "-> selected", A.panel_sel == B.cfg["uid"])
A.render(); pygame.image.save(A.screen, str(tmp / "panel_mp.png"))
print("click Battle:", click(A, "Battle"), "-> mode", A.mode)
print("Brock got a prompt:", run(200, lambda: B.mode == "prompt"), B.prompt and B.prompt["kind"])
time.sleep(0.6); B.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n))
run(100)
# ---- 3. regions panel renders, shows Yellow as current, Add button present
print("click Regions:", click(A, "Regions"), "->", A.panel)
A.render(); labels = [l for _, _, l in A.click_targets]
print("regions buttons:", [l for l in labels if l in ("Travel", "Add a game file...", "x")])
pygame.image.save(A.screen, str(tmp / "panel_regions.png"))
print("Esc closes:", A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)) or A.mode)
print("shots", tmp)
A.shutdown(); B.shutdown(); srv.terminate()
