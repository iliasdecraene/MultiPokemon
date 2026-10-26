"""Servers (one per group of friends): each has its own address, password, player id and folders."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import config as cfgmod  # noqa: E402
from yellowlink.config import Config  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="app_dir uses XDG_CONFIG_HOME on Linux")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "yellowlink"


def make(home, data=None):
    home.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (home / "config.json").write_text(json.dumps(data))
    return Config()


def test_old_config_becomes_the_first_server_and_keeps_its_folders(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "room_key": "pw", "name": "Ilias", "uid": "u-old",
                    "current_game": "yellow"})
    assert c.server_id == "main" and len(c.servers) == 1
    s = c.active_server
    assert (s["url"], s["key"], s["uid"]) == ("ws://1.2.3.4:8765", "pw", "u-old")
    assert c.data_dir == home
    assert c.save_path == home / "saves" / "yellow.sav"
    assert c.resume_path == home / "resume" / "yellow.state"
    assert c.pc_path == home / "pc" / "u-old.json"
    assert c.backup_dir == home / "saves" / "backups"
    assert c.challenge_dir("abc") == home / "challenges" / "abc"
    assert json.loads((home / "config.json").read_text())["servers"][0]["id"] == "main"


def test_add_and_switch_server_uses_its_own_everything(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "room_key": "pw", "name": "Ilias", "uid": "u-old",
                    "current_game": "crystal"})
    s2 = c.add_server("Work friends", "1.2.3.4:8766", "other")
    assert s2["url"] == "ws://1.2.3.4:8766" and s2["uid"] != "u-old"
    assert c.server_id == "main"                       # adding doesn't switch
    c.switch_server(s2["id"])
    assert (c["server_url"], c["room_key"], c["uid"]) == ("ws://1.2.3.4:8766", "other", s2["uid"])
    assert c.data_dir == home / "servers" / s2["id"]
    assert c.save_path == home / "servers" / s2["id"] / "saves" / "crystal.sav"
    assert c.pc_path.parent == home / "servers" / s2["id"] / "pc"
    assert c.challenge_dir("x").parent == home / "servers" / s2["id"] / "challenges"
    c["current_game"] = "yellow"                        # travelled on the second server
    c.switch_server("main")
    assert (c["server_url"], c["room_key"], c["uid"]) == ("ws://1.2.3.4:8765", "pw", "u-old")
    assert c["current_game"] == "crystal"              # each server remembers its own region
    assert c.save_path == home / "saves" / "crystal.sav"
    c2 = Config()                                       # survives a restart
    assert c2.server_id == "main" and len(c2.servers) == 2
    c2.switch_server(s2["id"])
    assert c2["current_game"] == "yellow" and c2["uid"] == s2["uid"]


def test_settings_window_edits_go_to_the_active_server(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "room_key": "pw", "name": "Ilias", "uid": "u"})
    s2 = c.add_server("", "ws://5.6.7.8:8765", "k")
    c["room_key"] = "new"                               # what the settings window does
    c.save()
    assert c.active_server["key"] == "new" and c.server(s2["id"])["key"] == "k"


def test_duplicate_and_bad_addresses_are_refused(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "room_key": "pw", "name": "Ilias", "uid": "u"})
    with pytest.raises(ValueError):
        c.add_server("", "WS://1.2.3.4:8765/", "x")
    with pytest.raises(ValueError):
        c.add_server("", "", "x")
    s2 = c.add_server("B", "ws://1.2.3.4:8766", "x")
    with pytest.raises(ValueError):
        c.edit_server(s2["id"], "B", "ws://1.2.3.4:8765", "x")
    c.edit_server(s2["id"], "Renamed", "ws://1.2.3.4:8767", "y")
    assert Config.server_label(c.server(s2["id"])) == "Renamed"


def test_remove_server(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "room_key": "pw", "name": "Ilias", "uid": "u"})
    with pytest.raises(ValueError):
        c.remove_server("main")                        # the one you're on
    s2 = c.add_server("", "ws://1.2.3.4:8766", "x")
    c.switch_server(s2["id"])
    with pytest.raises(ValueError):
        c.remove_server(s2["id"])
    c.remove_server("main")
    assert [s["id"] for s in c.servers] == [s2["id"]]


def test_labels(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "uid": "u"})
    s = c.active_server
    assert Config.server_label(s) == "1.2.3.4:8765"
    s["seen_name"] = "Kanto Crew"
    assert Config.server_label(s) == "Kanto Crew"
    s["label"] = "Mine"
    assert Config.server_label(s) == "Mine"


def test_switching_leaves_a_challenge(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "uid": "u"})
    s2 = c.add_server("", "ws://1.2.3.4:8766", "")
    c["active_challenge"] = {"id": "c1", "game": "yellow"}
    c.switch_server(s2["id"])
    assert c.challenge is None


def test_fresh_install(home):
    c = make(home)
    assert c.server_id == cfgmod.MAIN_SERVER and c.active_server["uid"] == c["uid"]


def test_address_typos_are_caught():
    from yellowlink.config import normalize_url, url_looks_ok
    good = ["91.107.193.182:8766", " ws://91.107.193.182:8765 ", "http://91.107.193.182:8765", "91.107.193.182 :8766",
            "wss://example.com", "ws://localhost:8765", "WS://91.107.193.182:8765/"]
    bad = ["ws://91,107.193.182:8765", "ws://91.1O7.193.182:8765", "ws://ws://91.107.193.182:8765",
           "ws://91.107.193:8765", "ws://91.107.193.300:8765", "", "ws://abc"]
    for u in good:
        assert url_looks_ok(normalize_url(u)), u
    for u in bad:
        assert not url_looks_ok(normalize_url(u)), u
    assert normalize_url("https://91.107.193.182:8765") == "ws://91.107.193.182:8765"


def test_server_moved_keeps_folder_and_player(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8765", "room_key": "one", "name": "Ilias", "uid": "u-main"})
    fly = c.add_server("", "ws://1.2.3.4:8766", "two")
    fly["seen_name"] = "Fly"
    c.switch_server(fly["id"])
    folder, uid = c.data_dir, c["uid"]
    twin = c.move_server(fly["id"], "ws://1.2.3.4:8765", "one")
    assert twin                                         # Ilias also has the main server: two saves, two characters
    assert (c["server_url"], c["room_key"]) == ("ws://1.2.3.4:8765", "one")
    assert c.data_dir == folder and c["uid"] == uid     # same saves, same player id
    assert Config.server_label(c.server(fly["id"])) == "Fly (save)"


def test_server_moved_for_a_fly_only_player(home):
    c = make(home, {"server_url": "ws://1.2.3.4:8766", "room_key": "two", "name": "Bea", "uid": "u-bea"})
    c.active_server["seen_name"] = "Fly"
    assert not c.move_server(c.server_id, "1.2.3.4:8765", "one")
    assert (c["server_url"], c["room_key"], c["uid"]) == ("ws://1.2.3.4:8765", "one", "u-bea")
    assert c.data_dir == home and "seen_name" not in c.active_server   # the new server will tell its name
    with pytest.raises(ValueError):
        c.move_server(c.server_id, "not an address", "x")
