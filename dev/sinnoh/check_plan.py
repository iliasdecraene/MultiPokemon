#!/usr/bin/env python3
"""Complete Sinnoh: obtainability audit of US Platinum + proof that the spec works.

    python dev/sinnoh/check_plan.py            # audit vanilla + check the spec (exit 1 on any problem)
    python dev/sinnoh/check_plan.py --audit    # also print the per-family audit table (vanilla)
    python dev/sinnoh/check_plan.py --markdown # print the audit as markdown (used for PLAN.md)
    python dev/sinnoh/check_plan.py --changes  # per edited table: which chances change (used for PLAN.md)

Sources (read-only):
  * pret/pokeplatinum (PRET env var, default /home/claude/pokeplatinum): encounter JSONs, encdata_ex lists
    (honey trees, Trophy Garden, Great Marsh), species list, evolutions.
  * The real ROM (dev/states/platinum.nds) if present: every encounter table is compared with pret byte for
    byte and every edit's `old_*` value is checked against the ROM (the engine refuses to patch otherwise).
  * Gifts, static encounters, in-game trades, fossils, roamers and distribution events cannot be read from
    data files, so they are listed below in SCRIPTED with the pret script each one comes from (verified by hand
    in res/field/scripts/*.s and src/*.c).

What it proves (exit code 0 only if all hold):
  1. every non-mythical evolution family has at least one way in single-player Platinum with the edits
     (wild / special wild / gift / static / event), except the other-region legendaries that wait for Ilias's
     decision (LEGENDARY_PENDING; they are reported, not failed);
  2. no species lost access: every species that vanilla offers in a wild category is still offered in the
     same category (or a more available one) somewhere no later in the story than before;
  3. the spec itself is sane (sinnoh_complete.validate()).
"""
import json
import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'client'))

from yellowlink import sinnoh_complete as sc  # noqa: E402

PRET = os.environ.get('PRET', '/home/claude/pokeplatinum')
ROM = os.path.join(ROOT, 'dev', 'states', 'platinum.nds')
ENC_DIR = os.path.join(PRET, 'res', 'field', 'encounters')

# ---------------------------------------------------------------------------------------------- pret data
def load_species():
    names = [l.strip()[len('SPECIES_'):] for l in open(os.path.join(PRET, 'generated', 'species.txt')) if l.strip()]
    return names[:494]                      # 0 = NONE, 1..493 (EGG/BAD_EGG dropped)


SPECIES = load_species()
SID = {n: i for i, n in enumerate(SPECIES)}


def sid(name):
    return SID[name.replace('SPECIES_', '')]


def pretty(i):
    n = SPECIES[i]
    special = {'NIDORAN_F': 'Nidoran♀', 'NIDORAN_M': 'Nidoran♂', 'MR_MIME': 'Mr. Mime', 'MIME_JR': 'Mime Jr.',
               'FARFETCHD': "Farfetch'd", 'HO_OH': 'Ho-Oh', 'PORYGON_Z': 'Porygon-Z'}
    return special.get(n, n.title())


def pret_dirname(name):
    return name.lower()


def load_evolutions():
    evo = defaultdict(list)
    base = os.path.join(PRET, 'res', 'pokemon')
    for i in range(1, 494):
        p = os.path.join(base, pret_dirname(SPECIES[i]), 'data.json')
        if not os.path.exists(p):
            raise SystemExit(f'missing {p}')
        d = json.load(open(p))
        for e in d.get('evolutions', []):
            evo[i].append(sid(e[-1]))
    return evo


def families():
    """root species -> sorted members. Families = connected components of the evolution graph (babies and
    split evolutions included). Root = lowest dex number that has no pre-evolution."""
    evo = load_evolutions()
    parent = list(range(494))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    pre = set()
    for a, outs in evo.items():
        for b in outs:
            pre.add(b)
            parent[find(a)] = find(b)
    comp = defaultdict(list)
    for i in range(1, 494):
        comp[find(i)].append(i)
    fams = {}
    for members in comp.values():
        bases = [m for m in members if m not in pre]
        fams[min(bases)] = sorted(members)
    return fams, evo


FAMILIES, EVOLUTIONS = families()
FAMILY_OF = {m: root for root, ms in FAMILIES.items() for m in ms}

MYTHICAL = {sid(n) for n in ('MEW', 'CELEBI', 'JIRACHI', 'DEOXYS', 'PHIONE', 'MANAPHY')}
# Other-region legendaries + the Regi/Regigigas loop: options for Ilias, not in wild tables without asking.
LEGENDARY_PENDING = {sid(n) for n in (
    'MEWTWO', 'RAIKOU', 'ENTEI', 'SUICUNE', 'LUGIA', 'HO_OH', 'REGIROCK', 'REGICE', 'REGISTEEL', 'LATIAS',
    'LATIOS', 'KYOGRE', 'GROUDON', 'RAYQUAZA', 'REGIGIGAS')}

# ------------------------------------------------------------------------------------------ story stages
# Rough story point at which a table's map can first be visited (0 = start ... 8 = 8 badges/Victory Road,
# 9 = after the Hall of Fame). Platinum gym order: Oreburgh, Eterna, Hearthome, Veilstone, Pastoria,
# Canalave, Snowpoint, Sunyshore. Water: Surf needs 5 badges (HM from Celestic, Fen Badge); Old Rod early
# (Jubilife), Good Rod Route 209 (2 badges), Super Rod in the post-game Fight Area.
STAGE = {
    'route_201': 0, 'route_202': 0, 'lake_verity_low_water': 0, 'lake_verity': 9, 'twinleaf_town': 0,
    'route_203': 0, 'route_204_south': 0, 'oreburgh_gate_1f': 0, 'oreburgh_gate_b1f': 1, 'ravaged_path': 1,
    'oreburgh_mine_b1f': 0, 'oreburgh_mine_b2f': 0, 'route_204_north': 1, 'valley_windworks_outside': 1,
    'route_205_south': 1, 'eterna_forest': 1, 'route_205_north': 1, 'eterna_city': 2, 'old_chateau': 2,
    'old_chateau_dining_area': 2, 'old_chateau_side_rooms': 2, 'old_chateau_corridor': 2,
    'old_chateau_back_west_room': 2, 'old_chateau_back_middle_west_room': 2, 'old_chateau_back_middle_room': 2,
    'old_chateau_back_middle_east_room': 2, 'old_chateau_back_east_room': 2, 'route_211_west': 2,
    'route_206': 2, 'route_207': 2, 'wayward_cave_1f': 4, 'wayward_cave_b1f': 4, 'mt_coronet_1f_south': 2,
    'mt_coronet_1f_north_room_1': 2, 'route_208': 2, 'route_209': 3, 'route_209_lost_tower_1f': 3,
    'route_209_lost_tower_2f': 3, 'route_209_lost_tower_3f': 3, 'route_209_lost_tower_4f': 3,
    'route_209_lost_tower_5f': 3, 'route_210_south': 3, 'solaceon_ruins_maniac_tunnel_room': 3,
    'ruin_maniac_cave_short': 3, 'ruin_maniac_cave_long': 3, 'maniac_tunnel': 3,
    'route_215': 3, 'route_214': 4, 'valor_lakefront': 4, 'route_213': 4, 'pastoria_city': 4,
    'great_marsh_1': 4, 'great_marsh_2': 4, 'great_marsh_3': 4, 'great_marsh_4': 4, 'great_marsh_5': 4,
    'great_marsh_6': 4, 'route_212_south': 4, 'route_212_north': 3, 'trophy_garden': 3,
    'route_210_north': 5, 'route_211_east': 5, 'celestic_town': 5, 'fuego_ironworks_outside': 5,
    'route_218': 5, 'canalave_city': 5, 'iron_island': 6, 'iron_island_1f': 6,
    'iron_island_b1f_left_room': 6, 'iron_island_b1f_right_room': 6, 'iron_island_b2f_right_room': 6,
    'iron_island_b2f_left_room': 6, 'iron_island_b3f': 6, 'lake_valor': 6, 'mt_coronet_2f': 6,
    'mt_coronet_3f': 6, 'mt_coronet_1f_tunnel_room': 6, 'route_216': 6, 'route_217': 6,
    'acuity_lakefront': 6, 'lake_acuity': 6, 'mt_coronet_outside_north': 7, 'mt_coronet_outside_south': 7,
    'mt_coronet_4f_rooms_1_and_2': 7, 'mt_coronet_4f_room_3': 7, 'mt_coronet_5f': 7, 'mt_coronet_6f': 7,
    'mt_coronet_1f_north_room_2': 7, 'mt_coronet_b1f': 7, 'route_219': 7, 'route_220': 7, 'route_221': 7,
    'route_222': 7, 'sunyshore_city': 7, 'route_223': 8, 'pokemon_league': 8, 'victory_road_1f': 8,
    'victory_road_2f': 8, 'victory_road_b1f': 8, 'victory_road_1f_room_1': 9, 'victory_road_1f_room_2': 9,
    'victory_road_1f_room_3': 9, 'route_224': 9, 'route_225': 9, 'route_226': 9, 'route_227': 9,
    'route_228': 9, 'route_229': 9, 'route_230': 9, 'resort_area': 9, 'stark_mountain_outside': 9,
    'stark_mountain_room_1': 9, 'stark_mountain_room_2': 9, 'snowpoint_temple_1f': 9,
    'snowpoint_temple_b1f': 9, 'snowpoint_temple_b2f': 9, 'snowpoint_temple_b3f': 9,
    'snowpoint_temple_b4f': 9, 'snowpoint_temple_b5f': 9, 'sendoff_spring': 8,
}
# Tables no map header points to (checked in pret include/data/map_headers.h): never used by the game.
UNUSED = {'turnback_cave_entrance', 'turnback_cave_pillar_room', 'turnback_cave_giratina_room',
          'turnback_cave_pillar_3_room_6'} | {n for n in sc.TABLE_NAMES if n.startswith('unknown_')}
for _n in sc.TABLE_NAMES:
    if _n.startswith('solaceon_ruins_room'):
        STAGE[_n] = 3
    if _n.startswith('turnback_cave_pillar') and _n not in UNUSED:
        STAGE[_n] = 8
assert set(STAGE) | UNUSED == set(sc.TABLE_NAMES) and not set(STAGE) & UNUSED
WATER_MIN_STAGE = {'surf': 5, 'old_rod': 1, 'good_rod': 2, 'super_rod': 9}
POST_NATDEX = 9      # swarms, Poke Radar, Trophy Garden dailies, GBA slots, marsh NatDex list


def stage_of(table_name, kind):
    s = STAGE.get(table_name)
    if s is None:
        return None
    return max(s, WATER_MIN_STAGE.get(kind, 0))


# ---------------------------------------------------------------------------------------- tables
def pret_tables():
    """List of 183 tables as plain dicts (sc.parse_table format), from pret's JSONs."""
    order = [l.strip() for l in open(os.path.join(ENC_DIR, 'encounters.order')) if l.strip()]
    out = []
    for name in order:
        d = json.load(open(os.path.join(ENC_DIR, name + '.json')))
        t = {'name': name[len('encounters_'):], 'rates': {}, 'slots': {}}
        t['rates'] = {'land': d['land_rate'], 'surf': d['surf_rate'], 'old_rod': d['old_rod_rate'],
                      'good_rod': d['good_rod_rate'], 'super_rod': d['super_rod_rate']}
        t['forms'] = [d['rate_form0'], d['rate_form1'], d['rate_form2'], d['rate_form3'], d['rate_form4'],
                      d['unown_table']]
        t['slots']['land'] = [(e['level'], sid(e['species'])) for e in d['land_encounters']]
        for kind, key in (('swarm', 'swarms'), ('day', 'day'), ('night', 'night'), ('radar', 'radar'),
                          ('ruby', 'ruby'), ('sapphire', 'sapphire'), ('emerald', 'emerald'),
                          ('firered', 'firered'), ('leafgreen', 'leafgreen')):
            t['slots'][kind] = [sid(s) for s in d[key]]
        for kind in ('surf', 'old_rod', 'good_rod', 'super_rod'):
            t['slots'][kind] = [(e['level_min'], e['level_max'], sid(e['species']))
                                for e in d[kind + '_encounters']]
        out.append(t)
    return out


def pret_bytes(t):
    """Rebuild the 424-byte member from a parsed table (the same packing as pret's encounter.py)."""
    b = bytearray(sc.TABLE_SIZE)
    for kind, off in sc.RATE_OFFSETS.items():
        struct.pack_into('<I', b, off, t['rates'][kind])
    struct.pack_into('<6I', b, sc.FORMS_OFFSET, *t['forms'])
    for kind, (base, count, size, etype) in sc.SLOT_KINDS.items():
        for i, v in enumerate(t['slots'][kind]):
            if etype == 'land':
                struct.pack_into('<II', b, base + size * i, v[0], v[1])
            elif etype == 'water':
                struct.pack_into('<BBHI', b, base + size * i, v[1], v[0], 0, v[2])
            else:
                struct.pack_into('<I', b, base + size * i, v)
    return bytes(b)


def rom_members():
    if not os.path.exists(ROM):
        return None
    from yellowlink.ndsrom import NDSRom
    rom = NDSRom(ROM)
    narc = rom.narc(sc.NARC_PATH)
    return [narc.member(i) for i in range(len(narc))]


def load_extra():
    ht = json.load(open(os.path.join(ENC_DIR, 'encounters_honey_tree.json')))
    tg = json.load(open(os.path.join(ENC_DIR, 'encounters_trophy_garden.json')))
    gm = json.load(open(os.path.join(ENC_DIR, 'encounters_great_marsh_lookout.json')))
    fb = json.load(open(os.path.join(ENC_DIR, 'encounters_mt_coronet_b1f.json')))['elusive_rod_encounter']
    return {
        'honey': {sid(s) for k in ('common', 'uncommon', 'rare') for s in ht[k]},
        'trophy_daily': {sid(s) for s in tg['daily_encounters']},
        'marsh_daily_local': {sid(s) for s in gm['before_national_dex']},
        'marsh_daily_natdex': {sid(s) for s in gm['after_national_dex']},
        'feebas': {sid(fb['species'])},
    }


# ------------------------------------------------------------------------- scripted (non-table) sources
# kind, species, where, stage, pret source. Checked by hand against the scripts; see PLAN.md for notes.
SCRIPTED = [
    ('gift_choice', 'TURTWIG', 'Starter (one of three)', 0, 'scripts_route_201.s GivePokemon VAR_0x8000'),
    ('gift_choice', 'CHIMCHAR', 'Starter (one of three)', 0, 'scripts_route_201.s'),
    ('gift_choice', 'PIPLUP', 'Starter (one of three)', 0, 'scripts_route_201.s'),
    ('gift', 'TOGEPI', 'Egg from Cynthia, Eterna City', 2, 'scripts_eterna_city.s GiveEgg SPECIES_TOGEPI'),
    ('gift', 'EEVEE', 'Bebe, Hearthome City', 3, 'scripts_hearthome_city_northwest_house.s GivePokemon'),
    ('gift', 'RIOLU', 'Egg from Riley, Iron Island', 6, 'scripts_iron_island_b2f_left_room.s GiveEgg'),
    ('gift', 'PORYGON', 'Pokefan in a Veilstone City house', 3,
     'scripts_veilstone_city_northeast_house.s GivePokemon SPECIES_PORYGON'),
    ('fossil_tid', 'CRANIDOS', 'Skull Fossil (Underground) - ONLY if Trainer ID is odd', 1,
     'src/underground/mining.c oddTIDWeight'),
    ('fossil_tid', 'SHIELDON', 'Armor Fossil (Underground) - ONLY if Trainer ID is even', 1,
     'src/underground/mining.c evenTIDWeight'),
    ('fossil', 'OMANYTE', 'Helix Fossil (Underground, after National Dex)', 9, 'mining.c NatDex weights'),
    ('fossil', 'KABUTO', 'Dome Fossil (Underground, after National Dex)', 9, 'mining.c'),
    ('fossil', 'AERODACTYL', 'Old Amber (Underground, after National Dex)', 9, 'mining.c'),
    ('fossil', 'LILEEP', 'Root Fossil (Underground, after National Dex)', 9, 'mining.c'),
    ('fossil', 'ANORITH', 'Claw Fossil (Underground, after National Dex)', 9, 'mining.c'),
    ('trade', 'ABRA', 'Trade for Machop (Oreburgh City)', 1, 'NPC_TRADE_KAZZA_ABRA'),
    ('trade', 'CHATOT', 'Trade for Buizel (Eterna City)', 2, 'NPC_TRADE_CHARAP_CHATOT'),
    ('trade', 'HAUNTER', 'Trade for Medicham (Snowpoint City)', 7, 'NPC_TRADE_GASPAR_HAUNTER'),
    ('trade', 'MAGIKARP', 'Trade for Finneon (Route 226)', 9, 'NPC_TRADE_FOPPA_MAGIKARP'),
    ('static', 'DRIFLOON', 'Valley Windworks, Fridays', 2, 'scripts_valley_windworks_outside.s'),
    ('static', 'ROTOM', 'Old Chateau TV, at night', 2, 'scripts_old_chateau_back_middle_west_room.s'),
    ('static', 'SPIRITOMB', 'Hallowed Tower (Odd Keystone + 32 Underground talks)', 3, 'scripts_route_209.s'),
    ('static', 'UXIE', 'Acuity Cavern', 7, 'scripts_acuity_cavern.s'),
    ('static', 'AZELF', 'Valor Cavern', 7, 'scripts_valor_cavern.s'),
    ('roamer', 'MESPRIT', 'Roams after Verity Cavern', 7, 'scripts_verity_cavern.s ActivateRoamingPokemon'),
    ('static', 'GIRATINA', 'Distortion World / Turnback Cave', 8, 'scripts_distortion_world / turnback_cave'),
    ('static', 'DIALGA', 'Spear Pillar (post-game)', 9, 'scripts_spear_pillar_dialga.s'),
    ('static', 'PALKIA', 'Spear Pillar (post-game)', 9, 'scripts_spear_pillar_palkia.s'),
    ('static', 'HEATRAN', 'Stark Mountain (post-game, with Buck)', 9, 'scripts_stark_mountain_room_3.s'),
    ('roamer', 'CRESSELIA', 'Fullmoon Island, then roams (post-game)', 9, 'scripts_fullmoon_island_forest.s'),
    ('roamer', 'ARTICUNO', 'Roams after talking to Oak in Eterna (National Dex)', 9,
     'scripts_eterna_city_south_house.s'),
    ('roamer', 'ZAPDOS', 'Roams after talking to Oak in Eterna (National Dex)', 9, 'same'),
    ('roamer', 'MOLTRES', 'Roams after talking to Oak in Eterna (National Dex)', 9, 'same'),
    ('blocked', 'REGIGIGAS', 'Snowpoint Temple: needs Regirock+Regice+Registeel in the party', 9,
     'scripts_snowpoint_temple_b5f.s CheckHasAllLegendaryTitansInParty'),
    ('blocked', 'REGIROCK', 'Rock Peak Ruins: needs an EVENT (fateful) Regigigas in the party', 9,
     'scripts_rock_peak_ruins.s CheckPartyHasFatefulEncounterRegigigas'),
    ('blocked', 'REGICE', 'Iceberg Ruins: needs an EVENT (fateful) Regigigas', 9, 'scripts_iceberg_ruins.s'),
    ('blocked', 'REGISTEEL', 'Iron Ruins: needs an EVENT (fateful) Regigigas', 9, 'scripts_iron_ruins.s'),
    ('event', 'DARKRAI', 'Member Card -> Newmoon Island', 9, 'DISTRIBUTION_EVENT_DARKRAI'),
    ('event', 'SHAYMIN', "Oak's Letter -> Seabreak Path / Flower Paradise", 9, 'DISTRIBUTION_EVENT_SHAYMIN'),
    ('event', 'ARCEUS', 'Azure Flute -> Hall of Origin', 9, 'DISTRIBUTION_EVENT_ARCEUS'),
]

# Ranking of access kinds: lower = easier/more normal. Used for "no species lost access".
KIND_RANK = {'wild': 0, 'wild_time': 1, 'honey': 2, 'marsh_daily': 2, 'trophy_daily': 3, 'feebas': 3,
             'swarm': 4, 'radar': 4, 'gift': 1, 'gift_choice': 5, 'trade': 5, 'fossil': 2,
             'fossil_tid': 6, 'static': 1, 'roamer': 2, 'event': 7, 'gba': 98, 'blocked': 99}
PLAYABLE = {k for k, r in KIND_RANK.items() if r < 98}

KIND_LABEL = {
    'wild': 'wild (normal)', 'wild_time': 'wild, one time of day only', 'swarm': 'swarm (post-game)',
    'radar': 'Poke Radar (post-game)', 'gba': 'GBA dual-slot only (impossible here)',
    'marsh_daily': 'Great Marsh daily', 'trophy_daily': 'Trophy Garden daily (post-game)',
    'honey': 'honey tree', 'feebas': 'Feebas tiles (Mt. Coronet B1F)', 'gift': 'gift / egg',
    'gift_choice': 'starter (1 of 3)', 'trade': 'in-game trade', 'fossil': 'fossil (post-game)',
    'fossil_tid': 'fossil (only one of the two per save)', 'static': 'static / one-off encounter',
    'roamer': 'roaming legendary', 'event': 'distribution event (needs our unlock)',
    'blocked': 'not obtainable (needs outside help)', 'none': 'not obtainable',
}


def sources(tables, extra):
    """species -> list of (kind, where, stage). `tables` in sc.parse_table format (+ 'name')."""
    src = defaultdict(list)

    def add(s, kind, where, stage):
        if s:
            src[s].append((kind, where, stage))
    for t in tables:
        n = t['name']
        if n in UNUSED:
            continue
        st = STAGE[n]
        marsh = n.startswith('great_marsh')
        if t['rates']['land']:
            land = t['slots']['land']
            slots = [i for i in range(12) if not (marsh and i in (6, 7))]  # marsh 6/7 always replaced
            base = {land[i][1] for i in slots}
            fixed = {land[i][1] for i in slots if i not in (2, 3)}
            morning = base
            day = fixed | set(t['slots']['day'])
            night = fixed | set(t['slots']['night'])
            for s in morning | day | night:
                kind = 'wild' if (s in morning and s in day and s in night) else 'wild_time'
                add(s, kind, n, st)
            for s in t['slots']['swarm']:
                add(s, 'swarm', n, POST_NATDEX)
            for s in t['slots']['radar']:
                add(s, 'radar', n, POST_NATDEX)
            for v in ('ruby', 'sapphire', 'emerald', 'firered', 'leafgreen'):
                for s in t['slots'][v]:
                    add(s, 'gba', n, POST_NATDEX)
        for kind in ('surf', 'old_rod', 'good_rod', 'super_rod'):
            if t['rates'][kind]:
                for (_lo, _hi, s) in t['slots'][kind]:
                    add(s, 'wild', f'{n} ({kind})', stage_of(n, kind))
    for s in extra['honey']:
        add(s, 'honey', 'honey trees', 1)
    for s in extra['trophy_daily']:
        add(s, 'trophy_daily', 'Trophy Garden (Mr. Backlot)', POST_NATDEX)
    for s in extra['marsh_daily_local']:
        add(s, 'marsh_daily', 'Great Marsh daily', 4)
    for s in extra['marsh_daily_natdex']:
        add(s, 'marsh_daily', 'Great Marsh daily (National Dex)', POST_NATDEX)
    for s in extra['feebas']:
        add(s, 'feebas', 'Mt. Coronet B1F fishing tiles', 7)
    for kind, name, where, st, _ref in SCRIPTED:
        add(sid(name), kind, where, st)
    return src


def best(src_list):
    if not src_list:
        return ('none', '', None)
    return min(src_list, key=lambda x: (KIND_RANK[x[0]], x[2] if x[2] is not None else 99))


def family_best(src, root):
    allsrc = [x for m in FAMILIES[root] for x in src.get(m, [])]
    return best(allsrc)


AUDIT_GROUPS = [
    ('wild', 'wild normal (grass/cave/surf/rods, any time of day)'),
    ('special', 'wild special only (time of day, swarm, Poke Radar, Great Marsh/Trophy Garden dailies, '
                'honey trees, Feebas)'),
    ('gift', 'gift / egg / in-game trade / fossil / static / roamer'),
    ('event', 'distribution event only (Darkrai, Shaymin, Arceus)'),
    ('gba', 'GBA dual-slot only (no cartridge here -> not obtainable)'),
    ('none', 'not obtainable in single-player Platinum'),
]


def group_of(kind):
    if kind == 'wild':
        return 'wild'
    if kind in ('wild_time', 'swarm', 'radar', 'marsh_daily', 'trophy_daily', 'honey', 'feebas'):
        return 'special'
    if kind in ('gift', 'gift_choice', 'trade', 'fossil', 'fossil_tid', 'static', 'roamer'):
        return 'gift'
    if kind == 'event':
        return 'event'
    if kind == 'gba':
        return 'gba'
    return 'none'


def audit(src):
    rows = []
    for root in sorted(FAMILIES):
        kind, where, st = family_best(src, root)
        rows.append((root, group_of(kind), kind, where, st))
    return rows


def fam_name(root):
    return '/'.join(pretty(m) for m in FAMILIES[root])


# ----------------------------------------------------------------------------------- change report
def change_report(tables, new_tables):
    """Markdown: per edited table, every species whose chance changed (morning/day/night or water kind)."""
    lines = []
    edited = sorted({e.table for e in sc.EDITS})
    for i in edited:
        a, b = tables[i], new_tables[i]
        n = a['name']
        rows = []
        if any(e.kind in ('land', 'day', 'night') for e in sc.edits_for(i)):
            per = {}
            for time in ('morning', 'day', 'night'):
                ca, cb = sc.chances(a, 'land', time), sc.chances(b, 'land', time)
                for spc in set(ca) | set(cb):
                    per.setdefault(spc, {t: (0, 0) for t in ('morning', 'day', 'night')})
                    per[spc][time] = (ca.get(spc, 0), cb.get(spc, 0))
            for spc, d in sorted(per.items(), key=lambda kv: -max(x[1] for x in kv[1].values())):
                if all(x == y for x, y in d.values()):
                    continue
                if len({v for v in d.values()}) == 1:
                    x, y = d['day']
                    rows.append(f'{pretty(spc)} {x}% -> **{y}%**')
                else:
                    rows.append(f'{pretty(spc)} ' + ', '.join(
                        f'{t} {d[t][0]}% -> **{d[t][1]}%**' for t in ('morning', 'day', 'night')
                        if d[t][0] != d[t][1]))
        for kind in ('surf', 'old_rod', 'good_rod', 'super_rod'):
            if not any(e.kind == kind for e in sc.edits_for(i)):
                continue
            ca, cb = sc.chances(a, kind), sc.chances(b, kind)
            for spc in sorted(set(ca) | set(cb), key=lambda x: -cb.get(x, 0)):
                if ca.get(spc, 0) != cb.get(spc, 0):
                    rows.append(f'{pretty(spc)} ({kind.replace("_", " ")}) {ca.get(spc, 0)}% -> '
                                f'**{cb.get(spc, 0)}%**')
        lines.append(f'- **{n}** (story stage {STAGE.get(n)}): ' + '; '.join(rows))
    return '\n'.join(lines)


# ----------------------------------------------------------------------------------------- checks
def apply_edits_to_tables(tables):
    """Apply the spec through its own byte-level apply() so the check covers the real patch code."""
    out = []
    for i, t in enumerate(tables):
        raw = pret_bytes(t)
        new = sc.apply(i, raw)
        nt = sc.parse_table(new)
        nt['name'] = t['name']
        out.append(nt)
    return out


def main(argv):
    problems = []
    tables = pret_tables()
    if [t['name'] for t in tables] != list(sc.TABLE_NAMES):
        problems.append('sinnoh_complete.TABLE_NAMES differs from pret encounters.order')
    # 0. pret == ROM, and the packer used here is exact
    members = rom_members()
    for i, t in enumerate(tables):
        mine = pret_bytes(t)
        if sc.parse_table(mine)['slots'] != t['slots']:
            problems.append(f'packer/parse mismatch on {t["name"]}')
        if members is not None and members[i] != mine:
            problems.append(f'ROM table {i} {t["name"]} differs from pret')
    rom_note = 'ROM checked: all 183 tables identical to pret' if members is not None else 'ROM not found: pret only'
    # 1. spec sanity
    problems += [f'spec: {p}' for p in sc.validate()]
    extra = load_extra()
    van_src = sources(tables, extra)
    new_tables = apply_edits_to_tables(tables)
    new_src = sources(new_tables, extra)
    van = audit(van_src)
    new = audit(new_src)
    # 2. every non-mythical family playable with the edits
    pending = []
    for root, grp, kind, where, st in new:
        if all(m in MYTHICAL for m in FAMILIES[root]):
            continue
        if kind not in PLAYABLE:
            if any(m in LEGENDARY_PENDING for m in FAMILIES[root]):
                pending.append(root)
            else:
                problems.append(f'family {fam_name(root)} still not obtainable ({kind})')
    # 2b. the Sinnoh dex (Platinum's 210, the badge counts 209 without Manaphy) can be completed
    sinnoh = [sid(n) for n in json.load(open(os.path.join(PRET, 'res', 'pokemon', 'sinnoh_pokedex.json')))[1:]]
    for root, grp, kind, where, st in new:
        if kind not in PLAYABLE:
            for m in FAMILIES[root]:
                if m in sinnoh and m not in MYTHICAL:
                    problems.append(f'Sinnoh dex entry {pretty(m)} not obtainable')
    sinnoh_missing_vanilla = sorted({m for root, grp, kind, where, st in van if kind not in PLAYABLE
                                     for m in FAMILIES[root] if m in sinnoh and m not in MYTHICAL})
    for e in sc.EDITS:
        if e.name in UNUSED:
            problems.append(f'edit on unused table {e.name}')
    # 3. no species lost access
    for s in range(1, 494):
        old = [x for x in van_src.get(s, []) if x[0] in PLAYABLE]
        if not old:
            continue
        nw = new_src.get(s, [])
        for kind, where, st in old:
            ok = any(KIND_RANK[k2] <= KIND_RANK[kind] and (st is None or (s2 is not None and s2 <= st))
                     for k2, w2, s2 in nw)
            if not ok:
                problems.append(f'{pretty(s)} lost access: was {kind} at {where} (stage {st})')
                break
    # report
    def counts(rows):
        c = defaultdict(int)
        for root, grp, *_ in rows:
            if all(m in MYTHICAL for m in FAMILIES[root]):
                c['mythical (excluded)'] += 1
            else:
                c[grp] += 1
        return c
    print(f'families: {len(FAMILIES)} ({rom_note})')
    cv, cn = counts(van), counts(new)
    print(f'{"category":<70} vanilla  with edits')
    for g, label in AUDIT_GROUPS + [('mythical (excluded)', 'mythical (excluded: Mew, Celebi, Jirachi, '
                                                            'Deoxys, Manaphy/Phione)')]:
        print(f'  {label[:68]:<68} {cv[g]:>7}  {cn[g]:>9}')
    print(f'edits: {len(sc.EDITS)} slot edits, {len(sc.RATE_EDITS)} rate edits, spec v{sc.SPEC_VERSION}')
    print(f'Sinnoh dex: {len(sinnoh)} entries; not catchable in vanilla single-player: '
          + ', '.join(pretty(m) for m in sinnoh_missing_vanilla) + ' (+ one of Cranidos/Shieldon per save)')
    if pending:
        print('pending Ilias decision: ' + ', '.join(fam_name(r) for r in pending))
    if '--audit' in argv or '--markdown' in argv:
        md = '--markdown' in argv
        for g, label in AUDIT_GROUPS:
            fams = [r for r in van if r[1] == g and not all(m in MYTHICAL for m in FAMILIES[r[0]])]
            print(f'\n{"### " if md else ""}{label} - {len(fams)} families')
            for root, grp, kind, where, st in fams:
                line = f'{fam_name(root)}: {KIND_LABEL[kind]}' + (f' - {where}' if where else '')
                print(('- ' if md else '  ') + line)
    if '--changes' in argv:
        print('\n' + change_report(tables, new_tables))
    if problems:
        print(f'\n{len(problems)} PROBLEM(S):')
        for p in problems:
            print('  ' + p)
        return 1
    print('\nOK: every non-mythical family is obtainable (legendaries pending excepted) and no species lost access')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
