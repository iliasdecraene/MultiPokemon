"""Friends in Sinnoh (client/yellowlink/sinnoh_view.py): projection maths, sprite decoding, network packing.

The camera fixtures are the game's Camera struct bytes read from RAM in two dev states (Twinleaf Town outdoors,
perspective; Sandgem Pokémon Center indoors, orthographic). Expected screen positions were measured on the real
top screen (dev/e2e/pt_friends.py checks the same thing live).
"""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import sinnoh_view as sv  # noqa: E402

# Camera bytes (0x48) from pt_town.dsst: player MapObject pos = (1864, 16, 14200), facing down
TOWN_CAM = bytes.fromhex(
    "40020000d70f00005515000000600900004038000080740021b824007aee8c03008074000000010000807703"
    "000000000010000000000000c1ae290002d60000000000000000c105")
# pt_pc.dsst (Sandgem Pokémon Center): player pos = (200, 0, 72)
PC_CAM = bytes.fromhex(
    "fb000000f80f0000551500000060090000706c0000800c008edd4a00b132430000800c0000000000008004"
    "000000000000100000000000009bb8610062dc00000000000001008102")


class Bus:
    def __init__(self, mem):
        self.mem = mem

    def r32(self, a):
        return struct.unpack("<I", self.read(a, 4))[0]

    def r16(self, a):
        return struct.unpack("<H", self.read(a, 2))[0]

    def read(self, a, n):
        out = bytearray()
        for i in range(n):
            out.append(self.mem.get(a + i, 0))
        return bytes(out)

    def put(self, a, data):
        for i, b in enumerate(data):
            self.mem[a + i] = b


def test_camera_parse_town():
    cam = sv.Camera.from_bytes(TOWN_CAM)
    assert cam is not None and cam.projection == 0
    assert cam.target == (1864.0, 16.0, 14200.0)
    assert abs(cam.distance - 666.922) < 0.01
    assert abs(cam.near - 150) < 1e-6 and abs(cam.far - 900) < 1e-6


def test_perspective_target_is_screen_centre():
    cam = sv.Camera.from_bytes(TOWN_CAM)
    x, y, z = cam.project(cam.target)
    assert abs(x - 128) < 1e-6 and abs(y - 96) < 1e-6 and z < 0
    # right in the world = right on screen, south (+z) = lower on screen, nearer = bigger
    assert cam.project((1880, 16, 14200))[0] > 128
    assert cam.project((1864, 16, 14216))[1] > 96
    near = cam.billboard((1864, 16, 14280))
    far = cam.billboard((1864, 16, 14120))
    assert near[2] > far[2] > 0


def test_billboard_matches_real_player_sprite():
    """The player's sprite in pt_town is drawn at top-left (113, 69), 32-33 px wide (measured on screen)."""
    cam = sv.Camera.from_bytes(TOWN_CAM)
    left, top, w, h, depth = cam.billboard(sv.billboard_anchor(1856, 16, 14192))
    assert round(left) == 113 and round(top) == 69
    assert 32 < w < 33 and abs(w - h) < 1e-9 and depth < 0


def test_billboard_npcs_outdoors():
    """NPC moved to tiles around the player in pt_town: where the game drew its 32x32 quad (dev/e2e/pt_friends.py:
    the NPC's own texture drawn at these (left, top) matched the screen with 0 px best shift)."""
    cam = sv.Camera.from_bytes(TOWN_CAM)
    for (dx, dz), (left, top) in {(-3, -3): (66, 30), (4, 1): (179, 83), (5, 3): (197, 112)}.items():
        r = cam.billboard(sv.billboard_anchor((116 + dx) * 16, 16, (887 + dz) * 16))
        assert abs(r[0] - left) <= 1 and abs(r[1] - top) <= 1, ((dx, dz), r)


def test_orthographic_interior():
    cam = sv.Camera.from_bytes(PC_CAM)
    assert cam.projection == 1
    assert cam.project(cam.target)[:2] == pytest.approx((128, 96))
    # an NPC standing at pos (216, 0, 88) is drawn exactly in (128, 81)-(160, 113) (orthographic: always 32x32)
    left, top, w, h, _ = cam.billboard((216, 0, 88 + sv.BILLBOARD_Z))
    assert (round(left), round(top), round(w), round(h)) == (128, 81, 32, 32)
    assert abs(top + h - 112.9) < 0.1


def test_camera_rejects_garbage():
    assert sv.Camera.from_bytes(bytes(0x48)) is None
    assert sv.Camera.from_bytes(b"\x01" * 0x48) is None
    assert sv.Camera.from_bytes(TOWN_CAM[:0x20]) is None


def test_read_camera_and_field_check():
    bus = Bus({})
    bus.put(sv.CAMERA_PTR, struct.pack("<I", 0x02300000))
    bus.put(0x02300000, TOWN_CAM)
    fs = 0x02200000
    bus.put(fs + sv.FS_CAMERA, struct.pack("<I", 0x02300000))
    assert sv.read_camera(bus, fs).target == (1864.0, 16.0, 14200.0)
    bus.put(fs + sv.FS_CAMERA, struct.pack("<I", 0x02300100))      # a cutscene camera is active instead
    assert sv.read_camera(bus, fs) is None
    bus.put(sv.CAMERA_PTR, struct.pack("<I", 0))
    assert sv.read_camera(bus) is None


def test_display_lag_history():
    view = sv.SinnohView.__new__(sv.SinnohView)
    view.cams = []
    cams = {f: sv.Camera.from_bytes(TOWN_CAM) for f in range(10, 20)}
    bus = Bus({})
    for f in range(10, 20):
        view.cams.append((f, cams[f], ("me", f)))
    cam, me = view.display(19)
    assert cam is cams[19 - sv.DISPLAY_LAG] and me == ("me", 19 - sv.DISPLAY_LAG)
    view.cams.append((20, None, None))                    # camera gone (warp/battle) -> nothing drawn
    assert view.display(20) == (None, None)
    view.cams.clear()
    view.record_camera(bus, None, 5)                      # no field system: records "no camera"
    assert view.cams == [(5, None, None)]


def test_map_space_rules():
    matrix = {411: 0, 342: 0, 420: 116, 421: 116, 414: 128}.get
    assert sv.same_space(420, 420, matrix)
    assert sv.same_space(411, 342, matrix)                 # Twinleaf + Route 201: one outdoor grid
    assert not sv.same_space(420, 421, matrix)             # two maps sharing a layout are still different places
    assert not sv.same_space(411, 420, matrix)
    bus = Bus({})
    bus.put(sv.MAP_HEADERS + 24 * 420 + 2, struct.pack("<H", 116))
    cache = {}
    assert sv.map_matrix(bus, 420, cache) == 116 and cache == {420: 116}
    assert sv.map_matrix(bus, 9999) == -1


def test_img_packing():
    for g, run, state, ph in ((0, False, 0, 16), (1, True, 0, 0), (1, False, 1, -32), (0, True, 2, 4096),
                              (1, False, 17, 7)):
        info = sv.unpack_img(sv.pack_img(g, run, state, ph))
        assert info == {"gender": g, "running": run, "kind": state if state in (0, 1, 2) else 3, "ph": ph}
    assert sv.unpack_img(None) == {"gender": 0, "running": False, "kind": 0, "ph": 0}


def test_frames_and_anchor():
    assert sv.frame_index(0) == 0 and sv.frame_index(1) == 4 and sv.frame_index(3, 3) == 15
    assert sv.frame_index(2, 1, running=True) == 25
    assert sv.billboard_anchor(1856, 16, 14192) == (1864, 16, 14206)
    assert [sv.walk_step(a) for a in (0, 7, 8, 16, 24, 32)] == [0, 0, 1, 2, 3, 0]


def test_glide_speeds():
    # walking: target moves 4 units every 4 frames -> friend moves 1 unit every frame
    x, moved = 0, []
    for f in range(16):
        t = (f // 4 + 1) * 4
        x, _y, m = sv.glide(x, 0, t, 0)
        moved.append(m)
    assert moved == [1] * 16
    x, y, m = sv.glide(0, 0, 8, 0)                        # running
    assert (x, m) == (2, 2)
    x, y, m = sv.glide(0, 0, 0, -16)                      # cycling, north
    assert (y, m) == (-4, 4)
    assert sv.glide(0, 0, 200, 0) == (200, 0, 0)          # warp: snap
    assert sv.glide(5, 5, 5, 5) == (5, 5, 0)


def _btx0(w, h, fmt, indices, palette, name="tex.1", color0=True):
    """Minimal BTX0 with one texture + one palette (NNS layout, same as the ROM's)."""
    def dict_block(names, entries, unit):
        n = len(names)
        out = struct.pack("<BBH", 0, n, 0)
        out += struct.pack("<HHI", 8, 12 + 4 * n, 0) + b"\0" * (4 * n)   # patricia tree (unused by us)
        out += struct.pack("<HH", unit, 4 + unit * n) + b"".join(entries)
        out += b"".join(nm.encode().ljust(16, b"\0") for nm in names)
        return out
    bpp = {2: 2, 3: 4, 4: 8}[fmt]
    px = bytearray()
    per = 8 // bpp
    for i in range(0, len(indices), per):
        b = 0
        for k in range(per):
            b |= indices[i + k] << (k * bpp)
        px.append(b)
    p0 = 0 | (({8: 0, 16: 1, 32: 2}[w]) << 20) | (({8: 0, 16: 1, 32: 2}[h]) << 23) | (fmt << 26) | (int(color0) << 29)
    tex_info = dict_block([name], [struct.pack("<II", p0, 0)], 8)
    pal_info = dict_block(["pal"], [struct.pack("<HH", 0, 0)], 4)
    pal = b"".join(struct.pack("<H", c) for c in palette)
    head = 0x3C
    tex_info_off = head
    pal_info_off = tex_info_off + len(tex_info)
    tex_data_off = pal_info_off + len(pal_info)
    pal_data_off = tex_data_off + len(px)
    tex0 = bytearray(b"TEX0" + struct.pack("<I", 0) + bytes(head - 8))
    struct.pack_into("<HH", tex0, 0x0C, len(px) >> 3, tex_info_off)
    struct.pack_into("<I", tex0, 0x14, tex_data_off)
    struct.pack_into("<III", tex0, 0x30, len(pal) >> 3, pal_info_off, pal_data_off)
    tex0 += tex_info + pal_info + px + pal
    struct.pack_into("<I", tex0, 4, len(tex0))
    return b"BTX0" + struct.pack("<HHIHHI", 0xFEFF, 1, 20 + len(tex0), 16, 1, 20) + bytes(tex0)


def test_decode_btx0_16_colour():
    idx = [(x + y) % 16 for y in range(8) for x in range(8)]
    pal = [0x7C1F] + [(i << 10) | (i << 5) | i for i in range(1, 16)]   # colour 0 magenta, then greys
    tex = sv.decode_btx0(_btx0(8, 8, 3, idx, pal, name="pl_test.1"))
    assert tex["name"] == "pl_test" and set(tex["frames"]) == {0}
    w, h, rgba = tex["frames"][0]
    assert (w, h, len(rgba)) == (8, 8, 256)
    assert rgba[3] == 0                                      # colour 0 transparent
    assert rgba[4:8] == bytes((8, 8, 8, 255))                # grey 1 = 5-bit 1 -> 8-bit 8
    assert rgba[(7 * 8 + 7) * 4:(7 * 8 + 7) * 4 + 4] == bytes((115, 115, 115, 255))   # index 14


def test_decode_btx0_4_colour_and_errors():
    idx = [x % 4 for y in range(8) for x in range(8)]
    tex = sv.decode_btx0(_btx0(8, 8, 2, idx, [0, 0x1F, 0x3E0, 0x7C00], name="t.2", color0=False))
    w, h, rgba = tex["frames"][1]
    assert rgba[0:4] == bytes((0, 0, 0, 255)) and rgba[4:8] == bytes((255, 0, 0, 255))
    with pytest.raises(ValueError):
        sv.decode_btx0(b"NARC" + bytes(40))


def test_tint_estimate_recovers_light():
    idx = [((x // 4) + (y // 4)) % 15 + 1 for y in range(32) for x in range(32)]
    pal = [0] + [((i * 2) << 10) | ((31 - i) << 5) | (i + 10) for i in range(15)]
    frame = sv.decode_btx0(_btx0(32, 32, 3, idx, pal))["frames"][0]
    screen = bytearray(256 * 192 * 3)
    rect = (100.3, 50.6, 32.0, 32.0)
    gain = (0.53, 0.52, 0.9)
    for sx, sy, u, v in sv.sample_quad(rect, 32, 32):
        t = (v * 32 + u) * 4
        for c in range(3):
            screen[(sy * 256 + sx) * 3 + c] = int(frame[2][t + c] * gain[c])
    est = sv.estimate_tint(bytes(screen), rect, frame)
    assert est is not None and all(abs(a - b) < 0.02 for a, b in zip(est, gain))
    assert sv.estimate_tint(bytes(screen), (10, 10, 32, 32), frame) is None    # nothing there


def test_sample_quad_pixel_rule():
    """A pixel belongs to the quad when its centre is inside (no extra row for a top just above a centre)."""
    rows = sorted({sy for _sx, sy, _u, v in sv.sample_quad((0.0, 129.933, 32.0, 32.0), 32, 32) if v == 0})
    assert rows == [130]
    assert {v for *_rest, v in sv.sample_quad((0.0, 129.933, 32.0, 32.0), 32, 32)} == set(range(32))


ROM = os.path.join(os.path.dirname(__file__), "..", "..", "dev", "states", "platinum.nds")


@pytest.mark.skipif(not os.path.exists(ROM), reason="needs the Platinum ROM (dev/states/platinum.nds)")
def test_player_sprites_from_rom():
    sprites = sv.player_sprites(ROM)
    assert set(sprites) == {(0, 0), (1, 0), (0, 1), (1, 1)}
    assert sprites[(0, 0)]["name"] == "pl_boy01c" and sprites[(1, 0)]["name"] == "pl_girl01c"
    for key, tex in sprites.items():
        fr = tex["frames"]
        assert len(fr) == (32 if key[1] == 0 else 24)
        w, h, rgba = fr[sv.frame_index(1)]
        assert (w, h) == (32, 32)
        assert rgba[3] == 0                                   # corner transparent
        assert sum(1 for i in range(3, len(rgba), 4) if rgba[i]) > 150   # a person-sized sprite
