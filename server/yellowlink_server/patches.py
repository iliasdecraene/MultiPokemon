"""Patch files the host puts in the data folder, served to clients in chunks (SPEC.md section 10).

Today only BigNation's Definitive Platinum patches: `<data_dir>/patches/definitive_platinum/rev0.xdelta` (for
Pokémon Platinum USA rev 0) and `rev1.xdelta` (rev 1). The server never changes them; each client patches its
player's own ROM. Files are looked at again on every hello (size + mtime), so the host can add them without a
restart; the SHA1 is only recomputed when a file changed."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("yellowlink.patches")

PATCHES = {"definitive_platinum": ("rev0", "rev1")}
MAGIC = b"\xd6\xc3\xc4"          # VCDIFF (xdelta3)
MAX_PATCH = 64 << 20
CHUNK = 48 * 1024                # raw bytes per patch_chunk (64 KiB as base64)
MAX_RANGE = 1 << 20              # one patch_get asks for at most this much


@dataclass
class PatchFile:
    name: str
    rev: str
    path: str
    size: int
    mtime: int
    sha1: str


class PatchStore:
    def __init__(self, data_dir: str):
        self.dir = os.path.join(data_dir, "patches")
        self.files: dict[tuple, PatchFile] = {}
        self._bad: dict[str, tuple] = {}         # path -> (size, mtime) already reported as unusable
        self.refresh()

    def path(self, name: str, rev: str) -> str:
        return os.path.join(self.dir, name, f"{rev}.xdelta")

    def refresh(self) -> None:
        for name, revs in PATCHES.items():
            for rev in revs:
                path = self.path(name, rev)
                try:
                    st = os.stat(path)
                except OSError:
                    if self.files.pop((name, rev), None):
                        log.info("patch %s/%s removed", name, rev)
                    continue
                old = self.files.get((name, rev))
                if old and (old.size, old.mtime) == (st.st_size, st.st_mtime_ns):
                    continue
                pf = self._load(name, rev, path, st)
                if pf:
                    self.files[(name, rev)] = pf
                    log.info("patch %s/%s: %d bytes, sha1 %s", name, rev, pf.size, pf.sha1)
                else:
                    self.files.pop((name, rev), None)

    def _load(self, name, rev, path, st) -> PatchFile | None:
        key = (st.st_size, st.st_mtime_ns)
        why = None
        if not 16 <= st.st_size <= MAX_PATCH:
            why = f"size {st.st_size} bytes (not a patch?)"
        else:
            h = hashlib.sha1()
            try:
                with open(path, "rb") as f:
                    head = f.read(4)
                    h.update(head)
                    for block in iter(lambda: f.read(1 << 20), b""):
                        h.update(block)
            except OSError as e:
                why = f"can't read it ({e})"
            else:
                if head[:3] != MAGIC:
                    why = "not an xdelta/VCDIFF file"
        if why:
            if self._bad.get(path) != key:
                self._bad[path] = key
                log.warning("patch %s ignored: %s", path, why)
            return None
        return PatchFile(name, rev, path, st.st_size, st.st_mtime_ns, h.hexdigest())

    def get(self, name: str, rev: str) -> PatchFile | None:
        return self.files.get((name, rev))

    def offer(self) -> dict:
        """welcome.patches: {name: {rev: {size, sha1}}} for the files that are there."""
        out: dict = {}
        for (name, rev), pf in sorted(self.files.items()):
            out.setdefault(name, {})[rev] = {"size": pf.size, "sha1": pf.sha1}
        return out

    @staticmethod
    def read(pf: PatchFile, offset: int, n: int) -> bytes:
        with open(pf.path, "rb") as f:
            f.seek(offset)
            return f.read(n)
