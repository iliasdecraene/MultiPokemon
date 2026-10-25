"""Gen-1 character encoding (subset that matters for names)."""

TERMINATOR = 0x50

_ENC = {" ": 0x7F, "(": 0x9A, ")": 0x9B, ":": 0x9C, ";": 0x9D, "[": 0x9E, "]": 0x9F,
        "'": 0xE0, "-": 0xE3, "?": 0xE6, "!": 0xE7, ".": 0xE8, "/": 0xF3, ",": 0xF4,
        "♂": 0xEF, "♀": 0xF5, "*": 0xF1}
for _i in range(26):
    _ENC[chr(65 + _i)] = 0x80 + _i
    _ENC[chr(97 + _i)] = 0xA0 + _i
for _i in range(10):
    _ENC[str(_i)] = 0xF6 + _i
_DEC = {v: k for k, v in _ENC.items()}
_DEC[0xE1] = "PK"
_DEC[0xE2] = "MN"
_DEC[0x54] = "POKé"


def encode(s: str, length: int = 11) -> bytes:
    """Encode to a fixed-size, 0x50-terminated/padded buffer (max length-1 chars)."""
    out = [_ENC.get(ch, 0x7F) for ch in s[: length - 1]]
    out += [TERMINATOR] * (length - len(out))
    return bytes(out)


def decode(b) -> str:
    out = []
    for v in b:
        if v == TERMINATOR:
            break
        out.append(_DEC.get(v, "?"))
    return "".join(out)


def sanitize_name(s: str, maxlen: int = 10) -> str:
    """Keep only characters the protocol allows for player names."""
    s = "".join(ch for ch in s if ch.isascii() and (ch.isalnum() or ch == " ")).strip()
    return s[:maxlen]
