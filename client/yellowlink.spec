# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the YellowLink desktop client.
#
# Build (from the repository root or from client/):
#     pyinstaller --noconfirm --clean client/yellowlink.spec
# Output: dist/YellowLink.exe (Windows) / dist/YellowLink (Linux/macOS),
# placed next to wherever pyinstaller was run from.

import os

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

# SPECPATH is the directory containing this spec file (= client/).
CLIENT_DIR = os.path.abspath(SPECPATH)

datas = []
binaries = []
hiddenimports = []


_SOURCE_EXTS = (".c", ".h", ".pxd", ".pyx", ".pyi")


def _skip_tests(name):
    # Drop bundled test suites / examples (pygame.tests, sdl2.test,
    # numpy.*.tests, pyboy.conftest, ...). Anything really imported at runtime
    # is still found by PyInstaller's normal import analysis.
    parts = set(name.split("."))
    return not (parts & {"test", "tests", "testing", "examples", "conftest"})


def _add(pkg, required=True):
    """collect_all() a package: data files, shared libs and all submodules."""
    try:
        d, b, h = collect_all(pkg, filter_submodules=_skip_tests)
    except Exception as exc:  # package not installed
        if required:
            raise
        print(f"[yellowlink.spec] optional package {pkg!r} skipped: {exc}")
        return
    if required and not (d or b or h):
        raise SystemExit(f"[yellowlink.spec] required package {pkg!r} not found")
    # Drop Cython/C build sources shipped in some wheels (pyboy ships ~64 .c
    # files of several MB each); only runtime data files are needed.
    d = [(src, dst) for src, dst in d if not src.endswith(_SOURCE_EXTS)]
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# PyBoy: compiled Cython extension modules (*.pyd / *.so) that import each
# other at C level (invisible to PyInstaller's analysis), plus data files:
# core/bootrom_dmg.bin, core/bootrom_cgb.bin, plugins/font.txt.
_add("pyboy")
# PyBoy depends on PySDL2 ("sdl2") and pysdl2-dll ("sdl2dll", the SDL2 DLLs).
# They are only used by PyBoy's SDL2 window/debug plugins, but are bundled so
# plugin imports never fail at runtime.
_add("sdl2", required=False)
_add("sdl2dll", required=False)
# pygame-ce installs as the package "pygame" (includes its own SDL2 DLLs/fonts).
_add("pygame")
_add("websockets")
_add("numpy")
# py-desmume (Pokémon Platinum): the Python wrapper + libdesmume.dll and the SDL2.dll it ships with. Its GTK
# frontend (desmume.frontend, *.glade) is never used. At runtime nds_core.py loads
# <bundle>/desmume/libdesmume.dll explicitly; libdesmume then shares pygame's already loaded SDL2.dll.
_ds_libs = collect_dynamic_libs("desmume")
if not any(os.path.basename(src).lower().startswith("libdesmume") for src, _ in _ds_libs):
    raise SystemExit("[yellowlink.spec] py-desmume's libdesmume library not found (pip install py-desmume)")
binaries.extend(_ds_libs)
hiddenimports += ["desmume", "desmume.emulator", "desmume.controls", "desmume.i18n_util"]

# Our own package: EVERY module, also the ones only loaded on demand with importlib (raid_gb, raid_pt, raid_data,
# raid_art, platinum_exp - PyInstaller's analysis can't see those). collect_submodules() needs the package on
# sys.path at spec time (it wasn't, so 0.7.3-0.7.10 shipped without them); the file list below is the safety net.
import sys as _sys
if CLIENT_DIR not in _sys.path:
    _sys.path.insert(0, CLIENT_DIR)
hiddenimports += collect_submodules("yellowlink")
_own = sorted(f[:-3] for f in os.listdir(os.path.join(CLIENT_DIR, "yellowlink"))
              if f.endswith(".py") and not f.startswith("__"))
hiddenimports += [f"yellowlink.{m}" for m in _own]
for _m in ("raid_gb", "raid_pt", "raid_data", "raid_art", "platinum_exp"):
    if f"yellowlink.{_m}" not in hiddenimports:
        raise SystemExit(f"[yellowlink.spec] yellowlink.{_m} would be missing from the exe")

# Optional bundled assets (icons, fonts, ...) -> <bundle>/assets
ASSETS_DIR = os.path.join(CLIENT_DIR, "assets")
if os.path.isdir(ASSETS_DIR):
    datas.append((ASSETS_DIR, "assets"))

# Raid den spots (our own generated coordinates, dev/raids/gen_spots.py): raid_data.py reads it next to the module
datas.append((os.path.join(CLIENT_DIR, "yellowlink", "raid_spots.json"), "yellowlink"))

# Optional icon
ICON = None
for _candidate in ("assets/icon.ico", "icon.ico"):
    _p = os.path.join(CLIENT_DIR, _candidate)
    if os.path.isfile(_p):
        ICON = _p
        break

a = Analysis(
    [os.path.join(CLIENT_DIR, "run_yellowlink.py")],
    pathex=[CLIENT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy stdlib/3rd-party bits we never use.
    # cv2/PIL are optional PyBoy extras (screen recorder etc.); never bundle
    # them even if they happen to be installed on the build machine.
    # yaml/cryptography get dragged in by optional imports (numpy.__config__, ...).
    # NOTE: do not exclude tkinter; yellowlink/setup_dialog.py uses it.
    # PIL: py-desmume imports it only for screenshot(), which we don't use (nds_core installs a stand-in).
    excludes=[
        "matplotlib", "IPython", "pytest", "cv2", "PIL",
        "yaml", "cryptography",
        "desmume.frontend", "gi", "cairo",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YellowLink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trigger more antivirus false positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
