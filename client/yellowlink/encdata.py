"""Pokémon Platinum wild-encounter tables (`/fielddata/encountdata/pl_enc_data.narc`): layout + parser. Pure Python.

The NARC has 183 members of exactly 424 bytes; member i is the table of every map whose header has
wildEncountersArchiveID i (pret res/field/encounters/encounters.order line i, `MapHeaderData_LoadWildEncounters`
copies the member whole into FieldSystem->mapHeaderData->wildEncounters when a map loads). Layout = pret
tools/jsoncnv/encounter.py / include/overlay006/wild_encounters.h, checked against the real ROM (all 183 members
equal pret's JSONs). SPEC.md "Complete Sinnoh" has the table.

    off  size  field
    0    4     land_rate u32
    4    96    land slots 0..11: (level u32, species u32)                      weights 20 20 10 10 10 10 5 5 4 4 1 1 %
    100  8     swarm species x2   -> land slots 0,1 while today's swarm is on this map
    108  8     day species x2     -> land slots 2,3 at day / twilight
    116  8     night species x2   -> land slots 2,3 at night / late night (morning: the land slots)
    124  16    radar species x4   -> land slots 4,5,10,11 in a shaking Poké Radar patch
    140  24    rate_form0..4, unown_table (u32)
    164  40    GBA dual-slot ruby, sapphire, emerald, firered, leafgreen x2 -> land slots 8,9 (National Dex + cartridge)
    204  4+40  surf: rate, 5 x (max u8, min u8, pad u16, species u32)          weights 60 30 5 4 1
    248  44    unused water table (all zero)
    292  4+40  old rod                                                          60 30 5 4 1
    336  4+40  good rod                                                         40 40 15 4 1
    380  4+40  super rod                                                        40 40 15 4 1
"""
from __future__ import annotations

import struct

NARC_PATH = "/fielddata/encountdata/pl_enc_data.narc"
TABLE_COUNT = 183
TABLE_SIZE = 424

LAND, SPECIES, WATER = "land", "species", "water"
# kind: (offset of slot 0, slot count, entry size, entry type)
SLOT_KINDS = {
    "land": (4, 12, 8, LAND),
    "swarm": (100, 2, 4, SPECIES), "day": (108, 2, 4, SPECIES), "night": (116, 2, 4, SPECIES),
    "radar": (124, 4, 4, SPECIES),
    "ruby": (164, 2, 4, SPECIES), "sapphire": (172, 2, 4, SPECIES), "emerald": (180, 2, 4, SPECIES),
    "firered": (188, 2, 4, SPECIES), "leafgreen": (196, 2, 4, SPECIES),
    "surf": (208, 5, 8, WATER), "old_rod": (296, 5, 8, WATER), "good_rod": (340, 5, 8, WATER),
    "super_rod": (384, 5, 8, WATER),
}
RATE_OFFSETS = {"land": 0, "surf": 204, "old_rod": 292, "good_rod": 336, "super_rod": 380}
LAND_WEIGHTS = (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)
WATER_WEIGHTS = {"surf": (60, 30, 5, 4, 1), "old_rod": (60, 30, 5, 4, 1),
                 "good_rod": (40, 40, 15, 4, 1), "super_rod": (40, 40, 15, 4, 1)}


def slot_offset(kind: str, slot: int) -> int:
    base, count, size, _ = SLOT_KINDS[kind]
    if not 0 <= slot < count:
        raise ValueError(f"{kind} has slots 0..{count - 1}, not {slot}")
    return base + size * slot


def parse(data: bytes) -> dict:
    """{'rates': {kind: rate}, kind: [entry, ...]}: land entries (species, level), water (species, min, max),
    species-only kinds species ints."""
    if len(data) != TABLE_SIZE:
        raise ValueError(f"an encounter table is {TABLE_SIZE} bytes, not {len(data)}")
    out = {"rates": {k: struct.unpack_from("<I", data, o)[0] for k, o in RATE_OFFSETS.items()}}
    for kind, (base, count, size, typ) in SLOT_KINDS.items():
        rows = []
        for i in range(count):
            o = base + size * i
            if typ == LAND:
                level, species = struct.unpack_from("<II", data, o)
                rows.append((species, level))
            elif typ == WATER:
                mx, mn, _pad, species = struct.unpack_from("<BBHI", data, o)
                rows.append((species, mn, mx))
            else:
                rows.append(struct.unpack_from("<I", data, o)[0])
        out[kind] = rows
    return out


def land_species(table: dict) -> set:
    """Every species a grass/cave walk on this table can give in single player (land slots plus the day/night,
    swarm and radar replacements; not the GBA dual-slot ones)."""
    s = {sp for sp, _ in table["land"]} if table["rates"]["land"] else set()
    if table["rates"]["land"]:
        for k in ("day", "night", "swarm", "radar"):
            s.update(table[k])
    s.discard(0)
    return s


# --- NARC (only what the builder/tests need; the ROM reader is ndsrom.Narc) ----------------------------------
def narc_member_offsets(narc: bytes) -> list:
    """[(start, end)] of every member, relative to the start of the NARC file."""
    if narc[:4] != b"NARC":
        raise ValueError("not a NARC")
    hdr = struct.unpack_from("<H", narc, 12)[0]
    if narc[hdr:hdr + 4] != b"BTAF":
        raise ValueError("NARC without BTAF")
    btaf_size, count = struct.unpack_from("<IH", narc, hdr + 4)
    fat = struct.unpack_from("<%dI" % (2 * count), narc, hdr + 12)
    o = hdr + btaf_size
    if narc[o:o + 4] != b"BTNF":
        raise ValueError("NARC without BTNF")
    o += struct.unpack_from("<I", narc, o + 4)[0]
    if narc[o:o + 4] != b"GMIF":
        raise ValueError("NARC without GMIF")
    data = o + 8
    out = []
    for i in range(count):
        s, e = fat[2 * i], fat[2 * i + 1]
        if e < s or data + e > len(narc):
            raise ValueError("bad NARC member %d" % i)
        out.append((data + s, data + e))
    return out


def make_narc(members: list) -> bytes:
    """A NARC holding `members` (tests; same structure as the game's: header 16, BTAF, empty BTNF, GMIF)."""
    fat = b""
    pos = 0
    for m in members:
        fat += struct.pack("<II", pos, pos + len(m))
        pos += len(m)
    btaf = b"BTAF" + struct.pack("<IHH", 12 + len(fat), len(members), 0) + fat
    btnf = b"BTNF" + struct.pack("<I", 16) + struct.pack("<IHH", 4, 0, 1)
    gmif = b"GMIF" + struct.pack("<I", 8 + pos) + b"".join(members)
    total = 16 + len(btaf) + len(btnf) + len(gmif)
    return b"NARC" + struct.pack("<HHIHH", 0xFFFE, 0x0100, total, 16, 3) + btaf + btnf + gmif


def apply_edits(member_index: int, data: bytes, edits=(), rate_edits=(), name_of=None) -> bytes:
    """Reference implementation of the spec's apply(): patch one table with the SlotEdit / RateEdit rows for it
    (fields as in sinnoh_complete.py: table, name, kind, slot, species, level, min_level, max_level, old_species,
    old_level, old_min, old_max / table, name, kind, rate, old_rate). Refuses (ValueError) when a vanilla value
    isn't what the row expects, or two rows edit the same slot. Water entries keep their 2 pad bytes."""
    if len(data) != TABLE_SIZE:
        raise ValueError(f"table {member_index}: {len(data)} bytes, expected {TABLE_SIZE}")
    out = bytearray(data)
    seen = set()
    for e in edits:
        if e.table != member_index:
            continue
        where = f"{e.name} (table {e.table}) {e.kind} slot {e.slot}"
        if (e.kind, e.slot) in seen:
            raise ValueError(f"{where}: edited twice")
        seen.add((e.kind, e.slot))
        o = slot_offset(e.kind, e.slot)
        typ = SLOT_KINDS[e.kind][3]
        if typ == LAND:
            level, species = struct.unpack_from("<II", out, o)
            if (species, level) != (e.old_species, e.old_level):
                raise ValueError(f"{where}: found species {species} Lv{level}, expected {e.old_species} Lv{e.old_level}")
            if not (1 <= e.level <= 100 and 1 <= e.species <= 493):
                raise ValueError(f"{where}: bad new species/level")
            struct.pack_into("<II", out, o, e.level, e.species)
        elif typ == WATER:
            mx, mn, _pad, species = struct.unpack_from("<BBHI", out, o)
            if (species, mn, mx) != (e.old_species, e.old_min, e.old_max):
                raise ValueError(f"{where}: found species {species} Lv{mn}-{mx}, expected {e.old_species} "
                                 f"Lv{e.old_min}-{e.old_max}")
            if not (1 <= e.min_level <= e.max_level <= 100 and 1 <= e.species <= 493):
                raise ValueError(f"{where}: bad new species/levels")
            struct.pack_into("<BB", out, o, e.max_level, e.min_level)
            struct.pack_into("<I", out, o + 4, e.species)
        else:
            species = struct.unpack_from("<I", out, o)[0]
            if species != e.old_species:
                raise ValueError(f"{where}: found species {species}, expected {e.old_species}")
            if not 1 <= e.species <= 493:
                raise ValueError(f"{where}: bad new species")
            struct.pack_into("<I", out, o, e.species)
    for r in rate_edits:
        if r.table != member_index:
            continue
        o = RATE_OFFSETS[r.kind]
        old = struct.unpack_from("<I", out, o)[0]
        if old != r.old_rate:
            raise ValueError(f"{r.name} (table {r.table}) {r.kind} rate: found {old}, expected {r.old_rate}")
        if (r.kind, "rate") in seen:
            raise ValueError(f"{r.name} (table {r.table}) {r.kind} rate: edited twice")
        seen.add((r.kind, "rate"))
        if not 0 <= r.rate <= 100:
            raise ValueError(f"{r.name}: bad rate {r.rate}")
        struct.pack_into("<I", out, o, r.rate)
    return bytes(out)
