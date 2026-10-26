"""Complete Sinnoh: the Mystery-Gift-only events of Pokémon Platinum, unlocked in game like a real distribution.

Platinum has four events that only a Nintendo distribution could start (pret src/scrcmd_mystery_gift.c = what the
Mystery Gift deliveryman writes when you pick the gift up; the event scripts check exactly that):

    event         key item (bag)        var written by the gift                      the game's own script then
    member_card   MEMBER CARD   454     VAR_DISTRIBUTION_EVENT_DARKRAI 0x4043=0x1209  Harbor Inn dream -> Darkrai
    oaks_letter   OAK'S LETTER  452     VAR_DISTRIBUTION_EVENT_SHAYMIN 0x4044=0x1112  Route 224 -> Seabreak Path -> Shaymin
                                        + VAR_SHAYMIN_EVENT_STATE 0x4057: 0 -> 1
    azure_flute   AZURE FLUTE   455     VAR_DISTRIBUTION_EVENT_ARCEUS  0x4045=0x1123  Spear Pillar -> Hall of Origin -> Arceus
    secret_key    SECRET KEY    467     VAR_DISTRIBUTION_EVENT_ROTOM   0x4046=0x1103  Rotom's room (Rotom's forms)

(The Manaphy egg is the only other Mystery-Gift-only content; mythicals are left for a later YellowLink feature.)

We do the same writes - bag slot + vars, nothing else - at a safe overworld frame once the save meets the event's
condition (Ilias's choice; the Darkrai/Shaymin/Arceus scripts need FLAG_GAME_COMPLETED + National Dex anyway):

    member_card, oaks_letter   the National Dex
    secret_key                 the first Rotom caught (Pokédex "caught" bit) or the National Dex, whichever comes first
    azure_flute                the National Dex + Dialga and Palkia caught at the Spear Pillar (FLAG_CAUGHT_DIALGA and
                               FLAG_CAUGHT_PALKIA; only then is the Spear Pillar back to the map where the flute works)

and say so with a toast. Everything after that is the game's own scripts. Idempotent: the
state lives in the save (an event is "given" when its item is in the bag and its var holds the magic number), so a
reload / second computer never gives anything twice. Addresses: save body pages (platinum.PAGE_*), verified by
dev/sinnoh/verify.py on the real ROM (SPEC.md "Complete Sinnoh").
"""
from __future__ import annotations

import logging
import struct
from typing import NamedTuple

log = logging.getLogger("yellowlink.sinnoh_events")

# save body layout (platinum.py PAGE_*; pret include/bag.h, include/vars_flags.h, include/pokedex.h)
PAGE_BAG = 0x0630
BAG_KEY_ITEMS = 165 * 4                 # Bag.keyItems follows items[165] (BagItem = u16 item, u16 quantity)
KEY_ITEM_POCKET_SIZE = 50
PAGE_VARS_FLAGS = 0x0DAC                # u16 vars[288] from VARS_START 0x4000, then u8 flags[364]
VF_FLAGS = 0x240
VARS_START, VARS_END = 0x4000, 0x4120
PAGE_POKEDEX = 0x1328
POKEDEX_MAGIC = 0xBEEFCAFE
POKEDEX_CAUGHT = 0x04                   # Pokedex.caughtPokemon[16] (u32): bit (species - 1)
POKEDEX_NATIONAL = 0x31B                # Pokedex.nationalDexObtained (u8)
NUM_FLAGS = 2912
FLAG_GAME_COMPLETED = 2404
FLAG_CAUGHT_DIALGA = 208                # set by scripts_spear_pillar_dialga.s only when Dialga is caught
FLAG_CAUGHT_PALKIA = 209
SPECIES_ROTOM = 479

# Event.requires
NATIONAL_DEX = "national_dex"
ROTOM_OR_NATIONAL_DEX = "rotom_or_national_dex"
NATIONAL_DEX_DIALGA_PALKIA = "national_dex_dialga_palkia"

VAR_SHAYMIN_EVENT_STATE = 0x4057


class Event(NamedTuple):
    id: str
    item: int            # key item id (pret generated/items.txt)
    item_name: str
    var: int             # VAR_DISTRIBUTION_EVENT_*
    magic: int           # sDistributionEventMagicNumbers
    legend: str          # what it leads to (for logs / PLAN)
    hint: str            # toast after "Mystery Gift: <item>!"
    requires: str = NATIONAL_DEX


EVENTS = (
    Event("member_card", 454, "MEMBER CARD", 0x4043, 0x1209, "Darkrai (Newmoon Island)",
          "Once the boy in Canalave City sleeps well again, stay at the Harbor Inn..."),
    Event("oaks_letter", 452, "OAK'S LETTER", 0x4044, 0x1112, "Shaymin (Flower Paradise)",
          "Professor Oak is waiting on Route 224."),
    Event("azure_flute", 455, "AZURE FLUTE", 0x4045, 0x1123, "Arceus (Hall of Origin)",
          "Play it at the top of the Spear Pillar.", NATIONAL_DEX_DIALGA_PALKIA),
    Event("secret_key", 467, "SECRET KEY", 0x4046, 0x1103, "Rotom's forms (Rotom's room, Eterna City)",
          "It opens a hidden room in the Galactic building in Eterna City.", ROTOM_OR_NATIONAL_DEX),
)
EVENTS_BY_ID = {e.id: e for e in EVENTS}
_warned: set = set()


def _body(pm):
    try:
        return pm.save_body()
    except Exception:  # noqa: BLE001 - a fake/broken bus: nothing to do
        return None


def national_dex(pm) -> bool:
    """The save has the National Dex (Pokedex.nationalDexObtained, with a valid Pokédex block)."""
    body = _body(pm)
    if not body or pm.m.r32(body + PAGE_POKEDEX) != POKEDEX_MAGIC:
        return False
    return pm.m.r8(body + PAGE_POKEDEX + POKEDEX_NATIONAL) == 1


def _flag(pm, body, n) -> bool:
    return bool(pm.m.r8(body + PAGE_VARS_FLAGS + VF_FLAGS + n // 8) >> (n % 8) & 1)


def caught(pm, species: int) -> bool:
    """The Pokédex has `species` as caught (Pokedex.caughtPokemon bit species - 1)."""
    body = _body(pm)
    if not body or pm.m.r32(body + PAGE_POKEDEX) != POKEDEX_MAGIC or not 1 <= species <= 493:
        return False
    i = species - 1
    return bool(pm.m.r8(body + PAGE_POKEDEX + POKEDEX_CAUGHT + i // 8) >> (i % 8) & 1)


def due(pm, ev: Event) -> bool:
    """The save meets the event's condition (Event.requires)."""
    body = _body(pm)
    if not body:
        return False
    nat = national_dex(pm)
    if ev.requires == ROTOM_OR_NATIONAL_DEX:
        return nat or caught(pm, SPECIES_ROTOM)
    if ev.requires == NATIONAL_DEX_DIALGA_PALKIA:
        return nat and _flag(pm, body, FLAG_CAUGHT_DIALGA) and _flag(pm, body, FLAG_CAUGHT_PALKIA)
    return nat


def _var_addr(body, var):
    if not VARS_START <= var < VARS_END:
        raise ValueError(var)
    return body + PAGE_VARS_FLAGS + 2 * (var - VARS_START)


def key_items(pm) -> list:
    """[(item, quantity)] of the 50 key-item slots (empty slots included)."""
    body = _body(pm)
    if not body:
        return []
    raw = pm.m.read(body + PAGE_BAG + BAG_KEY_ITEMS, 4 * KEY_ITEM_POCKET_SIZE)
    return [struct.unpack_from("<HH", raw, 4 * i) for i in range(KEY_ITEM_POCKET_SIZE)]


def has_item(pm, item: int) -> bool:
    return any(i == item and q > 0 for i, q in key_items(pm))


def given(pm, ev: Event) -> bool:
    body = _body(pm)
    if not body:
        return False
    return has_item(pm, ev.item) and pm.m.r16(_var_addr(body, ev.var)) == ev.magic


def pending(pm) -> list:
    """Events the player should have now (condition met) and doesn't."""
    return [ev for ev in EVENTS if due(pm, ev) and not given(pm, ev)]


def give(pm, ev: Event) -> bool:
    """What InitDarkraiEvent / InitShayminEvent / InitArceusEvent / InitRotomEvent do: Bag_TryAddItem(item, 1) into the
    key-item pocket (an existing stack or the first slot with item 0 and quantity 0) + the var(s). Call only when
    pm.is_safe(). False if the pocket is full (nothing written then)."""
    body = _body(pm)
    if not body:
        return False
    slots = key_items(pm)
    target = None
    for i, (item, qty) in enumerate(slots):
        if item == ev.item:
            target = None if qty > 0 else i
            if qty > 0:
                break
        if target is None and item == 0 and qty == 0:
            target = i
    have = any(item == ev.item and qty > 0 for item, qty in slots)
    if not have:
        if target is None:
            if ev.id not in _warned:
                _warned.add(ev.id)
                log.warning("key item pocket full: can't give %s yet", ev.item_name)
            return False
        pm.m.write(body + PAGE_BAG + BAG_KEY_ITEMS + 4 * target, struct.pack("<HH", ev.item, 1))
    pm.m.write(_var_addr(body, ev.var), struct.pack("<H", ev.magic))
    if ev.id == "oaks_letter" and pm.m.r16(_var_addr(body, VAR_SHAYMIN_EVENT_STATE)) == 0:
        pm.m.write(_var_addr(body, VAR_SHAYMIN_EVENT_STATE), struct.pack("<H", 1))
    ok = given(pm, ev)
    log.info("Mystery Gift %s (%s): %s", ev.item_name, ev.legend, "given" if ok else "FAILED to read back")
    return ok


class EventUnlocker:
    """Polled by the app (Platinum, Complete Sinnoh). step() every frame; it looks at the save every `every`
    frames and gives what is due at a safe frame. Returns the events given this call (for the toasts)."""

    def __init__(self, pm, every: int = 60):
        self.pm = pm
        self.every = every
        self._n = 0
        self._due = None          # events seen pending on the previous check (two checks in a row before giving)

    def step(self) -> list:
        self._n += 1
        if self._n % self.every:
            return []
        try:
            if not self.pm.is_safe():
                return []
            due = pending(self.pm)
        except Exception:  # noqa: BLE001 - never let this stop the game
            log.exception("event check failed")
            return []
        ids = [e.id for e in due]
        if not due or ids != self._due:
            self._due = ids or None          # must look the same on two checks (save RAM settled)
            return []
        self._due = None
        out = []
        for ev in due:
            try:
                if self.pm.is_safe() and give(self.pm, ev):
                    out.append(ev)
            except Exception:  # noqa: BLE001
                log.exception("giving %s failed", ev.id)
        return out
