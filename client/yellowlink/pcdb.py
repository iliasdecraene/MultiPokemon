"""Per-player PC database ("live HOME"). See SPEC.md section 5.

The store is the single source of truth for a player's boxed Pokémon. A game's PC is a *view*:
written in when Bill's PC opens, harvested back when it closes, committed at auto-save snapshots.
This module is game-agnostic except for the Gen-1 (Yellow) box codec at the bottom.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import uuid
from pathlib import Path

from . import text

log = logging.getLogger("yellowlink.pcdb")

STORE_BOXES = 14
MONS_PER_BOX = 20
STANDIN_OT = "YLINK"


def _b64(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def empty_store() -> dict:
    return {"version": 1, "rev": 0, "boxes": [[] for _ in range(STORE_BOXES)], "linked": []}


def valid_store(s) -> bool:
    try:
        return (isinstance(s, dict) and isinstance(s.get("rev"), int) and len(s["boxes"]) == STORE_BOXES
                and all(isinstance(b, list) and len(b) <= MONS_PER_BOX for b in s["boxes"]))
    except (KeyError, TypeError):
        return False


# ---------------------------------------------------------------------------------------------------------------
# Gen 1 (Pokémon Yellow) codec
# ---------------------------------------------------------------------------------------------------------------
G1_STRUCT = 33
G1_NAME = 11
G1_MON = G1_STRUCT + 2 * G1_NAME          # bytes we keep per mon: struct + OT + nickname
G1_BOXES = 12
G1_BOX_BYTES = 0x462                      # count(1) + species(21) + mons(660) + OT(220) + nicks(220)
G1_WBOX = 0xDA7F                          # wBoxDataStart
G1_CUR_BOX = 0xD59F                       # wCurrentBoxNum (bit 7 = boxes initialised)
G1_SRAM_BOX0 = 0xA000                     # sBox1 / sBox7 in banks 2 / 3
G1_BANK_SUM = 0xBA4C                      # sBank2/3AllBoxesChecksum
G1_BOX_SUMS = 0xBA4D                      # sBank2/3IndividualBoxChecksums (6 bytes)
G1_GAMEDATA = (0xA598, 0xB523)            # bank 1 sGameData .. sGameDataEnd (checksum at sGameDataEnd)
G1_CURBOX_SRAM = 0xB0C0                   # bank 1 sCurBoxData
DITTO = 0x4C
TRANSFORM = 0x90


def checksum(data) -> int:
    return (~sum(data)) & 0xFF


def g1_parse_box(raw: bytes) -> list:
    """1122-byte box block -> list of 55-byte mon records."""
    n = raw[0]
    if n > MONS_PER_BOX:
        return []
    mons_at, ot_at, nick_at = 22, 22 + 660, 22 + 660 + 220
    out = []
    for i in range(n):
        out.append(bytes(raw[mons_at + i * G1_STRUCT: mons_at + (i + 1) * G1_STRUCT])
                   + bytes(raw[ot_at + i * G1_NAME: ot_at + (i + 1) * G1_NAME])
                   + bytes(raw[nick_at + i * G1_NAME: nick_at + (i + 1) * G1_NAME]))
    return out


def g1_build_box(mons: list) -> bytes:
    raw = bytearray(G1_BOX_BYTES)
    raw[0] = len(mons)
    for i in range(21):
        raw[1 + i] = mons[i][0] if i < len(mons) else 0xFF
    for i, m in enumerate(mons):
        raw[22 + i * G1_STRUCT: 22 + (i + 1) * G1_STRUCT] = m[:G1_STRUCT]
        raw[682 + i * G1_NAME: 682 + (i + 1) * G1_NAME] = m[G1_STRUCT:G1_STRUCT + G1_NAME]
        raw[902 + i * G1_NAME: 902 + (i + 1) * G1_NAME] = m[G1_STRUCT + G1_NAME:]
    return bytes(raw)


class Gen1PC:
    """Read/write all 12 Yellow boxes (current box in WRAM, the rest in SRAM banks 2/3)."""

    def __init__(self, mem, rom):
        self.m = mem
        self.rom = rom

    def _read(self, bank, addr, n):
        m = self.m
        if bank is None:
            return bytes(m[addr + i] for i in range(n))
        return bytes(m[bank, addr + i] for i in range(n))

    def _write(self, bank, addr, data):
        m = self.m
        for i, v in enumerate(data):
            if bank is None:
                m[addr + i] = v
            else:
                m[bank, addr + i] = v

    @staticmethod
    def slot(i):
        return (2 if i < 6 else 3), G1_SRAM_BOX0 + (i % 6) * G1_BOX_BYTES

    def current_box(self) -> int:
        return self.m[G1_CUR_BOX] & 0x7F

    def initialised(self) -> bool:
        return bool(self.m[G1_CUR_BOX] & 0x80)

    def read_boxes(self) -> list:
        """List of 12 boxes, each a list of 55-byte records. Uninitialised SRAM boxes read as empty."""
        cur = self.current_box()
        boxes = []
        for i in range(G1_BOXES):
            if i == cur:
                raw = self._read(None, G1_WBOX, G1_BOX_BYTES)
            elif self.initialised():
                raw = self._read(*self.slot(i), G1_BOX_BYTES)
            else:
                raw = bytes([0, 0xFF]) + bytes(G1_BOX_BYTES - 2)
            boxes.append(g1_parse_box(raw))
        return boxes

    def write_boxes(self, boxes: list):
        """Write 12 boxes everywhere the game keeps them, with valid checksums."""
        cur = self.current_box()
        built = [g1_build_box(b[:MONS_PER_BOX]) for b in boxes[:G1_BOXES]]
        for bank in (2, 3):
            for k in range(6):
                i = (bank - 2) * 6 + k
                self._write(bank, G1_SRAM_BOX0 + k * G1_BOX_BYTES, built[i])
                self._write(bank, G1_BOX_SUMS + k, [checksum(built[i])])
            whole = self._read(bank, G1_SRAM_BOX0, 6 * G1_BOX_BYTES)
            self._write(bank, G1_BANK_SUM, [checksum(whole)])
        self._write(None, G1_WBOX, built[cur])
        # keep the cartridge save's copy of the current box coherent too (sCurBoxData + main checksum)
        self._write(1, G1_CURBOX_SRAM, built[cur])
        a, b = G1_GAMEDATA
        self._write(1, b, [checksum(self._read(1, a, b - a))])
        self.m[G1_CUR_BOX] = self.m[G1_CUR_BOX] | 0x80

    # --- records <-> entries ---------------------------------------------------------------------------------
    def entry_from_record(self, rec: bytes, origin="yellow", entry_id=None) -> dict:
        return {"id": entry_id or uuid.uuid4().hex, "origin": origin, "dex": self.rom.dex_no(rec[0]),
                "level": rec[3], "nick": text.decode(rec[G1_STRUCT + G1_NAME:]), "ot": text.decode(
                    rec[G1_STRUCT:G1_STRUCT + G1_NAME]), "otid": (rec[12] << 8) | rec[13],
                "gen1": _b64(rec), "gen2": None}

    def record_for(self, entry: dict) -> bytes:
        """The bytes to show in Yellow: the entry's own Gen-1 data, else a stand-in."""
        if entry.get("gen1"):
            return _unb64(entry["gen1"])
        return self.standin(entry)

    def standin(self, entry: dict) -> bytes:
        level = max(2, min(100, int(entry.get("level") or 5)))
        d = bytearray(G1_STRUCT)
        d[0] = DITTO
        hp = (48 * 2 * level) // 100 + level + 10
        d[1], d[2] = hp >> 8, hp & 0xFF
        d[3] = level
        d[5] = d[6] = 0x00          # Normal / Normal
        d[7] = 35                   # Ditto catch rate
        d[8] = TRANSFORM
        exp = level ** 3
        d[14], d[15], d[16] = (exp >> 16) & 0xFF, (exp >> 8) & 0xFF, exp & 0xFF
        d[29] = 10                  # Transform PP
        name = "?" + (entry.get("nick") or "POKEMON").upper()
        return bytes(d) + text.encode(STANDIN_OT, G1_NAME) + text.encode(name[:10], G1_NAME)

    @staticmethod
    def is_standin(rec: bytes) -> bool:
        return rec[0] == DITTO and rec[12] == 0 and rec[13] == 0 and \
            text.decode(rec[G1_STRUCT:G1_STRUCT + G1_NAME]) == STANDIN_OT


# ---------------------------------------------------------------------------------------------------------------
# Store + sync bookkeeping (game-agnostic apart from the codec passed in)
# ---------------------------------------------------------------------------------------------------------------
class PCSync:
    """Holds the committed store, pending changes and the last view written to each game."""

    def __init__(self, path: Path):
        self.path = path
        self.committed = empty_store()
        self.last_view_committed = {}          # game_id -> [[ [entry_id, b64 record], ... ] x boxes]
        self.load()
        self.working = copy.deepcopy(self.committed)
        self.last_view = copy.deepcopy(self.last_view_committed)
        self.pending = False
        self.session_open = False

    # persistence -------------------------------------------------------------------------------
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if valid_store(data.get("store")):
                self.committed = data["store"]
                self.last_view_committed = data.get("last_view", {})
        except (OSError, ValueError, AttributeError):
            pass

    def save(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"store": self.committed, "last_view": self.last_view_committed}),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            log.exception("could not write PC store")
            return False

    def commit(self) -> dict | None:
        """Make pending changes final (call right after an auto-resume snapshot). Returns the store to push."""
        if not self.pending or self.session_open:
            return None
        self.working["rev"] = self.committed.get("rev", 0) + 1
        self.committed = copy.deepcopy(self.working)
        self.last_view_committed = copy.deepcopy(self.last_view)
        self.pending = False
        self.save()
        log.info("PC store committed (rev %d, %d mons)", self.committed["rev"], self.count())
        return self.committed

    def rollback(self):
        """Discard pending changes (the game was rolled back to the last snapshot)."""
        self.working = copy.deepcopy(self.committed)
        self.last_view = copy.deepcopy(self.last_view_committed)
        self.pending = False
        self.session_open = False

    def adopt_remote(self, store: dict) -> bool:
        """Take a newer copy from the server (played on another PC). Only when idle and nothing pending."""
        if not valid_store(store) or self.pending or self.session_open:
            return False
        if store["rev"] <= self.committed.get("rev", 0):
            return False
        self.committed = store
        self.working = copy.deepcopy(store)
        # last views no longer describe what the games hold relative to this store: re-materialise next time
        self.last_view_committed = {}
        self.last_view = {}
        self.save()
        log.info("adopted PC store rev %d from server", store["rev"])
        return True

    def count(self, store=None) -> int:
        return sum(len(b) for b in (store or self.working)["boxes"])

    # sync --------------------------------------------------------------------------------------
    def _entries(self):
        return {e["id"]: e for b in self.working["boxes"] for e in b}

    def open_session(self, game_id: str, codec, trust_game: bool) -> list:
        """Reconcile + compute the view to write. Returns boxes of records for codec.write_boxes()."""
        game_boxes = codec.read_boxes()
        if game_id not in self.working["linked"]:
            self._import(game_id, codec, game_boxes)
        elif trust_game and game_id in self.last_view:
            self._apply_diff(game_id, codec, game_boxes)       # anything that changed since we last looked
        view = []
        rec_boxes = []
        n = len(game_boxes)
        for i in range(n):
            entries = self.working["boxes"][i][:MONS_PER_BOX]
            recs = [codec.record_for(e) for e in entries]
            view.append([[e["id"], _b64(r)] for e, r in zip(entries, recs)])
            rec_boxes.append(recs)
        self.last_view[game_id] = view
        self.session_open = True
        return rec_boxes

    def close_session(self, game_id: str, codec, party_records=None) -> dict:
        """Harvest changes after Bill's PC closed. Returns a report for the UI."""
        game_boxes = codec.read_boxes()
        report = self._apply_diff(game_id, codec, game_boxes, party_records)
        self.session_open = False
        return report

    def _import(self, game_id, codec, game_boxes):
        added = 0
        for i, box in enumerate(game_boxes):
            for rec in box:
                if codec.is_standin(rec):
                    continue
                target = i if len(self.working["boxes"][i]) < MONS_PER_BOX else next(
                    (k for k, b in enumerate(self.working["boxes"]) if len(b) < MONS_PER_BOX), None)
                if target is None:
                    log.warning("PC store full; could not import a mon from %s", game_id)
                    continue
                self.working["boxes"][target].append(codec.entry_from_record(rec, origin=game_id))
                added += 1
        self.working["linked"].append(game_id)
        self.pending = True
        log.info("linked %s to the PC store: imported %d mons", game_id, added)

    def _apply_diff(self, game_id, codec, game_boxes, party_records=None) -> dict:
        """Map the game's boxes back onto store entries using the last view.

        Identical bytes => same entry (possibly moved). New bytes => new entry (deposit).
        Missing entries => removed (withdraw/release), except stand-ins, which are always kept.
        """
        report = {"added": 0, "removed": 0, "standins_returned": [], "standin_party_slots": []}
        before = self.last_view.get(game_id)
        if before is None:
            return report
        entries = self._entries()
        pool = {}                                   # record bytes -> [(box, entry_id), ...]
        for bi, box in enumerate(before):
            for eid, rb in box:
                pool.setdefault(_unb64(rb), []).append((bi, eid))
        new_boxes = [list() for _ in range(STORE_BOXES)]
        used = set()
        # Entries this game never saw (added from another game since) are kept exactly where they are.
        seen = {eid for box in before for eid, _ in box}
        for bi, box in enumerate(self.working["boxes"][:len(game_boxes)]):
            for e in box:
                if e["id"] not in seen:
                    new_boxes[bi].append(e)
        for bi, box in enumerate(game_boxes):
            for rec in box:
                cands = pool.get(bytes(rec), [])
                pick = next((c for c in cands if c[0] == bi and c[1] not in used), None) or \
                    next((c for c in cands if c[1] not in used), None)
                if pick and pick[1] in entries:
                    used.add(pick[1])
                    new_boxes[bi].append(entries[pick[1]])
                elif codec.is_standin(rec):
                    continue                        # an unknown stand-in copy: never create entries from it
                else:
                    new_boxes[bi].append(codec.entry_from_record(rec, origin=game_id))
                    report["added"] += 1
        # entries that disappeared from this game's view
        for bi, box in enumerate(before):
            for eid, rb in box:
                if eid in used or eid not in entries:
                    continue
                if codec.is_standin(_unb64(rb)):
                    new_boxes[bi].append(entries[eid])          # stand-ins can't leave: put it back
                    report["standins_returned"].append(entries[eid].get("nick", "?"))
                else:
                    report["removed"] += 1
        # stand-ins that were withdrawn into the party must be removed from the party by the caller
        if party_records:
            report["standin_party_slots"] = [i for i, r in enumerate(party_records) if codec.is_standin(r)]
        # boxes beyond what this game shows are untouched
        for bi in range(len(game_boxes), STORE_BOXES):
            new_boxes[bi] = self.working["boxes"][bi]
        # overflow safety: a box can hold 20 at most
        for bi in range(STORE_BOXES):
            while len(new_boxes[bi]) > MONS_PER_BOX:
                extra = new_boxes[bi].pop()
                k = next((j for j in range(STORE_BOXES) if len(new_boxes[j]) < MONS_PER_BOX), None)
                if k is None:
                    new_boxes[bi].append(extra)
                    break
                new_boxes[k].append(extra)
        changed = new_boxes != self.working["boxes"]
        self.working["boxes"] = new_boxes
        self.last_view[game_id] = [[[e["id"], _b64(codec.record_for(e))] for e in b] for b in new_boxes[:len(game_boxes)]]
        if changed or report["added"] or report["removed"]:
            self.pending = True
        return report
