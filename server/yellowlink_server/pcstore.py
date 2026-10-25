"""Server-side operations on players' stored PC databases (see SPEC.md sections 5 and 2: trades).

Entries are the client's format: {id, origin, dex, level, nick, ot, otid, gen1: b64 55-byte Gen-1 box record or null,
gen2: b64 55-byte Gen-2 box record or null}. The server never needs a ROM: trade evolutions only change the species
byte (+ held item / nickname), and the games recalculate stats when the Pokémon is withdrawn.
"""
from __future__ import annotations

import base64
import copy
import uuid

STORE_BOXES = 14
MONS_PER_BOX = 20

# Gen 1 (Yellow internal species index): Kadabra, Machoke, Graveler, Haunter. Their types don't change.
G1_TRADE_EVOS = {0x26: (0x95, 65, "KADABRA", "ALAKAZAM"), 0x29: (0x7E, 68, "MACHOKE", "MACHAMP"),
                 0x27: (0x31, 76, "GRAVELER", "GOLEM"), 0x93: (0x0E, 94, "HAUNTER", "GENGAR")}
# Gen 2 (national dex): -> (evolution, required held item or None, old name, new name)
KINGS_ROCK, EVERSTONE, METAL_COAT, DRAGON_SCALE, UP_GRADE = 0x52, 0x70, 0x8F, 0x97, 0xAC
G2_TRADE_EVOS = {64: (65, None, "KADABRA", "ALAKAZAM"), 67: (68, None, "MACHOKE", "MACHAMP"),
                 75: (76, None, "GRAVELER", "GOLEM"), 93: (94, None, "HAUNTER", "GENGAR"),
                 61: (186, KINGS_ROCK, "POLIWHIRL", "POLITOED"), 79: (199, KINGS_ROCK, "SLOWPOKE", "SLOWKING"),
                 95: (208, METAL_COAT, "ONIX", "STEELIX"), 123: (212, METAL_COAT, "SCYTHER", "SCIZOR"),
                 117: (230, DRAGON_SCALE, "SEADRA", "KINGDRA"), 137: (233, UP_GRADE, "PORYGON", "PORYGON2")}


def empty_store() -> dict:
    return {"version": 1, "rev": 0, "boxes": [[] for _ in range(STORE_BOXES)], "linked": []}


def find(store: dict, entry_id: str):
    """(box index, position, entry) or None."""
    for bi, box in enumerate(store.get("boxes", [])):
        for i, e in enumerate(box):
            if isinstance(e, dict) and e.get("id") == entry_id:
                return bi, i, e
    return None


def has_room(store: dict, n: int = 1) -> bool:
    return sum(MONS_PER_BOX - len(b) for b in store["boxes"]) >= n


def _encode(name: str) -> bytes:
    """Gen 1/2 charset for A-Z and digits (both games share it), 0x50-terminated, 11 bytes."""
    out = bytearray()
    for ch in name[:10]:
        if "A" <= ch <= "Z":
            out.append(0x80 + ord(ch) - 65)
        elif "0" <= ch <= "9":
            out.append(0xF6 + ord(ch) - 48)
        else:
            out.append(0x7F)
    return bytes(out + b"\x50" + b"\x50" * (10 - len(out)))[:11]


def _nick_is(raw: bytes, name: str) -> bool:
    """True if the 11-byte nickname is exactly the species name (i.e. never nicknamed)."""
    end = raw.index(0x50) if 0x50 in raw else len(raw)
    return bytes(raw[:end]) == _encode(name)[:len(name)]


def evolve_for_trade(entry: dict) -> tuple[dict, str | None]:
    """Apply a trade evolution to a traded entry. Returns (entry, new species name or None)."""
    e = copy.deepcopy(entry)
    if e.get("gen1"):
        rec = bytearray(base64.b64decode(e["gen1"]))
        evo = G1_TRADE_EVOS.get(rec[0]) if len(rec) >= 55 else None
        if evo:
            new_sp, dex, old, new = evo
            rec[0] = new_sp
            if _nick_is(bytes(rec[44:55]), old):
                rec[44:55] = _encode(new)
                e["nick"] = new
            e["gen1"] = base64.b64encode(bytes(rec)).decode()
            e["dex"] = dex
            return e, new
        return e, None
    if e.get("gen2"):
        rec = bytearray(base64.b64decode(e["gen2"]))
        if len(rec) < 54 or (len(rec) > 54 and rec[54]):          # eggs don't evolve
            return e, None
        evo = G2_TRADE_EVOS.get(rec[0])
        if not evo or rec[1] == EVERSTONE:
            return e, None
        new_dex, item, old, new = evo
        if item is not None and rec[1] != item:
            return e, None
        rec[0] = new_dex
        if item is not None:
            rec[1] = 0                                               # the item is used up
        if _nick_is(bytes(rec[43:54]), old):
            rec[43:54] = _encode(new)
            e["nick"] = new
        e["gen2"] = base64.b64encode(bytes(rec)).decode()
        e["dex"] = new_dex
        return e, new
    return e, None


SERVER_KEYS = ("srv_rev", "inbox", "srv_trades", "srv_gone")
INBOX_MAX = 100          # undelivered trades per player before new trades are refused


def applied(store: dict, trade_id: str) -> bool:
    return trade_id in store.get("srv_trades", [])


def apply_delta(store: dict, trade_id: str, remove: list, add: list) -> dict:
    """Apply one side of a trade to a stored PC in place (idempotent by trade id). Returns the delta that the
    client must apply too (also kept in store['inbox'] until the client's upload lists the trade id).

    Server-only fields: srv_rev (bumped on every change), srv_trades (applied trade ids), srv_gone (entry ids
    given away; an upload still holding one is refused), inbox (undelivered deltas - never trimmed)."""
    if applied(store, trade_id):
        return next((d for d in store.get("inbox", []) if d.get("trade_id") == trade_id),
                    {"trade_id": trade_id, "remove": list(remove), "add": list(add),
                     "srv_rev": store.get("srv_rev", 0)})
    gone = store.setdefault("srv_gone", [])
    for eid in remove:
        hit = find(store, eid)
        if hit:
            store["boxes"][hit[0]].pop(hit[1])
        gone.append(eid)
    added = []
    for entry in add:
        entry = copy.deepcopy(entry)
        if find(store, entry["id"]):
            entry["id"] = uuid.uuid4().hex                          # never two entries with one id
        if entry["id"] in gone:
            gone.remove(entry["id"])                                # it came back in a later trade
        box = next((b for b in store["boxes"] if len(b) < MONS_PER_BOX), None)
        if box is None:
            store.setdefault("incoming", []).append(entry)
        else:
            box.append(entry)
        added.append(entry)
    del gone[:-2000]
    store["srv_rev"] = int(store.get("srv_rev", 0)) + 1
    trades = store.setdefault("srv_trades", [])
    trades.append(trade_id)
    del trades[:-2000]
    delta = {"trade_id": trade_id, "remove": list(remove), "add": added, "srv_rev": store["srv_rev"]}
    store.setdefault("inbox", []).append(delta)
    return delta


def valid_entry(entry) -> bool:
    """Offered entries must carry readable game data (a bad one would break the trade halfway)."""
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
        return False
    ok = False
    for k in ("gen1", "gen2"):
        v = entry.get(k)
        if v is None:
            continue
        try:
            raw = base64.b64decode(v, validate=True)
        except (ValueError, TypeError):
            return False
        if len(raw) not in (54, 55):
            return False
        ok = True
    return ok
