"""Raids e2e (SPEC.md section 12): the server (in this process, so the test can open raids and force the catch
rolls) + real YellowLink clients on the real ROMs, through raid_gb.py / raid_pt.py.

  S1 Kanto, mixed: Ilias + Alex (Yellow) and Max (Crystal) online. A Mew raid on the square below Ilias/Alex:
     notice (toast + horn + chat line) for all, "travel there" for Max; den (raid_gb), blocked step, A -> join
     dialog; both join (2 bars each), forced snapshot before, fast-forward off, no snapshot while fighting, trades
     refused (busy). The fights run on the move timer alone (auto_pick; Ilias's first turn on the real 10 s, ring
     screenshot); the pool hits 0; raid_down with chance = damage share + the server's roll forced per player:
     Ilias success, Alex miss; nobody throws by hand: the timer's auto_pick throws (AFK catch phase); Mew in
     Ilias's party, Alex's boss flees; chat lines.
  S2 Johto: Max (Crystal) alone, Celebi: forced success, AFK throw, Celebi in the party; Crystal battle screenshot.
  S3 Kanto: Ilias with a weak team blacks out (out of the raid, can't rejoin); Alex idles; the raid's time runs
     out -> Alex's boss flees (raid_end "time").
  S4 Sinnoh: Dawn (Platinum, the ROM dev/platinum/ptrom.py picks: YL_PT_ROM=sinnoh|vanilla) joins a Mew raid at
     a den next to her, fights on the timer, catch phase AFK throw (touch), forced success on Complete Sinnoh /
     forced miss on vanilla; Platinum battle screenshot at 1.5x.

    YL_PT_ROM=sinnoh python dev/e2e/raid_flow.py            # S1-S4 (Platinum = Complete Sinnoh)
    YL_PT_ROM=vanilla python dev/e2e/raid_flow.py --pt-only # S4 on plain Platinum (forced miss)
Needs dev/states/{yellow.gb, crystal.gbc, platinum.nds (+ platinum-sinnoh.nds for sinnoh), pallet_after_lab.state,
crystal_sl_route29.state, pt_sl_route202.dsst}. Screenshots: $YL_RAID_SHOTS (default: a temp folder, printed).
Everything else goes into a temp folder that is deleted at the end (disk space).
"""
import asyncio
import logging
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
PT_ONLY = "--pt-only" in sys.argv
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_raid_"))
shots = pathlib.Path(os.environ.get("YL_RAID_SHOTS") or tempfile.mkdtemp(prefix="yl_raid_shots_"))
shots.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "server"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  (YL_PT_ROM -> which Platinum ROM)
PT_ROM, PT_LABEL, PT_SINNOH = ptrom.ROM, ptrom.label(), ptrom.SINNOH
os.environ["YL_PT_ROM"] = "vanilla"     # the App runs PT_ROM as given (no Complete Sinnoh copy in the temp folder)
import pygame  # noqa: E402
pygame.quit = lambda: None              # several apps share this process
from yellowlink.config import Config  # noqa: E402
from yellowlink import app as appmod, crystal, game, raid, raid_pt  # noqa: E402
from yellowlink import platinum as P  # noqa: E402
from yellowlink_server.config import Config as ServerConfig  # noqa: E402
from yellowlink_server.server import YellowLinkServer  # noqa: E402

PORT = 8798
FAILS = []
HORNS = {}
APPS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)
    return ok


def counted_horn(self):
    HORNS[self.cfg["name"]] = HORNS.get(self.cfg["name"], 0) + 1


appmod.App.play_raid_horn = counted_horn

# ------------------------------------------------------------------------------------------------ the server
_slog = logging.FileHandler(tmp / "srv.log")
_slog.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s"))
for _n in ("yellowlink.server", "yellowlink.raids"):
    logging.getLogger(_n).addHandler(_slog)
    logging.getLogger(_n).setLevel(logging.INFO)
loop = asyncio.new_event_loop()
srv = YellowLinkServer(ServerConfig(host="127.0.0.1", port=PORT, key="test", data_dir=str(tmp / "data")),
                       store_debounce=0.2)
hub = srv.raids
hub.last_slot = 10 ** 12                # no scheduled raids: this test opens them; the scheduler still ends them
hub.spots = {}


def _serve():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(srv.start())
    loop.run_forever()


threading.Thread(target=_serve, daemon=True).start()


def on_server(coro, timeout=10):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)


def cleanup():
    for a in APPS:
        try:
            a.shutdown()
        except Exception:  # noqa: BLE001
            pass
    try:
        on_server(srv.stop())
    except Exception:  # noqa: BLE001
        pass
    if FAILS:
        try:
            print("--- server log (last lines) ---\n" + "".join(open(tmp / "srv.log").readlines()[-30:]))
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ clients
class Cfg(Config):
    def __init__(self, d, name, rom, gid):
        d.mkdir(parents=True, exist_ok=True)
        super().__init__(d / "config.json")
        self.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=rom, seen_help=True,
                    games={gid: rom}, current_game=gid, auto_resume=True, scale=3, ds_scale=1.5)


def make(name, rom, gid, state):
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(Cfg(tmp / name, name, rom if os.path.isabs(rom) else os.path.join(STATES, rom), gid))
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    a.driver = Driver(a)
    APPS.append(a)
    return a


def drop(a):
    a.shutdown()
    APPS.remove(a)


class Driver:
    """The test player: taps through battle texts (GB: B; DS: A, and 'No' to the nickname), never picks a move or
    a ball - the move timer's auto_pick does that (AFK)."""

    def __init__(self, app):
        self.app = app
        self.touch_until = self.key_until = None
        self.next = 0
        self.buffed = None

    def step(self):
        a = self.app
        b = a.raid_battle
        if self.touch_until is not None and a.nds.frame_count >= self.touch_until:
            a.nds.touch_release()
            self.touch_until = None
        if self.key_until is not None and a.nds.frame_count >= self.key_until:
            a.nds.button_release("a")
            self.key_until = None
        if b is not None:
            self.last = b
        elif a.ds and getattr(self, "last", None) is not None and self.last.throws and not a.game.is_safe():
            b = self.last                                # after the catch: Pokédex page, nickname -> No, texts
        else:
            return
        if a.ds:
            if getattr(b, "phase", "") in ("fight", "down") and self.buffed is not b and b._ctx():
                self.buffed = b
                buff_pt_battle(a, b)
            fr = a.nds.frame_count
            if b.throws and b.decision is None and fr >= self.next and not a.game.is_safe() \
                    and self.touch_until is None and self.key_until is None:
                self.next = fr + 80
                raw = a.nds.screen_rgb()
                x, y = 30, 192 + 140
                r, _g, bl = raw[3 * (y * 256 + x):3 * (y * 256 + x) + 3]
                if bl > 150 and r < 120:                 # "Give a nickname?" Yes/No: touch No
                    a.nds.touch(128, 140)
                    self.touch_until = a.nds.frame_count + 6
                else:
                    a.nds.button_press("a")              # texts, the Pokédex page
                    self.key_until = a.nds.frame_count + 6
        elif b.decision is None and not getattr(b, "auto", False) and b.frame % 30 == 0:
            a.pyboy.button("b", 3)                       # texts (A and B both go on; B = no nickname)


def run(n, cond=None, apps=None):
    for _ in range(n):
        for a in (apps or APPS):
            a.step()
            a.driver.step()
        if cond and cond():
            return True
    return False


def wait(secs, cond, apps=None):
    end = time.time() + secs
    while time.time() < end:
        if run(5, cond, apps):
            return True
    return False


def shot(app, name, scale=None):
    if scale:
        app.cfg[app._scale_key()] = scale
        app._make_window()
    app.render()
    pygame.image.save(app.screen, str(shots / name))


def lines(app):
    return [m for _, m, _ in app.chat_log]


def buff_gb(app):
    """Test help: the party at Lv100 with 999 everything (each hit takes a whole bar); Yellow also 5 Poké Balls."""
    if app.game_id == "yellow":
        m = app.pyboy.memory
        for i in range(m[game.PARTY_COUNT]):
            a = game.PARTY_MONS + i * 44
            m[a + 3] = m[a + 33] = 100
            for off in (1, 34, 36, 38, 40, 42):
                m[a + off], m[a + off + 1] = 0x03, 0xE7
        m[game.NUM_BAG_ITEMS], m[game.BAG_ITEMS], m[game.BAG_ITEMS + 1], m[game.BAG_ITEMS + 2] = 1, 4, 5, 0xFF
    else:
        n = app.game.r(crystal.PARTY_COUNT)
        for i in range(n):
            a = crystal.PARTY_MONS + i * 48
            app.game.write(a + 31, bytes([100]))
            app.game.write(a + 34, bytes([0x03, 0xE7] * 7))      # HP, max HP, 5 stats


def give_yellow_balls(app):
    m = app.pyboy.memory
    m[game.NUM_BAG_ITEMS], m[game.BAG_ITEMS], m[game.BAG_ITEMS + 1], m[game.BAG_ITEMS + 2] = 1, 4, 5, 0xFF


def buff_pt_battle(app, b):
    """Test help: the player's battle copy hits hard and takes a lot (a multi-bar pool in a few turns)."""
    ctx = b._ctx()
    base = ctx + raid_pt.CTX_BATTLE_MONS
    d = app.nds
    for off in (2, 4, 6, 8, 0xA):                       # attack, defense, speed, sp. attack, sp. defense
        d.w16(base + off, 600)
    # (not the HP: the battle copy's HP goes back into the party record, and HP > max HP there would make the
    # party unreadable for YellowLink - party_checked() refuses implausible records)


def den_spot(app, dx, dy):
    p = app.game.position()
    return [p["map"], p["x"] + dx, p["y"] + dy]


def spawn(region, species, spot, name, shiny=False):
    hub.spots = {region: {"names": {str(spot[0]): name}, "spots": [list(spot) + [0]]}}
    r = on_server(hub.spawn(region=region, species=species, shiny=shiny))
    return r


def join(app, d):
    """Bump into the den (the step is dropped), then A -> the dialog -> Join."""
    app.press(d, True)
    run(30, apps=[app])
    app.press(d, False)
    run(10, apps=[app])
    app.press("a", True)
    app.press("a", False)
    c = app.raid_confirm
    ok = app.mode == "raid_confirm" and bool(c and c["ok"])
    if ok:
        c["shown"] = 0
        app.raid_answer(True)
    return ok, c


def fight(apps, secs, first_turn_ring=None):
    """Run until every raid battle of `apps` is over; the timer is 3 s (the first turn of `first_turn_ring` keeps
    the real 10 s and is photographed)."""
    end = time.time() + secs
    ring = False
    for a in apps:
        if a is not first_turn_ring:
            a.raid_timer.secs = 3.0
    while time.time() < end and any(a.raid_battle is not None or a.raid_phase in ("joining", "starting")
                                    for a in apps):
        run(1)
        f = first_turn_ring
        if f is not None and not ring and f.raid_timer.running and f.raid_timer.left(time.time()) < 7:
            ring = True
            yield ("ring", f)
            f.raid_timer.secs = 3.0
    yield ("done", None)


def party_dex(app):
    if app.ds:
        return [i["dex"] for i in (P.mon_info(r) for r in (app.game.party_checked() or [])) if i]
    if app.game_id == "yellow":
        return [app.rom.dex_no(m.species) for m in app.game.party()]
    return [m.species for m in app.game.party()]


def scenario_kanto():
    print("== S1 Kanto: Ilias + Alex (Yellow) raid together, Max (Crystal) online elsewhere ==", flush=True)
    A, B, C = APPS
    spot = den_spot(A, 0, 1)                            # below them (Oak's lab door is above)
    buff_gb(A)
    buff_gb(B)
    buff_gb(C)
    run(5)
    spawn("kanto", 151, spot, "Pallet Town")
    ok = wait(10, lambda: all(a.raid is not None for a in (A, B, C)))
    check("the raid reached all three", ok)
    want = "RAID! Mew appeared in Pallet Town (Kanto) - 15 minutes!"
    wait(5, lambda: all(want in lines(a) for a in (A, B, C)))
    check("everyone gets the chat line", all(want in lines(a) for a in (A, B, C)), str(lines(C)[-3:]))
    check("toast + horn for everyone", HORNS == {"Ilias": 1, "Alex": 1, "Max": 1}, str(HORNS))
    check("Max (Crystal) is told to travel there, no den in his game",
          any("Travel there to join." in t[0] for t in C.toasts) and C.raid_den is None)
    check("Ilias's game has the den", A.raid_den is not None)
    shot(A, "1_notice_card_den_yellow_3x.png", 3)
    shot(C, "1b_notice_card_crystal_3x.png", 3)
    A.toasts.clear()
    shot(A, "2_card_den_yellow_4x.png", 4)
    y0 = A.game.position()["y"]
    A.press("down", True)
    run(40, apps=[A])
    A.press("down", False)
    run(20, apps=[A])
    check("walking onto the den is blocked", A.game.position()["y"] == y0, str(A.game.position()))
    A.press("a", True)
    A.press("a", False)
    check("A after bumping into the den: the join dialog (can join)",
          A.mode == "raid_confirm" and A.raid_confirm and A.raid_confirm["ok"], str(A.raid_confirm))
    shot(A, "3_join_dialog_yellow_3x.png", 3)
    A.raid_confirm["shown"] = 0
    A.raid_answer(True)
    ok_b, cb = join(B, "down")
    check("Alex joins too", ok_b, str(cb))
    A.fast = True
    snap0 = A.cfg.resume_path.stat().st_mtime if A.cfg.resume_path.exists() else 0
    ok = wait(30, lambda: A.raid_fighting() and B.raid_fighting())
    check("both raid battles started", ok, f"{A.raid_phase} {B.raid_phase}")
    wait(3, lambda: A.raid and A.raid.get("pool_max") == 4000)
    check("pool = 2 bars per player", A.raid and A.raid.get("pool_max") == 4000)
    check("a resume snapshot right before the battle", A.cfg.resume_path.exists()
          and A.cfg.resume_path.stat().st_mtime > snap0)
    check("fast-forward went off", A.fast is False)
    check("no resume snapshot while the raid battle runs", A.autosave(force=True) is False)
    C.net.send({"type": "trade_request", "to": A.cfg["uid"]})
    wait(5, lambda: any("busy" in m for m in lines(C)))
    check("a trade request to a raiding player is refused (busy)", any("busy" in m for m in lines(C)),
          str(lines(C)[-2:]))
    hub.catch_force = {A.cfg["uid"]: True, B.cfg["uid"]: False}
    for what, who in fight([A, B], 600, first_turn_ring=A):
        if what == "ring":
            shot(who, "4_timer_ring_card_yellow_3x.png", 3)
    check("both raid battles ended", A.raid_battle is None and B.raid_battle is None, f"{A.raid_phase} {B.raid_phase}")
    wait(10, lambda: A.raid and A.raid.get("phase") == "over" and "Raid over: Ilias caught Mew!" in lines(C))
    parts = {p["name"]: p for p in (A.raid or {}).get("participants", [])}
    total = sum(p["dealt"] for p in parts.values())
    check("the raid ended; the damage covers the whole pool", (A.raid or {}).get("phase") == "over" and total >= 4000,
          str(parts))
    for app, want in ((A, "caught"), (B, "missed")):
        n = app.cfg["name"]
        d = app.raid_down_info or {}
        check(f"{n}: raid_down chance = damage share ({d.get('chance', -1):.2f})",
              abs(d.get("chance", -1) - parts[n]["dealt"] / max(1, total)) < 0.01)
        check(f"{n}: the timer's auto_pick threw the ball; the forced roll -> {want}", parts[n]["state"] == want,
              str(parts[n]))
    wait(10, lambda: A.game.is_safe())
    check("Mew is in Ilias's party", 151 in party_dex(A), str(party_dex(A)))
    check("Alex's boss fled (no Mew)", 151 not in party_dex(B))
    check("chat lines: 'Ilias caught Mew!' + 'Raid over: Ilias caught Mew!'",
          "Ilias caught Mew!" in lines(C) and "Raid over: Ilias caught Mew!" in lines(C), str(lines(C)[-4:]))
    check("snapshots again after the raid battle", A.autosave(force=True) is True)
    shot(A, "5_card_over_yellow_3x.png", 3)


def scenario_johto():
    print("== S2 Johto: Max (Crystal) alone vs Celebi ==", flush=True)
    A, B, C = APPS
    hub.catch_force = {C.cfg["uid"]: True}
    spawn("johto", 251, den_spot(C, -1, 0), "Route 29")
    check("Johto raid reached Max (den in his game)", wait(10, lambda: C.raid and C.raid.get("species") == 251
                                                           and C.raid_den is not None))
    ok, c = join(C, "left")
    check("Max joins at the den", ok, str(c))
    check("Crystal raid battle started", wait(30, lambda: C.raid_fighting()), C.raid_phase)
    for what, who in fight([C], 600, first_turn_ring=C):
        if what == "ring":
            shot(who, "6_timer_ring_card_crystal_3x.png", 3)
    wait(10, lambda: C.raid and C.raid.get("phase") == "over")
    me = raid.participant(C.raid, C.cfg["uid"]) or {}
    check("Max caught Celebi (solo: chance 1.0, AFK throw)", me.get("state") == "caught"
          and (C.raid_down_info or {}).get("chance") == 1.0, str(me))
    wait(10, lambda: C.game.is_safe())
    check("Celebi is in Max's party", 251 in party_dex(C), str(party_dex(C)))


def scenario_blackout_time():
    print("== S3 Kanto: Ilias blacks out, Alex idles until the time is up ==", flush=True)
    A, B, C = APPS
    A.pyboy.load_state(open(os.path.join(STATES, "pallet_after_lab.state"), "rb"))     # a weak team again
    give_yellow_balls(A)
    run(30)
    hub.catch_force = None
    spawn("kanto", 150, den_spot(A, 0, 1), "Pallet Town")
    wait(10, lambda: A.raid and A.raid.get("species") == 150 and B.raid and B.raid.get("species") == 150)
    ok_a, ca = join(A, "down")
    ok_b, cb = join(B, "down")
    check("both join the Mewtwo raid", ok_a and ok_b, f"{ca} {cb}")
    wait(30, lambda: A.raid_fighting() and B.raid_fighting())
    B.raid_timer.secs = 1e9                             # Alex is away from the keyboard: no move
    A.raid_timer.secs = 3.0
    end = time.time() + 300
    while time.time() < end and A.raid_battle is not None:
        run(1)
    me = raid.participant(A.raid, A.cfg["uid"]) or {}
    wait(5, lambda: (raid.participant(A.raid, A.cfg["uid"]) or {}).get("state") == "blackout")
    me = raid.participant(A.raid, A.cfg["uid"]) or {}
    check("Ilias blacked out -> out of the raid", me.get("state") == "blackout" and A.raid_result == "blackout",
          f"{me} {A.raid_result}")
    check("Ilias can't rejoin", A.raid_join_check() == (False, "You're out of this raid."), str(A.raid_join_check()))
    hub.current["closes"] = time.time() + 2             # (instead of waiting 15 minutes)
    wait(15, lambda: B.raid and B.raid.get("phase") == "over" and "Mewtwo fled - the raid is over." in lines(B))
    check("time over: raid_end reason time, chat line", "Mewtwo fled - the raid is over." in lines(B),
          str(lines(B)[-3:]))
    B.raid_timer.secs = 3.0                             # back at the keyboard: the turn goes on, the boss teleports
    B.raid_timer.input_done()
    B.raid_timer.need_input(time.time())
    end = time.time() + 120
    while time.time() < end and B.raid_battle is not None:
        run(1)
    check("Alex's boss fled (flee)", B.raid_battle is None and B.raid_result == "fled", str(B.raid_result))


def scenario_platinum(dawn):
    success = PT_SINNOH
    print(f"== S4 Sinnoh: Dawn on {PT_LABEL}, forced {'success' if success else 'miss'} ==", flush=True)
    D = dawn
    hub.catch_force = {D.cfg["uid"]: success}
    run(30, apps=[D])
    spawn("sinnoh", 151, den_spot(D, 0, -1), "Route 202")
    check("Sinnoh raid reached Dawn (den in her game)", wait(15, lambda: D.raid and D.raid_den is not None))
    run(20, apps=[D])
    shot(D, "7_notice_card_den_platinum_1.5x.png", 1.5)
    ok, c = join(D, "up")
    check("Dawn joins at the den", ok, str(c))
    check("Platinum raid battle started", wait(60, lambda: D.raid_fighting()), D.raid_phase)
    pre = party_dex(D)
    for what, who in fight([D], 900, first_turn_ring=D):
        if what == "ring":
            shot(who, "8_timer_ring_card_platinum_1.5x.png", 1.5)
    wait(10, lambda: D.raid and D.raid.get("phase") == "over")
    me = raid.participant(D.raid, D.cfg["uid"]) or {}
    want = "caught" if success else "missed"
    check(f"Dawn: AFK throw by the timer, forced roll -> {want}", me.get("state") == want, str(me))
    wait(30, lambda: D.game.is_safe() and D.game.party_checked() is not None
         and len(party_dex(D)) >= len(pre), apps=[D])            # (the party is read whole, not mid-update)
    after = party_dex(D)
    check("Mew in Dawn's party" if success else "no Mew in Dawn's party (it fled)",
          (151 in after) == success and (len(after) == len(pre) + (1 if success else 0)),
          f"{pre} -> {after} safe={D.game.is_safe()} party={D.game.party_checked() is not None} "
          f"notes={[n for _f, n in getattr(D.driver.last, 'log', [])][-6:] if getattr(D.driver, 'last', None) else None}")
    if FAILS:
        shot(D, "debug_platinum_end.png", 1.5)


def main():
    if not PT_ONLY:
        make("Ilias", "yellow.gb", "yellow", "pallet_after_lab.state")
        make("Alex", "yellow.gb", "yellow", "pallet_after_lab.state")
        make("Max", "crystal.gbc", "crystal", "crystal_sl_route29.state")
        ok = wait(30, lambda: all("raids" in getattr(a, "server_features", ()) and len(a.roster) == 3 for a in APPS))
        check("three clients online; the server lists the raids feature", ok)
        scenario_kanto()
        scenario_johto()
        scenario_blackout_time()
        drop(APPS[2])                                   # Max leaves (one DS emulator per process: Dawn next)
    D = make("Dawn", PT_ROM, "platinum", "pt_sl_route202.dsst")
    check(f"Dawn online in {PT_LABEL}", wait(30, lambda: "raids" in getattr(D, "server_features", ())
                                              and D.net.status == "online", apps=[D]) and D.full)
    scenario_platinum(D)


try:
    main()
except Exception:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    FAILS.append("crashed")
print("\nscreens in", shots)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
cleanup()
os._exit(1 if FAILS else 0)
