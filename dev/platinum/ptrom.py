"""Which Sinnoh ROM the Platinum dev scripts run (env YL_PT_ROM), and that ROM's fixtures + facts.

    YL_PT_ROM=vanilla  (default)  dev/states/platinum.nds, fixtures dev/states/pt_*      (Platinum US rev 0)
    YL_PT_ROM=defplat             dev/states/defplat.nds,  fixtures dev/states/dp_*      (Definitive Platinum)

Every Platinum harness (dev/platinum/*.py, dev/e2e/pt_*.py, ...) takes its ROM path and state names from here, so the
same script verifies both games:  YL_PT_ROM=defplat python dev/platinum/verify.py
The Definitive Platinum fixtures are made by dev/defplat/make_states.py (see dev/defplat/STATUS.md).
YL_PLATINUM_ROM (an explicit ROM path, older scripts/tests) still wins over the variant's default path.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
STATES = os.environ.get("YL_STATES", os.path.normpath(os.path.join(_HERE, "..", "states")))

VARIANTS = {
    # key: (ROM file in dev/states, fixture prefix, SHA1, adapter variant key)
    "vanilla": ("platinum.nds", "pt_", "ce81046eda7d232513069519cb2085349896dec7", "vanilla"),
    "defplat": ("defplat.nds", "dp_", "2f368f0f68f98058699a8dd0b1f9af3c970172a2", "definitive"),
}
NAME = os.environ.get("YL_PT_ROM", "vanilla").strip().lower() or "vanilla"
if NAME in ("definitive", "definitive_platinum", "dp"):
    NAME = "defplat"
if NAME not in VARIANTS:
    raise SystemExit("YL_PT_ROM must be one of %s (got %r)" % (", ".join(VARIANTS), NAME))
DEFPLAT = NAME == "defplat"
ROM_FILE, PREFIX, SHA1, ADAPTER_KEY = VARIANTS[NAME]
ROM = os.environ.get("YL_PLATINUM_ROM") or os.path.join(STATES, ROM_FILE)


def state(name: str) -> str:
    """File name of a fixture for the selected ROM: 'pt_pc.dsst' -> 'dp_pc.dsst' with YL_PT_ROM=defplat."""
    if name.startswith("pt_"):
        return PREFIX + name[3:]
    return name


def state_path(name: str) -> str:
    return os.path.join(STATES, state(name))


def label() -> str:
    return "Definitive Platinum" if DEFPLAT else "Platinum (US rev 0)"


# Facts of the fixtures that differ between the two ROMs' fixture sets (the trainer of a fresh NEW GAME gets random
# ids; the battle fixture's wild Pokémon comes from that ROM's encounter table). Everything else is the same save.
SAVED_TRAINER = ("LUCAS", 33356, 2998)        # pt_saved.sav: the save every *_starter/_pc/_town fixture comes from
FACTS = {
    "vanilla": {
        "room_trainer": SAVED_TRAINER,          # pt_room: NEW GAME (vanilla explorer), never saved
        "room_save": False, "town_save": False,
        "battle_wild": 399,                     # pt_battle: BIDOOF Lv2 on Route 201
    },
    "defplat": {
        "room_trainer": None,                   # dp_room: a real Definitive Platinum NEW GAME: LUCAS, random ids
        "room_save": False, "town_save": True,  # dp_town: the vanilla pt_town saved in-game, CONTINUE'd in DefPlat
        "battle_wild": None,                    # dp_battle: whatever Route 201 gave (dev/defplat/STATUS.md)
    },
}[NAME]
