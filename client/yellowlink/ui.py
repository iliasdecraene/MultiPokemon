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

    text(surf, f.body, "Trainers", (x, y), TEXT)
    y += 24
    roster = app.sorted_roster()
    if not roster:
        text(surf, f.small, "nobody here yet", (x, y), DIM)
        y += 20
    for e in roster[:8]:
        online = e.get("online")
        dot = GOOD if online else (90, 94, 110)
        pygame.draw.circle(surf, dot, (x + 5, y + 8), 4)
        nm = e.get("name", "?") + (" (you)" if e.get("uid") == app.cfg["uid"] else "")
        text(surf, f.small, nm, (x + 16, y), TEXT if online else DIM)
        lv = e.get("levels") or []
        info = f"{len(lv)} mon" + (f" · Lv{max(lv)}" if lv else "")
        if app.is_nearby(e.get("uid")):
            info = "nearby · " + info
        text(surf, f.small, info, (x0 + SIDEBAR_W - 14, y), DIM, anchor="topright")
        y += 20
    y += 10

    text(surf, f.body, "Chat", (x, y), TEXT)
    y += 24
    hint_h = 92
    btn_h = 52   # Multiplayer / Regions buttons above the key hints
    chat_bottom = h - hint_h - btn_h - (34 if app.mode == "chat" else 4)
    lines = []
    for who, msg, color in app.chat_log[-40:]:
        prefix = f"{who}: " if who else ""
        for i, ln in enumerate(wrap(f.small, prefix + msg, SIDEBAR_W - 30)):
            lines.append((ln, color))
    max_lines = max(0, (chat_bottom - y) // 18)
    for ln, color in lines[-max_lines:] if max_lines else []:
        text(surf, f.small, ln, (x, y), color)
        y += 18

    if app.mode == "chat":
        r = pygame.Rect(x - 4, h - hint_h - btn_h - 30, SIDEBAR_W - 20, 26)
        box(surf, r, PANEL, ACCENT, 6)
        cursor = "_" if int(time.time() * 2) % 2 else " "
        shown = app.chat_input
        while f.small.size(shown + cursor)[0] > r.w - 12 and shown:
            shown = shown[1:]
        text(surf, f.small, shown + cursor, (r.x + 6, r.y + 6), TEXT)

    # big clickable buttons
    bw = (SIDEBAR_W - 34) // 2
    for i, (label, panel) in enumerate((("Multiplayer", "multiplayer"), ("Regions", "regions"))):
        br = pygame.Rect(x0 + 12 + i * (bw + 10), h - hint_h - btn_h + 4, bw, 40)
        active = app.panel == panel
        hover = br.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, ACCENT if active else ((78, 86, 114) if hover else (58, 64, 86)), br,
                         border_radius=8)
        text(surf, f.body, label, br.center, (30, 28, 20) if active else TEXT, anchor="center")
        app.click_targets.append((br, lambda p=panel: app.toggle_panel(p), label))

    hy = h - hint_h + 6
    pygame.draw.line(surf, PANEL_HI, (x0 + 10, hy - 6), (x0 + SIDEBAR_W - 10, hy - 6))
    hints = ["A button: X or A  ·  B button: Z or S",
             "Start: Enter · Select: Bksp · Space: fast",
             "Tab: multiplayer · T: chat · F4: leave battle",
             "F1: help · F3: report · V: wide view"]
    for hline in hints:
        text(surf, f.small, hline, (x, hy), DIM)
        hy += 19


def draw_toasts(surf, f: Fonts, toasts, game_w):
    y = 10
    now = time.time()
    for msg, until, color in toasts:
        if until < now:
            continue
        img = f.body.render(msg, True, color)
        r = img.get_rect(midtop=(game_w // 2, y))
        bg = r.inflate(20, 10)
        s = pygame.Surface(bg.size, pygame.SRCALPHA)
        s.fill((10, 12, 20, 210))
        surf.blit(s, bg)
        surf.blit(img, r)
        y += bg.h + 6


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


def mon_lines(app, mon, owner_label):
    rom = app.rom
    out = [(f"{mon.nick_str()}  ({rom.species_name(mon.species)})  Lv {mon.level}", TEXT),
           (f"HP {mon.hp}/{mon.max_hp} · OT {mon.ot_str()} · ID {mon.ot_id:05d}", DIM),
           ("Moves: " + ", ".join(rom.move_name(m) for m in mon.moves), DIM)]
    return out


def draw_trade(surf, f, app, game_w, game_h):
    t = app.trade
    w, h = min(game_w - 30, 600), min(game_h - 30, 500)
    r = pygame.Rect(0, 0, w, h)
    r.center = (game_w // 2, game_h // 2)
    shade = pygame.Surface((game_w, game_h), pygame.SRCALPHA)
    shade.fill((0, 0, 0, 140))
    surf.blit(shade, (0, 0))
    box(surf, r, PANEL, ACCENT, 10)
    text(surf, f.title, f"Trade with {t.partner_name}", (r.x + 18, r.y + 12), ACCENT)
    y = r.y + 50
    text(surf, f.body, "Your party", (r.x + 18, y), TEXT)
    y += 26
    party = app.game.party() if app.game else []
    for i, mon in enumerate(party):
        sel = i == app.trade_cursor
        offered = t.my_slot == i
        label = f"{'>' if sel else ' '} {mon.nick_str():<10} {app.rom.species_name(mon.species)} Lv{mon.level}"
        if offered:
            label += "   <- offered"
        text(surf, f.body, label, (r.x + 18, y), ACCENT if sel else TEXT)
        y += 24
    y = max(y, r.y + 50 + 26 + 6 * 24) + 10
    pygame.draw.line(surf, PANEL_HI, (r.x + 14, y), (r.right - 14, y))
    y += 10
    text(surf, f.body, f"{t.partner_name} offers:", (r.x + 18, y), TEXT)
    y += 26
    if t.their_mon:
        for s, color in mon_lines(app, t.their_mon, t.partner_name):
            text(surf, f.small if color == DIM else f.body, s, (r.x + 28, y), color)
            y += 22
    else:
        text(surf, f.small, "waiting for them to choose...", (r.x + 28, y), DIM)
        y += 22
    y += 8
    me = "[OK] you confirmed" if t.me_confirmed else "you: not confirmed"
    them = "[OK] they confirmed" if t.them_confirmed else "them: not confirmed"
    text(surf, f.body, me, (r.x + 18, y), GOOD if t.me_confirmed else DIM)
    text(surf, f.body, them, (r.x + w // 2, y), GOOD if t.them_confirmed else DIM)
    status = t.status_text()
    if status:
        text(surf, f.small, status, (r.x + 18, r.bottom - 52), ACCENT)
    text(surf, f.small, "[Up/Down] pick  [Enter] offer  [C] confirm  [Esc] cancel trade",
         (r.x + 18, r.bottom - 28), DIM)


def draw_help(surf, f, game_w, game_h):
    lines = [
        ("Game Boy controls", ACCENT),
        "  A button (talk, confirm, next text):  X  or  A",
        "  B button (back, cancel):  Z  or  S",
        "  Start: Enter      Select: Backspace      D-pad: arrow keys",
        "",
        ("YellowLink", ACCENT),
        "  Tab: link menu - battle (B), trade (R), practice (P)",
        "  T: chat     Space (hold): fast-forward     M: mute",
        "  V: wide view     F5/F6: window size     -/+: volume",
        "  F3: report a problem     F2: settings",
        "",
        ("Battles are exhibition matches - nothing is won or lost.", DIM),
        ("F4 leaves a live battle. Practice = their team played by the game AI.", DIM),
        ("After a trade, SAVE in-game (Start > Save).", DIM),
    ]
    modal(surf, f, game_w, game_h, "How to play", lines, "[Esc / Enter / F1] close")
