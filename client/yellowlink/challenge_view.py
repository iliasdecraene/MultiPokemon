"""What the Soul Link referee sees of the running game: one adapter for Yellow, Crystal and Platinum.

Uses the verified memory helpers in game.py / crystal.py / platinum.py (battle_info, battle_party_slot,
has_poke_balls, current_box_records, move_party_to_box, move_box_to_party, daycare_records, area_of, mon_key...).
"""
from __future__ import annotations

import logging

from . import crystal, pcdb, text
from .soullink import MonInfo

log = logging.getLogger("yellowlink.challenge")
DTO_RESULT_MASK = 0x14      # Platinum FieldBattleDTO.resultMask (pret field_battle_data_transfer.h; written on the
                            # battle's last ~7 frames, 5 = fled, 4 = caught, 1 = won, 2 = lost, 3 = draw - checked
                            # on the ROM)
BAD_SLOT_SCANS = 3          # a PC slot unreadable on this many full scans in a row is logged (a real Bad Egg)


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
            self.stored_keys = self._stored_keys_ds     # (referee: a catch can land in any box)
            self._bad_slot = (None, 0)   # (slot, full scans in a row it was unreadable)
        else:
            self.codec = crystal.Gen2PC(app.pyboy.memory, app.rom) if self.gen2 else \
                pcdb.Gen1PC(app.pyboy.memory, app.rom)
        self._move_error_logged = False

    # --- state -------------------------------------------------------------------------------------------
    def ready(self) -> bool:
        return self.g.in_game()

    def safe(self) -> bool:
        ok = self.g.is_safe()
        if ok and self.ds and hasattr(self.g, "observe_stable"):
            self.g.observe_stable()     # Platinum: the forced moves only write what read the same on an earlier frame
        return ok

    def battle(self):
        info = self.g.battle_info()
        if info is not None and self.ds and "result" not in info:
            dto = self.g.battle_dto() if hasattr(self.g, "battle_dto") else None
            info["result"] = self.g.r32(dto + DTO_RESULT_MASK) if dto else 0     # the game's own outcome
        return info

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

    def run_champion(self) -> bool:
        """Badges (0.7.5): this run's player entered the Hall of Fame in the run's save - the adapter's champion()
        (game.champion / crystal.champion / platinum.champion: the same saved proofs as the main-game Champion
        badge, plus standing in the Hall of Fame room). The app awards the Soul Link badge on it."""
        fn = getattr(self.G, "champion", None)
        try:
            return bool(fn(self.g)) if fn is not None else self.hof()
        except Exception:  # noqa: BLE001 - never break the game loop over a read
            log.exception("reading the Hall of Fame state failed")
            return False

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
        for b, box in enumerate(self.codec.read_slots()):
            for s, rec in enumerate(box):
                kind, rec = self.ppc.normalise(rec)
                if kind == "bad":                        # can't tell which mon that is: try again later
                    n = self._bad_slot[1] + 1 if self._bad_slot[0] == (b, s) else 1
                    self._bad_slot = ((b, s), n)
                    if n == BAD_SLOT_SCANS:
                        log.warning("soul link: PC box %d slot %d stays unreadable (a Bad Egg?) - release/rollback "
                                    "detection is off while it's there", b + 1, s + 1)
                    return None
                if kind == "mon":
                    keys.add(self._key(rec))
        self._bad_slot = (None, 0)
        day = self.g.daycare_records()
        if day is None:
            return None                                  # a Day Care slot can't be read right now
        for rec in day:
            m = self._pk4_info(rec, party=len(rec) == self.pk4.PARTY_SIZE)
            if m is None:
                return None
            keys.add(m.key)
        return keys

    def _stored_keys_ds(self):
        """Keys of every mon in the 18 Platinum boxes (unreadable slots left out)."""
        keys = set()
        try:
            for box in self.codec.read_slots():
                for rec in box:
                    kind, rec = self.ppc.normalise(rec)
                    if kind == "mon":
                        keys.add(self._key(rec))
        except Exception:  # noqa: BLE001
            log.exception("reading the PC boxes failed")
        return keys

    # --- forced moves --------------------------------------------------------------------------------------
    def _move(self, fn, index):
        try:
            return fn(index)
        except RuntimeError as e:                        # e.g. Platinum without its ROM data: can't rebuild
            if not self._move_error_logged:
                self._move_error_logged = True
                log.error("soul link: forced PC move impossible (%s) - the player is asked to do it", e)
            return False

    def to_box(self, slot: int) -> bool:
        ok = self._move(self.g.move_party_to_box, slot)
        log.info("soul link: party slot %d -> PC: %s", slot, ok)
        return ok

    def to_party(self, index: int) -> bool:
        if self.ds:                                     # box() skipped unreadable records: map back
            if not 0 <= index < len(self._box_index):
                return False
            index = self._box_index[index]
        ok = self._move(self.g.move_box_to_party, index)
        log.info("soul link: PC slot %d -> party: %s", index, ok)
        return ok
