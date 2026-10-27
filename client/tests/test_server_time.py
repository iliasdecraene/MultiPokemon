"""M3: the server's clock for the shared 2-hour swarm (SPEC.md section 11): welcome.now / pong.now -> time offset."""
import os
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from yellowlink import app as appmod, net  # noqa: E402

YEAR = 365 * 24 * 3600


def test_pong_sets_the_offset_with_half_the_round_trip():
    n = net.NetClient("ws://x", {"name": "a", "uid": "u"})
    assert n.time_offset is None
    n.on_pong({"t": 1000.0, "now": 5000.0}, received=1000.4)      # rtt 0.4 s: server time at receipt = 5000.2
    assert abs(n.time_offset - 3999.8) < 1e-6 and n.rtt_ms == 400
    n.on_pong({"t": 1000.0}, received=1000.2)                      # older server: no `now` -> unchanged
    assert abs(n.time_offset - 3999.8) < 1e-6
    n.on_pong({"t": 1000.0, "now": 1000.0 + 2 * YEAR}, received=1000.1)   # absurd: ignored
    n.on_pong({"t": 1000.0, "now": "soon"}, received=1000.1)
    assert abs(n.time_offset - 3999.8) < 1e-6
    n.set_server_time(None)
    assert abs(n.time_offset - 3999.8) < 1e-6


def test_welcome_sets_it_and_offline_is_the_local_clock():
    n = net.NetClient("ws://x", {"name": "a", "uid": "u"})
    n.set_server_time(time.time() + 3 * 3600)
    assert abs(n.time_offset - 3 * 3600) < 2
    fake = types.SimpleNamespace(net=n)
    n.status = "online"
    assert abs(appmod.App.server_time(fake) - (time.time() + 3 * 3600)) < 2
    n.status = "offline"
    assert abs(appmod.App.server_time(fake) - time.time()) < 2
    n.status = "online"
    n.time_offset = None                                          # older server
    assert abs(appmod.App.server_time(fake) - time.time()) < 2
    assert abs(appmod.App.server_time(types.SimpleNamespace()) - time.time()) < 2     # no net yet


def test_until_text_is_on_the_players_own_clock():
    """The slot ends at a server time; 'until HH:MM' shows when that is on the player's PC clock."""
    n = types.SimpleNamespace(status="online", time_offset=3 * 3600.0)
    fake = types.SimpleNamespace(net=n)
    ends = 1_800_000_000.0
    assert appmod.App.local_time_of(fake, ends) == ends - 3 * 3600
