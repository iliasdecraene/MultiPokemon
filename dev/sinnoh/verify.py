"""Complete Sinnoh on the real ROM: the built ROM, old snapshots, wild encounters on edited maps, the four events.

    python dev/sinnoh/verify.py [rom] [compat] [wild] [events]     (default: all)
    YL_SINNOH_SPEC=test python dev/sinnoh/verify.py                (with the tiny test spec instead of the real one)
    YL_SINNOH_MAPS=8 / YL_SINNOH_TRIALS=24                         (how many edited maps / encounters per map)

Needs dev/states/platinum.nds + pt_title.dsst, pt_town.dsst, pt_pc.dsst, pt_battle.dsst, pt_sl_route202.dsst.
Builds dev/states/platinum-sinnoh*.nds itself (dev/sinnoh/build_rom.py). Never writes into the fixtures.

Test shortcuts (RAM / battery edits, all through dev/sinnoh/devwarp.py, said again where used):
  * story progress for the events (FLAG_GAME_COMPLETED, National Dex, the Canalave boy woken up, Rotom in the party,
    the Route 224 tablet written ...) and the place to start (the game's own Communication-Club return path);
  * for wild encounters: the loaded table's land rate raised to 100 in RAM (rate only - the slots, species and
    levels are the ROM's) and the field RNG (sLCRNGState 0x021BFB14) seeded differently for every try.
Everything else - loading the table from the ROM, rolling the slot, the event scripts, the legendary's battle - is
the game itself.
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "client"))
sys.path.insert(0, os.path.join(ROOT, "dev", "platinum"))
sys.path.insert(0, HERE)
os.environ.setdefault("YL_PT_ROM", "sinnoh")
if os.environ["YL_PT_ROM"] != "sinnoh":
    sys.exit("dev/sinnoh/verify.py runs the Complete Sinnoh ROM (YL_PT_ROM=sinnoh)")

import ptrom  # noqa: E402
from ds import DS  # noqa: E402
import build_rom  # noqa: E402
import devwarp  # noqa: E402
import walker  # noqa: E402
import worldmap  # noqa: E402
from yellowlink import encdata, ndsrom, platinum as P, sinnoh_events as E, sinnoh_rom  # noqa: E402

LCRNG_STATE = 0x021BFB14          # sLCRNGState (math_util.c; ARM9 .bss, LCRNG_Next at 0x0201D2E9 stores it at +4)
RESULTS = []


SHOTS = os.environ.get("YL_SINNOH_SHOTS")       # folder: a screenshot of both screens for every failed check
_DS = []


def ok(cond, what):
    RESULTS.append((bool(cond), what))
    print(("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond and SHOTS and _DS:
        os.makedirs(SHOTS, exist_ok=True)
        _DS[0].shot(os.path.join(SHOTS, "fail%02d.png" % len(RESULTS)))
    return cond


def spec():
    return sinnoh_rom.load_spec()


# --------------------------------------------------------------------------------------------------- ROM
def check_rom():
    print("== ROM:", ptrom.ROM)
    sp = spec()
    vanilla = ptrom.VANILLA_ROM
    ok(sinnoh_rom._sha1(vanilla) == sinnoh_rom.VANILLA_SHA1, "source = vanilla Platinum US rev 0")
    rv, rs = ndsrom.NDSRom(vanilla), ndsrom.NDSRom(ptrom.ROM)
    start, size = rv.extent(encdata.NARC_PATH)
    ok(rs.extent(encdata.NARC_PATH) == (start, size), "NARC at the same offset/size (%#x, %d bytes)" % (start, size))
    ok(sinnoh_rom._compare_outside(vanilla, ptrom.ROM, start, start + size),
       "the ROM differs from vanilla only inside pl_enc_data.narc")
    nv, ns = rv.narc(encdata.NARC_PATH), rs.narc(encdata.NARC_PATH)
    same = all(ns[i] == sp.apply(i, nv[i]) for i in range(encdata.TABLE_COUNT))
    ok(same, "every table = spec.apply(vanilla table) (%d slot edits, %d rate edits)"
       % (len(sp.EDITS), len(getattr(sp, "RATE_EDITS", ()))))
    changed = [i for i in range(encdata.TABLE_COUNT) if ns[i] != nv[i]]
    ok(set(changed) == {e.table for e in sp.EDITS} | {r.table for r in getattr(sp, "RATE_EDITS", ())},
       "changed tables = the spec's tables (%d)" % len(changed))
    sha = sinnoh_rom._sha1(ptrom.ROM)
    want = sinnoh_rom.expected_sha1(sp)
    if want:
        ok(sha == want, "SHA1 %s = sinnoh_rom.EXPECTED for spec v%s" % (sha[:12], sp.SPEC_VERSION))
        ok(P.variant_of(sha) is not None and P.variant_of(sha).key == "sinnoh",
           "platinum.VARIANTS lists it (App.full, same code as vanilla)")
    else:
        ok(False, "spec v%s has no EXPECTED SHA1 yet (run dev/sinnoh/build_rom.py --update)" % sp.SPEC_VERSION)
    with open(vanilla, "rb") as f:
        hv = f.read(0x200)
    with open(ptrom.ROM, "rb") as f:
        hs = f.read(0x200)
    ok(hv == hs, "header identical (game code, CRCs, FAT/FNT offsets)")
    rv.close()
    rs.close()


# ------------------------------------------------------------------------------------------- snapshot compat
def check_compat(d, pm):
    """Vanilla savestates (= a player's resume snapshot from 0.7.6 or a vanilla dev fixture) in the Complete Sinnoh
    ROM: same code, so the game runs on; the loaded wild table is vanilla's until the next map load (the app
    refreshes it at the first safe frame: write_wild_table)."""
    print("== vanilla snapshots in the Complete Sinnoh ROM")
    rs = ndsrom.NDSRom(ptrom.ROM)
    narc = rs.narc(encdata.NARC_PATH)
    for st in ("pt_town", "pt_pc", "pt_battle", "pt_sl_route202"):
        d.load(os.path.join(ptrom.STATES, st + ".dsst"))
        d.run(300)
        party = pm.party_checked()
        ok(pm.in_game() and party is not None,
           "%s: loads and runs 300 frames (in game, party readable: %d)" % (st, len(party or [])))
    d.load(os.path.join(ptrom.STATES, "pt_sl_route202.dsst"))
    d.run(30)
    for _ in range(300):
        if pm.is_safe():
            break
        d.run(1)
    tid = pm.wild_table_id()
    ram = pm.wild_table()
    ok(tid == 141 and pm.map_id() == 343, "Route 202 snapshot: map 343, wild table 141 (MapHeader +0x0E)")
    edited = narc[tid] != ram
    print("     table in RAM %s the ROM's (%s)" % ("differs from" if edited else "equals",
                                                "spec edits Route 202" if edited else "no Route 202 edit"))
    ok(pm.write_wild_table(narc[tid]) and pm.wild_table() == narc[tid],
       "write_wild_table: RAM table = the ROM's member (what the app does after resuming another build)")
    # a real map load: from Twinleaf Town (pt_starter, vanilla snapshot with a Pokémon) walk north into Route 201
    d.load(os.path.join(ptrom.STATES, "pt_starter.dsst"))
    walker.settle(d, pm, 300)
    world = worldmap.World(ptrom.ROM)
    tiles = dict(world.tiles(411, 0))
    tiles.update(world.tiles(342, 0))
    goal = worldmap.encounter_pairs(world.tiles(342, 0))[-1][:2]
    walker.walk_to(d, pm, tiles, goal, map_change_ok=True)
    walker.settle(d, pm, 300)
    t2 = pm.wild_table_id()
    vanilla_t = ndsrom.NDSRom(ptrom.VANILLA_ROM).narc(encdata.NARC_PATH)[140]
    ok(pm.map_id() == 342 and t2 == 140 and pm.wild_table() == narc[140],
       "Twinleaf -> Route 201 on foot: the game loads table 140 from the Complete Sinnoh ROM%s (map %d)"
       % (" (edited)" if narc[140] != vanilla_t else "", pm.map_id()))
    rs.close()


# -------------------------------------------------------------------------------------------- wild encounters
def maps_using(d, table):
    """[(map id, matrix id)] whose header points at `table` (sMapHeaders in RAM)."""
    raw = d.read(P.MAP_HEADERS, 24 * P.NUM_MAP_HEADERS)
    out = []
    for m in range(P.NUM_MAP_HEADERS):
        wid, = struct.unpack_from("<H", raw, 24 * m + P.MH_WILD_ENCOUNTERS_ID)
        if wid == table:
            out.append((m, struct.unpack_from("<H", raw, 24 * m + 2)[0]))
    return out


def allowed(t):
    """(species, level) a walk can give with this table (land + day/night replacements of slots 2/3)."""
    out = set(t["land"])
    for k in ("day", "night"):
        for i, sp in enumerate(t[k]):
            out.add((sp, t["land"][2 + i][1]))
    return out


def force_encounters(d, pm, w, mid, x, z, dx, trials):
    """Encounters on tile pair (x, z)/(x+dx, z). Returns [(species, level)]."""
    snap = os.path.join(w.tmp, "here.dsst")
    d.save(snap)
    seen = []
    for k in range(trials):
        d.load(snap)
        a = pm.wild_table_addr()
        d.write(a, struct.pack("<I", 100))                       # TEST SHORTCUT: land rate 100 (RAM copy only)
        d.w32(LCRNG_STATE, (0x9E3779B1 * (k + 1) + 0x1234) & 0xFFFFFFFF)   # TEST SHORTCUT: another RNG seed
        d.run(1)
        keys = ("right", "left") if dx == 1 else ("left", "right")
        for n in range(80):
            d.hold(keys[n % 2], 8, 8)
            if pm.in_battle() or pm.map_id() != mid:
                break
        bi = None
        for _ in range(240):
            bi = pm.battle_info()
            if bi:
                break
            d.run(1)
        seen.append((bi["dex"], bi["level"]) if bi and bi["kind"] == "wild" else None)
    return seen


def check_wild(d, pm):
    sp = spec()
    print("== wild encounters on edited maps")
    world = worldmap.World(ptrom.ROM)
    narc = world.rom.narc(encdata.NARC_PATH)
    w = devwarp.DevWarp(d, ptrom.STATES)
    w.boot("pt_title.dsst")
    by_table = {}
    for e in sp.EDITS:
        if e.kind in ("land", "day", "night"):
            by_table.setdefault(e.table, []).append(e)
    # spread over the spec: every n-th edited table (deterministic), skipping the Great Marsh / Trophy Garden
    tables = [t for t in sorted(by_table) if not e_name(sp, t).startswith(("great_marsh", "trophy_garden"))]
    nmaps = int(os.environ.get("YL_SINNOH_MAPS", "6"))
    trials = int(os.environ.get("YL_SINNOH_TRIALS", "24"))
    if len(tables) > nmaps:
        step = len(tables) / nmaps
        tables = [tables[int(i * step)] for i in range(nmaps)]
    for t in tables:
        name = e_name(sp, t)
        cands = maps_using(d, t)
        spot = None
        for mid, mx in cands:
            tiles = world.tiles(mid, mx)
            pairs = worldmap.encounter_pairs(tiles)
            if pairs:
                spot = (mid, mx) + pairs[len(pairs) // 2]
                break
        if spot is None:
            ok(False, "%s (table %d): no map with grass/cave tiles found (%s)" % (name, t, cands))
            continue
        mid, mx, x, z, dx = spot
        w.boot("pt_title.dsst")
        t0 = time.time()
        r = w.go(mid, x, z)
        if not ok(r == "loaded" and pm.map_id() == mid and pm.wild_table_id() == t,
                  "%s: CONTINUE'd into map %d at (%d,%d), table %d (%s)" % (name, mid, x, z, t, r)):
            continue
        rom_table = narc[t]
        ok(pm.wild_table() == rom_table, "%s: the table the game loaded = the ROM's edited member" % name)
        parsed = encdata.parse(rom_table)
        seen = force_encounters(d, pm, w, mid, x, z, dx, trials)
        got = [s for s in seen if s]
        bad = [s for s in got if s not in allowed(parsed)]
        new = {(e.species, e.level if e.kind == "land" else None) for e in by_table[t]}
        new_species = {e.species for e in by_table[t]}
        hits = sorted({s for s in got if s[0] in new_species})
        weight = sum(encdata.LAND_WEIGHTS[e.slot] if e.kind == "land" else 10 for e in by_table[t])
        print("     %d encounters in %.0fs: %s" % (len(got), time.time() - t0,
                                                   ", ".join("%d/L%d" % s for s in sorted(set(got)))))
        ok(len(got) >= trials * 3 // 4 and not bad,
           "%s: %d/%d wild battles, every species+level is in the table%s" % (name, len(got), trials,
                                                                             (" - NOT: %s" % bad) if bad else ""))
        msg = "%s: new species seen: %s (added slots weigh %d%%)" % (
            name, ", ".join("%d/L%d" % s for s in hits) or "none", weight)
        if weight >= 20:
            ok(hits, msg)
        else:
            print("     (info) " + msg)
        del new


def e_name(sp, t):
    for e in sp.EDITS:
        if e.table == t:
            return e.name
    return "table_%d" % t


# ------------------------------------------------------------------------------------------------ events
def give_events(d, pm):
    """The real EventUnlocker on the real game: nothing before the National Dex, all four at a safe frame after."""
    u = E.EventUnlocker(pm, every=1)
    got = []
    for _ in range(10):
        got += u.step()
        d.run(1)
    return got


def check_events(d, pm):
    print("== Mystery Gift events")
    w = devwarp.DevWarp(d, ptrom.STATES)
    # 1. before the National Dex nothing happens
    w.boot("pt_title.dsst")
    P.fast_continue(d)
    walker.settle(d, pm)
    ok(give_events(d, pm) == [] and not E.national_dex(pm), "no National Dex: no event is given")
    # 2. with the National Dex (TEST SHORTCUT: Pokédex flag + game completed): all four, once
    w.flag(E.FLAG_GAME_COMPLETED)
    w.national_dex()
    got = give_events(d, pm)
    ok([e.id for e in got] == [e.id for e in E.EVENTS], "National Dex: the unlocker gives %s"
       % ", ".join(e.item_name for e in got))
    ok(all(E.given(pm, e) for e in E.EVENTS) and E.pending(pm) == [], "bag + distribution vars read back")
    ok(give_events(d, pm) == [], "nothing given twice")
    bag = [i for i, q in E.key_items(pm) if q]
    ok(all(e.item in bag for e in E.EVENTS), "the four key items are in the Key Items pocket: %s" % bag)
    for name, fn in (("darkrai", event_darkrai), ("shaymin", event_shaymin), ("arceus", event_arceus),
                     ("rotom", event_rotom)):
        try:
            fn(d, pm, w)
        except Exception as ex:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            ok(False, "%s event: crashed (%s)" % (name, ex))


def setup_postgame(w):
    """TEST SHORTCUTS: Hall of Fame done + National Dex, and the events given by the real unlocker."""
    w.flag(E.FLAG_GAME_COMPLETED)
    w.national_dex()
    for ev in E.EVENTS:
        E.give(w.pm, ev)


def talk(d, pm, direction, frames=600, until=None):
    walker.face(d, pm, direction)
    d.press("a", 10)
    for i in range(frames):
        if until and until():
            return True
        if i % 20 == 0:
            d.press("a", 4)
        d.run(1)
    return bool(until and until())


def battle_species(d, pm, frames=1800):
    for i in range(frames):
        bi = pm.battle_info()
        if bi and bi.get("dex"):
            return bi
        if i % 15 == 0:
            d.press("a", 3)
        d.run(1)
    return None


def event_darkrai(d, pm, w):
    """Member Card: Canalave City -> Harbor Inn (the game opens the door, the Gym Guide's reservation, the dream)
    -> Newmoon Island -> forest -> Darkrai Lv50."""
    print("-- Darkrai")
    world = worldmap.World(ptrom.ROM)
    w.boot("pt_title.dsst")
    setup_postgame(w)
    w.flag(300)        # TEST SHORTCUT: FLAG_WOKE_UP_CANALAVE_CITY_SAILOR_ELDRITCH_HOUSE_LITTLE_BOY (Lunar Wing quest)
    CANALAVE, INN, NEWMOON, FOREST = 33, 43, 320, 321
    r = w.go(CANALAVE, 58, 716)          # in front of the Harbor Inn (door at 58,713 when the event is on)
    ok(r == "loaded" and pm.map_id() == CANALAVE, "Canalave City (CONTINUE, %s)" % r)
    tiles = world.tiles(CANALAVE, 0)
    walker.walk_to(d, pm, tiles, (58, 714))
    walker.step(d, pm, "up")
    for _ in range(600):
        if pm.map_id() != CANALAVE:
            break
        d.run(1)
    ok(pm.map_id() == INN, "the Harbor Inn door is open (map %d)" % pm.map_id())
    # the inn's OnFrame script: Gym Guide, lie down, dream, warp to Newmoon Island
    for i in range(3000):
        if pm.map_id() == NEWMOON:
            break
        if i % 30 == 0:
            d.press("a", 4)
        d.run(1)
    ok(pm.map_id() == NEWMOON, "the dream warps to Newmoon Island (VAR_DARKRAI_EVENT_STATE %d)" % pm.var(0x40F8))
    walker.settle(d, pm, 600)
    ntiles = world.tiles(NEWMOON, d.r16(P.MAP_HEADERS + 24 * NEWMOON + 2))
    walker.walk_to(d, pm, ntiles, (137, 268), map_change_ok=True)
    for _ in range(300):
        if pm.map_id() == FOREST:
            break
        walker.step(d, pm, "up")
    walker.settle(d, pm, 600)
    ok(pm.map_id() == FOREST, "walked into the Newmoon Island forest")
    ftiles = world.tiles(FOREST, d.r16(P.MAP_HEADERS + 24 * FOREST + 2))
    walker.walk_to(d, pm, ftiles, (16, 14))
    bi = None
    if talk(d, pm, "up", 400, until=pm.in_battle):
        bi = battle_species(d, pm)
    ok(bi and bi["dex"] == 491 and bi["level"] == 50, "Darkrai's battle: %s" % (
        ("species %d Lv%d" % (bi["dex"], bi["level"])) if bi else "no battle"))


def event_shaymin(d, pm, w):
    """Oak's Letter: Route 224 shows Prof. Oak (OnTransition checks the letter + distribution var and moves the
    Shaymin event on); Seabreak Path / Flower Paradise -> Shaymin Lv30."""
    print("-- Shaymin")
    world = worldmap.World(ptrom.ROM)
    ROUTE_224, FLOWER = 399, 274
    w.boot("pt_title.dsst")
    setup_postgame(w)
    r = w.go(ROUTE_224, 905, 496)
    ok(r == "loaded" and pm.map_id() == ROUTE_224, "Route 224 (%s)" % r)
    flag_oak_hidden = pm.flag(_flag("FLAG_HIDE_ROUTE_224_PROF_OAK"))
    ok(not flag_oak_hidden and pm.var(0x4057) == 2,
       "Route 224 OnTransition: Prof. Oak shown, VAR_SHAYMIN_EVENT_STATE 1 -> 2 (got %d)" % pm.var(0x4057))
    # TEST SHORTCUT: the tablet scene (name entry) is skipped: FLAG_WROTE_ON_ROUTE_224_TABLET, Seabreak Path open
    w.boot("pt_title.dsst")
    setup_postgame(w)
    w.flag(_flag("FLAG_WROTE_ON_ROUTE_224_TABLET"))
    w.var(0x4057, 2)
    r = w.go(FLOWER, 911, 206)
    ok(r == "loaded" and pm.map_id() == FLOWER, "Flower Paradise (%s)" % r)
    ok(not pm.flag(_flag("FLAG_HIDE_FLOWER_PARADISE_SHAYMIN")), "Flower Paradise OnTransition shows Shaymin")
    tiles = world.tiles(FLOWER, d.r16(P.MAP_HEADERS + 24 * FLOWER + 2))
    walker.walk_to(d, pm, tiles, (911, 204))
    bi = None
    if talk(d, pm, "up", 600, until=pm.in_battle):
        bi = battle_species(d, pm)
    ok(bi and bi["dex"] == 492 and bi["level"] == 30, "Shaymin's battle: %s" % (
        ("species %d Lv%d" % (bi["dex"], bi["level"])) if bi else "no battle"))


def event_arceus(d, pm, w):
    """Azure Flute: Spear Pillar (OnTransition enables the Hall of Origin), play the flute at (31, 52) -> the stairs
    -> Hall of Origin -> Arceus Lv80."""
    print("-- Arceus")
    world = worldmap.World(ptrom.ROM)
    HALL = _map("MAP_HEADER_HALL_OF_ORIGIN")
    SPEAR = _map("MAP_HEADER_SPEAR_PILLAR")
    w.boot("pt_title.dsst")
    setup_postgame(w)
    w.register_item(455)                 # TEST SHORTCUT: the Azure Flute on the Y button (players use the bag)
    for f in ("FLAG_HIDE_SPEAR_PILLAR_CYRUS", "FLAG_HIDE_SPEAR_PILLAR_MARS", "FLAG_HIDE_SPEAR_PILLAR_JUPITER",
              "FLAG_HIDE_SPEAR_PILLAR_GRUNT_M", "FLAG_HIDE_SPEAR_PILLAR_GRUNT_F", "FLAG_HIDE_SPEAR_PILLAR_RIVAL"):
        n = _flag(f, None)
        if n is not None:
            w.flag(n)                    # TEST SHORTCUT: the story's Spear Pillar people gone (post-game)
    r = w.go(SPEAR, 31, 52, facing=0)
    ok(r == "loaded" and pm.map_id() == SPEAR, "Spear Pillar (%s)" % r)
    ok(pm.var(0x4118) == 1, "Spear Pillar OnTransition: VAR_HALL_OF_ORIGIN_STATE = 1 (flute + distribution var)")
    d.press("y", 30)
    for i in range(2400):
        if pm.map_id() == HALL:
            break
        if i % 25 == 0:
            d.press("a", 4)
        if i > 600 and pm.is_safe():
            walker.step(d, pm, "up")
        d.run(1)
    walker.settle(d, pm, 600)
    if pm.map_id() != HALL:
        for _ in range(60):
            if pm.map_id() == HALL:
                break
            walker.step(d, pm, "up")
    ok(pm.map_id() == HALL, "the flute's stairs lead to the Hall of Origin (map %d)" % pm.map_id())
    htiles = world.tiles(HALL, d.r16(P.MAP_HEADERS + 24 * HALL + 2))
    walker.walk_to(d, pm, htiles, (31, 28))
    bi = None
    if talk(d, pm, "up", 900, until=pm.in_battle):
        bi = battle_species(d, pm)
    ok(bi and bi["dex"] == 493 and bi["level"] == 80, "Arceus's battle: %s" % (
        ("species %d Lv%d" % (bi["dex"], bi["level"])) if bi else "no battle"))


def event_rotom(d, pm, w):
    """Secret Key: the wall in the Galactic building opens (FLAG_TEAM_GALACTIC_LEFT_ETERNA_BUILDING) -> Rotom's room."""
    print("-- Rotom's room")
    world = worldmap.World(ptrom.ROM)
    BLDG, ROOM = _map("MAP_HEADER_TEAM_GALACTIC_ETERNA_BUILDING_1F"), _map("MAP_HEADER_ROTOMS_ROOM")
    w.boot("pt_title.dsst")
    setup_postgame(w)
    # TEST SHORTCUTS: story past the Eterna building (Team Galactic gone, Looker's scene done)
    w.flag(_flag("FLAG_TEAM_GALACTIC_LEFT_ETERNA_BUILDING"))
    w.var(_flag("VAR_TEAM_GALACTIC_ETERNA_BUILDING_1F_STATE"), 1)
    for f in ("FLAG_HIDE_TEAM_GALACTIC_ETERNA_BUILDING_1F_GRUNT_M_LOOKER", "FLAG_HIDE_TEAM_GALACTIC_ETERNA_BUILDING_1F_LOOKER"):
        w.flag(_flag(f))
    r = w.go(BLDG, 1, 6, facing=0)
    ok(r == "loaded" and pm.map_id() == BLDG, "Galactic Eterna Building 1F (%s)" % r)
    talk(d, pm, "up", 600)
    walker.settle(d, pm, 600)
    tiles = world.tiles(BLDG, d.r16(P.MAP_HEADERS + 24 * BLDG + 2))
    walker.walk_to(d, pm, tiles, (2, 3), map_change_ok=True)
    for _ in range(10):
        if pm.map_id() == ROOM:
            break
        walker.step(d, pm, "up")
        walker.step(d, pm, "left")
    walker.settle(d, pm, 600)
    ok(pm.map_id() == ROOM, "the Secret Key opened the wall: in Rotom's room (map %d)" % pm.map_id())


_PRET = os.environ.get("YL_PRET_PLATINUM", "/home/claude/pokeplatinum")
_ENUMS = {}


def _enum(name):
    if name not in _ENUMS:
        vals, cur = {}, 0
        with open(os.path.join(_PRET, "generated", name + ".txt")) as f:
            for line in f:
                line = line.split("//")[0].strip()
                if not line:
                    continue
                if "=" in line:
                    k, v = [s.strip() for s in line.split("=", 1)]
                    try:
                        cur = int(v, 0)
                    except ValueError:
                        cur = eval(v, {}, dict(vals))  # noqa: S307 - pret's own enum file
                else:
                    k = line
                vals[k] = cur
                cur += 1
        _ENUMS[name] = vals
    return _ENUMS[name]


def _flag(name, default=KeyError):
    v = _enum("vars_flags").get(name)
    if v is None and default is KeyError:
        raise KeyError(name)
    return v if v is not None else default


def _map(name):
    return _enum("map_headers")[name]


def main():
    which = [a for a in sys.argv[1:]] or ["rom", "compat", "wild", "events"]
    build_rom.ensure_dev_rom("test" if os.environ.get("YL_SINNOH_SPEC") == "test" else "auto")
    print("Complete Sinnoh verify:", ptrom.label(), "spec", spec().__name__, "v%s" % spec().SPEC_VERSION)
    if "rom" in which:
        check_rom()
    d = pm = None
    if set(which) & {"compat", "wild", "events"}:
        d = DS()
        pm = P.PlatinumMemory(d)
        _DS.append(d)
    if "compat" in which:
        check_compat(d, pm)
    if "wild" in which:
        check_wild(d, pm)
    if "events" in which:
        check_events(d, pm)
    bad = [w for c, w in RESULTS if not c]
    print("\n%d checks, %d failed" % (len(RESULTS), len(bad)))
    for w in bad:
        print("  FAILED:", w)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
