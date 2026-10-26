"""Raid den spots for Kanto (Pokémon Yellow), generated from pret/pokeyellow map data. Throwaway prototype of the
placement generator (DESIGN.md "Where raids appear"). Output = coordinates only (map id, x, y), no game data.

A spot is a 16x16 step square on an outdoor map (tileset OVERWORLD / PLATEAU) that
  * is walkable: the bottom-left 8x8 tile of the square is in the tileset's passable list (Gen 1 collision rule)
  * is not tall grass, not a warp / sign / NPC square (NPC: its square + 1 around it), not next to a door warp
  * can be reached on foot inside the map from where you enter it (flood fill from the map edges that connect
    to another map and from the warps), so islands behind water / Cut trees / ledges-only areas drop out
  * has a free walkable square below it (the player stands there facing up to talk to the den)
Run: python dev/raids/proto/yellow_spots.py [--json out.json]
"""
import json
import os
import re
import sys
from collections import deque

PRET = os.environ.get("PRET_YELLOW", "/home/claude/pokeyellow")


def read(p):
    with open(os.path.join(PRET, p), encoding="utf-8") as f:
        return f.read()


def map_consts():
    out, idx = {}, 0
    for line in read("constants/map_constants.asm").splitlines():
        m = re.match(r"\s*map_const\s+(\w+),\s*(\d+),\s*(\d+)", line)
        if m:
            out[m.group(1)] = (idx, int(m.group(2)), int(m.group(3)))
            idx += 1
    return out


def coll_tiles():
    out, names = {}, []
    for line in read("data/tilesets/collision_tile_ids.asm").splitlines():
        m = re.match(r"(\w+)_Coll::", line)
        if m:
            names.append(m.group(1))
            continue
        m = re.match(r"\s*coll_tiles\s+(.*)", line)
        if m and names:
            tiles = {int(t.strip().lstrip("$"), 16) for t in m.group(1).split(",") if t.strip()}
            for n in names:
                out[n] = tiles
            names = []
    return out


def parse_objects(name):
    txt = read(f"data/maps/objects/{name}.asm")
    warps = [(int(a), int(b)) for a, b in re.findall(r"warp_event\s+(\d+),\s*(\d+)", txt)]
    signs = [(int(a), int(b)) for a, b in re.findall(r"bg_event\s+(\d+),\s*(\d+)", txt)]
    npcs = [(int(a), int(b)) for a, b in re.findall(r"object_event\s+(\d+),\s*(\d+)", txt)]
    return warps, signs, npcs


def spots_for(name, const, tileset, conns, consts, colls, blocksets):
    _, w, h = consts[const]
    blk = open(os.path.join(PRET, "maps", f"{name}.blk"), "rb").read()
    bst = blocksets[tileset]
    passable = colls["Overworld" if tileset == "OVERWORLD" else "Plateau"]
    grass = 0x52 if tileset == "OVERWORLD" else None
    W, H = w * 2, h * 2

    def tile(sx, sy, row, col):
        b = blk[(sy // 2) * w + sx // 2]
        return bst[b * 16 + ((sy % 2) * 2 + row) * 4 + (sx % 2) * 2 + col]

    walk = [[tile(x, y, 1, 0) in passable for x in range(W)] for y in range(H)]
    is_grass = [[grass is not None and tile(x, y, 1, 0) == grass for x in range(W)] for y in range(H)]
    warps, signs, npcs = parse_objects(name)
    blocked = set(warps) | set(signs)
    for (x, y) in npcs:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                blocked.add((x + dx, y + dy))
    near_warp = {(x + dx, y + dy) for (x, y) in warps for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    # flood fill from connected edges + warps
    seeds = []
    for d in conns:
        if d == "north":
            seeds += [(x, 0) for x in range(W)]
        if d == "south":
            seeds += [(x, H - 1) for x in range(W)]
        if d == "west":
            seeds += [(0, y) for y in range(H)]
        if d == "east":
            seeds += [(W - 1, y) for y in range(H)]
    seeds += [(x, y + 1) for (x, y) in warps]
    seen = set()
    q = deque((x, y) for (x, y) in seeds if 0 <= x < W and 0 <= y < H and walk[y][x])
    seen.update(q)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and walk[ny][nx] and (nx, ny) not in seen:
                seen.add((nx, ny))
                q.append((nx, ny))
    out = []
    for (x, y) in sorted(seen):
        if is_grass[y][x] or (x, y) in blocked or (x, y) in near_warp:
            continue
        if not (y + 1 < H and (x, y + 1) in seen and not is_grass[y + 1][x] and (x, y + 1) not in blocked):
            continue
        out.append((x, y))
    return out, len(seen), W * H


def main():
    consts = map_consts()
    colls = coll_tiles()
    blocksets = {"OVERWORLD": open(os.path.join(PRET, "gfx/blocksets/overworld.bst"), "rb").read(),
                 "PLATEAU": open(os.path.join(PRET, "gfx/blocksets/plateau.bst"), "rb").read()}
    result = {}
    for fn in sorted(os.listdir(os.path.join(PRET, "data/maps/headers"))):
        txt = read("data/maps/headers/" + fn)
        m = re.search(r"map_header\s+(\w+),\s*(\w+),\s*(\w+)", txt)
        if not m or m.group(3) not in ("OVERWORLD", "PLATEAU"):
            continue
        name, const, ts = m.groups()
        conns = re.findall(r"connection\s+(\w+)", txt)
        if not os.path.exists(os.path.join(PRET, "maps", f"{name}.blk")):
            continue
        spots, reach, total = spots_for(name, const, ts, conns, consts, colls, blocksets)
        result[name] = {"map": consts[const][0], "spots": spots, "reachable": reach, "squares": total}
    tot = sum(len(v["spots"]) for v in result.values())
    print("%d outdoor maps, %d candidate den spots" % (len(result), tot))
    for k, v in sorted(result.items(), key=lambda kv: kv[1]["map"]):
        print("  %-16s map %02X: %4d spots (%d reachable squares of %d)" % (k, v["map"], len(v["spots"]),
                                                                           v["reachable"], v["squares"]))
    if "--json" in sys.argv:
        json.dump({k: v["spots"] for k, v in result.items()}, open(sys.argv[sys.argv.index("--json") + 1], "w"))
    return result


if __name__ == "__main__":
    main()
