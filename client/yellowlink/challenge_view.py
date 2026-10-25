"""What the Soul Link referee sees of the running game: one adapter for Yellow and Crystal.

Uses the verified memory helpers in game.py / crystal.py (battle_info, battle_party_slot, has_poke_balls,
current_box_records, move_party_to_box, move_box_to_party, daycare_records, area_of, mon_key...).
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
        self.codec = crystal.Gen2PC(app.pyboy.memory, app.rom) if self.gen2 else pcdb.Gen1PC(app.pyboy.memory, app.rom)

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
        return self.g.in_hall_of_fame()

    def badges(self) -> int:
        return self.g.badges()

    # --- mons ----------------------------------------------------------------------------------------------
    def _dex(self, species: int) -> int:
        return species if self.gen2 else self.app.rom.dex_no(species)

    def party(self):
        p = self.g.party_checked()
        if p is None:
            return None
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
            return [self._rec(r) for r in self.g.current_box_records()]
        except Exception:  # noqa: BLE001 - never break the game loop over a read
            log.exception("reading the current box failed")
            return []

    def boxes_ok(self) -> bool:
        return self.codec.initialised() if self.gen2 else True

    def all_keys(self):
        """Keys of every mon this save holds: party, all PC boxes and the Day Care."""
        try:
            keys = {self.G.mon_key(m.data) for m in self.g.party()}
            for box in self.codec.read_boxes():
                keys.update(self.G.mon_key(r) for r in box)
            for r in self.g.daycare_records():
                keys.add(self.G.mon_key(r))
            return keys
        except Exception:  # noqa: BLE001
            log.exception("full PC scan failed")
            return None

    # --- forced moves --------------------------------------------------------------------------------------
    def to_box(self, slot: int) -> bool:
        ok = self.g.move_party_to_box(slot)
        log.info("soul link: party slot %d -> PC: %s", slot, ok)
        return ok

    def to_party(self, index: int) -> bool:
        ok = self.g.move_box_to_party(index)
        log.info("soul link: PC slot %d -> party: %s", index, ok)
        return ok
