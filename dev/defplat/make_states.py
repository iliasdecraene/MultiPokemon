"""Build the Definitive Platinum dev fixtures (dev/states/dp_*, gitignored) - the counterparts of the vanilla pt_*.

Usage: python dev/defplat/make_states.py [--force] [name ...]      (names: room saved title starter pc pc_open town
       battle; default = the missing ones; --force rebuilds them)

How (everything runs the real games; one DeSmuME, the two ROMs opened one after the other):
  dp_room.dsst      a real NEW GAME on Definitive Platinum (BigNation's intro, Rowan, boy, name LUCAS typed on the
                    keyboard, rival "Barry" from the list) up to the bedroom after the TV scene, idle. Random TID/SID.
  dp_saved.sav/.dsv the vanilla battery pt_saved.sav (LUCAS 33356/2998, TURTWIG, Twinleaf) CONTINUE'd in Definitive
                    Platinum and saved again IN Definitive Platinum (start menu > SAVE) - a battery the hack wrote.
  dp_title.dsst     power-on with dp_saved.sav, intro skipped with START, "PRESS START" title screen.
  dp_starter.dsst   CONTINUE from dp_saved.sav: Twinleaf Town (116,887), idle.
  dp_pc.dsst        vanilla pt_pc saved in-game in VANILLA Platinum (start menu > SAVE), that battery CONTINUE'd in
                    Definitive Platinum: Sandgem Pokémon Center at the PC (12,4) facing up (party = the 2 TURTWIG).
  dp_pc_open.dsst   dp_pc > A x6 (SOMEONE'S PC > DEPOSIT): the box UI is open.
  dp_town.dsst      vanilla pt_town saved in-game in vanilla (its first save), CONTINUE'd in Definitive Platinum:
                    Twinleaf (116,887), no Pokémon yet.
  dp_battle.dsst    dp_starter, walked north into Route 201's tall grass until a wild battle; its command menu.
The two "vanilla save -> Definitive" fixtures are the compatibility check the hack's ReadMe promises (vanilla
saves keep working); dp_saved.sav is also CONTINUE'd in vanilla Platinum to check the other direction.
Prints PASS/FAIL per step; exit code 1 if any step failed.
"""
import os
import shutil
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yl_dpstates_")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "dev", "platinum"))
sys.path.insert(0, os.path.join(REPO, "client"))

from ds import DS, KEYS  # noqa: E402
from desmume.controls import keymask  # noqa: E402
from ptrom import STATES, VARIANTS  # noqa: E402
from yellowlink import platinum as P  # noqa: E402

VANILLA_ROM = os.path.join(STATES, VARIANTS["vanilla"][0])
DEFPLAT_ROM = os.path.join(STATES, VARIANTS["defplat"][0])
TMP = os.environ["XDG_CONFIG_HOME"]
RESULTS = []
FORCE = "--force" in sys.argv


def ok(cond, what):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    return bool(cond)


def st(name):
    return os.path.join(STATES, name)


class Rig:
    def __init__(self):
        self.d = DS(DEFPLAT_ROM)
        self.rom = DEFPLAT_ROM
        self.pm = P.PlatinumMemory(self.d)

    def open(self, rom):
        if rom != self.rom:
            self.d.emu.open(os.path.abspath(rom))
            self.d.emu.volume_set(0)
            self.rom = rom

    def load(self, rom, name):
        self.open(rom)
        self.d.load(st(name))
        self.d.run(2)

    def boot(self, rom, battery):
        """Power on `rom` with a raw 512 KiB battery image."""
        self.open(rom)
        self.d.emu.backup.import_file(os.path.abspath(battery))
        self.d.emu.reset()

    def export(self, path):
        assert self.d.emu.backup.export_file(os.path.abspath(path)), "battery export failed"
        with open(path, "rb") as f:
            return f.read()

    def run(self, n):
        self.d.run(n)

    def press(self, key, after=20, hold=6):
        m = keymask(KEYS[key])
        self.d.emu.input.keypad_add_key(m)
        self.d.run(hold)
        self.d.emu.input.keypad_rm_key(m)
        self.d.run(after)

    def touch(self, x, y, hold=6, after=20):
        self.d.emu.input.touch_set_pos(int(x), int(y))
        self.d.run(hold)
        self.d.emu.input.touch_release()
        self.d.run(after)

    def wait(self, cond, n):
        for i in range(n):
            if cond():
                return i
            self.d.run(1)
        return None

    def settle(self, n=3000, frames=30):
        """Wait (no input) until the player has control for `frames` frames in a row."""
        good = 0
        for i in range(n):
            good = good + 1 if self.pm.is_safe() else 0
            if good >= frames:
                return i
            self.d.run(1)
        return None

    def step(self, direction):
        """One tile; False if blocked or a script/battle started."""
        pm = self.pm
        m = keymask(KEYS[direction])
        p0 = pm.position()
        self.d.emu.input.keypad_add_key(m)
        moved = False
        for _ in range(24):
            self.d.run(1)
            p = pm.position()
            if (p["x"], p["y"]) != (p0["x"], p0["y"]):
                moved = True
                break
        self.d.emu.input.keypad_rm_key(m)
        for _ in range(40):
            if not pm.walking():
                break
            self.d.run(1)
        return moved and not pm.field_task() and not pm.in_battle()

    def save_in_game(self, downs):
        """Start menu > SAVE (`downs` presses from where the cursor starts) > YES (> overwrite YES) and wait until
        the player has control again. True if the game wrote its battery."""
        pm = self.pm
        had = pm.has_save()
        before = self.export(os.path.join(TMP, "before.sav"))
        self.press("x", 30)
        for _ in range(downs):
            self.press("down", 12)
        self.press("a", 60)          # "Would you like to save the game?"
        self.press("a", 60)          # YES
        if had:
            self.press("a", 60)      # "There is already a saved file. Is it OK to overwrite it?" YES
        if self.settle(4000) is None:
            return False
        self.run(120)
        return self.export(os.path.join(TMP, "after.sav")) != before


# ---------------------------------------------------------------------------------------------------------
def build_room(rig):
    """NEW GAME on Definitive Platinum with a blank battery, up to the bedroom (input recorded by hand, with
    screenshots: BigNation's intro texts, Rowan, the touch-screen tutorial, gender, keyboard, rival list)."""
    blank = os.path.join(TMP, "blank.sav")
    with open(blank, "wb") as f:
        f.write(b"\xff" * 0x80000)
    rig.boot(DEFPLAT_ROM, blank)
    pm = rig.pm

    def px(x, y):
        return rig.d.emu.screenshot().getpixel((x, y))[:3]

    def yes_no():                     # red YES button on the touch screen
        r, g, b = px(120, 192 + 80)
        return r > 230 and g < 110 and b < 110

    def ball():                       # "touch the button on the Poké Ball": white lower half, no keyboard
        return px(128, 192 + 150) == (248, 248, 248)

    def keyboard():                   # name entry: pink keyboard background
        r, g, b = px(128, 192 + 150)
        return r > 240 and 185 < g < 215 and 165 < b < 200

    def menu():                       # a list box over Rowan / the rival (white)
        return px(60, 50) == (248, 248, 248)

    def press_until(cond, n=200, after=24):
        for _ in range(n):
            if rig.wait(cond, after) is not None:
                return True
            rig.press("a", 0)
        return rig.wait(cond, 60) is not None

    rig.run(400)
    rig.press("start", 60)
    rig.press("start", 60)
    rig.press("a", 300)               # no save: the main menu goes straight into the new-game intro
    # BigNation's texts, Rowan, "Would you like to know...?" (A = CONTROL INFO), its pages, then "touch a button"
    ok(press_until(yes_no), "new game: intro texts -> 'Please touch a button' YES/NO")
    rig.run(60)
    rig.touch(120, 80, hold=30, after=100)     # YES -> "Would you like to know more about anything else?"
    rig.press("a", 100)
    rig.press("a", 100)
    rig.press("down", 20)
    rig.press("down", 20)
    rig.press("a", 60)                # NO INFO NEEDED
    ok(press_until(ball), "new game: 'Touch the Poké Ball' screen")
    rig.run(250)
    rig.touch(128, 96, hold=30, after=200)     # the button on the Poké Ball
    # Rowan's speech, "Are you a boy?" (Lucas first), "So you're a boy?" YES, "Tell me, what is your name?"
    ok(press_until(keyboard, after=40), "new game: boy -> name keyboard")
    rig.run(120)
    for _ in range(8):
        rig.press("b", 12)            # BACK: delete anything a late A typed
    for x, y in ((51, 118), (35, 136), (67, 100), (35, 100), (163, 118)):      # L U C A S
        rig.touch(x, y)
    rig.press("start", 150)           # OK
    for _ in range(10):
        rig.press("a", 100)           # "Your name is LUCAS?" YES, Rowan, "What might his name be?"
    if not menu():
        press_until(menu, 4, 100)
    if keyboard():                    # an A too many picked "New name!": type it
        for x, y in ((51, 100), (35, 100), (147, 118), (147, 118), (99, 136)):  # B A R R Y
            rig.touch(x, y)
        rig.press("start", 150)
    else:
        rig.press("down", 20)
        rig.press("a", 100)           # Barry
    for _ in range(3):
        rig.press("a", 100)           # "Barry, is it?" YES
    for _ in range(300):              # Rowan's farewell, the TV scene in the bedroom
        if rig.settle(40, 30) is not None:
            break
        rig.press("a", 4)
    p = pm.position()
    ok(pm.in_game() and pm.is_safe() and p["map"] == 415 and (p["x"], p["y"]) == (4, 6)
       and pm.player_name() == "LUCAS" and pm.gender() == 0 and pm.party_count() == 0 and not pm.has_save(),
       "dp_room: Definitive Platinum NEW GAME -> bedroom (map 415, 4,6), LUCAS, boy, no Pokémon, no save "
       "(TID %d SID %d)" % (pm.trainer_id(), pm.secret_id()))
    rig.d.save(st("dp_room.dsst"))


def build_saved(rig):
    """pt_saved.sav (vanilla) -> CONTINUE in Definitive Platinum -> save in-game there -> dp_saved.sav/.dsv."""
    rig.boot(DEFPLAT_ROM, st("pt_saved.sav"))
    r = P.fast_continue(rig.d)
    pm = rig.pm
    ok(r == "loaded" and pm.map_id() == 411 and pm.player_name() == "LUCAS" and pm.party_count() == 1,
       "vanilla battery pt_saved.sav CONTINUEs in Definitive Platinum: %s, Twinleaf, LUCAS, 1 Pokémon" % r)
    rig.settle()
    ok(rig.save_in_game(3), "Definitive Platinum saves in-game (start menu > SAVE > overwrite)")
    data = rig.export(st("dp_saved.sav"))
    with open(st("pt_saved.dsv"), "rb") as f:
        footer = f.read()[0x80000:]
    with open(st("dp_saved.dsv"), "wb") as f:
        f.write(data + footer)
    ok(len(data) == 0x80000 and data != open(st("pt_saved.sav"), "rb").read(),
       "dp_saved.sav written (512 KiB, differs from the vanilla battery); dp_saved.dsv = + DeSmuME footer")
    # the other direction: a battery Definitive Platinum wrote, CONTINUE'd in vanilla Platinum
    rig.boot(VANILLA_ROM, st("dp_saved.sav"))
    r = P.fast_continue(rig.d)
    ok(r == "loaded" and pm.map_id() == 411 and pm.player_name() == "LUCAS" and pm.party_count() == 1,
       "dp_saved.sav (written by Definitive Platinum) CONTINUEs in vanilla Platinum too: %s" % r)


def build_title(rig):
    rig.boot(DEFPLAT_ROM, st("dp_saved.sav"))
    pm = rig.pm
    rig.wait(lambda: pm.top_overlay() == P.OVERLAY_GAME_OPENING, 600)
    rig.run(120)
    rig.press("start", 90)
    rig.press("start", 300)
    ok(pm.top_overlay() == P.OVERLAY_GAME_OPENING and pm.has_save() and not pm.in_game(),
       "dp_title: Definitive Platinum title screen (overlay 77), save present")
    rig.d.save(st("dp_title.dsst"))


def build_starter(rig):
    rig.boot(DEFPLAT_ROM, st("dp_saved.sav"))
    r = P.fast_continue(rig.d)
    rig.settle()
    pm = rig.pm
    p = pm.position()
    ok(r == "loaded" and pm.is_safe() and (p["map"], p["x"], p["y"]) == (411, 116, 887) and pm.party_count() == 1,
       "dp_starter: CONTINUE from dp_saved.sav -> Twinleaf (116,887), TURTWIG, idle")
    rig.d.save(st("dp_starter.dsst"))


def from_vanilla(rig, src, downs, out, want):
    """Vanilla fixture `src` saved in-game in vanilla Platinum, CONTINUE'd in Definitive Platinum -> `out`."""
    rig.load(VANILLA_ROM, src)
    rig.settle()
    ok(rig.save_in_game(downs), "%s: saved in-game in vanilla Platinum" % src)
    sav = os.path.join(TMP, src + ".sav")
    rig.export(sav)
    rig.boot(DEFPLAT_ROM, sav)
    r = P.fast_continue(rig.d)
    rig.settle()
    pm = rig.pm
    p = pm.position()
    got = (p["map"], p["x"], p["y"], p["facing"], pm.party_count())
    ok(r == "loaded" and pm.is_safe() and got == want,
       "%s: that vanilla battery CONTINUEs in Definitive Platinum: %s (map, x, y, facing, party) = %s" % (out, r, got))
    rig.d.save(st(out))


def build_pc(rig):
    from_vanilla(rig, "pt_pc.dsst", 0, "dp_pc.dsst", (420, 12, 4, P.DIR_UP, 2))


def build_town(rig):
    from_vanilla(rig, "pt_town.dsst", 2, "dp_town.dsst", (411, 116, 887, P.DIR_DOWN, 0))


def build_pc_open(rig):
    rig.load(DEFPLAT_ROM, "dp_pc.dsst")
    pm = rig.pm
    for _ in range(6):
        rig.press("a", 40)
    i = rig.wait(pm.pc_open, 300)
    rig.run(120)
    ok(i is not None and pm.pc_open(), "dp_pc_open: SOMEONE'S PC > DEPOSIT opens the box UI")
    rig.d.save(st("dp_pc_open.dsst"))


def build_battle(rig):
    """dp_starter -> north to Route 201 (vanilla pt_battle was at (124,853)) -> back and forth in the grass."""
    rig.load(DEFPLAT_ROM, "dp_starter.dsst")
    pm = rig.pm
    route = [("up", 6), ("left", 1), ("up", 14), ("right", 9), ("up", 12)]
    for d, n in route:
        for _ in range(n):
            if not rig.step(d):
                break
    p = pm.position()
    print("     at map %d (%d,%d)" % (p["map"], p["x"], p["y"]), flush=True)
    found = None
    for k in range(400):
        for d in ("left", "right"):
            if not rig.step(d) and rig.wait(pm.in_battle, 300) is not None:
                found = k
                break
        if found is not None:
            break
    menu = rig.wait(lambda: pm.in_battle() and rig.d.emu.screenshot().getpixel((128, 192 + 90))[0] > 180
                    and rig.d.emu.screenshot().getpixel((128, 192 + 90))[1] < 80, 3000)
    rig.run(20)
    bi = pm.battle_info() or {}
    ok(found is not None and menu is not None and pm.map_id() == 342 and bi.get("kind") == "wild",
       "dp_battle: wild battle on Route 201 (map %d), command menu shown: dex %s Lv%s"
       % (pm.map_id(), bi.get("dex"), bi.get("level")))
    rig.d.save(st("dp_battle.dsst"))


BUILDERS = [("room", "dp_room.dsst", build_room), ("saved", "dp_saved.sav", build_saved),
            ("title", "dp_title.dsst", build_title), ("starter", "dp_starter.dsst", build_starter),
            ("pc", "dp_pc.dsst", build_pc), ("pc_open", "dp_pc_open.dsst", build_pc_open),
            ("town", "dp_town.dsst", build_town), ("battle", "dp_battle.dsst", build_battle)]


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    rig = Rig()
    for key, out, fn in BUILDERS:
        if names and key not in names:
            continue
        if not names and not FORCE and os.path.exists(st(out)):
            print("skip %s (exists)" % out, flush=True)
            continue
        print("building %s" % out, flush=True)
        fn(rig)
    bad = RESULTS.count(False)
    print("%d checks, %d failed" % (len(RESULTS), bad) if bad else "ALL PASS (%d checks)" % len(RESULTS))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
