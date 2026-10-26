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
  * First link with a nearly full store: what doesn't fit stays in the game (`unplaced`), never erased.
  * The box UI can change a stored mon in place (MARK, MOVE ITEMS give/take): `edits_in_box` makes PCSync match a
    changed record to the vanished entry with the same key that is closest byte-wise (`distance`) and keep its id
    (`updated_entry`). Stand-ins, mons in an unfinished trade and traded-away mons stay as stored; an item given
    to one goes back to the bag (`give_item`: Bag_TryAddItem, pockets from the ROM's item data; verified in the
    game's own bag screen and with MOVE ITEMS in dev/e2e/pt_pctest.py).
  * A record the game has decrypted in place at the moment we read (a DS frame can end mid-update) is read as the
    mon it is (`normalise`), not as a Bad Egg; the app also harvests only once two box reads in a row agree and
    the party reads clean (App._pt_pc_poll "closing").

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

# Bag (pret include/bag.h; save body + PAGE_BAG, page 0x774 bytes): pockets of BagItem {u16 item, u16 quantity}
# in this order. Verified: the save's TM27 (item 354) sits in the TM pocket at +0x35C; struct size = 0x774.
PAGE_BAG = 0x630
BAG_POCKETS = {0: (0x000, 165), 7: (0x294, 50), 3: (0x35C, 100), 5: (0x4EC, 12), 1: (0x51C, 40), 4: (0x5BC, 64),
               2: (0x6BC, 15), 6: (0x6F8, 30)}     # POCKET_* id -> (offset, slots): items, key, TM/HM, mail, ...
POCKET_TMHMS = 3
ITEM_DATA_NARC = "/itemtool/itemdata/pl_item_data.narc"
NUM_ITEMS = 468


def item_data_id(item: int) -> int:
    """sItemArchiveIDs[item].dataID (ARM9 table at 0x020F0CC4; checked for all 468 items against the RAM):
    items 113-134 are unused (data 0), from 135 on the data member is item - 22."""
    return item if item <= 112 else (0 if item <= 134 else item - 22)


def load_item_pockets(path):
    """Field pocket (POCKET_*) of every item id, from the ROM's item data (ItemData.fieldPocket: u16 at +8, bits
    7-10), or None."""
    if not path:
        return None
    try:
        from . import ndsrom
        rom = ndsrom.NDSRom(path)
        try:
            narc = rom.narc(ITEM_DATA_NARC)
            return [(struct.unpack_from("<H", narc.member(item_data_id(i), 8, 2))[0] >> 7) & 0xF
                    for i in range(NUM_ITEMS)]
        finally:
            rom.close()
    except Exception:  # noqa: BLE001
        log.exception("could not read Platinum item data from %s", path)
        return None


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


def normalise(rec: bytes) -> tuple:
    """(kind, record): kind 'empty', 'mon' or 'bad'. A record the game is working on right now (decrypted in
    place: XOR only, blocks still shuffled, boxDecrypted set - pret BoxPokemon_EnterDecryptionContext; a DS frame
    can end in the middle) is returned as the game will write it back (pk4.reencrypt_in_place), and only if that
    is a valid record. 'bad' = no valid record either way (a Bad Egg in the game, or data we can't read): never
    harvested or erased."""
    rec = bytes(rec)
    if rec == EMPTY:
        return "empty", rec
    flags, chk = struct.unpack_from("<HH", rec, 4)
    if pk4.valid(rec):
        if flags & 0x7:                              # flag set, data not decrypted yet: the game clears it again
            rec = rec[:4] + struct.pack("<H", flags & ~0x7) + rec[6:]
    elif len(rec) == BOX_MON_SIZE and (flags & pk4.FLAG_BOX_DECRYPTED or pk4.checksum(rec) == chk):
        # decrypted in place (flag set), or being encrypted again (ExitDecryptionContext clears the flags first):
        # XOR back in place = exactly what the game writes next (checked on captured frames of the real game)
        fixed = rec[:4] + struct.pack("<H", flags & ~0x7) + rec[6:8] + pk4._crypt(rec[8:], chk)
        if pk4.valid(fixed):
            rec = fixed
    if not pk4.valid(rec):
        return "bad", rec
    return ("empty" if pk4.is_empty(rec) else "mon"), rec


def classify(rec: bytes) -> str:
    return normalise(rec)[0]


def fold(slots: list, known=frozenset()) -> tuple:
    """18 x 30 records (empty slots allowed) -> (store-shaped boxes, unplaced records, number harvested).

    See the module docstring. Pure function (unit tested without a ROM)."""
    boxes = [[] for _ in range(STORE_BOXES)]
    extra = []                 # (record) in game order: past slot 20 of boxes 1-14, then boxes 15-18
    unplaced = []
    for b in range(NUM_BOXES):
        for s in range(BOX_SLOTS):
            rec = bytes(slots[b][s]) if b < len(slots) and s < len(slots[b]) else EMPTY
            kind, rec = normalise(rec)
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


# slots outside the store's view, where Pokémon the store can't take are kept: boxes 15-18, then slots 21-30
SPARE = [(b, s) for b in range(STORE_BOXES, NUM_BOXES) for s in range(BOX_SLOTS)] + \
        [(b, s) for b in range(STORE_BOXES) for s in range(MONS_PER_BOX, BOX_SLOTS)]       # 260


def layout(boxes: list, unplaced=()) -> list:
    """Store-shaped boxes (+ records to keep in the game) -> 18 x 30 records for Platinum. Raises ValueError
    (nothing written) if `unplaced` doesn't fit the spare slots."""
    slots = [[EMPTY] * BOX_SLOTS for _ in range(NUM_BOXES)]
    for b in range(min(STORE_BOXES, len(boxes))):
        for s, rec in enumerate(list(boxes[b])[:MONS_PER_BOX]):
            assert len(rec) == BOX_MON_SIZE
            slots[b][s] = bytes(rec)
    spare = SPARE
    if len(unplaced) > len(spare):                      # the caller must not write anything then
        raise ValueError("%d Pokémon wouldn't fit in Platinum's PC" % (len(unplaced) - len(spare)))
    for (b, s), rec in zip(spare, unplaced):
        slots[b][s] = bytes(rec)
    return slots


class Gen4PC:
    """Read/write Platinum's 18 x 30 PC boxes in the save RAM as the store's 14 x 20 view."""

    edits_in_box = True             # the box UI can mark a mon or give/take its item in place (pcdb._match_edits)
    spare = len(SPARE)              # Pokémon the store can't take can be kept in the game (pcdb._import)

    def __init__(self, pm, rom, pockets=None):
        """pm: platinum.PlatinumMemory (memory bus at pm.m); rom: ndsrom.PlatinumData of the running ROM;
        pockets: load_item_pockets() (needed to give items back to the bag)."""
        self.pm = pm
        self.rom = rom
        self.pockets = pockets
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
        slots = layout(boxes, self.unplaced)            # raises before anything is written if it doesn't fit
        self.pm.m.write(addr, b"".join(rec for box in slots for rec in box))

    def remove_party_slot(self, slot: int) -> bool:
        """Party_RemovePokemonBySlotIndex: shift the rest up, clear the last slot (Pokemon_Init), count - 1.
        Never removes the last party Pokémon; refuses (False) while a record is mid-update (bad checksum)."""
        body = self.pm.save_body()
        if not body:
            return False
        m = self.pm.m
        party = body + PAGE_PARTY
        n = m.r32(party + 4)
        if not 0 <= slot < n <= 6 or n <= 1:
            return False
        recs = [m.read(party + 8 + i * PARTY_MON_SIZE, PARTY_MON_SIZE) for i in range(n)]
        if not all(pk4.valid(r) for r in recs):         # the game is working on a record right now: not now
            return False
        del recs[slot]
        recs.append(pk4.EMPTY_PARTY)
        m.write(party + 8, b"".join(recs))
        m.write(party + 4, struct.pack("<I", n - 1))
        return True

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

    def updated_entry(self, entry: dict, rec: bytes, origin="platinum") -> dict:
        """A stored mon the box UI changed in place (same entry id). Only markings changed on a mon from another
        game -> the entry stays as it is (marks are cosmetic; its own game's exact data is kept). Anything else
        (an item given/taken) -> the entry's current representation becomes these Platinum bytes."""
        try:
            old = pk4.PK4(pk4._plain_of(self.record_for(entry))[:pk4.BOX_SIZE])
            new = pk4.PK4(pk4._plain_of(bytes(rec))[:pk4.BOX_SIZE])
            new.markings = old.markings
            same = new.plain()[:4] == old.plain()[:4] and new.plain()[8:] == old.plain()[8:]   # not flags/checksum
            if entry.get("origin") != origin and same:
                return entry
        except ValueError:
            pass
        return pk4.entry_from_pk4(rec, self.rom, origin=origin, entry_id=entry["id"])

    @staticmethod
    def held_item(rec: bytes) -> int:
        try:
            return pk4.PK4(pk4._plain_of(bytes(rec))[:pk4.BOX_SIZE]).item
        except ValueError:
            return 0

    def item_changes(self, old: bytes, new: bytes) -> tuple:
        """(give, take) when the game's copy `new` replaces nothing and the stored `old` is kept: the box UI took
        an item from the bag that `new` holds (give it back) and/or put `old`'s item into the bag (take it out
        again, or it would exist twice)."""
        a, b = self.held_item(old), self.held_item(new)
        return ([b] if b and b != a else []), ([a] if a and a != b else [])

    @staticmethod
    def same_mon(old: bytes, new: bytes) -> bool:
        """`new` is `old` after what Platinum's box UI can do to a stored mon in place: MARK and MOVE ITEMS change
        only the markings and the held item (and so the checksum). Anything else = a different mon."""
        try:
            a = pk4.PK4(pk4._plain_of(bytes(old))[:pk4.BOX_SIZE])
            b = pk4.PK4(pk4._plain_of(bytes(new))[:pk4.BOX_SIZE])
            if not (a.checksum_ok() and b.checksum_ok()):
                return False
        except ValueError:
            return False
        for pk in (a, b):
            pk.markings = 0
            pk.item = 0
        pa, pb = a.plain(), b.plain()
        return pa[:4] == pb[:4] and pa[8:] == pb[8:]

    @staticmethod
    def distance(a: bytes, b: bytes) -> int:
        """Bytes that differ between two mons' decrypted data (encryption hides how close two records are)."""
        try:
            pa, pb = pk4._plain_of(bytes(a))[:pk4.BOX_SIZE], pk4._plain_of(bytes(b))[:pk4.BOX_SIZE]
        except ValueError:
            pa, pb = bytes(a), bytes(b)
        return sum(x != y for x, y in zip(pa, pb)) + abs(len(pa) - len(pb))

    def give_item(self, item: int) -> bool:
        """Bag_TryAddItem(bag, item, 1): onto the pocket's stack of that item, else its first empty slot (max 999,
        TMs/HMs 99). False if it doesn't fit or the item is unknown."""
        where = self._pocket(item)
        if where is None:
            return False
        pocket, base, size = where
        m = self.pm.m
        slots = [struct.unpack_from("<HH", m.read(base + 4 * k, 4)) for k in range(size)]
        cap = 99 if pocket == POCKET_TMHMS else 999
        free = None
        for k, (it, q) in enumerate(slots):
            if it == item:
                if q + 1 > cap:
                    return False
                m.write(base + 4 * k, struct.pack("<HH", item, q + 1))
                return True
            if free is None and it == 0 and q == 0:
                free = k
        if free is None:
            return False
        m.write(base + 4 * free, struct.pack("<HH", item, 1))
        return True

    def _pocket(self, item: int):
        if not self.pockets or not 0 < item < len(self.pockets) or self.pockets[item] not in BAG_POCKETS:
            return None
        body = self.pm.save_body()
        if not body:
            return None
        pocket = self.pockets[item]
        off, size = BAG_POCKETS[pocket]
        return pocket, body + PAGE_BAG + off, size

    def take_item(self, item: int) -> bool:
        """Bag_TryRemoveItem(bag, item, 1): one off that item's stack; an emptied slot is cleared and the pocket's
        empty slots moved to the end (Pocket_SortEmpty, same loop). False if the bag has none."""
        where = self._pocket(item)
        if where is None:
            return False
        _pocket, base, size = where
        m = self.pm.m
        slots = [list(struct.unpack_from("<HH", m.read(base + 4 * k, 4))) for k in range(size)]
        k = next((k for k, (it, _q) in enumerate(slots) if it == item), None)
        if k is None or slots[k][1] < 1:
            return False
        slots[k][1] -= 1
        if slots[k][1] == 0:
            slots[k][0] = 0
            for i in range(size - 1):
                for j in range(i + 1, size):
                    if slots[i][1] == 0:
                        slots[i], slots[j] = slots[j], slots[i]
        m.write(base, b"".join(struct.pack("<HH", it, q) for it, q in slots))
        return True

    @staticmethod
    def key(rec: bytes) -> str:
        return pk4.mon_key(rec)

    @staticmethod
    def is_standin(rec: bytes) -> bool:
        return pk4.is_standin(rec)

    def mark_dex(self, boxes) -> list:
        """Pokédex for the mons YellowLink shows in the boxes, like receiving a trade in the game
        (platinum.mark_dex = pret Pokedex_Capture: seen/caught, gender, forms, language); stand-ins and eggs left
        out. Call right after write_boxes (PC open hook, main game only). Returns the species newly caught."""
        from .platinum import mark_dex
        recs = []
        for box in boxes:
            for rec in box:
                kind, rec = normalise(rec)
                if kind == "mon":
                    recs.append(rec)
        return mark_dex(self.pm, recs, self.rom)
