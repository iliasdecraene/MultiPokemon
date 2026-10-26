"""Tiny Gen-4 Pokémon record codec for dev/platinum/verify.py (the real one is client/yellowlink/pk4.py).

Record = 136-byte box part (+ 100-byte party extension = 236 bytes in the party):
  0x00 u32 PID, 0x04 u16 flags, 0x06 u16 checksum, 0x08..0x87 four 32-byte blocks A/B/C/D,
  shuffled by ((PID >> 13) & 31) % 24 and XORed with the LCRNG (x*0x41C64E6D + 0x6073, high 16 bits)
  seeded with the checksum; the party extension 0x88..0xEB is XORed with the LCRNG seeded with the PID.
  checksum = sum of the 64 decrypted u16 words of 0x08..0x87 (mod 0x10000).
"""
import struct

ORDERS = ["ABCD", "ABDC", "ACBD", "ACDB", "ADBC", "ADCB", "BACD", "BADC", "BCAD", "BCDA", "BDAC", "BDCA",
          "CABD", "CADB", "CBAD", "CBDA", "CDAB", "CDBA", "DABC", "DACB", "DBAC", "DBCA", "DCAB", "DCBA"]


def _xor(data: bytes, seed: int) -> bytes:
    out = bytearray(data)
    for i in range(0, len(out), 2):
        seed = (seed * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        w = struct.unpack_from("<H", out, i)[0] ^ (seed >> 16)
        struct.pack_into("<H", out, i, w)
    return bytes(out)


def checksum(plain: bytes) -> int:
    return sum(struct.unpack_from("<64H", plain, 8)) & 0xFFFF


def decrypt(rec: bytes) -> bytes:
    """Encrypted 136/236-byte record -> plain record with blocks in A,B,C,D order."""
    pid, _flags, chk = struct.unpack_from("<IHH", rec, 0)
    body = _xor(rec[8:0x88], chk)
    order = ORDERS[((pid >> 13) & 31) % 24]
    blocks = [body[i * 32:(i + 1) * 32] for i in range(4)]
    plain = b"".join(blocks[order.index(c)] for c in "ABCD")
    out = rec[:8] + plain
    if len(rec) > 0x88:
        out += _xor(rec[0x88:], pid)
    return out


def encrypt(plain: bytes) -> bytes:
    pid, _flags, chk = struct.unpack_from("<IHH", plain, 0)
    order = ORDERS[((pid >> 13) & 31) % 24]
    blocks = [plain[8 + i * 32:8 + (i + 1) * 32] for i in range(4)]
    body = b"".join(blocks["ABCD".index(c)] for c in order)
    out = plain[:8] + _xor(body, chk)
    if len(plain) > 0x88:
        out += _xor(plain[0x88:], pid)
    return out


# Gen-4 text (only what the checks need): 0x0121-0x012A '0'-'9', 0x012B-0x0144 'A'-'Z', 0x0145-0x015E 'a'-'z',
# 0xFFFF terminator, 0xE000 newline.
def decode_text(data: bytes) -> str:
    s = []
    for i in range(0, len(data) - 1, 2):
        c = struct.unpack_from("<H", data, i)[0]
        if c == 0xFFFF:
            break
        if 0x121 <= c <= 0x12A:
            s.append(chr(ord("0") + c - 0x121))
        elif 0x12B <= c <= 0x144:
            s.append(chr(ord("A") + c - 0x12B))
        elif 0x145 <= c <= 0x15E:
            s.append(chr(ord("a") + c - 0x145))
        elif c == 0x1DE:
            s.append(" ")
        else:
            s.append("?")
    return "".join(s)


def info(rec: bytes) -> dict:
    """A few decoded fields of an encrypted record (for checks/debugging)."""
    p = decrypt(rec)
    species, item, tid, sid = struct.unpack_from("<HHHH", p, 0x08)
    exp = struct.unpack_from("<I", p, 0x10)[0]
    d = {"pid": struct.unpack_from("<I", p, 0)[0], "species": species, "item": item, "tid": tid, "sid": sid,
         "exp": exp, "checksum_ok": checksum(p) == struct.unpack_from("<H", p, 6)[0],
         "nickname": decode_text(p[0x48:0x48 + 22]), "ot": decode_text(p[0x68:0x68 + 16]),
         "moves": list(struct.unpack_from("<4H", p, 0x28))}
    if len(p) > 0x88:
        d["level"] = p[0x8C]
        d["hp"], d["max_hp"] = struct.unpack_from("<HH", p, 0x8E)
    return d
