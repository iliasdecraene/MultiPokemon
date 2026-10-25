"""Unit tests for yellowlink.worldview with a synthetic ROM/RAM/VRAM (no real ROM).

The fake world is built with the same layout rules as the game; the expected
pictures are computed here independently with plain loops.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import worldview as wvm  # noqa: E402
from yellowlink.worldview import WorldView  # noqa: E402

MX, MY = 80, 72
W, H = 4, 3            # current map size in blocks
NW, NH = 4, 5          # north-connected map size
N_OFS = 1              # connected col 0 == current col 1
BG_BLOCK = 7
PALETTE = [(250, 250, 250), (170, 100, 240), (80, 180, 240), (20, 20, 20)]
CUR_BLOCKS = [(r * W + c) % 16 + 16 for r in range(H) for c in range(W)]
NORTH_BLOCKS = [(r * NW + c) % 16 + 32 for r in range(NH) for c in range(NW)]


def block_tile(b, r, c):
    return (b * 16 + r * 4 + c) % 0x60


def tile_pixel(t, y, x):
    return ((t * 7 + y * 3 + x * 5 + (t >> 2) * y) >> 1) % 4


def tile_bytes(pix):
    """8x8 colour indices -> 16 bytes 2bpp."""
    out = bytearray()
    for y in range(8):
        lo = hi = 0
        for x in range(8):
            v = pix(y, x)
            lo |= (v & 1) << (7 - x)
            hi |= ((v >> 1) & 1) << (7 - x)
        out += bytes([lo, hi])
    return bytes(out)


class FakeMem:
    """Enough of pyboy.memory: m[a], m[a:b], m[bank, a], m[bank, a:b]."""

    def __init__(self):
        self.ram = bytearray(0x10000)
        self.vram1 = bytearray(0x2000)

    def _get(self, bank, a):
        if bank == 1 and 0x8000 <= a < 0xA000:
            return self.vram1[a - 0x8000]
        return self.ram[a]

    def __getitem__(self, k):
        bank = None
        if isinstance(k, tuple):
            bank, k = k
        if isinstance(k, slice):
            return [self._get(bank, a) for a in range(k.start, k.stop)]
        return self._get(bank, k)

    def __setitem__(self, a, v):
        self.ram[a] = v


def build_rom():
    rom = bytearray(0x40 * 0x4000)
    # map 0 / map 1 headers in bank 1, blocks in bank 1, blockset in bank 2
    def put_map(mid, hdr_ptr, blocks_ptr, h, w, blocks):
        rom[wvm.ROM_MAP_HEADER_BANKS + mid] = 1
        rom[wvm.ROM_MAP_HEADER_POINTERS + 2 * mid] = hdr_ptr & 0xFF
        rom[wvm.ROM_MAP_HEADER_POINTERS + 2 * mid + 1] = hdr_ptr >> 8
        h_ofs = 0x4000 + hdr_ptr - 0x4000
        rom[h_ofs:h_ofs + 5] = bytes([0, h, w, blocks_ptr & 0xFF, blocks_ptr >> 8])
        b_ofs = 0x4000 + blocks_ptr - 0x4000
        rom[b_ofs:b_ofs + len(blocks)] = bytes(blocks)
    put_map(0, 0x4000, 0x4100, H, W, CUR_BLOCKS)
    put_map(1, 0x4020, 0x4200, NH, NW, NORTH_BLOCKS)
    for b in range(64):
        for r in range(4):
            for c in range(4):
                rom[0x8000 + b * 16 + r * 4 + c] = block_tile(b, r, c)
    return bytes(rom)


def expected_blocks():
    """Block grid in bordered-map coordinates extended by 6 blocks each way:
    returns f(row, col) for bordered-map block coordinates."""
    def f(r, c):
        mr, mc = r - 3, c - 3                    # current-map coords
        if 0 <= mr < H and 0 <= mc < W:
            return CUR_BLOCKS[mr * W + mc]
        nr, nc = mr + NH, mc - N_OFS              # north map coords
        if 0 <= nr < NH and 0 <= nc < NW:
            return NORTH_BLOCKS[nr * NW + nc]
        return BG_BLOCK
    return f


def world_pixel_idx(px, py):
    """Colour index at bordered-map pixel (px, py)."""
    f = expected_blocks()
    b = f(py // 32, px // 32)
    t = block_tile(b, (py % 32) // 8, (px % 32) // 8)
    return tile_pixel(t, py % 8, px % 8)


def make_state(x=2, y=1, walk=0, vec=(0, 0), scx=0, scy=0, battle=0, tileset=1):
    m = FakeMem()
    stride = W + 6
    # wOverworldMap with border: copy what LoadTileBlockMap would produce
    f = expected_blocks()
    for r in range(H + 6):
        for c in range(stride):
            mr, mc = r - 3, c - 3
            inside = 0 <= mr < H and 0 <= mc < W
            north_strip = r < 3 and 0 <= mc - N_OFS < NW
            m[wvm.W_OVERWORLD_MAP + r * stride + c] = f(r, c) if (inside or north_strip) else BG_BLOCK
    m[wvm.W_CUR_MAP] = 0
    m[wvm.W_CUR_TILESET] = tileset
    m[wvm.W_CUR_WIDTH] = W
    m[wvm.W_CUR_HEIGHT] = H
    m[wvm.W_MAP_BG_TILE] = BG_BLOCK
    m[wvm.W_TILESET_BANK] = 2
    m[wvm.W_TILESET_BLOCKS] = 0x00
    m[wvm.W_TILESET_BLOCKS + 1] = 0x40
    m[wvm.W_X_COORD], m[wvm.W_Y_COORD] = x, y
    m[wvm.W_X_BLOCK], m[wvm.W_Y_BLOCK] = x & 1, y & 1
    vp = wvm.W_OVERWORLD_MAP + (y // 2 + 1) * stride + (x // 2 + 1)
    m[wvm.W_VIEW_PTR], m[wvm.W_VIEW_PTR + 1] = vp & 0xFF, vp >> 8
    m[wvm.W_WALK_COUNTER] = walk
    m[wvm.PLAYER_XVEC], m[wvm.PLAYER_YVEC] = vec[0] & 0xFF, vec[1] & 0xFF
    m[wvm.R_SCX] = m[wvm.H_SCX] = scx & 0xFF
    m[wvm.R_SCY] = m[wvm.H_SCY] = scy & 0xFF
    m[wvm.R_LCDC] = 0xE3
    m[wvm.R_WY] = 144
    m[wvm.W_IS_IN_BATTLE] = battle
    for d in range(4):
        m[wvm.W_CONNECTIONS + 11 * d] = 0xFF
    # north connection: map 1, x alignment = -2*offset
    m[wvm.W_CONNECTIONS + 0] = 1
    m[wvm.W_CONNECTIONS + 8] = (-2 * N_OFS) & 0xFF
    m[wvm.W_TOGGLE_LIST] = 0xFF
    # OAM: all sprites hidden
    for i in range(40):
        m[wvm.OAM + 4 * i] = 0
    # BG tiles at $9000 (ids < $80)
    for t in range(0x60):
        tb = tile_bytes(lambda yy, xx, t=t: tile_pixel(t, yy, xx))
        m.ram[0x9000 + t * 16:0x9000 + t * 16 + 16] = tb
    # wTileMap = 20x18 tiles of the view at rest
    tr0, tc0 = (y // 2 + 1) * 4 + 2 * (y & 1), (x // 2 + 1) * 4 + 2 * (x & 1)
    for r in range(18):
        for c in range(20):
            br, bc = (tr0 + r) // 4, (tc0 + c) // 4
            m[wvm.W_TILEMAP + r * 20 + c] = block_tile(f(br, bc), (tr0 + r) % 4, (tc0 + c) % 4)
    return m


def screen_for(ox, oy):
    scr = np.zeros((144, 160, 4), np.uint8)
    scr[..., 3] = 255
    for yy in range(144):
        for xx in range(160):
            scr[yy, xx, :3] = PALETTE[world_pixel_idx(ox + xx, oy + yy)]
    return scr


def expected_wide(ox, oy):
    out = np.zeros((144 + 2 * MY, 160 + 2 * MX, 3), np.uint8)
    for yy in range(out.shape[0]):
        for xx in range(out.shape[1]):
            out[yy, xx] = PALETTE[world_pixel_idx(ox - MX + xx, oy - MY + yy)]
    return out


@pytest.fixture(scope="module")
def rom():
    return build_rom()


def test_decode_tiles_and_bg_index():
    raw = tile_bytes(lambda y, x: (x + y) % 4)
    t = wvm.decode_tiles(raw)
    assert t.shape == (1, 8, 8)
    assert all(t[0, y, x] == (x + y) % 4 for y in range(8) for x in range(8))
    idx = wvm.bg_tile_index(np.array([0, 0x7F, 0x80, 0xFF]))
    assert idx.tolist() == [256, 383, 128, 255]


def test_map_header_from_rom(rom):
    wv = WorldView(rom)
    ts, h, w, bofs = wv.map_header(1)
    assert (h, w) == (NH, NW)
    assert rom[bofs:bofs + NW * NH] == bytes(NORTH_BLOCKS)


def test_render_at_rest_matches_world_and_connection(rom):
    x, y = 2, 0                                   # top edge: north map visible
    m = make_state(x=x, y=y)
    ox, oy = x * 16 + 32, y * 16 + 32
    wv = WorldView(rom, MX, MY)
    img = wv.render(m, screen_for(ox, oy), fill_center=False)
    assert img is not None, wv.last_reason
    assert img.shape == (288, 320, 4)
    assert wv.screen_offset() == (0, 0)
    assert wv.last_agreement == 1.0
    exp = expected_wide(ox, oy)
    world = wv.world_image()[..., :3]
    assert np.array_equal(world, exp)
    # margins drawn, centre transparent
    assert (img[MY:MY + 144, MX:MX + 160, 3] == 0).all()
    assert np.array_equal(img[:MY, :, :3], exp[:MY])
    # the top rows really come from the north map beyond the 3-block border
    top_left_block = expected_blocks()((oy - MY) // 32, (ox - MX + 160) // 32)
    assert top_left_block in NORTH_BLOCKS


def test_fill_center_pastes_real_frame(rom):
    m = make_state()
    scr = screen_for(2 * 16 + 32, 1 * 16 + 32)
    wv = WorldView(rom, MX, MY)
    img = wv.render(m, scr)
    assert np.array_equal(img[MY:MY + 144, MX:MX + 160], scr)


def test_mid_step_scrolls_in_lockstep(rom):
    wv = WorldView(rom, MX, MY)
    x, y = 2, 1
    m = make_state(x=x, y=y)
    assert wv.render(m, screen_for(x * 16 + 32, y * 16 + 32)) is not None   # learns anchor
    # walking right, walk counter 5 -> 6 px moved; screen drawn with SCX=6
    m[wvm.W_WALK_COUNTER] = 5
    m[wvm.PLAYER_XVEC] = 1
    m[wvm.R_SCX] = m[wvm.H_SCX] = 6
    ox, oy = x * 16 + 32 + 6, y * 16 + 32
    img = wv.render(m, screen_for(ox, oy), scroll=(6, 0))
    assert img is not None, wv.last_reason
    assert wv.screen_offset() == (6, 0)
    assert np.array_equal(wv.world_image()[..., :3], expected_wide(ox, oy))
    # without an explicit scroll the previous call's registers are used:
    # the frame on screen lags the registers by one frame
    m[wvm.W_WALK_COUNTER] = 4
    m[wvm.R_SCX] = m[wvm.H_SCX] = 8
    img = wv.render(m, screen_for(ox, oy))         # still showing SCX=6
    assert wv.screen_offset() == (6, 0)
    img = wv.render(m, screen_for(ox + 2, oy))     # now SCX=8 is on screen
    assert wv.screen_offset() == (8, 0)


def test_world_to_view(rom):
    wv = WorldView(rom, MX, MY)
    m = make_state(x=2, y=1)
    wv.render(m, screen_for(64, 48))
    assert wv.world_to_view(2, 1) == (MX + 64, MY + 60)
    assert wv.world_to_view(0, 1) == (MX + 32, MY + 60)


@pytest.mark.parametrize("kw,reason", [
    ({"battle": 1}, "battle"),
    ({"tileset": 0x30}, "unknown tileset"),
])
def test_returns_none_when_unreliable(rom, kw, reason):
    wv = WorldView(rom, MX, MY)
    m = make_state(**kw)
    assert wv.render(m, screen_for(64, 48)) is None
    assert wv.last_reason.startswith(reason)


def test_returns_none_for_full_screen_menu(rom):
    wv = WorldView(rom, MX, MY)
    m = make_state()
    for i in range(360):                       # wTileMap full of map-ish tiles that do not match
        m[wvm.W_TILEMAP + i] = (i * 7 + 3) % 0x60
    assert wv.render(m, screen_for(64, 48)) is None
    assert wv.last_reason.startswith("tilemap mismatch")


def test_returns_none_when_screen_disagrees(rom):
    wv = WorldView(rom, MX, MY)
    m = make_state()
    noise = np.random.default_rng(1).integers(0, 255, (144, 160, 4), dtype=np.uint8)
    assert wv.render(m, noise) is None


def test_block_change_rerenders(rom):
    wv = WorldView(rom, MX, MY)
    x, y = 2, 1
    m = make_state(x=x, y=y)
    scr = screen_for(x * 16 + 32, y * 16 + 32)
    wv.render(m, scr)
    before = wv.world_image().copy()
    # change a block far outside the real screen (bottom-right border block)
    addr = wvm.W_OVERWORLD_MAP + (H + 5) * (W + 6) + (W + 5)
    m[addr] = 40
    wv.render(m, scr)
    assert not np.array_equal(before, wv.world_image())


def _setup_npc(m, slot, mapx, mapy, base=2, facing=0):
    d1, d2 = wvm.W_SPRITE_DATA1 + 16 * slot, wvm.W_SPRITE_DATA2 + 16 * slot
    m[d1 + 0] = 5               # picture id
    m[d1 + 2] = 0xFF            # off screen
    m[d1 + 9] = facing
    px, py = m[wvm.W_X_COORD], m[wvm.W_Y_COORD]
    m[d1 + 4] = (60 + (mapy - py) * 16) & 0xFF     # screen pixels (mod 256)
    m[d1 + 6] = (64 + (mapx - px) * 16) & 0xFF
    m[d2 + 4], m[d2 + 5] = mapy + 4, mapx + 4
    m[d2 + 0xE] = base
    # sprite tiles for VRAM slot base-1: 12 tiles at $8000 + (base-1)*$C0
    for k in range(12):
        a = 0x8000 + (base - 1) * 0xC0 + k * 16
        m.ram[a:a + 16] = tile_bytes(lambda yy, xx, k=k: 3 if (xx + k) % 3 == 0 else 0)


def test_npc_drawn_outside_screen_and_hidden_ones_skipped(rom):
    x, y = 2, 1
    m = make_state(x=x, y=y)
    _setup_npc(m, 1, mapx=x - 5, mapy=y)          # 80 px left of the player: in the margin
    wv = WorldView(rom, MX, MY)
    scr = screen_for(x * 16 + 32, y * 16 + 32)
    img = wv.render(m, scr)
    left, top = wv.world_to_view(x - 5, y)
    assert (left, top) == (MX + 64 - 80, MY + 60)
    npcs = wv.npc_positions(m)
    assert npcs[0][:4] == (1, left, top, 0x10)
    spr = wv.npc_sprite(0x10)
    assert spr.shape == (16, 16) and (spr != 0).any()
    region = img[top:top + 16, left:left + 16]
    drawn = spr != 0
    assert (region[drawn][:, :3] == [24, 24, 24]).all()   # fallback palette colour 3
    world = wv.world_image()[top:top + 16, left:left + 16]
    assert np.array_equal(region[~drawn], world[~drawn])
    # hide it through the toggleable-object list (slot 1 -> global index 5)
    m[wvm.W_TOGGLE_LIST], m[wvm.W_TOGGLE_LIST + 1], m[wvm.W_TOGGLE_LIST + 2] = 1, 5, 0xFF
    m[wvm.W_TOGGLE_FLAGS] = 1 << 5
    assert wv.npc_positions(m) == []


def test_npc_facing_table_flips_right():
    wv = WorldView(build_rom(), MX, MY)
    raw = bytearray(0x1800)
    for k in range(24):
        raw[k * 16:k * 16 + 16] = tile_bytes(lambda yy, xx, k=k: 1 if xx == 0 else 0)
    wv._tiles = wvm.decode_tiles(bytes(raw))
    left = wv.npc_sprite(0x08)        # facing left, standing
    right = wv.npc_sprite(0x0C)       # facing right = mirrored left
    assert np.array_equal(right, left[:, ::-1])
