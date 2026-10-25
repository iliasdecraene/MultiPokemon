"""Unit tests for the Crystal adapter (no ROM needed): synthetic memory with real WRAM/VRAM/SRAM banking."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import crystal, pcdb, sprites, text  # noqa: E402
from yellowlink.crystal import CrystalMemory, Gen2PC, Mon  # noqa: E402


class FakeMem:
    """Unbanked C000-CFFF/FFxx, plus banked WRAMX (D000-DFFF), VRAM (8000-9FFF) and SRAM (A000-BFFF).

    A plain mem[addr] in D000-DFFF raises: Crystal switches SVBK, so the adapter must always say which bank.
    """

    def __init__(self):
        self.flat = bytearray(0x10000)
        self.wram = {b: bytearray(0x1000) for b in range(8)}
        self.vram = {b: bytearray(0x2000) for b in range(2)}
        self.sram = {b: bytearray(0x2000) for b in range(4)}

    def _where(self, k):
        b, a = k
        if 0xD000 <= a < 0xE000:
            return self.wram[b], a - 0xD000
        if 0x8000 <= a < 0xA000:
            return self.vram[b], a - 0x8000
        if 0xA000 <= a < 0xC000:
            return self.sram[b], a - 0xA000
        raise AssertionError("banked access outside D000-DFFF/8000-9FFF/A000-BFFF: %r" % (k,))

    def __getitem__(self, k):
        if isinstance(k, tuple):
            buf, i = self._where(k)
            return buf[i]
        assert not 0xD000 <= k < 0xE000, "unbanked WRAMX read at %04X" % k
        return self.flat[k]

    def __setitem__(self, k, v):
        if isinstance(k, tuple):
            buf, i = self._where(k)
            buf[i] = v
            return
        assert not 0xD000 <= k < 0xE000, "unbanked WRAMX write at %04X" % k
        self.flat[k] = v


class FakeRom:
    """Enough of RomData for the tests (Cyndaquil/Pidgey/Pikachu/Hoothoot/Ditto base stats + PP)."""
    BASE = {155: (39, 52, 43, 65, 60, 50, 20, 20), 16: (40, 45, 40, 56, 35, 35, 0, 2),
            25: (35, 55, 30, 90, 50, 40, 23, 23), 163: (60, 30, 30, 50, 36, 56, 0, 2), 132: (48,) * 6 + (0, 0)}
    GROWTH = {155: 3, 16: 3, 25: 0, 163: 0, 132: 0}
    PP = {33: 35, 43: 30, 45: 40, 84: 30, 144: 10}

    def base_stats(self, s):
        return self.BASE.get(s)

    def growth_rate(self, s):
        return self.GROWTH.get(s, 0)

    def move_pp(self, m):
        return self.PP.get(m, 0)

    def species_name_raw(self, s):
        return text.encode({155: "CYNDAQUIL", 16: "PIDGEY", 163: "HOOTHOOT"}.get(s, "MON"))

    def dex_no(self, s):
        return s


def in_game_mem():
    mem = FakeMem()
    cm = CrystalMemory(mem, FakeRom())
    cm.w(crystal.GAME_TIMER, 1)
    cm.w(crystal.MAP_GROUP, 24)
    cm.w(crystal.MAP_NUMBER, 4)
    cm.w(crystal.PLAYER_SPRITE, 1)
    cm.write(crystal.PLAYER_NAME, text.encode("CHRIS"))
    cm.w(crystal.PLAYER_WALKING, 0xFF)
    cm.w(crystal.X_COORD, 6)
    cm.w(crystal.Y_COORD, 4)
    return mem, cm


# --- state + position ---------------------------------------------------------------------------------------
def test_in_game_and_safe():
    mem, cm = in_game_mem()
    assert cm.in_game() and cm.is_safe() and cm.overworld_visible()
    cm.w(crystal.GAME_TIMER, 0)                # title / CONTINUE card: clock not running
    assert not cm.in_game() and not cm.is_safe()


@pytest.mark.parametrize("addr,val", [
    (crystal.BATTLE_MODE, 1), (crystal.STATE_FLAGS, 0x40), (crystal.STATE_FLAGS, 0x80), (crystal.SCRIPT_MODE, 1),
    (crystal.SCRIPT_FLAGS, 0x04), (crystal.JOYPAD_DISABLE, 0x10), (crystal.WINDOW_STACK_SIZE, 1),
    (crystal.PLAYER_WALKING, 7), (crystal.PLAYER_STEP_VECTOR_X, 2), (crystal.PLAYER_FACING, 0xFF)])
def test_each_flag_makes_unsafe(addr, val):
    mem, cm = in_game_mem()
    cm.w(addr, val)
    assert not cm.is_safe()


def test_font_loaded():
    mem, cm = in_game_mem()
    assert not cm.font_loaded()
    cm.w(crystal.STATE_FLAGS, 0x41)
    assert cm.font_loaded() and not cm.overworld_visible()


def test_position_at_rest():
    mem, cm = in_game_mem()
    cm.w(crystal.PLAYER_DIRECTION, 12)
    cm.w(crystal.PLAYER_FACING, 12)
    assert cm.position() == {"map": 24 << 8 | 4, "x": 6, "y": 4, "px": 96, "py": 64, "facing": 12, "img": 12}


@pytest.mark.parametrize("walking,dx,dy", [(4, 0, 1), (5, 0, -1), (6, -1, 0), (7, 1, 0)])
def test_position_mid_step_matches_camera(walking, dx, dy):
    """Recorded from the ROM: the camera trails the step logic by the step vector (2 px at walking speed)."""
    mem, cm = in_game_mem()
    cm.w(crystal.PLAYER_WALKING, walking)
    cm.w(crystal.PLAYER_STEP_VECTOR_X, (2 * dx) & 0xFF)
    cm.w(crystal.PLAYER_STEP_VECTOR_Y, (2 * dy) & 0xFF)
    seen = []
    for left in range(7, 0, -1):                # duration 8 updates of 2 px
        cm.w(crystal.PLAYER_STEP_DURATION, left)
        p = cm.position()
        seen.append((p["px"] - 96) * dx + (p["py"] - 64) * dy)
    assert seen == [0, 2, 4, 6, 8, 10, 12]
    # arrival frame: coords already updated, camera 2 px short of the new tile
    cm.w(crystal.PLAYER_WALKING, 0xFF)
    cm.w(crystal.PLAYER_STEP_DURATION, 0)
    cm.w(crystal.X_COORD, 6 + dx)
    cm.w(crystal.Y_COORD, 4 + dy)
    p = cm.position()
    assert (p["px"], p["py"]) == ((6 + dx) * 16 - 2 * dx, (4 + dy) * 16 - 2 * dy)


def test_position_bike_speed():
    mem, cm = in_game_mem()
    cm.w(crystal.PLAYER_WALKING, 8 + 3)          # fast, right: 4 updates of 4 px
    cm.w(crystal.PLAYER_STEP_VECTOR_X, 4)
    cm.w(crystal.PLAYER_STEP_DURATION, 3)
    assert cm.position()["px"] == 96 + 4 - 4


def test_map_id_and_badges():
    mem, cm = in_game_mem()
    cm.w(crystal.JOHTO_BADGES, 0b101)
    cm.w(crystal.KANTO_BADGES, 0b1)
    assert cm.map_id() == 0x1804 and cm.badges() == 0x105


# --- sprite ---------------------------------------------------------------------------------------------------
def test_sprite_tiles_from_vram_bank1():
    mem, cm = in_game_mem()
    for i in range(192):
        mem[1, 0x8000 + i] = (i * 7 + 1) & 0xFF
        mem[1, 0x8800 + i] = (i * 3 + 2) & 0xFF
        mem.vram[0][i] = 0xEE                     # bank 0 must not be used
    blob = cm.sprite_tiles()
    assert len(blob) == crystal.SPRITE_BYTES and blob[0] == 1 and blob[192] == 2
    cm.w(crystal.STATE_FLAGS, 0x40)
    assert cm.sprite_tiles() is None
    cm.w(crystal.STATE_FLAGS, 0)
    cm.w(crystal.PLAYER_SPRITE_TILE, 0x80)       # not the normal player tile layout
    assert cm.sprite_tiles() is None


def test_frame_layout_matches_facings_table():
    # data/sprites/facings.asm: (first tile, mirrored) for FACING_STEP_DOWN_0 .. FACING_STEP_RIGHT_3
    want = [(0x00, False), (0x80, False), (0x00, False), (0x80, True),
            (0x04, False), (0x84, False), (0x04, False), (0x84, True),
            (0x08, False), (0x88, False), (0x08, False), (0x88, False),
            (0x08, True), (0x88, True), (0x08, True), (0x88, True)]
    assert [crystal.frame_layout(i) for i in range(16)] == want
    assert [crystal.frame_layout(i) for i in range(16)] == [sprites.frame_layout(i) for i in range(16)]


def test_compose_same_as_yellow():
    blob = bytes((i * 37) & 0xFF for i in range(384))
    for img in range(16):
        assert crystal.compose(blob, img) == sprites.compose(blob, img)


def test_sprite_palette():
    mem, cm = in_game_mem()
    cm.w(crystal.PLAYER_PALETTE, 0x02)
    base = crystal.OB_PALS + 16
    for i, c in enumerate((0x6FFB, 0x2A7F, 0x04FF, 0x0000)):
        mem[5, base + 2 * i], mem[5, base + 2 * i + 1] = c & 0xFF, c >> 8
    assert cm.sprite_palette() == [(0, 0, 0, 0), (248, 152, 80, 255), (248, 56, 8, 255), (0, 0, 0, 255)]


# --- party ------------------------------------------------------------------------------------------------------
def cyndaquil():
    """Byte-exact Cyndaquil from Elm (recorded from the game) except DVs 3FD7."""
    return Mon(bytes.fromhex("9bad212b0000a4f0000087000000000000000000003fd7231e00004600850105000000"
                             "140014000a000a000c000b000a"), text.encode("CHRIS"), text.encode("CYNDAQUIL"))


def test_stat_formula_matches_game():
    m = cyndaquil()
    assert crystal.calc_stats(m.data, FakeRom.BASE[155]) == [20, 10, 10, 12, 11, 10]
    assert crystal.exp_for_level(3, 5) == 135 and crystal.exp_for_level(3, 4) == 96
    assert crystal.exp_for_level(0, 10) == 1000 and crystal.exp_for_level(5, 10) == 1250


def test_make_mon_consistent():
    rom = FakeRom()
    m = crystal.make_mon(rom, 16, 4, [33], 0xA4F0, "CHRIS", dvs=0xA7C5)
    assert m.species == 16 and m.level == 4 and m.hp == m.max_hp and m.data[23] == 35
    assert int.from_bytes(m.data[8:11], "big") == 96
    assert crystal.calc_stats(m.data, rom.base_stats(16))[0] == m.max_hp
    assert m.nick_str() == "PIDGEY" and m.ot_id == 0xA4F0


def test_party_roundtrip_and_checked():
    mem, cm = in_game_mem()
    a = cyndaquil()
    b = crystal.make_mon(FakeRom(), 163, 6, [33, 45], 1, "FRIEND", nick="OWLY")
    cm.set_party([a, b])
    assert cm.r(crystal.PARTY_COUNT) == 2 and cm.r(crystal.PARTY_SPECIES + 2) == 0xFF
    assert cm.party_checked() == [a, b]
    cm.w(crystal.PARTY_SPECIES + 1, 0)           # half-written species list
    assert cm.party_checked() is None
    cm.write_party_slot(1, b)
    cm.w(crystal.PARTY_SPECIES + 2, 0x10)        # missing terminator
    assert cm.party_checked() is None


def test_party_egg():
    mem, cm = in_game_mem()
    egg = Mon(cyndaquil().data, text.encode("CHRIS"), text.encode("EGG"), egg=True)
    cm.set_party([cyndaquil(), egg])
    assert cm.r(crystal.PARTY_SPECIES + 1) == crystal.EGG
    p = cm.party_checked()
    assert p[1].egg and not p[0].egg and p[1].species == 155
    assert p[1].box_record()[-1] == 1


def test_full_heal_and_dex():
    mem, cm = in_game_mem()
    d = bytearray(cyndaquil().data)
    d[35] = 1
    d[32] = 0x08
    d[23] = 0x40 | 3                              # 1 PP up, 3 left
    cm.set_party([Mon(bytes(d), text.encode("CHRIS"), text.encode("X"))])
    cm.heal_party()
    m = cm.party()[0]
    assert m.hp == m.max_hp and m.data[32] == 0 and m.data[23] == 0x40 | 42
    cm.set_dex(155)
    assert cm.r(crystal.DEX_CAUGHT + 19) & (1 << 2) and cm.r(crystal.DEX_SEEN + 19) & (1 << 2)


def test_mon_wire():
    m = cyndaquil()
    assert Mon.from_wire(m.to_wire()) == m
    with pytest.raises(ValueError):
        Mon.from_wire({"data": crystal.b64(bytes(44)), "ot": m.to_wire()["ot"], "nick": m.to_wire()["nick"]})


# --- PC boxes ---------------------------------------------------------------------------------------------------
def rec(species, nick, level=10, otid=1, egg=False):
    d = bytearray(32)
    d[0], d[6], d[7], d[31] = species, otid >> 8, otid & 0xFF, level
    return crystal.record(bytes(d), text.encode("CHRIS"), text.encode(nick), egg)


def setup_pc(cur=0, initialised=True):
    mem = FakeMem()
    cm = CrystalMemory(mem)
    cm.w(crystal.CUR_BOX, cur)
    cm.w(crystal.SAVED_AT_LEAST_ONCE, 1 if initialised else 0)
    return mem, Gen2PC(mem, FakeRom())


def test_box_codec_roundtrip():
    mons = [rec(25, "PIKA"), rec(161, "RATTY", egg=True)]
    raw = crystal.build_box(mons)
    assert len(raw) == crystal.BOX_STRIDE and raw[0] == 2 and list(raw[1:4]) == [25, crystal.EGG, 0xFF]
    assert raw[crystal.BOX_BYTES:] == b"\xff\x00"            # padding as written by EraseBoxes
    assert crystal.parse_box(raw) == mons
    assert crystal.parse_box(crystal.build_box([])) == []


def test_write_read_boxes_placement():
    mem, pc = setup_pc(cur=8)
    boxes = [[] for _ in range(14)]
    boxes[0] = [rec(1, "A")]
    boxes[6] = [rec(2, "B")]
    boxes[7] = [rec(3, "C")]
    boxes[8] = [rec(4, "CUR"), rec(5, "CUR2")]
    boxes[13] = [rec(6, "LAST")] * 20
    pc.write_boxes(boxes)
    assert pc.read_boxes() == boxes
    assert mem.sram[2][0] == 1 and mem.sram[2][6 * 0x450] == 1 and mem.sram[3][0] == 1
    assert mem.sram[3][6 * 0x450] == 20
    # the current box is also in sBox (bank 1 AD10), which is what the game reads/writes live
    assert mem.sram[1][0xAD10 - 0xA000] == 2 and mem.sram[1][0xAD11 - 0xA000] == 4
    # and its slot holds the same, so a reload (LoadBox) gives the same box
    assert mem.sram[3][1 * 0x450] == 2


def test_read_prefers_live_sbox_for_current_box():
    mem, pc = setup_pc(cur=1)
    pc.write_boxes([[] for _ in range(14)])
    live = crystal.build_box([rec(9, "NEW")])
    for i, v in enumerate(live[:crystal.BOX_BYTES]):
        mem[1, 0xAD10 + i] = v                   # deposited in game, not saved yet
    assert pc.read_boxes()[1] == [rec(9, "NEW")]


def test_uninitialised_boxes():
    mem, pc = setup_pc(initialised=False)
    for b in (2, 3):
        mem.sram[b][:] = bytes([0x55]) * 0x2000   # garbage before the first save
    mem.sram[1][0xAD10 - 0xA000] = 0
    mem.sram[1][0xAD11 - 0xA000] = 0xFF
    assert pc.read_boxes() == [[] for _ in range(14)]
    with pytest.raises(RuntimeError):
        pc.write_boxes([[] for _ in range(14)])


def test_write_boxes_keeps_save_checksum():
    mem, pc = setup_pc()
    data = bytes((i * 13) & 0xFF for i in range(0xAB83 - 0xA009))
    mem.sram[1][0x009:0x009 + len(data)] = data
    s = crystal.checksum16(data)
    mem.sram[1][0xD0D], mem.sram[1][0xD0E] = s & 0xFF, s >> 8
    assert pc.save_checksum_ok()
    pc.write_boxes([[rec(1, "A")]] * 14)
    assert pc.save_checksum_ok()


def test_entries_and_records():
    mem, pc = setup_pc()
    r = rec(152, "LEAFY", level=7, otid=0x1234)
    e = pc.entry_from_record(r)
    assert (e["dex"], e["level"], e["nick"], e["ot"], e["otid"], e["origin"]) == (152, 7, "LEAFY", "CHRIS", 0x1234, "crystal")
    assert e["gen1"] is None and pc.record_for(e) == r


def gen1_rec(species=0x54, catch_rate=0xA3, level=12, moves=(84, 45, 0, 0)):
    d = bytearray(33)
    d[0], d[3], d[7] = species, level, catch_rate
    d[8:12] = bytes(moves)
    d[12], d[13] = 0x30, 0x39
    d[14:17] = (1728).to_bytes(3, "big")
    d[17:27] = bytes(range(1, 11))
    d[27], d[28] = 0xAB, 0xCD
    d[29:33] = bytes([30, 40, 0, 0])
    return bytes(d) + text.encode("ASH") + text.encode("SPARKY")


def test_gen1_to_gen2_time_capsule_rules():
    g2 = crystal.gen1_to_gen2(gen1_rec(), 25)
    assert len(g2) == crystal.RECORD_SIZE
    assert g2[0] == 25 and g2[1] == 0xA3 and list(g2[2:6]) == [84, 45, 0, 0]
    assert g2[6:8] == b"\x30\x39" and int.from_bytes(g2[8:11], "big") == 1728
    assert g2[11:21] == bytes(range(1, 11)) and g2[21:23] == b"\xab\xcd" and list(g2[23:27]) == [30, 40, 0, 0]
    assert g2[27] == 70 and g2[28] == 0 and g2[31] == 12
    assert text.decode(g2[32:43]) == "ASH" and text.decode(g2[43:54]) == "SPARKY" and g2[54] == 0
    assert crystal.gen1_to_gen2(gen1_rec(catch_rate=0x19), 25)[1] == 0x92      # -> LEFTOVERS
    assert crystal.gen1_to_gen2(gen1_rec(catch_rate=0xFF), 25)[1] == 0xAD      # -> BERRY
    assert crystal.gen1_to_gen2(gen1_rec(), 0) is None


def test_record_for_prefers_gen2_then_gen1_then_standin():
    mem, pc = setup_pc()
    native = rec(25, "NATIVE")
    assert pc.record_for({"gen2": crystal.b64(native), "gen1": crystal.b64(gen1_rec()), "dex": 25}) == native
    conv = pc.record_for({"gen2": None, "gen1": crystal.b64(gen1_rec()), "dex": 25, "level": 12})
    assert conv[0] == 25 and not pc.is_standin(conv)
    st = pc.record_for({"gen2": None, "gen1": crystal.b64(gen1_rec()), "dex": 0, "nick": "MISSINGNO", "level": 30})
    assert pc.is_standin(st) and st[0] == crystal.DITTO and st[31] == 30 and st[2] == crystal.TRANSFORM
    assert text.decode(st[43:54]) == "?MISSINGNO" and len(st) == crystal.RECORD_SIZE
    assert not pc.is_standin(native)


def test_pcsync_with_gen2_codec(tmp_path):
    """The game-agnostic PCSync works unchanged with Gen2PC (deposit + withdraw + move)."""
    mem, pc = setup_pc(cur=0)
    pc.write_boxes([[rec(25, "PIKA")], [rec(1, "BULBA")]] + [[] for _ in range(12)])
    sync = pcdb.PCSync(tmp_path / "store.json")
    view = sync.open_session("crystal", pc, trust_game=True)
    assert [len(b) for b in view[:2]] == [1, 1]
    pc.write_boxes(view)
    # in game: deposit a new mon into box 1, withdraw BULBA from box 2
    boxes = pc.read_boxes()
    boxes[0].append(rec(155, "CYNDA"))
    boxes[1] = []
    pc.write_boxes(boxes)
    report = sync.close_session("crystal", pc)
    assert report["added"] == 1 and report["removed"] == 1
    assert [e["nick"] for e in sync.working["boxes"][0]] == ["PIKA", "CYNDA"]
    assert sync.working["boxes"][1] == []
    assert sync.working["boxes"][0][1]["gen2"] is not None
