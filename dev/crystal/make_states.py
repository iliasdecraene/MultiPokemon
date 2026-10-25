"""Build the Crystal test fixtures by playing the real game (dev/states/, see dev/README.md).

  crystal_room.state   new game (CHRIS, clock 10:00), standing in the bedroom (New Bark 24:7), can walk
  crystal_party.state  New Bark Town outside Elm's lab, party CYNDAQUIL (from Elm) + PIDGEY + SENTRET,
                       saved once in-game (so all 14 PC boxes are initialised)
  crystal.sav          the cartridge RAM right after that in-game SAVE (32 KB)
  crystal.rtc          PyBoy RTC file matching the save (optional for tests; see SPEC: Crystal RTC)

Usage: python dev/crystal/make_states.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import STATES, new_pyboy, screen_text, sram_dump, tap, ticks, wram  # noqa: E402
from play import busy, idle, pos, step  # noqa: E402

from yellowlink import crystal  # noqa: E402


def a_until(pb, cond, limit=300, wait=40, button="a"):
    for _ in range(limit):
        if cond(pb):
            return True
        tap(pb, button, 4, wait)
    return cond(pb)


def talk(pb, limit=400, b_on=()):
    """Advance a scripted scene with A until the overworld is idle again (B on prompts in b_on)."""
    for _ in range(limit):
        joined = " ".join(screen_text(pb))
        tap(pb, "b" if any(s in joined for s in b_on) else "a", 4, 40)
        if not busy(pb) and idle(pb, 30, 60):
            return True
    raise RuntimeError("scene did not end")


def intro_to_bedroom(pb):
    # title -> main menu
    a_until(pb, lambda p: any("NEW GAME" in r for r in screen_text(p)), 400, 30, "start")
    ticks(pb, 20)
    tap(pb, "a", 4, 40)                                    # NEW GAME (only option without a save)
    # gender (Boy default), clock (10:00 default), Oak's speech until the name menu
    a_until(pb, lambda p: any("CHRIS" in r for r in screen_text(p)), 300, 40)
    ticks(pb, 60)
    tap(pb, "down", 4, 20)                                 # NEW NAME -> CHRIS
    tap(pb, "a", 4, 40)
    a_until(pb, lambda p: wram(p, crystal.MAP_GROUP) == 24, 100, 40)
    if not idle(pb, 60, 3000):
        raise RuntimeError("bedroom not reached")
    assert pos(pb) == (24, 7, 3, 3), pos(pb)


def bedroom_to_starter(pb):
    step(pb, "right", 4)
    step(pb, "up", 3)                                      # stairs -> 1F
    idle(pb)
    step(pb, "down", 3)                                    # Mom's scene (Pokégear, day, DST)
    talk(pb)
    step(pb, "down", 3)
    step(pb, "left", 2)
    step(pb, "down", 1)                                    # out of the door
    idle(pb)
    step(pb, "down", 1)
    step(pb, "left", 7)
    step(pb, "up", 3)
    step(pb, "up", 1)                                      # into Elm's lab: Elm's speech
    talk(pb)
    step(pb, "right", 2)
    tap(pb, "up", 4, 20)                                   # face Cyndaquil's ball
    talk(pb, b_on=("received?",))                          # take it, no nickname
    for _ in range(12):                                    # walk out; Elm's aide gives a Potion
        step(pb, "down", 1)
        if busy(pb):
            talk(pb)
        if pos(pb)[1] == 4:
            break
    idle(pb)
    assert pos(pb)[:2] == (24, 4), pos(pb)
    assert wram(pb, crystal.PARTY_COUNT) == 1


def add_party(pb):
    rom = crystal.RomData.from_file(os.environ.get("YL_CRYSTAL_ROM", os.path.join(STATES, "crystal.gbc")))
    cm = crystal.CrystalMemory(pb.memory, rom)
    assert cm.is_safe()
    me = cm.party()[0]
    otid, ot = cm.player_id(), cm.player_name()
    pidgey = crystal.make_mon(rom, 16, 4, [33], otid, ot, dvs=0xA7C5)          # Tackle
    sentret = crystal.make_mon(rom, 161, 3, [33], otid, ot, dvs=0x6B3E)        # Tackle
    cm.set_party([me, pidgey, sentret])
    for s in (16, 161):
        cm.set_dex(s)
    return cm


def menu_select(pb, label, limit=10):
    """Move the start-menu cursor (drawn in column 11) onto `label`."""
    for _ in range(limit):
        if any(r[12:].startswith(label) and r[11] != " " for r in screen_text(pb)):
            return True
        tap(pb, "down", 4, 12)
    return False


def save_game(pb):
    """START -> SAVE -> YES, through the game's own menu."""
    for _ in range(10):
        if idle(pb, 30, 60):
            break
        tap(pb, "b", 4, 30)
    tap(pb, "start", 4, 40)
    assert menu_select(pb, "SAVE")
    tap(pb, "a", 4, 60)                                    # "Would you like to save the game?"
    a_until(pb, lambda p: any("YES" in r for r in screen_text(p)), 20, 30, button="b")
    tap(pb, "a", 4, 60)                                    # YES
    for _ in range(60):                                    # (YES again to "overwrite the save file?")
        rows = screen_text(pb)
        if any(" saved" in r for r in rows):
            break
        tap(pb, "a" if any(">YES" in r for r in rows) else "b", 4, 30)
    assert any(" saved" in r for r in screen_text(pb)), "save failed"
    ticks(pb, 60)
    tap(pb, "a", 4, 30)
    idle(pb, 60, 1000)


def main():
    pb = new_pyboy()
    intro_to_bedroom(pb)
    with open(os.path.join(STATES, "crystal_room.state"), "wb") as f:
        pb.save_state(f)
    print("crystal_room.state at", pos(pb))
    bedroom_to_starter(pb)
    cm = add_party(pb)
    ticks(pb, 30)
    save_game(pb)
    print("party:", [(m.nick_str(), m.level, m.hp, m.max_hp) for m in cm.party()],
          "checked:", cm.party_checked() is not None, "saved once:", cm.r(crystal.SAVED_AT_LEAST_ONCE))
    assert cm.is_safe() and cm.r(crystal.SAVED_AT_LEAST_ONCE) == 1
    with open(os.path.join(STATES, "crystal_party.state"), "wb") as f:
        pb.save_state(f)
    with open(os.path.join(STATES, "crystal.sav"), "wb") as f:
        f.write(sram_dump(pb))
    rtc = io.BytesIO()
    pb.stop(save=True, ram_file=io.BytesIO(), rtc_file=rtc)
    with open(os.path.join(STATES, "crystal.rtc"), "wb") as f:
        f.write(rtc.getvalue())
    print("crystal_party.state, crystal.sav, crystal.rtc written; map", crystal.CrystalMemory(pb.memory).map_id())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
