"""Shared helpers for the Crystal dev harnesses (see dev/README.md).

Paths: YL_CRYSTAL_ROM (default dev/states/crystal.gbc), YL_STATES (default dev/states).
"""
import io
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROM = os.environ.get("YL_CRYSTAL_ROM", os.path.join(REPO, "dev", "states", "crystal.gbc"))
STATES = os.environ.get("YL_STATES", os.path.join(REPO, "dev", "states"))
sys.path.insert(0, os.path.join(REPO, "client"))

from pyboy import PyBoy  # noqa: E402

from yellowlink import text  # noqa: E402

TILEMAP = 0xC4A0          # wTilemap, 20x18, WRAM0


def new_pyboy(sav: bytes | None = None, rtc: bytes | None = None, state: str | None = None):
    ram = io.BytesIO(sav if sav is not None else bytes(32768))
    rtcf = io.BytesIO(rtc) if rtc is not None else None
    kw = {"rtc_file": rtcf} if rtcf is not None else {"rtc_file": io.BytesIO(_blank_rtc())}
    pb = PyBoy(ROM, window="null", sound_emulated=False, ram_file=ram, log_level="ERROR", **kw)
    pb.set_emulation_speed(0)
    if state:
        with open(state if os.path.isabs(state) else os.path.join(STATES, state), "rb") as f:
            pb.load_state(f)
    return pb


def _blank_rtc() -> bytes:
    """A valid, zeroed PyBoy RTC file (PyBoy can't parse an empty one)."""
    import struct
    import time
    out = bytearray(struct.pack("d", time.time()))
    out += bytes([0, 0, 0, 0, 0, 0, 0, 0, 0])
    out += bytes(8)                                 # rtc_cycles (64-bit)
    out += struct.pack("d", time.time())
    return bytes(out)


def ticks(pb, n=1, render=False):
    for _ in range(n):
        pb.tick(1, render)


def tap(pb, b, hold=4, wait=10):
    pb.button_press(b)
    ticks(pb, hold)
    pb.button_release(b)
    ticks(pb, wait)


def screen_text(pb) -> list:
    m = pb.memory
    rows = []
    for r in range(18):
        raw = bytes(m[TILEMAP + r * 20 + c] for c in range(20))
        rows.append("".join(_ch(v) for v in raw))
    return rows


def _ch(v):
    if v == 0x7F or v == 0x50:
        return " "
    s = text.decode(bytes([v]))
    return s if s != "?" else ("?" if v == 0xE6 else ".")


def has_text(pb, s: str) -> bool:
    return any(s in r for r in screen_text(pb))


def dump(pb):
    print("\n".join("|" + r + "|" for r in screen_text(pb)))


def wram(pb, addr, bank=1):
    """Read a WRAMX byte from a fixed bank (Crystal switches SVBK)."""
    return pb.memory[bank, addr] if 0xD000 <= addr < 0xE000 else pb.memory[addr]


def save_png(pb, name):
    path = os.path.join(os.environ.get("YL_SHOTS", "/tmp"), name)
    pb.screen.image.save(path)
    return path


def sram_dump(pb) -> bytes:
    return b"".join(bytes(pb.memory[b, 0xA000:0xBFFF]) + bytes([pb.memory[b, 0xBFFF]]) for b in range(4))
