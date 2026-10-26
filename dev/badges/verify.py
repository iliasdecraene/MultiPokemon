"""Verify the badge detections (badges.py + progress()/wild_is_shiny()/champion() in game.py, crystal.py,
platinum.py, ChallengeView.run_champion) on the real ROMs.

Needs dev/states/{yellow.gb, crystal.gbc, platinum.nds} and the existing states (soullink_*.state, crystal_sl_*.state,
pt_sl_*.dsst - built by the Soul Link verifiers). Never writes into dev/states (all edits are RAM writes on loaded
states, kept in memory). pret symbol files for the address checks: YL_PRET_SYMS (default /home/claude/pret-syms,
containing pokeyellow.sym / pokecrystal.sym from pret's `symbols` branches); without them those checks are skipped
(reported). pret/pokeplatinum checkout (optional, YL_PRET_PLATINUM, default /home/claude/pokeplatinum) for the
Sinnoh dex source list.

Usage: python dev/badges/verify.py [yellow] [crystal] [platinum]
Prints PASS/FAIL per check and ends with "N checks, 0 failed" / "ALL PASS".
"""
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import types

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="ylbadges_verify_")   # DeSmuME's own battery file
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
STATES = os.path.join(REPO, "dev", "states")
for p in ("client", "dev/e2e", "dev/crystal", "dev/platinum"):
    sys.path.insert(0, os.path.join(REPO, p))
SYMS = os.environ.get("YL_PRET_SYMS", "/home/claude/pret-syms")
PRET_PT = os.environ.get("YL_PRET_PLATINUM", "/home/claude/pokeplatinum")

from yellowlink import badges, challenge_view, crystal, game, pcdb, pk4, text  # noqa: E402
from yellowlink import platinum as P  # noqa: E402

RESULTS = []
SHINY_DVS = bytes([0xEA, 0xAA])          # Atk 14, Def 10, Spd 10, Spc 10
PLAIN_DVS = bytes([0x98, 0x88])


def ok(cond, what):
    RESULTS.append((bool(cond), what))
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    return bool(cond)


def note(s):
    print("     " + s, flush=True)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def syms(name):
    path = os.path.join(SYMS, name)
    if not os.path.exists(path):
        return None
    out = {}
    for line in open(path):
        if line.startswith(";") or " " not in line:
            continue
        parts = line.split()
        if len(parts) < 2 or ":" not in parts[0]:
            continue
        loc, label = parts[:2]
        bank, addr = loc.split(":")
        out.setdefault(label, (int(bank, 16), int(addr, 16)))
    return out


def check_syms(game_name, table, pairs):
    if table is None:
        ok(True, "%s: pret symbol file missing in %s - address checks SKIPPED" % (game_name, SYMS))
        return
    bad = [(label, hex(addr), table.get(label)) for label, addr in pairs if table.get(label, (0, -1))[1] != addr]
    ok(not bad, "%s: %d addresses == pret symbols (%s)" % (game_name, len(pairs), ", ".join(lbl for lbl, _ in pairs))
       + (" MISMATCH %s" % bad if bad else ""))


def fake_app(**kw):
    return types.SimpleNamespace(**kw)


# =====================================================================================================================
# Yellow
# =====================================================================================================================
def yellow():
    print("=== Yellow ===", flush=True)
    Y = load_module("sl_yellow", os.path.join(REPO, "dev", "e2e", "soullink_mem_yellow.py"))
    pa = sys.modules["pc_actions"]
    RD = Y.RD
    ok(game.sha1_of(Y.ROM) == game.YELLOW_SHA1, "yellow.gb is the verified ROM (%s)" % game.YELLOW_SHA1[:8])
    t = syms("pokeyellow.sym")
    check_syms("Yellow", t, [("wNumHoFTeams", game.NUM_HOF_TEAMS), ("wPokedexOwned", game.DEX_OWNED),
                             ("wPlayerName", game.PLAYER_NAME), ("wPlayerID", game.PLAYER_ID),
                             ("wPartyMons", game.PARTY_MONS), ("wPartyMonOT", game.PARTY_OT),
                             ("wIsInBattle", game.IS_IN_BATTLE), ("wEnemyMonDVs", game.ENEMY_MON_DVS),
                             ("wTransformedEnemyMonOriginalDVs", game.TRANSFORMED_ENEMY_ORIG_DVS),
                             ("wEnemyBattleStatus3", game.ENEMY_BATTLE_STATUS3), ("wEnemyMonMoves", 0xCFEC),
                             ("wDayCareInUse", game.DAYCARE_IN_USE), ("wDayCareMon", game.DAYCARE_MON),
                             ("wBoxDataStart", game.BOX_COUNT), ("wEventFlags", EVENT_FLAGS_Y)])
    if t:
        ok(t["wPokedexOwnedEnd"][1] - t["wPokedexOwned"][1] == 19 and
           t["wMainDataStart"][1] <= game.NUM_HOF_TEAMS < t["wMainDataEnd"][1],
           "wPokedexOwned is 19 bytes; wNumHoFTeams lies in wMainData (saved with the game)")

    def emu(state):
        pb, g = Y.emu(state)
        Y.tick(pb, 2)
        return pb, g

    def own_count(pb):
        """The game's own Pokédex screen: START -> POKéDEX, read the OWN number (includes Mew)."""
        m = pb.memory
        m[EVENT_FLAGS_Y + EVENT_GOT_POKEDEX // 8] |= 1 << (EVENT_GOT_POKEDEX % 8)   # so the menu has POKéDEX
        pa._press(pb, "start")
        pa._settle(pb, 30)
        for _ in range(4):
            pa._press(pb, "a", hold=6)
            Y.tick(pb, 120)
            lines = pa.screen_lines(pb)
            idx = next((i for i, ln in enumerate(lines) if "OWN" in ln), None)
            if idx is not None:
                return int(lines[idx + 1].split(".")[-1].strip() or -1)
        return None

    def set_dex(pb, dexes, value=True):
        m = pb.memory
        for d in dexes:
            a, b = game.DEX_OWNED + (d - 1) // 8, 1 << ((d - 1) % 8)
            m[a] = (m[a] | b) if value else (m[a] & ~b & 0xFF)

    # --- existing states ---------------------------------------------------------------------------------------
    for st in ("pallet_after_lab.state", "pokecenter_pc.state", "soullink_party3.state", "soullink_daycare.state",
               "soullink_route1_grass.state", "soullink_wild_battle.state", "soullink_trainer_battle.state"):
        pb, g = emu(st)
        p = game.progress(g)
        party = g.party()
        mine = [mn for mn in party if mn.ot_id == p["owner"]["tid"]] if p else []
        owned = all(g.m[game.DEX_OWNED + (RD.dex_no(mn.species) - 1) // 8] >> ((RD.dex_no(mn.species) - 1) % 8) & 1
                    for mn in mine)
        ok(p is not None and not p["champion"] and p["shinies"] == [] and p["dex_total"] == 150 and owned and
           not p["dex_complete"],
           "%s: progress() champion False, no shinies, own party species owned in wPokedexOwned, Kanto %d/150 "
           "(owner %s/%d)" % (st, p and p["dex_caught"], p and p["owner"]["ot"], p and p["owner"]["tid"]))
        pb.stop(save=False)
    for st in ("pokecenter_pc.state", "soullink_party3.state"):
        pb, g = emu(st)
        p = game.progress(g)
        mew = g.m[game.DEX_OWNED + 150 // 8] >> (150 % 8) & 1
        own = own_count(pb)
        ok(own is not None and p["dex_caught"] == own - mew,
           "%s: dex_caught %d == the game's own Pokédex OWN %s minus Mew" % (st, p["dex_caught"], own))
        pb.stop(save=False)
    for desc, setup, want, complete in (
            ("all 151 owned", lambda pb: set_dex(pb, range(1, 152)), 151, True),
            ("#1-150 owned, no Mew", lambda pb: (set_dex(pb, range(1, 151)), set_dex(pb, [151], False)), 150, True),
            ("all but BULBASAUR (Mew owned)", lambda pb: (set_dex(pb, range(1, 152)), set_dex(pb, [1], False)),
             150, False)):
        pb, g = emu("pokecenter_pc.state")
        setup(pb)
        p = game.progress(g)
        own = own_count(pb)
        ok(own == want and p["dex_complete"] == complete and p["dex_caught"] == (150 if complete else 149),
           "dex %s: game's OWN %s, dex_caught %d, dex_complete %s" % (desc, own, p["dex_caught"], p["dex_complete"]))
        pb.stop(save=False)

    # --- champion ----------------------------------------------------------------------------------------------
    pb, g = emu("pallet_after_lab.state")
    view = challenge_view.ChallengeView(fake_app(game=g, G=game, gen2=False, ds=False, pyboy=pb, rom=RD))
    ok(not game.champion(g) and not view.run_champion(), "no Hall of Fame: champion() / run_champion() False")
    pb.memory[game.NUM_HOF_TEAMS] = 1
    ok(game.champion(g) and game.progress(g)["champion"] and view.run_champion(),
       "wNumHoFTeams = 1 (RAM): champion() True, progress()['champion'] True, run_champion() True")
    pb.memory[game.NUM_HOF_TEAMS] = 0
    ok(not game.champion(g), "wNumHoFTeams back to 0: champion() False")
    ok(Y.warp_via_lab_door(pb, game.HALL_OF_FAME_MAP, 0) and game.champion(g) and g.hall_of_fame_count() == 0,
       "walked into the HALL_OF_FAME room (warp): champion() True before the game counts it")
    f = 0
    while f < 6000 and g.hall_of_fame_count() == 0:
        if f % 40 == 0:
            pb.button_press("a")
        if f % 40 == 3:
            pb.button_release("a")
        pb.tick()
        f += 1
    pb.button_release("a")
    ok(g.hall_of_fame_count() == 1 and game.champion(g),
       "the game's own HoF script: wNumHoFTeams 1 after %d frames, champion() True (the save after the credits "
       "keeps it: dev/e2e/soullink_mem_yellow.py)" % f)
    pb.stop(save=False)

    # --- wild battles ------------------------------------------------------------------------------------------
    pb, g = emu("soullink_route1_grass.state")
    ok(game.wild_is_shiny(g) is None, "overworld: wild_is_shiny() None")
    pb.stop(save=False)
    pb, g = emu("soullink_trainer_battle.state")
    ok(g.m[game.IS_IN_BATTLE] == 2 and game.wild_is_shiny(g) is None, "trainer battle: wild_is_shiny() None")
    pb.stop(save=False)
    # several real encounters: the value never changes between frame 0 and the battle menu
    base = io.BytesIO()
    pb, g = Y.emu("soullink_route1_grass.state")
    pb.save_state(base)
    stable = waited = 0
    for trial in range(6):
        base.seek(0)
        pb.load_state(base)
        Y.tick(pb, 3 + 11 * trial)
        view = challenge_view.ChallengeView(fake_app(game=g, G=game, gen2=False, ds=False, pyboy=pb, rom=RD))
        view.battle()                                       # overworld: the view sees no battle
        if not Y.step_until_battle(pb, ("up", "down")):
            continue
        first = (game.wild_is_shiny(g), g.read(game.ENEMY_MON_DVS, 2))
        v0 = view.battle()                                  # frame 0: the referee's view waits
        pb.tick()
        v1 = view.battle()
        samples = Y.advance_record(pb, Y.in_battle_menu, lambda: (game.wild_is_shiny(g), g.read(game.ENEMY_MON_DVS, 2)))
        stable += first[0] is not None and all(s == first for s in samples)
        waited += v0 is None and v1 is not None and v1["shiny"] == badges.gen1_is_shiny(samples[-1][1]) \
            and v1 == g.battle_info()
    ok(stable == 6, "6 real Route 1 encounters: wild_is_shiny() is a bool from wIsInBattle's first frame and the "
       "DVs it reads never change up to the battle menu (%d/6)" % stable)
    ok(waited == 6, "... the referee's view (ChallengeView.battle) gives None on frame 0 and from frame 1 the "
       "battle_info with 'shiny' = the Gen-2 rule on the final DVs (%d/6)" % waited)
    pb.stop(save=False)

    pb, g = emu("soullink_wild_battle.state")              # frame 0 of a real wild battle (Route 1)
    m = pb.memory
    ok(game.wild_is_shiny(g) is badges.gen1_is_shiny(g.read(game.ENEMY_MON_DVS, 2)),
       "real wild battle: wild_is_shiny() = Gen-2 rule on wEnemyMonDVs %s" % g.read(game.ENEMY_MON_DVS, 2).hex())
    g.write(game.ENEMY_MON_DVS, SHINY_DVS)
    ok(game.wild_is_shiny(g) is True and g.battle_info()["shiny"] is True,
       "shiny DVs EA AA written into wEnemyMonDVs: wild_is_shiny() True, battle_info()['shiny'] True (Soul Link "
       "shiny clause)")
    samples = Y.advance_record(pb, Y.in_battle_menu, lambda: game.wild_is_shiny(g))
    ok(samples and all(s is True for s in samples), "... and True on every frame up to the battle menu")
    # the wild mon uses Transform (all its moves = TRANSFORM): wEnemyMonDVs become the player's, the original
    # DVs move to wTransformedEnemyMonOriginalDVs
    g.write(0xCFEC, bytes([0x90] * 4))                      # wEnemyMonMoves
    g.write(0xCFFD, bytes([10] * 4))                        # wEnemyMonPP
    pdvs = g.party()[0].data[27:29]
    Y.fight_move(pb, 2)                                     # GROWL (the move menu's wCurrentMenuItem is 1-based)
    log = Y.advance_record(pb, Y.in_battle_menu, lambda: game.wild_is_shiny(g))
    trans = bool(m[game.ENEMY_BATTLE_STATUS3] & 8)
    ok(trans and g.read(game.ENEMY_MON_DVS, 2) == pdvs and g.read(game.TRANSFORMED_ENEMY_ORIG_DVS, 2) == SHINY_DVS
       and all(s is True for s in log),
       "the wild mon used the game's Transform: TRANSFORMED bit set, wEnemyMonDVs = player's %s, original EA AA in "
       "wTransformedEnemyMonOriginalDVs; wild_is_shiny() stays True" % pdvs.hex())
    g.write(game.TRANSFORMED_ENEMY_ORIG_DVS, PLAIN_DVS)
    ok(game.wild_is_shiny(g) is False, "transformed with plain original DVs: False (not fooled by copied DVs)")
    g.write(game.TRANSFORMED_ENEMY_ORIG_DVS, SHINY_DVS)
    before = game.progress(g)
    n0 = m[game.PARTY_COUNT]
    Y.use_item_in_battle(pb, 1)                             # MASTER BALL
    ok(Y.finish_battle(pb) and Y.wait_safe(pb, g), "caught it with the game's MASTER BALL")
    party = g.party_checked()
    caught = party[-1]
    p = game.progress(g)
    rec = caught.data[:33] + caught.ot + caught.nick
    key = badges.gen1_badge_key(caught.data, RD.dex_no(caught.species))
    ok(len(party) == n0 + 1 and caught.data[27:29] == SHINY_DVS and caught.ot_id == p["owner"]["tid"]
       and caught.ot_str() == p["owner"]["ot"],
       "the caught mon (%s) has the original shiny DVs EA AA and the player's OT name + ID"
       % RD.species_name(caught.species))
    entry = pcdb.Gen1PC(m, RD).entry_from_record(rec)
    boxrec = rec[:3] + bytes([caught.data[33], rec[4], rec[5], rec[6], 0x19]) + rec[8:]   # deposited; a catch-rate
    c2 = crystal.gen1_to_gen2(boxrec, RD.dex_no(caught.species))                       # byte the Time Capsule maps
    back = pcdb.gen2_to_gen1(c2, RD)
    ok(p["shinies"] == [key] and badges.entry_shiny_key(entry, "kanto", p["owner"]) == key and before["shinies"] == []
       and badges.entry_shiny_key(dict(entry, gen1=pcdb._b64(back)), "kanto", p["owner"]) == key,
       "progress(): the catch is an own shiny, badge key %s == the key of its PC-store entry, also after a "
       "Yellow -> Crystal -> Yellow round trip (Time Capsule catch-rate byte %02X -> item %02X -> %02X)"
       % (key, boxrec[7], c2 and c2[1], back and back[7]))
    ok(p["dex_caught"] == before["dex_caught"] + (0 if before["dex_caught"] == p["dex_caught"] else 1) and
       g.m[game.DEX_OWNED + (RD.dex_no(caught.species) - 1) // 8] >> ((RD.dex_no(caught.species) - 1) % 8) & 1,
       "the game set the caught species' wPokedexOwned bit (Kanto %d -> %d)" % (before["dex_caught"], p["dex_caught"]))
    snap = io.BytesIO()
    pb.save_state(snap)
    # other OT name / OT ID -> not the player's catch
    i = len(party) - 1
    g.write(game.PARTY_OT + i * 11, text.encode("OTHER", 11))
    ok(game.progress(g)["shinies"] == [], "same mon with OT name OTHER: not counted")
    snap.seek(0)
    pb.load_state(snap)
    m[game.PARTY_MONS + i * 44 + 12] ^= 0x01
    ok(game.progress(g)["shinies"] == [], "same mon with another OT ID: not counted")
    snap.seek(0)
    pb.load_state(snap)
    # into the current box (the game's own deposit routine, mirrored by move_party_to_box) and an SRAM box
    ok(g.move_party_to_box(i) and game.progress(g)["shinies"] == [key],
       "deposited into the current box (wBoxDataStart): still counted once, same key")
    pc = pcdb.Gen1PC(m, RD)
    boxes = pc.read_boxes()
    cur = pc.current_box()
    other = (cur + 5) % 12
    rec2 = bytearray(boxes[cur][-1])
    rec2[12] ^= 0x10                                         # a different mon: another OT ID ...
    boxes[other].append(bytes(rec2))
    rec3 = bytearray(boxes[cur][-1])
    rec3[27] = 0xAA                                          # ... and an own one with Atk DV 10 (still shiny)
    boxes[other].append(bytes(rec3))
    pc.write_boxes(boxes)
    p = game.progress(g)
    key3 = badges.gen1_badge_key(bytes(rec3), RD.dex_no(rec3[0]))
    ok(p["shinies"] == [key, key3],
       "box %d (SRAM bank %d, written with checksums): own shiny found, the other OT's skipped" % (other + 1,
                                                                                                  2 if other < 6 else 3))
    boxes = pc.read_boxes()
    boxes[(other + 1) % 12].append(bytes(rec3))             # an identical twin of that one in yet another box
    pc.write_boxes(boxes)
    ok(game.progress(g)["shinies"] == [key, key3, key3 + "-2"], "an identical twin counts as a second mon (key-2)")
    pb.stop(save=False)

    pb, g = emu("soullink_daycare.state")
    m = pb.memory
    ok(m[game.DAYCARE_IN_USE] == 1 and len(g.daycare_records()) == 1 and game.progress(g)["shinies"] == [],
       "Day Care state: one mon in the Day Care, not shiny")
    g.write(game.DAYCARE_MON + 27, SHINY_DVS)
    rec = g.daycare_records()[0]
    ok(game.progress(g)["shinies"] == [badges.gen1_badge_key(rec, RD.dex_no(rec[0]))],
       "its DVs made shiny: counted from the Day Care")
    pb.stop(save=False)


def yellow_dexmark():
    """A Crystal-caught GEODUDE in the YellowLink PC store shows up in Yellow's PC (real App, Bill's PC hook): the
    game's own Pokédex OWN count goes up by one; a ?STAND-IN does nothing."""
    print("=== Yellow: Pokédex for mons arriving through YellowLink ===", flush=True)
    import pathlib
    from yellowlink import app as appmod
    from yellowlink.config import Config
    Y = load_module("sl_yellow2", os.path.join(REPO, "dev", "e2e", "soullink_mem_yellow.py"))
    pa = sys.modules["pc_actions"]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ylbadges_y_"))
    crom = crystal.RomData.from_file(os.path.join(STATES, "crystal.gbc"))

    class Cfg(Config):
        def __init__(self, d, rom):
            d.mkdir(parents=True, exist_ok=True)
            self.d = d
            super().__init__(d / "config.json")
            self.update(name="Ash", server_url="ws://127.0.0.1:1", room_key="x", rom_path=rom, seen_help=True,
                        games={"yellow": rom}, current_game="yellow")
        save_path = property(lambda self: self.d / "game.sav")
        resume_path = property(lambda self: self.d / "resume.state")

        @property
        def backup_dir(self):
            p = self.d / "bk"
            p.mkdir(exist_ok=True)
            return p

    geo = crystal.make_mon(crom, 74, 12, [33], 0x4242, "KRIS", dvs=0x9888)
    rec2 = crystal.record(geo.data[:32], geo.ot, geo.nick)
    store = pcdb.empty_store()
    store["boxes"][0] = [{"id": "geo", "origin": "crystal", "dex": 74, "level": 12, "nick": "GEODUDE", "ot": "KRIS",
                          "otid": 0x4242, "gen1": None, "gen2": pcdb._b64(rec2), "gen4": None},
                         {"id": "sen", "origin": "crystal", "dex": 161, "level": 5, "nick": "SENTRET", "ot": "KRIS",
                          "otid": 0x4242, "gen1": None, "gen2": pcdb._b64(crystal.record(
                              crystal.make_mon(crom, 161, 5, [33], 0x4242, "KRIS").data[:32], text.encode("KRIS", 11),
                              text.encode("SENTRET", 11))), "gen4": None}]
    store["linked"] = ["crystal"]
    path = tmp / "pc.json"
    os.environ["YL_DEV_LOAD_STATE"] = os.path.join(STATES, "pokecenter_pc.state")
    a = appmod.App(Cfg(tmp / "y", os.path.join(STATES, "yellow.gb")))
    os.environ.pop("YL_DEV_LOAD_STATE", None)
    a.pcsync = pcdb.PCSync(path)
    a.pcsync.working = json.loads(json.dumps(store))
    a.pcsync.committed = json.loads(json.dumps(store))
    for _ in range(60):
        a.step()
    pb, m = a.pyboy, a.pyboy.memory

    def owned(d):
        return bool(m[game.DEX_OWNED + (d - 1) // 8] >> ((d - 1) % 8) & 1)
    ok(a.full and not a.chal and isinstance(a.pc_codec, pcdb.Gen1PC) and not owned(74) and not owned(132),
       "Yellow App (main game, PC database on); GEODUDE and DITTO not owned")
    snap = io.BytesIO()
    pb.save_state(snap)
    before = Y_own_count(pa, Y, pb)
    snap.seek(0)
    pb.load_state(snap)
    pa.open_pc(pb)
    names = [n for _, n in pa.box(pb)]
    pa.close_pc(pb)
    ok("GEODUDE" in names and "?SENTRET" in names and owned(74) and not owned(132)
       and m[game.DEX_SEEN + (74 - 1) // 8] >> ((74 - 1) % 8) & 1,
       "Bill's PC shows the Crystal GEODUDE + ?SENTRET; the open hook set GEODUDE's owned + seen bits, the stand-in "
       "(DITTO) left out (box: %s)" % names)
    for _ in range(60):
        a.step()
    after = Y_own_count(pa, Y, pb)
    ok(before is not None and after == before + 1, "the game's own Pokédex screen: OWN %s -> %s" % (before, after))
    a.shutdown()


def Y_own_count(pa, Y, pb):
    m = pb.memory
    m[EVENT_FLAGS_Y + EVENT_GOT_POKEDEX // 8] |= 1 << (EVENT_GOT_POKEDEX % 8)
    for _ in range(60):
        Y.tick(pb, 1)
    pa._press(pb, "start")
    pa._settle(pb, 30)
    out = None
    for _ in range(4):
        pa._press(pb, "a", hold=6)
        Y.tick(pb, 120)
        lines = pa.screen_lines(pb)
        idx = next((i for i, ln in enumerate(lines) if "OWN" in ln), None)
        if idx is not None:
            out = int(lines[idx + 1].split(".")[-1].strip() or -1)
            break
    for _ in range(6):                                      # back out to the overworld
        pa._press(pb, "b", hold=6)
        Y.tick(pb, 60)
    return out


EVENT_FLAGS_Y = 0xD746        # wEventFlags
EVENT_GOT_POKEDEX = 37        # pret constants/event_constants.asm (index of EVENT_GOT_POKEDEX)


# =====================================================================================================================
# Crystal
# =====================================================================================================================
def crystal_checks():
    print("=== Crystal ===", flush=True)
    C = load_module("sl_crystal", os.path.join(REPO, "dev", "crystal", "soullink_verify.py"))
    from play import idle, warp
    from common import ROM as CROM, ticks
    rom = crystal.RomData.from_file(CROM)
    ok(game.sha1_of(CROM) == crystal.CRYSTAL_SHA1, "crystal.gbc is the verified ROM (%s)" % crystal.CRYSTAL_SHA1[:8])
    t = syms("pokecrystal.sym")
    check_syms("Crystal", t, [("wPokedexCaught", crystal.DEX_CAUGHT), ("wPlayerID", crystal.PLAYER_ID),
                              ("wPlayerName", crystal.PLAYER_NAME), ("wHallOfFameCount", crystal.HALL_OF_FAME_COUNT),
                              ("wStatusFlags", crystal.STATUS_FLAGS), ("wEventFlags", crystal.EVENT_FLAGS),
                              ("wPartyMons", crystal.PARTY_MONS), ("wPartyMonOTs", crystal.PARTY_OT),
                              ("wEnemyMonDVs", crystal.ENEMY_MON_DVS), ("wDayCareMan", crystal.DAYCARE_MAN),
                              ("wBreedMon1Nickname", crystal.BREED_MON1_NICK)])
    if t:
        ok(t["wEndPokedexCaught"][1] - t["wPokedexCaught"][1] == 32 and t["wPokedexCaught"][0] == 1,
           "wPokedexCaught: 32 bytes in WRAM bank 1")

    def load(st):
        pb = C.load(st)
        cm = crystal.CrystalMemory(pb.memory, rom)
        ticks(pb, 2)
        return pb, cm

    def dexbits(cm):
        return cm.read(crystal.DEX_CAUGHT, 32)

    def manual(bits):
        return sum(1 for d in range(1, 252) if d not in (151, 251) and bits[(d - 1) // 8] >> ((d - 1) % 8) & 1)

    for st in ("crystal_party.state", "crystal_room.state", "crystal_sl_caught.state", "crystal_sl_pc6.state",
               "crystal_sl_daycare.state", "crystal_sl_wild.state"):
        pb, cm = load(st)
        p = crystal.progress(cm)
        mine = [mn for mn in cm.party() if mn.ot_id == cm.player_id() and not mn.egg]
        owned = all(dexbits(cm)[(mn.species - 1) // 8] >> ((mn.species - 1) % 8) & 1 for mn in mine)
        ok(p is not None and not p["champion"] and p["shinies"] == [] and p["dex_total"] == 249 and owned
           and p["dex_caught"] == manual(dexbits(cm)),
           "%s: progress() champion False, no shinies, own party species caught, Johto %d/249 (manual count %d; "
           "owner %s/%d)" % (st, p and p["dex_caught"], manual(dexbits(cm)), p and p["owner"]["ot"],
                             p and p["owner"]["tid"]))
        pb.stop(False)

    # dex goal
    pb, cm = load("crystal_sl_caught.state")
    for d in range(1, 252):
        cm.w(crystal.DEX_CAUGHT + (d - 1) // 8, cm.r(crystal.DEX_CAUGHT + (d - 1) // 8) | 1 << ((d - 1) % 8))
    p1 = crystal.progress(cm)
    for d in (151, 251):
        cm.w(crystal.DEX_CAUGHT + (d - 1) // 8, cm.r(crystal.DEX_CAUGHT + (d - 1) // 8) & ~(1 << ((d - 1) % 8)))
    p2 = crystal.progress(cm)
    cm.w(crystal.DEX_CAUGHT + 30 // 8, cm.r(crystal.DEX_CAUGHT + 30 // 8) & ~(1 << (30 % 8)))   # #31 NIDOQUEEN
    p3 = crystal.progress(cm)
    ok((p1["dex_caught"], p1["dex_complete"], p2["dex_caught"], p2["dex_complete"], p3["dex_caught"],
        p3["dex_complete"]) == (249, True, 249, True, 248, False),
       "dex: all 251 -> 249/249 complete; without Mew + Celebi still complete; one missing -> 248, not complete")
    pb.stop(False)

    # champion: each saved proof, then the real Hall of Fame scene
    pb, cm = load("crystal_party.state")
    idle(pb)
    view = challenge_view.ChallengeView(fake_app(game=cm, G=crystal, gen2=True, ds=False, pyboy=pb, rom=rom))
    ok(not crystal.champion(cm) and not view.run_champion(), "no Hall of Fame: champion() / run_champion() False")
    res = []
    for what, addr, bit in (("wHallOfFameCount = 1", crystal.HALL_OF_FAME_COUNT, None),
                            ("wStatusFlags bit 6", crystal.STATUS_FLAGS, 0x40),
                            ("EVENT_BEAT_ELITE_FOUR", crystal.EVENT_FLAGS + crystal.EVENT_BEAT_ELITE_FOUR // 8,
                             1 << (crystal.EVENT_BEAT_ELITE_FOUR % 8))):
        old = cm.r(addr)
        cm.w(addr, 1 if bit is None else old | bit)
        res.append(crystal.champion(cm) and crystal.progress(cm)["champion"] and view.run_champion())
        cm.w(addr, old)
        res.append(not crystal.champion(cm))
    ok(all(res), "each saved proof alone (wHallOfFameCount, wStatusFlags bit 6, EVENT_BEAT_ELITE_FOUR) -> champion(), "
       "progress()['champion'], run_champion() True; cleared again -> False")
    warp(pb, 16, 8, 4, 13)
    ok(crystal.champion(cm) and cm.hall_of_fame_count() == 0, "warped into the HALL_OF_FAME room: champion() True")
    for _ in range(300):
        if cm.hall_of_fame_count():
            break
        C.tap(pb, "a", 4, 30)
    ok(cm.hall_of_fame_count() == 1 and cm.entered_hall_of_fame() and crystal.champion(cm),
       "the game's own HoF scene: wHallOfFameCount 1 + flags, champion() True (persists after reload: "
       "dev/crystal/soullink_verify.py)")
    pb.stop(False)

    # a real shiny catch: shiny DVs written into the wild mon at the battle menu, MASTER BALL
    pb, cm = load("crystal_sl_wild.state")
    before = crystal.progress(cm)
    cm.write(crystal.ENEMY_MON_DVS, SHINY_DVS)
    n0 = len(cm.party())
    C.throw_ball(pb, "MASTER BALL")
    ok(C.finish_battle(pb, cm), "caught the wild mon (DVs EA AA) with the game's MASTER BALL")
    party = cm.party_checked()
    caught = party[-1]
    p = crystal.progress(cm)
    key = badges.gen2_badge_key(caught.data)
    ok(len(party) == n0 + 1 and caught.data[21:23] == SHINY_DVS and caught.ot_id == cm.player_id()
       and caught.ot_str() == cm.player_name() and (caught.data[29] or caught.data[30]),
       "the caught %s: DVs EA AA, the player's OT name + ID, caught data %02X%02X written by the game"
       % (rom.species_name(caught.species), caught.data[29], caught.data[30]))
    rec = crystal.record(caught.data[:32], caught.ot, caught.nick)
    entry = crystal.Gen2PC(pb.memory, rom).entry_from_record(rec)
    ok(p["shinies"] == [key] and badges.entry_shiny_key(entry, "johto", p["owner"]) == key and before["shinies"] == [],
       "progress(): own shiny, badge key %s == the key of its PC-store entry" % key)
    snap = io.BytesIO()
    pb.save_state(snap)
    i = len(party) - 1
    variants = []
    for what, addr, data in (("OT name OTHER", crystal.PARTY_OT + i * 11, text.encode("OTHER", 11)),
                             ("another OT ID", crystal.PARTY_MONS + i * 48 + 6, bytes([caught.data[6] ^ 1])),
                             ("no caught data (a converted Gen-1 mon)", crystal.PARTY_MONS + i * 48 + 29, bytes(2)),
                             ("an egg", crystal.PARTY_SPECIES + i, bytes([crystal.EGG]))):
        snap.seek(0)
        pb.load_state(snap)
        cm.write(addr, data)
        variants.append((what, crystal.progress(cm)["shinies"] == []))
    ok(all(v for _, v in variants), "not counted: " + ", ".join(w for w, _ in variants))
    snap.seek(0)
    pb.load_state(snap)
    idle(pb)
    ok(cm.move_party_to_box(i) and crystal.progress(cm)["shinies"] == [key],
       "deposited (game-style move into sBox): still counted once")
    pc = crystal.Gen2PC(pb.memory, rom)
    boxes = pc.read_boxes()
    other = (pc.current_box() + 3) % 14
    r2 = bytearray(boxes[pc.current_box()][-1])
    r2[21] = 0xAA                                           # another own shiny (Atk 10)
    boxes[other].append(bytes(r2))
    pc.write_boxes(boxes)
    ok(crystal.progress(cm)["shinies"] == [key, badges.gen2_badge_key(bytes(r2))],
       "an own shiny written into SRAM box %d: found too" % (other + 1))
    pb.stop(False)

    pb, cm = load("crystal_sl_daycare.state")
    recs = cm.daycare_records()
    ok(len(recs) == 1 and crystal.progress(cm)["shinies"] == [], "Day Care state: one mon there, not shiny")
    lady = bool(cm.r(crystal.DAYCARE_LADY) & 1)
    struct_at = (crystal.BREED_MON2_NICK if lady else crystal.BREED_MON1_NICK) + 22
    note("the fixture's Day Care mon (wBreedMon%d) was made by RAM writes: caught data %s (a real catch has it)"
         % (2 if lady else 1, cm.read(struct_at + 29, 2).hex()))
    cm.write(struct_at + 21, SHINY_DVS)
    ok(crystal.progress(cm)["shinies"] == [], "DVs made shiny but no caught data: not counted (origin unknown)")
    cm.write(struct_at + 29, bytes([0x82, 0x02]))           # caught data like the real Route 29 catch above
    rec = cm.daycare_records()[0]
    own = rec[6:8] == cm.read(crystal.PLAYER_ID, 2)
    ok(own and crystal.progress(cm)["shinies"] == [badges.gen2_badge_key(rec)],
       "with caught data 8202 (wBreedMon%d struct +29): counted from the Day Care" % (2 if lady else 1))
    pb.stop(False)


# =====================================================================================================================
# Platinum
# =====================================================================================================================
def platinum_checks():
    print("=== Platinum ===", flush=True)
    S, rig = platinum_rig()
    from yellowlink import ndsrom, platinum_pc
    pm, d = rig.pm, rig.d
    nr = ndsrom.NDSRom(S.ROM)
    z = nr.narc("/application/zukanlist/zkn_data/zukan_data.narc")
    rom_list = struct.unpack("<%dH" % (z.size(12) // 2), z.member(12))
    ok(rom_list == badges.SINNOH_DEX, "badges.SINNOH_DEX == the ROM's Sinnoh dex list (zukan_data.narc member 12, "
       "%d species)" % len(rom_list))
    pz = nr.narc("/poketool/pl_pokezukan.narc")
    n2r = struct.unpack("<%dH" % (pz.size(0) // 2), pz.member(0))
    ok(sorted(s for s in range(len(n2r)) if n2r[s]) == sorted(badges.SINNOH_DEX)
       and all(n2r[s] == i + 1 for i, s in enumerate(badges.SINNOH_DEX)),
       "pl_pokezukan.narc (Pokemon_SinnohDexNumber): exactly these 210 species have a Sinnoh number, in this order")
    src = os.path.join(PRET_PT, "res", "pokemon", "sinnoh_pokedex.json")
    if os.path.exists(src):
        names = [ln.strip() for ln in open(os.path.join(PRET_PT, "generated", "species.txt")) if ln.strip()]
        idx = {n: i for i, n in enumerate(names)}
        lst = [idx[n] for n in json.load(open(src)) if n not in ("SPECIES_ARCEUS", "SPECIES_NONE", "SPECIES_EGG")]
        ok(tuple(lst) == badges.SINNOH_DEX, "pret res/pokemon/sinnoh_pokedex.json (minus Arceus) == SINNOH_DEX")

    def load(st):
        rig.load(st)
        for _ in range(240):
            p = P.progress(pm)
            if p is not None:
                return p
            d.run(1)
        return None

    def dexbits():
        return d.read(pm.save_body() + P.PAGE_POKEDEX + P.POKEDEX_CAUGHT, 64)

    def manual():
        b = dexbits()
        return sum(1 for s in range(1, 494) if n2r[s] and s != badges.MANAPHY and b[(s - 1) // 8] >> ((s - 1) % 8) & 1)

    for st in ("pt_town.dsst", "pt_pc.dsst", "pt_sl_route202.dsst", "pt_sl_wild.dsst", "pt_sl_caught.dsst",
               "pt_sl_party6.dsst", "pt_sl_pc6.dsst", "pt_sl_daycare.dsst"):
        p = load(st)
        sd = pm.save_data()
        pages = {}
        for i in range(38):
            pid, size, off, _c, blk = struct.unpack("<IIIHH", d.read(sd + 0x20024 + 16 * i, 16))
            pages[pid] = (off, size, blk)
        ok(p is not None and pages[7] == (P.PAGE_POKEDEX, 812, 0) and d.r32(pm.save_body() + P.PAGE_POKEDEX)
           == P.POKEDEX_MAGIC and not p["champion"] and p["shinies"] == [] and p["dex_total"] == 209
           and p["dex_caught"] == manual(),
           "%s: save page 7 (POKEDEX) at body+0x1328 (812 bytes, magic BEEFCAFE); progress() champion False, no "
           "shinies, Sinnoh %s/209 == count over the ROM's Sinnoh numbers (Manaphy left out) (owner %s/%s/%s)"
           % (st, p and p["dex_caught"], p and p["owner"]["ot"], p and p["owner"]["tid"], p and p["owner"]["sid"]))

    # a real catch sets the caught bit and makes an own mon (origin Platinum, OT name + TID + SID)
    p0 = load("pt_sl_wild.dsst")
    info = pm.battle_info()
    rig.throw()
    rig.finish()
    p1 = load_cur(pm, d)
    party = pm.party_checked()
    pk = pk4.PK4(pk4.decrypt(platinum_pc.normalise(party[-1][:136])[1]))
    ok(pk.species == info["dex"] and pk.origin_game == 12 and pk.ot_name == pm.player_name()
       and (pk.tid, pk.sid) == (pm.trainer_id(), pm.secret_id()),
       "real catch (MASTER BALL) of species %d: origin game 12 (Platinum), OT %s, TID/SID = the player's"
       % (pk.species, pk.ot_name))
    bit = dexbits()[(pk.species - 1) // 8] >> ((pk.species - 1) % 8) & 1
    ok(bit and p1["dex_caught"] >= p0["dex_caught"], "the game set its caught bit (Sinnoh %d -> %d)"
       % (p0["dex_caught"], p1["dex_caught"]))

    # a shiny wild mon caught for real: the wild record's PID made shiny for the player before the ball
    load("pt_sl_wild.dsst")
    dto = pm.battle_dto()
    tid, sid = pm.trainer_id(), pm.secret_id()
    app = pm._battle_app()
    bs = d.r32(app + 0x1C)
    targets = [d.r32(dto + P.DTO_PARTIES + 4), d.r32(bs + P.BS_PARTIES + 4)]
    new_pid = None
    for party_ptr in targets:
        at = party_ptr + 8
        _k, rec = platinum_pc.normalise(d.read(at, 136))
        plain = pk4.decrypt(bytes(rec) + d.read(at + 136, 100))
        wpk = pk4.PK4(plain)
        lo = wpk.pid & 0xFFFF
        new_pid = ((lo ^ tid ^ sid) << 16) | lo
        wpk.pid = new_pid
        d.write(at, wpk.encrypted())
    cx = d.r32(bs + P.BS_CTX)
    d.w32(cx + P.CTX_BATTLE_MONS + 1 * P.BATTLE_MON_SIZE + P.BM_PID, new_pid)
    ok(pm.battle_info()["shiny"], "wild mon's PID set to %08X: battle_info() shiny for TID %d / SID %d" %
       (new_pid, tid, sid))
    rig.throw()
    rig.finish()
    p = load_cur(pm, d)
    party = pm.party_checked()
    last = platinum_pc.normalise(party[-1][:136])[1]
    key = pk4.mon_key(last)
    ok(struct.unpack_from("<I", last, 0)[0] == new_pid and p["shinies"] == [key]
       and key == platinum_pc.Gen4PC.key(last),
       "caught it: the party mon has that PID; progress() counts it as an own shiny, key %s == PC database key" % key)
    view = challenge_view.ChallengeView(fake_app(game=pm, G=P, gen2=False, ds=True, pyboy=None, rom=None))
    ok(not view.run_champion(), "run_champion() False (no Hall of Fame in this save)")

    # variations of that record (written into the party, the boxes and the Day Care)
    base = pk4.PK4(pk4.decrypt(last))
    lo2 = (new_pid & 0xFFFF) ^ 0x1234                       # another mon (own key), still shiny for TID/SID
    pid2 = ((lo2 ^ tid ^ sid) << 16) | lo2
    base.pid = pid2

    def variant(**kw):
        v = pk4.PK4(base.plain())
        for k, val in kw.items():
            setattr(v, k, val)
        return v.box_bytes()
    slot_box, slot_i = 5, 7
    cases = [("own shiny", variant(), True),
             ("OT name OTHER", variant(ot_codes=ndsrom.g4_encode("OTHER", 8)), False),
             ("another SID", variant(sid=sid ^ 0x100, pid=pid2 ^ (0x100 << 16)), False),
             ("origin FireRed (converted Gen-1 mon)", variant(origin_game=4), False),
             ("an egg", variant(is_egg=True), False)]
    res = []
    for what, rec, want in cases:
        pm.write_box_slot(slot_box, slot_i, rec)
        d.run(10)
        got = P.progress(pm)
        res.append((what, (pk4.mon_key(rec) in got["shinies"]) == want))
    ok(all(r for _, r in res), "box %d slot %d: %s" % (slot_box + 1, slot_i + 1,
                                                       ", ".join("%s %s" % (w, "ok" if r else "WRONG") for w, r in res)))
    pm.write_box_slot(slot_box, slot_i, pk4.EMPTY_BOX)

    load("pt_sl_daycare.dsst")
    day = pm.daycare_records()
    ok(len(day) >= 1 and P.progress(pm)["shinies"] == [], "Day Care state: %d mon(s) there, none shiny" % len(day))
    dpk = pk4.PK4(pk4.decrypt(day[0]))
    lo = dpk.pid & 0xFFFF
    dpk.pid = ((lo ^ dpk.tid ^ dpk.sid) << 16) | lo
    d.write(pm.save_body() + P.PAGE_DAYCARE, dpk.box_bytes())
    d.run(10)
    own = dpk.origin_game == 12 and dpk.ot_name == pm.player_name() and dpk.tid == tid
    ok(own and P.progress(pm)["shinies"] == [pk4.mon_key(dpk.box_bytes())],
       "the Day Care mon's PID made shiny (it is an own catch): counted from the Day Care")

    # dex goal
    load("pt_sl_caught.dsst")
    at = pm.save_body() + P.PAGE_POKEDEX + P.POKEDEX_CAUGHT
    b = bytearray(dexbits())
    for s in badges.SINNOH_DEX:
        b[(s - 1) // 8] |= 1 << ((s - 1) % 8)
    d.write(at, bytes(b))
    pa_ = P.progress(pm)
    b[(badges.MANAPHY - 1) // 8] &= ~(1 << ((badges.MANAPHY - 1) % 8))
    d.write(at, bytes(b))
    pb_ = P.progress(pm)
    b[(387 - 1) // 8] &= ~(1 << ((387 - 1) % 8))
    d.write(at, bytes(b))
    pc_ = P.progress(pm)
    ok((pa_["dex_caught"], pa_["dex_complete"], pb_["dex_caught"], pb_["dex_complete"], pc_["dex_caught"],
        pc_["dex_complete"]) == (209, True, 209, True, 208, False),
       "dex: all 210 Sinnoh species caught -> 209/209 complete; without MANAPHY still complete; without TURTWIG "
       "too -> 208, not complete")

    # champion: the saved proofs, then the real Hall of Fame scene
    load("pt_sl_pc6.dsst")
    ok(not P.champion(pm), "no Hall of Fame: champion() False")
    S.set_flag(rig, P.FLAG_GAME_COMPLETED, True)
    a1 = P.champion(pm) and P.progress(pm)["champion"] and view.run_champion()
    S.set_flag(rig, P.FLAG_GAME_COMPLETED, False)
    a2 = not P.champion(pm)
    t = pm.save_body() + P.TRAINER + 0x1D
    old = d.r8(t)
    d.w8(t, old | 1)
    a3 = P.champion(pm)
    d.w8(t, old)
    ok(a1 and a2 and a3 and not P.champion(pm), "FLAG_GAME_COMPLETED (2404) alone -> champion()/progress()/"
       "run_champion() True; cleared -> False; TrainerInfo.isMainStoryCleared alone -> True")
    S.warp_patch(rig, P.HALL_OF_FAME_MAP, 0)
    rig.step("down", 3)
    rig.step("left", 4)
    rig.step("down", 6)
    seen_room, first = False, None
    for f in range(12000):
        if pm.in_hall_of_fame():
            seen_room = seen_room or P.champion(pm)
        if pm.entered_hall_of_fame() and first is None:
            first = f
        if first is not None and f > first + 600:
            break
        if f % 60 == 0:
            rig.hold("a", 6)
        else:
            d.run(1)
    ok(seen_room and first is not None and P.champion(pm) and pm.hall_of_fame_count() == 1,
       "the game's own Hall of Fame scene (warp trick): champion() True in the room, then ClearGame's saved proof "
       "(%d frames in), HoF count 1 (survives power-off + CONTINUE: dev/platinum/soullink_verify.py hof)" % (first or -1))


def crystal_dexmark():
    """Gen2PC.mark_dex (= pret SetSeenAndCaughtMon) gives the same Pokédex bytes as the game's own catch code."""
    print("=== Crystal: Pokédex marking == the game's own ===", flush=True)
    C = load_module("sl_crystal2", os.path.join(REPO, "dev", "crystal", "soullink_verify.py"))
    from common import ROM as CROM, ticks
    rom = crystal.RomData.from_file(CROM)
    pb = C.load("crystal_sl_wild.state")
    cm = crystal.CrystalMemory(pb.memory, rom)
    ticks(pb, 2)
    sp = cm.r(crystal.TEMP_ENEMY_MON_SPECIES)
    for base in (crystal.DEX_CAUGHT, crystal.DEX_SEEN):          # make it a species the save doesn't have yet
        a = base + (sp - 1) // 8
        cm.w(a, cm.r(a) & ~(1 << ((sp - 1) % 8)))
    before = cm.read(crystal.DEX_CAUGHT, 64)
    C.throw_ball(pb, "MASTER BALL")
    ok(C.finish_battle(pb, cm), "caught the wild %s with the game's MASTER BALL" % rom.species_name(sp))
    game_after = cm.read(crystal.DEX_CAUGHT, 64)
    caught = cm.party()[-1]
    cm.write(crystal.DEX_CAUGHT, before)
    pc = crystal.Gen2PC(pb.memory, rom)
    new = pc.mark_dex([[crystal.record(caught.data[:32], caught.ot, caught.nick)]])
    ok(new == [sp] and cm.read(crystal.DEX_CAUGHT, 64) == game_after,
       "mark_dex on the dex as it was before the catch == the game's own caught + seen bytes afterwards (64 bytes)")
    letters = [crystal.unown_letter(v) for v in range(0x10000)]
    ok(min(letters) == 1 and max(letters) == 26, "unown_letter (pret GetUnownLetter) gives letters 1-26")
    cm.write(crystal.UNOWN_DEX, bytes(26))
    cm.w(crystal.FIRST_UNOWN_SEEN, 0)
    un = crystal.make_mon(rom, crystal.UNOWN, 10, [237], 0x4242, "KRIS", dvs=0xFFFF)
    egg = crystal.make_mon(rom, 25, 5, [33], 0x4242, "KRIS")
    pc.mark_dex([[crystal.record(un.data[:32], un.ot, un.nick),
                  crystal.record(egg.data[:32], egg.ot, egg.nick, egg=True)]])
    want = crystal.unown_letter(0xFFFF)
    ok(cm.read(crystal.UNOWN_DEX, 2) == bytes([want, 0]) and cm.r(crystal.FIRST_UNOWN_SEEN) == want
       and cm.r(crystal.DEX_CAUGHT + 200 // 8) >> (200 % 8) & 1,
       "an UNOWN arriving: caught + wUnownDex gets its letter (%d) + wFirstUnownSeen (UpdateUnownDex); "
       "an egg in the view is left out" % want)
    ok(not (cm.r(crystal.DEX_CAUGHT + 24 // 8) >> (24 % 8)) & 1, "the PIKACHU egg in that view didn't mark PIKACHU")
    pb.stop(False)


def platinum_dexmark():
    """platinum.pokedex_capture (port of pret Pokedex_Capture) == what the game's own catch writes."""
    print("=== Platinum: Pokédex marking == the game's own ===", flush=True)
    S, rig = platinum_rig()
    from yellowlink import ndsrom, platinum_pc
    pm, d = rig.pm, rig.d
    rom = ndsrom.platinum(S.ROM)
    rig.load("pt_sl_wild.dsst")
    load_cur(pm, d)
    body = pm.save_body()
    blk = d.read(body + P.PAGE_POKEDEX, P.POKEDEX_SIZE)
    ok(blk[P.DEX_UNOWN:P.DEX_UNOWN + 28] == b"\xff" * 28 and blk[P.DEX_SHELLOS:P.DEX_WORMADAM + 1] == b"\xff" * 4
       and struct.unpack_from("<I", blk, P.DEX_ROTOM)[0] == 0xFFFFFFFF and blk[P.DEX_SHAYMIN:P.DEX_GIRATINA + 1]
       == b"\xff\xff" and blk[P.POKEDEX_CAUGHT + 63] == 0xFF and blk[P.POKEDEX_SEEN + 63] == 0xFF
       and blk[P.DEX_LANGUAGES + 387] == 1 << P.DEX_LANGUAGE_ORDER.index(2),
       "Pokedex layout on the ROM: unown forms FF x 28 @0x10C, Shellos/Gastrodon/Burmy/Wormadam FF @0x108, Rotom "
       "FFFFFFFF @0x31C, Shaymin/Giratina FF @0x320, Deoxys nibbles F in caught/seen[15], TURTWIG's language = "
       "English bit @0x128 + 387")
    info = pm.battle_info()

    def page_checksums():
        sd = pm.save_data()
        return {i: struct.unpack("<IIIHH", d.read(sd + 0x20024 + 16 * i, 16))[3] for i in (2, 7, 37)}
    sums0 = page_checksums()
    boxes0 = d.read(body + P.PAGE_PC_BOXES, 0x200)
    rig.throw()
    rig.finish()
    load_cur(pm, d)
    after = d.read(body + P.PAGE_POKEDEX, P.POKEDEX_SIZE)
    ok(page_checksums() == sums0 and after != blk,
       "the game doesn't maintain the page-table checksums while playing: party/Pokédex/PC-boxes entries %s stay "
       "the same through a real catch although the Pokédex page changed (pret: SavePageInfo.checksum is only "
       "computed at load, never compared; block footers are recomputed on save) -> mark_dex / write_boxes have "
       "nothing to fix up" % sums0)
    note("(boxes page changed during the catch: %s)" % (d.read(body + P.PAGE_PC_BOXES, 0x200) != boxes0))
    party = pm.party_checked()
    rec = platinum_pc.normalise(party[-1][:136])[1]
    mine = bytearray(blk)
    new = P.pokedex_capture(mine, rec, rom)
    ok(new and bytes(mine) == after and after != blk,
       "real catch of species %d: pokedex_capture() on the block from before the catch == the game's own block "
       "afterwards (all %d bytes: caught, seen, gender, language)" % (info["dex"], P.POKEDEX_SIZE))
    # a converted Crystal UNOWN arriving in the PC view (Gen4PC.write_boxes + mark_dex, like the app's open hook)
    rig.load("pt_pc.dsst")
    load_cur(pm, d)
    crom = crystal.RomData.from_file(os.path.join(STATES, "crystal.gbc"))
    un = crystal.make_mon(crom, crystal.UNOWN, 10, [237], 0x4242, "KRIS", dvs=0xFFFF)
    g4 = pk4.from_gen2(crystal.record(un.data[:32], un.ot, un.nick), rom)
    form = pk4.PK4(pk4.decrypt(g4)).form
    standin = pk4.standin({"id": "x", "nick": "TOGEPI", "level": 5}, rom)
    codec = platinum_pc.Gen4PC(pm, rom)
    boxes = [[] for _ in range(14)]
    boxes[0] = [g4, standin]
    codec.write_boxes(boxes)
    new = codec.mark_dex(boxes)
    blk2 = d.read(pm.save_body() + P.PAGE_POKEDEX, P.POKEDEX_SIZE)
    ok(new == [201] and blk2[P.DEX_UNOWN] == form and blk2[P.DEX_UNOWN + 1] == 0xFF
       and blk2[P.POKEDEX_CAUGHT + 200 // 8] >> (200 % 8) & 1 and not blk2[P.POKEDEX_CAUGHT + 131 // 8] >> (131 % 8) & 1,
       "a converted Crystal UNOWN written into box 1: caught + its form (%d) in unownFormsSeen; the ?TOGEPI "
       "stand-in (DITTO) left out" % form)
    d.run(30)
    ok(P.progress(pm) is not None, "the game keeps running with the updated Pokédex (progress() readable)")


_PT = []


def platinum_rig():
    """(dev/platinum/soullink_verify module, its Rig) - created once: only ONE DeSmuME can exist per process."""
    if not _PT:
        S = load_module("sl_platinum", os.path.join(REPO, "dev", "platinum", "soullink_verify.py"))
        _PT.extend([S, S.Rig()])
    return _PT[0], _PT[1]


def load_cur(pm, d):
    for _ in range(240):
        p = P.progress(pm)
        if p is not None:
            return p
        d.run(1)
    return None


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or ["yellow", "crystal", "platinum"]
    for name, fn in (("yellow", yellow), ("yellow", yellow_dexmark), ("crystal", crystal_checks),
                     ("crystal", crystal_dexmark), ("platinum", platinum_checks), ("platinum", platinum_dexmark)):
        if name in names:
            fn()
    bad = [w for c, w in RESULTS if not c]
    print("\n%d checks, %d failed" % (len(RESULTS), len(bad)))
    if bad:
        for w in bad:
            print("FAILED: " + w)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
