"""Per-player PC database ("live HOME"). See SPEC.md section 5.

The store is the single source of truth for a player's boxed Pokémon. A game's PC is a *view*:
written in when Bill's PC opens, harvested back when it closes, committed at auto-save snapshots.
This module is game-agnostic except for the Gen-1 (Yellow) box codec at the bottom.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import time
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


LEFT_MAX = 1000       # ids of entries that left the store (withdrawn, traded, released) - never resurrected
DEPARTED_MAX = 600    # per game: records it showed of mons that have left the store since (see PCSync.rebase)


def rec_hash(rec: bytes) -> str:
    return hashlib.sha1(bytes(rec)).hexdigest()[:20]


def empty_ledger(fp=None) -> dict:
    """What left / entered a game's party through the PC (and trades) since its last in-game save (SPEC.md 5,
    "Back to the last in-game save"): `out` = [key, nick] deposited from this game, `back` = [key, nick]
    withdrawn/released here, `changed` = nicks edited in the box UI, `traded` = nicks traded/gifted away,
    `touched` = keys of every mon deposited or withdrawn here (never cancelled out)."""
    return {"fp": fp, "out": [], "back": [], "changed": [], "traded": [], "touched": []}


def _ids(st: dict) -> set:
    return {e["id"] for b in st.get("boxes", []) for e in b} | {e["id"] for e in st.get("incoming", [])}


def _note_left(st: dict, ids):
    """Remember entries that left this store, so a merge with an outdated server copy (an upload whose
    confirmation got lost, or another server sharing this save) never brings them back."""
    left = st.setdefault("left", [])
    for i in ids:
        if i not in left:
            left.append(i)
    del left[:-LEFT_MAX]


def _strip_server(st: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in st.items() if k not in ("srv_rev", "inbox", "srv_trades", "srv_gone")}


ORIGIN_REP = {"yellow": "gen1", "crystal": "gen2", "platinum": "gen4"}


def current_rep(entry: dict, own: str | None = None):
    """(representation name, bytes) of an entry's *current* representation (SPEC.md section 5): the one named by
    its origin, else the first present of gen1, gen2, gen4. (None, None) if it has no data at all.
    `own` (the asking game's representation) is tried right after the origin's: it only matters for entries
    without a known origin."""
    order = [ORIGIN_REP.get(entry.get("origin")), own, "gen1", "gen2", "gen4"]
    for rep in [r for r in order if r]:
        if entry.get(rep):
            return rep, _unb64(entry[rep])
    return None, None


def repair_store(s):
    """Fix an overfull store (a box with more than 20): extras go to 'incoming'. None if beyond repair."""
    try:
        if not isinstance(s, dict) or not isinstance(s.get("boxes"), list) or len(s["boxes"]) != STORE_BOXES:
            return None
        fixed = copy.deepcopy(s)
        fixed.setdefault("rev", 0)
        for box in fixed["boxes"]:
            if not isinstance(box, list):
                return None
            while len(box) > MONS_PER_BOX:
                fixed.setdefault("incoming", []).append(box.pop())
        return fixed if valid_store(fixed) else None
    except (TypeError, KeyError):
        return None


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

    plat_rom = None             # Platinum ROM data (ndsrom.PlatinumData) if the player has that game, for levels

    def record_for(self, entry: dict) -> bytes:
        """The bytes to show in Yellow, from the entry's current representation (SPEC.md section 5): own Gen-1
        data, else a Time-Capsule conversion of the Gen-2 data or a conversion of the Gen-4 data, else a stand-in."""
        rep, data = current_rep(entry, "gen1")
        rec = None
        try:
            if rep == "gen1":
                return data
            if rep == "gen2":
                rec = gen2_to_gen1(data, self.rom)
            elif rep == "gen4":
                from . import pk4
                rec = pk4.to_gen1(data, self.rom, self.plat_rom)
        except Exception:  # noqa: BLE001 - damaged data from a friend: show a stand-in instead
            log.exception("could not convert %s to Gen 1", entry.get("id"))
            rec = None
        return rec if rec is not None else self.standin(entry)

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
    def key(rec: bytes) -> str:
        from .game import mon_key
        return mon_key(rec)

    @staticmethod
    def is_standin(rec: bytes) -> bool:
        return rec[0] == DITTO and rec[12] == 0 and rec[13] == 0 and \
            text.decode(rec[G1_STRUCT:G1_STRUCT + G1_NAME]) == STANDIN_OT

    def mark_dex(self, boxes) -> list:
        """Pokédex for the mons YellowLink shows in the boxes, like the game's own trade receive (pret AddPartyMon:
        FlagAction FLAG_SET on wPokedexOwned + wPokedexSeen): seen + owned for every species there (stand-ins
        left out). Call right after write_boxes (PC open hook, main game only). Returns the dex numbers newly
        owned."""
        from .game import DEX_OWNED, DEX_SEEN
        new = set()
        for box in boxes:
            for rec in box:
                if self.is_standin(rec):
                    continue
                dex = self.rom.dex_no(rec[0])
                if not 1 <= dex <= 151:
                    continue
                i, bit = (dex - 1) // 8, 1 << ((dex - 1) % 8)
                if not self.m[DEX_OWNED + i] & bit:
                    new.add(dex)
                for base in (DEX_OWNED, DEX_SEEN):
                    self.m[base + i] = self.m[base + i] | bit
        return sorted(new)


# ---------------------------------------------------------------------------------------------------------------
# Store + sync bookkeeping (game-agnostic apart from the codec passed in)
# ---------------------------------------------------------------------------------------------------------------
class PCSync:
    """Holds the committed store, pending changes and the last view written to each game."""

    def __init__(self, path: Path, server_key: str = "", home_key: str = ""):
        """server_key: the server this session talks to; home_key: the server whose folder holds this store.
        A store can be shared by several servers (one save on two servers, SPEC.md section 8): each server has its
        own server_rev/synced copy (`sync`), and trade locks remember their server (`lock_servers`) so a trade
        that's still open on one server is never released by another one."""
        self.path = path
        self.server_key = server_key or home_key or ""
        self.home_key = home_key or self.server_key
        self.committed = empty_store()
        self.last_view_committed = {}          # game_id -> [[ [entry_id, b64 record], ... ] x boxes]
        self.sync = {}                         # server key -> {"server_rev": int, "synced": store | None}
        self.locks = {}                        # trade_id -> [entry ids] offered in a trade with unknown outcome
        self.lock_servers = {}                 # trade_id -> server key it belongs to
        self.deferred = None                   # a server copy to merge once nothing is pending
        self.uploading = None
        self.refused = None                    # (mons, free space) when a game couldn't be linked (store full)
        self.ledger_committed = {}             # game_id -> empty_ledger(): changes since its last in-game save
        self.departed_committed = {}           # game_id -> [rec_hash] of shown records whose mon has left since
        self.load()
        self.working = copy.deepcopy(self.committed)
        self.last_view = copy.deepcopy(self.last_view_committed)
        self.ledger = copy.deepcopy(self.ledger_committed)
        self.departed = copy.deepcopy(self.departed_committed)
        self.pending = False
        self.session_open = False

    # persistence -------------------------------------------------------------------------------
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            self._keep_bad_copy(f"unreadable ({e})")
            return
        store = data.get("store") if isinstance(data, dict) else None
        if not valid_store(store):
            store = repair_store(store)
            if store is None:
                self._keep_bad_copy("invalid")
                return
            log.warning("PC store repaired (overfull boxes moved to incoming)")
        self.committed = store
        self.last_view_committed = data.get("last_view", {})
        sync = data.get("sync") if isinstance(data.get("sync"), dict) else {}
        self.sync = {str(k): v for k, v in sync.items() if isinstance(v, dict)}
        if self.home_key not in self.sync:      # older file: the top-level copy belongs to its own server
            self.sync[self.home_key] = {"server_rev": int(data.get("server_rev", 0) or 0),
                                        "synced": data.get("synced")}
        self.locks = data.get("locks") if isinstance(data.get("locks"), dict) else {}
        led = data.get("since_save")
        self.ledger_committed = {str(k): v for k, v in led.items() if isinstance(v, dict)} \
            if isinstance(led, dict) else {}
        dep = data.get("departed")
        self.departed_committed = {str(k): list(v) for k, v in dep.items() if isinstance(v, list)} \
            if isinstance(dep, dict) else {}
        ls = data.get("lock_servers") if isinstance(data.get("lock_servers"), dict) else {}
        self.lock_servers = {t: ls.get(t, self.home_key) for t in self.locks}

    # this server's sync state (see __init__)
    def _mine(self) -> dict:
        return self.sync.setdefault(self.server_key, {"server_rev": 0, "synced": None})

    @property
    def server_rev(self) -> int:
        return int(self._mine().get("server_rev", 0) or 0)

    @server_rev.setter
    def server_rev(self, v):
        self._mine()["server_rev"] = int(v or 0)

    @property
    def synced(self):
        s = self._mine().get("synced")
        return s if valid_store(s) else None

    @synced.setter
    def synced(self, v):
        self._mine()["synced"] = v

    def locks_here(self) -> list:
        """Trade ids of locks that belong to the server we're talking to (only it can tell how they ended)."""
        return [t for t in self.locks if self.lock_servers.get(t, self.home_key) == self.server_key]

    def _keep_bad_copy(self, why):
        """Never silently replace a damaged store with an empty one: keep the file for the host."""
        bad = self.path.with_suffix(f".bad-{int(time.time())}.json")
        try:
            os.replace(self.path, bad)
        except OSError:
            pass
        log.error("PC store %s %s; kept as %s and starting fresh (the server copy will be merged in)",
                  self.path, why, bad)

    def save(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            home = self.sync.get(self.home_key, {})
            tmp.write_text(json.dumps({"store": self.committed, "last_view": self.last_view_committed,
                                       # top-level copy = the home server's, so older versions still read it
                                       "server_rev": int(home.get("server_rev", 0) or 0),
                                       "synced": home.get("synced"), "sync": self.sync,
                                       "locks": self.locks, "lock_servers": self.lock_servers,
                                       "since_save": self.ledger_committed,
                                       "departed": self.departed_committed}),
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
        _note_left(self.working, _ids(self.committed) - _ids(self.working))
        self.committed = copy.deepcopy(self.working)
        self.last_view_committed = copy.deepcopy(self.last_view)
        self.ledger_committed = copy.deepcopy(self.ledger)
        self.departed_committed = copy.deepcopy(self.departed)
        self.pending = False
        self.save()
        log.info("PC store committed (rev %d, %d mons)", self.committed["rev"], self.count())
        return self.committed

    def rollback(self):
        """Discard pending changes (the game was rolled back to the last snapshot)."""
        self.working = copy.deepcopy(self.committed)
        self.last_view = copy.deepcopy(self.last_view_committed)
        self.ledger = copy.deepcopy(self.ledger_committed)
        self.departed = copy.deepcopy(self.departed_committed)
        self.pending = False
        self.session_open = False

    # server sync (SPEC.md section 5) ---------------------------------------------------------------
    def upload(self):
        """(store, base) for a compare-and-swap `pc_put`, or None while something is unfinished."""
        if self.session_open:
            return None
        self.uploading = copy.deepcopy(self.committed)
        return self.uploading, self.server_rev

    def saved(self, srv_rev: int):
        """The server accepted our last upload."""
        self.server_rev = int(srv_rev)
        if self.uploading is not None:
            self.synced = _strip_server(self.uploading)
            self.uploading = None
        self.save()

    def merge_remote(self, remote) -> bool:
        """Bring in the server's copy (another computer uploaded, or our base is stale). Three-way merge:
        our changes since the last sync are replayed on top of the server's copy. Deferred while the game's
        PC is open or changes are pending (done right after the next commit)."""
        if not valid_store(remote):
            return False
        if self.pending or self.session_open:
            self.deferred = remote
            return False
        base = self.synced or empty_store()
        local = self.committed
        ids = lambda st: {e["id"]: (bi, e) for bi, b in enumerate(st["boxes"]) for e in b}  # noqa: E731
        b_ids, l_ids, r_ids = ids(base), ids(local), ids(remote)
        # removed here since the last sync - or ever: an outdated server copy (lost confirmation, another server
        # sharing this save) must not bring back a Pokémon this store already let go
        removed_here = (set(b_ids) - set(l_ids)) | ((set(r_ids) & set(local.get("left") or [])) - set(l_ids))
        added_here = [l_ids[i] for i in l_ids if i not in b_ids]
        server_gone = set(remote.get("srv_gone") or [])
        merged = _strip_server(remote)
        for box in merged["boxes"]:
            box[:] = [e for e in box if e["id"] not in removed_here]
            # changed only here (e.g. an item given in Platinum's box UI keeps the entry's id): keep ours
            for k, e in enumerate(box):
                i = e["id"]
                if i in b_ids and i in l_ids and l_ids[i][1] != b_ids[i][1] and e == b_ids[i][1]:
                    box[k] = copy.deepcopy(l_ids[i][1])
        have = {e["id"] for b in merged["boxes"] for e in b}
        for bi, e in added_here:
            if e["id"] in have or e["id"] in server_gone:
                continue
            target = merged["boxes"][bi] if len(merged["boxes"][bi]) < MONS_PER_BOX else \
                next((b for b in merged["boxes"] if len(b) < MONS_PER_BOX), None)
            (target if target is not None else merged.setdefault("incoming", [])).append(e)
        # removed on the other computer (or traded away): this computer's game PC must not bring them back
        gone = merged.setdefault("gone", [])
        for i in (set(b_ids) | set(l_ids)) - set(r_ids) - {e["id"] for _, e in added_here}:
            if i not in gone:
                gone.append(i)
        for key in ("trades", "linked"):
            merged[key] = sorted(set(merged.get(key, [])) | set(local.get(key, [])))
        merged["left"] = list(local.get("left") or [])
        _note_left(merged, [i for i in remote.get("left") or [] if i not in {e["id"] for _, e in added_here}])
        del gone[:-500]
        merged["rev"] = max(local.get("rev", 0), remote.get("rev", 0)) + 1
        self.committed = merged
        self.working = copy.deepcopy(merged)
        self.synced = _strip_server(remote)
        self.server_rev = int(remote.get("srv_rev", 0))
        self.deferred = None
        self.save()
        log.info("merged the server's PC copy (srv_rev %d, %d mons)", self.server_rev, self.count())
        return True

    def take_deferred(self) -> bool:
        if self.deferred is not None and not self.pending and not self.session_open:
            return self.merge_remote(self.deferred)
        return False

    # locks: offered entries stay put until we know how the trade ended --------------------------------
    def lock(self, trade_id: str, entry_ids: list):
        self.locks[trade_id] = list(entry_ids)
        self.lock_servers[trade_id] = self.server_key
        self.save()

    def unlock(self, trade_id: str):
        self.lock_servers.pop(trade_id, None)
        if self.locks.pop(trade_id, None) is not None:
            self.save()

    def locked_ids(self) -> set:
        return {i for ids in self.locks.values() for i in ids}

    # trades (applied by the server to the stored PCs; SPEC.md "Trade state machine") ------------------
    def apply_trade(self, delta: dict) -> bool:
        """Apply a server trade delta to the committed and the working store (idempotent). True if new."""
        tid = delta.get("trade_id")
        if not tid or tid in self.committed.get("trades", []):
            self.unlock(tid)
            self.server_rev = max(self.server_rev, int(delta.get("srv_rev", 0)))
            return False
        gone_ids = set(delta.get("remove", []))
        gave = [e.get("nick") or "?" for st in (self.working,) for b in st["boxes"] for e in b if e.get("id") in gone_ids]
        if gave:            # a trade/gift away: every game's "since your last save" note (Back to my last save)
            for led in list(self.ledger.values()) + list(self.ledger_committed.values()):
                led.setdefault("traded", []).extend(gave)
                del led["traded"][:-50]
        for st in [x for x in (self.committed, self.working, self.synced) if x is not None]:
            for eid in delta.get("remove", []):
                for box in st["boxes"]:
                    box[:] = [e for e in box if e.get("id") != eid]
                gone = st.setdefault("gone", [])
                gone.append(eid)
                del gone[:-500]
            _note_left(st, delta.get("remove", []))
            if st.get("left"):                      # one that comes back in a trade is ours again
                back = {e.get("id") for e in delta.get("add", [])}
                st["left"] = [i for i in st["left"] if i not in back]
            have = {e["id"] for b in st["boxes"] for e in b} | {e["id"] for e in st.get("incoming", [])}
            for entry in delta.get("add", []):
                if entry.get("id") in have:
                    continue
                box = next((b for b in st["boxes"] if len(b) < MONS_PER_BOX), None)
                if box is None:                     # PC full here: waits until there's room (never a 21st)
                    st.setdefault("incoming", []).append(copy.deepcopy(entry))
                else:
                    box.append(copy.deepcopy(entry))
            trades = st.setdefault("trades", [])
            trades.append(tid)
            del trades[:-200]
        self.server_rev = max(self.server_rev, int(delta.get("srv_rev", 0)))
        self.committed["rev"] = self.committed.get("rev", 0) + 1
        self.working["rev"] = self.committed["rev"]
        self.locks.pop(tid, None)
        self.lock_servers.pop(tid, None)
        self.save()
        log.info("applied trade %s: -%s +%s", tid, delta.get("remove"), [e.get("nick") for e in delta.get("add", [])])
        return True

    def offerable(self) -> list:
        """[(box index, entry)] that can be traded: saved (committed) and not being changed right now."""
        if self.session_open:
            return []
        working = {e["id"] for b in self.working["boxes"] for e in b}
        locked = self.locked_ids()
        return [(bi, e) for bi, box in enumerate(self.committed["boxes"]) for e in box
                if e["id"] in working and e["id"] not in locked]

    def count(self, store=None) -> int:
        return sum(len(b) for b in (store or self.working)["boxes"])

    # sync --------------------------------------------------------------------------------------
    def _entries(self):
        return {e["id"]: e for b in self.working["boxes"] for e in b}

    def _prime(self, game_id: str, codec):
        """Codecs whose game PC is bigger than the store (Platinum: 18 x 30) fold the extra slots into free store
        space when reading; tell them which records the store already knows (they keep those over new ones when
        the store is full)."""
        if hasattr(codec, "known"):
            codec.known = {_unb64(rb) for box in self.last_view.get(game_id) or [] for _eid, rb in box}

    def open_session(self, game_id: str, codec, trust_game: bool) -> list | None:
        """Reconcile + compute the view to write. Returns boxes of records for codec.write_boxes(), or None if the
        game can't be linked yet (its boxes hold more than the store has room for; `refused` says how many):
        then nothing may be written to the game and no session is open."""
        self._prime(game_id, codec)
        self._before_open = (copy.deepcopy(self.working), copy.deepcopy(self.last_view), self.pending)
        game_boxes = codec.read_boxes()
        self.refused = None
        if game_id not in self.working["linked"]:
            if not self._import(game_id, codec, game_boxes):
                return None                                    # store too full to link: don't touch the game
        elif trust_game and game_id in self.last_view:
            self._apply_diff(game_id, codec, game_boxes)       # anything that changed since we last looked
        incoming = self.working.get("incoming") or []
        while incoming:                                        # Pokémon that arrived while the PC was full
            box = next((b for b in self.working["boxes"] if len(b) < MONS_PER_BOX), None)
            if box is None:
                break
            box.append(incoming.pop(0))
            self.pending = True
        self._note_departed(game_id)
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

    def abort_open(self):
        """The view from open_session couldn't be written to the game: forget that open ever happened."""
        if getattr(self, "_before_open", None) is not None:
            self.working, self.last_view, self.pending = self._before_open
            self._before_open = None
        self.session_open = False

    def close_session(self, game_id: str, codec, party_records=None) -> dict:
        """Harvest changes after Bill's PC closed. Returns a report for the UI."""
        self._prime(game_id, codec)
        game_boxes = codec.read_boxes()
        report = self._apply_diff(game_id, codec, game_boxes, party_records)
        self.session_open = False
        return report

    def _import(self, game_id, codec, game_boxes) -> bool:
        """First contact: the game's boxes join the store. Nothing may be lost from the game: if the store has no
        room for everything, codecs with `unplaced` (Platinum: more slots than the store) keep the rest in the game;
        other games - or Platinum when even its spare slots can't hold the rest - aren't linked (False, `refused` =
        (Pokémon to import, free space)) and their boxes stay as they are until there's room."""
        recs = [(i, rec) for i, box in enumerate(game_boxes) for rec in box if not codec.is_standin(rec)]
        free = sum(MONS_PER_BOX - len(b) for b in self.working["boxes"])
        keep_in_game = hasattr(codec, "unplaced")
        spare = getattr(codec, "spare", 0) - len(getattr(codec, "unplaced", []))
        if len(recs) > free and (not keep_in_game or len(recs) - free > spare):
            n = len(recs) + len(getattr(codec, "unplaced", []))           # everything the game holds
            self.refused = (n, free)
            log.warning("PC store has room for %d; %s has %d mons: not linking it yet", free, game_id, n)
            return False
        added = 0
        for i, rec in recs:
            target = i if len(self.working["boxes"][i]) < MONS_PER_BOX else next(
                (k for k, b in enumerate(self.working["boxes"]) if len(b) < MONS_PER_BOX), None)
            if target is None:                      # only for keep_in_game codecs (checked above)
                codec.unplaced.append(rec)
                continue
            self.working["boxes"][target].append(codec.entry_from_record(rec, origin=game_id))
            added += 1
        self.working["linked"].append(game_id)
        self.pending = True
        log.info("linked %s to the PC store: imported %d mons, %d left in the game", game_id, added,
                 len(recs) - added)
        return True

    def _apply_diff(self, game_id, codec, game_boxes, party_records=None) -> dict:
        """Map the game's boxes back onto store entries using the last view.

        Identical bytes => same entry (possibly moved). New bytes => new entry (deposit).
        Missing entries => removed (withdraw/release), except stand-ins, which are always kept.
        """
        report = {"added": 0, "removed": 0, "changed": 0, "standins_returned": [], "standin_party_slots": [],
                  "party_removals": {}, "items_back": [], "items_taken": [], "locked_returned": [],
                  "traded_returned": [], "left_returned": [], "deposited": [], "withdrawn": [], "edited": []}
        before = self.last_view.get(game_id)
        if before is None:
            return report
        entries = self._entries()
        gone = set(self.working.get("gone", []))
        pool = {}                                   # record bytes -> [(box, entry_id), ...]
        for bi, box in enumerate(before):
            for eid, rb in box:
                pool.setdefault(_unb64(rb), []).append((bi, eid))
        new_boxes = [list() for _ in range(STORE_BOXES)]
        used = set()
        locked = self.locked_ids()
        edits = getattr(codec, "edits_in_box", False)   # the game's box UI can change a stored mon in place
        changed_recs = []                               # (box, index in new_boxes[box], record) to resolve below
        # What the game holds after this, where it differs from record_for(entry): the next diff must recognise it
        # (an entry kept as stored while the game shows an edited copy; a traded-away mon still in the box)
        override = {}                                   # entry id -> game record
        tombstones = []                                 # (box, gone entry id, game record)
        for bi, box in enumerate(game_boxes):
            for rec in box:
                cands = pool.get(bytes(rec), [])
                free = [c for c in cands if c[1] not in used]
                pick = next((c for c in free if c[0] == bi and c[1] in entries), None) or \
                    next((c for c in free if c[1] in entries), None) or (free[0] if free else None)
                if pick and pick[1] in entries:
                    used.add(pick[1])
                    new_boxes[bi].append(entries[pick[1]])
                elif pick:
                    # left the store while this game still showed it (traded away, withdrawn in another game,
                    # merged away): never re-import it - that would be a copy
                    used.add(pick[1])
                    tombstones.append((bi, pick[1], bytes(rec)))
                    continue
                elif edits:
                    new_boxes[bi].append(None)      # placeholder: an edited known mon, or a deposit
                    changed_recs.append((bi, len(new_boxes[bi]) - 1, bytes(rec)))
                elif codec.is_standin(rec):
                    continue                        # an unknown stand-in copy: never create entries from it
                else:
                    new_boxes[bi].append(codec.entry_from_record(rec, origin=game_id))
                    report["added"] += 1
                    report["deposited"].append([codec.key(rec), new_boxes[bi][-1].get("nick") or "?"])
        if changed_recs:
            self._match_edits(game_id, codec, before, changed_recs, new_boxes, used, entries, gone, locked, report,
                              override, tombstones)
        # Entries this game never saw (added from another game since) stay in their box, after the ones it knows.
        seen = {eid for box in before for eid, _ in box}
        for bi, box in enumerate(self.working["boxes"][:len(game_boxes)]):
            for e in box:
                if e["id"] not in seen:
                    new_boxes[bi].append(e)
        # entries that disappeared from this game's view
        not_ours = []                               # records of mons that must not stay in the party
        for bi, box in enumerate(before):
            for eid, rb in box:
                if eid in used:
                    continue
                if eid not in entries:
                    # traded away (or moved to another game) while this game still showed it, then withdrawn
                    not_ours.append(_unb64(rb))
                    report["traded_returned" if eid in gone else "left_returned"].append(eid)
                    continue
                if codec.is_standin(_unb64(rb)):
                    new_boxes[bi].append(entries[eid])          # stand-ins can't leave: put it back
                    report["standins_returned"].append(entries[eid].get("nick", "?"))
                elif eid in locked:                 # offered in a trade we don't know the outcome of yet
                    new_boxes[bi].append(entries[eid])
                    not_ours.append(_unb64(rb))
                    report["locked_returned"].append(entries[eid].get("nick", "?"))
                else:
                    report["removed"] += 1
                    report["withdrawn"].append([codec.key(_unb64(rb)), entries[eid].get("nick") or "?"])
        # stand-ins / locked / traded-away mons that were withdrawn must be removed from the party by the caller.
        # Each locked/traded mon takes out at most one party mon, and only if it is that very mon: the same bytes
        # (codecs with `same_mon`: the same bytes apart from what the box UI can change - Platinum's marks and held
        # item). A mon that left the box some other way (released) never takes a look-alike out of the party.
        if party_records:
            same = getattr(codec, "same_mon", None) or (lambda a, b: bytes(a) == bytes(b))
            removals = {i: None for i, r in enumerate(party_records) if codec.is_standin(r)}
            for old in not_ours:
                cands = [i for i, r in enumerate(party_records) if i not in removals and same(old, r)]
                if cands:
                    exact = [i for i in cands if bytes(party_records[i]) == bytes(old)]
                    removals[(exact or cands)[0]] = old
            report["standin_party_slots"] = sorted(removals)
            report["party_removals"] = removals
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
        view = [[[e["id"], _b64(override.get(e["id"]) or codec.record_for(e))] for e in b]
                for b in new_boxes[:len(game_boxes)]]
        for bi, eid, rec in tombstones:
            if bi < len(view):
                view[bi].append([eid, _b64(rec)])
        self.last_view[game_id] = view
        if changed or report["added"] or report["removed"]:
            self.pending = True
        self._ledger_note(game_id, report)
        return report

    # "Back to my last in-game save" (SPEC.md section 5): the game comes back from its battery save -------------
    def note_battery(self, game_id: str, fp) -> bool:
        """`fp` = fingerprint of the game's in-game save (its battery, without what YellowLink writes there). A
        new fingerprint means the player saved in-game: the game's since-the-save notes start over. True then.
        Applied to the committed copy too (the battery is a fact on disk, whatever happens to pending changes)."""
        if fp is None:
            return False
        led = self.ledger.get(game_id)
        if led is not None and led.get("fp") in (fp, None):
            if led.get("fp") is None:           # first time we see this game's save: keep what we noted so far
                led["fp"] = fp
                self.ledger_committed.setdefault(game_id, empty_ledger(fp))["fp"] = fp
            return False
        self.ledger[game_id] = empty_ledger(fp)
        self.ledger_committed[game_id] = empty_ledger(fp)
        self.save()
        return led is not None

    def since_save(self, game_id: str) -> dict:
        """What changed through this game's PC (and trades) since its last in-game save."""
        return self.ledger.get(game_id) or empty_ledger()

    def _ledger_note(self, game_id, report):
        led = self.ledger.setdefault(game_id, empty_ledger())
        for k in ("out", "back", "changed", "traded", "touched"):
            led.setdefault(k, [])
        for key, _nick in report.get("withdrawn", []) + report.get("deposited", []):
            if key not in led["touched"]:
                led["touched"].append(key)
        for key, nick in report.get("withdrawn", []):
            hit = next((x for x in led["out"] if x[0] == key), None)
            if hit:                         # deposited since the save and taken back out: as it was at the save
                led["out"].remove(hit)
            else:
                led["back"].append([key, nick])
        for key, nick in report.get("deposited", []):
            hit = next((x for x in led["back"] if x[0] == key), None)
            if hit:                         # withdrawn since the save and put back: as it was at the save
                led["back"].remove(hit)
            else:
                led["out"].append([key, nick])
        led["changed"].extend(report.get("edited", []))
        for k in ("out", "back", "changed", "touched"):
            del led[k][:-200]

    def _note_departed(self, game_id):
        """Before a new view replaces this game's last one: remember the records it showed of mons that have left
        the store since (traded away, withdrawn in another game). An older in-game save still has them in its
        boxes; rebase() must never take them for new Pokémon."""
        ids = set(self._entries())
        dep = self.departed.setdefault(game_id, [])
        for box in self.last_view.get(game_id) or []:
            for eid, rb in box:
                if eid not in ids:
                    h = rec_hash(_unb64(rb))
                    if h not in dep:
                        dep.append(h)
        del dep[:-DEPARTED_MAX]

    def rebase(self, game_id: str, codec) -> bool:
        """The game was loaded from its battery save (YellowLink's "Back to my last in-game save", the game's own
        soft reset + CONTINUE, or a start without resume snapshot): its boxes are those of that save, which can be
        older than our last view. Make the last view what the game really holds now, so the next sync is right:
          * a record we showed for an entry that is still in the store -> that entry (unchanged)
          * a record of a mon that has left the store since (traded, gifted, withdrawn in another game, or the same
            mon in the store with newer bytes) -> a tombstone: never imported again, the store wins
          * a record we never saw (caught straight into a box, or withdrawn here after the save, so the save
            still has it in its box) -> left out: the next sync imports it as a deposit
        Store entries the save doesn't show are simply new to this game (they stay). Returns False if the game
        isn't linked yet (nothing to do)."""
        if self.session_open or game_id not in self.working.get("linked", []) or game_id not in self.last_view:
            return False
        self._prime(game_id, codec)
        game_boxes = codec.read_boxes()
        entries = self._entries()
        self._note_departed(game_id)
        departed = set(self.departed.get(game_id, []))
        pool = {}
        for bi, box in enumerate(self.last_view.get(game_id) or []):
            for eid, rb in box:
                pool.setdefault(_unb64(rb), []).append((bi, eid))
        store_keys = None
        used = set()
        view = []
        kept = dropped = new = 0
        for bi, box in enumerate(game_boxes):
            row = []
            for rec in box:
                rec = bytes(rec)
                free = [c for c in pool.get(rec, []) if c[1] not in used]
                pick = next((c for c in free if c[0] == bi and c[1] in entries), None) or \
                    next((c for c in free if c[1] in entries), None) or (free[0] if free else None)
                if pick:
                    used.add(pick[1])
                    row.append([pick[1], _b64(rec)])
                    kept += pick[1] in entries
                    dropped += pick[1] not in entries
                    continue
                if codec.is_standin(rec):
                    continue                    # unknown stand-in copy: the diff ignores those anyway
                touched = set(self.since_save(game_id).get("touched") or [])
                if store_keys is None:
                    store_keys = set()
                    for e in (entries.values() if touched else []):
                        try:
                            r = codec.record_for(e)
                            if not codec.is_standin(r):
                                store_keys.add(codec.key(r))
                        except Exception:  # noqa: BLE001 - a damaged entry only weakens this check
                            pass
                # departed, or a mon moved through this PC since the save that the store holds with newer bytes
                # (withdrawn, levelled up, deposited again)
                if rec_hash(rec) in departed or (codec.key(rec) in touched and codec.key(rec) in store_keys):
                    row.append(["rebased-" + rec_hash(rec), _b64(rec)])
                    dropped += 1
                else:
                    new += 1                    # not in the view: imported at the next sync
            view.append(row)
        self.last_view[game_id] = view
        self.pending = True                     # committed with the next snapshot (which must match the game)
        log.info("%s loaded from its save: view rebased (%d known, %d left the store, %d new)", game_id, kept,
                 dropped, new)
        return True

    def party_clones(self, game_id: str, codec, party_records) -> list:
        """After the game came back from its save: party slots holding a Pokémon that was put into the YellowLink
        PC from this game after that save (it's in the store - or traded on - so the party copy is a clone).
        Matched by key, each noted deposit at most once."""
        out = [x[0] for x in self.since_save(game_id).get("out", [])]
        slots = []
        for i, rec in enumerate(party_records or []):
            try:
                k = codec.key(bytes(rec))
            except Exception:  # noqa: BLE001
                continue
            if k in out:
                out.remove(k)
                slots.append(i)
        return slots

    def loaded_from_save(self, game_id: str, fp):
        """The game's state is its save again: nothing has changed since it (working copy; committed with the
        next snapshot, like the rebased view)."""
        self.ledger[game_id] = empty_ledger(fp)
        self.pending = True

    @staticmethod
    def _match_edits(game_id, codec, before, changed_recs, new_boxes, used, entries, gone, locked, report,
                     override, tombstones):
        """Records whose bytes match nothing in the last view (codecs with `edits_in_box`): a known mon the box
        UI changed in place (Platinum: MARK, give/take an item) is codec.same_mon as an entry that disappeared
        (same bytes apart from marks/item; same box preferred). Otherwise it's a deposit (new entry).
          * normal entry -> codec.updated_entry (keeps the id; locks/trades still refer to it)
          * stand-in, locked (in a trade) or traded away -> the stored mon stays as it was (the game's copy is
            rewritten at the next open); the bag gets back what the box UI took from it (`items_back`) and loses
            what the box UI put into it from the stored mon (`items_taken`), so no item exists twice."""
        vanished = [(bi, eid, _unb64(rb)) for bi, box in enumerate(before) for eid, rb in box if eid not in used]
        for bi, pos, rec in changed_recs:
            cands = [c for c in vanished if c[1] not in used and codec.same_mon(c[2], rec)]
            pick = min(cands, key=lambda c: (c[0] != bi, _distance(codec, c[2], rec))) if cands else None
            entry = None
            if pick is not None:
                _bi, eid, old = pick
                used.add(eid)
                if eid in entries and not codec.is_standin(old) and eid not in locked:
                    entry = codec.updated_entry(entries[eid], rec, origin=game_id)
                    if entry != entries[eid]:
                        report["changed"] += 1
                        report["edited"].append(entry.get("nick") or "?")
                    else:
                        override[eid] = rec                 # marks on another game's mon: kept as stored
                else:
                    if eid in entries:
                        entry = entries[eid]
                        override[eid] = rec
                    else:
                        tombstones.append((bi, eid, rec))
                    give, take = codec.item_changes(old, rec)
                    report["items_back"].extend(give)
                    report["items_taken"].extend(take)
            elif not codec.is_standin(rec):
                entry = codec.entry_from_record(rec, origin=game_id)
                report["added"] += 1
                report["deposited"].append([codec.key(rec), entry.get("nick") or "?"])
            new_boxes[bi][pos] = entry
        for box in new_boxes:
            box[:] = [e for e in box if e is not None]


def _distance(codec, a: bytes, b: bytes) -> int:
    """How different two records are (for telling apart mons with the same key)."""
    fn = getattr(codec, "distance", None)
    if fn is not None:
        return fn(a, b)
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))


def gen2_to_gen1(rec2: bytes, rom) -> bytes | None:
    """Convert a 55-byte Gen-2 (Crystal) box record to a 55-byte Gen-1 (Yellow) one, like the Time Capsule:
    only Gen-1 species with Gen-1 moves can go; held item, happiness and Pokérus are dropped.
    None if the mon can't exist in Gen 1 (it then shows as a stand-in)."""
    from .game import calc_stats
    if len(rec2) < 54 or (len(rec2) > 54 and rec2[54]):         # eggs stay in Gen 2
        return None
    dex, level = rec2[0], rec2[31]
    species = rom.species_for_dex(dex)
    if not species or not 1 <= level <= 100:
        return None
    moves = rec2[2:6]
    if any(m > 165 for m in moves) or not any(moves):
        return None
    base = rom.base_stats(species)
    if base is None:
        return None
    d = bytearray(G1_STRUCT)
    d[0] = species
    d[3] = level
    d[5], d[6] = base[5], base[6]
    d[7] = rom.catch_rate(species)
    d[8:12] = moves
    d[12:14] = rec2[6:8]
    d[14:17] = rec2[8:11]
    d[17:27] = rec2[11:21]
    d[27:29] = rec2[21:23]
    d[29:33] = rec2[23:27]
    party = bytearray(44)                       # calc_stats works on the party layout (level at 33)
    party[:G1_STRUCT] = d
    party[33] = level
    hp = calc_stats(bytes(party), base)[0]
    d[1], d[2] = hp >> 8, hp & 0xFF
    return bytes(d) + bytes(rec2[32:43]) + bytes(rec2[43:54])
