"""Pokémon Platinum must never crash-loop the app: every way the DS core can fail to start becomes DSUnavailable,
the App closes its sound device, and _main falls back to a Game Boy game (or explains it, with no other game).
Also: exec hooks don't keep old Apps alive. No ROM or real emulator needed (fakes)."""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from yellowlink import nds_core  # noqa: E402


class FakeLib:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):          # every desmume_* function: record the call, return 0
        def fn(*a):
            self.calls.append((name, a))
            return 0
        return fn


class FakeEmu:
    def __init__(self, open_error=None, import_ok=True):
        self.lib = FakeLib()
        self.open_error, self.import_ok = open_error, import_ok
        self.paused = self.closed = False
        self.backup = types.SimpleNamespace(import_file=lambda p, size=0: self.import_ok,
                                            export_file=lambda p: False)

    def open(self, path):
        if self.open_error:
            raise self.open_error

    def volume_set(self, v):
        pass

    def pause(self):
        self.paused = True

    def close(self):
        self.closed = True


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(nds_core, "_EMU", None)
    monkeypatch.setattr(nds_core, "_ACTIVE", None)
    monkeypatch.setattr(nds_core, "_HOOK_FN", {})
    monkeypatch.setattr(nds_core, "_HOOK_CFUNC", {})
    return monkeypatch


def test_missing_desmume_package(fresh):
    fresh.setitem(sys.modules, "desmume.emulator", None)       # import fails (not installed / not bundled)
    with pytest.raises(nds_core.DSUnavailable):
        nds_core.get_emulator()


def test_library_fails_to_load(fresh):
    import desmume.emulator as de

    def boom(*a, **k):
        raise OSError("libdesmume.dll: The specified module could not be found")
    fresh.setattr(de, "DeSmuME", boom)
    with pytest.raises(nds_core.DSUnavailable):
        nds_core.get_emulator()


@pytest.mark.parametrize("emu", [FakeEmu(open_error=RuntimeError("Unable to open ROM file.")),
                                 FakeEmu(import_ok=False)], ids=["rom refused", "save refused"])
def test_rom_or_save_refused(fresh, tmp_path, emu):
    fresh.setattr(nds_core, "get_emulator", lambda: emu)
    fresh.setattr(nds_core, "_staging_dir", lambda: str(tmp_path / "stage"))
    (tmp_path / "stage").mkdir()
    with pytest.raises(nds_core.DSUnavailable):
        nds_core.NDSCore(str(tmp_path / "platinum.nds"), None, 50)
    assert nds_core._ACTIVE is None
    assert emu.paused and not (tmp_path / "stage").exists()       # emulator left idle, staging folder gone


def test_hooks_hold_no_reference_after_close(fresh, tmp_path):
    import gc
    import weakref
    emu = FakeEmu()
    fresh.setattr(nds_core, "get_emulator", lambda: emu)
    fresh.setattr(nds_core, "_staging_dir", lambda: str(tmp_path / "stage"))
    (tmp_path / "stage").mkdir()

    class FakeApp:
        hits = 0

        def on_hook(self):
            self.hits += 1

    app = FakeApp()
    core = nds_core.NDSCore(str(tmp_path / "platinum.nds"), None, 50)
    core.hook_register(0x0203D754, app.on_hook)
    tramp = nds_core._HOOK_CFUNC[0x0203D754]
    tramp(0x0203D754, 2)                                   # what libdesmume calls
    assert app.hits == 1
    (tmp_path / "stage").mkdir(exist_ok=True)
    core.close(blank_battery=False)
    tramp(0x0203D754, 2)                                   # a stale call after close does nothing
    assert app.hits == 1
    ref = weakref.ref(app)
    del app, core
    gc.collect()
    assert ref() is None                                  # nothing global keeps the old App alive
    # a new core re-uses the same trampoline (no new ctypes callback per travel)
    (tmp_path / "stage").mkdir(exist_ok=True)
    core2 = nds_core.NDSCore(str(tmp_path / "platinum.nds"), None, 50)
    core2.hook_register(0x0203D754, lambda: None)
    assert nds_core._HOOK_CFUNC[0x0203D754] is tramp
    core2.close(blank_battery=False)


def _cfg_with(tmp_path, monkeypatch, games):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    from yellowlink.config import Config
    cfg = Config()
    paths = {}
    for g in games:
        p = tmp_path / f"{g}.rom"
        p.write_bytes(b"x")
        paths[g] = str(p)
    cfg.update(games=paths, current_game="platinum", name="Ilias", server_url="ws://1.2.3.4:8765")
    return cfg


def test_fallback_to_a_game_boy_game(tmp_path, monkeypatch):
    from yellowlink import app as appmod
    cfg = _cfg_with(tmp_path, monkeypatch, ["platinum", "yellow"])
    assert appmod._ds_fallback(cfg, None, nds_core.DSUnavailable("no DLL")) is True
    assert cfg["current_game"] == "yellow" and "no DLL" in cfg["_ds_error"]


def test_platinum_only_player_gets_a_window_not_a_crash(tmp_path, monkeypatch):
    from yellowlink import app as appmod, setup_dialog
    cfg = _cfg_with(tmp_path, monkeypatch, ["platinum"])
    shown = []
    monkeypatch.setattr(appmod, "show_report_window", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(setup_dialog, "run_setup", lambda c, first_run: False)       # player closes settings
    assert appmod._ds_fallback(cfg, None, nds_core.DSUnavailable("no DLL")) is False
    assert shown and "Platinum" in shown[0]


def test_failed_app_start_closes_sound_and_raises_dsunavailable(tmp_path, monkeypatch):
    import pygame
    from yellowlink import app as appmod, audio
    cfg = _cfg_with(tmp_path, monkeypatch, ["platinum"])
    cfg["games"]["platinum"] = cfg["rom_path"] = str(tmp_path / "platinum.rom")
    monkeypatch.setitem(appmod.library.KNOWN_GAMES, appmod.library.sha1_file(cfg["rom_path"]),
                        appmod.library.GAMES_BY_ID["platinum"])
    closed = []
    monkeypatch.setattr(audio.AudioOut, "close", lambda self: closed.append(True))
    monkeypatch.setattr(appmod.App, "_start_nds", lambda self: (_ for _ in ()).throw(ValueError("bad state")))
    with pytest.raises(nds_core.DSUnavailable):
        appmod.App(cfg)
    assert closed
    pygame.quit()


def test_complete_sinnoh_path_stays_on_the_app_not_in_the_config(tmp_path, monkeypatch):
    """H2: the built ROM (roms/platinum-sinnoh.nds) is what the App runs (self.rom_path), but cfg['rom_path'] - saved
    and shown in Settings - stays the player's own vanilla file."""
    import pygame
    from yellowlink import app as appmod
    cfg = _cfg_with(tmp_path, monkeypatch, ["platinum"])
    vanilla = str(tmp_path / "platinum.rom")
    cfg["games"]["platinum"] = cfg["rom_path"] = vanilla
    built = tmp_path / "platinum-sinnoh.nds"
    built.write_bytes(b"built")
    monkeypatch.setitem(appmod.library.KNOWN_GAMES, appmod.library.sha1_file(vanilla),
                        appmod.library.GAMES_BY_ID["platinum"])
    monkeypatch.setattr(appmod.App, "_complete_sinnoh_rom", lambda self, v: str(built))
    seen = {}

    def start(self):
        seen.update(rom_path=self.rom_path, cfg=self.cfg["rom_path"], sha=self.rom_sha1)
        raise ValueError("stop here")
    monkeypatch.setattr(appmod.App, "_start_nds", start)
    with pytest.raises(nds_core.DSUnavailable):
        appmod.App(cfg)
    assert seen["rom_path"] == str(built) and seen["cfg"] == vanilla
    assert seen["sha"] == appmod.library.sha1_file(str(built))
    assert cfg["rom_path"] == vanilla
    pygame.quit()


def test_failed_build_is_remembered_for_the_process(tmp_path, monkeypatch):
    """H1: a failed Complete Sinnoh build -> plain Platinum, and sinnoh_rom.BUILD_FAILED tells Soul Link partners
    (also after travelling to Yellow/Crystal in the same process)."""
    import types as _t
    from yellowlink import app as appmod, sinnoh_rom
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", None)
    monkeypatch.delenv("YL_PT_ROM", raising=False)

    def boom(*a, **k):
        raise sinnoh_rom.BuildError("Making Complete Sinnoh went wrong (test).")
    monkeypatch.setattr(sinnoh_rom, "ensure", boom)
    fake = _t.SimpleNamespace(lib=_t.SimpleNamespace(dir=tmp_path), sinnoh_problem=None,
                              _building_notice=lambda: None)
    assert appmod.App._complete_sinnoh_rom(fake, "/roms/platinum.nds") == "/roms/platinum.nds"
    assert "went wrong" in fake.sinnoh_problem and sinnoh_rom.BUILD_FAILED == fake.sinnoh_problem


def test_build_fallback_is_told(tmp_path, monkeypatch):
    """M4: the new build failed, an older verified one runs: not a failure (no BUILD_FAILED), but the player hears it."""
    import types as _t
    from yellowlink import app as appmod, sinnoh_rom
    monkeypatch.setattr(sinnoh_rom, "BUILD_FAILED", None)
    monkeypatch.delenv("YL_PT_ROM", raising=False)

    def old_build(*a, **k):
        sinnoh_rom.FALLBACK = (1, "disk full")
        return "/roms/platinum-sinnoh.nds", "ab" * 20
    monkeypatch.setattr(sinnoh_rom, "ensure", old_build)
    monkeypatch.setattr(sinnoh_rom, "FALLBACK", None)
    fake = _t.SimpleNamespace(lib=_t.SimpleNamespace(dir=tmp_path), sinnoh_problem=None, sinnoh_note=None,
                              _building_notice=lambda: None)
    assert appmod.App._complete_sinnoh_rom(fake, "/roms/platinum.nds") == "/roms/platinum-sinnoh.nds"
    assert fake.sinnoh_problem is None and sinnoh_rom.BUILD_FAILED is None
    assert "disk full" in fake.sinnoh_note and "previous" in fake.sinnoh_note


def test_frozen_start_removes_old_shared_battery_files(fresh, tmp_path):
    """L3: the exe runs roms/platinum-sinnoh.nds now; DeSmuME's shared battery of the plain file (platinum.dsv,
    from 0.7.6 or a plain-Platinum fallback) is removed too, so no stray save stays next to the exe."""
    fresh.setattr(nds_core, "get_emulator", lambda: FakeEmu())
    fresh.setattr(nds_core, "_staging_dir", lambda: str(tmp_path / "stage"))
    fresh.setattr(sys, "frozen", True, raising=False)
    fresh.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "stage").mkdir()
    dsv_dir = tmp_path / "cfg" / "desmume"
    dsv_dir.mkdir(parents=True)
    for n in ("platinum.dsv", "platinum-sinnoh.dsv", "yellow.dsv"):
        (dsv_dir / n).write_bytes(b"old save")
    core = nds_core.NDSCore(str(tmp_path / "roms" / "platinum-sinnoh.nds"), None, 50)
    assert sorted(p.name for p in dsv_dir.iterdir()) == ["yellow.dsv"]
    (tmp_path / "stage").mkdir(exist_ok=True)
    core.close(blank_battery=False)
