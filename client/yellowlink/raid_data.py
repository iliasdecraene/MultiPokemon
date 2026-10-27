"""Raid data: which Pokémon appear where, their level and their raid moves, the shared-HP maths and the den spots.

Pure Python (stdlib only, no emulator): the client uses it, the server can vendor a copy (dev/raids/STATUS.md).
Ilias's decisions (dev/raids/DESIGN.md, "Ilias's decisions", 2026-10-27):
  * a region's raid boss is a Pokémon its game can hold and can't (normally) be caught there (DESIGN section 2),
    plus "second chance" raids for one-per-save legendaries in Kanto/Johto and Darkrai/Shaymin/Arceus/the Regis
    (+ Regigigas) in Sinnoh;
  * legendaries have a fixed, proper level (their canonical in-game encounters), mythicals are scaled per player:
    strongest party level + 5, clamped to 20..70; the caught Pokémon keeps that level;
  * shiny bosses 1 in 20;
  * group HP = 2 bars per player who joins, counted in milli-bars: 1000 = one full HP bar of the boss in the
    attacking player's own game (DESIGN A.1) - so every player can have their own boss level.

Move ids: Gen 1, Gen 2 and Gen 4 number the moves the same way (Gen 2 adds 166-251, Gen 4 goes up to 467), so one id
means the same move in every game that has it. Boss moves never end the battle or hurt the boss itself (no
Explosion/Selfdestruct/Memento/Teleport/Roar/Whirlwind/Metronome, no recoil, no Substitute/Curse/Belly Drum, no
binding moves in Yellow, no item-stealing moves) - DESIGN section 2. Checked against pret by dev/raids/verify.py.
"""
from __future__ import annotations

import json
import os
import random

REGION_GAME = {"kanto": "yellow", "johto": "crystal", "sinnoh": "platinum"}
GAME_REGION = {v: k for k, v in REGION_GAME.items()}
GAME_GEN = {"yellow": 1, "crystal": 2, "platinum": 4}
MAX_MOVE = {1: 165, 2: 251, 4: 467}           # highest move id each generation has
MAX_SPECIES = {1: 151, 2: 251, 4: 493}

MB_PER_BAR = 1000                              # milli-bars
BARS_PER_PLAYER = 2
SHINY_ODDS = 20                                # 1 in 20
MYTHICAL_BONUS = 5
MYTHICAL_MIN, MYTHICAL_MAX = 20, 70

# --- species (national dex) ---------------------------------------------------------------------------------------
ARTICUNO, ZAPDOS, MOLTRES, MEWTWO, MEW = 144, 145, 146, 150, 151
RAIKOU, ENTEI, SUICUNE, LUGIA, HO_OH, CELEBI = 243, 244, 245, 249, 250, 251
REGIROCK, REGICE, REGISTEEL, LATIAS, LATIOS, KYOGRE, GROUDON, RAYQUAZA, JIRACHI, DEOXYS = range(377, 387)
REGIGIGAS = 486
PHIONE, MANAPHY, DARKRAI, SHAYMIN, ARCEUS = 489, 490, 491, 492, 493

NAMES = {ARTICUNO: "Articuno", ZAPDOS: "Zapdos", MOLTRES: "Moltres", MEWTWO: "Mewtwo", MEW: "Mew",
         RAIKOU: "Raikou", ENTEI: "Entei", SUICUNE: "Suicune", LUGIA: "Lugia", HO_OH: "Ho-Oh", CELEBI: "Celebi",
         REGIROCK: "Regirock", REGICE: "Regice", REGISTEEL: "Registeel", LATIAS: "Latias", LATIOS: "Latios",
         KYOGRE: "Kyogre", GROUDON: "Groudon", RAYQUAZA: "Rayquaza", JIRACHI: "Jirachi", DEOXYS: "Deoxys",
         REGIGIGAS: "Regigigas", PHIONE: "Phione", MANAPHY: "Manaphy", DARKRAI: "Darkrai", SHAYMIN: "Shaymin",
         ARCEUS: "Arceus"}

MYTHICALS = frozenset({MEW, CELEBI, JIRACHI, DEOXYS, PHIONE, MANAPHY, DARKRAI, SHAYMIN, ARCEUS})
# Gen 4 checks the "fateful encounter" flag for obedience (Mew, Deoxys, Darkrai, Shaymin, Arceus); every mythical
# raid catch gets it, like an event Pokémon.
FATEFUL = MYTHICALS

# Fixed levels of the legendaries, from their canonical encounters (Ilias's decision 3):
LEVELS = {
    ARTICUNO: 50, ZAPDOS: 50, MOLTRES: 50,          # Red/Blue/Yellow + FireRed/LeafGreen statics: 50
    MEWTWO: 70,                                     # Cerulean Cave 70 (RBY, FRLG, HGSS)
    RAIKOU: 50, ENTEI: 50, SUICUNE: 50,             # roaming 40 (GSC) / 50 (FRLG); Ilias: beasts ~50
    LUGIA: 60, HO_OH: 60,                           # Crystal: Whirl Islands / Tin Tower 60
    REGIROCK: 40, REGICE: 40, REGISTEEL: 40,        # Ruby/Sapphire/Emerald 40
    LATIAS: 40, LATIOS: 40,                         # roaming 40 (RSE)
    KYOGRE: 70, GROUDON: 70, RAYQUAZA: 70,          # Emerald 70 (Rayquaza 70 in all three)
    REGIGIGAS: 70,                                  # Snowpoint Temple 70 (Diamond/Pearl)
}

# Raid pools per region: [(species, weight)] (raid-net mirrors these in the server; keep the shape).
POOLS = {
    # Kanto (Yellow can only hold Gen 1): Mew + second chances for the one-per-save legendaries
    "kanto": [(MEW, 1), (ARTICUNO, 1), (ZAPDOS, 1), (MOLTRES, 1), (MEWTWO, 1)],
    # Johto (Crystal): what Crystal doesn't have + second chances for its own one-per-save legendaries
    "johto": [(CELEBI, 1), (MEW, 1), (MEWTWO, 1), (ARTICUNO, 1), (ZAPDOS, 1), (MOLTRES, 1),
              (LUGIA, 1), (HO_OH, 1), (SUICUNE, 1), (RAIKOU, 1), (ENTEI, 1)],
    # Sinnoh (Platinum): Ilias's list + Darkrai, Shaymin, Arceus, the Regis and Regigigas
    "sinnoh": [(MEWTWO, 1), (LUGIA, 1), (HO_OH, 1), (RAIKOU, 1), (ENTEI, 1), (SUICUNE, 1), (LATIAS, 1), (LATIOS, 1),
               (KYOGRE, 1), (GROUDON, 1), (RAYQUAZA, 1), (MEW, 1), (CELEBI, 1), (JIRACHI, 1), (DEOXYS, 1),
               (MANAPHY, 1), (PHIONE, 1), (DARKRAI, 1), (SHAYMIN, 1), (ARCEUS, 1), (REGIROCK, 1), (REGICE, 1),
               (REGISTEEL, 1), (REGIGIGAS, 1)],
}

# --- moves (national move ids; names = pret constants) ------------------------------------------------------------
MOVE_IDS = {
    "WING_ATTACK": 17, "STOMP": 23, "LEER": 43, "MIST": 54, "FLAMETHROWER": 53, "HYDRO_PUMP": 56, "SURF": 57,
    "ICE_BEAM": 58, "BLIZZARD": 59, "BUBBLE_BEAM": 61, "AURORA_BEAM": 62, "PECK": 64, "DRILL_PECK": 65, "GROWTH": 74,
    "THUNDERBOLT": 85, "THUNDER": 87, "EARTHQUAKE": 89, "PSYCHIC": 94, "AGILITY": 97, "QUICK_ATTACK": 98,
    "TELEPORT": 100, "BARRIER": 112, "FIRE_BLAST": 126, "SWIFT": 129, "AMNESIA": 133, "ACID_ARMOR": 151,
    "SPLASH": 150, "AEROBLAST": 177, "GIGA_DRAIN": 202, "CHARM": 204, "SPARK": 209, "SAFEGUARD": 219,
    "SACRED_FIRE": 221, "RAIN_DANCE": 240, "SUNNY_DAY": 241, "CRUNCH": 242, "MIRROR_COAT": 243,
    "EXTREME_SPEED": 245, "ANCIENT_POWER": 246, "SHADOW_BALL": 247, "FUTURE_SIGHT": 248, "SUPERPOWER": 276,
    "ERUPTION": 284, "TAIL_GLOW": 294, "LUSTER_PURGE": 295, "MIST_BALL": 296, "WATER_SPOUT": 323,
    "EXTRASENSORY": 326, "IRON_DEFENSE": 334, "BULK_UP": 339, "MAGICAL_LEAF": 345, "CALM_MIND": 347,
    "DRAGON_DANCE": 349, "WATER_PULSE": 352, "DOOM_DESIRE": 353, "PSYCHO_BOOST": 354, "HAMMER_ARM": 359,
    "AURA_SPHERE": 396, "DARK_PULSE": 399, "DRAGON_PULSE": 406, "EARTH_POWER": 414, "NASTY_PLOT": 417,
    "THUNDER_FANG": 422, "ICE_FANG": 423, "FIRE_FANG": 424, "ZEN_HEADBUTT": 428, "FLASH_CANNON": 430,
    "DISCHARGE": 435, "LEAF_STORM": 437, "IRON_HEAD": 442, "STONE_EDGE": 444, "JUDGMENT": 449, "CRUSH_GRIP": 462,
    "DARK_VOID": 464, "SEED_FLARE": 465,
}
# never a boss move (ends the battle, faints/hurts the boss, steals/moves items, swaps the boss out, ...)
BANNED_MOVES = frozenset({
    120, 153, 262,            # Selfdestruct, Explosion, Memento
    100, 46, 18, 118,         # Teleport (only our flee), Roar, Whirlwind, Metronome
    36, 38, 66, 394, 413, 452, 457, 344, 394,  # Take Down, Double-Edge, Submission, Flare Blitz, Brave Bird,
                                               # Wood Hammer, Head Smash, Volt Tackle
    164, 174, 187, 220,       # Substitute, Curse, Belly Drum, Pain Split
    194, 195,                 # Destiny Bond, Perish Song
    26, 136,                  # Jump Kick, Hi Jump Kick (crash damage)
    20, 35, 83, 128, 250, 328,  # Bind, Wrap, Fire Spin, Clamp, Whirlpool, Sand Tomb (Gen 1: the player can't act)
    168, 343, 271, 415, 282,  # Thief, Covet, Trick, Switcheroo, Knock Off
    144, 226, 369, 361, 461,  # Transform, Baton Pass, U-turn, Healing Wish, Lunar Dance
    32, 12, 90, 329,          # Horn Drill, Guillotine, Fissure, Sheer Cold (one-hit KOs)
})

_M = MOVE_IDS
# Raid move sets per game generation (2-4 strong moves of the species' own kind)
MOVESETS = {
    1: {
        MEW: ["PSYCHIC", "THUNDERBOLT", "ICE_BEAM", "BARRIER"],
        MEWTWO: ["PSYCHIC", "ICE_BEAM", "THUNDERBOLT", "AMNESIA"],
        ARTICUNO: ["BLIZZARD", "ICE_BEAM", "AGILITY", "MIST"],
        ZAPDOS: ["THUNDERBOLT", "DRILL_PECK", "THUNDER", "AGILITY"],
        MOLTRES: ["FLAMETHROWER", "FIRE_BLAST", "PECK", "AGILITY"],
    },
    2: {
        CELEBI: ["PSYCHIC", "GIGA_DRAIN", "ANCIENT_POWER", "SAFEGUARD"],
        MEW: ["PSYCHIC", "ANCIENT_POWER", "THUNDERBOLT", "ICE_BEAM"],
        MEWTWO: ["PSYCHIC", "SHADOW_BALL", "ICE_BEAM", "THUNDERBOLT"],
        ARTICUNO: ["BLIZZARD", "ICE_BEAM", "AGILITY", "MIST"],
        ZAPDOS: ["THUNDERBOLT", "DRILL_PECK", "THUNDER", "AGILITY"],
        MOLTRES: ["FLAMETHROWER", "FIRE_BLAST", "WING_ATTACK", "AGILITY"],
        LUGIA: ["AEROBLAST", "HYDRO_PUMP", "ANCIENT_POWER", "SAFEGUARD"],
        HO_OH: ["SACRED_FIRE", "ANCIENT_POWER", "FIRE_BLAST", "SAFEGUARD"],
        SUICUNE: ["HYDRO_PUMP", "AURORA_BEAM", "MIRROR_COAT", "MIST"],
        RAIKOU: ["THUNDER", "SPARK", "CRUNCH", "QUICK_ATTACK"],
        ENTEI: ["FIRE_BLAST", "FLAMETHROWER", "STOMP", "LEER"],
    },
    4: {
        MEWTWO: ["PSYCHIC", "AURA_SPHERE", "SHADOW_BALL", "ICE_BEAM"],
        LUGIA: ["AEROBLAST", "EXTRASENSORY", "HYDRO_PUMP", "ANCIENT_POWER"],
        HO_OH: ["SACRED_FIRE", "EXTRASENSORY", "ANCIENT_POWER", "SUNNY_DAY"],
        RAIKOU: ["DISCHARGE", "EXTRASENSORY", "CRUNCH", "THUNDER_FANG"],
        ENTEI: ["FLAMETHROWER", "FIRE_FANG", "EXTRASENSORY", "CRUNCH"],
        SUICUNE: ["HYDRO_PUMP", "EXTRASENSORY", "ICE_FANG", "AURORA_BEAM"],
        LATIAS: ["MIST_BALL", "DRAGON_PULSE", "ZEN_HEADBUTT", "CHARM"],
        LATIOS: ["LUSTER_PURGE", "DRAGON_PULSE", "ZEN_HEADBUTT", "DRAGON_DANCE"],
        KYOGRE: ["WATER_SPOUT", "ICE_BEAM", "ANCIENT_POWER", "CALM_MIND"],
        GROUDON: ["ERUPTION", "EARTHQUAKE", "ANCIENT_POWER", "BULK_UP"],
        RAYQUAZA: ["DRAGON_PULSE", "EXTREME_SPEED", "CRUNCH", "DRAGON_DANCE"],
        MEW: ["PSYCHIC", "AURA_SPHERE", "ANCIENT_POWER", "NASTY_PLOT"],
        CELEBI: ["PSYCHIC", "LEAF_STORM", "ANCIENT_POWER", "FUTURE_SIGHT"],
        JIRACHI: ["DOOM_DESIRE", "PSYCHIC", "IRON_HEAD", "CALM_MIND"],
        DEOXYS: ["PSYCHO_BOOST", "SUPERPOWER", "ICE_BEAM", "THUNDERBOLT"],
        MANAPHY: ["WATER_PULSE", "SURF", "ACID_ARMOR", "TAIL_GLOW"],
        PHIONE: ["WATER_PULSE", "BUBBLE_BEAM", "ACID_ARMOR", "RAIN_DANCE"],
        DARKRAI: ["DARK_PULSE", "DARK_VOID", "SHADOW_BALL", "NASTY_PLOT"],
        SHAYMIN: ["SEED_FLARE", "EARTH_POWER", "MAGICAL_LEAF", "GROWTH"],
        ARCEUS: ["JUDGMENT", "EXTREME_SPEED", "FUTURE_SIGHT", "CALM_MIND"],
        REGIROCK: ["STONE_EDGE", "HAMMER_ARM", "ANCIENT_POWER", "IRON_DEFENSE"],
        REGICE: ["ICE_BEAM", "ANCIENT_POWER", "HAMMER_ARM", "AMNESIA"],
        REGISTEEL: ["FLASH_CANNON", "ANCIENT_POWER", "HAMMER_ARM", "IRON_DEFENSE"],
        REGIGIGAS: ["CRUSH_GRIP", "ZEN_HEADBUTT", "STOMP", "AGILITY"],
    },
}


def moves_for(species: int, game: str) -> list:
    """The boss's 4 raid move ids in that game (0 = empty slot)."""
    names = MOVESETS[GAME_GEN[game]].get(species)
    if not names:
        raise KeyError("no raid moves for species %d in %s" % (species, game))
    ids = [_M[n] for n in names][:4]
    return ids + [0] * (4 - len(ids))


def is_mythical(species: int) -> bool:
    return species in MYTHICALS


def boss_level(species: int, player_strongest_level: int) -> int:
    """Legendaries: their fixed level. Mythicals: strongest + 5, between 20 and 70."""
    if species in LEVELS and species not in MYTHICALS:
        return LEVELS[species]
    lv = int(player_strongest_level or 0) + MYTHICAL_BONUS
    return max(MYTHICAL_MIN, min(MYTHICAL_MAX, lv))


def boss_for(species: int, player_strongest_level: int, region: str, shiny: bool = False, form: int = 0) -> dict:
    """The boss as this player's game runs it (see dev/raids/STATUS.md for the shape)."""
    game = REGION_GAME[region]
    if species not in dict(POOLS[region]):
        raise KeyError("species %d is not a %s raid boss" % (species, region))
    return {"species": species, "level": boss_level(species, player_strongest_level),
            "moves": moves_for(species, game), "shiny": bool(shiny), "fateful": species in FATEFUL,
            "region": region, "game": game, "kind": "mythical" if species in MYTHICALS else "legendary",
            "form": int(form)}


def pick_boss(region: str, rng: random.Random | None = None) -> int:
    """Weighted random species of that region's pool."""
    rng = rng or random
    pool = POOLS[region]
    total = sum(w for _s, w in pool)
    r = rng.random() * total
    for s, w in pool:
        r -= w
        if r < 0:
            return s
    return pool[-1][0]


def roll_shiny(rng: random.Random | None = None) -> bool:
    return (rng or random).randrange(SHINY_ODDS) == 0


def strongest_level(levels) -> int:
    """Highest level among the given party levels (eggs/zeros ignored), 0 if none."""
    return max((int(v) for v in levels if v), default=0)


# --- shared HP (DESIGN A.1) ------------------------------------------------------------------------------------
def bar_remainder(pool_mb: int) -> int:
    """Milli-bars left in the current bar (1..1000) of a non-empty pool, 0 for an empty one."""
    pool_mb = int(pool_mb)
    if pool_mb <= 0:
        return 0
    return (pool_mb - 1) % MB_PER_BAR + 1


def segment(pool_mb: int, max_hp: int) -> int:
    """The boss's local HP for a pool: the current bar scaled to the local max HP, rounded up, at least 1
    (0 only for an empty pool)."""
    rem = bar_remainder(pool_mb)
    if rem == 0 or max_hp <= 0:
        return 0
    return max(1, -(-rem * int(max_hp) // MB_PER_BAR))


def damage_mb(hp: int, max_hp: int) -> int:
    """Milli-bars that `hp` points of damage are worth on a boss with that local max HP (rounded to nearest)."""
    if max_hp <= 0 or hp <= 0:
        return 0
    return min(MB_PER_BAR, (int(hp) * MB_PER_BAR * 2 + int(max_hp)) // (2 * int(max_hp)))


class BarMeter:
    """Turns the boss's local HP drops into milli-bars, exactly: the bar's milli-bars at the last sync are spread
    over the segment's HP points (f(h) = bar_mb * h // seg), so hitting the whole segment down to "0" (the clamp at
    1 counts as 0) reports exactly the bar's milli-bars. HP increases (drain, Leftovers) don't count; only drops
    below the lowest HP seen since the sync do."""

    def __init__(self):
        self.bar_mb = 0
        self.seg = 0
        self.low = 0
        self.reported = 0          # milli-bars reported since the sync
        self.done = False          # the segment has been taken down (the boss sits at 1 HP until the next sync)

    def sync(self, pool_mb: int, max_hp: int) -> int:
        """Start a new segment for the pool; returns the local HP to write (0 = empty pool)."""
        self.bar_mb = bar_remainder(pool_mb)
        self.seg = segment(pool_mb, max_hp)
        self.low = self.seg
        self.reported = 0
        self.done = self.seg == 0
        return self.seg

    def _f(self, h: int) -> int:
        if self.seg <= 0:
            return 0
        return self.bar_mb * max(0, min(h, self.seg)) // self.seg

    def observe(self, hp: int, zeroed: bool = False):
        """Local HP now (zeroed = the hit took it to 0 / was clamped to 1). Returns (hp_drop, mb) since the last
        call, (0, 0) if nothing new."""
        if self.seg <= 0 or self.done:
            return 0, 0
        eff = 0 if zeroed else max(0, int(hp))
        if eff >= self.low:
            return 0, 0
        drop = self.low - eff
        self.low = eff
        if eff == 0:
            self.done = True
        mb = (self.bar_mb - self._f(eff)) - self.reported
        self.reported += mb
        return drop, mb


# --- spots -------------------------------------------------------------------------------------------------------
_SPOTS = None
SPOTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raid_spots.json")


def _load_spots() -> dict:
    global _SPOTS
    if _SPOTS is None:
        try:
            with open(SPOTS_FILE, encoding="utf-8") as f:
                _SPOTS = json.load(f)
        except (OSError, ValueError):
            _SPOTS = {}
    return _SPOTS


def spot_maps(region: str) -> dict:
    """{map key (str): {"map": id, "name", "reach", "spots": [[x, y], ...]}} for a region."""
    return _load_spots().get(region, {}).get("maps", {})


def spots(region: str) -> list:
    """Every den spot of a region: [{region, map, x, y, name, reach}]. map = the game's own map id (Yellow
    wCurMap, Crystal group << 8 | number, Platinum map header id); Platinum x/y are the overworld's global tile
    coordinates (outdoor maps share one grid); reach = gym badges needed to walk there."""
    out = []
    for _k, m in spot_maps(region).items():
        for x, y in m["spots"]:
            out.append({"region": region, "map": m["map"], "x": x, "y": y, "name": m["name"], "reach": m["reach"]})
    return out


def pick_spot(region: str, max_reach: int = 8, rng: random.Random | None = None):
    """A random spot (each map equally likely, then a spot in it) that needs at most `max_reach` badges."""
    rng = rng or random
    maps = [m for m in spot_maps(region).values() if m["reach"] <= max_reach and m["spots"]]
    if not maps:
        return None
    m = rng.choice(sorted(maps, key=lambda v: (v["map"], v["name"])))
    x, y = rng.choice(m["spots"])
    return {"region": region, "map": m["map"], "x": x, "y": y, "name": m["name"], "reach": m["reach"]}
