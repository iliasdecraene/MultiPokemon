"""Server-side operations on players' stored PC databases (see SPEC.md sections 5 and 2: trades).

Entries are the client's format: {id, origin, dex, level, nick, ot, otid, gen1: b64 55-byte Gen-1 box record or null,
gen2: b64 55-byte Gen-2 box record or null, gen4: b64 136-byte encrypted Gen-4 (Platinum) box mon or null}.
The server never needs a ROM: trade evolutions only change the species (+ held item / nickname / Gen-4 ability),
and the games recalculate stats when the Pokémon is withdrawn.

Which representation is current: an entry is (re)created from the bytes of the game it was last harvested from, and
`origin` names that game (yellow -> gen1, crystal -> gen2, platinum -> gen4). A trade evolution is applied to that
representation only; if it evolves, the other representations are stale and are dropped (set to null) so every game
converts from the evolved one. Entries without a matching representation fall back to gen1, gen2, gen4 in that order.
"""
from __future__ import annotations

import base64
import copy
import struct
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


# ------------------------------------------------------------------------------------------------------------------
# Gen 4 (Pokémon Platinum): PK4 encryption (pret/pokeplatinum src/pokemon.c; the client's pk4.py has the full codec)
# ------------------------------------------------------------------------------------------------------------------
PK4_BOX = 136
_BLOCK_POS = [(0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2), (0, 2, 3, 1), (0, 3, 2, 1),
              (1, 0, 2, 3), (1, 0, 3, 2), (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
              (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2), (2, 3, 0, 1), (3, 2, 0, 1),
              (1, 2, 3, 0), (1, 3, 2, 0), (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0)]


def _pk4_crypt(data: bytes, seed: int) -> bytes:
    n = len(data) // 2
    out = []
    s = seed
    for w in struct.unpack("<%dH" % n, data):
        s = (s * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        out.append(w ^ (s >> 16))
    return struct.pack("<%dH" % n, *out)


def pk4_decrypt(data: bytes) -> bytearray | None:
    """136 encrypted bytes -> plain (blocks A,B,C,D in order), or None if the checksum doesn't match."""
    if len(data) != PK4_BOX:
        return None
    pid, _, chk = struct.unpack_from("<IHH", data, 0)
    blocks = _pk4_crypt(bytes(data[8:]), chk)
    pos = _BLOCK_POS[((pid >> 13) & 31) % 24]
    plain = bytearray(data[:8]) + b"".join(blocks[p * 32:(p + 1) * 32] for p in pos)
    if sum(struct.unpack_from("<64H", plain, 8)) & 0xFFFF != chk:
        return None
    return plain


def pk4_encrypt(plain: bytes) -> bytes:
    plain = bytearray(plain)
    pid = struct.unpack_from("<I", plain, 0)[0]
    chk = sum(struct.unpack_from("<64H", plain, 8)) & 0xFFFF
    struct.pack_into("<H", plain, 6, chk)
    pos = _BLOCK_POS[((pid >> 13) & 31) % 24]
    stored = [b""] * 4
    for k in range(4):
        stored[pos[k]] = bytes(plain[8 + 32 * k:8 + 32 * (k + 1)])
    return bytes(plain[:8]) + _pk4_crypt(b"".join(stored), chk)


# Platinum item ids (pret generated/items.txt) and trade evolutions (checked against the ROM's evo.narc by
# client/tests/test_ndsrom.py): species -> [(evolution, required held item or None, new name, (ability1, ability2))]
G4_KINGS_ROCK, G4_DEEP_SEA_TOOTH, G4_DEEP_SEA_SCALE, G4_EVERSTONE = 221, 226, 227, 229
G4_METAL_COAT, G4_DRAGON_SCALE, G4_UP_GRADE = 233, 235, 252
G4_PROTECTOR, G4_ELECTIRIZER, G4_MAGMARIZER, G4_DUBIOUS_DISC, G4_REAPER_CLOTH = 321, 322, 323, 324, 325
G4_TRADE_EVOS = {
    64: [(65, None, "ALAKAZAM", (28, 39))], 67: [(68, None, "MACHAMP", (62, 99))],
    75: [(76, None, "GOLEM", (69, 5))], 93: [(94, None, "GENGAR", (26, 0))],
    61: [(186, G4_KINGS_ROCK, "POLITOED", (11, 6))], 79: [(199, G4_KINGS_ROCK, "SLOWKING", (12, 20))],
    95: [(208, G4_METAL_COAT, "STEELIX", (69, 5))], 123: [(212, G4_METAL_COAT, "SCIZOR", (68, 101))],
    117: [(230, G4_DRAGON_SCALE, "KINGDRA", (33, 97))], 137: [(233, G4_UP_GRADE, "PORYGON2", (36, 88))],
    233: [(474, G4_DUBIOUS_DISC, "PORYGON-Z", (91, 88))], 112: [(464, G4_PROTECTOR, "RHYPERIOR", (31, 116))],
    125: [(466, G4_ELECTIRIZER, "ELECTIVIRE", (78, 0))], 126: [(467, G4_MAGMARIZER, "MAGMORTAR", (49, 0))],
    356: [(477, G4_REAPER_CLOTH, "DUSKNOIR", (46, 0))],
    366: [(367, G4_DEEP_SEA_TOOTH, "HUNTAIL", (33, 0)), (368, G4_DEEP_SEA_SCALE, "GOREBYSS", (33, 0))]}
_G4_EOS = 0xFFFF


def _g4_name(name: str) -> list:
    """A-Z, 0-9, '-' -> Gen-4 character codes + terminator (pret tools/msgenc/charmap.txt)."""
    out = []
    for ch in name[:10]:
        if "A" <= ch <= "Z":
            out.append(0x012B + ord(ch) - 65)
        elif "0" <= ch <= "9":
            out.append(0x0121 + ord(ch) - 48)
        elif ch == "-":
            out.append(0x01BE)
        else:
            out.append(0x01DE)                                       # space
    return out + [_G4_EOS]


def _evolve_gen4(raw: bytes):
    """(new 136 bytes, new dex, new name) or None. Same rules as Platinum: eggs and Everstone don't evolve,
    the item is used up, the ability is recalculated from PID bit 0, an un-nicknamed mon takes the new name."""
    p = pk4_decrypt(raw)
    if p is None:
        return None
    species, item = struct.unpack_from("<HH", p, 0x08)
    ivword = struct.unpack_from("<I", p, 0x38)[0]
    if ivword >> 30 & 1 or item == G4_EVERSTONE:
        return None
    for new, need, name, abilities in G4_TRADE_EVOS.get(species, []):
        if need is not None and item != need:
            continue
        struct.pack_into("<H", p, 0x08, new)
        if need is not None:
            struct.pack_into("<H", p, 0x0A, 0)                       # the item is used up
        pid = struct.unpack_from("<I", p, 0)[0]
        p[0x15] = abilities[1] if (abilities[1] and pid & 1) else abilities[0]
        if not ivword >> 31 & 1:                                     # not nicknamed: name follows the species
            codes = _g4_name(name)
            codes += [0] * (11 - len(codes))
            struct.pack_into("<11H", p, 0x48, *codes)
        return pk4_encrypt(p), new, name
    return None


_REP = {"yellow": "gen1", "crystal": "gen2", "platinum": "gen4"}


def current_rep(entry: dict) -> str | None:
    """The representation that holds the entry's current data (see module doc)."""
    k = _REP.get(entry.get("origin"))
    if k and entry.get(k):
        return k
    return next((k for k in ("gen1", "gen2", "gen4") if entry.get(k)), None)


def evolve_for_trade(entry: dict) -> tuple[dict, str | None]:
    """Apply a trade evolution to a traded entry. Returns (entry, new species name or None)."""
    e = copy.deepcopy(entry)
    rep = current_rep(e)
    e2, name = _evolve_rep(e, rep)
    if name is not None:
        for k in ("gen1", "gen2", "gen4"):
            if k != rep and e2.get(k):
                e2[k] = None                                         # stale: the games convert from the new one
    return e2, name


def _evolve_rep(e: dict, rep: str | None) -> tuple[dict, str | None]:
    if rep == "gen4":
        out = _evolve_gen4(base64.b64decode(e["gen4"]))
        if out is None:
            return e, None
        raw, dex, name = out
        e["gen4"] = base64.b64encode(raw).decode()
        e["dex"] = dex
        plain = pk4_decrypt(raw)
        if plain is not None and not struct.unpack_from("<I", plain, 0x38)[0] >> 31 & 1:
            e["nick"] = name
        return e, name
    if rep == "gen1":
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
    if rep == "gen2":
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
    for k in ("gen1", "gen2", "gen4"):
        v = entry.get(k)
        if v is None:
            continue
        try:
            raw = base64.b64decode(v, validate=True)
        except (ValueError, TypeError):
            return False
        if k == "gen4":
            plain = pk4_decrypt(raw)                                 # 136 bytes with a valid checksum
            if plain is None or not 1 <= struct.unpack_from("<H", plain, 8)[0] <= 493:   # an empty slot isn't a mon
                return False
        elif len(raw) not in (54, 55):
            return False
        ok = True
    return ok
