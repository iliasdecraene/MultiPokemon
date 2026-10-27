"""Pokémon Platinum (Sinnoh) inside YellowLink, end to end: real ROM, local server, a Yellow friend.

  1. boot Platinum from dev/states/pt_room.dsst: frames advance, both screens in the window (screenshot)
  2. keyboard reaches the game (X button opens the menu: D key), mouse on the lower screen = touch (+ drag)
  3. in-game save -> written to the per-server save folder (and nowhere else; DeSmuME's own .dsv is blanked on close)
  4. on a local server: the Yellow friend sees "Sinnoh" in the trainer list and chat
  5. resume snapshot across a restart; travel Platinum -> Yellow -> Platinum (DeSmuME re-used, region follows)
  6. a second server starts with a blank Platinum save (no leak through DeSmuME's shared battery file)
Run: python dev/e2e/platinum_boot.py   (needs dev/states/platinum.nds, pt_room.dsst, yellow.gb, room.state)
If client/yellowlink/platinum.py isn't there yet, a small stand-in adapter is used (marked in the output).
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, types
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_pt_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")        # our app folder AND DeSmuME's battery folder (Linux)
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  YL_PT_ROM=vanilla (default) | sinnoh: the Sinnoh ROM the app / DS core runs
print("Sinnoh ROM:", ptrom.label())
import pygame
pygame.quit = lambda: None          # several apps share this process (the friend keeps its window)
from yellowlink.config import Config, app_dir
from yellowlink import app as appmod, library, nds_core

PT_ROM = os.path.join(STATES, "platinum.nds")
GB_ROM = os.path.join(STATES, "yellow.gb")
def _free_port():
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


PORT = _free_port()               # other agents/tests may hold fixed ports
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)],
                       cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="pt", YL_SERVER_NAME="Sinnoh test", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


# --- Platinum adapter: the real one if it exists, else a stand-in (publishing needs an in-game position) -----
STUB = appmod.platinum is None or not hasattr(appmod.platinum, "PlatinumMemory")
if STUB:
    class StubPlatinumMemory:
        def __init__(self, bus):
            self.bus = bus

        def in_game(self): return True
        def is_safe(self): return True
        def in_battle(self): return False
        def walking(self): return False
        def map_id(self): return 414
        def position(self): return {"map": 414, "x": 5, "y": 6, "facing": 0, "moving": False, "gender": 0}
        def status_line(self): return "stub adapter"
    appmod.platinum = types.SimpleNamespace(PlatinumMemory=StubPlatinumMemory, fast_continue=None, RomData=None,
                                            PLATINUM_SHA1=library.PLATINUM_SHA1)
print("platinum adapter:", "STAND-IN (client/yellowlink/platinum.py not available yet)" if STUB else "real",
      flush=True)


class FriendCfg(Config):
    @property
    def data_dir(self):
        (self.path.parent / "saves").mkdir(parents=True, exist_ok=True)
        return self.path.parent


def friend(name):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    c = FriendCfg(d / "config.json")
    c.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="pt", rom_path=GB_ROM, seen_help=True,
             games={"yellow": GB_ROM}, current_game="yellow")
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "room.state")
    a = appmod.App(c)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


def run(apps, n, cond=None):
    for _ in range(n):
        for a in apps:
            a.handle_events()
            a.step()
        if cond and cond():
            return True
    return False


def start(cfg, dev_state=None):
    if dev_state:
        os.environ["YL_DEV_LOAD_STATE"] = dev_state
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


def restart(app, travel_to=None):
    """What _main does after app.run() returns (travel = save, close, reopen on the other game)."""
    app.shutdown()
    if travel_to:
        library.Library(app.cfg, app_dir()).set_current(travel_to)
    return appmod.App(app.cfg)


def key(app, k, frames=8):
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode="", scancode=0))
    run([app], frames)
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=k, mod=0, unicode="", scancode=0))
    run([app], 2)


def window_pos(app, x, y):
    """Bottom-screen pixel -> window position."""
    return (int((x + 0.5) * app.scale), int(app.game_y + (192 + y + 0.5) * app.scale))


def find_gsystem(core):
    """The game's System struct (pokeplatinum include/system.h), found from a known touch: touchX/Y/Pressed/Held at
    +0x5C. Dev-test only (nothing in the client relies on it)."""
    import numpy as np

    def scan(tx, ty):
        core.touch(tx, ty)
        core.tick(3)
        h = np.frombuffer(core.read(0x02000000, 0x400000), dtype="<u2")
        ok = (np.abs(h[:-3].astype(int) - tx) <= 2) & (np.abs(h[1:-2].astype(int) - ty) <= 2) & (h[3:] == 1)
        return {0x02000000 + 2 * int(i) for i in np.nonzero(ok)[0]}
    cands = scan(37, 29) & scan(201, 150)
    core.touch_release()
    core.tick(3)
    cands = {a for a in cands if core.r16(a + 6) == 0}
    return (min(cands) - 0x5C) if cands else None


alex = friend("Alex")
cfg = Config()
lib = library.Library(cfg, app_dir())
t0 = time.time()
lib.add_rom(PT_ROM)
lib.add_rom(GB_ROM)
print(f"added both game files in {time.time() - t0:.1f}s", flush=True)
check("Platinum identified as Sinnoh", library.identify(PT_ROM).region == "sinnoh"
      and library.region_name("platinum") == "Sinnoh")
check("ROM copied into roms/", (app_dir() / "roms" / "platinum.nds").exists())
lib.set_current("platinum")
cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{PORT}", room_key="pt", seen_help=True, ds_scale=2)
cfg.save()
# the file the app runs: roms/platinum.nds, or with YL_PT_ROM=sinnoh the Complete Sinnoh copy it makes next to it
dsv = pathlib.Path(nds_core.battery_file_candidates(str(app_dir() / "roms" / (
    "platinum-sinnoh.nds" if ptrom.SINNOH else "platinum.nds")))[0])

# 1. boot -------------------------------------------------------------------------------------------------------
A = start(cfg, os.path.join(STATES, "pt_room.dsst"))
check("Platinum runs in the DS core", A.ds and A.nds is not None and A.pyboy is None and A.game_id == "platinum")
check("App.full for the verified ROM", A.full, (A.full, STUB))
check("window = two screens at scale 2 + sidebar", A.screen.get_size() == (512 + 300, 768) and
      (A.view_w, A.view_h) == (256, 384), A.screen.get_size())
f0 = A.nds.frame_count
run([A, alex], 60)
check("frames advance", A.nds.frame_count - f0 == 60, A.nds.frame_count - f0)
A.render()
pygame.image.save(A.screen, str(tmp / "1_boot.png"))
shot = pygame.surfarray.array3d(A.screen)          # (w, h, 3)
top = shot[0:512, 0:384].reshape(-1, 3)
bottom = shot[0:512, 384:768].reshape(-1, 3)
ntop = len({tuple(p) for p in top[::7]})
nbot = len({tuple(p) for p in bottom[::7]})
check("both screens drawn into the window", ntop > 8 and nbot >= 2, (ntop, nbot))

# 2. input --------------------------------------------------------------------------------------------------------
gs = find_gsystem(A.nds)
check("found the game's touch/keys struct", gs is not None)
if gs:
    held = gs + 0x38                      # heldKeysRaw
    tx, ty, tdown = gs + 0x5C, gs + 0x5E, gs + 0x62
    pos = window_pos(A, 100, 50)
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos))
    run([A], 4)
    got = (A.nds.r16(tx), A.nds.r16(ty), A.nds.r16(tdown))
    check("mouse click on the lower screen = touch", abs(got[0] - 100) <= 2 and abs(got[1] - 50) <= 2 and got[2] == 1,
          got)
    pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=window_pos(A, 180, 120), rel=(1, 1), buttons=(1, 0, 0)))
    run([A], 4)
    got = (A.nds.r16(tx), A.nds.r16(ty), A.nds.r16(tdown))
    check("dragging moves the touch", abs(got[0] - 180) <= 2 and abs(got[1] - 120) <= 2 and got[2] == 1, got)
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=window_pos(A, 180, 120)))
    run([A], 4)
    check("releasing the mouse releases the touch", A.nds.r16(tdown) == 0 and not A.touching)
    # a click on the top screen is not a touch
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(100, A.game_y + 100)))
    run([A], 3)
    check("click on the top screen is no touch", A.nds.r16(tdown) == 0 and not A.touching)
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(100, A.game_y + 100)))
    run([A], 2)
    # quick click (down + up in the same frame) still reaches the game
    p = window_pos(A, 60, 60)
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=p))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=p))
    seen = []
    for _ in range(8):
        A.handle_events(); A.step()
        seen.append(A.nds.r16(tdown))
    check("a quick click is seen by the game", 1 in seen and seen[-1] == 0, seen)

    for k, bit, name in ((pygame.K_c, 0x800, "C = Y"), (pygame.K_q, 0x200, "Q = L"), (pygame.K_w, 0x100, "W = R"),
                         (pygame.K_RETURN, 0x8, "Enter = Start"), (pygame.K_z, 0x2, "Z = B")):
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode="", scancode=0))
        run([A], 3)
        v = A.nds.r32(held)
        pygame.event.post(pygame.event.Event(pygame.KEYUP, key=k, mod=0, unicode="", scancode=0))
        run([A], 40)
        check(f"key {name}", v & bit, hex(v))
    for _ in range(3):                        # close whatever the Y button / Start showed
        key(A, pygame.K_z, 4)
        run([A], 40)
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d, mod=0, unicode="d", scancode=0))
    run([A], 4)
    check("D key = DS X button", A.nds.r32(held) & 0x400, hex(A.nds.r32(held)))
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=pygame.K_d, mod=0, unicode="d", scancode=0))
    run([A], 60)
    A.render(); pygame.image.save(A.screen, str(tmp / "2_menu.png"))

# 3. in-game save (menu is open: SAVE is the third entry) ---------------------------------------------------------
save = A.cfg.save_path
check("save path is the server folder's platinum.sav", save == app_dir() / "saves" / "platinum.sav", save)
for k in (pygame.K_DOWN, pygame.K_DOWN, pygame.K_x, pygame.K_x, pygame.K_x):
    key(A, k, 6)
    run([A], 60)
run([A], 900)          # the first save of a game writes everything: ~1450 DS frames (~24 s) in DeSmuME
A.render(); pygame.image.save(A.screen, str(tmp / "3_saved.png"))
t_save = A.frame
written = run([A], 2400, lambda: A.frame % 60 == 0 and save.exists() and save.read_bytes() == A.nds.export_save()
              and len(set(save.read_bytes())) > 1)
print(f"  save reached the server folder {A.frame - t_save} frames later", flush=True)
key(A, pygame.K_x)                       # close "LUCAS saved the game."
run([A], 60)
data = save.read_bytes() if save.exists() else b""
check("in-game save written to the server folder", len(data) == 512 * 1024 and data.count(0xFF) < len(data) - 1000,
      len(data))
check("written save = the game's battery", data == A.nds.export_save())
stray = [p for p in tmp.rglob("*") if p.suffix in (".sav", ".dsv") and p != save and "backups" not in p.parts
         and p != dsv and "Alex" not in p.parts]
check("no save files anywhere else", not stray, stray)
check("DeSmuME's shared battery file is in its own folder, not in roms/",
      not list((app_dir() / "roms").glob("*.dsv")) and dsv.parent.name == "desmume", dsv)

# 4. multiplayer: the Yellow friend sees Sinnoh -------------------------------------------------------------------
online = run([A, alex], 600, lambda: A.net.status == "online" and alex.net.status == "online" and any(
    e.get("uid") == A.cfg["uid"] and alex.player_region(e) == "Sinnoh" for e in alex.roster))
me = next((e for e in alex.roster if e.get("uid") == A.cfg["uid"]), {})
check("friend sees Ilias in Sinnoh", online and alex.player_region(me) == "Sinnoh", (A.net.status, me))
A.chat_input = "hello from Sinnoh"
A.send_chat()
run([A, alex], 120, lambda: any(m[1] == "hello from Sinnoh" for m in alex.chat_log))
line = next((m for m in alex.chat_log if m[1] == "hello from Sinnoh"), None)
check("chat shows 'Ilias · Sinnoh'", line is not None and line[0] == "Ilias · Sinnoh", line)
check("my own roster line says Sinnoh", A.player_region({"uid": A.cfg["uid"]}) == "Sinnoh")
A.toggle_panel("multiplayer"); A.render(); pygame.image.save(A.screen, str(tmp / "4_multiplayer.png"))
A.close_panel()
A.toggle_panel("regions"); A.render(); pygame.image.save(A.screen, str(tmp / "4_regions.png"))
check("Regions panel has a Travel button for Kanto", any(lab == "Travel" for _, _, lab in A.click_targets))
A.close_panel()

# 5. resume across a restart, travel Platinum -> Yellow -> Platinum ------------------------------------------------
run([A, alex], 30)
fc = A.nds.r32(gs + 0x30) if gs else None          # gSystem.frameCounter
B = restart(A)
check("restart: resumed from the snapshot", B.ds and gs is not None and abs(B.nds.r32(gs + 0x30) - fc) < 10,
      (fc, B.nds.r32(gs + 0x30) if gs else None))
check("restart: save loaded from the server folder", B.nds.loaded_save and B.had_save)
run([B, alex], 60)
fc = B.nds.r32(gs + 0x30) if gs else None
B.travel("yellow")
check("travel requested", B.quit_requested and B.travel_to == "yellow")
B.shutdown()
library.Library(cfg, app_dir()).set_current("yellow")
Y = start(cfg, os.path.join(STATES, "room.state"))
check("Yellow runs after Platinum", Y.game_id == "yellow" and Y.pyboy is not None and Y.nds is None
      and Y.screen.get_size()[0] == 160 * Y.scale + 300)
run([Y, alex], 400, lambda: alex.player_region(next((e for e in alex.roster if e.get("uid") == cfg["uid"]), {}))
    == "Kanto")
check("friend sees Ilias back in Kanto",
      alex.player_region(next((e for e in alex.roster if e.get("uid") == cfg["uid"]), {})) == "Kanto")
Y.travel("platinum")
P = restart(Y, "platinum")
check("Platinum again (same DeSmuME re-used)", P.ds and P.nds is not None and gs is not None
      and abs(P.nds.r32(gs + 0x30) - fc) < 10, (fc, P.nds.r32(gs + 0x30) if gs else None))
run([P, alex], 400, lambda: alex.player_region(next((e for e in alex.roster if e.get("uid") == cfg["uid"]), {}))
    == "Sinnoh")
check("friend sees Ilias in Sinnoh again",
      alex.player_region(next((e for e in alex.roster if e.get("uid") == cfg["uid"]), {})) == "Sinnoh")
P.fast = True
P._sync_ds_audio()
f0 = P.nds.frame_count
run([P], 10)
check("fast-forward: 4 DS frames per host frame, sound muted", P.nds.frame_count - f0 == 40
      and P.nds.emu.volume_get() == 0, (P.nds.frame_count - f0, P.nds.emu.volume_get()))
P.fast = False
P.set_volume(50)
check("volume slider drives DeSmuME's volume", P.nds.emu.volume_get() == 64, P.nds.emu.volume_get())
P.render(); pygame.image.save(P.screen, str(tmp / "5_back_in_sinnoh.png"))

# 6. a second server: own (blank) Platinum save -------------------------------------------------------------------
P.shutdown()
check("DeSmuME's shared .dsv is blanked on close", dsv.exists() and set(dsv.read_bytes()[:512 * 1024]) == {0xFF})
s2 = cfg.add_server("Other group", "ws://127.0.0.1:8792", "x")
cfg.switch_server(s2["id"])
cfg["current_game"] = "platinum"
Q = appmod.App(cfg)
check("second server: own folder", "servers" in str(Q.cfg.save_path), Q.cfg.save_path)
blank = Q.nds.export_save()
check("second server: blank battery (nothing leaked)", not Q.had_save and not Q.nds.loaded_save
      and set(blank) == {0xFF})
Q.shutdown()
cfg.switch_server("main")
R = appmod.App(cfg)
now = R.nds.export_save()
on_disk = save.read_bytes()
check("back on the first server: its save again", R.nds.loaded_save and now == on_disk and
      now.count(0xFF) < len(now) - 1000,
      (R.nds.loaded_save, sum(a != b for a, b in zip(now, on_disk)), sum(a != b for a, b in zip(now, data))))
R.shutdown()
check("DeSmuME's own battery file stayed in this test's folder", str(dsv).startswith(str(tmp)) and dsv.exists(), dsv)
alex.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
