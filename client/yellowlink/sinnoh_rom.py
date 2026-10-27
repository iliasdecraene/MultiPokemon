"""Complete Sinnoh: YellowLink's own version of Pokémon Platinum, made on the player's computer from their own ROM.

The player adds their vanilla Platinum (US rev 0, `VANILLA_SHA1`); YellowLink copies it into its data folder
(`roms/platinum-sinnoh.nds`) and changes only data files inside the copy (SPEC.md section 11):

  * `/fielddata/encountdata/pl_enc_data.narc` - the wild-encounter tables (183 x 424 bytes, edited in place, same
    size), from the spec `sinnoh_complete.py` (EDITS / RATE_EDITS);
  * `/application/zukanlist/zkn_data/zukan_enc_platinum.narc` - the Pokédex AREA map lists (spec AREA_EDITS). Its
    lists change length, so the rebuilt NARC (same structure, `encdata.make_narc`) is written after the last file of
    the vanilla ROM (inside the 128 MB image, which is 0xFF there) and its FAT entry + the header's "used ROM size"
    and header CRC are updated. The vanilla copy stays where it was (an old savestate's cached FAT still finds it);
  * `/fielddata/script/scr_seq.narc` - 2 bytes in each of the three Regi ruins' scripts (`ENGINE_EDITS`, ours, not
    the design spec's): the statue no longer asks for an event ("fateful encounter") Regigigas, so the Regis and
    then Snowpoint Temple's Regigigas can be caught in one save.

Game code is untouched, so every address/hook verified on vanilla holds for the copy (platinum.VARIANTS treats it
like vanilla).

Deterministic: the same vanilla ROM + the same spec always give the same file, whose SHA1 is stored below per spec
version (`EXPECTED`) and checked after every build. The original file is only read, never written. Nothing from the
ROM is ever stored in the repo or sent anywhere (CLAUDE.md "Never distribute ROMs").

    path = sinnoh_rom.ensure(vanilla_path, dest_path)   # builds once, then just checks the SHA1 (~0.3 s)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
from pathlib import Path
from typing import NamedTuple

from . import encdata, ndsrom

log = logging.getLogger("yellowlink.sinnoh_rom")

VANILLA_SHA1 = "ce81046eda7d232513069519cb2085349896dec7"     # Pokémon Platinum (USA) rev 0
FILE_NAME = "platinum-sinnoh.nds"

# spec digest (spec_digest()) -> (SHA1 of the finished ROM, spec version). One line per released spec version (once
# released, old lines stay: platinum.VARIANTS lists every build as a verified variant, so older resume snapshots stay
# loadable).
# A unit test checks that the spec in the repo has an entry here. Recompute: python dev/sinnoh/build_rom.py --update
EXPECTED = {
    "82f977085def05172091ef4b7b71f33695467ac3": ("8abeae7b6039637d0671fc3859a0952f8997054b", 2),
}
# Bumped when the builder itself changes what it writes for the same spec (part of the digest).
BUILD_FORMAT = 2
AREA_NARC_PATH = "/application/zukanlist/zkn_data/zukan_enc_platinum.narc"
SCRIPT_NARC_PATH = "/fielddata/script/scr_seq.narc"
ROM_ALIGN = 0x200                 # Platinum's files start on 512-byte boundaries
HDR_USED_SIZE = 0x80              # NDS header: total used ROM size (u32)
HDR_CRC = 0x15E                   # NDS header: CRC16 of bytes 0..0x15D


class ScriptEdit(NamedTuple):
    """Bytes in one member of a script NARC: `old` must be found at `offset` (else the build refuses)."""
    narc: str
    member: int
    offset: int
    old: bytes
    new: bytes
    why: str


# The Regi ruins (pret res/field/scripts/scripts_{iron,iceberg,rock_peak}_ruins.s, scr_seq members 392/394/396):
#     CheckPartyHasFatefulEncounterRegigigas VAR_RESULT        2B 03 0C 80   (0 or 1)
#     GoToIfEq VAR_RESULT, 0, <..._FromSomewhereSomethingSpokeOut>
#        = CompareVarToValue VAR_RESULT, 0                  11 00 0C 80 00 00
#          GoToIf 1 (equal), <offset>                       1C 00 01 ...
# The compared value 0 becomes 2 (VAR_RESULT is never 2): the statue no longer needs an event Regigigas; everything
# else (Hall of Fame, the dot puzzle, the battle, "caught" state) is the game's own script. Snowpoint Temple's
# Regigigas only needs the three Regis in the party (CheckHasAllLegendaryTitansInParty), so the chain is complete.
_REGI_CTX = bytes.fromhex("2b030c80" "11000c80" "0000" "1c0001")
ENGINE_EDITS = tuple(
    ScriptEdit(SCRIPT_NARC_PATH, member, 106, _REGI_CTX, _REGI_CTX[:8] + b"\x02\x00" + _REGI_CTX[10:],
               f"{name}: the statue doesn't ask for an event Regigigas")
    for member, name in ((392, "iron_ruins (Registeel)"), (394, "iceberg_ruins (Regice)"),
                         (396, "rock_peak_ruins (Regirock)")))

# Dev only (YL_SINNOH_SPEC=test): the tiny stand-in spec dev/sinnoh/test_spec.py, for the harnesses.
DEV_TEST_EXPECTED = {"ccdd90dc59ec74ae79101f89d788435a28d16943": ("2c1f3a8739f984fe7cc64a0ffb2e87dc6738fd03", 0)}
_DEV_SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dev", "sinnoh", "test_spec.py")


def dev_test_spec_on() -> bool:
    return os.environ.get("YL_SINNOH_SPEC", "") == "test" and os.path.exists(_DEV_SPEC)


def _expected() -> dict:
    return {**EXPECTED, **DEV_TEST_EXPECTED} if dev_test_spec_on() else EXPECTED


def known_builds() -> list:
    """[(sha1, spec version)] of every Complete Sinnoh build this YellowLink knows (platinum.VARIANTS)."""
    return list(_expected().values())


class BuildError(Exception):
    """Something the player should hear about in plain words (the message is shown as is)."""


def load_spec():
    """The spec in use: client/yellowlink/sinnoh_complete.py (None if it isn't there)."""
    if dev_test_spec_on():
        import importlib.util
        sp = importlib.util.spec_from_file_location("yl_sinnoh_test_spec", _DEV_SPEC)
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        return mod
    try:
        from . import sinnoh_complete
    except ImportError:
        return None
    return sinnoh_complete


def _engine(engine_edits):
    return ENGINE_EDITS if engine_edits is None else tuple(engine_edits)


def spec_digest(spec, engine_edits=None) -> str:
    """SHA1 over everything that changes the bytes: spec version + slot/rate/area edits, our script edits and the
    builder format."""
    def rows(seq):
        return [list(e) for e in sorted(seq or (), key=lambda e: tuple(str(x) for x in e))]
    eng = [[e.narc, e.member, e.offset, e.old.hex(), e.new.hex()] for e in _engine(engine_edits)]
    blob = {"version": spec.SPEC_VERSION, "edits": rows(getattr(spec, "EDITS", ())),
            "rates": rows(getattr(spec, "RATE_EDITS", ()))}
    if getattr(spec, "AREA_EDITS", ()) or eng:       # (digests of v1-style specs stay what they were)
        blob.update(area=rows(getattr(spec, "AREA_EDITS", ())), engine=eng, format=BUILD_FORMAT)
    return hashlib.sha1(json.dumps(blob, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def expected_sha1(spec, engine_edits=None) -> str | None:
    hit = _expected().get(spec_digest(spec, engine_edits))
    return hit[0] if hit else None


def patch_narc(narc: bytes, spec) -> bytes:
    """The edited NARC: every member passed through spec.apply(i, member). Same length, same member layout."""
    offsets = encdata.narc_member_offsets(narc)
    if len(offsets) != encdata.TABLE_COUNT or any(e - s != encdata.TABLE_SIZE for s, e in offsets):
        raise BuildError("Your Platinum's wild Pokémon file doesn't look like the one YellowLink knows.")
    out = bytearray(narc)
    for i, (s, e) in enumerate(offsets):
        new = spec.apply(i, bytes(narc[s:e]))
        if len(new) != encdata.TABLE_SIZE:
            raise ValueError(f"spec.apply changed the size of table {i}")
        out[s:e] = new
    return bytes(out)


def patch_area_narc(narc: bytes, spec) -> bytes:
    """The Pokédex area NARC with spec.apply_area() on every member the spec changes (other members byte-identical;
    re-packed with encdata.make_narc, which reproduces the game's file exactly - checked first)."""
    offsets = encdata.narc_member_offsets(narc)
    members = [bytes(narc[s:e]) for s, e in offsets]
    count = getattr(spec, "AREA_MEMBER_COUNT", None)
    if (count is not None and len(members) != count) or encdata.make_narc(members) != narc:
        raise BuildError("Your Platinum's Pokédex area file doesn't look like the one YellowLink knows.")
    for i in spec.area_members():
        members[i] = spec.apply_area(i, members[i])
    return encdata.make_narc(members)


def patch_scripts(narc: bytes, edits) -> bytes:
    """ENGINE_EDITS rows for one script NARC (in place, same size). Refuses when the vanilla bytes differ."""
    offsets = encdata.narc_member_offsets(narc)
    out = bytearray(narc)
    for e in edits:
        if len(e.old) != len(e.new) or not 0 <= e.member < len(offsets):
            raise ValueError(f"bad script edit {e.why}")
        s, end = offsets[e.member]
        a = s + e.offset
        if a + len(e.old) > end or bytes(out[a:a + len(e.old)]) != e.old:
            raise BuildError("Your Platinum's scripts don't look like the ones YellowLink knows (%s)." % e.why)
        out[a:a + len(e.new)] = e.new
    return bytes(out)


def crc16(data: bytes) -> int:
    """CRC-16 (poly 0xA001 reflected, init 0xFFFF): the NDS header checksum at 0x15E."""
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c


def _sha1(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _compare_outside(a, b, lo: int, hi: int) -> bool:
    """Files a and b are equal everywhere except maybe in [lo, hi)."""
    return _compare_except(a, b, [(lo, hi)])


def _compare_except(a, b, ranges) -> bool:
    """Files a and b have the same size and are equal everywhere outside the [lo, hi) ranges."""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    ranges = sorted((lo, hi) for lo, hi in ranges if hi > lo)
    with open(a, "rb") as fa, open(b, "rb") as fb:
        pos = 0
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if not x and not y:
                return True
            if x != y:
                end, cur = pos + len(x), pos
                for lo, hi in ranges + [(end, end)]:      # compare the parts of this chunk outside the ranges
                    if hi <= cur and lo < end:
                        continue
                    seg_end = min(lo, end)
                    if cur < seg_end and x[cur - pos:seg_end - pos] != y[cur - pos:seg_end - pos]:
                        return False
                    cur = max(cur, min(hi, end))
                    if cur >= end:
                        break
            pos += len(x)


class Plan(NamedTuple):
    """What a build writes: [(offset, bytes)] and the byte ranges allowed to differ from vanilla."""
    writes: list
    allowed: list


def plan(src, spec, engine_edits=None) -> Plan:
    """Compute every write for the Complete Sinnoh copy of the vanilla ROM `src` (nothing is written here)."""
    rom = ndsrom.NDSRom(str(src))
    try:
        writes, allowed = [], []
        # 1. wild tables, in place
        start, size = rom.extent(encdata.NARC_PATH)
        writes.append((start, patch_narc(rom.file(encdata.NARC_PATH), spec)))
        allowed.append((start, start + size))
        # 2. our script edits, in place (only the edited bytes may differ)
        edits = _engine(engine_edits)
        by_narc = {}
        for e in edits:
            by_narc.setdefault(e.narc, []).append(e)
        for path, rows in by_narc.items():
            s0, n0 = rom.extent(path)
            old = rom.file(path)
            new = patch_scripts(old, rows)
            writes.append((s0, new))
            allowed += [(s0 + i, s0 + i + 1) for i in range(n0) if old[i] != new[i]]
        # 3. the Pokédex area map: rebuilt, written after the vanilla ROM's last file, FAT + header updated
        if getattr(spec, "AREA_EDITS", ()):
            path = getattr(spec, "AREA_NARC_PATH", AREA_NARC_PATH)
            old = rom.file(path)
            new = patch_area_narc(old, spec)
            hdr = bytearray(rom._read(0, 0x200))
            used, = struct.unpack_from("<I", hdr, HDR_USED_SIZE)
            fat_off, = struct.unpack_from("<I", hdr, 0x48)
            fsize = os.path.getsize(src)
            at = (used + ROM_ALIGN - 1) & ~(ROM_ALIGN - 1)
            if at + len(new) > fsize or rom._read(used, at + len(new) - used) != b"\xff" * (at + len(new) - used):
                raise BuildError("Your Platinum file has no free room for the Pokédex area data.")
            fid = rom.file_id(path)
            writes.append((at, new))
            writes.append((fat_off + 8 * fid, struct.pack("<II", at, at + len(new))))
            struct.pack_into("<I", hdr, HDR_USED_SIZE, at + len(new))
            struct.pack_into("<H", hdr, HDR_CRC, crc16(bytes(hdr[:HDR_CRC])))
            writes.append((HDR_USED_SIZE, bytes(hdr[HDR_USED_SIZE:HDR_USED_SIZE + 4])))
            writes.append((HDR_CRC, bytes(hdr[HDR_CRC:HDR_CRC + 2])))
            allowed += [(at, at + len(new)), (fat_off + 8 * fid, fat_off + 8 * fid + 8),
                        (HDR_USED_SIZE, HDR_USED_SIZE + 4), (HDR_CRC, HDR_CRC + 2)]
        return Plan(writes, allowed)
    finally:
        rom.close()


def build(src, dest, spec=None, *, expected: str | None = "auto", sha1_of_src: str | None = None,
          engine_edits=None) -> str:
    """Make `dest` from the vanilla ROM `src` with `spec` (default: sinnoh_complete). Returns the new file's SHA1.

    Checks: src is the supported vanilla ROM; every write reads back; the copy equals vanilla outside the planned
    ranges (compared byte by byte); the result's SHA1 equals `expected` ("auto" = EXPECTED for this spec; None =
    don't check, for the dev tool that computes it). Writes `dest.part` first and renames it, so a crash never
    leaves a half-made ROM under the real name."""
    spec = spec or load_spec()
    if spec is None:
        raise BuildError("The Complete Sinnoh data is missing from this YellowLink.")
    if expected == "auto":
        expected = expected_sha1(spec, engine_edits)
        if expected is None:
            raise BuildError("This YellowLink's Complete Sinnoh data has no checked version yet.")
    src, dest = Path(src), Path(dest)
    if (sha1_of_src or _sha1(src)) != VANILLA_SHA1:
        raise BuildError("That isn't the Pokémon Platinum (USA) file YellowLink supports.")
    p = plan(src, spec, engine_edits)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        shutil.copyfile(src, part)
        with open(part, "r+b") as f:
            for off, data in p.writes:
                f.seek(off)
                f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(part, "rb") as f:
            for off, data in p.writes:
                f.seek(off)
                if f.read(len(data)) != data:
                    raise BuildError("Making Complete Sinnoh went wrong (the new data didn't write).")
        if not _compare_except(src, part, p.allowed):
            raise BuildError("Making Complete Sinnoh went wrong (the copy differs outside the data it changes).")
        got = _sha1(part)
        if expected is not None and got != expected:
            raise BuildError("Making Complete Sinnoh went wrong (the result isn't the expected file).")
        os.replace(part, dest)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    log.info("built %s (spec v%s, %d slot edits, %d area edits, %d script edits) sha1 %s", dest, spec.SPEC_VERSION,
             len(getattr(spec, "EDITS", ())), len(getattr(spec, "AREA_EDITS", ())), len(_engine(engine_edits)), got)
    return got


def ensure(src, dest, spec=None, sha1=None, engine_edits=None) -> tuple:
    """(path, sha1) of the Complete Sinnoh ROM: `dest` if it is already the expected file, else built now.
    `sha1` = a function path -> SHA1 (library.sha1_file: cached per path/size/mtime). Raises BuildError."""
    spec = spec or load_spec()
    if spec is None:
        raise BuildError("The Complete Sinnoh data is missing from this YellowLink.")
    want = expected_sha1(spec, engine_edits)
    if want is None:
        raise BuildError("This YellowLink's Complete Sinnoh data has no checked version yet.")
    sha1 = sha1 or _sha1
    dest = Path(dest)
    if dest.exists():
        try:
            if sha1(dest) == want:
                return str(dest), want
        except OSError:
            pass
        log.info("%s is out of date or damaged: making it again", dest)
    got = build(src, dest, spec, expected=want, sha1_of_src=sha1(src), engine_edits=engine_edits)
    return str(dest), got
