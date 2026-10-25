# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
"""Navigation helpers for the verification script."""
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

class Walker:
    def __init__(self, pb, on_frame):
        self.pb, self.m, self.on_frame = pb, pb.memory, on_frame
    def pos(self):
        m = self.m
        return m[0xD35D], m[0xD361], m[0xD360]
    def tick(self, n=1):
        for _ in range(n):
            self.pb.tick(); self.on_frame()
    def clear_battle(self, cap=6000):
        """Mash A through a battle (and the text after it)."""
        i = 0
        while self.m[0xD056] != 0 and i < cap:
            if i % 20 == 0: self.pb.button_press("a")
            if i % 20 == 3: self.pb.button_release("a")
            self.tick(); i += 1
        if i:
            for j in range(300):
                if j % 20 == 0: self.pb.button_press("b")
                if j % 20 == 3: self.pb.button_release("b")
                self.tick()
        return i

    def step(self, d, maxf=40):
        """Hold d until the player finishes one step (or map changes)."""
        self.clear_battle()
        start = self.pos()
        self.pb.button_press(d)
        f = 0
        moved = False
        while f < maxf:
            self.tick(); f += 1
            if self.m[0xCFC4] != 0: moved = True
            p = self.pos()
            if p != start and self.m[0xCFC4] == 0:
                break
        self.pb.button_release(d)
        self.tick(2)
        if self.m[0xD056] != 0:
            self.clear_battle()
        return self.pos() != start
    def go(self, d, n):
        for _ in range(n):
            if not self.step(d) and not self.step(d):
                return False
        return True
    def wait(self, n): self.tick(n)

    def goto(self, tx, ty, maxsteps=80):
        """Greedy walk to (tx, ty) on the current map with simple detours."""
        import random
        rnd = random.Random(1)
        for _ in range(maxsteps):
            _, x, y = self.pos()
            if (x, y) == (tx, ty):
                return True
            opts = []
            if x != tx: opts.append("right" if tx > x else "left")
            if y != ty: opts.append("down" if ty > y else "up")
            if not any(self.step(d) or self.step(d) for d in opts[:1]) and \
               not any(self.step(d) for d in opts[1:]):
                d = rnd.choice(["up", "down", "left", "right"])
                self.step(d); self.step(d)
        return self.pos()[1:] == (tx, ty)
