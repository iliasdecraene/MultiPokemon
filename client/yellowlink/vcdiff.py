"""VCDIFF (RFC 3284) patch decoder, as written by xdelta3 (the format of ROM-hack ".xdelta" patches).

Used to make Definitive Platinum from the player's own Pokémon Platinum (SPEC.md section 10). Supports what xdelta3
writes: the default code table, source and target windows, the Adler-32 checksum per window (VCD_ADLER32 = 0x04 in
the window indicator) and LZMA secondary compression (id 2). xdelta3 keeps ONE .xz stream per section kind
(data / instructions / addresses) for the whole patch and flushes it at the end of every window
(xdelta3-lzma.h: LZMA_SYNC_FLUSH), so each section's decompressor lives across windows.

Pure Python (no xdelta3 binary needed). `decode_file` streams the result to disk window by window (the source ROM is
memory-mapped), so memory stays near the size of the ROM, not ROM + result + copies.
"""
from __future__ import annotations

import hashlib
import lzma
import mmap
import os
import zlib
from dataclasses import dataclass, field

MAGIC = b"\xd6\xc3\xc4"
VCD_DECOMPRESS, VCD_CODETABLE, VCD_APPHEADER = 1, 2, 4
VCD_SOURCE, VCD_TARGET, VCD_ADLER32 = 1, 2, 4
VCD_DATACOMP, VCD_INSTCOMP, VCD_ADDRCOMP = 1, 2, 4
NOOP, ADD, RUN, COPY = 0, 1, 2, 3
SEC_LZMA = 2
MAX_WINDOW = 1 << 26          # 64 MB per target window (xdelta3 writes 8 MB ones)
MAX_TARGET = 1 << 29          # 512 MB in total
MAX_SECTION = 1 << 27         # one decompressed data/instruction/address section
PROGRESS_EVERY = 8192         # instructions between progress calls inside a window


class VCDiffError(Exception):
    """The patch can't be applied: damaged, unsupported, or (WrongSourceError) made for another file."""


class WrongSourceError(VCDiffError):
    """The patch is fine but was made for a different file (Adler-32 or bounds don't match)."""


def _default_table():
    """RFC 3284 section 5.6: the default instruction code table, as (type, size, mode) ops without NOOPs."""
    t = [((RUN, 0, 0),), ((ADD, 0, 0),)]
    t += [((ADD, s, 0),) for s in range(1, 18)]
    for mode in range(9):
        t.append(((COPY, 0, mode),))
        t += [((COPY, s, mode),) for s in range(4, 19)]
    for mode in range(6):
        for a in range(1, 5):
            for c in range(4, 7):
                t.append(((ADD, a, 0), (COPY, c, mode)))
    for mode in range(6, 9):
        for a in range(1, 5):
            t.append(((ADD, a, 0), (COPY, 4, mode)))
    for mode in range(9):
        t.append(((COPY, 4, mode), (ADD, 1, 0)))
    assert len(t) == 256
    return tuple(t)


TABLE = _default_table()


class _Reader:
    __slots__ = ("b", "p", "n")

    def __init__(self, b, p=0):
        self.b, self.p, self.n = b, p, len(b)

    def byte(self):
        if self.p >= self.n:
            raise VCDiffError("the patch file is cut short (incomplete download?)")
        v = self.b[self.p]
        self.p += 1
        return v

    def varint(self, limit=1 << 62):
        v = 0
        for _ in range(10):
            c = self.byte()
            v = (v << 7) | (c & 0x7F)
            if not c & 0x80:
                if v > limit:
                    raise VCDiffError("the patch file is damaged (number out of range)")
                return v
        raise VCDiffError("the patch file is damaged (bad number)")

    def take(self, n):
        if n < 0 or self.p + n > self.n:
            raise VCDiffError("the patch file is cut short (incomplete download?)")
        s = self.b[self.p:self.p + n]
        self.p += n
        return s


@dataclass
class Window:
    indicator: int
    seg_len: int
    seg_pos: int
    target_len: int
    delta_indicator: int
    data_len: int
    inst_len: int
    addr_len: int
    adler32: int | None
    offset: int               # where the window's sections start in the patch


@dataclass
class PatchInfo:
    secondary: int | None
    app_header: bytes
    windows: list = field(default_factory=list)
    body: int = 0             # patch offset of the first window

    @property
    def target_size(self) -> int:
        return sum(w.target_len for w in self.windows)

    @property
    def source_needed(self) -> int:
        """The source file must be at least this long (end of the farthest source segment)."""
        return max((w.seg_pos + w.seg_len for w in self.windows if w.indicator & VCD_SOURCE), default=0)


def inspect(patch) -> PatchInfo:
    """Parse the header and every window header (without decoding). Raises VCDiffError for anything we can't apply."""
    patch = memoryview(patch)
    r = _Reader(patch)
    if len(patch) < 5 or bytes(patch[:3]) != MAGIC:
        raise VCDiffError("this is not an xdelta/VCDIFF patch file")
    r.take(4)                                  # magic + version byte (xdelta3 writes 0)
    hdr = r.byte()
    if hdr & ~(VCD_DECOMPRESS | VCD_CODETABLE | VCD_APPHEADER):
        raise VCDiffError("the patch file is damaged (bad header)")
    secondary = r.byte() if hdr & VCD_DECOMPRESS else None
    if secondary is not None and secondary != SEC_LZMA:
        raise VCDiffError(f"unsupported patch compression (id {secondary})")
    if hdr & VCD_CODETABLE:
        raise VCDiffError("patches with a custom code table are not supported")
    app = bytes(r.take(r.varint(1 << 16))) if hdr & VCD_APPHEADER else b""
    info = PatchInfo(secondary, app, body=r.p)
    total = 0
    while r.p < len(patch):
        win = r.byte()
        if win & ~(VCD_SOURCE | VCD_TARGET | VCD_ADLER32) or (win & VCD_SOURCE and win & VCD_TARGET):
            raise VCDiffError("the patch file is damaged (bad window)")
        seg_len = seg_pos = 0
        if win & (VCD_SOURCE | VCD_TARGET):
            seg_len, seg_pos = r.varint(MAX_TARGET), r.varint(MAX_TARGET)
        delta_len = r.varint()
        start = r.p
        tlen = r.varint(MAX_WINDOW)
        dind = r.byte()
        if dind & ~(VCD_DATACOMP | VCD_INSTCOMP | VCD_ADDRCOMP) or (dind and secondary is None):
            raise VCDiffError("the patch file is damaged (bad section flags)")
        dlen, ilen, alen = r.varint(MAX_SECTION), r.varint(MAX_SECTION), r.varint(MAX_SECTION)
        cksum = int.from_bytes(r.take(4), "big") if win & VCD_ADLER32 else None
        if r.p - start + dlen + ilen + alen != delta_len:
            raise VCDiffError("the patch file is damaged (window length)")
        if win & VCD_TARGET and seg_pos + seg_len > total:
            raise VCDiffError("the patch file is damaged (target segment)")
        info.windows.append(Window(win, seg_len, seg_pos, tlen, dind, dlen, ilen, alen, cksum, r.p))
        r.take(dlen + ilen + alen)
        total += tlen
        if total > MAX_TARGET:
            raise VCDiffError("the patch file is damaged (result too big)")
    if not info.windows:
        raise VCDiffError("the patch file is empty")
    return info


def _section(data, compressed, streams, kind):
    if not compressed:
        return bytes(data)
    r = _Reader(data)
    size = r.varint(MAX_SECTION)
    if size == 0:
        return b""
    dec = streams.get(kind)
    if dec is None:
        dec = streams[kind] = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    try:
        out = dec.decompress(bytes(data[r.p:]), max_length=size + 1)
    except (lzma.LZMAError, EOFError) as e:
        raise VCDiffError(f"the patch file is damaged ({e})") from None
    if len(out) != size:
        raise VCDiffError("the patch file is damaged (section size)")
    return out


def _window(seg, tlen, data, inst, addr, tick=None):
    """Run one window's instructions. seg = the source/target segment (bytes-like); returns the target bytearray."""
    s_len = len(seg)
    t = bytearray()
    tl = 0                                   # len(t), kept by hand (this loop runs millions of times)
    n_inst, n_data = len(inst), len(data)
    ip = dp = ap = 0
    near = [0, 0, 0, 0]
    ni = 0
    same = [0] * 768
    table = TABLE
    count = PROGRESS_EVERY
    try:
        while ip < n_inst:
            ops = table[inst[ip]]
            ip += 1
            for typ, size, mode in ops:
                if not size:
                    c = 0x80
                    while c & 0x80:
                        c = inst[ip]
                        ip += 1
                        size = (size << 7) | (c & 0x7F)
                        if size > tlen:
                            raise VCDiffError("the patch file is damaged (size)")
                if tl + size > tlen:
                    raise VCDiffError("the patch file is damaged (window overflow)")
                if typ == COPY:
                    here = s_len + tl
                    if mode < 6:
                        a = 0
                        c = 0x80
                        while c & 0x80:
                            c = addr[ap]
                            ap += 1
                            a = (a << 7) | (c & 0x7F)
                            if a > MAX_TARGET:
                                raise VCDiffError("the patch file is damaged (address)")
                        ad = a if mode == 0 else (here - a if mode == 1 else near[mode - 2] + a)
                    else:
                        ad = same[(mode - 6) * 256 + addr[ap]]
                        ap += 1
                    near[ni] = ad
                    ni = (ni + 1) & 3
                    same[ad % 768] = ad
                    if ad < 0 or ad >= here:
                        raise VCDiffError("the patch file is damaged (copy address)")
                    end = ad + size
                    if end <= s_len:
                        t += seg[ad:end]
                    elif ad >= s_len and end <= here:
                        t += t[ad - s_len:end - s_len]
                    else:
                        left = size
                        while left:
                            if ad < s_len:
                                n = min(left, s_len - ad)
                                t += seg[ad:ad + n]
                            else:
                                off = ad - s_len
                                n = min(left, len(t) - off)       # overlapping copy repeats a pattern
                                t += t[off:off + n]
                            ad += n
                            left -= n
                elif typ == ADD:
                    end = dp + size
                    if end > n_data:
                        raise VCDiffError("the patch file is damaged (data)")
                    t += data[dp:end]
                    dp = end
                else:                                                 # RUN
                    if dp >= n_data:
                        raise VCDiffError("the patch file is damaged (data)")
                    t += data[dp:dp + 1] * size
                    dp += 1
                tl += size
            count -= 1
            if not count:
                count = PROGRESS_EVERY
                if tick:
                    tick(tl)
    except IndexError:
        raise VCDiffError("the patch file is damaged (cut short inside a window)") from None
    if len(t) != tlen:
        raise VCDiffError("the patch file is damaged (window size)")
    return t


def _run(source, patch, info: PatchInfo, emit, read_target, progress):
    """Decode every window of an inspected patch; emit(bytes) gets each window's output in order."""
    total = info.target_size
    done = 0
    streams = {}
    if len(source) < info.source_needed:
        raise WrongSourceError("this patch is for a different ROM (your file is too small)")
    if progress:
        progress(0, total)
    for w in info.windows:
        if w.indicator & VCD_SOURCE:
            seg = source[w.seg_pos:w.seg_pos + w.seg_len]
        elif w.indicator & VCD_TARGET:
            seg = read_target(w.seg_pos, w.seg_len)
        else:
            seg = b""
        p = w.offset
        data = _section(patch[p:p + w.data_len], w.delta_indicator & VCD_DATACOMP, streams, "d")
        p += w.data_len
        inst = _section(patch[p:p + w.inst_len], w.delta_indicator & VCD_INSTCOMP, streams, "i")
        p += w.inst_len
        addr = _section(patch[p:p + w.addr_len], w.delta_indicator & VCD_ADDRCOMP, streams, "a")
        tick = (lambda n, d=done: progress(d + n, total)) if progress else None
        target = _window(seg, w.target_len, data, inst, addr, tick)
        del seg
        if w.adler32 is not None and zlib.adler32(target) & 0xFFFFFFFF != w.adler32:
            raise WrongSourceError("this patch is for a different ROM (checksum mismatch)")
        emit(target)
        done += len(target)
        if progress:
            progress(done, total)
    return total


def decode(source, patch, progress=None) -> bytes:
    """Apply `patch` to `source` in memory; returns the result. progress(done_bytes, total_bytes) is called as it goes
    (it may raise to cancel)."""
    patch = memoryview(patch)
    info = inspect(patch)
    out = bytearray()
    _run(memoryview(source), patch, info, out.extend, lambda pos, n: bytes(out[pos:pos + n]), progress)
    return bytes(out)


def decode_file(source_path, patch_path, out_path, progress=None, expect_sha1: str | None = None) -> str:
    """Apply a patch file to a source file and write the result to out_path (via out_path + '.part', replaced only
    when everything checked out). Returns the result's SHA1 (hex). With expect_sha1, a different result raises
    WrongSourceError and nothing is written. The source file is never modified."""
    with open(patch_path, "rb") as f:
        patch = memoryview(f.read())
    info = inspect(patch)
    part = str(out_path) + ".part"
    sha = hashlib.sha1()
    ok = False
    with open(source_path, "rb") as sf:
        mm = None
        try:
            mm = mmap.mmap(sf.fileno(), 0, access=mmap.ACCESS_READ)
            src = memoryview(mm)
        except (ValueError, OSError):      # empty file, or no mmap: plain read
            mm = None
            src = memoryview(sf.read())
        try:
            with open(part, "w+b") as out:
                def emit(b):
                    out.write(b)
                    sha.update(b)

                def read_target(pos, n):
                    here = out.tell()
                    out.seek(pos)
                    b = out.read(n)
                    out.seek(here)
                    return b

                _run(src, patch, info, emit, read_target, progress)
                out.flush()
                os.fsync(out.fileno())
            digest = sha.hexdigest()
            if expect_sha1 and digest != expect_sha1.lower():
                raise WrongSourceError("the result is not the expected game (SHA1 mismatch)")
            os.replace(part, out_path)
            ok = True
            return digest
        finally:
            try:
                src.release()
            except BufferError:
                pass
            if mm is not None:
                try:
                    mm.close()
                except BufferError:             # a view still alive somewhere: the GC closes it
                    pass
            if not ok:
                try:
                    os.remove(part)
                except OSError:
                    pass
