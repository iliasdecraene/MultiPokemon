"""Friends walking around in Sinnoh: draw other Pokémon Platinum players into the DS top screen.

Platinum's overworld is 3D: every person is a 32x32 textured quad ("billboard") that always faces the camera,
standing on its MapObject position. To draw a friend exactly like a real NPC we
  1. read the game's active camera (lookAt + projection) from RAM,
  2. project the friend's world position to the 256x192 top screen the same way the DS does
     (NNS_G3dGlbLookAt + G3_Perspective / G3_Ortho, viewport 256x192),
  3. draw Lucas/Dawn's overworld frames, decoded from the player's OWN ROM at runtime (never stored anywhere),
     scaled like the game scales a billboard at that depth, tinted like the local player's sprite (time-of-day
     lighting), with the same faint shadow under the feet.

Everything below was checked on the real ROM (dev/platinum/MEMORY_MAP.md "Friend renderer", dev/e2e/pt_friends.py):
  * 0x021C0770 = sActiveCamera (camera.c) -> Camera = FieldSystem.camera (+0x24) while the field map runs.
    Camera: +0x00 perspective {sinFovY, cosFovY, aspectRatio, nearClip, farClip} fx32, +0x14 lookAt {position,
    target, up} VecFx32, +0x38 distance fx32, +0x3C angle u16[4], +0x44 projection u8 (0 perspective,
    1 orthographic: interiors), +0x46 fovY u16. Outdoors lookAt.target == the player's MapObject pos.
  * Billboard anchor = MapObject.pos + spriteJumpOffset + spritePosOffset + spriteTerrainOffset + (0, 0, 6)
    (ov5_021EDEB4); the model (mmodel.narc generic_32x32) is a quad x -16..16, y 0..32 in camera space above it.
    Measured on screen: NPC sprite bounding boxes match the projected quad within ~1 px (perspective outdoors,
    exact indoors (orthographic)).
  * The top screen shows the 3D scene of 3 emulated frames ago (geometry/display pipeline): project with the
    camera read 3 frames earlier (DISPLAY_LAG) or friends shift by 2-4 px while the camera scrolls.
  * Player textures: mmodel.narc member 90 (BTX0 "pl_boy01c") / 91 ("pl_girl01c"): 32 4bpp 32x32 frames
    "<name>.1".."<name>.32": frames 0-15 walk (4 per direction: up, down, left, right = the game's DIR_* order;
    stand, step, stand, step), 16-31 run (same order). Palette colour 0 is transparent.
  * Outdoor maps (map header mapMatrixID == 0) share the global Sinnoh grid; other maps have local coordinates
    and several maps may share one matrix (e.g. all Pokémon Centers) -> friends only show on the same map id, or
    when both players are on matrix-0 maps (then a friend one town over is still placed correctly).

Pure maths/decoding has no pygame dependency (unit-tested in client/tests/test_sinnoh_view.py); draw() uses pygame.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

CAMERA_PTR = 0x021C0770           # sActiveCamera (ARM9 .bss)
FS_CAMERA = 0x24                  # FieldSystem.camera
CAMERA_SIZE = 0x48
MAP_HEADERS = 0x020E601C          # sMapHeaders (ARM9 .rodata), 24 bytes each, +2 mapMatrixID u16
MAP_HEADER_SIZE = 24
NUM_MAP_HEADERS = 593
MO_SPRITE_OFFSETS = (0x7C, 0x88, 0x94)   # MapObject spriteJumpOffset / spritePosOffset / spriteTerrainOffset
BILLBOARD_Z = 6                   # ov5_021EDEB4: billboard pos.z += 6
QUAD_W, QUAD_H = 32, 32           # generic_32x32 billboard: x -16..16, y 0..32
SCREEN_W, SCREEN_H = 256, 192
DISPLAY_LAG = 3                   # emulated frames between the camera in RAM and the picture on the top screen
MMODEL_NARC = "/data/mmodel/mmodel.narc"
PLAYER_TEXTURES = {0: (90, "pl_boy01c"), 1: (91, "pl_girl01c")}
SHADOW_W, SHADOW_H, SHADOW_ALPHA = 13, 6, 28    # the game's shadow: ellipse ending at the anchor, ~10 % darker

RAM_LO, RAM_HI = 0x02000000, 0x02400000


def _ptr(v):
    return RAM_LO <= v < RAM_HI


# ---------------------------------------------------------------------------------------------------------
# Camera + projection
# ---------------------------------------------------------------------------------------------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


@dataclass
class Camera:
    """The game's Camera struct (camera.h), in world units (fx32 / 4096)."""
    sin_fov: float
    cos_fov: float
    aspect: float
    near: float
    far: float
    position: tuple
    target: tuple
    up: tuple
    distance: float
    projection: int          # 0 perspective, 1 orthographic

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Camera | None":
        if len(raw) < 0x46:
            return None
        p = struct.unpack_from("<5i", raw, 0)
        la = struct.unpack_from("<9i", raw, 0x14)
        dist = struct.unpack_from("<i", raw, 0x38)[0]
        proj = raw[0x44]
        f = 1 / 4096
        cam = cls(p[0] * f, p[1] * f, p[2] * f, p[3] * f, p[4] * f,
                  (la[0] * f, la[1] * f, la[2] * f), (la[3] * f, la[4] * f, la[5] * f), (la[6] * f, la[7] * f, la[8] * f),
                  dist * f, proj)
        return cam if cam.valid() else None

    def valid(self) -> bool:
        if self.projection not in (0, 1) or not (0 < self.sin_fov < 1 and 0 < self.cos_fov <= 1.0001):
            return False
        if not (0.5 < self.aspect < 3 and 0 < self.near < self.far):
            return False
        d = _sub(self.position, self.target)
        return _dot(d, d) > 1 and _dot(self.up, self.up) > 0.01

    def __post_init__(self):
        z = _norm(_sub(self.position, self.target))          # MTX_LookAt: camera looks down -z
        x = _norm(_cross(self.up, z))
        y = _cross(z, x)
        self._axes = (x, y, z)
        self._cot = self.cos_fov / self.sin_fov if self.sin_fov else 1.0
        self._top = self.sin_fov / self.cos_fov * self.distance if self.cos_fov else 1.0   # orthographic half-height

    def to_view(self, w) -> tuple:
        """World point -> camera space (x right, y up, z towards the viewer; visible points have z < 0)."""
        d = _sub(w, self.position)
        x, y, z = self._axes
        return (_dot(d, x), _dot(d, y), _dot(d, z))

    def px_per_unit(self, vz: float) -> float:
        """Screen pixels per world unit for a camera-facing quad at view depth vz."""
        if self.projection == 1:
            return (SCREEN_H / 2) / self._top
        if vz >= -1e-6:
            return 0.0
        return self._cot * (SCREEN_H / 2) / -vz

    def view_to_screen(self, v) -> tuple:
        if self.projection == 1:
            nx = v[0] / (self._top * self.aspect)
            ny = v[1] / self._top
        else:
            if v[2] >= -1e-6:
                return (float("nan"), float("nan"))
            nx = v[0] * self._cot / self.aspect / -v[2]
            ny = v[1] * self._cot / -v[2]
        return ((nx + 1) * SCREEN_W / 2, (1 - ny) * SCREEN_H / 2)

    def project(self, w) -> tuple:
        """World point -> (screen x, screen y, view depth z) on the 256x192 top screen (pixel edges at integers)."""
        v = self.to_view(w)
        sx, sy = self.view_to_screen(v)
        return sx, sy, v[2]

    def billboard(self, anchor, w: float = QUAD_W, h: float = QUAD_H):
        """Screen rectangle (left, top, width, height) of a camera-facing w x h quad standing on `anchor`
        (billboard anchor = MapObject pos + (0, 0, 6)), plus its view depth; None if behind the camera."""
        v = self.to_view(anchor)
        if self.projection == 0 and (v[2] > -self.near * 0.5 or v[2] < -self.far * 2):
            return None
        sx, sy = self.view_to_screen(v)
        s = self.px_per_unit(v[2])
        if not s or math.isnan(sx):
            return None
        return (sx - w / 2 * s, sy - h * s, w * s, h * s, v[2])


def read_camera(bus, fs: int | None = None):
    """The active camera, or None. With `fs` (FieldSystem address) only the field's own camera is accepted
    (cutscenes / other apps may install their own camera)."""
    cam = bus.r32(CAMERA_PTR)
    if not _ptr(cam):
        return None
    if fs is not None and bus.r32(fs + FS_CAMERA) != cam:
        return None
    return Camera.from_bytes(bus.read(cam, CAMERA_SIZE))


def billboard_anchor(px: float, ph: float, py: float) -> tuple:
    """World anchor of a person standing at the published smooth position (platinum.position(): px/py = pos - 8
    in world units (= tile*16 when standing), ph = pos.y)."""
    return (px + 8, ph, py + 8 + BILLBOARD_Z)


def map_matrix(bus, map_id: int, cache: dict | None = None) -> int:
    """mapMatrixID of a map header (0 = the outdoor Sinnoh grid). -1 if unknown."""
    if cache is not None and map_id in cache:
        return cache[map_id]
    v = -1
    if 0 <= map_id < NUM_MAP_HEADERS:
        v = bus.r16(MAP_HEADERS + MAP_HEADER_SIZE * map_id + 2)
    if cache is not None:
        cache[map_id] = v
    return v


def same_space(my_map: int, their_map: int, matrix_of) -> bool:
    """Can a friend on `their_map` be drawn in my world? Same map, or both on the outdoor matrix 0."""
    if my_map == their_map:
        return True
    return matrix_of(my_map) == 0 and matrix_of(their_map) == 0


# ---------------------------------------------------------------------------------------------------------
# Sprite frames
# ---------------------------------------------------------------------------------------------------------
def frame_index(facing: int, step: int = 0, running: bool = False) -> int:
    """Texture frame: 4 per direction (up, down, left, right), stand/step/stand/step; +16 for running."""
    return (16 if running else 0) + (facing & 3) * 4 + (step & 3)


def _dict(d: bytes, off: int):
    """NNS 3D-info dictionary -> [(name, entry bytes)]."""
    count = d[off + 1]
    p = off + 4 + 8 + 4 * count              # header, patricia-tree block (8 + 4 per entry)
    unit = struct.unpack_from("<H", d, p)[0]
    out = []
    for i in range(count):
        ent = d[p + 4 + i * unit:p + 4 + (i + 1) * unit]
        name = d[p + 4 + count * unit + i * 16:p + 4 + count * unit + (i + 1) * 16].split(b"\0")[0]
        out.append((name.decode("ascii", "replace"), ent))
    return out


def decode_btx0(data: bytes) -> dict:
    """BTX0 texture set -> {"name": texture name prefix, "frames": {index: (w, h, rgba bytes)}}.
    Supports the formats overworld sprites use: 4-colour (2), 16-colour (3), 256-colour (4); colour 0 is
    transparent when the texture says so. Frame index = the number after the last '.' minus 1 (or the order)."""
    if data[:4] != b"BTX0":
        raise ValueError("not a BTX0 file")
    tex0 = struct.unpack_from("<I", data, 16)[0]
    d = data[tex0:]
    if d[:4] != b"TEX0":
        raise ValueError("no TEX0 block")
    tex_info = struct.unpack_from("<H", d, 0x0E)[0]
    tex_data = struct.unpack_from("<I", d, 0x14)[0]
    pal_info, pal_data = struct.unpack_from("<II", d, 0x34)
    pals = _dict(d, pal_info)
    if not pals:
        raise ValueError("no palette")
    pal_off = pal_data + (struct.unpack_from("<H", pals[0][1], 0)[0] << 3)
    frames = {}
    prefix = ""
    for i, (name, ent) in enumerate(_dict(d, tex_info)):
        p0 = struct.unpack_from("<I", ent, 0)[0]
        off = tex_data + ((p0 & 0xFFFF) << 3)
        w, h = 8 << ((p0 >> 20) & 7), 8 << ((p0 >> 23) & 7)
        fmt, c0 = (p0 >> 26) & 7, (p0 >> 29) & 1
        bpp = {2: 2, 3: 4, 4: 8}.get(fmt)
        if bpp is None:
            continue
        ncol = 1 << bpp
        cols = []
        for c in range(ncol):
            raw = struct.unpack_from("<H", d, pal_off + 2 * c)[0] if pal_off + 2 * c + 2 <= len(d) else 0
            r, g, b = raw & 31, (raw >> 5) & 31, (raw >> 10) & 31
            cols.append(bytes(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2),
                               0 if (c == 0 and c0) else 255)))
        px = d[off:off + w * h * bpp // 8]
        out = bytearray()
        mask = ncol - 1
        per = 8 // bpp
        for byte in px:
            for k in range(per):
                out += cols[(byte >> (k * bpp)) & mask]
        base, _, num = name.rpartition(".")
        if num.isdigit():
            idx = int(num) - 1
            prefix = base
        else:
            idx = i
            prefix = prefix or name
        frames[idx] = (w, h, bytes(out))
    return {"name": prefix, "frames": frames}


_SPRITE_CACHE: dict = {}


def player_sprites(rom_path: str) -> dict:
    """{gender: decode_btx0 result} for Lucas (0) and Dawn (1), read from the player's own Platinum ROM.
    Cached per ROM path. Raises (OSError/ValueError/KeyError) if the ROM doesn't have them."""
    if rom_path in _SPRITE_CACHE:
        return _SPRITE_CACHE[rom_path]
    from . import ndsrom
    rom = ndsrom.NDSRom(rom_path)
    try:
        narc = rom.narc(MMODEL_NARC)
        out = {}
        for gender, (member, name) in PLAYER_TEXTURES.items():
            tex = decode_btx0(narc.member(member))
            if tex["name"] != name or len(tex["frames"]) < 32:
                raise ValueError(f"mmodel member {member} is {tex['name']!r}, expected {name!r}")
            out[gender] = tex
    finally:
        rom.close()
    _SPRITE_CACHE[rom_path] = out
    return out


# ---------------------------------------------------------------------------------------------------------
# Time-of-day tint: compare the local player's sprite on screen with its texture
# ---------------------------------------------------------------------------------------------------------
def sample_quad(rect, w: int, h: int):
    """Screen pixel centres covered by a w x h texture drawn into rect (left, top, width, height):
    yields (screen x, screen y, texel u, texel v) for the visible top screen."""
    left, top, rw, rh = rect[:4]
    if rw <= 0 or rh <= 0:
        return
    for sy in range(max(0, int(math.floor(top))), min(SCREEN_H, int(math.ceil(top + rh)))):
        v = int((sy + 0.5 - top) / rh * h)
        if not 0 <= v < h:
            continue
        for sx in range(max(0, int(math.floor(left))), min(SCREEN_W, int(math.ceil(left + rw)))):
            u = int((sx + 0.5 - left) / rw * w)
            if 0 <= u < w:
                yield sx, sy, u, v


def estimate_tint(screen_rgb: bytes, rect, frame) -> tuple | None:
    """Per-channel gain (r, g, b) that turns the texture `frame` (w, h, rgba) into what the screen shows inside
    `rect` (the local player's own billboard): rendered = texture * gain (area lighting). screen_rgb = the top
    screen as 256*192*3 bytes. None if the sprite doesn't match (other sprite frame, bike, something in front)."""
    w, h, rgba = frame
    num = [0.0, 0.0, 0.0]
    den = [0.0, 0.0, 0.0]
    pts = []
    for sx, sy, u, v in sample_quad(rect, w, h):
        t = (v * w + u) * 4
        if rgba[t + 3] == 0:
            continue
        s = (sy * SCREEN_W + sx) * 3
        tc = rgba[t:t + 3]
        sc = screen_rgb[s:s + 3]
        pts.append((tc, sc))
        for c in range(3):
            num[c] += tc[c] * sc[c]
            den[c] += tc[c] * tc[c]
    if len(pts) < 80:
        return None
    gain = tuple(num[c] / den[c] if den[c] else 1.0 for c in range(3))
    err = sum(abs(sc[c] - tc[c] * gain[c]) for tc, sc in pts for c in range(3)) / (3 * len(pts))
    if err > 14 or not all(0.15 < g < 1.3 for g in gain):
        return None
    return gain


# ---------------------------------------------------------------------------------------------------------
# Renderer (pygame)
# ---------------------------------------------------------------------------------------------------------
@dataclass
class FriendSprite:
    """What draw() needs per friend."""
    name: str
    anchor: tuple            # world billboard anchor (billboard_anchor())
    gender: int
    frame: int               # frame_index()


class SinnohView:
    """Per-app helper: keeps the camera history, the decoded sprites (as pygame surfaces) and the tint."""

    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.sprites = None          # {gender: {"name", "frames"}}
        self.error = None
        self.cams: list = []         # [(emu frame, Camera)]
        self.tint = (1.0, 1.0, 1.0)
        self._surf_cache: dict = {}
        self._matrix_cache: dict = {}
        self._mask_cache: dict = {}
        try:
            self.sprites = player_sprites(rom_path)
        except Exception as exc:  # noqa: BLE001 - friends just aren't drawn
            self.error = f"{type(exc).__name__}: {exc}"

    # camera history ----------------------------------------------------------------------------------------
    def record_camera(self, bus, fs, frame: int):
        """Call once per app frame after the emulator ran (frame = emulated frame counter)."""
        cam = read_camera(bus, fs) if fs else None
        if self.cams and frame <= self.cams[-1][0]:
            self.cams.clear()                     # state loaded / reset
        self.cams.append((frame, cam))
        if len(self.cams) > 12:
            del self.cams[:-12]

    def display_camera(self, frame: int):
        """The camera the top screen is currently showing (DISPLAY_LAG frames ago); None during a hand-over."""
        best = None
        for f, cam in self.cams:
            if f <= frame - DISPLAY_LAG:
                best = cam
            elif best is None:
                best = cam                         # not enough history yet (just started): newest-but-one is fine
                break
        return best

    def matrix_of(self, bus):
        return lambda m: map_matrix(bus, m, self._matrix_cache)

    # tint -------------------------------------------------------------------------------------------------
    def update_tint(self, screen_rgb: bytes, cam: Camera, my_anchor, gender: int, facing: int):
        """Estimate the area light from the local player's standing sprite (call only while he stands still)."""
        if not self.sprites or cam is None:
            return
        tex = self.sprites.get(gender & 1)
        rect = cam.billboard(my_anchor)
        if not tex or not rect:
            return
        g = estimate_tint(screen_rgb, rect, tex["frames"][frame_index(facing)])
        if g:
            self.tint = tuple(round(0.5 * a + 0.5 * b, 3) for a, b in zip(self.tint, g))

    # drawing ------------------------------------------------------------------------------------------------
    def _surface(self, gender: int, frame: int, w: int, h: int):
        import pygame
        tint = tuple(min(255, int(t * 255)) // 8 * 8 for t in self.tint)   # quantised: the cache stays small
        key = (gender, frame, w, h, tint)
        s = self._surf_cache.get(key)
        if s is None:
            fw, fh, rgba = self.sprites[gender & 1]["frames"][frame]
            base = pygame.image.frombuffer(rgba, (fw, fh), "RGBA").copy()
            if min(tint) < 248:
                base.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
            s = pygame.transform.scale(base, (w, h)) if (w, h) != (fw, fh) else base
            if len(self._surf_cache) > 256:
                self._surf_cache.clear()
            self._surf_cache[key] = s
        return s

    def draw(self, surf, cam: Camera, friends: list, me_anchor=None, me_mask_frames=()):
        """Draw friends (FriendSprite) on the top screen part of `surf` (256x192 at (0, 0)), far ones first.
        me_anchor/me_mask_frames: the local player's anchor + texture frames: friends standing behind him are
        hidden where his sprite is (the game would draw him in front). Returns name tags [(name, centre x, top y)]."""
        import pygame
        if not self.sprites or cam is None:
            return []
        items = []
        for f in friends:
            r = cam.billboard(f.anchor)
            if not r:
                continue
            left, top, w, h, depth = r
            if left > SCREEN_W or left + w < 0 or top > SCREEN_H or top + h < 0:
                continue
            items.append((depth, left, top, w, h, f))
        items.sort(key=lambda it: it[0])            # most negative z = farthest first
        me_rect = cam.billboard(me_anchor) if me_anchor is not None else None
        tags = []
        clip = surf.get_clip()
        surf.set_clip(pygame.Rect(0, 0, SCREEN_W, SCREEN_H))
        try:
            for depth, left, top, w, h, f in items:
                iw, ih = max(1, int(round(w))), max(1, int(round(h)))
                x0, y0 = int(round(left)), int(round(top))
                sw, sh = max(2, int(round(SHADOW_W * w / QUAD_W))), max(1, int(round(SHADOW_H * h / QUAD_H)))
                shadow = pygame.Surface((sw, sh), pygame.SRCALPHA)
                pygame.draw.ellipse(shadow, (0, 0, 0, SHADOW_ALPHA), shadow.get_rect())
                surf.blit(shadow, (int(round(left + w / 2 - sw / 2)), int(round(top + h)) - sh))
                img = self._surface(f.gender, f.frame, iw, ih)
                if me_rect and depth < me_rect[4] and me_mask_frames:
                    img = self._behind_me(img, (x0, y0), me_rect, me_mask_frames)
                surf.blit(img, (x0, y0))
                tags.append((f.name, left + w / 2, top + h * 0.18))
        finally:
            surf.set_clip(clip)
        return tags

    def _behind_me(self, img, pos, me_rect, frames):
        """Copy of img with the pixels covered by the local player's sprite made transparent."""
        left, top, w, h = me_rect[:4]
        x0, y0 = pos
        iw, ih = img.get_size()
        if left > x0 + iw or left + w < x0 or top > y0 + ih or top + h < y0:
            return img
        img = img.copy()
        fw, fh = frames[0][0], frames[0][1]
        key = tuple(id(fr[2]) for fr in frames)
        opaque = self._mask_cache.get(key)
        if opaque is None:
            opaque = {(u, v) for fr in frames for v in range(fh) for u in range(fw) if fr[2][(v * fw + u) * 4 + 3]}
            self._mask_cache = {key: opaque}
        for sx, sy, u, v in sample_quad((left, top, w, h), fw, fh):
            if (u, v) in opaque:
                lx, ly = sx - x0, sy - y0
                if 0 <= lx < iw and 0 <= ly < ih:
                    img.set_at((lx, ly), (0, 0, 0, 0))
        return img
