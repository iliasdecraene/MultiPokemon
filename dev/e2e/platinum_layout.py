"""Panel / dialog layout screenshots: Yellow window (scale 4) and the narrower Platinum window (scale 2 and 1.5).

Run: python dev/e2e/platinum_layout.py [out_dir]
Writes <out_dir>/<window>_<panel>.png. Only the game area is compared between runs (the sidebar shows the live
connection state). Compare two runs (e.g. before/after a layout change):
    python dev/e2e/platinum_layout.py /tmp/before ; ...change... ; python dev/e2e/platinum_layout.py /tmp/after
    python dev/e2e/platinum_layout.py --compare /tmp/before /tmp/after
No server is needed: the roster, trade and challenge form are filled in by hand.
"""
import os, sys, tempfile, pathlib
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"


def compare(a, b):
    import pygame
    bad = 0
    for p in sorted(pathlib.Path(a).glob("yellow_*.png")):
        q = pathlib.Path(b) / p.name
        if not q.exists():
            print("missing", q)
            bad += 1
            continue
        ia, ib = pygame.image.load(str(p)), pygame.image.load(str(q))
        w = ia.get_width() - 300                      # game area only (sidebar = live connection state)
        sa = pygame.image.tobytes(ia.subsurface((0, 0, w, ia.get_height())), "RGB")
        sb = pygame.image.tobytes(ib.subsurface((0, 0, w, ib.get_height())), "RGB")
        diff = sum(1 for i in range(0, len(sa), 3) if sa[i:i + 3] != sb[i:i + 3])
        print(f"{p.name}: {'identical' if diff == 0 else f'{diff} pixels differ'}")
        bad += diff > 0
    return bad


if len(sys.argv) > 1 and sys.argv[1] == "--compare":
    sys.exit(1 if compare(sys.argv[2], sys.argv[3]) else 0)

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="yl_layout_"))
OUT.mkdir(parents=True, exist_ok=True)
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_layout_cfg_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
sys.path.insert(0, os.path.join(_REPO, "client"))
import pygame  # noqa: E402
pygame.quit = lambda: None
pygame.mouse.get_pos = lambda: (0, 0)          # no hover effects
from yellowlink.config import Config, app_dir  # noqa: E402
from yellowlink import app as appmod, library  # noqa: E402
from yellowlink.trades import TradeSession  # noqa: E402

cfg = Config()
lib = library.Library(cfg, app_dir())
lib.add_rom(os.path.join(STATES, "yellow.gb"))
lib.add_rom(os.path.join(STATES, "platinum.nds"))
cfg.update(name="Ilias", server_url="ws://127.0.0.1:9", room_key="x", seen_help=True)
cfg.add_server("Weekend crew with a long name", "ws://10.0.0.2:8766", "k")
cfg.save()

ROSTER = [
    {"uid": cfg["uid"], "name": "Ilias", "online": True, "game": "platinum", "levels": [7]},
    {"uid": "u-alex", "name": "Alexandra", "online": True, "game": "yellow", "levels": [12, 9, 30]},
    {"uid": "u-bea", "name": "Bea", "online": False, "game": "crystal", "levels": []},
]


def entry(i, name, dex, lvl, origin):
    return {"id": f"e{i}", "origin": origin, "dex": dex, "level": lvl, "nick": name, "ot": "ASH", "otid": 1}


MINE = [(0, entry(1, "AAAAAAAAAA", 387, 7, "platinum")), (0, entry(2, "PIDGEY", 16, 5, "yellow")),
        (1, entry(3, "SENTRET", 161, 4, "crystal")), (2, entry(4, "Bulbasaur", 1, 12, "yellow"))]
THEIRS = [entry(10, "PIKACHU", 25, 30, "yellow"), entry(11, "Sparky the Mouse", 25, 8, "crystal")]


def shoot(a, tag):
    a.net.stop()                                   # fixed connection state (screens comparable between runs)
    th = getattr(a.net, "_thread", None)
    if th is not None:
        th.join(timeout=3)
    a.net.status, a.net.status_detail = "offline", "no server"
    a.roster = [dict(e) for e in ROSTER]
    a.roster[0]["uid"] = a.cfg["uid"]
    shots = []

    def snap(name):
        a.render()
        pygame.image.save(a.screen, str(OUT / f"{tag}_{name}.png"))
        shots.append(name)
    a.toggle_panel("multiplayer")
    a.panel_sel = "u-alex"
    snap("multiplayer")
    a.close_panel()
    a.toggle_panel("servers")
    snap("servers")
    a.close_panel()
    a.toggle_panel("regions")
    snap("regions")
    a.close_panel()
    a.open_challenges("u-alex")
    snap("challenges")
    a.chal_form = {"uid": "u-alex", "name": "Alexandra", "game": "yellow", "dupes": True, "shiny": True}
    snap("challenge_form")
    a.chal_form = None
    a.close_panel()
    a.trade_entries = lambda: MINE
    a.trade = TradeSession("t1", "u-alex", "Alexandra", my_entries=[MINE[0][1], MINE[1][1]],
                           their_entries=list(THEIRS), them_confirmed=True)
    a.mode = "trade"
    snap("trade")
    a.trade = TradeSession("t2", "u-alex", "Alexandra", gift=True, giving=False, their_entries=list(THEIRS))
    snap("gift_received")
    a.trade = TradeSession("t3", "u-alex", "Alexandra", gift=True, giving=True, my_entries=[MINE[2][1]])
    snap("gift_giving")
    a.trade = None
    a.mode = "help"
    snap("help")
    a.mode = "play"
    return shots


def start(game, state, **settings):
    lib.set_current(game)
    cfg.update(settings)
    os.environ["YL_DEV_LOAD_STATE"] = state
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    for _ in range(30):
        a.step()
    a.toasts.clear()
    return a


Y = start("yellow", os.path.join(STATES, "room.state"), scale=4)
shoot(Y, "yellow")
Y.shutdown()
Y = start("yellow", os.path.join(STATES, "room.state"), scale=3)     # 480x432: compact layout (< 600 px panels)
shoot(Y, "yellow3")
Y.shutdown()
cfg["scale"] = 4
for scale in (2, 1.5):
    P = start("platinum", os.path.join(STATES, "pt_room.dsst"), ds_scale=scale)
    shoot(P, f"platinum{scale}")
    P.shutdown()
print("screens in", OUT)
