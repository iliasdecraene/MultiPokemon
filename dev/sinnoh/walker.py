"""Walk the player somewhere on the real ROM (dev/test only): BFS over the map's tile collision bits (worldmap.py),
then one held direction per tile, checking the player's MapObject position after each step."""
from collections import deque

import worldmap

DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
FACE = {"up": 0, "down": 1, "left": 2, "right": 3}


def pos(pm):
    p = pm.position()
    return (p["x"], p["y"]) if p else None


def settle(d, pm, frames=120):
    for _ in range(frames):
        if pm.is_safe():
            return True
        d.run(1)
    return False


def step(d, pm, direction, tries=4):
    """Move one tile (or turn + move). True when the position changed."""
    before = pos(pm)
    for _ in range(tries):
        d.hold(direction, 10, 2)
        for _ in range(30):
            if not pm.walking():
                break
            d.run(1)
        if pos(pm) != before:
            return True
    return False


def face(d, pm, direction):
    """Turn on the spot (a short tap turns without stepping in Gen 4)."""
    for _ in range(3):
        if pm.position().get("facing") == FACE[direction]:
            return True
        d.hold(direction, 2, 10)
    return pm.position().get("facing") == FACE[direction]


def path(tiles, start, goal, avoid=()):
    q = deque([start])
    prev = {start: None}
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for name, (dx, dz) in DIRS.items():
            nxt = (cur[0] + dx, cur[1] + dz)
            a = tiles.get(nxt)
            if nxt in prev or a is None or nxt in avoid:
                continue
            if worldmap.blocked(a) and nxt != goal:
                continue
            prev[nxt] = (cur, name)
            q.append(nxt)
    if goal not in prev:
        return None
    out = []
    cur = goal
    while prev[cur] is not None:
        cur, name = prev[cur][0], (prev[cur][1])
        out.append(name)
    return out[::-1]


def walk_to(d, pm, tiles, goal, max_replans=6, map_change_ok=False):
    """Walk to tile `goal`. Returns True when there (or, with map_change_ok, when the map changed on the way)."""
    start_map = pm.map_id()
    avoid = set()
    for _ in range(max_replans):
        cur = pos(pm)
        if cur == goal:
            return True
        route = path(tiles, cur, goal, avoid)
        if route is None:
            return False
        for name in route:
            settle(d, pm)
            here = pos(pm)
            if not step(d, pm, name):
                dx, dz = DIRS[name]
                avoid.add((here[0] + dx, here[1] + dz))        # an object / something the tiles don't show
                break
            if pm.map_id() != start_map:
                return map_change_ok
        else:
            return pos(pm) == goal
    return pos(pm) == goal
