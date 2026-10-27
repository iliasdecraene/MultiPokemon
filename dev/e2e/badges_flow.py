"""Badges e2e (SPEC.md section 9): local server + two real Yellow clients.

  1. Ilias earns the Kanto badges in his main game via RAM edits (Hall of Fame count, a full Kanto dex, a party mon
     with Gen-2 shiny DVs): toasts, his badge book, the server roster; Alex sees them in the Multiplayer panel.
  2. Soul Link: a run with Exp Share All off -> Ilias enters the Hall of Fame -> gold Soul Link badge (server side,
     from the ledger); a run with it on -> Alex enters it -> silver. Main-game badges never come from a run.
  3. A Yellow wild battle with a would-be-shiny enemy shows the "Shiny!" tag.
Screenshots (Regions panel at 3x and 4x, the DS 1.5x layout, Multiplayer) go to the temp dir printed at the end.
Needs dev/states/yellow.gb, pallet_after_lab.state, soullink_route1_grass.state, soullink_wild_battle.state.
Run: python dev/e2e/badges_flow.py
"""
import os, sys, time, json, subprocess, tempfile, pathlib, atexit
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame
pygame.quit = lambda: None          # two apps share this process
from yellowlink.config import Config
from yellowlink import app as appmod, badge_art, badgesync, game, panels

PORT = 8791
ROM = os.path.join(STATES, "yellow.gb")
MAIN_STATE = os.path.join(STATES, "pallet_after_lab.state")
RUN_STATE = os.path.join(STATES, "soullink_route1_grass.state")
WILD_STATE = os.path.join(STATES, "soullink_wild_battle.state")
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


def make_cfg(name):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = Config(d / "config.json")
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=ROM, seen_help=True,
               games={"yellow": ROM}, current_game="yellow", scale=4)
    return cfg


def boot(cfg, state):
    os.environ["YL_DEV_LOAD_STATE"] = state
    app = appmod.App(cfg)
    return app


ca, cb = make_cfg("Ilias"), make_cfg("Alex")
A, B = boot(ca, MAIN_STATE), boot(cb, MAIN_STATE)


def run(n, cond=None):
    for _ in range(n):
        A.step(); B.step(); time.sleep(0.001)
        if cond and cond():
            return True
    return False


def wait(cond, secs=10.0):
    """Step both apps until cond() holds or `secs` of wall time passed (the server's reply can take a moment on a
    busy machine; a frame count alone races it)."""
    end = time.time() + secs
    while time.time() < end:
        if run(10, cond):
            return True
    return bool(cond())


def shot(app, name, scale=None):
    if scale:
        app.cfg[app._scale_key()] = scale
    app._make_window()
    app.render()
    pygame.image.save(app.screen, str(tmp / name))


def entry(app, uid):
    return next((e for e in app.roster if e.get("uid") == uid), {})


run(300, lambda: A.badge_feature and B.badge_feature and len(A.roster) == 2 and len(B.roster) == 2)
check("server announces the badges feature", A.badge_feature and B.badge_feature)
check("nobody has badges yet", entry(B, ca["uid"]).get("badges") == {} and A.badges.earned == {})

# 1. main-game badges by RAM edits (Ilias)
m = A.pyboy.memory
m[game.NUM_HOF_TEAMS] = 1                                    # entered the Hall of Fame once
party = A.game.party()
check("Ilias has a party to make shiny", bool(party))
m[0xD16A + 27], m[0xD16A + 28] = 0xEA, 0xAA                  # first party mon: Gen-2 shiny DVs (Atk 14, 10/10/10)
ok = run(badgesync.CHECK_EVERY * 3, lambda: len((A.badges.earned.get("kanto") or {})) >= 2)
wait(lambda: any("Ilias has" in line[1] for line in B.chat_log))
check("first report after the update: one summary line, not one per badge",
      any(line[1] == "Ilias has 2 badges" for line in B.chat_log)
      and not any("Kanto Champion badge" in line[1] for line in B.chat_log), str(B.chat_log[-5:]))
for i in range(18):
    m[game.DEX_OWNED + i] = 0xFF                             # #1-144 ...
m[game.DEX_OWNED + 18] = 0x3F                                # ... #145-150 (not Mew)
ok = run(badgesync.CHECK_EVERY * 3, lambda: len((A.badges.earned.get("kanto") or {})) >= 3)
k = A.badges.earned.get("kanto") or {}
check("Ilias's book: Champion + Pokédex + 1 shiny", ok and k.get("champion") and k.get("dex")
      and len(k.get("shinies") or []) == 1, str(k))
check("told: Champion, Pokédex, Shiny hunter (toasts + chat)",
      all(any(t in line[1] for line in A.chat_log) for t in ("Kanto Champion!", "Kanto Pokédex!", "Kanto Shiny hunter!"))
      and any("Badge earned" in s[0] for s in A.toasts), str(A.chat_log[-6:]))
book = json.loads(ca.badges_path.read_text())
check("book saved next to the save (per server folder)", "kanto" in book["earned"]
      and str(ca.badges_path).startswith(str(ca.data_dir)))
FULL = {"champion": True, "dex": True, "shiny": 1, "soullink": None}
wait(lambda: (entry(B, ca["uid"]).get("badges") or {}).get("kanto", {}) == FULL)      # the dex arrives in a later report
wait(lambda: any("completed the Kanto Pokédex" in line[1] for line in B.chat_log))
kb = (entry(B, ca["uid"]).get("badges") or {}).get("kanto", {})
check("Alex's roster has Ilias's Kanto badges", kb == {"champion": True, "dex": True, "shiny": 1, "soullink": None},
      str(kb))
check("a badge earned just now is announced", any("completed the Kanto Pokédex" in line[1] for line in B.chat_log),
      str(B.chat_log[-5:]))
check("Alex earned nothing", B.badges.earned == {} and (entry(A, cb["uid"]).get("badges") or {}) == {})

# screenshots: Ilias's Regions panel (Yellow 4x and 3x), the DS 1.5x layout, Alex's Multiplayer panel
A.toggle_panel("regions")
mouse = pygame.mouse.get_pos
for scale in (4, 3, 2):
    shot(A, f"1_regions_{scale}x.png", scale)
    socks = [r for r, _, label in A.click_targets if label.startswith("badge:")]
    here = [r for r, _, label in A.click_targets if label in ("Travel", "you are here")]
    check(f"Regions panel {scale}x: sockets inside the game area, clear of Travel",
          len(socks) == (12 if scale > 2 else len(socks)) and socks
          and all(pygame.Rect(0, A.game_y, A.game_w, A.game_h).contains(r) for r in socks)
          and not any(r.colliderect(t) for r in socks for t in here))
shot(A, "1_regions_3x.png", 3)
# hover over the shiny socket -> tip
rect = next(r for r, _, label in A.click_targets if label == "badge:kanto:shiny")
pygame.mouse.get_pos = lambda: rect.center
shot(A, "2_regions_hover_3x.png")
check("hovering a socket shows its tip", A.badge_tip and "Shiny hunter" in A.badge_tip[0][0][0])
pygame.mouse.get_pos = mouse
ds = pygame.Surface((384, 576))                             # Platinum's window at 1.5x: 256x384 game area
ds.fill((30, 30, 30))
A.click_targets = []
panels.draw_regions(ds, A.fonts, A, 384, 576, (0, 0))
pygame.image.save(ds, str(tmp / "3_regions_ds_1.5x.png"))
check("DS 1.5x layout: 12 sockets inside", len([1 for _, _, l in A.click_targets if l.startswith("badge:")]) == 12)
A.close_panel()

B.toggle_panel("multiplayer")
B.select_player(ca["uid"])
run(100, lambda: ca["uid"] in B.badge_views)
check("get_badges: Alex has Ilias's earn dates", (B.badge_views.get(ca["uid"]) or {}).get("kanto", {}).get("champion"))
for scale in (4, 3, 2):
    shot(B, f"4_multiplayer_{scale}x.png", scale)
    socks = [r for r, _, label in B.click_targets if label.startswith("badge:")]
    panel = next(r for r, _, label in B.click_targets if label == "panel")
    check(f"Multiplayer {scale}x: Ilias's sockets inside the panel (above the footer)",
          len(socks) == (12 if scale > 2 else len(socks)) and socks and all(r.bottom <= panel.bottom - 30 for r in socks))
shot(B, "4_multiplayer_3x.png", 3)
rect = next(r for r, _, label in B.click_targets if label == "badge:kanto:champion")
pygame.mouse.get_pos = lambda: rect.center
shot(B, "5_multiplayer_hover.png")
pygame.mouse.get_pos = mouse
B.close_panel()
B.select_player(ca["uid"])


# 2. Soul Link: gold (Exp Share All off) for Ilias, silver (on) for Alex
def start_run(exp_share):
    global A, B
    A.new_challenge_form(cb["uid"], "Alex")
    A.chal_form["exp_share"] = exp_share
    A.send_challenge()
    wait(lambda: B.prompt and B.prompt.get("kind") == "challenge")
    time.sleep(0.3)
    B.respond_challenge(True)
    wait(lambda: A.enter_challenge and B.enter_challenge)
    for app, cfg in ((A, ca), (B, cb)):
        app.shutdown()
        cfg["active_challenge"] = app.enter_challenge
    A, B = boot(ca, RUN_STATE), boot(cb, RUN_STATE)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    wait(lambda: A.chal_wait is None and B.chal_wait is None and A.referee and B.referee, 30)
    check(f"both in the run (exp_share {'on' if exp_share else 'off'})",
          A.chal_wait is None and B.chal_wait is None and bool(A.referee) and
          bool(A.chal_rec["rules"].get("exp_share")) == exp_share)


def back_home():
    global A, B
    for app, cfg in ((A, ca), (B, cb)):
        app.shutdown()
        cfg.pop("active_challenge", None)
    A, B = boot(ca, MAIN_STATE), boot(cb, MAIN_STATE)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    wait(lambda: A.badge_feature and B.badge_feature and len(A.roster) == 2)


start_run(False)
A.pyboy.memory[game.NUM_HOF_TEAMS] = 1                      # Ilias beats the Champion in the run
ok = run(1500, lambda: (entry(B, ca["uid"]).get("badges") or {}).get("kanto", {}).get("soullink") == "gold")
check("run without Exp Share All: Ilias gets the GOLD Soul Link badge", ok)
run(100, lambda: any("Soul Link (gold)" in line[1] for line in A.chat_log))
check("Ilias is told", any("Soul Link (gold)" in line[1] for line in A.chat_log), str(A.chat_log[-6:]))
check("Alex (no Hall of Fame yet) has none", not (entry(A, cb["uid"]).get("badges") or {}))
check("no main-game check inside the run", A.badges.earned.get("kanto", {}).get("shinies") == k.get("shinies")
      and B.badges.earned == {})
cid1 = A.chal["id"]
A.abandon_challenge(A.challenges[cid1])
run(200, lambda: A.quit_requested or A.mode == "chal_popup")
back_home()

start_run(True)
B.pyboy.memory[game.NUM_HOF_TEAMS] = 1                      # Alex enters the Hall of Fame in the Exp Share run
ok = run(1500, lambda: (entry(A, cb["uid"]).get("badges") or {}).get("kanto", {}).get("soullink") == "silver")
check("run with Exp Share All: Alex gets the SILVER Soul Link badge", ok)
check("Alex's main-game book is still empty (runs never give main-game badges)", B.badges.earned == {})
A.pyboy.memory[game.NUM_HOF_TEAMS] = 1                      # Ilias too: gold outranks silver -> stays gold
run(800, lambda: A.chal_rec and A.chal_rec.get("status") == "completed" or
    (A.challenges.get(A.chal["id"]) or {}).get("status") == "completed")
check("Ilias keeps gold", (entry(B, ca["uid"]).get("badges") or {}).get("kanto", {}).get("soullink") == "gold")
back_home()
check("after restart: Ilias's badges still there (book + server)",
      A.badge_summary(ca["uid"]).get("kanto") == {"champion": True, "dex": True, "shiny": 1, "soullink": "gold"},
      str(A.badge_summary(ca["uid"])))
B.toggle_panel("multiplayer"); B.select_player(ca["uid"]); run(60)
shot(B, "6_multiplayer_after_runs.png", 4)
A.toggle_panel("regions"); shot(A, "7_regions_after_runs.png", 4); A.close_panel()

# 3. wild shiny tag in a Yellow battle
A.pyboy.load_state(open(WILD_STATE, "rb"))
run(400)                                                    # the battle screen with the enemy on it
for _ in range(3):                                          # past "Wild PIDGEY appeared!" -> Pikachu + both HUDs
    A.pyboy.button("a"); run(200)
A.pyboy.memory[game.ENEMY_MON_DVS], A.pyboy.memory[game.ENEMY_MON_DVS + 1] = 0xEA, 0xAA
run(40, lambda: A.wild_shiny)
check("wild shiny flagged in battle", A.wild_shiny)
for scale in (4, 3, 2):
    shot(A, f"8_wild_shiny_{scale}x.png", scale)
    # the tag must stay clear of the player's name/level HUD (Game Boy rows from y 56) and the enemy's name/HP
    top = A.game_y + (A.off_y + appmod.SHINY_TAG_Y) * A.scale
    tag_h = badge_art.SHINY_TAG_H * A.scale
    check(f"'Shiny!' tag {scale}x between the HUDs", (A.off_y + 28) * A.scale <= top - A.game_y
          and top - A.game_y + tag_h <= (A.off_y + 40) * A.scale)
A.pyboy.memory[game.ENEMY_MON_DVS], A.pyboy.memory[game.ENEMY_MON_DVS + 1] = 0x12, 0x34
run(40, lambda: not A.wild_shiny)
check("not shiny -> no tag", not A.wild_shiny)
A.shutdown(); B.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
