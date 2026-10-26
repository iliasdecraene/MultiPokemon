"""Shared helper for driving Pokémon Platinum headless in py-desmume (dev/test use only).

    from ds import DS
    d = DS()                      # boots dev/states/platinum.nds (or YL_PLATINUM_ROM)
    d.press("a"); d.run(60)       # tap A, then 60 frames
    d.hold("up", 16)              # hold for 16 frames
    d.touch(128, 96)              # tap the bottom screen
    d.shot("x.png")               # both screens (256x384, top above bottom)
    d.save("dev/states/pt_x.dsst"); d.load(...)
    d.r32(0x02101D2C)             # memory reads/writes: r8/r16/r32/w8/w16/w32/read(addr, n)

Only ONE DeSmuME can exist per process (libdesmume is global). Needs SDL_AUDIODRIVER=dummy on this headless box
(set below). Frame rate: ~59.83 fps; one cycle() = one frame.
"""
import os
import sys

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROM = os.environ.get("YL_PLATINUM_ROM", os.path.join(_HERE, "..", "states", "platinum.nds"))

from desmume.emulator import DeSmuME  # noqa: E402
from desmume.controls import Keys, keymask  # noqa: E402

KEYS = {"a": Keys.KEY_A, "b": Keys.KEY_B, "select": Keys.KEY_SELECT, "start": Keys.KEY_START,
        "right": Keys.KEY_RIGHT, "left": Keys.KEY_LEFT, "up": Keys.KEY_UP, "down": Keys.KEY_DOWN,
        "r": Keys.KEY_R, "l": Keys.KEY_L, "x": Keys.KEY_X, "y": Keys.KEY_Y}


class DS:
    def __init__(self, rom=ROM, state=None):
        self.emu = DeSmuME()
        self.emu.open(os.path.abspath(rom))
        self.emu.volume_set(0)
        self.frame = 0
        if state:
            self.load(state)

    # --- time + input --------------------------------------------------------------------------------------
    def run(self, frames=1):
        for _ in range(frames):
            self.emu.cycle(with_joystick=False)
            self.frame += 1

    def hold(self, key, frames=8, after=8):
        m = keymask(KEYS[key])
        self.emu.input.keypad_add_key(m)
        self.run(frames)
        self.emu.input.keypad_rm_key(m)
        self.run(after)

    def press(self, key, after=20):
        self.hold(key, 6, after)

    def touch(self, x, y, frames=6, after=20):
        self.emu.input.touch_set_pos(int(x), int(y))
        self.run(frames)
        self.emu.input.touch_release()
        self.run(after)

    # --- screens ---------------------------------------------------------------------------------------------
    def shot(self, path=None):
        img = self.emu.screenshot()          # PIL image 256x384: top screen above bottom screen
        if path:
            img.save(path)
        return img

    # --- states ----------------------------------------------------------------------------------------------
    def save(self, path):
        self.emu.savestate.save_file(os.path.abspath(path))

    def load(self, path):
        self.emu.savestate.load_file(os.path.abspath(path))

    # --- memory ----------------------------------------------------------------------------------------------
    @property
    def mem(self):
        return self.emu.memory.unsigned

    def r8(self, a): return self.mem.read_byte(a)
    def r16(self, a): return self.mem.read_short(a)
    def r32(self, a): return self.mem.read_long(a)
    def w8(self, a, v): self.emu.memory.write_byte(a, v)
    def w16(self, a, v): self.emu.memory.write_short(a, v)
    def w32(self, a, v): self.emu.memory.write_long(a, v)

    def read(self, a, n):
        return bytes(self.mem[a:a + n])


if __name__ == "__main__":
    d = DS()
    d.run(int(sys.argv[1]) if len(sys.argv) > 1 else 600)
    d.shot("/tmp/pt_boot.png")
    print("frame", d.frame, "screenshot /tmp/pt_boot.png")
