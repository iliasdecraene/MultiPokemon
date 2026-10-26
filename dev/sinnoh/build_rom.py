"""Build the Complete Sinnoh ROM for the dev harnesses and compute its SHA1 (dev only, needs dev/states/platinum.nds).

    python dev/sinnoh/build_rom.py              # -> dev/states/platinum-sinnoh.nds from sinnoh_complete.py
                                                #    (dev/sinnoh/test_spec.py while sinnoh_complete.py doesn't exist)
    python dev/sinnoh/build_rom.py --spec test  # force the tiny test spec (-> dev/states/platinum-sinnoh-test.nds)
    python dev/sinnoh/build_rom.py --update     # after changing sinnoh_complete.py: build twice (determinism),
                                                #    then store {digest: sha1} in client/yellowlink/sinnoh_rom.py
Prints: spec version, digest, SHA1, changed tables/bytes and every byte range that differs from vanilla (labelled:
wild tables, script NARC, FAT entry, header, the relocated Pokédex area NARC).
"""
import argparse
import os
import re
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "client"))
sys.path.insert(0, HERE)
STATES = os.environ.get("YL_STATES", os.path.join(ROOT, "dev", "states"))
VANILLA = os.environ.get("YL_PLATINUM_ROM") or os.path.join(STATES, "platinum.nds")

from yellowlink import encdata, ndsrom, sinnoh_rom  # noqa: E402


def get_spec(which: str):
    if which in ("auto", "real"):
        spec = sinnoh_rom.load_spec()      # (YL_SINNOH_SPEC=test makes this the test spec)
        if spec is not None and spec.__name__.endswith("sinnoh_complete"):
            return spec, "sinnoh_complete"
        if which == "real" and not sinnoh_rom.dev_test_spec_on():
            return spec, "sinnoh_complete"
    import test_spec
    return test_spec, "test_spec"


def out_path(label: str) -> str:
    return os.path.join(STATES, "platinum-sinnoh.nds" if label == "sinnoh_complete" else "platinum-sinnoh-test.nds")


def diff_tables(a: str, b: str):
    ra, rb = ndsrom.NDSRom(a), ndsrom.NDSRom(b)
    na, nb = ra.narc(encdata.NARC_PATH), rb.narc(encdata.NARC_PATH)
    tables = [i for i in range(len(na)) if na[i] != nb[i]]
    nbytes = sum(x != y for i in tables for x, y in zip(na[i], nb[i]))
    ra.close()
    rb.close()
    return tables, nbytes


def diff_ranges(a: str, b: str, gap: int = 64) -> list:
    """[(start, end)] of the bytes that differ between two files of equal size (ranges closer than `gap` merged)."""
    out = []
    with open(a, "rb") as fa, open(b, "rb") as fb:
        pos = 0
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if not x:
                break
            if x != y:
                for i in range(len(x)):
                    if x[i] != y[i]:
                        p = pos + i
                        if out and p - out[-1][1] <= gap:
                            out[-1][1] = p + 1
                        else:
                            out.append([p, p + 1])
            pos += len(x)
    return [tuple(r) for r in out]


def label_ranges(rom_path: str, ranges: list) -> list:
    r = ndsrom.NDSRom(rom_path)
    files = {p: r.extent(p) for p in (encdata.NARC_PATH, sinnoh_rom.SCRIPT_NARC_PATH, sinnoh_rom.AREA_NARC_PATH)}
    hdr = r._read(0, 0x200)
    fat_off, fat_size = struct.unpack_from("<II", hdr, 0x48)
    r.close()
    out = []
    for lo, hi in ranges:
        what = "?"
        for p, (s, n) in files.items():
            if s <= lo and hi <= s + n:
                what = p
        if fat_off <= lo and hi <= fat_off + fat_size:
            what = "FAT entries %d..%d" % ((lo - fat_off) // 8, (hi - 1 - fat_off) // 8)
        elif hi <= 0x200:
            what = "NDS header"
        out.append((lo, hi, what))
    return out


def ensure_dev_rom(which: str = "auto") -> str:
    """Path of an up-to-date dev ROM for `which` (used by dev/platinum/ptrom.py); builds it when missing/stale."""
    spec, label = get_spec(which)
    dest = out_path(label)
    want = sinnoh_rom.expected_sha1(spec)
    stamp = dest + ".digest"
    digest = sinnoh_rom.spec_digest(spec)
    if os.path.exists(dest) and os.path.exists(stamp):
        with open(stamp) as f:
            ok = f.read().strip() == digest
        if ok and (want is None or sinnoh_rom._sha1(dest) == want):
            return dest
    sinnoh_rom.build(VANILLA, dest, spec, expected=want)
    with open(stamp, "w") as f:
        f.write(digest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", choices=["auto", "real", "test"], default="auto")
    ap.add_argument("--update", action="store_true", help="store the SHA1 for the current sinnoh_complete.py")
    a = ap.parse_args()
    spec, label = get_spec("real" if a.update else a.spec)
    if spec is None:
        sys.exit("client/yellowlink/sinnoh_complete.py doesn't exist yet")
    digest = sinnoh_rom.spec_digest(spec)
    dest = out_path(label)
    sha = sinnoh_rom.build(VANILLA, dest, spec, expected=None)
    with open(dest + ".digest", "w") as f:
        f.write(digest)
    with tempfile.TemporaryDirectory(dir=STATES) as tmp:
        again = sinnoh_rom.build(VANILLA, os.path.join(tmp, "again.nds"), spec, expected=None)
    tables, nbytes = diff_tables(VANILLA, dest)
    print(f"spec {label} v{spec.SPEC_VERSION}  digest {digest}")
    print(f"ROM  {dest}")
    print(f"SHA1 {sha}  (second build: {'same' if again == sha else 'DIFFERENT ' + again})")
    print(f"changed tables: {len(tables)}  changed bytes: {nbytes}  edits: {len(spec.EDITS)} slots + "
          f"{len(getattr(spec, 'RATE_EDITS', ()))} rates")
    for lo, hi, what in label_ranges(dest, diff_ranges(VANILLA, dest)):
        print(f"  differs {lo:#010x}..{hi:#010x} ({hi - lo:6d} bytes)  {what}")
    if again != sha:
        sys.exit("NOT deterministic")
    if a.update:
        path = os.path.join(ROOT, "client", "yellowlink", "sinnoh_rom.py")
        src = open(path, encoding="utf-8").read()
        line = f'    "{digest}": ("{sha}", {spec.SPEC_VERSION}),\n'
        if digest in src:
            print("already in EXPECTED")
        else:
            src = re.sub(r"(EXPECTED = \{\n)", lambda m: m.group(1) + line, src, count=1)
            open(path, "w", encoding="utf-8").write(src)
            print("added to sinnoh_rom.EXPECTED:", line.strip())
    cur = sinnoh_rom.expected_sha1(spec)
    if label == "sinnoh_complete" and not a.update and cur != sha:
        print("NOTE: sinnoh_rom.EXPECTED has", cur, "for this spec - run with --update")


if __name__ == "__main__":
    main()
