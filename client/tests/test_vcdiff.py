"""VCDIFF decoder (client/yellowlink/vcdiff.py).

The patches here are made by a tiny VCDIFF *encoder* written for these tests only: the RFC 3284 default code table
(single and double instructions, all nine address modes), source / target / no-segment windows, Adler-32 per window
and xdelta3-style LZMA secondary compression where each section kind is ONE .xz stream that spans all windows
(written by hand: an xz container around LZMA2 chunks, cut at window boundaries like LZMA_SYNC_FLUSH does).
The real Definitive Platinum patches are tested when dev/states/ has them (skipped otherwise)."""
import hashlib
import lzma
import os
import random
import struct
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import vcdiff  # noqa: E402
from yellowlink.vcdiff import ADD, COPY, RUN, TABLE  # noqa: E402

STATES = os.path.join(os.path.dirname(__file__), "..", "..", "dev", "states")
DEFPLAT_SHA1 = "2f368f0f68f98058699a8dd0b1f9af3c970172a2"
PLATINUM_SHA1 = "ce81046eda7d232513069519cb2085349896dec7"


# ------------------------------------------------------------------------------------------- test-only encoder
def varint(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


CODE = {ops: i for i, ops in enumerate(TABLE)}


def encode_ops(ops, s_len):
    """ops: [("add", bytes) | ("run", byte, n) | ("copy", addr, size)] -> (data, inst, addr) sections, choosing the
    cheapest address mode with the same near/same caches as a decoder and merging ADD+COPY / COPY+ADD pairs into
    the table's double instructions."""
    data, inst, addr = bytearray(), bytearray(), bytearray()
    near, ni, same = [0] * 4, 0, [0] * 768
    tl = 0
    last = None          # (inst position, op tuple) of a single-op code that could still become a double
    for op in ops:
        if op[0] == "add":
            b = op[1]
            data += b
            if last and last[1][0] == COPY and last[1][1] == 4 and len(b) == 1:
                inst[last[0]] = CODE[(last[1], (ADD, 1, 0))]
                last = None
            elif 1 <= len(b) <= 17:
                last = (len(inst), (ADD, len(b), 0)) if len(b) <= 4 else None
                inst.append(CODE[((ADD, len(b), 0),)])
            else:
                inst.append(CODE[((ADD, 0, 0),)])
                inst += varint(len(b))
                last = None
            tl += len(b)
        elif op[0] == "run":
            data.append(op[1])
            inst.append(CODE[((RUN, 0, 0),)])
            inst += varint(op[2])
            tl += op[2]
            last = None
        else:
            ad, size = op[1], op[2]
            here = s_len + tl
            cands = [(len(varint(ad)), 0, varint(ad)), (len(varint(here - ad)), 1, varint(here - ad))]
            for i in range(4):
                if ad - near[i] >= 0:
                    cands.append((len(varint(ad - near[i])), 2 + i, varint(ad - near[i])))
            if same[ad % 768] == ad:
                cands.append((0, 6 + (ad % 768) // 256, bytes([ad % 256])))
            _, mode, enc = min(cands, key=lambda c: (c[0], c[1]))
            addr += enc
            near[ni] = ad
            ni = (ni + 1) % 4
            same[ad % 768] = ad
            op_t = (COPY, size, mode)
            if last and last[1][0] == ADD and ((mode < 6 and 4 <= size <= 6) or (mode >= 6 and size == 4)):
                inst[last[0]] = CODE[(last[1], op_t)]
                last = None
            elif 4 <= size <= 18:
                last = (len(inst), op_t) if size == 4 else None
                inst.append(CODE[(op_t,)])
            else:
                inst.append(CODE[((COPY, 0, mode),)])
                inst += varint(size)
                last = None
            tl += size
    return bytes(data), bytes(inst), bytes(addr)


def apply_ops(seg, ops):
    """Reference semantics (byte by byte, independent of the decoder)."""
    t = bytearray()
    for op in ops:
        if op[0] == "add":
            t += op[1]
        elif op[0] == "run":
            t += bytes([op[1]]) * op[2]
        else:
            ad, size = op[1], op[2]
            for _ in range(size):
                t.append(seg[ad] if ad < len(seg) else t[ad - len(seg)])
                ad += 1
    return bytes(t)


def _crc(b):
    return struct.pack("<I", zlib.crc32(b) & 0xFFFFFFFF)


class XzSpanning:
    """One .xz stream (one block, LZMA2, no check) whose LZMA2 chunks are handed out piece by piece: piece(data)
    returns the bytes that make `data` decodable right after the previous pieces (the first one carries the stream
    and block headers), finish() the end of the stream. Each piece starts with a dictionary-reset chunk, so it is
    valid LZMA2 - but only decodable by a decompressor that already saw the earlier pieces (like xdelta3's)."""
    def __init__(self):
        self.total_u = 0
        self.total_c = 0
        self.started = False
        hdr = bytes([0, 0x00, 0x21, 0x01, 0x12]) + b"\0\0\0"        # filters: LZMA2, dict 1 MiB; padded to 8
        hdr = bytes([(len(hdr) + 4) // 4 - 1]) + hdr[1:]
        self.block_header = hdr + _crc(hdr)

    def piece(self, data):
        raw = lzma.compress(data, format=lzma.FORMAT_RAW,
                            filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20}])
        assert raw[-1] == 0
        raw = raw[:-1]                                               # drop the end-of-LZMA2 marker
        out = b""
        if not self.started:
            flags = b"\0\0"                                          # check: none
            out = b"\xfd7zXZ\0" + flags + _crc(flags) + self.block_header
            self.started = True
        self.total_u += len(data)
        self.total_c += len(raw)
        return out + raw

    def finish(self):
        comp = self.total_c + 1                                      # + end marker
        out = b"\0" + b"\0" * ((-(len(self.block_header) + comp)) % 4)
        unpadded = len(self.block_header) + comp
        index = b"\0" + varint_xz(1) + varint_xz(unpadded) + varint_xz(self.total_u)
        index += b"\0" * ((-len(index)) % 4)
        index += _crc(index)
        flags = b"\0\0"
        back = struct.pack("<I", len(index) // 4 - 1)
        return out + index + _crc(back + flags) + back + flags + b"YZ"


def varint_xz(n):                       # xz multibyte integers are little-endian groups of 7 bits
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def build_patch(source, windows, compress=True, app=b"test.nds//base.nds/", adler=True):
    """windows: [(kind 'source'|'target'|None, seg_pos, seg_len, ops)] -> (patch bytes, expected result)."""
    target = bytearray()
    encoded = []
    for kind, pos, length, ops in windows:
        seg = source[pos:pos + length] if kind == "source" else (bytes(target[pos:pos + length]) if kind else b"")
        out = apply_ops(seg, ops)
        encoded.append((kind, pos, len(seg), out, encode_ops(ops, len(seg))))
        target += out
    xz = {k: XzSpanning() for k in "dia"}
    last_nonempty = {k: max((i for i, e in enumerate(encoded) if e[4]["dia".index(k)]), default=-1) for k in "dia"}
    body = bytearray()
    for wi, (kind, pos, slen, out, secs) in enumerate(encoded):
        sections, dind = [], 0
        for si, (k, flag) in enumerate(zip("dia", (1, 2, 4))):
            raw = secs[si]
            if compress and raw:
                piece = xz[k].piece(raw)
                if wi == last_nonempty[k]:
                    piece += xz[k].finish()
                sections.append(varint(len(raw)) + piece)
                dind |= flag
            else:
                sections.append(raw)
        win = (vcdiff.VCD_ADLER32 if adler else 0) | {"source": 1, "target": 2, None: 0}[kind]
        head = bytes([win]) + (varint(slen) + varint(pos) if kind else b"")
        inner = varint(len(out)) + bytes([dind]) + b"".join(varint(len(s)) for s in sections)
        if adler:
            inner += struct.pack(">I", zlib.adler32(out) & 0xFFFFFFFF)
        delta = inner + b"".join(sections)
        body += head + varint(len(delta)) + delta
    hdr = b"\xd6\xc3\xc4\x00" + bytes([(1 if compress else 0) | 4]) + (b"\x02" if compress else b"")
    return hdr + varint(len(app)) + app + bytes(body), bytes(target)


def random_ops(rng, seg_len, out_len, prev=None):
    """Random instructions producing out_len bytes: adds, runs, copies from the segment (re-using addresses so the
    near/same caches are hit) and overlapping copies inside the window's own output."""
    ops, tl, used = [], 0, list(prev or [])
    while tl < out_len:
        left = out_len - tl
        r = rng.random()
        if r < 0.25:
            n = min(left, rng.choice([1, 2, 3, 4, rng.randint(5, 17), rng.randint(18, 60)]))
            ops.append(("add", bytes(rng.randrange(256) for _ in range(n))))
        elif r < 0.33:
            n = min(left, rng.randint(1, 400))
            ops.append(("run", rng.randrange(256), n))
        elif r < 0.85 and seg_len >= 64:
            n = min(left, rng.choice([4, 5, 6, rng.randint(4, 18), rng.randint(19, 3000)]), seg_len)
            if used and rng.random() < 0.4:
                ad = min(rng.choice(used) + rng.randint(0, 40), seg_len - n)
            else:
                ad = rng.randint(0, seg_len - n)
            used.append(ad)
            ops.append(("copy", ad, n))
        elif tl >= 8:
            n = min(left, rng.randint(4, 500))
            off = rng.randint(max(0, tl - 64), tl - 1)          # often overlapping: repeats a pattern
            ops.append(("copy", seg_len + off, n))
        else:
            continue
        tl += ops[-1][2] if ops[-1][0] != "add" else len(ops[-1][1])
    return ops


def sample(seed=1, compress=True):
    rng = random.Random(seed)
    source = bytes(rng.randrange(256) for _ in range(60000)) + bytes(range(256)) * 200
    windows = [
        ("source", 0, len(source), random_ops(rng, len(source), 30000)),
        ("source", 12345, 30000, random_ops(rng, 30000, 25000)),
        (None, 0, 0, random_ops(rng, 0, 4000)),
        ("target", 1000, 20000, random_ops(rng, 20000, 22000)),
        ("source", 40000, 50000, random_ops(rng, 50000, 9000)),
    ]
    patch, target = build_patch(source, windows, compress=compress)
    return source, patch, target


# ------------------------------------------------------------------------------------------- tests
def test_code_table_matches_rfc3284():
    assert TABLE[0] == ((RUN, 0, 0),) and TABLE[1] == ((ADD, 0, 0),) and TABLE[18] == ((ADD, 17, 0),)
    assert TABLE[19] == ((COPY, 0, 0),) and TABLE[20] == ((COPY, 4, 0),) and TABLE[34] == ((COPY, 18, 0),)
    assert TABLE[35] == ((COPY, 0, 1),) and TABLE[162] == ((COPY, 18, 8),)
    assert TABLE[163] == ((ADD, 1, 0), (COPY, 4, 0)) and TABLE[234] == ((ADD, 4, 0), (COPY, 6, 5))
    assert TABLE[235] == ((ADD, 1, 0), (COPY, 4, 6)) and TABLE[246] == ((ADD, 4, 0), (COPY, 4, 8))
    assert TABLE[247] == ((COPY, 4, 0), (ADD, 1, 0)) and TABLE[255] == ((COPY, 4, 8), (ADD, 1, 0))


def test_encoder_uses_every_address_mode_and_double_codes():
    source, patch, _ = sample()
    rng = random.Random(1)
    rng_ops = random_ops(rng, 100000, 60000)
    _, inst, _ = encode_ops(rng_ops, 100000)
    codes = set(inst)
    assert any(163 <= c <= 255 for c in codes)                       # double instructions
    modes = {op[2] for c in codes for op in TABLE[c] if op[0] == COPY}
    assert modes >= {0, 1, 2, 6}


@pytest.mark.parametrize("compress", [True, False])
def test_decode_synthetic(compress):
    source, patch, target = sample(compress=compress)
    info = vcdiff.inspect(patch)
    assert len(info.windows) == 5 and info.target_size == len(target)
    assert info.secondary == (2 if compress else None)
    assert info.app_header == b"test.nds//base.nds/"
    seen = []
    assert vcdiff.decode(source, patch, progress=lambda d, t: seen.append((d, t))) == target
    assert seen[0] == (0, len(target)) and seen[-1] == (len(target), len(target))
    assert all(a[0] <= b[0] for a, b in zip(seen, seen[1:]))


def test_lzma_sections_span_windows():
    """The later windows' sections can't be decoded on their own: they continue the first window's .xz stream."""
    source, patch, target = sample()
    info = vcdiff.inspect(patch)
    w = info.windows[1]
    piece = memoryview(patch)[w.offset:w.offset + w.data_len]
    with pytest.raises(vcdiff.VCDiffError):
        vcdiff._section(piece, True, {}, "d")          # a fresh decompressor: no xz header here
    assert vcdiff.decode(source, patch) == target


def test_decode_file(tmp_path):
    source, patch, target = sample(seed=7)
    (tmp_path / "src.bin").write_bytes(source)
    (tmp_path / "p.xdelta").write_bytes(patch)
    calls = []
    sha = vcdiff.decode_file(tmp_path / "src.bin", tmp_path / "p.xdelta", tmp_path / "out.bin",
                             progress=lambda d, t: calls.append(d), expect_sha1=hashlib.sha1(target).hexdigest())
    assert sha == hashlib.sha1(target).hexdigest()
    assert (tmp_path / "out.bin").read_bytes() == target
    assert (tmp_path / "src.bin").read_bytes() == source             # the source is never touched
    assert not (tmp_path / "out.bin.part").exists() and calls[-1] == len(target)


def test_decode_file_wrong_sha_leaves_nothing(tmp_path):
    source, patch, target = sample(seed=8)
    (tmp_path / "src.bin").write_bytes(source)
    (tmp_path / "p.xdelta").write_bytes(patch)
    with pytest.raises(vcdiff.WrongSourceError):
        vcdiff.decode_file(tmp_path / "src.bin", tmp_path / "p.xdelta", tmp_path / "out.bin", expect_sha1="0" * 40)
    assert not (tmp_path / "out.bin").exists() and not (tmp_path / "out.bin.part").exists()


def test_cancel_from_progress(tmp_path):
    source, patch, _ = sample(seed=9)
    (tmp_path / "src.bin").write_bytes(source)
    (tmp_path / "p.xdelta").write_bytes(patch)

    class Stop(Exception):
        pass

    def progress(done, total):
        if done > 0:
            raise Stop()
    with pytest.raises(Stop):
        vcdiff.decode_file(tmp_path / "src.bin", tmp_path / "p.xdelta", tmp_path / "out.bin", progress=progress)
    assert not (tmp_path / "out.bin").exists() and not (tmp_path / "out.bin.part").exists()


def test_wrong_source_is_reported_as_such():
    source, patch, _ = sample()
    other = bytearray(source)
    for i in range(0, len(other), 997):
        other[i] ^= 0xFF
    with pytest.raises(vcdiff.WrongSourceError, match="different ROM"):
        vcdiff.decode(bytes(other), patch)
    with pytest.raises(vcdiff.WrongSourceError, match="too small"):
        vcdiff.decode(source[:1000], patch)


def test_not_a_patch_and_cut_short():
    source, patch, _ = sample()
    with pytest.raises(vcdiff.VCDiffError, match="not an xdelta"):
        vcdiff.decode(source, b"PK\x03\x04 a zip file")
    with pytest.raises(vcdiff.VCDiffError, match="not an xdelta"):
        vcdiff.decode(source, b"")
    for cut in (5, 30, len(patch) // 2, len(patch) - 1):
        with pytest.raises(vcdiff.VCDiffError):
            vcdiff.decode(source, patch[:cut])


@pytest.mark.parametrize("compress", [True, False])
def test_damaged_patches_only_raise_vcdiff_errors(compress):
    """Flipped bytes anywhere: either a clean VCDiffError or (rarely, inside literal data that the checksum covers
    anyway)... never another exception, a hang or a huge allocation."""
    source, patch, target = sample(seed=3, compress=compress)
    rng = random.Random(5)
    for _ in range(250):
        bad = bytearray(patch)
        for _ in range(rng.randint(1, 3)):
            bad[rng.randrange(len(bad))] ^= 1 << rng.randrange(8)
        try:
            out = vcdiff.decode(source, bytes(bad))
        except vcdiff.VCDiffError:
            continue
        assert out == target           # only harmless flips (e.g. in the app header) may succeed


def test_no_checksum_windows_still_decode():
    rng = random.Random(4)
    source = bytes(rng.randrange(256) for _ in range(5000))
    patch, target = build_patch(source, [("source", 0, 5000, random_ops(rng, 5000, 7000))], adler=False)
    assert vcdiff.decode(source, patch) == target


def test_bad_header_flags():
    source, patch, _ = sample(compress=False)
    bad = bytearray(patch)
    bad[4] |= vcdiff.VCD_CODETABLE
    with pytest.raises(vcdiff.VCDiffError, match="code table"):
        vcdiff.inspect(bytes(bad))
    bad = bytearray(patch[:4]) + bytes([1, 9]) + patch[5:]           # secondary compressor 9 (unknown)
    with pytest.raises(vcdiff.VCDiffError, match="compression"):
        vcdiff.inspect(bytes(bad))


# ------------------------------------------------------------------------------------------- the real patches
def _real(name):
    path = os.path.join(STATES, name)
    if not os.path.exists(path):
        pytest.skip(f"dev/states/{name} not here")
    return path


def test_real_rev0_patch_makes_definitive_platinum(tmp_path):
    rom, patch = _real("platinum.nds"), _real("defplat_rev0.xdelta")
    out = tmp_path / "defplat.nds"
    assert vcdiff.decode_file(rom, patch, out, expect_sha1=DEFPLAT_SHA1) == DEFPLAT_SHA1
    assert out.stat().st_size == 105001364


def test_real_rev1_patch_parses_and_refuses_rev0():
    rom, patch = _real("platinum.nds"), _real("defplat_rev1.xdelta")
    with open(patch, "rb") as f:
        data = f.read()
    info = vcdiff.inspect(data)
    rev0 = vcdiff.inspect(open(_real("defplat_rev0.xdelta"), "rb").read())
    assert info.target_size == rev0.target_size == 105001364 and len(info.windows) == 13
    assert [w.adler32 for w in info.windows] == [w.adler32 for w in rev0.windows]   # same result
    assert b"(Rev 1)" in info.app_header
    with open(rom, "rb") as f:
        src = f.read()
    with pytest.raises(vcdiff.WrongSourceError):
        vcdiff.decode(src, data)                                       # rev 1 patch on a rev 0 ROM
