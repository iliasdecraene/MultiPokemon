"""Badges (0.7.5): per-region definitions and rules. Pure - no emulator, no network.

Per region (Kanto = Yellow, Johto = Crystal, Sinnoh = Platinum), earned in the player's MAIN game only:
  champion  entered the Hall of Fame in that game
  dex       caught (owned flag) the whole regional dex: Kanto 150 (all but Mew), Johto 249 (all 251 but Mew and
            Celebi), Sinnoh the 210 of Platinum's Sinnoh dex
  shinies   distinct shiny mons the player caught in that game (own OT name + TID, origin = that game); tiers
            1 / 10 / 25 / 50 / 100; remembered for good once seen, so trading/releasing it never lowers the count
  soullink  "gold" (run played with Exp Share All off) or "silver" (on) - set by the Soul Link award, not here

Earned state (what the client keeps and syncs; never loses anything):
  {"kanto": {"champion": ts, "dex": ts, "shinies": [key, ...], "soullink": "gold" | "silver"}, "johto": {...}, ...}
  A missing field = not earned. ts = unix time (float) of the first detection.

Adapter side (game.py / crystal.py / platinum.py, `self.G`): `progress(mem) -> dict | None` with
  {"champion": bool, "dex_caught": int, "dex_total": int, "dex_complete": bool, "shinies": [key, ...],
   "owner": {"ot": str, "tid": int[, "sid": int]}}
built with progress_dict() below. Shiny keys are the PC database's mon keys (game.mon_key / crystal.mon_key /
pk4.mon_key = Gen1PC/Gen2PC/Gen4PC .key()), so a mon seen in the game and in the YellowLink PC store is counted once.

Gen 1 has no shinies: a Yellow mon counts as shiny when its DVs would make it shiny in Gen 2 (Def 10, Spd 10,
Spc 10, Atk in {2, 3, 6, 7, 10, 11, 14, 15}) - gen1_is_shiny().
"""
from __future__ import annotations

import base64
import re

from . import text

REGIONS = ("kanto", "johto", "sinnoh")
REGION_OF_GAME = {"yellow": "kanto", "crystal": "johto", "platinum": "sinnoh"}
GAME_OF_REGION = {r: g for g, r in REGION_OF_GAME.items()}
REGION_NAMES = {"kanto": "Kanto", "johto": "Johto", "sinnoh": "Sinnoh"}

MEW, CELEBI, MANAPHY = 151, 251, 490

# --- regional Pokédex lists (national dex numbers) -------------------------------------------------------------
# Kanto: like Yellow's diploma (pret/pokeyellow scripts/CeladonMansion3F.asm: owned >= NUM_POKEMON - 1, "discount
# Mew") - here exactly #1-150.
KANTO_DEX = tuple(range(1, 151))
# Johto: Crystal's dex of 251, Mew and Celebi excluded (as Ilias decided: 249).
JOHTO_DEX = tuple(d for d in range(1, 252) if d not in (MEW, CELEBI))
# Sinnoh: Platinum's Sinnoh dex in its own order, from pret/pokeplatinum res/pokemon/sinnoh_pokedex.json (Arceus,
# listed first there, is left out by tools/dataproc speciesproc.c LoadSinnohDex -> 210 entries, = REGIONAL_DEX_COUNT)
# converted to national numbers with generated/species.txt. Checked against the ROM's own list
# (/application/zukanlist/zkn_data/zukan_data.narc member 12) by dev/badges/verify.py.
# NOTE: #151 of this list is MANAPHY (490): Platinum's own Sinnoh diploma only needs it SEEN; it can't be caught in
# Platinum without a Pokémon Ranger egg. SINNOH_DEX_EXCLUDED lets the coordinator drop it from the "caught all" goal.
SINNOH_DEX = (
    387, 388, 389, 390, 391, 392, 393, 394, 395, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 63,
    64, 65, 129, 130, 406, 315, 407, 41, 42, 169, 74, 75, 76, 95, 208, 408, 409, 410, 411, 66,
    67, 68, 54, 55, 412, 413, 414, 265, 266, 267, 268, 269, 415, 416, 417, 418, 419, 420, 421, 422,
    423, 214, 190, 424, 425, 426, 427, 428, 92, 93, 94, 200, 429, 198, 430, 431, 432, 118, 119, 339,
    340, 433, 358, 434, 435, 307, 308, 436, 437, 77, 78, 438, 185, 439, 122, 440, 113, 242, 173, 35,
    36, 441, 172, 25, 26, 163, 164, 442, 443, 444, 445, 446, 143, 201, 447, 448, 194, 195, 278, 279,
    203, 449, 450, 298, 183, 184, 451, 452, 453, 454, 455, 223, 224, 456, 457, 72, 73, 349, 350, 458,
    226, 459, 460, 215, 461, 480, 481, 482, 483, 484, 490, 479, 207, 472, 299, 476, 280, 281, 282, 475,
    108, 463, 133, 134, 135, 136, 196, 197, 470, 471, 333, 334, 175, 176, 468, 228, 229, 81, 82, 462,
    114, 465, 193, 469, 357, 111, 112, 464, 355, 356, 477, 137, 233, 474, 123, 212, 239, 125, 466, 240,
    126, 467, 220, 221, 473, 361, 362, 478, 359, 487,
)
SINNOH_DEX_EXCLUDED = ()          # e.g. (MANAPHY,) to make the Sinnoh goal 209 - see the note above
REGIONAL_DEX = {
    "kanto": KANTO_DEX,
    "johto": JOHTO_DEX,
    "sinnoh": tuple(d for d in SINNOH_DEX if d not in SINNOH_DEX_EXCLUDED),
}
DEX_TOTAL = {r: len(v) for r, v in REGIONAL_DEX.items()}          # 150 / 249 / 210

# --- shinies -----------------------------------------------------------------------------------------------------
SHINY_TIERS = (1, 10, 25, 50, 100)
SHINY_KEEP = 200                  # keys remembered per region (well above the top tier; bounds the synced state)
KEY_RE = re.compile(r"^[A-Za-z0-9_:-]{1,40}$")
SOULLINK_MEDALS = ("silver", "gold")      # rank order: gold beats silver


def shiny_tier(count: int) -> int:
    """Highest tier reached (1, 10, 25, 50 or 100), 0 = none yet."""
    best = 0
    for t in SHINY_TIERS:
        if count >= t:
            best = t
    return best


def next_shiny_tier(count: int):
    """The next tier to reach, or None after the top one."""
    for t in SHINY_TIERS:
        if count < t:
            return t
    return None


def gen1_is_shiny(dvs) -> bool:
    """Gen-2 shiny rule on Gen-1/Gen-2 DVs: 2 bytes (atk<<4|def, spd<<4|spc) or the same as a 16-bit int.
    Shiny = Def 10, Spd 10, Spc 10 and Atk in {2, 3, 6, 7, 10, 11, 14, 15} (Atk bit 1 set) - pret pokecrystal
    engine/gfx/color.asm CheckShininess."""
    if isinstance(dvs, int):
        hi, lo = (dvs >> 8) & 0xFF, dvs & 0xFF
    else:
        hi, lo = dvs[0], dvs[1]
    return (hi & 0x0F) == 10 and lo == 0xAA and bool(hi & 0x20)


gen2_is_shiny = gen1_is_shiny          # same rule, same 2 DV bytes


def gen4_is_shiny(pid: int, tid: int, sid: int) -> bool:
    return (tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)) < 8


def dex_caught(bits: bytes, region: str) -> int:
    """How many of the region's goal species are set in a caught/owned bitfield (bit dex-1, LSB first: the same
    layout in Yellow wPokedexOwned, Crystal wPokedexCaught and Platinum Pokedex.caughtPokemon)."""
    n = 0
    for d in REGIONAL_DEX[region]:
        i = d - 1
        if i >> 3 < len(bits) and bits[i >> 3] & (1 << (i & 7)):
            n += 1
    return n


def progress_dict(region: str, champion: bool, caught_bits: bytes, shinies, owner: dict) -> dict:
    caught = dex_caught(caught_bits, region)
    keys = []
    for k in shinies:
        if k not in keys:
            keys.append(k)
    return {"champion": bool(champion), "dex_caught": caught, "dex_total": DEX_TOTAL[region],
            "dex_complete": caught >= DEX_TOTAL[region], "shinies": keys, "owner": owner}


# --- own shiny catches (records in each game's format) --------------------------------------------------------
# owner = {"ot": player name (str), "tid": trainer id (int)[, "sid": secret id (Platinum)]}
def _same_ot(raw_ot: bytes, owner: dict) -> bool:
    return text.decode(raw_ot) == owner.get("ot") and owner.get("ot") not in (None, "")


def gen1_shiny_key(data: bytes, ot: bytes, owner: dict):
    """Yellow: key (game.mon_key) of an own shiny catch, else None. `data` = 44-byte party struct or 33-byte box
    struct (OT ID 12-13, DVs 27-28 in both), `ot` = its 11-byte OT name. Gen 1 records have no origin game."""
    from .game import mon_key
    if len(data) < 29 or data[0] == 0 or not gen1_is_shiny(data[27:29]):
        return None
    if ((data[12] << 8) | data[13]) != owner.get("tid") or not _same_ot(ot, owner):
        return None
    return mon_key(data)


def gen2_shiny_key(data: bytes, ot: bytes, owner: dict, egg: bool = False):
    """Crystal: key (crystal.mon_key) of an own shiny catch, else None. `data` = 48-byte party or 32-byte box struct
    (OT ID 6-7, DVs 21-22, caught data 29-30). Origin = Crystal: caught data non-zero (Crystal writes it for every
    catch, gift, hatch and contest prize - pret SetCaughtData / SetGiftMonCaughtData / SetEggMonCaughtData; Gen-1
    mons converted by YellowLink have none). Eggs don't count until they hatch (same key after hatching)."""
    from .crystal import mon_key
    if egg or len(data) < 31 or data[0] == 0 or not gen1_is_shiny(data[21:23]):
        return None
    if data[29] == 0 and data[30] == 0:
        return None
    if ((data[6] << 8) | data[7]) != owner.get("tid") or not _same_ot(ot, owner):
        return None
    return mon_key(data)


VERSION_PLATINUM = 12


def gen4_shiny_key(record: bytes, owner: dict):
    """Platinum: key (pk4.mon_key) of an own shiny catch, else None. `record` = a VALID encrypted 136/236-byte
    record (callers normalise / checksum first). Own = origin game Platinum (12) + OT name + TID + SID. Eggs don't
    count until they hatch (same PID/TID/SID -> same key)."""
    from . import pk4
    try:
        pk = pk4.PK4(pk4.decrypt(bytes(record)[:pk4.BOX_SIZE]))
    except Exception:  # noqa: BLE001 - unreadable record: not counted
        return None
    if not 1 <= pk.species <= pk4.NUM_SPECIES or pk.is_egg or not gen4_is_shiny(pk.pid, pk.tid, pk.sid):
        return None
    if pk.origin_game != VERSION_PLATINUM or pk.tid != owner.get("tid") or pk.sid != owner.get("sid"):
        return None
    try:
        name = pk.ot_name
    except Exception:  # noqa: BLE001
        return None
    if name != owner.get("ot") or not name:
        return None
    return pk4.mon_key(record)


def entry_is_shiny(entry: dict) -> bool:
    """A PC-store Entry (SPEC.md section 5) is shiny in its current representation: Gen-1/Gen-2 DVs by the Gen-2
    rule, Gen-4 by PID/TID/SID. Stand-ins (Ditto ?NAME, DVs 0) never are."""
    from .pcdb import current_rep
    try:
        rep, data = current_rep(entry)
        if rep == "gen1":
            return len(data) >= 29 and gen1_is_shiny(data[27:29])
        if rep == "gen2":
            return len(data) >= 23 and gen1_is_shiny(data[21:23])
        if rep == "gen4":
            from . import pk4
            pk = pk4.PK4(pk4.decrypt(data[:pk4.BOX_SIZE]))
            return gen4_is_shiny(pk.pid, pk.tid, pk.sid)
    except Exception:  # noqa: BLE001 - damaged entry
        return False
    return False


def entry_shiny_key(entry: dict, region: str, owner: dict | None):
    """Key of a PC-store Entry if it is one of the player's own shiny catches from `region`'s game (judged on that
    game's representation in the entry: gen1 for Kanto, gen2 for Johto, gen4 for Sinnoh), else None. `owner` =
    the "owner" of the last progress() of that game (None = unknown -> None)."""
    if not owner:
        return None
    rep = {"kanto": "gen1", "johto": "gen2", "sinnoh": "gen4"}.get(region)
    raw = entry.get(rep) if rep else None
    if not raw:
        return None
    try:
        data = base64.b64decode(raw)
        if region == "kanto":
            return gen1_shiny_key(data[:33], data[33:44], owner) if len(data) >= 44 else None
        if region == "johto":
            egg = len(data) > 54 and bool(data[54])
            return gen2_shiny_key(data[:32], data[32:43], owner, egg) if len(data) >= 43 else None
        return gen4_shiny_key(data, owner) if len(data) >= 136 else None
    except Exception:  # noqa: BLE001
        return None


# --- earned state --------------------------------------------------------------------------------------------
def normalise(earned) -> dict:
    """A clean copy of an earned state (unknown regions/fields and bad values dropped)."""
    out = {}
    if not isinstance(earned, dict):
        return out
    for region in REGIONS:
        src = earned.get(region)
        if not isinstance(src, dict):
            continue
        dst = {}
        for f in ("champion", "dex"):
            v = src.get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                dst[f] = v
        keys = []
        for k in src.get("shinies") or []:
            if isinstance(k, str) and KEY_RE.match(k) and k not in keys:
                keys.append(k)
        if keys:
            dst["shinies"] = keys[:SHINY_KEEP]
        if src.get("soullink") in SOULLINK_MEDALS:
            dst["soullink"] = src["soullink"]
        if dst:
            out[region] = dst
    return out


def merge(earned: dict, region: str, progress, now: float) -> list:
    """Fold one progress() reading into `earned` (in place; never removes anything). Returns what was newly earned:
    "champion", "dex", "shiny:<tier>" (a new tier reached). progress None (can't read now) -> []."""
    if not progress or region not in REGIONS:
        return []
    r = earned.setdefault(region, {})
    new = []
    if progress.get("champion") and not r.get("champion"):
        r["champion"] = now
        new.append("champion")
    if progress.get("dex_complete") and not r.get("dex"):
        r["dex"] = now
        new.append("dex")
    keys = r.get("shinies") or []
    before = shiny_tier(len(keys))
    for k in progress.get("shinies") or ():
        if isinstance(k, str) and KEY_RE.match(k) and k not in keys and len(keys) < SHINY_KEEP:
            keys.append(k)
    if keys:
        r["shinies"] = keys
    after = shiny_tier(len(keys))
    if after > before:
        new.append("shiny:%d" % after)
    if not r:
        del earned[region]
    return new


def award_soullink(earned: dict, region: str, medal: str) -> bool:
    """Give the Soul Link badge of a region; gold beats silver (silver never replaces gold). True if it changed."""
    if region not in REGIONS or medal not in SOULLINK_MEDALS:
        return False
    r = earned.setdefault(region, {})
    have = r.get("soullink")
    if have in SOULLINK_MEDALS and SOULLINK_MEDALS.index(have) >= SOULLINK_MEDALS.index(medal):
        return False
    r["soullink"] = medal
    return True


def merge_earned(a, b) -> dict:
    """Union of two earned states (e.g. the local copy and the server's): earliest timestamps, all shiny keys
    (a's first), the better Soul Link medal. Neither input is changed."""
    a, b = normalise(a), normalise(b)
    out = {}
    for region in REGIONS:
        ra, rb = a.get(region, {}), b.get(region, {})
        if not ra and not rb:
            continue
        r = {}
        for f in ("champion", "dex"):
            vals = [v for v in (ra.get(f), rb.get(f)) if v]
            if vals:
                r[f] = min(vals)
        keys = list(ra.get("shinies", []))
        for k in rb.get("shinies", []):
            if k not in keys:
                keys.append(k)
        if keys:
            r["shinies"] = keys[:SHINY_KEEP]
        medals = [m for m in (ra.get("soullink"), rb.get("soullink")) if m]
        if medals:
            r["soullink"] = max(medals, key=SOULLINK_MEDALS.index)
        out[region] = r
    return out


def summary(earned: dict, region: str) -> dict:
    """What the UI draws for one region: {"champion": bool, "dex": bool, "shinies": count, "shiny_tier": tier,
    "soullink": None | "silver" | "gold"}."""
    r = (earned or {}).get(region) or {}
    n = len(r.get("shinies") or [])
    return {"champion": bool(r.get("champion")), "dex": bool(r.get("dex")), "shinies": n,
            "shiny_tier": shiny_tier(n), "soullink": r.get("soullink")}
