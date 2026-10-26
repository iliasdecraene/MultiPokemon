"""Overlay UI drawing. Pure rendering — all state lives in app.App."""
from __future__ import annotations

import time

import pygame

BG = (22, 24, 33)
PANEL = (32, 35, 48)
PANEL_HI = (48, 53, 72)
TEXT = (232, 234, 240)
DIM = (140, 146, 166)
ACCENT = (250, 214, 65)    # Pikachu yellow
GOOD = (110, 214, 140)
BAD = (240, 110, 100)
CHAT_SELF = (160, 200, 255)

SIDEBAR_W = 300


class Fonts:
    def __init__(self):
        self.small = pygame.font.Font(None, 20)
        self.body = pygame.font.Font(None, 24)
        self.title = pygame.font.Font(None, 32)
        self.tag = pygame.font.Font(None, 18)


def text(surf, font, s, pos, color=TEXT, anchor="topleft"):
    img = font.render(s, True, color)
    r = img.get_rect(**{anchor: pos})
    surf.blit(img, r)
    return r


def wrap(font, s, width):
    words, lines, cur = s.split(" "), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.size(trial)[0] <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            while font.size(w)[0] > width and len(w) > 1:
                cut = len(w)
                while cut > 1 and font.size(w[:cut])[0] > width:
                    cut -= 1
                lines.append(w[:cut])
                w = w[cut:]
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def box(surf, rect, color=PANEL, border=PANEL_HI, radius=8):
    pygame.draw.rect(surf, color, rect, border_radius=radius)
    pygame.draw.rect(surf, border, rect, 2, border_radius=radius)


def name_tag(surf, font, name, center_x, bottom_y, color=TEXT):
    img = font.render(name, True, color)
    r = img.get_rect(midbottom=(center_x, bottom_y))
    bg = r.inflate(8, 4)
    s = pygame.Surface(bg.size, pygame.SRCALPHA)
    s.fill((0, 0, 0, 150))
    surf.blit(s, bg)
    surf.blit(img, r)


def draw_sidebar(surf, f: Fonts, app, x0, h):
    pygame.draw.rect(surf, BG, (x0, 0, SIDEBAR_W, h))
    x, y = x0 + 14, 12
    text(surf, f.title, "YellowLink", (x, y), ACCENT)
    st = app.net.status if app.net else "offline"
    col = {"online": GOOD, "connecting": ACCENT}.get(st, BAD)
    label = st
    name = getattr(app, "server_name", "")
    if st == "online" and name:
        label = name
    if st == "online" and app.net.rtt_ms is not None:
        label += f" · {app.net.rtt_ms} ms"
    if app.net and app.net.status_detail and st != "online":
        label += f" ({app.net.status_detail[:28]})"
    pygame.draw.circle(surf, col, (x + 5, y + 40), 5)
    text(surf, f.small, label, (x + 16, y + 33), DIM)
    y += 58

    if app.update_available:
        r = pygame.Rect(x - 4, y, SIDEBAR_W - 20, 40)
        box(surf, r, (60, 52, 20), ACCENT)
        text(surf, f.small, f"Update {app.update_available} available", (x + 4, y + 4), ACCENT)
        dl = app.update_dl
        if dl and dl.status == "downloading":
            label = f"downloading... {int(dl.fraction * 100)}%"
        elif dl and dl.status == "ready":
            label = "ready - press U to restart"
        else:
            label = "press U to update"
        text(surf, f.small, label, (x + 4, y + 21), TEXT)
        y += 48

    # volume slider (click/drag, or - / + keys)
    vol = 0 if app.audio.muted else int(round(app.audio.volume * 100))
    text(surf, f.small, "Volume" + (" (muted)" if app.audio.muted else ""), (x, y), DIM)
    bar = pygame.Rect(x + 70, y + 4, SIDEBAR_W - 120, 8)
    pygame.draw.rect(surf, PANEL_HI, bar, border_radius=4)
    fill = bar.copy()
    fill.w = int(bar.w * vol / 100)
    pygame.draw.rect(surf, ACCENT, fill, border_radius=4)
    pygame.draw.circle(surf, TEXT, (bar.x + fill.w, bar.centery), 7)
    text(surf, f.small, f"{vol}%", (x0 + SIDEBAR_W - 14, y), DIM, anchor="topright")
    app.volume_rect = bar.inflate(14, 16)
    app.volume_bar = bar
    y += 28

    # wide view toggle
    wr = pygame.Rect(x - 2, y - 2, SIDEBAR_W - 24, 26)
    available = app.wv is not None
    text(surf, f.small, "Wide view", (x, y + 3), TEXT if available else DIM)
    sw = pygame.Rect(x0 + SIDEBAR_W - 62, y, 46, 22)
    on = bool(app.wide)
    pygame.draw.rect(surf, ACCENT if on else PANEL_HI, sw, border_radius=11)
    pygame.draw.circle(surf, TEXT, (sw.right - 11 if on else sw.x + 11, sw.centery), 8)
    text(surf, f.small, "see more of the map" if available else "Yellow only", (x + 80, y + 3), DIM)
    if available:
        app.click_targets.append((wr, app.toggle_wide, "Wide view"))
    else:
        app.click_targets.append((wr, lambda: app.toast("Wide view works in Yellow for now.", 3, BAD), "Wide view"))
    y += 32

    text(surf, f.body, "Trainers", (x, y), TEXT)
    y += 24
    roster = app.sorted_roster()
    if not roster:
        text(surf, f.small, "nobody here yet", (x, y), DIM)
        y += 20
    for e in roster[:6]:
        online = e.get("online")
        dot = GOOD if online else (90, 94, 110)
        pygame.draw.circle(surf, dot, (x + 5, y + 8), 4)
        nm = e.get("name", "?") + (" (you)" if e.get("uid") == app.cfg["uid"] else "")
        nr = text(surf, f.small, nm, (x + 16, y), TEXT if online else DIM)
        region = app.player_region(e)
        if region:
            text(surf, f.small, " · " + region, (nr.right, y), DIM)
        lv = e.get("levels") or []
        info = f"{len(lv)} mon" + (f" · Lv{max(lv)}" if lv else "")
        if app.is_nearby(e.get("uid")):
            info = "nearby · " + info
        text(surf, f.small, info, (x0 + SIDEBAR_W - 14, y), DIM, anchor="topright")
        y += 20
    y += 8

    hint_h = 92
    btn_h = 52   # Multiplayer / Regions buttons above the key hints
    draw_chat(surf, f, app, pygame.Rect(x0 + 10, y, SIDEBAR_W - 20, h - hint_h - btn_h - y - 4))

    # big clickable buttons
    bw = (SIDEBAR_W - 34) // 2
    second = ("Soul Link", "soullink") if app.chal else ("Regions", "regions")
    for i, (label, panel) in enumerate((("Multiplayer", "multiplayer"), second)):
        br = pygame.Rect(x0 + 12 + i * (bw + 10), h - hint_h - btn_h + 4, bw, 40)
        active = app.panel == panel
        hover = br.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, ACCENT if active else ((78, 86, 114) if hover else (58, 64, 86)), br,
                         border_radius=8)
        text(surf, f.body, label, br.center, (30, 28, 20) if active else TEXT, anchor="center")
        app.click_targets.append((br, lambda p=panel: app.toggle_panel(p), label))

    hy = h - hint_h + 6
    pygame.draw.line(surf, PANEL_HI, (x0 + 10, hy - 6), (x0 + SIDEBAR_W - 10, hy - 6))
    if getattr(app, "ds", False):
        hints = ["A: X or A · B: Z or S · X: D · Y: C · L/R: Q/W",
                 "Start: Enter · Select: Bksp · Space: fast",
                 "Mouse on the lower screen: touch",
                 "Tab: multiplayer · T: chat · F1: help"]
    else:
        hints = ["A button: X or A  ·  B button: Z or S",
                 "Start: Enter · Select: Bksp · Space: fast",
                 "Tab: multiplayer · T: chat · F4: leave battle",
                 "F1: help · F3: report · V: wide view"]
    for hline in hints:
        text(surf, f.small, hline, (x, hy), DIM)
        hy += 19


NAME_COLORS = [(255, 179, 102), (130, 200, 255), (180, 160, 255), (120, 225, 170), (255, 140, 170), (240, 220, 110)]


def name_color(name):
    return NAME_COLORS[sum(map(ord, name)) % len(NAME_COLORS)]


def draw_chat(surf, f: Fonts, app, r):
    """Chat room box: scrollable history + an input field ('Click here to chat')."""
    if r.h < 90:
        return
    focused = app.mode == "chat"
    box(surf, r, PANEL, ACCENT if focused else PANEL_HI, 8)
    text(surf, f.body, "Chat", (r.x + 10, r.y + 6), TEXT)
    online = sum(1 for e in app.roster if e.get("online"))
    text(surf, f.small, f"{online} online", (r.right - 10, r.y + 9), DIM, anchor="topright")
    app.chat_rect = r

    inp = pygame.Rect(r.x + 8, r.bottom - 36, r.w - 16, 28)
    area = pygame.Rect(r.x + 10, r.y + 32, r.w - 20, inp.y - r.y - 38)
    width = area.w - 4

    # build wrapped lines: (segments, ) where the first line of a message starts with a coloured name
    lines = []
    for who, msg, color in app.chat_log[-200:]:
        if who:
            prefix = who + ": "
            wrapped = wrap(f.small, prefix + msg, width)
            first = wrapped[0]
            name, _, region = who.partition(" · ")      # "Ash · Kanto": name in its colour, region dimmed
            head = [(name, name_color(name))] + ([(" · " + region, DIM)] if region else []) + [(": ", name_color(name))]
            lines.append(head + [(first[len(prefix):], color)] if first.startswith(prefix)
                         else [(first, color)])
            lines.extend([[(ln, color)] for ln in wrapped[1:]])
        else:
            lines.extend([[(ln, color)] for ln in wrap(f.small, msg, width)])
    per = max(1, area.h // 18)
    max_scroll = max(0, len(lines) - per)
    app.chat_scroll = max(0, min(app.chat_scroll, max_scroll))
    start = max(0, len(lines) - per - app.chat_scroll)
    visible = lines[start:start + per]
    clip = surf.get_clip()
    surf.set_clip(area)
    yy = area.bottom - len(visible) * 18
    for segs in visible:
        xx = area.x
        for seg, color in segs:
            rr = text(surf, f.small, seg, (xx, yy), color)
            xx = rr.right
        yy += 18
    surf.set_clip(clip)
    if not lines:
        text(surf, f.small, "No messages yet. Say hi!", area.center, DIM, anchor="center")
    if app.chat_scroll > 0:
        tag = pygame.Rect(0, 0, 150, 20)
        tag.midbottom = (area.centerx, area.bottom)
        pygame.draw.rect(surf, PANEL_HI, tag, border_radius=10)
        text(surf, f.small, "v newer messages", tag.center, ACCENT, anchor="center")
        app.click_targets.append((tag, app.chat_to_bottom, "newer"))

    # input field
    pygame.draw.rect(surf, (24, 26, 36), inp, border_radius=6)
    pygame.draw.rect(surf, ACCENT if focused else PANEL_HI, inp, 2, border_radius=6)
    if focused:
        cursor = "|" if int(time.time() * 2) % 2 else " "
        shown = app.chat_input
        while f.small.size(shown + cursor)[0] > inp.w - 60 and shown:
            shown = shown[1:]
        text(surf, f.small, shown + cursor, (inp.x + 8, inp.y + 7), TEXT)
        send = pygame.Rect(inp.right - 50, inp.y + 3, 46, inp.h - 6)
        pygame.draw.rect(surf, ACCENT, send, border_radius=5)
        text(surf, f.small, "Send", send.center, (30, 28, 20), anchor="center")
        app.click_targets.append((send, app.send_chat, "Send"))
    else:
        text(surf, f.small, "Click here to chat...", (inp.x + 8, inp.y + 7), DIM)
        app.click_targets.append((inp, app.focus_chat, "chat input"))


def draw_toasts(surf, f: Fonts, toasts, game_w, bottom=False):
    """Short messages at the top of the game area (at the bottom while a panel/dialog covers the top)."""
    now = time.time()
    live = [t for t in toasts if t[1] >= now][-3:]
    step = f.body.get_height() + 16
    y = surf.get_height() - 12 - step * len(live) if bottom else 10
    for msg, until, color in live:
        img = f.body.render(msg, True, color)
        r = img.get_rect(midtop=(game_w // 2, y + 5))
        bg = r.inflate(20, 10)
        s = pygame.Surface(bg.size, pygame.SRCALPHA)
        s.fill((10, 12, 20, 230))
        surf.blit(s, bg)
        surf.blit(img, r)
        y += step


def modal(surf, f: Fonts, game_w, game_h, title, lines, footer, w=None, h=None):
    w = w or min(game_w - 40, 520)
    line_h = 24
    h = h or (70 + len(lines) * line_h + 40)
    r = pygame.Rect(0, 0, w, h)
    r.center = (game_w // 2, game_h // 2)
    shade = pygame.Surface((game_w, game_h), pygame.SRCALPHA)
    shade.fill((0, 0, 0, 120))
    surf.blit(shade, (0, 0))
    box(surf, r, PANEL, ACCENT, 10)
    text(surf, f.title, title, (r.x + 18, r.y + 14), ACCENT)
    y = r.y + 56
    for item in lines:
        if isinstance(item, tuple):
            s, color = item
        else:
            s, color = item, TEXT
        text(surf, f.body, s, (r.x + 18, y), color)
        y += line_h
    text(surf, f.small, footer, (r.x + 18, r.bottom - 28), DIM)
    return r


def draw_link_menu(surf, f, app, game_w, game_h):
    roster = [e for e in app.sorted_roster() if e.get("uid") != app.cfg["uid"]]
    lines = []
    if not roster:
        lines.append(("No other trainers yet. Invite your friends!", DIM))
    for i, e in enumerate(roster):
        sel = i == app.menu_index
        online = "online" if e.get("online") else "offline"
        lv = e.get("levels") or []
        s = f"{'>' if sel else ' '} {e.get('name')}  ({online}, {len(lv)} Pokémon{', Lv ' + str(max(lv)) if lv else ''})"
        lines.append((s, ACCENT if sel else (TEXT if e.get("online") else DIM)))
    footer = "[Up/Down] choose  [B] battle  [R] trade  [P] practice vs their team  [Esc] close"
    modal(surf, f, game_w, game_h, "Link menu", lines, footer)


def draw_help(surf, f, game_w, game_h, ds=False):
    if ds:
        lines = [
            ("Nintendo DS controls", ACCENT),
            "  A: X or A     B: Z or S",
            "  X: D     Y: C     L: Q     R: W",
            "  Start: Enter     Select: Backspace",
            "  D-pad: arrows     Touch: mouse",
            ("  (click / drag on the lower screen)", DIM),
            ("YellowLink", ACCENT),
            "  Tab: multiplayer     T: chat",
            "  Space: fast-forward     M: mute",
            "  -/+: volume     F5/F6: window size",
            "  F3: report a problem",
            ("Save in-game as usual (X > Save).", DIM),
        ]
        modal(surf, f, game_w, game_h, "How to play", lines, "[Esc / Enter / F1] close")
        return
    lines = [
        ("Game Boy controls", ACCENT),
        "  A button (talk, confirm, next text):  X  or  A",
        "  B button (back, cancel):  Z  or  S",
        "  Start: Enter      Select: Backspace      D-pad: arrow keys",
        "",
        ("YellowLink", ACCENT),
        "  Tab: link menu - battle (B), trade (R), practice (P)",
        "  T: chat     Space: fast-forward on/off     M: mute",
        "  V: wide view     F5/F6: window size     -/+: volume",
        "  F3: report a problem     F2: settings",
        "",
        ("Battles are exhibition matches - nothing is won or lost.", DIM),
        ("F4 leaves a live battle. Practice = their team played by the game AI.", DIM),
        ("After a trade, SAVE in-game (Start > Save).", DIM),
    ]
    modal(surf, f, game_w, game_h, "How to play", lines, "[Esc / Enter / F1] close")
