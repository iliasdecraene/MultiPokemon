"""Generate the Soul Link area table of Pokémon Platinum (client/yellowlink/platinum_areas.py).

Usage: python dev/platinum/gen_soullink_areas.py [/path/to/pokeplatinum] [path/to/platinum.nds] \
           > client/yellowlink/platinum_areas.py
Defaults: /home/claude/pokeplatinum and dev/states/platinum.nds. Checked by dev/platinum/soullink_verify.py
(regenerates and compares, checks the labels against the ROM's own sMapHeaders table in RAM).

Sources:
  pret generated/map_headers.txt     -> map header id (enum MapHeaderID, 0..592) and its name
  pret include/data/map_headers.h    -> each map's mapLabelTextID (the location name the game shows / the Town
                                        Map uses; the same table platinum.MAP_LOCATION holds)
  pret res/text/location_names.json  -> label id -> text id; the NAMES come from the player's ROM
                                        (location_names message bank via ndsrom) so they are the game's own.
Rule (same as Yellow/Crystal: one area per place, every floor of a dungeon and every interior / gatehouse of a
town or route shares the area of the place it belongs to): a map's area is its location label, except
  * buildings that have their own label but stand in a town/route (Jubilife TV, Pokétch Co., Global Terminal,
    Trainers' School, Mining Museum, Cycle Shop, T.G. Eterna Bldg, ROTOM's Room, Poffin House, Contest Hall,
    Foreign Building, Game Corner, Veilstone Store, Canalave Library, Sunyshore Market, Vista Lighthouse,
    Flower Shop, Pokémon Day Care, Pokémon Mansion, Footstep House, Café, Restaurant, Grand Lake hotel,
    Villa, Battleground) -> that town/route, like Crystal's interiors; gifts received inside count there
    (fossils at the Mining Museum = Oreburgh City, Day Care eggs = Solaceon Town, Porygon/Eevee in houses...)
  * the Lost Tower (label Route 209) is a separate place with its own wild Pokémon -> 'lost_tower' (like
    Crystal's Team Rocket Base override)
  * maps labelled 'Mystery Zone' (unused maps, Underground, Union Room, Colosseums, record mixing) -> one area
    each, named after the map, so nothing unrelated is grouped.
Names not in the ROM's location list (Lost Tower, Underground, Union Room, ...) are written here.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRET = "/home/claude/pokeplatinum"
ROM = os.path.join(HERE, "..", "states", "platinum.nds")

# location label key -> the town/route the building stands in
BUILDING_HOME = {
    "jubilife_tv": "jubilife_city", "poketch_co": "jubilife_city", "global_terminal": "jubilife_city",
    "gts": "jubilife_city", "trainers_school": "jubilife_city",
    "canalave_library": "canalave_city",
    "mining_museum": "oreburgh_city",
    "cycle_shop": "eterna_city", "team_galactic_eterna_building": "eterna_city", "rotoms_room": "eterna_city",
    "poffin_house": "hearthome_city", "contest_hall": "hearthome_city", "foreign_building": "hearthome_city",
    "game_corner": "veilstone_city", "veilstone_store": "veilstone_city",
    "sunyshore_market": "sunyshore_city", "vista_lighthouse": "sunyshore_city",
    "flower_shop": "floaroma_town",
    "pokemon_day_care": "solaceon_town",
    "pokemon_mansion": "route_212",
    "footstep_house": "route_213",
    "cafe": "route_210",
    "restaurant": "valor_lakefront",
    "villa": "resort_area",
    "battleground": "survival_area",
}
# map name (without MAP_HEADER_) -> area, where the label (or BUILDING_HOME) isn't right
MAP_OVERRIDES = {
    "GRAND_LAKE_VALOR_LAKEFRONT_EAST_HOUSE": "valor_lakefront",
    "GRAND_LAKE_VALOR_LAKEFRONT_WEST_HOUSE": "valor_lakefront",
    "GRAND_LAKE_ROUTE_213_LOBBY": "route_213", "GRAND_LAKE_ROUTE_213_EAST_HOUSE": "route_213",
    "GRAND_LAKE_ROUTE_213_NORTHWEST_HOUSE": "route_213", "GRAND_LAKE_ROUTE_213_NORTHEAST_HOUSE": "route_213",
    "ROUTE_209_LOST_TOWER_1F": "lost_tower", "ROUTE_209_LOST_TOWER_2F": "lost_tower",
    "ROUTE_209_LOST_TOWER_3F": "lost_tower", "ROUTE_209_LOST_TOWER_4F": "lost_tower",
    "ROUTE_209_LOST_TOWER_5F": "lost_tower",
}
EXTRA_NAMES = {   # areas without a location name in the ROM
    "lost_tower": "Lost Tower", "underground": "Underground", "union_room": "Union Room",
    "communication_club_colosseum_2p": "Colosseum (2 players)",
    "communication_club_colosseum_4p": "Colosseum (4 players)", "record_mixing_room": "Record Mixing Room",
    "everywhere": "Everywhere (unused)", "nothing": "Nothing (unused)",
}


def key_of(text_id):
    """LocationNames_Text_JubilifeTV -> jubilife_tv (CamelCase -> snake_case; digits as their own word)."""
    s = text_id[len("LocationNames_Text_"):]
    s = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def parse(pret):
    names = []
    for line in open(os.path.join(pret, "generated", "map_headers.txt")):
        s = line.strip()
        if s.startswith("MAP_HEADER_COUNT"):
            break
        names.append(s[len("MAP_HEADER_"):])
    labels = {}
    cur = None
    for line in open(os.path.join(pret, "include", "data", "map_headers.h")):
        m = re.match(r"\s*\[MAP_HEADER_(\w+)\]\s*=", line)
        if m:
            cur = m.group(1)
        m = re.match(r"\s*\.mapLabelTextID\s*=\s*(\w+)", line)
        if m and cur:
            labels[cur] = m.group(1)
    texts = [m["id"] for m in json.load(open(os.path.join(pret, "res", "text", "location_names.json")))["messages"]]
    rows = []
    for i, n in enumerate(names):
        tid = labels[n]
        rows.append((i, n, texts.index(tid), key_of(tid)))
    return rows


def area_key(name, label_key):
    if name in MAP_OVERRIDES:
        return MAP_OVERRIDES[name]
    if label_key == "mystery_zone":
        return "underground" if name == "UNDERGROUND" else name.lower()
    return BUILDING_HOME.get(label_key, label_key)


def generate(pret=PRET, rom_path=ROM):
    sys.path.insert(0, os.path.join(HERE, "..", "..", "client"))
    from yellowlink import ndsrom
    rom = ndsrom.platinum(rom_path)
    rows = parse(pret)
    label_name = {}
    for _i, _n, lab, key in rows:
        label_name[key] = rom.location_name(lab)
    out = ["# Soul Link areas of Pokémon Platinum - GENERATED by dev/platinum/gen_soullink_areas.py from pret/pokeplatinum",
           "# (generated/map_headers.txt, include/data/map_headers.h) + location names from the ROM. Do not edit.",
           "# AREAS[map header id] = area key; AREA_NAMES[area key] = readable name.",
           "AREAS = ("]
    for i, n, lab, key in rows:
        out.append("    %r,  # %d %s (label %s)" % (area_key(n, key), i, n, key))
    out.append(")")
    keys = sorted(set(area_key(n, key) for _i, n, _lab, key in rows))
    out.append("AREA_NAMES = {")
    for k in keys:
        if k in label_name and label_name[k] not in ("?", ""):
            name = label_name[k]
        else:
            name = EXTRA_NAMES.get(k) or " ".join(w.capitalize() for w in k.split("_"))
        out.append("    %r: %r," % (k, name))
    out.append("}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.stdout.write(generate(a[0] if a else PRET, a[1] if len(a) > 1 else ROM))
