"""Raids e2e (SPEC.md section 12): a local server + two real clients.

  1. Two Yellow players (Ilias, Alex) in Pallet Town; the server (YL_RAID_FIRST / YL_RAID_FORCE / YL_RAID_SPOTS)
     opens a Mew raid on the square right below them: both get the notice (toast + horn + the server's chat line),
     the sidebar card, the den (drawn by raid_gb) - screenshots at 3x.
  2. Walking down is blocked by the den; A after bumping into it opens "Join the raid against Mew?" (screenshot);
     both join (the pool grows by 2 bars each), a forced resume snapshot is taken, the raid battles start in both
     games, fast-forward is off, no snapshot while fighting, trades to a raiding player are refused (busy).
  3. The battles run on the move timer alone (auto_pick: the first turn on the real 10 s, ring screenshot, then 3 s):
     damage goes to the server as running totals, the pool hits 0, each player gets raid_down with their share as
     the chance + the server's roll, throws one Poké Ball (driven here) and the result matches the roll; the raid
     ends with the right chat line; a catch is in the catcher's party.
  4. Platinum UI at 1.5x (Alex switches to Platinum; the raid UI with a fake battle module - raid_pt isn't needed for
     the screenshots): card for a Kanto raid ("Go there"), a Sinnoh raid's notice, the join dialog, the timer ring.
Needs dev/states/{yellow.gb, platinum.nds, pallet_after_lab.state, pt_town.dsst}.
Run: python dev/e2e/raid_flow.py   (YL_RAID_CATCH=always|never forces the server's catch roll; screenshots: the folder printed at the end; deleted with --clean)
     python dev/e2e/raid_flow.py --fake   (no real raid battles: a fake RaidBattle, e.g. before raid_gb exists)
"""
import atexit
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
FAKE = "--fake" in sys.argv
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_raid_"))
shots = pathlib.Path(os.environ.get("YL_RAID_SHOTS") or tempfile.mkdtemp(prefix="yl_raid_shots_"))
shots.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["YL_PT_ROM"] = "vanilla"             # no 128 MB Complete Sinnoh copy in the temp folder
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame  # noqa: E402
pygame.quit = lambda: None          # several apps share this process
from yellowlink.config import Config  # noqa: E402
from yellowlink import app as appmod, game, raid  # noqa: E402

PORT = 8798
FAILS = []
HORNS = {}


def cleanup():
    try:
        srv.terminate()
        srv.wait(timeout=10)                     # it saves its data on the way out: before the folder goes
    except Exception:  # noqa: BLE001
        pass
    if FAILS:
        try:
            print("--- server log (last lines) ---\n" + "".join(open(tmp / "srv.log").readlines()[-25:]))
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)       # saves, server data, app data (disk space is tight)


atexit.register(cleanup)


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)
    return ok


def counted_horn(self):
    HORNS[self.cfg["name"]] = HORNS.get(self.cfg["name"], 0) + 1


appmod.App.play_raid_horn = counted_horn


class Cfg(Config):
    def __init__(self, d, name, rom, gid, extra=None):
        d.mkdir(parents=True, exist_ok=True)
        super().__init__(d / "config.json")
        self.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=rom, seen_help=True,
                    games={gid: rom, **(extra or {})}, current_game=gid, auto_resume=True, scale=3, ds_scale=1.5)


def make(name, rom, gid, state, extra=None):
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(Cfg(tmp / name, name, os.path.join(STATES, rom), gid, extra))
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


# ------------------------------------------------------------------------------------------------ fake battle
class FakeBattle:
    """--fake: no game hooks; damage comes in fixed hits, the catch follows group_down()."""

    def __init__(self, app, boss):
        self.app, self.boss, self.ev, self.t = app, boss, [], 0
        self.down = None
        self.over = False

    def can_join(self):
        return True, ""

    def start(self, pool):
        self.ev.append({"type": "started"})
        return True

    def step(self):
        self.t += 1
        if not self.over and self.down is None and self.t % 400 == 0:
            self.ev.append({"type": "damage", "hp": 30, "of": 60, "mb": 500})
        if self.down is not None and not self.over and self.t % 30 == 0:
            self.over = True
            self.ev += [{"type": "caught" if self.down >= 1 else "missed"},
                        {"type": "ended", "outcome": "caught" if self.down >= 1 else "missed"}]
        ev, self.ev = self.ev, []
        return ev

    def set_pool(self, *a):
        pass

    def group_down(self, chance):
        self.down = chance

    def flee(self):
        self.over = True
        self.ev += [{"type": "fled"}, {"type": "ended", "outcome": "fled"}]

    def auto_pick(self):
        pass

    def taking_over(self):
        return False

    def busy(self):
        return not self.over

    def remove(self):
        pass


if FAKE:
    _real_module = raid._game_module
    raid._game_module = lambda gid: types.SimpleNamespace(RaidBattle=FakeBattle,
                                                         Den=getattr(_real_module(gid), "Den", None))

# ------------------------------------------------------------------------------------------------ setup
A = make("Ilias", "yellow.gb", "yellow", "pallet_after_lab.state")
B = make("Alex", "yellow.gb", "yellow", "pallet_after_lab.state")
pos = A.game.position()
DEN = [pos["map"], pos["x"], pos["y"] + 1]           # right below the player (Oak's lab door is above)
spots = tmp / "spots.json"
spots.write_text(json.dumps({"version": 1, "regions": {"kanto": {"names": {str(pos["map"]): "Pallet Town"},
                                                                 "spots": [DEN + [0]]}}}))
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)],
                       cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server"), YL_RAID_FIRST="2",
                                YL_RAID_FORCE="kanto:151", YL_RAID_SPOTS=str(spots), YL_RAID_OPEN="15",
                                YL_RAID_CATCH=os.environ.get("YL_RAID_CATCH", "")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
time.sleep(1.5)
assert srv.poll() is None, f"the local server didn't start (port {PORT} busy?) - see {tmp / 'srv.log'}"


def run(n, cond=None, apps=None):
    for _ in range(n):
        for a in (apps or (A, B)):
            a.step()
        time.sleep(0.0005)
        if cond and cond():
            return True
    return False


def wait(secs, cond, apps=None):
    """Run the apps until cond() or `secs` of wall time (the server's scheduler ticks every few seconds)."""
    end = time.time() + secs
    while time.time() < end:
        if run(10, cond, apps):
            return True
        time.sleep(0.005)
    return False


def shot(app, name, scale=None):
    if scale:
        app.cfg[app._scale_key()] = scale
        app._make_window()
    app.render()
    pygame.image.save(app.screen, str(shots / name))


def lines(app):
    return [m for _, m, _ in app.chat_log]


def buff(app):
    """Test help: the lead Pikachu at Lv100 with 999 everything (each hit takes a whole bar), 5 Poké Balls."""
    m = app.pyboy.memory
    for i in range(m[game.PARTY_COUNT]):
        a = game.PARTY_MONS + i * 44
        m[a + 3] = m[a + 33] = 100
        for off in (1, 34, 36, 38, 40, 42):
            m[a + off], m[a + off + 1] = 0x03, 0xE7
    m[game.NUM_BAG_ITEMS], m[game.BAG_ITEMS], m[game.BAG_ITEMS + 1], m[game.BAG_ITEMS + 2] = 1, 4, 5, 0xFF


print(f"player at map {pos['map']} ({pos['x']}, {pos['y']}); den at {DEN}", flush=True)
ok = wait(20, lambda: all("raids" in getattr(a, "server_features", ()) and len(a.roster) == 2 for a in (A, B)))
check("both online; the server lists the raids feature", ok)
buff(A)
buff(B)
run(5)

# ------------------------------------------------------------------------------------------------ 1. notice
ok = wait(25, lambda: A.raid is not None and B.raid is not None)
check("the raid opened for both", ok)
WANT = "RAID! Mew appeared in Pallet Town (Kanto) - 15 minutes!"
wait(5, lambda: WANT in lines(A) and WANT in lines(B))
check("the server's chat line: " + WANT, WANT in lines(A) and WANT in lines(B), str(lines(A)[-3:]))
check("toast + horn for both", HORNS == {"Ilias": 1, "Alex": 1} and
      any(t[0].startswith("RAID! Mew appeared in Pallet Town (Kanto)") for t in A.toasts), f"{HORNS} {A.toasts}")
check("Ilias's game has the den", A.raid_den is not None and A.raid_here())
shot(A, "1_notice_card_den_yellow_3x.png", 3)
A.toasts.clear()
shot(A, "2_card_den_yellow_3x.png", 3)
shot(A, "2b_card_den_yellow_4x.png", 4)
A.cfg[A._scale_key()] = 3
A._make_window()

# ------------------------------------------------------------------------------------------------ 2. joining
y0 = A.game.position()["y"]
A.press("down", True)
run(40)
A.press("down", False)
run(20)
check("walking onto the den is blocked", A.game.position()["y"] == y0, str(A.game.position()))
A.press("a", True)
A.press("a", False)
check("A after bumping into the den opens the join dialog", A.mode == "raid_confirm" and A.raid_confirm,
      f"{A.mode} {A.raid_confirm}")
check("can join (party, balls, room)", bool(A.raid_confirm and A.raid_confirm["ok"]), str(A.raid_confirm))
shot(A, "3_join_dialog_yellow_3x.png", 3)
A.raid_confirm["shown"] = 0
A.raid_answer(True)
B.press("down", True)
run(40, apps=(B,))
B.press("down", False)
B.press("a", True)
B.press("a", False)
ok_b = B.mode == "raid_confirm" and B.raid_confirm and B.raid_confirm["ok"]
check("Alex gets the dialog too", bool(ok_b), f"{B.mode} {B.raid_confirm}")
if ok_b:
    B.raid_confirm["shown"] = 0
    B.raid_answer(True)
A.fast = True
snap0 = A.cfg.resume_path.stat().st_mtime if A.cfg.resume_path.exists() else 0
ok = run(1500, lambda: A.raid_fighting() and B.raid_fighting())
check("both raid battles started", ok, f"{A.raid_phase} {B.raid_phase}")
wait(3, lambda: A.raid and A.raid.get("pool_max") == 4000)
check("pool = 2 bars per player", A.raid and A.raid.get("pool_max") == 4000, str(A.raid and A.raid.get("pool_max")))
check("a resume snapshot right before the battle", A.cfg.resume_path.exists()
      and A.cfg.resume_path.stat().st_mtime > snap0)
check("fast-forward went off", A.fast is False)
check("no resume snapshot while the raid battle runs", A.autosave(force=True) is False)
B.net.send({"type": "trade_request", "to": A.cfg["uid"]})
wait(5, lambda: any("raid" in m and m.startswith("Server:") for m in lines(B)))
check("trades while raiding are refused (server: busy)", any("raid" in m and m.startswith("Server:") for m in lines(B)),
      str(lines(B)[-3:]))

# ------------------------------------------------------------------------------------------------ 3. the fight
ring_shot = False
caught_by = {}
throw = {}
DOWN = {}
t_end = time.time() + (120 if FAKE else 900)
first_timer = True
while time.time() < t_end and (A.raid_battle is not None or B.raid_battle is not None):
    run(1)
    for app in (A, B):
        b = app.raid_battle
        if b is None:
            continue
        if not first_timer or app is B:
            app.raid_timer.secs = 3.0
        if app is A and app.raid_timer.running and not ring_shot and app.raid_timer.left(time.time()) < 7:
            ring_shot = True
            shot(A, "4_timer_ring_card_yellow_3x.png", 3)
        if app is A and first_timer and getattr(b, "auto", False):
            first_timer = False
        if not FAKE and b.decision is None and not getattr(b, "auto", False) and b.frame % 30 == 0 \
                and app.raid_phase != "catch":
            app.pyboy.button("b", 3)            # the player taps through the battle's texts (A/B both do)
        if app.raid_phase == "catch":
            DOWN.setdefault(app.cfg["name"], dict(app.raid_down_info))
            app.raid_timer.input_done()
            app.raid_timer.secs = 1e9
            if FAKE:
                continue
            st = throw.setdefault(app.cfg["name"], {"i": 0, "next": 0})
            if b.decision is not None and getattr(b, "ui", None) == "menu" and st["i"] < 5 and b.frame >= st["next"]:
                btn = ("up", "left", "down", "a", "a")[st["i"]]
                app.pyboy.button(btn, 3)
                st["i"] += 1
                st["next"] = b.frame + (40 if btn == "a" else 12)
            elif st["i"] >= 4 and b.frame >= st["next"] and getattr(b, "ui", None) in ("bag",):
                app.pyboy.button("a", 3)
                st["i"] = 5
                st["next"] = b.frame + 40
            elif b.frame >= st["next"] + 60 and b.decision is None:
                app.pyboy.button("b", 3)                  # texts ("Gotcha!", Pokédex page, nickname -> NO)
                st["next"] = b.frame + 30
check("both raid battles ended", A.raid_battle is None and B.raid_battle is None, f"{A.raid_phase} {B.raid_phase}")
check("the timer ring was on screen", ring_shot or FAKE)
DOWN = {a.cfg["name"]: a.raid_down_info for a in (A, B) if a.raid_down_info}
check("both got raid_down with a chance (their share) and the server's roll",
      set(DOWN) == {"Ilias", "Alex"} and all(0 <= d["chance"] <= 1 for d in DOWN.values()), str(DOWN))
ok = wait(15, lambda: A.raid is not None and A.raid.get("phase") == "over")
check("the raid ended", ok, str(A.raid and A.raid.get("phase")))
summ = A.raid or {}
parts = {p["name"]: p for p in summ.get("participants", [])}
check("everyone's damage adds up to the whole pool", sum(p["dealt"] for p in parts.values()) >= 4000 and
      summ.get("pool") == 0, str(parts))
shares = {n: p["dealt"] / max(1, sum(q["dealt"] for q in parts.values())) for n, p in parts.items()}
for name, d in DOWN.items():
    check(f"{name}: chance = share of the damage ({d['chance']:.2f})", abs(d["chance"] - shares[name]) < 0.01)
    want = "caught" if d["catch"] else "missed"
    check(f"{name}: the throw followed the server's roll -> {want}", parts.get(name, {}).get("state") == want,
          str(parts.get(name)))
caught = [n for n, p in parts.items() if p["state"] == "caught"]
ends = ([f"Raid over: {' and '.join(order)} caught Mew!" for order in (caught, caught[::-1])] if caught
        else ["Raid over: Mew got away."])
run(60)
check("chat line: " + ends[0], any(m in ends for m in lines(A)), str(lines(A)[-4:]))
if not FAKE:
    for app in (A, B):
        if app.cfg["name"] in caught:
            run(600, lambda: app.game.is_safe(), apps=(A, B))
            sp = [app.rom.dex_no(m.species) for m in app.game.party()]
            check(f"{app.cfg['name']}'s Mew is in the party", 151 in sp, str(sp))
check("snapshots again after the raid battle", A.autosave(force=True) is True)
shot(A, "5_card_over_yellow_3x.png", 3)

# ------------------------------------------------------------------------------------------------ 4. Platinum UI
B.shutdown()
B = make("Alex", "platinum.nds", "platinum", "pt_town.dsst", {"yellow": os.path.join(STATES, "yellow.gb")})
wait(20, lambda: "raids" in getattr(B, "server_features", ()) and B.net.status == "online", apps=(B,))
check("Alex online in Platinum", B.ds and B.full and B.net.status == "online")
now = B.server_time()
kanto = dict(summ, phase="open", pool=2000, pool_max=4000, closes=now + 600,
             participants=[{"uid": "x", "name": "Ilias", "dealt": 2000, "state": "fighting"}])
B.on_raid_open({"raid": kanto, "fresh": True})
shot(B, "6_notice_card_kanto_platinum_1.5x.png", 1.5)
check("a Kanto raid seen from Platinum: card button 'Go there'", (B.raid_card_button() or ("",))[0] == "Go there")
B.raid = None
B._raid_reset()
raid._game_module = lambda gid: types.SimpleNamespace(RaidBattle=FakeBattle, Den=None)
sinnoh = dict(kanto, id="ui-test", region="sinnoh", game="platinum", species=384, name="Rayquaza", shiny=True,
              spot={"map": 411, "x": 3, "y": 3, "name": "Twinleaf Town"}, pool=0, pool_max=0, participants=[])
B.on_raid_open({"raid": sinnoh, "fresh": True})
B.raid_open_dialog()
check("Platinum join dialog", B.mode == "raid_confirm" and B.raid_confirm["ok"], str(B.raid_confirm))
shot(B, "7_join_dialog_platinum_1.5x.png", 1.5)
B.mode = "play"
B.raid_confirm = None
B.raid_battle = FakeBattle(B, {})
B.raid_phase = "fight"
B.raid = dict(sinnoh, pool=2600, pool_max=4000,
              participants=[{"uid": B.cfg["uid"], "name": "Alex", "dealt": 900, "state": "fighting"},
                            {"uid": "x", "name": "Ilias", "dealt": 500, "state": "fighting"}])
B.raid_meter = raid.DamageMeter()
B.raid_meter.total = 900
B.raid_timer.need_input(time.time() - 3.5)
B.toasts.clear()
shot(B, "8_timer_ring_card_platinum_1.5x.png", 1.5)
B.raid_battle = None
B.raid_phase = "idle"

A.shutdown()
B.shutdown()
print("\nscreens in", shots)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
cleanup()
os._exit(1 if FAILS else 0)
