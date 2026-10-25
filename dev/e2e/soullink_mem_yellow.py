"""Soul Link memory helpers on the real Pokémon Yellow ROM (PyBoy, headless).

Verifies the GameMemory additions in client/yellowlink/game.py against the running game:
  battle_info()        wild / trainer / special (ghost, Marowak, Old Man, Oak's Pikachu) / safari, from frame 0
  battle_party_slot()  active party index + wBattleMonHP, a real faint, the next mon sent out
  has_poke_balls()     bag + Safari Ball counter; a real catch with a ball
  in_hall_of_fame() / hall_of_fame_count()   the game's own Hall of Fame script + save + reload
  daycare_records()    a real Day Care deposit (and withdraw) on Route 5
  current_box_records / move_party_to_box / move_box_to_party   compared byte-for-byte with Bill's PC,
                       party menu, PC list, SAVE + reload, and the following starter Pikachu
  mon_key / AREAS      stability across all of the above, table coverage

Needs dev/states/{yellow.gb, room.state, pallet_after_lab.state, pokecenter_pc.state}.
Writes extra fixtures to dev/states/soullink_*.state. Prints PASS/FAIL per check, ends with ALL PASS.
Run: python dev/e2e/soullink_mem_yellow.py
Findings: dev/notes/soullink_yellow.md
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
ROM = os.environ.get("YL_ROM", os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from pyboy import PyBoy  # noqa: E402
from yellowlink import game, text  # noqa: E402
import pc_actions as pa  # noqa: E402

RD = game.RomData.from_file(ROM)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def info(msg):
    print("     " + msg)


# --- emulator / input ------------------------------------------------------------------------------------------
def emu(state):
    pb = PyBoy(ROM, window="null", sound_emulated=False, log_level="ERROR")
    pb.set_emulation_speed(0)
    with open(state if os.path.isabs(state) else os.path.join(STATES, state), "rb") as f:
        pb.load_state(f)
    return pb, game.GameMemory(pb.memory, RD)


def save_fixture(pb, name):
    with open(os.path.join(STATES, name), "wb") as f:
        pb.save_state(f)


def xy(pb):
    m = pb.memory
    return m[game.CUR_MAP], m[game.X_COORD], m[game.Y_COORD]


def tick(pb, n=1):
    for _ in range(n):
        pb.tick()


def step(pb, d, maxf=40):
    """Hold a direction for one step (or until a battle starts). True if the position changed."""
    m, start, moved = pb.memory, xy(pb), False
    pb.button_press(d)
    for _ in range(maxf):
        pb.tick()
        if m[game.WALK_COUNTER]:
            moved = True
        if (moved and m[game.WALK_COUNTER] == 0) or m[game.IS_IN_BATTLE]:
            break
    pb.button_release(d)
    tick(pb, 2)
    return xy(pb) != start


def walk(pb, seq, pause=0):
    for d in seq:
        step(pb, d)
        tick(pb, pause)


def mash_a(pb, until, maxf=8000, every=25):
    f = 0
    while f < maxf and not until():
        if f % every == 0:
            pb.button_press("a")
        if f % every == 3:
            pb.button_release("a")
        pb.tick()
        f += 1
    pb.button_release("a")
    return until()


def wait_safe(pb, g, maxf=900):
    for _ in range(maxf):
        if g.is_safe():
            return True
        pb.tick()
    return False


def set_bag(pb, items):
    m = pb.memory
    m[game.NUM_BAG_ITEMS] = len(items)
    for i, (it, q) in enumerate(items):
        m[game.BAG_ITEMS + 2 * i], m[game.BAG_ITEMS + 2 * i + 1] = it, q
    m[game.BAG_ITEMS + 2 * len(items)] = 0xFF


def has(pb, *s):
    return pa._has(pb, *s)


def step_until_battle(pb, dirs, maxsteps=500):
    """Walk until the exact frame wIsInBattle turns non-zero ("frame 0")."""
    m = pb.memory
    for i in range(maxsteps):
        d = dirs[i % len(dirs)]
        pb.button_press(d)
        for _ in range(40):
            pb.tick()
            if m[game.IS_IN_BATTLE]:
                pb.button_release(d)
                return True
            if m[game.WALK_COUNTER] == 1:
                break
        pb.button_release(d)
        for _ in range(16):
            pb.tick()
            if m[game.IS_IN_BATTLE]:
                return True
            if m[game.WALK_COUNTER] == 0:
                break
    return False


def tick_until_battle(pb, maxf=3000, press_a=False):
    m = pb.memory
    f = 0
    while not m[game.IS_IN_BATTLE] and f < maxf:
        if press_a and f % 30 == 0:
            pb.button_press("a")
        if press_a and f % 30 == 3:
            pb.button_release("a")
        pb.tick()
        f += 1
    pb.button_release("a")
    return bool(m[game.IS_IN_BATTLE])


def in_battle_menu(pb):
    return has(pb, "FIGHT", "RUN") and not pa._prompt_arrow(pb)


def advance_record(pb, pred, sampler, maxf=6000):
    """pc_actions._advance_until, sampling `sampler()` after every frame."""
    samples, f = [], 0
    while f < maxf:
        if pred(pb):
            pa._settle(pb)
            if pred(pb):
                return samples
        if pa._prompt_arrow(pb):
            pb.button_press("a")
            for _ in range(3):
                pb.tick()
                samples.append(sampler())
            pb.button_release("a")
            for _ in range(4):
                pb.tick()
                samples.append(sampler())
            f += 7
        else:
            pb.tick()
            samples.append(sampler())
            f += 1
    raise TimeoutError("advance_record\n" + pa.screen_text(pb))


def battle_menu_select(pb, idx):
    """0 FIGHT, 1 PKMN, 2 ITEM, 3 RUN"""
    for b in {0: ("up", "left"), 1: ("up", "right"), 2: ("down", "left"), 3: ("down", "right")}[idx]:
        pa._press(pb, b)
        pa._settle(pb, 3)
    pa._press(pb, "a")


def use_item_in_battle(pb, bag_index):
    battle_menu_select(pb, 2)
    pa._advance_until(pb, lambda p: has(p, "CANCEL"), what="bag")
    pa._move_cursor(pb, bag_index, scrolled=True)
    pa._press(pb, "a")


def fight_move(pb, move_index):
    battle_menu_select(pb, 0)
    pa._advance_until(pb, lambda p: has(p, "TYPE/"), what="move list")
    pa._move_cursor(pb, move_index)
    pa._press(pb, "a")


def finish_battle(pb, maxf=8000):
    """Through the end-of-battle texts (answering NO to a nickname) back to the overworld."""
    m, f = pb.memory, 0
    while f < maxf:
        if m[game.IS_IN_BATTLE] == 0 and not (m[game.FONT_LOADED] & 1):
            pa._settle(pb, 20)
            if m[game.IS_IN_BATTLE] == 0 and not (m[game.FONT_LOADED] & 1):
                return True
        if has(pb, "YES", "NO") and not pa._prompt_arrow(pb):
            pa._press(pb, "b")
            f += 7
        elif f % 20 == 0:
            pa._press(pb, "a")
            f += 7
        else:
            pb.tick()
            f += 1
    return False


def warp_via_lab_door(pb, target_map, warp_id=0):
    """From pallet_after_lab.state (player just below Oak's Lab door at (12,11)): retarget that door's entry in
    wWarpEntries (D3AE: y, x, dest warp id, dest map) and step through it, so the game's own warp code loads the
    target map (scripts, wild data, tileset) and puts the player on its warp `warp_id`."""
    m = pb.memory
    for i in range(m[0xD3AD]):
        e = 0xD3AE + 4 * i
        if (m[e + 1], m[e]) == (12, 11):
            m[e + 2], m[e + 3] = warp_id, target_map
            break
    else:
        raise RuntimeError("lab door warp not found")
    step(pb, "up")
    for _ in range(300):
        pb.tick()
        if m[game.CUR_MAP] == target_map and m[game.WALK_COUNTER] == 0 and not (m[game.FONT_LOADED] & 1):
            break
    tick(pb, 60)
    return m[game.CUR_MAP] == target_map


# --- menus / save ------------------------------------------------------------------------------------------------
def _cursor_line(pb):
    return next((ln for ln in pa.screen_lines(pb) if ">" in ln), "")


def _press_a_until_change(pb):
    before = pa._tiles(pb)
    for _ in range(10):
        pa._press(pb, "a", hold=6, gap=20)
        if pa._tiles(pb) != before:
            return


def open_start_item(pb, label):
    for _ in range(10):
        pa._press(pb, "start", hold=6, gap=14)
        if has(pb, "SAVE", "OPTION"):
            break
    pa._settle(pb)
    for _ in range(10):
        if label in _cursor_line(pb):
            break
        pa._press(pb, "down")
        pa._settle(pb, 3)
    _press_a_until_change(pb)


def close_menus(pb):
    for _ in range(20):
        if not (pb.memory[game.FONT_LOADED] & 1):
            break
        pa._press(pb, "b", hold=6, gap=20)
        pa._settle(pb, 5)
    pa._settle(pb, 10)
    if pb.memory[game.FONT_LOADED] & 1:
        raise TimeoutError("menus did not close\n" + pa.screen_text(pb))


def party_menu_lines(pb):
    """START -> POKéMON; returns the party screen's text rows, then closes the menus."""
    open_start_item(pb, "POKe")
    pa._advance_until(pb, lambda p: has(p, "Choose a POKe"), what="party menu")
    lines = pa.screen_lines(pb)
    close_menus(pb)
    return lines


def party_menu_matches(lines, party):
    """Every mon's nickname, level and HP/maxHP appear in the party screen rows."""
    for i, mon in enumerate(party):
        name_row, hp_row = lines[2 * i], lines[2 * i + 1]
        nick = "".join(pa._CH.get(v, "?") for v in mon.nick[:mon.nick.index(0x50) if 0x50 in mon.nick else 10])
        if nick not in name_row or f"{mon.level}" not in name_row[11:]:
            return False
        if f"{mon.hp:>3}/{mon.max_hp:>3}" not in hp_row:
            return False
    return True


def pc_withdraw_list_lines(pb):
    """In Bill's PC: open WITHDRAW, return the list screen rows, back out to the PC menu."""
    pa._select(pb, pa.WITHDRAW)
    pa._advance_until(pb, lambda p: pa._in_mon_list(p) or pa._in_bills_menu(p), what="withdraw list")
    lines = pa.screen_lines(pb)
    if pa._in_mon_list(pb):
        pa._press(pb, "b")
        pa._advance_until(pb, pa._in_bills_menu, what="Bill's PC menu")
    return lines


def save_game(pb):
    open_start_item(pb, "SAVE")
    pa._advance_until(pb, lambda p: has(p, "YES", "NO"), what="save yes/no")
    pa._select(pb, 0)
    pa._advance_until(pb, lambda p: has(p, "saved"), what="saved", maxf=4000)
    for _ in range(400):
        if not (pb.memory[game.FONT_LOADED] & 1):
            break
        if pa._prompt_arrow(pb):
            pa._press(pb, "a")
        pb.tick()
    pa._settle(pb, 20)


def sram(pb):
    return b"".join(bytes(pb.memory[b, 0xA000 + i] for i in range(0x2000)) for b in range(4))


def reboot(sav):
    pb = PyBoy(ROM, window="null", sound_emulated=False, log_level="ERROR", ram_file=io.BytesIO(sav))
    pb.set_emulation_speed(0)
    res = game.fast_continue(pb, 8000)
    tick(pb, 60)
    return pb, game.GameMemory(pb.memory, RD), res


def records_of_party(g):
    return [(m.data, m.ot, m.nick) for m in g.party()]


def pika_visible(pb):
    return pb.memory[0xC1F2] != 0xFF        # wSpritePikachuStateData1ImageIndex (0xFF = not drawn)


PALLET_TO_R1 = "down left left left left up up up up up up right right up up up up up up up up".split()
ROOM_TO_OAK = ("right right up up up up up up right right left down down down down down left left left left "
               "down down down right right right right right up up up up up up up").split()


# =================================================================================================================
def test_wild_catch_faint():
    print("--- wild battle on Route 1, catch with a ball, faint ---")
    pb, g = emu("pallet_after_lab.state")
    m = pb.memory
    walk(pb, PALLET_TO_R1)
    check("walked to Route 1 grass", xy(pb)[0] == 0x0C, xy(pb))
    set_bag(pb, [])
    check("has_poke_balls() False with an empty bag", not g.has_poke_balls())
    set_bag(pb, [(0x14, 1), (0x04, 5), (0x01, 3)])           # POTION, POKE_BALL x5, MASTER_BALL x3
    check("has_poke_balls() True with balls", g.has_poke_balls())
    set_bag(pb, [(0x14, 1)])
    check("has_poke_balls() False with only a Potion", not g.has_poke_balls())
    set_bag(pb, [(0x04, 5), (0x01, 3)])
    save_fixture(pb, "soullink_route1_grass.state")
    check("battle_info() None in the overworld", g.battle_info() is None and g.battle_party_slot() is None)

    check("wild encounter started", step_until_battle(pb, ("up", "down")))
    save_fixture(pb, "soullink_wild_battle.state")
    samples = [g.battle_info()]
    sp = m[game.ENEMY_MON_SPECIES2]
    frame0_key_bytes = (m[0xCFEB], m[0xCFF0], m[0xCFF1])           # wEnemyMonCatchRate, wEnemyMonDVs
    slot_samples = advance_record(pb, in_battle_menu, lambda: (g.battle_info(), g.battle_party_slot()))
    infos = samples + [s[0] for s in slot_samples]
    final = infos[-1]
    check("battle_info at frame 0 == at the battle menu (wild)", infos[0] == final and final["kind"] == "wild"
          and final["dex"] == RD.dex_no(sp) and not final["safari"], (infos[0], final))
    check("battle_info constant from frame 0 to the menu", all(i == final for i in infos), set(map(str, infos)))
    first = next((i for i, s in enumerate(slot_samples) if s[1] is not None), None)
    info(f"wild dex {final['dex']}; battle_party_slot() None until frame {first + 1 if first is not None else '?'} "
         f"(send-out), menu at frame {len(slot_samples)}")
    check("battle_party_slot() None until the player's mon is out, then (0, hp)",
          first is not None and all(s[1] is None for s in slot_samples[:first])
          and slot_samples[-1][1] == (0, g.party()[0].hp))

    # real catch with a Master Ball (bag index 1)
    n0 = m[game.PARTY_COUNT]
    use_item_in_battle(pb, 1)
    check("battle ended after the catch", finish_battle(pb) and wait_safe(pb, g))
    party = g.party_checked()
    check("caught mon joined the party", party is not None and len(party) == n0 + 1
          and RD.dex_no(party[-1].species) == final["dex"], party and [RD.species_name(x.species) for x in party])
    caught = party[-1]
    check("wEnemyMonCatchRate/DVs at frame 0 == the caught mon's bytes 7/27/28 (mon_key known at encounter)",
          frame0_key_bytes == (caught.data[7], caught.data[27], caught.data[28]), (frame0_key_bytes, caught.data.hex()))
    check("ball used up from the bag", g.bag_items() == [(0x04, 5), (0x01, 2)], g.bag_items())

    # faint: Pikachu (slot 0) at 1 HP via a setup write, Growl until the enemy knocks it out
    m[game.PARTY_MONS + 1], m[game.PARTY_MONS + 2] = 0, 1
    check("second wild encounter", step_until_battle(pb, ("up", "down")))
    log = []

    def sample():
        return ((m[game.BATTLE_MON_HP] << 8) | m[game.BATTLE_MON_HP + 1],
                (m[game.PARTY_MONS + 1] << 8) | m[game.PARTY_MONS + 2], g.battle_party_slot())
    for _ in range(12):
        log += advance_record(pb, lambda p: in_battle_menu(p) or has(p, "Use next") or m[game.IS_IN_BATTLE] == 0,
                              sample)
        if not in_battle_menu(pb):
            break
        fight_move(pb, 1)                                   # GROWL
        for _ in range(30):
            pb.tick()
            log.append(sample())
    check("'Use next POKéMON?' after the faint", has(pb, "Use next"))
    f_battle0 = next((i for i, s in enumerate(log) if s[0] == 0), None)
    f_party0 = next((i for i, s in enumerate(log) if s[1] == 0), None)
    f_slot0 = next((i for i, s in enumerate(log) if s[2] == (0, 0)), None)
    check("battle_party_slot() == (0, 0) on the same frame wBattleMonHP hits 0",
          f_battle0 is not None and f_slot0 == f_battle0, (f_battle0, f_slot0))
    check("party struct HP gets 0 written after the faint", f_party0 is not None and f_party0 > f_battle0)
    check("battle HP never 0 before the faint frame", all(s[0] > 0 for s in log[:f_battle0]))
    info(f"faint: wBattleMonHP=0 at sample {f_battle0}, party HP=0 {f_party0 - f_battle0} frames later "
         f"(RemoveFaintedPlayerMon -> ReadPlayerMonCurHPAndStatus, before the 'fainted!' text)")
    save_fixture(pb, "soullink_faint.state")

    # YES -> choose slot 1 -> sent out: index switches with the new mon's HP
    pa._press(pb, "a")
    pa._advance_until(pb, lambda p: has(p, "Bring out which"), what="party menu")
    pa._move_cursor(pb, 1)
    pb.button_press("a")
    seq = []
    for _ in range(3):
        pb.tick()
        seq.append(g.battle_party_slot())
    pb.button_release("a")
    seq += advance_record(pb, in_battle_menu, g.battle_party_slot)
    want = (1, g.party()[1].hp)
    check("next mon sent out: battle_party_slot() == (1, hp), never (1, 0)",
          seq[-1] == want and all(s in ((0, 0), want, None) for s in seq), set(seq))
    use_item_in_battle(pb, 1)
    finish_battle(pb)
    wait_safe(pb, g)
    party = g.party_checked()
    check("third mon caught", party is not None and len(party) == 3)
    save_fixture(pb, "soullink_party3.state")
    pb.stop(save=False)
    return caught


def test_special_battles():
    print("--- special battles ---")
    # Pokémon Tower ghost without / with the Silph Scope
    for scope in (False, True):
        pb, g = emu("pallet_after_lab.state")
        ok = warp_via_lab_door(pb, game.POKEMON_TOWER_1F + 2, 0)     # 3F
        set_bag(pb, [(0x04, 5)] + ([(game.SILPH_SCOPE, 1)] if scope else []))
        ok = ok and step_until_battle(pb, ("left", "right", "up", "down"))
        infos = [g.battle_info()]
        for _ in range(400):
            pb.tick()
            infos.append(g.battle_info())
        nick = text.decode(bytes(pb.memory[0xCFD9 + i] for i in range(11)))       # wEnemyMonNick
        want = "wild" if scope else "special"
        check(f"Pokémon Tower 3F {'with' if scope else 'without'} Silph Scope -> {want} from frame 0 "
              f"(game shows {nick})", ok and all(i == infos[0] for i in infos) and infos[0]["kind"] == want
              and (nick == "GHOST") != scope, (infos[0], nick))
        pb.stop(save=False)

    # Marowak on 6F (RESTLESS_SOUL can never be caught)
    for scope in (False, True):
        pb, g = emu("pallet_after_lab.state")
        m = pb.memory
        ok = warp_via_lab_door(pb, game.POKEMON_TOWER_6F, 1)        # arrives at (9,16)
        set_bag(pb, [(0x04, 5)] + ([(game.SILPH_SCOPE, 1)] if scope else []))
        m[0xD13B] = 5                  # wNumberOfNoRandomBattleStepsLeft: no random battle
        step(pb, "right")                                            # (10,16) = PokemonTower6FMarowakCoords
        ok = ok and tick_until_battle(pb, press_a=True)
        infos = [g.battle_info()]
        for _ in range(300):
            pb.tick()
            infos.append(g.battle_info())
        check(f"Marowak (6F, {'with' if scope else 'without'} scope) -> special, dex 105",
              ok and all(i == infos[0] for i in infos) and infos[0]["kind"] == "special" and infos[0]["dex"] == 105,
              infos[0])
        pb.stop(save=False)

    # Old Man catching demo: run the game's own SCRIPT_VIRIDIANCITY_OLD_MAN_START_CATCH_TRAINING
    pb, g = emu("pallet_after_lab.state")
    m = pb.memory
    ok = warp_via_lab_door(pb, 0x01, 0)                              # Viridian City
    m[0xD5F3] = 3                                                    # wViridianCityCurScript
    ok = ok and tick_until_battle(pb)
    infos = [(g.battle_info(), g.battle_party_slot())]
    infos += advance_record(pb, in_battle_menu, lambda: (g.battle_info(), g.battle_party_slot()))
    check("Old Man demo -> special from frame 0 (wBattleType=1), battle_party_slot None",
          ok and m[game.BATTLE_TYPE] == 1 and all(i[0] == infos[0][0] and i[1] is None for i in infos)
          and infos[0][0]["kind"] == "special" and infos[0][0]["dex"] == 19, infos[0])
    pb.stop(save=False)

    # Oak catching Pikachu at the start of the game (BATTLE_TYPE_PIKACHU), from the bedroom
    pb, g = emu("room.state")
    walk(pb, ROOM_TO_OAK, pause=30)
    ok = tick_until_battle(pb, maxf=8000, press_a=True)
    infos = [(g.battle_info(), g.battle_party_slot())]
    infos += advance_record(pb, lambda p: p.memory[game.IS_IN_BATTLE] == 0,
                            lambda: (g.battle_info(), g.battle_party_slot()), maxf=8000)
    inb = [i for i in infos if i[0] is not None]
    check("Oak's Pikachu battle -> special from frame 0 (wBattleType=4), battle_party_slot None",
          ok and all(i == inb[0] for i in inb) and inb[0][0]["kind"] == "special" and inb[0][0]["dex"] == 25
          and inb[0][1] is None, inb[0])
    pb.stop(save=False)

    # Safari Zone Center grass
    pb, g = emu("pallet_after_lab.state")
    m = pb.memory
    ok = warp_via_lab_door(pb, game.SAFARI_ZONE_CENTER, 0)
    walk(pb, "up up left left left up".split())
    set_bag(pb, [])
    m[game.NUM_SAFARI_BALLS] = 0
    no_balls = g.has_poke_balls()
    m[game.NUM_SAFARI_BALLS], m[0xD70C], m[0xD70D] = 30, 0x01, 0xF4  # 30 balls, wSafariSteps = 500
    check("has_poke_balls() follows wNumSafariBalls inside the Safari Zone", not no_balls and g.has_poke_balls())
    ok = ok and step_until_battle(pb, ("left", "right", "up", "down"))
    infos = [(g.battle_info(), g.battle_party_slot())]
    infos += advance_record(pb, lambda p: has(p, "BALL", "BAIT"), lambda: (g.battle_info(), g.battle_party_slot()))
    check("Safari Zone -> wild + safari=True from frame 0, battle_party_slot None",
          ok and all(i == infos[0] for i in infos) and infos[0][0]["kind"] == "wild" and infos[0][0]["safari"]
          and infos[0][1] is None, infos[0])
    pb.stop(save=False)

    # trainer battle (SPEC.md ghost-battle recipe: the game's own InitBattle with a trainer class)
    pb, g = emu("pallet_after_lab.state")
    m = pb.memory
    wait_safe(pb, g)
    g.trigger_trainer_battle()
    ok = tick_until_battle(pb)
    save_fixture(pb, "soullink_trainer_battle.state")
    first_species = m[game.ENEMY_PARTY_SPECIES]
    infos = [(g.battle_info(), g.battle_party_slot())]
    infos += advance_record(pb, in_battle_menu, lambda: (g.battle_info(), g.battle_party_slot()))
    check("trainer battle -> kind trainer from frame 0, dex of the first enemy mon",
          ok and all(i[0] == infos[0][0] for i in infos) and infos[0][0]["kind"] == "trainer"
          and infos[0][0]["dex"] == RD.dex_no(first_species), infos[0])
    check("trainer battle: battle_party_slot None until send-out, then (0, hp)",
          infos[0][1] is None and infos[-1][1] == (0, g.party()[0].hp), infos[-1])
    pb.stop(save=False)


def test_hall_of_fame():
    print("--- Hall of Fame ---")
    pb, g = emu("pallet_after_lab.state")
    m = pb.memory
    check("not in the Hall of Fame, count 0", not g.in_hall_of_fame() and g.hall_of_fame_count() == 0)
    ok = warp_via_lab_door(pb, game.HALL_OF_FAME_MAP, 0)
    check("in_hall_of_fame() True in map $76", ok and g.in_hall_of_fame())
    f, inc = 0, None
    while f < 6000 and inc is None:
        if f % 40 == 0:
            pb.button_press("a")
        if f % 40 == 3:
            pb.button_release("a")
        pb.tick()
        f += 1
        if g.hall_of_fame_count() == 1:
            inc = f
    check("wNumHoFTeams incremented by the game's HoF script", inc is not None and g.in_hall_of_fame())
    info(f"wNumHoFTeams 0->1 {inc} frames after arriving (HoF animation start, before the save)")
    # the game saves (SaveGameData) only after the HoF display and the credits; sNumHoFTeams = SRAM 1:A84E
    # (sMainData A5A3 + wNumHoFTeams - wMainDataStart D2F6)
    waited = 0
    while waited < 60000 and m[1, 0xA84E] != 1:
        if waited % 40 == 0:
            pb.button_press("a")
        if waited % 40 == 3:
            pb.button_release("a")
        pb.tick()
        waited += 1
    pb.button_release("a")
    check("the HoF script saved the game (SRAM sNumHoFTeams = 1)", m[1, 0xA84E] == 1)
    info(f"sNumHoFTeams written to SRAM {inc + waited} frames after arriving (after the credits)")
    tick(pb, 600)                                     # let SaveGameData finish the checksum
    sav = sram(pb)
    pb.stop(save=False)
    pb, g, res = reboot(sav)
    check("after the game's HoF save + reload: hall_of_fame_count() == 1", res == "loaded" and g.hall_of_fame_count() == 1,
          (res, g.hall_of_fame_count()))
    pb.stop(save=False)


def test_daycare(caught):
    print("--- Day Care ---")
    pb, g = emu("pallet_after_lab.state")
    m = pb.memory
    m[game.PARTY_COUNT] = 2                       # setup: the Route 1 catch joins the Pallet state's party
    g.write_party_slot(1, caught)
    m[game.PARTY_SPECIES + 2] = 0xFF
    check("daycare_records() [] when empty", g.daycare_records() == [])
    ok = warp_via_lab_door(pb, 0x48, 0)           # DAYCARE
    walk(pb, ["up", "up", "up"])                  # below the gentleman at (2,3)
    for _ in range(10):
        pa._press(pb, "a", hold=6, gap=14)
        if m[game.FONT_LOADED] & 1:
            break
    pa._advance_until(pb, lambda p: has(p, "YES", "NO"), what="day care yes/no")
    pa._select(pb, 0)
    pa._advance_until(pb, lambda p: has(p, "Choose a POKe"), what="party menu")
    pa._move_cursor(pb, 1)
    pa._press(pb, "a")
    mash_a(pb, lambda: g.is_safe() and m[game.PARTY_COUNT] == 1, maxf=3000)
    tick(pb, 100)
    recs = g.daycare_records()
    check("real Day Care deposit: daycare_records() has the mon", ok and len(recs) == 1 and recs[0][0] == caught.species)
    r = recs[0]
    check("daycare mon_key == party mon_key", game.mon_key(r) == game.mon_key(caught.data))
    check("daycare record = box format (struct w/ box level = level, OT, nick)",
          r[:3] + r[4:33] == caught.data[:3] + caught.data[4:33] and r[3] == caught.level
          and r[33:44] == caught.ot and r[44:] == caught.nick, r.hex())
    save_fixture(pb, "soullink_daycare.state")
    # take it back (pays ¥100)
    for _ in range(10):
        pa._press(pb, "a", hold=6, gap=14)
        if m[game.FONT_LOADED] & 1:
            break
    mash_a(pb, lambda: g.is_safe() and m[game.PARTY_COUNT] == 2, maxf=4000)
    tick(pb, 60)
    party = g.party()
    check("real Day Care withdraw: daycare empty, same mon_key in the party",
          g.daycare_records() == [] and len(party) == 2 and game.mon_key(party[1].data) == game.mon_key(caught.data),
          (len(party), g.daycare_records()))
    pb.stop(save=False)


def test_box_moves():
    print("--- box moves vs Bill's PC (Viridian Pokémon Center) ---")
    pb, g = emu("pokecenter_pc.state")
    m = pb.memory
    orig = g.party()
    keys = {game.mon_key(x.data) for x in orig}
    check("pokecenter_pc fixture: party of 4, empty box", len(orig) == 4 and g.current_box_records() == [])
    check("move_party_to_box(9) / move_box_to_party(0) refuse bad indexes",
          not g.move_party_to_box(9) and not g.move_box_to_party(0))

    # forced deposit of Pidgey (slot 1)
    check("move_party_to_box(1) -> True", g.move_party_to_box(1))
    box = g.current_box_records()
    ours = box[0] if box else b""
    check("box record: level byte = party level, rest = party struct, OT, nick",
          len(box) == 1 and ours[3] == orig[1].level and ours[:3] + ours[4:33] == orig[1].data[:3] + orig[1].data[4:33]
          and ours[33:44] == orig[1].ot and ours[44:] == orig[1].nick)
    check("party shifted up", [x.data for x in g.party()] == [orig[0].data, orig[2].data, orig[3].data]
          and g.party_checked() is not None)
    lines = party_menu_lines(pb)
    check("party menu shows the 3 remaining mons (names, levels, HP)", party_menu_matches(lines, g.party()),
          "\n".join(lines[:8]))
    check("is_safe() after closing the party menu", wait_safe(pb, g))

    pa.open_pc(pb)
    lines = pc_withdraw_list_lines(pb)
    check("Bill's PC lists the deposited mon", "PIDGEY" in "\n".join(lines[2:12]), "\n".join(lines))
    check("real withdraw of the forced deposit", pa.withdraw(pb, 0))
    check("withdrawn party struct == original (level + stats recomputed by the game)",
          g.party()[3].data == orig[1].data and g.party()[3].ot == orig[1].ot and g.party()[3].nick == orig[1].nick)
    check("real deposit of the same mon", pa.deposit(pb, 3))
    check("game's deposit record == move_party_to_box's record", g.current_box_records() == [ours])
    pa.close_pc(pb)
    wait_safe(pb, g)

    # forced withdraw
    check("move_box_to_party(0) -> True", g.move_box_to_party(0))
    check("forced withdraw == original party struct (CalcLevelFromExperience + CalcStats)",
          g.party()[3].data == orig[1].data and g.current_box_records() == [])
    check("mon_keys unchanged after the round trips", {game.mon_key(x.data) for x in g.party()} == keys)
    lines = party_menu_lines(pb)
    check("party menu after the forced withdraw", party_menu_matches(lines, g.party()), "\n".join(lines[:10]))

    check("move_party_to_box(2) (Rattata)", g.move_party_to_box(2))
    wait_safe(pb, g)
    before_save = (records_of_party(g), g.current_box_records())
    save_game(pb)
    check("is_safe() after SAVE", wait_safe(pb, g))
    sav = sram(pb)
    pb.stop(save=False)
    pb, g, res = reboot(sav)
    check("reload after START>SAVE: party and box identical", res == "loaded"
          and (records_of_party(g), g.current_box_records()) == before_save, res)
    check("reloaded party passes party_checked()", g.party_checked() is not None)
    pb.stop(save=False)

    # full box / last mon / full party refusals (counts set up in RAM, the methods must not write)
    pb, g = emu("pokecenter_pc.state")
    m = pb.memory
    m[game.BOX_COUNT] = 20
    snap = bytes(m[0xD162:0xDF00])
    check("box full (20) -> move_party_to_box False, RAM untouched",
          not g.move_party_to_box(1) and bytes(m[0xD162:0xDF00]) == snap)
    m[game.BOX_COUNT] = 0
    m[game.BOX_SPECIES] = 0xFF
    m[game.PARTY_COUNT] = 1
    m[game.PARTY_SPECIES + 1] = 0xFF
    check("last party mon -> move_party_to_box False", not g.move_party_to_box(0) and m[game.PARTY_COUNT] == 1)
    pb.stop(save=False)


def test_pikachu():
    print("--- following starter Pikachu ---")
    # reference: the real Bill's PC
    pr, gr = emu("pokecenter_pc.state")
    pa.open_pc(pr)
    dep = pa.deposit(pr, 0)
    pa.close_pc(pr)
    tick(pr, 60)
    real_after_dep = (pr.memory[game.PIKACHU_HAPPINESS], pr.memory[game.PIKACHU_MOOD], pika_visible(pr))
    real_rec = gr.current_box_records()[0]
    pa.open_pc(pr)
    wd = pa.withdraw(pr, 0)
    pa.close_pc(pr)
    tick(pr, 60)
    real_w = gr.party()[-1]
    real_vis_before_step = pika_visible(pr)
    step(pr, "left")
    real_vis_after_step = pika_visible(pr)
    pr.stop(save=False)
    check("reference: Bill's PC deposits and withdraws the following Pikachu", dep and wd)
    info(f"real PC deposit: happiness/mood/visible -> {real_after_dep}; after withdraw visible "
         f"{real_vis_before_step} until the first step, then {real_vis_after_step}")

    pb, g = emu("pokecenter_pc.state")
    m = pb.memory
    h0, mood0 = m[game.PIKACHU_HAPPINESS], m[game.PIKACHU_MOOD]
    pika = g.party()[0]
    check("is_starter_pikachu() recognises slot 0", g.is_starter_pikachu(pika.data, pika.ot)
          and not g.is_starter_pikachu(g.party()[1].data, g.party()[1].ot))
    check("move_party_to_box(0) (Pikachu)", g.move_party_to_box(0))
    vis = []
    for _ in range(60):
        pb.tick()
        vis.append(pika_visible(pb))
    check("forced deposit: box record == real Bill's PC record (box level 0 -> 5)",
          g.current_box_records()[0] == real_rec and pika.data[3] == 0 and real_rec[3] == 5)
    check("forced deposit: happiness/mood change == real PC (PIKAHAPPY_DEPOSITED)",
          (m[game.PIKACHU_HAPPINESS], m[game.PIKACHU_MOOD]) == real_after_dep[:2], (h0, mood0, m[game.PIKACHU_HAPPINESS],
                                                                                     m[game.PIKACHU_MOOD]))
    check("forced deposit: following sprite hidden by the game on the next frame", not any(vis))
    walk(pb, ["left", "left", "down"])
    check("no Pikachu sprite while walking with Pikachu boxed", not pika_visible(pb))
    pa_ok = False
    walk(pb, ["up", "right", "right"])
    if wait_safe(pb, g) and xy(pb) == (0x29, 13, 4):
        step(pb, "up")      # face the PC again
        pa.open_pc(pb)
        pa_ok = not pa.release(pb, 0)               # IsThisBoxMonStarterPikachu refuses the release
        pa.close_pc(pb)
    check("Bill's PC still treats the boxed mon as the starter Pikachu (release refused)", pa_ok)
    happy_boxed = m[game.PIKACHU_HAPPINESS]

    check("move_box_to_party(0) (Pikachu back)", g.move_box_to_party(0))
    check("forced withdraw: party struct == real PC withdraw", g.party()[-1].data == real_w.data)
    vis0 = pika_visible(pb)
    step(pb, "left")
    tick(pb, 30)
    vis1 = pika_visible(pb)
    check("following sprite: like the real PC, hidden until the first step, then back", vis0 == real_vis_before_step
          and vis1 and real_vis_after_step, (vis0, vis1))
    walk(pb, ["down", "right", "up"])
    check("Pikachu keeps following", pika_visible(pb))
    check("happiness not changed by the withdraw (only the deposit costs happiness)",
          m[game.PIKACHU_HAPPINESS] >= happy_boxed)
    lines = party_menu_lines(pb)
    check("party menu with Pikachu last", party_menu_matches(lines, g.party()), "\n".join(lines[:10]))
    wait_safe(pb, g)
    before = (records_of_party(g), g.current_box_records(), m[game.PIKACHU_HAPPINESS])
    save_game(pb)
    sav = sram(pb)
    pb.stop(save=False)
    pb, g, res = reboot(sav)
    tick(pb, 30)
    step(pb, "left")
    step(pb, "right")
    check("save + reload: party/box/happiness intact and Pikachu follows",
          res == "loaded" and (records_of_party(g), g.current_box_records(), pb.memory[game.PIKACHU_HAPPINESS]) == before
          and pika_visible(pb))
    pb.stop(save=False)


def test_tables():
    print("--- areas / mon_key ---")
    check("AREAS covers every map id 0x00-0xF8", sorted(game.AREAS) == list(range(0xF9)))
    check("every area key has a name", all(game.area_name(k) for k in set(game.AREAS.values())))
    same = [(0x3B, 0x3C, 0x3D), (0x52, 0xE8), (0x8E, 0x93, 0x94), (0xD9, 0xDA, 0xDB, 0xDC, 0xDD),
            (0x28, 0x00), (0x31, 0x0D), (0x89, 0x06), (0xA5, 0xD8), (0xB5, 0xEB), (0xC0, 0x9F), (0x6C, 0xC6)]
    check("dungeon floors / interiors share their area", all(len({game.area_of(i) for i in grp}) == 1 for grp in same))
    check("area names", game.area_name(game.area_of(0x0C)) == "Route 1" and game.area_name(game.area_of(0x3C)) == "Mt. Moon"
          and game.area_name(game.area_of(0xDC)) == "Safari Zone")


def main():
    caught = test_wild_catch_faint()
    test_special_battles()
    test_hall_of_fame()
    test_daycare(caught)
    test_box_moves()
    test_pikachu()
    test_tables()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
