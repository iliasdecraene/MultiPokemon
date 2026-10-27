"""Shiny alerts e2e (SPEC.md section 10): local server + two real clients, all three games.

  1. Ilias (Yellow) gets a wild battle with a shiny planted into wEnemyMonDVs (and its species marked caught):
     the server's chat line reaches Alex (Crystal) and Ilias; Alex gets the banner "Ilias found a shiny Pidgey!"
     + the jingle, Ilias "A shiny Pidgey appeared!" + the jingle (the play call is counted, not heard). At the
     battle menu Ilias sees the caught Poké Ball next to the enemy's name (+ the 'Shiny!' tag): screenshots 2x/3x/4x.
  2. Alex (Crystal) meets a planted shiny (wEnemyMonDVs) -> Ilias gets line + banner + jingle.
  3. Alex switches to Platinum; a shiny PID is planted into the wild mon on the battle's first frame -> Ilias is
     told; Alex's DS window (1.5x) shows the finder banner; then Ilias's second shiny (after the 30 s server rate
     limit) shows on Alex's DS window too.
  4. Only one alert per battle; a second shiny_found within 30 s is dropped by the server.
Needs dev/states/{yellow.gb, crystal.gbc, platinum.nds, pallet_after_lab.state, soullink_wild_battle.state,
crystal_party.state, crystal_sl_wild.state, pt_town.dsst, pt_sl_wild.dsst}.
Run: python dev/e2e/shiny_alert.py   (screenshots: the temp folder printed at the end)
"""
import atexit
import os
import pathlib
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_shiny_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "shiny"))
import pygame  # noqa: E402
pygame.quit = lambda: None          # several apps share this process
from yellowlink.config import Config  # noqa: E402
from yellowlink import app as appmod, crystal, game, shiny  # noqa: E402
from yellowlink import platinum as P  # noqa: E402

PORT = 8797
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)],
                       cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_KEY="test", YL_DATA_DIR=str(tmp / "data"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
assert srv.poll() is None, f"the local server didn't start (port {PORT} busy?) - see {tmp / 'srv.log'}"
FAILS = []
JINGLES = {}
_real_jingle = appmod.App.play_shiny_jingle


def counted_jingle(self):
    JINGLES[self.cfg["name"]] = JINGLES.get(self.cfg["name"], 0) + 1


appmod.App.play_shiny_jingle = counted_jingle


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


class Cfg(Config):
    def __init__(self, d, name, rom, gid):
        d.mkdir(parents=True, exist_ok=True)
        self.d = d
        super().__init__(d / "config.json")
        self.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="test", rom_path=rom, seen_help=True,
                    games={gid: rom}, current_game=gid, auto_resume=False, scale=3, ds_scale=1.5)


def make(name, rom, gid, state):
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(Cfg(tmp / name, name, os.path.join(STATES, rom), gid))
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


A = make("Ilias", "yellow.gb", "yellow", "pallet_after_lab.state")
B = make("Alex", "crystal.gbc", "crystal", "crystal_party.state")


def run(n, cond=None):
    for _ in range(n):
        A.step()
        B.step()
        time.sleep(0.001)
        if cond and cond():
            return True
    return False


def shot(app, name, scale=None):
    if app.banners.cur is not None:                         # show the banner as it looks 1.2 s in (fully in)
        app.banners.cur.start = time.time() - 1.2
    if scale:
        app.cfg[app._scale_key()] = scale
    app._make_window()
    app.render()
    pygame.image.save(app.screen, str(tmp / name))


def lines(app):
    return [m for _, m, _ in app.chat_log]


def banner(app):
    b = app.banners.current()
    return b and b.title


run(400, lambda: "shiny_alert" in A.server_features and "shiny_alert" in B.server_features
    and len(A.roster) == 2 and len(B.roster) == 2)
check("server lists the shiny_alert feature", "shiny_alert" in A.server_features and
      "shiny_alert" in B.server_features)

# 1. Ilias: a shiny Pidgey on Route 1 (Yellow), already caught
A.pyboy.load_state(open(os.path.join(STATES, "soullink_wild_battle.state"), "rb"))
m = A.pyboy.memory
m[game.ENEMY_MON_DVS], m[game.ENEMY_MON_DVS + 1] = 0xEA, 0xAA
dex = A.rom.dex_no(m[game.ENEMY_MON_SPECIES2])
m[game.DEX_OWNED + (dex - 1) // 8] |= 1 << ((dex - 1) % 8)
t_ilias = time.time()
ok = run(600, lambda: any("✨ Ilias found a shiny Pidgey on Route 1!" == x for x in lines(B)))
check("Alex gets the server's chat line '✨ Ilias found a shiny Pidgey on Route 1!'", ok, str(lines(B)[-3:]))
run(30, lambda: banner(B))
check("Alex's banner: 'Ilias found a shiny Pidgey!' (on Route 1) + jingle",
      banner(B) == "Ilias found a shiny Pidgey!" and B.banners.current().sub == "on Route 1"
      and JINGLES.get("Alex") == 1, f"{banner(B)} {JINGLES}")
check("Ilias's own banner 'A shiny Pidgey appeared!' + jingle, and the chat line came back",
      banner(A) == "A shiny Pidgey appeared!" and JINGLES.get("Ilias") == 1
      and "✨ Ilias found a shiny Pidgey on Route 1!" in lines(A), f"{banner(A)} {JINGLES} {lines(A)[-3:]}")
check("Ilias didn't get a second banner from the server's echo", not A.banners.queue)
shot(A, "1_finder_banner_yellow_3x.png", 3)
shot(A, "1_finder_banner_yellow_4x.png", 4)                  # (4x: the chat box has room for the sparkle line)
shot(B, "2_friend_banner_crystal_3x.png", 3)
B.chat_scroll = 0
check("Ilias's battle settled the 'already caught' flag", A.wild_owned)
for _ in range(3):                                          # past "Wild PIDGEY appeared!" -> both HUDs
    A.pyboy.button("a")
    run(200)
run(200, lambda: A.game.enemy_hud_shown())
A.banners.cur = None                                        # (the banner is long gone at real speed)
check("enemy HUD on screen at the battle menu", A.game.enemy_hud_shown())
for scale in (2, 3, 4):
    shot(A, f"3_yellow_caught_ball_{scale}x.png", scale)
    # the ball's tile (1, 1) and its 8x8 Game Boy pixels: left of ':L', under the name, above the HP bar
    x0, y0 = (A.off_x + appmod.OWNED_BALL_X) * scale, A.game_y + (A.off_y + appmod.OWNED_BALL_Y) * scale
    px = A.screen.get_at((int(x0 + 4 * scale), int(y0 + 2 * scale)))
    check(f"{scale}x: red ball top drawn at tile (1, 1)", px.r > 180 and px.g < 110, str(px))
A.cfg[A._scale_key()] = 3
A._make_window()
n_alerts = sum(1 for x in lines(B) if x.startswith("✨ Ilias"))
run(300)
check("one alert per battle (nothing more while the battle goes on)",
      sum(1 for x in lines(B) if x.startswith("✨ Ilias")) == n_alerts == 1)

# a manual second shiny_found inside the 30 s window: dropped by the server, no red error
A.net.send(shiny.found_message("Rattata", 19, "Route 1", "yellow"))
run(120)
check("server rate limit: a second alert within 30 s is dropped silently",
      not any("Rattata" in x for x in lines(B)) and not any(x.startswith("Server:") for x in lines(A)))

# 2. Alex: a shiny in Crystal
A.banners.cur = B.banners.cur = None                        # (earlier banners are over at real speed)
B.pyboy.load_state(open(os.path.join(STATES, "crystal_sl_wild.state"), "rb"))
B.game.write(crystal.ENEMY_MON_DVS, bytes([0xEA, 0xAA]))
bdex = B.game.battle_info()["dex"]
bname = shiny.species_label(B.rom.species_name(bdex))
barea = crystal.area_name(crystal.area_of(B.game.map_id()))
t_alex = time.time()
want = f"✨ Alex found a shiny {bname} {shiny.area_phrase(barea)}!"
ok = run(600, lambda: want in lines(A))
check(f"Ilias gets '{want}'", ok, str(lines(A)[-3:]))
run(30, lambda: banner(A) == f"Alex found a shiny {bname}!")
check("Ilias's banner + jingle for Alex's Crystal shiny", banner(A) == f"Alex found a shiny {bname}!"
      and JINGLES.get("Ilias") == 2, f"{banner(A)} {JINGLES}")
check("Alex: own banner + jingle", banner(B) == f"A shiny {bname} appeared!" and JINGLES.get("Alex") == 2)
shot(A, "4_friend_banner_yellow_3x.png", 3)

# 3. Alex switches to Platinum (one DeSmuME per process: this is the only one)
B.shutdown()
B = make("Alex", "platinum.nds", "platinum", "pt_town.dsst")
run(600, lambda: "shiny_alert" in B.server_features and B.net.status == "online")
check("Alex back online, now in Platinum", B.ds and B.full and "shiny_alert" in B.server_features)
from verify import make_shiny_at  # noqa: E402  (dev/shiny/verify.py)
wait = 31 - (time.time() - t_alex)
if wait > 0:
    print(f"     (waiting {wait:.0f} s for the server's per-player rate limit)", flush=True)
    t_end = time.time() + wait
    while time.time() < t_end:
        run(10)
A.banners.cur = B.banners.cur = None
B.nds.load_state(os.path.join(STATES, "pt_sl_wild.dsst"))
d = B.nds
dto = B.game.battle_dto()
pid = make_shiny_at(d, d.r32(dto + P.DTO_PARTIES + 4) + 8)
info = B.game.battle_info()
pname = shiny.species_label(B.plat_data.species_name(info["dex"]))
parea = P.area_name(P.area_of(B.game.map_id()))
want = f"✨ Alex found a shiny {pname} {shiny.area_phrase(parea)}!"
check(f"planted PID {pid:08X}: the wild {pname} reads shiny", info["shiny"])
ok = run(600, lambda: want in lines(A))
check(f"Ilias gets '{want}'", ok, str(lines(A)[-3:]))
run(30, lambda: banner(B))
check("Alex (DS): own banner + jingle", banner(B) == f"A shiny {pname} appeared!" and JINGLES.get("Alex") == 3,
      f"{banner(B)} {JINGLES}")
shot(B, "5_finder_banner_platinum_1.5x.png", 1.5)
run(30, lambda: banner(A) == f"Alex found a shiny {pname}!")
check("Ilias: banner + jingle for the Platinum shiny", banner(A) == f"Alex found a shiny {pname}!"
      and JINGLES.get("Ilias") == 3, f"{banner(A)} {JINGLES}")

# Ilias's next shiny (after his rate limit) shows on Alex's DS window
wait = 31 - (time.time() - t_ilias)
if wait > 0:
    print(f"     (waiting {wait:.0f} s for the server's per-player rate limit)", flush=True)
    t_end = time.time() + wait
    while time.time() < t_end:
        run(10)
A.pyboy.load_state(open(os.path.join(STATES, "pallet_after_lab.state"), "rb"))
run(shiny.POLL_EVERY * (shiny.END_READS + 2))
A.pyboy.load_state(open(os.path.join(STATES, "soullink_wild_battle.state"), "rb"))
m = A.pyboy.memory
m[game.ENEMY_MON_DVS], m[game.ENEMY_MON_DVS + 1] = 0xEA, 0xAA
B.banners.cur = None
ok = run(600, lambda: banner(B) == "Ilias found a shiny Pidgey!")
check("Alex (DS) gets Ilias's next shiny as a banner + jingle", ok and JINGLES.get("Alex") == 4, str(JINGLES))
shot(B, "6_friend_banner_platinum_1.5x.png", 1.5)

# the real jingle path runs without a crash (dummy audio driver here)
appmod.App.play_shiny_jingle = _real_jingle
try:
    A.play_shiny_jingle()
    check("the real jingle call works (own mixer channel)", A.audio.fx_channel is not None)
except Exception as e:  # noqa: BLE001
    check("the real jingle call works", False, repr(e))
A.shutdown()
B.shutdown()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
srv.terminate()
os._exit(1 if FAILS else 0)
