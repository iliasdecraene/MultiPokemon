# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
from pyboy import PyBoy
pb = PyBoy(ROM, window="null", sound_emulated=False); pb.set_emulation_speed(0); m=pb.memory
def tap(b, hold=4, wait=10):
    pb.button_press(b); [pb.tick() for _ in range(hold)]; pb.button_release(b); [pb.tick() for _ in range(wait)]
for _ in range(600): pb.tick()
for i in range(1,450):
    if i%7==0: tap("start")
    elif i%11==0: tap("down")
    else: tap("a")
for _ in range(12): tap("b", wait=20)
for _ in range(200): pb.tick()
print("map",hex(m[0xD35D]),"xy",m[0xD361],m[0xD360],"font",m[0xCFC3],"name",bytes(m[0xD157:0xD15e]).hex())
with open(_os.path.join(STATES, "room.state"),"wb") as f: pb.save_state(f)
pb.screen.image.save("room.png")
