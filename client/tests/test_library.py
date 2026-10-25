import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import library  # noqa: E402


class FakeCfg(dict):
    def save(self):
        pass


def test_unknown_rom_rejected(tmp_path):
    rom = tmp_path / "x.gb"
    rom.write_bytes(b"not a pokemon game")
    assert library.identify(rom) is None
    lib = library.Library(FakeCfg(), tmp_path)
    try:
        lib.add_rom(str(rom))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_known_games_cover_regions():
    ids = {g.id for g in library.KNOWN_GAMES.values()}
    for _, _, games in library.REGIONS:
        assert set(games) <= ids
    assert library.GAMES_BY_ID["yellow"].full_support


def test_add_and_switch(tmp_path, monkeypatch):
    rom = tmp_path / "game.gb"
    rom.write_bytes(b"\x00" * 64)
    monkeypatch.setitem(library.KNOWN_GAMES, library.sha1_file(rom), library.GAMES_BY_ID["red"])
    cfg = FakeCfg()
    lib = library.Library(cfg, tmp_path / "app")
    info = lib.add_rom(str(rom))
    assert info.id == "red" and "red" in lib.games
    assert (tmp_path / "app" / "roms" / "red.gb").exists()
    lib.set_current("red")
    assert lib.current == "red" and cfg["rom_path"].endswith("red.gb")
    assert lib.save_path().name == "red.sav" and lib.resume_path().name == "red.state"
    json.dumps(cfg)  # config stays serialisable
