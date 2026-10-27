"""Which Sinnoh ROM the Platinum dev harnesses run (env YL_PT_ROM), so one script verifies both:

    YL_PT_ROM=vanilla  (default)  dev/states/platinum.nds            (Pokémon Platinum US rev 0)
    YL_PT_ROM=sinnoh              dev/states/platinum-sinnoh.nds     (Complete Sinnoh, built from platinum.nds by
                                  dev/sinnoh/build_rom.py with client/yellowlink/sinnoh_complete.py; with
                                  YL_SINNOH_SPEC=test: the tiny dev/sinnoh/test_spec.py -> platinum-sinnoh-test.nds)

The fixtures (dev/states/pt_*.dsst, pt_saved.sav) are the vanilla ones in both cases: a vanilla savestate loads into
the Complete Sinnoh ROM (same code; checked by dev/sinnoh/verify.py) - that is also what a player's old resume
snapshot does. Importing this module also tells YellowLink's App (in the same process) which ROM to run: the app
reads YL_PT_ROM too (no YL_PT_ROM = Complete Sinnoh, the normal player setup), so harnesses that start the app
default to vanilla like the ds.py ones.
YL_PLATINUM_ROM (an explicit ROM path, older scripts/tests) still wins for ds.py.
"""
import atexit
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
STATES = os.environ.get("YL_STATES", os.path.normpath(os.path.join(_HERE, "..", "states")))
VANILLA_ROM = os.path.join(STATES, "platinum.nds")

NAME = (os.environ.get("YL_PT_ROM", "vanilla").strip().lower() or "vanilla")
if NAME in ("complete", "sinnoh_complete", "complete_sinnoh"):
    NAME = "sinnoh"
if NAME not in ("vanilla", "sinnoh"):
    raise SystemExit("YL_PT_ROM must be vanilla or sinnoh (got %r)" % NAME)
os.environ["YL_PT_ROM"] = NAME          # the App in this process follows the harness
SINNOH = NAME == "sinnoh"


def _sinnoh_rom() -> str:
    sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "client")))
    sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "sinnoh")))
    import build_rom
    return build_rom.ensure_dev_rom("test" if os.environ.get("YL_SINNOH_SPEC") == "test" else "auto")


ROM = os.environ.get("YL_PLATINUM_ROM") or (_sinnoh_rom() if SINNOH else VANILLA_ROM)


def label() -> str:
    return "Complete Sinnoh (%s)" % os.path.basename(ROM) if SINNOH else "Platinum (US rev 0)"


def rom_sha1() -> str:
    sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "client")))
    from yellowlink import library
    return library.sha1_file(ROM)


def sinnoh_caps() -> list:
    """What a client running this ROM puts into `library.challenge_rules` for Soul Link pairing (SPEC.md section 11):
    ["sinnoh_v<spec version>"] on Complete Sinnoh, [] on vanilla. Fake partners in the harnesses send the same, like a
    friend on the same YellowLink version would."""
    if not SINNOH:
        return []
    sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "client")))
    from yellowlink import platinum
    v = platinum.variant_of(rom_sha1())
    return ["sinnoh_v%s" % v.spec_version] if v and v.spec_version is not None else []


def _remove_rom_copies(folder: str):
    """The app copies the 128 MB ROM into its data folder and builds another 128 MB Complete Sinnoh file next to it:
    in a harness's temporary app folder those are deleted at exit (screenshots/logs stay), or a day of e2e runs fills
    the disk. Only *.nds / *.part files below a folder inside the system temp dir; YL_KEEP_ROM_COPIES=1 keeps them."""
    for root, _dirs, files in os.walk(folder):
        for n in files:
            if n.endswith(".nds") or n.endswith(".part"):
                try:
                    os.remove(os.path.join(root, n))
                except OSError:
                    pass


def _register_cleanup():
    cfg = os.environ.get("XDG_CONFIG_HOME")
    if not cfg or os.environ.get("YL_KEEP_ROM_COPIES"):
        return
    tmp = os.path.realpath(tempfile.gettempdir())
    real = os.path.realpath(cfg)
    if real.startswith(tmp + os.sep):          # a harness's own temporary app folder, never the repo or a real one
        atexit.register(_remove_rom_copies, real)


_register_cleanup()
