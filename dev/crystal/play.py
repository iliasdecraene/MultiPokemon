"""Small 'bot' helpers to drive Crystal's overworld (dev only)."""
from common import ticks, tap, wram, screen_text

MAP_GROUP, MAP_NUMBER, Y_COORD, X_COORD = 0xDCB5, 0xDCB6, 0xDCB7, 0xDCB8
PLAYER_WALKING = 0xD4DD
STATE_FLAGS = 0xD0ED
SCRIPT_MODE = 0xD437
SCRIPT_FLAGS = 0xD434
BATTLE_MODE = 0xD22D
WINDOW_STACK = 0xCF78
JOY_DISABLE = 0xCFBE


def pos(pb):
    return (wram(pb, MAP_GROUP), wram(pb, MAP_NUMBER), wram(pb, X_COORD), wram(pb, Y_COORD))


def busy(pb):
    return (wram(pb, STATE_FLAGS) & 0xC0 or wram(pb, SCRIPT_MODE) or wram(pb, SCRIPT_FLAGS) & 4
            or wram(pb, BATTLE_MODE) or wram(pb, WINDOW_STACK) or wram(pb, PLAYER_WALKING) != 0xFF
            or wram(pb, JOY_DISABLE))


def idle(pb, need=20, limit=2000):
    """Tick until the overworld has been quiet for `need` frames."""
    q = 0
    for _ in range(limit):
        ticks(pb, 1)
        q = 0 if busy(pb) else q + 1
        if q >= need:
            return True
    return False


def step(pb, d, n=1, limit=120):
    """Walk n tiles in direction d ('up','down','left','right'). Returns tiles actually walked."""
    done = 0
    for _ in range(n):
        start = pos(pb)
        pb.button_press(d)
        ok = False
        for _ in range(limit):
            ticks(pb, 1)
            if pos(pb) != start:
                ok = True
                break
        pb.button_release(d)
        if not ok:
            return done
        done += 1
        # let the step settle
        for _ in range(40):
            if wram(pb, PLAYER_WALKING) == 0xFF:
                break
            ticks(pb, 1)
    return done


def mash(pb, button="a", until=None, limit=200, wait=30):
    """Press a button repeatedly until until(pb) is true (or the overworld is idle)."""
    for i in range(limit):
        if until is not None and until(pb):
            return True
        tap(pb, button, 4, wait)
        if until is None and not busy(pb):
            if idle(pb, 30, 60):
                return True
    return False


def screen_has(pb, s):
    return any(s in r for r in screen_text(pb))


def warp(pb, group, number, x, y):
    """Teleport like the script command `warp` does (only call while the overworld is idle)."""
    m = pb.memory
    m[1, MAP_GROUP], m[1, MAP_NUMBER], m[1, X_COORD], m[1, Y_COORD] = group, number, x, y
    m[1, 0xD001] = 0xFF            # wDefaultSpawnpoint = SPAWN_N_A
    m[0xFF9F] = 0xF1               # hMapEntryMethod = MAPSETUP_WARP
    m[1, 0xD432] = 1               # wMapStatus = MAPSTATUS_ENTER
    return idle(pb, 30, 2000)


CHERRYGROVE_PC = (26, 5, 8, 1)     # stand here, face right: the Pokémon Center PC


def goto_pc(pb):
    warp(pb, 26, 5, 3, 6)
    step(pb, "right", 2)
    step(pb, "up", 5)
    step(pb, "right", 3)
    step(pb, "up", 2)
    assert pos(pb) == CHERRYGROVE_PC, pos(pb)
    tap(pb, "right", 3, 20)


def choose(pb, label, limit=12, wait=20):
    """Move a menu cursor ('>') down/up until it points at `label`, then press A."""
    for d in ("down", "up"):
        for _ in range(limit):
            ticks(pb, wait)
            rows = screen_text(pb)
            if any(">" + label in r.replace("> ", ">") for r in rows):
                tap(pb, "a", 4, 40)
                return True
            if not any(">" in r for r in rows):
                continue
            tap(pb, d, 4, 8)
    return False


def wait_text(pb, s, limit=40, button="a", wait=30):
    """Press `button` until text `s` is on screen."""
    for _ in range(limit):
        if screen_has(pb, s):
            return True
        tap(pb, button, 4, wait)
    return screen_has(pb, s)


def pick_in_pc_list(pb, name, limit=24):
    """Bill's PC mon lists use a sprite cursor; the selected mon's SPECIES name is printed on row 14."""
    for d in ("down", "up"):
        for _ in range(limit):
            ticks(pb, 20)
            if screen_text(pb)[14].strip().startswith(name):
                tap(pb, "a", 4, 40)
                return True
            tap(pb, d, 4, 8)
    return False


def pc_open(pb):
    """From the overworld at the Cherrygrove PC: turn it on and open Bill's PC main menu."""
    goto_pc(pb)
    tap(pb, "a", 4, 40)
    assert wait_text(pb, "Access whose PC")
    ticks(pb, 20)
    assert choose(pb, "BILL")
    assert wait_text(pb, "WITHDRAW")


def pc_deposit(pb, name):
    assert choose(pb, "DEPOSIT")
    assert wait_text(pb, "PARTY PKMN", 10, "a", 20)
    assert pick_in_pc_list(pb, name)
    assert choose(pb, "DEPOSIT")
    assert wait_text(pb, "Stored", 10, "a", 20)
    assert wait_text(pb, "Choose a PKMN", 10, "a", 20)
    assert wait_text(pb, "WITHDRAW", 10, "b", 30)


def pc_withdraw(pb, name):
    assert choose(pb, "WITHDRAW")
    assert wait_text(pb, "Choose a PKMN", 10, "a", 20)
    assert pick_in_pc_list(pb, name)
    assert choose(pb, "WITHDRAW")
    assert wait_text(pb, "Choose a PKMN", 20, "a", 20)
    assert wait_text(pb, "WITHDRAW", 10, "b", 30)


def pc_close(pb):
    """SEE YA! (closes Bill's PC), then B at "Access whose PC?" turns the PC off."""
    assert choose(pb, "SEE YA")
    assert wait_text(pb, "Access whose PC", 10, "a", 20)
    for _ in range(20):
        tap(pb, "b", 4, 30)
        if idle(pb, 30, 60):
            return True
    return False


def pc_change_box(pb, n):
    """CHANGE BOX -> BOXn -> SWITCH -> YES (the game saves) [-> YES overwrite]."""
    assert choose(pb, "CHANGE BOX")
    assert wait_text(pb, "Choose a BOX", 10, "a", 20)
    assert choose(pb, "BOX%d " % n if n < 10 else "BOX%d" % n)
    assert choose(pb, "SWITCH")
    for _ in range(40):
        if screen_has(pb, "Choose a BOX") and not screen_has(pb, "SWITCH"):
            break
        rows = screen_text(pb)
        if any(">YES" in r for r in rows):
            tap(pb, "a", 4, 40)
        else:
            tap(pb, "a", 4, 30)
    assert wait_text(pb, "WITHDRAW", 20, "b", 30)
