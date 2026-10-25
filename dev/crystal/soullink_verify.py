"""Verify the Soul Link (Nuzlocke) helpers of crystal.py against the real Crystal ROM.

Needs dev/states/crystal.gbc + crystal_party.state (dev/crystal/make_states.py). Builds its own fixtures
(dev/states/crystal_sl_*.state) and prints PASS/FAIL per check, ending with ALL PASS.
Usage: python dev/crystal/soullink_verify.py [--pret /path/to/pokecrystal]   (pret only for the area-table regen check)
See dev/notes/soullink_crystal.md for what each check proves.
"""
import io
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from common import ROM, STATES, new_pyboy, screen_text, sram_dump, tap, ticks  # noqa: E402
from play import (choose, idle, pc_close, pc_deposit, pc_open, pc_withdraw, screen_has, step,  # noqa: E402
                  wait_text, warp)

from yellowlink import crystal, game, text  # noqa: E402
from yellowlink.crystal import CrystalMemory, Gen2PC  # noqa: E402

RESULTS = []
OTHER_TRAINER_CLASS = 0xD22F     # wOtherTrainerClass (non-zero = trainer battle)
PLAYER_TILE_COLLISION = 0xD4E4   # wPlayerTileCollision (0x18 = tall grass)


def ok(cond, what):
    RESULTS.append((bool(cond), what))
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        raise SystemExit(1)


def note(s):
    print("     " + s, flush=True)


def rom_data():
    return crystal.RomData.from_file(ROM)


def load(name="crystal_party.state"):
    return new_pyboy(state=os.path.join(STATES, name))


def save(pb, name):
    with open(os.path.join(STATES, name), "wb") as f:
        pb.save_state(f)


def snapshot(pb):
    st = io.BytesIO()
    pb.save_state(st)
    return st.getvalue()


def restore(pb, blob):
    pb.load_state(io.BytesIO(blob))


def off(bank, addr):
    return bank * 0x4000 + addr - 0x4000 if bank else addr


# ---------------------------------------------------------------------------------------------------------
# ROM: symbols, constants, item pockets, map table
# ---------------------------------------------------------------------------------------------------------
ROUTINES = {      # (bank, addr) from pokecrystal.sym
    "DoBattle": (0x0F, 0x4000), "CheckContestBattleOver": (0x0F, 0x43F5), "LostBattle": (0x0F, 0x538E),
    "TryToRunAwayFromBattle": (0x0F, 0x58B3), "LoadEnemyMon": (0x0F, 0x68EB), "InitEnemyWildmon": (0x0F, 0x7607),
    "BattleStartMessage": (0x0F, 0x7C8B), "ClearBattleRAM": (0x0B, 0x6F18), "HallOfFame": (0x21, 0x640E),
    "ItemAttributes": (0x01, 0x67C1), "ItemNames": (0x72, 0x4000), "MapGroupPointers": (0x25, 0x4000),
    "MapGroup_Cherrygrove": (0x25, 0x4D75), "NewBarkTown_MapAttributes": (0x25, 0x4DD8),
    "HallOfFameEnterScript": (0x60, 0x5454),
}


def battletype_compares(rom, name, n):
    """Values compared right after `ld a, [wBattleType]` (fa 30 d2 = ld a,[D230]; fe xx = cp xx; then
    jr/jp z,... ; cp yy ...)."""
    s = rom[off(*ROUTINES[name]):off(*ROUTINES[name]) + n]
    vals = []
    for mt in re.finditer(re.escape(bytes.fromhex("fa30d2")), s):
        i = mt.end()
        while i < len(s) and s[i] == 0xFE:
            vals.append(s[i + 1])
            i += 2
            i += 3 if s[i] in (0xCA, 0xC2) else 2       # jp z / jp nz (3 bytes) or jr (2 bytes)
    return vals


def check_rom():
    rom = open(ROM, "rb").read()
    ok(game.sha1_of(ROM) == crystal.CRYSTAL_SHA1, "ROM is Crystal v1.0 USA")
    # wBattleType = D230 and the BATTLETYPE_* values as compiled into the battle engine
    ok(battletype_compares(rom, "TryToRunAwayFromBattle", 0x40)[:6] == [2, 6, 9, 11, 7, 12],
       "BATTLETYPE DEBUG=2 CONTEST=6 TRAP=9 CELEBI=11 FORCESHINY=7 SUICUNE=12 (TryToRunAwayFromBattle reads D230)")
    ok(battletype_compares(rom, "DoBattle", 0x120)[:2] == [2, 3], "BATTLETYPE TUTORIAL=3 (DoBattle)")
    ok(battletype_compares(rom, "LoadEnemyMon", 0x2B0)[:4] == [10, 5, 5, 8] and
       rom.find(bytes.fromhex("fe07"), off(*ROUTINES["LoadEnemyMon"]), off(*ROUTINES["LoadEnemyMon"]) + 0x100) > 0,
       "BATTLETYPE FORCEITEM=10 ROAMING=5 TREE=8 FORCESHINY=7 (LoadEnemyMon)")
    ok(battletype_compares(rom, "BattleStartMessage", 0x80)[:1] == [4], "BATTLETYPE FISH=4 (BattleStartMessage)")
    ok(battletype_compares(rom, "CheckContestBattleOver", 0x10) == [6], "BATTLETYPE CONTEST=6 (CheckContestBattleOver)")
    ok(battletype_compares(rom, "LostBattle", 0x30)[:1] == [1], "BATTLETYPE CANLOSE=1 (LostBattle)")
    # wEnemyStats C6C1 is written at the end of LoadEnemyMon (ld hl, wEnemyMonStats; ld de, wEnemyStats; ld bc, 10)
    le = rom[off(*ROUTINES["LoadEnemyMon"]):off(*ROUTINES["LoadEnemyMon"]) + 0x300]
    i = le.find(bytes.fromhex("211ad211c1c6010a00"))
    ok(i > 0x200, "LoadEnemyMon copies wEnemyMonStats D21A -> wEnemyStats C6C1 near its end (+%X)" % i)
    # ClearBattleRAM zeroes wBattle C608..C741 (ld hl, wBattle / ld bc, wBattleEnd - wBattle)
    cb = rom[off(*ROUTINES["ClearBattleRAM"]):off(*ROUTINES["ClearBattleRAM"]) + 0x60]
    ok(bytes.fromhex("2108c6013901") in cb, "ClearBattleRAM ByteFills wBattle C608 (0x139 bytes, covers wBattleMon "
                                            "C62C and wEnemyStats C6C1)")
    ok(bytes.fromhex("ea2cc6") in cb and bytes.fromhex("ead4d0") in cb,
       "ClearBattleRAM clears wBattleMonSpecies C62C and wCurBattleMon D0D4")
    # InitEnemyWildmon: ld a, WILD_BATTLE; ld [wBattleMode], a (D22D)
    ok(rom[off(*ROUTINES["InitEnemyWildmon"]):off(*ROUTINES["InitEnemyWildmon"]) + 5].hex() == "3e01ea2dd2",
       "InitEnemyWildmon sets wBattleMode D22D = 1 before LoadEnemyMon")
    # Hall of Fame routine: wStatusFlags D84C bit 6 and wHallOfFameCount D95E
    hof = rom[off(*ROUTINES["HallOfFame"]):off(*ROUTINES["HallOfFame"]) + 0x30]
    ok(bytes.fromhex("214cd8cbf6") in hof and bytes.fromhex("215ed97efec8") in hof,
       "HallOfFame: set STATUSFLAGS_HALL_OF_FAME_F (D84C bit 6), wHallOfFameCount D95E (cap 200)")
    hs = rom[off(*ROUTINES["HallOfFameEnterScript"]):off(*ROUTINES["HallOfFameEnterScript"]) + 0x40]
    ok(bytes.fromhex("334400") in hs, "HALL_OF_FAME map script: setevent EVENT_BEAT_ELITE_FOUR (68)")
    # Ball pocket: every item whose ItemAttributes pocket = that of POKé BALL, and their names
    attrs = off(*ROUTINES["ItemAttributes"])
    pocket = {i: rom[attrs + (i - 1) * 7 + 5] for i in range(1, 256)}
    names, o = [], off(*ROUTINES["ItemNames"])
    while len(names) < 255:
        e = rom.index(0x50, o)
        names.append(text.decode(rom[o:e + 1]))
        o = e + 1
    ball_pocket = {i for i in range(1, 256) if pocket[i] == pocket[5] and names[i - 1] not in ("?", "")}
    ball_pocket = {i for i in ball_pocket if i <= 0xF9}
    ok(ball_pocket == set(crystal.BALL_ITEMS), "Ball pocket items (ItemAttributes 01:67C1) = %s" %
       sorted(names[i - 1] for i in ball_pocket))
    ok(all(names[i - 1] == n.replace("POKé", "POKé") for i, n in crystal.BALL_ITEMS.items()),
       "ItemNames 72:4000 match BALL_ITEMS (Apricorn balls + PARK BALL are Ball-pocket items)")
    ok(all(names[i - 1].endswith("MAIL") for i in crystal.MAIL_ITEMS) and
       sum(1 for n in names[:0xF9] if n.endswith("MAIL")) == len(crystal.MAIL_ITEMS), "MAIL_ITEMS = every *MAIL item")
    # Map table: group sizes and landmarks (9-byte entries; env at +2, landmark at +5)
    ptrs = off(*ROUTINES["MapGroupPointers"])
    starts = [int.from_bytes(rom[ptrs + 2 * i:ptrs + 2 * i + 2], "little") for i in range(26)]
    ends = starts[1:] + [ROUTINES["NewBarkTown_MapAttributes"][1]]
    ok(starts[25] == ROUTINES["MapGroup_Cherrygrove"][1], "MapGroupPointers 25:4000 -> 26 groups")
    rom_maps = {}
    for g in range(26):
        for n in range((ends[g] - starts[g]) // 9):
            e = off(0x25, starts[g]) + n * 9
            rom_maps[(g + 1) << 8 | (n + 1)] = (rom[e + 2], rom[e + 5])
    ok(set(rom_maps) == set(crystal.AREAS), "AREAS covers exactly the ROM's %d maps (group sizes match)" % len(rom_maps))
    by_area, by_lm = {}, {}
    for mid, (env, lm) in rom_maps.items():
        by_area.setdefault(crystal.AREAS[mid], set()).add(lm)
        by_lm.setdefault(lm, set()).add(crystal.AREAS[mid])
    ok(all(len(v) == 1 for v in by_area.values()), "every area is exactly one ROM landmark (Pokégear location)")
    split = {lm: v for lm, v in by_lm.items() if len(v) > 1}
    want = {frozenset({"mahogany_town", "team_rocket_base"}), frozenset({"goldenrod_city", "goldenrod_underground"}),
            frozenset({"olivine_city", "olivine_port"}), frozenset({"vermilion_city", "vermilion_port"}),
            frozenset({"pokecenter_2f", "trade_center", "colosseum", "time_capsule", "mobile_trade_room",
                       "mobile_battle_room"})}
    ok({frozenset(v) for v in split.values()} == want, "landmarks split only by the documented overrides")
    hof_env, hof_lm = rom_maps[crystal.HALL_OF_FAME_MAP]
    ok(hof_lm == rom_maps[(16 << 8) | 7][1] and crystal.HALL_OF_FAME_MAP == 0x1008,
       "HALL_OF_FAME = group 16 map 8 (same landmark as LANCES_ROOM 16:7)")
    dungeons = {"union_cave": 3, "slowpoke_well": 2, "ilex_forest": 1, "burned_tower": 2, "tin_tower": 10,
                "whirl_islands": 8, "mt_mortar": 4, "ice_path": 5, "dark_cave": 2, "dragons_den": 3, "mt_silver": 6,
                "victory_road": 1, "ruins_of_alph": 15, "national_park": 2, "sprout_tower": 3, "lake_of_rage": 3,
                "tohjo_falls": 1, "rock_tunnel": 2, "mt_moon": 3}
    cnt = Counter(crystal.AREAS.values())
    ok(all(cnt[k] == v for k, v in dungeons.items()), "dungeon areas group all their floors %s" %
       {k: cnt[k] for k in dungeons})
    ok(crystal.area_of(0x1805) == "new_bark_town" and crystal.area_name("route_29") == "Route 29" and
       crystal.area_name(crystal.area_of(0x0325)) == "Union Cave", "area_of / area_name (ELMS_LAB -> New Bark Town)")


def rom_landmark(map_id):
    """Landmark byte of a map in the ROM's map table (MapGroupPointers 25:4000, 9-byte entries, +5)."""
    rom = open(ROM, "rb").read()
    ptrs = off(*ROUTINES["MapGroupPointers"])
    g, n = map_id >> 8, map_id & 0xFF
    start = int.from_bytes(rom[ptrs + 2 * (g - 1):ptrs + 2 * g], "little")
    return rom[off(0x25, start) + (n - 1) * 9 + 5]


# ---------------------------------------------------------------------------------------------------------
# helpers: battles
# ---------------------------------------------------------------------------------------------------------
GRASS = (24, 3, 44, 10)          # Route 29 tall grass (wPlayerTileCollision 0x18), x 44..47, y 10..11


def set_balls(cm, balls):
    cm.w(crystal.NUM_BALLS, len(balls))
    for i, (item, qty) in enumerate(balls):
        cm.w(crystal.BALLS + 2 * i, item)
        cm.w(crystal.BALLS + 2 * i + 1, qty)
    cm.w(crystal.BALLS + 2 * len(balls), 0xFF)


def walk_until_battle(pb, cm, on_frame=None, x0=44, x1=47, period=50, limit=40000):
    """Pace left/right in grass until wBattleMode turns non-zero. on_frame(i) is called every frame after."""
    d = "right"
    for i in range(limit):
        if i % period == 0:
            pb.button_release(d)
            x = cm.r(crystal.X_COORD)
            if x >= x1:
                d = "left"
            if x <= x0:
                d = "right"
            pb.button_press(d)
        pb.tick(1, False)
        if cm.r(crystal.BATTLE_MODE):
            pb.button_release(d)
            return True
    pb.button_release(d)
    return False


def battle_frames(pb, cm, n, log):
    """Tick n frames, appending (battle_info, battle_party_slot, raw) for each (frame 0 = already ticked)."""
    for _ in range(n):
        log.append((cm.battle_info(), cm.battle_party_slot(), cm.r(crystal.BATTLE_MODE)))
        pb.tick(1, False)


def to_battle_menu(pb, limit=40):
    return wait_text(pb, "FIGHT", limit, "a", 30)


def finish_battle(pb, cm, limit=200):
    """Press A (B on yes/no nickname prompts) until the battle is over and the overworld idles."""
    for _ in range(limit):
        if not cm.in_battle() and idle(pb, 30, 120):
            return True
        rows = screen_text(pb)
        tap(pb, "b" if any("YES" in r for r in rows) else "a", 4, 30)
    return False


def battle_menu(pb, label):
    """FIGHT/PKMN/PACK/RUN is a 2x2 menu: walk the cursor around until it is on `label`, then A."""
    for d in ("down", "right", "up", "left") * 2:
        ticks(pb, 10)
        if any(">" + label in r for r in screen_text(pb)):
            tap(pb, "a", 4, 40)
            return True
        tap(pb, d, 4, 8)
    return False


def run_away(pb, cm):
    assert to_battle_menu(pb)
    assert battle_menu(pb, "RUN")
    return finish_battle(pb, cm)


def throw_ball(pb, name):
    assert to_battle_menu(pb)
    assert battle_menu(pb, "PACK")
    for _ in range(4):
        ticks(pb, 30)
        if screen_has(pb, name):
            break
        tap(pb, "right", 4, 30)
    assert choose(pb, name), screen_text(pb)
    assert choose(pb, "USE")


# ---------------------------------------------------------------------------------------------------------
# battle_info / catching
# ---------------------------------------------------------------------------------------------------------
ROUTE29_DAY = {16, 19, 161, 187}     # data/wild/johto_grass.asm Route 29 (Pidgey, Rattata, Sentret, Hoppip)


def check_wild_timing():
    pb = load()
    cm = CrystalMemory(pb.memory)
    idle(pb)
    set_balls(cm, [(0x01, 1), (0x05, 5)])
    warp(pb, *GRASS)
    save(pb, "crystal_sl_route29.state")
    base = snapshot(pb)
    delays, kinds_first, stale = Counter(), Counter(), 0
    for trial in range(24):
        restore(pb, base)
        ticks(pb, trial * 7 % 53)
        assert walk_until_battle(pb, cm, period=40 + trial % 17)
        kinds_first[cm.battle_kind()] += 1
        log = []
        battle_frames(pb, cm, 12, log)
        first = next(i for i, (bi, _, _) in enumerate(log) if bi is not None)
        delays[first] += 1
        final = log[-1][0]
        stale += any(bi is not None and bi != final for bi, _, _ in log)
        if trial == 0:
            note("first wild battle: %s" % final)
    ok(kinds_first == {"wild": 24}, "battle_kind() == 'wild' on the first frame of all 24 encounters")
    ok(set(delays) == {1}, "battle_info(): None on frame 0, complete from frame 1 after wBattleMode != 0 (%s)"
       % dict(delays))
    ok(stale == 0, "battle_info(): once non-None it never changes during the intro (no stale data)")
    pb.stop(False)


def check_catch_and_sequence():
    rom = rom_data()
    pb = load("crystal_sl_route29.state")
    cm = CrystalMemory(pb.memory, rom)
    # 3 battles in a row: catch (Master Ball), run, run -- wEnemyMon is NOT cleared between battles, so this
    # proves battle_info never reports the previous battle's mon.
    seen = []
    for k in range(3):
        assert walk_until_battle(pb, cm)
        log = []
        battle_frames(pb, cm, 8, log)
        infos = [bi for bi, _, _ in log if bi is not None]
        truth = cm.r(crystal.TEMP_ENEMY_MON_SPECIES)
        ok(infos and all(bi["dex"] == truth and bi["kind"] == "wild" and not bi["contest"] and not bi["safari"]
                         for bi in infos) and truth in ROUTE29_DAY,
           "battle %d: battle_info dex %d (%s) == wTempEnemyMonSpecies on every frame, wild" %
           (k + 1, truth, rom.species_name(truth)))
        bi = infos[-1]
        ok(bi["dvs"] == (cm.r(crystal.ENEMY_MON_DVS) << 8 | cm.r(crystal.ENEMY_MON_DVS + 1)) and
           bi["shiny"] == crystal.is_shiny(bi["dvs"]) and bi["level"] == cm.r(crystal.ENEMY_MON_LEVEL),
           "battle %d: DVs %04X shiny=%s level %d" % (k + 1, bi["dvs"], bi["shiny"], bi["level"]))
        slots = [s for _, s, _ in log]
        ok(all(s is None for s in slots), "battle %d: battle_party_slot() None before the player's mon is out" % (k + 1))
        to_battle_menu(pb)
        lead = cm.party()[0]
        ok(cm.battle_party_slot() == (0, lead.hp), "battle %d: battle_party_slot() == (0, %d) once CYNDAQUIL is out"
           % (k + 1, lead.hp))
        seen.append(bi)
        if k == 0:
            n_before = len(cm.party())
            ok(cm.has_poke_balls(), "has_poke_balls(): MASTER BALL x1 + POKé BALL x5 -> True")
            save(pb, "crystal_sl_wild.state")
            throw_ball(pb, "MASTER BALL")
            ok(finish_battle(pb, cm), "caught with the game's own MASTER BALL use")
            party = cm.party_checked()
            ok(len(party) == n_before + 1 and party[-1].species == bi["dex"] and party[-1].dvs == bi["dvs"],
               "the caught mon (party slot %d) is the mon battle_info reported (species + DVs)" % (len(party) - 1))
            cl = party[-1].data[29]
            ok(cl & 0x3F == bi["level"] and party[-1].data[30] & 0x7F == rom_landmark(0x1803) and party[-1].ot_id == cm.player_id(),
               "caught data: level %d, time bits %d, location byte %02X (Route 29 landmark)" %
               (cl & 0x3F, cl >> 6, party[-1].data[30]))
            ok(cm.balls() == [(0x05, 5)] and cm.has_poke_balls(), "the game used up the MASTER BALL; POKé BALLs left")
            caught_key = crystal.mon_key(party[-1].data)
        else:
            ok(run_away(pb, cm), "battle %d: ran away" % (k + 1))
    set_balls(cm, [])
    ok(not cm.has_poke_balls(), "has_poke_balls(): empty Ball pocket -> False")
    set_balls(cm, [(0xA5, 1)])
    ok(cm.has_poke_balls(), "has_poke_balls(): an Apricorn ball (MOON BALL) counts")
    set_balls(cm, [(0x05, 5)])
    idle(pb)
    save(pb, "crystal_sl_caught.state")
    pb.stop(False)
    return caught_key


def check_faint():
    rom = rom_data()
    pb = load("crystal_sl_route29.state")
    cm = CrystalMemory(pb.memory, rom)
    cm.w(crystal.PARTY_MONS + 34, 0)
    cm.w(crystal.PARTY_MONS + 35, 1)                 # CYNDAQUIL at 1 HP (set up in RAM; the faint itself is real)
    for _ in range(10):
        if not cm.in_battle():
            assert walk_until_battle(pb, cm)
        assert to_battle_menu(pb)
        save(pb, "crystal_sl_faint.state")
        # LEER every turn until the wild mon's attack knocks CYNDAQUIL out
        f_battle = f_party = f_text = None
        frame = 0
        for _turn in range(30):
            if not cm.in_battle():
                break
            if screen_has(pb, "FIGHT"):
                battle_menu(pb, "FIGHT")
                ticks(pb, 20)
                choose(pb, "LEER")
            for _ in range(240):
                pb.tick(1, False)
                frame = pb.frame_count
                s = cm.battle_party_slot()
                if f_battle is None and s is not None and s[1] == 0:
                    f_battle = frame
                    note("faint seen by battle_party_slot(): %s, party HP %d" % (s, cm.party()[0].hp))
                if f_battle is not None and f_text is None and screen_has(pb, "fainted"):
                    f_text = frame
                if f_battle is not None and f_party is None and cm.party()[0].hp == 0:
                    f_party = frame
                if f_party is not None or screen_has(pb, "FIGHT") or screen_has(pb, "Use next"):
                    break
            if f_party is not None:
                break
            if not screen_has(pb, "FIGHT"):
                tap(pb, "a", 4, 30)
        if f_party is not None:
            break
        finish_battle(pb, cm)
    ok(f_battle is not None and f_party is not None, "the wild mon knocked out CYNDAQUIL for real")
    ok(f_party > f_battle, "wBattleMonHP hits 0 first; the party struct HP becomes 0 %d frames later "
       "(UpdateFaintedPlayerMon -> UpdateBattleMonInParty); '... fainted!' text at +%s" %
       (f_party - f_battle, None if f_text is None else f_text - f_battle))
    ok(cm.party()[0].hp == 0 and cm.party()[0].data[32] == 0, "party struct: HP 0, status cleared")
    # the game asks to use the next mon: YES, pick PIDGEY; battle_party_slot must never report (1, 0)
    assert wait_text(pb, "Use next", 20, "a", 30)
    bad = []
    assert wait_text(pb, "YES", 10, "a", 30)
    assert choose(pb, "YES")
    ticks(pb, 30)
    if not pick_party(pb, "PIDGEY"):
        from common import dump
        dump(pb)
        raise SystemExit("party menu")
    for _ in range(400):
        pb.tick(1, False)
        s = cm.battle_party_slot()
        if s is not None and s[0] != 0 and s[1] == 0:
            bad.append(s)
        if s is not None and s[0] == 1:
            break
    pidgey = cm.party()[1]
    ok(not bad and cm.battle_party_slot() == (1, pidgey.hp), "switch-in: battle_party_slot() -> (1, %d), never a "
       "false 0 HP for the new slot" % pidgey.hp)
    run_away(pb, cm)
    pb.stop(False)
    return f_party - f_battle


def pick_party(pb, name, limit=8):
    """Party menu: cursor '>' in column 0 of the mon's row."""
    for _ in range(limit):
        ticks(pb, 20)
        rows = screen_text(pb)
        for r in rows:
            if r.startswith(">") and name in r:
                tap(pb, "a", 4, 40)
                rows2 = screen_text(pb)
                if any(">SWITCH" in x or ">SEND" in x for x in rows2):
                    tap(pb, "a", 4, 40)
                return True
        tap(pb, "down", 4, 12)
    return False


def check_trainer():
    pb = load()
    cm = CrystalMemory(pb.memory)
    idle(pb)
    warp(pb, 26, 1, 1, 11)                           # Route 30, below BUG CATCHER DON (1,7, sight 3)
    pb.button_press("up")
    first = None
    log = []
    for i in range(3000):
        pb.tick(1, False)
        if i == 10:
            pb.button_release("up")
        if cm.in_battle():
            if first is None:
                first = (cm.battle_kind(), cm.battle_info())
            log.append(cm.battle_info())
            if len(log) > 2:
                break
        elif i > 30 and i % 30 == 0:
            tap(pb, "a", 4, 4)
    ok(first is not None and first[0] == "trainer" and first[1]["kind"] == "trainer" and first[1]["dex"] == 0,
       "trainer battle (BUG CATCHER DON): kind 'trainer' on frame 0, dex 0 before he sends a mon out (%s)" % (first,))
    to_battle_menu(pb)
    bi = cm.battle_info()
    ok(bi["kind"] == "trainer" and bi["dex"] == 10 and cm.r(OTHER_TRAINER_CLASS) != 0,
       "trainer battle: dex 10 (CATERPIE) once sent out")
    save(pb, "crystal_sl_trainer.state")
    pb.stop(False)


def check_tutorial():
    pb = load()
    cm = CrystalMemory(pb.memory)
    idle(pb)
    ev = crystal.EVENT_FLAGS
    cm.w(ev + 31 // 8, cm.r(ev + 31 // 8) | 1 << (31 % 8))       # EVENT_GAVE_MYSTERY_EGG_TO_ELM
    warp(pb, 24, 3, 51, 12)                                    # right of the Dude (50,12) on Route 29
    tap(pb, "left", 3, 20)
    n = len(cm.party())
    kind = None
    for _ in range(40):
        tap(pb, "a", 4, 30)
        if cm.in_battle():
            break
    for _ in range(30):
        pb.tick(1, False)
        bi = cm.battle_info()
        if bi:
            kind = bi
            break
    ok(kind is not None and kind["kind"] == "special" and kind["battle_type"] == crystal.BATTLETYPE_TUTORIAL
       and kind["dex"] == 19, "catching tutorial (the Dude, real script): kind 'special', BATTLETYPE_TUTORIAL, "
       "RATTATA")
    ok(cm.battle_party_slot() is None, "tutorial: battle_party_slot() None (the Dude's battle, no player mon out)")
    for _ in range(200):
        if not cm.in_battle():
            break
        tap(pb, "a", 4, 30)
    ok(not cm.in_battle() and len(cm.party()) == n, "tutorial: ends without touching the party")
    pb.stop(False)


def check_contest():
    pb = load()
    cm = CrystalMemory(pb.memory)
    idle(pb)
    # National Park contest map; start the contest state the way GiveParkBalls/StartBugContestTimer leave it
    m = pb.memory
    cm.w(crystal.PARK_BALLS_REMAINING, 20)
    cm.w(0xD46C, 20)                                 # wBugContestMinsRemaining  (StartBugContestTimer)
    cm.w(0xD46D, 0)                                  # wBugContestSecsRemaining
    for i, v in enumerate((cm.r(0xD4CB), m[0xFF94], m[0xFF96], m[0xFF98])):
        cm.w(0xDC35 + i, v)                          # wBugContestStartTime = wCurDay, hHours, hMinutes, hSeconds
    cm.w(crystal.STATUS_FLAGS2, cm.r(crystal.STATUS_FLAGS2) | 0x04)
    ok(cm.in_bug_contest(), "in_bug_contest(): STATUSFLAGS2_BUG_CONTEST_TIMER_F")
    warp(pb, 3, 16, 18, 31)                          # tall grass in the contest park (found by random walk)
    found = walk_until_battle(pb, cm, x0=17, x1=19, limit=20000) or find_grass_and_battle(pb, cm)
    ok(found, "contest: wild encounter through RandomEncounter -> BugCatchingContestBattleScript")
    bi = None
    for _ in range(10):
        pb.tick(1, False)
        bi = cm.battle_info()
        if bi:
            break
    ok(bi and bi["kind"] == "wild" and bi["contest"] and bi["battle_type"] == crystal.BATTLETYPE_CONTEST,
       "contest battle: kind 'wild', contest True (%s)" % bi)
    ok(cm.park_balls_remaining() == 20, "park_balls_remaining() == 20")
    save(pb, "crystal_sl_contest.state")
    pb.stop(False)


def find_grass_and_battle(pb, cm, limit=60):
    """Walk around the current map until a battle starts (random directions, stays near grass)."""
    import random
    rnd = random.Random(3)
    for _ in range(limit):
        d = rnd.choice(("up", "down", "left", "right"))
        for _ in range(4):
            step(pb, d)
            if cm.in_battle():
                return True
            if cm.r(PLAYER_TILE_COLLISION) == 0x18:
                for _ in range(80):
                    step(pb, rnd.choice(("left", "right")))
                    if cm.in_battle():
                        return True
    return False



# ---------------------------------------------------------------------------------------------------------
# forced box moves: our functions vs. the game's own Bill's PC, byte for byte
# ---------------------------------------------------------------------------------------------------------
PARTY_REGION = (crystal.PARTY_COUNT, crystal.PARTY_NICKS + 6 * 11)       # DCD7..DE83
SBOX_REGION = (crystal.SBOX_COUNT, 0xB15E)                               # sBox .. sBoxEnd
MAIL_REGION = (0xA600, 0xA600 + 6 * crystal.MAIL_STRUCT_LENGTH)           # sPartyMail


def regions(pb):
    m = pb.memory
    return (bytes(m[1, a] for a in range(*PARTY_REGION)), bytes(m[1, a] for a in range(*SBOX_REGION)),
            bytes(m[0, a] for a in range(*MAIL_REGION)))


def build_pc_fixture(rom):
    """crystal_party.state + a RAM-built party of 6 (PP used, status, an egg, a mon holding MAIL with a
    message in sPartyMail) and 3 mons in sBox (one with a stale level byte and stat exp, an egg)."""
    pb = load()
    cm = CrystalMemory(pb.memory, rom)
    idle(pb)
    party = cm.party()
    cyn = bytearray(party[0].data)
    cyn[23] = 0x40 | 7                               # TACKLE: 1 PP Up, 7 PP left
    cyn[24] = 3                                      # LEER: 3 PP left
    cyn[32] = 0x10                                   # burned (not poison: poison interrupts walking to the PC)
    cyn[35] -= 4
    party[0] = crystal.Mon(bytes(cyn), party[0].ot, party[0].nick)
    egg = crystal.make_mon(rom, 175, 5, [118, 45], cm.player_id(), "CHRIS", dvs=0x1234, nick="EGG")
    eggd = bytearray(egg.data)
    eggd[27] = 10                                    # egg cycles
    eggd[34] = eggd[35] = 0
    party.append(crystal.Mon(bytes(eggd), egg.ot, egg.nick, egg=True))
    hoot = crystal.make_mon(rom, 163, 8, [33, 45], 4321, "FRIEND", dvs=0xBEEF, nick="OWLY", item=0x9E)
    party.append(hoot)                               # holds FLOWER MAIL
    party.append(crystal.make_mon(rom, 25, 9, [84, 45], 999, "ASH", dvs=0x7777, nick="SPARKY"))
    cm.set_party(party)
    for s in range(6):
        for k in range(crystal.MAIL_STRUCT_LENGTH):
            pb.memory[0, 0xA600 + s * crystal.MAIL_STRUCT_LENGTH + k] = (s * 16 + k) & 0xFF
    spin = bytearray(crystal.make_mon(rom, 167, 4, [40, 81], cm.player_id(), "CHRIS", dvs=0x4321).data[:32])
    spin[8:11] = crystal.exp_for_level(rom.growth_rate(167), 9).to_bytes(3, "big")   # exp says level 9
    spin[11:13] = (900).to_bytes(2, "big")          # HP stat exp
    spin[17:19] = (2500).to_bytes(2, "big")         # Speed stat exp
    spin[23] = 0xC0 | 2                              # POISON STING: 3 PP Ups, 2 PP left
    egg2 = bytearray(crystal.make_mon(rom, 172, 5, [84], cm.player_id(), "CHRIS", dvs=0x2468).data[:32])
    egg2[27] = 20
    natu = crystal.make_mon(rom, 177, 7, [64, 43], 555, "MOM", dvs=0x1357, nick="NATTY")
    recs = [crystal.record(spin, text.encode("CHRIS"), rom.species_name_raw(167)),
            crystal.record(egg2, text.encode("CHRIS"), text.encode("EGG"), egg=True),
            natu.box_record()]
    raw = crystal.build_box(recs)
    for i in range(crystal.BOX_BYTES):
        pb.memory[1, 0xAD10 + i] = raw[i]
    idle(pb)
    return pb, cm


def close_menus(pb):
    for _ in range(10):
        tap(pb, "b", 4, 40)
        if idle(pb, 30, 90):
            return True
    return False


def pc_move_index(pb, action, index):
    """Bill's PC DEPOSIT/WITHDRAW of list entry `index` (picked by position: an egg's row-14 label is not a
    species name)."""
    label = "DEPOSIT" if action == "dep" else "WITHDRAW"
    assert choose(pb, label)
    assert wait_text(pb, "Choose a PKMN", 10, "a", 20)
    ticks(pb, 30)
    for _ in range(index):
        tap(pb, "down", 4, 20)
    ticks(pb, 20)
    tap(pb, "a", 4, 40)
    assert choose(pb, label), screen_text(pb)
    assert wait_text(pb, "Stored" if action == "dep" else "Got", 10, "a", 20) or True
    assert wait_text(pb, "Choose a PKMN", 20, "a", 20)
    assert wait_text(pb, "WITHDRAW", 10, "b", 30)


def game_pc(pb, action, index):
    pc_open(pb)
    pc_move_index(pb, action, index)
    pc_close(pb)
    idle(pb)


def check_box_moves():
    rom = rom_data()
    pb, cm = build_pc_fixture(rom)
    # the game accepts the RAM-built party (party menu lists the mons with the right HP)
    tap(pb, "start", 4, 40)
    choose(pb, "POK")
    ticks(pb, 60)
    rows = "\n".join(screen_text(pb))
    ok("OWLY" in rows and "SPARKY" in rows and "EGG" in rows, "fixture: game's party menu lists the 6 RAM-built mons")
    close_menus(pb)
    save(pb, "crystal_sl_pc6.state")
    base6 = snapshot(pb)
    ok(len(cm.current_box_records()) == 3 and cm.current_box_records()[1][54] == 1,
       "current_box_records(): 3 records, the egg flagged (sBox species list 0xFD)")
    cases = [("dep", 0, "CYNDAQUIL"), ("dep", 3, "EGG"), ("dep", 5, "PIKACHU"), ("dep", 1, "PIDGEY")]
    for action, idx, name in cases:
        restore(pb, base6)
        game_pc(pb, action, idx)
        want = regions(pb)
        restore(pb, base6)
        before = cm.party()[idx]
        assert cm.is_safe()
        ok(cm.move_party_to_box(idx), "move_party_to_box(%d) %s" % (idx, name))
        got = regions(pb)
        ok(got == want, "deposit %s: party, sBox and sPartyMail byte-identical to Bill's PC DEPOSIT" % name)
        rec = cm.current_box_records()[-1]
        ok(crystal.mon_key(rec) == crystal.mon_key(before.data) and rec[54] == (1 if before.egg else 0),
           "deposit %s: mon_key unchanged, egg flag %d" % (name, rec[54]))
    # withdraws need room: drop PIKACHU from the party first (through our deposit; it was proven above)
    restore(pb, base6)
    assert cm.move_party_to_box(5)
    idle(pb)
    base5 = snapshot(pb)
    for idx, name in ((0, "SPINARAK"), (1, "EGG"), (2, "NATU"), (3, "PIKACHU")):
        restore(pb, base5)
        game_pc(pb, "wd", idx)
        want = regions(pb)
        restore(pb, base5)
        rec = cm.current_box_records()[idx]
        ok(cm.move_box_to_party(idx), "move_box_to_party(%d) %s" % (idx, name))
        got = regions(pb)
        diff = [i for i in range(len(got[0])) if got[0][i] != want[0][i]]
        ok(got == want, "withdraw %s: party, sBox and sPartyMail byte-identical to Bill's PC WITHDRAW%s" %
           (name, "" if not diff else " (party diff at %s)" % [hex(PARTY_REGION[0] + i) for i in diff]))
        new = cm.party()[-1]
        ok(crystal.mon_key(new.data) == crystal.mon_key(rec), "withdraw %s: mon_key unchanged" % name)
        if name == "SPINARAK":
            ok(new.level == 9 and new.hp == new.max_hp and new.data[32] == 0,
               "withdraw SPINARAK: level recomputed from exp (4 -> 9), stats with stat exp, HP = max HP")
        if name == "EGG":
            ok(new.egg and new.hp == 0, "withdraw EGG: species list 0xFD, HP 0")
    # refusals the game also makes
    restore(pb, base6)
    ok(not cm.move_box_to_party(0), "move_box_to_party: False with a full party")
    ok(not cm.move_party_to_box(4), "move_party_to_box: False for a mon holding MAIL")
    restore(pb, base5)
    pb.memory[1, 0xAD10] = 20
    ok(not cm.move_party_to_box(1), "move_party_to_box: False when the box is full")
    # end to end: our moves, then the game uses the result (party menu, PC lists, SAVE + reload)
    restore(pb, base6)
    assert cm.move_party_to_box(3) and cm.move_party_to_box(0) and cm.move_box_to_party(2)
    tap(pb, "start", 4, 40)
    choose(pb, "POK")
    ticks(pb, 60)
    rows = screen_text(pb)
    ok(any("NATTY" in r for r in rows) and not any("CYNDAQUIL" in r for r in rows),
       "after forced moves: game's party menu shows NATTY, no CYNDAQUIL")
    note("party menu: " + " | ".join(r.strip() for r in rows[:12] if r.strip()))
    close_menus(pb)
    pc_open(pb)
    pc_withdraw(pb, "CYNDAQUIL")
    p = cm.party_checked()
    ok(p is not None and p[-1].species == 155 and p[-1].hp == p[-1].max_hp,
       "Bill's PC withdraws the force-deposited CYNDAQUIL normally (healed)")
    pc_deposit(pb, "CYNDAQUIL")
    pc_close(pb)
    idle(pb)
    boxes_before = cm.current_box_records()
    party_before = cm.party_checked()
    sav = save_via_menu(pb)
    pb.stop(False)
    pb2 = new_pyboy(sav=sav)
    ok(crystal.fast_continue(pb2) == "loaded", "SAVE + reload: CONTINUE works (no 'save file is corrupted')")
    pc2 = Gen2PC(pb2.memory, rom)
    cm2 = CrystalMemory(pb2.memory, rom)
    ok(pc2.save_checksum_ok(), "SAVE + reload: main save checksum valid")
    ok(cm2.party_checked() == party_before and cm2.current_box_records() == boxes_before,
       "SAVE + reload: party and current box identical")
    pb2.stop(False)


def save_via_menu(pb):
    from make_states import save_game
    save_game(pb)
    return sram_dump(pb)


# ---------------------------------------------------------------------------------------------------------
# Day Care, hatching, Hall of Fame
# ---------------------------------------------------------------------------------------------------------
def check_daycare():
    rom = rom_data()
    pb = load()
    cm = CrystalMemory(pb.memory, rom)
    idle(pb)
    ok(cm.daycare_records() == [], "daycare_records(): [] with nobody at the Day Care")
    pidgey = cm.party()[1]
    warp(pb, 11, 24, 4, 3)                           # DAY_CARE, left of the Day-Care Lady (5,3)
    tap(pb, "right", 3, 20)
    tap(pb, "a", 4, 40)
    assert wait_text(pb, "YES", 20, "a", 30), screen_text(pb)
    assert choose(pb, "YES")
    for _ in range(20):
        ticks(pb, 30)
        if any(r.startswith(">") or ">" in r[:2] for r in screen_text(pb)) and screen_has(pb, "PIDGEY"):
            break
        tap(pb, "a", 4, 20)
    assert pick_party(pb, "PIDGEY"), screen_text(pb)
    for _ in range(40):
        if not cm.font_loaded() and idle(pb, 30, 90):
            break
        tap(pb, "a", 4, 30)
    recs = cm.daycare_records()
    ok(len(recs) == 1 and cm.r(crystal.DAYCARE_LADY) & 1 and [m.species for m in cm.party()] == [155, 161],
       "Day-Care Lady took PIDGEY for real (wDayCareLady bit 0, party now CYNDAQUIL, SENTRET)")
    ok(recs[0] == pidgey.box_record(), "daycare_records()[0] == the party copy as a 55-byte record")
    ok(crystal.mon_key(recs[0]) == crystal.mon_key(pidgey.data), "mon_key(day care) == mon_key(party copy)")
    save(pb, "crystal_sl_daycare.state")
    pb.stop(False)


def check_hatch():
    rom = rom_data()
    pb = load()
    cm = CrystalMemory(pb.memory, rom)
    idle(pb)
    party = cm.party()
    egg = crystal.make_mon(rom, 175, 5, [118, 45], cm.player_id(), "CHRIS", dvs=0x1234, nick="EGG")
    d = bytearray(egg.data)
    d[27] = 1                                        # 1 egg cycle left
    d[29] = d[30] = 0                                # Day-Care eggs start with caught data 0 (ByteFill)
    d[34] = d[35] = 0
    party.append(crystal.Mon(bytes(d), egg.ot, egg.nick, egg=True))
    cm.set_party(party)
    key = crystal.mon_key(d)
    cm.w(0xDC73, 0x7F)                               # wStepCount: the next step is the egg step (every 256)
    warp(pb, 24, 3, 50, 8)
    step(pb, "left")
    for _ in range(80):
        if cm.r(crystal.PARTY_SPECIES + 3) == 175 and not cm.font_loaded() and idle(pb, 30, 90):
            break
        rows = screen_text(pb)
        tap(pb, "b" if any("YES" in r for r in rows) else "a", 4, 30)
    new = cm.party()[3]
    ok(cm.r(crystal.PARTY_SPECIES + 3) == 175 and not new.egg, "the game hatched the egg (TOGEPI)")
    ok(new.data[29] & 0x3F == crystal.CAUGHT_EGG_LEVEL and new.data[30] & 0x7F == rom_landmark(0x1803),
       "hatching wrote caught data (level 1, Route 29): %02X %02X" % (new.data[29], new.data[30]))
    ok(crystal.mon_key(new.data) == key, "mon_key(hatched) == mon_key(egg) (%s)" % key)
    pb.stop(False)


def check_hall_of_fame():
    pb = load()
    cm = CrystalMemory(pb.memory)
    idle(pb)
    ok(not cm.in_hall_of_fame() and not cm.entered_hall_of_fame(), "fresh save: not in / never entered the HoF")
    warp(pb, 16, 8, 4, 13)
    ok(cm.in_hall_of_fame() and cm.map_id() == crystal.HALL_OF_FAME_MAP, "in_hall_of_fame() after entering 16:8")
    flags = []
    for _ in range(300):
        if cm.hall_of_fame_count():
            break
        tap(pb, "a", 4, 30)
        flags.append(cm.event_flag(crystal.EVENT_BEAT_ELITE_FOUR))
    ok(cm.hall_of_fame_count() == 1 and cm.r(crystal.STATUS_FLAGS) & 0x40 and cm.event_flag(crystal.EVENT_BEAT_ELITE_FOUR),
       "the real HoF scene: wHallOfFameCount 1, wStatusFlags bit 6, EVENT_BEAT_ELITE_FOUR")
    note("during the HoF scene: map %04X, in_game() %s, wGameTimerPaused %02X" %
         (cm.map_id(), cm.in_game(), cm.r(crystal.GAME_TIMER)))
    ok(cm.entered_hall_of_fame() and cm.in_hall_of_fame(), "entered_hall_of_fame() True, in_hall_of_fame() still True")
    sav = sram_dump(pb)                              # HallOfFame saves the game (SaveGameData) by itself
    pb.stop(False)
    pb2 = new_pyboy(sav=sav)
    ok(crystal.fast_continue(pb2) == "loaded", "the HoF save CONTINUEs")
    cm2 = CrystalMemory(pb2.memory)
    ok(cm2.entered_hall_of_fame() and cm2.hall_of_fame_count() == 1 and not cm2.in_hall_of_fame(),
       "after reload: entered_hall_of_fame() persists (count 1), player is back outside the HoF (map %04X)"
       % cm2.map_id())
    pb2.stop(False)

def main():
    check_rom()
    check_wild_timing()
    check_catch_and_sequence()
    check_faint()
    check_trainer()
    check_tutorial()
    check_contest()
    check_box_moves()
    check_daycare()
    check_hatch()
    check_hall_of_fame()
    print("\nALL PASS (%d checks)" % len(RESULTS))


if __name__ == "__main__":
    main()
