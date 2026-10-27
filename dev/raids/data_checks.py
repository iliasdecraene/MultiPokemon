"""raid_data checks against the pret decomps (part of dev/raids/verify.py): move names = ids in all three games, every
boss move exists in its game, the banned list really is what DESIGN.md says, species ids, the spot file is what
dev/raids/gen_spots.py makes today."""
import json
import os
import re
import tempfile

from yellowlink import raid_data as rd

PRET = {"yellow": os.environ.get("PRET_YELLOW", "/home/claude/pokeyellow"),
        "crystal": os.environ.get("PRET_CRYSTAL", "/home/claude/pokecrystal"),
        "platinum": os.environ.get("PRET_PLATINUM", "/home/claude/pokeplatinum")}


def gb_moves(path):
    """const NAME lines of constants/move_constants.asm -> {NAME: id}."""
    out, idx = {}, 0
    with open(os.path.join(path, "constants/move_constants.asm"), encoding="utf-8") as f:
        for line in f:
            if re.match(r"\s*const_def", line):
                idx = 0
                continue
            m = re.match(r"\s*const\s+(\w+)", line)
            if m:
                out[m.group(1)] = idx
                idx += 1
    return out


def pt_moves(path):
    names = [l.strip() for l in open(os.path.join(path, "generated/moves.txt")) if l.strip()]
    return {n[len("MOVE_"):]: i for i, n in enumerate(names)}


def run(check, info):
    print("== raid data vs pret ==", flush=True)
    tables = {}
    for game in ("yellow", "crystal", "platinum"):
        if not os.path.isdir(PRET[game]):
            info("no %s decomp: skipped" % game)
            continue
        tables[game] = pt_moves(PRET[game]) if game == "platinum" else gb_moves(PRET[game])
    alias = {"ANCIENT_POWER": "ANCIENTPOWER", "BUBBLE_BEAM": "BUBBLEBEAM", "SELF_DESTRUCT": "SELFDESTRUCT",
             "PSYCHIC": "PSYCHIC_M", "EXTREME_SPEED": "EXTREMESPEED", "HIGH_JUMP_KICK": "HI_JUMP_KICK"}
    bad = []
    for name, mid in rd.MOVE_IDS.items():
        for game, t in tables.items():
            gen = rd.GAME_GEN[game]
            if mid > rd.MAX_MOVE[gen]:
                continue
            got = t.get(name, t.get(alias.get(name, ""), None))
            if got != mid:
                bad.append((game, name, mid, got))
    check("every raid move name = its id in each game's pret constants", not bad, str(bad[:6]))
    pt = tables.get("platinum", {})
    banned = {"SELF_DESTRUCT", "EXPLOSION", "MEMENTO", "TELEPORT", "ROAR", "WHIRLWIND", "METRONOME", "PERISH_SONG",
              "DESTINY_BOND", "SUBSTITUTE", "CURSE", "BELLY_DRUM", "PAIN_SPLIT", "THIEF", "COVET", "TRICK",
              "SWITCHEROO", "KNOCK_OFF", "TAKE_DOWN", "DOUBLE_EDGE", "SUBMISSION", "FLARE_BLITZ", "BRAVE_BIRD",
              "WOOD_HAMMER", "HEAD_SMASH", "VOLT_TACKLE", "BIND", "WRAP", "FIRE_SPIN", "CLAMP", "U_TURN",
              "BATON_PASS", "TRANSFORM", "HEALING_WISH", "LUNAR_DANCE", "FISSURE", "GUILLOTINE", "HORN_DRILL",
              "SHEER_COLD", "JUMP_KICK", "HIGH_JUMP_KICK", "WHIRLPOOL", "SAND_TOMB"}
    if pt:
        missing = [n for n in banned if pt.get(n, pt.get(alias.get(n, ""))) not in rd.BANNED_MOVES]
        check("banned boss moves (DESIGN section 2 + recoil/self-KO/item/binding) are all in BANNED_MOVES",
              not missing, str(missing))
        bosses = []
        for region, pool in rd.POOLS.items():
            game = rd.REGION_GAME[region]
            for s, _w in pool:
                for mv in rd.moves_for(s, game):
                    if mv and (mv in rd.BANNED_MOVES or mv > rd.MAX_MOVE[rd.GAME_GEN[game]]):
                        bosses.append((region, s, mv))
        check("every boss's raid moves exist in its game and none is banned", not bosses, str(bosses))
        species = [l.strip() for l in open(os.path.join(PRET["platinum"], "generated/species.txt"))
                   if l.strip()] if os.path.exists(os.path.join(PRET["platinum"], "generated/species.txt")) else []
        if species:
            wrong = [(n, sid) for sid, n in rd.NAMES.items()
                     if species[sid].replace("SPECIES_", "").replace("_", "-") != n.upper()]
            check("species ids = names (national dex)", not wrong, str(wrong))
    # spot file = today's generator output
    import gen_spots
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "spots.json")
        gen_spots.OUT = p
        import sys
        argv = sys.argv
        sys.argv = ["gen_spots", "--out", p]
        try:
            gen_spots.main()
        finally:
            sys.argv = argv
        fresh = json.load(open(p))
    shipped = json.load(open(rd.SPOTS_FILE))
    check("client/yellowlink/raid_spots.json = dev/raids/gen_spots.py output", fresh == shipped)
