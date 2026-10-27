"""Platinum part of the 'Back to my last in-game save' checks (SPEC.md section 5): the game's own soft reset
(L+R+Start+Select) is seen through in_game() going away and coming back, and the PC is lined up after CONTINUE.
Run: python dev/e2e/reload_save_pt.py   (needs dev/states/{platinum.nds,pt_pc.dsst})"""
import os, sys, tempfile, pathlib
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_reload_pt_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame
pygame.quit = lambda: None
from yellowlink.config import Config
from yellowlink import app as appmod, platinum

FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


cfg = Config()
cfg.update(name="Ilias", server_url="ws://127.0.0.1:1", room_key="x", seen_help=True,
           games={"platinum": os.path.join(STATES, "platinum.nds")}, current_game="platinum")
cfg.save()
os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "pt_pc.dsst")
P = appmod.App(cfg)
os.environ.pop("YL_DEV_LOAD_STATE", None)
for _ in range(120):
    P.step()
check("Platinum in game, PC database on", P.game.in_game() and P.pc_codec is not None)
check("dev state: no load from the save pending", not P._battery_due)
loads = []
real = P.after_battery_load
P.after_battery_load = lambda: (loads.append(P.frame), real())
for b in ("l", "r", "start", "select"):
    P.nds.button_press(b)
seen_out = False
for _ in range(240):
    P.step()
    seen_out = seen_out or not P.game.in_game()
for b in ("l", "r", "start", "select"):
    P.nds.button_release(b)
check("soft reset: the game left (in_game False)", seen_out)
check("the app noticed it left", P._left_game)
r = platinum.fast_continue(P.nds)
print("fast continue:", r)
for _ in range(900):
    P.step()
    if loads:
        break
check("CONTINUE after the reset -> after_battery_load ran", bool(loads), r)
check("nothing pending after it", not P._battery_due)
P.shutdown()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
