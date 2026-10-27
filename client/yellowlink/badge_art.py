"""Badge art, all drawn by our own code (no Nintendo assets): sockets per region, hover tips, the shiny sparkle.

A socket is a round medal: empty (dark, with a faint outline of its emblem) until earned. Emblems: Champion = a
crown, Pokédex = a little book, Shiny hunter = a four-point sparkle (+ the tier reached), Soul Link = two linked
rings on gold or silver.
"""
from __future__ import annotations

import math
import time

import pygame

from .badges import REGION_NAMES, next_shiny_tier as next_tier, shiny_tier
from .badgesync import NAMES, SOCKETS, how_to
from .ui import ACCENT, DIM, PANEL, TEXT, text, wrap

EMPTY_FILL = (26, 28, 38)
EMPTY_RIM = (78, 82, 100)
EMPTY_GLYPH = (58, 62, 80)
COLORS = {                                  # (rim, fill, emblem, emblem detail)
    "champion": ((34, 52, 120), (72, 112, 214), (255, 212, 84), (190, 140, 30)),
    "dex": ((128, 30, 34), (218, 64, 58), (245, 246, 250), (70, 150, 235)),
    "shiny": ((80, 52, 140), (146, 108, 226), (255, 246, 170), (255, 255, 255)),
    "gold": ((150, 108, 24), (238, 194, 70), (120, 76, 8), (255, 236, 150)),
    "silver": ((104, 110, 126), (204, 210, 222), (72, 78, 96), (250, 252, 255)),
}
SS = 4                                      # supersampling: medals are drawn 4x and smooth-scaled down
_cache: dict = {}


def earned(kind: str, s: dict) -> bool:
    if kind == "shiny":
        return int(s.get("shiny") or 0) > 0
    if kind == "soullink":
        return s.get("soullink") in ("gold", "silver")
    return bool(s.get(kind))


def _star(cx, cy, r_out, r_in, points=4, rot=-math.pi / 2):
    pts = []
    for i in range(points * 2):
        a = rot + i * math.pi / points
        rr = r_out if i % 2 == 0 else r_in
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _glyph(surf, kind, cx, cy, r, color, detail):
    """The emblem inside a medal of radius r (drawn at the supersampled size)."""
    if kind == "champion":                  # a crown with three jewels
        w, h = r * 1.15, r * 0.72
        x0, y0 = cx - w / 2, cy - h / 2 + r * 0.1
        pts = [(x0, y0 + h), (x0, y0), (x0 + w * 0.27, y0 + h * 0.5), (cx, y0 - h * 0.22),
               (x0 + w * 0.73, y0 + h * 0.5), (x0 + w, y0), (x0 + w, y0 + h)]
        pygame.draw.polygon(surf, color, pts)
        if detail:
            band = pygame.Rect(0, 0, int(w), max(1, int(h * 0.22)))
            band.midbottom = (cx, int(y0 + h))
            pygame.draw.rect(surf, detail, band)
            for px in (x0, cx, x0 + w):
                pygame.draw.circle(surf, detail, (int(px), int(y0 - (h * 0.22 if px == cx else 0))), max(1, int(r * 0.1)))
    elif kind == "dex":                     # a Pokédex: white screen + blue lens
        body = pygame.Rect(0, 0, int(r * 1.1), int(r * 0.95))
        body.center = (cx, int(cy + r * 0.05))
        pygame.draw.rect(surf, color, body, border_radius=int(r * 0.14))
        if detail:
            screen = body.inflate(-int(r * 0.34), -int(r * 0.5))
            screen.y = body.y + int(r * 0.38)
            pygame.draw.rect(surf, (60, 64, 80), screen, border_radius=int(r * 0.06))
            pygame.draw.circle(surf, detail, (body.x + int(r * 0.24), body.y + int(r * 0.2)), int(r * 0.13))
    elif kind == "shiny":                   # a four-point sparkle + a little one
        pygame.draw.polygon(surf, color, _star(cx - r * 0.08, cy + r * 0.06, r * 0.74, r * 0.17))
        pygame.draw.polygon(surf, detail or color, _star(cx + r * 0.46, cy - r * 0.44, r * 0.26, r * 0.07))
    else:                                   # two linked rings
        rr, wdt = int(r * 0.36), max(2, int(r * 0.13))
        pygame.draw.circle(surf, color, (int(cx - r * 0.24), cy), rr, wdt)
        pygame.draw.circle(surf, color, (int(cx + r * 0.24), cy), rr, wdt)
        if detail:                          # the left ring passes over the right one at the top
            pygame.draw.arc(surf, color, pygame.Rect(int(cx - r * 0.24) - rr, cy - rr, 2 * rr, 2 * rr),
                            -0.6, 0.6, wdt)


def _medal(kind, state, r):
    """A medal image (2r x 2r, transparent corners): state None = empty socket, else a COLORS key."""
    key = (kind, state, r)
    img = _cache.get(key)
    if img is not None:
        return img
    R = r * SS
    big = pygame.Surface((2 * R, 2 * R), pygame.SRCALPHA)
    c = (R, R)
    if state is None:
        pygame.draw.circle(big, EMPTY_FILL, c, R)
        pygame.draw.circle(big, EMPTY_RIM, c, R, SS)
        _glyph(big, kind, R, R, R, EMPTY_GLYPH, None)
    else:
        rim, fill, emblem, detail = COLORS[state]
        pygame.draw.circle(big, rim, c, R)
        pygame.draw.circle(big, fill, c, R - max(2 * SS, R // 6))
        hi = tuple(min(255, v + 34) for v in fill)            # a soft shine on the upper half
        pygame.draw.arc(big, hi, pygame.Rect(R // 3, R // 3, int(R * 1.34), int(R * 1.34)), 1.9, 2.9, max(SS, R // 10))
        _glyph(big, kind, R, R, R, emblem, detail)
    img = pygame.transform.smoothscale(big, (2 * r, 2 * r))
    if len(_cache) > 200:
        _cache.clear()
    _cache[key] = img
    return img


def _pill_font(r):
    key = ("font", max(12, int(r * 1.35)))
    if key not in _cache:
        _cache[key] = pygame.font.Font(None, key[1])
    return _cache[key]


def draw_socket(surf, center, kind, s: dict, r=11, font=None):
    """One badge socket (kind in SOCKETS) for a region summary s. Returns its rect."""
    cx, cy = int(center[0]), int(center[1])
    rect = pygame.Rect(cx - r, cy - r, 2 * r, 2 * r)
    state = (s["soullink"] if kind == "soullink" else kind) if earned(kind, s) else None
    surf.blit(_medal(kind, state, r), rect)
    if kind == "shiny" and state and font is not None:
        tier = shiny_tier(int(s.get("shiny") or 0))
        if tier > 1:                        # the tier on a pill over the medal's bottom edge
            img = _pill_font(r).render(str(tier), True, (30, 28, 20))
            pill = img.get_rect()
            pill.inflate_ip(4, -2)
            pill.midbottom = (cx, cy + r + 2)
            pygame.draw.rect(surf, ACCENT, pill, border_radius=pill.h // 2)
            surf.blit(img, img.get_rect(center=pill.center))
    return rect


def tip_lines(kind, region, s: dict, when=None) -> list:
    """Hover text: [(text, color)] - name, state, how to earn it."""
    rn = REGION_NAMES.get(region, region)
    title = f"{rn} {NAMES[kind]}"
    lines = []
    n = int(s.get("shiny") or 0)
    if kind == "shiny":
        tier = shiny_tier(n)
        nxt = next_tier(n)
        if n:
            title += f" {tier}" if tier > 1 else ""
            state = f"{n} shiny catch{'es' if n != 1 else ''}" + (f" - next tier at {nxt}" if nxt else " - top tier!")
        else:
            state = "Not earned yet"
    elif kind == "soullink":
        state = f"{s['soullink'].title()} - run finished" if earned(kind, s) else "Not earned yet"
        if earned(kind, s):
            title += f" ({s['soullink']})"
    else:
        state = "Earned" if earned(kind, s) else "Not earned yet"
    if when and earned(kind, s):
        state += time.strftime(" on %d %b %Y", time.localtime(when))
    lines.append((title, ACCENT if earned(kind, s) else TEXT))
    lines.append((state, TEXT if earned(kind, s) else DIM))
    lines.append((how_to(kind, region), DIM))
    return lines


def draw_row(surf, app, f, x, y, region, s: dict, offset=(0, 0), r=11, gap=5, uid=None):
    """The four sockets of a region from (x, y) (top-left). Registers click targets (click = the tip as a toast) and
    remembers the hovered socket's tip in app.badge_tip. Returns the row's width."""
    mouse = pygame.mouse.get_pos()
    cx = x + r
    for kind in SOCKETS:
        rect = draw_socket(surf, (cx, y + r), kind, s, r, f.tag)
        screen = rect.move(offset)
        when = app.badge_time(uid, region, kind) if uid and hasattr(app, "badge_time") else None
        lines = tip_lines(kind, region, s, when)
        if screen.collidepoint(mouse):
            app.badge_tip = (lines, rect)
            pygame.draw.circle(surf, ACCENT, rect.center, r + 1, 1)
        app.click_targets.append((screen, lambda ls=lines: app.toast(" - ".join(t for t, _ in ls[:2]), 4),
                                  f"badge:{region}:{kind}"))
        cx += 2 * r + gap
    return len(SOCKETS) * (2 * r + gap) - gap


def row_width(r=11, gap=5) -> int:
    return len(SOCKETS) * (2 * r + gap) - gap


def draw_tip(surf, f, tip, bounds: pygame.Rect):
    """The hover box for app.badge_tip, kept inside bounds (below the socket, or above if there's no room)."""
    if not tip:
        return
    lines, anchor = tip
    width = min(300, bounds.w - 16)
    rows = []
    for s, color in lines:
        rows += [(ln, color) for ln in wrap(f.small, s, width - 16)]
    h = 10 + 18 * len(rows)
    w = min(width, max(f.small.size(ln)[0] for ln, _ in rows) + 18)
    box = pygame.Rect(0, 0, w, h)
    box.midtop = (anchor.centerx, anchor.bottom + 6)
    if box.bottom > bounds.bottom - 4:
        box.midbottom = (anchor.centerx, anchor.y - 6)
    box.clamp_ip(bounds.inflate(-8, -8))
    pygame.draw.rect(surf, PANEL, box, border_radius=6)
    pygame.draw.rect(surf, ACCENT, box, 1, border_radius=6)
    yy = box.y + 5
    for ln, color in rows:
        text(surf, f.small, ln, (box.x + 9, yy), color)
        yy += 18


SHINY_TAG_H = 8.5                           # tag height in Game Boy pixels (fits between the two battle HUDs)


def draw_shiny_indicator(surf, f, x, y, scale, t=None):
    """'Shiny!' with a twinkling sparkle, drawn near the wild enemy in a Yellow battle: (x, y) = the middle of the
    tag's top edge, in screen pixels."""
    t = time.time() if t is None else t
    tw = 0.75 + 0.25 * math.sin(t * 6)
    h = max(16, int(SHINY_TAG_H * scale))          # at most ~9 Game Boy pixels tall at any window size
    font = f.body if h >= 26 else f.small
    img = font.render("Shiny!", True, (255, 236, 120))
    pad = max(2, int(scale))
    r = max(5, (h - 2 * pad) // 2)
    s = r / 9
    box = pygame.Rect(0, 0, img.get_width() + 2 * r + 3 * pad, h)
    box.midtop = (x, y)
    box.clamp_ip(surf.get_rect())
    shade = pygame.Surface(box.size, pygame.SRCALPHA)
    shade.fill((20, 18, 40, 190))
    surf.blit(shade, box.topleft)
    pygame.draw.rect(surf, (255, 236, 120), box, 1, border_radius=int(5 * s))
    cx, cy = box.x + pad + r, box.centery
    pygame.draw.polygon(surf, (255, 250, 200), _star(cx, cy, r * tw, r * 0.25))
    pygame.draw.polygon(surf, (255, 220, 90), _star(cx + r * 0.7, cy - r * 0.6, r * 0.4 * (1.6 - tw), r * 0.1))
    surf.blit(img, (cx + r + pad, box.centery - img.get_height() // 2))
    return box


def mini_sparkle(surf, center, r=5, color=(255, 220, 90)):
    """The small shiny mark next to a Pokémon's name (PC / trade views)."""
    cx, cy = center
    pygame.draw.polygon(surf, color, _star(cx, cy, r, r * 0.3))


# --- shiny alerts (SPEC.md section 10) --------------------------------------------------------------------------
BALL_RED, BALL_WHITE, BALL_INK = (226, 56, 48), (250, 250, 250), (28, 28, 34)


def draw_owned_ball(surf, x, y, scale):
    """Yellow: the 'already caught' Poké Ball in the enemy HUD, drawn into the Game Boy tile whose top-left corner is
    (x, y) in screen pixels (8x8 Game Boy pixels at `scale`): a 7-pixel ball, red top, white bottom, dark band and
    button. Returns its rect."""
    size = max(6, int(round(7 * scale)))
    key = ("ball", size)
    img = _cache.get(key)
    if img is None:
        big = size * SS
        s = pygame.Surface((big, big), pygame.SRCALPHA)
        c, r = big / 2, big / 2 - SS * 0.6
        ink = max(SS, int(big * 0.07))
        pygame.draw.circle(s, BALL_INK, (c, c), r)
        pygame.draw.circle(s, BALL_WHITE, (c, c), r - ink)
        top = pygame.Surface((big, big), pygame.SRCALPHA)
        pygame.draw.circle(top, BALL_RED, (c, c), r - ink)
        s.blit(top, (0, 0), pygame.Rect(0, 0, big, int(c)))
        pygame.draw.rect(s, BALL_INK, (c - r, c - ink * 0.75, 2 * r, ink * 1.5))
        pygame.draw.circle(s, BALL_INK, (c, c), r * 0.36)
        pygame.draw.circle(s, BALL_WHITE, (c, c), r * 0.36 - ink)
        pygame.draw.circle(s, (255, 190, 180), (c - r * 0.38, c - r * 0.45), max(1, r * 0.13))   # shine
        img = _cache[key] = pygame.transform.smoothscale(s, (size, size))
    tile = int(round(8 * scale))
    r = img.get_rect(center=(int(x + tile / 2), int(y + tile / 2)))
    surf.blit(img, r)
    return r


def _ease_out(v):
    v = max(0.0, min(1.0, v))
    return 1 - (1 - v) ** 3


def draw_shiny_banner(surf, f, banner, now=None):
    """The shiny alert across the top of the game area: slides in, glitters, fades out (shiny.Banner). Purely drawn
    over the game - it never takes input. Returns the banner's rect."""
    now = time.time() if now is None else now
    age = banner.age(now)
    alpha = banner.alpha(now)
    w = min(surf.get_width() - 16, 560)
    gold, ink = (255, 226, 110), (255, 250, 228)
    title_font = f.title
    if title_font.size(banner.title)[0] > w - 70:
        title_font = f.body
    title = title_font.render(banner.title, True, ink)
    if title.get_width() > w - 50:                  # very long name on a narrow (DS) window: squeeze a little
        title = pygame.transform.smoothscale(title, (w - 50, title.get_height()))
    sub = f.small.render(banner.sub, True, gold) if banner.sub else None
    h = 18 + title.get_height() + (sub.get_height() + 2 if sub else 0) + 12
    y = 8 - int((1 - _ease_out(age / 0.35)) * (h + 12))
    box = pygame.Rect(0, 0, w, h)
    box.midtop = (surf.get_width() // 2, y)
    layer = pygame.Surface(box.size, pygame.SRCALPHA)
    # deep violet card with a soft vertical gradient, gold rim, a highlight sweeping across once
    for i in range(h):
        k = i / max(1, h - 1)
        pygame.draw.line(layer, (int(46 - 18 * k), int(30 - 12 * k), int(92 - 30 * k), 236), (0, i), (w, i))
    sweep = ((age - 0.3) / 1.4) * (w + 120) - 60
    if -60 < sweep < w + 60:
        shine = pygame.Surface(box.size, pygame.SRCALPHA)
        for dx in range(-28, 29):
            a = int(46 * (1 - abs(dx) / 28) ** 1.5)
            pygame.draw.line(shine, (255, 255, 255, a), (sweep + dx, 0), (sweep + dx - h * 0.6, h), 2)
        layer.blit(shine, (0, 0))
    mask = pygame.Surface(box.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=12)
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    pygame.draw.rect(layer, gold, layer.get_rect(), 2, border_radius=12)
    trect = title.get_rect(midtop=(w // 2, 10))
    srect = sub.get_rect(midtop=(w // 2, 12 + title.get_height())) if sub else pygame.Rect(0, 0, 0, 0)
    # twinkling four-point sparkles around the text (fixed spots, each with its own phase; never on the words)
    for i in range(16):
        px = (0.08 + ((i * 0.618) % 1) * 0.84) * w
        py = (0.2 if i % 2 else 0.78) * h + math.sin(i * 1.7) * h * 0.08
        if trect.inflate(10, 6).collidepoint(px, py) or (sub and srect.inflate(12, 6).collidepoint(px, py)):
            continue
        tw = 0.5 + 0.5 * math.sin(age * 5.5 + i * 2.1)
        r = (3 + (i % 3) * 2) * tw
        if r > 1.2:
            pygame.draw.polygon(layer, (255, 248, 200, int(120 + 120 * tw)), _star(px, py, r, r * 0.28))
    # two big sparkles flanking the title
    tw = 0.8 + 0.2 * math.sin(age * 7)
    for sx in (22, w - 22):
        pygame.draw.polygon(layer, gold, _star(sx, h / 2, 11 * tw, 3))
        pygame.draw.polygon(layer, ink, _star(sx, h / 2, 5 * tw, 1.4, rot=-math.pi / 4))
    layer.blit(title, trect)
    if sub:
        layer.blit(sub, srect)
    layer.set_alpha(int(255 * alpha))
    surf.blit(layer, box.topleft)
    return box
