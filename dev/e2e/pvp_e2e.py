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
A=appmod.App(Cfg(tmp/"a","Ash")); B=appmod.App(Cfg(tmp/"b","Brock"))
rom=A.rom
def mk(sp, lvl, hp, t1, t2, moves, nick, ot, otid, st=30):
    d=bytearray(44); d[0]=sp; d[1:3]=hp.to_bytes(2,"big"); d[3]=lvl; d[5]=t1; d[6]=t2
    for i,mv in enumerate(moves): d[8+i]=mv; d[29+i]=rom.move_pp(mv)
    d[12:14]=otid.to_bytes(2,"big"); d[27:29]=b"\xaa\xaa"; d[33]=lvl; d[34:36]=hp.to_bytes(2,"big")
    for i in range(4): d[36+i*2:38+i*2]=st.to_bytes(2,"big")
    return Mon(bytes(d), text.encode(ot), text.encode(nick))
for app,mons in ((A,[mk(0x54,20,50,0x17,0x17,[0x54,0x62],"SPARKY","ASH",111)]),
                 (B,[mk(0xA5,18,45,0,0,[0x21,0x27],"RATTY","BROCK",222,28), mk(0x24,15,40,0,2,[0x10],"PIDGE","BROCK",222,25)])):
    g=app.game; g.m[game.PARTY_COUNT]=len(mons)
    for i,mn in enumerate(mons): g.write_party_slot(i,mn)
    g.m[game.PARTY_SPECIES+len(mons)]=0xFF
def run(n, cond=None, bot=False):
    for i in range(n):
        A.step(); B.step()
        if bot: drive(i)
        time.sleep(0.001)
        if cond and cond(): return True
    return False
run(200)
print("online:", A.net.status, B.net.status, "hooks:", A.hooks is not None)
# Ash challenges Brock via the link menu
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB))     # opens the Multiplayer panel
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN))    # select the first trainer (Brock)
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_b))       # Battle
run(200, lambda: B.mode=="prompt")
print("B prompt:", B.prompt and B.prompt["kind"])
time.sleep(0.6)
B.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y))
run(400, lambda: A.live and B.live and A.live.phase=="running" and B.live.phase=="running")
print("phases:", A.live and A.live.phase, B.live and B.live.phase, "master:", A.live and A.live.master, B.live and B.live.master)
# bots: mash A; B scripts a switch after its mon faints
state={"fainted":None,"alive":False}
def drive(i):
    for app in (A,B):
        if app is B and state["fainted"] is not None and i < state["fainted"]+520:
            for when,btn in state["script"]:
                if i==when: app.press(btn, True)
                if i==when+3: app.press(btn, False)
            continue
        if i%12==0: app.press("a", True)
        if i%12==3: app.press("a", False)
    hp = B.game.m[0xD014]<<8|B.game.m[0xD015]
    if hp>0: state["alive"]=True
    if B.game.m[game.IS_IN_BATTLE] and hp==0 and state["alive"] and state["fainted"] is None:
        state["fainted"]=i; state["script"]=[(i+400,"down"),(i+420,"a"),(i+460,"a"),(i+500,"a")]
pygame.image.save  # noop
ok = run(30000, lambda: A.live is None and B.live is None, bot=True)
print("battle finished:", ok)
print("A toasts:", [t[0] for t in A.toasts][-2:]); print("B toasts:", [t[0] for t in B.toasts][-2:])
print("chat:", [c[1] for c in A.chat_log[-3:]])
print("A back in overworld:", A.game.is_safe(), A.game.position()["map"], "linkstate", A.game.m[0xD12A], "| B:", B.game.is_safe(), B.game.m[0xD12A])
print("A party HP after restore:", [(m.hp) for m in A.game.party()], "B:", [(m.hp) for m in B.game.party()])
import re
log=open(A.diag.path).read() if A.diag else ""
A.shutdown(); B.shutdown(); srv.terminate()
print("server log tail:\n"+open(tmp/"srv.log").read()[-900:])
