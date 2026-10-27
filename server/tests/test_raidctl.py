"""Admin raids (SPEC.md section 12, "Admin raids"): `raidctl` checks the choices against the server's data and hands
the request to the running server through <data_dir>/admin; the server opens the raid through the normal path.
Also the host's `yl-raid` script (server/tools/yl-raid) with a fake `docker` on PATH."""
import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import time

import pytest

from yellowlink_server import raidctl as C
from yellowlink_server import raids as R

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(HERE)
SCRIPT = os.path.join(SERVER_DIR, "tools", "yl-raid")
SPOTS = R.load_spots()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def hub(server):
    h = server.raids
    h.auto = False                       # tests drive the scheduler themselves (admin requests still polled)
    h.clock = Clock()
    h.update_every = 0.02
    h.admin_every = 0.05
    h.salt = "test-salt"
    return h


async def raider(connect_client, name, games=("yellow", "crystal", "platinum"), game="yellow"):
    c = await connect_client(name)
    await c.send(type="library", games=list(games), raid_reach={"kanto": 8, "johto": 8, "sinnoh": 8})
    await c.send(type="state", map=12, x=5, y=8, px=80, py=128, facing=4, img=0, visible=True, game=game)
    await asyncio.sleep(0.05)
    return c


def ns(**kw):
    base = dict(region=None, place=None, pokemon=None, shiny=None, minutes=None, replace=False)
    base.update(kw)
    return argparse.Namespace(**base)


def req(**kw):
    base = {"action": "spawn", "created": time.time(), "regions": None, "species": None, "place": None,
            "shiny": None, "minutes": None, "replace": False}
    base.update(kw)
    return base


# ----------------------------------------------------------------------- raidctl: checking the choices
def test_resolve_everything_random():
    r = C.resolve(ns(), SPOTS, 15.0)
    assert r == {"action": "spawn", "regions": ["kanto", "johto", "sinnoh"], "species": None, "place": None,
                 "shiny": None, "minutes": 15.0, "replace": False}
    r = C.resolve(ns(region="random", place="Random", pokemon="RANDOM", shiny="random"), SPOTS, 15.0)
    assert r["regions"] == ["kanto", "johto", "sinnoh"] and r["species"] is None and r["place"] is None


def test_resolve_names_loosely():
    r = C.resolve(ns(region="Sinnoh", pokemon="RAYQUAZA", place="route 210", shiny="yes", minutes="20"), SPOTS, 15)
    assert r["regions"] == ["sinnoh"] and r["species"] == 384 and r["place"] == "Route 210"
    assert r["shiny"] is True and r["minutes"] == 20
    assert C.resolve(ns(pokemon="hooh"), SPOTS, 15)["species"] == 250
    assert C.resolve(ns(pokemon="Ho-Oh"), SPOTS, 15)["species"] == 250
    assert C.resolve(ns(pokemon="#384"), SPOTS, 15)["species"] == 384
    assert C.resolve(ns(pokemon="151"), SPOTS, 15)["species"] == 151
    assert C.resolve(ns(region="sinnoh", place="pokemon league"), SPOTS, 15)["place"] == "Pokémon League"
    assert C.resolve(ns(region="sinnoh", place="210"), SPOTS, 15)["place"] == "Route 210"
    assert C.resolve(ns(region="platinum"), SPOTS, 15)["regions"] == ["sinnoh"]
    assert C.resolve(ns(shiny="no"), SPOTS, 15)["shiny"] is False


def test_resolve_random_region_fits_the_choices():
    # a Pokémon in several regions: any of those; only in one: that one
    assert C.resolve(ns(pokemon="mewtwo"), SPOTS, 15)["regions"] == ["kanto", "johto", "sinnoh"]
    assert C.resolve(ns(pokemon="lugia"), SPOTS, 15)["regions"] == ["johto", "sinnoh"]
    assert C.resolve(ns(pokemon="arceus"), SPOTS, 15)["regions"] == ["sinnoh"]
    # a place decides the region
    r = C.resolve(ns(place="cerulean city"), SPOTS, 15)
    assert r["regions"] == ["kanto"] and r["place"] == "Cerulean City"
    r = C.resolve(ns(pokemon="mew", place="Route 29"), SPOTS, 15)
    assert r["regions"] == ["johto"] and r["species"] == 151


@pytest.mark.parametrize("kw, words", [
    (dict(region="sinoh"), ["no region called 'sinoh'", "Did you mean sinnoh"]),
    (dict(pokemon="raykwaza"), ["isn't a raid Pokémon", "Did you mean Rayquaza"]),
    (dict(pokemon="25"), ["#25 isn't a raid Pokémon"]),
    (dict(region="kanto", pokemon="rayquaza"), ["Rayquaza isn't one of Kanto's raid Pokémon", "can appear in Sinnoh"]),
    (dict(region="sinnoh", place="Route 21"), ["isn't a raid place in Sinnoh", "Did you mean", "Route 2"]),
    (dict(region="sinnoh", place="Route 1"), ["isn't a raid place in Sinnoh", "Route 1 is in Kanto"]),
    (dict(place="eterna"), ["isn't a raid place in any region", "Eterna City", "Eterna Forest"]),
    (dict(pokemon="arceus", place="route 1"), ["Route 1 is in Kanto", "Arceus", "Sinnoh"]),
    (dict(shiny="maybe"), ["--shiny must be yes, no or random"]),
    (dict(minutes="500"), ["between 1 and 120"]),
    (dict(minutes="soon"), ["must be a number"]),
])
def test_resolve_refusals(kw, words):
    with pytest.raises(C.Refused) as exc:
        C.resolve(ns(**kw), SPOTS, 15)
    for w in words:
        assert w in str(exc.value), (w, str(exc.value))


def test_list_is_readable():
    text = C.format_list(SPOTS)
    for w in ("KANTO", "JOHTO", "SINNOH", "Pokémon Yellow", "Rayquaza (384)", "Route 210", "Cerulean City"):
        assert w in text
    one = C.format_list(SPOTS, "johto")
    assert "JOHTO" in one and "KANTO " not in one and "Lugia (249)" in one
    # towns first, then the routes by number (Route 2 before Route 10)
    assert one.index("Violet City") < one.index("Route 26")
    kanto = C.format_list(SPOTS, "kanto")
    assert kanto.index("Route 2,") < kanto.index("Route 10")


def test_main_list_and_refusals(tmp_path, capsys):
    env = {"YL_DATA_DIR": str(tmp_path)}
    assert C.main(["--list", "--region", "johto"], env=env) == 0
    assert "JOHTO" in capsys.readouterr().out
    assert C.main(["--region", "kanto", "--pokemon", "lugia", "--label", "Fly"], env=env) == 2
    out = capsys.readouterr().out
    assert "Lugia isn't one of Kanto's" in out and "Nothing was changed on Fly." in out
    assert not os.path.exists(tmp_path / "admin") or not os.listdir(tmp_path / "admin")
    assert C.main(["--label", "Fly"], env=dict(env, YL_RAIDS="off")) == 1
    assert "Raids are turned off on Fly" in capsys.readouterr().out


def test_submit_withdraws_when_nobody_answers(tmp_path):
    t0 = time.monotonic()
    assert C.submit(str(tmp_path / "admin"), {"action": "spawn"}, wait=0.2) is None
    assert time.monotonic() - t0 < 2
    assert os.listdir(tmp_path / "admin") == []          # withdrawn: a server started later won't act on it


# ----------------------------------------------------------------------- the server side
def test_pick_admin_respects_the_choices():
    online = [{"games": ["platinum"], "reach": {"sinnoh": 0}}]
    for n in range(30):
        rng = random.Random(n)
        c = R.pick_admin(rng, online, SPOTS, {}, ["kanto", "johto", "sinnoh"], species=150)
        assert c["region"] == "sinnoh" and c["species"] == 150    # weighted by who is online: only Platinum
        c = R.pick_admin(rng, [], SPOTS, {}, ["kanto", "johto"], species=None)
        assert c["region"] in ("kanto", "johto") and c["species"] in R.POOLS[c["region"]]
        c = R.pick_admin(rng, online, SPOTS, {}, ["sinnoh"], species=384, place="route 210", shiny=True)
        assert c["spot"]["name"] == "Route 210" and c["shiny"] is True
        assert str(c["spot"]["map"]) in R.place_maps(SPOTS["sinnoh"], "Route 210")
    # a place far beyond the online players' badges is still used (the host chose it)
    hard = max(SPOTS["sinnoh"]["spots"], key=lambda s: s[3])
    name = SPOTS["sinnoh"]["names"][str(hard[0])]
    c = R.pick_admin(random.Random(1), online, SPOTS, {}, ["sinnoh"], place=name)
    assert c["spot"]["name"] == name
    assert R.pick_admin(random.Random(1), online, SPOTS, {}, ["kanto"], species=384) is None
    assert R.pick_admin(random.Random(1), online, SPOTS, {}, ["kanto"], place="Route 210") is None


async def test_command_opens_a_raid_through_the_running_server(server, hub, connect_client, capsys):
    c = await raider(connect_client, "Ilias", game="platinum")
    env = {"YL_DATA_DIR": server.config.data_dir}
    argv = ["--region", "sinnoh", "--pokemon", "rayquaza", "--place", "Route 210", "--shiny", "no", "--label", "Fly"]
    rc = await asyncio.to_thread(C.main, argv, env)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert out.strip() == "Raid opened on Fly: Rayquaza on Route 210 (Sinnoh), 15 minutes. 1 player online was notified."
    m = await c.expect("raid_open", fresh=True)
    assert m["raid"]["species"] == 384 and m["raid"]["spot"]["name"] == "Route 210"
    assert "forced" not in m["raid"]                        # server-only
    await c.expect("system", text="RAID! Rayquaza appeared on Route 210 (Sinnoh) - 15 minutes!")
    r = hub.current
    assert r["forced"] is True and r["closes"] - r["opens"] == 15 * 60
    assert hub.last_slot == -1                              # the schedule isn't touched
    with open(hub.path) as fh:
        assert json.load(fh)["current"]["forced"] is True   # persisted like any raid
    assert os.listdir(hub.admin_dir) == []                  # request + result cleaned up


async def test_busy_refuses_then_replace_ends_it(server, hub, connect_client):
    c = await raider(connect_client, "Ilias", game="yellow")
    old = await hub.spawn(region="kanto", species=151, shiny=False)
    await c.expect("raid_open", fresh=True)
    await c.send(type="raid_join", id=old["id"], level=50)
    await c.expect("raid_joined")
    hub.clock.t += 120
    res = await hub.admin_request(req(regions=["johto"], species=249))
    assert res["ok"] is False and res["busy"] is True
    assert "A raid is already running: Mew on Route" in res["message"] and "13 minutes left" in res["message"]
    assert "--replace" in res["message"]
    assert hub.current is old
    c.clear()
    res = await hub.admin_request(req(regions=["johto"], species=249, replace=True, minutes=5, shiny=False))
    assert res["ok"] is True, res
    assert res["message"].startswith("Lugia ") and ", 5 minutes." in res["message"]
    assert "The raid that was running (Mew " in res["message"]
    end = await c.expect("raid_end", id=old["id"])
    assert end["reason"] == "ended"
    await c.expect("system", text="The host ended the raid against Mew.")
    m = await c.expect("raid_open", fresh=True)
    assert m["raid"]["species"] == 249 and m["raid"]["closes"] - m["raid"]["opens"] == 300
    assert hub.history[-1]["id"] == old["id"] and hub.history[-1]["reason"] == "ended"
    assert hub.history[-1]["participants"][c.uid]["state"] == "fled"
    assert not server.clients[c.uid].raiding


async def test_replace_while_the_boss_is_down_keeps_the_rolled_throws(server, hub, connect_client):
    c = await raider(connect_client, "Ilias", game="yellow")
    old = await hub.spawn(region="kanto", species=151, shiny=False)
    await c.send(type="raid_join", id=old["id"], level=50)
    await c.expect("raid_joined")
    hub.clock.t += 60
    await c.send(type="raid_damage", id=old["id"], dealt=2000)
    await c.expect("raid_down", id=old["id"])
    res = await hub.admin_request(req(regions=["kanto"]))
    assert res["ok"] is False and "the boss is down" in res["message"]
    res = await hub.admin_request(req(regions=["kanto"], replace=True))
    assert res["ok"] is True
    assert hub.history[-1]["participants"][c.uid]["state"] == "fighting"     # kept, like after DOWN_GRACE


async def test_replace_never_ends_a_raid_when_the_new_one_cant_open(server, hub):
    old = await hub.spawn(region="kanto", species=151, shiny=False)
    hub.spots = {k: v for k, v in hub.spots.items() if k != "sinnoh"}
    res = await hub.admin_request(req(regions=["sinnoh"], species=384, replace=True))
    assert res["ok"] is False and hub.current is old


async def test_opens_with_nobody_online(server, hub):
    res = await hub.admin_request(req(regions=["kanto"], species=150, shiny=True, minutes=10))
    assert res["ok"] is True
    assert res["message"].startswith("Shiny Mewtwo ") and "(Kanto), 10 minutes." in res["message"]
    assert "Nobody is online right now" in res["message"] and res["online"] == 0
    assert hub.current["shiny"] is True and hub.current["forced"] is True


async def test_tells_who_can_join(server, hub, connect_client):
    await raider(connect_client, "Ilias", games=("yellow",))
    await raider(connect_client, "Alex", games=("yellow", "platinum"))
    res = await hub.admin_request(req(regions=["sinnoh"]))
    assert res["ok"] and "2 players online were notified (1 of them can join" in res["message"]


async def test_old_and_bad_requests_do_nothing(server, hub):
    path = os.path.join(hub.admin_dir, "raid-old1.json")
    with open(path, "w") as fh:
        json.dump(req(created=time.time() - 3600), fh)
    with open(os.path.join(hub.admin_dir, "raid-bad1.json"), "w") as fh:
        fh.write("{not json")
    await hub.check_admin()
    assert hub.current is None
    with open(os.path.join(hub.admin_dir, "raid-old1.result.json")) as fh:
        assert "too old" in json.load(fh)["message"]
    with open(os.path.join(hub.admin_dir, "raid-bad1.result.json")) as fh:
        assert json.load(fh)["ok"] is False
    assert not [n for n in os.listdir(hub.admin_dir) if not n.endswith(".result.json")]
    for bad in (req(regions=["hoenn"]), req(species=25), req(minutes=0), req(shiny="yes"), {"action": "nuke"}):
        res = await hub.admin_request(bad)
        assert res["ok"] is False, bad
    assert hub.current is None


async def test_raids_off_answers(tmp_path):
    from conftest import KEY
    from yellowlink_server.config import Config
    from yellowlink_server.server import YellowLinkServer
    srv = YellowLinkServer(Config(host="127.0.0.1", port=0, key=KEY, data_dir=str(tmp_path / "d"), raids=False))
    res = await srv.raids.admin_request(req())
    assert res["ok"] is False and "turned off" in res["message"]


async def test_forced_raid_doesnt_move_the_schedule(server, hub, connect_client):
    await raider(connect_client, "Ilias")
    hub.every = 7200.0
    hub.slot_start_time = lambda n: n * 7200.0 + 600          # the slot's raid is due 10 minutes in
    slot = 250000
    hub.last_slot = slot - 1
    hub.clock.t = slot * 7200 + 60
    res = await hub.admin_request(req(regions=["kanto"], minutes=15))
    assert res["ok"] and hub.last_slot == slot - 1
    forced = hub.current
    hub.clock.t = slot * 7200 + 600                         # the slot's time comes while the forced raid is open
    await hub.tick()
    assert hub.current is forced
    hub.clock.t = forced["closes"] + 1
    await hub.tick()                                        # the forced raid ends ...
    assert hub.current is None
    await hub.tick()                                        # ... a short breather (not the hour-long gap)
    assert hub.current is None
    hub.clock.t = forced["closes"] + R.FORCED_BREATHER + 2
    await hub.tick()
    assert hub.current is not None and not hub.current.get("forced") and hub.last_slot == slot


# ----------------------------------------------------------------------- the host script (fake docker)
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
[ "$1" = "exec" ] || exit 1
c="$2"; shift 2
case "$c" in
  Old) echo "/usr/local/bin/python: No module named yellowlink_server.raidctl"; exit 1 ;;
  Stopped) echo "Error response from daemon: container 1234 is not running"; exit 1 ;;
esac
shift 2   # python -m
shift     # yellowlink_server.raidctl
cd "$FAKE_SERVER_DIR" && YL_DATA_DIR="$FAKE_DATA_DIR" exec "$FAKE_PYTHON" -m yellowlink_server.raidctl "$@"
"""

needs_bash = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs bash")


def make_opt(root, servers):
    """servers: {folder: .env text or None}"""
    for folder, envtext in servers.items():
        d = root / folder / "server"
        d.mkdir(parents=True)
        (d / "docker-compose.yml").write_text("services: {}\n")
        if envtext is not None:
            (d / ".env").write_text(envtext)


@pytest.fixture
def host(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    data = tmp_path / "data"
    data.mkdir()
    opt = tmp_path / "opt"
    opt.mkdir()

    async def run(*args, servers=None, glob=None, data_dir=None):
        env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", FAKE_LOG=str(log),
                   FAKE_SERVER_DIR=SERVER_DIR, FAKE_DATA_DIR=str(data_dir or data), FAKE_PYTHON=sys.executable,
                   YL_SERVERS_GLOB=glob or str(opt / "yellowlink*"))
        env.pop("YL_RAIDS", None)
        proc = await asyncio.create_subprocess_exec("bash", SCRIPT, *args, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        return proc.returncode, out.decode()

    def calls():
        return log.read_text().splitlines() if log.exists() else []

    run.opt, run.calls = opt, calls
    return run


@needs_bash
async def test_script_help_and_server_choice(host):
    make_opt(host.opt, {"yellowlink": "YL_KEY=a\n",
                        "yellowlink2": 'YL_KEY=b\nYL_NAME="Fly"\nYL_HOST_PORT=8766\nYL_SERVER_NAME=Second Group\n',
                        "yellowlink3": "YL_NAME=Gone\nYL_MOVED_TO=ws://1.2.3.4:8765\n",
                        "yellowlink-notes": None})
    (host.opt / "yellowlink-notes" / "server" / "docker-compose.yml").unlink()   # not a server folder
    rc, out = await host("--help")
    assert rc == 0 and "sudo yl-raid --list" in out and "--replace" in out and host.calls() == []
    rc, out = await host("--servers")
    assert rc == 0 and "yellowlink  (" in out and "Fly  (" in out and "port 8766" in out and "Gone" not in out
    # two servers, no --server, nobody at the keyboard: say which ones exist, do nothing
    rc, out = await host("--region", "kanto")
    assert rc == 2 and "--server" in out and "Fly" in out and host.calls() == []
    rc, out = await host("--server", "nope")
    assert rc == 2 and "no server called 'nope'" in out
    # --list doesn't need a choice; the name matches without caring about case, also the folder or player name
    rc, out = await host("--list", "--region", "johto")
    assert rc == 0 and "JOHTO" in out
    assert host.calls()[-1] == "exec yellowlink python -m yellowlink_server.raidctl --label yellowlink --list --region johto"
    for name in ("fly", "yellowlink2", "second group"):
        rc, out = await host("--server", name, "--list")
        assert rc == 0 and host.calls()[-1].startswith("exec Fly python -m yellowlink_server.raidctl --label Fly")
    rc, out = await host("--server=all", "--list", "--region", "kanto")
    assert rc == 0 and out.count("KANTO") == 2
    assert [c.split()[1] for c in host.calls()[-2:]] == ["yellowlink", "Fly"]
    # its own options aren't passed on; the server's checks still answer
    rc, out = await host("--server", "Fly", "--pokemon", "pikachu")
    assert rc == 2 and "isn't a raid Pokémon" in out and "Nothing was changed on Fly." in out
    rc, out = await host("--server", "Fly", "--label", "x")
    assert rc == 2 and "Unknown option" in out


@needs_bash
async def test_script_explains_old_and_stopped_servers(host):
    make_opt(host.opt, {"yellowlink": "YL_NAME=Old\n", "yellowlink2": "YL_NAME=Stopped\n"})
    rc, out = await host("--server", "Old")
    assert rc == 1 and "older YellowLink" in out and "sudo yl-update" in out
    rc, out = await host("--server", "stopped")
    assert rc == 1 and "isn't running" in out and "docker compose up -d" in out
    rc, out = await host("--server", "all")
    assert rc == 1 and "older YellowLink" in out and "isn't running" in out


@needs_bash
async def test_script_end_to_end_with_one_server(host, server, hub, connect_client):
    """One server (the default container name): `yl-raid --region ...` opens the raid on the running server."""
    make_opt(host.opt, {"yellowlink": "YL_KEY=a\n"})
    c = await raider(connect_client, "Ilias", game="platinum")
    rc, out = await host("--region", "sinnoh", "--pokemon", "rayquaza", "--place", "Route 210", "--shiny", "no",
                         data_dir=server.config.data_dir)
    assert rc == 0, out
    assert out.strip() == ("Raid opened on yellowlink: Rayquaza on Route 210 (Sinnoh), 15 minutes. "
                           "1 player online was notified.")
    await c.expect("raid_open", fresh=True)
    rc, out = await host("--pokemon", "mew", data_dir=server.config.data_dir)
    assert rc == 1 and "A raid is already running: Rayquaza on Route 210 (Sinnoh)" in out
    rc, out = await host("--pokemon", "mew", "--shiny", "no", "--replace", data_dir=server.config.data_dir)
    assert rc == 0 and out.startswith("Raid opened on yellowlink: Mew ") and "was ended first" in out
    assert hub.current["species"] == 151 and hub.history[-1]["species"] == 384
