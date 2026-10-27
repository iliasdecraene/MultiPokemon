"""Complete Sinnoh pairing e2e (SPEC.md section 11, review H1/L5): a real client playing Pokémon Yellow and a real
client playing Platinum (Complete Sinnoh) + a local server.

  1. the Yellow client (Platinum added, but not running) advertises the Complete Sinnoh version it would build
     (`sinnoh_v<spec>`), the Platinum client the one it runs: the same -> Platinum is offered both ways
  2. a client whose build failed in this process advertises `sinnoh_failed`: the partner is told why (not "update")
  3. the Yellow player invites to a Platinum Soul Link, the Platinum player accepts: both are sent into the run

Run: python dev/e2e/sinnoh_pairing.py   (needs dev/states/yellow.gb, room.state, platinum.nds, pt_starter.dsst,
pt_saved.sav; YL_PT_ROM must be sinnoh - the default here)
"""
import atexit
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_snpair_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ.setdefault("YL_PT_ROM", "sinnoh")
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402
if not ptrom.SINNOH:
    sys.exit("sinnoh_pairing.py is about Complete Sinnoh (YL_PT_ROM=sinnoh)")
import pygame  # noqa: E402
pygame.quit = lambda: None          # two apps share this process
from yellowlink import app as appmod, challenge as chalmod, panels, sinnoh_rom  # noqa: E402
from yellowlink.config import Config  # noqa: E402

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
                       env=dict(os.environ, YL_KEY="sp", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)

GB = os.path.join(STATES, "yellow.gb")
PT = ptrom.VANILLA_ROM
WANT = ["sinnoh_v%d" % sinnoh_rom.load_spec().SPEC_VERSION]


def make_cfg(name, current):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = Config(d / "config.json")
    games = {"yellow": GB, "platinum": PT} if current == "yellow" else {"platinum": PT}
    cfg.update(name=name, server_url=f"ws://127.0.0.1:{PORT}", room_key="sp", rom_path=games[current],
               seen_help=True, games=games, current_game=current, ds_scale=1)
    return cfg


def boot(cfg, state):
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    try:
        return appmod.App(cfg)
    finally:
        os.environ.pop("YL_DEV_LOAD_STATE", None)


ca, cb = make_cfg("Ilias", "yellow"), make_cfg("Alex", "platinum")
shutil.copy2(os.path.join(STATES, "pt_saved.sav"), cb.save_path)
A = boot(ca, "room.state")
B = boot(cb, "pt_starter.dsst")


def run(n, cond=None):
    for _ in range(n):
        for x in (A, B):
            x.handle_events()
            x.step()
        time.sleep(0.002)
        if cond and cond():
            return True
    return False


def rules_of(app, uid):
    return (next((e for e in app.roster if e.get("uid") == uid), {}) or {}).get("challenge_rules") or []


# 1 ---------------------------------------------------------------------------------------------------------
check("Ilias plays Yellow, Alex plays Complete Sinnoh", not A.ds and A.game_id == "yellow" and B.ds and B.full
      and B.complete_sinnoh, (A.game_id, B.ds, getattr(B, "complete_sinnoh", None)))
check("the config keeps Alex's own Platinum file (the App runs the copy)",
      cb["rom_path"] == PT and B.rom_path.endswith(sinnoh_rom.FILE_NAME), (cb["rom_path"], B.rom_path))
check("Ilias (on Yellow) advertises %s" % WANT, A._sinnoh_caps() == WANT, A._sinnoh_caps())
check("Alex (on Platinum) advertises %s" % WANT, B._sinnoh_caps() == WANT, B._sinnoh_caps())
run(600, lambda: "platinum" in A.shared_games(cb["uid"]) and "platinum" in B.shared_games(ca["uid"]))
check("roster: each sees the other's version", WANT[0] in rules_of(A, cb["uid"]) and WANT[0] in rules_of(B, ca["uid"]),
      (rules_of(A, cb["uid"]), rules_of(B, ca["uid"])))
check("Platinum is offered both ways", "platinum" in A.shared_games(cb["uid"]) and
      "platinum" in B.shared_games(ca["uid"]), (A.shared_games(cb["uid"]), B.shared_games(ca["uid"])))

# 2 ---------------------------------------------------------------------------------------------------------
# as if Ilias's Platinum build had failed earlier (per App here: both apps share this process, real players don't)
A.sinnoh_problem = "disk full (test)"
A.chal_on_connected()                               # re-sends `library` (also done on every reconnect)
run(300, lambda: chalmod.SINNOH_FAILED in rules_of(B, ca["uid"]))
check("a failed build is advertised as sinnoh_failed", chalmod.SINNOH_FAILED in rules_of(B, ca["uid"]),
      rules_of(B, ca["uid"]))
tip = panels._game_tip(B, {"uid": ca["uid"], "name": "Ilias"}, "platinum")
check("Alex's tip: Ilias's Complete Sinnoh couldn't be prepared (not 'update')",
      "Ilias" in tip and "couldn't be prepared" in tip and "update" not in tip, tip)
check("... and no Platinum run is offered", "platinum" not in B.shared_games(ca["uid"]))
A.sinnoh_problem = None
A.chal_on_connected()
run(300, lambda: "platinum" in B.shared_games(ca["uid"]))
check("build fine again: Platinum offered again", "platinum" in B.shared_games(ca["uid"]))


# 3 ---------------------------------------------------------------------------------------------------------
A.new_challenge_form(cb["uid"], "Alex")
A.chal_form["game"] = "platinum"
A.send_challenge()
run(300, lambda: B.mode == "prompt" and B.prompt and B.prompt["kind"] == "challenge")
check("Alex gets the Platinum invite", B.mode == "prompt" and B.prompt and B.prompt["kind"] == "challenge")
B.render()
accept = next((cb_ for _r, cb_, lab in B.click_targets if lab == "Accept"), None)
check("the invite has an Accept button", accept is not None)
if accept:
    accept()
run(300, lambda: A.enter_challenge and B.enter_challenge)
check("both are sent into a Platinum Soul Link", bool(A.enter_challenge and B.enter_challenge) and
      A.enter_challenge.get("game") == "platinum" == B.enter_challenge.get("game"),
      (A.enter_challenge, B.enter_challenge))

A.shutdown()
B.shutdown()
print("\n%d failed" % len(FAILS))
for f in FAILS:
    print("  FAILED:", f)
sys.exit(1 if FAILS else 0)
