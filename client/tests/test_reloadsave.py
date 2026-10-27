"""'Back to my last in-game save' (SPEC.md section 5): the PC store vs a game that comes back from an older save,
through the button (restart from the battery) or the game's own soft reset. No ROM needed."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from yellowlink import pcdb, reloadsave, text  # noqa: E402
from test_pcdb import FakeMem, rec, setup_game  # noqa: E402


def names(view_or_boxes):
    return [text.decode(r[44:]) for box in view_or_boxes for r in box]


def store_names(sync):
    return sorted(e["nick"] for b in sync.working["boxes"] for e in b)


# Note: rec() mons only differ in key (OT ID + DVs + catch rate) by their OT ID - give each its own, like real mons
def linked(tmp_path, mons, fp="save1"):
    """A Yellow game linked to a fresh store, committed, with its in-game save fingerprint noted."""
    mem, pc = setup_game([mons])
    sync = pcdb.PCSync(tmp_path / "s.json")
    sync.note_battery("yellow", fp)
    pc.write_boxes(sync.open_session("yellow", pc, True))
    sync.close_session("yellow", pc)
    sync.commit()
    return mem, pc, sync


def visit(sync, pc, change=None, party=None):
    """Open the PC (the view is written), optionally change the boxes like the player would, close it."""
    pc.write_boxes(sync.open_session("yellow", pc, True))
    if change:
        boxes = pc.read_boxes()
        change(boxes)
        pc.write_boxes(boxes)
    return sync.close_session("yellow", pc, party_records=party)


# --- an existing hole: a stale view in one game brought back a mon that left the store elsewhere ------------
def test_mon_withdrawn_in_another_game_is_not_reimported_from_a_stale_view(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2)])
    ratty = next(e for e in sync.working["boxes"][0] if e["nick"] == "RATTY")
    sync.working["boxes"][0].remove(ratty)      # withdrawn in Crystal (no trade: never in 'gone')
    sync.pending = True
    sync.commit()
    # Yellow resumes from its snapshot, still showing RATTY: opening the PC must not bring it back
    view = sync.open_session("yellow", pc, True)
    assert names(view) == ["PIKA"] and store_names(sync) == ["PIKA"]


def test_withdrawing_that_stale_copy_takes_it_out_of_the_party(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2)])
    stale = pc.read_boxes()[0][1]
    ratty = next(e for e in sync.working["boxes"][0] if e["nick"] == "RATTY")
    sync.working["boxes"][0].remove(ratty)
    sync.pending = True
    sync.commit()
    # the game's PC was open (old view) while RATTY left the store; the player withdraws the old copy
    pc.write_boxes([[r for r in pc.read_boxes()[0]]] + [[]] * 11)
    sync.session_open = True
    boxes = pc.read_boxes()
    boxes[0] = boxes[0][:1]
    pc.write_boxes(boxes)
    report = sync.close_session("yellow", pc, party_records=[rec(0x99, "BULBA", otid=7), stale])
    assert report["left_returned"] and report["party_removals"] == {1: stale}
    assert store_names(sync) == ["PIKA"]


# --- since-the-save notes (the button's refusal) ------------------------------------------------------------
def test_ledger_notes_deposits_withdrawals_and_starts_over_at_an_in_game_save(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA")])
    assert reloadsave.blocker_text(sync.since_save("yellow")) is None
    pidge = rec(0x24, "PIDGE", otid=3)
    visit(sync, pc, lambda b: b[0].append(pidge))                      # deposit from the party
    led = sync.since_save("yellow")
    assert [x[1] for x in led["out"]] == ["PIDGE"]
    assert "put Pidge in your PC" in reloadsave.blocker_text(led)
    visit(sync, pc, lambda b: b[0].remove(pidge))                      # ... and back out: as at the save
    assert not sync.since_save("yellow")["out"] and not sync.since_save("yellow")["back"]
    visit(sync, pc, lambda b: b[0].clear())                            # PIKA withdrawn
    assert "took Pika out" in reloadsave.blocker_text(sync.since_save("yellow"))
    assert not sync.note_battery("yellow", "save1")                    # same save: nothing new
    assert sync.note_battery("yellow", "save2")                        # saved in-game
    assert reloadsave.blocker_text(sync.since_save("yellow")) is None
    sync.commit()
    assert pcdb.PCSync(tmp_path / "s.json").since_save("yellow")["fp"] == "save2"


def test_ledger_follows_commit_and_rollback(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA")])
    visit(sync, pc, lambda b: b[0].append(rec(0x24, "PIDGE", otid=3)))
    assert sync.since_save("yellow")["out"]
    sync.rollback()                     # crash: game and store back to the last snapshot - and so are the notes
    assert not sync.since_save("yellow")["out"]


def test_trade_or_gift_away_is_noted_for_every_game(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2)])
    sync.note_battery("crystal", "c1")
    ratty = next(e for e in sync.committed["boxes"][0] if e["nick"] == "RATTY")
    sync.apply_trade({"trade_id": "t1", "remove": [ratty["id"]], "add": [], "srv_rev": 3})
    for g in ("yellow", "crystal"):
        assert "traded/gave away Ratty" in reloadsave.blocker_text(sync.since_save(g))
    sync.note_battery("yellow", "save2")
    assert reloadsave.blocker_text(sync.since_save("yellow")) is None


# --- the game comes back from an older save (button / soft reset) --------------------------------------------
def battery(pc):
    return copy.deepcopy(pc.read_boxes())


def load_save(pc, boxes):
    """The game's boxes are the save's again."""
    pc.write_boxes(boxes + [[]] * (12 - len(boxes)))


def test_rebase_traded_mon_in_the_old_save_never_comes_back(tmp_path):
    a, b = rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2)
    mem, pc, sync = linked(tmp_path, [a, b])
    saved = battery(pc)                                 # in-game save: box [PIKA, RATTY]
    pika = next(e for e in sync.committed["boxes"][0] if e["nick"] == "PIKA")
    sync.apply_trade({"trade_id": "t1", "remove": [pika["id"]], "add": [], "srv_rev": 3})
    visit(sync, pc)                                     # look-only visit: the view no longer shows PIKA
    sync.commit()
    load_save(pc, saved)                                # back to the save: its box still has PIKA
    assert sync.rebase("yellow", pc)
    report = visit(sync, pc)
    assert report["added"] == 0 and store_names(sync) == ["RATTY"]


def test_rebase_without_the_departed_note_would_have_resurrected_it(tmp_path):
    """Same as above with the view rewritten twice (the departed list is what saves us)."""
    a, b = rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2)
    mem, pc, sync = linked(tmp_path, [a, b])
    saved = battery(pc)
    pika = next(e for e in sync.committed["boxes"][0] if e["nick"] == "PIKA")
    sync.apply_trade({"trade_id": "t1", "remove": [pika["id"]], "add": [], "srv_rev": 3})
    visit(sync, pc)
    visit(sync, pc)
    sync.commit()
    again = pcdb.PCSync(tmp_path / "s.json")            # also after a restart
    load_save(pc, saved)
    assert again.rebase("yellow", pc)
    visit(again, pc)
    assert store_names(again) == ["RATTY"]


def test_rebase_gift_received_after_the_save_stays(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA")])
    saved = battery(pc)
    gift = {"id": "g1", "origin": "yellow", "dex": 1, "level": 5, "nick": "GIFT", "ot": "MISTY", "otid": 9,
            "gen1": pcdb._b64(rec(0x99, "GIFT", ot="MISTY", otid=9)), "gen2": None}
    sync.apply_trade({"trade_id": "t2", "remove": [], "add": [gift], "srv_rev": 4})
    visit(sync, pc)                                     # the game showed it
    sync.commit()
    load_save(pc, saved)                                # the save doesn't have it in its box
    sync.rebase("yellow", pc)
    report = visit(sync, pc)
    assert report["removed"] == 0 and store_names(sync) == ["GIFT", "PIKA"]


def test_rebase_mon_caught_straight_into_the_box_after_loading_is_deposited(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA")])
    saved = battery(pc)
    load_save(pc, saved)
    sync.rebase("yellow", pc)
    boxes = pc.read_boxes()
    boxes[0].append(rec(0x91, "MEWTWO", otid=5))       # full party: the catch goes to the box
    pc.write_boxes(boxes)
    report = visit(sync, pc)
    assert report["added"] == 0 and store_names(sync) == ["MEWTWO", "PIKA"]   # imported at the open's diff


def test_soft_reset_party_copy_of_a_deposited_mon_is_a_clone(tmp_path):
    pidge = rec(0x24, "PIDGE", otid=3)
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA")])
    saved = battery(pc)                                 # saved with PIDGE in the party
    visit(sync, pc, lambda b: b[0].append(pidge))       # deposited after the save
    sync.commit()
    pid = next(e for e in sync.committed["boxes"][0] if e["nick"] == "PIDGE")
    sync.apply_trade({"trade_id": "t3", "remove": [pid["id"]], "add": [], "srv_rev": 5})   # ... and traded away
    load_save(pc, saved)                                # soft reset: PIDGE is back in the party
    sync.rebase("yellow", pc)
    party = [rec(0x54, "BULBA", otid=4, level=5), pidge]
    assert sync.party_clones("yellow", pc, party) == [1]
    sync.loaded_from_save("yellow", "save1")
    assert reloadsave.blocker_text(sync.since_save("yellow")) is None
    visit(sync, pc)
    assert store_names(sync) == ["PIKA"]


def test_soft_reset_mon_withdrawn_after_the_save_comes_back_to_the_pc(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2)])
    saved = battery(pc)
    visit(sync, pc, lambda b: b[0].pop())               # RATTY withdrawn after the save
    sync.commit()
    load_save(pc, saved)                                # soft reset: the save's box still holds RATTY
    sync.rebase("yellow", pc)
    assert sync.party_clones("yellow", pc, [rec(0x54, "BULBA", otid=4)]) == []
    visit(sync, pc)
    assert store_names(sync) == ["PIKA", "RATTY"]       # not lost


def test_soft_reset_withdrawn_levelled_and_deposited_again_is_not_doubled(tmp_path):
    mem, pc, sync = linked(tmp_path, [rec(0x54, "PIKA"), rec(0xA5, "RATTY", otid=2, level=10)])
    saved = battery(pc)
    visit(sync, pc, lambda b: b[0].pop())               # withdrawn...
    visit(sync, pc, lambda b: b[0].append(rec(0xA5, "RATTY", otid=2, level=12)))   # ...levelled up, deposited again
    sync.commit()
    load_save(pc, saved)
    sync.rebase("yellow", pc)
    visit(sync, pc)
    assert store_names(sync) == ["PIKA", "RATTY"]


# --- the in-game save in the battery ------------------------------------------------------------------------
def sram(mem):
    return b"".join(bytes(mem.banks[b]) for b in range(4))


def test_yellow_fingerprint_ignores_yellowlinks_box_writes():
    mem, pc = setup_game([[rec(0x54, "PIKA")]])
    assert not reloadsave.has_save("yellow", sram(mem))
    for i, ch in enumerate(text.encode("ASH")[:4]):
        mem[1, 0xA598 + i] = ch                         # sPlayerName: the first save
    assert reloadsave.has_save("yellow", sram(mem))
    fp = reloadsave.fingerprint("yellow", sram(mem))
    pc.write_boxes([[rec(0x54, "PIKA"), rec(0x24, "PIDGE", otid=3)]] + [[]] * 11)   # a PC visit (incl. sCurBoxData)
    assert reloadsave.fingerprint("yellow", sram(mem)) == fp
    mem[1, 0xA5A3 + 0x40] ^= 1                          # sMainData (play time...): the player saved
    assert reloadsave.fingerprint("yellow", sram(mem)) != fp


def test_crystal_fingerprint_and_has_save():
    from yellowlink import crystal
    data = bytearray(0x8000)
    assert not reloadsave.has_save("crystal", bytes(data))
    data[0x2000 + 0x100] = 7
    s = crystal.checksum16(bytes(data[0x2009:0x2B83]))
    data[0x2D0D], data[0x2D0E] = s & 0xFF, s >> 8
    assert reloadsave.has_save("crystal", bytes(data))
    fp = reloadsave.fingerprint("crystal", bytes(data))
    data[0x2D10 + 5] = 9                                # sBox (the live current box) isn't the save
    assert reloadsave.fingerprint("crystal", bytes(data)) == fp
    assert reloadsave.fingerprint("red", bytes(data)) is None


def test_fake_mem_is_shared_with_pcdb_tests():
    assert isinstance(FakeMem(), FakeMem)
