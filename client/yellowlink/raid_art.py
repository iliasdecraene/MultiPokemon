"""The raid den and its edge-of-screen arrow, drawn in code (our own art, nothing from the games).

The den is a stack of rough stones with a glowing core on top - in the spirit of Spiritomb's Hallowed Tower
(Ilias's decision 12: the same look in all three games). Two sizes:
  gb_den()  16 x 24 pixel art for the Game Boy screens (stands on one 16 x 16 map square, the top 8 px above it)
  ds_den()  32 x 48 for Platinum's top screen (scaled by the camera like a friend's billboard)
plus `arrow()` (a small arrow + step count in a 3x5 pixel font) for when the den is off screen.
"""
from __future__ import annotations

import math

import pygame

# stone shades (light -> dark), outline, glow
STONE = [(196, 190, 204), (150, 143, 160), (108, 101, 120), (70, 64, 82)]
OUTLINE = (34, 30, 44)
GLOW = (186, 92, 255)
GLOW_CORE = (246, 220, 255)

# 16 x 24: '.' transparent, 'o' outline, 1-3 stone shades (light..dark), 'g' glow, 'c' glow core
_GB_ART = [
    "......oooo......",
    ".....oggggo.....",
    "....oggccggo....",
    "....oggccggo....",
    ".....oggggo.....",
    "....oo1111oo....",
    "...o11222233o...",
    "...o12222333o...",
    "....oo3333oo....",
    "...oo111111oo...",
    "..o1122222233o..",
    "..o1222222333o..",
    "..o2223333333o..",
    "...oo333333oo...",
    "..oo11111111oo..",
    ".o112222222233o.",
    ".o122222222333o.",
    ".o122222233333o.",
    ".o222233333333o.",
    "..oo33333333oo..",
    ".oo1111111111oo.",
    "o11222222222333o",
    "o22223333333333o",
    ".oooooooooooooo.",
]


def _pulse(t: float) -> float:
    return 0.5 + 0.5 * math.sin(t * 4.0)


def gb_den(t: float = 0.0) -> pygame.Surface:
    """16 x 24 RGBA surface; t (seconds) makes the core pulse."""
    s = pygame.Surface((16, 24), pygame.SRCALPHA)
    p = _pulse(t)
    glow = tuple(int(GLOW[i] * (0.75 + 0.25 * p)) for i in range(3))
    for y, row in enumerate(_GB_ART):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            if ch == "o":
                col = OUTLINE
            elif ch == "g":
                col = glow
            elif ch == "c":
                col = GLOW_CORE
            else:
                col = STONE[int(ch) - 1]
            s.set_at((x, y), col + (255,))
    return s


def ds_den(t: float = 0.0, w: int = 32, h: int = 48) -> pygame.Surface:
    """A smoother version for the DS top screen (drawn at 32 x 48, the caller scales it to the billboard)."""
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.ellipse(s, (0, 0, 0, 80), (2, h - 7, w - 4, 7))
    # stones from the bottom up: (x, y, width, height)
    stones = [(2, h - 14, w - 4, 10), (5, h - 23, w - 10, 10), (7, h - 31, w - 14, 9), (10, h - 38, w - 20, 8)]
    for i, (x, y, ww, hh) in enumerate(stones):
        base = STONE[1] if i % 2 == 0 else STONE[2]
        pygame.draw.ellipse(s, base, (x, y, ww, hh))
        pygame.draw.ellipse(s, OUTLINE, (x, y, ww, hh), 1)
        pygame.draw.arc(s, STONE[0], (x + 2, y + 1, ww - 4, hh - 2), math.pi * 0.15, math.pi * 0.85, 1)
    p = _pulse(t)
    r = 5 + int(2 * p)
    halo = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.circle(halo, GLOW + (int(70 + 60 * p),), (w // 2, h - 42), r + 3)
    s.blit(halo, (0, 0))
    pygame.draw.circle(s, GLOW, (w // 2, h - 42), r)
    pygame.draw.circle(s, GLOW_CORE, (w // 2, h - 42), max(1, r // 2))
    return s


# 3 x 5 digits (our own), for the step count next to the arrow
_DIGITS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
    "8": "111101111101111", "9": "111101111001111", "+": "000010111010000",
}


def _digits(text: str, col) -> pygame.Surface:
    s = pygame.Surface((max(1, 4 * len(text) - 1), 5), pygame.SRCALPHA)
    for i, ch in enumerate(text):
        bits = _DIGITS.get(ch)
        if not bits:
            continue
        for k, b in enumerate(bits):
            if b == "1":
                s.set_at((4 * i + k % 3, k // 3), col)
    return s


def arrow(surface: pygame.Surface, rect, dx: float, dy: float, steps: int | None = None, t: float = 0.0):
    """Draw an arrow on the edge of `rect` (x, y, w, h) pointing along (dx, dy), with the step count."""
    x0, y0, w, h = rect
    if dx == 0 and dy == 0:
        return
    cx, cy = x0 + w / 2, y0 + h / 2
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    margin = 9
    # distance from the centre to the rect edge along (ux, uy)
    kx = (w / 2 - margin) / abs(ux) if ux else float("inf")
    ky = (h / 2 - margin) / abs(uy) if uy else float("inf")
    k = min(kx, ky)
    px, py = cx + ux * k, cy + uy * k
    p = _pulse(t)
    size = 5 + p
    tip = (px + ux * size, py + uy * size)
    left = (px - ux * size - uy * size * 0.8, py - uy * size + ux * size * 0.8)
    right = (px - ux * size + uy * size * 0.8, py - uy * size - ux * size * 0.8)
    pygame.draw.polygon(surface, OUTLINE, [(tip[0] + ux, tip[1] + uy), left, right])
    pygame.draw.polygon(surface, GLOW, [tip, left, right])
    pygame.draw.polygon(surface, OUTLINE, [tip, left, right], 1)
    if steps is not None:
        txt = str(steps) if steps < 1000 else "999+"
        d = _digits(txt, GLOW_CORE + (255,))
        bg = pygame.Surface((d.get_width() + 2, d.get_height() + 2), pygame.SRCALPHA)
        bg.fill(OUTLINE + (200,))
        bx = int(min(max(px - ux * 12 - bg.get_width() / 2, x0 + 1), x0 + w - bg.get_width() - 1))
        by = int(min(max(py - uy * 12 - bg.get_height() / 2, y0 + 1), y0 + h - bg.get_height() - 1))
        surface.blit(bg, (bx, by))
        surface.blit(d, (bx + 1, by + 1))
