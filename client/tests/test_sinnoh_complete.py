"""Sanity of the Complete Sinnoh spec (client/yellowlink/sinnoh_complete.py). No ROM or pret needed.

The full proof (every family obtainable, nothing lost, ROM byte layout) is dev/sinnoh/check_plan.py; these
tests guard the spec's own rules so a careless edit fails CI.
"""
import struct

import pytest

from yellowlink import sinnoh_complete as sc

MYTHICAL = {151, 251, 385, 386, 489, 490, 491, 492, 493}          # incl. Darkrai/Shaymin/Arceus (events)
LEGENDARY = {144, 145, 146, 150, 243, 244, 245, 249, 250, 377, 378, 379, 380, 381, 382, 383, 384,
             480, 481, 482, 483, 484, 485, 486, 487, 488}
STARTER_BASES = {1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393}
# fully evolved or middle stages we never put in the wild ourselves (base forms only)
NOT_BASE = {2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 24, 28, 38, 59, 205, 217, 271, 272, 274, 275, 329, 330, 368, 369,
            409, 411, 432, 435, 153, 154, 156, 157, 159, 160, 253, 254, 256, 257, 259, 260, 388, 389, 391, 392,
            394, 395, 199, 429, 430}


def test_version_and_layout_constants():
    assert isinstance(sc.SPEC_VERSION, int) and sc.SPEC_VERSION >= 1
    assert sc.TABLE_COUNT == len(sc.TABLE_NAMES) == 183 and sc.TABLE_SIZE == 424
    assert len(set(sc.TABLE_NAMES)) == 183
    # every slot region lies inside the 424-byte member and none overlap
    spans = []
    for kind, (base, count, size, _t) in sc.SLOT_KINDS.items():
        spans.append((base, base + count * size, kind))
    for kind, off in sc.RATE_OFFSETS.items():
        spans.append((off, off + 4, 'rate ' + kind))
    spans.append((sc.FORMS_OFFSET, sc.FORMS_OFFSET + 24, 'forms'))
    spans.sort()
    for (a0, a1, ka), (b0, b1, kb) in zip(spans, spans[1:]):
        assert a1 <= b0, f'{ka} overlaps {kb}'
    assert spans[-1][1] <= sc.TABLE_SIZE


def test_rarity_weights_sum_to_100():
    assert sum(sc.LAND_WEIGHTS) == 100 and len(sc.LAND_WEIGHTS) == 12
    for kind, w in sc.WATER_WEIGHTS.items():
        assert sum(w) == 100 and len(w) == 5, kind


def test_validate_clean():
    assert sc.validate() == []
    assert len(sc.EDITS) > 0


def test_ids_in_range():
    seen = set()
    for e in sc.EDITS:
        assert 0 <= e.table < sc.TABLE_COUNT and sc.TABLE_NAMES[e.table] == e.name
        base, count, size, etype = sc.SLOT_KINDS[e.kind]
        assert 0 <= e.slot < count
        assert 1 <= e.species <= sc.MAX_SPECIES and 1 <= e.old_species <= sc.MAX_SPECIES
        assert (e.table, e.kind, e.slot) not in seen
        seen.add((e.table, e.kind, e.slot))
        assert e.kind not in ('ruby', 'sapphire', 'emerald', 'firered', 'leafgreen'), 'GBA slots are unreachable'
    assert set(sc.SPECIES.values()) <= set(range(1, sc.MAX_SPECIES + 1))
    assert len(set(sc.SPECIES.values())) == len(sc.SPECIES)


def test_no_legendaries_or_mythicals_in_the_wild():
    added = {e.species for e in sc.EDITS}
    assert not added & LEGENDARY
    assert not added & MYTHICAL


def test_base_forms_only():
    assert not {e.species for e in sc.EDITS} & NOT_BASE


def test_levels_fit_the_area():
    for e in sc.EDITS:
        etype = sc.SLOT_KINDS[e.kind][3]
        if etype == 'land':
            assert 2 <= e.level <= 100
            assert abs(e.level - e.old_level) <= 3, e      # the slot's own level band
        elif etype == 'water':
            assert 1 <= e.min_level <= e.max_level <= 100
            assert e.old_min - 5 <= e.min_level and e.max_level <= e.old_max + 5, e
        else:
            assert e.level is None and e.min_level is None and e.max_level is None


def test_early_routes_untouched_and_guards():
    assert sc.EARLY_UNTOUCHED <= set(sc.TABLE_NAMES) and sc.POST_GAME <= set(sc.TABLE_NAMES)
    for e in sc.EDITS:
        assert e.name not in sc.EARLY_UNTOUCHED, e
        assert not e.name.startswith('unknown_')
        assert not (e.name.startswith('great_marsh') and e.kind == 'land' and e.slot in (6, 7))
        assert not (e.name == 'trophy_garden' and e.kind == 'land' and e.slot in (6, 7))


def _vanilla_like(member):
    """A synthetic 424-byte table holding exactly the old_* values of our edits (other bytes = pattern)."""
    b = bytearray((i * 7 + member) & 0xFF for i in range(sc.TABLE_SIZE))
    for e in sc.edits_for(member):
        base, count, size, etype = sc.SLOT_KINDS[e.kind]
        o = base + size * e.slot
        if etype == 'land':
            struct.pack_into('<II', b, o, e.old_level, e.old_species)
        elif etype == 'water':
            b[o], b[o + 1] = e.old_max, e.old_min
            struct.pack_into('<I', b, o + 4, e.old_species)
        else:
            struct.pack_into('<I', b, o, e.old_species)
    return bytes(b)


def test_apply_patches_exactly_the_slots():
    for member in sorted({e.table for e in sc.EDITS}):
        raw = _vanilla_like(member)
        new = sc.apply(member, raw)
        assert len(new) == sc.TABLE_SIZE
        assert sc.apply(member, raw) == new                    # deterministic
        touched = set()
        for e in sc.edits_for(member):
            base, count, size, etype = sc.SLOT_KINDS[e.kind]
            o = base + size * e.slot
            if etype == 'land':
                assert struct.unpack_from('<II', new, o) == (e.level, e.species)
                touched |= set(range(o, o + 8))
            elif etype == 'water':
                assert (new[o], new[o + 1]) == (e.max_level, e.min_level)
                assert new[o + 2:o + 4] == raw[o + 2:o + 4]    # pad bytes untouched
                assert struct.unpack_from('<I', new, o + 4)[0] == e.species
                touched |= {o, o + 1} | set(range(o + 4, o + 8))
            else:
                assert struct.unpack_from('<I', new, o)[0] == e.species
                touched |= set(range(o, o + 4))
        for i in range(sc.TABLE_SIZE):
            if i not in touched:
                assert new[i] == raw[i], (member, i)


def test_apply_leaves_other_tables_alone():
    untouched = [i for i in range(sc.TABLE_COUNT) if not sc.edits_for(i)]
    assert untouched
    raw = bytes(range(256)) + bytes(168)
    for i in untouched[:10]:
        assert sc.apply(i, raw) == raw


def test_apply_refuses_unexpected_vanilla():
    e = sc.EDITS[0]
    raw = bytearray(_vanilla_like(e.table))
    base, count, size, etype = sc.SLOT_KINDS[e.kind]
    o = base + size * e.slot + (4 if etype in ('land', 'water') else 0)
    struct.pack_into('<I', raw, o, e.old_species + 1 if e.old_species < sc.MAX_SPECIES else 1)
    with pytest.raises(ValueError, match=e.name):
        sc.apply(e.table, bytes(raw))
    with pytest.raises(ValueError):
        sc.apply(e.table, bytes(100))


def test_parse_roundtrip():
    member = sc.EDITS[0].table
    t = sc.parse_table(sc.apply(member, _vanilla_like(member)))
    for e in sc.edits_for(member):
        v = t['slots'][e.kind][e.slot]
        sp = v if isinstance(v, int) else v[-1]
        assert sp == e.species


def _added_share():
    """(table, species) -> % chance the edits give that species in that table (land at day, water per kind)."""
    share = {}
    for e in sc.EDITS:
        if e.kind == 'land':
            w = sc.LAND_WEIGHTS[e.slot]
        elif e.kind in ('day', 'night'):
            w = 10
        else:
            w = sc.WATER_WEIGHTS[e.kind][e.slot]
        key = (e.name, e.kind if e.kind in sc.WATER_WEIGHTS else 'land', e.species)
        share[key] = share.get(key, 0) + w
    return share


def test_rarity_fits_strength():
    share = _added_share()
    for (name, kind, sp), pct in share.items():
        assert 1 <= pct <= 15, (name, sp, pct)                 # an addition never dominates an area
        if sp in STARTER_BASES:
            # starters (Ilias, v2): before the League but not on the first routes, and rare
            assert name not in sc.POST_GAME and name not in sc.EARLY_UNTOUCHED, (name, sp)
            assert pct <= 5, (name, sp, pct)
    # every starter family that Platinum lacks (or gives only as 1 of 3) is somewhere
    assert STARTER_BASES <= {sp for (_n, _k, sp) in share}


def test_digest_stable():
    assert sc.digest() == sc.digest() and len(sc.digest()) == 40


def test_chances_helper():
    t = sc.parse_table(_vanilla_like(sc.EDITS[0].table))
    for time in ('morning', 'day', 'night'):
        assert sum(sc.chances(t, 'land', time).values()) == 100
    assert sum(sc.chances(t, 'surf').values()) == 100


# ------------------------------------------------------------------------------------ Pokedex area map
def _area_list(maps):
    return struct.pack(f'<{len(maps) + 1}I', *maps, 0)


def test_area_layout_constants():
    assert sc.AREA_MEMBER_COUNT == 4954 and len(sc.AREA_CATEGORIES) == 10
    assert sc.area_member(0, 'dungeon_morning') == 4
    assert sc.area_member(494, 'field_special_natdex') == sc.AREA_MEMBER_COUNT - 1
    for a in sc.AREA_EDITS:
        assert 4 <= sc.area_member(a.species, a.category) < sc.AREA_MEMBER_COUNT


def test_every_added_placement_is_on_the_area_map():
    adds = {(a.species, a.category, a.map) for a in sc.AREA_EDITS if a.op == 'add'}
    for e in sc.EDITS:
        mtype, num = sc.AREA_MAP_OF[e.name]
        if e.kind == 'day':
            times = ('day',)
        elif e.kind == 'night':
            times = ('night',)
        elif e.kind == 'land' and e.slot in (2, 3):
            times = ('morning',)
        else:
            times = ('morning', 'day', 'night')
        for tm in times:
            assert (e.species, f'{mtype}_{tm}', num) in adds, (e, tm)
    # and nothing is added that is not one of our placements
    placed = set()
    for e in sc.EDITS:
        mtype, num = sc.AREA_MAP_OF[e.name]
        placed |= {(e.species, f'{mtype}_{tm}', num) for tm in ('morning', 'day', 'night')}
    assert adds <= placed


def test_area_removals_only_for_replaced_species():
    replaced = {e.old_species for e in sc.EDITS}
    for a in sc.AREA_EDITS:
        if a.op == 'remove':
            assert a.species in replaced, a


def test_apply_area_add_remove_sorted():
    for m in sc.area_members()[:40]:
        edits = sc.area_edits_for(m)
        have = sorted({a.map for a in edits if a.op == 'remove'} | {250})
        new = sc.apply_area(m, _area_list(have))
        words = list(struct.unpack(f'<{len(new) // 4}I', new))
        assert words[-1] == 0 and words[:-1] == sorted(words[:-1])
        for a in edits:
            assert (a.map in words[:-1]) == (a.op == 'add')
        assert 250 in words


def test_apply_area_refuses_mismatch():
    a = next(x for x in sc.AREA_EDITS if x.op == 'add')
    m = sc.area_member(a.species, a.category)
    already = sorted({x.map for x in sc.area_edits_for(m) if x.op == 'remove'} | {a.map})
    with pytest.raises(ValueError):
        sc.apply_area(m, _area_list(already))
    with pytest.raises(ValueError):
        sc.apply_area(m, b'\x01\x00\x00\x00')               # no terminator
    untouched = next(i for i in range(4, sc.AREA_MEMBER_COUNT) if not sc.area_edits_for(i))
    assert sc.apply_area(untouched, _area_list([3, 9])) == _area_list([3, 9])
