"""Every module the app loads on demand (importlib) must be in app.ON_DEMAND_MODULES, which the release build's
selftest imports inside the exe (0.7.3-0.7.10 shipped without raid_gb/raid_pt/raid_data/raid_art/platinum_exp)."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from yellowlink import app  # noqa: E402

PKG = os.path.join(os.path.dirname(__file__), "..", "yellowlink")


def test_every_on_demand_import_is_selftested():
    found = set()
    for f in os.listdir(PKG):
        if f.endswith(".py"):
            src = open(os.path.join(PKG, f), encoding="utf-8").read()
            found |= set(re.findall(r'import_module\(\s*f?"\.(\w+)"', src))
            found |= set(re.findall(r'"raid_pt" if [^"]+ else "(\w+)"', src)) | (
                {"raid_pt"} if '"raid_pt" if' in src else set())
    assert found, "no on-demand imports found (pattern changed?)"
    assert found <= set(app.ON_DEMAND_MODULES), found - set(app.ON_DEMAND_MODULES)


def test_selftest_modules_imports_them():
    assert "modules ok" in app.selftest_modules()


def test_spec_bundles_every_module():
    spec = open(os.path.join(PKG, "..", "yellowlink.spec"), encoding="utf-8").read()
    assert "_own = sorted(" in spec and "hiddenimports += [f\"yellowlink.{m}\" for m in _own]" in spec
