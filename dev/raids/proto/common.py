"""Shared helpers for the raid prototypes (throwaway; see dev/raids/DESIGN.md).

Paths: ROMs + states from dev/states (YL_STATES), pret symbol files from /home/claude/pret-syms (YL_SYMS).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STATES = os.environ.get("YL_STATES", os.path.join(REPO, "dev", "states"))
SYMS = os.environ.get("YL_SYMS", "/home/claude/pret-syms")
OUT = os.environ.get("YL_RAID_OUT", "/tmp/yl-raid-proto")
sys.path.insert(0, os.path.join(REPO, "client"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.makedirs(OUT, exist_ok=True)

FAILS = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)
    return ok


def info(msg):
    print("     " + msg)


def done():
    print("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
    return 0 if not FAILS else 1


_sym_cache = {}


def syms(game):
    """{label: (bank, addr)} from pret's .sym file (pokeyellow / pokecrystal)."""
    if game not in _sym_cache:
        d = {}
        with open(os.path.join(SYMS, f"poke{game}.sym"), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(";"):
                    continue
                try:
                    loc, name = line.split(None, 1)
                    b, a = loc.split(":")
                    d[name.strip()] = (int(b, 16), int(a, 16))
                except ValueError:
                    continue
        _sym_cache[game] = d
    return _sym_cache[game]


def sym(game, name):
    return syms(game)[name]
