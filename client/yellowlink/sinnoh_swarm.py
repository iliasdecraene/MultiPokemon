"""Complete Sinnoh: the swarm ("massive outbreak") changes every 2 hours, the same for every player.

How Platinum does swarms (pret src/special_encounter.c, src/overlay006/swarm.c, wild_encounters.c):
  * the save's SpecialEncounter block holds `swarmDaily` (a random u32, new every day from the record-mixing RNG)
    and `swarmEnabled` (u8, set once by the rival's sister in Sandgem Town after the Hall of Fame -
    scripts_sandgem_town_counterpart_house_1f.s `EnableSwarms`);
  * the swarm is on map `sSwarmMapIdTable[swarmDaily % 22]` (22 routes, below); while it is, that map's wild table's
    two swarm species replace land slots 0 and 1 (40 %). The value is read at every encounter (and by the sister and
    the TV news), nothing is cached.

YellowLink replaces only the choice: time (the server's clock while online, SPEC.md section 11) is cut into 2-hour
UTC slots; every slot gets one of the 22 swarms from a
fixed shuffle (each cycle of 22 slots visits every swarm once, never the same one twice in a row), so every player
on every server computes the same swarm at the same time with no server involved. At a safe frame the app writes
`swarmDaily` = the swarm's index (0..21, the game only uses it modulo 22) whenever it differs - also after the
game's own daily change. `swarmEnabled` is never touched: swarms still start the game's way. Species come from the
running ROM's own tables (Complete Sinnoh's), so the toast always names what the game will really show.
"""
from __future__ import annotations

import hashlib
import logging
import struct
import time

log = logging.getLogger("yellowlink.sinnoh_swarm")

# pret sSwarmMapIdTable (map header ids, generated/map_headers.txt), in the game's order
SWARM_MAPS = (342, 343, 344, 350, 353, 354, 356, 380, 382, 385, 388, 392, 395, 399, 400, 469, 403, 406, 407,
              471, 200, 203)
#            Route 201 202 203 206 207 208 209 214 215 217 218 221 222 224 225 226 227 228 229 230,
#            Valley Windworks, Eterna Forest
NUM_SWARMS = len(SWARM_MAPS)
SLOT_SECONDS = 2 * 60 * 60
SEED = "yellowlink-swarm-v1"      # changing it changes everybody's rotation (keep)

# save body: SpecialEncounter (SAVE_TABLE_ENTRY_ENCOUNTERS; dev/platinum/MEMORY_MAP.md)
PAGE_SPECIAL_ENCOUNTERS = 0x7F24
SE_SWARM_DAILY = 0x04             # int swarmDaily
SE_SWARM_ENABLED = 0x14E          # u8 swarmEnabled (after marsh/swarm, trophy garden, honey trees, radar chains,
#                                   recent routes, 6 roamers, 6 roamer route indexes)
SWARM_SLOTS = (0, 1)              # land slots the swarm species replace


def slot_of(t: float) -> int:
    """The 2-hour slot (UTC) of unix time t."""
    return int(t // SLOT_SECONDS)


def _raw(cycle: int) -> list:
    return sorted(range(NUM_SWARMS), key=lambda i: hashlib.sha1(f"{SEED}:{cycle}:{i}".encode()).digest())


def order(cycle: int) -> list:
    """The swarm indexes of one cycle of 22 slots: a fixed shuffle; its first two are swapped when the first would
    repeat the previous cycle's last (only positions 0/1 ever move, so a cycle's last is always its raw last)."""
    perm = _raw(cycle)
    if perm[0] == _raw(cycle - 1)[-1]:
        perm[0], perm[1] = perm[1], perm[0]
    return perm


def pick(slot: int) -> int:
    """Swarm index (0..21) for a 2-hour slot."""
    return order(slot // NUM_SWARMS)[slot % NUM_SWARMS]


def current(t: float | None = None) -> dict:
    """{'slot', 'index', 'map', 'ends'} of the swarm at unix time t (default now)."""
    t = time.time() if t is None else t
    s = slot_of(t)
    i = pick(s)
    return {"slot": s, "index": i, "map": SWARM_MAPS[i], "ends": (s + 1) * SLOT_SECONDS}


# --- the game's save RAM (pm = platinum.PlatinumMemory) ---------------------------------------------------------
def _base(pm):
    try:
        body = pm.save_body()
    except Exception:  # noqa: BLE001
        return None
    return body + PAGE_SPECIAL_ENCOUNTERS if body else None


def swarm_value(pm):
    b = _base(pm)
    return pm.m.r32(b + SE_SWARM_DAILY) if b else None


def swarm_index(pm):
    v = swarm_value(pm)
    return None if v is None else v % NUM_SWARMS


def enabled(pm) -> bool:
    b = _base(pm)
    return bool(b and pm.m.r8(b + SE_SWARM_ENABLED) == 1)


def write_index(pm, index: int) -> bool:
    """swarmDaily = index (what the game's daily update does with a random number; only % 22 is ever used)."""
    b = _base(pm)
    if not b or not 0 <= index < NUM_SWARMS:
        return False
    pm.m.write(b + SE_SWARM_DAILY, struct.pack("<I", index))
    return pm.m.r32(b + SE_SWARM_DAILY) == index


class SwarmRotator:
    """Polled by the app (Complete Sinnoh). step() every frame; every `every` frames, at a safe frame, makes the
    game's swarm the current 2-hour one. Returns {'index', 'map', 'ends'} when the player should hear about it (a new
    swarm while swarms are on in this save, or swarms just turned on), else None."""

    def __init__(self, pm, every: int = 60, clock=time.time, told=None, on_told=None):
        """clock() = unix time on the shared clock (the app: server time); told = the slot announced last (kept by
        the app in its settings, so a new rotator after travelling doesn't repeat it); on_told(slot) after each
        announcement."""
        self.pm = pm
        self.every = every
        self.clock = clock
        self.on_told = on_told
        self._n = 0
        self._told = told         # slot we announced last

    def step(self):
        self._n += 1
        if self._n % self.every:
            return None
        try:
            if not self.pm.is_safe():
                return None
            cur = current(self.clock())
            if swarm_index(self.pm) != cur["index"]:
                if write_index(self.pm, cur["index"]):
                    log.info("swarm -> #%d (map %d) for slot %d", cur["index"], cur["map"], cur["slot"])
            if enabled(self.pm) and self._told != cur["slot"] and swarm_index(self.pm) == cur["index"]:
                self._told = cur["slot"]
                if self.on_told:
                    self.on_told(cur["slot"])
                return cur
        except Exception:  # noqa: BLE001 - never let this stop the game
            log.exception("swarm step failed")
        return None


def species_of(map_id: int, table_of_map, tables) -> int | None:
    """The swarm species the game shows for `map_id`: its wild table's swarm list, first entry (what
    Swarm_GetMapIdAndSpecies tells the TV/sister; the second entry is the same species in every table).
    table_of_map(map) -> NARC member or None; tables: indexable encounter NARC."""
    t = table_of_map(map_id)
    if t is None:
        return None
    return struct.unpack_from("<I", tables[t], 100)[0] or None
