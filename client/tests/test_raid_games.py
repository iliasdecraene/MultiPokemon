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



# --- the one Poké Ball for a player who has none (Ilias, 2026-10-27) -------------------------------------------------
def test_add_item_like_the_game():
    assert raid_gb.add_item([], 20, 4) == [(4, 1)]
    assert raid_gb.add_item([(0x14, 3), (4, 5)], 20, 4) == [(0x14, 3), (4, 6)]      # onto its own stack
    assert raid_gb.add_item([(0x14, 3)] * 19, 20, 4) == [(0x14, 3)] * 19 + [(4, 1)]
    assert raid_gb.add_item([(0x14, 3)] * 20, 20, 4) is None                         # full
    assert raid_gb.add_item([(4, 99)] * 12, 12, 5) is None


class BagGame:
    """GameMemory stand-in that reads the bag list from the fake RAM, like game.GameMemory / crystal.CrystalMemory."""

    def __init__(self, pyboy, game_id, safe=True):
        self.mem, self.game_id, self.safe = pyboy.memory, game_id, safe

    def _k(self, a):
        return (1, a) if self.game_id == "crystal" else a

    def has_poke_balls(self):
        cnt, items, _cap, _ball = raid_gb.GIVE_BALL[self.game_id]
        balls = (1, 2, 3, 4, 8) if self.game_id == "yellow" else (1, 2, 4, 5, 0x9D, 0x9F, 0xA0, 0xA1, 0xA4, 0xA5,
                                                                    0xA6, 0xB1)
        n = self.mem[self._k(cnt)]
        return any(self.mem[self._k(items + 2 * i)] in balls and self.mem[self._k(items + 2 * i + 1)]
                   for i in range(n))

    def is_safe(self):
        return self.safe


def _bag_battle(game_id, bag, app_extra=None, safe=True):
    pb = FakePyBoy()
    cnt, items, _cap, _ball = raid_gb.GIVE_BALL[game_id]
    k = (lambda a: (1, a)) if game_id == "crystal" else (lambda a: a)
    pb.memory[k(cnt)] = len(bag)
    for i, (it, q) in enumerate(bag):
        pb.memory[k(items + 2 * i)], pb.memory[k(items + 2 * i + 1)] = it, q
    pb.memory[k(items + 2 * len(bag))] = 0xFF
    src = pb
    if app_extra is not None:
        class App:
            pass
        src = App()
        src.pyboy = pb
        src.__dict__.update(app_extra)
    rb = raid_gb.RaidBattle(src, raid_data.boss_for(151, 30, "kanto"), game=game_id, rom=FakeRom(),
                            gm=BagGame(pb, game_id, safe))
    rb.species = 0x15
    return rb, pb, k


@pytest.mark.parametrize("game_id", ["yellow", "crystal"])
def test_gb_give_ball(game_id):
    cnt, items, cap, ball = raid_gb.GIVE_BALL[game_id]
    rb, pb, k = _bag_battle(game_id, [(0x14, 3), (0x10, 1)])            # potion-ish items, no ball
    assert not rb.has_ball() and rb.can_give_ball() == (True, "")
    assert rb.give_ball() == (True, "gave")
    assert pb.memory[k(cnt)] == 3
    assert (pb.memory[k(items + 4)], pb.memory[k(items + 5)]) == (ball, 1)
    assert pb.memory[k(items + 6)] == 0xFF
    assert pb.memory[k(items)] == 0x14 and pb.memory[k(items + 1)] == 3       # the rest untouched
    assert rb.has_ball() and rb.ball_given and {"type": "ball_given"} in rb.events
    assert rb.give_ball() == (True, "")                                         # exactly one
    assert pb.memory[k(cnt)] == 3 and pb.memory[k(items + 5)] == 1


@pytest.mark.parametrize("game_id", ["yellow", "crystal"])
def test_gb_give_ball_refused(game_id):
    cnt, items, cap, ball = raid_gb.GIVE_BALL[game_id]
    full = [(0x14 + i, 1) for i in range(cap)] if game_id == "yellow" else [(0x40 + i, 1) for i in range(cap)]
    rb, pb, k = _bag_battle(game_id, full)
    assert rb.can_give_ball() == (False, "you need a Poké Ball")
    assert rb.give_ball()[0] is False and pb.memory[k(cnt)] == cap and not rb.events
    rb, pb, k = _bag_battle(game_id, [], app_extra={"chal": {"id": "x", "game": game_id}})   # Soul Link run
    assert rb.can_give_ball() == (False, "you need a Poké Ball") and not rb.give_ball()[0]
    assert pb.memory[k(cnt)] == 0
    rb, pb, k = _bag_battle(game_id, [], app_extra={"chal": None})
    assert rb.can_give_ball() == (True, "")
    rb, pb, k = _bag_battle(game_id, [], safe=False)                            # not standing still: no write
    assert rb.give_ball() == (False, "stand still in the overworld first") and pb.memory[k(cnt)] == 0
    assert not rb.start(1000)


def test_pt_pocket_slot_like_bag_try_add_item():
    empty = [(0, 0)] * 15
    assert raid_pt.pocket_slot_for(empty, 4) == 0
    assert raid_pt.pocket_slot_for([(17, 3), (0, 0)] + empty[2:], 4) == 1
    assert raid_pt.pocket_slot_for([(17, 3), (0, 0), (4, 0)] + empty[3:], 4) == 2      # its own (empty) stack
    assert raid_pt.pocket_slot_for([(4, 999)] + empty[1:], 4) is None
    assert raid_pt.pocket_slot_for([(20 + i, 1) for i in range(15)], 4) is None
    assert raid_pt.is_ball(4) and raid_pt.is_ball(16) and not raid_pt.is_ball(17) and not raid_pt.is_ball(0)


PT_BOSS = raid_data.POOLS["sinnoh"][0][0]


class BagCore:
    class emu:
        class lib:
            desmume_memory_read_register = desmume_memory_write_register = staticmethod(lambda *a: 0)

    def __init__(self):
        self.ram = bytearray(0x2000)

    def read(self, a, n):
        return bytes(self.ram[a:a + n])

    def write(self, a, data):
        self.ram[a:a + len(data)] = data


class BagPM:
    def __init__(self, core, safe=True):
        self.core, self.safe = core, safe

    def save_body(self):
        return 0x100

    def is_safe(self):
        return self.safe

    def balls(self):
        import struct
        a = 0x100 + raid_pt.PAGE_BAG + raid_pt.BAG_BALLS
        return [x for x in struct.iter_unpack("<HH", self.core.read(a, 60)) if x[0]]


def test_pt_give_ball():
    import struct
    core = BagCore()
    a = 0x100 + raid_pt.PAGE_BAG + raid_pt.BAG_BALLS
    core.write(a, struct.pack("<HH", 17, 2))                                   # not a ball
    rb = raid_pt.RaidBattle(core, raid_data.boss_for(PT_BOSS, 40, "sinnoh"), pm=BagPM(core), data=object())
    assert not rb.has_ball() and rb.can_give_ball() == (True, "")
    assert rb.give_ball() == (True, "gave")
    assert struct.unpack_from("<HH", core.read(a + 4, 4)) == (4, 1)
    assert struct.unpack_from("<HH", core.read(a, 4)) == (17, 2)
    assert rb.has_ball() and {"type": "ball_given"} in rb.events
    assert rb.give_ball() == (True, "") and struct.unpack_from("<HH", core.read(a + 4, 4)) == (4, 1)
    full = BagCore()
    for i in range(15):
        full.write(a + 4 * i, struct.pack("<HH", 20 + i, 1))
    rb = raid_pt.RaidBattle(full, raid_data.boss_for(PT_BOSS, 40, "sinnoh"), pm=BagPM(full), data=object())
    assert rb.can_give_ball() == (False, "you need a Poké Ball") and not rb.give_ball()[0]
    unsafe = BagCore()
    rb = raid_pt.RaidBattle(unsafe, raid_data.boss_for(PT_BOSS, 40, "sinnoh"), pm=BagPM(unsafe, safe=False),
                            data=object())
    assert rb.give_ball()[0] is False and unsafe.read(a, 4) == bytes(4)


def test_pt_give_ball_never_in_soul_link():
    core = BagCore()

    class App:
        nds = core
        chal = {"id": "s", "game": "platinum"}
        game = BagPM(core)
        plat_data = object()
    rb = raid_pt.RaidBattle(App(), raid_data.boss_for(PT_BOSS, 40, "sinnoh"))
    assert rb.can_give_ball() == (False, "you need a Poké Ball") and not rb.give_ball()[0]
