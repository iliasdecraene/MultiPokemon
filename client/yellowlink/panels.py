"""Mouse-driven panels: Multiplayer and Regions. Pure drawing + click-target registration."""
from __future__ import annotations

import pygame

from . import library
from .ui import ACCENT, BAD, DIM, GOOD, PANEL, PANEL_HI, TEXT, box, text

BTN = (58, 64, 86)
BTN_HOVER = (78, 86, 114)
BTN_OFF = (40, 43, 56)


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


def draw_multiplayer(surf, f, app, game_w, game_h, offset):
    r = _panel_frame(surf, app, f, game_w, game_h, "Multiplayer", offset)
    others = [e for e in app.sorted_roster() if e.get("uid") != app.cfg["uid"]]
    y = r.y + 56
    if not app.full:
        text(surf, f.small, f"Trades and battles are Yellow-only for now (you're in {app.game_info.title}).",
             (r.x + 18, y), BAD)
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
        text(surf, f.body, e.get("name", "?"), (row.x + 32, row.y + 9), TEXT if online else DIM)
        lv = e.get("levels") or []
        info = ("online" if online else "offline") + f" · {len(lv)} Pokémon" + (f" · Lv {max(lv)}" if lv else "")
        if app.is_nearby(uid):
            info = "nearby · " + info
        text(surf, f.small, info, (row.right - 12, row.y + 11), DIM, anchor="topright")
        app.click_targets.append((row.move(offset), lambda u=uid: app.select_player(u), "player:" + e.get("name", "?")))
        y += row_h
        if selected:
            bx = row.x + 32
            bw = 130
            can_live = online and app.full and app.hooks is not None
            button(surf, app, f, pygame.Rect(bx, y, bw, 34), "Battle", lambda e=e: app.action_battle(e),
                   enabled=can_live, offset=offset, primary=True,
                   tip="They need to be online, and you both in Yellow.")
            button(surf, app, f, pygame.Rect(bx + bw + 10, y, bw, 34), "Trade", lambda e=e: app.action_trade(e),
                   enabled=online and app.full, offset=offset, tip="They need to be online, and you both in Yellow.")
            button(surf, app, f, pygame.Rect(bx + 2 * (bw + 10), y, bw, 34), "Practice",
                   lambda e=e: app.action_practice(e), enabled=app.full and bool(lv), offset=offset,
                   tip="Practice battles work in Yellow vs a trainer with Pokémon.")
            y += 44
    text(surf, f.small, "Battle: live vs them  ·  Practice: their team played by the AI (works offline)",
         (r.x + 18, r.bottom - 26), DIM)


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
    r = _panel_frame(surf, app, f, game_w, game_h, "Regions", offset)
    y = r.y + 56
    text(surf, f.small, "Kanto = Pokémon Yellow, Johto = Pokémon Crystal. Each keeps its own progress.", (r.x + 18, y), DIM)
    y += 30
    installed = app.lib.games
    for region_id, region_name, game_ids in library.REGIONS:
        have = [g for g in game_ids if g in installed]
        here = app.game_info.region == region_id
        card = pygame.Rect(r.x + 12, y, r.w - 24, 92 if len(have) > 1 else 64)
        if card.bottom > r.bottom - 60:
            break
        box(surf, card, PANEL_HI if here else PANEL, ACCENT if here else PANEL_HI, 8)
        text(surf, f.title, region_name, (card.x + 14, card.y + 8), ACCENT if here else TEXT)
        br = pygame.Rect(card.right - 140, card.y + 12, 126, 36)
        if not have:
            names = [library.GAMES_BY_ID[g].title for g in game_ids]
            text(surf, f.small, "Add your " + " / ".join(names) + " game file to unlock this region",
                 (card.x + 14, card.y + 40), DIM)
        else:
            gid = _region_game(app, region_id, have)
            info = library.GAMES_BY_ID[gid]
            status = f"Ready - via {info.title}"
            if not info.full_support:
                status += "  (single player for now)"
            text(surf, f.small, status, (card.x + 14, card.y + 40), GOOD if info.full_support else TEXT)
            if here and app.game_id == gid:
                text(surf, f.small, "you are here", br.center, GOOD, anchor="center")
            else:
                button(surf, app, f, br, "Travel", lambda g=gid: app.travel(g), offset=offset, primary=True,
                       enabled=not (app.trade or app.battle or app.live),
                       tip="Finish your trade or battle first.")
            if len(have) > 1:
                # optional: which version to play this region with
                text(surf, f.small, "Play with:", (card.x + 14, card.y + 64), DIM)
                bx = card.x + 90
                for g in have:
                    label = library.GAMES_BY_ID[g].title.replace("Pokémon ", "")
                    w = f.small.size(label)[0] + 22
                    chip = pygame.Rect(bx, card.y + 60, w, 24)
                    sel = g == gid
                    pygame.draw.rect(surf, ACCENT if sel else PANEL_HI, chip, border_radius=12)
                    text(surf, f.small, label, chip.center, (30, 28, 20) if sel else TEXT, anchor="center")
                    app.click_targets.append((chip.move(offset), lambda rg=region_id, g=g: app.pick_region_game(rg, g),
                                              "version:" + g))
                    bx += w + 8
        y = card.bottom + 10
    button(surf, app, f, pygame.Rect(r.x + 18, r.bottom - 48, 200, 36), "Add a game file...", app.add_rom_dialog,
           offset=offset)
    text(surf, f.small, "Kanto: Pokémon Yellow  ·  Johto: Pokémon Crystal",
         (r.x + 230, r.bottom - 40), DIM)
