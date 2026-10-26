"""Where can the player stand? Tile attributes of any Platinum map, read from the player's ROM (dev/test use only).

pret: src/map_matrix.c (map_matrix.narc member: width u8, height u8, hasMapHeaderIDs u8, hasAltitudes u8,
nameLen u8, name, [mapHeaderIDs u16 w*h], [altitudes u8 w*h], landDataIDs u16 w*h) and src/overlay005/land_data.c
(land_data.narc member: 4 x int section sizes, then terrain attributes 32x32 u16 at +0x10: bit 15 collision,
bits 0-7 tile behaviour, include/constants/field/map_tile_behaviors.h). Tile (x, z) of a map = matrix chunk
(x // 32, z // 32), attribute index (z % 32) * 32 + x % 32 - outdoors these are the global Sinnoh coordinates the
game keeps in Location / MapObject (matrix 0), indoors the map's own matrix.
"""
import struct

from yellowlink import ndsrom

TALL_GRASS, VERY_TALL_GRASS, CAVE_FLOOR, OLD_CHATEAU_FLOOR, MOUNTAIN_FLOOR = 0x02, 0x03, 0x08, 0x0B, 0x0C
WATER_RIVER, WATER_SEA, PUDDLE, SHALLOW_WATER = 0x10, 0x15, 0x16, 0x17
SNOW = (0xA0, 0xA1, 0xA2)        # snow tiles (Route 216/217 grass is 0x02/0x03 too)
WALKABLE_ENCOUNTER = {TALL_GRASS, VERY_TALL_GRASS, CAVE_FLOOR, OLD_CHATEAU_FLOOR, MOUNTAIN_FLOOR}
WATER = {WATER_RIVER, WATER_SEA}


class World:
    def __init__(self, rom_path: str):
        self.rom = ndsrom.NDSRom(rom_path)
        self.matrices = self.rom.narc("/fielddata/mapmatrix/map_matrix.narc")
        self.land = self.rom.narc("/fielddata/land_data/land_data.narc")
        self._mx = {}

    def matrix(self, mid: int):
        if mid not in self._mx:
            b = self.matrices[mid]
            w, h, has_ids, has_alt, nlen = b[0], b[1], b[2], b[3], b[4]
            o = 5 + nlen
            ids = None
            if has_ids:
                ids = struct.unpack_from("<%dH" % (w * h), b, o)
                o += 2 * w * h
            if has_alt:
                o += w * h
            land = struct.unpack_from("<%dH" % (w * h), b, o)
            self._mx[mid] = (w, h, ids, land)
        return self._mx[mid]

    def tiles(self, map_id: int, matrix_id: int) -> dict:
        """{(x, z): attribute u16} of every tile of `map_id` in matrix `matrix_id`."""
        w, h, ids, land = self.matrix(matrix_id)
        out = {}
        for cz in range(h):
            for cx in range(w):
                i = cz * w + cx
                if (ids is not None and ids[i] != map_id) or land[i] == 0xFFFF:
                    continue
                attrs = struct.unpack_from("<1024H", self.land[land[i]], 0x10)
                for k, a in enumerate(attrs):
                    out[(cx * 32 + k % 32, cz * 32 + k // 32)] = a
        return out


def behaviour(a: int) -> int:
    return a & 0xFF


def blocked(a: int) -> bool:
    return bool(a & 0x8000)


def encounter_pairs(tiles: dict, kinds=WALKABLE_ENCOUNTER):
    """[(x, z, dx)] walkable encounter tiles with a walkable encounter neighbour at x+dx (dx = +1 or -1), sorted so
    the result is deterministic."""
    out = []
    for (x, z), a in sorted(tiles.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if blocked(a) or behaviour(a) not in kinds:
            continue
        for dx in (1, -1):
            b = tiles.get((x + dx, z))
            if b is not None and not blocked(b) and behaviour(b) in kinds:
                out.append((x, z, dx))
                break
    return out


def water_facing(tiles: dict):
    """[(x, z, dir)] walkable land tiles directly next to sea/river water (dir = DIR_* the player must face)."""
    out = []
    for (x, z), a in sorted(tiles.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if blocked(a) or behaviour(a) in WATER:
            continue
        for d, (dx, dz) in ((0, (0, -1)), (1, (0, 1)), (2, (-1, 0)), (3, (1, 0))):
            b = tiles.get((x + dx, z + dz))
            if b is not None and behaviour(b) in WATER:
                out.append((x, z, d))
                break
    return out
