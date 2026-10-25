"""PC database end-to-end test on the real Yellow ROM (needs dev/states/pokecenter_pc.state).

Drives Bill's PC with real button presses inside a running App (hooks + PCSync + local server):
  1. deposit -> close -> pending (not committed) -> autosave commits + server pc_saved
  2. a Crystal-only mon in the database shows up as ?CHIKORITA; withdrawing or releasing it is undone
  3. withdraw a real mon -> commit
  4. restart from the resume snapshot -> game PC still matches the database
Run: python dev/e2e/pctest.py
"""
import os, sys, time, subprocess, tempfile, pathlib, glob
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROM = os.environ.get("YL_ROM", os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.dirname(__file__))
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
import pc_actions as pa
from yellowlink.config import Config
from yellowlink import app as appmod, pcdb

tmp = pathlib.Path(tempfile.mkdtemp())
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", "8798"], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
import atexit; atexit.register(srv.terminate)
time.sleep(1.5)


class Cfg(Config):
    def __init__(self, d, name):
        d.mkdir(parents=True, exist_ok=True); self.d = d
        super().__init__(d / "config.json")
        self.update(name=name, server_url="ws://127.0.0.1:8798", room_key="test", rom_path=ROM, seen_help=True)
    save_path = property(lambda self: self.d / "yellow.sav")
    resume_path = property(lambda self: self.d / "resume.state")
    @property
    def backup_dir(self):
        p = self.d / "bk"; p.mkdir(exist_ok=True); return p


FAILS = []
def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


def make_app(load_state):
    if load_state:
        os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "pokecenter_pc.state")
    else:
        os.environ.pop("YL_DEV_LOAD_STATE", None)
    a = appmod.App(Cfg(tmp / "a", "Ash"))
    os.environ.pop("YL_DEV_LOAD_STATE", None)      # let autosave run
    a.pcsync = pcdb.PCSync(tmp / "a" / "pc.json")
    a.pcsync_path = tmp / "a" / "pc.json"
    return a


def pump(a, n=60, cond=None):
    for _ in range(n):
        a.net_poll() if hasattr(a, "net_poll") else None
        a.step(); time.sleep(0.002)
        if cond and cond():
            return True
    return False


def db_names(a):
    return [e["nick"] for e in a.pcsync.working["boxes"][0]]


A = make_app(True)
pb = A.pyboy
pump(A, 120)
saved = []
orig_saved = A.on_pc_saved
A.on_pc_saved = lambda m: (saved.append(m.get("rev")), orig_saved(m))
# re-bind dispatch if the app looks handlers up by name at connect time
print("party:", pa.party(pb))

# 1. deposit
pa.open_pc(pb)
check("PC open hook started a session", A.pcsync.session_open)
check("deposit PIDGEY", pa.deposit(pb, 1))
pa.close_pc(pb)
check("close hook ended the session", not A.pcsync.session_open)
check("database has PIDGEY (pending)", db_names(A) == ["PIDGEY"] and A.pcsync.pending)
check("not committed yet", sum(len(b) for b in A.pcsync.committed["boxes"]) == 0)
check("autosave ok", A.autosave(force=True))
check("committed", not A.pcsync.pending and A.pcsync.committed["rev"] == 1)
pump(A, 200, lambda: saved)
check("server stored it (pc_saved)", saved[-1:] == [1])
files = glob.glob(str(tmp / "data" / "**" / "*.json"), recursive=True)
pcfiles = [f for f in files if "/pc/" in f]
check("server pc file exists", bool(pcfiles))

# 2. stand-in from another region
A.pcsync.working["boxes"][0].append({"id": "johto1", "origin": "crystal", "dex": 152, "level": 12,
                                     "nick": "CHIKORITA", "ot": "ASH", "otid": 1, "gen1": None, "gen2": "x"})
A.pcsync.pending = True
A.autosave(force=True)
pa.open_pc(pb)
box = pa.box(pb)
print("box view:", box)
check("game box shows PIDGEY + ?CHIKORITA", [n for _, n in box] == ["PIDGEY", "?CHIKORITA"])
n_party = len(pa.party(pb))
pa.withdraw(pb, 1)
check("game let the stand-in into the party (expected)", len(pa.party(pb)) == n_party + 1)
pa.close_pc(pb)
check("stand-in removed from party again", len(pa.party(pb)) == n_party and
      all(n != "?CHIKORITA" for _, n in pa.party(pb)))
check("stand-in back in the database", "CHIKORITA" in db_names(A) and
      next(e for e in A.pcsync.working["boxes"][0] if e["id"] == "johto1")["gen2"] == "x")
pa.open_pc(pb)
check("box view restored", [n for _, n in pa.box(pb)] == ["PIDGEY", "?CHIKORITA"])
pa.release(pb, 1)
pa.close_pc(pb)
check("released stand-in survives in the database", "CHIKORITA" in db_names(A))

# 3. withdraw the real mon
pa.open_pc(pb)
check("withdraw PIDGEY", pa.withdraw(pb, 0))
pa.close_pc(pb)
check("database now only CHIKORITA", db_names(A) == ["CHIKORITA"])
check("autosave ok", A.autosave(force=True))
rev = A.pcsync.committed["rev"]
party_before = pa.party(pb)
A.shutdown()

# 4. restart from the resume snapshot
B = make_app(False)
pump(B, 120)
check("store reloaded from disk", db_names(B) == ["CHIKORITA"] and B.pcsync.committed["rev"] == rev)
check("party restored from snapshot", pa.party(B.pyboy) == party_before)
pa.open_pc(B.pyboy)
check("game box after restart", [n for _, n in pa.box(B.pyboy)] == ["?CHIKORITA"])
pa.close_pc(B.pyboy)
check("no changes after look-only visit", db_names(B) == ["CHIKORITA"])
B.shutdown()
srv.terminate()
print("\nlogs in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
