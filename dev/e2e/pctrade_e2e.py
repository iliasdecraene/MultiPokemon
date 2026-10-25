"""PC trades + gifts across games (real ROMs): a Yellow player and a Crystal player, real Bill's PC menus.

  1. Ilias (Yellow) deposits PIDGEY + RATTATA; Alex (Crystal) deposits SENTRET; both saved to the server
  2. trade from the trade window: PIDGEY <-> SENTRET (the server swaps both stored PCs)
  3. Ilias's Yellow PC: ?SENTRET stand-in, RATTATA, no PIDGEY (never comes back)
  4. Alex's Crystal PC: PIDGEY (converted) - withdrawn for real into his party
  5. gift: Ilias gives RATTATA, Alex only accepts
Run: python dev/e2e/pctrade_e2e.py
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO, "dev", "crystal"))
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config
from yellowlink import app as appmod, crystal, text
import pc_actions as pa
import play

PORT = 8791
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def make(name, rom, gid, state):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = Config(d / "config.json")
    path = os.path.join(STATES, rom)
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=path, seen_help=True,
               games={gid: path}, current_game=gid)
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)       # allow autosave (commits the PC store)
    return a


A = make("Ilias", "yellow.gb", "yellow", "pokecenter_pc.state")
B = make("Alex", "crystal.gbc", "crystal", "crystal_party.state")


def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.002)
        if cond and cond():
            return True
    return False


class Driven:
    def __init__(self, me, other):
        self.pb, self.me, self.other, self.n = me.pyboy, me, other, 0

    def tick(self, *a, **k):
        r = self.pb.tick(*a, **k)
        self.n += 1
        if self.n % 3 == 0:
            self.me.handle_net()
            self.other.step()
        return r

    def __getattr__(self, name):
        return getattr(self.pb, name)


def names(app):
    return sorted(e["nick"] for b in app.pcsync.committed["boxes"] for e in b)


def click(app, label):
    app.render()
    for rect, cb, lab in reversed(app.click_targets):
        if lab == label:
            cb()
            return True
    return False


run(200, lambda: A.net.status == "online" and B.net.status == "online" and len(A.roster) == 2)

# 1. deposits with the real PCs, then autosave commits + uploads
d = Driven(A, B)
pa.open_pc(d)
check("Ilias deposits PIDGEY", pa.deposit(d, 1))
check("Ilias deposits RATTATA", pa.deposit(d, 1))
pa.close_pc(d)
run(30)
check("Ilias's autosave commits", A.autosave(force=True))
d = Driven(B, A)
play.pc_open(d)
play.pc_deposit(d, "SENTRET")
play.pc_close(d)
run(60)
check("Alex's autosave commits", B.autosave(force=True))
run(200)
check("stores saved", names(A) == ["PIDGEY", "RATTATA"] and names(B) == ["SENTRET"], (names(A), names(B)))

# 2. trade through the window (mouse)
e_alex = next(r for r in A.roster if r["uid"] == B.cfg["uid"])
A.action_trade(e_alex)
run(150, lambda: B.mode == "prompt")
check("Alex gets the trade invite", B.mode == "prompt" and B.prompt["kind"] == "trade")
time.sleep(0.6)
check("Accept button works", click(B, "Accept"))
run(150, lambda: A.mode == "trade" and B.mode == "trade")
check("both trade windows open", A.mode == "trade" and B.mode == "trade")
A.render(); pygame.image.save(A.screen, str(tmp / "1_trade_window.png"))
pid = next(e for _, e in A.trade_entries() if e["nick"] == "PIDGEY")
A.offer_entry(pid)
sen = next(e for _, e in B.trade_entries() if e["nick"] == "SENTRET")
B.offer_entry(sen)
run(100, lambda: A.trade and A.trade.their_entry and B.trade and B.trade.their_entry)
A.render(); pygame.image.save(A.screen, str(tmp / "2_offers.png"))
note = A.entry_note(A.trade.their_entry)
check("Ilias is told Sentret can't be used in Yellow", note is not None and "Yellow" in note[0], note)
check("Confirm button", click(A, "Confirm trade") and click(B, "Confirm trade"))
run(300, lambda: A.trade is None and B.trade is None and "SENTRET" in names(A))
check("trade done: Ilias has SENTRET, Alex has PIDGEY", names(A) == ["RATTATA", "SENTRET"]
      and names(B) == ["PIDGEY"], (names(A), names(B)))
run(200)
inbox = lambda app: __import__("json").load(open(tmp / "data" / "pc" / f"{app.cfg['uid']}.json")).get("inbox")
check("server inbox acknowledged by both", inbox(A) == [] and inbox(B) == [], (inbox(A), inbox(B)))

# 3. Yellow PC: ?SENTRET + RATTATA, PIDGEY gone for good
d = Driven(A, B)
pa.open_pc(d)
box = [n for _, n in pa.box(d)]
check("Ilias's Yellow PC shows ?SENTRET and RATTATA, no PIDGEY", sorted(box) == ["?SENTRET", "RATTATA"], box)
pa.close_pc(d)
run(30)
check("PIDGEY was not re-imported", "PIDGEY" not in names(A) and A.pcsync.count() == 2)

# 4. Crystal PC: PIDGEY arrives and can be withdrawn for real
d = Driven(B, A)
play.pc_open(d)
recs = B.pc_codec.read_boxes()[B.pc_codec.current_box()]
shown = [text.decode(r[crystal.BOX_MON_SIZE + crystal.NAME_SIZE:crystal.REC_SIZE]) for r in recs]
check("Alex's Crystal PC shows PIDGEY", "PIDGEY" in shown, shown)
play.pc_withdraw(d, "PIDGEY")
play.pc_close(d)
run(30)
check("PIDGEY withdrawn into Alex's Crystal party", any(m.nick_str() == "PIDGEY" and m.ot_str() != "KRIS"
                                                        for m in B.game.party()),
      [(m.nick_str(), m.ot_str()) for m in B.game.party()])

# 5. gift: Ilias gives RATTATA
A.action_gift(e_alex)
run(150, lambda: B.mode == "prompt")
check("Alex gets the gift invite", B.mode == "prompt" and B.prompt["kind"] == "gift")
time.sleep(0.6)
click(B, "Accept")
run(150, lambda: A.mode == "trade" and B.mode == "trade")
check("gift windows: Ilias gives, Alex receives", A.trade.giving and B.trade.receiving_only)
rat = next(e for _, e in A.trade_entries() if e["nick"] == "RATTATA")
A.offer_entry(rat)
run(100, lambda: B.trade and B.trade.their_entry)
B.render(); pygame.image.save(B.screen, str(tmp / "3_gift_receiver.png"))
click(A, "Give it")
click(B, "Accept gift")
run(300, lambda: A.trade is None and B.trade is None and "RATTATA" in names(B))
check("gift done", names(A) == ["SENTRET"] and "RATTATA" in names(B), (names(A), names(B)))
A.shutdown(); B.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
