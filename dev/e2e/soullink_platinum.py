"""Soul Link on the real Platinum ROM: the client (DeSmuME) in a challenge with a simulated partner, real battles.

Only ONE DeSmuME can run per process, so the partner is a plain protocol client that posts its own ledger events
(what its referee would send). Everything on Ilias's side is the real game + the real referee:

  1. Twinleaf Town (pt_starter): the starter Turtwig is seen as a gift = the Twinleaf encounter, linked with the
     partner's; our Sinnoh state carries the run id and only the partner is drawn (not a friend outside the run)
  2. Route 201 (pt_battle): a free battle before Poké Balls (nothing counts), then Poké Balls -> rules on; a wild
     encounter is logged as Route 201's first encounter, caught with a Master Ball -> linked with the partner's catch
  3. the partner's Turtwig faints -> ours dies too and is forced into the PC (real box write, game still fine)
  4. a gift in an area already used -> doesn't count, goes to the PC
  5. our last Pokémon faints in a real battle -> dead, blackout -> the run fails for both (pop-up)
Run: python dev/e2e/soullink_platinum.py   (needs dev/states/platinum.nds, pt_starter.dsst, pt_battle.dsst)
Test shortcuts (memory writes, marked below): Poké Balls put in the bag, the Pokédex marked complete (Platinum
can't show a new Pokédex entry before the story gives you the Pokédex), low HP for the faint, the gift's record.
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, uuid
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_ptsl_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame
pygame.quit = lambda: None
from yellowlink import __version__, pk4
from yellowlink import app as appmod, platinum as P
from yellowlink.config import Config
from yellowlink.net import NetClient

PT_ROM = os.path.join(STATES, "platinum.nds")
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


def _free_port():
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


PORT = _free_port()
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)],
                       cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="pt", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)


class Partner:
    """The other player as the server sees it (a client after 0.7.2 with Platinum)."""

    def __init__(self, name, uid=None):
        self.name, self.uid = name, uid or str(uuid.uuid4())
        self.inbox = []
        self.online = False
        self.net = NetClient(f"ws://127.0.0.1:{PORT}", {"name": name, "uid": self.uid, "key": "pt",
                                                        "version": __version__})
        self.net.start()

    def pump(self):
        for m in self.net.poll():
            self.inbox.append(m)
        if self.net.status == "online" and not self.online:
            self.online = True
            self.net.send({"type": "library", "games": ["platinum"], "challenge_games": ["platinum"]})

    def take(self, mtype, **match):
        for i, m in enumerate(self.inbox):
            if m.get("type") == mtype and all(m.get(k) == v for k, v in match.items()):
                return self.inbox.pop(i)
        return None

    def events(self, p=None):
        return [m["event"] for m in self.inbox if m.get("type") == "challenge_event"
                and (p is None or m["event"].get("p") == p)]

    def send(self, **msg):
        return self.net.send(msg)

    def event(self, cid, **ev):
        ev.setdefault("cid", uuid.uuid4().hex[:10])
        self.send(type="challenge_event", challenge_id=cid, event=ev)


cfg = Config()
cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{PORT}", room_key="pt", rom_path=PT_ROM, seen_help=True,
           games={"platinum": PT_ROM}, current_game="platinum", ds_scale=2)
cfg.save()
B = Partner("Alex")
A = None


def run(n, cond=None):
    for _ in range(n):
        A.handle_events()
        A.step()
        B.pump()
        if cond and cond():
            return True
    return False


def wait(cond, secs):
    """run() until cond() or `secs` wall-clock seconds passed (the machine may be busy)."""
    end = time.time() + secs
    while time.time() < end:
        if run(20, cond):
            return True
    return False


def boot(state=None):
    if state:
        os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    try:
        return appmod.App(cfg)
    finally:
        os.environ.pop("YL_DEV_LOAD_STATE", None)


def tap(x, y, after=60):
    A.nds.touch(x, y)
    run(6)
    A.nds.touch_release()
    run(after)


def key(k, after=30):
    A.nds.button_press(k)
    run(6)
    A.nds.button_release(k)
    run(after)


def keys(app):
    p = app.game.party_checked() or []
    return [P.mon_key(r) for r in p]


def box_keys(app):
    return [P.mon_key(r) for r in app.game.current_box_records()]


def body():
    return A.game.save_body()


def give_balls():
    """TEST SHORTCUT: 5 Master Balls in the bag's ball pocket, and every species marked caught in the Pokédex."""
    A.game.m.w16(body() + P.PAGE_BAG + P.BAG_BALLS, 1)
    A.game.m.w16(body() + P.PAGE_BAG + P.BAG_BALLS + 2, 5)
    dex = A.game.r32(A.game.save_data() + 0x20024 + 16 * 7 + 8)          # save table entry 7 = Pokédex
    for i in range(32):                                                    # caught[16], seen[16] (u32 bit sets)
        A.game.m.w32(body() + dex + 4 + 4 * i, 0xFFFFFFFF)


def set_hp(slot, hp, status=None):
    """TEST SHORTCUT: a party mon's current HP (+ status: 0x08 = poisoned) (party section, encrypted with the PID)."""
    addr = A.game.party_addr(slot)
    pk = pk4.PK4(pk4.decrypt(A.game.read(addr, pk4.PARTY_SIZE)))
    pk.hp = hp
    if status is not None:
        pk.status = status
    A.game.m.write(addr, pk.encrypted())


def lead():
    p = A.game.party_checked()
    return pk4.PK4(pk4.decrypt(p[0])) if p else None


def walk_until_battle(limit=120):
    for k in range(limit):
        if A.game.battle_info() is not None:
            return True
        A.nds.button_press("left" if k % 2 == 0 else "right")
        run(10)
        A.nds.button_release("left" if k % 2 == 0 else "right")
        run(8)
    return A.game.battle_info() is not None


def wait_menu():
    """Until the battle waits for a command (FIGHT/BAG/RUN/POKéMON) - a fixed wait is enough for these short intros."""
    run(700)


def until_overworld(limit=4000, press="b"):
    """Mash through the end of a battle until the field is safe again."""
    for i in range(limit // 40):
        if A.game.battle_info() is None and A.game.is_safe():
            return True
        key(press, 34)
    return A.game.battle_info() is None and A.game.is_safe()


# --- start the run ---------------------------------------------------------------------------------------------
A = boot("pt_starter.dsst")
run(400, lambda: A.net.status == "online" and A.shared_games(B.uid) == ["platinum"])
A.new_challenge_form(B.uid, "Alex")
A.chal_form["dupes"] = False                 # several Starly on Route 201 would otherwise be "dupes"
A.send_challenge()
got = {}
run(300, lambda: got.setdefault("r", B.take("challenge_request")) is not None or got.pop("r") is not None)
req = got.get("r") or {}
B.send(type="challenge_respond", request_id=req.get("request_id"), accept=True)
run(300, lambda: A.enter_challenge is not None)
check("Platinum Soul Link started", bool(A.enter_challenge and A.enter_challenge["game"] == "platinum"))
cid = A.enter_challenge["id"]
A.shutdown()
cfg["active_challenge"] = A.enter_challenge
cfg.save()

# 1. Twinleaf Town: the starter is a gift ----------------------------------------------------------------------
A = boot("pt_starter.dsst")                  # test shortcut: start the run with the starter already chosen
B.send(type="challenge_join", challenge_id=cid)
wait(lambda: A.chal_wait is None and A.referee is not None, 30)
check("both in, referee running on Platinum", A.chal_wait is None and A.referee is not None
      and A.referee.view.ds)
turtwig = keys(A)[0]
B.event(cid, t="enc", area="twinleaf_town", dex=390, status="caught", key="b0chimchar0000aa", gift=True)
run(300, lambda: A.sl.mine()["enc"].get("twinleaf_town", {}).get("status") == "caught")
enc = A.sl.mine()["enc"].get("twinleaf_town", {})
check("starter = gift, Twinleaf Town's encounter", enc.get("gift") and enc.get("key") == turtwig
      and enc.get("dex") == 387, enc)
run(60)
check("starters are linked", A.sl.partner_of(turtwig) == "b0chimchar0000aa")
check("no Poké Balls yet: rules not on", not A.sl.mine()["balls"])
B.inbox = [m for m in B.inbox if m.get("type") != "state"]      # (states from before the run)
run(300, lambda: any(m.get("type") == "state" and m.get("uid") == cfg["uid"] for m in B.inbox))
mine_state = next((m for m in B.inbox if m.get("type") == "state" and m.get("uid") == cfg["uid"]), {})
check("our Sinnoh state carries the run id", mine_state.get("challenge") == cid and mine_state.get("game") ==
      "platinum", mine_state)
# friends: the partner (in the run) is drawn, a friend in the main game on the same spot is not
pos = A.game.position()
near = {"type": "state", "game": "platinum", "visible": True, "map": pos["map"], "x": pos["x"] + 1,
        "y": pos["y"], "px": pos["px"] + 16, "py": pos["py"], "facing": 1, "img": 0}
A.friends[B.uid] = appmod.Friend(B.uid, "Alex", dict(near, uid=B.uid, challenge=cid))
A.friends["u-misty"] = appmod.Friend("u-misty", "Misty", dict(near, uid="u-misty", x=pos["x"] - 1,
                                                                px=pos["px"] - 16))
for f in A.friends.values():
    A._snap(f)
run(90)
A.render()
tags = [t[0] for t in A._draw_friends_ds()]
check("in the run only the partner is drawn", "Alex" in tags and "Misty" not in tags, tags)
A.friends.pop("u-misty")
# field poison: Platinum never takes the last HP (pret Pokemon_DoPoisonDamage) - the mon survives at 1 HP, cured
set_hp(0, 3, 0x08)                            # TEST SHORTCUT: poisoned, 3 HP
hps = set()
for k in range(30):
    A.nds.button_press("left" if k % 2 == 0 else "right")
    run(10)
    A.nds.button_release("left" if k % 2 == 0 else "right")
    run(8)
    m = lead()
    if m:
        hps.add(m.hp)
m = lead()
check("field poison: down to 1 HP, then cured - no faint", m is not None and m.hp == 1 and m.status == 0
      and 0 not in hps and 2 in hps, (sorted(hps), m and m.status))
check("...and the referee sees no death", A.sl.status(turtwig) == "linked" and not A.sl.mine()["dead"])
set_hp(0, 21, 0)
run(30)

# 2. Route 201: free battle, balls, first encounter, catch, link -------------------------------------------------
A._chal_leaving = True                       # restart without "left": like a crash, back within the 30 s grace
A.shutdown()                                 # (as if the player walked there; same save, same Turtwig)
A = boot("pt_battle.dsst")                   # a wild BIDOOF battle on Route 201, before the player has Poké Balls
B.send(type="challenge_join", challenge_id=cid)
wait(lambda: A.chal_wait is None and A.referee is not None, 30)
check("back in the run in a battle", A.chal_wait is None and A.game.battle_info() is not None)
wait_menu()
for _ in range(10):                          # RUN
    if A.game.battle_info() is None:
        break
    tap(128, 178, 120)
until_overworld()
area = A.referee.view.area()
check("on Route 201", area == "route_201" and A.referee.view.area_name(area) == "Route 201", area)
check("battle before Poké Balls: nothing counted", "route_201" not in A.sl.mine()["enc"])
give_balls()
run(200, lambda: A.sl.mine()["balls"])
check("Poké Balls -> rules on (balls event)", A.sl.mine()["balls"])
check("battle found", walk_until_battle())
wild = A.game.battle_info() or {}
run(60)
enc = A.sl.mine()["enc"].get("route_201", {})
check("first encounter on Route 201 logged as open", enc.get("status") == "open" and enc.get("dex") == wild.get("dex"),
      (enc, wild.get("dex")))
wait_menu()
tap(32, 172)                                  # BAG
tap(190, 50)                                  # POKé BALLS
tap(64, 35)                                   # Master Ball
tap(100, 172, 900)                            # USE
until_overworld()
run(120)
enc = A.sl.mine()["enc"].get("route_201", {})
caught = enc.get("key")
check("caught, logged with the wild mon's key", enc.get("status") == "caught" and caught == wild.get("key")
      and caught in keys(A), (enc, wild))
B.event(cid, t="enc", area="route_201", dex=399, status="caught", key="b1bidoof00000aa")
run(200, lambda: A.sl.partner_of(caught) == "b1bidoof00000aa")
check("catch linked with the partner's Route 201 catch", A.sl.partner_of(caught) == "b1bidoof00000aa")
check("both on the server's log (partner sees our catch)",
      any(e.get("t") == "enc" and e.get("key") == caught and e.get("status") == "caught"
          for e in B.events(cfg["uid"])))

# 3. partner's starter faints -> ours dies and goes to the PC ---------------------------------------------------
B.event(cid, t="dead", key="b0chimchar0000aa", why="fainted")
run(600, lambda: turtwig in box_keys(A) and turtwig not in keys(A))
check("partner's Turtwig died -> ours is dead", A.sl.status(turtwig) == "dead"
      and A.sl.mine()["dead"].get(turtwig) == "partner fainted")
check("...and was moved into the PC (real box write)", turtwig in box_keys(A) and turtwig not in keys(A),
      (keys(A), box_keys(A)))
check("party still readable, Starly leads", A.game.party_checked() is not None and keys(A) == [caught])

# 4. a gift in an area already used -> goes to the PC ------------------------------------------------------------
rec = pk4.PK4(pk4.decrypt(A.game.party_raw()[0]))
rec.pid ^= 0x5A5A0000                         # TEST SHORTCUT: a "gift" Starly (another mon: new PID)
n = A.game.party_count()
A.game.m.write(A.game.party_addr(n), rec.encrypted())
A.game.m.w32(body() + P.PAGE_PARTY + 4, n + 1)
gift = P.mon_key(rec.encrypted())
run(600, lambda: gift in box_keys(A))
check("extra gift doesn't count (area used) and is dead", A.sl.mine()["dead"].get(gift) == "extra gift")
check("...and was moved into the PC", gift in box_keys(A) and gift not in keys(A), (keys(A), box_keys(A)))

# 5. our last Pokémon faints for real -> blackout, the run fails ---------------------------------------------------
set_hp(0, 1)                                  # TEST SHORTCUT: 1 HP
run(30)
check("next battle found", walk_until_battle())
wait_menu()
popped = False
for turn in range(12):
    if A.mode == "chal_popup" or A.game.battle_info() is None:
        break
    tap(128, 88, 90)                          # FIGHT
    if turn == 0:
        A.render()
        pygame.image.save(A.screen, str(tmp / "moves.png"))
    tap(192, 50, 700)                         # move 2 (Growl: no damage, the wild mon hits back)
    if A.sl.status(caught) == "dead":
        break
check("Starly fainted in a real battle -> dead right away", A.sl.status(caught) == "dead"
      and A.sl.mine()["dead"].get(caught) == "fainted", A.sl.mine()["dead"])
for _ in range(120):
    if A.mode == "chal_popup":
        break
    key("a", 30)
check("blackout logged", A.sl.mine()["blackout"])
run(120)
check("partner saw our Starly die and the blackout",
      any(e.get("t") == "dead" and e.get("key") == caught for e in B.events(cfg["uid"]))
      and any(e.get("t") == "blackout" for e in B.events(cfg["uid"])),
      ([(e.get("t"), e.get("key")) for e in B.events(cfg["uid"])][-6:], A.chal_outbox, A.net.status))
run(300, lambda: A.mode == "chal_popup")
check("the run failed for both: pop-up", A.mode == "chal_popup" and "failed" in A.chal_popup["title"].lower(),
      A.chal_popup)
A.render()
pygame.image.save(A.screen, str(tmp / "failed.png"))
run(120)
upd = [m for m in B.inbox if m.get("type") == "challenge_update" and m["challenge"]["id"] == cid]
check("server: status failed", any(m["challenge"]["status"] == "failed" for m in upd) or
      A.challenges.get(cid, {}).get("status") == "failed")
A.chal_close_popup()
A.shutdown()
B.net.stop()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
