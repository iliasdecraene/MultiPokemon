"""Nintendo DS core: py-desmume (libdesmume) behind the small PyBoy-like API app.py uses for Pokémon Platinum.

    core = NDSCore(rom_path, save_path, volume=60)   # opens the ROM and loads OUR battery save (or a blank one)
    core.tick()                                     # one DS frame (59.8261 fps)
    core.button_press("a"); core.button_release("a")   # a b x y l r start select up down left right
    core.touch(x, y); core.touch_release()          # bottom screen, 0..255 x 0..191
    core.screen_rgb()                               # bytes, 256x384 RGB: top screen above the bottom screen
    core.save_state(f) / core.load_state(f)         # file object or path (DeSmuME savestates)
    core.export_save() -> bytes                     # the 512 KB battery save
    core.read(addr, n) / r8 / r16 / r32 / write(addr, data) / w8 / w16 / w32   # memory bus (BRIEF.md)
    core.hook_register(addr, fn)                    # exec hook (fn() runs when the ARM9 executes addr)

Facts verified with libdesmume 0.9.12 (py-desmume 0.0.9), see dev/platinum/STATUS.md:
- libdesmume is global: one emulator per process, and it can NOT be re-created after desmume_free (segfault). So
  this module keeps one DeSmuME for the whole process and every NDSCore (e.g. after travelling back to Sinnoh)
  re-opens the ROM in it. It is never freed (the OS cleans up at exit).
- desmume_init needs an SDL *video* driver (Linux headless: SDL_VIDEODRIVER=dummy). A missing audio device is not
  fatal: DeSmuME's SDL sound core just stays off (silent). Every ROM open / save import resets the DS and reopens
  DeSmuME's own audio device (SDL legacy device 1), so sound comes back after pygame.quit()/init() on travel.
- DeSmuME keeps its own live battery file `<battery dir>/<rom name>.dsv` (Linux: $XDG_CONFIG_HOME/desmume/, Windows:
  the folder of the .exe). That file is shared by every server and challenge, so we never rely on it: right after
  opening the ROM we always import OUR per-server save (or a blank one), and on close we blank it again.
- import_file/export_file use plain fopen (no UTF-8 on Windows): they go through an ASCII-only staging folder.
- The Python wrapper imports PIL at import time (only for screenshot(), which we don't use); the exe doesn't ship
  Pillow, so a tiny stand-in module is installed when PIL is missing.
"""
from __future__ import annotations

import atexit
import importlib
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import types

log = logging.getLogger("yellowlink.nds")

FPS = 59.8261
FRAME_TIME = 1 / FPS
SCREEN_W, SCREEN_H = 256, 192
SAVE_SIZE = 512 * 1024              # Pokémon Platinum: 4 Mbit flash
VOLUME_MAX = 128                    # DeSmuME's volume is SDL_MixAudio's 0..128

# DS key bits (desmume.controls.keymask(Keys.KEY_*) = 1 << (key - 1))
BUTTONS = {"a": 1 << 0, "b": 1 << 1, "select": 1 << 2, "start": 1 << 3, "right": 1 << 4, "left": 1 << 5,
           "up": 1 << 6, "down": 1 << 7, "r": 1 << 8, "l": 1 << 9, "x": 1 << 10, "y": 1 << 11}

class DSUnavailable(RuntimeError):
    """The DS emulator can't run on this PC (library missing/broken, no video driver, ROM refused)."""


_EMU = None          # the one DeSmuME of this process
_ACTIVE = None       # the NDSCore currently using it


def _install_pil_standin():
    try:
        importlib.import_module("PIL.Image")
        return
    except Exception:  # noqa: BLE001 - not installed (the exe) or broken
        pass
    pil = types.ModuleType("pil")
    image = types.ModuleType("pil.Image")

    class Image:          # only used as a type annotation by desmume.emulator
        pass
    image.Image = Image
    pil.Image = image
    sys.modules.setdefault("pil", pil)
    sys.modules.setdefault("pil.Image", image)


def _library_path():
    """libdesmume next to the frozen exe's bundled desmume package (None: let py-desmume find it)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    for name in ("libdesmume.dll", "libdesmume.so", "libdesmume.dylib"):
        p = os.path.join(base, "desmume", name)
        if os.path.isfile(p):
            return p
    return None


def get_emulator():
    """The process-wide DeSmuME (created on first use). Raises RuntimeError if it can't start."""
    global _EMU
    if _EMU is None:
        _install_pil_standin()
        from desmume.emulator import DeSmuME
        dl = _library_path()
        log.info("starting DeSmuME (%s)", dl or "py-desmume default library")
        try:
            _EMU = DeSmuME(dl) if dl else DeSmuME()
        except Exception as e:
            raise DSUnavailable(f"the DS emulator could not start: {e}") from e
        atexit.register(_forget)
    return _EMU


def _forget():
    """At exit: don't let DeSmuME.__del__ run desmume_free (it can crash during interpreter shutdown)."""
    global _EMU
    if _EMU is not None:
        try:
            _EMU.pause()
        except Exception:  # noqa: BLE001
            pass
        _EMU.lib = None
        _EMU = None


def _is_ascii(p: str) -> bool:
    try:
        p.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _short_path(p: str) -> str:
    """Windows: an ASCII 8.3 alias for a path with accents etc. (DeSmuME opens some files with plain fopen)."""
    if _is_ascii(p) or sys.platform != "win32":
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024)
        if 0 < n < 1024 and _is_ascii(buf.value):
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return p


def _staging_dir() -> str:
    """A private folder with an ASCII-only path for files DeSmuME reads/writes (imports, exports, states)."""
    candidates = [tempfile.gettempdir()]
    if sys.platform == "win32":
        candidates.append(os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "YellowLink"))
        candidates.append(os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "YellowLinkTemp"))
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            base = _short_path(base)
            if not _is_ascii(base):
                continue
            return tempfile.mkdtemp(prefix="ylnds-", dir=base)
        except OSError:
            continue
    return tempfile.mkdtemp(prefix="ylnds-")


def battery_file_candidates(rom_path: str) -> list:
    """Where libdesmume keeps its own live battery file for this ROM (see the module docstring)."""
    name = os.path.splitext(os.path.basename(rom_path))[0] + ".dsv"
    out = []
    if sys.platform == "win32":
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        out += [os.path.join(exe_dir, name), os.path.join(exe_dir, "Battery", name)]
    else:
        cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        out.append(os.path.join(cfg, "desmume", name))
    return out


class NDSCore:
    def __init__(self, rom_path: str, save_path=None, volume: int = 60):
        global _ACTIVE
        if _ACTIVE is not None and _ACTIVE is not self:
            _ACTIVE.close()
        self.emu = get_emulator()
        _ACTIVE = self
        lib = self.emu.lib
        self._lib = lib
        self._rd8 = lib.desmume_memory_read_byte
        self._rd16 = lib.desmume_memory_read_short
        self._rd32 = lib.desmume_memory_read_long
        self._wr8 = lib.desmume_memory_write_byte
        self._wr16 = lib.desmume_memory_write_short
        self._wr32 = lib.desmume_memory_write_long
        self._keys = 0
        self._hooks = {}
        self._shot_buf = None
        self._volume = -1
        self.frame_count = 0
        self.closed = False
        self.rom_path = rom_path
        self.tmp = _staging_dir()
        try:
            self.emu.open(_short_path(os.path.abspath(rom_path)))
        except Exception as e:
            _ACTIVE = None
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise DSUnavailable(f"the DS emulator could not open the game file: {e}") from e
        lib.desmume_input_keypad_update(0)
        lib.desmume_input_release_touch()
        self.set_volume(volume)
        # Never use DeSmuME's shared .dsv: load our own save (or a blank one) into it right away.
        self.loaded_save = self.import_save(save_path)

    # --- time + input ------------------------------------------------------------------------------------------
    def tick(self, count: int = 1, render: bool = True) -> bool:
        cycle = self._lib.desmume_cycle
        for _ in range(count):
            cycle(False)
        self.frame_count += count
        return True

    def button_press(self, name: str):
        self._keys |= BUTTONS[name]
        self._lib.desmume_input_keypad_update(self._keys)

    def button_release(self, name: str):
        self._keys &= ~BUTTONS[name]
        self._lib.desmume_input_keypad_update(self._keys)

    def release_all(self):
        self._keys = 0
        self._lib.desmume_input_keypad_update(0)
        self._lib.desmume_input_release_touch()

    def touch(self, x: int, y: int):
        """Touch (or drag to) a point on the bottom screen."""
        self._lib.desmume_input_set_touch_pos(max(0, min(SCREEN_W - 1, int(x))), max(0, min(SCREEN_H - 1, int(y))))

    def touch_release(self):
        self._lib.desmume_input_release_touch()

    # --- screens -----------------------------------------------------------------------------------------------
    def screen_rgb(self) -> bytes:
        """Both screens as 256x384 RGB bytes (top screen first) - what the window shows."""
        buf = self._shot_buf
        if buf is None:
            import ctypes
            buf = self._shot_buf = ctypes.create_string_buffer(SCREEN_W * SCREEN_H * 2 * 3)
            self._shot_ptr = ctypes.cast(buf, ctypes.c_char_p)
        self._lib.desmume_screenshot(self._shot_ptr)
        return buf.raw

    def screen_rgbx(self) -> bytes:
        """Both screens as 256x384x4 bytes, top screen first (despite DeSmuME's name the order is B, G, R, X)."""
        return bytes(self.emu.display_buffer_as_rgbx())

    def screen_ndarray(self):
        """(384, 256, 3) uint8 RGB."""
        import numpy as np
        return np.frombuffer(self.screen_rgb(), dtype=np.uint8).reshape(2 * SCREEN_H, SCREEN_W, 3)

    # --- sound ---------------------------------------------------------------------------------------------------
    def set_volume(self, percent: int):
        """0..100 (DeSmuME plays the sound itself through its own SDL audio device)."""
        v = max(0, min(VOLUME_MAX, int(round(max(0, min(100, percent)) * VOLUME_MAX / 100))))
        if v != self._volume:
            self._volume = v
            self.emu.volume_set(v)

    def pause_audio(self, paused: bool):
        """Stop DeSmuME's sound device while we aren't emulating (it would loop its last ~0.1 s otherwise)."""
        if paused:
            self._lib.desmume_pause()
        else:
            self._lib.desmume_resume()

    # --- battery save ------------------------------------------------------------------------------------------
    def import_save(self, save_path=None) -> bool:
        """Load a battery save into the game (resets the DS). No/short file -> a blank (never saved) one."""
        data = b""
        if save_path:
            try:
                with open(save_path, "rb") as f:
                    data = f.read(SAVE_SIZE)
            except OSError:
                data = b""
        ours = len(data) == SAVE_SIZE
        if not ours:
            data = b"\xff" * SAVE_SIZE
        p = os.path.join(self.tmp, "import.sav")
        with open(p, "wb") as f:
            f.write(data)
        if not self.emu.backup.import_file(p, SAVE_SIZE):
            raise RuntimeError("DeSmuME refused the save file")
        os.remove(p)
        return ours

    def export_save(self) -> bytes | None:
        """The current battery save (512 KB), or None if DeSmuME couldn't export it."""
        p = os.path.join(self.tmp, "export.sav")
        try:
            if not self.emu.backup.export_file(p):
                return None
            with open(p, "rb") as f:
                data = f.read()
            return data if len(data) >= SAVE_SIZE else None
        except OSError:
            return None
        finally:
            try:
                os.remove(p)
            except OSError:
                pass

    # --- savestates ----------------------------------------------------------------------------------------------
    def save_state(self, f):
        p = os.path.join(self.tmp, "state.dst")
        self.emu.savestate.save_file(p)
        try:
            with open(p, "rb") as src:
                data = src.read()
        finally:
            os.remove(p)
        if isinstance(f, (str, os.PathLike)):
            with open(f, "wb") as out:
                out.write(data)
        else:
            f.write(data)

    def load_state(self, f):
        if isinstance(f, (str, os.PathLike)):
            with open(f, "rb") as src:
                data = src.read()
        else:
            data = f.read()
        if len(data) < 1024:
            raise RuntimeError("not a DS savestate")
        p = os.path.join(self.tmp, "state.dst")
        with open(p, "wb") as out:
            out.write(data)
        try:
            self.emu.savestate.load_file(p)
        finally:
            os.remove(p)
        self._lib.desmume_input_keypad_update(self._keys)

    # --- memory bus (dev/platinum/BRIEF.md) ------------------------------------------------------------------
    def r8(self, a: int) -> int:
        return self._rd8(a)

    def r16(self, a: int) -> int:
        return self._rd16(a)

    def r32(self, a: int) -> int:
        return self._rd32(a)

    def read(self, a: int, n: int) -> bytes:
        if n <= 0:
            return b""
        start = a & ~3
        words = (a + n - start + 3) // 4
        rd = self._rd32
        raw = struct.pack(f"<{words}I", *(rd(start + 4 * i) for i in range(words)))
        return raw[a - start:a - start + n]

    def w8(self, a: int, v: int):
        self._wr8(a, v & 0xFF)

    def w16(self, a: int, v: int):
        self._wr16(a, v & 0xFFFF)

    def w32(self, a: int, v: int):
        self._wr32(a, v & 0xFFFFFFFF)

    def write(self, a: int, data: bytes):
        data = bytes(data)
        i = 0
        while i < len(data) and (a + i) & 3:
            self._wr8(a + i, data[i])
            i += 1
        while i + 4 <= len(data):
            self._wr32(a + i, struct.unpack_from("<I", data, i)[0])
            i += 4
        while i < len(data):
            self._wr8(a + i, data[i])
            i += 1

    # --- exec hooks --------------------------------------------------------------------------------------------
    def hook_register(self, addr: int, fn):
        """fn() runs (on the emulation thread, inside tick) when the ARM9 executes addr. Exceptions are logged."""
        def cb(_address, _size):
            try:
                fn()
            except Exception:  # noqa: BLE001 - never break the emulator from a hook
                log.exception("DS hook at %08X failed", addr)
        self._hooks[addr] = cb
        self.emu.memory.register_exec(addr, cb)

    def hook_deregister(self, addr: int):
        if self._hooks.pop(addr, None) is not None:
            self.emu.memory.register_exec(addr, None)

    # --- lifetime ----------------------------------------------------------------------------------------------
    def close(self, blank_battery: bool = True):
        """Stop using the emulator (it stays alive for the next NDSCore). Blanks DeSmuME's shared battery file."""
        global _ACTIVE
        if self.closed:
            return
        self.closed = True
        for addr in list(self._hooks):
            try:
                self.hook_deregister(addr)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.release_all()
            if blank_battery:
                self.import_save(None)       # the shared .dsv must not keep this server's save
            self.emu.pause()
            self.emu.close()
        except Exception:  # noqa: BLE001
            log.exception("closing the DS emulator failed")
        if getattr(sys, "frozen", False):
            for p in battery_file_candidates(self.rom_path):
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        if _ACTIVE is self:
            _ACTIVE = None


def make_test_rom(path):
    """A 1 MB do-nothing .nds (zeros + a minimal header) for tests and the release smoke test - no Nintendo data."""
    data = bytearray(1 << 20)
    data[0:12] = b"YLINKTEST\0\0\0"
    data[12:16] = b"YLTS"
    struct.pack_into("<IIII", data, 0x20, 0x4000, 0x02000000, 0x02000000, 0x100)    # ARM9 code
    struct.pack_into("<IIII", data, 0x30, 0x8000, 0x02380000, 0x02380000, 0x100)    # ARM7 code
    struct.pack_into("<I", data, 0x80, len(data))
    with open(path, "wb") as f:
        f.write(data)
    return path


def selftest(rom_path=None) -> str:
    """Start DeSmuME without a game (and with rom_path if given): used by the release build's smoke test."""
    emu = get_emulator()
    emu.volume_set(64)
    out = io.StringIO()
    out.write(f"desmume ok, volume {emu.volume_get()}\n")
    if rom_path:
        core = NDSCore(rom_path, None, 50)
        core.tick(30)
        save = core.export_save()
        out.write(f"rom ok, {core.frame_count} frames, save {len(save or b'')} bytes, "
                  f"screen {len(core.screen_rgbx())} bytes\n")
        out.write("battery candidates: " + ", ".join(
            f"{p} ({'exists' if os.path.exists(p) else 'missing'})" for p in battery_file_candidates(rom_path)) + "\n")
        core.close()
    return out.getvalue()
