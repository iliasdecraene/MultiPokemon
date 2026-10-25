"""Verify the Crystal memory map + crystal.py adapter against the real ROM (no server needed).

Needs dev/states/crystal.gbc + the fixtures from make_states.py. Prints one line per check and exits
non-zero on the first failure.  Usage: python dev/crystal/verify.py
"""
import io
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from common import ROM, STATES, new_pyboy, screen_text, sram_dump, tap, ticks  # noqa: E402
from play import choose, idle, pc_change_box, pc_close, pc_open, pc_withdraw, step  # noqa: E402

from yellowlink import crystal, game, text  # noqa: E402
from yellowlink.crystal import CrystalMemory, Gen2PC  # noqa: E402

RESULTS = []


def ok(cond, what):
    RESULTS.append((bool(cond), what))
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        raise SystemExit(1)


def load(name):
    return new_pyboy(state=os.path.join(STATES, name))


def rom_data():
    return crystal.RomData.from_file(ROM)


# ---------------------------------------------------------------------------------------------------------
def check_rom():
    ok(game.sha1_of(ROM) == crystal.CRYSTAL_SHA1, "ROM is Crystal v1.0 USA (SHA1)")
    rom = rom_data()
    ok(rom.species_name(155) == "CYNDAQUIL" and rom.species_name(251) == "CELEBI", "ROM PokemonNames 14:7384")
    ok(rom.base_stats(155)[:6] == (39, 52, 43, 65, 60, 50), "ROM BaseData 14:5424 (Cyndaquil 39/52/43/65/60/50)")
    ok(rom.move_pp(33) == 35 and rom.move_name(33) == "TACKLE", "ROM Moves 10:5AFB pp / MoveNames 72:5F29")
    for bank, addr, want in ((3, 0x641C, "afe0d4"), (3, 0x643F, "cd3c2b")):
        o = bank * 0x4000 + addr - 0x4000
        ok(rom.rom[o:o + 3].hex() == want, "hook %02X:%04X opcode bytes as in pokecrystal.sym" % (bank, addr))


def check_room():
    pb = load("crystal_room.state")
    cm = CrystalMemory(pb.memory)
    idle(pb)
    p = cm.position()
    ok(cm.in_game() and cm.is_safe() and not cm.in_battle() and not cm.font_loaded(), "room: in game, safe")
    ok(p == {"map": 24 << 8 | 7, "x": 3, "y": 3, "px": 48, "py": 48, "facing": 0, "img": 0},
       "room: position %s (PLAYERS_HOUSE_2F = group 24 / map 7)" % p)
    ok(cm.player_name() == "CHRIS" and cm.badges() == 0 and cm.r(crystal.PARTY_COUNT) == 0, "room: name/badges/party")
    ok(cm.party_checked() == [], "room: party_checked empty")
    ok(not Gen2PC(pb.memory).initialised(), "room: PC boxes not initialised before the first save")
    try:
        Gen2PC(pb.memory).write_boxes([[]] * 14)
        ok(False, "room: write_boxes must refuse before the first save")
    except RuntimeError:
        ok(True, "room: write_boxes refuses before the first save")
    pb.stop(False)


def player_oam(m):
    ent = [tuple(m[crystal.SHADOW_OAM + i * 4 + k] for k in range(4)) for i in range(40)]
    return [e for e in ent if e[0] in (76, 84) and e[1] in (72, 80)]


def check_walk_and_sprite():
    pb = load("crystal_party.state")
    cm = CrystalMemory(pb.memory)
    m = pb.memory
    idle(pb)
    hist = []
    offsets = set()
    covered = Counter()
    bad_pixels = 0
    rest_ok = True
    safe_while_walking = 0
    oam_ok = True
    plan = [("down", 40), ("left", 70), ("up", 70), ("right", 70), ("down", 3), ("left", 3), ("up", 3), ("right", 3),
            ("down", 36)]
    start_map = cm.map_id()
    for d, frames in plan:
        pb.button_press(d)
        for f in range(frames + 30):
            if f == frames:
                pb.button_release(d)
            pb.tick(1, True)
            p = cm.position()
            if p["map"] != start_map:
                continue
            offsets.add(((p["px"] - m[crystal.SCX]) % 256, (p["py"] - m[crystal.SCY]) % 256))
            if not cm.walking() and cm.r(crystal.PLAYER_WALKING) == 0xFF and (p["px"] != p["x"] * 16 or p["py"] != p["y"] * 16):
                rest_ok = False
            if cm.walking() and cm.is_safe():
                safe_while_walking += 1
            hist.append(p["img"])
            blob = cm.sprite_tiles()
            if blob is None or len(hist) < 4:
                continue
            shown = hist[-4]                         # OAM shows the facing from 3 frames earlier
            g = crystal.compose(blob, shown)
            pal = cm.sprite_palette()
            scr = pb.screen.ndarray
            bad = sum(1 for r in range(16) for c in range(16)
                      if g[r][c] and tuple(int(v) for v in scr[60 + r][64 + c][:3]) != pal[g[r][c]][:3])
            bad_pixels += bad
            covered[shown] += 1
            oam = player_oam(m)
            if len(oam) != 4 or any(not (e[3] & 0x08) or (e[3] & 7) != cm.sprite_palette_index() for e in oam):
                oam_ok = False
        pb.button_release(d)
    ok(len(offsets) == 1, "walk: px-SCX / py-SCY constant on every frame (camera-exact px/py) %s" % offsets)
    ok(rest_ok, "walk: px == x*16, py == y*16 whenever standing")
    ok(safe_while_walking == 0, "walk: is_safe() false mid-step")
    ok(set(covered) == set(range(16)), "sprite: all 16 facing/frame images seen (%d frames)" % sum(covered.values()))
    ok(bad_pixels == 0, "sprite: compose(VRAM bank 1 tiles, img) matches the screen pixel-exactly")
    ok(oam_ok, "sprite: OAM = 4 entries at (72|80, 76|84), VRAM bank 1 bit, OBJ palette = wPlayerPalette")
    # menus/text: font_loaded + tiles unchanged
    idle(pb)
    blob = cm.sprite_tiles()
    tap(pb, "start", 4, 40)
    ok(cm.font_loaded() and not cm.is_safe() and cm.sprite_tiles() is None, "start menu: font_loaded, not safe")
    tap(pb, "b", 4, 40)
    idle(pb)
    ok(cm.is_safe() and cm.sprite_tiles() == blob, "start menu closed: safe again, sprite tiles unchanged")
    pb.stop(False)


def check_battle():
    pb = load("crystal_party.state")
    cm = CrystalMemory(pb.memory)
    m = pb.memory
    step(pb, "down", 4)
    step(pb, "left", 8)
    random.seed(1)
    for _ in range(300):
        step(pb, random.choice(["left", "left", "up", "down"]), 1)
        if cm.in_battle():
            break
    ok(cm.in_battle() and cm.r(crystal.BATTLE_MODE) == 1, "battle: wild battle on Route 29 -> wBattleMode 1")
    banks = Counter()
    wrong = 0
    for _ in range(300):
        pb.tick(1, False)
        banks[m[0xFF70] & 7] += 1
        wrong += m[crystal.BATTLE_MODE] != m[1, crystal.BATTLE_MODE]
    ok(not cm.is_safe() and not cm.overworld_visible(), "battle: not safe, overworld hidden")
    ok(wrong > 0, "WRAM banking: SVBK seen %s at frame ends; unbanked reads of D22D wrong %d/300 frames"
       % (dict(banks), wrong))
    pb.stop(False)


def check_party_and_save():
    rom = rom_data()
    pb = load("crystal_party.state")
    cm = CrystalMemory(pb.memory, rom)
    party = cm.party_checked()
    ok(party is not None and [m.species for m in party] == [155, 16, 161], "party: CYNDAQUIL, PIDGEY, SENTRET")
    cyn = party[0]
    stats = crystal.calc_stats(cyn.data, rom.base_stats(155))
    have = [(cyn.data[36 + 2 * i] << 8) | cyn.data[37 + 2 * i] for i in range(6)]
    ok(stats == have, "party: Gen-2 stat formula reproduces the game's own Cyndaquil %s" % have)
    ok(int.from_bytes(cyn.data[8:11], "big") == crystal.exp_for_level(rom.growth_rate(155), 5), "party: exp table")
    ok(cyn.ot_id == cm.player_id() and cyn.ot_str() == "CHRIS", "party: OT name/ID")
    hoot = crystal.make_mon(rom, 163, 6, [33, 45], 12345, "FRIEND", dvs=0xBEEF, nick="OWLY")
    cm.write_party_slot(2, hoot)
    cm.set_dex(163)
    ok(cm.party_checked()[2] == hoot, "party: write_party_slot read-back")
    # the game itself shows it, then SAVE through the menu and CONTINUE from the cartridge RAM
    tap(pb, "start", 4, 40)
    choose(pb, "POK")
    ticks(pb, 60)
    ok(any("OWLY" in r for r in screen_text(pb)), "party: game's party menu lists the written mon")
    tap(pb, "b", 4, 40)
    tap(pb, "b", 4, 40)
    idle(pb)
    sav = save_via_menu(pb)
    pb.stop(False)
    pb2 = new_pyboy(sav=sav)
    ok(crystal.fast_continue(pb2) == "loaded", "fast_continue: loads the in-game save")
    cm2 = CrystalMemory(pb2.memory, rom)
    ok(cm2.party_checked() == cm.party_checked() and cm2.is_safe(), "party: survives the game's SAVE + CONTINUE")
    pb2.stop(False)
    pb3 = new_pyboy()
    ok(crystal.fast_continue(pb3) == "no-save", "fast_continue: blank cartridge -> 'no-save' at the menu")
    pb3.stop(False)


def save_via_menu(pb):
    from make_states import save_game
    save_game(pb)
    return sram_dump(pb)


def gen1_pikachu():
    """A Yellow-style Gen-1 box record (internal species 0x54 = Pikachu, catch rate 0xA3)."""
    d = bytearray(33)
    d[0], d[1], d[2], d[3] = 0x54, 0, 30, 12
    d[5] = d[6] = 0x17
    d[7] = 0xA3
    d[8], d[9] = 84, 45          # ThunderShock, Growl
    d[12], d[13] = 0x30, 0x39
    d[14:17] = (1728).to_bytes(3, "big")
    d[27], d[28] = 0xAB, 0xCD
    d[29], d[30] = 30, 40
    return bytes(d) + text.encode("ASH") + text.encode("SPARKY")


def check_boxes():
    rom = rom_data()
    pb = load("crystal_party.state")
    cm = CrystalMemory(pb.memory, rom)
    pc = Gen2PC(pb.memory, rom)
    ok(pc.initialised() and pc.current_box() == 0 and pc.read_boxes() == [[] for _ in range(14)],
       "boxes: initialised, all 14 empty, current = BOX1")
    party = cm.party()
    pidgey = party[1].box_record()
    pika = pc.record_for({"dex": 25, "level": 12, "gen1": crystal.b64(gen1_pikachu()), "gen2": None})
    ok(pika[0] == 25 and pika[1] == 0xA3 and pika[31] == 12 and pika[27] == 70, "boxes: Gen-1 -> Gen-2 conversion")
    stand = pc.standin({"nick": "TOGEPI2", "level": 9})
    ok(pc.is_standin(stand) and not pc.is_standin(pidgey), "boxes: stand-in detection")
    hoot = crystal.make_mon(rom, 163, 6, [33, 45], 777, "FRIEND", nick="OWLY").box_record()
    boxes = [[] for _ in range(14)]
    boxes[0] = [pidgey, pika]
    boxes[1] = [stand, hoot]
    boxes[13] = [hoot]
    pc.write_boxes(boxes)
    ok(pc.read_boxes() == boxes, "boxes: write_boxes / read_boxes round trip")
    ok(pc.save_checksum_ok(), "boxes: main save checksum untouched (boxes are not checksummed in Gen 2)")
    # Use the boxes in the game: hooks, withdraw from the current box, change box (the game saves), withdraw
    fired = []
    pb.hook_register(*crystal.HOOK_PC_OPEN, lambda _: fired.append("open"), None)
    pb.hook_register(*crystal.HOOK_PC_CLOSE, lambda _: fired.append("close"), None)
    pc_open(pb)
    ok(fired == ["open"], "hooks: _BillsPC.LogIn 03:641C fires when Bill's PC opens")
    pc_withdraw(pb, "PIKACHU")
    p = cm.party_checked()
    ok(p is not None and p[-1].species == 25 and p[-1].item == 0xA3 and p[-1].nick_str() == "SPARKY",
       "boxes: game withdrew the converted Pikachu from BOX1 (sBox)")
    ok(p[-1].max_hp == crystal.calc_stats(p[-1].data, rom.base_stats(25))[0], "boxes: game recomputed its stats")
    pc_change_box(pb, 2)
    expect = [list(b) for b in boxes]
    expect[0] = [pidgey]
    got = pc.read_boxes()
    ok(pc.current_box() == 1 and got == expect, "boxes: CHANGE BOX (game save + LoadBox) keeps every box")
    ok(pc.save_checksum_ok(), "boxes: game's own save after CHANGE BOX has a valid checksum")
    pc_withdraw(pb, "HOOTHOOT")
    ok(cm.party_checked()[-1].nick_str() == "OWLY", "boxes: withdrew OWLY from BOX2")
    expect[1] = [stand]
    pc_close(pb)
    ok(fired == ["open", "close"], "hooks: _BillsPC.LogOut 03:643F fires when Bill's PC closes")
    ok(pc.read_boxes() == expect, "boxes: read_boxes matches the game after the session")
    party_now = cm.party_checked()
    sav = save_via_menu(pb)
    pb.stop(False)
    pb2 = new_pyboy(sav=sav)
    ok(crystal.fast_continue(pb2) == "loaded", "boxes: reloaded save CONTINUEs (no 'save file is corrupted')")
    pc2 = Gen2PC(pb2.memory, rom)
    ok(pc2.read_boxes() == expect and pc2.current_box() == 1, "boxes: identical after save + reload")
    ok(CrystalMemory(pb2.memory).party_checked() == party_now, "boxes: party identical after reload")
    pb2.stop(False)


def check_rtc():
    sav = open(os.path.join(STATES, "crystal.sav"), "rb").read()
    pb = new_pyboy(sav=sav)
    crystal.fast_continue(pb)
    m = pb.memory
    a = (m[0xFF94], m[0xFF96])
    for _ in range(3600):
        pb.tick(1, False)
    b = (m[0xFF94], m[0xFF96])
    ok((b[0] * 60 + b[1]) - (a[0] * 60 + a[1]) in (1, 2), "RTC: in-game clock follows emulated frames (3600 = 1 min)")
    rtc = io.BytesIO()
    pb.stop(save=True, ram_file=io.BytesIO(), rtc_file=rtc)
    ok(len(rtc.getvalue()) > 0, "RTC: PyBoy writes an .rtc stream on stop() (%d bytes)" % len(rtc.getvalue()))


def main():
    check_rom()
    check_room()
    check_walk_and_sprite()
    check_battle()
    check_party_and_save()
    check_boxes()
    check_rtc()
    print("\nALL %d CHECKS PASSED" % len(RESULTS))


if __name__ == "__main__":
    main()
