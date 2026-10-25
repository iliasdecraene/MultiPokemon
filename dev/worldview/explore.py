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
pb = PyBoy(ROM, window="null", sound_emulated=False); pb.set_emulation_speed(0)
pb.load_state(open(_os.path.join(STATES, "room.state"),"rb"))
w=Walker(pb, lambda: None)
for cmd in sys.argv[1:]:
    d,n=cmd.split(':'); ok=w.go(d,int(n)) if d!='wait' else w.wait(int(n)); print(cmd, w.pos(), ok)
pb.screen.image.save('./explore.png')
