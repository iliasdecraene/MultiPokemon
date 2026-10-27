"""Raids (SPEC.md section 12): the client side - notice, sidebar card, join dialog, the raid battle's network part
and the 10-second move timer ring.

The game side (starting the battle, shared HP inside the game, auto-pick, the den in the overworld) lives in the
per-game modules `raid_gb.py` (Yellow + Crystal) and `raid_pt.py` (Platinum) with one interface (dev/raids/
STATUS.md): `RaidBattle(app, boss)` with `can_join() start(pool_mb) step() set_pool(pool_mb) group_down(chance)
flee() auto_pick() busy() remove()` and `Den(spot, game_id)` with `blocks(gm, d) pressed_a(gm) draw(...)`.
`raid_data.boss_for` gives each player's own boss (legendaries: a fixed level, mythicals: strongest + 5).

Local phases: idle -> open (a raid is on) -> joining (raid_join sent) -> starting (joined: snapshot, then the battle
starts at the next quiet moment) -> fight -> catch (the group's HP is 0: one ball) -> done (my part is over) / over.
"""
from __future__ import annotations

import importlib
import logging
import math
import time

import numpy as np
import pygame

from . import shiny, ui

log = logging.getLogger("yellowlink.raid")

FEATURE = "raids"                # welcome.features
BAR = 1000                       # milli-bars per bar
MOVE_SECS = 10.0                 # the move timer (Ilias's decision 6)
RETRY_PICK = 3.0                 # auto_pick again if the game still waits this long after one
SEND_EVERY = 0.25                # seconds between raid_damage messages (running total)
START_WAIT = 20.0                # seconds to find a quiet moment for the battle after joining
OVER_CARD = 25.0                 # the card shows the result this long after the raid ended
REACH_EVERY = 600                # frames between badge (reach) checks
REGION_OF_GAME = {"yellow": "kanto", "crystal": "johto", "platinum": "sinnoh"}
GAME_OF_REGION = {v: k for k, v in REGION_OF_GAME.items()}
REGION_NAMES = {"kanto": "Kanto", "johto": "Johto", "sinnoh": "Sinnoh"}
DIRECTIONS = ("up", "down", "left", "right")
RESULTS = ("caught", "missed", "blackout", "left", "fled")
HORN_VOLUME = 0.45               # peak of the raid horn (fixed, like the shiny jingle)


# --- pure helpers ---------------------------------------------------------------------------------------------
def segment(pool: int, max_hp: int) -> int:
    """The boss's local HP for a group pool (milli-bars): the current bar's rest, scaled to its max HP."""
    if pool <= 0 or max_hp <= 0:
        return 0
    return max(1, math.ceil(((pool - 1) % BAR + 1) * max_hp / BAR))


def bars_text(mb: int) -> str:
    """'3.4' bars (one decimal; '0' only when it's really 0)."""
    if mb <= 0:
        return "0"
    v = math.ceil(mb / 100) / 10
    return f"{v:.1f}".rstrip("0").rstrip(".")


def time_text(secs: float) -> str:
    secs = max(0, int(math.ceil(secs)))
    return f"{secs // 60}:{secs % 60:02d}"


def place_phrase(name: str) -> str:
    return f"on {name}" if name.startswith("Route") else f"in {name}"


def boss_label(raid: dict) -> str:
    name = str(raid.get("name") or f"#{raid.get('species')}")
    return f"shiny {name}" if raid.get("shiny") else name


def notice_text(raid: dict, minutes: int | None = None) -> str:
    """'RAID! Mew appeared on Route 1 (Kanto)' - the toast (the server's chat line says the same + minutes)."""
    who = f"A shiny {raid.get('name')}" if raid.get("shiny") else str(raid.get("name"))
    spot = raid.get("spot") or {}
    text = f"RAID! {who} appeared {place_phrase(str(spot.get('name', '?')))} " \
           f"({REGION_NAMES.get(raid.get('region'), '?')})"
    return text + (f" - {minutes} minutes!" if minutes else "!")


def reach_from_badges(game_id: str, value) -> int:
    """Gym badges of a region's own game (the spots' `reach`): Yellow wObtainedBadges bits, Crystal the Johto bits
    (badges() = johto | kanto << 8), Platinum badges() is already the count."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    if game_id in ("yellow", "crystal"):
        return bin(v & 0xFF).count("1")
    return max(0, min(8, v))


def participant(raid: dict | None, uid: str) -> dict | None:
    for p in (raid or {}).get("participants") or []:
        if p.get("uid") == uid:
            return p
    return None


def fighting_names(raid: dict) -> list:
    return [p.get("name", "?") for p in raid.get("participants") or [] if p.get("state") == "fighting"]


class MoveTimer:
    """The 10-second move timer: runs from the game's `need_input` until `input_done` (wall clock, across sub-menus
    of the same turn). `poll()` says True once when it runs out (-> auto_pick) and again every RETRY_PICK seconds
    while the game keeps waiting."""

    def __init__(self, secs: float = MOVE_SECS):
        self.secs = secs
        self.start = None
        self.next_fire = None

    @property
    def running(self) -> bool:
        return self.start is not None

    def need_input(self, now: float):
        if self.start is None:
            self.start = now
            self.next_fire = now + self.secs

    def input_done(self):
        self.start = self.next_fire = None

    def left(self, now: float):
        """Seconds left (None = not running)."""
        if self.start is None:
            return None
        return max(0.0, self.secs - (now - self.start))

    def fraction(self, now: float) -> float:
        left = self.left(now)
        return 0.0 if left is None else left / self.secs

    def poll(self, now: float) -> bool:
        if self.next_fire is not None and now >= self.next_fire:
            self.next_fire = now + RETRY_PICK
            return True
        return False


class DamageMeter:
    """My damage in milli-bars (running total). The game module caps each hit at the rest of the bar (`mb`);
    without `mb`, n HP of a boss with M max HP count n * 1000 / M (at most a bar)."""

    def __init__(self):
        self.total = 0
        self.hits = 0

    def add(self, ev: dict) -> int:
        mb = ev.get("mb")
        if mb is None:
            try:
                hp, of = int(ev.get("hp", 0)), int(ev.get("of", 0))
            except (TypeError, ValueError):
                return 0
            mb = hp * BAR // of if of > 0 else 0
        mb = max(0, min(BAR, int(mb)))
        if mb:
            self.total += mb
            self.hits += 1
        return mb


# --- the raid horn --------------------------------------------------------------------------------------------
# (start s, frequency Hz, length s): a short brass call - two low notes, then a rising fifth that holds
_HORN = [(0.00, 392.0, 0.16), (0.18, 392.0, 0.16), (0.36, 523.25, 0.20), (0.58, 783.99, 0.62)]
HORN_SECS = 1.35


def horn_samples(rate: int = 48000, channels: int = 2) -> np.ndarray:
    """The raid horn as int16 (n, channels): brassy tones (odd + even harmonics, a little vibrato on the last note),
    soft attack, short release; peak = HORN_VOLUME of full scale. Our own sound - no game audio."""
    n = int(HORN_SECS * rate)
    t = np.arange(n) / rate
    out = np.zeros(n)
    for start, f, length in _HORN:
        tt = t - start
        on = (tt >= 0) & (tt < length + 0.12)
        x = tt[on]
        env = np.minimum(1.0, x / 0.025) * np.where(x < length, 1.0, np.exp(-(x - length) / 0.035))
        env *= 1.0 - 0.25 * (x / (length + 0.12))
        vib = 1 + (0.006 * np.sin(2 * math.pi * 5.5 * x) * np.minimum(1.0, x / 0.25) if length > 0.5 else 0)
        phase = 2 * math.pi * f * x * vib
        tone = sum(a * np.sin(k * phase) for k, a in ((1, 1.0), (2, 0.55), (3, 0.42), (4, 0.22), (5, 0.14),
                                                      (6, 0.07)))
        out[on] += env * tone
    fade = int(0.05 * rate)
    out[-fade:] *= np.linspace(1, 0, fade)
    out *= HORN_VOLUME * 32767 / max(1e-9, float(np.abs(out).max()))
    mono = np.clip(np.round(out), -32767, 32767).astype(np.int16)
    return np.repeat(mono[:, None], channels, axis=1)


_HORN_CACHE: dict = {}


def horn_sound():
    return shiny.make_sound(horn_samples, _HORN_CACHE)


# --- drawing --------------------------------------------------------------------------------------------------
CARD_BG = (44, 30, 38)
CARD_RIM = (232, 120, 84)
CARD_FIGHT = (255, 150, 90)
POOL_FG = (238, 96, 78)
POOL_BG = (70, 48, 56)


def draw_timer_ring(surf, f, center, radius, frac, secs_left):
    """The move timer: a dark disc, a ring that empties clockwise (yellow, red in the last 3 s), seconds inside."""
    cx, cy = center
    r = max(8, int(radius))
    disc = pygame.Surface((2 * r + 4, 2 * r + 4), pygame.SRCALPHA)
    pygame.draw.circle(disc, (12, 14, 22, 215), (r + 2, r + 2), r)
    surf.blit(disc, (cx - r - 2, cy - r - 2))
    width = max(3, r // 5)
    pygame.draw.circle(surf, (70, 74, 92), (cx, cy), r - width // 2, width)
    color = ui.BAD if secs_left <= 3 else ui.ACCENT
    if frac > 0:
        pts = []
        steps = max(8, int(64 * frac))
        for i in range(steps + 1):
            a = -math.pi / 2 + 2 * math.pi * frac * i / steps
            pts.append((cx + (r - width / 2) * math.cos(a), cy + (r - width / 2) * math.sin(a)))
        if len(pts) > 1:
            pygame.draw.lines(surf, color, False, pts, width)
    font = f.title if r >= 20 else (f.body if r >= 14 else f.small)
    ui.text(surf, font, str(int(math.ceil(secs_left))), (cx, cy + 1), color, anchor="center")


def draw_pool_bar(surf, rect, pool, pool_max):
    pygame.draw.rect(surf, POOL_BG, rect, border_radius=4)
    if pool_max > 0 and pool > 0:
        fill = rect.copy()
        fill.w = max(2, int(rect.w * pool / pool_max))
        pygame.draw.rect(surf, POOL_FG, fill, border_radius=4)
    bars = max(1, pool_max // BAR) if pool_max else 0
    if 1 < bars <= 40:                       # bar separators
        for i in range(1, bars):
            x = rect.x + rect.w * i // bars
            pygame.draw.line(surf, (30, 20, 26), (x, rect.y + 1), (x, rect.bottom - 2))


CARD_H = 92                      # the sidebar raid card's height (incl. the gap below it)


def card_height(app) -> int:
    """Height of the sidebar raid card (0 = no card)."""
    return CARD_H if getattr(app, "raid", None) is not None else 0


def _fit(font, s: str, width: int) -> str:
    """s, shortened with '…' to fit width."""
    if font.size(s)[0] <= width:
        return s
    while len(s) > 1 and font.size(s + "…")[0] > width:
        s = s[:-1]
    return s.rstrip() + "…"


def draw_card(surf, f, app, x, y, w) -> int:
    """The sidebar raid card at (x, y), w wide (compact: GB 3x windows are 480 px tall). Returns its height."""
    raid = app.raid
    h = card_height(app)
    if not h:
        return 0
    now = app.server_time() if hasattr(app, "server_time") else time.time()
    fighting = app.raid_fighting() or app.raid_phase == "catch"
    r = pygame.Rect(x, y, w, h - 4)
    pygame.draw.rect(surf, CARD_BG, r, border_radius=8)
    pygame.draw.rect(surf, CARD_FIGHT if fighting else CARD_RIM, r, 2, border_radius=8)
    phase = raid.get("phase")
    ix, right_x, inner = r.x + 10, r.right - 10, r.w - 20
    # row 1: RAID · Kanto ................ 12:34
    head = ui.text(surf, f.small, "RAID", (ix, r.y + 5), CARD_RIM)
    ui.text(surf, f.small, f" · {REGION_NAMES.get(raid.get('region'), '?')}", (head.right, r.y + 5), ui.DIM)
    right = "over" if phase == "over" else ("down!" if phase == "down" else
                                            time_text(float(raid.get("closes", now)) - now))
    ui.text(surf, f.small, right, (right_x, r.y + 5), ui.TEXT if phase == "open" else ui.ACCENT, anchor="topright")
    # row 2: Mew (shiny: gold + sparkle) ........ Pallet Town
    name = boss_label(raid)
    title = f"{name[:1].upper()}{name[1:]}"
    spot = (raid.get("spot") or {}).get("name", "")
    spot_w = min(f.small.size(spot)[0], inner // 2)
    title = _fit(f.body, title, inner - spot_w - (24 if raid.get("shiny") else 8))
    tr = ui.text(surf, f.body, title, (ix, r.y + 20), shiny_color() if raid.get("shiny") else ui.TEXT)
    if raid.get("shiny"):
        ui._draw_sparkle(surf, tr.right + 9, tr.centery)
    ui.text(surf, f.small, _fit(f.small, spot, spot_w), (right_x, r.y + 24), ui.DIM, anchor="topright")
    # row 3: the group's HP (bars)
    pool, pool_max = int(raid.get("pool", 0)), int(raid.get("pool_max", 0))
    draw_pool_bar(surf, pygame.Rect(ix, r.y + 42, inner, 8), pool, pool_max)
    # row 4: status (or my damage while fighting) ........ 2.6 / 4 bars
    hp = f"{bars_text(pool)} / {bars_text(pool_max)} bars" if pool_max else ""
    hp_w = f.small.size(hp)[0] + 8 if hp else 0
    line, color = app.raid_status_line()
    if fighting and app.raid_phase != "catch":
        mine = app.raid_meter.total if app.raid_meter else 0
        total = max(sum(int(p.get("dealt", 0)) for p in raid.get("participants") or []), mine)
        line = f"You: {bars_text(mine)} bars" + (f" ({int(round(100 * mine / total))}%)" if total else "")
    if hp:
        ui.text(surf, f.small, hp, (right_x, r.y + 54), ui.DIM, anchor="topright")
    ui.text(surf, f.small, _fit(f.small, line, inner - hp_w), (ix, r.y + 54), color)
    # row 5: who's in ........ [Go there]
    btn = app.raid_card_button()
    if phase == "over":
        names = [p.get("name", "?") for p in raid.get("participants") or []]
        who = "Raiders: " + (", ".join(names) if names else "nobody")
    else:
        names = fighting_names(raid)
        who = "In: " + (", ".join(names) if names else "nobody yet")
    who_w = inner
    if btn is not None:
        label, cb = btn
        bw = f.small.size(label)[0] + 18
        br = pygame.Rect(right_x - bw, r.y + 68, bw, 18)
        hover = br.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, CARD_FIGHT if hover else CARD_RIM, br, border_radius=6)
        ui.text(surf, f.small, label, br.center, (30, 20, 20), anchor="center")
        app.click_targets.append((br, cb, label))
        who_w = br.x - ix - 6
    ui.text(surf, f.small, _fit(f.small, who, who_w), (ix, r.y + 70), ui.DIM)
    return h


def shiny_color():
    return (255, 226, 110)


def draw_confirm(surf, f, app, game_w, game_h, offset):
    """The join dialog ("Join the raid against Mew?") - buttons Join / Not now."""
    c = app.raid_confirm
    raid = app.raid or {}
    name = boss_label(raid)
    title = f"Raid: {name[:1].upper()}{name[1:]}"
    now = app.server_time() if hasattr(app, "server_time") else time.time()
    width = min(game_w - 40, 520) - 36
    lines = []
    if c.get("ok"):
        lv = c.get("level")
        lines += list(ui.wrap(f.body, f"Join the raid against {name}" + (f" (Lv {lv})" if lv else "") + "?", width))
        left = time_text(raid.get("closes", now) - now)
        dim = (f"You add {raid.get('bars_per_player', 2)} bars to the group's HP. {left} left. Your catch chance = "
               "your share of all the damage; blacking out = you're out.")
        lines += [(ln, ui.DIM) for ln in ui.wrap(f.body, dim, width)]      # ui.modal draws lines in f.body
        lines.append("")
        footer = "[Y] / [N]" if game_w < 520 else "[Y] join    [N] not now"
    else:
        lines += [(ln, ui.BAD) for ln in ui.wrap(f.body, c.get("why") or "You can't join right now.", width)]
        lines.append("")
        footer = "[Esc] close"
    r = ui.modal(surf, f, game_w, game_h, title, lines, footer)
    from . import panels
    if c.get("ok"):
        panels.button(surf, app, f, pygame.Rect(r.right - 250, r.bottom - 52, 110, 36), "Join",
                      lambda: app.raid_answer(True), offset=offset, primary=True)
        panels.button(surf, app, f, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "Not now",
                      lambda: app.raid_answer(False), offset=offset)
    else:
        panels.button(surf, app, f, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "OK",
                      lambda: app.raid_answer(False), offset=offset, primary=True)
    return r


# --- the app side ---------------------------------------------------------------------------------------------
def _game_module(game_id: str):
    """raid_gb (Yellow, Crystal) or raid_pt (Platinum), or None while it isn't there."""
    name = "raid_pt" if game_id == "platinum" else "raid_gb"
    try:
        return importlib.import_module(f".{name}", __package__)
    except ImportError:
        return None


def _raid_data():
    try:
        return importlib.import_module(".raid_data", __package__)
    except ImportError:
        return None


class RaidMixin:
    """Mixed into App. Everything raid that isn't the game's own battle (that's raid_gb / raid_pt)."""

    def init_raid(self):
        self.raid = None                 # the server's raid object (SPEC.md section 12), None = no raid
        self.raid_phase = "idle"
        self.raid_battle = None          # RaidBattle once started (no resume snapshot while it exists)
        self.raid_pending = None         # RaidBattle made for the join dialog / waiting to start
        self.raid_den = None
        self.raid_confirm = None         # join dialog {"ok", "why", "level"}
        self.raid_timer = MoveTimer()
        self.raid_meter = None
        self.raid_sent = 0               # last running total sent
        self.raid_sent_t = 0.0
        self.raid_result = None          # outcome reported for this raid
        self.raid_down_info = None       # {"chance", "catch"}
        self.raid_over_at = None         # local time the raid ended (card stays OVER_CARD s)
        self.raid_start_by = 0.0
        self.raid_snapshot_done = False
        self.raid_boss = None
        self.raid_reach_sent = None
        self._raid_flee_sent = False
        self._raid_outcomes = {}         # raid id -> the result we reported (resent if a disconnect ate it)

    # ---------------------------------------------------------------- state
    def raid_on(self) -> bool:
        return FEATURE in (getattr(self, "server_features", None) or ())

    def raid_fighting(self) -> bool:
        """A raid battle runs in this game (fast-forward off, no snapshots, no shiny_found of our own)."""
        return self.raid_battle is not None

    def raid_busy(self) -> bool:
        """In a raid (joining, starting, fighting): no trades, battles, travel, server switch."""
        return self.raid_battle is not None or self.raid_phase in ("joining", "starting", "fight", "catch")

    def raid_here(self) -> bool:
        """The open raid is in the game we're playing (main game, verified ROM)."""
        r = self.raid
        return bool(r and r.get("game") == self.game_id and self.full and not self.chal)

    def _raid_me(self):
        return participant(self.raid, self.cfg["uid"])

    def raid_status_line(self):
        """(text, colour) for the card's main line."""
        raid = self.raid or {}
        phase, me = self.raid_phase, self._raid_me()
        if raid.get("phase") == "over":
            caught = [p.get("name") for p in raid.get("participants") or [] if p.get("state") == "caught"]
            if caught:
                return (f"{', '.join(caught)} caught it!", ui.GOOD)
            return ("It got away.", ui.DIM)
        if phase == "catch":
            d = self.raid_down_info or {}
            return (f"Down! Throw a ball ({int(round(100 * float(d.get('chance', 0))))}% chance)", ui.ACCENT)
        if raid.get("phase") == "down":
            return ("Down! Catching...", ui.ACCENT)
        if self.raid_fighting():
            return ("Fight! Every hit counts", CARD_FIGHT)
        if phase in ("joining", "starting"):
            return ("Joining...", ui.ACCENT)
        if me and me.get("state") != "fighting":
            return ({"caught": "You caught it!", "missed": "It broke free.", "blackout": "You blacked out.",
                     "left": "You left this raid.", "fled": "It fled."}.get(me.get("state"), "Done."), ui.DIM)
        if self.chal:
            return ("Back in your main game", ui.DIM)
        if not self.raid_here():
            return (f"in {REGION_NAMES.get(raid.get('region'), '?')}", ui.DIM)
        return ("Find the den, press A", ui.TEXT)

    def raid_card_button(self):
        """(label, callback) for the card, or None."""
        raid = self.raid or {}
        if raid.get("phase") != "open" or self.raid_busy() or self.chal:
            return None
        game = raid.get("game")
        if game != self.game_id and game in getattr(self.lib, "games", {}):
            return ("Go there", lambda: self.raid_travel())
        return None

    def raid_travel(self):
        game = (self.raid or {}).get("game")
        if not game or self.raid_busy():
            return
        if self.trade or self.battle or self.live:
            self.toast("Finish your trade or battle first.", color=ui.BAD)
            return
        self.travel(game)

    # ---------------------------------------------------------------- network
    def raid_on_welcome(self):
        """(Re)connected: a raid we knew may have ended meanwhile - the server's catch-up `raid_open` (right after
        `welcome`) brings back a running one. A raid battle in progress keeps its raid."""
        if self.raid is not None and self.raid_battle is None and self.raid_phase not in ("joining", "starting"):
            self.raid = None
            self._raid_reset()

    def _raid_valid(self, raid) -> bool:
        return isinstance(raid, dict) and isinstance(raid.get("id"), str) and isinstance(raid.get("spot"), dict)

    def on_raid_open(self, msg):
        raid = msg.get("raid")
        if not self._raid_valid(raid):
            return
        new = self.raid is None or self.raid.get("id") != raid["id"]
        if new:
            self._raid_reset()
            self.raid_phase = "open"
        self.raid = raid
        self.raid_over_at = None
        if msg.get("fresh"):
            minutes = int(round((float(raid.get("closes", 0)) - float(raid.get("opens", 0))) / 60)) or None
            text = notice_text(raid, None)
            if self.chal:
                text += " (Back in your main game you can join.)"
            elif raid.get("game") != self.game_id:
                text += " Travel there to join."
            self.toast(text, 9, CARD_FIGHT)
            log.info("raid notice: %s (%s min)", text, minutes)
            banners = getattr(self, "banners", None)
            if raid.get("shiny") and banners is not None:
                # a shiny boss is also a shiny alert (decision 8): the shiny banner, whose start plays the jingle
                spot = (raid.get("spot") or {}).get("name", "")
                banners.push(f"A shiny {raid.get('name')} raid!",
                             f"{place_phrase(spot)} ({REGION_NAMES.get(raid.get('region'), '?')})")
            else:
                self.play_raid_horn()
        self._raid_make_den()
        me = self._raid_me()
        if self.raid_phase == "joining" and not msg.get("fresh"):
            # we asked to join just before the connection dropped: did it arrive?
            if me and me.get("state") == "fighting":
                self.on_raid_joined({"raid": raid})
            else:
                self.raid_phase = "open"
                self.raid_pending = None
                self.toast("Connection hiccup - press A at the den again to join.", 5, ui.DIM)
            return
        told = self._raid_outcomes.get(raid["id"])
        if me and me.get("state") == "fighting" and told and self.raid_battle is None:
            self.raid_result = told                   # our result got lost in a disconnect: again
            self.raid_phase = "done"
            self._raid_send_result()
        elif me and me.get("state") == "fighting":
            if self.raid_phase in ("fight", "catch", "starting") or self.raid_battle is not None:
                self._raid_send_damage(force=True)      # back after a reconnect: our running total again
            elif self.raid_phase != "joining":
                # the server has us fighting but this game has no raid battle (closed / crashed mid-raid)
                log.info("raid %s: fighting on the server but no battle here -> left", raid["id"])
                self._raid_report("left")
                self.raid_phase = "done"
        elif me and self.raid_phase in ("open", "idle"):
            self.raid_phase = "done"

    def on_raid_update(self, msg):
        raid = msg.get("raid")
        if not self._raid_valid(raid):
            return
        if self.raid is None or self.raid.get("id") != raid["id"]:
            if raid.get("phase") == "over":
                return
            self._raid_reset()
            self.raid_phase = "open"
            self.raid = raid
            self._raid_make_den()
            return
        old = int(self.raid.get("pool", 0))
        self.raid = raid
        pool = int(raid.get("pool", 0))
        if self.raid_battle is not None and pool > 0 and pool != old:
            # with what the server had counted of ours: hits still on their way are taken off by the game module
            me = self._raid_me() or {}
            self._raid_call("set_pool", pool, int(me.get("dealt", 0)))

    def on_raid_joined(self, msg):
        raid = msg.get("raid")
        if not self._raid_valid(raid) or self.raid is None or raid["id"] != self.raid.get("id"):
            return
        self.raid = raid
        if self.raid_phase != "joining":
            return                                       # a re-join after a reconnect: nothing to start
        self.raid_phase = "starting"
        self.raid_start_by = time.time() + START_WAIT
        self.raid_snapshot_done = False
        self.raid_meter = DamageMeter()
        self.raid_sent = 0
        log.info("raid %s joined: pool %s/%s", raid["id"], raid.get("pool"), raid.get("pool_max"))

    def on_raid_refused(self, msg):
        if self.raid is None or msg.get("id") != self.raid.get("id"):
            return
        reason = str(msg.get("reason") or "You can't join right now.")[:120]
        self.toast(reason, 5, ui.BAD)
        if self.raid_phase == "joining":
            self.raid_phase = "done" if "out of" in reason else "open"
            self.raid_pending = None

    def on_raid_down(self, msg):
        if self.raid is None or msg.get("id") != self.raid.get("id"):
            return
        try:
            chance = max(0.0, min(1.0, float(msg.get("chance", 0))))
        except (TypeError, ValueError):
            chance = 0.0
        catch = msg.get("catch") is True
        if self.raid_down_info is not None:
            return                                       # resent after a reconnect: already handled
        self.raid_down_info = {"chance": chance, "catch": catch}
        if self.raid_battle is None:
            if self.raid_result is None:
                self._raid_report("left")
            return
        self.raid_phase = "catch"
        self.raid_timer.input_done()
        self._raid_call("group_down", 1.0 if catch else 0.0)
        name = (self.raid or {}).get("name", "The boss")
        text = f"{name} is down! Throw a Poké Ball - your catch chance: {int(round(chance * 100))}%"
        self.toast(text, 7, ui.ACCENT)
        self.sys_msg(text, ui.ACCENT)

    def on_raid_end(self, msg):
        if self.raid is None or msg.get("id") != self.raid.get("id"):
            return
        raid = msg.get("raid")
        if self._raid_valid(raid):
            self.raid = raid
        else:
            self.raid = dict(self.raid, phase="over")
        self.raid_over_at = time.time()
        self.raid_den = None
        self.raid_confirm = None
        if self.mode == "raid_confirm":
            self.mode = "play"
        if self.raid_battle is not None and self.raid_phase != "catch":
            if msg.get("reason") == "time":
                self.toast(f"Time's up - {self.raid.get('name', 'the boss')} fled!", 5, ui.DIM)
            self._raid_call("flee")
        elif self.raid_battle is None:
            self.raid_phase = "over"
            self.raid_pending = None

    def _raid_reset(self):
        self.raid_phase = "idle"
        self.raid_pending = None
        self.raid_den = None
        self.raid_confirm = None
        self.raid_result = None
        self.raid_down_info = None
        self.raid_meter = None
        self.raid_sent = 0
        self._raid_flee_sent = False
        self.raid_timer.input_done()

    def _raid_report(self, outcome):
        if self.raid_result is not None or self.raid is None:
            return
        self.raid_result = outcome
        self._raid_outcomes[self.raid["id"]] = outcome
        while len(self._raid_outcomes) > 8:
            self._raid_outcomes.pop(next(iter(self._raid_outcomes)))
        log.info("raid %s: result %s", self.raid["id"], outcome)
        self._raid_send_result()

    def _raid_send_result(self):
        """Our last damage total, then the result (sent again after a reconnect if the server still has us
        fighting - it ignores repeats)."""
        self._raid_send_damage(force=True)
        net = getattr(self, "net", None)
        if net is not None and self.raid_on() and self.raid is not None:
            net.send({"type": "raid_result", "id": self.raid["id"], "outcome": self.raid_result})

    def _raid_send_damage(self, force=False):
        m = self.raid_meter
        if m is None or self.raid is None or not self.raid_on():
            return
        now = time.time()
        if m.total <= 0 or (not force and (m.total == self.raid_sent or now - self.raid_sent_t < SEND_EVERY)):
            return
        if self.net.send({"type": "raid_damage", "id": self.raid["id"], "dealt": int(m.total)}):
            self.raid_sent, self.raid_sent_t = m.total, now

    # ---------------------------------------------------------------- reach (badges per region, for spots)
    def raid_reach_for_server(self) -> dict:
        book = (self.cfg.get("raid_reach") or {}).get(self._raid_reach_key()) or {}
        return {region: int(book.get(region, 0) or 0) for region in REGION_NAMES}

    def _raid_reach_key(self) -> str:
        try:
            return str(self.cfg.data_dir)
        except Exception:  # noqa: BLE001
            return "main"

    def _raid_reach_step(self):
        if self.frame % REACH_EVERY or not self.full or self.chal or self.game_id not in REGION_OF_GAME:
            return
        try:
            if not self.game.is_safe():
                return
            n = reach_from_badges(self.game_id, self.game.badges())
        except Exception:  # noqa: BLE001
            log.exception("raid: badge count failed")
            return
        region = REGION_OF_GAME[self.game_id]
        allr = dict(self.cfg.get("raid_reach") or {})
        book = dict(allr.get(self._raid_reach_key()) or {})
        if book.get(region) != n:
            book[region] = n
            allr[self._raid_reach_key()] = book
            self.cfg["raid_reach"] = allr
            self.cfg.save()
        now = self.raid_reach_for_server()
        if now != self.raid_reach_sent and getattr(self, "net", None) is not None and self.net.status == "online":
            if self.net.send(self.library_message()):
                self.raid_reach_sent = now

    # ---------------------------------------------------------------- den + join dialog
    def _raid_make_den(self):
        raid = self.raid
        if not self.raid_here() or raid.get("phase") != "open" or self.raid_den is not None:
            return
        mod = _game_module(self.game_id)
        if mod is None or getattr(mod, "Den", None) is None:
            return
        spot = dict(raid["spot"], region=raid.get("region"), reach=0)
        try:
            self.raid_den = mod.Den(spot, self.game_id)
        except Exception:  # noqa: BLE001 - no den, no crash
            log.exception("raid: den failed")
            self.raid_den = None

    def raid_press(self, b, down) -> bool:
        """From App.press(): True = the press is taken (A at the den -> join dialog; a step onto the den)."""
        if self.raid_battle is not None and down and self._raid_call("taking_over"):
            return True                       # the move timer's auto pick drives the buttons right now
        den = self.raid_den
        if den is None or not down or self.mode != "play" or self.raid_battle is not None:
            return False
        try:
            if b in DIRECTIONS and den.blocks(self.game, b):
                return True
            if b == "a" and den.pressed_a(self.game):
                self.raid_open_dialog()
                return True
        except Exception:  # noqa: BLE001
            log.exception("raid: den input failed")
        return False

    def raid_filter_held(self):
        """Every frame: a direction held down must not walk onto the den (it was pressed before the den was in the
        way); give it back once the way is free."""
        den = self.raid_den
        if den is None or self.mode != "play" or self.raid_battle is not None:
            return
        for d in DIRECTIONS:
            want = d in self.kb_held or d in self.pad_held
            if not want:
                continue
            try:
                blocked = bool(den.blocks(self.game, d))
            except Exception:  # noqa: BLE001
                blocked = False
            if blocked and d in self.held:
                self.emu.button_release(d)
                self.held.discard(d)
            elif not blocked and d not in self.held:
                self.emu.button_press(d)
                self.held.add(d)

    def _raid_boss(self):
        """This player's boss: raid_data.boss_for(species, strongest, region, shiny) + the server's form/bars."""
        raid = self.raid
        rd = _raid_data()
        if rd is None or not hasattr(rd, "boss_for"):
            return None
        boss = dict(rd.boss_for(int(raid["species"]), self._raid_strongest(), raid.get("region"),
                                shiny=bool(raid.get("shiny")), form=int(raid.get("form", 0) or 0)))
        boss.update(shiny=bool(raid.get("shiny")), form=int(raid.get("form", 0) or 0), region=raid.get("region"),
                    bars=max(1, int(raid.get("pool_max", 0)) // BAR + int(raid.get("bars_per_player", 2))))
        return boss

    def _raid_strongest(self) -> int:
        try:
            if self.ds:
                party = self.game.party_checked() or []
                infos = [self.G.mon_info(r) for r in party]
                levels = [i["level"] for i in infos if i and not i.get("egg")]
            else:
                levels = [m.level for m in (self.game.party_checked() or [])]
            return max(levels) if levels else 5
        except Exception:  # noqa: BLE001
            log.exception("raid: party levels failed")
            return 5

    def raid_join_check(self) -> tuple:
        """(ok, why) before the join dialog / before sending raid_join."""
        raid = self.raid
        if raid is None or raid.get("phase") == "over":
            return False, "This raid is over."
        if raid.get("phase") == "down":
            return False, "The boss is already down."
        if self.chal:
            return False, "You're in a Soul Link run - raids are for your main game."
        if not self.full:
            return False, "Raids need the supported version of this game."
        if raid.get("game") != self.game_id:
            return False, f"This raid is in {REGION_NAMES.get(raid.get('region'), '?')}."
        if not self.raid_on() or self.net.status != "online":
            return False, "You need to be connected to the server."
        if self.trade or self.battle or self.live:
            return False, "Finish your trade or battle first."
        me = self._raid_me()
        if me is not None and me.get("state") != "fighting":
            return False, "You're out of this raid."
        if self.raid_phase in ("done", "over"):
            return False, "You're out of this raid."
        if self.server_time() >= float(raid.get("closes", 0)):
            return False, "Time's up for this raid."
        return True, ""

    def raid_open_dialog(self):
        ok, why = self.raid_join_check()
        level = None
        if ok:
            mod = _game_module(self.game_id)
            boss = self._raid_boss()
            if mod is None or boss is None or getattr(mod, "RaidBattle", None) is None:
                ok, why = False, "This YellowLink can't run raid battles in this game yet."
            else:
                try:
                    self.raid_pending = mod.RaidBattle(self, boss)
                    ok, why = self.raid_pending.can_join()
                    self.raid_boss = boss
                    level = boss.get("level")
                except Exception:  # noqa: BLE001
                    log.exception("raid: can_join failed")
                    ok, why = False, "Something went wrong - press F3 to send a report."
                    self.raid_pending = None
        self.raid_confirm = {"ok": bool(ok), "why": why, "level": level, "shown": time.time()}
        self.release_all()
        self.mode = "raid_confirm"

    def raid_answer(self, join: bool):
        c = self.raid_confirm
        if c is None or time.time() - c.get("shown", 0) < 0.3:
            return
        self.raid_confirm = None
        if self.mode == "raid_confirm":
            self.mode = "play"
        self.release_all()
        if not join or not c.get("ok"):
            self.raid_pending = None
            return
        ok, why = self.raid_join_check()
        if not ok:
            self.toast(why, 4, ui.BAD)
            self.raid_pending = None
            return
        level = int(c.get("level") or (self.raid_boss or {}).get("level") or 50)
        if self.net.send({"type": "raid_join", "id": self.raid["id"], "level": max(1, min(100, level))}):
            self.raid_phase = "joining"
            self.toast("Joining the raid...", 2)

    def raid_key(self, k):
        if k in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.raid_answer(bool(self.raid_confirm and self.raid_confirm.get("ok")))
        elif k in (pygame.K_n, pygame.K_ESCAPE):
            self.raid_answer(False)

    # ---------------------------------------------------------------- every frame
    def raid_step(self):
        """After the emulator ticked (App.step)."""
        now = time.time()
        if self.raid is not None and self.raid.get("phase") == "over" and self.raid_battle is None \
                and self.raid_over_at is not None and now - self.raid_over_at > OVER_CARD:
            self.raid = None
            self._raid_reset()
        self._raid_reach_step()
        if self.raid_phase == "starting" and self.raid_battle is None:
            self._raid_try_start(now)
        b = self.raid_battle
        if b is None:
            return
        try:
            events = b.step() or []
        except Exception:  # noqa: BLE001
            log.exception("raid battle step failed")
            events = []
        for ev in events:
            self._raid_event(ev, now)
        if self.raid_battle is None:
            return
        self._raid_send_damage()
        raid = self.raid or {}
        if (raid.get("phase") == "open" and not self._raid_flee_sent and self.raid_phase == "fight"
                and self.server_time() > float(raid.get("closes", 0)) + 20):
            # the raid's end never reached us (connection): its time is up anyway
            self._raid_flee_sent = True
            self._raid_call("flee")
        if self.raid_phase in ("fight", "catch") and self.raid_timer.poll(now):
            log.info("raid: move timer ran out -> auto pick")
            self.release_all()
            self._raid_call("auto_pick")

    def _raid_try_start(self, now):
        b = self.raid_pending
        if b is None or self.raid is None or self.raid.get("phase") != "open":
            self._raid_give_up("The raid battle couldn't start.")
            return
        if now > self.raid_start_by:
            self._raid_give_up("Couldn't find a quiet moment to start the raid battle - you left the raid.")
            return
        try:
            if not self.game.is_safe():
                return
            if not self.raid_snapshot_done:
                self.raid_snapshot_done = True
                self.autosave(force=True)            # closing mid-raid resumes from here (= leaving the raid)
            self._raid_disable_fast()
            if b.start(int(self.raid.get("pool", 0))):
                self.raid_battle, self.raid_pending = b, None
                self.raid_phase = "fight"
                self.raid_timer.input_done()
                self.release_all()
                log.info("raid battle started (boss %s)", self.raid_boss)
        except Exception:  # noqa: BLE001
            log.exception("raid battle start failed")
            self._raid_give_up("The raid battle couldn't start - press F3 to send a report.")

    def _raid_give_up(self, why):
        self.raid_pending = None
        self._raid_report("left")
        self.raid_phase = "done"
        self.toast(why, 6, ui.BAD)

    def _raid_disable_fast(self):
        if getattr(self, "fast", False):
            self.fast = False
            if hasattr(self, "_sync_ds_audio"):
                self._sync_ds_audio()

    def _raid_event(self, ev, now):
        t = ev.get("type") if isinstance(ev, dict) else None
        name = (self.raid or {}).get("name", "the boss")
        if t == "started":
            self.raid_phase = "fight" if self.raid_phase not in ("catch",) else self.raid_phase
        elif t == "damage":
            if self.raid_meter is None:
                self.raid_meter = DamageMeter()
            self.raid_meter.add(ev)
        elif t == "need_input":
            if self.raid_phase in ("fight", "catch"):
                self.raid_timer.need_input(now)
        elif t == "input_done":
            self.raid_timer.input_done()
        elif t == "blackout":
            self._raid_report("blackout")
            self.toast("You blacked out - you're out of this raid.", 6, ui.BAD)
        elif t == "left":
            self._raid_report("left")
            self.toast("You ran from the raid - you're out of it.", 5, ui.DIM)
        elif t == "caught":
            self._raid_report("caught")
            self.toast(f"You caught {name}!", 6, ui.GOOD)
        elif t == "missed":
            self._raid_report("missed")
            self.toast(f"{name} broke free and fled!", 6, ui.DIM)
        elif t == "fled":
            self._raid_report("fled")
        elif t == "ended":
            outcome = ev.get("outcome")
            self._raid_report(outcome if outcome in RESULTS else "left")
            self.raid_battle = None
            self.raid_timer.input_done()
            self.raid_phase = "over" if (self.raid or {}).get("phase") == "over" else "done"
            log.info("raid battle over (%s)", outcome)

    def _raid_call(self, fn, *args):
        b = self.raid_battle
        if b is None:
            return None
        try:
            return getattr(b, fn)(*args)
        except Exception:  # noqa: BLE001
            log.exception("raid battle %s failed", fn)
            return None

    def raid_no_snapshot(self) -> bool:
        """No resume snapshot while a raid battle exists (closing = leaving the raid; resume = just before it)."""
        return self.raid_battle is not None

    def raid_shutdown(self):
        """Closing the game mid-raid = leaving the raid (the resume snapshot is the one from before the battle)."""
        if self.raid_battle is not None or self.raid_phase in ("joining", "starting"):
            self._raid_report("left")

    def play_raid_horn(self):
        out = getattr(self, "audio", None)
        if out is not None:
            out.play_fx(horn_sound())

    # ---------------------------------------------------------------- drawing
    def raid_draw_world(self):
        """The den on the game picture (view pixels, before scaling) - drawn by the game module."""
        den = self.raid_den
        if den is None or self.raid_battle is not None:
            return
        try:
            if self.ds:
                den.draw(self.game_surf, self.game, off=(0, 0), mask=None)
            else:
                den.draw(self.game_surf, self.game, off=(self.off_x, self.off_y),
                         mask=getattr(self, "_friend_mask", None),
                         neighbor_shift=self._neighbor_shift if getattr(self, "wv", None) else None)
        except Exception:  # noqa: BLE001
            if not getattr(self, "_raid_draw_err", False):
                self._raid_draw_err = True
                log.exception("raid: den draw failed")

    def raid_draw_overlay(self, sub):
        """The move timer ring over the game (top right; the DS: on the top screen)."""
        if self.raid_battle is None or not self.raid_timer.running:
            return
        now = time.time()
        left = self.raid_timer.left(now)
        if left is None:
            return
        r = max(14, int(11 * self.scale))
        cx = int(sub.get_width() - r - 6)
        cy = int(r + 6)
        draw_timer_ring(sub, self.fonts, (cx, cy), r, self.raid_timer.fraction(now), left)
