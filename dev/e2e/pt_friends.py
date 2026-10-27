"""Friends walking around in Sinnoh (Pokémon Platinum) - end to end on the real ROM.

Only ONE DeSmuME can run per process and this machine is shared, so the second Platinum player is *recorded*
first (a real app run in a subprocess: it walks around and we keep every `state` message it would send), then the
main app runs alone and a lightweight network-only friend (NetClient, no emulator) replays those messages through
a local relay server - the main app sees exactly what it would see from a real second player.

  1. record "Dawn" in Twinleaf Town (pt_town) and in the Sandgem Pokémon Center (pt_pc) (subprocess, one emulator)
  2. main app in Twinleaf + local server + replay: Dawn shows up next to the player, walks, name tag; img carries
     gender/height through the real server
  3. accuracy: a real NPC moved (in the main game) onto the friend's tile vs. our drawing of the same sprite
  4. hidden during the start menu; neighbouring outdoor map shown; other indoor map hidden
  5. Pokémon Center (orthographic camera indoors) + hidden while the PC is open
Run: python dev/e2e/pt_friends.py   (needs dev/states/platinum.nds, pt_town.dsst, pt_pc.dsst)
Screenshots: printed at the end (a temp folder).
"""
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import time

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
PT_ROM = os.path.join(STATES, "platinum.nds")
ROLE = sys.argv[1] if len(sys.argv) > 1 else "main"
tmp = pathlib.Path(sys.argv[2]) if ROLE == "record" else pathlib.Path(tempfile.mkdtemp(prefix="yl_ptf_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / ("rec_" + sys.argv[3] if ROLE == "record" else "main") / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  YL_PT_ROM=vanilla (default) | sinnoh: the Sinnoh ROM the app / DS core runs
print("Sinnoh ROM:", ptrom.label())
import pygame  # noqa: E402
import numpy as np  # noqa: E402
from yellowlink.config import Config, app_dir  # noqa: E402
from yellowlink import app as appmod, library, sinnoh_view  # noqa: E402

KEYS = {"up": pygame.K_UP, "down": pygame.K_DOWN, "left": pygame.K_LEFT, "right": pygame.K_RIGHT,
        "menu": pygame.K_d, "b": pygame.K_z, "a": pygame.K_x}


def make_app(state, name="Ilias", port=None):
    cfg = Config()
    lib = library.Library(cfg, app_dir())
    if not (app_dir() / "roms" / "platinum.nds").exists():
        lib.add_rom(PT_ROM)
    lib.set_current("platinum")
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{port or 9}", room_key="ptf", seen_help=True, ds_scale=2,
               auto_resume=False)
    cfg.save()
    os.environ["YL_DEV_LOAD_STATE"] = state
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


def frame(app, render=True):
    app.handle_events()
    app.step()
    if render:
        app.render()


def hold(app, k, n, render=True):
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=KEYS[k], mod=0, unicode="", scancode=0))
    for _ in range(n):
        frame(app, render)
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=KEYS[k], mod=0, unicode="", scancode=0))
    frame(app, render)


def walk(app, k, tiles, render=True):
    """Walk `tiles` tiles (holding the arrow until the tile coordinate changed that often), then stop."""
    g = app.game
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=KEYS[k], mod=0, unicode="", scancode=0))
    last = (g.position()["x"], g.position()["y"])
    moved = 0
    for _ in range(40 * tiles + 40):
        frame(app, render)
        p = g.position()
        if (p["x"], p["y"]) != last:
            moved += 1
            last = (p["x"], p["y"])
            if moved >= tiles:
                break
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=KEYS[k], mod=0, unicode="", scancode=0))
    for _ in range(60):
        frame(app, render)
        if not g.walking():
            break
    return moved


# =============================================================================================================
if ROLE == "record":
    # pt_friends.py record <tmp> <name> <state> <script>   script: "right:1,wait:40,up:2,..."
    name, state, script = sys.argv[3], sys.argv[4], sys.argv[5]
    A = make_app(state, name="Dawn")
    real_pos = A.game.position
    A.game.position = lambda: dict(real_pos(), gender=1)        # this friend plays as Dawn

    class Recorder:
        status = "online"
        status_detail = ""
        rtt_ms = None

        def __init__(self):
            self.msgs = []

        def send(self, msg):
            if msg.get("type") == "state":
                self.msgs.append([A.frame, dict(msg)])
            return True

        def poll(self):
            return []

        def stop(self):
            pass
    A.net.stop()
    A.net = rec = Recorder()
    for _ in range(30):
        frame(A, False)
    marks = {}
    for cmd in script.split(","):
        k, _, n = cmd.partition(":")
        n = int(n or 1)
        if k == "wait":
            for _ in range(n):
                frame(A, False)
        elif k == "mark":
            marks[str(n)] = A.frame
        else:
            moved = walk(A, k, n, False)
            p = A.game.position()
            print(f"walk {k} {n}: moved {moved} -> ({p['x']}, {p['y']})", flush=True)
    for _ in range(20):
        frame(A, False)
    out = {"msgs": rec.msgs, "marks": marks, "end": A.frame, "pos": A.game.position()}
    (tmp / f"{name}.json").write_text(json.dumps(out))
    print("recorded", len(rec.msgs), "states, end at", out["pos"]["x"], out["pos"]["y"], flush=True)
    A.net = type("N", (), {"stop": lambda self: None, "send": lambda self, m: True, "status": "offline"})()
    A.shutdown()
    sys.exit(0)

# =============================================================================================================
FAILS = []
NOTES = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail != "" else ""), flush=True)
    if not ok:
        FAILS.append(label)


def record(name, state, script):
    t = time.time()
    r = subprocess.run([sys.executable, __file__, "record", str(tmp), name, state, script],
                       capture_output=True, text=True, timeout=900)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-4000:])
        raise SystemExit(f"recording {name} failed")
    line = [x for x in r.stdout.splitlines() if x.startswith("recorded")]
    for x in r.stdout.splitlines():
        if x.startswith("walk "):
            print("   ", x)
    print(f"{name}: {line[-1] if line else '?'} ({time.time() - t:.0f}s)", flush=True)
    return json.loads((tmp / f"{name}.json").read_text())


# 1. record the friend (Twinleaf: 1 right = next to the player, then a walk around; Pokémon Center: 2 down, 1 left)
TOWN = record("town", os.path.join(STATES, "pt_town.dsst"),
              "right:1,wait:40,mark:1,down:2,right:2,up:2,left:2,wait:30,mark:2,right:2,left:2,wait:30,mark:3")
PC = record("pc", os.path.join(STATES, "pt_pc.dsst"), "down:2,left:2,wait:30,mark:1")

import socket  # noqa: E402
with socket.socket() as _s:                      # a free port (other tests may be running on this machine)
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)],
                       cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="ptf", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
time.sleep(1.5)

try:
    from yellowlink.net import NetClient
    A = make_app(os.path.join(STATES, "pt_town.dsst"), port=PORT)
    check("App.full on the verified Platinum ROM", A.full)
    check("Lucas/Dawn decoded from the ROM", A._sinnoh() is not None and len(A._sinnoh().sprites) == 4,
          getattr(A._sinnoh(), "error", "no view"))
    F = NetClient(f"ws://127.0.0.1:{PORT}", {"name": "Dawn", "uid": "dawn-test-uid", "key": "ptf",
                                             "version": appmod.__version__, "protocol": 1})
    F.start()
    for _ in range(600):
        frame(A, False)
        F.poll()
        if A.net.status == "online" and F.status == "online" and "dawn-test-uid" in A.friends:
            break
    check("main app + network friend online", A.net.status == "online" and F.status == "online"
          and "dawn-test-uid" in A.friends, (A.net.status, F.status))

    def topsurf(app):
        """(game_surf top screen as drawn, raw DS top screen) as (192, 256, 3) arrays."""
        drawn = pygame.surfarray.array3d(app.game_surf)[:, :192].transpose(1, 0, 2)
        raw = np.frombuffer(app.nds.screen_rgb(), np.uint8).reshape(384, 256, 3)[:192]
        return drawn.astype(int), raw.astype(int)

    def friend_bbox(app):
        drawn, raw = topsurf(app)
        diff = np.abs(drawn - raw).sum(2) > 0
        if not diff.any():
            return None
        ys, xs = np.nonzero(diff)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def shot(app, name, crop_top=False):
        p = tmp / name
        if crop_top:
            surf = app.screen.subsurface((0, app.game_y, app.game_w, int(192 * app.scale)))
            pygame.image.save(surf, str(p))
        else:
            pygame.image.save(app.screen, str(p))
        return p

    REPLAY_POS = {}

    def replay(app, rec, until_mark=None, shots_every=None, prefix=None):
        """Send the recorded states through the server, paced by the main app's frames."""
        msgs = rec["msgs"]
        f0 = msgs[0][0]
        end = rec["marks"][str(until_mark)] if until_mark else rec["end"]
        i = REPLAY_POS.get(id(rec), 0)
        start = app.frame - (msgs[i][0] - f0 if i < len(msgs) else 0)
        strip = []
        while True:
            t = app.frame - start + f0
            while i < len(msgs) and msgs[i][0] <= t:
                F.send(dict(msgs[i][1]))
                i += 1
            frame(app)
            F.poll()
            if shots_every and (app.frame - start) % shots_every == 0:
                strip.append(app.screen.subsurface((0, app.game_y, app.game_w, int(192 * app.scale))).copy())
            if t >= end:
                break
        REPLAY_POS[id(rec)] = i
        for _ in range(30):                       # let the last state arrive and the glide settle
            frame(app)
            F.poll()
        if strip and prefix:
            w, h = strip[0].get_size()
            sw = pygame.transform.smoothscale
            n = min(len(strip), 8)
            pick = [strip[int(k * (len(strip) - 1) / max(1, n - 1))] for k in range(n)]
            out = pygame.Surface((w // 2 * 4, h // 2 * ((n + 3) // 4)))
            for k, s in enumerate(pick):
                out.blit(sw(s, (w // 2, h // 2)), ((k % 4) * (w // 2), (k // 4) * (h // 2)))
            pygame.image.save(out, str(tmp / prefix))
        return strip

    # 2. Twinleaf: Dawn steps next to the player -----------------------------------------------------------------
    me = A.game.position()
    replay(A, TOWN, until_mark=1)
    f = A.friends["dawn-test-uid"]
    st = f.state or {}
    info = sinnoh_view.unpack_img(st.get("img"))
    check("state relayed by the server keeps gender/height in img", info["gender"] == 1 and info["ph"] == me["ph"],
          (st.get("img"), info, me["ph"]))
    check("friend reported one tile right of the player", (st.get("x"), st.get("y")) == (me["x"] + 1, me["y"]),
          (st.get("x"), st.get("y"), me["x"], me["y"]))
    bb = friend_bbox(A)
    cam, _ = A._sinnoh().display(A.nds.frame_count)
    rect = cam.billboard(sinnoh_view.billboard_anchor(f.cur_px, info["ph"], f.cur_py)) if cam else None
    check("Dawn drawn on the top screen next to the player", bb is not None and rect is not None
          and abs((bb[0] + bb[2]) / 2 - (rect[0] + rect[2] / 2)) < 6 and 128 < (bb[0] + bb[2]) / 2 < 160, (bb, rect))
    shot(A, "1_twinleaf_next_to_player.png")
    shot(A, "1_twinleaf_top.png", crop_top=True)

    # 3. walking
    replay(A, TOWN, until_mark=2, shots_every=6, prefix="2_twinleaf_walking_strip.png")
    shot(A, "2_twinleaf_after_walk.png")
    replay(A, TOWN, until_mark=3)
    st = A.friends["dawn-test-uid"].state
    check("friend back next to the player after the walk", (st["x"], st["y"]) == (me["x"] + 1, me["y"]),
          (st["x"], st["y"]))

    # 4. accuracy: move a real NPC around the player (RAM write in the main game) and check that our projection
    #    puts that NPC's own texture (found in the ROM by matching) exactly where the game draws it ---------------
    from yellowlink import ndsrom
    narc = ndsrom.NDSRom(PT_ROM).narc(sinnoh_view.MMODEL_NARC)
    TEXTURES = {}
    for i in range(len(narc)):
        try:
            fr = sinnoh_view.decode_btx0(narc.member(i))["frames"]
        except Exception:  # noqa: BLE001 - models / other formats
            continue
        if len(fr) >= 16 and all(v[0] == 32 and v[1] == 32 for v in fr.values()):
            TEXTURES[i] = {k: np.frombuffer(v[2], np.uint8).reshape(32, 32, 4) for k, v in fr.items()}

    def fit_err(img, rect, tex, ox=0.0, oy=0.0):
        """Mean colour error of tex drawn into rect (+ shift) vs. the screen, with a per-channel gain (light)."""
        left, top, w, h = rect[0] + ox, rect[1] + oy, rect[2], rect[3]
        ys = np.arange(max(0, int(top)), min(192, int(np.ceil(top + h))))
        xs = np.arange(max(0, int(left)), min(256, int(np.ceil(left + w))))
        v = np.floor((ys + 0.5 - top) / h * 32).astype(int)
        u = np.floor((xs + 0.5 - left) / w * 32).astype(int)
        okv, oku = (v >= 0) & (v < 32), (u >= 0) & (u < 32)
        ys, v, xs, u = ys[okv], v[okv], xs[oku], u[oku]
        t = tex[np.ix_(v, u)]
        m = t[:, :, 3] > 0
        if m.sum() < 60:
            return 999.0
        sc = img[np.ix_(ys, xs)][m].astype(float)
        tc = t[:, :, :3][m].astype(float)
        gain = (sc * tc).sum(0) / np.maximum((tc * tc).sum(0), 1)
        return float(np.abs(sc - tc * gain).mean())

    def npc_accuracy(app, tiles, label):
        """Move one NPC of the current map to each tile (dx, dz) around the player; return rows of
        (tile, best shift (x, y) of our quad vs. the game's sprite, error at shift 0, best error)."""
        g = app.game
        fs = g.field_system()
        arr = app.nds.r32(app.nds.r32(fs + 0x38) + 0x124)
        mo = next(arr + k * 0x128 for k in range(1, 16)
                  if app.nds.r32(arr + k * 0x128) & 1 and app.nds.r32(arr + k * 0x128 + 8) != 0xFF)
        me = g.position()
        home = struct.unpack("<3i", app.nds.read(mo + 0x64, 12))
        sv = app._sinnoh()
        tex_id = None
        rows = []

        def put(x, z):
            for o, v in ((0x64, x), (0x6C, z), (0x70, (x * 16 + 8) << 12), (0x78, (z * 16 + 8) << 12)):
                app.nds.w32(mo + o, v & 0xFFFFFFFF)
        for dx, dz in tiles:
            app.nds.w32(mo, app.nds.r32(mo) & ~0x40)
            put(me["x"] + dx, me["y"] + dz)
            for _ in range(16):                               # the game re-creates its sprite after a jump
                frame(app, False)
            app.nds.w32(mo, app.nds.r32(mo) | 0x40)          # then pause its own wandering
            for _ in range(6):
                frame(app, False)
            img = np.frombuffer(app.nds.screen_rgb(), np.uint8).reshape(384, 256, 3)[:192]
            cam, _m = sv.display(app.nds.frame_count)
            pos = np.array(struct.unpack("<3i", app.nds.read(mo + 0x70, 12))) / 4096
            for o in sinnoh_view.MO_SPRITE_OFFSETS:
                pos = pos + np.array(struct.unpack("<3i", app.nds.read(mo + o, 12))) / 4096
            rect = cam.billboard((pos[0], pos[1], pos[2] + sinnoh_view.BILLBOARD_Z))
            k = 4 * (app.nds.r32(mo + 0x28) & 3)
            if rect is None or not (0 <= rect[0] and rect[0] + rect[2] <= 256 and 0 <= rect[1] and
                                    rect[1] + rect[3] <= 192):
                continue
            if tex_id is None:                                # which ROM texture is this NPC? (best match)
                tex_id = min(TEXTURES, key=lambda i: min(fit_err(img, rect, TEXTURES[i][kk], ox, oy)
                                                         for kk in {k, 0} if kk in TEXTURES[i]
                                                         for ox in (-1, 0, 1) for oy in (-1, 0, 1)))
            tex = TEXTURES[tex_id][k if k in TEXTURES[tex_id] else 0]
            e0 = fit_err(img, rect, tex)
            best = min((fit_err(img, rect, tex, ox, oy), ox, oy) for ox in np.arange(-3, 3.5, 0.5)
                       for oy in np.arange(-3, 3.5, 0.5))
            rows.append(((dx, dz), (best[1], best[2]), round(e0, 1), round(best[0], 1)))
        put(*[home[0], home[2]])
        app.nds.w32(mo, app.nds.r32(mo) & ~0x40)
        for _ in range(20):
            frame(app, False)
        print(f"   {label}: NPC texture = mmodel member {tex_id}")
        for r in rows:
            print(f"   {label} tile {r[0]}: best shift of our quad {r[1]} px, colour error at our position {r[2]}"
                  f" (best {r[3]})")
        return rows

    ACC = npc_accuracy(A, [(-3, -3), (3, -3), (-5, 1), (4, 1), (-2, 3), (5, 3), (-6, -1), (2, -2), (-4, -1),
                           (3, 2), (-1, 2), (6, -2)], "Twinleaf")
    clean = [r for r in ACC if r[3] < 8]          # others: hidden behind trees/houses/the player, or off screen
    check("outdoor projection = where the game draws NPCs (within 1 px)",
          len(clean) >= 4 and all(abs(r[1][0]) <= 1 and abs(r[1][1]) <= 1 for r in clean), clean)
    NOTES.append(("accuracy_outdoor", ACC))

    # 5. hidden in the start menu; outdoor neighbour map shown, other indoor map hidden ---------------------------
    for _ in range(20):
        frame(A)
    check("visible before opening the menu", friend_bbox(A) is not None)
    hold(A, "menu", 6)
    for _ in range(40):
        frame(A)
    check("hidden while the start menu is open", friend_bbox(A) is None and A.game.field_task())
    shot(A, "3_menu_hidden.png")
    hold(A, "b", 6)
    for _ in range(60):
        frame(A)
    check("visible again after closing the menu", friend_bbox(A) is not None)
    base = dict(A.friends["dawn-test-uid"].state)
    A.on_state(dict(base, uid="dawn-test-uid", map=342))              # Route 201 (same outdoor matrix)
    for _ in range(4):
        frame(A)
    check("friend on a neighbouring outdoor map is drawn (global Sinnoh grid)", friend_bbox(A) is not None)
    A.on_state(dict(base, uid="dawn-test-uid", map=420))              # a Pokémon Center interior
    for _ in range(4):
        frame(A)
    check("friend in another indoor map is hidden", friend_bbox(A) is None)
    A.on_state(dict(base, uid="dawn-test-uid"))
    for _ in range(4):
        frame(A)

    # performance
    t = time.perf_counter()
    for _ in range(120):
        A._draw_friends_ds()
    ms = (time.perf_counter() - t) / 120 * 1000
    check("drawing friends is cheap (< 2 ms/frame)", ms < 2, f"{ms:.2f} ms")
    NOTES.append(("draw_ms", ms))

    # 6. Pokémon Center (orthographic camera) --------------------------------------------------------------------
    A.nds.load_state(os.path.join(STATES, "pt_pc.dsst"))
    for _ in range(30):
        frame(A)
    me = A.game.position()
    replay(A, PC, shots_every=6, prefix="5_pokecenter_walking_strip.png")
    st = A.friends["dawn-test-uid"].state
    check("friend in the Pokémon Center (same map)", st["map"] == me["map"] == 420, (st["map"], me["map"]))
    bb = friend_bbox(A)
    cam, _ = A._sinnoh().display(A.nds.frame_count)
    check("indoor camera is orthographic", cam is not None and cam.projection == 1)
    info = sinnoh_view.unpack_img(st.get("img"))
    f = A.friends["dawn-test-uid"]
    rect = cam.billboard(sinnoh_view.billboard_anchor(f.cur_px, info["ph"], f.cur_py)) if cam else None
    check("Dawn drawn indoors where she stands", bb is not None and rect is not None
          and abs((bb[0] + bb[2]) / 2 - (rect[0] + rect[2] / 2)) < 4, (bb, rect))
    shot(A, "4_pokecenter.png")
    shot(A, "4_pokecenter_top.png", crop_top=True)
    ACC_IN = npc_accuracy(A, [(-3, 2), (2, 2), (-5, 4), (3, 5), (-6, 6), (-2, 6), (1, 4)], "Pokemon Center")
    clean = [r for r in ACC_IN if r[3] < 8]
    check("indoor projection = where the game draws NPCs (within 1 px)",
          len(clean) >= 2 and all(abs(r[1][0]) <= 1 and abs(r[1][1]) <= 1 for r in clean), clean)
    NOTES.append(("accuracy_indoor", ACC_IN))
    hold(A, "a", 6)                          # the player faces the PC: A boots it (field task + menus)
    for _ in range(90):
        frame(A)
    check("hidden while the PC / its messages are up", friend_bbox(A) is None, A.game.status_line())
    shot(A, "6_pc_hidden.png")

    F.stop()
    A.shutdown()
finally:
    srv.terminate()
for k, v in NOTES:
    print("note", k, v)
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
