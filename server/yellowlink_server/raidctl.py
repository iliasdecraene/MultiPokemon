"""Admin command for raids (SPEC.md section 12, "Admin raids"): open a raid right now, or list what can be chosen.

Runs inside the server's container, started by the host's `yl-raid` script (server/tools/yl-raid):
    docker exec <container> python -m yellowlink_server.raidctl [--region R] [--place P] [--pokemon X]
        [--shiny yes|no|random] [--minutes N] [--replace] [--label NAME]
    docker exec <container> python -m yellowlink_server.raidctl --list [--region R]

It checks the choices against this server's own data (regions, places, raid pools), writes the request into
<data_dir>/admin/raid-<id>.json, waits for the running server to answer (raid-<id>.result.json, the raid loop looks
every second) and prints one plain sentence. No network port, nothing for the clients to know.
Exit codes: 0 done, 1 the server said no / didn't answer, 2 a wrong choice (nothing was sent).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import secrets
import sys
import tempfile
import time

from .config import load_config
from .raids import (FORCED_MINUTES_MAX, POOLS, REGION_NAMES, REGIONS, SPECIES_NAMES, SPOTS_FILE,
                    load_spots, norm)

RANDOM = {"", "random", "any", "rnd", "?", "*"}
GAME_TITLES = {"kanto": "Pokémon Yellow", "johto": "Pokémon Crystal", "sinnoh": "Pokémon Platinum"}
REGION_ALIASES = {"kanto": "kanto", "johto": "johto", "sinnoh": "sinnoh",
                  "yellow": "kanto", "crystal": "johto", "platinum": "sinnoh"}
YES = {"yes", "y", "true", "1", "on", "shiny"}
NO = {"no", "n", "false", "0", "off"}
WAIT = 10.0                      # seconds to wait for the server to take the request
WAIT_ANSWER = 10.0               # ... and then for its answer


class Refused(Exception):
    """A wrong choice: the message is printed as is (exit code 2)."""


# ----------------------------------------------------------------------------------------------- helpers
def _is_random(value) -> bool:
    return value is None or str(value).strip().lower() in RANDOM


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _or(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " or " + items[-1]


def _suggest(text: str, names) -> str:
    """' Did you mean Route 210 or Route 211?' (or '')."""
    by_key = {}
    for n in names:
        by_key.setdefault(norm(n), n)
    hits = difflib.get_close_matches(norm(text), list(by_key), n=3, cutoff=0.6)
    return f" Did you mean {_or(by_key[h] for h in hits)}?" if hits else ""


def _place_key(name: str):
    """Towns first (A-Z), then routes by number."""
    parts = name.split()
    if len(parts) == 2 and parts[0] == "Route" and parts[1].isdigit():
        return (1, int(parts[1]), name)
    return (0, 0, name)


def places(spots: dict, region: str) -> list:
    """The region's place names (one per name, though some places have several maps), towns first then routes."""
    names = {n for n in (spots.get(region) or {}).get("names", {}).values()}
    return sorted(names, key=_place_key)


def pool_names(region: str) -> list:
    return [SPECIES_NAMES[s] for s in POOLS[region]]


def resolve_region(text) -> str | None:
    if _is_random(text):
        return None
    key = norm(text)
    if key in REGION_ALIASES:
        return REGION_ALIASES[key]
    raise Refused(f"There's no region called '{text}'. Choose kanto, johto or sinnoh (or random)."
                  + _suggest(text, REGIONS))


def resolve_species(text) -> int | None:
    if _is_random(text):
        return None
    raw = str(text).strip().lstrip("#")
    if raw.isdigit():
        dex = int(raw)
        if dex in SPECIES_NAMES:
            return dex
        raise Refused(f"#{dex} isn't a raid Pokémon. Raid Pokémon: {_and(_all_bosses())}.")
    key = norm(raw)
    for dex, name in SPECIES_NAMES.items():
        if norm(name) == key:
            return dex
    raise Refused(f"'{text}' isn't a raid Pokémon." + _suggest(raw, SPECIES_NAMES.values())
                  + " See them all with: sudo yl-raid --list")


def _all_bosses() -> list:
    seen = []
    for region in REGIONS:
        for s in POOLS[region]:
            if SPECIES_NAMES[s] not in seen:
                seen.append(SPECIES_NAMES[s])
    return seen


def _find_place(spots: dict, region: str, text: str) -> str | None:
    """The place's own name in that region (loose match; a bare number = that route), or None."""
    key = norm(text)
    if key.isdigit():
        key = "route" + key
    for name in places(spots, region):
        if norm(name) == key:
            return name
    return None


def resolve(args, spots: dict, default_minutes: float) -> dict:
    """The request for the server from the command's choices, or Refused with a plain explanation."""
    region = resolve_region(args.region)
    species = resolve_species(args.pokemon)
    have = [r for r in REGIONS if r in spots]
    if region is not None and region not in spots:
        raise Refused(f"{REGION_NAMES[region]} has no raid places on this server (its spot data is missing).")
    sname = SPECIES_NAMES.get(species, "")
    homes = [r for r in have if species in POOLS[r]] if species is not None else have
    if species is not None and not homes:
        raise Refused(f"{sname} has no raid places on this server.")
    if region is not None and species is not None and species not in POOLS[region]:
        raise Refused(f"{sname} isn't one of {REGION_NAMES[region]}'s raid Pokémon. {REGION_NAMES[region]}: "
                      f"{_and(pool_names(region))}. {sname} can appear in {_or(REGION_NAMES[r] for r in homes)}.")

    place = None
    if not _is_random(args.place):
        text = str(args.place).strip()
        found = {r: _find_place(spots, r, text) for r in have}
        found = {r: n for r, n in found.items() if n}
        if region is not None:
            if region not in found:
                elsewhere = ""
                if found:
                    r2, n2 = next(iter(found.items()))
                    elsewhere = f" {n2} is in {REGION_NAMES[r2]}."
                raise Refused(f"'{text}' isn't a raid place in {REGION_NAMES[region]}.{elsewhere}"
                              + ("" if elsewhere else _suggest(text, places(spots, region)))
                              + f" See the places with: sudo yl-raid --list --region {region}")
            place = found[region]
        else:
            if not found:
                raise Refused(f"'{text}' isn't a raid place in any region."
                              + _suggest(text, [n for r in have for n in places(spots, r)])
                              + " See the places with: sudo yl-raid --list")
            fits = [r for r in found if species is None or species in POOLS[r]]
            if not fits:
                r2 = next(iter(found))
                raise Refused(f"{found[r2]} is in {REGION_NAMES[r2]}, but {sname} isn't one of "
                              f"{REGION_NAMES[r2]}'s raid Pokémon. {sname} can appear in "
                              f"{_or(REGION_NAMES[r] for r in homes)}.")
            place = found[fits[0]]
            homes = fits                      # place names are unique across regions, but stay general
    regions = [region] if region is not None else homes

    shiny = None
    if not _is_random(args.shiny):
        v = str(args.shiny).strip().lower()
        if v in YES:
            shiny = True
        elif v in NO:
            shiny = False
        else:
            raise Refused(f"--shiny must be yes, no or random (not '{args.shiny}').")

    minutes = default_minutes
    if args.minutes is not None:
        try:
            minutes = float(str(args.minutes).strip().rstrip("m").strip())
        except ValueError:
            raise Refused(f"--minutes must be a number (not '{args.minutes}').") from None
        if not 1 <= minutes <= FORCED_MINUTES_MAX:
            raise Refused(f"--minutes must be between 1 and {FORCED_MINUTES_MAX}.")
        minutes = int(minutes) if minutes == int(minutes) else minutes
    return {"action": "spawn", "regions": regions, "species": species, "place": place, "shiny": shiny,
            "minutes": minutes, "replace": bool(args.replace)}


def _wrap_items(head: str, items: list, width: int) -> list:
    """'head a, b, c,' lines no wider than `width`, never breaking inside an item ('Route 210', 'Mew (151)')."""
    lines, line = [], head
    for i, item in enumerate(items):
        piece = item + ("," if i < len(items) - 1 else "")
        if line.strip() and line != head and len(line) + 1 + len(piece) > width:
            lines.append(line)
            line = " " * len(head) + piece
        else:
            line += ("" if line == head else " ") + piece
    lines.append(line)
    return lines


def format_list(spots: dict, region: str | None = None, width: int = 100) -> str:
    """Human-friendly overview: per region its game, raid Pokémon (with dex numbers) and places."""
    out = []
    for r in ([region] if region else REGIONS):
        if r not in spots:
            out.append(f"{REGION_NAMES[r].upper()} - no raid places on this server.")
            out.append("")
            continue
        mons = [f"{SPECIES_NAMES[s]} ({s})" for s in POOLS[r]]
        pl = places(spots, r)
        out.append(f"{REGION_NAMES[r].upper()}  ({GAME_TITLES[r]}) - {len(mons)} Pokémon, {len(pl)} places")
        out += _wrap_items("  Pokémon: ", mons, width)
        out += _wrap_items("  Places:  ", pl, width)
        out.append("")
    out.append("Names don't care about upper/lower case; a Pokémon can also be its number (e.g. --pokemon 384).")
    out.append('Example: sudo yl-raid --region sinnoh --pokemon rayquaza --place "Route 210"')
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- talking to the server
def submit(admin_dir: str, request: dict, wait: float = WAIT, wait_answer: float = WAIT_ANSWER,
           poll: float = 0.1) -> dict | None:
    """Write the request, wait for the running server's answer. None = the server didn't take it in time
    (the request is withdrawn, nothing happens later)."""
    os.makedirs(admin_dir, exist_ok=True)
    rid = f"{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    path = os.path.join(admin_dir, f"raid-{rid}.json")
    result = os.path.join(admin_dir, f"raid-{rid}.result.json")
    request = dict(request, version=1, created=time.time())
    fd, tmp = tempfile.mkstemp(prefix=".request-", suffix=".tmp", dir=admin_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(request, fh)
    os.replace(tmp, path)

    def read_result():
        try:
            with open(result, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        try:
            os.unlink(result)
        except OSError:
            pass
        return data

    end = time.monotonic() + wait
    while time.monotonic() < end:
        got = read_result()
        if got is not None:
            return got
        time.sleep(poll)
    try:
        os.unlink(path)                       # withdraw it: the server never took it
        return None
    except FileNotFoundError:
        pass                                  # the server took it just now: wait for the answer
    end = time.monotonic() + wait_answer
    while time.monotonic() < end:
        got = read_result()
        if got is not None:
            return got
        time.sleep(poll)
    return {"ok": False, "message": "The server took the request but didn't answer in time - check whether a raid "
                                    "opened (sudo docker logs --since 5m <container>)."}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yl-raid", description="Open a YellowLink raid right now, or list the choices.")
    p.add_argument("--region", help="kanto, johto, sinnoh or random (default: random)")
    p.add_argument("--place", help='a route or town of that region, e.g. "Route 210", or random (default)')
    p.add_argument("--pokemon", help="a raid Pokémon of that region (name or number), or random (default)")
    p.add_argument("--shiny", help="yes, no or random (default: random = the normal 1 in 20)")
    p.add_argument("--minutes", help="how long the raid stays open (default: the server's raid time, 15)")
    p.add_argument("--replace", action="store_true", help="end a raid that is still running first")
    p.add_argument("--list", action="store_true", help="show the regions, places and raid Pokémon")
    p.add_argument("--label", help="the server's name in the output (yl-raid passes it)")
    p.add_argument("--data-dir", help="the server's data folder (default: the server's own setting)")
    p.add_argument("--wait", type=float, default=WAIT, help=argparse.SUPPRESS)
    return p


def _config(env):
    path = env.get("YL_CONFIG")
    try:
        return load_config(path if path and os.path.isfile(path) else None, env=env)
    except (OSError, ValueError) as exc:
        print(f"Couldn't read the server's settings ({exc}).")
        return None


def main(argv=None, env=None) -> int:
    env = os.environ if env is None else env
    args = build_parser().parse_args(argv)
    cfg = _config(env)
    if cfg is None:
        return 1
    label = (args.label or cfg.name or "the server").strip()
    spots = load_spots(cfg.raid_spots or SPOTS_FILE)
    if args.list:
        try:
            region = resolve_region(args.region)
        except Refused as exc:
            print(exc)
            return 2
        print(format_list(spots, region))
        return 0
    if not cfg.raids:
        print(f"Raids are turned off on {label} (YL_RAIDS=off in its .env) - nothing was done.")
        return 1
    if not spots:
        print(f"{label} has no raid places (its spot data is missing) - nothing was done.")
        return 1
    try:
        request = resolve(args, spots, cfg.raid_open)
    except Refused as exc:
        print(f"{exc}\nNothing was changed on {label}.")
        return 2
    data_dir = args.data_dir or cfg.data_dir
    try:
        result = submit(os.path.join(data_dir, "admin"), request, wait=args.wait)
    except OSError as exc:
        print(f"Couldn't hand the request to {label} ({exc}). Does the server own its data folder? "
              "(sudo chown -R 1000:1000 data in its server folder)")
        return 1
    if result is None:
        print(f"{label} didn't pick up the request within {args.wait:g} seconds - nothing was done. Is it running and "
              "up to date? (sudo yl-update)")
        return 1
    if result.get("ok"):
        print(f"Raid opened on {label}: {result.get('message', '')}")
        return 0
    print(f"{label}: {result.get('message', 'the server said no.')}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
