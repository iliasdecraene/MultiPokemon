"""Raid den spots for Kanto (Yellow), Johto (Crystal) and Sinnoh (Platinum), generated from the pret decomps' map data.
Writes client/yellowlink/raid_spots.json: coordinates + names + "reach" only (our own data, no ROM bytes).

    python dev/raids/gen_spots.py [--out PATH] [--per-map N]

A spot is one step square (Game Boy: 16x16 px; Platinum: one tile) on an outdoor map where
  * the square and the 8 around it are plain walkable ground: no grass (no wild battles at the den), water, ledge,
    door/warp, wall/tree/rock, ice, sand pit, Rock Climb... (Yellow: the tileset's passable tiles minus grass;
    Crystal: collision FLOOR; Platinum: no collision bit + tile behaviour "none"/sand/snow), so the den never blocks a
    path narrower than 3 squares and the player can stand on any side of it;
  * not within 2 squares of a warp, sign, script trigger (coord event) or person (people walk around), nor of the map
    edge (Game Boy maps join their neighbours there);
  * can be reached on foot (Game Boy: flood fill inside the map from its connected edges and its warps; Platinum: one
    flood fill over the whole outdoor grid from every warp, objects like Cut trees/Strength boulders/Rock Smash rocks
    block) - so islands behind water or HM obstacles drop out;
and every map gets a "reach" = the gym badges a player needs to walk there (hand table below, from the games' story:
HMs need their badge; Snorlax/Sudowoodo/Psyduck-style story blocks counted as the badge you normally have then).
Post-game areas (Sevii-free Kanto in Crystal, Sinnoh's Battle Zone, Seabreak Path...) are left out.
Spots are thinned out (at least 3 squares apart) and capped per map.
Needs /home/claude/pokeyellow, /home/claude/pokecrystal, /home/claude/pokeplatinum (or PRET_YELLOW/PRET_CRYSTAL/PRET_PLATINUM).
"""
import json
import os
import re
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PRET_Y = os.environ.get("PRET_YELLOW", "/home/claude/pokeyellow")
PRET_C = os.environ.get("PRET_CRYSTAL", "/home/claude/pokecrystal")
PRET_P = os.environ.get("PRET_PLATINUM", "/home/claude/pokeplatinum")
OUT = os.path.join(REPO, "client", "yellowlink", "raid_spots.json")
PER_MAP = 60
MIN_GAP = 3
EDGE = 2
NEAR = 2

# --- reach tables: gym badges needed to walk to a map (None = not used for raids) ---------------------------------
KANTO_REACH = {
    "PALLET_TOWN": 0, "ROUTE_1": 0, "VIRIDIAN_CITY": 0, "ROUTE_2": 0, "ROUTE_22": 0, "PEWTER_CITY": 0,
    "ROUTE_3": 1, "ROUTE_4": 1, "CERULEAN_CITY": 1, "ROUTE_24": 1, "ROUTE_25": 1, "ROUTE_5": 1, "ROUTE_6": 1,
    "VERMILION_CITY": 1, "ROUTE_11": 1,
    "ROUTE_9": 2, "ROUTE_10": 2, "LAVENDER_TOWN": 2, "ROUTE_8": 2, "ROUTE_7": 2, "CELADON_CITY": 2, "ROUTE_12": 2,
    # (no Route 17: Cycling Road, forced downhill on the bike - no dens)
    "ROUTE_16": 4, "ROUTE_18": 4, "FUCHSIA_CITY": 4, "ROUTE_13": 4, "ROUTE_14": 4, "ROUTE_15": 4,
    "SAFFRON_CITY": 4,
    "ROUTE_19": 5, "ROUTE_20": 5, "ROUTE_21": 5, "CINNABAR_ISLAND": 5,
    "ROUTE_23": 8, "INDIGO_PLATEAU": 8,
}
JOHTO_REACH = {
    "NEW_BARK_TOWN": 0, "ROUTE_29": 0, "CHERRYGROVE_CITY": 0, "ROUTE_30": 0, "ROUTE_31": 0, "VIOLET_CITY": 0,
    "ROUTE_46": 0,
    "ROUTE_32": 1, "ROUTE_33": 1, "AZALEA_TOWN": 1,
    "ROUTE_34": 2, "GOLDENROD_CITY": 2, "ROUTE_35": 2,
    "ROUTE_36": 3, "ROUTE_37": 3, "ECRUTEAK_CITY": 3, "ROUTE_38": 3, "ROUTE_39": 3, "OLIVINE_CITY": 3,
    "ROUTE_40": 4, "ROUTE_41": 4, "CIANWOOD_CITY": 4, "ROUTE_42": 4, "MAHOGANY_TOWN": 4, "ROUTE_43": 4,
    "LAKE_OF_RAGE": 4,
    "ROUTE_44": 7, "BLACKTHORN_CITY": 7, "ROUTE_45": 7,
    "ROUTE_27": 8, "ROUTE_26": 8,
}
SINNOH_REACH = {
    "TWINLEAF_TOWN": 0, "VERITY_LAKEFRONT": 0, "ROUTE_201": 0, "SANDGEM_TOWN": 0, "ROUTE_202": 0,
    "JUBILIFE_CITY": 0, "ROUTE_203": 0, "OREBURGH_CITY": 0, "ROUTE_207": 2,
    "ROUTE_204_SOUTH": 1, "ROUTE_204_NORTH": 1, "FLOAROMA_TOWN": 1, "VALLEY_WINDWORKS_OUTSIDE": 1,
    "ROUTE_205_SOUTH": 1, "ETERNA_FOREST_OUTSIDE": 1, "ROUTE_205_NORTH": 1, "ETERNA_CITY": 1,
    "ROUTE_211_WEST": 2, "ROUTE_206": 2, "ROUTE_208": 2, "HEARTHOME_CITY": 2, "ROUTE_212_NORTH": 2,
    "ROUTE_209": 3, "SOLACEON_TOWN": 3, "ROUTE_210_SOUTH": 3, "ROUTE_215": 3, "VEILSTONE_CITY": 3,
    "ROUTE_214": 4, "VALOR_LAKEFRONT": 4, "ROUTE_213": 4, "PASTORIA_CITY": 4, "ROUTE_212_SOUTH": 4,
    "ROUTE_210_NORTH": 5, "CELESTIC_TOWN": 5, "ROUTE_211_EAST": 5, "ROUTE_218": 5, "CANALAVE_CITY": 5,
    "ROUTE_219": 5, "ROUTE_220": 5, "ROUTE_221": 5, "FUEGO_IRONWORKS_OUTSIDE": 5,
    "IRON_ISLAND": 6, "ROUTE_216": 6, "ROUTE_217": 6, "ACUITY_LAKEFRONT": 6, "SNOWPOINT_CITY": 6,
    "ROUTE_222": 7, "SUNYSHORE_CITY": 7,
    "ROUTE_223": 8, "POKEMON_LEAGUE": 8,
}

SMALL = {"OF", "THE"}


def pretty(name: str) -> str:
    name = re.sub(r"_(NORTH|SOUTH|EAST|WEST|OUTSIDE)$", "", name)
    words = name.split("_")
    out = []
    for w in words:
        if w.isdigit():
            out.append(w)
        elif w in SMALL and out:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    s = " ".join(out)
    return s.replace("Pokemon", "Pokémon").replace("Ho Oh", "Ho-Oh")


def thin(cands, limit=PER_MAP):
    """Spread-out subset: at least MIN_GAP squares apart (Chebyshev), then an even sample of at most `limit`."""
    chosen = []
    taken = set()
    for (x, y) in sorted(cands, key=lambda p: (p[1], p[0])):
        if any((x + dx, y + dy) in taken for dx in range(-MIN_GAP + 1, MIN_GAP) for dy in range(-MIN_GAP + 1, MIN_GAP)):
            continue
        chosen.append((x, y))
        taken.add((x, y))
    if len(chosen) > limit:
        step = len(chosen) / limit
        chosen = [chosen[int(i * step)] for i in range(limit)]
    return [list(p) for p in chosen]


def good_squares(W, H, ok, reach, blocked, edge=EDGE):
    """Squares whose 3x3 neighbourhood is all `ok`, reachable, away from `blocked` and the edge."""
    out = []
    for (x, y) in reach:
        if not (edge <= x < W - edge and edge <= y < H - edge):
            continue
        if (x, y) in blocked:
            continue
        if all(ok(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
            out.append((x, y))
    return out


def around(points, r):
    return {(x + dx, y + dy) for (x, y) in points for dx in range(-r, r + 1) for dy in range(-r, r + 1)}


def flood(W, H, walk, seeds):
    seen = set()
    q = deque((x, y) for (x, y) in seeds if 0 <= x < W and 0 <= y < H and walk(x, y))
    seen.update(q)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen and walk(nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


def edge_seeds(W, H, conns, warps):
    seeds = []
    for d in conns:
        if d == "north":
            seeds += [(x, 0) for x in range(W)]
        elif d == "south":
            seeds += [(x, H - 1) for x in range(W)]
        elif d == "west":
            seeds += [(0, y) for y in range(H)]
        elif d == "east":
            seeds += [(W - 1, y) for y in range(H)]
    for (x, y) in warps:
        seeds += [(x, y + 1), (x, y - 1), (x + 1, y), (x - 1, y)]
    return seeds


# --- Kanto (pokeyellow) ----------------------------------------------------------------------------------------
# Cycling Road (bike only): Route 17 is left out; on Routes 16 and 18 the flood fill doesn't start from the Route 17
# edge or from the gate doors that lead onto the cycling side (pret data/maps/objects/Route16.asm / Route18.asm), so
# their bike-only western parts get no dens.
KANTO_NO_SEEDS = {"ROUTE_16": ({"south"}, {(17, 10), (17, 11)}), "ROUTE_18": ({"north"}, {(33, 8), (33, 9)})}


def kanto():
    def read(p):
        with open(os.path.join(PRET_Y, p), encoding="utf-8") as f:
            return f.read()

    consts, idx = {}, 0
    for line in read("constants/map_constants.asm").splitlines():
        m = re.match(r"\s*map_const\s+(\w+),\s*(\d+),\s*(\d+)", line)
        if m:
            consts[m.group(1)] = (idx, int(m.group(2)), int(m.group(3)))
            idx += 1
    colls, names = {}, []
    for line in read("data/tilesets/collision_tile_ids.asm").splitlines():
        m = re.match(r"(\w+)_Coll::", line)
        if m:
            names.append(m.group(1))
            continue
        m = re.match(r"\s*coll_tiles\s+(.*)", line)
        if m and names:
            tiles = {int(t.strip().lstrip("$"), 16) for t in m.group(1).split(",") if t.strip()}
            for n in names:
                colls[n] = tiles
            names = []
    bsets = {"OVERWORLD": open(os.path.join(PRET_Y, "gfx/blocksets/overworld.bst"), "rb").read(),
             "PLATEAU": open(os.path.join(PRET_Y, "gfx/blocksets/plateau.bst"), "rb").read()}
    maps = {}
    for fn in sorted(os.listdir(os.path.join(PRET_Y, "data/maps/headers"))):
        txt = read("data/maps/headers/" + fn)
        m = re.search(r"map_header\s+(\w+),\s*(\w+),\s*(\w+)", txt)
        if not m or m.group(3) not in ("OVERWORLD", "PLATEAU"):
            continue
        name, const, ts = m.groups()
        if const not in KANTO_REACH:
            continue
        conns = re.findall(r"connection\s+(\w+)", txt)
        blk = open(os.path.join(PRET_Y, "maps", f"{name}.blk"), "rb").read()
        mid, w, h = consts[const]
        bst = bsets[ts]
        passable = colls["Overworld" if ts == "OVERWORLD" else "Plateau"]
        grass = 0x52 if ts == "OVERWORLD" else None
        W, H = 2 * w, 2 * h

        def tile(sx, sy, row=1, col=0, blk=blk, w=w, bst=bst):
            b = blk[(sy // 2) * w + sx // 2]
            return bst[b * 16 + ((sy % 2) * 2 + row) * 4 + (sx % 2) * 2 + col]

        def walk(x, y, W=W, H=H, passable=passable, tile=tile):
            return 0 <= x < W and 0 <= y < H and tile(x, y) in passable

        def ground(x, y, walk=walk, tile=tile, grass=grass):
            return walk(x, y) and tile(x, y) != grass

        obj = read(f"data/maps/objects/{name}.asm")
        warps = [(int(a), int(b)) for a, b in re.findall(r"warp_event\s+(\d+),\s*(\d+)", obj)]
        signs = [(int(a), int(b)) for a, b in re.findall(r"bg_event\s+(\d+),\s*(\d+)", obj)]
        npcs = [(int(a), int(b)) for a, b in re.findall(r"object_event\s+(\d+),\s*(\d+)", obj)]
        no_conn, no_warps = KANTO_NO_SEEDS.get(const, (set(), set()))
        reach = flood(W, H, walk, edge_seeds(W, H, [c for c in conns if c not in no_conn],
                                              [w for w in warps if w not in no_warps]))
        blocked = around(warps + signs + npcs, NEAR)
        spots = thin(good_squares(W, H, ground, reach, blocked))
        if spots:
            maps[const] = {"map": mid, "name": pretty(const), "reach": KANTO_REACH[const], "spots": spots}
    return maps


# --- Johto (pokecrystal) ---------------------------------------------------------------------------------------
def johto():
    def read(p):
        with open(os.path.join(PRET_C, p), encoding="utf-8") as f:
            return f.read()

    consts = {}
    group = 0
    num = 0
    for line in read("constants/map_constants.asm").splitlines():
        if re.match(r"\s*newgroup\s+\w+", line):
            group += 1
            num = 0
            continue
        m = re.match(r"\s*map_const\s+(\w+),\s*(\d+),\s*(\d+)", line)
        if m:
            num += 1
            consts[m.group(1)] = ((group << 8) | num, int(m.group(2)), int(m.group(3)))
    cvals = {}
    for line in read("constants/collision_constants.asm").splitlines():
        m = re.match(r"DEF\s+COLL_(\w+)\s+EQU\s+\$([0-9a-fA-F]+)", line)
        if m:
            cvals[m.group(1)] = int(m.group(2), 16)

    def coll_table(tsname):
        out = []
        for line in read(f"data/tilesets/{tsname}_collision.asm").splitlines():
            m = re.match(r"\s*tilecoll\s+(.*?)\s*(;.*)?$", line)
            if not m:
                continue
            vals = []
            for t in m.group(1).split(","):
                t = t.strip()
                vals.append(cvals[t] if t in cvals else int(t.lstrip("$"), 16))
            out.append(vals)
        return out

    tables = {"TILESET_JOHTO": coll_table("johto"), "TILESET_JOHTO_MODERN": coll_table("johto_modern")}
    attrs = read("data/maps/attributes.asm")
    conns_of = {}
    cur = None
    for line in attrs.splitlines():
        m = re.match(r"\s*map_attributes\s+(\w+),\s*(\w+)", line)
        if m:
            cur = m.group(2)
            conns_of[cur] = []
            continue
        m = re.match(r"\s*connection\s+(\w+)", line)
        if m and cur:
            conns_of[cur].append(m.group(1))
    name_of = {}
    for line in attrs.splitlines():
        m = re.match(r"\s*map_attributes\s+(\w+),\s*(\w+)", line)
        if m:
            name_of[m.group(2)] = m.group(1)
    maps = {}
    for line in read("data/maps/maps.asm").splitlines():
        m = re.match(r"\s*map\s+(\w+),\s*(\w+),\s*(\w+),\s*(\w+)", line)
        if not m:
            continue
        mname, ts, env, _lm = m.groups()
        if ts not in tables or env not in ("TOWN", "ROUTE"):
            continue
        const = next((c for c, n in name_of.items() if n == mname), None)
        if const is None or const not in JOHTO_REACH:
            continue
        mid, w, h = consts[const]
        blk = open(os.path.join(PRET_C, "maps", f"{mname}.blk"), "rb").read()
        coll = tables[ts]
        W, H = 2 * w, 2 * h

        def cval(x, y, blk=blk, w=w, coll=coll):
            b = blk[(y // 2) * w + x // 2]
            return coll[b][(y % 2) * 2 + (x % 2)] if b < len(coll) else 0xFF

        def walk(x, y, W=W, H=H, cval=cval):
            # walkable for the flood fill: floor, grass (you can walk through it), not water/walls/ledges/doors
            return 0 <= x < W and 0 <= y < H and cval(x, y) in (0x00, 0x18, 0x10, 0x14, 0x1C)

        def ground(x, y, W=W, H=H, cval=cval):
            return 0 <= x < W and 0 <= y < H and cval(x, y) == 0x00

        obj = read(f"maps/{mname}.asm")
        warps = [(int(a), int(b)) for a, b in re.findall(r"warp_event\s+(\d+),\s*(\d+)", obj)]
        signs = [(int(a), int(b)) for a, b in re.findall(r"bg_event\s+(\d+),\s*(\d+)", obj)]
        trig = [(int(a), int(b)) for a, b in re.findall(r"coord_event\s+(\d+),\s*(\d+)", obj)]
        npcs = [(int(a), int(b)) for a, b in re.findall(r"object_event\s+(\d+),\s*(\d+)", obj)]
        reach = flood(W, H, walk, edge_seeds(W, H, conns_of.get(const, []), warps))
        blocked = around(warps + signs + trig + npcs, NEAR)
        spots = thin(good_squares(W, H, ground, reach, blocked))
        if spots:
            maps[const] = {"map": mid, "name": pretty(const), "reach": JOHTO_REACH[const], "spots": spots}
    return maps


# --- Sinnoh (pokeplatinum) -------------------------------------------------------------------------------------
# ground a den can stand on (no wild encounters there: pret src/map_tile_behavior.c sTileBehaviorFlags):
# NONE, MOUNTAIN_FLOOR, SAND, SNOW_DEEP, SNOW_SHALLOW, SNOW_WITH_SHADOWS
GROUND_BEHAVIOURS = {0x00, 0x0C, 0x21, 0xA1, 0xA8, 0xA9}
# walkable for the flood fill: + tall grass, deeper snow, mud
WALK_BEHAVIOURS = GROUND_BEHAVIOURS | {0x02, 0x03, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7}


def sinnoh():
    mat = json.load(open(os.path.join(PRET_P, "res/field/matrices/map_matrix_000.json")))
    headers = [l.strip() for l in open(os.path.join(PRET_P, "generated/map_headers.txt")) if l.strip()]
    hid = {n: i for i, n in enumerate(headers)}
    rows = len(mat["maps"])
    cols = len(mat["maps"][0])
    W, H = cols * 32, rows * 32
    attr = {}                                   # (x, y) global -> u16
    head = {}                                   # (cx, cy) -> header name
    for cy in range(rows):
        for cx in range(cols):
            m = mat["maps"][cy][cx]
            if m == "MAP_NONE":
                continue
            n = int(m.split("_")[1])
            raw = open(os.path.join(PRET_P, "res/field/maps/data/map_data_%03d.bin" % n), "rb").read()
            size = int.from_bytes(raw[0:4], "little")
            perms = raw[16:16 + size]
            for i in range(min(len(perms) // 2, 1024)):
                v = perms[2 * i] | perms[2 * i + 1] << 8
                attr[(cx * 32 + i % 32, cy * 32 + i // 32)] = v
            head[(cx, cy)] = mat["headers"][cy][cx]

    def beh(x, y):
        v = attr.get((x, y))
        return None if v is None else v & 0xFF

    def free(x, y):
        v = attr.get((x, y))
        return v is not None and not (v >> 15) & 1

    objects, warps, triggers, signs = set(), [], set(), set()
    for name in set(head.values()):
        if not name.startswith("MAP_HEADER_") or name == "MAP_HEADER_EVERYWHERE":
            continue
        p = os.path.join(PRET_P, "res/field/events/events_%s.json" % name[len("MAP_HEADER_"):].lower())
        if not os.path.exists(p):
            continue
        ev = json.load(open(p))
        for o in ev.get("object_events", []):
            objects.add((o["x"], o["z"]))
        for wv in ev.get("warp_events", []):
            warps.append((wv["x"], wv["z"]))
        for c in ev.get("coord_events", []):
            for dx in range(c.get("width", 1)):
                for dz in range(c.get("length", 1)):
                    triggers.add((c["x"] + dx, c["z"] + dz))
        for b in ev.get("bg_events", []):
            signs.add((b["x"], b["z"]))

    def walk(x, y):
        return free(x, y) and beh(x, y) in WALK_BEHAVIOURS and (x, y) not in objects

    seeds = []
    for (x, y) in warps:
        seeds += [(x, y + 1), (x, y - 1), (x + 1, y), (x - 1, y)]
    reach = flood(W, H, walk, seeds)

    def ground(x, y):
        return free(x, y) and beh(x, y) in GROUND_BEHAVIOURS and (x, y) not in objects

    blocked = around(list(objects) + warps + list(triggers) + list(signs), NEAR)
    cands = good_squares(W, H, ground, reach, blocked, edge=1)
    by_head = {}
    for (x, y) in cands:
        name = head.get((x // 32, y // 32), "")
        short = name[len("MAP_HEADER_"):]
        if short not in SINNOH_REACH:
            continue
        by_head.setdefault(short, []).append((x, y))
    maps = {}
    for short, pts in sorted(by_head.items()):
        spots = thin(pts)
        if spots:
            maps[short] = {"map": hid["MAP_HEADER_" + short], "name": pretty(short), "reach": SINNOH_REACH[short],
                           "spots": spots}
    return maps


def main():
    out = OUT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    global PER_MAP
    if "--per-map" in sys.argv:
        PER_MAP = int(sys.argv[sys.argv.index("--per-map") + 1])
    data = {"_about": "Raid den spots generated by dev/raids/gen_spots.py from the pret decomps' map data "
                      "(coordinates only). reach = gym badges needed to walk there.",
            "kanto": {"game": "yellow", "maps": kanto()},
            "johto": {"game": "crystal", "maps": johto()},
            "sinnoh": {"game": "platinum", "maps": sinnoh()}}
    for region in ("kanto", "johto", "sinnoh"):
        maps = data[region]["maps"]
        table = {"kanto": KANTO_REACH, "johto": JOHTO_REACH, "sinnoh": SINNOH_REACH}[region]
        missing = sorted(set(table) - set(maps))
        print("%-7s %3d maps, %5d spots%s" % (region, len(maps), sum(len(m["spots"]) for m in maps.values()),
                                              ("; no spots on: " + ", ".join(missing)) if missing else ""))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    print("wrote", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
