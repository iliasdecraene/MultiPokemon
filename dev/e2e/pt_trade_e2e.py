"""Trades + gifts between a Yellow player and a Platinum player (real ROMs, real PCs, ONE process: PyBoy + DeSmuME).

  1. Ilias (Yellow) deposits PIDGEY + RATTATA; Lucas (Platinum) deposits his TURTWIG; both commit to the server
  2. trade window: PIDGEY <-> TURTWIG (Ilias is told Turtwig can't be used in Yellow; Lucas isn't warned)
  3. Ilias's Yellow PC: the ?AAAAAAAAA stand-in (Lucas's nicknamed Turtwig) + RATTATA, no PIDGEY
  4. Lucas's Platinum PC: PIDGEY (converted to Gen 4) - withdrawn for real into his party
  5. gift: Ilias gives RATTATA, Lucas only accepts; it shows up in Lucas's Platinum box
Run: python dev/e2e/pt_trade_e2e.py   (needs dev/states/{yellow.gb,pokecenter_pc.state,platinum.nds,pt_pc.dsst})
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_pttrade_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  YL_PT_ROM=vanilla (default) | sinnoh: the Sinnoh ROM the app / DS core runs
print("Sinnoh ROM:", ptrom.label())
sys.path.insert(0, _HERE)
import pygame
pygame.quit = lambda: None
from PIL import Image
from yellowlink.config import Config
from yellowlink import app as appmod, pk4
import pc_actions as pa

PT_ROM = os.path.join(STATES, "platinum.nds")
GB_ROM = os.path.join(STATES, "yellow.gb")
PORT = 8794
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


def make(name, gid, state, games):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = Config(d / "config.json")
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", seen_help=True,
               games=games, current_game=gid)
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)       # allow autosave (commits the PC store)
    return a


# Ilias also has Platinum in his library (not running): his Yellow uses its species names / growth data
A = make("Ilias", "yellow", "pokecenter_pc.state", {"yellow": GB_ROM, "platinum": PT_ROM})
B = make("Lucas", "platinum", "pt_pc.dsst", {"platinum": PT_ROM})


def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step()
        if cond and cond():
            return True
    return False


class Driven:
    """PyBoy wrapper for pc_actions: keeps the Platinum app (and Ilias's network) running while Yellow is driven."""
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


# --- Platinum driving (both apps keep running) ----------------------------------------------------------------
def press(k, after=40):
    B.nds.button_press(k)
    run(6)
    B.nds.button_release(k)
    run(after)


def storage_menu():
    top = B.nds.screen_ndarray()[78:87, 12:100]
    return int((top.min(axis=2) > 230).sum()) > 400


def pt_open(mode):
    for _ in range(10):
        if storage_menu():
            break
        press("a")
    if not storage_menu():
        return False
    for _ in range({"deposit": 0, "withdraw": 1}[mode]):
        press("down", 10)
    press("a", 30)
    ok = run(300, B.game.pc_open)
    run(150)
    return ok


def pt_close():
    for _ in range(8):
        press("b", 40)
        if not B.game.pc_open():
            break
    for _ in range(8):
        if B.game.is_safe():
            break
        press("b", 40)
    return run(200, B.game.is_safe)


def box_view(b=0):
    return [(pk4.PK4(pk4.decrypt(r)).species, pk4.PK4(pk4.decrypt(r)).nickname) for r in B.game.box_raw(b)
            if r != pk4.EMPTY_BOX]


def party_species():
    for _ in range(120):
        recs = B.game.party_checked()
        if recs is not None:
            return [pk4.PK4(pk4.decrypt(r[:136])).species for r in recs]
        run(1)
    return None


def shot(name):
    Image.frombytes("RGB", (256, 384), B.nds.screen_rgb()).save(tmp / name)
    return tmp / name


run(300, lambda: A.net.status == "online" and B.net.status == "online" and len(A.roster) == 2 and len(B.roster) == 2)
check("both online (Yellow in PyBoy + Platinum in DeSmuME, one process)", A.net.status == B.net.status == "online"
      and A.pyboy is not None and B.nds is not None and B.full and B.pc_codec is not None)

# 1. deposits with the real PCs; autosave commits + uploads
d = Driven(A, B)
pa.open_pc(d)
check("Ilias deposits PIDGEY", pa.deposit(d, 1))
check("Ilias deposits RATTATA", pa.deposit(d, 1))
pa.close_pc(d)
run(30)
check("Ilias's autosave commits", A.autosave(force=True))
check("Lucas opens DEPOSIT", pt_open("deposit"))
press("a", 40)
press("a", 40)                              # STORE
press("a", 80)                              # Box 1
check("Lucas deposits a TURTWIG", party_species() == [387] and [s for s, _ in box_view()] == [387])
check("Lucas closes the PC", pt_close())
run(30)
check("Lucas's autosave commits", B.autosave(force=True))
run(300, lambda: names(A) == ["PIDGEY", "RATTATA"] and names(B) == ["AAAAAAAAAA"] and not A.pcsync.uploading
    and not B.pcsync.uploading)
check("stores saved", names(A) == ["PIDGEY", "RATTATA"] and names(B) == ["AAAAAAAAAA"], (names(A), names(B)))

# 2. trade through the window (mouse)
e_lucas = next(r for r in A.roster if r["uid"] == B.cfg["uid"])
A.action_trade(e_lucas)
run(150, lambda: B.mode == "prompt")
check("Lucas gets the trade invite (in Platinum)", B.mode == "prompt" and B.prompt["kind"] == "trade")
time.sleep(0.6)
check("Accept button works", click(B, "Accept"))
run(150, lambda: A.mode == "trade" and B.mode == "trade")
check("both trade windows open", A.mode == "trade" and B.mode == "trade")
pid = next(e for _, e in A.trade_entries() if e["nick"] == "PIDGEY")
A.offer_entry(pid)
turt = next(e for _, e in B.trade_entries() if e["nick"] == "AAAAAAAAAA")
B.offer_entry(turt)
run(100, lambda: A.trade and A.trade.their_entries and B.trade and B.trade.their_entries)
A.render(); pygame.image.save(A.screen, str(tmp / "1_yellow_trade_window.png"))
B.render(); pygame.image.save(B.screen, str(tmp / "2_platinum_trade_window.png"))
note = A.entry_note(A.trade.their_entries[0])
check("Ilias is told the Turtwig can't be used in Yellow", note is not None and "Yellow" in note[0], note)
check("Lucas isn't warned about PIDGEY (it converts)", B.entry_note(B.trade.their_entries[0]) is None)
check("labels: 'AAAAAAAAAA (Turtwig) Lv7' in Yellow (Platinum names), 'Pidgey Lv6' in Platinum",
      A.entry_label(A.trade.their_entries[0]) == "AAAAAAAAAA (Turtwig) Lv7"
      and B.entry_label(B.trade.their_entries[0]) == "Pidgey Lv6",
      (A.entry_label(A.trade.their_entries[0]), B.entry_label(B.trade.their_entries[0])))
check("Confirm buttons", click(A, "Confirm trade") and click(B, "Confirm trade"))
run(300, lambda: A.trade is None and B.trade is None and "AAAAAAAAAA" in names(A))
check("trade done: Ilias has the Turtwig, Lucas has PIDGEY", names(A) == ["AAAAAAAAAA", "RATTATA"]
      and names(B) == ["PIDGEY"], (names(A), names(B)))
run(200)
inbox = lambda app: __import__("json").load(open(tmp / "data" / "pc" / f"{app.cfg['uid']}.json")).get("inbox")
check("server inbox acknowledged by both", inbox(A) == [] and inbox(B) == [], (inbox(A), inbox(B)))

# 3. Yellow PC: ?AAAAAAAAA stand-in + RATTATA
d = Driven(A, B)
pa.open_pc(d)
box = [n for _, n in pa.box(d)]
check("Ilias's Yellow PC shows ?AAAAAAAAA and RATTATA, no PIDGEY", sorted(box) == ["?AAAAAAAAA", "RATTATA"], box)
pa.close_pc(d)
run(30)
check("PIDGEY was not re-imported in Yellow", "PIDGEY" not in names(A) and A.pcsync.count() == 2)

# 4. Platinum PC: PIDGEY arrives (converted) and can be withdrawn for real
check("Lucas opens WITHDRAW", pt_open("withdraw"))
check("Lucas's Platinum box 1 shows PIDGEY (Gen 4)", box_view() == [(16, "PIDGEY")], box_view())
s1 = shot("3_platinum_box_traded_pidgey.png")
press("a", 40)
press("a", 100)                             # WITHDRAW
check("PIDGEY withdrawn into Lucas's party", party_species() == [387, 16], party_species())
check("Lucas closes the PC", pt_close())
run(30)
check("Lucas's store: PIDGEY left the database (it's in his party)", B.pcsync.count() == 0)
check("Lucas's autosave commits", B.autosave(force=True) and names(B) == [])
run(200)

# 5. gift: Ilias gives RATTATA, it appears in Lucas's Platinum box
A.action_gift(e_lucas)
run(150, lambda: B.mode == "prompt")
check("Lucas gets the gift invite", B.mode == "prompt" and B.prompt["kind"] == "gift")
time.sleep(0.6)
click(B, "Accept")
run(150, lambda: A.mode == "trade" and B.mode == "trade")
check("gift windows: Ilias gives, Lucas receives", A.trade and A.trade.giving and B.trade and B.trade.receiving_only)
A.offer_entry(next(e for _, e in A.trade_entries() if e["nick"] == "RATTATA"))
run(100, lambda: B.trade and len(B.trade.their_entries) == 1)
check("Give it / Accept gift buttons", click(A, "Give it") and click(B, "Accept gift"))
run(300, lambda: A.trade is None and B.trade is None and "RATTATA" in names(B))
check("gift done", names(A) == ["AAAAAAAAAA"] and names(B) == ["RATTATA"], (names(A), names(B)))
check("Lucas is told", any("Rattata" in t[0] for t in B.toasts), [t[0] for t in B.toasts])
check("Lucas opens WITHDRAW again", pt_open("withdraw"))
check("RATTATA in Lucas's Platinum box 1", box_view() == [(19, "RATTATA")], box_view())
s2 = shot("4_platinum_box_gift_rattata.png")
check("look-only visit", pt_close() and not B.pcsync.pending)
A.shutdown(); B.shutdown()
for p in (s1, s2):
    print("screenshot:", p)
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
