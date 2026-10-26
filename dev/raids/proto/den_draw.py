"""Raid den drawn into the game picture (prototype of the overlay, our own art - nothing from the games).

  GB (Yellow): the den is a 16x16 sprite at a map square, placed like a friend (SPEC.md section 3:
               sx = 64 + (x*16 - my.px), sy = 60 + (y*16 - my.py)), coloured with 4 shades sampled from the
               screen so it matches the map palette.
  DS (Platinum): a 32x48 billboard standing on a tile, projected with sinnoh_view's camera model
               (Camera.billboard on the tile's anchor), like friends are drawn.
Writes /tmp/yl-raid-proto/den_yellow.png and den_platinum.png (screenshots: keep them out of git).
Run: python dev/raids/proto/den_draw.py
"""
import os
import sys

from common import STATES, OUT, check, done, info
import pygame

DEN_16 = [  # 0 transparent, 1 light, 2 mid, 3 dark : a small stone shrine with a glowing core
    "0000003333000000",
    "0000032222300000",
    "0000321111230000",
    "0003211331123000",
    "0003213333123000",
    "0003211331123000",
    "0032222222222300",
    "0032111111112300",
    "0321133333311230",
    "0321131111311230",
    "0321131331311230",
    "0321131111311230",
    "3222222222222223",
    "3211111111111123",
    "3333333333333333",
    "0333333333333330",
]


def den_surface_gb(shades):
    s = pygame.Surface((16, 16), pygame.SRCALPHA)
    for y, row in enumerate(DEN_16):
        for x, c in enumerate(row):
            if c != "0":
                s.set_at((x, y), shades[int(c)])
    return s


def den_surface_ds(w=32, h=48):
    """'Hallowed Tower'-like stack of stones with a purple glow, drawn in code."""
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.ellipse(s, (0, 0, 0, 70), (2, h - 8, w - 4, 8))
    stones = [(4, h - 14, w - 8, 9), (6, h - 23, w - 12, 9), (8, h - 32, w - 16, 9), (10, h - 40, w - 20, 8),
              (12, h - 47, w - 24, 7)]
    for i, (x, y, ww, hh) in enumerate(stones):
        base = (120 - i * 6, 112 - i * 6, 128 - i * 4)
        pygame.draw.rect(s, base, (x, y, ww, hh), border_radius=2)
        pygame.draw.rect(s, (60, 54, 70), (x, y, ww, hh), 1, border_radius=2)
        pygame.draw.line(s, (170, 165, 180), (x + 2, y + 1), (x + ww - 3, y + 1))
    pygame.draw.circle(s, (180, 80, 255, 150), (w // 2, h - 26), 6)
    pygame.draw.circle(s, (240, 200, 255), (w // 2, h - 26), 2)
    return s


def yellow():
    from pyboy import PyBoy
    from yellowlink import game
    rom = os.path.join(STATES, "yellow.gb")
    rd = game.RomData.from_file(rom)
    pb = PyBoy(rom, window="null", sound_emulated=False, log_level="ERROR")
    pb.load_state(open(os.path.join(STATES, "soullink_route1_grass.state"), "rb"))
    for _ in range(30):
        pb.tick()
    gm = game.GameMemory(pb.memory, rd)
    pos = gm.position()
    img = pb.screen.image.convert("RGB")
    raw = img.tobytes()
    screen = pygame.image.frombuffer(raw, img.size, "RGB").copy()
    cols = sorted({screen.get_at((x, y))[:3] for x in range(0, 160, 3) for y in range(0, 144, 3)}, key=sum)
    shades = {1: cols[-1], 2: cols[len(cols) // 2], 3: cols[0]}
    den = den_surface_gb(shades)
    tx, ty = pos["x"] + 2, pos["y"] - 2        # a square 2 right, 2 up of the player
    sx, sy = 64 + (tx * 16 - pos["px"]), 60 + (ty * 16 - pos["py"])
    screen.blit(den, (sx, sy))
    out = pygame.transform.scale(screen, (480, 432))
    pygame.image.save(out, os.path.join(OUT, "den_yellow.png"))
    check("GB: den drawn at map square (%d,%d) -> screen (%d,%d)" % (tx, ty, sx, sy), 0 <= sx < 160 and 0 <= sy < 144)
    pb.stop(save=False)


def platinum():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "platinum"))
    from ds import DS
    from yellowlink import sinnoh_view
    from yellowlink.platinum import PlatinumMemory
    d = DS(state=os.path.join(STATES, "pt_town.dsst"))
    d.run(10)
    pm = PlatinumMemory(d)
    fs = d.r32(0x021C07DC)
    pos = pm.position()
    # the top screen shows the camera of 3 frames earlier (MEMORY_MAP.md): read it, run 3 frames, draw
    cam = sinnoh_view.read_camera(d, fs)
    d.run(3)
    d.shot(os.path.join(OUT, "_pt_town_raw.png"))
    full = pygame.image.load(os.path.join(OUT, "_pt_town_raw.png"))
    tx, tz = pos["x"] + 2, pos["y"] - 3            # tile 2 east, 3 north of the player
    anchor = (tx * 16 + 8, pos["ph"], tz * 16 + 8 + sinnoh_view.BILLBOARD_Z)
    bb = cam.billboard(anchor, 32, 48) if cam else None
    ok = bb is not None
    if ok:
        x, y, w, h, _z = bb
        den = pygame.transform.smoothscale(den_surface_ds(), (max(1, int(w)), max(1, int(h))))
        full.blit(den, (int(x), int(y)))
        pygame.image.save(pygame.transform.scale(full, (512, 768)), os.path.join(OUT, "den_platinum.png"))
        info("billboard at %s (player tile %s,%s)" % ([round(v, 1) for v in bb[:4]], pos["x"], pos["y"]))
    check("DS: den billboard projected onto the top screen", ok and 0 <= bb[0] < 256 and 0 <= bb[1] < 192)


if __name__ == "__main__":
    pygame.init()
    yellow()
    platinum()
    raise SystemExit(done())
