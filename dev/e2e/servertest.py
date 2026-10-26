"""Server switching e2e: one player, two local servers (two groups of friends), real Yellow ROM.

  1. Ilias plays on "Group One" (sees Alex), opens Multiplayer > Switch server > Add a server (joins right away)
  2. restart on "Group Two": own folder, fresh save, new player id, sees Bea and not Alex
  3. Switch back from the server list: Group One's game continues where it was, same player id, sees Alex
Run: python dev/e2e/servertest.py   (needs dev/states/yellow.gb and room.state)
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, json
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config, app_dir
from yellowlink import app as appmod, library, setup_dialog

ROM = os.path.join(STATES, "yellow.gb")
srvs = []
for port, key, name in ((8781, "one", "Group One"), (8782, "two", "Group Two")):
    srvs.append(subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(port)],
                                 cwd=os.path.join(_REPO, "server"),
                                 env=dict(os.environ, YL_KEY=key, YL_SERVER_NAME=name, YL_DATA_DIR=str(tmp / f"data{port}"),
                                          PYTHONPATH=os.path.join(_REPO, "server")),
                                 stdout=open(tmp / f"srv{port}.log", "w"), stderr=subprocess.STDOUT))
    atexit.register(srvs[-1].terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


class FriendCfg(Config):
    """A friend on the same machine: own folder instead of the shared app folder."""
    @property
    def data_dir(self):
        (self.path.parent / "saves").mkdir(parents=True, exist_ok=True)
        return self.path.parent


def friend(name, port, key):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    c = FriendCfg(d / "config.json")
    c.update(name=name, server_url=f"ws://127.0.0.1:{port}", room_key=key, rom_path=ROM, seen_help=True,
             games={"yellow": ROM}, current_game="yellow")
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "room.state")
    a = appmod.App(c)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


def run(apps, n, cond=None):
    for _ in range(n):
        for a in apps:
            a.step()
        time.sleep(0.002)
        if cond and cond():
            return True
    return False


def click(app, label):
    app.render()
    for rect, cb, lab in reversed(app.click_targets):
        if lab == label:
            cb()
            return True
    return False


def names(app):
    return sorted(e["name"] for e in app.roster if e.get("uid") != app.cfg["uid"])


def restart(app):
    """What _main does after app.run() returns for a server switch."""
    app.shutdown()
    cfg = app.cfg
    cfg.switch_server(app.switch_to)
    lib = library.Library(cfg, app_dir())
    if lib.current:
        lib.set_current(lib.current)
    return appmod.App(cfg)


alex = friend("Alex", 8781, "one")
bea = friend("Bea", 8782, "two")

cfg = Config()
cfg.update(name="Ilias", server_url="ws://127.0.0.1:8781", room_key="one", rom_path=ROM, seen_help=True,
           games={"yellow": ROM}, current_game="yellow")
cfg.save()
uid1 = cfg["uid"]
os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "room.state")
A = appmod.App(cfg)
os.environ.pop("YL_DEV_LOAD_STATE", None)
run([A, alex, bea], 300, lambda: A.net.status == "online" and names(A) == ["Alex"])
check("Group One: Ilias sees Alex", names(A) == ["Alex"], names(A))
check("server name shown", A.server_name == "Group One")
A.press("down", True); run([A, alex, bea], 30); A.press("down", False); run([A, alex, bea], 60)
pos1 = A.game.position()
save1 = A.cfg.save_path
check("first server uses the original folders", save1 == app_dir() / "saves" / "yellow.sav", save1)

# 1. Multiplayer > Switch server > Add a server (the window is faked: it fills the form and presses Add)
A.toggle_panel("multiplayer")
A.render(); pygame.image.save(A.screen, str(tmp / "0_multiplayer.png"))
check("Switch server button", click(A, "Switch server") and A.panel == "servers")
A.render(); pygame.image.save(A.screen, str(tmp / "1_multiplayer_to_servers.png"))
setup_dialog.server_form = lambda entry, chk: (chk("", "127.0.0.1:8781", "x") and None) or None  # duplicate
check("Add a server button", click(A, "Add a server..."))
check("duplicate address refused", len(A.cfg.servers) == 1)
setup_dialog.server_form = lambda entry, chk: None if chk("", "127.0.0.1:8782", "two") else {"join": True}
click(A, "Add a server...")
check("added and joining", len(A.cfg.servers) == 2 and A.quit_requested and A.switch_to)
run([A, alex, bea], 5)

# 2. restart on the second server
B = restart(A)
run([B, alex, bea], 300, lambda: B.net.status == "online" and names(B) == ["Bea"])
check("Group Two: sees Bea, not Alex", names(B) == ["Bea"], names(B))
check("Group Two: server name", B.server_name == "Group Two")
check("own player id on Group Two", B.cfg["uid"] != uid1)
uid2 = B.cfg["uid"]
check("own folder + fresh save", "servers" in str(B.cfg.save_path) and not B.had_save, (B.cfg.save_path, B.had_save))
check("own PC database file", "servers" in str(B.pcsync.path) and B.pcsync.path != A.pcsync.path, (A.pcsync.path, B.pcsync.path))
check("unnamed server shows its own name in the list", Config.server_label(B.cfg.active_server) == "Group Two")
B.toggle_panel("servers")
B.render(); pygame.image.save(B.screen, str(tmp / "2_servers_two.png"))
run([B, alex, bea], 30)
check("Group One's server never saw the new id",
      B.cfg["uid"] not in (tmp / "data8781").joinpath("players.json").read_text()
      if (tmp / "data8781" / "players.json").exists() else True)

# 2b. edit the current server: a wrong password reconnects and is refused, fixing it reconnects again
sid2 = B.cfg.server_id
setup_dialog.server_form = lambda entry, chk: None if chk("", entry["url"], "wrong") else {"join": False}
check("Edit button", click(B, "Edit") and B.switch_to == sid2)
B.switch_to = sid2
B = restart(B)
run([B, alex, bea], 300, lambda: B.net.status == "rejected")
check("wrong password -> rejected", B.net.status == "rejected" and B.net.status_detail == "wrong room password")
B.toggle_panel("servers")
B.render(); pygame.image.save(B.screen, str(tmp / "3_rejected.png"))
check("still the same player and folder", B.cfg["uid"] == uid2 and B.cfg.server_id == sid2)
setup_dialog.server_form = lambda entry, chk: None if chk("Weekend crew", entry["url"], "two") else {"join": False}
click(B, "Edit")
B = restart(B)
run([B, alex, bea], 300, lambda: B.net.status == "online" and names(B) == ["Bea"])
check("fixed password -> back online with Bea", names(B) == ["Bea"])
check("own name for the server wins", Config.server_label(B.cfg.active_server) == "Weekend crew")
B.toggle_panel("servers")

# 3. back to Group One from the list
check("Switch button", click(B, "Switch") and B.switch_to == "main")
C = restart(B)
run([C, alex, bea], 300, lambda: C.net.status == "online" and names(C) == ["Alex"])
check("back on Group One with Alex", names(C) == ["Alex"], names(C))
check("same player id as before", C.cfg["uid"] == uid1)
check("same save file", C.cfg.save_path == save1)
run([C, alex, bea], 60)
check("game continues where it was", C.game.position() == pos1, (C.game.position(), pos1))
saved = json.loads((app_dir() / "config.json").read_text())
check("config remembers both servers", len(saved["servers"]) == 2 and saved["server_id"] == "main")
check("no password stored in the wrong server", {s["key"] for s in saved["servers"]} == {"one", "two"})
for a in (C, alex, bea):
    a.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
