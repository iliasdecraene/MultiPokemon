"""Scripted Bill's PC actions for Pokémon Yellow (PyBoy 2.x, headless).

Start from dev/states/pokecenter_pc.state (Viridian Pokémon Center, player at
(13,4) facing the PC at (13,3), party of 4). Every function drives the real
menus with button presses and waits on what the screen shows (wTileMap at
C3A0), so it does not depend on text speed or lag frames.

    open_pc(pb)            overworld -> Bill's PC main menu (WITHDRAW/DEPOSIT/...)
    deposit(pb, party_slot)
    withdraw(pb, box_slot)
    release(pb, box_slot)
    change_box(pb, box_index)   answers YES to "data will be saved" (saves the game)
    close_pc(pb, how="b")  Bill's PC -> PC main menu -> LOG OFF -> overworld
                           how: "b" (B button) or "seeya" (choose SEE YA!)

Slots and box indexes are 0-based. deposit/withdraw/release return True if the
party/box counts changed as expected (False if the game refused, e.g. the last
party mon, the following Pikachu, a full box or an empty box).

Run as a script for a demo:  python dev/e2e/pc_actions.py
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
ROM = os.environ.get("YL_ROM", os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
STATE = os.path.join(STATES, "pokecenter_pc.state")

# --- RAM ---------------------------------------------------------------
TILEMAP = 0xC3A0            # wTileMap, 20x18
CURRENT_MENU_ITEM = 0xCC26  # wCurrentMenuItem
LIST_SCROLL = 0xCC36        # wListScrollOffset
FONT_LOADED = 0xCFC3        # wFontLoaded (bit 0 = text box/menu active)
IS_IN_BATTLE = 0xD056
PARTY_COUNT = 0xD162
PARTY_SPECIES = 0xD163
PARTY_NICKS = 0xD2B4
CUR_MAP = 0xD35D
Y_COORD, X_COORD = 0xD360, 0xD361
CUR_BOX = 0xD59F            # wCurrentBoxNum (bit 7 = has changed boxes once)
BOX_COUNT = 0xDA7F
BOX_SPECIES = 0xDA80
BOX_NICKS = 0xDE05

# Bill's PC main menu items
WITHDRAW, DEPOSIT, RELEASE, CHANGE_BOX, PRINT_BOX, SEE_YA = range(6)

# --- screen ------------------------------------------------------------
_CH = {0x7F: " ", 0x9A: "(", 0x9B: ")", 0x9C: ":", 0xE0: "'", 0xE3: "-", 0xE6: "?",
       0xE7: "!", 0xE8: ".", 0xF3: "/", 0xF4: ",", 0xEF: "M", 0xF5: "F", 0xBA: "e",
       0xBD: "'s", 0xBE: "'t", 0xE1: "PK", 0xE2: "MN", 0x4A: "PKMN", 0x54: "POKe",
       0xED: ">", 0xEC: ">", 0xEE: "v", 0x79: "+", 0x7B: "+", 0x7D: "+", 0x7E: "+",
       0x7A: "-", 0x7C: "|"}
for _i in range(26):
    _CH[0x80 + _i] = chr(65 + _i)
    _CH[0xA0 + _i] = chr(97 + _i)
for _i in range(10):
    _CH[0xF6 + _i] = str(_i)


def _tiles(pb):
    return bytes(pb.memory[TILEMAP:TILEMAP + 360])


def screen_lines(pb):
    t = _tiles(pb)
    return ["".join(_CH.get(v, ".") for v in t[r * 20:r * 20 + 20]) for r in range(18)]


def screen_text(pb):
    return "\n".join(screen_lines(pb))


def _has(pb, *needles):
    s = screen_text(pb)
    return all(n in s for n in needles)


def _prompt_arrow(pb):
    # the blinking "v" at the bottom-right of the text box (tile 0xEE at 18,16)
    return pb.memory[TILEMAP + 16 * 20 + 18] == 0xEE


# --- input primitives ----------------------------------------------------
def _tick(pb, n=1):
    for _ in range(n):
        pb.tick()


def _press(pb, button, hold=3, gap=4):
    pb.button_press(button)
    _tick(pb, hold)
    pb.button_release(button)
    _tick(pb, gap)


def _settle(pb, frames=8, maxf=600):
    """Wait until the tilemap stops changing for `frames` frames."""
    last, same = None, 0
    for _ in range(maxf):
        t = _tiles(pb)
        same = same + 1 if t == last else 0
        last = t
        if same >= frames:
            return
        pb.tick()


def _advance_until(pb, pred, maxf=3000, what="?"):
    """Tick, pressing A on each text prompt arrow, until pred(pb) holds on a settled screen."""
    f = 0
    while f < maxf:
        if pred(pb):
            _settle(pb)
            if pred(pb):
                return
        if _prompt_arrow(pb):
            _press(pb, "a")
            f += 7
        else:
            pb.tick()
            f += 1
    raise TimeoutError(f"waiting for {what}\n{screen_text(pb)}")


def _move_cursor(pb, index, scrolled=False, maxpress=40):
    """Move the menu cursor to absolute item `index` (list menus add wListScrollOffset)."""
    for _ in range(maxpress):
        cur = pb.memory[CURRENT_MENU_ITEM] + (pb.memory[LIST_SCROLL] if scrolled else 0)
        if cur == index:
            return
        _press(pb, "down" if index > cur else "up")
        _settle(pb, 3)
    raise TimeoutError(f"cursor stuck moving to {index}")


def _in_bills_menu(pb):
    # main menu drawn and no message box on top of the "BOX No." box
    return _has(pb, "SEE YA!", "BOX No.") and not _prompt_arrow(pb)


def _in_mon_list(pb):
    # DisplayListMenuID box: top-left corner at (4,2), top-right corner at (19,2)
    t = _tiles(pb)
    return t[2 * 20 + 4] == 0x79 and t[2 * 20 + 19] == 0x7B and not _prompt_arrow(pb)


def _in_pc_main_menu(pb):
    return _has(pb, "LOG OFF") and "PC" in screen_lines(pb)[2]


def _overworld_free(pb):
    return (pb.memory[FONT_LOADED] & 1) == 0 and pb.memory[IS_IN_BATTLE] == 0


def _select(pb, index):
    _move_cursor(pb, index)
    _press(pb, "a")


# --- state readers -------------------------------------------------------
def _name(pb, base, i):
    out = []
    for k in range(11):
        v = pb.memory[base + i * 11 + k]
        if v == 0x50:
            break
        out.append(_CH.get(v, "?"))
    return "".join(out)


def party(pb):
    n = pb.memory[PARTY_COUNT]
    return [(pb.memory[PARTY_SPECIES + i], _name(pb, PARTY_NICKS, i)) for i in range(min(n, 6))]


def box(pb):
    n = pb.memory[BOX_COUNT]
    return [(pb.memory[BOX_SPECIES + i], _name(pb, BOX_NICKS, i)) for i in range(min(n, 20))]


def current_box(pb):
    return pb.memory[CUR_BOX] & 0x7F


# --- actions -------------------------------------------------------------
def open_pc(pb):
    """From the overworld, facing the PC: A -> SOMEONE's/BILL's PC -> Bill's PC menu."""
    if not _overworld_free(pb):
        raise RuntimeError("not in a free overworld state")
    # The overworld does not always take a short A tap (it only polls between
    # movement checks), so hold A until the "turned on the PC" text box opens.
    for _ in range(10):
        _press(pb, "a", hold=6, gap=14)
        if pb.memory[FONT_LOADED] & 1:
            break
    else:
        raise TimeoutError("A on the PC did nothing - is the player facing it?")
    _advance_until(pb, _in_pc_main_menu, what="PC main menu")
    _select(pb, 0)  # SOMEONE's PC (BILL's PC after meeting Bill) is always item 0
    _advance_until(pb, _in_bills_menu, what="Bill's PC menu")


def _pick_from_list(pb, menu_item, index, count):
    """Choose WITHDRAW/DEPOSIT/RELEASE, then entry `index` in the mon list. Returns False if refused."""
    _select(pb, menu_item)
    # either the mon list (has CANCEL) or a refusal message that returns to the menu
    _advance_until(pb, lambda p: _in_mon_list(p) or _in_bills_menu(p), what="mon list")
    if not _in_mon_list(pb):
        return False
    if not 0 <= index < count:
        _press(pb, "b")
        _advance_until(pb, _in_bills_menu, what="Bill's PC menu")
        raise IndexError(f"slot {index} out of range (count {count})")
    _move_cursor(pb, index, scrolled=True)
    _press(pb, "a")
    return True


def deposit(pb, party_slot):
    n_party, n_box = pb.memory[PARTY_COUNT], pb.memory[BOX_COUNT]
    if _pick_from_list(pb, DEPOSIT, party_slot, n_party):
        # DEPOSIT / STATS / CANCEL submenu (or "sleeping Pikachu" refusal)
        _advance_until(pb, lambda p: _has(p, "DEPOSIT", "STATS") or _in_bills_menu(p), what="deposit submenu")
        if _has(pb, "STATS"):
            _select(pb, 0)
        _advance_until(pb, _in_bills_menu, what="Bill's PC menu")
    return pb.memory[PARTY_COUNT] == n_party - 1 and pb.memory[BOX_COUNT] == n_box + 1


def withdraw(pb, box_slot):
    n_party, n_box = pb.memory[PARTY_COUNT], pb.memory[BOX_COUNT]
    if _pick_from_list(pb, WITHDRAW, box_slot, n_box):
        _advance_until(pb, lambda p: _has(p, "WITHDRAW", "STATS"), what="withdraw submenu")
        _select(pb, 0)
        _advance_until(pb, _in_bills_menu, what="Bill's PC menu")
    return pb.memory[PARTY_COUNT] == n_party + 1 and pb.memory[BOX_COUNT] == n_box - 1


def release(pb, box_slot):
    n_box = pb.memory[BOX_COUNT]
    if _pick_from_list(pb, RELEASE, box_slot, n_box):
        # "Once released, X is gone forever. OK?" YES/NO (or Pikachu refusal)
        _advance_until(pb, lambda p: _has(p, "YES", "NO") or _in_bills_menu(p), what="release YES/NO")
        if _has(pb, "YES", "NO"):
            _select(pb, 0)  # YES
        _advance_until(pb, _in_bills_menu, what="Bill's PC menu")
    return pb.memory[BOX_COUNT] == n_box - 1


def change_box(pb, box_index):
    """CHANGE BOX -> YES (saves the game) -> BOX `box_index` (0-based, 0..11)."""
    if not 0 <= box_index < 12:
        raise IndexError(box_index)
    _select(pb, CHANGE_BOX)
    _advance_until(pb, lambda p: _has(p, "YES", "NO"), what="change box YES/NO")
    _select(pb, 0)  # YES
    _advance_until(pb, lambda p: _has(p, "BOX12") or _has(p, "BOX 12"), what="box list")
    _select(pb, box_index)
    _advance_until(pb, _in_bills_menu, what="Bill's PC menu")
    return current_box(pb) == box_index


def close_pc(pb, how="b"):
    """Leave Bill's PC (B or SEE YA!), then LOG OFF the PC, back to the overworld."""
    if how == "b":
        _press(pb, "b")
    else:
        _select(pb, SEE_YA)
    _advance_until(pb, _in_pc_main_menu, what="PC main menu")
    _press(pb, "b")  # B on the PC main menu == LOG OFF
    for _ in range(600):
        if _overworld_free(pb):
            break
        pb.tick()
    _tick(pb, 10)
    if not _overworld_free(pb):
        raise TimeoutError("PC did not close\n" + screen_text(pb))


# --- demo ----------------------------------------------------------------
def new_emulator(state=STATE):
    from pyboy import PyBoy
    pb = PyBoy(ROM, window="null", sound_emulated=False)
    pb.set_emulation_speed(0)
    with open(state, "rb") as f:
        pb.load_state(f)
    return pb


def _show(pb, label):
    print(f"[{label}] box {current_box(pb) + 1}: {box(pb)}")
    print(f"[{label}] party: {party(pb)}")


if __name__ == "__main__":
    pb = new_emulator()
    _show(pb, "start")
    open_pc(pb)
    _show(pb, "open_pc")
    print("deposit(1) ->", deposit(pb, 1)); _show(pb, "deposit")
    print("deposit(1) ->", deposit(pb, 1)); _show(pb, "deposit")
    print("withdraw(0) ->", withdraw(pb, 0)); _show(pb, "withdraw")
    print("release(0) ->", release(pb, 0)); _show(pb, "release")
    print("change_box(1) ->", change_box(pb, 1)); _show(pb, "change_box")
    print("change_box(0) ->", change_box(pb, 0)); _show(pb, "change_box")
    close_pc(pb)
    _show(pb, "close_pc")
    print("overworld:", pb.memory[CUR_MAP], pb.memory[X_COORD], pb.memory[Y_COORD],
          "font", pb.memory[FONT_LOADED], "battle", pb.memory[IS_IN_BATTLE])
    pb.stop(save=False)
