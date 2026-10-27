"""Platinum resume snapshots across ROM builds (app.py: resume_plan / snapshot_tag / move_aside_other_rom)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from yellowlink import app as appmod, library, platinum, sinnoh_rom  # noqa: E402

VAN = library.PLATINUM_SHA1
CS = next(iter(sinnoh_rom.EXPECTED.values()))[0]          # the shipped Complete Sinnoh build
same = platinum.same_code


def test_resume_plan():
    plan = appmod.resume_plan
    assert plan(None, False, CS, same) == ("load", VAN)                 # no tag = before 0.7.7 = vanilla
    assert plan(CS, False, CS, same) == ("load", None)                  # same build: nothing to refresh
    assert plan(VAN, False, CS, same) == ("load", VAN)
    assert plan(CS, False, VAN, same) == ("load", CS)                   # CS snapshot, plain Platinum now (fallback)
    assert plan("0" * 40, False, CS, same) == ("aside", None)           # a readable unknown SHA1: aside
    # M2: a tag we can't read is NOT a reason to throw the snapshot away: vanilla-compatible, refresh to be safe
    assert plan(None, True, CS, same) == ("load", appmod.UNKNOWN_TAG)
    assert plan(None, True, VAN, same) == ("load", appmod.UNKNOWN_TAG)
    assert plan(appmod.UNKNOWN_TAG, False, VAN, same) == ("load", appmod.UNKNOWN_TAG)
    assert plan("  " + CS + "\n", False, CS, same) == ("load", None)


def test_snapshot_tag_keeps_the_old_build_while_a_refresh_is_pending():
    """M1: a snapshot taken before the wild table / cached FAT were refreshed still holds the OLD build's data in
    RAM: tag it with the old build (or 'unknown'), else the next resume would skip the refresh forever."""
    assert appmod.snapshot_tag(None, CS) == CS
    assert appmod.snapshot_tag(VAN, CS) == VAN
    assert appmod.snapshot_tag(appmod.UNKNOWN_TAG, CS) == appmod.UNKNOWN_TAG
    # and that tag leads to the refresh again on the next start
    assert appmod.resume_plan(appmod.snapshot_tag(VAN, CS), False, CS, same) == ("load", VAN)


def test_move_aside_keeps_the_newest_three(tmp_path):
    """L7"""
    state = tmp_path / "platinum.state"
    for i in range(5):
        state.write_bytes(b"s%d" % i)
        appmod.move_aside_other_rom(state, now=1_800_000_000 + i)
        assert not state.exists()
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) == 3 and all(".other-rom-" in n for n in left)
    assert sorted(p.read_bytes() for p in tmp_path.iterdir()) == [b"s2", b"s3", b"s4"]


def test_released_builds_stay_known():
    """M2: once released, a Complete Sinnoh build must stay in EXPECTED forever (old snapshots stay loadable and
    platinum.VARIANTS keeps App.full for it). Add each new release here."""
    released = {
        "82f977085def05172091ef4b7b71f33695467ac3": ("8abeae7b6039637d0671fc3859a0952f8997054b", 2),   # 0.7.7
    }
    for digest, row in released.items():
        assert sinnoh_rom.EXPECTED.get(digest) == row
        assert platinum.variant_of(row[0]).key == "sinnoh"


def test_try_resume_unreadable_tag_loads_and_refreshes(tmp_path, monkeypatch):
    """M2 through App._try_resume: an OSError reading the tag loads the snapshot and schedules the refresh."""
    import types
    state = tmp_path / "platinum.state"
    state.write_bytes(b"snapshot")
    tag = appmod.rom_tag_path(state)
    tag.write_text(CS)
    real_read = type(tag).read_text

    def broken(self, *a, **k):
        if self == tag:
            raise PermissionError("locked by an antivirus")
        return real_read(self, *a, **k)
    monkeypatch.setattr(type(tag), "read_text", broken)
    loaded = []
    fake = types.SimpleNamespace(
        cfg=types.SimpleNamespace(resume_path=state, get=lambda k, d=None: d), ds=True, rom_sha1=CS,
        G=platinum, emu=types.SimpleNamespace(load_state=lambda f: loaded.append(f.read())),
        _read_sram=lambda: b"", _wild_refresh=None, game_id="platinum")
    assert appmod.App._try_resume(fake) is True
    assert loaded == [b"snapshot"] and state.exists() and fake._wild_refresh == appmod.UNKNOWN_TAG
