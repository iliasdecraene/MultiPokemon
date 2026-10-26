"""Pokémon Platinum's PC boxes as a view of the PC database (pcdb.PCSync; SPEC.md section 5).

Same interface as pcdb.Gen1PC (Yellow) / crystal.Gen2PC (Crystal): read_boxes, write_boxes, record_for,
entry_from_record, key, is_standin, initialised, current_box (+ remove_party_slot for stand-ins withdrawn into the
party, which PlatinumMemory doesn't have).

Mapping. The store has 14 boxes x 20; Platinum has 18 boxes x 30 slots that may have holes.
  * write_boxes: store box i -> Platinum box i, slots 1-20 (no holes). Everything else (slots 21-30, boxes 15-18)
    is emptied, except Pokémon that didn't fit into a full store (`unplaced`, see below), which are put back into
    boxes 15-18 (then slots 21-30). Box names / wallpapers / the current box are never touched.
  * read_boxes: store-shaped (14 lists of <= 20 encrypted 136-byte records). Platinum box i (i < 14) is read
    slot by slot without the holes; its first 20 Pokémon stay in box i. Pokémon past the 20th, and everything in
    boxes 15-18, go into free space of the first boxes with room ("harvested"). If the store is full, records
    the store doesn't know yet (`known`, primed by PCSync from its last view) are the ones left out; they stay in
    the game (`unplaced`: write_boxes keeps them in boxes 15-18) and the app tells the player. Slots with a bad
    checksum are never harvested or erased either (they also stay `unplaced`).
PCSync's diff matches records by their bytes, not by position, so the folding above needs nothing else from it.

When to write (verified on the real game, dev/e2e/pt_pctest.py): in the exec hook platinum.HOOK_PC_OPEN
(FieldSystem_OpenPokemonStorage), i.e. after the player picked DEPOSIT / WITHDRAW / MOVE in "SOMEONE'S PC" and
before the box application exists. The box UI reads PCBoxes from the save RAM (BoxAppMan_Init ->
SaveData_GetPCBoxes) when it starts, so the written boxes are what it shows. Closing is polled (pc_open() goes
False; there is no main-ARM9 close function to hook).
"""
from __future__ import annotations

import logging
import struct

from . import pk4
from .pcdb import MONS_PER_BOX, STORE_BOXES, current_rep
from .platinum import (BOX_MON_SIZE, BOX_MONS, BOX_SLOTS, NUM_BOXES, PAGE_PARTY, PARTY_MON_SIZE)

log = logging.getLogger("yellowlink.platinum_pc")

ALL_SLOTS = NUM_BOXES * BOX_SLOTS              # 540
EMPTY = pk4.EMPTY_BOX


def load_data(path):
    """ndsrom.PlatinumData of the player's Platinum ROM (cached), or None if there's no usable file."""
    if not path:
        return None
    try:
        from . import ndsrom
        return ndsrom.platinum(path)
    except Exception:  # noqa: BLE001 - a missing/odd file only means conversions without the Platinum data
        log.exception("could not read Platinum data from %s", path)
        return None


def classify(rec: bytes) -> str:
    """'empty', 'mon' or 'bad' (checksum mismatch: the game shows a Bad Egg; we never harvest or erase it)."""
    rec = bytes(rec)
    if rec == EMPTY:
        return "empty"
    if not pk4.valid(rec):
        return "bad"
    return "empty" if pk4.is_empty(rec) else "mon"


def fold(slots: list, known=frozenset()) -> tuple:
    """18 x 30 records (empty slots allowed) -> (store-shaped boxes, unplaced records, number harvested).

    See the module docstring. Pure function (unit tested without a ROM)."""
    boxes = [[] for _ in range(STORE_BOXES)]
    extra = []                 # (record) in game order: past slot 20 of boxes 1-14, then boxes 15-18
    unplaced = []
    for b in range(NUM_BOXES):
        for s in range(BOX_SLOTS):
            rec = bytes(slots[b][s]) if b < len(slots) and s < len(slots[b]) else EMPTY
            kind = classify(rec)
            if kind == "empty":
                continue
            if kind == "bad":
                unplaced.append(rec)
            elif b < STORE_BOXES and len(boxes[b]) < MONS_PER_BOX:
                boxes[b].append(rec)
            else:
                extra.append(rec)
    free = sum(MONS_PER_BOX - len(b) for b in boxes)
    if len(extra) > free:
        # The store is full. Keep what it already knows; leave new Pokémon in the game.
        extra_known = [r for r in extra if r in known]
        extra_new = [r for r in extra if r not in known]
        need = len(extra_known) - free
        for b in reversed(boxes):                       # make room for known ones by leaving out new ones
            for i in range(len(b) - 1, -1, -1):
                if need <= 0:
                    break
                if b[i] not in known:
                    unplaced.append(b.pop(i))
                    need -= 1
                    free += 1
        extra = extra_known + extra_new
        unplaced.extend(extra[free:])
        extra = extra[:free]
    for rec in extra:
        box = next(b for b in boxes if len(b) < MONS_PER_BOX)
        box.append(rec)
    return boxes, unplaced, len(extra)


def layout(boxes: list, unplaced=()) -> list:
    """Store-shaped boxes (+ records to keep in the game) -> 18 x 30 records for Platinum."""
    slots = [[EMPTY] * BOX_SLOTS for _ in range(NUM_BOXES)]
    for b in range(min(STORE_BOXES, len(boxes))):
        for s, rec in enumerate(list(boxes[b])[:MONS_PER_BOX]):
            assert len(rec) == BOX_MON_SIZE
            slots[b][s] = bytes(rec)
    spare = [(b, s) for b in range(STORE_BOXES, NUM_BOXES) for s in range(BOX_SLOTS)] + \
            [(b, s) for b in range(STORE_BOXES) for s in range(MONS_PER_BOX, BOX_SLOTS)]
    for (b, s), rec in zip(spare, unplaced):
        slots[b][s] = bytes(rec)
    if len(unplaced) > len(spare):                      # can't happen: 540 slots - 280 >= anything left over
        log.error("%d Pokémon didn't fit anywhere in Platinum's PC", len(unplaced) - len(spare))
    return slots


class Gen4PC:
    """Read/write Platinum's 18 x 30 PC boxes in the save RAM as the store's 14 x 20 view."""

    def __init__(self, pm, rom):
        """pm: platinum.PlatinumMemory (memory bus at pm.m); rom: ndsrom.PlatinumData of the running ROM."""
        self.pm = pm
        self.rom = rom
        self.known = set()          # records of the store's last view (PCSync._prime)
        self.unplaced = []          # records left in the game because the store is full (or with a bad checksum)
        self.harvested = 0          # Pokémon folded in from slots 21-30 / boxes 15-18 at the last read
        self._cache = {}

    # --- game memory ---------------------------------------------------------------------------------------
    def initialised(self) -> bool:
        """Platinum's boxes exist from NEW GAME on (no Crystal-style first-save step): valid while in game."""
        return self.pm.in_game() and self.pm.pc_boxes_addr() is not None

    def current_box(self) -> int:
        return self.pm.current_box()

    def _mons_addr(self):
        base = self.pm.pc_boxes_addr()
        if base is None:
            raise RuntimeError("Platinum's PC boxes aren't in memory (not in game)")
        return base + BOX_MONS

    def read_slots(self) -> list:
        """All 18 x 30 encrypted 136-byte records as they are in RAM."""
        raw = self.pm.m.read(self._mons_addr(), ALL_SLOTS * BOX_MON_SIZE)
        return [[raw[(b * BOX_SLOTS + s) * BOX_MON_SIZE:(b * BOX_SLOTS + s + 1) * BOX_MON_SIZE]
                 for s in range(BOX_SLOTS)] for b in range(NUM_BOXES)]

    def read_boxes(self) -> list:
        boxes, self.unplaced, self.harvested = fold(self.read_slots(), self.known)
        return boxes

    def write_boxes(self, boxes: list):
        """Store box i -> Platinum box i slots 1-20; the rest emptied, except `unplaced` (kept in boxes 15-18).
        Only while the box UI isn't running (the app calls it from HOOK_PC_OPEN, before the UI starts)."""
        if self.pm.pc_open():
            raise RuntimeError("Platinum's box UI is running; not writing the boxes under it")
        addr = self._mons_addr()
        slots = layout(boxes, self.unplaced)
        self.pm.m.write(addr, b"".join(rec for box in slots for rec in box))

    def remove_party_slot(self, slot: int):
        """Party_RemovePokemonBySlotIndex: shift the rest up, clear the last slot (Pokemon_Init), count - 1.
        Never removes the last party Pokémon."""
        body = self.pm.save_body()
        if not body:
            return
        m = self.pm.m
        party = body + PAGE_PARTY
        n = m.r32(party + 4)
        if not 0 <= slot < n <= 6 or n <= 1:
            return
        recs = [m.read(party + 8 + i * PARTY_MON_SIZE, PARTY_MON_SIZE) for i in range(n)]
        del recs[slot]
        recs.append(pk4.EMPTY_PARTY)
        m.write(party + 8, b"".join(recs))
        m.write(party + 4, struct.pack("<I", n - 1))

    # --- records <-> entries --------------------------------------------------------------------------------
    def record_for(self, entry: dict) -> bytes:
        """136 encrypted bytes to show in Platinum, from the entry's current representation: own Gen-4 data,
        else a conversion of the Gen-2 / Gen-1 data (pk4.from_gen2 / from_gen1), else a ?NAME stand-in."""
        rep, data = current_rep(entry, "gen4")
        if rep == "gen4":
            return data
        rec = None
        if rep in ("gen1", "gen2"):
            key = (rep, data, int(entry.get("dex") or 0))
            if key in self._cache:                       # conversions search a PID: remember them
                rec = self._cache[key]
            else:
                try:
                    if rep == "gen2":
                        rec = pk4.from_gen2(data, self.rom)
                    else:
                        rec = pk4.from_gen1(data, self.rom, dex=int(entry.get("dex") or 0) or None)
                except Exception:  # noqa: BLE001 - damaged data from a friend: show a stand-in instead
                    log.exception("could not convert %s to Gen 4", entry.get("id"))
                    rec = None
                if len(self._cache) > 4000:
                    self._cache.clear()
                self._cache[key] = rec
        return rec if rec is not None else pk4.standin(entry, self.rom)

    def entry_from_record(self, rec: bytes, origin="platinum", entry_id=None) -> dict:
        return pk4.entry_from_pk4(rec, self.rom, origin=origin, entry_id=entry_id)

    @staticmethod
    def key(rec: bytes) -> str:
        return pk4.mon_key(rec)

    @staticmethod
    def is_standin(rec: bytes) -> bool:
        return pk4.is_standin(rec)
