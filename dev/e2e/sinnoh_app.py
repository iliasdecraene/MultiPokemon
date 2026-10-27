"""Complete Sinnoh inside YellowLink (the app), end to end on the real ROM (SPEC.md section 11).

  1. first start: the app makes roms/platinum-sinnoh.nds from the player's vanilla file (original untouched), runs
     it with every feature (App.full) and advertises sinnoh_v<spec> for Soul Link pairing
  2. a 0.7.6 resume snapshot (vanilla, no .rom tag) resumes in Complete Sinnoh; the loaded wild table is replaced by
     the ROM's at the first safe frame; the next snapshot is tagged with the Complete Sinnoh SHA1
  3. a snapshot from an unknown ROM is put aside (.other-rom) and the battery save is used
  4. the Mystery Gift events arrive (toast + bag) once the save meets each condition (TEST SHORTCUTS: RAM flags):
     National Dex -> Member Card, Oak's Letter, Secret Key; + Dialga and Palkia caught -> Azure Flute;
     the shared 2-hour swarm is written into the save and announced once swarms are on (TEST SHORTCUT: swarmEnabled)
  5. a second start only checks the file; a damaged copy is made again
  6. if it can't be made the app plays plain Platinum and says so; YL_PT_ROM=vanilla runs vanilla (dev)

Run: YL_SINNOH_SPEC=test python dev/e2e/sinnoh_app.py   (or with the real spec once its SHA1 is recorded)
Needs dev/states/{platinum.nds, pt_saved.sav, pt_sl_route202.dsst, pt_town.dsst}. No server needed (offline).
"""
import os, sys, shutil, tempfile, pathlib, hashlib
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_sn_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ.setdefault("YL_PT_ROM", "sinnoh")
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402
print("Sinnoh ROM:", ptrom.label())
import pygame  # noqa: E402
pygame.quit = lambda: None
from yellowlink.config import Config, app_dir  # noqa: E402
from yellowlink import app as appmod, library, sinnoh_events, sinnoh_rom, sinnoh_swarm, encdata, ndsrom  # noqa: E402

PT_ROM = ptrom.VANILLA_ROM
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)
    return ok


def sha(p):
    return hashlib.sha1(pathlib.Path(p).read_bytes()).hexdigest()


cfg = Config(tmp / "appdata" / "cfg.json")
cfg.update(name="Lucas", server_url="ws://127.0.0.1:1", room_key="x", seen_help=True, ds_scale=1)
lib = library.Library(cfg, app_dir())
lib.add_rom(PT_ROM)
lib.set_current("platinum")
vanilla_copy = app_dir() / "roms" / "platinum.nds"
SN = app_dir() / "roms" / sinnoh_rom.FILE_NAME
spec = sinnoh_rom.load_spec()
WANT = sinnoh_rom.expected_sha1(spec)
print("spec", spec.__name__, "v%s" % spec.SPEC_VERSION, "expected", WANT)


def run(a, n, cond=None):
    for _ in range(n):
        a.handle_events()
        a.step()
        if cond and cond():
            return True
    return False


def start(dev_state=None):
    if dev_state:
        os.environ["YL_DEV_LOAD_STATE"] = dev_state
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    return a


# 1 ---------------------------------------------------------------------------------------------------------
shutil.copy2(os.path.join(STATES, "pt_saved.sav"), cfg.save_path)
a = start()
check("built roms/platinum-sinnoh.nds on first start", SN.exists() and sha(SN) == WANT, f"{SN.exists()}")
check("the player's own ROM copy is untouched", sha(vanilla_copy) == library.PLATINUM_SHA1)
check("the app runs the Complete Sinnoh file", a.cfg["rom_path"] == str(SN), a.cfg["rom_path"])
check("App.full + complete_sinnoh", a.full and a.complete_sinnoh and a.rom_sha1 == WANT)
check("Soul Link capability sinnoh_v%s" % spec.SPEC_VERSION, a._sinnoh_caps() == ["sinnoh_v%s" % spec.SPEC_VERSION])
check("event unlocker + swarm rotation running", a.sinnoh_events is not None and a.sinnoh_swarm is not None)
run(a, 900, lambda: a.game.is_safe())
check("CONTINUE'd into the battery save (fast continue)", a.game.in_game() and a.game.is_safe())
a.shutdown()
tag = cfg.resume_path.with_suffix(".rom")
check("shutdown snapshot tagged with the ROM's SHA1", tag.exists() and tag.read_text() == WANT)

# 2 ---------------------------------------------------------------------------------------------------------
shutil.copy2(os.path.join(STATES, "pt_sl_route202.dsst"), cfg.resume_path)     # a vanilla snapshot (0.7.6)
tag.unlink()
a = start()
rom = ndsrom.NDSRom(str(SN))
member = rom.narc(encdata.NARC_PATH)[141]
vanilla_member = ndsrom.NDSRom(PT_ROM).narc(encdata.NARC_PATH)[141]
check("resumed the vanilla snapshot in Complete Sinnoh", a.game.map_id() == 343 and a._wild_refresh is not None)
run(a, 600, lambda: a._wild_refresh is None)
check("Route 202's table in RAM = the Complete Sinnoh ROM's%s" % (" (edited)" if member != vanilla_member else ""),
      a._wild_refresh is None and a.game.wild_table() == member)
with open(SN, "rb") as f:
    hdr = f.read(0x50)
    fat_off, fat_size = __import__("struct").unpack_from("<II", hdr, 0x48)
    f.seek(fat_off)
    sn_fat = f.read(fat_size)
fa = a.game.cached_fat_addr(len(sn_fat))
check("the game's cached FAT = the Complete Sinnoh ROM's (moved Pokédex area file)",
      fa is not None and a.nds.read(fa, len(sn_fat)) == sn_fat)
check("the autosave snapshot is tagged", a.autosave(force=True) and tag.read_text() == WANT)
a.shutdown()

# 3 ---------------------------------------------------------------------------------------------------------
tag.write_text("0" * 40)
a = start()
other = cfg.resume_path.with_suffix(".other-rom")
check("snapshot of an unknown ROM put aside, battery used instead", other.exists() and a._wild_refresh is None)
run(a, 900, lambda: a.game.is_safe())
check("... and the game CONTINUE'd from the save", a.game.in_game())
a.shutdown()

# 4 ---------------------------------------------------------------------------------------------------------
a = start(os.path.join(STATES, "pt_town.dsst"))
run(a, 300, lambda: a.game.is_safe())
def gifts():
    return [m[1] for m in a.chat_log if "Mystery Gift" in m[1]]


run(a, 200)
check("no Mystery Gift without the National Dex", gifts() == [])
body = a.game.save_body()
a.nds.write(body + sinnoh_events.PAGE_POKEDEX + sinnoh_events.POKEDEX_NATIONAL, b"\x01")   # TEST SHORTCUT
run(a, 400, lambda: len(gifts()) >= 3)
check("National Dex: Member Card, Oak's Letter, Secret Key (toast + chat)", len(gifts()) == 3 and
      any("Mystery Gift" in t[0] for t in a.toasts) and not any("AZURE" in g for g in gifts()), gifts())
flags = body + sinnoh_events.PAGE_VARS_FLAGS + sinnoh_events.VF_FLAGS
for n in (sinnoh_events.FLAG_CAUGHT_DIALGA, sinnoh_events.FLAG_CAUGHT_PALKIA):     # TEST SHORTCUT
    a.nds.write(flags + n // 8, bytes([a.nds.read(flags + n // 8, 1)[0] | 1 << n % 8]))
run(a, 400, lambda: len(gifts()) >= 4)
check("Dialga + Palkia caught: the Azure Flute", len(gifts()) == 4 and "AZURE" in gifts()[-1], gifts())
check("the key items + vars are in the save", all(sinnoh_events.given(a.game, e) for e in sinnoh_events.EVENTS))
run(a, 300)
check("not given twice", len(gifts()) == 4)
# swarms: the shared 2-hour pick is in the save; announced once swarms are on
cur = sinnoh_swarm.current()
check("swarmDaily = this 2-hour slot's swarm (%d)" % cur["index"], sinnoh_swarm.swarm_index(a.game) == cur["index"])
check("swarms off in this save: no swarm message", not [m for m in a.chat_log if "Swarm now" in m[1]])
a.nds.write(body + sinnoh_swarm.PAGE_SPECIAL_ENCOUNTERS + sinnoh_swarm.SE_SWARM_ENABLED, b"\x01")  # TEST SHORTCUT
run(a, 300, lambda: any("Swarm now" in m[1] for m in a.chat_log))
msgs = [m[1] for m in a.chat_log if "Swarm now" in m[1]]
check("swarms on: 'Swarm now: <species> on <place> (until HH:MM)' in chat + toast", len(msgs) == 1 and
      any("Swarm now" in t[0] for t in a.toasts) and "?" not in msgs[0] and " on " in msgs[0], msgs)
print("     ", msgs)
a.shutdown()

# 5 ---------------------------------------------------------------------------------------------------------
calls = []
real_build = sinnoh_rom.build
sinnoh_rom.build = lambda *x, **k: calls.append(1) or real_build(*x, **k)
a = start()
check("second start: no rebuild", calls == [])
a.shutdown()
data = bytearray(SN.read_bytes())
data[0x1000] ^= 0xFF
SN.write_bytes(bytes(data))
a = start()
check("damaged copy made again", calls == [1] and sha(SN) == WANT)
a.shutdown()
sinnoh_rom.build = real_build

# 6 ---------------------------------------------------------------------------------------------------------
real_ensure = sinnoh_rom.ensure


def broken(*x, **k):
    raise sinnoh_rom.BuildError("Making Complete Sinnoh went wrong (test).")


sinnoh_rom.ensure = broken
a = start()
check("can't be made: plain Platinum, still full features", a.cfg["rom_path"] == str(vanilla_copy) and a.full
      and not a.complete_sinnoh and a.sinnoh_events is None and a.sinnoh_swarm is None)
check("... and the player is told", any("plain Platinum" in m[1] for m in a.chat_log))
check("... no sinnoh capability (no Soul Link with Complete Sinnoh players)", a._sinnoh_caps() == [])
a.shutdown()
sinnoh_rom.ensure = real_ensure
os.environ["YL_PT_ROM"] = "vanilla"
a = start()
check("YL_PT_ROM=vanilla (dev): the vanilla file", a.cfg["rom_path"] == str(vanilla_copy) and not a.complete_sinnoh)
a.shutdown()

print("\n%d failed" % len(FAILS))
for f in FAILS:
    print("  FAILED:", f)
sys.exit(1 if FAILS else 0)
