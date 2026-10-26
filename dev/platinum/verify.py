"""Verify the Pokémon Platinum memory map + client/yellowlink/platinum.py against the real ROM (no server needed).

Needs dev/states/platinum.nds + the pt_* fixtures (see dev/platinum/STATUS.md / MEMORY_MAP.md).
Prints one PASS/FAIL line per check and exits non-zero if any check failed.   Usage: python dev/platinum/verify.py

Runs in a private XDG_CONFIG_HOME so DeSmuME's own battery file (~/.config/desmume/<rom>.dsv) is not touched.
"""
import hashlib
import os
import struct
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="ylpt_verify_")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "client"))

from ds import DS, ROM, KEYS  # noqa: E402
from desmume.controls import keymask  # noqa: E402
import gen4crypt as G  # noqa: E402
from yellowlink import platinum as P  # noqa: E402

STATES = os.path.join(HERE, "..", "states")
RESULTS = []


def ok(cond, what):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    return bool(cond)


def st(name):
    return os.path.join(STATES, name)


class Rig:
    def __init__(self):
        self.d = DS()
        self.pm = P.PlatinumMemory(self.d)

    def load(self, name):
        self.d.load(st(name))
        self.d.run(2)

    def hold(self, keys, frames):
        ms = [keymask(KEYS[k]) for k in keys]
        for m in ms:
            self.d.emu.input.keypad_add_key(m)
        out = []
        for _ in range(frames):
            self.d.run(1)
            out.append(self.pm.position())
        for m in ms:
            self.d.emu.input.keypad_rm_key(m)
        return out

    def wait(self, cond, frames):
        for i in range(frames):
            if cond():
                return i
            self.d.run(1)
        return None


class CoreShim:
    """NDSCore-like interface (button_press/button_release/tick) over ds.DS, for fast_continue."""
    def __init__(self, d):
        self.d = d

    def button_press(self, k):
        self.d.emu.input.keypad_add_key(keymask(KEYS[k]))

    def button_release(self, k):
        self.d.emu.input.keypad_rm_key(keymask(KEYS[k]))

    def tick(self, count=1, render=True):
        self.d.run(count)

    def __getattr__(self, name):          # r8/r16/r32/read/write...
        return getattr(self.d, name)


# ---------------------------------------------------------------------------------------------------------
def check_rom(rig):
    h = hashlib.sha1()
    with open(ROM, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    ok(h.hexdigest() == P.PLATINUM_SHA1, "ROM SHA1 = PLATINUM_SHA1 (Platinum USA rev 0)")
    d = rig.d
    # static code/data in the ARM9 binary (present from boot)
    labels = d.read(P.MAP_HEADERS, 24 * P.NUM_MAP_HEADERS)
    ok(all(labels[24 * i + 18] == P.MAP_LOCATION[i] for i in range(P.NUM_MAP_HEADERS)),
       "sMapHeaders @%08X: mapLabelTextID of all 593 maps = MAP_LOCATION table" % P.MAP_HEADERS)
    ok(P.area_of(411) == "twinleaf_town" and P.area_of(415) == "twinleaf_town" and P.area_of(342) == "route_201"
       and P.area_name("sandgem_town") == "Sandgem Town" and P.area_of(420) == "sandgem_town",
       "area_of/area_name: 411/415 Twinleaf Town, 342 Route 201, 420 Sandgem Town")
    tmpl = struct.unpack("<4I", d.read(0x020EA2C8, 16))
    ok(tmpl == (0x021D0D81, 0x021D0DED, 0x021D0E59, P.OVERLAY_PC_BOXES),
       "PC box app template @020EA2C8 = BoxAppMan_Init/Main/Exit, overlay 19 (pc_boxes)")
    code = struct.unpack("<4H2I", d.read(P.HOOK_PC_OPEN, 16))
    ok(code == (0x4B01, 0x1C0A, 0x4901, 0x4718, 0x0203CD85, 0x020EA2C8),
       "HOOK_PC_OPEN @%08X = FieldSystem_OpenPokemonStorage (ldr r3 StartChildProcess; ldr r1 template; bx r3)"
       % P.HOOK_PC_OPEN)
    ok(struct.unpack("<4I", d.read(0x020EA358, 16))[3] == P.OVERLAY_BATTLE, "battle app template @020EA358: overlay 16")


def check_save_layout(rig, name):
    pm, d = rig.pm, rig.d
    sd = pm.save_data()
    ok(sd is not None and d.r32(0x02101D38) == 0xFFFFFFFF and d.r32(0x02101D3C) == 0,
       "%s: sApplication.args {unk_00=-1, error=0, saveData=%08X} at 0x02101D38" % (name, sd or 0))
    pages = {}
    for i in range(38):
        pid, size, off, _chk, blk = struct.unpack("<IIIHH", d.read(sd + 0x20024 + 16 * i, 16))
        pages[pid] = (off, size, blk)
    ok(pages[1][0] == P.PAGE_PLAYER and pages[2][0] == P.PAGE_PARTY and pages[6][0] == P.PAGE_FIELD_PLAYER
       and pages[37][0] == P.PAGE_PC_BOXES and pages[37][2] == 1 and pages[37][1] == 0x121D0,
       "%s: SaveData page table (+0x20024): player 0x64, party 0x98, field 0x1280, PC boxes 0xCF2C (block 1)" % name)
    return sd


def check_static_state(rig, name, exp):
    rig.load(name)
    pm = rig.pm
    sd = check_save_layout(rig, name)
    fs = pm.field_system()
    ok(fs is not None and rig.d.r32(fs + P.FS_LOCATION) == sd + P.SAVE_BODY + P.PAGE_FIELD_PLAYER,
       "%s: sFieldSystem @021C07DC -> FieldSystem (saveData, location = body+0x1280)" % name)
    ok(pm.in_game() and pm.save_loaded() and pm.is_safe() and not pm.in_battle() and not pm.pc_open()
       and not pm.field_task() and not pm.walking(), "%s: in game, idle, is_safe" % name)
    p = pm.position()
    got = {k: p[k] for k in exp["pos"]}
    ok(got == exp["pos"], "%s: position %s" % (name, got))
    ok(pm.player_name() == "LUCAS" and pm.trainer_id() == 33356 and pm.secret_id() == 2998 and pm.gender() == 0
       and p["gender"] == 0, "%s: trainer LUCAS, TID 33356, SID 2998, boy" % name)
    ok(pm.money() == exp["money"] and pm.badges() == 0, "%s: money %d, badges 0" % (name, pm.money()))
    ok(pm.has_save() == exp["save"], "%s: has_save() = %s" % (name, exp["save"]))
    party = pm.party_checked()
    info = [G.info(r) for r in party or []]
    ok(party is not None and len(party) == exp["party"] and pm.party_count() == exp["party"]
       and all(i["checksum_ok"] and i["species"] == 387 and i["ot"] == "LUCAS" and i["tid"] == 33356
               and i["sid"] == 2998 for i in info),
       "%s: party %d x TURTWIG (species 387, OT LUCAS 33356/2998), checksums ok" % (name, exp["party"]))
    if party:
        ok(all(P.record_checksum_ok(r) for r in party), "%s: platinum.record_checksum_ok on party records" % name)
    base = pm.box_base()
    ok(base == sd + P.SAVE_BODY + P.PAGE_PC_BOXES and pm.current_box() == 0,
       "%s: PC boxes at body+0xCF2C (=%08X), current box 0" % (name, base or 0))
    names = [pm.box_name(b) for b in range(18)]
    walls = [pm.box_wallpaper(b) for b in range(18)]
    ok(names == ["BOX %d" % (b + 1) for b in range(18)] and walls == list(range(16)) + [0, 1],
       "%s: box names BOX 1..BOX 18, wallpapers 0..15,0,1 (initialised by NEW GAME)" % name)
    empty = G.encrypt(bytes(136))
    ok(all(r == empty for b in range(18) for r in pm.box_raw(b)), "%s: all 540 box slots empty (zeros encrypted)" % name)


def check_walk(rig):
    rig.load("pt_starter.dsst")
    pm = rig.pm
    trace = rig.hold(["left"], 12)      # turn (~6 frames) + start one step; it completes after release
    for _ in range(6):
        rig.d.run(1)
        trace.append(pm.position())
    xs = [t["px"] for t in trace]
    steps = [a - b for a, b in zip(xs, xs[1:])]
    ok(any(t["moving"] for t in trace) and not pm.is_safe(), "walk: moving flag set while stepping, not safe")
    ok(trace[-1]["x"] == 115 and all(0 <= s <= 2 for s in steps) and xs[-1] < 1856,
       "walk: x 116 -> 115 (destination at step start), px decreases smoothly (<= 2 units/frame): %s" % xs[:20])
    i = rig.wait(pm.is_safe, 60)
    p = pm.position()
    ok(i is not None and p["x"] == 115 and p["px"] == 115 * 16 and p["facing"] == P.DIR_LEFT and not p["moving"],
       "walk: settles at x=115, px=1840, facing left (2), is_safe again after %s frames" % i)
    trace = rig.hold(["left", "b"], 20)
    ok(any(t["running"] for t in trace) and any(88 <= t["action"] <= 91 for t in trace),
       "run (B held): running flag / RUN action 88-91 seen")
    xs = [t["px"] for t in trace]
    ok(max(a - b for a, b in zip(xs, xs[1:])) == 4, "run: px moves 4 units per update (2 x walking)")
    trace = rig.hold(["up"], 3)
    rig.d.run(30)


def check_menu_and_text(rig):
    rig.load("pt_starter.dsst")
    pm, d = rig.pm, rig.d
    d.press("x", 30)
    ok(pm.in_game() and pm.field_task() and not pm.is_safe(), "start menu open: field_task, not safe")
    d.press("b", 30)
    i = rig.wait(pm.is_safe, 60)
    ok(i is not None, "start menu closed: is_safe again")
    rig.load("pt_pc.dsst")
    d.press("a", 30)
    ok(pm.field_task() and not pm.is_safe() and not pm.pc_open(), "PC boot text box: field_task, not safe, pc_open False")


def check_battle(rig):
    rig.load("pt_battle.dsst")
    pm = rig.pm
    ok(pm.in_game() and pm.in_battle() and not pm.is_safe() and not pm.pc_open() and pm.child_overlay() == 16,
       "pt_battle: in_battle (child app overlay 16), not safe")
    ok(pm.map_id() == 342 and P.area_of(pm.map_id()) == "route_201", "pt_battle: map 342 Route 201")


def check_pc(rig):
    pm, d = rig.pm, rig.d
    rig.load("pt_pc.dsst")
    fired = []
    d.emu.memory.register_exec(P.HOOK_PC_OPEN, lambda a, s: fired.append(d.frame))
    for _ in range(6):      # "booted up the PC" (2: text still printing) / SOMEONE'S PC / "accessed" / "may be stored" / DEPOSIT
        d.press("a", 40)
    i = rig.wait(pm.pc_open, 300)
    d.emu.memory.register_exec(P.HOOK_PC_OPEN, None)
    ok(i is not None and len(fired) == 1, "PC: A x6 opens the box UI -> pc_open True; HOOK_PC_OPEN fired once")
    ok(not pm.is_safe() and pm.in_game() and not pm.in_battle(), "PC open: not safe, not battle")
    d.run(120)
    d.press("right", 20)                    # party slot 2
    d.press("a", 30)                        # STORE
    d.press("a", 40)
    d.press("a", 60)                        # Box 1
    d.run(60)
    box0 = pm.box_raw(0)
    i0, ip = G.info(box0[0]), G.info(pm.party_raw()[0])
    ok(pm.party_count() == 1 and i0["species"] == 387 and i0["checksum_ok"] and i0["ot"] == "LUCAS"
       and i0["pid"] == ip["pid"] and pm.pc_open(),
       "PC deposit: box 1 slot 1 = the deposited TURTWIG (same PID, valid checksum; PP healed), party count 1, live")
    closed = None
    for _ in range(8):                      # B: leave the box screen (may need a few presses), then the PC menus
        d.press("b", 40)
        if not pm.pc_open():
            closed = closed or d.frame
        if pm.is_safe():
            break
    ok(closed is not None, "PC: B closes the box UI -> pc_open False")
    i = rig.wait(pm.is_safe, 200)
    ok(i is not None and G.info(pm.box_raw(0)[0])["species"] == 387, "PC closed: is_safe again, deposit kept")
    rig.load("pt_pc_open.dsst")
    ok(pm.pc_open() and pm.child_overlay() == P.OVERLAY_PC_BOXES, "pt_pc_open: pc_open True")
    # MOVE POKéMON mode: R switches the box, currentBoxID follows live
    rig.load("pt_pc.dsst")
    for _ in range(5):
        d.press("a", 40)
    d.press("down", 10)
    d.press("down", 10)
    d.press("a", 30)
    rig.wait(pm.pc_open, 300)
    d.run(200)
    d.press("r", 60)
    ok(pm.current_box() == 1, "PC MOVE mode: R -> current_box() = 1 (live)")


def check_title_and_continue(rig):
    pm, d = rig.pm, rig.d
    rig.load("pt_title.dsst")
    ok(not pm.in_game() and pm.field_system() is None and pm.top_overlay() == P.OVERLAY_GAME_OPENING
       and pm.has_save() and pm.box_base() is None and pm.map_id() == 0,
       "pt_title: not in game (top overlay 77 game_opening), save present")
    ok(pm.player_name() == "LUCAS" and pm.pc_boxes_addr() is not None,
       "pt_title: save data readable before CONTINUE (name LUCAS)")
    r = P.fast_continue(d)
    ok(r == "loaded" and pm.is_safe() and pm.map_id() == 411 and pm.position()["x"] == 116,
       "fast_continue (ds.DS) from the title: %s, Twinleaf (116,887), safe" % r)
    # from power-on, NDSCore-style core
    d.emu.reset()
    n = []
    r = P.fast_continue(CoreShim(d), on_progress=lambda: n.append(1))
    ok(r == "loaded" and pm.is_safe() and pm.party_count() == 1 and n,
       "fast_continue (NDSCore-style) from power-on: %s, party 1, on_progress called %d x" % (r, len(n)))
    # no save: blank battery -> 'no save' right away, nothing pressed, game stays in intro/title
    blank = os.path.join(os.environ["XDG_CONFIG_HOME"], "blank.sav")
    with open(blank, "wb") as f:
        f.write(b"\xff" * 0x80000)
    d.emu.backup.import_file(blank)
    d.emu.reset()
    f0 = d.frame
    r = P.fast_continue(d)
    ok(r == "no save" and d.frame - f0 < 400 and not pm.has_save(), "fast_continue without a save: %s" % r)
    d.run(1500)
    ok(pm.top_overlay() == P.OVERLAY_GAME_OPENING and not pm.in_game(), "no save: game left alone on intro/title")
    d.emu.backup.import_file(st("pt_saved.sav"))
    d.emu.reset()
    r = P.fast_continue(d)
    ok(r == "loaded" and pm.player_name() == "LUCAS", "battery pt_saved.sav imported (raw 512 KiB) -> CONTINUE works")


def check_battery_file():
    with open(st("pt_saved.sav"), "rb") as f:
        sav = f.read()
    ok(len(sav) == 0x80000, "pt_saved.sav = raw 512 KiB flash image")
    blocks = []
    for base in (0, 0x40000):
        foot = sav[base + 0xCF18:base + 0xCF2C]
        cnt, bc, size, sig = struct.unpack("<IIII", foot[:16])
        if sig == 0x20060623 and size == 0xCF2C:
            blocks.append((cnt, base))
    ok(blocks, "pt_saved.sav: general block footer (size 0xCF2C, signature 0x20060623) found at %s"
       % [hex(b) for _, b in blocks])
    if blocks:
        base = max(blocks)[1]
        name = G.decode_text(sav[base + P.TRAINER:base + P.TRAINER + 16])
        tid, sid = struct.unpack_from("<HH", sav, base + P.TRAINER + 0x10)
        ok(name == "LUCAS" and (tid, sid) == (33356, 2998), "pt_saved.sav: newest block = body layout (LUCAS 33356/2998)")


def main():
    rig = Rig()
    check_rom(rig)
    check_battery_file()
    check_static_state(rig, "pt_room.dsst", {"pos": {"map": 415, "x": 4, "y": 6, "h": 0, "px": 64, "py": 96, "facing": 0},
                                             "money": 3000, "party": 0, "save": False})
    check_static_state(rig, "pt_town.dsst", {"pos": {"map": 411, "x": 116, "y": 887, "h": 2, "px": 1856, "py": 14192,
                                                     "facing": 1}, "money": 3000, "party": 0, "save": False})
    check_static_state(rig, "pt_starter.dsst", {"pos": {"map": 411, "x": 116, "y": 887, "h": 2, "facing": 1},
                                                "money": 3500, "party": 1, "save": True})
    check_static_state(rig, "pt_pc.dsst", {"pos": {"map": 420, "x": 12, "y": 4, "h": 0, "px": 192, "py": 64, "facing": 0},
                                           "money": 3500, "party": 2, "save": True})
    check_walk(rig)
    check_menu_and_text(rig)
    check_battle(rig)
    check_pc(rig)
    check_title_and_continue(rig)
    bad = RESULTS.count(False)
    print("%d checks, %d failed" % (len(RESULTS), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
