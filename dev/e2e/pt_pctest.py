"""Platinum PC <-> PC database, end to end on the real ROMs (real button presses in the box UI, local server).

  1. Yellow: deposit PIDGEY in Bill's PC, autosave commits, the server stores it.
  2. Platinum (dev/states/pt_pc.dsst): SOMEONE'S PC > WITHDRAW shows PIDGEY converted to Gen 4 and a Crystal egg as
     the ?EGG stand-in (screenshots); withdraw both; on close the stand-in leaves the party and stays in the
     database, PIDGEY is out of the database (it's in the Platinum party now).
  3. DEPOSIT PIDGEY into box 1 and a TURTWIG into box 16: on close both are harvested into the store (box 16 is
     folded into store box 1); autosave commits and the server stores it; reopening shows them in box 1 slots 1-20.
  4. Yellow again: PIDGEY (now Platinum data) converts back to Gen 1; TURTWIG is the ?TURTWIG stand-in.
Run: python dev/e2e/pt_pctest.py   (needs dev/states/{platinum.nds,pt_pc.dsst,yellow.gb,pokecenter_pc.state})
Screenshots are written to the temp folder printed at the end.
"""
import os, sys, time, subprocess, tempfile, pathlib, atexit, struct
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(_REPO, "dev", "states"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="yl_ptpc_"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "appdata")        # app folder + DeSmuME's battery folder
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "dev", "platinum"))
import ptrom  # noqa: E402  YL_PT_ROM=vanilla (default) | sinnoh: the Sinnoh ROM the app / DS core runs
print("Sinnoh ROM:", ptrom.label())
sys.path.insert(0, _HERE)
import pygame
pygame.quit = lambda: None
from PIL import Image
from yellowlink.config import Config
from yellowlink import app as appmod, pcdb, pk4, text
import pc_actions as pa

PT_ROM = os.path.join(STATES, "platinum.nds")
GB_ROM = os.path.join(STATES, "yellow.gb")
PORT = 8793
srv = subprocess.Popen([sys.executable, "-m", "yellowlink_server", "--port", str(PORT)], cwd=os.path.join(_REPO, "server"),
                       env=dict(os.environ, YL_RAIDS="off", YL_KEY="test", YL_DATA_DIR=str(tmp / "srv"),
                                PYTHONPATH=os.path.join(_REPO, "server")),
                       stdout=open(tmp / "srv.log", "w"), stderr=subprocess.STDOUT)
atexit.register(srv.terminate)
time.sleep(1.5)
FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


cfg = Config()
cfg.update(name="Ilias", server_url=f"ws://127.0.0.1:{PORT}", room_key="test", seen_help=True,
           games={"yellow": GB_ROM, "platinum": PT_ROM})


def start(game, state):
    cfg["current_game"] = game
    cfg.save()
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, state)
    a = appmod.App(cfg)
    os.environ.pop("YL_DEV_LOAD_STATE", None)      # let autosave run (it commits the PC store)
    saved = []
    orig = a.on_pc_saved
    a.on_pc_saved = lambda m: (saved.append(m.get("srv_rev")), orig(m))
    a.saved_revs = saved
    return a


def steps(a, n, cond=None):
    for _ in range(n):
        a.step()
        if cond and cond():
            return True
    return False


def store_nicks(a, committed=False):
    st = a.pcsync.committed if committed else a.pcsync.working
    return [e["nick"] for b in st["boxes"] for e in b]


# --- Platinum driving (real buttons through the app's DS core) -----------------------------------------------
def press(a, k, after=40):
    a.nds.button_press(k)
    steps(a, 6)
    a.nds.button_release(k)
    steps(a, after)


def storage_menu(a):
    """The DEPOSIT / WITHDRAW / MOVE / MOVE ITEMS / SEE YA! menu is on the top screen (white box down to y=86;
    the SOMEONE'S PC menu before it is one row shorter)."""
    top = a.nds.screen_ndarray()[78:87, 12:100]
    return int((top.min(axis=2) > 230).sum()) > 400


def pt_open(a, mode):
    """Pokémon Center PC (player in front of it): A boots it, SOMEONE'S PC, two text boxes, then the menu."""
    for i in range(10):
        if storage_menu(a):
            break
        press(a, "a")
    if not storage_menu(a):
        shot(a, "pt_open_failed.png")
        return False
    for _ in range({"deposit": 0, "withdraw": 1, "move": 2, "items": 3}[mode]):
        press(a, "down", 10)
    press(a, "a", 30)
    ok = steps(a, 300, a.game.pc_open)
    steps(a, 150)
    return ok


def pt_close(a):
    """B out of the box UI, then out of the PC menus back to the overworld."""
    for _ in range(8):
        press(a, "b", 40)
        if not a.game.pc_open():
            break
    for _ in range(8):
        if a.game.is_safe():
            break
        press(a, "b", 40)
    return steps(a, 200, a.game.is_safe)


def species(rec):
    return pk4.PK4(pk4.decrypt(rec)).species


def nick(rec):
    return pk4.PK4(pk4.decrypt(rec)).nickname


def box_view(a, b=0):
    return [(species(r), nick(r)) for r in a.game.box_raw(b) if r != pk4.EMPTY_BOX]


def shot(a, name):
    img = Image.frombytes("RGB", (256, 384), a.nds.screen_rgb())
    img.save(tmp / name)
    return tmp / name


def party_species(a):
    """Party species once the game isn't in the middle of updating a record (it decrypts them in place)."""
    for _ in range(120):
        recs = a.game.party_checked()
        if recs is not None:
            return [species(r[:136]) for r in recs]
        steps(a, 1)
    return None


# 1. Yellow ---------------------------------------------------------------------------------------------------
Y = start("yellow", "pokecenter_pc.state")
steps(Y, 300, lambda: Y.net.status == "online")
check("Yellow online with the PC database on", Y.full and Y.pc_codec is not None and Y.net.status == "online")
pa.open_pc(Y.pyboy)
Y_OT = Y.game.party()[1].ot_str()
Y_LEVEL = Y.game.party()[1].level
check("Yellow: deposit PIDGEY", pa.deposit(Y.pyboy, 1))
pa.close_pc(Y.pyboy)
check("Yellow: autosave commits", Y.autosave(force=True) and store_nicks(Y, True) == ["PIDGEY"])
steps(Y, 300, lambda: Y.saved_revs)
check("Yellow: server stored the PC", bool(Y.saved_revs))
Y.shutdown()

# 2. Platinum: converted PIDGEY + ?EGG stand-in ----------------------------------------------------------------
P = start("platinum", "pt_pc.dsst")
check("Platinum runs with the PC database on", P.ds and P.full and P.pc_codec is not None
      and type(P.pc_codec).__name__ == "Gen4PC")
steps(P, 300, lambda: P.net.status == "online")
check("store loaded: PIDGEY", store_nicks(P) == ["PIDGEY"], store_nicks(P))
egg2 = bytearray(32)
egg2[0], egg2[2], egg2[3], egg2[6], egg2[7], egg2[31] = 152, 33, 45, 0x12, 0x34, 5
egg2[8:11] = (135).to_bytes(3, "big")
egg2[23], egg2[24] = 35, 40
EGG = bytes(egg2) + text.encode("KRIS") + text.encode("EGG") + b"\x01"
P.pcsync.working["boxes"][0].append({"id": "cegg", "origin": "crystal", "dex": 152, "level": 5, "nick": "EGG",
                                     "ot": "KRIS", "otid": 0x1234, "gen1": None, "gen2": pcdb._b64(EGG), "gen4": None})
P.pcsync.pending = True
check("egg added to the store (committed)", P.autosave(force=True) and "EGG" in store_nicks(P, True))
party0 = party_species(P)
check("Platinum party: 2 x TURTWIG", party0 == [387, 387], party0)


def dex_bits(a, off):
    from yellowlink import platinum as PL
    return a.game.m.read(a.game.save_body() + PL.PAGE_POKEDEX + off, 64)


def has(bits, sp):
    return bool(bits[(sp - 1) // 8] >> ((sp - 1) % 8) & 1)


check("Platinum Pokédex before: PIDGEY and DITTO not caught", not has(dex_bits(P, 4), 16) and
      not has(dex_bits(P, 4), 132))
check("SOMEONE'S PC > WITHDRAW opens the box UI", pt_open(P, "withdraw"))
check("the converted PIDGEY in the PC counts in Platinum's Pokédex like a received trade (caught + seen); the ?EGG "
      "stand-in (DITTO) doesn't", has(dex_bits(P, 4), 16) and has(dex_bits(P, 0x44), 16)
      and not has(dex_bits(P, 4), 132) and not has(dex_bits(P, 0x44), 132))
check("open hook started a PC session", P.pcsync.session_open and P._pt_pc == "open", P._pt_pc)
view = box_view(P)
check("Platinum box 1 shows PIDGEY (converted) + ?EGG", view == [(16, "PIDGEY"), (132, "?EGG")], view)
s1 = shot(P, "pt_1_box_converted_pidgey.png")
press(P, "right", 40)
s2 = shot(P, "pt_2_box_standin_egg.png")
press(P, "left", 60)
press(P, "a", 40)                       # PIDGEY: WITHDRAW
press(P, "a", 100)
press(P, "right", 40)
press(P, "a", 40)                       # ?EGG: WITHDRAW
press(P, "a", 100)
check("the game let both into the party", party_species(P) == [387, 387, 16, 132], party_species(P))
check("box 1 empty in the game", box_view(P) == [], box_view(P))
check("PC closes (B)", pt_close(P))
check("close detected: session ended", not P.pcsync.session_open and P._pt_pc is None)
check("stand-in removed from the party again", party_species(P) == [387, 387, 16], party_species(P))
check("?EGG back in the database (same entry), PIDGEY out", store_nicks(P) == ["EGG"] and
      P.pcsync.working["boxes"][0][0]["id"] == "cegg" and P.pcsync.pending, store_nicks(P))
check("player told the stand-in stays", any("Egg" in t[0] and "can't leave" in t[0] for t in P.toasts),
      [t[0] for t in P.toasts])
party_species(P)
pk = pk4.PK4(pk4.decrypt(P.game.party_checked()[2]))
check("PIDGEY in the Platinum party: real Gen-4 mon (level, Yellow OT, stats)", pk.species == 16
      and pk.ot_name == Y_OT and pk.level(P.pc_codec.rom) == Y_LEVEL and pk.stats[0] > 0,
      (pk.ot_name, pk.level(P.pc_codec.rom), pk.stats))

# 3. deposit PIDGEY -> box 1, TURTWIG -> box 16 -------------------------------------------------------------------
check("DEPOSIT opens the box UI", pt_open(P, "deposit"))
check("box 1 shows ?EGG only", box_view(P) == [(132, "?EGG")], box_view(P))
press(P, "down", 30)                    # party slot 3 (PIDGEY)
press(P, "a", 40)
press(P, "a", 40)                       # STORE
press(P, "a", 80)                       # Box 1
check("PIDGEY deposited into box 1", party_species(P) == [387, 387] and (16, "PIDGEY") in box_view(P),
      (party_species(P), box_view(P)))
press(P, "up", 30)
press(P, "a", 40)
press(P, "a", 40)                       # STORE
for _ in range(15):
    press(P, "right", 20)               # Box 16
press(P, "a", 80)
check("TURTWIG deposited into box 16", party_species(P) == [387] and [s for s, _ in box_view(P, 15)] == [387],
      (party_species(P), box_view(P, 15)))
check("PC closes", pt_close(P))
nicks = store_nicks(P)
check("harvested: EGG + PIDGEY + TURTWIG in store box 1", sorted(nicks) == ["AAAAAAAAAA", "EGG", "PIDGEY"]
      and all(len(b) == 0 for b in P.pcsync.working["boxes"][1:]), P.pcsync.working["boxes"])
pid = next(e for e in P.pcsync.working["boxes"][0] if e["nick"] == "PIDGEY")
check("PIDGEY is now a Platinum entry (gen4 = the game's bytes)", pid["origin"] == "platinum" and pid["gen4"]
      and not pid["gen1"] and pid["dex"] == 16)
check("player told about the box-16 fold", any("boxes 1-14" in t[0] for t in P.toasts), [t[0] for t in P.toasts])
n = len(P.saved_revs)
if P.pcsync.pending:                    # (the app's own 30-second autosave may already have done it)
    check("not committed until the autosave", "AAAAAAAAAA" not in store_nicks(P, True), store_nicks(P, True))
    check("autosave (overworld, idle) commits", P.game.is_safe() and P.autosave() and not P.pcsync.pending)
check("committed", sorted(store_nicks(P, True)) == ["AAAAAAAAAA", "EGG", "PIDGEY"])
steps(P, 300, lambda: len(P.saved_revs) > n)
check("server stored the Platinum changes", len(P.saved_revs) > n)

check("reopen", pt_open(P, "withdraw"))
check("box 1 = ?EGG, PIDGEY, TURTWIG (slots 1-3); box 16 empty",
      [s for s, _ in box_view(P)] == [132, 16, 387] and box_view(P, 15) == [], (box_view(P), box_view(P, 15)))
shot(P, "pt_3_box_after_harvest.png")
check("look-only visit changes nothing", pt_close(P) and not P.pcsync.pending)

# 3b. MOVE ITEMS: give Leftovers to the ?EGG stand-in (goes back to the bag) and to PIDGEY (stays, same entry) ----
LEFTOVERS = 234
from yellowlink import platinum_pc


def leftovers(a):
    bag = a.game.save_body() + platinum_pc.PAGE_BAG
    return sum(q for i, q in (struct.unpack_from("<HH", a.game.read(bag + 4 * k, 4)) for k in range(165))
               if i == LEFTOVERS)


check("bag writer: 2 Leftovers into the Items pocket", P.pc_codec.give_item(LEFTOVERS)
      and P.pc_codec.give_item(LEFTOVERS) and leftovers(P) == 2)
ids_before = [e["id"] for e in P.pcsync.working["boxes"][0]]
egg_before = dict(P.pcsync.working["boxes"][0][0])
check("SOMEONE'S PC > MOVE ITEMS opens the box UI", pt_open(P, "items"))
for _ in range(5):                      # ?EGG: GIVE > the bag opens > Leftovers > give > "now being held"
    press(P, "a", 70)
press(P, "right", 60)
for _ in range(5):                      # PIDGEY: the same
    press(P, "a", 70)
held = [pk4.PK4(pk4.decrypt(r)).item for r in P.game.box_raw(0)[:2]]
check("the game put Leftovers on ?EGG and PIDGEY", held == [LEFTOVERS, LEFTOVERS] and leftovers(P) == 0,
      (held, leftovers(P)))
shot(P, "pt_4_items_given.png")
check("PC closes", pt_close(P))
steps(P, 30)
check("the stand-in's Leftovers went back to the bag", leftovers(P) == 1, leftovers(P))
check("player told", any("Leftovers went back" in t[0] for t in P.toasts), [t[0] for t in P.toasts])
check("same entries, no clones; ?EGG's real data untouched",
      [e["id"] for e in P.pcsync.working["boxes"][0]] == ids_before
      and P.pcsync.working["boxes"][0][0] == egg_before, store_nicks(P))
pid = P.pcsync.working["boxes"][0][1]
check("PIDGEY keeps its entry and now holds Leftovers",
      pid["nick"] == "PIDGEY" and pk4.PK4(pk4.decrypt(pcdb._unb64(pid["gen4"]))).item == LEFTOVERS)
check("reopen", pt_open(P, "withdraw"))
held = [pk4.PK4(pk4.decrypt(r)).item for r in P.game.box_raw(0)[:3]]
check("box: ?EGG without an item, PIDGEY with Leftovers", held == [0, LEFTOVERS, 0], held)
check("closing again returns nothing more", pt_close(P) and leftovers(P) == 1 and P.pcsync.count() == 3)

# 3c. PIDGEY is offered in a trade (locked); taking its Leftovers into the bag is undone (no duplicate item) ----
pid_entry = dict(P.pcsync.working["boxes"][0][1])
P.pcsync.lock("t-e2e", [pid_entry["id"]])
check("MOVE ITEMS again", pt_open(P, "items"))
press(P, "right", 60)
press(P, "a", 70)                       # pick up PIDGEY's Leftovers: GIVE / BAG / INFO / CANCEL
press(P, "down", 30)
press(P, "a", 90)                       # BAG
for _ in range(2):
    if pk4.PK4(pk4.decrypt(P.game.box_raw(0)[1])).item == 0 and leftovers(P) == 2:
        break
    press(P, "a", 70)
check("the game moved PIDGEY's Leftovers into the bag", pk4.PK4(pk4.decrypt(P.game.box_raw(0)[1])).item == 0
      and leftovers(P) == 2, (pk4.PK4(pk4.decrypt(P.game.box_raw(0)[1])).item, leftovers(P)))
check("PC closes", pt_close(P))
steps(P, 30)
check("that Leftovers left the bag again (it stays with PIDGEY, who is in a trade)", leftovers(P) == 1, leftovers(P))
check("PIDGEY's entry unchanged, nothing new", P.pcsync.working["boxes"][0][1] == pid_entry and P.pcsync.count() == 3)
P.pcsync.unlock("t-e2e")
check("reopen: PIDGEY holds Leftovers again", pt_open(P, "withdraw")
      and pk4.PK4(pk4.decrypt(P.game.box_raw(0)[1])).item == LEFTOVERS)
check("and closing changes nothing", pt_close(P) and leftovers(P) == 1 and P.pcsync.count() == 3)
P.shutdown()

# 4. Yellow again ---------------------------------------------------------------------------------------------------
Y2 = start("yellow", "pokecenter_pc.state")
steps(Y2, 120)
pa.open_pc(Y2.pyboy)
box = [n for _, n in pa.box(Y2.pyboy)]
check("Yellow box: ?EGG, PIDGEY (converted back), ?AAAAAAAAA (the Turtwig)", box == ["?EGG", "PIDGEY", "?AAAAAAAAA"],
      box)
rec = Y2.pc_codec.read_boxes()[Y2.pc_codec.current_box()]
check("Yellow PIDGEY is a real Gen-1 Pidgey with its OT and level", Y2.rom.dex_no(rec[1][0]) == 16
      and text.decode(rec[1][33:44]) == Y_OT and rec[1][3] == Y_LEVEL, rec[1].hex())
pa.close_pc(Y2.pyboy)
check("Yellow: database unchanged by a look-only visit", sorted(store_nicks(Y2)) == ["AAAAAAAAAA", "EGG", "PIDGEY"])
Y2.shutdown()
for p in (s1, s2):
    print("screenshot:", p)
print("\nfiles in", tmp)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
