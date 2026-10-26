"""Complete Sinnoh: YellowLink's own version of Pokémon Platinum, made on the player's computer from their own ROM.

The player adds their vanilla Platinum (US rev 0, `PLATINUM_SHA1`); YellowLink copies it into its data folder
(`roms/platinum-sinnoh.nds`) and edits ONE file inside the copy in place: the wild-encounter tables
`/fielddata/encountdata/pl_enc_data.narc` (183 tables of 424 bytes; same size before and after, so nothing moves).
What is changed is our own data, `sinnoh_complete.py` (the spec: map tables, slots, species, levels). Game code is
untouched, so every address/hook verified on vanilla holds for the copy (platinum.VARIANTS treats it like vanilla).

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
from pathlib import Path

from . import encdata, ndsrom

log = logging.getLogger("yellowlink.sinnoh_rom")

VANILLA_SHA1 = "ce81046eda7d232513069519cb2085349896dec7"     # Pokémon Platinum (USA) rev 0
FILE_NAME = "platinum-sinnoh.nds"

# spec digest (spec_digest()) -> (SHA1 of the finished ROM, spec version). One line per released spec version (old
# lines stay: platinum.VARIANTS lists every build as a verified variant, so older resume snapshots stay loadable).
# A unit test checks that the spec in the repo has an entry here. Recompute: python dev/sinnoh/build_rom.py --update
EXPECTED = {
    "8f9e2816b06feb3be17f1772ef41b35c769d639e": ("a12d1e9c5b96aede5805b52f4a75852f40a8d8b2", 1),
}
# Dev only (YL_SINNOH_SPEC=test): the tiny stand-in spec dev/sinnoh/test_spec.py, for the harnesses.
DEV_TEST_EXPECTED = {"c207175fa670fd19861a9c54d5abb1d0ff4d916f": ("b8f5ef8f60e28745c3938705fa310100c6beefe1", 0)}
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


def spec_digest(spec) -> str:
    """SHA1 over everything in the spec that changes the bytes (version + slot edits + rate edits)."""
    def rows(seq):
        return [list(e) for e in sorted(seq or (), key=lambda e: tuple(str(x) for x in e))]
    blob = json.dumps({"version": spec.SPEC_VERSION, "edits": rows(getattr(spec, "EDITS", ())),
                       "rates": rows(getattr(spec, "RATE_EDITS", ()))}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def expected_sha1(spec) -> str | None:
    hit = _expected().get(spec_digest(spec))
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


def _sha1(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _compare_outside(a, b, lo: int, hi: int) -> bool:
    """Files a and b are equal everywhere except maybe in [lo, hi)."""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        pos = 0
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if not x and not y:
                return True
            if x != y:
                n = len(x)
                if pos + n <= lo or pos >= hi:
                    return False
                # chunk overlaps the NARC: compare only the parts outside it
                for s, e in ((pos, min(pos + n, lo)), (max(pos, hi), pos + n)):
                    if s < e and x[s - pos:e - pos] != y[s - pos:e - pos]:
                        return False
            pos += len(x)


def build(src, dest, spec=None, *, expected: str | None = "auto", sha1_of_src: str | None = None) -> str:
    """Make `dest` from the vanilla ROM `src` with `spec` (default: sinnoh_complete). Returns the new file's SHA1.

    Checks: src is the supported vanilla ROM; only the encounter NARC differs (compared byte by byte); the result's
    SHA1 equals `expected` ("auto" = EXPECTED for this spec; None = don't check, for the dev tool that computes it).
    Writes `dest.part` first and renames it, so a crash never leaves a half-made ROM under the real name."""
    spec = spec or load_spec()
    if spec is None:
        raise BuildError("The Complete Sinnoh data is missing from this YellowLink.")
    if expected == "auto":
        expected = expected_sha1(spec)
        if expected is None:
            raise BuildError("This YellowLink's Complete Sinnoh data has no checked version yet.")
    src, dest = Path(src), Path(dest)
    if (sha1_of_src or _sha1(src)) != VANILLA_SHA1:
        raise BuildError("That isn't the Pokémon Platinum (USA) file YellowLink supports.")
    rom = ndsrom.NDSRom(str(src))
    try:
        start, size = rom.extent(encdata.NARC_PATH)
        narc = rom.file(encdata.NARC_PATH)
    finally:
        rom.close()
    new = patch_narc(narc, spec)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        shutil.copyfile(src, part)
        with open(part, "r+b") as f:
            f.seek(start)
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        if not _compare_outside(src, part, start, start + size):
            raise BuildError("Making Complete Sinnoh went wrong (the copy differs outside the wild Pokémon data).")
        with open(part, "rb") as f:
            f.seek(start)
            if f.read(size) != new:
                raise BuildError("Making Complete Sinnoh went wrong (the wild Pokémon data didn't write).")
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
    log.info("built %s (spec v%s, %d slot edits) sha1 %s", dest, spec.SPEC_VERSION, len(getattr(spec, "EDITS", ())),
             got)
    return got


def ensure(src, dest, spec=None, sha1=None) -> tuple:
    """(path, sha1) of the Complete Sinnoh ROM: `dest` if it is already the expected file, else built now.
    `sha1` = a function path -> SHA1 (library.sha1_file: cached per path/size/mtime). Raises BuildError."""
    spec = spec or load_spec()
    if spec is None:
        raise BuildError("The Complete Sinnoh data is missing from this YellowLink.")
    want = expected_sha1(spec)
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
    got = build(src, dest, spec, expected=want, sha1_of_src=sha1(src))
    return str(dest), got
