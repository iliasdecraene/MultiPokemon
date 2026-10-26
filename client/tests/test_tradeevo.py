"""Soul Link trade evolutions (tradeevo.py, SPEC.md section 7): which party mons, the evolution in each game's RAM
(fake memories, no ROM needed), the Soul Link bookkeeping and the client flow."""
import os
import struct
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from yellowlink import crystal, game, ndsrom, pk4, platinum as P, text, tradeevo  # noqa: E402
from yellowlink.soullink import SoulLink, family  # noqa: E402
import test_game as tg  # noqa: E402
import test_crystal as tc  # noqa: E402
import test_pk4 as tp  # noqa: E402
import test_soullink_platinum as tsp  # noqa: E402


# --- Yellow ----------------------------------------------------------------------------------------------------
def yellow(party):
    mem = bytearray(0x10000)
    g = game.GameMemory(mem, tg.fake_rom())
    mem[game.PLAYER_PIC] = 1
    mem[game.PLAYER_NAME] = 0x80
    mem[game.PARTY_COUNT] = len(party)
    for i, m in enumerate(party):
        g.write_party_slot(i, m)
    mem[game.PARTY_SPECIES + len(party)] = 0xFF
    return g


def test_yellow_options_and_evolve_keeps_identity():
    pika = tg.make_mon(0x54, nick="PIKACHU", ot="ILIAS")
    kad = tg.make_mon(0x26, hp=30, max_hp=40, nick="KADABRA", ot="ILIAS")
    kd = bytearray(kad.data)
    kd[7] = 100                                          # catch-rate byte (part of the Yellow Soul Link key)
    kad = game.Mon(bytes(kd), kad.ot, kad.nick)
    g = yellow([pika, kad])
    opts = tradeevo.options("gen1", g, g.rom)
    assert [(o.slot, o.dex, o.into, o.name, o.into_name, o.ok) for o in opts] == \
        [(1, 64, 65, "Kadabra", "Alakazam", True)]
    key = game.mon_key(kad.data)
    assert opts[0].key == key
    done = tradeevo.evolve("gen1", g, g.rom, key)
    assert done.into_name == "Alakazam" and done.new_nick == "ALAKAZAM" and done.label() == "Kadabra"
    new = g.party()[1]
    assert new.species == 0x95 and g.m[game.PARTY_SPECIES + 1] == 0x95
    assert game.mon_key(new.data) == key                  # same Soul Link key
    assert new.ot == kad.ot and new.data[12:14] == kad.data[12:14]     # still the owner's: same OT name + ID
    assert new.max_hp - new.hp == 10                      # damage carried over
    assert g.m[game.DEX_OWNED + 64 // 8] & (1 << (64 % 8))      # Alakazam (dex 65) owned
    assert tradeevo.options("gen1", g, g.rom) == []      # nothing left to evolve
    assert g.party()[0] == pika


def test_yellow_nickname_kept_and_missing_mon():
    kad = tg.make_mon(0x26, nick="SPOONY", ot="ILIAS")
    g = yellow([kad])
    key = game.mon_key(kad.data)
    assert tradeevo.options("gen1", g, g.rom)[0].label() == "SPOONY (Kadabra)"
    assert tradeevo.evolve("gen1", g, g.rom, key).new_nick == "SPOONY"
    with pytest.raises(tradeevo.EvoFailed, match="not in the party"):
        tradeevo.evolve("gen1", g, g.rom, "ffffffffff")


def test_yellow_half_written_party_waits():
    g = yellow([tg.make_mon(0x26)])
    g.m[game.PARTY_SPECIES + 1] = 0x00                   # terminator missing: the game is mid-update
    assert tradeevo.options("gen1", g, g.rom) is None
    assert tradeevo.evolve("gen1", g, g.rom, "x") is None


def test_dead_mons_are_listed_but_not_ok():
    kad = tg.make_mon(0x26)
    g = yellow([kad])
    key = game.mon_key(kad.data)
    sl = SoulLink("me", "you", ledger=[{"seq": 1, "p": "me", "t": "enc", "area": "A", "dex": 63,
                                        "status": "caught", "key": key},
                                       {"seq": 2, "p": "me", "t": "dead", "key": key}])
    o = tradeevo.options("gen1", g, g.rom, sl)[0]
    assert not o.ok and o.why == "lost in this run"


# --- Crystal ---------------------------------------------------------------------------------------------------
class CrystalRom(tc.EvoRom):
    BASE = {**tc.EvoRom.BASE, 93: (45, 50, 45, 95, 115, 55, 7, 3), 94: (60, 65, 60, 110, 130, 75, 7, 3)}

    def species_name(self, s):
        return {93: "HAUNTER", 94: "GENGAR"}.get(s) or super().species_name(s)


def crystal_world(party):
    mem, cm = tc.in_game_mem()
    cm.rom = CrystalRom()
    cm.set_party(party)
    return cm


def test_crystal_items_everstone_and_evolve():
    rom = CrystalRom()
    onix = crystal.make_mon(rom, 95, 20, [33], 1, "ILIAS", nick="ROCKY", item=crystal.METAL_COAT)
    bare = crystal.make_mon(rom, 95, 21, [33], 2, "ILIAS")
    stone = crystal.make_mon(rom, 64, 20, [93], 3, "ILIAS", nick="KADABRA", item=crystal.EVERSTONE)
    haunter = crystal.make_mon(rom, 93, 25, [33], 4, "ILIAS", nick="HAUNTER")
    cm = crystal_world([onix, bare, stone, haunter])
    opts = tradeevo.options("gen2", cm, rom)
    got = [(o.slot, o.name, o.into_name, o.ok, o.why, o.item) for o in opts]
    assert got == [(0, "Onix", "Steelix", True, "", "Metal Coat"),
                   (1, "Onix", "Steelix", False, "needs to hold a Metal Coat", "Metal Coat"),
                   (2, "Kadabra", "Alakazam", False, "holds an Everstone", None),
                   (3, "Haunter", "Gengar", True, "", None)]
    key = crystal.mon_key(onix.data)
    done = tradeevo.evolve("gen2", cm, rom, key)
    assert done.new_nick == "ROCKY" and done.item == "Metal Coat" and done.label() == "ROCKY (Onix)"
    new = cm.party()[0]
    assert new.species == 208 and new.item == 0           # the Metal Coat is used up
    assert crystal.mon_key(new.data) == key and new.ot == onix.ot and new.data[6:8] == onix.data[6:8]
    assert cm.r(crystal.PARTY_SPECIES) == 208
    with pytest.raises(tradeevo.EvoFailed, match="Everstone"):
        tradeevo.evolve("gen2", cm, rom, crystal.mon_key(stone.data))
    with pytest.raises(tradeevo.EvoFailed, match="Metal Coat"):
        tradeevo.evolve("gen2", cm, rom, crystal.mon_key(bare.data))
    assert tradeevo.evolve("gen2", cm, rom, crystal.mon_key(haunter.data)).new_nick == "GENGAR"


def test_crystal_eggs_never():
    rom = CrystalRom()
    egg = crystal.make_mon(rom, 64, 5, [93], 1, "ILIAS")
    egg = crystal.Mon(egg.data, egg.ot, egg.nick, egg=True)
    cm = crystal_world([crystal.make_mon(rom, 95, 20, [33], 2, "ILIAS"), egg])
    assert [o.slot for o in tradeevo.options("gen2", cm, rom)] == [0]


# --- Platinum --------------------------------------------------------------------------------------------------
class PtRom(tp.FakeRom):
    """Fake Platinum data with a few trade evolutions (ids like the ROM: Metal Coat 233, Electirizer 322...)."""
    MORE = {95: (5, 127, (69, 5), "ONIX", (35, 45, 160, 70, 30, 45)),
            208: (5, 127, (69, 5), "STEELIX", (75, 85, 200, 30, 55, 65)),
            125: (5, 63, (9, 0), "ELECTABUZZ", (65, 83, 57, 105, 95, 85)),
            466: (5, 63, (78, 0), "ELECTIVIRE", (75, 123, 67, 95, 95, 85)),
            366: (1, 127, (75, 0), "CLAMPERL", (35, 64, 85, 32, 74, 55)),
            367: (1, 127, (33, 0), "HUNTAIL", (55, 104, 105, 52, 94, 75)),
            368: (1, 127, (33, 0), "GOREBYSS", (55, 84, 105, 52, 114, 75))}
    ITEMS = {233: "Metal Coat", 322: "Electirizer", 226: "DeepSeaTooth", 227: "DeepSeaScale", 229: "Everstone"}

    def _sp(self, dex):
        return self.MORE.get(dex) or tp.SPECIES.get(dex)

    def growth_rate(self, dex):
        return self._sp(dex)[0] if self._sp(dex) else 0

    def gender_ratio(self, dex):
        return self._sp(dex)[1] if self._sp(dex) else 255

    def abilities(self, dex):
        return self._sp(dex)[2] if self._sp(dex) else (1, 0)

    def species_name(self, dex):
        return self._sp(dex)[3] if self._sp(dex) else "MON%d" % dex

    def base_stats(self, dex):
        return self._sp(dex)[4] if self._sp(dex) else (50,) * 6

    def trade_evolutions(self):
        return {64: [(65, None)], 95: [(208, 233)], 125: [(466, 322)], 366: [(367, 226), (368, 227)]}

    def item_name(self, item):
        return self.ITEMS.get(item, "?")


PTROM = PtRom()


def pt_mon(i, species, level=30, item=0, nick=None, hp_lost=0, pid=None):
    box = tp.native(species=species, level=level, pid=pid if pid is not None else 0x20000 + i * 7919,
                    tid=tsp.TID, sid=tsp.SID, item=item, nick=nick)
    full = pk4.PK4(pk4.decrypt(pk4.to_party(box, PTROM)))
    full.hp = full.stats[0] - hp_lost
    return full.encrypted()


def pt_world(party):
    bus, pm = tsp.world(party, rom=PTROM)
    body = pm.save_body()
    bus.w32(body + P.PAGE_POKEDEX, P.POKEDEX_MAGIC)       # an (empty) Pokédex page
    return bus, pm


def test_platinum_options():
    party = [pt_mon(0, 95, item=233, nick="ROCKY"), pt_mon(1, 125), pt_mon(2, 64, item=229), pt_mon(3, 366, item=227),
             pt_mon(4, 387)]
    _bus, pm = pt_world(party)
    got = [(o.slot, o.name, o.into_name, o.ok, o.why, o.item) for o in tradeevo.options("gen4", pm, PTROM)]
    assert got == [(0, "Onix", "Steelix", True, "", "Metal Coat"),
                   (1, "Electabuzz", "Electivire", False, "needs to hold an Electirizer", "Electirizer"),
                   (2, "Kadabra", "Alakazam", False, "holds an Everstone", None),
                   (3, "Clamperl", "Gorebyss", True, "", "DeepSeaScale")]
    assert tradeevo.options("gen4", pm, PTROM)[0].label() == "ROCKY (Onix)"


def test_platinum_evolve_item_case():
    rec = pt_mon(0, 95, item=233, hp_lost=7, pid=0x20001)            # odd PID: second ability slot
    turtwig = pt_mon(1, 387)
    bus, pm = pt_world([rec, turtwig])
    key = P.mon_key(rec)
    old = pk4.PK4(pk4.decrypt(rec))
    done = tradeevo.evolve("gen4", pm, PTROM, key)
    assert done.into == 208 and done.item == "Metal Coat" and done.new_nick == "STEELIX"
    party = pm.party_checked()
    assert party is not None and len(party) == 2 and party[1] == turtwig
    new = pk4.PK4(pk4.decrypt(party[0]))
    assert new.species == 208 and new.item == 0 and new.ability == 5           # (69, 5)[pid & 1]
    assert P.mon_key(party[0]) == key and new.pid == old.pid and new.tid == old.tid and new.sid == old.sid
    assert new.ot_codes == old.ot_codes and new.origin_game == old.origin_game and new.exp == old.exp
    assert new.nickname == "STEELIX" and not new.has_nickname
    assert new.party_level == old.party_level                                   # same level
    assert new.stats == pk4.calc_stats(new, PTROM, old.party_level) and new.stats[0] - new.hp == 7   # damage kept
    bits = P.pokedex_caught_bits(pm)
    assert bits[(208 - 1) // 8] & (1 << ((208 - 1) % 8))                  # Steelix caught in the Pokédex


def test_platinum_evolve_plain_nicknamed_and_fainted():
    kad = pt_mon(0, 64, nick="SPOONY")
    fainted = pt_mon(1, 64)
    pk = pk4.PK4(pk4.decrypt(fainted))
    pk.hp = 0
    fainted = pk.encrypted()
    bus, pm = pt_world([kad, fainted])
    assert tradeevo.evolve("gen4", pm, PTROM, P.mon_key(kad)).new_nick == "SPOONY"
    tradeevo.evolve("gen4", pm, PTROM, P.mon_key(fainted))
    a, b = (pk4.PK4(pk4.decrypt(r)) for r in pm.party_checked())
    assert a.species == b.species == 65 and a.nickname == "SPOONY" and a.has_nickname
    assert b.hp == 0                                   # a fainted mon stays fainted (Pokemon_CalcStats)
    assert b.nickname == "ALAKAZAM"


# --- Soul Link bookkeeping -------------------------------------------------------------------------------------
def test_evo_event_keeps_the_link_and_the_rules():
    led = [{"seq": 1, "p": "me", "t": "enc", "area": "R5", "dex": 63, "status": "caught", "key": "k1"},
           {"seq": 2, "p": "you", "t": "enc", "area": "R5", "dex": 66, "status": "caught", "key": "k2"},
           {"seq": 3, "p": "me", "t": "evo", "key": "k1", "dex": 64, "into": 65, "nick": "KADABRA", "evo": "e1"}]
    sl = SoulLink("me", "you", ledger=led)
    assert sl.partner_of("k1") == "k2" and sl.status("k1") == "linked"
    assert sl.pairs() == [("R5", "k1", "k2", "caught", "caught", 65, 66)]        # shows Alakazam now
    assert sl.is_dupe(64) and family(65) == family(63)                            # dupes clause: same family
    sl.add({"seq": 4, "p": "you", "t": "dead", "key": "k2", "why": "fainted"})
    assert sl.needed_deaths() == [{"t": "dead", "key": "k1", "why": "partner fainted"}]
    # the partner's view: its evolved mon on the other side
    sl2 = SoulLink("you", "me", ledger=led)
    assert sl2.pairs()[0][5:] == (66, 65)


# --- the flow ----------------------------------------------------------------------------------------------------
class FakeNet:
    def __init__(self):
        self.sent, self.status = [], "online"

    def send(self, msg):
        if self.status != "online":
            return False
        self.sent.append(msg)
        return True

    def take(self, t):
        out = [m for m in self.sent if m["type"] == t]
        self.sent = [m for m in self.sent if m["type"] != t]
        return out


class FlowApp(tradeevo.TradeEvoMixin):
    def __init__(self, g, me="me", partner="you"):
        self.init_tradeevo()
        self.game, self.rom, self.gen2, self.ds, self.full = g, g.rom, False, False, True
        self.net = FakeNet()
        self.cfg = {"uid": me}
        self.chal = {"id": "c1", "game": "yellow"}
        self.sl = SoulLink(me, partner)
        self.referee = None
        self.chal_wait = None
        self.chal_popup = None
        self.prompt = None
        self.mode = "play"
        self.frame = 0
        self.server_features = {"badges", "trade_evo"}
        self.roster = [{"uid": partner, "online": True, "challenge_rules": ["exp_share", "trade_evo"]}]
        self.partner = partner
        self.toasts, self.notes = [], []
        self._chal_leaving = False
        self.saves = []                      # autosave(force) calls: (force, net messages sent before it)

    def autosave(self, force=False):
        self.saves.append((force, [m["type"] for m in self.net.sent]))
        return True

    def chal_partner(self, rec=None):
        return self.partner, "Alex"

    def toast(self, s, secs=4, color=None):
        self.toasts.append(s)

    def _chal_notify(self, s, kind="info"):
        self.notes.append(s)

    def close_panel(self):
        self.evo_pick = False

    def release_all(self):
        pass

    def _maybe_show_prompt(self):
        if self.prompt and self.mode == "play":
            self.mode = "prompt"

    def dex_name(self, dex):
        return {64: "Kadabra", 65: "Alakazam"}.get(dex, f"#{dex}")


def test_flow_requester_side():
    kad = tg.make_mon(0x26, nick="KADABRA")
    app = FlowApp(yellow([kad]))
    opt = app.tradeevo_options()[0]
    assert app.tradeevo_blocker() == ""
    app.request_tradeevo(opt)
    req = app.net.take("challenge_evo_request")[0]
    assert req["key"] == game.mon_key(kad.data) and (req["dex"], req["into"]) == (64, 65) and req["item"] is None
    assert app.mode == "evo_wait" and app.evo_out["state"] == "asking"
    assert "going on" in app.tradeevo_blocker()
    app.tradeevo_step()                                  # nothing happens before the partner's yes
    assert app.game.party()[0].species == 0x26
    app.on_challenge_evo_go({"challenge_id": "c1", "evo_id": "other"})      # not ours: ignored
    assert app.evo_out["state"] == "asking"
    app.on_challenge_evo_go({"challenge_id": "c1", "evo_id": req["evo_id"]})
    app.game.m[game.FONT_LOADED] = 1                     # a text box: not safe, wait
    app.tradeevo_step()
    assert app.game.party()[0].species == 0x26 and not app.net.sent
    app.game.m[game.FONT_LOADED] = 0
    app.tradeevo_step()
    assert app.game.party()[0].species == 0x95
    done = app.net.take("challenge_evo_done")[0]
    assert done == {"type": "challenge_evo_done", "challenge_id": "c1", "evo_id": req["evo_id"], "ok": True}
    assert app.mode == "play" and app.notes == ["Kadabra evolved into Alakazam!"]
    assert app.evo_out["state"] == "applied"
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": req["evo_id"], "ok": True, "reason": "evolved"})
    assert app.evo_out is None and not app.toasts


def test_flow_requester_cancel_decline_and_offline_done():
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    app.request_tradeevo(app.tradeevo_options()[0])
    eid = app.net.take("challenge_evo_request")[0]["evo_id"]
    app.cancel_tradeevo()
    assert app.net.take("challenge_evo_done")[0]["ok"] is False and app.evo_out is None and app.mode == "play"
    # declined
    app.request_tradeevo(app.tradeevo_options()[0])
    eid = app.net.take("challenge_evo_request")[0]["evo_id"]
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": eid, "ok": False, "reason": "declined"})
    assert app.evo_out is None and app.toasts[-1] == "Trade evolution: Alex said no."
    # the done can't go out (connection drop right after the write): resent when back
    app.request_tradeevo(app.tradeevo_options()[0])
    eid = app.net.take("challenge_evo_request")[0]["evo_id"]
    app.on_challenge_evo_go({"evo_id": eid})
    app.net.status = "offline"
    app.tradeevo_step()
    assert app.game.party()[0].species == 0x95 and app.evo_out["unsent"]
    app.tradeevo_step()                                  # still offline: kept
    app.net.status = "online"
    app.tradeevo_reset()                                 # (challenge_state after the reconnect)
    app.tradeevo_step()
    assert app.net.take("challenge_evo_done")[0]["ok"] is True and app.evo_out["unsent"] is None
    app.on_challenge_evo_end({"evo_id": eid, "ok": False, "reason": "expired"})   # server had it already
    assert app.evo_out is None and not app.toasts[-1].startswith("Couldn't")


def test_flow_requester_failures():
    kad = tg.make_mon(0x26)
    app = FlowApp(yellow([kad]))
    app.request_tradeevo(app.tradeevo_options()[0])
    eid = app.net.take("challenge_evo_request")[0]["evo_id"]
    app.on_challenge_evo_go({"evo_id": eid})
    app.game.write_party_slot(0, tg.make_mon(0x54, nick="PIKACHU"))          # it's gone from the party
    app.tradeevo_step()
    done = app.net.take("challenge_evo_done")[0]
    assert done["ok"] is False and "party" in done["why"] and app.evo_out is None
    # no quiet moment within the time limit
    app.game.write_party_slot(0, kad)
    app.request_tradeevo(app.tradeevo_options()[0])
    eid = app.net.take("challenge_evo_request")[0]["evo_id"]
    app.on_challenge_evo_go({"evo_id": eid})
    app.game.m[game.FONT_LOADED] = 1
    app.evo_out["since"] = time.time() - tradeevo.APPLY_WITHIN - 1
    app.tradeevo_step()
    assert app.net.take("challenge_evo_done")[0]["ok"] is False and app.evo_out is None
    assert app.game.party()[0].species == 0x26


def test_blockers():
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    app.server_features = {"badges"}
    assert "server needs an update" in app.tradeevo_blocker()
    app.server_features.add("trade_evo")
    app.roster = [{"uid": "you", "challenge_rules": ["exp_share"]}]
    assert "Alex needs to update" in app.tradeevo_blocker()
    app.roster = [{"uid": "you", "challenge_rules": ["trade_evo"]}]
    app.chal_wait = "lost"
    assert "both need to be in the run" in app.tradeevo_blocker()
    app.chal_wait = None
    app.game.m[game.WALK_COUNTER] = 3
    app.request_tradeevo(app.tradeevo_options()[0])
    assert not app.net.sent and "Stand still" in app.toasts[-1]


def test_flow_partner_side():
    app = FlowApp(yellow([tg.make_mon(0x54, nick="PIKACHU")]), me="you", partner="me")
    msg = {"type": "challenge_evo_request", "challenge_id": "c1", "evo_id": "e1", "from": "me", "name": "Ilias",
           "key": "k", "dex": 64, "into": 65, "nick": "KADABRA", "item": None}
    app.on_challenge_evo_request(msg)
    assert app.prompt["kind"] == "tradeevo" and app.mode == "prompt"
    app.respond_tradeevo(True)
    assert app.net.take("challenge_evo_respond") == [{"type": "challenge_evo_respond", "challenge_id": "c1",
                                                      "evo_id": "e1", "accept": True}]
    app.tradeevo_event({"t": "evo", "p": "me", "key": "k", "dex": 64, "into": 65, "nick": "KADABRA"}, "Ilias")
    assert app.notes[-1] == "Ilias's Kadabra evolved into Alakazam!"
    app.tradeevo_event({"t": "evo", "p": "me", "key": "k", "dex": 64, "into": 65, "nick": "SPOONY",
                        "item": "Metal Coat"}, "Ilias")
    assert app.notes[-1] == "Ilias's SPOONY evolved into Alakazam! (Metal Coat used up)"
    # the requester cancels while the question is up: it goes away
    app.prompt, app.mode = None, "play"
    app.on_challenge_evo_request(dict(msg, evo_id="e2"))
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": "e2", "ok": False, "reason": "cancelled"})
    assert app.prompt is None and app.mode == "play" and "cancelled" in app.toasts[-1]
    # a request for another run (or while another question is up) is declined right away
    app.on_challenge_evo_request(dict(msg, challenge_id="other", evo_id="e3"))
    assert app.net.take("challenge_evo_respond")[0]["accept"] is False and app.prompt is None


def test_both_ask_at_once():
    """Both click Evolve at the same moment: the server takes one (the partner's here) and ends the other "busy";
    the partner's question shows once our own wait is over."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    app.request_tradeevo(app.tradeevo_options()[0])
    mine = app.net.take("challenge_evo_request")[0]["evo_id"]
    app.on_challenge_evo_request({"challenge_id": "c1", "evo_id": "theirs", "name": "Alex", "key": "k", "dex": 67,
                                  "into": 68, "nick": "MACHOKE", "item": None})
    assert not app.net.take("challenge_evo_respond") and app.prompt["id"] == "theirs" and app.mode == "evo_wait"
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": mine, "ok": False, "reason": "busy"})
    assert app.evo_out is None and app.mode == "play" and "going on" in app.toasts[-1]
    app._maybe_show_prompt()
    assert app.mode == "prompt" and app.prompt["id"] == "theirs"


def test_refusals_from_the_server_end_the_wait():
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    for reason, want in (("not both in the run", "Alex isn't in the run right now."),
                         ("Alex needs a newer YellowLink (press U to update)",
                          "Trade evolution: Alex needs a newer YellowLink (press U to update)."),
                         ("the run is over", "the run is over.")):
        app.request_tradeevo(app.tradeevo_options()[0])
        eid = app.net.take("challenge_evo_request")[0]["evo_id"]
        app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": eid, "ok": False, "reason": reason})
        assert app.evo_out is None and app.mode == "play" and app.toasts[-1].endswith(want), app.toasts[-1]


def test_options_cached_per_few_frames():
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    a = app.tradeevo_options()
    app.game.write_party_slot(0, tg.make_mon(0x54, nick="PIKACHU"))
    assert app.tradeevo_options() is a
    app.frame += 10
    assert app.tradeevo_options() == []


def test_server_and_client_agree_on_the_pairs():
    """The server only accepts (dex, into) pairs it knows; every option the client can make is one of them."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))
    from yellowlink_server import challenges
    pairs = challenges.TRADE_EVO_PAIRS
    rom = tg.fake_rom()
    for sp, new in game.TRADE_EVOLUTIONS.items():
        if rom.dex_no(sp):
            assert (rom.dex_no(sp), rom.dex_no(new)) in pairs
    assert {(64, 65), (67, 68), (75, 76), (93, 94)} <= pairs
    for sp, (new, _item) in crystal.TRADE_EVOLUTIONS.items():
        assert (sp, new) in pairs
    for sp, evos in PTROM.trade_evolutions().items():
        for new, _item in evos:
            assert (sp, new) in pairs


def test_item_names_are_short_enough_for_the_server():
    for name in tradeevo.CRYSTAL_ITEMS.values():
        assert len(name) <= 20 and name.isprintable()
    assert text and ndsrom and struct                    # (imports used by the fakes)


# --- review fixes -------------------------------------------------------------------------------------------------
def asked(app):
    app.request_tradeevo(app.tradeevo_options()[0])
    return app.net.take("challenge_evo_request")[0]["evo_id"]


def test_own_failure_after_go_is_not_toasted_twice():
    """M1: our own failure (after go) is told once, by us; the server's end for it is then quiet."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    eid = asked(app)
    app.on_challenge_evo_go({"evo_id": eid})
    app.game.write_party_slot(0, tg.make_mon(0x54, nick="PIKACHU"))          # gone from the party
    app.tradeevo_step()
    done = app.net.take("challenge_evo_done")[0]
    assert done["ok"] is False and done["why"] == "not in the party anymore"
    n = len(app.toasts)
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": eid, "ok": False, "reason": done["why"]})
    assert len(app.toasts) == n                          # nothing more: no "Alex's trade evolution didn't happen"
    # cancelling: the server's end for it is quiet too
    app.game.write_party_slot(0, tg.make_mon(0x26))
    eid = asked(app)
    app.cancel_tradeevo()
    n = len(app.toasts)
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": eid, "ok": False, "reason": "cancelled"})
    assert len(app.toasts) == n


def test_partner_is_told_whose_mon_failed():
    """M1: the partner's toast names the requester's mon (not 'your party')."""
    app = FlowApp(yellow([tg.make_mon(0x54, nick="PIKACHU")]), me="you", partner="me")
    app.on_challenge_evo_request({"challenge_id": "c1", "evo_id": "e1", "name": "Alex", "key": "k", "dex": 64,
                                  "into": 65, "nick": "KADABRA", "item": None})
    app.respond_tradeevo(True)
    app.prompt, app.mode = None, "play"                  # (what _answer_prompt does)
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": "e1", "ok": False,
                              "reason": "not in the party anymore"})
    assert app.toasts[-1] == "Alex's Kadabra didn't evolve (not in the party anymore)."
    # a stranger end (not ours, not one we helped with) says nothing
    n = len(app.toasts)
    app.on_challenge_evo_end({"challenge_id": "c1", "evo_id": "zz", "ok": False, "reason": "timed out"})
    assert len(app.toasts) == n


def test_giving_up_tells_the_server():
    """M2: no answer seen for 75 s -> done ok:false 'no answer' (the server frees the run right away)."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    eid = asked(app)
    app.evo_out["since"] = time.time() - tradeevo.ASK_WITHIN - 1
    app.tradeevo_step()
    done = app.net.take("challenge_evo_done")
    assert done == [{"type": "challenge_evo_done", "challenge_id": "c1", "evo_id": eid, "ok": False,
                     "why": "no answer"}]
    assert app.evo_out is None and app.mode == "play"


def test_reconnect_cancels_an_open_one():
    """M2: after a reconnect (challenge_state) an evolution still asking / at go is cancelled, not left running."""
    for state in ("asking", "go"):
        app = FlowApp(yellow([tg.make_mon(0x26)]))
        eid = asked(app)
        if state == "go":
            app.on_challenge_evo_go({"evo_id": eid})
        app.tradeevo_reset()
        done = app.net.take("challenge_evo_done")
        assert done and done[0]["ok"] is False and done[0]["evo_id"] == eid and done[0]["why"] == "connection lost"
        assert app.evo_out is None and app.mode == "play"
        app.tradeevo_step()
        assert app.game.party()[0].species == 0x26       # never evolved


def test_dead_at_go_is_refused():
    """L1: the mon died between asking and go -> no evolution."""
    kad = tg.make_mon(0x26)
    app = FlowApp(yellow([kad]))
    key = game.mon_key(kad.data)
    app.sl = SoulLink("me", "you", ledger=[{"seq": 1, "p": "me", "t": "enc", "area": "A", "dex": 64,
                                            "status": "caught", "key": key}])
    eid = asked(app)
    app.sl.add({"seq": 2, "p": "me", "t": "dead", "key": key, "why": "fainted"})
    app.on_challenge_evo_go({"evo_id": eid})
    app.tradeevo_step()
    assert app.game.party()[0].species == 0x26
    assert app.net.take("challenge_evo_done")[0]["ok"] is False
    with pytest.raises(tradeevo.EvoFailed):
        tradeevo.evolve("gen1", app.game, app.rom, key, app.sl)


def test_snapshot_before_done():
    """L3: the resume snapshot is taken right after the write, before `done` goes out."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    eid = asked(app)
    app.on_challenge_evo_go({"evo_id": eid})
    app.tradeevo_step()
    assert app.saves == [(True, [])]                     # forced, and nothing sent before it
    assert app.net.take("challenge_evo_done")[0]["ok"] is True


def test_pause_does_not_count_against_the_go_limit():
    """L4: while the run is paused (partner reconnecting...) the 30 s limit stands still."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    eid = asked(app)
    app.on_challenge_evo_go({"evo_id": eid})
    app.chal_wait = "lost"
    app.evo_out["since"] -= tradeevo.APPLY_WITHIN + 5     # 35 s go by while paused...
    app.evo_out["tick"] -= tradeevo.APPLY_WITHIN + 5
    app.tradeevo_step()
    app.chal_wait = None
    app.game.m[game.FONT_LOADED] = 1                     # (not safe yet)
    app.tradeevo_step()
    assert app.evo_out is not None and not app.net.take("challenge_evo_done")
    app.game.m[game.FONT_LOADED] = 0
    app.tradeevo_step()
    assert app.game.party()[0].species == 0x95


def test_busy_partner_says_busy():
    """L5: the automatic decline says 'busy'; the requester is told so (not 'said no')."""
    app = FlowApp(yellow([tg.make_mon(0x54, nick="PIKACHU")]), me="you", partner="me")
    app.prompt = {"kind": "challenge", "id": "x"}
    app.on_challenge_evo_request({"challenge_id": "c1", "evo_id": "e1", "name": "Alex", "key": "k", "dex": 64,
                                  "into": 65, "nick": "KADABRA", "item": None})
    assert app.net.take("challenge_evo_respond") == [{"type": "challenge_evo_respond", "challenge_id": "c1",
                                                      "evo_id": "e1", "accept": False, "busy": True}]
    req = FlowApp(yellow([tg.make_mon(0x26)]))
    eid = asked(req)
    req.on_challenge_evo_end({"challenge_id": "c1", "evo_id": eid, "ok": False, "reason": "partner busy"})
    assert req.toasts[-1] == "Trade evolution: Alex is busy right now - try again in a moment."
    eid = asked(req)
    req.on_challenge_evo_end({"challenge_id": "c1", "evo_id": eid, "ok": False, "reason": "wait a moment"})
    assert "wait a moment" in req.toasts[-1]


def test_blocker_order():
    """L6: a partner who is offline / not in the run is reported as such before 'needs to update'."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    app.roster = [{"uid": "you", "online": False, "challenge_rules": None}]
    assert "both need to be in the run" in app.tradeevo_blocker()
    app.roster = [{"uid": "you", "online": True, "challenge_rules": ["exp_share"]}]
    app.chal_wait = "lost"
    assert "both need to be in the run" in app.tradeevo_blocker()
    app.chal_wait = None
    assert "needs to update" in app.tradeevo_blocker()


def test_options_keep_the_last_good_list():
    """L8: a frame where the party can't be read (Platinum mid-update) keeps the last list (no flicker)."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    good = app.tradeevo_options()
    assert len(good) == 1
    app.game.m[game.PARTY_SPECIES + 1] = 0x00            # mid-update
    app.frame += 10
    assert app.tradeevo_options() == good
    app.game.m[game.PARTY_SPECIES + 1] = 0xFF
    app.game.write_party_slot(0, tg.make_mon(0x54, nick="PIKACHU"))
    app.frame += 10
    assert app.tradeevo_options() == []


def test_leaving_stops_the_flow():
    """L9: while leaving the run nothing is written."""
    app = FlowApp(yellow([tg.make_mon(0x26)]))
    eid = asked(app)
    app.on_challenge_evo_go({"evo_id": eid})
    app._chal_leaving = True
    app.tradeevo_step()
    assert app.game.party()[0].species == 0x26 and not app.net.take("challenge_evo_done")
