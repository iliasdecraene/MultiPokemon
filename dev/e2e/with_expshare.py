"""Run another Platinum test script with Exp. Share All installed (rule ON) on every emulator it creates.

    python dev/e2e/with_expshare.py dev/platinum/verify.py
    python dev/e2e/with_expshare.py dev/e2e/platinum_boot.py

Patches nds_core.NDSCore (the app's core) and dev/platinum/ds.DS (the dev driver) so that each new instance gets
platinum_exp.install_exp_share(core, lambda: True) right after it is created; then runs the script unchanged.
Used to check that the hooks don't disturb anything else (until app.py installs them itself).
"""
import os
import runpy
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))

from yellowlink import nds_core, platinum_exp  # noqa: E402

INSTALLED = []


def _wrap(cls):
    orig = cls.__init__

    def __init__(self, *a, **k):
        orig(self, *a, **k)
        INSTALLED.append(platinum_exp.install_exp_share(self, lambda: True))
        print(f"[with_expshare] Exp. Share All installed on {cls.__name__} #{len(INSTALLED)}", flush=True)
    cls.__init__ = __init__


_wrap(nds_core.NDSCore)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import ds  # noqa: E402
_wrap(ds.DS)

script = os.path.abspath(sys.argv[1])
sys.argv = [script] + sys.argv[2:]
sys.path.insert(0, os.path.dirname(script))
runpy.run_path(script, run_name="__main__")
