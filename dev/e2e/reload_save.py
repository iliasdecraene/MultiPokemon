"""'Back to my last in-game save' (SPEC.md section 5) on the real Yellow ROM, with a local server.

  1. Save in-game (Start > SAVE). The button is available; F7 opens the confirmation (screenshots at GB 3x).
  2. Deposit PIDGEY after the save -> the button refuses ("You put Pidgey in your PC after your last save").
  3. Manual soft reset (A+B+Start+Select) + CONTINUE: the save brings PIDGEY back into the party, but it's in the
     YellowLink PC -> taken out of the party again (no clone); the PC still has it exactly once.
  4. Save in-game again; a trade/gift away after the save -> refused; save again -> allowed.
  5. Walk away from the save spot, press the button for real: the resume snapshot goes to backups/, the app restarts
     the game from its in-game save straight into CONTINUE (same restart path as travelling): position and party
     are the save's, the PC store is unchanged, the PC view shows the store, nothing duplicated.
  6. Platinum (DS 1.5x): the Settings button + confirmation screenshots (dev/states/pt_pc.dsst).
Run: python dev/e2e/reload_save.py      (needs dev/states/{yellow.gb,pokecenter_pc.state}; platinum.nds optional)
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_reload_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)
import pygame
pygame.quit = lambda: None
import pc_actions as pa
from yellowlink.config import Config
from yellowlink import app as appmod, reloadsave

GB_ROM = os.path.join(STATES, "yellow.gb")
PT_ROM = os.path.join(STATES, "platinum.nds")
PORT = 8791
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="test", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []
SHOTS = tmp / "shots"
SHOTS.mkdir()


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


cfg = Config()
cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{PORT}", room_key="test", seen_help=True, scale=3,
           ds_scale=1.5, games={"yellow": GB_ROM, "platinum": PT_ROM}, current_game="yellow")
cfg.save()


def start(state=None):
    if state:
        os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)      # let autosave run (it commits the PC store)
    return a


def pump(a, n=60, cond=None):
    for _ in range(n):
        a.handle_events()
        a.step()
        if cond and cond():
            return True
    return False


def shot(a, name):
    a.click_targets = []
    a.render()
    pygame.image.save(a.screen, str(SHOTS / name))


def save_ingame(pb):
    """Start > SAVE > YES (and YES to 'the older file will be erased'), back to the overworld."""
    for _ in range(10):
        pa._press(pb, "start", hold=6, gap=30)
        if pa._has(pb, "SAVE", "EXIT"):
            break
    pa._settle(pb, 6)
    rows = pa.screen_lines(pb)
    pa._move_cursor(pb, (next(i for i, r in enumerate(rows) if "SAVE" in r) - 2) // 2)
    for _ in range(20):                    # the first A on a freshly moved cursor is sometimes ignored
        pa._press(pb, "a", hold=6, gap=40)
        if pa._has(pb, "SAVE the game"):
            break
    answered = 0
    for _ in range(3000):
        s = pa.screen_text(pb)
        if pa._has(pb, "YES", "NO") and answered < 2 and ("SAVE the game" in s or "erased" in s or "older" in s):
            pa._settle(pb, 6)
            pa._move_cursor(pb, 0)
            pa._press(pb, "a", hold=6, gap=40)
            answered += 1
            continue
        if "saved" in s:
            break
        pb.tick()
    pa._advance_until(pb, pa._overworld_free, what="overworld after saving")
    pa._tick(pb, 20)


def soft_reset(a):
    """A+B+Start+Select through the app's own frames, then CONTINUE like a player (A until the CONTINUE card is
    confirmed). Returns (the game restarted, the CONTINUE hook fired)."""
    pb = a.pyboy
    restarted = []
    pb.hook_register(0x00, 0x1D05, lambda _c: restarted.append(1), None)       # SoftReset (pret)
    for b in ("a", "b", "start", "select"):
        pb.button_press(b)
    pump(a, 60)
    for b in ("a", "b", "start", "select"):
        pb.button_release(b)
    pb.hook_deregister(0x00, 0x1D05)
    a._battery_due = False
    loads = []
    real = a.after_battery_load
    a.after_battery_load = lambda: (loads.append(1), real())
    for i in range(200):                    # intro, title, main menu (CONTINUE first), the CONTINUE card
        if a._battery_due or loads:
            break
        pb.button_press("a")
        pump(a, 4)
        pb.button_release("a")
        pump(a, 26)
    hooked = a._battery_due or bool(loads)
    pump(a, 600, lambda: not a._battery_due)
    del a.after_battery_load
    face_pc(a)
    return bool(restarted), hooked


def face_pc(a):
    """CONTINUE faces the player down: turn back to the PC (a wall: no step)."""
    for _ in range(20):
        if a.game.position()["facing"] == 4:
            break
        a.pyboy.button_press("up")
        pump(a, 6)
        a.pyboy.button_release("up")
        pump(a, 20)
    print("facing up:", a.game.position())


def store(a):
    return sorted(e["nick"] for b in a.pcsync.working["boxes"] for e in b)


def names(party):
    return [n for _, n in party]


# ------------------------------------------------------------------------------------------------- 1. the save
A = start("pokecenter_pc.state")
pb = A.pyboy
pump(A, 120)
check("Yellow runs with the PC database", A.full and A.pc_codec is not None)
check("the button is shown in the main game", A.reload_available())
save_ingame(pb)
pump(A, 30)
A.flush_sram()
check("in-game save found in the battery", reloadsave.has_save("yellow", A._read_sram()))
check("button usable right after saving", A.reload_blocker() is None, A.reload_blocker())
party_at_save = pa.party(pb)
print("party at the save:", party_at_save)
A.toggle_panel("settings")
shot(A, "gb3x_settings.png")
A.close_panel()
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F7))
check("F7 opens the confirmation", A.mode == "reload_confirm")
pump(A, 5)
shot(A, "gb3x_confirm.png")
A._reload_shown = 0
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
check("Esc cancels (nothing happens)", A.mode == "play" and not A.quit_requested and not A.reload_save)

# ------------------------------------------------------------------------------------------------- 2. deposit
pa.open_pc(pb)
check("deposit PIDGEY", pa.deposit(pb, 1))
pa.close_pc(pb)
check("autosave commits the deposit", A.autosave(force=True) and not A.pcsync.pending)
pump(A, 120, lambda: A.pcsync.uploading is None)
why = A.reload_blocker()
check("button refuses after a deposit since the save", why and "Pidgey" in why, why)
A.ask_reload()
check("... and doesn't open the confirmation", A.mode == "play")
shot(A, "gb3x_refused.png")
store_before = store(A)
party_after_deposit = pa.party(pb)
print("store:", store_before, "party:", party_after_deposit)

# ------------------------------------------------------------------------------------------------- 3. soft reset
left, hooked = soft_reset(A)
check("soft reset restarted the game; the CONTINUE hook saw the save load", left and hooked, (left, hooked))
check("the app saw the game load its save and lined the PC up", not A._battery_due)
print("party after soft reset:", pa.party(pb))
check("PIDGEY (in the YellowLink PC since) was taken out of the party again: no clone",
      names(pa.party(pb)) == names(party_after_deposit) and "PIDGEY" not in names(pa.party(pb)),
      pa.party(pb))
check("PC store unchanged by the soft reset", store(A) == store_before, store(A))
print("after reset at", A.game.position(), "mode", A.mode, "safe", A.game.is_safe())
A.pyboy.screen.image.save(str(SHOTS / "debug_after_reset.png"))
pa.open_pc(pb)
check("PC shows PIDGEY exactly once", names(pa.box(pb)).count("PIDGEY") == 1, pa.box(pb))
pa.close_pc(pb)
check("look-only visit after the reset changes nothing", store(A) == store_before and
      not A.pcsync.since_save("yellow")["out"], store(A))
check("autosave after the reset", A.autosave(force=True))

# ------------------------------------------------------------------------------------------------- 4. trades
save_ingame(pb)
pump(A, 30)
check("button usable after saving again", A.reload_blocker() is None, A.reload_blocker())
pid = next(e for b in A.pcsync.committed["boxes"] for e in b if e["nick"] == "PIDGEY")
A.pcsync.apply_trade({"trade_id": "gift-e2e", "remove": [pid["id"]], "add": [], "srv_rev": A.pcsync.server_rev})
why = A.reload_blocker()
check("a gift/trade away since the save -> refused", why and "gave away Pidgey" in why, why)
A.pc_push()
pump(A, 120, lambda: A.pcsync.uploading is None)
pa.open_pc(pb)
check("deposit RATTATA (so the PC has something at the button test)", pa.deposit(pb, 1))
pa.close_pc(pb)
check("autosave commits it", A.autosave(force=True) and not A.pcsync.pending)
save_ingame(pb)
pump(A, 30)
check("saving in-game clears it", A.reload_blocker() is None, A.reload_blocker())
store_saved = store(A)
party_saved = pa.party(pb)
def where(a):
    p = a.game.position()
    return a.game.map_id(), p["x"], p["y"]


pos_saved = where(A)
print("saved at", pos_saved, "party", party_saved, "store", store_saved)

# ------------------------------------------------------------------------------------------------- 5. the button
for d in ("down", "down", "left"):
    pb.button_press(d)
    pump(A, 18)
    pb.button_release(d)
    pump(A, 20)
pos_now = where(A)
check("walked away from the save spot", pos_now != pos_saved, pos_now)
check("autosave (resume snapshot) written", A.autosave(force=True) and cfg.resume_path.exists())
A.ask_reload()
check("confirmation open", A.mode == "reload_confirm")
pump(A, 3)
A.confirm_reload(True)
check("too quick after opening: the save must be stable for a moment", not A.reload_save)
A.ask_reload()
pump(A, reloadsave.STABLE_FRAMES + 12)
A._reload_shown = 0
A.on_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
check("Enter confirms: restart requested", A.reload_save and A.quit_requested)
check("the main loop stops", A.handle_events() is False)
A.shutdown()
backups = sorted(cfg.backup_dir.glob("yellow-resume-*.state"))
check("resume snapshot moved to backups/", not cfg.resume_path.exists() and len(backups) == 1, backups)
check("restart flag for the next start", cfg.get("_reload_boot") == "yellow")

B = start()                                  # what _main does next (continue -> App(cfg))
pbB = B.pyboy
check("restarted from the battery (no resume)", not B.resumed and B.reload_boot)
pump(B, 400, lambda: not B._battery_due)
check("straight into the game (fast CONTINUE)", B.game.in_game())
check("flag cleared once loaded", cfg.get("_reload_boot") is None)
check("position is the save's", where(B) == pos_saved, (where(B), pos_saved))
check("party is the save's", pa.party(pbB) == party_saved, pa.party(pbB))
check("PC store unchanged", store(B) == store_saved, store(B))
face_pc(B)
pa.open_pc(pbB)
view = names(pa.box(pbB))
check("PC view = the store, each once", view == ["RATTATA"], view)
check("PIDGEY (given away) is not back", "PIDGEY" not in view)
pa.close_pc(pbB)
check("look-only visit: nothing changed", store(B) == store_saved == ["RATTATA"], store(B))
B.toggle_panel("settings")
shot(B, "gb3x_settings_after.png")
B.close_panel()
B.shutdown()

# ------------------------------------------------------------------------------------------------- 6. Platinum
if os.path.exists(PT_ROM) and os.path.exists(os.path.join(STATES, "pt_pc.dsst")):
    cfg["current_game"] = "platinum"
    cfg.save()
    P = start("pt_pc.dsst")
    pump(P, 120)
    check("Platinum: button shown", P.ds and P.reload_available())
    print("Platinum blocker:", P.reload_blocker())
    P.toggle_panel("settings")
    shot(P, "ds15_settings.png")
    P.close_panel()
    P.ask_reload()
    if P.mode != "reload_confirm":           # a dev state may have no in-game save: show the dialog anyway
        P.mode = "reload_confirm"
    pump(P, 5)
    shot(P, "ds15_confirm.png")
    P.mode = "play"
    P.shutdown()
else:
    print("(Platinum skipped: no platinum.nds / pt_pc.dsst)")

srv.terminate()
print("\nscreenshots in", SHOTS)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
