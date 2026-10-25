"""Unit tests for game-memory logic. They use a synthetic ROM/RAM, so no real ROM is needed."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import game, sprites, text  # noqa: E402
from yellowlink.game import GameMemory, Mon, RomData  # noqa: E402


def fake_rom():
    rom = bytearray(0x100000)
    # dex order: species 0x26 (Kadabra) -> 64, 0x95 (Alakazam) -> 65, 0x54 Pikachu -> 25
    rom[game.ROM_DEX_ORDER + 0x26 - 1] = 64
    rom[game.ROM_DEX_ORDER + 0x95 - 1] = 65
    rom[game.ROM_DEX_ORDER + 0x54 - 1] = 25
    o = game.ROM_BASE_STATS + 64 * 28  # Alakazam (dex 65)
    rom[o:o + 8] = bytes([65, 55, 50, 45, 120, 135, 0x18, 0x18])
    for sp, name in ((0x26, "KADABRA"), (0x95, "ALAKAZAM"), (0x54, "PIKACHU")):
        rom[game.ROM_MON_NAMES + (sp - 1) * 10: game.ROM_MON_NAMES + sp * 10] = text.encode(name, 10)
    rom[game.ROM_MOVES + (0x5D - 1) * 6 + 5] = 25  # CONFUSION pp 25
    return RomData(bytes(rom))


def make_mon(species, level=16, hp=40, max_hp=40, nick="KADABRA", ot="BROCK", move=0x5D, pp_ups=0):
    d = bytearray(44)
    d[0] = species
    d[1:3] = hp.to_bytes(2, "big")
    d[3] = level
    d[5] = d[6] = 0x18
    d[8] = move
    d[12:14] = (222).to_bytes(2, "big")
    d[27:29] = b"\xaa\xaa"
    d[29] = (pp_ups << 6) | 3
    d[33] = level
    d[34:36] = max_hp.to_bytes(2, "big")
    return Mon(bytes(d), text.encode(ot), text.encode(nick))


@pytest.fixture
def gm():
    mem = bytearray(0x10000)
    g = GameMemory(mem, fake_rom())
    mem[game.PLAYER_PIC] = 1
    mem[game.PLAYER_NAME] = 0x80
    return g


def test_text_roundtrip():
    assert text.decode(text.encode("Ash 2")) == "Ash 2"
    assert len(text.encode("ABCDEFGHIJKLMNOP")) == 11
    assert text.sanitize_name("  Al!ex_99  ") == "Alex99"


def test_mon_wire_roundtrip():
    m = make_mon(0x26)
    assert Mon.from_wire(m.to_wire()) == m
    with pytest.raises(ValueError):
        Mon.from_wire({"data": game.b64(bytes(44)), "ot": game.b64(bytes(11)), "nick": game.b64(bytes(11))})


def test_safe_state(gm):
    assert gm.is_safe()
    gm.m[game.FONT_LOADED] = 1
    assert not gm.is_safe()
    gm.m[game.FONT_LOADED] = 0
    gm.m[game.WALK_COUNTER] = 3
    assert not gm.is_safe()


def test_position_mid_step(gm):
    gm.m[game.X_COORD], gm.m[game.Y_COORD] = 5, 6
    gm.m[game.WALK_COUNTER] = 4
    gm.m[game.PLAYER_XVEC] = 0xFF  # walking left
    p = gm.position()
    assert (p["px"], p["py"]) == (5 * 16 - 8, 96)


def test_trade_apply_with_evolution(gm):
    mine = make_mon(0x54, nick="PIKACHU", ot="ASH")
    gm.m[game.PARTY_COUNT] = 1
    gm.write_party_slot(0, mine)
    received = make_mon(0x26, hp=30, max_hp=40)
    new = gm.apply_trade(0, mine, received)
    assert new.species == 0x95
    assert new.nick_str() == "ALAKAZAM"
    assert new.max_hp > 0 and new.max_hp - new.hp == 10  # damage carried over
    assert gm.party()[0] == new
    assert gm.m[game.DEX_OWNED + 64 // 8] & (1 << (64 % 8))  # alakazam (dex 65) owned


def test_trade_apply_refuses_changed_party(gm):
    mine = make_mon(0x54, nick="PIKACHU", ot="ASH")
    gm.m[game.PARTY_COUNT] = 1
    gm.write_party_slot(0, make_mon(0x26))
    with pytest.raises(RuntimeError):
        gm.apply_trade(0, mine, make_mon(0x26))


def test_full_heal_restores_pp(gm):
    m = game.full_heal(make_mon(0x26, hp=1, max_hp=40, pp_ups=2), gm.rom)
    assert m.hp == 40
    assert m.data[29] & 0x3F == 25 + 5 * 2
    assert m.data[29] >> 6 == 2


def test_inject_enemy(gm):
    gm.inject_enemy([make_mon(0x26), make_mon(0x54)], "Brock")
    assert gm.m[game.ENEMY_PARTY_COUNT] == 2
    assert gm.m[game.ENEMY_PARTY_SPECIES + 2] == 0xFF
    assert text.decode(gm.read(game.TRAINER_NAME, 11)) == "BROCK"


def test_sprite_layout():
    assert sprites.frame_layout(0x0) == (0, False)
    assert sprites.frame_layout(0x3) == (0x80, True)
    assert sprites.frame_layout(0x9) == (0x88, False)
    assert sprites.frame_layout(0xC) == (8, True)
    assert sprites.frame_layout(0xD) == (0x88, True)
    blob = bytes(range(256)) + bytes(128)
    grid = sprites.compose(blob, 0)
    assert len(grid) == 16 and all(len(r) == 16 for r in grid)
