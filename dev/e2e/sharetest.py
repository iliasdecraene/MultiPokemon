"""One save on two servers (SPEC.md section 8), end to end with the real Yellow ROM and real Bill's PC menus.

  1. Ilias plays on "Main" (Alex is there): deposits PIDGEY, autosave -> Main has it
  2. Ilias switches to "Fly" (shares Main's save, Bea is there): same game resumes, same PC; trading waits until
     Fly has his PC; Fly gets his PC under his own Fly player id
  3. Ilias gifts PIDGEY to Bea on Fly -> gone from the shared PC
  4. back on Main: the same game, PIDGEY not there; Main's copy follows without a conflict; Alex still sees him
Run: python dev/e2e/sharetest.py   (needs dev/states/yellow.gb + pokecenter_pc.state + room.state)
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, json
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config, app_dir
from yellowlink import app as appmod, library
import pc_actions as pa

ROM = os.path.join(STATES, "yellow.gb")
MAIN, FLY = 8789, 8790
FAILS = []


def start(port, key, name, data):
    env = dict(os.environ, YL_RAIDS="off", YL_KEY=key, YL_SERVER_NAME=name, YL_DATA_DIR=str(data),
               PYTHONPATH=os.path.join(_REPO, "server"))
    p = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(port)],
                         cwd=os.path.join(_REPO, "server"), env=env,
                         stdout=open(tmp / f"srv{port}.log", "a"), stderr=subprocess.STDOUT)
    atexit.register(p.terminate)


start(MAIN, "one", "Main", tmp / "main")
start(FLY, "two", "Fly", tmp / "fly")
time.sleep(1.5)


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


class FriendCfg(Config):
    @property
    def data_dir(self):
        (self.path.parent / "saves").mkdir(parents=True, exist_ok=True)
        return self.path.parent


def friend(name, port, key):
    d = tmp / name
    d.mkdir()
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


class Driven:
    def __init__(self, me, others):
        self.pb, self.me, self.others, self.n = me.pyboy, me, others, 0

    def tick(self, *a, **k):
        r = self.pb.tick(*a, **k)
        self.n += 1
        if self.n % 3 == 0:
            self.me.handle_net()
            for o in self.others:
                o.step()
        return r

    def __getattr__(self, name):
        return getattr(self.pb, name)


def click(app, label):
    app.render()
    for rect, cb, lab in reversed(app.click_targets):
        if lab == label:
            cb()
            return True
    return False


def names(app):
    return sorted(e["name"] for e in app.roster if e.get("uid") != app.cfg["uid"])


def store_nicks(app):
    return sorted(e["nick"] for b in app.pcsync.committed["boxes"] for e in b)


def server_nicks(port_dir, uid):
    p = tmp / port_dir / "pc" / f"{uid}.json"
    if not p.exists():
        return None
    st = json.loads(p.read_text())
    return sorted(e["nick"] for b in st["boxes"] for e in b)


def restart(app):
    app.shutdown()
    cfg = app.cfg
    cfg.switch_server(app.switch_to)
    lib = library.Library(cfg, app_dir())
    lib.set_current(lib.current)
    return appmod.App(cfg)


alex = friend("Alex", MAIN, "one")
bea = friend("Bea", FLY, "two")

cfg = Config()
cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{MAIN}", room_key="one", rom_path=ROM, seen_help=True,
           games={"yellow": ROM}, current_game="yellow")
fly = cfg.add_server("Fly", f"ws://127.0.0.1:{FLY}", "two")
cfg.share_save(fly["id"], "main")
cfg.save()
main_uid = cfg["uid"]

# 1. on Main: deposit PIDGEY
os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "pokecenter_pc.state")
A = appmod.App(cfg)
os.environ.pop("YL_DEV_LOAD_STATE", None)
run([A, alex, bea], 300, lambda: A.net.status == "online" and names(A) == ["Alex"])
d = Driven(A, [alex, bea])
pa.open_pc(d)
pa.deposit(d, 1)
pa.close_pc(d)
run([A, alex, bea], 30)
check("autosave commits the deposit", A.autosave(force=True))
run([A, alex, bea], 200, lambda: server_nicks("main", main_uid) == ["PIDGEY"])
check("Main has PIDGEY", server_nicks("main", main_uid) == ["PIDGEY"], server_nicks("main", main_uid))
pos = A.game.position()
party = [m.nick_str() for m in A.game.party()]

# 2. switch to Fly (shares Main's save)
A.switch_server(fly["id"])
B = restart(A)
check("same save folder and PC store", B.cfg.data_dir == app_dir() and B.pcsync.path == A.pcsync.path)
check("own player id on Fly", B.cfg["uid"] == fly["uid"] != main_uid)
check("trading waits until Fly has the PC", B._trade_blocked({"online": True}) is not None)
run([B, alex, bea], 400, lambda: B.net.status == "online" and names(B) == ["Bea"] and B.pc_ready)
check("on Fly with Bea, PC synced", names(B) == ["Bea"] and B.pc_ready, (names(B), B.pc_ready))
check("same game continues", B.game.position() == pos and [m.nick_str() for m in B.game.party()] == party)
check("same PC (PIDGEY)", store_nicks(B) == ["PIDGEY"], store_nicks(B))
run([B, alex, bea], 100, lambda: server_nicks("fly", fly["uid"]) == ["PIDGEY"])
check("Fly got the PC under Ilias's Fly id", server_nicks("fly", fly["uid"]) == ["PIDGEY"])

# 3. gift PIDGEY to Bea on Fly
e_bea = next(r for r in B.roster if r["uid"] == bea.cfg["uid"])
B.action_gift(e_bea)
run([B, alex, bea], 200, lambda: bea.mode == "prompt")
time.sleep(0.6)
click(bea, "Accept")
run([B, alex, bea], 200, lambda: B.mode == "trade" and bea.mode == "trade")
pid = next(e for _, e in B.trade_entries() if e["nick"] == "PIDGEY")
B.offer_entry(pid)
run([B, alex, bea], 100, lambda: bea.trade and bea.trade.their_entries)
check("Give/Accept", click(B, "Give it") and click(bea, "Accept gift"))
run([B, alex, bea], 300, lambda: B.trade is None and "PIDGEY" in store_nicks(bea))
check("gift done: Bea has PIDGEY, Ilias's shared PC doesn't", "PIDGEY" in store_nicks(bea) and store_nicks(B) == [],
      (store_nicks(bea), store_nicks(B)))
run([B, alex, bea], 100)
B.autosave(force=True)
run([B, alex, bea], 100)

# 4. back to Main
B.switch_server("main")
B.shutdown(); B.shutdown = lambda: None
C = restart(B)
run([C, alex, bea], 400, lambda: C.net.status == "online" and names(C) == ["Alex"] and server_nicks("main", main_uid) == [])
check("back on Main with Alex, same player", names(C) == ["Alex"] and C.cfg["uid"] == main_uid)
check("PIDGEY is gone here too", store_nicks(C) == [])
check("Main's copy followed without a conflict", server_nicks("main", main_uid) == []
      and "conflict" not in (tmp / f"srv{MAIN}.log").read_text().lower(), server_nicks("main", main_uid))
check("Alex sees Ilias", "Ilias" in names(alex))
# 5. the save choice in the add/edit server window
from yellowlink import setup_dialog
seen = {}
def fake_form(entry, chk, saves, current, ret=None):
    seen["saves"], seen["current"] = saves, current
    return None if chk(entry.get("label", "") if entry else "Third", entry["url"] if entry else "ws://127.0.0.1:8799",
                       entry["key"] if entry else "x") else ret
setup_dialog.server_form = lambda e, c, s, cur: fake_form(e, c, s, cur, {"join": False, "save": "main"})
C.server_dialog()
third = next(x for x in C.cfg.servers if x.get("label") == "Third")
check("new server set to share the main save", third.get("save_dir") == "main")
check("the window offers 'own save' + the servers that own a save",
      seen["saves"][0][0] is None and [v for v, _ in seen["saves"][1:]] == ["main"], seen["saves"])
setup_dialog.server_form = lambda e, c, s, cur: fake_form(e, c, s, cur, {"join": False, "save": None})
C.server_dialog(fly["id"])
check("Fly back to its own save (not the server we're on: applied at once)", "save_dir" not in C.cfg.server(fly["id"]))
setup_dialog.server_form = lambda e, c, s, cur: fake_form(e, c, s, cur, {"join": False, "save": third["id"]})
C.server_dialog("main")
check("changing the save of the server we're on waits for a restart",
      getattr(C, "pending_share", None) == ("main", third["id"]) and C.quit_requested
      and "save_dir" not in C.cfg.server("main"))
for a in (C, alex, bea):
    a.shutdown()
print("\nfiles in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
