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
