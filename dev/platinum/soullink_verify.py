"""Verify the Soul Link (Nuzlocke) helpers of client/yellowlink/platinum.py on the real Platinum ROM.

Needs dev/states/platinum.nds + pt_pc.dsst (dev/platinum/STATUS.md). Builds its own fixtures
(dev/states/pt_sl_*.dsst; missing ones are rebuilt, --rebuild rebuilds all) and prints PASS/FAIL per check,
ending with ALL PASS.            Usage: python dev/platinum/soullink_verify.py [--rebuild] [section ...]
Sections: rom areas tutorial wild catch trainer faint blackout party6 boxmoves daycare double evolve hof
See dev/notes/soullink_platinum.md for what each check proves. Runs in a private XDG_CONFIG_HOME (DeSmuME's own
battery file is not touched). RAM writes used to save time are listed in the notes ("Fixtures").
"""
import os
import struct
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="ylpt_slverify_")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "client"))

from ds import DS, ROM, KEYS  # noqa: E402
from desmume.controls import keymask  # noqa: E402
from yellowlink import ndsrom, pk4  # noqa: E402
from yellowlink import platinum as P  # noqa: E402

STATES = os.path.join(HERE, "..", "states")
# ds.DS has no bulk write; PlatinumMemory's forced moves need bus.write(addr, data) like nds_core.NDSCore
DS.write = lambda self, a, data: self.emu.memory.unsigned.__setitem__(slice(a, a + len(data), 1), bytes(data))
RESULTS = []
REBUILD = "--rebuild" in sys.argv

# pret constants used only here (generated/vars_flags.txt, trainers.txt, map_headers.txt)
FLAG_HAS_POKEDEX = 144
FLAG_RECEIVED_PARCEL = 143
TRAINER_CHERYL_ETERNA_FOREST = 608
MAP_SANDGEM_POKECENTER = 420
MAP_ROUTE_202 = 343
MAP_DAY_CARE = 437
SPECIES = {"turtwig": 387, "grotle": 388, "bidoof": 399, "starly": 396}


def ok(cond, what):
    RESULTS.append((bool(cond), what))
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    return bool(cond)


def note(s):
    print("     " + s, flush=True)


def st(name):
    return os.path.join(STATES, name)


class Rig:
    def __init__(self):
        self.d = DS()
        self.rom = ndsrom.platinum(ROM)
        self.pm = P.PlatinumMemory(self.d, self.rom)
        self.frame = 0

    # --- states / time --------------------------------------------------------------------------------
    def load(self, name):
        self.d.load(st(name))
        self.d.run(2)

    def save(self, name):
        self.d.save(st(name))

    def run(self, n=1, on=None):
        for _ in range(n):
            if on:
                on()
            self.d.run(1)

    def settle(self, n=900):
        for i in range(n):
            if self.pm.is_safe():
                return i
            self.d.run(1)
        return None

    def wait(self, cond, n, on=None):
        for i in range(n):
            if cond():
                return i
            if on:
                on()
            self.d.run(1)
        return None

    # --- input ----------------------------------------------------------------------------------------
    def hold(self, key, frames, on=None):
        m = keymask(KEYS[key])
        self.d.emu.input.keypad_add_key(m)
        self.run(frames, on)
        self.d.emu.input.keypad_rm_key(m)

    def press(self, key, after=20, on=None):
        self.hold(key, 6, on)
        self.run(after, on)

    def touch(self, x, y, after=60, on=None):
        self.d.emu.input.touch_set_pos(int(x), int(y))
        self.run(6, on)
        self.d.emu.input.touch_release()
        self.run(after, on)

    def step(self, direction, n=1):
        """Walk n tiles; False as soon as a script/battle starts or the player can't move."""
        pm = self.pm
        for _ in range(n):
            m = keymask(KEYS[direction])
            self.d.emu.input.keypad_add_key(m)
            p0 = pm.position()
            for _i in range(40):
                self.d.run(1)
                p = pm.position()
                if (p["x"], p["y"], p["map"]) != (p0["x"], p0["y"], p0["map"]):
                    break
            self.d.emu.input.keypad_rm_key(m)
            for _i in range(60):
                if not pm.walking():
                    break
                self.d.run(1)
            if pm.field_task() or pm.in_battle():
                return False
        return True

    def teleport(self, x, z):
        """Move the player object to tile (x, z) of the loaded area (same map matrix) - test setup only."""
        mo = self.pm.player_object()
        self.d.w32(mo + P.MO_X, x)
        self.d.w32(mo + P.MO_Z, z)
        self.d.w32(mo + P.MO_POS, (x * 16 + 8) << 12)
        self.d.w32(mo + P.MO_POS + 8, (z * 16 + 8) << 12)
        self.d.run(2)

    def walk_to_battle(self, a="left", b="right", limit=300, on=None):
        """Walk back and forth in grass until a battle app runs; returns the number of steps or None."""
        for k in range(limit):
            for dr in (a, b):
                if not self.step(dr):
                    if self.wait(self.pm.in_battle, 600, on) is not None:
                        return k
                    return None
        return None

    # --- battle UI (touch screen) ---------------------------------------------------------------------
    def menu_shown(self):
        """The battle command menu (big red FIGHT button) waits for input."""
        px = self.d.emu.screenshot().getpixel((128, 192 + 90))
        return self.pm.in_battle() and px[0] > 180 and px[1] < 80 and px[2] < 80

    def wait_menu(self, n=3000, on=None):
        return self.wait(self.menu_shown, n, on)

    def yes_no_shown(self):
        img = self.d.emu.screenshot()
        top, bot = img.getpixel((40, 192 + 70)), img.getpixel((40, 192 + 138))
        return top[0] > 200 and top[1] < 90 and bot[2] > 180 and bot[0] < 80

    def use_move(self, slot, on=None):
        self.touch(128, 90, 60, on)                         # FIGHT
        self.touch((64, 190, 64, 190)[slot], (40, 40, 100, 100)[slot], 0, on)

    def throw(self, ball_xy=(64, 30), on=None):
        self.touch(40, 172, 60, on)                         # BAG
        self.touch(190, 55, 60, on)                         # POKé BALLS
        self.touch(*ball_xy, 60, on)                        # first ball of the pocket
        self.touch(100, 170, 10, on)                        # USE

    def run_away(self, on=None):
        for _ in range(40):
            if not self.pm.in_battle():
                break
            if self.menu_shown():
                self.touch(128, 172, 30, on)
            else:
                self.press("b", 30, on)
        return self.finish(on=on)

    def finish(self, limit=8000, on=None):
        """Press through texts, the Pokédex page and "nickname?" (answer No) until the player is in control."""
        f = 0
        while f < limit:
            if self.pm.is_safe():
                return f
            if f % 40 == 0:
                if self.yes_no_shown():
                    self.touch(128, 138, 20, on)
                    f += 26
                    continue
                self.hold("a", 6, on)
                f += 6
            else:
                self.run(1, on)
                f += 1
        return None


# ---------------------------------------------------------------------------------------------------------
# helpers on game data
# ---------------------------------------------------------------------------------------------------------
def party_infos(pm):
    return [P.mon_info(r) for r in pm.party_raw()]


def party_keys(pm):
    return [P.mon_key(r) for r in pm.party_raw()]


def body(pm):
    return pm.save_body()


def set_flag(rig, n, v=True):
    a = body(rig.pm) + P.PAGE_VARS_FLAGS + P.VF_FLAGS + n // 8
    x = rig.d.r8(a)
    rig.d.w8(a, (x | 1 << (n % 8)) if v else (x & ~(1 << (n % 8))))


def set_var(rig, n, v):
    rig.d.w16(body(rig.pm) + P.PAGE_VARS_FLAGS + 2 * (n - P.VARS_START), v)


def ctx_addr(rig):
    app = rig.pm._battle_app()
    bs = rig.d.r32(app + 0x1C)
    return bs, rig.d.r32(bs + P.BS_CTX)


def battle_mon(rig, battler):
    """(species, pid, cur hp, selected party slot) of a battler from the BattleContext."""
    _bs, cx = ctx_addr(rig)
    bm = cx + P.CTX_BATTLE_MONS + battler * P.BATTLE_MON_SIZE
    return (rig.d.r16(bm + P.BM_SPECIES), rig.d.r32(bm + P.BM_PID), rig.d.r32(bm + P.BM_CUR_HP),
            rig.d.r8(cx + P.CTX_SELECTED_SLOT + battler))


def set_battle_hp(rig, battler, hp):
    _bs, cx = ctx_addr(rig)
    rig.d.w32(cx + P.CTX_BATTLE_MONS + battler * P.BATTLE_MON_SIZE + P.BM_CUR_HP, hp)


def write_party(rig, records):
    """Replace the party with these 236-byte records (test setup)."""
    b = body(rig.pm)
    for i in range(6):
        rig.d.write(b + P.PAGE_PARTY + 8 + i * P.PARTY_MON_SIZE,
                    bytes(records[i]) if i < len(records) else pk4.EMPTY_PARTY)
    rig.d.w32(b + P.PAGE_PARTY + 4, len(records))


def snapshot_mem(rig):
    """(party page, PC boxes) bytes."""
    b = body(rig.pm)
    return (rig.d.read(b + P.PAGE_PARTY, 8 + 6 * P.PARTY_MON_SIZE),
            rig.d.read(b + P.PAGE_PC_BOXES, 4 + 18 * 30 * P.BOX_MON_SIZE))


def warp_patch(rig, dest_map, dest_warp=0):
    """Point every warp event of the current map at (dest_map, dest_warp) - the "warp trick" (test setup only):
    MapHeaderData (FieldSystem+0x14) {numBg, numObj, numWarp, numCoord, bg*, obj*, warp*, coord*}; WarpEvent =
    {u16 x, z, destHeaderID, destWarpID, u8 unused[4]}."""
    fs = rig.pm.field_system()
    mhd = rig.d.r32(fs + 0x14)
    n = rig.d.r32(mhd + 8)
    warps = rig.d.r32(mhd + 0x18)
    for i in range(n):
        rig.d.w16(warps + 12 * i + 4, dest_map)
        rig.d.w16(warps + 12 * i + 6, dest_warp)
    return n


# ---------------------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------------------
def build_route202(rig):
    """pt_pc (Sandgem Pokémon Center, Pokédex obtained) -> Route 202: Dawn's catching tutorial
    (pt_sl_tutorial.dsst: its battle just started) -> pt_sl_route202.dsst (in the tall grass after it, 10 MASTER
    BALL + Dawn's 5 POKé BALL, party = TURTWIG Lv7). RAM writes: party slot 2 (an exact copy of slot 1 in
    pt_pc) removed, 10 Master Balls put in the ball pocket, FLAG_RECEIVED_PARCEL set (the tutorial needs it;
    normally set by visiting Mom in Twinleaf)."""
    rig.load("pt_pc.dsst")
    pm, d = rig.pm, rig.d
    ok(pm.balls() == [] and not pm.has_poke_balls(), "pt_pc: ball pocket empty -> has_poke_balls() False")
    write_party(rig, pm.party_raw()[:1])
    b = body(pm) + P.PAGE_BAG + P.BAG_BALLS
    d.w16(b, 1)
    d.w16(b + 2, 10)
    rig.step("down", 3)
    rig.step("left", 4)
    rig.step("down", 6)
    rig.settle()
    rig.step("down", 1)
    rig.step("right", 6)
    rig.step("up", 10)
    rig.step("right", 4)
    rig.step("up", 12)
    ok(pm.map_id() == MAP_ROUTE_202, "walked from the Pokémon Center to Route 202 (map %d)" % pm.map_id())
    set_flag(rig, FLAG_RECEIVED_PARCEL)
    rig.step("down", 2)
    for _ in range(7):
        if not rig.step("left"):
            break
    for _ in range(200):
        if pm.in_battle():
            break
        rig.press("a", 10)
    ok(pm.in_battle(), "catching tutorial battle started (coord event at x=180)")
    rig.save("pt_sl_tutorial.dsst")
    for _ in range(3000):
        if not pm.in_battle():
            break
        rig.press("a", 14)
    for _ in range(80):
        if pm.is_safe():
            break
        rig.press("a", 20)
    rig.settle()
    ok(pm.is_safe() and pm.map_id() == MAP_ROUTE_202, "tutorial over, in the grass of Route 202")
    rig.save("pt_sl_route202.dsst")


def build_caught(rig):
    """pt_sl_route202 -> a wild battle at its command menu (pt_sl_wild.dsst) -> caught with a MASTER BALL
    (pt_sl_caught.dsst: party TURTWIG + the catch, back in the grass)."""
    rig.load("pt_sl_route202.dsst")
    rig.walk_to_battle()
    rig.wait_menu()
    rig.save("pt_sl_wild.dsst")
    rig.throw()
    rig.finish()
    rig.save("pt_sl_caught.dsst")


def build_party6(rig):
    """pt_sl_caught + 4 more MASTER BALL catches (party of 6) + 2 more that go to the PC (box 1) ->
    pt_sl_party6.dsst (Route 202, safe)."""
    rig.load("pt_sl_caught.dsst")
    for _ in range(6):
        rig.walk_to_battle()
        rig.wait_menu()
        rig.throw()
        rig.finish()
    rig.save("pt_sl_party6.dsst")


def build_pc6(rig):
    """pt_pc (at the Sandgem PC) with the party and box 1 of pt_sl_party6 copied in (RAM, records made by the
    game) -> pt_sl_pc6.dsst."""
    rig.load("pt_sl_party6.dsst")
    party = rig.pm.party_raw()
    box = rig.pm.box_raw(0)
    rig.load("pt_pc.dsst")
    write_party(rig, party)
    for i, r in enumerate(box):
        rig.pm.write_box_slot(0, i, r)
    rig.save("pt_sl_pc6.dsst")


FIXTURES = [("pt_sl_route202.dsst", build_route202), ("pt_sl_caught.dsst", build_caught),
            ("pt_sl_party6.dsst", build_party6), ("pt_sl_pc6.dsst", build_pc6)]


def fixtures(rig):
    for name, fn in FIXTURES:
        if REBUILD or not os.path.exists(st(name)):
            note("building %s" % name)
            fn(rig)


# ---------------------------------------------------------------------------------------------------------
SECTIONS = []


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    rig = Rig()
    fixtures(rig)
    for name, fn in SECTIONS:
        if not names or name in names:
            print("--- %s" % name, flush=True)
            fn(rig)
    bad = [w for c, w in RESULTS if not c]
    print("%d checks, %d failed" % (len(RESULTS), len(bad)))
    if bad:
        for w in bad:
            print("FAILED: " + w)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
