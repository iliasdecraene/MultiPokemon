"""Raid battles on the real ROMs, through the production modules (client/yellowlink/raid_gb.py, raid_pt.py).

    python dev/raids/verify.py                  # Yellow + Crystal + Platinum (YL_PT_ROM=vanilla|sinnoh)
    python dev/raids/verify.py yellow crystal   # only those games
    python dev/raids/verify.py platinum         # Platinum only (run once with YL_PT_ROM=sinnoh too)

Per game: start, override (moves/level/shiny), shared HP over several bars (damage adds up exactly, the boss never
faints), the 10-s timer (need_input / input_done every turn, auto_pick drives FIGHT + a move, "Use next Pokémon?"
+ the party after a faint), balls blocked while fighting, catch with forced success (natural moves, level, Pokédex,
shiny / fateful) and forced miss (the boss runs away), time over (flee), blackout, den drawing + collision + A.
Screenshots go to $YL_RAID_OUT (default: a temp folder; keep them out of git). Prints PASS/FAIL, exit 1 on failure.
Needs dev/states/ (ROMs + states, see dev/README.md) and the pret symbol files for the address check
(/home/claude/pret-syms or YL_SYMS).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(REPO, "dev", "states"))
SYMS = os.environ.get("YL_SYMS", "/home/claude/pret-syms")
OUT = os.environ.get("YL_RAID_OUT") or os.path.join(tempfile.gettempdir(), "yl-raid-verify")
sys.path.insert(0, os.path.join(REPO, "client"))
sys.path.insert(0, os.path.join(REPO, "dev", "platinum"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.makedirs(OUT, exist_ok=True)

FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        FAILS.append(label)
    return ok


def info(msg):
    print("     " + msg, flush=True)


def syms(game):
    d = {}
    path = os.path.join(SYMS, f"poke{game}.sym")
    if not os.path.exists(path):
        return d
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                loc, name = line.strip().split(None, 1)
                b, a = loc.split(":")
                d[name.strip()] = (int(b, 16), int(a, 16))
            except ValueError:
                continue
    return d


import gb_checks  # noqa: E402  (dev/raids/gb_checks.py)

if __name__ == "__main__":
    which = [a.lower() for a in sys.argv[1:]] or ["data", "yellow", "crystal", "platinum"]
    if "data" in which:
        import data_checks  # noqa: E402
        data_checks.run(check, info)
    for g in ("yellow", "crystal"):
        if g in which:
            gb_checks.run(g, check, info, syms(g), STATES, OUT)
    if "platinum" in which:
        import pt_checks  # noqa: E402
        pt_checks.run(check, info, STATES, OUT)
    print("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
    raise SystemExit(0 if not FAILS else 1)
