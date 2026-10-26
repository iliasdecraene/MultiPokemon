"""Exp Share All as a Soul Link rule: set when the run is created, applies to both players, legacy runs = off."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import app as appmod, panels  # noqa: E402
from yellowlink.challenge import ChallengeMixin  # noqa: E402


class FakeNet:
    def __init__(self):
        self.sent = []
        self.status = "online"

    def send(self, m):
        self.sent.append(m)
        return True


class FakeApp(ChallengeMixin):
    def __init__(self, roster=(), exp_share_all=True):
        self.cfg = {"uid": "me", "name": "Ilias", "exp_share_all": exp_share_all}
        self.lib = types.SimpleNamespace(games={"yellow": "/y"}, rom_path=lambda g: "/y", current="yellow",
                                         info=lambda g: types.SimpleNamespace(title="Pokémon Yellow"))
        self.roster = list(roster)
        self.net = FakeNet()
        self.toasts = []
        self.challenges = {}
        self.chal = self.chal_rec = None
        self.trade = self.battle = self.live = self.prompt = None
        self.enter_challenge = None
        self.quit_requested = False

    def _game_supported(self, g):
        return g in self.lib.games

    def toast(self, s, *a):
        self.toasts.append(s)

    def close_panel(self):
        pass

    exp_share_on = appmod.App.exp_share_on


NEW = {"uid": "you", "name": "Alex", "games": ["yellow"], "challenge_games": ["yellow"],
       "challenge_rules": ["exp_share"]}
OLD = {"uid": "you", "name": "Alex", "games": ["yellow"], "challenge_games": ["yellow"]}       # 0.7.3


def test_library_advertises_the_rule():
    app = FakeApp()
    app.chal_on_connected()
    assert app.net.sent[-1]["challenge_rules"] == ["exp_share"]


def test_form_default_is_my_setting_and_needs_partner_support():
    app = FakeApp([NEW], exp_share_all=True)
    app.new_challenge_form("you", "Alex")
    assert app.chal_form["exp_share"] is True and app.can_exp_share_rule("you")
    app.send_challenge()
    assert app.net.sent[-1]["rules"] == {"dupes": True, "shiny": True, "exp_share": True}
    app = FakeApp([NEW], exp_share_all=False)
    app.new_challenge_form("you", "Alex")
    assert app.chal_form["exp_share"] is False
    app = FakeApp([OLD], exp_share_all=True)                 # partner can't play it: off, can't be switched on
    app.new_challenge_form("you", "Alex")
    assert app.chal_form["exp_share"] is False and not app.can_exp_share_rule("you")
    app.toggle_exp_share_rule()
    assert app.chal_form["exp_share"] is False and app.toasts
    app.send_challenge()
    assert app.net.sent[-1]["rules"]["exp_share"] is False


def test_in_a_run_the_runs_rule_wins_for_both_players():
    for mine in (True, False):
        app = FakeApp(exp_share_all=mine)
        assert app.exp_share_on() is mine                      # main game: my own setting
        app.chal = {"id": "c1", "game": "yellow", "rules": {"exp_share": True}}
        assert app.exp_share_on() is True                       # run: the run's rule
        app.chal_rec = {"id": "c1", "rules": {"dupes": True, "shiny": True, "exp_share": False}}
        assert app.exp_share_on() is False                      # the server's record wins over the copy
        app.chal_rec = {"id": "c1", "rules": {"dupes": True, "shiny": True}}
        assert app.exp_share_on() is False                      # run from before the rule: off


def test_entering_a_run_remembers_its_rules():
    app = FakeApp()
    app.cfg = type("C", (dict,), {"challenge_dir": lambda self, cid: None})(app.cfg)
    app.has_challenge_save = lambda cid: False
    app.on_challenge_go({"challenge": {"id": "c1", "game": "yellow", "players": ["me", "you"], "events": 0,
                                       "names": {"you": "Alex"}, "rules": {"dupes": True, "exp_share": True}}})
    assert app.enter_challenge == {"id": "c1", "game": "yellow", "rules": {"dupes": True, "exp_share": True}}


def test_settings_note_in_a_run():
    app = FakeApp(exp_share_all=False)
    assert panels.exp_share_run_note(app) is None
    app.chal = {"id": "c1", "game": "yellow", "rules": {"exp_share": True}}
    note = panels.exp_share_run_note(app)
    assert "This Soul Link run: Exp Share All on" in note and "set when the run started" in note


def test_rules_line_mentions_exp_share():
    assert "Exp Share All: on" in panels.rules_line({"dupes": True, "shiny": False, "exp_share": True})
    assert "Exp Share All: off" in panels.rules_line({"dupes": True})
