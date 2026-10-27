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
print("A status", A.net.status, "friends", list(f.name for f in A.friends.values()))
# B walks right twice
B.press("right", True); run(30); B.press("right", False); run(60)
B.press("up", True); run(18); B.press("up", False); run(80)
print("B pos", B.game.position(), "A sees B state", {k:v for k,v in list(A.friends.values())[0].state.items() if k in ('px','py','map','visible')})
shot(A,"a_sees_b.png")

# give parties
def mk(sp,lvl,hp,t1,t2,move,nick,ot,otid):
    from yellowlink import text
    d=bytearray(44); d[0]=sp; d[1:3]=hp.to_bytes(2,"big"); d[3]=lvl; d[5]=t1; d[6]=t2; d[8]=move; d[12:14]=otid.to_bytes(2,"big")
    d[27:29]=b"\xaa\xaa"; d[29]=10; d[33]=lvl; d[34:36]=hp.to_bytes(2,"big")
    for i in range(4): d[36+i*2:38+i*2]=(20+lvl).to_bytes(2,"big")
    return Mon(bytes(d), text.encode(ot), text.encode(nick))
for app,mons in ((A,[mk(0x54,12,35,0x17,0x17,0x54,"PIKACHU","ASH",111)]),
                 (B,[mk(0x26,16,40,0x18,0x18,0x5D,"KADABRA","BROCK",222), mk(0xA5,9,25,0,0,0x21,"RATTY","BROCK",222)])):
    g=app.game; g.m[game.PARTY_COUNT]=len(mons)
    for i,mn in enumerate(mons): g.write_party_slot(i,mn)
    g.m[game.PARTY_SPECIES+len(mons)]=0xFF
run(400)
print("roster", [(e["name"],e["online"],e["levels"]) for e in A.roster])

# trade: A requests B
brock_uid=B.cfg["uid"]
# (party trades were replaced by PC trades in 0.6.1: dev/e2e/pctrade_e2e.py)
run(400)
# battle: A battles B's team
A.net.send({"type":"get_party","uid":brock_uid}); run(100, lambda: A.mode=="battle_confirm")
print("A mode", A.mode)
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
run(400, lambda: A.battle and A.battle.phase=="running")
print("battle phase", A.battle and A.battle.phase)
run(200); shot(A,"a_battle.png")
# spam A until battle over
for i in range(20000):
    if i%10==0: A.press("a", True)
    if i%10==3: A.press("a", False)
    A.step(); B.step()
    if A.battle is None: break
print("battle finished after", i, "frames; toasts", [t[0] for t in A.toasts][-2:])
run(200)
print("A pos after", A.game.position(), "party", [(A.rom.species_name(m.species), m.hp) for m in A.game.party()])
print("B chat", [c[1] for c in B.chat_log[-4:]])
shot(A,"a_after.png")
A.shutdown(); B.shutdown(); srv.terminate()
print("server log tail:"); print(open(tmp/"srv.log").read()[-1500:])
