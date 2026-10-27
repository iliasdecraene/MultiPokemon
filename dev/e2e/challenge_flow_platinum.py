"""Challenge flow e2e for Pokémon Platinum: the real client (DeSmuME) + local server + a simulated partner.

Only ONE DeSmuME can run per process, so the partner is a plain protocol client (a second YellowLink "playing" the
run on another PC, simulated with server messages).

  1. the Challenges panel offers Platinum (both have it, both clients new enough); invite -> accept
  2. restart into the challenge: a fresh, separate Platinum save (DeSmuME's shared .dsv holding the MAIN save is
     never used), paused until the partner loads in, then the game runs
  3. partner's connection drops: paused (DeSmuME's sound paused too), back within 30 s: runs again
  4. pause & go back: main game with its own save, partner told; challenge folder has the run, not the main save
  5. continue together: same run, resumed exactly from the DS snapshot
  6. partner closes the game: pop-up, back to the main game; main save never touched, no stale active_challenge
Run: python dev/e2e/challenge_flow_platinum.py   (needs dev/states/platinum.nds, pt_starter.dsst, pt_saved.sav/.dsv)
If platinum.py doesn't have the Soul Link helpers yet, no-op stand-ins are patched in (marked in the output).
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, uuid, hashlib, shutil
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_ptchal_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")        # our app folder AND DeSmuME's battery folder (Linux)
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  YL_PT_ROM=vanilla (default) | sinnoh: the Sinnoh ROM the app / DS core runs
print("Sinnoh ROM:", ptrom.label())
import pygame
pygame.quit = lambda: None
from yellowlink import __version__
from yellowlink import app as appmod, challenge as chalmod, nds_core, platinum
from yellowlink.config import Config, app_dir
from yellowlink.net import NetClient

PT_ROM = os.path.join(STATES, "platinum.nds")
MAIN_SAVE = pathlib.Path(STATES, "pt_saved.sav").read_bytes()
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
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="pt", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)

missing = [n for n in chalmod.REFEREE_HELPERS if not hasattr(platinum.PlatinumMemory, n)]
if missing:
    stub = {"battle_info": None, "battle_party_slot": None, "has_poke_balls": False, "current_box_records": [],
            "move_party_to_box": False, "move_box_to_party": False, "daycare_records": [], "badges": 0}
    for n in missing:
        setattr(platinum.PlatinumMemory, n, (lambda v: lambda self, *a: v)(stub[n]))
print("Soul Link helpers:", f"STAND-INS for {missing}" if missing else "real (platinum.py)", flush=True)


class Partner:
    """The other player, as the server sees it: a YellowLink client after 0.7.2 that has Platinum."""

    def __init__(self, name, uid=None):
        self.name, self.uid = name, uid or str(uuid.uuid4())
        self.inbox = []
        self.online = False
        self.net = NetClient(f"ws://127.0.0.1:{PORT}", {"name": name, "uid": self.uid, "key": "pt",
                                                        "version": __version__})
        self.net.start()

    def pump(self):
        for m in self.net.poll():
            self.inbox.append(m)
        if self.net.status == "online" and not self.online:
            self.online = True
            self.net.send({"type": "library", "games": ["platinum"], "challenge_games": ["platinum"],
                           "challenge_rules": ["exp_share"] + ptrom.sinnoh_caps()})

    def take(self, mtype, **match):
        for i, m in enumerate(self.inbox):
            if m.get("type") == mtype and all(m.get(k) == v for k, v in match.items()):
                return self.inbox.pop(i)
        return None

    def send(self, **msg):
        return self.net.send(msg)


cfg = Config()
cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{PORT}", room_key="pt", rom_path=PT_ROM, seen_help=True,
           games={"platinum": PT_ROM}, current_game="platinum", ds_scale=2, exp_share_all=False)
cfg.save()
main_save = cfg.data_dir / "saves" / "platinum.sav"
main_save.write_bytes(MAIN_SAVE)
# DeSmuME's battery file is named after the ROM it runs: with YL_PT_ROM=sinnoh the app makes and runs
# roms/platinum-sinnoh.nds from PT_ROM
dsv = pathlib.Path(nds_core.battery_file_candidates(os.path.join(os.path.dirname(PT_ROM), "platinum-sinnoh.nds")
                                                    if ptrom.SINNOH else PT_ROM)[0])
check("test isolation: DeSmuME's battery folder is in this test's folder", str(dsv).startswith(str(tmp)), dsv)


def boot(dev_state=None):
    if dev_state:
        os.environ["YL_DEV_LOAD_STATE"] = dev_state
    try:
        return appmod.App(cfg)
    finally:
        os.environ.pop("YL_DEV_LOAD_STATE", None)


def restart(app):
    """What _main does after app.run() returns."""
    app.shutdown()
    if app.enter_challenge:
        cfg["active_challenge"] = app.enter_challenge
        cfg.save()
        return boot()
    if cfg.pop("active_challenge", None) is not None:
        cfg.save()
    return boot(os.path.join(STATES, "pt_starter.dsst"))


def run(n, cond=None):
    for _ in range(n):
        A.handle_events()
        A.step()
        B.pump()
        time.sleep(0.001)
        if cond and cond():
            return True
    return False


def shot(name):
    A.render()
    pygame.image.save(A.screen, str(tmp / name))


def ram_hash():
    return hashlib.sha1(A.nds.read(0x02000000, 0x400000)).hexdigest()


A = boot(os.path.join(STATES, "pt_starter.dsst"))
B = Partner("Alex")
check("main game: Platinum with its own save", A.ds and A.full and A.nds.loaded_save
      and A.nds.export_save() == MAIN_SAVE)

# 1. invite -----------------------------------------------------------------------------------------------------
run(400, lambda: A.net.status == "online" and A.shared_games(B.uid) == ["platinum"])
entry = next((e for e in A.roster if e.get("uid") == B.uid), {})
check("roster: partner can run Platinum challenges", entry.get("challenge_games") == ["platinum"], entry)
check("I can run Platinum challenges", "platinum" in A.challenge_games(), A.challenge_games())
A.open_challenges(B.uid)
shot("1_challenges_panel.png")
check("Start a Soul Link is offered", any(lab == "Start a Soul Link" for _, _, lab in A.click_targets))
A.new_challenge_form(B.uid, "Alex")
check("form defaults to Platinum", A.chal_form["game"] == "platinum", A.chal_form)
check("Exp Share All rule: default = my own setting (off), partner supports it",
      A.chal_form["exp_share"] is False and A.can_exp_share_rule(B.uid))
A.toggle_exp_share_rule()                    # switch it ON for this run (my personal setting stays off)
shot("2_form.png")
labels = [lab for _, _, lab in A.click_targets]
check("form: Platinum button on, Yellow/Crystal off (Alex only has Platinum)",
      "Platinum" in labels and "Yellow (disabled)" in labels and "Crystal (disabled)" in labels, labels)
A.send_challenge()
got = {}
run(200, lambda: got.setdefault("req", B.take("challenge_request")) is not None or got.pop("req") is not None)
req = got.get("req") or {}
check("partner gets a Platinum invite", req.get("game") == "platinum" and req.get("challenge_id") is None, req)
check("...with Exp Share All on as a run rule", (req.get("rules") or {}).get("exp_share") is True, req)
A.close_panel()
B.send(type="challenge_respond", request_id=req.get("request_id"), accept=True)
run(300, lambda: A.enter_challenge is not None and B.take("challenge_go") is not None)
check("both are sent into the challenge", A.enter_challenge is not None and A.enter_challenge["game"] == "platinum",
      A.enter_challenge)
cid = A.enter_challenge["id"]

# 2. into the challenge: fresh save, DeSmuME's shared battery holding the MAIN save is ignored -------------------
A.shutdown()
check("closing blanks DeSmuME's shared .dsv", dsv.exists() and set(dsv.read_bytes()[:512 * 1024]) == {0xFF})
shutil.copy(os.path.join(STATES, "pt_saved.dsv"), dsv)          # as if a crash had left the main save in it
cfg["active_challenge"] = A.enter_challenge
cfg.save()
A = boot()
check("in the challenge, own save path", A.chal and A.cfg.save_path == app_dir() / "challenges" / cid / "game.sav",
      A.cfg.save_path)
blank = A.nds.export_save()
check("fresh game: no save loaded, the battery is blank (main save not leaked from the .dsv)",
      not A.had_save and not A.nds.loaded_save and set(blank) == {0xFF})
f0 = A.nds.frame_count
run(60)
check("paused until the partner loads in", A.chal_wait == "load" and A.nds.frame_count == f0
      and A._ds_chal_paused, (A.chal_wait, A.nds.frame_count - f0))
shot("3_waiting.png")
B.send(type="challenge_join", challenge_id=cid)
run(300, lambda: A.chal_wait is None)
run(120)
check("both in -> the game runs", A.chal_wait is None and A.nds.frame_count > f0 + 60 and not A._ds_chal_paused,
      (A.chal_wait, A.nds.frame_count - f0))
check("rules engine + referee loaded", A.sl is not None and A.referee is not None and A.sl.partner == B.uid)
check("in the run Exp Share All is ON (the run's rule; my own setting is off)",
      A.exp_share_on() and not cfg.get("exp_share_all") and A.chal_rec["rules"]["exp_share"] is True)
check("no PC database / trades in the challenge", A.pc_codec is None and not A.pcsync.session_open)
A.toggle_panel("soullink")
shot("4_soullink_panel.png")
A.close_panel()

# 3. partner's connection drops, comes back -------------------------------------------------------------------
B.net.stop()
run(600, lambda: A.chal_wait == "lost")
f1 = A.nds.frame_count
run(30)
check("partner lost: paused, DS sound paused", A.chal_wait == "lost" and A.nds.frame_count == f1
      and A._ds_chal_paused)
shot("5_partner_lost.png")
B = Partner("Alex", B.uid)
run(300, lambda: B.online)
B.send(type="challenge_join", challenge_id=cid)
run(300, lambda: A.chal_wait is None)
run(30)
check("partner back: runs again", A.chal_wait is None and A.nds.frame_count > f1)
check("still no in-game save in the run (blank battery)", set(A.nds.export_save()) == {0xFF})

# 4. pause & go back --------------------------------------------------------------------------------------------
before = ram_hash()
A.leave_to_main("paused")
check("pausing sends me home", A.quit_requested and A.leave_challenge)
A = restart(A)
check("main game: my own Exp Share setting (off) again", not A.exp_share_on())
check("back in the main game with the main save", not A.chal and A.cfg.save_path == main_save
      and A.nds.loaded_save and A.nds.export_save() == MAIN_SAVE)
told = run(300, lambda: B.take("challenge_partner_left", challenge_id=cid, reason="paused") is not None)
check("partner told: paused", told)
run(60)
cdir = app_dir() / "challenges" / cid
check("the run's snapshot is in its folder", (cdir / "resume.state").exists())
check("no copy of the main save in the run's folder",
      not any(p.read_bytes()[:512 * 1024] == MAIN_SAVE for p in cdir.glob("*") if p.is_file()))
check("main save on disk untouched", main_save.read_bytes() == MAIN_SAVE)

# 5. continue together ------------------------------------------------------------------------------------------
B.send(type="challenge_request", to=cfg["uid"], challenge_id=cid)
run(300, lambda: A.prompt and A.prompt.get("kind") == "challenge" and A.prompt.get("continue") == cid)
check("continue invite shown", bool(A.prompt and A.prompt.get("continue") == cid))
shot("6_continue_invite.png")
A.respond_challenge(True)
run(300, lambda: A.enter_challenge is not None)
A = restart(A)
check("same run, resumed exactly from the DS snapshot", A.chal and A.chal["id"] == cid and ram_hash() == before)
B.send(type="challenge_join", challenge_id=cid)
run(300, lambda: A.chal_wait is None)
check("running again", A.chal_wait is None)
run(60)

# 6. partner closes the game -----------------------------------------------------------------------------------
B.send(type="challenge_leave", challenge_id=cid, reason="closed the game")
run(300, lambda: A.mode == "chal_popup")
check("pop-up: partner closed the game", A.mode == "chal_popup" and "closed the game" in str(A.chal_popup["lines"]))
shot("7_partner_left.png")
A.render()
ok = next((cb for _, cb, lab in A.click_targets if lab == "OK"), None)
check("pop-up has an OK button", ok is not None)
ok()
check("OK sends me home", A.quit_requested and A.leave_challenge)
A = restart(A)
check("home again, main save", not A.chal and A.nds.export_save() == MAIN_SAVE)
check("no stale active_challenge in config.json", "active_challenge" not in Config(cfg.path))
run(30)
A.shutdown()
check("main save on disk untouched at the end", main_save.read_bytes() == MAIN_SAVE)
check("DeSmuME's .dsv blank after closing", set(dsv.read_bytes()[:512 * 1024]) == {0xFF})
B.net.stop()
print("\nscreens in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
