"""Mouse-driven panels: Multiplayer and Regions. Pure drawing + click-target registration."""
from __future__ import annotations

import math

import pygame

from . import badge_art, library
from .badges import REGION_NAMES, REGIONS, entry_is_shiny
from .challenge import CHALLENGE_GAMES
from .ui import ACCENT, BAD, DIM, GOOD, PANEL, PANEL_HI, TEXT, box, text, wrap

BTN = (58, 64, 86)
BTN_HOVER = (78, 86, 114)
BTN_OFF = (40, 43, 56)


NARROW_W = 600     # panels narrower than this (the DS window, small Game Boy windows) use a compact layout


def fit(font, s, width):
    """s, cut with "..." so it fits in width pixels."""
    if font.size(s)[0] <= width:
        return s
    while s and font.size(s + "...")[0] > width:
        s = s[:-1]
    return s.rstrip() + "..." if s else ""


def text_wrapped(surf, font, s, pos, width, color=TEXT, line_h=None):
    """Draw s wrapped to width; returns the y below the last line."""
    x, y = pos
    for ln in wrap(font, s, width):
        text(surf, font, ln, (x, y), color)
        y += line_h or font.get_height() + 1
    return y


def _panel_w(game_w):
    return min(game_w - 30, 620)


def button(surf, app, f, rect, label, cb, enabled=True, offset=(0, 0), primary=False, tip=""):
    """Draw a button and register its click target (in screen coordinates)."""
    screen_rect = rect.move(offset)
    hover = enabled and screen_rect.collidepoint(pygame.mouse.get_pos())
    color = BTN_OFF if not enabled else (BTN_HOVER if hover else (ACCENT if primary else BTN))
    pygame.draw.rect(surf, color, rect, border_radius=7)
    fg = (30, 28, 20) if (primary and enabled and not hover) else (TEXT if enabled else DIM)
    text(surf, f.body, label, rect.center, fg, anchor="center")
    if enabled:
        app.click_targets.append((screen_rect, cb, label))
    elif tip:
        app.click_targets.append((screen_rect, lambda: app.toast(tip, 3, BAD), label + " (disabled)"))
    return rect


def _panel_frame(surf, app, f, game_w, game_h, title, offset):
    shade = pygame.Surface((game_w, game_h), pygame.SRCALPHA)
    shade.fill((0, 0, 0, 140))
    surf.blit(shade, (0, 0))
    # clicking the dark area closes the panel (registered first = lowest priority)
    app.click_targets.append((pygame.Rect(offset[0], offset[1], game_w, game_h), app.close_panel, "backdrop"))
    w, h = min(game_w - 30, 620), min(game_h - 30, 560)
    r = pygame.Rect(0, 0, w, h)
    r.center = (game_w // 2, game_h // 2)
    box(surf, r, PANEL, ACCENT, 10)
    app.click_targets.append((r.move(offset), lambda: None, "panel"))  # swallow clicks inside the panel
    text(surf, f.title, title, (r.x + 18, r.y + 14), ACCENT)
    button(surf, app, f, pygame.Rect(r.right - 44, r.y + 10, 32, 30), "x", app.close_panel, offset=offset)
    return r


def _badge_summary(app, uid) -> dict:
    fn = getattr(app, "badge_summary", None)
    return fn(uid) if fn else {}


def draw_player_badges(surf, f, app, uid, area, offset, bottom):
    """A player's badges: 'Kanto (4 sockets)  Johto (...)  Sinnoh (...)', wrapping onto more lines when narrow;
    nothing below `bottom`. Returns the y below."""
    if not getattr(app, "badge_feature", True):
        app._badges_note = "Badges need a server update."
        if area.y + 18 <= bottom:
            text(surf, f.small, app._badges_note, (area.x, area.y + 2), DIM)
        return area.y + 20
    summ = _badge_summary(app, uid)
    rad = 9 if area.w < 420 else 10
    x, y = area.x, area.y
    line_h = 2 * rad + 6
    for region in REGIONS:
        label = REGION_NAMES[region]
        w = f.small.size(label)[0] + 6 + badge_art.row_width(rad, 4)
        if x > area.x and x + w > area.right:
            x, y = area.x, y + line_h
        if y + 2 * rad > bottom:
            break
        text(surf, f.small, label, (x, y + rad - 7), DIM)
        badge_art.draw_row(surf, app, f, x + f.small.size(label)[0] + 6, y, region, summ.get(region) or {},
                           offset, r=rad, gap=4, uid=uid)
        x += w + 16
    return y + line_h


def draw_multiplayer(surf, f, app, game_w, game_h, offset):
    app.badge_tip = None
    r = _panel_frame(surf, app, f, game_w, game_h, "Multiplayer", offset)
    others = [e for e in app.sorted_roster() if e.get("uid") != app.cfg["uid"]]
    narrow = r.w < NARROW_W
    # which server (group of friends) you're on + the way to another one
    label = "Servers" if narrow else "Switch server"
    sw = f.body.size(label)[0] + 28 if narrow else 150
    sb = pygame.Rect(r.right - 52 - sw, r.y + 10, sw, 30)
    button(surf, app, f, sb, label, lambda: app.toggle_panel("servers"), offset=offset)
    name = app.cfg.server_label(app.cfg.active_server)
    tx = r.x + 18 + f.title.size("Multiplayer")[0] + 14
    if narrow:
        if sb.x - 10 - tx >= f.small.size("on ab...")[0]:
            text(surf, f.small, fit(f.small, "on " + name, sb.x - 10 - tx), (tx, r.y + 20), DIM)
    else:
        while name and tx + f.small.size("on " + name)[0] > sb.x - 10:
            name = name[:-1]
        text(surf, f.small, "on " + name, (tx, r.y + 20), DIM)
    y = r.y + 56
    if not app.full or getattr(app, "ds", False):
        msg = f"Battles don't work in {app.game_info.title} - trades and gifts do (they use your PC)."
        if narrow:
            y = text_wrapped(surf, f.small, msg, (r.x + 18, y), r.w - 36, DIM, 18) + 4
        else:
            text(surf, f.small, msg, (r.x + 18, y), DIM)
            y += 22
    if not others:
        text(surf, f.body, "No other trainers yet - invite your friends!", (r.x + 18, y + 10), DIM)
        return
    text(surf, f.small, "Click a trainer:", (r.x + 18, y), DIM)
    y += 24
    row_h = 40
    for e in others:
        if y + row_h > r.bottom - 10:
            break
        uid = e.get("uid")
        online = bool(e.get("online"))
        selected = uid == app.panel_sel
        row = pygame.Rect(r.x + 12, y, r.w - 24, row_h - 4)
        hover = row.move(offset).collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, PANEL_HI if (selected or hover) else PANEL, row, border_radius=8)
        pygame.draw.circle(surf, GOOD if online else (90, 94, 110), (row.x + 16, row.centery), 6)
        nr = text(surf, f.body, e.get("name", "?"), (row.x + 32, row.y + 9), TEXT if online else DIM)
        lv = e.get("levels") or []
        info = ("online" if online else "offline") + f" · {len(lv)} Pokémon" + (f" · Lv {max(lv)}" if lv else "")
        region = app.player_region(e)
        if region:
            info = f"in {region} · " + info
        if app.is_nearby(uid):
            info = "nearby · " + info
        if narrow:
            room = row.right - 12 - nr.right - 12
            if f.small.size(info)[0] > room:       # compact: region + team size
                info = " · ".join(p for p in (("nearby" if app.is_nearby(uid) else region),
                                              f"{len(lv)} mon" + (f" Lv{max(lv)}" if lv else "")) if p)
            info = fit(f.small, info, room)
        text(surf, f.small, info, (row.right - 12, row.y + 11), DIM, anchor="topright")
        app.click_targets.append((row.move(offset), lambda u=uid: app.select_player(u), "player:" + e.get("name", "?")))
        y += row_h
        if selected:
            bx = row.x + 24
            bw = 106
            same_game = app.friend_game(uid) == app.game_id if online else True
            can_live = online and same_game and app.full and app.hooks is not None and not app.chal
            buttons = [
                ("Battle", lambda e=e: app.action_battle(e), can_live, True,
                 "They need to be online, and you both in Yellow."),
                ("Trade", lambda e=e: app.action_trade(e), online and not app.chal, False,
                 "They need to be online (trades use your PC and work across games)."),
                ("Gift", lambda e=e: app.action_gift(e), online and not app.chal, False,
                 "Give them a Pokémon from your PC - they need to be online."),
                ("Practice", lambda e=e: app.action_practice(e),
                 app.full and not app.gen2 and not getattr(app, "ds", False) and same_game and bool(lv) and not app.chal, False,
                 "Practice battles work in Yellow vs a trainer with Pokémon."),
            ]
            done = sum(1 for c in app.challenges_with(uid) if c["status"] == "completed")
            chal = ("Challenges" + (f" ({done})" if done else ""), lambda u=uid: app.open_challenges(u), True, False, "")
            if narrow:                    # two rows: Battle / Trade / Gift, then Practice / Challenges
                bx, avail = row.x + 12, row.w - 24
                for line in (buttons[:3], buttons[3:] + [chal]):
                    bw = (avail - 8 * (len(line) - 1)) // len(line)
                    for i, (label, cb, enabled, primary, tip) in enumerate(line):
                        button(surf, app, f, pygame.Rect(bx + i * (bw + 8), y, bw, 34), label, cb, enabled=enabled,
                               offset=offset, primary=primary, tip=tip)
                    y += 40
                y += 4
            else:
                for i, (label, cb, enabled, primary, tip) in enumerate(buttons):
                    button(surf, app, f, pygame.Rect(bx + i * (bw + 8), y, bw, 34), label, cb, enabled=enabled,
                           offset=offset, primary=primary, tip=tip)
                button(surf, app, f, pygame.Rect(bx + 4 * (bw + 8), y, bw + 14, 34), chal[0], chal[1],
                       offset=offset)
                y += 44
            y = draw_player_badges(surf, f, app, uid, pygame.Rect(row.x + 12, y, row.w - 24, 0), offset,
                                   r.bottom - 34) + 6
    foot = "Trade/Gift: Pokémon from your PC, any game  ·  Practice: their team vs the AI"
    if f.small.size(foot)[0] > r.w - 36:
        foot = "Trade/Gift: Pokémon from your PC, any game"
    text(surf, f.small, foot, (r.x + 18, r.bottom - 26), DIM)
    badge_art.draw_tip(surf, f, getattr(app, "badge_tip", None), r)


def draw_servers(surf, f, app, game_w, game_h, offset):
    r = _panel_frame(surf, app, f, game_w, game_h, "Servers", offset)
    narrow = r.w < NARROW_W
    y = r.y + 56
    intro = "One server per group of friends. Each has its own saves, PC and challenges."
    if narrow:
        y = text_wrapped(surf, f.small, intro, (r.x + 18, y), r.w - 36, DIM, 18) + 10
    else:
        text(surf, f.small, intro, (r.x + 18, y), DIM)
        y += 28
    busy = bool(app.trade or app.battle or app.live or app.chal)
    tip = "Pause your Soul Link first." if app.chal else "Finish your trade or battle first."
    row_h = 58
    for s in app.cfg.servers:
        if y + row_h > r.bottom - (98 if narrow else 60):
            text(surf, f.small, fit(f.small, "(more servers - remove one to see the rest)", r.w - 36), (r.x + 18, y),
                 DIM)
            break
        here = s["id"] == app.cfg.server_id
        card = pygame.Rect(r.x + 12, y, r.w - 24, row_h - 6)
        box(surf, card, PANEL_HI if here else PANEL, ACCENT if here else PANEL_HI, 8)
        bw = 72 if narrow else 84
        right = card.right - 10
        buttons = []
        if not here:
            buttons.append(("Sure?" if app.confirming_remove(s["id"]) else "Remove",
                            lambda sid=s["id"]: app.remove_server(sid), True, False, ""))
        buttons.append(("Edit", lambda sid=s["id"]: app.server_dialog(sid), True, False, ""))
        if not here:
            buttons.append(("Switch", lambda sid=s["id"]: app.switch_server(sid), not busy, True, tip))
        x = right
        for label, cb, enabled, primary, t in buttons:
            x -= bw
            button(surf, app, f, pygame.Rect(x, card.y + 9, bw - 6, 34), label, cb, enabled=enabled, offset=offset,
                   primary=primary, tip=t)
        name = app.cfg.server_label(s)
        maxw = x - card.x - 24
        while name and f.body.size(name)[0] > maxw:
            name = name[:-1]
        text(surf, f.body, name, (card.x + 14, card.y + 6), ACCENT if here else TEXT)
        sub = "you are here" if here else s.get("url", "").split("://", 1)[-1]
        if here:
            st = app.net.status
            sub += " · " + {"online": "connected"}.get(st, st) + \
                (f" ({app.net.status_detail})" if st != "online" and app.net.status_detail else "")
        owner = app.cfg.save_owner(s)
        if owner is not None:
            sub += " · save of " + app.cfg.server_label(owner)
        elif s.get("save_dir"):
            sub += " · shared save"
        elif any(o.get("save_dir") == s["id"] for o in app.cfg.servers):
            sub += " · save shared"
        while sub and f.small.size(sub)[0] > maxw:
            sub = sub[:-1]
        text(surf, f.small, sub, (card.x + 14, card.y + 29), GOOD if here and app.net.status == "online" else DIM)
        y += row_h
    button(surf, app, f, pygame.Rect(r.x + 18, r.bottom - 48, 180, 36), "Add a server...", app.server_dialog,
           offset=offset, primary=True)
    shared = "Your name and game files are shared by all servers."
    if narrow:
        text_wrapped(surf, f.small, shared, (r.x + 18, r.bottom - 88), r.w - 36, DIM, 18)
    else:
        text(surf, f.small, shared, (r.x + 210, r.bottom - 40), DIM)


def _region_game(app, region_id, installed_here):
    """The game used for a region: the player's pick, else the best-supported one they have."""
    pick = app.cfg.get("region_game", {}).get(region_id)
    if pick in installed_here:
        return pick
    if app.game_id in installed_here:
        return app.game_id
    return sorted(installed_here, key=lambda g: (not library.GAMES_BY_ID[g].full_support,
                                                 installed_here.index(g)))[0]


def draw_regions(surf, f, app, game_w, game_h, offset):
    app.badge_tip = None
    r = _panel_frame(surf, app, f, game_w, game_h, "Regions", offset)
    mine = _badge_summary(app, app.cfg["uid"])
    narrow = r.w < NARROW_W
    y = r.y + 56
    intro = "Each region is its own game and keeps its own progress."
    if f.small.size(intro)[0] > r.w - 36:
        intro = fit(f.small, "Each region keeps its own progress.", r.w - 36)
    text(surf, f.small, intro, (r.x + 18, y), DIM)
    y += 30
    installed = app.lib.games
    for region_id, region_name, game_ids in library.REGIONS:
        have = [g for g in game_ids if g in installed]
        here = app.game_info.region == region_id
        # badge sockets right of the region's name (hover: what it is and how to earn it); in a small window
        # where they'd reach the Travel button they get their own line under the name
        card_w = r.w - 24
        br_x = card_w - (110 if narrow else 140)             # Travel button, relative to the card
        bx_rel = 14 + f.title.size(region_name)[0] + 12
        rad = next((rr for rr in (11, 10, 9, 8) if bx_rel + badge_art.row_width(rr, 5) <= br_x - 8), None)
        extra = 0 if rad else 26
        card = pygame.Rect(r.x + 12, y, card_w, (92 if len(have) > 1 else 64) + extra)
        if card.bottom > r.bottom - 60:
            break
        box(surf, card, PANEL_HI if here else PANEL, ACCENT if here else PANEL_HI, 8)
        nr = text(surf, f.title, region_name, (card.x + 14, card.y + 8), ACCENT if here else TEXT)
        br = pygame.Rect(card.x + br_x, card.y + 12, 96 if narrow else 126, 36)
        if rad:
            badge_art.draw_row(surf, app, f, card.x + bx_rel, nr.centery - rad, region_id,
                               mine.get(region_id) or {}, offset, r=rad, gap=5, uid=app.cfg["uid"])
        else:
            badge_art.draw_row(surf, app, f, card.x + 14, nr.bottom + 4, region_id, mine.get(region_id) or {},
                               offset, r=10, gap=5, uid=app.cfg["uid"])
        if not have:
            names = [library.GAMES_BY_ID[g].title for g in game_ids]
            text(surf, f.small, fit(f.small, "Add your " + " / ".join(names) + " game file to unlock this region",
                                    card.w - 28), (card.x + 14, card.y + 40 + extra), DIM)
        else:
            gid = _region_game(app, region_id, have)
            info = library.GAMES_BY_ID[gid]
            status = f"Ready - via {info.title}"
            if not info.full_support:
                status += "  (single player: multiplayer needs Crystal v1.0)" if gid == "crystal" else \
                    "  (single player for now)"
            if narrow:
                if f.small.size(status)[0] > br.x - card.x - 22:
                    status = status.replace("Pokémon ", "")
                status = fit(f.small, status, br.x - card.x - 22)
            text(surf, f.small, status, (card.x + 14, card.y + 40 + extra), GOOD if info.full_support else TEXT)
            if here and app.game_id == gid:
                text(surf, f.small, "you are here", br.center, GOOD, anchor="center")
                app.click_targets.append((br.move(offset), lambda: None, "you are here"))
            else:
                button(surf, app, f, br, "Travel", lambda g=gid: app.travel(g), offset=offset, primary=True,
                       enabled=not (app.trade or app.battle or app.live),
                       tip="Finish your trade or battle first.")
            if len(have) > 1:
                # optional: which version to play this region with
                text(surf, f.small, "Play with:", (card.x + 14, card.y + 64 + extra), DIM)
                bx = card.x + 90
                for g in have:
                    label = library.GAMES_BY_ID[g].title.replace("Pokémon ", "")
                    w = f.small.size(label)[0] + 22
                    chip = pygame.Rect(bx, card.y + 60 + extra, w, 24)
                    sel = g == gid
                    pygame.draw.rect(surf, ACCENT if sel else PANEL_HI, chip, border_radius=12)
                    text(surf, f.small, label, chip.center, (30, 28, 20) if sel else TEXT, anchor="center")
                    app.click_targets.append((chip.move(offset), lambda rg=region_id, g=g: app.pick_region_game(rg, g),
                                              "version:" + g))
                    bx += w + 8
        y = card.bottom + 10
    button(surf, app, f, pygame.Rect(r.x + 18, r.bottom - 48, 200, 36), "Add a game file...", app.add_rom_dialog,
           offset=offset)
    text(surf, f.small, ".gb, .gbc or .nds", (r.x + 230, r.bottom - 40), DIM)
    badge_art.draw_tip(surf, f, getattr(app, "badge_tip", None), r)



# --- challenges -------------------------------------------------------------------------------------------
GOLD, GOLD_DARK = (240, 200, 80), (150, 112, 30)


def draw_badge(surf, center, state, small=False):
    """The badge slot: an empty dashed circle, or a gold badge when the challenge was completed."""
    rad = 13 if small else 20
    cx, cy = center
    if state == "completed":
        pygame.draw.circle(surf, GOLD_DARK, center, rad)
        pygame.draw.circle(surf, GOLD, center, rad - 3)
        pts = []
        for i in range(10):                          # a star
            a = -math.pi / 2 + i * math.pi / 5
            rr = (rad - 6) if i % 2 == 0 else (rad - 6) * 0.45
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        pygame.draw.polygon(surf, (255, 248, 210), pts)
    else:
        for i in range(0, 360, 30):
            a0, a1 = math.radians(i), math.radians(i + 16)
            pygame.draw.line(surf, (90, 94, 110), (cx + rad * math.cos(a0), cy + rad * math.sin(a0)),
                             (cx + rad * math.cos(a1), cy + rad * math.sin(a1)), 2)


def _status_line(app, c):
    st = c.get("status")
    if st == "completed":
        return "Completed together!", GOOD
    if st == "failed":
        return f"Failed: {c.get('reason', '')}", BAD
    if st == "abandoned":
        reason = c.get("reason") or "abandoned"
        return reason[:1].upper() + reason[1:], DIM
    me = app.cfg["uid"]
    other = next((u for u in c["players"] if u != me), None)
    prog = c.get("progress", {})
    stats = c.get("stats", {})
    game = library.GAMES_BY_ID.get(c.get("game"))
    mine = stats.get(me, {})
    return (f"In progress · {game.title if game else c.get('game')} · you: {mine.get('caught', 0)} caught, "
            f"{mine.get('dead', 0)} lost · badges {bin(prog.get(me, {}).get('badges', 0)).count('1')}"
            f"/{bin(prog.get(other, {}).get('badges', 0)).count('1')}"), TEXT


def draw_challenges(surf, f, app, game_w, game_h, offset):
    uid = app.chal_view_uid
    e = next((r for r in app.roster if r.get("uid") == uid), {}) or {}
    name = e.get("name", "?")
    narrow = _panel_w(game_w) < NARROW_W
    title = f"Challenges with {name}"
    if narrow:
        title = fit(f.title, title, _panel_w(game_w) - 160)
    r = _panel_frame(surf, app, f, game_w, game_h, title, offset)
    button(surf, app, f, pygame.Rect(r.right - 120, r.y + 10, 68, 30), "Back",
           lambda: app.toggle_panel("multiplayer"), offset=offset)
    y = r.y + 58
    if app.chal_form and app.chal_form.get("uid") == uid:
        _draw_challenge_form(surf, f, app, r, y, offset)
        return
    history = [c for c in app.challenges_with(uid) if c["kind"] == "soul_link"]
    finished = [c for c in history if c["status"] != "active"]
    completed = sum(1 for c in history if c["status"] == "completed")
    active = next((c for c in history if c["status"] == "active"), None)
    # the Soul Link row with its badge slot
    card = pygame.Rect(r.x + 12, y, r.w - 24, 118)
    box(surf, card, PANEL_HI, PANEL_HI, 8)
    draw_badge(surf, (card.x + 34, card.y + 34), "completed" if completed else "empty")
    if completed > 1:
        text(surf, f.small, f"x{completed}", (card.x + 34, card.y + 60), GOLD, anchor="midtop")
    text(surf, f.title, "Soul Link", (card.x + 66, card.y + 10), TEXT)
    room = card.right - 10 - (card.x + 66)        # text width right of the badge
    text(surf, f.small, fit(f.small, "A Nuzlocke for two: linked partners live and die together.", room),
         (card.x + 66, card.y + 40), DIM)
    online = bool(e.get("online"))
    if active:
        line, color = _status_line(app, active)
        text(surf, f.small, fit(f.small, line, room), (card.x + 66, card.y + 62), color)
        if app.chal and app.chal["id"] == active["id"]:
            text(surf, f.small, "You're playing it right now.", (card.x + 66, card.y + 86), GOOD)
        else:
            free = online and not e.get("challenge") and not app.chal
            button(surf, app, f, pygame.Rect(card.x + 66, card.y + 82, 130, 30), "Continue",
                   lambda c=active: app.continue_challenge(c), enabled=free, offset=offset, primary=True,
                   tip=f"{name} needs to be online and free to continue." if not app.chal
                   else "Leave your current challenge first.")
            if getattr(app, "chal_confirm", None) == active["id"]:
                button(surf, app, f, pygame.Rect(card.x + 206, card.y + 82, min(170, card.right - 8 - card.x - 206), 30),
                       "Sure?" if narrow else "Really abandon?",
                       lambda c=active: (app.abandon_challenge(c), setattr(app, "chal_confirm", None)),
                       offset=offset)
            else:
                button(surf, app, f, pygame.Rect(card.x + 206, card.y + 82, 110, 30), "Abandon",
                       lambda c=active: setattr(app, "chal_confirm", c["id"]), offset=offset)
    else:
        last = history[0] if history else None
        if last:
            line, color = _status_line(app, last)
            text(surf, f.small, fit(f.small, "Last run: " + line, room), (card.x + 66, card.y + 62), color)
        shared = app.shared_games(uid)
        can = online and not e.get("challenge") and not app.chal and bool(shared)
        tip = ("Leave your current challenge first." if app.chal else f"{name} needs to be online." if not online
               else f"{name} is busy in another challenge." if e.get("challenge")
               else "You both need Pokémon Yellow, Crystal (v1.0) or Platinum added in Regions.")
        button(surf, app, f, pygame.Rect(card.x + 66, card.y + 82, 170, 30), "Start a Soul Link",
               lambda: app.new_challenge_form(uid, name), enabled=can, offset=offset, primary=True, tip=tip)
    y = card.bottom + 14
    # history
    if finished:
        text(surf, f.body, "Past runs", (r.x + 18, y), TEXT)
        y += 26
        for c in finished[:6]:
            if y > r.bottom - 30:
                break
            draw_badge(surf, (r.x + 32, y + 9), "completed" if c["status"] == "completed" else "empty", small=True)
            line, color = _status_line(app, c)
            game = library.GAMES_BY_ID.get(c.get("game"))
            text(surf, f.small, fit(f.small, f"{game.title if game else ''}: {line}", r.right - 18 - (r.x + 54)),
                 (r.x + 54, y + 1), color)
            y += 26
    text(surf, f.small, "More challenges coming later.", (r.x + 18, r.bottom - 26), DIM)


SETTINGS = [   # key, default, label, description, short description (small windows)
    ("exp_share_all", True, "Exp Share All",
     "Like the newer games: Pokémon that battle get full EXP, the rest of your party gets half. "
     "Off = the original game's rules (Exp. All / Exp. Share items).",
     "Battlers get full EXP, the rest half."),
    ("show_names", True, "Name tags above friends", "", ""),
    ("skip_intro", True, "Skip the intro",
     "Start straight into your saved game (Continue is picked for you).", "Continue your save at once."),
]


def draw_settings(surf, f, app, game_w, game_h, offset):
    r = _panel_frame(surf, app, f, game_w, game_h, "Settings", offset)
    x, y = r.x + 18, r.y + 58
    width = r.w - 36
    small = width < 330
    note = "Changes apply right away." if small else "Changes apply right away, also in the middle of your adventure."
    note_lines = wrap(f.small, note, width)
    bottom = r.bottom - 16 - 18 * len(note_lines)          # rows stop above the note
    for key, default, label, desc, short in SETTINGS:
        lines = wrap(f.small, short if small else desc, width - 56) if desc else []
        n_desc = len(lines)
        if key == "exp_share_all" and getattr(app, "exp_share", None) is None:
            lines += wrap(f.small, "Not active for this game file." if small else
                          "Not active for this game file (needs the supported Yellow, Crystal or Platinum).",
                          width - 56)
        elif key == "exp_share_all" and exp_share_run_note(app):
            lines += wrap(f.small, exp_share_run_note(app), width - 56)
        if y + 32 > bottom:
            break
        on = bool(app.cfg.get(key, default))
        row = pygame.Rect(x, y, width, 32)

        def flip(k=key, d=default, lab=label):
            new = not bool(app.cfg.get(k, d))
            app.set_setting(k, new, f"{lab}: {'on' if new else 'off'}")
        shown = label
        if f.body.size(shown)[0] > width - 56:
            shown = {"show_names": "Name tags"}.get(key, shown)
        _toggle(surf, app, f, row, shown, on, flip, offset)
        y += 34
        for i, line in enumerate(lines):
            if y + 18 > bottom:
                break
            warn = i >= n_desc
            text(surf, f.small, line, (x + 56, y), BAD if warn else DIM)
            y += 18
        y += 10
    yy = bottom + 8
    for line in note_lines:
        text(surf, f.small, line, (x, yy), DIM)
        yy += 18


def _toggle(surf, app, f, rect, label, on, cb, offset):
    hover = rect.move(offset).collidepoint(pygame.mouse.get_pos())
    sw = pygame.Rect(rect.x, rect.y + 4, 44, 24)
    pygame.draw.rect(surf, GOOD if on else (70, 74, 92), sw, border_radius=12)
    pygame.draw.circle(surf, (245, 245, 250), (sw.right - 12 if on else sw.x + 12, sw.centery), 9)
    text(surf, f.body, label, (rect.x + 56, rect.y + 5), TEXT if not hover else ACCENT)
    app.click_targets.append((rect.move(offset), cb, label))


def rules_line(rules: dict, game: str = "") -> str:
    """'Dupes clause on, shiny clause on, Exp Share All: off' for a run's rules (legacy runs: Exp Share off)."""
    rules = rules or {}
    parts = ["dupes clause " + ("on" if rules.get("dupes", True) else "off")]
    if rules.get("shiny", True) and game != "yellow":
        parts.append("shiny clause on")
    parts.append("Exp Share All: " + ("on" if rules.get("exp_share") else "off"))
    return ", ".join(parts)


def exp_share_run_note(app):
    """Settings: in a Soul Link the run's Exp Share All rule applies (both players), not the toggle."""
    run = app.chal_exp_share() if hasattr(app, "chal_exp_share") else None
    if run is None:
        return None
    return (f"This Soul Link run: Exp Share All {'on' if run else 'off'} (set when the run started). "
            f"The toggle is for your main game.")


def _game_tip(app, form, g) -> str:
    """Why a game button in the Soul Link form is off."""
    title = library.GAMES_BY_ID[g].title
    if g not in app.challenge_games():
        return f"Add {title} in Regions first" + (" (v1.0)." if g == "crystal" else ".")
    e = next((x for x in app.roster if x.get("uid") == form["uid"]), {}) or {}
    if g not in ("yellow", "crystal") and g in (e.get("games") or []) and not e.get("challenge_games"):
        if "challenge_games" not in e:            # the server doesn't send it at all: it's the server that's old
            return f"The server needs updating for a {title} Soul Link (ask the host)."
        return f"{form['name']} needs to update YellowLink (press U) for a {title} Soul Link."
    return f"{form['name']} doesn't have {title} added" + (" (v1.0)." if g == "crystal" else ".")


def _draw_challenge_form(surf, f, app, r, y, offset):
    form = app.chal_form
    narrow = r.w < NARROW_W
    text(surf, f.title, "New Soul Link", (r.x + 18, y), ACCENT)
    y += 34
    intro = ["You both start a fresh save, separate from your main games.",
             "It only runs while you're both in it - pause and continue together any time."]
    if narrow:
        for line in intro:
            y = text_wrapped(surf, f.small, line, (r.x + 18, y), r.w - 36, DIM, 18)
        y += 14
    else:
        text(surf, f.small, intro[0], (r.x + 18, y), DIM)
        text(surf, f.small, intro[1], (r.x + 18, y + 18), DIM)
        y += 50
    text(surf, f.body, "Game", (r.x + 18, y + 6), TEXT)
    shared = app.shared_games(form["uid"])
    games = CHALLENGE_GAMES
    x0 = r.x + 82 if narrow else r.x + 100
    gw = (r.right - 18 - x0 - 10 * (len(games) - 1)) // len(games)
    full = not narrow and all(f.body.size(library.GAMES_BY_ID[g].title)[0] <= gw - 14 for g in games)
    for i, g in enumerate(games):
        info = library.GAMES_BY_ID[g]
        rect = pygame.Rect(x0 + i * (gw + 10), y, gw, 34)
        sel = form["game"] == g
        if sel:
            pygame.draw.rect(surf, ACCENT, rect.inflate(6, 6), 2, border_radius=9)
        label = info.title if full else info.title.replace("Pokémon ", "")
        button(surf, app, f, rect, label, lambda g=g: form.__setitem__("game", g), enabled=g in shared,
               offset=offset, primary=sel, tip=_game_tip(app, form, g))
    y += 54
    tw = r.w - 36 if narrow else 420
    shiny_ok = form["game"] != "yellow"
    dupes = "Dupes clause (skip families you already caught)"
    shiny = "Shiny clause (shinies are a free catch)" + ("" if shiny_ok else " - not in Yellow")
    if narrow:
        dupes, shiny = fit(f.body, dupes, tw - 56), fit(f.body, shiny, tw - 56)
    _toggle(surf, app, f, pygame.Rect(r.x + 18, y, tw, 30), dupes,
            form["dupes"], lambda: form.__setitem__("dupes", not form["dupes"]), offset)
    y += 40
    _toggle(surf, app, f, pygame.Rect(r.x + 18, y, tw, 30), shiny,
            form["shiny"], lambda: form.__setitem__("shiny", not form["shiny"]), offset)
    y += 40
    exp = "Exp Share All for both of you" + ("" if app.can_exp_share_rule(form["uid"]) else
                                             f" - {form['name']} must update")
    if narrow:
        exp = fit(f.body, "Exp Share All (both)" if app.can_exp_share_rule(form["uid"]) else
                  "Exp Share All (needs update)", tw - 56)
    _toggle(surf, app, f, pygame.Rect(r.x + 18, y, tw, 30), exp, bool(form.get("exp_share")),
            app.toggle_exp_share_rule, offset)
    y += 52
    rules = ["Only the first wild encounter per area counts (once you have Poké Balls).",
             "Same area = partners. Faint, and your partner's goes too. Missed = no partner.",
             "PC rule: box one, the partner follows. Blackout = run over for both.",
             "Win: both of you reach the Hall of Fame."]
    if narrow:
        # wrapped rules as far as they fit; Invite / Cancel always at the bottom of the panel
        lines = [ln for rule in rules for ln in wrap(f.small, "· " + rule, r.w - 36)]
        room = max(0, (r.bottom - 56 - y) // 18)
        if len(lines) > room:
            lines = lines[:max(0, room - 1)] + (["..."] if room else [])
        for ln in lines:
            text(surf, f.small, ln, (r.x + 18, y), DIM)
            y += 18
        y = r.bottom - 48
    else:
        for line in rules:
            text(surf, f.small, "· " + line, (r.x + 18, y), DIM)
            y += 19
        y += 10
    button(surf, app, f, pygame.Rect(r.x + 18, y, 200, 36), f"Invite {form['name']}", app.send_challenge,
           enabled=bool(form.get("game")), offset=offset, primary=True)
    button(surf, app, f, pygame.Rect(r.x + 230, y, 110, 36), "Cancel",
           lambda: setattr(app, "chal_form", None), offset=offset)


def draw_soullink(surf, f, app, game_w, game_h, offset):
    """In a run: the linked pairs, and the Pause button."""
    _, partner = app.chal_partner()
    narrow = _panel_w(game_w) < NARROW_W
    title = f"Soul Link with {partner}"
    r = _panel_frame(surf, app, f, game_w, game_h, fit(f.title, title, _panel_w(game_w) - 90) if narrow else title,
                     offset)
    y = r.y + 54
    sl = app.sl
    if getattr(app, "evo_pick", False):
        _draw_tradeevo_picker(surf, f, app, r, y, offset, narrow)
        return
    # columns: area / you / partner (the DS window and small Game Boy windows are narrower)
    if narrow:
        cw = (r.w - 36) // 3
        cx = (r.x + 18, r.x + 18 + cw + 4, r.x + 18 + 2 * cw + 8)
        cols = (cw, cw - 4, r.right - 18 - cx[2])
    else:
        cx, cols = (r.x + 18, r.x + 200, r.x + 390), (180, 188, r.right - 18 - (r.x + 390))
    if sl is None:
        text(surf, f.body, "Connecting...", (r.x + 18, y), DIM)
    else:
        cnt = sl.counts()
        rec = app.chal_rec or {}
        line = (f"{cnt['alive']} alive · {cnt['dead']} lost · rules: "
                + rules_line(rec.get("rules") or sl.rules, rec.get("game", "")))
        text(surf, f.small, fit(f.small, line, r.w - 36) if narrow else line, (r.x + 18, y), DIM)
        y += 26
        text(surf, f.small, "Area", (cx[0], y), DIM)
        text(surf, f.small, "You", (cx[1], y), DIM)
        text(surf, f.small, fit(f.small, partner, cols[2]) if narrow else partner, (cx[2], y), DIM)
        y += 20
        view = app.referee.view if app.referee else None
        rows = sl.pairs()
        if not rows:
            text(surf, f.body, "No encounters yet. Go catch something!", (r.x + 18, y + 6), DIM)
        mine_dead, theirs_dead = sl.mine()["dead"], sl.theirs()["dead"]

        def cell(key, st, dex, dead):
            if st == "caught":
                name = app.dex_name(dex or 0)
                return (f"{name} (lost)", BAD) if key in dead else (name, GOOD)
            if st == "failed":
                return "missed", BAD
            if st == "open":
                return "in battle...", ACCENT
            return "-", DIM

        bottom = r.bottom - (110 if narrow else 106)
        for area, ka, kb, sa, sb, da, db in rows:
            if y > bottom:
                text(surf, f.small, "...", (r.x + 18, y), DIM)
                break
            label = view.area_name(area) if view else area
            if "*" in area:
                label += " (shiny)"
            s1, c1 = cell(ka, sa, da, mine_dead)
            s2, c2 = cell(kb, sb, db, theirs_dead)
            if narrow:
                label, s1, s2 = fit(f.small, label, cols[0] - 4), fit(f.small, s1, cols[1] - 4), \
                    fit(f.small, s2, cols[2])
            text(surf, f.small, label[:24], (cx[0], y), TEXT)
            text(surf, f.small, s1, (cx[1], y), c1)
            text(surf, f.small, s2, (cx[2], y), c2)
            y += 20
    evo_ok = hasattr(app, "tradeevo_blocker")
    if narrow:
        bw = (r.w - 36 - 8) // 2 if evo_ok else 210
        fits = lambda s: f.body.size(s)[0] <= bw - 14          # noqa: E731
        button(surf, app, f, pygame.Rect(r.x + 18, r.bottom - 50, bw, 36),
               "Pause & go back" if fits("Pause & go back") else "Pause",
               lambda: app.leave_to_main("paused"), offset=offset, primary=True)
        if evo_ok:
            button(surf, app, f, pygame.Rect(r.x + 26 + bw, r.bottom - 50, bw, 36),
                   "Trade evolution" if fits("Trade evolution") else "Trade evo",
                   app.open_tradeevo_picker, offset=offset)
        text_wrapped(surf, f.small, "Your run is saved. Continue it together any time from Multiplayer > "
                     "Challenges.", (r.x + 18, r.bottom - 92), r.w - 36, DIM, 18)
        return
    button(surf, app, f, pygame.Rect(r.x + 18, r.bottom - 50, 210, 36), "Pause & go back",
           lambda: app.leave_to_main("paused"), offset=offset, primary=True)
    if evo_ok:
        button(surf, app, f, pygame.Rect(r.x + 240, r.bottom - 50, 190, 36), "Trade evolution",
               app.open_tradeevo_picker, offset=offset)
    text(surf, f.small, fit(f.small, "Your run is saved. Continue it together any time from Multiplayer > "
                                     "Challenges.", r.w - 36), (r.x + 18, r.bottom - 80), DIM)


def _draw_tradeevo_picker(surf, f, app, r, y, offset, narrow):
    """Soul Link panel, "Trade evolution": the party mons that evolve by trade (SPEC.md section 7). Small windows
    (Game Boy 2x, ...) get compact rows and pages."""
    _, partner = app.chal_partner()
    w = r.w - 36
    compact = r.h < 420
    if compact:
        text(surf, f.small, fit(f.small, f"Trade and trade back with {partner} - it stays yours and linked.", w),
             (r.x + 18, y), DIM)
        y += 22
    else:
        y = text_wrapped(surf, f.small, f"Trade and trade back with {partner}: your Pokémon goes over and comes "
                         f"straight back, evolved. It stays yours and keeps its Soul Link.", (r.x + 18, y), w, DIM,
                         18) + 8
    blocker = app.tradeevo_blocker()
    if blocker:
        if compact:
            text(surf, f.small, fit(f.small, blocker, w), (r.x + 18, y), BAD)
            y += 22
        else:
            y = text_wrapped(surf, f.small, blocker, (r.x + 18, y), w, BAD, 18) + 6
    opts = app.tradeevo_options()
    bottom = r.bottom - 58                                   # (the Back button row below)
    page_buttons = False
    if opts is None:
        text(surf, f.body, "Reading your party...", (r.x + 18, y), DIM)
    elif not opts:
        y = text_wrapped(surf, f.body, "No Pokémon in your party evolves by trade.", (r.x + 18, y), w, TEXT) + 4
        hint = ("Kadabra, Machoke, Graveler and Haunter do." if not app.gen2 and not getattr(app, "ds", False) else
                "Kadabra, Machoke, Graveler, Haunter do - and some others holding the right item "
                "(Onix + Metal Coat...).")
        text_wrapped(surf, f.small, hint, (r.x + 18, y), w, DIM, 18)
    else:
        bw, row_h, bh = (96, 36, 30) if compact else (110, 40, 34)
        per = max(1, (bottom - y) // row_h)
        pages = (len(opts) + per - 1) // per
        page = min(max(0, getattr(app, "evo_page", 0)), pages - 1)
        for o in opts[page * per:(page + 1) * per]:
            label = f"{o.label()} -> {o.into_name}"
            text(surf, f.body, fit(f.body, label, w - bw - 10), (r.x + 18, y), TEXT if o.ok else DIM)
            note = (f"uses up its {o.item}" if o.item else "") if o.ok else o.why
            if note:
                text(surf, f.small, fit(f.small, note, w - bw - 10), (r.x + 18, y + 19), DIM if o.ok else BAD)
            button(surf, app, f, pygame.Rect(r.right - 18 - bw, y - 2, bw, bh), "Evolve",
                   lambda o=o: app.request_tradeevo(o), enabled=o.ok and not blocker, offset=offset,
                   tip=(blocker or (f"{o.label()} {o.why}." if o.why else "")))
            y += row_h
        if pages > 1:
            page_buttons = True
            x = r.x + 18 + 100 + 10
            button(surf, app, f, pygame.Rect(x, r.bottom - 50, 44, 36), "<",
                   lambda: setattr(app, "evo_page", page - 1), enabled=page > 0, offset=offset)
            text(surf, f.small, f"{page + 1}/{pages}", (x + 66, r.bottom - 32), DIM, anchor="center")
            button(surf, app, f, pygame.Rect(x + 88, r.bottom - 50, 44, 36), ">",
                   lambda: setattr(app, "evo_page", page + 1), enabled=page < pages - 1, offset=offset)
    button(surf, app, f, pygame.Rect(r.x + 18, r.bottom - 50, 100 if page_buttons or compact else 120, 36), "Back",
           lambda: setattr(app, "evo_pick", False), offset=offset)


# --- trades / gifts (PC database) --------------------------------------------------------------------------
def is_shiny_entry(app, e) -> bool:
    """Shiny mark for a PC entry (Yellow: Gen-2 shiny DVs count, SPEC.md section 9); cached per entry id + data."""
    cache = getattr(app, "_shiny_marks", None)
    if cache is None:
        cache = app._shiny_marks = {}
    key = (e.get("id"), e.get("origin"), e.get("gen1"), e.get("gen2"), e.get("gen4"))
    if key not in cache:
        if len(cache) > 2000:
            cache.clear()
        cache[key] = entry_is_shiny(e)
    return cache[key]


def mon_label(surf, f, app, e, pos, color, width, prefix="", suffix=""):
    """A PC entry's name line, cut to width, with a small drawn sparkle after it when it's shiny."""
    shiny = is_shiny_entry(app, e)
    label = fit(f.small, prefix + app.entry_label(e) + suffix, width - (16 if shiny else 0))
    rect = text(surf, f.small, label, pos, color)
    if shiny:
        dark_bg = sum(color) < 200                  # picked row: dark text on yellow -> a purple mark
        badge_art.mini_sparkle(surf, (rect.right + 8, rect.centery), 5,
                               (120, 60, 170) if dark_bg else (255, 220, 90))
    return rect


def draw_trade(surf, f, app, game_w, game_h, offset):
    t = app.trade
    if t is None:
        return
    title = (f"Gift for {t.partner_name}" if t.giving else f"Gift from {t.partner_name}") if t.gift \
        else f"Trade with {t.partner_name}"
    shade = pygame.Surface((game_w, game_h), pygame.SRCALPHA)
    shade.fill((0, 0, 0, 150))
    surf.blit(shade, (0, 0))
    w, h = min(game_w - 30, 640), min(game_h - 30, 540)
    r = pygame.Rect(0, 0, w, h)
    r.center = (game_w // 2, game_h // 2)
    box(surf, r, PANEL, ACCENT, 10)
    app.click_targets.append((r.move(offset), lambda: None, "trade window"))
    if r.w < NARROW_W:
        _draw_trade_narrow(surf, f, app, t, r, offset, title)
        return
    text(surf, f.title, title, (r.x + 18, r.y + 14), ACCENT)
    col_w = (r.w - 48) // 2
    left = pygame.Rect(r.x + 16, r.y + 54, col_w, r.h - 140)
    right = pygame.Rect(left.right + 16, left.y, col_w, left.h)

    # left: your PC - click to add to / remove from your offer
    if t.receiving_only:
        text(surf, f.body, "You're receiving a gift", (left.x, left.y), TEXT)
        text(surf, f.small, "Nothing leaves your PC.", (left.x, left.y + 28), DIM)
        text(surf, f.small, "Accept when you've seen it.", (left.x, left.y + 48), DIM)
    else:
        rows = app.trade_entries()
        text(surf, f.body, "Your PC", (left.x, left.y), TEXT)
        text(surf, f.small, "click to pick (several OK)", (left.x + 78, left.y + 4), DIM)
        row_h = 34
        per = max(1, (left.h - 70) // row_h)
        app.trade_per = per
        app.trade_scroll = max(0, min(app.trade_scroll, max(0, len(rows) - per)))
        app.trade_sel = max(app.trade_scroll, min(app.trade_sel, app.trade_scroll + per - 1, len(rows) - 1))
        y = left.y + 30
        if not rows:
            text(surf, f.small, "Your PC is empty. Deposit Pokémon in the", (left.x, y), DIM)
            text(surf, f.small, "game's PC first (they're saved within ~30 s).", (left.x, y + 18), DIM)
        for i in range(app.trade_scroll, min(len(rows), app.trade_scroll + per)):
            bi, e = rows[i]
            rr = pygame.Rect(left.x, y, left.w, row_h - 4)
            picked = t.offered(e["id"])
            hover = rr.move(offset).collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(surf, ACCENT if picked else (PANEL_HI if (hover or i == app.trade_sel) else PANEL),
                             rr, border_radius=6)
            fg = (30, 28, 20) if picked else TEXT
            check = pygame.Rect(rr.x + 6, rr.y + 7, 16, 16)
            pygame.draw.rect(surf, fg if picked else DIM, check, 2, border_radius=3)
            if picked:
                pygame.draw.lines(surf, fg, False, [(check.x + 3, check.y + 8), (check.x + 7, check.y + 12),
                                                    (check.x + 13, check.y + 3)], 2)
            mon_label(surf, f, app, e, (rr.x + 30, rr.y + 2), fg, rr.w - 36)
            note = app.entry_note(e)
            sub = f"Box {bi + 1}" + (f" · from {e.get('origin', '').title()}" if e.get("origin") not in
                                     (None, "", app.game_id) else "")
            text(surf, f.small, sub, (rr.x + 30, rr.y + 15), (60, 55, 40) if picked else (note[1] if note else DIM))
            app.click_targets.append((rr.move(offset), lambda e=e, i=i: (setattr(app, "trade_sel", i),
                                                                         app.offer_entry(e)), "offer"))
            y += row_h
        if len(rows) > per:
            button(surf, app, f, pygame.Rect(left.right - 66, left.y - 2, 30, 26), "^",
                   lambda: setattr(app, "trade_scroll", max(0, app.trade_scroll - per)), offset=offset)
            button(surf, app, f, pygame.Rect(left.right - 32, left.y - 2, 30, 26), "v",
                   lambda: setattr(app, "trade_scroll", app.trade_scroll + per), offset=offset)
        if rows:
            sel_box = rows[min(app.trade_sel, len(rows) - 1)][0]
            by = left.bottom - 32
            button(surf, app, f, pygame.Rect(left.x, by, 150, 30), f"Select box {sel_box + 1}",
                   lambda b=sel_box: app.select_box(b), offset=offset)
            button(surf, app, f, pygame.Rect(left.x + 158, by, 80, 30), "Clear", app.clear_offer,
                   enabled=bool(t.my_entries), offset=offset)

    # right: what changes hands
    y = right.y
    if t.gift and t.giving:
        head, shown, empty = f"You're giving ({len(t.my_entries)}):", t.my_entries, "Pick Pokémon on the left."
    else:
        head = f"{t.partner_name} {'is giving you' if t.gift else 'offers'} ({len(t.their_entries)}):"
        shown, empty = t.their_entries, "Waiting for them to choose..."
    text(surf, f.body, head, (right.x, y), TEXT)
    y += 30
    if not shown:
        text(surf, f.small, empty, (right.x, y), DIM)
        y += 26
    max_rows = 7 if t.gift else 5
    for e in shown[:max_rows]:
        card = pygame.Rect(right.x, y, right.w, 40)
        box(surf, card, PANEL_HI, PANEL_HI, 6)
        mon_label(surf, f, app, e, (card.x + 8, card.y + 3), TEXT, card.w - 16)
        note = app.entry_note(e)
        origin = e.get("origin")
        sub = note[0] if note else (f"OT {e.get('ot', '?')}" + (f" · from {origin.title()}" if origin else ""))
        text(surf, f.small, sub, (card.x + 8, card.y + 20), note[1] if note else DIM)
        y += 44
    if len(shown) > max_rows:
        text(surf, f.small, f"... and {len(shown) - max_rows} more", (right.x, y), DIM)
        y += 20
    if not t.gift and t.my_entries:
        text(surf, f.small, f"You offer ({len(t.my_entries)}): " + app.mons_text(t.my_entries),
             (right.x, max(y + 4, right.bottom - 44)), ACCENT)
    text(surf, f.small, "Trade evolutions happen on arrival.", (right.x, right.bottom - 20), DIM)

    # bottom: confirmations + buttons
    by = r.bottom - 76
    me = "You confirmed" if t.me_confirmed else "You haven't confirmed"
    them = f"{t.partner_name} confirmed" if t.them_confirmed else f"{t.partner_name} hasn't confirmed"
    text(surf, f.small, me, (r.x + 18, by), GOOD if t.me_confirmed else DIM)
    text(surf, f.small, them, (r.x + 18, by + 18), GOOD if t.them_confirmed else DIM)
    if t.receiving_only:
        label = "Accept gift" if len(t.their_entries) <= 1 else f"Accept {len(t.their_entries)} Pokémon"
    elif t.gift:
        label = "Give it" if len(t.my_entries) <= 1 else f"Give {len(t.my_entries)}"
    else:
        label = "Confirm trade"
    button(surf, app, f, pygame.Rect(r.right - 300, r.bottom - 54, 160, 38), label, app.confirm_trade,
           enabled=t.can_confirm() and not t.me_confirmed, offset=offset, primary=True,
           tip="Pick a Pokémon first." if not t.can_confirm() else "")
    button(surf, app, f, pygame.Rect(r.right - 128, r.bottom - 54, 110, 38), "Cancel", app.cancel_trade,
           offset=offset)


def _trade_rows(surf, f, app, t, area, offset, row_h=34):
    """'Your PC' list (click to add to / remove from the offer) inside area; returns the rows."""
    rows = app.trade_entries()
    per = max(1, area.h // row_h)
    app.trade_per = per
    app.trade_scroll = max(0, min(app.trade_scroll, max(0, len(rows) - per)))
    app.trade_sel = max(app.trade_scroll, min(app.trade_sel, app.trade_scroll + per - 1, len(rows) - 1))
    y = area.y
    if not rows:
        text_wrapped(surf, f.small, "Your PC is empty. Deposit Pokémon in the game's PC first (they're saved "
                     "within ~30 s).", (area.x, y), area.w, DIM, 18)
    for i in range(app.trade_scroll, min(len(rows), app.trade_scroll + per)):
        bi, e = rows[i]
        rr = pygame.Rect(area.x, y, area.w, row_h - 4)
        picked = t.offered(e["id"])
        hover = rr.move(offset).collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, ACCENT if picked else (PANEL_HI if (hover or i == app.trade_sel) else PANEL),
                         rr, border_radius=6)
        fg = (30, 28, 20) if picked else TEXT
        check = pygame.Rect(rr.x + 6, rr.y + 7, 16, 16)
        pygame.draw.rect(surf, fg if picked else DIM, check, 2, border_radius=3)
        if picked:
            pygame.draw.lines(surf, fg, False, [(check.x + 3, check.y + 8), (check.x + 7, check.y + 12),
                                                (check.x + 13, check.y + 3)], 2)
        mon_label(surf, f, app, e, (rr.x + 30, rr.y + 2), fg, rr.w - 36)
        note = app.entry_note(e)
        sub = f"Box {bi + 1}" + (f" · from {e.get('origin', '').title()}" if e.get("origin") not in
                                 (None, "", app.game_id) else "")
        text(surf, f.small, fit(f.small, sub, rr.w - 36), (rr.x + 30, rr.y + 15),
             (60, 55, 40) if picked else (note[1] if note else DIM))
        app.click_targets.append((rr.move(offset), lambda e=e, i=i: (setattr(app, "trade_sel", i),
                                                                     app.offer_entry(e)), "offer"))
        y += row_h
    return rows, per


def _draw_trade_narrow(surf, f, app, t, r, offset, title):
    """The trade/gift window for narrow windows (DS): your PC on top, what changes hands below, buttons at the
    bottom - one column, long names cut with '...'."""
    x, w = r.x + 16, r.w - 32
    text(surf, f.title, fit(f.title, title, w), (x, r.y + 12), ACCENT)
    bottom_top = r.bottom - 100                 # confirmations + buttons live below this line
    y = r.y + 48
    if t.receiving_only:
        text(surf, f.small, "You're receiving a gift - nothing leaves your PC.", (x, y), DIM)
        y += 26
    else:
        text(surf, f.body, "Your PC", (x, y), TEXT)
        hint = "click to pick (several OK)"
        if f.body.size("Your PC")[0] + 12 + f.small.size(hint)[0] < w - 70:
            text(surf, f.small, hint, (x + f.body.size("Your PC")[0] + 12, y + 4), DIM)
        avail = bottom_top - (y + 26) - 34 - 6          # minus the Select box / Clear row
        shown_n = len(t.my_entries if (t.gift and t.giving) else t.their_entries)
        offers_need = 32 + 42 * min(2, max(1, shown_n)) + (20 if not t.gift and t.my_entries else 0) + 18
        list_h = max(68, min(avail * 55 // 100, avail - offers_need))
        list_h -= list_h % 34
        area = pygame.Rect(x, y + 26, w, list_h)
        rows, per = _trade_rows(surf, f, app, t, area, offset)
        if len(rows) > per:
            button(surf, app, f, pygame.Rect(x + w - 64, y - 2, 30, 24), "^",
                   lambda: setattr(app, "trade_scroll", max(0, app.trade_scroll - per)), offset=offset)
            button(surf, app, f, pygame.Rect(x + w - 30, y - 2, 30, 24), "v",
                   lambda: setattr(app, "trade_scroll", app.trade_scroll + per), offset=offset)
        y = area.bottom + 4
        if rows:
            sel_box = rows[min(app.trade_sel, len(rows) - 1)][0]
            button(surf, app, f, pygame.Rect(x, y, 130, 28), f"Select box {sel_box + 1}",
                   lambda b=sel_box: app.select_box(b), offset=offset)
            button(surf, app, f, pygame.Rect(x + 138, y, 72, 28), "Clear", app.clear_offer,
                   enabled=bool(t.my_entries), offset=offset)
            y += 34
    # what changes hands
    if t.gift and t.giving:
        head, shown, empty = f"You're giving ({len(t.my_entries)}):", t.my_entries, "Pick Pokémon above."
    else:
        head = f"{t.partner_name} {'is giving you' if t.gift else 'offers'} ({len(t.their_entries)}):"
        shown, empty = t.their_entries, "Waiting for them to choose..."
    pygame.draw.line(surf, PANEL_HI, (x, y), (x + w, y))
    y += 6
    text(surf, f.body, fit(f.body, head, w), (x, y), TEXT)
    y += 26
    mine_line = bool(not t.gift and t.my_entries)
    limit = bottom_top - (20 if mine_line else 0) - 18
    if not shown:
        text(surf, f.small, empty, (x, y), DIM)
        y += 22
    compact = limit - y < 42 * min(2, len(shown))         # short window: one line per Pokémon
    for n, e in enumerate(shown):
        need = 18 if compact else 40
        if y + need > limit - (18 if n < len(shown) - 1 else 0):     # keep room for the "... more" line
            text(surf, f.small, f"... and {len(shown) - n} more", (x, min(y, limit - 16)), DIM)
            y += 18
            break
        if compact:
            note = app.entry_note(e)
            mon_label(surf, f, app, e, (x, y), note[1] if note else TEXT, w, prefix="· ",
                      suffix=f" ({note[0]})" if note else "")
            y += 18
            continue
        card = pygame.Rect(x, y, w, 38)
        box(surf, card, PANEL_HI, PANEL_HI, 6)
        mon_label(surf, f, app, e, (card.x + 8, card.y + 3), TEXT, w - 16)
        note = app.entry_note(e)
        origin = e.get("origin")
        sub = note[0] if note else (f"OT {e.get('ot', '?')}" + (f" · from {origin.title()}" if origin else ""))
        text(surf, f.small, fit(f.small, sub, w - 16), (card.x + 8, card.y + 20), note[1] if note else DIM)
        y += 42
    if mine_line:
        text(surf, f.small, fit(f.small, f"You offer ({len(t.my_entries)}): " + app.mons_text(t.my_entries), w),
             (x, bottom_top - 38), ACCENT)
    text(surf, f.small, fit(f.small, "Trade evolutions happen on arrival.", w), (x, bottom_top - 18), DIM)

    # confirmations, then the buttons
    by = bottom_top + 6
    me = "You confirmed" if t.me_confirmed else "You haven't confirmed"
    them = f"{t.partner_name} confirmed" if t.them_confirmed else f"{t.partner_name} hasn't confirmed"
    text(surf, f.small, fit(f.small, me, w), (x, by), GOOD if t.me_confirmed else DIM)
    text(surf, f.small, fit(f.small, them, w), (x, by + 18), GOOD if t.them_confirmed else DIM)
    if t.receiving_only:
        label = "Accept gift" if len(t.their_entries) <= 1 else f"Accept {len(t.their_entries)}"
    elif t.gift:
        label = "Give it" if len(t.my_entries) <= 1 else f"Give {len(t.my_entries)}"
    else:
        label = "Confirm trade"
    cw = min(160, w - 98)
    button(surf, app, f, pygame.Rect(r.right - 16 - 90 - 8 - cw, r.bottom - 52, cw, 38), label, app.confirm_trade,
           enabled=t.can_confirm() and not t.me_confirmed, offset=offset, primary=True,
           tip="Pick a Pokémon first." if not t.can_confirm() else "")
    button(surf, app, f, pygame.Rect(r.right - 16 - 90, r.bottom - 52, 90, 38), "Cancel", app.cancel_trade,
           offset=offset)
