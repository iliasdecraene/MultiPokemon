"""Raids, the client's network/UI side (client/yellowlink/raid.py, SPEC.md section 12) with a fake RaidBattle/Den,
plus the server/client data checks (pools, names, the vendored spot sample)."""
import json
import os
import sys
import types

import numpy as np
import pygame
import pytest

from yellowlink import raid as R

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
from yellowlink_server import raids as SR  # noqa: E402


# ------------------------------------------------------------------------------------------------ pure helpers
def test_segment_matches_design():
    assert R.segment(2000, 100) == 100          # a full bar
    assert R.segment(1500, 100) == 50
    assert R.segment(1001, 100) == 1            # 1 milli-bar left of the second bar
    assert R.segment(1, 37) == 1 and R.segment(0, 37) == 0
    assert R.segment(999, 41) == 41             # ceil(999 * 41 / 1000)


def test_texts():
    assert R.bars_text(0) == "0" and R.bars_text(2000) == "2" and R.bars_text(3401) == "3.5"
    assert R.time_text(754.2) == "12:35" and R.time_text(-3) == "0:00"
    raid = {"name": "Mew", "shiny": False, "spot": {"name": "Route 1"}, "region": "kanto"}
    assert R.notice_text(raid) == "RAID! Mew appeared on Route 1 (Kanto)!"
    raid.update(shiny=True, spot={"name": "Cerulean City"})
    assert R.notice_text(raid, 15) == "RAID! A shiny Mew appeared in Cerulean City (Kanto) - 15 minutes!"


def test_reach_from_badges():
    assert R.reach_from_badges("yellow", 0b00000111) == 3
    assert R.reach_from_badges("crystal", 0xFF | (0x0F << 8)) == 8       # Johto badges only
    assert R.reach_from_badges("platinum", 5) == 5
    assert R.reach_from_badges("yellow", None) == 0


def test_move_timer():
    t = R.MoveTimer()
    assert not t.running and t.left(0) is None and not t.poll(100)
    t.need_input(10.0)
    t.need_input(14.0)                          # repeats don't restart it (same turn, other sub-menu)
    assert t.left(15.0) == pytest.approx(5.0) and t.fraction(15.0) == pytest.approx(0.5)
    assert not t.poll(19.9)
    assert t.poll(20.0)                         # ran out: auto pick
    assert not t.poll(21.0)
    assert t.poll(23.0)                         # still waiting 3 s later: again
    t.input_done()
    assert not t.running and not t.poll(100)


def test_damage_meter():
    m = R.DamageMeter()
    assert m.add({"type": "damage", "hp": 10, "of": 40, "mb": 250}) == 250
    assert m.add({"type": "damage", "hp": 30, "of": 120}) == 250            # no mb: n * 1000 // M
    assert m.add({"type": "damage", "hp": 500, "of": 100}) == 1000          # at most a bar
    assert m.add({"type": "damage", "hp": 0, "of": 100}) == 0
    assert m.total == 1500 and m.hits == 3


def test_horn_sound_shape():
    a = R.horn_samples(48000, 2)
    assert a.dtype == np.int16 and a.shape[1] == 2 and a.shape[0] == int(R.HORN_SECS * 48000)
    peak = int(np.abs(a).max())
    assert abs(peak - R.HORN_VOLUME * 32767) < 2
    assert abs(int(a[-1, 0])) < 50 and abs(int(a[0, 0])) < 50           # silent ends: no click
    assert not np.array_equal(a[:1000], R.shiny.jingle_samples(48000, 2)[:1000])


# ------------------------------------------------------------------------------------------------ server <-> client
def test_server_pools_match_raid_data():
    rd = pytest.importorskip("yellowlink.raid_data")
    for region, pool in rd.POOLS.items():
        assert sorted(s for s, _w in pool) == sorted(SR.POOLS[region]), region
        assert len({w for _s, w in pool}) == 1, "the server picks evenly: tell raid-net when weights differ"
        for s, _w in pool:
            assert SR.SPECIES_NAMES[s] == rd.NAMES[s]
            boss = rd.boss_for(s, 30, region)                 # every server species works on the client
            assert boss["species"] == s and 1 <= boss["level"] <= 100
    assert set(rd.POOLS) == set(SR.POOLS)


def test_vendored_spots_are_a_sample_of_the_client_data():
    client = os.path.join(REPO, "client", "yellowlink", "raid_spots.json")
    server = SR.SPOTS_FILE
    if not os.path.exists(client):
        pytest.skip("no client spot data yet (raid-games)")
    assert os.path.exists(server), "run: python dev/raids/server_spots.py"
    with open(client, encoding="utf-8") as fh:
        cdata = json.load(fh)
    sdata = SR.load_spots(server)
    for region, r in cdata.items():
        if region not in SR.POOLS:
            continue
        maps = {m["map"]: m for m in (r.get("maps") or {}).values() if m.get("spots")}
        assert region in sdata, f"{region} missing in the server copy: run dev/raids/server_spots.py"
        s = sdata[region]
        assert {int(k) for k in s["names"]} == set(maps), "maps differ: run dev/raids/server_spots.py"
        for mid, x, y, reach in s["spots"]:
            m = maps[mid]
            assert [x, y] in [list(p) for p in m["spots"]] and reach == m["reach"]
            assert s["names"][str(mid)] == m["name"]


def test_server_spots_script_converts(tmp_path):
    sys.path.insert(0, os.path.join(REPO, "dev", "raids"))
    import server_spots
    client = {"kanto": {"maps": {"12": {"map": 12, "name": "Route 1", "reach": 0,
                                        "spots": [[x, 5] for x in range(100)]},
                                 "3": {"map": 3, "name": "Cerulean City", "reach": 2, "spots": [[1, 1]]},
                                 "9": {"map": 9, "name": "Empty", "reach": 0, "spots": []}}}}
    out = server_spots.convert(client)
    k = out["regions"]["kanto"]
    assert k["names"] == {"3": "Cerulean City", "12": "Route 1"}
    assert sum(1 for s in k["spots"] if s[0] == 12) == server_spots.PER_MAP
    assert [3, 1, 1, 2] in k["spots"]
    assert server_spots.convert(client) == out                                    # deterministic


# ------------------------------------------------------------------------------------------------ the mixin
class FakeBattle:
    instances = []
    default_ok = (True, "")

    def __init__(self, app, boss):
        self.app, self.boss = app, boss
        self.calls = []
        self.events = []
        self.ok = FakeBattle.default_ok
        self.started = False
        FakeBattle.instances.append(self)

    def can_join(self):
        return self.ok

    def start(self, pool):
        self.calls.append(("start", pool))
        self.started = True
        return True

    def step(self):
        ev, self.events = self.events, []
        return ev

    def busy(self):
        return self.started

    def __getattr__(self, name):
        if name == "taking_over":
            return lambda: False
        if name in ("set_pool", "group_down", "flee", "auto_pick", "remove"):
            return lambda *a: self.calls.append((name,) + a)
        raise AttributeError(name)


class FakeDen:
    def __init__(self, spot, game_id):
        self.spot, self.game_id = spot, game_id
        self.block = set()
        self.a_hits = True

    def blocks(self, gm, d):
        return d in self.block

    def pressed_a(self, gm):
        return self.a_hits

    def draw(self, surf, gm, **kw):
        self.drawn = kw


FAKE_GB = types.SimpleNamespace(RaidBattle=FakeBattle, Den=FakeDen)


def fake_boss_for(species, strongest, region, shiny=False, form=0):
    return {"species": species, "level": 50 if species != 151 else max(20, min(70, strongest + 5)),
            "moves": [1, 2, 3, 4], "shiny": shiny, "fateful": species == 151, "region": region, "form": form}


class Cfg(dict):
    data_dir = "/tmp/yl-test-server"

    def save(self):
        pass


class Net:
    def __init__(self):
        self.sent = []
        self.status = "online"

    def send(self, msg):
        self.sent.append(msg)
        return True

    def of(self, t):
        return [m for m in self.sent if m.get("type") == t]


class Emu:
    def __init__(self):
        self.log = []

    def button_press(self, b):
        self.log.append(("press", b))

    def button_release(self, b):
        self.log.append(("release", b))


class Game:
    safe = True
    badge_value = 0b111

    def is_safe(self):
        return self.safe

    def badges(self):
        return self.badge_value

    def party_checked(self):
        return [types.SimpleNamespace(level=33), types.SimpleNamespace(level=12)]


class App(R.RaidMixin):
    def __init__(self, game_id="yellow"):
        self.cfg = Cfg(uid="me", name="Ilias")
        self.net = Net()
        self.server_features = {"raids"}
        self.game = Game()
        self.G = None
        self.game_id = game_id
        self.ds = game_id == "platinum"
        self.full = True
        self.chal = None
        self.trade = self.battle = self.live = None
        self.mode = "play"
        self.frame = 1
        self.toasts, self.msgs, self.toast_colors = [], [], []
        self.kb_held, self.pad_held, self.held = set(), set(), set()
        self.emu = Emu()
        self.snapshots = []
        self.horns = 0
        self.fast = False
        self.lib = types.SimpleNamespace(games={"yellow": "y.gb", "crystal": "c.gbc"})
        self.travelled = None
        self.clock = 1_800_000_000.0
        self.init_raid()

    def toast(self, s, secs=4.0, color=None):
        self.toasts.append(s)
        self.toast_colors.append(color)

    def sys_msg(self, s, color=None):
        self.msgs.append(s)

    def release_all(self):
        pass

    def autosave(self, force=False):
        self.snapshots.append(force)
        return True

    def server_time(self):
        return self.clock

    def travel(self, game):
        self.travelled = game

    def play_raid_horn(self):
        self.horns += 1

    def library_message(self):
        return {"type": "library", "games": ["yellow"], "raid_reach": self.raid_reach_for_server()}


def raid_obj(**kw):
    r = {"id": "250000-abc", "region": "kanto", "game": "yellow", "species": 151, "name": "Mew", "form": 0,
         "shiny": False, "spot": {"map": 12, "x": 5, "y": 7, "name": "Route 1"}, "opens": 1_800_000_000.0,
         "closes": 1_800_000_900.0, "now": 1_800_000_000.0, "pool": 0, "pool_max": 0, "bars_per_player": 2,
         "phase": "open", "participants": []}
    r.update(kw)
    return r


@pytest.fixture
def app(monkeypatch):
    FakeBattle.instances.clear()
    FakeBattle.default_ok = (True, "")
    monkeypatch.setattr(R, "_game_module", lambda gid: FAKE_GB)
    monkeypatch.setattr(R, "_raid_data", lambda: types.SimpleNamespace(boss_for=fake_boss_for))
    return App()


def joined(app, pool=2000):
    app.on_raid_joined({"raid": raid_obj(pool=pool, pool_max=pool,
                                         participants=[{"uid": "me", "name": "Ilias", "dealt": 0,
                                                        "state": "fighting"}])})


def start_fight(app, monkeypatch, now=1000.0):
    monkeypatch.setattr(R.time, "time", lambda: now)
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    app.raid_press("a", True)
    app.raid_confirm["shown"] = 0
    app.raid_answer(True)
    joined(app)
    app.raid_step()
    return FakeBattle.instances[-1]


def test_notice_den_and_dialog(app, monkeypatch):
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    assert app.horns == 1 and app.toasts[-1] == "RAID! Mew appeared on Route 1 (Kanto)!"
    assert app.raid_phase == "open" and isinstance(app.raid_den, FakeDen)
    assert app.raid_den.spot["region"] == "kanto" and app.raid_den.spot["map"] == 12
    # the catch-up after a reconnect: no second horn
    app.on_raid_open({"raid": raid_obj(), "fresh": False})
    assert app.horns == 1
    # A facing the den: the dialog, the A is swallowed
    assert app.raid_press("a", True) is True
    assert app.mode == "raid_confirm" and app.raid_confirm["ok"] and app.raid_confirm["level"] == 38   # Mew: 33 + 5
    # walking onto the den is dropped; other directions pass
    app.mode = "play"
    app.raid_den.block = {"up"}
    assert app.raid_press("up", True) is True and app.raid_press("left", True) is False


def test_den_only_in_its_region_and_not_in_soul_link(app):
    app.on_raid_open({"raid": raid_obj(region="johto", game="crystal"), "fresh": True})
    assert app.raid_den is None and "Travel there to join." in app.toasts[-1]
    assert app.raid_card_button()[0] == "Go there"
    app.raid_card_button()[1]()
    assert app.travelled == "crystal"
    b = App()
    b.chal = {"id": "c1", "game": "yellow"}
    b.on_raid_open({"raid": raid_obj(), "fresh": True})
    assert b.raid_den is None and "main game" in b.toasts[-1]
    assert b.raid_join_check() == (False, "You're in a Soul Link run - raids are for your main game.")
    assert b.raid_card_button() is None


def test_cant_join_shows_why(app, monkeypatch):
    app.on_raid_open({"raid": raid_obj(), "fresh": False})
    monkeypatch.setattr(FakeBattle, "default_ok", (False, "Your party and box are full."))
    app.raid_press("a", True)
    assert app.raid_confirm["ok"] is False and app.raid_confirm["why"] == "Your party and box are full."
    app.raid_confirm["shown"] = 0
    app.raid_answer(True)
    assert not app.net.of("raid_join") and app.mode == "play" and app.raid_pending is None


def test_full_fight_and_catch(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    assert app.net.of("raid_join") == [{"type": "raid_join", "id": "250000-abc", "level": 38}]
    assert app.snapshots == [True], "a forced resume snapshot right before the raid battle"
    assert b.calls[0] == ("start", 2000) and app.raid_phase == "fight" and app.raid_fighting() and app.raid_busy()
    assert app.raid_no_snapshot()
    # hits -> running totals, throttled
    b.events = [{"type": "damage", "hp": 20, "of": 100, "mb": 200}]
    app.raid_step()
    assert app.net.of("raid_damage")[-1]["dealt"] == 200
    b.events = [{"type": "damage", "hp": 10, "of": 100, "mb": 100}]
    app.raid_step()
    assert app.net.of("raid_damage")[-1]["dealt"] == 200          # too soon: waits
    monkeypatch.setattr(R.time, "time", lambda: 1001.0)
    app.raid_step()
    assert app.net.of("raid_damage")[-1]["dealt"] == 300
    # someone else hits: the pool goes to the game
    app.on_raid_update({"raid": raid_obj(pool=3100, pool_max=4000)})
    assert ("set_pool", 3100, 0) in b.calls
    # the move timer: 10 s after need_input -> auto pick
    b.events = [{"type": "need_input", "what": "menu"}]
    app.raid_step()
    assert app.raid_timer.running
    monkeypatch.setattr(R.time, "time", lambda: 1011.5)
    app.raid_step()
    assert ("auto_pick",) in b.calls
    b.events = [{"type": "input_done"}]
    app.raid_step()
    assert not app.raid_timer.running
    # the group's HP is 0: the server's roll -> group_down(1.0)
    app.on_raid_down({"id": "250000-abc", "chance": 0.63, "catch": True})
    assert ("group_down", 1.0) in b.calls and app.raid_phase == "catch"
    assert "63%" in app.toasts[-1]
    app.on_raid_down({"id": "250000-abc", "chance": 0.63, "catch": True})        # resent: once
    assert sum(1 for c in b.calls if c[0] == "group_down") == 1
    b.events = [{"type": "caught", "key": "k", "shiny": False}, {"type": "ended", "outcome": "caught"}]
    app.raid_step()
    assert app.net.of("raid_result") == [{"type": "raid_result", "id": "250000-abc", "outcome": "caught"}]
    assert app.raid_battle is None and not app.raid_no_snapshot() and app.raid_phase == "done"
    assert "You caught Mew!" in app.toasts
    # damage is flushed before the result
    types_ = [m["type"] for m in app.net.sent]
    assert types_.index("raid_result") > max(i for i, t in enumerate(types_) if t == "raid_damage")


def test_missed_catch(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    app.on_raid_down({"id": "250000-abc", "chance": 0.2, "catch": False})
    assert ("group_down", 0.0) in b.calls
    b.events = [{"type": "missed"}, {"type": "fled"}, {"type": "ended", "outcome": "missed"}]
    app.raid_step()
    assert [m["outcome"] for m in app.net.of("raid_result")] == ["missed"]


def test_time_over_flees(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    app.on_raid_end({"id": "250000-abc", "reason": "time", "caught": [], "raid": raid_obj(phase="over")})
    assert ("flee",) in b.calls and app.raid_den is None
    b.events = [{"type": "fled"}, {"type": "ended", "outcome": "fled"}]
    app.raid_step()
    assert [m["outcome"] for m in app.net.of("raid_result")] == ["fled"]
    assert app.raid_phase == "over"
    monkeypatch.setattr(R.time, "time", lambda: 1000.0 + R.OVER_CARD + 5)
    app.raid_step()
    assert app.raid is None and app.raid_phase == "idle"


def test_time_over_without_the_message(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    app.clock = app.raid["closes"] + 30
    app.raid_step()
    assert b.calls.count(("flee",)) == 1
    app.raid_step()
    assert b.calls.count(("flee",)) == 1


def test_blackout(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    b.events = [{"type": "blackout"}, {"type": "ended", "outcome": "blackout"}]
    app.raid_step()
    assert [m["outcome"] for m in app.net.of("raid_result")] == ["blackout"]
    assert "out of this raid" in app.toasts[-1]


def test_fast_forward_goes_off(app, monkeypatch):
    app.fast = True
    start_fight(app, monkeypatch)
    assert app.fast is False


def test_closing_mid_raid_is_leaving(app, monkeypatch):
    start_fight(app, monkeypatch)
    app.raid_shutdown()
    assert app.net.of("raid_result")[-1]["outcome"] == "left"


def test_crashed_mid_raid_reports_left_on_reconnect(app):
    app.raid_on_welcome()
    me = {"uid": "me", "name": "Ilias", "dealt": 1500, "state": "fighting"}
    app.on_raid_open({"raid": raid_obj(pool=500, pool_max=2000, participants=[me]), "fresh": False})
    assert app.net.of("raid_result") == [{"type": "raid_result", "id": "250000-abc", "outcome": "left"}]
    assert app.raid_phase == "done" and app.raid_join_check()[0] is False


def test_reconnect_during_the_fight_resends_damage(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    b.events = [{"type": "damage", "hp": 20, "of": 100, "mb": 700}]
    app.raid_step()
    n = len(app.net.of("raid_damage"))
    app.raid_on_welcome()
    assert app.raid is not None                       # the battle keeps its raid
    me = {"uid": "me", "name": "Ilias", "dealt": 0, "state": "fighting"}
    app.on_raid_open({"raid": raid_obj(pool=1500, pool_max=2000, participants=[me]), "fresh": False})
    assert len(app.net.of("raid_damage")) == n + 1 and app.net.of("raid_damage")[-1]["dealt"] == 700
    assert not app.net.of("raid_result")


def test_join_lost_in_a_reconnect(app, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: 1000.0)
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    app.raid_press("a", True)
    app.raid_confirm["shown"] = 0
    app.raid_answer(True)
    assert app.raid_phase == "joining" and app.raid_busy()
    app.raid_on_welcome()
    app.on_raid_open({"raid": raid_obj(), "fresh": False})
    assert app.raid_phase == "open"
    # ... or it did arrive
    app.raid_press("a", True)
    app.raid_confirm["shown"] = 0
    app.raid_answer(True)
    me = {"uid": "me", "name": "Ilias", "dealt": 0, "state": "fighting"}
    app.on_raid_open({"raid": raid_obj(pool=2000, pool_max=2000, participants=[me]), "fresh": False})
    assert app.raid_phase == "starting"


def test_refused(app):
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    app.raid_phase = "joining"
    app.on_raid_refused({"id": "250000-abc", "reason": "You're out of this raid."})
    assert app.raid_phase == "done" and app.toasts[-1] == "You're out of this raid."
    app.on_raid_refused({"id": "other", "reason": "x"})
    assert app.toasts[-1] == "You're out of this raid."


def test_stale_raid_dropped_on_welcome(app):
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    app.raid_on_welcome()
    assert app.raid is None and app.raid_den is None


def test_reach_is_reported(app):
    app.frame = R.REACH_EVERY
    app.raid_step()
    assert app.cfg["raid_reach"][Cfg.data_dir]["kanto"] == 3
    assert app.net.of("library")[-1]["raid_reach"] == {"kanto": 3, "johto": 0, "sinnoh": 0}
    app.frame = 2 * R.REACH_EVERY
    app.raid_step()
    assert len(app.net.of("library")) == 1               # unchanged: not sent again


def test_old_server_never_gets_raid_messages(app, monkeypatch):
    app.server_features = set()
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    assert app.raid_join_check() == (False, "You need to be connected to the server.")


# ------------------------------------------------------------------------------------------------ drawing
@pytest.fixture
def screen():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    from yellowlink import ui
    return pygame.Surface((800, 600)), ui.Fonts()


def test_drawing_does_not_crash(app, monkeypatch, screen):
    surf, fonts = screen
    app.fonts = fonts
    app.click_targets = []
    app.scale = 3
    app.on_raid_open({"raid": raid_obj(shiny=True), "fresh": True})
    assert R.draw_card(surf, fonts, app, 10, 10, 280) == R.card_height(app)
    app.raid_open_dialog()
    R.draw_confirm(surf, fonts, app, 480, 432, (0, 24))
    assert any(label == "Join" for _r, _cb, label in app.click_targets)
    b = start_fight(app, monkeypatch)
    b.events = [{"type": "need_input"}]
    app.raid_step()
    app.raid_draw_overlay(surf)
    R.draw_timer_ring(surf, fonts, (100, 100), 30, 0.25, 2.5)
    assert R.draw_card(surf, fonts, app, 10, 10, 280) == R.card_height(app) == R.CARD_H


def test_result_lost_offline_is_sent_again(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    app.net.status = "offline"
    app.net.send = lambda msg: False
    b.events = [{"type": "blackout"}, {"type": "ended", "outcome": "blackout"}]
    app.raid_step()
    app.net = Net()
    app.raid_on_welcome()
    me = {"uid": "me", "name": "Ilias", "dealt": 0, "state": "fighting"}
    app.on_raid_open({"raid": raid_obj(pool=2000, pool_max=2000, participants=[me]), "fresh": False})
    assert app.net.of("raid_result") == [{"type": "raid_result", "id": "250000-abc", "outcome": "blackout"}]


def test_shiny_raid_is_a_shiny_alert(app):
    from yellowlink import shiny
    app.banners = shiny.Banners()
    app.on_raid_open({"raid": raid_obj(shiny=True), "fresh": True})
    assert app.horns == 0 and app.banners.queue[0].title == "A shiny Mew raid!"
    assert app.banners.queue[0].sub == "on Route 1 (Kanto)"


def test_down_while_the_menu_waits_restarts_the_timer(app, monkeypatch):
    b = start_fight(app, monkeypatch, now=1000.0)
    b.events = [{"type": "need_input", "what": "menu"}]
    app.raid_step()
    monkeypatch.setattr(R.time, "time", lambda: 1008.0)
    app.on_raid_down({"id": "250000-abc", "chance": 1.0, "catch": True})
    assert app.raid_timer.running and app.raid_timer.left(1008.0) == pytest.approx(10.0)
    monkeypatch.setattr(R.time, "time", lambda: 1018.5)
    app.raid_step()
    assert ("auto_pick",) in b.calls                    # the AFK player's throw



# ------------------------------------------------------------------------------------------------ review round 3
def test_busy_player_A_goes_to_the_game_and_join_is_refused(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    app.mode = "play"
    app.raid_battle = None                    # (between frames of the start: still "fight" phase, busy)
    app.raid_phase = "starting"
    assert app.raid_press("a", True) is False and app.mode == "play"
    assert app.raid_join_check() == (False, "You're already in this raid.")
    app.raid_battle = b


def test_a_second_raid_joined_never_resets_the_damage(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    b.events = [{"type": "damage", "hp": 20, "of": 100, "mb": 400}]
    app.raid_step()
    joined(app)
    app.raid_phase = "joining"                # even a stray "joining" state: the count stays
    joined(app)
    assert app.raid_meter.total == 400


def test_a_new_raid_during_the_battle_waits(app, monkeypatch):
    b = start_fight(app, monkeypatch)
    new = raid_obj(id="250001-def", species=150, name="Mewtwo")
    app.on_raid_open({"raid": new, "fresh": True})
    assert app.raid["id"] == "250000-abc" and app.raid_next["id"] == "250001-def"
    assert app.horns == 2 and "(after this battle)" in app.toasts[-1]
    app.on_raid_update({"raid": dict(new, pool=2000, pool_max=2000)})
    assert app.raid["id"] == "250000-abc" and app.raid_next["pool"] == 2000
    # the old battle's events still belong to the old raid
    b.events = [{"type": "damage", "hp": 20, "of": 100, "mb": 300}, {"type": "blackout"},
                {"type": "ended", "outcome": "blackout"}]
    monkeypatch.setattr(R.time, "time", lambda: 1005.0)
    app.raid_step()
    assert all(m["id"] == "250000-abc" for m in app.net.of("raid_damage") + app.net.of("raid_result"))
    app.raid_step()
    assert app.raid["id"] == "250001-def" and app.raid_next is None and app.raid_phase == "open"
    assert isinstance(app.raid_den, FakeDen) and app.raid_meter is None and app.raid_result is None


def test_held_raid_that_ends_first_is_dropped(app, monkeypatch):
    start_fight(app, monkeypatch)
    app.on_raid_open({"raid": raid_obj(id="250001-def"), "fresh": False})
    app.on_raid_end({"id": "250001-def", "reason": "time", "caught": [], "raid": raid_obj(id="250001-def",
                                                                                           phase="over")})
    assert app.raid_next is None and app.raid["id"] == "250000-abc" and app.raid_battle is not None


def test_no_quiet_moment_is_unstarted_and_can_retry(app, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: 1000.0)
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    app.raid_press("a", True)
    app.raid_confirm["shown"] = 0
    app.raid_answer(True)
    joined(app)
    app.game.safe = False
    monkeypatch.setattr(R.time, "time", lambda: 1000.0 + R.START_WAIT + 1)
    app.raid_step()
    assert app.net.of("raid_result")[-1]["outcome"] == "unstarted"
    assert app.raid_phase == "open" and "press A at the den to try again" in app.toasts[-1]
    assert app.toast_colors[-1] != R.ui.BAD
    app.game.safe = True
    app.on_raid_update({"raid": raid_obj(pool=0, pool_max=0)})        # the server took us off
    assert app.raid_join_check() == (True, "")


def test_refusal_is_not_red(app):
    app.on_raid_open({"raid": raid_obj(), "fresh": True})
    app.raid_phase = "joining"
    app.on_raid_refused({"id": "250000-abc", "reason": "Time's up for this raid."})
    assert app.toast_colors[-1] not in (R.ui.BAD,)


@pytest.mark.parametrize("size", [(320, 288), (256, 384), (480, 432), (384, 576)])
@pytest.mark.parametrize("ok", [True, False])
def test_join_dialog_fits_small_windows(app, screen, size, ok):
    surf = pygame.Surface(size)
    fonts = screen[1]
    app.fonts = fonts
    app.click_targets = []
    app.on_raid_open({"raid": raid_obj(shiny=True, name="Rayquaza"), "fresh": False})
    app.raid_confirm = {"ok": ok, "why": "Your party and your current PC box are full - make room first.",
                        "level": 70, "shown": 0}
    r = R.draw_confirm(surf, fonts, app, size[0], size[1], (0, 0))
    assert surf.get_rect().contains(r)
    rects = app.raid_dialog_rects
    for br in rects["buttons"]:
        assert r.contains(br)
        if rects["hint"] is not None:
            assert not br.colliderect(rects["hint"])
    if len(rects["buttons"]) == 2:
        assert not rects["buttons"][0].colliderect(rects["buttons"][1])


@pytest.mark.parametrize("h", [480, 576, 720])
@pytest.mark.parametrize("update", [False, True])
def test_chat_stays_visible_with_raid_card(h, update):
    from yellowlink import ui
    plan = ui.sidebar_plan(h, update, True)
    assert plan["chat"] >= ui.CHAT_MIN
    assert ui.sidebar_plan(h, update, False)["wide"] is True        # nothing changes without a raid
