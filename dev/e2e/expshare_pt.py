"""Exp. Share All in Pokémon Platinum, on the real ROM (client/yellowlink/platinum_exp.py).

Every case is played twice from the same savestate: rule OFF (must be Platinum's own numbers) and rule ON.
Experience is measured in the save party after the battle (the battle copies its party back), and the hooks'
own record of E (the game's base award) is checked against floor(baseExp(ROM) * enemyLevel / 7).

Fixture (built at the start from dev/states/pt_pc.dsst, nothing is written to dev/states):
  * VAR_ROUTE_202_STATE = 1 (skips the catching-tutorial coord event), battle style SET (no "change Pokémon?"),
  * walk out of the Sandgem Pokémon Center to Route 202 (tall grass at (165, 819..825); wild battles on the way
    are fled),
  * the party is written into the save RAM (body + 0x98) before each case: records built from the game's own
    TURTWIG (pk4 + the ROM's stats/growth tables), e.g. a Lv 50 lead so it wins in one hit.
Cases: A wild, lead alone vs 2 benched + fainted + egg; B wild, two participants (switch once); C trainer
(Youngster Tristan, Route 202); D a benched mon levels up (move-learn prompt) and one evolves after battle;
E a benched mon holds Exp. Share.
Run: python dev/e2e/expshare_pt.py      (ends with ALL PASS; ~4 min; screenshots of the level-up / move-learn
steps in the temp folder printed at the end, or $YL_SHOTS)
Other Platinum tests with the hooks installed: python dev/e2e/with_expshare.py dev/platinum/verify.py (or
dev/e2e/platinum_boot.py).
"""
import io
import os
import struct
import sys
import tempfile

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
TMP = tempfile.mkdtemp(prefix="yl_expshare_")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "cfg")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.join(_REPO, "client"))

from yellowlink import nds_core, pk4, platinum_exp as X  # noqa: E402
from yellowlink.ndsrom import NDSRom, PlatinumData, lz77_decompress, EOS  # noqa: E402
from yellowlink.platinum import PlatinumMemory, PAGE_PARTY  # noqa: E402

ROM = os.path.join(STATES, "platinum.nds")
FAILS = []
SHOTS = os.environ.get("YL_SHOTS") or os.path.join(TMP, "shots")


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


# --- species / moves / items (pret generated/*.txt, line - 1) ------------------------------------------------
TURTWIG, STARLY, STARAVIA, BIDOOF = 387, 396, 397, 399
TACKLE, BITE, ABSORB, RAZOR_LEAF, WITHDRAW = 33, 44, 71, 75, 110
ITEM_EXP_SHARE, ITEM_LUCKY_EGG = 216, 231
VAR_ROUTE_202_STATE = 16519      # VarsFlags.vars[id - 0x4000], VarsFlags = body + 0xDAC
VARS_FLAGS = 0xDAC

data = PlatinumData(ROM)
_rom = NDSRom(ROM)
_pers = _rom.narc("poketool/personal/pl_personal.narc")
BASE_EXP = {}                    # SpeciesData.baseExpReward = personal byte 9


def base_exp(dex):
    if dex not in BASE_EXP:
        BASE_EXP[dex] = lz77_decompress(_pers[dex])[9]
    return BASE_EXP[dex]


# --- emulator ------------------------------------------------------------------------------------------------
core = nds_core.NDSCore(ROM, None, 0)
pm = PlatinumMemory(core)
ON = {"v": False}
handle = X.install_exp_share(core, lambda: ON["v"])


def shot(name):
    try:
        from PIL import Image
    except ImportError:
        return
    os.makedirs(SHOTS, exist_ok=True)
    Image.frombytes("RGB", (256, 384), core.screen_rgb()).save(os.path.join(SHOTS, name + ".png"))


def press(k, after=20, hold=6):
    core.button_press(k)
    core.tick(hold)
    core.button_release(k)
    core.tick(after)


def tap(x, y, after=20):
    core.touch(x, y)
    core.tick(6)
    core.touch_release()
    core.tick(after)


def pos():
    p = pm.position()
    return p["x"], p["y"]


def snapshot():
    b = io.BytesIO()
    core.save_state(b)
    b.seek(0)
    return b.getvalue()


def restore(s):
    core.load_state(io.BytesIO(s))
    core.tick(2)


def _step(key, run):
    if run:
        core.button_press("b")
    core.button_press(key)
    core.tick(8 if run else 16)
    core.button_release(key)
    if run:
        core.button_release("b")
    for _ in range(40):
        core.tick(1)
        if not pm.walking():
            break


def flee():
    for _ in range(80):
        if not pm.in_battle():
            break
        tap(128, 175, 30)
        press("b", 30)


def settle(maxf=400):
    """Wait until the player has control again (12 safe frames); flee wild battles. True if one was fled."""
    fled, ok = False, 0
    for _ in range(maxf):
        ok = ok + 1 if pm.is_safe() else 0
        if ok >= 12:
            return fled
        if pm.in_battle():
            flee()
            fled = True
            continue
        core.tick(1)
    return fled


def go(key, tiles, run=True):
    moved = 0
    while moved < tiles:
        b = pos()
        _step(key, run)
        if pos() == b:            # turning only, or bumped: try once more
            _step(key, run)
        if settle() and pos() == b:
            continue
        if pos() == b:
            return moved
        moved += 1
    return moved


# --- party records -------------------------------------------------------------------------------------------
def party_base():
    return pm.save_body() + PAGE_PARTY


def read_party(tries=600):
    """The save party, once every record is valid and two valid reads agree. (After a battle the field keeps
    working on party records for a while: a record can read in the game's half re-encrypted state on every
    other frame.)"""
    last = None
    for _ in range(tries):
        base = party_base()
        n = core.r32(base + 4)
        raw = [core.read(base + 8 + 236 * i, 236) for i in range(n)]
        if all(pk4.valid(r) for r in raw):
            if raw == last:
                return [pk4.PK4(pk4.decrypt(r)) for r in raw]
            last = raw
        core.tick(1)
    raise RuntimeError("party records not stable/valid")


def write_party(mons):
    base = party_base()
    core.w32(base + 4, len(mons))
    for i in range(6):
        rec = mons[i].encrypted() if i < len(mons) else pk4.EMPTY_PARTY
        core.write(base + 8 + 236 * i, rec)


def make_mon(template, species, level, *, exp=None, moves=None, item=0, hp=None, egg=False, pid=None):
    p = pk4.PK4(template.plain())
    if pid is not None:
        p.pid = pid
    p.species = species
    p.exp = data.exp_for_level(species, level) if exp is None else exp
    p.item = item
    p.evs = [0] * 6
    if moves:
        p.moves = (list(moves) + [0] * 4)[:4]
        p.pp = [data.move_pp(m) for m in p.moves]
        p.pp_ups = [0] * 4
    p.nickname_codes = data.species_name_codes(species) + [EOS]
    p.has_nickname = False
    p.is_egg = egg
    p.friendship = 70
    lv = data.level_for_exp(species, p.exp)
    p.party_level = lv
    st = pk4.calc_stats(p, data, lv)
    p.stats = st
    p.hp = st[0] if hp is None else hp
    p.status = 0
    return p


# --- battles -------------------------------------------------------------------------------------------------
def wild_encounter(max_steps=80):
    """Step up/down in the grass until a wild battle starts."""
    d = "up"
    for _ in range(max_steps):
        b = pos()
        _step(d, False)
        for _ in range(60):
            core.tick(1)
            if pm.in_battle():
                return True
        if pos() == b:
            d = "down" if d == "up" else "up"
    return False


def _px(x, y):
    i = (y * 256 + x) * 3
    return tuple(core.screen_rgb()[i:i + 3])


def menu_ready():
    """The battle command menu (FIGHT / BAG / RUN / POKeMON) is on the touch screen."""
    return _px(128, 250) == (232, 56, 56) and _px(215, 357) == (88, 176, 40)


def wait_menu(maxf=2400):
    """Wait until the battle command menu is up; False if the battle ended first."""
    for _ in range(maxf // 5):
        if not pm.in_battle():
            return False
        if menu_ready():
            return True
        core.tick(5)
        if _ % 40 == 39:
            press("b", 10)          # skip messages that wait for a button
    return False


def run_battle(max_iter=400, label="b"):
    """Mash A through a battle (FIGHT, move 1; A also answers the move-learn prompts: forget the first move),
    then through the evolution scene etc. until the field is back."""
    i, seen = 0, set()
    for i in range(max_iter):
        if not pm.in_battle():
            break
        press("a", 24)
        step = handle.trace[-1] if handle.trace else None
        if step and step[1] in (7, 11, 17, 21) and step not in seen:      # level-up text/stats, move prompts
            seen.add(step)
            shot(f"{label}_slot{step[0]}_seq{step[1]}")
    shot(label + "_end")
    # after battle: evolution scene etc. -> mash A until the field is back
    for _ in range(200):
        if pm.is_safe():
            break
        press("a", 24)
    return i


# --- fixture -------------------------------------------------------------------------------------------------
def build_fixture():
    """pt_pc -> Route 202 tall grass at (165, 825), field idle. Returns the snapshot."""
    core.load_state(os.path.join(STATES, "pt_pc.dsst"))
    core.tick(2)
    body = pm.save_body()
    core.w16(body + VARS_FLAGS + 2 * (VAR_ROUTE_202_STATE - 0x4000), 1)
    core.w16(body + 0x64, core.r16(body + 0x64) | 0x40)       # Options.battleStyle = SET
    go("down", 6, False); go("left", 4, False); go("down", 3, False)
    settle(900)
    go("down", 1); go("right", 6); go("up", 10); go("right", 2); go("up", 10)    # Sandgem -> Route 202
    go("left", 4); go("down", 1); go("left", 16)
    return pos()


# --- parties (built from the game's own TURTWIG record t) --------------------------------------------------------
def lead(t):
    return make_mon(t, TURTWIG, 50)


def party_A(t):
    """lead + B1 one exp point below Lv 13 (learns Razor Leaf with 4 moves known) + B2 one point below Lv 14
    (Starly -> Staravia) + a fainted mon + an egg."""
    return [lead(t),
            make_mon(t, TURTWIG, 12, exp=data.exp_for_level(TURTWIG, 13) - 1, moves=[TACKLE, WITHDRAW, ABSORB, BITE],
                     pid=0x12345678),
            make_mon(t, STARLY, 13, exp=data.exp_for_level(STARLY, 14) - 1, moves=[TACKLE, 45, 98, 0], pid=0x2345678A),
            make_mon(t, BIDOOF, 5, hp=0, pid=0x3456789B, moves=[TACKLE, 0, 0, 0]),
            make_mon(t, TURTWIG, 5, egg=True, pid=0x456789AC)]


def party_B(t):
    """lead + B1 (will be switched in) + B2 benched."""
    return [lead(t), make_mon(t, TURTWIG, 12, moves=[TACKLE, WITHDRAW, ABSORB, BITE], pid=0x12345678),
            make_mon(t, STARLY, 10, moves=[TACKLE, 45, 98, 0], pid=0x2345678A)]


def party_E(t):
    """lead + B1 holding Exp. Share + B2 holding a Lucky Egg + B3 benched."""
    return [lead(t), make_mon(t, TURTWIG, 10, item=ITEM_EXP_SHARE, pid=0x12345678),
            make_mon(t, STARLY, 10, item=ITEM_LUCKY_EGG, moves=[TACKLE, 45, 98, 0], pid=0x2345678A),
            make_mon(t, BIDOOF, 8, moves=[TACKLE, 0, 0, 0], pid=0x3456789B)]


# --- expected numbers ------------------------------------------------------------------------------------------
def expected(party, awards, on, trainer):
    """slot -> total exp the party should gain over `awards` (rule on/off), from the game's formulas."""
    out = [0] * len(party)
    for a in awards:
        E, parts = a["exp"], a["participants"]
        alive = [i for i, m in enumerate(party) if m.species and not m.is_egg and m.hp > 0]
        holders = [i for i in alive if party[i].item == ITEM_EXP_SHARE]
        if on:
            base = X.plan(E, parts, sum(1 << i for i in alive), len(party))
        else:
            g, sh = X.original_split(E, sum(1 for i in alive if parts >> i & 1), len(holders))
            base = {}
            for i in alive:
                v = (g if parts >> i & 1 else 0) + (sh if i in holders else 0)
                if parts >> i & 1 or i in holders:
                    base[i] = v
        for i, v in base.items():
            out[i] += X.apply_modifiers(v, trainer=trainer, lucky_egg=party[i].item == ITEM_LUCKY_EGG)
    return out


def play(start, on, party_fn, label, battle=None, plan=None):
    """From `start` (field): write the party, start a battle (wild by default), win it by mashing A (after
    running `plan` at the first command menu), back to the field. Returns before/after parties + hook records."""
    restore(start)
    write_party(party_fn(read_party()[0]))
    before = read_party()
    ON["v"] = on
    if battle is None:
        if not wild_encounter():
            raise RuntimeError("no wild encounter")
    else:
        battle()
    handle.awards, handle.given, handle.trace = [], [], []
    if plan:
        if not wait_menu():
            raise RuntimeError("no battle menu")
        plan()
    run_battle(label=label)
    after = read_party()
    return {"before": before, "after": after, "awards": list(handle.awards), "given": list(handle.given),
            "trace": list(handle.trace)}


def deltas(r):
    return [a.exp - b.exp for b, a in zip(r["before"], r["after"])]


def ev_deltas(r):
    return [sum(a.evs) - sum(b.evs) for b, a in zip(r["before"], r["after"])]


def check_E(r, label):
    for a in r["awards"]:
        want = X.base_award(base_exp(a["enemy_species"]), a["enemy_level"])
        check(f"{label}: E = floor(baseExp * level / 7) from the ROM ({a['enemy_species']} Lv{a['enemy_level']})",
              a["exp"] == want, f"hook {a['exp']} vs {want}")


def switch_to_slot1():
    """Battle menu -> POKeMON -> 2nd party member -> SHIFT."""
    core.tick(40)
    tap(215, 165, 90)
    tap(192, 38, 60)
    tap(128, 108, 60)


def trainer_battle():
    """From the grass at (165, 825): north to (165, 817), one step east into Youngster Tristan's line of sight."""
    go("up", 8, run=False)
    if pos() != (165, 817):
        raise RuntimeError(f"not at (165, 817): {pos()}")
    _step("right", False)
    for _ in range(120):
        if pm.in_battle():
            return
        press("a", 20)
    raise RuntimeError("Tristan did not start a battle")


if __name__ == "__main__":
    where = build_fixture()
    check("fixture: Route 202 (map 343) grass, idle", pm.map_id() == 343 and pm.is_safe(), f"{where} map {pm.map_id()}")
    check("outside battles the hook addresses hold other code (overlay check needed and working)",
          not handle._code_ok(X.HOOK_CALC) and not handle._code_ok(X.HOOK_TASK))
    shot("fixture")
    FIX = snapshot()

    # A: lead alone vs two benched (one learns a move, one evolves) + fainted + egg
    off = play(FIX, False, party_A, "A_off")
    on = play(FIX, True, party_A, "A_on")
    for r, lab in ((off, "A off"), (on, "A on")):
        check(f"{lab}: one award, wild, participants = lead only", len(r["awards"]) == 1
              and r["awards"][0]["participants"] == 1 and not r["awards"][0]["trainer"], str(r["awards"]))
        check_E(r, lab)
    E = on["awards"][0]["exp"]
    d_off, d_on = deltas(off), deltas(on)
    print(f"   A: E = {E}; OFF gains {d_off}; ON gains {d_on}")
    check("A OFF = Platinum's own: lead E, everybody else 0", d_off == [E, 0, 0, 0, 0], str(d_off))
    check("A ON: lead gets the solo amount E", d_on[0] == E, str(d_on))
    check("A ON: both benched mons get exactly floor(E/2)", d_on[1] == d_on[2] == E // 2, str(d_on))
    check("A ON: fainted mon and egg get nothing", d_on[3] == 0 and d_on[4] == 0, str(d_on))
    check("A ON matches the rule's prediction", d_on == expected(on["before"], on["awards"], True, False))
    check("A EVs: OFF only the lead (+1), ON lead + both benched (+1 each), fainted/egg none",
          ev_deltas(off) == [1, 0, 0, 0, 0] and ev_deltas(on) == [1, 1, 1, 0, 0], f"{ev_deltas(off)} {ev_deltas(on)}")
    b1, b2 = on["after"][1], on["after"][2]
    tr = on["trace"]
    check("A ON: benched TURTWIG grew to Lv 13 natively (level, stats recalculated)",
          b1.party_level == 13 and b1.stats == pk4.calc_stats(b1, data, 13) and b1.hp > on["before"][1].hp,
          f"lv {b1.party_level} stats {b1.stats}")
    check("A ON: the game ran its level-up steps for slot 1 (effect, 'grew to Lv.' message, stat window)",
          all((1, s) in tr for s in (6, 7, 9, 11, 13, 14)), str(tr))
    check("A ON: move-learn prompt for slot 1 ('wants to learn', 'make it forget?', forget screen, 'Poof')",
          all((1, s) in tr for s in (16, 18, 20, 22, 24, 30)), str(tr))
    check("A ON: TURTWIG learnt RAZOR LEAF in place of TACKLE", b1.moves == [RAZOR_LEAF, WITHDRAW, ABSORB, BITE],
          str(b1.moves))
    check("A ON: benched STARLY reached Lv 14 and evolved into STARAVIA after the battle",
          on["before"][2].species == STARLY and b2.species == STARAVIA and b2.party_level == 14
          and b2.stats == pk4.calc_stats(b2, data, 14), f"species {b2.species} lv {b2.party_level}")
    check("A OFF: nothing levels or evolves on the bench",
          [m.species for m in off["after"]] == [m.species for m in off["before"]]
          and off["after"][1].party_level == 12, "")

    # B: two participants (switch once)
    off = play(FIX, False, party_B, "B_off", plan=switch_to_slot1)
    on = play(FIX, True, party_B, "B_on", plan=switch_to_slot1)
    for r, lab in ((off, "B off"), (on, "B on")):
        check(f"{lab}: one award with two participants (lead + switched-in slot 1)", len(r["awards"]) == 1
              and r["awards"][0]["participants"] == 3, str(r["awards"]))
        check_E(r, lab)
    E = on["awards"][0]["exp"]
    d_off, d_on = deltas(off), deltas(on)
    print(f"   B: E = {E}; OFF gains {d_off}; ON gains {d_on}")
    check("B OFF = Platinum's own split: floor(E/2) each participant, bench 0", d_off == [E // 2, E // 2, 0], str(d_off))
    check("B ON: both participants get the full E, bench floor(E/2)", d_on == [E, E, E // 2], str(d_on))

    # C: trainer battle (Youngster Tristan, STARLY Lv 5)
    off = play(FIX, False, party_B, "C_off", battle=trainer_battle)
    on = play(FIX, True, party_B, "C_on", battle=trainer_battle)
    for r, lab in ((off, "C off"), (on, "C on")):
        check(f"{lab}: trainer battle award(s), participants = lead", r["awards"] and all(
            a["trainer"] and a["participants"] == 1 for a in r["awards"]), str(r["awards"]))
        check_E(r, lab)
    d_off, d_on = deltas(off), deltas(on)
    Es = [a["exp"] for a in on["awards"]]
    print(f"   C: E = {Es}; OFF gains {d_off}; ON gains {d_on}")
    check("C OFF = Platinum's own: lead floor(E*1.5), bench 0",
          d_off == [sum(e * 150 // 100 for e in Es), 0, 0], str(d_off))
    check("C ON: lead floor(E*1.5), each benched floor(floor(E/2)*1.5)",
          d_on == [sum(e * 150 // 100 for e in Es)] + [sum((e // 2) * 150 // 100 for e in Es)] * 2, str(d_on))
    check("C ON matches the rule's prediction", d_on == expected(on["before"], on["awards"], True, True))

    # E: held Exp. Share / Lucky Egg on the bench
    off = play(FIX, False, party_E, "E_off")
    on = play(FIX, True, party_E, "E_on")
    E = on["awards"][0]["exp"]
    d_off, d_on = deltas(off), deltas(on)
    print(f"   E: E = {E}; OFF gains {d_off}; ON gains {d_on}")
    check("E OFF = Platinum's own Exp. Share: lead floor(E/2), holder floor(E/2), Lucky Egg/plain bench 0",
          d_off == [E // 2, E // 2, 0, 0], str(d_off))
    check("E OFF matches the original formula", d_off == expected(off["before"], off["awards"], False, False))
    check("E ON: Exp. Share holder gets floor(E/2) like any benched mon (no double), lead full E",
          d_on[0] == E and d_on[1] == E // 2 and d_on[3] == E // 2, str(d_on))
    check("E ON: Lucky Egg on the bench: floor(floor(E/2) * 1.5)", d_on[2] == (E // 2) * 150 // 100, str(d_on))

    # handle.remove(): back to the original game
    handle.remove()
    r = play(FIX, True, party_A, "removed")
    check("after handle.remove(): Platinum's own numbers again (bench 0) and no hook records",
          deltas(r)[1:3] == [0, 0] and deltas(r)[0] > 0 and not r["awards"], str(deltas(r)))

    print("screenshots in", SHOTS)
    print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}", flush=True)
    os._exit(0 if not FAILS else 1)
