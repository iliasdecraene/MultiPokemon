"""NitroFS / NARC / message-bank reader tests. The ROM-backed ones need the player's own Platinum ROM
(dev/states/platinum.nds, or YL_PLATINUM_ROM) and are skipped without it - no Nintendo data is in the repo."""
import base64
import os
import struct
import sys
import time

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "server"))

from yellowlink import ndsrom, pk4, text  # noqa: E402

ROM_PATH = os.environ.get("YL_PLATINUM_ROM", os.path.join(_HERE, "..", "..", "dev", "states", "platinum.nds"))
needs_rom = pytest.mark.skipif(not os.path.exists(ROM_PATH), reason="Platinum ROM not available")


# ---------------------------------------------------------------------------------------------------------
# no ROM needed
# ---------------------------------------------------------------------------------------------------------
def test_lz77_decompress():
    # "ABCABCABCX": literals A B C, then a 6-byte back-reference (disp 3), then literal X
    data = bytes([0x10, 10, 0, 0, 0b00010000]) + b"ABC" + bytes([(6 - 3) << 4, 3 - 1]) + b"X"
    assert ndsrom.lz77_decompress(data) == b"ABCABCABCX"
    assert ndsrom.lz77_decompress(b"\x00plain") == b"\x00plain"


def _encode_bank(strings, seed=0x1234):
    """Inverse of decode_message_bank (for the test)."""
    head = struct.pack("<HH", len(strings), seed)
    table, body = b"", b""
    off = 4 + 8 * len(strings)
    for i, s in enumerate(strings):
        key = ((i + 1) * 596947) & 0xFFFF
        chars = []
        for c in s:
            chars.append(c ^ key)
            key = (key + 18749) & 0xFFFF
        k = (seed * 765 * (i + 1)) & 0xFFFF
        k |= k << 16
        table += struct.pack("<II", (off + len(body)) ^ k, len(s) ^ k)
        body += struct.pack("<%dH" % len(chars), *chars)
    return head + table + body


def test_message_bank_decode():
    strings = [ndsrom.g4_encode("PIKACHU"), ndsrom.g4_encode("Mr. Mime"), ndsrom.g4_encode("")]
    out = ndsrom.decode_message_bank(_encode_bank(strings))
    assert [ndsrom.g4_decode(s) for s in out] == ["PIKACHU", "Mr. Mime", ""]


def _fake_nds(tmp_path):
    """Minimal NDS image: FNT with /a/b.narc, FAT, and a NARC holding two members."""
    narc_members = [b"hello", b"world!!"]
    fat = b"".join(struct.pack("<II", s, s + len(m)) for s, m in
                   zip((0, 8), narc_members))
    gmif = narc_members[0] + b"\0\0\0" + narc_members[1]
    btaf = b"BTAF" + struct.pack("<IHH", 12 + len(fat), 2, 0) + fat
    btnf = b"BTNF" + struct.pack("<I", 16) + struct.pack("<IHH", 4, 0, 1)
    gm = b"GMIF" + struct.pack("<I", 8 + len(gmif)) + gmif
    narc = b"NARC" + struct.pack("<HHIHH", 0xFFFE, 0x0100, 16 + len(btaf) + len(btnf) + len(gm), 16, 3) + btaf + btnf + gm
    # FNT: root dir (id 0xF000) has subdir "a" (0xF001); "a" has file "b.narc" (file id 0)
    root = bytes([0x81]) + b"a" + struct.pack("<H", 0xF001) + b"\0"
    sub = bytes([6]) + b"b.narc" + b"\0"
    fnt_main = struct.pack("<IHH", 16, 0, 2) + struct.pack("<IHH", 16 + len(root), 0, 0xF000)
    fnt = fnt_main + root + sub
    rom = bytearray(0x200)
    fnt_off = len(rom)
    rom += fnt
    while len(rom) % 4:
        rom += b"\0"
    narc_off = len(rom)
    rom += narc
    fat_off = len(rom)
    rom += struct.pack("<II", narc_off, narc_off + len(narc))
    rom[0:12] = b"TEST".ljust(12, b"\0")
    rom[12:16] = b"TEST"
    struct.pack_into("<4I", rom, 0x40, fnt_off, len(fnt), fat_off, 8)
    p = tmp_path / "t.nds"
    p.write_bytes(bytes(rom))
    return str(p)


def test_nitrofs_and_narc(tmp_path):
    r = ndsrom.NDSRom(_fake_nds(tmp_path))
    assert r.files() == ["/a/b.narc"] and r.game_code == "TEST"
    n = r.narc("a/b.narc")
    assert len(n) == 2 and n[0] == b"hello" and n[1] == b"world!!" and n.member(1, 2, 3) == b"rld"
    with pytest.raises(ndsrom.NDSError):
        r.file("/missing")
    with pytest.raises(ndsrom.NDSError):
        ndsrom.PlatinumData(_fake_nds(tmp_path))          # not Platinum
    r.close()


# ---------------------------------------------------------------------------------------------------------
# the player's Platinum ROM
# ---------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rom():
    return ndsrom.platinum(ROM_PATH)


@needs_rom
def test_header_and_files():
    r = ndsrom.NDSRom(ROM_PATH)
    assert r.game_code == "CPUE"
    for path in (ndsrom.PERSONAL_NARC, ndsrom.GROWTH_NARC, ndsrom.MOVES_NARC, ndsrom.MSG_NARC, ndsrom.EVO_NARC):
        assert path in r.files()
    r.close()


@needs_rom
def test_load_is_fast_and_cached():
    ndsrom._cache.clear()
    t = time.perf_counter()
    a = ndsrom.platinum(ROM_PATH)
    assert time.perf_counter() - t < 2.0
    assert ndsrom.platinum(ROM_PATH) is a


@needs_rom
def test_exp_tables_match_formulas(rom):
    for rate in range(6):
        assert rom.exp_tables[rate] == [pk4.exp_for(rate, n) for n in range(101)]
    for dex in (1, 25, 150, 387, 493):
        for lv in (1, 2, 50, 99, 100):
            e = rom.exp_for_level(dex, lv)
            assert rom.level_for_exp(dex, e) == lv
            if lv > 1:
                assert rom.level_for_exp(dex, e - 1) == lv - 1


@needs_rom
def test_species_data_shape(rom):
    for dex in range(1, ndsrom.NUM_SPECIES + 1):
        p = rom.info(dex)
        name = rom.species_name(dex)
        assert name and name == name.upper() and "?" not in name
        assert 0 <= p.growth_rate <= 5 and p.abilities[0] and all(p.base_stats)
    assert rom.species_name(25) == "PIKACHU" and rom.species_name(122) == "MR. MIME"
    assert rom.gender_ratio(132) == ndsrom.GENDERLESS and rom.abilities(132)[0] == pk4.LIMBER
    assert rom.move_pp(pk4.TRANSFORM) == 10 and rom.move_name(1) == "Pound"
    assert rom.location_name(pk4.LOC_PAL_PARK) == "Pal Park"


@needs_rom
def test_item_ids(rom):
    from yellowlink_server import pcstore
    names = {pcstore.G4_KINGS_ROCK: "King’s Rock", pcstore.G4_EVERSTONE: "Everstone", pcstore.G4_METAL_COAT:
             "Metal Coat", pcstore.G4_DRAGON_SCALE: "Dragon Scale", pcstore.G4_UP_GRADE: "Up-Grade",
             pcstore.G4_PROTECTOR: "Protector", pcstore.G4_ELECTIRIZER: "Electirizer", pcstore.G4_MAGMARIZER:
             "Magmarizer", pcstore.G4_DUBIOUS_DISC: "Dubious Disc", pcstore.G4_REAPER_CLOTH: "Reaper Cloth",
             pcstore.G4_DEEP_SEA_TOOTH: "DeepSeaTooth", pcstore.G4_DEEP_SEA_SCALE: "DeepSeaScale", 4: "Poké Ball"}
    for item, name in names.items():
        assert rom.item_name(item) == name
    for g4 in pk4.G2_TO_G4_ITEM.values():
        assert rom.item_name(g4) not in ("?", "")


@needs_rom
def test_server_trade_evolutions_match_rom(rom):
    from yellowlink_server import pcstore
    want = {sp: sorted((t, item) for t, item, _, _ in evos) for sp, evos in pcstore.G4_TRADE_EVOS.items()}
    assert {sp: sorted(v) for sp, v in rom.trade_evolutions().items()} == want
    for evos in pcstore.G4_TRADE_EVOS.values():
        for target, _, name, abilities in evos:
            assert rom.species_name(target) == name and tuple(rom.abilities(target)) == abilities


def _g1(dex, internal, dvs=0x8C3A, otid=0xBEEF, moves=(33, 0, 0, 0), exp=1000):
    d = bytearray(33)
    d[0], d[3], d[7] = internal, 10, 45
    d[8:12] = bytes(moves)
    d[12], d[13] = otid >> 8, otid & 0xFF
    d[14:17] = exp.to_bytes(3, "big")
    d[27], d[28] = dvs >> 8, dvs & 0xFF
    d[29] = 35
    return bytes(d) + text.encode("RED", 11) + text.encode("NAMEME", 11)


@needs_rom
def test_conversion_with_real_rom(rom):
    for dex in (1, 25, 63, 122, 150):
        for dvs in (0x0000, 0x7777, 0xEAAA, 0xFFFF):
            b = pk4.from_gen1(_g1(dex, 1, dvs=dvs), rom, dex=dex)
            pk = pk4.PK4.from_encrypted(b)
            assert pk.ability in rom.abilities(dex) and pk.gender_bits == pk4.gender_of(rom.gender_ratio(dex), pk.pid)
            assert pk.shiny == (dvs == 0xEAAA) and pk.pp[0] == rom.move_pp(33)
            assert pk.level(rom) == rom.level_for_exp(dex, 1000)
            e = pk4.entry_from_pk4(b, rom)
            assert e["dex"] == dex and e["nick"] == "NAMEME" and base64.b64decode(e["gen4"]) == b
            r2 = pk4.to_gen2(b, rom)
            assert r2[0] == dex and r2[21:23] == bytes([dvs >> 8, dvs & 0xFF])
    s = pk4.standin({"id": "x", "nick": "Turtwig", "level": 30}, rom)
    assert pk4.is_standin(s) and pk4.PK4.from_encrypted(s).level(rom) == 30
