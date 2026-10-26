"""Minimal VCDIFF (RFC 3284) decoder with the xdelta3 extensions used by ROM-hack patches:
Adler-32 per window (VCD_ADLER32 = 0x04 in Win_Indicator) and LZMA secondary compression (id 2: each compressed
section = varint uncompressed size + an .xz stream). Only the default code table."""
from __future__ import annotations

import lzma
import zlib

MAGIC = b"\xd6\xc3\xc4\x00"
VCD_DECOMPRESS, VCD_CODETABLE, VCD_APPHEADER = 1, 2, 4
VCD_SOURCE, VCD_TARGET, VCD_ADLER32 = 1, 2, 4
VCD_DATACOMP, VCD_INSTCOMP, VCD_ADDRCOMP = 1, 2, 4
NOOP, ADD, RUN, COPY = 0, 1, 2, 3
SEC_LZMA = 2


class VCDiffError(Exception):
    pass


def _default_table():
    t = [((RUN, 0, 0), (NOOP, 0, 0)), ((ADD, 0, 0), (NOOP, 0, 0))]
    t += [((ADD, s, 0), (NOOP, 0, 0)) for s in range(1, 18)]
    for mode in range(9):
        t.append(((COPY, 0, mode), (NOOP, 0, 0)))
        t += [((COPY, s, mode), (NOOP, 0, 0)) for s in range(4, 19)]
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
    return t


TABLE = _default_table()


class _Reader:
    __slots__ = ("b", "p")

    def __init__(self, b, p=0):
        self.b, self.p = b, p

    def byte(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def varint(self):
        v = 0
        while True:
            c = self.b[self.p]
            self.p += 1
            v = (v << 7) | (c & 0x7F)
            if not c & 0x80:
                return v

    def take(self, n):
        s = self.b[self.p:self.p + n]
        if len(s) != n:
            raise VCDiffError("patch is cut short")
        self.p += n
        return s


def _section(data, compressed, secondary, streams, kind):
    """xdelta3 keeps ONE xz stream per section kind (data/inst/addr) for the whole patch, flushed at every window
    (xdelta3-lzma.h: LZMA_SYNC_FLUSH), so the decompressor lives across windows."""
    if not compressed:
        return data
    if secondary != SEC_LZMA:
        raise VCDiffError(f"unsupported secondary compressor {secondary}")
    r = _Reader(data)
    size = r.varint()
    if size == 0:
        return b""
    dec = streams.get(kind)
    if dec is None:
        dec = streams[kind] = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    out = dec.decompress(bytes(data[r.p:]))
    if len(out) != size:
        raise VCDiffError("secondary section size mismatch")
    return out


def decode(source: bytes, patch: bytes, progress=None) -> bytes:
    patch = memoryview(patch)
    r = _Reader(patch)
    if bytes(r.take(4)) != MAGIC:
        raise VCDiffError("not a VCDIFF/xdelta patch")
    hdr = r.byte()
    secondary = r.byte() if hdr & VCD_DECOMPRESS else None
    if hdr & VCD_CODETABLE:
        raise VCDiffError("custom code tables are not supported")
    if hdr & VCD_APPHEADER:
        r.take(r.varint())
    out = bytearray()
    streams = {}
    while r.p < len(patch):
        win = r.byte()
        if win & VCD_SOURCE and win & VCD_TARGET:
            raise VCDiffError("bad window indicator")
        seg = b""
        if win & (VCD_SOURCE | VCD_TARGET):
            seg_len, seg_pos = r.varint(), r.varint()
            base = source if win & VCD_SOURCE else out
            if seg_pos + seg_len > len(base):
                raise VCDiffError("patch doesn't fit this ROM (wrong base file?)")
            seg = bytes(base[seg_pos:seg_pos + seg_len])
        r.varint()                         # length of the delta encoding
        tlen = r.varint()
        dind = r.byte()
        dlen, ilen, alen = r.varint(), r.varint(), r.varint()
        cksum = int.from_bytes(r.take(4), "big") if win & VCD_ADLER32 else None
        data = _section(r.take(dlen), dind & VCD_DATACOMP, secondary, streams, "d")
        inst = _section(r.take(ilen), dind & VCD_INSTCOMP, secondary, streams, "i")
        addr = _section(r.take(alen), dind & VCD_ADDRCOMP, secondary, streams, "a")
        target = _window(seg, tlen, data, inst, addr)
        if cksum is not None and zlib.adler32(target) & 0xFFFFFFFF != cksum:
            raise VCDiffError("checksum mismatch: this patch is for a different ROM")
        out += target
        if progress:
            progress(r.p, len(patch))
    return bytes(out)


def _window(seg, tlen, data, inst, addr):
    s_len = len(seg)
    t = bytearray()
    d = _Reader(data)
    i = _Reader(inst)
    a = _Reader(addr)
    near = [0, 0, 0, 0]
    ni = 0
    same = [0] * (3 * 256)
    while i.p < len(inst):
        code = i.byte()
        for typ, size, mode in TABLE[code]:
            if typ == NOOP:
                continue
            if size == 0:
                size = i.varint()
            if typ == ADD:
                t += d.take(size)
            elif typ == RUN:
                t += bytes([d.byte()]) * size
            else:
                here = s_len + len(t)
                if mode == 0:
                    ad = a.varint()
                elif mode == 1:
                    ad = here - a.varint()
                elif mode < 6:
                    ad = near[mode - 2] + a.varint()
                else:
                    ad = same[(mode - 6) * 256 + a.byte()]
                near[ni] = ad
                ni = (ni + 1) % 4
                same[ad % (3 * 256)] = ad
                if ad + size <= s_len:
                    t += seg[ad:ad + size]
                else:
                    if ad >= here:
                        raise VCDiffError("bad copy address")
                    while size:
                        if ad < s_len:
                            n = min(size, s_len - ad)
                            t += seg[ad:ad + n]
                        else:
                            off = ad - s_len
                            n = min(size, len(t) - off)
                            t += t[off:off + n]
                        ad += n
                        size -= n
    if len(t) != tlen:
        raise VCDiffError("window size mismatch")
    return t
