"""What the Soul Link referee sees of the running game: one adapter for Yellow, Crystal and Platinum.

Uses the verified memory helpers in game.py / crystal.py / platinum.py (battle_info, battle_party_slot,
has_poke_balls, current_box_records, move_party_to_box, move_box_to_party, daycare_records, area_of, mon_key...).
"""
from __future__ import annotations

import logging

from . import crystal, pcdb, text
from .soullink import MonInfo

log = logging.getLogger("yellowlink.challenge")


class ChallengeView:
    def __init__(self, app):
        self.app = app
        self.g = app.game
        self.G = app.G
        self.gen2 = app.gen2
        self.ds = bool(getattr(app, "ds", False))
        if self.ds:
            from . import pk4, platinum_pc
            self.pk4, self.ppc = pk4, platinum_pc
            # only reads the 18 x 30 box slots here (no store conversion, so no ROM data needed)
            self.codec = platinum_pc.Gen4PC(self.g, None)
            self._key = getattr(self.G, "mon_key", None) or pk4.mon_key
            self._box_index = []         # box() position -> index in current_box_records() (unreadable ones skipped)
        else:
            self.codec = crystal.Gen2PC(app.pyboy.memory, app.rom) if self.gen2 else \
                pcdb.Gen1PC(app.pyboy.memory, app.rom)

    # --- state -------------------------------------------------------------------------------------------
    def ready(self) -> bool:
        return self.g.in_game()

    def safe(self) -> bool:
        return self.g.is_safe()

    def battle(self):
        return self.g.battle_info()

    def battle_slot(self):
        return self.g.battle_party_slot()

    def has_balls(self) -> bool:
        return self.g.has_poke_balls()

    def area(self) -> str:
        return self.G.area_of(self.g.map_id())

    def area_name(self, area: str) -> str:
        return self.G.area_name(area.split("*")[0])

    def hof(self) -> bool:
        """Entered the Hall of Fame at least once (saved flags, so a short safe window can't be missed)."""
        g = self.g
        if self.ds:
            for name in ("entered_hall_of_fame", "in_hall_of_fame"):
                fn = getattr(g, name, None)
                if fn is not None and fn():
                    return True
            fn = getattr(g, "hall_of_fame_count", None)
            return bool(fn is not None and fn() > 0)
        if self.gen2:
            return g.entered_hall_of_fame() or g.in_hall_of_fame()
        return g.hall_of_fame_count() > 0 or g.in_hall_of_fame()

    def hold(self) -> bool:
        """Party partly hidden by the game (Crystal's Bug-Catching Contest). Platinum has nothing like it."""
        return self.gen2 and self.g.in_bug_contest()

    def badges(self) -> int:
        """Badge bitfield for the challenge's progress display (the panel counts the bits)."""
        if self.ds:
            mask = getattr(self.g, "badge_mask", None)
            if mask is not None:
                return mask()
            return (1 << self.g.badges()) - 1
        return self.g.badges()

    # --- mons ----------------------------------------------------------------------------------------------
    def _dex(self, species: int) -> int:
        return species if self.gen2 else self.app.rom.dex_no(species)

    def _pk4_info(self, rec: bytes, party: bool):
        """MonInfo for a Gen-4 record (encrypted, or decrypted in place by the game mid-frame), or None for an
        empty slot / a record that can't be read right now (bad checksum)."""
        pk4 = self.pk4
        rec = bytes(rec)
        info = getattr(self.G, "mon_info", None)
        if info is not None:                             # platinum.py's reader (any of the game's record states)
            if party and not pk4.valid(rec):
                return None                              # the game is working on it: read the party next frame
            d = info(rec)
            return None if d is None else MonInfo(d["key"], d["dex"], d["hp"] if party else -1, bool(d["egg"]),
                                                  d.get("nick") or "")
        if party and len(rec) == pk4.PARTY_SIZE:
            if not pk4.valid(rec):
                return None
            pk = pk4.PK4(pk4.decrypt(rec))
            hp = pk.hp
        else:
            kind, rec = self.ppc.normalise(rec[:pk4.BOX_SIZE])
            if kind != "mon":
                return None
            pk = pk4.PK4(pk4.decrypt(rec))
            hp = -1                                      # box records have no current HP (withdrawing heals)
        if pk.species == 0:
            return None
        try:
            nick = pk.nickname
        except Exception:  # noqa: BLE001
            nick = ""
        return MonInfo(self._key(rec), pk.species, hp, bool(pk.is_egg), nick)

    def party(self):
        p = self.g.party_checked()
        if p is None:
            return None
        if self.ds:
            out = []
            for rec in p:
                m = self._pk4_info(rec, party=True)
                if m is None:                            # party_checked() checks the checksums; be safe anyway
                    return None
                out.append(m)
            return out
        return [MonInfo(self.G.mon_key(m.data), self._dex(m.species), m.hp, bool(getattr(m, "egg", False)),
                        m.nick_str()) for m in p]

    def _rec(self, rec: bytes) -> MonInfo:
        if self.gen2:
            egg = len(rec) > 54 and bool(rec[54])
            return MonInfo(self.G.mon_key(rec), rec[0], -1, egg, text.decode(rec[43:54]))
        hp = (rec[1] << 8) | rec[2]
        return MonInfo(self.G.mon_key(rec), self._dex(rec[0]), hp, False, text.decode(rec[44:55]))

    def box(self):
        try:
            if self.ds:
                out, index = [], []
                for i, r in enumerate(self.g.current_box_records()):
                    m = self._pk4_info(r, party=False)
                    if m is not None:
                        out.append(m)
                        index.append(i)
                self._box_index = index
                return out
            return [self._rec(r) for r in self.g.current_box_records()]
        except Exception:  # noqa: BLE001 - never break the game loop over a read
            log.exception("reading the current box failed")
            return []

    def boxes_ok(self) -> bool:
        if self.ds:
            return self.codec.initialised()
        return self.codec.initialised() if self.gen2 else True

    def all_keys(self):
        """Keys of every mon this save holds: party, all PC boxes and the Day Care. None when that can't be known
        right now (party mid-update, a Platinum record the game is working on): a missing key means "released"."""
        try:
            if self.ds:
                return self._all_keys_ds()
            keys = {self.G.mon_key(m.data) for m in self.g.party()}
            for box in self.codec.read_boxes():
                keys.update(self.G.mon_key(r) for r in box)
            for r in self.g.daycare_records():
                keys.add(self.G.mon_key(r))
            return keys
        except Exception:  # noqa: BLE001
            log.exception("full PC scan failed")
            return None

    def _all_keys_ds(self):
        party = self.party()
        if party is None:
            return None
        keys = {m.key for m in party}
        for box in self.codec.read_slots():
            for rec in box:
                kind, rec = self.ppc.normalise(rec)
                if kind == "bad":
                    return None                          # can't tell which mon that is: try again later
                if kind == "mon":
                    keys.add(self._key(rec))
        for rec in self.g.daycare_records():
            m = self._pk4_info(rec, party=len(rec) == self.pk4.PARTY_SIZE)
            if m is None:
                return None
            keys.add(m.key)
        return keys

    # --- forced moves --------------------------------------------------------------------------------------
    def to_box(self, slot: int) -> bool:
        ok = self.g.move_party_to_box(slot)
        log.info("soul link: party slot %d -> PC: %s", slot, ok)
        return ok

    def to_party(self, index: int) -> bool:
        if self.ds:                                     # box() skipped unreadable records: map back
            if not 0 <= index < len(self._box_index):
                return False
            index = self._box_index[index]
        ok = self.g.move_box_to_party(index)
        log.info("soul link: PC slot %d -> party: %s", index, ok)
        return ok
