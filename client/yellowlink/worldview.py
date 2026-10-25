"""Wide "world view" renderer for Pokémon Yellow (USA/Europe) running in PyBoy.

Draws the overworld *around* the real 160x144 Game Boy screen so the app can
show a larger view (by default 320x288 game pixels).  Pure logic: no pygame,
no app wiring.

Usage::

    wv = WorldView(rom_bytes)                  # margins default to 80 x 72
    # call once per emulated frame, right after pyboy.tick()
    img = wv.render(pyboy.memory, pyboy.screen.ndarray,
                    scroll=pyboy.screen.tilemap_position_list[0][:2])
    if img is None:
        ...                                    # show dim/black margins
    else:
        ...                                    # img: (288, 320, 4) RGBA uint8,
                                               # centre already holds the real
                                               # frame (fill_center=True)
    dx, dy = wv.screen_offset()                # see method docs

How it works (addresses from pret/pokeyellow, verified with the ROM):

* ``wOverworldMap`` ($C6E8) holds the current map's block IDs with a
  3-block border (MAP_BORDER); row stride = width + 6.  It already contains
  the game's own connection strips and border blocks, and reflects runtime
  changes (cut trees, opened doors...).
* Beyond that border we draw connected maps from ROM (map headers via
  MapHeaderPointers/MapHeaderBanks) using the RAM connection headers
  ($D370.., 11 bytes each); everything else gets the map's background block
  (``wMapBackgroundTile``, $D3AC).
* Blocks -> tiles via the current blockset in ROM (``wTilesetBank``/
  ``wTilesetBlocksPtr``).  Tile pixels come straight from VRAM bank 0
  (LCDC bit 4 = 0: tile IDs < $80 at $9000, >= $80 at $8800), so water/flower
  animations follow the game.
* Screen origin in the bordered map at rest = (wXCoord*16 + 32,
  wYCoord*16 + 32).  At rest we remember the relation between that and the
  BG scroll (hSCX/hSCY); the fine scroll of a frame then comes from the SCX/SCY
  the frame was *drawn* with.  When pyboy.tick() returns, the V-blank handler
  has already written the next frame's SCX/SCY, OAM and animated tiles, so the
  frame on screen corresponds to the registers/OAM/VRAM of the previous
  tick: pass ``scroll`` (pyboy.screen.tilemap_position_list[0][:2]) or call
  render() every frame so the previous call's values are used.  The
  walk-counter estimate 2*(8 - wWalkCounter)*stepvector disambiguates, and a
  few nearby candidates are scored against the real frame as a fallback.
* Colours: per colour index, voted from the real screen against our own
  render of the same area (sprite rectangles masked out); cached per map and
  re-voted whenever agreement drops.
* NPCs outside the real screen are drawn from the sprite state tables with
  their VRAM tiles and the game's facing/animation table (one frame behind
  the logic, like the real OAM), with grass priority.  Hidden toggleable
  objects are skipped; Pikachu (slot 15) is not drawn.
* Returns None (see ``last_reason``) in battle, with LCD/BG off, unknown
  tileset, when wTileMap does not match our map (menus, transitions), when
  the real frame disagrees with our render (< min_agreement) or during
  fades to/from white/black.
"""
from __future__ import annotations

import numpy as np

SCREEN_W, SCREEN_H = 160, 144

# --- RAM ---------------------------------------------------------------
W_SPRITE_DATA1 = 0xC100
W_SPRITE_DATA2 = 0xC200
W_TILEMAP = 0xC3A0            # 20x18 screen tile buffer
W_OVERWORLD_MAP = 0xC6E8
W_OVERWORLD_MAP_END = 0xCBFC
W_WALK_COUNTER = 0xCFC4
W_IS_IN_BATTLE = 0xD056
W_CUR_MAP = 0xD35D
W_VIEW_PTR = 0xD35E           # wCurrentTileBlockMapViewPointer
W_Y_COORD = 0xD360
W_X_COORD = 0xD361
W_Y_BLOCK = 0xD362
W_X_BLOCK = 0xD363
W_CUR_TILESET = 0xD366
W_CUR_HEIGHT = 0xD367
W_CUR_WIDTH = 0xD368
W_CONNECTIONS = 0xD370        # N, S, W, E headers, 11 bytes each
W_MAP_BG_TILE = 0xD3AC
W_TILESET_BANK = 0xD52A
W_TILESET_BLOCKS = 0xD52B
W_TOGGLE_FLAGS = 0xD5A5
W_TOGGLE_LIST = 0xD5CD
H_SCX = 0xFFAE
H_SCY = 0xFFAF
H_PIKA_VRAM_OFS = 0xFFFC
R_LCDC = 0xFF40
R_SCY = 0xFF42
R_SCX = 0xFF43
R_WY = 0xFF4A
OAM = 0xFE00

PLAYER_YVEC = 0xC103
PLAYER_XVEC = 0xC105

# --- ROM (bank 3F) -----------------------------------------------------
ROM_MAP_HEADER_POINTERS = 0x3F * 0x4000 + (0x41F2 - 0x4000)
ROM_MAP_HEADER_BANKS = 0x3F * 0x4000 + (0x43E4 - 0x4000)

MAP_BORDER = 3
NUM_TILESETS = 0x19
MAP_TILESET_SIZE = 0x60
NUM_MAPS = 0xF8

# Player stands on the 16x16 square whose top-left is at screen (64, 64);
# sprites are drawn 4 px higher.
PLAYER_SQ_X, PLAYER_SQ_Y = 64, 64

# SpriteFacingAndAnimationTable (data/sprites/facings.asm):
# entry -> list of (dy, dx, tile, xflip)
_SD = [(0, 0, 0, 0), (0, 8, 1, 0), (8, 0, 2, 0), (8, 8, 3, 0)]


def _walk(b):
    return [(0, 0, b, 0), (0, 8, b + 1, 0), (8, 0, b + 2, 0), (8, 8, b + 3, 0)]


def _flip(b):
    return [(0, 8, b, 1), (0, 0, b + 1, 1), (8, 8, b + 2, 1), (8, 0, b + 3, 1)]


_FACINGS = [
    _SD, _walk(0x80), _SD, _flip(0x80),                       # down
    _walk(4), _walk(0x84), _walk(4), _flip(0x84),             # up
    _walk(8), _walk(0x88), _walk(8), _walk(0x88),             # left
    _flip(8), _flip(0x88), _flip(8), _flip(0x88),             # right
]


def _s8(v):
    return v - 256 if v >= 128 else v


def decode_tiles(raw) -> np.ndarray:
    """2bpp tile bytes (n*16) -> (n, 8, 8) uint8 colour indices."""
    a = np.frombuffer(bytes(raw), dtype=np.uint8).reshape(-1, 8, 2)
    lo = np.unpackbits(a[:, :, 0], axis=-1)
    hi = np.unpackbits(a[:, :, 1], axis=-1)
    return (lo | (hi << 1)).reshape(-1, 8, 8)


def bg_tile_index(tile_ids: np.ndarray) -> np.ndarray:
    """BG tile id -> index into the 384-tile VRAM array ($8000-$97FF),
    for LCDC bit 4 = 0 (signed addressing, id 0 at $9000)."""
    t = tile_ids.astype(np.int32)
    return np.where(t < 0x80, t + 256, t)


class _Mem:
    """Small helper around pyboy.memory (or a dict/bytearray in tests)."""

    def __init__(self, m):
        self.m = m

    def __getitem__(self, a):
        return self.m[a]

    def block(self, start, n):
        if n <= 0:
            return b""
        try:
            return bytes(self.m[start:start + n])
        except Exception:
            return bytes(self.m[a] for a in range(start, start + n))

    def vram0(self, start, n):
        try:
            return bytes(self.m[0, start:start + n])
        except Exception:
            try:
                return bytes(self.m[0, a] for a in range(start, start + n))
            except Exception:
                return self.block(start, n)


class WorldView:
    """Render the overworld around the real screen.

    render(memory, screen_rgba, fill_center=True, scroll=None)
        -> (H, W, 4) uint8 RGBA or None
      H = 144 + 2*margin_y, W = 160 + 2*margin_x.  With fill_center the real
      frame is pasted in the middle (on top of NPCs straddling the edge);
      otherwise the centre is left transparent (alpha 0).  `scroll` = the
      (SCX, SCY) the frame was drawn with (pyboy.screen.
      tilemap_position_list[0][:2]); if omitted, the registers seen on the
      previous call are used, which is exact when called every frame.
      The returned array is new each call (safe to keep/modify).

    screen_offset() -> (dx, dy)
      Fine scroll of the last rendered frame: the screen's top-left is
      (dx, dy) pixels away from where it would be with the player standing
      still on (wXCoord, wYCoord); dx, dy in -15..15.  To place a sprite for a
      player standing on map square (X, Y) in the wide image:
          left = margin_x + 64 + (X - wXCoord)*16 - dx
          top  = margin_y + 60 + (Y - wYCoord)*16 - dy
      (use world_to_view() for exactly that).

    world_to_view(map_x, map_y) -> (left, top) for a 16x16 sprite.
    npc_positions(memory) -> [(slot, left, top, image_index, in_grass)].
    world_image() / origin(): map-only render and screen origin of the last
      frame (verification/debugging).
    last_agreement: fraction of non-sprite pixels of the centre region that
      matched the real screen on the last render; last_reason: why the last
      render returned None ("ok" otherwise).
    """

    def __init__(self, rom_bytes: bytes, margin_x: int = 80, margin_y: int = 72,
                 draw_npcs: bool = True, min_agreement: float = 0.55):
        self.rom = bytes(rom_bytes)
        self.mx = int(margin_x)
        self.my = int(margin_y)
        self.out_w = SCREEN_W + 2 * self.mx
        self.out_h = SCREEN_H + 2 * self.my
        self.npcs_enabled = draw_npcs
        self.min_agreement = min_agreement
        # extra blocks around the bordered map so any crop stays inside
        need = max(self.mx, self.my) + 2 * 32 + 32
        self.pad = max(MAP_BORDER + 1, MAP_BORDER + -(-need // 32))
        self._canvas_key = None
        self._tile_ids = None          # canvas of BG tile ids (tiles)
        self._vram_raw = None
        self._tiles = None             # (384, 8, 8)
        self._img_key = None
        self._idx_img = None           # cached colour-index image of canvas
        self._bg_pal = {}              # map id -> (4,4) uint8 RGBA LUT
        self._obj_pal = {}             # cgb obj palette -> (4,4) LUT
        self._offset = (0, 0)
        self._origin = None
        self._prev_scroll = None
        self._prev_vram = None
        self._prev_npcs = None
        self._npc_map = None
        self.last_npcs = []
        self._anchor = None
        self._last_centre = None
        self._last_crop = None
        self._last_lut = None
        self.last_agreement = 0.0
        self.last_reason = ""
        self.player = (0, 0)

    # ------------------------------------------------------------------
    # ROM helpers
    def _rom_addr(self, bank, ptr):
        if ptr < 0x4000:
            return ptr
        return bank * 0x4000 + (ptr - 0x4000)

    def map_header(self, map_id):
        """(tileset, height, width, blocks_file_offset) from ROM, or None."""
        if not 0 <= map_id < NUM_MAPS:
            return None
        r = self.rom
        bank = r[ROM_MAP_HEADER_BANKS + map_id]
        p = r[ROM_MAP_HEADER_POINTERS + 2 * map_id] | (r[ROM_MAP_HEADER_POINTERS + 2 * map_id + 1] << 8)
        h = self._rom_addr(bank, p)
        if h + 5 > len(r):
            return None
        ts, hh, ww = r[h], r[h + 1], r[h + 2]
        bp = r[h + 3] | (r[h + 4] << 8)
        return ts, hh, ww, self._rom_addr(bank, bp)

    def neighbor_offset(self, memory, map_id):
        """Where a connected map sits relative to the current one, in map squares (steps).

        Returns (dx, dy) so that a position (x, y) on `map_id` is (x + dx, y + dy) in the current
        map's coordinates — the same placement _build_canvas uses. None if not directly connected.
        """
        mem = _Mem(memory)
        w, h = mem[W_CUR_WIDTH], mem[W_CUR_HEIGHT]
        raw = mem.block(W_CONNECTIONS, 44)
        for d in range(4):
            c = raw[11 * d:11 * d + 11]
            if c[0] == 0xFF or c[0] != map_id:
                continue
            hdr = self.map_header(map_id)
            if hdr is None:
                return None
            _, ch, cw, _ = hdr
            if d in (0, 1):
                c0 = -_s8(c[8]) // 2
                r0 = -ch if d == 0 else h
            else:
                r0 = -_s8(c[7]) // 2
                c0 = -cw if d == 2 else w
            return c0 * 2, r0 * 2
        return None

    # ------------------------------------------------------------------
    def _blockset(self, bank, ptr):
        start = self._rom_addr(bank, ptr)
        end = min(len(self.rom), start + 256 * 16)
        raw = self.rom[start:end]
        raw = raw + bytes(256 * 16 - len(raw))
        return np.frombuffer(raw, dtype=np.uint8).reshape(256, 4, 4)

    def _build_canvas(self, mem, w, h, ow_bytes, bg_block, conns, blockset):
        """Block canvas: map at (pad, pad) in block units -> tile-id canvas."""
        P = self.pad
        CH, CW = h + 2 * P, w + 2 * P
        blocks = np.full((CH, CW), bg_block, dtype=np.uint8)
        # connected maps (north, south, west, east, like LoadTileBlockMap)
        for d, c in enumerate(conns):
            if c is None:
                continue
            cid, yal, xal = c
            hdr = self.map_header(cid)
            if hdr is None:
                continue
            _, ch, cw, bofs = hdr
            if ch == 0 or cw == 0 or bofs + ch * cw > len(self.rom):
                continue
            cm = np.frombuffer(self.rom[bofs:bofs + ch * cw], dtype=np.uint8).reshape(ch, cw)
            if d in (0, 1):
                o = -_s8(xal) // 2
                r0 = -ch if d == 0 else h
                c0 = o
            else:
                o = -_s8(yal) // 2
                c0 = -cw if d == 2 else w
                r0 = o
            r0 += P
            c0 += P
            ra, rb = max(r0, 0), min(r0 + ch, CH)
            ca, cb = max(c0, 0), min(c0 + cw, CW)
            if ra < rb and ca < cb:
                blocks[ra:rb, ca:cb] = cm[ra - r0:rb - r0, ca - c0:cb - c0]
        # the game's own bordered map always wins
        stride = w + 2 * MAP_BORDER
        ow = np.frombuffer(ow_bytes, dtype=np.uint8).reshape(h + 2 * MAP_BORDER, stride)
        o = P - MAP_BORDER
        blocks[o:o + h + 6, o:o + stride] = ow
        tiles = blockset[blocks]                      # (CH, CW, 4, 4)
        return tiles.transpose(0, 2, 1, 3).reshape(CH * 4, CW * 4)

    # ------------------------------------------------------------------
    def _reject(self, why):
        self.last_reason = why
        return None

    def _state(self, mem):
        if mem[W_IS_IN_BATTLE] != 0:
            return self._reject("battle")
        lcdc = mem[R_LCDC]
        if not (lcdc & 0x80) or not (lcdc & 0x01):
            return self._reject("lcd/bg off")
        if lcdc & 0x18:
            return self._reject("unexpected LCDC addressing")
        ts = mem[W_CUR_TILESET]
        if ts >= NUM_TILESETS:
            return self._reject("unknown tileset")
        w, h = mem[W_CUR_WIDTH], mem[W_CUR_HEIGHT]
        if w == 0 or h == 0 or (w + 6) * (h + 6) > W_OVERWORLD_MAP_END - W_OVERWORLD_MAP:
            return self._reject("bad map size")
        return ts, w, h

    def _origin_candidates(self, mem, scroll):
        """Possible screen top-left positions (pixels in the bordered map) for
        the frame on screen, most likely first.  Each: (ox, oy, dx, dy)."""
        x, y = _s8(mem[W_X_COORD]), _s8(mem[W_Y_COORD])
        c = mem[W_WALK_COUNTER]
        vx, vy = _s8(mem[PLAYER_XVEC]), _s8(mem[PLAYER_YVEC])
        rx, ry = x * 16 + 32, y * 16 + 32            # origin when standing still
        moved = 2 * (8 - c) if 0 < c < 8 else 0
        ex, ey = rx + vx * moved, ry + vy * moved      # walk-counter estimate
        if c == 0:
            # at rest the game's scroll registers are aligned with (x, y):
            # remember the BG-scroll <-> map-pixel relation.
            self._anchor = ((mem[H_SCX] - rx) & 0xFF, (mem[H_SCY] - ry) & 0xFF)
        out = []
        if self._anchor is not None:
            ax, ay = self._anchor
            scrolls = [] if scroll is None else [tuple(scroll)]
            scrolls.append((mem[R_SCX], mem[R_SCY]))
            for sx, sy in scrolls:
                ox = ex + _s8((sx - ax - ex) & 0xFF)
                oy = ey + _s8((sy - ay - ey) & 0xFF)
                if abs(ox - ex) <= 4 and abs(oy - ey) <= 4:
                    out.append((ox, oy))
        out.append((ex, ey))
        for k in (-2, 2, -4, 4):
            out.append((ex + k * vx, ey + k * vy))
        res, seen = [], set()
        for ox, oy in out:
            if (ox, oy) not in seen:
                seen.add((ox, oy))
                res.append((ox, oy, ox - rx, oy - ry))
        return res

    def _fresh_lut(self, centre, packed, valid, old):
        nl, found = self._vote(centre, packed, valid)
        if old is not None:
            nl[~found] = old[~found]
        elif not found.all():
            ramp = np.array([[248, 248, 248, 255], [168, 168, 168, 255],
                             [88, 88, 88, 255], [24, 24, 24, 255]], np.uint8)
            nl[~found] = ramp[~found]
        return nl

    def _tiles_check(self, mem, tile_ids):
        """Compare wTileMap with our canvas at the logic view (block level)."""
        vp = mem[W_VIEW_PTR] | (mem[W_VIEW_PTR + 1] << 8)
        w = mem[W_CUR_WIDTH]
        rel = vp - W_OVERWORLD_MAP
        if rel < 0:
            return 0.0, 0
        br, bc = divmod(rel, w + 6)
        tr = (br + self.pad - MAP_BORDER) * 4 + 2 * (mem[W_Y_BLOCK] & 1)
        tc = (bc + self.pad - MAP_BORDER) * 4 + 2 * (mem[W_X_BLOCK] & 1)
        sub = tile_ids[tr:tr + 18, tc:tc + 20]
        if sub.shape != (18, 20):
            return 0.0, 0
        tm = np.frombuffer(mem.block(W_TILEMAP, 360), dtype=np.uint8).reshape(18, 20)
        comparable = tm < MAP_TILESET_SIZE
        n = int(comparable.sum())
        if n == 0:
            return 0.0, 0
        return float((tm[comparable] == sub[comparable]).sum()) / n, n

    def _sprite_mask(self, mem):
        """Boolean (144,160) mask of pixels covered by OAM sprites."""
        oam = mem.block(OAM, 160)
        mask = np.zeros((SCREEN_H, SCREEN_W), dtype=bool)
        tall = mem[R_LCDC] & 0x04
        sh = 16 if tall else 8
        for i in range(40):
            y, x = oam[4 * i] - 16, oam[4 * i + 1] - 8
            if y <= -sh or y >= SCREEN_H or x <= -8 or x >= SCREEN_W:
                continue
            mask[max(y, 0):max(y + sh, 0), max(x, 0):max(x + 8, 0)] = True
        return mask, oam

    @staticmethod
    def _pack(rgb):
        rgb = rgb.astype(np.uint32)
        return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]

    def _vote(self, idx, packed, valid):
        lut = np.zeros((4, 4), dtype=np.uint8)
        found = np.zeros(4, dtype=bool)
        for k in range(4):
            sel = packed[(idx == k) & valid]
            if sel.size == 0:
                continue
            vals, cnt = np.unique(sel, return_counts=True)
            v = int(vals[cnt.argmax()])
            lut[k] = ((v >> 16) & 255, (v >> 8) & 255, v & 255, 255)
            found[k] = True
        return lut, found

    # ------------------------------------------------------------------
    def render(self, memory, screen_rgba, fill_center: bool = True, scroll=None):
        mem = _Mem(memory)
        prev = self._prev_scroll
        self._prev_scroll = (mem[R_SCX], mem[R_SCY])
        if scroll is None:
            scroll = prev
        raw_now = mem.vram0(0x8000, 0x1800)
        prev_vram, self._prev_vram = self._prev_vram, raw_now
        # NPC positions: the frame on screen shows the previous logic state
        cur_npcs = self.npc_positions(mem) if self.npcs_enabled else []
        if self._npc_map != mem[W_CUR_MAP]:
            self._prev_npcs = None
        self._npc_map = mem[W_CUR_MAP]
        prev_npcs, self._prev_npcs = self._prev_npcs, cur_npcs
        shown_npcs = prev_npcs if prev_npcs is not None else cur_npcs
        st = self._state(mem)
        if st is None:
            return None
        ts, w, h = st
        cur_map = mem[W_CUR_MAP]

        # --- block canvas (cached on map/tileset/block data/connections) ---
        ow_bytes = mem.block(W_OVERWORLD_MAP, (w + 6) * (h + 6))
        conn_raw = mem.block(W_CONNECTIONS, 44)
        bank = mem[W_TILESET_BANK]
        bptr = mem[W_TILESET_BLOCKS] | (mem[W_TILESET_BLOCKS + 1] << 8)
        bg_block = mem[W_MAP_BG_TILE]
        key = (cur_map, ts, w, h, bank, bptr, bg_block, ow_bytes, conn_raw)
        if key != self._canvas_key:
            conns = []
            for d in range(4):
                c = conn_raw[11 * d:11 * d + 11]
                conns.append(None if c[0] == 0xFF else (c[0], c[7], c[8]))
            self._tile_ids = self._build_canvas(mem, w, h, ow_bytes, bg_block, conns,
                                                self._blockset(bank, bptr))
            self._canvas_key = key
            self._img_key = None

        # --- tile graphics from VRAM bank 0 ---
        # The frame on screen was drawn before this V-blank's VRAM updates
        # (tile animations), so use the tile data seen on the previous call.
        raw = prev_vram if prev_vram is not None else raw_now
        if raw != self._vram_raw:
            self._vram_raw = raw
            self._tiles = decode_tiles(raw)
            self._img_key = None
        if self._img_key is None:
            ti = bg_tile_index(self._tile_ids)
            th, tw = ti.shape
            img = self._tiles[ti]                    # (th, tw, 8, 8)
            self._idx_img = img.transpose(0, 2, 1, 3).reshape(th * 8, tw * 8)
            self._img_key = True

        # --- sanity: the game's screen tile buffer must match our canvas ---
        frac, n = self._tiles_check(mem, self._tile_ids)
        if n < 60 or frac < 0.9:
            return self._reject(f"tilemap mismatch ({frac:.2f} of {n})")

        # --- origin candidates / crop ---
        scr = np.asarray(screen_rgba)[:, :, :3]
        mask, oam = self._sprite_mask(mem)
        # colour checks run on every other pixel/row (plenty of samples)
        valid = ~mask[::2, ::2]
        packed = self._pack(scr[::2, ::2])
        lut = self._bg_pal.get(cur_map)
        base = (self.pad - MAP_BORDER) * 32
        H, W = self._idx_img.shape
        cands = []
        for cand in self._origin_candidates(mem, scroll):
            ox, oy = cand[0], cand[1]
            x0, y0 = ox + base - self.mx, oy + base - self.my
            if x0 < 0 or y0 < 0 or x0 + self.out_w > W or y0 + self.out_h > H:
                continue
            crop = self._idx_img[y0:y0 + self.out_h, x0:x0 + self.out_w]
            centre = crop[self.my:self.my + SCREEN_H, self.mx:self.mx + SCREEN_W]
            cands.append((cand, crop, centre))
        if not cands:
            return self._reject("view outside canvas")
        cand, crop, centre = cands[0]
        agree = -1.0
        if lut is not None:
            agree = self._agreement(lut, centre[::2, ::2], packed, valid)
        if agree < 0.97:
            # palette may be fading or the first guess is off: score every
            # candidate with its own freshly voted palette; keep the first
            # (most likely) one unless another is clearly better.
            best = None
            for c in cands:
                nl = self._fresh_lut(c[2][::2, ::2], packed, valid, lut)
                a = self._agreement(nl, c[2][::2, ::2], packed, valid)
                if best is None or a > best[0] + 0.01:
                    best = (a, nl, c)
            agree, lut, (cand, crop, centre) = best
        self._bg_pal[cur_map] = lut
        ox, oy, dx, dy = cand
        self.last_agreement = agree
        if agree < self.min_agreement:
            return self._reject(f"screen disagrees ({agree:.2f})")
        used = np.nonzero(np.bincount(centre[::2, ::2].ravel(), minlength=4))[0]
        if len({tuple(c) for c in lut[used].tolist()}) < min(3, len(used)):
            return self._reject("degenerate palette (fade/transition)")

        lut32 = np.ascontiguousarray(lut).view(np.uint32).reshape(4)
        out = lut32[crop].view(np.uint8).reshape(self.out_h, self.out_w, 4)
        self._last_centre = centre
        self._last_lut = lut
        self._last_crop = crop
        self._offset = (dx, dy)
        self._origin = (ox, oy)
        self.player = (mem[W_X_COORD], mem[W_Y_COORD])

        if self.npcs_enabled:
            self._learn_obj_palettes(scr, oam)
            self.last_npcs = shown_npcs
            self.draw_npcs(out, crop, shown_npcs, mem[H_PIKA_VRAM_OFS])

        if fill_center:
            out[self.my:self.my + SCREEN_H, self.mx:self.mx + SCREEN_W, :3] = scr
            out[self.my:self.my + SCREEN_H, self.mx:self.mx + SCREEN_W, 3] = 255
        else:
            out[self.my:self.my + SCREEN_H, self.mx:self.mx + SCREEN_W] = 0
        self.last_reason = "ok"
        return out

    def _agreement(self, lut, centre, packed, valid):
        pred = self._pack(lut[:, :3])[centre]
        n = int(valid.sum())
        if n == 0:
            return 0.0
        return float(((pred == packed) & valid).sum()) / n

    def world_image(self):
        """Wide RGBA image of the map alone (no NPCs, no real frame) from the
        last successful render, including our prediction of the centre.
        Allocates; meant for verification/debugging."""
        if self._last_crop is None:
            return None
        return self._last_lut[self._last_crop]

    def origin(self):
        """Screen top-left of the last rendered frame in pixels of the
        bordered map (wOverworldMap coordinates, 32 px per block)."""
        return self._origin

    # ------------------------------------------------------------------
    def screen_offset(self):
        """(dx, dy) fine scroll of the last rendered frame, see class doc."""
        return self._offset

    def world_to_view(self, map_x, map_y):
        """Top-left in the wide image of a 16x16 overworld sprite standing on
        map square (map_x, map_y) of the current map (4 px above the grid,
        like the game draws sprites)."""
        px, py = self.player
        dx, dy = self._offset
        return (self.mx + PLAYER_SQ_X + (map_x - px) * 16 - dx,
                self.my + PLAYER_SQ_Y - 4 + (map_y - py) * 16 - dy)

    # ------------------------------------------------------------------
    def _obj_tile(self, tile_id):
        return self._tiles[tile_id & 0xFF]           # $8000-based sprite tiles

    def _learn_obj_palettes(self, scr, oam):
        """Vote sprite colours per CGB OBJ palette from visible OAM entries."""
        keys, cols = [], []
        for i in range(40):
            y, x, t, a = oam[4 * i] - 16, oam[4 * i + 1] - 8, oam[4 * i + 2], oam[4 * i + 3]
            if y < 0 or x < 0 or y + 8 > SCREEN_H or x + 8 > SCREEN_W or a & 0x80:
                continue                                # clipped or behind BG
            tile = self._obj_tile(t)
            if a & 0x20:
                tile = tile[:, ::-1]
            if a & 0x40:
                tile = tile[::-1]
            nz = tile != 0
            keys.append((a & 7) * 4 + tile[nz].astype(np.int64))
            cols.append(self._pack(scr[y:y + 8, x:x + 8][nz]).astype(np.int64))
        if not keys:
            return
        combo = np.concatenate(keys) << 24 | np.concatenate(cols)
        vals, cnt = np.unique(combo, return_counts=True)
        order = np.argsort(cnt, kind="stable")          # ascending: last wins
        best = {}
        for v in vals[order]:
            best[int(v) >> 24] = int(v) & 0xFFFFFF
        for key, rgb in best.items():
            pal, k = divmod(key, 4)
            lut = self._obj_pal.get(pal)
            lut = np.zeros((4, 4), np.uint8) if lut is None else lut.copy()
            lut[k] = ((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255, 255)
            lut[0] = 0
            self._obj_pal[pal] = lut

    def _hidden_slots(self, mem):
        hidden = set()
        lst = mem.block(W_TOGGLE_LIST, 34)
        flags = mem.block(W_TOGGLE_FLAGS, 32)
        for i in range(0, 32, 2):
            slot = lst[i]
            if slot == 0xFF:
                break
            g = lst[i + 1]
            if flags[g >> 3] & (1 << (g & 7)):
                hidden.add(slot)
        return hidden

    def npc_positions(self, mem):
        """[(slot, left, top, img, in_grass)] of the current map's NPCs in
        wide-image coordinates, for the game's *current logic state* (the
        frame on screen lags this by one frame; render() accounts for it).
        Slot 0 (player) and 15 (Pikachu) are not included."""
        mem = mem if isinstance(mem, _Mem) else _Mem(mem)
        d1 = mem.block(W_SPRITE_DATA1, 256)
        d2 = mem.block(W_SPRITE_DATA2, 256)
        hidden = self._hidden_slots(mem)
        px, py = mem[W_X_COORD], mem[W_Y_COORD]
        c = mem[W_WALK_COUNTER]
        moved = 2 * (8 - c) if 0 < c < 8 else 0
        odx = _s8(mem[PLAYER_XVEC]) * moved
        ody = _s8(mem[PLAYER_YVEC]) * moved
        res = []
        for s in range(1, 15):
            a, b = d1[16 * s:16 * s + 16], d2[16 * s:16 * s + 16]
            if a[0] == 0 or s in hidden:
                continue
            img = a[2]
            if img == 0xFF:
                if b[0xE] == 0:
                    continue
                img = (((b[0xE] - 1) << 4) + a[9] + (a[8] & 3)) & 0xFF
            mx, my = b[5] - 4, b[4] - 4
            ex = PLAYER_SQ_X + (mx - px) * 16 - odx
            ey = PLAYER_SQ_Y - 4 + (my - py) * 16 - ody
            # pixel bytes are exact screen positions (mod 256) when consistent
            ax = ex + _s8((a[6] - ex) & 0xFF)
            ay = ey + _s8((a[4] - ey) & 0xFF)
            if abs(ax - ex) < 16:
                ex = ax
            if abs(ay - ey) < 16:
                ey = ay
            res.append((s, ex + self.mx, ey + self.my, img, bool(b[7] & 0x80)))
        return res

    def npc_sprite(self, img, pika_ofs=0):
        """16x16 colour indices (0 = transparent) for a sprite image index,
        built like PrepareOAMData does (SpriteFacingAndAnimationTable)."""
        hi = img >> 4
        base = 0x7C if hi == 0xB else hi * 12
        entry = _FACINGS[0] if img >= 0xA0 else _FACINGS[img & 0xF]
        spr = np.zeros((16, 16), np.uint8)
        for dy, dx, t, fl in entry:
            tid = (base + t) & 0xFF
            if tid >= 0x80:
                tid = (tid + pika_ofs) & 0xFF
            tile = self._obj_tile(tid)
            if fl:
                tile = tile[:, ::-1]
            spr[dy:dy + 8, dx:dx + 8] = tile
        return spr

    def draw_npcs(self, out, crop, npcs, pika_ofs=0, include_onscreen=False):
        """Draw NPCs (from npc_positions) into the wide RGBA image `out`.
        `crop` is the wide colour-index BG image (for grass priority: the
        lower half of a sprite standing in grass only shows over BG colour 0).
        By default NPCs fully inside the real screen are skipped."""
        pal = self._obj_pal.get(0)
        if pal is None:
            pal = np.array([[0, 0, 0, 0], [248, 248, 248, 255],
                            [168, 168, 168, 255], [24, 24, 24, 255]], np.uint8)
        for _s, left, top, img, grass in npcs:
            sl, st = left - self.mx, top - self.my
            if not include_onscreen and sl >= 0 and st >= 0 \
                    and sl + 16 <= SCREEN_W and st + 16 <= SCREEN_H:
                continue
            y0, x0 = max(top, 0), max(left, 0)
            y1, x1 = min(top + 16, self.out_h), min(left + 16, self.out_w)
            if y0 >= y1 or x0 >= x1:
                continue
            spr = self.npc_sprite(img, pika_ofs)
            sub = spr[y0 - top:y1 - top, x0 - left:x1 - left]
            m = sub != 0
            if grass:
                rows = np.arange(y0 - top, y1 - top)[:, None] >= 8
                m &= ~rows | (crop[y0:y1, x0:x1] == 0)
            region = out[y0:y1, x0:x1]
            region[m] = pal[sub[m]]
