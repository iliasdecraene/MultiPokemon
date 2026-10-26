"""Complete Sinnoh ROM builder (sinnoh_rom.py) + encounter table format (encdata.py), without the real ROM: a tiny
synthetic .nds with the same NitroFS layout and a fake pl_enc_data.narc."""
import hashlib
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dev", "sinnoh"))

from yellowlink import encdata, ndsrom, platinum, sinnoh_rom  # noqa: E402
import test_spec  # noqa: E402  (dev/sinnoh/test_spec.py: same interface as sinnoh_complete.py)

SlotEdit, RateEdit = test_spec.SlotEdit, test_spec.RateEdit


# --- synthetic data -----------------------------------------------------------------------------------------
def table(seed: int) -> bytes:
    """A plausible 424-byte table: land slots (level, species), species lists, water slots with pad bytes."""
    b = bytearray(encdata.TABLE_SIZE)
    struct.pack_into("<I", b, 0, 30)
    for i in range(12):
        struct.pack_into("<II", b, 4 + 8 * i, 2 + i % 3, 396 + (seed + i) % 5)
    for kind in ("swarm", "day", "night", "radar", "ruby", "sapphire", "emerald", "firered", "leafgreen"):
        base, count, size, _ = encdata.SLOT_KINDS[kind]
        for i in range(count):
            struct.pack_into("<I", b, base + size * i, 300 + seed + i)
    for kind in ("surf", "old_rod", "good_rod", "super_rod"):
        struct.pack_into("<I", b, encdata.RATE_OFFSETS[kind], 10 if kind != "surf" else 0)
        base = encdata.SLOT_KINDS[kind][0]
        for i in range(5):
            struct.pack_into("<BBHI", b, base + 8 * i, 20 + i, 10 + i, 0xBEEF, 129 + i)
    return bytes(b)


def make_nds(files: dict, pad_to: int = 0x40000) -> bytes:
    """A minimal NDS image with a NitroFS (FNT/FAT) holding `files` {"/a/b/c": bytes} (one file per dir chain)."""
    # directory tree
    dirs = {"/": []}
    for path in files:
        parts = path.strip("/").split("/")
        for d in range(1, len(parts)):
            parent = "/" + "/".join(parts[:d - 1]) + ("/" if d > 1 else "")
            parent = parent.replace("//", "/")
            name = "/" + "/".join(parts[:d]) + "/"
            dirs.setdefault(name, [])
            if ("d", parts[d - 1], name) not in dirs[parent]:
                dirs[parent].append(("d", parts[d - 1], name))
        parent = ("/" + "/".join(parts[:-1]) + "/").replace("//", "/")
        dirs.setdefault(parent, []).append(("f", parts[-1], path))
    order = list(dirs)
    ids = {d: 0xF000 + i for i, d in enumerate(order)}
    file_ids, fid = {}, 0
    main, subs = [], []
    for d in order:
        first = fid
        sub = b""
        for kind, name, target in dirs[d]:
            if kind == "f":
                sub += bytes([len(name)]) + name.encode()
                file_ids[target] = fid
                fid += 1
            else:
                sub += bytes([0x80 | len(name)]) + name.encode() + struct.pack("<H", ids[target])
        subs.append(sub + b"\0")
        main.append(first)
    main_size = 8 * len(order)
    fnt = b""
    off = main_size
    for i, d in enumerate(order):
        fnt += struct.pack("<IHH", off, main[i], len(order) if i == 0 else 0xF000)
        off += len(subs[i])
    fnt += b"".join(subs)
    fnt_off = 0x200
    fat_off = (fnt_off + len(fnt) + 0x1FF) & ~0x1FF
    data_off = (fat_off + 8 * len(files) + 0x1FF) & ~0x1FF
    blob = bytearray(max(pad_to, data_off + sum(len(v) + 0x200 for v in files.values())))
    fat = bytearray(8 * len(files))
    pos = data_off
    for path, content in files.items():
        i = file_ids[path]
        struct.pack_into("<II", fat, 8 * i, pos, pos + len(content))
        blob[pos:pos + len(content)] = content
        pos = (pos + len(content) + 0x1FF) & ~0x1FF
    blob[0:12] = b"POKEMON PL\0\0"
    blob[12:16] = b"CPUE"
    struct.pack_into("<4I", blob, 0x40, fnt_off, len(fnt), fat_off, len(fat))
    blob[fnt_off:fnt_off + len(fnt)] = fnt
    blob[fat_off:fat_off + len(fat)] = fat
    for i in range(0x1000, len(blob) - 0x400, 0x777):      # some "code" bytes everywhere else
        if blob[i] == 0:
            blob[i] = (i * 31) & 0xFF
    return bytes(blob)


TABLES = [table(i) for i in range(encdata.TABLE_COUNT)]
NARC = encdata.make_narc(TABLES)


@pytest.fixture
def fake_rom(tmp_path, monkeypatch):
    img = make_nds({encdata.NARC_PATH: NARC, "/poketool/other.narc": b"OTHER" * 50})
    p = tmp_path / "platinum.nds"
    p.write_bytes(img)
    sha = hashlib.sha1(img).hexdigest()
    monkeypatch.setattr(sinnoh_rom, "VANILLA_SHA1", sha)
    return p


def spec_with(edits=(), rates=(), version=7):
    class S:
        SPEC_VERSION = version
        EDITS = tuple(edits)
        RATE_EDITS = tuple(rates)

        @staticmethod
        def apply(i, data):
            return encdata.apply_edits(i, data, S.EDITS, S.RATE_EDITS)
    return S


def land_edit(t=3, slot=0, new=25, lvl=9):
    sp, old = encdata.parse(TABLES[t])["land"][slot]
    return SlotEdit(t, "t%d" % t, "land", slot, new, lvl, old_species=sp, old_level=old)


# --- encdata ------------------------------------------------------------------------------------------------
def test_parse_layout():
    t = encdata.parse(TABLES[5])
    assert t["rates"] == {"land": 30, "surf": 0, "old_rod": 10, "good_rod": 10, "super_rod": 10}
    assert t["land"][0] == (396 + 5 % 5, 2) and len(t["land"]) == 12
    assert t["day"] == [305, 306] and t["radar"] == [305, 306, 307, 308]
    assert t["super_rod"][4] == (133, 14, 24)
    assert encdata.slot_offset("super_rod", 4) == 384 + 32 and encdata.slot_offset("land", 11) == 92
    with pytest.raises(ValueError):
        encdata.slot_offset("land", 12)
    with pytest.raises(ValueError):
        encdata.parse(b"\0" * 10)
    assert sum(encdata.LAND_WEIGHTS) == 100 and all(sum(w) == 100 for w in encdata.WATER_WEIGHTS.values())


def test_narc_roundtrip():
    offs = encdata.narc_member_offsets(NARC)
    assert len(offs) == encdata.TABLE_COUNT
    assert all(NARC[s:e] == TABLES[i] for i, (s, e) in enumerate(offs))
    with pytest.raises(ValueError):
        encdata.narc_member_offsets(b"NOPE" + NARC[4:])


def test_apply_edits_kinds_and_checks():
    t = TABLES[4]
    p = encdata.parse(t)
    edits = [
        SlotEdit(4, "t4", "land", 11, 280, 30, old_species=p["land"][11][0], old_level=p["land"][11][1]),
        SlotEdit(4, "t4", "night", 1, 198, old_species=p["night"][1]),
        SlotEdit(4, "t4", "good_rod", 2, 223, None, 25, 35, old_species=p["good_rod"][2][0],
                 old_min=p["good_rod"][2][1], old_max=p["good_rod"][2][2]),
        SlotEdit(3, "t3", "land", 0, 1, 5, old_species=999, old_level=1),        # other table: ignored here
    ]
    rates = [RateEdit(4, "t4", "surf", 10, 0)]
    out = encdata.apply_edits(4, t, edits, rates)
    q = encdata.parse(out)
    assert q["land"][11] == (280, 30) and q["night"][1] == 198 and q["good_rod"][2] == (223, 25, 35)
    assert q["rates"]["surf"] == 10
    o = encdata.slot_offset("good_rod", 2)
    assert out[o + 2:o + 4] == t[o + 2:o + 4] == struct.pack("<H", 0xBEEF)        # pad bytes kept
    changed = [i for i in range(len(t)) if t[i] != out[i]]
    allowed = set(range(encdata.slot_offset("land", 11), encdata.slot_offset("land", 11) + 8)) | \
        set(range(encdata.slot_offset("night", 1), encdata.slot_offset("night", 1) + 4)) | \
        {o, o + 1, o + 4, o + 5, o + 6, o + 7} | set(range(204, 208))
    assert set(changed) <= allowed
    with pytest.raises(ValueError, match="expected"):          # wrong vanilla value -> refuse
        encdata.apply_edits(3, TABLES[3], edits)
    with pytest.raises(ValueError, match="twice"):
        encdata.apply_edits(4, t, edits[:1] * 2)
    with pytest.raises(ValueError, match="rate"):
        encdata.apply_edits(4, t, (), [RateEdit(4, "t4", "surf", 10, 5)])
    with pytest.raises(ValueError, match="bad"):
        encdata.apply_edits(4, t, [edits[0]._replace(species=600)])


def test_test_spec_is_valid_against_vanilla_values():
    # dev/sinnoh/test_spec.py uses the real vanilla values of Route 201/202 (pret JSON) -> on a fake table they fail
    with pytest.raises(ValueError):
        test_spec.apply(140, TABLES[140])
    assert test_spec.apply(5, TABLES[5]) == TABLES[5]


# --- builder ------------------------------------------------------------------------------------------------
def test_patch_narc_only_edited_members():
    spec = spec_with([land_edit(3), land_edit(100, 5, 280, 40)])
    new = sinnoh_rom.patch_narc(NARC, spec)
    assert len(new) == len(NARC)
    offs = encdata.narc_member_offsets(new)
    diff = [i for i, (s, e) in enumerate(offs) if new[s:e] != NARC[s:e]]
    assert diff == [3, 100]
    assert new[:offs[0][0]] == NARC[:offs[0][0]]
    with pytest.raises(sinnoh_rom.BuildError):
        sinnoh_rom.patch_narc(encdata.make_narc(TABLES[:10]), spec)


def test_build_deterministic_and_verified(fake_rom, tmp_path):
    spec = spec_with([land_edit(3), land_edit(7, 1)])
    before = fake_rom.read_bytes()
    a = sinnoh_rom.build(fake_rom, tmp_path / "a.nds", spec, expected=None)
    b = sinnoh_rom.build(fake_rom, tmp_path / "b.nds", spec, expected=None)
    assert a == b == hashlib.sha1((tmp_path / "a.nds").read_bytes()).hexdigest()
    assert fake_rom.read_bytes() == before                         # the original is never written
    out = (tmp_path / "a.nds").read_bytes()
    rom = ndsrom.NDSRom(str(fake_rom))
    start, size = rom.extent(encdata.NARC_PATH)
    rom.close()
    diff = [i for i in range(len(out)) if out[i] != before[i]]
    assert diff and all(start <= i < start + size for i in diff)
    r2 = ndsrom.NDSRom(str(tmp_path / "a.nds"))
    assert encdata.parse(r2.narc(encdata.NARC_PATH)[3])["land"][0] == (25, 9)
    r2.close()
    # expected SHA1 enforced; nothing left behind on failure
    with pytest.raises(sinnoh_rom.BuildError, match="expected"):
        sinnoh_rom.build(fake_rom, tmp_path / "c.nds", spec, expected="0" * 40)
    assert not (tmp_path / "c.nds").exists() and not (tmp_path / "c.nds.part").exists()
    assert sinnoh_rom.build(fake_rom, tmp_path / "d.nds", spec, expected=a) == a


def test_build_refuses_other_roms_and_bad_specs(fake_rom, tmp_path, monkeypatch):
    spec = spec_with([land_edit(3)])
    other = tmp_path / "other.nds"
    other.write_bytes(fake_rom.read_bytes()[:-1] + b"X")
    with pytest.raises(sinnoh_rom.BuildError, match="isn't"):
        sinnoh_rom.build(other, tmp_path / "x.nds", spec, expected=None)
    bad = spec_with([land_edit(3)._replace(old_species=1)])
    with pytest.raises(ValueError):
        sinnoh_rom.build(fake_rom, tmp_path / "y.nds", bad, expected=None)
    assert not (tmp_path / "y.nds").exists() and not (tmp_path / "y.nds.part").exists()
    monkeypatch.setattr(sinnoh_rom, "load_spec", lambda: None)
    with pytest.raises(sinnoh_rom.BuildError, match="missing"):
        sinnoh_rom.build(fake_rom, tmp_path / "z.nds")
    with pytest.raises(sinnoh_rom.BuildError, match="no checked version"):
        sinnoh_rom.build(fake_rom, tmp_path / "z.nds", spec)        # spec without an EXPECTED entry


def test_ensure_builds_once_and_repairs(fake_rom, tmp_path, monkeypatch):
    spec = spec_with([land_edit(9)])
    sha = sinnoh_rom.build(fake_rom, tmp_path / "ref.nds", spec, expected=None)
    monkeypatch.setitem(sinnoh_rom.EXPECTED, sinnoh_rom.spec_digest(spec), (sha, 7))
    dest = tmp_path / "roms" / sinnoh_rom.FILE_NAME
    calls = []
    real_build = sinnoh_rom.build
    monkeypatch.setattr(sinnoh_rom, "build", lambda *a, **k: calls.append(1) or real_build(*a, **k))
    assert sinnoh_rom.ensure(fake_rom, dest, spec) == (str(dest), sha)
    assert sinnoh_rom.ensure(fake_rom, dest, spec) == (str(dest), sha)
    assert len(calls) == 1                                         # second call: just the SHA1 check
    data = bytearray(dest.read_bytes())
    data[5000] ^= 0xFF                                             # damaged copy -> made again
    dest.write_bytes(bytes(data))
    assert sinnoh_rom.ensure(fake_rom, dest, spec) == (str(dest), sha)
    assert len(calls) == 2 and hashlib.sha1(dest.read_bytes()).hexdigest() == sha
    assert sha in [s for s, _v in sinnoh_rom.known_builds()]


def test_spec_digest_tracks_content():
    a = spec_with([land_edit(3)])
    b = spec_with([land_edit(3, lvl=10)])
    c = spec_with([land_edit(3)], version=8)
    assert len({sinnoh_rom.spec_digest(s) for s in (a, b, c)}) == 3
    assert sinnoh_rom.spec_digest(a) == sinnoh_rom.spec_digest(spec_with([land_edit(3)]))
    assert sinnoh_rom.spec_digest(test_spec) in sinnoh_rom.DEV_TEST_EXPECTED


def test_shipped_spec_has_a_checked_rom():
    """Whenever client/yellowlink/sinnoh_complete.py changes, its ROM SHA1 must be recomputed
    (python dev/sinnoh/build_rom.py --update) - otherwise players would fall back to plain Platinum."""
    spec = sinnoh_rom.load_spec()
    if spec is None:
        pytest.skip("sinnoh_complete.py not written yet")
    assert sinnoh_rom.spec_digest(spec) in sinnoh_rom.EXPECTED, "run: python dev/sinnoh/build_rom.py --update"
    assert sinnoh_rom.EXPECTED[sinnoh_rom.spec_digest(spec)][1] == spec.SPEC_VERSION
    for e in spec.EDITS:                                           # sane rows (the full check: check_plan.py)
        assert 0 <= e.table < encdata.TABLE_COUNT and e.kind in encdata.SLOT_KINDS
        assert 1 <= e.species <= 493


def test_variant_table():
    assert platinum.VARIANTS[platinum.VANILLA_SHA1].key == "vanilla"
    assert platinum.SUPPORTED_SHA1S[0] == platinum.PLATINUM_SHA1
    for sha, ver in sinnoh_rom.known_builds():
        v = platinum.variant_of(sha)
        assert v.key == "sinnoh" and v.spec_version == ver
        assert platinum.same_code(sha, platinum.VANILLA_SHA1)
    assert platinum.variant_of("0" * 40) is None
    assert not platinum.same_code("0" * 40, platinum.VANILLA_SHA1)
