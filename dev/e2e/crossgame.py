"""Cross-game PC database test: Yellow and Crystal share one player's boxes (real ROMs, real PC menus).

  1. Yellow: deposit PIDGEY + RATTATA, commit.
  2. Crystal: the PC shows them (Time Capsule conversion); withdraw RATTATA into the Crystal party,
     deposit SENTRET (a Johto mon), commit.
  3. Yellow again: PIDGEY plus a ?SENTRET stand-in; RATTATA is gone (it lives in Crystal now).
Needs dev/states/{yellow.gb,pokecenter_pc.state,crystal.gbc,crystal_party.state}.
Run: python dev/e2e/crossgame.py
"""
import os, sys, tempfile, pathlib
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_REPO, "dev", "crystal"))
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
import pc_actions as pa
import play as cp
from yellowlink.config import Config
from yellowlink import app as appmod, pcdb, crystal, text

tmp = pathlib.Path(tempfile.mkdtemp())
STORE = tmp / "pc.json"
FAILS = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


class Cfg(Config):
    def __init__(self, d, rom):
        d.mkdir(parents=True, exist_ok=True); self.d = d
        super().__init__(d / "config.json")
        self.update(name="Ash", server_url="ws://127.0.0.1:1", room_key="x", rom_path=rom, seen_help=True,
                    games={"yellow" if rom.endswith(".gb") else "crystal": rom},
                    current_game="yellow" if rom.endswith(".gb") else "crystal")
    save_path = property(lambda self: self.d / "game.sav")
    resume_path = property(lambda self: self.d / "resume.state")
    @property
    def backup_dir(self):
        p = self.d / "bk"; p.mkdir(exist_ok=True); return p


def make_app(rom, state, sub):
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(Cfg(tmp / sub, os.path.join(STATES, rom)))
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    a.pcsync = pcdb.PCSync(STORE)
    for _ in range(60):
        a.step()
    return a


def db(a):
    return [e["nick"] for b in a.pcsync.working["boxes"] for e in b]


def crystal_box(a):
    return [text.decode(r[crystal.BOX_MON_SIZE + crystal.NAME_SIZE:crystal.REC_SIZE])
            for r in a.pc_codec.read_boxes()[a.pc_codec.current_box()]]


# 1. Yellow
Y = make_app("yellow.gb", "pokecenter_pc.state", "y1")
check("Yellow runs with multiplayer on", Y.full and not Y.gen2)
pa.open_pc(Y.pyboy)
check("deposit PIDGEY", pa.deposit(Y.pyboy, 1))
check("deposit RATTATA", pa.deposit(Y.pyboy, 1))
pa.close_pc(Y.pyboy)
check("autosave commits", Y.autosave(force=True) and not Y.pcsync.pending)
check("database: PIDGEY, RATTATA", db(Y) == ["PIDGEY", "RATTATA"])
Y.shutdown()

# 2. Crystal
C = make_app("crystal.gbc", "crystal_party.state", "c1")
print("crystal app:", C.game_id, C.full, C.gen2, C.pc_codec, C.game_info)
check("Crystal runs with multiplayer on", C.full and C.gen2 and C.pc_codec is not None)
print("crystal party:", [m.nick_str() for m in C.game.party()])
pb = C.pyboy
cp.pc_open(pb)
check("PC open hook started a session", C.pcsync.session_open)
check("Crystal PC shows the Yellow mons", crystal_box(C) == ["PIDGEY", "RATTATA"])
cp.pc_withdraw(pb, "RATTATA")
names = [m.nick_str() for m in C.game.party()]
check("RATTATA withdrawn into the Crystal party", "RATTATA" in names)
rat = next(m for m in C.game.party() if m.nick_str() == "RATTATA")
check("game computed RATTATA's stats (max HP > 0, level kept)", rat.max_hp > 0 and rat.level == 4)
cp.pc_deposit(pb, "SENTRET")
cp.pc_close(pb)
check("close hook ended the session", not C.pcsync.session_open)
check("database: PIDGEY, SENTRET", sorted(db(C)) == ["PIDGEY", "SENTRET"])
pid = next(e for b in C.pcsync.working["boxes"] for e in b if e["nick"] == "PIDGEY")
check("PIDGEY kept its original Yellow data", pid["gen1"] and pid["origin"] == "yellow")
for _ in range(30):
    C.step()
check("autosave commits", C.autosave(force=True) and not C.pcsync.pending)
C.shutdown()
check("Crystal clock saved next to the save", (tmp / "c1" / "game.rtc").exists())

# 3. Yellow again
Y2 = make_app("yellow.gb", "pokecenter_pc.state", "y2")
pa.open_pc(Y2.pyboy)
box = [n for _, n in pa.box(Y2.pyboy)]
print("yellow box:", box)
check("Yellow PC: PIDGEY + ?SENTRET", box == ["PIDGEY", "?SENTRET"])
pa.close_pc(Y2.pyboy)
check("database unchanged by a look-only visit", sorted(db(Y2)) == ["PIDGEY", "SENTRET"])
Y2.shutdown()

print("\nfiles in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
