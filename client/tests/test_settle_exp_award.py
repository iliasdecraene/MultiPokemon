"""App._settle_exp_award: closing/leaving while Exp Share All is in the middle of an award (no ROM needed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from yellowlink.app import App  # noqa: E402


class FakeHandle:
    def __init__(self, busy_frames):
        self.left = busy_frames          # None = stays busy (waits for a button)
        self.removed = False

    def busy(self):
        return not self.removed and (self.left is None or self.left > 0)

    def tick(self):
        if self.left:
            self.left -= 1

    def remove(self):
        self.removed = True


class FakeEmu:
    def __init__(self, h):
        self.h = h
        self.frames = 0

    def tick(self, *a):
        self.frames += 1
        self.h.tick()


def make(busy_frames, chal=None, ds=False):
    app = object.__new__(App)
    h = FakeHandle(busy_frames)
    emu = FakeEmu(h)
    app.exp_share = h
    app.chal = chal
    app.ds = ds
    app.nds = emu if ds else None
    app.pyboy = None if ds else emu
    app.release_all = lambda: None
    app._pause_ds_audio = lambda paused: None
    return app, h, emu


def test_not_busy_nothing_runs():
    app, h, emu = make(0)
    assert app._settle_exp_award() is True
    assert emu.frames == 0


def test_award_finishes_by_itself_gb_and_ds():
    for ds in (False, True):
        app, h, emu = make(40, ds=ds)
        assert app._settle_exp_award() is True
        assert emu.frames == 40 and not h.removed and app.exp_share is h


def test_stuck_main_game_keeps_old_snapshot():
    app, h, emu = make(None)
    assert app._settle_exp_award(max_frames=50) is False
    assert emu.frames == 50 and not h.removed and app.exp_share is h


def test_stuck_in_soul_link_removes_hooks_so_the_snapshot_is_current():
    app, h, emu = make(None, chal={"id": "c1"}, ds=True)
    assert app._settle_exp_award(max_frames=30) is True
    assert h.removed and app.exp_share is None
    assert app._exp_award_busy() is False
