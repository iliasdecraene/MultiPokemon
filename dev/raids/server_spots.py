"""Vendor the raid den spots into the server (SPEC.md section 12).

The server's Docker image only contains server/, so it can't read the client's spot file
(client/yellowlink/raid_spots.json, made by raid-games' dev/raids/gen_spots.py). This writes a deterministic sample of
it - at most PER_MAP spots per map, evenly spread over the map's sorted list - to
server/yellowlink_server/raid_spots.json in the server's shape:
    {"version": 1, "regions": {region: {"names": {map: name}, "spots": [[map, x, y, reach], ...]}}}
Coordinates only (our own generated data, no ROM bytes). client/tests/test_raid_net.py checks the copy is a subset of
the client data with the same names and reach, so run this again whenever the client file changes:
    python dev/raids/server_spots.py
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(REPO, "client", "yellowlink", "raid_spots.json")
DST = os.path.join(REPO, "server", "yellowlink_server", "raid_spots.json")
PER_MAP = 40


def sample(points: list, n: int) -> list:
    pts = sorted(points)
    if len(pts) <= n:
        return pts
    return [pts[(i * len(pts)) // n] for i in range(n)]


def convert(client: dict) -> dict:
    regions = {}
    for region in sorted(client):
        maps = (client[region] or {}).get("maps") or {}
        names, spots = {}, []
        for m in sorted(maps.values(), key=lambda v: v["map"]):
            if not m.get("spots"):
                continue
            names[str(m["map"])] = m["name"]
            for x, y in sample([tuple(p) for p in m["spots"]], PER_MAP):
                spots.append([int(m["map"]), int(x), int(y), int(m["reach"])])
        if spots:
            regions[region] = {"names": names, "spots": spots}
    return {"version": 1, "regions": regions}


def main() -> int:
    with open(SRC, encoding="utf-8") as fh:
        client = json.load(fh)
    out = convert(client)
    with open(DST, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
        fh.write("\n")
    for region, r in out["regions"].items():
        print(f"{region}: {len(r['names'])} maps, {len(r['spots'])} spots")
    print("written", DST, os.path.getsize(DST), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
