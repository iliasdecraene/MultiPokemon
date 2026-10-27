"""Merging two servers, end to end (real Yellow ROM): SPEC.md section 8.

  1. Bea plays on "Fly" (her PC backup is uploaded there); Alex plays on "Main"
  2. Fly is stopped, merged into Main (yellowlink_server.merge), and restarted as a forwarder (YL_MOVED_TO)
  3. Bea starts YellowLink again: Fly tells her game it moved, the game takes over Main's address + password,
     restarts, and she's on Main with Alex - same save folder, same player id, same PC backup, no conflicts
Run: python dev/e2e/movetest.py   (needs dev/states/yellow.gb and room.state)
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, json
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "server"))
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config, app_dir
from yellowlink import app as appmod, library
from yellowlink_server.merge import merge

ROM = os.path.join(STATES, "yellow.gb")
MAIN, FLY = 8787, 8788
procs = {}


def start(port, key, name, data, **extra):
    env = dict(os.environ, YL_RAIDS="off", YL_KEY=key, YL_SERVER_NAME=name, YL_DATA_DIR=str(data),
               PYTHONPATH=os.path.join(_REPO, "server"), **extra)
    p = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(port)],
                         cwd=os.path.join(_REPO, "server"), env=env,
                         stdout=open(tmp / f"srv{port}.log", "a"), stderr=subprocess.STDOUT)
    procs[port] = p
    atexit.register(p.terminate)
    time.sleep(1.5)


def stop(port):
    p = procs.pop(port)
    p.terminate()
    p.wait(10)


FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


class FriendCfg(Config):
    @property
    def data_dir(self):
        (self.path.parent / "saves").mkdir(parents=True, exist_ok=True)
        return self.path.parent


def run(apps, n, cond=None):
    for _ in range(n):
        for a in apps:
            a.step()
        time.sleep(0.002)
        if cond and cond():
            return True
    return False


def names(app):
    return sorted(e["name"] for e in app.roster if e.get("uid") != app.cfg["uid"])


def boot(cfg, state=True):
    if state:
        os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "room.state")
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


start(MAIN, "one", "Main", tmp / "main")
start(FLY, "two", "Fly", tmp / "fly")

d = tmp / "Alex"
d.mkdir()
acfg = FriendCfg(d / "config.json")
acfg.update(name="Alex", server_url=f"ws://127.0.0.1:{MAIN}", room_key="one", rom_path=ROM, seen_help=True,
            games={"yellow": ROM}, current_game="yellow")
alex = boot(acfg)

# 1. Bea on Fly (her first and only server)
cfg = Config()
cfg.update(name="Bea", server_url=f"ws://127.0.0.1:{FLY}", room_key="two", rom_path=ROM, seen_help=True,
           games={"yellow": ROM}, current_game="yellow")
cfg.save()
uid, folder = cfg["uid"], cfg.data_dir
bea = boot(cfg)
run([bea, alex], 300, lambda: bea.net.status == "online" and getattr(bea, "server_name", "") == "Fly" and bea.pcsync.server_rev > 0)
check("Bea is on Fly", getattr(bea, "server_name", "") == "Fly" and names(bea) == [], names(bea))
check("her PC backup is on Fly", (tmp / "fly" / "pc" / f"{uid}.json").exists())
srv_rev = bea.pcsync.server_rev
bea.shutdown()

# 2. merge Fly into Main, Fly becomes a forwarder
stop(FLY)
stop(MAIN)
r = merge(str(tmp / "fly"), str(tmp / "main"))
check("merge copied Bea + her PC backup", any("Bea" in p for p in r["players_added"]) and r["pc_added"] == 1, r)
start(MAIN, "one", "Main", tmp / "main")
start(FLY, "two", "Fly", tmp / "fly-moved", YL_MOVED_TO=f"ws://127.0.0.1:{MAIN}", YL_MOVED_KEY="one")
run([alex], 300, lambda: alex.net.status == "online")

# 3. Bea starts again: moved -> follows
bea = boot(cfg, state=False)
run([bea, alex], 300, lambda: bea.quit_requested)
check("Fly told Bea's game it moved", bea.quit_requested and bea.switch_to == cfg.server_id)
bea.shutdown()
cfg.switch_server(bea.switch_to)                    # what _main does
lib = library.Library(cfg, app_dir())
lib.set_current(lib.current)
bea = boot(cfg, state=False)
run([bea, alex], 400, lambda: bea.net.status == "online" and names(bea) == ["Alex"])
check("Bea is on Main with Alex", getattr(bea, "server_name", "") == "Main" and names(bea) == ["Alex"], (getattr(bea, "server_name", ""), names(bea)))
check("Alex sees Bea", "Bea" in names(alex), names(alex))
check("same player id and save folder", cfg["uid"] == uid and cfg.data_dir == folder)
saved = json.loads((app_dir() / "config.json").read_text())
check("config now points to Main", saved["server_url"] == f"ws://127.0.0.1:{MAIN}" and saved["room_key"] == "one"
      and len(saved["servers"]) == 1)
run([bea, alex], 200)
on_main = json.loads((tmp / "main" / "pc" / f"{uid}.json").read_text()).get("srv_rev")
check("PC backup continues on Main without a conflict", bea.pcsync.server_rev >= srv_rev
      and on_main == bea.pcsync.server_rev and "conflict" not in (tmp / f"srv{MAIN}.log").read_text().lower(),
      (srv_rev, bea.pcsync.server_rev, on_main))
for a in (bea, alex):
    a.shutdown()
print("\nfiles in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
