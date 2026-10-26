"""Badges, app side (SPEC.md section 9): the badge book, sync with the server, toasts, panels, shiny marks."""
import base64
import json
import os
import sys
import types

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pygame  # noqa: E402
import pytest  # noqa: E402

from yellowlink import badge_art, badgesync, library, panels, ui  # noqa: E402
from yellowlink.badgesync import BadgeBook, BadgeMixin  # noqa: E402

T0 = 1.8e9


# --- the book -------------------------------------------------------------------------------------------------
def test_apply_earns_once_and_never_removes(tmp_path):
    book = BadgeBook(tmp_path / "badges" / "me.json")
    new = book.apply("kanto", {"champion": True, "dex_complete": False, "shinies": ["aa"]}, now=T0)
    assert new == [("champion", None), ("shiny", 1)]
    assert book.changed
    # the same reading again: nothing new, nothing written
    assert book.apply("kanto", {"champion": True, "shinies": ["aa"]}, now=T0 + 5) == []
    assert not book.changed
    # a new game (no Hall of Fame, shiny traded away) takes nothing away
    assert book.apply("kanto", {"champion": False, "dex_complete": False, "shinies": []}, now=T0 + 9) == []
    assert book.earned["kanto"]["champion"] == T0 and book.earned["kanto"]["shinies"] == ["aa"]
    # tiers: the 10th distinct shiny reaches tier 10
    keys = [f"k{i}" for i in range(9)]
    assert book.apply("kanto", {"shinies": keys}, now=T0) == [("shiny", 10)]
    assert book.apply("kanto", {"dex_complete": True}, now=T0) == [("dex", None)]
    again = BadgeBook(tmp_path / "badges" / "me.json")          # persisted
    assert again.earned == book.earned
    assert again.summary() == {"kanto": {"champion": True, "dex": True, "shiny": 10, "soullink": None}}


def test_apply_ignores_bad_input(tmp_path):
    book = BadgeBook(tmp_path / "b.json")
    assert book.apply("hoenn", {"champion": True}) == []
    assert book.apply("kanto", None) == []
    assert book.apply("kanto", {"shinies": ["bad key!", "x" * 50]}) == []
    assert book.earned == {}
    assert not (tmp_path / "b.json").exists()


def test_damaged_book_is_kept_aside(tmp_path):
    p = tmp_path / "b.json"
    p.write_text("{not json")
    book = BadgeBook(p)
    assert book.earned == {}
    assert any(n.name.startswith("badges.bad-") for n in tmp_path.iterdir())


def test_wire_format_matches_spec(tmp_path):
    book = BadgeBook(tmp_path / "b.json")
    book.apply("johto", {"champion": True, "shinies": ["0a0b0c0d0e0f"]}, now=T0)
    msg = book.wire()
    assert msg == {"type": "badges", "earned": {"johto": {"champion": T0, "dex": None,
                                                          "shinies": ["0a0b0c0d0e0f"]}}}
    json.dumps(msg)


def test_soullink_from_server_cached_per_server(tmp_path):
    book = BadgeBook(tmp_path / "b.json")
    assert book.note_server("main", {"kanto": {"soullink": "silver"}}) == [("kanto", "silver")]
    assert book.note_server("main", {"kanto": {"soullink": "silver"}}) == []
    assert book.note_server("main", {"kanto": {"soullink": "gold"}}) == [("kanto", "gold")]
    assert book.note_server("main", {"kanto": {"soullink": "silver"}}) == []     # gold stays
    assert book.summary("main")["kanto"]["soullink"] == "gold"
    assert book.summary("fly") == {}                                              # per server
    assert BadgeBook(tmp_path / "b.json").soullink == {"main": {"kanto": "gold"}}


def test_combine_takes_the_best_of_both():
    a = {"kanto": {"champion": True, "dex": False, "shiny": 3, "soullink": "silver"}}
    b = {"kanto": {"champion": False, "dex": True, "shiny": 1, "soullink": "gold"}, "sinnoh": {"shiny": 2}}
    c = badgesync.combine(a, b)
    assert c["kanto"] == {"champion": True, "dex": True, "shiny": 3, "soullink": "gold"}
    assert c["sinnoh"] == {"champion": False, "dex": False, "shiny": 2, "soullink": None}


def test_texts():
    assert badgesync.earned_text("kanto", "champion") == "Badge earned: Kanto Champion!"
    assert badgesync.earned_text("johto", "dex") == "Badge earned: Johto Pokédex!"
    assert badgesync.earned_text("sinnoh", "shiny", 1) == "Badge earned: Sinnoh Shiny hunter!"
    assert badgesync.earned_text("sinnoh", "shiny", 25) == "Shiny hunter 25: Sinnoh!"
    assert badgesync.earned_text("kanto", "soullink", "silver") == "Badge earned: Kanto Soul Link (silver)!"
    assert "150" in badgesync.how_to("dex", "kanto") and "249" in badgesync.how_to("dex", "johto")
    assert "Gold" in badgesync.how_to("soullink", "johto")


# --- the app side -------------------------------------------------------------------------------------------
class FakeNet:
    def __init__(self):
        self.sent, self.status = [], "online"

    def send(self, m):
        if self.status != "online":
            return False
        self.sent.append(m)
        return True


class FakeGame:
    def __init__(self):
        self.safe, self.battle, self.shiny = True, False, False

    def in_game(self):
        return True

    def is_safe(self):
        return self.safe

    def in_battle(self):
        return self.battle


class FakeCfg(dict):
    server_id = "main"

    def __init__(self, path):
        super().__init__(uid="me", name="Ilias")
        self.badges_path = path


class SyncApp(BadgeMixin):
    def __init__(self, tmp_path, game_id="yellow"):
        self.cfg = FakeCfg(tmp_path / "badges" / "me.json")
        self.net = FakeNet()
        self.game = FakeGame()
        self.reading = {"champion": False, "dex_complete": False, "shinies": [], "owner": None}
        self.calls = 0

        def progress(mem):
            assert mem is self.game
            self.calls += 1
            return self.reading
        self.G = types.SimpleNamespace(progress=progress, wild_is_shiny=lambda mem: mem.shiny)
        self.game_id, self.full, self.chal = game_id, True, None
        self.trade = self.battle = self.live = None
        self.frame = 0
        self.roster, self.toasts, self.log = [], [], []
        self.init_badges()

    def toast(self, s, *a):
        self.toasts.append(s)

    def sys_msg(self, s, *a):
        self.log.append(s)

    def run(self, frames):
        for _ in range(frames):
            self.badges_step()
            self.frame += 1


def test_sync_checks_merges_toasts_and_reports(tmp_path):
    app = SyncApp(tmp_path)
    app.badges_on_welcome({"features": ["badges"], "roster": []})
    assert app.net.sent == []                                  # nothing earned yet: nothing to report
    app.reading = {"champion": True, "dex_complete": False, "shinies": ["aa"]}
    app.run(badgesync.CHECK_EVERY)
    assert app.calls == 1
    assert app.toasts == ["Badge earned: Kanto Champion!", "Badge earned: Kanto Shiny hunter!"]
    assert app.net.sent[-1]["type"] == "badges" and app.net.sent[-1]["earned"]["kanto"]["shinies"] == ["aa"]
    # a second shiny (no new tier): no toast, but reported so the count others see follows
    app.reading = {"champion": True, "shinies": ["aa", "bb"]}
    n = len(app.net.sent)
    app.run(badgesync.CHECK_EVERY)
    assert len(app.toasts) == 2 and len(app.net.sent) == n + 1
    # nothing new: nothing sent
    app.run(badgesync.CHECK_EVERY)
    assert len(app.net.sent) == n + 1


def test_no_checks_in_a_run_unsafe_or_unverified(tmp_path):
    app = SyncApp(tmp_path)
    app.reading = {"champion": True}
    app.chal = {"id": "c1", "game": "yellow"}               # Soul Link run: never counts for main-game badges
    app.run(badgesync.CHECK_EVERY * 2)
    app.chal, app.game.safe = None, False                    # not a safe moment
    app.run(badgesync.CHECK_EVERY * 2)
    app.game.safe, app.full = True, False                    # unverified ROM
    app.run(badgesync.CHECK_EVERY * 2)
    app.full, app.trade = True, object()                     # mid trade
    app.run(badgesync.CHECK_EVERY * 2)
    assert app.calls == 0 and app.badges.earned == {}
    app.trade = None
    app.run(badgesync.CHECK_EVERY)
    assert app.calls == 1 and "champion" in app.badges.earned["kanto"]


def test_reports_only_to_servers_that_know_badges(tmp_path):
    app = SyncApp(tmp_path)
    app.badges.apply("kanto", {"champion": True})
    app.badges_on_welcome({"roster": []})                     # older server: no features list
    app.run(badgesync.CHECK_EVERY)
    assert not any(m["type"] in ("badges", "get_badges") for m in app.net.sent)
    app.request_badges("you")
    assert app.net.sent == []
    # offline when earned -> reported on the next welcome
    app.net.status = "offline"
    app.reading = {"champion": True, "dex_complete": True}
    app.run(badgesync.CHECK_EVERY)
    app.net.status = "online"
    app.badges_on_welcome({"features": ["badges"], "roster": []})
    assert app.net.sent[-1]["earned"]["kanto"]["dex"] is not None


def test_shared_book_reported_to_each_server(tmp_path):
    """Two servers sharing one save share its book: whichever server we're on gets the same badges."""
    a = SyncApp(tmp_path)
    a.reading = {"champion": True}
    a.run(badgesync.CHECK_EVERY)
    b = SyncApp(tmp_path)                       # same folder, other server
    b.cfg.server_id = "fly"
    b.badges_on_welcome({"features": ["badges"], "roster": []})
    assert b.net.sent[-1]["earned"]["kanto"]["champion"] is not None


def test_soullink_toast_from_own_roster_entry(tmp_path):
    app = SyncApp(tmp_path)
    me = {"uid": "me", "badges": {"kanto": {"champion": False, "dex": False, "shiny": 0, "soullink": "gold"}}}
    app.roster = [me]
    app.badges_on_welcome({"features": ["badges"], "roster": [me]})       # known at login: quiet
    assert app.toasts == []
    me2 = {"uid": "me", "badges": {"johto": {"soullink": "silver"}, **me["badges"]}}
    app.roster = [me2]
    app.badges_on_roster()
    assert app.toasts == ["Badge earned: Johto Soul Link (silver)!"]
    assert app.badge_summary("me")["kanto"]["soullink"] == "gold"


def test_badge_summary_and_times(tmp_path):
    app = SyncApp(tmp_path)
    app.badges.apply("sinnoh", {"champion": True, "shinies": ["a1"]}, now=T0)
    app.roster = [{"uid": "me", "badges": {"sinnoh": {"champion": False, "dex": True, "shiny": 0,
                                                      "soullink": None}}},
                  {"uid": "you", "badges": {"kanto": {"champion": True, "dex": False, "shiny": 12,
                                                      "soullink": "silver"}}}]
    assert app.badge_summary("me")["sinnoh"] == {"champion": True, "dex": True, "shiny": 1, "soullink": None}
    assert app.badge_summary("you")["kanto"]["shiny"] == 12
    assert app.badge_summary("nobody") == {}
    assert app.badge_time("me", "sinnoh", "champion") == T0
    app.on_badges_data({"uid": "you", "badges": {"kanto": {"champion": T0 + 1, "soullink_ts": T0 + 2}}})
    assert app.badge_time("you", "kanto", "champion") == T0 + 1
    assert app.badge_time("you", "kanto", "soullink") == T0 + 2


def test_pc_store_shinies_counted(tmp_path, monkeypatch):
    app = SyncApp(tmp_path)
    app.pcsync = types.SimpleNamespace(committed={"boxes": [[{"id": "e1", "gen1": "x"}, {"id": "e2", "gen1": "y"}]]})
    monkeypatch.setattr(badgesync.rules, "entry_shiny_key",
                        lambda e, region, owner: "pc" + e["id"] if e["id"] == "e2" and owner else None)
    app.reading = {"champion": False, "shinies": ["g1"], "owner": {"ot": "ILIAS", "tid": 1}}
    app.run(badgesync.CHECK_EVERY)
    assert app.badges.earned["kanto"]["shinies"] == ["g1", "pce2"]


def test_wild_shiny_flag_yellow_only(tmp_path):
    app = SyncApp(tmp_path)
    app.game.battle, app.game.shiny = True, True
    app.run(badgesync.WILD_EVERY)
    assert app.wild_shiny
    app.game.battle = False
    app.run(badgesync.WILD_EVERY)
    assert not app.wild_shiny
    other = SyncApp(tmp_path, game_id="crystal")
    other.game.battle = other.game.shiny = True
    other.run(badgesync.WILD_EVERY)
    assert not other.wild_shiny


# --- drawing ----------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def fonts():
    pygame.init()
    pygame.display.set_mode((10, 10))
    return ui.Fonts()


class PanelApp(SyncApp):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.cfg.server_label = lambda s: "Main"
        self.cfg.active_server = {"id": "main"}
        self.cfg.servers = [{"id": "main"}]
        self.lib = types.SimpleNamespace(games={"yellow": "/y", "crystal": "/c"})
        self.game_info = library.GAMES_BY_ID["yellow"]
        self.click_targets = []
        self.panel_sel = "you"
        self.hooks = None
        self.gen2 = self.ds = False
        self.roster = [{"uid": "me", "name": "Ilias", "online": True, "badges": {}},
                       {"uid": "you", "name": "Alex", "online": True, "levels": [5],
                        "badges": {"johto": {"champion": True, "dex": False, "shiny": 27, "soullink": "gold"}}}]

    def __getattr__(self, name):                     # buttons' callbacks / helpers not needed here
        if name.startswith(("action_", "toggle_", "open_", "travel", "add_", "pick_", "close_", "select_")):
            return lambda *a, **k: None
        raise AttributeError(name)

    def sorted_roster(self):
        return self.roster

    def player_region(self, e):
        return "Kanto"

    def is_nearby(self, uid):
        return False

    def friend_game(self, uid):
        return "yellow"

    def challenges_with(self, uid):
        return []


@pytest.mark.parametrize("size", [(480, 432), (640, 576), (384, 576)])      # Yellow 3x/4x, DS 1.5x
def test_regions_panel_has_sockets_that_fit(tmp_path, fonts, size):
    app = PanelApp(tmp_path)
    app.badges.apply("kanto", {"champion": True, "shinies": [f"s{i}" for i in range(10)]}, now=T0)
    surf = pygame.Surface(size)
    panels.draw_regions(surf, fonts, app, size[0], size[1], (0, 0))
    socks = [(r, label) for r, _, label in app.click_targets if label.startswith("badge:")]
    assert len(socks) == 12                                       # 3 regions x 4 sockets
    travel = [r for r, _, label in app.click_targets if label == "Travel"]
    panel = pygame.Rect(0, 0, size[0], size[1])
    for r, _ in socks:
        assert panel.contains(r)
        assert not any(r.colliderect(t) for t in travel)
    # click = the tip as a toast
    cb = next(c for r, c, label in app.click_targets if label == "badge:kanto:shiny")
    cb()
    assert app.toasts[-1].startswith("Kanto Shiny hunter 10 - 10 shiny catches")


def test_multiplayer_shows_selected_players_badges(tmp_path, fonts):
    app = PanelApp(tmp_path)
    for size in ((480, 432), (640, 576), (384, 576)):
        app.click_targets = []
        surf = pygame.Surface(size)
        panels.draw_multiplayer(surf, fonts, app, size[0], size[1], (0, 0))
        socks = [r for r, _, label in app.click_targets if label.startswith("badge:")]
        assert len(socks) == 12, size
        assert all(pygame.Rect(0, 0, *size).contains(r) for r in socks)


def test_tip_lines():
    s = {"champion": False, "dex": False, "shiny": 27, "soullink": "silver"}
    lines = [t for t, _ in badge_art.tip_lines("shiny", "johto", s)]
    assert lines[0] == "Johto Shiny hunter 25" and "27 shiny catches - next tier at 50" in lines[1]
    lines = [t for t, _ in badge_art.tip_lines("soullink", "kanto", s, when=T0)]
    assert lines[0] == "Kanto Soul Link (silver)" and lines[1].startswith("Silver - run finished on ")
    lines = [t for t, _ in badge_art.tip_lines("champion", "sinnoh", s)]
    assert lines[1] == "Not earned yet" and "Hall of Fame" in lines[2]


def test_sockets_draw_differently(fonts):
    def pixels(kind, s):
        surf = pygame.Surface((30, 30))
        badge_art.draw_socket(surf, (15, 15), kind, s, 11, fonts.tag)
        return pygame.image.tobytes(surf, "RGB")
    empty = {"champion": False, "dex": False, "shiny": 0, "soullink": None}
    full = {"champion": True, "dex": True, "shiny": 30, "soullink": "gold"}
    for kind in badgesync.SOCKETS:
        assert pixels(kind, empty) != pixels(kind, full)
    assert pixels("soullink", full) != pixels("soullink", dict(full, soullink="silver"))
    assert pixels("shiny", dict(full, shiny=1)) != pixels("shiny", full)        # tier pill


def test_shiny_indicator_draws(fonts):
    surf = pygame.Surface((640, 576))
    box = badge_art.draw_shiny_indicator(surf, fonts, 300, 10, 4)
    assert surf.get_rect().contains(box) and box.w > 40


def _gen1_entry(dvs):
    rec = bytearray(55)
    rec[0], rec[27], rec[28] = 0x54, dvs >> 8, dvs & 0xFF
    return {"id": "e", "origin": "yellow", "dex": 25, "level": 5, "nick": "PIKA",
            "gen1": base64.b64encode(bytes(rec)).decode()}


def test_shiny_mark_in_trade_rows(fonts):
    app = types.SimpleNamespace(entry_label=lambda e: "PIKA Lv5")
    plain, shiny = _gen1_entry(0x1234), _gen1_entry(0xEAAA)
    assert not panels.is_shiny_entry(app, plain) and panels.is_shiny_entry(app, shiny)
    a, b = pygame.Surface((200, 30)), pygame.Surface((200, 30))
    panels.mon_label(a, fonts, app, plain, (2, 2), ui.TEXT, 190)
    panels.mon_label(b, fonts, app, shiny, (2, 2), ui.TEXT, 190)
    assert pygame.image.tobytes(a, "RGB") != pygame.image.tobytes(b, "RGB")
