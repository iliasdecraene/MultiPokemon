"""Run another e2e script with Exp Share All installed and FORCED on everywhere (even live and practice battles).

    python dev/e2e/expshare_forced.py dev/e2e/pvp_e2e.py
    python dev/e2e/expshare_forced.py --off dev/e2e/soullink_crystal.py     (installed, forced OFF: baseline)
    python dev/e2e/expshare_forced.py --none dev/e2e/soullink_crystal.py    (not installed at all)

Patches App.exp_share_on to always return True, counts what the hooks did and checks that a link battle never got
the rule (the games give no exp there; the hooks must stay passive so both sides stay in sync). The wrapped script's
own PASS/FAIL output and exit code are kept; this adds an "expshare:" summary line and fails if a link battle was
touched or if no hooks were installed.
"""
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, _HERE)

from yellowlink import app as appmod  # noqa: E402
from yellowlink import expshare_gb  # noqa: E402

STATS = {"installed": 0, "decide": 0, "applied": 0, "link_decide": 0, "link_applied": 0}


def _instrument(cls, link_active):
    orig_init, orig_decide = cls.__init__, cls.on_decide

    def init(self, *a, **kw):
        orig_init(self, *a, **kw)
        STATS["installed"] += 1

    def decide(self):
        link = link_active(self.m)
        orig_decide(self)
        STATS["decide"] += 1
        STATS["applied"] += self.phase != 0
        if os.environ.get("YL_EXPSHARE_TRACE"):
            print(f"expshare trace: {type(self).__name__} emu {id(self.pyboy):x} applied={self.phase != 0} "
                  f"participants={self.participants:06b} shared={self.shared:06b}", flush=True)
            if isinstance(self, expshare_gb.CrystalExpShare):
                m = self.m
                print(f"   enemy {m[1, 0xD206]} L{m[1, 0xD213]} hp {m[1, 0xD216] << 8 | m[1, 0xD217]} battlemon "
                      f"{m[0xC62C]} hp {m[0xC63C] << 8 | m[0xC63D]} party {[m[1, 0xDCD8 + i] for i in range(6)]}",
                      flush=True)
        if link:
            STATS["link_decide"] += 1
            STATS["link_applied"] += self.phase != 0
    cls.__init__, cls.on_decide = init, decide


_instrument(expshare_gb.YellowExpShare, lambda m: m[expshare_gb.YellowExpShare.LINK_STATE] == 4)
_instrument(expshare_gb.CrystalExpShare, lambda m: m[expshare_gb.CrystalExpShare.LINK_MODE] != 0)
FORCE = "--off" not in sys.argv
NONE = "--none" in sys.argv
args = [a for a in sys.argv[1:] if a not in ("--off", "--none")]
appmod.App.exp_share_on = lambda self: FORCE
if NONE:
    appmod.App._install_exp_share = lambda self: None

script = os.path.abspath(args[0])
sys.argv = [script] + args[1:]
sys.path.insert(0, os.path.dirname(script))
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
except BaseException:  # noqa: BLE001 - report, then fail
    import traceback
    traceback.print_exc()
    code = 1
ok = (NONE or STATS["installed"] > 0) and STATS["link_applied"] == 0
how = "not installed" if NONE else ("forced on" if FORCE else "forced off")
print(f"expshare ({how}): {STATS} -> {'OK' if ok else 'PROBLEM'}", flush=True)
sys.exit(code or (0 if ok else 1))
