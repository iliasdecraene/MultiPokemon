"""raid_gb / raid_pt / raid_art pieces that need no ROM (the battles themselves: dev/raids/verify.py)."""
import os
import random
import sys

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pygame  # noqa: E402

from yellowlink import pk4, raid_art, raid_data, raid_gb, raid_pt  # noqa: E402


@pytest.mark.parametrize("seed", range(40))
def test_shiny_pid_keeps_nature_gender_ability(seed):
    rng = random.Random(seed)
    pid, tid, sid = rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(16)
    new = raid_pt.shiny_pid(pid, tid, sid)
    assert pk4.is_shiny(new, tid, sid)
    assert new % 25 == pid % 25                 # nature
    assert new & 0xFF == pid & 0xFF             # gender byte
    assert new & 1 == pid & 1                   # ability slot


class FakeMem(dict):
    def __getitem__(self, k):
        return self.get(k, 0)


class FakeGB:
    """Just enough of GameMemory for the den."""

    def __init__(self, x, y, map_id=12, facing=0x4, walking=False):
        self.x, self.y, self._map, self.facing, self._walking = x, y, map_id, facing, walking
        self.m = FakeMem()

    def in_game(self):
        return True

    def in_battle(self):
        return False

    def is_safe(self):
        return not self._walking

    def walking(self):
        return self._walking

    def map_id(self):
        return self._map

    def position(self):
        return {"map": self._map, "x": self.x, "y": self.y, "px": self.x * 16, "py": self.y * 16,
                "facing": self.facing}


def test_gb_den_collision_and_a():
    den = raid_gb.Den({"map": 12, "x": 10, "y": 5}, "crystal")
    gm = FakeGB(10, 7)
    assert not den.blocks(gm, "up") and not den.near(gm)
    gm.y = 6
    assert den.near(gm) and den.blocks(gm, "up") and not den.blocks(gm, "left")
    assert den.pressed_a(gm)                    # facing up = facing the den
    gm.facing = 0x0
    den._bump = None
    assert not den.pressed_a(gm)                # facing away, never pushed against it
    den.blocks(gm, "up")
    assert den.pressed_a(gm)                    # just pushed against it
    other = FakeGB(10, 6, map_id=13)
    assert not den.near(other) and not den.blocks(other, "up") and den.offset(other) is None
    assert den.offset(other, neighbor_shift=lambda m: (0, 20)) == (0, 19)


def test_gb_den_yellow_walking_uses_destination():
    from yellowlink import game
    den = raid_gb.Den({"map": 12, "x": 10, "y": 5}, "yellow")
    gm = FakeGB(10, 7, walking=True)             # Yellow: coords are the square the step started from
    gm.m[game.PLAYER_YVEC] = 0xFF                # moving up
    assert den.blocks(gm, "up")                  # the step ends next to the den: the next one is refused


def test_gb_den_draw_and_arrow():
    pygame.init()
    surf = pygame.Surface((160, 144), pygame.SRCALPHA)
    den = raid_gb.Den({"map": 12, "x": 10, "y": 5}, "yellow")
    assert den.draw(surf, FakeGB(10, 7), t=0.1)
    assert not raid_gb.Den({"map": 12, "x": 40, "y": 5}, "yellow").draw(surf, FakeGB(10, 7), t=0.1)
    assert not den.draw(surf, FakeGB(10, 7, map_id=99), t=0.1)


class FakePM:
    def __init__(self, x, y, map_id=343, facing=0):
        self.x, self.y, self._map, self.facing = x, y, map_id, facing

    def in_game(self):
        return True

    def is_safe(self):
        return True

    def walking(self):
        return False

    def map_id(self):
        return self._map

    def overworld_running(self):
        return True

    def field_task(self):
        return False

    def position(self):
        return {"map": self._map, "x": self.x, "y": self.y, "ph": 16, "facing": self.facing}


def test_pt_den():
    den = raid_pt.Den({"map": 343, "x": 178, "y": 824})
    pm = FakePM(178, 825)
    assert den.near(pm) and den.blocks(pm, "up") and den.pressed_a(pm)
    pm.facing = 1
    den._bump = None
    assert not den.pressed_a(pm)
    assert den.offset(FakePM(170, 830)) == (8, -6)
    pygame.init()
    surf = pygame.Surface((256, 384))
    assert not den.draw(surf, FakePM(100, 900), cam=None)     # no camera: arrow only


def test_art():
    pygame.init()
    assert raid_art.gb_den(0.0).get_size() == (16, 24)
    assert raid_art.ds_den(0.0).get_size() == (32, 48)
    s = pygame.Surface((160, 144), pygame.SRCALPHA)
    for dx, dy in ((1, 0), (-3, 2), (0, -9), (5, 5)):
        raid_art.arrow(s, (0, 0, 160, 144), dx, dy, abs(dx) + abs(dy), 0.5)


class FakePyBoy:
    def __init__(self):
        self.memory = FakeMem()


class FakeRom:
    rom = bytes(0x80000)

    def species_for_dex(self, dex):
        return 0x15 if dex == 151 else 0

    def move_pp(self, mv):
        return 10


def test_gb_pool_bookkeeping():
    boss = raid_data.boss_for(151, 30, "kanto")
    rb = raid_gb.RaidBattle(FakePyBoy(), boss, game="yellow", rom=FakeRom(), gm=object(), rng=random.Random(1))
    assert rb.species == 0x15
    rb.pool, rb.max_hp = 2500, 100
    rb.meter.sync(2500, 100)
    rb.phase = "fight"
    rb._observe(30, False)                       # 20 HP off a 50-HP segment (500 mb) = 200 mb
    ev = rb._drain()
    assert ev == [{"type": "damage", "hp": 20, "of": 100, "mb": 200}] and rb.dealt == 200 and rb.pool == 2300
    rb.set_pool(2500, dealt_mb=0)                # the server hasn't counted our 200 yet
    assert rb.pool == 2300
    rb.set_pool(4300, dealt_mb=200)              # another player joined (+2 bars) and our hit arrived
    assert rb.pool == 4300
    rb.group_down(1.0)
    assert rb.phase == "down" and rb.catch_success is True
    rb2 = raid_gb.RaidBattle(FakePyBoy(), boss, game="yellow", rom=FakeRom(), gm=object(), rng=random.Random(1))
    rb2.phase = "fight"
    rb2.group_down(0.0)
    assert rb2.catch_success is False


class PressPyBoy(FakePyBoy):
    def __init__(self):
        super().__init__()
        self.presses = []

    def button(self, name, hold=1):
        self.presses.append(name)


def test_gb_autopilot_gives_up_without_progress(caplog):
    """M3: auto_pick stops driving after NO_PROGRESS frames in which the game didn't move on, and logs why."""
    pb = PressPyBoy()
    rb = raid_gb.RaidBattle(pb, raid_data.boss_for(151, 30, "kanto"), game="yellow", rom=FakeRom(), gm=object())
    pb.memory[raid_gb.YellowSpec.IN_BATTLE] = 1
    rb.phase = "fight"
    rb.ui = "menu"
    rb._open_decision("menu")
    rb.auto_pick()
    assert rb.taking_over()
    with caplog.at_level("WARNING", logger="yellowlink.raid"):
        for _ in range(raid_gb.NO_PROGRESS + 50):
            rb.frame += 1
            rb._autopilot()
    assert pb.presses                            # it did try
    assert not rb.auto and not rb.taking_over()
    assert any("gave up" in r.message for r in caplog.records)


def test_gb_taking_over_while_starting_is_bounded():
    rb = raid_gb.RaidBattle(PressPyBoy(), raid_data.boss_for(151, 30, "kanto"), game="yellow", rom=FakeRom(),
                            gm=object())
    rb.phase = "starting"
    rb._start_frame = rb.frame = 100
    assert rb.taking_over()
    rb.frame = 100 + raid_gb.NO_PROGRESS + 1
    assert not rb.taking_over()


def test_gb_flee_takes_over_and_runs():
    """M1: time over while the player is in the menu: YellowLink presses RUN (bottom right) at once."""
    pb = PressPyBoy()
    rb = raid_gb.RaidBattle(pb, raid_data.boss_for(151, 30, "kanto"), game="yellow", rom=FakeRom(), gm=object())
    pb.memory[raid_gb.YellowSpec.IN_BATTLE] = 1
    rb.phase = "fight"
    rb.ui = "menu"
    rb._open_decision("menu")
    rb.flee()
    assert rb.phase == "fleeing" and rb.auto
    assert pb.memory[raid_gb.YellowSpec.ENEMY_SPEED + 1] == 1          # boss speed 1: the player's RUN works
    for _ in range(200):
        rb.frame += 1
        rb._autopilot()
    assert pb.presses[:5] == ["up", "left", "down", "right", "a"]

