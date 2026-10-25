# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import sys; sys.path.insert(0,_os.path.join(_REPO, "client"))
from pyboy import PyBoy
from yellowlink import text
pb = PyBoy(ROM, window="null", sound_emulated=False); pb.set_emulation_speed(0); m=pb.memory
pb.load_state(open(_os.path.join(STATES, "room.state"),"rb"))
def tap(b, wait=30):
    pb.button_press(b); [pb.tick() for _ in range(4)]; pb.button_release(b); [pb.tick() for _ in range(wait)]
def screen_text():
    rows=[]
    for r in range(18):
        rows.append(text.decode(bytes((v if v!=0x50 else 0x7F) for v in [m[0xC3A0+r*20+c] for c in range(20)])))
    return rows
tap("start", 40)
for _ in range(8):
    rows=screen_text()
    cur=[r for r in rows if "▶" in r or "?" in r]
    # cursor glyph 0xED isn't in our decode table -> shows as '?'
    line=[r for r in rows if "?SAVE" in r.replace(" ","")]
    if line: break
    tap("down", 12)
print("\n".join(screen_text()))
tap("a", 120)
print("\n".join(screen_text()))
tap("a", 300)   # YES
for _ in range(3): tap("a", 120)
print("\n".join(screen_text()))
dump=b"".join(bytes(m[b,0xA000:0xBFFF])+bytes([m[b,0xBFFF]]) for b in range(4))
open(_os.path.join(STATES, "yellow.sav"),"wb").write(dump); print("nonzero bytes:", sum(1 for x in dump if x))
