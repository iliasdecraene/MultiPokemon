"""A tiny stand-in spec (same interface as client/yellowlink/sinnoh_complete.py) for the engine's tests while the
real spec is being designed. Route 201's two most common grass slots become Pikachu / Ralts Lv2 and Route 202's
first slot Ralts Lv3 - easy to see in a forced encounter. Not shipped: the app only uses sinnoh_complete.py."""
from typing import NamedTuple

from yellowlink import encdata

SPEC_VERSION = 0
NARC_PATH = encdata.NARC_PATH
TABLE_COUNT, TABLE_SIZE = encdata.TABLE_COUNT, encdata.TABLE_SIZE
SLOT_KINDS, RATE_OFFSETS = encdata.SLOT_KINDS, encdata.RATE_OFFSETS


class SlotEdit(NamedTuple):
    table: int
    name: str
    kind: str
    slot: int
    species: int
    level: int | None = None
    min_level: int | None = None
    max_level: int | None = None
    old_species: int = 0
    old_level: int | None = None
    old_min: int | None = None
    old_max: int | None = None


class RateEdit(NamedTuple):
    table: int
    name: str
    kind: str
    rate: int
    old_rate: int


STARLY, BIDOOF, SHINX, PIKACHU, RALTS = 396, 399, 403, 25, 280
EDITS = (
    SlotEdit(140, "route_201", "land", 0, PIKACHU, 2, old_species=STARLY, old_level=2),
    SlotEdit(140, "route_201", "land", 1, RALTS, 2, old_species=BIDOOF, old_level=2),
    SlotEdit(141, "route_202", "land", 0, RALTS, 3, old_species=SHINX, old_level=3),
)
RATE_EDITS = ()


def apply(member_index: int, data: bytes) -> bytes:
    return encdata.apply_edits(member_index, data, EDITS, RATE_EDITS)


def edits_for(member_index: int) -> list:
    return [e for e in EDITS if e.table == member_index]
