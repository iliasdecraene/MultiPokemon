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
        i = self.wait(self.menu_shown, n, on)
        if i is not None:
            self.run(20, on)                                # drawn a few frames before it takes input
        return i

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
    """mon_info of the party as party_checked() reads it (None while the game is mid-update)."""
    for _ in range(10):
        p = pm.party_checked()
        if p is not None:
            return [P.mon_info(r) for r in p]
        pm.m.run(1)
    return None


def party_keys(pm):
    return [i["key"] for i in party_infos(pm)]


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
# checks
# ---------------------------------------------------------------------------------------------------------
def check_areas(rig):
    import gen_soullink_areas as gen
    from yellowlink import platinum_areas as A
    pret = os.environ.get("YL_PRET_PLATINUM", gen.PRET)
    if os.path.isdir(pret):
        ok(gen.generate(pret, ROM) == open(os.path.join(HERE, "..", "..", "client", "yellowlink",
                                                        "platinum_areas.py"), encoding="utf-8").read(),
           "platinum_areas.py = output of gen_soullink_areas.py (pret + ROM names)")
        rows = gen.parse(pret)
        ok(all(P.LOCATIONS[P.MAP_LOCATION[i]][0] == key for i, _n, _lab, key in rows) and len(rows) == 593,
           "pret labels of all 593 maps = platinum.MAP_LOCATION (= the ROM's sMapHeaders, verify.py)")
    else:
        note("pret checkout not found (%s): generator comparison skipped" % pret)
    labels = rig.d.read(P.MAP_HEADERS, 24 * P.NUM_MAP_HEADERS)
    same = {}
    for i in range(P.NUM_MAP_HEADERS):
        same.setdefault(P.area_of(i), set()).add(labels[24 * i + 18])
    ok(len(A.AREAS) == 593 and all(P.area_name(a) for a in A.AREAS),
       "593 maps, every area has a name (%d areas)" % len(set(A.AREAS)))
    expect = {342: "route_201", 343: "route_202", 411: "twinleaf_town", 415: "twinleaf_town",
              420: "sandgem_town", 422: "sandgem_town", 437: "solaceon_town", 59: "oreburgh_city",
              11: "jubilife_city", 207: "mt_coronet", 219: "mt_coronet", 202: "eterna_forest", 203: "eterna_forest",
              357: "lost_tower", 356: "route_209", 504: "great_marsh", 509: "great_marsh", 187: "pokemon_league",
              2: "underground", 466: "union_room", 345: "route_204", 346: "route_204", 362: "route_210",
              363: "route_210", 492: "route_210", 288: "iron_island", 294: "iron_island"}
    ok(all(P.area_of(k) == v for k, v in expect.items()),
       "area_of: Route 201/202, Twinleaf + house, Sandgem + lab, Day Care -> Solaceon Town, Mining Museum -> "
       "Oreburgh City, Jubilife TV -> Jubilife City, Mt. Coronet floors, Eterna Forest in/out, Lost Tower, "
       "Great Marsh 1-6, Route 204/210 halves (+ Cafe), Iron Island floors")
    ok(P.area_name("route_201") == "Route 201" and P.area_name("mt_coronet") == "Mt. Coronet"
       and P.area_name("lost_tower") == "Lost Tower" and P.area_name("route_201*2") == "Route 201"
       and P.area_name(P.area_of(437)) == "Solaceon Town" and P.area_of(9999) == "map_270f",
       "area_name: ROM names (Mt. Coronet), extras (Lost Tower), shiny-clause keys, unknown ids")
    merged = {a: sorted(v) for a, v in same.items() if len(v) > 1}
    note("areas joining several ROM labels (buildings into their town): %s" % merged)


def battle_trace(rig, frames, log, on_extra=None):
    """Run frames, logging (frame info, slot) once per frame."""
    pm = rig.pm
    for _ in range(frames):
        log.append((pm.battle_info(), pm.battle_party_slot(), pm.in_battle()))
        if on_extra:
            on_extra()
        rig.d.run(1)


def check_tutorial(rig):
    rig.load("pt_sl_tutorial.dsst")
    pm = rig.pm
    infos, slots = set(), set()
    balls0 = dict(pm.balls())

    def on():
        i = pm.battle_info()
        infos.add((i or {}).get("kind"))
        if i:
            infos.add(("dex", i["dex"], i["battle_type"]))
        slots.add(pm.battle_party_slot())
    while pm.in_battle():
        on()
        rig.press("a", 14, on)
    ok(pm.battle_kind() is None and {"special", ("dex", SPECIES["bidoof"], P.BATTLE_TYPE_CATCH_TUTORIAL)} <= infos
       and None not in infos - {None} and slots == {None},
       "Dawn's catching tutorial: kind 'special' on every battle frame (battle_type 0x400), dex BIDOOF, "
       "battle_party_slot() None throughout (%s)" % sorted(map(str, infos)))
    for _ in range(80):
        if pm.is_safe():
            break
        rig.press("a", 20)
    b = dict(pm.balls())
    ok(b.get(4, 0) == balls0.get(4, 0) + 5 and pm.has_poke_balls(),
       "after the tutorial: Dawn's 5 POKé BALL arrive in the ball pocket (body+0x630+0x6BC): %s" % pm.balls())


def check_wild(rig):
    """Several encounters in a row from the same grass: info valid from the battle app's first frame, constant
    until the battle ends, equal to the BattleContext's enemy mon, never the previous battle's mon."""
    rig.load("pt_sl_route202.dsst")
    pm = rig.pm
    prev_key = None
    seen = []
    for n in range(6):
        before = []
        k = rig.walk_to_battle(on=lambda: before.append(pm.battle_info()))
        first = pm.battle_info()
        log = []
        rig.wait_menu(on=lambda: log.append((pm.battle_info(), pm.battle_party_slot())))
        species, pid, hp, _slot = battle_mon(rig, 1)
        infos = [i for i, _s in log] + [first]
        ok(k is not None and all(i is None for i in before) and first is not None
           and all(i == first for i in infos) and first["kind"] == "wild" and first["battle_type"] == 0
           and first["dex"] == species and first["pid"] == pid and first["key"] != prev_key
           and first["map"] == MAP_ROUTE_202 and 1 <= first["level"] <= 100,
           "wild #%d: battle_info None before the battle app, then from its first frame on %s (%d frames to "
           "the menu, = BattleContext enemy: species %d pid %08x), never the previous mon"
           % (n + 1, {x: first[x] for x in ("dex", "level", "shiny", "key")}, len(log), species, pid))
        slots = [s for _i, s in log]
        firsts = [i for i, s in enumerate(slots) if s is not None]
        ok(firsts and all(s is None for s in slots[:firsts[0]]) and all(s == (0, slots[-1][1]) for s in
                                                                     slots[firsts[0]:]),
           "wild #%d: battle_party_slot() None until the battle mon is loaded (frame %s), then %s"
           % (n + 1, firsts[0] if firsts else None, slots[-1]))
        seen.append(first["dex"])
        prev_key = first["key"]
        rig.run_away()
        ok(pm.battle_info() is None and pm.battle_kind() is None and pm.battle_party_slot() is None and pm.is_safe(),
           "wild #%d: ran away -> battle_info/kind/slot None" % (n + 1))
    note("species met: %s" % seen)


def check_catch(rig):
    rig.load("pt_sl_wild.dsst")
    pm = rig.pm
    info = pm.battle_info()
    n0 = pm.party_count()
    masters = dict(pm.balls()).get(1, 0)
    log = []
    rig.throw(on=lambda: log.append((pm.battle_info() is not None, pm.party_count())))
    rig.finish(on=lambda: log.append((pm.battle_info() is not None, pm.party_count())))
    end = next(i for i, (b, _n) in enumerate(log) if not b)
    grow = next(i for i, (_b, n) in enumerate(log) if n == n0 + 1)
    keys = party_keys(pm)
    mi = party_infos(pm)[-1]
    ok(pm.party_count() == n0 + 1 and keys[-1] == info["key"] and mi["dex"] == info["dex"],
       "catch (MASTER BALL): the new party mon has key %s = battle_info()['key'] and dex %d" % (keys[-1], mi["dex"]))
    ok(0 < grow - end <= 4 and all(b for b, _n in log[:end]),
       "battle_info() stays valid until the battle app ends; the field copies the party back %d frames later "
       "(referee waits END_FRAMES=8 before reading the party)" % (grow - end))
    ok(dict(pm.balls()).get(1, 0) == masters - 1, "the MASTER BALL left the ball pocket")


def check_trainer(rig):
    rig.load("pt_sl_route202.dsst")
    pm = rig.pm
    rig.teleport(182, 821)
    rig.step("left")
    for _ in range(100):
        if pm.in_battle():
            break
        rig.press("a", 10)
    info = pm.battle_info()
    ok(info is not None and info["kind"] == "trainer" and info["battle_type"] == P.BATTLE_TYPE_TRAINER
       and info["dex"] == SPECIES["bidoof"] and pm.battle_kind() == "trainer",
       "Lass Natalie (Route 202): kind 'trainer', battle_type 1, dex = her BIDOOF: %s"
       % {x: info and info[x] for x in ("kind", "dex", "level")})
    rig.wait_menu()
    ok(pm.battle_party_slot() == (0, battle_mon(rig, 0)[2]), "trainer battle: battle_party_slot (0, hp)")
    rig.save("pt_sl_trainer.dsst")


def faint_lead(rig, on=None, limit=25):
    """At the command menu: lead's battle HP -> 1 (RAM), then WITHDRAW until it faints. Returns True once
    battle_party_slot() reported HP 0."""
    pm = rig.pm
    set_battle_hp(rig, 0, 1)
    seen = []

    def watch():
        s = pm.battle_party_slot()
        seen.append(s)
        if on:
            on(s)
    for _ in range(limit):
        rig.use_move(1, watch)                       # WITHDRAW (no damage dealt -> the wild mon stays)
        for i in range(800):
            watch()
            rig.d.run(1)
            if seen[-1] and seen[-1][1] == 0:
                return True
            if i > 150 and rig.menu_shown():
                break
    return False


def check_faint(rig):
    """Party of 2: the lead faints (HP 0 reported live), the next mon is sent out: (1, hp) - never (1, 0)."""
    rig.load("pt_sl_caught.dsst")
    pm = rig.pm
    rig.walk_to_battle()
    rig.wait_menu()
    rig.save("pt_sl_faint.dsst")
    log = []
    fainted = faint_lead(rig, log.append)
    zero = [i for i, s in enumerate(log) if s == (0, 0)]
    note("slots before the faint: %s" % sorted(set(map(str, log[:zero[0] if zero else None]))))
    ok(fainted and len(zero) >= 2 and zero[1] == zero[0] + 1 and all(s in (None, (0, 1)) for s in log[:zero[0]]),
       "lead at 1 HP: battle_party_slot() (0, 1) ... then (0, 0) on consecutive frames when it faints")
    rig.run(300)
    rig.press("a", 80)                              # "Use next Pokémon"
    rig.press("a", 80)
    rig.touch(190, 30, 60)                          # party slot 2
    log2 = []
    rig.touch(128, 100, 0)                          # SHIFT
    rig.run(400, lambda: log2.append(pm.battle_party_slot()))
    after = [s for s in log2 if s is not None and s != (0, 0)]
    hp1 = party_infos(pm)[1]["hp"]
    ok(after and all(s == (1, hp1) for s in after) and (1, 0) not in log2,
       "switch-in after the faint: (0, 0) -> None (1 frame: slot index written before the battle mon) -> (1, %d); "
       "never (1, 0)" % hp1)
    rig.run_away()
    party = party_infos(pm)
    ok(party[0]["hp"] == 0 and party[1]["hp"] > 0, "after the battle the save party has the fainted lead at HP 0")


def check_blackout(rig):
    """Party of 1 faints: the battle ends, the field copies the party (HP 0) back 2 frames later and only heals
    it at the Pokémon Center hundreds of frames later -> the referee's blackout check (party read 8 frames after
    the battle) sees HP 0."""
    rig.load("pt_sl_wild.dsst")
    pm = rig.pm
    ok(faint_lead(rig), "lone lead faints (HP 0 reported live)")
    log = []

    def on():
        mi = P.mon_info(pm.party_raw()[0]) if pm.party_raw() else None
        log.append((pm.battle_info() is not None, mi and mi["hp"], pm.map_id()))
    for _ in range(300):
        if pm.is_safe():
            break
        on()
        rig.press("a", 30, on)
    end = next(i for i, e in enumerate(log) if not e[0])
    zero = [i for i, e in enumerate(log) if i >= end and e[1] == 0]
    healed = next((i for i, e in enumerate(log) if zero and i > zero[0] and e[1]), None)
    ok(zero and zero[0] - end <= 4 and healed and healed - end > 100 and log[end + 8][1] == 0,
       "blackout: party HP 0 from %d frames after battle_info() turns None until the heal %d frames later "
       "(map %d -> %d, the Pokémon Center)" % (zero[0] - end, healed - end, log[0][2], log[-1][2]))


def check_party6(rig):
    """Catches with a full party go to the PC: current box (the game switches it to the first box with room)."""
    rig.load("pt_sl_party6.dsst")
    pm = rig.pm
    infos = party_infos(pm)
    box = pm.current_box_records()
    ok(pm.party_count() == 6 and len(box) == 2 and pm.current_box() == 0 and all(i["hp"] > 0 for i in infos),
       "pt_sl_party6: 6 in the party, 2 catches in box 1 (current box 0)")
    keys = [P.mon_key(r) for r in box]
    ok(len(set(keys + [i["key"] for i in infos])) == 8, "8 different mon_keys")
    rig.walk_to_battle()
    info = pm.battle_info()
    rig.wait_menu()
    rig.throw()
    rig.finish()
    box2 = pm.current_box_records()
    ok(pm.party_count() == 6 and len(box2) == 3 and P.mon_key(box2[-1]) == info["key"] and pm.current_box() == 0,
       "a catch with a full party: stored in the current box's first free slot, key = battle_info()['key']")


def game_deposit(rig, slot):
    """Bill's... SOMEONE'S PC -> DEPOSIT -> party slot -> STORE -> the box shown (current). Party grid in the
    deposit screen: two columns (slot 1 left, 2 right, 3 left ...)."""
    pm = rig.pm
    for _ in range(6):
        rig.press("a", 40)
    rig.wait(pm.pc_open, 300)
    rig.run(120)
    if slot % 2:
        rig.press("right", 20)
    for _ in range(slot // 2):
        rig.press("down", 20)
    rig.press("a", 30)          # menu: STORE
    rig.press("a", 40)
    rig.press("a", 60)          # the box shown
    rig.run(60)
    close_pc(rig)


def close_pc(rig):
    pm = rig.pm
    for _ in range(12):
        if pm.is_safe():
            break
        rig.press("b", 40)
    rig.settle(300)


def game_withdraw(rig, pos):
    """SOMEONE'S PC -> WITHDRAW -> box slot `pos` (6 columns) -> WITHDRAW."""
    pm = rig.pm
    for _ in range(5):
        rig.press("a", 40)
    rig.press("down", 10)
    rig.press("a", 30)
    rig.wait(pm.pc_open, 300)
    rig.run(200)
    for _ in range(pos % 6):
        rig.press("right", 15)
    for _ in range(pos // 6):
        rig.press("down", 15)
    rig.press("a", 30)          # menu: WITHDRAW
    rig.press("a", 60)
    rig.run(60)
    close_pc(rig)


# ---------------------------------------------------------------------------------------------------------
SECTIONS = [("areas", check_areas), ("tutorial", check_tutorial), ("wild", check_wild), ("catch", check_catch),
            ("trainer", check_trainer), ("faint", check_faint), ("blackout", check_blackout),
            ("party6", check_party6)]


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
