"""NDSCore (DeSmuME) with a do-nothing test .nds (no Nintendo data): battery save, savestates, memory bus, input."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from yellowlink import nds_core  # noqa: E402


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("nds")
    old = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "cfg")      # DeSmuME's own .dsv lands here (Linux)
    try:
        try:
            nds_core.get_emulator()
        except Exception as e:  # noqa: BLE001 - e.g. no libGL on a bare CI image
            pytest.skip(f"DeSmuME unavailable: {e}")
        rom = nds_core.make_test_rom(str(tmp / "test.nds"))
        c = nds_core.NDSCore(rom, None, 50)
        yield c
        c.close()
    finally:
        if old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old


def test_blank_save_and_frames(core):
    assert core.loaded_save is False
    core.tick(5)
    assert core.frame_count >= 5
    save = core.export_save()
    assert save is not None and len(save) == nds_core.SAVE_SIZE and set(save) == {0xFF}
    assert len(core.screen_rgb()) == 256 * 384 * 3


def test_import_our_save(core, tmp_path):
    data = bytes(range(256)) * (nds_core.SAVE_SIZE // 256)
    p = tmp_path / "ours.sav"
    p.write_bytes(data)
    assert core.import_save(p) is True
    assert core.export_save() == data
    assert core.import_save(None) is False                 # back to a blank battery
    assert set(core.export_save()) == {0xFF}


def test_memory_bus(core):
    a = 0x023FE000
    core.write(a + 1, b"hello world!")
    assert core.read(a + 1, 12) == b"hello world!"
    core.w32(a, 0x11223344)
    assert core.r32(a) == 0x11223344 and core.r16(a) == 0x3344 and core.r8(a + 3) == 0x11
    assert core.read(a, 4) == bytes.fromhex("44332211")


def test_savestate_roundtrip(core):
    a = 0x023FE100
    core.w32(a, 0xCAFEBABE)
    buf = io.BytesIO()
    core.save_state(buf)
    core.w32(a, 0)
    core.load_state(io.BytesIO(buf.getvalue()))
    assert core.r32(a) == 0xCAFEBABE


def test_input_reaches_keyinput(core):
    core.button_press("a")
    core.button_press("r")
    core.tick(2)
    keys = core.r16(0x04000130)                 # KEYINPUT, active low
    assert not keys & 1 and not keys & 0x100
    core.release_all()
    core.tick(2)
    assert core.r16(0x04000130) & 0x3FF == 0x3FF


def test_volume_mapping(core):
    core.set_volume(100)
    assert core.emu.volume_get() == nds_core.VOLUME_MAX
    core.set_volume(0)
    assert core.emu.volume_get() == 0
