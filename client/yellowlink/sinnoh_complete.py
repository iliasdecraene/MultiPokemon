"""Complete Sinnoh: our own additions to Pokemon Platinum's wild encounter tables (machine-readable spec).

Ilias's rule: normal Platinum stays normal Platinum, but every Pokemon family (except the mythicals that get a
YellowLink raid later) can be found in Sinnoh. The design and the reasons are in dev/sinnoh/PLAN.md; the proof
that it works is dev/sinnoh/check_plan.py. This module holds only our own data (table numbers, slot numbers,
national dex numbers, levels) - no bytes from the ROM. The engine (sinnoh_rom.py) applies EDITS to the
player's own vanilla ROM: /fielddata/encountdata/pl_enc_data.narc (183 members x 424 bytes, edited in place, no
size change) and, so the Pokedex "Area" screen shows the new spots, AREA_EDITS to
/application/zukanlist/zkn_data/zukan_enc_platinum.narc (variable-size lists, see below).

Table layout (verified against the real US ROM rev 0 and pret/pokeplatinum: all 183 tables identical):
    off  size  field
    0    4     land_rate (u32)
    4    96    land slots 0..11: (level u32, species u32)       weights 20,20,10,10,10,10,5,5,4,4,1,1 %
    100  8     swarm   2 x species u32 -> land slots 0,1 while today's swarm is here (post-game)
    108  8     day     2 x species     -> land slots 2,3 at day/twilight (morning = the base slots)
    116  8     night   2 x species     -> land slots 2,3 at night/late night
    124  16    radar   4 x species     -> land slots 4,5,10,11 in a shaking Poke Radar patch (post-game)
    140  24    form rates x5 + unown table (never touched)
    164  40    GBA dual-slot ruby, sapphire, emerald, firered, leafgreen: 2 x species each -> land slots 8,9
    204  4+40  surf: rate, 5 x (max u8, min u8, pad u16, species u32)   weights 60,30,5,4,1 %
    248  44    unused water table (all zero)
    292  4+40  old rod  (weights 60,30,5,4,1 %)
    336  4+40  good rod (weights 40,40,15,4,1 %)
    380  4+40  super rod(weights 40,40,15,4,1 %)
Great Marsh tables: land slots 6,7 are always replaced by the daily marsh Pokemon (never edit them), and its
surf lists are never used (no field moves in the Safari game; fishing works).
Trophy Garden: land slots 6,7 are replaced by Mr. Backlot's daily Pokemon after the National Dex.
"""
import hashlib
import struct
from typing import NamedTuple

SPEC_VERSION = 2
NARC_PATH = '/fielddata/encountdata/pl_enc_data.narc'
TABLE_COUNT = 183
TABLE_SIZE = 424

# kind: (base offset, slot count, entry size, entry type)
SLOT_KINDS = {
    'land': (4, 12, 8, 'land'),
    'swarm': (100, 2, 4, 'species'), 'day': (108, 2, 4, 'species'), 'night': (116, 2, 4, 'species'),
    'radar': (124, 4, 4, 'species'),
    'ruby': (164, 2, 4, 'species'), 'sapphire': (172, 2, 4, 'species'), 'emerald': (180, 2, 4, 'species'),
    'firered': (188, 2, 4, 'species'), 'leafgreen': (196, 2, 4, 'species'),
    'surf': (208, 5, 8, 'water'), 'old_rod': (296, 5, 8, 'water'), 'good_rod': (340, 5, 8, 'water'),
    'super_rod': (384, 5, 8, 'water'),
}
FORMS_OFFSET = 140              # 5 form rates + Unown table id (u32 each) - never touched
RATE_OFFSETS = {'land': 0, 'surf': 204, 'old_rod': 292, 'good_rod': 336, 'super_rod': 380}
LAND_WEIGHTS = (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)
WATER_WEIGHTS = {'surf': (60, 30, 5, 4, 1), 'old_rod': (60, 30, 5, 4, 1),
                 'good_rod': (40, 40, 15, 4, 1), 'super_rod': (40, 40, 15, 4, 1)}
MAX_SPECIES = 493

# NARC member index -> pret table name (pret res/field/encounters/encounters.order)
TABLE_NAMES = (
    'canalave_city', 'eterna_city', 'pastoria_city', 'sunyshore_city', 'pokemon_league', 'oreburgh_mine_b1f',
    'oreburgh_mine_b2f', 'valley_windworks_outside', 'eterna_forest', 'fuego_ironworks_outside',
    'mt_coronet_1f_south', 'mt_coronet_2f', 'mt_coronet_3f', 'mt_coronet_outside_north', 'mt_coronet_outside_south',
    'mt_coronet_4f_rooms_1_and_2', 'mt_coronet_4f_room_3', 'mt_coronet_5f', 'mt_coronet_6f',
    'mt_coronet_1f_tunnel_room', 'mt_coronet_1f_north_room_2', 'mt_coronet_1f_north_room_1', 'mt_coronet_b1f',
    'great_marsh_1', 'great_marsh_2', 'great_marsh_3', 'great_marsh_4', 'great_marsh_5', 'great_marsh_6',
    'solaceon_ruins_maniac_tunnel_room', 'solaceon_ruins_room_2_northeast_dead_end',
    'solaceon_ruins_room_1_northwest_dead_end', 'solaceon_ruins_room_2', 'solaceon_ruins_room_1_southeast_dead_end',
    'solaceon_ruins_room_3', 'solaceon_ruins_room_2_southeast_dead_end', 'solaceon_ruins_room_6_southeast_dead_end',
    'solaceon_ruins_room_5_southwest_dead_end', 'solaceon_ruins_room_3_northwest_dead_end',
    'solaceon_ruins_room_3_southwest_dead_end', 'solaceon_ruins_room_4', 'solaceon_ruins_room_6',
    'solaceon_ruins_room_5', 'solaceon_ruins_room_7', 'solaceon_ruins_room_4_southeast_dead_end',
    'solaceon_ruins_room_6_northwest_dead_end', 'solaceon_ruins_room_5_southeast_deadend', 'victory_road_1f',
    'victory_road_2f', 'victory_road_b1f', 'victory_road_1f_room_2', 'victory_road_1f_room_1',
    'victory_road_1f_room_3', 'ravaged_path', 'oreburgh_gate_1f', 'oreburgh_gate_b1f', 'stark_mountain_outside',
    'stark_mountain_room_1', 'stark_mountain_room_2', 'sendoff_spring', 'turnback_cave_entrance',
    'turnback_cave_pillar_room', 'turnback_cave_giratina_room', 'turnback_cave_pillar_1_room_1',
    'turnback_cave_pillar_1_room_2', 'turnback_cave_pillar_1_room_3', 'turnback_cave_pillar_1_room_4',
    'turnback_cave_pillar_1_room_5', 'turnback_cave_pillar_1_room_6', 'turnback_cave_pillar_2_room_1',
    'turnback_cave_pillar_2_room_2', 'turnback_cave_pillar_2_room_3', 'turnback_cave_pillar_2_room_4',
    'turnback_cave_pillar_2_room_5', 'turnback_cave_pillar_2_room_6', 'turnback_cave_pillar_3_room_1',
    'turnback_cave_pillar_3_room_2', 'turnback_cave_pillar_3_room_3', 'turnback_cave_pillar_3_room_4',
    'turnback_cave_pillar_3_room_5', 'turnback_cave_pillar_3_room_6', 'unknown_533', 'unknown_534', 'unknown_535',
    'unknown_536', 'unknown_537', 'unknown_538', 'unknown_539', 'unknown_540', 'unknown_541', 'unknown_542',
    'unknown_543', 'unknown_544', 'unknown_545', 'unknown_546', 'unknown_547', 'unknown_548', 'unknown_549',
    'unknown_550', 'unknown_551', 'unknown_552', 'unknown_553', 'unknown_554', 'unknown_555', 'unknown_556',
    'unknown_557', 'snowpoint_temple_1f', 'snowpoint_temple_b1f', 'snowpoint_temple_b2f', 'snowpoint_temple_b3f',
    'snowpoint_temple_b4f', 'snowpoint_temple_b5f', 'wayward_cave_1f', 'wayward_cave_b1f', 'ruin_maniac_cave_short',
    'ruin_maniac_cave_long', 'maniac_tunnel', 'trophy_garden', 'iron_island', 'iron_island_1f',
    'iron_island_b1f_left_room', 'iron_island_b1f_right_room', 'iron_island_b2f_right_room',
    'iron_island_b2f_left_room', 'iron_island_b3f', 'old_chateau', 'old_chateau_dining_area',
    'old_chateau_side_rooms', 'old_chateau_corridor', 'old_chateau_back_west_room',
    'old_chateau_back_middle_west_room', 'old_chateau_back_middle_room', 'old_chateau_back_middle_east_room',
    'old_chateau_back_east_room', 'lake_verity_low_water', 'lake_verity', 'lake_valor', 'lake_acuity',
    'valor_lakefront', 'acuity_lakefront', 'route_201', 'route_202', 'route_203', 'route_204_south',
    'route_204_north', 'route_205_south', 'route_205_north', 'route_206', 'route_207', 'route_208', 'route_209',
    'route_209_lost_tower_1f', 'route_209_lost_tower_2f', 'route_209_lost_tower_3f', 'route_209_lost_tower_4f',
    'route_209_lost_tower_5f', 'route_210_south', 'route_210_north', 'route_211_west', 'route_211_east',
    'route_212_north', 'route_212_south', 'route_213', 'route_214', 'route_215', 'route_216', 'route_217',
    'route_218', 'route_219', 'route_221', 'route_222', 'route_224', 'route_225', 'route_227', 'route_228',
    'route_229', 'twinleaf_town', 'celestic_town', 'resort_area', 'route_220', 'route_223', 'route_226',
    'route_230',
)
TABLE_INDEX = {n: i for i, n in enumerate(TABLE_NAMES)}


class SlotEdit(NamedTuple):
    table: int                  # NARC member index 0..182
    name: str                   # pret table name (for people and logs)
    kind: str                   # key of SLOT_KINDS
    slot: int
    species: int                # national dex number
    level: int | None           # land only
    min_level: int | None       # water only
    max_level: int | None       # water only
    old_species: int            # vanilla value the engine must find (else it refuses to patch)
    old_level: int | None
    old_min: int | None
    old_max: int | None


class RateEdit(NamedTuple):
    table: int
    name: str
    kind: str                   # key of RATE_OFFSETS
    rate: int
    old_rate: int


# ------------------------------------------------------------------------------------------- the design
# Species used by the design (national dex numbers; names only for people, logs and tests).
SPECIES = {
    'BULBASAUR': 1, 'CHARMANDER': 4, 'SQUIRTLE': 7, 'CATERPIE': 10, 'WEEDLE': 13, 'EKANS': 23, 'SANDSHREW': 27,
    'VULPIX': 37, 'GOLBAT': 42, 'DUGTRIO': 51, 'GOLDUCK': 55, 'GROWLITHE': 58, 'MACHOP': 66, 'MACHOKE': 67,
    'GEODUDE': 74, 'GRAVELER': 75, 'PONYTA': 77, 'MAGNEMITE': 81, 'RHYHORN': 111, 'GYARADOS': 130, 'CHIKORITA': 152,
    'CYNDAQUIL': 155, 'TOTODILE': 158, 'HOOTHOOT': 163, 'MARILL': 183, 'MURKROW': 198, 'MISDREAVUS': 200,
    'PINECO': 204, 'STEELIX': 208, 'SHUCKLE': 213, 'TEDDIURSA': 216, 'HOUNDOUR': 228, 'TREECKO': 252,
    'TORCHIC': 255, 'MUDKIP': 258, 'DUSTOX': 269, 'LOTAD': 270, 'SEEDOT': 273, 'RALTS': 280, 'KIRLIA': 281,
    'SABLEYE': 302, 'MAWILE': 303, 'ROSELIA': 315, 'TRAPINCH': 328, 'ZANGOOSE': 335, 'SEVIPER': 336,
    'LUNATONE': 337, 'SOLROCK': 338, 'CLAMPERL': 366, 'TURTWIG': 387, 'CHIMCHAR': 390, 'PIPLUP': 393, 'BIDOOF': 399,
    'BIBAREL': 400, 'KRICKETOT': 401, 'BUDEW': 406, 'CRANIDOS': 408, 'SHIELDON': 410, 'FLOATZEL': 419,
    'SHELLOS': 422, 'BUNEARY': 427, 'GLAMEOW': 431, 'STUNKY': 434, 'BRONZOR': 436, 'LUMINEON': 457,
}

# (table name, kind, slot, new species, new level | (min, max), old species, old level | (min, max))
# Grouped by area; dev/sinnoh/PLAN.md has one row per added family with the reasons.
_DESIGN = (
    # Eterna Forest
    ('eterna_forest', 'land', 4, 'CATERPIE', 11, 'BUDEW', 11),
    ('eterna_forest', 'land', 5, 'WEEDLE', 12, 'BIDOOF', 12),
    ('eterna_forest', 'land', 8, 'SEEDOT', 13, 'BUNEARY', 13),
    ('eterna_forest', 'night', 0, 'MURKROW', None, 'KRICKETOT', None),
    ('eterna_forest', 'night', 1, 'MISDREAVUS', None, 'HOOTHOOT', None),
    # Route 205 north
    ('route_205_north', 'land', 6, 'LOTAD', 13, 'BIDOOF', 13),
    ('route_205_north', 'land', 7, 'LOTAD', 14, 'BIDOOF', 14),
    # Route 206
    ('route_206', 'land', 7, 'STUNKY', 18, 'GEODUDE', 18),
    ('route_206', 'land', 9, 'STUNKY', 18, 'GEODUDE', 18),
    ('route_206', 'land', 11, 'STUNKY', 18, 'GEODUDE', 18),
    # Route 207
    ('route_207', 'land', 6, 'GROWLITHE', 8, 'MACHOP', 8),
    # Route 208
    ('route_208', 'land', 7, 'ZANGOOSE', 19, 'BIBAREL', 19),
    ('route_208', 'land', 9, 'SEVIPER', 20, 'BIBAREL', 20),
    ('route_208', 'land', 11, 'SEVIPER', 20, 'BIBAREL', 20),
    # Route 209
    ('route_209', 'land', 8, 'VULPIX', 19, 'RALTS', 19),
    ('route_209', 'land', 10, 'VULPIX', 19, 'RALTS', 19),
    # Route 210 south
    ('route_210_south', 'land', 7, 'SEEDOT', 21, 'ROSELIA', 21),
    ('route_210_south', 'land', 8, 'PINECO', 21, 'PONYTA', 21),
    ('route_210_south', 'land', 10, 'PINECO', 21, 'PONYTA', 21),
    # Route 211
    ('route_211_west', 'land', 5, 'TEDDIURSA', 14, 'MACHOP', 14),
    ('route_211_east', 'land', 5, 'TEDDIURSA', 29, 'MACHOKE', 29),
    # Route 212 south
    ('route_212_south', 'land', 5, 'EKANS', 25, 'SHELLOS', 25),
    # Route 214
    ('route_214', 'land', 6, 'STUNKY', 24, 'GRAVELER', 24),
    ('route_214', 'land', 8, 'STUNKY', 24, 'HOUNDOUR', 24),
    ('route_214', 'land', 9, 'VULPIX', 24, 'RHYHORN', 24),
    # Wayward Cave
    ('wayward_cave_1f', 'land', 6, 'SANDSHREW', 19, 'GEODUDE', 19),
    ('wayward_cave_1f', 'land', 7, 'SANDSHREW', 20, 'GEODUDE', 20),
    # Route 218
    ('route_218', 'land', 6, 'GLAMEOW', 30, 'FLOATZEL', 30),
    ('route_218', 'land', 8, 'GLAMEOW', 31, 'FLOATZEL', 31),
    ('route_218', 'land', 10, 'GLAMEOW', 31, 'FLOATZEL', 31),
    # Iron Island
    ('iron_island_b1f_left_room', 'land', 8, 'SABLEYE', 30, 'GRAVELER', 30),
    ('iron_island_b1f_left_room', 'land', 9, 'MAWILE', 31, 'GRAVELER', 31),
    ('iron_island_b1f_left_room', 'land', 10, 'SABLEYE', 30, 'GRAVELER', 30),
    ('iron_island_b1f_left_room', 'land', 11, 'MAWILE', 31, 'GRAVELER', 31),
    ('iron_island_b1f_right_room', 'land', 8, 'SABLEYE', 30, 'GRAVELER', 30),
    ('iron_island_b1f_right_room', 'land', 9, 'MAWILE', 31, 'GRAVELER', 31),
    ('iron_island_b1f_right_room', 'land', 10, 'SABLEYE', 30, 'GRAVELER', 30),
    ('iron_island_b1f_right_room', 'land', 11, 'MAWILE', 31, 'GRAVELER', 31),
    ('iron_island_b2f_right_room', 'land', 8, 'SABLEYE', 31, 'GRAVELER', 31),
    ('iron_island_b2f_right_room', 'land', 9, 'MAWILE', 32, 'GRAVELER', 32),
    ('iron_island_b2f_right_room', 'land', 10, 'SABLEYE', 31, 'GRAVELER', 31),
    ('iron_island_b2f_right_room', 'land', 11, 'MAWILE', 32, 'GRAVELER', 32),
    ('iron_island_b2f_left_room', 'land', 8, 'SABLEYE', 31, 'GRAVELER', 31),
    ('iron_island_b2f_left_room', 'land', 9, 'MAWILE', 32, 'GRAVELER', 32),
    ('iron_island_b2f_left_room', 'land', 10, 'SABLEYE', 31, 'GRAVELER', 31),
    ('iron_island_b2f_left_room', 'land', 11, 'MAWILE', 32, 'GRAVELER', 32),
    ('iron_island_b3f', 'land', 8, 'SABLEYE', 31, 'GRAVELER', 31),
    ('iron_island_b3f', 'land', 9, 'MAWILE', 32, 'GRAVELER', 32),
    ('iron_island_b3f', 'land', 10, 'SABLEYE', 31, 'GRAVELER', 31),
    ('iron_island_b3f', 'land', 11, 'MAWILE', 32, 'GRAVELER', 32),
    # Mt. Coronet
    ('mt_coronet_2f', 'day', 1, 'SOLROCK', None, 'GRAVELER', None),
    ('mt_coronet_2f', 'night', 0, 'LUNATONE', None, 'GOLBAT', None),
    ('mt_coronet_3f', 'day', 1, 'SOLROCK', None, 'GRAVELER', None),
    ('mt_coronet_3f', 'night', 0, 'LUNATONE', None, 'GOLBAT', None),
    # Routes 219 and 221 (fishing)
    ('route_219', 'good_rod', 3, 'CLAMPERL', (20, 30), 'LUMINEON', (25, 35)),
    ('route_221', 'super_rod', 2, 'CLAMPERL', (35, 45), 'GYARADOS', (40, 55)),
    # Victory Road (post-game rooms)
    ('victory_road_1f_room_1', 'land', 8, 'SHIELDON', 48, 'STEELIX', 50),
    ('victory_road_1f_room_1', 'land', 10, 'SHIELDON', 48, 'STEELIX', 50),
    ('victory_road_1f_room_3', 'land', 8, 'CRANIDOS', 48, 'STEELIX', 50),
    ('victory_road_1f_room_3', 'land', 10, 'CRANIDOS', 48, 'STEELIX', 50),
    # Starters (before the League)
    ('route_209', 'land', 7, 'CHIKORITA', 19, 'ROSELIA', 20),
    ('route_212_north', 'land', 6, 'BULBASAUR', 23, 'KIRLIA', 24),
    ('route_215', 'land', 8, 'TREECKO', 21, 'MARILL', 22),
    ('route_215', 'land', 10, 'TREECKO', 21, 'MARILL', 22),
    ('valor_lakefront', 'land', 7, 'TURTWIG', 26, 'BIBAREL', 26),
    ('route_206', 'land', 6, 'CYNDAQUIL', 17, 'MACHOP', 18),
    ('fuego_ironworks_outside', 'land', 6, 'CHARMANDER', 28, 'MAGNEMITE', 28),
    ('fuego_ironworks_outside', 'land', 7, 'TORCHIC', 29, 'MAGNEMITE', 30),
    ('route_211_east', 'land', 9, 'CHIMCHAR', 28, 'BRONZOR', 29),
    ('route_211_east', 'land', 11, 'CHIMCHAR', 28, 'BRONZOR', 29),
    ('route_212_north', 'surf', 2, 'SQUIRTLE', (20, 30), 'GOLDUCK', (20, 40)),
    ('route_208', 'surf', 2, 'TOTODILE', (20, 30), 'GOLDUCK', (20, 40)),
    ('lake_valor', 'surf', 2, 'MUDKIP', (20, 30), 'GOLDUCK', (20, 40)),
    ('lake_acuity', 'surf', 2, 'PIPLUP', (20, 30), 'GOLDUCK', (20, 40)),
    # Route 224
    ('route_224', 'land', 9, 'SHUCKLE', 50, 'DUSTOX', 52),
    # Route 228
    ('route_228', 'land', 6, 'TRAPINCH', 49, 'DUGTRIO', 52),
    ('route_228', 'land', 7, 'TRAPINCH', 49, 'DUGTRIO', 50),
)

RATE_EDITS: tuple = ()

# ------------------------------------------------------------------------------------ Pokedex area map
# The Pokedex "Area" screen reads its own file, built by Nintendo from the same wild lists. Layout (pret
# tools/scripts/make_pokedex_enc_platinum.py, verified against the ROM by check_plan.py): members 0-3 = map
# positions (never touched); member 4 + species + 495 * category = the list of map numbers where that species
# shows up in that category: u32 map numbers, sorted, ending with a 0 word. Members change size, so the
# engine rebuilds this NARC (only these lists change). Map numbers are the encounter table's "map number"
# (dungeon or field numbering, see AREA_CATEGORIES); several tables share one number (e.g. all Iron Island rooms).
AREA_NARC_PATH = '/application/zukanlist/zkn_data/zukan_enc_platinum.narc'
AREA_SPECIES_STRIDE = 495            # species 0..494 per category (pret: len(Species) - 1)
AREA_FIRST_LIST = 4
AREA_CATEGORIES = ('dungeon_morning', 'dungeon_day', 'dungeon_night', 'dungeon_special', 'dungeon_special_natdex',
                   'field_morning', 'field_day', 'field_night', 'field_special', 'field_special_natdex')
AREA_MEMBER_COUNT = AREA_FIRST_LIST + AREA_SPECIES_STRIDE * len(AREA_CATEGORIES)     # 4954


class AreaEdit(NamedTuple):
    species: int
    category: str               # one of AREA_CATEGORIES
    map: int                    # map number in that category's numbering
    op: str                     # 'add' (must not be there yet) or 'remove' (must be there)


def area_member(species: int, category: str) -> int:
    return AREA_FIRST_LIST + species + AREA_SPECIES_STRIDE * AREA_CATEGORIES.index(category)


# Generated from EDITS by dev/sinnoh/check_plan.py --area-edits (the same rules as Nintendo's own build):
# every new placement is added with its time of day, and a species that no longer lives in a map loses it.
_AREA = (
    ('BULBASAUR', 'field_day', 27, 'add'),
    ('BULBASAUR', 'field_morning', 27, 'add'),
    ('BULBASAUR', 'field_night', 27, 'add'),
    ('CHARMANDER', 'field_day', 7, 'add'),
    ('CHARMANDER', 'field_morning', 7, 'add'),
    ('CHARMANDER', 'field_night', 7, 'add'),
    ('SQUIRTLE', 'field_day', 27, 'add'),
    ('SQUIRTLE', 'field_morning', 27, 'add'),
    ('SQUIRTLE', 'field_night', 27, 'add'),
    ('CATERPIE', 'dungeon_day', 2, 'add'),
    ('CATERPIE', 'dungeon_morning', 2, 'add'),
    ('CATERPIE', 'dungeon_night', 2, 'add'),
    ('WEEDLE', 'dungeon_day', 2, 'add'),
    ('WEEDLE', 'dungeon_morning', 2, 'add'),
    ('WEEDLE', 'dungeon_night', 2, 'add'),
    ('EKANS', 'field_day', 28, 'add'),
    ('EKANS', 'field_morning', 28, 'add'),
    ('EKANS', 'field_night', 28, 'add'),
    ('SANDSHREW', 'dungeon_day', 12, 'add'),
    ('SANDSHREW', 'dungeon_morning', 12, 'add'),
    ('SANDSHREW', 'dungeon_night', 12, 'add'),
    ('VULPIX', 'field_day', 22, 'add'),
    ('VULPIX', 'field_day', 30, 'add'),
    ('VULPIX', 'field_morning', 22, 'add'),
    ('VULPIX', 'field_morning', 30, 'add'),
    ('VULPIX', 'field_night', 22, 'add'),
    ('VULPIX', 'field_night', 30, 'add'),
    ('GROWLITHE', 'field_day', 20, 'add'),
    ('GROWLITHE', 'field_morning', 20, 'add'),
    ('GROWLITHE', 'field_night', 20, 'add'),
    ('CHIKORITA', 'field_day', 22, 'add'),
    ('CHIKORITA', 'field_morning', 22, 'add'),
    ('CHIKORITA', 'field_night', 22, 'add'),
    ('CYNDAQUIL', 'field_day', 19, 'add'),
    ('CYNDAQUIL', 'field_morning', 19, 'add'),
    ('CYNDAQUIL', 'field_night', 19, 'add'),
    ('TOTODILE', 'field_day', 21, 'add'),
    ('TOTODILE', 'field_morning', 21, 'add'),
    ('TOTODILE', 'field_night', 21, 'add'),
    ('MURKROW', 'dungeon_night', 2, 'add'),
    ('MISDREAVUS', 'dungeon_night', 2, 'add'),
    ('PINECO', 'field_day', 23, 'add'),
    ('PINECO', 'field_morning', 23, 'add'),
    ('PINECO', 'field_night', 23, 'add'),
    ('SHUCKLE', 'field_day', 38, 'add'),
    ('SHUCKLE', 'field_morning', 38, 'add'),
    ('SHUCKLE', 'field_night', 38, 'add'),
    ('TEDDIURSA', 'field_day', 25, 'add'),
    ('TEDDIURSA', 'field_day', 26, 'add'),
    ('TEDDIURSA', 'field_morning', 25, 'add'),
    ('TEDDIURSA', 'field_morning', 26, 'add'),
    ('TEDDIURSA', 'field_night', 25, 'add'),
    ('TEDDIURSA', 'field_night', 26, 'add'),
    ('TREECKO', 'field_day', 31, 'add'),
    ('TREECKO', 'field_morning', 31, 'add'),
    ('TREECKO', 'field_night', 31, 'add'),
    ('TORCHIC', 'field_day', 7, 'add'),
    ('TORCHIC', 'field_morning', 7, 'add'),
    ('TORCHIC', 'field_night', 7, 'add'),
    ('MUDKIP', 'dungeon_day', 18, 'add'),
    ('MUDKIP', 'dungeon_morning', 18, 'add'),
    ('MUDKIP', 'dungeon_night', 18, 'add'),
    ('LOTAD', 'field_day', 18, 'add'),
    ('LOTAD', 'field_morning', 18, 'add'),
    ('LOTAD', 'field_night', 18, 'add'),
    ('SEEDOT', 'dungeon_day', 2, 'add'),
    ('SEEDOT', 'dungeon_morning', 2, 'add'),
    ('SEEDOT', 'dungeon_night', 2, 'add'),
    ('SEEDOT', 'field_day', 23, 'add'),
    ('SEEDOT', 'field_morning', 23, 'add'),
    ('SEEDOT', 'field_night', 23, 'add'),
    ('SABLEYE', 'dungeon_day', 15, 'add'),
    ('SABLEYE', 'dungeon_morning', 15, 'add'),
    ('SABLEYE', 'dungeon_night', 15, 'add'),
    ('MAWILE', 'dungeon_day', 15, 'add'),
    ('MAWILE', 'dungeon_morning', 15, 'add'),
    ('MAWILE', 'dungeon_night', 15, 'add'),
    ('TRAPINCH', 'field_day', 41, 'add'),
    ('TRAPINCH', 'field_morning', 41, 'add'),
    ('TRAPINCH', 'field_night', 41, 'add'),
    ('ZANGOOSE', 'field_day', 21, 'add'),
    ('ZANGOOSE', 'field_morning', 21, 'add'),
    ('ZANGOOSE', 'field_night', 21, 'add'),
    ('SEVIPER', 'field_day', 21, 'add'),
    ('SEVIPER', 'field_morning', 21, 'add'),
    ('SEVIPER', 'field_night', 21, 'add'),
    ('LUNATONE', 'dungeon_night', 3, 'add'),
    ('SOLROCK', 'dungeon_day', 3, 'add'),
    ('CLAMPERL', 'field_day', 35, 'add'),
    ('CLAMPERL', 'field_day', 36, 'add'),
    ('CLAMPERL', 'field_morning', 35, 'add'),
    ('CLAMPERL', 'field_morning', 36, 'add'),
    ('CLAMPERL', 'field_night', 35, 'add'),
    ('CLAMPERL', 'field_night', 36, 'add'),
    ('TURTWIG', 'field_day', 10, 'add'),
    ('TURTWIG', 'field_morning', 10, 'add'),
    ('TURTWIG', 'field_night', 10, 'add'),
    ('CHIMCHAR', 'field_day', 26, 'add'),
    ('CHIMCHAR', 'field_morning', 26, 'add'),
    ('CHIMCHAR', 'field_night', 26, 'add'),
    ('PIPLUP', 'dungeon_day', 19, 'add'),
    ('PIPLUP', 'dungeon_morning', 19, 'add'),
    ('PIPLUP', 'dungeon_night', 19, 'add'),
    ('CRANIDOS', 'dungeon_day', 6, 'add'),
    ('CRANIDOS', 'dungeon_morning', 6, 'add'),
    ('CRANIDOS', 'dungeon_night', 6, 'add'),
    ('SHIELDON', 'dungeon_day', 6, 'add'),
    ('SHIELDON', 'dungeon_morning', 6, 'add'),
    ('SHIELDON', 'dungeon_night', 6, 'add'),
    ('GLAMEOW', 'field_day', 34, 'add'),
    ('GLAMEOW', 'field_morning', 34, 'add'),
    ('GLAMEOW', 'field_night', 34, 'add'),
    ('STUNKY', 'field_day', 19, 'add'),
    ('STUNKY', 'field_day', 30, 'add'),
    ('STUNKY', 'field_morning', 19, 'add'),
    ('STUNKY', 'field_morning', 30, 'add'),
    ('STUNKY', 'field_night', 19, 'add'),
    ('STUNKY', 'field_night', 30, 'add'),
    ('HOOTHOOT', 'dungeon_night', 2, 'remove'),
    ('BIDOOF', 'dungeon_day', 2, 'remove'),
    ('BIDOOF', 'dungeon_morning', 2, 'remove'),
    ('BIDOOF', 'dungeon_night', 2, 'remove'),
    ('KRICKETOT', 'dungeon_night', 2, 'remove'),
)
AREA_EDITS = tuple(AreaEdit(SPECIES[n], c, m, op) for n, c, m, op in _AREA)
# (map type, map number) of every table we edit, as the area file numbers them (for tests and people).
AREA_MAP_OF = {
    'eterna_forest': ('dungeon', 2),
    'fuego_ironworks_outside': ('field', 7),
    'iron_island_b1f_left_room': ('dungeon', 15),
    'iron_island_b1f_right_room': ('dungeon', 15),
    'iron_island_b2f_left_room': ('dungeon', 15),
    'iron_island_b2f_right_room': ('dungeon', 15),
    'iron_island_b3f': ('dungeon', 15),
    'lake_acuity': ('dungeon', 19),
    'lake_valor': ('dungeon', 18),
    'mt_coronet_2f': ('dungeon', 3),
    'mt_coronet_3f': ('dungeon', 3),
    'route_205_north': ('field', 18),
    'route_206': ('field', 19),
    'route_207': ('field', 20),
    'route_208': ('field', 21),
    'route_209': ('field', 22),
    'route_210_south': ('field', 23),
    'route_211_east': ('field', 26),
    'route_211_west': ('field', 25),
    'route_212_north': ('field', 27),
    'route_212_south': ('field', 28),
    'route_214': ('field', 30),
    'route_215': ('field', 31),
    'route_218': ('field', 34),
    'route_219': ('field', 35),
    'route_221': ('field', 36),
    'route_224': ('field', 38),
    'route_228': ('field', 41),
    'valor_lakefront': ('field', 10),
    'victory_road_1f_room_1': ('dungeon', 6),
    'victory_road_1f_room_3': ('dungeon', 6),
    'wayward_cave_1f': ('dungeon', 12),
}


def area_edits_for(member_index: int) -> list:
    return [a for a in AREA_EDITS if area_member(a.species, a.category) == member_index]


def area_members() -> list:
    """Member indexes the engine has to rewrite (sorted)."""
    return sorted({area_member(a.species, a.category) for a in AREA_EDITS})


def apply_area(member_index: int, data: bytes) -> bytes:
    """Patch one area list. Pure; raises ValueError if the list is malformed or an add/remove does not match
    vanilla (map already there / not there)."""
    if len(data) % 4 or len(data) < 4:
        raise ValueError(f'area member {member_index}: bad size {len(data)}')
    words = list(struct.unpack(f'<{len(data) // 4}I', data))
    if words[-1] != 0 or 0 in words[:-1]:
        raise ValueError(f'area member {member_index}: list not terminated by a single 0')
    maps = words[:-1]
    for a in area_edits_for(member_index):
        where = f'area member {member_index} (species {a.species} {a.category} map {a.map})'
        if a.op == 'add':
            if a.map in maps:
                raise ValueError(f'{where}: already there')
            maps.append(a.map)
        else:
            if a.map not in maps:
                raise ValueError(f'{where}: not there')
            maps.remove(a.map)
    maps.sort()
    return struct.pack(f'<{len(maps) + 1}I', *maps, 0)

# Design guard rails (checked by client/tests/test_sinnoh_complete.py):
# the first routes/caves of the game stay exactly vanilla ...
EARLY_UNTOUCHED = frozenset({
    'route_201', 'route_202', 'route_203', 'route_204_south', 'route_204_north', 'lake_verity_low_water',
    'lake_verity', 'twinleaf_town', 'oreburgh_gate_1f', 'oreburgh_gate_b1f', 'ravaged_path',
    'oreburgh_mine_b1f', 'oreburgh_mine_b2f', 'valley_windworks_outside', 'route_205_south',
})
# ... and these areas are only reachable after the Hall of Fame (starters and other strong lines go here).
POST_GAME = frozenset({
    'route_224', 'route_225', 'route_226', 'route_227', 'route_228', 'route_229', 'route_230', 'resort_area',
    'stark_mountain_outside', 'stark_mountain_room_1', 'stark_mountain_room_2', 'victory_road_1f_room_1',
    'victory_road_1f_room_2', 'victory_road_1f_room_3', 'snowpoint_temple_1f', 'snowpoint_temple_b1f',
    'snowpoint_temple_b2f', 'snowpoint_temple_b3f', 'snowpoint_temple_b4f', 'snowpoint_temple_b5f',
})


def _build():
    out = []
    for name, kind, slot, new, lv, old, olv in _DESIGN:
        t = TABLE_INDEX[name]
        if SLOT_KINDS[kind][3] == 'water':
            out.append(SlotEdit(t, name, kind, slot, SPECIES[new], None, lv[0], lv[1],
                                SPECIES[old], None, olv[0], olv[1]))
        elif SLOT_KINDS[kind][3] == 'land':
            out.append(SlotEdit(t, name, kind, slot, SPECIES[new], lv, None, None, SPECIES[old], olv, None, None))
        else:
            out.append(SlotEdit(t, name, kind, slot, SPECIES[new], None, None, None, SPECIES[old], None, None, None))
    return tuple(out)


EDITS = _build()


# ---------------------------------------------------------------------------------------------- helpers
def edits_for(member_index: int) -> list:
    return [e for e in EDITS if e.table == member_index]


def _off(kind, slot):
    base, count, size, _ = SLOT_KINDS[kind]
    if not 0 <= slot < count:
        raise ValueError(f'slot {slot} out of range for {kind}')
    return base + size * slot


def parse_table(data: bytes) -> dict:
    """424-byte member -> {'rates': {kind: rate}, 'slots': {kind: [...]}, 'forms': [6 ints]} with land
    (level, species), water (min, max, species) and plain species ints; 'forms' = the untouched form-rate /
    Unown words at offset 140."""
    if len(data) != TABLE_SIZE:
        raise ValueError(f'table is {len(data)} bytes, expected {TABLE_SIZE}')
    rates = {k: struct.unpack_from('<I', data, o)[0] for k, o in RATE_OFFSETS.items()}
    forms = list(struct.unpack_from('<6I', data, FORMS_OFFSET))
    slots = {}
    for kind, (base, count, size, etype) in SLOT_KINDS.items():
        vals = []
        for i in range(count):
            o = base + size * i
            if etype == 'land':
                vals.append(struct.unpack_from('<II', data, o))
            elif etype == 'water':
                mx, mn, _pad, sp = struct.unpack_from('<BBHI', data, o)
                vals.append((mn, mx, sp))
            else:
                vals.append(struct.unpack_from('<I', data, o)[0])
        slots[kind] = vals
    return {'rates': rates, 'slots': slots, 'forms': forms}


def apply(member_index: int, data: bytes) -> bytes:
    """Patch one 424-byte member with our edits. Pure and deterministic. Raises ValueError (naming table,
    kind and slot) if the vanilla value is not what the spec expects. Water pad bytes are left untouched."""
    if len(data) != TABLE_SIZE:
        raise ValueError(f'table {member_index}: {len(data)} bytes, expected {TABLE_SIZE}')
    b = bytearray(data)
    for e in edits_for(member_index):
        o = _off(e.kind, e.slot)
        etype = SLOT_KINDS[e.kind][3]
        where = f'table {e.table} {e.name} {e.kind}[{e.slot}]'
        if etype == 'land':
            lv, sp = struct.unpack_from('<II', b, o)
            if (lv, sp) != (e.old_level, e.old_species):
                raise ValueError(f'{where}: found level {lv} species {sp}, expected {e.old_level}/{e.old_species}')
            struct.pack_into('<II', b, o, e.level, e.species)
        elif etype == 'water':
            mx, mn = b[o], b[o + 1]
            sp = struct.unpack_from('<I', b, o + 4)[0]
            if (mn, mx, sp) != (e.old_min, e.old_max, e.old_species):
                raise ValueError(f'{where}: found {mn}-{mx} species {sp}, '
                                 f'expected {e.old_min}-{e.old_max}/{e.old_species}')
            b[o] = e.max_level
            b[o + 1] = e.min_level
            struct.pack_into('<I', b, o + 4, e.species)
        else:
            sp = struct.unpack_from('<I', b, o)[0]
            if sp != e.old_species:
                raise ValueError(f'{where}: found species {sp}, expected {e.old_species}')
            struct.pack_into('<I', b, o, e.species)
    for r in RATE_EDITS:
        if r.table != member_index:
            continue
        o = RATE_OFFSETS[r.kind]
        cur = struct.unpack_from('<I', b, o)[0]
        if cur != r.old_rate:
            raise ValueError(f'table {r.table} {r.name} {r.kind} rate: found {cur}, expected {r.old_rate}')
        struct.pack_into('<I', b, o, r.rate)
    return bytes(b)


def validate() -> list:
    """Sanity problems of the spec itself (empty list = fine). Used by the unit test and check_plan."""
    probs = []
    seen = set()
    for e in EDITS:
        where = f'{e.name} {e.kind}[{e.slot}]'
        if not 0 <= e.table < TABLE_COUNT or TABLE_NAMES[e.table] != e.name:
            probs.append(f'{where}: bad table id')
        if e.kind not in SLOT_KINDS:
            probs.append(f'{where}: bad kind')
            continue
        if not 0 <= e.slot < SLOT_KINDS[e.kind][1]:
            probs.append(f'{where}: slot out of range')
        if (e.table, e.kind, e.slot) in seen:
            probs.append(f'{where}: edited twice')
        seen.add((e.table, e.kind, e.slot))
        if not 1 <= e.species <= MAX_SPECIES or not 1 <= e.old_species <= MAX_SPECIES:
            probs.append(f'{where}: species out of range')
        if e.species == e.old_species and (e.level, e.min_level, e.max_level) == (e.old_level, e.old_min, e.old_max):
            probs.append(f'{where}: no-op edit')
        etype = SLOT_KINDS[e.kind][3]
        if etype == 'land':
            if e.level is None or not 2 <= e.level <= 100 or e.min_level is not None:
                probs.append(f'{where}: bad land level')
        elif etype == 'water':
            if (e.level is not None or e.min_level is None or e.max_level is None
                    or not 1 <= e.min_level <= e.max_level <= 100):
                probs.append(f'{where}: bad water levels')
        elif (e.level, e.min_level, e.max_level) != (None, None, None):
            probs.append(f'{where}: species-only slot with a level')
        if e.name.startswith('great_marsh') and e.kind == 'land' and e.slot in (6, 7):
            probs.append(f'{where}: Great Marsh land slots 6/7 are always replaced by the daily mon')
        if e.name.startswith('great_marsh') and e.kind == 'surf':
            probs.append(f'{where}: no surfing in the Great Marsh (Safari game: no field moves)')
        if e.name == 'trophy_garden' and e.kind == 'land' and e.slot in (6, 7):
            probs.append(f'{where}: Trophy Garden slots 6/7 are replaced post-game')
    seen_a = set()
    for a in AREA_EDITS:
        if (a.category not in AREA_CATEGORIES or a.op not in ('add', 'remove') or not 1 <= a.map <= 255
                or not 1 <= a.species <= MAX_SPECIES):
            probs.append(f'bad area edit {a}')
        if (a.species, a.category, a.map) in seen_a:
            probs.append(f'area edit twice: {a}')
        seen_a.add((a.species, a.category, a.map))
    if sum(LAND_WEIGHTS) != 100 or any(sum(w) != 100 for w in WATER_WEIGHTS.values()):
        probs.append('rarity weights do not sum to 100')
    for r in RATE_EDITS:
        if TABLE_NAMES[r.table] != r.name or r.kind not in RATE_OFFSETS or not 0 <= r.rate <= 100:
            probs.append(f'rate edit {r}: bad')
    return probs


def digest() -> str:
    """Stable digest of the edits (the engine pins the built ROM's SHA1 to SPEC_VERSION + this)."""
    h = hashlib.sha1()
    h.update(repr((SPEC_VERSION, EDITS, RATE_EDITS, AREA_EDITS)).encode())
    return h.hexdigest()


RADAR_WEIGHTS = (10, 10, 1, 1)   # radar list entries -> land slots 4, 5, 10, 11 inside a shaking patch


def chances(table: dict, kind: str, time: str = 'day') -> dict:
    """species -> percent for one parsed table: kind 'land' (time 'morning' | 'day' | 'night', without swarm,
    radar or GBA) or a water kind. Great Marsh's daily slots are counted as they are (callers decide)."""
    out = {}
    if kind == 'land':
        sl = [sp for _lv, sp in table['slots']['land']]
        if time in ('day', 'night'):
            sl[2], sl[3] = table['slots'][time]
        for sp, w in zip(sl, LAND_WEIGHTS):
            out[sp] = out.get(sp, 0) + w
    else:
        for (_mn, _mx, sp), w in zip(table['slots'][kind], WATER_WEIGHTS[kind]):
            out[sp] = out.get(sp, 0) + w
    return out
