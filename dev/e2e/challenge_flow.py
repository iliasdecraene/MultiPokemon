"""Challenge flow e2e: two real clients + local server, Pokémon Yellow.

Invite -> accept -> both restart into a fresh challenge save -> both in = game runs -> pause (partner gets the
pop-up and goes home) -> continue from the menu -> close the game (partner told right away) -> main games intact.
Run: python dev/e2e/challenge_flow.py   (needs dev/states/yellow.gb and room.state)
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")        # isolate app_dir() (challenge saves land here)
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame
pygame.quit = lambda: None          # two apps share this process: one closing must not kill the other's window
from yellowlink.config import Config
from yellowlink import app as appmod

PORT = 8794
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []
ROM = os.path.join(STATES, "yellow.gb")


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


def make_cfg(name):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = Config(d / "config.json")
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=ROM, seen_help=True,
               games={"yellow": ROM}, current_game="yellow")
    return cfg


def boot(cfg):
    """Start the app like the real main loop does (main game from a fixed test state)."""
    if cfg.challenge:
        os.environ.pop("YL_DEV_LOAD_STATE", None)
    else:
        os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "room.state")
    return appmod.App(cfg)


def restart(app):
    """What _main does after app.run() returns."""
    app.shutdown()
    cfg = app.cfg
    if app.enter_challenge:
        cfg["active_challenge"] = app.enter_challenge
    elif app.leave_challenge:
        cfg.pop("active_challenge", None)
    return boot(cfg)


ca, cb = make_cfg("Ilias"), make_cfg("Alex")
A, B = boot(ca), boot(cb)


def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.002)
        if cond and cond():
            return True
    return False


def shot(app, name):
    app.render(); pygame.image.save(app.screen, str(tmp / name))


run(200, lambda: any(e.get("games") for e in A.roster if e["uid"] == cb["uid"]))
check("roster knows Alex's games", any(e.get("games") == ["yellow"] for e in A.roster if e["uid"] == cb["uid"]))

# 1. invite from the Challenges panel
A.open_challenges(cb["uid"]); A.render(); shot(A, "1_challenges_panel.png")
check("Start button is offered", any(l == "Start a Soul Link" for _, _, l in A.click_targets))
A.new_challenge_form(cb["uid"], "Alex")
check("form defaults to Yellow", A.chal_form["game"] == "yellow")
A.render(); shot(A, "2_form.png")
A.send_challenge()
run(150, lambda: B.mode == "prompt" and B.prompt and B.prompt["kind"] == "challenge")
check("Alex sees the invite", B.mode == "prompt" and B.prompt["kind"] == "challenge")
shot(B, "3_invite.png")
B.render()
accept = next((cb_ for r, cb_, l in B.click_targets if l == "Accept"), None)
check("invite has an Accept button", accept is not None)
accept()
run(150, lambda: A.enter_challenge and B.enter_challenge)
check("both are sent into the challenge", bool(A.enter_challenge and B.enter_challenge))
cid = A.enter_challenge["id"]

# 2. restart into the challenge: fresh save, paused until both are in
A = restart(A)
check("Ilias is in the challenge with its own save path", A.chal and str(ca.save_path).endswith(f"{cid}/game.sav"))
check("fresh game (no save yet)", not A.had_save)
frame_before = A.pyboy.frame_count
run(60)
check("game paused while waiting for Alex", A.chal_wait == "load" and A.pyboy.frame_count == frame_before)
shot(A, "4_waiting.png")
B = restart(B)
run(200, lambda: A.chal_wait is None and B.chal_wait is None)
check("both in -> game runs", A.chal_wait is None and B.chal_wait is None and A.pyboy.frame_count > frame_before)
check("rules engine loaded", A.sl is not None and B.sl is not None and A.sl.partner == cb["uid"])
run(200, lambda: any(e.get("challenge") == "soul_link" for e in A.roster if e["uid"] == cb["uid"]))
check("roster says they're in a Soul Link", A.player_region({"uid": cb["uid"], "challenge": "soul_link"}) == "Soul Link"
      and any(e.get("challenge") == "soul_link" for e in A.roster if e["uid"] == cb["uid"]))
A.toggle_panel("soullink"); A.render(); shot(A, "5_soullink_panel.png"); A.close_panel()

# 3. Ilias pauses -> Alex gets the pop-up and goes home
A.leave_to_main("paused")
check("pausing sends Ilias home", A.quit_requested and A.leave_challenge)
A = restart(A)
check("Ilias is back in his main game", not A.chal and A.cfg.game_id == "yellow" and str(ca.save_path).endswith("yellow.sav"))
run(150, lambda: B.mode == "chal_popup")
check("Alex gets the 'left' pop-up", B.mode == "chal_popup" and "paused" in str(B.chal_popup["lines"]))
shot(B, "6_partner_left.png")
B.render()
ok_btn = next((cb_ for r, cb_, l in B.click_targets if l == "OK"), None)
ok_btn()
check("OK sends Alex home", B.quit_requested and B.leave_challenge)
B = restart(B)
check("Alex is back in his main game", not B.chal)
check("challenge save exists on disk", (tmp / "appdata" / "yellowlink" / "challenges" / cid / "game.sav").exists()
      or (tmp / "appdata" / "yellowlink" / "challenges" / cid / "resume.state").exists())

# 4. continue together from the Challenges panel
run(200, lambda: cid in B.challenges)
c = B.challenges[cid]
B.open_challenges(ca["uid"]); B.render(); shot(B, "7_continue.png")
check("Continue button shown", any(l == "Continue" for _, _, l in B.click_targets))
B.continue_challenge(c)
run(150, lambda: A.mode == "prompt" and A.prompt and A.prompt.get("continue") == cid)
check("Ilias sees 'continue' invite", A.mode == "prompt" and A.prompt.get("continue") == cid)
A.respond_challenge(True)
run(150, lambda: A.enter_challenge and B.enter_challenge)
A, B = restart(A), restart(B)
run(250, lambda: A.chal_wait is None and B.chal_wait is None)
check("back in the same run", A.chal["id"] == cid and A.chal_wait is None and B.chal_wait is None)

# 5. Alex closes the game -> Ilias told right away (no 30 s wait)
B.shutdown()
t0 = time.time()
for _ in range(400):
    A.step(); time.sleep(0.002)
    if A.mode == "chal_popup":
        break
check("closing the game tells the partner quickly", A.mode == "chal_popup" and time.time() - t0 < 5
      and "closed the game" in str(A.chal_popup["lines"]))
A.chal_close_popup()
A = restart(A)
check("Ilias is home again", not A.chal)
cfg_on_disk = Config(ca.path)
check("no stale active_challenge in config", "active_challenge" not in cfg_on_disk)

# Alex starts YellowLink again: like the real app, he always starts in his main game
cb.pop("active_challenge", None)
B = boot(cb)
check("Alex restarts in his main game", not B.chal)

# 6. abandon
run(100)
c = A.challenges[cid]
A.abandon_challenge(c)
run(100, lambda: A.challenges[cid]["status"] == "abandoned")
check("abandoned", A.challenges[cid]["status"] == "abandoned")
A.open_challenges(cb["uid"]); A.render(); shot(A, "8_after_abandon.png")
A.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
