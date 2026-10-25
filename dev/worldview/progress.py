# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import sys; sys.path.insert(0,'.')
from pyboy import PyBoy
from nav import Walker
pb = PyBoy(ROM, window="null", sound_emulated=False); pb.set_emulation_speed(0); m=pb.memory
pb.load_state(open(sys.argv[1],"rb"))
w=Walker(pb, lambda: None)
def mash(n, btn='a'):
    for i in range(n):
        if i%20==0: pb.button_press(btn)
        if i%20==3: pb.button_release(btn)
        pb.tick()
        if i%1000==0: print(" mash",i,w.pos(),"battle",m[0xD056],"party",m[0xD162], "joyignore",m[0xCD6B])
for cmd in sys.argv[3:]:
    d,n=cmd.split(':')
    if d=='mash': mash(int(n))
    elif d=='mashb': mash(int(n),'b')
    elif d=='goto':
        x,y=map(int,n.split(',')); print(w.goto(x,y))
    elif d=='wait': w.wait(int(n))
    else: w.go(d,int(n))
    print(cmd, w.pos(), "battle", m[0xD056])
pb.screen.image.save('./explore.png')
pb.save_state(open(sys.argv[2],'wb'))
