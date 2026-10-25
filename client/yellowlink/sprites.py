"""Render friends' overworld sprites from raw Game Boy tile data."""
from __future__ import annotations

from collections import Counter

import pygame

PLAYER_SCREEN_X = 64
PLAYER_SCREEN_Y = 60

# Fallback colors (index 1..3) if we can't sample the real palette yet.
DEFAULT_PALETTE = [(0, 0, 0, 0), (248, 232, 200, 255), (224, 80, 56, 255), (24, 24, 24, 255)]


def decode_tile(t: bytes):
    """16 bytes of 2bpp -> 8 rows of 8 color indices."""
    rows = []
    for r in range(8):
        lo, hi = t[r * 2], t[r * 2 + 1]
        rows.append([((lo >> (7 - c)) & 1) | (((hi >> (7 - c)) & 1) << 1) for c in range(8)])
    return rows


def tile_index_in_blob(tile_id: int) -> int:
    """Our 384-byte blob holds tiles 0x00-0x0B then 0x80-0x8B."""
    return tile_id if tile_id < 0x80 else 12 + (tile_id - 0x80)


def frame_layout(img: int):
    """Return (base_tile_id, hflip) for an image index byte."""
    d, f = img & 0xC, img & 3
    base = 8 if d == 12 else d
    if f in (1, 3):
        base += 0x80
    flip = d == 12 or (d in (0, 4) and f == 3)
    return base, flip


def compose(blob: bytes, img: int):
    """16x16 grid of color indices for the given image index."""
    base, flip = frame_layout(img)
    grid = [[0] * 16 for _ in range(16)]
    for k, (ox, oy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
        ti = tile_index_in_blob(base + k) * 16
        rows = decode_tile(blob[ti:ti + 16])
        for r in range(8):
            for c in range(8):
                grid[oy + r][ox + c] = rows[r][c]
    if flip:
        grid = [list(reversed(row)) for row in grid]
    return grid


def sample_palette(screen_rgba, blob: bytes, img: int):
    """Learn index->RGB by comparing our own sprite to the rendered screen.

    screen_rgba: numpy array (144,160,4). Returns list of 4 RGBA tuples or None.
    """
    grid = compose(blob, img)
    votes = {1: Counter(), 2: Counter(), 3: Counter()}
    for r in range(16):
        for c in range(16):
            idx = grid[r][c]
            if idx:
                p = screen_rgba[PLAYER_SCREEN_Y + r][PLAYER_SCREEN_X + c]
                votes[idx][(int(p[0]), int(p[1]), int(p[2]))] += 1
    pal = [(0, 0, 0, 0)]
    for idx in (1, 2, 3):
        if not votes[idx]:
            return None
        color, count = votes[idx].most_common(1)[0]
        pal.append((*color, 255))
    return pal


class SpriteCache:
    def __init__(self):
        self._cache = {}

    def get(self, blob: bytes, img: int, palette) -> pygame.Surface:
        key = (hash(blob), img & 0xF, tuple(palette))
        surf = self._cache.get(key)
        if surf is None:
            grid = compose(blob, img)
            surf = pygame.Surface((16, 16), pygame.SRCALPHA)
            for r in range(16):
                for c in range(16):
                    if grid[r][c]:
                        surf.set_at((c, r), palette[grid[r][c]])
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[key] = surf
        return surf
