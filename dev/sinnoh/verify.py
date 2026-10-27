"""Complete Sinnoh on the real ROM: the built ROM, old snapshots, wild encounters on edited maps, the four events.

    python dev/sinnoh/verify.py [rom] [compat] [wild] [events] [regis] [swarm] [area]     (default: all)
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
if os.environ["YL_PT_ROM"] != "sinnoh" and sys.argv[1:2] != ["_area_shot"]:
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
    ok(rs.extent(encdata.NARC_PATH) == (start, size), "wild NARC at the same offset/size (%#x, %d bytes)" % (start, size))
    plan = sinnoh_rom.plan(vanilla, sp)
    ok(sinnoh_rom._compare_except(vanilla, ptrom.ROM, plan.allowed),
       "the ROM equals vanilla outside the builder's planned ranges (%d ranges)" % len(plan.allowed))
    nv, ns = rv.narc(encdata.NARC_PATH), rs.narc(encdata.NARC_PATH)
    same = all(ns[i] == sp.apply(i, nv[i]) for i in range(encdata.TABLE_COUNT))
    ok(same, "every wild table = spec.apply(vanilla table) (%d slot edits, %d rate edits)"
       % (len(sp.EDITS), len(getattr(sp, "RATE_EDITS", ()))))
    changed = [i for i in range(encdata.TABLE_COUNT) if ns[i] != nv[i]]
    ok(set(changed) == {e.table for e in sp.EDITS} | {r.table for r in getattr(sp, "RATE_EDITS", ())},
       "changed tables = the spec's tables (%d)" % len(changed))
    # scripts: exactly the three Regi statue bytes
    so, sn = rv.extent(sinnoh_rom.SCRIPT_NARC_PATH)
    ok(rs.extent(sinnoh_rom.SCRIPT_NARC_PATH) == (so, sn), "script NARC in place")
    sv, ss = rv.narc(sinnoh_rom.SCRIPT_NARC_PATH), rs.narc(sinnoh_rom.SCRIPT_NARC_PATH)
    diff = [i for i in range(len(sv)) if sv[i] != ss[i]]
    ok(diff == [e.member for e in sinnoh_rom.ENGINE_EDITS] and all(
        ss[e.member][e.offset:e.offset + len(e.new)] == e.new for e in sinnoh_rom.ENGINE_EDITS),
       "scripts: only the Regi ruins' statue check changed (members %s)" % diff)
    # Pokédex area map
    if getattr(sp, "AREA_EDITS", ()):
        av, as_ = rv.narc(sp.AREA_NARC_PATH), rs.narc(sp.AREA_NARC_PATH)
        mem = set(sp.area_members())
        ok(len(av) == len(as_) == sp.AREA_MEMBER_COUNT, "area NARC: %d members" % len(as_))
        bad = [i for i in range(len(av)) if (as_[i] != (sp.apply_area(i, av[i]) if i in mem else av[i]))]
        ok(not bad, "area NARC: %d edited lists = spec.apply_area, all others vanilla%s"
           % (len(mem), (" - NOT %s" % bad[:5]) if bad else ""))
        a0, n0 = rv.extent(sp.AREA_NARC_PATH)
        a1, n1 = rs.extent(sp.AREA_NARC_PATH)
        with open(vanilla, "rb") as f:
            hv = f.read(0x200)
        used = struct.unpack_from("<I", hv, 0x80)[0]
        ok(a1 >= used and a1 % 0x200 == 0 and n1 > 0, "area NARC moved after the vanilla data (%#x -> %#x)" % (a0, a1))
        with open(ptrom.ROM, "rb") as f:
            f.seek(a0)
            old_kept = f.read(n0) == rv.file(sp.AREA_NARC_PATH)
            f.seek(0)
            hs = f.read(0x200)
        ok(old_kept, "the vanilla area NARC is still at its old place (old snapshots' cached FAT)")
        ok(struct.unpack_from("<I", hs, 0x80)[0] == a1 + n1 and
           struct.unpack_from("<H", hs, 0x15E)[0] == sinnoh_rom.crc16(hs[:0x15E]),
           "header: used ROM size = end of the moved NARC, header CRC valid")
        ok(hs[:0x80] == hv[:0x80] and hs[0x84:0x15E] == hv[0x84:0x15E],
           "header otherwise identical (game code offsets, ARM9/ARM7, FNT/FAT positions)")
    sha = sinnoh_rom._sha1(ptrom.ROM)
    want = sinnoh_rom.expected_sha1(sp)
    if want:
        ok(sha == want, "SHA1 %s = sinnoh_rom.EXPECTED for spec v%s" % (sha[:12], sp.SPEC_VERSION))
        ok(P.variant_of(sha) is not None and P.variant_of(sha).key == "sinnoh",
           "platinum.VARIANTS lists it (App.full, same code as vanilla)")
    else:
        ok(False, "spec v%s has no EXPECTED SHA1 yet (run dev/sinnoh/build_rom.py --update)" % sp.SPEC_VERSION)
    rv.close()
    rs.close()


# ------------------------------------------------------------------------------------------- snapshot compat
def rom_fat(path):
    with open(path, "rb") as f:
        hdr = f.read(0x50)
        off, size = struct.unpack_from("<II", hdr, 0x48)
        f.seek(off)
        return f.read(size)


def check_compat(d, pm):
    """Vanilla savestates (= a player's resume snapshot from 0.7.6 or a vanilla dev fixture) in the Complete Sinnoh
    ROM: same code, so the game runs on; the loaded wild table is vanilla's until the next map load (the app
    refreshes it at the first safe frame: write_wild_table)."""
    print("== vanilla snapshots in the Complete Sinnoh ROM")
    rs = ndsrom.NDSRom(ptrom.ROM)
    narc = rs.narc(encdata.NARC_PATH)
    fat = rom_fat(ptrom.ROM)
    vfat = rom_fat(ptrom.VANILLA_ROM)
    # a fresh boot of the Complete Sinnoh ROM caches ITS file table (FS_LoadTable): the moved area NARC is found
    d.run(600)
    a = pm.cached_fat_addr(len(fat))
    ok(a is not None and d.read(a, len(fat)) == fat,
       "power-on: the game's cached FAT (FSArchive fat ptr at %#x -> %s) = this ROM's FAT"
       % (P.FS_ROM_FAT_PTR, hex(a) if a else None))
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
    a = pm.cached_fat_addr(len(fat))
    before = d.read(a, len(fat)) if a else b""
    diff = [i // 8 for i in range(0, len(fat), 8) if before[i:i + 8] != fat[i:i + 8]]
    fid = rs.file_id(sinnoh_rom.AREA_NARC_PATH)
    ok(before == vfat and diff == [fid], "vanilla snapshot: cached FAT = vanilla's (differs from this ROM only in "
       "entry %s = the area NARC; the vanilla copy is still there, so it is safe as is)" % diff)
    n = pm.refresh_cached_fat(fat)
    ok(n == 1 and d.read(a, len(fat)) == fat, "refresh_cached_fat: 1 entry written, cached FAT = this ROM's")
    ok(pm.refresh_cached_fat(fat) == 0, "refresh again: nothing to do")
    d.run(120)
    ok(pm.in_game() and pm.party_checked() is not None, "game runs on after the refresh")
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
    ids = lambda evs: [e.id for e in evs]  # noqa: E731
    # 1. a mid-game save: nothing
    w.boot("pt_title.dsst")
    P.fast_continue(d)
    walker.settle(d, pm)
    ok(give_events(d, pm) == [] and not E.national_dex(pm), "no National Dex, no Rotom: no event is given")
    # 2. the first Rotom (TEST SHORTCUT: Pokédex "caught" bit 479): the Secret Key only
    i = E.SPECIES_ROTOM - 1
    a = pm.save_body() + E.PAGE_POKEDEX + E.POKEDEX_CAUGHT + i // 8
    d.w8(a, d.r8(a) | 1 << i % 8)
    got = give_events(d, pm)
    ok(ids(got) == ["secret_key"] and E.given(pm, E.EVENTS_BY_ID["secret_key"]),
       "Rotom caught before the National Dex: the unlocker gives %s" % ids(got))
    # 3. National Dex (TEST SHORTCUT: Pokédex flag + game completed): Member Card + Oak's Letter, not the flute
    w.flag(E.FLAG_GAME_COMPLETED)
    w.national_dex()
    got = give_events(d, pm)
    ok(ids(got) == ["member_card", "oaks_letter"], "National Dex: the unlocker gives %s" % ids(got))
    w.flag(E.FLAG_CAUGHT_DIALGA)          # TEST SHORTCUT: Dialga caught at the Spear Pillar
    ok(give_events(d, pm) == [], "Dialga alone: no Azure Flute yet")
    w.flag(E.FLAG_CAUGHT_PALKIA)
    got = give_events(d, pm)
    ok(ids(got) == ["azure_flute"], "Dialga + Palkia caught: the unlocker gives %s" % ids(got))
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
    """TEST SHORTCUTS: Hall of Fame done + National Dex + Dialga/Palkia caught, and the events given by the real
    unlocker's give()."""
    w.flag(E.FLAG_GAME_COMPLETED)
    w.national_dex()
    w.flag(E.FLAG_CAUGHT_DIALGA)
    w.flag(E.FLAG_CAUGHT_PALKIA)
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


# ------------------------------------------------------------------------------------------- Regis / Regigigas
RUINS = (  # (name, map, state var, dots (pret sDotCoordinates*), species)
    ("Rock Peak Ruins", 592, "VAR_ROCK_PEAK_RUINS_STATE", [(5, 5), (5, 7), (5, 9), (7, 7), (9, 5), (9, 7), (9, 9)], 377),
    ("Iceberg Ruins", 590, "VAR_ICEBERG_RUINS_STATE", [(3, 7), (5, 7), (7, 5), (7, 7), (7, 9), (9, 7), (11, 7)], 378),
    ("Iron Ruins", 588, "VAR_IRON_RUINS_STATE", [(4, 7), (5, 5), (5, 9), (7, 7), (9, 5), (9, 9), (10, 7)], 379),
)
RUINS_ACTIVATED_STATUE, RUINS_CAUGHT = 270, 290
MASTER_BALL = 1
BAG_BALLS = 0x6BC                  # Bag.balls[15] after items, key items, TMs, mail, medicine, berries


def until_safe(d, pm, frames=1500, every=30, key="a"):
    for i in range(frames):
        if i > 60 and pm.is_safe():
            return True
        if i % every == 0:
            d.press(key, 4)
        d.run(1)
    return pm.is_safe()


def balls(d, pm):
    return struct.unpack("<HH", d.read(pm.save_body() + E.PAGE_BAG + BAG_BALLS, 4))


FIGHT_RED = (232, 56, 56)          # the big FIGHT button of the battle command menu (touch screen)


def command_menu(d) -> bool:
    """The battle command menu (FIGHT / BAG / RUN / POKéMON) is on the touch screen."""
    img = d.emu.screenshot().convert("RGB")
    return img.getpixel((60, 192 + 100)) == FIGHT_RED and img.getpixel((200, 192 + 100)) == FIGHT_RED


def catch(d, pm, tries=3):
    """In a wild battle (TEST SHORTCUT: Master Balls in the bag): wait for the command menu without pressing anything
    (A there = FIGHT, B = RUN: a Lv1 Regigigas would faint or flee), BAG -> POKé BALLS -> the first ball -> USE
    (touch screen), then B through the messages, the Pokédex page and the nickname question (B = no) - never while
    the command menu is up. The bag in the save only changes when the battle ends. True when the battle ended with
    one ball fewer."""
    before = balls(d, pm)[1]
    for _ in range(tries):
        for _ in range(3000):
            if command_menu(d) or not pm.in_battle():
                break
            d.run(10)
        if not pm.in_battle():
            break
        for x, y, wait in ((40, 172, 90), (190, 58, 90), (64, 30, 90), (100, 172, 600)):
            d.touch(x, y)
            d.run(wait)
        for i in range(400):
            if not pm.in_battle() or command_menu(d):
                break
            if i % 6 == 0:
                d.press("b", 4)
            d.run(10)
    for i in range(300):
        if not pm.in_battle() and pm.is_safe():
            break
        if not command_menu(d) and i % 6 == 0:
            d.press("b", 4)
        d.run(10)
    return balls(d, pm)[1] == before - 1 and not pm.in_battle()


def wild_battle(d, pm, frames=1200):
    """battle_info once the battle has its opponent - without pressing anything (an A here could pick FIGHT and
    knock out a Lv1 Regigigas)."""
    for _ in range(frames):
        bi = pm.battle_info()
        if bi and bi.get("dex"):
            return bi
        d.run(1)
    return None


def settle_party(d, pm, frames=1800):
    """After a battle the field keeps re-encrypting party records, so a record reads valid / half re-encrypted on
    alternate frames (MEMORY_MAP.md) - for a long while here. Step ONE frame at a time until the field is safe and
    every record is valid; the caller must use this frame (the next dev warp writes the RAM save into the battery
    right away, so it never saves a half-updated party)."""
    for _ in range(frames):
        if pm.is_safe() and pm.party_checked() is not None:
            return True
        d.run(1)
    return False


def party_report(pm):
    from yellowlink import pk4
    raw = pm.party_raw()
    return "count %d, %s" % (pm.party_count(), [(pk4.PK4.from_encrypted(r).species, P.record_checksum_ok(r))
                                                for r in raw])


def party_species(pm):
    from yellowlink import pk4
    return [pk4.PK4.from_encrypted(r).species for r in (pm.party_checked() or [])]


def check_regis(d, pm):
    """Ilias: the Regi ruins without an event Regigigas, then Snowpoint Temple's Regigigas, in ONE save: dots ->
    statue -> catch Regirock, Regice, Registeel (real battles, Master Balls) -> Regigigas wakes up."""
    print("== Regis -> Regigigas in one save (no event Regigigas)")
    world = worldmap.World(ptrom.ROM)
    w = devwarp.DevWarp(d, ptrom.STATES)
    w.boot("pt_title.dsst")
    w.flag(E.FLAG_GAME_COMPLETED)          # TEST SHORTCUT: Hall of Fame done (the ruins need it)
    w.national_dex()
    d.write(w.body + E.PAGE_BAG + BAG_BALLS, struct.pack("<HH", MASTER_BALL, 10))   # TEST SHORTCUT: Master Balls
    ok(all(sp != 486 for sp in party_species(pm)), "the party has no Regigigas (event or not)")
    for name, mid, var_name, dots, species in RUINS:
        var = _flag(var_name)
        settle_party(d, pm)
        r = w.go(mid, 7, 11, facing=0)
        if not ok(r == "loaded" and pm.map_id() == mid, "%s: CONTINUE'd in (%s)" % (name, r)):
            continue
        tiles = world.tiles(mid, d.r16(P.MAP_HEADERS + 24 * mid + 2))
        for dot in dots:
            walker.walk_to(d, pm, tiles, dot)
            d.run(30)
            if pm.var(var) >= 260:
                break
        until_safe(d, pm)
        ok(pm.var(var) == RUINS_ACTIVATED_STATUE, "%s: walking the 7 dots activates the statue (state %d)"
           % (name, pm.var(var)))
        walker.walk_to(d, pm, tiles, (7, 2))
        walker.face(d, pm, "up")
        d.press("a", 10)
        for i in range(900):
            if pm.in_battle():
                break
            if i % 30 == 0:
                d.press("a", 4)
            d.run(1)
        bi = wild_battle(d, pm) if pm.in_battle() else None
        if not ok(bi and bi["dex"] == species and bi["level"] == 30,
                  "%s: the statue (no event Regigigas in the party) starts the battle: %s" % (
                      name, ("species %d Lv%d" % (bi["dex"], bi["level"])) if bi else "no battle")):
            continue
        ok(catch(d, pm), "%s: caught with a Poké Ball" % name)
        print("     party settled: %s (%s)" % (settle_party(d, pm), party_report(pm)))
        ok(pm.var(var) == RUINS_CAUGHT and species in party_species(pm),
           "%s: the game's script records it (state %d) and it is in the party %s" % (
               name, pm.var(var), party_species(pm)))
    TEMPLE = _map("MAP_HEADER_SNOWPOINT_TEMPLE_B5F")
    settle_party(d, pm)
    r = w.go(TEMPLE, 11, 14, facing=0)
    ok(r == "loaded" and pm.map_id() == TEMPLE, "Snowpoint Temple B5F (%s)" % r)
    walker.settle(d, pm, 300)
    settle_party(d, pm)
    ok({377, 378, 379} <= set(party_species(pm)), "the three Regis are in the party after the warp (%s)"
       % party_report(pm))
    tiles = world.tiles(TEMPLE, d.r16(P.MAP_HEADERS + 24 * TEMPLE + 2))
    walker.walk_to(d, pm, tiles, (11, 13))
    walker.face(d, pm, "up")
    d.press("a", 10)
    bi = None
    for i in range(3000):
        if pm.in_battle():
            bi = wild_battle(d, pm)
            break
        if i % 30 == 0:
            d.press("a", 4)
        d.run(1)
    ok(pm.flag(_flag("FLAG_AWAKENED_REGIGIGAS")), "Regigigas awakens (the three Regis in the party)")
    ok(bi and bi["dex"] == 486 and bi["level"] == 1, "Regigigas's battle: %s" % (
        ("species %d Lv%d" % (bi["dex"], bi["level"])) if bi else "no battle"))
    if bi:
        caught = catch(d, pm)
        settle_party(d, pm)
        ok(caught and pm.flag(_flag("FLAG_CAUGHT_REGIGIGAS")) and 486 in party_species(pm),
           "Regigigas caught (FLAG_CAUGHT_REGIGIGAS, in the party %s)" % party_species(pm))


# ------------------------------------------------------------------------------------------------- swarms
def slot_for(index, start=249000):
    """A 2-hour slot whose swarm is `index` (sinnoh_swarm's rotation)."""
    from yellowlink import sinnoh_swarm as SW
    return next(s for s in range(start, start + 400) if SW.pick(s) == index)


def check_swarm(d, pm):
    """The game's own swarm mechanism, driven by the shared 2-hour rotation (sinnoh_swarm.SwarmRotator)."""
    from yellowlink import sinnoh_swarm as SW
    print("== swarms every 2 hours")
    world = worldmap.World(ptrom.ROM)
    narc = world.rom.narc(encdata.NARC_PATH)
    w = devwarp.DevWarp(d, ptrom.STATES)
    w.boot("pt_title.dsst")
    P.fast_continue(d)
    walker.settle(d, pm)
    ram = d.read(0x02000000, 0x400000)
    hits = []
    pat = struct.pack("<22I", *SW.SWARM_MAPS)
    k = ram.find(pat)
    while k >= 0:
        hits.append(0x02000000 + k)
        k = ram.find(pat, k + 1)
    ok(len(hits) == 1, "sSwarmMapIdTable in RAM (overlay 6) = SWARM_MAPS (at %s)" % [hex(h) for h in hits])
    base = pm.save_body() + SW.PAGE_SPECIAL_ENCOUNTERS
    trophy = d.read(base + 8, 8)
    ok(trophy == b"\0\0\0\0\xff\xff\xff\xff" and d.r8(base + 16) == 21,
       "SpecialEncounter at body + %#x (trophy garden slots NONE, last slathered honey tree = 21)"
       % SW.PAGE_SPECIAL_ENCOUNTERS)
    ok(not SW.enabled(pm), "a mid-game save: swarms off")
    # the rival's sister turns them on (National Dex + Hall of Fame): the game's own EnableSwarms
    w.boot("pt_title.dsst")
    w.flag(E.FLAG_GAME_COMPLETED)        # TEST SHORTCUT
    w.national_dex()                     # TEST SHORTCUT
    HOUSE = _map("MAP_HEADER_SANDGEM_TOWN_COUNTERPART_HOUSE_1F")
    r = w.go(HOUSE, 6, 7, facing=2)
    ok(r == "loaded" and pm.map_id() == HOUSE, "Sandgem Town, the rival's house (%s)" % r)
    told = _flag("FLAG_TALKED_TO_COUNTERPART_SISTER_WITH_NATIONAL_DEX")
    tiles = world.tiles(HOUSE, d.r16(P.MAP_HEADERS + 24 * HOUSE + 2))
    for i in range(80):                  # she wanders north/south in column 5: wait next to her and talk
        if pm.flag(told):
            break
        if pm.is_safe():
            z = 6 + (i // 4) % 3
            if walker.pos(pm) != (6, z):
                walker.walk_to(d, pm, tiles, (6, z))
            walker.face(d, pm, "left")
            d.press("a", 10)
        d.run(40)
    until_safe(d, pm)
    ok(pm.flag(told) and SW.enabled(pm), "her script (EnableSwarms) sets swarmEnabled at +%#x: %s"
       % (SW.SE_SWARM_ENABLED, SW.enabled(pm)))
    # the rotation picks Route 201 -> the game shows the swarm there; another slot -> gone again
    ROUTE_201 = 342
    t201 = pm.m.r16(P.MAP_HEADERS + 24 * ROUTE_201 + P.MH_WILD_ENCOUNTERS_ID)
    swarm_sp = SW.species_of(ROUTE_201, lambda m: t201, narc)
    land = {sp for sp, _ in encdata.parse(narc[t201])["land"]}
    ok(swarm_sp and swarm_sp not in land, "Route 201's swarm species %s isn't in its normal slots" % swarm_sp)
    s201 = slot_for(SW.SWARM_MAPS.index(ROUTE_201))
    tiles = world.tiles(ROUTE_201, 0)
    x, z, dx = worldmap.encounter_pairs(tiles)[len(worldmap.encounter_pairs(tiles)) // 2]
    r = w.go(ROUTE_201, x, z)
    ok(r == "loaded" and pm.map_id() == ROUTE_201, "Route 201 (%s)" % r)
    rot = SW.SwarmRotator(pm, every=1, clock=lambda: s201 * SW.SLOT_SECONDS + 60)
    news = None
    for _ in range(10):
        news = news or rot.step()
        d.run(1)
    ok(news and news["map"] == ROUTE_201 and SW.swarm_index(pm) == 0,
       "the rotator writes swarmDaily for slot %d (Route 201) and announces it: %s" % (s201, news))
    trials = int(os.environ.get("YL_SINNOH_TRIALS", "24"))
    got = [g for g in force_encounters(d, pm, w, ROUTE_201, x, z, dx, trials) if g]
    n_swarm = sum(1 for sp, _ in got if sp == swarm_sp)
    ok(n_swarm >= trials // 8, "swarm on: %d of %d Route 201 battles are %d (slots 0+1 = 40%%)"
       % (n_swarm, len(got), swarm_sp))
    other = slot_for(next(i for i in range(22) if SW.SWARM_MAPS[i] != ROUTE_201), s201 + 1)
    d.load(os.path.join(w.tmp, "here.dsst"))     # back on Route 201's grass (out of the last test battle)
    walker.settle(d, pm, 300)
    rot.clock = lambda: other * SW.SLOT_SECONDS + 60
    news2 = None
    for _ in range(10):
        news2 = news2 or rot.step()
        d.run(1)
    ok(news2 and news2["slot"] == other and SW.swarm_index(pm) == SW.pick(other),
       "2 hours later: the next swarm (%s) replaces it" % (news2 and news2["map"]))
    got = [g for g in force_encounters(d, pm, w, ROUTE_201, x, z, dx, trials) if g]
    ok(got and all(sp != swarm_sp for sp, _ in got), "swarm moved away: no %d in %d Route 201 battles"
       % (swarm_sp, len(got)))


# ------------------------------------------------------------------------------------------- Pokédex area map
# Turtwig: Sinnoh dex #1, in every test save; vanilla area "unknown" (a starter); Complete Sinnoh adds Valor Lakefront
# (morning/day/night), which blinks at about (192..205, 144..155) on the top screen.
AREA_BANNER = (96, 84, 224, 108)   # the "Area Unknown" box
AREA_MAP = (80, 56, 248, 184)      # the area map on the top screen


def dex_area_shot(d, pm, path):
    """Start menu -> POKéDEX -> Turtwig -> AREA (touch), on whatever ROM `d` runs; saves both screens to `path`.
    TEST SHORTCUT: Pokedex.pokedexObtained set in RAM (the test save is from before Professor Rowan's Pokédex)."""
    w = devwarp.DevWarp(d, ptrom.STATES)
    w.boot("pt_title.dsst")
    P.fast_continue(d)
    walker.settle(d, pm, 300)
    # pt_title is a vanilla savestate: its cached FAT still points at the vanilla area file - exactly what the app
    # fixes after resuming a snapshot of another build (App._wild_refresh_now); a no-op on vanilla
    n = pm.refresh_cached_fat(rom_fat(ptrom.ROM))
    print("     cached FAT entries refreshed:", n)
    d.w8(pm.save_body() + E.PAGE_POKEDEX + 0x31A, 1)
    d.press("x", 60)
    d.press("a", 600)                  # the first start menu entry: POKéDEX (list opens on #001)
    d.press("a", 600)                  # Turtwig's page
    d.touch(68, 21)                    # AREA
    d.run(600)
    img = d.shot(path)
    return img, blinking(d)


def blinking(d, frames=16, step=7):
    """Pixels of the area map that change over ~2 s: the places where the species lives blink."""
    shots = []
    for _ in range(frames):
        d.run(step)
        shots.append(d.emu.screenshot().convert("RGB"))
    x0, y0, x1, y1 = AREA_MAP
    return sorted({(x, y) for im in shots[1:] for x in range(x0, x1) for y in range(y0, y1)
                   if im.getpixel((x, y)) != shots[0].getpixel((x, y))})


def check_area(d, pm):
    """The Pokédex AREA screen reads the moved, edited zukan_enc_platinum.narc: Turtwig is "Area Unknown" in vanilla
    Platinum (other process, YL_PT_ROM=vanilla) and shows a place in Complete Sinnoh."""
    import subprocess
    import tempfile
    print("== Pokédex area map")
    tmp = tempfile.mkdtemp(prefix="yl_area_")
    van = os.path.join(tmp, "vanilla.png")
    env = dict(os.environ, YL_PT_ROM="vanilla")
    subprocess.run([sys.executable, os.path.abspath(__file__), "_area_shot", van], env=env, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
    ours = os.path.join(tmp, "sinnoh.png")
    img, blink = dex_area_shot(d, pm, ours)
    if not ok(os.path.exists(van), "vanilla screenshot made (%s)" % van):
        return
    from PIL import Image
    vimg = Image.open(van).convert("RGB")
    img = img.convert("RGB")
    print("     screens:", tmp)
    # the AREA tab is lit on both (same pixels in the tab row at the top of the touch screen)
    tabs = (0, 200, 256, 228)
    ok(vimg.crop(tabs).tobytes() == img.crop(tabs).tobytes(), "both runs are on Turtwig's AREA page")
    vb, sb = vimg.crop(AREA_BANNER).tobytes(), img.crop(AREA_BANNER).tobytes()
    vm, sm = vimg.crop(AREA_MAP).tobytes(), img.crop(AREA_MAP).tobytes()
    ok(vb != sb, "the \"Area Unknown\" box of vanilla is gone in Complete Sinnoh")
    ok(vm != sm, "the area map differs from vanilla")
    vblink = []
    try:
        import json
        with open(van + ".json") as f:
            vblink = json.load(f)
    except (OSError, ValueError):
        vblink = None
    box = (min(blink), max(blink)) if blink else None
    ok(vblink == [] and blink and all(185 <= x <= 210 and 138 <= y <= 160 for x, y in blink),
       "a place blinks on the map: %d pixels in %s (Valor Lakefront, south-east; vanilla: %s)"
       % (len(blink), box, "none" if vblink == [] else vblink))
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        vimg.save(os.path.join(SHOTS, "area_vanilla.png"))
        img.save(os.path.join(SHOTS, "area_sinnoh.png"))


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
    if sys.argv[1:2] == ["_area_shot"]:          # helper for check_area: the same screen on vanilla Platinum
        d = DS()
        _img, blink = dex_area_shot(d, P.PlatinumMemory(d), sys.argv[2])
        import json
        with open(sys.argv[2] + ".json", "w") as f:
            json.dump(blink, f)
        return
    which = [a for a in sys.argv[1:]] or ["rom", "compat", "wild", "events", "regis", "swarm", "area"]
    build_rom.ensure_dev_rom("test" if os.environ.get("YL_SINNOH_SPEC") == "test" else "auto")
    print("Complete Sinnoh verify:", ptrom.label(), "spec", spec().__name__, "v%s" % spec().SPEC_VERSION)
    if "rom" in which:
        check_rom()
    d = pm = None
    if set(which) & {"compat", "wild", "events", "regis", "swarm", "area"}:
        d = DS()
        pm = P.PlatinumMemory(d)
        _DS.append(d)
    if "compat" in which:
        check_compat(d, pm)
    if "wild" in which:
        check_wild(d, pm)
    if "events" in which:
        check_events(d, pm)
    if "regis" in which:
        check_regis(d, pm)
    if "swarm" in which:
        check_swarm(d, pm)
    if "area" in which:
        check_area(d, pm)
    bad = [w for c, w in RESULTS if not c]
    print("\n%d checks, %d failed" % (len(RESULTS), len(bad)))
    for w in bad:
        print("  FAILED:", w)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
